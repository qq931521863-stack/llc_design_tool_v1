"""Interactive Fc × PM Solution Map shared by LLC and TTPL workspaces."""
from __future__ import annotations

import math
from dataclasses import dataclass, replace

import numpy as np
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.colors import BoundaryNorm, ListedColormap
from matplotlib.figure import Figure
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from llc_design.control.digital_loop import (
    PIControllerConfig,
    calculate_stability_margins,
)
from llc_design.control.solution_map import (
    STATUS_LABELS,
    SolutionMapConstraints,
    SolutionMapPoint,
    SolutionMapResult,
    SolutionStatus,
)
from llc_design.control.smart_control import (
    FixedLoopCorner,
    build_common_fc_pm_solution_map,
    evaluate_common_solution_point,
)


@dataclass(frozen=True)
class LoopMapSource:
    key: str
    label: str
    frequencies_hz: np.ndarray
    fixed_loop_response: np.ndarray
    sample_rate_hz: float
    switching_frequency_hz: float
    provenance: str = ""
    current_loop_response: np.ndarray | None = None
    operating_point: str = ""
    compatibility_key: str = ""


class SolutionMapView(QWidget):
    """Fc × PM design-space browser driven by an already-built loop model."""

    controller_selected = Signal(str, object)

    def __init__(self, title: str = "Fc × PM Solution Map", parent=None) -> None:
        super().__init__(parent)
        self.sources: dict[str, LoopMapSource] = {}
        self.result: SolutionMapResult | None = None
        self.selected_point: SolutionMapPoint | None = None
        self._selection_artist = None
        self._preview_loop: np.ndarray | None = None
        self._intent = None
        self._retained_sources: dict[str, LoopMapSource] = {}
        self._checked_corner_ids: set[str] = set()
        self._corner_sequence = 0
        self._last_source_key = None
        self._corner_notice = ""

        root = QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)
        header = QHBoxLayout()
        heading = QLabel(title)
        heading.setStyleSheet("font-size:15px;font-weight:650;")
        header.addWidget(heading)
        self.source_combo = QComboBox()
        self.source_combo.currentIndexChanged.connect(self._source_changed)
        header.addWidget(QLabel("Loop"))
        header.addWidget(self.source_combo)
        self.generate_button = QPushButton("Generate candidate from Fc / PM")
        self.generate_button.clicked.connect(self.generate_candidate)
        header.addWidget(self.generate_button)
        self.cancel_button = QPushButton("Cancel preview")
        self.cancel_button.clicked.connect(self.cancel_preview)
        header.addWidget(self.cancel_button)
        self.run_button = QPushButton("Build Fc × PM Map")
        self.run_button.clicked.connect(self.build_map)
        header.addWidget(self.run_button)
        self.apply_button = QPushButton("Apply selected PI")
        self.apply_button.setEnabled(False)
        self.apply_button.setToolTip(
            "Apply FEASIBLE points, or explicitly adopt a CONDITIONAL point with unavailable Fsw / GM evidence."
        )
        self.apply_button.clicked.connect(self._apply_selected)
        header.addWidget(self.apply_button)
        header.addStretch(1)
        root.addLayout(header)

        splitter = QSplitter()
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.addWidget(self._build_controls())
        left_layout.addWidget(self._build_selected_point_panel())
        left_scroll = QScrollArea()
        left_scroll.setWidgetResizable(True)
        left_scroll.setWidget(left)
        left_scroll.setMinimumWidth(340)
        splitter.addWidget(left_scroll)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        self.result_tabs = QTabWidget()
        self.result_tabs.addTab(self._build_map_tab(), "Solution Map")
        self.result_tabs.addTab(self._build_preview_tab(), "Current / candidate comparison")
        right_layout.addWidget(self.result_tabs, 1)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([360, 1100])
        root.addWidget(splitter, 1)
        for widget in (
            self.fc_min, self.fc_max, self.fc_points, self.pm_min, self.pm_max, self.pm_points,
            self.gm_min, self.ms_max, self.switch_max, self.fc_tol, self.pm_tol,
        ):
            widget.valueChanged.connect(self._inputs_changed)
        self._source_changed()

    @staticmethod
    def _double(lo: float, hi: float, decimals: int, value: float, suffix: str = "") -> QDoubleSpinBox:
        widget = QDoubleSpinBox()
        widget.setRange(lo, hi)
        widget.setDecimals(decimals)
        widget.setValue(value)
        widget.setSuffix(suffix)
        widget.setKeyboardTracking(False)
        return widget

    def _build_controls(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        target = QGroupBox("Optional map sweep range")
        form = QFormLayout(target)
        self.fc_min = self._double(0.01, 1e7, 2, 100.0, " Hz")
        self.fc_max = self._double(0.02, 1e7, 2, 10_000.0, " Hz")
        self.fc_points = QSpinBox()
        self.fc_points.setRange(5, 120)
        self.fc_points.setValue(28)
        self.pm_min = self._double(1, 179, 2, 30.0, "°")
        self.pm_max = self._double(1, 179, 2, 80.0, "°")
        self.pm_points = QSpinBox()
        self.pm_points.setRange(5, 120)
        self.pm_points.setValue(26)
        for label, widget in (
            ("Fc min", self.fc_min), ("Fc max", self.fc_max), ("Fc points", self.fc_points),
            ("PM min", self.pm_min), ("PM max", self.pm_max), ("PM points", self.pm_points),
        ):
            form.addRow(label, widget)
        layout.addWidget(target)

        constraints = QGroupBox("Objective constraints")
        form = QFormLayout(constraints)
        self.gm_min = self._double(-100, 100, 2, 6.0, " dB")
        self.ms_max = self._double(0.1, 100, 3, 2.0)
        self.switch_max = self._double(-200, 100, 2, -20.0, " dB")
        self.allow_conditional = QCheckBox("Allow conditional PI (unverified Fsw / GM)")
        self.allow_conditional.setChecked(False)
        self.allow_conditional.toggled.connect(self._inputs_changed)
        self.fc_tol = self._double(0.1, 100, 2, 5.0, "%")
        self.pm_tol = self._double(0.1, 90, 2, 3.0, "°")
        for label, widget in (
            ("GM minimum", self.gm_min), ("Ms maximum", self.ms_max),
            ("|L(Fsw)| maximum", self.switch_max), ("Fc target tolerance", self.fc_tol),
            ("PM target tolerance", self.pm_tol),
        ):
            form.addRow(label, widget)
        form.addRow(self.allow_conditional)
        layout.addWidget(constraints)

        self.source_info = QLabel("No loop source loaded.")
        self.source_info.setWordWrap(True)
        self.source_info.setStyleSheet("padding:7px;border:1px solid #d0d5dd;border-radius:5px;")
        layout.addWidget(self.source_info)
        corners = QGroupBox("Retained operating points (this session)")
        corner_layout = QVBoxLayout(corners)
        self.corner_summary = QLabel()
        self.corner_summary.setWordWrap(True)
        corner_layout.addWidget(self.corner_summary)
        self.corner_list = QListWidget()
        self.corner_list.setMaximumHeight(125)
        self.corner_list.itemChanged.connect(self._corner_toggled)
        corner_layout.addWidget(self.corner_list)
        buttons = QHBoxLayout()
        self.retain_button = QPushButton("Retain current")
        self.retain_button.clicked.connect(self.retain_current_source)
        self.remove_corner_button = QPushButton("Remove selected")
        self.remove_corner_button.clicked.connect(self.remove_selected_corner)
        self.clear_corners_button = QPushButton("Clear all")
        self.clear_corners_button.clicked.connect(self.clear_corners)
        for button in (self.retain_button, self.remove_corner_button, self.clear_corners_button):
            buttons.addWidget(button)
        corner_layout.addLayout(buttons)
        self.corner_notice = QLabel()
        self.corner_notice.setWordWrap(True)
        corner_layout.addWidget(self.corner_notice)
        layout.addWidget(corners)
        note = QLabel(
            "Map synthesis removes only the current controller from the maintained loop result. "
            "Plant, sensing, ADC, modulator/PWM/FM and delay remain unchanged. "
            "The current point synthesizes one exact Tustin PI; every checked snapshot "
            "tests that same H(z), without retuning. Change the operating point, run the "
            "maintained analysis, then retain it. Plant / sensing / timing changes clear "
            "incompatible snapshots. UNKNOWN blocks Apply."
        )
        note.setWordWrap(True)
        note.setStyleSheet("color:#667085;padding:6px;")
        layout.addWidget(note)
        layout.addStretch(1)
        return panel

    def _build_selected_point_panel(self) -> QWidget:
        box = QGroupBox("Candidate target / exact PI preview")
        layout = QFormLayout(box)
        self.live_fc = self._double(0.01, 1e7, 3, 1000.0, " Hz")
        self.live_pm = self._double(1, 179, 2, 60.0, "°")
        self.live_kp = self._double(0, 1e100, 15, 0.01)
        self.live_ti = self._double(0, 1e100, 15, 0.001, " s")
        for label, widget in (
            ("Fc", self.live_fc), ("PM", self.live_pm), ("Kp", self.live_kp), ("Ti", self.live_ti),
        ):
            form_row = layout  # alias
            form_row.addRow(label, widget)
            if widget in (self.live_fc, self.live_pm):
                widget.valueChanged.connect(self._inputs_changed)
            else:
                widget.setReadOnly(True)
                widget.setMaximumWidth(240)
                widget.setButtonSymbols(QDoubleSpinBox.ButtonSymbols.NoButtons)
                widget.setToolTip("Synthesized preview only; Apply uses the full-precision candidate.")
        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        self.details.setMaximumHeight(220)
        self.details.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        layout.addRow(self.details)
        return box

    def _build_map_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        self.figure = Figure(figsize=(10, 6))
        self.canvas = FigureCanvasQTAgg(self.figure)
        self.canvas.mpl_connect("button_press_event", self._map_clicked)
        self.canvas.mpl_connect("motion_notify_event", self._map_hovered)
        layout.addWidget(self.canvas, 1)
        self.hover_label = QLabel("Hover a map point for status / click to select.")
        self.hover_label.setStyleSheet("color:#667085;padding:4px;")
        layout.addWidget(self.hover_label)
        return page

    def _build_preview_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        self.comparison_summary = QLabel("Generate a candidate to compare with the current controller.")
        self.comparison_summary.setWordWrap(True)
        self.comparison_summary.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.comparison_summary)
        self.preview_figure = Figure(figsize=(10, 7))
        self.preview_canvas = FigureCanvasQTAgg(self.preview_figure)
        layout.addWidget(self.preview_canvas, 1)
        return page

    def set_sources(self, sources: list[LoopMapSource]) -> None:
        self.sources = {source.key: self._freeze_source(source) for source in sources}
        current = self.source_combo.currentData() or self._last_source_key
        incompatible = [corner_id for corner_id, retained in self._retained_sources.items()
                        if retained.key in self.sources
                        and not self._compatible(retained, self.sources[retained.key])]
        for corner_id in incompatible:
            self._retained_sources.pop(corner_id)
            self._checked_corner_ids.discard(corner_id)
        if incompatible:
            self._corner_notice = (
                f"Cleared {len(incompatible)} incompatible snapshot(s): "
                "plant, sensing, timing or fixed inner controller changed."
            )
        self.source_combo.blockSignals(True)
        self.source_combo.clear()
        for source in sources:
            problem = self._source_problem(source)
            self.source_combo.addItem(source.label + (" [insufficient / invalid coverage]" if problem else ""), source.key)
            if problem:
                self.source_combo.setItemData(self.source_combo.count() - 1, problem, Qt.ItemDataRole.ToolTipRole)
        if current in self.sources:
            self.source_combo.setCurrentIndex(self.source_combo.findData(current))
        self.source_combo.blockSignals(False)
        self._source_changed()

    @staticmethod
    def _freeze_source(source: LoopMapSource) -> LoopMapSource:
        arrays = {}
        for name in ("frequencies_hz", "fixed_loop_response", "current_loop_response"):
            value = getattr(source, name)
            if value is not None:
                frozen = np.array(value, copy=True)
                frozen.setflags(write=False)
                arrays[name] = frozen
        return replace(source, **arrays)

    @staticmethod
    def _compatible(first: LoopMapSource, second: LoopMapSource) -> bool:
        return (first.key == second.key and bool(first.compatibility_key)
                and first.compatibility_key == second.compatibility_key
                and first.sample_rate_hz == second.sample_rate_hz)

    @staticmethod
    def _source_problem(source: LoopMapSource) -> str:
        f = np.asarray(source.frequencies_hz)
        fixed = np.asarray(source.fixed_loop_response)
        if f.ndim != 1 or f.size < 3 or fixed.shape != f.shape:
            return "Insufficient response coverage: at least 3 aligned principal-band samples are required."
        if (not np.all(np.isfinite(f)) or not np.all(np.isfinite(fixed))
                or np.any(f <= 0) or np.any(np.diff(f) <= 0)):
            return "Invalid response: frequencies must increase and all samples must be finite."
        if (not math.isfinite(source.sample_rate_hz) or source.sample_rate_hz <= 0
                or not math.isfinite(source.switching_frequency_hz) or source.switching_frequency_hz <= 0):
            return "Invalid sampling or switching frequency."
        if np.any(f >= 0.5 * source.sample_rate_hz):
            return "Invalid response coverage: samples must be below this loop's Nyquist frequency."
        if (source.current_loop_response is not None
                and (np.asarray(source.current_loop_response).shape != f.shape
                     or not np.all(np.isfinite(source.current_loop_response)))):
            return "Invalid current-loop comparison response."
        return ""

    def _refresh_corners(self) -> None:
        source = self._source()
        self.corner_list.blockSignals(True)
        self.corner_list.clear()
        for corner_id, retained in self._retained_sources.items():
            if source is not None and retained.key != source.key:
                continue
            item = QListWidgetItem(f"{corner_id}: {retained.operating_point or retained.label}")
            item.setData(Qt.ItemDataRole.UserRole, corner_id)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if corner_id in self._checked_corner_ids
                               else Qt.CheckState.Unchecked)
            item.setToolTip(f"{retained.label}\n{retained.provenance}\n"
                            f"Fs={retained.sample_rate_hz:.17g} Hz; "
                            f"Fsw={retained.switching_frequency_hz:.17g} Hz")
            self.corner_list.addItem(item)
        self.corner_list.blockSignals(False)
        selected = len(self._active_corners()) if source is not None else 0
        self.corner_summary.setText(
            f"{selected} active point(s): current nominal + {max(0, selected - 1)} checked snapshots."
            if source is not None else "Run analysis to verify compatibility with retained snapshots."
        )
        self.retain_button.setEnabled(source is not None and not self._source_problem(source)
                                      and bool(source.compatibility_key))
        self.remove_corner_button.setEnabled(self.corner_list.count() > 0)
        self.clear_corners_button.setEnabled(bool(self._retained_sources))
        self.corner_notice.setText(self._corner_notice)

    def retain_current_source(self) -> None:
        source = self._source()
        if source is None or not source.compatibility_key or self._source_problem(source):
            return
        # Repeated clicks must not silently multiply an identical operating point.
        for retained in self._retained_sources.values():
            if (self._compatible(source, retained)
                    and source.operating_point == retained.operating_point
                    and np.array_equal(source.frequencies_hz, retained.frequencies_hz)
                    and np.array_equal(source.fixed_loop_response, retained.fixed_loop_response)):
                self._corner_notice = "This operating point is already retained."
                self._refresh_corners()
                return
        self._corner_sequence += 1
        corner_id = f"OP{self._corner_sequence}"
        self._retained_sources[corner_id] = self._freeze_source(source)
        self._checked_corner_ids.add(corner_id)
        self._corner_notice = "Retained the completed analysis. Checked snapshots use the same candidate PI."
        self._inputs_changed()
        self._refresh_corners()

    def remove_selected_corner(self) -> None:
        item = self.corner_list.currentItem()
        if item is None:
            return
        corner_id = item.data(Qt.ItemDataRole.UserRole)
        self._retained_sources.pop(corner_id, None)
        self._checked_corner_ids.discard(corner_id)
        self._corner_notice = "Removed the selected operating-point snapshot."
        self._inputs_changed()
        self._refresh_corners()

    def clear_corners(self) -> None:
        self._retained_sources.clear()
        self._checked_corner_ids.clear()
        self._corner_notice = "Retained operating points cleared."
        self._inputs_changed()
        self._refresh_corners()

    def _corner_toggled(self, item) -> None:
        corner_id = item.data(Qt.ItemDataRole.UserRole)
        if item.checkState() == Qt.CheckState.Checked:
            self._checked_corner_ids.add(corner_id)
        else:
            self._checked_corner_ids.discard(corner_id)
        self._inputs_changed()
        self._refresh_corners()

    def _active_corners(self) -> tuple[FixedLoopCorner, ...]:
        source = self._source()
        if source is None or self._source_problem(source):
            return ()
        selected = [("Current nominal", source)] + [
            (corner_id, retained) for corner_id, retained in self._retained_sources.items()
            if corner_id in self._checked_corner_ids and self._compatible(source, retained)
        ]
        return tuple(FixedLoopCorner(
            corner_id=f"{corner_id}: {item.operating_point or item.label}",
            frequencies_hz=item.frequencies_hz,
            fixed_loop_response=item.fixed_loop_response,
            sample_rate_hz=item.sample_rate_hz,
            switching_frequency_hz=item.switching_frequency_hz,
            provenance=item.provenance,
        ) for corner_id, item in selected)

    def _source(self) -> LoopMapSource | None:
        key = self.source_combo.currentData()
        return self.sources.get(str(key)) if key is not None else None

    def cancel_preview(self) -> None:
        """Discard the unapplied candidate. The maintained controller is untouched."""
        self.selected_point = None
        self._preview_loop = None
        self.apply_button.setEnabled(False)
        self.apply_button.setText("Apply selected PI")
        self.preview_figure.clear()
        self.preview_canvas.draw_idle()
        self.comparison_summary.setText("Preview discarded. Current controller is unchanged.")
        self.details.setPlainText("Preview discarded. Current controller is unchanged.")

    def _inputs_changed(self, *_args) -> None:
        self.cancel_preview()
        self.result = None
        self.figure.clear()
        self.canvas.draw_idle()
        self._selection_artist = None
        self.details.setPlainText("Targets or constraints changed. Generate a new candidate before Apply.")

    def invalidate(self, reason: str = "System inputs changed. Re-run analysis before designing a controller.",
                   *, clear_corners: bool = False) -> None:
        """Reject candidates from an obsolete maintained-loop analysis."""
        if clear_corners and self._retained_sources:
            self.clear_corners()
            self._corner_notice = "Plant / sensing / timing inputs changed; retained operating points cleared."
        self.set_sources([])
        self.details.setPlainText(reason)

    def _source_changed(self) -> None:
        self._inputs_changed()
        self.allow_conditional.setChecked(False)
        source = self._source()
        if source is not None:
            self._last_source_key = source.key
        self._refresh_corners()
        supported = self._intent is None or self._intent.design_mode != "auto" or self._intent.structure == "PI"
        self.run_button.setEnabled(source is not None and supported)
        self.generate_button.setEnabled(source is not None and supported)
        if source is None:
            self.source_info.setText("No current loop source. Run the maintained analysis first.")
            return
        problem = self._source_problem(source)
        if problem:
            self.run_button.setEnabled(False)
            self.generate_button.setEnabled(False)
            self.source_info.setText(f"{source.label}\n{problem}\n{source.provenance}")
            self.corner_summary.setText("No active operating points; update the analysis frequency range.")
            self.details.setPlainText(problem + " Run the maintained analysis over a valid frequency range.")
            return
        upper = min(0.20 * source.sample_rate_hz, 0.20 * source.switching_frequency_hz, source.frequencies_hz[-1] * 0.95)
        lower = max(source.frequencies_hz[0] * 2.0, upper / 200.0)
        if upper > lower:
            self.fc_min.setValue(lower)
            self.fc_max.setValue(upper)
        self.source_info.setText(
            f"{source.label}\nFs={source.sample_rate_hz/1e3:.5g} kHz | "
            f"Fsw={source.switching_frequency_hz/1e3:.5g} kHz\n{source.provenance}"
            f"\n{source.operating_point}"
        )
        self.details.setPlainText("Set target Fc / PM, generate a candidate, compare, then explicitly Apply.")

    def configure_intent(self, intent) -> None:
        """Consume guided targets without creating another controller owner."""
        self._inputs_changed()
        self._intent = intent
        # A fresh guided request cannot silently inherit an earlier conditional-adoption permission.
        self.allow_conditional.setChecked(False)
        supported = intent.design_mode != "auto" or intent.structure == "PI"
        source = self._source()
        valid_source = source is not None and not self._source_problem(source)
        self.generate_button.setEnabled(supported and valid_source)
        self.run_button.setEnabled(supported and valid_source)
        for widget, value in (
            (self.live_fc, intent.target_crossover_hz),
            (self.live_pm, intent.target_phase_margin_deg),
            (self.gm_min, intent.minimum_gain_margin_db),
            (self.ms_max, intent.maximum_sensitivity),
            (self.switch_max, intent.maximum_switching_loop_gain_db),
        ):
            if value is not None:
                widget.setValue(value)
        if intent.design_mode == "auto":
            if intent.structure != "PI":
                self.cancel_preview()
                self.details.setPlainText(
                    f"Auto synthesis for {intent.structure} is not implemented. "
                    "Current controller is unchanged. Select PI in Guided Design or use manual expert tuning."
                )
                return
            if intent.target_crossover_hz is None or intent.target_phase_margin_deg is None:
                self.details.setPlainText("Auto design needs explicit Fc and PM targets. Enter both and generate a PI candidate.")
                return
            self.generate_candidate()

    def generate_candidate(self) -> None:
        """Synthesize and validate exactly the candidate shown and applied."""
        self.cancel_preview()
        source = self._source()
        if source is None:
            self.details.setPlainText("Run the maintained loop analysis before generating a candidate.")
            return
        if self._source_problem(source):
            self.details.setPlainText(self._source_problem(source))
            return
        if self._intent is not None and self._intent.design_mode == "auto" and self._intent.structure != "PI":
            self.details.setPlainText(f"Auto synthesis for {self._intent.structure} is not implemented; no controller changed.")
            return
        try:
            point = evaluate_common_solution_point(
                self._active_corners(),
                target_crossover_hz=self.live_fc.value(),
                target_phase_margin_deg=self.live_pm.value(),
                constraints=self._constraints(),
            )
        except (ValueError, ArithmeticError) as exc:
            self.details.setPlainText(f"Candidate generation failed: {exc}")
            return
        self.selected_point = point
        self._update_apply_button(point)
        self._sync_live_fields(point)
        self._show_point(point)
        self._update_preview(point)
        self.result_tabs.setCurrentIndex(1)

    def _constraints(self) -> SolutionMapConstraints:
        return SolutionMapConstraints(
            minimum_gain_margin_db=self.gm_min.value(),
            maximum_sensitivity=self.ms_max.value(),
            maximum_switching_loop_gain_db=self.switch_max.value(),
            crossover_tolerance_fraction=self.fc_tol.value() / 100.0,
            phase_margin_tolerance_deg=self.pm_tol.value(),
        )

    def build_map(self) -> None:
        self.cancel_preview()
        self.result = None
        if self._intent is not None and self._intent.design_mode == "auto" and self._intent.structure != "PI":
            self.details.setPlainText(f"Auto synthesis for {self._intent.structure} is not implemented; no controller changed.")
            return
        source = self._source()
        if source is None:
            return
        if self._source_problem(source):
            self.details.setPlainText(self._source_problem(source))
            return
        if self.fc_max.value() <= self.fc_min.value():
            self.details.setPlainText("Fc max must exceed Fc min.")
            return
        if self.pm_max.value() <= self.pm_min.value():
            self.details.setPlainText("PM max must exceed PM min.")
            return
        fc = np.geomspace(self.fc_min.value(), self.fc_max.value(), self.fc_points.value())
        pm = np.linspace(self.pm_min.value(), self.pm_max.value(), self.pm_points.value())
        try:
            self.result = build_common_fc_pm_solution_map(
                self._active_corners(),
                crossover_targets_hz=fc,
                phase_margin_targets_deg=pm,
                loop_label=f"{source.label} ({len(self._active_corners())} operating points)",
                constraints=self._constraints(),
            )
        except Exception as exc:
            self.details.setPlainText(f"Solution Map failed: {exc}")
            return
        self.selected_point = None
        self.apply_button.setEnabled(False)
        self._plot_result()

    def _plot_result(self) -> None:
        result = self.result
        if result is None:
            return
        self.figure.clear()
        ax = self.figure.add_subplot(111)
        codes = result.status_codes.astype(float)
        cmap = ListedColormap([
            "#d1fadf", "#e4e7ec", "#fef0c7", "#fee4e2",
            "#fedf89", "#fecdc9", "#d1e9ff", "#f2f4f7", "#e9d7fe", "#ffead5",
        ])
        norm = BoundaryNorm(np.arange(-0.5, len(SolutionStatus) + 0.5, 1), cmap.N)
        xx, yy = np.meshgrid(result.crossover_targets_hz, result.phase_margin_targets_deg)
        ax.pcolormesh(xx, yy, codes, cmap=cmap, norm=norm, shading="nearest")
        ax.set_xscale("log")
        ax.set_xlabel("Target crossover Fc (Hz)")
        ax.set_ylabel("Target phase margin PM (deg)")
        ax.set_title(f"{result.loop_label} — Fc × PM Solution Map")
        ax.grid(True, which="both", alpha=0.22)
        counts = {
            status: int(np.sum(result.status_codes == int(status)))
            for status in SolutionStatus
        }
        legend_text = " | ".join(
            f"{STATUS_LABELS[status]}={count}" for status, count in counts.items() if count
        )
        ax.text(0.01, -0.14, legend_text, transform=ax.transAxes, fontsize=8, va="top", wrap=True)
        self.figure.subplots_adjust(bottom=0.20)
        self.canvas.draw_idle()
        self.details.setPlainText(
            f"{result.loop_label}\n"
            f"Grid: {len(result.crossover_targets_hz)} × {len(result.phase_margin_targets_deg)} = {result.status_codes.size}\n"
            f"FEASIBLE: {int(np.sum(result.feasible_mask))} ({100*result.feasible_fraction:.2f}%)\n\n"
            f"Each cell uses one PI at {len(self._active_corners())} operating point(s).\n"
            f"{self._scope_note()}\n\n"
            "Click a map point to inspect exact PI parameters and preview Bode / S / T."
        )

    def _nearest_point(self, xdata: float, ydata: float) -> SolutionMapPoint | None:
        result = self.result
        if result is None or xdata is None or ydata is None or xdata <= 0.0:
            return None
        ix = int(np.argmin(np.abs(np.log(result.crossover_targets_hz) - math.log(xdata))))
        iy = int(np.argmin(np.abs(result.phase_margin_targets_deg - ydata)))
        return result.point(iy, ix)

    def _map_hovered(self, event) -> None:
        if event.inaxes is None or event.xdata is None or event.ydata is None:
            return
        point = self._nearest_point(event.xdata, event.ydata)
        if point is None:
            return
        self.hover_label.setText(
            f"{STATUS_LABELS[point.status]} | Fc={point.target_crossover_hz:.5g} Hz | "
            f"PM={point.target_phase_margin_deg:.4g}° | "
            f"actual Fc={point.actual_crossover_hz} | GM={point.gain_margin_db} | "
            f"corners={len(point.corner_results) or 1}"
        )

    def _map_clicked(self, event) -> None:
        if self._intent is not None and self._intent.design_mode == "auto" and self._intent.structure != "PI":
            return
        result = self.result
        if result is None or event.xdata is None or event.ydata is None or event.inaxes is None:
            return
        point = self._nearest_point(event.xdata, event.ydata)
        if point is None:
            return
        self.cancel_preview()
        self.selected_point = point
        self._update_apply_button(point)
        ax = event.inaxes
        if self._selection_artist is not None:
            try:
                self._selection_artist.remove()
            except Exception:
                pass
        self._selection_artist = ax.plot(
            [point.target_crossover_hz], [point.target_phase_margin_deg],
            marker="o", markersize=9, markerfacecolor="none", markeredgewidth=2,
        )[0]
        self.canvas.draw_idle()
        self._show_point(point)
        self._sync_live_fields(point)
        self._update_preview(point)

    def _sync_live_fields(self, point: SolutionMapPoint) -> None:
        self.live_fc.blockSignals(True)
        self.live_pm.blockSignals(True)
        self.live_kp.blockSignals(True)
        self.live_ti.blockSignals(True)
        self.live_fc.setValue(point.target_crossover_hz)
        self.live_pm.setValue(point.target_phase_margin_deg)
        if point.kp is not None:
            self.live_kp.setValue(point.kp)
        if point.ti_s is not None:
            self.live_ti.setValue(point.ti_s)
        self.live_fc.blockSignals(False)
        self.live_pm.blockSignals(False)
        self.live_kp.blockSignals(False)
        self.live_ti.blockSignals(False)

    def _show_point(self, point: SolutionMapPoint) -> None:
        value = lambda x, fmt=".6g": "—" if x is None else format(x, fmt)
        self.details.setPlainText(
            "SOLUTION MAP POINT\n" + "=" * 68 + "\n"
            f"Status           : {STATUS_LABELS[point.status]}\n"
            f"Message          : {point.message}\n"
            f"Target Fc        : {point.target_crossover_hz:.7g} Hz\n"
            f"Target PM        : {point.target_phase_margin_deg:.5g} deg\n"
            f"PI Kp            : {value(point.kp)}\n"
            f"PI Ti            : {value(None if point.ti_s is None else point.ti_s*1e3)} ms\n"
            f"Controller phase : {value(point.controller_phase_deg)} deg\n"
            f"Actual Fc        : {value(point.actual_crossover_hz)} Hz\n"
            f"Actual PM        : {value(point.actual_phase_margin_deg)} deg\n"
            f"GM               : {value(point.gain_margin_db)} dB\n"
            f"Ms               : {value(point.ms)}\n"
            f"Mt               : {value(point.mt)}\n"
            f"|L(Fsw)|         : {value(point.switching_loop_gain_db)} dB\n"
            f"Configured frequency-band pass: {point.feasible}\n"
            f"{self._scope_note()}\n\n{self._corner_report(point)}"
        )

    def _scope_note(self) -> str:
        if not self.allow_conditional.isChecked():
            return "Conditional adoption disabled; incomplete required evidence blocks Apply."
        return ("Conditional adoption authorized for missing Fsw / GM evidence only; "
                "known failures and UNKNOWN still block Apply. This is not a full feasibility or stability pass.")

    def _corner_report(self, point: SolutionMapPoint) -> str:
        results = point.corner_results or (point,)
        ids = point.corner_ids or ("Current nominal",)
        unknown = sum(item.status == SolutionStatus.UNKNOWN for item in results)
        conditional = sum(item.status == SolutionStatus.CONDITIONAL for item in results)
        passed = sum(item.feasible for item in results)
        rows = [f"COMMON PI: {len(results)} point(s); PASS={passed}; "
                f"FAIL={len(results) - passed - unknown - conditional}; UNKNOWN={unknown}; "
                f"CONDITIONAL={conditional}"]
        for label, field, worst in (
            ("PM", "actual_phase_margin_deg", min), ("GM", "gain_margin_db", min),
            ("Ms", "ms", max), ("Mt", "mt", max), ("|L(Fsw)|", "switching_loop_gain_db", max),
        ):
            observed = [getattr(item, field) for item in results if getattr(item, field) is not None]
            value = f"{worst(observed):.7g}" if observed else "UNKNOWN"
            missing = len(results) - len(observed)
            rows.append(f"Worst computed {label}: {value}" + (f"; UNKNOWN at {missing} point(s)" if missing else ""))
        active = self._active_corners()
        for index, (corner_id, item) in enumerate(zip(ids, results)):
            value = lambda number: "UNKNOWN" if number is None else f"{number:.7g}"
            rows.append(f"{corner_id}: {STATUS_LABELS[item.status]} | Fc={value(item.actual_crossover_hz)} Hz; "
                        f"PM={value(item.actual_phase_margin_deg)}°; GM={value(item.gain_margin_db)} dB; "
                        f"Ms={value(item.ms)}; Mt={value(item.mt)}; |L(Fsw)|={value(item.switching_loop_gain_db)} dB")
            if index < len(active):
                rows.append(f"  Source: {active[index].provenance}")
        return "\n".join(rows)

    def _update_preview(self, point: SolutionMapPoint) -> None:
        source = self._source()
        if source is None or point.kp is None or point.ti_s is None:
            return
        f = np.asarray(source.frequencies_hz, dtype=float)
        controller = PIControllerConfig(
            kp=float(point.kp), ti_s=float(point.ti_s), sample_time_s=1.0 / source.sample_rate_hz,
        ).transfer_function()
        loop = source.fixed_loop_response * controller.frequency_response(f)
        self._preview_loop = loop
        self.preview_figure.clear()
        axes = [self.preview_figure.add_subplot(2, 2, index) for index in range(1, 5)]
        curves = [("Candidate", f, loop, "#175cd3", "-")]
        if source.current_loop_response is not None:
            curves.insert(0, ("Current", f, np.asarray(source.current_loop_response), "#667085", "--"))
        for index, corner in enumerate(self._active_corners()[1:]):
            corner_f = np.asarray(corner.frequencies_hz, dtype=float)
            corner_loop = corner.fixed_loop_response * controller.frequency_response(corner_f)
            curves.append((f"Candidate @ {corner.corner_id.split(': ', 1)[0]}", corner_f,
                           corner_loop, f"C{(index + 1) % 10}", ":"))
        rows = []
        value = lambda x: "not observed" if x is None else f"{x:.7g}"
        for label, curve_f, response, color, style in curves:
            measured = calculate_stability_margins(curve_f, response)
            sens = 1.0 / (1.0 + response)
            comp = response / (1.0 + response)
            series = (
                20.0 * np.log10(np.maximum(np.abs(response), 1e-300)),
                np.degrees(np.unwrap(np.angle(response))),
                20.0 * np.log10(np.maximum(np.abs(sens), 1e-300)),
                20.0 * np.log10(np.maximum(np.abs(comp), 1e-300)),
            )
            for ax, y in zip(axes, series):
                ax.semilogx(curve_f, y, color=color, linestyle=style, label=label)
            rows.append(
                f"{label}: Fc={value(measured.critical_gain_crossover_hz)} Hz; "
                f"PM={value(measured.phase_margin_deg)} deg; GM={value(min(measured.gain_margins_db, default=None))} dB; "
                f"Ms={np.max(np.abs(sens)):.7g}; Mt={np.max(np.abs(comp)):.7g}"
            )
        for ax, title, ylabel in zip(axes,
                ("Open-loop magnitude", "Open-loop phase", "Sensitivity S", "Closed-loop response T"),
                ("|L| dB", "∠L deg", "|S| dB", "|T| dB")):
            ax.set_title(title)
            ax.set_ylabel(ylabel)
            ax.set_xlabel("Hz")
            ax.grid(True, which="both", alpha=0.25)
            ax.legend()
        coverage_note = (
            " | |L(Fsw)| not evaluated: switching frequency is outside the analyzed range."
            if point.switching_loop_gain_db is None else ""
        )
        self.comparison_summary.setText(
            f"{STATUS_LABELS[point.status]} across {len(point.corner_results) or 1} operating point(s). "
            + self._scope_note() + "\n" + "\n".join(rows)
            + "\nFrequency-domain comparison; switching transients require separate validation."
            + coverage_note
        )
        self.preview_figure.suptitle("One exact candidate PI across selected operating points", fontsize=10)
        self.preview_figure.tight_layout()
        self.preview_canvas.draw_idle()
        self.result_tabs.setCurrentIndex(1)
        coefficients = (
            f"Candidate H(z), Ts={controller.sample_time_s:.17g} s\n"
            f"b={[float(v) for v in controller.numerator]}\na={[float(v) for v in controller.denominator]}"
        )
        self.details.appendPlainText(
            "\nCURRENT / CANDIDATE\n" + "\n".join(rows) + "\n" + coefficients +
            "\nFrequency-domain comparison only; no switching transient or hardware validation is implied."
        )

    def can_apply(self, point: SolutionMapPoint) -> bool:
        return (point.kp is not None and point.ti_s is not None
                and (point.feasible or (point.status == SolutionStatus.CONDITIONAL
                                       and self.allow_conditional.isChecked())))

    def _update_apply_button(self, point: SolutionMapPoint) -> None:
        self.apply_button.setEnabled(self.can_apply(point))
        self.apply_button.setText("Apply conditional PI" if point.status == SolutionStatus.CONDITIONAL
                                  else "Apply selected PI")

    def _apply_selected(self) -> None:
        if self._intent is not None and self._intent.design_mode == "auto" and self._intent.structure != "PI":
            return
        source = self._source()
        if (
            source is not None
            and self.selected_point is not None
            and self.can_apply(self.selected_point)
        ):
            point = self.selected_point
            # Consume before emitting: synchronous re-entry / repeated clicks must
            # never reapply an obsolete candidate while the analysis is running.
            self.cancel_preview()
            self.details.setPlainText("Applying candidate. Waiting for the maintained loop analysis…")
            self.controller_selected.emit(source.key, point)


__all__ = ["LoopMapSource", "SolutionMapView"]

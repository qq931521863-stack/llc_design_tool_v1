"""Interactive Fc × PM Solution Map shared by LLC and TTPL workspaces."""
from __future__ import annotations

from dataclasses import dataclass
import math

import numpy as np
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.colors import BoundaryNorm, ListedColormap
from matplotlib.figure import Figure

from PySide6.QtCore import Signal
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from llc_design.control.solution_map import (
    STATUS_LABELS,
    SolutionMapConstraints,
    SolutionMapPoint,
    SolutionMapResult,
    SolutionStatus,
    build_fc_pm_solution_map,
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


class SolutionMapView(QWidget):
    """Fc × PM design-space browser driven by an already-built loop model."""

    controller_selected = Signal(str, object)

    def __init__(self, title: str = "Fc × PM Solution Map", parent=None) -> None:
        super().__init__(parent)
        self.sources: dict[str, LoopMapSource] = {}
        self.result: SolutionMapResult | None = None
        self.selected_point: SolutionMapPoint | None = None
        self._selection_artist = None

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
        self.run_button = QPushButton("Build Fc × PM Map")
        self.run_button.clicked.connect(self.build_map)
        header.addWidget(self.run_button)
        self.apply_button = QPushButton("Apply selected PI")
        self.apply_button.setEnabled(False)
        self.apply_button.clicked.connect(self._apply_selected)
        header.addWidget(self.apply_button)
        header.addStretch(1)
        root.addLayout(header)

        splitter = QSplitter()
        splitter.addWidget(self._build_controls())
        splitter.addWidget(self._build_results())
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([330, 1100])
        root.addWidget(splitter, 1)

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
        panel = QWidget(); layout = QVBoxLayout(panel)
        target = QGroupBox("Design space")
        form = QFormLayout(target)
        self.fc_min = self._double(0.01, 1e7, 2, 100.0, " Hz")
        self.fc_max = self._double(0.02, 1e7, 2, 10_000.0, " Hz")
        self.fc_points = QSpinBox(); self.fc_points.setRange(5, 120); self.fc_points.setValue(28)
        self.pm_min = self._double(1, 179, 2, 30.0, "°")
        self.pm_max = self._double(1, 179, 2, 80.0, "°")
        self.pm_points = QSpinBox(); self.pm_points.setRange(5, 120); self.pm_points.setValue(26)
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
        self.fc_tol = self._double(0.1, 100, 2, 5.0, "%")
        self.pm_tol = self._double(0.1, 90, 2, 3.0, "°")
        for label, widget in (
            ("GM minimum", self.gm_min), ("Ms maximum", self.ms_max),
            ("|L(Fsw)| maximum", self.switch_max), ("Fc target tolerance", self.fc_tol),
            ("PM target tolerance", self.pm_tol),
        ):
            form.addRow(label, widget)
        layout.addWidget(constraints)

        self.source_info = QLabel("No loop source loaded.")
        self.source_info.setWordWrap(True)
        self.source_info.setStyleSheet("padding:7px;border:1px solid #d0d5dd;border-radius:5px;")
        layout.addWidget(self.source_info)
        note = QLabel(
            "Map synthesis removes only the current controller from the maintained loop result. "
            "Plant, sensing, ADC, modulator/PWM/FM and delay remain unchanged. V9.3 maps the exact firmware Tustin PI structure first."
        )
        note.setWordWrap(True)
        note.setStyleSheet("color:#667085;padding:6px;")
        layout.addWidget(note)
        layout.addStretch(1)
        return panel

    def _build_results(self) -> QWidget:
        page = QWidget(); layout = QVBoxLayout(page)
        self.figure = Figure(figsize=(10, 6))
        self.canvas = FigureCanvasQTAgg(self.figure)
        self.canvas.mpl_connect("button_press_event", self._map_clicked)
        layout.addWidget(self.canvas, 1)
        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        self.details.setMaximumHeight(220)
        self.details.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        layout.addWidget(self.details)
        return page

    def set_sources(self, sources: list[LoopMapSource]) -> None:
        self.sources = {source.key: source for source in sources}
        current = self.source_combo.currentData()
        self.source_combo.blockSignals(True)
        self.source_combo.clear()
        for source in sources:
            self.source_combo.addItem(source.label, source.key)
        if current in self.sources:
            self.source_combo.setCurrentIndex(self.source_combo.findData(current))
        self.source_combo.blockSignals(False)
        self.result = None
        self.selected_point = None
        self.apply_button.setEnabled(False)
        self._source_changed()

    def _source(self) -> LoopMapSource | None:
        key = self.source_combo.currentData()
        return self.sources.get(str(key)) if key is not None else None

    def _source_changed(self) -> None:
        source = self._source()
        if source is None:
            self.source_info.setText("No loop source loaded.")
            self.run_button.setEnabled(False)
            return
        self.run_button.setEnabled(True)
        upper = min(0.20 * source.sample_rate_hz, 0.20 * source.switching_frequency_hz, source.frequencies_hz[-1] * 0.95)
        lower = max(source.frequencies_hz[0] * 2.0, upper / 200.0)
        if upper > lower:
            self.fc_min.setValue(lower)
            self.fc_max.setValue(upper)
        self.source_info.setText(
            f"{source.label}\nFs={source.sample_rate_hz/1e3:.5g} kHz | "
            f"Fsw={source.switching_frequency_hz/1e3:.5g} kHz\n{source.provenance}"
        )
        self.result = None
        self.selected_point = None
        self.apply_button.setEnabled(False)

    def _constraints(self) -> SolutionMapConstraints:
        return SolutionMapConstraints(
            minimum_gain_margin_db=self.gm_min.value(),
            maximum_sensitivity=self.ms_max.value(),
            maximum_switching_loop_gain_db=self.switch_max.value(),
            crossover_tolerance_fraction=self.fc_tol.value() / 100.0,
            phase_margin_tolerance_deg=self.pm_tol.value(),
        )

    def build_map(self) -> None:
        source = self._source()
        if source is None:
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
            self.result = build_fc_pm_solution_map(
                source.frequencies_hz,
                source.fixed_loop_response,
                sample_rate_hz=source.sample_rate_hz,
                switching_frequency_hz=source.switching_frequency_hz,
                crossover_targets_hz=fc,
                phase_margin_targets_deg=pm,
                loop_label=source.label,
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
        # One fixed visual identity per objective status. No ordering/ranking is implied.
        cmap = ListedColormap([
            "#d1fadf", "#e4e7ec", "#fef0c7", "#fee4e2",
            "#fedf89", "#fecdc9", "#d1e9ff", "#f2f4f7",
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
            "Click a map point to inspect exact PI parameters and constraints."
        )

    def _map_clicked(self, event) -> None:
        result = self.result
        if result is None or event.xdata is None or event.ydata is None or event.inaxes is None:
            return
        if event.xdata <= 0.0:
            return
        ix = int(np.argmin(np.abs(np.log(result.crossover_targets_hz) - math.log(event.xdata))))
        iy = int(np.argmin(np.abs(result.phase_margin_targets_deg - event.ydata)))
        point = result.point(iy, ix)
        self.selected_point = point
        self.apply_button.setEnabled(point.kp is not None and point.ti_s is not None)
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

    def _show_point(self, point: SolutionMapPoint) -> None:
        value = lambda x, fmt=".6g": "—" if x is None else format(x, fmt)
        self.details.setPlainText(
            "SOLUTION MAP POINT\n" + "="*68 + "\n"
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
        )

    def _apply_selected(self) -> None:
        source = self._source()
        if source is not None and self.selected_point is not None:
            self.controller_selected.emit(source.key, self.selected_point)


__all__ = ["LoopMapSource", "SolutionMapView"]

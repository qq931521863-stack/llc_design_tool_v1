"""Interactive PyQtGraph LLC waveform workbench.

The widget deliberately consumes :class:`WaveformBundle` only.  It does not
know whether the data came from the fast EDF reconstruction, the piecewise
switched solver, or a future measured/PLECS import.  This keeps the GUI
independent from the waveform engine.
"""

from __future__ import annotations

from typing import ClassVar

import pyqtgraph as pg
from PySide6.QtCore import QSignalBlocker, Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from shiboken6 import isValid

from ...dynamics.waveforms import WaveformBundle

pg.setConfigOptions(antialias=True)


class _WorkPointSpinBox(QDoubleSpinBox):
    """Keep IEEE double input precision without padding ordinary values with zeros."""
    def textFromValue(self, value):
        return str(value)


class WaveformView(QWidget):
    """Synchronized multi-trace waveform viewer with two time cursors."""

    fast_requested = Signal(dict)
    harmonic_requested = Signal(dict)
    detailed_requested = Signal(dict)
    inputs_changed = Signal()
    export_requested = Signal()

    GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
        (
            "关键节点",
            (
                "v_bridge", "i_resonant", "v_resonant_cap",
                "v_transformer_primary", "i_transformer_secondary",
                "i_output_cap", "v_output_ripple",
            ),
        ),
        (
            "桥臂与谐振腔",
            (
                "v_leg_a", "v_leg_b", "v_bridge", "i_resonant",
                "v_resonant_inductor", "v_resonant_cap", "energy_lr",
            ),
        ),
        (
            "变压器",
            (
                "v_transformer_primary", "i_transformer_primary",
                "i_magnetizing", "i_primary_load",
                "v_transformer_secondary", "i_transformer_secondary",
            ),
        ),
        (
            "次级与输出",
            (
                "v_transformer_secondary", "i_transformer_secondary",
                "v_rectified", "i_rectified", "i_load_output",
                "i_output_cap", "v_output_cap_internal", "v_output_ripple",
            ),
        ),
        (
            "一次开关",
            (
                "gate_q1", "vds_q1", "ids_q1",
                "gate_q2", "vds_q2", "ids_q2",
                "gate_q3", "vds_q3", "ids_q3",
                "gate_q4", "vds_q4", "ids_q4",
            ),
        ),
        (
            "同步整流",
            (
                "gate_sr1", "vds_sr1", "ids_sr1",
                "gate_sr2", "vds_sr2", "ids_sr2",
                "gate_sr3", "vds_sr3", "ids_sr3",
                "gate_sr4", "vds_sr4", "ids_sr4",
            ),
        ),
        (
            "磁件",
            (
                "b_transformer", "h_transformer",
                "b_resonant_inductor", "h_resonant_inductor", "energy_lr",
            ),
        ),
    )

    COMPONENT_SIGNALS: ClassVar[dict[str, tuple[str, ...]]] = {
        "bus": ("v_bus", "i_bus"),
        "bridge": ("v_bridge", "i_resonant"),
        "lr": ("v_resonant_inductor", "i_resonant"),
        "rseries": ("v_series_resistance", "i_resonant"),
        "cr": ("v_resonant_cap", "i_resonant"),
        "lm": ("v_transformer_primary", "i_magnetizing"),
        "primary": ("v_transformer_primary", "i_primary_load"),
        "secondary": ("v_transformer_secondary", "i_transformer_secondary"),
        "transformer": ("v_transformer_primary", "i_transformer_primary",
                        "v_transformer_secondary", "i_transformer_secondary"),
        "co": ("v_output_cap_internal", "i_output_cap"),
        "coesr": ("v_output_cap_esr", "i_output_cap"),
        "load": ("v_output", "i_load_output"),
        "output": ("v_output", "i_output_cap", "i_load_output"),
        "sr": ("v_rectified", "i_rectified"),
        **{f"q{i}": (f"vds_q{i}", f"ids_q{i}") for i in range(1, 5)},
        **{f"sr{i}": (f"vds_sr{i}", f"ids_sr{i}") for i in range(1, 5)},
    }

    def __init__(self, parent=None):
        super().__init__(parent)
        self.bundle: WaveformBundle | None = None
        self._plots: list[pg.PlotItem] = []
        self._cursor_a_lines: list[pg.InfiniteLine] = []
        self._cursor_b_lines: list[pg.InfiniteLine] = []
        self._cursor_guard = False
        self.selected_component = ""
        self.primary_topology = "FULL_BRIDGE"
        self.solver = "fast"
        self._exact_work_point = {}
        self.component_context = {}

        layout = QVBoxLayout(self)
        controls = QHBoxLayout()
        controls.addWidget(QLabel("工作点母线"))
        self.vbus = _WorkPointSpinBox()
        self.vbus.setRange(20.0, 2000.0)
        self.vbus.setDecimals(17)
        self.vbus.setValue(400.0)
        self.vbus.setSuffix(" V")
        controls.addWidget(self.vbus)
        controls.addWidget(QLabel("负载"))
        self.load_percent = _WorkPointSpinBox()
        self.load_percent.setRange(1.0, 150.0)
        self.load_percent.setDecimals(17)
        self.load_percent.setValue(100.0)
        self.load_percent.setSuffix(" %")
        controls.addWidget(self.load_percent)
        controls.addWidget(QLabel("波形组"))
        self.group_combo = QComboBox()
        self.group_combo.addItems([name for name, _ in self.GROUPS])
        controls.addWidget(self.group_combo)
        self.fast_button = QPushButton("快速 EDF 波形")
        self.harmonic_button = QPushButton("多谐波 HB 波形")
        self.detail_button = QPushButton("详细分段波形")
        self.fast_button.clicked.connect(lambda: self._request("fast"))
        self.harmonic_button.clicked.connect(lambda: self._request("harmonic"))
        self.detail_button.clicked.connect(lambda: self._request("detailed"))
        controls.addWidget(self.fast_button)
        controls.addWidget(self.harmonic_button)
        controls.addWidget(self.detail_button)
        controls.addStretch(1)
        layout.addLayout(controls)
        source_row = QHBoxLayout()
        self.source_label = QLabel("尚无当前工况结果；修改输入后请重新计算。")
        self.source_label.setWordWrap(True)
        source_row.addWidget(self.source_label, 1)
        self.export_button = QPushButton("导出当前波形与报告")
        self.export_button.setEnabled(False)
        self.export_button.clicked.connect(self.export_requested)
        source_row.addWidget(self.export_button)
        layout.addLayout(source_row)
        self.component_label = QLabel("器件：波形组；损耗与约束仅可引用同工况的独立分析。")
        self.component_label.setWordWrap(True)
        layout.addWidget(self.component_label)

        cursor_controls = QHBoxLayout()
        cursor_controls.addWidget(QLabel("游标 A"))
        self.cursor_a_us = QDoubleSpinBox()
        self.cursor_a_us.setRange(-1e9, 1e9)
        self.cursor_a_us.setDecimals(5)
        self.cursor_a_us.setSuffix(" µs")
        cursor_controls.addWidget(self.cursor_a_us)
        cursor_controls.addWidget(QLabel("游标 B"))
        self.cursor_b_us = QDoubleSpinBox()
        self.cursor_b_us.setRange(-1e9, 1e9)
        self.cursor_b_us.setDecimals(5)
        self.cursor_b_us.setSuffix(" µs")
        cursor_controls.addWidget(self.cursor_b_us)
        self.cursor_measurement = QLabel("Δt = —; 1/Δt = —")
        cursor_controls.addWidget(self.cursor_measurement)
        cursor_controls.addStretch(1)
        layout.addLayout(cursor_controls)

        self.graphics = pg.GraphicsLayoutWidget()
        # A parent's C++ teardown does not call GraphicsView.close().  Run its
        # Python scene/viewport cleanup while these children are still alive.
        graphics = self.graphics
        self.destroyed.connect(
            lambda: graphics.close() if isValid(graphics) and not graphics.closed else None)
        self.graphics.setBackground("w")
        graphics_scroll = QScrollArea()
        graphics_scroll.setWidgetResizable(True)
        graphics_scroll.setWidget(self.graphics)
        layout.addWidget(graphics_scroll, 1)

        self.statistics = QTableWidget(0, 13)
        self.statistics.setHorizontalHeaderLabels(
            [
                "信号", "单位", "频率", "平均", "总 RMS", "AC RMS", "最小", "最大",
                "峰峰值", "绝对峰值", "峰值因数", "基波 RMS", "THD",
            ]
        )
        self.statistics.setMaximumHeight(190)
        header = self.statistics.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        for column in range(1, self.statistics.columnCount()):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(self.statistics)

        self.group_combo.currentIndexChanged.connect(self._group_changed)
        self.vbus.valueChanged.connect(lambda: self._work_point_changed("vbus_v"))
        self.load_percent.valueChanged.connect(lambda: self._work_point_changed("load_fraction"))
        self.cursor_a_us.valueChanged.connect(self._cursor_spin_changed)
        self.cursor_b_us.valueChanged.connect(self._cursor_spin_changed)

    def work_point(self) -> dict[str, float]:
        return {
            "vbus_v": self._exact_work_point.get("vbus_v", self.vbus.value()),
            "load_fraction": self._exact_work_point.get("load_fraction", self.load_percent.value() / 100.0),
        }

    def set_nominal_work_point(self, vbus_v: float) -> None:
        self.set_work_point({"vbus_v": vbus_v, "load_fraction": 1.0})

    def set_work_point(self, work_point: dict) -> None:
        self._exact_work_point = dict(work_point)
        with QSignalBlocker(self.vbus), QSignalBlocker(self.load_percent):
            self.vbus.setValue(work_point["vbus_v"])
            self.load_percent.setValue(work_point["load_fraction"] * 100)
        self._work_point_changed()

    def _work_point_changed(self, key=None) -> None:
        if key is not None:
            self._exact_work_point.pop(key, None)
        self.invalidate()
        self.inputs_changed.emit()

    def invalidate(self) -> None:
        self.bundle = None
        self.component_context = {}
        self.export_button.setEnabled(False)
        self.source_label.setText("当前输入尚未计算，旧波形与导出已失效；请重新计算。")
        self.refresh()

    def _request(self, solver: str) -> None:
        changed = self.solver != solver
        self.solver = solver
        if changed:
            self._work_point_changed()
        else:
            self.invalidate()
        {"fast": self.fast_requested, "harmonic": self.harmonic_requested,
         "detailed": self.detailed_requested}[solver].emit(self.work_point())

    def request_current(self) -> None:
        self._request(self.solver)

    def set_primary_topology(self, topology) -> None:
        self.primary_topology = getattr(topology, "value", topology)
        if self.primary_topology == "HALF_BRIDGE" and self.selected_component in {"q3", "q4"}:
            self.selected_component = ""

    def _group_changed(self, *_args) -> None:
        self.selected_component = ""
        self.refresh()

    def set_busy(self, busy: bool) -> None:
        self.fast_button.setEnabled(not busy)
        self.harmonic_button.setEnabled(not busy)
        self.detail_button.setEnabled(not busy)

    def set_bundle(self, bundle: WaveformBundle) -> None:
        self.bundle = bundle
        self.export_button.setEnabled(True)
        metadata = bundle.metadata
        self.source_label.setText(
            f"来源：{bundle.model_name} | {metadata.get('vbus_v', self.vbus.value()):g} V | "
            f"负载 {100 * float(metadata.get('load_fraction', self.load_percent.value()/100)):g}% | "
            f"fs={bundle.switching_frequency_hz/1000:.6g} kHz | "
            f"请求 {str(metadata.get('request_identity', '未附来源身份'))[:16]}\n"
            + "；".join(bundle.warnings)
            + ("\n使用冻结等效电气输入重算；保存的来源不恢复 TD 损耗验证权限。"
               if "plant_inputs" in metadata else ""))
        if len(bundle.time_s):
            start = float(bundle.time_s[0] * 1e6)
            stop = float(bundle.time_s[-1] * 1e6)
            self.cursor_a_us.setValue(start + 0.25 * (stop - start))
            self.cursor_b_us.setValue(start + 0.75 * (stop - start))
        self.refresh()

    def select_component(self, key: str) -> None:
        if key in self.COMPONENT_SIGNALS:
            self.selected_component = key
            self.refresh()

    def _selected_keys(self) -> tuple[str, ...]:
        if self.bundle is None:
            return ()
        keys = self.COMPONENT_SIGNALS.get(self.selected_component)
        if keys is None:
            _, keys = self.GROUPS[self.group_combo.currentIndex()]
        if "v_transformer_terminal" in self.bundle.signals:
            if self.selected_component in {"transformer", "primary"}:
                keys = tuple("v_transformer_terminal" if key == "v_transformer_primary" else
                             "i_transformer_primary" if key == "i_primary_load" else key for key in keys)
            elif not self.selected_component and self.group_combo.currentIndex() == 2:
                keys = (*keys, "v_transformer_terminal", "v_transformer_core", "i_magnetizing_physical")
        return tuple(key for key in keys if key in self.bundle.signals and not (
            self.primary_topology == "HALF_BRIDGE" and key.endswith(("_q3", "_q4"))))

    def _cursor_spin_changed(self) -> None:
        if self._cursor_guard:
            return
        self._set_cursor_lines(self.cursor_a_us.value(), self.cursor_b_us.value())
        self._update_cursor_measurement()

    def _line_moved(self, source: str, position_us: float) -> None:
        if self._cursor_guard:
            return
        self._cursor_guard = True
        try:
            target = self.cursor_a_us if source == "a" else self.cursor_b_us
            target.setValue(float(position_us))
            lines = self._cursor_a_lines if source == "a" else self._cursor_b_lines
            for line in lines:
                if abs(line.value() - position_us) > 1e-12:
                    line.setValue(position_us)
        finally:
            self._cursor_guard = False
        self._update_cursor_measurement()

    def _set_cursor_lines(self, a_us: float, b_us: float) -> None:
        self._cursor_guard = True
        try:
            for line in self._cursor_a_lines:
                line.setValue(a_us)
            for line in self._cursor_b_lines:
                line.setValue(b_us)
        finally:
            self._cursor_guard = False

    def _update_cursor_measurement(self) -> None:
        delta_us = self.cursor_b_us.value() - self.cursor_a_us.value()
        if abs(delta_us) < 1e-15:
            self.cursor_measurement.setText("Δt = 0 µs; 1/Δt = —")
            return
        reciprocal_khz = 1e3 / abs(delta_us)
        self.cursor_measurement.setText(
            f"Δt = {delta_us:.6g} µs; 1/|Δt| = {reciprocal_khz:.6g} kHz"
        )

    def refresh(self) -> None:
        evidence = self.component_context.get(self.selected_component, {})
        self.component_label.setText(
            f"器件：{self.selected_component or self.group_combo.currentText()} | "
            + evidence.get("summary", "损耗/约束：无同源计算，不能引用其他工况。"))
        if self.bundle is not None and self.selected_component:
            missing = [key for key in self.COMPONENT_SIGNALS.get(self.selected_component, ())
                       if key not in self.bundle.signals]
            if missing:
                self.component_label.setText(self.component_label.text()
                                             + " | 本来源未提供：" + ", ".join(missing))
        self.statistics.setRowCount(0)
        self._plots.clear()
        self._cursor_a_lines.clear()
        self._cursor_b_lines.clear()
        # A caller may legitimately close the graphics child before its owner.
        # Clear result authority above, but never touch its deleted Qt scene.
        if not isValid(self.graphics) or self.graphics.closed or not isValid(self.graphics.ci):
            return
        self.graphics.clear()
        if self.bundle is None:
            self.graphics.setMinimumHeight(0)
            self.graphics.ci.setGeometry(self.graphics.range)
            return
        keys = self._selected_keys()
        # QGraphicsLayout retains its last expanded geometry after clear().
        # Resize the outer scroll child as well, otherwise longer groups clip
        # silently and selecting a two-trace device retains the old tall layout.
        self.graphics.setMinimumHeight(20 + 190 * len(keys))
        time_us = self.bundle.time_s * 1e6
        previous: pg.PlotItem | None = None
        for row, key in enumerate(keys):
            signal = self.bundle.signals[key]
            plot = self.graphics.addPlot(row=row, col=0)
            plot.setMinimumHeight(180)
            plot.showGrid(x=True, y=True, alpha=0.25)
            plot.setLabel("left", signal.label, units=signal.unit)
            if row == len(keys) - 1:
                plot.setLabel("bottom", "Time", units="µs")
            if previous is not None:
                plot.setXLink(previous)
            plot.plot(time_us, signal.values, pen=pg.mkPen("#1769aa", width=2.2))
            line_a = pg.InfiniteLine(
                pos=self.cursor_a_us.value(), angle=90, movable=(row == 0),
                pen=pg.mkPen((30, 120, 220), width=1.4), label="A" if row == 0 else None,
            )
            line_b = pg.InfiniteLine(
                pos=self.cursor_b_us.value(), angle=90, movable=(row == 0),
                pen=pg.mkPen((220, 100, 30), width=1.4), label="B" if row == 0 else None,
            )
            plot.addItem(line_a)
            plot.addItem(line_b)
            if row == 0:
                line_a.sigPositionChanged.connect(
                    lambda line: self._line_moved("a", float(line.value())))
                line_b.sigPositionChanged.connect(
                    lambda line: self._line_moved("b", float(line.value())))
            self._cursor_a_lines.append(line_a)
            self._cursor_b_lines.append(line_b)
            self._plots.append(plot)
            previous = plot

            stats = signal.statistics
            table_row = self.statistics.rowCount()
            self.statistics.insertRow(table_row)
            values = (
                signal.label,
                signal.unit,
                f"{stats.frequency_hz/1e3:.7g} kHz",
                f"{stats.average:.8g}",
                f"{stats.rms:.8g}",
                f"{stats.ac_rms:.8g}",
                f"{stats.minimum:.8g}",
                f"{stats.maximum:.8g}",
                f"{stats.peak_to_peak:.8g}",
                f"{stats.absolute_peak:.8g}",
                f"{stats.crest_factor:.6g}",
                f"{stats.fundamental_rms:.8g}",
                f"{stats.thd_percent:.5g}%",
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.statistics.setItem(table_row, column, item)
        # A two-signal component needs only two statistics rows. Reserving the
        # full group-table height unnecessarily pushes its current trace below
        # the viewport on laptop screens; larger groups keep table scrolling.
        table_height = (self.statistics.horizontalHeader().height()
                        + sum(self.statistics.rowHeight(row) for row in range(len(keys)))
                        + self.statistics.horizontalScrollBar().sizeHint().height()
                        + 2 * self.statistics.frameWidth())
        self.statistics.setMaximumHeight(min(190, table_height))
        # Hidden-tab selection does not trigger QScrollArea's resize pass. Its
        # previous multi-trace canvas otherwise survives as a large size hint.
        # Shrink to the viewport while retaining the explicit per-trace floor.
        viewport = self.graphics.parentWidget()
        self.graphics.resize(viewport.size().expandedTo(self.graphics.minimumSize()))
        self.graphics.ci.setGeometry(self.graphics.range)
        self._update_cursor_measurement()

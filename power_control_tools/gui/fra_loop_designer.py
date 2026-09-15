"""Interactive FRA-based loop controller design workspace.

V1 workflow:
1. Import Bode100 / SIMPLIS / generic frequency-gain-phase data.
2. Treat data as either plant TS directly or a complete measured loop TS.
3. For a complete loop, divide out the exact existing digital controller.
4. Apply a new controller and update Bode / margins / S/T in real time.
5. Export the final exact H(z) as the existing float32_t C99 implementation.
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure

from llc_design.gui import theme
from power_control_tools.codegen import export_c99_filter, render_c99_single_file, verify_c99_filter
from power_control_tools.controllers import design_controller
from power_control_tools.discretize import discretize_transfer_function
from power_control_tools.fra.analysis import (
    analyze_loop_response,
    deembed_controller,
    digital_frequency_response,
    magnitude_phase,
)
from power_control_tools.fra.importers import load_fra_file
from power_control_tools.fra.models import FRAMeasurement, FRAMeasurementKind, FRASourceFormat
from power_control_tools.models import ControllerKind, DigitalTransferFunction, DiscretizationMethod
from power_control_tools.gui.main_window import SliderSpin


_NEW_CONTROLLER_LABELS = {
    ControllerKind.PI: "PI — Kp + Ti",
    ControllerKind.PIF: "PIF — PI + LPF",
    ControllerKind.PID: "PID — Kp + Ti + Td",
    ControllerKind.TWO_P_TWO_Z: "2P2Z — K + 2 Zero + 2 Pole",
    ControllerKind.THREE_P_THREE_Z: "3P3Z — K + 3 Zero + 3 Pole",
}


class FRALoopDesignerWindow(QMainWindow):
    workspace_switch_requested = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("电源设计工具箱 — FRA Loop Designer")
        self.resize(1850, 1080)
        self.measurement: FRAMeasurement | None = None
        self.current_new_controller: DigitalTransferFunction | None = None
        self.current_metrics = None
        self.current_deembed = None
        self.current_plant = None
        self.current_loop = None
        self.current_usable_mask = None

        root = QWidget()
        row = QHBoxLayout(root)
        self.param_widget = self._build_parameters()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setWidget(self.param_widget)
        scroll.setMinimumWidth(520)
        scroll.setMaximumWidth(670)
        row.addWidget(scroll, 0)
        row.addWidget(self._build_tabs(), 1)
        self.setCentralWidget(root)
        self._build_toolbar()
        self.setStyleSheet(theme.workspace_stylesheet(theme.active_theme()))

        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(45)
        self.timer.timeout.connect(self.recalculate)
        self._update_visibility()

    def _build_toolbar(self) -> None:
        tb = self.addToolBar("FRA Loop Designer")
        tb.setMovable(False)
        for name, target in [("LLC", "llc"), ("PFC", "pfc"), ("Control Tools", "control"), ("功能选择", "home")]:
            action = QAction(name, self)
            action.triggered.connect(lambda checked=False, t=target: self.workspace_switch_requested.emit(t))
            tb.addAction(action)
        tb.addSeparator()
        action = QAction("导入 FRA", self)
        action.triggered.connect(self.import_fra)
        tb.addAction(action)
        action = QAction("重新计算", self)
        action.triggered.connect(self.recalculate)
        tb.addAction(action)
        action = QAction("导出 C99", self)
        action.triggered.connect(self.export_c99)
        tb.addAction(action)

    def _hook(self, widget) -> None:
        if hasattr(widget, "valueChanged"):
            widget.valueChanged.connect(lambda *_: self.schedule())
        if hasattr(widget, "currentIndexChanged"):
            widget.currentIndexChanged.connect(lambda *_: self._changed())
        if hasattr(widget, "textChanged"):
            widget.textChanged.connect(lambda *_: self.schedule())

    def _changed(self) -> None:
        self._update_visibility()
        self.schedule()

    def schedule(self) -> None:
        self.timer.start()

    @staticmethod
    def _field_visible(form: QFormLayout, widget: QWidget, visible: bool) -> None:
        widget.setVisible(visible)
        label = form.labelForField(widget)
        if label is not None:
            label.setVisible(visible)

    def _build_parameters(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        w.setMinimumWidth(500)

        source_group = QGroupBox("1. FRA / Bode 数据")
        f = QFormLayout(source_group)
        self.source_format = QComboBox()
        self.source_format.addItem("Bode 100 CSV", FRASourceFormat.BODE100)
        self.source_format.addItem("SIMPLIS TXT", FRASourceFormat.SIMPLIS)
        self.source_format.addItem("Generic Freq/Gain/Phase", FRASourceFormat.GENERIC)
        self.measurement_kind = QComboBox()
        self.measurement_kind.addItem("Plant TS — 数据不含控制器", FRAMeasurementKind.PLANT)
        self.measurement_kind.addItem("Complete Loop TS — 数据包含当前控制器", FRAMeasurementKind.COMPLETE_LOOP)
        self.phase_offset = QDoubleSpinBox()
        self.phase_offset.setRange(-720.0, 720.0)
        self.phase_offset.setDecimals(3)
        self.phase_offset.setSuffix(" °")
        self.phase_offset.setSingleStep(10.0)
        self.import_button = QPushButton("选择并导入 FRA 文件")
        self.import_button.clicked.connect(self.import_fra)
        self.file_label = QLabel("未导入")
        self.file_label.setWordWrap(True)
        for x in (self.source_format, self.measurement_kind, self.phase_offset):
            self._hook(x)
        f.addRow("Source", self.source_format)
        f.addRow("TS Type", self.measurement_kind)
        f.addRow("Phase Offset", self.phase_offset)
        f.addRow(self.import_button)
        f.addRow("File", self.file_label)
        v.addWidget(source_group)

        self.old_group = QGroupBox("2. 当前控制器 — 仅 Complete Loop 需要")
        f = QFormLayout(self.old_group)
        self.old_form = f
        self.old_mode = QComboBox()
        self.old_mode.addItem("Digital B/A coefficients — 推荐", "ba")
        self.old_mode.addItem("PI — Kp + Ti", "pi")
        self.old_structure = QComboBox()
        for label in ("PI", "PIF", "PID", "2P2Z", "3P3Z", "Custom H(z)"):
            self.old_structure.addItem(label)
        self.old_fs = SliderSpin(1_000.0, 1_000_000.0, 40_000.0, decimals=1, logarithmic=True, suffix=" Hz")
        self.old_b = QLineEdit("1.0")
        self.old_a = QLineEdit("1.0")
        self.old_kp = SliderSpin(1e-6, 1e4, 1.0, decimals=8, logarithmic=True)
        self.old_ti = SliderSpin(1e-7, 10.0, 0.01, decimals=8, logarithmic=True, suffix=" s")
        self.old_method = QComboBox()
        for method in DiscretizationMethod:
            self.old_method.addItem(method.value, method)
        for x in (self.old_mode, self.old_structure, self.old_fs, self.old_b, self.old_a, self.old_kp, self.old_ti, self.old_method):
            self._hook(x)
        f.addRow("Input", self.old_mode)
        f.addRow("Controller Type", self.old_structure)
        f.addRow("Fs", self.old_fs)
        f.addRow("b =", self.old_b)
        f.addRow("a =", self.old_a)
        f.addRow("Kp", self.old_kp)
        f.addRow("Ti", self.old_ti)
        f.addRow("S→Z", self.old_method)
        v.addWidget(self.old_group)

        new_group = QGroupBox("3. 新控制器 — Slider 实时整定")
        f = QFormLayout(new_group)
        self.new_form = f
        self.new_kind = QComboBox()
        for kind, label in _NEW_CONTROLLER_LABELS.items():
            self.new_kind.addItem(label, kind)
        self.new_fs = SliderSpin(1_000.0, 1_000_000.0, 40_000.0, decimals=1, logarithmic=True, suffix=" Hz")
        self.new_method = QComboBox()
        for method in DiscretizationMethod:
            self.new_method.addItem(method.value, method)
        self.new_kp = SliderSpin(1e-6, 1e4, 1.0, decimals=8, logarithmic=True)
        self.new_ti = SliderSpin(1e-7, 10.0, 0.01, decimals=8, logarithmic=True, suffix=" s")
        self.new_td = SliderSpin(1e-9, 1.0, 1e-4, decimals=9, logarithmic=True, suffix=" s")
        self.new_lpf = SliderSpin(0.1, 500_000.0, 10_000.0, decimals=2, logarithmic=True, suffix=" Hz")
        self.new_gain = SliderSpin(1e-6, 1e6, 1.0, decimals=8, logarithmic=True)
        self.new_fz1 = SliderSpin(0.1, 200_000.0, 300.0, decimals=2, logarithmic=True, suffix=" Hz")
        self.new_fz2 = SliderSpin(0.1, 200_000.0, 1_000.0, decimals=2, logarithmic=True, suffix=" Hz")
        self.new_fz3 = SliderSpin(0.1, 200_000.0, 3_000.0, decimals=2, logarithmic=True, suffix=" Hz")
        self.new_fp1 = SliderSpin(0.1, 500_000.0, 8_000.0, decimals=2, logarithmic=True, suffix=" Hz")
        self.new_fp2 = SliderSpin(0.1, 500_000.0, 20_000.0, decimals=2, logarithmic=True, suffix=" Hz")
        self.new_fp3 = SliderSpin(0.1, 500_000.0, 50_000.0, decimals=2, logarithmic=True, suffix=" Hz")
        for x in (
            self.new_kind, self.new_fs, self.new_method, self.new_kp, self.new_ti, self.new_td,
            self.new_lpf, self.new_gain, self.new_fz1, self.new_fz2, self.new_fz3,
            self.new_fp1, self.new_fp2, self.new_fp3,
        ):
            self._hook(x)
        f.addRow("Controller", self.new_kind)
        f.addRow("Fs", self.new_fs)
        f.addRow("S→Z", self.new_method)
        f.addRow("Kp", self.new_kp)
        f.addRow("Ti", self.new_ti)
        f.addRow("Td", self.new_td)
        f.addRow("LPF pole", self.new_lpf)
        f.addRow("Gain K", self.new_gain)
        f.addRow("Zero fz1", self.new_fz1)
        f.addRow("Zero fz2", self.new_fz2)
        f.addRow("Zero fz3", self.new_fz3)
        f.addRow("Pole fp1", self.new_fp1)
        f.addRow("Pole fp2", self.new_fp2)
        f.addRow("Pole fp3", self.new_fp3)
        self.new_fields = {
            self.new_kp, self.new_ti, self.new_td, self.new_lpf, self.new_gain,
            self.new_fz1, self.new_fz2, self.new_fz3, self.new_fp1, self.new_fp2, self.new_fp3,
        }
        v.addWidget(new_group)

        action_row = QHBoxLayout()
        copy_button = QPushButton("当前 PI → 新 PI")
        copy_button.clicked.connect(self.copy_existing_pi)
        reset_button = QPushButton("新控制器恢复默认")
        reset_button.clicked.connect(self.reset_new_controller)
        action_row.addWidget(copy_button)
        action_row.addWidget(reset_button)
        v.addLayout(action_row)

        result_group = QGroupBox("实时稳定性")
        rv = QVBoxLayout(result_group)
        self.summary = QPlainTextEdit()
        self.summary.setReadOnly(True)
        self.summary.setMaximumHeight(250)
        rv.addWidget(self.summary)
        v.addWidget(result_group)

        export_group = QGroupBox("输出")
        ef = QFormLayout(export_group)
        self.export_prefix = QLineEdit("FRA_CTRL")
        self._hook(self.export_prefix)
        export_button = QPushButton("导出最终 H(z) — C99 float32_t")
        export_button.clicked.connect(self.export_c99)
        ef.addRow("Symbol Prefix", self.export_prefix)
        ef.addRow(export_button)
        v.addWidget(export_group)
        v.addStretch(1)
        return w

    def _build_tabs(self) -> QTabWidget:
        tabs = QTabWidget()
        self.bode_fig = Figure(figsize=(9, 7))
        self.bode_canvas = FigureCanvasQTAgg(self.bode_fig)
        tabs.addTab(self.bode_canvas, "Loop Bode")
        self.st_fig = Figure(figsize=(9, 6))
        self.st_canvas = FigureCanvasQTAgg(self.st_fig)
        tabs.addTab(self.st_canvas, "S / T")
        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        tabs.addTab(self.details, "Analysis Details")
        self.output_text = QPlainTextEdit()
        self.output_text.setReadOnly(True)
        tabs.addTab(self.output_text, "H(z) / C99")
        return tabs

    def _update_visibility(self) -> None:
        complete = self.measurement_kind.currentData() == FRAMeasurementKind.COMPLETE_LOOP
        self.old_group.setVisible(complete)
        old_ba = self.old_mode.currentData() == "ba"
        self._field_visible(self.old_form, self.old_structure, old_ba)
        self._field_visible(self.old_form, self.old_b, old_ba)
        self._field_visible(self.old_form, self.old_a, old_ba)
        self._field_visible(self.old_form, self.old_kp, not old_ba)
        self._field_visible(self.old_form, self.old_ti, not old_ba)
        self._field_visible(self.old_form, self.old_method, not old_ba)

        kind = self.new_kind.currentData()
        visible: set[QWidget] = set()
        if kind == ControllerKind.PI:
            visible |= {self.new_kp, self.new_ti}
        elif kind == ControllerKind.PIF:
            visible |= {self.new_kp, self.new_ti, self.new_lpf}
        elif kind == ControllerKind.PID:
            visible |= {self.new_kp, self.new_ti, self.new_td}
        elif kind == ControllerKind.TWO_P_TWO_Z:
            visible |= {self.new_gain, self.new_fz1, self.new_fz2, self.new_fp1, self.new_fp2}
        elif kind == ControllerKind.THREE_P_THREE_Z:
            visible |= {self.new_gain, self.new_fz1, self.new_fz2, self.new_fz3, self.new_fp1, self.new_fp2, self.new_fp3}
        for widget in self.new_fields:
            self._field_visible(self.new_form, widget, widget in visible)

    @staticmethod
    def _parse_coefficients(text: str) -> tuple[float, ...]:
        values = [float(x) for x in text.replace(";", ",").replace(" ", ",").split(",") if x.strip()]
        if not values:
            raise ValueError("controller coefficients cannot be empty")
        return tuple(values)

    def _existing_controller(self) -> DigitalTransferFunction:
        fs = self.old_fs.value()
        if self.old_mode.currentData() == "ba":
            b = self._parse_coefficients(self.old_b.text())
            a = self._parse_coefficients(self.old_a.text())
            return DigitalTransferFunction(b, a, fs, self.old_structure.currentText(), "FRA existing B/A").normalized()
        analog = design_controller(ControllerKind.PI, kp=self.old_kp.value(), ti_s=self.old_ti.value())
        return discretize_transfer_function(analog, fs, self.old_method.currentData())

    def _new_controller(self) -> DigitalTransferFunction:
        kind = self.new_kind.currentData()
        kwargs = dict(
            kp=self.new_kp.value(),
            ti_s=self.new_ti.value(),
            td_s=self.new_td.value(),
            lpf_pole_hz=self.new_lpf.value(),
            gain=self.new_gain.value(),
            fz1_hz=self.new_fz1.value(),
            fz2_hz=self.new_fz2.value(),
            fz3_hz=self.new_fz3.value(),
            fp1_hz=self.new_fp1.value(),
            fp2_hz=self.new_fp2.value(),
            fp3_hz=self.new_fp3.value(),
        )
        analog = design_controller(kind, **kwargs)
        return discretize_transfer_function(analog, self.new_fs.value(), self.new_method.currentData())

    def import_fra(self) -> None:
        fmt = self.source_format.currentData()
        if fmt == FRASourceFormat.BODE100:
            file_filter = "Bode100 CSV (*.csv);;All files (*.*)"
        elif fmt == FRASourceFormat.SIMPLIS:
            file_filter = "SIMPLIS / Text (*.txt *.csv *.dat);;All files (*.*)"
        else:
            file_filter = "Frequency response (*.csv *.txt *.dat);;All files (*.*)"
        path, _ = QFileDialog.getOpenFileName(self, "导入 FRA / Bode 数据", str(Path.cwd()), file_filter)
        if not path:
            return
        try:
            measurement = load_fra_file(path, fmt)
            self.measurement = measurement
            self.phase_offset.setValue(measurement.default_phase_offset_deg)
            self.file_label.setText(
                f"{measurement.file_name}\n{len(measurement.frequency_hz)} points | "
                f"{measurement.frequency_hz[0]:.4g} Hz → {measurement.frequency_hz[-1]:.4g} Hz"
            )
            self.recalculate()
        except Exception as exc:
            QMessageBox.critical(self, "FRA 导入失败", str(exc))

    def copy_existing_pi(self) -> None:
        if self.old_mode.currentData() != "pi":
            QMessageBox.information(self, "仅 PI 可直接复制", "B/A 控制器无法在不做结构辨识的情况下唯一还原为 Kp/Ti 或零极点。")
            return
        idx = self.new_kind.findData(ControllerKind.PI)
        if idx >= 0:
            self.new_kind.setCurrentIndex(idx)
        self.new_fs.setValue(self.old_fs.value())
        self.new_kp.setValue(self.old_kp.value())
        self.new_ti.setValue(self.old_ti.value())
        method_index = self.new_method.findData(self.old_method.currentData())
        if method_index >= 0:
            self.new_method.setCurrentIndex(method_index)
        self.recalculate()

    def reset_new_controller(self) -> None:
        self.new_fs.setValue(40_000.0)
        self.new_kp.setValue(1.0)
        self.new_ti.setValue(0.01)
        self.new_td.setValue(1e-4)
        self.new_lpf.setValue(10_000.0)
        self.new_gain.setValue(1.0)
        self.new_fz1.setValue(300.0)
        self.new_fz2.setValue(1_000.0)
        self.new_fz3.setValue(3_000.0)
        self.new_fp1.setValue(8_000.0)
        self.new_fp2.setValue(20_000.0)
        self.new_fp3.setValue(50_000.0)
        self.recalculate()

    def recalculate(self) -> None:
        if self.measurement is None:
            self.summary.setPlainText("请先导入 FRA / Bode 数据。")
            return
        try:
            f = self.measurement.frequency_hz
            measured = self.measurement.complex_response(self.phase_offset.value())
            old_controller = None
            self.current_deembed = None
            if self.measurement_kind.currentData() == FRAMeasurementKind.COMPLETE_LOOP:
                old_controller = self._existing_controller()
                cold = digital_frequency_response(old_controller, f)
                self.current_deembed = deembed_controller(measured, cold)
                plant = self.current_deembed.equivalent_plant
            else:
                plant = measured.copy()

            new_controller = self._new_controller()
            cnew = digital_frequency_response(new_controller, f)
            loop = plant * cnew

            valid_limit = 0.49 * new_controller.sample_rate_hz
            if old_controller is not None:
                valid_limit = min(valid_limit, 0.49 * old_controller.sample_rate_hz)
            usable = f <= valid_limit
            if int(np.count_nonzero(usable)) < 2:
                raise ValueError("FRA frequency range does not contain at least two points below the controller Nyquist limit")
            metrics = analyze_loop_response(f[usable], loop[usable])

            self.current_new_controller = new_controller
            self.current_metrics = metrics
            self.current_plant = plant
            self.current_loop = loop
            self.current_usable_mask = usable
            self._render(measured, plant, cnew, loop, usable, valid_limit)
        except Exception as exc:
            self.summary.setPlainText("ERROR: " + str(exc))
            self.details.setPlainText("ERROR: " + str(exc))

    def _render(self, measured, plant, cnew, loop, usable, valid_limit: float) -> None:
        assert self.measurement is not None
        assert self.current_new_controller is not None
        assert self.current_metrics is not None
        f = self.measurement.frequency_hz
        fu = f[usable]
        metrics = self.current_metrics

        measured_mag, measured_phase = magnitude_phase(measured)
        plant_mag, plant_phase = magnitude_phase(plant)
        ctrl_mag, ctrl_phase = magnitude_phase(cnew[usable])
        loop_mag, loop_phase = magnitude_phase(loop[usable])

        self.bode_fig.clear()
        ax = self.bode_fig.add_subplot(211)
        axp = self.bode_fig.add_subplot(212, sharex=ax)
        ax.semilogx(f, measured_mag, label="Imported TS")
        axp.semilogx(f, measured_phase, label="Imported TS")
        if self.measurement_kind.currentData() == FRAMeasurementKind.COMPLETE_LOOP:
            ax.semilogx(f, plant_mag, label="Equivalent Plant = TS / C_old")
            axp.semilogx(f, plant_phase, label="Equivalent Plant = TS / C_old")
        else:
            ax.semilogx(f, plant_mag, label="Plant")
            axp.semilogx(f, plant_phase, label="Plant")
        ax.semilogx(fu, ctrl_mag, "--", label="New Controller")
        axp.semilogx(fu, ctrl_phase, "--", label="New Controller")
        ax.semilogx(fu, loop_mag, linewidth=2.0, label="New Open Loop")
        axp.semilogx(fu, loop_phase, linewidth=2.0, label="New Open Loop")
        ax.axhline(0.0, linewidth=0.8)
        axp.axhline(-180.0, linewidth=0.8)
        if metrics.main_crossover_hz is not None:
            ax.axvline(metrics.main_crossover_hz, linewidth=0.9, linestyle=":")
            axp.axvline(metrics.main_crossover_hz, linewidth=0.9, linestyle=":")
        ax.set_ylabel("Magnitude (dB)")
        axp.set_ylabel("Phase (deg)")
        axp.set_xlabel("Frequency (Hz)")
        ax.grid(True, which="both")
        axp.grid(True, which="both")
        ax.legend(loc="best")
        axp.legend(loc="best")
        self.bode_fig.tight_layout()
        self.bode_canvas.draw_idle()

        self.st_fig.clear()
        axs = self.st_fig.add_subplot(111)
        s_mag = 20.0 * np.log10(np.maximum(np.abs(metrics.sensitivity), 1e-300))
        t_mag = 20.0 * np.log10(np.maximum(np.abs(metrics.complementary_sensitivity), 1e-300))
        axs.semilogx(fu, s_mag, label="S = 1/(1+L)")
        axs.semilogx(fu, t_mag, label="T = L/(1+L)")
        axs.set_xlabel("Frequency (Hz)")
        axs.set_ylabel("Magnitude (dB)")
        axs.grid(True, which="both")
        axs.legend(loc="best")
        axs.set_title(f"Ms={metrics.ms:.4g} | Mt={metrics.mt:.4g}")
        self.st_fig.tight_layout()
        self.st_canvas.draw_idle()

        def fmt(value, suffix=""):
            return "N/A" if value is None or not math.isfinite(float(value)) else f"{float(value):.6g}{suffix}"

        summary_lines = [
            f"Status         {metrics.status}",
            f"Main Fc        {fmt(metrics.main_crossover_hz, ' Hz')}",
            f"PM             {fmt(metrics.phase_margin_deg, ' °')}",
            f"GM             {fmt(metrics.gain_margin_db, ' dB')}",
            f"Worst PM       {fmt(metrics.worst_phase_margin_deg, ' °')}",
            f"Worst GM       {fmt(metrics.worst_gain_margin_db, ' dB')}",
            f"Ms             {metrics.ms:.6g}",
            f"Mt             {metrics.mt:.6g}",
            f"0-dB crossings {len(metrics.gain_crossovers)}",
        ]
        if self.current_deembed is not None:
            summary_lines += [
                "",
                f"De-embed       {'PASS' if self.current_deembed.passed else 'FAIL'}",
                f"Rebuild |ΔG|   {self.current_deembed.magnitude_error_db_max:.3e} dB",
                f"Rebuild |Δφ|   {self.current_deembed.phase_error_deg_max:.3e} °",
            ]
        self.summary.setPlainText("\n".join(summary_lines))

        details = [
            "FRA LOOP DESIGN DETAILS",
            "=" * 72,
            f"File: {self.measurement.source_path}",
            f"Format: {self.measurement.source_format.value}",
            f"TS type: {self.measurement_kind.currentData().value}",
            f"Points: {len(f)}",
            f"Frequency: {f[0]:.8g} Hz -> {f[-1]:.8g} Hz",
            f"Applied phase offset: {self.phase_offset.value():.6g} deg",
            f"Loop-analysis upper limit: {valid_limit:.8g} Hz (0.49 x controller Fs)",
            "",
        ]
        if np.any(~usable):
            details.append("WARNING: imported data above the controller Nyquist design limit is shown as measurement context but excluded from stability margins.")
            details.append("")
        if self.current_deembed is not None:
            details += [
                "DE-EMBED RECONSTRUCTION",
                f"Magnitude max error: {self.current_deembed.magnitude_error_db_max:.6e} dB",
                f"Phase max error: {self.current_deembed.phase_error_deg_max:.6e} deg",
                f"Result: {'PASS' if self.current_deembed.passed else 'FAIL'}",
                "",
            ]
        details.append("GAIN CROSSOVERS")
        if metrics.gain_crossovers:
            for i, item in enumerate(metrics.gain_crossovers, 1):
                details.append(f"#{i}: Fc={item.frequency_hz:.8g} Hz, phase={item.phase_deg:.6g} deg, PM={item.phase_margin_deg:.6g} deg")
        else:
            details.append("none")
        details.append("")
        details.append("PHASE CROSSOVERS")
        if metrics.phase_crossovers:
            for i, item in enumerate(metrics.phase_crossovers, 1):
                details.append(f"#{i}: F={item.frequency_hz:.8g} Hz, phase={item.target_phase_deg:.3f} deg, gain={item.magnitude_db:.6g} dB, GM={item.gain_margin_db:.6g} dB")
        else:
            details.append("none in usable FRA range")
        self.details.setPlainText("\n".join(details))

        d = self.current_new_controller.normalized()
        coeff_lines = [
            "FINAL DIGITAL CONTROLLER",
            "H(z) = (b0 + b1 z^-1 + ...)/(1 + a1 z^-1 + ...)",
            "y[n] = Σ b[k]x[n-k] - Σ a[k]y[n-k]",
            f"Fs = {d.sample_rate_hz:.12g} Hz",
            "",
        ]
        coeff_lines += [f"b{i} = {value:+.12e}" for i, value in enumerate(d.b)]
        coeff_lines += [f"a{i} = {value:+.12e}" for i, value in enumerate(d.a)]
        coeff_lines += ["", render_c99_single_file(d, prefix=self.export_prefix.text().strip() or "FRA_CTRL")]
        self.output_text.setPlainText("\n".join(coeff_lines))

    def export_c99(self) -> None:
        if self.current_new_controller is None:
            QMessageBox.information(self, "没有可导出的控制器", "请先导入 FRA 并完成一次有效计算。")
            return
        prefix = self.export_prefix.text().strip() or "FRA_CTRL"
        default = str(Path.cwd() / f"{prefix.lower()}.h")
        path, _ = QFileDialog.getSaveFileName(self, "导出 FRA 设计控制器", default, "C99 Header (*.h)")
        if not path:
            return
        if not path.lower().endswith(".h"):
            path += ".h"
        try:
            out = export_c99_filter(self.current_new_controller, path, prefix=prefix)
            verify = verify_c99_filter(self.current_new_controller, out)
            QMessageBox.information(
                self,
                "C99 导出完成",
                f"{out.file_path}\n\n{verify.message}\nImpulse error={verify.impulse_max_abs_error:.3e}\nStep error={verify.step_max_abs_error:.3e}",
            )
        except Exception as exc:
            QMessageBox.critical(self, "C99 导出失败", str(exc))


__all__ = ["FRALoopDesignerWindow"]

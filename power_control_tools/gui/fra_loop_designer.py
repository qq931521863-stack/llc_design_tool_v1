"""Interactive FRA-based loop controller design workspace.

V1 workflow:
1. Import Bode100 / SIMPLIS / generic frequency-gain-phase data.
2. Treat data as either plant TS directly or a complete measured loop TS.
3. For a complete loop, divide out only the exact existing digital controller.
4. Quick-tune the existing controller or design a new controller structure.
5. Update Bode / margins / S/T in real time and export the final exact H(z).
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
from power_control_tools.fra.tuning import firmware_feedback_a_to_denominator, scale_digital_controller
from power_control_tools.gui.main_window import SliderSpin
from power_control_tools.models import ControllerKind, DigitalTransferFunction, DiscretizationMethod, StabilityClass


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
        self.current_old_controller: DigitalTransferFunction | None = None
        self.current_new_controller: DigitalTransferFunction | None = None
        self.current_metrics = None
        self.current_deembed = None
        self.current_plant = None
        self.current_loop = None
        self.current_usable_mask = None
        self.current_plant_valid_mask = None

        root = QWidget()
        row = QHBoxLayout(root)
        self.param_widget = self._build_parameters()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setWidget(self.param_widget)
        scroll.setMinimumWidth(540)
        scroll.setMaximumWidth(700)
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
            widget.textChanged.connect(lambda *_: self._changed())

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
        w.setMinimumWidth(510)

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
        self.old_coeff_convention = QComboBox()
        self.old_coeff_convention.addItem("Canonical: y = Σbx − Σay", "canonical")
        self.old_coeff_convention.addItem("Firmware +A: y = Σbx + ΣAy", "plus_a")
        self.old_b = QLineEdit("1.0")
        self.old_a = QLineEdit("1.0")
        self.old_kp = SliderSpin(1e-6, 1e4, 1.0, decimals=8, logarithmic=True)
        self.old_ti = SliderSpin(1e-7, 10.0, 0.01, decimals=8, logarithmic=True, suffix=" s")
        self.old_method = QComboBox()
        for method in DiscretizationMethod:
            self.old_method.addItem(method.value, method)
        self.old_coeff_note = QLabel(
            "Canonical 输入 denominator: 1,a1,a2...；Firmware +A 输入反馈 A1,A2...，软件转换 a=-A。"
        )
        self.old_coeff_note.setWordWrap(True)
        for x in (
            self.old_mode, self.old_structure, self.old_fs, self.old_coeff_convention,
            self.old_b, self.old_a, self.old_kp, self.old_ti, self.old_method,
        ):
            self._hook(x)
        f.addRow("Input", self.old_mode)
        f.addRow("Controller Type", self.old_structure)
        f.addRow("Fs", self.old_fs)
        f.addRow("B/A convention", self.old_coeff_convention)
        f.addRow("b =", self.old_b)
        f.addRow("a / A =", self.old_a)
        f.addRow(self.old_coeff_note)
        f.addRow("Kp", self.old_kp)
        f.addRow("Ti", self.old_ti)
        f.addRow("S→Z", self.old_method)
        v.addWidget(self.old_group)

        new_group = QGroupBox("3. 控制器整定 — Slider 实时更新")
        f = QFormLayout(new_group)
        self.new_form = f

        self.new_mode = QComboBox()
        self.new_mode.addItem("Quick Tune — 基于当前控制器", "quick")
        self.new_mode.addItem("New Structure — 重新设计控制器", "structure")
        self._hook(self.new_mode)
        f.addRow("Tuning Mode", self.new_mode)

        # Quick Tune controls. Unity scales are an exact baseline copy.
        self.quick_gain = SliderSpin(0.1, 10.0, 1.0, decimals=5, logarithmic=True, suffix=" ×")
        self.quick_ti = SliderSpin(0.2, 5.0, 1.0, decimals=5, logarithmic=True, suffix=" ×")
        self.quick_b_scales = [
            SliderSpin(0.2, 5.0, 1.0, decimals=5, logarithmic=True, suffix=" ×") for _ in range(4)
        ]
        self.quick_a_scales = [
            SliderSpin(0.2, 5.0, 1.0, decimals=5, logarithmic=True, suffix=" ×") for _ in range(3)
        ]
        for x in (self.quick_gain, self.quick_ti, *self.quick_b_scales, *self.quick_a_scales):
            self._hook(x)
        f.addRow("Loop Gain / Kp", self.quick_gain)
        f.addRow("Ti", self.quick_ti)
        for i, x in enumerate(self.quick_b_scales):
            f.addRow(f"b{i} shape", x)
        for i, x in enumerate(self.quick_a_scales, 1):
            f.addRow(f"a{i} feedback", x)
        self.quick_fields = {self.quick_gain, self.quick_ti, *self.quick_b_scales, *self.quick_a_scales}

        # New-structure controls reuse the existing Control Tools engine.
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
        self.structure_header_fields = {self.new_kind, self.new_fs, self.new_method}
        self.new_fields = {
            self.new_kp, self.new_ti, self.new_td, self.new_lpf, self.new_gain,
            self.new_fz1, self.new_fz2, self.new_fz3, self.new_fp1, self.new_fp2, self.new_fp3,
        }
        v.addWidget(new_group)

        action_row = QHBoxLayout()
        copy_button = QPushButton("当前 PI → New Structure")
        copy_button.clicked.connect(self.copy_existing_pi)
        reset_button = QPushButton("整定参数复位")
        reset_button.clicked.connect(self.reset_new_controller)
        action_row.addWidget(copy_button)
        action_row.addWidget(reset_button)
        v.addLayout(action_row)

        result_group = QGroupBox("实时稳定性")
        rv = QVBoxLayout(result_group)
        self.summary = QPlainTextEdit()
        self.summary.setReadOnly(True)
        self.summary.setMaximumHeight(285)
        rv.addWidget(self.summary)
        v.addWidget(result_group)

        export_group = QGroupBox("输出")
        ef = QFormLayout(export_group)
        self.export_prefix = QLineEdit("FRA_CTRL")
        self._hook(self.export_prefix)
        self.export_button = QPushButton("导出最终 H(z) — C99 float32_t")
        self.export_button.clicked.connect(self.export_c99)
        ef.addRow("Symbol Prefix", self.export_prefix)
        ef.addRow(self.export_button)
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

    def _is_complete_loop(self) -> bool:
        return self.measurement_kind.currentData() == FRAMeasurementKind.COMPLETE_LOOP

    def _is_quick_tune(self) -> bool:
        return self._is_complete_loop() and self.new_mode.currentData() == "quick"

    def _safe_old_lengths(self) -> tuple[int, int]:
        """Return normalized b/a lengths for visibility only; never raise while typing."""
        if self.old_mode.currentData() != "ba":
            return 0, 0
        try:
            b = self._parse_coefficients(self.old_b.text())
            raw_a = self._parse_coefficients(self.old_a.text())
            if self.old_coeff_convention.currentData() == "plus_a":
                a = firmware_feedback_a_to_denominator(raw_a)
            else:
                a = raw_a
            return len(b), len(a)
        except Exception:
            return 0, 0

    def _update_visibility(self) -> None:
        complete = self._is_complete_loop()
        self.old_group.setVisible(complete)

        old_ba = self.old_mode.currentData() == "ba"
        self._field_visible(self.old_form, self.old_structure, old_ba)
        self._field_visible(self.old_form, self.old_coeff_convention, old_ba)
        self._field_visible(self.old_form, self.old_b, old_ba)
        self._field_visible(self.old_form, self.old_a, old_ba)
        self.old_coeff_note.setVisible(old_ba)
        self._field_visible(self.old_form, self.old_kp, not old_ba)
        self._field_visible(self.old_form, self.old_ti, not old_ba)
        self._field_visible(self.old_form, self.old_method, not old_ba)
        if old_ba:
            if self.old_coeff_convention.currentData() == "plus_a":
                self.old_a.setPlaceholderText("A1,A2,... (do not include a0)")
            else:
                self.old_a.setPlaceholderText("1,a1,a2,...")

        # Quick Tune only exists when a current controller can be de-embedded.
        self._field_visible(self.new_form, self.new_mode, complete)
        quick = self._is_quick_tune()

        for widget in self.quick_fields:
            self._field_visible(self.new_form, widget, False)
        for widget in self.structure_header_fields | self.new_fields:
            self._field_visible(self.new_form, widget, False)

        if quick:
            self._field_visible(self.new_form, self.quick_gain, True)
            if old_ba:
                b_len, a_len = self._safe_old_lengths()
                for i, widget in enumerate(self.quick_b_scales):
                    self._field_visible(self.new_form, widget, i < b_len)
                for i, widget in enumerate(self.quick_a_scales):
                    self._field_visible(self.new_form, widget, i < max(a_len - 1, 0))
            else:
                self._field_visible(self.new_form, self.quick_ti, True)
            return

        for widget in self.structure_header_fields:
            self._field_visible(self.new_form, widget, True)
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
        if not all(math.isfinite(v) for v in values):
            raise ValueError("controller coefficients must be finite")
        return tuple(values)

    def _existing_controller(self) -> DigitalTransferFunction:
        fs = self.old_fs.value()
        if self.old_mode.currentData() == "ba":
            b = self._parse_coefficients(self.old_b.text())
            raw_a = self._parse_coefficients(self.old_a.text())
            if self.old_coeff_convention.currentData() == "plus_a":
                a = firmware_feedback_a_to_denominator(raw_a)
                source = "FRA existing firmware +A coefficients"
            else:
                a = raw_a
                source = "FRA existing canonical B/A"
            return DigitalTransferFunction(b, a, fs, self.old_structure.currentText(), source).normalized()
        analog = design_controller(ControllerKind.PI, kp=self.old_kp.value(), ti_s=self.old_ti.value())
        return discretize_transfer_function(analog, fs, self.old_method.currentData())

    def _quick_controller(self, old_controller: DigitalTransferFunction) -> DigitalTransferFunction:
        if self.old_mode.currentData() == "pi":
            kp = self.old_kp.value() * self.quick_gain.value()
            ti = self.old_ti.value() * self.quick_ti.value()
            analog = design_controller(ControllerKind.PI, kp=kp, ti_s=ti)
            return discretize_transfer_function(analog, self.old_fs.value(), self.old_method.currentData())

        d = old_controller.normalized()
        if len(d.b) > len(self.quick_b_scales) or len(d.a) - 1 > len(self.quick_a_scales):
            raise ValueError(
                "Quick Tune supports up to four numerator coefficients and three feedback coefficients (through 3P3Z). "
                "Use New Structure or exact expert coefficients for a higher-order controller."
            )
        b_scales = tuple(self.quick_b_scales[i].value() for i in range(len(d.b)))
        a_scales = tuple(self.quick_a_scales[i].value() for i in range(max(len(d.a) - 1, 0)))
        return scale_digital_controller(
            d,
            gain_scale=self.quick_gain.value(),
            numerator_scales=b_scales,
            denominator_scales=a_scales,
            name=f"{d.name} quick tune",
        )

    def _new_structure_controller(self) -> DigitalTransferFunction:
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

    def _new_controller(self, old_controller: DigitalTransferFunction | None) -> DigitalTransferFunction:
        if self._is_quick_tune():
            if old_controller is None:
                raise ValueError("Quick Tune requires Complete Loop TS and an existing controller")
            return self._quick_controller(old_controller)
        return self._new_structure_controller()

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
        if not self._is_complete_loop() or self.old_mode.currentData() != "pi":
            QMessageBox.information(self, "仅 PI 可直接复制", "请先选择 Complete Loop TS，并使用 PI Kp+Ti 作为当前控制器输入。")
            return
        idx = self.new_mode.findData("structure")
        if idx >= 0:
            self.new_mode.setCurrentIndex(idx)
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
        self.quick_gain.setValue(1.0)
        self.quick_ti.setValue(1.0)
        for x in (*self.quick_b_scales, *self.quick_a_scales):
            x.setValue(1.0)
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
            self.export_button.setEnabled(False)
            return
        try:
            f = self.measurement.frequency_hz
            measured = self.measurement.complex_response(self.phase_offset.value())
            old_controller = None
            self.current_deembed = None

            if self._is_complete_loop():
                old_controller = self._existing_controller()
                cold = digital_frequency_response(old_controller, f)
                self.current_deembed = deembed_controller(measured, cold)
                plant = self.current_deembed.equivalent_plant
                plant_valid = f <= (0.49 * old_controller.sample_rate_hz)
            else:
                plant = measured.copy()
                plant_valid = np.ones_like(f, dtype=bool)

            new_controller = self._new_controller(old_controller)
            cnew = digital_frequency_response(new_controller, f)
            loop = plant * cnew

            valid_limit = 0.49 * new_controller.sample_rate_hz
            if old_controller is not None:
                valid_limit = min(valid_limit, 0.49 * old_controller.sample_rate_hz)
            usable = f <= valid_limit
            if int(np.count_nonzero(usable)) < 2:
                raise ValueError("FRA frequency range does not contain at least two points below the controller Nyquist limit")
            metrics = analyze_loop_response(f[usable], loop[usable])

            self.current_old_controller = old_controller
            self.current_new_controller = new_controller
            self.current_metrics = metrics
            self.current_plant = plant
            self.current_loop = loop
            self.current_usable_mask = usable
            self.current_plant_valid_mask = plant_valid
            self.export_button.setEnabled(new_controller.implementable)
            self._render(measured, plant, cnew, loop, plant_valid, usable, valid_limit)
        except Exception as exc:
            self.current_new_controller = None
            self.summary.setPlainText("ERROR: " + str(exc))
            self.details.setPlainText("ERROR: " + str(exc))
            self.export_button.setEnabled(False)

    def _render(self, measured, plant, cnew, loop, plant_valid, usable, valid_limit: float) -> None:
        assert self.measurement is not None
        assert self.current_new_controller is not None
        assert self.current_metrics is not None
        f = self.measurement.frequency_hz
        fu = f[usable]
        fp = f[plant_valid]
        metrics = self.current_metrics
        controller = self.current_new_controller.normalized()

        measured_mag, measured_phase = magnitude_phase(measured)
        plant_mag, plant_phase = magnitude_phase(plant[plant_valid])
        ctrl_mag, ctrl_phase = magnitude_phase(cnew[usable])
        loop_mag, loop_phase = magnitude_phase(loop[usable])

        self.bode_fig.clear()
        ax = self.bode_fig.add_subplot(211)
        axp = self.bode_fig.add_subplot(212, sharex=ax)
        ax.semilogx(f, measured_mag, label="Imported TS")
        axp.semilogx(f, measured_phase, label="Imported TS")
        if self._is_complete_loop():
            ax.semilogx(fp, plant_mag, label="Equivalent Plant = TS / C_old")
            axp.semilogx(fp, plant_phase, label="Equivalent Plant = TS / C_old")
        else:
            ax.semilogx(fp, plant_mag, label="Plant")
            axp.semilogx(fp, plant_phase, label="Plant")
        ctrl_label = "Quick-Tuned Controller" if self._is_quick_tune() else "New Controller"
        ax.semilogx(fu, ctrl_mag, "--", label=ctrl_label)
        axp.semilogx(fu, ctrl_phase, "--", label=ctrl_label)
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

        controller_state = {
            StabilityClass.STABLE: "STABLE",
            StabilityClass.MARGINAL: "MARGINAL / INTEGRATOR",
            StabilityClass.UNSTABLE: "UNSTABLE",
        }[controller.stability_class]
        mode_text = "Quick Tune" if self._is_quick_tune() else "New Structure"
        summary_lines = [
            f"Mode           {mode_text}",
            f"Status         {metrics.status}",
            f"Controller     {controller_state} | max |p|={controller.max_pole_radius:.6g}",
            f"Main Fc        {fmt(metrics.main_crossover_hz, ' Hz')}",
            f"PM             {fmt(metrics.phase_margin_deg, ' °')}",
            f"GM             {fmt(metrics.gain_margin_db, ' dB')}",
            f"Worst PM       {fmt(metrics.worst_phase_margin_deg, ' °')}",
            f"Worst GM       {fmt(metrics.worst_gain_margin_db, ' dB')}",
            f"Ms             {metrics.ms:.6g}",
            f"Mt             {metrics.mt:.6g}",
            f"0-dB crossings {len(metrics.gain_crossovers)}",
        ]
        if metrics.gain_margin_db is None:
            summary_lines.append("GM evidence     phase crossover not observed in usable FRA range")
        if self.current_deembed is not None:
            summary_lines += [
                "",
                f"Math rebuild   {'PASS' if self.current_deembed.passed else 'FAIL'}",
                f"Rebuild |ΔG|   {self.current_deembed.magnitude_error_db_max:.3e} dB",
                f"Rebuild |Δφ|   {self.current_deembed.phase_error_deg_max:.3e} °",
                "NOTE: rebuild checks arithmetic only; it does not prove C_old provenance.",
            ]
        if controller.stability_class == StabilityClass.UNSTABLE:
            summary_lines += ["", "WARNING: tuned controller H(z) is unstable; C99 export is disabled."]
        self.summary.setPlainText("\n".join(summary_lines))

        details = [
            "FRA LOOP DESIGN DETAILS",
            "=" * 72,
            f"File: {self.measurement.source_path}",
            f"Format: {self.measurement.source_format.value}",
            f"TS type: {self.measurement_kind.currentData().value}",
            f"Tuning mode: {mode_text}",
            f"Points: {len(f)}",
            f"Frequency: {f[0]:.8g} Hz -> {f[-1]:.8g} Hz",
            f"Applied phase offset: {self.phase_offset.value():.6g} deg",
            f"Loop-analysis upper limit: {valid_limit:.8g} Hz (0.49 x relevant controller Fs)",
            "",
        ]
        if np.any(~usable):
            details.append("WARNING: imported data above the controller Nyquist design limit is shown as measurement context but excluded from stability margins.")
            details.append("")
        if self._is_complete_loop() and np.any(~plant_valid):
            details.append("WARNING: Equivalent Plant above old-controller 0.49*Fs is not plotted as a trusted extracted plant.")
            details.append("")
        if self.current_deembed is not None:
            details += [
                "DE-EMBED NUMERICAL RECONSTRUCTION",
                f"Magnitude max error: {self.current_deembed.magnitude_error_db_max:.6e} dB",
                f"Phase max error: {self.current_deembed.phase_error_deg_max:.6e} deg",
                f"Arithmetic result: {'PASS' if self.current_deembed.passed else 'FAIL'}",
                "This does not validate that the user-entered C_old matches the controller that produced the hardware measurement.",
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
            details.append("none in usable FRA range — GM is not proven by this measurement window")
        self.details.setPlainText("\n".join(details))

        coeff_lines = [
            "FINAL DIGITAL CONTROLLER",
            "H(z) = (b0 + b1 z^-1 + ...)/(1 + a1 z^-1 + ...)",
            "y[n] = Σ b[k]x[n-k] - Σ a[k]y[n-k]",
            f"Fs = {controller.sample_rate_hz:.12g} Hz",
            f"Stability = {controller.stability_class.value}; max |p| = {controller.max_pole_radius:.12g}",
            "",
        ]
        coeff_lines += [f"b{i} = {value:+.12e}" for i, value in enumerate(controller.b)]
        coeff_lines += [f"a{i} = {value:+.12e}" for i, value in enumerate(controller.a)]
        coeff_lines += ["", render_c99_single_file(controller, prefix=self.export_prefix.text().strip() or "FRA_CTRL")]
        self.output_text.setPlainText("\n".join(coeff_lines))

    def export_c99(self) -> None:
        if self.current_new_controller is None:
            QMessageBox.information(self, "没有可导出的控制器", "请先导入 FRA 并完成一次有效计算。")
            return
        if not self.current_new_controller.implementable:
            QMessageBox.critical(self, "控制器不可导出", "当前 H(z) 含单位圆外极点。请先恢复控制器稳定性。")
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

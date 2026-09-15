"""Interactive FRA-based loop controller design workspace.

V1 workflow:
1. Import Bode100 / SIMPLIS / generic frequency-gain-phase data.
2. Treat data as either plant TS directly or a complete measured loop TS.
3. For a complete loop, divide out only the exact existing digital controller.
4. Quick-tune the existing controller or design a new controller structure.
5. Update Bode / margins / S/T in real time and export the final exact H(z).
"""

from __future__ import annotations

import hashlib
import math
from pathlib import Path

import numpy as np
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QCheckBox,
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
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure

from llc_design.gui import theme
from llc_design.gui.help import install_help
from power_control_tools.codegen import export_c99_filter, render_c99_single_file, verify_c99_filter
from power_control_tools.controllers import CONTROLLER_LABELS, EXACT_CONTROLLER, controller_parameter_keys, design_controller
from power_control_tools.discretize import discretize_transfer_function
from power_control_tools.fra.analysis import (
    analyze_loop_response,
    deembed_controller,
    digital_frequency_response,
    magnitude_phase,
)
from power_control_tools.fra.fitting import fit_rational_frequency_response
from power_control_tools.fra.importers import load_fra_file
from power_control_tools.fra.loop_link import (
    STEP_OK,
    link_plant_model_with_controller,
)
from power_control_tools.fra.models import FRAMeasurement, FRAMeasurementKind, FRASourceFormat
from power_control_tools.fra.tuning import firmware_feedback_a_to_denominator, scale_digital_controller
from power_control_tools.gui.main_window import SliderSpin
from power_control_tools.models import ControllerKind, DigitalTransferFunction, DiscretizationMethod, StabilityClass


_PLANT_SOURCE_MEASURED = "measured"
_PLANT_SOURCE_IDENTIFIED = "identified"


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
        self.current_frequency_hz: np.ndarray | None = None
        # Plant model identified by the FRA Model Identification dialog.
        self.identified_plant = None
        self.identified_plant_band: tuple[float, float] | None = None
        self.identified_plant_confidence: str | None = None
        self.current_step = None
        self.current_step_status = "DISABLED"
        self._plant_model_cache_key: str | None = None
        self._plant_model_cache = None
        self._plant_model_cache_confidence: str | None = None

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
        install_help(self, "fra")

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

        # Plant source lets an identified (Model ID) rational model replace the
        # raw measured points, so the whole controller catalogue below can be
        # applied to the identified transfer function.
        self.plant_source = QComboBox()
        self.plant_source.addItem("Measured TS — 导入的 FRA 数据", _PLANT_SOURCE_MEASURED)
        self.plant_source.addItem("Identified model — Model ID 回传模型", _PLANT_SOURCE_IDENTIFIED)
        self._hook(self.plant_source)
        self.identified_label = QLabel("未回传辨识模型：Advanced → Model ID / Fit 完成后点击“用于环路设计”。")
        self.identified_label.setWordWrap(True)
        self.clear_identified_button = QPushButton("清除辨识模型")
        self.clear_identified_button.clicked.connect(self.clear_identified_plant)
        f.addRow("Plant Source", self.plant_source)
        f.addRow(self.identified_label)
        f.addRow(self.clear_identified_button)
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

        # New-structure controls reuse the existing Control Tools engine and now
        # expose the complete shared controller catalogue.
        self.new_kind = QComboBox()
        for kind, label in CONTROLLER_LABELS.items():
            self.new_kind.addItem(label, kind)
        self.new_kind.addItem("Custom H(z) — exact coefficients", EXACT_CONTROLLER)
        self.new_fs = SliderSpin(1_000.0, 1_000_000.0, 40_000.0, decimals=1, logarithmic=True, suffix=" Hz")
        self.new_method = QComboBox()
        for method in DiscretizationMethod:
            self.new_method.addItem(method.value, method)
        self.new_type_input_mode = QComboBox()
        self.new_type_input_mode.addItem("Pole / Zero", "pz")
        self.new_type_input_mode.addItem("R / C Components", "rc")
        self.new_kp = SliderSpin(1e-6, 1e4, 1.0, decimals=8, logarithmic=True)
        self.new_ti = SliderSpin(1e-7, 10.0, 0.01, decimals=8, logarithmic=True, suffix=" s")
        self.new_td = SliderSpin(1e-9, 1.0, 1e-4, decimals=9, logarithmic=True, suffix=" s")
        self.new_lpf = SliderSpin(0.1, 500_000.0, 10_000.0, decimals=2, logarithmic=True, suffix=" Hz")
        self.new_gain = SliderSpin(1e-6, 1e6, 1.0, decimals=8, logarithmic=True)
        self.new_fp0 = SliderSpin(0.01, 200_000.0, 100.0, decimals=2, logarithmic=True, suffix=" Hz")
        self.new_fz1 = SliderSpin(0.1, 200_000.0, 300.0, decimals=2, logarithmic=True, suffix=" Hz")
        self.new_fz2 = SliderSpin(0.1, 200_000.0, 1_000.0, decimals=2, logarithmic=True, suffix=" Hz")
        self.new_fz3 = SliderSpin(0.1, 200_000.0, 3_000.0, decimals=2, logarithmic=True, suffix=" Hz")
        self.new_fp1 = SliderSpin(0.1, 500_000.0, 8_000.0, decimals=2, logarithmic=True, suffix=" Hz")
        self.new_fp2 = SliderSpin(0.1, 500_000.0, 20_000.0, decimals=2, logarithmic=True, suffix=" Hz")
        self.new_fp3 = SliderSpin(0.1, 500_000.0, 50_000.0, decimals=2, logarithmic=True, suffix=" Hz")
        self.new_r1 = SliderSpin(10.0, 10_000_000.0, 10_000.0, decimals=1, logarithmic=True, suffix=" Ω")
        self.new_r2 = SliderSpin(10.0, 10_000_000.0, 47_000.0, decimals=1, logarithmic=True, suffix=" Ω")
        self.new_r3 = SliderSpin(10.0, 10_000_000.0, 10_000.0, decimals=1, logarithmic=True, suffix=" Ω")
        self.new_c1 = SliderSpin(0.001, 100_000.0, 10.0, decimals=4, logarithmic=True, suffix=" nF")
        self.new_c2 = SliderSpin(0.001, 100_000.0, 0.47, decimals=4, logarithmic=True, suffix=" nF")
        self.new_c3 = SliderSpin(0.001, 100_000.0, 1.0, decimals=4, logarithmic=True, suffix=" nF")
        self.new_general_num = QLineEdit("1")
        self.new_general_den = QLineEdit("1, 1")
        self.new_exact_b = QLineEdit("0.1, -0.09")
        self.new_exact_a = QLineEdit("1, -1.9, 0.91")
        for x in (
            self.new_kind, self.new_fs, self.new_method, self.new_type_input_mode,
            self.new_kp, self.new_ti, self.new_td, self.new_lpf, self.new_gain, self.new_fp0,
            self.new_fz1, self.new_fz2, self.new_fz3, self.new_fp1, self.new_fp2, self.new_fp3,
            self.new_r1, self.new_r2, self.new_r3, self.new_c1, self.new_c2, self.new_c3,
            self.new_general_num, self.new_general_den, self.new_exact_b, self.new_exact_a,
        ):
            self._hook(x)
        f.addRow("Controller", self.new_kind)
        f.addRow("Fs", self.new_fs)
        f.addRow("S→Z", self.new_method)
        f.addRow("Type-II/III input", self.new_type_input_mode)
        f.addRow("Kp", self.new_kp)
        f.addRow("Ti", self.new_ti)
        f.addRow("Td", self.new_td)
        f.addRow("LPF pole", self.new_lpf)
        f.addRow("Gain K", self.new_gain)
        f.addRow("Integrator fp0", self.new_fp0)
        f.addRow("Zero fz1", self.new_fz1)
        f.addRow("Zero fz2", self.new_fz2)
        f.addRow("Zero fz3", self.new_fz3)
        f.addRow("Pole fp1", self.new_fp1)
        f.addRow("Pole fp2", self.new_fp2)
        f.addRow("Pole fp3", self.new_fp3)
        f.addRow("R1", self.new_r1)
        f.addRow("R2", self.new_r2)
        f.addRow("R3", self.new_r3)
        f.addRow("C1", self.new_c1)
        f.addRow("C2", self.new_c2)
        f.addRow("C3", self.new_c3)
        f.addRow("General numerator", self.new_general_num)
        f.addRow("General denominator", self.new_general_den)
        f.addRow("Exact b =", self.new_exact_b)
        f.addRow("Exact a =", self.new_exact_a)
        self.structure_header_fields = {self.new_kind, self.new_fs, self.new_method}
        self.new_field_widgets = {
            "gain": self.new_gain, "kp": self.new_kp, "ti": self.new_ti, "td": self.new_td,
            "lpf_pole": self.new_lpf, "fp0": self.new_fp0,
            "fz1": self.new_fz1, "fz2": self.new_fz2, "fz3": self.new_fz3,
            "fp1": self.new_fp1, "fp2": self.new_fp2, "fp3": self.new_fp3,
            "r1": self.new_r1, "r2": self.new_r2, "r3": self.new_r3,
            "c1": self.new_c1, "c2": self.new_c2, "c3": self.new_c3,
            "numerator": self.new_general_num, "denominator": self.new_general_den,
            "type_input_mode": self.new_type_input_mode,
        }
        self.new_exact_fields = {self.new_exact_b, self.new_exact_a}
        self.new_fields = set(self.new_field_widgets.values()) | self.new_exact_fields
        v.addWidget(new_group)

        action_row = QHBoxLayout()
        copy_button = QPushButton("当前 PI → New Structure")
        copy_button.clicked.connect(self.copy_existing_pi)
        reset_button = QPushButton("整定参数复位")
        reset_button.clicked.connect(self.reset_new_controller)
        action_row.addWidget(copy_button)
        action_row.addWidget(reset_button)
        v.addLayout(action_row)

        step_group = QGroupBox("闭环 Step（需要有理被控对象模型）")
        sf = QFormLayout(step_group)
        self.step_enable = QCheckBox("计算闭环 Step")
        self.step_enable.stateChanged.connect(lambda *_: self.schedule())
        self.step_max_order = QSpinBox()
        self.step_max_order.setRange(1, 5)
        self.step_max_order.setValue(3)
        self.step_max_order.valueChanged.connect(lambda *_: self._step_setting_changed())
        self.step_samples = QSpinBox()
        self.step_samples.setRange(200, 20_000)
        self.step_samples.setSingleStep(200)
        self.step_samples.setValue(1_500)
        self.step_samples.valueChanged.connect(lambda *_: self.schedule())
        self.step_note = QLabel(
            "Measured TS 需要先辨识有理模型（首次拟合较慢，之后按被控对象缓存）；"
            "回传的 Model ID 辨识模型可直接计算。Step 为模型推算结果，不能替代实测瞬态。"
        )
        self.step_note.setWordWrap(True)
        sf.addRow(self.step_enable)
        sf.addRow("Plant fit max poles", self.step_max_order)
        sf.addRow("Step samples", self.step_samples)
        sf.addRow(self.step_note)
        v.addWidget(step_group)

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
        self.export_button.setEnabled(False)
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
        self.step_fig = Figure(figsize=(9, 5))
        self.step_canvas = FigureCanvasQTAgg(self.step_fig)
        tabs.addTab(self.step_canvas, "Closed-Loop Step")
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
        # Quick Tune scales the de-embedded controller of a complete-loop
        # measurement; it is meaningless when the identified model is the plant.
        return (
            not self._identified_plant_active()
            and self._is_complete_loop()
            and self.new_mode.currentData() == "quick"
        )

    # ------------------------------------------------------------------
    # Identified (Model ID) plant model as an alternative plant source
    # ------------------------------------------------------------------
    def _identified_plant_active(self) -> bool:
        return self.plant_source.currentData() == _PLANT_SOURCE_IDENTIFIED

    def set_identified_plant(self, model, band_hz, confidence: str) -> None:
        """Receive a fitted plant model from the FRA Model Identification dialog."""
        low, high = (float(band_hz[0]), float(band_hz[1]))
        if not (low > 0.0 and high > low):
            raise ValueError("identified model band must satisfy 0 < f_min < f_max")
        self.identified_plant = model
        self.identified_plant_band = (low, high)
        self.identified_plant_confidence = str(confidence)
        self._invalidate_plant_model()
        self.identified_label.setText(
            f"已回传辨识模型：order={getattr(model, 'order', '?')} | "
            f"band={low:.5g}–{high:.5g} Hz | confidence={confidence}"
        )
        index = self.plant_source.findData(_PLANT_SOURCE_IDENTIFIED)
        if index >= 0:
            self.plant_source.setCurrentIndex(index)
        self.step_enable.setChecked(True)
        self.recalculate()

    def clear_identified_plant(self) -> None:
        self.identified_plant = None
        self.identified_plant_band = None
        self.identified_plant_confidence = None
        self._invalidate_plant_model()
        self.identified_label.setText("未回传辨识模型：Advanced → Model ID / Fit 完成后点击“用于环路设计”。")
        index = self.plant_source.findData(_PLANT_SOURCE_MEASURED)
        if index >= 0:
            self.plant_source.setCurrentIndex(index)
        self.recalculate()

    def _invalidate_plant_model(self) -> None:
        self._plant_model_cache_key = None
        self._plant_model_cache = None
        self._plant_model_cache_confidence = None

    def _step_setting_changed(self) -> None:
        self._invalidate_plant_model()
        self.schedule()

    def _analysis_frequency_hz(self) -> np.ndarray:
        if self._identified_plant_active():
            if self.identified_plant is None or self.identified_plant_band is None:
                raise ValueError(
                    "尚未回传辨识模型：请先在 Advanced → Model ID / Fit 完成辨识，再点击“用于环路设计”。"
                )
            low, high = self.identified_plant_band
            if self.measurement is not None:
                f = np.asarray(self.measurement.frequency_hz, dtype=float)
                inside = f[(f >= low) & (f <= high)]
                if inside.size >= 8:
                    return inside
            return np.geomspace(low, high, 800)
        if self.measurement is None:
            raise ValueError("请先导入 FRA / Bode 数据，或回传 Model ID 辨识模型作为被控对象。")
        return np.asarray(self.measurement.frequency_hz, dtype=float)

    def _plant_model_for_step(self, f: np.ndarray, plant: np.ndarray):
        """Return ``(model, band, confidence)`` for the closed-loop step.

        An identified model is used directly.  A measured plant is fitted once
        per plant/band/order combination and cached, so slider moves reuse it.
        """
        if self._identified_plant_active() and self.identified_plant is not None:
            return self.identified_plant, self.identified_plant_band, self.identified_plant_confidence
        order = int(self.step_max_order.value())
        digest = hashlib.sha1()
        for array in (np.ascontiguousarray(f, dtype=float), np.ascontiguousarray(np.asarray(plant, dtype=complex).real), np.ascontiguousarray(np.asarray(plant, dtype=complex).imag)):
            digest.update(array.tobytes())
        digest.update(str(order).encode("ascii"))
        key = digest.hexdigest()
        if key == self._plant_model_cache_key and self._plant_model_cache is not None:
            return self._plant_model_cache, (float(f[0]), float(f[-1])), self._plant_model_cache_confidence
        focus = None
        if self.current_metrics is not None and self.current_metrics.main_crossover_hz is not None:
            focus = float(self.current_metrics.main_crossover_hz)
        result = fit_rational_frequency_response(
            f, plant, max_poles=order, focus_hz=focus, fit_delay=True, fit_target="Current plant"
        )
        self._plant_model_cache_key = key
        self._plant_model_cache = result.model
        self._plant_model_cache_confidence = result.metrics.confidence
        return result.model, (float(f[0]), float(f[-1])), result.metrics.confidence

    def _render_step(self, f: np.ndarray, plant: np.ndarray, usable: np.ndarray) -> list[str]:
        """Render the closed-loop step tab and return summary lines."""
        self.step_fig.clear()
        axis = self.step_fig.add_subplot(111)
        self.current_step = None
        self.current_step_status = "DISABLED"
        if not self.step_enable.isChecked():
            axis.text(0.5, 0.5, "Closed-loop step disabled\n(tick the step checkbox to enable)", ha="center", va="center", transform=axis.transAxes)
            self.step_fig.tight_layout()
            self.step_canvas.draw_idle()
            return ["Step           disabled"]

        controller = self.current_new_controller
        if controller is None:
            return ["Step           unavailable"]
        band_mask = np.asarray(usable, dtype=bool) & np.asarray(self.current_plant_valid_mask, dtype=bool)
        if int(np.count_nonzero(band_mask)) < 8:
            axis.text(0.5, 0.5, "Not enough usable plant points (<8)", ha="center", va="center", transform=axis.transAxes)
            self.step_fig.tight_layout()
            self.step_canvas.draw_idle()
            return ["Step           unavailable (too few plant points)"]
        f_use = np.asarray(f, dtype=float)[band_mask]
        plant_use = np.asarray(plant, dtype=complex)[band_mask]
        try:
            model, band, confidence = self._plant_model_for_step(f_use, plant_use)
            if band is None:
                raise ValueError("被控对象模型缺少辨识频带信息")
            link = link_plant_model_with_controller(
                model,
                controller,
                f_min_hz=float(band[0]),
                f_max_hz=float(band[1]),
                points=min(max(f_use.size, 200), 1000),
                fit_confidence=confidence,
                step_samples=int(self.step_samples.value()),
            )
        except Exception as exc:
            axis.text(0.5, 0.5, "Step computation failed:\n" + str(exc), ha="center", va="center", transform=axis.transAxes, wrap=True)
            self.step_fig.tight_layout()
            self.step_canvas.draw_idle()
            return [f"Step           ERROR: {exc}"]

        lines = [
            f"Step status    {link.step_status}",
            f"Plant model    fit confidence={confidence} | band={band[0]:.5g}–{band[1]:.5g} Hz",
        ]
        step = link.step
        if step is not None and step.stable and step.time_s.size:
            axis.plot(step.time_s * 1e3, step.response, linewidth=1.8, label="Closed-loop step")
            axis.axhline(step.final_value, linestyle=":", linewidth=0.9, label="Final value")
            axis.set_xlabel("Time (ms)")
            axis.set_ylabel("Amplitude")
            axis.grid(True, which="both")
            title = f"Model-derived step | overshoot={step.overshoot_percent:.3g}%"
            if step.settling_time_s is not None:
                title += f" | Ts={step.settling_time_s * 1e3:.4g} ms"
            axis.set_title(title)
            axis.legend(loc="best")
            lines.append(f"Step overshoot {step.overshoot_percent:.6g} %")
            lines.append(
                "Step settling  "
                + ("N/A" if step.settling_time_s is None else f"{step.settling_time_s * 1e3:.6g} ms")
            )
            lines.append(f"Step final     {step.final_value:.6g}")
            self.current_step = step
        else:
            axis.text(0.5, 0.5, link.step_note, ha="center", va="center", transform=axis.transAxes, wrap=True)
            axis.set_title("Closed-loop step withheld")
        lines.append("Step note      " + link.step_note)
        self.current_step_status = link.step_status
        self.step_fig.tight_layout()
        self.step_canvas.draw_idle()
        return lines

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
        if kind == EXACT_CONTROLLER:
            visible |= self.new_exact_fields
        else:
            # Qt hands back the enum's str value, not the enum member.
            keys = controller_parameter_keys(ControllerKind(kind), type_input_mode=self.new_type_input_mode.currentData())
            visible |= {self.new_field_widgets[key] for key in keys}
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
        if kind == EXACT_CONTROLLER:
            b = self._parse_coefficients(self.new_exact_b.text())
            a = self._parse_coefficients(self.new_exact_a.text())
            return DigitalTransferFunction(
                b, a, self.new_fs.value(), "Custom H(z)", "FRA exact H(z) entry"
            ).normalized()
        kind = ControllerKind(kind)
        kwargs = dict(
            gain=self.new_gain.value(),
            kp=self.new_kp.value(),
            ti_s=self.new_ti.value(),
            td_s=self.new_td.value(),
            lpf_pole_hz=self.new_lpf.value(),
            fp0_hz=self.new_fp0.value(),
            fz1_hz=self.new_fz1.value(),
            fz2_hz=self.new_fz2.value(),
            fz3_hz=self.new_fz3.value(),
            fp1_hz=self.new_fp1.value(),
            fp2_hz=self.new_fp2.value(),
            fp3_hz=self.new_fp3.value(),
            type_input_mode=self.new_type_input_mode.currentData(),
            r1_ohm=self.new_r1.value(),
            r2_ohm=self.new_r2.value(),
            r3_ohm=self.new_r3.value(),
            c1_f=self.new_c1.value() * 1e-9,
            c2_f=self.new_c2.value() * 1e-9,
            c3_f=self.new_c3.value() * 1e-9,
        )
        if kind == ControllerKind.GENERAL:
            kwargs["numerator"] = self._parse_coefficients(self.new_general_num.text())
            kwargs["denominator"] = self._parse_coefficients(self.new_general_den.text())
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
        self.new_fp0.setValue(100.0)
        self.new_fz1.setValue(300.0)
        self.new_fz2.setValue(1_000.0)
        self.new_fz3.setValue(3_000.0)
        self.new_fp1.setValue(8_000.0)
        self.new_fp2.setValue(20_000.0)
        self.new_fp3.setValue(50_000.0)
        self.new_r1.setValue(10_000.0)
        self.new_r2.setValue(47_000.0)
        self.new_r3.setValue(10_000.0)
        self.new_c1.setValue(10.0)
        self.new_c2.setValue(0.47)
        self.new_c3.setValue(1.0)
        self.recalculate()

    def recalculate(self) -> None:
        try:
            f = self._analysis_frequency_hz()
            measured = None
            measured_f = None
            if self.measurement is not None:
                measured_f = np.asarray(self.measurement.frequency_hz, dtype=float)
                measured = self.measurement.complex_response(self.phase_offset.value())
            old_controller = None
            self.current_deembed = None

            if self._identified_plant_active():
                # The identified rational model replaces the measured plant.
                plant = np.asarray(self.identified_plant.frequency_response(f), dtype=complex)
                plant_valid = np.ones_like(f, dtype=bool)
            elif self._is_complete_loop():
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

            self.current_frequency_hz = f
            self.current_old_controller = old_controller
            self.current_new_controller = new_controller
            self.current_metrics = metrics
            self.current_plant = plant
            self.current_loop = loop
            self.current_usable_mask = usable
            self.current_plant_valid_mask = plant_valid
            self.export_button.setEnabled(new_controller.implementable)
            step_lines = self._render_step(f, plant, usable)
            self._render(measured_f, measured, plant, cnew, loop, plant_valid, usable, valid_limit, step_lines)
        except Exception as exc:
            self.current_new_controller = None
            self.summary.setPlainText("ERROR: " + str(exc))
            self.details.setPlainText("ERROR: " + str(exc))
            self.export_button.setEnabled(False)

    def _render(self, measured_f, measured, plant, cnew, loop, plant_valid, usable, valid_limit: float, step_lines: list[str] | None = None) -> None:
        assert self.current_new_controller is not None
        assert self.current_metrics is not None
        f = np.asarray(self.current_frequency_hz, dtype=float)
        fu = f[usable]
        fp = f[plant_valid]
        metrics = self.current_metrics
        controller = self.current_new_controller.normalized()

        plant_mag, plant_phase = magnitude_phase(plant[plant_valid])
        ctrl_mag, ctrl_phase = magnitude_phase(cnew[usable])
        loop_mag, loop_phase = magnitude_phase(loop[usable])
        if measured is not None:
            measured_mag, measured_phase = magnitude_phase(measured)

        # Reset log scales before clear(): clearing an existing log-scaled axis
        # makes matplotlib try to restore a non-positive default xlim.
        for stale in list(self.bode_fig.axes):
            stale.set_xscale("linear")
        self.bode_fig.clear()
        ax = self.bode_fig.add_subplot(211)
        axp = self.bode_fig.add_subplot(212, sharex=ax)
        if measured is not None:
            ax.semilogx(measured_f, measured_mag, label="Imported TS")
            axp.semilogx(measured_f, measured_phase, label="Imported TS")
        plant_label = "Identified plant model" if self._identified_plant_active() else (
            "Equivalent Plant = TS / C_old" if self._is_complete_loop() else "Plant"
        )
        ax.semilogx(fp, plant_mag, label=plant_label)
        axp.semilogx(fp, plant_phase, label=plant_label)
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
        if step_lines:
            summary_lines += ["", *step_lines]
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
            f"File: {self.measurement.source_path if self.measurement is not None else '(no imported FRA)'}",
            f"Format: {self.measurement.source_format.value if self.measurement is not None else 'identified model'}",
            # Qt returns the enum's str value, not the enum member, for
            # str-derived Enums stored as item data.
            f"TS type: {FRAMeasurementKind(self.measurement_kind.currentData()).value}",
            f"Plant source: {self.plant_source.currentData()}",
            f"Tuning mode: {mode_text}",
            f"Points: {len(f)}",
            f"Frequency: {f[0]:.8g} Hz -> {f[-1]:.8g} Hz",
            f"Applied phase offset: {self.phase_offset.value():.6g} deg",
            f"Loop-analysis upper limit: {valid_limit:.8g} Hz (0.49 x relevant controller Fs)",
            "",
        ]
        if self.identified_plant is not None and self.identified_plant_band is not None:
            details.append(
                f"Identified plant model: order={getattr(self.identified_plant, 'order', '?')}, "
                f"band={self.identified_plant_band[0]:.8g}..{self.identified_plant_band[1]:.8g} Hz, "
                f"confidence={self.identified_plant_confidence}"
            )
            details.append("")
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

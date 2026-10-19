"""PySide6 main window for LLC engineering design, waveform and control work."""

from __future__ import annotations

import json
import traceback
from copy import deepcopy
from dataclasses import asdict, replace
from pathlib import Path

import numpy as np
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure
from PySide6.QtCore import Qt, QThreadPool, QTimer, Signal
from PySide6.QtGui import QAction, QImage, QPainter
from PySide6.QtWidgets import (
    QComboBox,
    QDockWidget,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QStatusBar,
    QTabWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)
from shiboken6 import isValid

from llc_design.i18n import t
from llc_design.user_messages import design_reason, design_status, show_operation_issue

from ..analysis import (
    FidelityLevel,
    GoldenSolverConfig,
    HarmonicBalanceConfig,
    LLCAnalysisRequest,
    LLCGoldenSolver,
    LLCModelResult,
    MultiFidelityAnalysis,
    TimeDomainConfig,
    solve_harmonic_balance,
)
from ..control.analysis import SmallSignalAnalysis, build_small_signal_analysis
from ..control.digital_loop import DigitalLoopAnalysis, build_digital_loop_analysis
from ..control.linearize import ControlInputKind
from ..core.config import (
    create_waveform_session,
    load_project,
    load_spec,
    save_project,
    waveform_request_identity,
)
from ..core.q_zvs import LLCQZVSAnalysis, build_q_zvs_analysis
from ..core.spec import (
    IntegratedInductanceSource,
    LLCDesignSpec,
    MagneticsMode,
    PrimaryTopology,
    TankParameterMode,
)
from ..core.tank import design_tank, equivalent_ac_load_ohm, gain, target_gain
from ..dynamics.plant import DynamicPhasorModel
from ..dynamics.switched import SwitchedSimulationConfig, simulate_switched_steady_state
from ..dynamics.waveforms import WaveformBundle, reconstruct_dynamic_phasor_waveforms
from ..magnetics.transformer_designer import (
    FerriteCoreInput,
    TransformerSynthesisResult,
    TransformerSynthesisSettings,
    export_transformer_synthesis,
    synthesize_transformer,
)
from ..models.devices import DeviceDatabase
from ..models.system import LLCSystemAnalyzer, SystemAnalysis
from ..multiphase import solve_interleaved_llc
from ..report.formula_pdf import build_formula_pdf
from ..switching.sr import analyze_sr
from . import theme
from .help import install_help
from .i18n_ui import about_text, install_language_selector
from .updater import add_toolbar_right_side, check_for_updates
from .widgets.digital_loop_view import DigitalLoopView
from .widgets.interleaved_view import InterleavedLLCView
from .widgets.model_comparison_view import ModelComparisonView
from .widgets.q_zvs_view import LLCQZVSView
from .widgets.small_signal_view import SmallSignalView
from .widgets.sr_design_view import SRDesignView
from .widgets.topology import TopologyView
from .widgets.transformer_design_view import TransformerDesignView
from .widgets.waveform_view import WaveformView
from .workers import FunctionWorker


class LLCMainWindow(QMainWindow):
    workspace_switch_requested = Signal(str)

    def __init__(self, initial_spec: LLCDesignSpec | None = None):
        super().__init__()
        self.setWindowTitle("电源设计工具箱 — LLC Design / Waveform / Control")
        available = self.screen().availableGeometry()
        self.resize(min(1920, available.width() - 32),
                    min(1080, available.height() - 64))
        self.spec = initial_spec or LLCDesignSpec()
        self.system_analysis: SystemAnalysis | None = None
        self.small_signal_analysis: SmallSignalAnalysis | None = None
        self.digital_loop_analysis: DigitalLoopAnalysis | None = None
        self.external_control_design = None
        self.external_control_label = ""
        self.q_zvs_analysis: LLCQZVSAnalysis | None = None
        self.transformer_synthesis: TransformerSynthesisResult | None = None
        self.multi_fidelity_analysis: MultiFidelityAnalysis | None = None
        self.thread_pool = QThreadPool.globalInstance()
        self._active_workers: list[FunctionWorker] = []
        self._power_stage_revision = 0
        self._waveform_generation = 0
        self._closing = False
        self._comparison_control = None
        self._accepted_waveform_identity = None
        self._waveform_small_signal = None
        self._waveform_plant_inputs = None
        self._results_current = False
        self.output_directory = Path("output/gui_session")
        self._build_actions()
        self._build_ui()
        self._load_spec_to_widgets(self.spec)
        from .optimization_install import install_two_stage_optimization
        install_two_stage_optimization(self)
        for key, widget in self.fields.items():
            widget.valueChanged.connect(lambda _value, key=key: self._power_stage_field_edited(key))
            widget.valueChanged.connect(self._power_stage_inputs_changed)
            if key.startswith("integrated_"):
                widget.valueChanged.connect(self._integrated_mode_changed)
        for widget in (self.topology, self.parameter_mode_combo, self.primary_device_combo, self.magnetics_mode_combo, self.integrated_source_combo, self.integrated_core_combo):
            widget.currentIndexChanged.connect(self._power_stage_inputs_changed)
        self.primary_turns.valueChanged.connect(self._integrated_mode_changed)
        self.secondary_turns.valueChanged.connect(self._integrated_mode_changed)
        self.integrated_core_combo.currentIndexChanged.connect(self._integrated_mode_changed)
        self.integrated_note.textChanged.connect(self._integrated_mode_changed)
        self.topology_view.set_primary_topology(self.topology.currentData())
        self.topology_view.set_integrated_magnetics(self.magnetics_mode_combo.currentData() == MagneticsMode.INTEGRATED)
        comparison = self.model_comparison_view
        for widget in (comparison.vbus, comparison.load_percent,
                       comparison.max_harmonic, comparison.hb_samples,
                       comparison.execution_budget_s):
            widget.valueChanged.connect(self._invalidate_model_comparison)
        for widget in (comparison.include_td, comparison.apply_td_losses):
            widget.toggled.connect(self._invalidate_model_comparison)

    def _cancel_model_comparison(self, reason: str = "用户取消") -> None:
        control = getattr(self, "_comparison_control", None)
        if control is not None:
            control.cancel(reason)
            self.model_comparison_view.cancel_button.setEnabled(False)
            self.statusBar().showMessage("已请求取消，等待数值线程退出…")

    def _invalidate_model_comparison(self, *_args) -> None:
        self._model_comparison_request_id = getattr(self, "_model_comparison_request_id", 0) + 1
        self._accepted_model_options = None
        self._power_stage_inputs_changed()
        self.multi_fidelity_analysis = None
        self.model_comparison_view.analysis = None
        self.model_comparison_view.table.setRowCount(0)
        self.model_comparison_view.details.clear()
        self.model_comparison_view.set_loss_report_available(False)

    def _power_stage_field_edited(self, key: str) -> None:
        # An explicit edit back to the displayed rounded value is still an edit.
        # It must not resurrect a previous hidden full-precision saved value.
        getattr(self, "_loaded_widget_values", {}).pop(key, None)

    def _power_stage_inputs_changed(self, *_args) -> None:
        """Invalidate visible results and late jobs when the physical plant changes."""
        self._cancel_model_comparison("输入已改变")
        if hasattr(self, "invalidate_two_stage_optimization"):
            self.invalidate_two_stage_optimization()
        self._power_stage_revision += 1
        self._waveform_inputs_changed()
        self.digital_loop_view.invalidate_analysis()
        self.small_signal_analysis = None
        self.topology_view.set_primary_topology(self.topology.currentData())
        self.topology_view.set_integrated_magnetics(self.magnetics_mode_combo.currentData() == MagneticsMode.INTEGRATED)
        self.waveform_view.set_primary_topology(self.topology.currentData())
        if (not self._results_current and all(getattr(self, name, None) is None for name in (
                "system_analysis", "small_signal_analysis", "digital_loop_analysis",
                "q_zvs_analysis", "transformer_synthesis", "multi_fidelity_analysis",
                "external_control_design")) and self.transformer_design_view.result is None
                and self.waveform_view.bundle is None):
            return
        self._results_current = False
        self.system_analysis = self.small_signal_analysis = self.digital_loop_analysis = None
        self.q_zvs_analysis = self.transformer_synthesis = self.multi_fidelity_analysis = None
        self.external_control_design = None
        self.summary_text.clear()
        self.loss_summary_view._analysis = None
        self.loss_summary_view.point.clear()
        self.model_comparison_view.analysis = None
        self.model_comparison_view.table.setRowCount(0)
        self.model_comparison_view.details.clear()
        self.model_comparison_view.set_loss_report_available(False)
        self.waveform_view.bundle = None
        self.waveform_view.refresh()
        self.q_zvs_view.result = None; self.q_zvs_view.table.clear()
        self.small_signal_view.result = None; self.small_signal_view.text.clear()
        self.transformer_design_view.invalidate_result()
        self.sr_design_view.text.clear(); self.interleaved_view.text.clear()
        for canvas in self.findChildren(FigureCanvasQTAgg):
            canvas.figure.clear(); canvas.draw_idle()
        self.statusBar().showMessage("功率级参数已改变，旧结果已失效；请重新计算。")

    def _build_actions(self) -> None:
        """Build a deliberately small toolbar.

        V7.1 keeps the global toolbar for project/navigation commands only.
        Analysis commands live in the page that owns them, while a single
        ``运行当前`` action remains available for keyboard-driven work.
        """
        toolbar = self.addToolBar("工程")
        toolbar.setObjectName("llc_main_toolbar")
        toolbar.setMovable(False)

        for label, callback in (
            ("加载 JSON", self.load_json),
            ("保存工程 JSON", self.save_json),
            ("导出公式 PDF", self.export_formula_pdf),
            ("输出目录", self.choose_output_directory),
        ):
            action = QAction(label, self)
            action.triggered.connect(callback)
            toolbar.addAction(action)

        toolbar.addSeparator()
        switch_action = QAction("切换到 PFC", self)
        switch_action.triggered.connect(
            lambda: self.workspace_switch_requested.emit("pfc"))
        toolbar.addAction(switch_action)
        dab_action = QAction("DAB TPS", self)
        dab_action.triggered.connect(lambda: self.workspace_switch_requested.emit("dab"))
        toolbar.addAction(dab_action)
        control_action = QAction("Control Tools", self)
        control_action.triggered.connect(
            lambda: self.workspace_switch_requested.emit("control"))
        toolbar.addAction(control_action)
        home_action = QAction("功能选择", self)
        home_action.triggered.connect(
            lambda: self.workspace_switch_requested.emit("home"))
        toolbar.addAction(home_action)

        toolbar.addSeparator()
        # Global LLC design parameters use the toolbar action itself as a true
        # show/hide toggle.  The dock deliberately has no close (X) button: the
        # same toolbar control (or F4) is the single, predictable way to hide it.
        self.toggle_params_action = QAction("隐藏设计参数", self)
        self.toggle_params_action.setCheckable(True)
        self.toggle_params_action.setChecked(True)
        self.toggle_params_action.setShortcut("F4")
        self.toggle_params_action.setToolTip("显示/隐藏 LLC 全局设计参数（F4）")
        toolbar.addAction(self.toggle_params_action)

        self.toggle_log_action = QAction("运行日志", self)
        self.toggle_log_action.setCheckable(True)
        self.toggle_log_action.setChecked(False)
        self.toggle_log_action.setShortcut("F8")
        toolbar.addAction(self.toggle_log_action)

        self.focus_action = QAction("专注模式", self)
        self.focus_action.setCheckable(True)
        self.focus_action.setShortcut("F9")
        self.focus_action.toggled.connect(self._toggle_focus_mode)
        toolbar.addAction(self.focus_action)

        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding,
                             QSizePolicy.Policy.Preferred)
        toolbar.addWidget(spacer)

        run_button = QToolButton()
        run_button.setText("运行当前")
        run_button.setToolTip("运行当前页面对应的分析（Ctrl+R）")
        run_button.clicked.connect(self._run_current_page)
        toolbar.addWidget(run_button)
        run_action = QAction(self)
        run_action.setShortcut("Ctrl+R")
        run_action.triggered.connect(self._run_current_page)
        self.addAction(run_action)

        about_action = QAction("关于", self)
        about_action.triggered.connect(self.show_about)
        toolbar.addAction(about_action)

        install_help(self, "llc")
        add_toolbar_right_side(toolbar, self)
        install_language_selector(self)
        QTimer.singleShot(2500, self._auto_check_update)

    def _auto_check_update(self) -> None:
        """启动后静默检查一次更新,仅在新版本时提示。"""
        check_for_updates(self, notify_up_to_date=False)

    def _build_ui(self) -> None:
        """Use dockable global inputs instead of a permanent nested sidebar."""
        self.setDockNestingEnabled(True)
        self.setCentralWidget(self._build_workspace())

        self.parameter_dock = QDockWidget("LLC 设计参数", self)
        self.parameter_dock.setObjectName("llc_parameter_dock")
        self.parameter_dock.setAllowedAreas(
            Qt.DockWidgetArea.LeftDockWidgetArea |
            Qt.DockWidgetArea.RightDockWidgetArea
        )
        self.parameter_dock.setFeatures(
            # No DockWidgetClosable: users hide/show this panel with the same
            # “设计参数” toolbar action instead of hunting for a tiny X button.
            QDockWidget.DockWidgetFeature.DockWidgetMovable |
            QDockWidget.DockWidgetFeature.DockWidgetFloatable
        )
        self.parameter_dock.setWidget(self._build_parameter_panel())
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self.parameter_dock)
        self.resizeDocks([self.parameter_dock], [300], Qt.Orientation.Horizontal)
        self.toggle_params_action.toggled.connect(self.parameter_dock.setVisible)
        self.parameter_dock.visibilityChanged.connect(self._sync_parameter_toggle)
        self._sync_parameter_toggle(self.parameter_dock.isVisible())

        self.log_text = QPlainTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setMaximumBlockCount(3000)
        self.log_dock = QDockWidget("运行日志", self)
        self.log_dock.setObjectName("llc_log_dock")
        self.log_dock.setAllowedAreas(
            Qt.DockWidgetArea.BottomDockWidgetArea |
            Qt.DockWidgetArea.TopDockWidgetArea
        )
        self.log_dock.setWidget(self.log_text)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self.log_dock)
        self.resizeDocks([self.log_dock], [180], Qt.Orientation.Vertical)
        self.log_dock.hide()
        self.toggle_log_action.toggled.connect(self.log_dock.setVisible)
        self.log_dock.visibilityChanged.connect(self.toggle_log_action.setChecked)

        status = QStatusBar()
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setVisible(False)
        status.addPermanentWidget(self.progress)
        self.setStatusBar(status)
        self.statusBar().showMessage(t("就绪"))
        self._apply_llc_style()

    def _sync_parameter_toggle(self, visible: bool) -> None:
        """Keep the design-parameter toolbar toggle and dock visibility in sync."""
        self.toggle_params_action.blockSignals(True)
        self.toggle_params_action.setChecked(bool(visible))
        self.toggle_params_action.setText(
            "隐藏设计参数" if visible else "显示设计参数"
        )
        self.toggle_params_action.blockSignals(False)

    def _toggle_focus_mode(self, enabled: bool) -> None:
        if enabled:
            self._params_was_visible = self.parameter_dock.isVisible() if hasattr(self, "parameter_dock") else True
            self._log_was_visible = self.log_dock.isVisible() if hasattr(self, "log_dock") else False
            if hasattr(self, "parameter_dock"):
                self.parameter_dock.hide()
            if hasattr(self, "log_dock"):
                self.log_dock.hide()
            self.statusBar().showMessage(t("专注模式：已隐藏全局参数与运行日志"))
        else:
            if hasattr(self, "parameter_dock") and getattr(self, "_params_was_visible", True):
                self.parameter_dock.show()
            if hasattr(self, "log_dock") and getattr(self, "_log_was_visible", False):
                self.log_dock.show()
            self.statusBar().showMessage(t("就绪"))

    def _run_current_page(self) -> None:
        if not hasattr(self, "tabs") or self._active_workers:
            return
        current = self.tabs.currentWidget()
        if current is getattr(self, "two_stage_optimization_view", None):
            self.run_two_stage_optimization()
        elif current is self.q_zvs_view:
            self.run_q_zvs()
        elif current is self.transformer_design_view:
            self.run_transformer_design(None, None)
        elif current is self.model_comparison_view:
            self.model_comparison_view.run_button.click()
        elif current is self.sr_design_view:
            self.sr_design_view.run.click()
        elif current is self.interleaved_view:
            self.interleaved_view.run.click()
        elif current is self.waveform_view:
            self.waveform_view.request_current()
        elif current is self.small_signal_view:
            self.small_signal_view.run_button.click()
        elif current is self.digital_loop_view:
            self.digital_loop_view.request_analysis()
        else:
            self.run_design()

    def _apply_llc_style(self) -> None:
        self.setStyleSheet(theme.workspace_stylesheet(theme.active_theme()))

    def _spin(self, minimum, maximum, decimals, suffix="") -> QDoubleSpinBox:
        widget = QDoubleSpinBox()
        widget.setRange(minimum, maximum)
        widget.setDecimals(decimals)
        widget.setSuffix(suffix)
        widget.setKeyboardTracking(False)
        widget.setMinimumWidth(112)
        return widget

    def _build_parameter_panel(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(10, 8, 10, 12)
        layout.setSpacing(8)
        hint = QLabel("全局 LLC 设计输入。需要最大化图形区域时，可按 F4 隐藏此面板，F9 进入专注模式。")
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color:{theme.active_theme().text_muted};padding:2px 2px 6px 2px;")
        layout.addWidget(hint)
        self.fields: dict[str, object] = {}

        electrical = QGroupBox("输入与输出")
        form = QFormLayout(electrical)
        field_specs = [
            ("vbus_nom_v", "母线额定", self._spin(20, 2000, 2, " V")),
            ("vbus_min_normal_v", "母线最低正常", self._spin(20, 2000, 2, " V")),
            ("vbus_max_v", "母线最高", self._spin(20, 2000, 2, " V")),
            ("vbus_hold_end_v", "Hold-up 末端", self._spin(20, 2000, 2, " V")),
            ("vout_v", "输出电压", self._spin(0.1, 1000, 3, " V")),
            ("pout_w", "输出功率", self._spin(1, 200000, 1, " W")),
        ]
        for key, label, widget in field_specs:
            self.fields[key] = widget
            form.addRow(label, widget)
        layout.addWidget(electrical)

        tank = QGroupBox("谐振腔与变压器")
        form = QFormLayout(tank)
        self.magnetics_mode_combo = QComboBox()
        self.magnetics_mode_combo.addItem("分立 Lr + 变压器", MagneticsMode.SEPARATE)
        self.magnetics_mode_combo.addItem("集成漏感 Lr", MagneticsMode.INTEGRATED)
        self.magnetics_mode_combo.setToolTip("默认分立磁件；集成模式使用同一变压器漏场作为 Lr，支持两绕组全桥 SR。表征输入在下方磁件结构组。")
        form.addRow("磁件方案", self.magnetics_mode_combo)
        self.parameter_mode_combo = QComboBox()
        self.parameter_mode_combo.addItem("Auto：fr / Ln / Q", TankParameterMode.AUTO_DESIGN)
        self.parameter_mode_combo.addItem("User：Lr / Cr / Lm", TankParameterMode.USER_DEFINED)
        self.parameter_mode_combo.currentIndexChanged.connect(self._parameter_mode_changed)
        form.addRow("参数来源", self.parameter_mode_combo)
        tank_specs = [
            ("resonant_frequency_hz", "谐振频率", self._spin(1, 2000, 3, " kHz")),
            ("minimum_frequency_hz", "最低频率", self._spin(1, 2000, 3, " kHz")),
            ("maximum_frequency_hz", "最高频率", self._spin(1, 3000, 3, " kHz")),
            ("ln_ratio", "Ln=Lm/Lr", self._spin(1.01, 30, 4)),
            ("q_full_load", "满载 Qe", self._spin(0.01, 5, 4)),
        ]
        self.auto_tank_widgets = []
        for key, label, widget in tank_specs:
            self.fields[key] = widget
            self.auto_tank_widgets.append(widget)
            form.addRow(label, widget)
        self.user_lr = self._spin(0.001, 100000, 4, " µH")
        self.user_cr = self._spin(0.001, 1000000, 4, " nF")
        self.user_lm = self._spin(0.001, 1000000, 4, " µH")
        self.fields["user_lr_h"] = self.user_lr
        self.fields["user_cr_f"] = self.user_cr
        self.fields["user_lm_h"] = self.user_lm
        self.manual_tank_widgets = [self.user_lr, self.user_cr, self.user_lm]
        form.addRow("用户 Lr", self.user_lr)
        form.addRow("用户 Cr", self.user_cr)
        form.addRow("用户 Lm", self.user_lm)
        self.manual_derived_label = QLabel("User Defined: fr / Ln / Q 将由输入值实时派生")
        self.manual_derived_label.setWordWrap(True)
        form.addRow("派生参数", self.manual_derived_label)
        self.copy_auto_button = QPushButton("复制 Auto → User")
        self.copy_auto_button.clicked.connect(self._copy_auto_to_manual)
        form.addRow(self.copy_auto_button)
        for w in self.manual_tank_widgets:
            w.valueChanged.connect(self._update_manual_derived_label)
        self.primary_turns = QSpinBox(); self.primary_turns.setRange(1, 500)
        self.secondary_turns = QSpinBox(); self.secondary_turns.setRange(1, 100)
        self.fields["primary_turns"] = self.primary_turns
        self.fields["secondary_turns"] = self.secondary_turns
        form.addRow("原边匝数", self.primary_turns)
        form.addRow("副边匝数", self.secondary_turns)
        self.primary_turns.valueChanged.connect(self._update_manual_derived_label)
        self.secondary_turns.valueChanged.connect(self._update_manual_derived_label)
        self.topology = QComboBox()
        self.topology.addItem("全桥 LLC", PrimaryTopology.FULL_BRIDGE)
        self.topology.addItem("半桥 LLC", PrimaryTopology.HALF_BRIDGE)
        form.addRow("一次侧拓扑", self.topology)
        layout.addWidget(tank)

        integrated = QGroupBox("磁件结构 / 集成谐振漏感")
        form = QFormLayout(integrated)
        self.integrated_source_combo = QComboBox()
        self.integrated_source_combo.addItem("电感矩阵 Lp / Ls / M", IntegratedInductanceSource.INDUCTANCE_MATRIX)
        self.integrated_source_combo.addItem("开短路 Lp / Ls / Lshort", IntegratedInductanceSource.OPEN_SHORT)
        form.addRow("实体表征输入", self.integrated_source_combo)
        self.integrated_core_combo = QComboBox()
        self.integrated_core_combo.addItem("请选择表征磁芯", "")
        from ..magnetics.core import CoreDatabase
        for core in CoreDatabase().for_purpose("transformer"):
            self.integrated_core_combo.addItem(core.part_number, core.part_number)
        form.addRow("表征磁芯（锁定）", self.integrated_core_combo)
        self.integrated_note = QLineEdit()
        self.integrated_note.setPlaceholderText("样件/来源、偏置、温度、测试条件；参考模型请注明")
        form.addRow("表征来源/条件", self.integrated_note)
        self.integrated_note.textChanged.connect(self._power_stage_inputs_changed)
        for key, label, widget in (
            ("integrated_characterized_primary_turns", "表征时 Np", QSpinBox()),
            ("integrated_characterized_secondary_turns", "表征时 Ns", QSpinBox()),
            ("integrated_lp_h", "Lp（次级开路）", self._spin(0, 1000000, 6, " µH")),
            ("integrated_ls_h", "Ls（原边开路）", self._spin(0, 1000000, 6, " µH")),
            ("integrated_mutual_h", "M（同名端为正）", self._spin(0, 1000000, 6, " µH")),
            ("integrated_lshort_h", "Lshort（次级短路）", self._spin(0, 1000000, 6, " µH")),
            ("integrated_measurement_frequency_hz", "表征频率", self._spin(0.001, 10000, 3, " kHz")),
            ("integrated_target_lr_h", "Lr 目标（0=未指定）", self._spin(0, 1000000, 6, " µH")),
            ("integrated_interwinding_gap_m", "P-S 每界面绝缘间距", self._spin(0, 50, 4, " mm")),
            ("integrated_interlayer_gap_m", "同绕组层间绝缘", self._spin(0, 10, 4, " mm")),
            ("integrated_minimum_insulation_m", "声明最小绝缘", self._spin(0, 50, 4, " mm")),
            ("integrated_geometry_tolerance", "几何匹配容差", self._spin(0.001, 99.999, 3, " %")),
            ("integrated_target_tolerance", "Lr 目标容差", self._spin(0.001, 99.999, 3, " %")),
        ):
            if isinstance(widget, QSpinBox):
                widget.setRange(1, 500)
            self.fields[key] = widget
            form.addRow(label, widget)
        self.integrated_summary = QLabel("表征实体后推导 APR Lr / Lm / n_eff；目标值不代表已实现。")
        self.fields["integrated_minimum_insulation_m"].setToolTip("用户声明的一维几何筛查下限；不是安规电气间隙、爬电距离或绝缘认证。")
        self.integrated_summary.setWordWrap(True)
        form.addRow(self.integrated_summary)
        example = QPushButton("双漏感参考例（非实测）")
        example.clicked.connect(self._load_integrated_example)
        form.addRow(example)
        reachable_example = QPushButton("可达参考例（非实测）")
        reachable_example.setToolTip("标称工作点的一维几何可达例；不是硬件实测，也未通过全工作范围认证。")
        reachable_example.clicked.connect(self._load_reachable_integrated_example)
        form.addRow(reachable_example)
        self.magnetics_mode_combo.currentIndexChanged.connect(self._integrated_mode_changed)
        self.integrated_source_combo.currentIndexChanged.connect(self._integrated_mode_changed)
        layout.addWidget(integrated)

        capacitors = QGroupBox("电容与控制建模")
        form = QFormLayout(capacitors)
        cap_specs = [
            ("bus_capacitance_f", "母线电容", self._spin(1, 100000, 1, " µF")),
            ("requested_hold_time_s", "Hold-up 时间", self._spin(0.1, 1000, 2, " ms")),
            ("output_capacitance_f", "输出电容", self._spin(1, 100000, 1, " µF")),
            ("output_cap_esr_ohm", "输出电容 ESR", self._spin(0, 1000, 4, " mΩ")),
            ("primary_deadtime_s", "一次死区", self._spin(0, 5000, 2, " ns")),
            ("primary_zvs_margin_required", "ZVS 首选裕量", self._spin(0.1, 20, 3)),
        ]
        for key, label, widget in cap_specs:
            self.fields[key] = widget
            form.addRow(label, widget)
        self.primary_device_combo = QComboBox()
        for device in DeviceDatabase().primary:
            self.primary_device_combo.addItem(
                f"{device.part_number} | Coss={device.coss_er_f*1e12:.0f} pF | Qoss={device.qoss_c*1e9:.1f} nC",
                device.part_number,
            )
        form.addRow("ZVS 主功率器件", self.primary_device_combo)
        layout.addWidget(capacitors)

        actions = QGroupBox("快速运行")
        action_layout = QVBoxLayout(actions)
        action_layout.setSpacing(6)
        self.run_button = QPushButton("运行 LLC 完整计算")
        self.fast_wave_button = QPushButton("FHA 波形")
        self.hb_wave_button = QPushButton("HB 波形")
        self.detail_wave_button = QPushButton("TD 波形")
        self.small_signal_button = QPushButton("小信号 G(s) / G(z)")
        self.run_button.clicked.connect(self.run_design)
        self.fast_wave_button.clicked.connect(lambda: self.run_waveforms(False))
        self.hb_wave_button.clicked.connect(lambda: self.run_harmonic_waveforms())
        self.detail_wave_button.clicked.connect(lambda: self.run_waveforms(True))
        self.small_signal_button.clicked.connect(lambda: self.run_small_signal({}))
        action_layout.addWidget(self.run_button)
        # Keep each solver action reachable at the dock's minimum width,
        # including native fonts with wider glyphs and scroll-bar metrics.
        action_layout.addWidget(self.fast_wave_button)
        action_layout.addWidget(self.hb_wave_button)
        action_layout.addWidget(self.detail_wave_button)
        action_layout.addWidget(self.small_signal_button)
        layout.addWidget(actions)
        layout.addStretch(1)
        # The default 300 px dock must not require horizontal scrolling to
        # reveal selectors or engineering boundaries, including long device IDs.
        for group in content.findChildren(QGroupBox):
            group_form = group.layout()
            if isinstance(group_form, QFormLayout):
                group_form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
                group_form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        for combo in content.findChildren(QComboBox):
            combo.setMinimumContentsLength(10)
            combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
            combo.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
            combo.setMinimumWidth(112)
        for label in content.findChildren(QLabel):
            if label.wordWrap():
                label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
                label.setMinimumWidth(0)
        scroll.setWidget(content)
        scroll.setMinimumWidth(265)
        scroll.setMaximumWidth(420)
        return scroll

    def _build_workspace(self) -> QWidget:
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.setUsesScrollButtons(True)

        summary = QWidget()
        summary_layout = QVBoxLayout(summary)
        summary_layout.setContentsMargins(10, 10, 10, 10)
        self.topology_view = TopologyView()
        self.topology_view.component_selected.connect(self._component_selected)
        summary_layout.addWidget(self.topology_view)
        self.summary_text = QPlainTextEdit()
        self.summary_text.setReadOnly(True)
        summary_layout.addWidget(self.summary_text, 1)
        self.tabs.addTab(summary, "设计总览")
        from llc_design.gui.widgets.loss_summary_view import LossSummaryView
        self.loss_summary_view = LossSummaryView()
        self.tabs.addTab(self.loss_summary_view, "损耗汇总")

        self.gain_figure = Figure(figsize=(9, 6))
        self.gain_canvas = FigureCanvasQTAgg(self.gain_figure)
        self.tabs.addTab(self.gain_canvas, "增益 / 工作区")

        self.q_zvs_view = LLCQZVSView()
        self.q_zvs_view.analysis_requested.connect(self.run_q_zvs)
        self.tabs.addTab(self.q_zvs_view, "Q / ZVS")

        self.model_comparison_view = ModelComparisonView()
        self.model_comparison_view.analysis_requested.connect(self.run_model_comparison)
        self.model_comparison_view.cancel_requested.connect(self._cancel_model_comparison)
        self.model_comparison_view.loss_report_requested.connect(self._export_td_loss_report)
        self.tabs.addTab(self.model_comparison_view, "FHA / HB / TD")

        self.transformer_design_view = TransformerDesignView()
        self.transformer_design_view.analysis_requested.connect(self.run_transformer_design)
        self.transformer_design_view.apply_turns_requested.connect(self._apply_transformer_turns)
        self.transformer_design_view.export_requested.connect(self._export_transformer_design)
        self.tabs.addTab(self.transformer_design_view, "变压器")

        self.sr_design_view = SRDesignView()
        self.sr_design_view.analysis_requested.connect(self.run_sr_design)
        self.tabs.addTab(self.sr_design_view, "SR Timing / Loss")

        self.interleaved_view = InterleavedLLCView()
        self.interleaved_view.analysis_requested.connect(self.run_interleaved)
        self.tabs.addTab(self.interleaved_view, "2P / 3P Interleaved")

        self.waveform_view = WaveformView()
        self.waveform_view.fast_requested.connect(lambda options: self.run_waveforms(False, options))
        self.waveform_view.harmonic_requested.connect(self.run_harmonic_waveforms)
        self.waveform_view.detailed_requested.connect(lambda options: self.run_waveforms(True, options))
        self.waveform_view.inputs_changed.connect(self._waveform_inputs_changed)
        self.waveform_view.export_requested.connect(self.export_current_waveforms)
        self.tabs.addTab(self.waveform_view, "波形")

        self.small_signal_view = SmallSignalView()
        self.small_signal_view.analysis_requested.connect(self.run_small_signal)
        self.tabs.addTab(self.small_signal_view, "小信号")

        self.digital_loop_view = DigitalLoopView()
        self.digital_loop_view.analysis_requested.connect(self.run_digital_loop)
        self.digital_loop_view.inputs_changed.connect(self._digital_loop_inputs_changed)
        self.tabs.addTab(self.digital_loop_view, "数字控制")
        return self.tabs

    def _component_selected(self, key: str) -> None:
        self.statusBar().showMessage(f"已选择功率级器件：{key}")
        if key in self.waveform_view.COMPONENT_SIGNALS:
            self.waveform_view.select_component(key)
            self.tabs.setCurrentWidget(self.waveform_view)

    def _waveform_inputs_changed(self, *_args) -> None:
        self._waveform_generation += 1
        self._accepted_waveform_identity = None
        if self.small_signal_analysis is self._waveform_small_signal:
            self.small_signal_analysis = None
        self._waveform_small_signal = None
        self._waveform_plant_inputs = None
        if hasattr(self, "waveform_view"):
            self.waveform_view.invalidate()

    def _waveform_session(self, spec=None) -> dict:
        view = self.waveform_view
        return create_waveform_session(spec or self._spec_from_widgets(), view.work_point(),
                                       view.solver, view.selected_component,
                                       view.group_combo.currentIndex(), self._waveform_plant_inputs)

    def _waveform_owner(self):
        return self._power_stage_revision, self._waveform_generation, self._closing

    def closeEvent(self, event):
        self._cancel_model_comparison("窗口已关闭")
        if hasattr(self, "cancel_two_stage_optimization"):
            self.cancel_two_stage_optimization()
        self._closing = True
        self._power_stage_revision += 1
        self._waveform_inputs_changed()
        super().closeEvent(event)

    def showEvent(self, event):
        if self._closing:
            self._closing = False
            self._waveform_inputs_changed()
            self._set_busy(bool(self._active_workers))
        super().showEvent(event)

    def _set_busy(self, busy: bool, message: str = "") -> None:
        self.progress.setVisible(busy)
        for button in (
            self.run_button, self.fast_wave_button, self.hb_wave_button,
            self.detail_wave_button, self.small_signal_button,
        ):
            button.setEnabled(not busy)
        self.waveform_view.set_busy(busy)
        self.model_comparison_view.set_busy(busy)
        control = getattr(self, "_comparison_control", None)
        self.model_comparison_view.cancel_button.setEnabled(
            bool(busy and control is not None and control.outcome is None))
        self.small_signal_view.set_busy(busy)
        self.digital_loop_view.set_busy(busy)
        self.transformer_design_view.set_busy(busy)
        self.sr_design_view.set_busy(busy)
        self.interleaved_view.set_busy(busy)
        self.statusBar().showMessage(message if busy else t("就绪"))

    def _append_log(self, message: str) -> None:
        self.log_text.appendPlainText(message.rstrip())

    def _load_reachable_integrated_example(self):
        base = LLCDesignSpec()
        mu, sigma, n = 300e-6, 3.5e-6, base.turns_ratio
        sample = base.clone(magnetics_mode=MagneticsMode.INTEGRATED,
            integrated_characterized_primary_turns=30, integrated_characterized_secondary_turns=4,
            integrated_characterized_core="PQ50_REF", integrated_lp_h=mu+sigma,
            integrated_ls_h=(mu+sigma)/n**2, integrated_mutual_h=mu/n,
            integrated_interwinding_gap_m=0.0010325249820337418,
            integrated_target_lr_h=mu+sigma-mu*mu/(mu+sigma),
            integrated_measurement_note="Synthetic nominal-point geometry-reachable reference; NOT a hardware measurement or full-envelope qualification")
        self.spec=sample
        self._load_spec_to_widgets(sample)
        self.transformer_design_view.workpoint_scope.setCurrentIndex(
            self.transformer_design_view.workpoint_scope.findData("nominal"))
        if hasattr(self, "two_stage_optimization_view"):
            self.two_stage_optimization_view.set_initial_spec(sample)

    def _load_integrated_example(self):
        # Deliberately labeled synthetic reference, never a measured achievement.
        base = self.spec.clone(magnetics_mode=MagneticsMode.SEPARATE,
            primary_turns=self.primary_turns.value(), secondary_turns=self.secondary_turns.value())
        tank = design_tank(base)
        r, n = .98, base.turns_ratio
        mu = tank.lm_h/r
        sample = base.clone(magnetics_mode=MagneticsMode.INTEGRATED,
            integrated_characterized_primary_turns=base.primary_turns,
            integrated_characterized_secondary_turns=base.secondary_turns,
            integrated_characterized_core="PQ50_REF",
            integrated_lp_h=mu+tank.lr_h-mu*(1-r),
            integrated_ls_h=mu/(r*n*n), integrated_mutual_h=mu/n,
            integrated_lshort_h=tank.lr_h,
            integrated_target_lr_h=tank.lr_h,
            integrated_measurement_note="Synthetic linear two-leakage reference model; NOT a hardware measurement")
        self.spec = sample
        self._load_spec_to_widgets(sample)
        if hasattr(self, "two_stage_optimization_view"):
            self.two_stage_optimization_view.set_initial_spec(sample)

    def _integrated_mode_changed(self):
        self._parameter_mode_changed()
        if not hasattr(self, "integrated_summary"):
            return
        integrated = self.magnetics_mode_combo.currentData() == MagneticsMode.INTEGRATED
        source = self.integrated_source_combo.currentData()
        if hasattr(self, "transformer_design_view"):
            self.transformer_design_view.set_magnetics_mode(integrated, self.integrated_core_combo.currentData())
        for key, widget in self.fields.items():
            if key.startswith("integrated_"):
                widget.setEnabled(integrated)
        self.fields["integrated_mutual_h"].setEnabled(integrated and source == IntegratedInductanceSource.INDUCTANCE_MATRIX)
        self.fields["integrated_lshort_h"].setEnabled(integrated and source == IntegratedInductanceSource.OPEN_SHORT)
        self.integrated_note.setEnabled(integrated)
        self.integrated_core_combo.setEnabled(integrated)
        self.integrated_source_combo.setEnabled(integrated)
        if integrated:
            try:
                spec = self._spec_from_widgets()
                from ..core.integrated_magnetics import from_spec
                m = from_spec(spec)
                self.integrated_summary.setText(f"APR 等效：\nLr={m.lr_h*1e6:.4f} µH\nLm={m.lm_h*1e6:.4f} µH\nn_eff={m.effective_turns_ratio:.5f}\n实体 n={spec.turns_ratio:.5f}\nLμ={m.physical_magnetizing_h*1e6:.4f} µH\n单个共用磁件，无独立 Lr。\n两绕组全桥 SR；几何估计仍需实测。")
            except (ValueError, TypeError) as exc:
                self.integrated_summary.setText("集成输入待完成：" + str(exc))
        else:
            self.integrated_summary.setText("选择上方集成漏感 Lr 后输入表征矩阵。目标值不代表已实现。声明绝缘与容差仅作工程筛查，不是安规认证。")
        self._parameter_mode_changed()

    def _parameter_mode_changed(self) -> None:
        if not hasattr(self, "parameter_mode_combo"):
            return
        manual = TankParameterMode(self.parameter_mode_combo.currentData()) == TankParameterMode.USER_DEFINED
        for w in getattr(self, "auto_tank_widgets", []):
            w.setEnabled(not manual)
        for w in getattr(self, "manual_tank_widgets", []):
            w.setEnabled(manual)
        if hasattr(self, "copy_auto_button"):
            self.copy_auto_button.setEnabled(not manual)
        if hasattr(self, "magnetics_mode_combo") and self.magnetics_mode_combo.currentData() == MagneticsMode.INTEGRATED:
            self.user_lr.setEnabled(False); self.user_lm.setEnabled(False)
            self.fields["ln_ratio"].setEnabled(False); self.fields["q_full_load"].setEnabled(False)
        self._update_manual_derived_label()

    def _copy_auto_to_manual(self) -> None:
        try:
            temp = self._spec_from_widgets().clone(parameter_mode=TankParameterMode.AUTO_DESIGN)
            tank = design_tank(temp)
            self.user_lr.setValue(tank.lr_h * 1e6)
            self.user_cr.setValue(tank.cr_f * 1e9)
            self.user_lm.setValue(tank.lm_h * 1e6)
            idx = self.parameter_mode_combo.findData(TankParameterMode.USER_DEFINED)
            if idx >= 0:
                self.parameter_mode_combo.setCurrentIndex(idx)
        except Exception as exc:
            show_operation_issue(self, t("无法复制参数"), str(exc), critical=False)

    def _update_manual_derived_label(self) -> None:
        if not hasattr(self, "manual_derived_label"):
            return
        if (hasattr(self, "integrated_summary") and self.magnetics_mode_combo.currentData() == MagneticsMode.INTEGRATED):
            try:
                spec = self._spec_from_widgets()
                tank = design_tank(spec)
                self.manual_derived_label.setText(f"APR: fr={tank.fr_hz/1e3:.3f} kHz\nLn={tank.ln_ratio:.4f}\nn_eff={spec.electrical_turns_ratio:.5f}\nLr/Lm 由表征矩阵导出")
            except (ValueError, TypeError):
                self.manual_derived_label.setText("APR 派生参数等待完整表征输入")
            return
        lr = self.user_lr.value() * 1e-6 if hasattr(self, "user_lr") else 0.0
        cr = self.user_cr.value() * 1e-9 if hasattr(self, "user_cr") else 0.0
        lm = self.user_lm.value() * 1e-6 if hasattr(self, "user_lm") else 0.0
        if lr > 0 and cr > 0 and lm > 0:
            fr = 1.0 / (2.0 * np.pi * np.sqrt(lr * cr))
            ln = lm / lr
            n=(self.primary_turns.value()/max(self.secondary_turns.value(),1)) if hasattr(self,"primary_turns") else 0.0
            self.manual_derived_label.setText(f"fr={fr/1e3:.3f} kHz   Ln={ln:.4f}   n=Np/Ns={n:.5f}   Q 按实际负载派生")
        else:
            self.manual_derived_label.setText("请输入正的 Lr / Cr / Lm")

    def _spec_from_widgets(self) -> LLCDesignSpec:
        changes = {}
        frequency_keys = {"resonant_frequency_hz", "minimum_frequency_hz", "maximum_frequency_hz", "integrated_measurement_frequency_hz"}
        microfarad_keys = {"bus_capacitance_f", "output_capacitance_f"}
        microhenry_keys = {"user_lr_h", "user_lm_h", "integrated_lp_h", "integrated_ls_h", "integrated_mutual_h", "integrated_lshort_h", "integrated_target_lr_h"}
        nanofarad_keys = {"user_cr_f"}
        for key, widget in self.fields.items():
            value = widget.value()
            if key in frequency_keys:
                value *= 1e3
            elif key in microfarad_keys:
                value *= 1e-6
            elif key in microhenry_keys:
                value *= 1e-6
            elif key in nanofarad_keys:
                value *= 1e-9
            elif key in {"integrated_interwinding_gap_m", "integrated_interlayer_gap_m", "integrated_minimum_insulation_m"}:
                value *= 1e-3
            elif key in {"integrated_geometry_tolerance", "integrated_target_tolerance"}:
                value /= 100.0
            elif key == "requested_hold_time_s":
                value *= 1e-3
            elif key == "output_cap_esr_ohm":
                value *= 1e-3
            elif key == "primary_deadtime_s":
                value *= 1e-9
            elif key in {"primary_turns", "secondary_turns"}:
                value = int(value)
            # Retain full saved input precision until this specific control is edited.
            loaded = getattr(self, "_loaded_widget_values", {})
            original = getattr(self, "_loaded_spec", None)
            entering_manual = (key in {"user_lr_h", "user_cr_f", "user_lm_h"}
                and self.parameter_mode_combo.currentData() == TankParameterMode.USER_DEFINED
                and original is not None and original.parameter_mode != TankParameterMode.USER_DEFINED)
            if original is not None and widget.value() == loaded.get(key) and not entering_manual:
                value = getattr(original, key)
            if key in {"integrated_lp_h", "integrated_ls_h", "integrated_mutual_h", "integrated_lshort_h", "integrated_target_lr_h"} and not value:
                value = None
            if key.startswith("integrated_characterized_") and value is None and self.magnetics_mode_combo.currentData() == MagneticsMode.INTEGRATED:
                value = int(widget.value())
            changes[key] = value
        changes["primary_topology"] = PrimaryTopology(self.topology.currentData())
        changes["parameter_mode"] = TankParameterMode(self.parameter_mode_combo.currentData())
        changes["magnetics_mode"] = MagneticsMode(self.magnetics_mode_combo.currentData())
        changes["integrated_inductance_source"] = IntegratedInductanceSource(self.integrated_source_combo.currentData())
        changes["integrated_characterized_core"] = self.integrated_core_combo.currentData()
        changes["integrated_measurement_note"] = self.integrated_note.text()
        changes["primary_device"] = self.primary_device_combo.currentData()
        spec = self.spec.clone(**changes)
        spec.validate()
        return spec

    def _project_spec_widget_values(self, spec: LLCDesignSpec) -> dict:
        """Validate a characterized model's display before mutating live inputs."""
        if spec.integrated_magnetics and self.integrated_core_combo.findData(spec.integrated_characterized_core) < 0:
            raise ValueError("Unknown characterized transformer core: " + spec.integrated_characterized_core)
        projected = {}
        for key, widget in self.fields.items():
            value = getattr(spec, key)
            if key.startswith("integrated_") and value is None:
                value = (spec.primary_turns if key.endswith("primary_turns") else
                         spec.secondary_turns if key.endswith("secondary_turns") else 0.0)
            if key in {"integrated_lp_h", "integrated_ls_h", "integrated_mutual_h", "integrated_lshort_h", "integrated_target_lr_h"}:
                value *= 1e6
            elif key in {"integrated_interwinding_gap_m", "integrated_interlayer_gap_m", "integrated_minimum_insulation_m"}:
                value *= 1e3
            if key in {"resonant_frequency_hz", "minimum_frequency_hz", "maximum_frequency_hz", "integrated_measurement_frequency_hz"}:
                value /= 1e3
            elif key in {"bus_capacitance_f", "output_capacitance_f"}:
                value *= 1e6
            elif key in {"user_lr_h", "user_lm_h"}:
                if value is None:
                    auto_tank = design_tank(spec.clone(parameter_mode=TankParameterMode.AUTO_DESIGN))
                    value = auto_tank.lr_h if key == "user_lr_h" else auto_tank.lm_h
                value *= 1e6
            elif key == "user_cr_f":
                if value is None:
                    value = design_tank(spec.clone(parameter_mode=TankParameterMode.AUTO_DESIGN)).cr_f
                value *= 1e9
            elif key in {"integrated_geometry_tolerance", "integrated_target_tolerance"}:
                value *= 100.0
            elif key == "requested_hold_time_s":
                value *= 1e3
            elif key == "output_cap_esr_ohm":
                value *= 1e3
            elif key == "primary_deadtime_s":
                value *= 1e9
            if (key.startswith("integrated_") or spec.integrated_magnetics) and not widget.minimum() <= value <= widget.maximum():
                raise ValueError(f"{key}: value {value:g} outside supported GUI range [{widget.minimum():g}, {widget.maximum():g}]; input was not loaded")
            projected[key] = value
        return projected

    def _load_spec_to_widgets(self, spec: LLCDesignSpec) -> None:
        projected = self._project_spec_widget_values(spec)
        self._power_stage_inputs_changed()
        for key, value in projected.items():
            self.fields[key].setValue(value)
        self.magnetics_mode_combo.setCurrentIndex(self.magnetics_mode_combo.findData(MagneticsMode(spec.magnetics_mode)))
        self.integrated_source_combo.setCurrentIndex(self.integrated_source_combo.findData(IntegratedInductanceSource(spec.integrated_inductance_source)))
        self.integrated_core_combo.setCurrentIndex(max(0, self.integrated_core_combo.findData(spec.integrated_characterized_core)))
        self.integrated_note.setText(spec.integrated_measurement_note)
        self._integrated_mode_changed()
        self.topology.setCurrentIndex(0 if PrimaryTopology(spec.primary_topology) == PrimaryTopology.FULL_BRIDGE else 1)
        mode_idx = self.parameter_mode_combo.findData(TankParameterMode(spec.parameter_mode))
        if mode_idx >= 0:
            self.parameter_mode_combo.setCurrentIndex(mode_idx)
        self._parameter_mode_changed()
        idx = self.primary_device_combo.findData(spec.primary_device)
        if idx >= 0:
            self.primary_device_combo.setCurrentIndex(idx)
        if hasattr(self, "waveform_view"):
            self.waveform_view.set_nominal_work_point(spec.vbus_nom_v)
        if hasattr(self, "model_comparison_view"):
            self.model_comparison_view.set_nominal_work_point(spec.vbus_nom_v)
        if hasattr(self, "small_signal_view"):
            self.small_signal_view.set_nominal_work_point(spec.vbus_nom_v)
        if hasattr(self, "digital_loop_view"):
            self.digital_loop_view.set_nominal_work_point(spec.vbus_nom_v)
        if hasattr(self, "transformer_design_view"):
            self.transformer_design_view.set_nominal_spec(spec)
        if hasattr(self, "sr_design_view"):
            self.sr_design_view.set_nominal_spec(spec)
        if hasattr(self, "interleaved_view"):
            self.interleaved_view.set_nominal_spec(spec)
        self._loaded_spec = deepcopy(spec)
        self._loaded_widget_values = {key: widget.value() for key, widget in self.fields.items()}
        self._integrated_mode_changed()

    def _project_owner(self):
        optimization = getattr(self, "_two_stage_optimization_state", {})
        view = getattr(self, "two_stage_optimization_view", None)
        return (self._waveform_owner(), optimization.get("generation"),
                view.table.currentRow() if view is not None else None)

    def load_json(self) -> None:
        owner = self._project_owner()
        path, _ = QFileDialog.getOpenFileName(self, "加载 LLC JSON", "", "JSON (*.json)")
        if not path:
            return
        if self._closing or owner != self._project_owner():
            return
        try:
            spec, session, waveform_session, optimization_session = load_project(
                path, include_waveform=True, include_optimization=True)
            prepared = None
            if session is not None:
                import json

                from ..control.design_session import decode_model
                from .design_session_install import validate_map_state
                from .solution_map_install import install_llc_solution_map
                view = install_llc_solution_map(self)
                prepared = validate_map_state(view, session.get("solution_map"), "llc")
                exact_spec = decode_model(session["inputs"])["spec"]
                if json.dumps(asdict(spec), sort_keys=True) != json.dumps(asdict(exact_spec), sort_keys=True):
                    raise ValueError("Project spec and control-session spec do not match")
                spec = exact_spec
            self._project_spec_widget_values(spec)
            self.spec = spec
            self._load_spec_to_widgets(self.spec)
            if session is not None:
                self.digital_loop_view._session_restore(session, prepared)
            if waveform_session is not None:
                view = self.waveform_view
                view.solver = waveform_session["solver"]
                view.set_work_point(waveform_session["work_point"])
                view.group_combo.setCurrentIndex(waveform_session["group"])
                view.selected_component = waveform_session["component"]
                self._waveform_plant_inputs = deepcopy(waveform_session["plant_inputs"])
                view.request_current()
            from .optimization_install import restore_two_stage_session
            restore_two_stage_session(self, optimization_session)
            self._append_log(f"Loaded: {path}" + (
                " — exact control inputs restored; Run and regenerate candidate before Apply."
                if session is not None else ""))
        except Exception as exc:
            show_operation_issue(self, t("加载失败"), str(exc), critical=True)

    def save_json(self) -> None:
        owner = self._project_owner()
        path, _ = QFileDialog.getSaveFileName(self, "保存 LLC JSON", "llc_project.json", "JSON (*.json)")
        if not path or self._closing or owner != self._project_owner():
            return
        try:
            self.spec = self._spec_from_widgets()
            analysis = (
                self.system_analysis
                if self.system_analysis and self.system_analysis.spec == self.spec
                else None
            )
            from .solution_map_install import install_llc_solution_map
            install_llc_solution_map(self)
            session = self.digital_loop_view._session_capture()
            from .optimization_install import capture_two_stage_session
            save_project(self.spec, path, analysis, control_session=session,
                         waveform_session=self._waveform_session(self.spec),
                         optimization_session=capture_two_stage_session(self))
            self._append_log(f"Saved: {path}")
        except Exception as exc:
            show_operation_issue(self, t("保存失败"), str(exc), critical=True)

    def choose_output_directory(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "选择输出目录", str(self.output_directory))
        if path:
            self.output_directory = Path(path)
            self.statusBar().showMessage(f"输出目录：{path}")

    def export_formula_pdf(self) -> None:
        # Capture the clicked physical state before entering the nested dialog
        # event loop. Workers never read live host.spec or publish external files.
        import tempfile

        from .optimization_install import publish_export_files
        try:
            spec = deepcopy(self._spec_from_widgets())
        except Exception as exc:
            show_operation_issue(self, t("参数错误"), str(exc), critical=False)
            return
        owner = self._project_owner()
        analysis_owner = self.system_analysis
        analysis = analysis_owner if analysis_owner and analysis_owner.spec == spec else None

        def current():
            return (isValid(self) and not self._closing and owner == self._project_owner()
                    and self.system_analysis is analysis_owner and self._spec_from_widgets() == spec)

        path, _ = QFileDialog.getSaveFileName(
            self, "导出 LLC 公式计算书", str(self.output_directory / "LLC_formula_worksheet.pdf"), "PDF (*.pdf)",
        )
        if not path or not current():
            return

        def calculate():
            with tempfile.TemporaryDirectory(prefix="llc-formula-") as temporary:
                result = build_formula_pdf(analysis or LLCSystemAnalyzer().analyze(spec),
                                           Path(temporary) / "worksheet.pdf")
                return Path(result).read_bytes()

        calculate._llc_waveform_current = current

        def ready(content):
            if not current():
                return
            try:
                output = Path(path)
                publish_export_files(output.parent, {output.name: content})
            except (OSError, ValueError) as exc:
                self._worker_error(str(exc))
                return
            self._formula_pdf_ready(output)

        self._run_worker("正在生成公式计算书…", calculate, ready)

    def _formula_pdf_ready(self, path: Path) -> None:
        self._append_log(f"Formula worksheet exported: {path}")
        self.statusBar().showMessage(f"公式计算书已导出：{path}", 8000)

    def show_about(self) -> None:
        QMessageBox.about(self, t("关于"), about_text())

    def _run_worker(self, label: str, function, callback) -> None:
        control = getattr(function, "_llc_comparison_control", None)
        self._set_busy(True, label)
        worker = FunctionWorker(function)
        # Keep a strong reference until the worker finishes; the thread pool
        # would otherwise drop it and its signals QObject could be
        # garbage-collected before the result callback fires.
        self._active_workers.append(worker)
        revision = self._power_stage_revision
        digital_revision = self.digital_loop_view.input_revision
        # The solution-map adapter wraps completion callbacks. Preserve task
        # identity on its callable so stale exceptions are rejected as well.
        digital_request = callback == self._digital_loop_ready or getattr(function, "_llc_digital_request", False)
        waveform_current = getattr(function, "_llc_waveform_current", lambda: True)
        def inputs_current():
            return isValid(self) and worker in self._active_workers and not self._closing and revision == self._power_stage_revision and (
                not digital_request or digital_revision == self.digital_loop_view.input_revision) and waveform_current()
        def ready(result):
            if control is not None:
                from ..analysis.execution import SolveInterrupted
                try:
                    control.checkpoint()
                except SolveInterrupted:
                    return
            if not isValid(self) or worker not in self._active_workers or self._closing:
                return
            if not waveform_current():
                self._append_log("Discarded waveform: input/request identity changed.")
                return
            if revision != self._power_stage_revision:
                self._append_log("Discarded result: power-stage inputs changed during calculation.")
                return
            if (digital_request or isinstance(result, DigitalLoopAnalysis)) and digital_revision != self.digital_loop_view.input_revision:
                self._append_log("Discarded result: digital-loop inputs changed during calculation.")
                return
            self._results_current = True
            callback(result)
        worker.signals.result.connect(ready)
        def failed(error):
            if control is not None and control.outcome is not None:
                return  # A requested stop is reported after real worker release.
            if inputs_current():
                self._worker_error(error)
        worker.signals.error.connect(failed)
        def finished():
            if worker not in self._active_workers:
                return
            self._active_workers.remove(worker)
            owns_comparison = control is not None and control is getattr(self, "_comparison_control", None)
            if owns_comparison:
                self._comparison_control = None
            if isValid(self) and not self._closing:
                self._set_busy(bool(self._active_workers))
                if owns_comparison and control.outcome is not None:
                    self._results_current = False
                    self.multi_fidelity_analysis = None
                    self._accepted_model_options = None
                    message = (f"计算超时：已达到 {control.budget_s:g} s 总预算，未发布部分结果。"
                        if control.outcome == "timed_out" else f"计算已取消（{control.reason}），数值线程已退出，未发布结果。")
                    self.model_comparison_view.show_stopped(message)
                    self.statusBar().showMessage(message)
                    self._append_log(message)
            worker.signals.result.disconnect()
            worker.signals.error.disconnect()
            worker.signals.finished.disconnect()
            worker.signals.deleteLater()
        worker.signals.finished.connect(finished)
        worker.start(self.thread_pool)

    def _worker_error(self, error: str) -> None:
        self._append_log(error)
        show_operation_issue(self, t("计算失败"), error, critical=True)

    def run_design(self) -> None:
        try:
            self.spec = self._spec_from_widgets()
        except Exception as exc:
            show_operation_issue(self, t("参数错误"), str(exc), critical=False); return
        self._run_worker("正在运行 LLC 完整计算…", lambda: LLCSystemAnalyzer().analyze(self.spec), self._design_ready)

    def _design_ready(self, analysis: SystemAnalysis) -> None:
        self._waveform_inputs_changed()
        self._results_current = True
        self.system_analysis = analysis
        if hasattr(self, "model_comparison_view"):
            self.model_comparison_view.set_loss_report_available(
                any(point.semiconductor_source == "TIME_DOMAIN_WAVEFORM"
                    for point in analysis.operating_points))
        nominal = analysis.nominal
        op = nominal.operating_point
        text = [
            "LLC 设计结果摘要",
            "=" * 72,
            f"拓扑: {PrimaryTopology(analysis.spec.primary_topology).value} + {analysis.spec.secondary_topology.value}",
            f"输入/输出: {analysis.spec.vbus_nom_v:.1f} Vdc -> {analysis.spec.vout_v:.3f} V / {analysis.spec.pout_w/1000:.3f} kW",
            "",
            f"Lr={analysis.tank.lr_h*1e6:.6f} µH",
            f"Cr={analysis.tank.cr_f*1e9:.6f} nF",
            f"Lm={analysis.tank.lm_h*1e6:.6f} µH",
            f"标称 fs={op.switching_frequency_hz/1e3:.6f} kHz",
            "",
            f"变压器: {analysis.transformer.core.part_number}, {analysis.spec.primary_turns}:{analysis.spec.secondary_turns}, fill={analysis.transformer.fill_factor*100:.3f}%",
            (f"谐振电感: {analysis.resonant_inductor.core.part_number}, {analysis.resonant_inductor.turns} T, {analysis.resonant_inductor.layers} layers" if analysis.resonant_inductor else "集成谐振电感: 同一变压器漏感，无独立磁芯/绕组/热节点"),
            "",
            f"标称损耗={nominal.total_loss_w:.5f} W",
            f"器件电流来源={nominal.semiconductor_source}",
            f"磁件/电容来源={nominal.magnetic_source} / {nominal.capacitor_source}",
            f"换流/ZVS 来源={nominal.commutation_source}",
            f"标称效率={nominal.efficiency*100:.5f}%",
            design_status(analysis.feasible),
        ]
        if analysis.spec.integrated_magnetics:
            m = analysis.spec
            geometry = analysis.transformer.integrated_leakage
            text.extend(["", f"实体 n={m.turns_ratio:.6f}; APR n_eff={m.electrical_turns_ratio:.6f}",
                f"几何平衡漏感={geometry.balanced_geometry_h*1e6:.5f} µH；矩阵平衡目标={geometry.characterized_balanced_h*1e6:.5f} µH（不是 APR Lr）",
                f"层序 P/2-S-P/2；每界面建议间距={geometry.required_interwinding_gap_m*1e3:.4f} mm；所需build={geometry.required_radial_build_m*1e3:.4f}/{geometry.available_window_height_m*1e3:.4f} mm",
                f"几何状态: {geometry.status}；1D 工程筛查，须样件表征/绝缘验证。"])
        if analysis.feasibility_reasons:
            text.extend(["", t("设计提醒："), t("可继续查看和导出已有结果；未满足项与未求解工况仍需复核。")]
                        + [f"- {design_reason(reason)}" for reason in analysis.feasibility_reasons])
            self._append_log("Design diagnostics:\n" + "\n".join(analysis.feasibility_reasons))
        self.summary_text.setPlainText("\n".join(text))
        if hasattr(self, "loss_summary_view"):
            self.loss_summary_view.set_analysis(analysis)
        self._plot_gain(analysis)
        try:
            self.q_zvs_analysis = build_q_zvs_analysis(analysis.spec)
            self.q_zvs_view.set_analysis(self.q_zvs_analysis)
        except Exception as exc:
            self._append_log(f"Q/ZVS map warning: {exc}")
        self._append_log("Design analysis complete")
        self.tabs.setCurrentIndex(0)

    def _plot_gain(self, analysis: SystemAnalysis) -> None:
        self.gain_figure.clear()
        ax = self.gain_figure.add_subplot(111)
        spec, tank = analysis.spec, analysis.tank
        frequencies = np.linspace(spec.minimum_frequency_hz, spec.maximum_frequency_hz, 800)
        for load in (0.1, 0.25, 0.5, 0.75, 1.0):
            pout = spec.pout_w * max(load, spec.minimum_modeled_load_fraction)
            rac = equivalent_ac_load_ohm(spec.electrical_turns_ratio, spec.vout_v + spec.rectifier_equivalent_drop_v,
                                         pout * (1.0 + spec.rectifier_equivalent_drop_v / spec.vout_v))
            ax.plot(frequencies / 1e3, [gain(tank, f, rac) for f in frequencies], label=f"{load*100:.0f}%")
        for bus in (spec.vbus_hold_end_v, spec.vbus_min_normal_v, spec.vbus_nom_v, spec.vbus_max_v):
            ax.axhline(target_gain(spec, bus), linestyle="--", linewidth=0.9, label=f"{bus:.0f} V")
        for point in analysis.operating_points:
            op = point.operating_point
            ax.scatter(op.switching_frequency_hz / 1e3, op.achieved_gain, s=28)
        ax.axvline(tank.fr_hz / 1e3, linestyle=":", label="fr")
        ax.set_xlabel("Switching frequency (kHz)")
        ax.set_ylabel("Normalized gain")
        ax.grid(True, alpha=0.3)
        ax.legend(ncol=2, fontsize=8)
        self.gain_figure.tight_layout()
        self.gain_canvas.draw_idle()


    def run_transformer_design(self, core_input=None, settings=None) -> None:
        try:
            self.spec = self._spec_from_widgets()
        except Exception as exc:
            show_operation_issue(self, t("参数错误"), str(exc), critical=False); return
        if core_input is None:
            core_input = self.transformer_design_view._core_input()
        if settings is None:
            settings = self.transformer_design_view._settings()
        self._run_worker(
            "正在根据磁芯规格书自动设计 LLC 变压器…",
            lambda: synthesize_transformer(self.spec, core_input, settings),
            self._transformer_design_ready,
        )

    def _transformer_design_ready(self, result: TransformerSynthesisResult) -> None:
        self.transformer_synthesis = result
        self.transformer_design_view.set_result(result)
        self.tabs.setCurrentWidget(self.transformer_design_view)
        self._append_log(
            f"Transformer synthesis: {result.core.shape}/{result.core.material_grade}, "
            f"Np:Ns={result.primary_turns}:{result.secondary_turns}, "
            f"P={result.primary_litz.strand_count}x{result.primary_litz.strand_diameter_mm:.3f} mm, "
            f"S={result.secondary_litz.strand_count}x{result.secondary_litz.strand_diameter_mm:.3f} mm, "
            f"loss={result.total_nominal_loss_w:.3f} W, feasible={result.feasible}"
        )

    def _apply_transformer_turns(self, primary_turns: int, secondary_turns: int) -> None:
        self.primary_turns.setValue(int(primary_turns))
        self.secondary_turns.setValue(int(secondary_turns))
        self.spec = self._spec_from_widgets()
        self.statusBar().showMessage(
            f"已将变压器推荐匝数应用到主设计：{primary_turns}:{secondary_turns}"
        )

    def _export_transformer_design(self) -> None:
        if self.transformer_synthesis is None:
            return
        out = self.output_directory / "transformer_design"
        paths = export_transformer_synthesis(self.transformer_synthesis, out)
        self._append_log(f"Transformer design exported: {paths['json']}")
        self.statusBar().showMessage(f"变压器设计已导出到：{out}")



    def run_q_zvs(self) -> None:
        try:
            self.spec = self._spec_from_widgets()
        except Exception as exc:
            show_operation_issue(self, t("参数错误"), str(exc), critical=False); return
        self._run_worker(
            "正在计算 LLC 多负载 Q / ZVS 工作区域…",
            lambda: build_q_zvs_analysis(self.spec),
            self._q_zvs_ready,
        )

    def _q_zvs_ready(self, result: LLCQZVSAnalysis) -> None:
        self.q_zvs_analysis = result
        self.q_zvs_view.set_analysis(result)
        self.tabs.setCurrentWidget(self.q_zvs_view)
        self._append_log(
            f"Q/ZVS map ready: {len(result.map.load_fractions)} load levels, "
            f"{len(result.workpoints)} operating points, warnings={len(result.warnings)}"
        )

    def _ensure_analysis(self) -> SystemAnalysis:
        if self.system_analysis is None or self.system_analysis.spec != self.spec:
            self.system_analysis = LLCSystemAnalyzer().analyze(self.spec)
        return self.system_analysis

    def _build_small_signal(self, options: dict) -> SmallSignalAnalysis:
        analysis = self._ensure_analysis()
        defaults = {
            "vbus_v": self.spec.vbus_nom_v,
            "load_fraction": 1.0,
            "sample_time_s": 20e-6,
            "control_input_kind": ControlInputKind.FREQUENCY_HZ,
            "timer_clock_hz": 120e6,
            "input_delay_samples": 0,
        }
        defaults.update(options)
        return build_small_signal_analysis(
            self.spec,
            system_analysis=analysis,
            **defaults,
        )

    def run_model_comparison(self, options: dict | None = None) -> None:
        try:
            self.spec = self._spec_from_widgets()
        except Exception as exc:
            show_operation_issue(self, t("参数错误"), str(exc), critical=False); return
        options = options or {}
        vbus_v = float(options.get("vbus_v", self.spec.vbus_nom_v))
        load_fraction = float(options.get("load_fraction", 1.0))
        max_harmonic = int(options.get("max_harmonic", 7))
        if max_harmonic % 2 == 0:
            max_harmonic += 1
        hb_samples = int(options.get("hb_samples", 1024))
        include_td = bool(options.get("include_time_domain", True))
        apply_td_losses = bool(options.get("apply_td_losses", False))
        spec = self.spec
        self._invalidate_model_comparison()
        self._results_current = False
        self._model_comparison_request_id = getattr(self, "_model_comparison_request_id", 0) + 1
        request_id = self._model_comparison_request_id
        self.model_comparison_view.set_loss_report_available(False)
        if apply_td_losses and not include_td:
            show_operation_issue(self, t("参数错误"), t("请先勾选运行分段时域参考。"), critical=False)
            return

        from ..analysis.execution import SolveControl
        try:
            control = SolveControl(float(options.get("execution_budget_s", 300.0)))
        except (TypeError, ValueError) as exc:
            show_operation_issue(self, t("参数错误"), str(exc), critical=False)
            return
        self._comparison_control = control

        def calculate():
            control.checkpoint()
            request = LLCAnalysisRequest(
                spec=spec,
                vbus_v=vbus_v,
                load_fraction=load_fraction,
                waveform_cycles=2,
                samples_per_cycle=hb_samples,
            )
            config = GoldenSolverConfig(
                include_time_domain=include_td,
                harmonic_balance=HarmonicBalanceConfig(
                    max_harmonic=max_harmonic,
                    minimum_harmonic=min(5, max_harmonic),
                    samples_per_cycle=hb_samples,
                    output_cycles=2,
                ),
                time_domain=TimeDomainConfig(
                    samples_per_cycle=512,
                    output_cycles=2,
                ),
            )
            result = LLCGoldenSolver(config).solve(request, checkpoint=control.checkpoint)
            control.checkpoint()
            losses = None
            if apply_td_losses:
                analyzer = LLCSystemAnalyzer()
                base = analyzer.analyze(spec, work_points=[(vbus_v, load_fraction)])
                control.checkpoint()
                losses = analyzer.with_time_domain_losses(
                    base, [result.result(FidelityLevel.SWITCHED_TIME_DOMAIN)])
            control.checkpoint()
            return result, losses

        calculate._llc_comparison_control = control
        self._run_worker(
            "正在计算 FHA / 多谐波 HB / 分段时域对比…",
            calculate,
            lambda payload: self._model_comparison_ready(*payload)
            if request_id == self._model_comparison_request_id else None,
        )

    def _model_comparison_ready(self, result: MultiFidelityAnalysis,
                                loss_analysis: SystemAnalysis | None = None) -> None:
        try:
            current_spec = self._spec_from_widgets()
        except ValueError:
            self._append_log("Discarded model comparison: current inputs are invalid")
            return
        if result.request.spec != current_spec:
            self._append_log("Discarded stale model comparison: inputs changed during calculation")
            return
        self.model_comparison_view.set_loss_report_available(loss_analysis is not None)
        if loss_analysis is not None:
            self._design_ready(loss_analysis)
        self.multi_fidelity_analysis = result
        self._accepted_model_options = self.model_comparison_view.options()
        self.model_comparison_view.set_analysis(result)
        self.tabs.setCurrentWidget(self.model_comparison_view)
        self._append_log(
            "Multi-fidelity analysis ready: "
            f"reference={result.reference_level.value}, "
            f"models={','.join(level.value for level in result.results)}"
        )

    def _export_td_loss_report(self) -> None:
        analysis = self.system_analysis
        comparison = self.multi_fidelity_analysis
        if analysis is None or comparison is None:
            return
        options = deepcopy(getattr(self, "_accepted_model_options", None))
        if options != self.model_comparison_view.options():
            self._invalidate_model_comparison()
            return
        if not any(point.semiconductor_source == "TIME_DOMAIN_WAVEFORM"
                   and point.operating_point.vbus_v == comparison.request.bus_voltage_v
                   and point.operating_point.load_fraction == comparison.request.load_fraction
                   for point in analysis.operating_points):
            return
        if analysis.spec != self._spec_from_widgets() or comparison.request.spec != analysis.spec:
            show_operation_issue(self, t("参数错误"), t("输入已改变，请重新计算 TD 损耗后导出。"), critical=False)
            return
        owner = self._project_owner()
        request_id = getattr(self, "_model_comparison_request_id", 0)

        def current():
            return (isValid(self) and not self._closing and owner == self._project_owner()
                    and self.system_analysis is analysis and self.multi_fidelity_analysis is comparison
                    and request_id == getattr(self, "_model_comparison_request_id", 0)
                    and options == getattr(self, "_accepted_model_options", None)
                    and options == self.model_comparison_view.options()
                    and self._spec_from_widgets() == analysis.spec)

        directory = QFileDialog.getExistingDirectory(self, "导出当前 TD 损耗报告", str(self.output_directory))
        if not directory or not current():
            return
        import tempfile

        from ..analysis import export_multifidelity_analysis
        from ..report.export import export_calculation_book
        from .optimization_install import publish_export_files

        def export():
            with tempfile.TemporaryDirectory(prefix="llc-td-report-") as temporary:
                root = Path(temporary)
                paths = export_calculation_book(analysis, root)
                export_multifidelity_analysis(comparison, root / "model_comparison")
                files = {str(item.relative_to(root)): item.read_bytes()
                         for item in root.rglob("*") if item.is_file()}
                return files, str(Path(paths["report"]).relative_to(root))

        export._llc_waveform_current = current

        def ready(payload):
            if not current():
                return
            files, report = payload
            try:
                published = publish_export_files(directory, files)
            except (OSError, ValueError) as exc:
                self._worker_error(str(exc))
                return
            self._append_log(f"TD loss report exported: {published[report]}")

        self._run_worker(t("正在导出同一 TD 工况的损耗与波形报告…"), export, ready)

    def run_sr_design(self, options: dict | None = None) -> None:
        try:
            self.spec = self._spec_from_widgets()
        except Exception as exc:
            show_operation_issue(self, t("参数错误"), str(exc), critical=False); return
        options = options or {}
        vbus = float(options.get("vbus_v", self.spec.vbus_nom_v))
        load = float(options.get("load_fraction", 1.0))
        def calculate():
            request = LLCAnalysisRequest(self.spec, vbus_v=vbus, load_fraction=load, waveform_cycles=1, samples_per_cycle=1024)
            result = solve_harmonic_balance(request, HarmonicBalanceConfig(max_harmonic=7, samples_per_cycle=1024, output_cycles=1))
            return analyze_sr(result, self.spec, DeviceDatabase().get_sr(self.spec.sr_device))
        self._run_worker("正在计算 Golden SR Timing / Qrr / 第三象限损耗…", calculate, self._sr_design_ready)

    def _sr_design_ready(self, result) -> None:
        self.sr_design_view.set_result(result)
        self.tabs.setCurrentWidget(self.sr_design_view)
        self._append_log(f"SR timing ready: Ton={result.timing.on_delay_s*1e9:.2f} ns, ToffAdv={result.timing.off_advance_s*1e9:.2f} ns, loss={result.loss.total_w:.3f} W")

    def run_interleaved(self, phase_count: int, options: dict | None = None) -> None:
        try:
            self.spec = self._spec_from_widgets()
        except Exception as exc:
            show_operation_issue(self, t("参数错误"), str(exc), critical=False); return
        options = options or {}
        vbus = float(options.get("vbus_v", self.spec.vbus_nom_v))
        load = float(options.get("load_fraction", 1.0))
        self._run_worker(
            f"正在计算 {phase_count} 相固定交错 LLC…",
            lambda: solve_interleaved_llc(self.spec, int(phase_count), vbus_v=vbus, load_fraction=load, samples_per_cycle=512),
            self._interleaved_ready,
        )

    def _interleaved_ready(self, result) -> None:
        self.interleaved_view.set_result(result)
        self.tabs.setCurrentWidget(self.interleaved_view)
        self._append_log(f"Interleaved ready: N={result.phase_count}, offsets={result.phase_offsets_deg}, CoutIrms={result.output_capacitor_rms_a:.3f} A")


    def _digital_loop_inputs_changed(self) -> None:
        self.digital_loop_analysis = None
        view = self.digital_loop_view
        self.external_control_design = view._external_controller if view.external_controller_check.isChecked() else None
        self.external_control_label = view._external_controller_label if self.external_control_design is not None else ""
        if hasattr(self, "refresh_closed_loop_controller"):
            self.refresh_closed_loop_controller()

    def set_external_control_design(self, digital, label: str = "") -> None:
        """Receive the canonical H(z) designed in Control Tools.

        V9 uses this exact discrete transfer function in the LLC small-signal
        closed-loop analysis.  No PI/PID parameter re-fit is performed.
        """
        self.external_control_design = digital
        self.external_control_label = label or getattr(digital, "name", "Control Tools")
        if hasattr(self, "digital_loop_view"):
            self.digital_loop_view.set_external_controller(digital, self.external_control_label)
        self._append_log(
            f"Control Tools -> LLC small-signal loop: {self.external_control_label}, "
            f"Fs={digital.sample_rate_hz/1e3:.3f} kHz"
        )

    def _prepare_waveform_request(self, solver: str, options: dict | None):
        # Capture every physical input on the GUI thread, before the worker runs.
        # No worker reads a widget or mutates the shared nominal system analysis.
        retained_inputs = deepcopy(self._waveform_plant_inputs) if (
            solver == self.waveform_view.solver and
            (options is None or options == self.waveform_view.work_point())) else None
        self._waveform_inputs_changed()
        self._waveform_plant_inputs = retained_inputs
        spec = deepcopy(self._spec_from_widgets())
        self.spec = spec
        view = self.waveform_view
        view.solver = solver
        if options is not None and options != view.work_point():
            view.set_work_point(options)
        state = self._waveform_session(spec)
        identity = waveform_request_identity(state)
        generation = self._waveform_generation
        def current():
            if not isValid(self) or self._closing or generation != self._waveform_generation:
                return False
            try:
                return identity == waveform_request_identity(self._waveform_session())
            except (TypeError, ValueError):
                return False
        return spec, state, identity, generation, current

    def _accept_waveform(self, bundle, state, identity, generation):
        # Resolved electrical coefficients are replayable inputs, not saved curve
        # authority. Include them in the accepted identity and subsequent session.
        self._waveform_plant_inputs = deepcopy(state["plant_inputs"])
        identity = waveform_request_identity(state)
        metadata = {**bundle.metadata, **state["work_point"],
                    "primary_topology": self.spec.primary_topology.value,
                    "request_identity": identity, "request_generation": generation,
                    "spec_identity": state["spec_identity"],
                    "model_revision": state["model_revision"], "solver": state["solver"],
                    "solver_options": json.dumps(state["solver_options"], sort_keys=True),
                    "replay_spec": json.dumps(asdict(self.spec), sort_keys=True),
                    "replay_session": json.dumps(state, sort_keys=True)}
        if state["plant_inputs"] is not None:
            metadata["plant_inputs"] = json.dumps(state["plant_inputs"], sort_keys=True)
            metadata["input_authority"] = "Frozen equivalent input coefficients only; recomputed curve, no saved TD loss authority"
        signals = bundle.signals
        if self.spec.primary_topology == PrimaryTopology.HALF_BRIDGE:
            signals = {key: value for key, value in signals.items()
                       if not key.endswith(("_q3", "_q4"))}
        bundle = replace(bundle, metadata=metadata, signals=signals)
        self._accepted_waveform_identity = identity
        self.waveform_view.set_primary_topology(self.spec.primary_topology)
        from ..models.waveform_evidence import component_waveform_evidence
        self.waveform_view.component_context = component_waveform_evidence(bundle, self.spec)
        self.waveform_view.set_bundle(bundle)
        self.tabs.setCurrentWidget(self.waveform_view)
        self._append_log(f"Waveforms ready: {bundle.model_name}; source={identity}")

    def run_harmonic_waveforms(self, options: dict | None = None) -> None:
        try:
            spec, state, identity, generation, current = self._prepare_waveform_request("harmonic", options)
        except Exception as exc:
            show_operation_issue(self, t("参数错误"), str(exc), critical=False); return
        request = LLCAnalysisRequest(spec=spec, **state["work_point"],
                                     waveform_cycles=2, samples_per_cycle=1024)
        def calculate():
            result = solve_harmonic_balance(request, HarmonicBalanceConfig(**state["solver_options"]))
            if not result.convergence.converged:
                raise ValueError(f"Harmonic waveform did not converge: {result.convergence.message}")
            return result
        calculate._llc_waveform_current = current
        def ready(result):
            self._accept_waveform(result.waveform, state, identity, generation)
        self._run_worker("正在求解自洽多谐波 LLC 波形…", calculate, ready)

    def _harmonic_waveforms_ready(self, result: LLCModelResult) -> None:
        # Retained API for existing callers; ordinary requests use guarded snapshots.
        self.waveform_view.set_bundle(result.waveform)
        self.tabs.setCurrentWidget(self.waveform_view)

    def run_waveforms(self, detailed: bool, options: dict | None = None) -> None:
        try:
            spec, state, identity, generation, current = self._prepare_waveform_request(
                "detailed" if detailed else "fast", options)
        except Exception as exc:
            show_operation_issue(self, t("参数错误"), str(exc), critical=False); return
        # Preserve existing nominal loss/damping consumers, but use a detached
        # snapshot and only if its physical spec is exactly the requested spec.
        analysis = deepcopy(self.system_analysis) if (
            self.system_analysis is not None and self.system_analysis.spec == spec) else None
        def calculate():
            local_analysis = analysis or LLCSystemAnalyzer().analyze(spec)
            frozen = state["plant_inputs"]
            small = build_small_signal_analysis(spec, system_analysis=local_analysis,
                **state["work_point"], timer_clock_hz=120e6,
                series_resistance_ohm=None if frozen is None else frozen["series_resistance_ohm"])
            if frozen is not None:
                geometry = {key: value for key, value in frozen.items()
                            if key not in {"source", "series_resistance_ohm"}}
                small = replace(small, parameters=replace(small.parameters, **geometry))
            if not small.steady_state.converged:
                raise ValueError("Waveform operating point did not converge")
            model = DynamicPhasorModel(small.parameters)
            if detailed:
                bundle = simulate_switched_steady_state(model, small.steady_state,
                    SwitchedSimulationConfig(**state["solver_options"]))
                if str(bundle.metadata.get("converged", "False")).lower() != "true":
                    raise ValueError("Detailed waveform did not reach a periodic steady state")
            else:
                bundle = reconstruct_dynamic_phasor_waveforms(
                    model, small.steady_state, **state["solver_options"])
            from ..models.waveform_evidence import waveform_electrical_inputs
            inputs = frozen if frozen is not None else waveform_electrical_inputs(small, local_analysis)
            return small, bundle, inputs
        calculate._llc_waveform_current = current
        def ready(result):
            small, bundle, inputs = result
            self.small_signal_analysis = small
            self._waveform_small_signal = small
            self._accept_waveform(bundle, {**state, "plant_inputs": inputs}, identity, generation)
        self._run_worker("正在计算详细开关波形…" if detailed else "正在重构关键波形…",
                         calculate, ready)

    def _waveforms_ready(self, result: tuple[SmallSignalAnalysis, WaveformBundle]) -> None:
        self.small_signal_analysis, bundle = result
        self.waveform_view.set_bundle(bundle)
        self.tabs.setCurrentWidget(self.waveform_view)

    def export_current_waveforms(self) -> None:
        bundle = self.waveform_view.bundle
        if bundle is None or self._accepted_waveform_identity is None:
            return
        owner = self._waveform_owner()
        identity = self._accepted_waveform_identity
        path = QFileDialog.getExistingDirectory(self, "导出当前波形与报告", str(self.output_directory))
        if not path or self._closing or owner != self._waveform_owner() or bundle is not self.waveform_view.bundle:
            return
        try:
            if identity != waveform_request_identity(self._waveform_session()):
                self._waveform_inputs_changed()
                return
            from ..dynamics.export import export_waveform_bundle
            context = {**self.topology_view.context(),
                       "selected_component": self.waveform_view.selected_component,
                       "selected_signals": list(self.waveform_view._selected_keys()),
                       "request_identity": identity,
                       "component_evidence": self.waveform_view.component_context,
                       "losses_constraints": "Only same-bundle stresses and documented ratings; unmatched nominal losses are unknown."}
            paths = export_waveform_bundle(bundle, path, context=context)
            # Scene rendering preserves all connections even when its tab is hidden.
            scene = self.topology_view.scene()
            image = QImage(1380, 570, QImage.Format.Format_ARGB32)
            image.fill(Qt.GlobalColor.white)
            painter = QPainter(image)
            scene.render(painter)
            painter.end()
            if not image.save(str(Path(path) / "llc_schematic.png")):
                raise OSError("Could not save the electrical schematic")
            self._append_log(f"Current waveform report: {paths['report']}; source={identity}")
            self.statusBar().showMessage(f"当前波形与报告已导出：{paths['report']}", 8000)
        except (OSError, TypeError, ValueError, RuntimeError) as exc:
            show_operation_issue(self, t("导出失败"), str(exc), critical=True)

    def run_small_signal(self, options: dict) -> None:
        try:
            self.spec = self._spec_from_widgets()
        except Exception as exc:
            show_operation_issue(self, t("参数错误"), str(exc), critical=False); return
        self._run_worker("正在建立 LLC 小信号与 ZOH 对象…",
                         lambda: self._build_small_signal(options), self._small_signal_ready)

    def _small_signal_ready(self, result: SmallSignalAnalysis) -> None:
        self.small_signal_analysis = result
        self.small_signal_view.set_analysis(result)
        self.tabs.setCurrentWidget(self.small_signal_view)
        self._append_log(
            f"Small signal ready: G(0)={result.continuous_transfer.dc_gain:.9g}, "
            f"stable={result.stable}")

    def run_digital_loop(self, options: dict) -> None:
        self.digital_loop_view.invalidate_analysis()
        try:
            self.spec = self._spec_from_widgets()
        except Exception as exc:
            show_operation_issue(self, t("参数错误"), str(exc), critical=False); return

        def calculate():
            small_options = options.get("small_signal", {}) if options else {}
            loop_options = options.get("loop", {}) if options else {}
            small = self._build_small_signal(small_options)
            return build_digital_loop_analysis(small, **loop_options)

        calculate._llc_digital_request = True
        self._run_worker(
            "正在建立完整 LLC 数字电压环…",
            calculate,
            self._digital_loop_ready,
        )

    def _digital_loop_ready(self, result: DigitalLoopAnalysis) -> None:
        self.digital_loop_analysis = result
        self.small_signal_analysis = result.small_signal
        self.digital_loop_view.set_analysis(result)
        self.tabs.setCurrentWidget(self.digital_loop_view)
        margin = result.margins_nominal_delay
        self._append_log(
            "Digital loop ready: "
            f"PCMD={result.fm_operating_point.command_pu:.6g}, "
            f"Kfm={result.fm_operating_point.gain_hz_per_pu:.7g} Hz/pu, "
            f"fc={margin.critical_gain_crossover_hz}, "
            f"PM={margin.phase_margin_deg}, stable={result.likely_stable}"
        )

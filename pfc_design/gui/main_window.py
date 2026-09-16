"""Dedicated PFC top-level workspace with TTPL and Vienna sub-workspaces."""
from __future__ import annotations

import traceback

from PySide6.QtCore import QThreadPool, QTimer, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QSizePolicy,
    QStatusBar,
    QTabWidget,
    QWidget,
)

from llc_design.gui import theme
from llc_design.gui.help import install_help
from llc_design.gui.i18n_ui import install_language_selector
from llc_design.gui.updater import add_toolbar_right_side, check_for_updates
from llc_design.gui.workers import FunctionWorker
from llc_design.i18n import t
from pfc_design.control import (
    PFCControlLabConfig,
    build_pfc_control_handoff,
    build_pfc_control_lab_analysis,
    build_pfc_switching_waveforms,
    simulate_pfc_line_cycle,
)
from pfc_design.vienna import (
    ViennaControlLabConfig,
    build_vienna_control_lab_analysis,
    build_vienna_switching_waveforms,
    simulate_vienna_line_cycle,
    validate_vienna_analysis,
    validate_vienna_line_cycle,
    validate_vienna_switching,
)
from power_sim.spice import run_ttpl_shared_closed_loop

from .ac_switching_install import install_ttpl_ac_switching_stages
from .cap_thermal_install import install_ttpl_capacitor_thermal_stage
from .device_loss_install import install_ttpl_device_loss_stage
from .exact_hz_install import install_ttpl_exact_hz_stage
from .ngspice_closed_loop_install import install_ttpl_ngspice_closed_loop_stage
from .ttpl_engineering_view import TTPLWorkbenchView
from .vienna_control_view import ViennaControlLabView


class PFCMainWindow(QMainWindow):
    """Independent PFC workspace: engineering TTPL + three-phase Vienna."""

    workspace_switch_requested = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("电源设计工具箱 — PFC Engineering: TTPL / Vienna")
        self.resize(1920, 1080)
        self.thread_pool = QThreadPool.globalInstance()
        self._active_workers = []
        self.result = None

        self.subtabs = QTabWidget()
        self.subtabs.setDocumentMode(True)
        self.subtabs.setUsesScrollButtons(True)
        # Keep the historical attribute name as a compatibility surface for
        # tests/callers, but the object is now an engineering workbench whose
        # stages cover hardware, AC/switching, exact H(z) and circuit-level
        # shared-ngspice verification.
        self.control_lab_view = TTPLWorkbenchView()
        self.vienna_view = ViennaControlLabView()
        install_ttpl_device_loss_stage(self)
        install_ttpl_capacitor_thermal_stage(self)
        install_ttpl_ac_switching_stages(self)
        install_ttpl_exact_hz_stage(self)
        install_ttpl_ngspice_closed_loop_stage(self)
        self.control_lab_view.analysis_requested.connect(self.run_ttpl_analysis)
        self.vienna_view.analysis_requested.connect(self.run_vienna_analysis)
        self.subtabs.addTab(self.control_lab_view, "Single-Phase TTPL Engineering")
        self.subtabs.addTab(self.vienna_view, "Three-Phase Vienna PFC")
        self.setCentralWidget(self.subtabs)

        self._build_toolbar()
        self._build_statusbar()
        self._apply_pfc_style()

    def _build_toolbar(self):
        toolbar = self.addToolBar("PFC 工作区")
        toolbar.setMovable(False)

        action = QAction("切换到 LLC", self)
        action.triggered.connect(lambda: self.workspace_switch_requested.emit("llc"))
        toolbar.addAction(action)
        action = QAction("Control Tools", self)
        action.triggered.connect(lambda: self.workspace_switch_requested.emit("control"))
        toolbar.addAction(action)
        action = QAction("功能选择", self)
        action.triggered.connect(lambda: self.workspace_switch_requested.emit("home"))
        toolbar.addAction(action)
        toolbar.addSeparator()

        action = QAction("TTPL Engineering", self)
        action.triggered.connect(lambda: self.subtabs.setCurrentIndex(0))
        toolbar.addAction(action)
        action = QAction("Vienna", self)
        action.triggered.connect(lambda: self.subtabs.setCurrentIndex(1))
        toolbar.addAction(action)

        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        toolbar.addWidget(spacer)
        action = QAction("运行当前 PFC 分析", self)
        action.triggered.connect(self._run_current)
        toolbar.addAction(action)
        action = QAction("关于 PFC", self)
        action.triggered.connect(self.show_about)
        toolbar.addAction(action)

        install_help(self, "pfc")
        add_toolbar_right_side(toolbar, self)
        install_language_selector(self)
        QTimer.singleShot(2500, self._auto_check_update)

    def _auto_check_update(self):
        check_for_updates(self, notify_up_to_date=False)

    def _apply_pfc_style(self):
        self.setStyleSheet(theme.workspace_stylesheet(theme.active_theme()))

    def _run_current(self):
        widget = self.subtabs.currentWidget()
        if hasattr(widget, "_request"):
            widget._request()

    def _build_statusbar(self):
        status = QStatusBar()
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setVisible(False)
        status.addPermanentWidget(self.progress)
        self.setStatusBar(status)
        status.showMessage(t("PFC 工作区就绪"))

    def set_busy(self, busy, message=""):
        self.progress.setVisible(busy)
        self.control_lab_view.set_busy(busy)
        if hasattr(self.control_lab_view, "switching_validation_view"):
            self.control_lab_view.switching_validation_view.set_busy(busy)
        if hasattr(self.control_lab_view, "exact_hz_view"):
            self.control_lab_view.exact_hz_view.set_busy(busy)
        if hasattr(self.control_lab_view, "ngspice_closed_loop_view"):
            self.control_lab_view.ngspice_closed_loop_view.set_busy(busy)
        self.vienna_view.set_busy(busy)
        self.statusBar().showMessage(message if busy else t("PFC 工作区就绪"))

    def _run_worker(self, label, function, callback):
        self.set_busy(True, label)
        worker = FunctionWorker(function)
        self._active_workers.append(worker)
        worker.signals.result.connect(callback)
        worker.signals.error.connect(self._worker_error)
        worker.signals.finished.connect(lambda: self.set_busy(False))
        worker.signals.finished.connect(lambda: self._active_workers.remove(worker))
        self.thread_pool.start(worker)

    def _worker_error(self, error):
        lines = [line for line in str(error).splitlines() if line.strip()]
        last = lines[-1] if lines else "Unknown PFC calculation error"
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Critical)
        box.setWindowTitle("PFC 计算失败")
        box.setText(last)
        box.setInformativeText("已保留完整 traceback。点击“显示详细信息”可直接复制给开发者定位。")
        box.setDetailedText(str(error))
        box.exec()

    def run_ttpl_analysis(self, config: PFCControlLabConfig):
        def calculate():
            analysis = build_pfc_control_lab_analysis(config)
            line = simulate_pfc_line_cycle(config)
            switching = build_pfc_switching_waveforms(
                config,
                line_cycle=line,
                line_angle_deg=config.power_stage.line_angle_deg,
            )
            return analysis, line, switching

        self._run_worker(
            "正在建立 TTPL 双环、AC 周期、过零与开关工作点…",
            calculate,
            self._ttpl_ready,
        )

    def _ttpl_ready(self, result):
        try:
            self.result = result
            # Control/Bode remains the owner of controller/sensing settings.
            # AC/switching, exact H(z) and shared-ngspice all consume this exact
            # analysis result; no downstream stage recreates the controller.
            self.control_lab_view.set_result(result)
            self.control_lab_view.ac_performance_view.set_result(result)
            self.control_lab_view.switching_validation_view.set_result(result)
            analysis, line, _ = result
            self.control_lab_view.exact_hz_view.set_analysis(analysis)
            self.control_lab_view.ngspice_closed_loop_view.set_analysis(analysis)
            current = analysis.current_loop.margins
            voltage = analysis.voltage_loop.margins
            self.statusBar().showMessage(
                f"TTPL 完成: Li fc={current.critical_gain_crossover_hz}, "
                f"PM={current.phase_margin_deg}; Lv fc={voltage.critical_gain_crossover_hz}, "
                f"PM={voltage.phase_margin_deg}; PF={line.metrics.power_factor:.6g}, "
                f"THD={line.metrics.current_thd_percent:.5g}%"
            )
        except Exception:
            self._worker_error("TTPL 结果绘图/GUI 更新失败\n" + traceback.format_exc())

    def run_ttpl_shared_ngspice(self, analysis, scenario, simulation) -> None:
        """Run the circuit-level TTPL path from the frozen exact-H(z) contract."""
        def calculate():
            handoff = build_pfc_control_handoff(analysis)
            return run_ttpl_shared_closed_loop(
                analysis,
                handoff=handoff,
                scenario=scenario,
                simulation=simulation,
            )

        self._run_worker(
            "正在运行 TTPL shared-ngspice：Exact H(z) → PWM → 四开关功率级…",
            calculate,
            self._ttpl_ngspice_ready,
        )

    def _ttpl_ngspice_ready(self, result) -> None:
        try:
            self.control_lab_view.ngspice_closed_loop_view.set_simulation_result(result)
            m = result.metrics
            self.statusBar().showMessage(
                f"TTPL shared-ngspice 完成: Vbus={m.final_bus_voltage_v:.3f} V, "
                f"Ipk={m.peak_inductor_current_a:.3f} A, "
                f"duty={m.duty_min:.4f}..{m.duty_max:.4f}"
            )
        except Exception:
            self._worker_error("TTPL shared-ngspice 结果绘图/GUI 更新失败\n" + traceback.format_exc())

    def run_vienna_analysis(self, config: ViennaControlLabConfig):
        def calculate():
            try:
                analysis = build_vienna_control_lab_analysis(config)
                validate_vienna_analysis(analysis)
            except Exception as exc:
                raise RuntimeError(f"Vienna 小信号/Bode 建模失败: {exc}") from exc
            try:
                line = simulate_vienna_line_cycle(config)
                validate_vienna_line_cycle(line)
            except Exception as exc:
                raise RuntimeError(f"Vienna 三相 AC 周期求解失败: {exc}") from exc
            try:
                switching = build_vienna_switching_waveforms(
                    config, line, line_angle_deg=config.switching_line_angle_deg
                )
                validate_vienna_switching(switching)
            except Exception as exc:
                raise RuntimeError(f"Vienna 开关工作点重建失败: {exc}") from exc
            return analysis, line, switching

        self._run_worker(
            "正在建立 Vienna ABC 双环、中点平衡、三相 AC 周期与 Sector…",
            calculate,
            self._vienna_ready,
        )

    def _vienna_ready(self, result):
        try:
            self.result = result
            self.vienna_view.set_result(result)
            analysis, line, _ = result
            current = analysis.current_loop.margins
            voltage = analysis.voltage_loop.margins
            balance = analysis.balance_loop.margins
            self.statusBar().showMessage(
                f"Vienna 完成: Li PM={current.phase_margin_deg}; "
                f"Lv PM={voltage.phase_margin_deg}; Balance PM={balance.phase_margin_deg}; "
                f"PF={line.metrics.overall_power_factor:.6g}"
            )
        except Exception:
            self._worker_error("Vienna 结果绘图/GUI 更新失败\n" + traceback.format_exc())

    def show_about(self):
        QMessageBox.about(
            self,
            t("关于 PFC Design"),
            "<h3>PFC Engineering Workspace</h3>"
            "<p>Single-phase TTPL + Three-phase Vienna PFC.</p>"
            "<p>TTPL follows an explicit engineering flow: power-stage sizing, device/loss selection, "
            "DC-bus capacitor/thermal design, AC PF/THD, switching/zero-crossing, sensing/Bode, exact "
            "H(z)/C99 handoff, then shared-ngspice digital closed-loop switching verification.</p>"
            "<p>Exact H(z) remains the linear controller source of truth; the ngspice stage does not rebuild "
            "Kp/Ti or re-discretize the controller.</p>"
            "<p>Vienna retains split DC bus, midpoint balance and sector analysis while "
            "its engineering-design layer is upgraded in a later phase.</p>",
        )


__all__ = ["PFCMainWindow"]

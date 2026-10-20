"""Dedicated PFC top-level workspace with TTPL and Vienna sub-workspaces."""
from __future__ import annotations

import traceback
from threading import Event
from dataclasses import dataclass, replace

from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from PySide6.QtCore import QThreadPool, QTimer, Signal, QSize
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QFileDialog,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QScrollArea,
    QSizePolicy,
    QStatusBar,
    QTabWidget,
    QWidget,
)
from shiboken6 import isValid

from llc_design.gui import theme
from llc_design.gui.help import install_help
from llc_design.gui.i18n_ui import install_language_selector
from llc_design.gui.updater import add_toolbar_right_side, check_for_updates
from llc_design.gui.workers import FunctionWorker
from llc_design.i18n import t
from llc_design.user_messages import show_operation_issue
from pfc_design.control import (
    PFCControlLabConfig,
    build_pfc_control_handoff,
    build_pfc_switching_waveforms,
)
from pfc_design.engineering.pfc_v3 import (
    PFCEngineeringV3Result,
    build_pfc_engineering_v3,
)
from pfc_design.vienna import (
    ViennaControlLabConfig,
    ViennaAnalysisCancelled,
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
from .three_phase_view import ThreePhaseDQView
from .ttpl_engineering_view import TTPLWorkbenchView
from .v3_diagnostics_view import install_v3_diagnostics, show_v3_diagnostics
from .vienna_control_view import ViennaControlLabView


@dataclass(frozen=True)
class _TTPLCalculation:
    legacy_result: tuple
    engineering: PFCEngineeringV3Result
    input_revision: int | None = None
    displayed_config: PFCControlLabConfig | None = None


@dataclass(frozen=True)
class _ViennaCalculation:
    legacy_result: tuple
    input_revision: int
    displayed_config: ViennaControlLabConfig


class _WorkspaceTabs(QTabWidget):
    """Size the scroll viewport for the active workspace, not every hidden page.

    QTabWidget normally takes the maximum page hint. A large hidden TTPL table
    must not force scrolling around a compact Vienna/dq page at 1280×720.
    The active page still retains its own real minimum and scroll reachability.
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.currentChanged.connect(lambda _index: self.updateGeometry())

    def _active_hint(self, minimum):
        page = self.currentWidget()
        if page is None:
            return super().minimumSizeHint() if minimum else super().sizeHint()
        hint = page.minimumSizeHint() if minimum else page.sizeHint()
        return QSize(max(0, hint.width()) + 4,
                     max(0, hint.height()) + self.tabBar().sizeHint().height() + 4)

    def minimumSizeHint(self):
        return self._active_hint(True)

    def sizeHint(self):
        return self._active_hint(False)


class PFCMainWindow(QMainWindow):
    """Independent PFC workspace: engineering TTPL + three-phase Vienna."""

    workspace_switch_requested = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("电源设计工具箱 — PFC Engineering: TTPL / Vienna / Two-Level dq")
        self.resize(1920, 1080)
        self.thread_pool = QThreadPool.globalInstance()
        self._active_workers = []
        self._worker_request_id = 0
        self._ttpl_input_revision = 0
        self._vienna_input_revision = 0
        self._vienna_cancel_event: Event | None = None
        self.result = None
        self.engineering_result = None

        self.subtabs = _WorkspaceTabs()
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
        install_v3_diagnostics(self.control_lab_view)
        self.control_lab_view.switching_validation_view.zero_cross_evidence_changed.connect(
            self._on_v3_zero_cross_changed
        )
        self.control_lab_view.analysis_requested.connect(self.run_ttpl_analysis)
        self.vienna_view.analysis_requested.connect(self.run_vienna_analysis)
        self.subtabs.addTab(self.control_lab_view, "Single-Phase TTPL Engineering")
        self.subtabs.addTab(self.vienna_view, "Three-Phase Vienna PFC")
        self.three_phase_view = ThreePhaseDQView()
        self.subtabs.addTab(self.three_phase_view, "Three-Phase Two-Level dq / PLL")
        # Some existing engineering pages have wide tables and tall plots.
        # Keep their real layout and navigation intact, but make the full
        # workspace scrollable instead of forcing a desktop-sized main window.
        self.workspace_scroll = QScrollArea(self)
        self.workspace_scroll.setObjectName("pfc_workspace_scroll")
        self.workspace_scroll.setWidgetResizable(True)
        self.workspace_scroll.setWidget(self.subtabs)
        self.setCentralWidget(self.workspace_scroll)

        self._build_toolbar()
        self._build_statusbar()
        self._apply_pfc_style()
        self.control_lab_view.control_lab.inputs_changed.connect(self._invalidate_ttpl_results)
        self.vienna_view.config_changed.connect(self._invalidate_vienna_results)
        self.vienna_view.cancel_requested.connect(self._cancel_vienna_analysis)

    def _invalidate_vienna_results(self) -> None:
        """Invalidate only Vienna ownership, including an in-flight request."""
        if self._vienna_cancel_event is not None:
            self._vienna_cancel_event.set()
        self._vienna_input_revision += 1
        self.vienna_view.invalidate_result()
        if (isinstance(self.result, tuple) and self.result
                and isinstance(getattr(self.result[0], "config", None), ViennaControlLabConfig)):
            self.result = None

    def _cancel_vienna_analysis(self) -> None:
        self._invalidate_vienna_results()
        self.statusBar().showMessage("Vienna 计算已请求取消；旧结果已失效，等待后台释放。")

    def _invalidate_ttpl_results(self) -> None:
        """Drop every accepted TTPL consumer/export without cancelling Vienna."""
        self._ttpl_input_revision += 1
        self.result = None
        self.engineering_result = None
        workbench = self.control_lab_view
        control = workbench.control_lab
        control.invalidate_results()
        devices = workbench.device_loss_view
        devices.line_cycle = None
        devices.switched_cycle = None
        devices._last_comparison = None
        devices.hf_table.setRowCount(0)
        devices.slow_table.setRowCount(0)
        devices.hf_pick.clear()
        devices.slow_pick.clear()
        devices.export_button.setEnabled(False)
        devices.summary.setText(t("TTPL 输入已改变，请重新运行分析。"))
        devices.rollup_text.setPlainText(t("TTPL 输入已改变，请重新运行分析。"))
        cap = workbench.cap_thermal_view
        cap.line_cycle = cap.switched_cycle = cap.cap_result = cap.thermal_result = None
        cap.cap_export_button.setEnabled(False)
        cap.cap_summary.clear()
        cap.thermal_summary.clear()
        workbench.ac_performance_view.result = None
        workbench.ac_performance_view.summary.clear()
        switching = workbench.switching_validation_view
        switching.result = switching.switching = switching.engineering_v3 = None
        switching.summary.clear()
        exact = workbench.exact_hz_view
        exact.analysis = exact.handoff = None
        exact.set_busy(bool(self._active_workers))
        exact.status.setText(t("TTPL 输入已改变，请重新运行分析。"))
        for text in (exact.current_text, exact.voltage_text, exact.contract_text):
            text.clear()
        spice = workbench.ngspice_closed_loop_view
        spice.analysis = spice.result = spice.switched_cycle = None
        spice.run_button.setEnabled(False)
        spice.summary.clear()
        spice.status.setText(t("TTPL 输入已改变，请重新运行分析。"))
        workbench.v3_diagnostics_view.clear()
        # Clear plotted snapshots too; changing a display tab must not resurrect
        # the old Bode data or switching trajectory.
        for panel in (control.current_bode, control.voltage_bode, control.sense_bode):
            panel._disconnect_cursor()
            panel._cursor_frequency_hz = None
            panel._frequencies_hz = None
            panel._curves = []
            panel._margins = None
            panel._clear_selector_grid()
        for view in (control, workbench.ac_performance_view, switching, exact, spice):
            for canvas in view.findChildren(FigureCanvasQTAgg):
                canvas.figure.clear()
                canvas.draw_idle()
        self.statusBar().showMessage(t("TTPL 输入已改变，请重新运行分析。"))

    def _ttpl_context_current(self, config, revision) -> bool:
        if revision != self._ttpl_input_revision:
            return False
        try:
            return config == self.control_lab_view.control_lab._config()
        except (ValueError, TypeError):
            return False

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

        for label, callback in (("保存 TTPL 会话", self.save_ttpl_session),
                                ("打开 TTPL 会话", self.load_ttpl_session)):
            action = QAction(label, self)
            action.triggered.connect(callback)
            toolbar.addAction(action)

        action = QAction("TTPL Engineering", self)
        action.triggered.connect(lambda: self.subtabs.setCurrentIndex(0))
        toolbar.addAction(action)
        action = QAction("Vienna", self)
        action.triggered.connect(lambda: self.subtabs.setCurrentIndex(1))
        toolbar.addAction(action)
        action = QAction("Two-Level dq / PLL", self)
        action.triggered.connect(lambda: self.subtabs.setCurrentWidget(self.three_phase_view))
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

    def save_ttpl_session(self):
        path, _ = QFileDialog.getSaveFileName(self, "保存 TTPL 设计会话", "ttpl_session.json", "JSON (*.json)")
        if not path:
            return
        try:
            from llc_design.control.design_session import write_session

            from .solution_map_install import install_ttpl_solution_map
            install_ttpl_solution_map(self)
            write_session(self.control_lab_view.control_lab._session_capture(), path)
            self.statusBar().showMessage(f"TTPL session saved: {path}")
        except Exception as exc:
            show_operation_issue(self, t("保存失败"), str(exc), critical=True)

    def load_ttpl_session(self):
        path, _ = QFileDialog.getOpenFileName(self, "打开 TTPL 设计会话", "", "JSON (*.json)")
        if not path:
            return
        try:
            from llc_design.control.design_session import read_session
            from llc_design.gui.design_session_install import validate_map_state

            from .solution_map_install import install_ttpl_solution_map

            document = read_session(path)
            if document["workspace"] != "ttpl":
                raise ValueError("Choose a TTPL design session")
            view = install_ttpl_solution_map(self)
            prepared = validate_map_state(view, document.get("solution_map"), "ttpl")
            self.control_lab_view.control_lab._session_restore(document, prepared)
            self.subtabs.setCurrentIndex(0)
            self.statusBar().showMessage("TTPL inputs restored. Run analysis and regenerate candidate before Apply.")
        except Exception as exc:
            show_operation_issue(self, t("加载失败"), str(exc), critical=True)

    def _auto_check_update(self):
        check_for_updates(self, notify_up_to_date=False)

    def _apply_pfc_style(self):
        self.setStyleSheet(theme.workspace_stylesheet(theme.active_theme()))

    def _run_current(self):
        widget = self.subtabs.currentWidget()
        if hasattr(widget, "_request"):
            widget._request()
        elif widget is self.three_phase_view:
            widget.run_analysis()

    def closeEvent(self, event):
        maps = self.vienna_view.solution_map_view
        dq_active = bool(getattr(self.three_phase_view, "has_active_work",
                                 self.three_phase_view._worker is not None))
        if (self._active_workers or dq_active or maps.scan_running
                or self.three_phase_view.pll_comparison._worker is not None
                or self.vienna_view.pll_comparison._worker is not None):
            if maps.scan_running:
                maps.cancel_preview()
            if dq_active and hasattr(self.three_phase_view, "cancel_background_work"):
                self.three_phase_view.cancel_background_work()
            if self._vienna_cancel_event is not None:
                self._cancel_vienna_analysis()
            else:
                self.statusBar().showMessage("后台计算正在取消或等待结束；请完成后关闭。")
            event.ignore()
        else:
            super().closeEvent(event)

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
        if busy or message:
            self.statusBar().showMessage(message)

    def _run_worker(self, label, function, callback):
        self._worker_request_id += 1
        request_id = self._worker_request_id
        self.set_busy(True, label)
        worker = FunctionWorker(function)
        # The pool releases the runnable after run() returns; this registry
        # keeps its signals alive until their queued completion is handled.
        worker.setAutoDelete(True)
        self._active_workers.append(worker)
        def current():
            return (isValid(self) and worker in self._active_workers
                    and request_id == self._worker_request_id)
        worker.signals.result.connect(
            lambda result: callback(result) if current() else None)
        worker.signals.error.connect(
            lambda error: self._worker_error(error) if current() else None)
        worker.signals.finished.connect(lambda: self._worker_finished(worker, request_id))
        worker.start(self.thread_pool)

    def _worker_finished(self, worker, request_id):
        if worker not in self._active_workers:
            return
        self._active_workers.remove(worker)
        # End the signals -> callback -> worker cycle only after the job's
        # final emission. Queued callbacks also check registry membership.
        worker.signals.result.disconnect()
        worker.signals.error.disconnect()
        worker.signals.finished.disconnect()
        worker.signals.deleteLater()
        if not self._active_workers:
            self._vienna_cancel_event = None
        if isValid(self) and (request_id == self._worker_request_id or not self._active_workers):
            self.set_busy(False)

    def _worker_error(self, error):
        self.statusBar().showMessage("Calculation failed; no new result was accepted.")
        show_operation_issue(self, t("计算失败"), str(error), critical=True)

    def run_ttpl_analysis(self, config: PFCControlLabConfig):
        revision = self._ttpl_input_revision
        displayed_config = self.control_lab_view.control_lab._config()
        def calculate():
            engineering = build_pfc_engineering_v3(config)
            analysis = engineering.smart_control.analysis
            line = engineering.line_cycle.waveforms
            switching = build_pfc_switching_waveforms(
                config,
                line_cycle=line,
                line_angle_deg=config.power_stage.line_angle_deg,
            )
            return _TTPLCalculation((analysis, line, switching), engineering, revision, displayed_config)

        self._run_worker(
            "正在建立 TTPL 双环、AC 周期、过零与开关工作点…",
            calculate,
            self._ttpl_ready,
        )

    def _ttpl_ready(self, result):
        try:
            if isinstance(result, _TTPLCalculation):
                if result.input_revision is not None and not self._ttpl_context_current(
                        result.displayed_config, result.input_revision):
                    return
                engineering = result.engineering
                result = result.legacy_result
            else:
                # Keep legacy callers working without repeating their simulation.
                engineering = build_pfc_engineering_v3(
                    result[0].config, analysis=result[0], waveforms=result[1],
                )
            self.result = result
            self.engineering_result = engineering
            # Control/Bode remains the owner of controller/sensing settings.
            # AC/switching, exact H(z) and shared-ngspice all consume this exact
            # analysis result; no downstream stage recreates the controller.
            self.control_lab_view.set_result(result, engineering_v3=engineering)
            if hasattr(self.control_lab_view, "device_loss_view"):
                self.control_lab_view.device_loss_view.set_line_cycle(engineering.line_cycle)
            if hasattr(self.control_lab_view, "cap_thermal_view"):
                self.control_lab_view.cap_thermal_view.set_line_cycle(engineering.line_cycle)
            self.control_lab_view.ac_performance_view.set_result(result, engineering_v3=engineering)
            self.control_lab_view.switching_validation_view.set_result(result, engineering_v3=engineering)
            analysis, line, _ = result
            self.control_lab_view.exact_hz_view.set_analysis(analysis)
            self.control_lab_view.ngspice_closed_loop_view.set_analysis(analysis)
            show_v3_diagnostics(self.control_lab_view, engineering)
            current = analysis.current_loop.margins
            voltage = analysis.voltage_loop.margins
            self.statusBar().showMessage(
                f"TTPL 完成: Li fc={current.critical_gain_crossover_hz}, "
                f"PM={current.phase_margin_deg}; Lv fc={voltage.critical_gain_crossover_hz}, "
                f"PM={voltage.phase_margin_deg}; PF={engineering.pf_thd.pf:.6g}, "
                f"THD={engineering.pf_thd.thd_percent:.5g}%; "
                f"V3: {engineering.line_cycle.convergence.status.value}; "
                f"{engineering.pf_thd.status.value} (software model only)"
            )
        except Exception:
            self._worker_error("TTPL 结果绘图/GUI 更新失败\n" + traceback.format_exc())

    def _on_v3_zero_cross_changed(self, zero_crossing) -> None:
        if self.engineering_result is None:
            return
        self.engineering_result = replace(
            self.engineering_result, zero_crossing=zero_crossing,
        )
        show_v3_diagnostics(self.control_lab_view, self.engineering_result)

    def _invalidate_switched_consumers(self) -> None:
        workbench = self.control_lab_view
        workbench.device_loss_view.set_switched_cycle(None)
        workbench.cap_thermal_view.set_switched_cycle(None)

    def run_ttpl_shared_ngspice(self, analysis, scenario, simulation) -> None:
        """Run the circuit-level TTPL path from the frozen exact-H(z) contract."""
        spice_view = self.control_lab_view.ngspice_closed_loop_view
        simulation_revision = spice_view.input_revision
        self._invalidate_switched_consumers()
        revision = self._ttpl_input_revision
        displayed_config = self.control_lab_view.control_lab._config()
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
            lambda result: self._ttpl_ngspice_ready(result)
            if self._ttpl_context_current(displayed_config, revision)
            and simulation_revision == spice_view.input_revision
            and result.config == analysis.config else None,
        )

    def _ttpl_ngspice_ready(self, result) -> None:
        try:
            self.control_lab_view.ngspice_closed_loop_view.set_simulation_result(result)
            source = self.control_lab_view.ngspice_closed_loop_view.switched_cycle
            self.control_lab_view.device_loss_view.set_switched_cycle(source)
            self.control_lab_view.cap_thermal_view.set_switched_cycle(source)
            m = result.metrics
            self.statusBar().showMessage(
                f"TTPL shared-ngspice 完成: Vbus={m.final_bus_voltage_v:.3f} V, "
                f"Ipk={m.peak_inductor_current_a:.3f} A, "
                f"duty={m.duty_min:.4f}..{m.duty_max:.4f}"
            )
        except Exception:
            self._invalidate_switched_consumers()
            self._worker_error("TTPL shared-ngspice 结果绘图/GUI 更新失败\n" + traceback.format_exc())

    def run_vienna_analysis(self, config: ViennaControlLabConfig):
        self._invalidate_vienna_results()
        revision = self._vienna_input_revision
        displayed_config = self.vienna_view._config()
        cancelled = Event()
        self._vienna_cancel_event = cancelled
        def calculate():
            if cancelled.is_set():
                return None
            try:
                analysis = build_vienna_control_lab_analysis(config)
                validate_vienna_analysis(analysis)
            except Exception as exc:
                raise RuntimeError(f"Vienna 小信号/Bode 建模失败: {exc}") from exc
            try:
                line = simulate_vienna_line_cycle(config, cancelled=cancelled.is_set)
                validate_vienna_line_cycle(line)
            except ViennaAnalysisCancelled:
                return None
            except Exception as exc:
                raise RuntimeError(f"Vienna 三相 AC 周期求解失败: {exc}") from exc
            if cancelled.is_set():
                return None
            try:
                switching = build_vienna_switching_waveforms(
                    config, line, line_angle_deg=config.switching_line_angle_deg
                )
                validate_vienna_switching(switching)
            except Exception as exc:
                raise RuntimeError(f"Vienna 开关工作点重建失败: {exc}") from exc
            if cancelled.is_set():
                return None
            return _ViennaCalculation((analysis, line, switching), revision, displayed_config)

        self._run_worker(
            "正在建立 Vienna ABC 双环、中点平衡、三相 AC 周期与 Sector…",
            calculate,
            self._vienna_ready,
        )

    def _vienna_ready(self, result):
        if result is None:
            return False
        try:
            if isinstance(result, _ViennaCalculation):
                if (result.input_revision != self._vienna_input_revision
                        or result.displayed_config != self.vienna_view._config()):
                    return False
                result = result.legacy_result
            # Reject an obsolete payload without clearing a newer valid result.
            if result[0].config != self.vienna_view._config():
                return False
            if not self.vienna_view.set_result(result):
                return False
            self.result = result
            analysis, line, _ = result
            current = analysis.current_loop.margins
            voltage = analysis.voltage_loop.margins
            balance = analysis.balance_loop.margins
            self.statusBar().showMessage(
                f"Vienna 完成: Li PM={current.phase_margin_deg}; "
                f"Lv PM={voltage.phase_margin_deg}; Balance PM={balance.phase_margin_deg}; "
                f"PF={line.metrics.overall_power_factor:.6g}"
            )
            return True
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

"""Dedicated two-level dq / PLL workspace inside the PFC window."""

from __future__ import annotations

import json
from dataclasses import asdict, replace
from threading import Event

import numpy as np
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg, NavigationToolbar2QT
from matplotlib.figure import Figure
from PySide6.QtCore import QSignalBlocker, QThreadPool
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)
from shiboken6 import isValid

from llc_design.gui.workers import FunctionWorker
from pfc_design.three_phase.control import build_three_phase_loops, request_identity
from pfc_design.three_phase.exact_control import ExactController, SampledSense
from pfc_design.three_phase.model import (
    ThreePhaseConfig,
    ThreePhaseResult,
    export_result,
    simulate_three_phase,
    switching_snapshot,
)
from pfc_design.three_phase.workspace import load_session, save_session

PARAMETERS = {
    "line_line_rms_v": ("线电压 RMS (V)", 1, 1, 5000),
    "grid_hz": ("电网频率 (Hz)", 1, 10, 400),
    "nominal_hz": ("PLL 标称频率 (Hz)", 1, 10, 400),
    "bus_voltage_v": ("母线给定 (V)", 1, 1, 10000),
    "output_power_w": ("负载功率 (kW)", 1e-3, 0, 1000),
    "inductance_h": ("每相电感 (µH)", 1e6, 0.1, 100000),
    "resistance_ohm": ("每相电阻 (mΩ)", 1e3, 0, 10000),
    "bus_capacitance_f": ("母线总电容 (µF)", 1e6, 1, 1000000),
    "control_hz": ("电流/PLL/PWM (kHz)", 1e-3, 1, 200),
    "voltage_loop_hz": ("电压环 (kHz)", 1e-3, 0.01, 100),
    "analysis_min_hz": ("Bode 起点 (Hz)", 1, 0.00001, 100000),
    "analysis_max_hz": ("Bode 请求终点 (Hz)", 1, 0.001, 1e7),
    "current_kp": ("电流 Kp (V/A)", 1, 0, 1000),
    "current_ki": ("电流 Ki (V/A/s)", 1, 0, 100000),
    "voltage_kp": ("电压 Kp (A/V)", 1, 0, 100),
    "voltage_ki": ("电压 Ki (A/V/s)", 1, 0, 10000),
    "current_limit_a": ("dq 矢量电流上限 (A)", 1, 0.1, 10000),
    "iq_reference_a": ("Iq 给定 (A)", 1, -10000, 10000),
    "current_slew_a_per_s": ("Id 斜率 (A/s)", 1, 1, 1000000),
    "modulation_limit": ("线性调制上限", 1, 0.01, 1),
    "pll_bandwidth_hz": ("PLL 自然频率 (Hz)", 1, 0.1, 500),
    "pll_damping": ("PLL 阻尼比", 1, 0.01, 5),
    "pll_min_hz": ("PLL 最低频率 (Hz)", 1, 1, 400),
    "pll_max_hz": ("PLL 最高频率 (Hz)", 1, 1, 500),
    "pll_initial_deg": ("PLL 初始相位 (deg)", 1, -180, 180),
    "pll_lock_error": ("锁定 |Vq|/|V| 门限", 1, 0.0001, 0.5),
    "pll_lock_time_s": ("锁定持续时间 (ms)", 1e3, 0.1, 1000),
    "duration_s": ("仿真时长 (s)", 1, 0.06, 5),
    "load_ramp_s": ("初始负载爬升 (ms)", 1e3, 1, 1000),
    "disturbance_time_s": ("扰动时刻 (s)", 1, 0, 5),
    "grid_after_hz": ("扰动后电网频率 (Hz)", 1, 10, 400),
    "phase_step_deg": ("电网相位跳变 (deg)", 1, -180, 180),
    "load_step_ratio": ("扰动后负载倍数", 1, 0, 3),
}


class EngineeringSpinBox(QDoubleSpinBox):
    """Keep replay precision without filling the parameter panel with zeroes."""

    def textFromValue(self, value):
        return f"{value:.12f}".rstrip("0").rstrip(".") or "0"


class ThreePhaseDQView(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.result: ThreePhaseResult | None = None
        self._worker = None
        self._revision = 0
        self._cancel = Event()
        self._closed = False
        self._base_config = ThreePhaseConfig()
        self.retained = ()
        self._exact = {"current": None, "voltage": None}
        self._widget_baseline = {}
        root = QVBoxLayout(self)
        title = QLabel("三相两电平 PFC · 六开关 · dq 双环 · 可选 PLL · SVPWM")
        title.setStyleSheet("font-size:18px;font-weight:650")
        root.addWidget(title)
        note = QLabel(
            "电流正方向：电网→直流母线；d 轴对齐电网电压，Iq=0 为单位功率因数目标。"
            "预充母线平均模型；不含器件损耗、死区和硬件保护。"
        )
        note.setWordWrap(True)
        root.addWidget(note)
        splitter = QSplitter()
        root.addWidget(splitter, 1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        panel = QWidget()
        form = QFormLayout(panel)
        self.inputs = {}
        for key, (label, scale, low, high) in PARAMETERS.items():
            w = EngineeringSpinBox()
            w.setRange(low, high)
            w.setDecimals(12)
            w.setValue(getattr(ThreePhaseConfig(), key) * scale)
            w.valueChanged.connect(self._changed)
            self.inputs[key] = w
            form.addRow(label, w)
        for key, label, low, high in (
            ("pwm_delay_ticks", "PWM 延迟拍数", 0, 5),
            ("voltage_delay_ticks", "电压命令延迟拍数", 0, 5),
            ("analysis_points", "每环 Bode 样本数", 100, 10000),
            ("integration_substeps", "每拍积分子步", 2, 32),
        ):
            w = QSpinBox()
            w.setRange(low, high)
            w.setValue(getattr(ThreePhaseConfig(), key))
            w.valueChanged.connect(self._changed)
            self.inputs[key] = w
            form.addRow(label, w)
        self.pll_kind = QComboBox()
        self.pll_kind.addItems(["SRF", "DDSRF", "DDSOGI"])
        self.pll_kind.currentTextChanged.connect(self._changed)
        form.addRow("PLL 算法", self.pll_kind)
        self.load_model = QComboBox()
        self.load_model.addItems(["constant_power", "resistance"])
        self.load_model.currentTextChanged.connect(self._changed)
        form.addRow("负载模型", self.load_model)
        self.sense_inputs = {}
        for loop, label in (("current", "dq 电流"), ("voltage", "母线")):
            self.sense_inputs[loop] = {}
            for key, title, high in (
                ("gain", "校准增益", 10.0),
                ("pole_hz", "离散滤波极点 Hz (0=无)", 1e6),
                ("delay_ticks", "采样延迟拍数", 5),
                ("adc_v_per_unit", "ADC V/工程单位 (0=未知)", 100.0),
                ("adc_bias_v", "ADC 偏置 V", 100.0),
                ("adc_reference_v", "ADC 参考 V", 100.0),
            ):
                widget = QSpinBox() if key == "delay_ticks" else EngineeringSpinBox()
                widget.setRange(0, high)
                if isinstance(widget, QDoubleSpinBox):
                    widget.setDecimals(12)
                widget.setValue(
                    getattr(getattr(self._base_config, loop + "_sense"), key)
                )
                widget.valueChanged.connect(self._changed)
                self.sense_inputs[loop][key] = widget
                form.addRow(label + " " + title, widget)
        self.run_button = QPushButton("运行 dq / PLL 仿真")
        self.run_button.clicked.connect(self.run_analysis)
        self.cancel_button = QPushButton("取消运行")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self.cancel_run)
        self.local_button = QPushButton("只计算局部环路")
        self.local_button.clicked.connect(self.run_local_analysis)
        self.save_button = QPushButton("保存完整会话 JSON")
        self.load_button = QPushButton("打开完整会话 JSON")
        self.export_button = QPushButton("导出波形 CSV + 指标 JSON")
        self.save_button.clicked.connect(self.save_project)
        self.load_button.clicked.connect(self.load_project)
        self.export_button.clicked.connect(self.export_results)
        self.export_button.setEnabled(False)
        actions = QHBoxLayout()
        for button in (
            self.run_button,
            self.cancel_button,
            self.local_button,
            self.save_button,
            self.load_button,
            self.export_button,
        ):
            actions.addWidget(button)
        actions.addStretch(1)
        root.insertLayout(2, actions)
        scroll.setWidget(panel)
        splitter.addWidget(scroll)
        self.tabs = QTabWidget()
        splitter.addWidget(self.tabs)
        splitter.setSizes([350, 1050])
        self.summary = QPlainTextEdit()
        self.summary.setReadOnly(True)
        self.summary.setPlainText(
            "默认：400 V 线电压、700 V 母线、10 kW；先运行，再查看各控制环响应。"
        )
        self.tabs.addTab(self.summary, "总览 / 模型边界")
        self.figures, self.canvases = {}, {}
        for title in (
            "三相电网 / 电流",
            "dq 电流环",
            "PLL 锁相",
            "母线 / 能量",
            "SVPWM 六路",
        ):
            page = QWidget()
            layout = QVBoxLayout(page)
            fig = Figure(figsize=(10, 7), layout="constrained")
            canvas = FigureCanvasQTAgg(fig)
            layout.addWidget(NavigationToolbar2QT(canvas, self))
            layout.addWidget(canvas)
            self.figures[title], self.canvases[title] = fig, canvas
            self.tabs.addTab(page, title)
        from .pll_comparison_view import PLLComparisonView

        self.pll_comparison = PLLComparisonView()
        # Only the parameter sidebar scrolls. A Matplotlib canvas nested inside
        # an outer QScrollArea can crash during native Qt paint on tab switches.
        pll_layout = self.pll_comparison.layout()
        items = []
        while pll_layout.count():
            items.append(pll_layout.takeAt(0))
        pll_parameters = QWidget()
        pll_parameters_layout = QVBoxLayout(pll_parameters)
        for item in items:
            if item.widget() is self.pll_comparison.tabs:
                continue
            if item.widget() is not None:
                pll_parameters_layout.addWidget(item.widget())
            elif item.layout() is not None:
                pll_parameters_layout.addLayout(item.layout())
        pll_scroll = QScrollArea()
        pll_scroll.setWidgetResizable(True)
        pll_scroll.setWidget(pll_parameters)
        pll_splitter = QSplitter()
        pll_splitter.addWidget(pll_scroll)
        pll_splitter.addWidget(self.pll_comparison.tabs)
        pll_splitter.setSizes([350, 650])
        pll_layout.addWidget(pll_splitter)
        self.tabs.addTab(self.pll_comparison, "三种 PLL 对比 / C99")
        from .three_phase_loop_view import ThreePhaseLoopView
        from .three_phase_performance_view import ThreePhasePerformanceView

        self.performance = ThreePhasePerformanceView()
        self.loop_view = ThreePhaseLoopView()
        self.loop_view.controllerApplied.connect(self._receive_controller)
        self.loop_view.nativeRequested.connect(self.restore_native_controller)
        self.loop_view.retainRequested.connect(self.retain_operating_point)
        self.loop_view.clearRetainedRequested.connect(self.clear_retained_points)
        self.tabs.addTab(self.performance, "电流应力 / 损耗 / PF")
        self.tabs.addTab(self.loop_view, "控制框图 / 双环稳定性")
        self.status = QLabel("准备就绪")
        root.addWidget(self.status)
        self._capture_widget_baseline()
        self.loop_view.set_context(self._base_config, self.retained)
        cancellation = self._cancel
        self.destroyed.connect(lambda: cancellation.set())

    def _capture_widget_baseline(self):
        self._widget_baseline = {
            key: widget.value() for key, widget in self.inputs.items()
        }
        self._sense_baseline = {
            loop: {key: widget.value() for key, widget in inputs.items()}
            for loop, inputs in self.sense_inputs.items()
        }

    def _changed(self, *_):
        self._revision += 1
        self._cancel.set()
        self.result = None
        if hasattr(self, "export_button"):
            self.export_button.setEnabled(False)
        if hasattr(self, "loop_view"):
            self.loop_view.invalidate(
                "输入已更改；旧结果、候选和导出已失效，请重新运行。"
            )
            try:
                self.loop_view.set_context(self.config_from_widgets(), self.retained)
            except (ValueError, TypeError):
                self.loop_view.config = None
        if hasattr(self, "status"):
            self.status.setText("输入已更改；请重新运行。")

    def config_from_widgets(self):
        values = {}
        for key, widget in self.inputs.items():
            if widget.value() == self._widget_baseline.get(key):
                values[key] = getattr(self._base_config, key)
            else:
                values[key] = (
                    widget.value() / PARAMETERS[key][1]
                    if key in PARAMETERS
                    else widget.value()
                )
        for loop in ("current", "voltage"):
            original = getattr(self._base_config, loop + "_sense")
            values[loop + "_sense"] = SampledSense(
                **{
                    key: getattr(original, key)
                    if widget.value() == self._sense_baseline[loop][key]
                    else widget.value()
                    for key, widget in self.sense_inputs[loop].items()
                }
            )
            values[loop + "_controller"] = self._exact[loop]
        config = replace(
            self._base_config,
            **values,
            pll_kind=self.pll_kind.currentText(),
            load_model=self.load_model.currentText(),
        )
        config.validate()
        return config

    def _receive_controller(self, loop, controller):
        try:
            self.import_controller(loop, controller)
        except (ValueError, TypeError) as exc:
            self._error(str(exc))

    def import_controller(self, loop, controller, source_identity=None):
        if loop not in ("current", "voltage"):
            raise ValueError("unknown dq loop")
        if not isinstance(controller, ExactController):
            controller = ExactController(
                tuple(controller.b),
                tuple(controller.a),
                controller.sample_rate_hz,
                source_identity
                or getattr(controller, "source", "")
                or "external exact H(z)",
            )
        config = replace(
            self.config_from_widgets(), **{loop + "_controller": controller}
        )
        config.validate()
        self._exact[loop] = controller
        for suffix in ("kp", "ki"):
            self.inputs[loop + "_" + suffix].setEnabled(False)
        self._changed()
        self.status.setText(
            f"已导入 {loop} 精确 H(z)；原始 b/a 已保留，重新运行后可导出。"
        )

    def restore_native_controller(self, loop):
        self._exact[loop] = None
        for suffix in ("kp", "ki"):
            self.inputs[loop + "_" + suffix].setEnabled(True)
        self._changed()

    def retain_operating_point(self):
        try:
            config = self.config_from_widgets()
            if len(self.retained) >= 16:
                raise ValueError("最多保留 16 个工况")
            self.retained = self.retained + (config,)
            self.loop_view.set_context(config, self.retained)
            self.loop_view.clear_candidates()
            self.loop_view.candidate_details.setPlainText(
                json.dumps(
                    [asdict(c) for c in self.retained], indent=2, ensure_ascii=False
                )
            )
            self.status.setText(
                f"已保留 {len(self.retained)} 个工况；扫描保持同一精确控制器。"
            )
        except (ValueError, TypeError) as exc:
            self._error(str(exc))

    def clear_retained_points(self):
        self.retained = ()
        self.loop_view.retained = ()
        self.loop_view.clear_candidates()
        self.loop_view.candidate_details.setPlainText("保留工况已清空。")

    def run_local_analysis(self):
        try:
            config = self.config_from_widgets()
            analysis = build_three_phase_loops(config)
            self.loop_view.set_context(config, self.retained)
            self.loop_view.set_analysis(analysis)
            self.tabs.setCurrentWidget(self.loop_view)
            self.status.setText("局部双环完成；非线性 dq / PLL 仿真仍需单独运行。")
        except (ValueError, TypeError) as exc:
            self._error(str(exc))

    def cancel_run(self):
        self._revision += 1
        self._cancel.set()
        self.status.setText("正在取消；旧任务不会写入结果。")

    def run_analysis(self):
        if self._worker is not None:
            return
        try:
            config = self.config_from_widgets()
        except (ValueError, TypeError) as exc:
            self._error(str(exc))
            return
        self.run_button.setEnabled(False)
        self.status.setText("正在计算三相电流、母线能量和离散控制…")
        revision = self._revision
        self._cancel = Event()
        cancellation = self._cancel
        self.destroyed.connect(lambda: cancellation.set())
        self.cancel_button.setEnabled(True)
        worker = FunctionWorker(simulate_three_phase, config, cancel_event=cancellation)
        self._worker = worker

        def current():
            return (
                isValid(self)
                and not self._closed
                and self._worker is worker
                and revision == self._revision
                and not cancellation.is_set()
            )

        worker.signals.result.connect(
            lambda result: self.set_result(result) if current() else None
        )
        worker.signals.error.connect(
            lambda error: self._error(error) if current() else None
        )
        worker.signals.finished.connect(lambda: self._finished(worker))
        worker.start(QThreadPool.globalInstance())

    def _finished(self, worker):
        if self._worker is not worker:
            return
        self._worker = None
        worker.signals.deleteLater()
        if not isValid(self):
            return
        self.run_button.setEnabled(True)
        self.cancel_button.setEnabled(False)
        if self.status.text().startswith("正在取消"):
            self.status.setText("已取消；未生成新结果。")

    def _error(self, message):
        self.status.setText("计算/文件操作失败；未生成新结果")
        QMessageBox.warning(self, "三相两电平 PFC", message)

    def set_result(self, result: ThreePhaseResult):
        if request_identity(self.config_from_widgets()) != request_identity(
            result.config
        ):
            return
        self.result = result
        self.loop_view.set_context(result.config, self.retained)
        self.performance.set_two_level(result)
        self.loop_view.set_result(result)
        self.summary.setPlainText(
            f"SOFTWARE_MODEL_ONLY · dq current control + {result.config.pll_kind} PLL + two-level SVPWM\n\n"
            + json.dumps(result.metrics, indent=2, ensure_ascii=False)
            + "\n\n"
            + "\n".join(result.warnings)
            + "\n\n控制约定：\n"
            "vd*=vd_grid + ωL·iq − PI_d；vq*=vq_grid − ωL·id − PI_q。\n"
            "正 Id 向母线送入有功；电压 H(z) 输出采样反馈域 Id 给定，带矢量限幅及 Id 斜率限制。\n"
            "dq 图分别列出物理 Id/Iq、采样反馈和参考；跟踪误差比较参考与采样反馈。母线参考线为 Vref / sense gain。\n"
            "PLL 使用实际三相电压的 Vq/|V|，含 PI、频率限幅和连续锁定资格。\n"
            "PLL 丢锁时 Id 目标降至 0（按斜率下降）、Iq 给定归零；此模型不实现硬件关断。\n"
            "积分对象为 abc 电流与 ½CV² 母线能量；控制器不能读取理想电网角度。\n"
            "PWM 页面为末工况理想互补门极重构，不含死区，不是实际驱动代码。"
        )
        s, t = result.signals, result.time_s * 1e3

        def axes(title, count):
            fig = self.figures[title]
            fig.clear()
            return np.atleast_1d(fig.subplots(count, 1))

        ax = axes("三相电网 / 电流", 2)
        for p in "abc":
            ax[0].plot(t, s[f"vac_{p}"], label=f"V{p}")
            ax[1].plot(t, s[f"current_{p}"], label=f"I{p}")
        ax[0].set_ylabel("V")
        ax[1].set_ylabel("A")
        ax = axes("dq 电流环", 2)
        for j, keys in enumerate(
            (
                ("id_a", "id_feedback_a", "id_ref_a"),
                ("iq_a", "iq_feedback_a", "iq_ref_a"),
            )
        ):
            for key in keys:
                ax[j].plot(t, s[key], label=key)
            ax[j].set_ylabel("A")
        ax = axes("PLL 锁相", 3)
        ax[0].plot(t, s["pll_hz"], label="PLL frequency")
        ax[0].set_ylabel("Hz")
        error = np.angle(np.exp(1j * (s["grid_theta_rad"] - s["pll_theta_rad"])))
        ax[1].plot(t, np.degrees(error), label="Phase error (diagnostic truth only)")
        ax[1].set_ylabel("deg")
        ax[2].plot(t, s["pll_error"], label="Vq / |V|")
        ax[2].plot(t, s["pll_locked"], label="Qualified lock")
        ax[2].set_ylabel("pu / flag")
        ax = axes("母线 / 能量", 3)
        ax[0].plot(t, s["vbus_v"], label="Vbus")
        ax[0].axhline(
            result.metrics["vbus_regulated_target_v"],
            linestyle="--",
            label="Physical target: Vref / sense gain",
        )
        ax[0].set_ylabel("V")
        for key in ("pin_w", "pconv_w", "pload_w"):
            ax[1].plot(t, s[key], label=key)
        ax[1].set_ylabel("W")
        ax[2].plot(t, s["energy_balance_error_j"], label="Energy balance residual")
        ax[2].set_ylabel("J")
        ax = axes("SVPWM 六路", 3)
        for p in "abc":
            ax[0].plot(t, s[f"duty_{p}"], label=f"Duty {p}")
        ax[0].plot(t, s["modulation_limited"], "--", label="Command limited")
        ax[0].set_ylabel("Duty / flag")
        ts, high, low, pole, _phase = switching_snapshot(result)
        for j, p in enumerate("abc"):
            ax[1].step(ts * 1e6, high[j] + j * 3, where="post", label=f"{p} upper")
            ax[1].step(
                ts * 1e6,
                low[j] + j * 3,
                where="post",
                linestyle="--",
                label=f"{p} lower",
            )
            ax[2].step(ts * 1e6, pole[j], where="post", label=f"Pole {p}")
        ax[1].set_ylabel("Ideal gates (offset)")
        ax[2].set_ylabel("Pole V (two levels)")
        for title, fig in self.figures.items():
            for a in fig.axes:
                a.grid(True, alpha=0.25)
                a.legend(fontsize=8, ncol=2)
                a.set_xlabel("Time (ms)")
            if title == "SVPWM 六路":
                fig.axes[1].set_xlabel("Local time (us)")
                fig.axes[2].set_xlabel("Local time (us)")
            self.canvases[title].draw_idle()
        self.export_button.setEnabled(True)
        self.status.setText(
            "完成；"
            + (
                "末周期满足数值收敛检查"
                if result.metrics["settled"]
                else "末周期尚未收敛"
            )
        )
        try:
            if self.config_from_widgets() != result.config:
                self._changed()
        except ValueError:
            self._changed()

    def save_project(self):
        try:
            config = self.config_from_widgets()
            name, _ = QFileDialog.getSaveFileName(
                self, "保存三相 dq 完整会话", "three-phase-dq.json", "JSON (*.json)"
            )
            if name:
                save_session(name, config, self.retained, self.loop_view.targets())
        except (ValueError, OSError, TypeError) as exc:
            self._error(str(exc))

    def load_project(self):
        name, _ = QFileDialog.getOpenFileName(
            self, "打开三相 dq 完整会话", "", "JSON (*.json)"
        )
        if not name:
            return
        try:
            config, retained, targets = load_session(name)
            self.apply_loaded_session(config, retained, targets)
        except (ValueError, OSError, KeyError, TypeError) as exc:
            self._error(str(exc))

    def apply_loaded_session(self, config, retained=(), targets=None):
        # Validate every field/range before modifying any widget or active source.
        config.validate()
        all_values = []
        for key, widget in self.inputs.items():
            value = getattr(config, key) * (
                PARAMETERS[key][1] if key in PARAMETERS else 1
            )
            all_values.append((widget, value, key))
        for loop, inputs in self.sense_inputs.items():
            for key, widget in inputs.items():
                all_values.append(
                    (
                        widget,
                        getattr(getattr(config, loop + "_sense"), key),
                        loop + "_sense." + key,
                    )
                )
        if len(retained) > 16:
            raise ValueError("too many retained operating points")
        for point in retained:
            point.validate()
        for widget, value, key in all_values:
            if not widget.minimum() <= value <= widget.maximum():
                raise ValueError(
                    key + " exceeds desktop input range; values were not changed"
                )
        targets = targets or {}
        for key, widget in (
            ("fc_hz", self.loop_view.fc),
            ("pm_deg", self.loop_view.pm),
        ):
            if (
                key in targets
                and not widget.minimum() <= targets[key] <= widget.maximum()
            ):
                raise ValueError("target exceeds desktop input range")
        blockers = [QSignalBlocker(widget) for widget, _, _ in all_values] + [
            QSignalBlocker(self.pll_kind),
            QSignalBlocker(self.load_model),
        ]
        for widget, value, key in all_values:
            widget.setValue(value)
        self.pll_kind.setCurrentText(config.pll_kind)
        self.load_model.setCurrentText(config.load_model)
        self._base_config = config
        self._exact = {
            loop: getattr(config, loop + "_controller")
            for loop in ("current", "voltage")
        }
        self.retained = tuple(retained)
        self._capture_widget_baseline()
        del blockers
        for loop in ("current", "voltage"):
            for suffix in ("kp", "ki"):
                self.inputs[loop + "_" + suffix].setEnabled(self._exact[loop] is None)
        if targets:
            self.loop_view.loop_selector.setCurrentText(targets["loop"])
            self.loop_view.fc.setValue(targets["fc_hz"])
            self.loop_view.pm.setValue(targets["pm_deg"])
        self._changed()
        self.status.setText("完整会话已打开；需重新计算，历史结果未作为当前认证。")

    def export_results(self):
        if self.result is None:
            return
        if request_identity(self.config_from_widgets()) != self.result.metrics.get(
            "source_identity"
        ):
            self._changed()
            return
        name, _ = QFileDialog.getSaveFileName(
            self, "导出当前运行快照", "three-phase-dq.csv", "CSV (*.csv)"
        )
        if name:
            try:
                export_result(self.result, name)
            except OSError as exc:
                self._error(str(exc))

    @property
    def has_active_work(self):
        return self._worker is not None or self.loop_view._worker is not None

    def cancel_background_work(self):
        """Host-window close hook: cancel computation and invalidate pending callbacks."""
        self._revision += 1
        self._cancel.set()
        self.loop_view.invalidate("工作区任务已取消。")

    def closeEvent(self, event):
        self._closed = True
        self.cancel_background_work()
        super().closeEvent(event)

    def showEvent(self, event):
        self._closed = False
        super().showEvent(event)

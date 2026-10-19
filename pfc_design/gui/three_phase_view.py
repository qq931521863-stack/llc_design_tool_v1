"""Dedicated two-level dq / PLL workspace inside the PFC window."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

import numpy as np
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg, NavigationToolbar2QT
from matplotlib.figure import Figure
from PySide6.QtCore import QThreadPool
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
from pfc_design.three_phase.model import (
    ThreePhaseConfig,
    ThreePhaseResult,
    export_result,
    simulate_three_phase,
    switching_snapshot,
)

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


class ThreePhaseDQView(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.result: ThreePhaseResult | None = None
        self._worker = None
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
            w = QDoubleSpinBox()
            w.setRange(low, high)
            w.setDecimals(5)
            w.setValue(getattr(ThreePhaseConfig(), key) * scale)
            w.valueChanged.connect(self._changed)
            self.inputs[key] = w
            form.addRow(label, w)
        for key, label, low, high in (
            ("pwm_delay_ticks", "PWM 延迟拍数", 0, 5),
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
        self.run_button = QPushButton("运行 dq / PLL 仿真")
        self.run_button.clicked.connect(self.run_analysis)
        self.save_button = QPushButton("保存参数 JSON")
        self.load_button = QPushButton("加载参数 JSON")
        self.export_button = QPushButton("导出波形 CSV + 指标 JSON")
        self.save_button.clicked.connect(self.save_project)
        self.load_button.clicked.connect(self.load_project)
        self.export_button.clicked.connect(self.export_results)
        self.export_button.setEnabled(False)
        actions = QHBoxLayout()
        for button in (
            self.run_button,
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
        self.tabs.addTab(self.pll_comparison, "三种 PLL 对比 / C99")
        from .three_phase_loop_view import ThreePhaseLoopView
        from .three_phase_performance_view import ThreePhasePerformanceView
        self.performance = ThreePhasePerformanceView()
        self.loop_view = ThreePhaseLoopView()
        self.tabs.addTab(self.performance, "电流应力 / 损耗 / PF")
        self.tabs.addTab(self.loop_view, "控制框图 / 双环稳定性")
        self.status = QLabel("准备就绪")
        root.addWidget(self.status)

    def _changed(self, *_):
        if self.result is not None:
            self.status.setText("参数已更改；显示与导出仍为上次运行快照，请重新运行。")

    def config_from_widgets(self):
        values = {
            k: w.value() / PARAMETERS[k][1] if k in PARAMETERS else w.value()
            for k, w in self.inputs.items()
        }
        config = ThreePhaseConfig(**values, pll_kind=self.pll_kind.currentText())
        config.validate()
        return config

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
        worker = FunctionWorker(simulate_three_phase, config)
        self._worker = worker
        def current():
            return isValid(self) and self._worker is worker
        worker.signals.result.connect(lambda result: self.set_result(result) if current() else None)
        worker.signals.error.connect(lambda error: self._error(error) if current() else None)
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

    def _error(self, message):
        self.status.setText("计算/文件操作失败；未生成新结果")
        QMessageBox.warning(self, "三相两电平 PFC", message)

    def set_result(self, result: ThreePhaseResult):
        self.result = result
        self.performance.set_two_level(result)
        self.loop_view.set_result(result)
        self.summary.setPlainText(
            f"SOFTWARE_MODEL_ONLY · dq current control + {result.config.pll_kind} PLL + two-level SVPWM\n\n"
            + json.dumps(result.metrics, indent=2, ensure_ascii=False)
            + "\n\n"
            + "\n".join(result.warnings)
            + "\n\n控制约定：\n"
            "vd*=vd_grid + ωL·iq − PI_d；vq*=vq_grid − ωL·id − PI_q。\n"
            "正 Id 向母线送入有功；电压 PI 输出 Id 给定，带矢量限幅及 Id 斜率限制。\n"
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
        for j, keys in enumerate((("id_a", "id_ref_a"), ("iq_a", "iq_ref_a"))):
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
        ax[0].axhline(result.config.bus_voltage_v, linestyle="--", label="Reference")
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
                self, "保存三相 dq 工程", "three-phase-dq.json", "JSON (*.json)"
            )
            if name:
                Path(name).write_text(
                    json.dumps(
                        {"schema": "three-phase-dq-v1", "config": asdict(config)},
                        indent=2,
                        allow_nan=False,
                    )
                    + "\n",
                    encoding="utf-8",
                )
        except (ValueError, OSError) as exc:
            self._error(str(exc))

    def load_project(self):
        name, _ = QFileDialog.getOpenFileName(
            self, "加载三相 dq 工程", "", "JSON (*.json)"
        )
        if not name:
            return
        try:
            data = json.loads(Path(name).read_text(encoding="utf-8"))
            if data["schema"] != "three-phase-dq-v1":
                raise ValueError("not a three-phase-dq-v1 project")
            config = ThreePhaseConfig(**data["config"])
            config.validate()
            self.pll_kind.setCurrentText(config.pll_kind)
            for key, w in self.inputs.items():
                val = getattr(config, key) * (
                    PARAMETERS[key][1] if key in PARAMETERS else 1
                )
                if not w.minimum() <= val <= w.maximum():
                    raise ValueError(f"{key} exceeds desktop input range")
            for key, w in self.inputs.items():
                w.setValue(
                    getattr(config, key)
                    * (PARAMETERS[key][1] if key in PARAMETERS else 1)
                )
        except (ValueError, OSError, KeyError, TypeError) as exc:
            self._error(str(exc))

    def export_results(self):
        if self.result is None:
            return
        name, _ = QFileDialog.getSaveFileName(
            self, "导出运行快照", "three-phase-dq.csv", "CSV (*.csv)"
        )
        if name:
            try:
                export_result(self.result, name)
            except OSError as exc:
                self._error(str(exc))

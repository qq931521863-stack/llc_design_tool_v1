"""DAB voltage-loop and constrained-TPS workspace using shared control tools."""

import json
from pathlib import Path

import numpy as np
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure
from PySide6.QtCore import QThreadPool
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from dab_design.control import DABVoltageControlConfig, simulate_dab_voltage_loop
from dab_design.optimization import TPSConstraints, optimize_tps
from llc_design.gui.widgets.control_block_diagram import (
    BlockSpec,
    ConnectionSpec,
    ControlBlockDiagram,
)
from llc_design.gui.workers import FunctionWorker
from power_control_tools.codegen import render_c99_single_file
from power_control_tools.models import DigitalTransferFunction


class DABControlView(QWidget):
    def __init__(self, stage_provider, parent=None):
        super().__init__(parent)
        self.stage_provider = stage_provider
        self._worker = None
        self.snapshot = None
        self.custom_controller = None
        root = QVBoxLayout(self)
        self.diagram = ControlBlockDiagram()
        blocks = [
            BlockSpec("sum", "Vref - Vout", "Voltage error [V]", 0, 0),
            BlockSpec("pi", "Digital PI / imported C(z)", "Output Pcmd [W]", 180, 0),
            BlockSpec("tps", "TPS + limits", "Pcmd -> D1/D2/D3", 360, 0),
            BlockSpec("plant", "DAB + Co + load", "Vout", 540, 0),
            BlockSpec("sense", "Vout sample", "Ts + sample delay", 360, 100),
        ]
        edges = [
            ConnectionSpec("sum", "pi"),
            ConnectionSpec("pi", "tps"),
            ConnectionSpec("tps", "plant"),
            ConnectionSpec("plant", "sense"),
            ConnectionSpec("sense", "sum", "feedback", True),
        ]
        self.diagram.set_diagram(blocks, edges)
        self.diagram.setMinimumHeight(180)
        root.addWidget(self.diagram)
        form = QFormLayout()
        root.addLayout(form)
        self.inputs = {}
        for key, label, value, lo, hi in [
            ("vb", "工况 Vout / V", 400, 10, 5000),
            ("power", "功率指令 / W", 3500, 1, 1e6),
            ("sample_hz", "电压环采样 / Hz", 5000, 100, 1e5),
            ("kp_w_per_v", "PI Kp / W/V", 100, 0.0001, 1e5),
            ("ti_s", "PI Ti / s", 0.02, 0.00001, 10),
            ("peak", "峰值电流上限 / A", 100, 1, 1e4),
            ("tolerance", "功率容差 / %", 0.5, 0.01, 10),
        ]:
            w = QDoubleSpinBox()
            w.setDecimals(5)
            w.setRange(lo, hi)
            w.setValue(value)
            w.setKeyboardTracking(False)
            self.inputs[key] = w
            form.addRow(label, w)
        self.load = QComboBox()
        self.load.addItem("电阻负载", "resistance")
        self.load.addItem("恒功率负载", "constant_power")
        form.addRow("负载模型", self.load)
        self.cp = QCheckBox("周期功率映射包含 Cp（不含 Cp 瞬态状态）")
        form.addRow(self.cp)
        self.proxy = QCheckBox("优化中约束换流极性（最小电流 0 A；非 ZVS 证明）")
        form.addRow(self.proxy)
        buttons = QHBoxLayout()
        root.addLayout(buttons)
        for text, callback in [
            ("运行电压环 / 闭环验证", self.run_control),
            ("TPS 约束优化对比", self.run_optimization),
            ("打开数字控制器设计", self.open_controller),
            ("导出快照 JSON", self.export_snapshot),
        ]:
            b = QPushButton(text)
            b.clicked.connect(callback)
            buttons.addWidget(b)
        self.tabs = QTabWidget()
        root.addWidget(self.tabs, 1)
        self.summary = QPlainTextEdit()
        self.summary.setReadOnly(True)
        self.tabs.addTab(self.summary, "模型 / 稳定性 / 约束")
        self.fig = Figure(figsize=(10, 6), layout="constrained")
        self.canvas = FigureCanvasQTAgg(self.fig)
        self.tabs.addTab(self.canvas, "Bode / 闭环响应")
        self.code = QPlainTextEdit()
        self.code.setReadOnly(True)
        self.tabs.addTab(self.code, "控制系数 / C99")
        self.status = QLabel("选择工况后可分析功率指令到 Vout 的数字闭环。")
        root.addWidget(self.status)
        self.inputs["kp_w_per_v"].valueChanged.connect(self._use_pi)
        self.inputs["ti_s"].valueChanged.connect(self._use_pi)

    def _use_pi(self):
        self.custom_controller = None

    def set_operating_point(self, vb, power):
        self.inputs["vb"].setValue(vb)
        self.inputs["power"].setValue(max(1, power))

    def _start(self, func, *args):
        if self._worker is not None:
            return
        worker = FunctionWorker(func, *args)
        self._worker = worker
        worker.signals.result.connect(self._display)
        worker.signals.error.connect(
            lambda text: self.status.setText("分析失败：" + text)
        )
        worker.signals.finished.connect(self._finished)
        self.status.setText("正在计算…")
        QThreadPool.globalInstance().start(worker)

    def _finished(self):
        self._worker = None

    def run_control(self):
        try:
            stage = self.stage_provider()
            control = DABVoltageControlConfig(
                sample_hz=self.inputs["sample_hz"].value(),
                kp_w_per_v=self.inputs["kp_w_per_v"].value(),
                ti_s=self.inputs["ti_s"].value(),
                include_cp=self.cp.isChecked(),
                load_model=self.load.currentData(),
            )
            self._start(
                simulate_dab_voltage_loop,
                stage,
                self.inputs["vb"].value(),
                self.inputs["power"].value(),
                control,
                self.custom_controller,
            )
        except ValueError as exc:
            self.status.setText(str(exc))

    def run_optimization(self):
        try:
            limits = TPSConstraints(
                self.inputs["tolerance"].value() / 100,
                self.inputs["peak"].value(),
                0.02,
                0 if self.proxy.isChecked() else None,
            )
            self._start(
                optimize_tps,
                self.stage_provider(),
                self.inputs["vb"].value(),
                self.inputs["power"].value(),
                limits,
            )
        except ValueError as exc:
            self.status.setText(str(exc))

    def open_controller(self):
        from power_control_tools.gui.main_window import ControlToolsMainWindow

        self.designer = ControlToolsMainWindow()
        self.designer.digital_design_updated.connect(self._import_controller)
        self.designer.fs.setValue(self.inputs["sample_hz"].value())
        self.designer.show()

    def _import_controller(self, digital, label):
        from llc_design.control.digital_loop import (
            DigitalTransferFunction as LLCTransfer,
        )

        self.custom_controller = LLCTransfer(
            np.asarray(digital.b),
            np.asarray(digital.a),
            1 / digital.sample_rate_hz,
            label,
        )
        self.inputs["sample_hz"].setValue(digital.sample_rate_hz)
        self.status.setText("已导入 " + label + "；按该 C(z) 原始系数分析和仿真。")

    def _display(self, result):
        if hasattr(result, "to_dict"):
            self.snapshot = result.to_dict()
            self.summary.setPlainText(
                json.dumps(self.snapshot, indent=2, ensure_ascii=False)
            )
            self.status.setText("优化完成；仅显示经 PWM 计数量化复核的候选。")
            return
        t, data, metrics, loop = result
        self.snapshot = {
            "metrics": metrics,
            "time_s": t.tolist(),
            "signals": {k: v.tolist() for k, v in data.items()},
        }
        self.summary.setPlainText(json.dumps(metrics, indent=2, ensure_ascii=False))
        for ax in self.fig.axes:
            ax.set_xscale("linear")
        self.fig.clear()
        axes = self.fig.subplots(2, 2)
        f = loop.frequency_hz
        for label, value in [
            ("G(z)", loop.plant_response),
            ("C(z)", loop.controller_response),
            ("L(z)", loop.open_response),
            ("T(z)", loop.closed_response),
        ]:
            axes[0, 0].semilogx(
                f, 20 * np.log10(np.maximum(abs(value), 1e-20)), label=label
            )
        axes[0, 0].set_ylabel("Gain / dB")
        axes[0, 0].legend()
        axes[0, 1].semilogx(f, np.unwrap(np.angle(loop.open_response)) * 180 / np.pi)
        axes[0, 1].set_ylabel("Loop phase / deg")
        axes[1, 0].plot(t, data["vout_v"], label="Vout")
        axes[1, 0].plot(t, data["vref_v"], "--", label="Vref")
        axes[1, 0].legend()
        axes[1, 0].set_ylabel("V")
        axes[1, 1].plot(t, data["pcmd_w"], label="PI -> Pcmd")
        axes[1, 1].plot(t, data["pload_w"], label="Pload")
        axes[1, 1].legend()
        axes[1, 1].set_ylabel("W")
        for ax in axes.ravel():
            ax.grid(True, alpha=0.3)
        self.canvas.draw_idle()
        digital = DigitalTransferFunction(
            tuple(loop.controller_b),
            tuple(loop.controller_a),
            1 / loop.sample_time_s,
            name="DAB voltage controller",
        )
        self.code.setPlainText(render_c99_single_file(digital, prefix="DAB_VOLTAGE"))
        self.status.setText(
            "闭环完成；离散极点 "
            + ("稳定" if loop.stable else "不稳定")
            + "；C99 为线性控制器，应用层需处理限幅和许可。"
        )

    def export_snapshot(self):
        if self.snapshot is None:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "导出闭环 / 优化快照", "dab_control.json", "JSON (*.json)"
        )
        if path:
            try:
                Path(path).write_text(
                    json.dumps(self.snapshot, indent=2, ensure_ascii=False),
                    encoding="utf-8",
                )
            except OSError as exc:
                self.status.setText(str(exc))

"""Shared PLL experiment for two-level and Vienna workspaces."""

import json
from dataclasses import asdict
from pathlib import Path

import numpy as np
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure
from PySide6.QtCore import QThreadPool
from PySide6.QtWidgets import (
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
from shiboken6 import isValid

from llc_design.gui.widgets.control_block_diagram import (
    BlockSpec,
    ConnectionSpec,
    ControlBlockDiagram,
)
from llc_design.gui.workers import FunctionWorker
from pfc_design.three_phase.pll import KINDS, PLLConfig, PLLScenario, compare_plls
from pfc_design.three_phase.pll_codegen import render_pll_c99


def run_comparison(config, scenario):
    result = compare_plls(config, scenario)
    result["config"] = asdict(config)
    result["scenario"] = asdict(scenario)
    return result


class PLLComparisonView(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.result = None
        self._worker = None
        root = QVBoxLayout(self)
        note = QLabel(
            "同一组实测电压输入对比 SRF / DDSRF / 双 SOGI 正序 PLL。DDSOGI 此处指标准 DSOGI，非多谐波 MSOGI。理想角度仅用于误差评估。"
        )
        note.setWordWrap(True)
        root.addWidget(note)
        form = QFormLayout()
        root.addLayout(form)
        self.inputs = {}
        for key, label, value, lo, hi in (
            ("sample_hz", "采样频率 / Hz", 20000, 4000, 200000),
            ("bandwidth_hz", "PLL 自然频率 / Hz", 20, 0.1, 500),
            ("after_hz", "扰动后电网 / Hz", 52, 40.1, 69.9),
            ("phase_step_deg", "相位跳变 / deg", 20, -180, 180),
            ("negative_sequence", "负序 / pu", 0.15, 0, 1),
            ("fifth_harmonic", "5 次电压 / pu", 0.05, 0, 1),
            ("seventh_harmonic", "7 次电压 / pu", 0.03, 0, 1),
            ("sag_ratio", "扰动后电压 / pu", 0.8, 0.05, 1),
        ):
            w = QDoubleSpinBox()
            w.setDecimals(4)
            w.setRange(lo, hi)
            w.setValue(value)
            w.setKeyboardTracking(False)
            self.inputs[key] = w
            form.addRow(label, w)
        row = QHBoxLayout()
        root.addLayout(row)
        self.run_button = QPushButton("运行三种 PLL 对比")
        self.run_button.clicked.connect(self.run)
        row.addWidget(self.run_button)
        self.kind = QComboBox()
        self.kind.addItems(KINDS)
        self.kind.currentTextChanged.connect(self._kind_changed)
        row.addWidget(self.kind)
        export = QPushButton("导出 CSV / JSON / C99")
        export.clicked.connect(self.export)
        row.addWidget(export)
        self.diagram = ControlBlockDiagram()
        self.diagram.setMinimumHeight(170)
        root.addWidget(self.diagram)
        self.tabs = QTabWidget()
        root.addWidget(self.tabs, 1)
        self.figure = Figure(figsize=(10, 9), layout="constrained")
        self.canvas = FigureCanvasQTAgg(self.figure)
        self.tabs.addTab(self.canvas, "ABC / 锁相过程 / 输出")
        self.summary = QPlainTextEdit()
        self.summary.setReadOnly(True)
        self.tabs.addTab(self.summary, "指标 / 模型边界")
        self.code = QPlainTextEdit()
        self.code.setReadOnly(True)
        self.tabs.addTab(self.code, "C99 实现 / 配置系数")
        self.status = QLabel("0.2 s 时施加频率、相位、负序、5/7 次谐波和电压跌落。")
        root.addWidget(self.status)
        self._kind_changed()

    def _kind_changed(self, *_):
        kind = self.kind.currentText()
        preprocessing = {
            "SRF": "Clarke + Park",
            "DDSRF": "+/- dq decoupling + LPF",
            "DDSOGI": "Two SOGIs + positive sequence",
        }[kind]
        self.diagram.set_diagram(
            [
                BlockSpec("abc", "Vabc", "Measured voltage", 0, 0),
                BlockSpec("sequence", kind, preprocessing, 180, 0),
                BlockSpec("norm", "Vq / magnitude", "Normalized phase error", 360, 0),
                BlockSpec("pi", "PI + clamp", "omega / Hz", 540, 0),
                BlockSpec(
                    "angle", "Angle integration", "theta, sin, cos, lock", 360, 100
                ),
            ],
            [
                ConnectionSpec("abc", "sequence"),
                ConnectionSpec("sequence", "norm"),
                ConnectionSpec("norm", "pi"),
                ConnectionSpec("pi", "angle"),
                ConnectionSpec("angle", "sequence", "theta / adaptive omega", True),
            ],
        )
        config = PLLConfig(**self.result["config"]) if self.result else PLLConfig()
        self.code.setPlainText(render_pll_c99(config, kind))

    def run(self):
        if self._worker is not None:
            return
        try:
            config = PLLConfig(
                sample_hz=self.inputs["sample_hz"].value(),
                bandwidth_hz=self.inputs["bandwidth_hz"].value(),
            )
            scenario = PLLScenario(
                **{
                    key: w.value()
                    for key, w in self.inputs.items()
                    if key not in ("sample_hz", "bandwidth_hz")
                }
            )
            config.validate()
        except ValueError as exc:
            self.status.setText(str(exc))
            return
        worker = FunctionWorker(run_comparison, config, scenario)
        self._worker = worker
        def current():
            return isValid(self) and self._worker is worker
        worker.signals.result.connect(lambda result: self.set_result(result) if current() else None)
        worker.signals.error.connect(lambda error: self.status.setText(error) if current() else None)
        worker.signals.finished.connect(lambda: self._finished(worker))
        self.run_button.setEnabled(False)
        self.status.setText("正在计算三种 PLL…")
        QThreadPool.globalInstance().start(worker)

    def _finished(self, worker):
        if self._worker is not worker:
            return
        self._worker = None
        worker.signals.deleteLater()
        if not isValid(self):
            return
        self.run_button.setEnabled(True)

    def set_result(self, result):
        self.result = result
        self.summary.setPlainText(
            json.dumps(
                {k: result[k] for k in ("config", "scenario", "metrics")},
                indent=2,
                ensure_ascii=False,
            )
        )
        self.figure.clear()
        axes = self.figure.subplots(3, 2)
        t = result["time_s"]
        for j, phase in enumerate("abc"):
            axes[0, 0].plot(t, result["voltage_abc"][:, j], label=phase)
        axes[0, 0].set_ylabel("Grid Vabc / V")
        axes[0, 1].plot(
            t, np.cos(result["true_theta_rad"]), "k--", label="Grid cos(theta)"
        )
        for kind, s in result["results"].items():
            axes[0, 1].plot(t, np.cos(s["theta_rad"]), label=kind)
            axes[1, 0].plot(t, np.degrees(s["phase_error_rad"]), label=kind)
            axes[1, 1].plot(t, s["frequency_hz"], label=kind)
            axes[2, 0].plot(t, s["locked"], label=kind)
            axes[2, 1].plot(t, s["positive_alpha_v"], label=kind + " alpha")
            axes[2, 1].plot(t, s["positive_beta_v"], "--", label=kind + " beta")
        for ax, label in zip(
            axes.ravel(),
            (
                "Vabc / V",
                "cos(theta)",
                "Phase error / deg",
                "Frequency / Hz",
                "Lock qualification",
                "Positive alpha/beta / V",
            ),
        ):
            ax.set_ylabel(label)
            ax.set_xlabel("Time / s")
            ax.grid(True, alpha=0.25)
            ax.legend(fontsize=7)
        self.canvas.draw_idle()
        self._kind_changed()
        self.status.setText(
            "对比完成；未锁定时 relock_s = null。C99 使用本次运行参数。"
        )

    def export(self):
        if self.result is None:
            return
        name, _ = QFileDialog.getSaveFileName(
            self, "导出 PLL 对比", "pll_comparison.csv", "CSV (*.csv)"
        )
        if not name:
            return
        try:
            r = self.result
            columns = {"time_s": r["time_s"]}
            for j, phase in enumerate("abc"):
                columns["v" + phase] = r["voltage_abc"][:, j]
            for kind, signals in r["results"].items():
                columns.update(
                    {kind + "_" + key: value for key, value in signals.items()}
                )
            path = Path(name)
            np.savetxt(
                path,
                np.column_stack(list(columns.values())),
                delimiter=",",
                header=",".join(columns),
                comments="",
            )
            path.with_suffix(".json").write_text(
                json.dumps(
                    {k: r[k] for k in ("config", "scenario", "metrics")}, indent=2
                ),
                encoding="utf-8",
            )
            path.with_suffix(".h").write_text(self.code.toPlainText(), encoding="utf-8")
            self.status.setText(
                "已导出运行快照及 " + self.kind.currentText() + " C99。"
            )
        except OSError as exc:
            self.status.setText(str(exc))

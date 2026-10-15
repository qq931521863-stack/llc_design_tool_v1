"""Sampled current and voltage loop analysis for the two-level dq rectifier."""

import json

import numpy as np
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure
from PySide6.QtWidgets import QPlainTextEdit, QTabWidget, QVBoxLayout, QWidget

from llc_design.gui.widgets.control_block_diagram import (
    BlockSpec,
    ConnectionSpec,
    ControlBlockDiagram,
)
from pfc_design.three_phase.control import build_three_phase_loops
from power_control_tools.codegen import render_c99_single_file
from power_control_tools.models import DigitalTransferFunction


class ThreePhaseLoopView(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        root = QVBoxLayout(self)
        self.diagram = ControlBlockDiagram()
        self.diagram.setMinimumHeight(245)
        root.addWidget(self.diagram)
        blocks = [
            BlockSpec("vsum", "Vref - Vdc", "Voltage error", 0, 0),
            BlockSpec("cv", "Voltage C(z)", "Id reference / A", 180, 0),
            BlockSpec("isum", "Idref - Id", "dq error", 360, 0),
            BlockSpec("ci", "Current C(z)", "PI + decoupling", 540, 0),
            BlockSpec("pwm", "SVPWM + delay", "Current-rate hold", 540, 100),
            BlockSpec("rl", "Three-phase L/R", "Id / Iq", 360, 100),
            BlockSpec("bus", "Bus energy + load", "Vdc", 180, 100),
            BlockSpec("pll", "Vabc -> PLL", "theta / omega", 540, 200),
        ]
        edges = [
            ConnectionSpec("vsum", "cv"),
            ConnectionSpec("cv", "isum"),
            ConnectionSpec("isum", "ci"),
            ConnectionSpec("ci", "pwm"),
            ConnectionSpec("pwm", "rl"),
            ConnectionSpec("rl", "isum", "Id/Iq", True),
            ConnectionSpec("rl", "bus"),
            ConnectionSpec("bus", "vsum", "Vdc", True),
            ConnectionSpec("pll", "ci", "angle"),
        ]
        self.diagram.set_diagram(blocks, edges)
        self.tabs = QTabWidget()
        root.addWidget(self.tabs, 1)
        self.figure = Figure(figsize=(10, 7), layout="constrained")
        self.canvas = FigureCanvasQTAgg(self.figure)
        self.tabs.addTab(self.canvas, "电流环 / 电压环 Bode")
        self.summary = QPlainTextEdit()
        self.summary.setReadOnly(True)
        self.tabs.addTab(self.summary, "传递函数 / 极点 / 裕度")
        self.code = QPlainTextEdit()
        self.code.setReadOnly(True)
        self.tabs.addTab(self.code, "双环系数 / C99")

    def set_result(self, result):
        try:
            analysis = build_three_phase_loops(result.config)
            self.analysis = analysis
            summary = {
                k: v for k, v in analysis.items() if k not in ("current", "voltage")
            }
            summary.update({k: analysis[k].summary() for k in ("current", "voltage")})
            summary["nonlinear_validation"] = result.metrics
            self.summary.setPlainText(json.dumps(summary, indent=2, ensure_ascii=False))
            self.figure.clear()
            axes = self.figure.subplots(2, 2)
            code = []
            for j, key in enumerate(("current", "voltage")):
                loop = analysis[key]
                f = loop.frequency_hz
                for label, value in [
                    ("G(z)", loop.plant_response),
                    ("C(z)", loop.controller_response),
                    ("L(z)", loop.open_response),
                    ("T(z)", loop.closed_response),
                ]:
                    axes[0, j].semilogx(
                        f, 20 * np.log10(np.maximum(abs(value), 1e-20)), label=label
                    )
                axes[0, j].set_title(key)
                axes[0, j].set_ylabel("Gain / dB")
                axes[0, j].legend()
                axes[1, j].semilogx(
                    f, np.degrees(np.unwrap(np.angle(loop.open_response)))
                )
                axes[1, j].set_ylabel("Loop phase / deg")
                axes[1, j].set_xlabel("Hz")
                digital = DigitalTransferFunction(
                    tuple(loop.controller_b),
                    tuple(loop.controller_a),
                    1 / loop.sample_time_s,
                    name=key,
                )
                code.append(
                    render_c99_single_file(digital, prefix="PFC_" + key.upper())
                )
            for ax in axes.ravel():
                ax.grid(True, alpha=0.25)
            self.canvas.draw_idle()
            self.code.setPlainText("\n\n".join(code))
        except ValueError as exc:
            self.summary.setPlainText("局部模型不可用：" + str(exc))

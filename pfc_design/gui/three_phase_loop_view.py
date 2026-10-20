"""Actual dq controller workspace consumers: Bode, candidates, cursor and delivery."""

import json
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from threading import Event

import numpy as np
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure
from PySide6.QtCore import QThreadPool, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
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
from pfc_design.three_phase.control import build_three_phase_loops, request_identity
from pfc_design.three_phase.delivery import export_bundle, render_c99
from pfc_design.three_phase.exact_control import ExactController
from pfc_design.three_phase.workspace import (
    apply_candidate,
    design_candidate,
    scan_operating_points,
)


class ThreePhaseLoopView(QWidget):
    controllerApplied = Signal(str, object)
    nativeRequested = Signal(str)
    retainRequested = Signal()
    clearRetainedRequested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.analysis = None
        self.config = None
        self.retained = ()
        self.result = None
        self.candidates = []
        self._revision = 0
        self._worker = None
        self._cancel = Event()
        root = QVBoxLayout(self)
        self.source_label = QLabel("先运行当前输入。局部模型不含 PLL / 限幅闭环证明。")
        self.source_label.setWordWrap(True)
        root.addWidget(self.source_label)
        self.diagram = ControlBlockDiagram()
        self.diagram.setMinimumHeight(230)
        blocks = [
            BlockSpec("vsum", "Vref − sense(V)", "Voltage error", 0, 0),
            BlockSpec("cv", "Voltage H(z)", "Id* / A + slow FIFO", 180, 0),
            BlockSpec("isum", "Id* − sense(Id)", "dq current error", 360, 0),
            BlockSpec("ci", "Exact current H(z)", "controller voltage / V", 540, 0),
            BlockSpec("pwm", "SVPWM + FIFO", "ZOH physical plant", 540, 100),
            BlockSpec("rl", "Three-phase L/R", "Id / Iq", 360, 100),
            BlockSpec("bus", "Bus energy + load", "Vdc", 180, 100),
            BlockSpec("pll", "Vabc → PLL", "nonlinear only", 540, 200),
        ]
        self.diagram.set_diagram(
            blocks,
            [
                ConnectionSpec("vsum", "cv"),
                ConnectionSpec("cv", "isum"),
                ConnectionSpec("isum", "ci"),
                ConnectionSpec("ci", "pwm"),
                ConnectionSpec("pwm", "rl"),
                ConnectionSpec("rl", "isum", "sampled sense", True),
                ConnectionSpec("rl", "bus"),
                ConnectionSpec("bus", "vsum", "sampled sense", True),
                ConnectionSpec("pll", "ci", "angle"),
            ],
        )
        actions = QHBoxLayout()
        self.loop_selector = QComboBox()
        self.loop_selector.addItems(["current", "voltage"])
        actions.addWidget(self.loop_selector)
        self.import_button = QPushButton("导入精确 H(z) JSON")
        self.native_button = QPushButton("恢复原生 PI")
        self.export_button = QPushButton("导出报告 / CSV / Python / MATLAB / C99")
        self.import_button.clicked.connect(self.import_json)
        self.native_button.clicked.connect(
            lambda: self.nativeRequested.emit(self.loop_selector.currentText())
        )
        self.export_button.clicked.connect(self.export_current)
        self.export_button.setEnabled(False)
        for button in (self.import_button, self.native_button, self.export_button):
            actions.addWidget(button)
        root.addLayout(actions)
        self.tabs = QTabWidget()
        root.addWidget(self.tabs, 1)
        bode_page = QWidget()
        bode_layout = QVBoxLayout(bode_page)
        toggles = QHBoxLayout()
        self.curve_checks = {}
        for name in ("G", "C", "Sense", "Command", "L", "T", "S"):
            cb = QCheckBox(name)
            cb.setChecked(name in ("G", "C", "L", "T", "S"))
            cb.toggled.connect(self.plot)
            toggles.addWidget(cb)
            self.curve_checks[name] = cb
        bode_layout.addLayout(toggles)
        self.figure = Figure(figsize=(10, 7), layout="constrained")
        self.canvas = FigureCanvasQTAgg(self.figure)
        bode_layout.addWidget(self.canvas)
        self.tabs.addTab(bode_page, "独立频域 / 可选 Bode")
        self.summary = QPlainTextEdit()
        self.summary.setReadOnly(True)
        self.tabs.addTab(self.summary, "状态极点 / 全部 signed margins")
        cursor_page = QWidget()
        cursor_layout = QVBoxLayout(cursor_page)
        self.cursor = QDoubleSpinBox()
        self.cursor.setRange(0.00001, 1e8)
        self.cursor.setDecimals(5)
        self.cursor.setValue(100.0)
        self.cursor.valueChanged.connect(self.update_cursor)
        self.loop_selector.currentTextChanged.connect(self.update_cursor)
        cursor_layout.addWidget(self.cursor)
        self.cursor_summary = QPlainTextEdit()
        self.cursor_summary.setReadOnly(True)
        cursor_layout.addWidget(self.cursor_summary)
        self.tabs.addTab(cursor_page, "游标 / 增益相位预算")
        self.code = QPlainTextEdit()
        self.code.setReadOnly(True)
        self.tabs.addTab(self.code, "双环精确系数 / C99")
        page = QWidget()
        layout = QVBoxLayout(page)
        line = QHBoxLayout()
        self.fc = QDoubleSpinBox()
        self.fc.setRange(0.001, 1e7)
        self.fc.setDecimals(4)
        self.fc.setValue(1000.0)
        self.pm = QDoubleSpinBox()
        self.pm.setRange(1.0, 179.0)
        self.pm.setValue(60.0)
        self.candidate_button = QPushButton("计算 Fc × PM 3×3 候选")
        self.apply_button = QPushButton("Apply 所选候选")
        self.apply_button.setEnabled(False)
        self.retain_button = QPushButton("保留当前工况")
        self.clear_retained_button = QPushButton("清空保留工况")
        self.scan_button = QPushButton("同一控制器扫描工况")
        for label, w in (("Fc (Hz)", self.fc), ("PM (deg)", self.pm)):
            line.addWidget(QLabel(label))
            line.addWidget(w)
        line.addWidget(self.candidate_button)
        line.addWidget(self.apply_button)
        layout.addLayout(line)
        line2 = QHBoxLayout()
        for button in (
            self.retain_button,
            self.clear_retained_button,
            self.scan_button,
        ):
            line2.addWidget(button)
        layout.addLayout(line2)
        layout.addWidget(
            QLabel(
                "网格：Fc × [0.5, 1, 1.5]；PM + [−10, 0, 10]。先计算、再显式 Apply；工况不重新调参。"
            )
        )
        self.candidate_table = QTableWidget(0, 5)
        self.candidate_table.setHorizontalHeaderLabels(
            ["Fc", "PM", "状态", "实际 Fc / PM", "最大极点半径"]
        )
        self.candidate_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        self.candidate_table.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows
        )
        self.candidate_table.itemSelectionChanged.connect(self.select_candidate)
        layout.addWidget(self.candidate_table)
        self.candidate_details = QPlainTextEdit()
        self.candidate_details.setReadOnly(True)
        layout.addWidget(self.candidate_details)
        self.tabs.addTab(page, "候选 / 工况 / Apply")
        self.tabs.addTab(self.diagram, "控制框图 / 模型边界")
        self.candidate_button.clicked.connect(self.generate_candidates)
        self.apply_button.clicked.connect(self.apply_selected)
        self.retain_button.clicked.connect(self.retainRequested.emit)
        self.clear_retained_button.clicked.connect(self.clearRetainedRequested.emit)
        self.scan_button.clicked.connect(self.scan_corners)
        self.fc.valueChanged.connect(self.clear_candidates)
        self.pm.valueChanged.connect(self.clear_candidates)
        self.loop_selector.currentTextChanged.connect(self.clear_candidates)

    def clear_candidates(self, *_):
        self._cancel.set()
        self._revision += 1
        self.candidates = []
        self.candidate_table.setRowCount(0)
        self.apply_button.setEnabled(False)

    def invalidate(self, message="输入已更改；请重新运行。"):
        self.analysis = None
        self.result = None
        self.clear_candidates()
        self.export_button.setEnabled(False)
        self.code.clear()
        self.summary.setPlainText(message)
        self.source_label.setText(message)
        self.figure.clear()
        self.canvas.draw_idle()
        self.cursor_summary.clear()

    def set_context(self, config, retained=()):
        self.config = config
        self.retained = tuple(retained)

    def set_result(self, result):
        self.result = result
        self.config = result.config
        try:
            self.set_analysis(build_three_phase_loops(result.config), result)
        except ValueError as exc:
            self.invalidate("局部模型不可用：" + str(exc))

    def set_analysis(self, analysis, result=None):
        if self.config is None or analysis["source_identity"] != request_identity(
            self.config
        ):
            raise ValueError("analysis belongs to different source inputs")
        self.analysis = analysis
        self.result = result
        summary = {k: v for k, v in analysis.items() if k not in ("current", "voltage")}
        summary.update({k: analysis[k].summary() for k in ("current", "voltage")})
        if result is not None:
            summary["nonlinear_validation"] = result.metrics
        self.summary.setPlainText(json.dumps(summary, indent=2, ensure_ascii=False))
        self.source_label.setText(
            "当前 source: "
            + analysis["source_identity"]
            + "；局部采样模型极点证明，不代表 PLL / 非线性 / 硬件。"
        )
        self.code.setPlainText(
            "/* UNVERIFIED preview. Export action runs host C99 + standalone Python verification. */\n"
            + render_c99(self.config)
        )
        self.export_button.setEnabled(True)
        self.plot()
        self.update_cursor()

    def plot(self, *_):
        if self.analysis is None:
            return
        self.figure.clear()
        axes = self.figure.subplots(2, 2)
        for j, key in enumerate(("current", "voltage")):
            loop = self.analysis[key]
            responses = {
                "G": loop.physical_plant_response,
                "C": loop.controller_response,
                "Sense": loop.sense_response,
                "Command": loop.command_response,
                "L": loop.open_response,
                "T": loop.closed_response,
                "S": loop.sensitivity,
            }
            for name, response in responses.items():
                if self.curve_checks[name].isChecked():
                    axes[0, j].semilogx(
                        loop.frequency_hz,
                        20 * np.log10(np.maximum(abs(response), 1e-30)),
                        label=name,
                    )
                    axes[1, j].semilogx(
                        loop.frequency_hz,
                        np.degrees(np.unwrap(np.angle(response))),
                        label=name,
                    )
            axes[0, j].set_title(key + f" (< {0.5 / loop.sample_time_s:g} Hz Nyquist)")
            axes[0, j].set_ylabel("Gain / dB")
            axes[1, j].set_ylabel("Phase / deg")
            axes[1, j].set_xlabel("Hz")
        for ax in axes.ravel():
            ax.grid(True, alpha=0.25)
            if ax.lines:
                ax.legend(fontsize=8, ncol=4)
        self.canvas.draw_idle()

    def update_cursor(self, *_):
        if self.analysis is None:
            return
        try:
            budget = self.analysis[self.loop_selector.currentText()].phase_budget(
                self.cursor.value()
            )
            self.cursor_summary.setPlainText(
                json.dumps(budget, indent=2, ensure_ascii=False)
            )
        except ValueError as exc:
            self.cursor_summary.setPlainText(str(exc))

    def import_json(self):
        name, _ = QFileDialog.getOpenFileName(
            self, "导入精确 H(z)", "", "JSON (*.json)"
        )
        if not name:
            return
        try:
            if Path(name).stat().st_size > 100000:
                raise ValueError("controller file too large")
            data = json.loads(Path(name).read_text(encoding="utf-8"))
            controller = ExactController(**data)
            self.controllerApplied.emit(self.loop_selector.currentText(), controller)
        except (ValueError, TypeError, OSError) as exc:
            self._error(exc)

    def generate_candidates(self):
        if self.config is None or self._worker is not None:
            return
        config, retained, loop = (
            self.config,
            self.retained,
            self.loop_selector.currentText(),
        )
        revision = self._revision
        fc, pm = self.fc.value(), self.pm.value()

        self._cancel = Event()
        cancel = self._cancel
        self.destroyed.connect(lambda: cancel.set())

        def run():
            candidates = []
            for offset in (-10, 0, 10):
                for scale in (0.5, 1.0, 1.5):
                    if cancel.is_set():
                        return []
                    candidates.append(
                        design_candidate(
                            config, loop, fc * scale, pm + offset, retained
                        )
                    )
            return candidates

        self.candidate_button.setEnabled(False)
        worker = FunctionWorker(run)
        self._worker = worker

        def receive(candidates):
            if isValid(self) and revision == self._revision and self._worker is worker:
                self.candidates = candidates
                self.candidate_table.setRowCount(len(candidates))
                for row, c in enumerate(candidates):
                    actual = c.nominal.get("actual", {})
                    values = (
                        c.target_fc_hz,
                        c.target_pm_deg,
                        c.status,
                        f"{actual.get('crossover_hz')} / {actual.get('phase_margin_deg')}",
                        actual.get("max_pole_radius"),
                    )
                    for col, value in enumerate(values):
                        self.candidate_table.setItem(
                            row, col, QTableWidgetItem(str(value))
                        )

        worker.signals.result.connect(receive)
        worker.signals.error.connect(
            lambda error: (
                self._error(error)
                if isValid(self) and revision == self._revision
                else None
            )
        )
        worker.signals.finished.connect(lambda: self._finish_worker(worker))
        worker.start(QThreadPool.globalInstance())

    def _finish_worker(self, worker):
        if self._worker is worker:
            self._worker = None
            if isValid(self):
                self.candidate_button.setEnabled(True)
        worker.signals.deleteLater()

    def select_candidate(self):
        row = self.candidate_table.currentRow()
        candidate = self.candidates[row] if 0 <= row < len(self.candidates) else None
        self.apply_button.setEnabled(candidate is not None and candidate.applicable)
        if candidate:
            self.candidate_details.setPlainText(
                json.dumps(asdict(candidate), indent=2, ensure_ascii=False)
            )

    def apply_selected(self):
        row = self.candidate_table.currentRow()
        if not 0 <= row < len(self.candidates):
            return
        try:
            candidate = self.candidates[row]
            updated = apply_candidate(self.config, candidate)
            self.controllerApplied.emit(
                candidate.loop, getattr(updated, candidate.loop + "_controller")
            )
        except ValueError as exc:
            self._error(exc)

    def scan_corners(self):
        if self.config is None:
            return
        self.candidate_details.setPlainText(
            json.dumps(
                scan_operating_points(self.config, self.retained),
                indent=2,
                ensure_ascii=False,
            )
        )

    def export_current(self):
        if self.analysis is None:
            return
        parent = QFileDialog.getExistingDirectory(self, "选择导出父目录")
        if not parent:
            return
        destination = Path(parent) / (
            "dq-"
            + request_identity(self.config)[:12]
            + "-"
            + datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        )
        try:
            result = export_bundle(
                destination,
                self.config,
                self.analysis,
                retained=self.retained,
                targets=self.targets(),
                nonlinear_result=self.result,
            )
            self.source_label.setText(
                f"已导出 {result['path']}；C99 host {result['c99']['status']}；MATLAB 未执行。"
            )
        except (ValueError, OSError, RuntimeError) as exc:
            self._error(exc)

    def targets(self):
        return {
            "loop": self.loop_selector.currentText(),
            "fc_hz": self.fc.value(),
            "pm_deg": self.pm.value(),
        }

    def _error(self, error):
        QMessageBox.warning(self, "dq 控制工作区", str(error))

    def closeEvent(self, event):
        self._cancel.set()
        self._revision += 1
        super().closeEvent(event)

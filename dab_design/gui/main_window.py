"""LLC-style parameter dock and analysis tabs for the DAB TPS model."""

from __future__ import annotations

from pathlib import Path

import numpy as np
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg, NavigationToolbar2QT
from matplotlib.figure import Figure
from PySide6.QtCore import Qt, QThreadPool, QTimer, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDockWidget,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from dab_design.core import (
    DABConfig,
    DABResult,
    analyze_dab,
    export_csv,
    load_config,
    save_config,
)
from llc_design.gui import theme
from llc_design.gui.contact import add_toolbar_contacts
from llc_design.gui.help import install_help
from llc_design.gui.i18n_ui import install_language_selector
from llc_design.gui.workers import FunctionWorker

# label, multiplier from SI to displayed unit, minimum, maximum
PARAMETERS = {
    "va_v": ("输入 VA (V)", 1, 1, 5000),
    "turns_ratio": ("变比 n = Ns/Np", 1, 0.01, 100),
    "switching_hz": ("开关频率 (kHz)", 1e-3, 0.1, 2000),
    "inductance_h": ("传输电感 (µH)", 1e6, 0.01, 100000),
    "primary_capacitance_f": ("原边串联 Cp (µF)", 1e6, 0.001, 100000),
    "output_capacitance_f": ("输出 Co (µF)", 1e6, 0.01, 1000000),
    "series_resistance_ohm": ("串联电阻 (mΩ)", 1e3, 0, 10000),
    "low_power_w": ("降额功率 (kW)", 1e-3, 0.001, 10000),
    "rated_power_w": ("额定功率 (kW)", 1e-3, 0.001, 10000),
    "derate_end_v": ("降额区上限 VB (V)", 1, 1, 5000),
    "rated_start_v": ("额定区起点 VB (V)", 1, 1, 5000),
    "p_limit": ("归一化功率上限", 1, 0.001, 1),
    "tbclk_hz": ("PWM 时钟 (MHz)", 1e-6, 1, 1000),
}


class DABMainWindow(QMainWindow):
    workspace_switch_requested = Signal(str)

    def __init__(self):
        super().__init__()
        self.setWindowTitle("电源设计工具箱 — DAB TPS")
        self.resize(1440, 900)
        self.setStyleSheet(theme.workspace_stylesheet(theme.active_theme()))
        self.result: DABResult | None = None
        self._worker = None
        self._running_config = None
        self._rerun_requested = False
        self._dirty = True
        self._pending_point = 0
        self._animation_phase = 0
        self._animated_lines = []
        toolbar = self.addToolBar("DAB 工程")
        toolbar.setObjectName("dab_toolbar")
        for label, callback in (
            ("功能选择", lambda: self.workspace_switch_requested.emit("home")),
            ("LLC", lambda: self.workspace_switch_requested.emit("llc")),
            ("PFC", lambda: self.workspace_switch_requested.emit("pfc")),
            ("加载工程", self.load_project),
            ("保存工程", self.save_project),
            ("导出结果 CSV", self.export_results),
        ):
            action = QAction(label, self)
            action.triggered.connect(callback)
            toolbar.addAction(action)
        self.export_action = action
        self.export_action.setEnabled(False)
        add_toolbar_contacts(toolbar)
        install_help(self, "dab")
        self.tabs = QTabWidget()
        self.setCentralWidget(self.tabs)
        summary_page = QWidget()
        layout = QVBoxLayout(summary_page)
        self.summary = QPlainTextEdit()
        self.summary.setReadOnly(True)
        self.summary.setMaximumHeight(220)
        self.summary.setPlainText(
            "选择“查看工况”即可自动计算：默认 5 个输出电压 × 5 个负载点。"
        )
        layout.addWidget(self.summary)
        self.table = QTableWidget()
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.cellClicked.connect(self._select_row)
        layout.addWidget(self.table)
        self.tabs.addTab(summary_page, "总览 / 工况表")
        self.figures = {}
        self.canvases = {}
        for title in (
            "TPS / 功率",
            "电气波形",
            "电流 / 回流",
            "电容应力",
            "换流 / PWM",
        ):
            page = QWidget()
            box = QVBoxLayout(page)
            fig = Figure(figsize=(10, 7), layout="constrained")
            canvas = FigureCanvasQTAgg(fig)
            box.addWidget(NavigationToolbar2QT(canvas, self))
            box.addWidget(canvas)
            self.tabs.addTab(page, title)
            self.figures[title], self.canvases[title] = fig, canvas
        dock = QDockWidget("DAB 设计参数", self)
        dock.setObjectName("dab_parameters")
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, dock)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        panel = QWidget()
        form = QFormLayout(panel)
        self.inputs = {}
        for key, (label, scale, low, high) in PARAMETERS.items():
            widget = QDoubleSpinBox()
            widget.setRange(low, high)
            widget.setDecimals(5)
            widget.setValue(getattr(DABConfig(), key) * scale)
            widget.valueChanged.connect(self._inputs_changed)
            self.inputs[key] = widget
            form.addRow(label, widget)
        self.voltages = QLineEdit("300,350,400,450,500")
        self.loads = QLineEdit("10,30,50,75,100")
        self.samples = QSpinBox()
        self.samples.setRange(512, 262144)
        self.samples.setSingleStep(512)
        self.samples.setValue(16384)
        for label, widget in (
            ("输出电压 VB (V)", self.voltages),
            ("负载点 (%)", self.loads),
            ("周期采样数 (偶数)", self.samples),
        ):
            form.addRow(label, widget)
        self.voltages.textChanged.connect(self._inputs_changed)
        self.loads.textChanged.connect(self._inputs_changed)
        self.samples.valueChanged.connect(self._inputs_changed)
        self.run_button = QPushButton("运行 TPS 扫描")
        self.run_button.clicked.connect(self.run_analysis)
        form.addRow(self.run_button)
        self.point_choice = QComboBox()
        cfg = DABConfig()
        for vb in cfg.vb_cases_v:
            for load in cfg.load_ratios:
                self.point_choice.addItem(f"VB {vb:g} V · {100 * load:g}%")
        self.point_choice.setCurrentIndex(-1)
        self.point_choice.currentIndexChanged.connect(self._point_selected)
        self.point_choice.activated.connect(self._point_activated)
        form.addRow("查看工况", self.point_choice)
        self.animate = QCheckBox("周期波形循环播放（10 帧/秒）")
        self.animate.setChecked(True)
        form.addRow(self.animate)
        reset_animation = QPushButton("波形相位复位")
        reset_animation.clicked.connect(self._reset_animation)
        form.addRow(reset_animation)
        note = QLabel(
            "n = Ns/Np；D1/D2/D3 为半周期归一化相位。\n"
            "正向功率稳态分析；Cp 不改变理想 TPS 指令公式。\n"
            "换流电流不等于 ZVS 证明，计数不直接对应 MCU 寄存器。"
        )
        note.setWordWrap(True)
        form.addRow(note)
        scroll.setWidget(panel)
        dock.setWidget(scroll)
        dock.setMinimumWidth(340)
        from .control_view import DABControlView

        self.control_view = DABControlView(self.config_from_widgets)
        self.tabs.addTab(self.control_view, "电压环 / TPS 优化")
        self.animation_timer = QTimer(self)
        self.animation_timer.setInterval(100)
        self.animation_timer.timeout.connect(self._animate_waveform)
        self.animation_timer.start()
        install_language_selector(self)

    def _point_activated(self, index):
        if self._dirty:
            self._point_selected(index)

    def _point_selected(self, index):
        if index < 0:
            return
        self._pending_point = index
        if self.result is None or self._dirty:
            self.run_analysis()
        else:
            self._plot_selected(index)

    def _reset_animation(self):
        self._animation_phase = 0
        for line, values in self._animated_lines:
            line.set_ydata(values)
        self.canvases["电气波形"].draw_idle()

    def _animate_waveform(self):
        if (
            not self.animate.isChecked()
            or not self.isVisible()
            or self.tabs.currentWidget() is not self.canvases["电气波形"].parentWidget()
        ):
            return
        self._animation_phase = (self._animation_phase + 1) % 40
        for line, values in self._animated_lines:
            line.set_ydata(
                np.roll(values, round(len(values) * self._animation_phase / 40))
            )
        self.canvases["电气波形"].draw_idle()

    def _inputs_changed(self):
        self._dirty = True
        if hasattr(self, "control_view"):
            self.control_view.mark_stale()
        if hasattr(self, "point_choice"):
            try:
                config = self.config_from_widgets()
                selected = self.point_choice.currentIndex()
                self.point_choice.blockSignals(True)
                self.point_choice.clear()
                for vb in config.vb_cases_v:
                    for load in config.load_ratios:
                        self.point_choice.addItem(f"VB {vb:g} V · {100 * load:g}%")
                self._pending_point = max(
                    0, min(selected, self.point_choice.count() - 1)
                )
                self.point_choice.setCurrentIndex(
                    min(selected, self.point_choice.count() - 1)
                )
            except (ValueError, TypeError):
                pass
            finally:
                self.point_choice.blockSignals(False)
        if self.result is not None:
            self.statusBar().showMessage(
                "参数已修改；显示与导出的仍为上次运行快照，请重新运行。"
            )

    def config_from_widgets(self) -> DABConfig:
        values = {
            key: widget.value() / PARAMETERS[key][1]
            for key, widget in self.inputs.items()
        }
        values.update(
            vb_cases_v=tuple(float(v.strip()) for v in self.voltages.text().split(",")),
            load_ratios=tuple(
                float(v.strip()) / 100 for v in self.loads.text().split(",")
            ),
            wave_samples=self.samples.value(),
        )
        config = DABConfig(**values)
        config.validate()
        return config

    def run_analysis(self):
        try:
            config = self.config_from_widgets()
        except (ValueError, TypeError) as exc:
            self._error(str(exc))
            return
        if self._worker is not None:
            self._rerun_requested = config != self._running_config
            if self._rerun_requested:
                self.statusBar().showMessage("计算完成后将自动使用最新参数重算。")
            return
        self._running_config = config
        self.run_button.setEnabled(False)
        self.statusBar().showMessage("正在计算 TPS 工况…")
        worker = FunctionWorker(analyze_dab, config)
        self._worker = worker
        worker.signals.result.connect(self.set_result)
        worker.signals.error.connect(self._error)
        worker.signals.finished.connect(self._finished)
        QThreadPool.globalInstance().start(worker)

    def _finished(self):
        self._worker = None
        self._running_config = None
        self.run_button.setEnabled(True)
        if self._rerun_requested:
            self._rerun_requested = False
            QTimer.singleShot(0, self.run_analysis)

    def _error(self, message):
        self.statusBar().showMessage("分析失败；未生成新结果")
        QMessageBox.warning(self, "DAB", message)

    def set_result(self, result: DABResult):
        self.result = result
        self._dirty = False
        columns = (
            "vb_v",
            "load_percent",
            "pref_w",
            "mode_id",
            "p_raw",
            "limited",
            "d1_deg",
            "d2_deg",
            "d3_deg",
            "ideal_pout_w",
            "pout_w",
            "irms_a",
            "ipeak_a",
            "vcp_peak_v",
            "vout_ripple_pp_v",
            "zvs_status",
        )
        self.table.setRowCount(len(result.points))
        self.table.setColumnCount(len(columns))
        self.table.setHorizontalHeaderLabels(columns)
        for row, point in enumerate(result.points):
            for col, key in enumerate(columns):
                value = point.summary[key]
                text = f"{value:.5g}" if isinstance(value, float) else str(value)
                self.table.setItem(row, col, QTableWidgetItem(text))
        self.table.resizeColumnsToContents()
        limited = sum(p.command.limited for p in result.points)
        infeasible = sum(not p.command.feasible for p in result.points)
        self.summary.setPlainText(
            f"{len(result.points)} 工况完成 · 指令限幅 {limited} 点 · 超理想功率上限 {infeasible} 点\n"
            "证据范围：SOFTWARE_MODEL_ONLY；ZVS = UNKNOWN。\n"
            "L-only：理想电感；L+Cp：频域周期解，包含指定串联 R。\n"
            "Cp 引起的功率偏差直接显示，不回调 TPS 指令。未建模器件损耗、死区和磁化支路。\n"
            "输出纹波仅为开关周期稳态纹波，不含负载阶跃或闭环响应。\n"
            "点击表格行或选择工况查看波形；CSV 导出全部指标及运行参数快照。"
        )
        self.point_choice.blockSignals(True)
        self.point_choice.clear()
        for point in result.points:
            self.point_choice.addItem(
                f"VB {point.vb_v:g} V · {point.load_ratio * 100:g}% · {point.pref_w:g} W"
            )
        selected = min(self._pending_point, len(result.points) - 1)
        self.point_choice.setCurrentIndex(selected)
        self.point_choice.blockSignals(False)
        self._plot_selected(selected)
        self.export_action.setEnabled(True)
        self.statusBar().showMessage("TPS 扫描完成；软件模型结果")
        try:
            if self.config_from_widgets() != result.config:
                self._inputs_changed()
        except (ValueError, TypeError):
            self._inputs_changed()

    def _select_row(self, row, column):
        self.point_choice.setCurrentIndex(row)

    def _plot_selected(self, index):
        if self.result is None or index < 0:
            return
        point = self.result.points[index]
        self.control_view.set_operating_point(point.vb_v, point.pref_w)
        self._animation_phase = 0
        self._animated_lines = []
        same_v = [p for p in self.result.points if p.vb_v == point.vb_v]
        x = [100 * p.load_ratio for p in same_v]

        def axes(title, count):
            fig = self.figures[title]
            fig.clear()
            return np.atleast_1d(fig.subplots(count, 1))

        ax = axes("TPS / 功率", 2)
        for key in ("d1_deg", "d2_deg", "d3_deg", "secondary_inner_deg"):
            ax[0].plot(x, [p.summary[key] for p in same_v], "o-", label=key)
        ax[0].set_ylabel("deg")
        for key in ("pref_w", "ideal_pout_w", "pout_w"):
            ax[1].plot(x, [p.summary[key] for p in same_v], "o-", label=key)
        ax[1].set_ylabel("W")
        ax[1].set_xlabel(f"Load (%) at VB={point.vb_v:g} V")
        ax = axes("电气波形", 3)
        wave = point.with_cp
        t = wave.time_s * 1e6
        for key in ("va_v", "vb_referred_v", "vl_v"):
            ax[0].plot(t, wave.signals[key], label=key)
        ax[0].set_ylabel("V")
        ax[1].plot(t, wave.signals["il_a"], label="IL with Cp")
        ax[1].plot(t, point.ideal.signals["il_a"], "--", label="IL ideal L-only")
        ax[1].set_ylabel("A")
        for j, key in enumerate(("qa1", "qa2", "qb1", "qb2")):
            ax[2].step(t, wave.signals[key] + 3 * j, where="post", label=key)
        ax[2].set_ylabel("Leg functions (offset)")
        ax[2].set_xlabel("Time (us); mathematical leg polarity, not gate outputs")
        for axis in self.figures["电气波形"].axes:
            for line in axis.lines:
                self._animated_lines.append(
                    (line, np.array(line.get_ydata(), copy=True))
                )
        self.figures["电气波形"].suptitle(
            "Periodic steady-state replay · 10 fps; not transient simulation"
        )
        ax = axes("电流 / 回流", 2)
        for key in ("irms_a", "ipeak_a", "ipp_a"):
            ax[0].plot(x, [p.summary[key] for p in same_v], "o-", label=key)
        ax[0].set_ylabel("A")
        for key in (
            "backflow_primary_w",
            "backflow_secondary_w",
            "circulation_proxy_w",
        ):
            ax[1].plot(x, [p.summary[key] for p in same_v], "o-", label=key)
        ax[1].set_ylabel("W (proxy, not reactive power)")
        ax[1].set_xlabel("Load (%)")
        ax = axes("电容应力", 3)
        ax[0].plot(t, wave.signals["vcp_v"], label="Primary Cp voltage")
        ax[0].set_ylabel("V")
        ax[1].plot(t, wave.signals["iout_ripple_a"], label="Co ripple current")
        ax[1].set_ylabel("A")
        ax[2].plot(t, wave.signals["vout_ripple_v"], label="Output switching ripple")
        ax[2].set_ylabel("V")
        ax[2].set_xlabel("Time (us)")
        ax = axes("换流 / PWM", 2)
        events = np.concatenate(
            (wave.primary_event_current_a, wave.secondary_event_current_a)
        )
        ax[0].bar(range(8), events)
        ax[0].set_xticks(
            range(8), ("A1+", "A1-", "A2+", "A2-", "B1+", "B1-", "B2+", "B2-")
        )
        ax[0].set_ylabel("Primary-referred IL (A)")
        ax[0].set_title("Signed sampled commutation current; ZVS UNKNOWN")
        for key in ("primary_active_width_count", "secondary_active_width_count"):
            ax[1].plot(x, [p.summary[key] for p in same_v], "o-", label=key)
        ax[1].set_ylabel("TBCLK ticks")
        ax[1].set_xlabel("Load (%); full-period tick convention")
        for title, fig in self.figures.items():
            for a in fig.axes:
                a.grid(True, alpha=0.25)
                if a.get_legend_handles_labels()[0]:
                    a.legend(fontsize=8)
            self.canvases[title].draw_idle()

    def save_project(self):
        try:
            config = self.config_from_widgets()
            name, _ = QFileDialog.getSaveFileName(
                self, "保存 DAB 工程", "dab-tps.json", "JSON (*.json)"
            )
            if name:
                save_config(config, name)
        except (ValueError, TypeError, OSError) as exc:
            self._error(str(exc))

    def load_project(self):
        name, _ = QFileDialog.getOpenFileName(
            self, "加载 DAB 工程", "", "JSON (*.json)"
        )
        if not name:
            return
        try:
            config = load_config(name)
            for key, widget in self.inputs.items():
                display = getattr(config, key) * PARAMETERS[key][1]
                if not widget.minimum() <= display <= widget.maximum():
                    raise ValueError(f"{key} exceeds the desktop input range")
            for key, widget in self.inputs.items():
                widget.setValue(getattr(config, key) * PARAMETERS[key][1])
            self.voltages.setText(",".join(f"{v:g}" for v in config.vb_cases_v))
            self.loads.setText(",".join(f"{100 * v:g}" for v in config.load_ratios))
            self.samples.setValue(config.wave_samples)
        except (ValueError, TypeError, KeyError, OSError) as exc:
            self._error(str(exc))

    def export_results(self):
        if self.result is None:
            return
        name, _ = QFileDialog.getSaveFileName(
            self, "导出运行快照", "dab-tps-results.csv", "CSV (*.csv)"
        )
        if name:
            try:
                export_csv(self.result, name)
                save_config(self.result.config, Path(name).with_suffix(".config.json"))
                self.statusBar().showMessage("已导出 CSV 和对应参数快照")
            except OSError as exc:
                self._error(str(exc))

    def closeEvent(self, event):
        if self._worker is not None or self.control_view._worker is not None:
            self.statusBar().showMessage("计算进行中，请完成后关闭。")
            event.ignore()
        else:
            super().closeEvent(event)

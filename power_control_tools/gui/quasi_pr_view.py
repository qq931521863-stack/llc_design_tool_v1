"""Multi-harmonic PR design page with branch Bode and standalone C export."""
from pathlib import Path

import numpy as np
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure
from PySide6.QtCore import QTimer, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from power_control_tools.quasi_pr import (
    HARMONICS,
    QuasiPRConfig,
    ResonantBranch,
    bode_frequencies,
    frequency_response,
    validate_float32,
)
from power_control_tools.quasi_pr_codegen import coefficient_json, render_quasi_pr_c99


class QuasiPRView(QWidget):
    export_state_changed = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.config = None
        self._config_generation = 0
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(100)
        self.timer.timeout.connect(self.recalculate)
        root = QHBoxLayout(self)
        panel = QWidget()
        left = QVBoxLayout(panel)
        formula = QLabel('多谐波准 PR\nH(s) = Kp + Σ 2Kr·wc·s / (s² + 2wc·s + wr²)\n'
                         'wr = 2π·h·fgrid；wc = 2π·带宽参数\n并联 R 支路，共用一次 Kp；Tustin（无预畸变）。')
        formula.setWordWrap(True)
        left.addWidget(formula)
        form = QFormLayout()
        self.fs = self._spin(100, 1e6, 40000, 1)
        self.grid = self._spin(1, 1000, 50, 3)
        self.kp = self._spin(0, 10000, 0.1, 6)
        self.lower = self._spin(-1e6, 0, -1, 6)
        self.upper = self._spin(0, 1e6, 1, 6)
        for label, w in [('采样 Fs / Hz', self.fs), ('基频 / Hz', self.grid),
                         ('公共 Kp', self.kp), ('输出下限', self.lower), ('输出上限', self.upper)]:
            form.addRow(label, w)
        left.addLayout(form)
        self.branch_widgets = {}
        for h in HARMONICS:
            row = QHBoxLayout()
            selected = QCheckBox(f'{h} 次')
            selected.setChecked(h == 1)
            kr = self._spin(0, 1e5, 10, 5)
            bw = self._spin(0.0001, 1e4, 1, 4)
            row.addWidget(selected)
            row.addWidget(QLabel('Kr'))
            row.addWidget(kr)
            row.addWidget(QLabel('wc/2π [Hz]'))
            row.addWidget(bw)
            left.addLayout(row)
            selected.toggled.connect(self.schedule)
            self.branch_widgets[h] = selected, kr, bw
        self.freeze = QCheckBox('饱和时冻结全部谐振状态（需闭环验证）')
        self.freeze.toggled.connect(self.schedule)
        left.addWidget(self.freeze)
        note = QLabel('带宽参数为 wc/2π，模拟支路全 −3 dB 带宽为其两倍。\n'
                      '默认仅输出限幅，不防止内部状态累积。Bode 对应未饱和小信号。\n'
                      '无选中支路时为纯 P；Kp=0 时为纯 R 补偿。\n'
                      '系数重算和初始化在控制停用时执行，状态只由一个任务持有。')
        note.setWordWrap(True)
        left.addWidget(note)
        self.status = QLabel()
        self.status.setWordWrap(True)
        left.addWidget(self.status)
        self.export_button = QPushButton('导出全部函数 C99 .h')
        self.export_button.clicked.connect(self.export_c99)
        left.addWidget(self.export_button)
        self.json_button = QPushButton('导出系数与参数 JSON')
        self.json_button.clicked.connect(self.export_json)
        left.addWidget(self.json_button)
        left.addStretch()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(panel)
        scroll.setMinimumWidth(480)
        scroll.setMaximumWidth(620)
        root.addWidget(scroll)
        tabs = QTabWidget()
        root.addWidget(tabs, 1)
        self.figure = Figure(figsize=(9, 7))
        self.canvas = FigureCanvasQTAgg(self.figure)
        tabs.addTab(self.canvas, '各支路 / 合成 Bode')
        self.coefficients = QPlainTextEdit()
        self.coefficients.setReadOnly(True)
        tabs.addTab(self.coefficients, '控制系数')
        self.code = QPlainTextEdit()
        self.code.setReadOnly(True)
        tabs.addTab(self.code, 'C99 函数实现')
        self.recalculate()

    def _spin(self, lo, hi, value, decimals):
        w = QDoubleSpinBox()
        w.setDecimals(decimals)
        w.setRange(lo, hi)
        w.setValue(value)
        w.setKeyboardTracking(False)
        w.valueChanged.connect(self.schedule)
        return w

    def schedule(self):
        self._invalidate_config()
        self.timer.start()

    def _invalidate_config(self):
        self._config_generation += 1
        self.config = None
        self.export_button.setEnabled(False)
        self.json_button.setEnabled(False)
        self.export_state_changed.emit(False)

    def closeEvent(self, event):
        self.timer.stop()
        self._invalidate_config()
        super().closeEvent(event)

    def _clear_plot(self):
        # Shared log axes can warn while Figure.clear resets their limits.
        for axis in self.figure.axes:
            axis.set_xscale('linear')
            axis.set_yscale('linear')
        self.figure.clear()

    def recalculate(self):
        self.timer.stop()
        self._invalidate_config()
        try:
            cfg = QuasiPRConfig(
                sample_rate_hz=self.fs.value(), grid_frequency_hz=self.grid.value(), kp=self.kp.value(),
                branches=tuple(ResonantBranch(h, kr.value(), bw.value())
                               for h, (box, kr, bw) in self.branch_widgets.items() if box.isChecked()),
                output_min=self.lower.value(), output_max=self.upper.value(),
                freeze_on_saturation=self.freeze.isChecked())
            cfg.validate()
            validate_float32(cfg)
            f = bode_frequencies(cfg)
            total, branches = frequency_response(cfg, f)
            analog, _ = frequency_response(cfg, f, analog=True)
            quantized, _ = frequency_response(cfg, f, quantized=True)
            self._clear_plot()
            mag, phase = self.figure.subplots(2, 1, sharex=True)
            curves = [(f'R{b.harmonic}', v, ':') for b, v in zip(cfg.branches, branches)]
            curves += [('Total analog', analog, '--'), ('Total digital f64', total, '-'),
                       ('Total digital f32', quantized, '-.')]
            for label, value, style in curves:
                mag.semilogx(f, 20*np.log10(np.maximum(abs(value), 1e-15)), style, label=label)
                phase.semilogx(f, np.unwrap(np.angle(value))*180/np.pi, style, label=label)
            mag.set_ylabel('Magnitude / dB')
            phase.set_ylabel('Phase / deg')
            phase.set_xlabel('Frequency / Hz')
            for ax in (mag, phase):
                ax.grid(True, which='both', alpha=0.3)
            mag.legend(ncol=3, fontsize=8)
            self.figure.tight_layout()
            self.canvas.draw_idle()
            self.coefficients.setPlainText(coefficient_json(cfg))
            self.code.setPlainText(render_quasi_pr_c99(cfg))
            deviation = float(np.max(abs(quantized-total))/max(np.max(abs(total)), 1e-30))
            self.status.setText(f'float32 极点检查通过 | Ts={1e6/cfg.sample_rate_hz:.3f} μs\n'
                                f'频响最大绝对差 / 总峰值 = {deviation:.3%}\n'
                                '窄带 DFII 仍需检查长时间运行舍入误差；此处不代表闭环稳定性。')
            self.config = cfg
            self.export_button.setEnabled(True)
            self.json_button.setEnabled(True)
            self.export_state_changed.emit(True)
        except (ValueError, OverflowError) as exc:
            self.status.setText('参数无效：' + str(exc))
            self.coefficients.clear()
            self.code.clear()
            self._clear_plot()
            self.canvas.draw_idle()

    def _export(self, json=False):
        self.recalculate()
        if self.config is None:
            return
        config = self.config
        generation = self._config_generation
        suffix = 'json' if json else 'h'
        path, _ = QFileDialog.getSaveFileName(self, '导出多谐波准 PR', f'multi_pr.{suffix}', f'*.{suffix}')
        if (not path or generation != self._config_generation
                or self.config is not config):
            return
        target = Path(path)
        if target.suffix.lower() != '.' + suffix:
            target = target.with_suffix('.' + suffix)
        try:
            target.write_text(coefficient_json(config) if json else render_quasi_pr_c99(config), encoding='utf-8')
            self.status.setText(f'已导出 {target}')
        except OSError as exc:
            self.status.setText(f'导出失败：{exc}')

    def export_c99(self):
        self._export()

    def export_json(self):
        self._export(json=True)

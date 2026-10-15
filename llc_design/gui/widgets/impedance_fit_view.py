"""Measured small-signal choke model fitting; explicit CSV units and diagnostics."""
from pathlib import Path

import numpy as np
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)

from llc_design.magnetics.impedance_fit import fit_choke_impedance, load_impedance_csv


class ImpedanceFitDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('磁性元件 — 实测复阻抗拟合')
        self.resize(1050, 800)
        self.result = None
        root = QVBoxLayout(self)
        note = QLabel('导入 CSV 列：frequency_hz,real_ohm,imag_ohm\n'
                      '拟合 (Rs + sL) ∥ Rp ∥ Cp，同时使用实部与虚部。仅用于测量条件下的小信号模型。')
        note.setWordWrap(True)
        root.addWidget(note)
        actions = QHBoxLayout()
        load = QPushButton('导入并拟合 CSV')
        load.clicked.connect(self.import_csv)
        actions.addWidget(load)
        self.export = QPushButton('导出 JSON + SPICE')
        self.export.clicked.connect(self.export_model)
        self.export.setEnabled(False)
        actions.addWidget(self.export)
        root.addLayout(actions)
        self.summary = QPlainTextEdit()
        self.summary.setReadOnly(True)
        self.summary.setMaximumHeight(190)
        root.addWidget(self.summary)
        self.figure = Figure(figsize=(10, 5))
        self.canvas = FigureCanvasQTAgg(self.figure)
        root.addWidget(self.canvas, 1)

    def _clear_plot(self):
        # Shared log axes can warn while Figure.clear resets their limits.
        for axis in self.figure.axes:
            axis.set_xscale('linear')
            axis.set_yscale('linear')
        self.figure.clear()

    def import_csv(self):
        path, _ = QFileDialog.getOpenFileName(self, '导入复阻抗数据', '', 'CSV (*.csv)')
        if path:
            self.fit_file(path)

    def fit_file(self, path):
        self.result = None
        self.export.setEnabled(False)
        self._clear_plot()
        try:
            f, z = load_impedance_csv(path)
            result = fit_choke_impedance(f, z)
            predicted = result.model.impedance(f)
            magnitude, phase, residual = self.figure.subplots(3, 1, sharex=True)
            magnitude.loglog(f, abs(z), '.', label='Measured')
            magnitude.loglog(f, abs(predicted), label='Fitted')
            magnitude.set_ylabel('|Z| / ohm')
            magnitude.legend()
            phase.semilogx(f, np.angle(z, deg=True), '.')
            phase.semilogx(f, np.angle(predicted, deg=True))
            phase.set_ylabel('Phase / deg')
            residual.semilogx(f, 100*abs(predicted-z)/abs(z))
            residual.set_ylabel('Residual / %')
            residual.set_xlabel('Frequency / Hz')
            for ax in (magnitude, phase, residual):
                ax.grid(True, which='both', alpha=0.3)
            self.figure.tight_layout()
            self.summary.setPlainText(result.to_json())
            self.result = result
            self.export.setEnabled(True)
        except (ValueError, OSError) as exc:
            self.summary.setPlainText(f'拟合失败：{exc}')
        self.canvas.draw_idle()

    def export_model(self):
        if self.result is None:
            return
        path, _ = QFileDialog.getSaveFileName(self, '导出拟合模型', 'choke_fit.json', 'JSON (*.json)')
        if not path:
            return
        target = Path(path).with_suffix('.json')
        spice = target.with_suffix('.cir')
        # A selected JSON filename must not silently overwrite a second file.
        if spice.exists():
            from PySide6.QtWidgets import QMessageBox
            answer = QMessageBox.question(self, '覆盖 SPICE 文件？', str(spice))
            if answer != QMessageBox.StandardButton.Yes:
                return
        try:
            target.write_text(self.result.to_json(), encoding='utf-8')
            spice.write_text(self.result.model.spice(), encoding='utf-8')
            self.summary.appendPlainText(f'已导出：{target}\n{spice}')
        except OSError as exc:
            self.summary.appendPlainText(f'导出失败：{exc}')

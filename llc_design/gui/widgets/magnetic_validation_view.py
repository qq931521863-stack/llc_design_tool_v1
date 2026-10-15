"""User-facing measured-data comparison and explicitly scoped 3C90 inference."""

import json
from pathlib import Path

import numpy as np
from PySide6.QtWidgets import (
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ...magnetics.validated_surrogate import ValidatedMagneticSurrogate
from ...magnetics.validation import compare_measurements


class MagneticValidationView(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.snapshot = None
        root = QVBoxLayout(self)
        note = QLabel(
            "实测校核：导入正弦激励、磁芯单独损耗密度 CSV，列名 material_key,frequency_hz,temperature_c,b_peak_t,measured_pv_w_m3。整机损耗不能当作磁芯损耗。"
        )
        note.setWordWrap(True)
        root.addWidget(note)
        button = QPushButton("导入实测 / 厂商基准 CSV 并对比")
        button.clicked.connect(self.measurements)
        root.addWidget(button)
        note = QLabel(
            "可选 AI：仅限 3C90、1024 点等间隔周期 B[T] 波形。独立 70°C 留出集验证，不代表本机实测、其他材料或任意波形可靠性。禁止跨材料和越界外推。"
        )
        note.setWordWrap(True)
        root.addWidget(note)
        form = QFormLayout()
        root.addLayout(form)
        self.frequency = QDoubleSpinBox()
        self.frequency.setRange(1, 1e7)
        self.frequency.setValue(100000)
        form.addRow("3C90 波形频率 / Hz", self.frequency)
        self.temperature = QDoubleSpinBox()
        self.temperature.setRange(-50, 200)
        self.temperature.setValue(70)
        form.addRow("磁芯温度 / °C", self.temperature)
        button = QPushButton("导入单列 B[T] CSV，计算 3C90 Pv")
        button.clicked.connect(self.predict)
        root.addWidget(button)
        self.output = QPlainTextEdit()
        self.output.setReadOnly(True)
        root.addWidget(self.output, 1)
        self.export_button = QPushButton("导出校核 / 预测快照 JSON")
        self.export_button.clicked.connect(self.export)
        self.export_button.setEnabled(False)
        root.addWidget(self.export_button)
        self.frequency.valueChanged.connect(self._prediction_inputs_changed)
        self.temperature.valueChanged.connect(self._prediction_inputs_changed)

    def measurements(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "导入磁芯实测 CSV", "", "CSV (*.csv)"
        )
        if not path:
            return
        self._invalidate()
        try:
            self._display(compare_measurements(path))
        except (ValueError, OSError) as exc:
            self.output.setPlainText(str(exc))

    def predict(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "导入 1024 点 B 波形（不重复周期端点）", "", "CSV (*.csv)"
        )
        if not path:
            return
        self._invalidate()
        try:
            b = np.loadtxt(path, delimiter=",")
            if b.ndim != 1:
                raise ValueError("需要单列 1024 点 B[T] 波形，无表头。")
            model = ValidatedMagneticSurrogate()
            f, t = self.frequency.value(), self.temperature.value()
            pv = model.predict(b, f, t, material_grade="3C90")[0]
            self._display(
                {
                    "source_file": Path(path).name,
                    "material_grade": "3C90",
                    "frequency_hz": f,
                    "temperature_c": t,
                    "b_peak_t": float(max(abs(b))),
                    "pv_w_m3": float(pv),
                    "domain": model.data["domain"],
                    "evidence": "Public dataset validation only",
                    "warning": "No winding-loss, saturation, DC-bias or cross-material prediction",
                }
            )
        except (ValueError, OSError) as exc:
            self.output.setPlainText(str(exc))

    def _invalidate(self):
        self.snapshot = None
        self.export_button.setEnabled(False)

    def _prediction_inputs_changed(self, *_):
        if self.snapshot is not None and "pv_w_m3" in self.snapshot:
            self._invalidate()
            self.output.setPlainText(
                "Waveform inputs changed. Run the prediction again before exporting."
            )

    def _display(self, data):
        self.snapshot = data
        self.export_button.setEnabled(True)
        self.output.setPlainText(json.dumps(data, indent=2, ensure_ascii=False))

    def export(self):
        if self.snapshot is None:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "导出磁芯校核", "magnetic_validation.json", "JSON (*.json)"
        )
        if path:
            try:
                Path(path).write_text(
                    json.dumps(self.snapshot, indent=2, ensure_ascii=False),
                    encoding="utf-8",
                )
            except OSError as exc:
                self.output.setPlainText(str(exc))

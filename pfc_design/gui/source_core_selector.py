"""Source-backed selector embedded in the existing PFC magnetic editor."""
from __future__ import annotations

import json

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QFormLayout,
    QLabel, QLineEdit, QPlainTextEdit, QPushButton, QSpinBox, QVBoxLayout, QWidget)

from pfc_design.magnetics.manufacturer_catalog import (SourceCoreSelection,
    reference_evaluation, source_pfc_components, validate_model_declaration)
from llc_design.user_messages import show_operation_issue


class SourceCoreSelector(QWidget):
    selection_changed = Signal()
    reference_completed = Signal(object)

    def __init__(self, catalog, parent=None):
        super().__init__(parent)
        self.catalog = catalog
        self.selection = None
        self.declaration = None
        self.reference_result = None
        root = QVBoxLayout(self)
        self.search = QLineEdit(self)
        self.search.setPlaceholderText("搜索原文型号、尺寸代码、材料、页码")
        root.addWidget(self.search)
        self.kind = QComboBox(self)
        self.kind.addItem("原文明示型号", "explicit_part")
        self.kind.addItem("尺寸 / μ / AL 可用性（不编造型号）", "availability")
        self.kind.addItem("纯几何记录", "geometry")
        self.kind.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.kind.setMinimumContentsLength(12)
        root.addWidget(self.kind)
        self.parts = QComboBox(self)
        self.parts.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.parts.setMinimumContentsLength(12)
        root.addWidget(self.parts)
        self.details = QPlainTextEdit(self)
        self.details.setReadOnly(True)
        self.details.setMaximumHeight(210)
        root.addWidget(self.details)
        form = QFormLayout()
        self.turns = QSpinBox(self); self.turns.setRange(1, 10000); self.turns.setValue(20)
        self.stack = QSpinBox(self); self.stack.setRange(1, 100); self.stack.setValue(1)
        self.h = QLineEdit(self); self.h.setPlaceholderText("可选；原图范围内 H [Oe]")
        self.b = QLineEdit(self); self.b.setPlaceholderText("可选；原式 B [kG]，峰值约定未知")
        self.frequency = QLineEdit(self); self.frequency.setPlaceholderText("与 B 同填；f [kHz]")
        for label, widget in (("参考匝数", self.turns), ("相同磁环叠加数", self.stack),
                              ("DC bias 原式输入", self.h), ("损耗原式 B", self.b), ("损耗原式频率", self.frequency)):
            form.addRow(label, widget)
        root.addLayout(form)
        self.reference_button = QPushButton("计算零偏磁 / 公式参考值", self)
        self.reference_button.clicked.connect(self.calculate_reference)
        root.addWidget(self.reference_button)
        self.reference_output = QPlainTextEdit(self)
        self.reference_output.setReadOnly(True)
        self.reference_output.setMaximumHeight(150)
        root.addWidget(self.reference_output)
        self.declaration_button = QPushButton("用户模型声明…", self)
        self.declaration_button.clicked.connect(self.edit_declaration)
        root.addWidget(self.declaration_button)
        self.primary_reference_button = QPushButton("主目录材料 / 原式 / 冲突…", self)
        self.primary_reference_button.clicked.connect(lambda: self.open_supplemental(primary=True))
        root.addWidget(self.primary_reference_button)
        self.supplemental_button = QPushButton("其他 PDF 资料 / 案例…", self)
        self.supplemental_button.clicked.connect(self.open_supplemental)
        root.addWidget(self.supplemental_button)
        self.search.textChanged.connect(self.refresh)
        self.kind.currentIndexChanged.connect(self.refresh)
        self.parts.currentIndexChanged.connect(self._select)
        for widget in (self.turns, self.stack):
            widget.valueChanged.connect(self._reference_changed)
        for widget in (self.h, self.b, self.frequency):
            widget.textChanged.connect(self._reference_changed)
        self.refresh()

    def refresh(self):
        previous = self.parts.currentData()
        words = self.search.text().casefold().split()
        self.parts.blockSignals(True); self.parts.clear()
        for key, record in self.catalog.records.items():
            if self.catalog.record_types[key] != self.kind.currentData():
                continue
            text = json.dumps(record, ensure_ascii=False).casefold()
            if not all(word in text for word in words):
                continue
            name = record.get("part_number") or record.get("geometry_code") or key
            label = f"{name} · {record.get('material', '?')} · μ={record.get('ui', '?')} · AL={record.get('al_nH_per_t2', 'UNKNOWN')}"
            self.parts.addItem(label, key)
        index = self.parts.findData(previous)
        if index >= 0:
            self.parts.setCurrentIndex(index)
        self.parts.blockSignals(False)
        self._select()

    def _select(self):
        key = self.parts.currentData()
        self.selection = self.catalog.select(key) if key else None
        self.declaration = None
        self._reference_changed()
        self._show_selection()
        self.selection_changed.emit()

    def set_snapshot(self, snapshot, declaration=None):
        selection = SourceCoreSelection(snapshot)
        if declaration is not None:
            validate_model_declaration(declaration)
        self.parts.blockSignals(True)
        self.parts.clear()
        self.parts.addItem("[保存的来源快照] " + selection.label, selection.record_id)
        self.parts.blockSignals(False)
        self.selection = selection
        self.declaration = declaration
        self._reference_changed()
        self._show_selection()
        self.selection_changed.emit()

    def _show_selection(self):
        if self.selection is None:
            self.details.setPlainText("没有符合筛选条件的来源记录")
            self.reference_button.setEnabled(False)
            return
        selected = self.selection
        self.reference_button.setEnabled(True)
        known = {"record": selected.record, "geometry": selected.geometry}
        text = selected.label + "\n" + selected.record_id + "\n" + "\n".join(selected.provenance)
        text += "\n\n" + json.dumps(known, ensure_ascii=False, indent=2)
        text += "\n\n完整 PFC 模型未知项：\n" + "\n".join(selected.full_pfc_blockers())
        if self.declaration:
            text += "\n\n用户声明（不是厂家限定）：\n" + json.dumps(self.declaration, ensure_ascii=False, indent=2)
        if selected.snapshot.get("catalog_conflicts"):
            text += "\n\n原文冲突：\n" + json.dumps(selected.snapshot["catalog_conflicts"], ensure_ascii=False, indent=2)
        self.details.setPlainText(text)

    def _reference_changed(self):
        self.reference_result = None
        self.reference_output.clear()
        self.selection_changed.emit()

    def reference_inputs(self):
        def optional(widget):
            return float(widget.text()) if widget.text().strip() else None
        return {"turns": self.turns.value(), "n_cores": self.stack.value(),
                "h_oe": optional(self.h), "b_kg": optional(self.b), "f_khz": optional(self.frequency)}

    def set_reference_inputs(self, inputs):
        inputs = inputs or {}
        self.turns.setValue(inputs.get("turns", 20)); self.stack.setValue(inputs.get("n_cores", 1))
        for key, widget in (("h_oe", self.h), ("b_kg", self.b), ("f_khz", self.frequency)):
            widget.setText(str(inputs[key]) if inputs.get(key) is not None else "")

    def calculate_reference(self):
        if self.selection is None:
            return
        try:
            self.reference_result = reference_evaluation(self.selection, **self.reference_inputs())
            self.reference_output.setPlainText(json.dumps(self.reference_result, ensure_ascii=False, indent=2))
            self.reference_completed.emit(self.reference_result)
        except ValueError as exc:
            self.reference_result = None
            self.reference_output.setPlainText(str(exc))

    def pfc_blocker(self):
        if self.selection is None:
            return "未选择磁芯"
        if self.declaration is None:
            return "PFC 满载估算未启用：请查看未知项，必要时录入有依据的用户模型声明"
        try:
            source_pfc_components(self.selection, self.declaration)
        except (ValueError, KeyError) as exc:
            return str(exc)
        return ""

    def open_supplemental(self, _checked=False, *, primary=False):
        from .source_reference_view import SourceReferenceView
        dialog = QDialog(self)
        dialog.setWindowTitle("主目录材料与公式" if primary else "补充磁芯来源资料（独立记录）")
        dialog.resize(1000, 720)
        layout = QVBoxLayout(dialog)
        layout.addWidget(SourceReferenceView(dialog, primary=primary))
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, dialog)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        dialog.exec()
        dialog.deleteLater()

    def edit_declaration(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("用户模型声明（默认 UNKNOWN；不覆盖原厂数据）")
        root = QVBoxLayout(dialog)
        note = QLabel("仅填写你有依据确认的条件。图轴不是厂家适用域；用户声明将随工程与报告保存。此路径是工程参考估算。", dialog)
        note.setWordWrap(True); root.addWidget(note)
        form = QFormLayout(); root.addLayout(form)
        choices = {}
        for key, label, value, display in (
            ("B_convention", "损耗公式 B 约定", "peak_ac", "确认：AC 峰值，非峰峰值"),
            ("waveform", "波形解释", "sinusoidal_reference", "确认：正弦参考式用于 PFC 近似，无波形修正"),
            ("dimension_basis", "几何依据", "after_coating_limits", "确认：涂层后 OD/HT 最大值、ID 最小值估算")):
            widget = QComboBox(dialog); widget.addItem("UNKNOWN", None); widget.addItem(display, value)
            form.addRow(label, widget); choices[key] = widget
        entries = {}
        for key, label in (("H_Oe", "H 范围 [Oe]：min,max"), ("B_T", "AC 峰值 B 范围 [T]：min,max"),
                           ("frequency_Hz", "频率范围 [Hz]：min,max"), ("temperature_C", "温度范围 [°C]：min,max"),
                           ("core_temperature_C", "本次磁芯温度 [°C]"), ("source", "声明依据 / 来源")):
            widget = QLineEdit(dialog); widget.setPlaceholderText("UNKNOWN；必须由用户输入")
            entries[key] = widget; form.addRow(label, widget)
        if self.declaration:
            for key, widget in choices.items(): widget.setCurrentIndex(widget.findData(self.declaration[key]))
            for key, widget in entries.items():
                value = self.declaration[key]
                widget.setText(",".join(map(str, value)) if isinstance(value, list) else str(value))
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel, dialog)
        root.addWidget(buttons); buttons.accepted.connect(dialog.accept); buttons.rejected.connect(dialog.reject)
        accepted = dialog.exec() == QDialog.DialogCode.Accepted
        if accepted:
            try:
                declaration = {key: widget.currentData() for key, widget in choices.items()}
                declaration.update({key: [float(v.strip()) for v in entries[key].text().split(",")] for key in ("H_Oe", "B_T", "frequency_Hz", "temperature_C")})
                declaration["source"] = entries["source"].text()
                declaration["core_temperature_C"] = float(entries["core_temperature_C"].text())
                self.declaration = validate_model_declaration(declaration)
                self._show_selection(); self.selection_changed.emit()
            except ValueError as exc:
                show_operation_issue(self, "用户模型声明无效", str(exc), critical=False)
        dialog.deleteLater()

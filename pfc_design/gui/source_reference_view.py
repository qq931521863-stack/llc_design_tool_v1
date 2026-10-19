"""Search the three companion PDFs separately from ready-to-calculate parts."""
import json

from PySide6.QtWidgets import QComboBox, QLabel, QLineEdit, QPlainTextEdit, QPushButton, QVBoxLayout, QWidget
from pfc_design.magnetics.supplemental_catalog import load_supplemental_catalog, replay_pfc_document_example, PFC_CASE_ENGINEERING_REVIEW


class SourceReferenceView(QWidget):
    def __init__(self, parent=None, *, primary=False):
        super().__init__(parent)
        self.primary = primary
        if primary:
            from pfc_design.magnetics.manufacturer_catalog import ManufacturerCoreCatalog
            self.data = ManufacturerCoreCatalog().data
            sections = ("material_profiles", "material_models", "chart_references", "conflicts", "raw_special_tables", "raw_winding_tables", "application_examples")
        else:
            self.data = load_supplemental_catalog()
            sections = ("material_profiles", "part_records", "performance_points", "model_records", "application_cases",
                        "shape_families", "chart_only_part_references", "roadmap_targets", "unresolved_issues")
        self.rows = []
        for section in sections:
            for row in self.data.get(section, []):
                self.rows.append((section, row))
        root = QVBoxLayout(self)
        note = QLabel("主目录材料 / 原式 / 图线 / 冲突独立可查；不混同图示范围与厂家适用域。" if primary else "三份补充 PDF 独立保留：材料描述、原文型号、性能点、案例和冲突。不会把跨厂商引用、路线图目标或图示比较自动变成量产磁芯预设。", self)
        note.setWordWrap(True); root.addWidget(note)
        self.search = QLineEdit(self); self.search.setPlaceholderText("搜索文件名、型号、材料或案例，例如 NPH157060 / APP-023")
        root.addWidget(self.search)
        self.records = QComboBox(self); root.addWidget(self.records)
        self.text = QPlainTextEdit(self); self.text.setReadOnly(True); root.addWidget(self.text, 1)
        self.replay = QPushButton("按原文复算 120 kHz / 1 kW 案例（非适用域认证）", self)
        self.replay.clicked.connect(self._replay); root.addWidget(self.replay)
        self.search.textChanged.connect(self.refresh)
        self.records.currentIndexChanged.connect(self._select)
        self.refresh()

    def refresh(self):
        words = self.search.text().casefold().split()
        self.records.blockSignals(True); self.records.clear()
        for index, (section, row) in enumerate(self.rows):
            if not all(w in json.dumps(row, ensure_ascii=False).casefold() for w in words): continue
            label = row.get("id") or row.get("record_id") or row.get("conflict_id") or row.get("issue_id") or row.get("family") or section
            self.records.addItem(f"{section} · {label} · {row.get('part_number') or row.get('material') or row.get('title', '')}", index)
        self.records.blockSignals(False); self._select()

    def _select(self):
        index = self.records.currentData()
        if index is None:
            self.text.clear(); self.replay.setEnabled(False); return
        section, row = self.rows[index]
        payload = {"section": section, "record": row, "source_documents": self.data.get("documents", self.data.get("source_documents"))}
        if row.get("id") == "APP-023":
            payload["software_engineering_review"] = PFC_CASE_ENGINEERING_REVIEW
        self.text.setPlainText(json.dumps(payload, ensure_ascii=False, indent=2))
        self.replay.setEnabled(row.get("id") == "APP-023")

    def _replay(self):
        result = replay_pfc_document_example(self.data)
        self.text.setPlainText(json.dumps(result, ensure_ascii=False, indent=2))

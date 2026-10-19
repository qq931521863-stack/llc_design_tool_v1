"""Search ready core/bobbin presets and the separately retained legacy catalog."""

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
)

from ...magnetics.catalog import load_magnetic_catalog
from ...magnetics.transformer_designer import load_transformer_core_presets


class MagneticCatalogDialog(QDialog):
    preset_selected = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("变压器磁芯 / 骨架数据库")
        self.resize(1150, 720)
        root = QVBoxLayout(self)
        self.search = QLineEdit()
        self.search.setPlaceholderText("搜索型号、材料、形状或来源")
        root.addWidget(self.search)
        self.note = QLabel(
            "选择完整磁芯/骨架组合可直接应用。历史表保留原始参数，冲突值不会自动覆盖已核对数据。"
        )
        self.note.setWordWrap(True)
        root.addWidget(self.note)
        self.tabs = QTabWidget()
        root.addWidget(self.tabs, 1)
        self.ready = self._table(
            [
                "型号",
                "材料",
                "Ae mm²",
                "Ve mm³",
                "AL nH",
                "骨架 Aw mm²",
                "MLT mm",
                "来源",
            ]
        )
        self.reference = self._table(
            [
                "型号",
                "材料",
                "Ae mm²",
                "Ve mm³",
                "AL nH",
                "磁芯窗口 mm²",
                "原表位置",
                "数据检查",
            ]
        )
        self.tabs.addTab(self.ready, "完整设计预设")
        self.tabs.addTab(self.reference, "历史参数参考")
        from pfc_design.magnetics.core_database import CoreDatabase
        self.pdf_catalog = CoreDatabase(include_user=False).source_catalog
        self.pdf_reference = self._table(["记录类型", "原文型号 / 几何代码", "材料", "μ", "AL nH/T²", "Ae cm²", "Le cm", "Ve cm³", "来源"])
        self.tabs.addTab(self.pdf_reference, "CSC PDF 来源数据")
        self.pdf_reference.itemSelectionChanged.connect(self._pdf_selected)
        from pfc_design.gui.source_reference_view import SourceReferenceView
        self.supplemental = SourceReferenceView(self)
        self.tabs.addTab(self.supplemental, "其他 PDF 材料 / 案例")
        from .magnetic_validation_view import MagneticValidationView

        self.validation = MagneticValidationView()
        self.tabs.addTab(self.validation, "实测校核 / 可选 AI")
        self.apply = QPushButton("应用选中的磁芯 / 骨架组合")
        self.apply.clicked.connect(self._apply)
        root.addWidget(self.apply)
        self.search.textChanged.connect(self.refresh)
        self.tabs.currentChanged.connect(self._update_apply)
        self.ready.itemSelectionChanged.connect(self._update_apply)
        self.refresh()

    @staticmethod
    def _table(labels):
        table = QTableWidget(0, len(labels))
        table.setHorizontalHeaderLabels(labels)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setAlternatingRowColors(True)
        return table

    def refresh(self):
        query = self.search.text().casefold().strip()
        self.keys = []
        self.ready.setRowCount(0)
        for key, p in load_transformer_core_presets().items():
            values = [
                p.part_number,
                p.material_grade,
                p.ae_mm2,
                p.ve_mm3,
                p.al_nh,
                p.winding_area_mm2,
                p.mean_turn_length_mm,
                p.source,
            ]
            if query not in " ".join(map(str, values)).casefold():
                continue
            self.keys.append(key)
            self._append(self.ready, values)
        self.reference.setRowCount(0)
        for p in load_magnetic_catalog()["records"]:
            values = [
                p["shape"],
                p["material_grade"],
                p["ae_mm2"],
                p["ve_mm3"],
                p["al_nh"],
                p["core_window_mm2"],
                f"{p['source_sheet']}:{p['source_row']}",
                "; ".join(p["issues"]) or "历史参考；需匹配骨架和材料模型",
            ]
            if query in " ".join(map(str, values)).casefold():
                self._append(self.reference, values)
        from pfc_design.magnetics.manufacturer_catalog import source_text
        self.pdf_reference.setRowCount(0)
        self.pdf_keys = []
        for key, record in self.pdf_catalog.records.items():
            geometry = self.pdf_catalog.geometries.get(record.get("geometry_id"), record)
            kind = self.pdf_catalog.record_types[key]
            values = [kind, record.get("part_number") or record.get("geometry_code") or key,
                      record.get("material", "UNKNOWN"), record.get("ui", "UNKNOWN"),
                      record.get("al_nH_per_t2", "UNKNOWN"), geometry.get("ae_cm2", "UNKNOWN"),
                      geometry.get("le_cm", "UNKNOWN"), geometry.get("ve_cm3", "UNKNOWN"),
                      source_text(record.get("source") or {})]
            if query in " ".join(map(str, values)).casefold():
                self.pdf_keys.append(key)
                self._append(self.pdf_reference, ["UNKNOWN" if v is None else v for v in values])
        self.tabs.setTabText(2, f"CSC PDF 来源数据 ({self.pdf_reference.rowCount()})")
        for table in (self.ready, self.reference, self.pdf_reference):
            table.resizeColumnsToContents()
        self.tabs.setTabText(0, f"完整设计预设 ({self.ready.rowCount()})")
        self.tabs.setTabText(1, f"历史参数参考 ({self.reference.rowCount()})")
        self._update_apply()

    @staticmethod
    def _append(table, values):
        row = table.rowCount()
        table.insertRow(row)
        for col, value in enumerate(values):
            table.setItem(row, col, QTableWidgetItem(str(value)))

    def _pdf_selected(self):
        row = self.pdf_reference.currentRow()
        if not hasattr(self, "pdf_keys") or not 0 <= row < len(self.pdf_keys):
            return
        selected = self.pdf_catalog.select(self.pdf_keys[row])
        self.note.setText(
            selected.label + "\n" + "\n".join(selected.provenance)
            + "\n粉芯 / 几何来源记录不是完整 LLC 磁芯-骨架预设；不能自动代入铁氧体材料或骨架。"
            + "电感零偏磁及原厂公式参考计算请从 PFC → 电感设计 → CSC PDF 来源磁环进入。"
        )

    def _update_apply(self):
        self.apply.setEnabled(
            self.tabs.currentIndex() == 0 and self.ready.currentRow() >= 0
        )

    def _apply(self):
        row = self.ready.currentRow()
        if self.tabs.currentIndex() == 0 and 0 <= row < len(self.keys):
            self.preset_selected.emit(self.keys[row])
            self.accept()

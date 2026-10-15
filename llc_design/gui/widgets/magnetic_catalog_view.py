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
        for table in (self.ready, self.reference):
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

    def _update_apply(self):
        self.apply.setEnabled(
            self.tabs.currentIndex() == 0 and self.ready.currentRow() >= 0
        )

    def _apply(self):
        row = self.ready.currentRow()
        if self.tabs.currentIndex() == 0 and 0 <= row < len(self.keys):
            self.preset_selected.emit(self.keys[row])
            self.accept()

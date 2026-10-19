"""Explicit, bounded LLC candidate selection; selecting never applies a design."""
from __future__ import annotations

import json
from dataclasses import asdict

from PySide6.QtCore import Signal, Qt, QSignalBlocker
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QFormLayout, QHBoxLayout, QHeaderView, QLabel,
    QPlainTextEdit, QPushButton, QSpinBox, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)

from ...optimization.sweep import OptimizationConfig
from ...optimization.two_stage import DEFAULT_WEIGHTS


class TwoStageOptimizationView(QWidget):
    run_requested = Signal()
    cancel_requested = Signal()
    apply_requested = Signal()
    report_requested = Signal()
    inputs_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.result = None
        self.request = None
        self.historical = None
        self._busy = False
        self._valid = False
        layout = QVBoxLayout(self)
        note = QLabel("Stage A: FHA 筛选 → Stage B: 最多 4 × Top N 候选的 TD 波形损耗与模型/ZVS 验证。\n"
                      "选择行只预览；Apply 才改变工程。未知/失败候选不可应用。TD 损耗保留 FHA 换流/ZVS 与硬件选型假设。\n"
                      "仅支持 AUTO_DESIGN 的 Ln/Q/fr 优化；其他工程参数保持原始精度。取消在当前候选计算结束后释放工作线程。")
        note.setWordWrap(True)
        layout.addWidget(note)
        form = QFormLayout()
        self.config_text = QPlainTextEdit()
        self.config_text.setMaximumHeight(160)
        form.addRow("搜索轴 JSON（Hz；整数匝数）", self.config_text)
        self.weights_text = QPlainTextEdit(json.dumps(DEFAULT_WEIGHTS, indent=2))
        self.weights_text.setMaximumHeight(95)
        form.addRow("排序权重（较大评分优先）", self.weights_text)
        bounds = QHBoxLayout()
        self.maximum_candidates = QSpinBox(); self.maximum_candidates.setRange(1, 128); self.maximum_candidates.setValue(12)
        self.top_n = QSpinBox(); self.top_n.setRange(1, 8); self.top_n.setValue(3)
        bounds.addWidget(QLabel("Stage A 上限")); bounds.addWidget(self.maximum_candidates)
        bounds.addWidget(QLabel("Top N")); bounds.addWidget(self.top_n); bounds.addStretch(1)
        form.addRow(bounds)
        self.verification_scope = QComboBox()
        self.verification_scope.addItem("仅名义点 TD 损耗验证（其他工况保留 FHA）", "nominal")
        self.verification_scope.addItem("全部关键工况 TD 验证", "critical")
        form.addRow("TD 验证范围", self.verification_scope)
        layout.addLayout(form)
        actions = QHBoxLayout()
        self.run_button = QPushButton("运行 Stage A → B")
        self.cancel_button = QPushButton("取消 / 丢弃")
        self.apply_button = QPushButton("Apply 所选候选")
        self.report_button = QPushButton("导出所选候选报告")
        for button, signal in ((self.run_button, self.run_requested), (self.cancel_button, self.cancel_requested),
                               (self.apply_button, self.apply_requested), (self.report_button, self.report_requested)):
            button.clicked.connect(lambda _checked=False, signal=signal: signal.emit()); actions.addWidget(button)
        layout.addLayout(actions)
        self.status = QLabel("尚未运行")
        self.status.setWordWrap(True); layout.addWidget(self.status)
        headers = ("状态", "Ln", "Q", "fr / kHz", "Np:Ns", "器件 / SR", "验证点最坏损耗 W", "验证点最低效率 %", "ZVS 裕量", "FHA↔TD", "原因 / 来源")
        self.table = QTableWidget(0, len(headers)); self.table.setHorizontalHeaderLabels(headers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSortingEnabled(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.itemSelectionChanged.connect(self._selection_changed)
        layout.addWidget(self.table, 1)
        self.details = QPlainTextEdit(); self.details.setReadOnly(True); self.details.setMaximumHeight(230)
        layout.addWidget(self.details)
        for widget in (self.config_text, self.weights_text):
            widget.textChanged.connect(self.inputs_changed.emit)
        for widget in (self.maximum_candidates, self.top_n):
            widget.valueChanged.connect(lambda _value: self.inputs_changed.emit())
        self.verification_scope.currentIndexChanged.connect(lambda _index: self.inputs_changed.emit())
        self._refresh_buttons()

    def set_initial_spec(self, spec):
        config = OptimizationConfig(
            ln_values=(spec.ln_ratio * 0.9, spec.ln_ratio, spec.ln_ratio * 1.1),
            q_values=(spec.q_full_load,), fr_values_hz=(spec.resonant_frequency_hz,),
            primary_turn_values=(spec.primary_turns,), secondary_turn_values=(spec.secondary_turns,),
            primary_devices=(spec.primary_device,), sr_parallel_values=(spec.sr_parallel_devices_per_position,))
        with QSignalBlocker(self.config_text):
            self.config_text.setPlainText(json.dumps(asdict(config), indent=2, ensure_ascii=False))

    def options(self):
        from ...control.design_session import strict_json_loads
        config = strict_json_loads(self.config_text.toPlainText())
        weights = strict_json_loads(self.weights_text.toPlainText())
        return {"config": config, "ranking_weights": weights,
                "maximum_candidates": self.maximum_candidates.value(), "top_n": self.top_n.value(),
                "verification_scope": self.verification_scope.currentData()}

    def restore_options(self, request):
        with QSignalBlocker(self.verification_scope):
            self.verification_scope.setCurrentIndex(self.verification_scope.findData(request["verification_scope"]))
        for widget, value in ((self.config_text, json.dumps(request["config"], indent=2)),
                              (self.weights_text, json.dumps(request["ranking_weights"], indent=2)),
                              (self.maximum_candidates, request["maximum_candidates"]), (self.top_n, request["top_n"])):
            with QSignalBlocker(widget):
                widget.setPlainText(value) if isinstance(widget, QPlainTextEdit) else widget.setValue(value)

    def invalidate(self, message="输入已改变；重新运行后才能 Apply / 导出。"):
        self._valid = False
        self.result = None
        self.status.setText(message)
        self.table.setRowCount(0)
        self.details.clear()
        self._refresh_buttons()

    def set_busy(self, busy):
        self._busy = busy
        # Inputs intentionally remain editable: an edit invalidates running work.
        self._refresh_buttons()

    def _refresh_buttons(self):
        selected = self.selected_candidate()
        self.run_button.setEnabled(not self._busy)
        self.cancel_button.setEnabled(self._busy or self._valid)
        self.apply_button.setEnabled(selected is not None and not self._busy)
        self.report_button.setEnabled(selected is not None and not self._busy)

    def selected_candidate(self):
        row = self.table.currentRow()
        if not self._valid or self.result is None or not 0 <= row < len(self.result.verified):
            return None
        candidate = self.result.verified[row]
        return candidate if not candidate.rejected and candidate.analysis is not None else None

    def _selection_changed(self):
        if self.result is not None and 0 <= self.table.currentRow() < len(self.result.verified):
            from ..optimization_install import candidate_history
            self.details.setPlainText(json.dumps(candidate_history(self.result.verified[self.table.currentRow()]),
                                                 indent=2, ensure_ascii=False))
        elif self.result is not None:
            row = self.table.currentRow()
            reason = self.table.item(row, self.table.columnCount() - 1) if row >= 0 else None
            self.details.setPlainText("Stage A failed; no Stage-B candidate.\n" + reason.text() if reason else "")
        self._refresh_buttons()

    def set_result(self, request, result):
        self.request, self.result = request, result
        self.historical = None
        self._valid = True
        rows = []
        for candidate in result.verified:
            spec = candidate.spec
            rows.append(("REJECTED" if candidate.rejected else ("仅名义TD损耗验证*" if request["verification_scope"] == "nominal" else "关键工况TD验证*"), spec.ln_ratio, spec.q_full_load,
                spec.resonant_frequency_hz / 1000, f"{spec.primary_turns}:{spec.secondary_turns}",
                f"{spec.primary_device} / {spec.sr_parallel_devices_per_position}", candidate.rank_metrics.get("total_loss_w"),
                None if "efficiency" not in candidate.rank_metrics else candidate.rank_metrics["efficiency"] * 100, candidate.zvs_margin_worst,
                candidate.fha_td_validity.value, candidate.reject_reason or "; ".join(candidate.diagnostics)))
        if not result.stage_a.table.empty:
            for _, row in result.stage_a.table[result.stage_a.table["feasible"] == False].iterrows():
                rows.append(("STAGE A FAIL", row["ln"], row["q_full"], row["fr_khz"],
                    f"{row['primary_turns']}:{row['secondary_turns']}", row["primary_device"],
                    None, None, None, "NOT RUN", row.get("failure", "FHA infeasible")))
        self.table.setRowCount(len(rows))
        for index, row in enumerate(rows):
            for column, value in enumerate(row):
                text = "UNKNOWN" if value is None else (f"{value:.8g}" if isinstance(value, float) else str(value))
                self.table.setItem(index, column, QTableWidgetItem(text))
        self.status.setText("\n".join(result.warnings) + "\n*理想 TD 损耗验证；不等同硬件签核。")
        if rows:
            self.table.selectRow(0)
        self._refresh_buttons()

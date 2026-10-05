"""Export the completed analysis, never pending widget values or candidates."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from power_control_tools.research_export import (
    build_llc_research_export,
    build_pfc_research_export,
    render_matlab,
    render_python,
)


class ExportResearchPanel(QWidget):
    """Small shared MATLAB/Python export surface for LLC and PFC results."""

    def __init__(self, kind: str, parent=None) -> None:
        super().__init__(parent)
        if kind not in ("llc", "pfc"):
            raise ValueError("Unsupported research export kind")
        self.kind = kind
        self.analysis = None
        self._busy = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(3)
        row = QHBoxLayout()
        row.addWidget(QLabel("研究模型导出"))
        if kind == "pfc":
            self.loop_selector = QComboBox()
            self.loop_selector.addItem("双环", "both")
            self.loop_selector.addItem("电流环", "current")
            self.loop_selector.addItem("电压环", "voltage")
            row.addWidget(self.loop_selector)
        self.matlab_button = QPushButton("MATLAB .m")
        self.python_button = QPushButton("Python .py")
        self.matlab_button.clicked.connect(lambda: self._export("matlab"))
        self.python_button.clicked.connect(lambda: self._export("python"))
        for button in (self.matlab_button, self.python_button):
            button.setToolTip(
                "导出最后完成的分析：精确传递函数、采样时间、频响、模型边界与自检；"
                "不读取未分析的参数或未应用的候选控制器。"
            )
            row.addWidget(button)
        row.addStretch(1)
        layout.addLayout(row)
        self.status = QLabel("请先完成环路分析，再导出模型。")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self._update_enabled()

    def set_analysis(self, analysis) -> None:
        self.analysis = analysis
        self.status.setText(
            "导出最后完成的分析快照；未重新分析的输入修改与未应用候选不包含在内。"
            if analysis is not None else "请先完成环路分析，再导出模型。"
        )
        self._update_enabled()

    def set_busy(self, busy: bool) -> None:
        self._busy = busy
        self._update_enabled()

    def _update_enabled(self) -> None:
        enabled = self.analysis is not None and not self._busy
        self.matlab_button.setEnabled(enabled)
        self.python_button.setEnabled(enabled)
        if hasattr(self, "loop_selector"):
            self.loop_selector.setEnabled(enabled)

    def _export(self, language: str) -> None:
        if self.analysis is None or self._busy:
            return
        analysis = self.analysis
        suffix = ".m" if language == "matlab" else ".py"
        loop = self.loop_selector.currentData() if self.kind == "pfc" else "voltage"
        filename, _ = QFileDialog.getSaveFileName(
            self, "导出已完成分析的研究模型",
            f"{self.kind}_{loop}_research{suffix}",
            "MATLAB (*.m)" if language == "matlab" else "Python (*.py)",
        )
        if not filename:
            return
        try:
            snapshot = (
                build_llc_research_export(analysis) if self.kind == "llc"
                else build_pfc_research_export(analysis, loop=loop)
            )
            text = render_matlab(snapshot) if language == "matlab" else render_python(snapshot)
            path = Path(filename)
            if not path.suffix:
                path = path.with_suffix(suffix)
            path.write_text(text, encoding="utf-8")
        except (OSError, TypeError, ValueError) as exc:
            QMessageBox.warning(self, "研究模型导出失败", str(exc))
            return
        QMessageBox.information(
            self, "研究模型已导出",
            f"{path}\n\n导出的是最后完成的分析快照。脚本包含模型边界、依赖说明与频响自检。",
        )

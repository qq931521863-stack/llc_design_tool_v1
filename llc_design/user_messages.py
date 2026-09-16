"""User-facing explanations; keep solver diagnostics and decisions unchanged."""

from __future__ import annotations

import re

from llc_design.i18n import current_language


def design_status(feasible: bool) -> str:
    if current_language() == "zh-Hans":
        return "计算完成｜已检查项满足当前约束" if feasible else "计算完成｜存在未满足项，请查看设计提醒"
    return "Calculation complete | checked constraints met" if feasible else "Calculation complete | review unmet constraints"


def design_reason(reason: str, *, language: str | None = None) -> str:
    """Explain known diagnostics in Chinese without losing numeric evidence."""
    if (language or current_language()) != "zh-Hans":
        return reason
    exact = {
        "installed bus capacitance does not meet requested hold-up time":
            "母线保持时间未达到设定要求。可以增大母线电容，或按实际需求调整保持时间。",
        "bus voltage falls below LLC hold-up endpoint":
            "保持期间母线电压预计低于设定末端电压。请结合母线电容与保持时间一起检查。",
        "primary MOSFET voltage derating failed":
            "原边 MOSFET 电压裕量未满足降额要求。请核对电压应力及器件耐压。",
        "SR MOSFET voltage derating failed":
            "同步整流 MOSFET 电压裕量未满足降额要求。请核对电压应力及器件耐压。",
        "Bpk exceeds 70% of temperature-adjusted saturation flux":
            "峰值磁通密度超过温度修正后饱和值的 70%。请复核磁芯、匝数及工作温度。",
        "required Lm exceeds ungapped-core estimate":
            "所需励磁电感超过无气隙磁芯的估算值。请核对磁芯、匝数和励磁电感。",
        "non-positive calculated gap":
            "估算气隙小于或等于零。请核对磁芯、匝数和目标电感的匹配关系。",
    }
    for prefix, label in (("transformer: ", "变压器："), ("resonant inductor: ", "谐振电感：")):
        if reason.startswith(prefix):
            return label + design_reason(reason[len(prefix):], language=language)
    if reason in exact:
        return exact[reason]
    patterns = (
        (r"(.+): required gain (\S+) is outside available range (\S+)", "工况 {0}：所需增益 {1} 超出当前可实现范围 {2}，此工况尚未求解。请检查变比、谐振参数与频率范围。"),
        (r"total gap (\S+) mm exceeds (\S+) mm", "估算总气隙为 {0} mm，超过当前设定上限 {1} mm。请核对磁芯、匝数、目标电感及加工可行性。"),
        (r"total gap (\S+) mm exceeds limit", "估算总气隙为 {0} mm，超过当前设定上限。请核对磁芯、匝数、目标电感及加工可行性。"),
        (r"window fill (\S+) exceeds (\S+)", "窗口填充系数为 {0}，超过设定上限 {1}。请检查绕组尺寸与磁芯窗口。"),
        (r"window fill (\S+) exceeds limit", "窗口填充系数为 {0}，超过设定上限。请检查绕组尺寸与磁芯窗口。"),
        (r"radial build (\S+) mm exceeds (\S+) mm", "绕组径向厚度为 {0} mm，超过窗口高度 {1} mm。请调整绕组方案或磁芯。"),
        (r"Bpk at minimum area (\S+) T exceeds (\S+) T", "最小截面处峰值磁通密度为 {0} T，超过设定上限 {1} T。请复核磁芯与匝数。"),
        (r"Bpk at minimum area (\S+) T exceeds limit", "最小截面处峰值磁通密度为 {0} T，超过设定上限。请复核磁芯与匝数。"),
        (r"winding requires (\S+) layers", "绕组需要 {0} 层，超过当前层数限制。请检查线材与绕组布局。"),
        (r"(.+): inductive angle is only (\S+)°", "工况 {0}：感性相角为 {1}°，未满足设定要求。请检查谐振参数与工作频率。"),
        (r"(.+): primary ZVS margin (\S+) < 1.0", "工况 {0}：原边 ZVS 裕量为 {1}，低于 1.0。当前模型下软开关条件未满足，请复核换流条件。"),
        (r"(.+): resonant capacitor voltage rating failed", "工况 {0}：谐振电容耐压不足。请检查电压应力与电容额定值。"),
        (r"(.+): resonant capacitor current rating failed", "工况 {0}：谐振电容电流额定值不足。请检查纹波电流与电容选型。"),
        (r"(.+): output ripple exceeds limit", "工况 {0}：输出纹波超过设定上限。请检查输出电容、ESR 与纹波要求。"),
        (r"(.+): transformer hotspot (\S+) °C exceeds limit", "工况 {0}：变压器估算热点温度 {1} °C 超过设定上限。请检查损耗与散热条件。"),
        (r"(.+): resonant-inductor hotspot (\S+) °C exceeds limit", "工况 {0}：谐振电感估算热点温度 {1} °C 超过设定上限。请检查损耗与散热条件。"),
    )
    for pattern, explanation in patterns:
        match = re.fullmatch(pattern, reason)
        if match:
            return explanation.format(*match.groups())
    return "此项需要进一步复核，可结合运行日志检查相关参数。原始信息：" + reason


def show_operation_issue(parent, title: str, details: str, *, critical: bool = False) -> None:
    """Keep technical exceptions available behind a readable, actionable dialog."""
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QMessageBox

    box = QMessageBox(parent)
    box.setTextFormat(Qt.TextFormat.PlainText)
    box.setIcon(QMessageBox.Icon.Critical if critical else QMessageBox.Icon.Warning)
    box.setWindowTitle(title)
    box.setText(title)
    if current_language() == "zh-Hans":
        box.setInformativeText("这次操作尚未完成。请先查看下方详细信息，检查相关参数或文件后再试；如果仍无法完成，可以把详细信息发给我们协助排查。")
    else:
        box.setInformativeText("This operation did not complete. Check the details below and review the relevant parameters or file before retrying. If the issue persists, share the details so we can help investigate.")
    box.setDetailedText(str(details))
    box.exec()

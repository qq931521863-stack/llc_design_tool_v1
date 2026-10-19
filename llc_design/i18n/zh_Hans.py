"""Simplified Chinese catalogue.

This is the **source language**: the strings written in the code are already
Simplified Chinese, so most lookups fall back to the source text. User-facing wording
refinements are kept here without changing existing translation keys.
"""

CATALOGUE: dict[str, str] = {
    "参数错误": "请检查设计参数",
    "数字环路参数错误": "请检查数字环路参数",
    "Vienna 参数错误": "请检查 Vienna 参数",
    "PFC Control Lab 参数错误": "请检查 PFC 控制参数",
    "加载失败": "暂时未能打开文件",
    "保存失败": "文件尚未保存成功",
    "计算失败": "本次计算尚未完成",
    "无法复制参数": "参数尚未复制完成",
    "C99 代码生成失败": "C99 代码尚未生成",
    "C99 导出失败": "C99 文件尚未导出",
    "FRA 导入失败": "FRA 数据尚未导入",
    "PFC 电感设计失败": "本次 PFC 电感计算尚未完成",
    "Auto Design 失败": "本次自动设计尚未完成",
    "Auto Design 未通过": "自动设计结果需要调整",
    "当前结果没有满足完整的 Fc/PM/GM/Ms 约束，禁止一键应用。":
        "本次自动设计已完成，但结果尚未满足全部 Fc/PM/GM/Ms 要求，因此暂不能一键应用。可以调整设计目标后重新计算。",
    "控制器不可导出": "控制器稳定性需要先调整",
    "当前 H(z) 含单位圆外极点。请先恢复控制器稳定性。":
        "当前 H(z) 含单位圆外极点，暂不能导出。请先调整控制器参数，确认稳定性后再试。",
    "回传失败": "模型尚未传回工作区",
    "Model Fit 失败": "本次模型拟合尚未完成",
    "当前工作区不支持": "当前工作区暂不支持此操作",
    "宿主窗口没有辨识模型回传接口。":
        "当前工作区暂不能接收辨识模型。请在支持模型回传的工作区使用此功能。",
}


# Converged time-domain semiconductor loss selection and reports.
CATALOGUE.update({
    '导出当前 TD 损耗报告': '导出当前 TD 损耗报告',
    '将收敛 TD 波形用于当前点损耗': '将收敛 TD 波形用于当前点损耗',
    'TD 波形驱动器件、磁件与电容损耗；材料/热模型仍为工程估算，换流/ZVS 与选型约束保持 FHA': 'TD 波形驱动器件、磁件与电容损耗；材料/热模型仍为工程估算，换流/ZVS 与选型约束保持 FHA',
    '将收敛 TD 电流用于当前点器件损耗': '将收敛 TD 电流用于当前点器件损耗',
    '仅更新器件电流/频率及系统损耗汇总；磁件、电容与 ZVS 仍为原工程估算': '仅更新器件电流/频率及系统损耗汇总；磁件、电容与 ZVS 仍为原工程估算',
    '请先勾选运行分段时域参考。': '请先勾选运行分段时域参考。',
    '输入已改变，请重新计算 TD 损耗后导出。': '输入已改变，请重新计算 TD 损耗后导出。',
    '正在导出同一 TD 工况的损耗与波形报告…': '正在导出同一 TD 工况的损耗与波形报告…',
    '收敛时域波形': '收敛时域波形',
    'FHA 工程估算': 'FHA 工程估算',
    '器件电流来源={source}（其余损耗保持原工程模型）': '器件电流来源={source}（其余损耗保持原工程模型）',
})


# Explicit AC convergence controls and stale-result invalidation.
CATALOGUE.update({
    'AC 收敛周期数': 'AC 收敛周期数',
    'AC 积分采样率': 'AC 积分采样率',
    '未收敛时增加 AC 周期数并重新运行；只有收敛结果可用于器件与电容损耗。': '未收敛时增加 AC 周期数并重新运行；只有收敛结果可用于器件与电容损耗。',
    '积分采样率至少为电流环更新率的 4 倍；提高采样率会增加计算量。': '积分采样率至少为电流环更新率的 4 倍；提高采样率会增加计算量。',
    'TTPL 输入已改变，请重新运行分析。': 'TTPL 输入已改变，请重新运行分析。',
})


# FRA reproducible session actions.
CATALOGUE.update({
    '保存 FRA 会话': '保存 FRA 会话',
    '打开 FRA 会话': '打开 FRA 会话',
})

CATALOGUE['严格 PASS 的自动设计结果已按完整精度 H(z) 系数应用；可继续编辑系数、保存会话或导出 C99。'] = '严格 PASS 的自动设计结果已按完整精度 H(z) 系数应用；可继续编辑系数、保存会话或导出 C99。'

CATALOGUE['系数文件已写入，但验证未通过；请勿用于控制硬件。'] = '系数文件已写入，但验证未通过；请勿用于控制硬件。'

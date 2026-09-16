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

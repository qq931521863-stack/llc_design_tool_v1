"""Chinese labels for exact toolbox results; no electrical calculations here.

Normal views expose results, missing data and model limits in everyday language.
The explicit details view retains original provenance and keys. Large maps and
curves are previewed without modifying the result or its full-fidelity exports.
"""
from __future__ import annotations

import json
from itertools import islice

from .toolbox import CalculatorResult

TITLES = {
    "X01": "FET 损耗与器件比较", "X02": "负载阶跃电容", "X03": "并联电容电流分配",
    "X04": "AC/DC 整流母线电容", "X05": "Flyback RCD 吸收", "X06": "RC 吸收",
    "X07": "输出分压与标准阻值", "X08": "模拟调压网络", "X09": "数字档位调压网络",
    "X10": "单位转换", "X12": "差模 LC / π 滤波器", "G04-hold-up": "恒功率保持时间",
}
STATUS_LABELS = {"MODELED": "计算完成", "PARTIAL": "部分数据缺失",
                 "MODELED_WITH_SOURCE_DISCREPANCY": "计算完成，参考资料存在差异"}
CHOICE_LABELS = {
    "main": "主开关", "sr": "同步整流管", "CMC": "电流模式（CMC）", "VMC": "电压模式（VMC）",
    "design": "根据目标反算电阻", "evaluate": "按实际电阻计算输出", "low": "下臂电阻", "high": "上臂电阻",
    "pi": "π 型网络", "lc": "LC 网络", "flux_density": "磁通密度", "voltage_gain": "电压 / 幅值增益",
    "power_gain": "功率增益", "length": "长度", "mass": "质量", "airflow": "风速", "pcb_copper": "电路板铜厚",
    "temperature": "温度", "power": "功率", "torque": "转矩", "speed": "速度", "factor": "线性倍数",
    "oz/ft2 (nominal)": "oz/ft²（标称铜重）", "PS (metric hp)": "PS（公制马力）",
    "hp (mechanical)": "hp（机械马力）", "um": "µm", "C": "°C", "F": "°F",
}
LABELS = {
    "conduction_w": "导通损耗", "output_capacitance_w": "输出电容损耗", "switching_overlap_w": "开关交叠损耗",
    "body_diode_w": "体二极管损耗", "baseline_total_w": "已建模分项总损耗", "known_subtotal_w": "已知损耗小计",
    "gate_driver_w": "栅极驱动损耗（单列）", "rise_time_s": "开通等效时间", "fall_time_s": "关断等效时间",
    "missing_terms": "缺失的损耗分项", "full_device_loss_w": "器件完整损耗", "devices": "器件比较",
    "name": "名称", "status": "结果状态", "minimum_capacitance_f": "最低有效输出电容",
    "phase_factor": "相位裕量系数", "available_impedance_ohm": "允许的瞬态阻抗",
    "esr_headroom_ohm": "扣除 ESR 后的阻抗余量", "branches": "电容支路", "current_rms_a": "支路电流有效值",
    "esr_loss_w": "ESR 损耗", "spectrum": "频率分量", "frequency_hz": "频率", "total_rms_a": "总电流有效值",
    "parallel_impedance_real_ohm": "并联阻抗实部", "parallel_impedance_imag_ohm": "并联阻抗虚部",
    "kcl_residual_a": "电流守恒残差", "current_real_a": "电流实部", "current_imag_a": "电流虚部", "phase_deg": "相位",
    "minimum_line_rms_v": "最低交流输入有效值", "maximum_voltage_v": "母线最高电压",
    "discharge_time_s": "放电时段", "charge_time_s": "充电时段", "capacitance_f": "所需电容量",
    "bulk_rms_current_eq13_a": "母线电容电流有效值（资料公式估算）", "removed_energy_j": "释放能量",
    "clamp_voltage_above_input_v": "相对输入母线的钳位电压", "snubber_power_w": "吸收电路耗散功率",
    "resistance_ohm": "吸收电阻", "leakage_energy_j": "每周期漏感储能", "clamp_ripple_pp_v": "钳位电压峰峰纹波",
    "leakage_discharge_time_s": "漏感放电时间", "parasitic_capacitance_f": "估算寄生电容",
    "parasitic_inductance_h": "估算寄生电感", "snubber_capacitance_f": "吸收电容起始值",
    "snubber_resistance_ohm": "吸收电阻起始值", "charge_discharge_power_w": "附加充放电功耗估算",
    "output_v": "输出电压", "minimum_output_v": "容差下限电压", "maximum_output_v": "容差上限电压",
    "divider_current_a": "分压电流", "relative_error": "标称输出误差", "minimum_relative_error": "最小输出误差",
    "maximum_relative_error": "最大输出误差", "high_arm_power_w": "上臂电阻功耗", "low_arm_power_w": "下臂电阻功耗",
    "ideal_unknown_ohm": "待选电阻理想值", "selected_resistors_ohm": "选用电阻（多项时并联）",
    "high_ohm": "实际上臂电阻", "low_ohm": "实际下臂电阻", "bottom_resistance_ohm": "下臂电阻 R2",
    "adjust_resistance_ohm": "调整支路电阻 R3", "minimum_top_current_a": "上臂最小电流",
    "output_at_zero_adjust_v": "调整电压为零时的输出", "output_at_maximum_adjust_v": "最大调整电压时的输出",
    "adjust_to_output_gain": "调整电压到输出的增益", "mapping": "输出设定映射", "adjust_v": "调整电压",
    "bit_resistances_ohm_lsb_first": "位电阻（从最低位到最高位）", "step_v": "相邻档位电压步距", "code": "数字档位",
    "converted_value": "转换结果", "inductor_parallel_capacitance_f": "电感等效并联寄生电容",
    "unfiltered_level_dbuv": "滤波前噪声幅度", "required_attenuation_db": "达到所填限值所需衰减",
    "noise_frequency_checks": "指定噪声频率检查", "noise_to_other": "噪声侧到源 / 负载侧",
    "other_to_noise_undamped": "源侧到变换器侧（无阻尼）", "other_to_noise_damped": "源侧到变换器侧（有阻尼）",
    "attenuation_db": "衰减", "filtered_level_dbuv": "滤波后噪声幅度", "margin_to_limit_db": "距所填限值的余量",
    "suggested_damping_capacitance_f": "阻尼电容建议起始值",
    "suggested_damping_total_resistance_ohm": "阻尼总电阻建议起始值", "damping_frequency_hz": "阻尼评估频率",
    "effective_capacitance_f": "阻尼等效电容", "effective_esr_ohm": "阻尼等效 ESR",
    "characteristic_impedance_ohm": "特征阻抗", "constant_power_input_resistance_ohm": "恒功率负载小信号输入电阻",
    "damping_factor": "近似阻尼系数", "inductor_dc_loss_w": "电感直流铜损", "curves": "频率响应数据",
}
for _base, _label in {
    "noise_to_other": "噪声侧到源 / 负载侧", "other_to_noise_undamped": "源侧到变换器侧（无阻尼）",
    "other_to_noise_damped": "源侧到变换器侧（有阻尼）", "output_impedance_undamped": "输出阻抗（无阻尼）",
    "output_impedance_damped": "输出阻抗（有阻尼）",
}.items():
    for _suffix, _quantity in (("_magnitude", "幅值"), ("_phase_deg", "相位"), ("_db", "增益")):
        LABELS[_base + _suffix] = _label + _quantity

# Exact source sentences are keys: a new kernel warning cannot accidentally be
# mistaken for a previously translated claim. Unknown notices remain visible.
NOTICES = {
    "Reverse recovery, parasitic overshoot, magnetics and thermal feedback are excluded; baseline sum is not full converter loss/efficiency.":
        "未计反向恢复、寄生过冲、磁件和温度反馈；分项合计不能代表整机总损耗或效率。",
    "SR assumes zero switching overlap; verify commutation and reverse conduction before using this approximation.":
        "同步整流近似忽略开关交叠；使用前须核对换流过程和反向导通。",
    "PM below TI's 60° recommendation (65° for electrolytics).":
        "相位裕量低于资料建议的 60°；电解电容建议至少 65°。",
    "Clamp ripple is not small; the constant-clamp approximation may be inaccurate.":
        "钳位纹波较大，恒定钳位电压近似可能不准确。",
    "Negative constant-power input resistance is retained in δ. Positive δ alone is not a full converter/filter stability or EMI compliance proof.":
        "阻尼计算保留恒功率负载的负输入电阻；阻尼系数为正仍不能证明完整系统稳定或 EMI 合规。",
    "Damping estimate ignores ESL and inductor self-capacitance; frequency curves include them. Automatic fdamp is an explicit RC-resonance extension; no TI executable match is claimed.":
        "阻尼近似忽略 ESL 和电感寄生电容，频响曲线包含这些参数。自动阻尼频率采用 RC 谐振扩展，尚未与 TI 软件实测对照。",
    "Non-positive approximate damping: constant-power load overwhelms passive damping in this reduced model.":
        "近似阻尼系数不大于零：在此简化模型中，恒功率负载的负阻效应已超过无源阻尼。",
    "TI Figure 5-1 shows 2.49 A for 100 W, 85 V, 20%, 47 Hz; printed eq13 gives 2.622526265 A. This is the published approximation, not a full-period integrated capacitor current. Independent charge-balanced rectifier models give different RMS values; do not use this estimate as validated thermal sizing. No executable correlation.":
        "参考资料存在差异：在 100 W、85 V、20%、47 Hz 时，图示为 2.49 A，印刷公式为 2.622526265 A。此处采用印刷近似式，未做整周期电容电流积分。独立电荷平衡模型会得到不同有效值，不应把本估算当作已验证的热设计依据；尚未与 TI 软件实测对照。",
    "RMS is over the full switching period. Rds is at the entered temperature and gate voltage.":
        "电流有效值按完整开关周期计算；导通电阻对应所填温度和栅极驱动电压。",
    "Main: conduction + gate-charge overlap + Coss/Eoss. SR: conduction + dead-time diode + Coss/Eoss.":
        "主开关包含导通、栅电荷交叠和输出电容损耗；同步整流管包含导通、死区二极管和输出电容损耗。",
    "Gate-driver loss is separate, never added to device total. Eoss replaces constant-Coss energy; Eon/Eoff are not mixed in.":
        "栅极驱动损耗单独列出；Eoss 与恒定 Coss 能量二选一，不混入 Eon/Eoff，避免重复计数。",
    "Closed-loop first-order TI estimate using crossover and phase margin, not a general charge-gap transient model.":
        "使用交越频率和相位裕量的闭环一阶估算，适用性取决于相应控制模型。",
    "ESL ignored; effective DC-biased capacitance required. Internal controller compensation may constrain C.":
        "忽略 ESL；应使用考虑直流偏压后的有效容量。控制器内部补偿可能进一步限制可用电容。",
    "Linear series C/ESR/ESL branch models; current-source drive. KCL sums complex currents, not magnitudes.":
        "每支路采用线性串联 C、ESR、ESL 模型并由电流源激励；电流守恒按复数相量求和，不能直接相加幅值。",
    "Single-frequency baseline. Supplied multi-harmonic spectra are an extension with orthogonal RMS-square summation.":
        "基线为单频计算；多谐波扩展按正交分量有效值的平方和合成。",
    "Ideal full-wave rectification; constant power drawn from bulk; sinusoidal line and zero bridge/source drop.":
        "假设正弦交流、理想全波整流、母线恒功率放电，忽略整流桥和电源压降。",
    "Energy-discharge capacitance reuses llc_design.core.bus_capacitor. Rectifier charge/discharge intervals are not arbitrary hold-up time.":
        "电容量复用现有能量放电模型；充放电时段由整流过程决定，不能代入任意保持时间。",
    "Ideal constant-power energy discharge; power is taken from capacitor. Does not model AC rectifier recharge or RMS ripple.":
        "理想恒功率放电，功率从电容取出；未计算交流整流补充充电或纹波有效值。",
    "Clamp voltage is above input rail; main FET stress also includes Vin and parasitic overshoot.":
        "钳位电压以输入母线为参考；主开关电压应力还需计入输入电压和寄生过冲。",
    "Leakage energy approximation, ideal diode, small clamp ripple. R/C are starting values, not total switching loss.":
        "采用漏感能量、理想二极管和小纹波近似；R/C 为调试起始值，耗散功率不代表完整开关损耗。",
    "Verify computed leakage-discharge time fits the actual off interval; only the weaker full-period bound can be checked without duty input.":
        "请核对漏感放电时间小于实际关断时段；未输入占空比时，程序只能检查其小于完整开关周期。",
    "Same linear parasitic L before/after adding known C; measurement probe and operating point unchanged.":
        "加试验电容前后假设寄生电感不变，测量探头及工作条件也应保持一致。",
    "R=√(L/C0), Csn=3C0 are damping starting values. Optional Csn ΔV² fs power assumes full hard-switched charge/discharge each cycle; extension, not TI eq17–21.":
        "吸收电阻和电容是阻尼调试起始值。附加功耗按每周期完整硬开关充放电估算，属于公式之外的扩展。",
    "Ideal reference and zero feedback-input current. Divider current is not amplifier bias-input current.":
        "假设参考源理想、反馈输入电流为零；分压电流不同于放大器输入偏置电流。",
    "Worst-case independent resistor/reference tolerance; common fractional tolerance also bounds same-tolerance parallel pairs.":
        "按电阻与参考源容差独立变化的最坏组合计算；相同容差的并联电阻也采用该界限。",
    "Arm dissipation uses nominal values; choose package/rating with actual thermal derating rather than an unsupported package recommendation.":
        "电阻功耗按标称值计算；封装及额定功率需结合实际热降额选取。",
    "Unknown arm selected; known arm retained exactly. Two-resistor search uses each resistor from Rtarget to 100 Rtarget.":
        "已知臂保持输入值，只选择未知臂；双电阻并联搜索中，每只电阻范围为目标值的 1 至 100 倍。",
    "Ideal voltage-source adjustment 0..Vadj,max. DAC/PWM source must source and sink the feedback-branch current.":
        "调整端假设为理想电压源，范围为零至所填最大值；DAC/PWM 源须能向反馈支路供电并吸收电流。",
    "Zero feedback input current; output decreases as adjustment voltage increases.":
        "忽略反馈输入电流；调整电压升高时，输出设定电压下降。",
    "Code bit=1 closes its resistor to ground; bit=0 disconnects. Ideal switches, no pull-high GPIO voltage.":
        "位值为 1 时对应电阻接地，为 0 时断开；假设开关理想，不采用 GPIO 高电平上拉。",
    "Nominal resistors; switch Rds/leakage and resistor tolerance excluded. This generates a setpoint, not a digital feedback controller.":
        "采用标称电阻，忽略开关导通电阻、漏电和电阻容差；本工具只生成输出设定档位，不计算数字反馈控制器。",
    "Amplitude/voltage gain uses 20 log10; power gain uses 10 log10. No impedance equivalence implied.":
        "电压 / 幅值增益采用 20 log10，功率增益采用 10 log10；不隐含两端阻抗相等。",
    "PCB ounce means nominal copper areal weight/thickness: 1 oz/ft² = 35 µm. Metric PS and mechanical hp are explicitly distinct.":
        "电路板铜重采用标称换算 1 oz/ft² = 35 µm；公制马力 PS 与机械马力 hp 分开计算。",
    "Source-side CF, series LF, converter-side CIN/OUT and series RD+CD shunt damping; all capacitor ESR/ESL and inductor DCR/SRF included in frequency response.":
        "电路由源侧 CF、串联 LF、变换器侧电容和串联 RD/CD 阻尼支路组成；频响包含电容 ESR/ESL 及电感 DCR/自谐振。",
    "Directional current/voltage transfers use TI's ideal source/load terminations. Output impedance assumes the source is AC-shortened; CF then drops out.":
        "各方向电流 / 电压传递采用资料中的理想源与负载端接；输出阻抗按源端交流短路计算，此时 CF 不参与。",
    "Noise amplitude and dBµV limit must use the same detector/amplitude convention; no peak-to-RMS conversion is assumed.":
        "噪声幅度与 dBµV 限值必须采用相同检波及幅值口径；程序不自动将峰值换算为有效值。",
    "Suggested CD=4C, RD+ESRd=√(L/C) reproduces Figure 13-1 starting values, not a documented TI internal optimizer. Re-evaluate chosen damping components.":
        "阻尼建议值对应资料示例的起始选值，不代表 TI 内部优化算法；选择实际元件后应重新计算。",
}


def choice_label(value: object) -> str:
    if value is None:
        return "理想值 / 无标准化"
    return CHOICE_LABELS.get(value, str(value))


def status_label(status: str) -> str:
    return STATUS_LABELS.get(status, "结果状态待核对，见详细依据")


def notice_text(text: str) -> str:
    if text.startswith("Missing baseline terms: "):
        terms = text.split(": ", 1)[1].split(";", 1)[0].split(", ")
        return "缺失分项：" + "、".join(LABELS.get(term, "未识别分项") for term in terms) + "；未知项保留为空，不按零计入。"
    return NOTICES.get(text, "模型另有一条尚未翻译的说明，请展开详细依据核对原文。")


def format_value(value: object, unit: str = "") -> str:
    """Unit scaling is display-only; model quantities remain unchanged."""
    if value is None:
        return "未知 / 未提供"
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, (int, float)):
        scale, display_unit = {
            "F": (1e6, "µF"), "H": (1e6, "µH"), "s": (1e6, "µs"), "ohm": (1, "Ω"),
            "fraction": (100, "%"), "deg": (1, "°"), "integer": (1, ""), "1": (1, ""),
        }.get(unit, (1, unit))
        return format(value * scale, ".10g") + (" " + display_unit if display_unit else "")
    return status_label(value) if value in STATUS_LABELS else str(value)


def _preview_indices(count: int, limit: int = 8) -> list[int]:
    if count <= limit:
        return list(range(count))
    return list(range(limit // 2)) + list(range(count - limit // 2, count))


def result_text(result: CalculatorResult) -> str:
    lines = [TITLES.get(result.tool_id, "计算结果"), status_label(result.status),
             "以下为计算模型结果，尚不代表硬件验证。"]
    if result.warnings:
        lines.extend(["", "注意事项"])
        lines.extend("• " + notice_text(text) for text in result.warnings)
    lines.extend(["", "计算结果"])

    def emit(key, value, depth=0):
        label = LABELS.get(key, "补充结果（请在详细依据中核对）")
        prefix = "  " * depth
        if key == "missing_terms":
            lines.append(prefix + label + "：" + ("、".join(LABELS.get(v, "未识别分项") for v in value) if value else "无"))
        elif key == "curves":
            count = len(value["frequency_hz"])
            lines.append(prefix + f"频率响应：共 {count} 个频率点；下方曲线及完整导出保留全部数据。")
            for column, samples in value.items():
                lines.append(prefix + "  " + LABELS.get(column, "补充曲线") + "：" +
                             "、".join(format_value(samples[i], result.units.get(column, "")) for i in _preview_indices(count)))
            if count > 8:
                lines.append(prefix + "  仅预览首尾各 4 点；完整数据请导出 TXT / CSV / JSON。")
        elif isinstance(value, dict):
            lines.append(prefix + label + "：")
            for child, item in value.items():
                emit(child, item, depth + 1)
        elif isinstance(value, (list, tuple)):
            lines.append(prefix + label + f"（共 {len(value)} 项）：")
            limit = 16 if key == "bit_resistances_ohm_lsb_first" else 8
            for index in _preview_indices(len(value), limit):
                item = value[index]
                lines.append(prefix + f"  第 {index + 1} 项" + ("：" if isinstance(item, dict) else "：" + format_value(item, result.units.get(key, ""))))
                if isinstance(item, dict):
                    for child, child_value in item.items():
                        emit(child, child_value, depth + 2)
            if len(value) > limit:
                lines.append(prefix + "  中间条目已省略；完整数据请导出 TXT / CSV / JSON。")
        else:
            unit = result.units.get(key, "")
            rendered = (format_value(value) + " " + choice_label(unit) if result.tool_id == "X10"
                        else format_value(value, unit))
            lines.append(prefix + label + "：" + rendered)

    if result.tool_id == "X10":
        lines.append("转换类别：" + choice_label(result.inputs["category"]))
        lines.append("输入：" + format_value(result.inputs["value"]) + " " + choice_label(result.inputs["from_unit"]))
    for key, value in result.outputs.items():
        emit(key, value)
    lines.extend(["", "适用条件"])
    lines.extend("• " + notice_text(text) for text in result.assumptions)
    if "stage_import" in result.inputs:
        lines.append("• 部分输入来自当前功率级；修改功率级后须重新导入。")
    lines.extend(["", "原始输入、来源、模型版本与结果标识可在“显示详细依据 / 原始数据”中查看。"])
    return "\n".join(lines)


def details_text(result: CalculatorResult) -> str:
    # Shallow metadata copy avoids duplicating a 65,536-row map merely to show
    # eight rows; neither the kernel result nor its export contract is mutated.
    outputs = dict(result.outputs)
    for key in ("mapping", "spectrum"):
        if key in outputs and len(outputs[key]) > 8:
            rows = outputs[key]
            outputs[key] = {"preview": [rows[i] for i in _preview_indices(len(rows))], "rows": len(rows),
                            "full_data": "完整数据请导出 TXT / CSV / JSON"}
    if "curves" in outputs:
        curves = outputs["curves"]
        outputs["curves"] = {"preview": {key: list(islice(values, 8)) for key, values in curves.items()},
                             "points": len(curves["frequency_hz"]), "full_data": "完整数据请导出 TXT / CSV / JSON"}
    display = {"tool_id": result.tool_id, "result_id": result.result_id, "outputs": outputs, "units": result.units,
               "inputs": result.inputs, "assumptions": result.assumptions, "warnings": result.warnings,
               "source": result.source, "equations": result.equations, "status": result.status,
               "model_version": result.model_version,
               "evidence": "Analytical calculation; no TI executable or hardware validation implied"}
    return json.dumps(display, ensure_ascii=False, indent=2, allow_nan=False)

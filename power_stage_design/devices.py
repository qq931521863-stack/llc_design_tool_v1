"""Same-result component evidence; unavailable loss and thermal terms stay unknown."""
from __future__ import annotations

import math


def assess_component(result, key: str, *, selected_main_device=None, temperature_c=100.0) -> dict:
    if key not in result.stress:
        raise ValueError("Component has no waveform/stress mapping in this design")
    if not math.isfinite(temperature_c) or not -40 <= temperature_c <= 175:
        raise ValueError("Screening junction temperature must be within −40…175 °C")
    stats=result.stress[key]
    vmax=max(abs(stats["voltage_min_v"]),abs(stats["voltage_max_v"]))
    imax=max(abs(stats["current_min_a"]),abs(stats["current_max_a"]))
    evidence={"design_id":result.design_id,"component":key,"voltage_peak_v":vmax,
        "current_peak_a":imax,"known_conduction_loss_w":None,
        "total_loss_w":None,"thermal_status":"UNKNOWN",
        "assessment":"未指定此支路的器件/ESR/磁件与热参数；损耗与额定裕量未知。"}
    if key in {"node_A","node_B"} and result.inputs.psfb_options is not None:
        evidence.update(assessment="理想高阻电压探针，电流恒为零；不是额外功率器件，不作器件损耗/额定评估。",
            loss_scope="ideal_voltage_probe", thermal_status="NOT_APPLICABLE")
        return evidence
    if key=="Q1" and selected_main_device is not None:
        device=selected_main_device
        violations=[]
        if vmax>device.vds_max_v:violations.append("理想电压已超过额定")
        if imax>device.id_cont_a:violations.append("峰值电流已超过连续额定，需SOA核对")
        evidence["rating_violations"]=violations
        resistance=device.rds_at(temperature_c)
        if not math.isfinite(resistance) or resistance<=0:
            evidence.update(part_number=device.part_number,assessment=("；".join(violations)+"；" if violations else "")+"器件未提供有效温度相关RDS(on)，导通损耗保持未知。")
            return evidence
        evidence.update(part_number=device.part_number,temperature_c=temperature_c,
            known_conduction_loss_w=stats["current_rms_a"]**2*resistance,
            assessment="；".join(violations) if violations else "理想应力未超过所录入额定；仍需降额与热/SOA检查。")
        evidence["loss_scope"]="仅I²R导通项，固定用户温度；驱动条件须核对。开关、死区、磁件和完整热网络未计算。"
    return evidence

"""Component stresses and documented limits tied to one waveform bundle.

A waveform's ideal VDS trace is not evidence of switching energy or junction
survival.  Never attach nominal FHA/TD losses to a different waveform source.
"""
from __future__ import annotations

from ..core.spec import LLCDesignSpec, PrimaryTopology
from ..dynamics.waveforms import WaveformBundle
from .devices import DeviceDatabase


def component_waveform_evidence(bundle: WaveformBundle, spec: LLCDesignSpec) -> dict:
    database = DeviceDatabase()
    result = {}
    primary = [f"q{i}" for i in range(1, 3 if spec.primary_topology == PrimaryTopology.HALF_BRIDGE else 5)]
    for component in primary + [f"sr{i}" for i in range(1, 5)]:
        voltage, current = bundle.signals.get(f"vds_{component}"), bundle.signals.get(f"ids_{component}")
        if voltage is None or current is None:
            continue
        device = (database.get_primary(spec.primary_device) if component.startswith('q')
                  else database.get_sr(spec.sr_device))
        count = spec.primary_parallel_devices if component.startswith('q') else spec.sr_parallel_devices_per_position
        per_device_rms = current.statistics.rms / count
        derating = spec.primary_voltage_derating if component.startswith('q') else spec.sr_voltage_derating
        # This reports raw limits and actual bundle statistics. It deliberately
        # makes no SOA, thermal, pulse-current or hardware-safe inference.
        result[component] = {
            "source_identity": str(bundle.metadata.get("request_identity", "unknown")),
            "part_number": device.part_number,
            "peak_voltage_v": voltage.statistics.absolute_peak,
            "current_rms_a": current.statistics.rms,
            "current_ac_rms_a": current.statistics.ac_rms,
            "parallel_devices_per_position": count,
            "current_basis": "whole switch position; per-device current assumes ideal equal sharing",
            "per_device_current_rms_a": per_device_rms,
            "derated_voltage_limit_v": device.vds_max_v * derating,
            "datasheet_current_basis": "one device, continuous rating; not pulse SOA",
            "datasheet_vds_limit_v": device.vds_max_v,
            "datasheet_continuous_current_a": device.id_cont_a,
            "loss_status": "UNKNOWN: no losses calculated from this accepted waveform",
            "constraint_scope": "Reference database limits only; pulse SOA, ringing and thermal validation unknown",
            "summary": (f"{device.part_number} | 本波形 VDS峰值={voltage.statistics.absolute_peak:.6g} V，"
                        f"位置 RMS={current.statistics.rms:.6g} A，{count}颗并联理想均流后单颗={per_device_rms:.6g} A | "
                        f"单颗额定 VDS={device.vds_max_v:g} V，降额限值={device.vds_max_v * derating:g} V，"
                        f"连续电流={device.id_cont_a:g} A；不代表脉冲 SOA/热校核。总损耗：未计算。"),
        }
    identity = str(bundle.metadata.get("request_identity", "unknown"))
    # Constant-ESR I²R uses this accepted bundle's own sampled RMS. Keep the
    # plotted midpoint-circuit voltage separate from A03's conventional
    # single-ended HB capacitor rating reference (Vcr + Vbus/2). No bias is
    # injected into the solver arrays or their statistics.
    cr_i = bundle.signals["i_resonant"].statistics
    cr_v = bundle.signals["v_resonant_cap"].statistics
    cr_loss = cr_i.rms**2 * spec.resonant_cap_esr_ohm
    voltage_limit = spec.resonant_cap_voltage_rating_v * spec.capacitor_voltage_derating
    bias = (0.5 * float(bundle.metadata["vbus_v"])
            if spec.primary_topology == PrimaryTopology.HALF_BRIDGE else 0.0)
    rating_peak = max(abs(cr_v.minimum + bias), abs(cr_v.maximum + bias))
    result["cr"] = {
        "source_identity": identity, "loss_model": "same-bundle RMS squared times constant ESR",
        "esr_ohm": spec.resonant_cap_esr_ohm, "esr_loss_w": cr_loss,
        "current_rms_a": cr_i.rms, "voltage_peak_v": cr_v.absolute_peak,
        "current_signal": "i_resonant", "voltage_signal": "v_resonant_cap",
        "rating_evaluation_dc_bias_v": bias, "rating_evaluation_peak_v": rating_peak,
        "voltage_rating_v": spec.resonant_cap_voltage_rating_v,
        "voltage_derating": spec.capacitor_voltage_derating,
        "derated_voltage_limit_v": voltage_limit,
        "current_rms_limit_a": spec.resonant_cap_rms_current_rating_a,
        "voltage_within_model_limit": cr_v.absolute_peak <= voltage_limit,
        "single_ended_variant_voltage_within_limit": rating_peak <= voltage_limit,
        "modeled_return_connection": "B = Vbus/2 ideal midpoint" if bias else "opposite bridge leg B",
        "single_ended_variant_return_connection": "PGND; alternate HB connection only" if bias else "not applicable: full bridge",
        "current_within_model_limit": cr_i.rms <= spec.resonant_cap_rms_current_rating_a,
        "voltage_reference": "HB plotted circuit returns to B = Vbus/2 (zero added Cr bias); alternate single-ended HB returns to PGND (Vcr + Vbus/2), not the plotted connection",
        "unknown_losses": "frequency/temperature dependent ESR and dielectric loss not modeled",
        "summary": (f"同源恒定 ESR 损耗={cr_loss:.6g} W | 图示峰值={cr_v.absolute_peak:.6g} V；"
                    f"额定评估 +{bias:g} V 偏置后峰值={rating_peak:.6g} V / "
                    f"降额限值 {voltage_limit:g} V，RMS={cr_i.rms:.6g} A / "
                    f"额定 {spec.resonant_cap_rms_current_rating_a:g} A；HB 图示返回 B=Vbus/2 无附加偏置；"
                    "偏置评估仅适用于改为返回 PGND 的单端变体，热设计另核对。"),
    }
    co_i = bundle.signals["i_output_cap"].statistics
    ripple = bundle.signals["v_output"].statistics.peak_to_peak
    co_loss = co_i.rms**2 * spec.output_cap_esr_ohm
    result["co"] = {
        "source_identity": identity, "loss_model": "same-bundle RMS squared times constant ESR",
        "esr_ohm": spec.output_cap_esr_ohm, "esr_loss_w": co_loss,
        "current_rms_a": co_i.rms, "terminal_ripple_vpp": ripple,
        "current_signal": "i_output_cap", "ripple_voltage_signal": "v_output",
        "plotted_voltage_signal": "v_output_cap_internal",
        "ripple_limit_vpp": spec.output_ripple_limit_vpp,
        "ripple_within_model_limit": ripple <= spec.output_ripple_limit_vpp,
        "unknown_limits": "capacitor-specific rated voltage/current and thermal limits not provided",
        "summary": (f"同源恒定 ESR 损耗={co_loss:.6g} W | 输出端纹波={ripple:.6g} Vpp / "
                    f"限值 {spec.output_ripple_limit_vpp:g} Vpp；电容单体额定/温升约束未提供。"),
    }
    for component in ('lr', 'lm', 'primary', 'secondary', 'transformer'):
        key = 'b_resonant_inductor' if component == 'lr' else 'b_transformer'
        limit = spec.resonant_inductor_max_b_t if component == 'lr' else spec.transformer_max_b_t
        signal = bundle.signals.get(key)
        peak = None if signal is None else signal.statistics.absolute_peak
        result[component] = {
            "source_identity": identity, "flux_signal": key,
            "peak_flux_density_t": peak, "flux_density_limit_t": limit,
            "flux_within_model_limit": None if peak is None else peak <= limit,
            "loss_status": "UNKNOWN: no magnetic loss calculation from this accepted waveform",
            "summary": (f"同源 |B|峰值={peak:.6g} T / 限值 {limit:g} T；磁损/温升：未计算。"
                        if peak is not None else f"磁通限值={limit:g} T；本来源缺少 B 波形，磁损/温升：未计算。"),
        }
    result["load"] = {"source_identity": identity, "summary": "同源输出端 V/I 见统计；负载为等效电阻，不表示实际负载瞬态。"}
    return result


def waveform_electrical_inputs(small, analysis) -> dict:
    """Freeze the same numeric damping/geometry inputs as control-session replay.

    The source text is historical provenance, never a restored convergence or
    loss-acceptance flag. A fresh curve must still be solved from these inputs.
    """
    fields = ("series_resistance_ohm", "transformer_core_area_m2",
              "transformer_magnetic_path_m", "resonant_inductor_turns",
              "resonant_inductor_core_area_m2", "resonant_inductor_magnetic_path_m")
    inputs = {key: getattr(small.parameters, key) for key in fields}
    point = min(analysis.operating_points, key=lambda item:
        abs(item.operating_point.vbus_v-small.operating_point.vbus_v)
        + 1000*abs(item.operating_point.load_fraction-small.operating_point.load_fraction))
    inputs["source"] = (f"{point.semiconductor_source}; {point.magnetic_source}; "
                        f"{point.capacitor_source}; "
                        f"loss-point {point.operating_point.vbus_v:g} V/{point.operating_point.load_fraction:g}; "
                        f"waveform {point.loss_waveform_sha256 or 'not supplied'}")
    return inputs

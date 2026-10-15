"""Carrier-resolved CCM stress and loss screening for two-level/Vienna PFC.

A bank of local switching periods samples a full line cycle. Each period uses
frozen average duty/current and a floating-neutral RL ripple reconstruction.
This is not an independent switching transient or a DCM/device physics solver.
"""

import math
from dataclasses import asdict, dataclass

import numpy as np

from llc_design.magnetics.material import MaterialDatabase
from pfc_design.magnetics.steinmetz import _igse_ki


@dataclass(frozen=True)
class LossParameters:
    # Zero means unknown / not included, NOT a loss-free physical component.
    rds_on_ohm: float = 0
    switch_energy_j: float = 0  # Eon+Eoff per device/cycle, no extra Coss term
    energy_reference_v: float = 400
    energy_reference_a: float = 20
    vienna_switch_kind: str = "back_to_back"
    bridge_diode_vf_v: float = 0
    bridge_diode_resistance_ohm: float = 0
    diode_vf_v: float = 0
    diode_resistance_ohm: float = 0
    capacitor_esr_ohm: float = 0  # per capacitor bank; Vienna upper and lower
    hf_ac_resistance_ratio: float = 1
    turns: float = 0
    ae_mm2: float = 0
    ve_mm3: float = 0
    material_key: str = "TDK_PC40_2002"
    core_temperature_c: float = 100

    def validate(self):
        for key, value in asdict(self).items():
            if key in ("material_key", "vienna_switch_kind"):
                continue
            if not math.isfinite(value):
                raise ValueError("Finite loss parameters required")
            if key != "core_temperature_c" and value < 0:
                raise ValueError("Negative loss parameter: " + key)
        if (
            min(self.energy_reference_v, self.energy_reference_a) <= 0
            or self.hf_ac_resistance_ratio < 1
        ):
            raise ValueError("Positive energy reference and Rac/Rdc >=1 required")
        if self.vienna_switch_kind not in ("back_to_back", "diode_bridge"):
            raise ValueError("Unknown Vienna center-switch implementation")
        geometry = (self.turns, self.ae_mm2, self.ve_mm3)
        if any(geometry) and not all(v > 0 for v in geometry):
            raise ValueError("Core loss requires turns, Ae and Ve together")
        if self.turns:
            MaterialDatabase().get(self.material_key)


def _stress(values):
    return {
        "rms_a": float(np.sqrt(np.mean(values**2))),
        "peak_a": float(np.max(abs(values))),
        "mean_abs_a": float(np.mean(abs(values))),
    }


def _evaluate(
    *,
    topology,
    time_s,
    voltage,
    average_current,
    duty,
    bus,
    upper,
    lower,
    load_power,
    grid_hz,
    switching_hz,
    inductance_h,
    resistance_ohm,
    losses,
    line_points=256,
    carrier_points=128,
):
    losses.validate()
    if (
        line_points < 64
        or carrier_points < 32
        or line_points * carrier_points > 2_000_000
    ):
        raise ValueError("Resolution out of range")
    if not np.isfinite(time_s).all() or time_s[-1] - time_s[0] < 1 / grid_hz * 0.99:
        raise ValueError("At least one complete line cycle is required")
    # End-exclusive coherent line window; no duplicate endpoint in averages.
    time = time_s[-1] - 1 / grid_hz + np.arange(line_points) / (grid_hz * line_points)

    def interp(values):
        values = np.asarray(values)
        return (
            np.column_stack(
                [np.interp(time, time_s, values[:, j]) for j in range(values.shape[1])]
            )
            if values.ndim == 2
            else np.interp(time, time_s, values)
        )

    v, i0, d, vb = map(interp, (voltage, average_current, duty, bus))
    vp, vn, pload = map(interp, (upper, lower, load_power))
    phase = (np.arange(carrier_points) + 0.5) / carrier_points
    carrier = 1 - abs(2 * phase - 1)
    on = carrier[None, :, None] < d[:, None, :]
    if topology == "two_level":
        pole = vb[:, None, None] * (on - 0.5)
    else:
        active_voltage = np.where(i0 >= 0, vp[:, None], -vn[:, None])
        pole = np.where(on, 0, active_voltage[:, None, :])
    converter = pole - np.mean(pole, axis=2, keepdims=True)
    # Remove discretization-induced volt-seconds; preserve requested mean I.
    ripple_v = np.mean(converter, axis=1, keepdims=True) - converter
    dt = 1 / (switching_hz * carrier_points)
    edges = np.concatenate(
        [
            np.zeros((line_points, 1, 3)),
            np.cumsum(ripple_v * dt / inductance_h, axis=1),
        ],
        axis=1,
    )
    ripple = 0.5 * (edges[:, :-1] + edges[:, 1:])
    ripple -= np.mean(ripple, axis=1, keepdims=True)
    current = i0[:, None, :] + ripple
    devices = {}
    gates = {}
    for j, ph in enumerate("abc"):
        if topology == "two_level":
            devices[ph + "_upper_switch"] = current[:, :, j] * on[:, :, j]
            devices[ph + "_lower_switch"] = -current[:, :, j] * (~on[:, :, j])
            gates[ph + "_upper_switch"] = on[:, :, j]
            gates[ph + "_lower_switch"] = ~on[:, :, j]
        else:
            if losses.vienna_switch_kind == "back_to_back":
                for k in (1, 2):
                    devices[f"{ph}_center_switch_{k}"] = current[:, :, j] * on[:, :, j]
                    gates[f"{ph}_center_switch_{k}"] = on[:, :, j]
            else:
                devices[ph + "_center_switch"] = abs(current[:, :, j]) * on[:, :, j]
                gates[ph + "_center_switch"] = on[:, :, j]
                for k in (1, 2, 3, 4):
                    polarity = 1 if k <= 2 else -1
                    devices[f"{ph}_bridge_diode_{k}"] = (
                        np.maximum(polarity * current[:, :, j], 0) * on[:, :, j]
                    )
            devices[ph + "_upper_diode"] = np.maximum(current[:, :, j], 0) * (
                ~on[:, :, j]
            )
            devices[ph + "_lower_diode"] = np.maximum(-current[:, :, j], 0) * (
                ~on[:, :, j]
            )
    if topology == "two_level":
        ic = np.sum(on * current, axis=2) - pload[:, None] / vb[:, None]
        cap_currents = {"bus": ic}
    else:
        iu = np.sum((~on) * np.maximum(current, 0), axis=2)
        il = np.sum((~on) * np.maximum(-current, 0), axis=2)
        cap_currents = {
            "upper": iu - pload[:, None] / vb[:, None],
            "lower": il - pload[:, None] / vb[:, None],
        }
    stress = {key: _stress(value) for key, value in devices.items()}
    l_stress = {ph: _stress(current[:, :, j]) for j, ph in enumerate("abc")}
    cap_stress = {key: _stress(value) for key, value in cap_currents.items()}
    copper_lf = resistance_ohm * float(np.mean(np.sum(i0**2, axis=1)))
    copper_hf = (
        resistance_ohm
        * losses.hf_ac_resistance_ratio
        * float(np.mean(np.sum(ripple**2, axis=2)))
    )
    conduction = sum(
        item["rms_a"] ** 2 * losses.rds_on_ohm
        for key, item in stress.items()
        if "switch" in key
    )
    diode = sum(
        item["mean_abs_a"] * losses.diode_vf_v
        + item["rms_a"] ** 2 * losses.diode_resistance_ohm
        for key, item in stress.items()
        if "_upper_diode" in key or "_lower_diode" in key
    )
    bridge_diode = sum(
        item["mean_abs_a"] * losses.bridge_diode_vf_v
        + item["rms_a"] ** 2 * losses.bridge_diode_resistance_ohm
        for key, item in stress.items()
        if "bridge_diode" in key
    )
    switching = 0.0
    for key, gate in gates.items():
        j = "abc".index(key[0])
        transition = gate != np.roll(gate, 1, axis=1)
        # Only the blocking/hard-commutating device is charged Eon+Eoff.
        if topology == "two_level":
            hard = i0[:, j] < 0 if "upper" in key else i0[:, j] >= 0
            transition &= hard[:, None]
        elif losses.vienna_switch_kind == "back_to_back":
            hard = i0[:, j] >= 0 if key.endswith("_1") else i0[:, j] < 0
            transition &= hard[:, None]
        # Half Epair per edge, scaled by sampled edge current and blocking V.
        iboundary = 0.5 * (
            abs(current[:, :, j]) + abs(np.roll(current[:, :, j], 1, axis=1))
        )
        vblock = vb if topology == "two_level" else vb / 2
        switching += (
            float(np.mean(np.sum(transition * iboundary, axis=1) * vblock))
            * switching_hz
            * losses.switch_energy_j
            / (2 * losses.energy_reference_a * losses.energy_reference_v)
        )
    capacitor = (
        sum(s["rms_a"] ** 2 for s in cap_stress.values()) * losses.capacitor_esr_ohm
    )
    warnings = [
        "SOFTWARE_MODEL_ONLY: local frozen-duty CCM carrier reconstruction; no deadtime, reverse recovery or independent transient validation.",
        "PF_total includes reconstructed HF ripple; ITHD_2_40 is low-frequency harmonics only.",
        "Losses are postprocessed and do not feed back into the bus/current simulation.",
    ]
    missing = []
    if not losses.rds_on_ohm:
        missing.append("switch conduction Rds(Tj)")
    if not losses.switch_energy_j:
        missing.append("switching Eon+Eoff")
    if topology == "vienna" and not (losses.diode_vf_v or losses.diode_resistance_ohm):
        missing.append("outer diode conduction")
    if not losses.capacitor_esr_ohm:
        missing.append("capacitor ESR(f,T)")
    if topology == "vienna":
        warnings.append("Center switch implementation: " + losses.vienna_switch_kind)
        if losses.vienna_switch_kind == "diode_bridge" and not (
            losses.bridge_diode_vf_v or losses.bridge_diode_resistance_ohm
        ):
            missing.append("center bridge diode conduction")
    warnings.append(
        "Switching Epair is assigned only to hard-commutating devices by current polarity; soft commutation, reverse recovery and Coss are not added separately."
    )
    core_loss = None
    flux = {}
    if losses.turns:
        material = MaterialDatabase().get(losses.material_key)
        coeff = material.coefficients_at(losses.core_temperature_c)
        ki = _igse_ki(coeff.k, coeff.alpha, coeff.beta) * material.core_loss_correction
        br = inductance_h * ripple / (losses.turns * losses.ae_mm2 * 1e-6)
        # Periodic microcycle derivative, per local line point and phase.
        rate = (np.roll(br, -1, axis=1) - br) / dt
        pv_hf = (
            ki
            * np.mean(abs(rate) ** coeff.alpha, axis=1)
            * np.ptp(br, axis=1) ** (coeff.beta - coeff.alpha)
        )
        b_lf = inductance_h * i0 / (losses.turns * losses.ae_mm2 * 1e-6)
        low = sum(
            material.steinmetz_density_w_m3(
                grid_hz, float(np.ptp(b_lf[:, j])) / 2, losses.core_temperature_c
            )
            for j in range(3)
        )
        core_loss = (float(np.mean(np.sum(pv_hf, axis=1))) + low) * losses.ve_mm3 * 1e-9
        bpk = float(np.max(abs(b_lf[:, None, :] + br)))
        flux = {
            "peak_t": bpk,
            "bsat_t": material.bsat_at(losses.core_temperature_c),
            "peak_margin_t": material.bsat_at(losses.core_temperature_c) - bpk,
        }
        warnings.extend(
            material.range_warnings(switching_hz, float(np.max(np.ptp(br, axis=1))) / 2)
        )
        warnings.append(
            "Core LF+microcycle iGSE sum is screening only: no DC-bias or minor-loop interaction model; check material/gap and measured loss."
        )
        if (
            not material.frequency_range_hz[0]
            <= grid_hz
            <= material.frequency_range_hz[1]
        ):
            warnings.append("Line-frequency core term extrapolates the material fit.")
    else:
        missing.append("inductor core geometry, turns and material")
    breakdown = {
        "inductor_copper_lf": copper_lf,
        "inductor_copper_hf": copper_hf,
        "switch_conduction": conduction,
        "switch_switching": switching,
        "outer_diode": diode,
        "center_bridge_diodes": bridge_diode,
        "capacitor_esr": capacitor,
        "inductor_core": core_loss,
    }
    total = sum(v for v in breakdown.values() if v is not None)
    angle = 2 * np.pi * grid_hz * (time - time[0])
    basis = np.column_stack(
        [np.ones(line_points)]
        + [fun(h * angle) for h in range(1, 41) for fun in (np.cos, np.sin)]
    )
    coeff = np.linalg.lstsq(basis, i0, rcond=None)[0]
    harmonics = np.hypot(coeff[1::2], coeff[2::2]) / math.sqrt(2)
    phase_irms = np.sqrt(np.mean(current**2, axis=(0, 1)))
    pin = float(np.mean(np.sum(v * i0, axis=1)))
    pf = pin / max(float(np.sqrt(np.mean(v * v, axis=0)) @ phase_irms), 1e-12)
    thd = np.sqrt(np.sum(harmonics[1:] ** 2, axis=0)) / np.maximum(harmonics[0], 1e-12)
    distortion_total = np.sqrt(
        np.maximum(phase_irms**2 - harmonics[0] ** 2, 0)
    ) / np.maximum(harmonics[0], 1e-12)
    if topology == "vienna" and np.any(
        np.min(current, axis=1) * np.max(current, axis=1) < 0
    ):
        warnings.append(
            "Ripple crosses zero: Vienna CCM fixed-polarity reconstruction is unreliable near zero crossings (DCM not solved)."
        )
    return {
        "topology": topology,
        "operating_point": {
            "grid_hz": grid_hz,
            "switching_hz": switching_hz,
            "inductance_h": inductance_h,
            "series_resistance_ohm": resistance_ohm,
            "bus_mean_v": float(np.mean(vb)),
            "load_mean_w": float(np.mean(pload)),
        },
        "assumptions": asdict(losses),
        "device_stress": stress,
        "inductor_stress": l_stress,
        "capacitor_stress": cap_stress,
        "flux": flux,
        "loss_breakdown_w": breakdown,
        "included_loss_total_w": total,
        "efficiency_included_terms": float(np.mean(pload) / (np.mean(pload) + total)),
        "loss_model_complete": not missing,
        "missing_loss_inputs": missing,
        "power_factor_total": pf,
        "ithd_2_40_percent": (100 * thd).tolist(),
        "total_distortion_with_hf_percent": (100 * distortion_total).tolist(),
        "harmonic_orders": list(range(1, 41)),
        "harmonic_rms_a": harmonics.tolist(),
        "warnings": warnings,
        "replay": {
            "time_s": (time - time[0]).tolist(),
            "voltage_abc": v.tolist(),
            "current_mean_abc": i0.tolist(),
            "local_time_s": (phase / switching_hz).tolist(),
            "local_current_abc": current[line_points // 4].tolist(),
            "local_gates_abc": on[line_points // 4].astype(int).tolist(),
            "local_device_current_a": {
                k: value[line_points // 4].tolist() for k, value in devices.items()
            },
            "local_capacitor_current_a": {
                k: value[line_points // 4].tolist() for k, value in cap_currents.items()
            },
        },
    }


def evaluate_two_level(result, losses=None, **resolution):
    losses = losses or LossParameters()
    c, s = result.config, result.signals
    frequency = (
        c.grid_after_hz if result.time_s[-1] > c.disturbance_time_s else c.grid_hz
    )
    return _evaluate(
        topology="two_level",
        time_s=result.time_s,
        voltage=np.column_stack([s["vac_" + p] for p in "abc"]),
        average_current=np.column_stack([s["current_" + p] for p in "abc"]),
        duty=np.column_stack([s["duty_" + p] for p in "abc"]),
        bus=s["vbus_v"],
        upper=s["vbus_v"] / 2,
        lower=s["vbus_v"] / 2,
        load_power=s["pload_w"],
        grid_hz=frequency,
        switching_hz=c.control_hz,
        inductance_h=c.inductance_h,
        resistance_ohm=c.resistance_ohm,
        losses=losses,
        **resolution,
    )


def evaluate_vienna(config, line, losses=None, **resolution):
    losses = losses or LossParameters()
    c, s = config.power_stage, line.signals
    # Work only on the frozen run configuration, never current GUI widgets.
    return _evaluate(
        topology="vienna",
        time_s=line.time_s,
        voltage=np.column_stack([s["v" + p] for p in "abc"]),
        average_current=np.column_stack([s["i" + p] for p in "abc"]),
        duty=np.column_stack([s["duty_" + p] for p in "abc"]),
        bus=s["vdc"],
        upper=s["vdc_plus"],
        lower=s["vdc_minus"],
        load_power=s["load_current"] * s["vdc"],
        grid_hz=c.line_frequency_hz,
        switching_hz=c.switching_frequency_hz,
        inductance_h=c.boost_inductance_h,
        resistance_ohm=c.phase_series_resistance_ohm,
        losses=losses,
        **resolution,
    )

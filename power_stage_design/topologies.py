"""Topology-specific steady-state models with exact branch-waveform integrals.

The small-ripple approximation holds input/output voltages constant while
solving inductor currents. Capacitor voltage ripple is reconstructed from the
same current, not fed back into the slopes. Diode voltages use cathode-anode
reference; a conducting diode therefore has voltage -Vf.
"""
from __future__ import annotations

import itertools
import math
from dataclasses import replace
from numbers import Real

from .models import (
    DesignInputs,
    DeviceWaveforms,
    ModelDomainError,
    StageResult,
    UnsupportedTopologyError,
)
from .waveforms import Waveform, capacitor_voltage, piecewise

TOPOLOGY_NAMES = {
    "T01": "Buck", "T02": "Boost", "T03": "Inverting Buck-Boost", "T04": "Fly-Buck",
    "T05": "Buck-Boost 2-Switch", "T06": "SEPIC", "T07": "ZETA", "T08": "Ćuk",
    "T09": "Flyback", "T10": "2-Switch Flyback", "T11": "QR Flyback",
    "T12": "Active Clamp Forward", "T13": "Forward", "T14": "2-Switch Forward",
    "T15": "Half Bridge", "T16": "Push-Pull", "T17": "Full Bridge",
    "T18": "Phase-Shifted Full Bridge", "T19": "Series Capacitor Buck",
    "T20": "LLC Half Bridge", "T21": "LLC Full Bridge",
}
_SUPPORTED = ("T01", "T02", "T03", "T04", "T05", "T06", "T07", "T08", "T09", "T10", "T11", "T12", "T13", "T14", "T15", "T16", "T17", "T18", "T19")


def all_topologies() -> dict[str, str]:
    return dict(TOPOLOGY_NAMES)


def supported_topologies() -> dict[str, str]:
    return {key: TOPOLOGY_NAMES[key] for key in _SUPPORTED}


def _topology_id(name: str) -> str:
    if name.upper() in TOPOLOGY_NAMES:
        return name.upper()
    canonical = name.casefold().replace("_", "-")
    aliases = {value.casefold(): key for key, value in TOPOLOGY_NAMES.items()}
    aliases.update({"buck-boost": "T03", "inverting-buck-boost": "T03", "cuk": "T08"})
    if canonical not in aliases:
        raise UnsupportedTopologyError(f"Unknown topology: {name}")
    return aliases[canonical]


def _validate(inputs: DesignInputs) -> None:
    if not isinstance(inputs.topology, str) or not isinstance(inputs.mode, str):
        raise ModelDomainError("topology and mode must be strings")
    numeric_fields = ("vin_v", "vout_v", "iout_a", "fsw_hz", "inductance_h", "capacitance_f",
                      "turns_ratio_np_ns", "diode_drop_v", "secondary_inductance_h",
                      "transfer_capacitance_f", "magnetizing_inductance_h", "reset_turns_ratio_np_nr",
                      "resonant_capacitance_f", "dead_time_s", "leakage_inductance_h", "primary_capacitance_f", "primary_output_current_a")
    optional = {"secondary_inductance_h", "transfer_capacitance_f", "magnetizing_inductance_h", "resonant_capacitance_f", "leakage_inductance_h", "primary_capacitance_f", "primary_output_current_a"}
    for name in numeric_fields:
        value = getattr(inputs, name)
        if value is None and name in optional:
            continue
        if isinstance(value, bool) or not isinstance(value, Real):
            raise ModelDomainError(f"{name} must be a real scalar, not {type(value).__name__}")
    for name in ("vin_v", "vout_v", "fsw_hz", "inductance_h", "capacitance_f", "turns_ratio_np_ns", "reset_turns_ratio_np_nr"):
        value = getattr(inputs, name)
        if not math.isfinite(value) or value <= 0:
            raise ModelDomainError(f"{name} must be finite and positive (SI units)")
    for name in ("iout_a", "diode_drop_v", "dead_time_s"):
        value = getattr(inputs, name)
        if not math.isfinite(value) or value < 0:
            raise ModelDomainError(f"{name} must be finite and nonnegative")
    for name in ("secondary_inductance_h", "transfer_capacitance_f", "magnetizing_inductance_h", "resonant_capacitance_f", "leakage_inductance_h", "primary_capacitance_f"):
        value = getattr(inputs, name)
        if value is not None and (not math.isfinite(value) or value <= 0):
            raise ModelDomainError(f"{name}, when specified, must be finite and positive")
    if inputs.primary_output_current_a is not None and (not math.isfinite(inputs.primary_output_current_a) or inputs.primary_output_current_a < 0):
        raise ModelDomainError("primary_output_current_a must be finite and nonnegative when specified")
    if inputs.mode.casefold() not in ("auto", "ccm", "dcm", "bcm"):
        raise ModelDomainError("mode must be auto, CCM, DCM or BCM")


def _switch_intervals(inputs: DesignInputs, on_v: float, off_v_magnitude: float,
                      output_gain: float = 1.0, output_continuous: bool = False
                      ) -> tuple[str, tuple[float, float, float], float, float]:
    """Solve charge balance, including a genuine third DCM zero interval."""
    if on_v <= 0 or off_v_magnitude <= 0:
        raise ModelDomainError("positive energizing and demagnetizing voltages are required")
    duty = off_v_magnitude / (on_v + off_v_magnitude)
    ripple = on_v * duty / (inputs.inductance_h * inputs.fsw_hz)
    average = inputs.iout_a / output_gain
    if not output_continuous:
        average /= 1 - duty
    low, high = average - ripple / 2, average + ripple / 2
    tolerance = 1e-11 * max(1.0, high)
    if low >= -tolerance:
        mode = "BCM" if abs(low) <= tolerance else "CCM"
        intervals = duty, 1 - duty, 0.0
        if mode == "BCM":
            low = 0.0
    else:
        mode = "DCM"
        ratio = on_v / off_v_magnitude
        output_factor = output_gain * (1 + ratio if output_continuous else ratio)
        duty = math.sqrt(2 * inputs.iout_a * inputs.inductance_h * inputs.fsw_hz / (on_v * output_factor))
        discharge = ratio * duty
        intervals = duty, discharge, 1 - duty - discharge
        low, high = 0.0, on_v * duty / (inputs.inductance_h * inputs.fsw_hz)
    requested = inputs.mode.upper()
    if requested != "AUTO" and requested != mode and not (mode == "BCM" and requested in ("CCM", "DCM")):
        raise ModelDomainError(f"Requested {requested}, but the operating point requires {mode}; change L, load or frequency")
    return mode, intervals, low, high


def _wave(inputs: DesignInputs, intervals: tuple[float, ...], values: tuple[tuple[float, float], ...],
          unit: str, reference: str) -> Waveform:
    return piecewise(1 / inputs.fsw_hz, intervals, values, unit, reference)


def _voltage(inputs: DesignInputs, intervals: tuple[float, ...], levels: tuple[float, ...], reference: str) -> Waveform:
    return _wave(inputs, intervals, tuple((value, value) for value in levels), "V", reference)


def _finish(inputs: DesignInputs, topology_id: str, mode: str, duty: float,
            devices: dict[str, DeviceWaveforms], input_current: Waveform,
            inductor: Waveform, diode_loss_w: float, *, assumptions: tuple[str, ...] = (),
            warnings: tuple[str, ...] = (), diagnostics: dict[str, float] | None = None,
            metrics: dict[str, float] | None = None, modeled_other_loss_w: float = 0.0, extra_output_power_w: float = 0.0) -> StageResult:
    if "Csplit1" not in devices:
        input_capacitor = input_current.affine(scale=-1, offset=input_current.statistics()["mean"], reference="into input-capacitor positive plate")
        devices["Cin"] = DeviceWaveforms("Input capacitor (stiff DC bus voltage)", input_capacitor,
                                          input_current.affine(scale=0, offset=inputs.vin_v, unit="V", reference="input positive to return"))
    stresses = {}
    for key, device in devices.items():
        current = device.current_a.statistics()
        voltage = device.voltage_v.statistics()
        stresses[key] = {
            **{f"current_{suffix}_a": current[field] for suffix, field in (("min", "min"), ("max", "max"), ("mean", "mean"), ("rms", "rms"), ("ac_rms", "ac_rms"), ("pp", "peak_to_peak"))},
            **{f"voltage_{suffix}_v": voltage[field] for suffix, field in (("min", "min"), ("max", "max"), ("mean", "mean"), ("rms", "rms"), ("ac_rms", "ac_rms"), ("pp", "peak_to_peak"))},
        }
    pin = input_current.statistics()["mean"] * inputs.vin_v
    pout = inputs.vout_v * inputs.iout_a + extra_output_power_w
    cap = devices["Cout"]
    cap_ripple = cap.voltage_v.statistics()["peak_to_peak"]
    if cap_ripple > inputs.vout_v * 0.1:
        warnings += ("Output ripple exceeds 10% of Vout; constant-output-voltage current slopes may be inaccurate.",)
    if cap.voltage_v.statistics()["min"] < 0:
        raise ModelDomainError("Reconstructed output voltage crosses zero; increase output capacitance or use a switching model")
    base_metrics = {
        "inductor_ripple_pp_a": inductor.statistics()["peak_to_peak"],
        "input_current_mean_a": input_current.statistics()["mean"],
        "input_power_w": pin, "output_power_w": pout,
        "actual_switching_frequency_hz": 1/input_current.period_s,
        "modeled_other_loss_w": modeled_other_loss_w,
        "output_voltage_signed_v": -inputs.vout_v if topology_id in ("T03", "T08") else inputs.vout_v,
        "modeled_diode_loss_w": diode_loss_w, "output_ripple_pp_v": cap_ripple,
        **(metrics or {}),
    }
    residuals = {
        "output_capacitor_mean_current_a": cap.current_a.statistics()["mean"],
        "modeled_power_balance_w": pin - pout - diode_loss_w - modeled_other_loss_w,
        **(diagnostics or {}),
    }
    if abs(residuals["output_capacitor_mean_current_a"]) > 1e-7*max(1.0, inputs.iout_a):
        raise ModelDomainError("Output capacitor charge does not close within the model tolerance")
    if abs(residuals["modeled_power_balance_w"]) > 1e-7*max(1.0, abs(pin), pout):
        raise ModelDomainError("Modeled input/output/loss power does not close within the model tolerance")
    return StageResult(
        inputs=inputs, design_id=inputs.design_id, topology_id=topology_id, mode=mode,
        duty=duty, waveforms=devices, stress=stresses, metrics=base_metrics,
        assumptions=("Ideal switches and specified constant diode drop; additional elements/losses appear only where explicitly stated. Unspecified losses are excluded.",
                     "Input is a stiff DC source. Algebraic models use DC output for current slopes; Fly-Buck instead solves both capacitor voltages dynamically.",
                     "RMS and means use the complete displayed switching period, including nonconducting intervals.",
                     "Diode voltage is cathode minus anode; positive current is anode to cathode.") + assumptions,
        warnings=warnings, diagnostics=residuals,
        provenance={"model": "Independent piecewise steady-state circuit equations",
                    "baseline": "TI Power Stage Designer SLVUBB4B, February 2023",
                    "reference_url": "https://www.ti.com/lit/ug/slvubb4b/slvubb4b.pdf",
                    "evidence": "Analytical model; no TI executable or hardware correlation implied"},
    )


def _single_inductor(inputs: DesignInputs, topology_id: str) -> StageResult:
    vin, vo, vf, n = inputs.vin_v, inputs.vout_v, inputs.diode_drop_v, inputs.turns_ratio_np_ns
    if topology_id == "T01":
        if vin <= vo:
            raise ModelDomainError("Buck requires Vin > Vout; bypass and reverse current are not modeled")
        on_v, off_v, gain, continuous = vin - vo, vo + vf, 1.0, True
    elif topology_id == "T02":
        if vo <= vin:
            raise ModelDomainError("Boost requires Vout > Vin; pass-through and buck operation are unsupported")
        on_v, off_v, gain, continuous = vin, vo + vf - vin, 1.0, False
    else:
        if topology_id == "T03":
            n = 1.0
        on_v, off_v, gain, continuous = vin, n * (vo + vf), n, False
    mode, durations, low, high = _switch_intervals(inputs, on_v, off_v, gain, continuous)
    duty, discharge, idle = durations
    inductor_ports = {"T01": ("SW", "OUT"), "T02": ("VIN", "SW"), "T03": ("SW", "GND")}.get(topology_id)
    current_reference = f"{inductor_ports[0]} to {inductor_ports[1]}" if inductor_ports else "inductor current; flyback referred to primary"
    current = _wave(inputs, durations, ((low, high), (high, low), (0, 0)), "A", current_reference)
    switch = _wave(inputs, durations, ((low, high), (0, 0), (0, 0)), "A", "drain to source")
    diode = _wave(inputs, durations, ((0, 0), (gain * high, gain * low), (0, 0)), "A", "anode to cathode")
    if topology_id == "T01":
        qvoltage = (0, vin + vf, vin - vo)
        dvoltage = (vin, -vf, vo)
        output = current
        input_current = switch
    elif topology_id == "T02":
        qvoltage = (0, vo + vf, vin)
        dvoltage = (vo, -vf, vo - vin)
        output = diode
        input_current = current
    else:
        qvoltage = (0, vin + n * (vo + vf), vin)
        dvoltage = (vo + vin / n, -vf, vo)
        output = diode
        input_current = switch
    capacitor = output.affine(offset=-inputs.iout_a, reference="into Cout positive plate")
    voltage_reference = f"{inductor_ports[0]} minus {inductor_ports[1]}" if inductor_ports else "passive sign; flyback primary-referred"
    inductor_voltage = _voltage(inputs, durations, (on_v, -off_v, 0), voltage_reference)
    devices = {
        "Q1": DeviceWaveforms("Main switch", switch, _voltage(inputs, durations, qvoltage, "drain to source")),
        "D1": DeviceWaveforms("Rectifier / freewheel diode", diode, _voltage(inputs, durations, dvoltage, "cathode to anode")),
        "Lm" if topology_id in ("T09", "T10") else "L1": DeviceWaveforms("Primary-referred magnetizing inductance" if topology_id in ("T09", "T10") else "Inductor", current, inductor_voltage),
        "Cout": DeviceWaveforms("Output capacitor", capacitor, capacitor_voltage(capacitor, inputs.capacitance_f, vo)),
    }
    if topology_id in ("T09", "T10"):
        primary_top = "P" if topology_id == "T10" else "VIN"
        devices["primary"] = DeviceWaveforms("Actual primary winding", replace(switch, reference=f"{primary_top} to N (into primary dot)"),
                                             replace(inductor_voltage, reference=f"{primary_top} minus N"))
        devices["secondary"] = DeviceWaveforms("Actual secondary winding", replace(diode, reference="ISO to SEC (return to rectifier terminal)"),
                                               inductor_voltage.affine(scale=-1/n, reference="SEC minus ISO"))
    if topology_id == "T10":
        if n * (vo + vf) >= vin:
            raise ModelDomainError("Two-switch flyback requires n*(Vout+Vf) < Vin to avoid primary clamp conduction")
        q_wave = devices["Q1"].voltage_v.affine(scale=0.5)
        devices["Q1"] = DeviceWaveforms("Upper primary switch", switch, q_wave)
        devices["Q2"] = DeviceWaveforms("Lower primary switch", switch, q_wave)
        clamp_voltage = _voltage(inputs, durations, (vin, (vin - n*(vo+vf))/2, vin/2), "cathode to anode")
        zero = current.affine(scale=0)
        devices["Dclamp1"] = DeviceWaveforms("Upper clamp diode (zero leakage model)", replace(zero, reference="N to VIN (anode to cathode)"),
                                            replace(clamp_voltage, reference="VIN minus N (cathode to anode)"))
        devices["Dclamp2"] = DeviceWaveforms("Lower clamp diode (zero leakage model)", replace(zero, reference="GND to P (anode to cathode)"),
                                            replace(clamp_voltage, reference="P minus GND (cathode to anode)"))
    return _finish(inputs, topology_id, mode, duty, devices, input_current, current,
                   vf * diode.statistics()["mean"],
                   assumptions=("Asynchronous unidirectional conduction; DCM includes a solved zero-current interval.",)
                   + (("Flyback n=Np/Ns; magnetizing current is a primary-referred state, not continuous actual winding current.",) if topology_id in ("T09", "T10") else ())
                   + (("Two-switch flyback assumes symmetric off-state voltage sharing and zero leakage; primary clamp diodes therefore carry zero current in the accepted reflected-voltage domain.",) if topology_id == "T10" else ()) ,
                   diagnostics={"inductor_mean_voltage_v": inductor_voltage.statistics()["mean"]},
                   metrics={"discharge_fraction": discharge, "idle_fraction": idle,
                            "inductor_current_mean_a": current.statistics()["mean"]})


def calculate_stage(inputs: DesignInputs, cancelled=None) -> StageResult:
    """Calculate a supported model or explicitly reject an unavailable domain."""
    if cancelled is not None and cancelled():
        raise InterruptedError("Power-stage calculation cancelled")
    _validate(inputs)
    topology_id = _topology_id(inputs.topology)
    if topology_id not in _SUPPORTED:
        reason = "Use the existing LLC workspace; a separate resonant model is not duplicated here." if topology_id in ("T20", "T21") else "Topology-specific numerical model is not yet connected."
        raise UnsupportedTopologyError(f"{TOPOLOGY_NAMES[topology_id]}: {reason}")
    if inputs.two_switch_options is not None:
        if topology_id != "T05":
            raise ModelDomainError("Two-switch dynamic options apply only to T05")
        from .two_switch_dynamics import calculate_two_switch_dynamic
        return calculate_two_switch_dynamic(inputs, inputs.two_switch_options, cancelled=cancelled)
    if inputs.qr_options is not None:
        if topology_id != "T11":
            raise ModelDomainError("QR controller/capacitance options apply only to T11")
        from .qr_dynamics import calculate_qr_dynamic
        return calculate_qr_dynamic(inputs, inputs.qr_options, cancelled=cancelled)
    if inputs.psfb_options is not None:
        if topology_id != "T18":
            raise ModelDomainError("PSFB commutation options apply only to T18")
        from .psfb_dynamics import calculate_psfb_dynamic
        return calculate_psfb_dynamic(inputs, inputs.psfb_options, cancelled=cancelled)
    if inputs.dead_time_s:
        raise ModelDomainError("Nonzero dead time is not modeled by these ideal-switch waveforms")
    if topology_id == "T04":
        return _fly_buck(inputs, cancelled)
    if topology_id == "T11":
        return _quasi_resonant_flyback(inputs, cancelled)
    if topology_id == "T18":
        return _phase_shifted_bridge(inputs, cancelled)
    if topology_id == "T19":
        return _series_capacitor_buck(inputs)
    if topology_id in ("T12", "T13", "T14"):
        return _forward(inputs, topology_id)
    if topology_id in ("T15", "T16", "T17"):
        return _bridge(inputs, topology_id)
    if topology_id in ("T06", "T07", "T08"):
        return _two_inductor(inputs, topology_id)
    if topology_id == "T05":
        return _two_switch_buck_boost(inputs)
    return _single_inductor(inputs, topology_id)


def _two_switch_buck_boost(inputs: DesignInputs) -> StageResult:
    """Two switches simultaneously on, two diodes conducting during off-time."""
    vin, vo, vf = inputs.vin_v, inputs.vout_v, inputs.diode_drop_v
    mode, durations, low, high = _switch_intervals(inputs, vin, vo + 2*vf)
    if mode == "DCM":
        raise ModelDomainError("Two-switch Buck-Boost currently requires CCM/BCM; floating-node DCM is not resolved")
    current = _wave(inputs, durations, ((low, high), (high, low), (0, 0)), "A", "left to right through L1")
    q = _wave(inputs, durations, ((low, high), (0, 0), (0, 0)), "A", "drain to source")
    diode = _wave(inputs, durations, ((0, 0), (high, low), (0, 0)), "A", "anode to cathode")
    vl = _voltage(inputs, durations, (vin, -vo-2*vf, 0), "left minus right")
    cap = diode.affine(offset=-inputs.iout_a, reference="OUT to GND (into Cout positive plate)")
    devices = {
        "L1": DeviceWaveforms("Energy-transfer inductor", current, vl),
        "Q1": DeviceWaveforms("Input high-side switch", q, _voltage(inputs, durations, (0, vin+vf, 0), "drain to source")),
        "Q2": DeviceWaveforms("Output low-side switch", q, _voltage(inputs, durations, (0, vo+vf, 0), "drain to source")),
        "D1": DeviceWaveforms("Input freewheel diode", diode, _voltage(inputs, durations, (vin, -vf, 0), "cathode to anode")),
        "D2": DeviceWaveforms("Output rectifier diode", diode, _voltage(inputs, durations, (vo, -vf, 0), "cathode to anode")),
        "Cout": DeviceWaveforms("Output capacitor", cap, capacitor_voltage(cap, inputs.capacitance_f, vo)),
    }
    return _finish(inputs, "T05", mode, durations[0], devices, q, current, 2*vf*inputs.iout_a,
                   assumptions=("Two switches turn on together; both diodes conduct together during off-time. This is the non-inverting two-switch topology.",
                                "Buck-only and boost-only gate strategies and DCM floating nodes are outside this model."),
                   diagnostics={"inductor_mean_voltage_v": vl.statistics()["mean"]})


def _two_inductor(inputs: DesignInputs, topology_id: str) -> StageResult:
    """Uncoupled SEPIC / ZETA / Cuk; DCM preserves circulating offsets.

    The switched branch carries i1+i2. In DCM that sum reaches zero while
    the individual inductor currents remain equal and opposite, not clipped.
    """
    if inputs.secondary_inductance_h is None or inputs.transfer_capacitance_f is None:
        raise ModelDomainError("SEPIC/ZETA/Cuk require secondary_inductance_h and transfer_capacitance_f")
    vin, vo, vf, l1, l2 = inputs.vin_v, inputs.vout_v, inputs.diode_drop_v, inputs.inductance_h, inputs.secondary_inductance_h
    equivalent = l1 * l2 / (l1 + l2)
    mode, durations, _, _ = _switch_intervals(replace(inputs, inductance_h=equivalent), vin, vo+vf)
    duty, discharge, idle = durations
    input_average = (vo+vf) * inputs.iout_a / vin
    ripple1 = vin * duty / (l1 * inputs.fsw_hz)
    ripple2 = vin * duty / (l2 * inputs.fsw_hz)
    low1 = input_average - 0.5*ripple1*(duty+discharge)
    low2 = inputs.iout_a - 0.5*ripple2*(duty+discharge)
    high1, high2 = low1+ripple1, low2+ripple2
    one = _wave(inputs, durations, ((low1, high1), (high1, low1), (low1, low1)), "A", "input inductor energy-flow reference")
    second_ports = {"T06": ("GND", "B"), "T07": ("B", "OUT"), "T08": ("OUT", "B")}[topology_id]
    two = _wave(inputs, durations, ((low2, high2), (high2, low2), (low2, low2)), "A", f"{second_ports[0]} to {second_ports[1]}")
    low, high = low1+low2, high1+high2
    q = _wave(inputs, durations, ((low, high), (0, 0), (0, 0)), "A", "drain to source")
    diode = _wave(inputs, durations, ((0, 0), (high, low), (0, 0)), "A", "anode to cathode")
    transfer_ports = ("B", "A") if topology_id == "T07" else ("A", "B")
    transfer_current = _wave(inputs, durations, ((-low2, -high2), (high1, low1), (low1, low1)), "A", f"{transfer_ports[0]} to {transfer_ports[1]} (into Ctransfer positive plate)")
    vc_dc = vin if topology_id == "T06" else vo if topology_id == "T07" else vin+vo
    transfer_voltage = capacitor_voltage(transfer_current, inputs.transfer_capacitance_f, vc_dc,
                                         reference=f"{transfer_ports[0]} minus {transfer_ports[1]}")
    if transfer_voltage.statistics()["peak_to_peak"] > 0.1*vc_dc:
        raise ModelDomainError("Transfer-capacitor ripple exceeds 10%; constant-transfer-voltage model is outside its domain")
    output = diode if topology_id == "T06" else two
    supply = q if topology_id == "T07" else one
    cap = output.affine(offset=-inputs.iout_a, reference="into output-capacitor positive plate")
    vl = _voltage(inputs, durations, (vin, -vo-vf, 0), "passive sign in chosen current direction")
    devices = {
        "L1": DeviceWaveforms("First inductor (uncoupled)", one, vl),
        "L2": DeviceWaveforms("Second inductor (uncoupled)", two, replace(vl, reference=f"{second_ports[0]} minus {second_ports[1]}")),
        "Q1": DeviceWaveforms("Main switch", q, _voltage(inputs, durations, (0, vin+vo+vf, vin), "drain to source")),
        "D1": DeviceWaveforms("Rectifier diode", diode, _voltage(inputs, durations, (vin+vo, -vf, vo), "cathode to anode")),
        "Ctransfer": DeviceWaveforms("Energy-transfer capacitor", transfer_current, transfer_voltage),
        "Cout": DeviceWaveforms("Output capacitor", cap, capacitor_voltage(cap, inputs.capacitance_f, vo)),
    }
    return _finish(inputs, topology_id, mode, duty, devices, supply, one, vf*inputs.iout_a,
                   assumptions=("Two uncoupled inductors; transfer-capacitor mean is Vin (SEPIC), Vout (ZETA), Vin+abs(Vout) (Cuk).",
                                "DCM means zero switch/diode sum current; individual inductor circulating currents are retained.",
                                "Transfer-capacitor ripple is reconstructed; its influence on current slopes and semiconductor voltage ripple is neglected."),
                   diagnostics={"inductor_mean_voltage_v": vl.statistics()["mean"],
                                "transfer_capacitor_mean_current_a": transfer_current.statistics()["mean"]},
                   metrics={"inductor2_ripple_pp_a": ripple2, "transfer_capacitor_voltage_mean_v": vc_dc,
                            "discharge_fraction": discharge, "idle_fraction": idle})


def _event_wave(inputs: DesignInputs, events: list[float], function, unit: str, reference: str) -> Waveform:
    """Evaluate each linear interval with its interior state to preserve jumps."""
    durations, values = [], []
    points = sorted({max(0.0, min(1.0, float(t))) for t in events})
    for start, end in itertools.pairwise(points):
        if end - start > 1e-14:
            state = (start + end)/2
            durations.append(end-start)
            values.append((function(start, state), function(end, state)))
    return _wave(inputs, tuple(durations), tuple(values), unit, reference)


def _forward(inputs: DesignInputs, topology_id: str) -> StageResult:
    if inputs.magnetizing_inductance_h is None:
        raise ModelDomainError("Forward models require magnetizing_inductance_h; inductance_h is the output inductor")
    active = topology_id == "T12"
    if active and inputs.transfer_capacitance_f is None:
        raise ModelDomainError("Active-clamp Forward requires transfer_capacitance_f for the clamp capacitor")
    vin, vo, vf, n = inputs.vin_v, inputs.vout_v, inputs.diode_drop_v, inputs.turns_ratio_np_ns
    vs = vin/n
    mode, durations, low, high = _switch_intervals(inputs, vs-vo-vf, vo+vf, output_continuous=True)
    duty, discharge, _ = durations
    imag = vin*duty/(inputs.magnetizing_inductance_h*inputs.fsw_hz)
    if active:
        reset_v = vin*duty/(1-duty)
        reset_time = 1-duty
        reset_ratio = 1.0
    else:
        reset_ratio = inputs.reset_turns_ratio_np_nr if topology_id == "T13" else 1.0
        reset_v = reset_ratio*(vin+vf) if topology_id == "T13" else vin+2*vf
        reset_time = vin*duty/reset_v
        if duty+reset_time > 1+1e-12:
            raise ModelDomainError("Insufficient transformer reset time; lower duty or change the reset turns ratio")
    events = [0, duty, duty+discharge, duty+reset_time, 1]

    def il(t, state):
        if state < duty:
            return low+(high-low)*t/duty
        if state < duty+discharge:
            return high-(high-low)*(t-duty)/discharge
        return 0.0

    def im(t, state):
        if state < duty:
            return imag*t/duty-(imag/2 if active else 0)
        if state < duty+reset_time:
            return imag*(1-(t-duty)/reset_time)-(imag/2 if active else 0)
        return 0.0

    def vp(t, state):
        return vin if state < duty else -reset_v if state < duty+reset_time else 0.0

    def node(t, state):
        return vs-vf if state < duty else -vf if state < duty+discharge else vo

    def primary(t, state):
        if state < duty:
            return il(t,state)/n+im(t,state)
        return im(t,state) if active or topology_id == "T14" else 0.0

    def supply(t, state):
        if state < duty:
            return primary(t,state)
        if not active and state < duty+reset_time:
            return -reset_ratio*im(t,state)
        return 0.0

    wave = lambda fn, unit, ref: _event_wave(inputs, events, fn, unit, ref)
    inductor = wave(il,"A","output inductor current toward output")
    magnetizing = wave(im,"A","primary-referred magnetizing state")
    primary_current = wave(primary,"A","primary dot into transformer")
    primary_voltage = wave(vp,"V","primary dot minus undotted terminal")
    switch = wave(lambda t,s: primary(t,s) if s < duty else 0,"A","drain to source")
    switch_voltage = wave(lambda t,s: 0 if s < duty else (vin+reset_v if active or topology_id == "T13" else vin+vf) if s < duty+reset_time else vin if topology_id == "T13" else vin/2,"V","drain to source")
    rectifier = wave(lambda t,s: il(t,s) if s < duty else 0,"A","anode to cathode")
    freewheel = wave(lambda t,s: il(t,s) if duty < s < duty+discharge else 0,"A","anode to cathode")
    capacitor = inductor.affine(offset=-inputs.iout_a, reference="OUT to ISO (into Cout positive plate)")
    vl = wave(lambda t,s: vs-vo-vf if s < duty else -vo-vf if s < duty+discharge else 0,"V","passive inductor sign")
    devices = {
        "L1": DeviceWaveforms("Output inductor", inductor, vl),
        "Lm": DeviceWaveforms("Primary-referred magnetizing state", magnetizing, primary_voltage),
        "primary": DeviceWaveforms("Actual primary winding", replace(primary_current, reference=("P" if topology_id == "T14" else "VIN")+" to N (into primary dot)"),
                                   replace(primary_voltage, reference=("P" if topology_id == "T14" else "VIN")+" minus N")),
        "secondary": DeviceWaveforms("Actual secondary winding", replace(rectifier, reference="ISO to SEC (return to rectifier terminal)"),
                                     primary_voltage.affine(scale=1/n, reference="SEC minus ISO")),
        "Q1": DeviceWaveforms("Main switch", switch, switch_voltage),
        "D1": DeviceWaveforms("Forward rectifier", rectifier, wave(lambda t,s: node(t,s)-vp(t,s)/n,"V","cathode to anode")),
        "D2": DeviceWaveforms("Output freewheel diode", freewheel, wave(node,"V","cathode to anode")),
        "Cout": DeviceWaveforms("Output capacitor", capacitor, capacitor_voltage(capacitor, inputs.capacitance_f, vo)),
    }
    diode_loss = vf*inputs.iout_a
    assumptions = ("inductance_h is output L; magnetizing_inductance_h is primary-referred Lm; n=Np/Ns.",)
    if active:
        clamp = wave(lambda t,s: im(t,s) if s > duty else 0,"A","CLAMP to VIN (into Cclamp positive plate)")
        clamp_voltage = capacitor_voltage(clamp, inputs.transfer_capacitance_f, reset_v, reference="CLAMP minus VIN")
        if clamp_voltage.statistics()["peak_to_peak"] > 0.1*max(reset_v, 1e-12):
            raise ModelDomainError("Clamp-capacitor ripple exceeds 10%; constant clamp-voltage model is outside its domain")
        devices["Q2"] = DeviceWaveforms("Active-clamp switch", replace(clamp, reference="N to CLAMP (source to drain; signed)"),
                                        wave(lambda t,s: vin+reset_v if s < duty else 0,"V","CLAMP minus N (drain to source)"))
        devices["Cclamp"] = DeviceWaveforms("Clamp capacitor", clamp, clamp_voltage)
        assumptions += ("Complementary ideal active-clamp operation; bipolar magnetizing current. Dead time, leakage resonance and ZVS are not solved.",)
    else:
        reset_current = wave(lambda t,s: reset_ratio*im(t,s) if duty < s < duty+reset_time else 0,"A","return current into input supply")
        reset_diode_v = wave(lambda t,s: vin+vin/reset_ratio if s < duty else -vf if s < duty+reset_time else vin,"V","cathode to anode")
        if topology_id == "T14":
            reset_diode_v = wave(lambda t,s: vin if s < duty else -vf if s < duty+reset_time else vin/2,"V","cathode to anode")
            devices["Q2"] = DeviceWaveforms("Second primary switch", switch, switch_voltage)
            devices["Dreset2"] = DeviceWaveforms("Second reset clamp diode", reset_current, reset_diode_v)
        else:
            devices["reset_winding"] = DeviceWaveforms("Reset winding", replace(reset_current, reference="GND to RESET (into reset winding dot)"),
                                                       primary_voltage.affine(scale=-1/reset_ratio, reference="RESET minus GND"))
        devices["Dreset1"] = DeviceWaveforms("Transformer reset diode", reset_current, reset_diode_v)
        diode_loss += vf*reset_current.statistics()["mean"]*(2 if topology_id == "T14" else 1)
        assumptions += ("Magnetic reset interval is solved separately from the output-inductor freewheel interval, including reset diode drops.",
                        "After reset, zero-current rectifier at its conduction threshold is allowed; parasitic current redistribution is omitted.")
    return _finish(inputs, topology_id, mode, duty, devices, wave(supply,"A","from DC input supply"), inductor, diode_loss,
                   assumptions=assumptions,
                   diagnostics={"inductor_mean_voltage_v": vl.statistics()["mean"],
                                "transformer_mean_voltage_v": primary_voltage.statistics()["mean"]},
                   metrics={"reset_fraction": reset_time,"reset_voltage_primary_v": reset_v,
                            "magnetizing_ripple_pp_a": imag,"discharge_fraction": discharge})


_HALF_BRIDGE_SPLIT_ASSUMPTION = (
    "Equal split input capacitors: iCsplit1=-ip/2 and iCsplit2=ip/2, with their voltages summing to Vin. "
    "Current slopes and primary voltage use the nominal Vin/2 midpoint; reconstructed capacitor voltages "
    "must stay positive with peak-to-peak ripple no greater than 10% of Vin/2. Midpoint ripple is not fed back into the magnetic states."
)


def _bridge_split_capacitors(inputs: DesignInputs, primary_current: Waveform) -> dict[str, DeviceWaveforms]:
    """Equal series-capacitor node KCL with a stiff total DC-bus voltage."""
    devices = {}
    # Primary current enters the midpoint. Ctop+Cbottom therefore carries ip,
    # while d(Vtop+Vbottom)/dt=0: the two positive-plate currents are opposite.
    # The source current is then Q1+iCtop=(Q1+Q2)/2, not its cycle average.
    for key, scale, reference in (("Csplit1", -.5, "input positive to midpoint"),
                                  ("Csplit2", .5, "midpoint to input return")):
        current = primary_current.affine(scale=scale, reference="into split-capacitor positive plate")
        voltage = capacitor_voltage(current, inputs.transfer_capacitance_f, inputs.vin_v/2, reference)
        stats = voltage.statistics()
        if stats["min"] <= 0:
            raise ModelDomainError(f"Half Bridge {key} reconstructed voltage reaches or crosses zero; increase split capacitance or use a dynamic midpoint model")
        if stats["peak_to_peak"] > .1*(inputs.vin_v/2):
            raise ModelDomainError(f"Half Bridge {key} ripple exceeds 10% of Vin/2; the fixed-midpoint model is outside its domain")
        devices[key] = DeviceWaveforms("Split DC-input capacitor", current, voltage)
    return devices


def _bridge(inputs: DesignInputs, topology_id: str) -> StageResult:
    """Symmetric PWM bridges with center-tapped secondary.

    During open-primary freewheel, secondary-current imbalance carries Lm
    current. This is distinct from PSFB primary circulating current.
    """
    if inputs.magnetizing_inductance_h is None:
        raise ModelDomainError("Bridge models require magnetizing_inductance_h")
    half = topology_id == "T15"
    if half and inputs.transfer_capacitance_f is None:
        raise ModelDomainError("Half bridge requires transfer_capacitance_f for each split input capacitor")
    vin, vo, vf, n = inputs.vin_v, inputs.vout_v, inputs.diode_drop_v, inputs.turns_ratio_np_ns
    va = vin/2 if half else vin
    vs = va/n
    mode, half_durations, low, high = _switch_intervals(replace(inputs, fsw_hz=2*inputs.fsw_hz, mode="auto"), vs-vo-vf, vo+vf, output_continuous=True)
    duty = half_durations[0]
    imag = va*duty/(2*inputs.fsw_hz*inputs.magnetizing_inductance_h)
    h = imag/2
    if mode != "CCM" or low < n*h:
        return _bridge_demagnetizing(inputs, topology_id)
    if inputs.mode.casefold() not in ("auto", "ccm"):
        raise ModelDomainError("Requested mode does not match the bridge CCM operating point")
    durations = (duty/2,(1-duty)/2,duty/2,(1-duty)/2)
    il_values = ((low,high),(high,low),(low,high),(high,low))
    il = _wave(inputs,durations,il_values,"A","output inductor current")
    im = _wave(inputs,durations,((-h,h),(h,h),(h,-h),(-h,-h)),"A","primary-referred magnetizing current")
    ip_low, ip_high = low/n-h, high/n+h
    ip = _wave(inputs,durations,((ip_low,ip_high),(0,0),(-ip_low,-ip_high),(0,0)),"A","primary dot into transformer")
    vp = _voltage(inputs,durations,(va,0,-va,0),"primary dot minus undotted terminal")
    q1 = _wave(inputs,durations,((ip_low,ip_high),(0,0),(0,0),(0,0)),"A","drain to source")
    q2 = _wave(inputs,durations,((0,0),(0,0),(ip_low,ip_high),(0,0)),"A","drain to source")
    d1 = _wave(inputs,durations,((low,high),((high-n*h)/2,(low-n*h)/2),(0,0),((high+n*h)/2,(low+n*h)/2)),"A","anode to cathode")
    d2 = _wave(inputs,durations,((0,0),((high+n*h)/2,(low+n*h)/2),(low,high),((high-n*h)/2,(low-n*h)/2)),"A","anode to cathode")
    q_block = 2*vin if topology_id == "T16" else vin
    q_mid = vin if topology_id == "T16" else vin/2
    q1v = _voltage(inputs,durations,(0,q_mid,q_block,q_mid),"drain to source")
    q2v = _voltage(inputs,durations,(q_block,q_mid,0,q_mid),"drain to source")
    capacitor = il.affine(offset=-inputs.iout_a, reference="OUT to ISO (into Cout positive plate)")
    vl = _voltage(inputs,durations,(vs-vo-vf,-vo-vf,vs-vo-vf,-vo-vf),"passive inductor sign")
    supply = _wave(inputs,durations,((ip_low,ip_high),(0,0),(ip_low,ip_high),(0,0)),"A","equivalent DC source current").affine(scale=0.5 if half else 1)
    devices = {
        "L1": DeviceWaveforms("Output inductor",il,vl),
        "Lm": DeviceWaveforms("Primary-referred magnetizing state",im,vp),
        "primary": DeviceWaveforms("Primary winding",replace(ip,reference="A to MID" if half else "A to B"),
                                   replace(vp,reference="A minus MID" if half else "A minus B")),
        "secondary1": DeviceWaveforms("Positive secondary half",replace(d1,reference="ISO to S1 (center tap to upper outer terminal)"),vp.affine(scale=1/n,reference="S1 minus ISO")),
        "secondary2": DeviceWaveforms("Negative secondary half",replace(d2,reference="ISO to S2 (center tap to lower outer terminal)"),vp.affine(scale=-1/n,reference="S2 minus ISO")),
        "Q1": DeviceWaveforms("First primary switch",q1,q1v),
        "Q2": DeviceWaveforms("Second primary switch",q2,q2v),
        "D1": DeviceWaveforms("First secondary rectifier",d1,_voltage(inputs,durations,(-vf,-vf,2*vs-vf,-vf),"cathode to anode")),
        "D2": DeviceWaveforms("Second secondary rectifier",d2,_voltage(inputs,durations,(2*vs-vf,-vf,-vf,-vf),"cathode to anode")),
        "Cout": DeviceWaveforms("Output capacitor",capacitor,capacitor_voltage(capacitor,inputs.capacitance_f,vo)),
    }
    if topology_id == "T17":
        devices["Q3"] = DeviceWaveforms("First diagonal paired switch",q1,q1v)
        devices["Q4"] = DeviceWaveforms("Second diagonal paired switch",q2,q2v)
    if topology_id == "T16":
        devices["primary1"] = DeviceWaveforms("First center-tapped primary half",replace(q1,reference="VIN to P1 (center tap to upper outer terminal)"),replace(vp,reference="VIN minus P1 (dotted center tap minus upper outer terminal)"))
        devices["primary2"] = DeviceWaveforms("Second center-tapped primary half",replace(q2,reference="VIN to P2 (center tap to lower outer terminal)"),vp.affine(scale=-1,reference="VIN minus P2 (center tap minus dotted lower outer terminal)"))
        del devices["primary"]
    if half:
        devices.update(_bridge_split_capacitors(inputs, ip))
    return _finish(inputs,topology_id,mode,duty,devices,supply,il,vf*inputs.iout_a,
                   assumptions=("fsw is a full bipolar bridge period; output-inductor ripple frequency is 2*fsw. Each main switch conducts D/2 of a full period.",
                                "Center-tapped secondary; n=Np/Ns uses one secondary half. Push-pull Np is one primary half.",
                                "Both main diagonals are off during freewheel; secondary current imbalance sustains magnetizing flux. Symmetric device blocking-voltage sharing is assumed.",
                                "This result uses the ordinary CCM branch; joint magnetic-state CCM/BCM/DCM is resolved separately. Commutation, leakage and parasitic ringing are outside these ideal bridge branches.")
                               + ((_HALF_BRIDGE_SPLIT_ASSUMPTION,) if half else ()),
                   diagnostics={"inductor_mean_voltage_v":vl.statistics()["mean"],"transformer_mean_voltage_v":vp.statistics()["mean"]},
                   metrics={"per_switch_duty":duty/2,"rectified_ripple_frequency_hz":2*inputs.fsw_hz,"magnetizing_ripple_pp_a":imag})


def _series_capacitor_buck(inputs: DesignInputs) -> StageResult:
    if inputs.secondary_inductance_h is None or inputs.transfer_capacitance_f is None:
        raise ModelDomainError("Series-capacitor Buck requires the second phase inductance and series capacitance")
    if inputs.diode_drop_v:
        raise ModelDomainError("Series-capacitor Buck uses ideal synchronous FETs; nonzero diode-drop operation is not modeled")
    vin,vo,fs = inputs.vin_v,inputs.vout_v,inputs.fsw_hz
    duty = 2*vo/vin
    if duty > .5:
        raise ModelDomainError("Nonoverlapping series-capacitor Buck requires Vout <= Vin/4 (phase duty <= 0.5)")
    average = inputs.iout_a/2
    l1,l2 = inputs.inductance_h,inputs.secondary_inductance_h
    ripples = [(vin/2-vo)*duty/(inductance*fs) for inductance in (l1,l2)]
    if average <= max(ripples)/2:
        raise ModelDomainError("Series-capacitor Buck model requires positive CCM current in both phases")
    if inputs.mode.casefold() not in ("auto","ccm"):
        raise ModelDomainError("Series-capacitor Buck supports CCM only")
    events = [0,duty,.5,.5+duty,1]
    def phase(t,state,shift,inductance,ripple):
        tau = t-shift if state>=shift else t-shift+1
        tau_state = state-shift if state>=shift else state-shift+1
        low,high = average-ripple/2,average+ripple/2
        if tau_state < duty:
            return low+(vin/2-vo)*tau/(inductance*fs)
        return high-vo*(tau-duty)/(inductance*fs)
    a = lambda t,s: phase(t,s,0,l1,ripples[0])
    b = lambda t,s: phase(t,s,.5,l2,ripples[1])
    a_on = lambda s: s<duty
    b_on = lambda s: .5<s<.5+duty
    wave = lambda fn,unit,ref: _event_wave(inputs,events,fn,unit,ref)
    ia,ib = wave(a,"A","phase A toward output"),wave(b,"A","phase B toward output")
    source = wave(lambda t,s:a(t,s) if a_on(s) else 0,"A","from input supply")
    series_current = wave(lambda t,s:a(t,s) if a_on(s) else -b(t,s) if b_on(s) else 0,"A","TOP to A (into Cseries positive plate)")
    series_voltage = capacitor_voltage(series_current,inputs.transfer_capacitance_f,vin/2,reference="TOP minus A")
    if series_voltage.statistics()["peak_to_peak"] > .1*vin/2:
        raise ModelDomainError("Series-capacitor voltage ripple exceeds 10%; small-ripple approximation is outside its domain")
    cap = wave(lambda t,s:a(t,s)+b(t,s)-inputs.iout_a,"A","into output-capacitor positive plate")
    devices = {
        "L1": DeviceWaveforms("Phase A inductor",ia,wave(lambda t,s:vin/2-vo if a_on(s) else -vo,"V","switch node A minus output")),
        "L2": DeviceWaveforms("Phase B inductor",ib,wave(lambda t,s:vin/2-vo if b_on(s) else -vo,"V","switch node B minus output")),
        "Q1a": DeviceWaveforms("Phase A high-side switch",source,wave(lambda t,s:0 if a_on(s) else vin/2,"V","drain to source")),
        "Q1b": DeviceWaveforms("Phase B high-side switch",wave(lambda t,s:b(t,s) if b_on(s) else 0,"A","drain to source"),wave(lambda t,s:vin if a_on(s) else 0 if b_on(s) else vin/2,"V","drain to source")),
        "Q2a": DeviceWaveforms("Phase A low-side switch",wave(lambda t,s:0 if a_on(s) else a(t,s)+b(t,s) if b_on(s) else a(t,s),"A","source to drain freewheel current"),wave(lambda t,s:vin/2 if a_on(s) else 0,"V","drain to source")),
        "Q2b": DeviceWaveforms("Phase B low-side switch",wave(lambda t,s:0 if b_on(s) else b(t,s),"A","source to drain freewheel current"),wave(lambda t,s:vin/2 if b_on(s) else 0,"V","drain to source")),
        "Cseries": DeviceWaveforms("Series energy-transfer capacitor",series_current,series_voltage),
        "Cout": DeviceWaveforms("Output capacitor",cap,capacitor_voltage(cap,inputs.capacitance_f,vo)),
    }
    result = _finish(inputs,"T19","CCM",duty,devices,source,ia,0,
                    assumptions=("Two synchronous phases, 180 degrees apart, equal average currents Io/2; high-side pulses never overlap.",
                                 "Series capacitor mean is Vin/2. Q1b blocks full Vin during phase A on-time; Q2a carries both inductor currents during phase B on-time.",
                                 "Constant series-capacitor voltage used for current slopes; reconstructed ripple is first order."),
                    diagnostics={"inductor_mean_voltage_v":devices["L1"].voltage_v.statistics()["mean"],
                                 "inductor2_mean_voltage_v":devices["L2"].voltage_v.statistics()["mean"],
                                 "series_capacitor_mean_current_a":series_current.statistics()["mean"]},
                    metrics={"inductor2_ripple_pp_a":ripples[1],"phase_shift_deg":180,"series_capacitor_mean_voltage_v":vin/2})
    return replace(result,provenance={**result.provenance,"reference_url":"https://www.ti.com/lit/an/slva750a/slva750a.pdf","supplement":"TI SLVA750A, May 2016, sections 3.1–3.4"})


def _phase_shifted_bridge(inputs: DesignInputs, cancelled=None) -> StageResult:
    """Constant-Vout, finite-Llk/Lm diode-secondary interval model.

    Rectifier commutation keeps both diodes conducting (Vp=0). Single-diode
    transfer and zero-bridge circulating intervals solve the coupled three
    inductor equations, rather than dropping leakage duty or circulating RMS.
    """
    from scipy.optimize import brentq
    if inputs.magnetizing_inductance_h is None or inputs.leakage_inductance_h is None:
        raise ModelDomainError("PSFB requires primary magnetizing_inductance_h and leakage_inductance_h")
    if inputs.iout_a <= 0:
        raise ModelDomainError("PSFB requires positive output current; no-load controller/flux policy is not modeled")
    vin, vo, vf, n, lo, lm, lk = inputs.vin_v, inputs.vout_v, inputs.diode_drop_v, inputs.turns_ratio_np_ns, inputs.inductance_h, inputs.magnetizing_inductance_h, inputs.leakage_inductance_h
    half_period = .5/inputs.fsw_hz
    load_voltage = vo+vf
    conductance = 1/lk+1/lm+1/(n*n*lo)
    vp_on = (vin/lk+load_voltage/(n*lo))/conductance
    vp_zero = load_voltage/(n*lo)/conductance
    slope_on = (vp_on/n-load_voltage)/lo
    slope_zero = (vp_zero/n-load_voltage)/lo
    slope_comm = -load_voltage/lo
    comm_denominator = n*vin/lk-slope_comm
    if slope_on <= 0:
        raise ModelDomainError("PSFB cannot sustain the requested output with these inductances")
    boundary_load = .5*slope_on*(-slope_zero)*half_period/(slope_on-slope_zero)
    boundary_tolerance = 1e-11*max(1.0, boundary_load)
    if inputs.iout_a <= boundary_load+boundary_tolerance:
        return _phase_shifted_bridge_discontinuous(inputs, vp_on, vp_zero, boundary_load)
    if inputs.mode.casefold() not in ("auto", "ccm"):
        raise ModelDomainError("Requested PSFB mode does not match the positive-current CCM operating point")
    if n*vin/lk <= -slope_comm:
        raise ModelDomainError("PSFB cannot sustain positive-current commutation with these inductances")
    minimum_duty = -slope_zero/(slope_on-slope_zero)

    def solution(duty):
        if cancelled is not None and cancelled():
            raise InterruptedError("PSFB calculation cancelled")
        ton, tz = duty*half_period, (1-duty)*half_period
        i0 = comm_denominator*(slope_on*ton+slope_zero*tz)/(2*(slope_on-slope_comm))
        tc = 2*i0/comm_denominator
        ta = ton-tc
        if min(i0,tc,ta,tz)<-1e-12:
            raise ModelDomainError("PSFB commutation consumes the available phase-shift pulse")
        i1 = i0+slope_comm*tc
        i2 = i1+slope_on*ta
        i3 = i2+slope_zero*tz
        mean = ((i0+i1)*tc+(i1+i2)*ta+(i2+i3)*tz)/(2*half_period)
        return mean, (tc,ta,tz), (i0,i1,i2,i3)

    low_d, high_d = minimum_duty+1e-12, 1-1e-12
    low_load, high_load = solution(low_d)[0], solution(high_d)[0]
    if not low_load < inputs.iout_a < high_load:
        raise ModelDomainError(f"PSFB CCM current must be between {low_load:.6g} and {high_load:.6g} A for this commutation model")
    duty = brentq(lambda d:solution(d)[0]-inputs.iout_a,low_d,high_d,xtol=1e-14)
    _, times, il_points = solution(duty)
    tc,ta,tz=times
    im0 = -(vp_on*ta+vp_zero*tz)/(2*lm)
    im_points = (im0,im0,im0+vp_on*ta/lm,-im0)
    ip_points = (im0-il_points[0]/n, im0+il_points[1]/n,
                 im_points[2]+il_points[2]/n, -im0+il_points[3]/n)
    if min(il_points)<=0:
        raise ModelDomainError("PSFB output current reaches zero; DCM is outside this model")
    duration_half = tuple(time*inputs.fsw_hz for time in times)
    durations=duration_half+duration_half
    pairs=lambda x:tuple(itertools.pairwise(x))
    il_pairs=pairs(il_points)
    im_pairs=pairs(im_points)
    ip_pairs=pairs(ip_points)
    negate=lambda values:tuple((-a,-b) for a,b in values)
    il=_wave(inputs,durations,il_pairs+il_pairs,"A","output inductor toward output")
    im=_wave(inputs,durations,im_pairs+negate(im_pairs),"A","primary-referred magnetizing current")
    ip=_wave(inputs,durations,ip_pairs+negate(ip_pairs),"A","primary dot into transformer")
    vp=_voltage(inputs,durations,(0,vp_on,vp_zero,0,-vp_on,-vp_zero),"primary dot minus undotted terminal")
    source=_wave(inputs,durations,ip_pairs[:2]+((0,0),)+ip_pairs[:2]+((0,0),),"A","from DC supply; negative during commutation energy return")
    # During overlap, diode currents are set by IL and transformer ampere-turns.
    comm_d1=(0,il_points[1])
    comm_d2=(il_points[0],0)
    d1_values=(comm_d1,il_pairs[1],il_pairs[2],comm_d2,(0,0),(0,0))
    d2_values=(comm_d2,(0,0),(0,0),comm_d1,il_pairs[1],il_pairs[2])
    d1=_wave(inputs,durations,d1_values,"A","anode to cathode")
    d2=_wave(inputs,durations,d2_values,"A","anode to cathode")
    zeros=((0,0),)*3
    q1=_wave(inputs,durations,ip_pairs+zeros,"A","drain to source, signed reverse-channel conduction")
    q2=_wave(inputs,durations,zeros+ip_pairs,"A","drain to source, signed reverse-channel conduction")
    q3=_wave(inputs,durations,((0,0),(0,0),(-ip_pairs[2][0],-ip_pairs[2][1]))+ip_pairs[:2]+((0,0),),"A","drain to source, signed reverse-channel conduction")
    q4=_wave(inputs,durations,ip_pairs[:2]+((0,0),(0,0),(0,0),(-ip_pairs[2][0],-ip_pairs[2][1])),"A","drain to source, signed reverse-channel conduction")
    cap=il.affine(offset=-inputs.iout_a,reference="OUT to ISO (into Cout positive plate)")
    vl=_voltage(inputs,durations,(-load_voltage,vp_on/n-load_voltage,vp_zero/n-load_voltage)*2,"passive output-inductor voltage")
    devices={
        "L1":DeviceWaveforms("Output inductor",il,vl),
        "Lleak":DeviceWaveforms("Primary series/leakage inductance",ip,_voltage(inputs,durations,(vin,vin-vp_on,-vp_zero,-vin,-vin+vp_on,vp_zero),"bridge minus transformer voltage")),
        "Lm":DeviceWaveforms("Magnetizing state",im,vp),
        "primary":DeviceWaveforms("Transformer primary",replace(ip,reference="P to B (into primary dot)"),replace(vp,reference="P minus B")),
        "secondary1":DeviceWaveforms("Positive secondary half",replace(d1,reference="ISO to S1 (center tap to upper outer terminal)"),vp.affine(scale=1/n,reference="S1 minus ISO")),
        "secondary2":DeviceWaveforms("Negative secondary half",replace(d2,reference="ISO to S2 (center tap to lower outer terminal)"),vp.affine(scale=-1/n,reference="S2 minus ISO")),
        "Q1":DeviceWaveforms("Leg A high-side FET",q1,_voltage(inputs,durations,(0,0,0,vin,vin,vin),"drain to source")),
        "Q2":DeviceWaveforms("Leg A low-side FET",q2,_voltage(inputs,durations,(vin,vin,vin,0,0,0),"drain to source")),
        "Q3":DeviceWaveforms("Leg B high-side FET",q3,_voltage(inputs,durations,(vin,vin,0,0,0,vin),"drain to source")),
        "Q4":DeviceWaveforms("Leg B low-side FET",q4,_voltage(inputs,durations,(0,0,vin,vin,vin,0),"drain to source")),
        "D1":DeviceWaveforms("Positive rectifier",d1,_voltage(inputs,durations,(-vf,-vf,-vf,-vf,2*vp_on/n-vf,2*vp_zero/n-vf),"cathode to anode")),
        "D2":DeviceWaveforms("Negative rectifier",d2,_voltage(inputs,durations,(-vf,2*vp_on/n-vf,2*vp_zero/n-vf,-vf,-vf,-vf),"cathode to anode")),
        "Cout":DeviceWaveforms("Output capacitor",cap,capacitor_voltage(cap,inputs.capacitance_f,vo)),
    }
    circulating=_wave(inputs,durations,((0,0),(0,0),ip_pairs[2],(0,0),(0,0),(-ip_pairs[2][0],-ip_pairs[2][1])),"A","primary current during commanded zero states")
    return _finish(inputs,"T18","CCM",duty,devices,source,il,vf*inputs.iout_a,
                   assumptions=("Each leg uses complementary 50% gate duty; phase shift determines bridge active fraction. No dead time or Coss transition is solved.",
                                "Finite primary leakage, magnetizing and output inductances determine overlap, duty loss and circulating-current slopes.",
                                "Center-tapped secondary; n uses one secondary half. Ideal reverse FET-channel conduction permits returned energy.",
                                "CCM analytical enhancement; neither ZVS nor the TI software's 50–100% rated-load baseline is inferred from this solution."),
                   warnings=("ZVS is UNKNOWN: Coss, nonlinear charge, dead-time and commutation energy are not verified.",),
                   diagnostics={"inductor_mean_voltage_v":vl.statistics()["mean"],"transformer_mean_voltage_v":vp.statistics()["mean"],"leakage_mean_voltage_v":devices["Lleak"].voltage_v.statistics()["mean"]},
                   metrics={"phase_shift_deg":180*duty,"per_switch_duty":.5,"commutation_time_s":tc,
                            "effective_transfer_fraction":2*ta*inputs.fsw_hz,"circulating_current_rms_a":circulating.statistics()["rms"],
                            "rectified_ripple_frequency_hz":2*inputs.fsw_hz})


def _phase_shifted_bridge_discontinuous(inputs: DesignInputs, vp_on: float,
                                       vp_freewheel: float, boundary_load: float) -> StageResult:
    """Diode-secondary DCM/BCM; zero secondary current retains primary flux."""
    vin, vo, vf, n = inputs.vin_v, inputs.vout_v, inputs.diode_drop_v, inputs.turns_ratio_np_ns
    lo, lm = inputs.inductance_h, inputs.magnetizing_inductance_h
    half_period = .5/inputs.fsw_hz
    a = (vp_on/n-vo-vf)/lo
    b = (vo+vf-vp_freewheel/n)/lo
    boundary = abs(inputs.iout_a-boundary_load) <= 1e-11*max(1.0, boundary_load)
    if boundary:
        active = b*half_period/(a+b)
        freewheel = half_period-active
        idle = 0.0
    else:
        active = math.sqrt(2*inputs.iout_a*half_period/(a*(1+a/b)))
        freewheel = a*active/b
        idle = half_period-active-freewheel
    mode = "BCM" if boundary else "DCM"
    requested = inputs.mode.upper()
    if requested != "AUTO" and requested != mode and not (mode == "BCM" and requested in ("CCM", "DCM")):
        raise ModelDomainError(f"Requested {requested}, but PSFB output current requires {mode}")
    peak = a*active
    im0 = -(vp_on*active+vp_freewheel*freewheel)/(2*lm)
    im1 = im0+vp_on*active/lm
    im2 = -im0
    il_half = ((0,peak),(peak,0),(0,0))
    im_half = ((im0,im1),(im1,im2),(im2,im2))
    ip_half = ((im0,im1+peak/n),(im1+peak/n,im2),(im2,im2))
    negate = lambda pairs: tuple((-x,-y) for x,y in pairs)
    zeros = ((0,0),)*3
    durations = tuple(t*inputs.fsw_hz for t in (active,freewheel,idle))*2
    wave = lambda values,ref: _wave(inputs,durations,values,"A",ref)
    voltage = lambda levels,ref: _voltage(inputs,durations,levels,ref)
    il = wave(il_half+il_half,"output inductor toward output")
    im = wave(im_half+negate(im_half),"primary-referred magnetizing current")
    ip = wave(ip_half+negate(ip_half),"primary dot into transformer")
    vp = voltage((vp_on,vp_freewheel,0,-vp_on,-vp_freewheel,0),"primary dot minus undotted terminal")
    vl = voltage((vp_on/n-vo-vf,vp_freewheel/n-vo-vf,0)*2,"passive output-inductor voltage")
    source = wave((ip_half[0],(0,0),(0,0))*2,"from DC supply; signed energy return")
    q1 = wave(ip_half+zeros,"drain to source, signed reverse-channel conduction")
    q2 = wave(zeros+ip_half,"drain to source, signed reverse-channel conduction")
    q3 = wave(((0,0),)+negate(ip_half[1:])+(ip_half[0],(0,0),(0,0)),"drain to source, signed reverse-channel conduction")
    q4 = wave((ip_half[0],(0,0),(0,0),(0,0))+negate(ip_half[1:]),"drain to source, signed reverse-channel conduction")
    d1 = wave(il_half+zeros,"anode to cathode")
    d2 = wave(zeros+il_half,"anode to cathode")
    cap = il.affine(offset=-inputs.iout_a,reference="OUT to ISO (into Cout positive plate)")
    devices = {
        "L1":DeviceWaveforms("Output inductor",il,vl),
        "Lleak":DeviceWaveforms("Primary series/leakage inductance",ip,voltage((vin-vp_on,-vp_freewheel,0,-vin+vp_on,vp_freewheel,0),"bridge minus transformer voltage")),
        "Lm":DeviceWaveforms("Magnetizing state",im,vp),
        "primary":DeviceWaveforms("Transformer primary",replace(ip,reference="P to B (into primary dot)"),replace(vp,reference="P minus B")),
        "secondary1":DeviceWaveforms("Positive secondary half",replace(d1,reference="ISO to S1 (center tap to upper outer terminal)"),vp.affine(scale=1/n,reference="S1 minus ISO")),
        "secondary2":DeviceWaveforms("Negative secondary half",replace(d2,reference="ISO to S2 (center tap to lower outer terminal)"),vp.affine(scale=-1/n,reference="S2 minus ISO")),
        "Q1":DeviceWaveforms("Leg A high-side FET",q1,voltage((0,0,0,vin,vin,vin),"drain to source")),
        "Q2":DeviceWaveforms("Leg A low-side FET",q2,voltage((vin,vin,vin,0,0,0),"drain to source")),
        "Q3":DeviceWaveforms("Leg B high-side FET",q3,voltage((vin,0,0,0,vin,vin),"drain to source")),
        "Q4":DeviceWaveforms("Leg B low-side FET",q4,voltage((0,vin,vin,vin,0,0),"drain to source")),
        "D1":DeviceWaveforms("Positive rectifier",d1,voltage((-vf,-vf,vo,2*vp_on/n-vf,2*vp_freewheel/n-vf,vo),"cathode to anode")),
        "D2":DeviceWaveforms("Negative rectifier",d2,voltage((2*vp_on/n-vf,2*vp_freewheel/n-vf,vo,-vf,-vf,vo),"cathode to anode")),
        "Cout":DeviceWaveforms("Output capacitor",cap,capacitor_voltage(cap,inputs.capacitance_f,vo)),
    }
    circulating = wave(((0,0),)+ip_half[1:]+((0,0),)+negate(ip_half[1:]),"primary current during commanded zero states, including secondary-open idle")
    result = _finish(inputs,"T18",mode,active/half_period,devices,source,il,vf*inputs.iout_a,
                     assumptions=("Independent diode-secondary DCM/BCM extension with finite primary leakage, magnetizing and output inductances; balanced centered transformer flux.",
                                  "Each primary leg retains complementary 50% gate duty. Active transfer, single-diode decay and secondary-open idle are solved in each half-cycle.",
                                  "During secondary-open idle, both diodes block, output-inductor voltage is zero, and primary magnetizing current circulates through the commanded zero-state FET pair.",
                                  "Ideal reverse FET-channel conduction; no Coss, dead time, synchronous-rectifier timing or burst policy is modeled."),
                     warnings=("Independent ideal PSFB DCM/BCM extension; TI executable equivalence is UNKNOWN.",
                               "ZVS is UNKNOWN: switching capacitance, dead-time commutation and recovery losses are not verified."),
                     diagnostics={"inductor_mean_voltage_v":vl.statistics()["mean"],"transformer_mean_voltage_v":vp.statistics()["mean"],"leakage_mean_voltage_v":devices["Lleak"].voltage_v.statistics()["mean"]},
                     metrics={"phase_shift_deg":180*active/half_period,"per_switch_duty":.5,"commutation_time_s":0.0,
                              "effective_transfer_fraction":active/half_period,"circulating_current_rms_a":circulating.statistics()["rms"],
                              "rectified_ripple_frequency_hz":2*inputs.fsw_hz,"idle_fraction":idle/half_period,
                              "secondary_decay_time_s":freewheel,"bcm_output_current_a":boundary_load})
    return replace(result,provenance={**result.provenance,"physics_revision":"psfb-diode-dcm-1",
                                     "reference_url":"https://www.ti.com/lit/pdf/slup414",
                                     "supplement":"TI SLUP414 (April 2024), diode-only light-load policy; finite-inductance DCM equations independently derived, not PSD runtime evidence"})


def _quasi_resonant_flyback(inputs: DesignInputs, cancelled=None) -> StageResult:
    """Fixed-power variable-frequency valley switching with constant Coss.

    Includes turn-off capacitance charging before secondary conduction. Positive
    valleys permit skipping; zero-clamped operation uses only the first zero
    crossing with signed negative initial current and instantaneous gate-on.
    """
    from scipy.optimize import brentq

    from .waveforms import WaveformSegment
    if inputs.resonant_capacitance_f is None:
        raise ModelDomainError("QR Flyback requires resonant_capacitance_f (constant equivalent primary-node Coss)")
    if inputs.iout_a <= 0:
        raise ModelDomainError("QR Flyback requires a fixed positive output power")
    if inputs.mode.casefold() not in ("auto","dcm"):
        raise ModelDomainError("QR Flyback requires complete demagnetization; CCM/BCM forcing is unsupported")
    vin,vo,vf,n,lm,c = inputs.vin_v,inputs.vout_v,inputs.diode_drop_v,inputs.turns_ratio_np_ns,inputs.inductance_h,inputs.resonant_capacitance_f
    vr=n*(vo+vf)
    clamped = vr >= vin
    omega=1/math.sqrt(lm*c)
    impedance=math.sqrt(lm/c)
    power=(vo+vf)*inputs.iout_a

    def cycle(peak,valley):
        if cancelled is not None and cancelled():
            raise InterruptedError("QR Flyback calculation cancelled")
        amplitude=math.hypot(vin,peak*impedance)
        phase=math.atan2(vin,peak*impedance)
        charge_angle=phase+math.asin(vr/amplitude)
        charge_time=charge_angle/omega
        transfer_peak=peak*math.cos(charge_angle)+vin/impedance*math.sin(charge_angle)
        ton=lm*peak/vin
        td=lm*transfer_peak/vr
        wait=(2*valley-1)*math.pi/omega
        period=ton+charge_time+td+wait
        transferred=.5*lm*transfer_peak**2
        return transferred/period,(ton,charge_time,td,wait),transfer_peak,charge_angle

    chosen=None
    initial_current = 0.0
    if clamped:
        initial_current = -math.sqrt((vr-vin)*(vr+vin))/impedance
        clamp_angle = math.acos(-vin/vr)
        def clamped_cycle(transfer_peak):
            if cancelled is not None and cancelled():
                raise InterruptedError("QR Flyback calculation cancelled")
            peak = math.hypot(transfer_peak,initial_current)
            charge_angle = math.atan2(vin,peak*impedance)+math.atan2(vr,transfer_peak*impedance)
            times = (lm*(peak-initial_current)/vin,charge_angle/omega,lm*transfer_peak/vr,clamp_angle/omega)
            return .5*lm*transfer_peak**2/sum(times),peak,times,charge_angle
        upper = max(1.0,2*math.sqrt(power/(lm*inputs.fsw_hz)))
        while clamped_cycle(upper)[0] < power:
            upper *= 2
            if upper > 1e8:
                raise ModelDomainError("No bounded first-zero-clamp QR peak-current solution")
        transfer_peak = brentq(lambda ip:clamped_cycle(ip)[0]-power,0,upper,xtol=1e-13)
        _,peak,times,charge_angle = clamped_cycle(transfer_peak)
        frequency = 1/sum(times)
        if frequency > inputs.fsw_hz*(1+1e-12):
            raise ModelDomainError(f"QR first-zero-clamp frequency {frequency:.9g} Hz exceeds the specified ceiling {inputs.fsw_hz:.9g} Hz; later clamped valleys require a separately defined model")
        chosen = 1,peak,times,transfer_peak,charge_angle,frequency
    for valley in (() if clamped else range(1,101)):
        if cycle(0,valley)[0] >= power:
            continue
        upper=max(1.0,2*math.sqrt(power/(lm*inputs.fsw_hz)))
        while cycle(upper,valley)[0] < power:
            upper*=2
            if upper>1e8:
                raise ModelDomainError("No bounded QR peak-current solution")
        peak=brentq(lambda ip,valley=valley:cycle(ip,valley)[0]-power,0,upper,xtol=1e-13)
        _,times,transfer_peak,charge_angle=cycle(peak,valley)
        frequency=1/sum(times)
        if frequency <= inputs.fsw_hz*(1+1e-12):
            chosen=valley,peak,times,transfer_peak,charge_angle,frequency
            break
    if chosen is None:
        raise ModelDomainError("QR solution would require more than 100 valley skips; use a burst-mode model or revise the frequency ceiling")
    valley,peak,times,transfer_peak,charge_angle,frequency=chosen
    period=sum(times)
    durations=tuple(t/period for t in times)
    boundaries=[0]
    for duration in durations:
        boundaries.append(boundaries[-1]+duration)
    boundaries[-1]=1.0
    phi=clamp_angle if clamped else (2*valley-1)*math.pi
    zero=(0.0,0.0,0.0)
    def wave(polynomials,harmonics,unit,ref):
        segments=tuple(WaveformSegment(boundaries[k],boundaries[k+1],tuple(polynomials[k]),tuple(harmonics[k])) for k in range(4) if boundaries[k+1]>boundaries[k])
        return Waveform(period,unit,ref,segments)
    im=wave(((initial_current,peak-initial_current),(0,),(transfer_peak,-transfer_peak),(0,)),(zero,(peak,vin/impedance,charge_angle),zero,(0,-vr/impedance,phi)),"A","primary-referred magnetizing state")
    primary=wave(((initial_current,peak-initial_current),(0,),(0,),(0,)),(zero,(peak,vin/impedance,charge_angle),zero,(0,-vr/impedance,phi)),"A","actual primary winding current into dot")
    switch=wave(((initial_current,peak-initial_current),(0,),(0,),(0,)),(zero,zero,zero,zero),"A","MOSFET channel conduction current, including signed reverse conduction; instantaneous Coss discharge excluded")
    diode=wave(((0,),(0,),(n*transfer_peak,-n*transfer_peak),(0,)),(zero,zero,zero,zero),"A","secondary rectifier anode to cathode")
    vq=wave(((0,),(vin,),(vin+vr,),(vin,)),(zero,(-vin,peak*impedance,charge_angle),zero,(vr,0,phi)),"V","drain to source")
    vp=vq.affine(scale=-1,offset=vin,reference="primary dot minus undotted terminal")
    vd=vp.affine(scale=1/n,offset=vo,reference="diode cathode to anode")
    cap=diode.affine(offset=-inputs.iout_a,reference="OUT to ISO (into Cout positive plate)")
    devices={
        "Q1":DeviceWaveforms("QR main switch",switch,vq),
        "D1":DeviceWaveforms("Secondary rectifier",diode,vd),
        "Lm":DeviceWaveforms("Primary-referred magnetizing state",im,vp),
        "primary":DeviceWaveforms("Actual primary winding",replace(primary,reference="VIN to N (into primary dot; signed)"),replace(vp,reference="VIN minus N")),
        "secondary":DeviceWaveforms("Actual secondary winding",replace(diode,reference="ISO to SEC (return to rectifier terminal)"),vp.affine(scale=-1/n,reference="SEC minus ISO")),
        "Cout":DeviceWaveforms("Output capacitor",cap,capacitor_voltage(cap,inputs.capacitance_f,vo)),
    }
    if clamped:
        displacement = wave(((0,), (0,), (0,), (0,)),
                            (zero, (peak,vin/impedance,charge_angle), zero,
                             (0,-vr/impedance,phi)), "A", "into Cnode drain-side positive plate")
        devices["Cnode"] = DeviceWaveforms("Drain-node resonant capacitance", displacement,
                                           replace(vq, reference="Cnode drain-side plate minus source"))
    valley_voltage = 0.0 if clamped else vin-vr
    turn_on_energy=.5*c*valley_voltage**2
    result=_finish(inputs,"T11","QR-DCM",durations[0],devices,primary,im,vf*inputs.iout_a,
                   modeled_other_loss_w=turn_on_energy*frequency,
                   assumptions=("Fixed output power; fsw_hz is the maximum allowed frequency, not the solved switching frequency.",
                                "Exact sinusoidal Lm–constant-Coss charging and valley intervals, linear on/demagnetization intervals; first feasible valley is selected.",
                                "Positive drain valley only (Vin > reflected output). Nonlinear Coss, leakage, body-diode clamp and burst mode are excluded.",
                                "The ideal turn-on Coss discharge is an impulse: its finite energy is reported separately; no finite switching-current RMS is invented."),
                   warnings=("MOSFET RMS includes conduction current only; turn-on discharge peak/RMS require finite transition/device data.",),
                   diagnostics={"inductor_mean_voltage_v":vp.statistics()["mean"]},
                   metrics={"valley_index":float(valley),"drain_valley_voltage_v":valley_voltage,
                            "turn_on_capacitive_energy_j":turn_on_energy,"turn_on_capacitive_loss_w":turn_on_energy*frequency,
                            "demagnetization_time_s":times[2],"resonant_wait_time_s":times[3],"turn_off_charge_time_s":times[1]})
    if clamped:
        result = replace(result,
                         assumptions=result.assumptions[:4]+(
                             "Fixed output power; fsw_hz is a maximum frequency. Only the first zero-voltage crossing is used; later clamped valleys and burst are excluded.",
                             "Constant Cnode, zero leakage and ideal zero-drop primary body clamp. Gate-on occurs instantaneously when Vds first reaches zero, retaining negative initial magnetizing/channel current.",
                             "Turn-off LC charging, secondary demagnetization and the truncated LC ring are solved. There is no finite off-gate body-diode interval in this declared limiting policy.",
                             "No capacitor-voltage jump occurs at this zero-voltage turn-on, so ideal turn-on capacitive energy is zero; this does not establish real switching or reverse-recovery loss."),
                         warnings=tuple(w for w in result.warnings if not w.startswith("MOSFET RMS"))+(
                             "Independent ideal first-zero-clamp QR extension; TI executable equivalence is UNKNOWN.",
                             "Nonlinear Coss, detector/gate delay, leakage, body-diode recovery and hardware ZVS losses are UNKNOWN."),
                         metrics={**result.metrics,"turn_on_magnetizing_current_a":initial_current,
                                  "negative_channel_time_s":-lm*initial_current/vin},
                         provenance={**result.provenance,"physics_revision":"qr-first-zero-clamp-1",
                                     "reference_url":"https://www.nxp.com/docs/en/application-note/AN00047.pdf",
                                     "supplement":"Philips/NXP AN00047 Appendix 2 pp40–41, first-zero-crossing policy; energy-consistent constant-Cnode equations independently derived"})
    return replace(result,provenance={**result.provenance,"input_frequency_role":"maximum switching-frequency ceiling; solved frequency is in actual_switching_frequency_hz",
                                     **({} if clamped else {"supplement":"Four-interval constant-Coss analytical model; QR behavior cross-checked against TI UCC28600 section 7.4.1, not TI PSD executable"})})


def _fly_buck(inputs: DesignInputs, cancelled=None) -> StageResult:
    """Periodic synchronous Fly-Buck with finite secondary leakage and two C's.

    Primary rail voltage is solved to deliver the requested isolated output,
    not guessed as n*Vout. The secondary diode can keep conducting into the
    next on-state while leakage current commutates. Constant-current loads.
    """
    import numpy as np
    from scipy.integrate import solve_ivp
    from scipy.optimize import least_squares

    from .waveforms import WaveformSegment
    if inputs.leakage_inductance_h is None or inputs.primary_capacitance_f is None or inputs.primary_output_current_a is None:
        raise ModelDomainError("Fly-Buck requires secondary-referred leakage_inductance_h, primary_capacitance_f and explicit primary_output_current_a (0 is allowed)")
    if inputs.iout_a <= 0:
        raise ModelDomainError("Fly-Buck periodic solver requires a positive isolated load")
    if inputs.mode.casefold() != "auto":
        raise ModelDomainError("Fly-Buck uses synchronous signed primary current and diode-event states; select auto mode")
    vin,vo,io,n,lm,lk,cp,co,vf,ipload=inputs.vin_v,inputs.vout_v,inputs.iout_a,inputs.turns_ratio_np_ns,inputs.inductance_h,inputs.leakage_inductance_h,inputs.primary_capacitance_f,inputs.capacitance_f,inputs.diode_drop_v,inputs.primary_output_current_a
    period=1/inputs.fsw_hz
    nominal_primary=n*(vo+vf)
    if nominal_primary >= .95*vin:
        raise ModelDomainError("Fly-Buck isolated target leaves insufficient primary buck and commutation headroom")
    scale=np.array([max(.1,ipload+io/n),max(1,nominal_primary),max(.1,io),max(1,vo)])
    cycle_evaluations=0

    def check_cancel():
        if cancelled is not None and cancelled():
            raise InterruptedError("Fly-Buck calculation cancelled")

    def rhs(gate,conducting):
        switch_voltage=vin if gate else 0.0
        def derivative(t,state):
            check_cancel()
            im,vp,secondary,vout=state[:4]
            dis=((vp-switch_voltage)/n-vout-vf)/lk if conducting else 0.0
            return np.array([(switch_voltage-vp)/lm,(im-secondary/n-ipload)/cp,dis,(secondary-io)/co,vout/period,vp/period])
        return derivative

    def cycle(unknown,retain=False):
        nonlocal cycle_evaluations
        check_cancel()
        cycle_evaluations+=1
        state=np.r_[unknown[:4],0.0,0.0]
        duty=unknown[4]
        pieces=[]
        for start,stop,gate in ((0,duty*period,True),(duty*period,period,False)):
            cursor=start
            switches=0
            forced_conducting=None
            while cursor < stop-period*1e-12:
                check_cancel()
                switch_voltage=vin if gate else 0.0
                drive=(state[1]-switch_voltage)/n-state[3]-vf
                conducting=(state[2]>1e-10 or drive>0) if forced_conducting is None else forced_conducting
                forced_conducting=None
                if not conducting:
                    state[2]=0.0
                derivative=rhs(gate,conducting)
                if conducting:
                    def event(t,y): return y[2]
                    event.direction=-1
                else:
                    def event(t,y,switch_voltage=switch_voltage): return (y[1]-switch_voltage)/n-y[3]-vf
                    event.direction=1
                event.terminal=True
                sol=solve_ivp(derivative,(cursor,stop),state,method="DOP853",rtol=2e-10,
                              atol=np.array([1e-11,1e-10,1e-11,1e-10,1e-10,1e-10]),
                              dense_output=retain,events=event,max_step=period/24)
                if not sol.success:
                    raise ModelDomainError(f"Fly-Buck integration failed: {sol.message}")
                if retain:
                    pieces.append((sol,gate,conducting,derivative))
                state=sol.y[:,-1]
                end=float(sol.t[-1])
                if sol.t_events[0].size:
                    switches+=1
                    if switches>25 or end-cursor < period*1e-14:
                        raise ModelDomainError("Fly-Buck has unresolved repeated diode grazing events")
                    if conducting:
                        state[2]=0.0
                    forced_conducting=not conducting
                cursor=end
        return state,pieces

    duty0=min(.85,nominal_primary/vin*1.02)
    ripple=(vin-nominal_primary)*duty0/(lm*inputs.fsw_hz)
    guess=np.array([ipload+io/n-ripple/2,nominal_primary,2*io/(1-duty0),vo,duty0])
    def residual(unknown):
        final,_=cycle(unknown)
        return np.r_[(final[:4]-unknown[:4])/scale,(final[4]-vo)/max(1,vo)]
    lower=np.array([-100*scale[0],.001*vin,0,.05*vo,.001])
    upper=np.array([100*scale[0],.999*vin,100*scale[2],5*vo,.95])
    solved=least_squares(residual,guess,bounds=(lower,upper),x_scale=np.r_[scale,1],
                         ftol=2e-11,xtol=2e-11,gtol=2e-11,max_nfev=120,diff_step=1e-5)
    final,pieces=cycle(solved.x,retain=True)
    error=float(np.max(np.abs(np.r_[(final[:4]-solved.x[:4])/scale,(final[4]-vo)/max(1,vo)])))
    if not solved.success or error>1e-7:
        raise ModelDomainError(f"Fly-Buck periodic solution did not converge (normalized closure {error:.3g}); revise leakage, capacitance or operating point")
    duty=float(solved.x[4])
    # Construct cubic Hermite pieces from the actual adaptive integration,
    # retaining diode and gate events exactly. Display and statistics use the
    # same coefficients; no generated 'triangle' substitutes for these states.
    signals={key:[] for key in ("im","vp","is","vo","ip","vlm","vlk","iq1","iq2","vq1","vq2","id","vd","icp","ico","vs")}
    def quantities(t,state,derivative,gate,conducting):
        im,vp,secondary,vout=state[:4]
        dim,dvp,dis,dvo=derivative(t,state)[:4]
        switch_voltage=vin if gate else 0.0
        ip=im-secondary/n
        dip=dim-dis/n
        vsec=(vp-switch_voltage)/n
        return {
            "im":(im,dim),"vp":(vp,dvp),"is":(secondary,dis),"vo":(vout,dvo),
            "ip":(ip,dip),"vlm":(switch_voltage-vp,-dvp),
            "vlk":(vsec-vout-vf,dvp/n-dvo) if conducting else (0,0),
            "iq1":(ip,dip) if gate else (0,0),"iq2":(0,0) if gate else (-ip,-dip),
            "vq1":(0,0) if gate else (vin,0),"vq2":(vin,0) if gate else (0,0),
            "id":(secondary,dis),"vd":(-vf,0) if conducting else (vout-vsec,dvo-dvp/n),
            "icp":(ip-ipload,dip),"ico":(secondary-io,dis),"vs":(vsec,dvp/n),
        }
    for sol,gate,conducting,derivative in pieces:
        start,stop=float(sol.t[0]),float(sol.t[-1])
        grid=np.unique(np.r_[sol.t,np.linspace(start,stop,max(3,math.ceil((stop-start)/period*256)+1))])
        for a,b in itertools.pairwise(grid):
            qa=quantities(a,sol.sol(a),derivative,gate,conducting)
            qb=quantities(b,sol.sol(b),derivative,gate,conducting)
            dt=b-a
            for key,segments in signals.items():
                va,da=qa[key]
                vb,db=qb[key]
                coefficients=(float(va),float(dt*da),float(3*(vb-va)-dt*(2*da+db)),float(2*(va-vb)+dt*(da+db)))
                segments.append(WaveformSegment(float(a/period),min(1,float(b/period)),coefficients))
    def wave(key,unit,reference):
        return Waveform(period,unit,reference,tuple(signals[key]))
    im=wave("im","A","primary-referred magnetizing current")
    vp=wave("vp","V","primary rail positive to return")
    secondary=wave("is","A","rectifier anode to cathode")
    output=wave("vo","V","isolated output positive to return")
    primary=wave("ip","A","primary winding switch node to primary rail; signed")
    vlm=wave("vlm","V","switch node minus primary rail")
    devices={
        "Lm":DeviceWaveforms("Primary-referred magnetizing state",im,vlm),
        "primary":DeviceWaveforms("Actual coupled primary winding",replace(primary,reference="SW to PRIMARY_OUT (into primary dot; signed)"),replace(vlm,reference="SW minus PRIMARY_OUT")),
        "secondary":DeviceWaveforms("Actual coupled secondary winding",replace(secondary,reference="ISO to SEC (return to rectifier terminal)"),wave("vs","V","SEC minus ISO")),
        "Lleak":DeviceWaveforms("Secondary-referred leakage inductance",replace(secondary,reference="SEC to SEC_L"),wave("vlk","V","SEC minus SEC_L")),
        "Q1":DeviceWaveforms("Synchronous buck high-side switch",wave("iq1","A","drain to source; signed"),wave("vq1","V","drain to source")),
        "Q2":DeviceWaveforms("Synchronous buck low-side switch",wave("iq2","A","drain to source; signed"),wave("vq2","V","drain to source")),
        "D1":DeviceWaveforms("Isolated output diode",wave("id","A","anode to cathode"),wave("vd","V","cathode to anode")),
        "Cprimary":DeviceWaveforms("Primary output capacitor",wave("icp","A","into primary-rail capacitor"),vp),
        "Cout":DeviceWaveforms("Isolated output capacitor",wave("ico","A","into isolated output capacitor"),output),
    }
    if min(vp.statistics()["min"],output.statistics()["min"])<=0 or vp.statistics()["max"]>=vin:
        raise ModelDomainError("Fly-Buck solved capacitor voltage leaves positive buck-rail domain")
    primary_power=vp.statistics()["mean"]*ipload
    result=_finish(inputs,"T04","SYNCHRONOUS-PERIODIC",duty,devices,wave("iq1","A","from input supply"),im,
                   vf*secondary.statistics()["mean"],
                   assumptions=("Finite secondary leakage plus dynamic primary and isolated output capacitors; constant-current loads on both outputs.",
                                "Duty and required mean primary rail voltage solve periodic state closure plus the requested mean isolated voltage.",
                                "Synchronous primary current may reverse; diode current commutates through leakage after high-side turn-on.",
                                "Adaptive DOP853 shooting solution; cubic Hermite waveform interpolation shares the displayed/statistical representation. No hardware stability or parasitic loss claim."),
                   diagnostics={"periodic_normalized_residual":error,"primary_capacitor_mean_current_a":devices["Cprimary"].current_a.statistics()["mean"],
                                "inductor_mean_voltage_v":vlm.statistics()["mean"],"leakage_mean_voltage_v":devices["Lleak"].voltage_v.statistics()["mean"],
                                "isolated_output_target_error_v":output.statistics()["mean"]-vo},
                   metrics={"primary_output_voltage_v":vp.statistics()["mean"],"primary_output_power_w":primary_power,
                            "isolated_output_power_w":output.statistics()["mean"]*io,"periodic_solver_evaluations":float(cycle_evaluations)},
                   extra_output_power_w=primary_power)
    return replace(result,provenance={**result.provenance,"reference_url":"https://www.ti.com/lit/an/snva790a/snva790a.pdf",
                                     "supplement":"Independent four-state switched model; physical branch orientation checked against TI SNVA790A figures 2–3"})


def _bridge_demagnetizing(inputs: DesignInputs, topology_id: str) -> StageResult:
    """Light-load bridge solution with real secondary magnetizing demagnetization.

    Once one freewheel diode reaches zero, the opposite half-winding supplies
    output current from Lm. Its primary-referred series output inductance is
    retained; a subsequent idle interval appears only after both states vanish.
    """
    from scipy.optimize import brentq
    vin,vo,vf,n,lo,lm=inputs.vin_v,inputs.vout_v,inputs.diode_drop_v,inputs.turns_ratio_np_ns,inputs.inductance_h,inputs.magnetizing_inductance_h
    half=topology_id=="T15"
    va=vin/2 if half else vin
    vs=va/n
    hp=.5/inputs.fsw_hz
    alpha=(vs-vo-vf)/lo
    beta=(vo+vf)/lo
    gamma=(vo+vf)/(lo+lm/n**2)
    k=n*va/lm
    nominal_duty=(vo+vf)/vs
    coefficient=k+gamma+gamma*(alpha-k)/beta
    boundary_duty=gamma/coefficient
    # alpha == k is the physical zero-two-diode-freewheel endpoint. Each
    # slope contains several floating operations; a handful of ulps must not
    # turn that endpoint into a different commutation-capacitance sub-mode.
    requires_commutation = k-alpha > 8*max(math.ulp(alpha),math.ulp(k))

    def partial(duty):
        ton=duty*hp
        low=(coefficient*ton-gamma*hp)/(2*(1-gamma/beta))
        high=low+alpha*ton
        demag_start=k*ton-low
        tb=(high-demag_start)/beta
        ts=hp-ton-tb
        mean=((low+high)*ton+(high+demag_start)*tb+(demag_start+low)*ts)/(2*hp)
        return mean,low,high,demag_start,(ton,tb,ts,0.0)

    lower=boundary_duty
    if requires_commutation:
        lower=brentq(lambda d:partial(d)[4][1],boundary_duty,nominal_duty,xtol=1e-14)
    minimum_partial=partial(lower)[0]
    if inputs.iout_a >= minimum_partial:
        duty=brentq(lambda d:partial(d)[0]-inputs.iout_a,lower,nominal_duty,xtol=1e-14)
        _,low,high,demag_start,times=partial(duty)
        mode="BCM" if low<1e-10 else "CCM"
    else:
        if requires_commutation:
            raise ModelDomainError("Bridge magnetizing inductance is too small for this light-load open-primary sub-mode; commutation capacitances are required")
        bt=(alpha-k)/beta
        st=k/gamma
        charge_coefficient=alpha+(alpha+k)*bt+k*st
        ton=math.sqrt(2*inputs.iout_a*hp/charge_coefficient)
        tb,ts=bt*ton,st*ton
        idle=hp-ton-tb-ts
        if idle < -1e-12:
            raise ModelDomainError("Bridge demagnetization does not close before the next pulse")
        low,high,demag_start=0.0,alpha*ton,k*ton
        times=(ton,tb,ts,max(0.0,idle))
        duty=ton/hp
        mode="DCM" if idle>hp*1e-10 else "BCM"
    requested=inputs.mode.upper()
    if requested not in ("AUTO",mode) and not (mode=="BCM" and requested in ("CCM","DCM")):
        raise ModelDomainError(f"Requested {requested}, but bridge operating point requires {mode}")
    ton,tb,ts,idle=times
    if min(times)<-1e-13:
        raise ModelDomainError("Negative bridge state duration")
    duration_half=tuple(max(0,t)*inputs.fsw_hz for t in times)
    durations=duration_half+duration_half
    im_start=-low/n
    im_peak=demag_start/n
    im_pairs=((im_start,im_peak),(im_peak,im_peak),(im_peak,low/n),(0,0))
    il_pairs=((low,high),(high,demag_start),(demag_start,low),(0,0))
    ip_end=high/n+im_peak
    positive_primary=((0,ip_end),(0,0),(0,0),(0,0))
    zeros=((0,0),)*4
    negative=lambda v:tuple((-a,-b) for a,b in v)
    il=_wave(inputs,durations,il_pairs+il_pairs,"A","output inductor toward output")
    im=_wave(inputs,durations,im_pairs+negative(im_pairs),"A","primary-referred magnetizing state")
    ip=_wave(inputs,durations,positive_primary+negative(positive_primary),"A","primary into dotted terminal")
    vm=lm*gamma/n
    if vm>va*(1+1e-12):
        raise ModelDomainError("Bridge reset would forward-bias a primary body diode; clamp sub-mode is not modeled")
    vp_levels=(va,0,-vm,0,-va,0,vm,0)
    vp=_voltage(inputs,durations,vp_levels,"primary dotted minus undotted terminal")
    q1=_wave(inputs,durations,positive_primary+zeros,"A","drain to source")
    q2=_wave(inputs,durations,zeros+positive_primary,"A","drain to source")
    dpos=((low,high),((high-demag_start)/2,0),(0,0),(0,0))
    dneg=((0,0),((high+demag_start)/2,demag_start),(demag_start,low),(0,0))
    d1=_wave(inputs,durations,dpos+dneg,"A","anode to cathode")
    d2=_wave(inputs,durations,dneg+dpos,"A","anode to cathode")
    v1=(-vf,-vf,2*vm/n-vf,vo,2*vs-vf,-vf,-vf,vo)
    v2=(2*vs-vf,-vf,-vf,vo,-vf,-vf,2*vm/n-vf,vo)
    q_bias=vin if topology_id=="T16" else vin/2
    q_scale=1 if topology_id in ("T15","T16") else .5
    q1v=vp.affine(scale=-q_scale,offset=q_bias,reference="drain to source")
    q2v=vp.affine(scale=q_scale,offset=q_bias,reference="drain to source")
    source=_wave(inputs,durations,positive_primary+positive_primary,"A","equivalent DC source current").affine(scale=.5 if half else 1)
    capacitor=il.affine(offset=-inputs.iout_a,reference="OUT to ISO (into Cout positive plate)")
    vl=_voltage(inputs,durations,(va/n-vo-vf,-vo-vf,vm/n-vo-vf,0)*2,"passive output-inductor voltage")
    devices={
        "L1":DeviceWaveforms("Output inductor",il,vl),
        "Lm":DeviceWaveforms("Primary-referred magnetizing state",im,vp),
        "primary":DeviceWaveforms("Actual primary winding",replace(ip,reference="A to MID" if half else "A to B"),replace(vp,reference="A minus MID" if half else "A minus B")),
        "secondary1":DeviceWaveforms("Positive secondary half",replace(d1,reference="ISO to S1 (center tap to upper outer terminal)"),vp.affine(scale=1/n,reference="S1 minus ISO")),
        "secondary2":DeviceWaveforms("Negative secondary half",replace(d2,reference="ISO to S2 (center tap to lower outer terminal)"),vp.affine(scale=-1/n,reference="S2 minus ISO")),
        "Q1":DeviceWaveforms("First primary switch",q1,q1v),
        "Q2":DeviceWaveforms("Second primary switch",q2,q2v),
        "D1":DeviceWaveforms("First secondary rectifier",d1,_voltage(inputs,durations,v1,"cathode to anode")),
        "D2":DeviceWaveforms("Second secondary rectifier",d2,_voltage(inputs,durations,v2,"cathode to anode")),
        "Cout":DeviceWaveforms("Output capacitor",capacitor,capacitor_voltage(capacitor,inputs.capacitance_f,vo)),
    }
    if topology_id=="T17":
        devices["Q3"]=DeviceWaveforms("First diagonal paired switch",q1,q1v)
        devices["Q4"]=DeviceWaveforms("Second diagonal paired switch",q2,q2v)
    if topology_id=="T16":
        devices["primary1"]=DeviceWaveforms("First primary half",replace(q1,reference="VIN to P1 (center tap to upper outer terminal)"),replace(vp,reference="VIN minus P1 (dotted center tap minus upper outer terminal)"))
        devices["primary2"]=DeviceWaveforms("Second primary half",replace(q2,reference="VIN to P2 (center tap to lower outer terminal)"),vp.affine(scale=-1,reference="VIN minus P2 (center tap minus dotted lower outer terminal)"))
        del devices["primary"]
    if half:
        devices.update(_bridge_split_capacitors(inputs, ip))
    return _finish(inputs,topology_id,mode,duty,devices,source,il,vf*inputs.iout_a,
                   assumptions=("Full bipolar bridge period is 1/fsw; output ripple repeats at 2*fsw. Center-tapped secondary n uses one secondary half; Push-pull Np is one primary half.",
                                "Each half-cycle explicitly resolves on, two-diode freewheel, single-diode Lm/output-L demagnetization and any zero-current idle interval.",
                                "During single-diode demagnetization the effective secondary inductance is Lout+Lm/n². Magnetizing and output currents are not independently clipped.",
                                "Primary leakage, nonlinear capacitances, switching transitions and body-diode clamp states are excluded; symmetric blocking-voltage sharing is assumed.")
                               + ((_HALF_BRIDGE_SPLIT_ASSUMPTION,) if half else ()),
                   diagnostics={"inductor_mean_voltage_v":vl.statistics()["mean"],"transformer_mean_voltage_v":vp.statistics()["mean"]},
                   metrics={"per_switch_duty":duty/2,"rectified_ripple_frequency_hz":2*inputs.fsw_hz,
                            "secondary_demagnetization_fraction":2*ts*inputs.fsw_hz,"idle_fraction":2*idle*inputs.fsw_hz})

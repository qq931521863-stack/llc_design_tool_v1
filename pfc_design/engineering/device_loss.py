"""TTPL semiconductor comparison on the engineering power-stage line cycle.

This is a screening model, not a semiconductor sign-off model. It integrates
a sizing half-cycle or an explicitly selected converged closed-loop full cycle
with CCM triangular-ripple reconstruction, and keeps the existing
PFC database's single-point Eon/Eoff/Coss assumptions explicit.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field, replace
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .capacitor_ripple import PFCCapacitorRipple
    from .pfc_v3 import PFCLineCycleResult
    from .switched_cycle import PFCSwitchedCycleResult

import numpy as np

from pfc_design.core.spec import MosfetSpec

from .device_library import PFCDeviceDatabase
from .ttpl_design import TTPLDesignResult, TTPLLineTrace


@dataclass(frozen=True)
class TTPLPhysicalDeviceLoss:
    """Full-line-period loss of one fixed upper/lower switch, not a PWM role."""

    rds_hot_ohm: float
    conduction_w: float
    switching_w: float
    deadtime_reverse_w: float
    coss_w: float
    gate_drive_w: float
    total_w: float
    current_ok: bool
    diode_conduction_w: float = 0.0


@dataclass(frozen=True)
class TTPLHFDeviceLoss:
    device: MosfetSpec
    rds_hot_ohm: float
    active_conduction_w: float
    active_switching_w: float
    sr_conduction_w: float
    sr_switching_w: float
    deadtime_reverse_w: float
    coss_w: float
    gate_drive_w: float
    total_w: float
    voltage_ok: bool
    current_ok: bool
    switching_model: str
    upper: TTPLPhysicalDeviceLoss | None = None
    lower: TTPLPhysicalDeviceLoss | None = None
    diode_conduction_w: float = 0.0


@dataclass(frozen=True)
class TTPLSlowDeviceLoss:
    device: MosfetSpec
    rds_hot_ohm: float
    conduction_w: float
    gate_drive_w: float
    total_w: float
    voltage_ok: bool
    current_ok: bool
    upper: TTPLPhysicalDeviceLoss | None = None
    lower: TTPLPhysicalDeviceLoss | None = None
    diode_conduction_w: float = 0.0
    switching_w: float = 0.0
    coss_w: float = 0.0


@dataclass(frozen=True)
class TTPLDeviceComparison:
    design: TTPLDesignResult
    workpoint: str
    vin_rms_v: float
    junction_temperature_c: float
    deadtime_s: float
    reverse_drop_v: float
    voltage_derating: float
    hf_devices: tuple[TTPLHFDeviceLoss, ...]
    slow_devices: tuple[TTPLSlowDeviceLoss, ...]
    waveform_source: str = "ideal_sizing_half_cycle"
    capacitor_current_rms_a: float | None = None
    capacitor_ripple: PFCCapacitorRipple | None = None
    power_factor: float | None = None
    thd_percent: float | None = None
    line_cycle: PFCLineCycleResult | None = field(default=None, repr=False, compare=False)
    switched_cycle: PFCSwitchedCycleResult | None = field(default=None, repr=False, compare=False)

    def as_dict(self) -> dict:
        """Export the exact comparison, including the source operating point."""
        if self.switched_cycle is not None:
            self.switched_cycle.require_accepted()
        return {
            "workpoint": self.workpoint,
            "vin_rms_v": self.vin_rms_v,
            "waveform_source": self.waveform_source,
            "junction_temperature_c": self.junction_temperature_c,
            "deadtime_s": self.deadtime_s,
            "reverse_drop_v": self.reverse_drop_v,
            "voltage_derating": self.voltage_derating,
            "capacitor_current_rms_a": self.capacitor_current_rms_a,
            "capacitor_current_rms_scope": ("signed switching-source capacitor current including PWM" if self.switched_cycle is not None else "accepted averaged current; see capacitor_ripple for PWM moments"),
            "capacitor_ripple": None if self.capacitor_ripple is None else self.capacitor_ripple.as_dict(),
            "power_factor": self.power_factor,
            "thd_percent": self.thd_percent,
            "thd_harmonic_orders": list(range(2,26)) if self.switched_cycle is not None else None,
            "loss_total_scope": "MODELED_KNOWN_TERMS_ONLY" if self.switched_cycle is not None else "averaged/sizing screening",
            "reverse_recovery": {"status":"UNMODELED_NOT_A_HARDWARE_ZERO", "explicit_loss_w":None,
                "note":"Memoryless generic diode has no stored charge. Selected Eon/Eoff may embed unseparated recovery; no extra Qrr term is asserted."} if self.switched_cycle is not None else None,
            "source_config": (asdict(self.switched_cycle.config) if self.switched_cycle is not None else None if self.line_cycle is None else asdict(self.line_cycle.config)),
            "source_line_cycle": None if self.line_cycle is None else self.line_cycle.as_dict(),
            "source_switched_cycle": None if self.switched_cycle is None else self.switched_cycle.as_dict(include_waveforms=True),
            "physical_switch_allocation": ("Direct signed measured channel/diode branch sources at all four fixed positions; metadata target polarity only labels HF active/SR channel roles." if self.switched_cycle is not None else
                "Vac >= 0: HF lower active, HF upper SR, LF lower on; roles reverse at negative Vac. "
                "Legacy sizing half-cycle is mirrored. Active Eon/Eoff and SR Eoff follow roles; "
                "both CCM deadtimes conduct in SR; Coss/gate are per physical switch."
            ),
            "hf_devices": [asdict(item) for item in self.hf_devices],
            "slow_devices": [asdict(item) for item in self.slow_devices],
            "limitations": (["Signed switching circuit is generic Ron/Roff and memoryless diode model, not vendor dynamics.", "Selected Rds(T), edge Eon/Eoff or tr/tf overlay the frozen circuit. Explicit rising Coss excluded when Eon supplied; overlap of Qrr/diode recovery unknown.", "Gate-drive dissipation is counted in this modeled/known-term subtotal; physical thermal network is lumped. Explicit Qrr is unmodeled, not a claimed zero hardware loss."] if self.switched_cycle is not None else [
                "Semiconductor screening only; magnetic/capacitor ESR losses excluded",
                "Averaged capacitor RMS is retained separately; capacitor_ripple reports available PWM moments and unresolved coverage",
                "Datasheet Eon/Eoff and explicit Coss may overlap; no hardware sign-off",
                "CCM triangular ripple is reconstructed from the averaged trajectory; switching-resolved zero crossing is not verified",
            ]),
        }


def _line_trace(result: TTPLDesignResult, vin_rms_v: float) -> TTPLLineTrace:
    spec = result.spec
    angle = np.linspace(0.0, math.pi, spec.trace_points)
    s = np.sin(angle)
    vin = math.sqrt(2.0) * vin_rms_v * s
    duty_raw = 1.0 - vin / spec.bus_voltage_v
    duty = np.clip(duty_raw, spec.duty_min, spec.duty_max)
    i_rms = spec.output_power_w / (spec.efficiency * vin_rms_v)
    i_peak = math.sqrt(2.0) * i_rms
    i_avg = i_peak * s
    ripple = vin * np.maximum(1.0 - vin / spec.bus_voltage_v, 0.0) / (
        max(result.required_inductance_h, 1e-15) * spec.switching_frequency_hz
    )
    i_pk = i_avg + 0.5 * ripple
    i_valley = np.maximum(i_avg - 0.5 * ripple, 0.0)
    return TTPLLineTrace(
        angle_deg=np.degrees(angle),
        vin_abs_v=vin,
        duty=duty,
        average_inductor_current_a=i_avg,
        ripple_pp_a=ripple,
        inductor_peak_a=i_pk,
        inductor_valley_a=i_valley,
        on_time_s=duty / spec.switching_frequency_hz,
    )


def _closed_loop_trace(line: PFCLineCycleResult) -> TTPLLineTrace:
    from .pfc_v3 import ConvergenceStatus

    if line.convergence.status != ConvergenceStatus.LINE_CYCLE_CONVERGED:
        raise ValueError("device loss requires a converged closed-loop line cycle; increase AC settling cycles and rerun Control analysis before selecting closed-loop losses")
    if line.config is None:
        raise ValueError("closed-loop line-cycle operating-point identity is missing")
    if not math.isfinite(line.fsw_hz) or not math.isclose(
            line.fsw_hz, line.config.power_stage.switching_frequency_hz, rel_tol=1e-12):
        raise ValueError("line-cycle switching frequency does not match its source configuration")
    time = np.asarray(line.time_s, dtype=float)
    if time.ndim != 1 or len(time) < 4 or not np.all(np.isfinite(time)):
        raise ValueError("invalid closed-loop line-cycle time samples")
    steps = np.diff(time)
    if np.any(steps <= 0) or not np.allclose(steps, steps[0], rtol=1e-6, atol=1e-15):
        raise ValueError("closed-loop loss requires uniform full-cycle samples")
    period = 1.0 / line.config.power_stage.line_frequency_hz
    if abs(len(time) * steps[0] - period) > 0.51 * steps[0]:
        raise ValueError("closed-loop loss requires one complete line period")
    for values in (line.vac_v, line.vac_abs_v, line.duty, line.il_avg_a, line.il_ripple_pp_a,
                   line.il_peak_a, line.il_valley_a, line.vbus_v, line.iac_a,
                   line.capacitor_current_a):
        if np.shape(values) != time.shape or not np.all(np.isfinite(values)):
            raise ValueError("invalid closed-loop loss signal samples")
    if np.any((line.duty < 0) | (line.duty > 1)) or np.any(line.vbus_v <= 0):
        raise ValueError("closed-loop duty or bus voltage is outside loss-model domain")
    return TTPLLineTrace(
        angle_deg=line.theta_deg, vin_abs_v=line.vac_abs_v, duty=line.duty,
        average_inductor_current_a=line.il_avg_a, ripple_pp_a=line.il_ripple_pp_a,
        inductor_peak_a=line.il_peak_a, inductor_valley_a=line.il_valley_a,
        on_time_s=line.duty / line.fsw_hz,
    )


def _design_for_line_cycle(design: TTPLDesignResult, line: PFCLineCycleResult) -> TTPLDesignResult:
    """The accepted closed-loop result owns the electrical operating point."""
    _closed_loop_trace(line)
    stage = line.config.power_stage
    return replace(design, spec=replace(
        design.spec, bus_voltage_v=stage.bus_voltage_v,
        output_power_w=stage.output_power_w,
        switching_frequency_hz=stage.switching_frequency_hz,
        line_frequency_hz=stage.line_frequency_hz,
    ))


def _mean_half_cycle(values: np.ndarray, trace: TTPLLineTrace, *,
                     line_cycle: PFCLineCycleResult | None = None) -> float:
    if line_cycle is not None:
        # Periodic endpoint-exclusive full-cycle samples: each timestep has the
        # same weight, exactly as PF/THD and capacitor RMS on this shared result.
        return float(np.mean(values))
    theta = np.radians(trace.angle_deg)
    return float(np.trapezoid(values, theta) / math.pi)


def _rds_at(device: MosfetSpec, temperature_c: float) -> float:
    """Interpolate the explicit 25/150 °C RDS points when available."""
    if device.rds_on_150c > 0.0:
        t = min(max(temperature_c, 25.0), 150.0)
        k = (t - 25.0) / 125.0
        return device.rds_on_25c + k * (device.rds_on_150c - device.rds_on_25c)
    return device.rds_on_25c * (1.0 + device.rds_alpha * (temperature_c - 25.0))


def _current_rating_at(device: MosfetSpec, temperature_c: float) -> float:
    if temperature_c <= 25.0:
        return device.id_25c
    if temperature_c >= 100.0:
        return device.id_100c
    k = (temperature_c - 25.0) / 75.0
    return device.id_25c + k * (device.id_100c - device.id_25c)


def _scaled_switch_energy_j(
    device: MosfetSpec,
    *,
    voltage_v: float,
    current_a: np.ndarray,
    turn_on: bool,
) -> np.ndarray:
    ref_uj = device.eon_ref_uj if turn_on else device.eoff_ref_uj
    if ref_uj > 0.0 and device.e_ref_v > 0.0 and device.e_ref_i > 0.0:
        return (
            ref_uj
            * 1e-6
            * (voltage_v / device.e_ref_v)
            * (np.maximum(current_a, 0.0) / device.e_ref_i)
        )
    edge_s = device.tr if turn_on else device.tf
    return 0.5 * voltage_v * np.maximum(current_a, 0.0) * edge_s


def _upper_active_fraction(trace: TTPLLineTrace, line_cycle: PFCLineCycleResult | None):
    """Fixed-node convention: positive Vac => HF lower active, LF lower on.

    The legacy half-cycle is explicitly mirrored, hence either physical switch
    spends half the full period in each HF role. Full-cycle inputs are never
    symmetrized. At an exact zero-voltage sample split the role weight equally.
    """
    if np.any(trace.average_inductor_current_a < 0) or np.any(trace.ripple_pp_a < 0):
        raise ValueError("physical switch allocation requires nonnegative rectified current/ripple")
    if line_cycle is None:
        return np.full_like(trace.duty, 0.5)
    vac = np.asarray(line_cycle.vac_v)
    zero_tol = max(float(np.max(np.abs(vac))) * 1e-12, 1e-12)
    if not np.any(vac > zero_tol) or not np.any(vac < -zero_tol):
        raise ValueError("physical switch allocation requires both AC half-cycle polarities")
    if not np.allclose(np.abs(line_cycle.iac_a), trace.average_inductor_current_a,
                       rtol=1e-6, atol=1e-9):
        raise ValueError("line and inductor currents disagree; physical switch allocation is undefined")
    if np.any(vac * line_cycle.iac_a < -zero_tol):
        raise ValueError("regenerative current is outside the rectified boost allocation model")
    return np.where(vac < -zero_tol, 1.0, np.where(vac > zero_tol, 0.0, 0.5))


def _physical_temperatures(junction_temperature_c, upper, lower):
    values = (junction_temperature_c if upper is None else upper,
              junction_temperature_c if lower is None else lower)
    if not all(math.isfinite(value) for value in values):
        raise ValueError("physical junction temperatures must be finite")
    return values


def evaluate_hf_device(
    result: TTPLDesignResult,
    device: MosfetSpec,
    *,
    vin_rms_v: float,
    junction_temperature_c: float = 100.0,
    deadtime_s: float = 100.0e-9,
    reverse_drop_v: float = 2.0,
    voltage_derating: float = 0.80,
    line_cycle: PFCLineCycleResult | None = None,
    upper_junction_temperature_c: float | None = None,
    lower_junction_temperature_c: float | None = None,
) -> TTPLHFDeviceLoss:
    """Evaluate both fixed HF switches and the aggregate active/SR role terms.

    Eon(valley)+Eoff(peak) belong to the active role; Eoff(valley) belongs
    to SR. The two deadtime reverse-conduction intervals belong to SR under
    the rectified CCM boost assumption. Each physical MOSFET retains half the
    existing two-device Coss proxy and one Qg*Vgs charge per switching cycle.
    """
    if deadtime_s < 0.0:
        raise ValueError("deadtime cannot be negative")
    if reverse_drop_v < 0.0:
        raise ValueError("reverse-conduction drop cannot be negative")
    if not 0.0 < voltage_derating <= 1.0:
        raise ValueError("voltage derating must lie in (0,1]")

    spec = result.spec
    trace = _line_trace(result, vin_rms_v) if line_cycle is None else _closed_loop_trace(line_cycle)
    upper_active = _upper_active_fraction(trace, line_cycle)
    upper_tj, lower_tj = _physical_temperatures(
        junction_temperature_c, upper_junction_temperature_c, lower_junction_temperature_c)
    upper_rds, lower_rds = _rds_at(device, upper_tj), _rds_at(device, lower_tj)
    fsw = spec.switching_frequency_hz if line_cycle is None else line_cycle.fsw_hz
    voltage = spec.bus_voltage_v if line_cycle is None else line_cycle.vbus_v
    local_i2 = trace.average_inductor_current_a**2 + trace.ripple_pp_a**2 / 12.0
    active_i2 = trace.duty * local_i2
    sr_i2 = (1.0 - trace.duty) * local_i2

    def mean(values):
        return _mean_half_cycle(values, trace, line_cycle=line_cycle)

    active_rds = upper_active * upper_rds + (1.0 - upper_active) * lower_rds
    sr_rds = (1.0 - upper_active) * upper_rds + upper_active * lower_rds
    p_active_cond = mean(active_rds * active_i2)
    p_sr_cond = mean(sr_rds * sr_i2)
    active_sw = fsw * (
        _scaled_switch_energy_j(device, voltage_v=voltage,
                                current_a=trace.inductor_valley_a, turn_on=True)
        + _scaled_switch_energy_j(device, voltage_v=voltage,
                                  current_a=trace.inductor_peak_a, turn_on=False))
    sr_sw = fsw * _scaled_switch_energy_j(
        device, voltage_v=voltage, current_a=trace.inductor_valley_a, turn_on=False)
    sr_dead = fsw * reverse_drop_v * deadtime_s * (trace.inductor_peak_a + trace.inductor_valley_a)
    # Same screening energy convention as the aggregate model, now attributable.
    coss_each = 0.5 * device.coss_er * np.asarray(voltage)**2 * fsw
    gate_each = device.qg * device.vgs * fsw

    def physical(active_fraction, rds, tj):
        cond = rds * mean(active_fraction * active_i2 + (1.0 - active_fraction) * sr_i2)
        switching = mean(active_fraction * active_sw + (1.0 - active_fraction) * sr_sw)
        dead = mean((1.0 - active_fraction) * sr_dead)
        coss = mean(np.ones_like(local_i2) * coss_each)
        current_ok = float(np.max(trace.inductor_peak_a)) <= _current_rating_at(device, tj)
        return TTPLPhysicalDeviceLoss(rds, cond, switching, dead, coss, gate_each,
                                      cond + switching + dead + coss + gate_each, current_ok)

    upper = physical(upper_active, upper_rds, upper_tj)
    lower = physical(1.0 - upper_active, lower_rds, lower_tj)
    # Backward-compatible scalar: effective leg Rds. Individual Rds are explicit
    # in upper/lower and are the only values used for thermal feedback.
    rds = (p_active_cond + p_sr_cond) / mean(local_i2) if mean(local_i2) > 0 else 0.5 * (upper_rds + lower_rds)
    has_energy_ref = (device.eon_ref_uj > 0.0 and device.eoff_ref_uj > 0.0
                      and device.e_ref_v > 0.0 and device.e_ref_i > 0.0)
    return TTPLHFDeviceLoss(
        device=device, rds_hot_ohm=rds,
        active_conduction_w=p_active_cond, active_switching_w=mean(active_sw),
        sr_conduction_w=p_sr_cond, sr_switching_w=mean(sr_sw),
        deadtime_reverse_w=upper.deadtime_reverse_w + lower.deadtime_reverse_w,
        coss_w=upper.coss_w + lower.coss_w, gate_drive_w=2.0 * gate_each,
        total_w=upper.total_w + lower.total_w,
        voltage_ok=float(np.max(voltage)) <= device.vds_max * voltage_derating,
        current_ok=upper.current_ok and lower.current_ok,
        switching_model="datasheet Eon/Eoff linear V/I scale" if has_energy_ref else "tr/tf linear-ramp fallback",
        upper=upper, lower=lower,
    )


def evaluate_slow_device(
    result: TTPLDesignResult,
    device: MosfetSpec,
    *,
    vin_rms_v: float,
    junction_temperature_c: float = 100.0,
    voltage_derating: float = 0.80,
    line_cycle: PFCLineCycleResult | None = None,
    upper_junction_temperature_c: float | None = None,
    lower_junction_temperature_c: float | None = None,
) -> TTPLSlowDeviceLoss:
    """Evaluate each fixed LF switch using the same series-inductor I².

    LF lower conducts at positive Vac, upper at negative Vac. Both therefore
    include the switching ripple term Δi²/12, even though they switch at line
    frequency. Each receives one Qg*Vgs gate charge per complete line period.
    """
    if not 0.0 < voltage_derating <= 1.0:
        raise ValueError("voltage derating must lie in (0,1]")
    spec = result.spec
    trace = _line_trace(result, vin_rms_v) if line_cycle is None else _closed_loop_trace(line_cycle)
    upper_on = _upper_active_fraction(trace, line_cycle)
    upper_tj, lower_tj = _physical_temperatures(
        junction_temperature_c, upper_junction_temperature_c, lower_junction_temperature_c)
    local_i2 = trace.average_inductor_current_a**2 + trace.ripple_pp_a**2 / 12.0
    line_hz = spec.line_frequency_hz if line_cycle is None else line_cycle.config.power_stage.line_frequency_hz
    voltage = spec.bus_voltage_v if line_cycle is None else float(np.max(line_cycle.vbus_v))
    gate_each = device.qg * device.vgs * line_hz

    def physical(on_fraction, tj):
        rds = _rds_at(device, tj)
        cond = rds * _mean_half_cycle(on_fraction * local_i2, trace, line_cycle=line_cycle)
        peak = float(np.max(np.where(on_fraction > 0, trace.inductor_peak_a, 0.0)))
        return TTPLPhysicalDeviceLoss(rds, cond, 0.0, 0.0, 0.0, gate_each,
                                      cond + gate_each, peak <= _current_rating_at(device, tj))

    upper, lower = physical(upper_on, upper_tj), physical(1.0 - upper_on, lower_tj)
    p_cond = upper.conduction_w + lower.conduction_w
    mean_i2 = _mean_half_cycle(local_i2, trace, line_cycle=line_cycle)
    rds = p_cond / mean_i2 if mean_i2 > 0 else 0.5 * (upper.rds_hot_ohm + lower.rds_hot_ohm)
    return TTPLSlowDeviceLoss(
        device=device, rds_hot_ohm=rds, conduction_w=p_cond,
        gate_drive_w=2.0 * gate_each, total_w=upper.total_w + lower.total_w,
        voltage_ok=voltage <= device.vds_max * voltage_derating,
        current_ok=upper.current_ok and lower.current_ok, upper=upper, lower=lower,
    )


def compare_ttpl_devices(
    design: TTPLDesignResult,
    database: PFCDeviceDatabase,
    *,
    workpoint: str = "low",
    junction_temperature_c: float = 100.0,
    deadtime_s: float = 100.0e-9,
    reverse_drop_v: float = 2.0,
    voltage_derating: float = 0.80,
    line_cycle: PFCLineCycleResult | None = None,
    switched_cycle: PFCSwitchedCycleResult | None = None,
) -> TTPLDeviceComparison:
    """Compare every built-in/user MOSFET at one electrical work point."""
    if switched_cycle is not None:
        if line_cycle is not None:
            raise ValueError("select exactly one PFC current source")
        from .switched_loss import compare_switched_devices
        return compare_switched_devices(design, database, switched_cycle,
            junction_temperature_c=junction_temperature_c, voltage_derating=voltage_derating)
    if line_cycle is not None:
        design = _design_for_line_cycle(design, line_cycle)
    key = workpoint.strip().casefold()
    points = {
        "low": design.spec.vin_min_rms_v,
        "nominal": design.spec.vin_nom_rms_v,
        "high": design.spec.vin_max_rms_v,
    }
    if key not in points:
        raise ValueError("workpoint must be 'low', 'nominal' or 'high'")
    vin = points[key] if line_cycle is None else line_cycle.config.power_stage.vin_rms_v
    if line_cycle is not None:
        key = "closed_loop"
    hf = tuple(
        sorted(
            (
                evaluate_hf_device(
                    design,
                    device,
                    vin_rms_v=vin,
                    junction_temperature_c=junction_temperature_c,
                    deadtime_s=deadtime_s,
                    reverse_drop_v=reverse_drop_v,
                    voltage_derating=voltage_derating,
                    line_cycle=line_cycle,
                )
                for device in database.all
            ),
            key=lambda item: item.total_w,
        )
    )
    slow = tuple(
        sorted(
            (
                evaluate_slow_device(
                    design,
                    device,
                    vin_rms_v=vin,
                    junction_temperature_c=junction_temperature_c,
                    voltage_derating=voltage_derating,
                    line_cycle=line_cycle,
                )
                for device in database.all
            ),
            key=lambda item: item.total_w,
        )
    )
    pfthd = None
    capacitor_ripple = None
    if line_cycle is not None:
        from .capacitor_ripple import analyze_capacitor_ripple
        from .pfc_v3 import compute_pf_thd
        capacitor_ripple = analyze_capacitor_ripple(line_cycle)
        pfthd = compute_pf_thd(line_cycle.time_s, line_cycle.vac_v, line_cycle.iac_a,
                              line_hz=design.spec.line_frequency_hz)
    return TTPLDeviceComparison(
        design=design,
        workpoint=key,
        vin_rms_v=vin,
        junction_temperature_c=junction_temperature_c,
        deadtime_s=deadtime_s,
        reverse_drop_v=reverse_drop_v,
        voltage_derating=voltage_derating,
        hf_devices=hf,
        slow_devices=slow,
        waveform_source="ideal_sizing_half_cycle" if line_cycle is None else "closed_loop_final_line_cycle",
        capacitor_current_rms_a=None if line_cycle is None else float(np.sqrt(np.mean(line_cycle.capacitor_current_a**2))),
        capacitor_ripple=capacitor_ripple,
        power_factor=None if pfthd is None else pfthd.pf,
        thd_percent=None if pfthd is None else pfthd.thd_percent,
        line_cycle=line_cycle,
    )


__all__ = [
    "TTPLDeviceComparison",
    "TTPLHFDeviceLoss",
    "TTPLPhysicalDeviceLoss",
    "TTPLSlowDeviceLoss",
    "compare_ttpl_devices",
    "evaluate_hf_device",
    "evaluate_slow_device",
]

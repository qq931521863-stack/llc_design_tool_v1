"""TTPL semiconductor comparison on the engineering power-stage line cycle.

This is a screening model, not a semiconductor sign-off model.  It integrates
conduction and switching loss over a CCM half-line-cycle and keeps the existing
PFC database's single-point Eon/Eoff/Coss assumptions explicit.
"""
from __future__ import annotations

from dataclasses import dataclass
import math

import numpy as np

from pfc_design.core.spec import MosfetSpec
from .device_library import PFCDeviceDatabase
from .ttpl_design import TTPLDesignResult, TTPLLineTrace


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


@dataclass(frozen=True)
class TTPLSlowDeviceLoss:
    device: MosfetSpec
    rds_hot_ohm: float
    conduction_w: float
    gate_drive_w: float
    total_w: float
    voltage_ok: bool
    current_ok: bool


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


def _mean_half_cycle(values: np.ndarray, trace: TTPLLineTrace) -> float:
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


def evaluate_hf_device(
    result: TTPLDesignResult,
    device: MosfetSpec,
    *,
    vin_rms_v: float,
    junction_temperature_c: float = 100.0,
    deadtime_s: float = 100.0e-9,
    reverse_drop_v: float = 2.0,
    voltage_derating: float = 0.80,
) -> TTPLHFDeviceLoss:
    """Evaluate one device used for both TTPL high-frequency half-bridge positions."""
    if deadtime_s < 0.0:
        raise ValueError("deadtime cannot be negative")
    if reverse_drop_v < 0.0:
        raise ValueError("reverse-conduction drop cannot be negative")
    if not 0.0 < voltage_derating <= 1.0:
        raise ValueError("voltage derating must lie in (0,1]")

    spec = result.spec
    trace = _line_trace(result, vin_rms_v)
    rds = _rds_at(device, junction_temperature_c)
    local_i2 = trace.average_inductor_current_a**2 + trace.ripple_pp_a**2 / 12.0

    p_active_cond = rds * _mean_half_cycle(trace.duty * local_i2, trace)
    p_sr_cond = rds * _mean_half_cycle((1.0 - trace.duty) * local_i2, trace)

    eon = _scaled_switch_energy_j(
        device,
        voltage_v=spec.bus_voltage_v,
        current_a=trace.inductor_valley_a,
        turn_on=True,
    )
    eoff_active = _scaled_switch_energy_j(
        device,
        voltage_v=spec.bus_voltage_v,
        current_a=trace.inductor_peak_a,
        turn_on=False,
    )
    eoff_sr = _scaled_switch_energy_j(
        device,
        voltage_v=spec.bus_voltage_v,
        current_a=trace.inductor_valley_a,
        turn_on=False,
    )
    p_active_sw = spec.switching_frequency_hz * _mean_half_cycle(eon + eoff_active, trace)
    p_sr_sw = spec.switching_frequency_hz * _mean_half_cycle(eoff_sr, trace)

    p_dead = (
        spec.switching_frequency_hz
        * reverse_drop_v
        * deadtime_s
        * _mean_half_cycle(trace.inductor_peak_a + trace.inductor_valley_a, trace)
    )
    # Two high-frequency devices, each charged/discharged once per switching cycle.
    p_coss = device.coss_er * spec.bus_voltage_v**2 * spec.switching_frequency_hz
    p_gate = 2.0 * device.qg * device.vgs * spec.switching_frequency_hz
    total = p_active_cond + p_active_sw + p_sr_cond + p_sr_sw + p_dead + p_coss + p_gate

    peak_current = float(np.max(trace.inductor_peak_a))
    current_rating = _current_rating_at(device, junction_temperature_c)
    has_energy_ref = (
        device.eon_ref_uj > 0.0
        and device.eoff_ref_uj > 0.0
        and device.e_ref_v > 0.0
        and device.e_ref_i > 0.0
    )
    return TTPLHFDeviceLoss(
        device=device,
        rds_hot_ohm=rds,
        active_conduction_w=p_active_cond,
        active_switching_w=p_active_sw,
        sr_conduction_w=p_sr_cond,
        sr_switching_w=p_sr_sw,
        deadtime_reverse_w=p_dead,
        coss_w=p_coss,
        gate_drive_w=p_gate,
        total_w=total,
        voltage_ok=spec.bus_voltage_v <= device.vds_max * voltage_derating,
        current_ok=peak_current <= current_rating,
        switching_model="datasheet Eon/Eoff linear V/I scale" if has_energy_ref else "tr/tf linear-ramp fallback",
    )


def evaluate_slow_device(
    result: TTPLDesignResult,
    device: MosfetSpec,
    *,
    vin_rms_v: float,
    junction_temperature_c: float = 100.0,
    voltage_derating: float = 0.80,
) -> TTPLSlowDeviceLoss:
    """Evaluate the complete two-device line-frequency leg."""
    if not 0.0 < voltage_derating <= 1.0:
        raise ValueError("voltage derating must lie in (0,1]")
    spec = result.spec
    i_rms = spec.output_power_w / (spec.efficiency * vin_rms_v)
    i_peak = math.sqrt(2.0) * i_rms
    rds = _rds_at(device, junction_temperature_c)
    p_cond = i_rms**2 * rds
    p_gate = 2.0 * device.qg * device.vgs * spec.line_frequency_hz
    return TTPLSlowDeviceLoss(
        device=device,
        rds_hot_ohm=rds,
        conduction_w=p_cond,
        gate_drive_w=p_gate,
        total_w=p_cond + p_gate,
        voltage_ok=spec.bus_voltage_v <= device.vds_max * voltage_derating,
        current_ok=i_peak <= _current_rating_at(device, junction_temperature_c),
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
) -> TTPLDeviceComparison:
    """Compare every built-in/user MOSFET at one electrical work point."""
    key = workpoint.strip().casefold()
    points = {
        "low": design.spec.vin_min_rms_v,
        "nominal": design.spec.vin_nom_rms_v,
        "high": design.spec.vin_max_rms_v,
    }
    if key not in points:
        raise ValueError("workpoint must be 'low', 'nominal' or 'high'")
    vin = points[key]
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
                )
                for device in database.all
            ),
            key=lambda item: item.total_w,
        )
    )
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
    )


__all__ = [
    "TTPLDeviceComparison",
    "TTPLHFDeviceLoss",
    "TTPLSlowDeviceLoss",
    "compare_ttpl_devices",
    "evaluate_hf_device",
    "evaluate_slow_device",
]

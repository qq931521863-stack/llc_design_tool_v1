"""Direct physical-branch loss observers for accepted shared-ngspice cycles.

Signed measured channel currents and diode V*I are never routed through CCM
triangles or a Vac-sign current-magnitude allocation. Selected Rds/energy data
are screening overlays on the frozen generic circuit, not a circuit rerun.
"""

from __future__ import annotations

from dataclasses import replace

import numpy as np

from .device_loss import (
    TTPLDeviceComparison,
    TTPLHFDeviceLoss,
    TTPLPhysicalDeviceLoss,
    TTPLSlowDeviceLoss,
    _current_rating_at,
    _rds_at,
    _scaled_switch_energy_j,
)
from .switched_cycle import PFCSwitchedCycleResult


def design_for_switched_cycle(design, source):
    source.require_accepted()
    stage = source.config.power_stage
    return replace(
        design,
        spec=replace(
            design.spec,
            vin_nom_rms_v=stage.vin_rms_v,
            vin_min_rms_v=min(design.spec.vin_min_rms_v, stage.vin_rms_v),
            vin_max_rms_v=max(design.spec.vin_max_rms_v, stage.vin_rms_v),
            bus_voltage_v=stage.bus_voltage_v,
            output_power_w=stage.output_power_w,
            switching_frequency_hz=stage.switching_frequency_hz,
            line_frequency_hz=stage.line_frequency_hz,
        ),
    )


def _active_upper(source, time=None):
    segments = source.source_metadata["commutation_segments"]
    starts = np.array([s["start_time_s"] for s in segments])
    polarity = np.array([s["target_polarity"] for s in segments])
    index = np.maximum(
        np.searchsorted(
            starts, source.qualification_time_s if time is None else time, side="right"
        )
        - 1,
        0,
    )
    return polarity[index] < 0


def _physical(source, device, position, temperature, active_mask=None):
    if not np.isfinite(temperature):
        raise ValueError("junction temperature must be finite")
    native_current = source.qualification_signals[f"i(vs{position})"]
    rds = _rds_at(device, temperature)
    conduction = source.integrate_native(native_current * native_current) * rds
    diode = source.diode_power(position)
    events = source.switch_events[position]
    rising = [event for event in events if event["turn_on"]]
    energies = []
    for event in events:
        # Terminal current includes the actual body-diode path as well as the
        # channel. Use measured states immediately bracketing this gate edge.
        current = max(
            abs(event["channel_left_a"] - event["diode_left_a"]),
            abs(event["channel_right_a"] - event["diode_right_a"]),
        )
        voltage = max(abs(event["voltage_left_v"]), abs(event["voltage_right_v"]))
        energy = float(
            _scaled_switch_energy_j(
                device,
                voltage_v=voltage,
                current_a=np.asarray(current),
                turn_on=event["turn_on"],
            )
        )
        energies.append(energy)
    switching = sum(energies) / source.duration_s
    # A supplied Eon can already include capacitive energy even when Eoff is
    # absent. Do not add rising Coss on top of Eon-only user data either.
    has_eon = device.eon_ref_uj > 0 and device.e_ref_v > 0 and device.e_ref_i > 0
    coss = (
        0.0
        if has_eon
        else sum(
            0.5
            * device.coss_er_pF
            * 1e-12
            * max(abs(e["voltage_left_v"]), abs(e["voltage_right_v"])) ** 2
            for e in rising
        )
        / source.duration_s
    )
    gate_drive = len(rising) * device.qg * device.vgs / source.duration_s
    current = source.signals[f"i(vs{position})"] - source.signals[f"i(vd{position})"]
    physical = TTPLPhysicalDeviceLoss(
        rds,
        conduction,
        switching,
        0.0,
        coss,
        gate_drive,
        conduction + switching + diode + coss + gate_drive,
        float(np.max(abs(current))) <= _current_rating_at(device, temperature),
        diode,
    )
    if active_mask is None:
        return physical
    active_cond = (
        source.integrate_native(native_current * native_current * active_mask) * rds
    )
    if events:
        active_events = _active_upper(source, np.array([e["time_s"] for e in events]))
        if position == "hfl":
            active_events = ~active_events
        active_sw = float(np.dot(energies, active_events)) / source.duration_s
    else:
        active_sw = 0.0
    return physical, active_cond, active_sw


def evaluate_switched_hf(
    source,
    device,
    *,
    upper_temperature_c=100.0,
    lower_temperature_c=100.0,
    voltage_derating=0.8,
):
    source.require_accepted()
    if not 0 < voltage_derating <= 1:
        raise ValueError("voltage derating must lie in (0,1]")
    active = _active_upper(source)
    upper, upper_cond, upper_sw = _physical(
        source, device, "hfh", upper_temperature_c, active
    )
    lower, lower_cond, lower_sw = _physical(
        source, device, "hfl", lower_temperature_c, ~active
    )
    cond, switching = (
        upper.conduction_w + lower.conduction_w,
        upper.switching_w + lower.switching_w,
    )
    active_cond, active_sw = upper_cond + lower_cond, upper_sw + lower_sw
    i2 = source.channel_mean_square("hfh") + source.channel_mean_square("hfl")
    return TTPLHFDeviceLoss(
        device,
        cond / i2 if i2 else upper.rds_hot_ohm,
        active_cond,
        active_sw,
        cond - active_cond,
        switching - active_sw,
        0.0,
        upper.coss_w + lower.coss_w,
        upper.gate_drive_w + lower.gate_drive_w,
        upper.total_w + lower.total_w,
        max(float(max(abs(source.voltage(p)))) for p in ("hfh", "hfl"))
        <= device.vds_max * voltage_derating,
        upper.current_ok and lower.current_ok,
        "Measured signed channel/diode paths; selected Rds and edge Eon/Eoff or tr/tf; Coss excluded with supplied Eon to avoid overlap",
        upper,
        lower,
        upper.diode_conduction_w + lower.diode_conduction_w,
    )


def evaluate_switched_slow(
    source,
    device,
    *,
    upper_temperature_c=100.0,
    lower_temperature_c=100.0,
    voltage_derating=0.8,
):
    source.require_accepted()
    if not 0 < voltage_derating <= 1:
        raise ValueError("voltage derating must lie in (0,1]")
    upper = _physical(source, device, "lfp", upper_temperature_c)
    lower = _physical(source, device, "lfn", lower_temperature_c)
    cond = upper.conduction_w + lower.conduction_w
    i2 = source.channel_mean_square("lfp") + source.channel_mean_square("lfn")
    return TTPLSlowDeviceLoss(
        device,
        cond / i2 if i2 else upper.rds_hot_ohm,
        cond,
        upper.gate_drive_w + lower.gate_drive_w,
        upper.total_w + lower.total_w,
        max(float(max(abs(source.voltage(p)))) for p in ("lfp", "lfn"))
        <= device.vds_max * voltage_derating,
        upper.current_ok and lower.current_ok,
        upper,
        lower,
        upper.diode_conduction_w + lower.diode_conduction_w,
        upper.switching_w + lower.switching_w,
        upper.coss_w + lower.coss_w,
    )


def compare_switched_devices(
    design,
    database,
    source: PFCSwitchedCycleResult,
    *,
    junction_temperature_c=100.0,
    voltage_derating=0.8,
):
    design = design_for_switched_cycle(design, source)
    kwargs = {
        "upper_temperature_c": junction_temperature_c,
        "lower_temperature_c": junction_temperature_c,
        "voltage_derating": voltage_derating,
    }
    hf = tuple(
        sorted(
            (evaluate_switched_hf(source, d, **kwargs) for d in database.all),
            key=lambda d: d.total_w,
        )
    )
    lf = tuple(
        sorted(
            (evaluate_switched_slow(source, d, **kwargs) for d in database.all),
            key=lambda d: d.total_w,
        )
    )
    return TTPLDeviceComparison(
        design,
        "switching",
        source.config.power_stage.vin_rms_v,
        junction_temperature_c,
        source.config.power_stage.deadtime_s,
        0.0,
        voltage_derating,
        hf,
        lf,
        waveform_source="shared_ngspice_signed_switching_cycle",
        capacitor_current_rms_a=source.capacitor_current_rms_a,
        power_factor=source.power_factor,
        thd_percent=source.thd_percent,
        switched_cycle=source,
    )

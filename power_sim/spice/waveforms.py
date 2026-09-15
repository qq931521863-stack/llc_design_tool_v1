"""WaveformBundle adapter for shared-ngspice closed-loop results.

ngspice transient output is adaptive/non-uniform in time, while the existing
LLC :class:`WaveformBundle` statistics assume uniformly spaced samples.  The
adapter therefore resamples the accepted SPICE points to a deterministic uniform
grid before exposing them to the existing GUI.  Digital controller signals are
expanded with zero-order hold, matching firmware semantics between ISR updates.
"""
from __future__ import annotations

import math
from typing import Mapping

import numpy as np

from llc_design.dynamics.waveforms import WaveformBundle, WaveformSignal, signal_statistics

from .closed_loop import NgSpiceClosedLoopResult


def _strict_time_axis(time_s: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    raw = np.asarray(time_s, dtype=float).reshape(-1)
    if raw.size < 2 or not np.all(np.isfinite(raw)):
        raise ValueError("ngspice waveform requires at least two finite time points")
    keep = np.zeros(raw.size, dtype=bool)
    keep[0] = True
    last = raw[0]
    for i in range(1, raw.size):
        if raw[i] > last + 1e-18:
            keep[i] = True
            last = raw[i]
    filtered = raw[keep]
    if filtered.size < 2:
        raise ValueError("ngspice time vector has no strictly increasing interval")
    return filtered, keep


def _interp_analog(raw_t: np.ndarray, raw_y: np.ndarray, uniform_t: np.ndarray) -> np.ndarray:
    y = np.asarray(raw_y, dtype=float).reshape(-1)
    if y.size != raw_t.size:
        raise ValueError("ngspice vector length does not match time vector")
    return np.interp(uniform_t, raw_t, y).astype(float)


def _zoh(sample_t: np.ndarray, sample_y: np.ndarray, uniform_t: np.ndarray) -> np.ndarray:
    t = np.asarray(sample_t, dtype=float).reshape(-1)
    y = np.asarray(sample_y, dtype=float).reshape(-1)
    if t.size == 0 or t.size != y.size:
        raise ValueError("control trace requires matching non-empty time/value arrays")
    index = np.searchsorted(t, uniform_t, side="right") - 1
    index = np.clip(index, 0, len(t) - 1)
    return y[index].astype(float)


def _signal(
    key: str,
    label: str,
    unit: str,
    values: np.ndarray,
    *,
    samples_per_period: int,
    switching_frequency_hz: float,
    group: str,
    description: str,
) -> WaveformSignal:
    values = np.asarray(values, dtype=float)
    return WaveformSignal(
        key=key,
        label=label,
        unit=unit,
        values=values,
        statistics=signal_statistics(
            values,
            samples_per_period=samples_per_period,
            switching_frequency_hz=switching_frequency_hz,
        ),
        group=group,
        description=description,
    )


def ngspice_closed_loop_waveform_bundle(
    result: NgSpiceClosedLoopResult,
    *,
    bus_voltage_v: float,
    samples_per_switching_cycle: int = 100,
    maximum_points: int = 200_000,
) -> WaveformBundle:
    """Convert a shared-ngspice run to the existing LLC waveform contract.

    The returned bundle contains switching power-stage traces and the exact
    digital-control traces used during the same simulation.  It is intended for
    GUI visualization/cursor/statistics; the original adaptive ngspice vectors
    remain available in ``NgSpiceClosedLoopResult.vectors`` for detailed solver
    diagnostics.
    """
    if samples_per_switching_cycle < 20:
        raise ValueError("samples_per_switching_cycle must be >=20")
    if maximum_points < 100:
        raise ValueError("maximum_points must be >=100")
    if "time" not in result.vectors:
        raise ValueError("closed-loop ngspice result has no time vector")

    raw_t_all = np.asarray(result.vectors["time"], dtype=float).reshape(-1)
    raw_t, keep = _strict_time_axis(raw_t_all)
    if raw_t[0] < -1e-15:
        raise ValueError("negative ngspice transient time is not supported")

    control = result.control.samples
    if not control:
        raise ValueError("closed-loop ngspice result has no digital control samples")
    control_t = np.asarray([sample.time_s for sample in control], dtype=float)
    fsw = np.asarray([sample.frequency_actual_hz for sample in control], dtype=float)
    if not np.all(np.isfinite(fsw)) or np.any(fsw <= 0.0):
        raise ValueError("closed-loop switching-frequency trace is invalid")
    representative_fsw = float(np.median(fsw))
    maximum_fsw = float(np.max(fsw))

    duration = float(raw_t[-1] - raw_t[0])
    requested_points = int(math.ceil(duration * maximum_fsw * samples_per_switching_cycle)) + 1
    point_count = min(max(requested_points, 2), int(maximum_points))
    uniform_t = np.linspace(float(raw_t[0]), float(raw_t[-1]), point_count)
    uniform_rate_hz = (point_count - 1) / max(duration, 1e-30)
    samples_per_period = max(20, int(round(uniform_rate_hz / representative_fsw)))

    raw_vectors: dict[str, np.ndarray] = {}
    for name, values in result.vectors.items():
        if name == "time":
            continue
        values_arr = np.asarray(values, dtype=float).reshape(-1)
        if values_arr.size == raw_t_all.size:
            raw_vectors[name] = values_arr[keep]

    analog: dict[str, np.ndarray] = {}
    for name, values in raw_vectors.items():
        analog[name] = _interp_analog(raw_t, values, uniform_t)

    signals: dict[str, WaveformSignal] = {}

    def add_analog(key: str, source: str, label: str, unit: str, group: str, description: str) -> None:
        if source not in analog:
            return
        signals[key] = _signal(
            key,
            label,
            unit,
            analog[source],
            samples_per_period=samples_per_period,
            switching_frequency_hz=representative_fsw,
            group=group,
            description=description,
        )

    add_analog("v_leg_a", "v(a)", "Bridge leg A", "V", "bridge", "ngspice full-bridge midpoint A")
    add_analog("v_leg_b", "v(b)", "Bridge leg B", "V", "bridge", "ngspice full-bridge midpoint B")
    if "v(a)" in analog and "v(b)" in analog:
        values = analog["v(a)"] - analog["v(b)"]
        signals["v_bridge"] = _signal(
            "v_bridge", "Bridge differential voltage", "V", values,
            samples_per_period=samples_per_period,
            switching_frequency_hz=representative_fsw,
            group="bridge",
            description="V(A)-V(B) from shared-ngspice",
        )
    add_analog("i_resonant", "i(lr)", "Resonant current", "A", "tank", "ngspice Lr current")

    if "v(out)" in analog:
        vout = analog["v(out)"]
        ripple = vout - float(np.mean(vout))
        signals["v_output_ripple"] = _signal(
            "v_output_ripple", "Output voltage ripple", "V", ripple,
            samples_per_period=samples_per_period,
            switching_frequency_hz=representative_fsw,
            group="output",
            description="ngspice Vout with DC component removed",
        )
        signals["v_output"] = _signal(
            "v_output", "Output voltage", "V", vout,
            samples_per_period=samples_per_period,
            switching_frequency_hz=representative_fsw,
            group="output",
            description="absolute ngspice output voltage",
        )

    gate_map = {
        "gate_q1": ("v(g_ah)", "Q1 / AH gate"),
        "gate_q2": ("v(g_al)", "Q2 / AL gate"),
        "gate_q3": ("v(g_bh)", "Q3 / BH gate"),
        "gate_q4": ("v(g_bl)", "Q4 / BL gate"),
    }
    for key, (source, label) in gate_map.items():
        add_analog(key, source, label, "V", "primary_switch", "shared-ngspice external gate source")

    if "v(a)" in analog:
        add_values = {
            "vds_q1": float(bus_voltage_v) - analog["v(a)"],
            "vds_q2": analog["v(a)"],
        }
        for key, values in add_values.items():
            signals[key] = _signal(
                key, key.upper(), "V", values,
                samples_per_period=samples_per_period,
                switching_frequency_hz=representative_fsw,
                group="primary_switch",
                description="ideal-switch VDS reconstructed from bridge node",
            )
    if "v(b)" in analog:
        add_values = {
            "vds_q3": float(bus_voltage_v) - analog["v(b)"],
            "vds_q4": analog["v(b)"],
        }
        for key, values in add_values.items():
            signals[key] = _signal(
                key, key.upper(), "V", values,
                samples_per_period=samples_per_period,
                switching_frequency_hz=representative_fsw,
                group="primary_switch",
                description="ideal-switch VDS reconstructed from bridge node",
            )

    # Digital traces are held between controller ticks exactly as the firmware
    # command is held until its next update.
    control_defs: Mapping[str, tuple[str, str, np.ndarray]] = {
        "control_reference": ("Reference", "V", np.asarray([s.reference_v for s in control], dtype=float)),
        "control_feedback": ("Sampled feedback", "V", np.asarray([s.feedback_v for s in control], dtype=float)),
        "control_error": ("Control error", "V", np.asarray([s.error_v for s in control], dtype=float)),
        "control_output": ("Controller output", "pu", np.asarray([s.controller_output for s in control], dtype=float)),
        "switching_frequency": ("Switching frequency", "Hz", fsw),
    }
    for key, (label, unit, values) in control_defs.items():
        expanded = _zoh(control_t, values, uniform_t)
        signals[key] = _signal(
            key,
            label,
            unit,
            expanded,
            samples_per_period=samples_per_period,
            switching_frequency_hz=representative_fsw,
            group="control",
            description="zero-order-held exact digital closed-loop trace",
        )

    warnings = (
        "Shared-ngspice uses an ideal switching correlation model with explicit small physical damping; it is not a vendor MOSFET/Qrr/SR model.",
        "Adaptive SPICE vectors were resampled to a uniform grid for WaveformBundle statistics and GUI display.",
        "Digital controller traces are zero-order-held between exact control ticks.",
    )
    metadata: dict[str, float | str] = {
        "engine": "shared-ngspice",
        "model_level": str(result.control.metadata.get("model_level", "ideal_switching_correlation")),
        "controller_name": str(result.control.metadata.get("controller_name", "")),
        "controller_source": str(result.control.metadata.get("controller_source", "")),
        "control_sample_rate_hz": float(result.control.metadata.get("sample_rate_hz", 0.0)),
        "uniform_display_rate_hz": float(uniform_rate_hz),
        "adaptive_raw_points": float(len(raw_t)),
    }
    return WaveformBundle(
        time_s=uniform_t,
        switching_frequency_hz=representative_fsw,
        model_name="shared-ngspice digital closed loop",
        signals=signals,
        warnings=warnings,
        metadata=metadata,
    )


__all__ = ["ngspice_closed_loop_waveform_bundle"]

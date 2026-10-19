"""Validated endpoint-exclusive TD samples for downstream loss consumers.

Magnetic FFT/iGSE routines require ONE period, even when the producer exports
several cycles. No cached statistics, magnetic reconstruction or fitted sine
wave is substituted for supplied samples.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from .waveforms import WaveformBundle


@dataclass(frozen=True)
class PeriodicSamples:
    time_s: np.ndarray
    frequency_hz: float
    signals: dict[str, np.ndarray]


def periodic_samples(bundle: WaveformBundle, units: dict[str, str],
                     td_converged: bool | None) -> PeriodicSamples:
    if td_converged is not True:
        raise ValueError("time-domain loss requires an explicitly converged waveform")
    time = np.asarray(bundle.time_s, dtype=float)
    fs = bundle.switching_frequency_hz
    if (time.ndim != 1 or len(time) < 32 or not np.isfinite(time).all()
            or not math.isfinite(fs) or fs <= 0):
        raise ValueError("time-domain passive loss requires finite periodic samples and positive frequency")
    steps = np.diff(time)
    if np.any(steps <= 0) or not np.allclose(steps, steps[0], rtol=1e-7, atol=1e-15):
        raise ValueError("time-domain passive loss requires uniform time samples")
    count = round(1.0 / (steps[0] * fs))
    if count < 32 or not math.isclose(count * steps[0] * fs, 1.0, rel_tol=1e-7):
        raise ValueError("time-domain passive loss requires an integer sample count per period")
    if len(time) % count:
        raise ValueError("time-domain passive loss requires complete switching periods")
    signals = {}
    for key, unit in units.items():
        if key not in bundle.signals:
            raise ValueError(f"time-domain passive loss is missing required signal: {key}")
        signal = bundle.signal(key)
        values = np.asarray(signal.values, dtype=float)
        if signal.unit != unit or values.shape != time.shape or not np.isfinite(values).all():
            raise ValueError(f"invalid time-domain passive samples or unit: {key}")
        periods = values.reshape(-1, count)
        scale = max(float(np.max(np.abs(values))), 1e-12)
        if len(periods) > 1 and np.max(np.abs(np.diff(periods, axis=0))) > 1e-6 * scale:
            raise ValueError(f"time-domain samples are not cycle-repeatable: {key}")
        # Do not pass two periods to a one-period FFT with fs as the fundamental.
        signals[key] = values[-count:].copy()
    return PeriodicSamples(np.arange(count) * steps[0], fs, signals)


def transformer_flux(samples: PeriodicSamples, turns: int, area_m2: float,
                     magnetizing_inductance_h: float, spec=None) -> np.ndarray:
    """Preserve modeled DC flux from Lm*Im/(N*Ae), cross-check Faraday's law.

    The linear Lm state retains a DC-current offset; physical remanence and
    nonlinear/hysteretic B(H) remain outside this model. Sampled Vp must have
    zero mean within 1 ppm. The AC flux cross-check allows dt*total_variation(V)
    quadrature error for switching edges, never a free flux-walk correction.
    """
    voltage = samples.signals["v_transformer_primary"]
    current = samples.signals["i_magnetizing"]
    if spec is not None and spec.integrated_magnetics:
        from ..core.integrated_magnetics import from_spec
        magnetic = from_spec(spec)
        voltage = magnetic.physical_core_voltage(voltage, samples.signals["v_resonant_inductor"])
        current = magnetic.physical_magnetizing_current(samples.signals["i_resonant"], current)
        magnetizing_inductance_h = magnetic.physical_magnetizing_h
    mean = float(np.mean(voltage))
    scale = max(float(np.max(np.abs(voltage))), 1e-12)
    if abs(mean) > 1e-6 * scale:
        raise ValueError("time-domain transformer voltage fails volt-second balance (1 ppm screen)")
    if turns <= 0 or area_m2 <= 0 or magnetizing_inductance_h <= 0:
        raise ValueError("transformer flux requires positive inductance, turns and core area")
    dt = samples.time_s[1] - samples.time_s[0]
    increments = 0.5 * (voltage + np.roll(voltage, -1)) * dt
    integral = np.concatenate(([0.0], np.cumsum(increments[:-1])))
    state_flux = magnetizing_inductance_h * current
    ac_error = np.max(np.abs((integral - np.mean(integral)) - (state_flux - np.mean(state_flux))))
    quadrature_bound = dt * float(np.sum(np.abs(np.roll(voltage, -1) - voltage)))
    numerical_floor = 1e-7 * max(float(np.ptp(state_flux)), 1e-12)
    if ac_error > quadrature_bound + numerical_floor:
        raise ValueError("time-domain magnetizing state disagrees with Faraday flux beyond sample-resolution bound")
    return state_flux / (turns * area_m2)

from __future__ import annotations

from dataclasses import dataclass
import math

import numpy as np
from numpy.typing import NDArray

from power_control_tools.models import DigitalTransferFunction


@dataclass(frozen=True)
class GainCrossover:
    frequency_hz: float
    phase_deg: float
    phase_margin_deg: float


@dataclass(frozen=True)
class PhaseCrossover:
    frequency_hz: float
    target_phase_deg: float
    magnitude_db: float
    gain_margin_db: float


@dataclass(frozen=True)
class LoopStabilityResult:
    gain_crossovers: tuple[GainCrossover, ...]
    phase_crossovers: tuple[PhaseCrossover, ...]
    main_crossover_hz: float | None
    phase_margin_deg: float | None
    gain_margin_db: float | None
    worst_phase_margin_deg: float | None
    worst_gain_margin_db: float | None
    ms: float
    mt: float
    sensitivity: NDArray[np.complex128]
    complementary_sensitivity: NDArray[np.complex128]
    status: str


@dataclass(frozen=True)
class DeembedResult:
    equivalent_plant: NDArray[np.complex128]
    rebuilt_loop: NDArray[np.complex128]
    magnitude_error_db_max: float
    phase_error_deg_max: float
    passed: bool


def digital_frequency_response(
    digital: DigitalTransferFunction,
    frequency_hz: NDArray[np.float64] | np.ndarray,
) -> NDArray[np.complex128]:
    """Evaluate an exact discrete H(z) on arbitrary FRA frequencies.

    The coefficient convention is the project's canonical one:
    H(z)=(b0+b1 z^-1+...)/(1+a1 z^-1+...).
    """
    d = digital.normalized()
    f = np.asarray(frequency_hz, dtype=float)
    if np.any(f < 0.0):
        raise ValueError("frequency must be non-negative")
    q = np.exp(-1j * 2.0 * np.pi * f / d.sample_rate_hz)
    num = np.zeros_like(q, dtype=complex)
    den = np.zeros_like(q, dtype=complex)
    for k, c in enumerate(d.b):
        num += float(c) * np.power(q, k)
    for k, c in enumerate(d.a):
        den += float(c) * np.power(q, k)
    if np.any(np.abs(den) < 1e-18):
        raise ValueError("controller frequency response contains a denominator singularity")
    return num / den


def magnitude_phase(response: NDArray[np.complex128] | np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    h = np.asarray(response, dtype=complex)
    mag = 20.0 * np.log10(np.maximum(np.abs(h), 1e-300))
    phase = np.unwrap(np.angle(h)) * 180.0 / np.pi
    return mag, phase


def phase_margin_from_unwrapped_phase(phase_deg: float) -> float:
    """Return signed phase margin for an arbitrary unwrapped loop phase.

    FRA records may legitimately unwrap below -360 deg.  The conventional
    ``180 + phase`` expression is only valid while phase is on the branch near
    -180 deg.  For later turns the relevant signed angular distance is to the
    nearest odd-180-degree branch.  Wrapping ``phase + 180`` to [-180, 180)
    gives that distance while preserving negative margin after a -180 crossing.

    Examples:
      -138 deg -> +42 deg PM
      -313 deg -> -133 deg PM
      -407 deg -> +133 deg PM (nearest branch is -540 deg)
    """
    x = float(phase_deg) + 180.0
    return (x + 180.0) % 360.0 - 180.0


def deembed_controller(
    measured_loop: NDArray[np.complex128] | np.ndarray,
    controller_response: NDArray[np.complex128] | np.ndarray,
    *,
    magnitude_tolerance_db: float = 1e-9,
    phase_tolerance_deg: float = 1e-8,
) -> DeembedResult:
    measured = np.asarray(measured_loop, dtype=complex)
    controller = np.asarray(controller_response, dtype=complex)
    if measured.shape != controller.shape:
        raise ValueError("measured loop and controller response shapes must match")
    if np.any(np.abs(controller) < 1e-18):
        raise ValueError("existing controller has a zero response at an FRA frequency")
    plant = measured / controller
    rebuilt = plant * controller
    ratio = rebuilt / np.where(np.abs(measured) > 1e-300, measured, 1e-300 + 0j)
    mag_error = np.abs(20.0 * np.log10(np.maximum(np.abs(ratio), 1e-300)))
    phase_error = np.abs(np.angle(ratio, deg=True))
    mag_max = float(np.max(mag_error)) if mag_error.size else math.inf
    phase_max = float(np.max(phase_error)) if phase_error.size else math.inf
    return DeembedResult(
        plant,
        rebuilt,
        mag_max,
        phase_max,
        bool(mag_max <= magnitude_tolerance_db and phase_max <= phase_tolerance_deg),
    )


def _log_interp_frequency(f0: float, f1: float, y0: float, y1: float, target: float) -> float:
    if f0 <= 0.0 or f1 <= 0.0:
        raise ValueError("log interpolation requires positive frequencies")
    if abs(y1 - y0) < 1e-30:
        return math.sqrt(f0 * f1)
    t = (target - y0) / (y1 - y0)
    t = float(np.clip(t, 0.0, 1.0))
    return float(10.0 ** (math.log10(f0) + t * (math.log10(f1) - math.log10(f0))))


def _interp_logx(f: np.ndarray, y: np.ndarray, x: float) -> float:
    if x <= f[0]:
        return float(y[0])
    if x >= f[-1]:
        return float(y[-1])
    i = int(np.searchsorted(f, x) - 1)
    i = max(0, min(i, len(f) - 2))
    lx0, lx1, lx = math.log10(f[i]), math.log10(f[i + 1]), math.log10(x)
    if lx1 == lx0:
        return float(y[i])
    t = (lx - lx0) / (lx1 - lx0)
    return float(y[i] + t * (y[i + 1] - y[i]))


def _crossings(f: np.ndarray, y: np.ndarray, target: float) -> list[float]:
    out: list[float] = []
    for i in range(len(f) - 1):
        a = float(y[i] - target)
        b = float(y[i + 1] - target)
        if a == 0.0:
            out.append(float(f[i]))
            continue
        if a * b < 0.0 or b == 0.0:
            out.append(_log_interp_frequency(float(f[i]), float(f[i + 1]), float(y[i]), float(y[i + 1]), target))
    dedup: list[float] = []
    for value in out:
        if not dedup or abs(math.log(value / dedup[-1])) > 1e-9:
            dedup.append(value)
    return dedup


def analyze_loop_response(
    frequency_hz: NDArray[np.float64] | np.ndarray,
    loop_response: NDArray[np.complex128] | np.ndarray,
    *,
    pm_pass_deg: float = 45.0,
    gm_pass_db: float = 6.0,
) -> LoopStabilityResult:
    f = np.asarray(frequency_hz, dtype=float).reshape(-1)
    h = np.asarray(loop_response, dtype=complex).reshape(-1)
    if f.size < 2 or f.size != h.size:
        raise ValueError("loop analysis requires matching frequency/response arrays")
    if np.any(f <= 0.0) or np.any(np.diff(f) <= 0.0):
        raise ValueError("loop analysis frequencies must be strictly increasing and positive")
    if not np.all(np.isfinite(h.real)) or not np.all(np.isfinite(h.imag)):
        raise ValueError("loop response contains NaN or infinite values")

    mag_db, phase_deg = magnitude_phase(h)

    gain_crossovers: list[GainCrossover] = []
    for fc in _crossings(f, mag_db, 0.0):
        phase = _interp_logx(f, phase_deg, fc)
        gain_crossovers.append(GainCrossover(fc, phase, phase_margin_from_unwrapped_phase(phase)))

    pmin = float(np.min(phase_deg))
    pmax = float(np.max(phase_deg))
    k_min = int(math.ceil((pmin + 180.0) / 360.0))
    k_max = int(math.floor((pmax + 180.0) / 360.0))
    phase_crossovers: list[PhaseCrossover] = []
    for k in range(k_min, k_max + 1):
        target = -180.0 + 360.0 * k
        for fp in _crossings(f, phase_deg, target):
            gain = _interp_logx(f, mag_db, fp)
            phase_crossovers.append(PhaseCrossover(fp, target, gain, -gain))
    phase_crossovers.sort(key=lambda x: x.frequency_hz)

    denom = 1.0 + h
    with np.errstate(divide="ignore", invalid="ignore"):
        s = 1.0 / denom
        t = h / denom
    s_abs = np.abs(s)
    t_abs = np.abs(t)
    ms = float(np.nanmax(s_abs)) if s_abs.size else math.inf
    mt = float(np.nanmax(t_abs)) if t_abs.size else math.inf

    main = gain_crossovers[0] if gain_crossovers else None
    worst_pm = min((x.phase_margin_deg for x in gain_crossovers), default=None)
    worst_gm = min((x.gain_margin_db for x in phase_crossovers), default=None)

    if not gain_crossovers:
        status = "NO_0DB_CROSSING"
    elif (worst_pm is not None and worst_pm <= 0.0) or (worst_gm is not None and worst_gm <= 0.0):
        status = "FAIL"
    elif len(gain_crossovers) > 1:
        status = "WARNING_MULTIPLE_CROSSOVERS"
    elif worst_pm is None or worst_pm < pm_pass_deg:
        status = "REVIEW_PM"
    elif not phase_crossovers:
        # GM cannot be claimed from a finite FRA record that never reaches an
        # odd-180-degree phase crossing. Keep the result explicitly unresolved.
        status = "REVIEW_GM_NOT_OBSERVED"
    elif worst_gm is None or worst_gm < gm_pass_db:
        status = "REVIEW_GM"
    else:
        status = "PASS"

    return LoopStabilityResult(
        tuple(gain_crossovers),
        tuple(phase_crossovers),
        main.frequency_hz if main else None,
        main.phase_margin_deg if main else None,
        worst_gm,
        worst_pm,
        worst_gm,
        ms,
        mt,
        s,
        t,
        status,
    )


__all__ = [
    "GainCrossover",
    "PhaseCrossover",
    "LoopStabilityResult",
    "DeembedResult",
    "digital_frequency_response",
    "magnitude_phase",
    "phase_margin_from_unwrapped_phase",
    "deembed_controller",
    "analyze_loop_response",
]

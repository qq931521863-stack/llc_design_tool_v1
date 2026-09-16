"""Fc × PM controller-design solution map for a fully defined loop.

The map does not create another plant model.  It consumes the *fixed* return-path
response already produced by the maintained LLC/PFC engines after removing only
the current controller.  Therefore Plant + Sensor + ADC + PWM/FM + Delay remain
exactly the system the user defined.

V9.3 first synthesizes the firmware Tustin PI structure.  PIF/2P2Z map synthesis
will be added as separate structures rather than silently approximating them as
PI controllers.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
import math
from typing import Sequence

import numpy as np
from numpy.typing import NDArray

from .digital_loop import PIControllerConfig, calculate_stability_margins


FloatArray = NDArray[np.float64]
ComplexArray = NDArray[np.complex128]


class SolutionStatus(IntEnum):
    """Objective engineering classification; values are stable for GUI/export."""

    FEASIBLE = 0
    PHASE_TARGET_UNREACHABLE = 1
    TARGET_MISMATCH = 2
    LOOP_UNSTABLE = 3
    GM_CONSTRAINT_FAIL = 4
    MS_CONSTRAINT_FAIL = 5
    SWITCHING_ATTENUATION_FAIL = 6
    INVALID_POINT = 7


STATUS_LABELS: dict[SolutionStatus, str] = {
    SolutionStatus.FEASIBLE: "FEASIBLE",
    SolutionStatus.PHASE_TARGET_UNREACHABLE: "PHASE_TARGET_UNREACHABLE",
    SolutionStatus.TARGET_MISMATCH: "TARGET_MISMATCH",
    SolutionStatus.LOOP_UNSTABLE: "LOOP_UNSTABLE",
    SolutionStatus.GM_CONSTRAINT_FAIL: "GM_CONSTRAINT_FAIL",
    SolutionStatus.MS_CONSTRAINT_FAIL: "MS_CONSTRAINT_FAIL",
    SolutionStatus.SWITCHING_ATTENUATION_FAIL: "SWITCHING_ATTENUATION_FAIL",
    SolutionStatus.INVALID_POINT: "INVALID_POINT",
}


@dataclass(frozen=True)
class SolutionMapConstraints:
    minimum_gain_margin_db: float = 6.0
    maximum_sensitivity: float = 2.0
    maximum_switching_loop_gain_db: float = -20.0
    crossover_tolerance_fraction: float = 0.05
    phase_margin_tolerance_deg: float = 3.0

    def validate(self) -> None:
        if self.maximum_sensitivity <= 0.0:
            raise ValueError("maximum sensitivity must be positive")
        if self.crossover_tolerance_fraction <= 0.0:
            raise ValueError("crossover tolerance must be positive")
        if self.phase_margin_tolerance_deg <= 0.0:
            raise ValueError("phase-margin tolerance must be positive")


@dataclass(frozen=True)
class SolutionMapPoint:
    target_crossover_hz: float
    target_phase_margin_deg: float
    status: SolutionStatus
    kp: float | None = None
    ti_s: float | None = None
    actual_crossover_hz: float | None = None
    actual_phase_margin_deg: float | None = None
    gain_margin_db: float | None = None
    ms: float | None = None
    mt: float | None = None
    switching_loop_gain_db: float | None = None
    controller_phase_deg: float | None = None
    message: str = ""

    @property
    def feasible(self) -> bool:
        return self.status == SolutionStatus.FEASIBLE


@dataclass(frozen=True)
class SolutionMapResult:
    frequencies_hz: FloatArray
    crossover_targets_hz: FloatArray
    phase_margin_targets_deg: FloatArray
    points: tuple[tuple[SolutionMapPoint, ...], ...]
    status_codes: NDArray[np.int16]
    feasible_mask: NDArray[np.bool_]
    sample_rate_hz: float
    switching_frequency_hz: float
    loop_label: str
    constraints: SolutionMapConstraints

    def point(self, pm_index: int, fc_index: int) -> SolutionMapPoint:
        return self.points[pm_index][fc_index]

    @property
    def feasible_fraction(self) -> float:
        return float(np.mean(self.feasible_mask)) if self.feasible_mask.size else 0.0


def _interp_complex_log_frequency(
    frequencies_hz: FloatArray,
    response: ComplexArray,
    target_hz: float,
) -> complex:
    if target_hz < frequencies_hz[0] or target_hz > frequencies_hz[-1]:
        raise ValueError("target frequency lies outside the analyzed frequency range")
    x = np.log(frequencies_hz)
    xt = math.log(target_hz)
    mag = np.log(np.maximum(np.abs(response), 1e-300))
    phase = np.unwrap(np.angle(response))
    return complex(
        math.exp(float(np.interp(xt, x, mag)))
        * np.exp(1j * float(np.interp(xt, x, phase)))
    )


def _required_pi_phase_deg(fixed_at_fc: complex, target_pm_deg: float) -> float | None:
    """Return a PI phase in (-90, 0) that places L at -180 + PM.

    The fixed-loop phase is ambiguous by integer turns.  Try equivalent branches
    and accept only a phase physically realizable by a positive-gain PI.
    """
    fixed_phase = math.degrees(math.atan2(fixed_at_fc.imag, fixed_at_fc.real))
    target_loop_phase = -180.0 + target_pm_deg
    candidates = [target_loop_phase + 360.0 * k - fixed_phase for k in range(-2, 3)]
    valid = [value for value in candidates if -89.999 < value < -0.001]
    if not valid:
        return None
    return min(valid, key=abs)


def synthesize_tustin_pi_at_target(
    fixed_at_fc: complex,
    *,
    target_crossover_hz: float,
    target_phase_margin_deg: float,
    sample_rate_hz: float,
) -> tuple[PIControllerConfig, float] | None:
    """Synthesize the exact firmware Tustin PI at one Fc/PM target.

    For the firmware PI

        C(z) = Kp * [(1+k) + (-1+k) z^-1] / (1-z^-1)
        k = Ts/(2 Ti)

    at z=exp(j*w*Ts), the normalized response is

        C/Kp = 1 - j * k * cot(w Ts / 2).

    This gives Ti directly from the required controller phase; Kp then enforces
    |L(j*w_c)|=1.  No continuous-PI fit or second S-to-Z conversion is used.
    """
    if sample_rate_hz <= 0.0 or target_crossover_hz <= 0.0:
        raise ValueError("sample/crossover frequencies must be positive")
    if not 0.0 < target_phase_margin_deg < 180.0:
        raise ValueError("target phase margin must lie in (0, 180) degrees")
    if target_crossover_hz >= 0.5 * sample_rate_hz:
        return None
    if not (np.isfinite(fixed_at_fc.real) and np.isfinite(fixed_at_fc.imag)):
        return None
    fixed_mag = abs(fixed_at_fc)
    if fixed_mag <= 1e-30:
        return None

    controller_phase = _required_pi_phase_deg(fixed_at_fc, target_phase_margin_deg)
    if controller_phase is None:
        return None

    ts = 1.0 / sample_rate_hz
    theta = 2.0 * math.pi * target_crossover_hz * ts
    cot = 1.0 / math.tan(0.5 * theta)
    x = math.tan(math.radians(-controller_phase))
    if not (x > 0.0 and cot > 0.0 and np.isfinite(x) and np.isfinite(cot)):
        return None
    ti_s = ts * cot / (2.0 * x)
    normalized_mag = math.sqrt(1.0 + x * x)
    kp = 1.0 / (fixed_mag * normalized_mag)
    if not (kp > 0.0 and ti_s > 0.0 and np.isfinite(kp) and np.isfinite(ti_s)):
        return None
    return PIControllerConfig(kp=kp, ti_s=ti_s, sample_time_s=ts), controller_phase


def evaluate_solution_point(
    frequencies_hz: Sequence[float] | FloatArray,
    fixed_loop_response: Sequence[complex] | ComplexArray,
    *,
    sample_rate_hz: float,
    switching_frequency_hz: float,
    target_crossover_hz: float,
    target_phase_margin_deg: float,
    constraints: SolutionMapConstraints | None = None,
) -> SolutionMapPoint:
    constraints = constraints or SolutionMapConstraints()
    constraints.validate()
    f = np.asarray(frequencies_hz, dtype=float)
    fixed = np.asarray(fixed_loop_response, dtype=complex)
    if f.ndim != 1 or fixed.shape != f.shape or len(f) < 3 or np.any(np.diff(f) <= 0.0):
        raise ValueError("solution-map input must be a strictly increasing 1-D frequency response")

    try:
        fixed_fc = _interp_complex_log_frequency(f, fixed, target_crossover_hz)
    except Exception as exc:
        return SolutionMapPoint(target_crossover_hz, target_phase_margin_deg, SolutionStatus.INVALID_POINT, message=str(exc))

    synthesis = synthesize_tustin_pi_at_target(
        fixed_fc,
        target_crossover_hz=target_crossover_hz,
        target_phase_margin_deg=target_phase_margin_deg,
        sample_rate_hz=sample_rate_hz,
    )
    if synthesis is None:
        return SolutionMapPoint(
            target_crossover_hz,
            target_phase_margin_deg,
            SolutionStatus.PHASE_TARGET_UNREACHABLE,
            message="required controller phase is outside the positive-gain PI range",
        )

    controller_cfg, controller_phase = synthesis
    controller = controller_cfg.transfer_function()
    loop = fixed * controller.frequency_response(f)
    margins = calculate_stability_margins(f, loop)
    sensitivity = 1.0 / (1.0 + loop)
    complementary = loop / (1.0 + loop)
    ms = float(np.nanmax(np.abs(sensitivity)))
    mt = float(np.nanmax(np.abs(complementary)))

    switching_gain = None
    if f[0] <= switching_frequency_hz <= f[-1]:
        switching_gain = 20.0 * math.log10(
            max(abs(_interp_complex_log_frequency(f, loop, switching_frequency_hz)), 1e-300)
        )

    fc_actual = margins.critical_gain_crossover_hz
    pm_actual = margins.phase_margin_deg
    gm = margins.gain_margin_db
    common = dict(
        target_crossover_hz=target_crossover_hz,
        target_phase_margin_deg=target_phase_margin_deg,
        kp=controller_cfg.kp,
        ti_s=controller_cfg.ti_s,
        actual_crossover_hz=fc_actual,
        actual_phase_margin_deg=pm_actual,
        gain_margin_db=gm,
        ms=ms,
        mt=mt,
        switching_loop_gain_db=switching_gain,
        controller_phase_deg=controller_phase,
    )

    if fc_actual is None or pm_actual is None or pm_actual <= 0.0 or (gm is not None and gm <= 0.0):
        return SolutionMapPoint(status=SolutionStatus.LOOP_UNSTABLE, message="critical loop margin is non-positive", **common)
    fc_error = abs(fc_actual / target_crossover_hz - 1.0)
    pm_error = abs(pm_actual - target_phase_margin_deg)
    if fc_error > constraints.crossover_tolerance_fraction or pm_error > constraints.phase_margin_tolerance_deg:
        return SolutionMapPoint(status=SolutionStatus.TARGET_MISMATCH, message="another crossover/phase branch dominates the requested target", **common)
    if gm is not None and gm < constraints.minimum_gain_margin_db:
        return SolutionMapPoint(status=SolutionStatus.GM_CONSTRAINT_FAIL, message="gain-margin constraint violated", **common)
    if ms > constraints.maximum_sensitivity:
        return SolutionMapPoint(status=SolutionStatus.MS_CONSTRAINT_FAIL, message="maximum-sensitivity constraint violated", **common)
    if switching_gain is not None and switching_gain > constraints.maximum_switching_loop_gain_db:
        return SolutionMapPoint(status=SolutionStatus.SWITCHING_ATTENUATION_FAIL, message="loop attenuation at switching frequency is insufficient", **common)
    return SolutionMapPoint(status=SolutionStatus.FEASIBLE, message="all configured constraints satisfied", **common)


def build_fc_pm_solution_map(
    frequencies_hz: Sequence[float] | FloatArray,
    fixed_loop_response: Sequence[complex] | ComplexArray,
    *,
    sample_rate_hz: float,
    switching_frequency_hz: float,
    crossover_targets_hz: Sequence[float] | FloatArray,
    phase_margin_targets_deg: Sequence[float] | FloatArray,
    loop_label: str = "loop",
    constraints: SolutionMapConstraints | None = None,
) -> SolutionMapResult:
    """Evaluate an Fc × PM mesh against one fully defined fixed loop."""
    constraints = constraints or SolutionMapConstraints()
    constraints.validate()
    f = np.asarray(frequencies_hz, dtype=float)
    fixed = np.asarray(fixed_loop_response, dtype=complex)
    fc = np.asarray(crossover_targets_hz, dtype=float)
    pm = np.asarray(phase_margin_targets_deg, dtype=float)
    if np.any(fc <= 0.0) or np.any(pm <= 0.0) or np.any(pm >= 180.0):
        raise ValueError("invalid Fc/PM target grid")
    if np.any(np.diff(fc) <= 0.0) or np.any(np.diff(pm) <= 0.0):
        raise ValueError("Fc and PM target arrays must be strictly increasing")

    rows: list[tuple[SolutionMapPoint, ...]] = []
    codes = np.empty((len(pm), len(fc)), dtype=np.int16)
    for iy, target_pm in enumerate(pm):
        row: list[SolutionMapPoint] = []
        for ix, target_fc in enumerate(fc):
            point = evaluate_solution_point(
                f,
                fixed,
                sample_rate_hz=sample_rate_hz,
                switching_frequency_hz=switching_frequency_hz,
                target_crossover_hz=float(target_fc),
                target_phase_margin_deg=float(target_pm),
                constraints=constraints,
            )
            row.append(point)
            codes[iy, ix] = int(point.status)
        rows.append(tuple(row))
    feasible = codes == int(SolutionStatus.FEASIBLE)
    return SolutionMapResult(
        frequencies_hz=f,
        crossover_targets_hz=fc,
        phase_margin_targets_deg=pm,
        points=tuple(rows),
        status_codes=codes,
        feasible_mask=feasible,
        sample_rate_hz=float(sample_rate_hz),
        switching_frequency_hz=float(switching_frequency_hz),
        loop_label=loop_label,
        constraints=constraints,
    )


__all__ = [
    "STATUS_LABELS",
    "SolutionMapConstraints",
    "SolutionMapPoint",
    "SolutionMapResult",
    "SolutionStatus",
    "build_fc_pm_solution_map",
    "evaluate_solution_point",
    "synthesize_tustin_pi_at_target",
]

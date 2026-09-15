from __future__ import annotations

from dataclasses import dataclass
import math

import numpy as np
from numpy.typing import NDArray
from scipy.optimize import minimize_scalar

from power_control_tools.controllers import design_controller
from power_control_tools.discretize import discretize_transfer_function
from power_control_tools.fra.analysis import (
    LoopStabilityResult,
    analyze_loop_response,
    digital_frequency_response,
    magnitude_phase,
)
from power_control_tools.fra.power_compensators import design_power_pz_compensator
from power_control_tools.models import ControllerKind, DigitalTransferFunction, DiscretizationMethod, StabilityClass


@dataclass(frozen=True)
class AutoDesignCandidate:
    requested_crossover_hz: float
    achieved_crossover_hz: float | None
    achieved_phase_margin_deg: float | None
    gain_margin_db: float | None
    ms: float
    mt: float
    controller: DigitalTransferFunction
    controller_kind: ControllerKind
    parameters: dict[str, float]
    metrics: LoopStabilityResult
    accepted: bool
    score: float
    note: str = ""


@dataclass(frozen=True)
class AutoDesignResult:
    requested_crossover_hz: float
    requested_phase_margin_deg: float
    selected: AutoDesignCandidate | None
    candidates: tuple[AutoDesignCandidate, ...]
    fallback_used: bool
    status: str
    message: str


def _interp_logx(frequency_hz: np.ndarray, values: np.ndarray, target_hz: float) -> float:
    f = np.asarray(frequency_hz, dtype=float)
    y = np.asarray(values, dtype=float)
    if target_hz <= f[0]:
        return float(y[0])
    if target_hz >= f[-1]:
        return float(y[-1])
    i = int(np.searchsorted(f, target_hz) - 1)
    i = max(0, min(i, len(f) - 2))
    x0, x1, x = math.log10(f[i]), math.log10(f[i + 1]), math.log10(target_hz)
    t = 0.0 if x1 == x0 else (x - x0) / (x1 - x0)
    return float(y[i] + t * (y[i + 1] - y[i]))


def _interp_response(
    frequency_hz: NDArray[np.float64] | np.ndarray,
    response: NDArray[np.complex128] | np.ndarray,
    target_hz: float,
) -> complex:
    f = np.asarray(frequency_hz, dtype=float).reshape(-1)
    h = np.asarray(response, dtype=complex).reshape(-1)
    mag_db, phase_deg = magnitude_phase(h)
    mag = _interp_logx(f, mag_db, target_hz)
    phase = _interp_logx(f, phase_deg, target_hz)
    return complex(10.0 ** (mag / 20.0) * np.exp(1j * math.radians(phase)))


def _wrap_phase_deg(value: float) -> float:
    return (float(value) + 180.0) % 360.0 - 180.0


def _controller_phase_deg(controller: DigitalTransferFunction, fc_hz: float) -> float:
    h = digital_frequency_response(controller, np.asarray([float(fc_hz)], dtype=float))[0]
    return math.degrees(math.atan2(h.imag, h.real))


def _scale_digital_gain(
    controller: DigitalTransferFunction,
    plant_at_fc: complex,
    fc_hz: float,
    *,
    name: str | None = None,
) -> tuple[DigitalTransferFunction, float]:
    d = controller.normalized()
    c_at_fc = digital_frequency_response(d, np.asarray([float(fc_hz)], dtype=float))[0]
    denom = abs(plant_at_fc * c_at_fc)
    if not math.isfinite(denom) or denom < 1e-30:
        raise ValueError("cannot solve controller gain at requested crossover")
    scale = 1.0 / denom
    tuned = DigitalTransferFunction(
        tuple(float(scale * v) for v in d.b),
        d.a,
        d.sample_rate_hz,
        name or d.name,
        f"FRA auto design gain-scaled from {d.source or d.name}",
    ).normalized()
    return tuned, float(scale)


def _optimize_single_frequency_phase(
    builder,
    *,
    low_hz: float,
    high_hz: float,
    target_phase_deg: float,
    fc_hz: float,
    max_error_deg: float = 3.0,
) -> tuple[DigitalTransferFunction, float, float]:
    low = max(float(low_hz), 1e-9)
    high = max(float(high_hz), low * 1.001)

    def objective(log_frequency: float) -> float:
        controller = builder(10.0 ** float(log_frequency))
        actual = _controller_phase_deg(controller, fc_hz)
        error = _wrap_phase_deg(actual - target_phase_deg)
        return error * error

    result = minimize_scalar(
        objective,
        bounds=(math.log10(low), math.log10(high)),
        method="bounded",
        options={"xatol": 2e-5, "maxiter": 120},
    )
    frequency = 10.0 ** float(result.x)
    controller = builder(frequency)
    phase = _controller_phase_deg(controller, fc_hz)
    error = _wrap_phase_deg(phase - target_phase_deg)
    if abs(error) > float(max_error_deg):
        raise ValueError(
            f"selected controller structure cannot reproduce required digital phase at Fc: error={error:.3f} deg"
        )
    return controller, float(frequency), float(error)


def _required_controller_phase(plant_at_fc: complex, target_pm_deg: float) -> float:
    plant_phase = math.degrees(math.atan2(plant_at_fc.imag, plant_at_fc.real))
    desired_loop_phase = -180.0 + float(target_pm_deg)
    return _wrap_phase_deg(desired_loop_phase - plant_phase)


def _build_pi_for_target(
    plant_at_fc: complex,
    fc_hz: float,
    target_pm_deg: float,
    sample_rate_hz: float,
    method: DiscretizationMethod,
) -> tuple[DigitalTransferFunction, dict[str, float]]:
    required_phase = _required_controller_phase(plant_at_fc, target_pm_deg)
    if not (-89.5 <= required_phase <= -0.02):
        raise ValueError(f"PI cannot provide required controller phase {required_phase:.3f} deg at this Fc")

    low_fz = max(fc_hz / 200.0, 1e-3)
    high_fz = min(fc_hz * 200.0, 0.45 * sample_rate_hz)

    def builder(fz_hz: float) -> DigitalTransferFunction:
        ti = 1.0 / (2.0 * math.pi * fz_hz)
        analog = design_controller(ControllerKind.PI, kp=1.0, ti_s=ti)
        return discretize_transfer_function(analog, sample_rate_hz, method)

    unity, fz, phase_error = _optimize_single_frequency_phase(
        builder,
        low_hz=low_fz,
        high_hz=high_fz,
        target_phase_deg=required_phase,
        fc_hz=fc_hz,
    )
    digital, gain = _scale_digital_gain(unity, plant_at_fc, fc_hz, name="FRA Auto PI")
    ti = 1.0 / (2.0 * math.pi * fz)
    return digital, {
        "kp": gain,
        "ti_s": float(ti),
        "fz_hz": float(fz),
        "digital_phase_error_deg": phase_error,
    }


def _build_pif_for_target(
    plant_at_fc: complex,
    fc_hz: float,
    target_pm_deg: float,
    sample_rate_hz: float,
    method: DiscretizationMethod,
) -> tuple[DigitalTransferFunction, dict[str, float]]:
    required_phase = _required_controller_phase(plant_at_fc, target_pm_deg)
    fp = min(max(8.0 * fc_hz, 1.5 * fc_hz), 0.40 * sample_rate_hz)
    low_fz = max(fc_hz / 200.0, 1e-3)
    high_fz = min(fc_hz * 200.0, 0.45 * sample_rate_hz)

    def builder(fz_hz: float) -> DigitalTransferFunction:
        ti = 1.0 / (2.0 * math.pi * fz_hz)
        analog = design_controller(ControllerKind.PIF, kp=1.0, ti_s=ti, lpf_pole_hz=fp)
        return discretize_transfer_function(analog, sample_rate_hz, method)

    unity, fz, phase_error = _optimize_single_frequency_phase(
        builder,
        low_hz=low_fz,
        high_hz=high_fz,
        target_phase_deg=required_phase,
        fc_hz=fc_hz,
    )
    digital, gain = _scale_digital_gain(unity, plant_at_fc, fc_hz, name="FRA Auto PIF")
    ti = 1.0 / (2.0 * math.pi * fz)
    return digital, {
        "kp": gain,
        "ti_s": float(ti),
        "fz_hz": float(fz),
        "lpf_pole_hz": float(fp),
        "digital_phase_error_deg": phase_error,
    }


def _build_pid_for_target(
    plant_at_fc: complex,
    fc_hz: float,
    target_pm_deg: float,
    sample_rate_hz: float,
    method: DiscretizationMethod,
) -> tuple[DigitalTransferFunction, dict[str, float]]:
    """Conservative ideal-PID heuristic followed by exact H(z) verification.

    Ideal PID has no derivative roll-off.  It remains available for parity with
    the existing controller engine, but one-click acceptance still depends on
    the full measured FRA band and should be treated more cautiously than PIF
    or the power 2P2Z/3P3Z templates.
    """
    required = _required_controller_phase(plant_at_fc, target_pm_deg)
    if not (-80.0 <= required <= 80.0):
        raise ValueError(f"PID heuristic cannot provide required controller phase {required:.3f} deg at this Fc")
    omega = 2.0 * math.pi * fc_hz
    if required <= 0.0:
        td = 0.0
        q = max(-math.tan(math.radians(required)), 1e-4)
        ti = 1.0 / (omega * q)
    else:
        integral_term = 0.10
        ti = 1.0 / (omega * integral_term)
        td = max((math.tan(math.radians(required)) + integral_term) / omega, 0.0)
    analog = design_controller(ControllerKind.PID, kp=1.0, ti_s=ti, td_s=td)
    unity = discretize_transfer_function(analog, sample_rate_hz, method)
    phase_error = _wrap_phase_deg(_controller_phase_deg(unity, fc_hz) - required)
    if abs(phase_error) > 6.0:
        raise ValueError(f"digital PID phase target error {phase_error:.3f} deg is too large at requested Fc")
    digital, gain = _scale_digital_gain(unity, plant_at_fc, fc_hz, name="FRA Auto PID")
    return digital, {
        "kp": gain,
        "ti_s": float(ti),
        "td_s": float(td),
        "digital_phase_error_deg": float(phase_error),
    }


def _power_pz_poles(kind: ControllerKind, fc_hz: float, sample_rate_hz: float) -> tuple[float, ...]:
    upper = 0.40 * float(sample_rate_hz)
    if kind == ControllerKind.TWO_P_TWO_Z:
        return (float(min(max(6.0 * fc_hz, 1.5 * fc_hz), upper)),)
    p1 = float(min(max(4.0 * fc_hz, 1.5 * fc_hz), upper))
    p2 = float(min(max(10.0 * fc_hz, 2.0 * fc_hz), upper))
    return (p1, p2)


def _build_power_pz_for_target(
    kind: ControllerKind,
    plant_at_fc: complex,
    fc_hz: float,
    target_pm_deg: float,
    sample_rate_hz: float,
    method: DiscretizationMethod,
) -> tuple[DigitalTransferFunction, dict[str, float]]:
    if kind == ControllerKind.TWO_P_TWO_Z:
        factors = (0.70, 1.40)
    elif kind == ControllerKind.THREE_P_THREE_Z:
        factors = (0.50, 1.00, 2.00)
    else:
        raise ValueError("power PZ auto design supports only 2P2Z/3P3Z")

    required_phase = _required_controller_phase(plant_at_fc, target_pm_deg)
    poles = _power_pz_poles(kind, fc_hz, sample_rate_hz)
    low_base = max(fc_hz / 300.0, 1e-3)
    high_base = min(fc_hz * 300.0, 0.40 * sample_rate_hz / max(factors))

    def builder(base_zero_hz: float) -> DigitalTransferFunction:
        zeros = tuple(float(np.clip(base_zero_hz * factor, 1e-3, 0.45 * sample_rate_hz)) for factor in factors)
        analog = design_power_pz_compensator(kind, zeros_hz=zeros, poles_hz=poles, gain=1.0)
        return discretize_transfer_function(analog, sample_rate_hz, method)

    unity, base_zero, phase_error = _optimize_single_frequency_phase(
        builder,
        low_hz=low_base,
        high_hz=high_base,
        target_phase_deg=required_phase,
        fc_hz=fc_hz,
        max_error_deg=4.0,
    )
    digital, gain = _scale_digital_gain(
        unity,
        plant_at_fc,
        fc_hz,
        name="FRA Auto Power 2P2Z" if kind == ControllerKind.TWO_P_TWO_Z else "FRA Auto Power 3P3Z",
    )
    zeros = tuple(float(np.clip(base_zero * factor, 1e-3, 0.45 * sample_rate_hz)) for factor in factors)
    params: dict[str, float] = {
        "gain": gain,
        "integrator_pole_hz": 0.0,
        "digital_phase_error_deg": phase_error,
    }
    for i, value in enumerate(zeros, 1):
        params[f"fz{i}_hz"] = value
    for i, value in enumerate(poles, 1):
        params[f"fp{i}_hz"] = value
    return digital, params


def _controller_for_target(
    kind: ControllerKind,
    plant_at_fc: complex,
    fc_hz: float,
    target_pm_deg: float,
    sample_rate_hz: float,
    method: DiscretizationMethod,
) -> tuple[DigitalTransferFunction, dict[str, float]]:
    if kind == ControllerKind.PI:
        return _build_pi_for_target(plant_at_fc, fc_hz, target_pm_deg, sample_rate_hz, method)
    if kind == ControllerKind.PIF:
        return _build_pif_for_target(plant_at_fc, fc_hz, target_pm_deg, sample_rate_hz, method)
    if kind == ControllerKind.PID:
        return _build_pid_for_target(plant_at_fc, fc_hz, target_pm_deg, sample_rate_hz, method)
    if kind in (ControllerKind.TWO_P_TWO_Z, ControllerKind.THREE_P_THREE_Z):
        return _build_power_pz_for_target(kind, plant_at_fc, fc_hz, target_pm_deg, sample_rate_hz, method)
    raise ValueError(f"automatic FRA design does not support controller {kind.value}")


def _candidate_score(
    metrics: LoopStabilityResult,
    requested_fc: float,
    target_pm_deg: float,
    *,
    min_gm_db: float,
    max_ms: float,
) -> tuple[float, bool, str]:
    if not metrics.gain_crossovers or metrics.main_crossover_hz is None:
        return 1e6, False, "no 0-dB crossover"
    fc = float(metrics.main_crossover_hz)
    pm = metrics.worst_phase_margin_deg
    score = 35.0 * abs(math.log(max(fc, 1e-30) / requested_fc))
    if pm is None:
        return 1e6, False, "phase margin unavailable"
    score += 0.6 * abs(float(pm) - target_pm_deg)
    if pm < target_pm_deg:
        score += 8.0 * (target_pm_deg - float(pm))
    if len(metrics.gain_crossovers) > 1:
        score += 250.0

    gm_observed = metrics.worst_gain_margin_db is not None
    if not gm_observed:
        # One-click design must not convert a finite measurement window into an
        # unsupported claim of adequate gain margin.  The user can extend the
        # FRA sweep or accept the candidate manually outside Auto PASS.
        score += 120.0
    elif metrics.worst_gain_margin_db < min_gm_db:
        score += 20.0 * (min_gm_db - metrics.worst_gain_margin_db)

    if not math.isfinite(metrics.ms):
        score += 500.0
    elif metrics.ms > max_ms:
        score += 60.0 * (metrics.ms - max_ms)

    fc_close = abs(math.log(max(fc, 1e-30) / requested_fc)) <= math.log(1.18)
    accepted = (
        fc_close
        and pm >= target_pm_deg - 2.0
        and len(metrics.gain_crossovers) == 1
        and gm_observed
        and metrics.worst_gain_margin_db is not None
        and metrics.worst_gain_margin_db >= min_gm_db
        and math.isfinite(metrics.ms)
        and metrics.ms <= max_ms
    )
    if accepted:
        note = "accepted"
    elif not gm_observed:
        note = "gain-margin phase crossing not observed in usable FRA range"
    else:
        note = "constraints not fully met"
    return float(score), bool(accepted), note


def auto_design_controller(
    frequency_hz: NDArray[np.float64] | np.ndarray,
    equivalent_plant: NDArray[np.complex128] | np.ndarray,
    *,
    controller_kind: ControllerKind | str,
    sample_rate_hz: float,
    discretization_method: DiscretizationMethod | str = DiscretizationMethod.TUSTIN,
    target_crossover_hz: float,
    target_phase_margin_deg: float,
    min_gain_margin_db: float = 6.0,
    max_ms: float = 2.0,
    min_crossover_ratio: float = 0.12,
    fallback_points: int = 20,
) -> AutoDesignResult:
    """Design a controller directly from measured Equivalent Plant data.

    The requested crossover is attempted first. If the selected controller
    structure cannot meet the full-band phase-margin/gain-margin/robustness
    constraints there, the routine progressively lowers crossover frequency and
    retries.  Candidate acceptance is always evaluated on the original FRA
    points; no rational plant fit is required for V1.5 Auto Design.

    For 2P2Z/3P3Z this function uses power-supply compensator templates with an
    integrator pole, matching common C2000/TI compensation practice rather than
    a generic equal-order lead/lag transfer function.
    """
    f = np.asarray(frequency_hz, dtype=float).reshape(-1)
    plant = np.asarray(equivalent_plant, dtype=complex).reshape(-1)
    if f.size < 4 or f.size != plant.size:
        raise ValueError("auto design requires matching FRA arrays with at least four points")
    if np.any(f <= 0.0) or np.any(np.diff(f) <= 0.0):
        raise ValueError("FRA frequencies must be strictly increasing and positive")
    if not np.all(np.isfinite(plant.real)) or not np.all(np.isfinite(plant.imag)):
        raise ValueError("Equivalent Plant contains NaN or infinite values")

    kind = ControllerKind(controller_kind)
    method = DiscretizationMethod(discretization_method)
    fs = float(sample_rate_hz)
    target_fc = float(target_crossover_hz)
    target_pm = float(target_phase_margin_deg)
    if fs <= 0.0:
        raise ValueError("sample_rate_hz must be positive")
    max_design_hz = min(float(f[-1]), 0.30 * fs)
    min_design_hz = max(float(f[0]) * 1.05, target_fc * float(min_crossover_ratio))
    if target_fc <= f[0] or target_fc > max_design_hz:
        raise ValueError(f"target crossover must be within {f[0]:.6g} .. {max_design_hz:.6g} Hz")
    if not (5.0 <= target_pm <= 85.0):
        raise ValueError("target phase margin must be within 5 .. 85 deg")

    fc_trials = np.geomspace(target_fc, min_design_hz, max(int(fallback_points), 2))
    candidates: list[AutoDesignCandidate] = []
    for fc_trial in fc_trials:
        fc_trial = float(fc_trial)
        try:
            plant_fc = _interp_response(f, plant, fc_trial)
            controller, parameters = _controller_for_target(kind, plant_fc, fc_trial, target_pm, fs, method)
            usable = f <= min(0.49 * fs, f[-1])
            c_resp = digital_frequency_response(controller, f[usable])
            loop = plant[usable] * c_resp
            metrics = analyze_loop_response(f[usable], loop)
            score, accepted, note = _candidate_score(
                metrics,
                fc_trial,
                target_pm,
                min_gm_db=float(min_gain_margin_db),
                max_ms=float(max_ms),
            )
            if controller.stability_class == StabilityClass.UNSTABLE:
                accepted = False
                score += 1000.0
                note = "controller has unit-circle-exterior pole"
            candidates.append(
                AutoDesignCandidate(
                    fc_trial,
                    metrics.main_crossover_hz,
                    metrics.phase_margin_deg,
                    metrics.gain_margin_db,
                    metrics.ms,
                    metrics.mt,
                    controller,
                    kind,
                    parameters,
                    metrics,
                    accepted,
                    float(score),
                    note,
                )
            )
        except Exception:
            # A lower Fc may make a previously impossible phase requirement
            # feasible, so failed trial construction does not terminate search.
            continue

    if not candidates:
        return AutoDesignResult(
            target_fc,
            target_pm,
            None,
            tuple(),
            False,
            "NO_SOLUTION",
            "No feasible controller candidate could be constructed inside the FRA/sample-rate design window.",
        )

    accepted = [c for c in candidates if c.accepted]
    if accepted:
        accepted.sort(key=lambda c: (-c.requested_crossover_hz, c.score))
        selected = accepted[0]
        fallback = selected.requested_crossover_hz < target_fc * 0.999
        msg = (
            "Target achieved at requested crossover."
            if not fallback
            else f"Requested Fc could not satisfy all measured robustness constraints; automatically reduced to {selected.requested_crossover_hz:.6g} Hz."
        )
        return AutoDesignResult(target_fc, target_pm, selected, tuple(candidates), fallback, "PASS", msg)

    selected = min(candidates, key=lambda c: c.score)
    return AutoDesignResult(
        target_fc,
        target_pm,
        selected,
        tuple(candidates),
        selected.requested_crossover_hz < target_fc * 0.999,
        "REVIEW",
        "No candidate met every measured robustness constraint. The best numerical candidate is returned for review only and must not be treated as a one-click PASS.",
    )


__all__ = ["AutoDesignCandidate", "AutoDesignResult", "auto_design_controller"]

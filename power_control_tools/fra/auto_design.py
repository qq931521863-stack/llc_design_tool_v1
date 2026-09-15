from __future__ import annotations

from dataclasses import dataclass
import math

import numpy as np
from numpy.typing import NDArray

from power_control_tools.controllers import design_controller
from power_control_tools.discretize import discretize_transfer_function
from power_control_tools.fra.analysis import LoopStabilityResult, analyze_loop_response, digital_frequency_response, magnitude_phase
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


def _build_pi_for_target(
    plant_at_fc: complex,
    fc_hz: float,
    target_pm_deg: float,
    sample_rate_hz: float,
    method: DiscretizationMethod,
) -> tuple[DigitalTransferFunction, dict[str, float]]:
    plant_phase = math.degrees(math.atan2(plant_at_fc.imag, plant_at_fc.real))
    desired_loop_phase = -180.0 + target_pm_deg
    required_c_phase = _wrap_phase_deg(desired_loop_phase - plant_phase)
    # PI can contribute phase from almost -90 deg to 0 deg.
    if not (-89.0 <= required_c_phase <= -0.05):
        raise ValueError(f"PI cannot provide required controller phase {required_c_phase:.3f} deg at this Fc")
    omega = 2.0 * math.pi * fc_hz
    ti = 1.0 / (omega * math.tan(math.radians(-required_c_phase)))
    base = design_controller(ControllerKind.PI, kp=1.0, ti_s=ti)
    digital = discretize_transfer_function(base, sample_rate_hz, method)
    c_at_fc = digital_frequency_response(digital, np.asarray([fc_hz], dtype=float))[0]
    gain = 1.0 / max(abs(plant_at_fc * c_at_fc), 1e-30)
    analog = design_controller(ControllerKind.PI, kp=gain, ti_s=ti)
    digital = discretize_transfer_function(analog, sample_rate_hz, method)
    return digital, {"kp": float(gain), "ti_s": float(ti)}


def _build_pif_for_target(
    plant_at_fc: complex,
    fc_hz: float,
    target_pm_deg: float,
    sample_rate_hz: float,
    method: DiscretizationMethod,
) -> tuple[DigitalTransferFunction, dict[str, float]]:
    fp = min(max(6.0 * fc_hz, 1.2 * fc_hz), 0.35 * sample_rate_hz)
    plant_phase = math.degrees(math.atan2(plant_at_fc.imag, plant_at_fc.real))
    desired_loop_phase = -180.0 + target_pm_deg
    required_total = _wrap_phase_deg(desired_loop_phase - plant_phase)
    lpf_phase = -math.degrees(math.atan(fc_hz / fp))
    required_pi = required_total - lpf_phase
    if not (-89.0 <= required_pi <= -0.05):
        raise ValueError(f"PIF cannot provide required controller phase {required_total:.3f} deg at this Fc")
    omega = 2.0 * math.pi * fc_hz
    ti = 1.0 / (omega * math.tan(math.radians(-required_pi)))
    base = design_controller(ControllerKind.PIF, kp=1.0, ti_s=ti, lpf_pole_hz=fp)
    digital = discretize_transfer_function(base, sample_rate_hz, method)
    c_at_fc = digital_frequency_response(digital, np.asarray([fc_hz], dtype=float))[0]
    gain = 1.0 / max(abs(plant_at_fc * c_at_fc), 1e-30)
    analog = design_controller(ControllerKind.PIF, kp=gain, ti_s=ti, lpf_pole_hz=fp)
    digital = discretize_transfer_function(analog, sample_rate_hz, method)
    return digital, {"kp": float(gain), "ti_s": float(ti), "lpf_pole_hz": float(fp)}


def _build_pid_for_target(
    plant_at_fc: complex,
    fc_hz: float,
    target_pm_deg: float,
    sample_rate_hz: float,
    method: DiscretizationMethod,
) -> tuple[DigitalTransferFunction, dict[str, float]]:
    plant_phase = math.degrees(math.atan2(plant_at_fc.imag, plant_at_fc.real))
    desired_loop_phase = -180.0 + target_pm_deg
    required = _wrap_phase_deg(desired_loop_phase - plant_phase)
    if not (-80.0 <= required <= 80.0):
        raise ValueError(f"PID heuristic cannot provide required controller phase {required:.3f} deg at this Fc")
    omega = 2.0 * math.pi * fc_hz
    if required <= 0.0:
        td = 0.0
        q = max(-math.tan(math.radians(required)), 1e-4)
        ti = 1.0 / (omega * q)
    else:
        # Keep the integral corner about one decade below Fc and solve the
        # derivative term for the requested phase lead.
        integral_term = 0.10
        ti = 1.0 / (omega * integral_term)
        td = max((math.tan(math.radians(required)) + integral_term) / omega, 0.0)
    base = design_controller(ControllerKind.PID, kp=1.0, ti_s=ti, td_s=td)
    digital = discretize_transfer_function(base, sample_rate_hz, method)
    c_at_fc = digital_frequency_response(digital, np.asarray([fc_hz], dtype=float))[0]
    gain = 1.0 / max(abs(plant_at_fc * c_at_fc), 1e-30)
    analog = design_controller(ControllerKind.PID, kp=gain, ti_s=ti, td_s=td)
    digital = discretize_transfer_function(analog, sample_rate_hz, method)
    return digital, {"kp": float(gain), "ti_s": float(ti), "td_s": float(td)}


def _lead_lag_pair(fc_hz: float, phase_deg: float) -> tuple[float, float]:
    limited = float(np.clip(phase_deg, -75.0, 75.0))
    if abs(limited) < 1e-6:
        return fc_hz / 1.05, fc_hz * 1.05
    s = math.sin(math.radians(abs(limited)))
    ratio = max((1.0 + s) / max(1.0 - s, 1e-9), 1.0001)
    root = math.sqrt(ratio)
    if limited > 0.0:
        return fc_hz / root, fc_hz * root
    return fc_hz * root, fc_hz / root


def _build_pz_for_target(
    kind: ControllerKind,
    plant_at_fc: complex,
    fc_hz: float,
    target_pm_deg: float,
    sample_rate_hz: float,
    method: DiscretizationMethod,
) -> tuple[DigitalTransferFunction, dict[str, float]]:
    count = 2 if kind == ControllerKind.TWO_P_TWO_Z else 3
    plant_phase = math.degrees(math.atan2(plant_at_fc.imag, plant_at_fc.real))
    desired_loop_phase = -180.0 + target_pm_deg
    required = _wrap_phase_deg(desired_loop_phase - plant_phase)
    if abs(required) > 75.0 * count:
        raise ValueError(f"{kind.value} cannot supply required phase shaping {required:.3f} deg at this Fc")

    pair_phase = required / count
    zeros: list[float] = []
    poles: list[float] = []
    for _ in range(count):
        fz, fp = _lead_lag_pair(fc_hz, pair_phase)
        zeros.append(float(np.clip(fz, 0.05, 0.45 * sample_rate_hz)))
        poles.append(float(np.clip(fp, 0.05, 0.45 * sample_rate_hz)))

    kwargs: dict[str, float] = {"gain": 1.0}
    for i, value in enumerate(zeros, 1):
        kwargs[f"fz{i}_hz"] = value
    for i, value in enumerate(poles, 1):
        kwargs[f"fp{i}_hz"] = value
    base = design_controller(kind, **kwargs)
    digital = discretize_transfer_function(base, sample_rate_hz, method)
    c_at_fc = digital_frequency_response(digital, np.asarray([fc_hz], dtype=float))[0]
    gain = 1.0 / max(abs(plant_at_fc * c_at_fc), 1e-30)
    kwargs["gain"] = gain
    analog = design_controller(kind, **kwargs)
    digital = discretize_transfer_function(analog, sample_rate_hz, method)
    parameters = dict(kwargs)
    return digital, parameters


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
        return _build_pz_for_target(kind, plant_at_fc, fc_hz, target_pm_deg, sample_rate_hz, method)
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
    if metrics.worst_gain_margin_db is not None and metrics.worst_gain_margin_db < min_gm_db:
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
        and (metrics.worst_gain_margin_db is None or metrics.worst_gain_margin_db >= min_gm_db)
        and math.isfinite(metrics.ms)
        and metrics.ms <= max_ms
    )
    note = "accepted" if accepted else "constraints not fully met"
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

    The requested crossover is attempted first.  If the selected controller
    structure cannot meet the phase-margin/robustness constraints there, the
    routine progressively lowers crossover frequency and retries.  No rational
    plant fit is required; all acceptance metrics are computed on the original
    FRA points.
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
    max_design_hz = min(float(f[-1]), 0.35 * fs)
    min_design_hz = max(float(f[0]) * 1.05, target_fc * float(min_crossover_ratio))
    if target_fc <= f[0] or target_fc > max_design_hz:
        raise ValueError(f"target crossover must be within {f[0]:.6g} .. {max_design_hz:.6g} Hz")
    if not (5.0 <= target_pm <= 85.0):
        raise ValueError("target phase margin must be within 5 .. 85 deg")

    # First point is exactly the requested Fc; subsequent points move lower.
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
        except Exception as exc:
            # Failed trials are intentionally skipped; lower Fc may make a
            # previously impossible controller phase requirement feasible.
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
        # The requested behavior is to preserve as much bandwidth as possible,
        # so select the highest accepted trial Fc first, then score.
        accepted.sort(key=lambda c: (-c.requested_crossover_hz, c.score))
        selected = accepted[0]
        fallback = selected.requested_crossover_hz < target_fc * 0.999
        msg = (
            "Target achieved at requested crossover."
            if not fallback
            else f"Requested Fc could not satisfy constraints; automatically reduced to {selected.requested_crossover_hz:.6g} Hz."
        )
        return AutoDesignResult(target_fc, target_pm, selected, tuple(candidates), fallback, "PASS", msg)

    # No strict candidate passed.  Return the best engineering compromise but
    # mark it REVIEW so the GUI never presents a mathematically weak design as
    # a successful one-click result.
    selected = min(candidates, key=lambda c: c.score)
    return AutoDesignResult(
        target_fc,
        target_pm,
        selected,
        tuple(candidates),
        selected.requested_crossover_hz < target_fc * 0.999,
        "REVIEW",
        "No candidate met every robustness constraint; returning the lowest-score candidate for manual review.",
    )


__all__ = ["AutoDesignCandidate", "AutoDesignResult", "auto_design_controller"]

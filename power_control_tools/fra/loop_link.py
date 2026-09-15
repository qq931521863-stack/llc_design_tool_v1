"""Connect an identified rational plant model to a toolbox controller.

Model Identification previously terminated in a report: the fitted
``RationalPlantModel`` could be plotted and, for a *loop* fit, turned into an
approximate step.  There was no way to attach a PI / PIF / Type-III / ... to a
fitted *plant* and evaluate the resulting loop.

This module closes that gap and keeps the same evidence boundary as the rest
of the FRA tool chain:

* the frequency-domain result is
  ``L(j w) = G_fit(j w) * H_ctrl(e^{j w T})`` evaluated on the identification
  band, limited to ``0.49 * Fs`` exactly like raw-FRA loop analysis;
* margins / Ms / Mt reuse :func:`analyze_loop_response`, so a linked loop is
  judged by the same code as a measured loop;
* the closed-loop step needs a *model*, so it is derived by zero-order-hold
  sampling the identified plant at the controller sample rate and closing the
  loop with the exact discrete controller.  It is therefore a model-derived
  prediction and is withheld when the fit confidence is LOW, the identified
  plant has right-half-plane poles, the linked loop fails its margin check, or
  the resulting closed loop is unstable.

Raw FRA remains the stability authority; nothing in this module upgrades a
model prediction into a hardware measurement.
"""

from __future__ import annotations

from dataclasses import dataclass
import math

import numpy as np
from numpy.typing import NDArray
from scipy import signal

from power_control_tools.fra.analysis import (
    LoopStabilityResult,
    analyze_loop_response,
    digital_frequency_response,
)
from power_control_tools.fra.fitting import (
    FitClosedLoopStepResult,
    RationalPlantModel,
    step_result_from_response,
)
from power_control_tools.models import DigitalTransferFunction, StabilityClass

# Fit confidence levels that are strong enough to authorize a model-derived
# time-domain prediction.
_STEP_ALLOWED_CONFIDENCE = ("GOOD", "FAIR")

STEP_WITHHELD_LOW_CONFIDENCE = "WITHHELD_LOW_FIT_CONFIDENCE"
STEP_WITHHELD_UNSTABLE_PLANT = "WITHHELD_PLANT_MODEL_NOT_STABLE"
STEP_WITHHELD_LOOP_FAIL = "WITHHELD_LOOP_MARGIN_FAIL"
STEP_WITHHELD_UNSTABLE_LOOP = "WITHHELD_CLOSED_LOOP_UNSTABLE"
STEP_DISABLED = "DISABLED"
STEP_OK = "OK"


def _descending_x_to_rad_s(coefficients: NDArray[np.float64] | np.ndarray, wref: float) -> NDArray[np.float64]:
    """Rewrite descending coefficients of ``p(x)`` as descending ``p(s/wref)``."""
    c = np.asarray(coefficients, dtype=float).reshape(-1)
    if c.size == 0:
        raise ValueError("polynomial coefficients cannot be empty")
    ascending = c[::-1]
    scaled = ascending / np.power(float(wref), np.arange(ascending.size, dtype=float))
    return np.asarray(scaled[::-1], dtype=float)


def plant_model_polynomials_rad_s(
    model: RationalPlantModel,
    *,
    pade_delay: bool = True,
) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    """Return descending-power numerator/denominator of the model in s (rad/s)."""
    wref = 2.0 * math.pi * float(model.reference_frequency_hz)
    if not math.isfinite(wref) or wref <= 0.0:
        raise ValueError("plant model reference frequency must be positive")
    num_x, den_x = model.normalized_polynomials(pade_delay=pade_delay)
    return _descending_x_to_rad_s(num_x, wref), _descending_x_to_rad_s(den_x, wref)


def discretize_plant_model(
    model: RationalPlantModel,
    sample_rate_hz: float,
    *,
    method: str = "zoh",
    extra_delay_samples: int = 0,
) -> DigitalTransferFunction:
    """Sample an identified continuous plant model at ``sample_rate_hz``.

    ``extra_delay_samples`` adds ``z^-k`` on top of whatever delay the fit
    already contains; it exists so a user can model a computation delay that
    was *not* embedded in the measured Equivalent Plant.  It must not be used
    to add delay a second time when the measurement already contains it.
    """
    fs = float(sample_rate_hz)
    if not math.isfinite(fs) or fs <= 0.0:
        raise ValueError("sample_rate_hz must be finite and positive")
    delay = int(extra_delay_samples)
    if delay < 0:
        raise ValueError("extra_delay_samples must be non-negative")
    num_s, den_s = plant_model_polynomials_rad_s(model, pade_delay=True)
    num_z, den_z, _ = signal.cont2discrete((num_s, den_s), 1.0 / fs, method=str(method))
    num_z = np.asarray(num_z, dtype=float).reshape(-1)
    den_z = np.asarray(den_z, dtype=float).reshape(-1)
    if den_z.size == 0 or abs(den_z[0]) < 1e-30:
        raise ValueError("plant discretization produced a zero leading denominator coefficient")
    num_z = num_z / den_z[0]
    den_z = den_z / den_z[0]
    pad = den_z.size - num_z.size
    if pad < 0:
        raise ValueError("identified plant discretizes to an improper transfer function")
    # scipy returns descending powers of z; this project stores H(z) as
    # ascending powers of z^-1, which is a z^(m-p) shift plus a reversal.
    b = np.concatenate([np.zeros(pad), num_z])
    if delay:
        b = np.concatenate([np.zeros(delay), b])
    return DigitalTransferFunction(
        tuple(float(v) for v in b),
        tuple(float(v) for v in den_z),
        fs,
        f"Identified plant ({str(method)} @ {fs:g} Hz)",
        "identified plant model",
    ).normalized()


def _z_poles_to_equivalent_rad_s(poles: NDArray[np.complex128], sample_rate_hz: float) -> NDArray[np.complex128]:
    """Map z-plane poles to their principal-branch continuous equivalents."""
    z = np.asarray(poles, dtype=complex).reshape(-1)
    if z.size == 0:
        return np.asarray([], dtype=complex)
    magnitude = np.maximum(np.abs(z), 1e-12)
    return (np.log(magnitude) + 1j * np.angle(z)) * float(sample_rate_hz)


def closed_loop_step_from_plant_and_controller(
    plant_model: RationalPlantModel,
    controller: DigitalTransferFunction,
    *,
    samples: int = 1500,
    settling_band: float = 0.02,
    method: str = "zoh",
    extra_delay_samples: int = 0,
) -> FitClosedLoopStepResult:
    """Closed-loop step of ``H_ctrl(z)`` driving the ZOH-sampled identified plant."""
    plant = discretize_plant_model(
        plant_model,
        controller.sample_rate_hz,
        method=method,
        extra_delay_samples=extra_delay_samples,
    )
    c = controller.normalized()
    loop_num = np.polymul(np.asarray(c.b, dtype=float), np.asarray(plant.b, dtype=float))
    loop_den = np.polymul(np.asarray(c.a, dtype=float), np.asarray(plant.a, dtype=float))

    size = max(loop_den.size, loop_num.size)
    closed_den = np.zeros(size, dtype=float)
    closed_den[: loop_den.size] += loop_den
    closed_den[: loop_num.size] += loop_num  # 1 + L(z) in ascending z^-1 powers
    closed_num = loop_num

    descending = closed_den
    trimmed = np.trim_zeros(descending, "f")
    poles_z = np.roots(trimmed).astype(complex) if trimmed.size > 1 else np.asarray([], dtype=complex)
    poles_s = _z_poles_to_equivalent_rad_s(poles_z, controller.sample_rate_hz)
    stable = bool(poles_z.size == 0 or np.all(np.abs(poles_z) < 1.0 - 1e-9))
    if not stable:
        return FitClosedLoopStepResult(
            np.asarray([], dtype=float),
            np.asarray([], dtype=float),
            False,
            poles_s,
            math.nan,
            math.nan,
            math.inf,
            None,
            None,
            "Identified-plant closed loop has a pole on or outside the unit circle; step response is not rendered.",
        )

    dt = 1.0 / float(controller.sample_rate_hz)
    # ``closed_num``/``closed_den`` are ascending powers of z^-1, which is the
    # same coefficient order as scipy's descending powers of z for the
    # denominator.  The numerator additionally needs the degree-difference
    # factor z^(n-m), i.e. trailing zeros up to the denominator length.
    padded_num = closed_num
    if closed_den.size > closed_num.size:
        padded_num = np.concatenate([closed_num, np.zeros(closed_den.size - closed_num.size, dtype=float)])
    t, y = signal.dstep((padded_num, closed_den, dt), n=max(int(samples), 200))
    t = np.asarray(t, dtype=float).reshape(-1)
    y = np.asarray(y[0], dtype=float).reshape(-1)
    final = float(np.sum(closed_num) / np.sum(closed_den))
    return step_result_from_response(
        t,
        y,
        poles_rad_s=poles_s,
        final_value=final,
        note=(
            "Model-derived step: exact digital controller H(z) driving the identified plant sampled with "
            f"{method.upper()} at {controller.sample_rate_hz:.8g} Hz. Validate against hardware before release use."
        ),
        settling_band=settling_band,
    )


@dataclass(frozen=True)
class PlantControllerLinkResult:
    """Frequency-domain and optional time-domain result of G_fit x H_ctrl."""

    frequency_hz: NDArray[np.float64]
    plant_response: NDArray[np.complex128]
    controller_response: NDArray[np.complex128]
    loop_response: NDArray[np.complex128]
    metrics: LoopStabilityResult
    controller: DigitalTransferFunction
    analysis_limit_hz: float
    step: FitClosedLoopStepResult | None
    step_status: str
    step_note: str


def _plant_model_is_stable(model: RationalPlantModel) -> bool:
    poles = model.poles_rad_s
    if poles.size == 0:
        return True
    return bool(np.all(np.real(poles) < 1e-9))


def link_plant_model_with_controller(
    plant_model: RationalPlantModel,
    controller: DigitalTransferFunction,
    *,
    f_min_hz: float,
    f_max_hz: float,
    points: int = 800,
    nyquist_fraction: float = 0.49,
    include_step: bool = True,
    step_samples: int = 1500,
    fit_confidence: str | None = None,
    extra_delay_samples: int = 0,
) -> PlantControllerLinkResult:
    """Evaluate ``L = G_fit * H_ctrl`` on the identification band.

    ``f_min_hz``/``f_max_hz`` are the band that was actually used for the
    rational fit.  They are required arguments because a model extrapolated
    outside its own fit band must not be presented as validated data.
    """
    fs = float(controller.sample_rate_hz)
    if not math.isfinite(fs) or fs <= 0.0:
        raise ValueError("controller sample rate must be finite and positive")
    low = float(f_min_hz)
    high = float(f_max_hz)
    if not math.isfinite(low) or not math.isfinite(high) or low <= 0.0 or high <= low:
        raise ValueError("fit band must satisfy 0 < f_min < f_max")
    limit = min(high, float(nyquist_fraction) * fs)
    if limit <= low * 1.0000001:
        raise ValueError(
            "identification band does not contain two usable points below "
            f"{float(nyquist_fraction):.2f} x Fs ({float(nyquist_fraction) * fs:.8g} Hz)"
        )

    f = np.geomspace(low, limit, max(int(points), 4))
    delay = int(extra_delay_samples)
    if delay < 0:
        raise ValueError("extra_delay_samples must be non-negative")
    plant_response = plant_model.frequency_response(f)
    controller_response = digital_frequency_response(controller, f)
    if delay:
        # Keep the frequency-domain loop consistent with the discrete step: an
        # extra computation delay must shift the phase of the analysis too.
        controller_response = controller_response * np.exp(-1j * 2.0 * np.pi * f * delay / fs)
    loop_response = plant_response * controller_response
    metrics = analyze_loop_response(f, loop_response)

    step: FitClosedLoopStepResult | None = None
    status = STEP_DISABLED
    note = "Step computation was not requested."
    if include_step:
        if fit_confidence is not None and str(fit_confidence).upper() not in _STEP_ALLOWED_CONFIDENCE:
            status = STEP_WITHHELD_LOW_CONFIDENCE
            note = (
                f"Rational fit residual confidence is {fit_confidence}; a model-derived step is not authorized. "
                "Re-fit with a wider band, a higher order, or a tighter control focus."
            )
        elif not _plant_model_is_stable(plant_model):
            status = STEP_WITHHELD_UNSTABLE_PLANT
            note = "Identified plant has right-half-plane poles; the step prediction is withheld."
        elif metrics.status == "FAIL":
            status = STEP_WITHHELD_LOOP_FAIL
            note = "Linked open loop fails its margin check; the step prediction is withheld."
        else:
            candidate = closed_loop_step_from_plant_and_controller(
                plant_model,
                controller,
                samples=step_samples,
                extra_delay_samples=extra_delay_samples,
            )
            if candidate.stable:
                step = candidate
                status = STEP_OK
                note = candidate.note
            else:
                status = STEP_WITHHELD_UNSTABLE_LOOP
                note = candidate.note

    return PlantControllerLinkResult(
        f,
        np.asarray(plant_response, dtype=complex),
        np.asarray(controller_response, dtype=complex),
        np.asarray(loop_response, dtype=complex),
        metrics,
        controller.normalized(),
        limit,
        step,
        status,
        note,
    )


__all__ = [
    "PlantControllerLinkResult",
    "STEP_DISABLED",
    "STEP_OK",
    "STEP_WITHHELD_LOW_CONFIDENCE",
    "STEP_WITHHELD_LOOP_FAIL",
    "STEP_WITHHELD_UNSTABLE_LOOP",
    "STEP_WITHHELD_UNSTABLE_PLANT",
    "closed_loop_step_from_plant_and_controller",
    "discretize_plant_model",
    "link_plant_model_with_controller",
    "plant_model_polynomials_rad_s",
]

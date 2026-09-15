from __future__ import annotations

from dataclasses import dataclass
import math

import numpy as np
from numpy.polynomial import polynomial as poly
from numpy.typing import NDArray
from scipy import signal
from scipy.optimize import least_squares

from power_control_tools.fra.analysis import LoopStabilityResult, analyze_loop_response


@dataclass(frozen=True)
class RationalPlantModel:
    """Stable real-coefficient rational FRA approximation plus pure delay.

    The polynomial variable is normalized as x=s/w_ref to improve numerical
    conditioning.  Fitted poles/zeros are an engineering approximation of the
    measured complex response, not physical component identification.
    """

    numerator_coefficients: tuple[float, ...]  # ascending powers of x
    real_pole_hz: tuple[float, ...]
    complex_pole_pairs: tuple[tuple[float, float], ...]  # (fn_hz, damping)
    reference_frequency_hz: float
    delay_s: float = 0.0

    @property
    def order(self) -> int:
        return len(self.real_pole_hz) + 2 * len(self.complex_pole_pairs)

    def frequency_response(self, frequency_hz: NDArray[np.float64] | np.ndarray) -> NDArray[np.complex128]:
        f = np.asarray(frequency_hz, dtype=float)
        if np.any(f < 0.0):
            raise ValueError("frequency must be non-negative")
        wref = 2.0 * math.pi * float(self.reference_frequency_hz)
        x = 1j * 2.0 * math.pi * f / wref
        numerator = np.zeros_like(x, dtype=complex)
        for k, coeff in enumerate(self.numerator_coefficients):
            numerator += float(coeff) * np.power(x, k)
        denominator = np.ones_like(x, dtype=complex)
        for pole_hz in self.real_pole_hz:
            denominator *= 1.0 + 1j * f / float(pole_hz)
        for natural_hz, damping in self.complex_pole_pairs:
            q = 1j * f / float(natural_hz)
            denominator *= 1.0 + 2.0 * float(damping) * q + q * q
        delay = np.exp(-1j * 2.0 * np.pi * f * float(self.delay_s))
        return numerator / denominator * delay

    def normalized_polynomials(self, *, pade_delay: bool = True) -> tuple[np.ndarray, np.ndarray]:
        """Return descending polynomial coefficients in x=s/w_ref.

        Pure delay is represented by first-order Pade only for optional
        time-domain/root calculations.  Tiny fitted delays are ignored so
        optimizer noise cannot create an artificial extremely-fast pole/zero.
        """
        wref = 2.0 * math.pi * float(self.reference_frequency_hz)
        den_asc = np.asarray([1.0], dtype=float)
        for pole_hz in self.real_pole_hz:
            wp = 2.0 * math.pi * float(pole_hz)
            den_asc = poly.polymul(den_asc, np.asarray([1.0, wref / wp], dtype=float))
        for natural_hz, damping in self.complex_pole_pairs:
            wn = 2.0 * math.pi * float(natural_hz)
            ratio = wref / wn
            den_asc = poly.polymul(
                den_asc,
                np.asarray([1.0, 2.0 * float(damping) * ratio, ratio * ratio], dtype=float),
            )
        num_asc = np.asarray(self.numerator_coefficients, dtype=float)
        tau_normalized = wref * float(self.delay_s)
        if pade_delay and self.delay_s > 0.0 and abs(tau_normalized) > 1e-9:
            num_asc = poly.polymul(num_asc, np.asarray([1.0, -0.5 * tau_normalized], dtype=float))
            den_asc = poly.polymul(den_asc, np.asarray([1.0, 0.5 * tau_normalized], dtype=float))
        return np.asarray(num_asc[::-1], dtype=float), np.asarray(den_asc[::-1], dtype=float)

    @property
    def poles_rad_s(self) -> NDArray[np.complex128]:
        _, den = self.normalized_polynomials(pade_delay=False)
        roots_x = np.roots(den).astype(complex) if len(den) > 1 else np.asarray([], dtype=complex)
        return roots_x * (2.0 * math.pi * self.reference_frequency_hz)

    @property
    def zeros_rad_s(self) -> NDArray[np.complex128]:
        num, _ = self.normalized_polynomials(pade_delay=False)
        trimmed = np.trim_zeros(np.asarray(num, dtype=float), "f")
        roots_x = np.roots(trimmed).astype(complex) if len(trimmed) > 1 else np.asarray([], dtype=complex)
        return roots_x * (2.0 * math.pi * self.reference_frequency_hz)


@dataclass(frozen=True)
class FRAFitMetrics:
    magnitude_rms_db: float
    magnitude_max_db: float
    phase_rms_deg: float
    phase_max_deg: float
    focus_magnitude_rms_db: float | None
    focus_phase_rms_deg: float | None
    confidence: str


@dataclass(frozen=True)
class FRAFitResult:
    model: RationalPlantModel
    fitted_response: NDArray[np.complex128]
    metrics: FRAFitMetrics
    requested_max_order: int
    selected_order: int
    fit_target: str
    note: str


@dataclass(frozen=True)
class FitClosedLoopStepResult:
    time_s: NDArray[np.float64]
    response: NDArray[np.float64]
    stable: bool
    poles_rad_s: NDArray[np.complex128]
    final_value: float
    peak_value: float
    overshoot_percent: float
    rise_time_s: float | None
    settling_time_s: float | None
    note: str


@dataclass(frozen=True)
class FRAFitLoopValidation:
    passed: bool
    status: str
    raw_metrics: LoopStabilityResult
    fitted_metrics: LoopStabilityResult
    crossover_count_match: bool
    crossover_error_percent: float | None
    phase_margin_error_deg: float | None
    gain_margin_error_db: float | None
    fitted_closed_loop_stable: bool
    time_domain_coverage_ok: bool
    low_frequency_ratio_to_fc: float | None
    high_frequency_ratio_to_fc: float | None
    note: str


# Minimum measurement-band coverage required before a fitted model may be
# used for a closed-loop step prediction (FRA deep audit 7.3).  A local fit
# around crossover does not constrain DC or high-frequency behaviour, so the
# step would otherwise be an extrapolation.  These are the single source of
# truth for both the fitted-loop and the identified-plant step paths.
DEFAULT_MIN_LOW_FREQUENCY_RATIO_TO_FC = 10.0
DEFAULT_MIN_HIGH_FREQUENCY_RATIO_TO_FC = 5.0


def _fit_metrics(
    measured: np.ndarray,
    fitted: np.ndarray,
    frequency_hz: np.ndarray,
    focus_hz: float | None,
) -> FRAFitMetrics:
    ratio = fitted / np.where(np.abs(measured) > 1e-300, measured, 1e-300 + 0j)
    mag_error = 20.0 * np.log10(np.maximum(np.abs(ratio), 1e-300))
    phase_error = np.angle(ratio, deg=True)
    mag_rms = float(np.sqrt(np.mean(np.square(mag_error))))
    mag_max = float(np.max(np.abs(mag_error)))
    phase_rms = float(np.sqrt(np.mean(np.square(phase_error))))
    phase_max = float(np.max(np.abs(phase_error)))

    focus_mag = focus_phase = None
    if focus_hz is not None and focus_hz > 0.0:
        lf = np.log10(frequency_hz)
        center = math.log10(float(focus_hz))
        mask = np.abs(lf - center) <= 0.5
        if int(np.count_nonzero(mask)) >= 3:
            focus_mag = float(np.sqrt(np.mean(np.square(mag_error[mask]))))
            focus_phase = float(np.sqrt(np.mean(np.square(phase_error[mask]))))

    if (
        mag_rms <= 1.0
        and phase_rms <= 5.0
        and (focus_mag is None or focus_mag <= 0.75)
        and (focus_phase is None or focus_phase <= 4.0)
    ):
        confidence = "GOOD"
    elif (
        mag_rms <= 3.0
        and phase_rms <= 15.0
        and (focus_mag is None or focus_mag <= 2.0)
        and (focus_phase is None or focus_phase <= 10.0)
    ):
        confidence = "FAIR"
    else:
        confidence = "LOW"
    return FRAFitMetrics(mag_rms, mag_max, phase_rms, phase_max, focus_mag, focus_phase, confidence)


def _downsample_for_fit(f: np.ndarray, h: np.ndarray, max_points: int = 500) -> tuple[np.ndarray, np.ndarray]:
    if len(f) <= max_points:
        return f, h
    index = np.unique(np.round(np.linspace(0, len(f) - 1, max_points)).astype(int))
    return f[index], h[index]


def _fit_structure(
    frequency_hz: np.ndarray,
    response: np.ndarray,
    *,
    order: int,
    complex_pairs: int,
    focus_hz: float | None,
    fit_delay: bool,
    max_nfev: int,
) -> tuple[RationalPlantModel, np.ndarray, float]:
    f, h = _downsample_for_fit(frequency_hz, response)
    omega = 2.0 * np.pi * f
    ref_hz = math.sqrt(float(f[0]) * float(f[-1]))
    wref = 2.0 * math.pi * ref_hz
    x = 1j * omega / wref
    scale = np.maximum(np.abs(h), 1e-12)
    weights = np.ones_like(f, dtype=float)
    if focus_hz is not None and focus_hz > 0.0:
        weights *= 1.0 + 3.0 * np.exp(-0.5 * np.square((np.log10(f) - math.log10(focus_hz)) / 0.60))

    pair_count = int(complex_pairs)
    real_count = int(order) - 2 * pair_count
    if real_count < 0:
        raise ValueError("complex-pair count exceeds rational order")
    numerator_degree = max(int(order) - 1, 0)

    feature_count = real_count + pair_count
    if feature_count:
        centers = np.linspace(math.log10(float(f[0])), math.log10(float(f[-1])), feature_count + 2)[1:-1]
    else:
        centers = np.asarray([], dtype=float)

    low_log = math.log10(float(f[0]) / 10.0)
    high_log = math.log10(float(f[-1]) * 10.0)
    lower_base = np.r_[
        np.full(real_count + pair_count, low_log),
        np.full(pair_count, math.log10(0.03)),
    ]
    upper_base = np.r_[
        np.full(real_count + pair_count, high_log),
        np.full(pair_count, math.log10(2.0)),
    ]

    phase = np.unwrap(np.angle(h))
    hi_start = max(0, int(0.70 * len(f)))
    if len(f) - hi_start >= 2:
        slope = float(np.polyfit(omega[hi_start:], phase[hi_start:], 1)[0])
    else:
        slope = 0.0
    max_delay = 5.0 / float(f[-1])
    estimated_delay = float(np.clip(-slope, 0.0, 0.80 * max_delay))

    def unpack(v: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray, float]:
        i = 0
        real_hz = np.power(10.0, v[i:i + real_count]); i += real_count
        pair_hz = np.power(10.0, v[i:i + pair_count]); i += pair_count
        damping = np.power(10.0, v[i:i + pair_count]); i += pair_count
        delay_s = float(v[i]) if fit_delay else 0.0
        return real_hz, pair_hz, damping, delay_s

    def solve_numerator(v: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        real_hz, pair_hz, damping, delay_s = unpack(v)
        den = np.ones_like(f, dtype=complex)
        for pole_hz in real_hz:
            den *= 1.0 + 1j * f / pole_hz
        for natural_hz, zeta in zip(pair_hz, damping):
            q = 1j * f / natural_hz
            den *= 1.0 + 2.0 * zeta * q + q * q
        delay = np.exp(-1j * omega * delay_s)
        basis = np.column_stack([np.power(x, k) / den * delay for k in range(numerator_degree + 1)])
        w = weights / scale
        matrix = np.vstack([(basis * w[:, None]).real, (basis * w[:, None]).imag])
        target = np.r_[(h * w).real, (h * w).imag]
        coeff, *_ = np.linalg.lstsq(matrix, target, rcond=None)
        return coeff.astype(float), basis @ coeff

    def residual(v: np.ndarray) -> np.ndarray:
        _, fitted = solve_numerator(v)
        err = (fitted - h) / scale * weights
        return np.r_[err.real, err.imag]

    starts: list[np.ndarray] = []
    for shift, zeta0, use_delay_estimate in (
        (0.0, 0.50, False),
        (-0.20, 0.30, True),
        (+0.20, 0.80, True),
    ):
        shifted = np.clip(centers + shift, low_log, high_log)
        init_real = shifted[:real_count]
        init_pair = shifted[real_count:real_count + pair_count]
        init_zeta = np.full(pair_count, math.log10(zeta0), dtype=float)
        base = np.r_[init_real, init_pair, init_zeta]
        delay0 = estimated_delay if (fit_delay and use_delay_estimate) else 0.0
        starts.append(np.r_[base, delay0] if fit_delay else base.copy())

    lower = np.r_[lower_base, 0.0] if fit_delay else lower_base
    upper = np.r_[upper_base, max_delay] if fit_delay else upper_base
    best: tuple[float, np.ndarray, np.ndarray] | None = None
    for v0 in starts:
        result = least_squares(
            residual,
            v0,
            bounds=(lower, upper),
            max_nfev=max(int(max_nfev), 40),
            loss="soft_l1",
            f_scale=1.0,
        )
        coeff, fitted = solve_numerator(result.x)
        cost = float(np.mean(np.square(np.abs((fitted - h) / scale))))
        if best is None or cost < best[0]:
            best = (cost, result.x.copy(), coeff.copy())

    assert best is not None
    cost, vector, coeff = best
    real_hz, pair_hz, damping, delay_s = unpack(vector)
    if delay_s > 0.0 and 2.0 * math.pi * float(f[-1]) * delay_s < math.radians(0.1):
        delay_s = 0.0
    real_sorted = tuple(float(v) for v in np.sort(real_hz))
    pairs_sorted = tuple(sorted(((float(fn), float(z)) for fn, z in zip(pair_hz, damping)), key=lambda item: item[0]))
    model = RationalPlantModel(
        tuple(float(v) for v in coeff),
        real_sorted,
        pairs_sorted,
        float(ref_hz),
        float(delay_s),
    )
    full_fit = model.frequency_response(frequency_hz)
    return model, full_fit, cost


def fit_rational_frequency_response(
    frequency_hz: NDArray[np.float64] | np.ndarray,
    response: NDArray[np.complex128] | np.ndarray,
    *,
    max_poles: int = 5,
    min_poles: int = 1,
    focus_hz: float | None = None,
    fit_delay: bool = True,
    max_nfev: int = 220,
    fit_target: str = "Equivalent Plant",
) -> FRAFitResult:
    """Fit a stable low-order rational approximation to complex FRA points.

    This V2 implementation is a constrained nonlinear variable-projection
    fitter.  It is deliberately labelled rational fit, not Vector Fitting.
    Orders 1..5 and real/complex-conjugate stable pole structures are tried.
    Raw FRA remains the authority for crossover/margin decisions.
    """
    f = np.asarray(frequency_hz, dtype=float).reshape(-1)
    h = np.asarray(response, dtype=complex).reshape(-1)
    if f.size < 8 or f.size != h.size:
        raise ValueError("FRA fitting requires matching arrays with at least eight points")
    if np.any(f <= 0.0) or np.any(np.diff(f) <= 0.0):
        raise ValueError("FRA fitting frequencies must be strictly increasing and positive")
    if not np.all(np.isfinite(h.real)) or not np.all(np.isfinite(h.imag)):
        raise ValueError("FRA fitting response contains NaN or infinite values")
    max_order = int(max_poles)
    min_order = int(min_poles)
    if min_order < 1 or max_order < min_order or max_order > 5:
        raise ValueError("FRA rational fit supports 1..5 poles")

    best_result: FRAFitResult | None = None
    best_rank = math.inf
    for order in range(min_order, max_order + 1):
        order_best: FRAFitResult | None = None
        order_rank = math.inf
        for pair_count in range(order // 2 + 1):
            try:
                model, fitted, _ = _fit_structure(
                    f,
                    h,
                    order=order,
                    complex_pairs=pair_count,
                    focus_hz=focus_hz,
                    fit_delay=bool(fit_delay),
                    max_nfev=max_nfev,
                )
                metrics = _fit_metrics(h, fitted, f, focus_hz)
                rank = metrics.magnitude_rms_db + 0.20 * metrics.phase_rms_deg
                if metrics.focus_magnitude_rms_db is not None:
                    rank += 0.5 * metrics.focus_magnitude_rms_db
                if metrics.focus_phase_rms_deg is not None:
                    rank += 0.10 * metrics.focus_phase_rms_deg
                result = FRAFitResult(
                    model,
                    fitted,
                    metrics,
                    max_order,
                    order,
                    str(fit_target),
                    "Constrained stable rational approximation; raw FRA remains the stability authority and fitted poles/zeros are not physical-component identification.",
                )
                if rank < order_rank:
                    order_rank = rank
                    order_best = result
            except Exception:
                continue
        if order_best is None:
            continue
        if order_best.metrics.confidence == "GOOD":
            return order_best
        if order_rank < best_rank:
            best_rank = order_rank
            best_result = order_best

    if best_result is None:
        raise ValueError("rational FRA fitting failed for every candidate order")
    return best_result


def step_result_from_response(
    t_s: NDArray[np.float64] | np.ndarray,
    y: NDArray[np.float64] | np.ndarray,
    *,
    poles_rad_s: NDArray[np.complex128] | np.ndarray,
    final_value: float,
    note: str,
    settling_band: float = 0.02,
) -> FitClosedLoopStepResult:
    """Build step statistics from an already-simulated response.

    Shared by the fitted-open-loop path and by the plant-model x controller
    link so both report overshoot/rise/settling with identical definitions.
    """
    t = np.asarray(t_s, dtype=float).reshape(-1)
    values = np.asarray(y, dtype=float).reshape(-1)
    final = float(final_value)
    peak = float(np.max(values)) if final >= 0.0 else float(np.min(values))
    if abs(final) > 1e-12:
        overshoot = max(0.0, (peak - final) / abs(final) * 100.0) if final >= 0.0 else max(0.0, (final - peak) / abs(final) * 100.0)
    else:
        overshoot = 0.0

    rise = None
    if abs(final) > 1e-12:
        low, high = 0.10 * final, 0.90 * final
        if final >= 0.0:
            i10 = np.flatnonzero(values >= low)
            i90 = np.flatnonzero(values >= high)
        else:
            i10 = np.flatnonzero(values <= low)
            i90 = np.flatnonzero(values <= high)
        if i10.size and i90.size and i90[0] >= i10[0]:
            rise = float(t[i90[0]] - t[i10[0]])

    scale = max(abs(final), 1e-12)
    outside = np.flatnonzero(np.abs(values - final) > float(settling_band) * scale)
    settling = None
    if outside.size:
        index = int(outside[-1] + 1)
        if index < len(t):
            settling = float(t[index])
    else:
        settling = 0.0

    return FitClosedLoopStepResult(
        t,
        values,
        True,
        np.asarray(poles_rad_s, dtype=complex),
        final,
        peak,
        float(overshoot),
        rise,
        settling,
        note,
    )


def _trim_tf(num: np.ndarray, den: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    num_t = np.trim_zeros(np.asarray(num, dtype=float), "f")
    den_t = np.trim_zeros(np.asarray(den, dtype=float), "f")
    if num_t.size == 0:
        num_t = np.asarray([0.0], dtype=float)
    if den_t.size == 0 or abs(float(den_t[0])) < 1e-30:
        raise ValueError("closed-loop polynomial denominator is singular")
    scale = float(den_t[0])
    return num_t / scale, den_t / scale


def closed_loop_step_from_fitted_loop(
    loop_model: RationalPlantModel,
    *,
    samples: int = 1200,
    settling_band: float = 0.02,
) -> FitClosedLoopStepResult:
    """Approximate closed-loop step from a fitted *open-loop* model.

    Delay is represented by first-order Pade only when numerically meaningful.
    This output is fit-derived and must not replace measured transient
    validation.
    """
    num, den = loop_model.normalized_polynomials(pade_delay=True)
    length = max(len(num), len(den))
    num_pad = np.pad(num, (length - len(num), 0))
    den_pad = np.pad(den, (length - len(den), 0))
    closed_den_raw = den_pad + num_pad
    closed_num_raw = num_pad.copy()
    closed_num, closed_den = _trim_tf(closed_num_raw, closed_den_raw)
    poles_x = np.roots(closed_den).astype(complex) if len(closed_den) > 1 else np.asarray([], dtype=complex)
    wref = 2.0 * math.pi * loop_model.reference_frequency_hz
    poles_s = poles_x * wref
    stable = bool(not poles_s.size or np.all(np.real(poles_s) < -1e-9))
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
            "Fitted closed loop has a right-half-plane pole; step response is not rendered.",
        )

    if poles_x.size:
        rates = -np.real(poles_x[np.real(poles_x) < -1e-9])
        tau_norm = 1.0 / max(float(np.min(rates)) if rates.size else 1.0, 1e-6)
    else:
        tau_norm = 1.0
    t_norm = np.linspace(0.0, min(max(8.0 * tau_norm, 10.0), 2e5), max(int(samples), 200))
    _, y = signal.step((closed_num, closed_den), T=t_norm)
    y = np.asarray(y, dtype=float).reshape(-1)
    t_s = np.asarray(t_norm / wref, dtype=float)

    if abs(float(closed_den[-1])) > 1e-18:
        final = float(closed_num[-1] / closed_den[-1])
    else:
        final = float(y[-1])

    return step_result_from_response(
        t_s,
        y,
        poles_rad_s=poles_s,
        final_value=final,
        note="Approximate step from validated rational open-loop fit + first-order Pade delay; validate against hardware before release use.",
        settling_band=settling_band,
    )


def validate_fitted_open_loop(
    frequency_hz: NDArray[np.float64] | np.ndarray,
    measured_loop: NDArray[np.complex128] | np.ndarray,
    fit_result: FRAFitResult,
    *,
    crossover_tolerance_percent: float = 8.0,
    phase_margin_tolerance_deg: float = 5.0,
    gain_margin_tolerance_db: float = 4.0,
    min_low_frequency_ratio_to_fc: float = DEFAULT_MIN_LOW_FREQUENCY_RATIO_TO_FC,
    min_high_frequency_ratio_to_fc: float = DEFAULT_MIN_HIGH_FREQUENCY_RATIO_TO_FC,
) -> FRAFitLoopValidation:
    """Check whether an open-loop fit preserves control-critical behavior.

    Low Bode RMS error alone is not enough to authorize a time-domain step.
    The fit must preserve gain-crossing count, Fc/PM/GM, closed-loop pole
    stability, and must span enough data below/above crossover that a step is
    not inferred from a narrow local fit with unconstrained DC/HF behavior.
    """
    f = np.asarray(frequency_hz, dtype=float).reshape(-1)
    measured = np.asarray(measured_loop, dtype=complex).reshape(-1)
    fitted = np.asarray(fit_result.fitted_response, dtype=complex).reshape(-1)
    if f.size != measured.size or f.size != fitted.size:
        raise ValueError("fit validation requires matching frequency/measured/fitted arrays")

    raw = analyze_loop_response(f, measured)
    fit = analyze_loop_response(f, fitted)
    count_match = len(raw.gain_crossovers) == len(fit.gain_crossovers)

    fc_error = None
    if raw.main_crossover_hz is not None and fit.main_crossover_hz is not None:
        fc_error = abs(float(fit.main_crossover_hz) / float(raw.main_crossover_hz) - 1.0) * 100.0

    pm_error = None
    if raw.phase_margin_deg is not None and fit.phase_margin_deg is not None:
        pm_error = abs(float(fit.phase_margin_deg) - float(raw.phase_margin_deg))

    gm_error = None
    gm_evidence_ok = True
    if raw.gain_margin_db is not None:
        if fit.gain_margin_db is None:
            gm_evidence_ok = False
        else:
            gm_error = abs(float(fit.gain_margin_db) - float(raw.gain_margin_db))

    low_ratio = high_ratio = None
    coverage_ok = False
    if raw.main_crossover_hz is not None and raw.main_crossover_hz > 0.0:
        fc = float(raw.main_crossover_hz)
        low_ratio = fc / float(f[0])
        high_ratio = float(f[-1]) / fc
        coverage_ok = (
            low_ratio >= float(min_low_frequency_ratio_to_fc)
            and high_ratio >= float(min_high_frequency_ratio_to_fc)
        )

    step_probe = closed_loop_step_from_fitted_loop(fit_result.model, samples=240)
    closed_stable = step_probe.stable

    passed = (
        fit_result.metrics.confidence != "LOW"
        and count_match
        and fc_error is not None
        and fc_error <= float(crossover_tolerance_percent)
        and pm_error is not None
        and pm_error <= float(phase_margin_tolerance_deg)
        and gm_evidence_ok
        and (gm_error is None or gm_error <= float(gain_margin_tolerance_db))
        and closed_stable
        and coverage_ok
    )
    status = "PASS" if passed else "REVIEW"
    note_parts: list[str] = []
    if fit_result.metrics.confidence == "LOW":
        note_parts.append("Bode residual confidence is LOW")
    if not count_match:
        note_parts.append("gain-crossover count changed")
    if fc_error is None or fc_error > float(crossover_tolerance_percent):
        note_parts.append("crossover mismatch")
    if pm_error is None or pm_error > float(phase_margin_tolerance_deg):
        note_parts.append("phase-margin mismatch")
    if not gm_evidence_ok or (gm_error is not None and gm_error > float(gain_margin_tolerance_db)):
        note_parts.append("gain-margin evidence/mismatch")
    if not closed_stable:
        note_parts.append("fitted closed loop is unstable")
    if not coverage_ok:
        note_parts.append(
            f"insufficient step-model bandwidth coverage (Fc/Fmin={low_ratio if low_ratio is not None else float('nan'):.3g}, "
            f"Fmax/Fc={high_ratio if high_ratio is not None else float('nan'):.3g})"
        )
    note = "control-critical fit and time-domain coverage preserved" if passed else "; ".join(note_parts)

    return FRAFitLoopValidation(
        bool(passed),
        status,
        raw,
        fit,
        bool(count_match),
        fc_error,
        pm_error,
        gm_error,
        bool(closed_stable),
        bool(coverage_ok),
        low_ratio,
        high_ratio,
        note,
    )


__all__ = [
    "DEFAULT_MIN_HIGH_FREQUENCY_RATIO_TO_FC",
    "DEFAULT_MIN_LOW_FREQUENCY_RATIO_TO_FC",
    "RationalPlantModel",
    "FRAFitMetrics",
    "FRAFitResult",
    "FitClosedLoopStepResult",
    "FRAFitLoopValidation",
    "fit_rational_frequency_response",
    "closed_loop_step_from_fitted_loop",
    "step_result_from_response",
    "validate_fitted_open_loop",
]

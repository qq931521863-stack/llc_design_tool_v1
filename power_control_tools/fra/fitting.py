from __future__ import annotations

from dataclasses import dataclass
import math

import numpy as np
from numpy.polynomial import polynomial as poly
from numpy.typing import NDArray
from scipy import signal
from scipy.optimize import least_squares


@dataclass(frozen=True)
class RationalPlantModel:
    """Stable real-coefficient rational FRA model plus optional pure delay.

    The rational part is represented in normalized frequency ``x=s/w_ref`` to
    keep polynomial conditioning reasonable across wide FRA frequency ranges.
    ``numerator_coefficients`` are ascending powers of x.  The denominator is
    factored into stable first-order real poles and stable second-order pole
    pairs.  This is an engineering identification model, not a claim about the
    physical component topology.
    """

    numerator_coefficients: tuple[float, ...]
    real_pole_hz: tuple[float, ...]
    complex_pole_pairs: tuple[tuple[float, float], ...]  # (natural_frequency_hz, damping_ratio)
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

        A first-order Padé delay is used only for approximate time-domain/root
        calculations.  Frequency-domain validation always uses the exact delay.
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
        if pade_delay and self.delay_s > 0.0:
            tau = wref * float(self.delay_s)
            num_asc = poly.polymul(num_asc, np.asarray([1.0, -0.5 * tau], dtype=float))
            den_asc = poly.polymul(den_asc, np.asarray([1.0, 0.5 * tau], dtype=float))
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
        mask = np.abs(lf - center) <= 0.5  # +/- half decade around the control focus
        if int(np.count_nonzero(mask)) >= 3:
            focus_mag = float(np.sqrt(np.mean(np.square(mag_error[mask]))))
            focus_phase = float(np.sqrt(np.mean(np.square(phase_error[mask]))))

    if mag_rms <= 1.0 and phase_rms <= 5.0 and (focus_mag is None or focus_mag <= 0.75) and (focus_phase is None or focus_phase <= 4.0):
        confidence = "GOOD"
    elif mag_rms <= 3.0 and phase_rms <= 15.0 and (focus_mag is None or focus_mag <= 2.0) and (focus_phase is None or focus_phase <= 10.0):
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
    init_real = centers[:real_count]
    init_pair = centers[real_count:real_count + pair_count]
    init_zeta = np.full(pair_count, math.log10(0.5), dtype=float)
    base = np.r_[init_real, init_pair, init_zeta]

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

    delay_starts = (0.0, estimated_delay) if fit_delay and estimated_delay > 1e-12 else (0.0,)
    best: tuple[float, np.ndarray, np.ndarray] | None = None
    for delay0 in delay_starts:
        v0 = np.r_[base, delay0] if fit_delay else base.copy()
        lower = np.r_[lower_base, 0.0] if fit_delay else lower_base
        upper = np.r_[upper_base, max_delay] if fit_delay else upper_base
        result = least_squares(residual, v0, bounds=(lower, upper), max_nfev=max(int(max_nfev), 40))
        coeff, fitted = solve_numerator(result.x)
        cost = float(np.mean(np.square(np.abs((fitted - h) / scale))))
        if best is None or cost < best[0]:
            best = (cost, result.x.copy(), coeff.copy())

    assert best is not None
    cost, vector, coeff = best
    real_hz, pair_hz, damping, delay_s = unpack(vector)
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
    """Fit a stable low-order rational model directly to complex FRA points.

    Orders are tried from ``min_poles`` through ``max_poles``.  For each order,
    all combinations of stable real poles and stable complex-conjugate pole
    pairs are considered.  The lowest order reaching GOOD confidence is kept;
    otherwise the lowest normalized fit-error candidate through max order is
    returned and explicitly marked FAIR/LOW by the metrics.
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
                    "Fit uses stable real/complex pole factors and optional pure delay; raw FRA remains the stability authority.",
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


def closed_loop_step_from_fitted_loop(
    loop_model: RationalPlantModel,
    *,
    samples: int = 1200,
    settling_band: float = 0.02,
) -> FitClosedLoopStepResult:
    """Approximate closed-loop step from a fitted *open-loop* model.

    The model's pure delay is converted to a first-order Padé approximation for
    this time-domain calculation.  The result must therefore be presented as a
    fit-derived engineering estimate, never as a substitute for measured step
    validation.
    """
    num, den = loop_model.normalized_polynomials(pade_delay=True)
    length = max(len(num), len(den))
    num_pad = np.pad(num, (length - len(num), 0))
    den_pad = np.pad(den, (length - len(den), 0))
    closed_den = den_pad + num_pad
    closed_num = num_pad.copy()
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
    final = float(y[-1])
    peak = float(np.max(y)) if final >= 0.0 else float(np.min(y))
    if abs(final) > 1e-12:
        overshoot = max(0.0, (peak - final) / abs(final) * 100.0) if final >= 0.0 else max(0.0, (final - peak) / abs(final) * 100.0)
    else:
        overshoot = 0.0

    rise = None
    if abs(final) > 1e-12:
        low, high = 0.10 * final, 0.90 * final
        if final >= 0.0:
            i10 = np.flatnonzero(y >= low)
            i90 = np.flatnonzero(y >= high)
        else:
            i10 = np.flatnonzero(y <= low)
            i90 = np.flatnonzero(y <= high)
        if i10.size and i90.size and i90[0] >= i10[0]:
            rise = float(t_s[i90[0]] - t_s[i10[0]])

    scale = max(abs(final), 1e-12)
    outside = np.flatnonzero(np.abs(y - final) > float(settling_band) * scale)
    settling = None
    if outside.size:
        index = int(outside[-1] + 1)
        if index < len(t_s):
            settling = float(t_s[index])
    else:
        settling = 0.0

    return FitClosedLoopStepResult(
        t_s,
        y,
        True,
        poles_s,
        final,
        peak,
        float(overshoot),
        rise,
        settling,
        "Approximate step from rational open-loop fit + first-order Padé delay; validate against hardware before release use.",
    )


__all__ = [
    "RationalPlantModel",
    "FRAFitMetrics",
    "FRAFitResult",
    "FitClosedLoopStepResult",
    "fit_rational_frequency_response",
    "closed_loop_step_from_fitted_loop",
]

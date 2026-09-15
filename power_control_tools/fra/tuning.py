from __future__ import annotations

from collections.abc import Sequence

import numpy as np

from power_control_tools.models import DigitalTransferFunction


def scale_digital_controller(
    base: DigitalTransferFunction,
    *,
    gain_scale: float = 1.0,
    numerator_scales: Sequence[float] | None = None,
    denominator_scales: Sequence[float] | None = None,
    name: str | None = None,
) -> DigitalTransferFunction:
    """Return an exact H(z) obtained by scaling an existing controller.

    This helper is intentionally coefficient-domain rather than model-domain.
    It supports FRA quick tuning when the current firmware controller is only
    known as exact B/A coefficients and no trustworthy analog PI/2P2Z model is
    available.

    ``gain_scale`` multiplies all numerator coefficients and therefore moves
    the open-loop gain without changing the controller phase.

    ``numerator_scales`` optionally applies one multiplier to each normalized
    numerator coefficient. ``denominator_scales`` applies one multiplier to
    each denominator feedback coefficient a1..an; a0 always remains 1.0.

    Positive multipliers are expected for interactive tuning.  The function
    itself accepts any finite value so tests and expert workflows can remain
    mathematically complete.
    """

    d = base.normalized()
    gain = float(gain_scale)
    if not np.isfinite(gain):
        raise ValueError("gain_scale must be finite")

    b = np.asarray(d.b, dtype=float).copy()
    a = np.asarray(d.a, dtype=float).copy()

    if numerator_scales is not None:
        ns = np.asarray(tuple(float(v) for v in numerator_scales), dtype=float)
        if ns.size != b.size:
            raise ValueError(f"numerator_scales requires {b.size} values")
        if not np.all(np.isfinite(ns)):
            raise ValueError("numerator_scales must be finite")
        b *= ns

    if denominator_scales is not None:
        ds = np.asarray(tuple(float(v) for v in denominator_scales), dtype=float)
        expected = max(a.size - 1, 0)
        if ds.size != expected:
            raise ValueError(f"denominator_scales requires {expected} values for a1..an")
        if not np.all(np.isfinite(ds)):
            raise ValueError("denominator_scales must be finite")
        if expected:
            a[1:] *= ds

    b *= gain
    a[0] = 1.0

    return DigitalTransferFunction(
        tuple(float(v) for v in b),
        tuple(float(v) for v in a),
        d.sample_rate_hz,
        name or f"{d.name} quick tune",
        f"FRA quick tune from {d.source or d.name}",
    ).normalized()


def firmware_feedback_a_to_denominator(feedback_a: Sequence[float]) -> tuple[float, ...]:
    """Convert a direct-recursion ``+A*y`` convention into canonical H(z).

    Firmware often stores the recurrence as::

        y[n] = sum(b[k] x[n-k]) + A1*y[n-1] + A2*y[n-2] + ...

    The toolkit canonical convention is::

        y[n] = sum(b[k] x[n-k]) - a1*y[n-1] - a2*y[n-2] - ...

    Hence ``a_i = -A_i`` and the normalized denominator is
    ``(1, -A1, -A2, ...)``.
    """

    values = tuple(float(v) for v in feedback_a)
    if not all(np.isfinite(v) for v in values):
        raise ValueError("firmware feedback coefficients must be finite")
    return (1.0,) + tuple(-v for v in values)


__all__ = ["scale_digital_controller", "firmware_feedback_a_to_denominator"]

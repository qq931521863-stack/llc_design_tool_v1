from __future__ import annotations

import math
from collections.abc import Sequence

import numpy as np

from power_control_tools.models import AnalogTransferFunction, ControllerKind


def _w(f_hz: float) -> float:
    f = float(f_hz)
    if not math.isfinite(f) or f <= 0.0:
        raise ValueError("pole/zero frequency must be finite and positive")
    return 2.0 * math.pi * f


def design_power_pz_compensator(
    kind: ControllerKind | str,
    *,
    zeros_hz: Sequence[float],
    poles_hz: Sequence[float],
    gain: float = 1.0,
) -> AnalogTransferFunction:
    """Build a power-supply 2P2Z/3P3Z analog template.

    This is intentionally different from a generic equal-order pole/zero
    transfer function.  In power conversion, TI Compensation Designer and
    common C2000 practice use an integrator pole at s=0:

      2P2Z: K * (s+wz1)(s+wz2) / [s (s+wp1)]
      3P3Z: K * (s+wz1)(s+wz2)(s+wz3) /
                   [s (s+wp1)(s+wp2)]

    The exact normalization of K is not important to FRA Auto Design because
    the gain is solved again from the 0-dB condition at the requested
    crossover.  What matters here is the compensator pole/zero topology and
    its integral action.
    """
    kind = ControllerKind(kind)
    if kind == ControllerKind.TWO_P_TWO_Z:
        expected_z, expected_p = 2, 1
        name = "Power 2P2Z"
    elif kind == ControllerKind.THREE_P_THREE_Z:
        expected_z, expected_p = 3, 2
        name = "Power 3P3Z"
    else:
        raise ValueError("power pole/zero template supports only 2P2Z or 3P3Z")

    zeros = tuple(float(v) for v in zeros_hz)
    poles = tuple(float(v) for v in poles_hz)
    if len(zeros) != expected_z or len(poles) != expected_p:
        raise ValueError(
            f"{name} requires {expected_z} finite zeros and {expected_p} finite poles plus the integrator pole"
        )
    k = float(gain)
    if not math.isfinite(k) or k <= 0.0:
        raise ValueError("compensator gain must be finite and positive")

    wz = [_w(v) for v in zeros]
    wp = [_w(v) for v in poles]
    num = k * np.poly([-v for v in wz]).astype(float)
    den = np.polymul(np.asarray([1.0, 0.0]), np.poly([-v for v in wp])).astype(float)
    return AnalogTransferFunction(tuple(float(v) for v in num), tuple(float(v) for v in den), name)


__all__ = ["design_power_pz_compensator"]

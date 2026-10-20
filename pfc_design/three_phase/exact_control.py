"""Canonical dq controllers and explicitly sampled engineering-unit sensors.

All coefficients are ascending powers of z^-1. DF2T is used unchanged by the
runtime and the state-space model. The nonlinear tracking correction is applied
only to the first state; it is a declared policy, not a generic AW stability proof.
"""

import math
from dataclasses import dataclass
from numbers import Real

import numpy as np

from llc_design.control.digital_loop import DigitalTransferFunction as LoopTransfer
from power_control_tools.models import DigitalTransferFunction


@dataclass(frozen=True)
class ExactController:
    b: tuple[float, ...]
    a: tuple[float, ...]
    sample_rate_hz: float
    source: str = "external H(z)"

    def __post_init__(self):
        for values in (self.b, self.a):
            if not isinstance(values, (tuple, list)) or not 1 <= len(values) <= 17:
                raise ValueError(
                    "H(z) requires 1..17 finite coefficients per polynomial"
                )
            if any(
                isinstance(v, (bool, np.bool_))
                or not isinstance(v, Real)
                or not math.isfinite(v)
                for v in values
            ):
                raise ValueError("H(z) coefficients must be finite numbers")
        if (
            isinstance(self.sample_rate_hz, (bool, np.bool_))
            or not isinstance(self.sample_rate_hz, Real)
            or not math.isfinite(self.sample_rate_hz)
            or self.sample_rate_hz <= 0
        ):
            raise ValueError("H(z) sample rate must be finite and positive")
        if abs(self.a[0]) < 1e-18:
            raise ValueError("H(z) a0 must be nonzero")
        if not isinstance(self.source, str) or len(self.source) > 4096:
            raise ValueError("H(z) source must be a string of at most 4096 characters")
        object.__setattr__(self, "sample_rate_hz", float(self.sample_rate_hz))
        a0 = self.a[0]
        object.__setattr__(self, "b", tuple(float(v / a0) for v in self.b))
        object.__setattr__(self, "a", tuple(float(v / a0) for v in self.a))
        if not all(math.isfinite(v) for v in self.b + self.a):
            raise ValueError("normalized H(z) coefficients must be finite")

    @property
    def order(self):
        return max(len(self.a), len(self.b)) - 1

    def loop_transfer(self):
        return LoopTransfer(
            np.asarray(self.b), np.asarray(self.a), 1 / self.sample_rate_hz, self.source
        )

    def digital(self, name="dq controller"):
        return DigitalTransferFunction(
            self.b, self.a, self.sample_rate_hz, name, self.source
        )


def native_controller(config, loop):
    """Preserve the original physical runtime, including update ordering."""
    if loop == "current":
        kp, ki, rate = config.current_kp, config.current_ki, config.control_hz
        return ExactController(
            (kp, -kp + ki / rate),
            (1.0, -1.0),
            rate,
            "native current PI / forward Euler / runtime-preserving v2",
        )
    if loop == "voltage":
        kp, ki, rate = config.voltage_kp, config.voltage_ki, config.voltage_loop_hz
        return ExactController(
            (kp + ki / rate, -kp),
            (1.0, -1.0),
            rate,
            "native voltage PI / backward Euler / runtime-preserving v2",
        )
    raise ValueError("unknown dq loop")


def controller_for(config, loop):
    return getattr(config, loop + "_controller") or native_controller(config, loop)


def df2t_advance(controller, state, error):
    """Pure, linear state update; works for FIR, IIR, leading zeros and gain."""
    n = controller.order
    y = controller.b[0] * error + (state[0] if n else 0.0)
    result = np.zeros(n)
    for j in range(n):
        bj = controller.b[j + 1] if j + 1 < len(controller.b) else 0.0
        aj = controller.a[j + 1] if j + 1 < len(controller.a) else 0.0
        result[j] = bj * error - aj * y + (state[j + 1] if j + 1 < n else 0.0)
    return float(y), result


class DF2T:
    def __init__(self, controller, initial_output=0.0):
        self.controller = controller
        self.state = np.zeros(controller.order)
        if self.state.size:
            self.state[0] = initial_output

    def output(self, error):
        return df2t_advance(self.controller, self.state, error)[0]

    def advance(self, error, tracking_correction=0.0, *, enabled=True):
        y, state = df2t_advance(self.controller, self.state, error)
        if enabled:
            if state.size:
                state[0] += tracking_correction
            self.state = state
        return y


@dataclass(frozen=True)
class SampledSense:
    """Calibrated dq/bus sample-domain gain + one pole + integer latency.

    Pole is a matched-z discrete filter after ideal sampling, NOT an analog
    anti-alias circuit. ADC scale/bias provide headroom diagnostics only;
    quantization, aperture, clipping and ABC-to-dq sensor coupling are excluded.
    """

    gain: float = 1.0
    pole_hz: float = 0.0
    delay_ticks: int = 0
    adc_v_per_unit: float = 0.0
    adc_bias_v: float = 0.0
    adc_reference_v: float = 3.3

    def validate(self):
        for key, value in vars(self).items():
            if type(value) not in (int, float) or not math.isfinite(value):
                raise ValueError(f"sense {key} must be finite")
        if (
            self.gain <= 0
            or self.pole_hz < 0
            or self.adc_v_per_unit < 0
            or self.adc_reference_v <= 0
        ):
            raise ValueError(
                "sense gain/reference positive; pole/ADC scale nonnegative"
            )
        if type(self.delay_ticks) is not int or not 0 <= self.delay_ticks <= 5:
            raise ValueError("sense latency must be integer 0..5 samples")

    def controller(self, rate):
        self.validate()
        if self.pole_hz:
            p = math.exp(-2 * math.pi * self.pole_hz / rate)
            b, a = (self.gain * (1 - p),), (1.0, -p)
        else:
            b, a = (self.gain,), (1.0,)
        return ExactController(
            (0.0,) * self.delay_ticks + b, a, rate, "sampled engineering-unit sense"
        )

    def headroom(self, minimum, maximum):
        self.validate()
        if self.adc_v_per_unit == 0:
            return {"status": "UNKNOWN", "reason": "physical ADC scale unspecified"}
        limits = [
            minimum * self.adc_v_per_unit + self.adc_bias_v,
            maximum * self.adc_v_per_unit + self.adc_bias_v,
        ]
        return {
            "status": "IN_RANGE"
            if 0 <= min(limits) and max(limits) <= self.adc_reference_v
            else "OVERRANGE",
            "adc_min_v": min(limits),
            "adc_max_v": max(limits),
            "adc_reference_v": self.adc_reference_v,
            "scope": "configured nominal/limit envelope only; no transient or hardware proof",
        }


def adc_headroom(config):
    """Declared precharge/regulated target and reference-limit envelope only."""
    current_bound = max(
        config.current_limit_a, config.current_limit_a / config.current_sense.gain
    )
    bus_bound = max(
        config.bus_voltage_v, config.bus_voltage_v / config.voltage_sense.gain
    )
    return {
        "current": config.current_sense.headroom(-current_bound, current_bound),
        "voltage": config.voltage_sense.headroom(0, bus_bound),
    }

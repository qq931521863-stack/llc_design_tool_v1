"""Digital-control execution primitives for digital closed-loop analysis.

These classes model what firmware actually executes: ADC sampling/quantization,
one-sample-at-a-time exact H(z), controller limiting and LLC frequency
modulation. They are backend independent so the same digital chain can drive a
surrogate plant or a shared-ngspice switching circuit.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from enum import Enum
import math

import numpy as np

from power_control_tools.models import DigitalTransferFunction


class LLCFMMode(str, Enum):
    LINEAR_FM = "linear_fm"
    FREQUENCY_COMMAND = "frequency_command"
    DIRECT_TBPRD = "direct_tbprd"


class PWMCountMode(str, Enum):
    UP = "up"
    UP_DOWN = "up_down"

    @property
    def divisor(self) -> float:
        return 1.0 if self == PWMCountMode.UP else 2.0


@dataclass(frozen=True)
class SamplerConfig:
    sample_rate_hz: float
    sample_phase_s: float = 0.0
    gain: float = 1.0
    offset: float = 0.0
    adc_bits: int | None = None
    adc_min: float = 0.0
    adc_max: float = 3.3
    delay_samples: int = 0

    def validate(self) -> None:
        if self.sample_rate_hz <= 0.0:
            raise ValueError("sample_rate_hz must be positive")
        if self.sample_phase_s < 0.0 or self.sample_phase_s >= self.sample_time_s:
            raise ValueError("sample_phase_s must lie within one control sample")
        if self.adc_bits is not None and self.adc_bits < 1:
            raise ValueError("adc_bits must be >= 1")
        if self.adc_max <= self.adc_min:
            raise ValueError("adc_max must exceed adc_min")
        if self.delay_samples < 0:
            raise ValueError("delay_samples cannot be negative")

    @property
    def sample_time_s(self) -> float:
        return 1.0 / self.sample_rate_hz


class SamplerRuntime:
    def __init__(self, config: SamplerConfig):
        config.validate()
        self.config = config
        self._delay = deque(maxlen=config.delay_samples + 1)
        self.reset()

    def reset(self, initial_value: float = 0.0) -> None:
        self._delay.clear()
        for _ in range(self.config.delay_samples + 1):
            self._delay.append(float(initial_value))

    def _quantize_adc_domain(self, value: float) -> float:
        if self.config.adc_bits is None:
            return float(value)
        clipped = min(max(float(value), self.config.adc_min), self.config.adc_max)
        levels = (1 << self.config.adc_bits) - 1
        code = int(round((clipped - self.config.adc_min) * levels / (self.config.adc_max - self.config.adc_min)))
        return self.config.adc_min + code * (self.config.adc_max - self.config.adc_min) / levels

    def sample(self, engineering_value: float) -> float:
        adc_domain = self.config.gain * float(engineering_value) + self.config.offset
        adc_domain = self._quantize_adc_domain(adc_domain)
        if abs(self.config.gain) > 1e-30:
            engineering = (adc_domain - self.config.offset) / self.config.gain
        else:
            engineering = adc_domain
        self._delay.append(float(engineering))
        return float(self._delay[0])


@dataclass(frozen=True)
class ControllerLimitConfig:
    minimum: float = -math.inf
    maximum: float = math.inf
    clamp_state_to_output: bool = True

    def validate(self) -> None:
        if self.maximum <= self.minimum:
            raise ValueError("controller maximum must exceed minimum")


@dataclass(frozen=True)
class ControllerStep:
    raw_output: float
    output: float
    saturated: bool


class DigitalTransferRuntime:
    """Sample-by-sample direct-form execution of the project exact H(z).

    Project convention:
      H(z)=(b0+b1 z^-1+...)/(1+a1 z^-1+...)
      y[n]=sum(b*x)-sum(a*y)

    When limited, the clamped output can be inserted into output history as a
    generic state-clamping anti-windup approximation. No controller
    re-discretization occurs here.
    """

    def __init__(self, transfer: DigitalTransferFunction, limits: ControllerLimitConfig | None = None):
        self.transfer = transfer.normalized()
        self.limits = limits or ControllerLimitConfig()
        self.limits.validate()
        self._x = np.zeros(len(self.transfer.b), dtype=float)
        self._y = np.zeros(max(0, len(self.transfer.a) - 1), dtype=float)

    def reset(self) -> None:
        self._x.fill(0.0)
        self._y.fill(0.0)

    def step(self, value: float) -> ControllerStep:
        if len(self._x) > 1:
            self._x[1:] = self._x[:-1]
        self._x[0] = float(value)
        b = np.asarray(self.transfer.b, dtype=float)
        a = np.asarray(self.transfer.a, dtype=float)
        raw = float(np.dot(b, self._x))
        if self._y.size:
            raw -= float(np.dot(a[1:], self._y))
        out = min(max(raw, self.limits.minimum), self.limits.maximum)
        saturated = not math.isclose(out, raw, rel_tol=0.0, abs_tol=1e-15)
        y_for_state = out if (saturated and self.limits.clamp_state_to_output) else raw
        if self._y.size > 1:
            self._y[1:] = self._y[:-1]
        if self._y.size:
            self._y[0] = y_for_state
        return ControllerStep(raw, out, saturated)


@dataclass(frozen=True)
class LLCFMConfig:
    mode: LLCFMMode = LLCFMMode.LINEAR_FM
    nominal_frequency_hz: float = 100_000.0
    kfm_hz_per_unit: float = -50_000.0
    minimum_frequency_hz: float = 50_000.0
    maximum_frequency_hz: float = 250_000.0
    tbclk_hz: float = 120_000_000.0
    count_mode: PWMCountMode = PWMCountMode.UP_DOWN
    quantize_tbprd: bool = True

    def validate(self) -> None:
        if self.nominal_frequency_hz <= 0.0:
            raise ValueError("nominal_frequency_hz must be positive")
        if self.minimum_frequency_hz <= 0.0 or self.maximum_frequency_hz <= self.minimum_frequency_hz:
            raise ValueError("invalid frequency limits")
        if self.tbclk_hz <= 0.0:
            raise ValueError("tbclk_hz must be positive")

    def tbprd_from_frequency(self, frequency_hz: float) -> float:
        return self.tbclk_hz / (self.count_mode.divisor * float(frequency_hz))

    def frequency_from_tbprd(self, tbprd: float) -> float:
        if tbprd <= 0.0:
            raise ValueError("TBPRD must be positive")
        return self.tbclk_hz / (self.count_mode.divisor * float(tbprd))

    @property
    def tbprd_min(self) -> int:
        return max(1, int(math.ceil(self.tbprd_from_frequency(self.maximum_frequency_hz))))

    @property
    def tbprd_max(self) -> int:
        return max(self.tbprd_min, int(math.floor(self.tbprd_from_frequency(self.minimum_frequency_hz))))

    def small_signal_gain_hz_per_command(self, operating_command: float = 0.0) -> float:
        if self.mode == LLCFMMode.LINEAR_FM:
            return self.kfm_hz_per_unit
        if self.mode == LLCFMMode.FREQUENCY_COMMAND:
            return 1.0
        tbprd = max(float(operating_command), 1.0)
        return -self.tbclk_hz / (self.count_mode.divisor * tbprd * tbprd)


@dataclass(frozen=True)
class LLCFMLUTConfig:
    """Firmware-style piecewise-linear PCMD frequency-modulator table.

    ``values_are_tbprd`` selects PCMD->TBPRD or direct PCMD->frequency.
    ``command_bias_pu`` is the steady operating command; the exact H(z)
    controller supplies the perturbation around that bias.
    """

    pcmd: tuple[float, ...]
    values: tuple[float, ...]
    command_bias_pu: float
    values_are_tbprd: bool = True
    tbclk_hz: float = 120_000_000.0
    count_mode: PWMCountMode = PWMCountMode.UP_DOWN
    quantize_tbprd: bool = True

    def arrays(self) -> tuple[np.ndarray, np.ndarray]:
        return np.asarray(self.pcmd, dtype=float), np.asarray(self.values, dtype=float)

    def validate(self) -> None:
        p, v = self.arrays()
        if p.size < 2 or p.size != v.size:
            raise ValueError("FM LUT requires matching PCMD/value arrays with >=2 points")
        if np.any(~np.isfinite(p)) or np.any(~np.isfinite(v)) or np.any(v <= 0.0):
            raise ValueError("FM LUT contains invalid values")
        if np.any(np.diff(p) <= 0.0):
            raise ValueError("FM LUT PCMD nodes must be strictly increasing")
        if p[0] > 0.0 or p[-1] < 1.0:
            raise ValueError("FM LUT must cover PCMD 0..1")
        if not (p[0] <= self.command_bias_pu <= p[-1]):
            raise ValueError("FM LUT command bias is outside table range")
        if self.tbclk_hz <= 0.0:
            raise ValueError("tbclk_hz must be positive")

    def frequency_from_value(self, value: float) -> float:
        if self.values_are_tbprd:
            return self.tbclk_hz / (self.count_mode.divisor * float(value))
        return float(value)

    def frequency_at_absolute_command(self, command_pu: float) -> float:
        p, v = self.arrays()
        command = float(np.clip(command_pu, p[0], p[-1]))
        return self.frequency_from_value(float(np.interp(command, p, v)))

    @property
    def minimum_frequency_hz(self) -> float:
        p, _ = self.arrays()
        frequencies = np.asarray([self.frequency_at_absolute_command(float(x)) for x in p])
        return float(np.min(frequencies))

    @property
    def maximum_frequency_hz(self) -> float:
        p, _ = self.arrays()
        frequencies = np.asarray([self.frequency_at_absolute_command(float(x)) for x in p])
        return float(np.max(frequencies))

    @property
    def nominal_frequency_hz(self) -> float:
        return self.frequency_at_absolute_command(self.command_bias_pu)


@dataclass(frozen=True)
class LLCFMStep:
    command: float
    frequency_command_hz: float
    actual_frequency_hz: float
    tbprd: int | None
    saturated: bool
    quantized: bool


def _map_lut(config: LLCFMLUTConfig, command: float) -> LLCFMStep:
    c = config
    p, v = c.arrays()
    perturbation = float(command)
    absolute_raw = c.command_bias_pu + perturbation
    absolute = float(np.clip(absolute_raw, p[0], p[-1]))
    saturated = not math.isclose(absolute, absolute_raw, rel_tol=0.0, abs_tol=1e-15)
    mapped_value = float(np.interp(absolute, p, v))

    if c.values_are_tbprd:
        raw_tbprd = mapped_value
        if c.quantize_tbprd:
            tbprd = max(1, int(round(raw_tbprd)))
            quantized = not math.isclose(float(tbprd), raw_tbprd, rel_tol=0.0, abs_tol=1e-15)
        else:
            tbprd = None
            quantized = False
        fcmd = c.frequency_from_value(raw_tbprd)
        factual = c.frequency_from_value(float(tbprd)) if tbprd is not None else fcmd
    else:
        fcmd = mapped_value
        if c.quantize_tbprd:
            raw_tbprd = c.tbclk_hz / (c.count_mode.divisor * fcmd)
            tbprd = max(1, int(round(raw_tbprd)))
            factual = c.tbclk_hz / (c.count_mode.divisor * tbprd)
            quantized = not math.isclose(factual, fcmd, rel_tol=0.0, abs_tol=1e-12)
        else:
            tbprd = None
            factual = fcmd
            quantized = False

    return LLCFMStep(perturbation, float(fcmd), float(factual), tbprd, saturated, quantized)


class LLCFMRuntime:
    """Compatibility runtime accepting legacy analytic FM or exact LUT config."""

    def __init__(self, config: LLCFMConfig | LLCFMLUTConfig):
        config.validate()
        self.config = config

    def map(self, command: float) -> LLCFMStep:
        c = self.config
        if isinstance(c, LLCFMLUTConfig):
            return _map_lut(c, command)

        command = float(command)
        saturated = False
        quantized = False
        tbprd: int | None = None

        if c.mode == LLCFMMode.LINEAR_FM:
            fcmd = c.nominal_frequency_hz + c.kfm_hz_per_unit * command
        elif c.mode == LLCFMMode.FREQUENCY_COMMAND:
            fcmd = command
        else:
            raw_tbprd = command
            q = int(round(raw_tbprd))
            quantized = c.quantize_tbprd and not math.isclose(float(q), raw_tbprd, rel_tol=0.0, abs_tol=1e-15)
            clipped = min(max(q, c.tbprd_min), c.tbprd_max)
            saturated = clipped != q
            tbprd = clipped
            factual = c.frequency_from_tbprd(tbprd)
            return LLCFMStep(command, factual, factual, tbprd, saturated, quantized)

        clipped_f = min(max(fcmd, c.minimum_frequency_hz), c.maximum_frequency_hz)
        saturated = not math.isclose(clipped_f, fcmd, rel_tol=0.0, abs_tol=1e-12)
        if c.quantize_tbprd:
            raw_tbprd = c.tbprd_from_frequency(clipped_f)
            tbprd = int(round(raw_tbprd))
            tbprd = min(max(tbprd, c.tbprd_min), c.tbprd_max)
            factual = c.frequency_from_tbprd(tbprd)
            quantized = not math.isclose(factual, clipped_f, rel_tol=0.0, abs_tol=1e-12)
        else:
            factual = clipped_f
        return LLCFMStep(command, clipped_f, factual, tbprd, saturated, quantized)


class LLCFMLUTRuntime:
    def __init__(self, config: LLCFMLUTConfig):
        config.validate()
        self.config = config

    def map(self, command: float) -> LLCFMStep:
        return _map_lut(self.config, command)


def make_fm_runtime(config: LLCFMConfig | LLCFMLUTConfig) -> LLCFMRuntime | LLCFMLUTRuntime:
    if isinstance(config, LLCFMLUTConfig):
        return LLCFMLUTRuntime(config)
    if isinstance(config, LLCFMConfig):
        return LLCFMRuntime(config)
    raise TypeError(f"unsupported LLC FM configuration: {type(config)!r}")

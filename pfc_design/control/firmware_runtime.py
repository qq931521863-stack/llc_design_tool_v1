"""Firmware-faithful TTPL PFC execution primitives.

This module is the time-domain implementation companion to the exact-H(z)
contract.  The linear controller coefficients remain authoritative and are
consumed directly from :class:`PFCControllerArtifact`; no Kp/Ti -> S2Z path is
used here.

For PI/PIF controllers the internal state parameters required for anti-windup
are recovered algebraically from the exact H(z) coefficients.  That keeps the
frozen discrete controller as the source of truth while allowing the runtime to
match the C99 firmware semantics used by Power Design Toolkit:

* float32 arithmetic at every state update;
* Tustin PI integrator state;
* conditional integrator freeze when saturation and error push outward;
* PIF output LPF after PI saturation;
* 2P2Z with clamped output inserted into denominator history.

The sensing runtime models the configured analog first-order poles, ADC
resolution, multi-SOC recursive averaging and digital filtering.  The current
PFC schema does not yet carry board-specific ADC offset/clipping rails, so ADC
quantization is performed in calibrated signed engineering units using the
configured Vref/bits/raw gain.  This limitation is explicit in metadata and is
not presented as absolute MCU-code bit identity.
"""
from __future__ import annotations

from dataclasses import dataclass
import math

import numpy as np

from .config import ExternalSenseConfig
from .handoff import PFCControllerArtifact


def _f32(value: float) -> np.float32:
    return np.float32(value)


def _close(a: float, b: float, tol: float = 2e-6) -> bool:
    return math.isclose(float(a), float(b), rel_tol=tol, abs_tol=tol)


@dataclass(frozen=True)
class PFCFirmwareControllerStep:
    raw_output: float
    output: float
    saturated: bool
    anti_windup_frozen: bool


class PFCExactFirmwareControllerRuntime:
    """Execute one frozen exact H(z) with explicit firmware state semantics."""

    def __init__(self, artifact: PFCControllerArtifact, *, initial_output: float = 0.0):
        artifact.validate()
        self.artifact = artifact
        self.kind = str(artifact.kind).lower()
        self.b = np.asarray(artifact.b, dtype=np.float32)
        self.a = np.asarray(artifact.a, dtype=np.float32)
        self.out_min = _f32(artifact.output_min)
        self.out_max = _f32(artifact.output_max)

        self.kp = _f32(0.0)
        self.ki2 = _f32(0.0)
        self.alpha = _f32(1.0)
        if self.kind == "pi":
            self._derive_pi()
        elif self.kind == "pif":
            self._derive_pif()
        elif self.kind == "2p2z":
            if len(self.b) != 3 or len(self.a) != 3 or not _close(self.a[0], 1.0):
                raise ValueError("2P2Z exact-H(z) runtime requires 3 b and 3 normalized a coefficients")
        else:
            raise TypeError(f"unsupported PFC exact-H(z) controller kind: {artifact.kind!r}")
        self.reset(initial_output=initial_output)

    def _derive_pi(self) -> None:
        if len(self.b) != 2 or len(self.a) != 2:
            raise ValueError("PI exact-H(z) runtime requires two numerator/two denominator coefficients")
        if not (_close(self.a[0], 1.0) and _close(self.a[1], -1.0)):
            raise ValueError("PI exact-H(z) denominator is not the frozen Tustin integrator [1, -1]")
        denominator = float(self.b[0] - self.b[1])
        if abs(denominator) < 1e-20:
            raise ValueError("PI exact-H(z) cannot recover Kp from degenerate numerator")
        self.kp = _f32(0.5 * denominator)
        self.ki2 = _f32(float(self.b[0] + self.b[1]) / denominator)

        # Reconstruct only as an identity check; runtime state is derived above
        # from the frozen b/a values rather than from Kp/Ti settings.
        b0 = _f32(self.kp * _f32(1.0 + self.ki2))
        b1 = _f32(self.kp * _f32(-1.0 + self.ki2))
        if not (_close(b0, self.b[0]) and _close(b1, self.b[1])):
            raise ValueError("PI firmware-state decomposition does not reproduce exact H(z)")

    def _derive_pif(self) -> None:
        if len(self.b) != 2 or len(self.a) != 3:
            raise ValueError("PIF exact-H(z) runtime requires two numerator/three denominator coefficients")
        if not _close(self.a[0], 1.0):
            raise ValueError("PIF exact-H(z) denominator must be normalized")
        alpha = 1.0 - float(self.a[2])
        if not 0.0 < alpha <= 1.0 + 1e-6:
            raise ValueError("PIF exact-H(z) cannot recover a valid output-LPF alpha")
        expected_a1 = -(2.0 - alpha)
        if not _close(self.a[1], expected_a1):
            raise ValueError("PIF exact-H(z) denominator is not PI cascaded with first-order output LPF")
        self.alpha = _f32(alpha)
        pi_b0 = float(self.b[0]) / alpha
        pi_b1 = float(self.b[1]) / alpha
        denominator = pi_b0 - pi_b1
        if abs(denominator) < 1e-20:
            raise ValueError("PIF exact-H(z) cannot recover PI Kp from degenerate numerator")
        self.kp = _f32(0.5 * denominator)
        self.ki2 = _f32((pi_b0 + pi_b1) / denominator)

        b0 = _f32(self.alpha * self.kp * _f32(1.0 + self.ki2))
        b1 = _f32(self.alpha * self.kp * _f32(-1.0 + self.ki2))
        if not (_close(b0, self.b[0]) and _close(b1, self.b[1])):
            raise ValueError("PIF firmware-state decomposition does not reproduce exact H(z)")

    def reset(self, *, initial_output: float = 0.0) -> None:
        initial = _f32(min(max(float(initial_output), float(self.out_min)), float(self.out_max)))
        self.error_prev = _f32(0.0)
        self.i_state = _f32(0.0)
        self.output_prev = initial
        self.x1 = _f32(0.0)
        self.x2 = _f32(0.0)
        self.y1 = initial
        self.y2 = initial
        if self.kind in {"pi", "pif"} and abs(float(self.kp)) > 1e-30:
            self.i_state = _f32(initial / self.kp)

    def _clip(self, value: np.float32) -> np.float32:
        return _f32(min(max(float(value), float(self.out_min)), float(self.out_max)))

    def step(self, error: float) -> PFCFirmwareControllerStep:
        e = _f32(error)
        if self.kind in {"pi", "pif"}:
            error_sum = _f32(e + self.error_prev)
            integral_delta = _f32(self.ki2 * error_sum)
            i_new = _f32(self.i_state + integral_delta)
            raw = _f32(self.kp * _f32(e + i_new))
            saturated_pi = self._clip(raw)
            frozen = bool(
                (float(raw) > float(self.out_max) and float(e) > 0.0)
                or (float(raw) < float(self.out_min) and float(e) < 0.0)
            )
            if not frozen:
                self.i_state = i_new
            if self.kind == "pif":
                one_minus = _f32(1.0 - self.alpha)
                output = _f32(_f32(one_minus * self.output_prev) + _f32(self.alpha * saturated_pi))
                self.output_prev = output
            else:
                output = saturated_pi
            self.error_prev = e
            return PFCFirmwareControllerStep(
                raw_output=float(raw),
                output=float(output),
                saturated=not _close(raw, saturated_pi, tol=0.0),
                anti_windup_frozen=frozen,
            )

        # Canonical 2P2Z: exact b/a with clamped output history.
        t0 = _f32(-self.a[1] * self.y1)
        t1 = _f32(-self.a[2] * self.y2)
        t2 = _f32(self.b[0] * e)
        t3 = _f32(self.b[1] * self.x1)
        t4 = _f32(self.b[2] * self.x2)
        raw = _f32(_f32(_f32(t0 + t1) + _f32(t2 + t3)) + t4)
        output = self._clip(raw)
        self.x2, self.x1 = self.x1, e
        self.y2, self.y1 = self.y1, output
        return PFCFirmwareControllerStep(
            raw_output=float(raw),
            output=float(output),
            saturated=not _close(raw, output, tol=0.0),
            anti_windup_frozen=False,
        )


@dataclass(frozen=True)
class PFCSenseSample:
    value: float
    adc_code: int
    sample_time_s: float


class PFCSampledSenseRuntime:
    """Analog poles + signed calibrated ADC quantization + sampled filtering.

    The runtime intentionally keeps ADC offset/rail clipping out of the model
    until the project schema carries those board-specific values.  Quantization
    LSB is nevertheless derived from the configured raw V/unit gain, Vref and
    ADC width, matching the firmware calibration scale in engineering units.
    """

    def __init__(self, config: ExternalSenseConfig, initial: float = 0.0) -> None:
        config.validate()
        self.config = config
        self.poles: list[float] = []
        if config.amplifier_bandwidth_hz > 0.0:
            self.poles.append(2.0 * math.pi * config.amplifier_bandwidth_hz)
        for resistance, capacitance in (
            (config.source_resistance_ohm, config.shunt_capacitance_f),
            (config.output_resistance_ohm, config.adc_capacitance_f),
            (config.second_resistance_ohm, config.second_capacitance_f),
        ):
            if resistance > 0.0 and capacitance > 0.0:
                self.poles.append(1.0 / (resistance * capacitance))
        self.states = [_f32(initial) for _ in self.poles]
        self.last_time_s = 0.0
        self.sample_period_s = 1.0 / config.timing.sample_rate_hz
        self.next_sample_s = 0.0
        self.previous_recursive = _f32(initial)
        self.digital = _f32(initial)
        self.output = float(_f32(initial))
        self.last_adc_code = self._engineering_to_code(initial)
        self.last_sample_time_s = 0.0

    @property
    def acquisition_to_ready_s(self) -> float:
        timing = self.config.timing
        return (
            0.5 * timing.acquisition_time_s
            + timing.conversion_time_s
            + max(timing.soc_count - 1, 0) * timing.soc_spacing_s
        )

    @property
    def lsb_engineering_units(self) -> float:
        full_scale_codes = float(1 << self.config.adc_bits)
        return self.config.adc_vref_v / full_scale_codes / max(self.config.raw_dc_gain, 1e-30)

    def _engineering_to_code(self, value: float) -> int:
        # Signed calibrated code. Board-specific common-mode offset/rail
        # clipping are intentionally deferred until those values are explicit.
        return int(round(float(value) / self.lsb_engineering_units))

    def _quantize(self, value: float) -> tuple[np.float32, int]:
        code = self._engineering_to_code(value)
        reconstructed = _f32(code * self.lsb_engineering_units)
        return reconstructed, code

    def next_event_after(self, time_s: float) -> float:
        t = float(time_s)
        if self.next_sample_s > t + 1e-15:
            return self.next_sample_s
        periods = math.floor((t - self.next_sample_s) / self.sample_period_s) + 1
        return self.next_sample_s + periods * self.sample_period_s

    def advance(self, physical_value: float, time_s: float) -> float:
        t = float(time_s)
        if t < self.last_time_s - 1e-15:
            return self.output
        dt = max(t - self.last_time_s, 0.0)
        x = _f32(physical_value)
        for index, pole in enumerate(self.poles):
            decay = _f32(math.exp(-pole * dt)) if dt > 0.0 else _f32(1.0)
            state = _f32(_f32(decay * self.states[index]) + _f32(_f32(1.0 - decay) * x))
            self.states[index] = state
            x = state
        self.last_time_s = t

        tolerance = max(1e-15, 1e-8 * self.sample_period_s)
        while t + tolerance >= self.next_sample_s:
            quantized, code = self._quantize(float(x))
            timing = self.config.timing
            w = _f32(timing.recursive_previous_weight)
            fresh = _f32(1.0 - w)
            recursive = _f32(_f32(fresh * quantized) + _f32(w * self.previous_recursive))
            self.previous_recursive = recursive
            alpha = _f32(timing.digital_filter.alpha)
            self.digital = _f32(self.digital + _f32(alpha * _f32(recursive - self.digital)))
            self.output = float(self.digital)
            self.last_adc_code = int(code)
            self.last_sample_time_s = float(self.next_sample_s)
            self.next_sample_s += self.sample_period_s
        return self.output

    @property
    def snapshot(self) -> PFCSenseSample:
        return PFCSenseSample(self.output, self.last_adc_code, self.last_sample_time_s)


@dataclass(frozen=True)
class PFCZeroCrossConfig:
    sign_hysteresis_v: float = 7.5
    transition_v: float = 15.0
    softstart_ticks: int = 10

    def validate(self) -> None:
        if self.sign_hysteresis_v < 0.0 or self.transition_v <= 0.0:
            raise ValueError("zero-cross thresholds must be non-negative/positive")
        if self.softstart_ticks < 1:
            raise ValueError("zero-cross softstart_ticks must be >= 1")


@dataclass(frozen=True)
class PFCZeroCrossStep:
    state_code: int
    reset_current_pi: bool
    zero_cross_active: bool
    lf_state: int
    deadband_fraction: float
    hf_softstart_fraction: float
    target_polarity: int


class PFCZeroCrossRuntime:
    """Eight-state TTPL commutation machine used by the PFC waveform model."""

    def __init__(self, config: PFCZeroCrossConfig | None = None) -> None:
        self.config = config or PFCZeroCrossConfig()
        self.config.validate()
        self.state = 1
        self.sign_filtered = 1
        self.count = 0

    def reset(self, *, positive_half: bool = True) -> None:
        self.state = 1 if positive_half else 5
        self.sign_filtered = 1 if positive_half else 0
        self.count = 0

    def step(self, vac_v: float) -> PFCZeroCrossStep:
        vac = float(_f32(vac_v))
        sign_h = self.config.sign_hysteresis_v
        threshold = self.config.transition_v
        if vac > sign_h:
            self.sign_filtered = 1
        elif vac < -sign_h:
            self.sign_filtered = 0

        reset_pi = False
        state = self.state
        if state == 1 and (vac < threshold or self.sign_filtered == 0):
            self.state, self.count, reset_pi = 2, 0, True
        elif state == 2 and vac <= 0.0:
            self.state, self.count = 3, 0
        elif state == 3 and vac < -threshold:
            self.state, self.count = 4, 0
        elif state == 4:
            self.count += 1
            reset_pi = True
            if self.count >= self.config.softstart_ticks and self.sign_filtered == 0:
                self.state, self.count = 5, 0
        elif state == 5 and (vac > -threshold or self.sign_filtered == 1):
            self.state, self.count, reset_pi = 6, 0, True
        elif state == 6 and vac >= 0.0:
            self.state, self.count = 7, 0
        elif state == 7 and vac > threshold:
            self.state, self.count, reset_pi = 8, 0, True
        elif state == 8:
            self.count += 1
            reset_pi = True
            if self.count >= self.config.softstart_ticks and self.sign_filtered == 1:
                self.state, self.count = 1, 0

        zero_cross = self.state not in (1, 5)
        lf_state = 1 if self.state == 1 else (-1 if self.state == 5 else 0)
        if self.state in (4, 8):
            hf_softstart = min(0.05 + 0.075 * self.count, 1.0)
            deadband = max(1.0 - hf_softstart, 0.0)
        else:
            hf_softstart = 0.0 if zero_cross else 1.0
            deadband = 1.0 if zero_cross else 0.0
        target = 1 if self.state in (1, 6, 7, 8) else -1
        return PFCZeroCrossStep(
            state_code=self.state,
            reset_current_pi=reset_pi,
            zero_cross_active=zero_cross,
            lf_state=lf_state,
            deadband_fraction=float(deadband),
            hf_softstart_fraction=float(hf_softstart),
            target_polarity=target,
        )


__all__ = [
    "PFCExactFirmwareControllerRuntime",
    "PFCFirmwareControllerStep",
    "PFCSampledSenseRuntime",
    "PFCSenseSample",
    "PFCZeroCrossConfig",
    "PFCZeroCrossRuntime",
    "PFCZeroCrossStep",
]

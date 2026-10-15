"""Parallel quasi-PR controller; units: Hz, seconds, and application I/O units.

H(s) = Kp + sum(2 Kr_h wc_h s / (s*s + 2 wc_h s + wr_h*wr_h)).
Tustin coefficients use the user's A1/A2 denominator convention. Never expand
parallel branches into a high-order polynomial for implementation.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

HARMONICS = (1, 3, 5, 7, 9, 11)


@dataclass(frozen=True)
class ResonantBranch:
    harmonic: int
    kr: float = 10.0
    bandwidth_hz: float = 1.0  # wc / (2*pi), not the full -3 dB bandwidth


@dataclass(frozen=True)
class QuasiPRConfig:
    sample_rate_hz: float = 40000.0
    grid_frequency_hz: float = 50.0
    kp: float = 0.1
    branches: tuple[ResonantBranch, ...] = (ResonantBranch(1),)
    output_min: float = -1.0
    output_max: float = 1.0
    freeze_on_saturation: bool = False

    def validate(self) -> None:
        values = (self.sample_rate_hz, self.grid_frequency_hz, self.kp,
                  self.output_min, self.output_max)
        if not all(math.isfinite(v) for v in values):
            raise ValueError("PR parameters must be finite")
        if self.sample_rate_hz <= 0 or self.grid_frequency_hz <= 0 or self.kp < 0:
            raise ValueError("Fs and grid frequency must be positive; Kp must be nonnegative")
        if not self.output_min <= 0 <= self.output_max or self.output_min >= self.output_max:
            raise ValueError("Output limits must be ordered and include zero")
        orders = [b.harmonic for b in self.branches]
        if len(set(orders)) != len(orders) or any(h not in HARMONICS for h in orders):
            raise ValueError("Select distinct harmonics from 1, 3, 5, 7, 9, 11")
        for branch in self.branches:
            if not math.isfinite(branch.kr) or branch.kr < 0:
                raise ValueError("Kr must be finite and nonnegative")
            if not math.isfinite(branch.bandwidth_hz) or branch.bandwidth_hz <= 0:
                raise ValueError("wc/(2*pi) must be finite and positive")
            if max(branch.harmonic * self.grid_frequency_hz, branch.bandwidth_hz) >= self.sample_rate_hz / 2:
                raise ValueError("Resonance and bandwidth must be below Nyquist")


def resonant_coefficients(kr: float, wc: float, wr: float, ts: float,
                         kp: float = 0.0) -> tuple[np.ndarray, np.ndarray]:
    """User's bilinear formula; wc/wr in rad/s, ts in s; kp=0 for each R branch.

    Nonzero kp implements one full PR section for compatibility/comparison.
    Parallel multi-harmonic control adds a common kp only once.
    """
    if not all(math.isfinite(v) for v in (kr, wc, wr, ts, kp)):
        raise ValueError("Coefficients require finite parameters")
    if min(wc, wr, ts) <= 0 or min(kr, kp) < 0 or wr * ts >= math.pi:
        raise ValueError("Invalid gain, frequency or sample period")
    q = (ts * wr) ** 2
    c = 4.0 * wc * ts
    den = q + c + 4.0
    a = np.array([1.0, (2.0 * q - 8.0) / den, (q - c + 4.0) / den])
    r = kr * c / den
    b = kp * a + np.array([r, 0.0, -r])
    if not np.isfinite(b).all() or not np.isfinite(a).all():
        raise ValueError("Coefficient overflow")
    return b, a


def branch_coefficients(config: QuasiPRConfig, *, quantized: bool = False):
    config.validate()
    result = []
    for branch in config.branches:
        b, a = resonant_coefficients(branch.kr, 2 * math.pi * branch.bandwidth_hz,
                                     2 * math.pi * branch.harmonic * config.grid_frequency_hz,
                                     1 / config.sample_rate_hz)
        if quantized:
            b, a = b.astype(np.float32), a.astype(np.float32)
        result.append((b, a))
    return tuple(result)


def validate_float32(config: QuasiPRConfig) -> None:
    """Jury test on actual rounded denominator, evaluated in float64."""
    for branch, (b, a) in zip(config.branches, branch_coefficients(config, quantized=True)):
        a1, a2 = float(a[1]), float(a[2])
        if not np.isfinite(b).all() or min(1 + a1 + a2, 1 - a1 + a2, 1 - a2) <= 0:
            raise ValueError(f"H{branch.harmonic}: float32 coefficients are not strictly stable; increase bandwidth or reduce Fs")
    vals = np.asarray([config.kp, config.output_min, config.output_max], dtype=np.float32)
    if not np.isfinite(vals).all() or vals[1] >= vals[2]:
        raise ValueError("Gains/limits cannot be represented in float32")


def frequency_response(config: QuasiPRConfig, frequency_hz, *, analog=False,
                       quantized=False) -> tuple[np.ndarray, tuple[np.ndarray, ...]]:
    config.validate()
    f = np.asarray(frequency_hz, dtype=float)
    if not np.isfinite(f).all() or np.any(f < 0) or np.any(f >= config.sample_rate_hz / 2):
        raise ValueError("Bode frequencies must be finite and below Nyquist")
    branches = []
    if analog:
        s = 2j * math.pi * f
        for branch in config.branches:
            wc = 2 * math.pi * branch.bandwidth_hz
            wr = 2 * math.pi * branch.harmonic * config.grid_frequency_hz
            branches.append(2 * branch.kr * wc * s / (s*s + 2*wc*s + wr*wr))
    else:
        zi = np.exp(-2j * math.pi * f / config.sample_rate_hz)
        for b, a in branch_coefficients(config, quantized=quantized):
            branches.append((b[0] + b[1]*zi + b[2]*zi*zi) / (1 + a[1]*zi + a[2]*zi*zi))
    kp = float(np.float32(config.kp)) if quantized else config.kp
    total = np.full(f.shape, kp, dtype=complex)
    for response in branches:
        total += response
    return total, tuple(branches)


def bode_frequencies(config: QuasiPRConfig) -> np.ndarray:
    """Resolve narrow peaks, including Tustin frequency warping."""
    config.validate()
    fs = config.sample_rate_hz
    points = [np.geomspace(max(0.01, config.grid_frequency_hz / 100), fs * 0.499, 1400)]
    for b in config.branches:
        center = fs / math.pi * math.atan(math.pi * b.harmonic * config.grid_frequency_hz / fs)
        for fc in (center, b.harmonic * config.grid_frequency_hz):
            points.append(np.linspace(fc - 8*b.bandwidth_hz, fc + 8*b.bandwidth_hz, 321))
    f = np.unique(np.concatenate(points))
    return f[(f > 0) & (f < fs/2)]


class QuasiPRRuntime:
    """DFII reference, one owner, one step per Ts, zero-state reset.

    Default: output clipping only (as supplied); NOT anti-windup. Optional
    freeze discards all tentative resonator state updates on saturation.
    This nonlinear policy needs closed-loop validation; Bode is unsaturated.
    Nonfinite input/state computation resets all states, returns zero, flags fault.
    """
    def __init__(self, config: QuasiPRConfig, *, float32: bool = True):
        config.validate()
        if float32:
            validate_float32(config)
        self.config = config
        self.dtype = np.float32 if float32 else np.float64
        self.coefficients = branch_coefficients(config, quantized=float32)
        self.reset()

    def reset(self):
        self.states = np.zeros((len(self.config.branches), 2), dtype=self.dtype)
        self.fault = False
        self.saturated = False

    def step(self, error: float) -> float:
        d = self.dtype
        self.fault = False
        self.saturated = False
        with np.errstate(over="ignore", invalid="ignore"):
            x = d(error)
            total = d(d(self.config.kp) * x)
            candidate = self.states.copy()
            for i, (b, a) in enumerate(self.coefficients):
                w1, w2 = self.states[i]
                w0 = d(d(x - d(a[1]*w1)) - d(a[2]*w2))
                y = d(d(d(b[0]*w0) + d(b[1]*w1)) + d(b[2]*w2))
                total = d(total + y)
                candidate[i] = w0, w1
        if not np.isfinite(x) or not np.isfinite(total) or not np.isfinite(candidate).all():
            self.reset()
            self.fault = True
            return 0.0
        lo, hi = d(self.config.output_min), d(self.config.output_max)
        result = d(np.clip(total, lo, hi))
        self.saturated = bool(total < lo or total > hi)
        if not (self.saturated and self.config.freeze_on_saturation):
            self.states = candidate
        return float(result)

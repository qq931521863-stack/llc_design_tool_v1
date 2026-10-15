"""Offline constrained TPS optimization using exact ideal-L segment integrals.

This model excludes Cp, deadtime and Coss. Signed commutation-current constraints
are optional proxies, not proof of ZVS. Every published candidate is rechecked
on the PWM tick lattice. No PWM registers or real-time control are modified.
"""

from __future__ import annotations

import math
import time
from dataclasses import asdict, dataclass
from itertools import product

import numpy as np
from scipy.optimize import minimize

from .core import DABConfig, tps_command


@dataclass(frozen=True)
class TPSMetrics:
    power_w: float
    irms_a: float
    ipeak_a: float
    commutation_margin_a: float
    primary_active_pu: float
    secondary_active_pu: float


def exact_tps_metrics(config: DABConfig, vb_v: float, phases) -> TPSMetrics:
    """Exact integral of piecewise-linear zero-mean current over one period."""
    d1, d2, d3 = map(float, phases)
    if (
        not all(math.isfinite(v) for v in (vb_v, d1, d2, d3))
        or vb_v <= 0
        or not 0 <= d1 <= 1
        or not 0 <= d2 <= d3 <= 1
    ):
        raise ValueError("Require VB>0 and 0<=D1<=1, 0<=D2<=D3<=1")
    shifts = np.array([0, d1 / 2, d2 / 2, d3 / 2])
    events = np.unique(np.r_[0, 1, shifts, (shifts + 0.5) % 1])
    width = np.diff(events)
    mid = (events[:-1] + events[1:]) / 2
    legs = np.where((mid[:, None] - shifts) % 1 < 0.5, 1.0, -1.0)
    va = config.va_v * (legs[:, 0] + legs[:, 1]) / 2
    vb = vb_v / config.turns_ratio * (legs[:, 2] + legs[:, 3]) / 2
    delta = (va - vb) * width / (config.inductance_h * config.switching_hz)
    current = np.r_[0, np.cumsum(delta)]
    if abs(current[-1]) > 1e-7 * max(1, np.max(abs(current))):
        raise ValueError("Nonzero volt-seconds: no periodic ideal-L solution")
    current -= np.sum((current[:-1] + delta / 2) * width)
    mean = current[:-1] + delta / 2
    rms = math.sqrt(
        max(
            0,
            float(
                np.sum(
                    (current[:-1] ** 2 + current[:-1] * delta + delta**2 / 3) * width
                )
            ),
        )
    )
    # Rising primary leg proxy: -i; rising secondary: +i/n. Falling reverses it.
    margins = []
    for j, shift in enumerate(shifts):
        sign = -1 if j < 2 else 1 / config.turns_ratio
        for edge, polarity in [(shift, 1), ((shift + 0.5) % 1, -1)]:
            margins.append(sign * polarity * float(np.interp(edge, events, current)))
    return TPSMetrics(
        float(np.sum(vb * mean * width)),
        rms,
        float(np.max(abs(current))),
        min(margins),
        1 - d1,
        1 - (d3 - d2),
    )


@dataclass(frozen=True)
class TPSConstraints:
    power_tolerance_fraction: float = 0.005
    peak_current_limit_a: float = 100.0
    min_active_pu: float = 0.02
    min_commutation_current_a: float | None = None

    def validate(self):
        if not 0 < self.power_tolerance_fraction <= 0.1:
            raise ValueError("Power tolerance must be >0 and <=10%")
        if (
            not math.isfinite(self.peak_current_limit_a)
            or self.peak_current_limit_a <= 0
        ):
            raise ValueError("Peak current limit must be finite and positive")
        if not 0 < self.min_active_pu < 1:
            raise ValueError("Minimum active width must be >0 and <1")
        if self.min_commutation_current_a is not None and (
            not math.isfinite(self.min_commutation_current_a)
            or self.min_commutation_current_a < 0
        ):
            raise ValueError(
                "Commutation proxy threshold must be nonnegative or disabled"
            )


def constraint_violations(metrics, target_w, limits):
    reasons = []
    if abs(metrics.power_w - target_w) > max(
        1.0, target_w * limits.power_tolerance_fraction
    ):
        reasons.append("power error exceeds tolerance")
    if metrics.ipeak_a > limits.peak_current_limit_a + 1e-8:
        reasons.append("peak current exceeds limit")
    if (
        min(metrics.primary_active_pu, metrics.secondary_active_pu)
        < limits.min_active_pu - 1e-8
    ):
        reasons.append("active pulse below minimum")
    if (
        limits.min_commutation_current_a is not None
        and metrics.commutation_margin_a < limits.min_commutation_current_a - 1e-7
    ):
        reasons.append("signed commutation-current proxy not satisfied")
    return tuple(reasons)


def quantize_phases(config, phases):
    half_ticks = math.floor(config.tbclk_hz / config.switching_hz + 0.5) / 2
    return tuple(
        float(np.floor(np.asarray(phases) * half_ticks + 0.5)[i] / half_ticks)
        for i in range(3)
    )


@dataclass(frozen=True)
class TPSCandidate:
    method: str
    continuous_phases: tuple[float, float, float]
    phases: tuple[float, float, float]
    metrics: TPSMetrics
    feasible: bool
    violations: tuple[str, ...]


def evaluate_candidate(config, vb_v, target_w, phases, limits, method):
    phases = tuple(map(float, phases))
    quantized = quantize_phases(config, phases)
    metrics = exact_tps_metrics(config, vb_v, quantized)
    reasons = constraint_violations(metrics, target_w, limits)
    return TPSCandidate(method, phases, quantized, metrics, not reasons, reasons)


def lattice_candidate(config, vb_v, target_w, phases, limits, method, *, radius=1):
    """Check a bounded PWM tick neighborhood around a continuous solution."""
    ticks = math.floor(config.tbclk_hz / config.switching_hz + 0.5) / 2
    nearest = np.floor(np.asarray(phases) * ticks + 0.5)
    candidates = []
    for offset in product(range(-radius, radius + 1), repeat=3):
        q = (nearest + offset) / ticks
        if not 0 <= q[0] <= 1 or not 0 <= q[1] <= q[2] <= 1:
            continue
        candidate = evaluate_candidate(config, vb_v, target_w, q, limits, method)
        if candidate.feasible:
            candidates.append(
                TPSCandidate(
                    method,
                    tuple(map(float, phases)),
                    candidate.phases,
                    candidate.metrics,
                    True,
                    (),
                )
            )
    if candidates:
        return min(candidates, key=lambda c: c.metrics.irms_a)
    return evaluate_candidate(config, vb_v, target_w, phases, limits, method)


@dataclass(frozen=True)
class TPSOptimizationResult:
    target_w: float
    vb_v: float
    baseline: TPSCandidate
    grid: TPSCandidate | None
    optimized: TPSCandidate
    limits: TPSConstraints
    timings_s: dict[str, float]
    evaluations: int  # grid + SLSQP calls; excludes PWM verification
    zvs_status: str = "UNKNOWN: ideal-L commutation proxy only"
    model: str = "Exact ideal-L; Cp/deadtime/Coss excluded"

    def to_dict(self):
        return asdict(self)


def optimize_tps(
    config: DABConfig,
    vb_v: float,
    target_w: float,
    limits: TPSConstraints | None = None,
    *,
    grid_points: int = 9,
    initial_seeds=None,
) -> TPSOptimizationResult:
    limits = limits or TPSConstraints()
    config.validate()
    limits.validate()
    if (
        not math.isfinite(target_w)
        or target_w <= 0
        or not math.isfinite(vb_v)
        or vb_v <= 0
    ):
        raise ValueError(
            "Offline optimization requires finite positive target power and VB"
        )
    if type(grid_points) is not int or grid_points != 0 and not 3 <= grid_points <= 25:
        raise ValueError("Grid points must be 0 (disabled) or an integer from 3 to 25")
    command = tps_command(config, vb_v, target_w)
    if command.limited:
        raise ValueError("Requested power exceeds the configured command limit")
    start = time.perf_counter()
    baseline = evaluate_candidate(
        config,
        vb_v,
        target_w,
        (command.d1, command.d2, command.d3),
        limits,
        "reference formula",
    )
    reference_time = time.perf_counter() - start
    evaluations = 0
    grid = None
    seeds = [np.array(baseline.continuous_phases)]
    start = time.perf_counter()
    axis = np.linspace(0, 1 - limits.min_active_pu, grid_points)
    if initial_seeds is not None:
        for seed in initial_seeds:
            exact_tps_metrics(config, vb_v, seed)  # validate before optimization
            seeds.append(np.asarray(seed, dtype=float))
    best_merit = float("inf")
    best_seed = None
    for d1 in axis:
        for d2 in np.linspace(0, 1, grid_points):
            for d3 in np.linspace(
                d2, min(1, d2 + 1 - limits.min_active_pu), grid_points
            ):
                metrics = exact_tps_metrics(config, vb_v, (d1, d2, d3))
                evaluations += 1
                merit = (
                    metrics.irms_a + 1000 * abs(metrics.power_w - target_w) / target_w
                )
                if merit < best_merit:
                    best_merit, best_seed = merit, np.array([d1, d2, d3])
                if not constraint_violations(metrics, target_w, limits):
                    c = evaluate_candidate(
                        config, vb_v, target_w, (d1, d2, d3), limits, "coarse grid"
                    )
                    if c.feasible and (
                        grid is None or c.metrics.irms_a < grid.metrics.irms_a
                    ):
                        grid = c
    grid_time = time.perf_counter() - start
    if best_seed is not None:
        seeds.append(best_seed)
    if grid is not None:
        seeds.append(np.array(grid.continuous_phases))
    seeds += [np.array([0.2, 0.15, 0.45]), np.array([0.5, 0.2, 0.6])]
    start = time.perf_counter()
    candidates = [baseline] + ([grid] if grid is not None else [])
    scale = max(1.0, baseline.metrics.irms_a)

    def physical(x):
        nonlocal evaluations
        evaluations += 1
        # SLSQP may evaluate just outside its inequality-feasible region.
        return exact_tps_metrics(config, vb_v, (x[0], min(x[1], x[2]), max(x[1], x[2])))

    def inequality(x):
        m = physical(x)
        values = [
            x[2] - x[1],
            1 - (x[2] - x[1]) - limits.min_active_pu,
            (limits.peak_current_limit_a - m.ipeak_a) / limits.peak_current_limit_a,
        ]
        if limits.min_commutation_current_a is not None:
            values.append(
                (m.commutation_margin_a - limits.min_commutation_current_a) / scale
            )
        return np.array(values)

    for seed in seeds:
        result = minimize(
            lambda x: physical(x).irms_a / scale,
            seed,
            method="SLSQP",
            bounds=[(0, 1 - limits.min_active_pu), (0, 1), (0, 1)],
            constraints=[
                {
                    "type": "eq",
                    "fun": lambda x: (physical(x).power_w - target_w) / target_w,
                },
                {"type": "ineq", "fun": inequality},
            ],
            options={"maxiter": 180, "ftol": 1e-10},
        )
        if result.success and result.x[1] <= result.x[2]:
            candidates.append(
                lattice_candidate(
                    config,
                    vb_v,
                    target_w,
                    result.x,
                    limits,
                    "multi-start SLSQP + PWM neighborhood",
                )
            )
    feasible = [c for c in candidates if c.feasible]
    if (
        not feasible
        and limits.peak_current_limit_a >= target_w * config.turns_ratio / vb_v
    ):
        # Near low-power SPS, one tick of phase may exceed the entire power
        # tolerance. A wider local pulse-spread search recovers feasible points.
        seed = min(candidates, key=lambda candidate: candidate.metrics.irms_a)
        fallback = lattice_candidate(
            config,
            vb_v,
            target_w,
            seed.continuous_phases,
            limits,
            "expanded PWM neighborhood",
            radius=12,
        )
        if fallback.feasible:
            feasible.append(fallback)
    best = min(feasible, key=lambda c: c.metrics.irms_a) if feasible else baseline
    return TPSOptimizationResult(
        target_w,
        vb_v,
        baseline,
        grid,
        best,
        limits,
        {
            "reference": reference_time,
            "grid": grid_time,
            "constrained_search": time.perf_counter() - start,
        },
        evaluations,
    )

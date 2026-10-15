"""Shared sampled plant/controller loop analysis, reusing LLC margin semantics."""

from dataclasses import dataclass

import numpy as np
from scipy import signal

from llc_design.control.digital_loop import calculate_stability_margins


@dataclass(frozen=True)
class SampledLoop:
    sample_time_s: float
    frequency_hz: np.ndarray
    plant_response: np.ndarray
    controller_response: np.ndarray
    open_response: np.ndarray
    closed_response: np.ndarray
    sensitivity: np.ndarray
    poles: np.ndarray
    plant_b: np.ndarray
    plant_a: np.ndarray
    controller_b: np.ndarray
    controller_a: np.ndarray
    margins: object
    stable: bool

    def summary(self):
        m = self.margins
        return {
            "sample_time_s": self.sample_time_s,
            "stable": self.stable,
            "max_pole_radius": float(max(abs(self.poles), default=0)),
            "phase_margin_deg": m.phase_margin_deg,
            "gain_margin_db": m.gain_margin_db,
            "crossover_hz": m.critical_gain_crossover_hz,
            "plant_b": self.plant_b.tolist(),
            "plant_a": self.plant_a.tolist(),
            "controller_b": self.controller_b.tolist(),
            "controller_a": self.controller_a.tolist(),
            "closed_loop_poles": [[float(p.real), float(p.imag)] for p in self.poles],
        }


def sampled_loop(numerator, denominator, controller, *, delay_ticks=1, points=1200):
    """Exact ZOH plant plus integer sample delay; negative unity feedback."""
    ts = controller.sample_time_s
    if type(delay_ticks) is not int or not 0 <= delay_ticks <= 10:
        raise ValueError("Delay ticks must be an integer from 0 to 10")
    b, a, _ = signal.cont2discrete((numerator, denominator), ts, method="zoh")
    b = np.r_[np.zeros(delay_ticks), b.ravel()]
    return discrete_loop(b, a, controller, points=points)


def discrete_loop(b, a, controller, *, points=1200):
    """Negative-unity loop of a discrete physical plant and digital controller."""
    ts = controller.sample_time_s
    b = np.asarray(b, dtype=float)
    a = np.asarray(a, dtype=float)
    cb = np.asarray(controller.numerator)
    ca = np.asarray(controller.denominator)
    ob = np.convolve(b, cb)
    oa = np.convolve(a, ca)
    n = max(len(ob), len(oa))
    denominator_closed = np.pad(oa, (0, n - len(oa))) + np.pad(ob, (0, n - len(ob)))
    poles = np.roots(denominator_closed)
    f = np.geomspace(0.05, 0.49 / ts, points)
    _, g = signal.freqz(b, a, worN=2 * np.pi * f * ts)
    _, c = signal.freqz(cb, ca, worN=2 * np.pi * f * ts)
    op = g * c
    margins = calculate_stability_margins(f, op)
    return SampledLoop(
        ts,
        f,
        g,
        c,
        op,
        op / (1 + op),
        1 / (1 + op),
        poles,
        b,
        a,
        cb,
        ca,
        margins,
        bool(np.all(abs(poles) < 1)),
    )

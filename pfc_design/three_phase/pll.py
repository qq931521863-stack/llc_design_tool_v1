"""Three-phase SRF, DDSRF and dual-SOGI positive-sequence PLL comparison.

DDSOGI in the UI denotes two SOGIs on alpha/beta plus sequence separation
(standard DSOGI-PLL); it is not an MSOGI harmonic-decoupling bank.
Independent equations; TI SPRABT4A / SDK DDSRF and imperix SOGI notes cross-check.
"""

import math
from dataclasses import dataclass

import numpy as np

KINDS = ("SRF", "DDSRF", "DDSOGI")


@dataclass(frozen=True)
class PLLConfig:
    sample_hz: float = 20000
    nominal_hz: float = 50
    bandwidth_hz: float = 20
    damping: float = 0.707
    minimum_hz: float = 40
    maximum_hz: float = 70
    nominal_peak_v: float = 326.598632371
    initial_angle_deg: float = -30
    lock_error: float = 0.02
    lock_time_s: float = 0.01
    sogi_k: float = math.sqrt(2)

    def validate(self):
        if not all(math.isfinite(v) for v in self.__dict__.values()):
            raise ValueError("Finite PLL parameters required")
        if not 0 < self.minimum_hz < self.nominal_hz < self.maximum_hz:
            raise ValueError("PLL frequency range must contain nominal frequency")
        if (
            min(
                self.sample_hz,
                self.bandwidth_hz,
                self.damping,
                self.nominal_peak_v,
                self.lock_error,
                self.lock_time_s,
                self.sogi_k,
            )
            <= 0
        ):
            raise ValueError("Positive PLL parameters required")
        if self.sample_hz < 40 * max(self.maximum_hz, self.bandwidth_hz):
            raise ValueError("PLL sample rate too low")


class GridPLL:
    def __init__(self, config=None, kind="SRF"):
        config = config or PLLConfig()
        config.validate()
        if kind not in KINDS:
            raise ValueError("Choose SRF, DDSRF or DDSOGI")
        self.config = config
        self.kind = kind
        self.reset()

    def reset(self):
        c = self.config
        self.theta = math.radians(c.initial_angle_deg) % (2 * math.pi)
        self.integral = 0.0
        self.frequency_hz = c.nominal_hz
        self.error = 0.0
        self.lock_count = 0
        self.locked = False
        self.fault = False
        self.lp = np.zeros(4)
        self.previous_decoupled = np.zeros(4)
        self.sogi = np.zeros((2, 2))
        self.previous_input = np.zeros(2)
        self.positive_alpha = 0.0
        self.positive_beta = 0.0

    def step(self, abc):
        c = self.config
        v = np.asarray(abc, dtype=float)
        if v.shape != (3,) or not np.isfinite(v).all():
            self.reset()
            self.fault = True
            return self.theta
        self.fault = False
        theta = self.theta
        alpha = (2 * v[0] - v[1] - v[2]) / 3
        beta = (v[1] - v[2]) / math.sqrt(3)
        cs, sn = math.cos(theta), math.sin(theta)
        d = alpha * cs + beta * sn
        q = -alpha * sn + beta * cs
        if self.kind == "DDSRF":
            dn = alpha * cs - beta * sn
            qn = alpha * sn + beta * cs
            c2, s2 = math.cos(2 * theta), math.sin(2 * theta)
            pd, pq, nd, nq = self.lp
            dec = np.array(
                [
                    d - nd * c2 - nq * s2,
                    q + nd * s2 - nq * c2,
                    dn - pd * c2 + pq * s2,
                    qn - pd * s2 - pq * c2,
                ]
            )
            wc = 2 * math.pi * self.frequency_hz / math.sqrt(2)
            h = wc / (2 * c.sample_hz)
            self.lp = ((1 - h) * self.lp + h * (dec + self.previous_decoupled)) / (
                1 + h
            )
            self.previous_decoupled = dec
            d, q = dec[:2]
        elif self.kind == "DDSOGI":
            a = math.pi * self.frequency_hz / c.sample_hz
            k = c.sogi_k
            denominator = 1 + k * a + a * a
            for j, value in enumerate((alpha, beta)):
                u, z = self.sogi[j]
                total = value + self.previous_input[j]
                self.sogi[j] = (
                    (1 - k * a - a * a) * u - 2 * a * z + k * a * total,
                    2 * a * u + (1 + k * a - a * a) * z + k * a * a * total,
                )
                self.sogi[j] /= denominator
                self.previous_input[j] = value
            alpha = (self.sogi[0, 0] - self.sogi[1, 1]) / 2
            beta = (self.sogi[0, 1] + self.sogi[1, 0]) / 2
            d = alpha * cs + beta * sn
            q = -alpha * sn + beta * cs
        self.positive_alpha = d * cs - q * sn
        self.positive_beta = d * sn + q * cs
        amplitude = math.hypot(d, q)
        if amplitude > 0.2 * c.nominal_peak_v:
            self.error = q / amplitude
            wn = 2 * math.pi * c.bandwidth_hz
            candidate = self.integral + wn * wn * self.error / c.sample_hz
            raw = (
                2 * math.pi * c.nominal_hz + 2 * c.damping * wn * self.error + candidate
            )
            omega = float(
                np.clip(raw, 2 * math.pi * c.minimum_hz, 2 * math.pi * c.maximum_hz)
            )
            self.integral = candidate + omega - raw
            self.frequency_hz = omega / (2 * math.pi)
            qualifies = d > 0 and abs(self.error) < c.lock_error
            self.lock_count = self.lock_count + 1 if qualifies else 0
        else:
            self.lock_count = 0
            self.error = 0
        self.locked = self.lock_count >= math.ceil(c.lock_time_s * c.sample_hz)
        self.theta = (theta + 2 * math.pi * self.frequency_hz / c.sample_hz) % (
            2 * math.pi
        )
        return theta


@dataclass(frozen=True)
class PLLScenario:
    duration_s: float = 0.5
    disturbance_s: float = 0.2
    after_hz: float = 52
    phase_step_deg: float = 20
    negative_sequence: float = 0.15
    fifth_harmonic: float = 0.05
    seventh_harmonic: float = 0.03
    sag_ratio: float = 0.8


def compare_plls(config=None, scenario=None):
    config = config or PLLConfig()
    scenario = scenario or PLLScenario()
    config.validate()
    if (
        not all(math.isfinite(v) for v in scenario.__dict__.values())
        or not 0 < scenario.disturbance_s < scenario.duration_s
        or scenario.duration_s * config.sample_hz > 100000
        or not 0 < scenario.sag_ratio <= 1
        or min(
            scenario.negative_sequence,
            scenario.fifth_harmonic,
            scenario.seventh_harmonic,
        )
        < 0
    ):
        raise ValueError("Invalid PLL comparison scenario")
    from .model import dq_to_abc

    t = np.arange(round(scenario.duration_s * config.sample_hz)) / config.sample_hz
    theta = (
        2
        * math.pi
        * (
            config.nominal_hz * t
            + (scenario.after_hz - config.nominal_hz)
            * np.maximum(t - scenario.disturbance_s, 0)
        )
    )
    theta += np.where(
        t >= scenario.disturbance_s, math.radians(scenario.phase_step_deg), 0
    )
    abc = np.zeros((len(t), 3))
    for i, (time_s, phase) in enumerate(zip(t, theta)):
        disturbed = time_s >= scenario.disturbance_s
        scale = config.nominal_peak_v * (scenario.sag_ratio if disturbed else 1)
        abc[i] = dq_to_abc(scale, 0, phase)
        if disturbed:
            abc[i] += dq_to_abc(scale * scenario.negative_sequence, 0, -phase)
            abc[i] += dq_to_abc(scale * scenario.fifth_harmonic, 0, -5 * phase)
            abc[i] += dq_to_abc(scale * scenario.seventh_harmonic, 0, 7 * phase)
    results = {}
    metrics = {}
    for kind in KINDS:
        pll = GridPLL(config, kind)
        out = np.zeros((len(t), 7))
        for i, v in enumerate(abc):
            angle = pll.step(v)
            out[i] = (
                angle,
                pll.frequency_hz,
                pll.error,
                float(pll.locked),
                pll.positive_alpha,
                pll.positive_beta,
                float(pll.fault),
            )
        error = np.angle(np.exp(1j * (out[:, 0] - theta)))
        tail = t > scenario.duration_s - 0.08
        qualifies = (abs(error) < config.lock_error) & (out[:, 3] > 0)

        def lock_delay(start, qualifies=qualifies):
            begin = np.searchsorted(t, start)
            n = math.ceil(config.lock_time_s * config.sample_hz)
            for j in range(begin, len(t) - n):
                if np.all(qualifies[j : j + n]):
                    return float(t[j] - start)
            return None

        metrics[kind] = {
            "initial_lock_s": lock_delay(0),
            "relock_s": lock_delay(scenario.disturbance_s),
            "tail_phase_rms_deg": float(
                np.sqrt(np.mean(error[tail] ** 2)) * 180 / math.pi
            ),
            "tail_frequency_ripple_pp_hz": float(np.ptp(out[tail, 1])),
            "tail_locked_fraction": float(np.mean(out[tail, 3])),
        }
        results[kind] = {
            "theta_rad": out[:, 0],
            "frequency_hz": out[:, 1],
            "error": out[:, 2],
            "locked": out[:, 3],
            "positive_alpha_v": out[:, 4],
            "positive_beta_v": out[:, 5],
            "phase_error_rad": error,
        }
    return {
        "time_s": t,
        "voltage_abc": abc,
        "true_theta_rad": theta,
        "results": results,
        "metrics": metrics,
    }

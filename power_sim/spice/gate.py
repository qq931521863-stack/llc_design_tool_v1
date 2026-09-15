"""Phase-continuous LLC gate generation for shared-ngspice external sources.

ngspice may retry a transient step or query an external source at non-uniform
Times.  Gate generation therefore cannot rely on a mutable 'advance phase by
last dt' callback.  Instead switching-frequency changes are stored as a
piecewise timeline; phase is a deterministic function of absolute simulation
time, so repeated/out-of-order evaluations return identical values.
"""
from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass
import math


_TWO_PI = 2.0 * math.pi


@dataclass(frozen=True)
class FrequencySegment:
    start_time_s: float
    phase_start_rad: float
    frequency_hz: float


class FrequencyTimeline:
    def __init__(self, initial_frequency_hz: float, *, initial_time_s: float = 0.0, initial_phase_rad: float = 0.0):
        if initial_frequency_hz <= 0.0:
            raise ValueError("initial switching frequency must be positive")
        if initial_time_s < 0.0:
            raise ValueError("initial_time_s cannot be negative")
        self._segments: list[FrequencySegment] = [
            FrequencySegment(float(initial_time_s), float(initial_phase_rad) % _TWO_PI, float(initial_frequency_hz))
        ]

    @property
    def segments(self) -> tuple[FrequencySegment, ...]:
        return tuple(self._segments)

    def _index(self, time_s: float) -> int:
        t = float(time_s)
        starts = [segment.start_time_s for segment in self._segments]
        return max(0, bisect_right(starts, t) - 1)

    def frequency_at(self, time_s: float) -> float:
        return self._segments[self._index(time_s)].frequency_hz

    def phase_at(self, time_s: float) -> float:
        t = float(time_s)
        segment = self._segments[self._index(t)]
        dt = t - segment.start_time_s
        return (segment.phase_start_rad + _TWO_PI * segment.frequency_hz * dt) % _TWO_PI

    def set_frequency(self, time_s: float, frequency_hz: float) -> None:
        t = float(time_s)
        f = float(frequency_hz)
        if f <= 0.0:
            raise ValueError("switching frequency must be positive")
        last = self._segments[-1]
        if t < last.start_time_s - 1e-15:
            raise ValueError("frequency commands must be appended in nondecreasing time order")
        phase = self.phase_at(t)
        if abs(t - last.start_time_s) <= 1e-15:
            self._segments[-1] = FrequencySegment(t, last.phase_start_rad, f)
        elif math.isclose(f, last.frequency_hz, rel_tol=0.0, abs_tol=1e-12):
            return
        else:
            self._segments.append(FrequencySegment(t, phase, f))


class LLCFullBridgeGateScheduler:
    """Complementary full-bridge gates with symmetric deadtime.

    Positive bridge state: AH + BL on.
    Negative bridge state: AL + BH on.
    Around each half-cycle transition all four switches are off for deadtime.
    """

    _POSITIVE = {"vgah", "gah", "vgbl", "gbl"}
    _NEGATIVE = {"vgal", "gal", "vgbh", "gbh"}

    def __init__(self, timeline: FrequencyTimeline, *, deadtime_s: float, gate_high_v: float = 5.0):
        if deadtime_s < 0.0:
            raise ValueError("deadtime_s cannot be negative")
        if gate_high_v <= 0.0:
            raise ValueError("gate_high_v must be positive")
        self.timeline = timeline
        self.deadtime_s = float(deadtime_s)
        self.gate_high_v = float(gate_high_v)

    @staticmethod
    def _normalize_source_name(name: str) -> str:
        text = str(name).strip().lower()
        # Shared-ngspice may present source names with a leading V exactly as in
        # the netlist; preserve both aliases in the lookup sets above.
        return text

    def gate_value(self, time_s: float, source_name: str) -> float:
        name = self._normalize_source_name(source_name)
        if name not in self._POSITIVE and name not in self._NEGATIVE:
            raise KeyError(f"unknown LLC gate external source {source_name!r}")

        frequency = self.timeline.frequency_at(time_s)
        phase = self.timeline.phase_at(time_s)
        dead_angle = min(self.deadtime_s * frequency * _TWO_PI, math.pi * 0.9)
        half_dead = dead_angle / 2.0

        positive_on = half_dead <= phase < math.pi - half_dead
        negative_on = math.pi + half_dead <= phase < _TWO_PI - half_dead
        if name in self._POSITIVE:
            return self.gate_high_v if positive_on else 0.0
        return self.gate_high_v if negative_on else 0.0


__all__ = ["FrequencySegment", "FrequencyTimeline", "LLCFullBridgeGateScheduler"]

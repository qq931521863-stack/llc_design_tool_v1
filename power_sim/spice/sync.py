"""Transient step synchronization for shared-ngspice digital power co-sim."""
from __future__ import annotations

from dataclasses import dataclass
import math

from .gate import FrequencyTimeline


_TWO_PI = 2.0 * math.pi


@dataclass(frozen=True)
class SyncEvent:
    time_s: float
    kind: str


class LLCCoSimulationSynchronizer:
    """Limit ngspice steps so accepted points land on controller/PWM events.

    External gate values are discontinuous at deadtime boundaries.  If ngspice
    takes a large step across an edge, the solver can miss the exact commutation
    time even though the source callback itself is deterministic.  The shared
    API's GetSyncData callback allows the host to reduce delta to the next gate,
    controller-sample, or scheduled frequency-change event.
    """

    def __init__(
        self,
        timeline: FrequencyTimeline,
        *,
        sample_time_s: float,
        sample_phase_s: float = 0.0,
        deadtime_s: float = 0.0,
        minimum_delta_s: float = 1e-12,
    ):
        if sample_time_s <= 0.0:
            raise ValueError("sample_time_s must be positive")
        if sample_phase_s < 0.0 or sample_phase_s >= sample_time_s:
            raise ValueError("sample_phase_s must lie inside one sample interval")
        if deadtime_s < 0.0:
            raise ValueError("deadtime_s cannot be negative")
        if minimum_delta_s <= 0.0:
            raise ValueError("minimum_delta_s must be positive")
        self.timeline = timeline
        self.sample_time_s = float(sample_time_s)
        self.sample_phase_s = float(sample_phase_s)
        self.deadtime_s = float(deadtime_s)
        self.minimum_delta_s = float(minimum_delta_s)

    def next_sample_time(self, time_s: float) -> float:
        t = float(time_s)
        phase = self.sample_phase_s
        ts = self.sample_time_s
        if t < phase - self.minimum_delta_s:
            return phase
        k = math.floor((t - phase) / ts + 1e-12) + 1
        return phase + k * ts

    def next_frequency_change(self, time_s: float) -> float | None:
        t = float(time_s)
        for segment in self.timeline.segments:
            if segment.start_time_s > t + self.minimum_delta_s:
                return segment.start_time_s
        return None

    def next_gate_transition(self, time_s: float) -> float:
        t = float(time_s)
        f = self.timeline.frequency_at(t)
        phase = self.timeline.phase_at(t)
        dead_angle = min(self.deadtime_s * f * _TWO_PI, math.pi * 0.9)
        hd = dead_angle / 2.0
        edges = (hd, math.pi - hd, math.pi + hd, _TWO_PI - hd)
        best_dphi = _TWO_PI
        phase_eps = max(_TWO_PI * f * self.minimum_delta_s, 1e-12)
        for edge in edges:
            dphi = (edge - phase) % _TWO_PI
            if dphi <= phase_eps:
                dphi += _TWO_PI
            if dphi < best_dphi:
                best_dphi = dphi
        return t + best_dphi / (_TWO_PI * f)

    def next_event(self, time_s: float) -> SyncEvent:
        candidates = [
            SyncEvent(self.next_sample_time(time_s), "control_sample"),
            SyncEvent(self.next_gate_transition(time_s), "gate_edge"),
        ]
        change = self.next_frequency_change(time_s)
        if change is not None:
            candidates.append(SyncEvent(change, "frequency_change"))
        return min(candidates, key=lambda event: event.time_s)

    def constrain_delta(self, time_s: float, proposed_delta_s: float) -> float:
        proposed = float(proposed_delta_s)
        if proposed <= 0.0:
            return proposed
        event = self.next_event(time_s)
        gap = event.time_s - float(time_s)
        if gap <= self.minimum_delta_s:
            return min(proposed, self.minimum_delta_s)
        return min(proposed, gap)


__all__ = ["SyncEvent", "LLCCoSimulationSynchronizer"]

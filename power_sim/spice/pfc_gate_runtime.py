"""Deterministic TTPL gate scheduling driven by the firmware zero-cross state.

The original shared-ngspice V1 scheduler inferred line polarity directly from
an ideal sine source and simply blanked all gates in a small voltage window.
This module instead consumes the control-rate eight-state TTPL commutation
machine through an absolute-time timeline, so ngspice retries/out-of-order
external-source evaluations remain deterministic.

States 2/3 and 6/7 represent the full-deadband zero-cross intervals and command
all four gates off.  States 4/8 model the deadband soft-start by gradually
opening the HF PWM windows while the LF leg remains off.  This reproduces the
software state semantics; detailed ePWM DBRED/DBFED/TBCLK register timing is a
separate hardware-fidelity layer.
"""
from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass
import math

from .pfc import DutyTimeline, TTPLSpiceConfig


@dataclass(frozen=True)
class TTPLCommutationSegment:
    start_time_s: float
    state_code: int
    lf_state: int
    hf_softstart_fraction: float
    target_polarity: int


class TTPLCommutationTimeline:
    def __init__(
        self,
        *,
        initial_state_code: int = 1,
        initial_lf_state: int = 1,
        initial_hf_softstart_fraction: float = 1.0,
        initial_target_polarity: int = 1,
    ) -> None:
        self._segments: list[TTPLCommutationSegment] = [
            TTPLCommutationSegment(
                0.0,
                int(initial_state_code),
                int(initial_lf_state),
                float(initial_hf_softstart_fraction),
                1 if initial_target_polarity >= 0 else -1,
            )
        ]

    @property
    def segments(self) -> tuple[TTPLCommutationSegment, ...]:
        return tuple(self._segments)

    def command_at(self, time_s: float) -> TTPLCommutationSegment:
        t = float(time_s)
        starts = [segment.start_time_s for segment in self._segments]
        index = max(0, bisect_right(starts, t) - 1)
        return self._segments[index]

    def set_command(
        self,
        time_s: float,
        *,
        state_code: int,
        lf_state: int,
        hf_softstart_fraction: float,
        target_polarity: int,
    ) -> None:
        t = float(time_s)
        soft = min(max(float(hf_softstart_fraction), 0.0), 1.0)
        new = TTPLCommutationSegment(
            t,
            int(state_code),
            int(lf_state),
            soft,
            1 if target_polarity >= 0 else -1,
        )
        last = self._segments[-1]
        if t < last.start_time_s - 1e-15:
            raise ValueError("commutation commands must be appended in nondecreasing time order")
        if abs(t - last.start_time_s) <= 1e-15:
            self._segments[-1] = new
        elif (
            new.state_code == last.state_code
            and new.lf_state == last.lf_state
            and math.isclose(new.hf_softstart_fraction, last.hf_softstart_fraction, abs_tol=1e-15)
            and new.target_polarity == last.target_polarity
        ):
            return
        else:
            self._segments.append(new)


class TTPLFirmwareGateScheduler:
    """Four-switch gates driven by duty + firmware commutation timelines."""

    _HF_HIGH = {"vghfh", "ghfh", "vhfhigh", "hfhigh"}
    _HF_LOW = {"vghfl", "ghfl", "vhflow", "hflow"}
    _LF_POS = {"vglfp", "glfp", "vlfpos", "lfpos"}
    _LF_NEG = {"vglfn", "glfn", "vlfneg", "lfneg"}

    def __init__(
        self,
        config: TTPLSpiceConfig,
        duty_timeline: DutyTimeline,
        commutation_timeline: TTPLCommutationTimeline,
    ) -> None:
        config.validate()
        self.config = config
        self.duty_timeline = duty_timeline
        self.commutation_timeline = commutation_timeline

    @staticmethod
    def _scaled_window(value: float, start: float, end: float, scale: float) -> bool:
        if end <= start or scale <= 0.0:
            return False
        if scale >= 1.0:
            return start <= value < end
        center = 0.5 * (start + end)
        half = 0.5 * (end - start) * scale
        return center - half <= value < center + half

    def _pwm_windows(self, time_s: float, softstart: float) -> tuple[bool, bool, tuple[float, ...]]:
        period = self.config.switching_period_s
        tau = float(time_s) % period
        duty = self.duty_timeline.duty_at(time_s)
        dead = min(self.config.deadtime_s, 0.2 * period)
        half_dead = 0.5 * dead
        transition = duty * period
        active_start = half_dead
        active_end = max(transition - half_dead, half_dead)
        sync_start = min(transition + half_dead, period)
        sync_end = period - half_dead
        active_on = self._scaled_window(tau, active_start, active_end, softstart)
        sync_on = self._scaled_window(tau, sync_start, sync_end, softstart)

        def scaled_edges(start: float, end: float) -> tuple[float, float]:
            if end <= start or softstart <= 0.0:
                return (start, start)
            if softstart >= 1.0:
                return (start, end)
            center = 0.5 * (start + end)
            half = 0.5 * (end - start) * softstart
            return center - half, center + half

        a0, a1 = scaled_edges(active_start, active_end)
        s0, s1 = scaled_edges(sync_start, sync_end)
        return active_on, sync_on, (a0, a1, s0, s1, period)

    def gate_value(self, time_s: float, source_name: str) -> float:
        name = str(source_name).strip().lower()
        known = self._HF_HIGH | self._HF_LOW | self._LF_POS | self._LF_NEG
        if name not in known:
            raise KeyError(f"unknown TTPL gate external source {source_name!r}")

        command = self.commutation_timeline.command_at(time_s)
        state = command.state_code
        # Full blanking intervals around the true zero crossing.
        if state in (2, 3, 6, 7):
            return 0.0

        if state in (4, 8):
            positive = command.target_polarity > 0
            lf_state = 0
            softstart = command.hf_softstart_fraction
        elif state == 1:
            positive = True
            lf_state = 1
            softstart = 1.0
        elif state == 5:
            positive = False
            lf_state = -1
            softstart = 1.0
        else:
            return 0.0

        active_on, sync_on, _ = self._pwm_windows(time_s, softstart)
        on = False
        if positive:
            if name in self._HF_LOW:
                on = active_on
            elif name in self._HF_HIGH:
                on = sync_on
            elif name in self._LF_NEG:
                on = lf_state > 0
        else:
            if name in self._HF_HIGH:
                on = active_on
            elif name in self._HF_LOW:
                on = sync_on
            elif name in self._LF_POS:
                on = lf_state < 0
        return self.config.gate_high_v if on else 0.0

    def next_pwm_event_after(self, time_s: float) -> float:
        t = float(time_s)
        period = self.config.switching_period_s
        base = math.floor(t / period) * period
        command = self.commutation_timeline.command_at(t)
        if command.state_code in (2, 3, 6, 7):
            return base + period if base + period > t + 1e-15 else base + 2.0 * period
        _, _, relative_edges = self._pwm_windows(t, command.hf_softstart_fraction)
        eps = max(1e-15, period * 1e-10)
        future = [base + edge for edge in relative_edges if base + edge > t + eps]
        if future:
            return min(future)
        return base + period + relative_edges[0]


__all__ = [
    "TTPLCommutationSegment",
    "TTPLCommutationTimeline",
    "TTPLFirmwareGateScheduler",
]

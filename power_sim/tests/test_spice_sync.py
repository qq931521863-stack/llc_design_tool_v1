from __future__ import annotations

from power_sim.spice.gate import FrequencyTimeline
from power_sim.spice.sync import LLCCoSimulationSynchronizer


def test_sync_lands_on_control_sample_before_later_gate_event():
    timeline = FrequencyTimeline(100_000.0)
    sync = LLCCoSimulationSynchronizer(
        timeline,
        sample_time_s=25e-6,
        sample_phase_s=2e-6,
        deadtime_s=200e-9,
    )
    event = sync.next_event(1.0e-6)
    assert event.kind == "control_sample"
    assert abs(event.time_s - 2.0e-6) < 1e-15
    assert abs(sync.constrain_delta(1.0e-6, 10e-6) - 1.0e-6) < 1e-15


def test_sync_sees_scheduled_frequency_change_as_hard_event():
    timeline = FrequencyTimeline(100_000.0)
    timeline.set_frequency(7.0e-6, 150_000.0)
    sync = LLCCoSimulationSynchronizer(
        timeline,
        sample_time_s=25e-6,
        sample_phase_s=20e-6,
        deadtime_s=0.0,
    )
    # At 6.5 us the next PWM edge is 10 us, control tick 20 us, but the
    # already-scheduled frequency update at 7 us must be hit first.
    event = sync.next_event(6.5e-6)
    assert event.kind == "frequency_change"
    assert abs(event.time_s - 7.0e-6) < 1e-15


def test_sync_reduces_step_to_deadtime_gate_boundary():
    timeline = FrequencyTimeline(100_000.0)
    sync = LLCCoSimulationSynchronizer(
        timeline,
        sample_time_s=100e-6,
        sample_phase_s=90e-6,
        deadtime_s=200e-9,
    )
    # First positive gate turn-on occurs at deadtime/2 = 100 ns.
    event = sync.next_event(0.0)
    assert event.kind == "gate_edge"
    assert abs(event.time_s - 100e-9) < 1e-15
    assert abs(sync.constrain_delta(0.0, 2e-6) - 100e-9) < 1e-15

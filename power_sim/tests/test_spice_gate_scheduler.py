from __future__ import annotations

import math

from power_sim.spice import FrequencyTimeline, LLCFullBridgeGateScheduler


def test_frequency_timeline_preserves_phase_across_command_change():
    timeline = FrequencyTimeline(100_000.0)
    t_change = 37.25e-6
    phase_before = timeline.phase_at(t_change)
    timeline.set_frequency(t_change, 150_000.0)
    phase_after = timeline.phase_at(t_change)
    assert abs(phase_after - phase_before) < 1e-12

    dt = 1.0e-6
    expected = (phase_before + 2.0 * math.pi * 150_000.0 * dt) % (2.0 * math.pi)
    assert abs(timeline.phase_at(t_change + dt) - expected) < 1e-12


def test_gate_callbacks_are_deterministic_for_repeated_ngspice_queries():
    timeline = FrequencyTimeline(100_000.0)
    gates = LLCFullBridgeGateScheduler(timeline, deadtime_s=200e-9, gate_high_v=5.0)

    t = 2.0e-6
    first = gates.gate_value(t, "VGAH")
    second = gates.gate_value(t, "VGAH")
    assert first == second == 5.0
    assert gates.gate_value(t, "VGAL") == 0.0
    assert gates.gate_value(t, "VGBL") == 5.0
    assert gates.gate_value(t, "VGBH") == 0.0


def test_full_bridge_deadtime_turns_all_switches_off_around_transition():
    frequency = 100_000.0
    deadtime = 200e-9
    timeline = FrequencyTimeline(frequency)
    gates = LLCFullBridgeGateScheduler(timeline, deadtime_s=deadtime)
    half_period = 0.5 / frequency

    # At the exact half-cycle boundary both diagonal pairs must be off.
    for source in ("VGAH", "VGAL", "VGBH", "VGBL"):
        assert gates.gate_value(half_period, source) == 0.0

    # Safely beyond half deadtime the negative bridge state is active.
    t = half_period + deadtime
    assert gates.gate_value(t, "VGAL") == 5.0
    assert gates.gate_value(t, "VGBH") == 5.0
    assert gates.gate_value(t, "VGAH") == 0.0
    assert gates.gate_value(t, "VGBL") == 0.0

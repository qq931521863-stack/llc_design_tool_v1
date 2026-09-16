from __future__ import annotations

import numpy as np
import pytest

from pfc_design.control import PFCControlLabConfig, build_pfc_control_lab_analysis
from pfc_design.control.handoff import build_pfc_control_handoff
from power_sim.spice import (
    DutyTimeline,
    TTPLClosedLoopScenario,
    TTPLGateScheduler,
    TTPLSharedNgSpiceConfig,
    TTPLSpiceConfig,
    build_ttpl_pfc_circuit,
    find_ngspice_shared_library,
    render_netlist,
    run_ttpl_shared_closed_loop,
)


def test_ttpl_spice_circuit_contains_full_totem_pole_and_external_gates():
    cfg = PFCControlLabConfig()
    spice = TTPLSpiceConfig.from_power_stage(cfg.power_stage)
    circuit = build_ttpl_pfc_circuit(spice)
    text = render_netlist(circuit).lower()

    assert "vac line neutral sin(" in text
    for gate in ("vghfh", "vghfl", "vglfp", "vglfn"):
        assert gate in text
        assert "external" in text
    for switch in ("shfh", "shfl", "slfp", "slfn"):
        assert switch in text
    for diode in ("dhfh", "dhfl", "dlfp", "dlfn"):
        assert diode in text
    assert "lboost" in text
    assert "cbus" in text


def test_ttpl_gate_scheduler_swaps_hf_roles_with_line_polarity():
    cfg = PFCControlLabConfig()
    spice = TTPLSpiceConfig.from_power_stage(cfg.power_stage, input_phase_deg=90.0)
    timeline = DutyTimeline(0.25)
    gates = TTPLGateScheduler(spice, timeline)
    period = spice.switching_period_s

    # At +line peak and inside the active interval: HF low + LF negative are on.
    t_pos = 0.10 * period
    assert gates.line_voltage(t_pos) > 0.0
    assert gates.gate_value(t_pos, "VGHFL") > 0.0
    assert gates.gate_value(t_pos, "VGHFH") == 0.0
    assert gates.gate_value(t_pos, "VGLFN") > 0.0
    assert gates.gate_value(t_pos, "VGLFP") == 0.0

    # Half a line later the input is negative; HF high becomes the active switch.
    t_neg_base = 0.5 / spice.line_frequency_hz
    # Keep the same PWM phase while moving to the negative half-cycle.
    cycles = round((t_neg_base - t_pos) / period)
    t_neg = t_pos + cycles * period
    if gates.line_voltage(t_neg) >= 0.0:
        t_neg += period
    assert gates.line_voltage(t_neg) < 0.0
    assert gates.gate_value(t_neg, "VGHFH") > 0.0
    assert gates.gate_value(t_neg, "VGHFL") == 0.0
    assert gates.gate_value(t_neg, "VGLFP") > 0.0
    assert gates.gate_value(t_neg, "VGLFN") == 0.0


def test_ttpl_shared_ngspice_consumes_handoff_coefficients_without_redigitizing_if_library_installed():
    library = find_ngspice_shared_library()
    if library is None:
        pytest.skip("shared libngspice is not installed")

    cfg = PFCControlLabConfig()
    analysis = build_pfc_control_lab_analysis(cfg)
    handoff = build_pfc_control_handoff(analysis)
    sim = TTPLSharedNgSpiceConfig(
        duration_s=140e-6,
        input_phase_deg=90.0,
        output_step_s=0.20e-6,
        max_step_s=0.08e-6,
        wall_timeout_s=120.0,
    )
    result = run_ttpl_shared_closed_loop(
        analysis,
        handoff=handoff,
        scenario=TTPLClosedLoopScenario(cfg.power_stage.bus_voltage_v),
        simulation=sim,
        library=library,
    )

    assert len(result.samples) >= 6
    times = np.asarray([sample.time_s for sample in result.samples])
    assert np.all(np.diff(times) > 0.0)
    assert result.vectors["time"].size > 100
    assert result.vectors["v(bus)"].size == result.vectors["time"].size
    assert np.all(np.isfinite(result.vectors["v(bus)"]))
    assert np.all(np.isfinite(result.vectors["i(lboost)"]))

    assert result.metadata["exact_hz_match_checked"] is True
    assert result.metadata["controller_re_discretized"] is False
    assert tuple(result.metadata["current_b"]) == handoff.current.b
    assert tuple(result.metadata["current_a"]) == handoff.current.a
    assert tuple(result.metadata["voltage_b"]) == handoff.voltage.b
    assert tuple(result.metadata["voltage_a"]) == handoff.voltage.a
    assert result.metrics.peak_inductor_current_a >= 0.0

from dataclasses import replace

import numpy as np
import pytest

from dab_design.control import (
    DABVoltageControlConfig,
    build_dab_voltage_loop,
    simulate_dab_voltage_loop,
)
from dab_design.core import DABConfig, periodic_waveform, tps_command
from dab_design.optimization import TPSConstraints, exact_tps_metrics, optimize_tps


@pytest.mark.parametrize("vb,power", [(300, 700), (400, 3500), (500, 6000)])
def test_exact_integrals_agree_with_independent_sampled_switch_waveform(vb, power):
    c = DABConfig(wave_samples=65536)
    command = tps_command(c, vb, power)
    exact = exact_tps_metrics(c, vb, (command.d1, command.d2, command.d3))
    sampled = periodic_waveform(c, vb, command, include_cp=False)
    assert exact.power_w == pytest.approx(sampled.metrics["pout_w"], rel=0.001)
    assert exact.irms_a == pytest.approx(sampled.metrics["irms_a"], rel=0.001)
    assert exact.ipeak_a == pytest.approx(sampled.metrics["ipeak_a"], rel=0.001)


def test_optimizer_checks_pwm_rounding_and_impossible_peak_constraints():
    c = DABConfig()
    r = optimize_tps(c, 400, 3500, grid_points=3)
    assert r.optimized.feasible
    m = exact_tps_metrics(c, 400, r.optimized.phases)
    assert abs(m.power_w - 3500) <= 17.5
    assert np.allclose(
        np.array(r.optimized.phases) * (c.tbclk_hz / c.switching_hz / 2),
        np.round(np.array(r.optimized.phases) * (c.tbclk_hz / c.switching_hz / 2)),
    )
    impossible = optimize_tps(
        c, 400, 3500, TPSConstraints(peak_current_limit_a=0.1), grid_points=3
    )
    assert not impossible.optimized.feasible
    assert "peak current exceeds limit" in impossible.optimized.violations


def test_dab_power_to_voltage_linearization_matches_energy_equation():
    c = DABConfig()
    ctrl = DABVoltageControlConfig()
    loop, model = build_dab_voltage_loop(c, 400, 3500, ctrl)
    assert model["dpout_dpcmd"] == pytest.approx(1, abs=1e-6)
    assert model["dpout_dvout_w_per_v"] == pytest.approx(0, abs=1e-6)
    assert model["plant_numerator"] == pytest.approx(
        [1 / (c.output_capacitance_f * 400)]
    )
    assert model["plant_denominator"] == pytest.approx(
        [1, 2 * 3500 / (c.output_capacitance_f * 400**2)]
    )
    assert loop.stable and loop.margins.phase_margin_deg > 45
    _, cpl = build_dab_voltage_loop(
        c, 400, 3500, replace(ctrl, load_model="constant_power")
    )
    assert cpl["plant_denominator"][1] == pytest.approx(0, abs=1e-6)
    unstable, _ = build_dab_voltage_loop(c, 400, 3500, replace(ctrl, kp_w_per_v=30000))
    assert not unstable.stable


def test_dab_nonlinear_closed_loop_tracks_reference_and_load_with_energy_balance():
    t, data, metrics, _ = simulate_dab_voltage_loop(
        DABConfig(), 400, 3500, DABVoltageControlConfig()
    )
    assert abs(metrics["final_error_v"]) < 0.01
    assert metrics["max_energy_error_j"] < 1e-8
    assert np.max(abs(data["vout_v"] - data["vref_v"])) < 10
    assert data["pcmd_w"][-1] > data["pcmd_w"][0]
    assert len(t) == len(data["vout_v"])

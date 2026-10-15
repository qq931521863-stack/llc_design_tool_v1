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


def test_power_tolerance_has_no_hidden_one_watt_floor():
    from dab_design.optimization import TPSMetrics, constraint_violations

    m = TPSMetrics(0, 0, 0, 0, 1, 1)
    assert "power error exceeds tolerance" in constraint_violations(
        m, 1, TPSConstraints()
    )
    m = replace(m, power_w=99.25)
    assert "power error exceeds tolerance" in constraint_violations(
        m, 100, TPSConstraints()
    )


@pytest.mark.parametrize(
    "field",
    (
        "power_w",
        "irms_a",
        "ipeak_a",
        "commutation_margin_a",
        "primary_active_pu",
        "secondary_active_pu",
    ),
)
@pytest.mark.parametrize("invalid", (float("nan"), float("inf")))
def test_nonfinite_physics_can_never_be_feasible(field, invalid):
    from dab_design.optimization import TPSMetrics, constraint_violations

    metrics = replace(TPSMetrics(1000, 10, 15, 1, 0.8, 0.8), **{field: invalid})
    assert constraint_violations(metrics, 1000, TPSConstraints()) == (
        "nonfinite physical metrics",
    )


def test_odd_pwm_period_keeps_phase_endpoints_in_domain():
    from dab_design.optimization import evaluate_candidate, quantize_phases

    c = replace(DABConfig(), tbclk_hz=100.08e6)  # 1251 ticks, half-period 625.5
    c.validate()
    phases = quantize_phases(c, (0, 0.5, 1))
    assert 0 <= phases[0] <= 1 and 0 <= phases[1] <= phases[2] <= 1
    assert phases[2] == pytest.approx(625 / 625.5)
    assert phases == quantize_phases(c, phases)
    assert np.allclose(np.array(phases) * 625.5, np.round(np.array(phases) * 625.5))
    evaluate_candidate(c, 400, 1000, (0, 0.5, 1), TPSConstraints(), "endpoint")


def test_solver_cache_is_local_to_each_physical_configuration():
    first = optimize_tps(DABConfig(), 400, 3500, grid_points=3)
    c = replace(DABConfig(), inductance_h=60e-6)
    second = optimize_tps(c, 400, 3500, grid_points=3)
    assert first.solver_cache_hits > 0 and second.solver_cache_hits > 0
    assert second.optimized.feasible
    expected = exact_tps_metrics(c, 400, second.optimized.phases)
    assert second.optimized.metrics == expected
    assert first.optimized.phases != second.optimized.phases


def independent_lc_power(c, vb, phases):
    """Independent scipy matrix exponential periodic solution for the same circuit."""
    from scipy.linalg import expm

    a = np.array(
        [
            [-c.series_resistance_ohm / c.inductance_h, -1 / c.inductance_h],
            [1 / c.primary_capacitance_f, 0.0],
        ]
    )
    shifts = np.array([0, *phases]) / 2
    events = np.unique(np.r_[0.0, 1.0, shifts, (shifts + 0.5) % 1])
    transitions = []
    offset = np.zeros(2)
    total = np.eye(2)
    for left, right in zip(events[:-1], events[1:]):
        legs = np.where(((left + right) / 2 - shifts) % 1 < 0.5, 1.0, -1.0)
        va = c.va_v * (legs[0] + legs[1]) / 2
        vs = vb / c.turns_ratio * (legs[2] + legs[3]) / 2
        phi = expm(a * (right - left) / c.switching_hz)
        forcing = (np.eye(2) - phi) @ np.array([0.0, va - vs])
        transitions.append((phi, forcing, vs))
        total = phi @ total
        offset = phi @ offset + forcing
    state = np.linalg.solve(np.eye(2) - total, offset)
    energy = 0.0
    for phi, forcing, vs in transitions:
        after = phi @ state + forcing
        energy += vs * c.primary_capacitance_f * (after[1] - state[1])
        state = after
    return energy * c.switching_hz


@pytest.mark.parametrize("resistance", (0.0, 0.02, 10.0, 2 * np.sqrt(30e-6 / 4.7e-6)))
def test_event_exact_cp_power_and_derivative_against_matrix_exponential(resistance):
    from dab_design.control import mapped_power

    c = replace(DABConfig(), series_resistance_ohm=resistance)
    pref = 3500.0
    vb = 400.0
    delta = 0.35
    results = []
    for p in (pref - delta, pref, pref + delta):
        cmd = tps_command(c, vb, p)
        independent = independent_lc_power(c, vb, (cmd.d1, cmd.d2, cmd.d3))
        assert mapped_power(c, vb, p, True) == pytest.approx(
            independent, rel=1e-9, abs=1e-7
        )
        results.append(independent)
    if resistance == 0.02:
        _, model = build_dab_voltage_loop(
            c, vb, pref, replace(DABVoltageControlConfig(), include_cp=True)
        )
        assert model["dpout_dpcmd"] == pytest.approx(
            (results[2] - results[0]) / (2 * delta), rel=1e-8
        )
        assert (
            model["dpout_dpcmd"] > 1
        )  # Rasterized 2048-point predecessor returned zero.


@pytest.mark.parametrize("cp", (False, True))
def test_linear_discrete_plant_matches_nonlinear_energy_map_jacobian(cp):
    from dab_design.control import mapped_power

    c = DABConfig()
    v0 = 400.0
    p0 = 3500.0
    ctrl = replace(DABVoltageControlConfig(), include_cp=cp, delay_ticks=0)
    loop, model = build_dab_voltage_loop(c, v0, p0, ctrl)
    ts = 1 / ctrl.sample_hz
    load0 = model["operating_output_w"]

    def update(v, p):
        net = mapped_power(c, v, p, cp) - load0 * (v / v0) ** 2
        return np.sqrt(v * v + 2 * ts * net / c.output_capacitance_f)

    dv = 0.001
    dp = 0.1
    jv = (update(v0 + dv, p0) - update(v0 - dv, p0)) / (2 * dv)
    jp = (update(v0, p0 + dp) - update(v0, p0 - dp)) / (2 * dp)
    assert loop.plant_a[1] == pytest.approx(-jv, abs=1e-7)
    assert loop.plant_b[-1] == pytest.approx(jp, rel=1e-5)


def test_small_reference_step_matches_closed_loop_discrete_response():
    from scipy.signal import lfilter

    c = DABConfig()
    ctrl = replace(
        DABVoltageControlConfig(),
        sample_hz=1000,
        duration_s=0.08,
        disturbance_time_s=0.02,
        load_step_ratio=1.0,
        reference_step_v=0.001,
    )
    t, data, _, loop = simulate_dab_voltage_loop(c, 400, 3500, ctrl)
    numerator = np.convolve(loop.plant_b, loop.controller_b)
    denominator = np.convolve(loop.plant_a, loop.controller_a)
    length = max(len(numerator), len(denominator))
    closed_den = np.pad(denominator, (0, length - len(denominator))) + np.pad(
        numerator, (0, length - len(numerator))
    )
    prediction = lfilter(
        numerator, closed_den, (t >= ctrl.disturbance_time_s) * ctrl.reference_step_v
    )
    assert data["vout_v"] - 400 == pytest.approx(prediction, abs=1e-7)

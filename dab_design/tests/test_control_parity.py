"""Independent state/branch contracts for the DAB fan-out."""

from dataclasses import replace

import numpy as np
import pytest
from scipy.signal import lfilter

from dab_design.control import (
    DABVoltageControlConfig,
    build_dab_voltage_loop,
    forward_sps_power,
    power_command,
    power_map_jacobian,
    simulate_dab_voltage_loop,
)
from dab_design.core import DABConfig, tps_command
from dab_design.sensing import DABSenseConfig


@pytest.mark.parametrize("vb,power", [(300, 2343.75), (500, 3333.333333333333)])
def test_tps_boundary_rejects_physical_cp_kink_but_accepts_smooth_ideal_l(vb, power):
    stage = DABConfig()
    kp, kv, evidence = power_map_jacobian(stage, vb, power)
    assert kp == pytest.approx(1, abs=1e-7)
    assert kv == pytest.approx(0, abs=1e-7)
    assert all(e["smooth"] for e in evidence["axes"].values())
    with pytest.raises(ValueError, match="Nonsmooth"):
        build_dab_voltage_loop(
            stage, vb, power, replace(DABVoltageControlConfig(), include_cp=True)
        )


@pytest.mark.parametrize(
    "d,power,derivative",
    [
        (0.1, 3000, 26666.6666666667),
        (0.25, 6250, 16666.6666666667),
        (0.5, 8333.3333333333, 0),
    ],
)
def test_forward_sps_formula_and_control_mapping(d, power, derivative):
    stage = DABConfig(p_limit=1)
    value = forward_sps_power(stage, 400, d)
    assert value["power_w"] == pytest.approx(power)
    assert value["dp_dd_w_per_pu"] == pytest.approx(derivative)
    command = power_command(stage, 400, power, "sps_power")
    assert (command.d1, command.d2, command.d3) == pytest.approx((0, d, d), abs=1e-7)
    assert value["interior_authority"] == (0 < d < 0.5)


@pytest.mark.parametrize("actuator", ["tps_power", "sps_power"])
def test_sensed_time_loop_matches_independent_linear_difference_equations(actuator):
    stage = DABConfig()
    control = DABVoltageControlConfig(
        sample_hz=1000,
        duration_s=0.08,
        disturbance_time_s=0.02,
        load_step_ratio=1,
        reference_step_v=0.0001,
        actuator=actuator,
        sensing=DABSenseConfig(lowpass_hz=100, delay_ticks=2, calibration_gain=0.97),
    )
    t, data, metrics, loop = simulate_dab_voltage_loop(stage, 400, 3500, control)
    gb, ga, cb, ca, hb, ha = (
        loop.plant_b,
        loop.plant_a,
        loop.controller_b,
        loop.controller_a,
        loop.sense_b,
        loop.sense_a,
    )
    # V/R = GC/(1+GCH), independently assembled from constituent recurrences.
    numerator = np.convolve(np.convolve(gb, cb), ha)
    denominator = np.convolve(np.convolve(ga, ca), ha)
    feedback = np.convolve(np.convolve(gb, cb), hb)
    size = max(len(denominator), len(feedback))
    denominator = np.pad(denominator, (0, size - len(denominator))) + np.pad(
        feedback, (0, size - len(feedback))
    )
    prediction = lfilter(numerator, denominator, (t >= 0.02) * 0.0001)
    assert data["vout_v"] - 400 == pytest.approx(prediction, abs=2e-8)
    assert sorted(abs(loop.poles)) == pytest.approx(sorted(abs(np.roots(denominator))))
    assert metrics["max_energy_error_j"] < 1e-8
    assert np.all(loop.frequency_hz < 500)
    assert loop.open_response == pytest.approx(
        loop.plant_response * loop.controller_response * loop.sense_response
    )


def test_invalid_adc_and_frequency_and_forward_boundaries():
    stage = DABConfig()
    for cfg in (
        DABVoltageControlConfig(sensing=DABSenseConfig(divider_gain=0.01)),
        DABVoltageControlConfig(frequency_min_hz=2500),
    ):
        with pytest.raises(ValueError):
            build_dab_voltage_loop(stage, 400, 3500, cfg)
    with pytest.raises(ValueError, match="inside forward"):
        power_map_jacobian(
            stage, 400, tps_command(stage, 400, 0).pbase_w * stage.p_limit
        )
    with pytest.raises(ValueError):
        power_map_jacobian(stage, 400, -1)


def test_current_candidate_is_exact_and_missing_gain_margin_cannot_apply():
    from dab_design.workspace import controller_from_payload, synthesize_candidate

    config = DABVoltageControlConfig()
    candidate = synthesize_candidate(
        DABConfig(), config, 400, 3500, 100, 60, source="frozen"
    )
    assert candidate["can_apply"]
    controller = controller_from_payload(candidate["controller"])
    loop, _ = build_dab_voltage_loop(DABConfig(), 400, 3500, config, controller)
    assert np.array_equal(loop.controller_b, candidate["controller"]["b"])
    assert abs(loop.margins.phase_margin_deg - 60) < 0.001
    narrow = replace(config, frequency_max_hz=200)
    unknown = synthesize_candidate(
        DABConfig(), narrow, 400, 3500, 100, 60, source="narrow"
    )
    assert not unknown["can_apply"] and "GM_UNKNOWN" in unknown["status"]


def test_optimization_cooperatively_cancels():
    from dab_design.optimization import optimize_tps

    with pytest.raises(ValueError, match="cancelled"):
        optimize_tps(DABConfig(), 400, 3500, cancelled=lambda: True)

import math
from dataclasses import replace

import numpy as np
import pytest

from pfc_design.three_phase.model import (
    SRFPLL,
    ThreePhaseConfig,
    abc_to_dq,
    dq_to_abc,
    export_result,
    simulate_three_phase,
    svpwm,
    switching_snapshot,
)


@pytest.fixture(scope="module")
def baseline():
    return simulate_three_phase(ThreePhaseConfig())


def test_park_inverse_and_instantaneous_power():
    for theta in np.linspace(-math.pi, math.pi, 21):
        v = dq_to_abc(325, -30, theta)
        i = dq_to_abc(20, 5, theta)
        assert abc_to_dq(v, theta) == pytest.approx((325, -30))
        assert np.sum(v) == pytest.approx(0, abs=1e-12)
        assert v @ i == pytest.approx(1.5 * (325 * 20 - 30 * 5))


@pytest.mark.parametrize("initial", [-90, 45, 120])
def test_pll_acquires_from_measured_voltages_and_loses_lock(initial):
    cfg = replace(ThreePhaseConfig(), pll_initial_deg=initial)
    pll = SRFPLL(cfg)
    for t in np.arange(0, 0.25, 1 / cfg.control_hz):
        phase = 2 * math.pi * 60 * t + 0.2
        theta = pll.step(dq_to_abc(325, 0, phase))
        assert cfg.pll_min_hz <= pll.frequency_hz <= cfg.pll_max_hz
    assert pll.locked
    assert pll.frequency_hz == pytest.approx(60, abs=0.002)
    assert abs(np.angle(np.exp(1j * (phase - theta)))) < 0.001
    pll.step((0, 0, 0))
    assert not pll.locked
    assert math.isfinite(pll.theta)


def test_pll_rejects_antiphase_false_lock():
    cfg = replace(ThreePhaseConfig(), pll_initial_deg=180)
    pll = SRFPLL(cfg)
    for t in np.arange(0, 0.01, 1 / cfg.control_hz):
        pll.step(dq_to_abc(325, 0, 2 * math.pi * 50 * t))
        assert not pll.locked


def test_svpwm_two_level_duties_and_saturation():
    for theta in np.linspace(0, 2 * math.pi, 19):
        requested = dq_to_abc(325, 10, theta)
        duty, actual, limited = svpwm(requested, 700)
        assert not limited
        assert actual == pytest.approx(requested, abs=1e-10)
        assert np.all((duty >= 0) & (duty <= 1))
        duty, actual, limited = svpwm(requested * 10, 700)
        assert limited
        assert np.all((duty >= 0) & (duty <= 1))
        assert math.sqrt(2 / 3 * (actual @ actual)) <= 0.98 * 700 / math.sqrt(3) + 1e-10


def test_closed_loop_default_and_energy_balance(baseline):
    m, s = baseline.metrics, baseline.signals
    assert m["settled"] and m["pll_locked"]
    assert m["vbus_average_v"] == pytest.approx(700, abs=0.1)
    assert m["power_factor"] > 0.995
    assert m["id_error_rms_a"] < 0.05 and m["iq_error_rms_a"] < 0.05
    assert np.max(np.abs(s["current_a"] + s["current_b"] + s["current_c"])) < 1e-10
    assert abs(m["energy_balance_error_j"]) < 0.05
    assert m["pin_w"] > m["load_w"]  # physical copper loss included
    assert s["pll_theta_rad"][0] == pytest.approx(math.radians(-30) % (2 * math.pi))


def test_grid_frequency_phase_and_load_step_recovers():
    c = replace(
        ThreePhaseConfig(), grid_after_hz=60, phase_step_deg=20, load_step_ratio=0.7
    )
    r = simulate_three_phase(c)
    assert r.metrics["pll_locked"] and r.metrics["settled"]
    assert r.metrics["pll_frequency_hz"] == pytest.approx(60, abs=0.01)
    assert r.metrics["vbus_average_v"] == pytest.approx(700, abs=0.2)
    after = r.time_s > c.disturbance_time_s
    assert np.any(r.signals["pll_locked"][after] == 0)
    assert r.metrics["load_w"] == 7000


def test_nonzero_q_reference_controls_reactive_current():
    r = simulate_three_phase(replace(ThreePhaseConfig(), iq_reference_a=5))
    assert r.metrics["iq_error_rms_a"] < 0.05
    assert 0.9 < r.metrics["power_factor"] < 0.99


def test_zero_current_controller_is_detectably_not_nominal():
    r = simulate_three_phase(
        replace(
            ThreePhaseConfig(),
            current_kp=0,
            current_ki=0,
            output_power_w=500,
            duration_s=0.1,
        )
    )
    assert not r.metrics["settled"]
    assert r.metrics["id_error_rms_a"] > 1


def test_energy_residual_reduces_with_integration_refinement(baseline):
    finer = simulate_three_phase(replace(ThreePhaseConfig(), integration_substeps=8))
    assert (
        abs(finer.metrics["energy_balance_error_j"])
        < abs(baseline.metrics["energy_balance_error_j"]) * 0.4
    )


def test_switching_snapshot_has_six_complementary_gates(baseline):
    t, high, low, pole, phase = switching_snapshot(baseline)
    assert high.shape == low.shape == (3, len(t))
    assert np.all(high + low == 1)
    assert len(np.unique(pole)) == 2
    assert np.sum(phase, axis=0) == pytest.approx(np.zeros(len(t)), abs=1e-10)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"inductance_h": 0},
        {"bus_voltage_v": 400},
        {"pll_min_hz": 55},
        {"control_hz": float("nan")},
        {"voltage_loop_hz": 3000},
        {"pwm_delay_ticks": 1.5},
        {"iq_reference_a": 61},
        {"load_step_ratio": -1},
    ],
)
def test_invalid_configuration(kwargs):
    with pytest.raises(ValueError):
        simulate_three_phase(replace(ThreePhaseConfig(), **kwargs))


def test_export_keeps_result_snapshot(baseline, tmp_path):
    import json

    path = tmp_path / "dq.csv"
    export_result(baseline, path)
    saved = json.loads(path.with_suffix(".json").read_text())
    assert saved["config"]["output_power_w"] == 10000
    assert saved["metrics"]["evidence"] == "SOFTWARE_MODEL_ONLY"
    assert "pll_theta_rad" in path.read_text().splitlines()[0]

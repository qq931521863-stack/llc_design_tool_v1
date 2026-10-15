from dataclasses import replace

import numpy as np
import pytest

from pfc_design.three_phase.control import build_three_phase_loops
from pfc_design.three_phase.model import ThreePhaseConfig, simulate_three_phase
from pfc_design.three_phase.performance import (
    LossParameters,
    evaluate_two_level,
    evaluate_vienna,
)
from pfc_design.vienna.config import ViennaControlLabConfig
from pfc_design.vienna.waveforms import simulate_vienna_line_cycle


@pytest.fixture(scope="module")
def result():
    return simulate_three_phase(ThreePhaseConfig())


def test_current_partition_and_capacitor_loss_are_consistent(result):
    loss = LossParameters(
        rds_on_ohm=0.05, switch_energy_j=0.0001, capacitor_esr_ohm=0.01
    )
    r = evaluate_two_level(result, loss)
    for ph in "abc":
        upper = r["device_stress"][ph + "_upper_switch"]["rms_a"]
        lower = r["device_stress"][ph + "_lower_switch"]["rms_a"]
        assert upper**2 + lower**2 == pytest.approx(
            r["inductor_stress"][ph]["rms_a"] ** 2
        )
    assert r["loss_breakdown_w"]["switch_conduction"] == pytest.approx(
        sum(v["rms_a"] ** 2 * 0.05 for v in r["inductor_stress"].values())
    )
    assert r["loss_breakdown_w"]["capacitor_esr"] == pytest.approx(
        r["capacitor_stress"]["bus"]["rms_a"] ** 2 * 0.01
    )
    assert 0.99 < r["power_factor_total"] <= 1
    assert not r[
        "loss_model_complete"
    ]  # Core missing, never silently assumed lossless.
    finer = evaluate_two_level(result, loss, carrier_points=256)
    assert r["inductor_stress"]["a"]["rms_a"] == pytest.approx(
        finer["inductor_stress"]["a"]["rms_a"], rel=0.003
    )


def test_vienna_uses_two_caps_and_bidirectional_center_switch():
    c = ViennaControlLabConfig()
    line = simulate_vienna_line_cycle(c)
    r = evaluate_vienna(c, line, LossParameters(diode_vf_v=1, capacitor_esr_ohm=0.02))
    assert len(r["device_stress"]) == 12  # Six center MOSFETs plus six outer diodes.
    assert set(r["capacitor_stress"]) == {"upper", "lower"}
    for ph in "abc":
        s = r["device_stress"]
        assert s[ph + "_center_switch_1"] == s[ph + "_center_switch_2"]
        squares = sum(
            s[key]["rms_a"] ** 2
            for key in (
                ph + "_center_switch_1",
                ph + "_upper_diode",
                ph + "_lower_diode",
            )
        )
        assert squares == pytest.approx(r["inductor_stress"][ph]["rms_a"] ** 2)
    assert r["loss_breakdown_w"]["outer_diode"] > 0


def test_sampled_multirate_loop_margins_and_unstable_gain():
    c = ThreePhaseConfig()
    r = build_three_phase_loops(c)
    assert r["current"].stable and r["voltage"].stable
    assert r["current"].margins.phase_margin_deg > 50
    assert r["voltage"].margins.phase_margin_deg > 60
    aggressive = build_three_phase_loops(replace(c, current_kp=40))
    assert not aggressive["current"].stable
    assert np.max(abs(aggressive["current"].poles)) > 1


def test_vienna_voltage_plant_includes_load_slope_and_energy_zero():
    from pfc_design.control.config import LoadModel
    from pfc_design.vienna.analysis import build_vienna_control_lab_analysis

    c = ViennaControlLabConfig()
    a = build_vienna_control_lab_analysis(c)
    f = a.frequencies_hz
    stage = c.power_stage
    s = 2j * np.pi * f
    g0 = stage.input_conductance_a_per_v
    expected = (
        3
        * stage.phase_rms_v**2
        * (
            1
            - 2 * stage.phase_series_resistance_ohm * g0
            - stage.boost_inductance_h * g0 * s
        )
        / (stage.bus_voltage_v * stage.series_bus_capacitance_f * s)
    )
    assert a.voltage_loop.responses["bus_plant_gvg"] == pytest.approx(expected)
    resistive = replace(c, power_stage=replace(stage, load_model=LoadModel.RESISTIVE))
    ar = build_vienna_control_lab_analysis(resistive)
    assert abs(ar.voltage_loop.responses["bus_plant_gvg"][0]) < abs(expected[0])


def test_vienna_diode_bridge_and_back_to_back_losses_use_distinct_paths():
    c = ViennaControlLabConfig()
    line = simulate_vienna_line_cycle(c)
    loss = LossParameters(vienna_switch_kind='diode_bridge', rds_on_ohm=.05,
                          diode_vf_v=1, bridge_diode_vf_v=.8, switch_energy_j=.0001)
    r = evaluate_vienna(c, line, loss)
    assert len(r['device_stress']) == 21
    assert r['loss_breakdown_w']['center_bridge_diodes'] > 0
    assert r['loss_breakdown_w']['switch_switching'] > 0
    for ph in 'abc':
        s = r['device_stress']
        inner = sum(s[f'{ph}_bridge_diode_{j}']['rms_a']**2 for j in range(1, 5))
        assert inner == pytest.approx(2*s[ph+'_center_switch']['rms_a']**2)

"""Independent circuit/steady-state checks for PSD analog-loop calculations."""
from dataclasses import asdict, replace

import numpy as np
import pytest

from power_control_tools.controllers import design_controller
from power_control_tools.fra.analysis import analyze_loop_response
from power_stage_design.loop import (
    Compensator,
    LoopPlant,
    analyze_loop,
    compensator_frequency_response,
    output_impedance,
    plant_frequency_response,
    synthesize_compensator,
)


def test_vmc_buck_matches_independent_lcr_transfer():
    p = LoopPlant(esr_ohm=0, dcr_ohm=0.04, ramp_v=1.5)
    f = np.geomspace(1, 49_000, 500)
    s = 2j * np.pi * f
    r = p.vout_v / p.iout_a
    expected = (p.vin_v / p.ramp_v) / (
        p.inductance_h * p.capacitance_f * s ** 2
        + (p.inductance_h / r + p.dcr_ohm * p.capacitance_f) * s
        + 1 + p.dcr_ohm / r)
    actual, _, _ = plant_frequency_response(p, f)
    np.testing.assert_allclose(actual, expected, rtol=2e-14)


def test_two_capacitor_branches_use_admittance_and_merge_identical_rc():
    p = LoopPlant(capacitance_f=60e-6, esr_ohm=.08,
                  capacitance2_f=60e-6, esr2_ohm=.08)
    f = np.geomspace(.1, 50_000, 800)
    merged = replace(p, capacitance_f=120e-6, esr_ohm=.04, capacitance2_f=0)
    np.testing.assert_allclose(output_impedance(p, f), output_impedance(merged, f), rtol=1e-14)
    # At arbitrarily high frequency both capacitor ESRs remain in parallel.
    high = output_impedance(p, [1e12])[0]
    np.testing.assert_allclose(high.real, 1 / (p.iout_a / p.vout_v + 2 / .08), rtol=1e-11)


@pytest.mark.parametrize("network", ["type_ii", "type_iii"])
def test_legacy_physical_controller_sign_is_corrected_exactly_once(network):
    c = Compensator(network=network, open_loop_gain_db=260, gbw_hz=1e18)
    f = np.geomspace(1, 100_000, 500)
    s = 2j * np.pi * f
    legacy = design_controller(network, type_input_mode="rc", r1_ohm=c.r1_ohm,
                               r2_ohm=c.r2_ohm, r3_ohm=c.r3_ohm,
                               c1_f=c.c1_f, c2_f=c.c2_f, c3_f=c.c3_f)
    expected = -np.polyval(legacy.numerator, s) / np.polyval(legacy.denominator, s)
    actual, _, _ = compensator_frequency_response(c, f)
    np.testing.assert_allclose(actual, expected, rtol=4e-10)
    assert np.angle(actual[0], deg=True) < 0


@pytest.mark.parametrize("network", ["type_ii", "type_iii"])
def test_finite_voltage_amplifier_matches_independent_two_node_kcl(network):
    c = Compensator(network=network, gbw_hz=200_000, open_loop_gain_db=55, cff_f=2e-9)
    f = np.geomspace(.1, 50_000, 100)
    actual, amplifier, _ = compensator_frequency_response(c, f)
    for index, freq in enumerate(f):
        s = 2j * np.pi * freq
        yf = s * c.c2_f + 1 / (c.r2_ohm + 1 / (s * c.c1_f))
        yin = 1 / c.r1_ohm
        if network == "type_iii":
            yin += 1 / (c.r3_ohm + 1 / (s * c.c3_f))
        else:
            yin += s * c.cff_f
        # Unknowns [Vfb, Vc], Vout=1, Vref=0.
        matrix = [[yin + 1 / c.r_lower_ohm + yf, -yf], [amplifier[index], 1]]
        physical = np.linalg.solve(matrix, [yin, 0])[1]
        np.testing.assert_allclose(actual[index], -physical, rtol=3e-13)


def test_type_i_limit_and_zero_high_frequency_capacitor_are_physical():
    c = Compensator(r2_ohm=0, c2_f=0, open_loop_gain_db=260, gbw_hz=1e18)
    f = np.asarray([100, 1000])
    actual, _, _ = compensator_frequency_response(c, f)
    np.testing.assert_allclose(actual, 1 / (2j * np.pi * f * c.r1_ohm * c.c1_f), rtol=1e-12)


def test_gm_network_uses_loaded_output_and_unloaded_input_divider():
    c = Compensator(network="type_ii_gm", cff_f=22e-9,
                    gm_s=.002, output_resistance_ohm=80_000, gbw_hz=2e6)
    f = np.geomspace(1, 50_000, 400)
    s = 2j * np.pi * f
    actual, a, _ = compensator_frequency_response(c, f)
    zf = 1 / (s * c.c2_f + 1 / (c.r2_ohm + 1 / (s * c.c1_f)))
    ytop = 1 / c.r1_ohm + s * c.cff_f
    expected = (a / c.output_resistance_ohm) * (ytop / (ytop + 1 / c.r_lower_ohm)) / (1 / zf + 1 / c.output_resistance_ohm)
    np.testing.assert_allclose(actual, expected, rtol=2e-14)
    # Feed-forward divider tends towards 1, rather than growing unbounded.
    noff = compensator_frequency_response(replace(c, cff_f=0), f)[0]
    assert 1.9 < abs(actual[-1] / noff[-1]) < 2.01


def test_isolated_topologies_have_distinct_direct_path_and_single_ctr_factor():
    f = np.geomspace(1, 50_000, 300)
    c = Compensator(network="isolated_zener", ctr=.4, rpullup_ohm=5000,
                    rled_ohm=2000, opto_pole_hz=8000)
    zener = compensator_frequency_response(c, f)[0]
    inner = compensator_frequency_response(replace(c, network="isolated_inner"), f)[0]
    h = c.ctr * c.rpullup_ohm / c.rled_ohm / (1 + 1j * f / c.opto_pole_hz)
    np.testing.assert_allclose(inner - zener, h, rtol=2e-13)
    doubled = compensator_frequency_response(replace(c, ctr=.8), f)[0]
    np.testing.assert_allclose(doubled, 2 * zener, rtol=1e-14)


CMC_CASES = [
    ("buck", 12, 5, 3, 47e-6, 1),
    ("boost", 24, 48, 2, 100e-6, 1),
    ("inverting_buck_boost", 12, -5, 3, 47e-6, 1),
    ("forward", 48, 5, 3, 47e-6, 4),
    ("flyback", 48, 12, 1, 1e-3, 4),
]


@pytest.mark.parametrize("top,vin,vout,iout,inductance,n", CMC_CASES)
def test_cmc_low_ripple_dc_gain_matches_independent_peak_current_derivative(top, vin, vout, iout, inductance, n):
    p = LoopPlant(topology=top, control_mode="peak_cmc", vin_v=vin, vout_v=vout,
                  iout_a=iout, inductance_h=inductance, turns_ratio=n, fsw_hz=1e15,
                  slope_v=0, esr_ohm=0)
    actual, m, _ = plant_frequency_response(p, np.asarray([1e-10]))
    r = abs(vout) / iout
    q = 1 - m["duty_cycle"]
    if top == "buck":
        expected = r / p.sense_ohm
    elif top == "forward":
        expected = n * r / p.sense_ohm
    elif top == "boost":
        expected = r * q / (2 * p.sense_ohm)
    else:
        expected = (n if top == "flyback" else 1) * r * q / ((2 - q) * p.sense_ohm)
    np.testing.assert_allclose(actual, expected, rtol=2e-11)


def test_forward_secondary_referral_matches_buck_in_every_frequency_bin():
    p = LoopPlant(topology="forward", control_mode="peak_cmc", vin_v=48,
                  turns_ratio=4, sense_gain=2, slope_mode="multiplier", dcr_ohm=.04)
    equivalent = replace(p, topology="buck", vin_v=12, turns_ratio=1,
                         sense_ohm=p.sense_ohm / 4)
    f = np.geomspace(.1, 50_000, 400)
    np.testing.assert_allclose(plant_frequency_response(p, f)[0],
                               plant_frequency_response(equivalent, f)[0], rtol=1e-14)


def test_flyback_primary_referral_matches_buck_boost_in_every_frequency_bin():
    p = LoopPlant(topology="flyback", control_mode="peak_cmc", vin_v=48, vout_v=12,
                  iout_a=1, inductance_h=1e-3, turns_ratio=4, sense_gain=2,
                  slope_mode="multiplier", dcr_ohm=.04, capacitance2_f=22e-6, esr2_ohm=.1)
    n = p.turns_ratio
    equivalent = replace(p, topology="inverting_buck_boost", vout_v=-n * p.vout_v,
                         iout_a=p.iout_a / n, turns_ratio=1,
                         capacitance_f=p.capacitance_f / n ** 2, esr_ohm=p.esr_ohm * n ** 2,
                         capacitance2_f=p.capacitance2_f / n ** 2, esr2_ohm=p.esr2_ohm * n ** 2)
    f = np.geomspace(.1, 50_000, 400)
    np.testing.assert_allclose(plant_frequency_response(p, f)[0],
                               plant_frequency_response(equivalent, f)[0] / n, rtol=1e-14)


def test_cmc_half_duty_without_ramp_has_finite_response_and_rejects_stability():
    p = LoopPlant(control_mode="peak_cmc", vin_v=10, vout_v=5)
    r = analyze_loop(p, Compensator())
    assert np.all(np.isfinite(r.plant_response))
    assert r.stability.status == "FAIL_SUBHARMONIC"
    assert r.metrics["sampled_current_pole"] == -1
    stable = analyze_loop(replace(p, slope_mode="multiplier"), Compensator())
    assert abs(stable.metrics["sampled_current_pole"]) < 1
    assert stable.stability.status != "FAIL_SUBHARMONIC"


def test_slope_multiplier_and_equivalent_voltage_ramp_are_identical():
    p = LoopPlant(control_mode="peak_cmc", slope_mode="multiplier", slope_multiplier=.8)
    r = analyze_loop(p, Compensator())
    voltage = replace(p, slope_mode="fixed", slope_v=r.metrics["effective_slope_v"])
    np.testing.assert_allclose(analyze_loop(voltage, Compensator()).plant_response, r.plant_response)


def test_rhp_zero_is_positive_and_has_nonminimum_phase_sign():
    p = LoopPlant(topology="boost", control_mode="peak_cmc", vin_v=24, vout_v=48,
                  iout_a=2, inductance_h=100e-6, slope_mode="multiplier")
    r = analyze_loop(p, Compensator())
    expected = (1 - .5) ** 2 * 24 / (2 * np.pi * 100e-6)
    assert r.metrics["rhp_zero_hz"] == pytest.approx(expected)
    # The numerator evaluated with s=+wz is zero, not s=-wz.
    zl_at_positive_s = 2 * np.pi * expected * p.inductance_h
    assert 1 - zl_at_positive_s / (.5 ** 2 * 24) == pytest.approx(0)


def test_fra_margins_are_reused_without_reimplementing_phase_conventions():
    r = analyze_loop(LoopPlant(), Compensator(network="type_iii"))
    independent = analyze_loop_response(r.frequency_hz, r.loop_response)
    assert independent.gain_crossovers == r.stability.gain_crossovers
    assert independent.phase_crossovers == r.stability.phase_crossovers
    np.testing.assert_allclose(r.loop_response, r.plant_response * r.compensator_response)


@pytest.mark.parametrize("changes", [
    {"vin_v": float("nan")}, {"iout_a": 0}, {"capacitance_f": 0},
    {"esr_ohm": -.1}, {"capacitance2_f": -1}, {"vout_v": 15},
    {"vout_v": -5}, {"iout_a": .1}, {"control_mode": "average_cmc"},
    {"topology": "boost", "control_mode": "vmc"},
])
def test_invalid_or_outside_ccm_operating_points_are_rejected(changes):
    with pytest.raises(ValueError):
        analyze_loop(replace(LoopPlant(), **changes), Compensator())


@pytest.mark.parametrize("f", [[0, 10], [10, 10], [20, 10], [1, 60_000], [1, float("nan")]])
def test_frequency_contract_rejects_invalid_or_above_model_band(f):
    with pytest.raises(ValueError):
        analyze_loop(LoopPlant(), Compensator(), f)


def test_no_crossover_is_not_declared_infinite_gain_margin():
    r = analyze_loop(LoopPlant(), Compensator(r1_ohm=1e12))
    assert r.stability.main_crossover_hz is None
    assert r.stability.status == "NO_0DB_CROSSING"


def test_dataclass_roundtrip_preserves_full_response():
    p = LoopPlant(control_mode="peak_cmc", slope_mode="multiplier", capacitance2_f=22e-6, esr2_ohm=.1)
    c = Compensator(network="type_ii_gm", cff_f=22e-9, output_resistance_ohm=1e6)
    first = analyze_loop(p, c)
    restored = analyze_loop(LoopPlant(**asdict(p)), Compensator(**asdict(c)))
    np.testing.assert_array_equal(first.loop_response, restored.loop_response)


def test_synthesis_reanalyzes_e24_components_and_meets_buck_vmc_target():
    p = LoopPlant()
    result = synthesize_compensator(p, 3000, network="type_iii")
    assert result.achievable, result.reason
    assert result.compensator is not None
    assert result.result is not None
    actual = result.result
    assert abs(actual.stability.main_crossover_hz / 3000 - 1) < .2
    assert abs(actual.stability.phase_margin_deg - 60) < 7.5
    reanalyzed = analyze_loop(p, result.compensator, actual.frequency_hz)
    np.testing.assert_array_equal(reanalyzed.loop_response, actual.loop_response)
    assert actual.stability.main_crossover_hz != 3000  # Requested != measured.


@pytest.mark.parametrize("top,vin,vout,iout,inductance,n,network,fc", [
    ("buck", 12, 5, 3, 47e-6, 1, "type_ii", 3000),
    ("boost", 24, 48, 2, 100e-6, 1, "type_ii", 1000),
    ("flyback", 48, 12, 1, 1e-3, 4, "isolated_zener", 500),
])
def test_cmc_suggestions_are_realized_and_rechecked(top,vin,vout,iout,inductance,n,network,fc):
    p = LoopPlant(topology=top, vin_v=vin, vout_v=vout, iout_a=iout,
                  inductance_h=inductance, turns_ratio=n, control_mode="peak_cmc", slope_mode="multiplier")
    result = synthesize_compensator(p, fc, network=network)
    assert result.achievable, result.reason
    assert len(result.result.stability.gain_crossovers) == 1
    assert result.result.stability.worst_phase_margin_deg > 50


def test_unreachable_target_reports_reason_without_fake_design():
    result = synthesize_compensator(LoopPlant(), 30_000)
    assert not result.achievable
    assert result.compensator is None and result.result is None
    assert "limit" in result.reason
    result = synthesize_compensator(LoopPlant(control_mode="peak_cmc", vin_v=10), 3000)
    assert not result.achievable and "slope" in result.reason


@pytest.mark.parametrize("top,vin,vout,iout,inductance,n", CMC_CASES)
def test_finite_slope_dc_gain_matches_independent_peak_condition_derivative(top,vin,vout,iout,inductance,n):
    p = LoopPlant(topology=top, control_mode="peak_cmc", vin_v=vin, vout_v=vout,
                  iout_a=iout, inductance_h=inductance, turns_ratio=n, slope_v=.37)
    rload = abs(vout) / iout

    def peak_control_voltage(v):
        if top in ("buck", "forward"):
            ratio = n if top == "forward" else 1
            duty = v * ratio / vin
            average = v / rload
            ripple = (vin / ratio - v) * duty / (inductance * p.fsw_hz)
            sensed_peak = (average + ripple / 2) / ratio
        else:
            if top == "boost":
                duty = 1 - vin / v
            else:
                reflected = n * v if top == "flyback" else v
                duty = reflected / (vin + reflected)
            average = v / (rload * (1 - duty) * (n if top == "flyback" else 1))
            ripple = vin * duty / (inductance * p.fsw_hz)
            sensed_peak = average + ripple / 2
        return p.sense_ohm * p.sense_gain * sensed_peak + p.slope_v * duty

    dv = abs(vout) * 1e-5
    derivative = (peak_control_voltage(abs(vout) + dv) - peak_control_voltage(abs(vout) - dv)) / (2 * dv)
    actual = plant_frequency_response(p, np.asarray([1e-8]))[0][0]
    np.testing.assert_allclose(actual, 1 / derivative, rtol=2e-9)


def test_three_crossovers_keep_all_margins_and_do_not_report_success():
    p = LoopPlant(iout_a=.4, esr_ohm=0)
    c = Compensator(r2_ohm=0, c1_f=1e-6, c2_f=0)
    result = analyze_loop(p, c)
    assert len(result.stability.gain_crossovers) == 3
    assert result.stability.worst_phase_margin_deg < 0
    assert result.stability.status == "FAIL"
    assert any("Multiple" in warning for warning in result.warnings)


@pytest.mark.parametrize("cancel_after", [1, 15])
def test_synthesis_cancellation_interrupts_rather_than_returning_design(cancel_after):
    calls = 0

    def cancelled():
        nonlocal calls
        calls += 1
        return calls >= cancel_after

    with pytest.raises(InterruptedError, match="cancelled"):
        synthesize_compensator(LoopPlant(), 3000, cancelled=cancelled)
    assert calls == cancel_after

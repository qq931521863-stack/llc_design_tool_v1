"""Independent volt-second/charge-balance oracles for component suggestions."""
from __future__ import annotations

import copy
import json
import math
from dataclasses import replace

import pytest

from power_stage_design.models import DesignInputs, ModelDomainError
from power_stage_design.project import read_project, save_project
from power_stage_design.report import write_report
from power_stage_design.suggestions import (
    MAGNETIZING_TOPOLOGIES,
    RATIO_TOPOLOGIES,
    TWO_INDUCTOR_TOPOLOGIES,
    SuggestionTargets,
    suggest_parameters,
    validate_suggestion_record,
)


def example(topology):
    values = DesignInputs(topology, 48, 5, 2, 100e3, 470e-6,
                          capacitance_f=1e-3, turns_ratio_np_ns=2,
                          secondary_inductance_h=100e-6, transfer_capacitance_f=1e-3,
                          magnetizing_inductance_h=1e-3)
    if topology == "T01":
        return replace(values, vin_v=12, diode_drop_v=.4)
    if topology == "T02":
        return replace(values, vin_v=24, vout_v=48, diode_drop_v=.5)
    if topology == "T04":
        return replace(values, vin_v=24, iout_a=.2, turns_ratio_np_ns=1,
                       diode_drop_v=.4, inductance_h=47e-6, capacitance_f=100e-6,
                       leakage_inductance_h=1e-6, primary_capacitance_f=47e-6,
                       primary_output_current_a=.1)
    if topology == "T11":
        return replace(values, vin_v=325, vout_v=12, iout_a=1,
                       turns_ratio_np_ns=10, inductance_h=1e-3,
                       resonant_capacitance_f=100e-12)
    if topology == "T18":
        return replace(values, vin_v=400, vout_v=48, iout_a=5,
                       turns_ratio_np_ns=4, inductance_h=100e-6,
                       leakage_inductance_h=20e-6, diode_drop_v=.7)
    if topology == "T19":
        return replace(values, vin_v=12, vout_v=1.2, iout_a=10, fsw_hz=500e3)
    return values


@pytest.mark.parametrize("topology", ["T01", "T02", "T03", "T05", "T09", "T10"])
@pytest.mark.parametrize("ripple", [.6, 20.0])
def test_single_inductor_suggestions_obey_independent_charge_and_volt_seconds(topology, ripple):
    inputs = example(topology)
    if topology == "T05" and ripple == 20:
        with pytest.raises(ModelDomainError, match="CCM/BCM"):
            suggest_parameters(inputs, SuggestionTargets(ripple, max_duty=.8))
        return
    proposal = suggest_parameters(inputs, SuggestionTargets(ripple, max_duty=.8))
    chosen = proposal.inputs
    vin, vo, vf, n, fs = chosen.vin_v, chosen.vout_v, chosen.diode_drop_v, chosen.turns_ratio_np_ns, chosen.fsw_hz
    gain = n if topology in {"T09", "T10"} else 1
    continuous = topology == "T01"
    on = vin-vo if continuous else vin
    off = vo+vf if continuous else vo+vf-vin if topology == "T02" else gain*(vo+(2 if topology == "T05" else 1)*vf)
    dcc = off/(on+off)
    mean_cc = chosen.iout_a/gain/(1 if continuous else 1-dcc)
    if ripple <= 2*mean_cc:
        expected = on*dcc/fs/ripple
        assert proposal.result.duty == pytest.approx(dcc)
    elif continuous:
        expected = 2*chosen.iout_a/(fs*ripple**2*(1/on+1/off))
    else:
        expected = 2*chosen.iout_a*off/(gain*fs*ripple**2)
    assert chosen.inductance_h == pytest.approx(expected)
    d = proposal.result.duty
    assert on*d/(chosen.inductance_h*fs) == pytest.approx(ripple)
    if ripple > 2*mean_cc:
        discharge = on*d/off
        delivered = gain*ripple*(d+discharge if continuous else discharge)/2
        assert delivered == pytest.approx(chosen.iout_a)
        assert d+discharge < 1
    assert proposal.source_inputs == inputs
    assert inputs.inductance_h == 470e-6


@pytest.mark.parametrize("topology", ["T09", "T10", "T12", "T13", "T14", "T15", "T16", "T17"])
def test_isolated_ratio_and_inductor_oracles_use_np_over_ns_and_bridge_double_frequency(topology):
    inputs = replace(example(topology), diode_drop_v=.35)
    targets = SuggestionTargets(.6, .42, magnetizing_ripple_pp_a=.08 if topology in MAGNETIZING_TOPOLOGIES else None, size_turns_ratio=True)
    proposal = suggest_parameters(inputs, targets)
    chosen = proposal.inputs
    if topology in {"T09", "T10"}:
        expected_n = 48*.42/(5.35*.58)
        expected_l = 48*.42/(100e3*.6)
    else:
        amplitude = 24 if topology == "T15" else 48
        frequency = 200e3 if topology in {"T15", "T16", "T17"} else 100e3
        expected_n = amplitude*.42/5.35
        expected_l = (amplitude/expected_n-5.35)*.42/(frequency*.6)
        assert chosen.magnetizing_inductance_h == pytest.approx(amplitude*.42/(frequency*.08))
    assert chosen.turns_ratio_np_ns == pytest.approx(expected_n)
    assert chosen.inductance_h == pytest.approx(expected_l)
    assert proposal.result.duty == pytest.approx(.42)


@pytest.mark.parametrize("topology", ["T06", "T07", "T08"])
@pytest.mark.parametrize("targets", [(.7, 1.1), (14., 22.)])
def test_dual_inductor_uses_distinct_ripple_and_circulating_current_sum(topology, targets):
    inputs = example(topology)
    one, two = targets
    proposal = suggest_parameters(inputs, SuggestionTargets(one, .8, secondary_ripple_pp_a=two))
    dcc = 5/53
    total_mean = 2*(1+5/48)
    duty = dcc*min(1, 2*total_mean/(one+two))
    assert proposal.inputs.inductance_h == pytest.approx(48*duty/(100e3*one))
    assert proposal.inputs.secondary_inductance_h == pytest.approx(48*duty/(100e3*two))
    assert proposal.result.duty == pytest.approx(duty)


def test_series_capacitor_inductor_uses_per_phase_current_and_half_input_voltage():
    proposal = suggest_parameters(example("T19"), SuggestionTargets(3, .45, secondary_ripple_pp_a=2))
    assert proposal.inputs.inductance_h == pytest.approx((6-1.2)*.2/(500e3*3))
    assert proposal.inputs.secondary_inductance_h == pytest.approx((6-1.2)*.2/(500e3*2))
    assert proposal.result.stress["L1"]["current_mean_a"] == pytest.approx(5)


@pytest.mark.parametrize("topology", ["T11", "T18", "T04"])
def test_numerical_proposal_actual_duty_ripple_and_domains(topology):
    inputs = example(topology)
    target = SuggestionTargets(.3*inputs.iout_a, .45, size_turns_ratio=True,
                               reflected_voltage_v=110 if topology == "T11" else None)
    proposal = suggest_parameters(inputs, target)
    assert proposal.result.metrics["inductor_ripple_pp_a"] == pytest.approx(target.ripple_pp_a, rel=2e-4)
    assert proposal.result.duty <= target.max_duty+2e-6
    assert proposal.inputs.leakage_inductance_h == inputs.leakage_inductance_h
    if topology == "T11":
        assert proposal.inputs.turns_ratio_np_ns == pytest.approx(110/12)
        assert proposal.result.metrics["actual_switching_frequency_hz"] <= inputs.fsw_hz*(1+1e-12)
        assert proposal.result.metrics["drain_valley_voltage_v"] == pytest.approx(215)
    else:
        assert proposal.result.duty == pytest.approx(.45, abs=2e-6)
    if topology == "T04":
        assert abs(proposal.result.diagnostics["periodic_normalized_residual"]) < 1e-6
    if topology == "T18":
        assert any("ZVS" in warning for warning in proposal.result.warnings)


@pytest.mark.parametrize("topology", sorted(RATIO_TOPOLOGIES | TWO_INDUCTOR_TOPOLOGIES | {"T01", "T02", "T03", "T05"}))
def test_save_restore_and_report_keep_actual_values_and_validated_provenance(topology, tmp_path):
    inputs = example(topology)
    targets = SuggestionTargets(.6, .6 if topology == "T02" else .45,
                                secondary_ripple_pp_a=.7 if topology in TWO_INDUCTOR_TOPOLOGIES else None,
                                magnetizing_ripple_pp_a=.1 if topology in MAGNETIZING_TOPOLOGIES else None,
                                size_turns_ratio=topology in RATIO_TOPOLOGIES)
    proposal = suggest_parameters(inputs, targets)
    path = tmp_path / "proposal.json"
    save_project(proposal.inputs, path, parameter_suggestion=proposal.record())
    restored, document = read_project(path)
    assert restored == proposal.inputs
    assert document["parameter_suggestion"] == proposal.record()
    report = tmp_path / "proposal.html"
    write_report(proposal.result, report, parameter_suggestion=document["parameter_suggestion"])
    assert proposal.source_design_id in report.read_text(encoding="utf-8")
    assert proposal.result.design_id in report.read_text(encoding="utf-8")
    assert "volt-second-targets-1" in report.read_text(encoding="utf-8")
    document["parameter_suggestion"]["targets"]["ripple_pp_a"] *= 2
    path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(ValueError, match="target"):
        read_project(path)


@pytest.mark.parametrize("mutation", ["identity", "achieved", "source", "changes", "algorithm"])
def test_provenance_tampering_rejected(mutation):
    proposal = suggest_parameters(example("T01"), SuggestionTargets(.6, .45))
    record = copy.deepcopy(proposal.record())
    if mutation == "identity":
        record["result_design_id"] = "different"
    elif mutation == "achieved":
        record["achieved"]["duty"] = .1
    elif mutation == "source":
        record["source_design_id"] = "different"
    elif mutation == "changes":
        record["changed_fields"] = []
    else:
        record["algorithm"] = "fabricated"
    with pytest.raises(ValueError):
        validate_suggestion_record(record, proposal.result)


@pytest.mark.parametrize("bad", [0, -1, math.nan, math.inf, True, "3"])
def test_invalid_target_scalar_rejected(bad):
    with pytest.raises(ModelDomainError):
        suggest_parameters(example("T01"), SuggestionTargets(bad, .45))


@pytest.mark.parametrize("topology,targets,message", [
    ("T01", SuggestionTargets(.6, .2), "exceeds"),
    ("T10", SuggestionTargets(.6, .6, size_turns_ratio=True), "reflected"),
    ("T13", SuggestionTargets(.6, .7, size_turns_ratio=True), "reset"),
    ("T11", SuggestionTargets(.6, .45, size_turns_ratio=True), "uniquely"),
    ("T11", SuggestionTargets(.6, .45, size_turns_ratio=True, reflected_voltage_v=400), "first-zero-clamp frequency"),
    ("T17", SuggestionTargets(5, .45), "CCM"),
    ("T19", SuggestionTargets(10, .45, secondary_ripple_pp_a=3), "Iout"),
    ("T06", SuggestionTargets(.6, .45), "L2"),
    ("T20", SuggestionTargets(.6, .45), "LLC"),
])
def test_infeasible_or_underspecified_targets_are_not_silently_replaced(topology, targets, message):
    with pytest.raises(ModelDomainError, match=message):
        suggest_parameters(example(topology), targets)


@pytest.mark.parametrize("topology", ["T01", "T04", "T11", "T18"])
def test_cancelled_proposal_never_returns(topology):
    with pytest.raises(InterruptedError):
        suggest_parameters(example(topology), SuggestionTargets(.6, .9), cancelled=lambda: True)


def test_numerical_search_checks_cancellation_inside_model_calls():
    calls = 0

    def cancelled():
        nonlocal calls
        calls += 1
        return calls > 12

    with pytest.raises(InterruptedError):
        suggest_parameters(example("T18"), SuggestionTargets(.6, .9), cancelled=cancelled)
    assert calls > 12


def test_historical_achieved_values_allow_documented_roundoff_not_false_targets():
    proposal = suggest_parameters(example("T01"), SuggestionTargets(.6, .45))
    record = proposal.record()
    record["achieved"]["ripple_pp_a"] *= 1+1e-7
    assert validate_suggestion_record(record, proposal.result) == record
    record["achieved"]["ripple_pp_a"] *= 1.01
    with pytest.raises(ValueError, match="recomputation"):
        validate_suggestion_record(record, proposal.result)


@pytest.mark.parametrize("field,value", [("duty", math.nan), ("ripple_pp_a", True), ("mode", "DCM"), ("switching_frequency_hz", "100000")])
def test_historical_achieved_values_reject_nonfinite_wrong_type_or_mode(field, value):
    proposal = suggest_parameters(example("T01"), SuggestionTargets(.6, .45))
    record = proposal.record()
    record["achieved"][field] = value
    with pytest.raises(ValueError):
        validate_suggestion_record(record, proposal.result)


def test_changed_ratio_target_cannot_relabel_old_proposal():
    proposal = suggest_parameters(example("T09"), SuggestionTargets(.6, .42, size_turns_ratio=True))
    record = proposal.record()
    record["targets"]["max_duty"] = .49
    with pytest.raises(ValueError, match="Ratio suggestion"):
        validate_suggestion_record(record, proposal.result)


def test_edited_specification_cannot_be_claimed_as_a_component_proposal():
    proposal = suggest_parameters(example("T01"), SuggestionTargets(.6, .45))
    record = proposal.record()
    source = replace(proposal.source_inputs, vin_v=13)
    record["source_inputs"]["vin_v"] = 13
    record["source_design_id"] = source.design_id
    record["changed_fields"] = ["vin_v", "inductance_h"]
    with pytest.raises(ValueError, match="specifications"):
        validate_suggestion_record(record, proposal.result)


@pytest.mark.parametrize("field,value", [("source_inputs", []), ("targets", None), ("changed_fields", "inductance_h"), ("assumptions", [4]), ("achieved", {})])
def test_malformed_provenance_rejected(field, value):
    proposal = suggest_parameters(example("T01"), SuggestionTargets(.6, .45))
    record = proposal.record()
    record[field] = value
    with pytest.raises(ValueError):
        validate_suggestion_record(record, proposal.result)


def test_qr_valley_frequency_ceiling_leaves_a_real_ripple_gap():
    """Independent constant-Coss energy/time equations locate the valley jump.

    At the first-valley 100 kHz boundary, ripple drops from 0.430604 A
    (valley 2) to 0.359916 A (valley 1); 0.375123456 A is inside that gap.
    A root finder must not publish the transition as a solved ripple target.
    """
    from scipy.optimize import brentq

    vin, reflected, power, capacitance = 325, 115, 12, 100e-12

    def independent_cycle(inductance, valley):
        impedance = math.sqrt(inductance/capacitance)
        omega = 1/math.sqrt(inductance*capacitance)

        def values(peak):
            transferred_peak = math.sqrt(peak**2+(vin**2-reflected**2)/impedance**2)
            angle = math.atan2(vin, peak*impedance)+math.asin(reflected/math.hypot(vin, peak*impedance))
            period = (inductance*peak/vin+angle/omega+inductance*transferred_peak/reflected
                      +(2*valley-1)*math.pi/omega)
            delivered_power = .5*inductance*transferred_peak**2/period
            ripple = math.hypot(peak, vin/impedance)+reflected/impedance
            return delivered_power, 1/period, ripple

        peak = brentq(lambda value: values(value)[0]-power, 0, 10)
        return values(peak)

    boundary = brentq(lambda inductance: independent_cycle(inductance, 1)[1]-100e3, .001, .004)
    assert boundary == pytest.approx(.00214895622689, rel=1e-9)
    _, frequency_one, ripple_one = independent_cycle(boundary, 1)
    _, frequency_two, ripple_two = independent_cycle(boundary, 2)
    assert frequency_one == pytest.approx(100e3)
    assert frequency_two < 100e3
    assert ripple_one < .375123456 < ripple_two
    inputs = replace(example("T11"), turns_ratio_np_ns=reflected/12)
    with pytest.raises(ModelDomainError, match="valley transitions|misses its target"):
        suggest_parameters(inputs, SuggestionTargets(.375123456, .45))

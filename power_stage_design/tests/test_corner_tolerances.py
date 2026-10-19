"""Explicit tolerance grid: independent enumeration, plant KCL and persistence."""
import json
from dataclasses import asdict, replace
from itertools import product

import numpy as np
import pytest

from power_stage_design import loop
from power_stage_design.corners import (
    CornerRequest,
    analyze_corners,
    validate_corner_request,
)
from power_stage_design.loop import Compensator, LoopPlant
from power_stage_design.models import DesignInputs
from power_stage_design.project import read_project, save_project
from power_stage_design.topologies import calculate_stage

NEW_FIELDS = ("secondary_inductance_factors", "loop_capacitance2_factors",
              "loop_esr_factors", "loop_esr2_factors", "loop_dcr_factors")


@pytest.fixture
def setup(monkeypatch):
    # Genuine calculations with enough samples for corner-binding checks. The
    # complete standard grid is exercised separately by real desktop tests.
    original = loop.analyze_loop
    grid = np.geomspace(.1, 49_900, 160)
    monkeypatch.setattr(loop, "analyze_loop", lambda p, c: original(p, c, grid))
    inputs = DesignInputs("T01", 12, 5, 3, 100e3, 47e-6)
    plant = LoopPlant(dcr_ohm=.08, esr_ohm=.03, capacitance2_f=22e-6, esr2_ohm=.12)
    comp = Compensator(network="type_iii")
    settings = {"plant": asdict(plant), "compensator": asdict(comp)}
    return inputs, plant, comp, settings


def test_all_loop_tolerance_points_and_extrema_match_independent_enumeration(setup):
    inputs, plant, comp, settings = setup
    request = CornerRequest((10., 14.), (2., 3.), (.9, 1.1), (.7, 1.2),
                            loop_capacitance2_factors=(.5, 1.4),
                            loop_esr_factors=(.6, 1.5), loop_esr2_factors=(.7, 1.8),
                            loop_dcr_factors=(.8, 1.3))
    sweep = analyze_corners(inputs, request, loop_settings=settings)
    assert len(sweep.points) == 256
    expected_values = {}
    for index, (vin, current, lf, cf, c2f, e1f, e2f, df) in enumerate(product(
            (10., 14.), (2., 3.), (.9, 1.1), (.7, 1.2), (.5, 1.4), (.6, 1.5), (.7, 1.8), (.8, 1.3))):
        point = sweep.points[index]
        expected_inputs = replace(inputs, vin_v=vin, iout_a=current,
                                  inductance_h=47e-6*lf, capacitance_f=100e-6*cf)
        expected_plant = replace(plant, vin_v=vin, iout_a=current,
                                inductance_h=47e-6*lf, capacitance_f=100e-6*cf,
                                capacitance2_f=22e-6*c2f, esr_ohm=.03*e1f,
                                esr2_ohm=.12*e2f, dcr_ohm=.08*df)
        assert point.inputs == expected_inputs
        assert point.loop_plant == expected_plant
        standalone = loop.analyze_loop(expected_plant, comp)
        # Independent KCL: source → L+DCR → parallel load/(C1+ESR1)/(C2+ESR2).
        s = 2j*np.pi*standalone.frequency_hz
        y = current/5 + 1/(.03*e1f + 1/(s*100e-6*cf)) + 1/(.12*e2f + 1/(s*22e-6*c2f))
        physical_plant = vin / (1 + (s*47e-6*lf + .08*df)*y)
        np.testing.assert_allclose(standalone.plant_response, physical_plant, rtol=1e-12)
        for metric, attribute in (("loop.phase_margin_deg", "worst_phase_margin_deg"),
                                  ("loop.gain_margin_db", "worst_gain_margin_db"),
                                  ("loop.crossover_hz", "main_crossover_hz"), ("loop.ms", "ms")):
            value = getattr(standalone.stability, attribute)
            if value is not None and np.isfinite(value):
                assert point.metrics[metric] == pytest.approx(value, rel=1e-13)
                expected_values.setdefault(metric, []).append((index, value))
            else:
                assert metric not in point.metrics
        # Loop C2 and parasitics must not be silently added to the ideal stage C.
        ripple = (vin-5)*(5/vin)/(47e-6*lf*100e3)
        assert point.metrics["output_ripple_pp_v"] == pytest.approx(ripple/(8*100e3*100e-6*cf))
        assert point.design_id == calculate_stage(expected_inputs).design_id
    assert sweep.controller == asdict(comp)
    assert sweep.loop_identity.plant == plant
    assert len({p.point_id for p in sweep.points}) == 256
    assert len({p.design_id for p in sweep.points}) == 16
    for metric, values in expected_values.items():
        bounds = sweep.extrema[metric]
        for kind, selector in (("minimum", min), ("maximum", max)):
            expected_index, expected_value = selector(values, key=lambda item: item[1])
            assert bounds[kind]["point_index"] == expected_index
            assert bounds[kind]["point_id"] == sweep.points[expected_index].point_id
            assert bounds[kind]["value"] == expected_value
        assert bounds["evaluated_points"] == len(values)
        assert bounds["requested_points"] == 256
    assert "loop-only" in sweep.evidence_scope
    assert "total_loss_w" not in sweep.extrema


@pytest.mark.parametrize("topology", ["T06", "T07", "T08", "T19"])
def test_secondary_inductor_is_independent_actual_second_winding(topology):
    if topology == "T19":
        inputs = DesignInputs(topology, 12, 1.2, 10, 500e3, 1e-6,
                              secondary_inductance_h=1.3e-6, transfer_capacitance_f=20e-6)
    else:
        inputs = DesignInputs(topology, 48, 5, 2, 100e3, 470e-6,
                              capacitance_f=1e-3, secondary_inductance_h=100e-6, transfer_capacitance_f=1e-3)
    sweep = analyze_corners(inputs, CornerRequest((inputs.vin_v,), (inputs.iout_a,),
                            secondary_inductance_factors=(.8, 1.2)))
    assert len(sweep.points) == 2
    for point, factor in zip(sweep.points, (.8, 1.2)):
        assert point.inputs.secondary_inductance_h == inputs.secondary_inductance_h*factor
        assert point.inputs.inductance_h == inputs.inductance_h
        standalone = calculate_stage(replace(inputs, secondary_inductance_h=inputs.secondary_inductance_h*factor))
        assert point.metrics["L2.current_max_a"] == standalone.stress["L2"]["current_max_a"]
        # Volt-second balance across an independently measured ON interval.
        current = standalone.waveforms["L2"].current_a.statistics()
        voltage = standalone.waveforms["L2"].voltage_v.statistics()
        expected_ripple = voltage["max"]*standalone.duty/(inputs.fsw_hz*inputs.secondary_inductance_h*factor)
        assert current["peak_to_peak"] == pytest.approx(expected_ripple)
    assert sweep.points[0].point_id != sweep.points[1].point_id


@pytest.mark.parametrize("field", NEW_FIELDS)
def test_nontrivial_unmodeled_tolerance_is_rejected_not_ignored(setup, field):
    inputs, _, _, _ = setup
    request = CornerRequest((12.,), (3.,), **{field: (.8, 1.2)})
    with pytest.raises(ValueError, match="requires|supported"):
        analyze_corners(inputs, request)


@pytest.mark.parametrize("field, change", [
    ("loop_capacitance2_factors", {"capacitance2_f": 0.}),
    ("loop_esr2_factors", {"capacitance2_f": 0.}),
    ("loop_esr_factors", {"esr_ohm": 0.}),
    ("loop_esr2_factors", {"esr2_ohm": 0.}),
    ("loop_dcr_factors", {"dcr_ohm": 0.}),
])
def test_zero_or_disabled_nominal_cannot_claim_a_tolerance_scan(setup, field, change):
    inputs, plant, comp, _ = setup
    with pytest.raises(ValueError, match="positive|C2"):
        analyze_corners(inputs, CornerRequest((12.,), (3.,), **{field: (.8, 1.2)}),
                        loop_settings={"plant": asdict(replace(plant, **change)), "compensator": asdict(comp)})


@pytest.mark.parametrize("field", NEW_FIELDS)
@pytest.mark.parametrize("values", [(), (0.,), (-1.,), (float("nan"),), (float("inf"),), (True,), "1"])
def test_new_factors_obey_same_finite_positive_and_sequence_contract(field, values):
    with pytest.raises(ValueError):
        validate_corner_request(CornerRequest((12.,), (3.,), **{field: values}))


def test_entire_grid_counts_towards_cap_before_any_evaluation():
    request = CornerRequest((12., 13.), (2., 3.), (.9, 1.1), (.8, 1.2),
                            **{key: (.9, 1.1) for key in NEW_FIELDS})
    with pytest.raises(ValueError, match="500"):
        validate_corner_request(request)


def test_legacy_project_defaults_only_missing_new_dimensions_and_recomputes(setup, tmp_path):
    inputs, _, _, settings = setup
    legacy = {"input_voltages_v": [12.], "output_currents_a": [3.],
              "inductance_factors": [1.], "capacitance_factors": [1.]}
    path = save_project(inputs, tmp_path / "legacy.json", loop_settings=settings, corner_request=legacy)
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["corner_request"] = legacy
    path.write_text(json.dumps(raw), encoding="utf-8")
    restored, document = read_project(path)
    assert document["corner_request"]["input_voltages_v"] == [12.]
    for key in NEW_FIELDS:
        assert document["corner_request"][key] == [1.]
    sweep = analyze_corners(restored, CornerRequest(**document["corner_request"]), loop_settings=settings)
    assert len(sweep.points) == 1
    assert sweep.points[0].loop_plant.capacitance2_f == 22e-6


def test_invalid_context_in_saved_request_is_rejected_before_restore(setup, tmp_path):
    inputs, _, _, settings = setup
    path = save_project(inputs, tmp_path / "bad.json", loop_settings=settings)
    document = json.loads(path.read_text(encoding="utf-8"))
    document["loop_settings"] = None
    document["corner_request"] = asdict(CornerRequest((12.,), (3.,), loop_capacitance2_factors=(.5, 1.)))
    path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(ValueError, match="requires"):
        read_project(path)


def test_invalid_mode_remains_missing_loop_metric_with_full_corner_identity(setup):
    inputs, _, _, settings = setup
    sweep = analyze_corners(inputs, CornerRequest((4., 12.), (.01, 3.), loop_capacitance2_factors=(.5, 1.)),
                            loop_settings=settings)
    assert len(sweep.points) == 8
    assert len({point.point_id for point in sweep.points}) == 8
    assert sum(point.error is not None for point in sweep.points) == 4
    assert sum(point.loop_error is not None for point in sweep.points) == 2
    assert sweep.extrema["loop.phase_margin_deg"]["evaluated_points"] == 2
    for point in sweep.points:
        if point.error or point.loop_error:
            assert "loop.phase_margin_deg" not in point.metrics


@pytest.mark.parametrize("stage_topology, plant_topology, mode", [
    ("T01", "buck", "vmc"), ("T01", "buck", "peak_cmc"),
    ("T02", "boost", "peak_cmc"), ("T03", "inverting_buck_boost", "peak_cmc"),
    ("T09", "flyback", "peak_cmc"), ("T13", "forward", "peak_cmc"), ("T14", "forward", "peak_cmc"),
])
@pytest.mark.parametrize("network", ["type_ii", "type_ii_gm", "type_iii", "isolated_zener", "isolated_inner"])
def test_tolerances_keep_the_exact_controller_across_supported_loop_combinations(setup, stage_topology, plant_topology, mode, network):
    _, template, _, _ = setup
    vin, vout, inductance = (24., 48., 1e-3) if stage_topology == "T02" else (48., 5., 470e-6)
    inputs = DesignInputs(stage_topology, vin, vout, 3., 100e3, inductance,
                          magnetizing_inductance_h=1e-3)
    plant = replace(template, topology=plant_topology, control_mode=mode, vin_v=vin,
                    vout_v=-vout if stage_topology == "T03" else vout, inductance_h=inductance, slope_v=1.2)
    comp = Compensator(network=network, r2_ohm=12345.678912345, gbw_hz=1.23456789e6,
                       output_resistance_ohm=2.34567891e6, ctr=.723456789)
    request = CornerRequest((vin,), (3.,), loop_capacitance2_factors=(.8, 1.2),
                            loop_esr_factors=(.5, 1.5), loop_esr2_factors=(.7, 1.3), loop_dcr_factors=(.8, 1.2))
    sweep = analyze_corners(inputs, request, loop_settings={"plant": asdict(plant), "compensator": asdict(comp)})
    assert sweep.controller == asdict(comp)
    assert sweep.loop_identity.compensator == comp
    for point, (c2f, e1f, e2f, df) in zip(sweep.points, product((.8, 1.2), (.5, 1.5), (.7, 1.3), (.8, 1.2))):
        expected = replace(plant, capacitance2_f=22e-6*c2f, esr_ohm=.03*e1f, esr2_ohm=.12*e2f, dcr_ohm=.08*df)
        assert point.loop_plant == expected
        actual = loop.analyze_loop(expected, comp)
        assert point.metrics["loop.ms"] == actual.stability.ms
        if actual.stability.worst_phase_margin_deg is not None:
            assert point.metrics["loop.phase_margin_deg"] == actual.stability.worst_phase_margin_deg


def test_loop_only_dimensions_keep_known_q1_loss_partial_and_unchanged(setup):
    from llc_design.models.devices import DeviceDatabase

    inputs, _, _, settings = setup
    device = DeviceDatabase().primary[0]
    sweep = analyze_corners(inputs, CornerRequest((12.,), (3.,), loop_dcr_factors=(.5, 2.),
                            loop_esr_factors=(.5, 2.)), loop_settings=settings, selected_device=device)
    ripple = (12.-5.)*(5./12.)/(47e-6*100e3)
    expected = (3.**2+ripple**2/12)*(5./12.)*device.rds_at(100.)
    for point in sweep.points:
        assert point.metrics["Q1.known_conduction_loss_w"] == pytest.approx(expected)
        assert "total_loss_w" not in point.metrics
    assert len({point.design_id for point in sweep.points}) == 1
    assert len({point.point_id for point in sweep.points}) == 4


def test_secondary_inductor_outside_existing_ccm_domain_has_no_fake_extrema():
    inputs = DesignInputs("T19", 12, 1.2, 10, 500e3, 1e-6,
                          secondary_inductance_h=1.3e-6, transfer_capacitance_f=20e-6)
    sweep = analyze_corners(inputs, CornerRequest((12.,), (10.,), secondary_inductance_factors=(.01, 1.)))
    assert sweep.points[0].error and sweep.points[0].metrics == {}
    assert sweep.points[1].error is None
    assert sweep.extrema["L2.current_max_a"]["evaluated_points"] == 1
    assert sweep.extrema["L2.current_max_a"]["requested_points"] == 2


def test_new_factor_precision_and_immutable_request_survive_project_roundtrip(setup, tmp_path):
    inputs, _, _, settings = setup
    source = [.8123456789123456, 1.1234567891234567]
    request = CornerRequest((12.,), (3.,), loop_esr_factors=source)
    source[0] = 999.
    assert request.loop_esr_factors[0] == .8123456789123456
    path = save_project(inputs, tmp_path / "precise.json", loop_settings=settings, corner_request=asdict(request))
    restored, document = read_project(path)
    fresh = analyze_corners(restored, CornerRequest(**document["corner_request"]), loop_settings=settings)
    expected = analyze_corners(inputs, request, loop_settings=settings)
    assert fresh == expected

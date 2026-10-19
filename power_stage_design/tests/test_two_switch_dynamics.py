"""Independent circuit, charge, and energy evidence for the T05 extension.

The reference integrator stamps a component graph, uses a different state
ordering and BDF, and consumes no production RHS or duty formula.  A second
oracle uses the passive two-capacitor resonance matrix exponential.  Neither
test implies measured device data, TI executable parity, or hardware proof.
"""
import json
import math
from bisect import bisect_right
from dataclasses import FrozenInstanceError, asdict, replace

import numpy as np
import pytest
from scipy.integrate import quad, solve_ivp
from scipy.linalg import expm

from power_stage_design.models import DesignInputs, ModelDomainError
from power_stage_design.two_switch_dynamics import (
    T05Options,
    calculate_two_switch_dynamic,
)


def _input(**changes):
    return replace(DesignInputs("T05", 24., 12., .1, 100e3, 100e-6), **changes)


def _at(wave, phase):
    index = min(len(wave.segments)-1, bisect_right([s.start_fraction for s in wave.segments], phase)-1)
    segment = wave.segments[index]
    return float(segment.evaluate((phase-segment.start_fraction)/segment.duration_fraction))


def _current(result, key, phase):
    return _at(result.waveforms[key].current_a, phase)


def _voltage(result, key, phase):
    return _at(result.waveforms[key].voltage_v, phase)


@pytest.fixture(scope="module")
def dcm():
    return calculate_two_switch_dynamic(_input(), T05Options())


@pytest.fixture(scope="module")
def clamped():
    p = _input(vin_v=12., vout_v=24., iout_a=.02, diode_drop_v=.4)
    o = T05Options(coss_q1_f=.7e-9, coss_q2_f=2e-9)
    return calculate_two_switch_dynamic(p, o), o


def test_dcm_solves_real_floating_nodes_and_reports_scope(dcm):
    assert dcm.mode == "DCM"
    assert 0 < dcm.duty < 1
    assert dcm.metrics["rectifiers_off_fraction"] > .2
    assert dcm.stress["L1"]["current_min_a"] < 0
    assert dcm.metrics["solved_mean_output_v"] == pytest.approx(12., rel=1e-7)
    assert {"Q1", "Q2", "D1", "D2", "Coss1", "Coss2", "Dbody1", "Dbody2", "L1", "Rwinding", "Cout", "Cin"} == set(dcm.waveforms)
    assert dcm.provenance["physics_revision"] == "t05-switched-1"
    assert json.loads(dcm.provenance["options_json"])["coss_q1_f"] == 1e-9
    assert any("TI executable equivalence" in warning and "UNKNOWN" in warning for warning in dcm.warnings)
    assert dcm.diagnostics["integration_refinement_max_normalized_error"] < 1e-6
    assert dcm.diagnostics["periodic_state_normalized_residual"] < 1e-6
    assert dcm.diagnostics["waveform_constitutive_max_normalized_residual"] < 2e-4
    assert dcm.stress["Q1"]["current_max_a"] > 10*dcm.stress["L1"]["current_max_a"]
    # A real finite-R capacitive charging pulse, not a voltage step with zero
    # switching stress and a synthetic energy loss added afterwards.
    assert dcm.metrics["q1_channel_loss_w"] > 0
    assert dcm.metrics["q2_channel_loss_w"] > 0


@pytest.mark.parametrize("case", ["default", "body_clamp"])
def test_graph_stamped_reference_cycle_matches_nodes_and_energy(dcm, clamped, case):
    if case == "body_clamp":
        dcm = clamped[0]
    p, o = dcm.inputs, dcm.inputs.two_switch_options
    period = 1/p.fsw_hz
    # Nodes: VIN, A, B, OUT, GND. State: [A, B, OUT, iL, Ein, Eload, Eloss].
    initial = np.array([p.vin_v-_voltage(dcm, "Q1", 0.), _voltage(dcm, "Q2", 0.),
                        _voltage(dcm, "Cout", 0.), _current(dcm, "L1", 0.), 0., 0., 0.])
    state = initial.copy()
    diode_edges = ((4, 1, p.diode_drop_v, o.diode_resistance_ohm),
                   (2, 3, p.diode_drop_v, o.diode_resistance_ohm),
                   (1, 0, o.body_diode_drop_v, o.body_diode_resistance_ohm),
                   (4, 2, o.body_diode_drop_v, o.body_diode_resistance_ohm))
    for start, stop, gate in ((0., dcm.duty*period, True), (dcm.duty*period, period, False)):
        def derivative(t, y, gate=gate):
            potentials = np.array([p.vin_v, y[0], y[1], y[2], 0.])
            net = np.zeros(5)
            losses = o.inductor_resistance_ohm*y[3]**2
            def stamp(source, destination, current):
                net[source] -= current
                net[destination] += current
            for source, destination, resistance in ((0, 1, o.ron_q1_ohm), (2, 4, o.ron_q2_ohm)):
                current = (potentials[source]-potentials[destination])/resistance if gate else 0.
                stamp(source, destination, current)
                losses += current*current*resistance
            for source, destination, drop, resistance in diode_edges:
                current = max(0., (potentials[source]-potentials[destination]-drop)/resistance)
                stamp(source, destination, current)
                losses += current*drop+current*current*resistance
            stamp(1, 2, y[3])
            stamp(3, 4, p.iout_a)
            rates = net[1:4]/np.array([o.coss_q1_f, o.coss_q2_f, p.capacitance_f])
            source_current = -net[0]-o.coss_q1_f*rates[0]
            return np.r_[rates, (potentials[1]-potentials[2]-o.inductor_resistance_ohm*y[3])/p.inductance_h,
                         p.vin_v*source_current, y[2]*p.iout_a, losses]
        solution = solve_ivp(derivative, (start, stop), state, method="BDF", rtol=1e-10,
                             atol=np.array([1e-10, 1e-10, 1e-10, 1e-12, 1e-13, 1e-13, 1e-13]),
                             max_step=period/500, dense_output=True)
        assert solution.success
        for time in np.linspace(start, stop, 41)[1:-1]:
            expected = solution.sol(time)
            phase = time/period
            observed = [p.vin_v-_voltage(dcm, "Q1", phase), _voltage(dcm, "Q2", phase),
                        _voltage(dcm, "Cout", phase), _current(dcm, "L1", phase)]
            assert observed == pytest.approx(expected[:4], abs=5e-6, rel=2e-5)
        state = solution.y[:, -1]
    assert state[:4] == pytest.approx(initial[:4], abs=5e-6)
    assert state[4]/period == pytest.approx(dcm.metrics["input_power_w"], rel=2e-6)
    assert state[5]/period == pytest.approx(dcm.metrics["output_power_w"], rel=2e-6)
    assert state[6]/period == pytest.approx(dcm.metrics["modeled_other_loss_w"]+dcm.metrics["modeled_diode_loss_w"], rel=2e-6)
    assert abs(state[4]-state[5]-state[6]) < 1e-9


def test_unclamped_floating_interval_matches_passive_resonance_exponential(dcm):
    p, o = dcm.inputs, T05Options()
    intervals = []
    current_start = None
    for segment in dcm.waveforms["L1"].current_a.segments:
        phase = (segment.start_fraction+segment.end_fraction)/2
        floating = phase > dcm.duty and all(abs(_current(dcm, key, phase)) < 1e-11 for key in ("D1", "D2", "Dbody1", "Dbody2"))
        if floating:
            if current_start is None:
                current_start = segment.start_fraction
            end = segment.end_fraction
        elif current_start is not None:
            intervals.append((current_start, end))
            current_start = None
    if current_start is not None:
        intervals.append((current_start, end))
    start, stop = max(intervals, key=lambda pair: pair[1]-pair[0])
    assert stop-start > .03
    a0 = p.vin_v-_voltage(dcm, "Q1", start)
    b0 = _voltage(dcm, "Q2", start)
    initial = np.array([_current(dcm, "L1", start), a0-b0])
    common_charge = o.coss_q1_f*a0+o.coss_q2_f*b0
    matrix = np.array([[-o.inductor_resistance_ohm/p.inductance_h, 1/p.inductance_h],
                       [-1/o.coss_q1_f-1/o.coss_q2_f, 0.]])
    for phase in np.linspace(start, stop, 21)[1:-1]:
        expected = expm(matrix*((phase-start)/p.fsw_hz)) @ initial
        va = (common_charge+o.coss_q2_f*expected[1])/(o.coss_q1_f+o.coss_q2_f)
        vb = (common_charge-o.coss_q1_f*expected[1])/(o.coss_q1_f+o.coss_q2_f)
        assert _current(dcm, "L1", phase) == pytest.approx(expected[0], abs=2e-8)
        assert p.vin_v-_voltage(dcm, "Q1", phase) == pytest.approx(va, abs=2e-6)
        assert _voltage(dcm, "Q2", phase) == pytest.approx(vb, abs=2e-6)


def test_returned_branch_kcl_constitutive_laws_and_passivity(dcm):
    p, o = dcm.inputs, T05Options()
    for phase in np.linspace(.001, .999, 121):
        current = {key: _current(dcm, key, phase) for key in dcm.waveforms}
        va, vb = p.vin_v-_voltage(dcm, "Q1", phase), _voltage(dcm, "Q2", phase)
        vout = _voltage(dcm, "Cout", phase)
        assert current["Q1"]+current["Coss1"]-current["Dbody1"]+current["D1"] == pytest.approx(current["L1"], abs=2e-10)
        assert current["L1"]+current["Dbody2"] == pytest.approx(current["Q2"]+current["Coss2"]+current["D2"], abs=2e-10)
        assert current["D2"] == pytest.approx(current["Cout"]+p.iout_a, abs=1e-10)
        assert _voltage(dcm, "L1", phase)+_voltage(dcm, "Rwinding", phase) == pytest.approx(va-vb, abs=1e-10)
        assert current["D1"] == pytest.approx(max(0., (-va-p.diode_drop_v)/o.diode_resistance_ohm), abs=1e-9)
        assert current["D2"] == pytest.approx(max(0., (vb-vout-p.diode_drop_v)/o.diode_resistance_ohm), abs=1e-9)
        for key in ("D1", "D2", "Dbody1", "Dbody2"):
            assert current[key] >= -1e-10
            assert -current[key]*_voltage(dcm, key, phase) >= -1e-9
    for key in ("Coss1", "Coss2", "Cout"):
        assert abs(dcm.diagnostics[f"{key.lower()}_charge_balance_c"]) < 1e-12
    assert abs(dcm.diagnostics["trajectory_energy_balance_w"]) < 1e-6
    assert abs(dcm.diagnostics["output_capacitor_mean_current_a"]) < 1e-7


def test_asymmetric_caps_step_up_and_body_clamp_are_real_branches(clamped):
    result, options = clamped
    assert result.mode == "DCM"
    assert result.stress["Dbody1"]["current_max_a"] > .001
    assert result.metrics["body_clamp_loss_w"] > 0
    assert result.metrics["external_rectifier_loss_w"] > 0
    assert result.stress["Coss1"]["voltage_pp_v"] != pytest.approx(result.stress["Coss2"]["voltage_pp_v"])
    assert abs(result.diagnostics["trajectory_energy_balance_w"]) < 1e-6
    assert result.metrics["solved_mean_output_v"] == pytest.approx(24., rel=1e-7)
    assert json.loads(result.provenance["options_json"])["coss_q2_f"] == options.coss_q2_f


def test_statistics_are_integrals_of_the_actual_finite_transition(dcm):
    for key in ("Q1", "D1", "Coss1", "L1"):
        wave = dcm.waveforms[key].current_a
        mean = sum(s.duration_fraction*quad(lambda u, s=s: float(s.evaluate(u)), 0, 1, epsabs=1e-11)[0] for s in wave.segments)
        square = sum(s.duration_fraction*quad(lambda u, s=s: float(s.evaluate(u))**2, 0, 1, epsabs=1e-11)[0] for s in wave.segments)
        assert dcm.stress[key]["current_mean_a"] == pytest.approx(mean, abs=1e-10)
        assert dcm.stress[key]["current_rms_a"] == pytest.approx(math.sqrt(square), rel=1e-10)


def test_dynamic_ccm_has_finite_loss_and_closes_output():
    result = calculate_two_switch_dynamic(_input(iout_a=2., diode_drop_v=.4), T05Options())
    assert result.mode == "CCM"
    assert result.stress["L1"]["current_min_a"] > 0
    assert result.metrics["input_power_w"] > 24.
    assert result.metrics["solved_mean_output_v"] == pytest.approx(12., rel=1e-7)
    assert abs(result.diagnostics["trajectory_energy_balance_w"]) < 1e-5


def test_actual_options_are_immutable_bound_and_serializable(dcm):
    options = T05Options()
    assert dcm.inputs.two_switch_options == options
    assert dcm.design_id == replace(_input(), two_switch_options=options).design_id
    assert dcm.design_id != _input().design_id
    restored = DesignInputs(**json.loads(json.dumps(asdict(dcm.inputs))))
    assert restored == dcm.inputs
    assert restored.design_id == dcm.design_id
    changed = replace(dcm.inputs, two_switch_options=replace(options, coss_q1_f=2e-9))
    assert changed.design_id != dcm.design_id
    with pytest.raises(ModelDomainError, match="revision"):
        DesignInputs(**{**asdict(_input()), "two_switch_options": {"model_revision": "future-unverified"}})


def test_default_ideal_baseline_does_not_implicitly_enable_dynamic_model():
    from power_stage_design.topologies import calculate_stage
    with pytest.raises(ModelDomainError, match="floating-node"):
        calculate_stage(_input())
    ideal = calculate_stage(_input(iout_a=2.))
    assert ideal.inputs.two_switch_options is None
    assert "Coss1" not in ideal.waveforms
    assert ideal.metrics["modeled_other_loss_w"] == 0


def test_requested_mode_is_enforced():
    with pytest.raises(ModelDomainError, match="requires DCM"):
        calculate_two_switch_dynamic(_input(mode="CCM"), T05Options())


@pytest.mark.parametrize("field,value", [
    ("coss_q1_f", 0.), ("coss_q2_f", -1e-9), ("ron_q1_ohm", 0.), ("ron_q2_ohm", float("nan")),
    ("diode_resistance_ohm", 0.), ("body_diode_drop_v", -1.), ("body_diode_resistance_ohm", 0.),
    ("inductor_resistance_ohm", -1.), ("relative_tolerance", .01), ("max_iterations", 0),
    ("max_iterations", True), ("gate_strategy", "four-switch"), ("model_revision", "invented"),
    ("parameter_source", ""), ("coss_q1_f", True),
])
def test_invalid_declared_parameters_rejected(field, value):
    with pytest.raises(ModelDomainError):
        calculate_two_switch_dynamic(_input(), replace(T05Options(), **{field: value}))


@pytest.mark.parametrize("changes", [{"topology": "T01"}, {"iout_a": 0.}, {"dead_time_s": 1e-7}])
def test_unsupported_conditions_rejected(changes):
    with pytest.raises(ModelDomainError):
        calculate_two_switch_dynamic(_input(**changes), T05Options())


def test_options_frozen_and_physical_resolution_guarded():
    with pytest.raises(FrozenInstanceError):
        T05Options().coss_q1_f = 2e-9
    with pytest.raises(ModelDomainError, match="resolution"):
        calculate_two_switch_dynamic(_input(), T05Options(coss_q1_f=1e-20))


def test_cancelled_before_and_during_integration():
    with pytest.raises(InterruptedError, match="cancelled"):
        calculate_two_switch_dynamic(_input(), T05Options(), cancelled=lambda: True)
    calls = 0
    def cancelled():
        nonlocal calls
        calls += 1
        return calls > 80
    with pytest.raises(InterruptedError, match="cancelled"):
        calculate_two_switch_dynamic(_input(), T05Options(), cancelled=cancelled)


def test_failed_periodic_shooting_does_not_publish_a_result():
    with pytest.raises(ModelDomainError, match="did not converge"):
        calculate_two_switch_dynamic(_input(), T05Options(max_iterations=1))

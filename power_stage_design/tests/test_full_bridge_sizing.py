"""T17 inverse targets checked against independent switched-circuit IVPs.

The oracle never calls the production bridge, sizing, waveform, or statistics
helpers. It integrates KVL/ampere-turns, output charge and source energy and
locates diode events. Fixture targets come from that IVP, not calculate_stage.
This verifies the stated ideal/constant-Vf scope, not switching/hardware data.
"""
from __future__ import annotations

import copy
import math
from dataclasses import fields, replace

import numpy as np
import pytest
from scipy.integrate import solve_ivp
from scipy.optimize import brentq

from power_stage_design import bridge_sizing
from power_stage_design.bridge_sizing import size_full_bridge
from power_stage_design.models import DesignInputs, ModelDomainError
from power_stage_design.topologies import calculate_stage


def _half_cycle(p, duty, low, initial_im, *, skip_freewheel=False):
    """Integrate in normalized half-cycle time; q and energy are H-averages."""
    h = .5/p.fsw_hz
    n, lm, lo = p.turns_ratio_np_ns, p.magnetizing_inductance_h, p.inductance_h
    u = p.vout_v+p.diode_drop_v
    pieces = []

    def integrate(start, stop, state, slopes, source, event=None):
        def derivative(t, y):
            return [*slopes, y[0], p.vin_v*(y[0]/n+y[1]) if source else 0.]
        solution = solve_ivp(derivative, (start, stop), state,
                             events=event, rtol=2e-11, atol=1e-13,
                             dense_output=True, max_step=.05)
        assert solution.success
        pieces.append(solution)
        return solution.t[-1], solution.y[:, -1]

    now, state = integrate(0., duty, [low, initial_im, 0., 0.],
                           ((p.vin_v/n-u)*h/lo, p.vin_v*h/lm), True)
    assert state[0] >= n*abs(state[1])-1e-10

    def diode_off(t, y):
        return y[0]-n*y[1]
    diode_off.terminal = True
    diode_off.direction = -1
    if skip_freewheel:
        assert state[0] == pytest.approx(n*state[1], abs=1e-12)
    else:
        now, state = integrate(now, 1., state, (-u*h/lo, 0.), False, diode_off)
    if now < 1.-1e-12:
        # KVL on the conducting secondary half plus iL=n*Im, rather than
        # reusing the production duty/charge reduction.
        slope = -u*h/(lo+lm/n**2)

        def no_energy(t, y):
            return y[0]
        no_energy.terminal = True
        no_energy.direction = -1
        now, state = integrate(now, 1., state, (slope, slope/n), False, no_energy)
        if now < 1.-1e-12:
            assert abs(state[0]) < 1e-10 and abs(state[1]) < 1e-10
            now, state = integrate(now, 1., state, (0., 0.), False)
    samples = np.concatenate([piece.sol(np.linspace(piece.t[0], piece.t[-1], 17))
                              for piece in pieces], axis=1)
    return state, pieces, samples


def _independent_fixture(branch, vf=0.):
    p = DesignInputs("T17", 48., 5., .1, 100e3, 470e-6,
                     capacitance_f=1e-3, turns_ratio_np_ns=2.,
                     magnetizing_inductance_h=.01, diode_drop_v=vf)
    nominal = (p.vout_v+vf)*p.turns_ratio_np_ns/p.vin_v
    if branch == "ordinary":
        duty, low = nominal, .08
        initial_im = -p.vin_v*duty/(4*p.fsw_hz*p.magnetizing_inductance_h)
    elif branch == "partial":
        duty = .72*nominal
        limit = p.turns_ratio_np_ns*p.vin_v*duty/(2*p.fsw_hz*p.magnetizing_inductance_h)
        low = brentq(lambda value: _half_cycle(p, duty, value, -value/p.turns_ratio_np_ns)[0][0]-value,
                     0., .9*limit, xtol=1e-15)
        initial_im = -low/p.turns_ratio_np_ns
    else:
        duty, low, initial_im = nominal*(.5 if branch == "boundary" else 1/3), 0., 0.
    final, pieces, samples = _half_cycle(p, duty, low, initial_im)
    assert final[0] == pytest.approx(low, abs=2e-12)
    assert final[1] == pytest.approx(-initial_im, abs=2e-12)
    actual = replace(p, iout_a=float(final[2]))
    ripple = float(np.ptp(samples[0]))
    magnetizing = float(2*np.max(np.abs(samples[1])))
    return actual, duty, ripple, magnetizing, pieces, samples


@pytest.mark.parametrize("branch", ["ordinary", "partial", "boundary", "dcm"])
@pytest.mark.parametrize("vf", [0., .4])
@pytest.mark.parametrize("size_lm,size_ratio", [(False, False), (True, False), (False, True), (True, True)])
def test_recovers_actual_components_from_independent_ivp_targets(branch, vf, size_lm, size_ratio):
    actual, duty, ripple, magnetizing, pieces, _ = _independent_fixture(branch, vf)
    source = replace(actual, inductance_h=120e-6,
                     magnetizing_inductance_h=.003 if size_lm else actual.magnetizing_inductance_h,
                     turns_ratio_np_ns=1.3 if size_ratio else actual.turns_ratio_np_ns,
                     secondary_inductance_h=300e-6, transfer_capacitance_f=220e-6,
                     leakage_inductance_h=1e-6, primary_capacitance_f=470e-12)
    unchanged = copy.deepcopy(source)
    result = size_full_bridge(source, ripple, max_duty=duty if size_ratio else .8,
                             magnetizing_ripple_pp_a=magnetizing if size_lm else None,
                             size_turns_ratio=size_ratio)
    assert source == unchanged
    assert result.inputs.inductance_h == pytest.approx(actual.inductance_h, rel=2e-9)
    assert result.inputs.magnetizing_inductance_h == pytest.approx(actual.magnetizing_inductance_h, rel=2e-9)
    assert result.inputs.turns_ratio_np_ns == pytest.approx(actual.turns_ratio_np_ns, rel=2e-9)
    assert result.duty == pytest.approx(duty, rel=2e-9)
    assert result.mode == {"ordinary": "CCM", "partial": "CCM", "boundary": "BCM", "dcm": "DCM"}[branch]
    assert result.metrics["inductor_ripple_pp_a"] == pytest.approx(ripple, rel=2e-9)
    assert result.stress["Lm"]["current_pp_a"] == pytest.approx(magnetizing, rel=2e-9)
    allowed = {"inductance_h", "magnetizing_inductance_h", "turns_ratio_np_ns"}
    for field in fields(source):
        if field.name not in allowed:
            assert getattr(result.inputs, field.name) == getattr(source, field.name)
    assert result.design_id == result.inputs.design_id

    # Check independently integrated pointwise currents at each event interval.
    for piece in pieces:
        for phase in np.linspace(piece.t[0], piece.t[-1], 7)[1:-1]:
            values = piece.sol(phase)
            for key, index in (("L1", 0), ("Lm", 1)):
                wave = result.waveforms[key].current_a
                segment = next(s for s in wave.segments if s.start_fraction <= phase/2 < s.end_fraction)
                observed = segment.evaluate((phase/2-segment.start_fraction)/segment.duration_fraction)
                assert observed == pytest.approx(values[index], rel=2e-8, abs=2e-10)
    # Source energy supplies output plus exactly one conducting secondary Vf.
    independent_power = pieces[-1].y[3, -1]
    assert independent_power == pytest.approx((actual.vout_v+vf)*actual.iout_a, rel=2e-10)
    assert result.metrics["input_power_w"] == pytest.approx(independent_power, rel=2e-9)
    assert result.stress["Cout"]["current_mean_a"] == pytest.approx(0., abs=1e-11)
    # Flux/charge closure were solved before the helper; final inductances give
    # the same stored-energy change on each numerical IVP segment.
    for piece in pieces:
        first, last = piece.y[:, 0], piece.y[:, -1]
        stored_change = (.5*actual.inductance_h*(last[0]**2-first[0]**2)
                         + .5*actual.magnetizing_inductance_h*(last[1]**2-first[1]**2))
        supplied = (last[3]-first[3]-(actual.vout_v+vf)*(last[2]-first[2]))/(2*actual.fsw_hz)
        assert stored_change == pytest.approx(supplied, rel=2e-8, abs=1e-14)


@pytest.mark.parametrize("load", [.02, .01, .005])
def test_original_light_load_counterexamples_use_true_joint_mode(load):
    p = DesignInputs("T17", 48, 5, load, 100e3, 470e-6,
                     capacitance_f=1e-3, turns_ratio_np_ns=2, magnetizing_inductance_h=.01)
    original = calculate_stage(p)
    result = size_full_bridge(replace(p, inductance_h=100e-6),
                             original.metrics["inductor_ripple_pp_a"], max_duty=.45)
    assert result.inputs.inductance_h == pytest.approx(p.inductance_h, rel=1e-10)
    assert result.mode == ("DCM" if load == .005 else "CCM")
    if load == .01:
        assert result.metrics["inductor_ripple_pp_a"] > 2*load
        assert result.stress["L1"]["current_min_a"] > 0


@pytest.mark.parametrize("branch", ["partial", "boundary", "dcm"])
def test_public_suggestion_project_and_report_recheck_actual_light_load_ratio(branch, tmp_path):
    from power_stage_design.project import read_project, save_project
    from power_stage_design.report import write_report
    from power_stage_design.suggestions import (
        SuggestionTargets,
        suggest_parameters,
        validate_suggestion_record,
    )

    actual, duty, ripple, magnetizing, _, _ = _independent_fixture(branch)
    source = replace(actual, inductance_h=100e-6, turns_ratio_np_ns=1.5,
                     magnetizing_inductance_h=.003)
    targets = SuggestionTargets(ripple, duty, magnetizing_ripple_pp_a=magnetizing,
                                size_turns_ratio=True)
    proposal = suggest_parameters(source, targets)
    assert proposal.inputs.inductance_h == pytest.approx(actual.inductance_h, rel=2e-9)
    assert proposal.inputs.turns_ratio_np_ns == pytest.approx(actual.turns_ratio_np_ns, rel=2e-9)
    assert proposal.inputs.magnetizing_inductance_h == pytest.approx(actual.magnetizing_inductance_h, rel=2e-9)
    assert proposal.changed_fields == ("inductance_h", "turns_ratio_np_ns", "magnetizing_inductance_h")
    assert proposal.result.duty == pytest.approx(duty, rel=2e-9)
    assert proposal.inputs.turns_ratio_np_ns != pytest.approx(actual.vin_v*duty/(actual.vout_v+actual.diode_drop_v))
    record = proposal.record()
    validate_suggestion_record(record, proposal.result)
    project = tmp_path/"light-load-bridge.json"
    save_project(proposal.inputs, project, parameter_suggestion=record)
    restored, document = read_project(project)
    assert restored == proposal.inputs
    fresh = calculate_stage(restored)
    assert fresh.design_id == proposal.result.design_id
    assert fresh.duty == pytest.approx(duty, rel=2e-9)
    report = tmp_path/"light-load-bridge.html"
    write_report(fresh, report, parameter_suggestion=document["parameter_suggestion"])
    text = report.read_text(encoding="utf-8")
    assert proposal.source_design_id in text and fresh.design_id in text
    tampered = copy.deepcopy(record)
    tampered["targets"]["max_duty"] *= 1.01
    with pytest.raises(ValueError, match="actual duty target"):
        validate_suggestion_record(tampered, fresh)


def test_max_duty_is_upper_bound_without_ratio_and_equality_with_ratio():
    p, duty, ripple, _, _, _ = _independent_fixture("partial")
    upper = duty*1.05
    fixed = size_full_bridge(p, ripple, max_duty=upper)
    assert fixed.duty < upper
    sized = size_full_bridge(p, ripple, max_duty=upper, size_turns_ratio=True)
    assert sized.duty == pytest.approx(upper, rel=1e-10)
    assert sized.inputs.turns_ratio_np_ns != p.turns_ratio_np_ns
    with pytest.raises(ModelDomainError, match="exceeds maximum"):
        size_full_bridge(p, ripple, max_duty=duty*.9)


@pytest.mark.parametrize("branch", ["ordinary", "partial", "boundary", "dcm"])
def test_each_branch_is_one_production_call_and_cancellation_is_bounded(monkeypatch, branch):
    p, _, ripple, _, _, _ = _independent_fixture(branch)
    calls = []
    real = bridge_sizing.calculate_stage
    def observed(*args, **kwargs):
        calls.append(args[0])
        return real(*args, **kwargs)
    monkeypatch.setattr(bridge_sizing, "calculate_stage", observed)
    size_full_bridge(p, ripple, max_duty=.8)
    assert len(calls) == 1
    with pytest.raises(InterruptedError):
        size_full_bridge(p, ripple, max_duty=.8, cancelled=lambda: True)
    assert len(calls) == 1
    checks = 0
    def cancel_during_final_check():
        nonlocal checks
        checks += 1
        return checks >= 4
    with pytest.raises(InterruptedError):
        size_full_bridge(p, ripple, max_duty=.8, cancelled=cancel_during_final_check)
    assert len(calls) == 2


@pytest.mark.parametrize("offset,mode", [(-1e-6, "DCM"), (0., "BCM"), (1e-6, "CCM")])
def test_actual_production_boundary_brackets_charge_without_tolerance_overlap(offset, mode):
    p, _, _, _, _, _ = _independent_fixture("boundary")
    result = calculate_stage(replace(p, iout_a=p.iout_a*(1+offset)))
    assert result.mode == mode
    assert result.stress["Cout"]["current_mean_a"] == pytest.approx(0., abs=1e-11)
    assert result.metrics["input_power_w"] == pytest.approx(p.vin_v*result.metrics["input_current_mean_a"])
    assert result.metrics["input_power_w"] == pytest.approx((p.vout_v+p.diode_drop_v)*result.inputs.iout_a, rel=1e-10)


def test_roundoff_below_bcm_enters_valid_zero_current_branch_instead_of_invalid_root():
    p, duty, ripple, magnetizing, _, _ = _independent_fixture("boundary")
    # The old 1e-12 overlap selected a Brent bracket whose lower endpoint
    # already supplied MORE than the load. This physical point is infinitesimally
    # below BCM and must still have a valid periodic result.
    p = replace(p, iout_a=p.iout_a*(1-5e-13))
    result = calculate_stage(p)
    assert result.mode == "BCM"
    assert result.metrics["idle_fraction"] >= 0
    assert result.stress["Cout"]["current_mean_a"] == pytest.approx(0., abs=1e-12)
    suggested = size_full_bridge(p, ripple, max_duty=duty, magnetizing_ripple_pp_a=magnetizing,
                                 size_turns_ratio=True)
    assert suggested.duty == pytest.approx(duty, rel=1e-10)


def test_small_lm_boundary_keeps_the_unsupported_commutation_domain_closed():
    p, _, _, _, _, _ = _independent_fixture("boundary")
    p = replace(p, magnetizing_inductance_h=.001)
    # Here diL/dt_on < n*diLm/dt_on; a zero-current start cannot support
    # the assumed open-primary freewheel without a different commutation model.
    assert (p.vin_v/p.turns_ratio_np_ns-p.vout_v)/p.inductance_h < p.turns_ratio_np_ns*p.vin_v/p.magnetizing_inductance_h
    with pytest.raises(ModelDomainError, match="commutation capacitances"):
        calculate_stage(p)
    valid = calculate_stage(replace(p, iout_a=.04))
    recovered = size_full_bridge(valid.inputs, valid.metrics["inductor_ripple_pp_a"], max_duty=.8)
    assert recovered.inputs.inductance_h == pytest.approx(p.inductance_h, rel=1e-10)


@pytest.mark.parametrize("ratio", [1., 2., 3.5, 4.])
@pytest.mark.parametrize("vf", [0., .4])
def test_zero_freewheel_endpoint_is_physical_with_independent_charge_and_energy(ratio, vf):
    p = DesignInputs("T17", 48, 5, .001, 100e3, 470e-6, capacitance_f=1e-3,
                     turns_ratio_np_ns=ratio, magnetizing_inductance_h=.01, diode_drop_v=vf)
    result = size_full_bridge(p, .02, max_duty=.8, magnetizing_ripple_pp_a=.04/ratio)
    assert result.mode == "DCM"
    final, _, samples = _half_cycle(result.inputs, result.duty, 0., 0., skip_freewheel=True)
    assert final[:2] == pytest.approx([0., 0.], abs=1e-12)
    assert final[2] == pytest.approx(p.iout_a, rel=1e-10)
    assert final[3] == pytest.approx((p.vout_v+vf)*p.iout_a, rel=1e-10)
    assert np.ptp(samples[0]) == pytest.approx(.02, rel=1e-10)
    assert 2*np.max(samples[1]) == pytest.approx(.04/ratio, rel=1e-10)
    # A true negative freewheel interval stays outside the model, even when
    # it is tiny: 1e-10 relative is much larger than the eight-ulp endpoint.
    with pytest.raises(ModelDomainError, match="commutation capacitances"):
        calculate_stage(replace(result.inputs, magnetizing_inductance_h=result.inputs.magnetizing_inductance_h*(1-1e-10)))
    above = calculate_stage(replace(result.inputs, magnetizing_inductance_h=result.inputs.magnetizing_inductance_h*(1+1e-10)))
    assert above.mode == "DCM"
    assert above.stress["Cout"]["current_mean_a"] == pytest.approx(0., abs=1e-12)


@pytest.mark.parametrize("durations", [(.5, 2**-56, .5), (.5, .5, 2**-56)])
def test_unrepresentable_boundary_interval_has_no_empty_segment(durations):
    from power_stage_design.waveforms import piecewise
    # Positive time can be below the ulp at an existing event boundary. The
    # former constructor built [0.5,0.5] or [1,1] and rejected the entire cycle.
    values = ((1., 1.), (7., 7.), (3., 3.)) if durations[1] < .5 else ((1., 1.), (3., 3.), (7., 7.))
    wave = piecewise(1., durations, values, "A", "test current")
    assert len(wave.segments) == 2
    assert wave.segments[0].start_fraction == 0.
    assert wave.segments[-1].end_fraction == 1.
    assert all(s.end_fraction > s.start_fraction for s in wave.segments)
    stats = wave.statistics()
    expected_charge = math.fsum(duration*(first+last)/2 for duration, (first, last) in zip(durations, values))
    expected_energy = math.fsum(duration*first**2 for duration, (first, _) in zip(durations, values))
    assert stats["mean"] == pytest.approx(expected_charge, rel=2e-15)
    assert stats["rms"]**2 == pytest.approx(expected_energy, rel=2e-15)


@pytest.mark.parametrize("tiny", [1e-30, 2**-52])
def test_representable_tiny_nonzero_segments_are_retained_without_duration_epsilon(tiny):
    from power_stage_design.waveforms import piecewise
    wave = piecewise(1., (tiny, 1-tiny), ((7., 7.), (1., 1.)), "A", "test current")
    assert len(wave.segments) == 2
    assert wave.segments[0].end_fraction == tiny
    assert wave.segments[0].duration_fraction > 0
    assert wave.statistics()["max"] == 7.
    assert wave.statistics()["mean"] == pytest.approx(1+6*tiny, rel=2e-15)
    assert wave.statistics()["rms"]**2 == pytest.approx(1+48*tiny, rel=2e-15)


@pytest.mark.parametrize("target", [.1, 1.])
def test_unsupported_commutation_submode_reports_specific_cause(target):
    p = DesignInputs("T17", 48, 5, .005, 100e3, 470e-6,
                     turns_ratio_np_ns=2, magnetizing_inductance_h=.01)
    with pytest.raises(ModelDomainError, match="commutation-capacitance"):
        size_full_bridge(p, .02, max_duty=.8, magnetizing_ripple_pp_a=target)


@pytest.mark.parametrize("magnetic_target", [None, .1])
def test_unreachable_ratio_does_not_return_conventional_ccm_value(magnetic_target):
    p = DesignInputs("T17", 48, 5, .001, 100e3, 470e-6,
                     turns_ratio_np_ns=2, magnetizing_inductance_h=.001)
    with pytest.raises(ModelDomainError, match="No Full Bridge ratio solution"):
        size_full_bridge(p, .02, max_duty=.8, magnetizing_ripple_pp_a=magnetic_target, size_turns_ratio=True)


def test_mode_request_and_unmodeled_deadtime_are_preserved():
    p, _, ripple, _, _, _ = _independent_fixture("dcm")
    with pytest.raises(ModelDomainError, match="Requested CCM"):
        size_full_bridge(replace(p, mode="CCM"), ripple, max_duty=.8)
    with pytest.raises(ModelDomainError, match="dead time"):
        size_full_bridge(replace(p, dead_time_s=100e-9), ripple, max_duty=.8)


@pytest.mark.parametrize("kwargs", [
    {"ripple_pp_a": 0.}, {"ripple_pp_a": math.nan}, {"max_duty": 1.},
    {"max_duty": True}, {"magnetizing_ripple_pp_a": -1.}, {"size_turns_ratio": 1},
])
def test_invalid_targets_rejected(kwargs):
    p, _, _, _, _, _ = _independent_fixture("ordinary")
    arguments = {"ripple_pp_a": .02, "max_duty": .8, **kwargs}
    with pytest.raises(ModelDomainError):
        size_full_bridge(p, **arguments)

"""Independent MNA/IVP targets for T18 Lout/Lm/ratio inverse sizing.

The finite-Coss joint inverse costs minutes because every trial re-solves its
physical periodic circuit and reconstructs actual extrema. Run it explicitly:
PSD_RUN_SLOW_PSFB_SIZING=1 pytest -s power_stage_design/tests/test_psfb_magnetizing_sizing.py
All ideal inverse and finite-Coss domain tests run by default.
"""
import os
import time
from dataclasses import fields, replace

import numpy as np
import pytest
from scipy.integrate import solve_ivp
from scipy.optimize import brentq, root

from power_stage_design.models import DesignInputs, ModelDomainError
from power_stage_design.psfb_sizing import size_phase_shifted_bridge


def example(load=5.):
    return DesignInputs("T18", 400., 48., load, 100e3, 100e-6,
                        capacitance_f=1e-3, turns_ratio_np_ns=4., diode_drop_v=.7,
                        magnetizing_inductance_h=1e-3, leakage_inductance_h=20e-6)


def _slopes(p, bridge):
    """Solve constitutive laws + differentiated transformer KCL, independently.

    Unknowns: dIp/dt, dIm/dt, dIo/dt, Vp. No forward-model slopes or
    inverse-sizing expressions are consumed by this reference observer.
    """
    n = p.turns_ratio_np_ns
    matrix = [[p.leakage_inductance_h, 0., 0., 1.],
              [0., p.magnetizing_inductance_h, 0., -1.],
              [0., 0., p.inductance_h, -1/n], [1., -1., -1/n, 0.]]
    return np.linalg.solve(matrix, [bridge, 0., -p.vout_v-p.diode_drop_v, 0.])[:3]


def _advance(state, duration, slope):
    if duration <= 0:
        return np.asarray(state)
    result = solve_ivp(lambda t, y: [*slope, y[2]], (0., duration), state,
                       rtol=1e-11, atol=1e-12)
    assert result.success
    return result.y[:, -1]


def reference(p, mode):
    half = .5/p.fsw_hz
    on, off = _slopes(p, p.vin_v), _slopes(p, 0.)
    if mode in {"DCM", "BCM"}:
        def cycle(active):
            peak = _advance([0., 0., 0., 0.], active, on)
            def zero_current(t, y):
                return y[2]
            zero_current.terminal, zero_current.direction = True, -1
            decay = solve_ivp(lambda t, y: [*off, y[2]], (0., 10*half), peak,
                              events=zero_current, rtol=1e-11, atol=1e-12)
            assert len(decay.t_events[0]) == 1
            return peak, decay.y[:, -1], decay.t[-1]
        boundary = brentq(lambda active: active+cycle(active)[2]-half,
                          half*1e-6, half, xtol=1e-18)
        if mode == "BCM":
            active = boundary
        else:
            active = brentq(lambda active: cycle(active)[1][3]/half-p.iout_a,
                            half*1e-6, boundary, xtol=1e-18)
        peak, end, _decay = cycle(active)
        return peak[2], end[1], active/half, end[3]/half

    def cycle(start, duty):
        initial = [-start/p.turns_ratio_np_ns, 0., start, 0.]
        def transferred(t, y):
            return y[0]-y[1]-y[2]/p.turns_ratio_np_ns
        transferred.terminal, transferred.direction = True, 1
        overlap = solve_ivp(lambda t, y: [p.vin_v/p.leakage_inductance_h, 0.,
            -(p.vout_v+p.diode_drop_v)/p.inductance_h, y[2]],
            (0., duty*half), initial, events=transferred, rtol=1e-11, atol=1e-12)
        assert len(overlap.t_events[0]) == 1
        minimum = overlap.y[:, -1]
        peak = _advance(minimum, duty*half-overlap.t[-1], on)
        end = _advance(peak, (1-duty)*half, off)
        return end, peak[2]-minimum[2]
    solved = root(lambda x: [cycle(*x)[0][2]-x[0],
                             cycle(*x)[0][3]/half-p.iout_a], [p.iout_a, .55], tol=1e-10)
    assert solved.success
    end, ripple = cycle(*solved.x)
    assert end[3]/half == pytest.approx(p.iout_a, rel=1e-9)
    return ripple, end[1], solved.x[1], end[3]/half


@pytest.mark.parametrize("mode", ["CCM", "BCM", "DCM"])
@pytest.mark.parametrize("size_ratio", [False, True])
@pytest.mark.parametrize("target_lm", [False, True])
def test_targets_from_independent_event_integrator_recover_components(mode, size_ratio, target_lm):
    expected = example(.1 if mode == "DCM" else 5.)
    ripple, magnetic, duty, load = reference(expected, mode)
    expected = replace(expected, iout_a=load)
    source = replace(expected, inductance_h=170e-6,
                     turns_ratio_np_ns=3.5 if size_ratio else expected.turns_ratio_np_ns,
                     magnetizing_inductance_h=2e-3 if target_lm else expected.magnetizing_inductance_h)
    result = size_phase_shifted_bridge(source, ripple, max_duty=duty if size_ratio else .95,
        magnetizing_ripple_pp_a=magnetic if target_lm else None, size_turns_ratio=size_ratio)
    assert result.mode == mode
    assert result.inputs.inductance_h == pytest.approx(expected.inductance_h, rel=5e-6)
    assert result.inputs.magnetizing_inductance_h == pytest.approx(expected.magnetizing_inductance_h, rel=5e-6)
    assert result.inputs.turns_ratio_np_ns == pytest.approx(expected.turns_ratio_np_ns, rel=5e-6)
    assert result.stress["L1"]["current_pp_a"] == pytest.approx(ripple, rel=2e-6)
    assert result.stress["Lm"]["current_pp_a"] == pytest.approx(magnetic, rel=2e-6)
    assert result.duty == pytest.approx(duty, rel=2e-6)
    allowed = {"inductance_h"}
    if target_lm:
        allowed.add("magnetizing_inductance_h")
    if size_ratio:
        allowed.add("turns_ratio_np_ns")
    for field in fields(source):
        if field.name not in allowed:
            assert getattr(result.inputs, field.name) == getattr(source, field.name)


@pytest.mark.parametrize("current_scale", [1e-3, 1., 1e3])
@pytest.mark.parametrize("target_lm", [False, True])
@pytest.mark.parametrize("ripple_offset,mode", [
    (-1e-6, "CCM"), (-1e-12, "BCM"), (0., "BCM"), (1e-12, "BCM"), (1e-6, "DCM"),
])
def test_boundary_target_roundoff_keeps_physical_mode_across_current_scales(current_scale, target_lm, ripple_offset, mode):
    p = example()
    p = replace(p, iout_a=p.iout_a*current_scale, inductance_h=p.inductance_h/current_scale,
                magnetizing_inductance_h=p.magnetizing_inductance_h/current_scale,
                leakage_inductance_h=p.leakage_inductance_h/current_scale)
    ripple, magnetic, duty, load = reference(p, "BCM")
    p = replace(p, iout_a=load)
    source = replace(p, inductance_h=1.7*p.inductance_h, turns_ratio_np_ns=3.5,
                     magnetizing_inductance_h=2*p.magnetizing_inductance_h if target_lm else p.magnetizing_inductance_h)
    result = size_phase_shifted_bridge(source, ripple*(1+ripple_offset), max_duty=duty,
        magnetizing_ripple_pp_a=magnetic if target_lm else None, size_turns_ratio=True)
    assert result.mode == mode
    assert result.stress["L1"]["current_pp_a"] == pytest.approx(ripple*(1+ripple_offset), rel=2e-6)
    assert result.duty == pytest.approx(duty, rel=2e-6)
    assert abs(result.diagnostics["output_capacitor_mean_current_a"])/load < 1e-10
    assert abs(result.diagnostics["modeled_power_balance_w"])/(p.vout_v*load) < 1e-10
    if mode == "BCM":
        assert result.inputs.inductance_h == pytest.approx(p.inductance_h, rel=1e-10)
        assert result.inputs.turns_ratio_np_ns == pytest.approx(p.turns_ratio_np_ns, rel=1e-10)
        assert result.inputs.magnetizing_inductance_h == pytest.approx(p.magnetizing_inductance_h, rel=1e-10)
        assert result.stress["L1"]["current_min_a"] == 0.
        assert result.metrics["idle_fraction"] == 0.
    elif mode == "DCM":
        assert result.stress["L1"]["current_min_a"] == 0.
        assert result.metrics["idle_fraction"] > 0.
    else:
        assert result.stress["L1"]["current_min_a"] > 0.
        assert result.metrics["commutation_time_s"] > 0.


@pytest.mark.parametrize("mode", ["CCM", "BCM", "DCM"])
def test_missing_lm_can_be_sized_from_an_explicit_absolute_target(mode):
    p = example(.1 if mode == "DCM" else 5.)
    ripple, magnetic, _duty, load = reference(p, mode)
    result = size_phase_shifted_bridge(replace(p, iout_a=load, magnetizing_inductance_h=None),
                                       ripple, max_duty=.95, magnetizing_ripple_pp_a=magnetic)
    assert result.inputs.magnetizing_inductance_h == pytest.approx(p.magnetizing_inductance_h, rel=2e-6)
    assert result.mode == mode


def test_maximum_duty_is_bound_unless_turns_ratio_is_targeted():
    p = example()
    ripple, magnetic, duty, _ = reference(p, "CCM")
    fixed = size_phase_shifted_bridge(p, ripple, max_duty=.95, magnetizing_ripple_pp_a=magnetic)
    assert fixed.duty < .95
    assert fixed.inputs.turns_ratio_np_ns == p.turns_ratio_np_ns
    with pytest.raises(ModelDomainError, match="Achieved duty.*exceeds maximum"):
        size_phase_shifted_bridge(p, ripple, max_duty=.9*duty, magnetizing_ripple_pp_a=magnetic)
    sized = size_phase_shifted_bridge(p, ripple, max_duty=.9*duty,
                                      magnetizing_ripple_pp_a=magnetic, size_turns_ratio=True)
    assert sized.duty == pytest.approx(.9*duty, rel=2e-6)


def test_dcm_magnetic_target_does_not_assume_full_half_cycle_volt_seconds():
    p = example(.1)
    ripple, magnetic, _, _ = reference(p, "DCM")
    result = size_phase_shifted_bridge(p, ripple, max_duty=.95, magnetizing_ripple_pp_a=magnetic)
    naive_ccm = p.turns_ratio_np_ns*(p.vout_v+p.diode_drop_v)/(2*p.fsw_hz*magnetic)
    assert result.inputs.magnetizing_inductance_h < naive_ccm/2
    assert abs(result.diagnostics["modeled_power_balance_w"]) < 1e-8
    assert abs(result.diagnostics["output_capacitor_mean_current_a"]) < 1e-9


@pytest.mark.parametrize("changes,kwargs,match", [
    ({"topology":"T17"}, {}, "only to T18"),
    ({"leakage_inductance_h":None}, {}, "explicit leakage"),
    ({"magnetizing_inductance_h":None}, {}, "Specify Lm"),
    ({"iout_a":0.}, {}, "positive load"),
    ({}, {"ripple_pp_a":True}, "finite and positive"),
    ({}, {"magnetizing_ripple_pp_a":float("nan")}, "finite and positive"),
    ({}, {"size_turns_ratio":1}, "boolean"),
    ({"mode":"DCM"}, {"ripple_pp_a":1.}, "mode"),
    ({"iout_a":.1}, {"ripple_pp_a":.5,"max_duty":.01,
       "magnetizing_ripple_pp_a":2.,"size_turns_ratio":True}, "leakage volt-seconds"),
    ({"iout_a":.1}, {"ripple_pp_a":1e5}, "fixed leakage"),
])
def test_invalid_unreachable_or_wrong_mode_is_not_a_proposal(changes, kwargs, match):
    arguments = {"ripple_pp_a":1.5,"max_duty":.9, **kwargs}
    with pytest.raises(ModelDomainError, match=match):
        size_phase_shifted_bridge(replace(example(), **changes), **arguments)


def test_cancel_before_and_during_sizing():
    with pytest.raises(InterruptedError):
        size_phase_shifted_bridge(example(), 1.5, max_duty=.9, cancelled=lambda:True)
    calls = 0
    def cancelled():
        nonlocal calls
        calls += 1
        return calls >= 8
    with pytest.raises(InterruptedError):
        size_phase_shifted_bridge(example(), 1.5, max_duty=.45,
                                  magnetizing_ripple_pp_a=.9, size_turns_ratio=True,
                                  cancelled=cancelled)
    assert calls >= 8


def test_actual_evaluation_budget_is_bounded(monkeypatch):
    import power_stage_design.psfb_sizing as sizing
    original = sizing.calculate_stage
    calls = 0
    def counted(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)
    monkeypatch.setattr(sizing, "_MAX_EVALUATIONS", 2)
    monkeypatch.setattr(sizing, "calculate_stage", counted)
    with pytest.raises(ModelDomainError, match="budget"):
        size_phase_shifted_bridge(example(), 1.5, max_duty=.45,
                                  magnetizing_ripple_pp_a=.9, size_turns_ratio=True)
    assert calls == 2


def _dynamic_example():
    from power_stage_design.psfb_dynamics import PSFBOptions
    options = PSFBOptions(300e-12, .15, .8, .05, 100e-12, 1e-9, .02,
                          "Explicit illustrative circuit values; not measured device data")
    return replace(example(), dead_time_s=200e-9, psfb_options=options)


@pytest.mark.parametrize("changes,duty,match", [
    ({"mode":"DCM"}, .5, "positive-current CCM"),
    ({"mode":"BCM"}, .5, "positive-current CCM"),
    ({}, .01, "dead-time windows"),
    ({}, .99, "dead-time windows"),
])
def test_finite_coss_explicit_mode_and_phase_constraints(changes, duty, match):
    with pytest.raises(ModelDomainError, match=match):
        size_phase_shifted_bridge(replace(_dynamic_example(), **changes), 1.2,
            max_duty=duty, magnetizing_ripple_pp_a=.9, size_turns_ratio=True)


def _independent_dynamic_extrema(result):
    """Different stiff integrator + independent branch equations and events."""
    p, o = result.inputs, result.inputs.psfb_options
    first = lambda key, field: getattr(result.waveforms[key], field).segments[0].evaluate(0.)
    initial = np.array([first("node_A", "voltage_v"), first("node_B", "voltage_v"),
        first("Lleak", "current_a"), first("Lm", "current_a"),
        first("Cprimary", "voltage_v"), first("Crectifier", "voltage_v"),
        first("L1", "current_a"), 0.])
    half = .5/p.fsw_hz
    edges = [0., p.dead_time_s, result.duty*half, result.duty*half+p.dead_time_s, half]
    state, points = initial.copy(), [initial.copy()]
    for left, right, gates in zip(edges, edges[1:], [(0,0,0,1),(1,0,0,1),(1,0,0,0),(1,0,1,0)]):
        def rhs(t, y, gates=gates):
            a, b, ip, im, vp, vr, il, _ = y
            vds = np.array([p.vin_v-a, a, p.vin_v-b, b])
            channel = np.asarray(gates)*vds/o.r_on_ohm
            body = np.maximum(-vds-o.body_diode_drop_v, 0.)/o.body_diode_resistance_ohm
            d1 = max(vp/p.turns_ratio_np_ns-vr-p.diode_drop_v, 0.)/o.rectifier_resistance_ohm
            d2 = max(-vp/p.turns_ratio_np_ns-vr-p.diode_drop_v, 0.)/o.rectifier_resistance_ohm
            return [(channel[0]-body[0]-channel[1]+body[1]-ip)/(2*o.coss_f),
                (channel[2]-body[2]-channel[3]+body[3]+ip)/(2*o.coss_f),
                (a-b-vp)/p.leakage_inductance_h, vp/p.magnetizing_inductance_h,
                (ip-im-(d1-d2)/p.turns_ratio_np_ns)/o.transformer_capacitance_f,
                (d1+d2-il)/o.rectifier_capacitance_f, (vr-p.vout_v)/p.inductance_h, il]
        solved = solve_ivp(rhs, (left, right), state, method="Radau", rtol=2e-10, atol=1e-11,
            max_step=half/100, events=(lambda t,y:rhs(t,y)[3], lambda t,y:rhs(t,y)[6]))
        assert solved.success
        state = solved.y[:, -1]
        points.append(state.copy())
        points.extend(solved.y_events[0])
        points.extend(solved.y_events[1])
    expected = initial[:7]*np.array([-1,-1,-1,-1,-1,1,1])+np.array([p.vin_v,p.vin_v,0,0,0,0,0])
    assert state[:7] == pytest.approx(expected, abs=3e-5)
    assert state[7]/half == pytest.approx(p.iout_a, abs=3e-6)
    observed = np.asarray(points)
    return np.ptp(observed[:,6]), 2*np.max(np.abs(observed[:,3]))


@pytest.mark.skipif(os.environ.get("PSD_RUN_SLOW_PSFB_SIZING") != "1",
                    reason="Opt-in finite-Coss joint sizing: measured 548 s; set PSD_RUN_SLOW_PSFB_SIZING=1")
def test_finite_coss_joint_lout_lm_ratio_uses_actual_extrema_and_retains_options(monkeypatch):
    import power_stage_design.psfb_sizing as sizing
    from power_stage_design.topologies import calculate_stage
    expected = _dynamic_example()
    started = time.monotonic()
    print("PSFB finite-Coss reference started", flush=True)
    baseline = calculate_stage(expected)
    print(f"PSFB reference finished in {time.monotonic()-started:.1f} s", flush=True)
    ripple = baseline.stress["L1"]["current_pp_a"]
    magnetic = baseline.stress["Lm"]["current_pp_a"]
    duty = baseline.duty
    del baseline
    calls = 0
    def monitored(*args, **kwargs):
        nonlocal calls
        calls += 1
        trial_start = time.monotonic()
        print(f"PSFB inverse evaluation {calls}/{sizing._MAX_EVALUATIONS} started", flush=True)
        result = calculate_stage(*args, **kwargs)
        print(f"PSFB evaluation {calls} finished in {time.monotonic()-trial_start:.1f} s", flush=True)
        return result
    monkeypatch.setattr(sizing, "calculate_stage", monitored)
    source = replace(expected, inductance_h=125e-6, magnetizing_inductance_h=1.2e-3,
                     turns_ratio_np_ns=3.8)
    result = size_phase_shifted_bridge(source, ripple, max_duty=duty,
                                       magnetizing_ripple_pp_a=magnetic, size_turns_ratio=True)
    assert calls <= sizing._MAX_EVALUATIONS
    assert result.inputs.psfb_options is source.psfb_options
    assert result.inputs.dead_time_s == source.dead_time_s
    assert result.inputs.leakage_inductance_h == source.leakage_inductance_h
    assert result.inputs.capacitance_f == source.capacitance_f
    assert result.inputs.inductance_h == pytest.approx(expected.inductance_h, rel=1e-5)
    assert result.inputs.magnetizing_inductance_h == pytest.approx(expected.magnetizing_inductance_h, rel=1e-5)
    assert result.inputs.turns_ratio_np_ns == pytest.approx(expected.turns_ratio_np_ns, rel=1e-5)
    assert result.mode == "CCM"
    assert result.provenance["model_revision"] == source.psfb_options.model_revision
    observed_ripple, observed_magnetic = _independent_dynamic_extrema(result)
    assert observed_ripple == pytest.approx(ripple, rel=1e-5)
    assert observed_magnetic == pytest.approx(magnetic, rel=1e-5)


@pytest.mark.parametrize("component", ["L1", "Lm"])
def test_acceptance_requires_actual_current_extrema_not_only_inverse_equations(monkeypatch, component):
    import power_stage_design.psfb_sizing as sizing
    original = sizing.calculate_stage
    def inconsistent(*args, **kwargs):
        result = original(*args, **kwargs)
        stress = {key:dict(value) for key,value in result.stress.items()}
        stress[component]["current_pp_a"] *= 1.1
        return replace(result, stress=stress)
    monkeypatch.setattr(sizing, "calculate_stage", inconsistent)
    p = example(.1)
    ripple, magnetic, _, _ = reference(p, "DCM")
    with pytest.raises(ModelDomainError, match="Actual PSFB.*extrema"):
        size_phase_shifted_bridge(p, ripple, max_duty=.9, magnetizing_ripple_pp_a=magnetic)

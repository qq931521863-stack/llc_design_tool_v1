"""Actual-model inverse sizing in first-clamp QR and diode-secondary PSFB DCM."""
import math
from dataclasses import replace

import numpy as np
import pytest
from scipy.integrate import solve_ivp
from scipy.optimize import brentq

from power_stage_design.models import DesignInputs, ModelDomainError
from power_stage_design.project import read_project, save_project
from power_stage_design.suggestions import SuggestionTargets, suggest_parameters


def _qr_reference(p):
    """IVP/event observer; does not call the production power-stage model."""
    lm,cn,n = p.inductance_h,p.resonant_capacitance_f,p.turns_ratio_np_ns
    vr = n*(p.vout_v+p.diode_drop_v)
    half_ring = math.pi*math.sqrt(lm*cn)
    rhs = lambda t,y:[(p.vin_v-y[1])/lm,y[0]/cn]
    def zero(t,y):
        return y[1]
    zero.direction = -1
    zero.terminal = True
    def current_extremum(t,y):
        return y[1]-p.vin_v
    current_extremum.direction = 0
    current_extremum.terminal = False
    ring = solve_ivp(rhs,(0,half_ring),[0.,p.vin_v+vr],events=(zero,current_extremum),
                     rtol=1e-11,atol=1e-12,max_step=half_ring/120)
    i0 = ring.y[0,-1]
    ring_time = ring.t[-1]
    minimum = ring.y_events[1][0,0]
    def secondary_on(t,y):
        return y[1]-p.vin_v-vr
    secondary_on.direction = 1
    secondary_on.terminal = True
    def cycle(peak):
        charge = solve_ivp(rhs,(0,half_ring),[peak,0.],events=(secondary_on,current_extremum),
                           rtol=1e-11,atol=1e-12,max_step=half_ring/120)
        assert len(charge.t_events[0]) == 1
        transfer = charge.y[0,-1]
        on_time = lm*(peak-i0)/p.vin_v
        period = on_time+charge.t[-1]+lm*transfer/vr+ring_time
        return .5*lm*transfer**2/period,period,on_time,charge.y_events[1][0,0]
    target = (p.vout_v+p.diode_drop_v)*p.iout_a
    # Stay away from the grazing zero-energy secondary-clamp event; these
    # positive-power fixtures have a root well above this verified bracket.
    lower = abs(i0)+1e-3
    assert cycle(lower)[0] < target
    upper = max(1.,2*lower)
    while cycle(upper)[0] < target:
        upper *= 2
    peak = brentq(lambda ip:cycle(ip)[0]-target,lower,upper,xtol=1e-12)
    _,period,on_time,maximum = cycle(peak)
    return maximum-minimum,1/period,on_time/period


@pytest.mark.parametrize("reflected",[48.,60.,120.])
@pytest.mark.parametrize("vf",[0.,.7])
def test_qr_ratio_and_l_suggestions_recover_independent_first_clamp_target(reflected,vf,tmp_path):
    reference = DesignInputs("T11",48.,15.,1.,100e3,1e-3,capacitance_f=1e-3,
                             turns_ratio_np_ns=reflected/(15.+vf),diode_drop_v=vf,
                             resonant_capacitance_f=300e-12)
    ripple,frequency,duty = _qr_reference(reference)
    source = replace(reference,inductance_h=2e-3,turns_ratio_np_ns=2.)
    targets = SuggestionTargets(ripple,.95,size_turns_ratio=True,reflected_voltage_v=reflected)
    proposal = suggest_parameters(source,targets)
    assert proposal.inputs.turns_ratio_np_ns == pytest.approx(reference.turns_ratio_np_ns,rel=1e-12)
    assert proposal.inputs.inductance_h == pytest.approx(reference.inductance_h,rel=1e-6)
    assert proposal.result.duty == pytest.approx(duty,rel=1e-7)
    assert proposal.result.metrics["actual_switching_frequency_hz"] == pytest.approx(frequency,rel=1e-7)
    assert proposal.result.metrics["inductor_ripple_pp_a"] == pytest.approx(ripple,rel=2e-6)
    assert proposal.result.provenance["physics_revision"] == "qr-first-zero-clamp-1"
    assert any("first-zero-clamp inverse sizing" in a for a in proposal.assumptions)
    assert proposal.inputs.resonant_capacitance_f == source.resonant_capacitance_f
    assert set(proposal.changed_fields) == {"inductance_h","turns_ratio_np_ns"}
    path = tmp_path/"first-clamp-proposal.json"
    save_project(proposal.inputs,path,parameter_suggestion=proposal.record())
    restored,record = read_project(path)
    assert restored == proposal.inputs
    assert record["parameter_suggestion"] == proposal.record()


def test_qr_clamp_sizing_keeps_frequency_and_maximum_duty_rejections():
    reference = DesignInputs("T11",48.,15.,1.,100e3,1e-3,capacitance_f=1e-3,
                             turns_ratio_np_ns=4.,resonant_capacitance_f=300e-12)
    ripple,frequency,duty = _qr_reference(reference)
    target = SuggestionTargets(ripple,.95,size_turns_ratio=True,reflected_voltage_v=60.)
    with pytest.raises(ModelDomainError,match="first-zero-clamp frequency.*later clamped valleys"):
        suggest_parameters(replace(reference,fsw_hz=.95*frequency),target)
    with pytest.raises(ModelDomainError,match="Achieved duty.*exceeds"):
        suggest_parameters(reference,replace(target,max_duty=.95*duty))
    with pytest.raises(ModelDomainError,match="explicit reflected-voltage target"):
        suggest_parameters(reference,replace(target,reflected_voltage_v=None))


def _psfb_slopes(p):
    n = p.turns_ratio_np_ns
    matrix = np.array([[p.leakage_inductance_h,0.,0.,1.],
                       [0.,p.magnetizing_inductance_h,0.,-1.],
                       [0.,0.,p.inductance_h,-1/n],[1.,-1.,-1/n,0.]])
    return [np.linalg.solve(matrix,[ub,0.,-p.vout_v-p.diode_drop_v,0.])[2] for ub in (p.vin_v,0.)]


def _psfb_reference_target(p):
    up,down = _psfb_slopes(p)
    h = .5/p.fsw_hz
    def half_cycle(active):
        first = solve_ivp(lambda t,y:[up,y[0]],(0,active),[0.,0.],rtol=1e-11,atol=1e-12)
        peak,charge = first.y[:,-1]
        decay = -peak/down
        second = solve_ivp(lambda t,y:[down,y[0]],(0,decay),[peak,charge],rtol=1e-11,atol=1e-12)
        return second.y[1,-1]/h,peak,decay
    maximum_active = -down*h/(up-down)
    active = brentq(lambda t:half_cycle(t)[0]-p.iout_a,1e-15,maximum_active,xtol=1e-17)
    mean,peak,decay = half_cycle(active)
    assert mean == pytest.approx(p.iout_a,rel=1e-9)
    assert active+decay < h
    return peak,active/h


@pytest.mark.parametrize("starting_ratio",[3.,4.,5.])
def test_psfb_dcm_joint_inverse_has_two_independent_targets_and_unique_solution(starting_ratio):
    reference = DesignInputs("T18",400.,48.,.1,100e3,100e-6,capacitance_f=1e-3,
                             turns_ratio_np_ns=4.,diode_drop_v=.7,
                             magnetizing_inductance_h=1e-3,leakage_inductance_h=20e-6)
    ripple,duty = _psfb_reference_target(reference)
    targets = SuggestionTargets(ripple,duty,size_turns_ratio=True)
    source = replace(reference,turns_ratio_np_ns=starting_ratio,inductance_h=150e-6)
    proposal = suggest_parameters(source,targets)
    alpha = 1+reference.leakage_inductance_h/reference.magnetizing_inductance_h
    unique_ratio = duty*reference.vin_v*ripple/(2*reference.iout_a*alpha*(reference.vout_v+reference.diode_drop_v))
    assert unique_ratio == pytest.approx(reference.turns_ratio_np_ns,rel=1e-9)
    assert proposal.inputs.turns_ratio_np_ns == pytest.approx(unique_ratio,rel=1e-7)
    assert proposal.inputs.inductance_h == pytest.approx(reference.inductance_h,rel=1e-7)
    assert proposal.result.mode == "DCM"
    assert proposal.result.duty == pytest.approx(duty,rel=1e-7)
    assert proposal.result.metrics["inductor_ripple_pp_a"] == pytest.approx(ripple,rel=1e-7)
    assert proposal.inputs.magnetizing_inductance_h == source.magnetizing_inductance_h
    assert proposal.inputs.leakage_inductance_h == source.leakage_inductance_h


def test_psfb_dcm_inverse_rejects_incompatible_or_unreachable_targets():
    p = DesignInputs("T18",400.,48.,.1,100e3,100e-6,capacitance_f=1e-3,
                     turns_ratio_np_ns=4.,diode_drop_v=.7,mode="dcm",
                     magnetizing_inductance_h=1e-3,leakage_inductance_h=20e-6)
    # Any unidirectional DCM triangle has Ipeak>2*Iout. Do not silently
    # switch to CCM or invent a different load to meet this request.
    with pytest.raises(ModelDomainError):
        suggest_parameters(p,SuggestionTargets(.1,.5))
    # Ripple and duty together would require a ratio outside the positive
    # energizing-voltage domain; no plausible-looking proposal is acceptable.
    with pytest.raises(ModelDomainError):
        suggest_parameters(p,SuggestionTargets(.49,.9,size_turns_ratio=True))

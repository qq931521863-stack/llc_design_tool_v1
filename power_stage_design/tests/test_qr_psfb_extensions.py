"""Independent state/branch checks for explicitly bounded QR and PSFB extensions."""
import math
from dataclasses import replace

import numpy as np
import pytest
from scipy.integrate import quad, solve_ivp
from scipy.optimize import brentq

from power_stage_design.models import DesignInputs, ModelDomainError
from power_stage_design.topologies import calculate_stage


def _at(wave, phase):
    for segment in wave.segments:
        if segment.start_fraction <= phase < segment.end_fraction or phase == segment.end_fraction == 1:
            return float(segment.evaluate((phase-segment.start_fraction)/segment.duration_fraction))
    raise AssertionError(f"Uncovered phase {phase}")


def _branch(result, key, phase, voltage=False):
    device = result.waveforms[key]
    return _at(device.voltage_v if voltage else device.current_a, phase)


def _integral(wave, power=1):
    return sum(segment.duration_fraction*quad(lambda u,segment=segment:float(segment.evaluate(u))**power,0,1,
                                              epsabs=1e-11,epsrel=1e-11)[0]
               for segment in wave.segments)


def _qr(vin=48., vo=15., load=1., capacitance=300e-12):
    return DesignInputs("T11",vin,vo,load,1e6,1e-3,capacitance_f=1e-3,
                        turns_ratio_np_ns=4.,resonant_capacitance_f=capacitance)


@pytest.mark.parametrize("vin,vo,load,capacitance",[
    (48.,15.,1.,300e-12), (48.,12.,1.,300e-12),
    (48.,15.,.01,300e-12), (120.,150.,1.,1e-9),
    (48.,12.+1e-8,.2,100e-12),
])
def test_qr_first_zero_clamp_matches_independent_ivp_and_physical_branches(vin,vo,load,capacitance):
    p = _qr(vin,vo,load,capacitance)
    r = calculate_stage(p)
    lm,cn,n,vr = p.inductance_h,p.resonant_capacitance_f,p.turns_ratio_np_ns,p.turns_ratio_np_ns*p.vout_v
    period = r.waveforms["Lm"].current_a.period_s
    intervals = r.waveforms["Lm"].current_a.segments
    assert len(intervals) == 4
    assert r.provenance["physics_revision"] == "qr-first-zero-clamp-1"
    assert r.metrics["valley_index"] == 1
    assert r.metrics["turn_on_capacitive_energy_j"] == 0
    assert r.metrics["turn_on_capacitive_loss_w"] == 0
    assert r.metrics["actual_switching_frequency_hz"] == pytest.approx(1/period)
    assert r.metrics["negative_channel_time_s"] >= 0
    assert any("TI executable equivalence is UNKNOWN" in w for w in r.warnings)
    assert not any("Positive drain valley only" in a for a in r.assumptions)
    # [magnetizing current, drain voltage, source charge, secondary charge,
    #  channel-current square integral]; propagation does not use production
    # harmonic coefficients, circuit gain formulas or power-balance helper.
    initial = np.array([_branch(r,"Lm",0),0.,0.,0.,0.])
    state = initial.copy()
    for index,segment in enumerate(intervals):
        duration = segment.duration_fraction*period
        if index == 0:
            rhs = lambda t,y:[p.vin_v/lm,0.,y[0],0.,y[0]**2]
        elif index in (1,3):
            rhs = lambda t,y:[(p.vin_v-y[1])/lm,y[0]/cn,y[0],0.,0.]
        else:
            rhs = lambda t,y:[-vr/lm,0.,0.,n*y[0],0.]
        solution = solve_ivp(rhs,(0,duration),state,rtol=2e-11,atol=1e-12,
                             dense_output=True,max_step=duration/120)
        assert solution.success
        for fraction in np.linspace(.03,.97,19):
            expected = solution.sol(fraction*duration)
            phase = segment.start_fraction+fraction*segment.duration_fraction
            assert _branch(r,"Lm",phase) == pytest.approx(expected[0],rel=2e-8,abs=1e-9)
            assert _branch(r,"Q1",phase,True) == pytest.approx(expected[1],rel=2e-8,abs=1e-7)
            assert _branch(r,"primary",phase) == pytest.approx(0 if index==2 else expected[0],abs=1e-9)
            assert _branch(r,"Q1",phase) == pytest.approx(expected[0] if index==0 else 0,abs=1e-9)
            assert _branch(r,"D1",phase) == pytest.approx(n*expected[0] if index==2 else 0,abs=1e-9)
            assert _branch(r,"primary",phase,True) == pytest.approx(p.vin_v-expected[1],abs=1e-7)
        state = solution.y[:,-1]
        if index==1:
            assert state[1] == pytest.approx(p.vin_v+vr,abs=1e-7)
            assert state[0] > 0
        if index==2:
            assert state[0] == pytest.approx(0.,abs=1e-9)
    assert state[:2] == pytest.approx(initial[:2],abs=1e-7)
    assert state[2]*p.vin_v/period == pytest.approx(p.vout_v*p.iout_a,rel=1e-8,abs=1e-8)
    assert state[3]/period == pytest.approx(p.iout_a,rel=1e-8,abs=1e-9)
    assert math.sqrt(state[4]/period) == pytest.approx(r.stress["Q1"]["current_rms_a"],rel=1e-8)
    assert r.stress["Q1"]["current_min_a"] == pytest.approx(initial[0])
    assert r.stress["Q1"]["voltage_min_v"] >= -1e-10
    assert r.stress["Q1"]["voltage_max_v"] == pytest.approx(p.vin_v+vr)
    for device in r.waveforms.values():
        for wave in (device.current_a,device.voltage_v):
            assert wave.statistics()["rms"]**2 == pytest.approx(_integral(wave,2),rel=1e-10,abs=1e-9)
    assert _integral(r.waveforms["primary"].voltage_v) == pytest.approx(0.,abs=1e-9)


def test_qr_clamped_branch_accounts_for_secondary_diode_power():
    p = replace(_qr(),diode_drop_v=.7)
    r = calculate_stage(p)
    assert p.vin_v*_integral(r.waveforms["primary"].current_a) == pytest.approx((p.vout_v+p.diode_drop_v)*p.iout_a)
    assert r.metrics["modeled_diode_loss_w"] == pytest.approx(.7)


@pytest.mark.parametrize("vin",[48.,47.999,40.])
def test_qr_clamped_frequency_ceiling_uses_solved_period_and_rejects_later_valleys(vin):
    p = _qr(vin,12.)
    nominal = calculate_stage(p)
    fs = nominal.metrics["actual_switching_frequency_hz"]
    exact = calculate_stage(replace(p,fsw_hz=fs))
    assert exact.waveforms["Lm"].current_a.period_s == pytest.approx(1/fs,rel=1e-12)
    assert calculate_stage(replace(p,fsw_hz=fs*(1+1e-8))).duty == pytest.approx(exact.duty,rel=1e-12)
    with pytest.raises(ModelDomainError,match="first-zero-clamp frequency.*exceeds.*later clamped valleys"):
        calculate_stage(replace(p,fsw_hz=fs*(1-1e-8)))


def test_qr_equality_is_continuous_with_positive_first_valley():
    boundary = _qr(48.,12.)
    mid = calculate_stage(boundary)
    for delta in (-1e-7,1e-7):
        r = calculate_stage(replace(boundary,vin_v=48.+delta))
        assert r.metrics["valley_index"] == 1
        assert r.metrics["actual_switching_frequency_hz"] == pytest.approx(mid.metrics["actual_switching_frequency_hz"],rel=1e-7)
        for key in ("Q1","D1","primary","Lm","Cout"):
            for field in ("current_rms_a","current_max_a","voltage_max_v"):
                assert r.stress[key][field] == pytest.approx(mid.stress[key][field],rel=1e-6,abs=1e-7)
    assert mid.stress["Q1"]["current_min_a"] == 0
    assert mid.metrics["negative_channel_time_s"] == 0
    with pytest.raises(ModelDomainError,match="positive output power"):
        calculate_stage(replace(boundary,iout_a=0))


def _psfb(load=.1, leakage=20e-6, lm=1e-3, vf=.7):
    return DesignInputs("T18",400.,48.,load,100e3,100e-6,capacitance_f=1e-3,
                        turns_ratio_np_ns=4.,magnetizing_inductance_h=lm,
                        leakage_inductance_h=leakage,diode_drop_v=vf)


def _psfb_derivative(p, ub, polarity):
    # Simultaneously solve Llk dip/dt=ub-vp, Lm dim/dt=vp,
    # Lo diL/dt=s*vp/n-Vo-Vf, and ip-im=s*iL/n.
    n = p.turns_ratio_np_ns
    matrix = np.array([[p.leakage_inductance_h,0.,0.,1.],
                       [0.,p.magnetizing_inductance_h,0.,-1.],
                       [0.,0.,p.inductance_h,-polarity/n],
                       [1.,-1.,-polarity/n,0.]])
    return np.linalg.solve(matrix,[ub,0.,-p.vout_v-p.diode_drop_v,0.])


@pytest.mark.parametrize("leakage",[5e-6,20e-6,80e-6])
@pytest.mark.parametrize("lm",[.2e-3,1e-3])
@pytest.mark.parametrize("vf",[0.,.7])
@pytest.mark.parametrize("lo",[1e-6,100e-6])
def test_psfb_dcm_ode_branch_kcl_and_retained_idle_magnetizing_current(leakage,lm,vf,lo):
    p = replace(_psfb(leakage=leakage,lm=lm,vf=vf),inductance_h=lo)
    r = calculate_stage(p)
    assert r.mode == "DCM"
    assert r.provenance["physics_revision"] == "psfb-diode-dcm-1"
    assert r.metrics["idle_fraction"] > 0
    assert r.metrics["commutation_time_s"] == 0
    period = 1/p.fsw_hz
    state = np.array([_branch(r,"primary",0),_branch(r,"Lm",0),0.,0.,0.])
    initial = state.copy()
    idle_seen = 0
    for segment in r.waveforms["L1"].current_a.segments:
        phase = (segment.start_fraction+segment.end_fraction)/2
        il = _branch(r,"L1",phase)
        d1,d2 = (_branch(r,key,phase) for key in ("D1","D2"))
        ub = _branch(r,"Q2",phase,True)-_branch(r,"Q4",phase,True)
        polarity = 1 if d1>1e-10 else -1 if d2>1e-10 else 0
        if polarity:
            dip,dim,dil,vp = _psfb_derivative(p,ub,polarity)
        else:
            assert abs(il)<1e-10 and abs(ub)<1e-10
            assert state[0] == pytest.approx(state[1],abs=1e-10)
            assert abs(state[1]) > 1e-3
            assert _branch(r,"D1",phase,True) == pytest.approx(p.vout_v)
            assert _branch(r,"D2",phase,True) == pytest.approx(p.vout_v)
            assert _branch(r,"L1",phase,True) == 0
            dip=dim=dil=vp=0.
            idle_seen += 1
        duration = segment.duration_fraction*period
        rhs = lambda t,y,dip=dip,dim=dim,dil=dil,ub=ub:[dip,dim,dil,ub/p.vin_v*y[0],y[2]]
        solved = solve_ivp(rhs,(0,duration),state,dense_output=True,
                           rtol=1e-11,atol=1e-12,max_step=duration/30)
        for fraction in (.1,.5,.9):
            phase = segment.start_fraction+fraction*segment.duration_fraction
            expected = solved.sol(fraction*duration)
            ip,im,il = expected[:3]
            assert [_branch(r,key,phase) for key in ("primary","Lm","L1")] == pytest.approx(expected[:3],rel=1e-9,abs=1e-10)
            assert _branch(r,"primary",phase,True) == pytest.approx(vp,abs=1e-8)
            assert _branch(r,"Lleak",phase,True) == pytest.approx(ub-vp,abs=1e-8)
            assert _branch(r,"L1",phase,True) == pytest.approx(p.inductance_h*dil,abs=1e-8)
            q1,q2,q3,q4 = (_branch(r,key,phase) for key in ("Q1","Q2","Q3","Q4"))
            d1,d2 = (_branch(r,key,phase) for key in ("D1","D2"))
            assert q1-q2 == pytest.approx(ip,abs=1e-10)
            assert q4-q3 == pytest.approx(ip,abs=1e-10)
            assert q1+q3 == pytest.approx(ub/p.vin_v*ip,abs=1e-10)
            assert d1+d2 == pytest.approx(il,abs=1e-10)
            assert ip-im == pytest.approx((d1-d2)/p.turns_ratio_np_ns,abs=1e-10)
            vrect = p.vout_v+_branch(r,"L1",phase,True)
            for key,diode_current,voltage in (("D1",d1,vrect-vp/p.turns_ratio_np_ns),("D2",d2,vrect+vp/p.turns_ratio_np_ns)):
                assert diode_current >= -1e-10
                assert _branch(r,key,phase,True) == pytest.approx(voltage,abs=1e-8)
                assert voltage >= -p.diode_drop_v-1e-8
                if diode_current > 1e-8:
                    assert voltage == pytest.approx(-p.diode_drop_v,abs=1e-8)
        state = solved.y[:,-1]
    assert idle_seen == 2
    assert state[:3] == pytest.approx(initial[:3],abs=1e-9)
    assert state[3]*p.vin_v/period == pytest.approx((p.vout_v+p.diode_drop_v)*p.iout_a,rel=1e-9)
    assert state[4]/period == pytest.approx(p.iout_a,rel=1e-9)
    assert _integral(r.waveforms["Lm"].current_a) == pytest.approx(0.,abs=1e-10)
    circulating_square = 0.0
    for segment in r.waveforms["primary"].current_a.segments:
        phase = (segment.start_fraction+segment.end_fraction)/2
        bridge_voltage = _branch(r,"Q2",phase,True)-_branch(r,"Q4",phase,True)
        if abs(bridge_voltage)<1e-10:
            circulating_square += segment.duration_fraction*quad(lambda u,segment=segment:float(segment.evaluate(u))**2,0,1)[0]
    assert r.metrics["circulating_current_rms_a"]**2 == pytest.approx(circulating_square,rel=1e-10)
    for key in ("Q1","Q2","Q3","Q4","D1","D2","primary","Lm","Lleak","L1","Cout","Cin"):
        assert r.stress[key]["current_rms_a"]**2 == pytest.approx(_integral(r.waveforms[key].current_a,2),rel=1e-10,abs=1e-10)
    assert any("ZVS is UNKNOWN" in warning for warning in r.warnings)


@pytest.mark.parametrize("leakage,lm,vf",[(5e-6,1e-3,0.),(20e-6,1e-3,.7),(80e-6,.2e-3,.7)])
def test_psfb_bcm_matches_independent_state_boundary_and_both_limits(leakage,lm,vf):
    p = _psfb(leakage=leakage,lm=lm,vf=vf)
    h = .5/p.fsw_hz
    on = _psfb_derivative(p,p.vin_v,1)
    off = _psfb_derivative(p,0.,1)
    # Find the duty whose full half-cycle returns output current to zero,
    # integrating the simultaneous inductor equations rather than production
    # boundary/duty formulas.
    def terminal_current(active):
        first = solve_ivp(lambda t,y:[on[2]],(0,active),[0.],rtol=1e-11,atol=1e-12).y[0,-1]
        return solve_ivp(lambda t,y:[off[2]],(0,h-active),[first],rtol=1e-11,atol=1e-12).y[0,-1]
    active = brentq(terminal_current,0.,h,xtol=1e-16)
    boundary_load = on[2]*active/2
    r = calculate_stage(replace(p,iout_a=boundary_load,mode="bcm"))
    assert r.mode == "BCM"
    assert r.duty == pytest.approx(active/h,rel=1e-10)
    assert r.metrics["idle_fraction"] == 0
    for factor,expected in ((1-1e-6,"DCM"),(1+1e-6,"CCM")):
        side = calculate_stage(replace(p,iout_a=boundary_load*factor))
        assert side.mode == expected
        assert side.duty == pytest.approx(r.duty,rel=2e-6)
        for key in ("primary","Lm","L1","D1","D2","Q1","Q2","Q3","Q4","Cin","Cout"):
            assert side.stress[key]["current_rms_a"] == pytest.approx(r.stress[key]["current_rms_a"],rel=3e-6,abs=1e-9)
        for phase in np.linspace(.01,.99,81):
            for key in ("primary","Lm","L1"):
                assert _branch(side,key,phase) == pytest.approx(_branch(r,key,phase),rel=1e-4,abs=2e-6)


def test_psfb_forced_mode_and_no_load_domain_contract():
    p = _psfb()
    for mode in ("ccm","bcm"):
        with pytest.raises(ModelDomainError,match="requires DCM"):
            calculate_stage(replace(p,mode=mode))
    for mode in ("dcm","bcm"):
        with pytest.raises(ModelDomainError,match="CCM operating point"):
            calculate_stage(replace(p,iout_a=5.,mode=mode))
    with pytest.raises(ModelDomainError,match="no-load controller/flux policy"):
        calculate_stage(replace(p,iout_a=0.))
    with pytest.raises(ModelDomainError,match="Nonzero dead time"):
        calculate_stage(replace(p,dead_time_s=100e-9))

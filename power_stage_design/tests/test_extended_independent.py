"""M3 circuit-node/transformer checks independent of production reductions.

SEPIC, ZETA and Cuk nodes follow TI SLYU036A figures 6.0.1/7.0.1/8.0.1
(https://www.ti.com/lit/eb/slyu036a/slyu036a.pdf), visually inspected in the
source PDF. We solve four/five simultaneous periodic-state/charge constraints,
not the production equivalent-inductance duty formula. Other tests apply
terminal KCL/KVL and ideal-transformer ampere-turns to actual exported waves.
All are small-ripple circuit-model validation, not SPICE/hardware evidence.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, replace
from itertools import pairwise

import numpy as np
import pytest
from scipy.integrate import quad
from scipy.optimize import root

from power_stage_design.models import DesignInputs, ModelDomainError
from power_stage_design.tests.test_independent_validation import (
    _wave_integral,
    _wave_value,
)
from power_stage_design.topologies import calculate_stage


@dataclass(frozen=True)
class _TwoInductorCircuit:
    inputs: DesignInputs
    on: float
    off: float
    i1_initial: float
    i2_initial: float
    transfer_dc: float

    @property
    def intervals(self):
        return ((0., self.on), (self.on, self.on+self.off), (self.on+self.off, 1.))

    def nodes(self, state):
        p = self.inputs
        if p.topology == "T06":  # input--L1--A--C--B--D--output; L2 ground->B
            if state == 0:
                return 0., -self.transfer_dc
            if state == 1:
                return p.vout_v+p.diode_drop_v+self.transfer_dc, p.vout_v+p.diode_drop_v
            return p.vin_v, 0.
        if p.topology == "T07":  # input--Q--A--C--B--L2--output; L1 A->ground
            if state == 0:
                return p.vin_v, p.vin_v+self.transfer_dc
            if state == 1:
                return -p.diode_drop_v-self.transfer_dc, -p.diode_drop_v
            return 0., p.vout_v
        if p.topology == "T08":  # input--L1--A--C--B--L2--negative output
            if state == 0:
                return 0., -self.transfer_dc
            if state == 1:
                return p.diode_drop_v+self.transfer_dc, p.diode_drop_v
            return p.vin_v, -p.vout_v
        raise AssertionError(p.topology)

    def inductor_voltages(self, state):
        p = self.inputs
        a, b = self.nodes(state)
        if p.topology == "T06":
            return p.vin_v-a, -b
        if p.topology == "T07":
            return a, b-p.vout_v
        return p.vin_v-a, -p.vout_v-b

    def currents(self, phase):
        p = self.inputs
        slopes_on = np.asarray(self.inductor_voltages(0)) / (np.asarray([p.inductance_h,p.secondary_inductance_h])*p.fsw_hz)
        slopes_off = np.asarray(self.inductor_voltages(1)) / (np.asarray([p.inductance_h,p.secondary_inductance_h])*p.fsw_hz)
        initial = np.asarray([self.i1_initial,self.i2_initial])
        if phase < self.on:
            return initial+slopes_on*phase
        if phase < self.on+self.off:
            return initial+slopes_on*self.on+slopes_off*(phase-self.on)
        return initial  # Zero diode current, opposed circulating states remain.

    def branch(self, key, phase):
        p = self.inputs
        i1, i2 = self.currents(phase)
        state = 0 if phase < self.on else 1 if phase < self.on+self.off else 2
        switch = i1+i2 if state == 0 else 0.
        diode = i1+i2 if state == 1 else 0.
        output = diode if p.topology == "T06" else i2
        values = {"L1":i1, "L2":i2, "Q1":switch,"D1":diode,
                  "Ctransfer": -i2 if state == 0 else i1,
                  "Cout":output-p.iout_a,
                  "input":switch if p.topology == "T07" else i1}
        return values[key]

    def moment(self, key, power=1):
        return sum(quad(lambda t:self.branch(key,t)**power,a,b,epsabs=1e-12,epsrel=1e-12)[0]
                   for a,b in self.intervals if b>a)

    def constraints(self):
        volt_seconds = (np.asarray(self.inductor_voltages(0))*self.on+
                        np.asarray(self.inductor_voltages(1))*self.off)
        return [*volt_seconds,self.moment("Ctransfer"),self.moment("Cout")]


def _two_inductor_oracle(inputs):
    guess_vc = {"T06":inputs.vin_v,"T07":inputs.vout_v,"T08":inputs.vin_v+inputs.vout_v}[inputs.topology]
    def ccm_residual(unknown):
        d,i1,i2,vc=unknown
        return _TwoInductorCircuit(inputs,d,1-d,i1,i2,vc).constraints()
    solved = root(ccm_residual,[.25,inputs.iout_a,inputs.iout_a,guess_vc],tol=1e-11)
    assert solved.success and np.linalg.norm(ccm_residual(solved.x))<1e-9
    d,i1,i2,vc=solved.x
    if i1+i2>=0:
        return _TwoInductorCircuit(inputs,d,1-d,i1,i2,vc)
    def dcm_residual(unknown):
        d,off,i1,i2,vc=unknown
        return [*_TwoInductorCircuit(inputs,d,off,i1,i2,vc).constraints(),i1+i2]
    solved=root(dcm_residual,[d/2,(1-d)/2,0.,0.,guess_vc],tol=1e-11)
    assert solved.success and np.linalg.norm(dcm_residual(solved.x))<1e-9
    d,off,i1,i2,vc=solved.x
    assert 0<d and 0<off and d+off<1
    return _TwoInductorCircuit(inputs,d,off,i1,i2,vc)


@pytest.mark.parametrize("topology",["T06","T07","T08"])
@pytest.mark.parametrize("load",[2.,.01])
@pytest.mark.parametrize("diode_drop",[0.,.7])
@pytest.mark.parametrize("inductances",[(470e-6,100e-6),(100e-6,470e-6)])
def test_two_inductor_nodes_and_charge_solve_actual_branch_waves(topology,load,diode_drop,inductances):
    p=DesignInputs(topology,48.,12.,load,100e3,inductances[0],capacitance_f=1e-3,
                   secondary_inductance_h=inductances[1],transfer_capacitance_f=1e-3,
                   diode_drop_v=diode_drop)
    reference=_two_inductor_oracle(p)
    result=calculate_stage(p)
    assert result.duty==pytest.approx(reference.on,rel=1e-10)
    assert _wave_integral(result.waveforms["Ctransfer"].voltage_v)==pytest.approx(reference.transfer_dc,rel=1e-10)
    for key in ("L1","L2","Q1","D1","Ctransfer","Cout"):
        wave=result.waveforms[key].current_a
        for start,end in reference.intervals:
            if end-start<1e-10:continue
            for phase in start+(np.arange(9)+.5)*(end-start)/9:
                assert _wave_value(wave,phase)==pytest.approx(reference.branch(key,phase),rel=1e-9,abs=1e-10)
        assert result.stress[key]["current_rms_a"]==pytest.approx(math.sqrt(reference.moment(key,2)),rel=1e-10,abs=1e-11)
    for state,(start,end) in enumerate(reference.intervals):
        if end-start<1e-10:continue
        phase=(start+end)/2
        a,b=reference.nodes(state)
        vq=p.vin_v-a if topology=="T07" else a
        vd=p.vout_v-b if topology=="T06" else b if topology=="T07" else -b
        assert _wave_value(result.waveforms["Q1"].voltage_v,phase)==pytest.approx(vq,abs=1e-10)
        assert _wave_value(result.waveforms["D1"].voltage_v,phase)==pytest.approx(vd,abs=1e-10)
        for key,expected in zip(("L1","L2"),reference.inductor_voltages(state)):
            assert _wave_value(result.waveforms[key].voltage_v,phase)==pytest.approx(expected,abs=1e-10)
    pin=p.vin_v*reference.moment("input")
    assert pin==pytest.approx(p.vout_v*p.iout_a+p.diode_drop_v*reference.moment("D1"),rel=1e-10)
    if load==.01:
        assert result.mode=="DCM"
        assert abs(reference.i1_initial)>1e-6
        assert reference.i1_initial==pytest.approx(-reference.i2_initial,abs=1e-10)
    if topology=="T08":
        assert result.metrics["output_voltage_signed_v"]==-p.vout_v


def _probe_phases(result):
    edges={0.,1.}
    for device in result.waveforms.values():
        for signal in (device.current_a,device.voltage_v):
            edges.update(segment.start_fraction for segment in signal.segments)
            edges.update(segment.end_fraction for segment in signal.segments)
    edges=sorted(edges)
    return [(a+(b-a)*fraction,b-a) for a,b in pairwise(edges)
            if b-a>1e-12 for fraction in (.2,.5,.8)]


def _integrate_expression(result, function):
    edges=sorted({0.,1.,*[s.start_fraction for w in result.waveforms.values()
                          for signal in (w.current_a,w.voltage_v) for s in signal.segments],
                  *[s.end_fraction for w in result.waveforms.values()
                    for signal in (w.current_a,w.voltage_v) for s in signal.segments]})
    # Event lists from different branches may differ by one ulp at closure.
    # Such measure-zero slivers have no representable interior quadrature node.
    return sum(quad(function,a,b,epsabs=1e-10,epsrel=1e-10)[0] for a,b in pairwise(edges) if b-a>16*np.finfo(float).eps)


def _at(result,key,phase,voltage=False):
    wave=result.waveforms[key]
    return _wave_value(wave.voltage_v if voltage else wave.current_a,phase)


def _assert_inductor_differential_law(result,key,inductance):
    wave=result.waveforms[key]
    for phase,width in _probe_phases(result):
        delta=width*1e-5
        derivative=(_wave_value(wave.current_a,phase+delta)-_wave_value(wave.current_a,phase-delta))/(2*delta*wave.current_a.period_s)
        assert inductance*derivative==pytest.approx(_wave_value(wave.voltage_v,phase),rel=1e-7,abs=1e-7)
    assert _wave_integral(wave.voltage_v)==pytest.approx(0.,abs=1e-9)
    first=wave.current_a.segments[0].coefficients[0]
    last=sum(wave.current_a.segments[-1].coefficients)
    assert last==pytest.approx(first,rel=1e-10,abs=1e-10)


@pytest.mark.parametrize("topology",["T12","T13","T14"])
@pytest.mark.parametrize("load",[2.,.01])
@pytest.mark.parametrize("diode_drop",[0.,.7])
def test_forward_transformer_kcl_reset_and_output_freewheel_are_distinct(topology,load,diode_drop):
    p=DesignInputs(topology,48.,5.,load,100e3,470e-6,capacitance_f=1e-3,
                   turns_ratio_np_ns=2.,magnetizing_inductance_h=1e-3,
                   transfer_capacitance_f=1e-3,reset_turns_ratio_np_nr=1.4,diode_drop_v=diode_drop)
    result=calculate_stage(p)
    _assert_inductor_differential_law(result,"L1",p.inductance_h)
    _assert_inductor_differential_law(result,"Lm",p.magnetizing_inductance_h)
    for phase,_ in _probe_phases(result):
        il,im=_at(result,"L1",phase),_at(result,"Lm",phase)
        vp=_at(result,"Lm",phase,True)
        vout_node=_at(result,"L1",phase,True)+p.vout_v
        assert _at(result,"secondary",phase,True)==pytest.approx(vp/p.turns_ratio_np_ns)
        assert _at(result,"D1",phase,True)==pytest.approx(vout_node-vp/p.turns_ratio_np_ns)
        assert _at(result,"D2",phase,True)==pytest.approx(vout_node)
        assert _at(result,"D1",phase)+_at(result,"D2",phase)==pytest.approx(il,abs=1e-10)
        on=vp>0
        if on:
            assert vp==pytest.approx(p.vin_v)
            assert _at(result,"primary",phase)==pytest.approx(im+il/p.turns_ratio_np_ns)
            assert _at(result,"Q1",phase)==pytest.approx(_at(result,"primary",phase))
        else:
            assert _at(result,"Q1",phase)==pytest.approx(0.,abs=1e-12)
            if topology!="T13":
                assert _at(result,"primary",phase)==pytest.approx(im)
        qv=(p.vin_v-vp)/(2 if topology=="T14" else 1)
        assert _at(result,"Q1",phase,True)==pytest.approx(qv)
        for key in ("D1","D2"):
            if _at(result,key,phase)>1e-10:
                assert _at(result,key,phase,True)==pytest.approx(-p.diode_drop_v,abs=1e-10)
        if topology=="T13":
            assert _at(result,"Dreset1",phase,True)==pytest.approx(p.vin_v+vp/p.reset_turns_ratio_np_nr)
        elif topology=="T14":
            assert _at(result,"Dreset1",phase,True)==pytest.approx((p.vin_v+vp)/2)
            assert _at(result,"Q1",phase)==pytest.approx(_at(result,"Q2",phase))
        else:
            assert _at(result,"Q2",phase)==pytest.approx(0. if on else im)
    if topology=="T12":
        input_average=_wave_integral(result.waveforms["Q1"].current_a)
        assert _wave_integral(result.waveforms["Cclamp"].current_a)==pytest.approx(0.,abs=1e-12)
        assert result.stress["Lm"]["current_min_a"]<0<result.stress["Lm"]["current_max_a"]
    else:
        input_average=(_wave_integral(result.waveforms["Q1"].current_a)-
                       _wave_integral(result.waveforms["Dreset1"].current_a))
        # Zero reset current and continuing output freewheel must coexist.
        assert any(_at(result,"D2",t)>1e-9 and abs(_at(result,"Dreset1",t))<1e-12
                   for t,_ in _probe_phases(result))
    diode_power=p.diode_drop_v*sum(_wave_integral(w.current_a) for key,w in result.waveforms.items() if key.startswith("D"))
    assert input_average*p.vin_v==pytest.approx(p.vout_v*p.iout_a+diode_power,rel=1e-10,abs=1e-10)


@pytest.mark.parametrize("topology",["T15","T16","T17"])
@pytest.mark.parametrize("diode_drop",[0.,.7])
def test_bipolar_bridge_full_period_transformer_ampere_turns_and_two_ripples(topology,diode_drop):
    p=DesignInputs(topology,400.,48.,5.,100e3,100e-6,capacitance_f=1e-3,
                   turns_ratio_np_ns=2.,magnetizing_inductance_h=2e-3,
                   transfer_capacitance_f=1e-3,diode_drop_v=diode_drop)
    r=calculate_stage(p)
    _assert_inductor_differential_law(r,"L1",p.inductance_h)
    _assert_inductor_differential_law(r,"Lm",p.magnetizing_inductance_h)
    expected_primary_amplitude=p.vin_v/2 if topology=="T15" else p.vin_v
    for phase,_ in _probe_phases(r):
        il,im=_at(r,"L1",phase),_at(r,"Lm",phase)
        d1,d2=_at(r,"D1",phase),_at(r,"D2",phase)
        vp=_at(r,"Lm",phase,True)
        ip=_at(r,"Q1",phase)-_at(r,"Q2",phase)
        assert d1+d2==pytest.approx(il)
        assert ip==pytest.approx(im+(d1-d2)/p.turns_ratio_np_ns,abs=1e-10)
        assert abs(vp)==pytest.approx(expected_primary_amplitude if abs(vp)>1e-8 else 0.)
        node=_at(r,"L1",phase,True)+p.vout_v
        assert _at(r,"D1",phase,True)==pytest.approx(node-vp/p.turns_ratio_np_ns)
        assert _at(r,"D2",phase,True)==pytest.approx(node+vp/p.turns_ratio_np_ns)
        assert d1>=0 and d2>=0
        assert _at(r,"L1",(phase+.5)%1)==pytest.approx(il,rel=1e-10)
        assert _at(r,"Lm",(phase+.5)%1)==pytest.approx(-im,abs=1e-10)
    # Transformer terminal power balances load plus both output rectifiers;
    # its magnetizing-state energy returns to its initial value each cycle.
    power=_integrate_expression(r,lambda t:_at(r,"Lm",t,True)*(_at(r,"Q1",t)-_at(r,"Q2",t)))
    assert power==pytest.approx((p.vout_v+p.diode_drop_v)*p.iout_a,rel=1e-10)
    on_fractions=[]
    for key in ("Q1","Q2"):
        on_fractions.append(sum(s.end_fraction-s.start_fraction for s in r.waveforms[key].current_a.segments if any(abs(c)>1e-12 for c in s.coefficients)))
    assert on_fractions[0]==pytest.approx(on_fractions[1])
    assert 2*on_fractions[0]==pytest.approx((p.vout_v+p.diode_drop_v)*p.turns_ratio_np_ns/expected_primary_amplitude)
    assert r.waveforms["L1"].current_a.period_s==pytest.approx(1/p.fsw_hz)


@dataclass(frozen=True)
class _SeriesCapacitorCircuit:
    inputs: DesignInputs
    duty: float
    capacitor_dc: float
    ia_initial: float
    ib_initial: float

    @property
    def intervals(self):
        return ((0.,self.duty),(self.duty,.5),(.5,.5+self.duty),(.5+self.duty,1.))

    def nodes(self,state):
        # p is between Q1a and Ct, a/b feed the two inductors. Directly from
        # SLVA750A figures 5-7: Q1b joins p to b (not input to b).
        p=self.inputs
        if state==0:return p.vin_v,p.vin_v-self.capacitor_dc,0.
        if state==2:return self.capacitor_dc,0.,self.capacitor_dc
        return self.capacitor_dc,0.,0.

    def currents(self,phase):
        values=np.asarray([self.ia_initial,self.ib_initial])
        for state,(start,end) in enumerate(self.intervals):
            if phase<=start:break
            _,a,b=self.nodes(state)
            voltages=np.asarray([a-self.inputs.vout_v,b-self.inputs.vout_v])
            elapsed=min(phase,end)-start
            values=values+voltages*elapsed/(self.inputs.fsw_hz*np.asarray([self.inputs.inductance_h,self.inputs.secondary_inductance_h]))
        return values

    def branch(self,key,phase):
        ia,ib=self.currents(phase)
        state=next(j for j,(a,b) in enumerate(self.intervals) if a<=phase<b)
        ct=ia if state==0 else -ib if state==2 else 0.
        return {"L1":ia,"L2":ib,"Cseries":ct,"Cout":ia+ib-self.inputs.iout_a,
                "Q1a":ia if state==0 else 0.,"Q1b":ib if state==2 else 0.,
                "Q2a":0. if state==0 else ia+ib if state==2 else ia,
                "Q2b":0. if state==2 else ib}[key]

    def moment(self,key,power=1):
        return sum(quad(lambda t:self.branch(key,t)**power,a,b,epsabs=1e-11,epsrel=1e-11)[0]
                   for a,b in self.intervals if b>a)


def _series_capacitor_oracle(p):
    def residual(unknown):
        d,vc,ia,ib=unknown
        ref=_SeriesCapacitorCircuit(p,d,vc,ia,ib)
        final=ref.currents(1.)
        return [*(final-[ia,ib]),ref.moment("Cseries"),ref.moment("Cout")]
    solved=root(residual,[.2,p.vin_v*.5,p.iout_a*.5,p.iout_a*.5],method="lm",tol=1e-11)
    assert solved.success and np.linalg.norm(residual(solved.x))<1e-10
    return _SeriesCapacitorCircuit(p,*solved.x)


@pytest.mark.parametrize("output",[.6,1.2,2.5])
@pytest.mark.parametrize("second_inductance",[1.e-6,1.3e-6])
def test_series_capacitor_buck_nodal_oracle_checks_full_voltage_and_combined_branch(output,second_inductance):
    p=DesignInputs("T19",12.,output,15.,500e3,1e-6,capacitance_f=200e-6,
                   secondary_inductance_h=second_inductance,transfer_capacitance_f=200e-6)
    ref=_series_capacitor_oracle(p)
    result=calculate_stage(p)
    assert result.duty==pytest.approx(ref.duty,rel=1e-10)
    assert _wave_integral(result.waveforms["Cseries"].voltage_v)==pytest.approx(ref.capacitor_dc)
    for key in ("L1","L2","Cseries","Cout","Q1a","Q1b","Q2a","Q2b"):
        for state,(a,b) in enumerate(ref.intervals):
            for t in a+(np.arange(7)+.5)*(b-a)/7:
                assert _at(result,key,t)==pytest.approx(ref.branch(key,t),rel=1e-10,abs=1e-10)
        assert result.stress[key]["current_rms_a"]==pytest.approx(math.sqrt(ref.moment(key,2)),rel=1e-10,abs=1e-10)
    for state,(a,b) in enumerate(ref.intervals):
        node_p,node_a,node_b=ref.nodes(state)
        t=(a+b)/2
        for key,voltage in {"Q1a":p.vin_v-node_p,"Q1b":node_p-node_b,"Q2a":node_a,"Q2b":node_b}.items():
            assert _at(result,key,t,True)==pytest.approx(voltage)
    assert p.vin_v*ref.moment("Q1a")==pytest.approx(p.vout_v*p.iout_a,rel=1e-10)


@pytest.mark.parametrize("leakage",[5e-6,20e-6,80e-6])
@pytest.mark.parametrize("diode_drop",[0.,.7])
def test_psfb_satisfies_all_three_state_laws_rectifier_commutation_and_bridge_kcl(leakage,diode_drop):
    p=DesignInputs("T18",400.,48.,5.,100e3,100e-6,capacitance_f=1e-3,
                   turns_ratio_np_ns=4.,magnetizing_inductance_h=1e-3,
                   leakage_inductance_h=leakage,diode_drop_v=diode_drop)
    r=calculate_stage(p)
    for key,inductance in (("L1",p.inductance_h),("Lm",p.magnetizing_inductance_h),("Lleak",p.leakage_inductance_h)):
        _assert_inductor_differential_law(r,key,inductance)
    overlapping=circulating=False
    for t,_ in _probe_phases(r):
        ip,im,il=_at(r,"primary",t),_at(r,"Lm",t),_at(r,"L1",t)
        vp=_at(r,"primary",t,True)
        d1,d2=_at(r,"D1",t),_at(r,"D2",t)
        q1,q2,q3,q4=(_at(r,key,t) for key in ("Q1","Q2","Q3","Q4"))
        a=p.vin_v-_at(r,"Q1",t,True)
        b=p.vin_v-_at(r,"Q3",t,True)
        assert _at(r,"Q2",t,True)==pytest.approx(a)
        assert _at(r,"Q4",t,True)==pytest.approx(b)
        assert q1-q2==pytest.approx(ip,abs=1e-10)
        assert q4-q3==pytest.approx(ip,abs=1e-10)
        assert a-b-vp==pytest.approx(_at(r,"Lleak",t,True))
        assert d1+d2==pytest.approx(il)
        assert ip-im==pytest.approx((d1-d2)/p.turns_ratio_np_ns,abs=1e-10)
        output_node=_at(r,"L1",t,True)+p.vout_v
        assert _at(r,"D1",t,True)==pytest.approx(output_node-vp/p.turns_ratio_np_ns,abs=1e-10)
        assert _at(r,"D2",t,True)==pytest.approx(output_node+vp/p.turns_ratio_np_ns,abs=1e-10)
        if d1>1e-8 and d2>1e-8:
            overlapping=True
            assert vp==pytest.approx(0.,abs=1e-10)
        if abs(a-b)<1e-10 and abs(ip)>1e-8:
            circulating=True
        assert _at(r,"L1",(t+.5)%1)==pytest.approx(il,rel=1e-9)
        assert _at(r,"primary",(t+.5)%1)==pytest.approx(-ip,rel=1e-9,abs=1e-9)
    assert overlapping and circulating
    # The two high-side terminal currents are the DC-source branch (signed).
    pin=p.vin_v*_integrate_expression(r,lambda t:_at(r,"Q1",t)+_at(r,"Q3",t))
    assert pin==pytest.approx((p.vout_v+p.diode_drop_v)*p.iout_a,rel=1e-10,abs=1e-9)
    assert any("ZVS" in warning and "UNKNOWN" in warning for warning in r.warnings)


@pytest.mark.parametrize("load",[.05,.2,1.,3.])
def test_qr_flyback_matches_independent_lc_ode_and_includes_turnon_energy(load):
    from scipy.integrate import solve_ivp
    p=DesignInputs("T11",400.,48.,load,100e3,1e-3,capacitance_f=1e-3,
                   resonant_capacitance_f=200e-12,turns_ratio_np_ns=4.)
    r=calculate_stage(p)
    im=r.waveforms["Lm"].current_a
    drain=r.waveforms["Q1"].voltage_v
    period=im.period_s
    assert 1/period<=p.fsw_hz*(1+1e-12)
    assert r.metrics["actual_switching_frequency_hz"]==pytest.approx(1/period)
    _assert_inductor_differential_law(r,"Lm",p.inductance_h)
    segments=im.segments
    assert len(segments)==4
    # Independently propagate the two LC intervals as differential equations;
    # the production representation happens to be harmonic, but is not used
    # by this oracle's integrator or its state equations.
    for index in (1,3):
        segment=segments[index]
        start,end=segment.start_fraction,segment.end_fraction
        tiny=(end-start)*1e-9
        i0=_wave_value(im,start+tiny)
        v0=_wave_value(drain,start+tiny)
        elapsed=(end-start-tiny)*period
        initial_time=(start+tiny)*period
        rhs=lambda _t,state:[(p.vin_v-state[1])/p.inductance_h,
                              state[0]/p.resonant_capacitance_f]
        solved=solve_ivp(rhs,[0.,elapsed],[i0,v0],rtol=1e-10,atol=1e-11,
                         dense_output=True,max_step=elapsed/120)
        assert solved.success
        for fraction in np.linspace(.01,.99,25):
            phase=start+tiny+fraction*(end-start-tiny)
            expected=solved.sol(phase*period-initial_time)
            assert _wave_value(im,phase)==pytest.approx(expected[0],rel=1e-8,abs=1e-9)
            assert _wave_value(drain,phase)==pytest.approx(expected[1],rel=1e-8,abs=1e-7)
    # Real primary current disappears while the secondary carries the stored
    # magnetizing energy; the primary-referred state must remain nonzero.
    demag=(segments[2].start_fraction+segments[2].end_fraction)/2
    assert _at(r,"primary",demag)==pytest.approx(0.,abs=1e-12)
    assert _at(r,"D1",demag)==pytest.approx(p.turns_ratio_np_ns*_at(r,"Lm",demag))
    assert _at(r,"Lm",demag)>0
    assert _wave_integral(r.waveforms["D1"].current_a)==pytest.approx(p.iout_a,rel=1e-10)
    # Positive valley voltage is discharged at turn-on. Its energy is separate
    # from the finite-conduction-current RMS, not silently discarded.
    last=drain.segments[-1]
    u=1.-1e-10
    endphase=last.start_fraction+u*(last.end_fraction-last.start_fraction)
    valley_voltage=_wave_value(drain,endphase)
    energy=.5*p.resonant_capacitance_f*valley_voltage**2
    pin=p.vin_v*_wave_integral(r.waveforms["primary"].current_a)
    assert pin==pytest.approx(p.vout_v*p.iout_a+energy/period,rel=1e-9,abs=1e-8)
    assert r.metrics["turn_on_capacitive_loss_w"]==pytest.approx(energy/period,rel=1e-9)
    for key,wave in r.waveforms.items():
        assert wave.current_a.period_s==period
        for signal,suffix in ((wave.current_a,"current_rms_a"),(wave.voltage_v,"voltage_rms_v")):
            assert r.stress[key][suffix]==pytest.approx(math.sqrt(_wave_integral(signal,2)),rel=1e-9,abs=1e-9)
    assert any("impulse" in assumption for assumption in r.assumptions)


@pytest.mark.parametrize("ratio,primary_load", [(1., 0.), (1., .1), (2., 0.), (2., .1)])
def test_flybuck_matches_exact_charge_flux_propagation_and_dual_output_power(ratio, primary_load):
    """Independent state observer using exact matrix exponentials, not shooting.

    States are physical fluxes and capacitor charges. Gate/diode events are
    read from the exported branches, then each continuous interval is solved
    exactly as an affine linear circuit. This independently verifies periodic
    closure, dynamic rails and every current direction against SNVA790A fig2-1.
    """
    from scipy.linalg import expm
    p = DesignInputs("T04", 24., 5., .2, 100e3, 47e-6, capacitance_f=100e-6,
                     turns_ratio_np_ns=ratio, diode_drop_v=.4, leakage_inductance_h=1e-6,
                     primary_capacitance_f=47e-6, primary_output_current_a=primary_load)
    r = calculate_stage(p)
    period = 1/p.fsw_hz
    lm, lk, cp, co, n = p.inductance_h, p.leakage_inductance_h, p.primary_capacitance_f, p.capacitance_f, ratio
    intervals = []
    for segment in r.waveforms["Lm"].current_a.segments:
        mid = (segment.start_fraction+segment.end_fraction)/2
        gate = _at(r, "Q1", mid, True) < p.vin_v/2
        diode = _at(r, "D1", mid) > 1e-9
        if intervals and intervals[-1][2:] == (gate, diode):
            intervals[-1] = (intervals[-1][0], segment.end_fraction, gate, diode)
        else:
            intervals.append((segment.start_fraction, segment.end_fraction, gate, diode))

    def observed(phase):
        return np.array([lm*_at(r,"Lm",phase), cp*_at(r,"Cprimary",phase,True),
                         lk*_at(r,"D1",phase), co*_at(r,"Cout",phase,True), 1.])

    initial = observed(0.)
    state = initial.copy()
    for start, end, gate, diode in intervals:
        # d(flux)/dt = terminal voltage; d(charge)/dt = branch KCL.
        a = np.zeros((5, 5))
        source = p.vin_v if gate else 0.
        a[0,1], a[0,4] = -1/cp, source
        a[1,0], a[1,2], a[1,4] = 1/lm, -1/(n*lk), -primary_load
        if diode:
            a[2,1], a[2,3], a[2,4] = 1/(n*cp), -1/co, -source/n-p.diode_drop_v
        a[3,2], a[3,4] = 1/lk, -p.iout_a
        for fraction in np.linspace(.025, .975, 19):
            elapsed = fraction*(end-start)*period
            expected = expm(a*elapsed) @ state
            np.testing.assert_allclose(observed(start+fraction*(end-start)), expected,
                                       rtol=3e-7, atol=2e-12)
        state = expm(a*((end-start)*period)) @ state
    np.testing.assert_allclose(state, initial, rtol=3e-7, atol=2e-12)
    assert any(gate and diode for _,_,gate,diode in intervals), "Leakage commutation after high-side turn-on must survive"
    assert any(not gate and diode for _,_,gate,diode in intervals)
    assert r.stress["primary"]["current_min_a"] < 0.
    for phase in np.linspace(.001,.999,301):
        im, ip, secondary = (_at(r,key,phase) for key in ("Lm","primary","secondary"))
        vp, vo = _at(r,"Cprimary",phase,True), _at(r,"Cout",phase,True)
        switch_node = p.vin_v-_at(r,"Q1",phase,True)
        assert im == pytest.approx(ip+secondary/n, abs=1e-10)
        assert _at(r,"Q1",phase)-_at(r,"Q2",phase) == pytest.approx(ip,abs=1e-10)
        assert _at(r,"Lm",phase,True) == pytest.approx(switch_node-vp)
        assert _at(r,"secondary",phase,True) == pytest.approx((vp-switch_node)/n)
        assert _at(r,"Cprimary",phase) == pytest.approx(ip-primary_load,abs=1e-10)
        assert _at(r,"Cout",phase) == pytest.approx(secondary-p.iout_a,abs=1e-10)
        if secondary > 1e-8:
            assert _at(r,"D1",phase,True) == pytest.approx(-p.diode_drop_v)
            assert _at(r,"Lleak",phase,True) == pytest.approx((vp-switch_node)/n-vo-p.diode_drop_v)
    mean_primary = _wave_integral(r.waveforms["Cprimary"].voltage_v)
    mean_isolated = _wave_integral(r.waveforms["Cout"].voltage_v)
    assert mean_isolated == pytest.approx(p.vout_v,abs=2e-8)
    for key in ("Cprimary","Cout"):
        assert _wave_integral(r.waveforms[key].current_a) == pytest.approx(0.,abs=1e-8)
    pin = p.vin_v*_wave_integral(r.waveforms["Q1"].current_a)
    assert pin == pytest.approx(primary_load*mean_primary+p.iout_a*mean_isolated+
                               p.diode_drop_v*_wave_integral(r.waveforms["D1"].current_a),abs=1e-8)


@pytest.mark.parametrize("load", [2., .001])
@pytest.mark.parametrize("vf", [0., .7])
def test_inverting_buckboost_actual_node_kvl_and_signed_output(load, vf):
    p = DesignInputs("T03",24.,12.,load,100e3,100e-6,capacitance_f=1e-3,diode_drop_v=vf)
    r = calculate_stage(p)
    _assert_inductor_differential_law(r,"L1",p.inductance_h)
    assert r.metrics["output_voltage_signed_v"] == -12.
    for phase,_ in _probe_phases(r):
        assert _at(r,"L1",phase) == pytest.approx(_at(r,"Q1",phase)+_at(r,"D1",phase))
        assert _at(r,"L1",phase,True) == pytest.approx(p.vin_v-_at(r,"Q1",phase,True))
        assert _at(r,"D1",phase,True) == pytest.approx(p.vout_v+_at(r,"L1",phase,True))
        assert _at(r,"Cout",phase)+load == pytest.approx(_at(r,"D1",phase))
    assert p.vin_v*_wave_integral(r.waveforms["Q1"].current_a) == pytest.approx((p.vout_v+vf)*load)


@pytest.mark.parametrize("output", [5.,48.])
@pytest.mark.parametrize("vf", [0.,.7])
def test_two_switch_buckboost_dual_diode_and_separate_blocking_nodes(output,vf):
    p=DesignInputs("T05",24.,output,2.,100e3,470e-6,capacitance_f=1e-3,diode_drop_v=vf)
    r=calculate_stage(p)
    _assert_inductor_differential_law(r,"L1",p.inductance_h)
    for phase,_ in _probe_phases(r):
        a=p.vin_v-_at(r,"Q1",phase,True)
        b=_at(r,"Q2",phase,True)
        assert _at(r,"L1",phase,True)==pytest.approx(a-b)
        assert _at(r,"D1",phase,True)==pytest.approx(a)
        assert _at(r,"D2",phase,True)==pytest.approx(p.vout_v-b)
        assert _at(r,"Q1",phase)==pytest.approx(_at(r,"Q2",phase))
        assert _at(r,"D1",phase)==pytest.approx(_at(r,"D2",phase))
        assert _at(r,"Q1",phase)+_at(r,"D1",phase)==pytest.approx(_at(r,"L1",phase))
        assert _at(r,"D2",phase)==pytest.approx(p.iout_a+_at(r,"Cout",phase))
    assert p.vin_v*_wave_integral(r.waveforms["Q1"].current_a)==pytest.approx((p.vout_v+2*vf)*p.iout_a)
    with pytest.raises(ModelDomainError):calculate_stage(replace(p,iout_a=1e-5))


@pytest.mark.parametrize("load", [2.,.001])
@pytest.mark.parametrize("ratio", [1.,2.])
@pytest.mark.parametrize("vf", [0.,.7])
def test_two_switch_flyback_winding_gating_and_clamp_headroom(load,ratio,vf):
    p=DesignInputs("T10",48.,12.,load,100e3,1e-3,capacitance_f=1e-3,turns_ratio_np_ns=ratio,diode_drop_v=vf)
    r=calculate_stage(p)
    _assert_inductor_differential_law(r,"Lm",p.inductance_h)
    for phase,_ in _probe_phases(r):
        assert _at(r,"Q1",phase)==pytest.approx(_at(r,"Q2",phase))
        assert _at(r,"primary",phase)+_at(r,"secondary",phase)/ratio==pytest.approx(_at(r,"Lm",phase))
        assert _at(r,"Q1",phase,True)+_at(r,"Q2",phase,True)+_at(r,"primary",phase,True)==pytest.approx(p.vin_v)
        assert _at(r,"secondary",phase,True)==pytest.approx(-_at(r,"primary",phase,True)/ratio)
        assert _at(r,"D1",phase,True)==pytest.approx(p.vout_v-_at(r,"secondary",phase,True))
        assert _at(r,"Dclamp1",phase)==_at(r,"Dclamp2",phase)==0.
        assert _at(r,"Dclamp1",phase,True)>=0.
    assert p.vin_v*_wave_integral(r.waveforms["Q1"].current_a)==pytest.approx((p.vout_v+vf)*load)
    with pytest.raises(ModelDomainError):calculate_stage(replace(p,turns_ratio_np_ns=5.))


@pytest.mark.parametrize("topology", ["T15","T16","T17"])
@pytest.mark.parametrize("load", [.01,.5])
@pytest.mark.parametrize("vf", [0.,.7])
def test_light_load_bridge_free_magnetizing_demagnetization_is_not_clipped(topology,load,vf):
    p=DesignInputs(topology,400.,48.,load,100e3,100e-6,capacitance_f=1e-3,
                   turns_ratio_np_ns=2.,magnetizing_inductance_h=2e-3,
                   transfer_capacitance_f=1e-3,diode_drop_v=vf)
    r=calculate_stage(p)
    _assert_inductor_differential_law(r,"L1",p.inductance_h)
    _assert_inductor_differential_law(r,"Lm",p.magnetizing_inductance_h)
    found_demagnetization=False
    for phase,_ in _probe_phases(r):
        il,im=_at(r,"L1",phase),_at(r,"Lm",phase)
        d1,d2=_at(r,"D1",phase),_at(r,"D2",phase)
        vp=_at(r,"Lm",phase,True)
        ip=_at(r,"Q1",phase)-_at(r,"Q2",phase)
        assert il>=-1e-10 and d1>=-1e-10 and d2>=-1e-10
        assert d1+d2==pytest.approx(il,abs=1e-10)
        assert ip==pytest.approx(im+(d1-d2)/p.turns_ratio_np_ns,abs=1e-10)
        node=_at(r,"L1",phase,True)+p.vout_v
        assert _at(r,"D1",phase,True)==pytest.approx(node-vp/p.turns_ratio_np_ns,abs=1e-10)
        assert _at(r,"D2",phase,True)==pytest.approx(node+vp/p.turns_ratio_np_ns,abs=1e-10)
        if abs(ip)<1e-9 and abs(vp)>1e-8 and abs(im)>1e-8:
            found_demagnetization=True
            assert vp*im<0  # Finite stored magnetic energy feeds the output.
            assert min(d1,d2)==pytest.approx(0.,abs=1e-10)
            assert il==pytest.approx(abs(im)*p.turns_ratio_np_ns)
        assert _at(r,"L1",(phase+.5)%1)==pytest.approx(il,abs=1e-10)
    assert found_demagnetization
    pin=_integrate_expression(r,lambda t:_at(r,"Lm",t,True)*(_at(r,"Q1",t)-_at(r,"Q2",t)))
    assert pin==pytest.approx((p.vout_v+vf)*load,rel=1e-9,abs=1e-10)
    assert _wave_integral(r.waveforms["Cout"].current_a)==pytest.approx(0.,abs=1e-10)

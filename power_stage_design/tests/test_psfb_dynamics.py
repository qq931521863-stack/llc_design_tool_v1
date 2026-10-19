"""Independent circuit conservation, alternate ODE and full-period quadrature.

These are tests of the declared constant-Coss circuit, not hardware correlation.
"""
from dataclasses import FrozenInstanceError, replace
from types import SimpleNamespace

import numpy as np
import pytest
from scipy.integrate import solve_ivp

from power_stage_design.models import DesignInputs, ModelDomainError
from power_stage_design.psfb_dynamics import (
    MODEL_VERSION,
    PSFBOptions,
    _Circuit,
    _shoot,
    calculate_psfb_dynamic,
)


def example(dead_time=200e-9, load=5.):
    return DesignInputs("T18",400.,48.,load,100e3,100e-6,capacitance_f=1e-3,
                        turns_ratio_np_ns=4.,diode_drop_v=.7,
                        magnetizing_inductance_h=1e-3,leakage_inductance_h=20e-6,
                        dead_time_s=dead_time)


def assumptions(**changes):
    return replace(PSFBOptions(300e-12,.15,.8,.05,100e-12,1e-9,.02,
                              "Explicit illustrative circuit values; not measured device data"), **changes)


@pytest.fixture(scope="module")
def baseline():
    return calculate_psfb_dynamic(example(), assumptions())


def value(wave, fraction, side="right"):
    for segment in wave.segments:
        if segment.start_fraction <= fraction <= segment.end_fraction:
            if side == "right" and fraction == segment.end_fraction and fraction < 1:
                continue
            u=(fraction-segment.start_fraction)/segment.duration_fraction
            return float(segment.evaluate(u))
    raise AssertionError(f"No waveform interval at {fraction}")


def gauss_product(a, b):
    """Independent Gauss-Legendre integration, not waveform integral methods."""
    nodes, weights=np.polynomial.legendre.leggauss(12)
    u=(nodes+1)/2
    return sum(segment.duration_fraction*np.dot(weights,segment.evaluate(u)*other.evaluate(u))/2
               for segment,other in zip(a.segments,b.segments))


def test_explicit_frozen_component_and_revision_contract():
    with pytest.raises(TypeError):
        PSFBOptions()
    o=assumptions()
    with pytest.raises(FrozenInstanceError):
        o.coss_f=0
    assert o.model_revision == MODEL_VERSION


@pytest.mark.parametrize("field,value",[
    ("coss_f",0.),("coss_f",True),("coss_f",1e-100),("coss_f",float("nan")),("r_on_ohm",-1.),
    ("transformer_capacitance_f",0.),("rectifier_capacitance_f",0.),
    ("body_diode_resistance_ohm",0.),("rectifier_resistance_ohm",0.),
    ("body_diode_drop_v",-1.),("relative_tolerance",1e-3),
    ("max_nfev",5), ("max_nfev",10.),("parameter_source",""),
    ("model_revision","future"),("zvs_voltage_tolerance_v",50.),
])
def test_invalid_or_singular_options_rejected(field,value):
    with pytest.raises(ModelDomainError):
        calculate_psfb_dynamic(example(), assumptions(**{field:value}))


@pytest.mark.parametrize("changes",[
    {"dead_time_s":0.},{"dead_time_s":3e-6},{"iout_a":0.},
    {"mode":"DCM"},{"iout_a":.1},{"leakage_inductance_h":None},
    {"magnetizing_inductance_h":None},{"topology":"T01"},
    {"iout_a":1000.},{"vout_v":200.},
])
def test_unsupported_or_unreachable_operating_points(changes):
    with pytest.raises(ModelDomainError):
        calculate_psfb_dynamic(replace(example(),**changes),assumptions())


def test_cancellation_at_entry_and_inside_integration():
    with pytest.raises(InterruptedError):
        calculate_psfb_dynamic(example(),assumptions(),cancelled=lambda:True)
    calls=0
    def cancel():
        nonlocal calls
        calls+=1
        return calls>=8
    with pytest.raises(InterruptedError):
        calculate_psfb_dynamic(example(),assumptions(),cancelled=cancel)
    assert calls>=8


def test_nonconvergence_is_failure_not_ideal_fallback(monkeypatch):
    def failed(fun, x0, **kwargs):
        return SimpleNamespace(x=x0,success=False)
    monkeypatch.setattr("power_stage_design.psfb_dynamics.least_squares",failed)
    with pytest.raises(ModelDomainError,match="did not close"):
        _shoot(_Circuit(example(),assumptions()))


def test_pointwise_passive_energy_identity_independently_derived():
    p,o=example(),assumptions()
    circuit=_Circuit(p,o)
    rng=np.random.default_rng(18)
    for _ in range(50):
        va,vb=rng.uniform(-5,405,2)
        ip,im=rng.uniform(-5,5,2)
        vp=rng.uniform(-450,450)
        vr=rng.uniform(-2,120)
        io=rng.uniform(.1,12)
        y=np.array([va,vb,ip,im,vp,vr,io])
        gates=((0,0,0,1),(1,0,0,1),(1,0,0,0),(1,0,1,0),
               (0,0,1,0),(0,1,1,0),(0,1,0,0),(0,1,0,1))[int(rng.integers(8))]
        dy=circuit.derivative(y,gates)
        # Derive terminal and heat powers directly from component definitions.
        v=np.array([400-va,va,400-vb,vb])
        channel=np.array(gates)*v/o.r_on_ohm
        body=np.maximum(-v-o.body_diode_drop_v,0)/o.body_diode_resistance_ohm
        diode=np.maximum(np.array([vp/4-vr,-vp/4-vr])-.7,0)/o.rectifier_resistance_ohm
        source=channel[0]+channel[2]-body[0]-body[2]-o.coss_f*(dy[0]+dy[1])
        heat=(sum(channel**2)*o.r_on_ohm
              +sum(body*o.body_diode_drop_v+body**2*o.body_diode_resistance_ohm)
              +sum(.7*diode+diode**2*o.rectifier_resistance_ohm))
        gradient=np.array([o.coss_f*(2*va-400),o.coss_f*(2*vb-400),
                           p.leakage_inductance_h*ip,p.magnetizing_inductance_h*im,
                           o.transformer_capacitance_f*vp,o.rectifier_capacitance_f*vr,
                           p.inductance_h*io])
        assert np.dot(gradient,dy) == pytest.approx(400*source-48*io-heat,rel=2e-12,abs=2e-8)


def test_requested_load_periodicity_and_energy_closure(baseline):
    r=baseline
    assert r.mode == "CCM" and r.model_version == MODEL_VERSION
    assert r.inputs.psfb_options == assumptions()
    assert r.design_id == r.inputs.design_id
    assert r.design_id != example().design_id
    assert r.metrics["phase_shift_deg"] == pytest.approx(94.22944,abs=.002)
    assert r.waveforms["L1"].current_a.statistics()["mean"] == pytest.approx(5.,abs=1e-6)
    assert abs(r.diagnostics["modeled_power_balance_w"]) < .002
    assert abs(r.diagnostics["half_period_stored_energy_residual_j"]) < 1e-8
    assert r.diagnostics["periodic_shooting_normalized_residual"] < 1e-7
    assert abs(r.diagnostics["inductor_mean_voltage_v"]) < 1e-5
    assert abs(r.diagnostics["transformer_mean_voltage_v"]) < 1e-9
    assert abs(r.diagnostics["leakage_mean_voltage_v"]) < 1e-9
    assert abs(r.diagnostics["output_capacitor_mean_current_a"]) < 1e-6
    assert r.waveforms["L1"].current_a.statistics()["min"] > 0
    assert r.provenance["parameter_source"] == assumptions().parameter_source
    for key in ("L1","Lleak","Lm"):
        w=r.waveforms[key].current_a
        assert value(w,0.) == pytest.approx(value(w,1.,"left"),abs=2e-5)
    for key in ("Cprimary","Crectifier","node_A","node_B"):
        w=r.waveforms[key].voltage_v
        assert value(w,0.) == pytest.approx(value(w,1.,"left"),abs=2e-5)


def test_quadrature_rms_loss_and_separate_channel_branches(baseline):
    r=baseline
    channel=0.
    for k in range(1,5):
        device=r.waveforms[f"Q{k}"]
        independent_rms=np.sqrt(gauss_product(device.current_a,device.current_a))
        assert independent_rms == pytest.approx(r.stress[f"Q{k}"]["current_rms_a"],rel=1e-9)
        loss=gauss_product(device.current_a,device.current_a)*assumptions().r_on_ohm
        assert loss == pytest.approx(gauss_product(device.current_a,device.voltage_v),rel=1e-9)
        channel+=loss
        assert f"Coss{k}" in r.waveforms and f"Dbody{k}" in r.waveforms
        assert r.waveforms[f"Dbody{k}"].current_a.statistics()["min"] > -1e-7
        cap=r.waveforms[f"Coss{k}"]
        assert abs(gauss_product(cap.current_a,cap.voltage_v)) < 1e-6
    assert channel == pytest.approx(r.metrics["modeled_channel_loss_w"],rel=1e-9)
    assert r.metrics["modeled_body_diode_loss_w"]>0
    assert r.metrics["modeled_rectifier_loss_w"]>0
    # Both switches off during actual dead time, but Coss carries real current.
    t=example().dead_time_s*example().fsw_hz/2
    assert value(r.waveforms["Q1"].current_a,t)==0
    assert value(r.waveforms["Q2"].current_a,t)==0
    assert abs(value(r.waveforms["Coss1"].current_a,t))>.01
    va=value(r.waveforms["node_A"].voltage_v,t)
    assert 0<va<400


def test_gate_on_zvs_evidence_matches_actual_node_voltage(baseline):
    r=baseline
    for k in range(1,5):
        fraction=r.metrics[f"Q{k}_gate_on_time_s"]*example().fsw_hz
        voltage=value(r.waveforms[f"Q{k}"].voltage_v,fraction,"left")
        assert r.metrics[f"Q{k}_gate_on_vds_v"] == pytest.approx(voltage,abs=1e-5)
        assert r.metrics[f"Q{k}_gate_on_residual_coss_energy_j"] == pytest.approx(.5*assumptions().coss_f*max(voltage,0)**2,abs=1e-12)
    assert r.metrics["Q1_zvs_qualified"] == 0.
    assert r.metrics["Q3_zvs_qualified"] == 1.
    assert r.metrics["all_switches_zvs_qualified"] == 0.


def test_alternate_radau_equations_match_half_period(baseline):
    """Separate physical RHS and stiff solver; no production RHS or root solve."""
    p=example()
    r=baseline
    y0=np.array([value(r.waveforms["node_A"].voltage_v,0),
                 value(r.waveforms["node_B"].voltage_v,0),
                 value(r.waveforms["Lleak"].current_a,0),
                 value(r.waveforms["Lm"].current_a,0),
                 value(r.waveforms["Cprimary"].voltage_v,0),
                 value(r.waveforms["Crectifier"].voltage_v,0),
                 value(r.waveforms["L1"].current_a,0),0.])
    half=.5/p.fsw_hz
    edges=[0.,p.dead_time_s,r.duty*half,r.duty*half+p.dead_time_s,half]
    state=y0.copy()
    for left,right,g in zip(edges,edges[1:],[(0,0,0,1),(1,0,0,1),(1,0,0,0),(1,0,1,0)]):
        def independent_rhs(t,y,g=g):
            a,b,i,m,v,z,j,_=y
            transistor=np.array(g)*np.array([400-a,a,400-b,b])/.15
            body=np.maximum(-np.array([400-a,a,400-b,b])-.8,0)/.05
            d1=max(v/4-z-.7,0)/.02
            d2=max(-v/4-z-.7,0)/.02
            return [(transistor[0]-body[0]-transistor[1]+body[1]-i)/(600e-12),
                    (transistor[2]-body[2]-transistor[3]+body[3]+i)/(600e-12),
                    (a-b-v)/20e-6,v/1e-3,(i-m-(d1-d2)/4)/100e-12,
                    (d1+d2-j)/1e-9,(z-48)/100e-6,j]
        sol=solve_ivp(independent_rhs,(left,right),state,method="Radau",rtol=2e-10,atol=1e-11,max_step=half/100)
        assert sol.success
        state=sol.y[:,-1]
    expected=y0[:7]*np.array([-1,-1,-1,-1,-1,1,1])+np.array([400,400,0,0,0,0,0])
    assert state[:7] == pytest.approx(expected,abs=2e-5)
    assert state[7]/half == pytest.approx(5.,abs=2e-6)


def test_short_dead_time_changes_real_waveforms_loss_and_zvs(baseline):
    short=calculate_psfb_dynamic(example(dead_time=50e-9),assumptions())
    assert short.metrics["all_switches_zvs_qualified"] == 0.
    assert max(short.metrics[f"Q{k}_gate_on_vds_v"] for k in range(1,5))>100
    assert short.metrics["modeled_channel_loss_w"] > baseline.metrics["modeled_channel_loss_w"]+1
    assert short.duty != pytest.approx(baseline.duty,abs=1e-5)
    assert short.stress["Q1"]["current_max_a"] > baseline.stress["Q1"]["current_max_a"]+100
    assert abs(short.diagnostics["modeled_power_balance_w"])<.003
    assert short.design_id != baseline.design_id


def test_smaller_coss_changes_actual_commutation_not_just_energy_label(baseline):
    result=calculate_psfb_dynamic(example(),assumptions(coss_f=100e-12))
    # At this fixed dead time the faster resonant transition has already
    # rebounded; smaller Coss alone is not a sufficient ZVS qualification.
    assert result.metrics["Q1_gate_on_vds_v"] > baseline.metrics["Q1_gate_on_vds_v"]+100
    assert result.metrics["Q1_gate_on_residual_coss_energy_j"] > 0.
    assert result.metrics["Q1_zvs_qualified"] == 0.
    assert result.metrics["modeled_channel_loss_w"] != pytest.approx(baseline.metrics["modeled_channel_loss_w"],rel=.1)
    assert result.duty != pytest.approx(baseline.duty,abs=1e-6)
    assert result.design_id != baseline.design_id
    assert abs(result.diagnostics["modeled_power_balance_w"]) < .003
    assert result.waveforms["node_A"].current_a.statistics()["rms"] == 0
    assert max(result.diagnostics[f"{key}_constitutive_relative_residual"]
               for key in ["Coss1","Coss2","Coss3","Coss4","Cprimary","Crectifier","Lleak","Lm","L1"]) < 2e-5


def test_tighter_integration_tolerance_preserves_periodic_load(baseline):
    r=baseline
    initial=np.array([value(r.waveforms["node_A"].voltage_v,0),
                      value(r.waveforms["node_B"].voltage_v,0),
                      value(r.waveforms["Lleak"].current_a,0),
                      value(r.waveforms["Lm"].current_a,0),
                      value(r.waveforms["Cprimary"].voltage_v,0),
                      value(r.waveforms["Crectifier"].voltage_v,0),
                      value(r.waveforms["L1"].current_a,0)])
    c=_Circuit(example(),assumptions(relative_tolerance=1e-9))
    terminal,mean,_=c.integrate(initial,r.duty)
    expected=initial*np.array([-1,-1,-1,-1,-1,1,1])+np.array([400,400,0,0,0,0,0])
    assert terminal == pytest.approx(expected,abs=2e-5)
    assert mean == pytest.approx(5.,abs=2e-6)
    assert 0 < r.diagnostics["fixed_gate_orbit_spectral_radius"] < 1


def test_high_load_qualifies_all_four_actual_transitions():
    result=calculate_psfb_dynamic(example(load=15.),assumptions())
    assert result.metrics["all_switches_zvs_qualified"] == 1.
    for k in range(1,5):
        assert result.metrics[f"Q{k}_gate_on_vds_v"] < 0
        assert result.metrics[f"Q{k}_gate_on_body_current_a"] > 0
        assert result.metrics[f"Q{k}_gate_on_residual_coss_energy_j"] == 0
        assert result.metrics[f"Q{k}_dead_time_s"] == 200e-9
    assert result.waveforms["L1"].current_a.statistics()["mean"] == pytest.approx(15.,abs=1e-6)
    assert abs(result.diagnostics["modeled_power_balance_w"]) < .005
    assert result.diagnostics["fixed_gate_orbit_spectral_radius"] < 1

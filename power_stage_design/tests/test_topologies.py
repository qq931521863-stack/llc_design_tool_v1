"""Focused circuit invariants and exact waveform representation tests."""
import math
from dataclasses import replace

import numpy as np
import pytest

from power_stage_design.models import (
    DesignInputs,
    ModelDomainError,
    UnsupportedTopologyError,
)
from power_stage_design.topologies import calculate_stage
from power_stage_design.waveforms import (
    Waveform,
    WaveformSegment,
    capacitor_voltage,
    piecewise,
)

CASES = (
    DesignInputs("T01", 12, 5, 3, 100e3, 47e-6),
    DesignInputs("T02", 24, 48, 2, 100e3, 100e-6),
    DesignInputs("T09", 48, 12, 1, 100e3, 1e-3, turns_ratio_np_ns=4),
)


@pytest.mark.parametrize("inputs", CASES)
@pytest.mark.parametrize("load_scale", (0, 0.001, 0.03, 1, 10))
@pytest.mark.parametrize("drop", (0, 0.7))
def test_charge_volt_second_and_power_balance(inputs, load_scale, drop):
    result = calculate_stage(replace(inputs, iout_a=inputs.iout_a * load_scale, diode_drop_v=drop, capacitance_f=1e-3))
    assert abs(result.diagnostics["output_capacitor_mean_current_a"]) < 1e-10
    assert abs(result.diagnostics["inductor_mean_voltage_v"]) < 1e-10
    assert abs(result.diagnostics["modeled_power_balance_w"]) < 1e-9
    for device in result.waveforms.values():
        for waveform in (device.current_a, device.voltage_v):
            stats = waveform.statistics()
            assert stats["rms"] + 1e-11 >= abs(stats["mean"])
            assert stats["min"] <= stats["max"]
            t, y = waveform.sample(count=3000)
            assert np.isfinite(y).all()
            assert t[-1] == pytest.approx(1/inputs.fsw_hz)


def test_g01_g02_g03_readable_reference_values():
    buck, boost, flyback = [calculate_stage(case) for case in CASES]
    assert buck.duty == pytest.approx(5/12)
    assert buck.stress["L1"]["current_rms_a"] == pytest.approx(3.0053439053)
    assert buck.stress["Q1"]["current_rms_a"] == pytest.approx(1.9399411491)
    assert buck.stress["D1"]["current_rms_a"] == pytest.approx(2.2953693226)
    assert buck.stress["Cout"]["current_rms_a"] == pytest.approx(0.1791423708)
    assert boost.stress["L1"]["current_mean_a"] == pytest.approx(4)
    assert boost.stress["Cout"]["current_rms_a"] == pytest.approx(2.0149441680)
    assert flyback.stress["Q1"]["current_rms_a"] == pytest.approx(0.3569313660)
    assert flyback.stress["D1"]["current_rms_a"] == pytest.approx(1.4277254638)
    assert flyback.stress["Q1"]["voltage_max_v"] == pytest.approx(96)
    assert flyback.stress["D1"]["voltage_max_v"] == pytest.approx(24)


@pytest.mark.parametrize("inputs", CASES)
def test_dcm_is_resolved_not_clipped(inputs):
    result = calculate_stage(replace(inputs, iout_a=0.001))
    ccm = calculate_stage(inputs)
    assert result.mode == "DCM"
    assert result.duty < ccm.duty
    assert result.metrics["idle_fraction"] > 0
    with pytest.raises(ModelDomainError, match="requires DCM"):
        calculate_stage(replace(inputs, iout_a=0.001, mode="ccm"))
    with pytest.raises(ModelDomainError, match="requires CCM"):
        calculate_stage(replace(inputs, mode="dcm"))


def test_buck_boundary_and_voltage_ripple_integral():
    inputs = CASES[0]
    boundary = 0.5 * (12 - 5) * (5/12) / (47e-6 * 100e3)
    result = calculate_stage(replace(inputs, iout_a=boundary))
    assert result.mode == "BCM"
    assert result.stress["L1"]["current_min_a"] == 0
    assert result.metrics["idle_fraction"] == 0
    expected_ripple = result.metrics["inductor_ripple_pp_a"] / (8 * inputs.fsw_hz * inputs.capacitance_f)
    assert result.metrics["output_ripple_pp_v"] == pytest.approx(expected_ripple)


@pytest.mark.parametrize("field,value", [("vin_v", 0), ("vout_v", -1), ("iout_a", -1), ("fsw_hz", float("nan")), ("inductance_h", float("inf")), ("capacitance_f", 0), ("mode", "fccm"), ("diode_drop_v", -0.1)])
def test_invalid_inputs(field, value):
    with pytest.raises(ModelDomainError):
        calculate_stage(replace(CASES[0], **{field: value}))


def test_unavailable_or_unreachable_is_not_silently_substituted():
    with pytest.raises(ModelDomainError, match="Boost requires"):
        calculate_stage(replace(CASES[1], vout_v=24))
    with pytest.raises(ModelDomainError, match="Buck requires"):
        calculate_stage(replace(CASES[0], vout_v=13))
    with pytest.raises(UnsupportedTopologyError):
        calculate_stage(replace(CASES[0], topology="T20"))


def test_polynomial_exact_extrema_and_rms():
    waveform = Waveform(1e-5, "V", "test", (WaveformSegment(0, 1, (0, 4, -4)),))
    stats = waveform.statistics()
    assert stats["max"] == pytest.approx(1)
    assert stats["mean"] == pytest.approx(2/3)
    assert stats["rms"] == pytest.approx(math.sqrt(8/15))
    current = piecewise(1e-5, (0.5, 0.5), ((-1, 1), (1, -1)), "A", "test")
    voltage = capacitor_voltage(current, 100e-6, 5)
    assert voltage.statistics()["mean"] == pytest.approx(5)
    assert voltage.segments[0].coefficients[0] == pytest.approx(sum(voltage.segments[-1].coefficients))


def test_design_identity_changes_with_input_or_model():
    result = calculate_stage(CASES[0])
    assert result.design_id == calculate_stage(CASES[0]).design_id
    assert result.design_id != calculate_stage(replace(CASES[0], vin_v=13)).design_id
    assert result.to_dict()["inputs"]["vin_v"] == 12


@pytest.mark.parametrize("topology", ("T03","T05","T06","T07","T08","T10","T12","T13","T14","T15","T16","T17"))
@pytest.mark.parametrize("drop", (0,.7))
def test_extended_topology_conservation(topology,drop):
    result=calculate_stage(DesignInputs(topology,48,5,2,100e3,470e-6,capacitance_f=1e-3,
                                       turns_ratio_np_ns=2,diode_drop_v=drop,
                                       secondary_inductance_h=100e-6,transfer_capacitance_f=1e-3,
                                       magnetizing_inductance_h=1e-3))
    for value in result.diagnostics.values():
        assert abs(value)<1e-10
    assert result.stress["Cout"]["current_mean_a"] == pytest.approx(0,abs=1e-12)


@pytest.mark.parametrize("topology", ("T06","T07","T08"))
def test_two_inductor_dcm_preserves_opposed_circulating_currents(topology):
    result=calculate_stage(DesignInputs(topology,48,12,.01,100e3,470e-6,secondary_inductance_h=100e-6,transfer_capacitance_f=1e-3))
    assert result.mode == "DCM"
    i1=result.waveforms["L1"].current_a.segments[-1].coefficients[0]
    i2=result.waveforms["L2"].current_a.segments[-1].coefficients[0]
    assert i1 == pytest.approx(-i2)
    assert abs(i1)>0
    assert result.waveforms["Q1"].current_a.segments[-1].coefficients[0] == 0


@pytest.mark.parametrize("topology",("T13","T14"))
def test_forward_reset_limit_and_diode_loss(topology):
    good=DesignInputs(topology,48,5,2,100e3,470e-6,turns_ratio_np_ns=2,diode_drop_v=.7,magnetizing_inductance_h=1e-3)
    r=calculate_stage(good)
    assert r.metrics["modeled_diode_loss_w"]>good.diode_drop_v*good.iout_a
    with pytest.raises(ModelDomainError,match="reset time"):
        calculate_stage(replace(good,vout_v=18))


def test_series_capacitor_buck_real_four_interval_mapping():
    r=calculate_stage(DesignInputs("T19",12,1.2,10,500e3,1e-6,secondary_inductance_h=1.3e-6,transfer_capacitance_f=20e-6))
    assert r.duty == pytest.approx(.2)
    assert r.stress["L1"]["current_mean_a"] == pytest.approx(5)
    assert r.stress["L2"]["current_mean_a"] == pytest.approx(5)
    assert r.stress["Q1b"]["voltage_max_v"] == 12
    assert r.stress["Q1a"]["voltage_max_v"] == 6
    for value in r.diagnostics.values():
        assert abs(value)<1e-10
    for index in range(len(r.waveforms["Q2a"].current_a.segments)):
        segment=r.waveforms["Q2a"].current_a.segments[index]
        if .5<=segment.start_fraction<.7:
            ia=r.waveforms["L1"].current_a.segments[index].coefficients
            ib=r.waveforms["L2"].current_a.segments[index].coefficients
            assert segment.coefficients == pytest.approx(tuple(x+y for x,y in zip(ia,ib)))


@pytest.mark.parametrize("field,value",[("vin_v","oops"),("vin_v",True),("vin_v",None),("mode",3),("topology",None)])
def test_malformed_json_scalar_types_raise_domain_error(field,value):
    with pytest.raises(ModelDomainError):
        calculate_stage(replace(CASES[0],**{field:value}))


def test_harmonic_polynomial_exact_integral_and_extrema():
    from scipy.integrate import quad
    for angular_span in (.05,math.pi,9*math.pi):
        segment=WaveformSegment(0,1,(.3,-.8,.6),(1.2,-.7,angular_span))
        exact_mean=quad(lambda x,segment=segment:float(segment.evaluate(x)),0,1,epsabs=1e-12)[0]
        exact_square=quad(lambda x,segment=segment:float(segment.evaluate(x))**2,0,1,epsabs=1e-12)[0]
        assert segment.integral() == pytest.approx(exact_mean,abs=1e-12)
        assert segment.integral(square=True) == pytest.approx(exact_square,abs=1e-10)
        low,high=segment.extrema()
        values=segment.evaluate(np.linspace(0,1,100001))
        assert low<=np.min(values)+1e-10
        assert high>=np.max(values)-1e-10
        assert high-np.max(values)<1e-6
    current=Waveform(1e-5,"A","test",(WaveformSegment(0,1,(0,),(2,3,2*math.pi)),))
    voltage=capacitor_voltage(current,1e-6,12)
    assert voltage.statistics()["mean"] == pytest.approx(12)
    assert voltage.segments[0].evaluate(0) == pytest.approx(voltage.segments[0].evaluate(1))


@pytest.mark.parametrize("load",(.1,1,3))
def test_qr_resonant_intervals_and_power_balance(load):
    inputs=DesignInputs("T11",325,12,load,100e3,1e-3,turns_ratio_np_ns=10,diode_drop_v=.7,resonant_capacitance_f=100e-12)
    r=calculate_stage(inputs)
    assert r.mode == "QR-DCM"
    assert r.metrics["actual_switching_frequency_hz"]<=inputs.fsw_hz
    assert r.metrics["turn_on_capacitive_loss_w"]>0
    assert abs(r.diagnostics["modeled_power_balance_w"])<1e-9
    assert abs(r.diagnostics["inductor_mean_voltage_v"])<1e-9
    assert r.waveforms["Q1"].voltage_v.segments[-1].evaluate(1) == pytest.approx(198)
    assert r.stress["D1"]["current_mean_a"] == pytest.approx(load)
    # Compare the exact harmonic statistics with independent Gauss quadrature.
    nodes,weights=np.polynomial.legendre.leggauss(80)
    for device in r.waveforms.values():
        for waveform in (device.current_a,device.voltage_v):
            integral=sum(s.duration_fraction*np.dot(weights,s.evaluate((nodes+1)/2)**2)/2 for s in waveform.segments)
            assert waveform.statistics()["rms"]**2 == pytest.approx(integral,rel=1e-10,abs=1e-10)


def test_qr_accepts_first_zero_clamp_boundary_but_rejects_zero_power():
    x=DesignInputs("T11",48,12,1,100e3,1e-3,turns_ratio_np_ns=4,resonant_capacitance_f=100e-12)
    result=calculate_stage(x)
    assert result.metrics["drain_valley_voltage_v"] == 0
    assert result.provenance["physics_revision"] == "qr-first-zero-clamp-1"
    with pytest.raises(ModelDomainError,match="positive output power"):
        calculate_stage(replace(x,iout_a=0))


def test_psfb_finite_leakage_state_constraints_and_duty_loss():
    inputs=DesignInputs("T18",400,48,5,100e3,100e-6,turns_ratio_np_ns=4,diode_drop_v=.7,magnetizing_inductance_h=1e-3,leakage_inductance_h=20e-6)
    r=calculate_stage(inputs)
    assert r.metrics["effective_transfer_fraction"]<r.duty
    assert r.metrics["circulating_current_rms_a"]>0
    assert abs(r.diagnostics["modeled_power_balance_w"])<1e-8
    for index,segment in enumerate(r.waveforms["primary"].current_a.segments):
        u=np.array([.1,.5,.9])
        ip=segment.evaluate(u)
        im=r.waveforms["Lm"].current_a.segments[index].evaluate(u)
        d1=r.waveforms["D1"].current_a.segments[index].evaluate(u)
        d2=r.waveforms["D2"].current_a.segments[index].evaluate(u)
        il=r.waveforms["L1"].current_a.segments[index].evaluate(u)
        assert ip-im == pytest.approx((d1-d2)/inputs.turns_ratio_np_ns)
        assert d1+d2 == pytest.approx(il)
        slope=segment.coefficients[1]/(segment.duration_fraction/inputs.fsw_hz)
        voltage=r.waveforms["Lleak"].voltage_v.segments[index].evaluate(.5)
        assert slope*inputs.leakage_inductance_h == pytest.approx(voltage,abs=1e-9)


@pytest.mark.parametrize("topology",("T15","T16","T17"))
@pytest.mark.parametrize("load",(.01,.5,2))
def test_bridge_light_load_demagnetization_physical_invariants(topology,load):
    inputs=DesignInputs(topology,400,24,load,100e3,100e-6,turns_ratio_np_ns=4,magnetizing_inductance_h=10e-3,transfer_capacitance_f=100e-6,diode_drop_v=.7)
    r=calculate_stage(inputs)
    for value in r.diagnostics.values():
        assert abs(value)<1e-9
    assert r.stress["D1"]["current_min_a"]>=-1e-10
    assert r.stress["D2"]["current_min_a"]>=-1e-10
    for index,segment in enumerate(r.waveforms["L1"].current_a.segments):
        il=segment.evaluate(.5)
        d1=r.waveforms["D1"].current_a.segments[index].evaluate(.5)
        d2=r.waveforms["D2"].current_a.segments[index].evaluate(.5)
        im=r.waveforms["Lm"].current_a.segments[index].evaluate(.5)
        if topology=="T16":
            ip=r.waveforms["Q1"].current_a.segments[index].evaluate(.5)-r.waveforms["Q2"].current_a.segments[index].evaluate(.5)
        else:
            ip=r.waveforms["primary"].current_a.segments[index].evaluate(.5)
        assert d1+d2 == pytest.approx(il,abs=1e-12)
        assert ip-im == pytest.approx((d1-d2)/inputs.turns_ratio_np_ns,abs=1e-12)
        slope=segment.coefficients[1]/(segment.duration_fraction/inputs.fsw_hz)
        assert slope*inputs.inductance_h == pytest.approx(r.waveforms["L1"].voltage_v.segments[index].evaluate(.5),abs=1e-8)
    if load==.01:
        assert r.mode=="DCM"
        assert r.metrics["idle_fraction"]>0
        assert r.metrics["secondary_demagnetization_fraction"]>0


@pytest.fixture(scope="module")
def fly_buck_result():
    return calculate_stage(DesignInputs("T04",24,5,.2,100e3,47e-6,capacitance_f=100e-6,turns_ratio_np_ns=1,diode_drop_v=.4,leakage_inductance_h=1e-6,primary_capacitance_f=47e-6,primary_output_current_a=.1))


def test_fly_buck_state_solution_is_not_a_flyback_triangle(fly_buck_result):
    r=fly_buck_result
    assert r.mode=="SYNCHRONOUS-PERIODIC"
    assert r.metrics["primary_output_voltage_v"]>5.4
    assert r.stress["primary"]["current_min_a"]<0
    assert r.metrics["primary_output_power_w"]>0
    for value in r.diagnostics.values():
        assert abs(value)<1e-7
    assert r.metrics["input_power_w"] == pytest.approx(r.metrics["isolated_output_power_w"]+r.metrics["primary_output_power_w"]+r.metrics["modeled_diode_loss_w"],abs=1e-7)
    secondary=r.waveforms["secondary"].current_a
    positive_off_slopes=[s.coefficients[1] for s in secondary.segments if s.start_fraction>r.duty]
    assert max(positive_off_slopes)>0
    assert any(abs(c)>1e-12 for s in secondary.segments for c in s.coefficients[2:])


def test_heavy_model_cancellation_is_not_reported_as_domain_failure():
    calls=0
    def cancelled():
        nonlocal calls
        calls+=1
        return calls>4
    x=DesignInputs("T04",24,5,.2,100e3,47e-6,leakage_inductance_h=1e-6,primary_capacitance_f=47e-6,primary_output_current_a=0)
    with pytest.raises(InterruptedError):
        calculate_stage(x,cancelled=cancelled)

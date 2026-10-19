"""Toolbox source checks, independent circuit invariants, domains and real Qt flows.

No TI executable or physical hardware is used. Source-number fixtures are
labelled separately from circuit KCL/energy/integral and nodal-solve checks.
"""
from __future__ import annotations

import csv
import io
import itertools
import json
import math
import os
from dataclasses import replace

import numpy as np
import pytest
from scipy.integrate import solve_ivp

from llc_design.models.devices import DeviceDatabase
from power_stage_design.models import DesignInputs, ModelDomainError
from power_stage_design.toolbox import (
    E_SERIES,
    CapacitorBranch,
    FETOperatingPoint,
    FETParameters,
    FilterInputs,
    acdc_bulk_capacitor,
    analog_scaling,
    capacitor_current_sharing,
    compare_fets,
    convert_units,
    design_divider,
    differential_filter,
    digital_scaling,
    divider_output,
    fet_losses,
    fet_parameters_from_device,
    hold_up_capacitance,
    load_step_capacitance,
    preferred_resistance,
    rc_snubber,
    rcd_snubber,
    stage_import_values,
)
from power_stage_design.topologies import calculate_stage


def example_fet():
    return FETParameters(name="Explicit test fixture", rds_on_ohm=.02, gate_voltage_v=10,
        gate_charge_c=30e-9, coss_f=100e-12, qgs_c=8e-9, qg_threshold_c=3e-9,
        qgd_c=10e-9, gate_resistance_ohm=4, miller_voltage_v=4, threshold_voltage_v=2,
        body_diode_drop_v=.8, temperature_c=100, source="Synthetic explicitly entered test values")


def example_operating():
    return FETOperatingPoint(2, 2, 4, 48, 100e3, "main", 50e-9, 50e-9)


def example_filter(**changes):
    return replace(FilterInputs(4.7e-6, 4.7e-6, 10e-6, 68e-6, .5, 12, 1,
        inductor_dcr_ohm=.001, inductor_srf_hz=16e6, converter_esr_ohm=.005,
        converter_esl_h=4e-9, filter_esr_ohm=.005, filter_esl_h=4e-9,
        damping_esr_ohm=.025, damping_esl_h=7e-9, maximum_frequency_hz=100e6), **changes)


def test_fet_main_terms_and_gate_charge_transition_independent_integration():
    d, op = example_fet(), example_operating()
    result = fet_losses(op, d)
    o = result.outputs
    # The charge-domain current integration has two constant gate-current
    # regions. Integrate dt=dq/Igate, then power with explicit linear V(t).
    q1 = np.linspace(0, 5e-9, 1001)
    q2 = np.linspace(0, 10e-9, 1001)
    tr = np.trapezoid(np.full_like(q1, 1/((10-3)/4)), q1) + np.trapezoid(np.full_like(q2, 1/((10-4)/4)), q2)
    tf = np.trapezoid(np.full_like(q2, 1/(4/4)), q2) + np.trapezoid(np.full_like(q1, 1/(3/4)), q1)
    assert o["rise_time_s"] == pytest.approx(tr, rel=1e-12)
    assert o["fall_time_s"] == pytest.approx(tf, rel=1e-12)
    t_on, t_off = np.linspace(0, tr, 1001), np.linspace(0, tf, 1001)
    energy = np.trapezoid(np.linspace(48,0,1001)*2, t_on) + np.trapezoid(np.linspace(0,48,1001)*4, t_off)
    assert o["switching_overlap_w"] == pytest.approx(energy*100e3, rel=1e-12)
    assert o["conduction_w"] == pytest.approx(.08)
    assert o["output_capacitance_w"] == pytest.approx(.01152)
    assert o["gate_driver_w"] == pytest.approx(.03)
    assert o["baseline_total_w"] == pytest.approx(sum(o[k] for k in ("conduction_w", "switching_overlap_w", "output_capacitance_w")))
    assert o["full_device_loss_w"] is None
    assert "Reverse recovery" in result.warnings[0]


def test_sr_keeps_driver_separate_and_zero_overlap_assumption_explicit():
    result = fet_losses(replace(example_operating(), role="sr"), example_fet())
    o = result.outputs
    assert o["body_diode_w"] == pytest.approx(.8*100e3*50e-9*(2+4))
    assert o["switching_overlap_w"] == 0
    assert o["baseline_total_w"] == pytest.approx(.08+.01152+.024)
    assert "zero switching overlap" in " ".join(result.warnings)


def test_device_library_reuse_preserves_missing_inputs_and_does_not_mix_eoff():
    device = DeviceDatabase(include_user=False).primary[0]
    parameters = fet_parameters_from_device(device, 100)
    assert parameters.rds_on_ohm == device.rds_at(100)
    assert parameters.coss_f == device.coss_er_f
    assert parameters.qgs_c is parameters.qgd_c is parameters.qg_threshold_c is None
    result = fet_losses(example_operating(), parameters)
    assert result.status == "PARTIAL"
    assert result.outputs["switching_overlap_w"] is None
    assert result.outputs["baseline_total_w"] is None
    assert result.outputs["known_subtotal_w"] > 0
    assert "synthetic" in result.inputs["device"]["source"]
    comparison = compare_fets(example_operating(), [parameters, example_fet()])
    assert len(comparison.outputs["devices"]) == 2
    assert comparison.status == "PARTIAL"


@pytest.mark.parametrize("changes", [{"coss_f": 1e-10, "eoss_j": 1e-6}, {"gate_voltage_v": 4},
    {"miller_voltage_v": 2}, {"qg_threshold_c": 9e-9}, {"gate_resistance_ohm": 0}, {"rds_on_ohm": -1}, {"coss_f": float('nan')}])
def test_fet_invalid_data_not_silently_corrected(changes):
    with pytest.raises(ModelDomainError):
        fet_losses(example_operating(), replace(example_fet(), **changes))


def test_eoss_replaces_coss_without_double_counting():
    a = fet_losses(example_operating(), example_fet())
    b = fet_losses(example_operating(), replace(example_fet(), coss_f=None, eoss_j=.5*100e-12*48**2))
    assert a.outputs["baseline_total_w"] == pytest.approx(b.outputs["baseline_total_w"])
    with pytest.raises(ModelDomainError):
        fet_losses(replace(example_operating(), dead_time_on_s=1e-4), example_fet())


def test_load_step_official_example_and_distinct_vmc_branch():
    cmc = load_step_capacitance("CMC", 2.5, .15, 1e4, 60, .05)
    assert cmc.outputs["minimum_capacitance_f"] == pytest.approx(.0015915494309189535, rel=1e-12)
    assert cmc.outputs["phase_factor"] == pytest.approx(1)
    vmc = load_step_capacitance("VMC", 2.5, .15, 1e4, 60, .05, 47e-6)
    assert 0 < vmc.outputs["minimum_capacitance_f"] < cmc.outputs["minimum_capacitance_f"]
    # VMC -> CMC as L -> infinity; finite L must actually change the result.
    limit = load_step_capacitance("VMC", 2.5, .15, 1e4, 60, .05, 1000)
    assert limit.outputs["minimum_capacitance_f"] == pytest.approx(cmc.outputs["minimum_capacitance_f"], rel=1e-7)
    low_pm = load_step_capacitance("CMC", 2.5, .15, 1e4, 30, .05)
    assert low_pm.outputs["minimum_capacitance_f"] > cmc.outputs["minimum_capacitance_f"]
    assert low_pm.warnings


@pytest.mark.parametrize("args", [("CMC",2.5,.15,1e4,60,.06,None), ("CMC",2.5,.15,1e4,0,.01,None),
    ("CMC",2.5,.15,1e4,180,.01,None), ("VMC",2.5,.15,1e4,60,.01,1e-9),
    ("VMC",2.5,.15,1e4,60,.01,None), ("CMC",2.5,.15,1e4,60,.01,47e-6),
    ("CMC",2.5,.15,float('nan'),60,.01,None), ("foo",2.5,.15,1e4,60,.01,None)])
def test_load_step_domains(args):
    with pytest.raises(ModelDomainError):
        load_step_capacitance(*args)


def test_g04_hold_up_energy_and_divider_reference_points():
    hold = hold_up_capacitance(400, 300, 100, .02)
    assert hold.outputs["capacitance_f"] == pytest.approx(57.142857142857e-6, rel=1e-12)
    assert hold.outputs["removed_energy_j"] == 2
    # Independent nonlinear capacitor ODE C dV/dt = -P/V.
    c = hold.outputs["capacitance_f"]
    ode = solve_ivp(lambda _t, y: [-100/(c*y[0])], (0,.02), [400], rtol=1e-10, atol=1e-10)
    assert ode.y[0,-1] == pytest.approx(300, abs=1e-6)
    divider = design_divider(2.5,12,10e3)
    assert divider.outputs["ideal_unknown_ohm"] == pytest.approx(38000)
    assert divider.outputs["output_v"] == pytest.approx(12)
    assert divider.outputs["divider_current_a"] == pytest.approx(.00025)


def test_g04_identical_branches_equal_current_and_three_branch_kcl():
    cap = CapacitorBranch(100e-6,.01,2e-9)
    halves = capacitor_current_sharing([cap, cap], 3, 100e3)
    assert [row["current_rms_a"] for row in halves.outputs["branches"]] == pytest.approx([1.5,1.5])
    branches = [cap, CapacitorBranch(10e-6,.025,7e-9), CapacitorBranch(1e-6,.003,1e-9)]
    result = capacitor_current_sharing(branches, 3, 100e3)
    spectral = result.outputs["spectrum"][0]
    current = [complex(row["current_real_a"], row["current_imag_a"]) for row in spectral["branches"]]
    assert sum(current) == pytest.approx(3+0j)
    # Equal branch terminal voltage from independent V=Z I evaluation.
    voltages = [i*b.impedance(100e3) for i,b in zip(current, branches)]
    assert voltages == pytest.approx([voltages[0]]*3)
    assert spectral["kcl_residual_a"] < 1e-13
    assert sum(abs(i) for i in current) > 3  # magnitudes do NOT satisfy KCL


def test_current_sharing_harmonic_extension_has_actual_spectral_dependence():
    caps = [CapacitorBranch(100e-6,.05,20e-9), CapacitorBranch(1e-6,.003,1e-9)]
    multi = capacitor_current_sharing(caps, 5, 100e3, harmonics=[(100e3,3),(1e6,4)])
    each = [capacitor_current_sharing(caps, i, f) for f,i in [(100e3,3),(1e6,4)]]
    for k in range(2):
        expected = math.hypot(*(r.outputs["branches"][k]["current_rms_a"] for r in each))
        assert multi.outputs["branches"][k]["current_rms_a"] == pytest.approx(expected)
    with pytest.raises(ModelDomainError):
        capacitor_current_sharing(caps, 5, 100e3, harmonics=[(100e3,3),(100e3,4)])
    with pytest.raises(ModelDomainError):
        capacitor_current_sharing(caps, 4, 100e3, harmonics=[(100e3,3),(1e6,4)])


def test_current_sharing_ideal_resonance_and_bad_branches_are_rejected():
    with pytest.raises(ModelDomainError):
        capacitor_current_sharing([CapacitorBranch(-1),CapacitorBranch(1e-6)],1,1e3)
    with pytest.raises(ModelDomainError):
        capacitor_current_sharing([CapacitorBranch(1e-6)],1,1e3)
    f = 1/(2*math.pi)
    with pytest.raises(ModelDomainError):
        capacitor_current_sharing([CapacitorBranch(1,0,1),CapacitorBranch(2,0,2)],1,f)


def test_acdc_official_eq13_and_independent_energy_timing():
    result = acdc_bulk_capacitor(100,85,.2,47)
    o = result.outputs
    assert o["capacitance_f"] == pytest.approx(416.2940854111386e-6, rel=1e-12)
    assert o["bulk_rms_current_eq13_a"] == pytest.approx(2.622526265460834, rel=1e-12)
    assert "2.49" in result.warnings[0]
    assert o["charge_time_s"] + o["discharge_time_s"] == pytest.approx(1/94)
    # At the end of the charge interval the ideal sinusoid is at its crest.
    # Moving backwards tc gives Vmin; energy integral closes during discharge.
    assert o["maximum_voltage_v"]*math.cos(2*math.pi*47*o["charge_time_s"]) == pytest.approx(85)
    c = o["capacitance_f"]
    ode = solve_ivp(lambda _t,y: [-100/(c*y[0])],(0,o["discharge_time_s"]),[o["maximum_voltage_v"]], rtol=1e-11,atol=1e-11)
    assert ode.y[0,-1] == pytest.approx(85,abs=1e-7)
    assert result.tool_id != hold_up_capacitance(400,300,100,.02).tool_id


@pytest.mark.parametrize("r", [0,1,-.1,1.1,float('nan')])
def test_acdc_rejects_nonphysical_ripple(r):
    with pytest.raises(ModelDomainError):
        acdc_bulk_capacitor(100,85,r,47)


def test_rcd_energy_integral_and_rc_ripple():
    result = rcd_snubber(2e-6,3,100e3,100,1.5,.05)
    o = result.outputs
    time = np.linspace(0,o["leakage_discharge_time_s"],10001)
    # Clamp absorbs integral(Vclamp I(t)), not just 0.5 L I² because
    # reflected-output voltage drives additional energy during leakage decay.
    current = 3-(150-100)/2e-6*time
    power = np.trapezoid(150*current,time)*100e3
    assert o["snubber_power_w"] == pytest.approx(power, rel=1e-12)
    assert o["snubber_power_w"] > o["leakage_energy_j"]*100e3
    assert 150**2/o["resistance_ohm"] == pytest.approx(power)
    # First-order small-ripple relation; exact RC droop is ~4.877%, not 5%.
    assert 1-math.exp(-1/(100e3*o["resistance_ohm"]*o["capacitance_f"])) == pytest.approx(.048770575499286,rel=1e-10)
    with pytest.raises(ModelDomainError):
        rcd_snubber(2e-6,3,100e3,100,1)
    with pytest.raises(ModelDomainError):
        rcd_snubber(1,3,100e3,100,1.5)


def test_rc_snubber_recovers_independently_chosen_lc():
    original_c, inductance, extra_c = 100e-12, 250e-9, 300e-12
    f0 = 1/(2*math.pi*math.sqrt(inductance*original_c))
    f1 = 1/(2*math.pi*math.sqrt(inductance*(original_c+extra_c)))
    result = rc_snubber(f0,f1,extra_c,100e3,48)
    o = result.outputs
    assert o["parasitic_capacitance_f"] == pytest.approx(original_c)
    assert o["parasitic_inductance_h"] == pytest.approx(inductance)
    assert o["snubber_resistance_ohm"] == pytest.approx(50)
    assert o["charge_discharge_power_w"] == pytest.approx(.06912)
    assert rc_snubber(f0,f1,extra_c).outputs["charge_discharge_power_w"] is None
    with pytest.raises(ModelDomainError):
        rc_snubber(f0,f0,extra_c)
    with pytest.raises(ModelDomainError):
        rc_snubber(f0,f1,extra_c,100e3)


@pytest.mark.parametrize("series", ["E24","E48","E96"])
@pytest.mark.parametrize("parallel", [False,True])
def test_standard_divider_actual_selected_values_and_tolerance_corners(series,parallel):
    r = design_divider(2.5,12,10e3,series=series,parallel=parallel)
    o = r.outputs
    selected = o["selected_resistors_ohm"]
    assert len(selected) == (2 if parallel else 1)
    norm = 10 if series == "E24" else 100
    for value in selected:
        mantissa = value/10**math.floor(math.log10(value))*norm
        assert min(abs(mantissa-number) for number in E_SERIES[series]) < 1e-8
    high = 1/sum(1/value for value in selected)
    assert o["high_ohm"] == pytest.approx(high)
    assert o["output_v"] == pytest.approx(2.5*(high+1e4)/1e4)
    corners = [vref*(1+rh/rl) for vref,rh,rl in itertools.product((2.475,2.525),(high*.99,high*1.01),(9900,10100))]
    assert o["minimum_output_v"] == pytest.approx(min(corners))
    assert o["maximum_output_v"] == pytest.approx(max(corners))
    assert o["minimum_output_v"] <= o["output_v"] <= o["maximum_output_v"]


def test_divider_reverse_and_impossible_targets():
    r = design_divider(2.5,12,38e3,"high")
    assert r.outputs["low_ohm"] == pytest.approx(1e4)
    assert divider_output(2.5,38e3,10e3,resistor_tolerance=0,reference_tolerance=0).outputs["minimum_output_v"] == pytest.approx(12)
    for args in [(2.5,2.5,1e4),(2.5,2,1e4)]:
        with pytest.raises(ModelDomainError):
            design_divider(*args)
    with pytest.raises(ModelDomainError):
        design_divider(2.5,12,10e3,parallel=True)
    with pytest.raises(ModelDomainError):
        preferred_resistance(1e4,"E12")
    with pytest.raises(ModelDomainError):
        divider_output(2.5,38e3,10e3,resistor_tolerance=1)


def test_analog_scaling_nodal_kcl_at_all_points_and_endpoints():
    r = analog_scaling(5,12,1.25,3.3,100e3)
    r2,r3 = r.outputs["bottom_resistance_ohm"],r.outputs["adjust_resistance_ohm"]
    for row in r.outputs["mapping"]:
        current_sum = (row["output_v"]-1.25)/100e3 + (row["adjust_v"]-1.25)/r3 - 1.25/r2
        assert current_sum == pytest.approx(0,abs=1e-18)
    assert r.outputs["output_at_zero_adjust_v"] == pytest.approx(12)
    assert r.outputs["output_at_maximum_adjust_v"] == pytest.approx(5)
    with pytest.raises(ModelDomainError):
        analog_scaling(5,12,1.25,.1,100e3)


@pytest.mark.parametrize("bits", [1,4,8])
def test_digital_scaling_every_code_reconstructed_from_parallel_resistors(bits):
    r = digital_scaling(5,12,1.25,bits,100e3)
    o = r.outputs
    assert len(o["mapping"]) == 2**bits
    measured = []
    for row in o["mapping"]:
        arms = [o["bottom_resistance_ohm"]]+[value for k,value in enumerate(o["bit_resistances_ohm_lsb_first"]) if row["code"] & (1<<k)]
        bottom = 1/sum(1/value for value in arms)
        voltage = 1.25*(100e3+bottom)/bottom
        assert row["output_v"] == pytest.approx(voltage)
        measured.append(voltage)
    assert measured[0] == pytest.approx(5)
    assert measured[-1] == pytest.approx(12)
    np.testing.assert_allclose(np.diff(measured), o["step_v"],rtol=1e-11)


@pytest.mark.parametrize("bits", [0,-1,1.5,True,17])
def test_digital_scaling_invalid_bit_count(bits):
    with pytest.raises(ModelDomainError):
        digital_scaling(5,12,1.25,bits,100e3)


@pytest.mark.parametrize("category,source,destination,value,expected", [
    ("flux_density","G","mT",1000,100), ("voltage_gain","factor","dB",10,20),
    ("power_gain","factor","dB",10,10), ("length","mil","mm",100,2.54),
    ("mass","oz","g",1,28.349523125), ("airflow","lfm","m/s",100,.508),
    ("pcb_copper","oz/ft2 (nominal)","um",1,35), ("temperature","C","F",20,68),
    ("power","kW","PS (metric hp)",200,271.9243234608), ("torque","N m","lb ft",1000,737.5621492773),
    ("speed","km/h","mph",250,155.3427980593)])
def test_official_unit_categories_and_round_trip(category,source,destination,value,expected):
    result = convert_units(value,category,source,destination)
    converted = result.outputs["converted_value"]
    assert converted == pytest.approx(expected,rel=1e-11)
    assert convert_units(converted,category,destination,source).outputs["converted_value"] == pytest.approx(value,rel=1e-12)


def test_units_preserve_dimension_temperature_and_db_domains():
    with pytest.raises(ModelDomainError):
        convert_units(1,"length","mm","g")
    with pytest.raises(ModelDomainError):
        convert_units(0,"voltage_gain","factor","dB")
    with pytest.raises(ModelDomainError):
        convert_units(-274,"temperature","C","F")
    assert convert_units(-273.15,"temperature","C","K").outputs["converted_value"] == 0
    metric = convert_units(200,"power","kW","PS (metric hp)").outputs["converted_value"]
    mechanical = convert_units(200,"power","kW","hp (mechanical)").outputs["converted_value"]
    assert metric > mechanical


def test_filter_independent_nodal_equations_match_complex_curves():
    i = example_filter(points=101)
    result = differential_filter(i)
    curves = result.outputs["curves"]
    # Independently stamp nodal admittances, including inductor parallel C.
    for index in (0,25,50,75,100):
        f = curves["frequency_hz"][index]; s = 2j*math.pi*f
        def cap(c,r,l,s=s):
            return 1/(r+s*l+1/(s*c))
        yi = cap(i.converter_capacitance_f,i.converter_esr_ohm,i.converter_esl_h)
        yf = cap(i.filter_capacitance_f,i.filter_esr_ohm,i.filter_esl_h)
        yd = cap(i.damping_capacitance_f,i.damping_resistance_ohm+i.damping_esr_ohm,i.damping_esl_h)
        yl = 1/(i.inductor_dcr_ohm+s*i.inductance_h)+s/(i.inductance_h*(2*math.pi*i.inductor_srf_hz)**2)
        expected = {"noise_to_other": np.linalg.solve(np.array([[yl+yf]]),np.array([yl]))[0],
                    "other_to_noise_damped": np.linalg.solve(np.array([[yl+yi+yd]]),np.array([yl]))[0],
                    "other_to_noise_undamped": np.linalg.solve(np.array([[yl+yi]]),np.array([yl]))[0],
                    "output_impedance_undamped": np.linalg.solve(np.array([[yl+yi]]),np.array([1]))[0],
                    "output_impedance_damped": np.linalg.solve(np.array([[yl+yi+yd]]),np.array([1]))[0]}
        for key,value in expected.items():
            got = curves[key+"_magnitude"][index]*np.exp(1j*math.radians(curves[key+"_phase_deg"][index]))
            assert got == pytest.approx(value,rel=1e-10,abs=1e-12)
    assert result.outputs["constant_power_input_resistance_ohm"] == -12
    assert result.outputs["effective_capacitance_f"] > 0
    assert result.outputs["effective_esr_ohm"] > 0


def test_filter_figure13_numbers_and_negative_resistance_stability_limit():
    result = differential_filter(example_filter())
    o = result.outputs
    assert o["required_attenuation_db"] == pytest.approx(18.5218251811136,rel=1e-12)
    # Figure13 displays 57.40 dB attenuation; compare its displayed precision.
    assert o["noise_frequency_checks"]["noise_to_other"]["attenuation_db"] == pytest.approx(57.40,abs=.01)
    assert o["suggested_damping_capacitance_f"] == pytest.approx(40e-6)
    assert o["suggested_damping_total_resistance_ohm"] == pytest.approx(.6855654600401044)
    # Figure δ=.41; our expressly documented RC-resonance selection may differ.
    assert o["damping_factor"] > 0
    no_load = differential_filter(example_filter(input_current_a=0))
    large_load = differential_filter(example_filter(input_current_a=100))
    assert no_load.outputs["constant_power_input_resistance_ohm"] is None
    assert large_load.outputs["damping_factor"] < 0
    assert no_load.outputs["damping_factor"] > o["damping_factor"] > large_load.outputs["damping_factor"]
    assert "Non-positive" in " ".join(large_load.warnings)
    lc = differential_filter(example_filter(configuration="lc"))
    assert lc.outputs["curves"]["noise_to_other_magnitude"] == [1]*401
    assert lc.outputs["curves"]["other_to_noise_damped_magnitude"] == o["curves"]["other_to_noise_damped_magnitude"]


@pytest.mark.parametrize("changes", [{"inductance_h": 0},{"input_voltage_v": 0},{"input_current_a": -1},
    {"minimum_frequency_hz": 1e9},{"points": 15},{"points": True},{"damping_frequency_hz": 0},{"configuration": "unknown"}])
def test_filter_invalid_domain(changes):
    with pytest.raises(ModelDomainError):
        differential_filter(example_filter(**changes))


def test_stage_imports_preserve_on_interval_current_and_reject_false_mappings():
    buck = calculate_stage(DesignInputs("T01",12,5,3,100e3,47e-6))
    values = stage_import_values("X01",buck)
    assert values["turn_on_current_a"] > 0  # min of full switch waveform is zero, which is wrong here.
    assert values["rms_current_a"] == buck.stress["Q1"]["current_rms_a"]
    assert values["turn_off_current_a"] == buck.stress["Q1"]["current_max_a"]
    assert stage_import_values("X02",buck) == {"inductance_h":47e-6}
    assert stage_import_values("X12",buck)["input_current_a"] == pytest.approx(1.25)
    fly = calculate_stage(DesignInputs("T09",48,12,1,100e3,1e-3,turns_ratio_np_ns=4))
    assert stage_import_values("X05",fly)["reflected_voltage_v"] == 48
    for tool,stage,kwargs in [("X05",buck,{}),("X01",buck,{"role":"sr"}),("X02",fly,{}),("X03",buck,{}),("X04",buck,{})]:
        with pytest.raises(ModelDomainError):
            stage_import_values(tool,stage,**kwargs)
    with pytest.raises(ModelDomainError):
        stage_import_values("X01",replace(buck,design_id="wrong"))


def test_result_reproducible_export_unknowns_and_all_sources(tmp_path):
    r = fet_losses(example_operating(),FETParameters())
    assert isinstance(r.assumptions,tuple)
    for ext in ("json","csv","txt"):
        path = tmp_path/("result."+ext)
        r.write(path)
        assert path.exists()
        text = path.read_text(encoding="utf-8")
        assert r.result_id in text
        assert "slvubb4b" in text
        if ext == "json":
            assert json.loads(text)["outputs"]["baseline_total_w"] is None
        if ext == "csv":
            rows = list(csv.DictReader(io.StringIO(text)))
            total = next(row for row in rows if row["parameter"] == "baseline_total_w")
            assert total["value"] == "UNKNOWN" and total["unit"] == "W"
    with pytest.raises(ValueError):
        r.write(tmp_path/"result.html")
    r2 = fet_losses(replace(example_operating(),frequency_hz=120e3),FETParameters())
    assert r2.result_id != r.result_id


# Real Qt form/workflow coverage. No mock calculator or screenshot fabrication.
os.environ.setdefault("QT_QPA_PLATFORM","offscreen")


@pytest.fixture(scope="module")
def qt_app():
    qt = pytest.importorskip("PySide6.QtWidgets")
    application = qt.QApplication.instance() or qt.QApplication([])
    yield application
    application.processEvents()


@pytest.fixture
def widget(qt_app):
    from PySide6.QtCore import QCoreApplication, QEvent

    from power_stage_design.toolbox_gui import ToolBoxWidget
    view = ToolBoxWidget(); view.resize(1300,850); view.show(); qt_app.processEvents()
    yield view
    view.close(); view.deleteLater()
    QCoreApplication.sendPostedEvents(None,QEvent.Type.DeferredDelete); qt_app.processEvents()


@pytest.mark.parametrize("tool_id", ["X01","X02","X03","X04","X05","X06","X07","X08","X09","X10","X12","G04"])
def test_each_real_tool_form_calculates_exports_and_recomputes_snapshot(widget,tool_id,tmp_path):
    widget.select_tool(tool_id)
    result = widget.calculate()
    assert result is not None,widget.status.text()
    assert widget.export_button.isEnabled()
    saved_id = result.result_id
    state = widget.state()
    for suffix in (".json",".csv",".txt"):
        path = tmp_path/(tool_id+suffix)
        widget.export_to(path)
        assert saved_id in path.read_text(encoding="utf-8")
    widget.select_tool("X07" if tool_id != "X07" else "X04")
    assert widget.latest_result is None
    widget.restore_state(json.loads(json.dumps(state)))
    assert widget.latest_result.result_id == saved_id
    assert widget.current_tool_id == tool_id


def test_real_gui_edits_failures_and_stale_stage_import_block_exports(widget,tmp_path):
    buck = calculate_stage(DesignInputs("T01",12,5,3,100e3,47e-6))
    widget.set_stage_result(buck); widget.select_tool("X01")
    widget.import_stage(); r=widget.calculate()
    assert r.inputs["stage_import"]["design_id"] == buck.design_id
    widget.set_stage_result(None)
    assert widget.latest_result is None
    assert not widget.export_button.isEnabled()
    assert widget.calculate() is None
    assert "stale" in widget.status.text()
    with pytest.raises(ValueError):
        widget.export_to(tmp_path/"stale.json")
    assert not (tmp_path/"stale.json").exists()
    widget.set_stage_result(buck);widget.import_stage();assert widget.calculate()
    widget.input_widgets["drain_voltage_v"].setText("nan")
    assert widget.latest_result is None
    assert widget.calculate() is None
    assert not widget.export_button.isEnabled()
    widget.input_widgets["drain_voltage_v"].setText("48")
    r=widget.calculate()
    assert r and "stage_import" not in r.inputs  # explicitly independent after manual edit
    widget.set_stage_result(None)
    assert widget.latest_result is r


def test_real_gui_library_missing_fields_never_inherit_manual_demo_values(widget):
    widget.select_tool("X01")
    page=widget.pages["X01"]
    page.load_device("a",widget.database.primary[0])
    assert page.widgets["a_qgs_c"].text() == ""
    assert page.widgets["a_qgd_c"].text() == ""
    r=widget.calculate()
    assert r.status == "PARTIAL"
    assert r.outputs["baseline_total_w"] is None
    widget.set_values({"compare":True})
    r=widget.calculate()
    assert len(r.outputs["devices"]) == 2
    assert r.outputs["devices"][0]["baseline_total_w"] is None
    assert r.outputs["devices"][1]["baseline_total_w"] > 0


def test_real_gui_invalid_restore_leaves_existing_result_unchanged(widget):
    widget.select_tool("X04");result=widget.calculate()
    state=widget.state();state["values"]["ripple_fraction"]=-.2
    with pytest.raises(ValueError):
        widget.restore_state(state)
    assert widget.latest_result is result
    assert widget.current_tool_id == "X04"
    assert widget.pages["X04"].values()["ripple_fraction"] == .2


def test_real_gui_unit_categories_and_filter_plots_consume_exact_result(widget,qt_app,tmp_path):
    widget.select_tool("X10")
    widget.set_values({"category":"power_gain","from_unit":"factor","to_unit":"dB","value":10})
    assert widget.calculate().outputs["converted_value"] == 10
    widget.select_tool("X12");r=widget.calculate();curves=r.outputs["curves"]
    assert len(widget.response_plot.listDataItems()) == 3
    assert len(widget.impedance_plot.listDataItems()) == 2
    # pyqtgraph stores original arrays even when displayed on logarithmic axes.
    plot=widget.response_plot.listDataItems()[0]
    np.testing.assert_array_equal(plot.xData,curves["frequency_hz"])
    np.testing.assert_array_equal(plot.yData,curves["noise_to_other_db"])
    qt_app.processEvents()
    evidence=os.environ.get("PSD_EVIDENCE_DIR")
    if evidence:
        from pathlib import Path
        path=Path(evidence);path.mkdir(parents=True,exist_ok=True)
        assert widget.grab().save(str(path/"toolbox-filter.png"))
        widget.export_to(path/"toolbox-filter.json")
        widget.select_tool("X09");widget.calculate();qt_app.processEvents()
        assert widget.grab().save(str(path/"toolbox-digital-scaling.png"))
        widget.export_to(path/"toolbox-digital-scaling.json")


def _wait_until(app, predicate, timeout_ms=5000):
    from PySide6.QtTest import QTest
    for _ in range(timeout_ms//10):
        app.processEvents()
        if predicate():
            return
        QTest.qWait(10)
    assert predicate(), "Timed out waiting for bounded calculator worker"


def test_large_map_runs_async_and_old_generation_cannot_overwrite_newer_tool(widget,qt_app):
    widget.select_tool("X09");widget.set_values({"bits":16})
    assert widget.calculate() is None
    assert widget._pending and widget.cancel_button.isEnabled()
    widget.select_tool("X04")
    current=widget.calculate()
    _wait_until(qt_app,lambda: not widget._workers)
    assert widget.latest_result is current
    assert widget.latest_result.tool_id == "X04"
    widget.select_tool("X09");widget.calculate()
    _wait_until(qt_app,lambda: widget.latest_result is not None)
    assert len(widget.latest_result.outputs["mapping"]) == 65536
    assert widget.latest_result.outputs["mapping"][-1]["output_v"] == pytest.approx(12)
    widget.calculate();widget.cancel_pending()
    _wait_until(qt_app,lambda: not widget._workers)
    assert widget.latest_result is None
    assert not widget.export_button.isEnabled()


def test_saved_inputs_file_replays_exact_scaled_si_values(widget,tmp_path):
    widget.select_tool("X06")
    values={"ringing_frequency_hz":30123456.789123456,"loaded_frequency_hz":15234567.891234567,
            "added_capacitance_f":3.123456789123456e-10}
    widget.set_values(values)
    result=widget.calculate()
    assert result is not None
    for key,value in values.items():
        assert result.inputs[key] == value
    path=tmp_path/"inputs.json"
    widget.save_inputs_to(path)
    widget.select_tool("X04");widget.calculate()
    widget.open_inputs_from(path)
    assert widget.latest_result.result_id == result.result_id
    for key,value in values.items():
        assert widget.latest_result.inputs[key] == value
    old=widget.latest_result
    document=json.loads(path.read_text(encoding="utf-8"));document["state"]["values"]["added_capacitance_f"]=-1
    path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(ValueError):
        widget.open_inputs_from(path)
    assert widget.latest_result is old


@pytest.mark.parametrize("topology",["T05","T06","T07","T08","T10"])
def test_additional_hard_switch_stage_imports_have_exact_conduction_interval(topology):
    if topology == "T10":
        inputs=DesignInputs(topology,48,12,1,100e3,1e-3,turns_ratio_np_ns=3)
    else:
        inputs=DesignInputs(topology,24,12,2,100e3,100e-6,secondary_inductance_h=100e-6,transfer_capacitance_f=100e-6)
    stage=calculate_stage(inputs)
    mapped=stage_import_values("X01",stage)
    assert mapped["turn_off_current_a"] == pytest.approx(stage.stress["Q1"]["current_max_a"])
    assert mapped["turn_on_current_a"] > 0
    assert mapped["drain_voltage_v"] == stage.stress["Q1"]["voltage_max_v"]
    assert mapped["rms_current_a"] == stage.stress["Q1"]["current_rms_a"]


def test_saved_choice_boolean_cannot_be_coerced_into_numeric_bit_count(widget):
    widget.select_tool("X09");r=widget.calculate()
    state=widget.state();state["values"]["bits"]=True
    with pytest.raises(ValueError):
        widget.restore_state(state)
    assert widget.latest_result is r


def test_close_during_async_map_drains_own_pool_and_reopen_is_fresh(qt_app):
    from PySide6.QtCore import QCoreApplication, QEvent

    from power_stage_design.toolbox_gui import ToolBoxWidget
    first=ToolBoxWidget();first.select_tool("X09");first.set_values({"bits":16});first.calculate()
    assert first._pending
    assert first.close()
    assert first._pool.activeThreadCount() == 0
    assert first.latest_result is None
    first.deleteLater();QCoreApplication.sendPostedEvents(None,QEvent.Type.DeferredDelete);qt_app.processEvents()
    second=ToolBoxWidget()
    try:
        assert second.latest_result is None and not second._pending
        assert second.current_tool_id == "X01"
        assert second.calculate() is not None
    finally:
        second.close();second.deleteLater();QCoreApplication.sendPostedEvents(None,QEvent.Type.DeferredDelete);qt_app.processEvents()


def test_dense_filter_runs_async_and_cancellation_does_not_publish(widget,qt_app):
    widget.select_tool("X12");widget.set_values({"points":4001})
    assert widget.calculate() is None
    assert widget._pending
    _wait_until(qt_app,lambda: widget.latest_result is not None)
    assert len(widget.latest_result.outputs["curves"]["frequency_hz"]) == 4001
    widget.calculate();widget.cancel_pending()
    _wait_until(qt_app,lambda: not widget._workers)
    assert widget.latest_result is None


def test_same_toolbox_can_be_shown_again_after_close(widget,qt_app):
    widget.select_tool("X12");assert widget.calculate()
    assert widget.close()
    widget.show();qt_app.processEvents()
    result=widget.calculate()
    assert result is not None
    assert len(widget.response_plot.listDataItems()) == 3
    assert len(widget.phase_plot.listDataItems()) == 3
    assert len(widget.impedance_plot.listDataItems()) == 2


def test_stop_background_work_preserves_visible_tab(widget,qt_app):
    widget.select_tool("X09");widget.set_values({"bits":16});widget.calculate()
    assert not widget.isHidden()
    assert widget.stop_background_work()
    assert not widget.isHidden()
    assert widget._pool.activeThreadCount() == 0
    qt_app.processEvents()
    assert widget.latest_result is None and not widget._pending
    widget.set_values({"bits":4})
    assert widget.calculate() is not None

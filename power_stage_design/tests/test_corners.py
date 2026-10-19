from dataclasses import asdict

import pytest

from power_stage_design.corners import CornerRequest, analyze_corners
from power_stage_design.loop import Compensator, LoopPlant, analyze_loop
from power_stage_design.models import DesignInputs


def test_per_metric_extrema_retain_different_physical_corners():
    inputs=DesignInputs('T01',12,5,3,100e3,47e-6)
    result=analyze_corners(inputs,CornerRequest((8,12,16),(.5,3)))
    voltage=result.extrema['Q1.voltage_max_v']['maximum']
    duty=result.extrema['duty']['maximum']
    assert voltage['value']==16
    assert result.points[voltage['point_index']].inputs.vin_v==16
    assert result.points[duty['point_index']].inputs.vin_v==8
    assert voltage['design_id']!=duty['design_id']
    assert len(result.points)==6


def test_unusable_corners_and_no_loop_margin_are_not_false_zero():
    inputs=DesignInputs('T01',12,5,3,100e3,47e-6)
    settings={'plant':asdict(LoopPlant()),'compensator':asdict(Compensator())}
    result=analyze_corners(inputs,CornerRequest((4,12),(.01,3)),loop_settings=settings)
    assert len([p for p in result.points if p.error])==2
    assert result.points[2].mode=='DCM' and result.points[2].loop_error
    assert 'loop.phase_margin_deg' not in result.points[2].metrics
    assert result.extrema['loop.phase_margin_deg']['evaluated_points']==1
    assert 'total_loss_w' not in result.extrema
    standalone=analyze_loop(LoopPlant(),Compensator())
    assert result.points[3].metrics['loop.phase_margin_deg']==pytest.approx(standalone.stability.worst_phase_margin_deg)
    assert result.controller==asdict(Compensator())


def test_explicit_tolerances_are_not_hidden_voltage_scans():
    inputs=DesignInputs('T01',12,5,3,100e3,47e-6)
    result=analyze_corners(inputs,CornerRequest((12,),(3,),(.8,1.2),(.5,1)))
    assert len(result.points)==4
    assert {p.inputs.inductance_h for p in result.points}=={47e-6*.8,47e-6*1.2}
    assert {p.inputs.capacitance_f for p in result.points}=={50e-6,100e-6}
    assert result.extrema['output_ripple_pp_v']['maximum']['value']>result.extrema['output_ripple_pp_v']['minimum']['value']


def test_sweep_cancel_and_scope_guard():
    inputs=DesignInputs('T01',12,5,3,100e3,47e-6)
    with pytest.raises(InterruptedError):analyze_corners(inputs,CornerRequest((12,),(3,)),cancelled=lambda:True)
    for request in [CornerRequest((),(3,)),CornerRequest((float('nan'),),(3,)),CornerRequest(tuple(range(1,502)),(3,))]:
        with pytest.raises(ValueError):analyze_corners(inputs,request)

"""A report cannot silently compare nominal/corners at different junction temperatures."""
from dataclasses import asdict, replace
from html import unescape
import json

import pytest

from llc_design.models.devices import DeviceDatabase
from power_stage_design.corners import CornerRequest, analyze_corners
from power_stage_design.devices import assess_component
from power_stage_design.models import DesignInputs
from power_stage_design.report import write_report
from power_stage_design.topologies import calculate_stage


def _case(temperature=125.0):
    inputs = DesignInputs('T01', 12, 5, 3, 100e3, 47e-6)
    device = DeviceDatabase().primary[0]
    result = calculate_stage(inputs)
    sweep = analyze_corners(inputs, CornerRequest((12.0,), (3.0,)),
                            selected_device=device, junction_temperature_c=temperature)
    return result, device, sweep


def _snapshot(path):
    text = path.read_text(encoding='utf-8')
    return json.loads(unescape(text.split('<h2>可复现数据</h2><pre>', 1)[1].split('</pre>', 1)[0]))


def test_report_uses_corner_temperature_for_all_device_consumers(tmp_path):
    result, device, sweep = _case()
    path = write_report(result, tmp_path/'same.html', selected_device=asdict(device), corner_sweep=sweep)
    snapshot = _snapshot(path)
    expected = sweep.points[0].metrics['Q1.known_conduction_loss_w']
    assert snapshot['junction_temperature_c'] == 125.0
    assert snapshot['device_assessment']['known_conduction_loss_w'] == pytest.approx(expected, rel=1e-14)
    assert snapshot['component_assessments']['Q1'] == snapshot['device_assessment']
    assert snapshot['device_assessment']['temperature_c'] == 125.0
    assert expected != pytest.approx(assess_component(result, 'Q1', selected_main_device=device)['known_conduction_loss_w'])
    assert snapshot['device_assessment']['total_loss_w'] is None


def test_explicit_temperature_mismatch_rejects_before_touching_destination(tmp_path):
    result, device, sweep = _case()
    path = tmp_path/'history.html'
    path.write_text('retained historical file', encoding='utf-8')
    with pytest.raises(ValueError, match='temperature'):
        write_report(result, path, selected_device=asdict(device), corner_sweep=sweep, junction_temperature_c=100)
    assert path.read_text(encoding='utf-8') == 'retained historical file'


def test_temperature_is_exported_without_a_corner_scan(tmp_path):
    result, device, _ = _case()
    current = assess_component(result, 'Q1', selected_main_device=device, temperature_c=75)
    path = write_report(result, tmp_path/'current.html', selected_device=asdict(device),
                        device_assessment=current, junction_temperature_c=75)
    assert _snapshot(path)['device_assessment'] == current
    with pytest.raises(ValueError, match='different design'):
        write_report(result, tmp_path/'stale.html', selected_device=asdict(device),
                     device_assessment=current, junction_temperature_c=125)
    assert not (tmp_path/'stale.html').exists()


@pytest.mark.parametrize('temperature', [float('nan'), float('inf'), -41, 176, True, '125'])
def test_invalid_temperature_never_produces_report(tmp_path, temperature):
    result, device, sweep = _case()
    with pytest.raises(ValueError, match='temperature'):
        write_report(result, tmp_path/'invalid.html', selected_device=asdict(device),
                     corner_sweep=replace(sweep, junction_temperature_c=temperature))
    assert not (tmp_path/'invalid.html').exists()

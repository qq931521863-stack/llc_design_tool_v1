from dataclasses import replace
from pathlib import Path

import pytest

from llc_design.models.devices import DeviceDatabase
from power_stage_design.devices import assess_component
from power_stage_design.models import DesignInputs
from power_stage_design.report import write_report
from power_stage_design.topologies import calculate_stage


def test_report_utf8_is_independent_of_windows_default_encoding(tmp_path, monkeypatch):
    original_open = Path.open

    def windows_open(path, mode="r", buffering=-1, encoding=None, errors=None, newline=None):
        if "b" not in mode and encoding in (None, "locale"):
            encoding = "cp1252"
        return original_open(path, mode, buffering, encoding, errors, newline)

    monkeypatch.setattr(Path, "open", windows_open)
    result = calculate_stage(DesignInputs("T01", 12, 5, 3, 100e3, 47e-6))
    report = write_report(result, tmp_path / "report.html")
    assert "软件稳态模型结果" in report.read_text(encoding="utf-8")
    with pytest.raises(UnicodeDecodeError):
        report.read_text(encoding="cp1252")


def test_component_loss_keeps_unknown_terms_and_design_identity(tmp_path):
    result=calculate_stage(DesignInputs('T01',12,5,3,100e3,47e-6))
    device=DeviceDatabase().primary[0]
    evidence=assess_component(result,'Q1',selected_main_device=device)
    assert evidence['design_id']==result.design_id
    assert evidence['known_conduction_loss_w']>0
    assert evidence['total_loss_w'] is None
    assert evidence['thermal_status']=='UNKNOWN'
    other=assess_component(result,'Cout',selected_main_device=device)
    assert other['known_conduction_loss_w'] is None
    assert other['component']=='Cout'
    from dataclasses import asdict
    with pytest.raises(ValueError,match='different design'):
        write_report(result,tmp_path/'bad.html',selected_device=asdict(device),device_assessment=other)
    assert not (tmp_path/'bad.html').exists()
    write_report(result,tmp_path/'good.html',selected_device=asdict(device),device_assessment=evidence)
    assert 'known_conduction_loss_w' in (tmp_path/'good.html').read_text(encoding="utf-8")


def test_missing_rds_stays_unknown_instead_of_zero_loss():
    result=calculate_stage(DesignInputs('T01',12,5,3,100e3,47e-6))
    device=replace(DeviceDatabase().primary[0],rds_on_25_ohm=0,rds_on_hot_ohm=0)
    evidence=assess_component(result,'Q1',selected_main_device=device)
    assert evidence['known_conduction_loss_w'] is None
    assert evidence['total_loss_w'] is None


def test_model_revision_migration_is_explicit_and_limited_to_verified_m1_kernels(tmp_path):
    import json

    from power_stage_design import MODEL_REVISION
    from power_stage_design.project import read_project, save_project
    path=tmp_path/'old.json'
    save_project(DesignInputs('T01',12,5,3,100e3,47e-6),path)
    data=json.loads(path.read_text(encoding="utf-8"));data['model_revision']='psd-piecewise-1'
    path.write_text(json.dumps(data), encoding="utf-8")
    inputs,restored=read_project(path)
    assert restored['restored_from_model_revision']=='psd-piecewise-1'
    assert restored['model_revision']==MODEL_REVISION
    assert inputs.topology=='T01'
    data['inputs']['topology']='T04';path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError,match='incompatible'):
        read_project(path)


def test_missing_loss_parameter_does_not_hide_known_rating_violations():
    result=calculate_stage(DesignInputs('T01',12,5,3,100e3,47e-6))
    device=replace(DeviceDatabase().primary[0],rds_on_25_ohm=0,rds_on_hot_ohm=0,
                   vds_max_v=6,id_cont_a=1)
    evidence=assess_component(result,'Q1',selected_main_device=device)
    assert evidence['known_conduction_loss_w'] is None
    assert len(evidence['rating_violations'])==2
    assert '超过额定' in evidence['assessment']

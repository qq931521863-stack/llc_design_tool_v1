"""Real current-result, atomic restore, aliases and synthesis-intent consumers."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import asdict, replace

import numpy as np
import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtCore import QCoreApplication, QEvent, QEventLoop, QTimer
from PySide6.QtWidgets import QApplication

from power_stage_design.gui import PRESETS, PowerStageMainWindow
from power_stage_design.loop import Compensator, LoopPlant, analyze_loop
from power_stage_design.project import (
    project_document,
    read_project,
    save_project,
    validate_loop_request,
    write_json,
)


def pump(milliseconds=300):
    event_loop=QEventLoop()
    QTimer.singleShot(milliseconds,event_loop.quit)
    event_loop.exec()


@pytest.fixture
def desktop():
    app=QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    view=PowerStageMainWindow();view.show();app.processEvents()
    yield view
    view.close();view.deleteLater()
    QCoreApplication.sendPostedEvents(None,QEvent.Type.DeferredDelete)
    app.processEvents()


def sparse_settings():
    return {'plant':{},'compensator':{}}


def test_sparse_defaults_are_identical_validated_write_read_and_real_open(desktop,tmp_path):
    source=PRESETS['T01']
    assert (source.vin_v,source.vout_v,source.iout_a,source.inductance_h)==(12,5,3,47e-6)
    document=project_document(source,loop_settings=sparse_settings())
    expected={'plant':asdict(LoopPlant()),'compensator':asdict(Compensator())}
    assert document['loop_settings']==expected
    # Old schema-1 files with absent default fields remain readable atomically.
    document['loop_settings']=sparse_settings()
    path=write_json(document,tmp_path/'sparse.json')
    assert read_project(path)[1]['loop_settings']==expected
    desktop.set_inputs(PRESETS['T02'])
    desktop._start_project_open(path)
    pump(500)
    assert desktop.result.inputs==source
    assert desktop._loop_settings==expected
    assert not desktop._jobs
    np.testing.assert_array_equal(desktop.loop_result.loop_response,
        analyze_loop(LoopPlant(),Compensator()).loop_response)


@pytest.mark.parametrize('corruption',['plant','request','topology','mode'])
def test_invalid_open_callback_keeps_previous_stage_and_loop(desktop,tmp_path,corruption):
    desktop.analyze_loop();old=desktop.result;loop=desktop.loop_result
    doc=project_document(PRESETS['T02'])
    if corruption=='plant':doc['loop_settings']={'plant':{'unknown':1},'compensator':{}}
    elif corruption=='request':doc['loop_request']={'target_crossover_hz':-3,'target_phase_margin_deg':60}
    elif corruption=='topology':doc['inputs']['topology']='not-a-topology'
    else:doc['inputs']['mode']='not-a-mode'
    path=write_json(doc,tmp_path/'invalid.json')
    desktop._start_project_open(path);pump(350)
    assert desktop.result is old and desktop.loop_result is loop
    assert '当前设计保留' in desktop.status.text()
    assert not desktop._jobs


@pytest.mark.parametrize('name,mode',[('Buck','CCM'),('t01','Auto'),('buck','ccm')])
def test_accepted_aliases_preserve_exact_input_identity_in_desktop_report(desktop,tmp_path,name,mode):
    inputs=replace(PRESETS['T01'],topology=name,mode=mode)
    path=save_project(inputs,tmp_path/'alias.json')
    desktop.open_from(path)
    assert desktop.topology_combo.currentData()=='T01'
    assert desktop.mode_combo.currentData()==mode.casefold()
    assert desktop.current_inputs()==inputs
    assert desktop.result.design_id==inputs.design_id
    desktop.analyze_loop();assert desktop.loop_result is not None
    saved=desktop.save_to(tmp_path/'again.json')
    assert read_project(saved)[0]==inputs
    report=desktop.report_to(tmp_path/'alias.html').read_text(encoding='utf-8')
    assert inputs.design_id in report
    desktop.mode_combo.setCurrentIndex(desktop.mode_combo.findData('dcm'))
    assert desktop.current_inputs().mode=='dcm'


def test_fast_loop_changes_debounce_current_arrays_stage_link_and_invalid_recovery(desktop):
    desktop.analyze_loop();old=desktop.loop_result
    for esr in (.03,.04,.057):
        desktop.loop_widgets['esr_ohm'].setValue(esr)
        assert desktop.loop_result is None and not desktop.loop_gain_plot.listDataItems()
    pump()
    assert desktop.loop_result is not None and desktop.loop_result is not old
    plant,comp=desktop.loop_parameters()
    expected=analyze_loop(plant,comp)
    np.testing.assert_array_equal(desktop.loop_result.loop_response,expected.loop_response)
    assert plant.esr_ohm==.057
    desktop.input_widgets['vin_v'].setValue(13.25)
    assert desktop.loop_result is None
    pump(450)
    assert desktop._loop_settings['plant']['vin_v']==13.25
    desktop.ota_output_resistance.setText('incomplete-')
    assert desktop.loop_result is None
    pump()
    assert desktop.loop_result is None and '有效结果' in desktop.loop_status.text()
    desktop.ota_output_resistance.clear();pump()
    assert desktop.loop_result is not None
    generation=desktop._loop_generation;pump()
    assert desktop._loop_generation==generation  # no recursive rescheduling
    desktop.comp_widgets['r2_ohm'].setValue(17)
    assert desktop._loop_timer.isActive()
    desktop.close();assert not desktop._loop_timer.isActive()


def test_synthesis_intent_exact_restore_report_cli_and_legacy_reset(desktop,tmp_path):
    request={'target_crossover_hz':3500.123456789123,'target_phase_margin_deg':67.123456789123,
             'preferred_series':12}
    document=project_document(PRESETS['T01'],loop_settings=sparse_settings(),loop_request=request)
    path=write_json(document,tmp_path/'intent.json')
    desktop.open_from(path)
    assert desktop._current_loop_request()==request
    assert desktop.preferred_series.currentData()==12
    accepted=desktop.loop_result
    desktop.target_fc.setValue(3750)
    assert desktop.loop_result is accepted  # request alone is not an actual R/C edit
    request['target_crossover_hz']=3750
    assert desktop._current_loop_request()==request
    saved=desktop.save_to(tmp_path/'again.json')
    assert read_project(saved)[1]['loop_request']==request
    report=desktop.report_to(tmp_path/'intent.html').read_text(encoding='utf-8')
    assert '搜索意图' in report and '3750' in report and '67.123456789123' in report
    cli=tmp_path/'cli.html'
    completed=subprocess.run([sys.executable,'-m','power_stage_design',str(saved),'--report',str(cli)],
        capture_output=True,text=True,timeout=30,check=False)
    assert completed.returncode==0,completed.stderr
    assert '67.123456789123' in cli.read_text(encoding='utf-8')
    legacy=project_document(PRESETS['T01']);legacy.pop('loop_request')
    desktop.open_from(write_json(legacy,tmp_path/'legacy.json'))
    assert desktop._current_loop_request()=={'target_crossover_hz':3000,'target_phase_margin_deg':60,'preferred_series':24}
    assert desktop.loop_result is None


@pytest.mark.parametrize('intent',[
    {'target_crossover_hz':True,'target_phase_margin_deg':60},
    {'target_crossover_hz':3000,'target_phase_margin_deg':180},
    {'target_crossover_hz':3000,'target_phase_margin_deg':60,'preferred_series':True},
    {'target_crossover_hz':3000,'target_phase_margin_deg':60,'preferred_series':96},
    {'target_crossover_hz':3000,'target_phase_margin_deg':60,'extra':1},
])
def test_invalid_synthesis_intent_is_rejected(intent):
    with pytest.raises(ValueError):validate_loop_request(intent)


@pytest.mark.parametrize('group,key,value',[
    ('plant','sense_gain','oops'),('plant','slope_mode','unknown'),
    ('plant','sense_ohm',True),('compensator','gm_s','oops'),
    ('compensator','ctr',None),('compensator','opto_pole_hz',float('inf')),
])
def test_inactive_loop_fields_are_validated_before_ui_mutation(desktop,tmp_path,group,key,value):
    desktop.analyze_loop();old=desktop.result;old_loop=desktop.loop_result
    doc=project_document(PRESETS['T01'],loop_settings=sparse_settings())
    # Valid but different power stage/plant proves rejection precedes replacement.
    doc['inputs']['vin_v']=24;doc['loop_settings']['plant']['vin_v']=24
    doc['loop_settings'][group][key]=value
    path=tmp_path/'inactive.json'
    path.write_text(json.dumps(doc),encoding='utf-8')
    with pytest.raises(ValueError):desktop.open_from(path)
    assert desktop.result is old and desktop.loop_result is old_loop
    desktop._start_project_open(path);pump(350)
    assert desktop.result is old and desktop.loop_result is old_loop
    assert '当前设计保留' in desktop.status.text()


def test_sparse_suggestion_defaults_restore_without_partial_mutation(desktop,tmp_path):
    from power_stage_design.suggestions import SuggestionTargets, suggest_parameters
    source=replace(PRESETS['T01'],vin_v=24)
    proposal=suggest_parameters(source,SuggestionTargets(.9))
    record=proposal.record();record['targets']={'ripple_pp_a':.9}
    doc=project_document(proposal.inputs,parameter_suggestion=record)
    assert doc['parameter_suggestion']['targets']==asdict(proposal.targets)
    doc['parameter_suggestion']['targets']={'ripple_pp_a':.9}
    path=write_json(doc,tmp_path/'sparse-suggestion.json')
    desktop.open_from(path)
    assert desktop.result.inputs==proposal.inputs
    assert desktop._applied_suggestion['targets']==asdict(proposal.targets)
    assert desktop.suggestion_duty.value()==.45
    assert 'ripple_pp_a' in desktop.report_to(tmp_path/'sparse.html').read_text(encoding='utf-8')


@pytest.mark.parametrize('topology',['T05','T11','T18'])
def test_alias_controls_preserve_explicit_extended_model_options(desktop,monkeypatch,topology):
    from power_stage_design.psfb_dynamics import PSFBOptions
    from power_stage_design.qr_dynamics import QROptions
    from power_stage_design.two_switch_dynamics import T05Options
    options={'T05':{'two_switch_options':T05Options()},'T11':{'qr_options':QROptions()},
        'T18':{'psfb_options':PSFBOptions(coss_f=1e-9,r_on_ohm=.1,body_diode_drop_v=.7,
            body_diode_resistance_ohm=.05,transformer_capacitance_f=1e-10,
            rectifier_capacitance_f=1e-10,rectifier_resistance_ohm=.1,parameter_source='explicit test fixture')}}
    source=replace(PRESETS[topology],topology=topology.lower(),**options[topology])
    # This is a controls/identity test, not a claim of numerical acceptance of
    # these illustrative dynamic options; those have separate physical suites.
    monkeypatch.setattr(desktop,'calculate',lambda:None)
    desktop.set_inputs(source)
    assert desktop.advanced_model.enabled.isChecked()
    assert desktop.current_inputs()==source
    assert desktop.current_inputs().design_id==source.design_id


@pytest.mark.parametrize('pending',['loop','stage'])
def test_pending_old_debounce_cannot_cancel_new_async_project_open(desktop,tmp_path,monkeypatch,pending):
    import threading

    from power_stage_design import gui
    desktop.analyze_loop()
    if pending=='loop':desktop.loop_widgets['esr_ohm'].setValue(.041)
    else:desktop.input_widgets['vin_v'].setValue(13)
    source=replace(PRESETS['T01'],vin_v=24)
    path=save_project(source,tmp_path/'delayed.json')
    real_read=gui.read_project
    gate=threading.Event()
    def delayed(*args,**kwargs):
        assert gate.wait(5)
        return real_read(*args,**kwargs)
    monkeypatch.setattr(gui,'read_project',delayed)
    desktop._start_project_open(path)
    assert not desktop._timer.isActive() and not desktop._loop_timer.isActive()
    pump(250);gate.set();pump(350)
    assert not desktop._jobs
    assert desktop.result.inputs==source
    assert '正在校验并重算' not in desktop.status.text()


@pytest.mark.parametrize('pending',['loop','stage'])
def test_failed_open_resumes_valid_pending_current_analysis(desktop,tmp_path,pending):
    desktop.analyze_loop()
    if pending=='loop':desktop.loop_widgets['esr_ohm'].setValue(.043)
    else:desktop.input_widgets['vin_v'].setValue(13.5)
    invalid=tmp_path/'invalid-input.json';invalid.write_text('{}',encoding='utf-8')
    desktop._start_project_open(invalid);pump(500)
    assert not desktop._jobs
    assert desktop.loop_result is not None
    if pending=='loop':assert desktop._loop_settings['plant']['esr_ohm']==.043
    else:assert desktop._loop_settings['plant']['vin_v']==13.5


@pytest.mark.parametrize('edit',['target','corner','device','cancel_synthesis'])
def test_new_target_cancels_open_without_losing_pending_current_loop(desktop,tmp_path,monkeypatch,edit):
    import threading

    from power_stage_design import gui
    desktop.analyze_loop();desktop.loop_widgets['esr_ohm'].setValue(.057)
    path=save_project(replace(PRESETS['T01'],vin_v=24),tmp_path/'unused.json')
    read=gui.read_project;gate=threading.Event()
    def delayed(*args,**kwargs):
        assert gate.wait(5)
        return read(*args,**kwargs)
    monkeypatch.setattr(gui,'read_project',delayed)
    desktop._start_project_open(path)
    if edit=='target':desktop.target_fc.setValue(3500)
    elif edit=='corner':desktop.corner_fields['input_voltages_v'].setText('12, 13')
    elif edit=='device':desktop.device_combo.setCurrentIndex(1)
    else:desktop.cancel_synthesis()
    gate.set();pump(400)
    assert not desktop._jobs
    assert desktop.result.inputs.vin_v==12
    assert desktop._loop_settings['plant']['esr_ohm']==.057
    assert desktop._current_loop_request()['target_crossover_hz']==(3500 if edit=='target' else 3000)
    assert '正在校验并重算' not in desktop.status.text()

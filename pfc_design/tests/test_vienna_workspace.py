"""Actual Vienna candidate/session/research delivery consumer regressions."""
from dataclasses import replace
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import numpy as np
import pytest

from pfc_design.vienna import build_vienna_control_lab_analysis,illustrative_vienna_config
from pfc_design.vienna.workspace import (LOOPS,encode_config,decode_config,import_exact_2p2z,
    export_report,loop_sources,read_session,write_session)


def app_view(monkeypatch):
    from PySide6.QtWidgets import QApplication
    from pfc_design.gui.vienna_control_view import ViennaControlLabView
    app=QApplication.instance() or QApplication([])
    v=ViennaControlLabView();v._test_app=app
    v.set_config(replace(illustrative_vienna_config(),frequency_points=400))
    monkeypatch.setattr(v.performance,'set_vienna',lambda *args:None)
    for name in ('_plot_ac','_plot_bus','_plot_switch','_plot_sector','_plot_harm','_summary'):
        monkeypatch.setattr(v,name,lambda *args:None)
    return v


def show_result(v):
    a=build_vienna_control_lab_analysis(v._config())
    assert v.set_result((a,SimpleNamespace(),SimpleNamespace()))
    return a


def test_exact_2p2z_preserves_coefficients_and_rejects_clock_or_order_change():
    c=illustrative_vienna_config()
    changed=import_exact_2p2z(c,'current',[.013, -.011, .000123],[1,-.7,.11],1/50000)
    tf=changed.current_controller.transfer_function()
    np.testing.assert_array_equal(tf.numerator,[.013,-.011,.000123])
    np.testing.assert_array_equal(tf.denominator,[1,-.7,.11])
    f=np.geomspace(1,20000,400);q=np.exp(-2j*np.pi*f/50000)
    np.testing.assert_allclose(tf.frequency_response(f),(.013-.011*q+.000123*q*q)/(1-.7*q+.11*q*q),rtol=2e-15)
    for b,a,ts in (([1,2,3,4],[1],1/50000),([1],[1],1/40000),([1],[0,1],1/50000)):
        with pytest.raises(ValueError):import_exact_2p2z(c,'current',b,a,ts)


def test_real_qt_candidate_apply_changes_exact_consumer_and_retained_point_checks(monkeypatch):
    v=app_view(monkeypatch);a=show_result(v);m=v.solution_map_view
    m.source_combo.setCurrentIndex(m.source_combo.findData('current'))
    m.retain_button.click();assert len(m._retained_sources)==1
    m.live_fc.setValue(1500);m.live_pm.setValue(60);m.generate_button.click()
    assert m.selected_point is not None
    assert m.selected_point.kp is not None
    assert m.selected_point.status.name in ('FEASIBLE','CONDITIONAL')
    m.allow_conditional.setChecked(True);m.generate_button.click()
    assert m.apply_button.isEnabled()
    point=m.selected_point
    requested=[];v.analysis_requested.connect(requested.append)
    m.apply_button.click();m.apply_button.click()
    assert len(requested)==1 and v.result is None
    assert requested[0].current_controller.kp==point.kp
    assert requested[0].current_controller.ti_s==point.ti_s
    assert v._config()==requested[0]
    b=show_result(v)
    controller=b.current_loop.controller
    expected=a.current_loop.responses['plant_gid']*a.current_loop.responses['pwm_zoh']*a.current_loop.responses['sense_hi']*controller.frequency_response(b.current_loop.frequencies_hz)
    np.testing.assert_allclose(b.current_loop.responses['open_current'],expected,rtol=3e-14)
    assert abs(b.current_loop.margins.critical_gain_crossover_hz-1500)<1
    assert abs(b.current_loop.margins.phase_margin_deg-60)<.1
    v.close()


def test_atomic_failed_session_and_exact_new_process_replay(tmp_path,monkeypatch):
    from pfc_design.vienna.workspace_ui import save_path,load_path
    v=app_view(monkeypatch);a=show_result(v);m=v.solution_map_view
    m.retain_button.click();m.live_fc.setValue(1234.567)
    config=replace(v._config(),current_controller=replace(v._config().current_controller,kp=.023456789123456),
                   split_bus_sense=replace(v._config().split_bus_sense,second_resistance_ohm=4321.234567,second_capacitance_f=2.3e-9))
    v.set_config(config);show_result(v);m.retain_button.click()
    path=save_path(v,tmp_path/'session.json')
    new=app_view(monkeypatch);load_path(new,path)
    assert new._config()==config and new.result is None
    assert not new.solution_map_view.apply_button.isEnabled()
    assert len(new.solution_map_view._retained_sources)==1
    assert new.solution_map_view.live_fc.value()==m.live_fc.value()
    before=new._config();bad=json.loads(path.read_text());bad['workspace']['controls']['fc_max']=-1
    badpath=tmp_path/'bad.json';badpath.write_text(json.dumps(bad))
    with pytest.raises(ValueError):load_path(new,badpath)
    assert new._config()==before
    output=tmp_path/'fresh.json'
    script='''from pfc_design.vienna.workspace import read_session,encode_config\nfrom pfc_design.vienna import build_vienna_control_lab_analysis\nimport json,sys\nc,_=read_session(sys.argv[1]);a=build_vienna_control_lab_analysis(c)\njson.dump({'config':encode_config(c),'loops':{key:[[float(z.real),float(z.imag)] for z in getattr(a,key+'_loop').responses['open_'+key]] for key in ('current','voltage','balance')}},open(sys.argv[2],'w'))'''
    subprocess.run([sys.executable,'-c',script,str(path),str(output)],check=True,env=os.environ)
    replay=json.loads(output.read_text());assert decode_config(replay['config'])==config
    analysis=build_vienna_control_lab_analysis(config)
    for key in LOOPS:
        samples=np.asarray(replay['loops'][key]);np.testing.assert_array_equal(samples[:,0]+1j*samples[:,1],getattr(analysis,key+'_loop').responses['open_'+key])
    v.close();new.close()


def test_report_files_and_standalone_python_exact_identity(tmp_path):
    a=build_vienna_control_lab_analysis(replace(illustrative_vienna_config(),frequency_points=220))
    files=export_report(a,tmp_path/'report');assert len(files)==11 and all(p.is_file() for p in files)
    for key in LOOPS:
        file=tmp_path/'report'/(key+'_research.py')
        namespace={'__name__':'test'};exec(compile(file.read_text(),str(file),'exec'),namespace)
        assert namespace['coefficient_verification']=={'C':'PASS_AT_WELL_CONDITIONED_SAMPLES'}
        csv=np.loadtxt(tmp_path/'report'/(key+'_responses.csv'),delimiter=',',skiprows=1)
        assert np.all(csv[:,0]<getattr(a,key+'_loop').nyquist_hz)
    assert 'NOT_PROVEN' in (tmp_path/'report'/'vienna_report.md').read_text()


def test_cancel_is_cooperative_and_no_partial_result():
    from pfc_design.vienna import simulate_vienna_line_cycle,ViennaAnalysisCancelled
    calls=[]
    def cancel():calls.append(1);return len(calls)>=3
    with pytest.raises(ViennaAnalysisCancelled):simulate_vienna_line_cycle(illustrative_vienna_config(),cancelled=cancel)
    assert len(calls)==3


def test_c99_dialog_toctou_rejects_changed_source(monkeypatch):
    from PySide6.QtWidgets import QFileDialog
    import pfc_design.gui.vienna_control_view as module
    v=app_view(monkeypatch);show_result(v)
    def dialog(*args):v.cc['kp'].setValue(.021);return '/tmp/never-vienna-export'
    monkeypatch.setattr(QFileDialog,'getExistingDirectory',dialog)
    generated=[];errors=[]
    monkeypatch.setattr(module,'generate_vienna_control_code',lambda *args:generated.append(args))
    monkeypatch.setattr(module,'show_operation_issue',lambda *args,**kwargs:errors.append(args))
    v._generate_c99()
    assert not generated and errors and v.result is None
    v.close()


def _drain_scan(app, maps, timeout=10):
    from time import monotonic,sleep
    deadline=monotonic()+timeout
    while maps.scan_running and monotonic()<deadline:
        app.processEvents();sleep(.01)
    app.processEvents()
    assert not maps.scan_running


def test_actual_map_worker_cancel_edit_and_success(monkeypatch):
    v=app_view(monkeypatch);show_result(v);m=v.solution_map_view
    m.fc_points.setValue(5);m.pm_points.setValue(5)
    m.fc_min.setValue(500);m.fc_max.setValue(2000)
    m.run_button.click();assert m.scan_running
    m.cancel_button.click();_drain_scan(v._test_app,m)
    assert m.result is None and not m.apply_button.isEnabled()
    m.run_button.click();v.cc['kp'].setValue(.021);_drain_scan(v._test_app,m)
    assert m.result is None and v.result is None
    show_result(v);m.run_button.click();_drain_scan(v._test_app,m)
    assert m.result is not None and m.result.status_codes.shape==(5,5)
    v.close()


@pytest.mark.parametrize('field,value',[('frequency_points',True),('frequency_stop_hz','1000'),('waveform_line_cycles',3.5)])
def test_malformed_session_types_rejected(tmp_path,field,value):
    c=illustrative_vienna_config();p=write_session(tmp_path/'s.json',c)
    document=json.loads(p.read_text());document['config']['fields'][field]=value;p.write_text(json.dumps(document))
    with pytest.raises(ValueError):read_session(p)


def test_loaded_hidden_delay_split_is_not_added_again_after_total_edit(monkeypatch):
    v=app_view(monkeypatch)
    c=v._config();assert c.firmware.current_computation_delay_s==2e-6 and c.firmware.pwm_update_delay_s==7e-6
    assert v._config()==c
    v.current_delay.setValue(10)
    fw=v._config().firmware
    assert fw.current_computation_delay_s+fw.pwm_update_delay_s==pytest.approx(10e-6)
    v.close()


def test_candidate_apply_rechecks_assessment_and_range(monkeypatch):
    from pfc_design.vienna.workspace_ui import apply_candidate
    v=app_view(monkeypatch);show_result(v);m=v.solution_map_view
    m.allow_conditional.setChecked(True);m.live_fc.setValue(1500);m.live_pm.setValue(60);m.generate_button.click()
    point=m.selected_point;before=v._config()
    apply_candidate(v,'current',replace(point,kp=point.kp*1.1))
    assert v._config()==before and v.has_current_result()
    v.close()

"""Production advanced inputs, identities, drawn connections and real Qt consumers."""
import hashlib
import json
import os
from collections import defaultdict
from dataclasses import asdict, replace
from pathlib import Path

import numpy as np
import pytest

from power_stage_design.models import MODEL_VERSION, DesignInputs
from power_stage_design.psfb_dynamics import PSFBOptions
from power_stage_design.qr_dynamics import QROptions
from power_stage_design.schematic import _circuit, layout, schematic_size
from power_stage_design.topologies import calculate_stage
from power_stage_design.two_switch_dynamics import T05Options


def _inputs(topology):
    if topology=='T05':
        return DesignInputs('T05',24,12,.1,100e3,100e-6,two_switch_options=T05Options(parameter_source='Explicit illustrative test constants'))
    if topology=='T18':
        return DesignInputs('T18',400,48,5,100e3,100e-6,turns_ratio_np_ns=4,magnetizing_inductance_h=1e-3,leakage_inductance_h=20e-6,dead_time_s=200e-9,diode_drop_v=.7,
            psfb_options=PSFBOptions(coss_f=300e-12,r_on_ohm=.15,body_diode_drop_v=.8,body_diode_resistance_ohm=.05,transformer_capacitance_f=100e-12,rectifier_capacitance_f=1e-9,rectifier_resistance_ohm=.02,parameter_source='Explicit seven-state circuit test constants'))
    return DesignInputs('T11',48,12,1,180e3,200e-6,turns_ratio_np_ns=8,resonant_capacitance_f=500e-12,
        qr_options=QROptions(valley_policy='selected',valley_index=2,on_resistance_ohm=.1,
            capacitance_source='Independent constant-C test case'))


def test_legacy_identity_unchanged_optional_model_not_silently_ignored():
    p=DesignInputs('T01',12,5,3,100e3,47e-6)
    legacy={k:v for k,v in asdict(p).items() if k not in {'two_switch_options','qr_options','psfb_options'}}
    expected=hashlib.sha256(json.dumps({'model_version':MODEL_VERSION,'inputs':legacy},sort_keys=True,allow_nan=False).encode()).hexdigest()[:20]
    assert p.design_id==expected
    for name,options in [('two_switch_options',T05Options()),('qr_options',QROptions()),('psfb_options',_inputs('T18').psfb_options)]:
        with pytest.raises(ValueError,match='only to'):
            calculate_stage(replace(p,**{name:options}))
        with pytest.raises(ValueError):
            DesignInputs(**{**asdict(p),name:{'unknown':1}})
        with pytest.raises(ValueError):
            DesignInputs(**{**asdict(p),name:[]})
    with pytest.raises(ValueError):
        replace(p,two_switch_options=T05Options(),qr_options=QROptions())


def _lies(point,line):
    x,y=point;a,b,c,d=line
    return a==c==x and min(b,d)<=y<=max(b,d) or b==d==y and min(a,c)<=x<=max(a,c)


@pytest.mark.parametrize('topology,keys',[
    ('T05',{'Q1','Q2','D1','D2','Dbody1','Dbody2','Coss1','Coss2','L1','Rwinding','Cout','Cin'}),
    ('T11',{'Q1','D1','Dbody','Cnode','primary','secondary','Lm','Cin','Cout'}),
    ('T18',{'L1','Lleak','Lm','primary','secondary1','secondary2','Q1','Q2','Q3','Q4','Dbody1','Dbody2','Dbody3','Dbody4','Coss1','Coss2','Coss3','Coss4','D1','D2','Cprimary','Crectifier','Cout','Cin','node_A','node_B'}),
])
def test_advanced_schematic_has_exact_connected_branch_graph(topology,keys):
    circuit=_circuit(topology,device_keys=keys)
    assert {e.key for e in circuit.elements if e.selectable}==keys
    points=defaultdict(set)
    for net,line in circuit.routes:points[net].update((line[:2],line[2:]))
    for e in circuit.elements:
        for net,p in zip(e.nets,e.terminals):
            assert {n for n,line in circuit.routes if _lies(p,line)}=={net},(e.key,p)
            points[net].add(p)
        assert [x.key for x in circuit.elements if x.contains(e.x,e.y)]==([e.key] if e.selectable else [])
    for net,line in circuit.routes:
        for point in (line[:2],line[2:]):
            assert all(other==net or not _lies(point,segment) for other,segment in circuit.routes),(net,point)
    for net,ps in points.items():
        parents={p:p for p in ps}
        def root(p, parents=parents):
            while parents[p]!=p:p=parents[p]
            return p
        for name,line in circuit.routes:
            if name==net:
                along=[p for p in ps if _lies(p,line)]
                for p in along:parents[root(p)]=root(along[0])
        assert len({root(p) for p in ps})==1,(topology,net)


@pytest.fixture(scope='module')
def app():
    os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
    pytest.importorskip('PySide6.QtWidgets')
    from PySide6.QtWidgets import QApplication
    application=QApplication.instance() or QApplication([])
    application.setQuitOnLastWindowClosed(False)
    return application


@pytest.fixture
def window(app):
    from PySide6.QtCore import QCoreApplication, QEvent

    from power_stage_design.gui import PowerStageMainWindow
    w=PowerStageMainWindow();w.show();app.processEvents()
    yield w
    w.close();w.deleteLater();QCoreApplication.sendPostedEvents(None,QEvent.Type.DeferredDelete);app.processEvents()


def _wait(predicate,seconds=120):
    from PySide6.QtCore import QEventLoop, QTimer
    if predicate():return
    loop=QEventLoop();poll=QTimer();deadline=QTimer();deadline.setSingleShot(True)
    poll.timeout.connect(lambda:loop.quit() if predicate() else None)
    deadline.timeout.connect(loop.quit);poll.start(20);deadline.start(round(seconds*1000))
    # The actual Qt event loop releases the GIL while the Python solver runs.
    loop.exec();poll.stop();deadline.stop()
    assert predicate(),'real worker did not finish'


@pytest.mark.parametrize('topology',['T05','T11','T18'])
def test_advanced_real_inputs_buttons_schematic_save_report_and_restore(window,app,tmp_path,topology):
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest
    inputs=_inputs(topology)
    window.set_inputs(inputs)
    assert window.current_inputs()==inputs
    _wait(lambda:window.result is not None or not window._jobs)
    assert window.result is not None,window.status.text()
    assert window.result.inputs==inputs
    key={'T05':'Coss1','T11':'Dbody','T18':'node_A'}[topology]
    element=next(e for e in layout(topology,device_keys=window.result.waveforms)[0] if e.key==key)
    sw,sh=schematic_size(topology,device_keys=window.result.waveforms)
    for branch in layout(topology,device_keys=window.result.waveforms)[0]:
        if not branch.selectable:continue
        QTest.mouseClick(window.schematic,Qt.MouseButton.LeftButton,pos=QPoint(round(branch.x*window.schematic.width()/sw),round(branch.y*window.schematic.height()/sh)))
        assert window.component_combo.currentData()==branch.key
        for plot,signal in ((window.current_plot,window.result.waveforms[branch.key].current_a),(window.voltage_plot,window.result.waveforms[branch.key].voltage_v)):
            _,actual=plot.listDataItems()[0].getData()
            np.testing.assert_array_equal(actual,signal.sample(count=800,periods=2)[1])
    QTest.mouseClick(window.schematic,Qt.MouseButton.LeftButton,pos=QPoint(round(element.x*window.schematic.width()/sw),round(element.y*window.schematic.height()/sh)))
    assert window.component_combo.currentData()==key
    _t,y=window.current_plot.listDataItems()[0].getData()
    np.testing.assert_array_equal(y,window.result.waveforms[key].current_a.sample(count=800,periods=2)[1])
    if topology=='T18':
        assert '不是额外功率器件' in window.component_status.text()
        assert '理想高阻电压探针' in window.component_combo.currentText()
    source=window.current_inputs();identity=source.design_id
    evidence=Path(os.environ.get('PSD_ADVANCED_EVIDENCE_DIR',tmp_path));evidence.mkdir(parents=True,exist_ok=True)
    path=evidence/f'{topology}.json';report=evidence/f'{topology}.html'
    if os.environ.get('PSD_ADVANCED_EVIDENCE_DIR'):
        window.resize(1440,1000);app.processEvents()
        assert window.grab().save(str(evidence/f'{topology}-branch.png'))
        window.tabs.setCurrentIndex(window.tabs.count()-1);app.processEvents()
        assert window.grab().save(str(evidence/f'{topology}-parameters.png'))
        window.tabs.setCurrentIndex(0);app.processEvents()
    window.save_to(path);window.report_to(report)
    assert identity in report.read_text(encoding='utf-8')
    assert source==DesignInputs(**json.loads(path.read_text(encoding='utf-8'))['inputs'])
    window.advanced_model.enabled.setChecked(False)
    assert window.result is None and not window.report_action.isEnabled()
    window.open_from(path)
    _wait(lambda:window.result is not None or not window._jobs)
    assert window.result is not None and window.current_inputs()==source
    assert window.result.design_id==identity
    assert window.advanced_model.enabled.isChecked()
    window.report_to(report)
    assert identity in report.read_text(encoding='utf-8')
    assert 'component-'+key in report.read_text(encoding='utf-8')


def test_invalid_advanced_curve_is_rejected_without_qt_slot_exception_or_stale_export(window):
    from power_stage_design.gui import PRESETS
    window.set_inputs(PRESETS['T11'])
    window.advanced_model.enabled.setChecked(True)
    window.advanced_model.qr_capacitance.setCurrentIndex(window.advanced_model.qr_capacitance.findData('charge'))
    window.advanced_model.qr_table.setPlainText('0,0\n200,not-a-number')
    window.calculate()
    assert window.result is None and not window.report_action.isEnabled()
    assert '输入无效' in window.status.text()


def test_exact_advanced_values_restore_then_edit_discards_hidden_precision(app):
    from power_stage_design.advanced_gui import AdvancedModelWidget
    widget=AdvancedModelWidget()
    try:
        options=QROptions(gate_delay_s=1.2345678901234567e-8,on_resistance_ohm=.12345678901234567,
            resistive_root_policy='higher_peak',capacitance_model='charge',capacitance_table=((0,0),(200,9.876543210987654e-8)))
        p=replace(_inputs('T11'),qr_options=options)
        widget.restore(p);assert widget.options()['qr_options']==options
        display=widget.qr_widgets['gate_delay_s'].value()
        widget.qr_widgets['gate_delay_s'].setValue(display+1)
        widget.qr_widgets['gate_delay_s'].setValue(display)
        assert widget.options()['qr_options'].gate_delay_s==display/1e9
    finally:widget.deleteLater();app.processEvents()


@pytest.mark.parametrize('change',['source','numeric','disable','topology','close'])
def test_advanced_edits_cancel_pending_request_and_reject_late_result(window,change):
    from power_stage_design.gui import PRESETS
    # Keep a genuine prior result to challenge the actual delivery slot. The
    # expensive-request cancellation is real, not a mocked result callback.
    previous=window.result
    window.set_inputs(_inputs('T05'))
    token=window._generation
    pending=list(window._cancel_events)
    assert pending and not pending[-1].is_set()
    if change=='source':window.advanced_model.t05_source.setText('Changed source identity')
    elif change=='numeric':window.advanced_model.t05_widgets['coss_q1_f'].setValue(2.123)
    elif change=='disable':window.advanced_model.enabled.setChecked(False)
    elif change=='topology':window.set_inputs(PRESETS['T01'])
    else:window.close()
    assert all(event.is_set() for event in pending)
    current=window.result
    window._on_stage_calculated((token,previous,None))
    assert window.result is current
    if change!='topology':assert not window.report_action.isEnabled()

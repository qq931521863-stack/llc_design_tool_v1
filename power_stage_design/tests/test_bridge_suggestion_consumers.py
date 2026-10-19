"""Real Preview/Apply/open/report consumers for newly connected bridge targets."""
import json
import os
import time
from dataclasses import replace

import pytest

os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PySide6.QtCore import QCoreApplication, QEvent, QEventLoop, Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from power_stage_design.gui import PowerStageMainWindow
from power_stage_design.project import read_project
from power_stage_design.tests.test_bridge_family_sizing import _fixture
from power_stage_design.tests.test_psfb_magnetizing_sizing import example, reference


@pytest.fixture
def desktop():
    app=QApplication.instance() or QApplication([]);app.setQuitOnLastWindowClosed(False)
    window=PowerStageMainWindow();window.show();app.processEvents()
    yield window
    window.close();window.deleteLater()
    QCoreApplication.sendPostedEvents(None,QEvent.Type.DeferredDelete);app.processEvents()


def wait(predicate,seconds=15):
    deadline=time.monotonic()+seconds
    while not predicate() and time.monotonic()<deadline:
        loop=QEventLoop();QTimer.singleShot(15,loop.quit);loop.exec()
    assert predicate()


@pytest.mark.parametrize('topology,branch',[(t,b) for t in ('T15','T16') for b in ('ordinary','dcm')]+[('T18','CCM'),('T18','DCM')])
def test_preview_apply_reopen_report_of_actual_Lout_Lm_ratio_targets(desktop,tmp_path,topology,branch):
    if topology=='T18':
        actual=example(5. if branch=='CCM' else .1)
        ripple,magnetic,duty,_=reference(actual,branch)
    else:
        actual,duty,ripple,magnetic,*_=_fixture(topology,branch,0.)
    source=replace(actual,inductance_h=actual.inductance_h*1.1,
        magnetizing_inductance_h=actual.magnetizing_inductance_h*1.1,
        turns_ratio_np_ns=actual.turns_ratio_np_ns*.95)
    desktop.set_inputs(source);wait(lambda:desktop.result is not None)
    old_id=desktop.result.design_id
    desktop.tabs.setCurrentWidget(desktop.suggestion_tab)
    desktop.suggestion_ripple.setValue(ripple)
    desktop.suggestion_magnetizing.setValue(magnetic)
    desktop.suggestion_duty.setValue(duty)
    desktop.suggestion_ratio.setChecked(True)
    assert not desktop.suggestion_magnetizing.isHidden()
    QTest.mouseClick(desktop.suggest_button,Qt.MouseButton.LeftButton)
    wait(lambda:desktop.parameter_proposal is not None or '无有效建议' in desktop.suggestion_output.toPlainText())
    proposal=desktop.parameter_proposal
    assert proposal is not None,desktop.suggestion_output.toPlainText()
    assert desktop.current_inputs()==source and desktop.result.design_id==old_id
    assert set(proposal.changed_fields)=={'inductance_h','magnetizing_inductance_h','turns_ratio_np_ns'}
    assert proposal.result.metrics['inductor_ripple_pp_a']==pytest.approx(ripple,rel=3e-6)
    assert proposal.result.stress['Lm']['current_pp_a']==pytest.approx(magnetic,rel=3e-6)
    assert proposal.result.duty==pytest.approx(duty,rel=3e-6)
    QTest.mouseClick(desktop.apply_suggestion_button,Qt.MouseButton.LeftButton)
    wait(lambda:desktop.result is not None)
    assert desktop.current_inputs()==proposal.inputs
    assert desktop.result.design_id==proposal.result.design_id!=old_id
    path=desktop.save_to(tmp_path/'project.json')
    saved,doc=read_project(path)
    assert saved==proposal.inputs and doc['parameter_suggestion']==proposal.record()
    html=desktop.report_to(tmp_path/'report.html').read_text(encoding='utf-8')
    assert old_id in html and proposal.result.design_id in html and 'magnetizing_ripple_pp_a' in html
    desktop.set_inputs(source);desktop.open_from(path);wait(lambda:desktop.result is not None)
    assert desktop.current_inputs()==proposal.inputs and desktop._applied_suggestion==proposal.record()
    again=desktop.save_to(tmp_path/'again.json')
    assert json.loads(again.read_text(encoding='utf-8'))['parameter_suggestion']==proposal.record()

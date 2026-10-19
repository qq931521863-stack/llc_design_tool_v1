"""Actual optional-model proposals preserve immutable options through consumers."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from power_stage_design.models import DesignInputs
from power_stage_design.project import read_project, save_project
from power_stage_design.suggestions import (
    SuggestionTargets,
    suggest_parameters,
    validate_suggestion_record,
)
from power_stage_design.topologies import calculate_stage

EXAMPLES = Path(__file__).resolve().parents[2] / 'examples' / 'power_stage_design'


@pytest.mark.parametrize('example,option_name', [
    ('two-switch-floating-nodes.json', 'two_switch_options'),
    ('qr-delayed-valley.json', 'qr_options'),
    ('psfb-finite-coss.json', 'psfb_options'),
])
def test_real_proposal_preserves_nested_options_and_saved_provenance(example, option_name, tmp_path):
    source, _ = read_project(EXAMPLES / example)
    actual = calculate_stage(source)
    proposal = suggest_parameters(source, SuggestionTargets(actual.metrics['inductor_ripple_pp_a'], .95))
    assert getattr(proposal.inputs, option_name) == getattr(source, option_name)
    assert set(proposal.changed_fields) <= {'inductance_h'}
    record = json.loads(json.dumps(proposal.record()))
    assert validate_suggestion_record(record, proposal.result) == record
    saved = tmp_path / example
    save_project(proposal.inputs, saved, parameter_suggestion=record)
    restored, document = read_project(saved)
    assert restored == proposal.inputs
    assert document['parameter_suggestion'] == record
    assert validate_suggestion_record(document['parameter_suggestion'], calculate_stage(restored)) == record
    # An actual parameter-source edit must still be identified and rejected.
    modified = json.loads(json.dumps(record))
    source_data = modified['source_inputs']
    field = 'capacitance_source' if option_name == 'qr_options' else 'parameter_source'
    source_data[option_name][field] += ' edited'
    modified['source_design_id'] = DesignInputs(**source_data).design_id
    modified['changed_fields'].append(option_name)
    with pytest.raises(ValueError, match='untargeted'):
        validate_suggestion_record(modified, proposal.result)


def test_t05_actual_preview_apply_save_reopen_report_and_live_scope(tmp_path, monkeypatch):
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    pytest.importorskip('PySide6.QtWidgets')
    from PySide6.QtCore import QCoreApplication, QEvent, QEventLoop, Qt, QTimer
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication

    from power_stage_design.gui import PRESETS, PowerStageMainWindow

    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    view = PowerStageMainWindow()
    view.show()

    def wait(predicate, seconds=180):
        # Use the real native event loop so Python ODE callbacks on the worker
        # get the GIL; a tight QTest.qWait loop can starve those callbacks.
        if not predicate():
            loop = QEventLoop()
            poll = QTimer();poll.setInterval(20)
            poll.timeout.connect(lambda: loop.quit() if predicate() else None)
            deadline = QTimer();deadline.setSingleShot(True)
            deadline.timeout.connect(loop.quit)
            poll.start();deadline.start(round(seconds*1000));loop.exec()
            poll.stop();deadline.stop()
        assert predicate(), view.status.text() + '\n' + view.suggestion_output.toPlainText()

    try:
        source, _ = read_project(EXAMPLES / 'two-switch-floating-nodes.json')
        view.set_inputs(source)
        wait(lambda: view.result is not None)
        actual = view.result
        view.tabs.setCurrentWidget(view.suggestion_tab)
        assert '浮空节点' in view.suggestion_scope.text()
        assert '仅支持 CCM/BCM' not in view.suggestion_scope.text()
        # A distinct real ripple target exercises the inverse solver and Apply.
        view.suggestion_ripple.setValue(actual.metrics['inductor_ripple_pp_a'] * 1.03)
        view.suggestion_duty.setValue(.95)
        QTest.mouseClick(view.suggest_button, Qt.MouseButton.LeftButton)
        wait(lambda: view.parameter_proposal is not None)
        proposal = view.parameter_proposal
        assert proposal.inputs.inductance_h != source.inductance_h
        assert proposal.changed_fields == ('inductance_h',)
        assert '尚未应用' in view.suggestion_output.toPlainText()
        assert view.current_inputs() == source
        QTest.mouseClick(view.apply_suggestion_button, Qt.MouseButton.LeftButton)
        wait(lambda: view.result is not None)
        assert view.current_inputs() == proposal.inputs
        saved, report = tmp_path / 'advanced.json', tmp_path / 'advanced.html'
        view.save_to(saved)
        view.report_to(report)
        assert proposal.source_design_id in report.read_text(encoding='utf-8')
        view.set_inputs(PRESETS['T01'])
        view.open_from(saved)
        wait(lambda: view.result is not None)
        assert view.current_inputs() == proposal.inputs
        assert view._applied_suggestion == proposal.record()
        view.report_to(report)
        text = report.read_text(encoding='utf-8')
        assert proposal.result.design_id in text and proposal.source_design_id in text
        # Help follows the actual toggle, without changing topology or reopening.
        view.advanced_model.enabled.setChecked(False)
        assert '仅支持 CCM/BCM' in view.suggestion_scope.text()
        assert view._applied_suggestion is None and not view.report_action.isEnabled()
        # Invalid advanced fields are handled by this actual action, not leaked
        # as an uncaught Qt slot exception or a background worker.
        import sys
        exceptions = []
        monkeypatch.setattr(sys, 'excepthook', lambda *args: exceptions.append(args))
        view.advanced_model.t05_widgets['ron_q1_ohm'].setValue(0)
        view.advanced_model.enabled.setChecked(True)
        QTest.mouseClick(view.suggest_button, Qt.MouseButton.LeftButton)
        assert not exceptions
        assert '输入无效' in view.suggestion_output.toPlainText()
        assert not view.apply_suggestion_button.isEnabled()
        assert not view._suggestion_events
    finally:
        view.close()
        view.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        app.processEvents()

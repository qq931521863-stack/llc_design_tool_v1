"""Visible component controls must name the actual modeled state/branch."""
from __future__ import annotations

import os
import time

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
pytest.importorskip('PySide6.QtWidgets')
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication

from power_stage_design.gui import PRESETS, PowerStageMainWindow


@pytest.fixture
def window():
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    view = PowerStageMainWindow()
    view.show()
    app.processEvents()
    yield view
    view.close()
    # PlotWidget.close() detaches the plot; retain Qt's owner tree until deletion.
    view.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()


def wait_for_result(view, design_id):
    end = time.monotonic() + 30
    app = QApplication.instance()
    while time.monotonic() < end:
        app.processEvents()
        if view.result is not None and view.result.design_id == design_id:
            return
        time.sleep(.01)
    raise AssertionError('Actual stage worker did not finish')


@pytest.mark.parametrize('topology', ['T04', 'T09', 'T10', 'T11'])
def test_main_winding_inductance_names_primary_lm_without_duplicate_control(window, topology):
    # Inspect visibility without starting a slow model just to read form labels.
    window.topology_combo.setCurrentIndex(window.topology_combo.findData(topology))
    window._timer.stop()
    field = window.input_widgets['inductance_h']
    assert 'Lm' in window._input_form.labelForField(field).text()
    assert field.isVisible()
    assert not window.input_widgets['magnetizing_inductance_h'].isVisible()


@pytest.mark.parametrize('topology', ['T12', 'T13', 'T14', 'T15', 'T16', 'T17', 'T18'])
def test_forward_bridge_has_distinct_output_and_magnetizing_controls(window, topology):
    window.topology_combo.setCurrentIndex(window.topology_combo.findData(topology))
    window._timer.stop()
    assert '输出电感' in window._input_form.labelForField(window.input_widgets['inductance_h']).text()
    assert window.input_widgets['magnetizing_inductance_h'].isVisible()


def test_fly_buck_visible_lm_edit_changes_actual_state_waveforms(window, tmp_path):
    window.set_inputs(PRESETS['T04'])
    wait_for_result(window, PRESETS['T04'].design_id)
    old = window.result
    box = window.input_widgets['inductance_h']
    box.setValue(box.value()*1.2)
    current = window.current_inputs()
    assert window.result is None
    wait_for_result(window, current.design_id)
    assert window.result.inputs.inductance_h == pytest.approx(old.inputs.inductance_h*1.2)
    assert window.result.metrics['inductor_ripple_pp_a'] != pytest.approx(old.metrics['inductor_ripple_pp_a'])
    assert not window.input_widgets['magnetizing_inductance_h'].isVisible()
    path = tmp_path/'fly-buck.json'
    window.save_to(path)
    window.open_from(path)
    wait_for_result(window, current.design_id)
    assert window.current_inputs() == current
    assert 'Lm' in window._input_form.labelForField(box).text()
    assert not window.input_widgets['magnetizing_inductance_h'].isVisible()


def test_fast_restored_suggestion_reports_completed_recomputation(window, tmp_path):
    from power_stage_design.project import save_project
    from power_stage_design.suggestions import SuggestionTargets, suggest_parameters

    proposal = suggest_parameters(PRESETS['T01'], SuggestionTargets(.9))
    path = save_project(proposal.inputs, tmp_path/'suggested.json', parameter_suggestion=proposal.record())
    window.open_from(path)
    assert window.result is not None
    assert window.result.design_id == proposal.inputs.design_id
    assert '当前结果已重新计算' in window.suggestion_output.toPlainText()
    assert '正在核对' not in window.suggestion_output.toPlainText()

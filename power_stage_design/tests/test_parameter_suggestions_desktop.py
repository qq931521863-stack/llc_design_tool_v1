"""Real Qt parameter-proposal buttons, application, persistence and ownership."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from dataclasses import replace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6.QtWidgets")
from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from power_stage_design.gui import PRESETS, PowerStageMainWindow
from power_stage_design.suggestions import SuggestionTargets, suggest_parameters


@pytest.fixture(scope="module")
def app():
    application = QApplication.instance() or QApplication([])
    application.setQuitOnLastWindowClosed(False)
    return application


@pytest.fixture
def window(app):
    view = PowerStageMainWindow()
    view.show()
    app.processEvents()
    yield view
    view.close()
    # PlotWidget.close() detaches the plot; retain Qt's owner tree until deletion.
    view.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()


def wait_for(predicate, seconds=20):
    end = time.monotonic()+seconds
    while not predicate() and time.monotonic() < end:
        QTest.qWait(20)
    assert predicate(), "Qt worker did not reach the expected state"


def propose(window):
    window.tabs.setCurrentWidget(window.suggestion_tab)
    QTest.mouseClick(window.suggest_button, Qt.MouseButton.LeftButton)
    wait_for(lambda: window.parameter_proposal is not None or "无有效建议" in window.suggestion_output.toPlainText())
    assert window.parameter_proposal is not None, window.suggestion_output.toPlainText()


@pytest.mark.parametrize("topology", ["T01", "T09", "T17", "T18", "T11"])
def test_real_buttons_preview_apply_report_restore_and_cli(window, tmp_path, topology):
    window.set_inputs(PRESETS[topology])
    wait_for(lambda: window.result is not None)
    original = window.current_inputs()
    window.suggestion_ripple.setValue(.31 if topology == "T11" else .375123456*original.iout_a)
    window.suggestion_duty.setValue(.45)
    window.suggestion_ratio.setChecked(topology != "T01")
    window.suggestion_reflected.setValue(115)
    propose(window)
    proposal = window.parameter_proposal
    assert window.current_inputs() == original
    assert window.result.design_id == original.design_id
    assert window.apply_suggestion_button.isEnabled()
    assert "尚未应用" in window.suggestion_output.toPlainText()
    assert "元件建议" in window.suggestion_output.toPlainText()
    assert "source_design_id" not in window.suggestion_output.toPlainText()
    assert proposal.source_design_id in window.evidence.toPlainText()
    QTest.mouseClick(window.apply_suggestion_button, Qt.MouseButton.LeftButton)
    wait_for(lambda: window.result is not None)
    assert window.current_inputs() == proposal.inputs
    assert window.result.design_id == proposal.result.design_id
    assert not window.apply_suggestion_button.isEnabled()
    assert window._applied_suggestion == proposal.record()
    assert proposal.source_design_id in window.evidence.toPlainText()
    assert "已应用" in window.suggestion_output.toPlainText()
    saved = tmp_path / f"{topology}.json"
    report = tmp_path / f"{topology}.html"
    window.save_to(saved)
    window.report_to(report)
    assert proposal.source_design_id in report.read_text(encoding="utf-8")
    window.set_inputs(PRESETS["T02"])
    assert window._applied_suggestion is None
    window.open_from(saved)
    wait_for(lambda: window.result is not None)
    assert window.current_inputs() == proposal.inputs
    assert window._applied_suggestion == proposal.record()
    window.report_to(report)
    assert "参数建议：目标、来源与实际验算" in report.read_text(encoding="utf-8")
    assert format(proposal.inputs.inductance_h, ".17g") == format(window.current_inputs().inductance_h, ".17g")
    if topology == "T01":
        cli_report = tmp_path / "headless.html"
        completed = subprocess.run([sys.executable, "-m", "power_stage_design", str(saved), "--report", str(cli_report)],
                                   capture_output=True, text=True, timeout=30, check=False)
        assert completed.returncode == 0, completed.stderr
        assert proposal.source_design_id in cli_report.read_text(encoding="utf-8")
        assert "volt-second-targets-1" in cli_report.read_text(encoding="utf-8")


@pytest.mark.parametrize("interruption", ["target", "input", "cancel", "open", "close", "close_reopen"])
def test_late_proposal_cannot_survive_changed_owner(window, tmp_path, interruption):
    inputs = window.current_inputs()
    proposal = suggest_parameters(inputs, SuggestionTargets(.9, .45))
    token = window._suggestion_generation
    if interruption == "target":
        window.suggestion_ripple.setValue(.7)
    elif interruption == "input":
        window.input_widgets["vin_v"].setValue(15)
    elif interruption == "cancel":
        QTest.mouseClick(window.cancel_suggestion_button, Qt.MouseButton.LeftButton)
    elif interruption == "open":
        path = tmp_path / "saved.json"
        window.save_to(path)
        window._start_project_open(path)
    else:
        window.close()
        if interruption == "close_reopen":
            window.show()
    window._on_parameter_proposal((token, proposal, None))
    assert window.parameter_proposal is None
    assert not window.apply_suggestion_button.isEnabled()
    assert window.current_inputs() != proposal.inputs


def test_real_async_cancel_and_repeat_uses_only_latest_targets(window):
    window.set_inputs(PRESETS["T18"])
    wait_for(lambda: window.result is not None)
    window.tabs.setCurrentWidget(window.suggestion_tab)
    window.suggestion_ratio.setChecked(True)
    QTest.mouseClick(window.suggest_button, Qt.MouseButton.LeftButton)
    old_events = tuple(window._suggestion_events)
    QTest.mouseClick(window.cancel_suggestion_button, Qt.MouseButton.LeftButton)
    assert old_events and all(event.is_set() for event in old_events)
    window.suggestion_ripple.setValue(1.4)
    propose(window)
    assert window.parameter_proposal.targets.ripple_pp_a == 1.4
    assert window.parameter_proposal.result.metrics["inductor_ripple_pp_a"] == pytest.approx(1.4, rel=2e-4)


def test_target_failure_does_not_apply_preset_or_retain_old_proposal(window):
    original = window.current_inputs()
    propose(window)
    window.suggestion_duty.setValue(.1)
    QTest.mouseClick(window.suggest_button, Qt.MouseButton.LeftButton)
    wait_for(lambda: "无有效建议" in window.suggestion_output.toPlainText())
    assert window.current_inputs() == original
    assert window.parameter_proposal is None
    assert not window.apply_suggestion_button.isEnabled()


def test_edit_after_apply_removes_stale_provenance(window, tmp_path):
    propose(window)
    QTest.mouseClick(window.apply_suggestion_button, Qt.MouseButton.LeftButton)
    assert window._applied_suggestion is not None
    window.set_inputs(replace(window.current_inputs(), vin_v=13))
    assert window._applied_suggestion is None
    path = tmp_path / "edited.json"
    window.save_to(path)
    assert json.loads(path.read_text(encoding="utf-8"))["parameter_suggestion"] is None

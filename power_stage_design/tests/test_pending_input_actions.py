"""Keyboard edits must be consumed before non-focus-taking PSD actions."""
from __future__ import annotations

import csv
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6.QtWidgets")
from PySide6.QtCore import QCoreApplication, QEvent, QEventLoop, QLocale, Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication,
    QDialogButtonBox,
    QFileDialog,
    QLineEdit,
    QToolButton,
)

from power_stage_design.gui import PowerStageMainWindow
from power_stage_design.project import read_project


@pytest.fixture
def desktop():
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    view = PowerStageMainWindow()
    view.show()
    app.processEvents()
    yield view
    view.close()
    view.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()


def edit(box, text):
    box.setFocus()
    box.selectAll()
    QTest.keyClick(box, Qt.Key.Key_Backspace)
    if text:
        QTest.keyClicks(box, text)


def click(window, label):
    button = next(button for button in window.findChildren(QToolButton) if button.text() == label)
    QTest.mouseClick(button, Qt.MouseButton.LeftButton)


def settle(window, seconds=20):
    deadline = time.monotonic() + seconds
    loop = QEventLoop()
    timer = QTimer()
    timer.setSingleShot(True)
    timer.timeout.connect(loop.quit)
    # QTest.qWait repeatedly holds the binding's GIL during its sleep. Let the
    # real Qt event loop schedule Python workers and their queued completion.
    try:
        while (window._jobs or window._timer.isActive() or window._loop_timer.isActive()) and time.monotonic() < deadline:
            timer.start(10)
            loop.exec()
    finally:
        timer.stop()
    assert not window._jobs and not window._timer.isActive() and not window._loop_timer.isActive()


def test_settle_processes_queued_completion_and_both_timers():
    app = QApplication.instance() or QApplication([])
    window = SimpleNamespace(_jobs={"pending": object()}, _timer=QTimer(), _loop_timer=QTimer())
    completed = []

    def complete():
        completed.append(window._jobs.pop("pending"))

    window._timer.setSingleShot(True)
    window._loop_timer.setSingleShot(True)
    window._timer.timeout.connect(complete)
    window._timer.start(10)
    window._loop_timer.start(20)
    try:
        settle(window)
        assert len(completed) == 1
        assert not window._jobs and not window._timer.isActive() and not window._loop_timer.isActive()
        app.processEvents()
        assert len(completed) == 1
    finally:
        window._timer.stop()
        window._loop_timer.stop()


@pytest.mark.parametrize("pending_kind", ["job", "stage_timer", "loop_timer"])
def test_settle_rejects_never_complete_work_at_original_deadline(pending_kind):
    app = QApplication.instance() or QApplication([])
    window = SimpleNamespace(_jobs={}, _timer=QTimer(), _loop_timer=QTimer())
    if pending_kind == "job":
        window._jobs["pending"] = object()
    else:
        timer = window._timer if pending_kind == "stage_timer" else window._loop_timer
        timer.start(10)
    started = time.monotonic()
    try:
        with pytest.raises(AssertionError):
            settle(window, seconds=.03)
        assert time.monotonic() - started >= .03
        assert window._jobs or window._timer.isActive() or window._loop_timer.isActive()
        app.processEvents()
    finally:
        window._timer.stop()
        window._loop_timer.stop()


def test_settle_propagates_deleted_timer_error_to_caller():
    from shiboken6 import isValid

    app = QApplication.instance() or QApplication([])
    window = SimpleNamespace(_jobs={}, _timer=QTimer(), _loop_timer=QTimer())
    window._timer.start(1000)
    QTimer.singleShot(0, window._timer.deleteLater)
    try:
        with pytest.raises(RuntimeError, match="already deleted"):
            settle(window, seconds=.05)
        assert not isValid(window._timer)
        app.processEvents()
    finally:
        if isValid(window._timer):
            window._timer.stop()
        window._loop_timer.stop()


def test_recalculate_commits_pending_keyboard_input(desktop):
    box = desktop.input_widgets["vin_v"]
    edit(box, "24")
    assert box.value() == 12  # Reproduces the actual uncommitted editor state.
    click(desktop, "重算")
    assert box.value() == 24
    assert desktop.result.inputs.vin_v == 24
    assert desktop.result.duty == pytest.approx(5 / 24)
    assert desktop.result.design_id == desktop.current_inputs().design_id


@pytest.mark.parametrize("text", ["0", "", "."])
def test_recalculate_rejects_invalid_or_intermediate_input(desktop, text):
    box = desktop.input_widgets["vin_v"]
    edit(box, text)
    click(desktop, "重算")
    settle(desktop)
    assert desktop.result is None
    assert not desktop.save_action.isEnabled()
    assert not desktop.report_action.isEnabled()
    assert not desktop.csv_action.isEnabled()
    assert "有效" in desktop.status.text() or "无效" in desktop.status.text()


def test_locale_decimal_is_committed_without_manual_float_parsing(desktop):
    box = desktop.input_widgets["vin_v"]
    box.setLocale(QLocale(QLocale.Language.German, QLocale.Country.Germany))
    edit(box, "24,5")
    click(desktop, "重算")
    assert desktop.result.inputs.vin_v == 24.5
    assert box.text().startswith("24,5")


def choose_file(dialog, path):
    # QFileDialog's completer retains selected directory rows if keys are
    # injected into an unfocused editor. accept() then navigates to that
    # directory instead of accepting the otherwise-correct filename text.
    dialog.activateWindow()
    assert QTest.qWaitForWindowActive(dialog, 2000), "file dialog did not activate"
    filename = dialog.findChild(QLineEdit, "fileNameEdit")
    QTest.mouseClick(filename, Qt.MouseButton.LeftButton)
    assert filename.hasFocus(), "filename editor did not receive focus"
    filename.selectAll()
    QTest.keyClicks(filename, str(path))
    assert filename.text() == str(path)
    assert [Path(name) for name in dialog.selectedFiles()] == [Path(path)], dialog.selectedFiles()
    save = dialog.findChild(QDialogButtonBox, "buttonBox").button(QDialogButtonBox.StandardButton.Save)
    assert save.isEnabled()
    QTest.mouseClick(save, Qt.MouseButton.LeftButton)


def test_real_file_dialog_chooser_activates_first_window(tmp_path):
    # No prior QApplication/window activation can mask the regression. Keep
    # the real chooser, completer, typed absolute path and Save-button signal.
    script = """
import sys
from pathlib import Path
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QFileDialog
from power_stage_design.tests.test_pending_input_actions import choose_file
app = QApplication([])
app.setQuitOnLastWindowClosed(False)
assert app.activeWindow() is None
errors, events = [], []
def choose():
    dialog = app.activeModalWidget()
    try:
        assert isinstance(dialog, QFileDialog)
        dialog.accepted.connect(lambda: events.append('accepted'))
        dialog.rejected.connect(lambda: events.append('rejected'))
        choose_file(dialog, sys.argv[1])
    except Exception as exc:
        errors.append(repr(exc))
        if dialog:
            dialog.reject()
def timeout():
    errors.append('watchdog timeout')
    if app.activeModalWidget():
        app.activeModalWidget().reject()
chooser = QTimer()
chooser.setSingleShot(True)
chooser.timeout.connect(choose)
chooser.start(250)
watchdog = QTimer()
watchdog.setSingleShot(True)
watchdog.timeout.connect(timeout)
watchdog.start(5000)
path, _ = QFileDialog.getSaveFileName(None, 'Save', 'default.json', 'JSON (*.json)')
assert not errors, errors
assert events == ['accepted'], events
assert Path(path) == Path(sys.argv[1]), (path, sys.argv[1])
"""
    completed = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path / "pending.json")],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20, check=False,
        env={**os.environ, "XDG_CONFIG_HOME": str(tmp_path), "XDG_CACHE_HOME": str(tmp_path)},
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr


@pytest.mark.parametrize("action,suffix", [("保存项目", "json"), ("导出报告", "html"), ("导出波形 CSV", "csv")])
def test_pending_edit_exports_from_real_file_dialog(desktop, tmp_path, action, suffix):
    path = tmp_path / ("pending." + suffix)
    box = desktop.input_widgets["vin_v"]
    edit(box, "24")
    observations = []
    dialog_events = []

    def choose():
        dialog = QApplication.activeModalWidget()
        if not isinstance(dialog, QFileDialog):
            observations.append("missing dialog")
            return
        try:
            observations.append(desktop.result.inputs.vin_v if desktop.result else None)
            dialog.accepted.connect(lambda: dialog_events.append("accepted"))
            dialog.rejected.connect(lambda: dialog_events.append("rejected"))
            choose_file(dialog, path)
        except (AssertionError, AttributeError, RuntimeError, TypeError) as exc:
            observations.append(str(exc))
            dialog.reject()

    chooser = QTimer(desktop)
    chooser.setSingleShot(True)
    chooser.timeout.connect(choose)
    watchdog = QTimer(desktop)
    watchdog.setSingleShot(True)

    def timed_out():
        dialog_events.append("watchdog timeout")
        if QApplication.activeModalWidget():
            QApplication.activeModalWidget().reject()

    watchdog.timeout.connect(timed_out)
    chooser.start(250)
    watchdog.start(5000)
    click(desktop, action)
    chooser.stop()
    watchdog.stop()
    settle(desktop)
    assert observations == [24]
    assert dialog_events == ["accepted"]
    assert path.exists(), desktop.status.text()
    identity = desktop.current_inputs().design_id
    if suffix == "json":
        document = json.loads(path.read_text(encoding="utf-8"))
        assert document["inputs"]["vin_v"] == 24
        assert document["result_identity"] == identity
        desktop.open_from(path)
        assert desktop.result.inputs == read_project(path)[0]
        assert desktop.result.design_id == identity
    elif suffix == "html":
        assert identity in path.read_text(encoding="utf-8")
    else:
        with path.open(encoding="utf-8", newline="") as stream:
            rows = list(csv.DictReader(stream))
        assert len(rows) == 2000
        assert {row["design_id"] for row in rows} == {identity}


@pytest.mark.parametrize("action", ["保存项目", "导出报告", "导出波形 CSV"])
def test_empty_input_does_not_open_a_file_dialog_or_restore_old_value(desktop, monkeypatch, action):
    calls = []
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *_: (calls.append(True) or "", ""))
    edit(desktop.input_widgets["vin_v"], "")
    click(desktop, action)
    assert not calls
    assert desktop.result is None
    assert desktop.input_widgets["vin_v"].text() == ""


def test_suggestion_focus_transfer_does_not_repair_empty_input_to_old_value(desktop):
    desktop.tabs.setCurrentWidget(desktop.suggestion_tab)
    box = desktop.input_widgets["vin_v"]
    edit(box, "")
    QTest.mouseClick(desktop.suggest_button, Qt.MouseButton.LeftButton)
    settle(desktop)
    assert box.text() == ""
    assert desktop.result is None
    assert desktop.parameter_proposal is None


def test_pending_loop_edit_is_recomputed_before_project_snapshot(desktop, tmp_path):
    desktop.analyze_loop()
    desktop.tabs.setCurrentWidget(desktop.loop_tab)
    box = desktop.loop_widgets["esr_ohm"]
    edit(box, ".057")
    desktop.save_to(tmp_path / "loop.json")
    document = json.loads((tmp_path / "loop.json").read_text(encoding="utf-8"))
    assert document["loop_settings"]["plant"]["esr_ohm"] == .057
    assert desktop._loop_settings["plant"]["esr_ohm"] == .057


def test_direct_save_and_report_commit_pending_input(desktop, tmp_path):
    edit(desktop.input_widgets["vin_v"], "24")
    desktop.save_to(tmp_path / "saved.json")
    assert read_project(tmp_path / "saved.json")[0].vin_v == 24
    edit(desktop.input_widgets["vin_v"], "30")
    desktop.report_to(tmp_path / "report.html")
    assert desktop.result.inputs.vin_v == 30
    assert desktop.current_inputs().design_id in (tmp_path / "report.html").read_text(encoding="utf-8")


def test_uncommitted_edit_cancels_inflight_project_snapshot(desktop, tmp_path, monkeypatch):
    import threading

    from power_stage_design import gui

    original = gui.project_document
    entered, release = threading.Event(), threading.Event()

    def delayed(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return original(*args, **kwargs)

    monkeypatch.setattr(gui, "project_document", delayed)
    path = tmp_path / "late.json"
    desktop._start_project_save(path)
    assert entered.wait(3)
    try:
        edit(desktop.input_widgets["vin_v"], "24")
    finally:
        release.set()
    settle(desktop)
    assert not path.exists()
    assert desktop.input_widgets["vin_v"].text() == "24"


def test_typing_retains_only_labeled_history_without_per_key_solver(desktop, monkeypatch):
    from power_stage_design import gui

    original = gui.calculate_stage
    calls = []

    def counted(*args, **kwargs):
        calls.append(args[0])
        return original(*args, **kwargs)

    monkeypatch.setattr(gui, "calculate_stage", counted)
    previous = desktop.result
    edit(desktop.input_widgets["vin_v"], "24")
    QTest.qWait(200)
    assert not calls
    assert desktop.result is previous
    assert "上次已提交" in desktop.status.text()
    click(desktop, "重算")
    assert len(calls) == 1 and desktop.result.inputs.vin_v == 24


def test_suggestion_preview_and_apply_use_edited_stage_and_target(desktop):
    desktop.tabs.setCurrentWidget(desktop.suggestion_tab)
    edit(desktop.input_widgets["vin_v"], "24")
    edit(desktop.suggestion_ripple, ".7")
    QTest.mouseClick(desktop.suggest_button, Qt.MouseButton.LeftButton)
    settle(desktop)
    proposal = desktop.parameter_proposal
    assert proposal is not None
    assert proposal.inputs.vin_v == 24
    assert proposal.targets.ripple_pp_a == .7
    QTest.mouseClick(desktop.apply_suggestion_button, Qt.MouseButton.LeftButton)
    settle(desktop)
    assert desktop.result.design_id == proposal.result.design_id
    assert desktop.current_inputs() == proposal.inputs


@pytest.mark.parametrize("target", ["stage", "suggestion"])
def test_typing_after_preview_invalidates_apply_without_enter(desktop, target):
    desktop.tabs.setCurrentWidget(desktop.suggestion_tab)
    QTest.mouseClick(desktop.suggest_button, Qt.MouseButton.LeftButton)
    settle(desktop)
    assert desktop.parameter_proposal is not None
    box = desktop.input_widgets["vin_v"] if target == "stage" else desktop.suggestion_ripple
    edit(box, "24" if target == "stage" else ".7")
    assert desktop.parameter_proposal is None
    assert not desktop.apply_suggestion_button.isEnabled()
    assert desktop._applied_suggestion is None


def test_empty_suggestion_target_does_not_revert_on_preview(desktop):
    desktop.tabs.setCurrentWidget(desktop.suggestion_tab)
    edit(desktop.suggestion_ripple, "")
    QTest.mouseClick(desktop.suggest_button, Qt.MouseButton.LeftButton)
    settle(desktop)
    assert desktop.suggestion_ripple.text() == ""
    assert desktop.parameter_proposal is None
    assert "无效" in desktop.suggestion_output.toPlainText()


def test_pending_loop_target_is_saved_but_does_not_replace_actual_response(desktop, tmp_path):
    desktop.analyze_loop()
    actual = desktop.loop_result
    desktop.tabs.setCurrentWidget(desktop.loop_tab)
    edit(desktop.target_fc, "3750")
    desktop.save_to(tmp_path / "target.json")
    assert desktop.loop_result is actual
    assert read_project(tmp_path / "target.json")[1]["loop_request"]["target_crossover_hz"] == 3750


def test_empty_loop_component_rejects_report_without_reverting(desktop, monkeypatch):
    desktop.analyze_loop()
    desktop.tabs.setCurrentWidget(desktop.loop_tab)
    box = desktop.comp_widgets["r2_ohm"]
    edit(box, "")
    calls = []
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *_: (calls.append(True) or "", ""))
    click(desktop, "导出报告")
    assert not calls and box.text() == ""
    assert desktop.loop_result is None and desktop._loop_settings is None
    assert "无效" in desktop.loop_status.text()


def test_empty_advanced_physics_input_rejects_recalculate(desktop):
    from power_stage_design.models import DesignInputs
    from power_stage_design.qr_dynamics import QROptions

    desktop.set_inputs(DesignInputs("T11", 48, 12, 1, 180e3, 200e-6,
        turns_ratio_np_ns=8, resonant_capacitance_f=500e-12,
        qr_options=QROptions(valley_policy="selected", valley_index=2, on_resistance_ohm=.1)))
    settle(desktop, seconds=90)
    assert desktop.result is not None
    desktop.tabs.setCurrentIndex(6)
    box = desktop.advanced_model.qr_widgets["detector_delay_s"]
    edit(box, "")
    click(desktop, "重算")
    settle(desktop)
    assert box.text() == "" and desktop.result is None
    assert not desktop.report_action.isEnabled()


@pytest.mark.parametrize("repaired", ["12", "24"])
def test_invalid_enter_does_not_revert_and_typing_recovers(desktop, repaired):
    box = desktop.input_widgets["vin_v"]
    edit(box, "")
    QTest.keyClick(box, Qt.Key.Key_Return)
    assert box.text() == "" and desktop.result is None
    QTest.keyClicks(box, repaired)
    QTest.keyClick(box, Qt.Key.Key_Return)
    settle(desktop)
    assert desktop.result.inputs.vin_v == float(repaired)


def test_untouched_exact_snapshot_is_preserved_but_explicit_edit_commits_precision(desktop, tmp_path):
    from dataclasses import replace

    original = replace(desktop.result.inputs, vin_v=12.123456789123)
    desktop.set_inputs(original)
    desktop.save_to(tmp_path / "exact.json")
    assert read_project(tmp_path / "exact.json")[0] == original
    box = desktop.input_widgets["vin_v"]
    edit(box, "12.123456789")
    desktop.save_to(tmp_path / "edited.json")
    assert read_project(tmp_path / "edited.json")[0].vin_v == 12.123456789

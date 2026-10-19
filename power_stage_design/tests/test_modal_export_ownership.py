"""Real Qt modal boundaries retain the identity of the initiating action.

Only native file choosers are replaced to deliver nested edits deterministically;
all calculations, workers, project readers and artifact writers are production.
Warnings are captured explicitly so a regression cannot hang in a popup.
"""
from __future__ import annotations

import csv
import json
import os
import time
from dataclasses import replace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="module")
def app():
    qt = pytest.importorskip("PySide6.QtWidgets")
    application = qt.QApplication.instance() or qt.QApplication([])
    yield application
    application.processEvents()


@pytest.fixture
def desktop(app, monkeypatch):
    from PySide6.QtCore import QCoreApplication, QEvent
    from PySide6.QtWidgets import QMessageBox

    from power_stage_design.gui import PowerStageMainWindow

    window = PowerStageMainWindow()
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: warnings.append(args[1:]))
    window.show()
    app.processEvents()
    yield window, warnings
    # PlotWidget.close() detaches plots; Qt must delete the intact owner tree.
    window.close()
    window.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()


def _settle(app, window):
    deadline = time.monotonic() + 10
    while (window._jobs or window.toolbox._workers) and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.002)
    app.processEvents()
    assert not window._jobs and not window.toolbox._workers


def _choose(monkeypatch, path, nested=lambda: None, *, opening=False):
    from PySide6.QtWidgets import QFileDialog

    def choose(*args, **kwargs):
        nested()
        return str(path), ""

    monkeypatch.setattr(QFileDialog, "getOpenFileName" if opening else "getSaveFileName", choose)


def _stage_export(app, window, action):
    getattr(window, {"save": "_save_dialog", "report": "_report_dialog", "csv": "_csv_dialog"}[action])()
    _settle(app, window)


def _assert_stage_artifact(path, action, result):
    if action == "save":
        document = json.loads(path.read_text(encoding="utf-8"))
        assert document["result_identity"] == result.design_id
        assert document["inputs"]["vin_v"] == result.inputs.vin_v
    elif action == "report":
        text = path.read_text(encoding="utf-8")
        assert result.design_id in text and "<svg" in text
    else:
        with path.open(encoding="utf-8", newline="") as stream:
            rows = list(csv.DictReader(stream))
        assert len(rows) == 2000
        assert {row["design_id"] for row in rows} == {result.design_id}


def _edit_stage(window, change):
    if change in {"clear", "clear_new", "same_recompute"}:
        old = window.result.inputs
        window.input_widgets["vin_v"].setValue(old.vin_v + 1)
        window._timer.stop()
        assert window.result is None
        if change == "clear_new":
            window.calculate()
        elif change == "same_recompute":
            window.set_inputs(old)
    elif change == "loop":
        window.comp_widgets["r2_ohm"].setValue(window.comp_widgets["r2_ohm"].value() + 1)
        window.analyze_loop()
    elif change == "device":
        window.device_combo.setCurrentIndex(1)
    elif change == "corner":
        window.corner_fields["input_voltages_v"].setText("12, 13")
    elif change in {"close", "close_reopen"}:
        assert window.close()
        if change == "close_reopen":
            window.show()
    else:
        raise AssertionError(change)


@pytest.mark.parametrize("action", ["save", "report", "csv"])
@pytest.mark.parametrize("change", ["clear", "clear_new", "same_recompute", "close", "close_reopen"])
def test_stage_modal_cancels_old_click_and_fresh_click_exports_current(
    app, desktop, monkeypatch, tmp_path, action, change,
):
    window, warnings = desktop
    path = tmp_path / {"save": "project.json", "report": "report.html", "csv": "waveforms.csv"}[action]
    historical = b"previous user artifact\n"
    path.write_bytes(historical)
    _choose(monkeypatch, path, lambda: _edit_stage(window, change))
    _stage_export(app, window, action)
    assert path.read_bytes() == historical
    assert not warnings
    window.show()
    if window.result is None:
        window.calculate()
    current = window.result
    assert current is not None
    _choose(monkeypatch, path)
    _stage_export(app, window, action)
    _assert_stage_artifact(path, action, current)
    assert not warnings


@pytest.mark.parametrize("action", ["save", "report"])
@pytest.mark.parametrize("change", ["loop", "device", "corner"])
def test_project_modal_binds_loop_device_and_corner_revisions(
    app, desktop, monkeypatch, tmp_path, action, change,
):
    window, warnings = desktop
    window.analyze_loop()
    assert window.loop_result is not None
    path = tmp_path / ("project.json" if action == "save" else "report.html")
    _choose(monkeypatch, path, lambda: _edit_stage(window, change))
    _stage_export(app, window, action)
    assert not path.exists()
    assert not warnings
    _choose(monkeypatch, path)
    _stage_export(app, window, action)
    _assert_stage_artifact(path, action, window.result)
    if change == "device":
        assert window.device_combo.currentData().part_number in path.read_text(encoding="utf-8")
    assert not warnings


@pytest.mark.parametrize("action", ["save", "report", "csv"])
def test_stage_navigation_does_not_cancel_current_export(app, desktop, monkeypatch, tmp_path, action):
    window, warnings = desktop
    original = window.result
    path = tmp_path / {"save": "project.json", "report": "report.html", "csv": "waveforms.csv"}[action]

    def navigate():
        window.tabs.setCurrentWidget(window.loop_tab)
        window.select_component("D1")
        window.tabs.setCurrentIndex(0)

    _choose(monkeypatch, path, navigate)
    _stage_export(app, window, action)
    assert window.result is original
    _assert_stage_artifact(path, action, original)
    assert not warnings


def test_csv_does_not_bind_unrelated_loop_device_corner_changes(app, desktop, monkeypatch, tmp_path):
    window, warnings = desktop
    original = window.result
    path = tmp_path / "waveforms.csv"

    def revise_non_waveform_data():
        for change in ("loop", "device", "corner"):
            _edit_stage(window, change)

    _choose(monkeypatch, path, revise_non_waveform_data)
    _stage_export(app, window, "csv")
    _assert_stage_artifact(path, "csv", original)
    assert not warnings


def test_report_cancels_if_pending_corner_result_arrives_inside_modal(app, desktop, monkeypatch, tmp_path):
    window, warnings = desktop
    for key, value in {"input_voltages_v": "12", "output_currents_a": "3",
                       "inductance_factors": "1", "capacitance_factors": "1"}.items():
        window.corner_fields[key].setText(value)
    window.run_corners()
    assert window.corner_sweep is None
    path = tmp_path / "report.html"
    _choose(monkeypatch, path, lambda: _settle(app, window))
    window._report_dialog()
    assert window.corner_sweep is not None
    assert not path.exists()
    _choose(monkeypatch, path)
    window._report_dialog()
    assert "逐指标多工况极值" in path.read_text(encoding="utf-8")
    assert not warnings


@pytest.mark.parametrize("action", ["export", "inputs"])
@pytest.mark.parametrize("change", ["clear", "clear_new", "same_recompute", "tool", "close", "close_reopen"])
def test_toolbox_modal_cancels_old_click_and_fresh_click_exports_current(
    app, desktop, monkeypatch, tmp_path, action, change,
):
    window, warnings = desktop
    toolbox = window.toolbox
    toolbox.select_tool("X02")
    toolbox.calculate()
    assert toolbox.latest_result is not None
    path = tmp_path / "toolbox.json"
    historical = b"previous toolbox artifact\n"
    path.write_bytes(historical)

    nested_status = []

    def mutate():
        if change in {"clear", "clear_new", "same_recompute"}:
            toolbox.input_widgets["delta_current_a"].setText("2")
            assert toolbox.latest_result is None
            if change == "same_recompute":
                toolbox.input_widgets["delta_current_a"].setText("2.5")
            if change != "clear":
                toolbox.calculate()
        elif change == "tool":
            toolbox.select_tool("X10")
            toolbox.calculate()
        else:
            assert window.close()
            if change == "close_reopen":
                window.show()
        nested_status.append(toolbox.status.text())

    dialog = toolbox._export_dialog if action == "export" else toolbox._save_inputs_dialog
    _choose(monkeypatch, path, mutate)
    dialog()
    assert toolbox.status.text() == nested_status[0]
    assert path.read_bytes() == historical
    assert not warnings
    window.show()
    if toolbox.latest_result is None:
        toolbox.calculate()
    _choose(monkeypatch, path)
    dialog()
    document = json.loads(path.read_text(encoding="utf-8"))
    if action == "export":
        assert document["result_id"] == toolbox.latest_result.result_id
        assert document["tool_id"] == toolbox.current_tool_id
    else:
        assert document["state"] == toolbox.state()
    assert not warnings


@pytest.mark.parametrize("action", ["export", "inputs"])
@pytest.mark.parametrize("imported", [False, True])
def test_toolbox_stage_edits_cancel_only_bound_imports(app, desktop, monkeypatch, tmp_path, action, imported):
    window, warnings = desktop
    toolbox = window.toolbox
    toolbox.select_tool("X02")
    if imported:
        toolbox.set_values({"control": "VMC"})
        toolbox.import_stage()
    toolbox.calculate()
    assert toolbox.latest_result is not None
    path = tmp_path / "toolbox.json"
    _choose(monkeypatch, path, lambda: _edit_stage(window, "clear_new"))
    (toolbox._export_dialog if action == "export" else toolbox._save_inputs_dialog)()
    assert path.exists() is not imported
    assert not warnings


@pytest.mark.parametrize("owner", ["stage", "toolbox"])
@pytest.mark.parametrize("change", ["edit", "close", "close_reopen"])
def test_open_modal_does_not_replace_newer_or_closed_state(app, desktop, monkeypatch, tmp_path, owner, change):
    from power_stage_design.gui import PRESETS
    from power_stage_design.project import save_project

    window, warnings = desktop
    path = tmp_path / "input.json"
    if owner == "stage":
        save_project(replace(PRESETS["T01"], vin_v=14), path)
        dialog = window._open_dialog
    else:
        toolbox = window.toolbox
        toolbox.select_tool("X02")
        toolbox.set_values({"delta_current_a": 1.5})
        toolbox.calculate()
        toolbox.save_inputs_to(path)
        toolbox.set_values({"delta_current_a": 2.5})
        toolbox.calculate()
        dialog = toolbox._open_inputs_dialog

    def mutate():
        if change == "edit":
            if owner == "stage":
                _edit_stage(window, "clear_new")
            else:
                toolbox.input_widgets["delta_current_a"].setText("2")
                toolbox.calculate()
        else:
            _edit_stage(window, change)

    _choose(monkeypatch, path, mutate, opening=True)
    dialog()
    _settle(app, window)
    if owner == "stage":
        assert window.result.inputs.vin_v == (13 if change == "edit" else 12)
    else:
        assert toolbox.state()["values"]["delta_current_a"] == (2 if change == "edit" else 2.5)
    window.show()
    _choose(monkeypatch, path, opening=True)
    dialog()
    _settle(app, window)
    if owner == "stage":
        assert window.result.inputs.vin_v == 14
    else:
        assert toolbox.state()["values"]["delta_current_a"] == 1.5
    assert not warnings


def _dialogs(window):
    return {
        "save": window._save_dialog,
        "report": window._report_dialog,
        "csv": window._csv_dialog,
        "open": window._open_dialog,
        "toolbox_export": window.toolbox._export_dialog,
        "toolbox_inputs": window.toolbox._save_inputs_dialog,
        "toolbox_open": window.toolbox._open_inputs_dialog,
    }


@pytest.mark.parametrize("action", ["save", "report", "csv", "toolbox_export", "toolbox_inputs"])
def test_invalid_actions_do_not_open_a_chooser(desktop, monkeypatch, action):
    from PySide6.QtWidgets import QFileDialog

    window, warnings = desktop
    window.invalidate()
    window._timer.stop()
    window.toolbox.cancel_pending()
    calls = []
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *args: (calls.append(args) or "", ""))
    _dialogs(window)[action]()
    assert not calls and not warnings


@pytest.mark.parametrize("action", ["save", "report", "csv", "open", "toolbox_export", "toolbox_inputs", "toolbox_open"])
def test_closed_owner_does_not_open_a_chooser(desktop, monkeypatch, action):
    from PySide6.QtWidgets import QFileDialog

    window, warnings = desktop
    window.toolbox.calculate()
    assert window.close()
    calls = []
    for name in ("getSaveFileName", "getOpenFileName"):
        monkeypatch.setattr(QFileDialog, name, lambda *args: (calls.append(args) or "", ""))
    _dialogs(window)[action]()
    assert not calls and not warnings


@pytest.mark.parametrize("action", ["toolbox_export", "toolbox_inputs", "toolbox_open"])
def test_closed_toolbox_itself_does_not_open_a_chooser(desktop, monkeypatch, action):
    from PySide6.QtWidgets import QFileDialog

    window, warnings = desktop
    window.toolbox.calculate()
    assert window.toolbox.close()
    calls = []
    for name in ("getSaveFileName", "getOpenFileName"):
        monkeypatch.setattr(QFileDialog, name, lambda *args: (calls.append(args) or "", ""))
    _dialogs(window)[action]()
    assert not calls and not warnings


@pytest.mark.parametrize("action", ["save", "report", "csv", "open", "toolbox_export", "toolbox_inputs", "toolbox_open"])
def test_canceling_chooser_keeps_current_results(desktop, monkeypatch, action):
    window, warnings = desktop
    window.toolbox.calculate()
    stage, tool = window.result, window.toolbox.latest_result
    _choose(monkeypatch, "", opening=action.endswith("open"))
    _dialogs(window)[action]()
    assert window.result is stage and window.toolbox.latest_result is tool
    assert not window._jobs and not warnings

"""Real Qt widget/event-loop workflows, with no mocked numerical calculations.

QT_QPA_PLATFORM=offscreen exercises actual Linux rendering. It does not claim
native Windows/macOS or a human-visible X11 session. PSD_EVIDENCE_DIR requests
real QWidget.grab() PNGs and reproducible project/report/CSV artifacts.
"""
from __future__ import annotations

import csv
import json
import os
from dataclasses import asdict, replace
from pathlib import Path

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
qt = pytest.importorskip("PySide6.QtWidgets")
from PySide6.QtCore import QCoreApplication, QEvent, QPoint, Qt
from PySide6.QtTest import QTest

from power_stage_design.gui import PRESETS, PowerStageMainWindow
from power_stage_design.project import save_project
from power_stage_design.report import write_report, write_waveform_csv
from power_stage_design.topologies import calculate_stage


@pytest.fixture(scope="module")
def app():
    application = qt.QApplication.instance() or qt.QApplication([])
    yield application
    application.processEvents()


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


def _assert_plots_match_current_result(window, key):
    wave = window.result.waveforms[key]
    for plot, signal in ((window.current_plot, wave.current_a), (window.voltage_plot, wave.voltage_v)):
        items = plot.listDataItems()
        assert len(items) == 1
        t, y = items[0].getData()
        expected_t, expected_y = signal.sample(count=800, periods=2)
        np.testing.assert_array_equal(t, expected_t)
        np.testing.assert_array_equal(y, expected_y)
    assert float(window.stress_table.item(0, 4).text()) == pytest.approx(window.result.stress[key]["current_rms_a"], rel=1e-7)


@pytest.mark.parametrize("topology", ["T01", "T02", "T09"])
def test_real_schematic_click_selects_matching_waveform_and_stress(app, window, topology):
    from power_stage_design.schematic import layout
    window.set_inputs(PRESETS[topology])
    app.processEvents()
    assert window.result.topology_id == topology
    assert window.report_action.isEnabled()
    element = next(element for element in layout(topology)[0] if element.key == "D1")
    point = QPoint(round(element.x * window.schematic.width() / 740),
                   round(element.y * window.schematic.height() / 285))
    QTest.mouseClick(window.schematic, Qt.MouseButton.LeftButton, pos=point)
    app.processEvents()
    assert window.component_combo.currentData() == "D1"
    assert window.schematic.selected == "D1"
    _assert_plots_match_current_result(window, "D1")


def test_rapid_edits_invalidate_immediately_and_only_latest_input_is_exported(app, window, tmp_path):
    initial_id = window.result.design_id
    window.analyze_loop()
    assert window.loop_result is not None
    for value in (12.1, 12.2, 12.7, 13.25):
        window.input_widgets["vin_v"].setValue(value)
        assert window.result is None
        assert window.loop_result is None
        assert not window.report_action.isEnabled()
        assert not window.current_plot.listDataItems()
        assert not window.loop_gain_plot.listDataItems()
    stale = tmp_path / "stale.html"
    with pytest.raises(ValueError):
        window.report_to(stale)
    assert not stale.exists()
    QTest.qWait(180)
    app.processEvents()
    assert window.result.inputs.vin_v == 13.25
    assert window.result.design_id != initial_id
    report = tmp_path / "latest.html"
    window.report_to(report)
    text = report.read_text(encoding="utf-8")
    assert window.result.design_id in text
    assert initial_id not in text
    assert "13.25" in text
    assert "此报告没有当前工况的有效模拟环路结果" in text
    window.input_widgets["vout_v"].setValue(20.)
    QTest.qWait(180)
    assert window.result is None
    assert not window.report_action.isEnabled()
    assert not window.current_plot.listDataItems()
    assert "未得到有效设计" in window.status.text()


@pytest.mark.parametrize("topology", ["T01", "T02", "T09"])
def test_save_reopen_recomputes_stage_loop_and_report(app, window, tmp_path, topology):
    window.set_inputs(PRESETS[topology])
    if topology == "T01":
        window.analyze_loop()
        assert window.loop_result is not None
    window.device_combo.setCurrentIndex(1)
    saved_id = window.result.design_id
    saved = tmp_path / f"{topology}.json"
    window.save_to(saved)
    document = json.loads(saved.read_text(encoding="utf-8"))
    assert document["result_identity"] == saved_id
    assert "waveforms" not in document  # Recompute, do not trust old plotted data.
    window.set_inputs(replace(PRESETS[topology], iout_a=1.5))
    window.open_from(saved)
    assert window.result.design_id == saved_id
    assert window.result.inputs == PRESETS[topology]
    if topology == "T01":
        assert window.loop_result is not None
        assert len(window.loop_gain_plot.listDataItems()) == 4
    assert window.device_combo.currentData().part_number == document["selected_device"]["part_number"]
    output = tmp_path / f"{topology}.html"
    window.report_to(output)
    text = output.read_text(encoding="utf-8")
    assert saved_id in text
    assert "<svg" in text
    assert "软件稳态模型结果" in text
    assert document["selected_device"]["part_number"] in text


@pytest.mark.parametrize("mutation", ["schema", "input", "loop_identity", "device_field", "device_value", "corner_request"])
def test_malformed_project_is_rejected_before_current_view_changes(window, tmp_path, mutation):
    window.analyze_loop()
    window.device_combo.setCurrentIndex(1)
    path = tmp_path / "project.json"
    window.save_to(path)
    document = json.loads(path.read_text(encoding="utf-8"))
    if mutation == "schema":
        document["schema_version"] = 999
    elif mutation == "input":
        document["inputs"]["inductance_h"] = -1.
    elif mutation == "loop_identity":
        document["loop_settings"]["plant"]["vin_v"] *= 2
    elif mutation == "device_field":
        document["selected_device"]["part_number"] = "unknown saved device"
        document["selected_device"]["unknown_field"] = 99
        document["inputs"]["vin_v"] = 13.
        document["loop_settings"] = None
    elif mutation == "corner_request":
        document["corner_request"] = {"input_voltages_v": [float("nan")], "output_currents_a": [3.],
                                      "inductance_factors": [1.], "capacitance_factors": [1.]}
        document["inputs"]["vin_v"] = 13.
        document["loop_settings"] = None
    else:
        document["selected_device"]["part_number"] = "unknown saved device"
        document["selected_device"]["vds_max_v"] = -1.
        document["inputs"]["vin_v"] = 13.
        document["loop_settings"] = None
    path.write_text(json.dumps(document), encoding="utf-8")
    previous_id = window.result.design_id
    previous_inputs = window.current_inputs()
    previous_loop = window.loop_result
    with pytest.raises((ValueError, TypeError)):
        window.open_from(path)
    assert window.result.design_id == previous_id
    assert window.current_inputs() == previous_inputs
    assert window.loop_result is previous_loop


def test_saved_device_snapshot_is_not_silently_replaced_by_same_named_database_part(window, tmp_path):
    window.device_combo.setCurrentIndex(1)
    path = tmp_path / "snapshot.json"
    window.save_to(path)
    document = json.loads(path.read_text(encoding="utf-8"))
    document["selected_device"]["vds_max_v"] = 123.456
    path.write_text(json.dumps(document), encoding="utf-8")
    window.open_from(path)
    assert window.device_combo.currentData().vds_max_v == pytest.approx(123.456)
    assert window.device_assessment()["part_number"] == document["selected_device"]["part_number"]


def test_external_high_precision_project_recomputes_same_inputs(window, tmp_path):
    inputs = replace(PRESETS["T01"], vin_v=12.123456789123, inductance_h=47.123456789123e-6)
    path = tmp_path / "precise.json"
    save_project(inputs, path)
    window.open_from(path)
    assert window.result.inputs == inputs
    assert window.result.design_id == calculate_stage(inputs).design_id


def test_close_cancels_pending_recompute_and_fresh_window_has_no_old_state(app, tmp_path):
    first = PowerStageMainWindow()
    first.input_widgets["vin_v"].setValue(99.)
    assert first._timer.isActive()
    assert first.result is None
    first.close()
    assert not first._timer.isActive()
    QTest.qWait(180)
    assert first.result is None
    second = PowerStageMainWindow()
    try:
        assert second.result.inputs == PRESETS["T01"]
        assert second.result.inputs.vin_v == 12.
        assert second.loop_result is None
    finally:
        for widget in (first, second):
            widget.close()
            widget.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        app.processEvents()


@pytest.mark.parametrize("topology", ["T01", "T02", "T09", "T11"])
def test_csv_is_current_identity_and_each_branch_matches_rendered_samples(tmp_path, topology):
    result = calculate_stage(PRESETS[topology])
    path = write_waveform_csv(result, tmp_path / "waveforms.csv", samples=317)
    with path.open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    assert rows
    assert {row["design_id"] for row in rows} == {result.design_id}
    times = np.asarray([float(row["time_s"]) for row in rows])
    period = next(iter(result.waveforms.values())).current_a.period_s
    assert times[-1] == pytest.approx(period * 316 / 317)
    if topology == "T11":
        assert period > 1 / result.inputs.fsw_hz
    for key, wave in result.waveforms.items():
        for suffix, signal in (("current_a", wave.current_a), ("voltage_v", wave.voltage_v)):
            from power_stage_design.tests.test_independent_validation import _wave_value
            assert len(times) == 317 and np.all(np.diff(times) > 0)
            expected_values = [_wave_value(signal, t / signal.period_s) for t in times]
            np.testing.assert_allclose([float(row[f"{key}_{suffix}"]) for row in rows], expected_values, rtol=1e-13, atol=1e-13)


def test_capture_real_qt_workflow_evidence_when_requested(app, window, tmp_path):
    requested = os.environ.get("PSD_EVIDENCE_DIR")
    directory = Path(requested) if requested else tmp_path
    directory.mkdir(parents=True, exist_ok=True)
    for topology in ("T01", "T02", "T09"):
        window.set_inputs(PRESETS[topology])
        window.select_component("Q1")
        window.tabs.setCurrentIndex(0)
        app.processEvents()
        QTest.qWait(40)
        pixmap = window.grab()
        assert not pixmap.isNull()
        assert pixmap.save(str(directory / f"{topology}-qt-offscreen.png"))
        window.save_to(directory / f"{topology}-project.json")
        window.report_to(directory / f"{topology}-report.html")
        write_waveform_csv(window.result, directory / f"{topology}-waveforms.csv")
    window.set_inputs(PRESETS["T01"])
    window.analyze_loop()
    assert window.loop_result is not None
    window.tabs.setCurrentWidget(window.loop_tab)
    app.processEvents()
    QTest.qWait(40)
    assert window.grab().save(str(directory / "T01-loop-qt-offscreen.png"))
    window.save_to(directory / "T01-loop-project.json")
    window.report_to(directory / "T01-loop-report.html")


def test_hidden_and_precise_loop_settings_survive_reopen(window, tmp_path):
    from power_stage_design.loop import Compensator, LoopPlant, analyze_loop
    plant = replace(LoopPlant(), esr_ohm=.02123456789123)
    compensator = replace(Compensator(), network="type_ii_gm", r2_ohm=12345.678912345,
                          output_resistance_ohm=1.23456789123e6)
    settings = {"plant": asdict(plant), "compensator": asdict(compensator)}
    path = tmp_path / "precise-loop.json"
    save_project(PRESETS["T01"], path, loop_settings=settings)
    expected = analyze_loop(plant, compensator)
    window.open_from(path)
    assert window._loop_settings == settings
    np.testing.assert_array_equal(window.loop_result.loop_response, expected.loop_response)


def test_report_rejects_foreign_stage_or_stale_loop_before_writing(window, tmp_path):
    window.analyze_loop()
    assert window.loop_result is not None
    path = tmp_path / "foreign.html"
    other = calculate_stage(replace(PRESETS["T01"], vin_v=13.))
    with pytest.raises(ValueError):
        write_report(other, path, loop_result=window.loop_result, loop_settings=window._loop_settings)
    assert not path.exists()
    stale = replace(window.loop_result, loop_response=window.loop_result.loop_response * 1.01)
    with pytest.raises(ValueError):
        write_report(window.result, path, loop_result=stale, loop_settings=window._loop_settings)
    assert not path.exists()


def test_headless_module_cli_recomputes_saved_project_and_report(tmp_path):
    import subprocess
    import sys
    project = tmp_path / "project.json"
    inputs = replace(PRESETS["T09"], iout_a=.05)
    save_project(inputs, project)
    report = tmp_path / "headless.html"
    completed = subprocess.run([sys.executable, "-m", "power_stage_design", str(project),
                                "--report", str(report)], check=False, capture_output=True, text=True, timeout=30)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert str(report) in completed.stdout
    text = report.read_text(encoding="utf-8")
    assert calculate_stage(inputs).design_id in text
    assert "DCM" in text


def test_launcher_exposes_and_reopens_real_power_stage_workspace(app, monkeypatch):
    import pyqtgraph as pg

    from llc_design.core.spec import LLCDesignSpec
    from llc_design.gui.launcher import (
        WorkspaceApplicationController,
        WorkspaceSelectionDialog,
    )
    from llc_design.gui.main_window import LLCMainWindow
    from pfc_design.gui.main_window import PFCMainWindow
    monkeypatch.setattr(LLCMainWindow, "_auto_check_update", lambda self: None)
    monkeypatch.setattr(PFCMainWindow, "_auto_check_update", lambda self: None)
    dialog = WorkspaceSelectionDialog()
    dialog.show()
    app.processEvents()
    button = dialog.findChild(qt.QPushButton, "power_stage_designer_button")
    assert button is not None and button.isVisible()
    QTest.mouseClick(button, Qt.MouseButton.LeftButton)
    assert dialog.selected_workspace == "power_stage"
    controller = WorkspaceApplicationController(LLCDesignSpec())
    windows = [controller.llc_window, controller.pfc_window, controller.control_window,
               controller.fra_window, controller.dab_window, controller.power_stage_window]
    try:
        controller.show_workspace("power_stage")
        app.processEvents()
        assert controller.power_stage_window.isVisible()
        identity = controller.power_stage_window.result.design_id
        controller.show_workspace("llc")
        app.processEvents()
        assert controller.llc_window.isVisible()
        assert not controller.power_stage_window.isVisible()
        controller.show_workspace("power_stage")
        app.processEvents()
        assert controller.power_stage_window.result.design_id == identity
    finally:
        for view in [dialog, *windows]:
            view.close()
            for graphics in view.findChildren(pg.GraphicsLayoutWidget):
                graphics.close()
            view.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        app.processEvents()


def _drain_synthesis(app, window, timeout=20.):
    import time
    deadline = time.monotonic() + timeout
    while window._jobs and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.005)
    assert not window._jobs, "Synthesis worker did not release its resources"
    assert window._pool.waitForDone(1000)


def test_real_synthesis_is_nonblocking_and_applies_verified_current_result(app, window, tmp_path):
    import time

    from PySide6.QtCore import QTimer
    callback_times = []
    before = time.perf_counter()
    QTimer.singleShot(0, lambda: callback_times.append(time.perf_counter()))
    window.synthesize_loop()
    returned = time.perf_counter()
    assert window._jobs
    assert window.loop_result is None
    app.processEvents()
    assert callback_times, "GUI queue did not run while actual synthesis was active"
    assert returned - before < .2  # Scheduler/CI budget, not numerical performance claim.
    _drain_synthesis(app, window)
    assert window.loop_result is not None, window.loop_status.text()
    assert window._loop_settings["plant"]["vin_v"] == window.result.inputs.vin_v
    assert len(window.loop_gain_plot.listDataItems()) == 4
    window.report_to(tmp_path / "synthesized.html")
    requested = os.environ.get("PSD_EVIDENCE_DIR")
    if requested:
        payload = {"synthesis_dispatch_ms": (returned-before)*1000,
                   "first_gui_event_ms": (callback_times[0]-before)*1000,
                   "completion_ms": (time.perf_counter()-before)*1000,
                   "design_id": window.result.design_id,
                   "scope": "Actual Type III search in Qt QThreadPool; event queue remained live; shared Linux offscreen runtime."}
        Path(requested, "synthesis-responsiveness.json").write_text(json.dumps(payload, indent=2)+"\n", encoding="utf-8")


@pytest.mark.parametrize("edit", ["stage", "compensator", "target", "cancel"])
def test_edit_or_cancel_discards_actual_inflight_synthesis(app, window, edit):
    before = {key: widget.value() for key, widget in window.comp_widgets.items()}
    window.synthesize_loop()
    assert window._jobs
    if edit == "stage":
        window.input_widgets["vin_v"].setValue(13.)
    elif edit == "compensator":
        window.comp_widgets["r2_ohm"].setValue(22.)
        before["r2_ohm"] = 22.
    elif edit == "target":
        window.target_fc.setValue(3500.)
    else:
        window.cancel_synthesis()
    _drain_synthesis(app, window)
    QTest.qWait(180)
    assert window.loop_result is None
    assert {key: widget.value() for key, widget in window.comp_widgets.items()} == before
    if edit == "stage":
        assert window.result.inputs.vin_v == 13.


def test_late_error_from_cancelled_generation_cannot_replace_current_status(app, window):
    window.synthesize_loop()
    old_worker = next(iter(window._jobs.values()))
    window.cancel_synthesis()
    status = window.loop_status.text()
    # Deliberately deliver an already-queued error from a real old job. This
    # tests event ownership; numerical successful workflow is tested above.
    old_worker.signals.error.emit("late old worker error")
    app.processEvents()
    assert window.loop_status.text() == status
    _drain_synthesis(app, window)


def test_close_during_actual_search_cancels_and_reopen_can_calculate(app, window):
    import time
    window.synthesize_loop()
    assert window._jobs
    start = time.perf_counter()
    window.close()
    assert time.perf_counter() - start < 2.
    _drain_synthesis(app, window)
    assert not window._timer.isActive()
    assert window.loop_result is None
    window.show()
    app.processEvents()
    window.synthesize_loop()
    _drain_synthesis(app, window)
    assert window.loop_result is not None


def _set_corner_lists(window, vin="8, 12, 16", current="0.5, 3", lf="1", cf="1"):
    for key, text in zip(window.corner_fields, (vin, current, lf, cf)):
        window.corner_fields[key].setText(text)


@pytest.mark.parametrize("edit", ["base", "compensator", "device", "corner_list", "load_project", "cancel"])
def test_inflight_corner_scan_never_applies_after_bound_inputs_change(app, window, tmp_path, edit):
    window.analyze_loop()
    # Actual loop calculations over 500 corners guarantee a useful cancellation
    # window; the event loop and worker are real, without a substitute solver.
    _set_corner_lists(window, vin=", ".join(str(8+i*.02) for i in range(100)),
                      current="1, 1.5, 2, 2.5, 3")
    path = tmp_path / "different-topology.json"
    save_project(PRESETS["T02"], path)
    window.run_corners()
    assert window._jobs
    worker = next(iter(window._jobs.values()))
    if edit == "base": window.input_widgets["vin_v"].setValue(13.)
    elif edit == "compensator": window.comp_widgets["r2_ohm"].setValue(22.)
    elif edit == "device": window.device_combo.setCurrentIndex(1)
    elif edit == "corner_list": window.corner_fields["input_voltages_v"].setText("10, 14")
    elif edit == "load_project": window.open_from(path)
    else: window.cancel_corners()
    assert window.corner_sweep is None
    assert window.corner_table.rowCount() == 0
    status = window.corner_status.text()
    worker.signals.error.emit("queued error from obsolete corner scan")
    app.processEvents()
    assert window.corner_status.text() == status
    _drain_synthesis(app, window)
    QTest.qWait(180)
    assert window.corner_sweep is None
    assert window.corner_table.rowCount() == 0
    if edit == "load_project": assert window.result.topology_id == "T02"


def test_corner_scan_saved_reopened_recomputed_and_reports_only_matching_identity(app, window, tmp_path):
    window.analyze_loop()
    window.device_combo.setCurrentIndex(1)
    settings = window._loop_settings
    _set_corner_lists(window)
    window.run_corners()
    _drain_synthesis(app, window)
    sweep = window.corner_sweep
    assert sweep is not None and len(sweep.points) == 6
    assert sweep.base_design_id == window.result.design_id
    assert sweep.controller == settings["compensator"]
    assert sweep.selected_device == asdict(window.device_combo.currentData())
    for point in sweep.points:
        result = calculate_stage(point.inputs)
        assert result.design_id == point.design_id
        assert point.metrics["Q1.current_rms_a"] == result.stress["Q1"]["current_rms_a"]
    saved = tmp_path / "corners.json"
    window.save_to(saved)
    window.set_inputs(PRESETS["T02"])
    window.open_from(saved)
    _drain_synthesis(app, window)
    assert window.corner_sweep == sweep
    window.report_to(tmp_path / "corners.html")
    assert "逐指标多工况极值" in (tmp_path / "corners.html").read_text(encoding="utf-8")
    with pytest.raises(ValueError):
        write_report(calculate_stage(PRESETS["T02"]), tmp_path / "foreign-corner.html", corner_sweep=sweep)
    with pytest.raises(ValueError):
        write_report(window.result, tmp_path / "wrong-device.html", loop_result=window.loop_result,
                     loop_settings=window._loop_settings, corner_sweep=sweep)
    requested = os.environ.get("PSD_EVIDENCE_DIR")
    if requested:
        tab = window.corner_table.parentWidget()
        window.tabs.setCurrentWidget(tab)
        app.processEvents()
        assert window.grab().save(str(Path(requested, "T01-corners-qt-offscreen.png")))
        window.report_to(Path(requested, "T01-corners-report.html"))


def test_editing_corner_request_saves_current_lists_not_previous_run(app, window, tmp_path):
    _set_corner_lists(window)
    window.run_corners()
    _drain_synthesis(app, window)
    window.corner_fields["input_voltages_v"].setText("9, 15")
    path = tmp_path / "edited-request.json"
    window.save_to(path)
    document = json.loads(path.read_text(encoding="utf-8"))
    assert document["corner_request"]["input_voltages_v"] == [9., 15.]


def test_opening_project_without_scan_does_not_restore_previous_request(app, window, tmp_path):
    _set_corner_lists(window)
    window.run_corners()
    _drain_synthesis(app, window)
    plain = tmp_path / "plain.json"
    save_project(PRESETS["T02"], plain)
    window.open_from(plain)
    window.save_to(tmp_path / "resaved.json")
    document = json.loads((tmp_path / "resaved.json").read_text(encoding="utf-8"))
    assert document.get("corner_request") is None


@pytest.mark.parametrize("topology", sorted(PRESETS))
def test_all_nineteen_presets_recompute_restore_and_export_real_desktop(app, window, tmp_path, topology):
    from power_stage_design.schematic import layout
    window.set_inputs(PRESETS[topology])
    _drain_synthesis(app, window)
    assert window.result is not None, window.status.text()
    assert window.result.inputs == PRESETS[topology]
    assert window.result.topology_id == topology
    window.tabs.setCurrentIndex(0)
    app.processEvents()
    for element in layout(topology)[0]:
        if not element.selectable:
            continue
        assert element.key in window.result.waveforms, f"No waveform mapping for {topology}/{element.key}"
        point = QPoint(round(element.x*window.schematic.width()/740),
                       round(element.y*window.schematic.height()/285))
        QTest.mouseClick(window.schematic, Qt.MouseButton.LeftButton, pos=point)
        app.processEvents()
        assert window.component_combo.currentData() == element.key, f"Click hit wrong component in {topology}/{element.key}"
        _assert_plots_match_current_result(window, element.key)
    key = "Q1" if "Q1" in window.result.waveforms else next(iter(window.result.waveforms))
    window.select_component(key)
    _assert_plots_match_current_result(window, key)
    identity = window.result.design_id
    saved = tmp_path / f"{topology}.json"
    window.save_to(saved)
    window.set_inputs(PRESETS["T01"])
    window.open_from(saved)
    _drain_synthesis(app, window)
    assert window.result.design_id == identity
    path = tmp_path / f"{topology}.html"
    window.report_to(path)
    assert identity in path.read_text(encoding="utf-8")
    requested = os.environ.get("PSD_EVIDENCE_DIR")
    if requested:
        directory = Path(requested)
        window.tabs.setCurrentIndex(0)
        app.processEvents()
        assert window.grab().save(str(directory / f"{topology}-qt-offscreen.png"))
        window.save_to(directory / f"{topology}-project.json")
        window.report_to(directory / f"{topology}-report.html")


def test_heavy_stage_solves_are_responsive_and_obsolete_topology_is_discarded(app, window):
    import time

    from PySide6.QtCore import QTimer
    events = []
    QTimer.singleShot(0, lambda: events.append(time.monotonic()))
    before = time.monotonic()
    window.set_inputs(PRESETS["T04"])
    assert time.monotonic()-before < .2
    assert window._jobs and window.result is None
    app.processEvents()
    assert events
    window.set_inputs(PRESETS["T11"])
    window.set_inputs(PRESETS["T18"])
    window.set_inputs(PRESETS["T02"])
    expected = window.result.design_id
    _drain_synthesis(app, window)
    assert window.result.topology_id == "T02"
    assert window.result.design_id == expected
    _assert_plots_match_current_result(window, "Q1")


def test_parent_close_cancels_actual_toolbox_mapping_before_reopen(app, window):
    import time
    toolbox = window.toolbox
    toolbox.select_tool("X09")
    toolbox.set_values({"bits": 16})
    toolbox.calculate()
    assert toolbox._workers and toolbox.latest_result is None
    window.close()
    deadline = time.monotonic()+15.
    while toolbox._workers and time.monotonic()<deadline:
        app.processEvents()
        time.sleep(.005)
    assert not toolbox._workers
    assert toolbox.latest_result is None
    assert not toolbox._pending
    window.show()
    window.tabs.setCurrentWidget(toolbox)
    app.processEvents()
    assert toolbox.isVisible(), "Reopened parent must not leave its toolbox tab explicitly hidden"
    toolbox.select_tool("X04")
    current = toolbox.calculate()
    assert current is not None and current.tool_id == "X04"


@pytest.mark.parametrize("edit", ["stage", "compensator", "device", "corner_list"])
def test_async_project_save_never_writes_obsolete_snapshot(app, window, tmp_path, edit):
    window.set_inputs(PRESETS["T04"])
    _drain_synthesis(app, window)
    path = tmp_path / "obsolete.json"
    window._start_project_save(path)
    assert window._jobs
    if edit == "stage": window.set_inputs(PRESETS["T02"])
    elif edit == "compensator": window.comp_widgets["r2_ohm"].setValue(22.)
    elif edit == "device": window.device_combo.setCurrentIndex(1)
    else: window.corner_fields["input_voltages_v"].setText("22, 25")
    _drain_synthesis(app, window)
    assert not path.exists()
    window._start_project_save(path)
    _drain_synthesis(app, window)
    assert path.exists()
    assert json.loads(path.read_text(encoding="utf-8"))["result_identity"] == window.result.design_id


@pytest.mark.parametrize("edit", ["stage", "compensator", "device", "corner_list"])
def test_async_project_open_does_not_replace_edits_made_after_request(app, window, tmp_path, edit):
    path = tmp_path / "heavy-project.json"
    save_project(PRESETS["T04"], path)
    window._start_project_open(path)
    assert window._jobs
    assert window.result.inputs == PRESETS["T01"]  # Atomic until successful.
    if edit == "stage": window.set_inputs(PRESETS["T02"])
    elif edit == "compensator": window.comp_widgets["r2_ohm"].setValue(22.)
    elif edit == "device": window.device_combo.setCurrentIndex(1)
    else: window.corner_fields["input_voltages_v"].setText("9, 15")
    expected = window.result.design_id
    _drain_synthesis(app, window)
    assert window.result.design_id == expected


def test_headless_cli_recomputes_saved_corner_request(tmp_path):
    import subprocess
    import sys
    project = tmp_path / "corner-cli.json"
    request = {"input_voltages_v": [8.,16.],"output_currents_a": [.5,3.],
               "inductance_factors": [1.],"capacitance_factors": [1.]}
    save_project(PRESETS["T01"], project, corner_request=request)
    report = tmp_path / "corner-cli.html"
    completed = subprocess.run([sys.executable,"-m","power_stage_design",str(project),"--report",str(report)],
                               check=False,capture_output=True,text=True,timeout=30)
    assert completed.returncode == 0, completed.stdout+completed.stderr
    text = report.read_text(encoding="utf-8")
    assert "逐指标多工况极值" in text
    for vin in (8.,16.):
        for load in (.5,3.):
            assert calculate_stage(replace(PRESETS["T01"],vin_v=vin,iout_a=load)).design_id in text


@pytest.mark.parametrize("topology,control", [("T01","vmc"),("T01","peak_cmc"),("T02","peak_cmc"),
                                              ("T03","peak_cmc"),("T13","peak_cmc"),("T09","peak_cmc")])
@pytest.mark.parametrize("network", ["type_ii","type_ii_gm","type_iii","isolated_zener","isolated_inner"])
def test_thirty_loop_combinations_real_desktop_exact_bode_restore_and_report(app,window,tmp_path,topology,control,network):
    from power_stage_design.loop import analyze_loop
    window.set_inputs(PRESETS[topology])
    window.loop_control.setCurrentIndex(window.loop_control.findData(control))
    window.network_combo.setCurrentIndex(window.network_combo.findData(network))
    window.ota_output_resistance.setText("1234567.8912345")
    window.loop_widgets["esr_ohm"].setValue(.021234567)
    window.comp_widgets["open_loop_gain_db"].setValue(92.3456789)
    window.comp_widgets["gbw_hz"].setValue(1.23456789)
    window.analyze_loop()
    assert window.loop_result is not None, window.loop_status.text()
    plant,compensator = window.loop_parameters()
    expected = analyze_loop(plant,compensator,window.loop_result.frequency_hz)
    for field in ("plant_response","compensator_response","amplifier_response","loop_response"):
        np.testing.assert_array_equal(getattr(window.loop_result,field),getattr(expected,field))
    settings = window._loop_settings
    assert settings["compensator"]["output_resistance_ohm"] == 1234567.8912345
    identity = window.result.design_id
    saved = tmp_path / f"{topology}-{control}-{network}.json"
    window.save_to(saved)
    window.set_inputs(PRESETS["T02"])
    window.open_from(saved)
    assert window.result.design_id == identity
    assert window._loop_settings == settings
    for field in ("plant_response","compensator_response","amplifier_response","loop_response"):
        np.testing.assert_array_equal(getattr(window.loop_result,field),getattr(expected,field))
    gain_curves = window.loop_gain_plot.listDataItems()
    assert len(gain_curves) == 4
    for curve,response in zip(gain_curves,(expected.plant_response,expected.compensator_response,
                                          expected.amplifier_response,expected.loop_response),strict=True):
        np.testing.assert_array_equal(curve.xData,expected.frequency_hz)
        np.testing.assert_allclose(curve.yData,20*np.log10(np.maximum(abs(response),1e-300)),rtol=1e-12,atol=1e-12)
    output = tmp_path / f"{topology}-{control}-{network}.html"
    window.report_to(output)
    assert identity in output.read_text(encoding="utf-8")


def test_async_open_successfully_restores_heavy_project_and_recomputes_its_corners(app,window,tmp_path):
    path=tmp_path/"heavy-valid.json"
    request={"input_voltages_v":[22.,24.],"output_currents_a":[.2],
             "inductance_factors":[1.],"capacitance_factors":[1.]}
    save_project(PRESETS["T04"],path,corner_request=request)
    original=window.result
    window._start_project_open(path)
    assert window.result is original
    _drain_synthesis(app,window)
    assert window.result.inputs==PRESETS["T04"]
    assert window.corner_sweep is not None
    assert window.corner_sweep.base_design_id==window.result.design_id
    assert len(window.corner_sweep.points)==2
    assert all(point.inputs.topology=="T04" for point in window.corner_sweep.points)
    assert all(point.error is None for point in window.corner_sweep.points)
    window.report_to(tmp_path/"heavy-valid.html")


def test_integrated_toolbox_stage_import_becomes_unusable_when_design_changes(app,window,tmp_path):
    toolbox=window.toolbox
    toolbox.select_tool("X01")
    toolbox.import_stage()
    result=toolbox.calculate()
    assert result.inputs["stage_import"]["design_id"]==window.result.design_id
    window.set_inputs(PRESETS["T02"])
    assert toolbox.latest_result is None
    assert toolbox.calculate() is None
    assert not toolbox.export_button.isEnabled()
    with pytest.raises(ValueError):toolbox.export_to(tmp_path/"obsolete-toolbox.json")
    toolbox.import_stage()
    current=toolbox.calculate()
    assert current.inputs["stage_import"]["design_id"]==window.result.design_id
    assert current.inputs["operating"]["drain_voltage_v"]==window.result.stress["Q1"]["voltage_max_v"]


@pytest.mark.parametrize("route,topology", [("launcher","T20"),("launcher","T21"),
                                           ("standalone","T20"),("standalone","T21")])
def test_llc_entries_use_real_existing_engine_and_preserve_return_and_bridge_identity(app,monkeypatch,tmp_path,route,topology):
    import time

    import pyqtgraph as pg

    from llc_design.core.spec import LLCDesignSpec, PrimaryTopology
    from llc_design.gui.launcher import WorkspaceApplicationController
    from llc_design.gui.main_window import LLCMainWindow
    from llc_design.report.export import write_markdown_report
    from pfc_design.gui.main_window import PFCMainWindow

    monkeypatch.setattr(LLCMainWindow,"_auto_check_update",lambda self:None)
    monkeypatch.setattr(PFCMainWindow,"_auto_check_update",lambda self:None)
    controller=None
    psd=None

    def calculate_save_and_report(llc,selected,suffix):
        expected=PrimaryTopology.HALF_BRIDGE if selected=="T20" else PrimaryTopology.FULL_BRIDGE
        assert llc._spec_from_widgets().primary_topology is expected
        # Explicit known reachable examples: 400→53 V uses 30:8 in half
        # bridge, 30:4 in full bridge. Merely changing bridge enum is not a
        # promise that an old turns ratio remains physically reachable.
        llc.fields["secondary_turns"].setValue(8 if selected=="T20" else 4)
        llc._spec_from_widgets().validate()
        errors=[]
        monkeypatch.setattr(llc,"_worker_error",errors.append)
        llc.run_design()
        deadline=time.monotonic()+30.
        while llc._active_workers and time.monotonic()<deadline:
            app.processEvents()
            time.sleep(.005)
        assert not llc._active_workers
        assert not errors, "\n".join(errors)
        analysis=llc.system_analysis
        assert analysis is not None
        assert analysis.spec.primary_topology is expected
        assert analysis.operating_points
        assert expected.value in llc.summary_text.toPlainText()
        assert llc.gain_figure.axes[0].lines
        saved=tmp_path/f"{route}-{selected}-{suffix}.json"
        # Only the native file chooser is redirected; save_json itself,
        # analysis snapshots, solver and report writer execute unchanged.
        monkeypatch.setattr(qt.QFileDialog,"getSaveFileName",lambda *args,**kwargs:(str(saved),"JSON (*.json)"))
        llc.save_json()
        document=json.loads(saved.read_text(encoding="utf-8"))
        assert document["spec"]["primary_topology"]==expected.value
        assert document["analysis"]["status"]=="complete"
        assert document["analysis"]["nominal"]["total_loss_w"]==analysis.nominal.total_loss_w
        report=write_markdown_report(analysis,tmp_path/f"{route}-{selected}-{suffix}.md")
        text=report.read_text(encoding="utf-8")
        assert f"| 一次侧 | {expected.value} |" in text
        assert ("半桥" if selected=="T20" else "全桥") in text.splitlines()[0]
        evidence=os.environ.get("PSD_EVIDENCE_DIR")
        if evidence:
            directory=Path(evidence)
            (directory/saved.name).write_text(saved.read_text(encoding="utf-8"),encoding="utf-8")
            (directory/report.name).write_text(text,encoding="utf-8")
            app.processEvents()
            assert llc.grab().save(str(directory/f"{route}-{selected}-{suffix}-llc-offscreen.png"))

    try:
        if route=="launcher":
            controller=WorkspaceApplicationController(LLCDesignSpec())
            psd=controller.power_stage_window
            controller.show_workspace("power_stage")
        else:
            psd=PowerStageMainWindow()
            psd.show()
        app.processEvents()
        psd.topology_combo.setCurrentIndex(psd.topology_combo.findData(topology))
        app.processEvents()
        assert psd.llc_button.isVisible()
        QTest.mouseClick(psd.llc_button,Qt.MouseButton.LeftButton)
        app.processEvents()
        if route=="standalone":controller=psd._linked_application
        assert controller.power_stage_window is psd
        llc=controller.llc_window
        assert llc.isVisible() and not psd.isVisible()
        calculate_save_and_report(llc,topology,"first")
        first_analysis=llc.system_analysis
        if route=="standalone" and topology=="T20":
            llc.run_design()
            assert llc._active_workers  # Genuine old-bridge job, not a fake callback.
        llc.workspace_switch_requested.emit("power_stage")
        app.processEvents()
        assert psd.isVisible() and not llc.isVisible()
        assert psd.topology_combo.currentData()==topology
        other="T21" if topology=="T20" else "T20"
        psd.topology_combo.setCurrentIndex(psd.topology_combo.findData(other))
        QTest.mouseClick(psd.llc_button,Qt.MouseButton.LeftButton)
        app.processEvents()
        assert controller.llc_window is llc
        deadline=time.monotonic()+30.
        while llc._active_workers and time.monotonic()<deadline:
            app.processEvents()
            time.sleep(.005)
        assert not llc._active_workers
        assert llc.system_analysis is None, "Changing LLC bridge must invalidate previous bridge results immediately"
        assert "LLC 设计结果摘要" not in llc.summary_text.toPlainText()
        assert not any(axes.lines for axes in llc.gain_figure.axes)
        assert first_analysis.spec.primary_topology is not llc._spec_from_widgets().primary_topology
        calculate_save_and_report(llc,other,"changed")
        llc.workspace_switch_requested.emit("power_stage")
        app.processEvents()
        assert psd.isVisible() and not llc.isVisible()
        psd.close()
        psd.show()
        app.processEvents()
        assert psd.topology_combo.currentData()==other
    finally:
        views=([getattr(controller,name) for name in ("llc_window","pfc_window","control_window",
               "fra_window","dab_window","power_stage_window")] if controller else [psd])
        for view in views:
            if view is None:continue
            for pool in (getattr(view,"thread_pool",None),getattr(view,"_pool",None)):
                if pool is not None:pool.waitForDone(30000)
            view.close()
            for graphics in view.findChildren(pg.GraphicsLayoutWidget):graphics.close()
            view.deleteLater()
        QCoreApplication.sendPostedEvents(None,QEvent.Type.DeferredDelete)
        app.processEvents()

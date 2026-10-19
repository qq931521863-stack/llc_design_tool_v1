"""Real Qt acceptance for declared QR first-clamp and PSFB diode-DCM extensions.

Calculations, component picking, saved projects and exported arrays are real;
this offscreen Linux flow does not assert TI executable or hardware parity.
"""
from __future__ import annotations

import csv
import json
import os
import time
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
qt = pytest.importorskip("PySide6.QtWidgets")
from PySide6.QtCore import QCoreApplication, QEvent, QPoint, Qt
from PySide6.QtTest import QTest

from power_stage_design.gui import PowerStageMainWindow
from power_stage_design.models import DesignInputs
from power_stage_design.report import write_waveform_csv
from power_stage_design.schematic import layout


@pytest.fixture(scope="module")
def app():
    application = qt.QApplication.instance() or qt.QApplication([])
    yield application
    application.processEvents()


@pytest.fixture
def window(app):
    view = PowerStageMainWindow()
    view.resize(1360, 900)
    view.show()
    app.processEvents()
    yield view
    view.close()
    view.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()


def _wait(app, view):
    deadline = time.monotonic() + 30
    while view._jobs and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.005)
    app.processEvents()
    assert not view._jobs, "Actual asynchronous stage solve did not complete"
    assert view._pool.waitForDone(30000)


QR = DesignInputs("T11", 48., 15., 1., 1e6, 1e-3, capacitance_f=1e-3,
                  turns_ratio_np_ns=4., resonant_capacitance_f=300e-12)
PSFB = DesignInputs("T18", 400., 48., .1, 100e3, 100e-6, capacitance_f=1e-3,
                    turns_ratio_np_ns=4., magnetizing_inductance_h=1e-3,
                    leakage_inductance_h=20e-6, diode_drop_v=.7)


def _pick_and_check(app, view, key):
    element = next(e for e in layout(view.result.topology_id, device_keys=view.result.waveforms)[0] if e.key == key)
    bounds = view.schematic.schematic_rect()
    point = QPoint(round(bounds.x() + element.x * bounds.width() / 740),
                   round(bounds.y() + element.y * bounds.height() / 285))
    QTest.mouseClick(view.schematic, Qt.MouseButton.LeftButton, pos=point)
    app.processEvents()
    assert view.schematic.selected == key
    assert view.component_combo.currentData() == key
    device = view.result.waveforms[key]
    for plot, signal in ((view.current_plot, device.current_a),
                         (view.voltage_plot, device.voltage_v)):
        items = plot.listDataItems()
        assert len(items) == 1
        actual_t, actual_y = items[0].getData()
        expected_t, expected_y = signal.sample(count=800, periods=2)
        np.testing.assert_array_equal(actual_t, expected_t)
        np.testing.assert_array_equal(actual_y, expected_y)
    assert float(view.stress_table.item(0, 4).text()) == pytest.approx(
        view.result.stress[key]["current_rms_a"], rel=1e-7)


@pytest.mark.parametrize("inputs,revision,component", [
    (QR, "qr-first-zero-clamp-1", "Q1"),
    (PSFB, "psfb-diode-dcm-1", "L1"),
])
def test_real_new_domain_click_save_restore_report_and_csv(
        app, window, tmp_path, inputs, revision, component):
    window.set_inputs(inputs)
    _wait(app, window)
    result = window.result
    assert result is not None
    assert result.provenance["physics_revision"] == revision
    assert any("TI executable equivalence is UNKNOWN" in w for w in result.warnings)
    assert window.report_action.isEnabled()
    # All selectable modeled symbols must point at the matching computed branch.
    for element in layout(inputs.topology, device_keys=result.waveforms)[0]:
        if element.key in result.waveforms:
            _pick_and_check(app, window, element.key)
    _pick_and_check(app, window, component)
    if inputs.topology == "T11":
        assert result.stress["Q1"]["current_min_a"] < 0
        assert result.metrics["turn_on_capacitive_loss_w"] == 0
        assert "首个零电压谷底" in window.suggestion_scope.text()
    else:
        assert result.mode == "DCM"
        assert result.metrics["idle_fraction"] > 0
    directory = Path(os.environ.get("PSD_EVIDENCE_DIR", tmp_path)) / inputs.topology
    directory.mkdir(parents=True, exist_ok=True)
    assert window.grab().save(str(directory / "actual-stage-1360.png"))
    if "Cnode" in result.waveforms:
        _pick_and_check(app, window, "Cnode")
        assert window.grab().save(str(directory / "actual-cnode-1360.png"))
        _pick_and_check(app, window, component)
    project = directory / "project.json"
    window.save_to(project)
    saved = json.loads(project.read_text(encoding="utf-8"))
    assert saved["result_identity"] == result.design_id
    assert "waveforms" not in saved
    window.set_inputs(replace(inputs, iout_a=inputs.iout_a * 1.1))
    _wait(app, window)
    assert window.result.design_id != result.design_id
    window.open_from(project)
    _wait(app, window)
    assert window.result.inputs == inputs
    assert window.result.design_id == result.design_id
    assert window.result.provenance["physics_revision"] == revision
    _pick_and_check(app, window, component)
    report = directory / "report.html"
    window.report_to(report)
    text = report.read_text(encoding="utf-8")
    assert result.design_id in text and revision in text
    if inputs.topology == "T11":
        assert 'data-device="Cnode"' in text
    assert "TI executable equivalence is UNKNOWN" in text
    output = write_waveform_csv(window.result, directory / "waveforms.csv", samples=2000)
    if inputs.topology == "T11":
        assert "Cnode_current_a" in output.read_text(encoding="utf-8")
    with output.open(newline="", encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
    assert len(rows) == 2000
    assert {row["design_id"] for row in rows} == {result.design_id}
    t = np.array([float(row["time_s"]) for row in rows])
    for key, device in result.waveforms.items():
        for suffix, signal in (("current_a", device.current_a), ("voltage_v", device.voltage_v)):
            actual = np.array([float(row[f"{key}_{suffix}"]) for row in rows])
            assert np.isfinite(actual).all()
            phase = t / signal.period_s
            for segment in signal.segments:
                mask = (phase >= segment.start_fraction) & (phase < segment.end_fraction)
                expected = segment.evaluate((phase[mask] - segment.start_fraction)
                                            / segment.duration_fraction)
                np.testing.assert_array_equal(actual[mask], expected)
    (directory / "observer.json").write_text(json.dumps({
        "design_id": result.design_id, "provenance": result.provenance,
        "mode": result.mode, "metrics": result.metrics, "stress": result.stress,
        "assumptions": result.assumptions, "warnings": result.warnings,
    }, indent=2) + "\n", encoding="utf-8")


def test_qr_unmodeled_later_clamp_rejects_current_gui_export(app, window, tmp_path):
    window.set_inputs(QR)
    _wait(app, window)
    original = window.result
    assert original is not None
    fs = original.metrics["actual_switching_frequency_hz"]
    window.input_widgets["fsw_hz"].setValue(.5 * fs / 1000.)
    assert window.result is None
    assert not window.report_action.isEnabled()
    QTest.qWait(200)
    _wait(app, window)
    assert window.result is None
    assert not window.current_plot.listDataItems()
    assert "later clamped valleys" in window.status.text()
    with pytest.raises(ValueError):
        window.report_to(tmp_path / "stale.html")
    assert not (tmp_path / "stale.html").exists()


@pytest.mark.parametrize("reference", [QR, PSFB])
def test_new_domain_real_parameter_proposal_apply_and_restored_targets(
        app, window, tmp_path, reference):
    from power_stage_design.topologies import calculate_stage

    # This test verifies the actual GUI/data path. Numerical inverse targets
    # already have separate original-state ODE oracles in test_qr_psfb_suggestions.
    target_result = calculate_stage(reference)
    initial = replace(reference, inductance_h=reference.inductance_h * 1.4,
                      turns_ratio_np_ns=3.5)
    window.set_inputs(initial)
    _wait(app, window)
    assert window.result is not None
    window.tabs.setCurrentWidget(window.suggestion_tab)
    window.suggestion_ratio.setChecked(True)
    window.suggestion_ripple.setValue(target_result.metrics["inductor_ripple_pp_a"])
    window.suggestion_duty.setValue(.9 if reference.topology == "T11" else target_result.duty)
    window.suggestion_reflected.setValue(
        reference.turns_ratio_np_ns * (reference.vout_v + reference.diode_drop_v))
    QTest.mouseClick(window.suggest_button, Qt.MouseButton.LeftButton)
    _wait(app, window)
    proposal = window.parameter_proposal
    assert proposal is not None, window.suggestion_output.toPlainText()
    assert window.current_inputs() == initial
    assert proposal.result.provenance["physics_revision"] == target_result.provenance["physics_revision"]
    assert proposal.inputs.inductance_h == pytest.approx(reference.inductance_h, rel=2e-5)
    assert proposal.inputs.turns_ratio_np_ns == pytest.approx(reference.turns_ratio_np_ns, rel=2e-5)
    assert window.apply_suggestion_button.isEnabled()
    QTest.mouseClick(window.apply_suggestion_button, Qt.MouseButton.LeftButton)
    _wait(app, window)
    assert window.current_inputs() == proposal.inputs
    assert window.result.design_id == proposal.result.design_id
    assert window._applied_suggestion == proposal.record()
    directory = Path(os.environ.get("PSD_EVIDENCE_DIR", tmp_path)) / reference.topology
    directory.mkdir(parents=True, exist_ok=True)
    assert window.grab().save(str(directory / "actual-parameter-apply-1360.png"))
    project = directory / "applied-parameters.json"
    window.save_to(project)
    window.set_inputs(reference)
    _wait(app, window)
    window.open_from(project)
    _wait(app, window)
    assert window.current_inputs() == proposal.inputs
    assert window._applied_suggestion == proposal.record()
    assert window.result.design_id == proposal.result.design_id
    report = directory / "applied-parameters.html"
    window.report_to(report)
    text = report.read_text(encoding="utf-8")
    assert proposal.source_design_id in text
    assert proposal.result.design_id in text
    assert target_result.provenance["physics_revision"] in text

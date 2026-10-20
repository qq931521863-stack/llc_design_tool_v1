"""Actual Qt button workflows, stale jobs, exact sessions and delivery."""

import json
import time
from dataclasses import replace

import numpy as np
import pytest
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox

from pfc_design.gui.three_phase_view import ThreePhaseDQView
from pfc_design.three_phase.control import request_identity
from pfc_design.three_phase.exact_control import ExactController


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def view(app, monkeypatch):
    errors = []
    monkeypatch.setattr(
        QMessageBox, "warning", lambda *args: errors.append(str(args[-1]))
    )
    w = ThreePhaseDQView()
    w._test_errors = errors
    yield w
    w.close()
    pump(app, lambda: w._worker is None and w.loop_view._worker is None)
    w.deleteLater()
    app.processEvents()


def pump(app, done, seconds=30):
    deadline = time.monotonic() + seconds
    while not done() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)
    app.processEvents()
    assert done(), "Qt worker did not finish"


def test_actual_candidate_apply_save_fresh_window_recompute_and_delivery(
    app, view, monkeypatch, tmp_path
):
    w = view
    w.resize(1280, 900)
    w.show()
    app.processEvents()
    w.local_button.click()
    assert w.loop_view.analysis is not None
    first = w.loop_view.analysis["source_identity"]
    w.loop_view.candidate_button.click()
    pump(app, lambda: w.loop_view._worker is None)
    assert len(w.loop_view.candidates) == 9
    row = next(
        i
        for i, c in enumerate(w.loop_view.candidates)
        if c.applicable and c.target_fc_hz == 1000 and c.target_pm_deg == 60
    )
    exact = w.loop_view.candidates[row].controller
    w.loop_view.candidate_table.selectRow(row)
    app.processEvents()
    assert w.loop_view.apply_button.isEnabled()
    w.loop_view.apply_button.click()
    assert w.config_from_widgets().current_controller == exact
    assert not w.loop_view.export_button.isEnabled()
    assert not w.loop_view.apply_button.isEnabled()
    w.local_button.click()
    assert w.loop_view.analysis["source_identity"] != first
    w.loop_view.retain_button.click()
    assert len(w.retained) == 1
    saved = tmp_path / "dq.json"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *args: (str(saved), ""))
    w.save_button.click()
    w2 = ThreePhaseDQView()
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *args: (str(saved), ""))
    w2.load_button.click()
    assert w2.config_from_widgets() == w.config_from_widgets()
    assert not w2.loop_view.export_button.isEnabled()
    w2.local_button.click()
    for loop in ("current", "voltage"):
        np.testing.assert_array_equal(
            w2.loop_view.analysis[loop].open_response,
            w.loop_view.analysis[loop].open_response,
        )
    # Deliver through actual button; scripts and C99 are verified inside exporter.
    monkeypatch.setattr(
        QFileDialog, "getExistingDirectory", lambda *args: str(tmp_path)
    )
    w2.loop_view.export_button.click()
    assert not w._test_errors, w._test_errors
    outputs = [p for p in tmp_path.iterdir() if p.is_dir() and p.name.startswith("dq-")]
    assert len(outputs) == 1
    report = json.loads((outputs[0] / "report.json").read_text())
    assert report["source_identity"] == request_identity(w2.config_from_widgets())
    assert report["c99_verification"]["status"] == "PASS"
    assert report["c99_float32_verification"]["status"] == "PASS"
    assert not w._test_errors
    w2.close()
    w2.deleteLater()


def test_same_rate_exact_import_invalidates_both_loops_and_current_exports(
    view, monkeypatch, tmp_path
):
    w = view
    w.local_button.click()
    old = w.loop_view.analysis["source_identity"]
    file = tmp_path / "controller.json"
    data = {
        "b": [0, 2, -1.98],
        "a": [1, -1],
        "sample_rate_hz": 20000,
        "source": "external exact delay",
    }
    file.write_text(json.dumps(data))
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *args: (str(file), ""))
    w.loop_view.import_button.click()
    assert w.loop_view.analysis is None and not w.loop_view.export_button.isEnabled()
    assert not w.export_button.isEnabled()
    w.local_button.click()
    assert w.loop_view.analysis["source_identity"] != old
    assert w.loop_view.analysis["current"].controller_b.tolist() == [0, 2, -1.98]
    assert not w._test_errors


def test_invalid_rate_import_and_atomic_out_of_range_session_leave_source_unchanged(
    view,
):
    w = view
    before = w.config_from_widgets()
    with pytest.raises(ValueError, match="sample rate mismatch"):
        w.import_controller("current", ExactController((1.0,), (1.0,), 17000))
    assert w.config_from_widgets() == before
    # Valid backend config, outside desktop widget range: no partial PLL/name/widget mutation.
    config = replace(before, pll_kind="DDSRF", current_kp=1001)
    with pytest.raises(ValueError, match="desktop input range"):
        w.apply_loaded_session(config)
    assert w.config_from_widgets() == before


def test_edit_during_simulation_and_cancel_never_publish_old_worker(app, view):
    w = view
    w.run_button.click()
    w.inputs["current_kp"].setValue(4.0)
    pump(app, lambda: w._worker is None)
    assert w.result is None and w.loop_view.analysis is None
    assert not w.export_button.isEnabled()
    w.run_button.click()
    w.cancel_button.click()
    pump(app, lambda: w._worker is None)
    assert w.result is None and w.run_button.isEnabled()
    assert not w._test_errors


def test_edit_during_candidate_compute_rejects_result_and_close_releases_worker(
    app, view
):
    w = view
    w.loop_view.candidate_button.click()
    w.inputs["current_ki"].setValue(600.0)
    pump(app, lambda: w.loop_view._worker is None)
    assert not w.loop_view.candidates
    assert not w.loop_view.apply_button.isEnabled()
    w.run_button.click()
    w.close()
    pump(app, lambda: w._worker is None)
    assert w.result is None


@pytest.mark.parametrize("reopen_before_delivery", (False, True))
def test_close_reopen_rejects_old_result_and_accepts_real_recompute(
    app, view, monkeypatch, reopen_before_delivery
):
    from threading import Event

    from PySide6.QtCore import QCoreApplication, QEvent
    from shiboken6 import isValid

    from pfc_design.gui import three_phase_view

    w = view
    w.apply_loaded_session(replace(w.config_from_widgets(), duration_s=0.08))
    w.show()
    app.processEvents()
    computed, release = Event(), Event()
    simulate = three_phase_view.simulate_three_phase

    def delayed_first_result(config, *, cancel_event):
        result = simulate(config, cancel_event=cancel_event)
        if not computed.is_set():
            computed.set()
            assert release.wait(10)
        return result

    monkeypatch.setattr(three_phase_view, "simulate_three_phase", delayed_first_result)
    w.run_button.click()
    old_worker, old_cancel = w._worker, w._cancel
    try:
        assert computed.wait(10)
        assert isValid(old_worker) and isValid(old_worker.signals)
        w.close()
        assert old_cancel.is_set()
        if reopen_before_delivery:
            w.show()
        release.set()
        pump(app, lambda: w._worker is None)
    finally:
        release.set()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert not isValid(old_worker) and not isValid(old_worker.signals)
    assert w.result is None and w.loop_view.analysis is None
    assert not w.export_button.isEnabled()
    if not reopen_before_delivery:
        w.show()
    w.run_button.click()
    new_worker = w._worker
    assert new_worker is not None and new_worker is not old_worker
    assert w._cancel is not old_cancel and not w._cancel.is_set()
    pump(app, lambda: w._worker is None)
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert not isValid(new_worker) and not isValid(new_worker.signals)
    assert w.result is not None
    assert w.result.config == w.config_from_widgets()
    assert w.result.metrics["source_identity"] == request_identity(w.result.config)
    assert w.loop_view.result is w.result
    assert w.loop_view.analysis["source_identity"] == request_identity(w.result.config)
    assert w.run_button.isEnabled() and not w.cancel_button.isEnabled()
    assert w.export_button.isEnabled()
    assert not w._test_errors


def test_sensor_controls_reach_model_and_cursor_and_no_stale_export(view):
    w = view
    w.local_button.click()
    original = w.loop_view.analysis["current"].open_response.copy()
    w.sense_inputs["current"]["pole_hz"].setValue(4000.0)
    assert not w.loop_view.export_button.isEnabled()
    w.local_button.click()
    loop = w.loop_view.analysis["current"]
    assert not np.allclose(loop.open_response, original)
    w.loop_view.cursor.setValue(200.123)
    budget = json.loads(w.loop_view.cursor_summary.toPlainText())
    assert "sampled sense" in budget["blocks"]
    assert budget["frequency_hz"] == pytest.approx(200.123)
    w.loop_view.curve_checks["Sense"].setChecked(True)
    assert any(line.get_label() == "Sense" for line in w.loop_view.figure.axes[0].lines)
    assert not w._test_errors


def test_actual_new_process_session_button_replays_same_exact_curves(
    view, monkeypatch, tmp_path
):
    import subprocess
    import sys

    w = view
    w.import_controller(
        "current",
        ExactController(
            (0.0, 2.0, -1.98), (1.0, -1.0), 20000.0, "fresh process exact source"
        ),
    )
    w.sense_inputs["voltage"]["pole_hz"].setValue(300.0)
    w.local_button.click()
    path = tmp_path / "fresh.json"
    out = tmp_path / "fresh.npz"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *args: (str(path), ""))
    w.save_button.click()
    code = """import sys,numpy as np
from PySide6.QtWidgets import QApplication,QFileDialog
from pfc_design.gui.three_phase_view import ThreePhaseDQView
app=QApplication([]);w=ThreePhaseDQView()
QFileDialog.getOpenFileName=lambda *args:(sys.argv[1],"")
w.load_button.click();assert not w.loop_view.export_button.isEnabled()
w.local_button.click()
a=w.loop_view.analysis
np.savez(sys.argv[2],current=a["current"].open_response,voltage=a["voltage"].open_response)
print(a["source_identity"])
w.close()
"""
    replay = subprocess.run(
        [sys.executable, "-c", code, str(path), str(out)],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert replay.stdout.strip() == request_identity(w.config_from_widgets())
    with np.load(out) as actual:
        for loop in ("current", "voltage"):
            np.testing.assert_array_equal(
                actual[loop], w.loop_view.analysis[loop].open_response
            )


def test_actual_pfc_parent_visible_all_dq_tabs_and_pll_paint_in_fresh_process():
    """Regression for a native 139 with a canvas in the former whole-page scroll."""
    import os
    import subprocess
    import sys

    code = """import time
from PySide6.QtWidgets import QApplication
from llc_design.gui import theme
from pfc_design.gui.main_window import PFCMainWindow
import pfc_design.gui.main_window as pfc_main_window
pfc_main_window.check_for_updates=lambda *args,**kwargs:None
app=QApplication([]);theme.apply_app_theme(app)
w=PFCMainWindow();w.resize(1280,720);dq=w.three_phase_view
w.subtabs.setCurrentWidget(dq);w.show()
for _ in range(4):app.processEvents()
dq.local_button.click();app.processEvents();dq.run_button.click()
deadline=time.monotonic()+40
while dq._worker is not None and time.monotonic()<deadline:
 app.processEvents();time.sleep(.01)
assert dq.result is not None
for j in range(dq.tabs.count()):
 dq.tabs.setCurrentIndex(j)
 for _ in range(4):app.processEvents()
 w.grab()
dq.tabs.setCurrentWidget(dq.pll_comparison)
dq.pll_comparison.run_button.click()
deadline=time.monotonic()+40
while dq.pll_comparison._worker is not None and time.monotonic()<deadline:
 app.processEvents();time.sleep(.01)
assert dq.pll_comparison.result is not None
for _ in range(8):app.processEvents()
w.grab();w.close();app.processEvents()
print("PARENT_VISIBLE_PLL_AND_DQ_PASS")
"""
    result = subprocess.run(
        [sys.executable, "-X", "faulthandler", "-c", code],
        check=False,
        capture_output=True,
        text=True,
        timeout=90,
        env={**os.environ, "QT_QPA_PLATFORM": "offscreen"},
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "PARENT_VISIBLE_PLL_AND_DQ_PASS" in result.stdout


def test_nonunity_sensor_plot_references_use_physical_and_feedback_domains(view):
    from pfc_design.three_phase.exact_control import SampledSense
    from pfc_design.three_phase.model import simulate_three_phase

    cfg = replace(
        view.config_from_widgets(),
        duration_s=0.08,
        voltage_sense=SampledSense(gain=1.05),
        current_sense=SampledSense(gain=0.95),
    )
    view.apply_loaded_session(cfg)
    view.set_result(simulate_three_phase(cfg))
    bus_axis = view.figures["母线 / 能量"].axes[0]
    np.testing.assert_allclose(bus_axis.lines[1].get_ydata(), [700 / 1.05, 700 / 1.05])
    labels = [line.get_label() for line in view.figures["dq 电流环"].axes[0].lines]
    assert labels == ["id_a", "id_feedback_a", "id_ref_a"]

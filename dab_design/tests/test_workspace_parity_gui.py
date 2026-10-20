"""Real Qt actions, atomic session replay, current-source gates and real exports."""

import copy
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication, QFileDialog

from dab_design.gui.main_window import DABMainWindow
from power_control_tools.models import DigitalTransferFunction


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def finish(app, view):
    deadline = time.monotonic() + 20
    while view._worker is not None and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.005)
    assert view._worker is None
    app.processEvents()


def test_actual_candidate_apply_session_new_process_exports_and_cursor(
    app, monkeypatch, tmp_path
):
    window = DABMainWindow()
    view = window.control_view
    window.resize(1280, 720)
    window.tabs.setCurrentWidget(view)
    window.show()
    app.processEvents()
    assert window.minimumSizeHint().height() <= 720
    assert view.page_scroll.verticalScrollBar().maximum() > 0
    view.actuator.setCurrentIndex(1)
    view.inputs["lowpass_hz"].setValue(800)
    view.inputs["sense_delay_ticks"].setValue(1)
    view.synthesize_button.click()
    candidates = [(i, c) for i, c in enumerate(view.candidates) if c["can_apply"]]
    assert candidates
    row, candidate = candidates[len(candidates) // 2]
    view.candidate_table.selectRow(row)
    assert view.apply_button.isEnabled()
    view.apply_button.click()
    assert np.array_equal(
        view.custom_controller.numerator, candidate["controller"]["b"]
    )
    view.retain_workpoint()
    view.action_buttons[0].click()
    finish(app, view)
    assert view.snapshot is not None and view.export_button.isEnabled()
    loop = view._result[3]
    view._cursor.set_frequency(77.777)
    budget = json.loads(view.phase_view.toPlainText())
    assert [x["key"] for x in budget["factors"]] == ["G", "C", "H"]
    view.trace_checks["H"].setChecked(True)
    assert "H" in [t.label for t in view._cursor.traces]
    expected = view.snapshot["responses"]
    session = tmp_path / "session.json"
    monkeypatch.setattr(
        QFileDialog, "getSaveFileName", lambda *a: (str(session), "JSON")
    )
    window.save_project()
    payload = json.loads(session.read_text())
    from dab_design.core import load_config

    assert load_config(session) == window.config_from_widgets()
    assert payload["controller"]["b"] == candidate["controller"]["b"]
    assert (
        payload["historical_result"]["last_result"]["run_context"]["candidate_evidence"]
        is not None
    )
    bad = copy.deepcopy(payload)
    bad["control"]["sensing"]["adc_bits"] = 99
    before = view.session_data()
    stage_before = window.config_from_widgets()
    with pytest.raises(ValueError):
        window.restore_project_data(bad)
    assert (
        view.session_data() == before and window.config_from_widgets() == stage_before
    )
    # Actual output buttons produce files, not only text in the code tab.
    for method, name in (
        (view.export_snapshot, "snapshot.json"),
        (view.export_report, "report.html"),
        (view.export_research, "research.py"),
        (view.export_research, "research.m"),
        (view.export_c99, "controller.h"),
    ):
        path = tmp_path / name
        monkeypatch.setattr(
            QFileDialog, "getSaveFileName", lambda *a, p=path: (str(p), "")
        )
        method()
        assert path.stat().st_size > 100
    assert "DAB" in (tmp_path / "research.m").read_text()
    assert "data:image/png;base64" in (tmp_path / "report.html").read_text()
    run = subprocess.run(
        [sys.executable, str(tmp_path / "research.py")],
        env={**os.environ, "MPLBACKEND": "Agg"},
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert run.returncode == 0, run.stderr
    assert "Reference and feedback identity checks passed" in run.stdout
    from power_control_tools.codegen import C99Export, verify_c99_filter

    digital = DigitalTransferFunction(
        tuple(loop.controller_b), tuple(loop.controller_a), 1 / loop.sample_time_s
    )
    verification = verify_c99_filter(
        digital, C99Export(tmp_path / "controller.h", len(digital.sos), "DAB_VOLTAGE")
    )
    assert verification.passed, verification
    # Truly separate process, real fresh Qt window, saved fields restored then async Run.
    output = tmp_path / "replayed.json"
    script = tmp_path / "replay.py"
    script.write_text("""import json,time,sys
from PySide6.QtWidgets import QApplication
from dab_design.gui.main_window import DABMainWindow
app=QApplication([]);w=DABMainWindow();v=w.control_view
w.restore_project_data(json.load(open(sys.argv[1])))
assert not v.export_button.isEnabled() and v.custom_controller is not None
v.run_control()
while v._worker is not None:app.processEvents();time.sleep(.005)
assert v.export_button.isEnabled(),v.status.text()
json.dump(v.snapshot['responses'],open(sys.argv[2],'w'));w.close()
""")
    run = subprocess.run(
        [sys.executable, str(script), str(session), str(output)],
        env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[2])},
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert run.returncode == 0, run.stderr
    assert json.loads(output.read_text()) == expected
    # Same-Fs controller import must invalidate every current result/export.
    revision = view._revision
    old = view.snapshot
    view._import_controller(
        DigitalTransferFunction((1.0, -0.5), (1.0, -0.8), 5000), "Different exact H(z)"
    )
    assert (
        view._revision > revision
        and view.snapshot is old
        and not view.export_button.isEnabled()
    )
    assert not view.apply_button.isEnabled()
    view.export_snapshot()  # no dialog while historical
    window.close()


def test_retained_scan_reports_invalid_branch_and_cancellation_drops_result(
    app, monkeypatch
):
    import threading

    from dab_design.gui import control_view as module

    window = DABMainWindow()
    view = window.control_view
    view.workpoints = [
        {"name": "smooth", "voltage_v": 400.0, "power_w": 3500.0},
        {"name": "Cp kink", "voltage_v": 300.0, "power_w": 2343.75},
    ]
    view.cp.setChecked(True)
    view.run_scan()
    finish(app, view)
    assert view.scan_results[0]["valid"]
    assert (
        not view.scan_results[1]["valid"]
        and "Nonsmooth" in view.scan_results[1]["reason"]
    )
    assert (
        view.snapshot["run_context"]["controller"]["b"]
        == view.control_config().controller().numerator.tolist()
    )
    entered = threading.Event()
    release = threading.Event()
    real = module.simulate_dab_voltage_loop

    def blocked(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return real(*args, **kwargs)

    monkeypatch.setattr(module, "simulate_dab_voltage_loop", blocked)
    view.cp.setChecked(False)
    view.run_control()
    assert entered.wait(2)
    view.cancel()
    release.set()
    finish(app, view)
    assert view.snapshot is None and not view.export_button.isEnabled()
    assert "Cancelled" in view.status.text()
    window.close()


@pytest.mark.parametrize(
    "mutation",
    [
        lambda p: p["control"].pop("delay_ticks"),
        lambda p: p["control"].update(sample_hz=5000.5),
        lambda p: p["stage"].update(inductance_h=30.123456789e-6),
        lambda p: p["historical_result"].update(last_result={"bad": float("nan")}),
        lambda p: p["ui"].update(visible_traces=["unknown"]),
    ],
)
def test_bad_full_session_rejection_is_atomic(app, mutation):
    window = DABMainWindow()
    view = window.control_view
    view._import_controller(
        DigitalTransferFunction((1.0, -0.5), (1.0, -0.8), 5000), "exact"
    )
    payload = view.session_data()
    mutation(payload)
    before = view.session_data()
    stage = window.config_from_widgets()
    with pytest.raises((ValueError, TypeError, KeyError)):
        window.restore_project_data(payload)
    assert view.session_data() == before
    assert window.config_from_widgets() == stage
    window.close()


def test_arbitrary_import_is_used_verbatim_and_roundtrips_without_pi_refit(app):
    window = DABMainWindow()
    view = window.control_view
    digital = DigitalTransferFunction(
        (0.0, 25.0, -9.0, 2.0), (1.0, -0.3, 0.05), 5000, name="arbitrary biquad"
    )
    view._import_controller(digital, "arbitrary biquad")
    view.run_control()
    finish(app, view)
    assert view.snapshot is not None, view.status.text()
    loop = view._result[3]
    assert np.array_equal(loop.controller_b, digital.b)
    assert np.array_equal(loop.controller_a, digital.a)
    z = np.exp(-2j * np.pi * loop.frequency_hz / 5000)
    assert loop.controller_response == pytest.approx(
        z * (25 - 9 * z + 2 * z * z) / (1 - 0.3 * z + 0.05 * z * z), rel=1e-13
    )
    payload = json.loads(json.dumps(view.session_data()))
    fresh = DABMainWindow()
    fresh.restore_project_data(payload)
    v = fresh.control_view
    assert np.array_equal(v.custom_controller.numerator, digital.b)
    assert np.array_equal(v.custom_controller.denominator, digital.a)
    assert v.custom_controller.name == "arbitrary biquad"
    v.run_control()
    finish(app, v)
    assert v.snapshot["responses"] == view.snapshot["responses"]
    window.close()
    fresh.close()


def test_legacy_stage_project_load_remains_supported(app, monkeypatch, tmp_path):
    from dataclasses import replace

    from dab_design.core import DABConfig, save_config

    path = tmp_path / "legacy.json"
    save_config(replace(DABConfig(), va_v=420), path)
    window = DABMainWindow()
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a: (str(path), "JSON"))
    errors = []
    monkeypatch.setattr(window, "_error", errors.append)
    window.load_project()
    assert not errors and window.config_from_widgets().va_v == 420
    assert window.control_view.custom_controller is None
    window.close()


def test_fractional_load_list_restores_exact_binary_stage_values(app):
    window = DABMainWindow()
    window.loads.setText("10,33.33333333333333,71.23456789012345,100")
    stage = window.config_from_widgets()
    payload = json.loads(json.dumps(window.control_view.session_data()))
    fresh = DABMainWindow()
    fresh.restore_project_data(payload)
    assert fresh.config_from_widgets() == stage
    window.close()
    fresh.close()


def test_shown_canvas_tab_stays_alive_through_repaint_and_close(tmp_path):
    """Native crashes must fail a subprocess test, not hide behind no event pumping."""
    script = tmp_path / "shown_lifecycle.py"
    script.write_text("""import time,sys
from PySide6.QtWidgets import QApplication
from dab_design.gui.main_window import DABMainWindow
app=QApplication([]);w=DABMainWindow();v=w.control_view
w.resize(1280,720);w.tabs.setCurrentWidget(v);w.show();app.processEvents()
assert w.minimumSizeHint().height() <= 720
v.run_control()
while v._worker is not None:app.processEvents();time.sleep(.005)
assert v.snapshot is not None,v.status.text()
assert not v.parameters_toggle.isChecked()
for _ in range(3):
 v.parameters_toggle.setChecked(True);app.processEvents()
 v.parameters_toggle.setChecked(False)
 v.tabs.setCurrentWidget(v.summary)
 for _ in range(5):app.processEvents();time.sleep(.01)
 v.tabs.setCurrentWidget(v.canvas);v.canvas.draw_idle()
 for _ in range(10):app.processEvents();time.sleep(.01)
 assert not w.grab().isNull()
v.canvas.draw_idle();w.close()
for _ in range(10):app.processEvents();time.sleep(.01)
print('shown-canvas-repaint-close-pass')
""")
    result = subprocess.run(
        [sys.executable, str(script)],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
        env={
            **os.environ,
            "PYTHONPATH": str(Path(__file__).resolve().parents[2]),
            "PYTHONFAULTHANDLER": "1",
        },
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "shown-canvas-repaint-close-pass" in result.stdout

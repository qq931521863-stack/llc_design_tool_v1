"""Narrow real-circuit GUI rejection and stale-result identity regressions."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _ignore_update_check(*args, **kwargs):
    pass


@pytest.fixture
def window(monkeypatch):
    qt = pytest.importorskip("PySide6.QtWidgets")
    from power_sim.spice import find_ngspice_shared_library

    if find_ngspice_shared_library() is None:
        pytest.skip("shared libngspice is not installed")
    from pfc_design.gui import main_window

    # Keep QTimer's real bound callback alive through native Qt teardown.
    monkeypatch.setattr(main_window, "check_for_updates", _ignore_update_check)
    app = qt.QApplication.instance() or qt.QApplication([])
    result = main_window.PFCMainWindow()
    yield result
    result.close()
    result.deleteLater()
    from PySide6.QtCore import QCoreApplication, QEvent

    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()


def test_real_button_delivers_same_unqualified_source_and_local_edit_rejects_late_result(
    window, monkeypatch
):
    from pfc_design.control import build_pfc_control_lab_analysis

    workbench = window.control_lab_view
    view = workbench.ngspice_closed_loop_view
    device = workbench.device_loss_view
    cap = workbench.cap_thermal_view
    view.set_analysis(build_pfc_control_lab_analysis(workbench.control_lab._config()))
    view.duration_ms.setValue(0.14)
    pending = []
    monkeypatch.setattr(
        window,
        "_run_worker",
        lambda label, calculate, ready: pending.append((calculate, ready)),
    )
    errors = []
    monkeypatch.setattr(window, "_worker_error", errors.append)
    device.source.setCurrentIndex(device.source.findData("switching"))
    view.run_button.click()
    assert len(pending) == 1
    calculate, ready = pending.pop()
    raw = calculate()  # real shared ngspice, not fabricated acceptance/arrays
    ready(raw)
    assert not errors
    assert view.result is raw
    assert view.switched_cycle is device.switched_cycle is cap.switched_cycle
    assert not view.switched_cycle.accepted
    assert (
        device._last_comparison is None
        and cap.cap_result is None
        and cap.thermal_result is None
    )
    assert (
        not device.export_button.isEnabled() and not cap.cap_export_button.isEnabled()
    )
    assert "INSUFFICIENT_CYCLES" in device.summary.text()
    assert len(view.figure.axes) == 4
    # Editing local simulation inputs invalidates all consumers before the old
    # worker returns. The captured global config is intentionally unchanged.
    view.run_button.click()
    _, late = pending.pop()
    revision = view.input_revision
    view.phase_deg.setValue(view.phase_deg.value() + 1.0)
    assert view.input_revision > revision
    assert (
        view.result is None
        and device.switched_cycle is None
        and cap.switched_cycle is None
    )
    late(raw)
    assert (
        view.result is None
        and device.switched_cycle is None
        and cap.switched_cycle is None
    )
    # Sizing remains separately available after a rejected switched source.
    device.source.setCurrentIndex(device.source.findData("sizing"))
    assert device._last_comparison is not None and cap.cap_result is not None
    assert (
        device._last_comparison.switched_cycle is None
        and cap.cap_result.switched_cycle is None
    )


@pytest.mark.skipif(
    not os.environ.get("PSD_RUN_PFC_SWITCHED_GUI"),
    reason="explicit long real-ngspice desktop evidence run",
)
def test_long_real_worker_button_consumers_exports_and_source_ownership(
    window, monkeypatch, tmp_path
):
    """Opt-in real cold-start circuit, no mocked producer/result/worker."""
    import json
    import pickle
    import time
    from pathlib import Path

    import numpy as np
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox

    app = QApplication.instance()
    directory = (
        Path(os.environ.get("PSD_EVIDENCE_DIR", tmp_path)) / "pfc-switched-actual-gui"
    )
    directory.mkdir(parents=True, exist_ok=True)
    messages = []
    timer = QTimer()

    def dismiss():
        for widget in app.topLevelWidgets():
            if isinstance(widget, QMessageBox) and widget.isVisible():
                messages.append(widget.text() + "\n" + widget.detailedText())
                widget.accept()

    timer.timeout.connect(dismiss)
    timer.start(20)

    def wait(seconds=1800):
        deadline = time.monotonic() + seconds
        while window._active_workers and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.02)
        app.processEvents()
        assert not window._active_workers, "real shared-ngspice worker did not finish"
        assert window.thread_pool.waitForDone(30000)
        assert not messages, "\n".join(messages)

    workbench = window.control_lab_view
    control = workbench.control_lab
    window.show()
    control.current_ctrl["kp"].setValue(0.01)
    control.current_ctrl["ti"].setValue(0.075)
    control.waveform_line_cycles.setValue(64)
    control.waveform_integration_rate_hz.setValue(250000)
    workbench.tabs.setCurrentWidget(control)
    control.run_button.click()
    wait(180)
    old_line = workbench.device_loss_view.line_cycle
    assert old_line is workbench.cap_thermal_view.line_cycle
    assert old_line is not None
    assert old_line.convergence.status.value == "LINE_CYCLE_CONVERGED"
    view = workbench.ngspice_closed_loop_view
    view.duration_ms.setValue(
        float(os.environ.get("PSD_PFC_SWITCHED_DURATION_MS", "800.1"))
    )
    view.phase_deg.setValue(0)
    view.max_step_us.setValue(1)
    view.output_step_us.setValue(0.5)
    view.timeout_s.setValue(1600)
    workbench.tabs.setCurrentWidget(view)
    view.run_button.click()
    wait()
    source = view.switched_cycle
    if view.result is not None:
        (directory / "pfc-switched-actual-gui.pkl").write_bytes(pickle.dumps(view.result))
    assert source is not None
    (directory / "source-summary.json").write_text(
        json.dumps(source.as_dict(), indent=2), encoding="utf-8"
    )
    assert source.accepted, source.convergence
    assert source.config == control._config()
    assert (
        source
        is workbench.device_loss_view.switched_cycle
        is workbench.cap_thermal_view.switched_cycle
    )
    assert window.grab().save(str(directory / "switching-source.png"))
    device, cap = workbench.device_loss_view, workbench.cap_thermal_view
    device.source.setCurrentIndex(device.source.findData("switching"))
    comparison = device._last_comparison
    assert comparison is not None and comparison.switched_cycle is source
    assert cap.cap_result.switched_cycle is source
    assert cap.thermal_result.source_identity == source.source_identity
    assert cap.cap_result.estimated_hotspot_c is not None
    assert cap.cap_result.estimated_life_h is not None
    # Independent native I² endpoint weights and clipped power endpoints.
    t = source.qualification_time_s
    native_square = source.qualification_signals["i(vscap)"] ** 2
    a, b = source.time_s[[0, -1]]
    mask = (t > a) & (t < b)
    tt = np.r_[a, t[mask], b]
    p = np.r_[
        np.interp(a, t, native_square),
        native_square[mask],
        np.interp(b, t, native_square),
    ]
    dt = np.diff(tt)
    weights = np.r_[dt[0] / 2, (dt[:-1] + dt[1:]) / 2, dt[-1] / 2]
    oracle = float(weights @ p / (b - a))
    assert cap.cap_result.bank_ripple_current_rms_a**2 == pytest.approx(
        oracle, rel=1e-11
    )
    assert comparison.capacitor_current_rms_a**2 == pytest.approx(oracle, rel=1e-11)
    assert cap.cap_result.bank_esr_loss_w == pytest.approx(
        oracle * cap.cap_result.bank_esr_ohm
    )
    captures = {}
    for panel, name in ((device, "device-loss"), (cap, "capacitor-thermal")):
        workbench.tabs.setCurrentWidget(panel)
        app.processEvents()
        assert window.grab().save(str(directory / (name + ".png")))
    cap.tabs.setCurrentIndex(1)
    app.processEvents()
    assert window.grab().save(str(directory / "semiconductor-thermal.png"))
    destinations = iter(
        [directory / "device-comparison.json", directory / "capacitor-bank.json"]
    )
    monkeypatch.setattr(
        QFileDialog, "getSaveFileName", lambda *a, **k: (str(next(destinations)), "")
    )
    device.export_button.click()
    cap.cap_export_button.click()
    for name in ("device-comparison.json", "capacitor-bank.json"):
        data = json.loads((directory / name).read_text(encoding="utf-8"))
        assert (
            data["source_switched_cycle"]["source_identity"] == source.source_identity
        )
    captures.update(
        source_identity=source.source_identity,
        cap_rms_a=source.capacitor_current_rms_a,
        native_mean_square_oracle=oracle,
        thermal_identity=cap.thermal_result.source_identity,
        cap_esr_w=cap.cap_result.bank_esr_loss_w,
        cap_hotspot_c=cap.cap_result.estimated_hotspot_c,
        source_config_equal_visible_controls=source.config == control._config(),
    )
    for key in ("sizing", "closed_loop", "switching"):
        device.source.setCurrentIndex(device.source.findData(key))
        assert device.switched_cycle is source and cap.switched_cycle is source
        assert device.line_cycle is old_line and cap.line_cycle is old_line
        assert device._last_comparison is not None
        assert (device._last_comparison.switched_cycle is source) == (
            key == "switching"
        )

    # Modal bank edit invalidates only that bank snapshot, then rerun consumes
    # the same accepted native source with the newly entered ESR.
    def cap_modal(*a, **k):
        cap.cap_esr.setValue(cap.cap_esr.value() + 1)
        return str(directory / "stale-cap-must-not-exist.json"), ""

    monkeypatch.setattr(QFileDialog, "getSaveFileName", cap_modal)
    cap.cap_export_button.click()
    assert not (directory / "stale-cap-must-not-exist.json").exists()
    cap.cap_run_button.click()
    assert cap.cap_result.switched_cycle is source

    def device_modal(*a, **k):
        view.phase_deg.setValue(1)
        return str(directory / "stale-device-must-not-exist.json"), ""

    monkeypatch.setattr(QFileDialog, "getSaveFileName", device_modal)
    device.export_button.click()
    assert not (directory / "stale-device-must-not-exist.json").exists()
    assert (
        device.switched_cycle is None
        and cap.switched_cycle is None
        and view.result is None
    )
    assert (
        device._last_comparison is None
        and cap.cap_result is None
        and cap.thermal_result is None
    )
    captures.update(
        modal_cap_rejected=True,
        modal_device_rejected=True,
        source_switches_preserved=True,
    )
    (directory / "observer.json").write_text(json.dumps(captures, indent=2), encoding="utf-8")
    timer.stop()

"""Live Control Tools ownership, exact sampling, detached replay and C99 identity."""
from __future__ import annotations

import json
from types import SimpleNamespace

import numpy as np
import pytest

from llc_design.tests.conftest import desktop_qapp  # noqa: F401


@pytest.fixture
def host(request, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    from llc_design.core.spec import LLCDesignSpec
    from llc_design.gui.launcher import WorkspaceApplicationController
    from llc_design.gui.main_window import LLCMainWindow
    app = request.getfixturevalue("desktop_qapp")
    monkeypatch.setattr(LLCMainWindow, "_auto_check_update", lambda self: None)
    for name in ("information", "warning", "critical"):
        monkeypatch.setattr(QMessageBox, name, lambda *a, **kw: None)
    controller = WorkspaceApplicationController(LLCDesignSpec())
    yield controller
    controller.dispose()
    app.processEvents()


def _enter(widget, value, *, wait=True):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    widget = getattr(widget, "spin", widget)
    widget.setFocus()
    QTest.keyClick(widget, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
    QTest.keyClicks(widget, str(value))
    QTest.keyClick(widget, Qt.Key.Key_Return)
    if wait:
        QTest.qWait(80)


def _wait_for_controller(host, *, error=False, timeout_ms=5000):
    from PySide6.QtCore import QElapsedTimer
    from PySide6.QtTest import QTest

    editor = host.control_window
    view = host.llc_window.digital_loop_view
    elapsed = QElapsedTimer()
    elapsed.start()
    # Queued canvas draws can consume an entire fixed qWait before the overdue
    # debounce timer is dispatched. Observe its real completion, never force it.
    while editor.timer.isActive() and elapsed.elapsed() < timeout_ms:
        QTest.qWait(10)
    ready = not editor.timer.isActive() and (
        editor.current_digital is None and "ERROR" in editor.status.text()
        if error else editor.current_digital is not None and "ERROR" not in editor.status.text())
    assert ready, (
        f"Controller did not reach {'ERROR' if error else 'valid'} state: "
        f"prewarp={editor.prewarp.value()}, text={editor.prewarp.spin.text()!r}, "
        f"focus={editor.prewarp.spin.hasFocus()}, gain={editor.gain.value()}, "
        f"timer_active={editor.timer.isActive()}, remaining_ms={editor.timer.remainingTime()}, "
        f"generation={editor._design_generation}, status={editor.status.text()!r}, "
        f"digital={editor.current_digital!r}, fingerprint={view._external_controller_metadata!r}")


def _click(widget):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    QTest.mouseClick(widget, Qt.MouseButton.LeftButton)


def _action(window, text):
    from PySide6.QtWidgets import QToolBar
    for toolbar in window.findChildren(QToolBar):
        for action in toolbar.actions():
            if action.text() == text:
                _click(toolbar.widgetForAction(action))
                return
    pytest.fail(f"Toolbar action not found: {text}")


def _valid_source(host):
    from power_control_tools.models import ControllerKind
    host.show_workspace("control")
    editor = host.control_window
    editor.ctrl.setCurrentIndex(editor.ctrl.findData(ControllerKind.TWO_P_TWO_Z))
    _enter(editor.gain, .002, wait=False)
    _wait_for_controller(host)
    assert editor.current_digital is not None
    return editor.current_digital


def _workers(window, timeout=120000):
    from PySide6.QtCore import QEventLoop, QTimer
    if not window._active_workers:
        return
    loop = QEventLoop()
    timer = QTimer()
    timer.setInterval(10)
    timer.timeout.connect(lambda: loop.quit() if not window._active_workers else None)
    timer.start()
    QTimer.singleShot(timeout, loop.quit)
    loop.exec()
    timer.stop()
    assert not window._active_workers


def _show_loop(host):
    from PySide6.QtTest import QTest
    host.show_workspace("llc")
    window = host.llc_window
    view = window.digital_loop_view
    window.showNormal()
    window.resize(1800, 1300)
    window.tabs.setCurrentWidget(view)
    view.page_scroll.ensureWidgetVisible(view.run_button)
    QTest.qWait(30)
    return window, view


def _run(window, view):
    view.page_scroll.ensureWidgetVisible(view.run_button)
    _click(view.run_button)
    _workers(window)
    assert view.result is not None
    return view.result


def test_live_exact_period_locked_and_internal_period_restored(host):
    from PySide6.QtTest import QTest
    view = host.llc_window.digital_loop_view
    view.sample_us.setValue(31.125)
    exact = _valid_source(host)
    window, view = _show_loop(host)
    view.parameter_selector.setCurrentIndex(view.parameter_selector.findData("plant"))
    view.page_scroll.ensureWidgetVisible(view.sample_us)
    QTest.qWait(30)
    assert view.sample_us.isVisible() and view.sample_us.isReadOnly()
    _enter(view.sample_us, 30)
    assert view.sample_us.value() == 25
    result = _run(window, view)
    assert result.controller.sample_time_s == 1 / exact.sample_rate_hz
    assert result.adc_sampling.control_sample_time_s == result.controller.sample_time_s
    assert result.small_signal.sample_time_s == result.controller.sample_time_s
    np.testing.assert_array_equal(result.controller.numerator, exact.b)
    assert "Control Tools live" in result.controller_source and "SHA256=" in result.controller_source
    assert "控制周期 Ts=25" in view.text.toPlainText()
    # Programmatic conflicting writes cannot silently override a displayed value either.
    view.sample_us.setValue(30)
    with pytest.raises(ValueError, match="精确 Ts 不一致"):
        view._analysis_options()
    view.page_scroll.ensureWidgetVisible(view.external_controller_check)
    _click(view.external_controller_check)
    assert view.sample_us.value() == 31.125 and not view.sample_us.isReadOnly()
    result = _run(window, view)
    assert result.controller_config is not None
    assert result.controller.sample_time_s == pytest.approx(31.125e-6)


@pytest.mark.parametrize("recovery_backlog", [False, True])
def test_live_pending_error_retires_result_and_late_worker_without_local_fallback(host, recovery_backlog):
    from PySide6.QtCore import QElapsedTimer, QTimer

    from llc_design.control.design_session import controller_fingerprint
    from power_control_tools.models import DiscretizationMethod
    exact = _valid_source(host)
    window, view = _show_loop(host)
    _run(window, view)
    assert view.solution_map_view.sources
    # A completed candidate/analysis source is retired even without a worker.
    host.control_window.schedule()
    host.control_window.timer.stop()
    assert not view.solution_map_view.sources and view.result is None
    assert not view.codegen_button.isEnabled()
    host.control_window.recalculate()
    pending = []
    window.thread_pool = SimpleNamespace(start=pending.append)
    _click(view.run_button)
    old = pending.pop()
    old_result = old.function()
    host.show_workspace("control")
    editor = host.control_window
    _enter(editor.gain, .003, wait=False)
    assert editor.gain.value() == .003
    editor.timer.stop()
    assert editor.current_digital is None
    assert view._external_controller is None and window.external_control_design is None
    assert view.external_controller_check.isChecked()
    assert view.result is None and window.digital_loop_analysis is None
    assert not view.codegen_button.isEnabled() and not view.run_button.isEnabled()
    with pytest.raises(ValueError, match="尚无有效"):
        view._analysis_options()
    old.signals.result.emit(old_result)
    old.signals.finished.emit()
    assert view.result is None and not view.run_button.isEnabled()
    editor.method.setCurrentIndex(editor.method.findData(DiscretizationMethod.PREWARP_TUSTIN))
    _enter(editor.prewarp, 30000, wait=False)
    assert editor.prewarp.value() == 30000
    assert editor.timer.isActive() and editor.current_digital is None
    _wait_for_controller(host, error=True)
    assert editor.current_digital is None and "ERROR" in editor.status.text()
    assert "ERROR" in view.external_controller_status.text()
    assert view._external_controller is None and view.external_controller_check.isChecked()
    assert not view.run_button.isEnabled() and not view.codegen_button.isEnabled()
    _enter(editor.prewarp, 1000, wait=False)
    assert editor.prewarp.value() == 1000
    assert editor.timer.isActive() and editor.current_digital is None
    assert view._external_controller is None and window.external_control_design is None
    assert not view.run_button.isEnabled() and not view.codegen_button.isEnabled()
    redraws = []
    if recovery_backlog:
        def redraw_backlog():
            # Real queued GUI work exceeds the former 80 ms assumption without
            # replacing input, publication, or the production debounce timer.
            elapsed = QElapsedTimer()
            elapsed.start()
            while elapsed.elapsed() < 120:
                editor.bode_canvas.draw()
                redraws.append(True)
        QTimer.singleShot(0, redraw_backlog)
    _wait_for_controller(host)
    assert bool(redraws) == recovery_backlog
    fresh = editor.current_digital
    assert fresh is not None and fresh != exact
    assert view._external_controller is fresh and window.external_control_design is fresh
    assert view.run_button.isEnabled()
    assert view.result is None
    window, view = _show_loop(host)
    _click(view.run_button)
    pending.pop().run()
    np.testing.assert_array_equal(view.result.controller.numerator, fresh.b)
    np.testing.assert_array_equal(view.result.controller.denominator, fresh.a)
    assert view.result.controller.sample_time_s == 1 / fresh.sample_rate_hz
    assert controller_fingerprint(view.result.controller) == view._external_controller_metadata["sha256"]


@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.parametrize("snapshot_backlog", [False, True], ids=["idle", "queued-edit"])
def test_saved_exact_snapshot_detaches_from_live_editor_and_exports(host, monkeypatch, tmp_path, legacy, snapshot_backlog):
    from PySide6.QtCore import QElapsedTimer, QTimer
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QFileDialog

    from llc_design.control.design_session import controller_fingerprint, decode_model
    from power_control_tools.codegen import C99Export, read_c99_export_verification
    from power_control_tools.models import DiscretizationMethod
    host.llc_window.digital_loop_view.sample_us.setValue(31.125)
    _valid_source(host)
    editor = host.control_window
    _enter(editor.fs, 61000 if snapshot_backlog else 61543.21, wait=False)
    redraws = []
    if snapshot_backlog:
        def redraw_then_edit():
            # A later real edit after queued rendering must finish its own
            # debounce; the previous 80 ms capture can still see None here.
            elapsed = QElapsedTimer()
            elapsed.start()
            while elapsed.elapsed() < 120:
                editor.bode_canvas.draw()
                redraws.append(True)
            _enter(editor.fs, 61543.21, wait=False)
        QTimer.singleShot(0, redraw_then_edit)
    _wait_for_controller(host)
    assert bool(redraws) == snapshot_backlog
    exact = editor.current_digital
    # The editor accepts one decimal place; preserve its actual displayed rate.
    assert exact.sample_rate_hz == editor.fs.value() == 61543.2
    window, view = _show_loop(host)
    original = _run(window, view)
    path = tmp_path / "external.json"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a: (str(path), ""))
    _action(window, "保存工程 JSON")
    document = json.loads(path.read_text(encoding="utf-8"))
    saved = document["control_session"]
    metadata = saved["external_controller"]
    native = decode_model(saved["inputs"])["digital_loop"]["controller_transfer_function"]
    assert metadata["sha256"] == controller_fingerprint(native)
    assert metadata["local_sample_time_s"] == pytest.approx(31.125e-6)
    if legacy:
        saved.pop("external_controller")
        # Baseline v1 sessions stored only the editor label, not live metadata.
        saved["inputs"]["digital_loop"]["controller_source"] = "2P2Z"
        path.write_text(json.dumps(document), encoding="utf-8")
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a: (str(path), ""))
    _action(window, "加载 JSON")
    _workers(window)
    assert view.result is None and not view.codegen_button.isEnabled()
    assert not view._external_controller_live
    assert "独立" in view.external_controller_check.text()
    assert "snapshot" in view.external_controller_status.text()
    if legacy:
        assert "unknown (legacy session)" in view.external_controller_status.text()
    editor = host.control_window
    host.show_workspace("control")
    editor.method.setCurrentIndex(editor.method.findData(DiscretizationMethod.PREWARP_TUSTIN))
    _enter(editor.prewarp, 40000, wait=False)
    _wait_for_controller(host, error=True)
    assert editor.current_digital is None
    window, view = _show_loop(host)
    rebuilt = _run(window, view)
    np.testing.assert_array_equal(rebuilt.controller.numerator, exact.b)
    np.testing.assert_array_equal(rebuilt.controller.denominator, exact.a)
    assert rebuilt.controller.sample_time_s == original.controller.sample_time_s
    assert "snapshot" in rebuilt.controller_source and "Control Tools live:" not in rebuilt.controller_source
    assert "SHA256=" in rebuilt.controller_source
    # Unrelated successful live publication must not replace a selected saved snapshot.
    _enter(editor.prewarp, 1000, wait=False)
    _wait_for_controller(host)
    assert view.result is rebuilt and view._external_controller.b == exact.b
    out = tmp_path / "export"
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *a: str(out))
    view.page_scroll.ensureWidgetVisible(view.codegen_button)
    QTest.qWait(30)
    _click(view.codegen_button)
    header = out / "llc_vloop.h"
    report = read_c99_export_verification(C99Export(header, 1))
    assert report["status"] == "PASS", report
    assert report["controller"]["b"] == list(exact.b)
    assert report["controller"]["sample_rate_hz"] == pytest.approx(exact.sample_rate_hz)
    assert report["controller"]["source"] == rebuilt.controller_source
    assert "snapshot" in header.read_text(encoding="utf-8") and metadata["sha256"] in header.read_text(encoding="utf-8")
    # Save/reopen once more: metadata and coefficient identity must remain stable.
    _action(window, "保存工程 JSON")
    resaved = json.loads(path.read_text(encoding="utf-8"))["control_session"]
    assert resaved["external_controller"]["sha256"] == metadata["sha256"]
    if not legacy:
        assert resaved["external_controller"]["captured_at_utc"] == metadata["captured_at_utc"]
    _action(window, "加载 JSON")
    _workers(window)
    assert view.result is None and not view.codegen_button.isEnabled()
    assert not view._external_controller_live
    reloaded = _run(window, view)
    np.testing.assert_array_equal(reloaded.controller.numerator, exact.b)
    np.testing.assert_array_equal(reloaded.controller.denominator, exact.a)
    assert reloaded.controller.sample_time_s == rebuilt.controller.sample_time_s
    reloaded_out = tmp_path / "export-reloaded"
    assert not reloaded_out.exists()
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *a: str(reloaded_out))
    view.page_scroll.ensureWidgetVisible(view.codegen_button)
    QTest.qWait(30)
    _click(view.codegen_button)
    header = reloaded_out / "llc_vloop.h"
    report = read_c99_export_verification(C99Export(header, 1))
    assert report["status"] == "PASS", report
    assert report["controller"]["b"] == list(exact.b)
    assert report["controller"]["a"] == list(exact.a)
    assert report["controller"]["sample_rate_hz"] == 1 / reloaded.controller.sample_time_s
    assert report["controller"]["source"] == reloaded.controller_source
    view.page_scroll.ensureWidgetVisible(view.external_controller_check)
    _click(view.external_controller_check)
    assert view.sample_us.value() == 31.125
    assert "重新计算" in view.external_controller_check.toolTip()
    host.show_workspace("control")
    _action(editor, "重新计算")
    assert view._external_controller_live and view.external_controller_check.isChecked()
    assert view._external_controller == editor.current_digital
    assert view._external_controller.b != exact.b
    assert view.result is None
    _click(view.external_controller_check)
    assert view.sample_us.value() == 31.125


@pytest.mark.parametrize("field,value", [("sha256", "wrong"), ("mode", "live"),
                                        ("local_sample_time_s", False), ("captured_at_utc", "bad")])
def test_invalid_saved_controller_metadata_rejected_atomically(host, monkeypatch, tmp_path, field, value):
    from PySide6.QtWidgets import QFileDialog

    from llc_design.gui import main_window
    _valid_source(host)
    window, view = _show_loop(host)
    original = _run(window, view)
    path = tmp_path / "bad.json"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a: (str(path), ""))
    _action(window, "保存工程 JSON")
    document = json.loads(path.read_text(encoding="utf-8"))
    document["control_session"]["external_controller"][field] = value
    path.write_text(json.dumps(document), encoding="utf-8")
    errors = []
    monkeypatch.setattr(main_window, "show_operation_issue", lambda *a, **kw: errors.append(a))
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a: (str(path), ""))
    _action(window, "加载 JSON")
    assert errors and view.result is original and window.digital_loop_analysis is original
    assert view.codegen_button.isEnabled()

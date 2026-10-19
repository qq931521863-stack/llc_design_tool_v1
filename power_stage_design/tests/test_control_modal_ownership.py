"""Real Qt/model/writer regressions for interrupted control-workspace dialogs."""

import os

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtCore import QEvent, QEventLoop, QTimer
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox, QTabWidget

from power_control_tools.codegen import render_c99_single_file
from power_control_tools.controllers import EXACT_CONTROLLER
from power_control_tools.fra.loop_session import read_loop_session, write_loop_session
from power_control_tools.fra.models import FRAMeasurement, FRASourceFormat
from power_control_tools.fra.tuning_session import (
    OperatingPoint,
    PIParameters,
    Scan,
    Session,
    export_scan_csv,
)
from power_control_tools.gui.fra_loop_designer import FRALoopDesignerWindow
from power_control_tools.gui.fra_tuning_map import FRATuningMapWindow
from power_control_tools.gui.main_window import ControlToolsMainWindow
from power_control_tools.gui.quasi_pr_view import QuasiPRView
from power_control_tools.models import ControllerKind
from power_control_tools.quasi_pr_codegen import coefficient_json, render_quasi_pr_c99


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def windows(app, monkeypatch):
    owned = []
    messages = []
    for name in ("information", "warning", "critical"):
        monkeypatch.setattr(QMessageBox, name, lambda *args: messages.append(args))
    for module in ("main_window", "fra_loop_designer"):
        monkeypatch.setattr(
            f"power_control_tools.gui.{module}.show_operation_issue",
            lambda *args, **kwargs: messages.append(args),
        )

    def create(kind):
        cls = {"control": ControlToolsMainWindow, "fra": FRALoopDesignerWindow,
               "pr": QuasiPRView, "map": FRATuningMapWindow}[kind]
        window = cls()
        owned.append(window)
        if kind == "control":
            window.ctrl.setCurrentIndex(window.ctrl.findData(ControllerKind.PI))
            window.kp.setValue(0.4)
            window.recalculate()
        elif kind == "fra":
            window.measurement = measurement()
            window.new_mode.setCurrentIndex(window.new_mode.findData("structure"))
            window.new_kind.setCurrentIndex(window.new_kind.findData(EXACT_CONTROLLER))
            window.new_exact_b.setText("0.2, -0.18")
            window.new_exact_a.setText("1, -0.6")
            window.recalculate()
            assert window.current_new_controller is not None, window.summary.toPlainText()
        elif kind == "map":
            window.add_scan(scan())
        return window

    yield create, messages
    for window in reversed(owned):
        for timer in window.findChildren(QTimer):
            timer.stop()
        window.close()
        window.deleteLater()
        app.sendPostedEvents(window, QEvent.Type.DeferredDelete)
    app.processEvents()


def measurement(gain=1.0):
    frequency = np.geomspace(10, 10000, 64)
    response = gain / (1 + 1j * frequency / 1000)
    return FRAMeasurement(
        frequency, 20 * np.log10(abs(response)), np.angle(response, deg=True),
        FRASourceFormat.GENERIC,
    )


def scan(name="baseline", gain=1.0):
    return Scan(name, measurement(gain), PIParameters(0.3, 0.001, 1e-5),
                OperatingPoint(400, 1000, "CCM", "bench"))


def dialog(monkeypatch, target, change=lambda: None, *, opening=False):
    """Exercise the nested Qt event loop entered by a modal file picker."""
    def choose(*args, **kwargs):
        loop = QEventLoop()
        errors = []

        def dispatch():
            try:
                change()
            except (AssertionError, ValueError, TypeError, AttributeError, RuntimeError, OSError) as exc:
                errors.append(exc)
            finally:
                loop.quit()

        QTimer.singleShot(0, dispatch)
        loop.exec()
        if errors:
            raise errors[0]
        return str(target) if target else "", ""

    monkeypatch.setattr(QFileDialog, "getOpenFileName" if opening else "getSaveFileName", choose)


def navigate(window):
    tabs = window.findChildren(QTabWidget)
    tabs[-1].setCurrentIndex(tabs[-1].count() - 1)


@pytest.mark.parametrize("kind", ["control", "fra"])
@pytest.mark.parametrize("interruption", ["recompute", "clear", "invalidate_restore", "close", "cancel", "view"])
def test_c99_export_owns_clicked_controller(windows, monkeypatch, tmp_path, kind, interruption):
    create, messages = windows
    window = create(kind)
    attribute = "current_digital" if kind == "control" else "current_new_controller"
    original = getattr(window, attribute)
    target = tmp_path / "controller.h"
    target.write_text("existing file", encoding="utf-8")

    def change():
        if interruption == "recompute":
            if kind == "control":
                window.kp.setValue(0.8)
            else:
                window.new_exact_b.setText("0.4, -0.36")
            assert getattr(window, attribute) is None
            window.recalculate()
            assert getattr(window, attribute).b != original.b
        elif interruption == "clear":
            setattr(window, attribute, None)
        elif interruption == "invalidate_restore":
            invalidate = window._invalidate_design if kind == "control" else window._invalidate_result
            invalidate()
            setattr(window, attribute, original)
        elif interruption == "close":
            window.close()
        elif interruption == "view":
            if kind == "control":
                window.tabs.setCurrentIndex(1)
            else:
                navigate(window)

    dialog(monkeypatch, None if interruption == "cancel" else target, change)
    window.export_c99()
    if interruption == "view":
        assert target.read_text(encoding="utf-8") == render_c99_single_file(original, prefix=window.export_prefix.text())
    else:
        assert target.read_text(encoding="utf-8") == "existing file"
        assert not (tmp_path / "_verify_filter.c").exists()
        assert not messages
    window.recalculate()
    current = getattr(window, attribute)
    assert current is not None
    dialog(monkeypatch, target)
    window.export_c99()
    assert target.read_text(encoding="utf-8") == render_c99_single_file(current, prefix=window.export_prefix.text())
    assert (tmp_path / "_verify_filter.c").exists()


@pytest.mark.parametrize("as_json", [False, True])
@pytest.mark.parametrize("interruption", ["recompute", "pending_edit", "clear", "invalidate_restore", "close", "cancel", "view"])
def test_pr_export_owns_clicked_config(windows, monkeypatch, tmp_path, as_json, interruption):
    create, _ = windows
    window = create("pr")
    target = tmp_path / ("pr.json" if as_json else "pr.h")
    target.write_text("existing file", encoding="utf-8")
    original = window.config
    renderer = coefficient_json if as_json else render_quasi_pr_c99

    def change():
        if interruption in ("recompute", "pending_edit"):
            window.kp.setValue(0.2)
            assert window.config is None
            assert not window.export_button.isEnabled()
            window.timer.stop()
            if interruption == "recompute":
                window.recalculate()
        elif interruption == "clear":
            window.config = None
        elif interruption == "invalidate_restore":
            config = window.config
            window._invalidate_config()
            window.config = config
        elif interruption == "close":
            window.close()
        elif interruption == "view":
            navigate(window)

    dialog(monkeypatch, None if interruption == "cancel" else target, change)
    window._export(json=as_json)
    assert target.read_text(encoding="utf-8") == (renderer(original) if interruption == "view" else "existing file")
    window.recalculate()
    current = window.config
    dialog(monkeypatch, target)
    window._export(json=as_json)
    assert target.read_text(encoding="utf-8") == renderer(current)


@pytest.mark.parametrize("field", ["branch", "freeze", "bandwidth"])
def test_pr_all_input_callbacks_invalidate_before_debounce(windows, field):
    create, _ = windows
    window = create("pr")
    if field == "branch":
        window.branch_widgets[3][0].setChecked(True)
    elif field == "freeze":
        window.freeze.setChecked(True)
    else:
        window.branch_widgets[1][2].setValue(2)
    assert window.config is None
    assert not window.export_button.isEnabled()
    assert not window.json_button.isEnabled()


@pytest.mark.parametrize("interruption", ["recompute", "clear", "mutate", "invalidate_restore", "close", "cancel", "view"])
def test_fra_session_save_rejects_invalidated_snapshot(windows, monkeypatch, tmp_path, interruption):
    create, messages = windows
    window = create("fra")
    target = tmp_path / "fra.json"
    target.write_text("existing file", encoding="utf-8")
    original = window._session_snapshot().to_document()

    def change():
        if interruption == "recompute":
            window.new_exact_b.setText("0.4, -0.36")
            window.recalculate()
        elif interruption == "clear":
            window.current_new_controller = None
        elif interruption == "mutate":
            window.measurement = measurement(2)
        elif interruption == "invalidate_restore":
            controller = window.current_new_controller
            window._invalidate_result()
            window.current_new_controller = controller
        elif interruption == "close":
            window.close()
        elif interruption == "view":
            navigate(window)

    dialog(monkeypatch, None if interruption == "cancel" else target, change)
    window.save_session()
    if interruption == "view":
        assert read_loop_session(target).to_document() == original
    else:
        assert target.read_text(encoding="utf-8") == "existing file"
        assert not messages
    window.recalculate()
    current = window._session_snapshot().to_document()
    dialog(monkeypatch, target)
    window.save_session()
    assert read_loop_session(target).to_document() == current
    window.new_exact_b.setText("0.6, -0.54")
    window.recalculate()
    dialog(monkeypatch, target, opening=True)
    window.open_session()
    assert window._session_snapshot().to_document() == current


@pytest.mark.parametrize("operation", ["save", "export"])
@pytest.mark.parametrize("interruption", ["add", "targets", "restore_targets", "selection", "mutate", "clear", "close", "cancel", "view"])
def test_tuning_map_owns_session_and_scan(windows, monkeypatch, tmp_path, operation, interruption):
    create, messages = windows
    window = create("map")
    if interruption == "selection":
        window.add_scan(scan("second", 2))
    target = tmp_path / ("map.json" if operation == "save" else "map.csv")
    target.write_text("existing file", encoding="utf-8")
    action = window.save_session if operation == "save" else window.export_current
    expected = window.session.to_json() if operation == "save" else export_scan_csv(window.session.current)

    def change():
        if interruption == "add":
            window.add_scan(scan("new", 2))
        elif interruption == "targets":
            window.fc.setValue(1500)
        elif interruption == "restore_targets":
            window.fc.setValue(1500)
            window.fc.setValue(1000)
        elif interruption == "selection":
            window.scan_selector.setCurrentIndex(0)
        elif interruption == "mutate":
            window.session.current.measurement = measurement(2)
        elif interruption == "clear":
            window.session.scans.clear()
            window.session.current_index = -1
        elif interruption == "close":
            window.close()
        elif interruption == "view":
            window.refresh()

    dialog(monkeypatch, None if interruption == "cancel" else target, change)
    action()
    assert target.read_bytes() == (expected.encode() if interruption == "view" else b"existing file")
    assert not messages
    if not window.session.current:
        window.add_scan(scan("fresh", 2))
    expected = window.session.to_json() if operation == "save" else export_scan_csv(window.session.current)
    dialog(monkeypatch, target)
    action()
    assert target.read_bytes() == expected.encode()
    if operation == "save":
        window.add_scan(scan("later", 3))
        dialog(monkeypatch, target, opening=True)
        window.open_session()
        assert window.session.to_json() == expected


@pytest.mark.parametrize("kind", ["fra", "map"])
@pytest.mark.parametrize("interruption", ["edit", "close", "cancel"])
def test_session_open_cannot_replace_newer_state(windows, monkeypatch, tmp_path, kind, interruption):
    create, _ = windows
    window = create(kind)
    target = tmp_path / "load.json"
    if kind == "fra":
        write_loop_session(window._session_snapshot(), target)
    else:
        target.write_text(window.session.to_json(), encoding="utf-8")
    original_session = window.session if kind == "map" else None

    def change():
        if interruption == "edit":
            if kind == "fra":
                window.new_exact_b.setText("0.6, -0.54")
                window.recalculate()
            else:
                window.add_scan(scan("newer", 3))
        elif interruption == "close":
            window.close()

    dialog(monkeypatch, None if interruption == "cancel" else target, change, opening=True)
    window.open_session()
    if interruption == "edit":
        if kind == "fra":
            assert window.current_new_controller.b == (0.6, -0.54)
        else:
            assert window.session.current.name == "newer"
    elif interruption == "close" and kind == "fra":
        assert window.current_new_controller is None
    elif kind == "map":
        assert window.session is original_session
    dialog(monkeypatch, target, opening=True)
    window.open_session()
    if kind == "fra":
        assert window._session_snapshot().to_document() == read_loop_session(target).to_document()
    else:
        assert window.session.to_json() == Session.from_json(target.read_text(encoding="utf-8")).to_json()


def test_parent_close_invalidates_nested_pr_export(windows, monkeypatch, tmp_path):
    create, _ = windows
    window = create("control")
    window.workspace_pages.setCurrentWidget(window.pr_view)
    target = tmp_path / "nested-pr.h"
    target.write_text("existing file", encoding="utf-8")
    dialog(monkeypatch, target, window.close)
    window.export_c99()
    assert target.read_text(encoding="utf-8") == "existing file"
    assert window.pr_view.config is None
    window.pr_view.recalculate()
    expected = render_quasi_pr_c99(window.pr_view.config)
    dialog(monkeypatch, target)
    window.export_c99()
    assert target.read_text(encoding="utf-8") == expected


@pytest.mark.parametrize("operation", ["save", "export"])
def test_parent_close_invalidates_tuning_map_dialog(windows, monkeypatch, tmp_path, operation):
    create, _ = windows
    window = create("fra")
    window.open_tuning_map()
    child = window._tuning_map_window
    child.add_scan(scan())
    target = tmp_path / ("nested-map.json" if operation == "save" else "nested-map.csv")
    target.write_text("existing file", encoding="utf-8")
    action = child.save_session if operation == "save" else child.export_current
    dialog(monkeypatch, target, window.close)
    action()
    assert target.read_text(encoding="utf-8") == "existing file"
    assert not child.isVisible()
    window.open_tuning_map()
    assert window._tuning_map_window is child
    expected = child.session.to_json() if operation == "save" else export_scan_csv(child.session.current)
    dialog(monkeypatch, target)
    action()
    assert target.read_bytes() == expected.encode()

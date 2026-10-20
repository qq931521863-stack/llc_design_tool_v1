"""Actual PFC exact-H(z) manifest/codegen ownership across modal Qt dispatch."""
from __future__ import annotations

import hashlib
import json
from dataclasses import replace

import numpy as np
import pytest

from llc_design.tests.conftest import desktop_qapp  # noqa: F401
from pfc_design.control import (
    PFCControlLabConfig,
    build_pfc_control_lab_analysis,
    illustrative_ttpl_config,
    tune_pfc_current_loop,
)
from power_codegen.generator import validate_ttpl_codegen


@pytest.fixture(scope="module")
def analysis():
    # Current-loop tuning alone cannot qualify the legacy 5.396 V bus-ADC
    # input. Positive C99 ownership tests need an explicitly in-range front end.
    base = illustrative_ttpl_config()
    tuning = tune_pfc_current_loop(base)
    assert tuning.accepted
    result = build_pfc_control_lab_analysis(replace(base, current_controller=tuning.controller))
    assert validate_ttpl_codegen(result).passed
    return result


@pytest.fixture
def desktop(request, monkeypatch):
    from pyqtgraph import GraphicsLayoutWidget
    from PySide6.QtCore import QCoreApplication, QEvent, QTimer
    from PySide6.QtWidgets import QMessageBox

    from pfc_design.gui.main_window import PFCMainWindow

    app = request.getfixturevalue("desktop_qapp")
    monkeypatch.setattr(PFCMainWindow, "_auto_check_update", lambda self: None)
    windows, messages = [], []
    for kind in ("information", "warning", "critical"):
        monkeypatch.setattr(QMessageBox, kind, lambda *a, k=kind: messages.append((k, a[2])))
    yield windows, messages
    for window in reversed(windows):
        for timer in window.findChildren(QTimer):
            timer.stop()
        for graphics in window.findChildren(GraphicsLayoutWidget):
            if not graphics.closed:
                graphics.close()
        window.close()
        window.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()


def _dialog(monkeypatch, tmp_path, action, interrupt=lambda: None):
    from PySide6.QtCore import QEventLoop, QTimer
    from PySide6.QtWidgets import QFileDialog

    def choose(*args):
        nested = QEventLoop()
        errors = []
        def event():
            try:
                interrupt()
            except (AssertionError, ValueError, TypeError, AttributeError) as exc:
                errors.append(exc)
            finally:
                nested.quit()
        QTimer.singleShot(0, event)
        nested.exec()
        if errors:
            raise errors[0]
        return str(tmp_path) if action == "codegen" else (str(tmp_path / "manifest.json"), "")

    monkeypatch.setattr(QFileDialog, "getExistingDirectory" if action == "codegen" else "getSaveFileName", choose)


def _export(view, action):
    (view._generate_c99 if action == "codegen" else view._export_manifest)()


def _assert_current_artifact(tmp_path, action, analysis):
    path = tmp_path / ("ttpl_control_generated/exact_hz_manifest.json" if action == "codegen" else "manifest.json")
    manifest = json.loads(path.read_text())
    np.testing.assert_array_equal(manifest["controllers"]["current"]["b"], analysis.current_loop.controller.numerator)
    np.testing.assert_array_equal(manifest["controllers"]["voltage"]["a"], analysis.voltage_loop.controller.denominator)
    if action == "codegen":
        validation = json.loads((path.parent / "c99_verification.json").read_text())
        assert validation == manifest["codegen_verification"]
        assert validation["linear_coefficient_identity"] == "PASS"
        assert validation["stability_configuration_gate"] == "PASS"
        assert validation["float32_execution"] == "NOT_RUN"
        assert validation["hardware_qualification"] == "NOT_RUN"
        for filename, digest in validation["source_sha256"].items():
            assert hashlib.sha256((path.parent / filename).read_bytes()).hexdigest() == digest


@pytest.mark.parametrize("action", ["manifest", "codegen"])
def test_busy_direct_callback_and_completion_cannot_export(desktop, analysis, tmp_path, monkeypatch, action):
    from PySide6.QtWidgets import QFileDialog

    from pfc_design.gui.exact_hz_view import TTPLExactHzView
    windows, messages = desktop
    view = TTPLExactHzView()
    windows.append(view)
    view.set_analysis(analysis)
    view.set_busy(True)
    view.set_analysis(analysis)  # A completion must not bypass the busy contract.
    assert not view.export_button.isEnabled() and not view.codegen_button.isEnabled()
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *a: pytest.fail("busy export reached dialog"))
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a: pytest.fail("busy export reached dialog"))
    _export(view, action)
    assert not list(tmp_path.iterdir()) and not messages
    view.set_busy(False)
    _dialog(monkeypatch, tmp_path, action)
    _export(view, action)
    _assert_current_artifact(tmp_path, action, analysis)


@pytest.mark.parametrize("action", ["manifest", "codegen"])
@pytest.mark.parametrize("interruption", ["busy", "busy_cycle", "new_result", "clear_refill", "cancel", "navigation"])
def test_real_modal_export_owns_clicked_pfc_analysis(desktop, analysis, tmp_path, monkeypatch, action, interruption):
    from PySide6.QtWidgets import QFileDialog

    from pfc_design.gui.exact_hz_view import TTPLExactHzView
    windows, messages = desktop
    view = TTPLExactHzView()
    windows.append(view)
    view.set_analysis(analysis)
    def interrupt():
        if interruption.startswith("busy"):
            view.set_busy(True)
            if interruption == "busy_cycle":
                view.set_busy(False)
        elif interruption == "new_result":
            config = replace(analysis.config, power_stage=replace(analysis.config.power_stage, line_angle_deg=63.0))
            view.set_analysis(build_pfc_control_lab_analysis(config))
        elif interruption == "clear_refill":
            view.analysis = view.handoff = None
            view.set_analysis(analysis)
        else:
            view.tabs.setCurrentIndex(1)
    _dialog(monkeypatch, tmp_path, action, interrupt)
    if interruption == "cancel":
        monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *a: "")
        monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a: ("", ""))
    _export(view, action)
    if interruption == "navigation":
        _assert_current_artifact(tmp_path, action, analysis)
    else:
        assert not list(tmp_path.iterdir()) and not messages
    view.set_busy(False)
    _dialog(monkeypatch, tmp_path, action)
    _export(view, action)
    _assert_current_artifact(tmp_path, action, view.analysis)


@pytest.mark.parametrize("action", ["manifest", "codegen"])
def test_real_host_input_edit_during_dialog_invalidates_then_new_analysis_exports(desktop, analysis, tmp_path, monkeypatch, action):
    from pfc_design.gui.main_window import PFCMainWindow
    windows, messages = desktop
    host = PFCMainWindow()
    windows.append(host)
    view = host.control_lab_view.exact_hz_view
    control = host.control_lab_view.control_lab
    # Set the real editor fields too: recomputation after the angle edit must
    # not fall back to the historical overrange divider.
    control.vbus_sense["gain"].setValue(1600.0 / (249000.0 + 1600.0))
    control.vbus_sense["source_r"].setValue(249000.0 * 1600.0 / (249000.0 + 1600.0))
    view.set_analysis(analysis)
    _dialog(monkeypatch, tmp_path, action, lambda: control.angle.setValue(63.0))
    _export(view, action)
    assert view.analysis is None and not list(tmp_path.iterdir()) and not messages
    config = control._config()
    tuning = tune_pfc_current_loop(config)
    assert tuning.accepted
    fresh = build_pfc_control_lab_analysis(replace(config, current_controller=tuning.controller))
    assert fresh.config.power_stage.line_angle_deg == 63.0
    assert validate_ttpl_codegen(fresh).passed
    view.set_analysis(fresh)
    _dialog(monkeypatch, tmp_path, action)
    _export(view, action)
    _assert_current_artifact(tmp_path, action, fresh)


def test_legacy_adc_overrange_is_rejected_then_current_valid_analysis_exports(
    desktop, analysis, tmp_path, monkeypatch,
):
    from pfc_design.gui.exact_hz_view import TTPLExactHzView

    windows, messages = desktop
    legacy = replace(analysis.config, vbus_sense=PFCControlLabConfig().vbus_sense)
    assert legacy.power_stage.bus_voltage_v * legacy.vbus_sense.raw_dc_gain == pytest.approx(
        5.396290050590219,
    )
    assert legacy.vbus_sense.adc_vref_v == 3.3
    rejected = build_pfc_control_lab_analysis(legacy)
    # The actual configuration, not a carried warning, must stop C99 export.
    rejected = replace(rejected, warnings=())
    validation = validate_ttpl_codegen(rejected)
    assert not validation.passed
    assert all("(PASS)" in check for check in validation.checks if "margin screen=" in check)
    assert any("Configured ADC magnitude range: FAIL" in check for check in validation.checks)

    view = TTPLExactHzView()
    windows.append(view)
    view.set_analysis(rejected)
    _dialog(monkeypatch, tmp_path, "codegen")
    _export(view, "codegen")
    assert not list(tmp_path.iterdir())
    assert len(messages) == 1
    assert messages[0][0] == "critical"
    assert "model-evidence gate failed" in messages[0][1]

    messages.clear()
    view.set_analysis(analysis)
    _export(view, "codegen")
    _assert_current_artifact(tmp_path, "codegen", analysis)
    assert len(messages) == 1 and messages[0][0] == "information"

"""Release acceptance for real desktop C99 exports, including a numerical FAIL.

The repeated-pole counterexample is numerically stable in float64, but the
existing float32 host budget fails. The budget is intentionally not relaxed.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import numpy as np
import pytest

from llc_design.tests.conftest import desktop_qapp  # noqa: F401
from power_control_tools import codegen
from power_control_tools.models import DigitalTransferFunction

PASS_CONTROLLER = DigitalTransferFunction((0.0, .26, -.042, .014), (2.0, -.62, .074), 50000.0)
FAIL_CONTROLLER = DigitalTransferFunction((.001,), (1.0, -1.98, .9801), 50000.0)


def _compile_header(path: Path) -> subprocess.CompletedProcess:
    harness = path.parent / "ordinary_include.c"
    harness.write_text(f'#include "{path.name}"\nint main(void) {{ return 0; }}\n')
    return subprocess.run(["gcc", "-std=c99", "-Wall", "-Wextra", "-Werror", str(harness),
                           "-o", str(path.parent / "ordinary_include")], capture_output=True, text=True, check=False)


@pytest.mark.parametrize("state", ["PASS", "FAIL", "NOT_RUN"])
def test_real_compiled_delivery_status_and_default_compile_gate(tmp_path, monkeypatch, state):
    controller = FAIL_CONTROLLER if state == "FAIL" else PASS_CONTROLLER
    if state == "NOT_RUN":
        monkeypatch.setattr(codegen.shutil, "which", lambda compiler: None)
    exported, result = codegen.export_verified_c99_filter(controller, tmp_path / "actual.h", prefix="ASYMMETRIC")
    assert result.status == state
    report = codegen.read_c99_export_verification(exported)
    assert report["status"] == state and report["passed"] is (state == "PASS")
    assert report["controller"]["b"] == list(controller.normalized().b)
    assert report["controller"]["a"] == list(controller.normalized().a)
    assert report["controller"]["sample_rate_hz"] == controller.sample_rate_hz
    compilation = _compile_header(exported.file_path)
    assert (compilation.returncode == 0) is (state == "PASS"), compilation.stderr
    assert not list(tmp_path.glob(".c99-verification-*")), "Do not leave an unblocked diagnostic copy"
    if state != "PASS":
        assert "#error" in compilation.stderr
        assert f"VERIFICATION_STATUS_{state}" in exported.file_path.read_text()
    else:
        assert exported.file_path.read_text() == codegen.render_c99_single_file(controller, prefix="ASYMMETRIC")
        raw = subprocess.check_output([str(tmp_path / "_verify_filter.exe")], text=True).splitlines()
        actual = np.asarray([float(line.split()[1]) for line in raw if line.startswith("I ")])
        # Independent causal recurrence; do not reuse production SOS/lfilter.
        b, a = controller.b, controller.a
        expected = np.zeros(len(actual))
        for n in range(len(expected)):
            feed = b[n] if n < len(b) else 0.0
            feedback = sum(a[k] * expected[n-k] for k in range(1, min(n+1, len(a))))
            expected[n] = (feed-feedback) / a[0]
        np.testing.assert_allclose(actual, expected, atol=2e-8, rtol=2e-6)


def test_saved_pass_is_invalid_after_actual_header_changes(tmp_path):
    exported, result = codegen.export_verified_c99_filter(PASS_CONTROLLER, tmp_path / "bound.h")
    assert result.passed
    exported.file_path.write_text(exported.file_path.read_text().replace("2.600000000e-01", "9.900000000e-01") + "\n/* edited */\n")
    report = codegen.read_c99_export_verification(exported)
    assert report["status"] == "FAIL" and not report["passed"]
    assert "changed after verification" in report["message"]


@pytest.mark.parametrize("failure", ["report", "header"])
def test_interrupted_acceptance_never_releases_unverified_header(tmp_path, monkeypatch, failure):
    original = codegen._atomic_text

    def fail(path, text):
        if failure == "report" and path.suffix == ".json" and json.loads(text)["status"] == "PASS":
            raise OSError("simulated acceptance report failure")
        if failure == "header" and path.suffix == ".h" and "#error" not in text:
            raise OSError("simulated accepted header replacement failure")
        original(path, text)

    monkeypatch.setattr(codegen, "_atomic_text", fail)
    with pytest.raises(OSError, match="simulated"):
        codegen.export_verified_c99_filter(PASS_CONTROLLER, tmp_path / "interrupted.h")
    path = tmp_path / "interrupted.h"
    assert _compile_header(path).returncode != 0
    exported = codegen.C99Export(path, 1)
    assert codegen.read_c99_export_verification(exported)["status"] != "PASS"


@pytest.fixture
def desktop(request, monkeypatch):
    from PySide6.QtCore import QEvent, QTimer
    from PySide6.QtWidgets import QMessageBox

    app = request.getfixturevalue("desktop_qapp")
    windows, messages = [], []
    for kind in ("information", "warning", "critical"):
        monkeypatch.setattr(QMessageBox, kind, lambda *a, k=kind: messages.append((k, a[1], a[2])))
    yield windows, messages
    for window in reversed(windows):
        for timer in window.findChildren(QTimer):
            timer.stop()
        window.close()
        window.deleteLater()
        app.sendPostedEvents(window, QEvent.Type.DeferredDelete)
    app.processEvents()


def _actual_view(kind, fail, windows):
    controller = FAIL_CONTROLLER if fail else PASS_CONTROLLER
    if kind == "llc":
        from llc_design.control.analysis import build_small_signal_analysis
        from llc_design.control.digital_loop import build_digital_loop_analysis
        from llc_design.core.spec import LLCDesignSpec
        from llc_design.gui.widgets.digital_loop_view import DigitalLoopView
        window = DigitalLoopView()
        windows.append(window)
        window.set_external_controller(controller, "independent release audit")
        options = window._analysis_options()
        small = build_small_signal_analysis(LLCDesignSpec(), **options["small_signal"])
        window.set_analysis(build_digital_loop_analysis(small, **options["loop"]))
        np.testing.assert_array_equal(window.result.controller.numerator, controller.normalized().b)
        return window, window._generate_c99
    if kind == "fra":
        from power_control_tools.controllers import EXACT_CONTROLLER
        from power_control_tools.fra.models import FRAMeasurement, FRASourceFormat
        from power_control_tools.gui.fra_loop_designer import FRALoopDesignerWindow
        window = FRALoopDesignerWindow()
        windows.append(window)
        f = np.geomspace(2, 10000, 64)
        h = 1 / (1 + 1j*f/237)
        window.measurement = FRAMeasurement(f, 20*np.log10(abs(h)), np.angle(h, deg=True), FRASourceFormat.GENERIC)
        window.new_mode.setCurrentIndex(window.new_mode.findData("structure"))
        window.new_kind.setCurrentIndex(window.new_kind.findData(EXACT_CONTROLLER))
        window.new_exact_b.setText(",".join(map(repr, controller.b)))
        window.new_exact_a.setText(",".join(map(repr, controller.a)))
        window.new_fs.setValue(controller.sample_rate_hz)
        window.step_enable.setChecked(False)
        window.recalculate()
        assert window.current_new_controller is not None
        return window, window.export_c99
    from power_control_tools.gui.main_window import ControlToolsMainWindow
    from power_control_tools.models import ControllerKind, DiscretizationMethod
    window = ControlToolsMainWindow()
    windows.append(window)
    window.ctrl.setCurrentIndex(window.ctrl.findData(ControllerKind.GENERAL))
    window.method.setCurrentIndex(window.method.findData(DiscretizationMethod.TUSTIN))
    window.fs.setValue(50000.0)
    k = 2*window.fs.value()
    p = .99 if fail else .3
    # Algebraic inverse of q=(2Fs-s)/(2Fs+s), independent of S2Z implementation.
    window.general_num.setText(",".join(map(repr, (.001, .002*k, .001*k*k))))
    window.general_den.setText(",".join(map(repr, ((1+p)**2, 2*k*(1-p*p), k*k*(1-p)**2))))
    window.recalculate()
    assert window.current_digital is not None
    np.testing.assert_allclose(window.current_digital.a, (1, -2*p, p*p), atol=1e-14)
    return window, window.export_c99


@pytest.mark.parametrize("kind", ["llc", "fra", "control"])
@pytest.mark.parametrize("state", ["PASS", "FAIL", "NOT_RUN"])
def test_real_desktop_export_uses_same_verified_delivery(tmp_path, monkeypatch, desktop, kind, state):
    from PySide6.QtWidgets import QFileDialog
    windows, messages = desktop
    _window, export = _actual_view(kind, state == "FAIL", windows)
    messages.clear()
    if state == "NOT_RUN":
        monkeypatch.setattr(codegen.shutil, "which", lambda compiler: None)
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *a: str(tmp_path))
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a: (str(tmp_path / "actual.h"), ""))
    export()
    path = tmp_path / ("llc_vloop.h" if kind == "llc" else "actual.h")
    report = codegen.read_c99_export_verification(codegen.C99Export(path, 1))
    assert report["status"] == state
    assert len(messages) == 1, messages
    assert messages[0][0] == ("information" if state == "PASS" else "warning"), messages
    if state != "PASS":
        assert state in messages[0][2] and "硬件" in messages[0][2]
    assert (_compile_header(path).returncode == 0) is (state == "PASS")

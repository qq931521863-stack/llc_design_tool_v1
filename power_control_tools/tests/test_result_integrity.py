"""Controller entry points and export guards use the same current coefficients."""

import os

import numpy as np
import pytest

from power_control_tools.analysis import analyze_digital_filter
from power_control_tools.models import DigitalTransferFunction

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def test_causal_fir_and_pure_delay_responses_use_z_inverse_convention():
    # H(z)=2+3z^-1+4z^-2: no state-space properness restriction in z.
    r = analyze_digital_filter(
        DigitalTransferFunction((2, 3, 4), (1,), 1000), response_samples=5
    )
    assert r.impulse == pytest.approx([2, 3, 4, 0, 0])
    assert r.step == pytest.approx([2, 5, 9, 9, 9])
    # H(z)=z^-2/(1-0.5z^-1): first nonzero output is sample two.
    r = analyze_digital_filter(
        DigitalTransferFunction((0, 0, 1), (1, -0.5), 1000), response_samples=5
    )
    assert r.impulse == pytest.approx([0, 0, 1, 0.5, 0.25])
    assert r.step == pytest.approx([0, 0, 1, 1.5, 1.75])


def test_every_controller_entry_and_discretization_analyzes(monkeypatch):
    qt = pytest.importorskip("PySide6.QtWidgets")
    from power_control_tools.gui.main_window import ControlToolsMainWindow

    app = qt.QApplication.instance() or qt.QApplication([])
    window = ControlToolsMainWindow()
    monkeypatch.setattr(
        window, "_render", lambda: None
    )  # numerical + GUI entry sweep, not 42 plot renders
    for method in range(window.method.count()):
        window.method.setCurrentIndex(method)
        for kind in range(window.ctrl.count()):
            window.ctrl.setCurrentIndex(kind)
            window.recalculate()
            assert window.current_digital is not None, (
                window.ctrl.currentText(),
                window.method.currentText(),
                window.status.text(),
            )
            assert np.isfinite(window.current_analysis.impulse).all()
            assert not window.status.text().startswith("ERROR")
    window.close()
    app.processEvents()


def test_pending_or_failed_design_cannot_export_previous_coefficients(monkeypatch):
    qt = pytest.importorskip("PySide6.QtWidgets")
    from power_control_tools.gui.main_window import ControlToolsMainWindow

    app = qt.QApplication.instance() or qt.QApplication([])
    window = ControlToolsMainWindow()
    assert window.current_digital is not None and window.c99.toPlainText()
    window.gain.setValue(2)
    assert window.current_digital is None
    assert not window.export.isEnabled() and not window.export_action.isEnabled()
    window.timer.stop()

    def fail():
        raise ValueError("deliberate invalid design")

    monkeypatch.setattr(window, "_design", fail)
    window.recalculate()
    assert (
        window.current_digital
        is window.current_analysis
        is window.current_analog
        is None
    )
    assert not window.c99.toPlainText() and not window.coeff.toPlainText()
    assert not window.bode_fig.axes
    monkeypatch.setattr(
        qt.QFileDialog,
        "getSaveFileName",
        lambda *a: pytest.fail("Invalid result must not be exported"),
    )
    window.export_c99()  # toolbar/API path also checks state, not just button enable
    window.close()
    app.processEvents()

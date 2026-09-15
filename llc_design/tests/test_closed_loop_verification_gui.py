from __future__ import annotations

import os

import pytest


def test_llc_closed_loop_verification_installs_and_receives_exact_hz():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")

    from llc_design.gui.closed_loop_install import install_closed_loop_verification
    from llc_design.gui.main_window import LLCMainWindow
    from power_control_tools.models import DigitalTransferFunction

    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    window = LLCMainWindow()
    view = install_closed_loop_verification(window)

    assert window.closed_loop_verification_view is view
    labels = [window.tabs.tabText(i) for i in range(window.tabs.count())]
    assert "Closed-Loop Verification" in labels

    exact = DigitalTransferFunction(
        (0.12, -0.10),
        (1.0, -1.0),
        40_000.0,
        "GUI exact PI",
        "Control Tools smoke",
    )
    window.external_control_design = exact
    window.external_control_label = "Control Tools / exact H(z)"
    window.refresh_closed_loop_controller()

    assert "exact H(z)" in view.controller_status.text()
    assert "40" in view.controller_status.text()
    assert view.waveform_view.GROUPS[-1][0] == "闭环控制"
    assert "switching_frequency" in view.waveform_view.GROUPS[-1][1]

    window.close()
    app.processEvents()

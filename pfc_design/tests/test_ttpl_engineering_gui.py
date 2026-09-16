from __future__ import annotations

import os

import pytest


def test_ttpl_engineering_workbench_wraps_existing_control_lab_and_applies_sizing():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")

    from pfc_design.gui.ttpl_engineering_view import TTPLWorkbenchView

    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    view = TTPLWorkbenchView()

    assert view.tabs.count() == 2
    assert view.tabs.tabText(0).startswith("1. Power Stage")
    assert "Control" in view.tabs.tabText(1)
    assert view.power_stage_view.result is not None

    result = view.power_stage_view.result
    assert result is not None
    view._apply_design_to_control(result)

    assert view.control_lab.vin_rms.value() == pytest.approx(result.spec.vin_nom_rms_v)
    assert view.control_lab.vbus.value() == pytest.approx(result.spec.bus_voltage_v)
    assert view.control_lab.pout.value() == pytest.approx(result.spec.output_power_w)
    assert view.control_lab.fsw.value() == pytest.approx(result.spec.switching_frequency_hz / 1e3)
    assert view.control_lab.inductance.value() == pytest.approx(result.required_inductance_uh, rel=1e-4)
    assert view.control_lab.cbus.value() == pytest.approx(result.recommended_bus_capacitance_uf, rel=1e-4)
    assert view.tabs.currentWidget() is view.control_lab

    view.close()
    app.processEvents()


def test_pfc_main_window_exposes_ttpl_as_engineering_workspace():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")

    from pfc_design.gui.main_window import PFCMainWindow

    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    window = PFCMainWindow()

    assert "Engineering" in window.subtabs.tabText(0)
    assert hasattr(window.control_lab_view, "power_stage_view")
    assert hasattr(window.control_lab_view, "control_lab")

    window.close()
    app.processEvents()

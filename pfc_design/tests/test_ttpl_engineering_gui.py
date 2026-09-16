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
    # Exercise a non-default efficiency so the engineering-stage assumption
    # cannot silently fall back to PFCPowerStageConfig's historical 0.97.
    view.power_stage_view.efficiency.setValue(0.945)
    view.power_stage_view.calculate()
    result = view.power_stage_view.result
    assert result is not None
    view._apply_design_to_control(result)

    assert view.control_lab.vin_rms.value() == pytest.approx(result.spec.vin_nom_rms_v)
    assert view.control_lab.vbus.value() == pytest.approx(result.spec.bus_voltage_v)
    assert view.control_lab.pout.value() == pytest.approx(result.spec.output_power_w)
    assert view.control_lab.fsw.value() == pytest.approx(result.spec.switching_frequency_hz / 1e3)
    assert view.control_lab.inductance.value() == pytest.approx(result.required_inductance_uh, rel=1e-4)
    assert view.control_lab.cbus.value() == pytest.approx(result.recommended_bus_capacitance_uf, rel=1e-4)
    assert view.control_lab._config().power_stage.efficiency == pytest.approx(result.spec.efficiency)
    assert view.tabs.currentWidget() is view.control_lab

    view.close()
    app.processEvents()


def test_pfc_main_window_exposes_eight_stage_ttpl_engineering_workflow():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")

    from pfc_design.control import build_pfc_control_lab_analysis
    from pfc_design.gui.main_window import PFCMainWindow

    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    window = PFCMainWindow()
    workbench = window.control_lab_view

    assert "Engineering" in window.subtabs.tabText(0)
    for attribute in (
        "power_stage_view",
        "device_loss_view",
        "device_database",
        "cap_thermal_view",
        "ac_performance_view",
        "switching_validation_view",
        "control_lab",
        "exact_hz_view",
        "ngspice_closed_loop_view",
    ):
        assert hasattr(workbench, attribute)

    assert workbench.tabs.count() == 8
    expected = (
        "Power Stage / Sizing",
        "Devices / Loss",
        "Capacitor / Thermal",
        "AC Line / PF / THD",
        "Switching / Zero Crossing",
        "Control / Sensing / Bode",
        "Exact H(z) / C99",
        "Closed-Loop Verification",
    )
    for index, text in enumerate(expected):
        assert text in workbench.tabs.tabText(index)

    assert workbench.device_loss_view.design is not None
    assert workbench.cap_thermal_view.design is not None
    assert workbench.cap_thermal_view.cap_result is not None
    assert workbench.cap_thermal_view.thermal_result is not None

    # Embedded AC/switching result pages are deliberately removed from the
    # Control/Bode tab so the product no longer shows duplicate workflows.
    legacy_titles = {
        "完整 AC 周期",
        "AC 控制细节",
        "局部开关周期",
        "Zero Crossing Analyzer",
        "PF / THD / Harmonics",
    }
    visible_control_titles = {
        workbench.control_lab.tabs.tabText(i)
        for i in range(workbench.control_lab.tabs.count())
    }
    assert legacy_titles.isdisjoint(visible_control_titles)
    assert len(workbench.control_lab._detached_legacy_waveform_pages) == 5

    # The old direct C99 entry point is hidden; exact H(z) must be frozen first.
    assert workbench.control_lab.codegen_button.isVisible() is False
    assert workbench.exact_hz_view.analysis is None
    assert workbench.ngspice_closed_loop_view.analysis is None
    analysis = build_pfc_control_lab_analysis(workbench.control_lab._config())
    workbench.exact_hz_view.set_analysis(analysis)
    workbench.ngspice_closed_loop_view.set_analysis(analysis)
    assert workbench.exact_hz_view.handoff is not None
    assert workbench.exact_hz_view.export_button.isEnabled()
    assert "PASS" in workbench.exact_hz_view.status.text()
    # ngspice execution may be unavailable on a developer machine, but the
    # stage must still consume the same analyzed controller object.
    assert workbench.ngspice_closed_loop_view.analysis is analysis

    # A selected physical capacitor bank must replace the Phase-1 minimum C in
    # the downstream control plant only after the explicit Apply action.
    cap_view = workbench.cap_thermal_view
    cap_result = cap_view.cap_result
    assert cap_result is not None
    cap_view.cap_apply_button.click()
    assert workbench.control_lab.cbus.value() == pytest.approx(
        cap_result.bank_capacitance_uf, rel=1e-4
    )
    assert workbench.control_lab.cbus_esr.value() == pytest.approx(
        cap_result.bank_esr_ohm * 1e3, rel=1e-4
    )
    assert workbench.tabs.currentWidget() is workbench.control_lab

    window.close()
    app.processEvents()

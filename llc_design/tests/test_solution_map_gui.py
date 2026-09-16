from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _qapp(qt_widgets):
    return qt_widgets.QApplication.instance() or qt_widgets.QApplication([])


def test_v93_launcher_installs_solution_map_for_llc_and_ttpl() -> None:
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    from llc_design.core.spec import LLCDesignSpec
    from llc_design.gui.launcher import WorkspaceApplicationController

    app = _qapp(qt_widgets)
    controller = WorkspaceApplicationController(LLCDesignSpec())
    llc_map = controller.llc_window.digital_loop_view.solution_map_view
    pfc_control = controller.pfc_window.control_lab_view.control_lab
    pfc_map = pfc_control.solution_map_view

    assert llc_map is not None
    assert pfc_map is not None
    llc_tabs = controller.llc_window.digital_loop_view.result_tabs
    assert any("Fc × PM Solution Map" == llc_tabs.tabText(i) for i in range(llc_tabs.count()))
    assert any("Fc × PM Solution Map" == pfc_control.tabs.tabText(i) for i in range(pfc_control.tabs.count()))

    for window in (
        controller.llc_window,
        controller.pfc_window,
        controller.control_window,
        controller.fra_window,
    ):
        window.close()
    app.processEvents()


def test_solution_map_view_accepts_a_full_loop_source_and_builds_map() -> None:
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    import numpy as np

    from llc_design.gui.widgets.solution_map_view import LoopMapSource, SolutionMapView

    app = _qapp(qt_widgets)
    view = SolutionMapView()
    f = np.geomspace(10.0, 20_000.0, 1200)
    pole = 1_000.0 / np.tan(np.deg2rad(60.0))
    fixed = 1.0 / (1.0 + 1j * f / pole)
    view.set_sources([
        LoopMapSource(
            key="test",
            label="Test full loop",
            frequencies_hz=f,
            fixed_loop_response=fixed,
            sample_rate_hz=50_000.0,
            switching_frequency_hz=20_000.0,
            provenance="test: plant + sensor + ADC + PWM + delay",
        )
    ])
    view.fc_min.setValue(700.0)
    view.fc_max.setValue(1400.0)
    view.fc_points.setValue(5)
    view.pm_min.setValue(50.0)
    view.pm_max.setValue(70.0)
    view.pm_points.setValue(5)
    view.gm_min.setValue(0.0)
    view.ms_max.setValue(3.0)
    view.switch_max.setValue(0.0)
    view.build_map()

    assert view.result is not None
    assert view.result.status_codes.shape == (5, 5)
    assert view.result.feasible_mask.any()
    assert "FEASIBLE" in view.details.toPlainText()

    view.close()
    app.processEvents()

from __future__ import annotations

import os

import pytest


@pytest.fixture
def view(monkeypatch):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    widgets = pytest.importorskip("PySide6.QtWidgets")
    from pfc_design.gui import ngspice_closed_loop_view as module
    app = widgets.QApplication.instance() or widgets.QApplication([])
    monkeypatch.setattr(module, "find_ngspice_shared_library", lambda **kwargs: "test-library")
    result = module.TTPLNgSpiceClosedLoopView()
    yield result
    result.close()
    app.processEvents()


def test_auto_timesteps_emit_none_and_manual_values_survive(view):
    from pfc_design.control import PFCControlLabConfig, build_pfc_control_lab_analysis
    view.set_analysis(build_pfc_control_lab_analysis(PFCControlLabConfig()))
    emissions = []
    view.run_requested.connect(lambda *args: emissions.append(args))
    assert view.max_step_us.value() == 0.0
    assert view.output_step_us.value() == 0.0
    view._run()
    assert emissions[-1][2].max_step_s is None
    assert emissions[-1][2].output_step_s is None
    view.max_step_us.setValue(0.25)
    view.output_step_us.setValue(0.5)
    view._run()
    assert emissions[-1][2].max_step_s == pytest.approx(0.25e-6)
    assert emissions[-1][2].output_step_s == pytest.approx(0.5e-6)


def test_recheck_recovers_library_and_respects_busy(view, monkeypatch):
    from pfc_design.control import PFCControlLabConfig, build_pfc_control_lab_analysis
    from pfc_design.gui import ngspice_closed_loop_view as module
    view.set_analysis(build_pfc_control_lab_analysis(PFCControlLabConfig()))
    def missing(**kwargs):
        kwargs["diagnostics"].append("example.dll: architecture mismatch")
    monkeypatch.setattr(module, "find_ngspice_shared_library", missing)
    view.check_engine_button.click()
    assert not view.run_button.isEnabled()
    assert "architecture mismatch" in view.status.text()
    monkeypatch.setattr(module, "find_ngspice_shared_library", lambda **kwargs: "restored-library")
    view.check_engine_button.click()
    assert view.run_button.isEnabled()
    assert view.library == "restored-library"
    view.set_busy(True)
    view._refresh_status()
    view._run()
    assert not view.run_button.isEnabled()
    assert not view.check_engine_button.isEnabled()
    view.set_busy(False)
    assert view.run_button.isEnabled()


def test_run_rechecks_removed_library(view, monkeypatch):
    from pfc_design.control import PFCControlLabConfig, build_pfc_control_lab_analysis
    from pfc_design.gui import ngspice_closed_loop_view as module
    view.set_analysis(build_pfc_control_lab_analysis(PFCControlLabConfig()))
    emissions = []
    view.run_requested.connect(lambda *args: emissions.append(args))
    monkeypatch.setattr(module, "find_ngspice_shared_library", lambda **kwargs: None)
    view._run()
    assert not emissions
    assert not view.run_button.isEnabled()


def test_real_shared_engine_returns_to_gui(view, monkeypatch):
    from pfc_design.control import PFCControlLabConfig, build_pfc_control_lab_analysis
    from pfc_design.gui import ngspice_closed_loop_view as module
    from power_sim.spice import find_ngspice_shared_library, run_ttpl_shared_closed_loop
    library = find_ngspice_shared_library()
    if library is None:
        pytest.skip("shared libngspice is not installed")
    monkeypatch.setattr(module, "find_ngspice_shared_library", find_ngspice_shared_library)
    view.set_analysis(build_pfc_control_lab_analysis(PFCControlLabConfig()))
    view.duration_ms.setValue(0.14)
    def run(analysis, scenario, simulation):
        result = run_ttpl_shared_closed_loop(
            analysis, scenario=scenario, simulation=simulation, library=library,
        )
        view.set_simulation_result(result)
    view.run_requested.connect(run)
    view.run_button.click()
    assert view.result is not None
    assert view.result.metadata["exact_hz_match_checked"] is True
    assert view.result.vectors["time"][-1] == pytest.approx(0.14e-3)
    assert len(view.figure.axes) == 4
    assert "run completed" in view.status.text()

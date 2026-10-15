"""Adversarial UI transitions: language, outstanding work and stale exports."""

import os
import threading
import time

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
qt = pytest.importorskip("PySide6.QtWidgets")


@pytest.fixture(scope="module")
def app():
    return qt.QApplication.instance() or qt.QApplication([])


def pump_until(app, condition):
    deadline = time.monotonic() + 15
    while not condition() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.005)
    assert condition(), "Asynchronous result did not arrive"
    app.processEvents()


def test_english_waveform_animation_and_late_control_tab_translation(app):
    from dab_design.core import DABConfig, analyze_dab
    from dab_design.gui.main_window import DABMainWindow
    from llc_design.gui.i18n_ui import retranslate_window
    from llc_design.i18n import current_language, set_language

    previous = current_language()
    window = DABMainWindow()
    try:
        window.set_result(analyze_dab(DABConfig(wave_samples=512)))
        window.animation_timer.stop()
        window.tabs.setCurrentIndex(2)
        window.show()
        set_language("en", notify=False)
        retranslate_window(window)
        app.processEvents()
        assert window.tabs.tabText(2) != "电气波形"
        assert (
            window.tabs.tabText(window.tabs.indexOf(window.control_view))
            == "Voltage Loop / TPS Optimization"
        )
        line, original = window._animated_lines[0]
        window._animate_waveform()
        assert not np.array_equal(line.get_ydata(), original)
    finally:
        set_language(previous, notify=False)
        window.close()


def test_selection_during_calculation_runs_latest_configuration(app, monkeypatch):
    from dab_design.gui import main_window as module

    real = module.analyze_dab
    release = threading.Event()
    started = threading.Event()
    calls = []

    def blocked(config):
        calls.append(config)
        if len(calls) == 1:
            started.set()
            assert release.wait(5)
        return real(config)

    monkeypatch.setattr(module, "analyze_dab", blocked)
    window = module.DABMainWindow()
    errors = []
    monkeypatch.setattr(window, "_error", errors.append)
    try:
        window.samples.setValue(512)
        window.point_choice.setCurrentIndex(1)
        assert started.wait(2)
        window.inputs["va_v"].setValue(410)
        window.point_choice.setCurrentIndex(2)
        assert window._rerun_requested
        release.set()
        pump_until(
            app,
            lambda: (
                len(calls) == 2
                and window._worker is None
                and window.result.config.va_v == 410
            ),
        )
        assert not errors
        assert calls[0].va_v == 400 and calls[1].va_v == 410
        assert window.point_choice.currentIndex() == 2
    finally:
        release.set()
        pump_until(app, lambda: window._worker is None)
        window.close()


def test_optimization_cannot_show_old_loop_code_or_false_success(app):
    from dab_design.control import DABVoltageControlConfig, simulate_dab_voltage_loop
    from dab_design.core import DABConfig
    from dab_design.gui.control_view import DABControlView
    from dab_design.optimization import TPSConstraints, optimize_tps

    view = DABControlView(DABConfig)
    loop_result = simulate_dab_voltage_loop(
        DABConfig(), 400, 3500, DABVoltageControlConfig()
    )
    view._display(loop_result)
    assert view.code.toPlainText()
    assert view.export_button.isEnabled()
    impossible = optimize_tps(
        DABConfig(), 400, 3500, TPSConstraints(peak_current_limit_a=0.1), grid_points=3
    )
    view._display(impossible)
    assert not view.code.toPlainText()
    assert not view.tabs.isTabEnabled(view.tabs.indexOf(view.canvas))
    assert not view.tabs.isTabEnabled(view.tabs.indexOf(view.code))
    assert view.optimization_table.item(2, 5).text() == "No"
    assert "No feasible candidate" in view.status.text()
    view._display(loop_result)
    assert view.tabs.isTabEnabled(view.tabs.indexOf(view.code))
    assert view.tabs.currentWidget() is view.canvas
    view.close()


def test_magnetic_failure_and_edited_prediction_invalidate_export(
    app, monkeypatch, tmp_path
):
    from llc_design.gui.widgets.magnetic_validation_view import MagneticValidationView

    view = MagneticValidationView()
    view._display({"pv_w_m3": 100, "frequency_hz": 100000})
    view.frequency.setValue(110000)
    assert view.snapshot is None and not view.export_button.isEnabled()
    view._display({"samples": 1, "mape_percent": 2})
    bad = tmp_path / "bad.csv"
    bad.write_text("wrong,columns\n1,2\n")
    monkeypatch.setattr(qt.QFileDialog, "getOpenFileName", lambda *a: (str(bad), "CSV"))
    view.measurements()
    assert view.snapshot is None and not view.export_button.isEnabled()
    assert "Required columns" in view.output.toPlainText()
    view.close()


def test_loss_edit_or_failed_calculation_cannot_export_stale_report(app, monkeypatch):
    from pfc_design.gui.three_phase_performance_view import ThreePhasePerformanceView
    from pfc_design.three_phase.model import ThreePhaseConfig, simulate_three_phase

    view = ThreePhasePerformanceView()
    view.set_two_level(simulate_three_phase(ThreePhaseConfig()))
    assert view.report is not None and view.export_button.isEnabled()
    assert not view.vienna_kind.isVisibleTo(view)
    view.inputs["rds_on_ohm"][0].setValue(50)
    assert not view.export_button.isEnabled()
    monkeypatch.setattr(
        qt.QFileDialog,
        "getSaveFileName",
        lambda *a: pytest.fail("Stale export must not open a dialog"),
    )
    view.export()
    view.calculate()
    assert view.report["assumptions"]["rds_on_ohm"] == 0.05
    assert view.export_button.isEnabled()
    view.inputs["turns"][0].setValue(1)  # Ae and Ve still missing
    view.calculate()
    assert view.report is None and not view.export_button.isEnabled()
    assert not view.figure.axes and not view.device_figure.axes
    assert not view.summary.toPlainText()
    view.close()

"""Exercise result consumers as well as the LLC numerical solvers."""

import os
import time

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
qt = pytest.importorskip("PySide6.QtWidgets")


@pytest.fixture(scope="module")
def app():
    return qt.QApplication.instance() or qt.QApplication([])


@pytest.mark.parametrize("phases", [2, 3])
def test_interleaved_real_waveform_contract_renders(app, phases):
    from llc_design.analysis import FidelityLevel
    from llc_design.core.spec import LLCDesignSpec
    from llc_design.gui.widgets.interleaved_view import InterleavedLLCView
    from llc_design.multiphase import solve_interleaved_llc

    spec = LLCDesignSpec()
    result = solve_interleaved_llc(
        spec, phases, fidelity=FidelityLevel.FHA, samples_per_cycle=258
    )
    view = InterleavedLLCView()
    try:
        view.set_nominal_spec(spec)
        view.set_result(result)
        lines = view.figure.axes[1].lines
        assert len(lines) == phases + 1
        prefix = "i_secondary" if phases == 3 else "i_rectified"
        for phase in range(phases):
            np.testing.assert_array_equal(
                lines[phase].get_ydata(),
                result.waveform.signal(f"{prefix}_p{phase + 1}").values,
            )
        np.testing.assert_array_equal(
            lines[-1].get_ydata(), result.waveform.signal("i_rectified_total").values
        )
        assert f"{phases}-phase" in view.text.toPlainText()
        if phases == 3:
            assert "secondary" in lines[0].get_label()
    finally:
        view.close()


def test_default_llc_worker_calculates_and_renders_with_launcher_bridges(
    app, monkeypatch
):
    from llc_design.gui.closed_loop_install import install_closed_loop_verification
    from llc_design.gui.device_library_install import install_device_library
    from llc_design.gui.main_window import LLCMainWindow
    from llc_design.gui.solution_map_install import install_llc_solution_map

    monkeypatch.setattr(LLCMainWindow, "_auto_check_update", lambda self: None)
    window = LLCMainWindow()
    errors = []
    monkeypatch.setattr(window, "_worker_error", errors.append)
    install_device_library(window)
    install_closed_loop_verification(window)
    install_llc_solution_map(window)
    try:
        window.run_design()
        deadline = time.monotonic() + 15
        while window._active_workers and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.005)
        assert not window._active_workers
        assert not errors
        assert window.system_analysis is not None
        assert "Lr=" in window.summary_text.toPlainText()
        assert window.gain_figure.axes[0].lines

        # Use the real parameter-collection and rendering callbacks too.
        def synchronous(label, calculate, callback):
            callback(calculate())

        monkeypatch.setattr(window, "_run_worker", synchronous)
        window.small_signal_view.run_button.click()
        assert window.small_signal_view.result is not None
        window.digital_loop_view.request_analysis()
        assert window.digital_loop_view.result is not None
    finally:
        window.thread_pool.waitForDone(15000)
        app.processEvents()
        window.close()


def test_run_current_uses_visible_llc_page_settings_and_blocks_reentry(
    app, monkeypatch
):
    from llc_design.gui.main_window import LLCMainWindow

    monkeypatch.setattr(LLCMainWindow, "_auto_check_update", lambda self: None)
    window = LLCMainWindow()
    requests = []
    monkeypatch.setattr(window, "_run_worker", lambda *args: requests.append(args))
    try:
        view = window.interleaved_view
        captured = []
        view.analysis_requested.connect(
            lambda phases, options: captured.append((phases, options))
        )
        view.phase.setCurrentIndex(1)
        view.vbus.setValue(390)
        view.load.setValue(75)
        window.tabs.setCurrentWidget(view)
        window._run_current_page()
        assert captured == [(3, {"vbus_v": 390, "load_fraction": 0.75})]
        assert len(requests) == 1
        window._active_workers.append(object())
        window._run_current_page()
        assert len(captured) == 1
        window._active_workers.clear()

        small = window.small_signal_view
        captured_small = []
        small.analysis_requested.connect(captured_small.append)
        small.vbus.setValue(380)
        small.load_percent.setValue(65)
        small.sample_us.setValue(25)
        window.tabs.setCurrentWidget(small)
        window._run_current_page()
        assert captured_small[-1]["vbus_v"] == 380
        assert captured_small[-1]["load_fraction"] == 0.65
        assert captured_small[-1]["sample_time_s"] == pytest.approx(25e-6)
    finally:
        window.close()

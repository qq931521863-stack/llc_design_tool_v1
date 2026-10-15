"""Exercise actual asynchronous entry points and frozen-result exports."""

import json
import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import pytest


def finish(app, window):
    deadline = time.monotonic() + 30
    while window._worker is not None and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)
    assert window._worker is None, "GUI worker did not finish"
    app.processEvents()


def test_dab_gui_runs_selects_and_exports_snapshot(monkeypatch, tmp_path):
    qt = pytest.importorskip("PySide6.QtWidgets")
    from dab_design.core import load_config
    from dab_design.gui.main_window import DABMainWindow

    app = qt.QApplication.instance() or qt.QApplication([])
    window = DABMainWindow()
    errors = []
    monkeypatch.setattr(window, "_error", errors.append)
    window.run_analysis()
    finish(app, window)
    assert not errors
    assert len(window.result.points) == window.table.rowCount() == 25
    window.point_choice.setCurrentIndex(24)
    point = window.result.points[24]
    plotted = window.figures["电气波形"].axes[1].lines[0].get_ydata()
    assert plotted == pytest.approx(point.with_cp.signals["il_a"])
    path = tmp_path / "dab.csv"
    monkeypatch.setattr(
        qt.QFileDialog, "getSaveFileName", lambda *a: (str(path), "CSV")
    )
    window.inputs["va_v"].setValue(450)
    window.export_results()
    assert load_config(path.with_suffix(".config.json")).va_v == 400
    assert (
        "上次" in window.statusBar().currentMessage()
        or "导出" in window.statusBar().currentMessage()
    )
    window.close()


def test_launcher_exposes_dab_and_pfc_dq(monkeypatch):
    qt = pytest.importorskip("PySide6.QtWidgets")
    from llc_design.core.spec import LLCDesignSpec
    from llc_design.gui.launcher import (
        WorkspaceApplicationController,
        WorkspaceSelectionDialog,
    )
    from llc_design.gui.main_window import LLCMainWindow
    from pfc_design.gui.main_window import PFCMainWindow

    app = qt.QApplication.instance() or qt.QApplication([])
    monkeypatch.setattr(PFCMainWindow, "_auto_check_update", lambda self: None)
    monkeypatch.setattr(LLCMainWindow, "_auto_check_update", lambda self: None)
    selector = WorkspaceSelectionDialog()
    button = next(
        b for b in selector.findChildren(qt.QPushButton) if b.text() == "进入 DAB TPS"
    )
    button.click()
    assert selector.selected_workspace == "dab"
    controller = WorkspaceApplicationController(LLCDesignSpec())
    controller.show_workspace("dab")
    assert controller.dab_window.isVisible()
    controller.show_workspace("pfc")
    assert not controller.dab_window.isVisible()
    view = controller.pfc_window.three_phase_view
    controller.pfc_window.subtabs.setCurrentWidget(view)
    calls = []
    monkeypatch.setattr(view, "run_analysis", lambda: calls.append("dq"))
    controller.pfc_window._run_current()
    assert calls == ["dq"]
    for name in ("llc", "pfc", "control", "fra", "dab"):
        getattr(controller, name + "_window").close()
    app.processEvents()


def test_three_phase_gui_runs_and_exports(monkeypatch, tmp_path):
    qt = pytest.importorskip("PySide6.QtWidgets")
    from pfc_design.gui.three_phase_view import ThreePhaseDQView

    app = qt.QApplication.instance() or qt.QApplication([])
    view = ThreePhaseDQView()
    errors = []
    monkeypatch.setattr(view, "_error", errors.append)
    view.run_analysis()
    finish(app, view)
    assert not errors
    assert view.result.metrics["settled"]
    assert "SRF PLL" in view.summary.toPlainText()
    path = tmp_path / "dq.csv"
    monkeypatch.setattr(
        qt.QFileDialog, "getSaveFileName", lambda *a: (str(path), "CSV")
    )
    view.inputs["output_power_w"].setValue(5)
    view.export_results()
    assert (
        json.loads(path.with_suffix(".json").read_text())["config"]["output_power_w"]
        == 10000
    )
    assert len(view.figures["SVPWM 六路"].axes[1].lines) == 6
    view.close()


def test_dab_selection_autoruns_and_animation_pause_reset(monkeypatch):
    qt = pytest.importorskip("PySide6.QtWidgets")
    from dab_design.gui.main_window import DABMainWindow
    import numpy as np

    app = qt.QApplication.instance() or qt.QApplication([])
    window = DABMainWindow()
    errors = []
    monkeypatch.setattr(window, "_error", errors.append)
    window.samples.setValue(1024)
    window.point_choice.setCurrentIndex(3)
    finish(app, window)
    assert not errors and window.result is not None
    assert window.point_choice.currentIndex() == 3
    assert window._worker is None
    previous = window.result
    window.inputs["va_v"].setValue(410)
    window.point_choice.setCurrentIndex(4)
    finish(app, window)
    assert window.result is not previous and window.result.config.va_v == 410
    assert window.point_choice.currentIndex() == 4
    window.show()
    window.tabs.setCurrentIndex(2)  # electrical waveform tab
    window.animation_timer.stop()  # deterministic frame stepping below
    line, original = window._animated_lines[0]
    window._animate_waveform()
    assert not np.array_equal(line.get_ydata(), original)
    window.animate.setChecked(False)
    paused = line.get_ydata().copy()
    window._animate_waveform()
    assert np.array_equal(line.get_ydata(), paused)
    window._reset_animation()
    assert np.array_equal(line.get_ydata(), original)
    assert window.animation_timer.interval() == 100
    assert window._worker is None  # playback does not recompute the circuit
    window.close()

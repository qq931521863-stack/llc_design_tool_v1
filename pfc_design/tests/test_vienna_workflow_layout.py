"""Exercise the shown parent window, including Qt callback and native failures."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.parametrize("size", [(1280, 720), (1366, 768)])
def test_shown_vienna_workflow_at_720p(tmp_path, size):
    pytest.importorskip("PySide6")
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", OPENBLAS_NUM_THREADS="1")
    root = Path(__file__).resolve().parents[2]
    env["PYTHONPATH"] = str(root)
    completed = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), str(tmp_path), *map(str, size)],
        cwd=root, env=env, text=True, capture_output=True, timeout=180,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "VIENNA_720P_OK" in completed.stdout
    assert "Traceback" not in completed.stderr
    assert len(list(tmp_path.glob("generated_*.png"))) == 3


def _exercise_shown_parent(output: Path, size=(1280, 720)) -> None:
    from dataclasses import replace
    from time import monotonic, sleep
    import traceback

    from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
    from PySide6.QtCore import QPoint, QRect
    from PySide6.QtWidgets import QApplication, QScrollArea

    import pfc_design.gui.main_window as main_window
    from pfc_design.vienna import illustrative_vienna_config

    # Qt reports slot exceptions through sys.excepthook, without propagating
    # them through button.click() or changing the subprocess exit status.
    errors = []
    def record_exception(*args):
        errors.append("".join(traceback.format_exception(*args)))
        sys.__excepthook__(*args)
    sys.excepthook = record_exception
    main_window.check_for_updates = lambda *args, **kwargs: None
    app = QApplication.instance() or QApplication([])
    window = main_window.PFCMainWindow()
    window._worker_error = lambda error: errors.append(str(error))
    window.resize(*size)
    window.subtabs.setCurrentIndex(1)
    view = window.vienna_view
    maps = view.solution_map_view
    view.set_config(replace(
        illustrative_vienna_config(), frequency_points=400, waveform_line_cycles=3,
        waveform_integration_rate_hz=200_000.0, switching_samples_per_cycle=100,
    ))
    window.show()
    app.processEvents()

    def settle_layout():
        # Qt posts nested LayoutRequest/paint events; one processEvents call
        # need not finish a real tab transition. Allow a bounded 250 ms, then
        # assert actual viewport bounds rather than accepting a clipped plot.
        deadline = monotonic() + .25
        while monotonic() < deadline:
            app.processEvents()
            sleep(.005)
        app.processEvents()

    def drain():
        deadline = monotonic() + 60
        while window._active_workers and monotonic() < deadline:
            app.processEvents()
            sleep(.005)
        app.processEvents()
        assert not window._active_workers, "Analysis worker did not finish"
        assert not errors, "\n".join(errors)

    def focus_workflow():
        view.tabs.setCurrentWidget(maps.parentWidget())
        app.processEvents()
        assert not view.diagram.isVisible()
        assert not view.cursor.isVisible() and not view.budget.isVisible()
        assert view.splitter.sizes()[0] == 0

    def assert_canvas_visible(canvas):
        # Checking only the outer window misses clipping by an intermediate
        # tab page. Preserve the entire canvas through every ancestor viewport.
        bounds = QRect(canvas.mapTo(window, QPoint(0, 0)), canvas.size())
        visible = bounds.intersected(window.rect())
        parent = canvas.parentWidget()
        while parent is not None and parent is not window:
            visible = visible.intersected(
                QRect(parent.mapTo(window, QPoint(0, 0)), parent.size())
            )
            parent = parent.parentWidget()
        assert visible == bounds, (bounds, visible)
        canvas.draw()
        renderer = canvas.get_renderer()
        width, height = canvas.get_width_height()
        for axis in canvas.figure.axes:
            box = axis.get_tightbbox(renderer)
            assert box.x0 >= -1 and box.y0 >= -1, box.bounds
            assert box.x1 <= width + 1 and box.y1 <= height + 1, box.bounds

    def generate(key, fc, pm):
        focus_workflow()
        maps.source_combo.setCurrentIndex(maps.source_combo.findData(key))
        maps.allow_conditional.setChecked(True)
        maps.live_fc.setValue(fc)
        maps.live_pm.setValue(pm)
        maps.generate_button.click()
        settle_layout()
        assert not errors, "\n".join(errors)
        assert maps.apply_button.isEnabled(), maps.details.toPlainText()
        assert maps.result_tabs.currentIndex() == 1
        canvas = maps.preview_canvas
        assert canvas.isVisible() and canvas.width() >= 480 and canvas.height() >= 300
        screenshot = window.grab()
        assert (screenshot.width(), screenshot.height()) == size
        assert screenshot.save(str(output / f"generated_{key}.png"))
        bottom = canvas.mapTo(window, QPoint(canvas.width() - 1, canvas.height() - 1))
        assert bottom.y() < window.statusBar().y(), (
            f"Candidate plot bottom {bottom.y()} is below viewport {window.statusBar().y()}"
        )
        assert bottom.x() < window.width(), "Candidate plot is clipped to the right"
        assert_canvas_visible(canvas)
        assert len(maps.preview_figure.axes) == 4
        assert all(axis.lines for axis in maps.preview_figure.axes)
        image = canvas.grab().toImage()
        colors = {
            image.pixel(x, y)
            for x in range(0, image.width(), 7)
            for y in range(0, image.height(), 7)
        }
        assert len(colors) > 12, "Candidate canvas is blank"
        # Targets must be reachable without scrolling past optional map inputs.
        assert maps.live_fc.mapTo(window, QPoint(0, 0)).y() < 440
        return maps.selected_point

    assert view.diagram_toggle.isChecked() and view.inspector_toggle.isChecked()
    focus_workflow()
    view.tabs.setCurrentWidget(view.bi)
    app.processEvents()
    assert view.diagram.isVisible() and view.inspector_toggle.isChecked()
    view.run_button.click()
    drain()
    assert view.has_current_result()
    for bode in (view.bi, view.bv, view.bb, view.bs):
        view.tabs.setCurrentWidget(bode)
        settle_layout()
        assert view.diagram.isVisible()
        assert bode.canvas.height() >= 220
        assert_canvas_visible(bode.canvas)

    for key, fc, pm in (("current", 1500, 60), ("voltage", 20, 65), ("balance", 50, 60)):
        point = generate(key, fc, pm)
        maps.apply_button.click()
        assert window._active_workers
        drain()
        assert view.has_current_result()
        controller = getattr(view._config(), key + "_controller")
        assert (controller.kp, controller.ti_s) == (point.kp, point.ti_s)
        assert window.result is view.result

    # Tour every actual result page, including the sidebar-only PLL layout,
    # then revisit Generate: this sequence previously left a zero-height canvas.
    for index in range(view.tabs.count()):
        view.tabs.setCurrentIndex(index)
        app.processEvents()
        for scroll in window.findChildren(QScrollArea):
            if scroll.isVisible():
                bar = scroll.verticalScrollBar()
                for value in (bar.maximum(), 0):
                    bar.setValue(value)
                    app.processEvents()
        assert not window.grab().isNull()
        assert not errors, "\n".join(errors)
    for canvas in (view.cac, view.cbus, view.csw, view.csec, view.charm):
        view.tabs.setCurrentWidget(canvas.parentWidget())
        settle_layout()
        assert not view.diagram.isVisible()
        assert not view.bode_status.isVisible()
        assert canvas.height() >= 400
        assert_canvas_visible(canvas)
    view.tabs.setCurrentWidget(view.performance)
    settle_layout()
    assert view.performance.figure.axes
    assert_canvas_visible(view.performance.canvas)
    view.performance.tabs.setCurrentIndex(1)
    settle_layout()
    assert view.performance.device_figure.axes
    assert_canvas_visible(view.performance.device_canvas)
    for scroll in view.pll_comparison.findChildren(QScrollArea):
        assert not scroll.widget().findChildren(FigureCanvasQTAgg)
    generate("current", 1500, 60)
    view.tabs.setCurrentWidget(view.bi)
    app.processEvents()
    assert view.diagram.isVisible()
    # A user's already-collapsed diagram stays collapsed after visiting workflow.
    view.diagram_toggle.setChecked(False)
    focus_workflow()
    view.tabs.setCurrentWidget(view.bi)
    app.processEvents()
    assert not view.diagram.isVisible()
    assert window.close()
    app.processEvents()
    assert not errors, "\n".join(errors)
    from llc_design.gui.lifetime import dispose_widgets
    dispose_widgets([window])
    print("VIENNA_720P_OK", flush=True)


if __name__ == "__main__":
    _exercise_shown_parent(Path(sys.argv[1]), tuple(map(int, sys.argv[2:4])) if len(sys.argv) > 2 else (1280, 720))

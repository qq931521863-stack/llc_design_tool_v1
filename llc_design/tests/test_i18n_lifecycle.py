"""Verify owned i18n cleanup survives failed assertions and native Qt exit."""
from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest


def test_i18n_cleanup_destroys_all_owners_after_success_and_failure(tmp_path):
    pytest.importorskip("PySide6.QtWidgets")
    pytest.importorskip("pyqtgraph")
    script = textwrap.dedent(
        """
        import pytest
        from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
        from pyqtgraph import GraphicsLayoutWidget, PlotWidget
        from PySide6 import QtWidgets
        from PySide6.QtCore import QCoreApplication, QEvent, QThreadPool, QTimer
        from shiboken6 import isValid

        from llc_design.gui import main_window as llc_module
        from pfc_design.gui import main_window as pfc_module
        from llc_design.i18n import catalogue
        from llc_design.tests import test_i18n as tests

        app = QtWidgets.QApplication([])
        app.setQuitOnLastWindowClosed(False)
        # Disable only the updater's network boundary. Native timers, workers,
        # closeEvent handlers, plot owners and normal shutdown remain real.
        llc_module.check_for_updates = lambda *args, **kwargs: None
        pfc_module.check_for_updates = lambda *args, **kwargs: None
        unrelated = QtWidgets.QMainWindow()
        unrelated_plot = PlotWidget()
        unrelated.setCentralWidget(unrelated_plot)
        unrelated_plot.plot([0, 1], [2, 3])
        unrelated_scene = unrelated_plot.scene()
        retained = []
        destroyed = []

        for cycle in range(3):
            expected_failure = cycle == 1
            failure_seen = False
            try:
                with tests._controller(QtWidgets) as owned:
                    _, controller = owned
                    windows = [getattr(controller, name) for name in tests._WORKSPACE_WINDOWS]
                    assert len(windows) == 6
                    for name, window in zip(tests._WORKSPACE_WINDOWS, windows):
                        window.destroyed.connect(lambda *args, n=name, c=cycle: destroyed.append((c, n)))
                    plots = [plot for window in windows for plot in window.findChildren(PlotWidget)]
                    layouts = [view for window in windows for view in window.findChildren(GraphicsLayoutWidget)]
                    canvases = [canvas for window in windows for canvas in window.findChildren(FigureCanvasQTAgg)]
                    scenes = [plot.scene() for plot in plots + layouts]
                    items = [plot.getPlotItem() for plot in plots]
                    views = [item.getViewBox() for item in items]
                    timers = [timer for window in windows for timer in window.findChildren(QTimer)]
                    pools = [pool for window in windows for pool in window.findChildren(QThreadPool)]
                    figures = [canvas.figure for canvas in canvases]
                    assert plots and layouts and canvases and timers and pools
                    wrappers = windows + plots + layouts + canvases + scenes + items + views + timers + pools
                    retained.append(wrappers)

                    # Exercise actual cancellable stage work as well as plots.
                    stage = controller.power_stage_window
                    stage.topology_combo.setCurrentIndex(stage.topology_combo.findData("T18"))
                    stage.calculate()
                    cancellations = tuple(stage._cancel_events)
                    assert cancellations and stage._jobs

                    if expected_failure:
                        entries = catalogue("en")
                        original = entries.pop("加载 JSON")
                        try:
                            tests.test_every_bound_shell_string_is_translated(owned)
                        finally:
                            entries["加载 JSON"] = original
                    else:
                        tests.test_every_bound_shell_string_is_translated(owned)
                    if cycle == 2:
                        # Negative control: ordinary close preserves all six
                        # native owners. Only the finally-backed disposal below
                        # can satisfy the destruction assertions.
                        for window in windows:
                            window.close()
                        assert all(isValid(window) for window in windows)
                        assert all(isValid(plot) for plot in plots)
                        print("CLOSE_ALONE_RETAINS_ALL_SIX_OWNERS", flush=True)
            except AssertionError as exc:
                if not expected_failure or "en is missing 1 shell strings: ['加载 JSON']" not in str(exc):
                    raise
                failure_seen = True

            assert failure_seen == expected_failure
            assert all(event.is_set() for event in cancellations)
            assert not stage._jobs and not stage._cancel_events
            assert QThreadPool.globalInstance().activeThreadCount() == 0
            assert len([entry for entry in destroyed if entry[0] == cycle]) == 6
            assert all(not isValid(wrapper) for wrapper in wrappers)
            assert all(not figure.axes for figure in figures)
            assert isValid(unrelated) and isValid(unrelated_plot) and isValid(unrelated_scene)
            assert len(unrelated_plot.listDataItems()) == 1
            print("OWNED_I18N_CYCLE", cycle, len(wrappers), "DESTROYED", flush=True)

        assert all(not isValid(wrapper) for wrappers in retained for wrapper in wrappers)
        unrelated.close()
        unrelated.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        app.processEvents()
        assert not isValid(unrelated_scene)
        print("I18N_ASSERTION_FAILURE_AND_NATIVE_CLEANUP_COMPLETE", flush=True)
        """
    )
    environment = dict(os.environ, QT_QPA_PLATFORM="offscreen", MPLBACKEND="Agg", PYTHONFAULTHANDLER="1")
    environment.pop("PYTEST_ADDOPTS", None)
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).resolve().parents[2],
        env=environment,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    output = completed.stdout + completed.stderr
    (tmp_path / "i18n-native-cleanup.log").write_text(output, encoding="utf-8")
    assert completed.returncode == 0, output
    assert "Error calling Python override" not in output, output
    assert output.count("OWNED_I18N_CYCLE") == 3, output
    assert "CLOSE_ALONE_RETAINS_ALL_SIX_OWNERS" in output, output
    assert "I18N_ASSERTION_FAILURE_AND_NATIVE_CLEANUP_COMPLETE" in output, output

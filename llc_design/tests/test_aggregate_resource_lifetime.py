"""Native ownership evidence for ordered tests and real application shutdown."""
from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_PREAMBLE = '''
import gc
import weakref
import faulthandler
faulthandler.dump_traceback_later(60, repeat=True)
import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QTimer
from PySide6.QtWidgets import QApplication, QMainWindow
from pyqtgraph import GraphicsLayoutWidget, PlotWidget
from shiboken6 import isValid
from llc_design.core.spec import LLCDesignSpec
from llc_design.gui import launcher, main_window as llc_module
from pfc_design.gui import main_window as pfc_module
from llc_design.gui.lifetime import dispose_widgets
llc_module.check_for_updates = pfc_module.check_for_updates = lambda *a, **k: None
app = QApplication([])
app.setQuitOnLastWindowClosed(False)
'''


def _run(script, tmp_path):
    pytest.importorskip("PySide6.QtWidgets")
    pytest.importorskip("pyqtgraph")
    environment = dict(os.environ, QT_QPA_PLATFORM="offscreen", MPLBACKEND="Agg",
                       PYTHONFAULTHANDLER="1")
    environment.pop("PYTEST_ADDOPTS", None)
    log = tmp_path / "aggregate-native-lifetime.log"
    with log.open("w", encoding="utf-8") as output_file:
        completed = subprocess.run([sys.executable, "-c", _PREAMBLE + textwrap.dedent(script)],
                                   cwd=_ROOT, env=environment, stdout=output_file,
                                   stderr=subprocess.STDOUT, text=True,
                                   timeout=180, check=False)
    output = log.read_text(encoding="utf-8")
    assert completed.returncode == 0, output
    assert "Error calling Python override" not in output, output
    assert "Traceback" not in output, output
    assert "NATIVE_LIFETIME_COMPLETE" in output, output


def test_switch_reuse_dialog_discard_and_real_event_loop_shutdown(tmp_path):
    _run('''
        unrelated = QMainWindow()
        unrelated_plot = PlotWidget()
        unrelated.setCentralWidget(unrelated_plot)
        unrelated_plot.plot([0, 1], [2, 3])
        unrelated_scene = unrelated_plot.scene()
        controller = launcher.WorkspaceApplicationController(LLCDesignSpec())
        names = ("llc", "pfc", "control", "fra", "dab", "power_stage")
        windows = [getattr(controller, name + "_window") for name in names]
        identities = [id(window) for window in windows]
        plots = [plot for window in windows for plot in window.findChildren(PlotWidget)]
        layouts = [view for window in windows for view in window.findChildren(GraphicsLayoutWidget)]
        scenes = [view.scene() for view in plots + layouts]
        scene_items = [item for scene in scenes for item in scene.items()]
        viewports = [view.viewport() for view in plots + layouts]
        from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
        canvases = [canvas for window in windows
                    for canvas in window.findChildren(FigureCanvasQTAgg)]
        assert scene_items and viewports and canvases
        dialog_wrappers = []
        dialog_refs = []
        def dismiss_dialog():
            dialog = app.activeModalWidget()
            assert isinstance(dialog, (launcher.WorkspaceSelectionDialog,
                                       launcher.SystemModelingDesignDialog))
            dialog_wrappers.append(dialog)
            dialog_refs.append(weakref.ref(dialog))
            dialog.reject()
        for cycle in range(3):
            for name in names:
                controller.show_workspace(name)
                app.processEvents()
                window = getattr(controller, name + "_window")
                assert window.isVisible() and window.close()
                controller.show_workspace(name)
                app.processEvents()
                assert window.isVisible()
            assert [id(getattr(controller, name + "_window")) for name in names] == identities
            assert all(isValid(window) for window in windows)
            assert len([window for window in app.topLevelWidgets()
                        if isinstance(window, QMainWindow) and window is not unrelated]) == 6
            QTimer.singleShot(0, dismiss_dialog)
            controller._show_selector_again()
            QTimer.singleShot(0, dismiss_dialog)
            assert controller._run_system_modeling(previous="power_stage")
            assert all(not isValid(dialog) for dialog in dialog_wrappers)
            # These wrappers are intentionally held: Qt ownership must end even
            # if Python still references a discarded dialog or workspace.
            assert len(dialog_wrappers) == 2 * (cycle + 1)
            print("BOUNDED_SIX_WORKSPACES_DISCARDED_DIALOGS", cycle, flush=True)
        dialog_wrappers.clear()
        gc.collect()
        assert all(ref() is None for ref in dialog_refs)
        # Quit the actual event loop, not a test fixture's cleanup substitute.
        QTimer.singleShot(0, app.quit)
        assert app.exec() == 0
        assert all(not isValid(window) for window in windows)
        assert all(not isValid(view) for view in plots + layouts + scenes
                   + scene_items + viewports + canvases)
        controller.dispose()  # final disposal is idempotent
        assert isValid(unrelated) and isValid(unrelated_scene)
        assert len(unrelated_plot.listDataItems()) == 1
        dispose_widgets([unrelated])
        print("NATIVE_LIFETIME_COMPLETE", flush=True)
    ''', tmp_path)


def test_run_gui_finally_disposes_cancel_success_and_start_failure(tmp_path):
    _run('''
        from llc_design.gui import app as entry
        retained = []
        for mode in ("cancel", "accept", "failure"):
            print("RUN_GUI_START", mode, flush=True)
            previous_start = launcher.WorkspaceApplicationController.start
            def quit_started_application():
                if getattr(app, "_power_design_controller", None) is None:
                    QTimer.singleShot(10, quit_started_application)
                    return
                print("APPLICATION_QUIT_REQUESTED", flush=True)
                app.quit()
            def drive_selector():
                dialog = app.activeModalWidget()
                if dialog is None:
                    # Construction/rendering may process posted events before
                    # the selector's nested event loop has actually started.
                    QTimer.singleShot(10, drive_selector)
                    return
                assert isinstance(dialog, launcher.WorkspaceSelectionDialog)
                print("SELECTOR_DRIVEN", mode, flush=True)
                retained.extend(window for window in app.topLevelWidgets()
                                if isinstance(window, QMainWindow))
                retained.append(dialog)
                if mode == "cancel":
                    dialog.reject()
                else:
                    dialog._select("power_stage")
                    QTimer.singleShot(10, quit_started_application)
            def fail_start(controller):
                retained.extend(controller._windows())
                raise ValueError("intentional startup failure")
            try:
                if mode == "failure":
                    launcher.WorkspaceApplicationController.start = fail_start
                    try:
                        entry.run_gui()
                    except ValueError as error:
                        assert str(error) == "intentional startup failure"
                    else:
                        raise AssertionError("startup failure was swallowed")
                else:
                    QTimer.singleShot(0, drive_selector)
                    assert entry.run_gui() == 0
            finally:
                launcher.WorkspaceApplicationController.start = previous_start
            assert retained and all(not isValid(owner) for owner in retained)
            assert not hasattr(app, "_power_design_controller")
            assert not [window for window in app.topLevelWidgets()
                        if isinstance(window, QMainWindow)]
            print("RUN_GUI_DISPOSED", mode, flush=True)
        print("NATIVE_LIFETIME_COMPLETE", flush=True)
    ''', tmp_path)


def test_partial_controller_construction_disposes_only_new_owners(tmp_path):
    _run('''
        from power_stage_design.gui import PowerStageMainWindow
        existing = PowerStageMainWindow()
        captured = []
        original = launcher.install_guided_context_actions
        def fail_after_native_construction(window, callback):
            captured.extend(owner for owner in app.topLevelWidgets()
                            if isinstance(owner, QMainWindow) and owner is not existing)
            raise ValueError("intentional installer failure")
        launcher.install_guided_context_actions = fail_after_native_construction
        try:
            with pytest.raises(ValueError, match="intentional installer failure"):
                launcher.WorkspaceApplicationController(LLCDesignSpec(), power_stage_window=existing)
        finally:
            launcher.install_guided_context_actions = original
        assert len(captured) == 5 and all(not isValid(owner) for owner in captured)
        assert isValid(existing)
        assert not hasattr(existing, "open_existing_llc")
        assert existing.toolbox.calculate() is not None
        dispose_widgets([existing])
        print("NATIVE_LIFETIME_COMPLETE", flush=True)
    ''', tmp_path)


def test_root_fixture_covers_standalone_widgets_and_failure_without_allowlist(tmp_path):
    _run('''
        from contextlib import contextmanager
        import importlib.util
        from pathlib import Path
        module_spec = importlib.util.spec_from_file_location("root_fixtures", Path("conftest.py"))
        fixtures = importlib.util.module_from_spec(module_spec)
        module_spec.loader.exec_module(fixtures)
        isolate = contextmanager(fixtures.dispose_test_owned_desktop_widgets.__wrapped__)
        from llc_design.gui.widgets.solution_map_view import SolutionMapView
        from pfc_design.gui.ttpl_engineering_view import TTPLWorkbenchView
        unrelated = QMainWindow()
        plot = PlotWidget()
        unrelated.setCentralWidget(plot)
        plot.plot([0, 1], [3, 4])
        retained = []
        for cycle in range(3):
            with pytest.raises(AssertionError, match="intentional test failure"):
                with isolate():
                    owners = [llc_module.LLCMainWindow(), pfc_module.PFCMainWindow(),
                              SolutionMapView(), TTPLWorkbenchView()]
                    retained.extend(owners)
                    for owner in owners:
                        owner.close()
                    assert all(isValid(owner) for owner in owners)  # negative control
                    raise AssertionError("intentional test failure")
            assert all(not isValid(owner) for owner in retained)
            assert isValid(unrelated) and len(plot.listDataItems()) == 1
        dispose_widgets([unrelated])
        print("NATIVE_LIFETIME_COMPLETE", flush=True)
    ''', tmp_path)


def test_owned_disposal_does_not_wait_for_unrelated_global_pool_work(tmp_path):
    _run('''
        from threading import Event
        from PySide6.QtCore import QThreadPool
        from llc_design.gui.workers import FunctionWorker
        started, release, finished = Event(), Event(), Event()
        def unrelated_calculation():
            started.set()
            assert release.wait(30)
            finished.set()
        unrelated = QMainWindow()
        unrelated_plot = PlotWidget()
        unrelated.setCentralWidget(unrelated_plot)
        unrelated_plot.plot([0, 1], [2, 3])
        worker = FunctionWorker(unrelated_calculation)
        QThreadPool.globalInstance().start(worker)
        assert started.wait(5)
        owner = llc_module.LLCMainWindow()
        graphics = owner.waveform_view.graphics
        scene = graphics.scene()
        try:
            dispose_widgets([owner])
            assert not finished.is_set()
            assert QThreadPool.globalInstance().activeThreadCount() > 0
            assert not isValid(owner) and not isValid(graphics) and not isValid(scene)
            assert isValid(unrelated) and len(unrelated_plot.listDataItems()) == 1
        finally:
            release.set()
            assert QThreadPool.globalInstance().waitForDone(5000)
            dispose_widgets([unrelated])
        assert finished.is_set()
        print("NATIVE_LIFETIME_COMPLETE", flush=True)
    ''', tmp_path)

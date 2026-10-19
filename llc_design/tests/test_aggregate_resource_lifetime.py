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
from PySide6.QtCore import QCoreApplication, QEvent, QObject, QTimer
from PySide6.QtWidgets import QApplication, QMainWindow
from pyqtgraph import GraphicsLayoutWidget, GraphicsView, PlotItem, PlotWidget, ViewBox
from shiboken6 import isValid
from llc_design.core.spec import LLCDesignSpec
from llc_design.gui import launcher, main_window as llc_module
from pfc_design.gui import main_window as pfc_module
from llc_design.gui.lifetime import dispose_widgets
llc_module.check_for_updates = pfc_module.check_for_updates = lambda *a, **k: None
app = QApplication([])
app.setQuitOnLastWindowClosed(False)

def plot_auxiliaries(windows):
    roots = []
    for window in windows:
        views = [window] if isinstance(window, GraphicsView) else []
        views.extend(window.findChildren(GraphicsView))
        for view in views:
            scene = view.scene()
            if scene.exportDialog is not None:
                roots.append(scene.exportDialog)
                exporter = scene.exportDialog.currentExporter
                if getattr(exporter, "fileDialog", None) is not None:
                    roots.append(exporter.fileDialog)
                parameter = getattr(exporter, "params", None)
                pending = [parameter] if parameter is not None else []
                while pending:
                    parameter = pending.pop()
                    roots.append(parameter)
                    pending.extend(parameter.children())
            for item in scene.items():
                if isinstance(item, PlotItem):
                    roots.extend([item.ctrlMenu, item.stateGroup])
                    if item.fileDialog is not None:
                        roots.append(item.fileDialog)
                if isinstance(item, ViewBox) and item.menu is not None:
                    roots.extend([item.menu, *item.menu.widgetGroups])
    return list(set(roots + [child for root in roots
                            for child in root.findChildren(QObject)]))
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
        unrelated_auxiliaries = plot_auxiliaries([unrelated])
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
        auxiliaries = plot_auxiliaries(windows)
        assert auxiliaries
        assert sum(type(obj).__name__ == "WidgetGroup"
                   for obj in plot_auxiliaries([controller.power_stage_window])) == 21
        auxiliary_ids = {id(obj) for obj in auxiliaries}
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
            assert all(isValid(obj) for obj in auxiliaries)
            assert {id(obj) for obj in plot_auxiliaries(windows)} == auxiliary_ids
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
        assert all(not isValid(obj) for obj in auxiliaries)
        controller.dispose()  # final disposal is idempotent
        assert isValid(unrelated) and isValid(unrelated_scene)
        assert all(isValid(obj) for obj in unrelated_auxiliaries)
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
                windows = [window for window in app.topLevelWidgets()
                           if isinstance(window, QMainWindow)]
                retained.extend(windows)
                retained.extend(plot_auxiliaries(windows))
                retained.append(dialog)
                if mode == "cancel":
                    dialog.reject()
                else:
                    dialog._select("power_stage")
                    QTimer.singleShot(10, quit_started_application)
            def fail_start(controller):
                retained.extend(controller._windows())
                retained.extend(plot_auxiliaries(controller._windows()))
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
        captured_auxiliaries = []
        original = launcher.install_guided_context_actions
        def fail_after_native_construction(window, callback):
            captured.extend(owner for owner in app.topLevelWidgets()
                            if isinstance(owner, QMainWindow) and owner is not existing)
            captured_auxiliaries.extend(plot_auxiliaries(captured))
            raise ValueError("intentional installer failure")
        launcher.install_guided_context_actions = fail_after_native_construction
        try:
            with pytest.raises(ValueError, match="intentional installer failure"):
                launcher.WorkspaceApplicationController(LLCDesignSpec(), power_stage_window=existing)
        finally:
            launcher.install_guided_context_actions = original
        assert len(captured) == 5 and all(not isValid(owner) for owner in captured)
        assert all(not isValid(obj) for obj in captured_auxiliaries)
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
        from power_stage_design.toolbox_gui import ToolBoxWidget
        unrelated = QMainWindow()
        plot = PlotWidget()
        unrelated.setCentralWidget(plot)
        plot.plot([0, 1], [3, 4])
        unrelated_auxiliaries = plot_auxiliaries([unrelated])
        retained = []
        for cycle in range(3):
            with pytest.raises(AssertionError, match="intentional test failure"):
                with isolate():
                    owners = [llc_module.LLCMainWindow(), pfc_module.PFCMainWindow(),
                              SolutionMapView(), TTPLWorkbenchView(), ToolBoxWidget()]
                    retained.extend(owners)
                    retained.extend(plot_auxiliaries(owners))
                    for owner in owners:
                        owner.close()
                    assert all(isValid(owner) for owner in owners)  # negative control
                    raise AssertionError("intentional test failure")
            assert all(not isValid(owner) for owner in retained)
            assert isValid(unrelated) and len(plot.listDataItems()) == 1
            assert all(isValid(obj) for obj in unrelated_auxiliaries)
        dispose_widgets([unrelated])
        print("NATIVE_LIFETIME_COMPLETE", flush=True)
    ''', tmp_path)


def test_plot_auxiliary_disposal_preserves_shared_and_external_menus(tmp_path):
    _run('''
        from PySide6.QtWidgets import QMenu
        from pyqtgraph.GraphicsScene.exportDialog import ExportDialog
        from pyqtgraph.exporters import CSVExporter
        from pyqtgraph.widgets.FileDialog import FileDialog

        unrelated = QMainWindow()
        unrelated_plot = PlotWidget()
        unrelated.setCentralWidget(unrelated_plot)
        unrelated_auxiliaries = plot_auxiliaries([unrelated])
        external_menu = QMenu(unrelated)
        external_menu.addAction("Externally owned")
        owner = QMainWindow()
        layout = GraphicsLayoutWidget()
        owner.setCentralWidget(layout)
        shared_plot = layout.addPlot()
        external_plot = layout.addPlot(row=1, col=0)
        independent_view = layout.addViewBox(row=2, col=0)
        # Preserve these original menus under the owned tree before replacing
        # their Python references with explicitly shared/external menus.
        shared_plot.ctrlMenu.setParent(owner)
        shared_plot.ctrlMenu = unrelated_plot.getPlotItem().ctrlMenu
        external_plot.ctrlMenu.setParent(owner)
        external_plot.ctrlMenu = external_menu
        layout.scene().exportDialog = ExportDialog(layout.scene())
        layout.scene().exportDialog.show(external_plot)
        formats = layout.scene().exportDialog.ui.formatList
        csv_row = next(index for index in range(formats.count())
                       if formats.item(index).expClass is CSVExporter)
        formats.setCurrentRow(csv_row)
        layout.scene().exportDialog.exportClicked()
        exporter = layout.scene().exportDialog.currentExporter
        exporter_dialog = exporter.fileDialog
        parameters = [exporter.params, *exporter.params.children()]
        assert len(parameters) == 4
        assert all(QObject.parent(parameter) is None for parameter in parameters)
        assert exporter_dialog.parent() is None
        exporter_dialog.reject()
        external_plot.fileDialog = FileDialog()
        auxiliaries = plot_auxiliaries([owner])
        owned = [obj for obj in auxiliaries
                 if obj not in unrelated_auxiliaries and obj is not external_menu
                 and obj.parent() is not external_menu]
        assert independent_view.menu in owned
        assert layout.scene().exportDialog in owned
        assert external_plot.fileDialog in owned
        assert exporter_dialog in owned
        assert all(parameter in owned for parameter in parameters)
        dispose_widgets([owner])
        assert not isValid(owner) and not isValid(layout)
        assert all(not isValid(obj) for obj in owned)
        assert isValid(external_menu)
        assert all(isValid(obj) for obj in unrelated_auxiliaries)
        unrelated_plot.plot([0, 1], [4, 5])
        assert len(unrelated_plot.listDataItems()) == 1
        dispose_widgets([unrelated])
        assert not isValid(external_menu)
        assert all(not isValid(obj) for obj in unrelated_auxiliaries)
        print("NATIVE_LIFETIME_COMPLETE", flush=True)
    ''', tmp_path)


@pytest.mark.parametrize("kind", ["plot", "layout", "close-clears-scene", "already-closed-layout"])
def test_direct_graphics_owner_disposes_retained_auxiliaries(kind, tmp_path):
    _run('''
        kind = KIND
        if kind == "plot":
            owner = PlotWidget()
            owner.plot([0, 1], [2, 3])
        else:
            graphics = GraphicsLayoutWidget()
            graphics.addPlot().plot([0, 1], [2, 3])
            if kind in {"layout", "already-closed-layout"}:
                owner = graphics
            else:
                class CloseClearsScene(QMainWindow):
                    def closeEvent(self, event):
                        if not graphics.closed:
                            graphics.close()
                        super().closeEvent(event)
                owner = CloseClearsScene()
                owner.setCentralWidget(graphics)
        auxiliaries = plot_auxiliaries([owner])
        assert auxiliaries
        views = [owner] if isinstance(owner, GraphicsView) else [graphics]
        scenes = [view.scene() for view in views]
        items = [item for scene in scenes for item in scene.items()]
        viewports = [view.viewport() for view in views]
        assert items
        if kind == "already-closed-layout":
            graphics.close()
            assert all(not isValid(item) for item in items)
        dispose_widgets([owner])
        assert not isValid(owner)
        assert all(not isValid(obj) for obj in views + scenes + items + viewports)
        assert all(not isValid(obj) for obj in auxiliaries)
        assert not app.topLevelWidgets()
        dispose_widgets([owner])
        print("NATIVE_LIFETIME_COMPLETE", flush=True)
    '''.replace("KIND", repr(kind)), tmp_path)


def test_refused_close_keeps_scene_and_auxiliaries_alive(tmp_path):
    _run('''
        class RefusesClose(PlotWidget):
            accepting = False
            def closeEvent(self, event):
                if self.accepting:
                    super().closeEvent(event)
                else:
                    event.ignore()
        owner = RefusesClose()
        owner.show()
        owner.plot([0, 1], [2, 3])
        scene = owner.scene()
        items = scene.items()
        auxiliaries = plot_auxiliaries([owner])
        with pytest.raises(RuntimeError, match="still refuses to close"):
            dispose_widgets([owner])
        assert all(isValid(obj) for obj in [owner, scene, *items, *auxiliaries])
        owner.accepting = True
        dispose_widgets([owner])
        assert all(not isValid(obj) for obj in [owner, scene, *items, *auxiliaries])
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

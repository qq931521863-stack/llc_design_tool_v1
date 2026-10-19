"""Keep Qt teardown regressions observable without killing the whole test run.

These children use real widgets and calculations. A successful assertion inside
the child is insufficient: native Qt/PySide cleanup must also exit cleanly.
"""
from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _run_child(arguments, tmp_path):
    pytest.importorskip("PySide6.QtWidgets")
    pytest.importorskip("pyqtgraph")
    environment = dict(os.environ, QT_QPA_PLATFORM="offscreen", PYTHONFAULTHANDLER="1")
    environment.pop("PYTEST_ADDOPTS", None)
    completed = subprocess.run(
        [sys.executable, *arguments],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    output = completed.stdout + completed.stderr
    (tmp_path / "qt-lifecycle.log").write_text(output, encoding="utf-8")
    assert completed.returncode == 0, output
    assert "Error calling Python override" not in output, output
    return output


@pytest.mark.parametrize("standalone_toolbox", [False, True], ids=["main-window", "toolbox"])
def test_plot_reuse_and_deferred_delete_exit_cleanly(standalone_toolbox, tmp_path):
    script = textwrap.dedent(
        f"""
        from PySide6.QtCore import QCoreApplication, QEvent
        from PySide6.QtWidgets import QApplication
        from shiboken6 import isValid
        from power_stage_design.gui import PowerStageMainWindow
        from power_stage_design.toolbox_gui import ToolBoxWidget

        app = QApplication([])
        app.setQuitOnLastWindowClosed(False)
        for cycle in range(3):
            view = ToolBoxWidget() if {standalone_toolbox!r} else PowerStageMainWindow()
            toolbox = view if {standalone_toolbox!r} else view.toolbox
            view.show()
            app.processEvents()
            toolbox.select_tool("X12")
            first = toolbox.calculate()
            assert first is not None
            assert view.close()
            view.show()
            app.processEvents()
            second = toolbox.calculate()
            assert second is not None and second.result_id == first.result_id
            assert len(toolbox.response_plot.listDataItems()) == 3
            assert len(toolbox.phase_plot.listDataItems()) == 3
            assert len(toolbox.impedance_plot.listDataItems()) == 2
            assert view.close()
            view.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            app.processEvents()
            assert not isValid(view)
        print("REUSE_AND_DELETE_COMPLETE", flush=True)
        """
    )
    output = _run_child(["-c", script], tmp_path)
    assert "REUSE_AND_DELETE_COMPLETE" in output


def test_existing_launcher_suites_finish_shared_application_teardown(tmp_path):
    # Both legacy tests instantiate all workspaces and leave cleanup to the
    # session fixture. Deleting pyqtgraph's internal top-level menus/viewport
    # widgets independently of their owning windows previously segfaulted here.
    nodes = [
        "llc_design/tests/test_solution_map_gui.py::test_v93_launcher_installs_solution_map_for_llc_and_ttpl",
        "llc_design/tests/test_help.py::test_every_workspace_installs_a_help_button_and_f1_shortcut",
    ]
    output = _run_child(
        ["-m", "pytest", "-q", "-o", "addopts=", "-p", "no:cacheprovider", *nodes],
        tmp_path,
    )
    assert "2 passed" in output


@pytest.mark.parametrize("module_name", [
    "test_desktop_workflow", "test_input_form_contract",
    "test_parameter_suggestions_desktop", "test_modal_export_ownership",
])
def test_fixture_cleanup_destroys_owned_plot_wrappers(module_name, tmp_path):
    # Retain the wrappers deliberately: deletion must come from the fixture's
    # explicit Qt ownership cleanup, not accidental Python garbage collection.
    script = textwrap.dedent(
        f"""
        import importlib
        import pytest
        from pyqtgraph import PlotWidget
        from PySide6.QtWidgets import QApplication
        from shiboken6 import isValid

        module = importlib.import_module("power_stage_design.tests.{module_name}")
        app = QApplication.instance() or QApplication([])
        app.setQuitOnLastWindowClosed(False)
        monkeypatch = pytest.MonkeyPatch()
        for cycle in range(3):
            if {module_name!r} == "test_modal_export_ownership":
                fixture = module.desktop.__wrapped__(app, monkeypatch)
                window, warnings = next(fixture)
            elif {module_name!r} == "test_input_form_contract":
                fixture = module.window.__wrapped__()
                window = next(fixture)
            else:
                fixture = module.window.__wrapped__(app)
                window = next(fixture)
            plots = window.findChildren(PlotWidget)
            scenes = [plot.scene() for plot in plots]
            items = [plot.getPlotItem() for plot in plots]
            views = [item.getViewBox() for item in items]
            assert len(plots) == 7
            assert window.close()
            window.show()
            app.processEvents()
            window.toolbox.select_tool("X12")
            assert window.toolbox.calculate() is not None
            assert len(window.toolbox.response_plot.listDataItems()) == 3
            try:
                next(fixture)
            except StopIteration:
                pass
            else:
                raise AssertionError("Fixture did not finish")
            assert not isValid(window)
            for name, wrappers in (("plots", plots), ("scenes", scenes),
                                   ("items", items), ("views", views)):
                remaining = sum(isValid(wrapper) for wrapper in wrappers)
                assert remaining == 0, (cycle, name, remaining)
        monkeypatch.undo()
        print("OWNED_PLOTS_DESTROYED", flush=True)
        """
    )
    output = _run_child(["-c", script], tmp_path)
    assert "OWNED_PLOTS_DESTROYED" in output

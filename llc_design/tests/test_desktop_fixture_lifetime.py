"""Desktop fixture ownership must end at the test boundary, with native Qt."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_PREAMBLE = '''
from contextlib import contextmanager

import pytest
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QMainWindow
from pyqtgraph import GraphicsLayoutWidget
from shiboken6 import isValid

from llc_design.tests.conftest import desktop_qapp
from llc_design.tests.desktop_support import (
    offline_update_checks, load_root_test_fixtures, record_test_fixture_proof,
)
root_fixtures = load_root_test_fixtures()

session = contextmanager(desktop_qapp.__wrapped__)
isolate = contextmanager(root_fixtures.dispose_test_owned_desktop_widgets.__wrapped__)
with offline_update_checks(), session() as app, pytest.MonkeyPatch.context() as monkeypatch:
    app.setQuitOnLastWindowClosed(False)
'''


def _run_python(arguments, tmp_path):
    pytest.importorskip("PySide6.QtWidgets")
    pytest.importorskip("pyqtgraph")
    proof_path = tmp_path / "fixture-origins.jsonl"
    result = subprocess.run(
        [sys.executable, *arguments], cwd=_ROOT, capture_output=True,
        text=True, timeout=120,
        env={**os.environ, "QT_QPA_PLATFORM": "offscreen", "PYTHONFAULTHANDLER": "1",
             "PDT_TEST_FIXTURE_PROOF": str(proof_path)},
        check=False,
    )
    output = result.stdout + result.stderr
    (tmp_path / "fixture-lifetime.log").write_text(output, encoding="utf-8")
    assert result.returncode == 0, output
    assert "Traceback" not in output, output
    assert "Error calling Python override" not in output, output
    proofs = [json.loads(line) for line in proof_path.read_text(encoding="utf-8").splitlines()]
    assert proofs and all(row["product_root"] == str(_ROOT) for row in proofs)
    assert proofs[-1]["product_origins"]


def _run_fixture_script(body, tmp_path):
    script = (_PREAMBLE + textwrap.indent(textwrap.dedent(body), "    ")
              + "\nrecord_test_fixture_proof(root_fixtures)\n")
    _run_python(["-c", script], tmp_path)


def test_short_pytest_sequence_retains_roots_and_exits_cleanly(tmp_path):
    # Use real pytest fixture ordering and retain every deleted root until
    # interpreter exit. Longer runs can conceal a pending-timer lifetime bug.
    probe = tmp_path / "test_i18n.py"
    probe.write_text(textwrap.dedent('''
        from shiboken6 import isValid
        pytest_plugins = ("llc_design.tests.root_fixture_plugin", "llc_design.tests.conftest")
        retained = []

        def test_create(desktop_qapp):
            from llc_design.core.spec import LLCDesignSpec
            from llc_design.gui.launcher import WorkspaceApplicationController
            controller = WorkspaceApplicationController(LLCDesignSpec())
            retained.extend(getattr(controller, name + "_window") for name in
                            ("llc", "pfc", "dab", "control", "fra", "power_stage"))
            desktop_qapp.processEvents()

        def test_previous_owners_are_deleted(desktop_qapp):
            assert len(retained) == 6
            assert all(not isValid(window) for window in retained)
    '''), encoding="utf-8")
    _run_python(["-m", "pytest", "-q", "-o", "addopts=", str(probe)], tmp_path)


def test_repeated_fixture_cycles_destroy_only_new_workspace_owners(tmp_path):
    _run_fixture_script('''
        from llc_design.core.spec import LLCDesignSpec
        from llc_design.gui.launcher import WorkspaceApplicationController

        # This unrelated owner predates the function fixture and must survive it.
        existing = QMainWindow()
        existing_graphics = GraphicsLayoutWidget()
        existing.setCentralWidget(existing_graphics)
        existing_plot = existing_graphics.addPlot()
        existing_plot.plot([0, 1], [1, 0])
        existing_scene = existing_graphics.scene()
        retained = []
        for cycle in range(3):
            with isolate():
                controller = WorkspaceApplicationController(LLCDesignSpec())
                windows = [controller.llc_window, controller.pfc_window,
                           controller.dab_window, controller.control_window,
                           controller.fra_window, controller.power_stage_window]
                graphics = [view for owner in windows
                            for view in owner.findChildren(GraphicsLayoutWidget)]
                scenes = [view.scene() for view in graphics]
                assert graphics and all(isValid(scene) for scene in scenes)
                retained.extend([controller, *windows, *graphics, *scenes])
                # A test may close and show a workspace several times before
                # its fixture ends. Its views must remain usable until then.
                window = controller.llc_window
                assert window.close()
                window.show()
                app.processEvents()
                assert isValid(window) and all(isValid(view) for view in graphics)
                controller.power_stage_window.toolbox.select_tool("X12")
                assert controller.power_stage_window.toolbox.calculate() is not None
                assert window.close()
            assert all(not isValid(owner) for owner in windows), cycle
            assert all(not isValid(view) for view in graphics), cycle
            assert all(not isValid(scene) for scene in scenes), cycle
            assert isValid(existing) and isValid(existing_graphics)
            assert isValid(existing_scene) and not existing_graphics.closed
            assert len(existing_plot.listDataItems()) == 1
        print("OWNED_WORKSPACES_DESTROYED_EXISTING_OWNER_PRESERVED", flush=True)
    ''', tmp_path)


def test_fixture_finishes_real_worker_and_rejects_late_result(tmp_path):
    _run_fixture_script('''
        from threading import Event
        from llc_design.gui.main_window import LLCMainWindow

        with isolate():
            window = LLCMainWindow()
            window.show()
            app.processEvents()
            started, release, finished = Event(), Event(), Event()
            callbacks = []
            def calculate():
                started.set()
                assert release.wait(5)
                finished.set()
                return "late result"
            window._run_worker("fixture lifetime", calculate, callbacks.append)
            assert started.wait(5)
            assert window._active_workers
            original_close = window.closeEvent
            def close_event(event):
                original_close(event)
                release.set()
            monkeypatch.setattr(window, "closeEvent", close_event)
            graphics = window.waveform_view.graphics
            scene = graphics.scene()
        assert finished.is_set()
        assert callbacks == []
        assert not window._active_workers
        assert not isValid(window) and not isValid(graphics) and not isValid(scene)
        print("REAL_WORKER_FINISHED_LATE_RESULT_REJECTED", flush=True)
    ''', tmp_path)


def test_fixture_handles_already_closed_and_deleted_graphics(tmp_path):
    _run_fixture_script('''
        with isolate():
            window = QMainWindow()
            graphics = GraphicsLayoutWidget()
            window.setCentralWidget(graphics)
            graphics.addPlot().plot([0, 1], [0, 1])
            graphics.close()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            assert isValid(graphics) and graphics.closed
            assert not isValid(graphics.ci)
        assert not isValid(window) and not isValid(graphics)
        print("ALREADY_CLOSED_GRAPHICS_DESTROYED", flush=True)
    ''', tmp_path)

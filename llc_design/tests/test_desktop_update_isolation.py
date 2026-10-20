"""Fresh-process proofs that host isolation cannot silently start network work."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import textwrap

import pytest

ROOT = Path(__file__).resolve().parents[2]
# This guard is independent of the updater fake. It records attempts even when
# a background worker catches the exception, and is inherited by Python children.
_GUARD = '''
import os, sys

with open(os.environ["PDT_TEST_NETWORK_ATTEMPTS"] + ".started", "a", encoding="utf-8") as log:
    log.write(str(os.getpid()) + "\\n")

def reject_outbound(event, arguments):
    if event in {"urllib.Request", "socket.getaddrinfo", "socket.connect"}:
        with open(os.environ["PDT_TEST_NETWORK_ATTEMPTS"], "a", encoding="utf-8") as log:
            log.write(event + "\\n")
        raise RuntimeError("Test attempted outbound network: " + event)

sys.addaudithook(reject_outbound)
'''


def _run_guarded(tmp_path, arguments, expected_attempts=""):
    guard = tmp_path / "guard"
    guard.mkdir()
    (guard / "sitecustomize.py").write_text(_GUARD, encoding="utf-8")
    attempts = tmp_path / "outbound-attempts.log"
    attempts.write_text("", encoding="utf-8")
    proof_path = tmp_path / "fixture-origins.jsonl"
    env = dict(os.environ, PDT_TEST_FIXTURE_PROOF=str(proof_path), QT_QPA_PLATFORM="offscreen", MPLBACKEND="Agg",
               PYTHONFAULTHANDLER="1", PDT_TEST_NETWORK_ATTEMPTS=str(attempts),
               PYTHONPATH=os.pathsep.join((str(guard), str(ROOT))))
    env.pop("PYTEST_ADDOPTS", None)
    is_pytest = arguments[:2] == ["-m", "pytest"]
    if is_pytest:
        arguments = [*arguments[:2], "-p", "llc_design.tests.root_fixture_plugin", *arguments[2:]]
    result = subprocess.run([sys.executable, *arguments], cwd=ROOT, env=env,
                            capture_output=True, text=True, timeout=120, check=False)
    output = result.stdout + result.stderr
    (tmp_path / "guarded-desktop.log").write_text(output, encoding="utf-8")
    assert Path(str(attempts) + ".started").read_text(encoding="utf-8").strip(), output
    assert attempts.read_text(encoding="utf-8") == expected_attempts, output
    assert result.returncode == 0, output
    assert "Error calling Python override" not in output, output
    if is_pytest:
        proofs = [json.loads(line) for line in proof_path.read_text(encoding="utf-8").splitlines()]
        assert proofs and all(row["product_root"] == str(ROOT) for row in proofs)
        assert proofs[-1]["product_origins"]
    return output


def test_all_workspace_hosts_keep_real_timers_without_outbound_attempts(tmp_path):
    pytest.importorskip("PySide6.QtWidgets")
    pytest.importorskip("pyqtgraph")
    probe = tmp_path / "test_new_host_names.py"
    probe.write_text(textwrap.dedent('''
        import pytest
        from pfc_design.gui import main_window as early_module
        pytest_plugins = ("llc_design.tests.conftest",)

        @pytest.fixture(scope="module")
        def hosts():
            from PySide6.QtWidgets import QApplication
            from llc_design.core.spec import LLCDesignSpec
            from llc_design.gui.launcher import WorkspaceApplicationController
            from llc_design.gui.lifetime import dispose_widgets
            app = QApplication.instance() or QApplication([])
            app.setQuitOnLastWindowClosed(False)
            controller = WorkspaceApplicationController(LLCDesignSpec())
            windows = [getattr(controller, name + "_window") for name in
                       ("llc", "pfc", "control", "fra", "dab", "power_stage")]
            try:
                yield app, windows
            finally:
                dispose_widgets(windows)

        def test_automatic_and_toolbar_checks(hosts, monkeypatch):
            from PySide6.QtCore import QTimer
            from PySide6.QtTest import QTest
            from PySide6.QtWidgets import QToolButton
            from llc_design.gui import main_window as late_module, updater
            from llc_design.tests.desktop_support import _ignore_update_check
            app, windows = hosts
            assert early_module.check_for_updates is _ignore_update_check
            assert late_module.check_for_updates is _ignore_update_check
            assert updater.check_for_updates is _ignore_update_check
            automatic, manual, real_timer = [], [], []
            def record(parent, notify_up_to_date):
                (manual if notify_up_to_date else automatic).append(type(parent).__name__)
                return _ignore_update_check(parent, notify_up_to_date)
            monkeypatch.setattr(early_module, "check_for_updates", record)
            monkeypatch.setattr(late_module, "check_for_updates", record)
            monkeypatch.setattr(updater, "check_for_updates", record)
            QTimer.singleShot(50, lambda: real_timer.append(True))
            QTest.qWait(2800)
            assert real_timer == [True]
            assert sorted(automatic) == ["LLCMainWindow", "PFCMainWindow"]
            for window in windows:
                for button in window.findChildren(QToolButton):
                    if button.text() == "检查更新":
                        button.click()
            assert sorted(manual) == ["ControlToolsMainWindow", "LLCMainWindow", "PFCMainWindow"]
            assert not updater._active_checks
            assert not updater._auto_check_started
            print("SIX_HOSTS_REAL_TIMERS_AND_TOOLBARS_ZERO_NETWORK", flush=True)
    '''), encoding="utf-8")
    output = _run_guarded(tmp_path, ["-m", "pytest", "-q", "-s", "-o", "addopts=", str(probe)])
    assert "SIX_HOSTS_REAL_TIMERS_AND_TOOLBARS_ZERO_NETWORK" in output


def test_real_updater_unit_tests_remain_mocked_and_offline(tmp_path):
    pytest.importorskip("PySide6.QtWidgets")
    output = _run_guarded(tmp_path, ["-m", "pytest", "-q", "-o", "addopts=",
                                   "llc_design/tests/test_updater.py"])
    assert "15 passed" in output


def test_network_guard_records_and_rejects_synthetic_attempt(tmp_path):
    # Trigger the audit event directly: no socket, DNS lookup or endpoint is used.
    program = """
import sys
try:
    sys.audit("socket.connect", None, ("example.invalid", 443))
except RuntimeError as exc:
    assert "Test attempted outbound network" in str(exc)
else:
    raise AssertionError("Network audit guard did not run")
"""
    _run_guarded(tmp_path, ["-c", program], expected_attempts="socket.connect\n")


@pytest.mark.parametrize("digest", [None, "0" * 64])
def test_archived_root_fixture_requires_matching_digest_before_execution(tmp_path, monkeypatch, digest):
    from llc_design.tests.desktop_support import load_root_test_fixtures

    archived = tmp_path / "conftest.py"
    archived.write_text("raise AssertionError('Unverified archive executed')\n", encoding="utf-8")
    monkeypatch.setenv("PDT_ARCHIVED_ROOT_CONFTEST", str(archived))
    if digest is None:
        monkeypatch.delenv("PDT_ARCHIVED_ROOT_CONFTEST_SHA256", raising=False)
    else:
        monkeypatch.setenv("PDT_ARCHIVED_ROOT_CONFTEST_SHA256", digest)
    with pytest.raises(AssertionError, match="Archived root fixture digest mismatch"):
        load_root_test_fixtures()

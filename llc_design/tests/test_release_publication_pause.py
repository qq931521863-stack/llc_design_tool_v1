"""Keep native qualification active while every current-release write job is paused."""
from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = (ROOT / ".github/workflows/build-release.yml").read_text(encoding="utf-8")
parts = re.split(r"(?m)^  ([a-z][a-z0-9-]*):\n", WORKFLOW.split("\njobs:\n", 1)[1])
JOBS = dict(zip(parts[1::2], parts[2::2]))
ORIGINAL_CONDITIONS = {
    "tag-release": "github.event_name == 'push' && github.ref == 'refs/heads/main'",
    "publish": "startsWith(github.ref, 'refs/tags/')",
}


@pytest.mark.parametrize("event,ref,previously_enabled", [
    ("push", "refs/heads/main", {"tag-release"}),
    ("push", "refs/tags/v9.9.0", {"publish"}),
    ("workflow_dispatch", "refs/heads/main", set()),
    ("workflow_dispatch", "refs/tags/v9.9.0", {"publish"}),
    ("pull_request", "refs/pull/1/merge", set()),
])
@pytest.mark.parametrize("job", ORIGINAL_CONDITIONS)
def test_publication_pause_blocks_every_entry_point(event, ref, previously_enabled, job):
    header = JOBS[job].split("    steps:\n", 1)[0]
    condition, = re.findall(r"(?m)^    if: (.+)$", header)
    original = ORIGINAL_CONDITIONS[job]
    # Constrain evaluation to these exact simple boolean expressions, not a
    # general Actions interpreter. No event/input may bypass the literal false.
    assert condition == "${{ false && " + original + " }}"
    context = {"__builtins__": {}, "false": False,
               "github": SimpleNamespace(event_name=event, ref=ref),
               "startsWith": str.startswith}
    assert eval(condition[3:-2].strip().replace("&&", "and"), context) is False
    # Show that the same real entry paths would write without the pause.
    assert eval(original.replace("&&", "and"), context) is (job in previously_enabled)
    assert "    needs: [test, build]\n" in header


def test_pause_keeps_test_build_and_frozen_verification_mandatory():
    assert set(JOBS) == {"test", "build", "tag-release", "publish"}
    triggers = WORKFLOW.split("\non:\n", 1)[1].split("\npermissions:\n", 1)[0]
    assert "  push:\n    branches: [main]\n    tags: [\"v*\"]\n" in triggers
    assert "  pull_request:\n    branches: [main]\n" in triggers
    assert "  workflow_dispatch:\n" in triggers
    assert "permissions:\n  contents: read\n" in WORKFLOW
    assert "continue-on-error" not in WORKFLOW
    for name in ("test", "build"):
        block = JOBS[name]
        header = block.split("    steps:\n", 1)[0]
        assert not re.search(r"(?m)^    if:", header)
        assert "contents: write" not in block and "actions: write" not in block
        assert "gh release" not in block and "git push" not in block
        assert "      - name: Run tests\n        run: python -m pytest\n" in block
    assert "    needs: test\n" in JOBS["build"]
    assert 'PSD_RUN_PFC_SWITCHED_GUI: "1"' in JOBS["test"]
    assert "fail-fast: false" in JOBS["build"]
    assert "os: macos-latest" in JOBS["build"]
    assert "os: windows-latest" in JOBS["build"]
    assert ('      - name: Verify frozen application and fresh evidence\n'
            '        shell: bash\n        run: |\n'
            '          python packaging/verify_bundle.py "${{ matrix.executable }}"') in JOBS["build"]


def test_native_regression_preflight_does_not_replace_complete_test_gate():
    block = JOBS["build"]
    early = block.index("      - name: Check native numerical and desktop regressions first\n")
    complete = block.index("      - name: Run tests\n        run: python -m pytest\n")
    frozen = block.index("      - name: Build with PyInstaller\n")
    assert early < complete < frozen
    assert "python -m pytest -x" in block[early:complete]
    assert "test_independent_validation.py" in block[early:complete]
    assert "test_two_stage_desktop.py" in block[early:complete]
    assert "test_llc_desktop_layout.py" in block[early:complete]
    assert "test_worker_submission.py" in block[early:complete]
    assert "test_worker_producer_lifetime.py" in block[early:complete]
    assert "test_schematic_rendering.py" in block[early:complete]
    assert "test_schematic_references.py" in block[early:complete]
    assert "continue-on-error" not in block

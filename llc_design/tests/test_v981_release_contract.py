"""The 9.8.1 recovery changes test decoding, never tagged application bytes."""
from __future__ import annotations

import ast
import hashlib
import json
import textwrap
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SOURCE_SHA = "bc969ab377d4b029cce351f3a38146d3e2a7659c"
RECOVERY = ROOT / ".github/workflows/complete-v981-release.yml"
# Recovery checks out SOURCE_SHA, not the current release's evolving workflow.
# Exact bytes from bc969ab:.github/workflows/build-release.yml, retained so a
# shallow checkout can test the historical contract without network or Git.
ORIGINAL = Path(__file__).with_name("fixtures") / "build-release-v981.yml"


def test_original_workflow_fixture_is_immutable():
    assert hashlib.sha256(ORIGINAL.read_bytes()).hexdigest() == "a9c1ed4f742a22d6f103678c16979876ff6fadfa863fe4cae0d8fd8823ec3d99"


def _step(text: str, heading: str) -> str:
    block = text.split(f"- name: {heading}\n", 1)[1]
    return textwrap.dedent(block.split("run: |\n", 1)[1].split("\n      - name:", 1)[0])


def _python_step(heading: str) -> str:
    return _step(RECOVERY.read_text(encoding="utf-8"), heading).split("python - <<'PY'\n", 1)[1].rsplit("\nPY", 1)[0]


def test_recovery_pins_original_source_and_scopes_utf8_to_tests():
    text = RECOVERY.read_text(encoding="utf-8")
    assert text.count(f"ref: {SOURCE_SHA}") == 2
    for expected in ("68d2a894bcb66ec4dcd7a3131c13d52e1a96697a", "be83a24e1055b215e28da9bc5945f17a9508b792",
                     "39083489678/attempts/1/jobs", "117545122672", "117547617917",
                     "artifact-ids: 12120287047", "merge-multiple: true",
                     "needs: [verifier-tests, windows]", "publish-refs/tags/v9.8.1",
                     "git diff --exit-code", "git diff --cached --exit-code"):
        assert expected in text
    assert "python -X utf8 -m pytest\n" in text
    assert "python packaging/verify_bundle.py" in text
    assert "PYTHONUTF8:" not in text
    assert "continue-on-error" not in text
    assert "git push" not in text
    assert "--force" not in text
    assert text.count("github.event_name != 'pull_request' && github.ref == 'refs/heads/main'") == 2


def test_future_evidence_test_readers_use_explicit_utf8():
    module = ast.parse((ROOT / "llc_design/tests/test_candidate_evidence.py").read_text(encoding="utf-8"))
    readers = [node for node in ast.walk(module) if isinstance(node, ast.Call)
               and isinstance(node.func, ast.Attribute) and node.func.attr == "read_text"]
    assert len(readers) == 4
    assert all(any(k.arg == "encoding" and isinstance(k.value, ast.Constant)
                   and k.value.value == "utf-8" for k in call.keywords) for call in readers)


@pytest.mark.parametrize("failure", [None, "test", "frozen"])
def test_original_job_gate_requires_success(tmp_path, monkeypatch, failure):
    monkeypatch.chdir(tmp_path)
    run = {"id": 39083489678, "run_attempt": 1, "head_sha": SOURCE_SHA, "head_branch": "v9.8.1", "status": "completed"}
    names = ["Run tests", "Build with PyInstaller", "Verify frozen application and fresh evidence",
             "Zip macOS app", "Upload verified package and evidence"]
    jobs = [{"id": 117545122672, "status": "completed", "conclusion": "success"},
            {"id": 117547617917, "status": "completed", "conclusion": "success",
             "steps": [{"name": name, "conclusion": "success"} for name in names]}]
    if failure == "test":
        jobs[0]["conclusion"] = "failure"
    if failure == "frozen":
        jobs[1]["steps"][2]["conclusion"] = "failure"
    artifact = {"id": 12120287047, "name": "power-design-tool-macos-arm64", "expired": False,
                "workflow_run": {"id": run["id"], "head_sha": SOURCE_SHA},
                "digest": "sha256:4c1a7806cee97b446d4d54cadfba958a6fdf855ff419e254637c71263976694b"}
    Path("source-artifact.json").write_text(json.dumps(artifact), encoding="utf-8")
    Path("source-run.json").write_text(json.dumps(run), encoding="utf-8")
    Path("source-jobs.json").write_text(json.dumps({"jobs": jobs}), encoding="utf-8")
    code = _python_step("Require original successful Ubuntu and macOS gates")
    if failure:
        with pytest.raises(AssertionError):
            exec(compile(code, "original-job-gate", "exec"), {})
    else:
        exec(compile(code, "original-job-gate", "exec"), {})


def _assets(tmp_path, monkeypatch, defect):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GITHUB_REPOSITORY", "yangshuai2022-star/power-design-toolkit")
    monkeypatch.setenv("GITHUB_RUN_ID", "123")
    monkeypatch.setenv("GITHUB_REF_NAME", "main")
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path))
    Path(".github/workflows").mkdir(parents=True)
    Path(".github/workflows/build-release.yml").write_bytes(ORIGINAL.read_bytes())
    Path("engineering_data").mkdir()
    data = b'{"brands": ["TEST"]}\n'
    Path("engineering_data/brand_taxonomy.json").write_bytes(data)
    Path("pyproject.toml").write_text('[project]\nversion="9.8.1"\n', encoding="utf-8")
    Path("CHANGELOG.md").write_text("# Changelog\n\n## 9.8.1 — test\nEvidence.\n", encoding="utf-8")
    Path("release").mkdir()
    for runner, platform, name, exe in (
        ("Windows", "win32", "PowerDesignTool-Windows-x64.zip", "PowerDesignTool/PowerDesignTool.exe"),
        ("macOS", "darwin", "PowerDesignTool-macOS-arm64.zip", "PowerDesignTool.app/Contents/MacOS/PowerDesignTool"),
    ):
        proof = dict(success=True, frozen=True, version="9.8.1", platform=platform,
                     guided_design=True, workspaces=["llc", "pfc", "control", "fra", "dab"],
                     brand_taxonomy_sha256=hashlib.sha256(data).hexdigest())
        if defect == "proof" and runner == "Windows":
            proof["success"] = False
        Path(f"release/bundle-proof-{runner}.json").write_text(json.dumps(proof), encoding="utf-8")
        with zipfile.ZipFile(Path("release") / name, "w") as archive:
            archive.writestr(exe, b"fixture executable")
            archive.writestr("root/engineering_data/brand_taxonomy.json",
                             b"wrong" if defect == "resource" and runner == "Windows" else data)
    if defect == "missing-platform":
        Path("release/PowerDesignTool-macOS-arm64.zip").unlink()


@pytest.mark.parametrize("defect", [None, "proof", "resource", "missing-platform"])
def test_recovery_retains_exact_original_archive_and_publish_gates(tmp_path, monkeypatch, defect):
    _assets(tmp_path, monkeypatch, defect)
    code = _python_step("Run unchanged tagged archive and checksum gates")
    if defect:
        with pytest.raises(AssertionError):
            exec(compile(code, "recovery-archive-gate", "exec"), {})
        return
    exec(compile(code, "recovery-archive-gate", "exec"), {})
    assert len(list(Path("release").iterdir())) == 5
    assert len(Path("release/SHA256SUMS.txt").read_text(encoding="utf-8").splitlines()) == 2
    assert Path("v981-publish.sh").read_text(encoding="utf-8") == _step(
        ORIGINAL.read_text(encoding="utf-8"), "Stage and publish complete release")
    notes = Path("release-notes.md").read_text(encoding="utf-8")
    assert SOURCE_SHA in notes and "test-only override" in notes

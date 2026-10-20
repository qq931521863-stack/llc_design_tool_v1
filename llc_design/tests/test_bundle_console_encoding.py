"""Console encoding must not weaken or interrupt frozen-report verification."""
from __future__ import annotations

import io
import json
import sys

import pytest

from llc_design.tests.test_release_verification import MODULE, _child, _verify


@pytest.mark.parametrize("encoding", ["cp1252", "ascii", "utf-8"])
def test_console_encoding_preserves_report_and_exit(tmp_path, monkeypatch, encoding):
    payload = {"label": "Primary ↔ secondary 中文", "success": True}
    executable = tmp_path / "app.exe"
    executable.touch()
    taxonomy = tmp_path / "taxonomy.json"
    taxonomy.write_text("{}", encoding="utf-8")
    output = tmp_path / "proof.json"
    raw = io.BytesIO()
    console = io.TextIOWrapper(raw, encoding=encoding, errors="strict")
    monkeypatch.setattr(sys, "stdout", console)
    monkeypatch.setattr(sys, "argv", ["verify_bundle.py", str(executable),
                                    "--version", "9.9.0", "--taxonomy", str(taxonomy),
                                    "--output", str(output)])
    calls = []

    def verified(*args, **kwargs):
        calls.append((args, kwargs))
        return payload

    monkeypatch.setattr(MODULE, "verify_bundle", verified)
    assert MODULE.main() == 0
    assert len(calls) == 1
    assert json.loads(output.read_text(encoding="utf-8")) == payload
    assert payload["label"] in output.read_text(encoding="utf-8")
    console.flush()
    logged = raw.getvalue().decode(encoding)
    assert json.loads(logged.split("\nFrozen application verification: PASS")[0]) == payload


def test_child_unicode_log_keeps_real_verification_and_failure_gate(tmp_path, monkeypatch):
    command = _child(tmp_path)
    script = tmp_path / "child.py"
    script.write_text("print('phase ↔ 中')\n" + script.read_text(encoding="utf-8"),
                      encoding="utf-8")
    raw = io.BytesIO()
    console = io.TextIOWrapper(raw, encoding="cp1252", errors="strict")
    monkeypatch.setattr(sys, "stdout", console)
    # The child emits UTF-8 regardless of the host's redirected console codepage.
    monkeypatch.setenv("PYTHONIOENCODING", "utf-8")
    assert _verify(command)["success"] is True
    console.flush()
    assert "phase" in raw.getvalue().decode("cp1252")
    import subprocess
    with pytest.raises(subprocess.CalledProcessError):
        _verify(_child(tmp_path, exit_code=7))

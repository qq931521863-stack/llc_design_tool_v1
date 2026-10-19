"""Portable IO-model and bounded strict-byte assertion counterexamples."""
import difflib

import pytest

from power_stage_design.tests.export_checks import (
    assert_exact_bytes,
    model_windows_text_newlines,
)


def test_windows_io_model_translates_only_default_text_writes(monkeypatch, tmp_path):
    model_windows_text_newlines(monkeypatch, tmp_path)
    text = "实测\nCSV\r\n"
    default = tmp_path / "default.txt"
    default.write_text(text, encoding="utf-8")
    assert_exact_bytes(default.read_bytes(), "实测\r\nCSV\r\r\n".encode())
    explicit = tmp_path / "explicit.txt"
    explicit.write_text(text, encoding="utf-8", newline="")
    assert_exact_bytes(explicit.read_bytes(), text.encode("utf-8"))
    binary = tmp_path / "binary.txt"
    binary.write_bytes(text.encode("utf-8"))
    assert_exact_bytes(binary.read_bytes(), text.encode("utf-8"))


@pytest.mark.parametrize("change", ["first", "middle", "last", "truncated", "appended", "crlf"])
def test_large_byte_mismatch_has_bounded_diagnostics_without_sequence_diff(monkeypatch, change):
    expected = b'{"code": 12345, "value": 0.5}\n' * 65536
    if change in {"first", "middle", "last"}:
        offset = {"first": 0, "middle": len(expected) // 2, "last": len(expected) - 1}[change]
        actual = expected[:offset] + bytes([expected[offset] ^ 1]) + expected[offset + 1:]
    elif change == "truncated":
        offset, actual = len(expected) - 1, expected[:-1]
    elif change == "appended":
        offset, actual = len(expected), expected + b"x"
    else:
        offset, actual = expected.index(b"\n"), expected.replace(b"\n", b"\r\n")

    def forbidden_diff(*args, **kwargs):
        raise RuntimeError("Large export checks must not invoke sequence diff")

    monkeypatch.setattr(difflib, "SequenceMatcher", forbidden_diff)
    monkeypatch.setattr(difflib, "ndiff", forbidden_diff)
    assert_exact_bytes(expected, expected)
    with pytest.raises(pytest.fail.Exception) as failure:
        assert_exact_bytes(actual, expected)
    message = str(failure.value)
    assert f"offset {offset};" in message
    assert f"actual length={len(actual)}, sha256=" in message
    assert f"expected length={len(expected)}, sha256=" in message
    assert len(message) < 400

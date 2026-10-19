"""Exact export checks without pytest's potentially unbounded sequence diff."""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest


def assert_exact_bytes(actual: bytes, expected: bytes) -> None:
    # Equality is over every byte; hashes are only bounded failure diagnostics.
    if actual == expected:
        return
    offset = next((i for i, (a, b) in enumerate(zip(actual, expected)) if a != b),
                  min(len(actual), len(expected)))
    pytest.fail(
        f"Export bytes differ at offset {offset}; "
        f"actual length={len(actual)}, sha256={hashlib.sha256(actual).hexdigest()}; "
        f"expected length={len(expected)}, sha256={hashlib.sha256(expected).hexdigest()}; "
        f"actual next16={actual[offset:offset + 16].hex()}; "
        f"expected next16={expected[offset:offset + 16].hex()}",
        pytrace=False,
    )


def model_windows_text_newlines(monkeypatch, directory: Path) -> None:
    """Use the real text IO CRLF translator at this directory's write boundary.

    This models Windows' default newline=None on any OS, without changing the
    serializers, binary IO, reads, or explicitly selected newline policies.
    """
    original_open = Path.open

    def open_with_windows_newlines(path, mode="r", buffering=-1, encoding=None,
                                   errors=None, newline=None):
        if (path.parent == directory and "b" not in mode
                and any(flag in mode for flag in "wax+") and newline is None):
            newline = "\r\n"
        return original_open(path, mode, buffering, encoding, errors, newline)

    monkeypatch.setattr(Path, "open", open_with_windows_newlines)

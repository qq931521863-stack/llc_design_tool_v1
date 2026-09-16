"""Lightweight message catalogue for the whole toolkit.

Design decisions
----------------

**The source string is the key.**  UI text is written once, in the language the
author thinks in (Simplified Chinese), and ``t("显示设计参数")`` looks that string
up in the active catalogue.  A string with no entry for the active language is
returned unchanged, so a partial translation degrades to the original wording
instead of showing a key name or an empty label.  This also makes the retrofit
incremental: a file only has to be touched where translations exist.

**Existing English is not translated.**  Strings that are already English in the
source (``Bode``, ``H(z)``, ``C99``, ``PM``, ``Kp``, ``Summary``, ...) deliberately
have no entries in any catalogue.  Per project policy they stay as written, in
every language.  ``zh-Hans`` uses the source wording, with selected user-facing refinements
in its catalogue. Technical diagnostics may have separate presentation helpers.

**No Qt import here.**  The catalogue is also usable from CLI, report and
code-generation code, which must keep working without PySide6 installed.
Language persistence and fonts live in :mod:`llc_design.i18n.fonts` and
:mod:`llc_design.i18n.gui`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .catalog import CATALOGUES


@dataclass(frozen=True)
class Language:
    code: str
    native_name: str
    english_name: str

    def __str__(self) -> str:  # pragma: no cover - convenience only
        return self.native_name


LANGUAGES: tuple[Language, ...] = (
    Language("zh-Hans", "简体中文", "Chinese (Simplified)"),
    Language("en", "English", "English"),
    Language("ja", "日本語", "Japanese"),
    Language("ko", "한국어", "Korean"),
)

DEFAULT_LANGUAGE = "zh-Hans"
_LANGUAGE_CODES = tuple(item.code for item in LANGUAGES)

_current: str = DEFAULT_LANGUAGE
_listeners: list[Callable[[str], None]] = []


def language_codes() -> tuple[str, ...]:
    return _LANGUAGE_CODES


def language(code: str) -> Language:
    for item in LANGUAGES:
        if item.code == code:
            return item
    raise KeyError(f"unknown language {code!r}; expected one of {list(_LANGUAGE_CODES)}")


def current_language() -> str:
    return _current


def set_language(code: str, *, notify: bool = True) -> str:
    """Activate ``code`` and return the previously active code."""
    global _current
    language(code)  # validate
    previous = _current
    _current = code
    if notify and previous != code:
        for callback in list(_listeners):
            callback(code)
    return previous


def add_language_listener(callback: Callable[[str], None]) -> None:
    if callback not in _listeners:
        _listeners.append(callback)


def remove_language_listener(callback: Callable[[str], None]) -> None:
    if callback in _listeners:
        _listeners.remove(callback)


def catalogue(code: str | None = None) -> dict[str, str]:
    return CATALOGUES.get(code or _current, {})


def t(source: str, /, **fmt: object) -> str:
    """Translate ``source`` into the active language.

    ``fmt`` values are applied with :meth:`str.format` only when the caller
    supplied placeholders, so braces in engineering text stay literal.
    """
    text = catalogue().get(source, source)
    if fmt:
        try:
            return text.format(**fmt)
        except (KeyError, IndexError, ValueError):
            return text
    return text


def missing_translations(code: str) -> list[str]:
    """Return the keys that exist in some catalogue but not in ``code``.

    Used by tests to keep the four catalogues in sync; ``zh-Hans`` is the source
    language and therefore never has gaps.
    """
    target = CATALOGUES.get(code, {})
    all_keys: set[str] = set()
    for other_code, entries in CATALOGUES.items():
        if other_code == code or other_code == DEFAULT_LANGUAGE:
            continue
        all_keys.update(entries)
    return sorted(key for key in all_keys if key not in target)


def untranslated(key_sources: list[str], code: str | None = None) -> list[str]:
    """Return the subset of ``key_sources`` with no entry in the catalogue."""
    entries = catalogue(code)
    return [source for source in key_sources if source not in entries]


__all__ = [
    "DEFAULT_LANGUAGE",
    "LANGUAGES",
    "Language",
    "add_language_listener",
    "catalogue",
    "current_language",
    "language",
    "language_codes",
    "missing_translations",
    "remove_language_listener",
    "set_language",
    "t",
    "untranslated",
]

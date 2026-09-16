"""Aggregate all catalogues.

Keeping the aggregation in one module means :mod:`llc_design.i18n` never has to
know which languages exist on disk, and a missing language module degrades to
"no translations" instead of an import error in a packaged build.
"""

from __future__ import annotations

from . import en, ja, ko, pfc_engineering, zh_Hans


def _merged(language: str, base: dict[str, str]) -> dict[str, str]:
    """Return one catalogue including feature-scoped translation additions."""
    return {**base, **pfc_engineering.CATALOGUES.get(language, {})}


CATALOGUES: dict[str, dict[str, str]] = {
    "zh-Hans": zh_Hans.CATALOGUE,
    "en": _merged("en", en.CATALOGUE),
    "ja": _merged("ja", ja.CATALOGUE),
    "ko": _merged("ko", ko.CATALOGUE),
}

__all__ = ["CATALOGUES"]

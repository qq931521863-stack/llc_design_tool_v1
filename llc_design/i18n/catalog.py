"""Aggregate all catalogues.

Keeping the aggregation in one module means :mod:`llc_design.i18n` never has to
know which languages exist on disk, and a missing language module degrades to
"no translations" instead of an import error in a packaged build.
"""

from __future__ import annotations

from . import en, ja, ko, zh_Hans

CATALOGUES: dict[str, dict[str, str]] = {
    "zh-Hans": zh_Hans.CATALOGUE,
    "en": en.CATALOGUE,
    "ja": ja.CATALOGUE,
    "ko": ko.CATALOGUE,
}

__all__ = ["CATALOGUES"]

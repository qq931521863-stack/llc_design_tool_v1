"""Per-language font stacks.

Two consumers need different mechanisms:

* **Qt widgets** take a single family name; the first installed family from the
  language stack wins, and Windows/Linux font fallback covers anything missing.
* **Matplotlib** takes an ordered ``font.sans-serif`` list and picks the first
  installed entry, so the whole stack can be handed over.

The stacks are language-specific on purpose.  A Simplified-Chinese stack still
*renders* Japanese kana and Korean hangul, but shared Han characters take the
Chinese glyph variants (直/骨/次 and friends), which is visibly wrong for a
Japanese or Korean reader.  English deliberately has no override so the platform
default is kept.
"""

from __future__ import annotations

_ZH_HANS = (
    "Microsoft YaHei UI", "Microsoft YaHei", "PingFang SC", "Hiragino Sans GB",
    "Noto Sans CJK SC", "Source Han Sans SC", "WenQuanYi Micro Hei", "AR PL UMing CN",
)
_JA = (
    "Meiryo", "Yu Gothic UI", "Yu Gothic", "Hiragino Sans", "Hiragino Kaku Gothic ProN",
    "Noto Sans CJK JP", "Source Han Sans JP", "MS Gothic", "TakaoPGothic",
)
_KO = (
    "Malgun Gothic", "Apple SD Gothic Neo", "Noto Sans CJK KR", "Source Han Sans KR",
    "NanumGothic", "UnDotum", "Gulim",
)

# English keeps the platform default (no override) but still needs a CJK-capable
# fallback for the Chinese contact/blog strings that are intentionally not
# translated, and for measurements whose units are written in Chinese.
_EN_FALLBACK = ("Segoe UI", "Helvetica Neue", "Noto Sans")

QT_FONT_STACKS: dict[str, tuple[str, ...]] = {
    "zh-Hans": _ZH_HANS,
    "en": _EN_FALLBACK,
    "ja": _JA,
    "ko": _KO,
}

MPL_FONT_STACKS: dict[str, tuple[str, ...]] = {
    "zh-Hans": _ZH_HANS + ("DejaVu Sans",),
    "en": _EN_FALLBACK + _ZH_HANS + ("DejaVu Sans",),
    "ja": _JA + ("DejaVu Sans",),
    "ko": _KO + ("DejaVu Sans",),
}

# Kept for callers that only need "a CJK font exists" (cursor labels, PDF export).
CJK_FONT_STACK: tuple[str, ...] = _ZH_HANS


def qt_font_stack(code: str) -> tuple[str, ...]:
    return QT_FONT_STACKS.get(code, ())


def matplotlib_font_stack(code: str) -> tuple[str, ...]:
    return MPL_FONT_STACKS.get(code, MPL_FONT_STACKS["en"])


def installed_family(candidates: tuple[str, ...]) -> str | None:
    """Return the first candidate font family that Qt reports as installed."""
    try:
        from PySide6.QtGui import QFontDatabase
    except Exception:  # pragma: no cover - PySide6 is an optional extra
        return None
    try:
        families = {name.casefold() for name in QFontDatabase.families()}
    except Exception:  # pragma: no cover - QGuiApplication may not exist yet
        return None
    for candidate in candidates:
        if candidate.casefold() in families:
            return candidate
    return None


def apply_qt_font(app, code: str) -> str | None:
    """Apply the language's font family to the whole application.

    Returns the family that was applied, or ``None`` when the platform default
    is kept (English, or no candidate installed).
    """
    candidates = qt_font_stack(code)
    if not candidates:
        return None
    family = installed_family(candidates)
    if family is None:
        return None
    from PySide6.QtGui import QFont

    font = QFont(app.font())
    font.setFamily(family)
    app.setFont(font)
    return family


def apply_matplotlib_fonts(code: str) -> str | None:
    """Point Matplotlib's sans-serif stack at the active language."""
    try:
        from matplotlib import rcParams
    except Exception:  # pragma: no cover - matplotlib is a hard dependency, but keep it safe
        return None
    stack = list(matplotlib_font_stack(code))
    rcParams["font.sans-serif"] = stack
    rcParams["axes.unicode_minus"] = False
    return stack[0] if stack else None


__all__ = [
    "CJK_FONT_STACK",
    "MPL_FONT_STACKS",
    "QT_FONT_STACKS",
    "apply_matplotlib_fonts",
    "apply_qt_font",
    "installed_family",
    "matplotlib_font_stack",
    "qt_font_stack",
]

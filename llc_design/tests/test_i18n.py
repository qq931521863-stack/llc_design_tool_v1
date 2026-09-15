"""Message-catalogue and language-switching regression tests.

The strongest test here is `test_every_bound_shell_string_is_translated`: it
builds all four workspaces, harvests every string the UI actually binds for
translation, and requires a translation for each one.  Adding a new toolbar or
tab to any workspace therefore fails this test until the catalogues are updated.
"""
from __future__ import annotations

import os
import re

import pytest

_CJK = re.compile(r"[\u4e00-\u9fff]")


def _qapp(qt_widgets):
    return qt_widgets.QApplication.instance() or qt_widgets.QApplication([])


def _controller(qt_widgets):
    from llc_design.core.spec import LLCDesignSpec
    from llc_design.gui.launcher import WorkspaceApplicationController

    app = _qapp(qt_widgets)
    controller = WorkspaceApplicationController(LLCDesignSpec())
    app.processEvents()
    return app, controller


def test_language_registry_and_lookup():
    from llc_design.i18n import LANGUAGES, current_language, language, set_language, t

    assert [item.code for item in LANGUAGES] == ["zh-Hans", "en", "ja", "ko"]
    assert language("ja").native_name == "日本語"
    with pytest.raises(KeyError):
        language("de")

    previous = current_language()
    try:
        set_language("en", notify=False)
        assert current_language() == "en"
        assert t("隐藏设计参数") == "Hide design parameters"
        # Existing English is intentionally not translated.
        assert t("Current Bode") == "Current Bode"
        assert t("H(z)") == "H(z)"
        # Unknown keys fall back to the source text.
        assert t("这是一个尚未翻译的字符串") == "这是一个尚未翻译的字符串"
    finally:
        set_language(previous, notify=False)


def test_shell_keys_are_present_in_every_translated_catalogue():
    from llc_design.i18n import catalogue

    shells = {code: {k for k in catalogue(code) if not k.startswith("helpbody.")} for code in ("en", "ja", "ko")}
    reference = shells["en"]
    for code in ("ja", "ko"):
        assert shells[code] == reference, {
            "missing": sorted(reference - shells[code]),
            "extra": sorted(shells[code] - reference),
        }


def test_no_identity_entries_for_existing_english():
    """A catalogue entry that maps a string to itself would defeat the policy."""
    from llc_design.i18n import catalogue

    for code in ("en", "ja", "ko"):
        for key, value in catalogue(code).items():
            if not _CJK.search(key):
                assert value != key, f"{code}: {key!r} maps to itself"


def test_every_bound_shell_string_is_translated():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    from PySide6.QtGui import QAction
    from PySide6.QtWidgets import QTabWidget, QWidget

    from llc_design.gui.i18n_ui import bind_window
    from llc_design.i18n import catalogue

    app, controller = _controller(qt_widgets)
    sources: set[str] = set()
    for window in (controller.llc_window, controller.pfc_window, controller.control_window, controller.fra_window):
        bind_window(window)
        for obj in list(window.findChildren(QWidget)) + list(window.findChildren(QAction)):
            source = obj.property("i18nSourceText")
            if source and not obj.property("i18nSkip"):
                sources.add(str(source))
        for tabs in window.findChildren(QTabWidget):
            for source in tabs.property("i18nTabSources") or []:
                sources.add(str(source))
    chinese = sorted(source for source in sources if _CJK.search(source))
    assert len(chinese) > 100, "binding harvested suspiciously few strings"

    for code in ("en", "ja", "ko"):
        entries = catalogue(code)
        missing = [source for source in chinese if source not in entries]
        assert not missing, f"{code} is missing {len(missing)} shell strings: {missing[:8]}"

    for window in (controller.llc_window, controller.pfc_window, controller.control_window, controller.fra_window):
        window.close()
    app.processEvents()


def test_switching_language_retranslates_and_restores():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    from PySide6.QtWidgets import QToolBar, QToolButton

    from llc_design.gui.i18n_ui import apply_language
    from llc_design.i18n import set_language

    app, controller = _controller(qt_widgets)
    window = controller.llc_window
    zh_title = window.windowTitle()

    def action_texts():
        return [a.text() for bar in window.findChildren(QToolBar) for a in bar.actions() if a.text()]

    assert "加载 JSON" in action_texts()
    try:
        apply_language("en", app)
        assert window.windowTitle() == "Power Design Toolkit — LLC Design / Waveform / Control"
        assert "Load JSON" in action_texts()
        assert "加载 JSON" not in action_texts()
        tabs = window.centralWidget()
        assert tabs.tabText(0) == "Overview"

        apply_language("ja", app)
        assert window.windowTitle().startswith("電源設計ツールボックス")
        assert tabs.tabText(0) == "概要"
        assert any(b.objectName() == "languageSelector" and b.text() == "言語 / Language"
                   for b in window.findChildren(QToolButton))

        apply_language("ko", app)
        assert tabs.tabText(0) == "개요"

        apply_language("zh-Hans", app)
        assert window.windowTitle() == zh_title
        assert "加载 JSON" in action_texts()
        assert tabs.tabText(0) == "设计总览"
    finally:
        set_language("zh-Hans", notify=False)
        window.close()
        app.processEvents()


def test_language_choice_is_persisted():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    from llc_design.gui.i18n_ui import apply_language, load_language
    from llc_design.i18n import set_language

    app = _qapp(qt_widgets)
    try:
        apply_language("en", app)
        assert load_language() == "en"
    finally:
        apply_language("zh-Hans", app)
        set_language("zh-Hans", notify=False)


def test_language_menu_offers_all_four_and_marks_the_active_one():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    from llc_design.gui.i18n_ui import apply_language
    from llc_design.i18n import LANGUAGES, current_language, set_language

    app, controller = _controller(qt_widgets)
    window = controller.pfc_window
    button = next(b for b in window.findChildren(qt_widgets.QToolButton)
                  if b.objectName() == "languageSelector")
    menu = button.menu()
    try:
        apply_language("ja", app)
        assert [a.data() for a in menu.actions()] == [item.code for item in LANGUAGES]
        checked = [a.data() for a in menu.actions() if a.isChecked()]
        assert checked == ["ja"] == [current_language()]
        # Language names themselves must never be translated.
        assert any("日本語" in a.text() for a in menu.actions())
    finally:
        apply_language("zh-Hans", app)
        set_language("zh-Hans", notify=False)
        window.close()
        app.processEvents()


def test_font_stacks_cover_all_languages_and_are_applied():
    from llc_design.i18n import language_codes
    from llc_design.i18n.fonts import (
        apply_matplotlib_fonts,
        matplotlib_font_stack,
        qt_font_stack,
    )

    for code in language_codes():
        assert qt_font_stack(code), code
        assert matplotlib_font_stack(code), code
    # Japanese and Korean must not lead with a Simplified-Chinese family.
    assert qt_font_stack("ja")[0] in {"Meiryo", "Yu Gothic UI", "Yu Gothic"}
    assert qt_font_stack("ko")[0] == "Malgun Gothic"
    assert apply_matplotlib_fonts("ja")
    from matplotlib import rcParams

    assert rcParams["font.sans-serif"][0] == "Meiryo"
    apply_matplotlib_fonts("zh-Hans")


def test_help_bodies_fall_back_with_a_visible_notice():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    import llc_design.i18n as i18n

    from llc_design.gui.help import HelpDialog
    from llc_design.i18n import catalogue, set_language, t

    app = _qapp(qt_widgets)

    def notice_for(code: str) -> str:
        return catalogue(code)["此章节暂无当前语言的正文译文，以下显示原文。"]

    try:
        # Source language: original text, never a notice.
        set_language("zh-Hans", notify=False)
        zh = HelpDialog(None, "fra", "测量语义：TS 类型不能猜")
        assert "Plant TS" in zh.browser.toPlainText()
        assert notice_for("en") not in zh.browser.toPlainText()
        zh.close()

        # A section that *is* translated must not show the notice.
        set_language("en", notify=False)
        assert "helpbody.fra.ts_semantics" in catalogue("en")
        en = HelpDialog(None, "fra", "测量语义：TS 类型不能猜")
        text = en.browser.toPlainText()
        assert "Complete Loop TS" in text and "reconstruction" in text
        assert notice_for("en") not in text
        en.close()

        # A section with no English body shows the source plus the notice.
        assert "helpbody.llc.impl_models" not in catalogue("en")
        fallback = HelpDialog(None, "llc", "实现说明 — FHA / HB / TD 三种模型怎么算的")
        fallback_text = fallback.browser.toPlainText()
        assert notice_for("en") in fallback_text
        assert "FHA（基波近似）" in fallback_text
        fallback.close()

        # Japanese translates the title but not this body yet: same contract.
        set_language("ja", notify=False)
        ja = HelpDialog(None, "llc", "实现说明 — FHA / HB / TD 三种模型怎么算的")
        ja_text = ja.browser.toPlainText()
        assert ja_text.startswith("実装の説明 — FHA / HB / TD")
        assert notice_for("ja") in ja_text
        ja.close()
        del i18n
    finally:
        set_language("zh-Hans", notify=False)
        app.processEvents()

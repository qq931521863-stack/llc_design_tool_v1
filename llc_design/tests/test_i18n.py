"""Message-catalogue and language-switching regression tests.

The strongest test here is `test_every_bound_shell_string_is_translated`: it
builds the five translated workspaces, harvests every string the UI binds for
translation, and requires a translation for each one.  Adding a new toolbar or
tab to any workspace therefore fails this test until the catalogues are updated.
"""
from __future__ import annotations

import os
import re
from contextlib import contextmanager

import pytest

_CJK = re.compile(r"[\u4e00-\u9fff]")
_WORKSPACE_WINDOWS = (
    "llc_window", "pfc_window", "control_window", "fra_window", "dab_window",
    "power_stage_window",
)


def _qapp(qt_widgets):
    return qt_widgets.QApplication.instance() or qt_widgets.QApplication([])


def _destroy_owned_windows(app, windows):
    """Release only these owners, including after a translation assertion fails.

    close() alone intentionally preserves workspaces for reopening. Tests must
    also drain their workers and deferred native deletion, without deleting
    unrelated windows or detaching PlotWidget objects from Qt's owner tree.
    """
    from llc_design.gui.lifetime import dispose_widgets

    dispose_widgets(windows)


@contextmanager
def _controller(qt_widgets):
    from llc_design.core.spec import LLCDesignSpec
    from llc_design.gui.launcher import WorkspaceApplicationController
    from llc_design.i18n import set_language

    app = _qapp(qt_widgets)
    previous = set_language("zh-Hans", notify=False)
    windows = ()
    try:
        controller = WorkspaceApplicationController(LLCDesignSpec())
        windows = tuple(getattr(controller, name) for name in _WORKSPACE_WINDOWS)
        app.processEvents()
        yield app, controller
    finally:
        try:
            _destroy_owned_windows(app, windows)
        finally:
            set_language(previous, notify=False)


@pytest.fixture
def workspace_controller(desktop_qapp):
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    with _controller(qt_widgets) as owned:
        yield owned


@pytest.fixture
def owned_widgets(desktop_qapp):
    windows = []
    try:
        yield windows
    finally:
        _destroy_owned_windows(desktop_qapp, windows)


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


def test_every_bound_shell_string_is_translated(workspace_controller):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtGui import QAction
    from PySide6.QtWidgets import QTabWidget, QWidget

    from llc_design.gui.i18n_ui import bind_window
    from llc_design.i18n import catalogue

    _, controller = workspace_controller
    sources: set[str] = set()
    for window in (controller.llc_window, controller.pfc_window, controller.control_window, controller.fra_window, controller.dab_window):
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


@pytest.mark.parametrize("code", ["zh-Hans", "en", "ja", "ko"])
def test_launcher_entry_text_is_translated(code, owned_widgets):
    from PySide6.QtWidgets import QLabel, QPushButton

    from llc_design.gui.launcher import WorkspaceSelectionDialog
    from llc_design.i18n import catalogue, set_language, t

    sources = (
        "电源设计工具箱",
        "选择功率级、专用拓扑或控制分析工作区；切换工作区时保留当前设计。",
        "功率级设计 / Power Stage Designer",
        ("Buck 降压 · Boost 升压 · Buck-Boost 升降压 · 全桥 / 移相全桥 · 更多拓扑\n"
         "功率级参数、原理图与电压电流波形、器件应力、模拟环路及工程工具箱"),
        "进入功率级设计",
        "开始系统建模",
        "工作区选择与使用说明",
    )
    previous = set_language(code, notify=False)
    try:
        dialog = WorkspaceSelectionDialog()
        owned_widgets.append(dialog)
        widgets = dialog.findChildren(QLabel) + dialog.findChildren(QPushButton)
        visible_text = [value for widget in widgets
                        for value in (widget.text(), widget.toolTip())]
        for source in sources:
            if code != "zh-Hans":
                assert source in catalogue(code)
                assert t(source) != source
            assert any(t(source) in value for value in visible_text), (code, source)
        assert dialog.findChild(QPushButton, "power_stage_designer_button").text() == t("进入功率级设计")
    finally:
        set_language(previous, notify=False)


def test_switching_language_retranslates_and_restores(workspace_controller):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QToolBar, QToolButton

    from llc_design.gui.i18n_ui import apply_language
    from llc_design.i18n import set_language

    app, controller = workspace_controller
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


def test_new_feature_buttons_retranslate_and_restore(workspace_controller):
    from PySide6.QtWidgets import QPushButton

    from llc_design.gui.i18n_ui import apply_language
    from llc_design.i18n import catalogue

    app, controller = workspace_controller
    sources = (
        "FHA 波形", "HB 波形", "TD 波形", "复制 Auto → User", "取消计算",
        "双漏感参考例（非实测）", "可达参考例（非实测）",
        "主目录材料 / 原式 / 冲突…", "其他 PDF 资料 / 案例…",
        "用户模型声明…", "计算零偏磁 / 公式参考值",
        "保存磁件工程", "打开磁件工程", "磁件报告",
    )
    buttons = {source: [] for source in sources}
    for window in (controller.llc_window, controller.pfc_window):
        for button in window.findChildren(QPushButton):
            source = button.property("i18nSourceText")
            if source in buttons:
                buttons[source].append(button)
    assert all(buttons.values()), [source for source, found in buttons.items() if not found]
    assert all(button.text() == source for source, found in buttons.items() for button in found)

    try:
        # Exercise real Qt bindings, restoration and a repeated switch cycle.
        for code in ("en", "ja", "ko", "zh-Hans", "en", "zh-Hans"):
            apply_language(code, app)
            app.processEvents()
            for source, found in buttons.items():
                expected = source if code == "zh-Hans" else catalogue(code)[source]
                if code != "zh-Hans":
                    assert expected != source
                for button in found:
                    assert button.text() == expected, (code, source)
                    assert button.property("i18nSourceText") == source
    finally:
        apply_language("zh-Hans", app)


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


def test_language_menu_offers_all_four_and_marks_the_active_one(workspace_controller):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    from llc_design.gui.i18n_ui import apply_language
    from llc_design.i18n import LANGUAGES, current_language, set_language

    app, controller = workspace_controller
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
    try:
        assert apply_matplotlib_fonts("ja")
        from matplotlib import rcParams

        assert rcParams["font.sans-serif"][0] == "Meiryo"
    finally:
        apply_matplotlib_fonts("zh-Hans")


def test_help_bodies_fall_back_with_a_visible_notice(monkeypatch, owned_widgets):
    """All bodies are translated today, so the fallback path is exercised by
    removing one entry rather than by relying on an unfinished translation."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    import llc_design.gui.help as help_module
    from llc_design.gui.help import HelpDialog, HelpSection, help_topic, section_body
    from llc_design.i18n import catalogue, set_language

    app = _qapp(qt_widgets)
    try:
        set_language("en", notify=False)
        target = next(s for s in help_topic("fra").sections if s.key == "fra.ts_semantics")
        body, translated = section_body(target)
        assert translated and "Complete Loop TS" in body

        orphan = HelpSection("未翻译的章节", "原始正文内容。", key="does.not.exist")
        body, translated = section_body(orphan)
        assert not translated and body == "原始正文内容。"

        original = help_module.catalogue
        monkeypatch.setattr(
            help_module, "catalogue",
            lambda code=None: {k: v for k, v in original(code).items()
                               if k != "helpbody.fra.ts_semantics"},
        )
        dialog = HelpDialog(None, "fra", "测量语义：TS 类型不能猜")
        owned_widgets.append(dialog)
        rendered = dialog.browser.toPlainText()
        assert catalogue("en")["此章节暂无当前语言的正文译文，以下显示原文。"] in rendered
        assert "Plant TS" in rendered
        dialog.close()

        set_language("zh-Hans", notify=False)
        zh = HelpDialog(None, "fra", "测量语义：TS 类型不能猜")
        owned_widgets.append(zh)
        assert "此章节暂无当前语言的正文译文，以下显示原文。" not in zh.browser.toPlainText()
        zh.close()
    finally:
        set_language("zh-Hans", notify=False)
        app.processEvents()

def test_starting_in_a_translated_language_still_allows_switching_back(workspace_controller):
    """Regression: the binding records the text it sees, so the windows must be
    built while the source language is active, not after the stored language has
    already been applied."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QToolBar

    from llc_design.gui.i18n_ui import apply_language
    from llc_design.i18n import set_language

    app, controller = workspace_controller
    window = controller.llc_window
    try:
        # Simulate a start-up with a persisted non-source language.
        apply_language("en", app)
        assert window.windowTitle().startswith("Power Design Toolkit")
        assert "Load JSON" in [a.text() for bar in window.findChildren(QToolBar)
                               for a in bar.actions() if a.text()]

        # The source must still be Chinese, so switching back must work.
        apply_language("zh-Hans", app)
        assert window.windowTitle().startswith("电源设计工具箱")
        assert "加载 JSON" in [a.text() for bar in window.findChildren(QToolBar)
                              for a in bar.actions() if a.text()]
    finally:
        apply_language("zh-Hans", app)
        set_language("zh-Hans", notify=False)

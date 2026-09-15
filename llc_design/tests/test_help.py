"""In-application help regression tests.

The help content is user-facing documentation, so it is tested like data: every
workspace topic must exist, every section must carry real text, the toolbar
button and F1 shortcut must be installed on all workspaces, and the document
links must have a working fallback for packaged builds that do not ship the
Markdown sources.
"""
from __future__ import annotations

import os

import pytest


def _qapp(qt_widgets):
    return qt_widgets.QApplication.instance() or qt_widgets.QApplication([])


def test_every_help_topic_has_real_content():
    from llc_design.gui.help import HELP_TOPICS, help_topic

    assert set(HELP_TOPICS) == {"selector", "llc", "pfc", "control", "fra"}
    for key in HELP_TOPICS:
        topic = help_topic(key)
        assert topic.heading and topic.intro
        assert len(topic.sections) >= 4, key
        titles = [section.title for section in topic.sections]
        assert len(titles) == len(set(titles)), f"duplicate section titles in {key}"
        # Shortcuts and About are appended to every topic.
        assert "快捷键与操作" in titles
        assert "关于" in titles
        for section in topic.sections:
            assert len(section.body.strip()) >= 40, f"{key}/{section.title} is too thin"


def test_unknown_help_topic_is_rejected():
    from llc_design.gui.help import help_topic

    with pytest.raises(KeyError):
        help_topic("not-a-workspace")


def test_fra_help_documents_the_step_gates_and_assumptions():
    from llc_design.gui.help import help_topic

    body = "\n".join(section.body for section in help_topic("fra").sections)
    for status in (
        "WITHHELD_LOW_FIT_CONFIDENCE",
        "WITHHELD_PLANT_MODEL_NOT_STABLE",
        "WITHHELD_LOOP_MARGIN_FAIL",
        "WITHHELD_CLOSED_LOOP_UNSTABLE",
        "WITHHELD_INSUFFICIENT_STEP_BANDWIDTH",
    ):
        assert status in body, status
    # The documented non-guarantees must stay visible next to the feature.
    assert "不是与拓扑无关的闭环稳定性证明" in body
    assert "不能当作数学上界" in body
    assert "不是 Vector Fitting" in body


def test_control_help_lists_the_whole_controller_catalogue():
    from llc_design.gui.help import help_topic
    from power_control_tools.controllers import CONTROLLER_LABELS

    body = "\n".join(section.body for section in help_topic("control").sections)
    for kind, label in CONTROLLER_LABELS.items():
        stem = label.split(" — ")[0].split("·")[0].strip()
        assert stem in body, f"{kind} ({stem}) missing from Control Tools help"


def test_document_targets_prefer_local_files_and_fall_back_to_github():
    from llc_design import __version__
    from llc_design.gui.help import HELP_TOPICS, document_target

    for key, topic in HELP_TOPICS.items():
        for label, relative in topic.docs:
            local, url = document_target(relative)
            assert label
            assert local.name == relative.split("/")[-1]
            assert f"/blob/v{__version__}/{relative}" in url.toString()


def test_help_dialog_renders_every_section_and_can_open_one_directly():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    from llc_design.gui.help import HELP_TOPICS, HelpDialog, help_topic

    app = _qapp(qt_widgets)
    for key in HELP_TOPICS:
        dialog = HelpDialog(None, key)
        assert dialog.section_list.count() == len(help_topic(key).sections)
        for row in range(dialog.section_list.count()):
            dialog.section_list.setCurrentRow(row)
            assert dialog.browser.toHtml().strip()
        dialog.close()
    targeted = HelpDialog(None, "llc", "模型边界与已知限制")
    assert targeted.section_list.currentItem().text() == "模型边界与已知限制"
    assert "FHA" in targeted.browser.toPlainText()
    targeted.close()
    app.processEvents()


def test_every_workspace_installs_a_help_button_and_f1_shortcut():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    from PySide6.QtWidgets import QToolButton

    from llc_design.core.spec import LLCDesignSpec
    from llc_design.gui.launcher import WorkspaceApplicationController, WorkspaceSelectionDialog

    app = _qapp(qt_widgets)
    controller = WorkspaceApplicationController(LLCDesignSpec())
    windows = {
        "llc": controller.llc_window,
        "pfc": controller.pfc_window,
        "control": controller.control_window,
        "fra": controller.fra_window,
    }
    for name, window in windows.items():
        buttons = [b for b in window.findChildren(QToolButton) if b.text() == "帮助"]
        assert len(buttons) == 1, name
        assert buttons[0].menu() is not None and buttons[0].menu().actions(), name
        shortcuts = [a.shortcut().toString() for a in window.actions() if a.text() == "帮助"]
        assert "F1" in shortcuts, name

    dialog = WorkspaceSelectionDialog()
    texts = [b.text() for b in dialog.findChildren(qt_widgets.QPushButton)]
    assert any("帮助" in text and "F1" in text for text in texts)
    dialog.close()
    for window in windows.values():
        window.close()
    app.processEvents()


def test_contact_details_are_present_in_help_and_match_the_readme():
    from pathlib import Path

    from llc_design.gui.help import CONTACT_BLOG, CONTACT_EMAIL, CONTACT_QR_FILE, CONTACT_WECHAT, help_topic

    assert CONTACT_EMAIL == "maileyang@qq.com"
    assert CONTACT_WECHAT == "maileyang"
    assert CONTACT_BLOG == "开关电源仿真与实用设计"

    # Every workspace help must expose the contact block, including the
    # "just send me an email" invitation that makes it actionable.
    for key in ("selector", "llc", "pfc", "control", "fra"):
        body = "\n".join(section.body for section in help_topic(key).sections)
        assert CONTACT_EMAIL in body, key
        assert CONTACT_WECHAT in body, key
        assert CONTACT_BLOG in body, key
        assert "直接发邮件" in body, key
        titles = [section.title for section in help_topic(key).sections]
        assert "联系方式与支持" in titles, key

    # The QR code must exist in the package (and be declared as package data)
    # or the packaged build would show help without it.
    assert CONTACT_QR_FILE.exists(), CONTACT_QR_FILE
    raw = CONTACT_QR_FILE.read_bytes()
    assert raw[:3] == b"\xff\xd8\xff", "contact QR is not a JPEG"
    assert len(raw) > 2000

    pyproject = (Path(__file__).resolve().parents[2] / "pyproject.toml").read_text(encoding="utf-8")
    assert '"llc_design.data" = ["*.json", "*.jpg"]' in pyproject

    readme = (Path(__file__).resolve().parents[2] / "README.md").read_text(encoding="utf-8")
    assert CONTACT_EMAIL in readme and CONTACT_WECHAT in readme and CONTACT_BLOG in readme
    assert "wechat_official_account.jpg" in readme


def test_help_dialog_embeds_the_contact_qr_code():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    from llc_design.gui.help import CONTACT_QR_FILE, HelpDialog

    app = _qapp(qt_widgets)
    dialog = HelpDialog(None, "llc", "联系方式与支持")
    html = dialog.browser.toHtml()
    assert CONTACT_QR_FILE.as_uri() in html
    assert "mailto:maileyang@qq.com" in html
    dialog.close()
    app.processEvents()


def test_help_rendering_converts_markup_and_leaves_no_stray_asterisks():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    from llc_design.gui.help import HELP_TOPICS, HelpDialog

    app = _qapp(qt_widgets)
    for key in HELP_TOPICS:
        dialog = HelpDialog(None, key)
        for row in range(dialog.section_list.count()):
            dialog.section_list.setCurrentRow(row)
            rendered = dialog.browser.toPlainText()
            assert "**" not in rendered, f"{key}/{dialog.section_list.item(row).text()}"
        dialog.close()
    app.processEvents()


def test_help_contains_implementation_notes_for_every_workspace():
    """The algorithm/coefficient notes are the part users asked to keep."""
    from llc_design.gui.help import help_topic

    required = {
        "llc": ("实现说明 — FHA / HB / TD", "实现说明 — 数字环", "Thiran"),
        "pfc": ("实现说明 — 采样链与延迟拆分", "不重复计数", "跨 PWM 周期连续积分"),
        "control": ("实现说明 — 系数约定与离散化", "实现说明 — C99 导出结构", "DF2T"),
        "fra": ("实现说明 — 频响求值与裕度判据", "实现说明 — 剥离、重建与辨识算法", "实现说明 — Auto Design"),
    }
    for key, needles in required.items():
        sections = help_topic(key).sections
        haystack = "\n".join([section.title for section in sections] + [section.body for section in sections])
        for needle in needles:
            assert needle in haystack, f"{key}: {needle}"
        assert any(section.title.startswith("实现说明") for section in sections), key

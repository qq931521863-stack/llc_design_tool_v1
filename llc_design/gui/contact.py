"""Prominent bilingual author/contact card shared by desktop workspaces."""

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QToolButton,
)

from . import theme
from .help import CONTACT_AUTHOR, CONTACT_AUTHOR_EN, CONTACT_EMAIL, CONTACT_WECHAT


def create_contact_card(parent=None):
    palette = theme.active_theme()
    card = QFrame(parent)
    card.setObjectName("author_contact_card")
    card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
    card.setStyleSheet(
        f"QFrame#author_contact_card {{ background:{palette.surface}; "
        f"border:2px solid {palette.accent}; border-radius:8px; }}"
    )
    row = QHBoxLayout(card)
    row.setContentsMargins(14, 7, 14, 7)
    row.setSpacing(24)
    author = QLabel(f"作者：{CONTACT_AUTHOR}\nAuthor: {CONTACT_AUTHOR_EN}")
    author.setObjectName("contact_author")
    author.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    author.setStyleSheet(
        f"color:{palette.accent}; font-size:15px; font-weight:700; border:0;"
    )
    row.addWidget(author)
    email = QLabel(
        f'邮箱 / Email<br><a href="mailto:{CONTACT_EMAIL}" '
        f'style="color:{palette.text_strong}; font-size:15px; font-weight:600;">{CONTACT_EMAIL}</a>'
    )
    email.setObjectName("contact_email")
    email.setOpenExternalLinks(True)
    email.setTextInteractionFlags(
        Qt.TextInteractionFlag.TextBrowserInteraction
        | Qt.TextInteractionFlag.TextSelectableByMouse
    )
    email.setStyleSheet(f"color:{palette.text}; font-size:13px; border:0;")
    row.addWidget(email)
    wechat = QToolButton()
    wechat.setObjectName("contact_wechat_copy")
    wechat.setProperty("i18nSkip", True)
    label = f"微信 / WeChat: {CONTACT_WECHAT}\n复制 / Copy"
    wechat.setText(label)
    wechat.setToolTip("点击复制微信号 / Click to copy WeChat ID")
    wechat.setCursor(Qt.CursorShape.PointingHandCursor)
    wechat.setStyleSheet(
        f"QToolButton {{ color:{palette.text_strong}; background:{palette.hover}; "
        f"border:1px solid {palette.accent}; border-radius:5px; padding:5px 12px; "
        "font-size:14px; font-weight:600; }"
    )
    timer = QTimer(wechat)
    timer.setSingleShot(True)
    timer.timeout.connect(lambda: wechat.setText(label))

    def copy_wechat():
        QApplication.clipboard().setText(CONTACT_WECHAT)
        wechat.setText(f"微信 / WeChat: {CONTACT_WECHAT}\n已复制 / Copied")
        timer.start(1800)

    wechat.clicked.connect(copy_wechat)
    row.addWidget(wechat)
    row.addStretch(1)
    return card


def install_contact_bar(window):
    if hasattr(window, "author_contact_bar"):
        return window.author_contact_bar
    window.addToolBarBreak(Qt.ToolBarArea.TopToolBarArea)
    bar = window.addToolBar("Author / Contact")
    bar.setObjectName("author_contact_toolbar")
    bar.setMovable(False)
    bar.setFloatable(False)
    bar.addWidget(create_contact_card(bar))
    window.author_contact_bar = bar
    return bar

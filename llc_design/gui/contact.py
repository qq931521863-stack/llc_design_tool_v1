"""Compact author/contact details in the existing toolbar row."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QToolButton,
    QWidget,
)

from . import theme
from .help import CONTACT_AUTHOR, CONTACT_AUTHOR_EN, CONTACT_EMAIL, CONTACT_WECHAT
from .i18n_ui import bind_text


def add_toolbar_contacts(toolbar):
    palette = theme.active_theme()
    strip = QWidget(toolbar)
    strip.setObjectName("author_contact_strip")
    strip.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
    strip.setStyleSheet("background:transparent; border:0;")
    row = QHBoxLayout(strip)
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(8)
    row.addStretch(1)
    author = QLabel(f"作者：{CONTACT_AUTHOR}")
    author.setObjectName("contact_author")
    author.setToolTip(f"{CONTACT_AUTHOR} / {CONTACT_AUTHOR_EN}")
    author.setStyleSheet(f"color:{palette.text_muted}; font-size:12px;")
    bind_text(author, f"作者：{CONTACT_AUTHOR}")
    row.addWidget(author)
    email = QLabel(
        f'<a href="mailto:{CONTACT_EMAIL}" style="color:{palette.text_muted};'
        f'text-decoration:none;font-size:12px;">{CONTACT_EMAIL}</a>'
    )
    email.setObjectName("contact_email")
    email.setOpenExternalLinks(True)
    email.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
    row.addWidget(email)
    wechat = QToolButton()
    wechat.setObjectName("contact_wechat_copy")
    wechat.setText(f"微信: {CONTACT_WECHAT}")
    wechat.setToolTip("点击复制微信号 / Click to copy WeChat ID")
    wechat.setAutoRaise(True)
    wechat.setCursor(Qt.CursorShape.PointingHandCursor)
    wechat.setStyleSheet(
        f"color:{palette.text_muted};font-size:12px;padding:0 2px;"
        "background:transparent;border:0;"
    )
    wechat.clicked.connect(lambda: QApplication.clipboard().setText(CONTACT_WECHAT))
    row.addWidget(wechat)
    toolbar.addWidget(strip)
    return strip

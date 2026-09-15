"""Qt integration for the message catalogue.

Why the binding is automatic
---------------------------

The UI is written with Chinese literals in the constructor.  Instead of
replacing every literal with ``t("...")`` *and* keeping a second list for
retranslation, the window is scanned **once after it is built**: whatever text a
widget currently holds *is* the source string, so it is stored as a Qt dynamic
property and re-applied as ``t(source)`` whenever the language changes.

Only widget types whose text is static are bound automatically (actions,
buttons, check boxes, group boxes, tab labels, dock titles and window titles).
Free-standing ``QLabel`` widgets are *not* bound by default because many of them
carry live status text — rebinding those would reset a status message on a
language switch.  Static labels opt in through :func:`bind_text`.

English strings stay English: ``t()`` returns the source unchanged when the
catalogue has no entry, which is exactly the documented policy for the project's
existing English wording.
"""

from __future__ import annotations

import weakref

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDockWidget,
    QGroupBox,
    QMainWindow,
    QMenu,
    QPushButton,
    QRadioButton,
    QTabWidget,
    QToolBar,
    QToolButton,
    QWidget,
)

from llc_design import __version__
from llc_design.i18n import LANGUAGES, current_language, set_language, t
from llc_design.i18n.fonts import apply_matplotlib_fonts, apply_qt_font

_SOURCE_PROPERTY = "i18nSourceText"
_TAB_SOURCES_PROPERTY = "i18nTabSources"
_SETTINGS_ORGANISATION = "PowerDesignToolkit"
_SETTINGS_APPLICATION = "PowerDesignToolkit"
_SETTINGS_KEY = "ui/language"

# Widget classes whose text is static for the lifetime of the window.
_BOUND_WIDGETS = (QToolButton, QPushButton, QCheckBox, QRadioButton, QGroupBox, QDockWidget)
# Widgets that own their own text setter rather than setText().
_ACTION_CLASSES = (QAction,)

_windows: list[weakref.ref] = []
_menu_buttons: list[weakref.ref] = []


# --------------------------------------------------------------------- settings
def load_language() -> str:
    from PySide6.QtCore import QSettings

    settings = QSettings(_SETTINGS_ORGANISATION, _SETTINGS_APPLICATION)
    code = str(settings.value(_SETTINGS_KEY, current_language()) or current_language())
    codes = {item.code for item in LANGUAGES}
    return code if code in codes else "zh-Hans"


def save_language(code: str) -> None:
    from PySide6.QtCore import QSettings

    QSettings(_SETTINGS_ORGANISATION, _SETTINGS_APPLICATION).setValue(_SETTINGS_KEY, code)


def apply_language(code: str, app=None) -> str:
    """Activate ``code``, persist it, and refresh fonts and every open window."""
    set_language(code, notify=False)
    save_language(code)
    target = app or QApplication.instance()
    if target is not None:
        apply_qt_font(target, code)
    apply_matplotlib_fonts(code)
    retranslate_all()
    return code


# ---------------------------------------------------------------------- binding
def _is_skipped(target) -> bool:
    try:
        return bool(target.property("i18nSkip"))
    except Exception:  # pragma: no cover - defensive
        return False


def bind_text(target, source: str | None = None) -> None:
    """Remember ``source`` (default: the current text) for later retranslation.

    Binding is idempotent: an object that already carries a source is left
    alone, so the scan can be repeated (it must never capture an already
    translated string as the new source).
    """
    if _is_skipped(target) or target.property(_SOURCE_PROPERTY) is not None:
        return
    text = source if source is not None else _current_text(target)
    if text is None:
        return
    target.setProperty(_SOURCE_PROPERTY, text)
    _apply_to(target, text)


def _current_text(target) -> str | None:
    if isinstance(target, QAction):
        return target.text()
    if hasattr(target, "text") and callable(target.text):
        try:
            return target.text()
        except Exception:  # pragma: no cover - defensive
            return None
    return None


def _apply_to(target, source: str) -> None:
    translated = t(source)
    if isinstance(target, QAction):
        if target.text() != translated:
            target.setText(translated)
        return
    if isinstance(target, QMainWindow):
        if target.windowTitle() != translated:
            target.setWindowTitle(translated)
        return
    if isinstance(target, QDockWidget):
        if target.windowTitle() != translated:
            target.setWindowTitle(translated)
        return
    if hasattr(target, "setText") and target.text() != translated:
        target.setText(translated)


def _bind_tabs(window) -> None:
    for tabs in window.findChildren(QTabWidget):
        existing = tabs.property(_TAB_SOURCES_PROPERTY)
        if existing:
            continue
        sources = [tabs.tabText(index) for index in range(tabs.count())]
        tabs.setProperty(_TAB_SOURCES_PROPERTY, sources)


def _retranslate_tabs(window) -> None:
    for tabs in window.findChildren(QTabWidget):
        sources = tabs.property(_TAB_SOURCES_PROPERTY)
        if not sources:
            continue
        for index, source in enumerate(sources):
            if index < tabs.count():
                tabs.setTabText(index, t(str(source)))


def bind_window(window) -> None:
    """Scan a freshly built window and remember all static, translatable text."""
    title = window.windowTitle()
    if title:
        window.setProperty(_SOURCE_PROPERTY, title)
    for widget in window.findChildren(QWidget):
        if isinstance(widget, _BOUND_WIDGETS) and not _is_skipped(widget):
            bind_text(widget)
    for action in window.findChildren(QAction):
        if isinstance(action, _ACTION_CLASSES) and not _is_skipped(action):
            bind_text(action)
    for menu in window.findChildren(QMenu):
        title = menu.title()
        if title:
            menu.setProperty(_SOURCE_PROPERTY, title)
    _bind_tabs(window)
    register_window(window)


def retranslate_window(window) -> None:
    source = window.property(_SOURCE_PROPERTY)
    if source:
        window.setWindowTitle(t(str(source)))
    for widget in window.findChildren(QWidget):
        stored = widget.property(_SOURCE_PROPERTY)
        if stored:
            _apply_to(widget, str(stored))
    for action in window.findChildren(QAction):
        stored = action.property(_SOURCE_PROPERTY)
        if stored:
            _apply_to(action, str(stored))
    for menu in window.findChildren(QMenu):
        stored = menu.property(_SOURCE_PROPERTY)
        if stored:
            menu.setTitle(t(str(stored)))
    _retranslate_tabs(window)
    _refresh_language_buttons()


def register_window(window) -> None:
    if not any(ref() is window for ref in _windows):
        _windows.append(weakref.ref(window))


def retranslate_all() -> None:
    alive: list[weakref.ref] = []
    for ref in _windows:
        window = ref()
        if window is None:
            continue
        alive.append(ref)
        retranslate_window(window)
    _windows[:] = alive


# ------------------------------------------------------------------ language UI
def install_language_menu(window) -> QToolButton:
    """Append a language selector to the window's last toolbar."""
    button = QToolButton(window)
    button.setObjectName("languageSelector")
    button.setProperty("i18nSkip", True)
    button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
    button.setText(t("语言 / Language"))
    button.setToolTip(t("界面语言（切换后立即生效）"))

    menu = QMenu(button)
    codes = [item.code for item in LANGUAGES]
    for item in LANGUAGES:
        action = QAction(f"{item.native_name}  ·  {item.english_name}", button)
        action.setProperty("i18nSkip", True)
        action.setCheckable(True)
        action.setChecked(item.code == current_language())
        action.setData(item.code)
        action.triggered.connect(lambda checked=False, code=item.code: apply_language(code))
        menu.addAction(action)
    # Keep the check marks honest without fixing the button's own text twice.
    menu.setProperty("languageCodes", codes)
    button.setMenu(menu)
    button.setProperty("languageMenu", menu)

    bars = window.findChildren(QToolBar)
    if bars:
        bars[-1].addWidget(button)
    _menu_buttons.append(weakref.ref(button))
    button.setProperty("languageButtonSource", "语言 / Language")
    button.setProperty("languageMenuSource", "界面语言（切换后立即生效）")
    return button


def _refresh_language_buttons() -> None:
    alive: list[weakref.ref] = []
    for ref in _menu_buttons:
        button = ref()
        if button is None:
            continue
        alive.append(ref)
        button.setText(t(str(button.property("languageButtonSource") or "语言 / Language")))
        button.setToolTip(t(str(button.property("languageMenuSource") or "界面语言（切换后立即生效）")))
        menu = button.property("languageMenu")
        if menu is not None:
            for action in menu.actions():
                action.setChecked(action.data() == current_language())
    _menu_buttons[:] = alive


def install_language_selector(window) -> QToolButton:
    """Install the selector and bind the window's translatable text.

    The button itself only needs the toolbar, which exists when this is called.
    The *binding* scan is repeated once on the next event-loop turn, because most
    windows build their toolbars before their page/tab widgets; scanning only
    now would miss every tab title.
    """
    from PySide6.QtCore import QTimer

    bind_window(window)
    QTimer.singleShot(0, lambda: bind_window(window))
    return install_language_menu(window)


ABOUT_SOURCE = """电源设计工具箱 Power Design Toolkit V{version}
工具设计人：杨帅锅
邮箱  maileyang@qq.com
微信  maileyang
公众号 / 技术博客  开关电源仿真与实用设计

LLC / PFC / Vienna 设计、数字控制工具与 FRA 环路设计。
许可证：GNU GPL v3。
"""


def about_text() -> str:
    """Translated About body, shared by every workspace's 关于 dialog."""
    from llc_design.i18n import catalogue

    body = catalogue().get("helpbody.common.about") or ABOUT_SOURCE
    return body.format(version=__version__)


__all__ = [
    "ABOUT_SOURCE",
    "about_text",
    "apply_language",
    "bind_text",
    "bind_window",
    "install_language_menu",
    "install_language_selector",
    "load_language",
    "register_window",
    "retranslate_all",
    "retranslate_window",
    "save_language",
]

"""End test-owned desktop lifetimes across every workspace test directory."""
from __future__ import annotations

import importlib.util
import sys

import pytest

_WORKSPACE_PACKAGES = (
    "llc_design.", "pfc_design.", "dab_design.", "power_stage_design.",
    "power_control_tools.", "power_sim.",
)


def _desktop_owners():
    # Headless numerical suites must not acquire a Qt dependency or application.
    qt = sys.modules.get("PySide6.QtWidgets")
    app = qt.QApplication.instance() if qt is not None else None
    if app is None:
        return ()
    return tuple(widget for widget in app.topLevelWidgets()
                 if widget.parent() is None and (
                     isinstance(widget, (qt.QMainWindow, qt.QDialog))
                     or type(widget).__module__.startswith(_WORKSPACE_PACKAGES)))


@pytest.fixture(scope="session", autouse=True)
def desktop_update_boundary():
    """Keep every host workflow offline before any scoped fixture constructs it.

    Numerical-only installations do not need Qt. When Qt is installed, importing
    the updater creates no QApplication or workspaces. Hosts imported later bind
    the same fake, so coverage does not depend on test names or collection order.
    """
    if importlib.util.find_spec("PySide6") is None:
        yield None
        return
    from llc_design.tests.desktop_support import offline_update_checks

    with offline_update_checks() as real_check:
        yield real_check


@pytest.fixture(autouse=True)
def dispose_test_owned_desktop_widgets():
    existing = set(_desktop_owners())
    try:
        yield
    finally:
        owners = [owner for owner in _desktop_owners() if owner not in existing]
        if owners:
            from llc_design.gui.lifetime import dispose_widgets
            # Internal pyqtgraph menus/viewports are not independent owners.
            # Production owner disposal also releases their auxiliary trees.
            dispose_widgets(owners)

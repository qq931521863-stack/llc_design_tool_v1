"""The production PFC scroll container keeps laptop navigation reachable."""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6.QtWidgets")
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QScrollArea


def test_actual_pfc_window_keeps_720p_size_and_vienna_controls_reachable():
    from pfc_design.gui.main_window import PFCMainWindow
    app = QApplication.instance() or QApplication([])
    window = PFCMainWindow()
    window.resize(1280, 720)
    window.show()
    app.processEvents()
    scroll = window.workspace_scroll
    assert window.size().toTuple() == (1280, 720)
    assert scroll.viewport().width() < scroll.widget().width()
    assert scroll.viewport().height() < scroll.widget().height()
    bar = window.subtabs.tabBar()
    QTest.mouseClick(bar, Qt.MouseButton.LeftButton, pos=bar.tabRect(1).center())
    app.processEvents()
    view = window.vienna_view
    assert window.subtabs.currentWidget() is view
    for button in (view.run_button, view.fullscreen_diagram_button,
                   view.fit_diagram_button, view.diagram_toggle, view.inspector_toggle):
        scroll.ensureWidgetVisible(button)
        app.processEvents()
        assert scroll.viewport().rect().contains(button.mapTo(scroll.viewport(), button.rect().center()))
        assert window.size().toTuple() == (1280, 720)
    for index in range(view.input_tabs.count()):
        view.input_tabs.setCurrentIndex(index)
        scroll.ensureWidgetVisible(view.input_tabs.tabBar())
        app.processEvents()
        assert view.input_tabs.currentWidget().isVisible()
    # The inspector has its own scroll boundary; reaching the last parameter
    # must not rely on stretching the real application window above 720 px.
    inspectors = [area for area in view.findChildren(QScrollArea)
                  if area.widget() is not None and area.verticalScrollBar().maximum() > 0]
    assert inspectors
    for area in inspectors:
        area.verticalScrollBar().setValue(area.verticalScrollBar().maximum())
        app.processEvents()
        assert area.verticalScrollBar().value() == area.verticalScrollBar().maximum()
    scroll.verticalScrollBar().setValue(scroll.verticalScrollBar().maximum())
    app.processEvents()
    assert window.size().toTuple() == (1280, 720)
    directory = os.environ.get("CONTROL_DIAGRAM_EVIDENCE_DIR")
    if directory:
        from pathlib import Path
        assert window.grab().save(str(Path(directory)/"production-Vienna-bottom-1280x720.png"))
    window.close()

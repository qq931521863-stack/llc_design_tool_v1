"""Real transient fit dialogs and diagram signals have explicit native owners."""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6.QtWidgets")
from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from shiboken6 import isValid

from llc_design.gui.lifetime import dispose_widgets
from llc_design.gui.widgets.control_block_diagram import BlockSpec, ControlBlockDiagram
from llc_design.gui.widgets.impedance_fit_view import ImpedanceFitDialog
from pfc_design.gui.inductor_design import PFCInductorDesignResultView


def _flush(app):
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()


def test_impedance_button_reuses_live_dialog_and_discards_closed_native_tree():
    app = QApplication.instance() or QApplication([])
    owner = PFCInductorDesignResultView("Ownership probe")
    owner.show()
    retained = []
    try:
        for _ in range(3):
            QTest.mouseClick(owner.impedance_fit_button, Qt.MouseButton.LeftButton)
            dialog = owner.impedance_dialog
            assert dialog.isVisible()
            QTest.mouseClick(owner.impedance_fit_button, Qt.MouseButton.LeftButton)
            assert owner.impedance_dialog is dialog
            retained.extend([dialog, dialog.canvas])
            assert dialog.close()
            assert owner.impedance_dialog is None
            _flush(app)
            assert all(not isValid(obj) for obj in retained)
            assert not owner.findChildren(ImpedanceFitDialog)
        # Reopen before the old DeferredDelete: its late destroyed signal must
        # not clear the new live dialog reference.
        owner.open_impedance_fit()
        old = owner.impedance_dialog
        old.close()
        owner.open_impedance_fit()
        current = owner.impedance_dialog
        assert current is not old
        _flush(app)
        assert not isValid(old) and isValid(current)
        assert owner.impedance_dialog is current
        retained.extend([current, current.canvas])
    finally:
        dispose_widgets([owner])
    assert all(not isValid(obj) for obj in retained)


def test_control_diagram_proxy_follows_native_owner_and_keeps_click_binding():
    app = QApplication.instance() or QApplication([])
    diagram = ControlBlockDiagram()
    diagram.set_diagram([BlockSpec("plant", "Plant")], [])
    diagram.resize(500, 250)
    diagram.show()
    app.processEvents()
    selected = []
    diagram.block_selected.connect(selected.append)
    item = diagram._items["plant"]
    point = diagram.view.mapFromScene(item.sceneBoundingRect().center())
    QTest.mouseClick(diagram.view.viewport(), Qt.MouseButton.LeftButton, pos=point)
    assert selected == ["plant"] and diagram.selected_key == "plant"
    proxy, scene = diagram._proxy, diagram.scene
    dispose_widgets([diagram])
    assert not isValid(diagram) and not isValid(scene) and not isValid(proxy)

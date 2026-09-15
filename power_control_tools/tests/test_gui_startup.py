import os

import numpy as np
import pytest


def _app(qt_widgets):
    return qt_widgets.QApplication.instance() or qt_widgets.QApplication([])


def test_control_tools_window_initializes_without_early_tab_signal():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    from power_control_tools.gui.main_window import ControlToolsMainWindow

    app = _app(qt_widgets)
    window = ControlToolsMainWindow()

    assert window.filter_impl.currentText() == "IIR"
    assert window.centralWidget() is not None

    window.close()
    app.processEvents()


def test_fra_loop_designer_window_initializes():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    from power_control_tools.fra.models import FRAMeasurementKind, FRASourceFormat
    from power_control_tools.gui.fra_loop_designer import FRALoopDesignerWindow

    app = _app(qt_widgets)
    window = FRALoopDesignerWindow()

    assert window.source_format.currentData() == FRASourceFormat.BODE100
    assert window.measurement_kind.currentData() == FRAMeasurementKind.PLANT
    assert window.new_mode.currentData() == "quick"
    assert window.centralWidget() is not None
    assert not window.export_button.isEnabled()

    window.close()
    app.processEvents()


def test_fra_quick_tune_gui_uses_explicit_firmware_plus_a_convention():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    from power_control_tools.fra.models import FRAMeasurementKind
    from power_control_tools.gui.fra_loop_designer import FRALoopDesignerWindow

    app = _app(qt_widgets)
    window = FRALoopDesignerWindow()

    complete_index = window.measurement_kind.findData(FRAMeasurementKind.COMPLETE_LOOP)
    window.measurement_kind.setCurrentIndex(complete_index)
    window.old_mode.setCurrentIndex(window.old_mode.findData("ba"))
    window.old_coeff_convention.setCurrentIndex(window.old_coeff_convention.findData("plus_a"))
    window.old_fs.setValue(40_000.0)
    window.old_b.setText("0.18,-0.31,0.14")
    window.old_a.setText("1.72,-0.74")

    existing = window._existing_controller()
    assert np.allclose(existing.a, (1.0, -1.72, 0.74))

    tuned = window._quick_controller(existing)
    assert np.allclose(tuned.b, existing.b)
    assert np.allclose(tuned.a, existing.a)

    window.quick_gain.setValue(2.0)
    tuned_gain = window._quick_controller(existing)
    assert np.allclose(np.asarray(tuned_gain.b), 2.0 * np.asarray(existing.b))
    assert np.allclose(tuned_gain.a, existing.a)

    window.close()
    app.processEvents()


def test_launcher_exposes_fra_loop_designer_as_top_level_workspace():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    from llc_design.gui.launcher import WorkspaceSelectionDialog

    app = _app(qt_widgets)
    dialog = WorkspaceSelectionDialog()
    texts = [button.text() for button in dialog.findChildren(qt_widgets.QPushButton)]
    assert any("FRA Loop Designer" in text for text in texts)

    dialog.close()
    app.processEvents()

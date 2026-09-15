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


def test_fra_loop_designer_window_initializes_and_no_data_state_is_safe():
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

    # Explicit no-data calculation must leave the tool in a safe state even
    # if the button's initial visual enabled state depends on widget creation.
    window.recalculate()
    assert not window.export_button.isEnabled()
    assert window.current_new_controller is None
    assert "请先导入" in window.summary.toPlainText()

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


def test_fra_advanced_actions_and_dialogs_initialize():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    from power_control_tools.gui.fra_advanced import FRAAutoDesignDialog, FRAModelFitDialog, install_advanced_fra_actions
    from power_control_tools.gui.fra_loop_designer import FRALoopDesignerWindow

    app = _app(qt_widgets)
    window = FRALoopDesignerWindow()
    install_advanced_fra_actions(window)
    actions = [action.text() for toolbar in window.findChildren(qt_widgets.QToolBar) for action in toolbar.actions()]
    assert "Auto Design" in actions
    assert "Model ID / Fit" in actions

    auto = FRAAutoDesignDialog(window)
    model = FRAModelFitDialog(window)
    assert auto.pm.value() == 60.0
    assert not auto.apply_button.isEnabled()
    assert model.max_order.value() == 5

    auto.close()
    model.close()
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


def _plant_measurement(low_hz: float = 1.0):
    """Synthetic Equivalent Plant with one pole and 5 us of pure delay.

    The default band starts far enough below the ~95 Hz crossover that the
    fitted model satisfies the step bandwidth-coverage gate (audit 7.3).
    """
    from power_control_tools.fra.models import FRAMeasurement, FRASourceFormat

    f = np.geomspace(low_hz, 15_000.0, 360)
    plant = 2.0 / (1.0 + 1j * f / 300.0) * np.exp(-1j * 2.0 * np.pi * f * 5e-6)
    return FRAMeasurement(
        f,
        20.0 * np.log10(np.abs(plant)),
        np.degrees(np.angle(plant)),
        FRASourceFormat.GENERIC,
        source_path="synthetic.csv",
    ), plant


def test_fra_loop_designer_offers_the_full_toolbox_controller_catalogue():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    from power_control_tools.controllers import CONTROLLER_LABELS, EXACT_CONTROLLER
    from power_control_tools.gui.fra_loop_designer import FRALoopDesignerWindow
    from power_control_tools.models import ControllerKind

    app = _app(qt_widgets)
    window = FRALoopDesignerWindow()

    offered = [window.new_kind.itemData(index) for index in range(window.new_kind.count())]
    for kind in ControllerKind:
        assert kind in offered, f"FRA designer is missing {kind}"
    assert EXACT_CONTROLLER in offered
    # The Control Tools panel must present the same catalogue and labels.
    for kind, label in CONTROLLER_LABELS.items():
        assert window.new_kind.findData(kind) >= 0
        assert window.new_kind.itemText(window.new_kind.findData(kind)) == label

    window.close()
    app.processEvents()


def test_fra_custom_hz_mode_uses_the_exact_coefficients():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    from power_control_tools.controllers import EXACT_CONTROLLER
    from power_control_tools.gui.fra_loop_designer import FRALoopDesignerWindow

    app = _app(qt_widgets)
    window = FRALoopDesignerWindow()

    window.new_kind.setCurrentIndex(window.new_kind.findData(EXACT_CONTROLLER))
    window.new_fs.setValue(40_000.0)
    window.new_exact_b.setText("0.1, -0.09")
    window.new_exact_a.setText("1, -1.9, 0.91")
    controller = window._new_structure_controller()
    np.testing.assert_allclose(controller.b, (0.1, -0.09))
    np.testing.assert_allclose(controller.a, (1.0, -1.9, 0.91))
    assert controller.sample_rate_hz == 40_000.0

    window.close()
    app.processEvents()


def test_identified_plant_model_can_be_used_for_loop_bode_and_step():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    from power_control_tools.fra.fitting import fit_rational_frequency_response
    from power_control_tools.fra.loop_link import STEP_OK
    from power_control_tools.gui.fra_loop_designer import FRALoopDesignerWindow
    from power_control_tools.models import ControllerKind

    app = _app(qt_widgets)
    window = FRALoopDesignerWindow()
    measurement, plant = _plant_measurement()
    window.measurement = measurement

    index = window.new_mode.findData("structure")
    window.new_mode.setCurrentIndex(index)
    window.new_kind.setCurrentIndex(window.new_kind.findData(ControllerKind.PI))
    window.new_fs.setValue(40_000.0)
    window.new_kp.setValue(0.4)
    window.new_ti.setValue(1.0 / (2.0 * np.pi * 80.0))
    window.recalculate()
    # A rendered analysis must not silently degrade into the ERROR path.
    assert not window.summary.toPlainText().startswith("ERROR"), window.summary.toPlainText()
    assert not window.details.toPlainText().startswith("ERROR"), window.details.toPlainText()
    assert "TS type: plant" in window.details.toPlainText()
    measured_bode_loop = np.asarray(window.current_loop).copy()
    assert measured_bode_loop.size

    fit = fit_rational_frequency_response(
        measurement.frequency_hz, plant, max_poles=3, focus_hz=1_000.0, fit_delay=True
    )
    assert fit.metrics.confidence in ("GOOD", "FAIR")

    window.set_identified_plant(fit.model, (float(measurement.frequency_hz[0]), float(measurement.frequency_hz[-1])), fit.metrics.confidence)
    assert window.plant_source.currentData() == "identified"
    assert window.current_plant is not None
    assert window.current_metrics is not None
    # The plant used for the loop must now be the identified model response.
    np.testing.assert_allclose(
        window.current_plant,
        fit.model.frequency_response(window.current_frequency_hz),
        rtol=1e-9,
    )
    # The identified plant must reproduce the measured-plant loop closely:
    # this is the whole point of linking the model to the controller.
    np.testing.assert_allclose(window.current_loop, measured_bode_loop, rtol=5e-2)

    window.step_enable.setChecked(True)
    window.recalculate()
    assert not window.summary.toPlainText().startswith("ERROR"), window.summary.toPlainText()
    assert window.current_step_status == STEP_OK
    assert window.current_step is not None and window.current_step.stable
    assert np.all(np.isfinite(window.current_step.response))
    assert window.current_step.final_value > 0.0

    window.close()
    app.processEvents()


def test_model_id_dialog_hands_the_plant_model_to_the_loop_designer(monkeypatch):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    from power_control_tools.fra.fitting import fit_rational_frequency_response
    from power_control_tools.gui.fra_advanced import FRAModelFitDialog
    from power_control_tools.gui.fra_loop_designer import FRALoopDesignerWindow

    app = _app(qt_widgets)
    window = FRALoopDesignerWindow()
    measurement, plant = _plant_measurement()
    window.measurement = measurement
    dialog = FRAModelFitDialog(window)
    dialog.target.setCurrentIndex(dialog.target.findData("plant"))
    dialog.focus.setValue(1_000.0)
    dialog.run_fit()
    assert dialog.result is not None
    assert dialog.use_for_loop.isEnabled()
    assert dialog.fit_band is not None

    monkeypatch.setattr(qt_widgets.QMessageBox, "information", staticmethod(lambda *a, **k: None))
    dialog.send_plant_to_host()
    assert window.identified_plant is not None
    assert window.plant_source.currentData() == "identified"

    dialog.close()
    window.close()
    app.processEvents()


def test_fra_new_structure_builds_every_toolbox_controller_kind():
    """Every Control Tools controller must be selectable *and constructible* here."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    from power_control_tools.controllers import controller_parameter_keys
    from power_control_tools.gui.fra_loop_designer import FRALoopDesignerWindow
    from power_control_tools.models import ControllerKind

    app = _app(qt_widgets)
    window = FRALoopDesignerWindow()
    inverse = {widget: key for key, widget in window.new_field_widgets.items()}

    for kind in ControllerKind:
        window.new_kind.setCurrentIndex(window.new_kind.findData(kind))
        if kind in (ControllerKind.TYPE_II, ControllerKind.TYPE_III):
            window.new_type_input_mode.setCurrentIndex(window.new_type_input_mode.findData("pz"))
        window._update_visibility()
        shown = {inverse[w] for w in window.new_fields if not w.isHidden() and w in inverse}
        expected = set(controller_parameter_keys(kind))
        assert shown == expected, f"{kind}: shown={sorted(shown)} expected={sorted(expected)}"
        controller = window._new_structure_controller()
        assert controller.sample_rate_hz == window.new_fs.value()
        assert len(controller.a) >= 2

    # The exact-H(z) entry mode bypasses structure derivation entirely.
    from power_control_tools.controllers import EXACT_CONTROLLER
    window.new_kind.setCurrentIndex(window.new_kind.findData(EXACT_CONTROLLER))
    window._update_visibility()
    assert not window.new_exact_b.isHidden() and not window.new_exact_a.isHidden()

    window.close()
    app.processEvents()


def test_auto_design_power_template_applies_as_exact_hz(monkeypatch):
    """Power 2P2Z/3P3Z must be transferable without silent parameter remapping."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    from power_control_tools.controllers import EXACT_CONTROLLER, design_controller
    from power_control_tools.discretize import discretize_transfer_function
    from power_control_tools.fra.analysis import LoopStabilityResult
    from power_control_tools.fra.auto_design import AutoDesignCandidate, AutoDesignResult
    from power_control_tools.gui.fra_advanced import FRAAutoDesignDialog
    from power_control_tools.gui.fra_loop_designer import FRALoopDesignerWindow
    from power_control_tools.models import ControllerKind, DiscretizationMethod

    app = _app(qt_widgets)
    window = FRALoopDesignerWindow()
    dialog = FRAAutoDesignDialog(window)

    power = discretize_transfer_function(
        design_controller(
            ControllerKind.TWO_P_TWO_Z, gain=0.5,
            fz1_hz=200.0, fz2_hz=1_000.0, fp1_hz=6_000.0, fp2_hz=20_000.0,
        ),
        50_000.0,
        DiscretizationMethod.TUSTIN,
    )
    metrics = LoopStabilityResult(
        (), (), 3_000.0, 60.0, 10.0, 60.0, 10.0, 1.2, 1.1,
        np.asarray([1.0 + 0j]), np.asarray([0.5 + 0j]), "PASS",
    )
    candidate = AutoDesignCandidate(
        3_000.0, 3_000.0, 60.0, 10.0, 1.2, 1.1, power,
        ControllerKind.TWO_P_TWO_Z, {"gain": 0.5}, metrics, True, 0.0, "accepted",
    )
    dialog.result = AutoDesignResult(3_000.0, 60.0, candidate, (candidate,), False, "PASS", "ok")

    monkeypatch.setattr(qt_widgets.QMessageBox, "information", staticmethod(lambda *a, **k: None))
    dialog.apply_result()

    assert window.new_kind.currentData() == EXACT_CONTROLLER
    applied = window._new_structure_controller()
    # The GUI transfers coefficients as 12-significant-digit text, so the
    # round-trip is exact to roughly 1e-11 relative precision.
    np.testing.assert_allclose(applied.b, power.normalized().b, rtol=1e-10)
    np.testing.assert_allclose(applied.a, power.normalized().a, rtol=1e-10)
    assert applied.sample_rate_hz == 50_000.0

    dialog.close()
    window.close()
    app.processEvents()

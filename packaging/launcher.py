"""PyInstaller entry point; --self-test exercises the actual frozen GUI."""
from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
from dataclasses import asdict
from html import unescape
from pathlib import Path


def _exercise_power_stage(stage, directory: Path) -> dict:
    """Run independent CCM checks through the real widget/save/report workflow."""
    import numpy as np

    from power_stage_design.models import DesignInputs

    # Explicit acceptance points, independent of changeable GUI presets.
    cases = (
        (DesignInputs("T01", 12, 5, 3, 100e3, 47e-6), 5 / 12, 3, 12),
        (DesignInputs("T02", 24, 48, 2, 100e3, 100e-6), 0.5, 4, 48),
        (DesignInputs("T09", 48, 12, 1, 100e3, 1e-3, turns_ratio_np_ns=4), 0.5, 0.5, 96),
    )
    evidence = {}
    buck_loop = None
    for inputs, duty, average, blocking_voltage in cases:
        stage.set_inputs(inputs)
        result = stage.result
        if result is None or result.inputs != inputs or result.mode != "CCM":
            raise RuntimeError(f"PSD packaged {inputs.topology} calculation failed")
        on_voltage = inputs.vin_v - inputs.vout_v if inputs.topology == "T01" else inputs.vin_v
        ripple = on_voltage * duty / (inputs.inductance_h * inputs.fsw_hz)
        expected_rms = (duty * (average ** 2 + ripple ** 2 / 12)) ** 0.5
        measured = {
            "duty": result.duty,
            "inductor_ripple_pp_a": result.metrics["inductor_ripple_pp_a"],
            "switch_rms_a": result.stress["Q1"]["current_rms_a"],
            "switch_voltage_v": result.stress["Q1"]["voltage_max_v"],
        }
        np.testing.assert_allclose(list(measured.values()),
                                   [duty, ripple, expected_rms, blocking_voltage], rtol=1e-10, atol=1e-12)
        stage.select_component("Q1")
        for plot, signal in ((stage.current_plot, result.waveforms["Q1"].current_a),
                             (stage.voltage_plot, result.waveforms["Q1"].voltage_v)):
            curve, = plot.listDataItems()
            t, y = signal.sample(count=800, periods=2)
            np.testing.assert_array_equal(curve.xData, t)
            np.testing.assert_array_equal(curve.yData, y)

        loop_arrays = {}
        if inputs.topology == "T01":
            stage.loop_control.setCurrentIndex(stage.loop_control.findData("vmc"))
            stage.analyze_loop()
            loop = stage.loop_result
            if loop is None:
                raise RuntimeError("PSD packaged Buck VMC calculation failed")
            plant, _ = stage.loop_parameters()
            # Independent loaded LC+ESR impedance, with actual GUI settings.
            s = 2j * np.pi * loop.frequency_hz
            z = 1 / (inputs.iout_a / inputs.vout_v
                     + s * plant.capacitance_f / (1 + s * plant.capacitance_f * plant.esr_ohm))
            expected = inputs.vin_v / plant.ramp_v * z / (s * inputs.inductance_h + plant.dcr_ohm + z)
            np.testing.assert_allclose(loop.plant_response, expected, rtol=1e-10, atol=1e-12)
            for key in ("frequency_hz", "plant_response", "compensator_response", "amplifier_response", "loop_response"):
                values = getattr(loop, key)
                if not np.all(np.isfinite(values)):
                    raise RuntimeError(f"PSD packaged Buck VMC has nonfinite {key}")
                loop_arrays[key] = values.copy()
            buck_loop = {"control_mode": "vmc", "frequency_points": len(loop.frequency_hz),
                         "max_plant_relative_error": float(np.max(abs((loop.plant_response - expected) / expected))),
                         "roundtrip_identical": True, "plotted_curves": 4}

        snapshot, settings = result.to_dict(), stage._loop_settings
        project = directory / f"{inputs.topology}.json"
        report_path = directory / f"{inputs.topology}.html"
        stage.save_to(project)
        saved = json.loads(project.read_text(encoding="utf-8"))
        if saved["inputs"] != asdict(inputs) or saved["result_identity"] != result.design_id or saved["loop_settings"] != settings:
            raise RuntimeError("PSD packaged saved project differs from the current result")
        # Change state before restoring, so a no-op loader cannot pass.
        stage.set_inputs(cases[1 if inputs.topology == "T01" else 0][0])
        stage.open_from(project)
        if stage.result is None or stage.result is result or stage.result.to_dict() != snapshot or stage._loop_settings != settings:
            raise RuntimeError("PSD packaged restore did not recompute the saved design")
        for key, expected in loop_arrays.items():
            np.testing.assert_array_equal(getattr(stage.loop_result, key), expected)
        if loop_arrays:
            for plot, transform in ((stage.loop_gain_plot, lambda x: 20 * np.log10(np.maximum(abs(x), 1e-300))),
                                    (stage.loop_phase_plot, lambda x: np.unwrap(np.angle(x)) * 180 / np.pi)):
                curves = plot.listDataItems()
                if len(curves) != 4:
                    raise RuntimeError("PSD packaged Buck VMC plots are incomplete")
                for curve, key in zip(curves, ("plant_response", "compensator_response", "amplifier_response", "loop_response"), strict=True):
                    np.testing.assert_array_equal(curve.xData, loop_arrays["frequency_hz"])
                    np.testing.assert_allclose(curve.yData, transform(loop_arrays[key]), rtol=1e-12, atol=1e-12)
        stage.report_to(report_path)
        report_text = report_path.read_text(encoding="utf-8")
        report_data = json.loads(unescape(report_text.split('<h2>可复现数据</h2><pre>', 1)[1].split('</pre>', 1)[0]))
        if (report_data["design_id"] != result.design_id or report_data["inputs"] != asdict(inputs)
                or report_data["metrics"] != result.metrics or report_data["stress"] != result.stress
                or report_data["loop_settings"] != settings or report_data["model_revision"] != result.model_version):
            raise RuntimeError("PSD packaged report differs from the restored design")
        evidence[inputs.topology] = {"design_id": result.design_id, "mode": result.mode, **measured,
                                     "project_roundtrip": True, "report_consistency": True}

    stage.toolbox.select_tool("X12")
    calculator = stage.toolbox.calculate()
    if calculator is None or calculator.tool_id != "X12":
        raise RuntimeError("PSD packaged filter calculator failed")
    return {"power_stage_designer": True, "power_stage_launcher_entry": True,
            "power_stage_design_id": evidence["T01"]["design_id"],
            "power_stage_model_revision": stage.result.model_version,
            "power_stage_cases": evidence, "power_stage_buck_vmc": buck_loop,
            "power_stage_toolbox_filter": True}


def _run_packaged_self_test() -> int:
    """Exercise all workspaces and emit evidence only after successful cleanup."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from pyqtgraph import GraphicsLayoutWidget
    from PySide6.QtCore import QCoreApplication, QEvent, QThreadPool
    from PySide6.QtWidgets import QApplication, QPushButton

    from llc_design import __version__
    from llc_design.core.spec import LLCDesignSpec
    from llc_design.gui import theme
    from llc_design.gui.launcher import (
        WorkspaceApplicationController,
        WorkspaceSelectionDialog,
    )
    from llc_design.gui.system_modeling import SystemModelingDesignDialog
    from llc_design.gui.updater import wait_for_update_checks
    from llc_design.validation.provenance import validate_bundled_data
    from power_control_tools import part_validation

    app = QApplication.instance() or QApplication(["PowerDesignTool", "--self-test"])
    app.setApplicationName("Power Design Toolkit")
    theme.apply_app_theme(app)
    # This self-test process is offline. Use the updater's shutdown boundary,
    # leaving QTimer's real bound callbacks and their Qt owners intact.
    wait_for_update_checks()
    controller = WorkspaceApplicationController(LLCDesignSpec())
    windows = [controller.llc_window, controller.pfc_window, controller.control_window,
               controller.fra_window, controller.dab_window, controller.power_stage_window]
    selector = WorkspaceSelectionDialog()
    guided = SystemModelingDesignDialog()
    try:
        report = validate_bundled_data()
        if not report.valid:
            raise RuntimeError(json.dumps(report.to_dict(), ensure_ascii=False))
        selector.show()
        app.processEvents()
        button = selector.findChild(QPushButton, "power_stage_designer_button")
        if button is None or not button.isVisible() or not button.isEnabled():
            raise RuntimeError("PSD packaged launcher entry is missing or disabled")
        button.click()
        if selector.selected_workspace != "power_stage":
            raise RuntimeError("PSD packaged launcher entry selected the wrong workspace")
        controller.show_workspace(selector.selected_workspace)
        app.processEvents()
        if not controller.power_stage_window.isVisible():
            raise RuntimeError("PSD packaged launcher did not open its workspace")
        guided.show()
        app.processEvents()
        guided.close()
        with tempfile.TemporaryDirectory(prefix="pdt-psd-self-test-") as directory:
            psd_proof = _exercise_power_stage(controller.power_stage_window, Path(directory))
        workspace_names = ["llc", "pfc", "control", "fra", "dab", "power_stage"]
        for workspace, window in zip(workspace_names, windows, strict=True):
            controller.show_workspace(workspace)
            app.processEvents()
            if controller.active_workspace != workspace or not window.isVisible():
                raise RuntimeError(f"Packaged workspace failed to open: {workspace}")

        # This path is inside the frozen bundle, not relative to source/CWD.
        taxonomy = Path(part_validation.__file__).resolve().parents[1] / "engineering_data/brand_taxonomy.json"
        part_validation.load_brand_taxonomy()
        proof = {"proof_schema": 2, "success": True, "version": __version__,
                 "frozen": bool(getattr(sys, "frozen", False)), "platform": sys.platform,
                 "workspaces": workspace_names, "guided_design": True, **psd_proof,
                 "brand_taxonomy_sha256": hashlib.sha256(taxonomy.read_bytes()).hexdigest()}
    finally:
        for window in [selector, guided, *windows]:
            window.close()
        if not QThreadPool.globalInstance().waitForDone(30000):
            raise RuntimeError("Packaged GUI workers did not finish")
        wait_for_update_checks()
        app.processEvents()
        # Destroy owned scenes first; never delete Qt's internal top-level
        # menus/viewports independently or replace delayed bound callbacks.
        for window in [selector, guided, *windows]:
            window.close()
            for graphics in window.findChildren(GraphicsLayoutWidget):
                if not graphics.closed:
                    graphics.close()
            window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        app.processEvents()

    destination = os.environ.get("PDT_SELF_TEST_REPORT")
    if destination:
        Path(destination).write_text(json.dumps(proof, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print("Power Design Toolkit packaged self-test: OK")
    return 0


def main() -> int:
    if "--self-test" in sys.argv[1:]:
        return _run_packaged_self_test()
    from llc_design.gui.app import run_gui
    return int(run_gui())


if __name__ == "__main__":
    sys.exit(main())

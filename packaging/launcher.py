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


def _require(condition, message):
    if not condition:
        raise RuntimeError(f"Packaged completion self-test: {message}")


def _wait_for_worker(predicate, seconds=300):
    """Run real Qt events while genuine solver workers own the GIL."""
    from PySide6.QtCore import QEventLoop, QTimer
    if predicate():
        return
    loop, poll, deadline = QEventLoop(), QTimer(), QTimer()
    deadline.setSingleShot(True)
    poll.timeout.connect(lambda: loop.quit() if predicate() else None)
    deadline.timeout.connect(loop.quit)
    poll.start(20)
    deadline.start(round(seconds * 1000))
    loop.exec()
    poll.stop()
    deadline.stop()
    _require(predicate(), "real worker exceeded bounded timeout")


def _advanced_inputs():
    from power_stage_design.models import DesignInputs
    from power_stage_design.psfb_dynamics import PSFBOptions
    from power_stage_design.qr_dynamics import QROptions
    from power_stage_design.two_switch_dynamics import T05Options
    return (
        DesignInputs("T05", 24, 12, .1, 100e3, 100e-6,
            two_switch_options=T05Options(parameter_source="Explicit illustrative artifact QA constants")),
        DesignInputs("T11", 48, 12, 1, 180e3, 200e-6, turns_ratio_np_ns=8,
            resonant_capacitance_f=500e-12, qr_options=QROptions(valley_policy="selected", valley_index=2,
                on_resistance_ohm=.1, capacitance_source="Independent constant-C artifact QA case")),
        DesignInputs("T18", 400, 48, 5, 100e3, 100e-6, turns_ratio_np_ns=4,
            magnetizing_inductance_h=1e-3, leakage_inductance_h=20e-6, dead_time_s=200e-9, diode_drop_v=.7,
            psfb_options=PSFBOptions(coss_f=300e-12, r_on_ohm=.15, body_diode_drop_v=.8,
                body_diode_resistance_ohm=.05, transformer_capacitance_f=100e-12,
                rectifier_capacitance_f=1e-9, rectifier_resistance_ohm=.02,
                parameter_source="Explicit seven-state artifact QA constants")),
    )


def _waveform_fingerprint(result):
    import numpy as np
    digest = hashlib.sha256()
    for key, waveform in sorted(result.waveforms.items()):
        digest.update(key.encode())
        for signal in (waveform.current_a, waveform.voltage_v):
            t, y = signal.sample(count=800, periods=2)
            _require(np.isfinite(t).all() and np.isfinite(y).all(), "nonfinite advanced waveform")
            digest.update(np.asarray(t, dtype="<f8").tobytes())
            digest.update(np.asarray(y, dtype="<f8").tobytes())
    return digest.hexdigest()


def _exercise_advanced_power_stage(stage, directory, app, priority_proof):
    import numpy as np
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest

    from power_stage_design.models import DesignInputs
    from power_stage_design.schematic import layout, schematic_size

    evidence = {}
    for inputs in _advanced_inputs():
        stage.set_inputs(inputs)
        _wait_for_worker(lambda: stage.result is not None or not stage._jobs)
        _require(stage.result is not None, stage.status.text())
        result = stage.result
        _require(stage.current_inputs() == inputs == result.inputs, "advanced inputs changed")
        _require(stage.advanced_model.enabled.isChecked(), "optional model is not enabled")
        fingerprint = _waveform_fingerprint(result)
        app.processEvents()
        width, height = schematic_size(inputs.topology, device_keys=result.waveforms)
        selected = []
        for branch in layout(inputs.topology, device_keys=result.waveforms)[0]:
            if not branch.selectable:
                continue
            QTest.mouseClick(stage.schematic, Qt.MouseButton.LeftButton,
                pos=QPoint(round(branch.x * stage.schematic.width() / width),
                           round(branch.y * stage.schematic.height() / height)))
            _require(stage.component_combo.currentData() == branch.key, "advanced branch hit test failed")
            for plot, signal in ((stage.current_plot, result.waveforms[branch.key].current_a),
                                 (stage.voltage_plot, result.waveforms[branch.key].voltage_v)):
                curve, = plot.listDataItems()
                t, y = signal.sample(count=800, periods=2)
                np.testing.assert_array_equal(curve.xData, t)
                np.testing.assert_array_equal(curve.yData, y)
            selected.append(branch.key)
        _require(set(selected) == set(result.waveforms), "advanced branches are incomplete")
        project, report = directory / f"{inputs.topology}.json", directory / f"{inputs.topology}.html"
        stage.save_to(project)
        saved = json.loads(project.read_text(encoding="utf-8"))
        _require(DesignInputs(**saved["inputs"]) == inputs, "advanced saved inputs changed")
        stage.advanced_model.enabled.setChecked(False)
        _require(stage.result is None and not stage.report_action.isEnabled(), "advanced stale result retained")
        stage.open_from(project)
        _wait_for_worker(lambda: stage.result is not None or not stage._jobs)
        _require(stage.result is not None, stage.status.text())
        _require(stage.result is not result and stage.current_inputs() == inputs == stage.result.inputs,
                 "advanced restore did not recompute the original inputs")
        _require(stage.advanced_model.enabled.isChecked(), "advanced optional model not restored")
        _require(_waveform_fingerprint(stage.result) == fingerprint, "restored advanced waveforms changed")
        stage.report_to(report)
        html = report.read_text(encoding="utf-8")
        data = json.loads(unescape(html.split('<h2>可复现数据</h2><pre>', 1)[1].split('</pre>', 1)[0]))
        # JSON canonicalization preserves tuple/list equivalence in option tables.
        _require(data["inputs"] == json.loads(json.dumps(asdict(inputs)))
                 and data["design_id"] == result.design_id and data["metrics"] == result.metrics
                 and data["stress"] == result.stress and data["model_revision"] == result.model_version,
                 "advanced report differs from the recomputed result")
        _require(all("component-" + key in html for key in selected), "advanced report lacks selected branches")
        evidence[inputs.topology] = {"design_id": result.design_id, "inputs": asdict(inputs),
            "waveform_sha256": fingerprint, "branches_clicked": sorted(selected),
            "samples_per_branch": 800, "max_curve_absolute_error": 0.0,
            "project_roundtrip": True, "recomputed_waveforms_identical": True,
            "report_consistency": True, "mode": result.mode, "provenance": result.provenance,
            "warnings": result.warnings}
        if inputs.topology == "T05":
            priority_proof["advanced_suggestion"] = _exercise_parameter_suggestion(
                stage, directory, app, ripple=result.metrics["inductor_ripple_pp_a"], maximum_duty=.95)
    return evidence


def _exercise_parameter_suggestion(stage, directory, app, *, ripple, maximum_duty):
    """Use production preview/Apply/restore; preserve nested model parameters."""
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    from power_stage_design.gui import PRESETS
    from power_stage_design.models import DesignInputs
    from power_stage_design.suggestions import validate_suggestion_record

    source = stage.current_inputs()
    _require(stage.result is not None and stage.result.inputs == source, "suggestion source is not solved")
    stage.tabs.setCurrentWidget(stage.suggestion_tab)
    stage.suggestion_ripple.setValue(ripple)
    stage.suggestion_duty.setValue(maximum_duty)
    stage.suggestion_magnetizing.setValue(0)
    stage.suggestion_ratio.setChecked(False)
    app.processEvents()
    QTest.mouseClick(stage.suggest_button, Qt.MouseButton.LeftButton)
    _wait_for_worker(lambda: not stage._suggestion_events)
    proposal = stage.parameter_proposal
    _require(proposal is not None, stage.suggestion_output.toPlainText())
    _require(stage.current_inputs() == source and stage.result.inputs == source,
             "preview silently applied parameter changes")
    record = json.loads(json.dumps(proposal.record()))
    _require(validate_suggestion_record(record, proposal.result) == record, "proposal provenance is invalid")
    _require(set(proposal.changed_fields) <= {"inductance_h"}, "proposal changed untargeted inputs")
    for field in ("two_switch_options", "qr_options", "psfb_options"):
        _require(getattr(proposal.inputs, field) == getattr(source, field), "proposal lost nested model options")
    QTest.mouseClick(stage.apply_suggestion_button, Qt.MouseButton.LeftButton)
    _wait_for_worker(lambda: stage.result is not None or not stage._jobs)
    _require(stage.result is not None and stage.current_inputs() == proposal.inputs == stage.result.inputs,
             "Apply changed the checked proposal")
    _require(stage.result is not proposal.result, "Apply skipped production recomputation")
    fingerprint = _waveform_fingerprint(stage.result)
    project = directory / f"{source.topology}-suggestion.json"
    report = directory / f"{source.topology}-suggestion.html"
    stage.save_to(project)
    saved = json.loads(project.read_text(encoding="utf-8"))
    _require(saved["parameter_suggestion"] == record and DesignInputs(**saved["inputs"]) == proposal.inputs,
             "saved proposal lost inputs or provenance")
    stage.set_inputs(PRESETS["T01"])
    stage.open_from(project)
    _wait_for_worker(lambda: stage.result is not None or not stage._jobs)
    _require(stage.result is not None and stage.current_inputs() == proposal.inputs == stage.result.inputs,
             "proposal restore changed inputs")
    _require(stage._applied_suggestion == record and _waveform_fingerprint(stage.result) == fingerprint,
             "proposal restore lost provenance or changed recalculated waveforms")
    stage.report_to(report)
    html = report.read_text(encoding="utf-8")
    data = json.loads(unescape(html.split('<h2>可复现数据</h2><pre>', 1)[1].split('</pre>', 1)[0]))
    _require(data["parameter_suggestion"] == record and data["inputs"] == saved["inputs"]
             and data["design_id"] == stage.result.design_id and data["metrics"] == stage.result.metrics
             and data["stress"] == stage.result.stress, "proposal report lost actual result or provenance")
    stage.tabs.setCurrentIndex(0)
    return {"inputs": asdict(stage.result.inputs), "design_id": stage.result.design_id,
            "proposal": record, "waveform_sha256": fingerprint, "preview_did_not_apply": True,
            "apply_recomputed": True, "nested_options_preserved": True,
            "project_roundtrip": True, "recomputed_waveforms_identical": True, "report_consistency": True}


def _exercise_priority_bridge(stage, directory, app):
    from power_stage_design.models import DesignInputs

    stage.set_inputs(DesignInputs("T17", 48, 5, .005, 100e3, 470e-6,
        capacitance_f=1e-3, turns_ratio_np_ns=2, magnetizing_inductance_h=.01))
    return _exercise_parameter_suggestion(stage, directory, app, ripple=.02, maximum_duty=.45)


def _exercise_llc_priority_components(host, directory, app):
    """Click and export new terminals from one genuine full-bridge source."""
    from unittest.mock import patch

    import numpy as np
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QFileDialog

    from llc_design.core.config import waveform_request_identity
    from llc_design.core.spec import PrimaryTopology

    view = host.waveform_view
    _require(host.spec.primary_topology == PrimaryTopology.FULL_BRIDGE, "T21 fixture is not full bridge")
    host.tabs.setCurrentWidget(view)
    app.processEvents()
    QTest.mouseClick(view.fast_button, Qt.MouseButton.LeftButton)
    _wait_for_worker(lambda: not host._active_workers)
    bundle = view.bundle
    _require(bundle is not None, "T21 current-input waveform was not calculated")
    identity = bundle.metadata["request_identity"]
    signals = {key: signal.values for key, signal in bundle.signals.items()}
    np.testing.assert_array_equal(signals["v_bus"], np.full_like(signals["i_resonant"], bundle.metadata["vbus_v"]))
    np.testing.assert_allclose(signals["v_output_cap_esr"], bundle.metadata["output_cap_esr_ohm"] * signals["i_output_cap"], atol=1e-10)
    np.testing.assert_allclose(signals["v_series_resistance"], bundle.metadata["series_resistance_ohm"] * signals["i_resonant"], atol=1e-12)
    bus_error = float(np.max(np.abs(signals["v_bus"] * signals["i_bus"] - signals["v_bridge"] * signals["i_resonant"])))
    _require(bus_error <= 1e-10 and np.min(signals["i_bus"]) < 0, "T21 source power or reverse current is wrong")
    digest = hashlib.sha256(np.asarray(bundle.time_s, dtype="<f8").tobytes())
    for key, values in sorted(signals.items()):
        _require(np.isfinite(values).all(), "T21 source has nonfinite values")
        digest.update(key.encode())
        digest.update(np.asarray(values, dtype="<f8").tobytes())
    selected = {}
    targets = {"coesr": ("v_output_cap_esr", "i_output_cap"),
               "rseries": ("v_series_resistance", "i_resonant"), "bus": ("v_bus", "i_bus")}
    for component, keys in targets.items():
        host.tabs.setCurrentIndex(0)
        app.processEvents()
        block = host.topology_view.blocks[component]
        point = host.topology_view.mapFromScene(block.sceneBoundingRect().center())
        QTest.mouseClick(host.topology_view.viewport(), Qt.MouseButton.LeftButton, pos=point)
        app.processEvents()
        _require(host.tabs.currentWidget() is view and view.selected_component == component
                 and view._selected_keys() == keys and view.bundle is bundle, "T21 component click selected another source")
        for row, key in enumerate(keys):
            curve, = view._plots[row].listDataItems()
            np.testing.assert_array_equal(curve.xData, bundle.time_s * 1e6)
            np.testing.assert_array_equal(curve.yData, signals[key])
        _require(view.component_context[component]["source_identity"] == identity
                 and waveform_request_identity(host._waveform_session()) == identity,
                 "T21 component context changed electrical identity")
        destination = directory / f"T21-{component}"
        with patch.object(QFileDialog, "getExistingDirectory", return_value=str(destination)):
            QTest.mouseClick(view.export_button, Qt.MouseButton.LeftButton)
        exported = json.loads((destination / "waveform_metadata.json").read_text(encoding="utf-8"))
        context = exported["context"]
        _require(context["selected_component"] == component and context["selected_signals"] == list(keys)
                 and context["component_evidence"][component]["source_identity"] == identity
                 and exported["metadata"]["request_identity"] == identity, "T21 report selected different component or source")
        markdown = (destination / "LLC_waveforms.md").read_text(encoding="utf-8")
        _require(identity in markdown and "selected_component.png" in markdown
                 and (destination / "selected_component.png").stat().st_size > 1000, "T21 selected-component report is incomplete")
        selected[component] = {"signals": list(keys), "source_identity": identity,
                               "max_curve_absolute_error": 0.0, "report_selection_exact": True}
    project = directory / "T21-priority.json"
    with patch.object(QFileDialog, "getSaveFileName", return_value=(str(project), "JSON")):
        host.save_json()
    saved = json.loads(project.read_text(encoding="utf-8"))
    _require(saved["waveform_session"]["component"] == "bus", "T21 saved selection is wrong")
    view.select_component("q1")
    with patch.object(QFileDialog, "getOpenFileName", return_value=(str(project), "JSON")):
        host.load_json()
    _wait_for_worker(lambda: not host._active_workers)
    _require(view.bundle is not None and view.bundle is not bundle and view.selected_component == "bus"
             and view.bundle.metadata["request_identity"] == identity, "T21 restore did not recompute exact source and selection")
    for key, values in signals.items():
        np.testing.assert_array_equal(view.bundle.signals[key].values, values)
    return {"topology": "T21", "primary_topology": "FULL_BRIDGE", "solver": "fast",
            "source_identity": identity, "waveform_sha256": digest.hexdigest(),
            "samples_per_signal": len(bundle.time_s), "components": selected,
            "bus_power_max_absolute_error_w": bus_error, "bus_reverse_current_min_a": float(np.min(signals["i_bus"])),
            "bus_voltage_ac_rms_v": bundle.signals["v_bus"].statistics.ac_rms,
            "project_roundtrip": True, "recomputed_waveforms_identical": True,
            "model_name": bundle.model_name, "warnings": list(bundle.warnings)}


def _exercise_two_stage(host, directory):
    import zipfile
    from unittest.mock import patch

    from PySide6.QtWidgets import QFileDialog

    from llc_design.core.config import load_project
    from llc_design.optimization.sweep import OptimizationConfig

    view = host.two_stage_optimization_view
    _require(any("Two-Stage" in host.tabs.tabText(i) for i in range(host.tabs.count())), "TwoStage tab is missing")
    host.tabs.setCurrentWidget(view)
    spec = host._spec_from_widgets()
    config = OptimizationConfig((5.5,), (spec.q_full_load,), (spec.resonant_frequency_hz,),
        (spec.primary_turns,), (spec.secondary_turns,), (spec.primary_device,),
        (spec.sr_parallel_devices_per_position,))
    view.config_text.setPlainText(json.dumps(asdict(config)))
    view.maximum_candidates.setValue(1)
    view.top_n.setValue(1)
    _require(view.verification_scope.currentData() == "nominal", "unexpected TwoStage verification scope")
    view.run_button.click()
    _wait_for_worker(lambda: not host._active_workers)
    _require(view.result is not None, view.status.text())
    candidate = view.selected_candidate()
    _require(candidate is not None, str([(c.reject_reason, c.diagnostics) for c in view.result.verified]))
    nominal = candidate.analysis.nominal
    digest = nominal.loss_waveform_sha256
    _require(nominal.semiconductor_source == "TIME_DOMAIN_WAVEFORM" and digest, "TwoStage lacks real TD provenance")
    verified_points = sum(p.semiconductor_source == "TIME_DOMAIN_WAVEFORM" for p in candidate.analysis.operating_points)
    _require(verified_points == 1 and "仅名义" in view.table.item(0, 0).text(), "nominal scope is overstated")
    _require(host._spec_from_widgets() == spec, "selecting a candidate silently changed the project")
    view.apply_button.click()
    _require(host.spec == host._spec_from_widgets() == candidate.spec and host.spec.ln_ratio == 5.5
             and host.system_analysis is candidate.analysis, "Apply changed exact inputs or TD analysis")
    project, report = directory / "two-stage.json", directory / "two-stage.zip"
    # Redirect file destinations only; production methods, workers and solvers run unchanged.
    with patch.object(QFileDialog, "getSaveFileName", return_value=(str(project), "JSON")):
        host.save_json()
    saved = load_project(project, include_optimization=True)[-1]
    _require(saved["request"]["verification_scope"] == "nominal"
             and any(s.get("waveform_sha256") == digest for s in saved["candidates"][0]["sources"]),
             "saved TwoStage source identity changed")
    with patch.object(QFileDialog, "getSaveFileName", return_value=(str(report), "ZIP")):
        view.report_button.click()
    _wait_for_worker(lambda: not host._active_workers)
    _require(report.is_file(), "TwoStage candidate report was not produced")
    with zipfile.ZipFile(report) as archive:
        _require(archive.testzip() is None, "TwoStage report archive is corrupt")
        _require(digest.encode() in archive.read("LLC_calculation_book.md"), "TwoStage report lost TD provenance")
        provenance = json.loads(archive.read("optimization_provenance.json"))
        _require(provenance["request"]["verification_scope"] == "nominal"
                 and provenance["selected_candidate"] == saved["candidates"][0], "TwoStage report selected another candidate")
    with patch.object(QFileDialog, "getOpenFileName", return_value=(str(project), "JSON")):
        host.load_json()
    _require(host.spec.ln_ratio == 5.5 and view.result is None and not view.apply_button.isEnabled()
             and not view.report_button.isEnabled() and "历史" in view.status.text(), "restored history authorizes stale Apply/report")
    return {"real_stage_a_and_b": True, "verification_scope": "nominal", "verified_td_points": verified_points,
        "fha_td_validity": candidate.fha_td_validity.value, "waveform_sha256": digest,
        "loss_source": nominal.semiconductor_source, "total_loss_w": candidate.total_loss_w,
        "selected_candidate": saved["candidates"][0], "applied_ln_ratio": host.spec.ln_ratio,
        "apply_exact": True, "project_roundtrip": True, "restored_history_requires_rerun": True,
        "report_consistency": True,
        "model_limit": "Nominal-point ideal TD losses only; remaining operating points and commutation retain FHA assumptions; no hardware qualification"}


def _run_packaged_self_test() -> int:
    """Exercise all workspaces and emit evidence only after successful cleanup."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QPushButton

    from llc_design import __version__
    from llc_design.core.spec import LLCDesignSpec
    from llc_design.gui import theme
    from llc_design.gui.launcher import (
        WorkspaceApplicationController,
        WorkspaceSelectionDialog,
    )
    from llc_design.gui.lifetime import dispose_dialog
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
    controller = WorkspaceApplicationController(LLCDesignSpec(vbus_hold_end_v=320., bus_capacitance_f=.0033))
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
            priority_proof = {"full_bridge_suggestion": _exercise_priority_bridge(controller.power_stage_window, Path(directory), app)}
            advanced_proof = _exercise_advanced_power_stage(controller.power_stage_window, Path(directory), app, priority_proof)
            controller.show_workspace("llc")
            app.processEvents()
            optimizer_proof = _exercise_two_stage(controller.llc_window, Path(directory))
            priority_proof["llc_components"] = _exercise_llc_priority_components(controller.llc_window, Path(directory), app)
        workspace_names = ["llc", "pfc", "control", "fra", "dab", "power_stage"]
        for workspace, window in zip(workspace_names, windows, strict=True):
            controller.show_workspace(workspace)
            app.processEvents()
            if controller.active_workspace != workspace or not window.isVisible():
                raise RuntimeError(f"Packaged workspace failed to open: {workspace}")

        # This path is inside the frozen bundle, not relative to source/CWD.
        taxonomy = Path(part_validation.__file__).resolve().parents[1] / "engineering_data/brand_taxonomy.json"
        part_validation.load_brand_taxonomy()
        proof = {"proof_schema": 3, "success": True, "version": __version__,
                 "frozen": bool(getattr(sys, "frozen", False)), "platform": sys.platform,
                 "workspaces": workspace_names, "guided_design": True, **psd_proof,
                 "power_stage_advanced": advanced_proof, "two_stage_optimization": optimizer_proof,
                 "power_stage_priority": priority_proof,
                 "brand_taxonomy_sha256": hashlib.sha256(taxonomy.read_bytes()).hexdigest()}
    finally:
        # Exercise the same final ownership boundary as normal application
        # exit, including Python-owned plot menus outside native scene trees.
        # Cleanup errors must prevent writing a successful frozen proof.
        dispose_dialog(selector)
        dispose_dialog(guided)
        controller.dispose()
        wait_for_update_checks()

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

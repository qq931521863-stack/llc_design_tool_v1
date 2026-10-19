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
                pos=QPoint(round(stage.schematic.schematic_rect().x() + branch.x * stage.schematic.schematic_rect().width() / width),
                           round(stage.schematic.schematic_rect().y() + branch.y * stage.schematic.schematic_rect().height() / height)))
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
    # The old Ln=5.5/Np=30 example is physically outside the unchanged 20%
    # FHA/TD gate. A coarse RK4 orbit used to understate its magnetizing peak.
    rejected_config = OptimizationConfig((5.5,), (spec.q_full_load,), (spec.resonant_frequency_hz,),
        (30,), (4,), (spec.primary_device,),
        (spec.sr_parallel_devices_per_position,))
    view.config_text.setPlainText(json.dumps(asdict(rejected_config)))
    view.maximum_candidates.setValue(1)
    view.top_n.setValue(1)
    _require(view.verification_scope.currentData() == "nominal", "unexpected TwoStage verification scope")
    view.run_button.click()
    _wait_for_worker(lambda: not host._active_workers)
    _require(view.result is not None and len(view.result.verified) == 1, view.status.text())
    rejected = view.result.verified[0]
    _require(rejected.rejected and rejected.fha_td_validity.value == "FAIL"
             and rejected.reject_reason == "FHA↔TD MODEL_VALIDITY FAIL"
             and view.selected_candidate() is None and not view.apply_button.isEnabled()
             and not view.report_button.isEnabled() and host._spec_from_widgets() == spec,
             "TwoStage accepted the independently invalid coarse-grid example")
    rejected_proof = {"ln_ratio": 5.5, "primary_turns": 30, "secondary_turns": 4,
        "fha_td_validity": rejected.fha_td_validity.value, "reject_reason": rejected.reject_reason,
        "diagnostics": list(rejected.diagnostics), "apply_disabled": True, "project_unchanged": True}
    # An independently integrated full-bridge case has ~13% Im deviation,
    # leaving margin to the same 20% gate; no model threshold is waived.
    config = OptimizationConfig((4.5,), (spec.q_full_load,), (spec.resonant_frequency_hz,),
        (32,), (4,), (spec.primary_device,), (spec.sr_parallel_devices_per_position,))
    view.config_text.setPlainText(json.dumps(asdict(config)))
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
    _require(host.spec == host._spec_from_widgets() == candidate.spec and host.spec.ln_ratio == 4.5
             and host.spec.primary_turns == 32 and host.spec.secondary_turns == 4
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
    _require(host.spec.ln_ratio == 4.5 and host.spec.primary_turns == 32
             and view.result is None and not view.apply_button.isEnabled()
             and not view.report_button.isEnabled() and "历史" in view.status.text(), "restored history authorizes stale Apply/report")
    return {"real_stage_a_and_b": True, "verification_scope": "nominal", "verified_td_points": verified_points,
        "fha_td_validity": candidate.fha_td_validity.value, "waveform_sha256": digest,
        "loss_source": nominal.semiconductor_source, "total_loss_w": candidate.total_loss_w,
        "selected_candidate": saved["candidates"][0], "applied_ln_ratio": host.spec.ln_ratio,
        "applied_primary_turns": host.spec.primary_turns, "invalid_example": rejected_proof,
        "apply_exact": True, "project_roundtrip": True, "restored_history_requires_rerun": True,
        "report_consistency": True,
        "model_limit": "Nominal-point ideal TD losses only; remaining operating points and commutation retain FHA assumptions; no hardware qualification"}


def _exercise_magnetic_catalog(host, directory, app):
    """Exercise the existing PFC editor, not a detached calculator or fake solver."""
    import math
    from dataclasses import fields

    import numpy as np
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication, QMessageBox

    from llc_design.gui.widgets.magnetic_catalog_view import MagneticCatalogDialog
    from pfc_design.magnetics.manufacturer_catalog import CATALOG_PATH
    from pfc_design.magnetics.supplemental_catalog import PATH as SUPPLEMENTAL_PATH

    workbench = host.control_lab_view
    control = workbench.control_lab
    editor = control.inductor_editor
    selector = editor.source_selector
    devices = workbench.device_loss_view
    workbench.tabs.setCurrentWidget(control)
    control.input_tabs.setCurrentWidget(editor)
    app.processEvents()
    _require(editor.isVisible(), "Existing PFC workspace did not expose its magnetic editor")
    for widget, value in ((control.vin_rms, 230), (control.vbus, 400),
                          (control.pout, 1000), (control.fsw, 120), (control.inductance, 200)):
        widget.setValue(value)
    editor.sync_from_stage()
    editor.target_l.setValue(200)
    editor.n_cores.setValue(2)
    editor.eff.setValue(.97)
    editor.material_path.setCurrentIndex(editor.material_path.findData("source_catalog"))
    selector.search.setText("CS400060")
    record_id = "csc2025:part:CS400060:054"
    _require(selector.parts.count() == 1 and selector.parts.currentData() == record_id,
             "PFC source selector did not find the exact published part")
    selected = selector.selection
    _require(not editor.run_button.isEnabled() and not editor.apply_button.isEnabled()
             and selector.declaration is None and selected.full_pfc_blockers(),
             "Undeclared magnetic source was treated as a qualified full PFC model")
    selector.turns.setValue(37)
    selector.stack.setValue(2)
    selector.reference_button.click()
    reference = selector.reference_result
    _require(reference is not None and reference["selection_identity"] == selected.identity
             and math.isclose(reference["l_zero_bias_uh"], 221.778, rel_tol=1e-12),
             "Magnetic reference failed independent 81 nH * 37^2 * 2 check")
    reference_path = directory / "magnetic-reference.json"
    editor.save_magnetic_to(reference_path)
    reference_document = json.loads(reference_path.read_text(encoding="utf-8"))
    _require(reference_document["selection"] == selected.snapshot
             and reference_document["pfc_inputs"] is None
             and reference_document["user_model_declaration"] is None,
             "Reference-only project invented a full application model")
    selector.search.setText("CM035060")
    _require(selector.reference_result is None and selector.selection.identity != selected.identity,
             "Magnetic reference did not invalidate when selection changed")
    editor.open_magnetic_from(reference_path)
    _require(selector.selection.identity == selected.identity and selector.reference_result == reference
             and not editor.run_button.isEnabled(), "Reference project did not recompute the original source")
    reference_report = directory / "magnetic-reference.html"
    editor.report_magnetic_to(reference_report)
    reference_html = reference_report.read_text(encoding="utf-8")
    _require(selected.identity in reference_html and record_id in reference_html
             and json.loads(unescape(reference_html.split("<pre>", 1)[1].split("</pre>", 1)[0])) == reference,
             "Reference report did not preserve exact values and provenance")

    # These are deliberately QA-only application assumptions. They must remain
    # separate from the immutable manufacturer snapshot, in projects and reports.
    declaration = {"B_convention": "peak_ac", "waveform": "sinusoidal_reference",
        "dimension_basis": "after_coating_limits", "source": "QA-only assumptions; not manufacturer qualification",
        "H_Oe": [0, 1000], "B_T": [0, 1], "frequency_Hz": [5000, 300000],
        "temperature_C": [20, 100], "core_temperature_C": 25}
    selector.declaration = declaration
    selector.selection_changed.emit()
    _require(editor.run_button.isEnabled(), "Explicit QA-only declaration did not enable the reference solver")
    def click_with_acknowledgements(button):
        # Dismiss native dialogs only; computation and application stay real.
        acknowledgements = []
        timer = QTimer(host)
        def dismiss_acknowledgement():
            modal = QApplication.activeModalWidget()
            if isinstance(modal, QMessageBox):
                acknowledgements.append(modal.windowTitle() + (": " + modal.detailedText() if modal.detailedText() else ""))
                modal.accept()
        timer.timeout.connect(dismiss_acknowledgement)
        timer.start(25)
        try:
            button.click()
        finally:
            timer.stop(); timer.deleteLater()
        return acknowledgements
    issues = click_with_acknowledgements(editor.run_button)
    _require(not issues, f"Real PFC Calculate reported a modal error: {issues}")
    result = editor.last_result
    _require(result is not None and result.inductance_target_met and result.window_ok
             and result.request.source_snapshot == selected.snapshot,
             "Real PFC source calculation failed or substituted another core")
    core_identity = f"Chang Sung Corporation CS400060 [{record_id}]"
    loss_identity = core_identity + "; user-declared reference model"
    _require(result.request.core.core_data == core_identity
             and devices._inductor_model_identity == loss_identity
             and devices._inductor_self_loss_w == result.total_loss_w
             and "CS400060" in control.inductor_result_view.summary.toPlainText(),
             "PFC displayed core or actual Devices / Loss consumer has a different identity")
    result_fields = ("turns", "parallel_wires", "l_no_bias_uh", "l_full_load_peak_uh", "h_peak_oe",
                     "full_load_peak_with_ripple_a", "phase_current_rms_a", "total_current_rms_with_ripple_a",
                     "core_loss_w", "copper_loss_w", "total_loss_w", "rdc_hot_ohm", "fill_factor", "b_ac_line_max_t")
    measured = {name: getattr(result, name) for name in result_fields}
    curve_fields = ("current_a", "inductance_uh", "line_angle_deg", "line_b_ac_t", "line_core_loss_w", "line_ripple_pp_a")
    arrays = {name: getattr(result, name).copy() for name in curve_fields}
    digest = hashlib.sha256()
    for name, values in arrays.items():
        _require(np.isfinite(values).all(), "PFC source calculation returned a nonfinite curve")
        digest.update(name.encode()); digest.update(np.asarray(values, dtype="<f8").tobytes())
    # Independently expand the selected printed double-term loss equation.
    # This verifies formula routing, not application/waveform qualification.
    coefficients = selected.snapshot["loss_model"]["coefficients"]
    b_power = (10 * arrays["line_b_ac_t"]) ** coefficients["beta"]
    frequency_khz = result.request.switching_frequency_hz / 1000
    rate = coefficients["a"] * frequency_khz + coefficients["b"] * frequency_khz ** coefficients["gamma"]
    expected_core_curve = b_power * rate * selected.geometry["ve_cm3"] * result.request.n_cores / 1000
    np.testing.assert_allclose(arrays["line_core_loss_w"], expected_core_curve, rtol=1e-12, atol=1e-12)
    _require(math.isclose(result.core_loss_w, float(np.mean(expected_core_curve)), rel_tol=1e-12),
             "PFC core loss does not use the selected source double-term expression")
    printed_loss_check = {"b_kg_beta_mean": float(np.mean(b_power)),
                         "max_curve_absolute_error_w": float(np.max(abs(arrays["line_core_loss_w"] - expected_core_curve)))}
    pfc_path = directory / "magnetic-pfc.json"
    editor.save_magnetic_to(pfc_path)
    document = json.loads(pfc_path.read_text(encoding="utf-8"))
    expected_inputs = {field.name: getattr(result.request, field.name) for field in fields(result.request)
                       if field.name not in {"core", "material", "source_snapshot", "model_declaration"}}
    _require(document["pfc_inputs"] == expected_inputs and document["selection"] == selected.snapshot
             and document["user_model_declaration"] == declaration,
             "Full PFC project lost source/declaration/input identity")
    acknowledgements = click_with_acknowledgements(editor.apply_button)
    _require(acknowledgements == ["TTPL 电感参数已应用"]
             and control.inductance.value() == round(result.l_full_load_peak_uh, control.inductance.decimals())
             and control.dcr.value() == round(result.rdc_hot_ohm * 1000, control.dcr.decimals()),
             "Real Apply did not write the calculated L / DCR to the PFC power stage")
    applied = {"inductance_uh": control.inductance.value(), "dcr_mohm": control.dcr.value()}
    editor.target_l.setValue(210)
    _require(editor.last_result is None and not editor.apply_button.isEnabled()
             and not control.inductor_result_view.figure.axes
             and devices._inductor_self_loss_w is None and devices._inductor_model_identity is None,
             "Changed source inputs left stale results, plots or component loss")
    editor.open_magnetic_from(pfc_path)
    restored = editor.last_result
    _require(restored is not None and restored is not result and editor.magnetic_document() == document
             and {name: getattr(restored, name) for name in result_fields} == measured
             and devices._inductor_model_identity == loss_identity
             and devices._inductor_self_loss_w == result.total_loss_w,
             "Full PFC Open did not recompute and reconnect the same source")
    for name, values in arrays.items():
        np.testing.assert_array_equal(getattr(restored, name), values)
    pfc_report = directory / "magnetic-pfc.html"
    editor.report_magnetic_to(pfc_report)
    pfc_html = pfc_report.read_text(encoding="utf-8")
    sections = [json.loads(unescape(section.split("</pre>", 1)[0])) for section in pfc_html.split("<pre>")[1:]]
    expected_output = {"core_identity": core_identity, "turns": result.turns,
        "l_zero_bias_uh": result.l_no_bias_uh, "l_full_load_peak_uh": result.l_full_load_peak_uh,
        "core_loss_reference_w": result.core_loss_w, "copper_dc_loss_w": result.copper_loss_w,
        "fill_factor": result.fill_factor, "h_peak_oe": result.h_peak_oe,
        "b_ac_peak_t": result.b_ac_line_max_t, "warnings": list(result.warnings)}
    _require(sections == [reference, expected_output, selected.snapshot,
                          {k: v for k, v in document.items() if k != "selection"}],
             "Full PFC report mixed computed values, source snapshot or QA-only declaration")

    # LLC exposes source powder records for reference, never as a complete
    # ferrite/bobbin preset. The companion case is original arithmetic only.
    dialog = MagneticCatalogDialog(host)
    try:
        dialog.show()
        app.processEvents()
        dialog.search.setText("CS400060")
        dialog.tabs.setCurrentIndex(2)
        dialog.pdf_reference.selectRow(0)
        _require(dialog.pdf_keys == [record_id] and not dialog.apply.isEnabled()
                 and "不是完整 LLC" in dialog.note.text(), "LLC offered a source ring as a complete bobbin preset")
        browser = dialog.supplemental
        dialog.tabs.setCurrentWidget(browser)
        browser.search.setText("APP-023")
        _require(browser.records.count() == 1 and browser.replay.isEnabled(), "Supplemental APP-023 source is absent")
        browser.replay.click()
        replay = json.loads(browser.text.toPlainText())
        _require(replay["source_case"] == "APP-023" and any("正确性基准" in note for note in replay["engineering_cautions"]),
                 "APP-023 omitted its source-formula engineering caveat")
    finally:
        dialog.close(); dialog.deleteLater(); app.processEvents()
    resource_sha = {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                    for path in (CATALOG_PATH, SUPPLEMENTAL_PATH)}
    return {"resource_sha256": resource_sha, "record_id": record_id, "selection_identity": selected.identity,
        "selection": selected.snapshot, "source_documents_sha256": {d["filename"]: d["sha256"] for d in selected.snapshot["source_documents"]},
        "reference": {"inputs": reference_document["reference_inputs"], "result": reference,
                      "default_full_calculate_disabled": True, "project_roundtrip": True, "report_consistency": True},
        "pfc": {"scope": "QA-only user-declared reference model; not manufacturer qualification",
            "inputs": expected_inputs, "user_model_declaration": declaration, "core_identity": core_identity,
            "selection_identity": selected.identity, "results": measured, "curve_sha256": digest.hexdigest(),
            "printed_loss_check": printed_loss_check,
            "curve_samples": {name: len(values) for name, values in arrays.items()},
            "self_loss_identity": loss_identity, "self_loss_w": devices._inductor_self_loss_w,
            "applied": applied, "real_calculate_and_apply": True, "project_roundtrip": True,
            "recomputed_curves_identical": True, "stale_consumers_cleared": True, "report_consistency": True},
        "llc_reference_only": {"record_id": record_id, "apply_disabled": True},
        "supplemental": {"source_case": replay["source_case"], "source_arithmetic_only": True,
                         "solver_baseline": False, "Lpfc_min_uH": replay["Lpfc_min_uH"],
                         "engineering_cautions": replay["engineering_cautions"]}}


def _exercise_integrated_comparison(host, app, spec_identity):
    """Real default-resolution HB and cooperative stop paths; no solver mocks."""
    from time import monotonic

    import numpy as np
    from PySide6.QtCore import QTimer

    from llc_design.analysis import execution
    from llc_design.analysis.types import FidelityLevel
    from llc_design.core.config import waveform_spec_identity

    origin = Path(execution.__file__).resolve()
    if getattr(sys, "frozen", False):
        _require(origin.is_relative_to(Path(sys._MEIPASS).resolve()),
                 "comparison execution controls were imported outside the frozen bundle")
    view = host.model_comparison_view
    host.tabs.setCurrentWidget(view)
    _require(view.max_harmonic.value() == 7 and view.hb_samples.value() == 1024
             and view.execution_budget_s.value() == 300., "comparison defaults were weakened")
    view.include_td.setChecked(False)
    view.apply_td_losses.setChecked(False)
    app.processEvents()
    options = view.options()
    _require(options["vbus_v"] == 400. and options["load_fraction"] == 1., "comparison work point changed")
    started = monotonic()
    view.run_button.click()
    _require(bool(host._active_workers) and view.cancel_button.isEnabled(), "comparison did not start a real worker")
    control = host._comparison_control
    _wait_for_worker(lambda: not host._active_workers and host.thread_pool.activeThreadCount() == 0, seconds=60)
    elapsed = monotonic()-started
    result = host.multi_fidelity_analysis
    _require(result is not None and view.analysis is result and control.outcome is None
             and host._comparison_control is None and host._results_current,
             "default comparison did not publish its complete current result")
    _require(set(result.results) == {FidelityLevel.FHA, FidelityLevel.HARMONIC_BALANCE}
             and result.reference_level == FidelityLevel.HARMONIC_BALANCE
             and waveform_spec_identity(result.request.spec) == spec_identity
             and result.request.samples_per_cycle == 1024 and result.request.waveform_cycles == 2,
             "comparison used a different spec, reduced resolution or unexpected TD")
    hb = result.result(FidelityLevel.HARMONIC_BALANCE)
    _require(hb.convergence.converged and hb.convergence.residual_norm <= 2e-6
             and hb.harmonic_orders == (1, 3, 5, 7), "default HB did not converge at its requested resolution")
    metrics = hb.metrics.as_dict()
    np.testing.assert_allclose([metrics["switching_frequency_hz"], metrics["output_voltage_v"],
        metrics["resonant_current_rms_a"]], [113123.35618195974, 53., 8.570744923304485], rtol=1e-6, atol=1e-8)
    digest = hashlib.sha256(np.asarray(hb.waveform.time_s, dtype="<f8").tobytes())
    _require(len(hb.waveform.time_s) == 2048, "HB output was downsampled")
    for key, signal in sorted(hb.waveform.signals.items()):
        _require(np.isfinite(signal.values).all(), "HB produced a nonfinite waveform")
        digest.update(key.encode()); digest.update(np.asarray(signal.values, dtype="<f8").tobytes())
    rms = float(np.sqrt(np.mean(hb.waveform.signals["i_resonant"].values**2)))
    np.testing.assert_allclose(rms, metrics["resonant_current_rms_a"], rtol=1e-12, atol=1e-12)
    _require(view.table.rowCount() == 2 and view.table.item(1, 0).text() == "harmonic_balance"
             and view.table.item(1, 4).text() == f"{metrics['switching_frequency_hz']/1e3:.6f}"
             and view.table.item(1, 5).text() == f"{metrics['output_voltage_v']:.6f}",
             "comparison table differs from actual HB numbers")
    success = {"converged": True, "reference": result.reference_level.value,
        "models": sorted(level.value for level in result.results), "spec_identity": spec_identity,
        "harmonic_orders": list(hb.harmonic_orders), "samples": len(hb.waveform.time_s),
        "residual_norm": float(hb.convergence.residual_norm),
        "exact_sign_residual_norm": float(hb.waveform.metadata["exact_sign_residual_norm"]),
        "switching_frequency_hz": metrics["switching_frequency_hz"], "output_voltage_v": metrics["output_voltage_v"],
        "resonant_current_rms_a": rms, "waveform_sha256": digest.hexdigest(),
        "gui_table_matches": True, "active_workers": len(host._active_workers), "active_threads": host.thread_pool.activeThreadCount(), "elapsed_s": elapsed}
    stopped = {}
    for name, budget, expected, wording in (("cancel", 300., "cancelled", "已取消"),
                                             ("timeout", .1, "timed_out", "计算超时")):
        view.execution_budget_s.setValue(budget)
        started = monotonic()
        view.run_button.click()
        control = host._comparison_control
        _require(control is not None and bool(host._active_workers) and view.cancel_button.isEnabled(),
                 "comparison stop test did not start a real worker")
        timer = QTimer(host)
        timer.setSingleShot(True)
        if name == "cancel":
            timer.timeout.connect(view.cancel_button.click)
            timer.start(100)
        try:
            _wait_for_worker(lambda: not host._active_workers and host.thread_pool.activeThreadCount() == 0, seconds=20)
        finally:
            timer.stop(); timer.deleteLater()
        elapsed = monotonic()-started
        message = view.details.toPlainText()
        _require(control.outcome == expected and wording in message and wording in host.statusBar().currentMessage()
                 and host._comparison_control is None and host.multi_fidelity_analysis is None
                 and view.analysis is None and view.table.rowCount() == 0 and not host._results_current
                 and host._accepted_model_options is None and view.run_button.isEnabled()
                 and not view.cancel_button.isEnabled() and not view.loss_report_button.isEnabled(),
                 "comparison stop published stale/partial results or retained worker authority")
        host._export_td_loss_report()
        _require(not host._active_workers, "stopped comparison still authorized a report job")
        _require(view.max_harmonic.value() == 7 and view.hb_samples.value() == 1024,
                 "comparison stop silently reduced numerical resolution")
        stopped[name] = {"outcome": control.outcome, "budget_s": control.budget_s,
            "elapsed_s": elapsed, "active_workers": len(host._active_workers),
            "active_threads": host.thread_pool.activeThreadCount(), "control_released": True,
            "no_partial_result": True, "rows": view.table.rowCount(), "run_enabled": view.run_button.isEnabled(),
            "report_disabled": not view.loss_report_button.isEnabled(), "status_visible": True}
    return {"execution_module": str(origin), "options": options, "success": success, **stopped}


def _exercise_integrated_magnetics(host, directory, app):
    """Exercise real matrix/geometry, FHA GUI, save/restore and report consumers.

    All characterization constants are synthetic software fixtures. Geometry
    reachability and solved FHA points are not hardware or envelope approval.
    """
    from types import SimpleNamespace
    from unittest.mock import patch

    import numpy as np
    from PySide6.QtWidgets import QFileDialog

    from llc_design.core import integrated_magnetics
    from llc_design.core.config import load_spec, waveform_spec_identity
    from llc_design.core.spec import LLCDesignSpec, MagneticsMode
    from llc_design.magnetics import leakage_geometry
    from llc_design.report.export import (
        export_calculation_book,
        integrated_magnetic_record,
    )

    modules = {module.__name__: str(Path(module.__file__).resolve())
               for module in (integrated_magnetics, leakage_geometry)}
    if getattr(sys, "frozen", False):
        bundle_root = Path(sys._MEIPASS).resolve()
        _require(all(Path(path).is_relative_to(bundle_root) for path in modules.values()),
                 "integrated magnetic modules were imported outside the frozen bundle")
    golden = integrated_magnetics.IntegratedMagnetics(100e-6, 25e-6, 40e-6, 2.)
    golden_values = {"lr_h": golden.lr_h, "lm_h": golden.lm_h,
        "effective_turns_ratio": golden.effective_turns_ratio,
        "physical_magnetizing_h": golden.physical_magnetizing_h,
        "primary_leakage_h": golden.primary_leakage_h, "secondary_leakage_h": golden.secondary_leakage_h,
        "physical_current_a": golden.physical_magnetizing_current(3., 1.),
        "flux_density_t": golden.flux_density(3., 1., 20, 100e-6)}
    np.testing.assert_allclose(list(golden_values.values()),
        [36e-6, 64e-6, 1.6, 80e-6, 20e-6, 5e-6, 1.4, .056], rtol=1e-12, atol=1e-15)
    mu, sigma, ratio = 300e-6, 3.5e-6, 7.5
    spec = LLCDesignSpec(magnetics_mode=MagneticsMode.INTEGRATED,
        integrated_characterized_primary_turns=30, integrated_characterized_secondary_turns=4,
        integrated_characterized_core="PQ50_REF", integrated_lp_h=mu+sigma,
        integrated_ls_h=(mu+sigma)/ratio**2, integrated_mutual_h=mu/ratio,
        integrated_interwinding_gap_m=.0010325249820337418,
        integrated_measurement_note="Synthetic two-leakage artifact QA model; NOT a hardware measurement")
    # A fixed independent winding-field counterexample: even exact electrical
    # agreement must fail a 3 mm window when the build needs 4.8 mm.
    target = 2.178170906488923e-6
    counter_spec = spec.clone(primary_turns=20, secondary_turns=10,
        integrated_characterized_primary_turns=20, integrated_characterized_secondary_turns=10,
        integrated_lp_h=80e-6+target/2, integrated_ls_h=(80e-6+target/2)/4,
        integrated_mutual_h=40e-6, integrated_interwinding_gap_m=.0014)
    counter_design = SimpleNamespace(primary_turns=20, secondary_turns=10,
        primary_turns_per_layer=10, secondary_turns_per_layer=10,
        primary_wire=SimpleNamespace(equivalent_outer_diameter_m=.0005),
        secondary_wire=SimpleNamespace(equivalent_outer_diameter_m=.001),
        core=SimpleNamespace(window_width_m=.02, window_height_mm=3.,
                             mlt_primary_mm=100., mlt_secondary_mm=100.))
    counter = leakage_geometry.screen_integrated_leakage(counter_spec, counter_design)
    np.testing.assert_allclose([counter.required_interwinding_gap_m, counter.required_radial_build_m,
                               counter.balanced_geometry_h], [.0014, .0048, target], rtol=1e-5, atol=1e-15)
    _require(not counter.reachable and counter.required_radial_build_m > counter.available_window_height_m,
             "integrated geometry accepted a winding wider than its window")

    host.spec = spec
    host._load_spec_to_widgets(spec)
    host.waveform_view.solver = "fast"
    host.waveform_view.select_component("transformer")
    app.processEvents()
    _require(host._spec_from_widgets() == spec, "integrated GUI changed exact characterization")
    host.run_design()
    _wait_for_worker(lambda: not host._active_workers)
    first = host.system_analysis
    _require(first is not None and first.spec == spec and first.resonant_inductor is None,
             "integrated GUI did not solve the shared transformer")
    project = directory / "integrated-llc.json"
    with patch.object(QFileDialog, "getSaveFileName", return_value=(str(project), "JSON")):
        host.save_json()
    _require(load_spec(project) == spec, "integrated project changed characterization")
    # Changing the mode must invalidate consumers before a real load/recompute.
    host.magnetics_mode_combo.setCurrentIndex(host.magnetics_mode_combo.findData(MagneticsMode.SEPARATE))
    _require(host.system_analysis is None and host.loss_summary_view._analysis is None,
             "integrated GUI retained stale shared-magnetic consumers")
    with patch.object(QFileDialog, "getOpenFileName", return_value=(str(project), "JSON")):
        host.load_json()
    _wait_for_worker(lambda: not host._active_workers)
    _require(host.spec == host._spec_from_widgets() == spec,
             "integrated project restore changed exact characterization")
    host.run_design()
    _wait_for_worker(lambda: not host._active_workers)
    analysis = host.system_analysis
    _require(analysis is not None and analysis is not first and analysis == first,
             "integrated restore did not recompute identical FHA analysis")
    _require(host.loss_summary_view._analysis is analysis and host.loss_summary_view.point.currentData() is analysis.nominal,
             "integrated loss-summary consumer has a different analysis")
    _require("无独立磁芯" in host.summary_text.toPlainText(), "integrated GUI omitted shared-magnetic status")
    _require(analysis.resonant_inductor is None and all(p.resonant_inductor.total_w == 0 for p in analysis.operating_points),
             "integrated analysis double-counted an external Lr")
    model = integrated_magnetics.from_spec(analysis.spec)
    electrical = {"lr_h": analysis.tank.lr_h, "lm_h": analysis.tank.lm_h,
        "effective_turns_ratio": analysis.spec.electrical_turns_ratio,
        "physical_turns_ratio": analysis.spec.turns_ratio, "physical_magnetizing_h": model.physical_magnetizing_h,
        "primary_leakage_h": model.primary_leakage_h, "secondary_leakage_h": model.secondary_leakage_h}
    np.testing.assert_allclose(list(electrical.values()),
        [6.9596375617793165e-6, .0002965403624382207, 7.413509060955519,
         7.5, .0003, 3.5e-6, 3.5e-6/7.5**2], rtol=1e-11, atol=1e-15)
    geometry = analysis.transformer.integrated_leakage
    _require(analysis.transformer.core.part_number == "PQ50_REF" and geometry.reachable,
             "integrated reference core/geometry changed")
    np.testing.assert_allclose([geometry.balanced_geometry_h, geometry.characterized_balanced_h,
        geometry.required_interwinding_gap_m, geometry.required_radial_build_m, geometry.available_window_height_m],
        [7e-6, 7e-6, .0010325249820337418, .015691021378672478, .017], rtol=1e-10, atol=1e-15)
    report_paths = export_calculation_book(analysis, directory / "integrated-report")
    exported = json.loads(report_paths["result_json"].read_text(encoding="utf-8"))
    record = integrated_magnetic_record(spec)
    identity = waveform_spec_identity(spec)
    _require(exported["spec"] == json.loads(json.dumps(asdict(spec)))
             and exported["integrated_magnetics"] == record and exported["resonant_inductor"] is None
             and exported["tank"] == asdict(analysis.tank)
             and exported["transformer"]["integrated_leakage"] == json.loads(json.dumps(asdict(geometry)))
             and exported["analysis"]["nominal"]["total_loss_w"] == analysis.nominal.total_loss_w
             and not (report_paths["result_json"].parent / "resonant_inductor_core_candidates.csv").exists(),
             "integrated report mixed source identity or added an external Lr")
    markdown = report_paths["report"].read_text(encoding="utf-8")
    _require(spec.integrated_measurement_note in markdown and geometry.status in markdown,
             "integrated report lost characterization or geometry caveats")
    for row in exported["operating_points"]:
        _require(row["external_resonant_inductor_present"] is False
                 and row["resonant_inductor_core_w"] == row["resonant_inductor_copper_w"] == 0,
                 "integrated report contains separate Lr losses")
    evidence = {"scope": "Synthetic characterization; FHA GUI/report QA, not hardware or full-envelope qualification",
        "modules": modules, "golden": golden_values,
        "geometry_counterexample": {"reachable": counter.reachable,
            "required_interwinding_gap_m": counter.required_interwinding_gap_m,
            "required_radial_build_m": counter.required_radial_build_m,
            "available_window_height_m": counter.available_window_height_m,
            "balanced_geometry_h": counter.balanced_geometry_h},
        "spec": json.loads(json.dumps(asdict(spec))), "spec_identity": identity,
        "electrical": electrical, "geometry": asdict(geometry), "core": analysis.transformer.core.part_number,
        "external_resonant_inductor_present": analysis.resonant_inductor is not None,
        "external_resonant_inductor_loss_w": analysis.nominal.resonant_inductor.total_w,
        "solved_points": len(analysis.operating_points), "nominal_frequency_hz": float(analysis.nominal.operating_point.switching_frequency_hz),
        "nominal_loss_w": float(analysis.nominal.total_loss_w), "loss_source": analysis.nominal.semiconductor_source,
        "is_hardware_validated": False, "all_conditions_feasible": analysis.feasible,
        "feasible": analysis.feasible, "feasibility_reasons": list(analysis.feasibility_reasons),
        "warnings": list(analysis.warnings), "gui_run_design": True, "project_roundtrip": True,
        "recomputed_analysis_identical": True, "stale_consumers_cleared": True,
        "consumers": {"summary_spec_identity": identity, "loss_spec_identity": waveform_spec_identity(host.loss_summary_view._analysis.spec),
                      "report_spec_identity": hashlib.sha256(json.dumps(exported["spec"], sort_keys=True, allow_nan=False).encode()).hexdigest()},
        "report_consistency": True}
    evidence["comparison_execution"] = _exercise_integrated_comparison(host, app, identity)
    return evidence


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
            integrated_proof = _exercise_integrated_magnetics(controller.llc_window, Path(directory), app)
            controller.show_workspace("pfc")
            app.processEvents()
            magnetic_proof = _exercise_magnetic_catalog(controller.pfc_window, Path(directory), app)
        workspace_names = ["llc", "pfc", "control", "fra", "dab", "power_stage"]
        for workspace, window in zip(workspace_names, windows, strict=True):
            controller.show_workspace(workspace)
            app.processEvents()
            if controller.active_workspace != workspace or not window.isVisible():
                raise RuntimeError(f"Packaged workspace failed to open: {workspace}")

        # This path is inside the frozen bundle, not relative to source/CWD.
        taxonomy = Path(part_validation.__file__).resolve().parents[1] / "engineering_data/brand_taxonomy.json"
        part_validation.load_brand_taxonomy()
        proof = {"proof_schema": 4, "success": True, "version": __version__,
                 "frozen": bool(getattr(sys, "frozen", False)), "platform": sys.platform,
                 "workspaces": workspace_names, "guided_design": True, **psd_proof,
                 "power_stage_advanced": advanced_proof, "two_stage_optimization": optimizer_proof,
                 "power_stage_priority": priority_proof, "magnetic_catalog": magnetic_proof,
                 "integrated_magnetics": integrated_proof,
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

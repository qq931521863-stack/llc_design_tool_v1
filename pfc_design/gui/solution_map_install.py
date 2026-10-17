"""Connect controller candidates to the maintained TTPL loop model."""
from __future__ import annotations

from dataclasses import asdict, replace
from types import MethodType

import numpy as np
from PySide6.QtWidgets import QWidget

from llc_design.control.candidate_evidence import (
    build_candidate_evidence, check_evidence,
    invalidate_evidence, ttpl_model_signature, validate_saved_evidence,
)
from llc_design.control.digital_loop import ControllerKind, PIControllerConfig
from llc_design.gui.solution_map_install import (
    _candidate_source_models,
    _guard_loop_completions,
    _merge_model_edits,
    _model_signature,
    _remove_controller,
    _show_candidate_evidence,
    _watch_input,
)
from llc_design.gui.widgets.solution_map_view import LoopMapSource, SolutionMapView


def _ttpl_source_compatibility(config, loop_key: str) -> str:
    stage = asdict(config.power_stage)
    for name in ("vin_rms_v", "bus_voltage_v", "output_power_w", "line_angle_deg"):
        stage.pop(name)
    # The outer-loop plant contains the closed current loop. An inner PI change
    # must therefore invalidate retained outer-loop responses.
    inner = config.current_controller if loop_key == "voltage" else None
    return _model_signature(
        loop_key, stage, config.firmware, config.current_sense,
        config.vac_sense, config.vbus_sense, inner,
    )


def ttpl_sources(analysis, *, provenance="TTPL maintained analysis") -> list[LoopMapSource]:
    """Build sources only from a newly completed maintained-model result."""
    from llc_design.control.design_session import encode_model, ttpl_inputs
    model_inputs = encode_model(ttpl_inputs(analysis))
    f = np.asarray(analysis.frequencies_hz, dtype=float)
    ci = analysis.current_loop.responses
    cv = analysis.voltage_loop.responses
    current_band = f < 0.5 / analysis.current_loop.controller.sample_time_s
    voltage_band = f < 0.5 / analysis.voltage_loop.controller.sample_time_s
    stage = analysis.config.power_stage
    operating_point = (f"Vin={stage.vin_rms_v:.9g} Vrms; Vbus={stage.bus_voltage_v:.9g} V; "
                       f"Pout={stage.output_power_w:.9g} W; line phase={stage.line_angle_deg:.9g}°")
    return [
        LoopMapSource(
            key="current", label="Current inner loop Li", frequencies_hz=f[current_band],
            fixed_loop_response=_remove_controller(ci["open_current"], ci["controller_ci"])[current_band],
            current_loop_response=ci["open_current"][current_band],
            sample_rate_hz=1.0 / analysis.current_loop.controller.sample_time_s,
            switching_frequency_hz=analysis.config.power_stage.switching_frequency_hz,
            provenance=(provenance + " | fixed: plant + Kindu + PWM/ZOH/delay + current sense/ADC"
                        + (" | retained samples below current-loop Nyquist" if not np.all(current_band) else "")),
            operating_point=operating_point,
            compatibility_key=_ttpl_source_compatibility(analysis.config, "current"),
            model_inputs=model_inputs,
        ),
        LoopMapSource(
            key="voltage", label="Bus-voltage outer loop Lv", frequencies_hz=f[voltage_band],
            fixed_loop_response=_remove_controller(cv["open_voltage"], cv["controller_cv"])[voltage_band],
            current_loop_response=cv["open_voltage"][voltage_band],
            sample_rate_hz=1.0 / analysis.voltage_loop.controller.sample_time_s,
            switching_frequency_hz=analysis.config.power_stage.switching_frequency_hz,
            provenance=(provenance + " | fixed: closed current loop + AMC/Vrms + bus plant + Vbus sense/ADC"
                        + (" | retained samples below voltage-loop Nyquist" if not np.all(voltage_band) else "")),
            operating_point=operating_point,
            compatibility_key=_ttpl_source_compatibility(analysis.config, "voltage"),
            model_inputs=model_inputs,
        ),
    ]


def install_ttpl_solution_map(pfc_window) -> SolutionMapView:
    """Reuse Control/Bode ownership for compare → explicit Apply → exact H(z)."""
    workbench = pfc_window.control_lab_view
    control = workbench.control_lab
    if hasattr(control, "solution_map_view"):
        return control.solution_map_view

    view = SolutionMapView("TTPL Fc × PM Solution Map", parent=control)
    control.solution_map_view = view
    control.tabs.addTab(view, "Fc × PM Solution Map")
    state = {"updating": False, "revision": 0, "requested_revision": 0,
             "config": None, "displayed": None, "evidence": {}, "pending_evidence": {}}
    _guard_loop_completions(pfc_window, "_ttpl_ready", view, state)
    original_config = control._config

    def config_with_exact_values(self):
        edited = original_config()
        if state["config"] is None:
            return edited
        return _merge_model_edits(state["config"], state["displayed"], edited)

    control._config = MethodType(config_with_exact_values, control)

    def restore_config(config) -> None:
        state["revision"] += 1
        state["request_id"] += 1
        state["config"] = config
        state["displayed"] = original_config()
        state["requested_revision"] = state["revision"]
        state["pending_evidence"] = {}

    def restore_evidence(evidence) -> None:
        restored = validate_saved_evidence("ttpl", evidence)
        for item in state["evidence"].values():
            invalidate_evidence(item, "A different session was loaded.")
        state["evidence"] = restored

    def context_changed() -> None:
        for evidence in (*state["evidence"].values(), *state["pending_evidence"].values()):
            invalidate_evidence(evidence, "Candidate targets, constraints or operating-point selection changed; regenerate and Apply required.")

    view.evidence_context_changed.connect(context_changed)
    control._solution_map_restore_config = restore_config
    control._candidate_evidence = lambda: state["evidence"]
    control._restore_candidate_evidence = restore_evidence

    def invalidate(*_args, clear_corners=False) -> None:
        if not state["updating"]:
            state["revision"] += 1
            for item in (*state["evidence"].values(), *state["pending_evidence"].values()):
                invalidate_evidence(item, "TTPL inputs changed; recompute and Apply required.")
            view.invalidate("TTPL inputs changed. Run the maintained model again before comparing or applying a candidate.",
                            clear_corners=clear_corners)

    operating_or_controller = {
        control.vin_rms, control.vbus, control.pout, control.angle,
        *control.current_ctrl.values(), *control.voltage_ctrl.values(),
    }
    for widget in control.input_tabs.findChildren(QWidget):
        _watch_input(widget, lambda *_args, clear=widget not in operating_or_controller:
                     invalidate(clear_corners=clear))
    for widget in workbench.power_stage_view.findChildren(QWidget):
        _watch_input(widget, lambda *_args: invalidate(clear_corners=True))

    original_set_busy = control.set_busy

    def set_busy_with_solution_map(self, busy) -> None:
        original_set_busy(busy)
        if busy:
            state["requested_revision"] = state["revision"]
            view.invalidate("TTPL analysis is running; wait for a fresh candidate source.")

    control.set_busy = MethodType(set_busy_with_solution_map, control)
    original_set_result = control.set_result

    def set_result_with_solution_map(self, result, *, engineering_v3=None) -> None:
        analysis = result[0]
        records = {**state["evidence"], **analysis.candidate_evidence, **state["pending_evidence"]}
        for key, evidence in records.items():
            check_evidence(evidence, controller=getattr(analysis, key + "_loop").controller,
                           config=getattr(analysis.config, key + "_controller"),
                           model_signature=ttpl_model_signature(analysis.config, key, analysis.frequencies_hz))
            if state["requested_revision"] != state["revision"]:
                invalidate_evidence(evidence, "Inputs changed during analysis; recompute and Apply required.")
        analysis.candidate_evidence.update(records)
        state["evidence"] = analysis.candidate_evidence
        state["pending_evidence"] = {}
        state["updating"] = True
        try:
            original_set_result(result, engineering_v3=engineering_v3)
        finally:
            state["updating"] = False
        if state["requested_revision"] != state["revision"]:
            view.invalidate("TTPL inputs changed during analysis. Run again before applying a candidate.")
            return
        analysis = result[0]
        state["config"] = analysis.config
        state["displayed"] = original_config()
        provenance = (
            "V9.3 Guided System Definition → maintained TTPL analysis"
            if hasattr(pfc_window, "guided_system_definition") else "TTPL Expert workspace → maintained TTPL analysis"
        )
        sources = ttpl_sources(analysis, provenance=provenance)
        view.set_sources(sources)
        _show_candidate_evidence(view, state["evidence"].get(view.source_combo.currentData()))
        intent = getattr(pfc_window, "_pending_controller_intent", None)
        if intent is not None:
            pfc_window._pending_controller_intent = None
            # The Guided target describes the inner loop. The outer loop must
            # be regenerated against the newly applied inner-loop closure.
            view.source_combo.setCurrentIndex(view.source_combo.findData("current"))
            view.configure_intent(intent)
            workbench.tabs.setCurrentWidget(control)
            control.tabs.setCurrentWidget(view)
            view.result_tabs.setCurrentIndex(1)

    control.set_result = MethodType(set_result_with_solution_map, control)

    def apply_selected(loop_key: str, point) -> None:
        if (loop_key not in view.sources or not view.can_apply(point)
                or point.kp is None or point.ti_s is None or pfc_window.result is None):
            return
        analysis = pfc_window.result[0]
        config = analysis.config
        if loop_key == "current":
            old = config.current_controller
            controller = PIControllerConfig(
                kp=float(point.kp), ti_s=float(point.ti_s),
                sample_time_s=1.0 / view.sources[loop_key].sample_rate_hz,
                output_min=old.output_min, output_max=old.output_max,
            )
            config = replace(config, current_controller=controller)
            fields = control.current_ctrl
        elif loop_key == "voltage":
            old = config.voltage_controller
            controller = PIControllerConfig(
                kp=float(point.kp), ti_s=float(point.ti_s),
                sample_time_s=1.0 / view.sources[loop_key].sample_rate_hz,
                output_min=old.output_min, output_max=old.output_max,
            )
            config = replace(config, voltage_controller=controller)
            fields = control.voltage_ctrl
        else:
            return

        evidence = build_candidate_evidence(
            point=point, corners=view._active_corners(), constraints=view._constraints(),
            controller=controller, loop_key=loop_key,
            model_signature=ttpl_model_signature(config, loop_key, analysis.frequencies_hz),
            compatibility_key=view.sources[loop_key].compatibility_key,
            conditional_approved=view.allow_conditional.isChecked(),
            source_models=_candidate_source_models(view),
        )
        for item in state["evidence"].values():
            invalidate_evidence(item, "A new candidate is being applied.")
        state["pending_evidence"] = {loop_key: evidence}
        state["updating"] = True
        try:
            fields["kind"].setCurrentIndex(fields["kind"].findData(ControllerKind.PI))
            fields["kp"].setValue(controller.kp)
            fields["ti"].setValue(controller.ti_s * 1e3)
        finally:
            state["updating"] = False
        # Cache the exact authoritative config and its rounded display so a
        # repeated Run preserves H(z), including nondefault guided sample rates.
        state["config"] = config
        state["displayed"] = original_config()
        state["requested_revision"] = state["revision"]
        view.invalidate("Applying the selected PI through the maintained TTPL analysis…")
        pfc_window.run_ttpl_analysis(config)

    view.controller_selected.connect(apply_selected)
    from llc_design.gui.design_session_install import install_ttpl_session
    install_ttpl_session(pfc_window)
    return view


__all__ = ["install_ttpl_solution_map", "ttpl_sources"]

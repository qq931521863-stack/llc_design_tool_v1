"""Connect controller candidates to the maintained TTPL loop model."""
from __future__ import annotations

from dataclasses import replace
from types import MethodType

import numpy as np
from PySide6.QtWidgets import QWidget

from llc_design.control.digital_loop import ControllerKind, PIControllerConfig
from llc_design.gui.solution_map_install import (
    _guard_loop_completions,
    _merge_model_edits,
    _remove_controller,
    _watch_input,
)
from llc_design.gui.widgets.solution_map_view import LoopMapSource, SolutionMapView


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
             "config": None, "displayed": None}
    _guard_loop_completions(pfc_window, "_ttpl_ready", view, state)
    original_config = control._config

    def config_with_exact_values(self):
        edited = original_config()
        if state["config"] is None:
            return edited
        return _merge_model_edits(state["config"], state["displayed"], edited)

    control._config = MethodType(config_with_exact_values, control)

    def invalidate(*_args) -> None:
        if not state["updating"]:
            state["revision"] += 1
            view.invalidate("TTPL inputs changed. Run the maintained model again before comparing or applying a candidate.")

    for widget in control.input_tabs.findChildren(QWidget):
        _watch_input(widget, invalidate)
    for widget in workbench.power_stage_view.findChildren(QWidget):
        _watch_input(widget, invalidate)

    original_set_busy = control.set_busy

    def set_busy_with_solution_map(self, busy) -> None:
        original_set_busy(busy)
        if busy:
            state["requested_revision"] = state["revision"]
            view.invalidate("TTPL analysis is running; wait for a fresh candidate source.")

    control.set_busy = MethodType(set_busy_with_solution_map, control)
    original_set_result = control.set_result

    def set_result_with_solution_map(self, result) -> None:
        state["updating"] = True
        try:
            original_set_result(result)
        finally:
            state["updating"] = False
        if state["requested_revision"] != state["revision"]:
            view.invalidate("TTPL inputs changed during analysis. Run again before applying a candidate.")
            return
        analysis = result[0]
        state["config"] = analysis.config
        state["displayed"] = original_config()
        f = np.asarray(analysis.frequencies_hz, dtype=float)
        ci = analysis.current_loop.responses
        cv = analysis.voltage_loop.responses
        provenance = (
            "V9.3 Guided System Definition → maintained TTPL analysis"
            if hasattr(pfc_window, "guided_system_definition")
            else "TTPL Expert workspace → maintained TTPL analysis"
        )
        sources = [
            LoopMapSource(
                key="current", label="Current inner loop Li", frequencies_hz=f,
                fixed_loop_response=_remove_controller(ci["open_current"], ci["controller_ci"]),
                current_loop_response=ci["open_current"],
                sample_rate_hz=1.0 / analysis.current_loop.controller.sample_time_s,
                switching_frequency_hz=analysis.config.power_stage.switching_frequency_hz,
                provenance=provenance + " | fixed: plant + Kindu + PWM/ZOH/delay + current sense/ADC",
            ),
            LoopMapSource(
                key="voltage", label="Bus-voltage outer loop Lv", frequencies_hz=f,
                fixed_loop_response=_remove_controller(cv["open_voltage"], cv["controller_cv"]),
                current_loop_response=cv["open_voltage"],
                sample_rate_hz=1.0 / analysis.voltage_loop.controller.sample_time_s,
                switching_frequency_hz=analysis.config.power_stage.switching_frequency_hz,
                provenance=provenance + " | fixed: closed current loop + AMC/Vrms + bus plant + Vbus sense/ADC",
            ),
        ]
        view.set_sources(sources)
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
        if (loop_key not in view.sources or not point.feasible
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
    return view


__all__ = ["install_ttpl_solution_map"]

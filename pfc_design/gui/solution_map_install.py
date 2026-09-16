"""Connect the V9.3 Fc × PM Solution Map to the maintained TTPL loop model."""
from __future__ import annotations

from dataclasses import replace
from types import MethodType

import numpy as np

from llc_design.control.digital_loop import ControllerKind, PIControllerConfig
from llc_design.gui.widgets.solution_map_view import LoopMapSource, SolutionMapView


def _remove_controller(open_loop: np.ndarray, controller: np.ndarray) -> np.ndarray:
    fixed = np.zeros_like(open_loop, dtype=complex)
    np.divide(open_loop, controller, out=fixed, where=np.abs(controller) > 1e-30)
    return fixed


def install_ttpl_solution_map(pfc_window) -> SolutionMapView:
    """Install one map tab inside TTPL Control/Sensing/Bode results.

    The map consumes the exact result just produced by ``build_pfc_control_lab_analysis``.
    It removes only Ci(z) or Cv(z); every other block remains unchanged.
    """
    workbench = pfc_window.control_lab_view
    control = workbench.control_lab
    if hasattr(control, "solution_map_view"):
        return control.solution_map_view

    view = SolutionMapView("TTPL Fc × PM Solution Map", parent=control)
    control.solution_map_view = view
    control.tabs.addTab(view, "Fc × PM Solution Map")

    original_set_result = control.set_result

    def set_result_with_solution_map(self, result) -> None:
        original_set_result(result)
        analysis = result[0]
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
                key="current",
                label="Current inner loop Li",
                frequencies_hz=f,
                fixed_loop_response=_remove_controller(ci["open_current"], ci["controller_ci"]),
                sample_rate_hz=1.0 / analysis.current_loop.controller.sample_time_s,
                switching_frequency_hz=analysis.config.power_stage.switching_frequency_hz,
                provenance=provenance + " | fixed: plant + Kindu + PWM/ZOH/delay + current sense/ADC",
            ),
            LoopMapSource(
                key="voltage",
                label="Bus-voltage outer loop Lv",
                frequencies_hz=f,
                fixed_loop_response=_remove_controller(cv["open_voltage"], cv["controller_cv"]),
                sample_rate_hz=1.0 / analysis.voltage_loop.controller.sample_time_s,
                switching_frequency_hz=analysis.config.power_stage.switching_frequency_hz,
                provenance=provenance + " | fixed: closed current loop + AMC/Vrms + bus plant + Vbus sense/ADC",
            ),
        ]
        view.set_sources(sources)

    control.set_result = MethodType(set_result_with_solution_map, control)

    def apply_selected(loop_key: str, point) -> None:
        # Diagnostic/non-feasible points remain inspectable but are never
        # allowed to overwrite the maintained controller configuration.
        if not point.feasible or point.kp is None or point.ti_s is None:
            return
        if pfc_window.result is None:
            return
        analysis = pfc_window.result[0]
        config = analysis.config
        if loop_key == "current":
            old = config.current_controller
            controller = PIControllerConfig(
                kp=float(point.kp), ti_s=float(point.ti_s),
                sample_time_s=1.0 / view.sources[loop_key].sample_rate_hz,
                output_min=float(getattr(old, "output_min", -2.0)),
                output_max=float(getattr(old, "output_max", 0.98)),
            )
            config = replace(config, current_controller=controller)
            fields = control.current_ctrl
        elif loop_key == "voltage":
            old = config.voltage_controller
            controller = PIControllerConfig(
                kp=float(point.kp), ti_s=float(point.ti_s),
                sample_time_s=1.0 / view.sources[loop_key].sample_rate_hz,
                output_min=float(getattr(old, "output_min", -1.0)),
                output_max=float(getattr(old, "output_max", 40.0)),
            )
            config = replace(config, voltage_controller=controller)
            fields = control.voltage_ctrl
        else:
            return

        index = fields["kind"].findData(ControllerKind.PI)
        if index >= 0:
            fields["kind"].setCurrentIndex(index)
        fields["kp"].setValue(float(point.kp))
        fields["ti"].setValue(float(point.ti_s) * 1e3)
        # Run from the exact previous analysis config rather than rebuilding a
        # default GUI config, so guided ADC/sampling/timing values are preserved.
        pfc_window.run_ttpl_analysis(config)

    view.controller_selected.connect(apply_selected)
    return view


__all__ = ["install_ttpl_solution_map"]

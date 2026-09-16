"""Connect the V9.3 Fc × PM Solution Map to the maintained LLC digital loop."""
from __future__ import annotations

from types import MethodType

import numpy as np

from llc_design.control.digital_loop import ControllerKind
from llc_design.gui.widgets.solution_map_view import LoopMapSource, SolutionMapView


def _remove_controller(open_loop: np.ndarray, controller: np.ndarray) -> np.ndarray:
    fixed = np.zeros_like(open_loop, dtype=complex)
    np.divide(open_loop, controller, out=fixed, where=np.abs(controller) > 1e-30)
    return fixed


def install_llc_solution_map(host) -> SolutionMapView:
    """Add an Fc × PM map to the existing LLC Digital Control result tabs."""
    loop_view = host.digital_loop_view
    if hasattr(loop_view, "solution_map_view"):
        return loop_view.solution_map_view

    view = SolutionMapView("LLC Fc × PM Solution Map", parent=loop_view)
    loop_view.solution_map_view = view
    loop_view.result_tabs.addTab(view, "Fc × PM Solution Map")

    original_set_analysis = loop_view.set_analysis

    def set_analysis_with_solution_map(self, result) -> None:
        original_set_analysis(result)
        f = np.asarray(result.frequencies_hz, dtype=float)
        responses = result.responses
        provenance = (
            "V9.3 Guided System Definition → maintained LLC digital-loop analysis"
            if hasattr(host, "guided_system_definition")
            else "LLC Expert workspace → maintained LLC digital-loop analysis"
        )
        source = LoopMapSource(
            key="voltage",
            label="LLC voltage loop",
            frequencies_hz=f,
            fixed_loop_response=_remove_controller(
                responses["open_loop_nominal"], responses["controller"]
            ),
            sample_rate_hz=1.0 / result.controller.sample_time_s,
            switching_frequency_hz=result.small_signal.operating_point.switching_frequency_hz,
            provenance=(
                provenance
                + " | fixed: Gvf + FM slope + analog sense + ADC/averaging + ZOH/application delay"
            ),
        )
        view.set_sources([source])

    loop_view.set_analysis = MethodType(set_analysis_with_solution_map, loop_view)

    def apply_selected(loop_key: str, point) -> None:
        if loop_key != "voltage" or point.kp is None or point.ti_s is None:
            return
        # A Solution Map point is an exact firmware-Tustin PI design.  Applying
        # it returns through the normal LLC Digital Loop request path so the
        # full maintained analysis, z-plane approximation and ngspice handoff
        # are rebuilt from the selected point.
        if hasattr(loop_view, "external_controller_check"):
            loop_view.external_controller_check.setChecked(False)
        index = loop_view.controller_kind.findData(ControllerKind.PI)
        if index >= 0:
            loop_view.controller_kind.setCurrentIndex(index)
        loop_view.kp.setValue(float(point.kp))
        loop_view.ti_ms.setValue(float(point.ti_s) * 1e3)
        loop_view.sample_us.setValue(1.0e6 / view.sources[loop_key].sample_rate_hz)
        loop_view.request_analysis()

    view.controller_selected.connect(apply_selected)
    return view


__all__ = ["install_llc_solution_map"]

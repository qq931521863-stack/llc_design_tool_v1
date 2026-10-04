"""Connect controller candidates to the maintained LLC digital loop."""
from __future__ import annotations

from dataclasses import fields, is_dataclass, replace
from types import MethodType

import numpy as np
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QPlainTextEdit,
    QSpinBox,
    QWidget,
)

from llc_design.control.digital_loop import (
    ControllerKind,
    PIControllerConfig,
    build_digital_loop_analysis,
)
from llc_design.gui.widgets.solution_map_view import LoopMapSource, SolutionMapView


def _remove_controller(open_loop: np.ndarray, controller: np.ndarray) -> np.ndarray:
    fixed = np.zeros_like(open_loop, dtype=complex)
    np.divide(open_loop, controller, out=fixed, where=np.abs(controller) > 1e-30)
    return fixed


def _merge_model_edits(frozen, displayed, edited):
    """Apply GUI deltas without rounding unchanged fields or resetting hidden ones."""
    if edited == displayed:
        return frozen
    if all(is_dataclass(value) for value in (frozen, displayed, edited)):
        target = frozen if type(edited) is type(displayed) else edited
        return replace(target, **{
            field.name: _merge_model_edits(
                getattr(frozen, field.name), getattr(displayed, field.name),
                getattr(edited, field.name),
            )
            for field in fields(edited)
            if hasattr(frozen, field.name) and hasattr(displayed, field.name)
            and hasattr(target, field.name)
        })
    return edited


def _watch_input(widget, callback) -> None:
    if isinstance(widget, (QDoubleSpinBox, QSpinBox)):
        widget.valueChanged.connect(callback)
    elif isinstance(widget, QComboBox):
        widget.currentIndexChanged.connect(callback)
    elif isinstance(widget, QCheckBox):
        widget.toggled.connect(callback)
    elif isinstance(widget, QPlainTextEdit) and not widget.isReadOnly():
        widget.textChanged.connect(callback)


def _guard_loop_completions(host, callback_name: str, view, state) -> None:
    """Bind each worker result to its own input revision and request sequence."""
    original_run_worker = host._run_worker
    state["request_id"] = 0

    def run_worker(self, label, function, callback):
        if callback != getattr(self, callback_name):
            return original_run_worker(label, function, callback)
        state["request_id"] += 1
        request_id = state["request_id"]
        revision = state["revision"]
        state["requested_revision"] = revision
        view.invalidate("Analysis is running; wait for a fresh candidate source.")

        def ready(result):
            if request_id != state["request_id"]:
                return
            if revision != state["revision"]:
                view.invalidate("Inputs changed during analysis. Run again before applying a candidate.")
                return
            callback(result)

        return original_run_worker(label, function, ready)

    host._run_worker = MethodType(run_worker, host)


def install_llc_solution_map(host) -> SolutionMapView:
    """Add candidate comparison to the existing LLC Digital Control result tabs."""
    loop_view = host.digital_loop_view
    if hasattr(loop_view, "solution_map_view"):
        return loop_view.solution_map_view

    view = SolutionMapView("LLC Fc × PM Solution Map", parent=loop_view)
    loop_view.solution_map_view = view
    loop_view.result_tabs.addTab(view, "Fc × PM Solution Map")
    state = {"updating": False, "revision": 0, "requested_revision": 0}
    loop_view._solution_map_controller = None
    _guard_loop_completions(host, "_digital_loop_ready", view, state)

    def invalidate(*_args) -> None:
        if state["updating"]:
            return
        state["revision"] += 1
        view.invalidate("LLC inputs changed. Run the maintained loop again before comparing or applying a candidate.")

    def adopt_controller(controller, source=None) -> None:
        loop_view._solution_map_controller = controller
        state["displayed_controller"] = loop_view._controller_config(loop_view.sample_us.value() * 1e-6)
        if source is not None:
            state["controller_source"] = source

    def current_controller():
        edited = loop_view._controller_config(loop_view.sample_us.value() * 1e-6)
        frozen = loop_view._solution_map_controller
        if frozen is None:
            return edited
        return _merge_model_edits(frozen, state["displayed_controller"], edited)

    loop_view._solution_map_set_controller = adopt_controller
    loop_view._solution_map_current_controller = current_controller
    state["controller_source"] = "LLC local controller"

    def sample_edited(*_args) -> None:
        if not state["updating"]:
            loop_view._solution_map_sample_time = None

    for widget in loop_view.parameter_stack.findChildren(QWidget):
        _watch_input(widget, invalidate)
    _watch_input(loop_view.external_controller_check, invalidate)
    _watch_input(loop_view.sample_us, sample_edited)
    for widget in (*host.fields.values(), host.parameter_mode_combo, host.topology, host.primary_device_combo):
        _watch_input(widget, invalidate)

    # Keep one owner of the normal request signal. The exact adopted controller
    # survives subsequent Run clicks; edits replace only the fields changed.
    loop_view.analysis_requested.disconnect(host.run_digital_loop)

    def request_exact_analysis(options) -> None:
        use_external = options["loop"]["controller_transfer_function"] is not None
        controller = (current_controller() if not use_external
                      and loop_view._solution_map_controller is not None else None)
        if not use_external:
            sample_time = (
                controller.sample_time_s if controller is not None
                else getattr(loop_view, "_solution_map_sample_time", None)
            )
            if controller is not None:
                options["loop"]["controller_config"] = controller
                options["loop"]["controller_source"] = state["controller_source"]
            if sample_time is not None:
                options["small_signal"]["sample_time_s"] = sample_time
                options["loop"]["controller_config"] = replace(
                    options["loop"]["controller_config"], sample_time_s=sample_time,
                )
                options["loop"]["adc_sampling"] = replace(
                    options["loop"]["adc_sampling"], control_sample_time_s=sample_time,
                )
        state["requested_revision"] = state["revision"]
        view.invalidate("LLC analysis is running; wait for a fresh candidate source.")
        host.run_digital_loop(options)

    loop_view.analysis_requested.connect(request_exact_analysis)
    original_set_busy = loop_view.set_busy

    def set_busy_with_solution_map(self, busy) -> None:
        original_set_busy(busy)
        if busy:
            state["requested_revision"] = state["revision"]
            view.invalidate("LLC analysis is running; wait for a fresh candidate source.")

    loop_view.set_busy = MethodType(set_busy_with_solution_map, loop_view)
    original_set_analysis = loop_view.set_analysis

    def set_analysis_with_solution_map(self, result) -> None:
        state["updating"] = True
        try:
            original_set_analysis(result)
        finally:
            state["updating"] = False
        if state["requested_revision"] != state["revision"]:
            view.invalidate("LLC inputs changed during analysis. Run again before applying a candidate.")
            return
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
            current_loop_response=responses["open_loop_nominal"],
            sample_rate_hz=1.0 / result.controller.sample_time_s,
            switching_frequency_hz=result.small_signal.operating_point.switching_frequency_hz,
            provenance=(provenance + " | fixed: Gvf + FM slope + analog sense + ADC/averaging + ZOH/application delay"),
        )
        if result.controller_config is not None:
            adopt_controller(result.controller_config, result.controller_source)
        view.set_sources([source])
        intent = getattr(host, "_pending_controller_intent", None)
        if intent is not None:
            host._pending_controller_intent = None
            view.configure_intent(intent)
            loop_view.result_tabs.setCurrentWidget(view)
            view.result_tabs.setCurrentIndex(1)

    loop_view.set_analysis = MethodType(set_analysis_with_solution_map, loop_view)
    original_design_ready = host._design_ready

    def design_ready_with_guided_loop(self, analysis) -> None:
        original_design_ready(analysis)
        if getattr(self, "_pending_controller_intent", None) is not None:
            self.digital_loop_view.request_analysis()

    host._design_ready = MethodType(design_ready_with_guided_loop, host)

    def apply_selected(loop_key: str, point) -> None:
        if (loop_key not in view.sources or loop_key != "voltage" or not point.feasible
                or point.kp is None or point.ti_s is None or loop_view.result is None):
            return
        source_result = loop_view.result
        old = source_result.controller_config
        controller = PIControllerConfig(
            kp=float(point.kp), ti_s=float(point.ti_s),
            sample_time_s=1.0 / view.sources[loop_key].sample_rate_hz,
            output_min=float(getattr(old, "output_min", loop_view.out_min.value())),
            output_max=float(getattr(old, "output_max", loop_view.out_max.value())),
        )
        state["updating"] = True
        try:
            loop_view.external_controller_check.setChecked(False)
            loop_view.controller_kind.setCurrentIndex(loop_view.controller_kind.findData(ControllerKind.PI))
            loop_view.kp.setValue(controller.kp)
            loop_view.ti_ms.setValue(controller.ti_s * 1e3)
            loop_view.sample_us.setValue(controller.sample_time_s * 1e6)
        finally:
            state["updating"] = False
        adopt_controller(controller, "LLC applied Fc/PM candidate")
        # The shared-SPICE bridge must no longer prefer an older external H(z).
        host.external_control_design = None
        host.external_control_label = ""
        state["requested_revision"] = state["revision"]
        view.invalidate("Applying the selected PI through the maintained LLC analysis…")
        # Reuse the frozen source exactly. The maintained completion callback
        # remains the sole owner of Bode, code generation and SPICE handoff.
        host._run_worker(
            "正在应用候选 PI 并更新完整 LLC 数字电压环…",
            lambda: build_digital_loop_analysis(
                source_result.small_signal, controller_config=controller,
                controller_source="LLC applied Fc/PM candidate",
                fm_lut=source_result.fm_lut,
                command_pu=source_result.fm_operating_point.command_pu,
                analog_sense=source_result.analog_sense,
                adc_sampling=source_result.adc_sampling,
                command_timing=source_result.command_timing,
                frequencies_hz=source_result.frequencies_hz,
            ),
            host._digital_loop_ready,
        )

    view.controller_selected.connect(apply_selected)
    return view


__all__ = ["install_llc_solution_map"]

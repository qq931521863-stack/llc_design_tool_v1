"""Exact, versioned control-session persistence for the maintained LLC/TTPL UIs.

Saved curves and assessments are history. Reopening never grants Apply permission:
real inputs are replayed after Run, and a candidate must be generated again.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, fields, is_dataclass, replace
from enum import Enum
import math
import re
from types import MethodType

import numpy as np
from PySide6.QtCore import QSignalBlocker
from PySide6.QtWidgets import QCheckBox, QComboBox

from llc_design.control.design_session import (
    MODEL_REVISION, decode_model, encode_model, llc_inputs, make_session,
    replay_llc, replay_ttpl, validate_session,
)
from llc_design.control.digital_loop import (
    ControllerKind, DigitalTransferFunction, PIControllerConfig,
    PIFControllerConfig,
)
from llc_design.gui.widgets.solution_map_view import LoopMapSource
from llc_design.control.linearize import ControlInputKind

_MAP_CONTROLS = (
    "fc_min", "fc_max", "fc_points", "pm_min", "pm_max", "pm_points",
    "gm_min", "ms_max", "switch_max", "fc_tol", "pm_tol", "live_fc", "live_pm",
)
_MAX_SNAPSHOTS = 32


def _canonical_options(options):
    options = {key: dict(value) for key, value in options.items()}
    options["small_signal"]["control_input_kind"] = ControlInputKind(options["small_signal"]["control_input_kind"])
    external = options["loop"].get("controller_transfer_function")
    if external is not None and not isinstance(external, DigitalTransferFunction):
        options["loop"]["controller_transfer_function"] = DigitalTransferFunction(
            external.b, external.a, 1.0 / external.sample_rate_hz, name=external.name,
        )
    return options


def _same(first, second):
    return encode_model(first) == encode_model(second)


def _merge_exact(frozen, displayed, edited):
    """Keep exact/hidden values until their visible field actually changes."""
    if _same(displayed, edited):
        return frozen
    if isinstance(frozen, dict) and isinstance(displayed, dict) and isinstance(edited, dict):
        merged = dict(frozen)
        for key, value in edited.items():
            merged[key] = (_merge_exact(frozen[key], displayed[key], value)
                           if key in frozen and key in displayed else value)
        return merged
    if (is_dataclass(frozen) and type(frozen) is type(displayed)
            and type(displayed) is type(edited)):
        return replace(frozen, **{
            field.name: _merge_exact(getattr(frozen, field.name), getattr(displayed, field.name),
                                     getattr(edited, field.name)) for field in fields(frozen)
        })
    return edited


def _history(value):
    """Represent non-finite assessment metrics explicitly as unavailable JSON."""
    if isinstance(value, Enum):
        return value.name
    if is_dataclass(value):
        return _history(asdict(value))
    if isinstance(value, dict):
        return {key: _history(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_history(item) for item in value]
    if isinstance(value, (float, np.floating)) and not math.isfinite(value):
        return None
    if isinstance(value, np.generic):
        return value.item()
    return value


def _source_document(source):
    return {
        "key": source.key, "label": source.label, "provenance": source.provenance,
        "operating_point": source.operating_point, "compatibility_key": source.compatibility_key,
        "sample_rate_hz": source.sample_rate_hz,
        "switching_frequency_hz": source.switching_frequency_hz,
        "frequencies_hz": source.frequencies_hz.tolist(),
        "fixed_loop_response": np.column_stack((source.fixed_loop_response.real,
                                                source.fixed_loop_response.imag)).tolist(),
        "current_loop_response": (None if source.current_loop_response is None else
                                  np.column_stack((source.current_loop_response.real,
                                                   source.current_loop_response.imag)).tolist()),
        "model_inputs": deepcopy(source.model_inputs),
    }


def _restore_source(payload, workspace):
    required = {"key", "label", "provenance", "operating_point", "compatibility_key",
                "sample_rate_hz", "switching_frequency_hz", "frequencies_hz",
                "fixed_loop_response", "current_loop_response", "model_inputs"}
    if not isinstance(payload, dict) or set(payload) != required:
        raise ValueError("Invalid operating-point snapshot fields")
    if payload["key"] not in ({"voltage"} if workspace == "llc" else {"current", "voltage"}):
        raise ValueError("Invalid operating-point loop")
    for key in ("label", "provenance", "operating_point", "compatibility_key"):
        if not isinstance(payload[key], str) or len(payload[key]) > 4096:
            raise ValueError("Invalid operating-point text")
    arrays = {}
    for key in ("fixed_loop_response", "current_loop_response"):
        value = payload[key]
        if value is None and key == "current_loop_response":
            arrays[key] = None
            continue
        a = np.asarray(value)
        if a.dtype.kind not in "iuf" or a.ndim != 2 or a.shape[1] != 2 or a.shape[0] > 100_000:
            raise ValueError("Invalid complex response samples")
        arrays[key] = np.asarray(a[:, 0] + 1j * a[:, 1], dtype=complex)
    f = np.asarray(payload["frequencies_hz"])
    if f.dtype.kind not in "iuf" or f.ndim != 1 or len(f) > 100_000:
        raise ValueError("Invalid frequency samples")
    for key in ("sample_rate_hz", "switching_frequency_hz"):
        if type(payload[key]) not in (int, float):
            raise ValueError("Invalid sample/switching frequency")
    inputs = payload["model_inputs"]
    if inputs is not None:
        # Header/input validation is independent of the historical responses.
        validate_session(make_session(workspace, decode_model(inputs)))
    source = LoopMapSource(**{key: payload[key] for key in required - {
        "frequencies_hz", "fixed_loop_response", "current_loop_response"}},
        frequencies_hz=f.astype(float), **arrays, replay_verified=False)
    from llc_design.gui.widgets.solution_map_view import SolutionMapView
    problem = SolutionMapView._source_problem(source)
    if problem:
        raise ValueError(problem)
    return source


def capture_map_state(view):
    if len(view._retained_sources) > _MAX_SNAPSHOTS:
        raise ValueError(f"A saved session supports at most {_MAX_SNAPSHOTS} retained points")
    return {
        "controls": {name: getattr(view, name).value() for name in _MAP_CONTROLS},
        "source_key": view.source_combo.currentData() or view._last_source_key,
        "snapshots": [{"id": key, "checked": key in view._checked_corner_ids,
                       "source": _source_document(source)}
                      for key, source in view._retained_sources.items()],
        "selected_candidate": (_history(view.selected_point) if view.selected_point is not None
                               else deepcopy(getattr(view, "_historical_selected_candidate", None))),
        "conditional_adoption_was_enabled": view.allow_conditional.isChecked(),
    }


def validate_map_state(view, payload, workspace):
    if payload is None or payload == {}:
        return {}, {}
    expected = {"controls", "source_key", "snapshots", "selected_candidate",
                "conditional_adoption_was_enabled"}
    if not isinstance(payload, dict) or set(payload) != expected:
        raise ValueError("Invalid solution-map session fields")
    controls = payload["controls"]
    if not isinstance(controls, dict) or set(controls) != set(_MAP_CONTROLS):
        raise ValueError("Invalid solution-map controls")
    for name, value in controls.items():
        widget = getattr(view, name)
        if (type(value) not in (int, float) or not math.isfinite(value)
                or not widget.minimum() <= value <= widget.maximum()
                or (name.endswith("points") and type(value) is not int)):
            raise ValueError(f"Invalid solution-map value: {name}")
    if controls["fc_max"] <= controls["fc_min"] or controls["pm_max"] <= controls["pm_min"]:
        raise ValueError("Invalid solution-map sweep interval")
    if payload["source_key"] not in (None, "voltage", "current"):
        raise ValueError("Invalid selected loop")
    if type(payload["conditional_adoption_was_enabled"]) is not bool:
        raise ValueError("Invalid saved conditional-adoption state")
    selected = payload["selected_candidate"]
    if selected is not None and not isinstance(selected, dict):
        raise ValueError("Invalid historical candidate")
    snapshots = payload["snapshots"]
    if not isinstance(snapshots, list) or len(snapshots) > _MAX_SNAPSHOTS:
        raise ValueError("Invalid operating-point snapshot count")
    sources = {}
    for snapshot in snapshots:
        if (not isinstance(snapshot, dict) or set(snapshot) != {"id", "checked", "source"}
                or not isinstance(snapshot["id"], str)
                or re.fullmatch(r"OP[1-9][0-9]{0,5}", snapshot["id"]) is None
                or snapshot["id"] in sources or type(snapshot["checked"]) is not bool):
            raise ValueError("Invalid operating-point identity")
        sources[snapshot["id"]] = _restore_source(snapshot["source"], workspace)
    return payload, sources


def restore_map_state(view, payload, sources, revision):
    view.invalidate("Reopened session: Run analysis, then generate a fresh candidate before Apply.",
                    clear_corners=True)
    view._retained_sources = {key: view._freeze_source(value) for key, value in sources.items()}
    view._checked_corner_ids = {item["id"] for item in payload.get("snapshots", []) if item["checked"]}
    view._corner_sequence = max((int(key[2:]) for key in sources), default=0)
    view._last_source_key = payload.get("source_key")
    for name, value in payload.get("controls", {}).items():
        with QSignalBlocker(getattr(view, name)):
            getattr(view, name).setValue(value)
    view.allow_conditional.setChecked(False)
    view._historical_selected_candidate = deepcopy(payload.get("selected_candidate"))
    view._restored_map_controls = deepcopy(payload.get("controls", {}))
    view._corner_notice = (
        "Saved curves/evidence are unverified history. Run rebuilds saved operating points from their inputs."
        + (" Model revision changed; all results require recomputation." if revision != MODEL_REVISION else "")
    )
    view._refresh_corners()
    selected = view._historical_selected_candidate
    if selected:
        view.details.setPlainText(
            "Historical saved candidate (unverified; Apply disabled):\n"
            f"Fc={selected.get('target_crossover_hz')} Hz; PM={selected.get('target_phase_margin_deg')}°\n"
            f"Kp={selected.get('kp')}; Ti={selected.get('ti_s')} s\n"
            f"Saved assessment={selected.get('status')}; {selected.get('message', '')}\n"
            "Run analysis and generate a new candidate. Conditional adoption must be confirmed again."
        )


def _replay_retained(view, workspace):
    failures = []
    for key, source in list(view._retained_sources.items()):
        if source.replay_verified:
            continue
        if source.model_inputs is None:
            failures.append(key)
            continue
        try:
            if workspace == "llc":
                from llc_design.gui.solution_map_install import llc_source
                refreshed = llc_source(replay_llc(decode_model(source.model_inputs)))
            else:
                from pfc_design.gui.solution_map_install import ttpl_sources
                options = ttpl_sources(replay_ttpl(decode_model(source.model_inputs)))
                refreshed = next(item for item in options if item.key == source.key)
            view._retained_sources[key] = view._freeze_source(refreshed)
        except (ValueError, TypeError, ArithmeticError, KeyError) as exc:
            failures.append(f"{key} ({exc})")
    view._corner_notice = (
        "Saved snapshots rebuilt from model inputs; saved candidate evidence remains historical."
        if not failures else "Excluded snapshots requiring a new analysis: " + "; ".join(failures)
    )


def _restore_map_controls_after_run(view):
    controls = getattr(view, "_restored_map_controls", None)
    if controls is not None:
        for name, value in controls.items():
            with QSignalBlocker(getattr(view, name)):
                getattr(view, name).setValue(value)
        del view._restored_map_controls


def _set(widget, value):
    with QSignalBlocker(widget):
        if isinstance(widget, QComboBox):
            index = widget.findData(value)
            if index < 0:
                raise ValueError("Unsupported saved selector value")
            widget.setCurrentIndex(index)
        elif isinstance(widget, QCheckBox):
            widget.setChecked(value)
        else:
            widget.setValue(value)


def _controller_widgets(controller, kind, kp, ti, fc, coefficients):
    category = (ControllerKind.PI if isinstance(controller, PIControllerConfig) else
                ControllerKind.PIF if isinstance(controller, PIFControllerConfig) else ControllerKind.TWO_P_TWO_Z)
    _set(kind, category)
    kp.setEnabled(category in (ControllerKind.PI, ControllerKind.PIF))
    ti.setEnabled(category in (ControllerKind.PI, ControllerKind.PIF))
    fc.setEnabled(category == ControllerKind.PIF)
    for widget in coefficients.values():
        widget.setEnabled(category == ControllerKind.TWO_P_TWO_Z)
    if category in (ControllerKind.PI, ControllerKind.PIF):
        _set(kp, controller.kp)
        _set(ti, controller.ti_s * 1e3)
        if category == ControllerKind.PIF:
            _set(fc, controller.lpf_cutoff_hz)
    else:
        for name, widget in coefficients.items():
            _set(widget, getattr(controller, name))


def _reflect_llc(loop, inputs):
    small, digital = inputs["small_signal"], inputs["digital_loop"]
    for name, value in {"vbus": small["vbus_v"], "load_percent": small["load_fraction"] * 100,
                        "sample_us": small["sample_time_s"] * 1e6}.items():
        _set(getattr(loop, name), value)
    controller = digital.get("controller_config")
    external = digital.get("controller_transfer_function")
    if external is not None:
        from power_control_tools.models import DigitalTransferFunction as PublicTransfer
        loop.set_external_controller(PublicTransfer(tuple(external.numerator), tuple(external.denominator),
                                                    1 / external.sample_time_s, external.name),
                                     digital.get("controller_source", "Restored H(z)"))
    else:
        _set(loop.external_controller_check, False)
        loop._external_controller = None
        loop._external_controller_label = ""
        _controller_widgets(controller, loop.controller_kind, loop.kp, loop.ti_ms, loop.pif_fc,
                            {name: getattr(loop, name) for name in ("b0", "b1", "b2", "a1", "a2")})
        _set(loop.out_min, controller.output_min)
        _set(loop.out_max, controller.output_max)
        loop._update_controller_fields()
    lut = digital["fm_lut"]
    _set(loop.fm_mode, lut.mode)
    _set(loop.count_mode, lut.count_mode)
    _set(loop.timer_mhz, lut.timer_clock_hz / 1e6)
    with QSignalBlocker(loop.lut_text):
        loop.lut_text.setPlainText("\n".join(f"{p:.17g}, {v:.17g}" for p, v in zip(lut.pcmd, lut.values)))
    command = digital.get("command_pu")
    _set(loop.auto_pcmd, command is None)
    if command is not None:
        _set(loop.pcmd, command)
    analog, adc, timing = digital["analog_sense"], digital["adc_sampling"], digital["command_timing"]
    values = {
        "rup_k": analog.rup_ohm / 1e3, "rlow_k": analog.rlow_ohm / 1e3,
        "cdiv_nf": analog.divider_capacitance_f * 1e9, "opamp_gain": analog.opamp_gain,
        "opamp_bw_khz": analog.opamp_bandwidth_hz / 1e3,
        "adc_r": analog.adc_series_resistance_ohm, "adc_c_nf": analog.adc_shunt_capacitance_f * 1e9,
        "adc_clock_mhz": adc.adc_clock_hz / 1e6, "acq_ns": adc.acquisition_time_s * 1e9,
        "conversion_cycles": adc.conversion_cycles, "soc_count": adc.soc_count,
        "previous_weight": adc.recursive_previous_weight,
        "computation_us": timing.computation_delay_s * 1e6,
        "pwm_update_us": timing.pwm_update_delay_s * 1e6, "include_zoh": timing.include_zero_order_hold,
    }
    for name, value in values.items():
        _set(getattr(loop, name), value)


def install_llc_session(host):
    loop = host.digital_loop_view
    if hasattr(loop, "_session_capture"):
        return
    raw_options = loop._analysis_options
    raw_spec = host._spec_from_widgets
    state = {"frozen_options": None, "displayed_options": None,
             "spec": host.spec, "displayed_spec": raw_spec()}

    def exact_spec(self):
        return _merge_exact(state["spec"], state["displayed_spec"], raw_spec())

    host._spec_from_widgets = MethodType(exact_spec, host)
    raw_load_spec = host._load_spec_to_widgets

    def load_spec(self, spec):
        raw_load_spec(spec)
        state["spec"], state["displayed_spec"] = spec, raw_spec()
        state["frozen_options"] = None

    host._load_spec_to_widgets = MethodType(load_spec, host)

    def exact_options(self):
        edited = _canonical_options(raw_options())
        if state["frozen_options"] is None:
            return edited
        merged = _merge_exact(state["frozen_options"], state["displayed_options"], edited)
        old = state["displayed_options"]["small_signal"]
        if (not _same(host._spec_from_widgets(), state["spec"])
                or any(edited["small_signal"][key] != old[key] for key in ("vbus_v", "load_fraction"))):
            merged["small_signal"].pop("series_resistance_ohm", None)
        # The resolved command belongs to the completed snapshot. Auto mode is
        # an input policy: new plant/LUT operating points must derive it again.
        if edited["loop"].get("command_pu") is None:
            merged["loop"]["command_pu"] = None
        if edited["small_signal"]["sample_time_s"] != old["sample_time_s"]:
            merged["loop"].pop("frequencies_hz", None)
        return merged

    loop._analysis_options = MethodType(exact_options, loop)
    raw_set = loop.set_analysis

    def set_analysis(self, result):
        if hasattr(loop.solution_map_view, "_restored_map_controls"):
            loop.solution_map_view._restored_map_controls = {
                name: getattr(loop.solution_map_view, name).value() for name in _MAP_CONTROLS}
        _replay_retained(loop.solution_map_view, "llc")
        raw_set(result)
        frozen = llc_inputs(result)
        state["frozen_options"] = {"small_signal": frozen["small_signal"], "loop": frozen["digital_loop"]}
        state["metadata"] = frozen.get("small_signal_metadata")
        state["displayed_options"] = _canonical_options(raw_options())
        state["spec"], state["displayed_spec"] = result.small_signal.spec, raw_spec()
        _restore_map_controls_after_run(loop.solution_map_view)

    loop.set_analysis = MethodType(set_analysis, loop)

    def capture():
        options = loop._analysis_options()
        if options["loop"].get("controller_transfer_function") is None:
            controller = loop._solution_map_current_controller()
            options["loop"]["controller_config"] = controller
            options["small_signal"]["sample_time_s"] = controller.sample_time_s
            options["loop"]["adc_sampling"] = replace(options["loop"]["adc_sampling"],
                                                       control_sample_time_s=controller.sample_time_s)
        inputs = {"spec": host._spec_from_widgets(), "small_signal": options["small_signal"],
                  "digital_loop": options["loop"]}
        if (state.get("metadata") is not None and _same(inputs["spec"], state["spec"])
                and "series_resistance_ohm" in options["small_signal"]):
            inputs["small_signal_metadata"] = state["metadata"]
        return make_session("llc", inputs, capture_map_state(loop.solution_map_view), loop._candidate_evidence())

    def restore(document, prepared):
        validate_session(document)
        inputs = decode_model(document["inputs"])
        _reflect_llc(loop, inputs)
        host.external_control_design = None
        host.external_control_label = ""
        loop._solution_map_restore_controller(inputs["digital_loop"].get("controller_config"),
                                               inputs["digital_loop"].get("controller_source", "Restored controller"))
        state["frozen_options"] = {"small_signal": inputs["small_signal"], "loop": inputs["digital_loop"]}
        state["displayed_options"] = _canonical_options(raw_options())
        state["metadata"] = inputs.get("small_signal_metadata")
        restore_map_state(loop.solution_map_view, *prepared, document["model_revision"])
        loop._restore_candidate_evidence(document.get("candidate_evidence"))
        # Old plots may remain visible, but no pre-load result may be exported.
        host.digital_loop_analysis = None
        loop.result = None
        loop.research_export.set_analysis(None)
        loop.codegen_button.setEnabled(False)

    loop._session_capture, loop._session_restore = capture, restore


def _reflect_ttpl(control, config):
    stage, fw = config.power_stage, config.firmware
    names = {
        "vin_rms": stage.vin_rms_v, "vbus": stage.bus_voltage_v, "pout": stage.output_power_w,
        "line_hz": stage.line_frequency_hz, "fsw": stage.switching_frequency_hz / 1e3,
        "inductance": stage.boost_inductance_h * 1e6, "dcr": stage.equivalent_series_resistance_ohm * 1e3,
        "cbus": stage.bus_capacitance_f * 1e6, "cbus_esr": stage.bus_cap_esr_ohm * 1e3,
        "load_model": stage.load_model, "angle": stage.line_angle_deg, "duty_min": stage.duty_min,
        "duty_max": stage.duty_max, "min_pulse_us": stage.minimum_effective_pulse_s * 1e6,
        "deadtime_ns": stage.deadtime_s * 1e9, "vff_gain": fw.vac_rms_feedforward_gain,
        "gcmd_max": fw.gcmd_max_a_per_v, "indu_gain": fw.indu_comp_gain,
        "current_delay_us": fw.current_computation_delay_s * 1e6,
        "amc_delay_us": fw.amc_update_delay_s * 1e6,
        "voltage_delay_us": fw.voltage_computation_delay_s * 1e6,
        "switch_cycles": config.switching_cycles, "switch_samples": config.switching_samples_per_cycle,
    }
    for name, value in names.items():
        _set(getattr(control, name), value)
    control.engineering_efficiency = stage.efficiency
    for name in ("current", "voltage"):
        controller = getattr(config, name + "_controller")
        widgets = getattr(control, name + "_ctrl")
        _controller_widgets(controller, widgets["kind"], widgets["kp"], widgets["ti"], widgets["fc"],
                            {key: widgets[key] for key in ("b0", "b1", "b2", "a1", "a2")})
    for name in ("current_sense", "vac_sense", "vbus_sense"):
        sense, widgets = getattr(config, name), getattr(control, name)
        values = {"gain": sense.front_end_gain_v_per_unit, "bw": sense.amplifier_bandwidth_hz / 1e3,
                  "source_r": sense.source_resistance_ohm, "source_c": sense.shunt_capacitance_f * 1e9,
                  "out_r": sense.output_resistance_ohm, "out_c": sense.adc_capacitance_f * 1e9,
                  "sample": sense.timing.sample_rate_hz / 1e3, "alpha": sense.timing.digital_filter.alpha}
        for key, value in values.items():
            _set(widgets[key], value)


def install_ttpl_session(host):
    control = host.control_lab_view.control_lab
    if hasattr(control, "_session_capture"):
        return
    raw_set = control.set_result

    def set_result(self, result, **kwargs):
        if hasattr(control.solution_map_view, "_restored_map_controls"):
            control.solution_map_view._restored_map_controls = {
                name: getattr(control.solution_map_view, name).value() for name in _MAP_CONTROLS}
        _replay_retained(control.solution_map_view, "ttpl")
        raw_set(result, **kwargs)
        _restore_map_controls_after_run(control.solution_map_view)

    control.set_result = MethodType(set_result, control)

    def capture():
        return make_session("ttpl", control._config(), capture_map_state(control.solution_map_view),
                            control._candidate_evidence())

    def restore(document, prepared):
        validate_session(document)
        config = decode_model(document["inputs"])
        _reflect_ttpl(control, config)
        control._solution_map_restore_config(config)
        restore_map_state(control.solution_map_view, *prepared, document["model_revision"])
        control._restore_candidate_evidence(document.get("candidate_evidence"))
        host.result = control.result = None
        host.engineering_result = None
        control.research_export.set_analysis(None)
        host._worker_request_id += 1
        host.set_busy(False)
        control.codegen_button.setEnabled(False)
        # Downstream H(z)/SPICE owners must not export or simulate the previous session.
        exact = host.control_lab_view.exact_hz_view
        exact.analysis = exact.handoff = None
        exact.set_busy(False)
        exact.status.setText("Session reopened; run TTPL analysis to rebuild the exact H(z) contract.")
        spice = host.control_lab_view.ngspice_closed_loop_view
        spice.analysis = None
        spice.set_busy(False)
        spice._refresh_status()

    control._session_capture, control._session_restore = capture, restore

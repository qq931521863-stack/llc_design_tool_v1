"""Bounded JSON control sessions and reproducible electrical-model requests.

Only the classes explicitly listed below can be reconstructed. Saved responses,
checks and candidate evidence are historical data, never executable models or
proof of validity. A caller must replay the input request before enabling Apply.
"""
from __future__ import annotations

from dataclasses import fields, is_dataclass, replace
from enum import Enum
import hashlib
import json
import math
from pathlib import Path
import tempfile
import types
from typing import Any, Union, get_args, get_origin, get_type_hints

import numpy as np

from llc_design import __version__
from llc_design.core.spec import (
    LLCDesignSpec, PrimaryTopology, SecondaryTopology, TankParameterMode,
)
from pfc_design.control.config import (
    ADCTimingConfig, DigitalFilterConfig, ExternalSenseConfig, LoadModel,
    PFCControlLabConfig, PFCFirmwareAlgorithmConfig, PFCPowerStageConfig,
)
from .analysis import build_small_signal_analysis
from .digital_loop import (
    ADCSamplingConfig, AnalogSenseConfig, CommandTimingConfig, ControllerKind,
    DelayEnvelope, DigitalTransferFunction, FMLUTMode, FrequencyModulatorLUT,
    PIControllerConfig, PIFControllerConfig, PWMCountMode, TwoP2ZControllerConfig,
    build_digital_loop_analysis,
)
from .linearize import ControlInputKind
from .solution_map import SolutionMapConstraints

SESSION_SCHEMA = "power-design-toolkit/control-session"
SESSION_SCHEMA_VERSION = 1
MODEL_REVISION = "llc-edf-ttpl-double-loop-v1"
MAX_FILE_BYTES = 32 * 1024 * 1024
MAX_DEPTH = 48
MAX_ARRAY_ITEMS = 100_000
MAX_SEQUENCE_ITEMS = 200_000
MAX_TOTAL_NODES = 2_000_000
MAX_STRING_LENGTH = 65_536
MAX_CONTROLLER_ORDER = 32
MAX_FREQUENCY_POINTS = 20_000
MAX_DELAY_SAMPLES = 64

_MODELS = {cls.__name__: cls for cls in (
    LLCDesignSpec, DigitalTransferFunction, PIControllerConfig,
    PIFControllerConfig, TwoP2ZControllerConfig, FrequencyModulatorLUT,
    AnalogSenseConfig, ADCSamplingConfig, CommandTimingConfig,
    ADCTimingConfig, DigitalFilterConfig, ExternalSenseConfig,
    PFCPowerStageConfig, PFCFirmwareAlgorithmConfig, PFCControlLabConfig,
    SolutionMapConstraints,
)}
_ENUMS = {cls.__name__: cls for cls in (
    PrimaryTopology, SecondaryTopology, TankParameterMode, ControllerKind,
    DelayEnvelope, FMLUTMode, PWMCountMode, ControlInputKind, LoadModel,
)}
_GEOMETRY_FIELDS = {
    "transformer_core_area_m2": float, "transformer_magnetic_path_m": float,
    "resonant_inductor_turns": int, "resonant_inductor_core_area_m2": float,
    "resonant_inductor_magnetic_path_m": float,
}
_TAGS = {"__model__", "__enum__", "__ndarray__", "__tuple__", "__complex__"}
_DTYPES = {np.dtype(code).str for code in (
    "?", "i1", "u1", "<i2", ">i2", "<u2", ">u2", "<i4", ">i4",
    "<u4", ">u4", "<i8", ">i8", "<u8", ">u8", "<f2", ">f2",
    "<f4", ">f4", "<f8", ">f8", "<c8", ">c8", "<c16", ">c16",
)}
_HINTS = {name: get_type_hints(cls) for name, cls in _MODELS.items()}


class SessionFormatError(ValueError):
    """A session is invalid, unsupported or exceeds a safe resource limit."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise SessionFormatError(message)


def _finite_number(value: Any) -> bool:
    return type(value) in (int, float) and math.isfinite(value)


def _check_json(value: Any, depth: int = 0, budget: list[int] | None = None) -> None:
    if budget is None:
        budget = [MAX_TOTAL_NODES]
    budget[0] -= 1
    _require(budget[0] >= 0 and depth <= MAX_DEPTH, "session structure exceeds limits")
    if value is None or type(value) is bool:
        return
    if type(value) is str:
        _require(len(value) <= MAX_STRING_LENGTH, "session string exceeds limit")
    elif type(value) is int:
        _require(-(2**63) <= value < 2**64, "session integer exceeds 64-bit range")
    elif type(value) is float:
        _require(math.isfinite(value), "session numbers must be finite")
    elif type(value) in (dict, list):
        _require(len(value) <= MAX_SEQUENCE_ITEMS, "session collection exceeds limit")
        if isinstance(value, dict):
            for key, item in value.items():
                _require(type(key) is str, "session object keys must be strings")
                _check_json(key, depth + 1, budget)
                _check_json(item, depth + 1, budget)
        else:
            for item in value:
                _check_json(item, depth + 1, budget)
    else:
        raise SessionFormatError("session must contain JSON data only")


def strict_json_loads(text: str | bytes) -> Any:
    """Reject oversized/deep JSON, duplicate keys and non-finite numbers."""
    if isinstance(text, bytes):
        _require(len(text) <= MAX_FILE_BYTES, "session file exceeds size limit")
        try:
            text = text.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise SessionFormatError("session is not valid UTF-8") from exc
    _require(type(text) is str, "session JSON must be text")
    _require(len(text) <= MAX_FILE_BYTES, "session file exceeds size limit")
    _require(len(text.encode("utf-8")) <= MAX_FILE_BYTES, "session file exceeds size limit")
    # Check nesting before json.loads, which otherwise accepts enough nesting to
    # exhaust Python's recursive parser. Escaped quotes are handled explicitly.
    depth, quoted, escaped = 0, False, False
    for char in text:
        if quoted:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted = True
        elif char in "[{":
            depth += 1
            _require(depth <= MAX_DEPTH, "session JSON is too deeply nested")
        elif char in "]}":
            depth -= 1
    def pairs(items):
        result = {}
        for key, value in items:
            _require(key not in result, f"duplicate JSON key: {key}")
            result[key] = value
        return result
    def bad_constant(value):
        raise SessionFormatError(f"non-finite JSON number: {value}")
    try:
        result = json.loads(text, object_pairs_hook=pairs, parse_constant=bad_constant)
    except (ValueError, TypeError, RecursionError) as exc:
        raise SessionFormatError(f"invalid session JSON: {exc}") from exc
    _check_json(result)
    return result


def _matches(value: Any, hint: Any) -> bool:
    origin, args = get_origin(hint), get_args(hint)
    if origin in (Union, types.UnionType):
        return any(_matches(value, item) for item in args)
    if hint is float:
        return _finite_number(value)
    if hint in (int, bool, str, type(None)):
        return type(value) is hint
    if origin is tuple:
        if type(value) is not tuple:
            return False
        return (all(_matches(v, args[0]) for v in value) if args[-1:] == (Ellipsis,)
                else len(value) == len(args) and all(_matches(v, h) for v, h in zip(value, args)))
    if origin is np.ndarray or hint is np.ndarray:
        return type(value) is np.ndarray
    return isinstance(hint, type) and type(value) is hint


def _validate_model(value: Any) -> None:
    cls = type(value)
    for field in fields(value):
        _require(_matches(getattr(value, field.name), _HINTS[cls.__name__][field.name]),
                 f"{cls.__name__}.{field.name} has the wrong type")
    validator = getattr(value, "validate", None)
    if validator is not None:
        validator()
    if isinstance(value, (PIControllerConfig, PIFControllerConfig, TwoP2ZControllerConfig)):
        _require(value.output_min < value.output_max, "controller output limits are invalid")
        value.transfer_function()
    if isinstance(value, DigitalTransferFunction):
        for coefficients in (value.numerator, value.denominator):
            _require(coefficients.ndim == 1 and 0 < coefficients.size <= MAX_CONTROLLER_ORDER + 1,
                     "controller polynomial order exceeds limit")
            _require(coefficients.dtype.kind == "f" and np.all(np.isfinite(coefficients)),
                     "controller coefficients must be finite real numbers")
    if isinstance(value, FrequencyModulatorLUT):
        _require(len(value.pcmd) <= 4096, "FM LUT exceeds node limit")
    if isinstance(value, (ADCSamplingConfig, ADCTimingConfig)):
        _require(value.soc_count <= 256, "ADC SOC count exceeds limit")
    if isinstance(value, ADCSamplingConfig) and value.soc_sample_offsets_s is not None:
        _require(all(0.0 <= item <= value.control_sample_time_s for item in value.soc_sample_offsets_s),
                 "ADC sample offsets must lie within the control frame")
    if isinstance(value, ExternalSenseConfig):
        _require(value.adc_bits <= 32, "ADC bit width exceeds limit")
    if isinstance(value, PFCControlLabConfig):
        _require(value.frequency_points <= MAX_FREQUENCY_POINTS, "Bode grid exceeds point limit")
        _require(value.waveform_line_cycles <= 100 and value.switching_samples_per_cycle <= 20_000,
                 "waveform sample request exceeds limit")
        _require(value.waveform_line_cycles * value.waveform_integration_rate_hz
                 / value.power_stage.line_frequency_hz <= 2_000_000,
                 "waveform integration request exceeds limit")
    if isinstance(value, LLCDesignSpec):
        _require(1 <= value.magnetic_thermal_max_iterations <= 100,
                 "magnetic thermal iterations must lie in 1..100")
        _require(value.magnetic_thermal_tolerance_c > 0.0,
                 "magnetic thermal tolerance must be positive")
        # The established inductor selector treats zero as a one-candidate
        # minimum via max(1, limit). Preserve that legacy input semantics.
        _require(0 <= value.magnetic_candidate_full_evaluation_limit <= 1024,
                 "magnetic candidate evaluation limit must lie in 0..1024")
        _require(value.magnetic_waveform_samples <= 65_536 and value.litz_max_harmonic <= 1024,
                 "magnetic sample request exceeds limit")


def encode_model(value: Any) -> Any:
    """Encode approved input dataclasses/enums and exact finite numeric arrays."""
    def encode(item, depth=0):
        _require(depth <= MAX_DEPTH, "model structure is too deeply nested")
        if isinstance(item, Enum):
            _require(_ENUMS.get(type(item).__name__) is type(item), "unsupported model enum")
            return {"__enum__": type(item).__name__, "value": item.value}
        if is_dataclass(item) and not isinstance(item, type):
            _require(_MODELS.get(type(item).__name__) is type(item), "unsupported model class")
            # Qt combo boxes can return a str-enum as its plain string value.
            # Canonicalize only trusted, explicitly typed enum fields here; the
            # decoder still requires valid tagged enum data.
            enum_changes = {}
            for field in fields(item):
                hint = _HINTS[type(item).__name__][field.name]
                value = getattr(item, field.name)
                if isinstance(hint, type) and issubclass(hint, Enum) and type(value) is str:
                    enum_changes[field.name] = hint(value)
            if enum_changes:
                item = replace(item, **enum_changes)
            _validate_model(item)
            return {"__model__": type(item).__name__, "fields": {
                field.name: encode(getattr(item, field.name), depth + 1) for field in fields(item)
            }}
        if isinstance(item, np.ndarray):
            _require(item.dtype.str in _DTYPES and item.ndim <= 4 and item.size <= MAX_ARRAY_ITEMS,
                     "unsupported array dtype, shape or size")
            _require(np.all(np.isfinite(item)), "array numbers must be finite")
            flat = item.reshape(-1)
            values = ([[float(v.real), float(v.imag)] for v in flat] if item.dtype.kind == "c"
                      else flat.tolist())
            return {"__ndarray__": item.dtype.str, "shape": list(item.shape), "data": values}
        if isinstance(item, np.generic):
            return encode(item.item(), depth + 1)
        if type(item) is complex:
            return {"__complex__": [item.real, item.imag]}
        if type(item) is tuple:
            return {"__tuple__": [encode(v, depth + 1) for v in item]}
        if type(item) is list:
            _require(len(item) <= MAX_SEQUENCE_ITEMS, "model collection exceeds limit")
            return [encode(v, depth + 1) for v in item]
        if type(item) is dict:
            _require(len(item) <= MAX_SEQUENCE_ITEMS, "model collection exceeds limit")
            _require(not (_TAGS & item.keys()), "model dictionary uses a reserved tag")
            return {key: encode(v, depth + 1) for key, v in item.items()}
        return item
    try:
        result = encode(value)
        _check_json(result)
        return result
    except (TypeError, ValueError, OverflowError) as exc:
        raise SessionFormatError(f"invalid model: {exc}") from exc


def _decode_array(item: dict) -> np.ndarray:
    _require(set(item) == {"__ndarray__", "shape", "data"}, "invalid array fields")
    _require(type(item["__ndarray__"]) is str and item["__ndarray__"] in _DTYPES,
             "unsupported array dtype")
    dtype = np.dtype(item["__ndarray__"])
    shape, data = item["shape"], item["data"]
    _require(type(shape) is list and len(shape) <= 4
             and all(type(n) is int and 0 <= n <= MAX_ARRAY_ITEMS for n in shape), "invalid array shape")
    size = math.prod(shape)
    _require(size <= MAX_ARRAY_ITEMS and type(data) is list and len(data) == size, "invalid array size")
    if dtype.kind == "c":
        _require(all(type(v) is list and len(v) == 2 and all(_finite_number(x) for x in v) for v in data),
                 "complex arrays require finite real/imaginary pairs")
        values = [complex(*v) for v in data]
    elif dtype.kind == "b":
        _require(all(type(v) is bool for v in data), "boolean array contains non-booleans")
        values = data
    elif dtype.kind in "iu":
        limits = np.iinfo(dtype)
        _require(all(type(v) is int and limits.min <= v <= limits.max for v in data),
                 "integer array values are out of range")
        values = data
    else:
        _require(all(_finite_number(v) for v in data), "real array contains non-numeric values")
        values = data
    with np.errstate(over="ignore", invalid="ignore"):
        array = np.asarray(values, dtype=dtype).reshape(shape)
    _require(np.all(np.isfinite(array)), "array conversion produced non-finite values")
    return array


def decode_model(value: Any) -> Any:
    """Decode only the fixed allowlist; no import/eval/pickle or class lookup."""
    _check_json(value)
    def decode(item):
        if type(item) is list:
            return [decode(v) for v in item]
        if type(item) is not dict:
            return item
        tags = _TAGS & item.keys()
        _require(len(tags) <= 1, "conflicting model tags")
        if "__model__" in tags:
            _require(set(item) == {"__model__", "fields"}, "invalid dataclass fields")
            name, payload = item["__model__"], item["fields"]
            _require(type(name) is str and name in _MODELS, "unsupported model class")
            cls = _MODELS[name]
            _require(type(payload) is dict and set(payload) == {f.name for f in fields(cls)},
                     f"{name} fields do not match schema")
            kwargs = {key: decode(v) for key, v in payload.items()}
            for key, val in kwargs.items():
                _require(_matches(val, _HINTS[name][key]), f"{name}.{key} has the wrong type")
            # Bound coercive constructors before allocating or normalizing.
            if cls is DigitalTransferFunction:
                for key in ("numerator", "denominator"):
                    array = kwargs[key]
                    _require(array.ndim == 1 and array.dtype.kind in "fiu"
                             and 0 < array.size <= MAX_CONTROLLER_ORDER + 1,
                             "invalid controller polynomial")
            if cls is FrequencyModulatorLUT:
                for key in ("pcmd", "values"):
                    array = kwargs[key]
                    _require(array.ndim == 1 and array.dtype.kind in "fiu" and 2 <= array.size <= 4096,
                             "invalid FM LUT array")
            result = cls(**kwargs)
            _validate_model(result)
            return result
        if "__enum__" in tags:
            _require(set(item) == {"__enum__", "value"}, "invalid enum fields")
            name = item["__enum__"]
            _require(type(name) is str and name in _ENUMS and type(item["value"]) is str,
                     "unsupported model enum")
            return _ENUMS[name](item["value"])
        if "__ndarray__" in tags:
            return _decode_array(item)
        if "__tuple__" in tags:
            _require(set(item) == {"__tuple__"} and type(item["__tuple__"]) is list, "invalid tuple")
            return tuple(decode(v) for v in item["__tuple__"])
        if "__complex__" in tags:
            pair = item["__complex__"]
            _require(set(item) == {"__complex__"} and type(pair) is list and len(pair) == 2
                     and all(_finite_number(v) for v in pair), "invalid complex value")
            return complex(*pair)
        return {key: decode(v) for key, v in item.items()}
    try:
        return decode(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise SessionFormatError(f"invalid model: {exc}") from exc


def llc_inputs(result) -> dict[str, Any]:
    """Extract the exact electrical request, including fields absent in the UI."""
    small = result.small_signal
    config = result.controller_config
    external = result.controller if config is None else None
    if config is not None:
        local = config.transfer_function()
        if (not np.array_equal(local.numerator, result.controller.numerator)
                or not np.array_equal(local.denominator, result.controller.denominator)):
            external = result.controller
    request = {
        "spec": small.spec,
        "small_signal": {
            "vbus_v": small.operating_point.vbus_v,
            "load_fraction": small.operating_point.load_fraction,
            "sample_time_s": small.sample_time_s,
            "control_input_kind": ControlInputKind(small.control_input_kind),
            "timer_clock_hz": small.timer_clock_hz,
            "input_delay_samples": small.discrete_plant.input_delay_samples,
            "series_resistance_ohm": small.parameters.series_resistance_ohm,
            "trim_frequency_to_output": small.steady_state.frequency_trimmed,
        },
        "small_signal_metadata": {
            key: getattr(small.parameters, key) for key in _GEOMETRY_FIELDS
        },
        "digital_loop": {
            "controller_config": config,
            "controller_transfer_function": external,
            "controller_source": result.controller_source,
            "fm_lut": result.fm_lut,
            "command_pu": result.fm_operating_point.command_pu,
            "analog_sense": result.analog_sense,
            "adc_sampling": result.adc_sampling,
            "command_timing": result.command_timing,
            "frequencies_hz": result.frequencies_hz,
        },
    }
    # Return independent objects; mutable specs/arrays must not alter snapshots.
    return decode_model(encode_model(request))


def ttpl_inputs(result) -> PFCControlLabConfig:
    return decode_model(encode_model(result.config))


def _llc_request(inputs: Any) -> dict:
    # Public replay accepts either the model dictionary or its encoded form.
    request = decode_model(inputs) if type(inputs) is dict and type(inputs.get("spec")) is dict else inputs
    required = {"spec", "small_signal", "digital_loop"}
    _require(type(request) is dict and required <= set(request) <= required | {"small_signal_metadata"},
             "invalid LLC input request")
    _require(type(request["spec"]) is LLCDesignSpec, "LLC request requires LLCDesignSpec")
    small, loop = request["small_signal"], request["digital_loop"]
    metadata = request.get("small_signal_metadata")
    if "small_signal_metadata" in request:
        _require(type(metadata) is dict and set(metadata) == set(_GEOMETRY_FIELDS),
                 "invalid LLC geometry metadata")
        for key, hint in _GEOMETRY_FIELDS.items():
            _require(_matches(metadata[key], hint) and metadata[key] >= 0,
                     f"invalid LLC geometry metadata: {key}")
    small_types = {
        "vbus_v": float, "load_fraction": float, "sample_time_s": float,
        "control_input_kind": ControlInputKind, "timer_clock_hz": float | None,
        "input_delay_samples": int, "series_resistance_ohm": float | None,
        "trim_frequency_to_output": bool,
    }
    loop_types = {
        "controller_config": PIControllerConfig | PIFControllerConfig | TwoP2ZControllerConfig | None,
        "controller_transfer_function": DigitalTransferFunction | None, "controller_source": str,
        "fm_lut": FrequencyModulatorLUT, "command_pu": float | None,
        "analog_sense": AnalogSenseConfig, "adc_sampling": ADCSamplingConfig,
        "command_timing": CommandTimingConfig, "frequencies_hz": np.ndarray | None,
    }
    for payload, hints, optional in (
        (small, small_types, {"series_resistance_ohm", "trim_frequency_to_output"}),
        (loop, loop_types, {"frequencies_hz"}),
    ):
        _require(type(payload) is dict and set(hints) - optional <= set(payload) <= set(hints),
                 "LLC request fields do not match schema")
        for key, value in payload.items():
            _require(_matches(value, hints[key]), f"invalid LLC input type: {key}")
    _require(small["vbus_v"] > 0 and 0 < small["load_fraction"] <= 1.5
             and small["sample_time_s"] > 0
             and (small.get("series_resistance_ohm") is None or small["series_resistance_ohm"] >= 0),
             "invalid LLC operating point")
    _require(small["timer_clock_hz"] is None or small["timer_clock_hz"] > 0, "invalid timer clock")
    _require(0 <= small["input_delay_samples"] <= MAX_DELAY_SAMPLES, "input delay exceeds limit")
    _require(loop["command_pu"] is None or 0 <= loop["command_pu"] <= 1, "FM command must be within 0..1")
    _require(loop["controller_config"] is not None or loop["controller_transfer_function"] is not None,
             "LLC request has no controller")
    frequencies = loop.get("frequencies_hz")
    if frequencies is not None:
        _require(frequencies.ndim == 1 and frequencies.dtype.kind in "fiu"
                 and 2 <= frequencies.size <= MAX_FREQUENCY_POINTS
                 and np.all(np.isfinite(frequencies)) and np.all(frequencies > 0)
                 and np.all(np.diff(frequencies) > 0), "invalid LLC frequency grid")
    timing, adc = loop["command_timing"], loop["adc_sampling"]
    controller = loop["controller_transfer_function"] or loop["controller_config"]
    _require(math.isclose(controller.sample_time_s, small["sample_time_s"], rel_tol=0.0, abs_tol=1e-15)
             and math.isclose(adc.control_sample_time_s, small["sample_time_s"], rel_tol=0.0, abs_tol=1e-15),
             "controller, plant and ADC sample times must match")
    if small["control_input_kind"] == ControlInputKind.TIMER_COUNTS:
        _require(small["timer_clock_hz"] is not None, "timer-count input requires a timer clock")
    request["spec"].validate()
    delay = (timing.computation_delay_s + timing.pwm_update_delay_s + adc.eoc_delay_s
             + 0.5 / request["spec"].minimum_frequency_hz)
    _require(delay / small["sample_time_s"] <= MAX_DELAY_SAMPLES, "command delay exceeds limit")
    # Validate known models even when caller supplies objects rather than JSON.
    encode_model(request)
    return request


def replay_llc(inputs):
    """Re-solve and re-linearize; no saved response or matrix is consumed."""
    request = _llc_request(inputs)
    small = build_small_signal_analysis(request["spec"], **request["small_signal"])
    # These dimensions are inert in the electrical model, but retained source
    # compatibility signatures include them. They cannot replace plant values.
    if "small_signal_metadata" in request:
        small = replace(small, parameters=replace(small.parameters, **request["small_signal_metadata"]))
    return build_digital_loop_analysis(small, **request["digital_loop"])


def replay_ttpl(inputs):
    from pfc_design.control.analysis import build_pfc_control_lab_analysis
    config = decode_model(inputs) if type(inputs) is dict else inputs
    _require(type(config) is PFCControlLabConfig, "TTPL request requires PFCControlLabConfig")
    encode_model(config)
    return build_pfc_control_lab_analysis(config)


def response_signature(frequencies_hz, *responses) -> str:
    """Exact response fingerprint for replay comparison, never an authenticity claim."""
    payload = encode_model([np.asarray(frequencies_hz), *(np.asarray(r) for r in responses)])
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def make_session(workspace: str, inputs: Any, solution_map: dict | None = None,
                 candidate_evidence: dict | None = None) -> dict:
    document = {
        "schema": SESSION_SCHEMA, "schema_version": SESSION_SCHEMA_VERSION,
        "model_revision": MODEL_REVISION, "toolkit_version": __version__,
        "workspace": workspace, "inputs": encode_model(inputs),
        "solution_map": {} if solution_map is None else solution_map,
        "candidate_evidence": {} if candidate_evidence is None else candidate_evidence,
    }
    return validate_session(document)


def validate_session(document: Any) -> dict:
    """Validate a raw envelope without treating historical metadata as evidence."""
    _check_json(document)
    required = {"schema", "schema_version", "model_revision", "workspace", "inputs"}
    allowed = required | {"toolkit_version", "solution_map", "candidate_evidence"}
    _require(type(document) is dict and required <= set(document) <= allowed, "invalid session envelope fields")
    _require(document["schema"] == SESSION_SCHEMA, "unsupported session schema")
    _require(type(document["schema_version"]) is int and document["schema_version"] == SESSION_SCHEMA_VERSION,
             "unsupported session schema version")
    _require(type(document["model_revision"]) is str and 0 < len(document["model_revision"]) <= 128,
             "model revision must be nonempty bounded text")
    workspace = document["workspace"]
    _require(type(workspace) is str and workspace in {"llc", "ttpl"}, "unsupported session workspace")
    for key in ("solution_map", "candidate_evidence"):
        _require(type(document.get(key, {})) is dict, f"{key} must be an object")
    _require(type(document.get("toolkit_version", "")) is str, "toolkit version must be text")
    from .candidate_evidence import validate_saved_evidence
    validate_saved_evidence(workspace, document.get("candidate_evidence"))
    inputs = decode_model(document["inputs"])
    if workspace == "llc":
        _llc_request(inputs)
    else:
        _require(type(inputs) is PFCControlLabConfig, "TTPL session requires PFCControlLabConfig")
    return document


def read_session(path: str | Path) -> dict:
    with Path(path).open("rb") as stream:
        data = stream.read(MAX_FILE_BYTES + 1)
    return validate_session(strict_json_loads(data))


def write_session(document: dict, path: str | Path) -> Path:
    validate_session(document)
    text = json.dumps(document, ensure_ascii=False, indent=2, allow_nan=False)
    _require(len(text.encode("utf-8")) <= MAX_FILE_BYTES, "session file exceeds size limit")
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=output.parent,
                                         prefix=f".{output.name}.", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(text)
        temporary.replace(output)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return output

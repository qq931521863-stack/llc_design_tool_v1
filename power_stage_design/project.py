"""Versioned PSD projects using the existing strict JSON/session safety contract."""
from __future__ import annotations

import json
import math
import tempfile
from dataclasses import asdict, fields
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from llc_design import __version__
from llc_design.control.design_session import MAX_FILE_BYTES, strict_json_loads

from . import MODEL_REVISION
from .models import DesignInputs
from .topologies import calculate_stage

SCHEMA = "power-design-toolkit/power-stage-project"
SCHEMA_VERSION = 1


def write_json(document: dict, path: str | Path) -> Path:
    """Atomic replace, as used by the existing LLC project/session writers."""
    text = json.dumps(document, ensure_ascii=False, indent=2, allow_nan=False)
    if len(text.encode("utf-8")) > MAX_FILE_BYTES:
        raise ValueError("Project exceeds the existing session size limit")
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=output.parent,
                                         prefix=f".{output.name}.", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(text + "\n")
        temporary.replace(output)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return output


def save_project(inputs: DesignInputs, path: str | Path, *, loop_settings: dict | None = None,
                 selected_device: dict | None = None, corner_request: dict | None = None,
                 parameter_suggestion: dict | None = None, loop_request: dict | None = None) -> Path:
    return write_json(project_document(inputs, loop_settings=loop_settings,
        selected_device=selected_device, corner_request=corner_request,
        parameter_suggestion=parameter_suggestion, loop_request=loop_request), path)


def project_document(inputs: DesignInputs, *, loop_settings: dict | None = None,
                     selected_device: dict | None = None, corner_request: dict | None = None,
                     parameter_suggestion: dict | None = None, loop_request: dict | None = None,
                     cancelled=None) -> dict:
    """Validate a snapshot without writing, so expensive checks can run off the UI thread."""
    result = calculate_stage(inputs, cancelled=cancelled)
    if loop_settings is not None:
        loop_settings = _canonical_loop_settings(inputs, loop_settings)
    if loop_request is not None:
        loop_request = validate_loop_request(loop_request)
    if selected_device is not None:
        _validate_device(selected_device)
    if corner_request is not None:
        corner_request = _validate_corners(corner_request, inputs, loop_settings)
    if parameter_suggestion is not None:
        from .suggestions import validate_suggestion_record
        parameter_suggestion=validate_suggestion_record(parameter_suggestion, result)
    if cancelled and cancelled():
        raise InterruptedError("Project save cancelled")
    return {
        "schema": SCHEMA, "schema_version": SCHEMA_VERSION,
        "toolkit_version": __version__, "model_revision": MODEL_REVISION,
        "saved_at": datetime.now(timezone.utc).isoformat(), "inputs": asdict(inputs),
        "result_identity": result.design_id,
        "result_policy": "Historical identity only; always recompute on restore",
        "loop_settings": loop_settings, "selected_device": selected_device, "corner_request": corner_request,
        "parameter_suggestion": parameter_suggestion, "loop_request": loop_request,
    }


def read_project(path: str | Path, *, cancelled=None) -> tuple[DesignInputs, dict[str, Any]]:
    source = Path(path)
    if source.stat().st_size > MAX_FILE_BYTES:
        raise ValueError("Project exceeds the existing session size limit")
    document = strict_json_loads(source.read_text(encoding="utf-8"))
    if not isinstance(document, dict) or document.get("schema") != SCHEMA:
        raise ValueError("This is not a Power Stage Designer project")
    if type(document.get("schema_version")) is not int or document["schema_version"] != SCHEMA_VERSION:
        raise ValueError("Unsupported Power Stage Designer project version")
    saved_revision=document.get("model_revision")
    values = document.get("inputs")
    if not isinstance(values, dict) or set(values) - {f.name for f in fields(DesignInputs)}:
        raise ValueError("Invalid or unknown power-stage input fields")
    try:
        inputs = DesignInputs(**values)
    except TypeError as exc:
        raise ValueError(f"Invalid power-stage inputs: {exc}") from exc
    if saved_revision != MODEL_REVISION:
        # These three M1 kernels retain the independently verified equations.
        # Extended v1 prototypes changed physical interpretation and cannot be
        # silently migrated just because their field names still deserialize.
        if saved_revision == "psd-piecewise-1" and inputs.topology in {"T01","T02","T09"}:
            document["restored_from_model_revision"]=saved_revision
            document["model_revision"]=MODEL_REVISION
        else:
            raise ValueError("Project model revision is incompatible; review inputs before migration")
    # Fully validate and recompute before the caller replaces any current UI state.
    result = calculate_stage(inputs,cancelled=cancelled)
    suggestion = document.get("parameter_suggestion")
    if suggestion is not None:
        from .suggestions import validate_suggestion_record
        document["parameter_suggestion"]=validate_suggestion_record(suggestion, result)
    settings = document.get("loop_settings")
    if settings is not None:
        settings = _canonical_loop_settings(inputs, settings)
        document["loop_settings"] = settings
    if document.get("loop_request") is not None:
        document["loop_request"] = validate_loop_request(document["loop_request"])
    selected = document.get("selected_device")
    if selected is not None:
        _validate_device(selected)
    corners=document.get("corner_request")
    if corners is not None:
        document["corner_request"] = _validate_corners(corners, inputs, settings)
    return inputs, document


def _canonical_loop_settings(inputs, settings):
    """Return the exact validated dataclasses, including accepted defaults.

    Validation and restoration must consume the same parameter set. Returning
    the sparse input dictionary used to let defaulted fields pass analysis and
    then fail in the GUI after replacing the active stage.
    """
    from .loop import Compensator, LoopPlant, analyze_loop

    if not isinstance(settings, dict) or set(settings) != {"plant", "compensator"}:
        raise ValueError("Invalid saved loop settings")
    try:
        plant = LoopPlant(**settings["plant"])
        compensator = Compensator(**settings["compensator"])
    except (TypeError, KeyError) as exc:
        raise ValueError("Invalid loop parameter fields") from exc
    # The active model does not consume every stored field (e.g. VMC does not
    # use sense_gain). All fields are nevertheless restored by the desktop, so
    # validate their representation before it replaces any existing project.
    enums = {"topology":{"buck","boost","inverting_buck_boost","flyback","forward"},
             "control_mode":{"vmc","peak_cmc"},"slope_mode":{"fixed","multiplier"},
             "network":{"type_ii","type_ii_gm","type_iii","isolated_zener","isolated_inner"}}
    for model in (plant,compensator):
        for key,value in asdict(model).items():
            if key in enums:
                if not isinstance(value,str) or value not in enums[key]:
                    raise ValueError(f"Invalid loop {key}")
            elif key=="output_resistance_ohm" and value is None:
                continue
            elif type(value) not in (int,float) or not math.isfinite(value):
                raise ValueError(f"Invalid loop {key}: a finite numeric value is required")
    _check_loop_identity(inputs, plant)
    analyze_loop(plant, compensator)
    return {"plant": asdict(plant), "compensator": asdict(compensator)}


def validate_loop_request(request):
    """Preserve synthesis intent separately from achieved controller evidence."""
    keys = {"target_crossover_hz", "target_phase_margin_deg"}
    if not isinstance(request, dict) or not keys <= set(request) or set(request) - keys - {"preferred_series"}:
        raise ValueError("Invalid loop synthesis request")
    for key in keys:
        value = request[key]
        if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
            raise ValueError(f"Invalid {key}: a finite positive target is required")
    if request["target_phase_margin_deg"] >= 180:
        raise ValueError("Target phase margin must be below 180 degrees")
    # A target above the model's useful band can be saved as intent. Only the
    # actual synthesis action determines reachability; a saved request is not
    # evidence that the current compensator achieves it.
    series = request.get("preferred_series", 24)
    if series is not None and (type(series) is not int or series not in (12, 24)):
        raise ValueError("Preferred series must be E12, E24, or unrounded")
    return {**request, "preferred_series": series}


def _validate_corners(corners, inputs=None, loop_settings=None):
    from .corners import (
        CornerRequest,
        validate_corner_dimensions,
        validate_corner_request,
    )
    names = {f.name for f in fields(CornerRequest)}
    legacy = {"input_voltages_v", "output_currents_a", "inductance_factors", "capacitance_factors"}
    if not isinstance(corners,dict) or set(corners)-names or not legacy <= set(corners):
        raise ValueError("Invalid corner scan request")
    if any(not isinstance(group,(list,tuple)) for group in corners.values()):
        raise ValueError("Invalid corner scan values")
    request = CornerRequest(**corners)
    validate_corner_request(request)
    if inputs is not None:
        from .loop import LoopPlant
        plant = LoopPlant(**loop_settings["plant"]) if loop_settings is not None else None
        validate_corner_dimensions(inputs, request, plant)
    # Older projects omitted the new dimensions: all default to unchanged, not
    # to the current GUI's potentially edited values. Never overwrite a list.
    return {key:list(value) for key,value in asdict(request).items()}


def _validate_device(selected):
    from llc_design.core.spec import MosfetSpec
    if not isinstance(selected, dict):
        raise ValueError("Invalid selected device record")  # noqa: TRY004 - project schema errors use ValueError
    try:
        device = MosfetSpec(**selected)
    except TypeError as exc:
        raise ValueError("Invalid or incomplete selected device record") from exc
    for key, value in asdict(device).items():
        if key in {"part_number", "technology", "package"}:
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"Invalid device {key}")
        elif type(value) not in (float, int) or not math.isfinite(value) or value < 0:
            raise ValueError(f"Invalid device {key}")
    if device.vds_max_v <= 0 or device.id_cont_a <= 0:
        raise ValueError("Device ratings must be positive")
    return device


def _check_loop_identity(inputs, plant) -> None:
    result = calculate_stage(inputs)
    mapping = {"T01":"buck", "T02":"boost", "T03":"inverting_buck_boost", "T09":"flyback", "T13":"forward", "T14":"forward"}
    if mapping.get(result.topology_id) != plant.topology:
        raise ValueError("Saved loop belongs to a different or unsupported topology")
    if result.mode != "CCM":
        raise ValueError("Analog loop requires strict CCM")
    if inputs.diode_drop_v != 0 or inputs.dead_time_s != 0:
        raise ValueError("Ideal analog loop cannot be attached to a nonideal stage")
    if inputs.turns_ratio_np_ns != plant.turns_ratio:
        raise ValueError("Saved loop belongs to a different turns ratio")
    for key in ("vin_v", "iout_a", "fsw_hz", "inductance_h", "capacitance_f"):
        if getattr(inputs, key) != getattr(plant, key):
            raise ValueError(f"Saved loop belongs to different {key}")
    if abs(plant.vout_v) != inputs.vout_v:
        raise ValueError("Saved loop belongs to different output voltage")

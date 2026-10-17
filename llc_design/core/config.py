"""JSON configuration import/export helpers."""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any
import tempfile

from llc_design import __version__

from .spec import LLCDesignSpec, PrimaryTopology, SecondaryTopology, TankParameterMode

PROJECT_SCHEMA_VERSION = 1


def _normalize(data: dict) -> dict:
    normalized = dict(data)
    for key in ("transformer_core_families", "resonant_inductor_core_families"):
        if key in normalized:
            if not isinstance(normalized[key], (list, tuple)):
                raise ValueError(f"{key} must be a list")
            normalized[key] = tuple(normalized[key])
    if "primary_topology" in normalized:
        normalized["primary_topology"] = PrimaryTopology(normalized["primary_topology"])
    if "secondary_topology" in normalized:
        normalized["secondary_topology"] = SecondaryTopology(normalized["secondary_topology"])
    if "parameter_mode" in normalized:
        normalized["parameter_mode"] = TankParameterMode(normalized["parameter_mode"])
    return normalized


def load_project(path: str | Path) -> tuple[LLCDesignSpec, dict | None]:
    """Read legacy specs and versioned projects without trusting saved evidence."""
    from ..control.design_session import strict_json_loads, validate_session, encode_model, MAX_FILE_BYTES

    source = Path(path)
    if source.stat().st_size > MAX_FILE_BYTES:
        raise ValueError("Project file is too large")
    document = strict_json_loads(source.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise ValueError("Project JSON must be an object")
    session = None
    if "spec" in document:
        if not isinstance(document["spec"], dict):
            raise ValueError("Project spec must be an object")
        if "schema" in document and document["schema"] != "power-design-toolkit/project":
            raise ValueError("Unsupported project schema")
        if "schema_version" in document and (type(document["schema_version"]) is not int
                                              or document["schema_version"] != PROJECT_SCHEMA_VERSION):
            raise ValueError("Unsupported project schema version")
        session = document.get("control_session")
        if session is not None:
            validate_session(session)
            if session["workspace"] != "llc":
                raise ValueError("An LLC project cannot contain a different control workspace")
        data = document["spec"]
    else:
        data = document
    spec = LLCDesignSpec(**_normalize(data))
    spec.validate()
    encode_model(spec)
    return spec, session


def load_spec(path: str | Path) -> LLCDesignSpec:
    return load_project(path)[0]


def save_spec(spec: LLCDesignSpec, path: str | Path) -> Path:
    data = _spec_payload(spec)
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    return output


def _spec_payload(spec: LLCDesignSpec) -> dict[str, Any]:
    data = asdict(spec)
    for key, value in list(data.items()):
        if isinstance(value, Enum):
            data[key] = value.value
    return data


def create_project_document(spec: LLCDesignSpec, analysis: Any | None = None,
                            control_session: dict | None = None) -> dict[str, Any]:
    from ..validation.provenance import validate_bundled_data

    analysis_state: dict[str, Any] = {"status": "not_run"}
    if analysis is not None:
        nominal = analysis.nominal
        analysis_state = {
            "status": "complete",
            "feasible": bool(analysis.feasible),
            "feasibility_reasons": list(analysis.feasibility_reasons),
            "warnings": list(analysis.warnings),
            "nominal": {
                "switching_frequency_hz": float(
                    nominal.operating_point.switching_frequency_hz
                ),
                "total_loss_w": float(nominal.total_loss_w),
                "efficiency": float(nominal.efficiency),
            },
        }

    document = {
        "schema": "power-design-toolkit/project",
        "schema_version": PROJECT_SCHEMA_VERSION,
        "toolkit_version": __version__,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "spec": _spec_payload(spec),
        "analysis": analysis_state,
        "data_provenance": validate_bundled_data().to_dict(),
    }
    if control_session is not None:
        from ..control.design_session import validate_session
        validate_session(control_session)
        document["control_session"] = control_session
    return document


def save_project(spec: LLCDesignSpec, path: str | Path, analysis: Any | None = None,
                 control_session: dict | None = None) -> Path:
    data = create_project_document(spec, analysis, control_session)
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    from ..control.design_session import MAX_FILE_BYTES
    text = json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False)
    if len(text.encode("utf-8")) > MAX_FILE_BYTES:
        raise ValueError("Project file is too large")
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

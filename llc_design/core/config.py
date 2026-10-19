"""JSON configuration import/export helpers."""

from __future__ import annotations

import hashlib
import json
import math
import tempfile
from dataclasses import asdict
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from llc_design import __version__

from .spec import (
    IntegratedInductanceSource,
    LLCDesignSpec,
    MagneticsMode,
    PrimaryTopology,
    SecondaryTopology,
    TankParameterMode,
)

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
    if "magnetics_mode" in normalized:
        normalized["magnetics_mode"] = MagneticsMode(normalized["magnetics_mode"])
    if "integrated_inductance_source" in normalized:
        normalized["integrated_inductance_source"] = IntegratedInductanceSource(normalized["integrated_inductance_source"])
    return normalized


def load_project(path: str | Path, *, include_waveform: bool = False, include_optimization: bool = False):
    """Read legacy specs and versioned projects without trusting saved evidence."""
    from ..control.design_session import (
        MAX_FILE_BYTES,
        encode_model,
        strict_json_loads,
        validate_session,
    )

    source = Path(path)
    if source.stat().st_size > MAX_FILE_BYTES:
        raise ValueError("Project file is too large")
    document = strict_json_loads(source.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise ValueError("Project JSON must be an object")
    session = None
    waveform_session = None
    optimization_session = None
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
        waveform_session = document.get("waveform_session")
        optimization_session = document.get("optimization_session")
        data = document["spec"]
    else:
        data = document
    spec = LLCDesignSpec(**_normalize(data))
    spec.validate()
    encode_model(spec)
    if waveform_session is not None:
        validate_waveform_session(waveform_session, spec)
    if optimization_session is not None:
        from ..optimization.two_stage import validate_two_stage_session
        optimization_session = validate_two_stage_session(optimization_session)
    result = (spec, session)
    if include_waveform:
        result += (waveform_session,)
    if include_optimization:
        result += (optimization_session,)
    return result


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
                            control_session: dict | None = None,
                            waveform_session: dict | None = None,
                            optimization_session: dict | None = None) -> dict[str, Any]:
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
    if waveform_session is not None:
        document["waveform_session"] = validate_waveform_session(waveform_session, spec)
    if optimization_session is not None:
        from ..optimization.two_stage import validate_two_stage_session
        document["optimization_session"] = validate_two_stage_session(optimization_session)
    return document


def save_project(spec: LLCDesignSpec, path: str | Path, analysis: Any | None = None,
                 control_session: dict | None = None, waveform_session: dict | None = None,
                 optimization_session: dict | None = None) -> Path:
    data = create_project_document(spec, analysis, control_session, waveform_session, optimization_session)
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


WAVEFORM_MODEL_REVISION = "llc-component-waveforms-v1"
WAVEFORM_SOLVERS = {'fast': {'cycles': 2, 'samples_per_cycle': 1024},
 'harmonic': {'max_harmonic': 7,
              'adaptive_harmonics': True,
              'minimum_harmonic': 5,
              'harmonic_convergence_tolerance': 0.00025,
              'samples_per_cycle': 1024,
              'output_cycles': 2,
              'series_resistance_ohm': None,
              'magnetizing_series_resistance_ohm': 0.0,
              'rectifier_smoothing_current_a': 0.02,
              'rectifier_smoothing_fraction': 0.001,
              'use_exact_rectifier_projection': True,
              'allow_regularized_fallback': True,
              'zero_crossing_samples': 2048,
              'residual_tolerance': 2e-06,
              'maximum_function_evaluations': 500,
              'output_voltage_tolerance_v': 0.02,
              'frequency_scan_points': 17,
              'frequency_tolerance_hz': 0.05,
              'include_primary_deadtime': True,
              'strict_convergence': True},
 'detailed': {'samples_per_cycle': 512,
              'output_cycles': 2,
              'minimum_settling_cycles': 20,
              'maximum_settling_cycles': 500,
              'convergence_tolerance': 1e-08,
              'rectifier_smoothing_current_a': 0.02,
              'use_periodic_shooting': True,
              'shooting_max_evaluations': 80}}
WAVEFORM_COMPONENTS = frozenset({
    "", "bus", "bridge", "transformer", "sr", "output", "lr", "cr", "lm",
    "primary", "secondary", "co", "coesr", "rseries", "load", "q1", "q2", "q3", "q4",
    "sr1", "sr2", "sr3", "sr4",
})


def waveform_spec_identity(spec: LLCDesignSpec) -> str:
    """Identity includes every physical spec input, even fields not shown in the GUI."""
    payload = _spec_payload(spec)
    defaults = _spec_payload(LLCDesignSpec())
    added = {key for key in defaults if key == "magnetics_mode" or key.startswith("integrated_")}
    if all(payload[key] == defaults[key] for key in added):
        # Legacy separate-magnetics waveform sessions retain the exact identity.
        payload = {key: value for key, value in payload.items() if key not in added}
    return hashlib.sha256(json.dumps(payload, sort_keys=True,
                                     allow_nan=False).encode()).hexdigest()


def create_waveform_session(spec: LLCDesignSpec, work_point: dict, solver: str,
                            component: str = "", group: int = 0, plant_inputs: dict | None = None) -> dict:
    state = {
        "schema": "power-design-toolkit/waveform-session", "schema_version": 1,
        "model_revision": WAVEFORM_MODEL_REVISION,
        "spec_identity": waveform_spec_identity(spec), "solver": solver,
        "solver_options": dict(WAVEFORM_SOLVERS.get(solver, {})),
        "work_point": dict(work_point), "component": component, "group": group,
        "plant_inputs": plant_inputs,
    }
    return validate_waveform_session(state, spec)


def validate_waveform_session(state: dict, spec: LLCDesignSpec) -> dict:
    """Strict input-only state. Saved arrays/accepted flags are never authority."""
    fields = {"schema", "schema_version", "model_revision", "spec_identity", "solver",
              "solver_options", "work_point", "component", "group", "plant_inputs"}
    if not isinstance(state, dict) or set(state) != fields:
        raise ValueError("Unsupported waveform session fields")
    if (state["schema"] != "power-design-toolkit/waveform-session"
            or type(state["schema_version"]) is not int or state["schema_version"] != 1
            or state["model_revision"] != WAVEFORM_MODEL_REVISION):
        raise ValueError("Unsupported waveform session/model version; recomputation requires compatible inputs")
    if state["spec_identity"] != waveform_spec_identity(spec):
        raise ValueError("Waveform session and project spec do not match")
    solver = state["solver"]
    if type(solver) is not str or solver not in WAVEFORM_SOLVERS:
        raise ValueError("Unsupported waveform solver")
    options = state["solver_options"]
    expected = WAVEFORM_SOLVERS[solver]
    if (not isinstance(options, dict) or options != expected
            or any(type(v) is not type(expected[key]) for key, v in options.items())):
        raise ValueError("Unsupported waveform solver options")
    work_point = state["work_point"]
    if not isinstance(work_point, dict) or set(work_point) != {"vbus_v", "load_fraction"}:
        raise ValueError("Invalid waveform work point")
    for key, low, high in (("vbus_v", 20, 2000), ("load_fraction", .01, 1.5)):
        value = work_point[key]
        if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
            raise ValueError(f"Invalid waveform {key}")
    plant = state["plant_inputs"]
    if plant is not None:
        geometry = {"transformer_core_area_m2", "transformer_magnetic_path_m",
                    "resonant_inductor_turns", "resonant_inductor_core_area_m2",
                    "resonant_inductor_magnetic_path_m"}
        if (not isinstance(plant, dict) or set(plant) != geometry | {"series_resistance_ohm", "source"}
                or type(plant["source"]) is not str or len(plant["source"]) > 512):
            raise ValueError("Invalid frozen waveform electrical inputs")
        for key in geometry | {"series_resistance_ohm"}:
            value = plant[key]
            if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 1e6:
                raise ValueError(f"Invalid frozen waveform electrical input: {key}")
        if type(plant["resonant_inductor_turns"]) is not int:
            raise ValueError("Invalid resonant inductor turn count")
        if not 0 < plant["series_resistance_ohm"] <= 1000:
            raise ValueError("Invalid equivalent series resistance")
        if solver == "harmonic":
            raise ValueError("Harmonic solver does not consume EDF electrical inputs")
    component = state["component"]
    if type(component) is not str or component not in WAVEFORM_COMPONENTS:
        raise ValueError("Unknown waveform component")
    if spec.primary_topology == PrimaryTopology.HALF_BRIDGE and component in {"q3", "q4"}:
        raise ValueError("Half bridge has no Q3/Q4")
    if type(state["group"]) is not int or not 0 <= state["group"] < 7:
        raise ValueError("Unknown waveform group")
    # Round-trip returns a detached bounded snapshot; callers cannot mutate saved input.
    return json.loads(json.dumps(state, allow_nan=False))


def waveform_request_identity(state: dict) -> str:
    inputs = {key: value for key, value in state.items() if key not in {"component", "group"}}
    return hashlib.sha256(json.dumps(inputs, sort_keys=True, allow_nan=False).encode()).hexdigest()

"""Traceable candidate assessments; saved evidence is never an execution authority.

Only a freshly evaluated and explicitly applied candidate creates current evidence.
Hashes bind exact inputs and coefficients, not authenticity or hardware validity.
The small mutable validity envelope lets GUI edits invalidate every consumer of an
otherwise frozen completed analysis without changing its engineering results.
"""
from __future__ import annotations

from dataclasses import fields, is_dataclass
from enum import Enum
import hashlib
import json
import math

import numpy as np


def _canonical(value):
    if isinstance(value, Enum):
        return {"enum": type(value).__name__, "value": value.value}
    if is_dataclass(value):
        return {"type": type(value).__name__, "fields": {
            item.name: _canonical(getattr(value, item.name)) for item in fields(value)
        }}
    if isinstance(value, np.ndarray):
        return _canonical(value.tolist())
    if isinstance(value, np.generic):
        return _canonical(value.item())
    if isinstance(value, complex):
        return {"real": _canonical(value.real), "imag": _canonical(value.imag)}
    if isinstance(value, float) and not math.isfinite(value):
        return {"nonfinite": str(value)}
    if isinstance(value, dict):
        return {str(key): _canonical(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_canonical(item) for item in value]
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    raise TypeError(f"Unsupported evidence value: {type(value).__name__}")


def exact_signature(*values) -> str:
    payload = json.dumps(_canonical(values), sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def controller_identity(controller, config=None) -> dict:
    payload = {
        "kind": type(config).__name__ if config is not None else "external_hz",
        "config": ({item.name: _canonical(getattr(config, item.name)) for item in fields(config)}
                   if config is not None else None),
        "numerator_z_minus": controller.numerator.tolist(),
        "denominator_z_minus": controller.denominator.tolist(),
        "sample_time_s": float(controller.sample_time_s),
    }
    return {**payload, "sha256": exact_signature(payload)}


def llc_model_signature(result) -> str:
    return exact_signature(
        result.small_signal, result.fm_lut, result.fm_operating_point,
        result.analog_sense, result.adc_sampling, result.command_timing,
        result.frequencies_hz,
    )


def ttpl_model_signature(config, loop_key: str, frequencies_hz) -> str:
    # The selected controller is bound separately. The outer loop's plant also
    # depends on the exact inner controller, so preserve the other controller.
    inputs = {item.name: getattr(config, item.name) for item in fields(config)
              if item.name != f"{loop_key}_controller"}
    return exact_signature(loop_key, inputs, frequencies_hz)


def _point_payload(point) -> dict:
    payload = {}
    unavailable = []
    for item in fields(point):
        if item.name in ("corner_ids", "corner_results"):
            continue
        value = getattr(point, item.name)
        if item.name == "status":
            value = value.name
        elif isinstance(value, (float, np.floating)) and not math.isfinite(value):
            unavailable.append(item.name)
            value = None
        payload[item.name] = value
    payload["nonfinite_metrics"] = unavailable
    return payload


def build_candidate_evidence(*, point, corners, constraints, controller, loop_key: str,
                             model_signature: str, compatibility_key: str,
                             conditional_approved: bool = False, source_models: dict | None = None) -> dict:
    """Capture the unchanged assessment before Apply consumes its GUI preview."""
    status = point.status.name
    if status not in ("FEASIBLE", "CONDITIONAL"):
        raise ValueError("Failed or unavailable candidates cannot be adopted")
    if status == "CONDITIONAL" and not conditional_approved:
        raise ValueError("Conditional candidate requires explicit adoption")
    from .digital_loop import PIControllerConfig
    if type(controller) is not PIControllerConfig:
        raise ValueError("Candidate assessment requires the evaluated PI controller")
    if (not math.isfinite(controller.sample_time_s) or controller.sample_time_s <= 0
            or point.kp != controller.kp or point.ti_s != controller.ti_s):
        raise ValueError("Candidate controller differs from its common assessment")
    ids = tuple(corner.corner_id for corner in corners)
    if not corners or ids != tuple(point.corner_ids) or len(point.corner_results) != len(corners):
        raise ValueError("Candidate operating-point evidence no longer matches its sources")
    if any(not math.isclose(corner.sample_rate_hz, 1.0 / controller.sample_time_s,
                            rel_tol=1e-12, abs_tol=0.0) for corner in corners):
        raise ValueError("Candidate controller sample time differs from its operating-point assessment")
    for result in point.corner_results:
        if result.status.name not in ("FEASIBLE", "CONDITIONAL"):
            raise ValueError("Failed or unavailable operating-point evidence cannot be adopted")
        if result.kp != controller.kp or result.ti_s != controller.ti_s:
            raise ValueError("Candidate controller differs from its operating-point assessment")
    if (status == "FEASIBLE" and any(item.status.name != "FEASIBLE" for item in point.corner_results)):
        raise ValueError("Common assessment cannot pass an unavailable operating point")
    if any(_point_payload(item)["nonfinite_metrics"] for item in (point, *point.corner_results)):
        raise ValueError("Nonfinite candidate metrics cannot be accepted as current evidence")
    records = []
    for corner, result in zip(corners, point.corner_results):
        records.append({
            "id": corner.corner_id, "provenance": corner.provenance,
            "sample_rate_hz": float(corner.sample_rate_hz),
            "switching_frequency_hz": float(corner.switching_frequency_hz),
            "frequency_range_hz": [float(corner.frequencies_hz[0]), float(corner.frequencies_hz[-1])],
            "frequency_sample_count": len(corner.frequencies_hz),
            "response_sha256": exact_signature(corner.frequencies_hz, corner.fixed_loop_response),
            "model_signature": (exact_signature(source_models[corner.corner_id])
                                if source_models and source_models.get(corner.corner_id) is not None else None),
            "assessment": _point_payload(result),
        })
    from .design_session import MODEL_REVISION
    from llc_design import __version__
    evidence = {
        "schema_version": 1, "model_revision": MODEL_REVISION, "toolkit_version": __version__,
        "loop_key": loop_key,
        "freshness": "current", "accepted_current": True,
        "freshness_reason": "Fresh candidate explicitly applied in this session.",
        "assessment": _point_payload(point),
        "conditional_adoption": status == "CONDITIONAL" and bool(conditional_approved),
        "controller": controller_identity(controller.transfer_function(), controller),
        "model_signature": model_signature, "compatibility_signature": compatibility_key,
        "constraints": {item.name: getattr(constraints, item.name) for item in fields(constraints)},
        "operating_points": records,
        "scope": "Frequency-grid assessment only; pole stability and hardware validation UNKNOWN.",
    }
    # Enforce strict portability at the producer, rather than silently writing NaN.
    json.dumps(evidence, allow_nan=False)
    return evidence


def invalidate_evidence(evidence: dict | None, reason: str) -> None:
    """Invalidate the shared validity envelope without losing original results."""
    if evidence:
        evidence.update(freshness="invalidated", accepted_current=False, freshness_reason=reason)


def _validate_record(record) -> None:
    """Validate data shape only. A valid saved shape still grants no authority."""
    required = {"schema_version", "model_revision", "toolkit_version", "loop_key", "freshness", "accepted_current", "freshness_reason",
                "assessment", "conditional_adoption", "controller", "model_signature",
                "compatibility_signature", "constraints", "operating_points", "scope"}
    if not isinstance(record, dict) or set(record) != required or type(record["schema_version"]) is not int or record["schema_version"] != 1:
        raise ValueError("Invalid saved candidate evidence schema")
    if record["loop_key"] not in ("current", "voltage"):
        raise ValueError("Invalid saved candidate loop")
    if type(record["accepted_current"]) is not bool or type(record["conditional_adoption"]) is not bool:
        raise ValueError("Invalid saved candidate acceptance fields")
    for key in ("model_revision", "toolkit_version", "freshness", "freshness_reason", "model_signature", "compatibility_signature", "scope"):
        if not isinstance(record[key], str) or len(record[key]) > 65536:
            raise ValueError("Invalid saved candidate metadata")
    expected_metrics = {"target_crossover_hz", "target_phase_margin_deg", "kp", "ti_s",
                        "actual_crossover_hz", "actual_phase_margin_deg", "gain_margin_db",
                        "ms", "mt", "switching_loop_gain_db", "controller_phase_deg"}

    def assessment(value):
        if not isinstance(value, dict) or set(value) != expected_metrics | {"status", "message", "nonfinite_metrics"}:
            raise ValueError("Invalid saved operating-point assessment")
        if not isinstance(value["status"], str) or not isinstance(value["message"], str):
            raise ValueError("Invalid saved assessment text")
        if (not isinstance(value["nonfinite_metrics"], list)
                or any(not isinstance(item, str) or item not in expected_metrics for item in value["nonfinite_metrics"])):
            raise ValueError("Invalid saved unavailable metrics")
        if any(value[key] is not None and (type(value[key]) not in (int, float) or not math.isfinite(value[key]))
               for key in expected_metrics):
            raise ValueError("Invalid saved numeric assessment")

    assessment(record["assessment"])
    controller = record["controller"]
    if (not isinstance(controller, dict) or set(controller) != {
            "kind", "config", "numerator_z_minus", "denominator_z_minus", "sample_time_s", "sha256"}
            or not isinstance(controller["kind"], str) or not isinstance(controller["sha256"], str)
            or not isinstance(controller["config"], (dict, type(None)))):
        raise ValueError("Invalid saved controller identity")
    for key in ("numerator_z_minus", "denominator_z_minus"):
        values = controller[key]
        if (not isinstance(values, list) or not 1 <= len(values) <= 33
                or any(type(value) not in (int, float) or not math.isfinite(value) for value in values)):
            raise ValueError("Invalid saved controller coefficients")
    if type(controller["sample_time_s"]) not in (int, float) or not math.isfinite(controller["sample_time_s"]) or controller["sample_time_s"] <= 0:
        raise ValueError("Invalid saved controller sample time")
    from .solution_map import SolutionMapConstraints
    constraint_fields = {item.name for item in fields(SolutionMapConstraints)}
    if not isinstance(record["constraints"], dict) or set(record["constraints"]) != constraint_fields:
        raise ValueError("Invalid saved candidate constraints")
    try:
        SolutionMapConstraints(**record["constraints"]).validate()
    except (TypeError, ValueError) as exc:
        raise ValueError("Invalid saved candidate constraints") from exc
    points = record["operating_points"]
    if not isinstance(points, list) or not 1 <= len(points) <= 33:
        raise ValueError("Invalid saved candidate operating points")
    point_fields = {"id", "provenance", "sample_rate_hz", "switching_frequency_hz", "frequency_range_hz",
                    "frequency_sample_count", "response_sha256", "model_signature", "assessment"}
    for point in points:
        if not isinstance(point, dict) or set(point) != point_fields:
            raise ValueError("Invalid saved operating-point evidence")
        if any(not isinstance(point[key], str) for key in ("id", "provenance", "response_sha256")):
            raise ValueError("Invalid saved operating-point identity")
        if point["model_signature"] is not None and not isinstance(point["model_signature"], str):
            raise ValueError("Invalid saved operating-point model signature")
        if any(type(point[key]) not in (int, float) or not math.isfinite(point[key]) or point[key] <= 0
               for key in ("sample_rate_hz", "switching_frequency_hz")):
            raise ValueError("Invalid saved operating-point rate")
        band = point["frequency_range_hz"]
        if (not isinstance(band, list) or len(band) != 2
                or any(type(value) not in (int, float) or not math.isfinite(value) for value in band)
                or not 0 < band[0] < band[1] or type(point["frequency_sample_count"]) is not int
                or not 3 <= point["frequency_sample_count"] <= 100000):
            raise ValueError("Invalid saved operating-point frequency coverage")
        assessment(point["assessment"])
    json.dumps(record, allow_nan=False)


def validate_saved_evidence(workspace: str, payload) -> dict:
    """Preflight before reflecting any loaded fields; return historical copies."""
    if payload is None:
        return {}
    if not isinstance(payload, dict):
        raise ValueError("Saved candidate evidence must be an object")
    if workspace == "llc":
        if payload and payload.get("loop_key") != "voltage":
            raise ValueError("LLC saved evidence must belong to the voltage loop")
        return historical_evidence(payload) or {}
    if workspace != "ttpl" or set(payload) - {"current", "voltage"}:
        raise ValueError("Invalid saved candidate evidence workspace or loop")
    records = {}
    for key, record in payload.items():
        _validate_record(record)
        if record["loop_key"] != key:
            raise ValueError("Saved candidate loop identity mismatch")
        records[key] = historical_evidence(record)
    return records


def historical_evidence(evidence: dict | None, reason: str = "Reopened session; recompute and Apply required.") -> dict | None:
    if evidence is None:
        return None
    if not isinstance(evidence, dict):
        raise ValueError("Saved candidate evidence must be an object")
    if not evidence:
        return None
    _validate_record(evidence)
    copy = json.loads(json.dumps(evidence, allow_nan=False))
    copy.update(freshness="historical", accepted_current=False, freshness_reason=reason)
    return copy


def check_evidence(evidence: dict | None, *, controller, config, model_signature: str) -> dict | None:
    """Can only retain or downgrade evidence, never promote stored assessments."""
    if not evidence:
        return None
    from .design_session import MODEL_REVISION
    assessment = evidence.get("assessment", {})
    if (evidence.get("schema_version") != 1
            or evidence.get("model_revision") != MODEL_REVISION
            or assessment.get("status") not in ("FEASIBLE", "CONDITIONAL")
            or (assessment.get("status") == "CONDITIONAL" and not evidence.get("conditional_adoption"))
            or any(item.get("assessment", {}).get("status") not in ("FEASIBLE", "CONDITIONAL")
                   for item in evidence.get("operating_points", []))
            or evidence.get("controller") != controller_identity(controller, config)
            or evidence.get("model_signature") != model_signature):
        invalidate_evidence(evidence, "Controller or model inputs changed; recompute and Apply required.")
    return evidence


def llc_export_evidence(result) -> dict | None:
    evidence = check_evidence(result.candidate_evidence, controller=result.controller,
                              config=result.controller_config, model_signature=llc_model_signature(result))
    return json.loads(json.dumps(evidence, allow_nan=False)) if evidence else None


def ttpl_export_evidence(analysis) -> dict:
    evidence = {}
    for key, record in analysis.candidate_evidence.items():
        if key not in ("current", "voltage"):
            continue
        check_evidence(record, controller=getattr(analysis, key + "_loop").controller,
                       config=getattr(analysis.config, key + "_controller"),
                       model_signature=ttpl_model_signature(analysis.config, key, getattr(analysis, key + "_loop").frequencies_hz))
        evidence[key] = json.loads(json.dumps(record, allow_nan=False))
    return evidence

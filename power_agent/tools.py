"""Deterministic engineering tools exposed to Agent/MCP front ends.

LLMs may choose and explain these tools; they do not replace the underlying
numerical kernels or change the evidence status of their results.
"""

from __future__ import annotations

from typing import Any

from llc_design.validation.provenance import validate_bundled_data
from webapp.service import analyze_llc, default_payload

from .contracts import (
    CoefficientRequest,
    ControlRequest,
    CornerRequest,
    LLCSpecInput,
    OptimizationRequest,
)
from .storage import DesignStore, checked_runtime, evidence, json_value

AGENT_TOOLS = ["toolkit_capabilities", "llc_defaults", "llc_analyze", "validate_engineering_data",
    "design_validate_spec", "llc_create_design", "llc_submit_analysis", "llc_optimize",
    "llc_check_corners", "job_get", "job_cancel", "design_export", "control_auto_design",
    "control_export_verified_c99"]


def toolkit_capabilities() -> dict[str, Any]:
    """Return machine-readable capability and evidence-boundary metadata."""

    return {
        "product": "Power Design Toolkit",
        "workspaces": [
            "LLC Design",
            "PFC Design",
            "Control Tools",
            "FRA Loop Designer",
        ],
        "agent_tools": AGENT_TOOLS,
        "workflow": ["design_validate_spec", "llc_create_design", "llc_submit_analysis / llc_optimize / llc_check_corners",
                     "job_get", "design_export"],
        "limits": {"concurrent_jobs_per_server": 1, "timeout_seconds_max": 600,
                   "optimization_candidates_max": 32, "explicit_corners_max": 128},
        "engineering_contract": {
            "llm_role": "interpret requirements, select tools, explain results",
            "kernel_role": "deterministic engineering computation",
            "evidence_statuses": ["VERIFIED", "UNKNOWN"],
            "hardware_rule": (
                "software/model evidence never implies hardware verification; "
                "verified_for_hardware is a separate gate"
            ),
        },
    }


def llc_defaults() -> dict[str, Any]:
    """Return the bounded LLC input schema/defaults used by the shared service."""

    return {**default_payload(), "input_schema": LLCSpecInput.model_json_schema(),
            "units": "DC volts, watts, Hz, H, F, ohm, seconds, degrees C; load is per-unit rated power"}


def llc_analyze(spec: dict[str, Any]) -> dict[str, Any]:
    """Run the shared LLC deterministic kernel and attach evidence semantics."""

    payload = LLCSpecInput.model_validate(spec).model_dump(exclude_unset=True)
    runtime = checked_runtime()
    result = analyze_llc(payload)
    checked_runtime()
    result["runtime"] = runtime
    result["agent_evidence"] = {
        "status": "VERIFIED",
        "scope": "execution of the repository software model for the supplied input",
        "verified_for_hardware": False,
        "boundary": (
            "The returned result is a software-model calculation. Device/material "
            "data and bench correlation must be verified separately for hardware release."
        ),
    }
    return result


def validate_engineering_data() -> dict[str, Any]:
    """Return the current bundled LLC data-provenance report."""

    return validate_bundled_data().to_dict()


def design_validate_spec(spec: dict[str, Any]) -> dict:
    from dataclasses import asdict

    from webapp.service import spec_from_payload
    payload = LLCSpecInput.model_validate(spec).model_dump(exclude_unset=True)
    effective = spec_from_payload(payload)
    return {"valid": True, "public_spec": payload, "effective_spec": json_value(effective),
            "supplied_fields": sorted(payload), "defaulted_fields": sorted(set(asdict(effective)) - set(payload)),
            "evidence": evidence("Input validation only; no design feasibility calculation")}


def llc_create_design(spec: dict[str, Any]) -> dict:
    payload = LLCSpecInput.model_validate(spec).model_dump(exclude_unset=True)
    return DesignStore().create(payload)


def llc_submit_analysis(design_id: str, timeout_seconds: int = 300) -> dict:
    from .jobs import manager
    return manager().submit("analysis", design_id, {}, timeout_seconds)


def llc_optimize(design_id: str, request: dict, timeout_seconds: int = 600) -> dict:
    from webapp.service import spec_from_payload

    from .jobs import manager
    from .workflow import optimization_config
    checked = OptimizationRequest.model_validate(request)
    snapshot = DesignStore().load(design_id)
    optimization_config(spec_from_payload(snapshot["public_spec"]), checked)
    return manager().submit("optimization", design_id, checked.model_dump(), timeout_seconds)


def llc_check_corners(design_id: str, request: dict, timeout_seconds: int = 300) -> dict:
    from webapp.service import spec_from_payload

    from .jobs import manager
    from .workflow import explicit_corners
    checked = CornerRequest.model_validate(request)
    snapshot = DesignStore().load(design_id)
    explicit_corners(spec_from_payload(snapshot["public_spec"]), checked)
    return manager().submit("corners", design_id, checked.model_dump(), timeout_seconds)


def job_get(job_id: str, include_result: bool = False) -> dict:
    from .jobs import manager
    return manager().get(job_id, include_result)


def job_cancel(job_id: str) -> dict:
    from .jobs import manager
    return manager().cancel(job_id)


def design_export(job_id: str, format: str = "json") -> dict:
    from .jobs import manager
    return manager().export(job_id, format)


def control_auto_design(request: dict) -> dict:
    from .workflow import design_control
    runtime = checked_runtime()
    result = design_control(ControlRequest.model_validate(request))
    checked_runtime()
    result["runtime"] = runtime
    return result


def control_export_verified_c99(request: dict) -> dict:
    from .c99 import export_verified
    checked = CoefficientRequest.model_validate(request)
    return export_verified(checked, DesignStore())

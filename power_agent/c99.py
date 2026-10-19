"""MCP adapter to the shared, hash-bound host C99 acceptance path."""
from __future__ import annotations

from power_control_tools import codegen
from power_control_tools.models import DigitalTransferFunction

from .contracts import CoefficientRequest
from .storage import DesignStore, checked_runtime, digest, evidence


def export_verified(request: CoefficientRequest, store: DesignStore) -> dict:
    controller = DigitalTransferFunction(tuple(request.b), tuple(request.a), request.sample_rate_hz).normalized()
    if not controller.implementable:
        raise ValueError("Controller has unstable poles and cannot be exported")
    runtime = checked_runtime()
    identity = digest({"coefficients": request.model_dump(), "runtime": runtime})
    path = store.path("exports", identity, ".h")
    def verify_bound(digital, exported):
        result = codegen.verify_c99_filter(digital, exported)
        checked_runtime()
        return result

    exported, _verification = codegen.export_verified_c99_filter(
        controller, path, prefix="POWER_AGENT_CTRL", verifier=verify_bound)
    report = codegen.read_c99_export_verification(exported)
    return {"artifact_id": identity, "path": str(exported.file_path),
            "verification": report, "runtime": runtime,
            "evidence": evidence("Host float32 impulse/step only; not closed-loop, target, saturation or hardware qualification")}

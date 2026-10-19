"""Local MCP interface to deterministic design kernels and managed jobs."""
from __future__ import annotations

import json
from typing import Annotated, Any, Literal

from mcp.server import MCPServer
from mcp.types import ToolAnnotations
from pydantic import Field

from . import tools
from .contracts import (
    CoefficientRequest,
    ControlRequest,
    CornerRequest,
    LLCSpecInput,
    OptimizationRequest,
)

mcp = MCPServer("Power Design Toolkit")
READ = ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False)
WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=False)
Identity = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Timeout = Annotated[int, Field(ge=1, le=600, strict=True)]


@mcp.tool(annotations=READ)
def toolkit_capabilities() -> dict[str, Any]:
    """Discover implemented tools, workflow, job budgets and evidence boundaries."""
    return tools.toolkit_capabilities()


@mcp.tool(annotations=READ)
def llc_defaults() -> dict[str, Any]:
    """Read LLC defaults, SI units, allowed fields and numeric limits before designing."""
    return tools.llc_defaults()


@mcp.tool(annotations=READ)
def llc_analyze(spec: LLCSpecInput) -> dict[str, Any]:
    """Run one bounded LLC model synchronously; use jobs for optimization/corner sweeps."""
    return tools.llc_analyze(spec.model_dump(exclude_unset=True))


@mcp.tool(annotations=READ)
def validate_engineering_data() -> dict[str, Any]:
    """Check bundled data provenance; structural validity does not establish hardware readiness."""
    return tools.validate_engineering_data()


@mcp.tool(annotations=READ)
def design_validate_spec(spec: LLCSpecInput) -> dict:
    """Validate LLC input and expose every defaulted field; no feasibility claim or file writes."""
    return tools.design_validate_spec(spec.model_dump(exclude_unset=True))


@mcp.tool(annotations=WRITE)
def llc_create_design(spec: LLCSpecInput) -> dict:
    """Save immutable LLC inputs with source/data/runtime identity; inspect defaults first."""
    return tools.llc_create_design(spec.model_dump(exclude_unset=True))


@mcp.tool(annotations=WRITE)
def llc_submit_analysis(design_id: Identity, timeout_seconds: Timeout = 300) -> dict:
    """Start one snapshot-bound LLC analysis. Poll job_get; completion is separate from design PASS."""
    return tools.llc_submit_analysis(design_id, timeout_seconds)


@mcp.tool(annotations=WRITE)
def llc_optimize(design_id: Identity, request: OptimizationRequest, timeout_seconds: Timeout = 600) -> dict:
    """Start bounded explicit Ln/Q/fr optimization. Other fields stay fixed; TD defaults on."""
    return tools.llc_optimize(design_id, request.model_dump(), timeout_seconds)


@mcp.tool(annotations=WRITE)
def llc_check_corners(design_id: Identity, request: CornerRequest, timeout_seconds: Timeout = 300) -> dict:
    """Check only the requested Cartesian product using FHA gain/ZVS constraints, not thermal/loss proof."""
    return tools.llc_check_corners(design_id, request.model_dump(), timeout_seconds)


@mcp.tool(annotations=READ)
def job_get(job_id: Identity, include_result: bool = False) -> dict:
    """Poll a managed job; request the full result only after completion. Failures remain explicit."""
    return tools.job_get(job_id, include_result)


@mcp.tool(annotations=WRITE)
def job_cancel(job_id: Identity) -> dict:
    """Terminate a calculation owned by this server session; poll for terminal cancellation state."""
    return tools.job_cancel(job_id)


@mcp.tool(annotations=WRITE)
def design_export(job_id: Identity, format: Literal["json", "html"] = "json") -> dict:
    """Export completed evidence and original inputs inside the managed workspace, including non-PASS results."""
    return tools.design_export(job_id, format)


@mcp.tool(annotations=READ)
def control_auto_design(request: ControlRequest) -> dict:
    """Tune against supplied equivalent-plant FRA points. A complete-loop response must be de-embedded first."""
    return tools.control_auto_design(request.model_dump())


@mcp.tool(annotations=WRITE)
def control_export_verified_c99(request: CoefficientRequest) -> dict:
    """Export bounded DF2T/SOS C99 and run host float32 verification; failed/unavailable verification blocks the header."""
    return tools.control_export_verified_c99(request.model_dump())


@mcp.resource("power-design://capabilities")
def capabilities_resource() -> str:
    return json.dumps(tools.toolkit_capabilities(), ensure_ascii=False, indent=2)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()

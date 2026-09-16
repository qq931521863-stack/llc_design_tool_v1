"""Exact-H(z) TTPL code-generation wrapper.

The historical TTPL generator intentionally preserves kind-specific nonlinear
firmware semantics (PI/PIF anti-windup and 2P2Z clamped output history).  This
wrapper adds a fail-closed exact linear-controller contract alongside those
sources so downstream simulation, review and future ngspice co-simulation can
consume the analyzed coefficients without re-discretizing controller settings.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from pfc_design.control.analysis import PFCControlLabAnalysis
from pfc_design.control.handoff import (
    PFCControlHandoff,
    assert_handoff_matches_analysis,
    build_pfc_control_handoff,
)

from .generator import CodegenResult, generate_ttpl_control_code


def _cf(value: float) -> str:
    f32 = np.float32(value)
    text = f"{float(f32):.9g}"
    if "e" not in text.lower() and "." not in text:
        text += ".0"
    return text + "f"


def export_pfc_exact_hz_manifest(
    analysis: PFCControlLabAnalysis,
    path: str | Path,
) -> Path:
    """Export the analyzed exact current/voltage H(z) contract as JSON."""
    handoff = build_pfc_control_handoff(analysis)
    assert_handoff_matches_analysis(analysis, handoff)
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(handoff.as_dict(), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return target


def _coefficient_header(handoff: PFCControlHandoff) -> str:
    """Emit auditable float32 coefficient arrays using the project sign convention."""
    lines = [
        "#ifndef TTPL_EXACT_HZ_COEFFICIENTS_H",
        "#define TTPL_EXACT_HZ_COEFFICIENTS_H",
        "",
        "/* Exact analyzed linear-controller contract, quantized to float32. */",
        "/* H(z)=(b0+b1*z^-1+...)/(1+a1*z^-1+...) */",
        "/* y[n]=sum(b*x)-sum(a[1:]*y_history) */",
        "/* Saturation/anti-windup semantics are NOT encoded by H(z); see manifest. */",
        "",
    ]
    for name, artifact in (("CURRENT", handoff.current), ("VOLTAGE", handoff.voltage)):
        lines += [
            f"#define TTPL_{name}_HZ_NB ({len(artifact.b)}u)",
            f"#define TTPL_{name}_HZ_NA ({len(artifact.a)}u)",
            f"#define TTPL_{name}_HZ_FS_HZ ({_cf(artifact.sample_rate_hz)})",
            f"static const float TTPL_{name}_HZ_B[TTPL_{name}_HZ_NB] = "
            + "{" + ", ".join(_cf(v) for v in artifact.b) + "};",
            f"static const float TTPL_{name}_HZ_A[TTPL_{name}_HZ_NA] = "
            + "{" + ", ".join(_cf(v) for v in artifact.a) + "};",
            "",
        ]
    lines += ["#endif /* TTPL_EXACT_HZ_COEFFICIENTS_H */", ""]
    return "\n".join(lines)


def _audit_text(handoff: PFCControlHandoff) -> str:
    lines = [
        "TTPL EXACT H(z) CONTRACT",
        "",
        handoff.current.coefficient_convention,
        "",
        "CURRENT LOOP",
        f"  kind: {handoff.current.kind}",
        f"  Fs: {handoff.current.sample_rate_hz:.12g} Hz",
        f"  b: {handoff.current.b}",
        f"  a: {handoff.current.a}",
        f"  equation: {handoff.current.difference_equation}",
        f"  limits: [{handoff.current.output_min:.12g}, {handoff.current.output_max:.12g}]",
        f"  saturation: {handoff.current.saturation_semantics}",
        f"  state: {handoff.current.state_semantics}",
        "",
        "VOLTAGE LOOP",
        f"  kind: {handoff.voltage.kind}",
        f"  Fs: {handoff.voltage.sample_rate_hz:.12g} Hz",
        f"  b: {handoff.voltage.b}",
        f"  a: {handoff.voltage.a}",
        f"  equation: {handoff.voltage.difference_equation}",
        f"  limits: [{handoff.voltage.output_min:.12g}, {handoff.voltage.output_max:.12g}]",
        f"  saturation: {handoff.voltage.saturation_semantics}",
        f"  state: {handoff.voltage.state_semantics}",
        "",
        "IMPLEMENTATION BOUNDARY",
        "  The JSON/header coefficients are the analyzed exact LINEAR H(z) source of truth.",
        "  The generated TTPL C runtime retains explicit kind-specific nonlinear saturation/state semantics.",
        "  Do not infer anti-windup behavior from H(z), and do not discretize these coefficients again.",
        "",
    ]
    return "\n".join(lines)


def generate_ttpl_control_code_exact(
    analysis: PFCControlLabAnalysis,
    directory: str | Path,
    *,
    duty_feedforward_enabled: bool = True,
    require_stable: bool = True,
) -> CodegenResult:
    """Generate TTPL C99 plus the exact analyzed H(z) handoff artifacts.

    No controller discretization occurs in this wrapper.  It first freezes the
    H(z) objects already used by Bode analysis, fails if that contract diverges,
    then calls the existing topology generator for nonlinear firmware semantics.
    """
    handoff = build_pfc_control_handoff(analysis)
    assert_handoff_matches_analysis(analysis, handoff)
    result = generate_ttpl_control_code(
        analysis,
        directory,
        duty_feedforward_enabled=duty_feedforward_enabled,
        require_stable=require_stable,
    )
    out = result.directory
    manifest = out / "exact_hz_manifest.json"
    manifest.write_text(
        json.dumps(handoff.as_dict(), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    coefficient_header = out / "ttpl_exact_hz_coefficients.h"
    coefficient_header.write_text(_coefficient_header(handoff), encoding="utf-8")
    audit = out / "EXACT_HZ_CONTRACT.txt"
    audit.write_text(_audit_text(handoff), encoding="utf-8")

    files = dict(result.files)
    files["exact_hz_manifest"] = manifest
    files["exact_hz_header"] = coefficient_header
    files["exact_hz_contract"] = audit
    return CodegenResult(result.directory, files, result.validation)


__all__ = [
    "export_pfc_exact_hz_manifest",
    "generate_ttpl_control_code_exact",
]

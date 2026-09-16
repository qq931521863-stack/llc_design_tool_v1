from __future__ import annotations

from dataclasses import replace
import json

import numpy as np
import pytest

from pfc_design.control import (
    PFCControlLabConfig,
    assert_handoff_matches_analysis,
    build_pfc_control_handoff,
    build_pfc_control_lab_analysis,
)
from power_codegen import (
    export_pfc_exact_hz_manifest,
    generate_ttpl_control_code_exact,
)


def _analysis():
    return build_pfc_control_lab_analysis(PFCControlLabConfig())


def _runtime_response(transfer, frequencies_hz):
    f = np.asarray(frequencies_hz, dtype=float)
    z = np.exp(1j * 2.0 * np.pi * f / transfer.sample_rate_hz)
    num = np.zeros_like(z, dtype=complex)
    den = np.zeros_like(z, dtype=complex)
    for index, coefficient in enumerate(transfer.b):
        num += coefficient * z ** (-index)
    for index, coefficient in enumerate(transfer.a):
        den += coefficient * z ** (-index)
    return num / den


def test_exact_hz_handoff_matches_analyzed_controllers_without_rediscretization():
    analysis = _analysis()
    handoff = build_pfc_control_handoff(analysis)
    assert_handoff_matches_analysis(analysis, handoff)

    assert handoff.current.b == pytest.approx(tuple(analysis.current_loop.controller.numerator))
    assert handoff.current.a == pytest.approx(tuple(analysis.current_loop.controller.denominator))
    assert handoff.voltage.b == pytest.approx(tuple(analysis.voltage_loop.controller.numerator))
    assert handoff.voltage.a == pytest.approx(tuple(analysis.voltage_loop.controller.denominator))
    assert handoff.current.sample_rate_hz == pytest.approx(analysis.config.firmware.current_loop_rate_hz)
    assert handoff.voltage.sample_rate_hz == pytest.approx(analysis.config.firmware.voltage_loop_rate_hz)
    assert "y[n]=sum" in handoff.current.coefficient_convention


def test_power_sim_transfer_is_frequency_identical_to_analyzed_hz():
    analysis = _analysis()
    handoff = build_pfc_control_handoff(analysis)

    for loop, artifact in (
        (analysis.current_loop, handoff.current),
        (analysis.voltage_loop, handoff.voltage),
    ):
        runtime = artifact.runtime_transfer()
        frequencies = np.geomspace(2.0, 0.4 * runtime.sample_rate_hz, 320)
        expected = loop.controller.frequency_response(frequencies)
        actual = _runtime_response(runtime, frequencies)
        assert np.allclose(actual, expected, rtol=1e-11, atol=1e-11)


def test_handoff_exposes_nonlinear_semantics_instead_of_inferring_them_from_hz():
    analysis = _analysis()
    handoff = build_pfc_control_handoff(analysis)
    assert "integrator" in handoff.current.state_semantics.lower()
    assert "saturation" in handoff.current.saturation_semantics.lower()
    assert "anti-windup" in handoff.as_dict()["note"].lower()


def test_handoff_mismatch_gate_fails_closed():
    analysis = _analysis()
    handoff = build_pfc_control_handoff(analysis)
    tampered_current = replace(
        handoff.current,
        b=(handoff.current.b[0] + 1e-4,) + handoff.current.b[1:],
    )
    tampered = replace(handoff, current=tampered_current)
    with pytest.raises(ValueError, match="numerator differs"):
        assert_handoff_matches_analysis(analysis, tampered)


def test_exact_hz_manifest_and_codegen_package_preserve_same_coefficients(tmp_path):
    analysis = _analysis()
    handoff = build_pfc_control_handoff(analysis)

    manifest_path = export_pfc_exact_hz_manifest(analysis, tmp_path / "handoff.json")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["schema"] == "power-design-toolkit-pfc-exact-hz-v1"
    assert manifest["controllers"]["current"]["b"] == pytest.approx(handoff.current.b)
    assert manifest["controllers"]["voltage"]["a"] == pytest.approx(handoff.voltage.a)

    generated = generate_ttpl_control_code_exact(analysis, tmp_path / "generated")
    assert generated.validation.passed
    for key in ("exact_hz_manifest", "exact_hz_header", "exact_hz_contract"):
        assert key in generated.files
        assert generated.files[key].exists()

    generated_manifest = json.loads(
        generated.files["exact_hz_manifest"].read_text(encoding="utf-8")
    )
    assert generated_manifest["controllers"]["current"]["b"] == pytest.approx(handoff.current.b)
    header = generated.files["exact_hz_header"].read_text(encoding="utf-8")
    assert "TTPL_CURRENT_HZ_B" in header
    assert "TTPL_VOLTAGE_HZ_A" in header
    contract = generated.files["exact_hz_contract"].read_text(encoding="utf-8")
    assert "Do not infer anti-windup behavior from H(z)" in contract

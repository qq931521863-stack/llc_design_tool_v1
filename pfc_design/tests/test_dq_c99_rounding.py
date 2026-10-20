"""Delivered float32 recurrence preserves separate operation rounding."""

import platform
from dataclasses import replace

import pytest

from pfc_design.three_phase import delivery
from pfc_design.three_phase.exact_control import ExactController
from pfc_design.three_phase.model import ThreePhaseConfig


@pytest.mark.parametrize("optimized", [False, True])
@pytest.mark.parametrize(
    "coefficients",
    [
        None,
        ((2.0,), (1.0,)),
        ((0.4, 0.2, 0.1, 0.05), (1.0,)),
        ((0.2,), (1.0, -0.7, 0.1)),
    ],
)
def test_float32_export_matches_separately_rounded_reference(
    monkeypatch, optimized, coefficients
):
    config = ThreePhaseConfig()
    if coefficients is not None:
        config = replace(
            config,
            current_controller=ExactController(
                *coefficients, config.control_hz, "rounding regression"
            ),
        )
    if optimized:
        run = delivery.subprocess.run

        def contracting_compile(args, **kwargs):
            if "-std=c99" in args:
                # Inlining the verifier's constant inputs can fold away the
                # recurrence and hide the fused-operation regression.
                args = [*args, "-O2", "-fno-inline", "-ffp-contract=fast"]
                if platform.machine().lower() in ("x86_64", "amd64"):
                    # Use FMA when supported by the executing x86 CPU, without
                    # requiring unsupported instructions on older CI hosts.
                    args.append("-march=native")
            return run(args, **kwargs)

        monkeypatch.setattr(delivery.subprocess, "run", contracting_compile)
    header = delivery.render_c99(config, scalar="float")
    result = delivery.verify_c99(config, header, scalar="float")
    assert result["status"] == "PASS", result
    assert result["maximum_absolute_error"] == 0.0, result
    assert "const volatile float product=" in header
    # Verification must still reject changed arithmetic, not merely compile it.
    broken = header.replace("return product + s->z[0]", "return product - s->z[0]")
    rejected = delivery.verify_c99(config, broken, scalar="float")
    assert rejected["status"] == "FAIL", rejected


def test_double_export_does_not_acquire_float_rounding_intermediates():
    header = delivery.render_c99(ThreePhaseConfig())
    assert "volatile" not in header
    result = delivery.verify_c99(ThreePhaseConfig(), header)
    assert result["status"] == "PASS", result

"""Canonical exact-H(z) handoff for PFC control, codegen and simulation.

The PFC loop analysis historically used
``llc_design.control.digital_loop.DigitalTransferFunction`` while generic
simulation executes ``power_control_tools.models.DigitalTransferFunction``.
Both use the same project convention, but passing controller *configuration*
objects between subsystems risks accidental re-discretization.

This module makes the already-computed exact discrete transfer functions the
linear-controller source of truth.  Nonlinear implementation semantics such as
saturation and anti-windup remain explicit metadata; they are not inferable
from H(z) alone.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any

import numpy as np

from llc_design.control.digital_loop import (
    ControllerKind,
    DigitalTransferFunction as LoopDigitalTransferFunction,
    controller_kind,
)
from power_control_tools.models import DigitalTransferFunction as RuntimeDigitalTransferFunction

from .analysis import PFCControlLabAnalysis


COEFFICIENT_CONVENTION = (
    "H(z)=(b0+b1*z^-1+...)/(1+a1*z^-1+...); "
    "y[n]=sum(b[k]*x[n-k])-sum(a[k]*y[n-k])"
)


@dataclass(frozen=True)
class PFCControllerArtifact:
    """One exact controller transfer plus explicit firmware semantics."""

    loop_name: str
    kind: str
    b: tuple[float, ...]
    a: tuple[float, ...]
    sample_rate_hz: float
    output_min: float
    output_max: float
    coefficient_convention: str
    saturation_semantics: str
    state_semantics: str
    source: str

    def validate(self) -> None:
        if not self.loop_name:
            raise ValueError("PFC controller artifact requires a loop name")
        if self.sample_rate_hz <= 0.0:
            raise ValueError(f"{self.loop_name}: sample rate must be positive")
        if self.output_max <= self.output_min:
            raise ValueError(f"{self.loop_name}: output limits are invalid")
        if not self.b or not self.a:
            raise ValueError(f"{self.loop_name}: H(z) coefficient arrays cannot be empty")
        if abs(self.a[0] - 1.0) > 1e-12:
            raise ValueError(f"{self.loop_name}: H(z) denominator must be normalized to a0=1")
        if not all(math.isfinite(v) for v in (*self.b, *self.a)):
            raise ValueError(f"{self.loop_name}: H(z) contains non-finite coefficients")

    @property
    def sample_time_s(self) -> float:
        return 1.0 / self.sample_rate_hz

    @property
    def difference_equation(self) -> str:
        feedback = [
            f"{-coefficient:+.9g}*y[n-{index}]"
            for index, coefficient in enumerate(self.a[1:], start=1)
            if abs(coefficient) > 1e-18
        ]
        feedforward = [
            f"{coefficient:+.9g}*x[n-{index}]"
            for index, coefficient in enumerate(self.b)
            if abs(coefficient) > 1e-18
        ]
        terms = feedforward + feedback
        if not terms:
            return "y[n] = 0"
        expression = " ".join(terms)
        if expression.startswith("+"):
            expression = expression[1:]
        return f"y[n] = {expression}"

    def runtime_transfer(self) -> RuntimeDigitalTransferFunction:
        """Return the exact transfer accepted by ``power_sim``; no S2Z occurs."""
        self.validate()
        return RuntimeDigitalTransferFunction(
            b=self.b,
            a=self.a,
            sample_rate_hz=self.sample_rate_hz,
            name=f"{self.loop_name} H(z)",
            source=self.source,
        ).normalized()

    def as_dict(self) -> dict[str, Any]:
        self.validate()
        return {
            "loop_name": self.loop_name,
            "kind": self.kind,
            "sample_rate_hz": self.sample_rate_hz,
            "sample_time_s": self.sample_time_s,
            "b": list(self.b),
            "a": list(self.a),
            "output_min": self.output_min,
            "output_max": self.output_max,
            "coefficient_convention": self.coefficient_convention,
            "difference_equation": self.difference_equation,
            "saturation_semantics": self.saturation_semantics,
            "state_semantics": self.state_semantics,
            "source": self.source,
        }


@dataclass(frozen=True)
class PFCControlHandoff:
    """Exact current/voltage controller contract for downstream consumers."""

    topology: str
    current: PFCControllerArtifact
    voltage: PFCControllerArtifact
    amc_rate_hz: float
    duty_min: float
    duty_max: float
    switching_frequency_hz: float
    provenance: str

    def validate(self) -> None:
        self.current.validate()
        self.voltage.validate()
        if self.topology != "single_phase_ttpl_pfc":
            raise ValueError(f"unsupported PFC handoff topology: {self.topology}")
        if self.amc_rate_hz <= 0.0 or self.switching_frequency_hz <= 0.0:
            raise ValueError("PFC handoff rates must be positive")
        if not 0.0 <= self.duty_min < self.duty_max <= 1.0:
            raise ValueError("PFC handoff duty limits are invalid")

    def as_dict(self) -> dict[str, Any]:
        self.validate()
        return {
            "schema": "power-design-toolkit-pfc-exact-hz-v1",
            "topology": self.topology,
            "coefficient_convention": COEFFICIENT_CONVENTION,
            "controllers": {
                "current": self.current.as_dict(),
                "voltage": self.voltage.as_dict(),
            },
            "rates": {
                "current_hz": self.current.sample_rate_hz,
                "amc_hz": self.amc_rate_hz,
                "voltage_hz": self.voltage.sample_rate_hz,
                "switching_hz": self.switching_frequency_hz,
            },
            "duty_limits": [self.duty_min, self.duty_max],
            "provenance": self.provenance,
            "note": (
                "H(z) is the exact analyzed linear controller. Saturation/anti-windup/state semantics "
                "are explicit implementation metadata and must not be inferred from coefficients alone."
            ),
        }


def _implementation_semantics(kind: ControllerKind) -> tuple[str, str]:
    if kind == ControllerKind.PI:
        return (
            "output clamped to configured limits; conditional integrator freeze when saturation and error push outward",
            "Tustin PI integrator state + previous error; unsaturated linear response equals exported exact H(z)",
        )
    if kind == ControllerKind.PIF:
        return (
            "PI raw output clamped before first-order output LPF; conditional PI integrator freeze on outward saturation",
            "Tustin PI integrator + previous error + filtered output state; unsaturated response equals exported exact H(z)",
        )
    if kind == ControllerKind.TWO_P_TWO_Z:
        return (
            "output clamped to configured limits and clamped output inserted into denominator history",
            "canonical direct-form 2P2Z histories x[n-1:n-2], y[n-1:n-2]",
        )
    raise TypeError(f"unsupported PFC controller kind: {kind}")


def _artifact(
    *,
    loop_name: str,
    transfer: LoopDigitalTransferFunction,
    config,
    expected_rate_hz: float,
) -> PFCControllerArtifact:
    kind = controller_kind(config)
    rate = 1.0 / transfer.sample_time_s
    if not math.isclose(rate, expected_rate_hz, rel_tol=1e-10, abs_tol=1e-8):
        raise ValueError(
            f"{loop_name}: analyzed controller rate {rate:g} Hz does not match firmware rate {expected_rate_hz:g} Hz"
        )
    saturation, state = _implementation_semantics(kind)
    artifact = PFCControllerArtifact(
        loop_name=loop_name,
        kind=kind.value,
        b=tuple(float(value) for value in np.asarray(transfer.numerator, dtype=float)),
        a=tuple(float(value) for value in np.asarray(transfer.denominator, dtype=float)),
        sample_rate_hz=rate,
        output_min=float(config.output_min),
        output_max=float(config.output_max),
        coefficient_convention=COEFFICIENT_CONVENTION,
        saturation_semantics=saturation,
        state_semantics=state,
        source=f"PFCControlLabAnalysis.{loop_name}_loop.controller",
    )
    artifact.validate()
    return artifact


def build_pfc_control_handoff(analysis: PFCControlLabAnalysis) -> PFCControlHandoff:
    """Build the downstream contract directly from analyzed exact H(z) objects."""
    cfg = analysis.config
    handoff = PFCControlHandoff(
        topology="single_phase_ttpl_pfc",
        current=_artifact(
            loop_name="current",
            transfer=analysis.current_loop.controller,
            config=cfg.current_controller,
            expected_rate_hz=cfg.firmware.current_loop_rate_hz,
        ),
        voltage=_artifact(
            loop_name="voltage",
            transfer=analysis.voltage_loop.controller,
            config=cfg.voltage_controller,
            expected_rate_hz=cfg.firmware.voltage_loop_rate_hz,
        ),
        amc_rate_hz=float(cfg.firmware.amc_rate_hz),
        duty_min=float(cfg.power_stage.duty_min),
        duty_max=float(cfg.power_stage.duty_max),
        switching_frequency_hz=float(cfg.power_stage.switching_frequency_hz),
        provenance="built from PFCControlLabAnalysis exact discrete controller objects; no re-discretization",
    )
    handoff.validate()
    return handoff


def assert_handoff_matches_analysis(
    analysis: PFCControlLabAnalysis,
    handoff: PFCControlHandoff | None = None,
    *,
    atol: float = 1e-12,
) -> None:
    """Fail closed if a downstream artifact diverges from analyzed H(z)."""
    artifact = handoff or build_pfc_control_handoff(analysis)
    for loop_name, loop, exported in (
        ("current", analysis.current_loop, artifact.current),
        ("voltage", analysis.voltage_loop, artifact.voltage),
    ):
        num = np.asarray(loop.controller.numerator, dtype=float)
        den = np.asarray(loop.controller.denominator, dtype=float)
        if not np.allclose(num, np.asarray(exported.b), rtol=0.0, atol=atol):
            raise ValueError(f"{loop_name}: handoff numerator differs from analyzed exact H(z)")
        if not np.allclose(den, np.asarray(exported.a), rtol=0.0, atol=atol):
            raise ValueError(f"{loop_name}: handoff denominator differs from analyzed exact H(z)")
        if not math.isclose(loop.controller.sample_time_s, exported.sample_time_s, rel_tol=0.0, abs_tol=atol):
            raise ValueError(f"{loop_name}: handoff sample time differs from analyzed exact H(z)")


__all__ = [
    "COEFFICIENT_CONVENTION",
    "PFCControlHandoff",
    "PFCControllerArtifact",
    "assert_handoff_matches_analysis",
    "build_pfc_control_handoff",
]

"""Bounded agent inputs; LLC limits remain owned by the shared service."""
from __future__ import annotations

import math
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, create_model, model_validator

from llc_design.core.spec import PrimaryTopology, TankParameterMode
from webapp.service import _INTEGER_FIELDS, _NUMERIC_LIMITS, spec_from_payload


class StrictInput(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, strict=True)


class _LLCInput(StrictInput):
    @model_validator(mode="after")
    def validate_kernel_spec(self):
        spec_from_payload(self.model_dump(exclude_unset=True))
        return self


_fields = {
    name: (int if name in _INTEGER_FIELDS else float,
           Field(default_factory=lambda: None, ge=limits[0], le=limits[1], strict=True))
    for name, limits in _NUMERIC_LIMITS.items()
}
_fields.update({
    "primary_topology": (Literal[tuple(item.value for item in PrimaryTopology)], Field(default_factory=lambda: None)),
    "parameter_mode": (Literal[tuple(item.value for item in TankParameterMode)], Field(default_factory=lambda: None)),
    "primary_device": (str, Field(default_factory=lambda: None, min_length=1, max_length=256, strict=True)),
    "sr_device": (str, Field(default_factory=lambda: None, min_length=1, max_length=256, strict=True)),
})
LLCSpecInput = create_model("LLCSpecInput", __base__=_LLCInput, **_fields)

Positive = Annotated[float, Field(gt=0, strict=True)]
Scale = Annotated[float, Field(ge=0.5, le=1.5, strict=True)]
Axis = Annotated[list[Positive], Field(min_length=1, max_length=8)]
Scales = Annotated[list[Scale], Field(min_length=1, max_length=3)]


class OptimizationRequest(StrictInput):
    ln_values: Axis | None = None
    q_values: Axis | None = None
    fr_values_hz: Axis | None = None
    top_n: int = Field(default=1, ge=1, le=3, strict=True)
    verify_time_domain: bool = Field(default=True, strict=True)
    verification_scope: Literal["nominal", "critical"] = "critical"

    @model_validator(mode="after")
    def bounded_grid(self):
        if math.prod(len(axis) if axis is not None else 1 for axis in
                     (self.ln_values, self.q_values, self.fr_values_hz)) > 32:
            raise ValueError("Choose at most 32 explicit LLC candidates")
        return self


class CornerRequest(StrictInput):
    vbus_values_v: Annotated[list[Positive], Field(min_length=1, max_length=8)]
    load_fractions: Annotated[list[Annotated[float, Field(ge=0, le=1, strict=True)]],
                              Field(min_length=1, max_length=8)]
    lr_scales: Scales = [1.0]
    lm_scales: Scales = [1.0]
    cr_scales: Scales = [1.0]

    @model_validator(mode="after")
    def bounded_grid(self):
        if math.prod(len(getattr(self, name)) for name in type(self).model_fields) > 128:
            raise ValueError("Choose at most 128 explicit LLC corners")
        return self


class ControlRequest(StrictInput):
    frequency_hz: Annotated[list[Positive], Field(min_length=4, max_length=2048)]
    plant_real: Annotated[list[float], Field(min_length=4, max_length=2048)]
    plant_imag: Annotated[list[float], Field(min_length=4, max_length=2048)]
    source_description: str = Field(min_length=1, max_length=1000)
    response_kind: Literal["equivalent_plant"] = "equivalent_plant"
    controller_kind: Literal["pi", "pif", "pid", "2p2z", "3p3z"] = "pi"
    sample_rate_hz: float = Field(ge=1, le=10_000_000, strict=True)
    target_crossover_hz: Positive
    target_phase_margin_deg: float = Field(ge=5, le=85, strict=True)
    min_gain_margin_db: float = Field(default=6.0, ge=0, strict=True)
    max_ms: float = Field(default=2.0, gt=1, strict=True)
    allow_lower_crossover: bool = Field(default=False, strict=True)

    @model_validator(mode="after")
    def check_frequency_axis(self):
        if not len(self.frequency_hz) == len(self.plant_real) == len(self.plant_imag):
            raise ValueError("FRA arrays must have equal length")
        if any(b <= a for a, b in zip(self.frequency_hz, self.frequency_hz[1:])):
            raise ValueError("Frequencies must be strictly increasing")
        if self.frequency_hz[-1] >= self.sample_rate_hz / 2:
            raise ValueError("FRA frequencies must be below Nyquist")
        if not self.frequency_hz[0] < self.target_crossover_hz <= min(self.frequency_hz[-1], 0.30*self.sample_rate_hz):
            raise ValueError("Target crossover must lie inside the FRA band and at most 0.30 fs")
        return self


class CoefficientRequest(StrictInput):
    b: Annotated[list[float], Field(min_length=1, max_length=9)]
    a: Annotated[list[float], Field(min_length=1, max_length=9)]
    sample_rate_hz: Positive

    @model_validator(mode="after")
    def causal(self):
        if abs(self.a[0]) < 1e-30 or len(self.b) > len(self.a):
            raise ValueError("Require nonzero a0 and a causal order <= 8")
        return self

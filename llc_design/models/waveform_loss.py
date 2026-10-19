"""Waveform-aware / provenance-tagged LLC semiconductor loss (V2).

Reuses FHA operating-point scalars only when no TD waveform is requested.
Requested TD data must be converged and complete. Current/frequency-dependent
semiconductor terms consume samples while switching/commutation physics remain
engineering approximations, never hardware verification.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
import math
from typing import Literal

import numpy as np

from ..core.operating_point import LLCOperatingPoint
from ..core.spec import LLCDesignSpec, MosfetSpec
from ..core.tank import TankDesign
from ..core.zvs_margin import evaluate_zvs_margin
from ..dynamics.periodic_samples import periodic_samples
from ..dynamics.waveforms import WaveformBundle
from .physics_evidence import (
    DataConfidence,
    LossTerm,
    ModelGrade,
    SwitchingClass,
    classify_turn_on_from_zvs_margin,
    physical_sanity_issues,
)
from .primary_bridge import PrimaryBridgeLoss, primary_bridge_loss
from .synchronous_rectifier import SynchronousRectifierLoss, synchronous_rectifier_loss


RectifierKind = Literal["DIODE", "SYNCHRONOUS_RECTIFIER"]


@dataclass(frozen=True)
class ProvenancedBridgeLoss:
    legacy: PrimaryBridgeLoss
    terms: tuple[LossTerm, ...]
    turn_on_class: SwitchingClass
    zvs_margin: float | None
    waveform_source: str
    sanity: tuple[str, ...]

    @property
    def total_w(self) -> float:
        return float(sum(t.value_w for t in self.terms))


@dataclass(frozen=True)
class ProvenancedRectifierLoss:
    kind: RectifierKind
    legacy: SynchronousRectifierLoss | None
    terms: tuple[LossTerm, ...]
    waveform_source: str
    sanity: tuple[str, ...]

    @property
    def total_w(self) -> float:
        return float(sum(t.value_w for t in self.terms if t.model != ModelGrade.NOT_APPLICABLE))


def _waveform_current(bundle: WaveformBundle, key: str, td_converged: bool | None) -> tuple[float, float]:
    """Measure actual samples; refuse missing/unconverged requested TD data."""
    samples = periodic_samples(bundle, {key: "A"}, td_converged)
    values = samples.signals[key]
    return float(np.sqrt(np.mean(values**2))), float(np.max(np.abs(values)))


def bridge_loss_with_provenance(
    spec: LLCDesignSpec,
    tank: TankDesign,
    op: LLCOperatingPoint,
    device: MosfetSpec,
    *,
    waveform: WaveformBundle | None = None,
    td_converged: bool | None = None,
) -> ProvenancedBridgeLoss:
    """Build MOSFET loss terms; waveform path upgrades conduction RMS when available."""

    zvs = evaluate_zvs_margin(spec, tank, op, device=device, level=2)
    turn_on = classify_turn_on_from_zvs_margin(
        None if math.isnan(zvs.zvs_margin) else zvs.zvs_margin
    )
    source = "FHA_OPERATING_POINT"
    if waveform is None and td_converged is not None:
        raise ValueError("time-domain loss requested without waveform data")
    loss_op = op
    if waveform is not None:
        ir_rms, ir_peak = _waveform_current(waveform, "i_resonant", td_converged)
        loss_op = replace(op, resonant_current_rms_a=ir_rms,
                          resonant_current_peak_a=ir_peak,
                          switching_frequency_hz=waveform.switching_frequency_hz)
        source = "TIME_DOMAIN_WAVEFORM"
    legacy = primary_bridge_loss(spec, tank, loss_op, device)
    # A converged ideal waveform is software evidence, not validation of Rds,
    # commutation timing, nonlinear Coss or the actual semiconductor.
    conduction_grade = ModelGrade.APPROXIMATION
    tj = spec.primary_junction_temperature_c
    p_cond = legacy.conduction_w
    # The existing residual-Coss term already estimates incomplete-ZVS turn-on.
    # Count that energy once; adding a second Eoss-like turn_on double counts it.
    turn_on_w = legacy.residual_coss_w
    ton_grade = ModelGrade.APPROXIMATION
    ton_note = "residual-Coss turn-on proxy counted once; commutation current remains FHA"

    qoss_source = (
        DataConfidence.DATASHEET if device.qoss_c > 0.0 else DataConfidence.APPROXIMATION
    )
    terms = (
        LossTerm("conduction", p_cond, conduction_grade, DataConfidence.DATASHEET, tj,
                 f"Rds_on({tj:.0f}°C); Ir_rms from {source}"),
        LossTerm("turn_off", legacy.turnoff_w, ModelGrade.APPROXIMATION, DataConfidence.DATASHEET, tj,
                 "Eoff ref scaled by V·I"),
        LossTerm("turn_on", turn_on_w, ton_grade, qoss_source, tj, ton_note),
        LossTerm("gate", legacy.gate_drive_w, ModelGrade.APPROXIMATION, DataConfidence.DATASHEET, tj),
        LossTerm("coss_residual", 0.0, ModelGrade.NOT_APPLICABLE, qoss_source, tj,
                 "included in turn_on; no second Eoss contribution"),
        LossTerm("deadtime_diode", legacy.deadtime_diode_w, ModelGrade.APPROXIMATION,
                 DataConfidence.DEFAULT, tj, "body-diode Vf default path — not GaN/SiC specific"),
    )
    total = sum(t.value_w for t in terms)
    sanity = physical_sanity_issues(
        loss_w=total,
        q_available=zvs.q_available,
        tj_c=tj,
        ta_c=spec.ambient_temperature_c if hasattr(spec, "ambient_temperature_c") else 40.0,
        dissipating=total > 0.0,
    )
    return ProvenancedBridgeLoss(
        legacy=legacy,
        terms=terms,
        turn_on_class=turn_on,
        zvs_margin=None if math.isnan(zvs.zvs_margin) else zvs.zvs_margin,
        waveform_source=source,
        sanity=sanity,
    )


def rectifier_loss_with_provenance(
    spec: LLCDesignSpec,
    op: LLCOperatingPoint,
    device: MosfetSpec,
    *,
    kind: RectifierKind = "SYNCHRONOUS_RECTIFIER",
    waveform: WaveformBundle | None = None,
    td_converged: bool | None = None,
) -> ProvenancedRectifierLoss:
    tj = spec.sr_junction_temperature_c
    source = "FHA_OPERATING_POINT"
    if waveform is None and td_converged is not None:
        raise ValueError("time-domain loss requested without waveform data")
    loss_op = op
    if waveform is not None:
        is_rms, is_peak = _waveform_current(waveform, "i_transformer_secondary", td_converged)
        loss_op = replace(op, secondary_current_rms_a=is_rms,
                          secondary_current_peak_a=is_peak,
                          switching_frequency_hz=waveform.switching_frequency_hz)
        source = "TIME_DOMAIN_WAVEFORM"

    if kind == "DIODE":
        if waveform is not None:
            raise ValueError("time-domain diode loss is not implemented; select SR or FHA")
        # Diode path: conduction ≈ 2·Vf·Iavg (full bridge), RR unknown without datasheet.
        i_avg = 2.0 * op.pout_w / max(spec.vout_v, 1e-9) / math.pi
        vf = device.body_diode_vf_v
        p_cond = 2.0 * vf * i_avg
        terms = (
            LossTerm("conduction", p_cond, ModelGrade.APPROXIMATION, DataConfidence.DEFAULT, tj,
                     "full-bridge diode average current approx"),
            LossTerm("switching", 0.0, ModelGrade.NOT_APPLICABLE, DataConfidence.UNKNOWN, tj),
            LossTerm("reverse_recovery", 0.0, ModelGrade.UNKNOWN, DataConfidence.UNKNOWN, tj,
                     "Qrr not supplied — UNKNOWN, not zero"),
            LossTerm("drive", 0.0, ModelGrade.NOT_APPLICABLE, DataConfidence.UNKNOWN, tj),
        )
        return ProvenancedRectifierLoss(
            kind=kind, legacy=None, terms=terms, waveform_source=source,
            sanity=physical_sanity_issues(loss_w=p_cond),
        )

    legacy = synchronous_rectifier_loss(spec, loss_op, device)
    grade = ModelGrade.APPROXIMATION
    terms = (
        LossTerm("conduction", legacy.conduction_w, grade, DataConfidence.DATASHEET, tj,
                 f"secondary RMS from {source}; Rds and SR timing remain approximate"),
        LossTerm("switching", legacy.turnoff_w, ModelGrade.APPROXIMATION, DataConfidence.DATASHEET, tj,
                 "SR turn-off advance model"),
        LossTerm("reverse_recovery", 0.0, ModelGrade.NOT_APPLICABLE, DataConfidence.UNKNOWN, tj,
                 "SR channel — RR N/A"),
        LossTerm("drive", legacy.gate_drive_w, ModelGrade.APPROXIMATION, DataConfidence.DATASHEET, tj),
        LossTerm("coss", legacy.coss_w, ModelGrade.APPROXIMATION, DataConfidence.APPROXIMATION, tj),
        LossTerm("deadtime_diode", legacy.deadtime_diode_w, ModelGrade.APPROXIMATION, DataConfidence.DEFAULT, tj),
    )
    return ProvenancedRectifierLoss(
        kind=kind,
        legacy=legacy,
        terms=terms,
        waveform_source=source,
        sanity=physical_sanity_issues(loss_w=legacy.total_w),
    )


__all__ = [
    "ProvenancedBridgeLoss",
    "ProvenancedRectifierLoss",
    "bridge_loss_with_provenance",
    "rectifier_loss_with_provenance",
]

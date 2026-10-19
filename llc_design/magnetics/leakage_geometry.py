"""1-D winding-field energy screen, including conductor and insulation regions.

The balanced excitation Ip=1 A, Is=-Np/Ns gives Lsigma,p+n²Lsigma,s,
NOT the APR short-circuit Lr. Its comparison with a characterized matrix is
an independent geometric consistency screen, not a measurement substitute.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from ..core.integrated_magnetics import from_spec
from .litz import distribute_turns

MU0 = 4*math.pi*1e-7


@dataclass(frozen=True)
class LeakageEnergyRegion:
    kind: str
    thickness_m: float
    mean_turn_length_m: float
    mmf_start_a_turn: float
    mmf_end_a_turn: float
    inductance_h: float


@dataclass(frozen=True)
class IntegratedLeakageScreen:
    balanced_geometry_h: float
    characterized_balanced_h: float
    relative_error: float
    configured_interwinding_gap_m: float
    required_interwinding_gap_m: float
    minimum_insulation_m: float
    radial_build_m: float
    required_radial_build_m: float
    available_window_height_m: float
    geometry_apr_lr_lower_h: float
    geometry_apr_lr_upper_h: float
    reachable: bool
    status: str
    regions: tuple[LeakageEnergyRegion, ...]
    notes: tuple[str, ...]


def screen_integrated_leakage(spec, design) -> IntegratedLeakageScreen:
    magnetic = from_spec(spec)
    if spec.transformer_winding_layout != "P/2-S-P/2":
        raise ValueError("integrated leakage geometry requires P/2-S-P/2")
    width = design.core.window_width_m
    if width <= 0:
        raise ValueError("winding-field energy requires a positive usable winding width")
    n = spec.turns_ratio
    rows = []
    for label, turns, per_layer, wire, mlt, current in (
            ("primary", design.primary_turns//2, design.primary_turns_per_layer,
             design.primary_wire, design.core.mlt_primary_mm*1e-3, 1.0),
            ("secondary", design.secondary_turns, design.secondary_turns_per_layer,
             design.secondary_wire, design.core.mlt_secondary_mm*1e-3, -n),
            ("primary", design.primary_turns-design.primary_turns//2, design.primary_turns_per_layer,
             design.primary_wire, design.core.mlt_primary_mm*1e-3, 1.0)):
        for layer_turns in distribute_turns(turns, per_layer):
            if layer_turns*wire.equivalent_outer_diameter_m > width*(1+1e-9):
                raise ValueError("integrated winding layer exceeds usable winding width")
            rows.append((label, layer_turns*current, wire.equivalent_outer_diameter_m, mlt))
    regions = []
    mmf = 0.0
    gap_slope = 0.0
    interfaces = 0
    for index, (kind, increment, thickness, mlt) in enumerate(rows):
        if index:
            other = rows[index-1]
            interface = other[0] != kind
            spacing = (spec.integrated_interwinding_gap_m if interface
                       else spec.integrated_interlayer_gap_m)
            spacer_mlt = (mlt+other[3])/2
            coefficient = MU0*spacer_mlt*mmf**2/width
            regions.append(LeakageEnergyRegion("P-S insulation" if interface else "interlayer insulation",
                            spacing, spacer_mlt, mmf, mmf, coefficient*spacing))
            if interface:
                gap_slope += coefficient
                interfaces += 1
        final = mmf+increment
        inductance = MU0*mlt*thickness/width*(mmf*mmf+mmf*final+final*final)/3
        regions.append(LeakageEnergyRegion(kind, thickness, mlt, mmf, final, inductance))
        mmf = final
    if abs(mmf) > 1e-9*max(design.primary_turns, 1):
        raise ValueError("balanced winding-field MMF does not close")
    estimate = sum(region.inductance_h for region in regions)
    build = sum(region.thickness_m for region in regions)
    target = magnetic.lp_h+n*n*magnetic.ls_h-2*n*magnetic.mutual_h
    fixed = estimate-gap_slope*spec.integrated_interwinding_gap_m
    if gap_slope <= 0:
        raise ValueError("integrated winding geometry has no adjustable P-S interface")
    required = (target-fixed)/gap_slope
    required_build = build+interfaces*(required-spec.integrated_interwinding_gap_m)
    available = design.core.window_height_mm*1e-3
    minimum = spec.integrated_minimum_insulation_m
    reachable = required >= minimum and required_build <= available
    mu = magnetic.physical_magnetizing_h
    return IntegratedLeakageScreen(estimate, target, abs(estimate/target-1),
        spec.integrated_interwinding_gap_m, required, minimum, build, required_build,
        available, mu*estimate/(mu+estimate), estimate, reachable,
        "MODEL_REACHABLE_REQUIRES_CHARACTERIZATION" if reachable else "MODEL_UNREACHABLE",
        tuple(regions), (
            "Balanced field energy is Lsigma,p+n_phys²*Lsigma,s, not APR Lshort.",
            "P/2-S-P/2 one-dimensional field; finite edges, gap fringing, shield and bobbin details omitted.",
            "Conductor thickness is round-bundle envelope; winding width is the supplied usable core window width.",
            "Required spacing targets balanced field energy only; actual Lp/Ls/M and insulation require verification.",
        ))

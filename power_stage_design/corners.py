"""Explicit operating-point/tolerance sweeps with a separate worst point per metric."""
from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Callable
from dataclasses import asdict, dataclass, fields, replace
from itertools import product
from typing import TYPE_CHECKING

from .models import DesignInputs
from .topologies import calculate_stage

if TYPE_CHECKING:
    from .loop import Compensator, LoopPlant


@dataclass(frozen=True)
class CornerLoopIdentity:
    """Complete effective settings, detached from the caller's mutable dicts."""

    plant: LoopPlant
    compensator: Compensator


@dataclass(frozen=True)
class CornerRequest:
    input_voltages_v: tuple[float, ...]
    output_currents_a: tuple[float, ...]
    inductance_factors: tuple[float, ...] = (1.0,)
    capacitance_factors: tuple[float, ...] = (1.0,)
    secondary_inductance_factors: tuple[float, ...] = (1.0,)
    loop_capacitance2_factors: tuple[float, ...] = (1.0,)
    loop_esr_factors: tuple[float, ...] = (1.0,)
    loop_esr2_factors: tuple[float, ...] = (1.0,)
    loop_dcr_factors: tuple[float, ...] = (1.0,)

    def __post_init__(self):
        # JSON lists must not remain mutable aliases of a completed request.
        for field in fields(self):
            values = getattr(self, field.name)
            if isinstance(values, (tuple, list)):
                object.__setattr__(self, field.name, tuple(values))


@dataclass(frozen=True)
class CornerPoint:
    inputs: DesignInputs
    design_id: str | None
    mode: str | None
    metrics: dict[str, float]
    error: str | None = None
    loop_error: str | None = None
    loop_plant: LoopPlant | None = None
    point_id: str | None = None


@dataclass(frozen=True)
class CornerSweep:
    base_design_id: str
    request: CornerRequest
    points: tuple[CornerPoint, ...]
    extrema: dict[str, dict]
    controller: dict | None
    selected_device: dict | None
    junction_temperature_c: float
    evidence_scope: str
    # Legacy loop-bearing sweeps have no provable plant identity and must be rerun.
    loop_identity: CornerLoopIdentity | None = None


def validate_corner_request(request: CornerRequest) -> None:
    groups=tuple(getattr(request,field.name) for field in fields(CornerRequest))
    if any(not isinstance(group,(tuple,list)) for group in groups):
        raise ValueError("Corner values must be explicit lists or tuples")
    count=math.prod(len(group) for group in groups)
    if count<1 or count>500:
        raise ValueError("Choose 1–500 explicit corners; no unscanned area is inferred")
    for group_index,group in enumerate(groups):
        for value in group:
            if type(value) not in (float,int) or not math.isfinite(value) or value<0 or (group_index!=1 and value==0):
                raise ValueError("Corner voltages/factors must be finite positive values; current may be zero")


def validate_corner_dimensions(inputs: DesignInputs, request: CornerRequest, plant: LoopPlant | None) -> None:
    """Reject requested dimensions which the selected model would ignore."""
    from .topologies import _topology_id
    if (any(value != 1 for value in request.secondary_inductance_factors)
            and (_topology_id(inputs.topology) not in {"T06", "T07", "T08", "T19"} or inputs.secondary_inductance_h is None)):
        raise ValueError("L2 tolerance requires a supported second inductor (T06/T07/T08/T19)")
    for field, nominal in (("loop_capacitance2_factors", "capacitance2_f"),
                           ("loop_esr_factors", "esr_ohm"), ("loop_esr2_factors", "esr2_ohm"),
                           ("loop_dcr_factors", "dcr_ohm")):
        if any(value != 1 for value in getattr(request, field)):
            if plant is None:
                raise ValueError(f"{field} requires a current matching analog loop")
            if getattr(plant, nominal) <= 0:
                raise ValueError(f"{field} requires a positive nominal {nominal}; a zero nominal cannot vary")
            if nominal == "esr2_ohm" and plant.capacitance2_f <= 0:
                raise ValueError("ESR2 tolerance requires an enabled positive C2 branch")


def _point_id(inputs, plant, compensator, selected_device, temperature):
    # A stage design_id alone cannot distinguish loop-only tolerance points.
    snapshot = {"stage": inputs.design_id, "plant": asdict(plant) if plant else None,
                "compensator": asdict(compensator) if compensator else None,
                "selected_device": asdict(selected_device) if selected_device else None,
                "junction_temperature_c": temperature}
    return hashlib.sha256(json.dumps(snapshot, sort_keys=True, allow_nan=False).encode()).hexdigest()[:20]


def analyze_corners(inputs: DesignInputs, request: CornerRequest, *, loop_settings: dict | None = None,
                    selected_device=None, junction_temperature_c: float = 100.0,
                    cancelled: Callable[[],bool] | None = None) -> CornerSweep:
    """Evaluate the exact same controller at explicitly enumerated corners.

    Optional semiconductor conduction is an identified partial loss term only;
    unknown switching, magnetics and thermal terms do not become zero totals.
    """
    validate_corner_request(request)
    groups=tuple(getattr(request,field.name) for field in fields(CornerRequest))
    count=math.prod(len(group) for group in groups)
    if not math.isfinite(junction_temperature_c):raise ValueError("Temperature must be finite")
    base=calculate_stage(inputs,cancelled=cancelled)
    plant=comp=None
    if loop_settings is not None:
        from .loop import Compensator, LoopPlant
        from .project import _check_loop_identity
        plant=LoopPlant(**loop_settings["plant"]);comp=Compensator(**loop_settings["compensator"])
        _check_loop_identity(inputs,plant)
    validate_corner_dimensions(inputs,request,plant)
    points=[]
    for vin,current,lf,cf,l2f,c2f,e1f,e2f,df in product(*groups):
        if cancelled and cancelled():raise InterruptedError("Corner scan cancelled")
        candidate=replace(inputs,vin_v=vin,iout_a=current,inductance_h=inputs.inductance_h*lf,capacitance_f=inputs.capacitance_f*cf,
                          secondary_inductance_h=inputs.secondary_inductance_h*l2f if inputs.secondary_inductance_h is not None else None)
        local=replace(plant,vin_v=vin,iout_a=current,inductance_h=plant.inductance_h*lf,capacitance_f=plant.capacitance_f*cf,
                      capacitance2_f=plant.capacitance2_f*c2f,esr_ohm=plant.esr_ohm*e1f,
                      esr2_ohm=plant.esr2_ohm*e2f,dcr_ohm=plant.dcr_ohm*df) if plant is not None else None
        identity=_point_id(candidate,local,comp,selected_device,junction_temperature_c)
        try:
            stage=calculate_stage(candidate,cancelled=cancelled)
        except ValueError as exc:
            points.append(CornerPoint(candidate,None,None,{},str(exc),loop_plant=local,point_id=identity));continue
        metrics={"duty":stage.duty,"output_ripple_pp_v":stage.metrics["output_ripple_pp_v"]}
        for device,stress in stage.stress.items():
            for key in ("current_rms_a","current_max_a","current_min_a","voltage_max_v","voltage_min_v"):
                metrics[f"{device}.{key}"]=stress[key]
        if selected_device is not None and "Q1" in stage.stress:
            from .devices import assess_component
            loss=assess_component(stage,"Q1",selected_main_device=selected_device,temperature_c=junction_temperature_c)["known_conduction_loss_w"]
            if loss is not None:metrics["Q1.known_conduction_loss_w"]=loss
        loop_error=None
        if plant is not None:
            from .loop import analyze_loop
            try:
                if stage.mode!="CCM":raise ValueError("Loop requires strict CCM")
                loop=analyze_loop(local,comp)
                for name,value in (("loop.phase_margin_deg",loop.stability.worst_phase_margin_deg),
                                   ("loop.gain_margin_db",loop.stability.worst_gain_margin_db),
                                   ("loop.crossover_hz",loop.stability.main_crossover_hz),
                                   ("loop.ms",loop.stability.ms)):
                    if value is not None and math.isfinite(value):metrics[name]=value
                if loop.stability.status in {"FAIL_SUBHARMONIC","FAIL"}:loop_error=loop.stability.status
            except ValueError as exc:loop_error=str(exc)
        points.append(CornerPoint(candidate,stage.design_id,stage.mode,metrics,loop_error=loop_error,loop_plant=local,point_id=identity))
    extrema={}
    for key in sorted({key for point in points for key in point.metrics}):
        available=[(i,p.metrics[key]) for i,p in enumerate(points) if key in p.metrics]
        low=min(available,key=lambda pair:pair[1]);high=max(available,key=lambda pair:pair[1])
        extrema[key]={"minimum":{"value":low[1],"point_index":low[0],"design_id":points[low[0]].design_id,"point_id":points[low[0]].point_id},
                      "maximum":{"value":high[1],"point_index":high[0],"design_id":points[high[0]].design_id,"point_id":points[high[0]].point_id},
                      "evaluated_points":len(available),"requested_points":count}
    return CornerSweep(base.design_id,request,tuple(points),extrema,asdict(comp) if comp else None,asdict(selected_device) if selected_device else None,junction_temperature_c,
        "Only the explicit listed voltage/load/L/C1/L2 and loop-only C2/ESR1/ESR2/DCR corners are evaluated. Factors multiply their own effective nominal values; unlisted parameters remain fixed. Loop-only parameters do not alter ideal stage waveforms/ripple or semiconductor conduction loss. The controller is unchanged. Conduction loss, when present, is partial; total loss, unscanned corners and hardware margins remain unknown.",
        loop_identity=CornerLoopIdentity(plant,comp) if plant is not None else None)

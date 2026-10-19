"""Agent orchestration over existing LLC and FRA kernels."""
from __future__ import annotations

import itertools
import math
from dataclasses import asdict

import numpy as np

from llc_design.core.engineering import (
    EnvelopeCorner,
    OperatingEnvelope,
    evaluate_operating_envelope,
)
from llc_design.core.spec import TankParameterMode
from llc_design.core.tank import design_tank
from llc_design.core.zvs_margin import evaluate_zvs_margin
from llc_design.models.devices import DeviceDatabase
from llc_design.optimization.sweep import LLCOptimizer, OptimizationConfig
from llc_design.optimization.two_stage import TwoStageLLCOptimizer, candidate_provenance
from power_control_tools.fra.auto_design import auto_design_controller
from webapp.service import analyze_llc, spec_from_payload

from .contracts import ControlRequest, CornerRequest, OptimizationRequest
from .storage import DesignStore, digest, evidence, json_value


def optimization_config(spec, request: OptimizationRequest) -> OptimizationConfig:
    if spec.parameter_mode != TankParameterMode.AUTO_DESIGN:
        raise ValueError("Optimization requires AUTO_DESIGN; user-defined Lr/Cr/Lm stay fixed")
    cfg = OptimizationConfig(
        tuple(request.ln_values or [spec.ln_ratio]),
        tuple(request.q_values or [spec.q_full_load]),
        tuple(request.fr_values_hz or [spec.resonant_frequency_hz]),
        (spec.primary_turns,), (spec.secondary_turns,), (spec.primary_device,),
        (spec.sr_parallel_devices_per_position,))
    cfg.validate()
    for ln, q, fr in itertools.product(cfg.ln_values, cfg.q_values, cfg.fr_values_hz):
        # Every candidate crosses the same public-input gate as single analysis.
        from .contracts import LLCSpecInput
        values = json_value(spec)
        public = {name: values[name] for name in LLCSpecInput.model_fields if values[name] is not None}
        LLCSpecInput.model_validate({**public, "ln_ratio": ln, "q_full_load": q, "resonant_frequency_hz": fr})
        spec.clone(ln_ratio=ln, q_full_load=q, resonant_frequency_hz=fr).validate()
    return cfg


def explicit_corners(spec, request: CornerRequest) -> tuple[OperatingEnvelope, list[EnvelopeCorner]]:
    if any(not spec.vbus_hold_end_v <= v <= spec.vbus_max_v for v in request.vbus_values_v):
        raise ValueError("Corner bus voltages must be inside the snapshot hold-up/max range")
    if any(0 < value < spec.minimum_modeled_load_fraction for value in request.load_fractions):
        raise ValueError("Nonzero corner loads below the FHA model minimum are unsupported")
    tank = design_tank(spec)
    if any(tank.lm_h * lm <= tank.lr_h * lr for lm, lr in
           itertools.product(request.lm_scales, request.lr_scales)):
        raise ValueError("Tolerance corners require Lm > Lr; no implicit tank correction")
    env = OperatingEnvelope.from_spec(spec, lr_tolerance=0, lm_tolerance=0, cr_tolerance=0,
                                     turns_ratio_tolerance=0,
                                     load_fractions=tuple(request.load_fractions),
                                     include_zero_load=0 in request.load_fractions)
    # Envelope metadata describes the explicit scanned limits; no interpolation claim.
    env = OperatingEnvelope(**{**asdict(env), "vin_min_v": min(request.vbus_values_v),
        "vin_max_v": max(request.vbus_values_v),
        "pout_min_w": spec.pout_w * max(min(request.load_fractions), spec.minimum_modeled_load_fraction),
        "pout_max_w": spec.pout_w * max(max(request.load_fractions), spec.minimum_modeled_load_fraction)})
    corners = [EnvelopeCorner(f"corner-{index:03d}", bus, spec.vout_v, spec.pout_w * load, load,
                             lr, lm, cr, dead_time_s=spec.primary_deadtime_s,
                             temperature_c=spec.primary_junction_temperature_c)
               for index, (bus, load, lr, lm, cr) in enumerate(itertools.product(
                   request.vbus_values_v, request.load_fractions, request.lr_scales,
                   request.lm_scales, request.cr_scales))]
    return env, corners


def execute(task: str, store: DesignStore, design_id: str, parameters: dict) -> dict:
    snapshot = store.load(design_id)
    public = snapshot["public_spec"]
    spec = spec_from_payload(public)
    if task == "analysis":
        result = analyze_llc(public)
        status = result["status"]
    elif task == "corners":
        request = CornerRequest.model_validate(parameters)
        env, corners = explicit_corners(spec, request)
        device = DeviceDatabase().get_primary(spec.primary_device)
        evaluation = evaluate_operating_envelope(spec, env, corners=corners,
            device_qoss_c=device.qoss_c, device_coss_f=device.coss_er_f, zvs_level=2)
        result = {"points": json_value(evaluation.points), "worst_cases": json_value(evaluation.worst_cases),
                  "warnings": list(evaluation.warnings), "corner_count": len(corners),
                  "coverage": "Only the explicit Cartesian product; no continuous-range or thermal/loss verification"}
        grades = [p.overall_status.value for p in evaluation.points]
        status = ("FAIL" if "FAIL" in grades else "UNKNOWN" if "UNKNOWN" in grades else
                  "WARN" if "WARN" in grades else "PASS")
    elif task == "optimization":
        request = OptimizationRequest.model_validate(parameters)
        config = optimization_config(spec, request)
        if request.verify_time_domain:
            optimized = TwoStageLLCOptimizer().run(spec, config, maximum_candidates=config.count,
                top_n=request.top_n, verification_scope=request.verification_scope)
            table, warnings = optimized.stage_a.table, list(optimized.warnings)
            candidates = []
            for item in optimized.verified:
                row = candidate_provenance(item)
                row["ranking_loss_source"] = "Explicitly TD-verified point set; retain per-point component sources"
                row["analysis_feasible"] = item.analysis.feasible if item.analysis else None
                row["zvs_level2"] = []
                if item.analysis:
                    device = DeviceDatabase().get_primary(item.spec.primary_device)
                    for point in item.analysis.operating_points:
                        zvs = evaluate_zvs_margin(item.spec, item.analysis.tank,
                            point.operating_point, device=device, level=2)
                        row["zvs_level2"].append(json_value(zvs))
                zvs_ok = bool(row["zvs_level2"]) and all(z["zvs_pass"] and
                    z["model_source"] != "UNKNOWN" and z["zvs_margin"] is not None and
                    math.isfinite(z["zvs_margin"]) for z in row["zvs_level2"])
                row["accepted"] = (not item.rejected and row["analysis_feasible"] is True
                    and row["envelope_status"] in ("PASS", "WARN")
                    and row["fha_td_validity"] in ("PASS", "WARN") and zvs_ok)
                if not row["accepted"] and not row["reject_reason"]:
                    row["reject_reason"] = "Agent acceptance requires finite known envelope, TD and all-point ZVS evidence"
                effective = asdict(item.spec)
                candidate_public = {**public, **{name: effective[name] for name in (
                    "ln_ratio", "q_full_load", "resonant_frequency_hz")}}
                row["design_id"] = store.create(candidate_public)["design_id"]
                candidates.append(row)
            accepted = [row for row in candidates if row["accepted"]]
            unknown = any(row["envelope_status"] == "UNKNOWN" or row["fha_td_validity"] == "UNKNOWN"
                          or row["zvs_level2"] and any(z["model_source"] == "UNKNOWN" or z["zvs_margin"] is None
                                                     for z in row["zvs_level2"]) for row in candidates)
            status = ("UNKNOWN" if not accepted and unknown else "NO_SOLUTION" if not accepted else
                      "REVIEW" if any(row["envelope_status"] != "PASS" or row["fha_td_validity"] != "PASS"
                                      for row in accepted) else "PASS")
        else:
            optimized = LLCOptimizer().run(spec, config, maximum_candidates=config.count)
            table, candidates = optimized.table, []
            warnings = ["FHA screening only; time-domain verification NOT_RUN"]
            status = "SCREENED" if not table.empty and bool(table["feasible"].any()) else "NO_SOLUTION"
        result = {"screening": json_value(table.to_dict(orient="records")),
                  "candidates": candidates, "warnings": warnings,
                  "evaluated_count": len(table), "requested_count": config.count,
                  "request": request.model_dump(),
                  "fixed_fields": ["primary_turns", "secondary_turns", "primary_device",
                                   "sr_parallel_devices_per_position", "sr_device"],
                  "scope": "Bounded explicit search; NO_SOLUTION applies only to this grid and model"}
    else:
        raise ValueError("Unsupported agent task")
    return {"design_id": design_id, "task": task, "design_status": status,
            "runtime": snapshot["runtime"], "parameters": parameters,
            "result": json_value(result), "evidence": evidence(f"Repository {task} software model only"),
            "nonfinite_policy": "Nonfinite numerical outputs are null, never passing zeros"}


def design_control(request: ControlRequest) -> dict:
    data = request.model_dump()
    result = auto_design_controller(np.asarray(request.frequency_hz),
        np.asarray(request.plant_real) + 1j * np.asarray(request.plant_imag),
        controller_kind=request.controller_kind, sample_rate_hz=request.sample_rate_hz,
        target_crossover_hz=request.target_crossover_hz,
        target_phase_margin_deg=request.target_phase_margin_deg,
        min_gain_margin_db=request.min_gain_margin_db, max_ms=request.max_ms)
    selected = result.selected
    accepted = selected is not None and selected.accepted and (
        request.allow_lower_crossover or not result.fallback_used)
    summary = None
    if selected:
        summary = {"controller": json_value(selected.controller),
                   "achieved_crossover_hz": selected.achieved_crossover_hz,
                   "achieved_phase_margin_deg": selected.achieved_phase_margin_deg,
                   "gain_margin_db": json_value(selected.gain_margin_db),
                   "ms": json_value(selected.ms), "mt": json_value(selected.mt),
                   "kernel_accepted": selected.accepted, "parameters": selected.parameters}
    return {"input_sha256": digest(data), "input": data,
            "status": ("PASS_WITH_FALLBACK" if accepted and result.fallback_used else
                       result.status if accepted or selected is None else "REVIEW"),
            "accepted": accepted, "fallback_used": result.fallback_used,
            "requested_target_met": accepted and not result.fallback_used,
            "requested_crossover_hz": result.requested_crossover_hz,
            "requested_phase_margin_deg": result.requested_phase_margin_deg,
            "selected": summary, "message": result.message,
            "evidence": evidence("Supplied equivalent-plant FRA points only; no automatic association with an LLC design")}

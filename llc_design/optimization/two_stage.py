"""Bounded FHA screening followed by waveform-loss verification and ranking.

Waveform losses retain the system model's explicit FHA commutation/hardware
assumptions. Unknown evidence is never represented by a passing zero margin.
"""
from __future__ import annotations

from concurrent.futures import CancelledError
from dataclasses import dataclass
import math
from typing import Callable

from ..analysis.fha_td_validation import (
    FhaTdValidationReport, ModelValidity, critical_requests_from_envelope, validate_fha_against_time_domain,
)
from ..analysis.time_domain import TimeDomainConfig
from ..core.engineering import ConstraintStatus, evaluate_operating_envelope, merge_status
from ..core.spec import LLCDesignSpec, TankParameterMode
from ..core.zvs_margin import evaluate_zvs_margin
from ..models.system import LLCSystemAnalyzer, SystemAnalysis
from .sweep import LLCOptimizer, OptimizationConfig, OptimizationResult


@dataclass(frozen=True)
class VerifiedCandidate:
    spec: LLCDesignSpec
    analysis: SystemAnalysis | None
    envelope_status: ConstraintStatus
    fha_td_validity: ModelValidity
    zvs_margin_worst: float | None
    total_loss_w: float | None
    efficiency: float | None
    rank_metrics: dict[str, float]
    rejected: bool
    reject_reason: str = ""
    diagnostics: tuple[str, ...] = ()


@dataclass
class TwoStageOptimizationResult:
    stage_a: OptimizationResult
    verified: tuple[VerifiedCandidate, ...]
    ranking_weights: dict[str, float]
    warnings: tuple[str, ...]


DEFAULT_WEIGHTS = {"efficiency": 1.0, "total_loss_w": -0.002, "zvs_margin_worst": 0.15}


def validate_ranking_weights(weights: dict[str, float]) -> None:
    if (not isinstance(weights, dict) or not weights or not set(weights) <= set(DEFAULT_WEIGHTS)
            or any(type(v) not in (int, float) or not math.isfinite(v) for v in weights.values())
            or not any(weights.values())):
        raise ValueError("Ranking weights must be finite, nonzero and use supported metrics")


def _envelope_status(spec: LLCDesignSpec, analyzer: LLCSystemAnalyzer) -> ConstraintStatus:
    primary = analyzer.device_db.get_primary(spec.primary_device)
    evaluation = evaluate_operating_envelope(spec, device_qoss_c=primary.qoss_c,
        device_coss_f=primary.coss_er_f, zvs_level=2)
    statuses = [c.status for point in evaluation.points for c in point.constraints]
    return merge_status(*statuses) if statuses else ConstraintStatus.UNKNOWN


class TwoStageLLCOptimizer:
    def __init__(self, analyzer: LLCSystemAnalyzer | None = None):
        self.analyzer = analyzer or LLCSystemAnalyzer()
        self.fast = LLCOptimizer(self.analyzer)

    def run(self, base_spec: LLCDesignSpec, config: OptimizationConfig | None = None, *,
            maximum_candidates: int | None = None, top_n: int = 3,
            ranking_weights: dict[str, float] | None = None, run_stage_b: bool = True,
            cancelled: Callable[[], bool] | None = None,
            time_domain: TimeDomainConfig | None = None,
            verification_scope: str = "critical") -> TwoStageOptimizationResult:
        if verification_scope not in ("nominal", "critical"):
            raise ValueError("verification_scope must be nominal or critical")
        weights = dict(DEFAULT_WEIGHTS if ranking_weights is None else ranking_weights)
        validate_ranking_weights(weights)
        if type(top_n) is not int or not 1 <= top_n <= 32:
            raise ValueError("top_n must be an integer in 1..32")
        if TankParameterMode(base_spec.parameter_mode) != TankParameterMode.AUTO_DESIGN:
            raise ValueError("Ln/Q/fr optimization requires AUTO_DESIGN tank mode; explicit Lr/Cr/Lm remain unchanged")
        cfg = config or (OptimizationConfig.integrated(base_spec) if base_spec.integrated_magnetics else OptimizationConfig.quick())
        cfg.validate()
        if maximum_candidates is None:
            maximum_candidates = min(cfg.count, 4096)
        if type(maximum_candidates) is not int or not 1 <= maximum_candidates <= 4096:
            raise ValueError("maximum_candidates must be an integer in 1..4096")
        if time_domain is not None:
            time_domain.validate()

        def check_cancelled():
            if cancelled is not None and cancelled():
                raise CancelledError("LLC optimization cancelled")

        check_cancelled()
        stage_a = self.fast.run(base_spec, cfg, maximum_candidates=maximum_candidates,
                                **({"cancelled": cancelled} if cancelled is not None else {}))
        check_cancelled()
        warnings: list[str] = []
        table = stage_a.table
        if table is None or table.empty or "feasible" not in table.columns:
            return TwoStageOptimizationResult(stage_a, (), weights, ("stage A empty",))
        feasible = table[table["feasible"] == True].copy()
        if feasible.empty:
            return TwoStageOptimizationResult(stage_a, (), weights, ("no feasible Stage-A candidates",))
        if "weighted_loss_w" in feasible.columns:
            feasible = feasible.sort_values("weighted_loss_w", ascending=True)
        shortlist = feasible.head(top_n * 4)
        warnings.append("TD verification scope: " + ("nominal point only; other operating-point losses remain FHA" if verification_scope == "nominal" else "critical operating points"))
        warnings.append(f"Stage B examines {len(shortlist)} of {len(feasible)} FHA-feasible candidates; retains top {top_n} ranked survivors")
        verified: list[VerifiedCandidate] = []
        for _, row in shortlist.iterrows():
            check_cancelled()
            spec = base_spec.clone(ln_ratio=float(row["ln"]), q_full_load=float(row["q_full"]),
                resonant_frequency_hz=float(row["fr_khz"]) * 1e3,
                primary_turns=int(row["primary_turns"]), secondary_turns=int(row["secondary_turns"]),
                primary_device=str(row["primary_device"]), sr_parallel_devices_per_position=int(row["sr_parallel"]))
            analysis = None
            env_status, validity = ConstraintStatus.UNKNOWN, ModelValidity.UNKNOWN
            worst_zvs = None
            diagnostics: list[str] = []

            def rejected(reason):
                verified.append(VerifiedCandidate(spec, analysis, env_status, validity, worst_zvs,
                    analysis.worst_loss.total_loss_w if analysis else None,
                    analysis.minimum_efficiency.efficiency if analysis else None,
                    {}, True, reason, tuple(diagnostics)))

            try:
                analysis = self.analyzer.analyze(spec)
            except Exception as exc:
                rejected(f"analysis unavailable: {exc}")
                continue
            check_cancelled()
            try:
                env_status = _envelope_status(spec, self.analyzer)
            except Exception as exc:
                diagnostics.append(f"envelope: {exc}")
            if env_status in (ConstraintStatus.FAIL, ConstraintStatus.UNKNOWN):
                rejected(f"envelope constraint {env_status.value}")
                continue
            if not run_stage_b:
                rejected("Stage B disabled; TD losses and model validity UNKNOWN")
                continue
            try:
                if cancelled is None and time_domain is None and verification_scope == "critical":
                    report = validate_fha_against_time_domain(spec)
                else:
                    # Check cancellation between operating points, not merely
                    # between the six-point validation batches.
                    reports = []
                    from ..analysis.types import LLCAnalysisRequest
                    requests = (critical_requests_from_envelope(spec) if verification_scope == "critical" else
                        (LLCAnalysisRequest(spec, vbus_v=spec.vbus_nom_v, load_fraction=1.,
                            samples_per_cycle=(time_domain.samples_per_cycle if time_domain else 512), waveform_cycles=1),))
                    for request in requests:
                        check_cancelled()
                        part = validate_fha_against_time_domain(spec, requests=[request], time_domain=time_domain)
                        reports.append(part)
                        check_cancelled()
                        if part.overall_validity in (ModelValidity.FAIL, ModelValidity.UNKNOWN):
                            break
                    grades = [part.overall_validity for part in reports]
                    overall = (ModelValidity.FAIL if ModelValidity.FAIL in grades else
                               ModelValidity.UNKNOWN if ModelValidity.UNKNOWN in grades else
                               ModelValidity.WARN if ModelValidity.WARN in grades else ModelValidity.PASS)
                    report = FhaTdValidationReport(
                        tuple(point for part in reports for point in part.points), overall,
                        reports[0].thresholds, tuple(warning for part in reports for warning in part.warnings))
                validity = report.overall_validity
                diagnostics.extend(getattr(report, "warnings", ()))
                for validation in report.points:
                    label = getattr(validation, "label", "TD point")
                    td = getattr(validation, "td", None)
                    convergence = getattr(td, "convergence", None)
                    if convergence is not None:
                        diagnostics.append(f"{label}: converged={convergence.converged}; residual={convergence.residual_norm:.6g}")
                    for deviation in getattr(validation, "deviations", ()):
                        if deviation.status in (ConstraintStatus.FAIL, ConstraintStatus.WARN, ConstraintStatus.UNKNOWN):
                            diagnostics.append(f"{label} {deviation.metric}: {deviation.status.value}; "
                                f"FHA={deviation.fha_value:.8g}, TD={deviation.td_value:.8g}, "
                                f"deviation={deviation.deviation_percent:.5g}%")
                check_cancelled()
                if validity in (ModelValidity.FAIL, ModelValidity.UNKNOWN):
                    rejected(f"FHA↔TD MODEL_VALIDITY {validity.value}")
                    continue
                td_results = [point.td for point in report.points]
                if not td_results or any(result is None for result in td_results):
                    raise ValueError("Stage-B time-domain result is missing")
                analysis = self.analyzer.with_time_domain_losses(analysis, td_results)
            except CancelledError:
                raise
            except Exception as exc:
                validity = ModelValidity.UNKNOWN
                rejected(f"TD loss unavailable: {exc}")
                continue
            check_cancelled()
            primary = self.analyzer.device_db.get_primary(spec.primary_device)
            zvs_unknown, zvs_fail = False, False
            for point in analysis.operating_points:
                try:
                    zvs = evaluate_zvs_margin(spec, analysis.tank, point.operating_point, device=primary, level=2)
                    diagnostics.extend(getattr(zvs, "warnings", ()))
                    if not math.isfinite(zvs.zvs_margin) or getattr(zvs, "model_source", None) == "UNKNOWN":
                        zvs_unknown = True
                        continue
                    worst_zvs = zvs.zvs_margin if worst_zvs is None else min(worst_zvs, zvs.zvs_margin)
                    zvs_fail |= not getattr(zvs, "zvs_pass", zvs.zvs_margin >= 0)
                except Exception as exc:
                    zvs_unknown = True
                    diagnostics.append(f"ZVS: {exc}")
            if zvs_unknown or worst_zvs is None:
                rejected("ZVS Level-2 UNKNOWN; missing/non-finite evidence")
                continue
            if zvs_fail:
                rejected("ZVS Level-2 FAIL (FHA commutation approximation)")
                continue
            # Rank the explicitly TD-verified work-point set. In nominal-only
            # mode an unverified FHA hold-up corner must not hide changes in
            # the actual nominal waveform losses.
            scoped_points = ([point for point in analysis.operating_points
                              if point.semiconductor_source == "TIME_DOMAIN_WAVEFORM"]
                             or list(analysis.operating_points))
            metrics = {"efficiency": float(min(point.efficiency for point in scoped_points)),
                "total_loss_w": float(max(point.total_loss_w for point in scoped_points)),
                "zvs_margin_worst": float(worst_zvs)}
            if not all(math.isfinite(value) for value in metrics.values()):
                rejected("ranking metrics UNKNOWN/non-finite")
                continue
            td_count = sum(point.semiconductor_source == "TIME_DOMAIN_WAVEFORM" for point in analysis.operating_points)
            diagnostics.append(f"TD waveform losses at {td_count}/{len(analysis.operating_points)} points; remaining points retain FHA")
            diagnostics.append("Loss/efficiency ranking uses the explicitly TD-verified point set; full-project report retains every point's source")
            diagnostics.append("TD semiconductor/passive losses; commutation/ZVS and hardware feasibility remain FHA-based")
            verified.append(VerifiedCandidate(spec, analysis, env_status, validity, worst_zvs,
                metrics["total_loss_w"], metrics["efficiency"], metrics, False,
                diagnostics=tuple(dict.fromkeys(diagnostics))))
        check_cancelled()
        accepted = sorted((v for v in verified if not v.rejected),
            key=lambda v: sum(weights[k] * v.rank_metrics[k] for k in weights), reverse=True)
        rejected_rows = [v for v in verified if v.rejected]
        return TwoStageOptimizationResult(stage_a, tuple(accepted[:top_n] + rejected_rows), weights,
                                           tuple(dict.fromkeys(warnings)))


__all__ = ["DEFAULT_WEIGHTS", "TwoStageLLCOptimizer", "TwoStageOptimizationResult", "VerifiedCandidate",
           "validate_ranking_weights"]


SESSION_SCHEMA = "power-design-toolkit/llc-two-stage"
SESSION_MODEL_REVISION = "llc-two-stage-td-loss-v1"


def validate_two_stage_request(payload: dict) -> dict:
    """Pure, bounded replay-input validation; no Qt dependency or model execution."""
    from copy import deepcopy
    from dataclasses import fields
    from ..control.design_session import decode_model, encode_model
    expected = {"base_spec", "config", "ranking_weights", "maximum_candidates", "top_n", "time_domain", "verification_scope"}
    if not isinstance(payload, dict) or set(payload) != expected:
        raise ValueError("Invalid two-stage request fields")
    if payload["verification_scope"] not in ("nominal", "critical"):
        raise ValueError("Invalid TD verification scope")
    spec = decode_model(payload["base_spec"])
    if not isinstance(spec, LLCDesignSpec):
        raise ValueError("Two-stage request requires LLCDesignSpec")
    encode_model(spec)
    config = payload["config"]
    if not isinstance(config, dict) or set(config) != {f.name for f in fields(OptimizationConfig)}:
        raise ValueError("Invalid optimization axes")
    OptimizationConfig(**config).validate()
    validate_ranking_weights(payload["ranking_weights"])
    for key, maximum in (("maximum_candidates", 128), ("top_n", 8)):
        if type(payload[key]) is not int or not 1 <= payload[key] <= maximum:
            raise ValueError(f"Invalid two-stage {key}")
    td = payload["time_domain"]
    if not isinstance(td, dict) or set(td) != {f.name for f in fields(TimeDomainConfig)}:
        raise ValueError("Invalid TD solver settings")
    defaults = TimeDomainConfig()
    for key, value in td.items():
        original = getattr(defaults, key)
        if isinstance(original, bool):
            if type(value) is not bool:
                raise ValueError(f"Invalid TD boolean: {key}")
        elif value is not None:
            if type(value) not in (int, float) or not math.isfinite(value):
                raise ValueError(f"Invalid TD number: {key}")
            if isinstance(original, int) and type(value) is not int:
                raise ValueError(f"Invalid TD integer: {key}")
    solver = TimeDomainConfig(**td); solver.validate()
    if (solver.samples_per_cycle > 2048 or solver.retry_samples_per_cycle > 2048
            or solver.output_cycles > 4 or solver.maximum_settling_cycles > 500
            or solver.shooting_max_evaluations > 180 or solver.frequency_scan_points > 9):
        raise ValueError("TD request exceeds desktop solver budget")
    return deepcopy(payload)


def validate_two_stage_session(payload: dict) -> dict:
    """Saved assessment/provenance is historical only and cannot authorize Apply."""
    from copy import deepcopy
    import hashlib
    import json
    from ..control.design_session import decode_model, encode_model
    expected = {"schema", "schema_version", "model_revision", "request", "candidates", "stage_a", "selected_index"}
    if (not isinstance(payload, dict) or set(payload) != expected
            or payload["schema"] != SESSION_SCHEMA or type(payload["schema_version"]) is not int
            or payload["schema_version"] != 1 or not isinstance(payload["model_revision"], str)
            or len(payload["model_revision"]) > 128):
        raise ValueError("Invalid two-stage session header")
    validate_two_stage_request(payload["request"])
    rows = payload["candidates"]
    if not isinstance(rows, list) or len(rows) > 128:
        raise ValueError("Invalid two-stage candidate count")
    for row in rows:
        if not isinstance(row, dict) or set(row) != {"candidate_id", "spec", "rejected", "reject_reason", "diagnostics",
                "envelope_status", "fha_td_validity", "zvs_margin_worst", "total_loss_w", "efficiency", "rank_metrics", "sources"}:
            raise ValueError("Invalid historical candidate fields")
        spec = decode_model(row["spec"])
        if not isinstance(spec, LLCDesignSpec):
            raise ValueError("Invalid candidate spec")
        identity = hashlib.sha256(json.dumps(encode_model(spec), sort_keys=True, allow_nan=False).encode()).hexdigest()
        if identity != row["candidate_id"] or type(row["rejected"]) is not bool:
            raise ValueError("Invalid historical candidate identity")
        ConstraintStatus(row["envelope_status"]); ModelValidity(row["fha_td_validity"])
        if (not isinstance(row["sources"], list) or len(row["sources"]) > 32
                or not isinstance(row["diagnostics"], list) or len(row["diagnostics"]) > 512
                or not isinstance(row["reject_reason"], str)):
            raise ValueError("Invalid candidate provenance")
        for key in ("zvs_margin_worst", "total_loss_w", "efficiency"):
            if row[key] is not None and (type(row[key]) not in (int, float) or not math.isfinite(row[key])):
                raise ValueError("Invalid historical metric")
        if not isinstance(row["rank_metrics"], dict) or not set(row["rank_metrics"]) <= set(DEFAULT_WEIGHTS):
            raise ValueError("Invalid historical ranking metrics")
    if (not isinstance(payload["stage_a"], list) or len(payload["stage_a"]) > 128
            or type(payload["selected_index"]) is not int
            or not -1 <= payload["selected_index"] < max(1, len(rows))
            or (not rows and payload["selected_index"] != -1)):
        raise ValueError("Invalid saved selection/stage-A evidence")
    # Reject nonfinite/deep/unbounded auxiliary evidence before accepting history.
    encoded = json.dumps(payload, allow_nan=False, ensure_ascii=False)
    if len(encoded.encode()) > 2_000_000:
        raise ValueError("Optimization history exceeds file budget")
    return deepcopy(payload)


def _historical_number(value):
    return float(value) if value is not None and math.isfinite(value) else None


def candidate_provenance(candidate):
    """Exact input and per-point source record shared by GUI, CLI and reports."""
    import hashlib
    import json
    from ..control.design_session import encode_model
    spec = encode_model(candidate.spec)
    sources = []
    if candidate.analysis is not None:
        for point in candidate.analysis.operating_points:
            sources.append({"vbus_v": point.operating_point.vbus_v,
                "load_fraction": point.operating_point.load_fraction,
                "semiconductor_frequency_hz": _historical_number(point.semiconductor_frequency_hz),
                "passive_frequency_hz": _historical_number(point.passive_frequency_hz),
                "waveform_sha256": point.loss_waveform_sha256,
                "components": point.component_sources(), "total_loss_w": _historical_number(point.total_loss_w)})
    return {"candidate_id": hashlib.sha256(json.dumps(spec, sort_keys=True, allow_nan=False).encode()).hexdigest(),
        "spec": spec, "rejected": candidate.rejected, "reject_reason": candidate.reject_reason,
        "diagnostics": list(candidate.diagnostics), "envelope_status": candidate.envelope_status.value,
        "fha_td_validity": candidate.fha_td_validity.value, "zvs_margin_worst": _historical_number(candidate.zvs_margin_worst),
        "total_loss_w": _historical_number(candidate.total_loss_w), "efficiency": _historical_number(candidate.efficiency),
        "rank_metrics": dict(candidate.rank_metrics), "sources": sources}

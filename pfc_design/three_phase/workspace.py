"""Strict dq sessions, source-bound candidates and retained operating points."""

import json
import math
import tempfile
from dataclasses import asdict, dataclass, fields, replace
from pathlib import Path

import numpy as np

from llc_design.control.solution_map import (
    SolutionMapConstraints,
    evaluate_solution_point,
)

from .control import build_three_phase_loops, request_identity
from .exact_control import ExactController, SampledSense, controller_for
from .model import ThreePhaseConfig

SCHEMA = "three-phase-dq-session-v2"


def config_from_dict(data):
    if not isinstance(data, dict) or set(data) - {
        f.name for f in fields(ThreePhaseConfig)
    }:
        raise ValueError("unknown or malformed dq configuration fields")
    values = dict(data)
    for loop in ("current", "voltage"):
        key = loop + "_controller"
        if values.get(key) is not None:
            if not isinstance(values[key], dict):
                raise ValueError("controller must be a coefficient object")
            values[key] = ExactController(**values[key])
        key = loop + "_sense"
        if key in values:
            if not isinstance(values[key], dict):
                raise ValueError("sense must be a configuration object")
            values[key] = SampledSense(**values[key])
    config = ThreePhaseConfig(**values)
    config.validate()
    return config


def atomic_json(path, data):
    path = Path(path)
    payload = json.dumps(data, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix="." + path.name,
            suffix=".tmp",
            delete=False,
        ) as stream:
            temporary = Path(stream.name)
            stream.write(payload)
            stream.flush()
        temporary.replace(path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def save_session(path, config, retained=(), targets=None):
    config.validate()
    for corner in retained:
        corner.validate()
    if len(retained) > 16:
        raise ValueError("at most 16 retained operating points")
    atomic_json(
        path,
        {
            "schema": SCHEMA,
            "config": asdict(config),
            "retained_operating_points": [asdict(c) for c in retained],
            "targets": targets or {},
            "source_identity": request_identity(config),
        },
    )


def load_session(path):
    path = Path(path)
    if path.stat().st_size > 1_000_000:
        raise ValueError("dq session exceeds 1 MB")
    data = json.loads(
        path.read_text(encoding="utf-8"),
        parse_constant=lambda value: (_ for _ in ()).throw(
            ValueError("nonfinite JSON value")
        ),
    )
    if not isinstance(data, dict):
        raise TypeError("dq session must be an object")
    if data.get("schema") == "three-phase-dq-v1":
        if set(data) - {"schema", "config"}:
            raise ValueError("legacy parameter file contains unsupported fields")
        return config_from_dict(data["config"]), (), {}
    if data.get("schema") != SCHEMA or set(data) - {
        "schema",
        "config",
        "retained_operating_points",
        "targets",
        "source_identity",
    }:
        raise ValueError("unknown dq session schema or fields")
    config = config_from_dict(data["config"])
    retained = data.get("retained_operating_points", [])
    if not isinstance(retained, list) or len(retained) > 16:
        raise ValueError("invalid retained operating-point list")
    retained = tuple(config_from_dict(c) for c in retained)
    if data.get("source_identity") != request_identity(config):
        raise ValueError("session source identity does not match its exact inputs")
    targets = data.get("targets", {})
    if not isinstance(targets, dict) or set(targets) - {"loop", "fc_hz", "pm_deg"}:
        raise ValueError("unknown design-target fields")
    if targets:
        if targets.get("loop") not in ("current", "voltage"):
            raise ValueError("unknown target loop")
        for key in ("fc_hz", "pm_deg"):
            value = targets.get(key)
            if (
                type(value) not in (int, float)
                or not math.isfinite(value)
                or value <= 0
            ):
                raise ValueError("invalid target number")
        if targets["pm_deg"] >= 180:
            raise ValueError("PM must lie in (0,180)")
    return config, retained, targets


@dataclass(frozen=True)
class DQCandidate:
    source_identity: str
    loop: str
    target_fc_hz: float
    target_pm_deg: float
    controller: ExactController | None
    applicable: bool
    status: str
    message: str
    nominal: dict
    corners: tuple[dict, ...]


def scan_operating_points(config, retained):
    """Keep both exact controllers fixed; never retune a corner."""
    controls = {
        key + "_controller": controller_for(config, key)
        for key in ("current", "voltage")
    }
    rows = []
    for index, original in enumerate(retained):
        try:
            corner = replace(original, **controls)
            corner.validate()
            loops = build_three_phase_loops(corner)
            rows.append(
                {
                    "index": index,
                    "source_identity": loops["source_identity"],
                    "config": asdict(corner),
                    "current": loops["current"].summary(),
                    "voltage": loops["voltage"].summary(),
                    "adc_headroom": loops["adc_headroom"],
                    "status": "ANALYZED",
                }
            )
        except ValueError as exc:
            rows.append({"index": index, "status": "INVALID", "message": str(exc)})
    return tuple(rows)


def design_candidate(config, loop, fc_hz, pm_deg, retained=()):
    if loop not in ("current", "voltage"):
        raise ValueError("unknown loop")
    analysis = build_three_phase_loops(config)
    result = analysis[loop]
    # Reuse the common bounded synthesis/screen; Nyquist-outside switching
    # attenuation remains UNKNOWN. Applicability additionally uses true poles.
    constraints = SolutionMapConstraints(maximum_switching_loop_gain_db=None)
    point = evaluate_solution_point(
        result.frequency_hz,
        result.plant_response,
        sample_rate_hz=1 / result.sample_time_s,
        switching_frequency_hz=config.control_hz,
        target_crossover_hz=fc_hz,
        target_phase_margin_deg=pm_deg,
        constraints=constraints,
    )
    if point.kp is None or point.ti_s is None:
        return DQCandidate(
            request_identity(config),
            loop,
            fc_hz,
            pm_deg,
            None,
            False,
            point.status.name,
            point.message,
            asdict(point),
            (),
        )
    rate = 1 / result.sample_time_s
    kp, ki = point.kp, point.kp / point.ti_s
    controller = ExactController(
        (kp + ki / (2 * rate), -kp + ki / (2 * rate)),
        (1.0, -1.0),
        rate,
        f"shared Fc×PM Tustin PI / {loop} / Fc={fc_hz:.17g} / PM={pm_deg:.17g}",
    )
    trial = replace(config, **{loop + "_controller": controller})
    evaluated_loops = build_three_phase_loops(trial)
    evaluated = evaluated_loops[loop]
    margins = evaluated.margins
    actual_fc, actual_pm = margins.critical_gain_crossover_hz, margins.phase_margin_deg
    # Missing GM is unknown, never infinity. A candidate needs observed GM >=6 dB.
    applicable = (
        all(evaluated_loops[key].stable for key in ("current", "voltage"))
        and actual_fc is not None
        and actual_pm is not None
        and abs(actual_fc / fc_hz - 1) <= constraints.crossover_tolerance_fraction
        and abs(actual_pm - pm_deg) <= constraints.phase_margin_tolerance_deg
        and bool(margins.gain_margins_db)
        and min(margins.gain_margins_db) >= 6
        and np.max(abs(evaluated.sensitivity)) <= 2
    )
    corners = scan_operating_points(trial, retained)
    for corner in corners:
        entry = corner.get(loop)
        applicable = bool(
            applicable
            and entry
            and all(corner[key]["stable"] for key in ("current", "voltage"))
            and all(
                value["status"] != "OVERRANGE"
                for value in corner["adc_headroom"].values()
            )
            and entry["phase_margin_deg"] is not None
            and entry["phase_margin_deg"] >= pm_deg - 3
            and entry["minimum_signed_gain_margin_db"] is not None
            and entry["minimum_signed_gain_margin_db"] >= 6
            and entry["ms"] <= 2
        )
    if any(v["status"] == "OVERRANGE" for v in analysis["adc_headroom"].values()):
        applicable = False
    return DQCandidate(
        request_identity(config),
        loop,
        fc_hz,
        pm_deg,
        controller,
        bool(applicable),
        "APPLICABLE_LOCAL_MODEL" if applicable else "REJECTED_OR_INCOMPLETE",
        "Shared Tustin synthesis; actual nominal/corner poles, all signed margins and Ms checked. Switching attenuation outside Nyquist remains UNKNOWN; ADC UNKNOWN is unqualified.",
        {
            "shared_screen": asdict(point),
            "actual": evaluated.summary(),
            "both_loop_stability": {
                key: evaluated_loops[key].summary() for key in ("current", "voltage")
            },
        },
        corners,
    )


def apply_candidate(config, candidate):
    if request_identity(config) != candidate.source_identity:
        raise ValueError(
            "candidate is stale: source inputs or inner controller changed"
        )
    if not candidate.applicable or candidate.controller is None:
        raise ValueError("candidate is not applicable")
    return replace(config, **{candidate.loop + "_controller": candidate.controller})

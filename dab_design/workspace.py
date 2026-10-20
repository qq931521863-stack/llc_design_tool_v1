"""DAB-specific session, bounded candidate and detached research consumers."""

import json
import math
from dataclasses import asdict, replace
from pathlib import Path

import numpy as np
from scipy import signal

from llc_design.control.candidate_evidence import exact_signature
from llc_design.control.digital_loop import DigitalTransferFunction
from llc_design.control.solution_map import synthesize_tustin_pi_at_target
from power_control_tools.research_export import (
    ResearchExport,
    ResearchLoop,
    ResearchModel,
)

from .control import DABVoltageControlConfig, build_dab_voltage_loop
from .core import DABConfig
from .sensing import DABSenseConfig

SESSION_SCHEMA = "dab-control-session-v1"


def controller_payload(controller):
    if controller is None:
        return None
    return {
        "b": controller.numerator.tolist(),
        "a": controller.denominator.tolist(),
        "sample_time_s": controller.sample_time_s,
        "name": controller.name,
    }


def controller_from_payload(data):
    if data is None:
        return None
    if set(data) != {"b", "a", "sample_time_s", "name"}:
        raise ValueError("Invalid exact controller payload")
    if not isinstance(data["name"], str) or max(len(data["a"]), len(data["b"])) > 128:
        raise ValueError("Invalid controller name or excessive controller order")
    if np.asarray(data["b"]).ndim != 1 or np.asarray(data["a"]).ndim != 1:
        raise ValueError("Controller coefficient arrays must be one-dimensional")
    return DigitalTransferFunction(
        data["b"], data["a"], data["sample_time_s"], data["name"]
    )


def source_identity(stage, control, vb, power, controller):
    return exact_signature(
        asdict(stage),
        asdict(control),
        vb,
        power,
        controller_payload(controller or control.controller()),
    )


def validate_session(data):
    """Parse the entire payload before the GUI mutates anything; no silent clamps."""
    if not isinstance(data, dict) or data.get("schema") != SESSION_SCHEMA:
        raise ValueError("Unsupported DAB control session schema")
    allowed = {
        "schema",
        "stage",
        "control",
        "operating_point",
        "controller",
        "workpoints",
        "ui",
        "historical_result",
    }
    if set(data) != allowed:
        raise ValueError("Incomplete or unknown DAB session fields")
    json.dumps(data, allow_nan=False)  # reject nonfinite history before any GUI change
    if set(data["stage"]) != set(asdict(DABConfig())) or set(data["control"]) != set(
        asdict(DABVoltageControlConfig())
    ):
        raise ValueError(
            "Session must contain complete stage and control configurations"
        )
    if set(data["control"]["sensing"]) != set(asdict(DABSenseConfig())):
        raise ValueError("Session must contain complete sensing configuration")
    stage = DABConfig(**data["stage"])
    stage = replace(
        stage, vb_cases_v=tuple(stage.vb_cases_v), load_ratios=tuple(stage.load_ratios)
    )
    stage.validate()
    values = dict(data["control"])
    values["sensing"] = DABSenseConfig(**values["sensing"])
    control = DABVoltageControlConfig(**values)
    control.validate()
    if control.sample_hz > stage.switching_hz:
        raise ValueError("Sample rate exceeds switching frequency")
    vb, power = data["operating_point"]
    if not all(
        isinstance(x, (float, int)) and math.isfinite(x) and x > 0 for x in (vb, power)
    ):
        raise ValueError("Finite positive operating point required")
    controller = controller_from_payload(data["controller"])
    if controller is not None and not math.isclose(
        controller.sample_time_s, 1 / control.sample_hz, rel_tol=1e-12
    ):
        raise ValueError("Saved exact controller sample period mismatch")
    points = data["workpoints"]
    if not isinstance(points, list) or len(points) > 200:
        raise ValueError("At most 200 retained workpoints")
    for point in points:
        if set(point) != {"name", "voltage_v", "power_w"} or not isinstance(
            point["name"], str
        ):
            raise ValueError("Invalid retained workpoint")
        if not all(
            math.isfinite(point[k]) and point[k] > 0 for k in ("voltage_v", "power_w")
        ):
            raise ValueError("Invalid retained workpoint coordinates")
    ui = data["ui"]
    if set(ui) != {
        "target_fc_hz",
        "target_pm_deg",
        "peak_a",
        "power_tolerance_percent",
        "commutation_proxy",
        "visible_traces",
    }:
        raise ValueError("Invalid session UI settings")
    if not all(
        isinstance(ui[k], (float, int)) and math.isfinite(ui[k]) and ui[k] > 0
        for k in ("target_fc_hz", "target_pm_deg", "peak_a", "power_tolerance_percent")
    ):
        raise ValueError("Invalid session UI numeric values")
    if not isinstance(ui["visible_traces"], list) or any(
        key not in ("G", "H", "C", "L", "T", "R") for key in ui["visible_traces"]
    ):
        raise ValueError("Invalid visible Bode traces")
    if ui["target_pm_deg"] >= 180 or type(ui["commutation_proxy"]) is not bool:
        raise ValueError("Invalid session phase target or proxy option")
    if data["historical_result"] is not None and not isinstance(
        data["historical_result"], dict
    ):
        raise ValueError("Invalid historical result")
    return stage, control, float(vb), float(power), controller


def synthesize_candidate(stage, control, vb, power, fc, pm, *, source, min_gm_db=6.0):
    """Exact Tustin PI, exact discrete roots; no controller refit or aliased Fsw screen."""
    loop, _ = build_dab_voltage_loop(stage, vb, power, control)
    if not loop.frequency_hz[0] <= fc <= loop.frequency_hz[-1]:
        return {
            "source": source,
            "fc": fc,
            "pm": pm,
            "can_apply": False,
            "status": "OUTSIDE_VALID_BAND",
        }
    omega = [2 * np.pi * fc / control.sample_hz]
    fixed = (
        signal.freqz(loop.plant_b, loop.plant_a, worN=omega)[1][0]
        * signal.freqz(loop.sense_b, loop.sense_a, worN=omega)[1][0]
    )
    synthesis = synthesize_tustin_pi_at_target(
        fixed,
        target_crossover_hz=fc,
        target_phase_margin_deg=pm,
        sample_rate_hz=control.sample_hz,
    )
    if synthesis is None:
        return {
            "source": source,
            "fc": fc,
            "pm": pm,
            "can_apply": False,
            "status": "PI_PHASE_UNREACHABLE",
        }
    pi, _ = synthesis
    controller = pi.transfer_function()
    check, model = build_dab_voltage_loop(stage, vb, power, control, controller)
    margins = check.margins
    gm = min(margins.gain_margins_db, default=None)
    reasons = []
    if not check.stable:
        reasons.append("DISCRETE_POLES_UNSTABLE")
    if margins.phase_margin_deg is None or margins.critical_gain_crossover_hz is None:
        reasons.append("FC_PM_UNKNOWN")
    elif (
        abs(margins.critical_gain_crossover_hz / fc - 1) > 0.05
        or abs(margins.phase_margin_deg - pm) > 3
    ):
        reasons.append("TARGET_MISMATCH")
    if gm is None:
        reasons.append("GM_UNKNOWN")
    elif gm < min_gm_db:
        reasons.append("SIGNED_GM_BELOW_6DB")
    ms = float(np.max(abs(check.sensitivity)))
    if ms > 2:
        reasons.append("MS_ABOVE_2")
    return {
        "source": source,
        "fc": fc,
        "pm": pm,
        "kp": pi.kp,
        "ti_s": pi.ti_s,
        "controller": controller_payload(controller),
        "can_apply": not reasons,
        "status": "; ".join(reasons) if reasons else "MODEL_SCREEN_PASS",
        "summary": check.summary(),
        "max_sensitivity": ms,
        "model": model,
        "scope": "Exact declared linear-model poles + observed finite-band margins; switching-frequency attenuation not evaluated above Nyquist; hardware unqualified",
    }


def scan_workpoints(stage, control, controller, points, *, cancelled=None):
    results = []
    for point in points:
        if cancelled is not None and cancelled():
            raise ValueError("DAB scan cancelled")
        try:
            loop, model = build_dab_voltage_loop(
                stage, point["voltage_v"], point["power_w"], control, controller
            )
            results.append(
                {**point, "valid": True, "summary": loop.summary(), "model": model}
            )
        except ValueError as exc:
            results.append({**point, "valid": False, "reason": str(exc)})
    return results


def research_bundle(loop, metadata):
    """Exact stored constituent rational models; renderer checks every sample."""
    f = loop.frequency_hz
    ts = loop.sample_time_s
    gb, ga, cb, ca, hb, ha = (
        loop.plant_b,
        loop.plant_a,
        loop.controller_b,
        loop.controller_a,
        loop.sense_b,
        loop.sense_a,
    )
    fb, fa = np.convolve(gb, cb), np.convolve(ga, ca)
    lb, la = np.convolve(fb, hb), np.convolve(fa, ha)
    length = max(len(lb), len(la))
    closed = np.pad(la, (0, length - len(la))) + np.pad(lb, (0, length - len(lb)))
    records = [
        ("G", "Physical power-command plant", "W", "V", gb, ga, loop.plant_response),
        ("H", "Incremental voltage sensing", "V", "V", hb, ha, loop.sense_response),
        ("C", "Exact digital controller", "V", "W", cb, ca, loop.controller_response),
        (
            "F",
            "Forward G*C",
            "V",
            "V",
            fb,
            fa,
            loop.plant_response * loop.controller_response,
        ),
        ("L", "Return ratio G*C*H", "1", "1", lb, la, loop.open_response),
        (
            "T",
            "Measured complementary sensitivity",
            "V",
            "V",
            lb,
            closed,
            loop.closed_response,
        ),
        ("S", "Sensitivity", "1", "1", la, closed, loop.sensitivity),
        (
            "R",
            "Physical output/reference",
            "V",
            "V",
            np.convolve(fb, ha),
            closed,
            loop.output_reference_response,
        ),
    ]
    models = tuple(
        ResearchModel(
            name,
            desc,
            "discrete",
            iu,
            ou,
            ts,
            tuple(b),
            tuple(a),
            tuple(r),
            "completed DAB snapshot",
        )
        for name, desc, iu, ou, b, a, r in records
    )
    return ResearchExport(
        "DAB voltage-loop research",
        tuple(f),
        models,
        (ResearchLoop("DAB", "G", "C", "L", "T", "S", "F", "H"),),
        json.dumps(metadata, ensure_ascii=False, allow_nan=False),
        (
            "Exact to the declared single-rate averaged model, not hardware qualification.",
            "Pcmd [W] actuates selected forward TPS/SPS mapping. Reverse power is unsupported.",
            "ADC aperture, quantization and switching aliasing excluded; digital lowpass/delays are exact.",
            "T is measured-output/reference; R is physical-output/reference.",
            "All signed gain margins are retained; an absent crossing is unknown, not infinite.",
        ),
    )


def write_report(path, snapshot):
    """Standalone HTML with source, model evidence, plots and complete frozen data."""
    import base64
    import html
    from io import BytesIO

    text = json.dumps(snapshot, indent=2, ensure_ascii=False, allow_nan=False)
    metrics = snapshot.get("metrics", {})
    model = metrics.get("model", {})
    loop = metrics.get("loop", {})
    source = snapshot.get("run_context", {})
    content = [
        "<!doctype html><meta charset='utf-8'><title>DAB control report</title>",
        (
            "<style>body{font:15px sans-serif;max-width:1100px;margin:32px auto;padding:0 16px}"
            "pre{white-space:pre-wrap;overflow-wrap:anywhere}td,th{border:1px solid #ddd;padding:8px;text-align:left}"
            "table{border-collapse:collapse;width:100%}img{max-width:100%}</style>"
        ),
        (
            "<h1>DAB completed analysis report</h1><p>Frozen source evidence. Declared linear-model "
            "stability does not establish switching or hardware qualification.</p>"
        ),
        "<h2>Run source</h2><pre>"
        + html.escape(json.dumps(source, indent=2, ensure_ascii=False))
        + "</pre>",
    ]
    if loop:
        content.append("<h2>Model and stability evidence</h2><table>")
        for key in (
            "stable",
            "stability_scope",
            "max_pole_radius",
            "crossover_hz",
            "phase_margin_deg",
            "gain_crossovers_hz",
            "phase_margins_deg",
            "phase_crossovers_hz",
            "gain_margins_db",
            "gain_margin_convention",
            "closed_loop_poles",
            "closed_response_definition",
        ):
            content.append(
                "<tr><th>"
                + html.escape(key)
                + "</th><td>"
                + html.escape(str(loop.get(key)))
                + "</td></tr>"
            )
        content.append(
            "</table><h2>Physical assumptions / sensing / timing</h2><pre>"
            + html.escape(json.dumps(model, indent=2, ensure_ascii=False))
            + "</pre>"
        )
    if "responses" in snapshot and "signals" in snapshot:
        from matplotlib.figure import Figure

        figure = Figure(figsize=(11, 7), layout="constrained")
        axes = figure.subplots(2, 2)
        frequency = np.asarray(snapshot["frequency_hz"])
        for name, pairs in snapshot["responses"].items():
            values = np.asarray(pairs)
            response = values[:, 0] + 1j * values[:, 1]
            axes[0, 0].semilogx(
                frequency, 20 * np.log10(np.maximum(abs(response), 1e-20)), label=name
            )
            axes[0, 1].semilogx(
                frequency, np.unwrap(np.angle(response)) * 180 / np.pi, label=name
            )
        for name in ("vout_v", "vref_v", "measured_v"):
            axes[1, 0].plot(snapshot["time_s"], snapshot["signals"][name], label=name)
        for name in ("pcmd_w", "pout_w", "pload_w"):
            axes[1, 1].plot(snapshot["time_s"], snapshot["signals"][name], label=name)
        for ax, label in zip(
            axes.ravel(),
            ("Gain / dB", "Phase / deg", "Voltage / V", "Power / W"),
            strict=True,
        ):
            ax.set_ylabel(label)
            ax.grid(True, alpha=0.3)
            ax.legend()
        buffer = BytesIO()
        figure.savefig(buffer, format="png", dpi=120)
        content.append(
            "<h2>Completed Bode and time response</h2><img alt='DAB completed response plots' src='data:image/png;base64,"
            + base64.b64encode(buffer.getvalue()).decode("ascii")
            + "'>"
        )
    content.append(
        "<details><summary>Complete frozen snapshot data</summary><pre>"
        + html.escape(text)
        + "</pre></details>"
    )
    Path(path).write_text("".join(content), encoding="utf-8")

"""Human-readable report backed by the complete saved calculation evidence."""
from __future__ import annotations

import html
import json


def _table(headers, rows):
    def cell(value):
        return html.escape(str(value if value is not None else "UNKNOWN"))
    heading = "".join(f"<th>{cell(value)}</th>" for value in headers)
    body = "".join("<tr>" + "".join(f"<td>{cell(value)}</td>" for value in row) + "</tr>" for row in rows)
    return f"<table><thead><tr>{heading}</tr></thead><tbody>{body}</tbody></table>"


def render_html(document: dict) -> str:
    job, design = document["job"], document["design"]
    calculation = job["result"]
    result = calculation["result"]
    spec = design["effective_spec"]
    sections = ["<h2>Design inputs (SI units)</h2>", _table(["Field", "Value", "Origin"], (
        (name, value, "supplied" if name in design["supplied_fields"] else "default")
        for name, value in spec.items() if name in ("vbus_nom_v", "vbus_min_normal_v", "vbus_max_v",
            "vbus_hold_end_v", "vout_v", "pout_w", "primary_topology", "ln_ratio", "q_full_load",
            "resonant_frequency_hz", "primary_turns", "secondary_turns", "primary_device", "sr_device")))]
    if calculation["task"] == "analysis":
        sections += ["<h2>System analysis</h2>", _table(["Metric", "Value"], result["summary"].items())]
    elif calculation["task"] == "corners":
        rows = []
        for point in result["points"]:
            op, zvs = point["operating_point"] or {}, point["zvs"] or {}
            corner = point["corner"]
            rows.append((corner["corner_id"], corner["vbus_v"], corner["load_fraction"],
                op.get("switching_frequency_hz"), op.get("resonant_current_rms_a"),
                zvs.get("zvs_margin"), point["overall_status"]))
        sections += ["<h2>Explicit corners</h2>", _table(
            ["Corner", "Bus / V", "Load / pu", "fs / Hz", "Ir / Arms", "ZVS L2 margin", "Status"], rows),
            "<p>Only these discrete points were scanned; thermal and complete loss validation are outside this task.</p>"]
    elif calculation["task"] == "optimization":
        sections += ["<h2>FHA screening</h2>", _table(
            ["Ln", "Q", "fr / kHz", "Feasible", "Failure"],
            ((row.get("ln"), row.get("q_full"), row.get("fr_khz"), row.get("feasible"), row.get("failure"))
             for row in result["screening"])), "<h2>Two-stage candidates</h2>", _table(
            ["Candidate design", "Envelope", "FHA/TD", "Accepted", "Reason"],
            ((row["design_id"], row["envelope_status"], row["fha_td_validity"], row["accepted"], row["reject_reason"])
             for row in result["candidates"]))]
    warnings = result.get("warnings", [])
    if warnings:
        sections.append("<h2>Warnings</h2><ul>" + "".join(f"<li>{html.escape(str(w))}</li>" for w in warnings) + "</ul>")
    raw = html.escape(json.dumps(document, ensure_ascii=False, indent=2, allow_nan=False))
    return ("<!doctype html><html lang='en'><meta charset='utf-8'>"
        "<title>Power Design Toolkit — calculation evidence</title>"
        "<style>body{max-width:1160px;margin:40px auto;font:16px/1.5 sans-serif;padding:0 24px;color:#17283a}"
        "table{border-collapse:collapse;width:100%;font-size:14px}th,td{border:1px solid #dce3eb;padding:8px;text-align:left;overflow-wrap:anywhere}"
        "th{background:#edf3f9}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f3f5f7;padding:20px}</style>"
        "<h1>Power Design Toolkit</h1><p>Software-model calculation. Hardware validation: NOT RUN.</p>"
        f"<p><b>Design status: {html.escape(str(job['design_status']))}</b></p>"
        f"<p>Design: {html.escape(job['design_id'])}<br>Task: {html.escape(calculation['task'])}</p>"
        + "".join(sections) + "<h2>Complete reproducibility record</h2>"
        f"<details><summary>Inputs, versions, provenance and all numerical evidence</summary><pre>{raw}</pre></details></html>")

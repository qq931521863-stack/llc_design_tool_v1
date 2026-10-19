"""Portable magnetic projects preserve source/model snapshots and recompute outputs."""
from __future__ import annotations

from dataclasses import fields
from html import escape
import json
import math
from pathlib import Path

from llc_design.control.design_session import strict_json_loads, MAX_FILE_BYTES
from .manufacturer_catalog import (SourceCoreSelection, reference_evaluation,
                                   source_pfc_components, validate_model_declaration)

SCHEMA = "power-design-toolkit-source-magnetic-project-v1"


def project_document(selection, *, reference_inputs=None, pfc_inputs=None, declaration=None):
    document = {"schema": SCHEMA, "selection": selection.snapshot,
                "selection_identity": selection.identity,
                "reference_inputs": reference_inputs, "pfc_inputs": pfc_inputs,
                "user_model_declaration": declaration}
    recompute_project(document)
    return document


def recompute_project(document):
    if not isinstance(document, dict) or document.get("schema") != SCHEMA:
        raise ValueError("Unsupported magnetic project schema")
    allowed = {"schema", "selection", "selection_identity", "reference_inputs", "pfc_inputs", "user_model_declaration"}
    if set(document) != allowed:
        raise ValueError("Incomplete or unknown magnetic project fields")
    selection = SourceCoreSelection(document["selection"])
    if selection.identity != document["selection_identity"]:
        raise ValueError("Magnetic source snapshot identity mismatch")
    declaration = document.get("user_model_declaration")
    if declaration is not None:
        validate_model_declaration(declaration)
    reference_inputs = document["reference_inputs"]
    reference = None
    if reference_inputs is not None:
        if not isinstance(reference_inputs, dict) or set(reference_inputs) - {"turns", "n_cores", "h_oe", "b_kg", "f_khz"}:
            raise ValueError("Unknown reference-calculation input")
        reference = reference_evaluation(selection, **reference_inputs)
    result = None
    if document["pfc_inputs"] is not None:
        from .pfc_inductor_designer import PFCInductorDesignRequest, design_pfc_inductor
        inputs = document["pfc_inputs"]
        supported = {f.name for f in fields(PFCInductorDesignRequest)} - {"core", "material", "source_snapshot", "model_declaration"}
        if not isinstance(inputs, dict) or set(inputs) != supported:
            raise ValueError("Missing or unknown PFC magnetic-project input")
        for key, value in inputs.items():
            if key == "topology":
                if value not in {"ttpl", "vienna"}: raise ValueError("Invalid PFC topology")
            elif key in {"n_cores", "curve_points"}:
                if type(value) is not int or value < 1 or value > (10000 if key == "curve_points" else 100):
                    raise ValueError(f"Invalid {key}")
            elif type(value) not in (int, float) or not math.isfinite(value):
                raise ValueError(f"Invalid numeric PFC input: {key}")
            elif key == "copper_temperature_c":
                if not 20 <= value <= 180:
                    raise ValueError("Source winding estimate supports copper temperatures from 20 to 180 °C")
            elif key == "enamel_build_mm":
                if value < 0: raise ValueError("Enamel build cannot be negative")
            elif value <= 0:
                raise ValueError(f"PFC input must be positive: {key}")
        if not 0 < inputs["efficiency"] <= 1:
            raise ValueError("Efficiency must be in (0, 1]")
        if not 0 < inputs["max_fill_factor"] < 1:
            raise ValueError("Fill factor must be in (0, 1)")
        if inputs["curve_points"] < 2:
            raise ValueError("At least two curve points are required")
        core, material = source_pfc_components(selection, declaration)
        result = design_pfc_inductor(PFCInductorDesignRequest(
            **inputs, core=core, material=material, source_snapshot=selection.snapshot,
            model_declaration=declaration,
        ))
    return selection, reference, result


def save_project(document, path):
    recompute_project(document)
    path = Path(path)
    text = json.dumps(document, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    if len(text.encode()) > MAX_FILE_BYTES:
        raise ValueError("Magnetic project exceeds maximum file size")
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)
    return path


def read_project(path):
    path = Path(path)
    if path.stat().st_size > MAX_FILE_BYTES:
        raise ValueError("Magnetic project exceeds maximum file size")
    document = strict_json_loads(path.read_text(encoding="utf-8"))
    recompute_project(document)
    return document


def write_report(document, path):
    selection, reference, result = recompute_project(document)
    sections = ["<!doctype html><html lang='zh'><meta charset='utf-8'><title>磁件工程报告</title>",
                "<style>body{font:16px sans-serif;max-width:1050px;margin:40px auto;line-height:1.5}pre{white-space:pre-wrap;word-break:break-word;background:#f3f5f8;padding:16px}li{margin:5px}</style>",
                f"<h1>磁件工程报告：{escape(selection.label)}</h1>",
                f"<p>记录：{escape(selection.record_id)}<br>来源快照 SHA256：{selection.identity}</p>",
                "<p>原厂出版数据、出版公式参考值与用户声明模型分开记录。未知参数未用其他厂牌或材料补齐。</p>"]
    if reference is not None:
        sections.extend(("<h2>零偏磁 / 出版公式参考计算</h2>", "<pre>" + escape(json.dumps(reference, ensure_ascii=False, indent=2)) + "</pre>"))
    if result is not None:
        output = {"core_identity": result.request.core.core_data, "turns": result.turns,
                  "l_zero_bias_uh": result.l_no_bias_uh, "l_full_load_peak_uh": result.l_full_load_peak_uh,
                  "core_loss_reference_w": result.core_loss_w, "copper_dc_loss_w": result.copper_loss_w,
                  "fill_factor": result.fill_factor, "h_peak_oe": result.h_peak_oe,
                  "b_ac_peak_t": result.b_ac_line_max_t, "warnings": result.warnings}
        sections.extend(("<h2>用户声明模型 PFC 参考估算</h2>", "<pre>" + escape(json.dumps(output, ensure_ascii=False, indent=2)) + "</pre>"))
    sections.append("<h2>所选记录与逐字段来源</h2><pre>" + escape(json.dumps(selection.snapshot, ensure_ascii=False, indent=2)) + "</pre>")
    sections.append("<h2>用户模型声明与输入</h2><pre>" + escape(json.dumps({k:v for k,v in document.items() if k != "selection"}, ensure_ascii=False, indent=2)) + "</pre>")
    sections.append("</html>")
    path = Path(path)
    path.write_text("\n".join(sections), encoding="utf-8")
    return path

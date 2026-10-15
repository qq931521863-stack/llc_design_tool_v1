"""Traceable geometry records, distinct from ready core/bobbin design presets."""

import json
from pathlib import Path


def load_magnetic_catalog() -> dict:
    path = Path(__file__).parent.parent / "data" / "magnetic_catalog.json"
    return json.loads(path.read_text(encoding="utf-8"))


def catalog_summary() -> dict:
    from .transformer_designer import load_transformer_core_presets

    records = load_magnetic_catalog()["records"]
    return {
        "reference_records": len(records),
        "ready_presets": len(load_transformer_core_presets()),
        "families": sorted({r["family"] for r in records}),
        "flagged_records": [r["key"] for r in records if r["issues"]],
    }

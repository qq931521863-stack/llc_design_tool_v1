"""Content-addressed design inputs and bounded, local calculation artifacts."""
from __future__ import annotations

import hashlib
import importlib.metadata
import json
import math
import os
import re
import sys
import tempfile
from dataclasses import asdict, is_dataclass
from enum import Enum
from pathlib import Path
from typing import Any

import numpy as np

from llc_design import __version__
from webapp.service import spec_from_payload

SCHEMA = "power-design-toolkit/agent-design-v1"
MAX_BYTES = 8_000_000
_PACKAGES = ("llc_design", "power_agent", "webapp", "power_control_tools", "power_codegen",
             "power_sim", "engineering_data", "power_stage_design", "pfc_design", "dab_design", "backend")


def json_value(value: Any) -> Any:
    if is_dataclass(value):
        return json_value(asdict(value))
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, np.ndarray):
        return json_value(value.tolist())
    if isinstance(value, np.generic):
        return json_value(value.item())
    if isinstance(value, complex):
        return {"real": json_value(value.real), "imag": json_value(value.imag)}
    if isinstance(value, dict):
        return {str(k): json_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_value(v) for v in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def runtime_identity() -> dict:
    root = Path(__file__).resolve().parent.parent
    hashes = {"source_sha256": hashlib.sha256(), "data_sha256": hashlib.sha256()}
    for package in _PACKAGES:
        for path in sorted((root / package).rglob("*")):
            if path.suffix not in (".py", ".json") or not path.is_file():
                continue
            name = "data_sha256" if path.suffix == ".json" else "source_sha256"
            hashes[name].update(str(path.relative_to(root)).encode() + b"\0")
            hashes[name].update(hashlib.sha256(path.read_bytes()).digest())
    versions = {}
    for package in ("numpy", "scipy", "pandas", "pydantic", "mcp"):
        try:
            versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            versions[package] = "NOT_INSTALLED"
    return {"toolkit_version": __version__, "python": sys.version.split()[0],
            **{name: value.hexdigest() for name, value in hashes.items()}, "dependencies": versions}


_LOADED_RUNTIME = runtime_identity()


def checked_runtime() -> dict:
    current = runtime_identity()
    if current != _LOADED_RUNTIME:
        raise ValueError("Source/data/runtime changed since server startup; restart the MCP server")
    return current


def evidence(scope: str) -> dict:
    return {"execution_status": "COMPLETED", "scope": scope,
            "verified_for_hardware": False, "hardware_status": "NOT_RUN"}


def atomic_json(path: Path, document: dict) -> None:
    atomic_text(path, canonical(document).decode("utf-8") + "\n")


def atomic_text(path: Path, text: str) -> None:
    encoded = text.encode("utf-8")
    if len(encoded) > MAX_BYTES:
        raise ValueError("Agent document exceeds 8 MB artifact budget")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".agent-", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(encoded)
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def read_json(path: Path) -> dict:
    if path.stat().st_size > MAX_BYTES:
        raise ValueError("Agent document exceeds 8 MB artifact budget")
    def reject(value):
        raise ValueError(f"Nonfinite JSON constant: {value}")
    result = json.loads(path.read_text(encoding="utf-8"), parse_constant=reject)
    if not isinstance(result, dict):
        raise TypeError("Agent document must be a JSON object")
    return result


class DesignStore:
    def __init__(self, root: str | Path | None = None):
        self.root = Path(root or os.environ.get("POWER_DESIGN_WORKSPACE", "output/agent")).expanduser().resolve()

    def path(self, category: str, identity: str, suffix: str = ".json") -> Path:
        if category not in ("designs", "jobs", "exports") or not re.fullmatch(r"[0-9a-f]{64}", identity):
            raise ValueError("Invalid agent artifact identifier")
        path = self.root / category / (identity + suffix)
        if not path.resolve().is_relative_to(self.root):
            raise ValueError("Agent artifact escapes workspace")
        return path

    def create(self, public_spec: dict) -> dict:
        spec = spec_from_payload(public_spec)
        document = {"schema": SCHEMA, "public_spec": public_spec,
                    "effective_spec": json_value(spec), "runtime": checked_runtime(),
                    "supplied_fields": sorted(public_spec),
                    "defaulted_fields": sorted(set(asdict(spec)) - set(public_spec))}
        design_id = digest(document)
        path = self.path("designs", design_id)
        if path.exists():
            if read_json(path) != document:
                raise ValueError("Existing design snapshot failed integrity check")
        else:
            atomic_json(path, document)
        return {"design_id": design_id, **document, "path": str(path),
                "evidence": evidence("Validated inputs only; no engineering calculation performed")}

    def load(self, design_id: str) -> dict:
        document = read_json(self.path("designs", design_id))
        if document.get("schema") != SCHEMA or digest(document) != design_id:
            raise ValueError("Design snapshot failed integrity check")
        if document["runtime"] != runtime_identity():
            raise ValueError("Design runtime/data changed; create a new snapshot before recomputing")
        checked_runtime()
        spec = spec_from_payload(document["public_spec"])
        if json_value(spec) != document["effective_spec"]:
            raise ValueError("Design effective defaults changed")
        return document

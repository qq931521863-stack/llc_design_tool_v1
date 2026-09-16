"""Built-in + user MOSFET library for PFC engineering workflows.

The legacy :class:`pfc_design.core.spec.MosfetDatabase` remains untouched for
backward compatibility.  This module adds a persistent user overlay that can be
shared by TTPL, Vienna and future PFC engineering pages.

Built-in records are read-only and originate from ``pfc_design/data/mosfets.json``.
The engineering-data catalogue currently marks that dataset as unverified for
hardware release, so the GUI must not present those records as signed-off parts.
"""
from __future__ import annotations

from dataclasses import asdict
import json
import os
from pathlib import Path
import sys

from pfc_design.core.spec import MosfetSpec

USER_LIBRARY_SCHEMA = "power-design-toolkit-pfc-mosfet-library-v1"


def default_user_pfc_device_library_path() -> Path:
    """Return a platform-appropriate persistent JSON path.

    ``POWER_DESIGN_TOOLKIT_PFC_DEVICE_LIBRARY`` can override the location for
    regression tests, portable installs and team-controlled data folders.
    """

    override = os.getenv("POWER_DESIGN_TOOLKIT_PFC_DEVICE_LIBRARY", "").strip()
    if override:
        return Path(override).expanduser()
    if sys.platform == "win32":
        root = Path(os.getenv("APPDATA", Path.home() / "AppData" / "Roaming"))
        return root / "PowerDesignToolkit" / "pfc_devices.json"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "PowerDesignToolkit" / "pfc_devices.json"
    root = Path(os.getenv("XDG_CONFIG_HOME", Path.home() / ".config"))
    return root / "power-design-toolkit" / "pfc_devices.json"


def _empty_payload() -> dict:
    return {
        "metadata": {
            "schema": USER_LIBRARY_SCHEMA,
            "source": "User-maintained PFC MOSFET library",
            "warning": (
                "Verify all datasheet values, revisions and reference conditions "
                "before hardware release."
            ),
        },
        "mosfets": [],
    }


def _load_json(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"PFC MOSFET library must contain a JSON object: {path}")
    return data


def _records(data: dict, source: Path) -> list[MosfetSpec]:
    raw = data.get("mosfets", [])
    if not isinstance(raw, list):
        raise ValueError(f"mosfets must be a JSON array in {source}")
    result: list[MosfetSpec] = []
    seen: set[str] = set()
    for entry in raw:
        if not isinstance(entry, dict):
            raise ValueError(f"mosfet entries must be objects in {source}")
        device = MosfetSpec(**entry)
        name = device.part_number.strip()
        if not name:
            raise ValueError(f"part_number must not be empty in {source}")
        folded = name.casefold()
        if folded in seen:
            raise ValueError(f"duplicate PFC MOSFET '{name}' in {source}")
        seen.add(folded)
        result.append(device)
    return result


def _atomic_write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temp.replace(path)


class PFCDeviceDatabase:
    """Merged built-in + user PFC MOSFET database.

    User records may add new part numbers but cannot silently shadow built-ins.
    The same database object can be used by TTPL and Vienna workflows.
    """

    def __init__(
        self,
        path: str | Path | None = None,
        user_path: str | Path | None = None,
        include_user: bool | None = None,
    ) -> None:
        self.source_path = (
            Path(path)
            if path is not None
            else Path(__file__).parent.parent / "data" / "mosfets.json"
        )
        self.user_path = (
            Path(user_path).expanduser()
            if user_path is not None
            else default_user_pfc_device_library_path()
        )
        if include_user is None:
            include_user = path is None or user_path is not None
        self.include_user = bool(include_user)
        self.builtin: list[MosfetSpec] = []
        self.user: list[MosfetSpec] = []
        self.all: list[MosfetSpec] = []
        self.warnings: list[str] = []
        self.metadata: dict = {}
        self.user_metadata: dict = {}
        self._builtin_names: set[str] = set()
        self.refresh()

    def refresh(self) -> None:
        data = _load_json(self.source_path)
        self.metadata = data.get("metadata", {}) if isinstance(data.get("metadata", {}), dict) else {}
        self.builtin = _records(data, self.source_path)
        self._builtin_names = {d.part_number.casefold() for d in self.builtin}
        self.user = []
        self.user_metadata = {}
        self.warnings = []

        if self.include_user and self.user_path.exists():
            user_data = _load_json(self.user_path)
            self.user_metadata = (
                user_data.get("metadata", {})
                if isinstance(user_data.get("metadata", {}), dict)
                else {}
            )
            for device in _records(user_data, self.user_path):
                if device.part_number.casefold() in self._builtin_names:
                    self.warnings.append(
                        f"user PFC MOSFET '{device.part_number}' ignored because a built-in record uses the same part number"
                    )
                else:
                    self.user.append(device)
        self.all = self.builtin + self.user

    def get(self, part_number: str) -> MosfetSpec:
        folded = part_number.casefold()
        for device in self.all:
            if device.part_number.casefold() == folded:
                return device
        choices = ", ".join(d.part_number for d in self.all)
        raise KeyError(f"PFC MOSFET '{part_number}' not found; choices: {choices}")

    def is_user(self, part_number: str) -> bool:
        folded = part_number.casefold()
        return any(d.part_number.casefold() == folded for d in self.user)

    def query(
        self,
        *,
        vds_min: float = 0.0,
        vds_max: float = float("inf"),
        technology: str = "",
    ) -> list[MosfetSpec]:
        tech = technology.strip().casefold()
        result: list[MosfetSpec] = []
        for device in self.all:
            if not vds_min <= device.vds_max <= vds_max:
                continue
            if tech and tech != "all":
                candidate = device.technology.casefold()
                if tech == "si":
                    if not candidate.startswith("si ") or candidate == "sic":
                        continue
                elif candidate != tech:
                    continue
            result.append(device)
        return sorted(result, key=lambda item: item.rds_on_25c)

    def save_user_device(self, device: MosfetSpec, *, overwrite: bool = False) -> None:
        folded = device.part_number.casefold()
        if folded in self._builtin_names:
            raise ValueError(
                f"'{device.part_number}' is a built-in record; clone it to a new part number before editing"
            )
        payload = self._read_user_payload()
        records = payload["mosfets"]
        existing = next(
            (
                i
                for i, item in enumerate(records)
                if str(item.get("part_number", "")).casefold() == folded
            ),
            None,
        )
        encoded = asdict(device)
        if existing is not None:
            if not overwrite:
                raise FileExistsError(f"user PFC MOSFET '{device.part_number}' already exists")
            records[existing] = encoded
        else:
            records.append(encoded)
        _atomic_write(self.user_path, payload)
        self.refresh()

    def delete_user_device(self, part_number: str) -> None:
        payload = self._read_user_payload()
        folded = part_number.casefold()
        filtered = [
            item
            for item in payload["mosfets"]
            if str(item.get("part_number", "")).casefold() != folded
        ]
        if len(filtered) == len(payload["mosfets"]):
            raise KeyError(f"user PFC MOSFET '{part_number}' not found")
        payload["mosfets"] = filtered
        _atomic_write(self.user_path, payload)
        self.refresh()

    def import_user_library(self, path: str | Path, *, overwrite: bool = False) -> int:
        source = Path(path)
        incoming = _records(_load_json(source), source)
        for device in incoming:
            self.save_user_device(device, overwrite=overwrite)
        return len(incoming)

    def export_user_library(self, path: str | Path) -> Path:
        target = Path(path)
        _atomic_write(target, self._read_user_payload())
        return target

    def _read_user_payload(self) -> dict:
        if not self.user_path.exists():
            return _empty_payload()
        payload = _load_json(self.user_path)
        _records(payload, self.user_path)  # validate hand-edited files before mutation
        payload.setdefault("metadata", _empty_payload()["metadata"])
        payload.setdefault("mosfets", [])
        return payload


__all__ = [
    "PFCDeviceDatabase",
    "USER_LIBRARY_SCHEMA",
    "default_user_pfc_device_library_path",
]

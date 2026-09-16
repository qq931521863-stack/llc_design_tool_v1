"""Reference and user MOSFET database support.

The built-in JSON file contains generic engineering reference devices.  Users can
keep selected real parts in a separate per-user library without modifying the
installed package.  The two libraries are merged at load time; built-in records
cannot be silently shadowed by user records.
"""

from __future__ import annotations

from dataclasses import asdict
import json
import os
from pathlib import Path
import sys
from typing import Literal

from ..core.spec import MosfetSpec

DeviceRole = Literal["primary", "sr"]
USER_LIBRARY_SCHEMA = "power-design-toolkit-device-library-v1"


def default_user_device_library_path() -> Path:
    """Return the platform-appropriate persistent user-device JSON path.

    ``POWER_DESIGN_TOOLKIT_DEVICE_LIBRARY`` is intentionally supported for
    regression tests, portable installations and engineering teams that keep a
    shared library in a controlled location.
    """

    override = os.getenv("POWER_DESIGN_TOOLKIT_DEVICE_LIBRARY", "").strip()
    if override:
        return Path(override).expanduser()
    if sys.platform == "win32":
        root = Path(os.getenv("APPDATA", Path.home() / "AppData" / "Roaming"))
        return root / "PowerDesignToolkit" / "devices.json"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "PowerDesignToolkit" / "devices.json"
    root = Path(os.getenv("XDG_CONFIG_HOME", Path.home() / ".config"))
    return root / "power-design-toolkit" / "devices.json"


def _empty_user_library() -> dict:
    return {
        "metadata": {
            "schema": USER_LIBRARY_SCHEMA,
            "source": "User-maintained MOSFET library",
            "warning": "Verify every datasheet value and operating condition before hardware release.",
        },
        "primary_mosfets": [],
        "sr_mosfets": [],
    }


def _load_json(path: Path) -> dict:
    with path.open(encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(f"device library must contain a JSON object: {path}")
    return data


def _records(data: dict, key: str, source: Path) -> list[MosfetSpec]:
    raw = data.get(key, [])
    if not isinstance(raw, list):
        raise ValueError(f"{key} must be a JSON array in {source}")
    result: list[MosfetSpec] = []
    seen: set[str] = set()
    for entry in raw:
        if not isinstance(entry, dict):
            raise ValueError(f"{key} entries must be objects in {source}")
        record = MosfetSpec(**entry)
        name = record.part_number.strip()
        if not name:
            raise ValueError(f"device part_number must not be empty in {source}")
        folded = name.casefold()
        if folded in seen:
            raise ValueError(f"duplicate device '{name}' in {source}")
        seen.add(folded)
        result.append(record)
    return result


def _atomic_write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temp.replace(path)


class DeviceDatabase:
    """Merged built-in + user MOSFET library.

    ``path`` preserves the original API for loading an alternate standalone
    library.  When ``path`` is omitted the packaged generic reference library is
    loaded and, by default, the persistent user library is merged on top.
    User records may add new part numbers but may not shadow built-ins.
    """

    def __init__(self, path: str | Path | None = None,
                 user_path: str | Path | None = None,
                 include_user: bool | None = None):
        self.source_path = Path(path) if path else Path(__file__).parent.parent / "data" / "devices.json"
        self.user_path = Path(user_path).expanduser() if user_path else default_user_device_library_path()
        if include_user is None:
            include_user = path is None or user_path is not None
        self.include_user = bool(include_user)
        self.warnings: list[str] = []
        self.metadata: dict = {}
        self.user_metadata: dict = {}
        self.primary: list[MosfetSpec] = []
        self.sr: list[MosfetSpec] = []
        self.user_primary: list[MosfetSpec] = []
        self.user_sr: list[MosfetSpec] = []
        self._builtin_primary_names: set[str] = set()
        self._builtin_sr_names: set[str] = set()
        self.refresh()

    def refresh(self) -> None:
        data = _load_json(self.source_path)
        self.metadata = data.get("metadata", {}) if isinstance(data.get("metadata", {}), dict) else {}
        builtin_primary = _records(data, "primary_mosfets", self.source_path)
        builtin_sr = _records(data, "sr_mosfets", self.source_path)
        self._builtin_primary_names = {d.part_number.casefold() for d in builtin_primary}
        self._builtin_sr_names = {d.part_number.casefold() for d in builtin_sr}
        self.warnings = []
        self.user_primary = []
        self.user_sr = []
        self.user_metadata = {}

        if self.include_user and self.user_path.exists():
            user_data = _load_json(self.user_path)
            self.user_metadata = user_data.get("metadata", {}) if isinstance(user_data.get("metadata", {}), dict) else {}
            user_primary = _records(user_data, "primary_mosfets", self.user_path)
            user_sr = _records(user_data, "sr_mosfets", self.user_path)
            self.user_primary = self._without_builtin_collisions(
                user_primary, self._builtin_primary_names, "primary"
            )
            self.user_sr = self._without_builtin_collisions(
                user_sr, self._builtin_sr_names, "SR"
            )

        self.primary = builtin_primary + self.user_primary
        self.sr = builtin_sr + self.user_sr

    def _without_builtin_collisions(self, records: list[MosfetSpec],
                                    builtin_names: set[str], role: str) -> list[MosfetSpec]:
        accepted: list[MosfetSpec] = []
        for record in records:
            if record.part_number.casefold() in builtin_names:
                self.warnings.append(
                    f"user {role} device '{record.part_number}' ignored because a built-in record uses the same part number"
                )
                continue
            accepted.append(record)
        return accepted

    def get_primary(self, part_number: str) -> MosfetSpec:
        return self._get(self.primary, part_number)

    def get_sr(self, part_number: str) -> MosfetSpec:
        return self._get(self.sr, part_number)

    def is_user(self, role: DeviceRole, part_number: str) -> bool:
        records = self.user_primary if role == "primary" else self.user_sr
        folded = part_number.casefold()
        return any(record.part_number.casefold() == folded for record in records)

    def save_user_device(self, role: DeviceRole, device: MosfetSpec,
                         *, overwrite: bool = False) -> None:
        role = self._validate_role(role)
        builtin_names = self._builtin_primary_names if role == "primary" else self._builtin_sr_names
        folded = device.part_number.casefold()
        if folded in builtin_names:
            raise ValueError(
                f"'{device.part_number}' is a built-in reference name; clone it to a new part number before editing"
            )
        payload = self._read_user_payload()
        key = "primary_mosfets" if role == "primary" else "sr_mosfets"
        records = payload[key]
        existing = next((i for i, item in enumerate(records)
                         if str(item.get("part_number", "")).casefold() == folded), None)
        encoded = asdict(device)
        if existing is not None:
            if not overwrite:
                raise FileExistsError(f"user device '{device.part_number}' already exists")
            records[existing] = encoded
        else:
            records.append(encoded)
        _atomic_write(self.user_path, payload)
        self.refresh()

    def delete_user_device(self, role: DeviceRole, part_number: str) -> None:
        role = self._validate_role(role)
        payload = self._read_user_payload()
        key = "primary_mosfets" if role == "primary" else "sr_mosfets"
        folded = part_number.casefold()
        filtered = [item for item in payload[key]
                    if str(item.get("part_number", "")).casefold() != folded]
        if len(filtered) == len(payload[key]):
            raise KeyError(f"user device '{part_number}' not found")
        payload[key] = filtered
        _atomic_write(self.user_path, payload)
        self.refresh()

    def import_user_library(self, path: str | Path, *, overwrite: bool = False) -> tuple[int, int]:
        """Merge a portable user-library JSON into the persistent library."""

        source = Path(path)
        incoming = _load_json(source)
        primary = _records(incoming, "primary_mosfets", source)
        sr = _records(incoming, "sr_mosfets", source)
        for record in primary:
            self.save_user_device("primary", record, overwrite=overwrite)
        for record in sr:
            self.save_user_device("sr", record, overwrite=overwrite)
        return len(primary), len(sr)

    def export_user_library(self, path: str | Path) -> Path:
        target = Path(path)
        payload = self._read_user_payload()
        _atomic_write(target, payload)
        return target

    def _read_user_payload(self) -> dict:
        if not self.user_path.exists():
            return _empty_user_library()
        payload = _load_json(self.user_path)
        # Validate before editing so malformed hand-edited files fail loudly.
        _records(payload, "primary_mosfets", self.user_path)
        _records(payload, "sr_mosfets", self.user_path)
        payload.setdefault("metadata", _empty_user_library()["metadata"])
        payload.setdefault("primary_mosfets", [])
        payload.setdefault("sr_mosfets", [])
        return payload

    @staticmethod
    def _validate_role(role: str) -> DeviceRole:
        if role not in {"primary", "sr"}:
            raise ValueError("device role must be 'primary' or 'sr'")
        return role  # type: ignore[return-value]

    @staticmethod
    def _get(records: list[MosfetSpec], part_number: str) -> MosfetSpec:
        for record in records:
            if record.part_number.casefold() == part_number.casefold():
                return record
        choices = ", ".join(r.part_number for r in records)
        raise KeyError(f"device '{part_number}' not found; choices: {choices}")

"""ngspice batch backend used for netlist/waveform smoke validation.

Production closed-loop co-simulation uses the shared-ngspice callback API so
circuit state is continuous while the digital controller executes at its sample
rate. This module remains a deterministic batch backend for open-loop waveform
correlation and CI integration tests.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os
import re
import shutil
import subprocess
import tempfile

import numpy as np

from .ir import CircuitIR
from .netlist import render_netlist


_ERROR_PATTERNS = (
    "error",
    "singular matrix",
    "timestep too small",
    "no convergence",
    "non-convergence",
    "unknown device",
    "no such model",
    "fatal",
)


@dataclass(frozen=True)
class NgSpiceRawData:
    plot_name: str
    variables: tuple[str, ...]
    vectors: dict[str, np.ndarray]
    points: int

    def vector(self, name: str) -> np.ndarray:
        if name not in self.vectors:
            raise KeyError(f"ngspice vector not found: {name}")
        return self.vectors[name]


@dataclass(frozen=True)
class NgSpiceBatchResult:
    command: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str
    netlist_path: Path
    raw_path: Path
    data: NgSpiceRawData | None
    errors: tuple[str, ...]

    @property
    def succeeded(self) -> bool:
        return self.returncode == 0 and self.data is not None and not self.errors


def find_ngspice_executable(explicit: str | Path | None = None) -> str | None:
    if explicit is not None:
        path = Path(explicit).expanduser()
        return str(path) if path.is_file() else None
    env = os.environ.get("NGSPICE_EXE")
    if env:
        path = Path(env).expanduser()
        if path.is_file():
            return str(path)
    for candidate in ("ngspice_con", "ngspice"):
        found = shutil.which(candidate)
        if found:
            return found
    common = (
        Path(r"C:\Program Files\ngspice\bin\ngspice_con.exe"),
        Path(r"C:\Program Files\ngspice\ngspice_con.exe"),
        Path(r"C:\Program Files (x86)\ngspice\bin\ngspice_con.exe"),
        Path("/opt/homebrew/bin/ngspice"),
        Path("/usr/local/bin/ngspice"),
        Path("/usr/bin/ngspice"),
    )
    for path in common:
        if path.is_file():
            return str(path)
    return None


def _parse_real_value(token: str) -> float:
    text = token.strip().rstrip(",")
    if text.startswith("(") and text.endswith(")"):
        text = text[1:-1].split(",", 1)[0]
    return float(text)


def parse_ascii_raw(path: str | Path) -> NgSpiceRawData:
    raw_path = Path(path)
    text = raw_path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    plot_name = ""
    variable_count: int | None = None
    point_count: int | None = None
    variable_names: list[str] = []
    values_start: int | None = None
    for index, raw_line in enumerate(lines):
        line = raw_line.strip()
        lower = line.lower()
        if lower.startswith("plotname:"):
            plot_name = line.split(":", 1)[1].strip()
        elif lower.startswith("no. variables:"):
            variable_count = int(line.split(":", 1)[1].strip())
        elif lower.startswith("no. points:"):
            point_count = int(line.split(":", 1)[1].strip())
        elif line == "Variables:":
            j = index + 1
            while j < len(lines):
                item = lines[j].strip()
                if not item or item in {"Values:", "Binary:"}:
                    break
                parts = item.split()
                if len(parts) >= 2 and parts[0].lstrip("+-").isdigit():
                    variable_names.append(parts[1])
                else:
                    break
                j += 1
        elif line == "Binary:":
            raise ValueError("binary ngspice raw is not supported; batch engine must write ASCII raw")
        elif line == "Values:":
            values_start = index + 1
            break
    if variable_count is None or point_count is None or values_start is None:
        raise ValueError("invalid ngspice ASCII raw header")
    if len(variable_names) != variable_count:
        raise ValueError(f"raw variable table has {len(variable_names)} names, expected {variable_count}")

    flat: list[float] = []
    for raw_line in lines[values_start:]:
        line = raw_line.strip()
        if not line:
            continue
        parts = line.split()
        if not parts:
            continue
        try:
            flat.append(_parse_real_value(parts[-1]))
        except ValueError:
            continue
    expected = variable_count * point_count
    if len(flat) < expected:
        raise ValueError(f"raw Values section has {len(flat)} numeric values, expected {expected}")
    if len(flat) > expected:
        flat = flat[:expected]
    matrix = np.asarray(flat, dtype=float).reshape(point_count, variable_count)
    vectors = {name: matrix[:, i].copy() for i, name in enumerate(variable_names)}
    return NgSpiceRawData(plot_name, tuple(variable_names), vectors, point_count)


def _error_lines(stdout: str, stderr: str) -> tuple[str, ...]:
    found: list[str] = []
    for stream in (stdout, stderr):
        for line in stream.splitlines():
            lowered = line.lower()
            if any(pattern in lowered for pattern in _ERROR_PATTERNS):
                found.append(line.strip())
    return tuple(dict.fromkeys(found))


class NgSpiceBatchEngine:
    def __init__(self, executable: str | Path | None = None, *, timeout_s: float = 120.0):
        self.executable = find_ngspice_executable(executable)
        self.timeout_s = float(timeout_s)

    @property
    def available(self) -> bool:
        return self.executable is not None

    def run_transient(
        self,
        circuit: CircuitIR,
        *,
        stop_time_s: float,
        max_step_s: float,
        workdir: str | Path | None = None,
        initial_step_s: float | None = None,
        use_initial_conditions: bool = False,
    ) -> NgSpiceBatchResult:
        if self.executable is None:
            raise FileNotFoundError("ngspice executable was not found; install ngspice or set NGSPICE_EXE")
        stop = float(stop_time_s)
        step = float(max_step_s)
        initial = step if initial_step_s is None else float(initial_step_s)
        if stop <= 0.0 or step <= 0.0 or initial <= 0.0:
            raise ValueError("transient times must be positive")
        if step > stop:
            raise ValueError("max_step_s cannot exceed stop_time_s")

        wd = Path(workdir) if workdir is not None else Path(tempfile.mkdtemp(prefix="power_design_ngspice_"))
        wd.mkdir(parents=True, exist_ok=True)
        netlist_path = wd / "circuit.cir"
        raw_path = wd / "transient.raw"

        base = render_netlist(circuit, include_end=False)
        uic = " uic" if use_initial_conditions else ""
        control = [
            f".tran {initial:.12e} {stop:.12e} 0 {step:.12e}{uic}",
            ".control",
            "set filetype=ascii",
            "run",
            f"write {raw_path.name} all",
            "quit",
            ".endc",
            ".end",
            "",
        ]
        netlist_path.write_text(base + "\n".join(control), encoding="utf-8")

        command = (self.executable, "-b", netlist_path.name)
        proc = subprocess.run(
            command,
            cwd=wd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=self.timeout_s,
            check=False,
        )
        errors = _error_lines(proc.stdout, proc.stderr)
        data: NgSpiceRawData | None = None
        if raw_path.exists():
            try:
                data = parse_ascii_raw(raw_path)
            except Exception as exc:
                errors = errors + (f"raw parse error: {exc}",)
        elif proc.returncode == 0:
            errors = errors + ("ngspice completed without creating transient.raw",)

        return NgSpiceBatchResult(
            command,
            int(proc.returncode),
            proc.stdout,
            proc.stderr,
            netlist_path,
            raw_path,
            data,
            tuple(dict.fromkeys(errors)),
        )


__all__ = [
    "NgSpiceRawData",
    "NgSpiceBatchResult",
    "NgSpiceBatchEngine",
    "find_ngspice_executable",
    "parse_ascii_raw",
]

from __future__ import annotations

import csv
from pathlib import Path
import re
from typing import Iterable

import numpy as np

from .models import FRAMeasurement, FRASourceFormat


def _read_text(path: str | Path) -> str:
    p = Path(path)
    errors: list[str] = []
    for encoding in ("utf-8-sig", "utf-16", "gb18030"):
        try:
            return p.read_text(encoding=encoding)
        except UnicodeError as exc:
            errors.append(f"{encoding}: {exc}")
    raise ValueError(f"unable to decode FRA file {p}: {'; '.join(errors)}")


def _sanitize(
    frequency_hz: Iterable[float],
    magnitude_db: Iterable[float],
    phase_deg: Iterable[float],
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    f = np.asarray(list(frequency_hz), dtype=float)
    mag = np.asarray(list(magnitude_db), dtype=float)
    phase = np.asarray(list(phase_deg), dtype=float)
    if f.size < 2 or f.size != mag.size or f.size != phase.size:
        raise ValueError("FRA file did not provide at least two complete frequency/gain/phase rows")
    keep = np.isfinite(f) & np.isfinite(mag) & np.isfinite(phase) & (f > 0.0)
    f, mag, phase = f[keep], mag[keep], phase[keep]
    if f.size < 2:
        raise ValueError("FRA file contains too few valid rows")
    order = np.argsort(f, kind="stable")
    f, mag, phase = f[order], mag[order], phase[order]
    # Deterministic duplicate handling: keep the first sample at each frequency.
    unique = np.r_[True, np.diff(f) > 0.0]
    f, mag, phase = f[unique], mag[unique], phase[unique]
    if f.size < 2:
        raise ValueError("FRA file contains fewer than two unique frequencies")
    return f, mag, phase


def _header_index(headers: list[str], *, include: tuple[str, ...]) -> int:
    lowered = [h.strip().lower() for h in headers]
    for i, text in enumerate(lowered):
        if all(token in text for token in include):
            return i
    raise ValueError(f"required FRA column not found: {' + '.join(include)}")


def _load_bode100(path: str | Path) -> FRAMeasurement:
    text = _read_text(path)
    rows = list(csv.reader(text.splitlines(), delimiter=";"))
    if len(rows) < 3:
        raise ValueError("Bode100 CSV is empty or incomplete")
    headers = [h.strip() for h in rows[0]]
    fi = _header_index(headers, include=("frequency",))
    mi = _header_index(headers, include=("magnitude", "db"))
    pi = _header_index(headers, include=("phase",))
    f: list[float] = []
    mag: list[float] = []
    phase: list[float] = []
    required = max(fi, mi, pi)
    for row in rows[1:]:
        if len(row) <= required:
            continue
        try:
            f.append(float(row[fi].strip()))
            mag.append(float(row[mi].strip()))
            phase.append(float(row[pi].strip()))
        except ValueError:
            continue
    ff, mm, pp = _sanitize(f, mag, phase)
    return FRAMeasurement(
        ff,
        mm,
        pp,
        FRASourceFormat.BODE100,
        str(path),
        # Bode100 loop-injection exports commonly show PM directly around the
        # 0-dB point (e.g. +86 deg).  Shift to the canonical negative-feedback
        # loop phase so stability equations use -93.7 deg in that example.
        default_phase_offset_deg=-180.0,
        metadata={"delimiter": ";", "magnitude_column": headers[mi], "phase_column": headers[pi]},
    )


def _numeric_triplets_from_lines(lines: Iterable[str]) -> tuple[list[float], list[float], list[float]]:
    f: list[float] = []
    mag: list[float] = []
    phase: list[float] = []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith(("#", "//", ";")):
            continue
        tokens = [t for t in re.split(r"[\s,;]+", stripped) if t]
        if len(tokens) < 3:
            continue
        try:
            values = [float(tokens[i]) for i in range(3)]
        except ValueError:
            continue
        f.append(values[0]); mag.append(values[1]); phase.append(values[2])
    return f, mag, phase


def _load_simplis(path: str | Path) -> FRAMeasurement:
    text = _read_text(path)
    f, mag, phase = _numeric_triplets_from_lines(text.splitlines())
    ff, mm, pp = _sanitize(f, mag, phase)
    return FRAMeasurement(
        ff,
        mm,
        pp,
        FRASourceFormat.SIMPLIS,
        str(path),
        default_phase_offset_deg=0.0,
        metadata={"columns": "freq Gain Phase"},
    )


def _load_generic(path: str | Path) -> FRAMeasurement:
    text = _read_text(path)
    f, mag, phase = _numeric_triplets_from_lines(text.splitlines())
    ff, mm, pp = _sanitize(f, mag, phase)
    return FRAMeasurement(
        ff,
        mm,
        pp,
        FRASourceFormat.GENERIC,
        str(path),
        default_phase_offset_deg=0.0,
        metadata={"columns": "first three numeric columns = frequency/gain_dB/phase_deg"},
    )


def load_fra_file(path: str | Path, source_format: FRASourceFormat | str) -> FRAMeasurement:
    source_format = FRASourceFormat(source_format)
    if source_format == FRASourceFormat.BODE100:
        return _load_bode100(path)
    if source_format == FRASourceFormat.SIMPLIS:
        return _load_simplis(path)
    return _load_generic(path)

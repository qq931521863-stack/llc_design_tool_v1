"""Port of DAB_TPS_LOAD_POINTS_2018_V2.m; all quantities use SI units.

n = Ns/Np, secondary bridge voltage referred to primary = VB/n.
D1/D2/D3 are half-period-normalized absolute leg shifts, not three
independent inter-bridge angles. This is a forward-power steady-state model.
"""

from __future__ import annotations

import csv
import json
import math
from dataclasses import asdict, dataclass, fields
from pathlib import Path

import numpy as np


@dataclass(frozen=True)
class DABConfig:
    va_v: float = 400.0
    vb_cases_v: tuple[float, ...] = (300.0, 350.0, 400.0, 450.0, 500.0)
    load_ratios: tuple[float, ...] = (0.10, 0.30, 0.50, 0.75, 1.0)
    turns_ratio: float = 1.0
    switching_hz: float = 80e3
    inductance_h: float = 30e-6
    primary_capacitance_f: float = 4.7e-6
    output_capacitance_f: float = 1000e-6
    series_resistance_ohm: float = 20e-3
    low_power_w: float = 5000.0
    rated_power_w: float = 7000.0
    derate_end_v: float = 350.0
    rated_start_v: float = 400.0
    p_limit: float = 0.995
    wave_samples: int = 16384
    tbclk_hz: float = 100e6

    def validate(self) -> None:
        positive = (
            self.va_v,
            self.turns_ratio,
            self.switching_hz,
            self.inductance_h,
            self.primary_capacitance_f,
            self.output_capacitance_f,
            self.low_power_w,
            self.rated_power_w,
            self.derate_end_v,
            self.rated_start_v,
            self.tbclk_hz,
        )
        if any(not math.isfinite(x) or x <= 0 for x in positive):
            raise ValueError("DAB physical parameters must be finite and positive")
        if (
            not math.isfinite(self.series_resistance_ohm)
            or self.series_resistance_ohm < 0
        ):
            raise ValueError("series resistance must be finite and nonnegative")
        if not 0 < self.p_limit <= 1:
            raise ValueError("p_limit must be in (0, 1]")
        if self.rated_start_v <= self.derate_end_v:
            raise ValueError("rated_start_v must exceed derate_end_v")
        if self.rated_power_w < self.low_power_w:
            raise ValueError("rated power must be >= derated power")
        if (
            type(self.wave_samples) is not int
            or self.wave_samples < 512
            or self.wave_samples > 262144
            or self.wave_samples % 2
        ):
            raise ValueError("wave_samples must be an even integer in [512, 262144]")
        if not self.vb_cases_v or any(
            not math.isfinite(v) or v <= 0 for v in self.vb_cases_v
        ):
            raise ValueError("VB cases must be finite and positive")
        if not self.load_ratios or any(
            not math.isfinite(x) or not 0 <= x <= 1 for x in self.load_ratios
        ):
            raise ValueError("load ratios must be finite and in [0, 1]")
        if len(self.vb_cases_v) * len(self.load_ratios) > 200:
            raise ValueError("at most 200 operating points per sweep")
        if self.tbclk_hz / self.switching_hz < 2:
            raise ValueError("TBCLK must provide at least two counts per period")

    def full_load_power(self, vb_v: float) -> float:
        fraction = float(
            np.clip(
                (vb_v - self.derate_end_v) / (self.rated_start_v - self.derate_end_v),
                0,
                1,
            )
        )
        return self.low_power_w + fraction * (self.rated_power_w - self.low_power_w)


@dataclass(frozen=True)
class TPSCommand:
    m: float
    pbase_w: float
    p_raw: float
    p: float
    p_boundary: float
    mode_id: int
    d1: float
    d2: float
    d3: float
    feasible: bool
    limited: bool


def tps_command(config: DABConfig, vb_v: float, pref_w: float) -> TPSCommand:
    config.validate()
    if not math.isfinite(vb_v) or vb_v <= 0 or not math.isfinite(pref_w) or pref_w < 0:
        raise ValueError(
            "VB must be positive; only nonnegative forward power is supported"
        )
    m = config.turns_ratio * config.va_v / vb_v
    base = (
        config.va_v
        * vb_v
        / (8 * config.turns_ratio * config.switching_hz * config.inductance_h)
    )
    raw = pref_w / base
    p = min(raw, config.p_limit)
    if abs(m - 1) < 1e-12:
        boundary, d1 = 0.0, 0.0
        d2 = d3 = 0.5 * (1 - math.sqrt(max(0.0, 1 - p)))
        mode = 2
    elif m > 1:
        boundary = 2 * (m - 1) / (m * m)
        if p < boundary:
            d1 = 1 - math.sqrt(p / (2 * (m - 1)))
            d2, d3, mode = (m - 1) * (1 - d1), d1, 3
        else:
            d1 = (m - 1) * math.sqrt((1 - p) / (m * m - 2 * m + 2))
            d2 = d3 = 0.5 + d1 * (m - 2) / (2 * (m - 1))
            mode = 4
    else:
        boundary = 2 * (m - m * m)
        if p < boundary:
            d1 = 1 - math.sqrt(p / (2 * m * (1 - m)))
            d2, d3, mode = 0.0, m * d1 - m + 1, 1
        else:
            d1 = 0.0
            d2 = 0.5 * (1 - math.sqrt((1 - p) / (2 * m * m - 2 * m + 1)))
            d3, mode = 2 * m * d2 - d2 - m + 1, 2
    d1, d2, d3 = (float(np.clip(d, 0, 1)) for d in (d1, d2, d3))
    return TPSCommand(
        m, base, raw, p, boundary, mode, d1, d2, d3, raw <= 1, raw > config.p_limit
    )


@dataclass(frozen=True)
class DABWaveform:
    time_s: np.ndarray
    signals: dict[str, np.ndarray]
    metrics: dict[str, float]
    primary_event_pu: np.ndarray
    secondary_event_pu: np.ndarray
    primary_event_current_a: np.ndarray
    secondary_event_current_a: np.ndarray


def periodic_waveform(
    config: DABConfig, vb_v: float, command: TPSCommand, *, include_cp: bool
) -> DABWaveform:
    """Sampled ideal bridge voltages, zero-mean periodic L / damped LC current.

    L-only deliberately retains the reference's right-rectangle integration;
    LC retains its FFT convention (including positive Nyquist). Neither model
    includes deadtime, Coss, diode recovery, magnetizing current or core losses.
    """
    config.validate()
    n = config.wave_samples
    t = np.arange(n) / n
    dt = 1 / (config.switching_hz * n)

    def leg(shift):
        return np.where((t - shift) % 1 < 0.5, 1.0, -1.0)

    qa1, qa2 = leg(0), leg(command.d1 / 2)
    qb1, qb2 = leg(command.d2 / 2), leg(command.d3 / 2)
    sa, sb = (qa1 + qa2) / 2, (qb1 + qb2) / 2
    va, vb = config.va_v * sa, vb_v / config.turns_ratio * sb
    drive = va - vb
    if include_cp:
        harmonic = np.concatenate((np.arange(n // 2 + 1), np.arange(-n // 2 + 1, 0)))
        omega = 2 * np.pi * config.switching_hz * harmonic[1:]
        zc = 1 / (1j * omega * config.primary_capacitance_f)
        z = config.series_resistance_ohm + 1j * omega * config.inductance_h + zc
        if np.min(np.abs(z)) < 1e-10:
            raise ValueError(
                "undamped LC harmonic resonance: no unique bounded periodic response"
            )
        ih = np.zeros(n, dtype=complex)
        vch = np.zeros(n, dtype=complex)
        ih[1:] = np.fft.fft(drive)[1:] / z
        vch[1:] = ih[1:] * zc
        current, vcp = np.fft.ifft(ih).real, np.fft.ifft(vch).real
        vl = drive - vcp - config.series_resistance_ohm * current
    else:
        current = np.cumsum(drive) * dt / config.inductance_h
        current -= np.mean(current)
        vcp = np.zeros(n)
        vl = drive.copy()  # L-only has no series R; do not report fictitious R loss.
    pin, pout = va * current, vb * current
    iin, iout = sa * current, sb * current / config.turns_ratio
    ripple_i = iout - np.mean(iout)
    ripple_v = np.cumsum(ripple_i) * dt / config.output_capacitance_f
    ripple_v -= np.mean(ripple_v)
    ep = np.array([0, 0.5, command.d1 / 2, (command.d1 / 2 + 0.5) % 1])
    es = np.array(
        [
            command.d2 / 2,
            (command.d2 / 2 + 0.5) % 1,
            command.d3 / 2,
            (command.d3 / 2 + 0.5) % 1,
        ]
    )
    ip, ise = current[(ep * n).astype(int) % n], current[(es * n).astype(int) % n]

    def rms(x):
        return float(np.sqrt(np.mean(x * x)))

    irms, peak = rms(current), float(np.max(np.abs(current)))
    min_comm = float(min(np.min(np.abs(ip)), np.min(np.abs(ise))))
    metrics = {
        "pin_w": float(np.mean(pin)),
        "pout_w": float(np.mean(pout)),
        "irms_a": irms,
        "ipeak_a": peak,
        "ipp_a": float(np.ptp(current)),
        "iabsavg_a": float(np.mean(np.abs(current))),
        "crest_factor": peak / max(irms, 1e-12),
        "backflow_primary_w": float(np.mean(np.maximum(-pin, 0))),
        "backflow_secondary_w": float(np.mean(np.maximum(-pout, 0))),
        "circulation_proxy_w": float(np.mean(np.abs(pin)) - abs(np.mean(pin))),
        "vcp_rms_v": rms(vcp),
        "vcp_peak_v": float(np.max(np.abs(vcp))),
        "icp_rms_a": irms if include_cp else 0.0,
        "iin_average_a": float(np.mean(iin)),
        "iin_ripple_rms_a": rms(iin - np.mean(iin)),
        "iout_average_a": float(np.mean(iout)),
        "iout_ripple_rms_a": rms(ripple_i),
        "vout_ripple_pp_v": float(np.ptp(ripple_v)),
        "comm_primary_min_abs_a": float(np.min(np.abs(ip))),
        "comm_secondary_min_abs_a": float(np.min(np.abs(ise))),
        "comm_min_abs_a": min_comm,
        "comm_energy_uj": 0.5 * config.inductance_h * min_comm**2 * 1e6,
        "primary_bridge_rms_v": rms(va),
        "secondary_referred_rms_v": rms(vb),
        "transformer_primary_va": rms(va) * irms,
        "transformer_secondary_ref_va": rms(vb) * irms,
        "max_di_dt_a_per_us": float(np.max(np.abs(vl))) / config.inductance_h / 1e6,
        "series_loss_w": config.series_resistance_ohm * irms**2 if include_cp else 0.0,
    }
    signals = {
        "va_v": va,
        "vb_referred_v": vb,
        "vl_v": vl,
        "il_a": current,
        "vcp_v": vcp,
        "iin_dc_a": iin,
        "iout_dc_a": iout,
        "iout_ripple_a": ripple_i,
        "vout_ripple_v": ripple_v,
        "qa1": qa1,
        "qa2": qa2,
        "qb1": qb1,
        "qb2": qb2,
    }
    return DABWaveform(t / config.switching_hz, signals, metrics, ep, es, ip, ise)


@dataclass(frozen=True)
class DABPoint:
    vb_v: float
    load_ratio: float
    pref_w: float
    command: TPSCommand
    ideal: DABWaveform
    with_cp: DABWaveform
    summary: dict[str, float | int | bool | str | None]


@dataclass(frozen=True)
class DABResult:
    config: DABConfig
    points: tuple[DABPoint, ...]


def analyze_dab(config: DABConfig) -> DABResult:
    config.validate()
    points = []
    full_counts = math.floor(config.tbclk_hz / config.switching_hz + 0.5)
    half_counts = full_counts / 2
    for vb in config.vb_cases_v:
        for load in config.load_ratios:
            pref = config.full_load_power(vb) * load
            command = tps_command(config, vb, pref)
            ideal = periodic_waveform(config, vb, command, include_cp=False)
            cp = periodic_waveform(config, vb, command, include_cp=True)
            row = dict(vb_v=vb, load_percent=load * 100, pref_w=pref, **asdict(command))
            row.update({"ideal_" + k: v for k, v in ideal.metrics.items()})
            row.update(cp.metrics)
            for name in ("d1", "d2", "d3"):
                d = getattr(command, name)
                row[name + "_deg"] = 180 * d
                row[name + "_count"] = math.floor(d * half_counts + 0.5)
            primary, secondary = 1 - command.d1, 1 - (command.d3 - command.d2)
            row.update(
                secondary_inner_deg=180 * (command.d3 - command.d2),
                primary_active_width_us=primary / (2 * config.switching_hz) * 1e6,
                secondary_active_width_us=secondary / (2 * config.switching_hz) * 1e6,
                primary_active_width_count=math.floor(primary * half_counts + 0.5),
                secondary_active_width_count=math.floor(secondary * half_counts + 0.5),
                ideal_headroom_w=command.pbase_w - pref,
                command_headroom_w=command.pbase_w * config.p_limit - pref,
                power_error_percent=100 * (cp.metrics["pout_w"] - pref) / pref
                if pref
                else None,
                evidence="SOFTWARE_MODEL_ONLY",
                zvs_status="UNKNOWN",
                pwm_count_convention="full-period ticks; not a device register image",
            )
            points.append(DABPoint(vb, load, pref, command, ideal, cp, row))
    return DABResult(config, tuple(points))


def save_config(config: DABConfig, path: str | Path) -> None:
    config.validate()
    Path(path).write_text(
        json.dumps(
            {"schema": "dab-tps-v1", "config": asdict(config)},
            indent=2,
            allow_nan=False,
        )
        + "\n",
        encoding="utf-8",
    )


def load_config(path: str | Path) -> DABConfig:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if data.get("schema") != "dab-tps-v1":
        raise ValueError("not a dab-tps-v1 project")
    values = data["config"]
    if set(values) != {f.name for f in fields(DABConfig)}:
        raise ValueError("DAB project configuration fields do not match schema")
    values["vb_cases_v"] = tuple(values["vb_cases_v"])
    values["load_ratios"] = tuple(values["load_ratios"])
    config = DABConfig(**values)
    config.validate()
    return config


def export_csv(result: DABResult, path: str | Path) -> None:
    with Path(path).open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(result.points[0].summary))
        writer.writeheader()
        writer.writerows(p.summary for p in result.points)

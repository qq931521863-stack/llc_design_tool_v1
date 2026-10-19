"""Time-weighted observer of the existing shared-ngspice PFC circuit.

Raw signed branch currents remain authoritative. No rectification, synthetic
triangles, uniform-sample mean or averaged-plant substitution is permitted.
"""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import asdict, dataclass, replace
from typing import Any

import numpy as np

from pfc_design.control import PFCControlLabConfig
from pfc_design.control.config import LoadModel
from power_sim.spice.pfc_closed_loop import TTPLSharedNgSpiceResult

POSITIONS = ("hfh", "hfl", "lfp", "lfn")
GATES = dict(zip(POSITIONS, ("v(g_hf_h)", "v(g_hf_l)", "v(g_lf_p)", "v(g_lf_n)")))
REQUIRED = (
    "time",
    "v(line)",
    "v(neutral)",
    "v(sw)",
    "v(bus)",
    "v(c_int)",
    "i(lboost)",
    "i(vscap)",
    "i(vsload)",
    *(f"i(vs{p})" for p in POSITIONS),
    *(f"i(vd{p})" for p in POSITIONS),
    *GATES.values(),
)


def product_mean(time: np.ndarray, a: np.ndarray, b: np.ndarray) -> float:
    """Time integral of native instantaneous products, including switch jumps.

    At a jump, interpolating I before squaring invents a ramp and breaks
    I²R == measured V*I. Event-adjacent samples bound trapezoid edge error.
    """
    return time_mean(time, a * b)


def time_mean(time: np.ndarray, values: np.ndarray) -> float:
    return float(
        np.sum(0.5 * (values[1:] + values[:-1]) * np.diff(time)) / (time[-1] - time[0])
    )


def _window(time, signals, start, end):
    inside = (time > start) & (time < end)
    out_time = np.r_[start, time[inside], end]
    out = {}
    for key, value in signals.items():
        # Internal duplicate timestamps preserve both event limits; start uses
        # the right limit and end the left limit when exactly on an event.
        left = np.interp(start, time, value)
        index = np.searchsorted(time, end, side="left")
        right = (
            value[index]
            if index < len(time) and time[index] == end
            else np.interp(end, time, value)
        )
        out[key] = np.r_[left, value[inside], right]
    return out_time, out


@dataclass(frozen=True)
class PFCSwitchedCycleResult:
    config: PFCControlLabConfig
    source_identity: str
    source_metadata: dict[str, Any]
    time_s: np.ndarray
    signals: dict[str, np.ndarray]
    full_line_cycle: bool
    convergence: dict[str, Any]
    power_factor: float
    thd_percent: float | None
    input_current_rms_a: float
    capacitor_current_rms_a: float
    input_power_w: float
    output_power_w: float
    kcl_bus_peak_error_a: float
    kcl_switch_peak_error_a: float
    circuit_netlist: str
    qualification_time_s: np.ndarray
    qualification_signals: dict[str, np.ndarray]
    switch_events: dict[str, list[dict[str, float | bool]]]

    @property
    def accepted(self) -> bool:
        return self.convergence["status"] == "CONVERGED"

    @property
    def duration_s(self) -> float:
        return float(self.time_s[-1] - self.time_s[0])

    def require_accepted(self) -> None:
        if self.source_identity != _identity(self):
            raise ValueError("switching source identity changed after observation")
        if not self.accepted:
            raise ValueError(
                "switching consumers require a converged, complete shared-ngspice line cycle; "
                + self.convergence["status"]
            )

    def voltage(self, position: str) -> np.ndarray:
        s = self.signals
        return {
            "hfh": s["v(bus)"] - s["v(sw)"],
            "hfl": s["v(sw)"],
            "lfp": s["v(bus)"] - s["v(neutral)"],
            "lfn": s["v(neutral)"],
        }[position]

    def channel_mean_square(self, position: str) -> float:
        current = self.qualification_signals[f"i(vs{position})"]
        return self.integrate_native(current * current)

    def diode_power(self, position: str) -> float:
        # A memoryless diode's V/I are nonlinear, particularly across an ideal
        # switch jump. Integrate observed instantaneous power; multiplying
        # independent linear interpolants creates a fictitious negative term.
        return self.integrate_native(self.qualification_signals[f"p(d{position})"])

    def integrate_native(self, values: np.ndarray) -> float:
        t, v = _window(
            self.qualification_time_s,
            {"value": values},
            self.time_s[0],
            self.time_s[-1],
        )
        return time_mean(t, v["value"])

    def capacitor_pwm_moments(self) -> dict[str, float]:
        """Variance decomposition by actual carrier windows (not spectral bands)."""
        t, current = self.time_s, self.signals["i(vscap)"]
        ts = 1 / self.config.power_stage.switching_frequency_hz
        interior = np.arange(np.floor(t[0] / ts) + 1, np.ceil(t[-1] / ts)) * ts
        bounds = np.r_[t[0], interior[(interior > t[0]) & (interior < t[-1])], t[-1]]
        dt = np.diff(t)
        slope = np.divide(np.diff(current), dt, out=np.zeros_like(dt), where=dt > 0)
        area = np.r_[0, np.cumsum(dt * (current[:-1] + current[1:]) / 2)]
        index = np.minimum(np.searchsorted(t, bounds, side="right") - 1, len(t) - 2)
        h = bounds - t[index]
        primitive = area[index] + current[index] * h + 0.5 * slope[index] * h * h
        weights = np.diff(bounds)
        means = np.diff(primitive) / weights
        between = float(np.dot(weights, means * means) / self.duration_s)
        total = self.capacitor_current_rms_a**2
        return {
            "pwm_interval_mean_square_a2": between,
            "within_pwm_variance_a2": max(0.0, total - between),
            "total_mean_square_a2": total,
        }

    def as_dict(self, *, include_waveforms: bool = False) -> dict[str, Any]:
        data = {
            "source": "shared_ngspice_signed_switching_cycle",
            "source_identity": self.source_identity,
            "source_config": asdict(self.config),
            "source_metadata": self.source_metadata,
            "start_time_s": float(self.time_s[0]),
            "end_time_s": float(self.time_s[-1]),
            "sample_count": len(self.time_s),
            "full_line_cycle": self.full_line_cycle,
            "convergence": self.convergence,
            "switch_events": self.switch_events,
            "weighting": "actual adaptive dt; native instantaneous squared currents and powers; event left/right limits retained; boundary powers interpolated directly",
            "power_factor": self.power_factor,
            "thd_percent": self.thd_percent,
            "thd_harmonic_orders": list(range(2, 26)),
            "thd_reference_harmonic": 1,
            "thd_scope": "line harmonics 2..25; switching-band distortion excluded",
            "input_current_rms_a": self.input_current_rms_a,
            "capacitor_current_rms_a": self.capacitor_current_rms_a,
            "input_power_w": self.input_power_w,
            "output_power_w": self.output_power_w,
            "kcl_bus_peak_error_a": self.kcl_bus_peak_error_a,
            "kcl_switch_peak_error_a": self.kcl_switch_peak_error_a,
            "channel_mean_square_a2": {
                p: self.channel_mean_square(p) for p in POSITIONS
            },
            "generic_diode_power_w": {p: self.diode_power(p) for p in POSITIONS},
            "capacitor_pwm_moments": self.capacitor_pwm_moments(),
            "diode_power_integration": "time trapezoid of observed instantaneous diode V*I; nonlinear V/I are not independently interpolated",
            "limitations": [
                "Generic circuit Ron/Roff and diode model; not vendor parasitics or hardware evidence.",
                "No Coss/Qrr/ringing model; selected-device loss screening does not rerun the circuit at that device temperature.",
                "Accepted means periodicity/regulation/KCL screening in this model, not physical stability proof.",
            ],
        }
        if include_waveforms:
            data.update(
                time_s=self.time_s.tolist(),
                signals={key: value.tolist() for key, value in self.signals.items()},
                circuit_netlist=self.circuit_netlist,
                qualification_time_s=self.qualification_time_s.tolist(),
                qualification_signals={
                    key: value.tolist()
                    for key, value in self.qualification_signals.items()
                },
            )
        return data


def _identity(source: PFCSwitchedCycleResult) -> str:
    contract = {
        "config": asdict(source.config),
        "metadata": source.source_metadata,
        "convergence": source.convergence,
        "full_line_cycle": source.full_line_cycle,
        "metrics": [
            source.power_factor,
            source.thd_percent,
            source.input_current_rms_a,
            source.capacitor_current_rms_a,
            source.input_power_w,
            source.output_power_w,
            source.kcl_bus_peak_error_a,
            source.kcl_switch_peak_error_a,
        ],
    }
    digest = hashlib.sha256(json.dumps(contract, sort_keys=True, default=str).encode())
    digest.update(source.circuit_netlist.encode())
    digest.update(source.time_s.tobytes())
    digest.update(json.dumps(source.switch_events, sort_keys=True).encode())
    digest.update(source.qualification_time_s.tobytes())
    for key in sorted(source.qualification_signals):
        digest.update(key.encode())
        digest.update(source.qualification_signals[key].tobytes())
    for key in sorted(source.signals):
        digest.update(key.encode())
        digest.update(source.signals[key].tobytes())
    return digest.hexdigest()


def observe_switched_cycle(result: TTPLSharedNgSpiceResult) -> PFCSwitchedCycleResult:
    """Observe one period; retain the three native periods proving qualification."""
    cfg = result.config
    if cfg is None or result.metadata.get("source_config") != asdict(cfg):
        raise ValueError(
            "switching source configuration identity is missing or inconsistent"
        )
    if not result.metadata.get("exact_hz_match_checked") or result.metadata.get(
        "controller_re_discretized"
    ):
        raise ValueError("switching exact-controller identity was not verified")
    absent = [name for name in REQUIRED if name not in result.vectors]
    if absent:
        raise ValueError(
            "switching branch observations are missing: " + ", ".join(absent)
        )
    t = np.asarray(result.vectors["time"], dtype=float)
    if (
        t.ndim != 1
        or len(t) < 4
        or not np.all(np.isfinite(t))
        or np.any(np.diff(t) < 0)
    ):
        raise ValueError("invalid switching time vector")
    raw = {
        key: np.asarray(result.vectors[key], dtype=float)
        for key in REQUIRED
        if key != "time"
    }
    if any(a.shape != t.shape or not np.all(np.isfinite(a)) for a in raw.values()):
        raise ValueError("switching branch vectors must be aligned and finite")
    if t[-1] <= t[0]:
        raise ValueError("switching source has no positive integration duration")
    duration = float(result.metadata["simulation"]["duration_s"])
    native_stop = float(result.metadata.get("native_stop_time_s", duration))
    if native_stop > duration or duration - native_stop > np.spacing(duration):
        raise ValueError("native/requested horizon exceeds the declared one-ULP budget")
    if (
        t[-1] != native_stop
        or t[-1] > duration
        or any(
            any(
                word in message.lower()
                for word in (
                    "timestep too small",
                    "simulation(s) aborted",
                    "doanalyses:",
                )
            )
            for message in result.simulator_messages
        )
    ):
        raise ValueError(
            "switching transient failed or did not reach requested duration"
        )
    circuit = result.metadata["circuit_config"]
    if float(circuit["diode_cjo_f"]) != 0:
        raise ValueError(
            "switching qualification currently requires the declared memoryless diode model"
        )
    if result.metadata["load_model"] != LoadModel(cfg.power_stage.load_model).value:
        raise ValueError("switching load identity differs from configured source")
    period = 1 / cfg.power_stage.line_frequency_hz
    full = bool(t[-1] - t[0] >= period * (1 - 1e-10))
    start, end = max(float(t[0]), float(t[-1] - period)), float(t[-1])
    # Keep one native point preceding the three-period window for boundary
    # interpolation and gate-event side information. Never deduplicate events.
    first = max(0, int(np.searchsorted(t, end - 3 * period, side="left")) - 1)
    t = t[first:].copy()
    raw = {key: value[first:].copy() for key, value in raw.items()}

    def voltage(p):
        return {
            "hfh": raw["v(bus)"] - raw["v(sw)"],
            "hfl": raw["v(sw)"],
            "lfp": raw["v(bus)"] - raw["v(neutral)"],
            "lfn": raw["v(neutral)"],
        }[p]

    for p in POSITIONS:
        raw[f"p(d{p})"] = -voltage(p) * raw[f"i(vd{p})"]

    def mean(values):
        tt, vv = _window(t, {"x": values}, start, end)
        return time_mean(tt, vv["x"])

    time, signals = _window(t, raw, start, end)
    # Qualification covers every retained cycle used for periodicity, not just
    # the final consumer period. Check only native states, never interpolated ones.
    native = (t >= max(t[0], end - 3 * period)) & (t <= end)
    vac, current, cap = (
        raw["v(line)"] - raw["v(neutral)"],
        raw["i(lboost)"],
        raw["i(vscap)"],
    )
    irms, vrms = np.sqrt(mean(current * current)), np.sqrt(mean(vac * vac))
    pin, pout = mean(vac * current), mean(raw["v(bus)"] * raw["i(vsload)"])
    pf = float(pin / (vrms * irms)) if vrms * irms > 0 else 0.0
    coeff = []
    for k in range(1, 26):
        tt, vv = _window(
            t, {"x": current * np.exp(-2j * np.pi * k * t / period)}, start, end
        )
        coeff.append(
            np.sum(0.5 * (vv["x"][1:] + vv["x"][:-1]) * np.diff(tt)) / (end - start)
        )
    coeff = np.array(coeff)
    thd = (
        float(100 * np.linalg.norm(coeff[1:]) / abs(coeff[0]))
        if full and abs(coeff[0]) > 1e-12
        else None
    )
    bus_residual = (
        -raw["i(vshfh)"]
        - raw["i(vslfp)"]
        + raw["i(vdhfh)"]
        + raw["i(vdlfp)"]
        - cap
        - raw["i(vsload)"]
    )
    bleeder = float(circuit["bleeder_ohm"])
    sw_residual = (
        current
        + raw["i(vshfh)"]
        + raw["i(vdhfl)"]
        - raw["i(vshfl)"]
        - raw["i(vdhfh)"]
        - raw["v(sw)"] / bleeder
    )
    bus_error, sw_error = (
        float(max(abs(bus_residual[native]))),
        float(max(abs(sw_residual[native]))),
    )
    tolerance = 1e-6 + 0.001 * max(
        float(max(abs(current[native]))), float(max(abs(raw["i(vsload)"][native]))), 1.0
    )
    scenario = result.metadata["scenario"]
    reference = float(scenario["bus_reference_v"])
    if (
        scenario.get("bus_reference_final_v") is not None
        and scenario.get("reference_step_time_s") is not None
        and end >= scenario["reference_step_time_s"]
    ):
        reference = float(scenario["bus_reference_final_v"])
    convergence = {
        "status": "INSUFFICIENT_CYCLES",
        "checked_cycles": 0,
        "current_waveform_relative_error": None,
        "bus_waveform_relative_error": None,
        "current_tolerance": 0.01,
        "bus_tolerance": 0.005,
        "bus_command_relative_error": abs(mean(raw["v(bus)"]) - reference) / reference,
        "bus_command_tolerance": 0.05,
        "kcl_peak_tolerance_a": tolerance,
    }
    if t[-1] - t[0] >= 3 * period * (1 - 1e-10):
        n = max(
            4096, int(np.ceil(period * cfg.power_stage.switching_frequency_hz * 32))
        )
        phase = (np.arange(n) + 0.5) / n * period
        ci = np.array(
            [
                np.interp(end - offset * period + phase, t, current)
                for offset in (3, 2, 1)
            ]
        )
        cv = np.array(
            [
                np.interp(end - offset * period + phase, t, raw["v(bus)"])
                for offset in (3, 2, 1)
            ]
        )
        ie = float(
            max(np.sqrt(np.mean(np.diff(ci, axis=0) ** 2, axis=1)))
            / max(float(max(np.sqrt(np.mean(ci**2, axis=1)))), 1e-9)
        )
        ve = float(max(np.sqrt(np.mean(np.diff(cv, axis=0) ** 2, axis=1))) / reference)
        convergence.update(
            checked_cycles=3,
            current_waveform_relative_error=ie,
            bus_waveform_relative_error=ve,
        )
        convergence["status"] = (
            "CONVERGED"
            if ie <= 0.01
            and ve <= 0.005
            and convergence["bus_command_relative_error"] <= 0.05
            else "NOT_CONVERGED"
        )
    failures, channel_error, events = {}, 0.0, {}
    edge_indices, ambiguous_edges = set(), []
    for p in POSITIONS:
        vd, diode, channel = voltage(p), raw[f"i(vd{p})"], raw[f"i(vs{p})"]
        expected_v = 0.0258649258 * np.log1p(
            np.maximum(diode, 0) / float(circuit["diode_is_a"])
        ) + diode * float(circuit["diode_rs_ohm"])
        bad = (diode < -1e-6) | (
            (diode > 1e-5) & (abs(-vd - expected_v) > 0.002 + 0.001 * abs(expected_v))
        )
        failures[p] = int(np.count_nonzero(bad[native]))
        gate = raw[GATES[p]] > 2.5
        channel_error = max(
            channel_error,
            float(
                max(
                    abs(
                        (
                            channel
                            - vd
                            / np.where(
                                gate,
                                circuit["switch_ron_ohm"],
                                circuit["switch_roff_ohm"],
                            )
                        )[native]
                    )
                )
            ),
        )
        changes = np.flatnonzero(gate[1:] != gate[:-1])
        events[p] = []
        for index in changes:
            event_time = 0.5 * (t[index] + t[index + 1])
            if t[index] < start < t[index + 1] or t[index] < end < t[index + 1]:
                ambiguous_edges.append(
                    {
                        "position": p,
                        "left_time_s": float(t[index]),
                        "right_time_s": float(t[index + 1]),
                    }
                )
            if start <= event_time < end:
                edge_indices.add(int(index))
                events[p].append(
                    {
                        "time_s": float(event_time),
                        "left_time_s": float(t[index]),
                        "right_time_s": float(t[index + 1]),
                        "turn_on": bool(gate[index + 1]),
                        "channel_left_a": float(channel[index]),
                        "channel_right_a": float(channel[index + 1]),
                        "diode_left_a": float(diode[index]),
                        "diode_right_a": float(diode[index + 1]),
                        "voltage_left_v": float(vd[index]),
                        "voltage_right_v": float(vd[index + 1]),
                    }
                )
    esr = max(float(circuit["bus_cap_esr_ohm"]), 1e-6)
    esr_error = float(max(abs(((raw["v(bus)"] - raw["v(c_int)"]) / esr - cap)[native])))
    cap_de = (
        0.5
        * float(circuit["bus_capacitance_f"])
        * (signals["v(c_int)"][-1] ** 2 - signals["v(c_int)"][0] ** 2)
        / (end - start)
    )
    l_de = (
        0.5
        * float(circuit["boost_inductance_h"])
        * (signals["i(lboost)"][-1] ** 2 - signals["i(lboost)"][0] ** 2)
        / (end - start)
    )
    loss = (
        mean(current * current) * max(float(circuit["boost_dcr_ohm"]), 1e-6)
        + mean(cap * cap) * esr
    )
    for p in POSITIONS:
        loss += mean(voltage(p) * raw[f"i(vs{p})"]) + mean(raw[f"p(d{p})"])
    loss += mean(raw["v(sw)"] ** 2 + raw["v(neutral)"] ** 2) / bleeder
    residual = pin - pout - loss - cap_de - l_de
    edge_gap = max(
        (
            e["right_time_s"] - e["left_time_s"]
            for entries in events.values()
            for e in entries
        ),
        default=0.0,
    )
    neutral_residual = (
        current
        + raw["i(vslfn)"]
        + raw["i(vdlfp)"]
        - raw["i(vslfp)"]
        - raw["i(vdlfn)"]
        + raw["v(neutral)"] / bleeder
    )
    neutral_error = float(max(abs(neutral_residual[native])))
    expected_load = (
        float(circuit["output_power_w"])
        / np.maximum(raw["v(bus)"], float(circuit["constant_power_floor_v"]))
        if result.metadata["load_model"] == "constant_power"
        else raw["v(bus)"]
        * float(circuit["output_power_w"])
        / float(circuit["bus_voltage_v"]) ** 2
    )
    load_error = float(max(abs((raw["i(vsload)"] - expected_load)[native])))
    edge_index = np.array(sorted(edge_indices), dtype=int)
    quantities = {"capacitor_current_square_a2": cap * cap}
    quantities.update(
        {f"{p}_channel_square_a2": raw[f"i(vs{p})"] ** 2 for p in POSITIONS}
    )
    quantities.update({f"{p}_diode_power_w": raw[f"p(d{p})"] for p in POSITIONS})
    edge_bounds, edge_qualified = {}, True
    for name, values in quantities.items():
        bound = float(
            np.sum(
                0.5
                * (t[edge_index + 1] - t[edge_index])
                * abs(values[edge_index + 1] - values[edge_index])
            )
            / (end - start)
        )
        scale = abs(mean(values))
        budget = (
            1e-4 + 0.02 * scale if name.endswith("_power_w") else 1e-6 + 0.002 * scale
        )
        edge_bounds[name] = {
            "absolute_bound": bound,
            "mean_magnitude": scale,
            "budget": budget,
        }
        edge_qualified = edge_qualified and bound <= budget
    convergence.update(
        ambiguous_boundary_events=ambiguous_edges,
        gate_edge_area_bounds=edge_bounds,
        gate_edge_bound_assumption="single step inside each measured bracket; excludes hidden peaks and smooth/solver errors; separate refinement evidence required",
        gate_edge_max_bracket_s=edge_gap,
        kcl_neutral_peak_error_a=neutral_error,
        load_law_peak_error_a=load_error,
        diode_constitutive_invalid_samples=failures,
        switch_constitutive_peak_error_a=channel_error,
        capacitor_esr_peak_error_a=esr_error,
        power_balance_residual_w=residual,
        power_balance_tolerance_w=0.01 * max(abs(pin), abs(pout), 1.0),
        observed_event_guard_s=result.metadata.get("event_observation_guard_s"),
    )
    if max(bus_error, sw_error, neutral_error) > tolerance:
        convergence["status"] = "INCONSISTENT_BRANCH_KCL"
    if any(failures.values()) or max(channel_error, esr_error, load_error) > tolerance:
        convergence["status"] = "INCONSISTENT_DEVICE_LAW"
    elif abs(residual) > convergence["power_balance_tolerance_w"]:
        convergence["status"] = "INCONSISTENT_ENERGY_BALANCE"
    elif ambiguous_edges:
        convergence["status"] = "AMBIGUOUS_BOUNDARY_EVENT"
    elif not result.metadata.get("event_observation_guard_s") or not edge_qualified:
        convergence["status"] = "UNQUALIFIED_EVENT_SAMPLING"
    for values in (*raw.values(), *signals.values(), t, time):
        values.setflags(write=False)
    source = PFCSwitchedCycleResult(
        cfg,
        "",
        copy.deepcopy(result.metadata),
        time,
        signals,
        full,
        convergence,
        pf,
        thd,
        float(irms),
        float(np.sqrt(mean(cap * cap))),
        pin,
        pout,
        bus_error,
        sw_error,
        result.circuit_netlist,
        t,
        raw,
        events,
    )
    return replace(source, source_identity=_identity(source))

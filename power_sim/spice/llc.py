"""LLC design -> ideal switching CircuitIR.

V1 intentionally targets correlation with the existing Python switched solver:
ideal voltage-controlled primary switches, coupled-inductor transformer,
full-wave diode rectifier and the designed output capacitor/load.  Vendor MOSFET
models, nonlinear Coss, Qrr and synchronous-rectifier control are later fidelity
layers and are not silently implied by this model.
"""
from __future__ import annotations

from dataclasses import dataclass
import math

from llc_design.core.spec import LLCDesignSpec, PrimaryTopology
from llc_design.core.tank import TankDesign

from .ir import CircuitIR, ElementKind, SpiceElement


@dataclass(frozen=True)
class LLCSpiceConfig:
    switching_frequency_hz: float
    bus_voltage_v: float
    load_fraction: float = 1.0
    gate_drive_mode: str = "pulse"  # pulse | external
    gate_high_v: float = 5.0
    switch_ron_ohm: float = 1e-3
    switch_roff_ohm: float = 1e9
    transformer_coupling: float = 0.999999
    diode_is_a: float = 1e-3
    diode_rs_ohm: float = 1e-3
    initial_output_v: float | None = None

    def validate(self, spec: LLCDesignSpec) -> None:
        if self.switching_frequency_hz <= 0.0:
            raise ValueError("switching_frequency_hz must be positive")
        if not (spec.minimum_frequency_hz <= self.switching_frequency_hz <= spec.maximum_frequency_hz):
            raise ValueError("switching frequency is outside LLC design limits")
        if self.bus_voltage_v <= 0.0:
            raise ValueError("bus_voltage_v must be positive")
        if self.load_fraction <= 0.0:
            raise ValueError("load_fraction must be positive")
        if self.gate_drive_mode not in {"pulse", "external"}:
            raise ValueError("gate_drive_mode must be 'pulse' or 'external'")
        if self.gate_high_v <= 0.0:
            raise ValueError("gate_high_v must be positive")
        if self.switch_ron_ohm <= 0.0 or self.switch_roff_ohm <= self.switch_ron_ohm:
            raise ValueError("invalid ideal-switch Ron/Roff")
        if not (0.0 < self.transformer_coupling <= 1.0):
            raise ValueError("transformer coupling must be within (0, 1]")


def _s(value: float) -> str:
    return f"{float(value):.12e}"


def _pulse(value_high: float, frequency_hz: float, deadtime_s: float, *, half_cycle_shift: bool) -> str:
    period = 1.0 / float(frequency_hz)
    dead = max(0.0, min(float(deadtime_s), 0.45 * period))
    rise = max(min(dead / 20.0 if dead > 0.0 else period / 5000.0, period / 1000.0), 1e-9)
    fall = rise
    width = max(period / 2.0 - dead, rise)
    delay = dead / 2.0 + (period / 2.0 if half_cycle_shift else 0.0)
    return (
        f"PULSE(0 {_s(value_high)} {_s(delay)} {_s(rise)} {_s(fall)} "
        f"{_s(width)} {_s(period)})"
    )


def _gate_source(refdes: str, node: str, config: LLCSpiceConfig, spec: LLCDesignSpec, *, shifted: bool) -> SpiceElement:
    if config.gate_drive_mode == "external":
        # shared-ngspice calls GetVSRCData for sources marked EXTERNAL.
        expression = "DC 0 EXTERNAL"
    else:
        expression = _pulse(
            config.gate_high_v,
            config.switching_frequency_hz,
            spec.primary_deadtime_s,
            half_cycle_shift=shifted,
        )
    return SpiceElement(refdes, ElementKind.VOLTAGE_SOURCE, (node, "0"), expression)


def build_ideal_llc_circuit(spec: LLCDesignSpec, tank: TankDesign, config: LLCSpiceConfig) -> CircuitIR:
    """Build the first ngspice LLC correlation model.

    The first production milestone deliberately supports FULL_BRIDGE only.  A
    half-bridge needs an explicit split-bus/midpoint definition and must not be
    approximated by silently halving the source voltage.
    """
    spec.validate()
    config.validate(spec)
    if PrimaryTopology(spec.primary_topology) != PrimaryTopology.FULL_BRIDGE:
        raise NotImplementedError("ngspice LLC V1 currently supports FULL_BRIDGE primary only")

    n = float(spec.turns_ratio)
    if n <= 0.0:
        raise ValueError("LLC turns ratio must be positive")
    secondary_l_h = float(tank.lm_h) / (n * n)
    rated_load_ohm = spec.vout_v * spec.vout_v / spec.pout_w
    load_ohm = rated_load_ohm / float(config.load_fraction)
    initial_vout = spec.vout_v if config.initial_output_v is None else float(config.initial_output_v)

    circuit = CircuitIR(
        name=(
            f"Power Design Toolkit LLC ideal SPICE | {config.bus_voltage_v:.6g} V | "
            f"{config.load_fraction*100:.3g}% load | {config.switching_frequency_hz:.8g} Hz"
        ),
        metadata={
            "model_level": "ideal_switching_correlation",
            "topology": "FULL_BRIDGE_LLC_FULL_BRIDGE_DIODE_RECTIFIER",
            "vbus_v": config.bus_voltage_v,
            "load_fraction": config.load_fraction,
            "switching_frequency_hz": config.switching_frequency_hz,
            "lr_h": tank.lr_h,
            "cr_f": tank.cr_f,
            "lm_h": tank.lm_h,
            "turns_ratio": n,
        },
    )

    circuit.add(SpiceElement("VBUS", ElementKind.VOLTAGE_SOURCE, ("BUS", "0"), f"DC {_s(config.bus_voltage_v)}"))
    circuit.extend(
        [
            _gate_source("VGAH", "G_AH", config, spec, shifted=False),
            _gate_source("VGAL", "G_AL", config, spec, shifted=True),
            _gate_source("VGBH", "G_BH", config, spec, shifted=True),
            _gate_source("VGBL", "G_BL", config, spec, shifted=False),
        ]
    )

    switch_model = "SWMOD"
    circuit.extend(
        [
            SpiceElement("SAH", ElementKind.SWITCH, ("BUS", "A", "G_AH", "0"), model=switch_model),
            SpiceElement("SAL", ElementKind.SWITCH, ("A", "0", "G_AL", "0"), model=switch_model),
            SpiceElement("SBH", ElementKind.SWITCH, ("BUS", "B", "G_BH", "0"), model=switch_model),
            SpiceElement("SBL", ElementKind.SWITCH, ("B", "0", "G_BL", "0"), model=switch_model),
            SpiceElement("LR", ElementKind.INDUCTOR, ("A", "N_LR"), _s(tank.lr_h)),
            SpiceElement("CR", ElementKind.CAPACITOR, ("N_LR", "PRI"), _s(tank.cr_f)),
            SpiceElement("LPRI", ElementKind.INDUCTOR, ("PRI", "B"), _s(tank.lm_h)),
            SpiceElement("LSEC", ElementKind.INDUCTOR, ("SEC_P", "SEC_N"), _s(secondary_l_h)),
            SpiceElement("KTX", ElementKind.COUPLING, ("LPRI", "LSEC"), _s(config.transformer_coupling)),
            SpiceElement("D1", ElementKind.DIODE, ("SEC_P", "OUT"), model="DRECT"),
            SpiceElement("D2", ElementKind.DIODE, ("SEC_N", "OUT"), model="DRECT"),
            SpiceElement("D3", ElementKind.DIODE, ("0", "SEC_P"), model="DRECT"),
            SpiceElement("D4", ElementKind.DIODE, ("0", "SEC_N"), model="DRECT"),
            SpiceElement("RCO", ElementKind.RESISTOR, ("OUT", "CO_INT"), _s(max(spec.output_cap_esr_ohm, 1e-9))),
            SpiceElement("CO", ElementKind.CAPACITOR, ("CO_INT", "0"), _s(spec.output_capacitance_f)),
            SpiceElement("RLOAD", ElementKind.RESISTOR, ("OUT", "0"), _s(load_ohm)),
        ]
    )

    circuit.models.extend(
        [
            (
                f".model {switch_model} SW(Ron={_s(config.switch_ron_ohm)} "
                f"Roff={_s(config.switch_roff_ohm)} Vt=2.5 Vh=0.1)"
            ),
            f".model DRECT D(Is={_s(config.diode_is_a)} N=1 Rs={_s(config.diode_rs_ohm)} Cjo=1p)",
        ]
    )
    circuit.directives.extend(
        [
            ".options method=gear reltol=1e-5 abstol=1e-9 vntol=1e-6",
            f".ic V(OUT)={_s(initial_vout)} V(CO_INT)={_s(initial_vout)}",
        ]
    )
    circuit.save_vectors.extend(
        [
            "time",
            "v(out)",
            "v(a)",
            "v(b)",
            "v(pri)",
            "v(sec_p)",
            "v(sec_n)",
            "v(g_ah)",
            "v(g_al)",
            "v(g_bh)",
            "v(g_bl)",
            "i(lr)",
            "i(lpri)",
            "i(lsec)",
            "i(vbus)",
        ]
    )
    return circuit


def default_llc_transient_window(config: LLCSpiceConfig, *, cycles: int = 400, samples_per_cycle: int = 200) -> tuple[float, float]:
    if cycles < 1 or samples_per_cycle < 20:
        raise ValueError("cycles must be >=1 and samples_per_cycle must be >=20")
    period = 1.0 / config.switching_frequency_hz
    return period * cycles, period / samples_per_cycle


__all__ = ["LLCSpiceConfig", "build_ideal_llc_circuit", "default_llc_transient_window"]

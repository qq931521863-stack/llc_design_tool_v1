"""Resonant, output and DC-link capacitor calculations."""

from __future__ import annotations

from dataclasses import dataclass
import math

from ..core.bus_capacitor import (hold_end_voltage_v, hold_time_s,
                                  required_capacitance_f)
from ..core.operating_point import LLCOperatingPoint
from ..core.spec import LLCDesignSpec, PrimaryTopology
from ..core.tank import TankDesign
from ..core.waveform import OutputRippleResult, output_capacitor_ripple


@dataclass(frozen=True)
class ResonantCapacitorResult:
    current_rms_a: float
    voltage_rms_v: float
    voltage_dc_bias_v: float
    voltage_peak_v: float
    loss_w: float
    voltage_margin: float
    current_margin: float


@dataclass(frozen=True)
class BusCapacitorResult:
    requested_hold_time_s: float
    predicted_end_voltage_v: float
    hold_time_to_limit_s: float
    required_capacitance_f: float
    installed_capacitance_f: float
    energy_start_j: float
    energy_end_j: float


def resonant_capacitor_result(spec: LLCDesignSpec, tank: TankDesign,
                              op: LLCOperatingPoint) -> ResonantCapacitorResult:
    xc = 1.0 / (2.0 * math.pi * op.switching_frequency_hz * tank.cr_f)
    vrms = op.resonant_current_rms_a * xc
    ac_peak = math.sqrt(2.0) * vrms
    # The series resonant capacitor blocks the bridge DC component: half bridge
    # sees a Vbus/2 DC bias, full bridge (symmetric square wave) sees ~0.
    dc_bias = 0.0 if spec.primary_topology == PrimaryTopology.FULL_BRIDGE else 0.5 * op.vbus_v
    vpk = dc_bias + ac_peak
    loss = op.resonant_current_rms_a**2 * spec.resonant_cap_esr_ohm
    allowed_v = spec.resonant_cap_voltage_rating_v * spec.capacitor_voltage_derating
    return ResonantCapacitorResult(
        current_rms_a=op.resonant_current_rms_a,
        voltage_rms_v=vrms, voltage_dc_bias_v=dc_bias, voltage_peak_v=vpk,
        loss_w=loss,
        voltage_margin=allowed_v / max(vpk, 1e-9),
        current_margin=spec.resonant_cap_rms_current_rating_a /
                       max(op.resonant_current_rms_a, 1e-9),
    )


def output_capacitor_result(spec: LLCDesignSpec,
                            op: LLCOperatingPoint) -> OutputRippleResult:
    return output_capacitor_ripple(
        output_current_a=op.output_current_a,
        switching_frequency_hz=op.switching_frequency_hz,
        capacitance_f=spec.output_capacitance_f,
        esr_ohm=spec.output_cap_esr_ohm,
        ripple_limit_vpp=spec.output_ripple_limit_vpp,
    )


def bus_capacitor_result(spec: LLCDesignSpec,
                         estimated_input_power_w: float | None = None) -> BusCapacitorResult:
    pin = estimated_input_power_w or spec.pout_w / spec.efficiency_assumption
    vend = hold_end_voltage_v(spec.bus_capacitance_f, spec.vbus_nom_v,
                             pin, spec.requested_hold_time_s)
    thold = hold_time_s(spec.bus_capacitance_f, spec.vbus_nom_v,
                        spec.vbus_hold_end_v, pin)
    creq = required_capacitance_f(spec.vbus_nom_v, spec.vbus_hold_end_v,
                                 pin, spec.requested_hold_time_s)
    return BusCapacitorResult(
        requested_hold_time_s=spec.requested_hold_time_s,
        predicted_end_voltage_v=vend,
        hold_time_to_limit_s=thold,
        required_capacitance_f=creq,
        installed_capacitance_f=spec.bus_capacitance_f,
        energy_start_j=0.5 * spec.bus_capacitance_f * spec.vbus_nom_v**2,
        energy_end_j=0.5 * spec.bus_capacitance_f * spec.vbus_hold_end_v**2,
    )


def capacitor_results_from_waveform(spec: LLCDesignSpec, op: LLCOperatingPoint,
                                    waveform, *, td_converged: bool | None = None
                                    ) -> tuple[ResonantCapacitorResult, OutputRippleResult]:
    """Constant-ESR losses and stress from accepted TD current/voltage arrays.

    The ideal switched solver uses a centered bridge voltage and AC Cr state.
    Add the physical half-bridge Vbus/2 bias exactly once. Output ripple uses
    the actual internal/terminal voltage states, not a sinusoidal estimate.
    """
    import numpy as np

    from ..dynamics.periodic_samples import periodic_samples

    samples = periodic_samples(waveform, {"i_resonant": "A", "v_resonant_cap": "V",
        "i_output_cap": "A", "v_output_cap_internal": "V", "v_output": "V"}, td_converged)
    values = samples.signals
    ir, ico = values["i_resonant"], values["i_output_cap"]
    vcr = values["v_resonant_cap"]
    bias = 0.0 if spec.primary_topology == PrimaryTopology.FULL_BRIDGE else 0.5 * op.vbus_v
    physical_vcr = vcr + bias
    ir_rms = float(np.sqrt(np.mean(ir**2)))
    cr_peak = float(np.max(np.abs(physical_vcr)))
    resonant = ResonantCapacitorResult(
        current_rms_a=ir_rms,
        voltage_rms_v=float(np.sqrt(np.mean((vcr - np.mean(vcr))**2))),
        voltage_dc_bias_v=float(np.mean(physical_vcr)), voltage_peak_v=cr_peak,
        loss_w=ir_rms**2 * spec.resonant_cap_esr_ohm,
        voltage_margin=spec.resonant_cap_voltage_rating_v * spec.capacitor_voltage_derating / max(cr_peak, 1e-9),
        current_margin=spec.resonant_cap_rms_current_rating_a / max(ir_rms, 1e-9),
    )
    internal_pp = float(np.ptp(values["v_output_cap_internal"]))
    esr_pp = float(np.ptp(spec.output_cap_esr_ohm * ico))
    # The charge state is C*Vc. Retain the existing conservative sizing budget,
    # without pretending that changing C leaves the solved waveform unchanged.
    charge_pp = spec.output_capacitance_f * internal_pp
    budget = spec.output_ripple_limit_vpp - esr_pp
    ico_rms = float(np.sqrt(np.mean(ico**2)))
    output = OutputRippleResult(
        capacitor_current_rms_a=ico_rms, capacitor_current_peak_a=float(np.max(np.abs(ico))),
        charge_pp_c=charge_pp, capacitive_ripple_vpp=internal_pp, esr_ripple_vpp=esr_pp,
        total_ripple_vpp=float(np.ptp(values["v_output"])),
        capacitor_loss_w=ico_rms**2 * spec.output_cap_esr_ohm,
        required_capacitance_f=math.inf if budget <= 0 else charge_pp / budget,
    )
    return resonant, output

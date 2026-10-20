"""Small-signal nested-loop analysis for three-phase Vienna PFC."""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

from pfc_design.control.analysis import _margin_screen

from llc_design.control.digital_loop import (
    DigitalTransferFunction,
    PIControllerConfig, PIFControllerConfig,
    StabilityMargins,
    calculate_stability_margins,
)
from pfc_design.control.config import LoadModel
from pfc_design.control.sensing import (
    SenseChainSummary,
    SenseFrequencyResponse,
    sense_frequency_response,
    summarize_sense_chain,
)

from .config import ViennaControlLabConfig

FloatArray = NDArray[np.float64]
ComplexArray = NDArray[np.complex128]


@dataclass(frozen=True)
class ViennaLoopResult:
    name: str
    controller: DigitalTransferFunction
    responses: dict[str, ComplexArray]
    margins: StabilityMargins
    likely_stable: bool
    frequencies_hz: FloatArray
    nyquist_hz: float
    margin_screen: str
    closed_loop_stability: str = "NOT_PROVEN"


@dataclass(frozen=True)
class ViennaControlLabAnalysis:
    config: ViennaControlLabConfig
    frequencies_hz: FloatArray
    current_loop: ViennaLoopResult
    voltage_loop: ViennaLoopResult
    balance_loop: ViennaLoopResult
    current_sense_response: SenseFrequencyResponse
    phase_voltage_sense_response: SenseFrequencyResponse
    split_bus_sense_response: SenseFrequencyResponse
    current_sense_summary: SenseChainSummary
    split_bus_sense_summary: SenseChainSummary
    warnings: tuple[str, ...]
    sensing_frequencies_hz: FloatArray

    def summary_dict(self) -> dict:
        from dataclasses import asdict
        data = {"model": "Vienna ABC averaged loops; auxiliary midpoint gain approximation",
                "closed_loop_stability": "NOT_PROVEN", "warnings": list(self.warnings),
                "requested_band_hz": [self.config.frequency_start_hz, self.config.frequency_stop_hz]}
        for name in ("current", "voltage", "balance"):
            loop = getattr(self, name + "_loop")
            data[name] = {"margin_screen": loop.margin_screen,
                          "closed_loop_stability": loop.closed_loop_stability,
                          "actual_band_hz": [float(loop.frequencies_hz[0]), float(loop.frequencies_hz[-1])],
                          "nyquist_hz_exclusive": loop.nyquist_hz,
                          "sample_count": len(loop.frequencies_hz), "margins": asdict(loop.margins)}
        return data


def _zoh(f: FloatArray, ts: float) -> ComplexArray:
    return np.sinc(f*ts)*np.exp(-1j*math.pi*f*ts)


def _stable(m: StabilityMargins) -> bool:
    pm = m.phase_margin_deg
    gm = m.gain_margin_db
    values = (pm, gm, *m.phase_margins_deg, *m.gain_margins_db)
    return (pm is not None and gm is not None and m.critical_gain_crossover_hz is not None
            and all(value is not None and math.isfinite(value) and value > 0 for value in values))


def build_vienna_control_lab_analysis(config: ViennaControlLabConfig) -> ViennaControlLabAnalysis:
    config.validate()
    stage, fw = config.power_stage, config.firmware
    f = config.loop_frequencies_hz("current")
    s = 1j*2*math.pi*f
    hi = sense_frequency_response(config.phase_current_sense, f)


    ci = config.current_controller.transfer_function(); ci_r = ci.frequency_response(f)
    current_plant = (0.5*stage.bus_voltage_v)/(stage.boost_inductance_h*s + stage.phase_series_resistance_ohm)
    pwm_i = _zoh(f, 1/fw.current_loop_rate_hz)*np.exp(-1j*2*math.pi*f*(fw.current_computation_delay_s+fw.pwm_update_delay_s))
    forward_i = ci_r*pwm_i*current_plant
    open_i = forward_i*hi.total
    ti_actual = forward_i/(1+open_i)
    current_responses = {
        "plant_gid": current_plant, "controller_ci": ci_r, "pwm_zoh": pwm_i,
        "sense_hi": hi.total, "sense_hi_analog": hi.calibrated_analog,
        "forward_current": forward_i, "open_current": open_i,
        "closed_current_actual": ti_actual, "sensitivity_current":1/(1+open_i),
    }
    mi = calculate_stability_margins(f, open_i)

    fi = f
    f = config.loop_frequencies_hz("voltage")
    s = 1j*2*math.pi*f
    # Evaluate the same closed-current model on this loop's actual band.
    hi_outer = sense_frequency_response(config.phase_current_sense, f)
    pi_outer = (0.5*stage.bus_voltage_v)/(stage.boost_inductance_h*s + stage.phase_series_resistance_ohm)
    pwm_outer = _zoh(f, 1/fw.current_loop_rate_hz)*np.exp(-s*(fw.current_computation_delay_s+fw.pwm_update_delay_s))
    forward_outer = ci.frequency_response(f)*pwm_outer*pi_outer
    ti_actual = forward_outer/(1+forward_outer*hi_outer.total)
    hvdc = sense_frequency_response(config.split_bus_sense, f)
    cv = config.voltage_controller.transfer_function(); cv_r=cv.frequency_response(f)
    ceq = stage.series_bus_capacitance_f
    # Average energy model with copper loss and stored inductor energy.
    # dPconv/dG = 3*Vrms^2*(1-2*R*G0-L*G0*s).
    # The -s term is the boost energy-transfer right-half-plane zero.
    g0 = stage.input_conductance_a_per_v
    slope = (2*stage.output_power_w/stage.bus_voltage_v
             if stage.load_model == LoadModel.RESISTIVE else 0.0)
    gvg = (3*stage.phase_rms_v**2*(1-2*stage.phase_series_resistance_ohm*g0
                                 -stage.boost_inductance_h*g0*s)
           /(stage.bus_voltage_v*ceq*s+slope))
    pwm_v = _zoh(f,1/fw.voltage_loop_rate_hz)*np.exp(-1j*2*math.pi*f*fw.voltage_computation_delay_s)
    forward_v = cv_r*pwm_v*ti_actual*gvg
    open_v = forward_v*hvdc.total
    voltage_responses = {
        "controller_cv":cv_r,"voltage_command_hold":pwm_v,"current_closed_for_outer":ti_actual,
        "bus_plant_gvg":gvg,"sense_hv":hvdc.total,"forward_voltage":forward_v,
        "open_voltage":open_v,"closed_voltage":forward_v/(1+open_v),
        "sensitivity_voltage":1/(1+open_v),
    }
    mv=calculate_stability_margins(f,open_v)

    fv = f
    f = config.loop_frequencies_hz("balance")
    s = 1j*2*math.pi*f
    hvdc_balance = sense_frequency_response(config.split_bus_sense, f)
    cb=config.balance_controller.transfer_function(); cb_r=cb.frequency_response(f)
    # A positive balance duty offset is defined to drive positive midpoint
    # current and reduce (Vdc+ - Vdc-).  The closed-loop sign is handled by
    # the feedback summing junction; this is the positive plant magnitude.
    inv_c_delta = (1.0/max(stage.upper_bus_capacitance_f,1e-12)
                   + 1.0/max(stage.lower_bus_capacitance_f,1e-12))
    gbal=fw.midpoint_current_gain_a_per_pu*inv_c_delta/s
    pwm_b=_zoh(f,1/fw.balance_loop_rate_hz)
    forward_b=cb_r*pwm_b*gbal
    open_b=forward_b*hvdc_balance.total
    balance_responses={
        "controller_cb":cb_r,"balance_command_hold":pwm_b,"balance_plant":gbal,"sense_balance":hvdc_balance.total,
        "forward_balance":forward_b,"open_balance":open_b,
        "closed_balance":forward_b/(1+open_b),"sensitivity_balance":1/(1+open_b),
    }
    mb=calculate_stability_margins(f,open_b)

    warnings=[
        "Local averaged control model; excludes saturation, DCM and periodic harmonic coupling.",
        "Voltage plant includes inductor-energy RHP zero, copper loss and selected R/CPL load slope.",
        "Midpoint plant is a configured gain approximation; validate against the nonlinear split-bus response.",
    ]
    warnings.extend([
        "Finite-band margin screen only; closed_loop_stability=NOT_PROVEN. All signed crossings are retained.",
        "Bipolar Iabc/Vabc ADC bias and negative-half-cycle availability UNKNOWN; no clipping is modeled.",
        "Bus model uses the feedback-only closed-current response; reference-rate hold/delay, R/L reference feedforward and line-periodic coupling are excluded from its approximation.",
        "Line-cycle/switching replay does not implement exact Bode computation/PWM delays and is not a timing-equivalence or diode-reachability proof near reversal, zero crossing or saturation.",
    ])
    loops = []
    for key, name, ctrl, responses, margins, frequencies in (
        ("current", "ABC phase current", ci, current_responses, mi, fi),
        ("voltage", "DC voltage", cv, voltage_responses, mv, fv),
        ("balance", "Auxiliary midpoint balance", cb, balance_responses, mb, f),
    ):
        typed = getattr(config, key + "_controller")
        screen = _margin_screen(margins, ctrl, intentional_pi_integrator=isinstance(typed, (PIControllerConfig, PIFControllerConfig)))
        loops.append(ViennaLoopResult(name, ctrl, responses, margins, screen == "POSITIVE_MARGINS",
                                     frequencies, config.loop_nyquist_hz(key), screen))
        if screen != "POSITIVE_MARGINS":
            warnings.append(f"Vienna {key} finite-band margin screen: {screen} (not a stability proof).")
        if frequencies[-1] < config.frequency_stop_hz:
            warnings.append(f"Vienna {key} requested Bode range truncated at {frequencies[-1]:.7g} Hz, strictly below all active sampled-block Nyquist limits ({config.loop_nyquist_hz(key):.7g} Hz).")
    from .adc_range import operating_adc_range_warnings
    warnings.extend(operating_adc_range_warnings(config))
    fs = config.sensing_frequencies_hz()
    return ViennaControlLabAnalysis(
        config=config, frequencies_hz=fi,
        current_loop=loops[0], voltage_loop=loops[1], balance_loop=loops[2],
        current_sense_response=sense_frequency_response(config.phase_current_sense, fs),
        phase_voltage_sense_response=sense_frequency_response(config.phase_voltage_sense, fs),
        split_bus_sense_response=sense_frequency_response(config.split_bus_sense, fs),
        current_sense_summary=summarize_sense_chain(config.phase_current_sense),
        split_bus_sense_summary=summarize_sense_chain(config.split_bus_sense),
        warnings=tuple(warnings), sensing_frequencies_hz=fs,
    )


__all__=["ViennaControlLabAnalysis","ViennaLoopResult","build_vienna_control_lab_analysis"]

"""Small-signal current-loop and bus-voltage-loop analysis for PFC Control Lab."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import math
from typing import Any

import numpy as np
from numpy.typing import NDArray

from llc_design.control.digital_loop import (
    DigitalTransferFunction,
    PIControllerConfig,
    PIFControllerConfig,
    StabilityMargins,
    calculate_stability_margins,
    controller_kind,
)

from .config import LoadModel, PFCControlLabConfig
from .sensing import (
    SenseChainSummary,
    SenseFrequencyResponse,
    sense_frequency_response,
    summarize_sense_chain,
    operating_adc_range_warnings,
)


FloatArray = NDArray[np.float64]
ComplexArray = NDArray[np.complex128]


@dataclass(frozen=True)
class PFCOperatingPoint:
    vin_instantaneous_abs_v: float
    line_angle_deg: float
    current_reference_a: float
    ideal_duty: float
    indu_comp: float
    input_conductance_a_per_v: float
    input_current_rms_a: float


@dataclass(frozen=True)
class LoopResult:
    name: str
    controller: DigitalTransferFunction
    responses: dict[str, ComplexArray]
    margins: StabilityMargins
    margin_screen: str
    frequencies_hz: FloatArray
    nyquist_hz: float
    closed_loop_stability: str = "NOT_PROVEN"


@dataclass(frozen=True)
class PFCControlLabAnalysis:
    config: PFCControlLabConfig
    frequencies_hz: FloatArray
    operating_point: PFCOperatingPoint
    current_loop: LoopResult
    voltage_loop: LoopResult
    current_sense_response: SenseFrequencyResponse
    vac_sense_response: SenseFrequencyResponse
    vbus_sense_response: SenseFrequencyResponse
    current_sense_summary: SenseChainSummary
    vac_sense_summary: SenseChainSummary
    vbus_sense_summary: SenseChainSummary
    angle_margins: dict[float, StabilityMargins]
    warnings: tuple[str, ...]
    candidate_evidence: dict = field(default_factory=dict)

    def frequency_domain_dict(self) -> dict[str, Any]:
        return {
            "requested_start_hz": self.config.frequency_start_hz,
            "requested_stop_hz": self.config.frequency_stop_hz,
            "requested_points": self.config.frequency_points,
            "sampling_rates_hz": {
                "current_controller": 1.0 / self.current_loop.controller.sample_time_s,
                "current_firmware": self.config.firmware.current_loop_rate_hz,
                "current_sense": self.config.current_sense.timing.sample_rate_hz,
                "voltage_controller": 1.0 / self.voltage_loop.controller.sample_time_s,
                "voltage_firmware": self.config.firmware.voltage_loop_rate_hz,
                "voltage_sense": self.config.vbus_sense.timing.sample_rate_hz,
                "amc": self.config.firmware.amc_rate_hz,
            },
            **{key: {"start_hz": float(loop.frequencies_hz[0]),
                     "stop_hz": float(loop.frequencies_hz[-1]),
                     "points": len(loop.frequencies_hz),
                     "nyquist_hz_exclusive": loop.nyquist_hz,
                     "limited_by_nyquist": len(loop.frequencies_hz) < self.config.frequency_points}
               for key, loop in (("current", self.current_loop), ("voltage", self.voltage_loop))},
        }

    def summary_dict(self) -> dict[str, Any]:
        from llc_design.control.candidate_evidence import ttpl_export_evidence
        return {
            "candidate_evidence": ttpl_export_evidence(self),
            "frequency_domain": self.frequency_domain_dict(),
            "margin_interpretation": (
                "Scalar GM uses the existing nonnegative-first selection; inspect all signed gain_margins_db "
                "at phase_crossovers_hz. Negative GM denotes a critical gain reduction, not a closed-loop stability proof."
            ),
            "operating_point": asdict(self.operating_point),
            "current_loop": {
                "controller": self.current_loop.controller.name,
                **asdict(self.current_loop.margins),
                "crossover_hz": self.current_loop.margins.critical_gain_crossover_hz,
                "phase_margin_deg": self.current_loop.margins.phase_margin_deg,
                "gain_margin_db": self.current_loop.margins.gain_margin_db,
                "margin_screen": self.current_loop.margin_screen,
                "closed_loop_stability": self.current_loop.closed_loop_stability,
            },
            "voltage_loop": {
                "controller": self.voltage_loop.controller.name,
                **asdict(self.voltage_loop.margins),
                "crossover_hz": self.voltage_loop.margins.critical_gain_crossover_hz,
                "phase_margin_deg": self.voltage_loop.margins.phase_margin_deg,
                "gain_margin_db": self.voltage_loop.margins.gain_margin_db,
                "margin_screen": self.voltage_loop.margin_screen,
                "closed_loop_stability": self.voltage_loop.closed_loop_stability,
            },
            "sensing": {
                "current": asdict(self.current_sense_summary),
                "vac": asdict(self.vac_sense_summary),
                "vbus": asdict(self.vbus_sense_summary),
            },
            "warnings": list(self.warnings),
        }


def _controller_response(controller: DigitalTransferFunction, frequencies_hz: FloatArray) -> ComplexArray:
    return controller.frequency_response(frequencies_hz)


def _zoh_response(frequencies_hz: FloatArray, sample_time_s: float) -> ComplexArray:
    f = np.asarray(frequencies_hz, dtype=float)
    return np.sinc(f * sample_time_s) * np.exp(-1j * math.pi * f * sample_time_s)


def _current_plant_response(
    config: PFCControlLabConfig,
    frequencies_hz: FloatArray,
) -> ComplexArray:
    stage = config.power_stage
    s = 1j * 2.0 * math.pi * frequencies_hz
    return stage.bus_voltage_v / (
        stage.boost_inductance_h * s + stage.equivalent_series_resistance_ohm
    )


def _current_loop_at_indu_comp(
    config: PFCControlLabConfig,
    frequencies_hz: FloatArray,
    current_sense: SenseFrequencyResponse,
    indu_comp: float,
) -> tuple[dict[str, ComplexArray], StabilityMargins, DigitalTransferFunction]:
    controller = config.current_controller.transfer_function()
    control = _controller_response(controller, frequencies_hz)
    plant = _current_plant_response(config, frequencies_hz)
    pwm = (_zoh_response(frequencies_hz, 1.0 / config.firmware.current_loop_rate_hz)
           if config.firmware.include_command_zero_order_hold else
           np.ones_like(frequencies_hz, dtype=complex))
    pwm *= np.exp(-1j * 2.0 * math.pi * frequencies_hz * (
        config.firmware.current_computation_delay_s
        + config.firmware.current_pwm_update_delay_s
    ))
    forward = control * indu_comp * pwm * plant
    loop = forward * current_sense.total
    closed_measured = loop / (1.0 + loop)
    closed_actual = forward / (1.0 + loop)
    sensitivity = 1.0 / (1.0 + loop)
    return ({
        "plant_gid": plant,
        "controller_ci": control,
        "indu_comp_gain": np.full_like(frequencies_hz, complex(indu_comp, 0.0), dtype=complex),
        "pwm_zoh": pwm,
        "sense_hi": current_sense.total,
        "sense_hi_analog": current_sense.calibrated_analog,
        "sense_hi_adc": (
            current_sense.adc_aperture
            * current_sense.multi_soc_recursive
            * current_sense.digital_filter
            * current_sense.pure_delay
        ),
        "forward_current": forward,
        "open_current": loop,
        "closed_current_measured": closed_measured,
        "closed_current_actual": closed_actual,
        "sensitivity_current": sensitivity,
    }, calculate_stability_margins(frequencies_hz, loop), controller)


def _bus_power_plant_response(
    config: PFCControlLabConfig,
    frequencies_hz: FloatArray,
    current_closed_actual: ComplexArray,
) -> ComplexArray:
    stage = config.power_stage
    s = 1j * 2.0 * math.pi * frequencies_hz
    if stage.load_model == LoadModel.RESISTIVE:
        incremental_load_w_per_v = 2.0 * stage.output_power_w / stage.bus_voltage_v
    else:
        incremental_load_w_per_v = 0.0
    denominator = (
        stage.bus_capacitance_f * stage.bus_voltage_v * s
        + incremental_load_w_per_v
    )
    # ESR creates the familiar capacitor zero.  The plant input is conductance
    # command [A/V], so dPin/dg = Vrms^2 [W/(A/V)].
    esr_zero = 1.0 + s * stage.bus_capacitance_f * stage.bus_cap_esr_ohm
    return (
        stage.vin_rms_v**2
        * current_closed_actual
        * esr_zero
        / denominator
    )


def _amc_vff_response(config: PFCControlLabConfig, frequencies_hz: FloatArray) -> ComplexArray:
    firmware = config.firmware
    vrms = config.power_stage.vin_rms_v
    if firmware.vff_bypass:
        dc_gain = firmware.vac_rms_feedforward_gain
    else:
        # Firmware: vloop /= (Kff*Vrms)^2; gcmd = vloop*Kff.
        dc_gain = 1.0 / (firmware.vac_rms_feedforward_gain * vrms**2)
    ts = 1.0 / firmware.amc_rate_hz
    response = np.full(frequencies_hz.shape, dc_gain, dtype=complex)
    if firmware.include_command_zero_order_hold:
        response *= _zoh_response(frequencies_hz, ts)
    response *= np.exp(-1j * 2.0 * math.pi * frequencies_hz * (
        firmware.amc_update_delay_s + firmware.voltage_computation_delay_s
    ))
    return response


def _margin_screen(
    margins: StabilityMargins, controller: DigitalTransferFunction, *,
    intentional_pi_integrator: bool = False,
) -> str:
    """Screen measured margins, without claiming closed-loop stability.

    A typed PI/PIF has an intentional single z=+1 integrator. Its standalone
    non-BIBO-stability is not evidence of closed-loop instability. Arbitrary
    marginal or unstable controller poles still require separate review.
    """
    poles = np.asarray(controller.poles, dtype=complex)
    integrators = np.isclose(poles, 1.0, rtol=0.0, atol=1e-10)
    acceptable_poles = controller.stable or (
        intentional_pi_integrator and np.count_nonzero(integrators) == 1
        and np.all(np.abs(poles[~integrators]) < 1.0)
    )
    if not acceptable_poles:
        return "REVIEW_CONTROLLER_POLES"
    pm, gm = margins.phase_margin_deg, margins.gain_margin_db
    observed = (*margins.phase_margins_deg, *margins.gain_margins_db, pm, gm)
    if any(value is not None and math.isfinite(value) and value <= 0.0 for value in observed):
        return "NONPOSITIVE_MARGIN"
    if (margins.critical_gain_crossover_hz is None
            or not math.isfinite(margins.critical_gain_crossover_hz)
            or margins.critical_gain_crossover_hz <= 0.0
            or pm is None or gm is None
            or any(value is not None and not math.isfinite(value) for value in observed)):
        return "INCOMPLETE"
    return "POSITIVE_MARGINS"


def build_pfc_control_lab_analysis(config: PFCControlLabConfig) -> PFCControlLabAnalysis:
    """Build both nested loops and all external sensing paths."""

    config.validate()
    frequencies = config.loop_frequencies_hz("current")
    voltage_frequencies = config.loop_frequencies_hz("voltage")
    voltage_count = len(voltage_frequencies)
    current_sense = sense_frequency_response(config.current_sense, frequencies)
    vac_frequencies = frequencies[frequencies < 0.5 * config.vac_sense.timing.sample_rate_hz]
    vac_sense = sense_frequency_response(config.vac_sense, vac_frequencies)
    vbus_sense = sense_frequency_response(config.vbus_sense, voltage_frequencies)

    stage = config.power_stage
    operating = PFCOperatingPoint(
        vin_instantaneous_abs_v=stage.vin_instantaneous_abs_v,
        line_angle_deg=stage.line_angle_deg,
        current_reference_a=stage.current_reference_a,
        ideal_duty=stage.ideal_duty,
        indu_comp=stage.indu_comp,
        input_conductance_a_per_v=stage.input_conductance_a_per_v,
        input_current_rms_a=stage.output_power_w /
        (stage.efficiency * stage.vin_rms_v),
    )

    current_responses, current_margins, current_controller = _current_loop_at_indu_comp(
        config, frequencies, current_sense, operating.indu_comp
    )
    current_result = LoopResult(
        name="PFC current inner loop",
        controller=current_controller,
        responses=current_responses,
        margins=current_margins,
        margin_screen=_margin_screen(
            current_margins, current_controller,
            intentional_pi_integrator=isinstance(config.current_controller, (PIControllerConfig, PIFControllerConfig)),
        ),
        frequencies_hz=frequencies,
        nyquist_hz=config.loop_nyquist_hz("current"),
    )

    voltage_controller = config.voltage_controller.transfer_function()
    cv = _controller_response(voltage_controller, voltage_frequencies)
    amc_vff = _amc_vff_response(config, voltage_frequencies)
    bus_plant = _bus_power_plant_response(
        config, voltage_frequencies, current_responses["closed_current_actual"][:voltage_count]
    )
    outer_forward = cv * amc_vff * bus_plant
    outer_open = outer_forward * vbus_sense.total
    outer_closed = outer_open / (1.0 + outer_open)
    outer_sensitivity = 1.0 / (1.0 + outer_open)
    voltage_responses = {
        "controller_cv": cv,
        "amc_vff": amc_vff,
        "current_closed_for_outer": current_responses["closed_current_actual"][:voltage_count],
        "bus_plant_gvg": bus_plant,
        "sense_hv": vbus_sense.total,
        "sense_hv_analog": vbus_sense.calibrated_analog,
        "sense_hv_adc": (
            vbus_sense.adc_aperture
            * vbus_sense.multi_soc_recursive
            * vbus_sense.digital_filter
            * vbus_sense.pure_delay
        ),
        "forward_voltage": outer_forward,
        "open_voltage": outer_open,
        "closed_voltage": outer_closed,
        "sensitivity_voltage": outer_sensitivity,
    }
    voltage_margins = calculate_stability_margins(voltage_frequencies, outer_open)
    voltage_result = LoopResult(
        name="PFC bus-voltage outer loop",
        controller=voltage_controller,
        responses=voltage_responses,
        margins=voltage_margins,
        margin_screen=_margin_screen(
            voltage_margins, voltage_controller,
            intentional_pi_integrator=isinstance(config.voltage_controller, (PIControllerConfig, PIFControllerConfig)),
        ),
        frequencies_hz=voltage_frequencies,
        nyquist_hz=config.loop_nyquist_hz("voltage"),
    )

    angle_margins: dict[float, StabilityMargins] = {}
    original = stage
    for angle in (10.0, 20.0, 30.0, 45.0, 60.0, 75.0, 90.0):
        vin = abs(original.line_peak_v * math.sin(math.radians(angle)))
        iref = original.input_conductance_a_per_v * vin
        indu_comp = min(max(config.firmware.indu_comp_gain * iref,
                            config.firmware.indu_comp_min),
                        config.firmware.indu_comp_max)
        _, margin, _ = _current_loop_at_indu_comp(
            config, frequencies, current_sense, indu_comp
        )
        angle_margins[angle] = margin

    warnings: list[str] = list(operating_adc_range_warnings(config))
    for key, result in (("Current", current_result), ("Voltage", voltage_result)):
        if len(result.frequencies_hz) < config.frequency_points:
            warnings.append(
                f"{key}-loop requested Bode range {config.frequency_start_hz:g}–{config.frequency_stop_hz:g} Hz "
                f"is limited to {result.frequencies_hz[0]:g}–{result.frequencies_hz[-1]:g} Hz "
                f"({len(result.frequencies_hz)} samples), strictly below its {result.nyquist_hz:g} Hz Nyquist bound; "
                "margins and downstream evidence cover only this retained band."
            )
    if current_margins.critical_gain_crossover_hz is None:
        warnings.append("Current-loop 0 dB crossover is outside the plotted range.")
    if voltage_margins.critical_gain_crossover_hz is None:
        warnings.append("Voltage-loop 0 dB crossover is outside the plotted range.")
    if current_margins.phase_margin_deg is not None and current_margins.phase_margin_deg < 35.0:
        warnings.append("Current-loop phase margin is below 35 degrees.")
    if voltage_margins.phase_margin_deg is not None and voltage_margins.phase_margin_deg < 35.0:
        warnings.append("Voltage-loop phase margin is below 35 degrees.")
    fc_i = current_margins.critical_gain_crossover_hz
    fc_v = voltage_margins.critical_gain_crossover_hz
    if fc_i and fc_v and fc_i / fc_v < 5.0:
        warnings.append("Current/voltage loop bandwidth separation is below 5:1.")
    if fc_v and fc_v > 0.5 * stage.line_frequency_hz:
        warnings.append(
            "Voltage-loop crossover is high relative to line frequency; 2x-line ripple may modulate input current."
        )
    pulse = stage.minimum_effective_pulse_s
    if pulse > 0.0 and pulse * stage.switching_frequency_hz >= stage.duty_min:
        warnings.append("Minimum effective pulse width is active at the configured duty minimum.")
    if config.firmware.vff_bypass:
        warnings.append("Vrms feed-forward bypass is enabled; outer-loop gain becomes line-voltage dependent.")

    return PFCControlLabAnalysis(
        config=config,
        frequencies_hz=frequencies,
        operating_point=operating,
        current_loop=current_result,
        voltage_loop=voltage_result,
        current_sense_response=current_sense,
        vac_sense_response=vac_sense,
        vbus_sense_response=vbus_sense,
        current_sense_summary=summarize_sense_chain(
            config.current_sense, switching_frequency_hz=stage.switching_frequency_hz),
        vac_sense_summary=summarize_sense_chain(
            config.vac_sense, switching_frequency_hz=stage.switching_frequency_hz),
        vbus_sense_summary=summarize_sense_chain(
            config.vbus_sense, switching_frequency_hz=stage.switching_frequency_hz),
        angle_margins=angle_margins,
        warnings=tuple(warnings),
    )


__all__ = [
    "LoopResult",
    "PFCControlLabAnalysis",
    "PFCOperatingPoint",
    "build_pfc_control_lab_analysis",
]

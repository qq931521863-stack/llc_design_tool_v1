"""Install the shared-ngspice closed-loop verifier into an LLC main window.

The host owns project state and worker-thread execution.  This bridge reuses the
exact H(z) already designed by LLC Digital Control / Control Tools and the same
firmware-style PCMD FM LUT used by the linear loop analysis; it does not create
a second controller implementation.
"""
from __future__ import annotations

import math
from types import MethodType

import numpy as np
from PySide6.QtWidgets import QMessageBox

from llc_design.control.analysis import build_small_signal_analysis
from llc_design.control.digital_loop import (
    DelayEnvelope,
    DigitalLoopAnalysis,
    FMLUTMode,
    FrequencyModulatorLUT,
    build_digital_loop_analysis,
)
from llc_design.core.spec import PrimaryTopology
from llc_design.core.tank import design_tank
from llc_design.models.system import LLCSystemAnalyzer
from power_control_tools.models import DigitalTransferFunction as PublicDigitalTransferFunction
from power_sim.closed_loop import ClosedLoopScenario, StepProfile
from power_sim.digital_control import (
    ControllerLimitConfig,
    LLCFMLUTConfig,
    PWMCountMode,
    SamplerConfig,
)
from power_sim.spice import (
    LLCSpiceConfig,
    NgSpiceClosedLoopConfig,
    find_ngspice_shared_library,
    ngspice_closed_loop_waveform_bundle,
    run_llc_shared_closed_loop,
)

from .widgets.closed_loop_verification_view import ClosedLoopVerificationView


def _public_controller_from_native(native, source: str) -> PublicDigitalTransferFunction:
    return PublicDigitalTransferFunction(
        tuple(float(v) for v in np.asarray(native.numerator, dtype=float).reshape(-1)),
        tuple(float(v) for v in np.asarray(native.denominator, dtype=float).reshape(-1)),
        1.0 / float(native.sample_time_s),
        str(getattr(native, "name", "LLC exact H(z)")),
        str(source),
    ).normalized()


def _public_controller(external, analysis: DigitalLoopAnalysis | None) -> tuple[PublicDigitalTransferFunction, str]:
    if external is not None:
        if all(hasattr(external, name) for name in ("b", "a", "sample_rate_hz")):
            controller = PublicDigitalTransferFunction(
                tuple(float(v) for v in getattr(external, "b")),
                tuple(float(v) for v in getattr(external, "a")),
                float(getattr(external, "sample_rate_hz")),
                str(getattr(external, "name", "Control Tools exact H(z)")),
                str(getattr(external, "source", "Control Tools") or "Control Tools"),
            ).normalized()
            return controller, controller.source or controller.name
        raise TypeError("external control design must provide b/a/sample_rate_hz")
    if analysis is None:
        raise ValueError("请先在 LLC 数字控制页完成控制器设计，或从 Control Tools 发送 Exact H(z)。")
    return _public_controller_from_native(analysis.controller, analysis.controller_source), analysis.controller_source


def _same_coefficients(native, controller: PublicDigitalTransferFunction) -> bool:
    b0 = np.asarray(native.numerator, dtype=float).reshape(-1)
    a0 = np.asarray(native.denominator, dtype=float).reshape(-1)
    b1 = np.asarray(controller.b, dtype=float).reshape(-1)
    a1 = np.asarray(controller.a, dtype=float).reshape(-1)
    return (
        b0.shape == b1.shape
        and a0.shape == a1.shape
        and np.allclose(b0, b1, rtol=1e-10, atol=1e-12)
        and np.allclose(a0, a1, rtol=1e-10, atol=1e-12)
    )


def _loop_analysis_for_controller(host, spec, controller: PublicDigitalTransferFunction, source: str) -> DigitalLoopAnalysis:
    existing = getattr(host, "digital_loop_analysis", None)
    if existing is not None:
        c = existing.controller
        same_fs = math.isclose(1.0 / c.sample_time_s, controller.sample_rate_hz, rel_tol=0.0, abs_tol=1e-6)
        if same_fs and _same_coefficients(c, controller):
            return existing

    system = getattr(host, "system_analysis", None)
    if system is None or system.spec != spec:
        system = LLCSystemAnalyzer().analyze(spec)
    small = build_small_signal_analysis(
        spec,
        system_analysis=system,
        sample_time_s=1.0 / controller.sample_rate_hz,
    )
    return build_digital_loop_analysis(
        small,
        controller_transfer_function=controller,
        controller_source=source,
        fm_lut=FrequencyModulatorLUT.firmware_default(),
    )


def _fm_runtime_config(loop: DigitalLoopAnalysis, spec) -> tuple[LLCFMLUTConfig, ControllerLimitConfig]:
    lut = loop.fm_lut
    op = loop.fm_operating_point
    count_mode = PWMCountMode.UP_DOWN if lut.count_mode.value == "up_down" else PWMCountMode.UP
    config = LLCFMLUTConfig(
        pcmd=tuple(float(v) for v in np.asarray(lut.pcmd, dtype=float)),
        values=tuple(float(v) for v in np.asarray(lut.values, dtype=float)),
        command_bias_pu=float(op.command_pu),
        values_are_tbprd=lut.mode == FMLUTMode.PCMD_TO_TBPRD,
        tbclk_hz=float(lut.timer_clock_hz),
        count_mode=count_mode,
        quantize_tbprd=True,
    )
    config.validate()

    # Restrict controller perturbation to both LUT endpoints and the active LLC
    # design's Fmin/Fmax.  The default firmware table may cover a wider range
    # than one particular power-stage design and must not silently overdrive it.
    cmd_for_fmax = float(lut.command_for_frequency(spec.maximum_frequency_hz))
    cmd_for_fmin = float(lut.command_for_frequency(spec.minimum_frequency_hz))
    allowed_low_abs = min(cmd_for_fmax, cmd_for_fmin)
    allowed_high_abs = max(cmd_for_fmax, cmd_for_fmin)
    allowed_low_abs = max(0.0, allowed_low_abs)
    allowed_high_abs = min(1.0, allowed_high_abs)
    bias = float(op.command_pu)
    minimum_delta = max(-float(op.command_headroom_low), allowed_low_abs - bias)
    maximum_delta = min(float(op.command_headroom_high), allowed_high_abs - bias)
    if maximum_delta <= minimum_delta + 1e-9:
        raise ValueError("当前 FM LUT 工作点在 LLC Fmin/Fmax 内没有可用控制余量。")
    return config, ControllerLimitConfig(minimum=minimum_delta, maximum=maximum_delta)


def install_closed_loop_verification(host) -> ClosedLoopVerificationView:
    """Add one closed-loop tab and its worker-backed execution bridge."""
    if hasattr(host, "closed_loop_verification_view"):
        return host.closed_loop_verification_view

    view = ClosedLoopVerificationView()
    host.closed_loop_verification_view = view
    host.tabs.addTab(view, "Closed-Loop Verification")

    initial_library = find_ngspice_shared_library()
    view.set_engine_available(
        initial_library is not None,
        initial_library or "shared library not found on this machine",
    )
    view.set_nominal_spec(host.spec)

    # Extend the host's normal busy state so the shared-ngspice singleton cannot
    # be launched twice from the GUI while one run is active.
    original_set_busy = host._set_busy

    def set_busy_with_closed_loop(self, busy: bool, message: str = "") -> None:
        original_set_busy(busy, message)
        view.set_busy(busy)

    host._set_busy = MethodType(set_busy_with_closed_loop, host)

    def refresh_controller(self) -> None:
        try:
            controller, source = _public_controller(
                getattr(self, "external_control_design", None),
                getattr(self, "digital_loop_analysis", None),
            )
            label = getattr(self, "external_control_label", "") or source or controller.name
            view.set_controller_info(
                f"{label}\n{controller.name} | order={max(len(controller.a), len(controller.b))-1}",
                controller.sample_rate_hz,
            )
        except Exception as exc:
            view.set_controller_info(str(exc), None)

    def prepare_run(self, options: dict[str, float]):
        spec = self._spec_from_widgets()
        if PrimaryTopology(spec.primary_topology) != PrimaryTopology.FULL_BRIDGE:
            raise NotImplementedError("ngspice Closed-Loop Verification V1 只支持 FULL_BRIDGE LLC。")

        controller, source = _public_controller(
            getattr(self, "external_control_design", None),
            getattr(self, "digital_loop_analysis", None),
        )
        loop = _loop_analysis_for_controller(self, spec, controller, source)
        modulator, limits = _fm_runtime_config(loop, spec)
        nominal_f = float(modulator.nominal_frequency_hz)
        if not (spec.minimum_frequency_hz <= nominal_f <= spec.maximum_frequency_hz):
            raise ValueError(
                f"FM operating point {nominal_f/1e3:.3f} kHz is outside LLC design limits "
                f"[{spec.minimum_frequency_hz/1e3:.3f}, {spec.maximum_frequency_hz/1e3:.3f}] kHz"
            )

        sampler = SamplerConfig(sample_rate_hz=controller.sample_rate_hz)
        computation_delay = float(loop.adc_sampling.eoc_delay_s + loop.command_timing.computation_delay_s)
        pwm_delay = float(loop.command_timing.pwm_zero_wait_s(nominal_f, DelayEnvelope.NOMINAL))

        bus = float(options["vbus_v"])
        load = float(options["load_fraction"])
        spice_cfg = LLCSpiceConfig(
            switching_frequency_hz=nominal_f,
            bus_voltage_v=bus,
            load_fraction=load,
            initial_output_v=spec.vout_v,
        )
        scenario = ClosedLoopScenario(
            reference_v=StepProfile(
                spec.vout_v,
                step_time_s=float(options["step_time_s"]),
                final=spec.vout_v + float(options["reference_step_v"]),
            ),
            bus_voltage_v=StepProfile(bus),
            load_fraction=StepProfile(load),
        )
        sim_cfg = NgSpiceClosedLoopConfig(
            duration_s=float(options["duration_s"]),
            computation_delay_s=computation_delay,
            pwm_update_delay_s=pwm_delay,
            output_step_s=float(options["output_step_s"]),
            max_step_s=float(options["max_step_s"]),
            wall_timeout_s=180.0,
            use_initial_conditions=True,
        )
        tank = design_tank(spec)
        return spec, tank, spice_cfg, controller, limits, sampler, modulator, scenario, sim_cfg, loop

    def run_worker_payload(options: dict[str, float], library_path: str):
        (
            spec,
            tank,
            spice_cfg,
            controller,
            limits,
            sampler,
            modulator,
            scenario,
            sim_cfg,
            loop,
        ) = prepare_run(host, options)
        result = run_llc_shared_closed_loop(
            spec,
            tank,
            spice_cfg,
            controller=controller,
            controller_limits=limits,
            sampler=sampler,
            modulator=modulator,
            scenario=scenario,
            simulation=sim_cfg,
            library=library_path,
        )
        bundle = ngspice_closed_loop_waveform_bundle(
            result,
            bus_voltage_v=spice_cfg.bus_voltage_v,
            samples_per_switching_cycle=80,
        )
        return result, bundle, loop, spice_cfg

    def run_requested(options: dict[str, float]) -> None:
        library_path = find_ngspice_shared_library()
        if library_path is None:
            QMessageBox.warning(
                host,
                "ngspice unavailable",
                "没有检测到 shared libngspice。请安装 ngspice/libngspice 后再运行闭环验证。",
            )
            return
        try:
            spec = host._spec_from_widgets()
            if PrimaryTopology(spec.primary_topology) != PrimaryTopology.FULL_BRIDGE:
                raise NotImplementedError("ngspice Closed-Loop Verification V1 只支持 FULL_BRIDGE LLC。")
            _public_controller(
                getattr(host, "external_control_design", None),
                getattr(host, "digital_loop_analysis", None),
            )
        except Exception as exc:
            QMessageBox.warning(host, "Closed-Loop Verification", str(exc))
            return

        def ready(payload) -> None:
            result, bundle, loop, spice_cfg = payload
            host.digital_loop_analysis = loop
            view.set_result(bundle, result.control)
            view.set_controller_info(
                f"{result.control.metadata.get('controller_source', '')}\n"
                f"{result.control.metadata.get('controller_name', '')}",
                float(result.control.metadata.get("sample_rate_hz", 0.0)),
            )
            host.tabs.setCurrentWidget(view)
            host._append_log(
                "shared-ngspice exact-H(z)/FM-LUT closed loop ready: "
                f"samples={len(result.control.samples)}, "
                f"points={result.control.metadata.get('shared_senddata_points')}, "
                f"Vbus={spice_cfg.bus_voltage_v:.3f} V, "
                f"load={spice_cfg.load_fraction*100:.1f}%"
            )

        host._run_worker(
            "正在运行 shared-ngspice Exact H(z) + FM LUT 数字闭环验证…",
            lambda: run_worker_payload(dict(options), library_path),
            ready,
        )

    view.analysis_requested.connect(run_requested)

    host.refresh_closed_loop_controller = MethodType(refresh_controller, host)
    host.refresh_closed_loop_controller()

    def tab_changed(index: int) -> None:
        if host.tabs.widget(index) is view:
            host.refresh_closed_loop_controller()
            view.set_nominal_spec(host.spec)
            current_library = find_ngspice_shared_library()
            view.set_engine_available(current_library is not None, current_library or "shared library not found")

    host.tabs.currentChanged.connect(tab_changed)
    return view


__all__ = ["install_closed_loop_verification"]

"""Install the shared-ngspice closed-loop verifier into an LLC main window.

Kept as a sidecar installer so the first SPICE integration does not entangle the
large LLC main-window class with simulator-specific code.  The host remains the
owner of project state and worker-thread execution; this module only bridges the
existing design/controller state into ``power_sim.spice``.
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
    FrequencyModulatorLUT,
    build_digital_loop_analysis,
)
from llc_design.core.spec import PrimaryTopology
from llc_design.core.tank import design_tank
from llc_design.models.system import LLCSystemAnalyzer
from power_control_tools.models import DigitalTransferFunction as PublicDigitalTransferFunction
from power_sim.closed_loop import ClosedLoopScenario, StepProfile
from power_sim.digital_control import ControllerLimitConfig, LLCFMConfig, LLCFMMode, PWMCountMode, SamplerConfig
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


def _loop_analysis_for_controller(host, spec, controller: PublicDigitalTransferFunction, source: str) -> DigitalLoopAnalysis:
    existing = getattr(host, "digital_loop_analysis", None)
    if existing is not None:
        c = existing.controller
        same_fs = math.isclose(1.0 / c.sample_time_s, controller.sample_rate_hz, rel_tol=0.0, abs_tol=1e-6)
        same_b = np.allclose(np.asarray(c.numerator), np.asarray(controller.b), rtol=1e-10, atol=1e-12)
        same_a = np.allclose(np.asarray(c.denominator), np.asarray(controller.a), rtol=1e-10, atol=1e-12)
        if same_fs and same_b and same_a:
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


def install_closed_loop_verification(host) -> ClosedLoopVerificationView:
    """Add one closed-loop tab and its worker-backed execution bridge."""
    if hasattr(host, "closed_loop_verification_view"):
        return host.closed_loop_verification_view

    view = ClosedLoopVerificationView()
    host.closed_loop_verification_view = view
    host.tabs.addTab(view, "Closed-Loop Verification")

    library = find_ngspice_shared_library()
    view.set_engine_available(
        library is not None,
        library or "shared library not found on this machine",
    )
    view.set_nominal_spec(host.spec)

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
        op = loop.fm_operating_point
        nominal_f = float(op.frequency_hz)
        if not (spec.minimum_frequency_hz <= nominal_f <= spec.maximum_frequency_hz):
            nominal_f = float(np.clip(nominal_f, spec.minimum_frequency_hz, spec.maximum_frequency_hz))

        # Exact H(z) produces a small-signal PCMD perturbation about the FM LUT
        # operating point.  Limits therefore use available command headroom,
        # while the nonlinear SPICE runtime applies the local LUT slope around
        # that operating point.  Replacing this local FM with the full firmware
        # LUT is a later fidelity layer and is reported explicitly in the GUI.
        limits = ControllerLimitConfig(
            minimum=-max(float(op.command_headroom_low), 1e-6),
            maximum=max(float(op.command_headroom_high), 1e-6),
        )
        modulator = LLCFMConfig(
            mode=LLCFMMode.LINEAR_FM,
            nominal_frequency_hz=nominal_f,
            kfm_hz_per_unit=float(op.gain_hz_per_pu),
            minimum_frequency_hz=spec.minimum_frequency_hz,
            maximum_frequency_hz=spec.maximum_frequency_hz,
            tbclk_hz=float(loop.fm_lut.timer_clock_hz),
            count_mode=(
                PWMCountMode.UP_DOWN
                if loop.fm_lut.count_mode.value == "up_down"
                else PWMCountMode.UP
            ),
            quantize_tbprd=True,
        )
        sampler = SamplerConfig(sample_rate_hz=controller.sample_rate_hz)

        # Preserve the nominal timing budget of the already-designed digital
        # loop: ADC EOC + firmware computation + average wait to PWM zero.
        computation_delay = float(loop.adc_sampling.eoc_delay_s + loop.command_timing.computation_delay_s)
        pwm_delay = float(
            loop.command_timing.pwm_zero_wait_s(nominal_f, DelayEnvelope.NOMINAL)
        )

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

    def run_worker_payload(self, options: dict[str, float]):
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
            library=library,
        )
        bundle = ngspice_closed_loop_waveform_bundle(
            result,
            bus_voltage_v=spice_cfg.bus_voltage_v,
            samples_per_switching_cycle=80,
        )
        return result, bundle, loop, spice_cfg

    def run_requested(options: dict[str, float]) -> None:
        if library is None:
            QMessageBox.warning(
                host,
                "ngspice unavailable",
                "没有检测到 shared libngspice。请安装 ngspice/libngspice 后再运行闭环验证。",
            )
            return
        try:
            # Validate all design/controller contracts in the GUI thread so
            # user input errors are returned immediately.  The SPICE solve
            # itself remains in the worker thread.
            prepare_run(host, options)
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
                "shared-ngspice closed loop ready: "
                f"samples={len(result.control.samples)}, "
                f"points={result.control.metadata.get('shared_senddata_points')}, "
                f"Vbus={spice_cfg.bus_voltage_v:.3f} V, "
                f"load={spice_cfg.load_fraction*100:.1f}%"
            )

        host._run_worker(
            "正在运行 shared-ngspice 数字闭环验证…",
            lambda: run_worker_payload(host_options := dict(options)),
            ready,
        )

    view.analysis_requested.connect(run_requested)

    # Expose tiny hooks on the host rather than modifying LLCMainWindow's large
    # class body.  They are useful to the launcher/controller bridge and tests.
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

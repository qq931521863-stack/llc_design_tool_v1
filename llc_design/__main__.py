"""Command-line interface for the LLC design calculator."""

from __future__ import annotations

from pathlib import Path
from dataclasses import replace

import click

from .analysis import (
    GoldenSolverConfig,
    HarmonicBalanceConfig,
    LLCAnalysisRequest,
    LLCGoldenSolver,
    TimeDomainConfig,
    export_multifidelity_analysis,
    solve_fha,
    solve_harmonic_balance,
)
from .core.config import load_spec, save_spec
from .core.spec import LLCDesignSpec, PrimaryTopology, TankParameterMode
from .core.tank import GainNotReachableError
from .models.devices import DeviceDatabase
from .models.system import LLCSystemAnalyzer
from .magnetics.core import CoreDatabase
from .magnetics.transformer_designer import (TransformerSynthesisSettings,
                                              export_transformer_synthesis,
                                              load_transformer_core_presets,
                                              synthesize_transformer)
from .optimization.sweep import LLCOptimizer, OptimizationConfig
from .report.export import export_calculation_book
from .control.analysis import build_small_signal_analysis, export_small_signal_analysis
from .control.linearize import ControlInputKind
from .control.digital_loop import (
    ADCSamplingConfig,
    AnalogSenseConfig,
    CommandTimingConfig,
    FMLUTMode,
    FrequencyModulatorLUT,
    PIControllerConfig,
    PIFControllerConfig,
    PWMCountMode,
    TwoP2ZControllerConfig,
    build_digital_loop_analysis,
    export_digital_loop_analysis,
)
from .dynamics.plant import DynamicPhasorModel
from .dynamics.waveforms import reconstruct_dynamic_phasor_waveforms
from .dynamics.switched import SwitchedSimulationConfig, simulate_switched_steady_state
from .dynamics.export import export_waveform_bundle
from .analysis.complementarity import ComplementarityTimeDomainConfig, solve_complementarity_time_domain
from .switching.sr import analyze_sr, export_sr_lut_c99, generate_sr_lut
from .multiphase import solve_interleaved_llc
from .magnetics.v8 import analyze_golden_magnetics

from power_sim import (
    ControllerLimitConfig, SamplerConfig, LLCFMConfig, LLCFMMode,
    PWMCountMode as SimPWMCountMode, ClosedLoopTiming, ClosedLoopScenario, StepProfile,
    FirstOrderLLCPlant, run_closed_loop, analyze_linear_closed_loop,
)
from power_control_tools import (
    ControllerKind as ToolControllerKind, DiscretizationMethod, design_controller, discretize_transfer_function,
)


def _base_spec(config: str | None) -> LLCDesignSpec:
    return load_spec(config) if config else LLCDesignSpec()


def _override(spec: LLCDesignSpec, **values) -> LLCDesignSpec:
    changes = {key: value for key, value in values.items() if value is not None}
    return spec.clone(**changes) if changes else spec


@click.group()
def cli():
    """LLC half/full-bridge + full-bridge SR design and optimization tool."""


@cli.command("transformer-design")
@click.option("--config", type=click.Path(exists=True, dir_okay=False), default=None)
@click.option("--preset", default="TDK_PQ35_35_B65881A_N87", show_default=True,
              help="Ferrite/core preset key; GUI also allows direct datasheet entry.")
@click.option("--bmax", type=float, default=0.18, show_default=True, help="Design Bpk limit (T)")
@click.option("--strand-mm", type=float, default=0.10, show_default=True, help="Litz copper strand diameter (mm)")
@click.option("--strand-step", type=int, default=50, show_default=True, help="Strand-count rounding step")
@click.option("--j", "current_density", type=float, default=6.0, show_default=True, help="Target copper current density (A/mm^2)")
@click.option("--output", type=click.Path(file_okay=False), default="output/transformer_design", show_default=True)
def transformer_design(config, preset, bmax, strand_mm, strand_step, current_density, output):
    """Synthesize LLC transformer turns/Litz and export magnetic/copper losses."""
    spec = _base_spec(config)
    presets = load_transformer_core_presets()
    if preset not in presets:
        raise click.ClickException(f"unknown transformer preset: {preset}; available={', '.join(presets)}")
    settings = TransformerSynthesisSettings(
        max_flux_density_t=bmax,
        strand_copper_diameter_mm=strand_mm,
        strand_outer_diameter_mm=strand_mm * 1.12,
        strand_count_step=strand_step,
        current_density_target_a_per_mm2=current_density,
    )
    result = synthesize_transformer(spec, presets[preset], settings)
    paths = export_transformer_synthesis(result, output)
    click.echo(f"Core: {result.core.shape} {result.core.material_grade} {result.core.part_number}")
    click.echo(f"Turns: {result.primary_turns}:{result.secondary_turns} (ratio={result.actual_turns_ratio:.5f})")
    click.echo(f"Primary Litz: {result.primary_litz.description}; J={result.primary_litz.current_density_a_per_mm2:.2f} A/mm2")
    click.echo(f"Secondary Litz: {result.secondary_litz.description}; J={result.secondary_litz.current_density_a_per_mm2:.2f} A/mm2")
    click.echo(f"Bpk(max): {result.worst_b_peak_t*1e3:.2f} mT; fill={result.fill_factor*100:.1f}%")
    click.echo(f"Loss nominal: core={result.nominal_loss.core_w:.3f} W, CuP={result.nominal_loss.primary_copper_w:.3f} W, CuS={result.nominal_loss.secondary_copper_w:.3f} W, total={result.nominal_loss.total_w:.3f} W")
    click.echo(f"Feasible: {result.feasible}")
    for reason in result.reasons:
        click.echo(f"  - {reason}")
    click.echo(f"Export: {paths['json']}")


@cli.command()
@click.option("--config", type=click.Path(exists=True, dir_okay=False), default=None,
              help="JSON input file; command options override it.")
@click.option("--vbus", type=float, default=None, help="Nominal DC bus voltage (V)")
@click.option("--vout", type=float, default=None, help="Output voltage (V)")
@click.option("--pout", type=float, default=None, help="Output power (W)")
@click.option("--fr-khz", type=float, default=None, help="Resonant frequency (kHz)")
@click.option("--fmin-khz", type=float, default=None, help="Minimum frequency (kHz)")
@click.option("--fmax-khz", type=float, default=None, help="Maximum frequency (kHz)")
@click.option("--ln", type=float, default=None, help="Lm/Lr ratio")
@click.option("--q", "q_full", type=float, default=None, help="Full-load FHA Q")
@click.option("--parameter-mode", type=click.Choice(["auto", "user"]), default=None, help="Tank source: auto synthesis or user-defined validation")
@click.option("--lr-uh", type=float, default=None, help="User-defined resonant inductance Lr (uH)")
@click.option("--cr-nf", type=float, default=None, help="User-defined resonant capacitance Cr (nF)")
@click.option("--lm-uh", type=float, default=None, help="User-defined magnetizing inductance Lm (uH)")
@click.option("--np", "primary_turns", type=int, default=None, help="Primary turns")
@click.option("--ns", "secondary_turns", type=int, default=None, help="Secondary turns")
@click.option("--bus-cap-uf", type=float, default=None, help="Installed bus capacitance (uF)")
@click.option("--hold-ms", type=float, default=None, help="Requested hold-up time (ms)")
@click.option("--device", default=None, help="Primary MOSFET reference part (see: python -m llc_design devices)")
@click.option("--sr-device", default=None, help="SR MOSFET reference part (see: python -m llc_design devices)")
@click.option("--primary-parallel", type=int, default=None, help="MOSFETs in parallel per primary switch")
@click.option("--sr-parallel", type=int, default=None, help="MOSFETs per SR bridge position")
@click.option("--half-bridge", is_flag=True, default=False, help="Use primary half bridge")
@click.option("--transformer-core", default=None, help="Force transformer reference core")
@click.option("--inductor-core", default=None, help="Force resonant-inductor reference core")
@click.option("--transformer-family", multiple=True, type=click.Choice(["PQ", "EE", "EC", "EER", "ETD"]), help="Restrict automatic transformer core search; repeat for multiple families")
@click.option("--inductor-family", multiple=True, type=click.Choice(["PQ", "EE", "EC", "EER", "ETD"]), help="Restrict automatic resonant-inductor core search")
@click.option("--output", type=click.Path(file_okay=False), default="output/llc_baseline",
              show_default=True, help="Calculation-book output directory")
@click.option("--plots/--no-plots", default=True, help="Generate PNG plots")
def run(config, vbus, vout, pout, fr_khz, fmin_khz, fmax_khz, ln, q_full,
        parameter_mode, lr_uh, cr_nf, lm_uh, primary_turns, secondary_turns, bus_cap_uf, hold_ms, device, sr_device,
        primary_parallel, sr_parallel,
        half_bridge, transformer_core, inductor_core, transformer_family, inductor_family, output, plots):
    """Run one complete LLC calculation and export the calculation book."""
    spec = _base_spec(config)
    if half_bridge and primary_turns is None:
        primary_turns = max(1, round(spec.primary_turns / 2))
    spec = _override(
        spec,
        vbus_nom_v=vbus,
        vout_v=vout,
        pout_w=pout,
        resonant_frequency_hz=None if fr_khz is None else fr_khz * 1e3,
        minimum_frequency_hz=None if fmin_khz is None else fmin_khz * 1e3,
        maximum_frequency_hz=None if fmax_khz is None else fmax_khz * 1e3,
        ln_ratio=ln,
        q_full_load=q_full,
        parameter_mode=(TankParameterMode.USER_DEFINED if parameter_mode == "user" else TankParameterMode.AUTO_DESIGN if parameter_mode == "auto" else None),
        user_lr_h=None if lr_uh is None else lr_uh * 1e-6,
        user_cr_f=None if cr_nf is None else cr_nf * 1e-9,
        user_lm_h=None if lm_uh is None else lm_uh * 1e-6,
        primary_turns=primary_turns,
        secondary_turns=secondary_turns,
        bus_capacitance_f=None if bus_cap_uf is None else bus_cap_uf * 1e-6,
        requested_hold_time_s=None if hold_ms is None else hold_ms * 1e-3,
        primary_device=device,
        sr_device=sr_device,
        primary_parallel_devices=primary_parallel,
        sr_parallel_devices_per_position=sr_parallel,
        primary_topology=PrimaryTopology.HALF_BRIDGE if half_bridge else None,
        transformer_core_families=tuple(transformer_family) if transformer_family else None,
        resonant_inductor_core_families=tuple(inductor_family) if inductor_family else None,
    )
    try:
        analysis = LLCSystemAnalyzer().analyze(
            spec, preferred_transformer_core=transformer_core,
            preferred_inductor_core=inductor_core)
    except GainNotReachableError as exc:
        click.echo(f"错误: 谐振增益不可达 - {exc}", err=True)
        click.echo("请调整匝比 (--np/--ns)、满载 Q (--q) 或谐振频率 (--fr-khz) 后重试。",
                   err=True)
        raise SystemExit(1)
    paths = export_calculation_book(analysis, output)
    if not plots:
        for key in ("plot_gain_curves", "plot_loss_breakdown", "plot_magnetics_loss_breakdown", "plot_efficiency_workpoints"):
            path = paths.get(key)
            if path and path.exists():
                path.unlink()

    nominal = analysis.nominal
    click.echo("\nLLC V2 calculation")
    click.echo(f"  Topology: {spec.primary_topology.value} + {spec.secondary_topology.value}")
    click.echo(f"  Input/output: {spec.vbus_nom_v:.0f} Vdc -> {spec.vout_v:.1f} V / {spec.pout_w/1000:.2f} kW")
    click.echo(f"  Tank source: {spec.parameter_mode.value}; Lr={analysis.tank.lr_h*1e6:.3f} uH, Cr={analysis.tank.cr_f*1e9:.3f} nF, Lm={analysis.tank.lm_h*1e6:.3f} uH, fr={analysis.tank.fr_hz/1e3:.3f} kHz, Ln={analysis.tank.ln_ratio:.4f}, Q={analysis.tank.q_full_load:.4f}")
    click.echo(f"  Transformer: {analysis.transformer.core.part_number}, {spec.primary_turns}:{spec.secondary_turns}, fill={analysis.transformer.fill_factor*100:.1f}%")
    click.echo(f"  Resonant inductor: {analysis.resonant_inductor.core.part_number}, {analysis.resonant_inductor.turns} T, {analysis.resonant_inductor.layers} layers")
    click.echo(f"  Nominal: fs={nominal.operating_point.switching_frequency_hz/1e3:.2f} kHz, loss={nominal.total_loss_w:.2f} W, eta={nominal.efficiency*100:.3f}%")
    click.echo(f"  Feasibility: {'PASS' if analysis.feasible else 'FAIL'}")
    for reason in analysis.feasibility_reasons:
        click.echo(f"    - {reason}")
    click.echo(f"  Report: {paths['report']}")


@cli.command("waveforms")
@click.option("--config", type=click.Path(exists=True, dir_okay=False), default=None)
@click.option("--vbus", type=float, default=None, help="DC bus voltage for the waveform work point")
@click.option("--load", "load_fraction", type=float, default=1.0, show_default=True,
              help="Load fraction, 1.0 = rated power")
@click.option(
    "--mode",
    type=click.Choice(["fast", "fha", "hb", "detailed"]),
    default="fast",
    show_default=True,
    help="fast=dynamic-phasor, fha=fundamental only, hb=multi-harmonic, detailed=switched TD",
)
@click.option("--cycles", type=int, default=2, show_default=True)
@click.option("--samples", type=int, default=512, show_default=True,
              help="Samples per switching cycle")
@click.option("--output", type=click.Path(file_okay=False), default="output/llc_waveforms",
              show_default=True)
def waveforms(config, vbus, load_fraction, mode, cycles, samples, output):
    """Generate synchronized LLC key-node voltage/current waveforms."""
    spec = _base_spec(config)
    bus_voltage = spec.vbus_nom_v if vbus is None else float(vbus)
    if mode in {"fha", "hb"}:
        request = LLCAnalysisRequest(
            spec=spec,
            vbus_v=bus_voltage,
            load_fraction=load_fraction,
            waveform_cycles=cycles,
            samples_per_cycle=max(samples, 256 if mode == "hb" else 128),
        )
        if mode == "fha":
            result = solve_fha(request)
        else:
            result = solve_harmonic_balance(
                request,
                HarmonicBalanceConfig(
                    max_harmonic=7,
                    samples_per_cycle=max(samples, 256),
                    output_cycles=cycles,
                ),
            )
        bundle = result.waveform
        work_frequency_hz = result.metrics.switching_frequency_hz
    else:
        system = LLCSystemAnalyzer().analyze(spec)
        small = build_small_signal_analysis(
            spec,
            vbus_v=bus_voltage,
            load_fraction=load_fraction,
            system_analysis=system,
        )
        model = DynamicPhasorModel(small.parameters)
        if mode == "fast":
            bundle = reconstruct_dynamic_phasor_waveforms(
                model, small.steady_state,
                cycles=cycles, samples_per_cycle=samples)
        else:
            bundle = simulate_switched_steady_state(
                model, small.steady_state,
                SwitchedSimulationConfig(
                    samples_per_cycle=samples,
                    output_cycles=cycles,
                ),
            )
        work_frequency_hz = small.operating_point.switching_frequency_hz
    paths = export_waveform_bundle(bundle, output)
    click.echo(f"Waveform model: {bundle.model_name}")
    click.echo(f"Work point: {bus_voltage:.1f} V / {load_fraction*100:.1f}% / "
               f"{work_frequency_hz/1e3:.3f} kHz")
    for key in ("i_resonant", "v_resonant_cap", "i_transformer_secondary", "i_output_cap", "v_output"):
        signal = bundle.signals[key]
        stats = signal.statistics
        click.echo(f"  {signal.label}: RMS={stats.rms:.5g} {signal.unit}, "
                   f"min/max={stats.minimum:.5g}/{stats.maximum:.5g} {signal.unit}")
    click.echo(f"Report: {paths['report']}")


@cli.command("model-compare")
@click.option("--config", type=click.Path(exists=True, dir_okay=False), default=None)
@click.option("--vbus", type=float, default=None, help="DC bus voltage")
@click.option("--load", "load_fraction", type=float, default=1.0, show_default=True)
@click.option("--fixed-frequency-khz", type=float, default=None,
              help="Compare all models at one frequency instead of independently regulating Vo")
@click.option("--max-harmonic", type=int, default=7, show_default=True)
@click.option("--hb-samples", type=int, default=1024, show_default=True)
@click.option("--td-samples", type=int, default=512, show_default=True)
@click.option("--skip-td", is_flag=True, default=False,
              help="Run FHA/HB only; useful for rapid sweeps")
@click.option("--system-loss-damping", is_flag=True, default=False,
              help="Use the existing complete loss model to calibrate TD series damping")
@click.option("--output", type=click.Path(file_okay=False),
              default="output/llc_v8_model_compare", show_default=True)
def model_compare(config, vbus, load_fraction, fixed_frequency_khz,
                  max_harmonic, hb_samples, td_samples, skip_td,
                  system_loss_damping, output):
    """Run V8 FHA / nonlinear multi-harmonic / switched-TD comparison."""
    spec = _base_spec(config)
    if max_harmonic < 1:
        raise click.ClickException("max-harmonic must be >= 1")
    if max_harmonic % 2 == 0:
        raise click.ClickException("max-harmonic must be odd")
    frequency_hz = (
        None if fixed_frequency_khz is None
        else fixed_frequency_khz * 1e3
    )
    request = LLCAnalysisRequest(
        spec=spec,
        vbus_v=spec.vbus_nom_v if vbus is None else vbus,
        load_fraction=load_fraction,
        frequency_hz=frequency_hz,
        regulate_output=frequency_hz is None,
        waveform_cycles=2,
        samples_per_cycle=max(hb_samples, 256),
    )
    solver = LLCGoldenSolver(GoldenSolverConfig(
        include_time_domain=not skip_td,
        harmonic_balance=HarmonicBalanceConfig(
            max_harmonic=max_harmonic,
            samples_per_cycle=max(hb_samples, 256),
            output_cycles=2,
        ),
        time_domain=TimeDomainConfig(
            samples_per_cycle=max(td_samples, 128),
            output_cycles=2,
            retry_samples_per_cycle=max(td_samples, 512),
        ),
        derive_time_domain_damping_from_system_loss=system_loss_damping,
    ))
    analysis = solver.solve(request)
    paths = export_multifidelity_analysis(analysis, output)
    click.echo("LLC V8 multi-fidelity comparison")
    click.echo(f"  Reference: {analysis.reference_level.value}")
    for row in analysis.comparison_rows:
        metrics = row.metrics
        click.echo(
            f"  {row.fidelity.value:22s} "
            f"{'PASS' if row.converged else 'FAIL':4s} "
            f"fs={metrics.switching_frequency_hz/1e3:10.5f} kHz  "
            f"Vo={metrics.output_voltage_v:9.5f} V  "
            f"Ir_rms={metrics.resonant_current_rms_a:9.5f} A  "
            f"dIr={row.resonant_rms_error_percent:+8.3f}%"
        )
    for warning in analysis.warnings:
        click.echo(f"  WARNING: {warning}")
    click.echo(f"  Report: {paths['report']}")


@cli.command("small-signal")
@click.option("--config", type=click.Path(exists=True, dir_okay=False), default=None)
@click.option("--vbus", type=float, default=None, help="DC bus voltage for linearization")
@click.option("--load", "load_fraction", type=float, default=1.0, show_default=True)
@click.option("--sample-us", type=float, default=20.0, show_default=True,
              help="Fixed digital-control sample period in microseconds")
@click.option("--input-kind", type=click.Choice([item.value for item in ControlInputKind]),
              default=ControlInputKind.FREQUENCY_HZ.value, show_default=True)
@click.option("--timer-clock-mhz", type=float, default=None,
              help="Timer clock, required for timer_counts input")
@click.option("--delay-samples", type=int, default=0, show_default=True,
              help="Integer computation/PWM update delay")
@click.option("--series-resistance", type=float, default=None,
              help="Override primary-side equivalent tank damping resistance (ohm)")
@click.option("--output", type=click.Path(file_okay=False), default="output/llc_small_signal",
              show_default=True)
def small_signal(config, vbus, load_fraction, sample_us, input_kind,
                 timer_clock_mhz, delay_samples, series_resistance, output):
    """Build Gvf(s), Bode, exact ZOH G(z), difference equation and C99 code."""
    spec = _base_spec(config)
    system = LLCSystemAnalyzer().analyze(spec)
    kind = ControlInputKind(input_kind)
    timer_clock_hz = None if timer_clock_mhz is None else timer_clock_mhz * 1e6
    result = build_small_signal_analysis(
        spec,
        vbus_v=spec.vbus_nom_v if vbus is None else vbus,
        load_fraction=load_fraction,
        sample_time_s=sample_us * 1e-6,
        control_input_kind=kind,
        timer_clock_hz=timer_clock_hz,
        input_delay_samples=delay_samples,
        system_analysis=system,
        series_resistance_ohm=series_resistance,
    )
    paths = export_small_signal_analysis(result, output)
    tf = result.continuous_transfer
    click.echo("LLC seven-state dynamic-phasor/EDF small-signal model")
    click.echo(f"  Work point: {result.operating_point.vbus_v:.1f} V / "
               f"{load_fraction*100:.1f}% / {result.operating_point.switching_frequency_hz/1e3:.3f} kHz")
    click.echo(f"  EDF steady output: {result.steady_state.output_voltage_v:.6f} V")
    click.echo(f"  G(0): {tf.dc_gain:.9g} {tf.output_unit}/{tf.input_unit}")
    click.echo(f"  Continuous/discrete stable: {result.continuous_plant.stable}/{result.discrete_plant.stable}")
    click.echo(f"  Difference equation: {result.discrete_plant.difference_equation.text(precision=9)}")
    click.echo(f"  Report: {paths['report']}")


@cli.command("digital-loop")
@click.option("--config", type=click.Path(exists=True, dir_okay=False), default=None)
@click.option("--vbus", type=float, default=None, help="DC bus voltage for the loop work point")
@click.option("--load", "load_fraction", type=float, default=1.0, show_default=True)
@click.option("--sample-us", type=float, default=20.0, show_default=True)
@click.option("--controller", type=click.Choice(["pi", "pif", "2p2z"]), default="pif", show_default=True)
@click.option("--kp", type=float, default=0.01, show_default=True)
@click.option("--ti-ms", type=float, default=1.0, show_default=True)
@click.option("--pif-fc", type=float, default=3500.0, show_default=True, help="PIF output LPF cutoff (Hz)")
@click.option("--b0", type=float, default=0.0)
@click.option("--b1", type=float, default=0.0)
@click.option("--b2", type=float, default=0.0)
@click.option("--a1", type=float, default=0.0, help="2P2Z denominator a1")
@click.option("--a2", type=float, default=0.0, help="2P2Z denominator a2")
@click.option("--pcmd", type=float, default=None, help="Manual PCMD; omitted = infer from work-point frequency")
@click.option("--fm-lut", type=click.Path(exists=True, dir_okay=False), default=None,
              help="CSV/text PCMD LUT. Default is the 20-point firmware TBPRD table.")
@click.option("--fm-mode", type=click.Choice(["tbprd", "frequency"]), default="tbprd", show_default=True)
@click.option("--timer-mhz", type=float, default=120.0, show_default=True)
@click.option("--count-mode", type=click.Choice(["up-down", "up"]), default="up-down", show_default=True)
@click.option("--cla-delay-us", type=float, default=1.0, show_default=True)
@click.option("--adc-conversion-cycles", type=float, default=13.0, show_default=True)
@click.option("--output", type=click.Path(file_okay=False), default="output/llc_digital_loop", show_default=True)
def digital_loop(config, vbus, load_fraction, sample_us, controller, kp, ti_ms,
                 pif_fc, b0, b1, b2, a1, a2, pcmd, fm_lut, fm_mode,
                 timer_mhz, count_mode, cla_delay_us, adc_conversion_cycles,
                 output):
    """Build the complete PI/PIF/2P2Z + FM + ADC + LLC voltage loop."""
    spec = _base_spec(config)
    system = LLCSystemAnalyzer().analyze(spec)
    sample_time_s = sample_us * 1e-6
    small = build_small_signal_analysis(
        spec,
        vbus_v=spec.vbus_nom_v if vbus is None else vbus,
        load_fraction=load_fraction,
        sample_time_s=sample_time_s,
        control_input_kind=ControlInputKind.FREQUENCY_HZ,
        timer_clock_hz=timer_mhz * 1e6,
        system_analysis=system,
    )
    if controller == "pi":
        controller_cfg = PIControllerConfig(
            kp=kp, ti_s=ti_ms * 1e-3, sample_time_s=sample_time_s)
    elif controller == "pif":
        controller_cfg = PIFControllerConfig(
            kp=kp, ti_s=ti_ms * 1e-3, lpf_cutoff_hz=pif_fc,
            sample_time_s=sample_time_s)
    else:
        controller_cfg = TwoP2ZControllerConfig(
            b0=b0, b1=b1, b2=b2, a1=a1, a2=a2,
            sample_time_s=sample_time_s)

    mode = FMLUTMode.PCMD_TO_TBPRD if fm_mode == "tbprd" else FMLUTMode.PCMD_TO_FREQUENCY
    pwm_mode = PWMCountMode.UP_DOWN if count_mode == "up-down" else PWMCountMode.UP
    if fm_lut:
        lut = FrequencyModulatorLUT.from_text(
            Path(fm_lut).read_text(encoding="utf-8"),
            mode=mode,
            timer_clock_hz=timer_mhz * 1e6,
            count_mode=pwm_mode,
        )
    else:
        lut = FrequencyModulatorLUT.firmware_default()
        if timer_mhz != 120.0 or pwm_mode != PWMCountMode.UP_DOWN:
            lut = FrequencyModulatorLUT(
                lut.pcmd, lut.values, lut.mode,
                timer_mhz * 1e6, pwm_mode, lut.name)

    result = build_digital_loop_analysis(
        small,
        controller_config=controller_cfg,
        fm_lut=lut,
        command_pu=pcmd,
        analog_sense=AnalogSenseConfig(
            rup_ohm=117e3,
            rlow_ohm=1.6e3,
            divider_capacitance_f=1e-9,
            opamp_gain=1.0,
            adc_series_resistance_ohm=220.0,
            adc_shunt_capacitance_f=2e-9,
        ),
        adc_sampling=ADCSamplingConfig(
            control_sample_time_s=sample_time_s,
            adc_clock_hz=60e6,
            acquisition_time_s=300e-9,
            conversion_cycles=adc_conversion_cycles,
            soc_count=3,
            recursive_previous_weight=0.25,
        ),
        command_timing=CommandTimingConfig(
            computation_delay_s=cla_delay_us * 1e-6),
    )
    paths = export_digital_loop_analysis(result, output)
    margin = result.margins_nominal_delay
    click.echo("LLC complete digital voltage loop")
    click.echo(f"  Controller: {result.controller.name}")
    click.echo(f"  PCMD: {result.fm_operating_point.command_pu:.8g}")
    click.echo(f"  Kfm: {result.fm_operating_point.gain_hz_per_pu:.9g} Hz/pu")
    click.echo(f"  Gain crossover: {margin.critical_gain_crossover_hz}")
    click.echo(f"  Phase margin: {margin.phase_margin_deg}")
    click.echo(f"  Gain margin: {margin.gain_margin_db}")
    click.echo(f"  Discrete closed-loop stable: {result.discrete_approximation.stable}")
    click.echo(f"  Output: {paths['settings_json']}")


@cli.command("gui")
@click.option("--config", type=click.Path(exists=True, dir_okay=False), default=None)
def gui(config):
    """Launch the optional PySide6 engineering GUI."""
    try:
        from .gui.app import run_gui
    except ImportError as exc:
        raise click.ClickException(
            "GUI dependencies are not installed. Run: pip install -e '.[gui]'"
        ) from exc
    raise SystemExit(run_gui(config))


@cli.command()
@click.option("--config", type=click.Path(exists=True, dir_okay=False), default=None)
@click.option("--profile", type=click.Choice(["current", "quick", "full"]), default="quick",
              show_default=True)
@click.option("--half-bridge", is_flag=True, default=False,
              help="Optimize a half-bridge LLC and scale the turns search accordingly")
@click.option("--max-candidates", type=click.IntRange(1, 4096), default=None,
              help="Candidate bound; two-stage supports at most 128 Stage-A candidates")
@click.option("--top", type=click.IntRange(1, 100), default=10, show_default=True)
@click.option("--two-stage", is_flag=True, default=False,
              help="Explicitly run bounded FHA screening then actual TD-loss verification; requires a new output directory")
@click.option("--verification-scope", type=click.Choice(["nominal", "critical"]), default="critical", show_default=True,
              help="With --two-stage: nominal verifies only the nominal point; critical verifies the critical set")
@click.option("--top-n", type=click.IntRange(1, 8), default=3, show_default=True,
              help="With --two-stage: keep Top N after examining at most 4*N feasible candidates")
@click.option("--candidate-rank", type=click.IntRange(1, 8), default=1, show_default=True,
              help="With --two-stage: export this 1-based ranked, verified candidate")
@click.option("--output", type=click.Path(file_okay=False), default="output/llc_optimization",
              show_default=True)
def optimize(config, profile, half_bridge, max_candidates, top, output,
             two_stage, verification_scope, top_n, candidate_rank):
    """Run multi-variable tank/turns/device/SR optimization."""
    spec = _base_spec(config)
    if half_bridge:
        spec = spec.clone(primary_topology=PrimaryTopology.HALF_BRIDGE,
                          primary_turns=max(1, round(spec.primary_turns / 2)))
    if not two_stage:
        from click.core import ParameterSource
        context = click.get_current_context()
        if any(context.get_parameter_source(name) == ParameterSource.COMMANDLINE
               for name in ("verification_scope", "top_n", "candidate_rank")):
            raise click.ClickException("--verification-scope/--top-n/--candidate-rank require --two-stage")
    cfg = (OptimizationConfig(
        (spec.ln_ratio,), (spec.q_full_load,), (spec.resonant_frequency_hz,),
        (spec.primary_turns,), (spec.secondary_turns,), (spec.primary_device,),
        (spec.sr_parallel_devices_per_position,)) if profile == "current" else
        OptimizationConfig.quick() if profile == "quick" else OptimizationConfig.full())
    if spec.primary_topology == PrimaryTopology.HALF_BRIDGE and profile != "current":
        cfg = replace(cfg, primary_turn_values=tuple(max(1, round(n / 2))
                                                     for n in cfg.primary_turn_values))
    if two_stage:
        _optimize_two_stage(spec, cfg, max_candidates, top_n, candidate_rank, verification_scope, output)
        return
    click.echo(f"Running {profile} sweep: up to {cfg.count} candidates (Stage A / FHA)")
    result = LLCOptimizer().run(spec, cfg, max_candidates)
    out = Path(output)
    out.mkdir(parents=True, exist_ok=True)
    result.table.to_csv(out / "optimization_all.csv", index=False)
    result.pareto.to_csv(out / "optimization_pareto.csv", index=False)
    if result.best_analysis is not None:
        export_calculation_book(result.best_analysis, out / "best_design")
    columns = [c for c in ["feasible", "weighted_loss_w", "nominal_efficiency_pct",
                           "ln", "q_full", "fr_khz", "primary_turns",
                           "secondary_turns", "lr_uh", "cr_nf", "lm_uh",
                           "transformer_core", "inductor_core", "minimum_zvs_margin"]
               if c in result.table.columns]
    click.echo(result.table.loc[:, columns].head(top).to_string(index=False))
    click.echo(f"Results: {out / 'optimization_all.csv'}")


def _optimize_two_stage(spec, cfg, maximum_candidates, top_n, candidate_rank, scope, output):
    """CLI adapter over the same bounded engine and provenance as the desktop."""
    import json
    from dataclasses import asdict
    import pandas as pd
    from .control.design_session import encode_model
    from .optimization.two_stage import (DEFAULT_WEIGHTS, SESSION_MODEL_REVISION,
        TwoStageLLCOptimizer, candidate_provenance)
    bound = min(cfg.count, 128) if maximum_candidates is None else maximum_candidates
    if bound > 128:
        raise click.ClickException("Two-stage Stage-A bound must be 1..128")
    if candidate_rank > top_n:
        raise click.ClickException("--candidate-rank must not exceed --top-n")
    # A two-stage result directory is one immutable request publication. Never
    # replace current rejection metadata alongside a previous successful report.
    out = Path(output)
    try:
        out.mkdir(parents=True, exist_ok=False)
    except FileExistsError as exc:
        raise click.ClickException("Two-stage requires a new output directory; existing files are unchanged: " + str(out)) from exc
    status_path = out / "run_status.json"
    def status(state, **details):
        status_path.write_text(json.dumps({"state": state, **details}, ensure_ascii=False,
                                         indent=2, allow_nan=False), encoding="utf-8")
    status("RUNNING", message="Incomplete request; no current accepted report yet")
    solver = TimeDomainConfig(samples_per_cycle=256, output_cycles=1,
        maximum_settling_cycles=120, shooting_max_evaluations=60, frequency_scan_points=5)
    click.echo(f"Two-stage: Stage A <= {bound}; Stage B <= {top_n * 4}; TD scope={scope}")
    if scope == "nominal":
        click.echo("Only nominal-point TD losses are verified; all other point losses remain FHA.")
    try:
        result = TwoStageLLCOptimizer().run(spec, cfg, maximum_candidates=bound,
            top_n=top_n, verification_scope=scope, time_domain=solver)
    except (ValueError, TypeError) as exc:
        status("FAILED", error=str(exc))
        raise click.ClickException(str(exc)) from exc
    result.stage_a.table.to_csv(out / "optimization_all.csv", index=False)
    result.stage_a.pareto.to_csv(out / "optimization_pareto.csv", index=False)
    accepted = [candidate for candidate in result.verified if not candidate.rejected]
    selected = accepted[candidate_rank - 1] if candidate_rank <= len(accepted) else None
    request = {"base_spec": encode_model(spec), "config": asdict(cfg),
        "maximum_candidates": bound, "top_n": top_n, "ranking_weights": dict(DEFAULT_WEIGHTS),
        "time_domain": asdict(solver), "verification_scope": scope}
    provenance = {"model_revision": SESSION_MODEL_REVISION, "request": request,
        "warnings": list(result.warnings), "candidates": [candidate_provenance(c) for c in result.verified],
        "selected_candidate_id": candidate_provenance(selected)["candidate_id"] if selected else None}
    record = out / "two_stage_verification.json"
    record.write_text(json.dumps(provenance, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    ranks = {id(candidate): rank for rank, candidate in enumerate(accepted, start=1)}
    rows = [{"rank": ranks.get(id(c)),
        "rejected": c.rejected, "reason": c.reject_reason, "ln": c.spec.ln_ratio,
        "q_full": c.spec.q_full_load, "fr_hz": c.spec.resonant_frequency_hz,
        "td_scope": scope, "td_scope_worst_loss_w": c.rank_metrics.get("total_loss_w"),
        "td_scope_minimum_efficiency": c.rank_metrics.get("efficiency"), "zvs_margin_worst": c.zvs_margin_worst,
        "fha_td_validity": c.fha_td_validity.value} for c in result.verified]
    table = pd.DataFrame(rows)
    table.to_csv(out / "two_stage_candidates.csv", index=False)
    if not table.empty:
        click.echo(table.to_string(index=False))
    for warning in result.warnings:
        click.echo(warning)
    if selected is None or selected.analysis is None:
        status("REJECTED", selected_candidate_id=None, evidence=record.name)
        raise click.ClickException(f"No verified candidate at rank {candidate_rank}; rejection/UNKNOWN evidence: {record}")
    directory = out / "selected_candidate"
    paths = export_calculation_book(selected.analysis, directory)
    (directory / "optimization_provenance.json").write_text(json.dumps({
        "model_revision": SESSION_MODEL_REVISION, "request": request,
        "selected_candidate": candidate_provenance(selected)}, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    status("COMPLETE", selected_candidate_id=provenance["selected_candidate_id"], evidence=record.name,
           report=str(Path(paths["report"]).relative_to(out)))
    click.echo(f"Selected rank {candidate_rank}, TD scope={scope}; report: {paths['report']}")
    click.echo(f"Evidence: {record}")


@cli.command("write-template")
@click.option("--path", type=click.Path(dir_okay=False), default="llc_baseline.json",
              show_default=True)
def write_template(path):
    """Write the agreed 400 V -> 53 V / 3 kW baseline JSON input."""
    output = save_spec(LLCDesignSpec(), path)
    click.echo(str(output))


@cli.command()
def devices():
    """List bundled reference MOSFET records."""
    db = DeviceDatabase()
    click.echo("Primary devices")
    for d in db.primary:
        click.echo(f"  {d.part_number:<22} {d.vds_max_v:>4.0f} V  Rds25={d.rds_on_25_ohm*1e3:>5.1f} mOhm  {d.technology}")
    click.echo("SR devices")
    for d in db.sr:
        click.echo(f"  {d.part_number:<22} {d.vds_max_v:>4.0f} V  Rds25={d.rds_on_25_ohm*1e3:>5.2f} mOhm  {d.technology}")


@cli.command()
@click.option("--family", type=click.Choice(["PQ", "EE", "EC", "EER", "ETD"]), default=None)
@click.option("--purpose", type=click.Choice(["transformer", "inductor"]), default=None)
def cores(family, purpose):
    """List standard PQ/EE/EC/EER/ETD magnetic-core records."""
    db = CoreDatabase()
    rows = db.cores
    if family:
        rows = [core for core in rows if core.family == family]
    if purpose:
        rows = [core for core in rows if core.supports(purpose)]
    for core in rows:
        click.echo(
            f"{core.part_number:<24} {core.family:<4} {core.shape:<12} "
            f"{core.material:<5} Ae/Amin={core.ae_mm2:>5.0f}/{core.amin_mm2:<5.0f} mm2  "
            f"Aw={core.aw_mm2:>5.0f} mm2  Ve={core.ve_mm3/1000:>6.1f} cm3  "
            f"{','.join(core.purposes)}")


@cli.command()
def materials():
    """List ferrite material models and fitted operating ranges."""
    db = CoreDatabase().material_db
    for material in db.materials:
        f0, f1 = material.frequency_range_hz
        b0, b1 = material.flux_range_t
        click.echo(
            f"{material.key:<22} {material.manufacturer:<16} {material.grade:<10} "
            f"f={f0/1e3:.0f}..{f1/1e3:.0f} kHz  B={b0:.2f}..{b1:.2f} T  "
            f"Bsat100={material.bsat_at(100.0):.3f} T")



@cli.command("v8-sr")
@click.option("--config", type=click.Path(exists=True, dir_okay=False), default=None)
@click.option("--vbus", type=float, default=None)
@click.option("--load", "load_fraction", type=float, default=1.0, show_default=True)
@click.option("--output", type=click.Path(file_okay=False), default="output/v8_sr", show_default=True)
def v8_sr(config, vbus, load_fraction, output):
    """Solve Golden HB SR timing, Qrr/third-quadrant loss and emit a C99 LUT row."""
    spec=_base_spec(config); vin=spec.vbus_nom_v if vbus is None else float(vbus)
    req=LLCAnalysisRequest(spec,vbus_v=vin,load_fraction=load_fraction,waveform_cycles=1,samples_per_cycle=1024)
    result=solve_harmonic_balance(req,HarmonicBalanceConfig(samples_per_cycle=1024,output_cycles=1,max_harmonic=7))
    device=DeviceDatabase().get_sr(spec.sr_device); sr=analyze_sr(result,spec,device)
    out=Path(output); out.mkdir(parents=True,exist_ok=True)
    row={"vin_v":vin,"load_fraction":load_fraction,"fs_hz":sr.timing.switching_frequency_hz,"on_delay_ns":sr.timing.on_delay_s*1e9,"off_advance_ns":sr.timing.off_advance_s*1e9,"conduction_ns":sr.timing.conduction_s*1e9}
    export_sr_lut_c99((row,),out/"llc_sr_lut.c")
    click.echo(f"SR timing: ON delay={row['on_delay_ns']:.2f} ns, OFF advance={row['off_advance_ns']:.2f} ns, conduction={row['conduction_ns']:.2f} ns")
    click.echo(f"SR loss: total={sr.loss.total_w:.3f} W, channel={sr.loss.channel_w:.3f} W, diode={sr.loss.body_diode_w:.3f} W, Qrr={sr.loss.reverse_recovery_w:.3f} W, third-Q={sr.loss.third_quadrant_w:.3f} W")
    click.echo(str(out/"llc_sr_lut.c"))


@cli.command("v8-dcm")
@click.option("--config", type=click.Path(exists=True, dir_okay=False), default=None)
@click.option("--vbus", type=float, default=None)
@click.option("--load", "load_fraction", type=float, default=0.10, show_default=True)
@click.option("--samples", type=int, default=1024, show_default=True)
def v8_dcm(config, vbus, load_fraction, samples):
    """Run the ON+/OPEN/ON- rectifier complementarity solver."""
    spec=_base_spec(config); vin=spec.vbus_nom_v if vbus is None else float(vbus)
    r=solve_complementarity_time_domain(LLCAnalysisRequest(spec,vbus_v=vin,load_fraction=load_fraction,waveform_cycles=1,samples_per_cycle=samples),ComplementarityTimeDomainConfig(samples_per_cycle=samples,output_cycles=1))
    click.echo(f"Converged={r.convergence.converged}, mismatch={r.convergence.residual_norm:.3e}, Vo={r.metrics.output_voltage_v:.4f} V")
    click.echo(f"DCM OPEN fraction={100*float(r.diagnostics['dcm_open_fraction']):.2f}%")


@cli.command("interleaved")
@click.option("--config", type=click.Path(exists=True, dir_okay=False), default=None)
@click.option("--phases", type=click.Choice(["2","3"]), default="2", show_default=True)
@click.option("--vbus", type=float, default=None)
@click.option("--load", "load_fraction", type=float, default=1.0, show_default=True)
def interleaved(config, phases, vbus, load_fraction):
    """Analyze fixed 90deg 2-phase or fixed 120deg 3-phase interleaved LLC."""
    spec=_base_spec(config); n=int(phases)
    r=solve_interleaved_llc(spec,n,vbus_v=vbus,load_fraction=load_fraction,samples_per_cycle=512)
    click.echo(f"Offsets: {r.phase_offsets_deg}")
    click.echo(f"Cout Irms={r.output_capacitor_rms_a:.4f} A, Vo ripple={r.output_ripple_vpp*1e3:.3f} mVpp, bus ripple={r.input_bus_ripple_rms_a:.4f} A")
    click.echo(f"Current-share imbalance={r.current_share_imbalance_percent:.6f}%")


@cli.command("v8-magnetics")
@click.option("--config", type=click.Path(exists=True, dir_okay=False), default=None)
@click.option("--vbus", type=float, default=None)
@click.option("--load", "load_fraction", type=float, default=1.0, show_default=True)
def v8_magnetics(config, vbus, load_fraction):
    """Evaluate transformer loss/parasitics from the Golden HB waveform."""
    spec=_base_spec(config); vin=spec.vbus_nom_v if vbus is None else float(vbus)
    system=LLCSystemAnalyzer().analyze(spec)
    result=solve_harmonic_balance(LLCAnalysisRequest(spec,vbus_v=vin,load_fraction=load_fraction,waveform_cycles=1,samples_per_cycle=1024))
    m=analyze_golden_magnetics(spec,result,system.transformer)
    click.echo(f"Core={m.core_loss_w:.3f} W, CuP={m.primary_copper_w:.3f} W, CuS={m.secondary_copper_w:.3f} W, total={m.total_loss_w:.3f} W")
    click.echo(f"Bpk={m.b_peak_t*1e3:.2f} mT, Llk={m.leakage_inductance_h*1e6:.3f} uH, Cps={m.primary_secondary_capacitance_f*1e12:.1f} pF, hotspot={m.hotspot_c:.1f} C")


@cli.command("closed-loop-selftest")
@click.option("--controller", "controller_name", type=click.Choice(["pi","pif","pid","pidf","2p2z"]), default="pi", show_default=True)
@click.option("--control-khz", type=float, default=40.0, show_default=True)
@click.option("--kp", type=float, default=0.06, show_default=True)
@click.option("--ti-ms", type=float, default=3.0, show_default=True)
@click.option("--td-us", type=float, default=100.0, show_default=True)
@click.option("--lpf-khz", type=float, default=3.5, show_default=True)
@click.option("--duration-ms", type=float, default=30.0, show_default=True)
def closed_loop_selftest(controller_name, control_khz, kp, ti_ms, td_us, lpf_khz, duration_ms):
    """Run the V9 digital scheduler against a deterministic inverse-gain LLC surrogate.

    This regression validates Sampler -> C(z) -> FM/TBPRD -> event scheduling
    without requiring an external simulator.  It is a digital-control runtime
    test, not a power-stage accuracy result.
    """
    fs = control_khz * 1e3
    kind = ToolControllerKind(controller_name)
    kwargs = {"kp": kp, "ti_s": ti_ms * 1e-3, "td_s": td_us * 1e-6,
              "lpf_pole_hz": lpf_khz * 1e3, "gain": kp,
              "fz1_hz": 300.0, "fz2_hz": 1200.0, "fp1_hz": 4000.0, "fp2_hz": 12000.0}
    analog = design_controller(kind, **kwargs)
    controller = discretize_transfer_function(analog, fs, DiscretizationMethod.TUSTIN)
    plant = FirstOrderLLCPlant()
    mod = LLCFMConfig(mode=LLCFMMode.LINEAR_FM, nominal_frequency_hz=100_000.0,
                      kfm_hz_per_unit=-20_000.0, minimum_frequency_hz=70_000.0,
                      maximum_frequency_hz=180_000.0, tbclk_hz=120e6,
                      count_mode=SimPWMCountMode.UP_DOWN)
    linear = analyze_linear_closed_loop(controller, plant.discrete_transfer(fs),
                                        modulator_gain=mod.kfm_hz_per_unit, delay_samples=1)
    result = run_closed_loop(
        plant,
        controller=controller,
        controller_limits=ControllerLimitConfig(-0.8, 0.8),
        sampler=SamplerConfig(sample_rate_hz=fs, sample_phase_s=min(2e-6, 0.2/fs),
                              gain=0.05, adc_bits=12, adc_min=0.0, adc_max=3.3),
        modulator=mod,
        timing=ClosedLoopTiming(duration_s=duration_ms*1e-3,
                                computation_delay_s=1e-6, pwm_update_delay_s=2e-6),
        scenario=ClosedLoopScenario(reference_v=StepProfile(53.0, 5e-3, 54.0)),
    )
    d = result.diagnostics
    click.echo("V9 closed-loop scheduler self-test (digital-control runtime)")
    click.echo(f"Linear closed-loop: stable={linear.stable}, pole_radius={linear.max_pole_radius:.8f}, PM={linear.phase_margin_deg}, GM={linear.gain_margin_db}, Fc={linear.crossover_hz}")
    click.echo(f"Event simulation: converged={d.converged}, error={d.regulation_error_v:.5f} V, std={d.output_std_v:.5f} V, overshoot={d.overshoot_percent:.3f}%")
    click.echo(f"Saturation: controller={100*d.controller_saturation_fraction:.2f}%, modulator={100*d.modulator_saturation_fraction:.2f}%")
    click.echo(f"Limit-cycle={d.limit_cycle_detected}, dominant oscillation={d.dominant_oscillation_hz}")
    for note in d.notes:
        click.echo(f"  - {note}")


if __name__ == "__main__":
    cli()

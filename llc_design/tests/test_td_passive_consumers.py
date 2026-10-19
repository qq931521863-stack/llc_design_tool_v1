"""Independent passive observers and actual producer-to-consumer regressions.

Synthetic orthogonal harmonics are not simulated hardware. The separate real
switched-solver case exercises production TD arrays, system totals and export.
"""
import math
from dataclasses import replace
from types import SimpleNamespace

import numpy as np
import pytest
from scipy.integrate import quad
from scipy.special import jv

from llc_design.analysis.time_domain import TimeDomainConfig, solve_time_domain
from llc_design.analysis.types import LLCAnalysisRequest
from llc_design.core.spec import LLCDesignSpec, PrimaryTopology
from llc_design.core.tank import design_tank
from llc_design.dynamics.periodic_samples import periodic_samples, transformer_flux
from llc_design.dynamics.waveforms import WaveformBundle, WaveformSignal
from llc_design.models.capacitors import capacitor_results_from_waveform
from llc_design.models.system import LLCSystemAnalyzer
from llc_design.report.export import operating_point_rows, write_markdown_report


def bundle(spec, *, cycles=1, samples=4096, fs=137_000.):
    x = 2 * np.pi * np.arange(samples * cycles) / samples
    omega = 2 * np.pi * fs
    current = 10 * np.sin(x) + 3 * np.cos(3 * x) + 1.5 * np.sin(17 * x)
    magnetizing = 300 / (omega * design_tank(spec).lm_h) * np.sin(x)
    secondary = spec.turns_ratio * (current - magnetizing)
    ico = 6 * np.sin(2 * x) + 2 * np.cos(4 * x)
    internal = spec.vout_v - 6 / (2 * omega * spec.output_capacitance_f) * np.cos(2 * x)
    internal += 2 / (4 * omega * spec.output_capacitance_f) * np.sin(4 * x)
    data = {
        "i_resonant": ("A", current), "i_transformer_secondary": ("A", secondary),
        "i_magnetizing": ("A", magnetizing),
        "v_transformer_primary": ("V", 300 * np.cos(x)),
        "v_resonant_cap": ("V", 120 * np.sin(x) + 15 * np.cos(3 * x)),
        "i_output_cap": ("A", ico), "v_output_cap_internal": ("V", internal),
        "v_output": ("V", internal + spec.output_cap_esr_ohm * ico),
    }
    signals = {key: WaveformSignal(key, key, unit, values, SimpleNamespace(rms=-99.))
               for key, (unit, values) in data.items()}
    return WaveformBundle(np.arange(samples * cycles) / (samples * fs), fs, "analytic_oracle", signals)


@pytest.fixture(scope="module")
def context():
    # Fix temperature for independent resistance/field arithmetic; no material
    # or core vendor data is fabricated or fitted by the observer.
    spec = LLCDesignSpec(ambient_temperature_c=100., winding_temperature_c=100.,
        transformer_rth_k_per_w=0., resonant_inductor_rth_k_per_w=0.,
        litz_transposition_quality=1., include_gap_fringing_loss=False)
    analyzer = LLCSystemAnalyzer()
    base = analyzer.analyze(spec, work_points=[(400., 1.)])
    return analyzer, base


@pytest.mark.parametrize("topology", list(PrimaryTopology))
def test_capacitor_power_and_bias_from_orthogonal_harmonics(context, topology):
    _, base = context
    spec = base.spec.clone(primary_topology=topology)
    waveform = bundle(spec, cycles=3)
    cr, co = capacitor_results_from_waveform(spec, base.nominal.operating_point,
                                            waveform, td_converged=True)
    assert cr.current_rms_a**2 == pytest.approx((100 + 9 + 2.25) / 2)
    assert cr.loss_w == pytest.approx((100 + 9 + 2.25) / 2 * spec.resonant_cap_esr_ohm)
    assert co.capacitor_loss_w == pytest.approx((36 + 4) / 2 * spec.output_cap_esr_ohm)
    assert cr.voltage_rms_v**2 == pytest.approx((120**2 + 15**2) / 2)
    bias = 200. if topology == PrimaryTopology.HALF_BRIDGE else 0.
    assert cr.voltage_dc_bias_v == pytest.approx(bias, abs=1e-10)
    assert cr.voltage_peak_v == pytest.approx(np.max(np.abs(waveform.signal("v_resonant_cap").values + bias)))
    # Adaptive integration of capacitor current gives its charge state.
    omega = 2 * np.pi * waveform.switching_frequency_hz
    t = waveform.time_s[:4096]
    charge = np.array([quad(lambda u: 6 * np.sin(2 * omega * u) + 2 * np.cos(4 * omega * u),
                           0., float(v), epsabs=1e-12)[0] for v in t[::16]])
    assert co.charge_pp_c == pytest.approx(np.ptp(charge), rel=3e-4)
    terminal = charge / spec.output_capacitance_f + spec.output_cap_esr_ohm * (
        6 * np.sin(2 * omega * t[::16]) + 2 * np.cos(4 * omega * t[::16]))
    assert co.total_ripple_vpp == pytest.approx(np.ptp(terminal), rel=3e-4)


def test_transformer_faraday_flux_and_igse_use_actual_voltage(context):
    _, base = context
    waveform = bundle(base.spec, cycles=3)
    design = base.transformer
    samples = periodic_samples(waveform, {"v_transformer_primary": "V", "i_magnetizing": "A"}, True)
    b = transformer_flux(samples, design.primary_turns, design.core.ae_m2, base.tank.lm_h)
    peak = 300 / (2 * math.pi * waveform.switching_frequency_hz * design.primary_turns * design.core.ae_m2)
    expected = peak * np.sin(2 * np.pi * waveform.switching_frequency_hz * samples.time_s)
    assert np.max(np.abs(b - expected)) < peak * 3e-7
    loss = design.loss(base.spec, base.nominal.operating_point, waveform=waveform, td_converged=True)
    assert loss.b_peak_t == pytest.approx(peak, rel=3e-7)
    # Continuous sinusoidal iGSE reduces to the material's tabulated Steinmetz
    # coefficients. No production waveform/core-loss function forms this oracle.
    coefficients = next(c for c in design.core.material_spec.steinmetz_points if c.temperature_c == 100.)
    expected_core = (design.core.material_spec.core_loss_correction * coefficients.k
        * waveform.switching_frequency_hz**coefficients.alpha * peak**coefficients.beta * design.core.ve_m3)
    assert loss.core_w == pytest.approx(expected_core, rel=2e-6)



def test_linear_magnetizing_dc_flux_is_preserved(context):
    _, base = context
    waveform = bundle(base.spec)
    signals = dict(waveform.signals)
    for key in ("i_resonant", "i_magnetizing"):
        signal = signals[key]
        signals[key] = replace(signal, values=signal.values + 1.3)
    waveform = replace(waveform, signals=signals)
    design = base.transformer
    biased = design.loss(base.spec, base.nominal.operating_point, waveform=waveform, td_converged=True)
    plain = design.loss(base.spec, base.nominal.operating_point, waveform=bundle(base.spec), td_converged=True)
    offset = base.tank.lm_h * 1.3 / (design.primary_turns * design.core.ae_m2)
    assert biased.b_dc_t == pytest.approx(offset)
    assert biased.b_peak_t == pytest.approx(plain.b_peak_t + offset)
    # The existing iGSE material law is shift-invariant, not a fabricated
    # DC-bias core-loss correction. Flux stress still retains the offset.
    assert biased.core_w == pytest.approx(plain.core_w)

def _layer_field_oracle(design, spec, waveform):
    """Direct Fourier projections + independent Gauss spatial field integral."""
    n = 4096
    phase = 2 * np.pi * np.arange(n) / n
    currents = {"primary": waveform.signal("i_resonant").values[-n:],
                "secondary": -waveform.signal("i_transformer_secondary").values[-n:]}
    layers = []
    groups = [("primary", design.primary_turns // 2, design.primary_turns_per_layer),
              ("secondary", design.secondary_turns, design.secondary_turns_per_layer),
              ("primary", design.primary_turns - design.primary_turns // 2, design.primary_turns_per_layer)]
    for name, turns, limit in groups:
        while turns:
            take = min(turns, limit)
            layers.append((name, take))
            turns -= take
    # Five-point Gauss integration of the squared complex MMF field, rather
    # than the implementation's closed-form three-term layer expression.
    nodes, weights = np.polynomial.legendre.leggauss(5)
    u = (nodes + 1) / 2
    rho = 1.724e-8 * (1 + 0.00393 * 80)
    result = {key: {"dc": 0., "skin": 0., "prox": 0.} for key in currents}
    for name, turns in layers:
        wire = getattr(design, f"{name}_wire")
        length = turns * getattr(design.core, f"mlt_{name}_mm") * 1e-3
        resistance = rho * length / (wire.strand_count * math.pi * wire.strand_copper_diameter_m**2 / 4)
        result[name]["dc"] += np.dot(currents[name], currents[name]) / n * resistance
    for harmonic in range(1, spec.litz_max_harmonic + 1):
        phasors = {key: np.sqrt(2) / n * np.dot(value, np.exp(-1j * harmonic * phase))
                   for key, value in currents.items()}
        boundary = 0j
        f = harmonic * waveform.switching_frequency_hz
        for name, turns in layers:
            wire = getattr(design, f"{name}_wire")
            length = turns * getattr(design.core, f"mlt_{name}_mm") * 1e-3
            resistance = rho * length / wire.copper_area_m2
            change = turns * phasors[name]
            h2 = np.dot(weights, abs((boundary + u * change) / design.core.window_width_m)**2) / 2
            boundary += change
            radius = wire.strand_copper_diameter_m / 2
            delta = np.sqrt(rho / (math.pi * f * 4e-7 * math.pi))
            z = (1 - 1j) * radius / delta
            skin = (z / 2 * jv(0, z) / jv(1, z)).real
            result[name]["skin"] += abs(phasors[name])**2 * resistance * (skin - 1)
            result[name]["prox"] += (math.pi * radius**4 * (2 * math.pi * f)**2 * (4e-7 * math.pi)**2
                * h2 / (4 * rho) * min(skin, 8) * wire.strand_count * length)
    return result


def test_magnetic_rms_harmonics_and_secondary_dot_polarity(context):
    _, base = context
    waveform = bundle(base.spec, cycles=3)
    expected = _layer_field_oracle(base.transformer, base.spec, waveform)
    loss = base.transformer.loss(base.spec, base.nominal.operating_point, waveform=waveform, td_converged=True)
    for name in ("primary", "secondary"):
        assert getattr(loss, f"{name}_dc_w") == pytest.approx(expected[name]["dc"], rel=1e-12)
        assert getattr(loss, f"{name}_skin_w") == pytest.approx(expected[name]["skin"], rel=1e-9)
        assert getattr(loss, f"{name}_proximity_w") == pytest.approx(expected[name]["prox"], rel=1e-10)
    inductor = base.resonant_inductor
    iloss = inductor.loss(base.spec, base.nominal.operating_point, waveform=waveform, td_converged=True)
    rho = 1.724e-8 * (1 + 0.00393 * 80)
    rdc = rho * inductor.turns * inductor.core.mlt_primary_mm * 1e-3 / inductor.wire.copper_area_m2
    assert iloss.dc_copper_w == pytest.approx((100 + 9 + 2.25) / 2 * rdc, rel=1e-12)
    assert iloss.b_peak_t == pytest.approx(inductor.inductance_h * np.max(np.abs(
        waveform.signal("i_resonant").values)) / (inductor.turns * inductor.core.ae_m2))
    # Three periods must not cause fs/3 Fourier bin interpretation.
    single = base.transformer.loss(base.spec, base.nominal.operating_point,
                                  waveform=bundle(base.spec), td_converged=True)
    assert loss.total_w == pytest.approx(single.total_w, rel=1e-10)


@pytest.mark.parametrize("fault", ["missing", "unit", "nan", "partial", "frequency", "nonuniform", "unconverged", "flux_walk", "state_mismatch", "cycle_drift"])
def test_passive_source_guards(context, fault):
    _, base = context
    waveform = bundle(base.spec, cycles=2)
    signals = dict(waveform.signals)
    if fault == "state_mismatch":
        sig = signals["i_magnetizing"]
        signals["i_magnetizing"] = replace(sig, values=sig.values * 1.2)
    elif fault == "cycle_drift":
        sig = signals["i_resonant"]
        values = sig.values.copy(); values[4096:] *= 1.001
        signals["i_resonant"] = replace(sig, values=values)
    elif fault == "missing":
        del signals["v_transformer_primary"]
    elif fault == "unit":
        signals["v_transformer_primary"] = replace(signals["v_transformer_primary"], unit="mV")
    elif fault in ("nan", "flux_walk"):
        sig = signals["v_transformer_primary"]
        signals["v_transformer_primary"] = replace(sig, values=sig.values + (np.nan if fault == "nan" else 30.))
    elif fault == "partial":
        waveform = replace(waveform, time_s=waveform.time_s[:-1])
    elif fault == "frequency":
        waveform = replace(waveform, switching_frequency_hz=137_000.123)
    elif fault == "nonuniform":
        time = waveform.time_s.copy(); time[5] += 1e-12
        waveform = replace(waveform, time_s=time)
    waveform = replace(waveform, signals=signals)
    with pytest.raises(ValueError):
        base.transformer.loss(base.spec, base.nominal.operating_point, waveform=waveform,
                              td_converged=fault != "unconverged")


@pytest.mark.parametrize("topology", list(PrimaryTopology))
def test_real_td_producer_to_passive_system_and_report(tmp_path, topology, monkeypatch):
    spec = LLCDesignSpec(primary_topology=topology,
                         primary_turns=15 if topology == PrimaryTopology.HALF_BRIDGE else 30)
    analyzer = LLCSystemAnalyzer()
    base = analyzer.analyze(spec, work_points=[(400., 1.)])
    td = solve_time_domain(LLCAnalysisRequest(spec, samples_per_cycle=512, waveform_cycles=2),
                          TimeDomainConfig(samples_per_cycle=512, output_cycles=2))
    assert td.convergence.converged
    analysis = analyzer.with_time_domain_losses(base, [td])
    point = analysis.nominal
    raw = td.waveform
    # Sample-energy sums over all producer cycles, independently from the
    # last-period selection in passive consumers.
    for key, actual, esr in [("i_resonant", point.resonant_capacitor.loss_w, spec.resonant_cap_esr_ohm),
                            ("i_output_cap", point.output_capacitor.capacitor_loss_w, spec.output_cap_esr_ohm)]:
        energy = sum(float(x)**2 * esr for x in raw.signal(key).values) / len(raw.time_s)
        assert actual == pytest.approx(energy, rel=2e-6)
    assert point.transformer.total_w != pytest.approx(base.nominal.transformer.total_w, rel=1e-4)
    assert point.resonant_inductor.total_w != pytest.approx(base.nominal.resonant_inductor.total_w, rel=1e-4)
    assert point.total_loss_w == pytest.approx(sum(point.breakdown().values()))
    from llc_design.dynamics.plant import estimate_equivalent_series_resistance

    conductive = (point.primary.conduction_w + point.synchronous_rectifier.conduction_w
        + point.transformer.primary_copper_w + point.transformer.secondary_copper_w
        + point.resonant_inductor.copper_w + point.resonant_capacitor.loss_w)
    ir_square = np.mean(raw.signal("i_resonant").values[-512:]**2)
    expected_r = conductive / ir_square
    assert 1e-4 < expected_r < 5.0  # no clipping can mask a mismatched denominator
    actual_r = estimate_equivalent_series_resistance(analysis, point.operating_point)
    assert type(actual_r) is float
    assert actual_r == pytest.approx(expected_r)
    assert actual_r != pytest.approx(conductive / point.operating_point.resonant_current_rms_a**2, rel=1e-5)
    assert analysis.feasible == base.feasible  # no implied upgrade of hardware synthesis
    row = operating_point_rows(analysis)[0]
    assert row["magnetic_source"] == "TIME_DOMAIN_WAVEFORM_IGSE_LITZ"
    assert row["capacitor_source"] == "TIME_DOMAIN_WAVEFORM_CONSTANT_ESR"
    assert row["commutation_source"] == "FHA_OPERATING_POINT_APPROXIMATION"
    assert len(row["loss_waveform_sha256"]) == 64
    assert row["passive_frequency_hz"] == raw.switching_frequency_hz
    report = write_markdown_report(analysis, tmp_path / "td-passive.md").read_text(encoding="utf-8")
    assert row["loss_waveform_sha256"] in report
    assert row["capacitor_source"] in report
    assert "commutation/ZVS and hardware feasibility remain FHA" in report
    # Replay gives the same physical identity; a modified sample changes it.
    assert analyzer.with_time_domain_losses(analysis, [td]).nominal.loss_waveform_sha256 == point.loss_waveform_sha256
    signals = dict(raw.signals)
    signals["i_output_cap"] = replace(signals["i_output_cap"], values=signals["i_output_cap"].values * 1.05)
    changed = analyzer.with_time_domain_losses(base, [replace(td, waveform=replace(raw, signals=signals))])
    assert changed.nominal.loss_waveform_sha256 != point.loss_waveform_sha256
    assert changed.nominal.output_capacitor.capacitor_loss_w == pytest.approx(point.output_capacitor.capacitor_loss_w * 1.05**2)
    import pandas as pd

    import llc_design.optimization.two_stage as module

    candidate_row = {"feasible": True, "ln": spec.ln_ratio, "q_full": spec.q_full_load,
        "fr_khz": spec.resonant_frequency_hz / 1000, "primary_turns": spec.primary_turns,
        "secondary_turns": spec.secondary_turns, "primary_device": spec.primary_device,
        "sr_parallel": spec.sr_parallel_devices_per_position, "weighted_loss_w": 1.}
    optimizer = module.TwoStageLLCOptimizer(analyzer)
    monkeypatch.setattr(optimizer.fast, "run", lambda *a, **k: SimpleNamespace(table=pd.DataFrame([candidate_row])))
    monkeypatch.setattr(analyzer, "analyze", lambda *a, **k: base)
    monkeypatch.setattr(module, "_envelope_status", lambda *a: module.ConstraintStatus.PASS)
    monkeypatch.setattr(module, "validate_fha_against_time_domain", lambda *a:
        SimpleNamespace(overall_validity=module.ModelValidity.PASS, points=[SimpleNamespace(td=td)]))
    candidate = optimizer.run(spec, top_n=1).verified[0]
    assert not candidate.rejected
    assert candidate.analysis.nominal.loss_waveform_sha256 == point.loss_waveform_sha256
    assert candidate.rank_metrics["total_loss_w"] == pytest.approx(point.total_loss_w)
    # Valid cached output metrics cannot conceal modified, unregulated samples.
    signals["v_output"] = replace(signals["v_output"], values=signals["v_output"].values + 1.)
    with pytest.raises(ValueError, match="waveform output regulation"):
        analyzer.with_time_domain_losses(base, [replace(td, waveform=replace(raw, signals=signals))])

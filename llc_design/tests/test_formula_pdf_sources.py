"""Worksheet formulas must use the same inputs as each printed loss bucket."""
from dataclasses import replace
import json
import math

import numpy as np
import pytest

from llc_design.report.formula_pdf import build_formula_steps, build_formula_pdf
from llc_design.tests.test_psd_consumer_independent import source_context


def steps_by_name(analysis):
    return {s.name: s for group in build_formula_steps(analysis).values() for s in group}


def test_td_formula_substitution_uses_actual_current_and_frequency(source_context):
    analyzer, base, td = source_context
    analysis = analyzer.with_time_domain_losses(base, [td])
    steps = steps_by_name(analysis)
    ir = np.sqrt(np.mean(td.waveform.signal("i_resonant").values**2))
    isec = np.sqrt(np.mean(td.waveform.signal("i_transformer_secondary").values**2))
    assert f"{ir:.6g}" in steps["Primary conduction loss"].substitution
    assert f"{isec:.6g}" in steps["SR conduction loss"].substitution
    assert f"{ir:.6g}" in steps["Resonant-capacitor ESR loss"].substitution
    for name in ("Primary gate-drive loss", "Primary turn-off loss", "SR gate-drive loss",
                 "SR dead-time diode loss", "SR turn-off loss"):
        assert f"{td.waveform.switching_frequency_hz:.6g}" in steps[name].substitution
    assert "FHA" in steps["Commutation current"].method
    assert analysis.nominal.loss_waveform_sha256 in str(build_formula_steps(analysis))


def test_td_formula_input_trace_is_same_source_and_immutable(source_context):
    analyzer, base, td = source_context
    point = analyzer.with_time_domain_losses(base, [td]).nominal
    trace = point.td_formula_inputs
    assert trace.sample_count == len(td.waveform.time_s)
    assert trace.frequency_hz == td.waveform.switching_frequency_hz
    assert trace.resonant_rms_a == pytest.approx(np.sqrt(np.mean(td.waveform.signal("i_resonant").values**2)))
    bt = base.tank.lm_h * td.waveform.signal("i_magnetizing").values / (base.transformer.primary_turns * base.transformer.core.ae_m2)
    bi = base.resonant_inductor.inductance_h * td.waveform.signal("i_resonant").values / (base.resonant_inductor.turns * base.resonant_inductor.core.ae_m2)
    for inputs, actual, flux in ((trace.transformer, point.transformer, bt),
                                 (trace.inductor, point.resonant_inductor, bi)):
        expected = (inputs.correction * inputs.ki * inputs.mean_abs_dbdt_alpha
                    * inputs.delta_b_t**(inputs.beta-inputs.alpha) * inputs.volume_m3)
        assert expected == pytest.approx(actual.core_w, rel=2e-12)
        assert inputs.temperature_c == actual.estimated_hotspot_c
        assert inputs.delta_b_t == pytest.approx(float(np.ptp(flux)))
        # Independent wrap endpoint and measured source-grid derivative.
        increments = np.diff(np.concatenate((flux, flux[:1])))
        rate = np.mean(np.abs(increments / (td.waveform.time_s[1]-td.waveform.time_s[0]))**inputs.alpha)
        assert inputs.mean_abs_dbdt_alpha == pytest.approx(rate, rel=2e-12)
    with pytest.raises(AttributeError):
        trace.frequency_hz = 1


def test_td_legacy_without_trace_is_explicitly_unavailable(source_context):
    analyzer, base, td = source_context
    updated = analyzer.with_time_domain_losses(base, [td])
    point = replace(updated.nominal, td_formula_inputs=None)
    steps = steps_by_name(replace(updated, operating_points=(point,)))
    assert "NOT_AVAILABLE" in steps["Magnetic core loss by iGSE"].substitution
    assert "4096 samples" not in steps["Output-capacitor ripple"].method
    assert "Ir,rms*Xc" not in steps["Resonant-capacitor voltage"].formula


def _substitution_value(text, target):
    """Evaluate only numeric assignments printed by the worksheet under test."""
    values = {"pi": math.pi, "sin": math.sin, "min": min}
    for assignment in text.split(";"):
        if ":=" not in assignment:
            continue
        name, expression = (part.strip() for part in assignment.split(":=", 1))
        values[name] = eval(expression.replace("^", "**"), {"__builtins__": {}}, values)
        if name == target:
            return values[name]
    raise AssertionError(f"Missing printed assignment {target}: {text}")


@pytest.mark.parametrize("name,target,component,field", [
    ("Primary conduction loss", "Pcond", "primary", "conduction_w"),
    ("Primary gate-drive loss", "Pgate", "primary", "gate_drive_w"),
    ("Primary turn-off loss", "Poff", "primary", "turnoff_w"),
    ("Residual Coss loss after incomplete ZVS", "Pcoss", "primary", "residual_coss_w"),
    ("Primary dead-time diode loss", "Pdiode", "primary", "deadtime_diode_w"),
    ("SR conduction loss", "Psr,cond", "synchronous_rectifier", "conduction_w"),
    ("SR dead-time diode loss", "Pdiode", "synchronous_rectifier", "deadtime_diode_w"),
    ("SR turn-off loss", "Poff", "synchronous_rectifier", "turnoff_w"),
    ("SR output-capacitance loss", "Pcoss", "synchronous_rectifier", "coss_w"),
    ("SR gate-drive loss", "Pgate", "synchronous_rectifier", "gate_drive_w"),
    ("Resonant-capacitor ESR loss", "Pcr", "resonant_capacitor", "loss_w"),
    ("Output-capacitor ripple", "Pcap", "output_capacitor", "capacitor_loss_w"),
])
def test_every_printed_td_loss_expression_reproduces_actual_bucket(source_context, name, target, component, field):
    analyzer, base, td = source_context
    analysis = analyzer.with_time_domain_losses(base, [td])
    step = steps_by_name(analysis)[name]
    actual = getattr(getattr(analysis.nominal, component), field)
    # The report intentionally displays 6 significant digits.
    assert _substitution_value(step.substitution, target) == pytest.approx(actual, rel=3e-5, abs=1e-10)


def test_cycle_summary_and_phasors_come_from_samples_not_cached_metrics(source_context):
    analyzer, base, td = source_context
    wave = td.waveform
    count = len(wave.time_s)
    signals = {key: replace(signal, values=np.tile(signal.values, 2))
               for key, signal in wave.signals.items()}
    ir = signals["i_resonant"].values * 1.4
    signals["i_resonant"] = replace(signals["i_resonant"], values=ir)
    wave = replace(wave, time_s=np.arange(2*count) / (count*wave.switching_frequency_hz), signals=signals)
    # Scalar metrics are deliberately stale; only periodic arrays are inputs.
    analysis = analyzer.with_time_domain_losses(base, [replace(td, waveform=wave)])
    trace = analysis.nominal.td_formula_inputs
    assert trace.sample_count == count
    assert trace.resonant_rms_a == pytest.approx(np.sqrt(np.mean(ir**2)))
    iph = np.fft.rfft(ir[-count:]) / count
    iph[1:] *= np.sqrt(2)
    iph[-1] /= np.sqrt(2)
    for h, pair in enumerate(trace.primary_rms_phasors_a):
        assert complex(*pair) == pytest.approx(iph[h], abs=1e-12)
    assert trace.transformer.temperature_c == analysis.nominal.transformer.estimated_hotspot_c
    from llc_design.optimization.two_stage import VerifiedCandidate, candidate_provenance
    from llc_design.analysis.fha_td_validation import ModelValidity
    from llc_design.core.engineering import ConstraintStatus
    candidate = VerifiedCandidate(analysis.spec, analysis, ConstraintStatus.PASS, ModelValidity.WARN,
        1., analysis.nominal.total_loss_w, analysis.nominal.efficiency, {}, False, "")
    # A bounded request/candidate session does not serialize internal phasors.
    saved = json.dumps(candidate_provenance(candidate))
    assert "td_formula_inputs" not in saved and "primary_rms_phasors" not in saved


def test_pdf_has_mixed_point_sources_and_retains_fha_reference(source_context, tmp_path):
    import pdfplumber
    analyzer, base, td = source_context
    analysis = analyzer.with_time_domain_losses(base, [td])
    # Preserve an untouched FHA point to expose accidental global TD labeling.
    analysis = replace(analysis, operating_points=(analysis.nominal, replace(base.nominal,
        operating_point=replace(base.nominal.operating_point, load_fraction=.5))))
    path = build_formula_pdf(analysis, tmp_path / "td.pdf")
    with pdfplumber.open(path) as pdf:
        text = "\n".join(page.extract_text() or "" for page in pdf.pages)
    assert analysis.nominal.loss_waveform_sha256 in text
    assert "TIME_DOMAIN_WAVEFORM" in text and "FHA_RECONSTRUCTED_WAVEFORM" in text
    assert "retained FHA hardware/synthesis screen" in text
    assert "mean(abs(dB/dt)^alpha)" in text
    assert "Ip=(" in text and "Is_into_dot=(" in text
    assert "Frequency is a numerical root of the FHA" not in text


def test_passive_waveform_statistics_and_printed_igse_expressions(source_context):
    analyzer, base, td = source_context
    analysis = analyzer.with_time_domain_losses(base, [td])
    p = analysis.nominal
    steps = steps_by_name(analysis)
    voltage = td.waveform.signal("v_resonant_cap").values
    assert p.resonant_capacitor.voltage_rms_v == pytest.approx(np.std(voltage))
    assert p.resonant_capacitor.voltage_peak_v == pytest.approx(np.max(np.abs(voltage)))
    co = p.output_capacitor
    assert co.capacitive_ripple_vpp == pytest.approx(np.ptp(td.waveform.signal("v_output_cap_internal").values))
    assert co.total_ripple_vpp == pytest.approx(np.ptp(td.waveform.signal("v_output").values))
    lines = steps["Magnetic core loss by iGSE"].substitution.splitlines()
    for line, actual in zip(lines, (p.transformer.core_w, p.resonant_inductor.core_w)):
        # Discard the explanatory prefix; evaluate only the printed factor product.
        expression = line[line.index("Pcore :="):]
        assert _substitution_value(expression, "Pcore") == pytest.approx(actual, rel=4e-5)
    assert sum(p.breakdown().values()) == pytest.approx(p.total_loss_w)


def test_real_nominal_td_formula_trace_and_pdf(tmp_path):
    """Unmocked physical solve, loss consumers, trace and final PDF exporter."""
    from llc_design.analysis.fha_td_validation import validate_fha_against_time_domain
    from llc_design.analysis.time_domain import TimeDomainConfig
    from llc_design.analysis.types import LLCAnalysisRequest
    from llc_design.core.spec import LLCDesignSpec
    from llc_design.models.system import LLCSystemAnalyzer
    spec = LLCDesignSpec(ln_ratio=5.5, vbus_hold_end_v=320., bus_capacitance_f=.0034)
    request = LLCAnalysisRequest(spec, samples_per_cycle=256, waveform_cycles=1)
    config = TimeDomainConfig(samples_per_cycle=256, output_cycles=1,
        maximum_settling_cycles=120, shooting_max_evaluations=60, frequency_scan_points=5)
    comparison = validate_fha_against_time_domain(spec, requests=[request], time_domain=config)
    td = comparison.points[0].td
    assert td is not None and td.convergence.converged
    analyzer = LLCSystemAnalyzer()
    analysis = analyzer.with_time_domain_losses(analyzer.analyze(spec), [td])
    p = analysis.nominal
    assert p.td_formula_inputs.frequency_hz == td.waveform.switching_frequency_hz
    assert p.operating_point.switching_frequency_hz != pytest.approx(p.td_formula_inputs.frequency_hz)
    steps = steps_by_name(analysis)
    assert _substitution_value(steps["Primary conduction loss"].substitution, "Pcond") == pytest.approx(p.primary.conduction_w, rel=2e-5)
    assert _substitution_value(steps["SR conduction loss"].substitution, "Psr,cond") == pytest.approx(p.synchronous_rectifier.conduction_w, rel=2e-5)
    for inputs, actual in ((p.td_formula_inputs.transformer, p.transformer),
                           (p.td_formula_inputs.inductor, p.resonant_inductor)):
        assert inputs.correction*inputs.ki*inputs.mean_abs_dbdt_alpha*inputs.delta_b_t**(inputs.beta-inputs.alpha)*inputs.volume_m3 == pytest.approx(actual.core_w, rel=2e-12)
    assert sum(point.td_formula_inputs is not None for point in analysis.operating_points) == 1
    assert build_formula_pdf(analysis, tmp_path / "real-td.pdf").read_bytes().startswith(b"%PDF")

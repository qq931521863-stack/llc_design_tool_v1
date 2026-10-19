"""Consumer regressions: synthetic distinguishable data, not hardware validation."""
from dataclasses import replace
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from llc_design.analysis.fha import solve_fha
from llc_design.analysis.types import FidelityLevel, LLCAnalysisRequest
from llc_design.core.spec import LLCDesignSpec
from llc_design.models.devices import DeviceDatabase
from llc_design.models.physics_evidence import ModelGrade
from llc_design.models.system import LLCSystemAnalyzer
from llc_design.models.waveform_loss import (
    bridge_loss_with_provenance,
    rectifier_loss_with_provenance,
)
from llc_design.report.export import operating_point_rows, write_markdown_report


@pytest.fixture(scope="module")
def example():
    spec = LLCDesignSpec()
    analyzer = LLCSystemAnalyzer()
    analysis = analyzer.analyze(spec, work_points=[(spec.vbus_nom_v, 1.0)])
    fha = solve_fha(LLCAnalysisRequest(spec, samples_per_cycle=128, waveform_cycles=1))
    # Deliberately stale cached statistics expose consumers that only relabel
    # metadata rather than reading the supplied physical waveform samples.
    signals = dict(fha.waveform.signals)
    for key in ("i_resonant", "i_transformer_secondary"):
        signal = signals[key]
        signals[key] = replace(signal, values=signal.values * 2.0)
    # The original FHA Im sinusoid is incompatible with its displayed square
    # Vp. Supply a deliberately constructed linear-Lm triangular state for
    # the passive consumer's Faraday check; this remains synthetic evidence.
    phase = np.arange(len(fha.waveform.time_s)) / len(fha.waveform.time_s)
    peak = max(signals["v_transformer_primary"].values) / (
        4 * fha.waveform.switching_frequency_hz * analysis.tank.lm_h)
    signals["i_magnetizing"] = replace(signals["i_magnetizing"],
        values=peak * (1 - 4 * np.abs(phase - 0.5)))
    waveform = replace(fha.waveform, signals=signals)
    td = replace(fha, fidelity=FidelityLevel.SWITCHED_TIME_DOMAIN, waveform=waveform)
    return analyzer, analysis, td


def test_primary_and_sr_really_consume_samples_without_evidence_upgrade(example):
    _analyzer, base, td = example
    point = base.nominal
    db = DeviceDatabase()
    primary = bridge_loss_with_provenance(base.spec, base.tank, point.operating_point,
        db.get_primary(base.spec.primary_device), waveform=td.waveform, td_converged=True)
    sr = rectifier_loss_with_provenance(base.spec, point.operating_point,
        db.get_sr(base.spec.sr_device), waveform=td.waveform, td_converged=True)
    for result in (primary, sr):
        assert result.waveform_source == "TIME_DOMAIN_WAVEFORM"
        assert all(term.model != ModelGrade.VERIFIED for term in result.terms)
        assert result.total_w == pytest.approx(result.legacy.total_w)
    ir = np.sqrt(np.mean(td.waveform.signal("i_resonant").values**2))
    isec = np.sqrt(np.mean(td.waveform.signal("i_transformer_secondary").values**2))
    assert primary.legacy.conduction_w == pytest.approx(ir**2 * base.spec.bridge_series_devices
        * db.get_primary(base.spec.primary_device).rds_at(base.spec.primary_junction_temperature_c)
        / base.spec.primary_parallel_devices)
    assert sr.legacy.conduction_w == pytest.approx(isec**2 * 2
        * db.get_sr(base.spec.sr_device).rds_at(base.spec.sr_junction_temperature_c)
        / base.spec.sr_parallel_devices_per_position)


def test_system_and_report_use_same_td_losses(example, tmp_path):
    analyzer, base, td = example
    updated = analyzer.with_time_domain_losses(base, [td])
    point = updated.nominal
    assert point.primary.conduction_w > base.nominal.primary.conduction_w * 3
    assert point.synchronous_rectifier.conduction_w > base.nominal.synchronous_rectifier.conduction_w * 3
    assert point.total_loss_w == pytest.approx(sum(point.breakdown().values()))
    assert updated.nominal.efficiency < base.nominal.efficiency
    assert point.transformer is not base.nominal.transformer
    assert point.magnetic_source == "TIME_DOMAIN_WAVEFORM_IGSE_LITZ"
    assert point.capacitor_source == "TIME_DOMAIN_WAVEFORM_CONSTANT_ESR"
    assert any("remain FHA" in warning for warning in updated.warnings)
    rows = operating_point_rows(updated)
    assert rows[0]["total_loss_w"] == point.total_loss_w
    assert rows[0]["semiconductor_source"] == "TIME_DOMAIN_WAVEFORM"
    path = write_markdown_report(updated, tmp_path / "report.md")
    assert "TIME_DOMAIN_WAVEFORM" in path.read_text(encoding="utf-8")
    assert f"{point.total_loss_w:.3f}" in path.read_text(encoding="utf-8")


@pytest.mark.parametrize("bad", ["missing", "unconverged", "spec", "signal", "nan", "nonuniform", "regulation"])
def test_requested_td_refuses_invalid_inputs(example, bad):
    analyzer, base, td = example
    if bad == "missing":
        results = []
    else:
        if bad == "unconverged":
            td = replace(td, convergence=replace(td.convergence, converged=False))
        elif bad == "spec":
            td = replace(td, request=replace(td.request, spec=base.spec.clone(pout_w=2900)))
        elif bad == "signal":
            td = replace(td, waveform=replace(td.waveform, signals={}))
        elif bad == "nan":
            signals = dict(td.waveform.signals)
            signal = signals["i_resonant"]
            signals["i_resonant"] = replace(signal, values=np.full_like(signal.values, np.nan))
            td = replace(td, waveform=replace(td.waveform, signals=signals))
        elif bad == "regulation":
            td = replace(td, metrics=replace(td.metrics, output_voltage_v=40.0))
        elif bad == "nonuniform":
            time = td.waveform.time_s.copy(); time[2] += (time[1] - time[0]) / 4
            td = replace(td, waveform=replace(td.waveform, time_s=time))
        results = [td]
    with pytest.raises(ValueError):
        analyzer.with_time_domain_losses(base, results)


def test_stage_b_ranking_consumes_actual_td_losses(example, monkeypatch):
    import llc_design.optimization.two_stage as module
    analyzer, base, td = example
    spec = base.spec
    row = {"feasible": True, "ln": spec.ln_ratio, "q_full": spec.q_full_load,
        "fr_khz": spec.resonant_frequency_hz/1000, "primary_turns": spec.primary_turns,
        "secondary_turns": spec.secondary_turns, "primary_device": spec.primary_device,
        "sr_parallel": spec.sr_parallel_devices_per_position, "weighted_loss_w": 1.}
    opt = module.TwoStageLLCOptimizer(analyzer)
    monkeypatch.setattr(opt.fast, "run", lambda *a, **k: SimpleNamespace(table=pd.DataFrame([row])))
    monkeypatch.setattr(analyzer, "analyze", lambda *a, **k: base)
    monkeypatch.setattr(module, "_envelope_status", lambda *a: module.ConstraintStatus.PASS)
    monkeypatch.setattr(module, "validate_fha_against_time_domain", lambda *a:
        SimpleNamespace(overall_validity=module.ModelValidity.PASS, points=[SimpleNamespace(td=td)]))
    result = opt.run(spec, top_n=1)
    candidate = result.verified[0]
    assert not candidate.rejected
    assert candidate.total_loss_w > base.worst_loss.total_loss_w
    assert candidate.total_loss_w == candidate.analysis.worst_loss.total_loss_w


def test_gui_opt_in_applies_same_result_and_rejects_stale_delivery(example, monkeypatch):
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    widgets = pytest.importorskip("PySide6.QtWidgets")
    import llc_design.gui.main_window as module
    from llc_design.analysis.types import MultiFidelityAnalysis

    app = widgets.QApplication.instance() or widgets.QApplication([])
    analyzer, base, td = example
    window = module.LLCMainWindow(base.spec.clone())
    result = MultiFidelityAnalysis(td.request, {td.fidelity: td}, td.fidelity, ())
    def solve(request):
        selected = replace(td, request=request)
        return MultiFidelityAnalysis(request, {selected.fidelity: selected}, selected.fidelity, ())
    monkeypatch.setattr(module, "LLCGoldenSolver", lambda config: SimpleNamespace(solve=solve))
    monkeypatch.setattr(module, "LLCSystemAnalyzer", lambda: analyzer)
    monkeypatch.setattr(analyzer, "analyze", lambda spec, **k: replace(base, spec=spec))
    monkeypatch.setattr(window, "_run_worker", lambda label, calculate, callback: callback(calculate()))
    window.model_comparison_view.apply_td_losses.setChecked(True)
    window.run_model_comparison(window.model_comparison_view.options())
    assert window.system_analysis.nominal.semiconductor_source == "TIME_DOMAIN_WAVEFORM"
    assert window.system_analysis.nominal.total_loss_w > base.nominal.total_loss_w
    assert "TIME_DOMAIN_WAVEFORM" in window.loss_summary_view.text.toPlainText()
    assert window.model_comparison_view.loss_report_button.isEnabled()
    window.fields["pout_w"].setValue(base.spec.pout_w + 100)
    assert window.system_analysis is None  # plant edit invalidates old accepted evidence
    assert not window.model_comparison_view.loss_report_button.isEnabled()
    window._model_comparison_ready(result, base)
    assert window.system_analysis is None  # stale callback must not restore it
    window.close(); app.processEvents()


def test_real_switched_solver_result_reaches_system_loss(example):
    from llc_design.analysis.time_domain import TimeDomainConfig, solve_time_domain
    analyzer, base, _ = example
    td = solve_time_domain(
        LLCAnalysisRequest(base.spec, samples_per_cycle=512, waveform_cycles=2),
        TimeDomainConfig(samples_per_cycle=512, output_cycles=2),
    )
    assert td.convergence.converged
    updated = analyzer.with_time_domain_losses(base, [td])
    assert updated.nominal.semiconductor_source == "TIME_DOMAIN_WAVEFORM"
    assert updated.nominal.total_loss_w != pytest.approx(base.nominal.total_loss_w, rel=1e-4)
    assert sum(updated.nominal.breakdown().values()) == pytest.approx(updated.nominal.total_loss_w)


def test_residual_eoss_is_not_counted_twice(example):
    _, base, _ = example
    op = replace(base.nominal.operating_point, commutation_current_a=0.)
    result = bridge_loss_with_provenance(base.spec, base.tank, op,
        DeviceDatabase().get_primary(base.spec.primary_device))
    assert result.legacy.residual_coss_w > 0
    assert result.total_w == pytest.approx(result.legacy.total_w)
    assert next(t for t in result.terms if t.name == "coss_residual").value_w == 0.

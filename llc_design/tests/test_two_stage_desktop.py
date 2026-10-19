"""Production desktop/optimizer ownership tests; synthetic arrays test consumers,
not the accuracy of the physical solver. Real TD solver coverage is separate.
"""
from concurrent.futures import CancelledError
from copy import deepcopy
from dataclasses import asdict, replace
import json
import os
from types import SimpleNamespace
import zipfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import numpy as np
import pandas as pd
import pytest

from llc_design.analysis.fha import solve_fha
from llc_design.analysis.types import FidelityLevel, LLCAnalysisRequest
from llc_design.core.spec import LLCDesignSpec
from llc_design.models.system import LLCSystemAnalyzer
from llc_design.optimization.sweep import LLCOptimizer, OptimizationConfig, OptimizationResult
import llc_design.optimization.two_stage as model

_APP = None


def config(spec, ln_values=None):
    return OptimizationConfig(ln_values or (spec.ln_ratio,), (spec.q_full_load,),
        (spec.resonant_frequency_hz,), (spec.primary_turns,), (spec.secondary_turns,),
        (spec.primary_device,), (spec.sr_parallel_devices_per_position,))


def td_source(spec, current_scale):
    """Controlled independent source perturbation with consistent Faraday Im."""
    from llc_design.core.tank import design_tank
    fha = solve_fha(LLCAnalysisRequest(spec, samples_per_cycle=128, waveform_cycles=1))
    signals = dict(fha.waveform.signals)
    for key in ("i_resonant", "i_transformer_secondary"):
        signals[key] = replace(signals[key], values=signals[key].values * current_scale)
    phase = np.arange(len(fha.waveform.time_s)) / len(fha.waveform.time_s)
    peak = max(signals["v_transformer_primary"].values) / (
        4 * fha.waveform.switching_frequency_hz * design_tank(spec).lm_h)
    signals["i_magnetizing"] = replace(signals["i_magnetizing"], values=peak * (1 - 4 * np.abs(phase - 0.5)))
    return replace(fha, fidelity=FidelityLevel.SWITCHED_TIME_DOMAIN,
                   waveform=replace(fha.waveform, signals=signals))


@pytest.fixture
def candidates(monkeypatch):
    spec = LLCDesignSpec(q_full_load=0.3500123456789)
    analyzer = LLCSystemAnalyzer()
    analyze = analyzer.analyze
    specs = [spec.clone(ln_ratio=4.1), spec.clone(ln_ratio=5.9)]
    bases = {s.ln_ratio: analyze(s, work_points=[(s.vbus_nom_v, 1.)]) for s in specs}
    rows = [LLCOptimizer._row(bases[s.ln_ratio]) for s in specs]
    for row in rows:
        row["feasible"] = True
    stage_a = OptimizationResult(pd.DataFrame(rows), pd.DataFrame(rows), bases[4.1])
    optimizer = model.TwoStageLLCOptimizer(analyzer)
    monkeypatch.setattr(optimizer.fast, "run", lambda *a, **k: stage_a)
    monkeypatch.setattr(analyzer, "analyze", lambda s: bases[s.ln_ratio])
    monkeypatch.setattr(model, "_envelope_status", lambda *a: model.ConstraintStatus.PASS)
    scale = {4.1: 3., 5.9: 1.}
    def report(s, **kwargs):
        return SimpleNamespace(overall_validity=model.ModelValidity.PASS,
            points=[SimpleNamespace(td=td_source(s, scale[s.ln_ratio]))], warnings=())
    monkeypatch.setattr(model, "validate_fha_against_time_domain", report)
    result = optimizer.run(spec, config(spec, (4.1, 5.9)), maximum_candidates=2, top_n=2,
                           ranking_weights={"total_loss_w": -1.})
    assert len(result.verified) == 2 and all(not c.rejected for c in result.verified)
    return spec, optimizer, result, scale


def test_different_waveforms_change_real_candidate_losses_and_ranking(candidates):
    spec, optimizer, first, scales = candidates
    assert first.verified[0].spec.ln_ratio == 5.9
    assert first.verified[0].total_loss_w < first.verified[1].total_loss_w
    original = {c.spec.ln_ratio: c.analysis.nominal for c in first.verified}
    scales.update({4.1: 1., 5.9: 3.})
    second = optimizer.run(spec, config(spec, (4.1, 5.9)), maximum_candidates=2, top_n=2,
                           ranking_weights={"total_loss_w": -1.})
    assert second.verified[0].spec.ln_ratio == 4.1
    for c in second.verified:
        point = c.analysis.nominal
        assert point.loss_waveform_sha256 != original[c.spec.ln_ratio].loss_waveform_sha256
        assert point.primary.conduction_w != original[c.spec.ln_ratio].primary.conduction_w
        assert point.total_loss_w == c.rank_metrics["total_loss_w"]
        assert point.efficiency == c.rank_metrics["efficiency"]
        assert point.commutation_source == "FHA_OPERATING_POINT_APPROXIMATION"


@pytest.mark.parametrize("margin", [float("nan"), float("inf"), None])
def test_missing_zvs_is_rejected_not_zero(candidates, monkeypatch, margin):
    spec, optimizer, _, _ = candidates
    monkeypatch.setattr(model, "evaluate_zvs_margin", lambda *a, **k: SimpleNamespace(zvs_margin=margin, model_source="UNKNOWN"))
    result = optimizer.run(spec, config(spec, (4.1, 5.9)), top_n=2)
    assert all(c.rejected and "UNKNOWN" in c.reject_reason and not c.rank_metrics for c in result.verified)
    assert all(c.zvs_margin_worst is None for c in result.verified)


@pytest.mark.parametrize("kwargs", [{"top_n": 0}, {"top_n": True}, {"maximum_candidates": 0},
    {"maximum_candidates": 4097}, {"ranking_weights": {"unknown": 1}}, {"ranking_weights": {"efficiency": float("nan")}}])
def test_invalid_bounds_and_weights_fail_before_work(kwargs):
    with pytest.raises(ValueError):
        model.TwoStageLLCOptimizer().run(LLCDesignSpec(), **kwargs)


def test_empty_or_zero_axes_refused_and_cancel_short_circuits():
    spec = LLCDesignSpec()
    for cfg in (replace(config(spec), ln_values=()), replace(config(spec), secondary_turn_values=(0,))):
        with pytest.raises(ValueError):
            LLCOptimizer().run(spec, cfg)
    with pytest.raises(CancelledError):
        LLCOptimizer().run(spec, config(spec), cancelled=lambda: True)
    with pytest.raises(CancelledError):
        model.TwoStageLLCOptimizer().run(spec, cancelled=lambda: True)


@pytest.fixture
def window(candidates, monkeypatch):
    widgets = pytest.importorskip("PySide6.QtWidgets")
    import llc_design.gui.main_window as main
    import llc_design.gui.optimization_install as install
    global _APP
    app = _APP = widgets.QApplication.instance() or widgets.QApplication([])
    monkeypatch.setattr(main.LLCMainWindow, "_auto_check_update", lambda self: None)
    spec, _, result, _ = candidates
    host = main.LLCMainWindow(spec)
    view = host.two_stage_optimization_view
    view.config_text.setPlainText(json.dumps(asdict(config(spec, (4.1, 5.9)))))
    view.weights_text.setPlainText(json.dumps({"total_loss_w": -1.}))
    queue, calls = [], []
    host.thread_pool = SimpleNamespace(start=queue.append)
    def run(spec, cfg, **kwargs):
        calls.append((spec, cfg, kwargs))
        return result
    monkeypatch.setattr(install, "TwoStageLLCOptimizer", lambda: SimpleNamespace(run=run))
    yield host, view, queue, calls, result
    host.close()
    for worker in list(queue):
        if worker in host._active_workers:
            worker.run()
    host.deleteLater()
    from PySide6.QtCore import QCoreApplication, QEvent
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()


def finish_run(window):
    host, view, queue, calls, result = window
    host.run_two_stage_optimization()
    assert host._active_workers and view._busy
    queue[-1].run()
    assert not host._active_workers and view.result is result and not view._busy
    return host, view, queue, calls, result


def test_actual_selected_row_apply_preserves_exact_spec_and_td_analysis(window):
    host, view, _, calls, result = finish_run(window)
    original = host._spec_from_widgets()
    view.table.selectRow(1)
    candidate = result.verified[1]
    assert host._spec_from_widgets() == original
    host.apply_two_stage_candidate()
    assert host.spec == host._spec_from_widgets() == candidate.spec
    assert host.spec.q_full_load == 0.3500123456789
    assert host.system_analysis is candidate.analysis
    assert host.loss_summary_view._analysis is candidate.analysis
    assert view.apply_button.isEnabled()
    assert calls[0][0] == original
    assert calls[0][1].ln_values == (4.1, 5.9)
    assert calls[0][2]["ranking_weights"] == {"total_loss_w": -1.}
    assert "TIME_DOMAIN_WAVEFORM" in host.loss_summary_view.text.toPlainText()


@pytest.mark.parametrize("action", ["cancel", "edit", "options", "newer", "close"])
def test_stale_result_or_error_never_reappears_and_workers_clean_up(window, action, monkeypatch):
    host, view, queue, _, _ = window
    errors = []
    monkeypatch.setattr(host, "_worker_error", errors.append)
    host.run_two_stage_optimization(); old = queue[-1]
    if action == "cancel": host.cancel_two_stage_optimization()
    elif action == "edit": host.fields["pout_w"].setValue(host.fields["pout_w"].value() + 1)
    elif action == "options": view.top_n.setValue(2)
    elif action == "newer": host.run_two_stage_optimization()
    else: host.close()
    old.run()
    assert view.result is None and not view.apply_button.isEnabled()
    assert not errors
    if action == "newer":
        queue[-1].run()
        assert view.result is not None
    assert not host._active_workers
    # Repeat with an exception to exercise the host's error identity check.
    if action != "close":
        host.run_two_stage_optimization(); failed = queue[-1]
        host.cancel_two_stage_optimization()
        failed.function = lambda: (_ for _ in ()).throw(ValueError("stale solver error"))
        failed.run()
        assert not errors and not host._active_workers


def test_session_preserves_request_sources_but_restores_only_history(window, tmp_path):
    from llc_design.gui.optimization_install import capture_two_stage_session, restore_two_stage_session
    from llc_design.core.config import save_project, load_project
    host, view, _, _, result = finish_run(window)
    original_base = deepcopy(view.request["base_spec"])
    view.table.selectRow(1); host.apply_two_stage_candidate()
    session = capture_two_stage_session(host)
    assert session["selected_index"] == 1 and session["request"]["base_spec"] == original_base
    assert session["candidates"][1]["sources"][0]["waveform_sha256"] == result.verified[1].analysis.nominal.loss_waveform_sha256
    path = tmp_path / "project.json"
    save_project(host.spec, path, host.system_analysis, optimization_session=session)
    spec, control, restored = load_project(path, include_optimization=True)
    assert spec == host.spec and restored == session
    restore_two_stage_session(host, restored)
    assert view.result is None and not view.apply_button.isEnabled() and not view.report_button.isEnabled()
    assert capture_two_stage_session(host) == session
    assert "历史" in view.status.text()
    bad = deepcopy(session); bad["candidates"][1]["candidate_id"] = "0" * 64
    with pytest.raises(ValueError, match="identity"):
        model.validate_two_stage_session(bad)


def test_report_uses_selected_candidate_and_rejects_selection_change_in_dialog(window, tmp_path, monkeypatch):
    from llc_design.gui.optimization_install import QFileDialog
    host, view, queue, _, result = finish_run(window)
    view.table.selectRow(1)
    output = tmp_path / "selected.zip"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a: (str(output), "ZIP"))
    host.export_two_stage_candidate_report(); queue[-1].run()
    assert output.exists()
    with zipfile.ZipFile(output) as archive:
        provenance = json.loads(archive.read("optimization_provenance.json"))
        assert provenance["selected_candidate"]["spec"]["fields"]["ln_ratio"] == result.verified[1].spec.ln_ratio
        assert result.verified[1].analysis.nominal.loss_waveform_sha256.encode() in archive.read("LLC_calculation_book.md")
        csv = pd.read_csv(archive.open("operating_points.csv"))
        assert csv.iloc[0]["total_loss_w"] == pytest.approx(result.verified[1].total_loss_w)
    missing = tmp_path / "stale.zip"
    def dialog(*args):
        view.table.selectRow(0)
        return str(missing), "ZIP"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", dialog)
    before = len(queue)
    host.export_two_stage_candidate_report()
    assert len(queue) == before and not missing.exists()


def test_report_discards_late_output_on_input_edit(window, tmp_path, monkeypatch):
    from llc_design.gui.optimization_install import QFileDialog
    host, view, queue, _, _ = finish_run(window)
    output = tmp_path / "stale.zip"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a: (str(output), "ZIP"))
    host.export_two_stage_candidate_report(); worker = queue[-1]
    view.top_n.setValue(1)
    worker.run()
    assert not output.exists() and not host._active_workers


def test_real_nominal_solver_button_apply_save_report_and_restore(tmp_path, monkeypatch):
    """Unsubstituted physical Stage A/B, actual worker signals and GUI actions.

    Ln=5.5 is WARN at nominal under the standard FHA/TD thresholds; the critical
    full-load/light-load scope is not inferred from this one-point evidence.
    """
    import time
    widgets = pytest.importorskip("PySide6.QtWidgets")
    from PySide6.QtCore import QCoreApplication, QEvent
    import llc_design.gui.main_window as main
    from llc_design.core.config import load_project
    global _APP
    app = _APP = widgets.QApplication.instance() or widgets.QApplication([])
    monkeypatch.setattr(main.LLCMainWindow, "_auto_check_update", lambda self: None)
    # Explicit 320 V hold-up endpoint and 3.3 mF bus capacitor support
    # Ln=5.5's attainable gain; no failed constraint is waived.
    host = main.LLCMainWindow(LLCDesignSpec(vbus_hold_end_v=320., bus_capacitance_f=0.0033))
    view = host.two_stage_optimization_view
    view.config_text.setPlainText(json.dumps(asdict(config(host.spec, (5.5,)))))
    view.maximum_candidates.setValue(1); view.top_n.setValue(1)
    errors = []
    monkeypatch.setattr(host, "_worker_error", errors.append)
    project, report = tmp_path / "real.json", tmp_path / "real.zip"
    monkeypatch.setattr(widgets.QFileDialog, "getSaveFileName",
        lambda *args: (str(report if "ZIP" in args[-1] else project), ""))
    monkeypatch.setattr(widgets.QFileDialog, "getOpenFileName", lambda *args: (str(project), ""))
    def wait():
        deadline = time.monotonic() + 180
        while host._active_workers and time.monotonic() < deadline:
            app.processEvents(); time.sleep(0.01)
        assert not host._active_workers, "Real solver exceeded bounded test timeout"
        assert not errors
    try:
        view.run_button.click(); wait()
        assert view.result is not None
        candidate = view.selected_candidate()
        assert candidate is not None, [(c.reject_reason, c.diagnostics) for c in view.result.verified]
        assert candidate.fha_td_validity == model.ModelValidity.WARN
        assert candidate.analysis.nominal.loss_waveform_sha256
        assert candidate.analysis.nominal.semiconductor_source == "TIME_DOMAIN_WAVEFORM"
        assert sum(p.semiconductor_source == "TIME_DOMAIN_WAVEFORM" for p in candidate.analysis.operating_points) == 1
        assert "仅名义" in view.table.item(0, 0).text()
        view.apply_button.click()
        assert host.spec.ln_ratio == 5.5 and host.system_analysis is candidate.analysis
        host.save_json()
        saved = load_project(project, include_optimization=True)[-1]
        assert saved["request"]["verification_scope"] == "nominal"
        assert saved["candidates"][0]["sources"][1]["waveform_sha256"] == candidate.analysis.nominal.loss_waveform_sha256
        view.report_button.click(); wait()
        assert report.exists()
        with zipfile.ZipFile(report) as archive:
            assert candidate.analysis.nominal.loss_waveform_sha256.encode() in archive.read("LLC_calculation_book.md")
            provenance = json.loads(archive.read("optimization_provenance.json"))
            assert provenance["request"]["verification_scope"] == "nominal"
        host.load_json()
        assert host.spec.ln_ratio == 5.5 and view.result is None
        assert not view.apply_button.isEnabled() and not view.report_button.isEnabled()
    finally:
        host.close()
        host.thread_pool.waitForDone(180000)
        app.processEvents()
        host.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        app.processEvents()


def test_historical_replay_retains_exact_base_and_nondefault_solver_settings(window):
    from llc_design.gui.optimization_install import capture_two_stage_session, restore_two_stage_session
    host, view, queue, calls, _ = finish_run(window)
    original = deepcopy(view.request["base_spec"])
    view.table.selectRow(1); host.apply_two_stage_candidate()
    session = capture_two_stage_session(host)
    session["request"]["time_domain"]["samples_per_cycle"] = 512
    session["model_revision"] = "older-model-revision"
    restore_two_stage_session(host, session)
    assert "模型版本已改变" in view.status.text()
    assert not view.apply_button.isEnabled()
    view.run_button.click(); queue[-1].run()
    from llc_design.control.design_session import encode_model
    assert encode_model(calls[-1][0]) == original
    assert calls[-1][2]["time_domain"].samples_per_cycle == 512
    assert view.result is not None  # ownership anchors displayed project, not historical base
    assert view.request["time_domain"]["samples_per_cycle"] == 512


@pytest.mark.parametrize("change", ["options", "selection", "cancel", "new_request"])
def test_save_dialog_reentry_cannot_save_another_optimization(window, tmp_path, monkeypatch, change):
    from PySide6.QtWidgets import QFileDialog
    host, view, queue, _, _ = finish_run(window)
    output = tmp_path / "stale-save.json"
    def dialog(*args):
        if change == "options": view.top_n.setValue(1)
        elif change == "selection": view.table.selectRow(1)
        elif change == "cancel": host.cancel_two_stage_optimization()
        else: host.run_two_stage_optimization()
        return str(output), "JSON"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", dialog)
    host.save_json()
    assert not output.exists()


def test_real_thread_pool_close_discards_worker_and_cleans_owner(monkeypatch):
    import time
    widgets = pytest.importorskip("PySide6.QtWidgets")
    from PySide6.QtCore import QCoreApplication, QEvent
    from llc_design.gui.main_window import LLCMainWindow
    global _APP
    app = _APP = widgets.QApplication.instance() or widgets.QApplication([])
    monkeypatch.setattr(LLCMainWindow, "_auto_check_update", lambda self: None)
    host = LLCMainWindow(LLCDesignSpec())
    errors = []
    monkeypatch.setattr(host, "_worker_error", errors.append)
    host.two_stage_optimization_view.run_button.click()
    assert host._active_workers
    host.close()
    assert host._two_stage_optimization_state["cancel"].is_set()
    assert host.thread_pool.waitForDone(30000)
    deadline = time.monotonic() + 5
    while host._active_workers and time.monotonic() < deadline:
        app.processEvents(); time.sleep(.01)
    assert not host._active_workers and not errors
    assert host.two_stage_optimization_view.result is None
    host.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()


def prepare_legacy_td_report(window):
    from llc_design.analysis.types import MultiFidelityAnalysis
    host, view, queue, _, result = finish_run(window)
    view.table.selectRow(0); host.apply_two_stage_candidate()
    candidate = result.verified[0]
    td = td_source(candidate.spec, 1.)
    host.multi_fidelity_analysis = MultiFidelityAnalysis(td.request, {td.fidelity: td}, td.fidelity, ())
    host._accepted_model_options = host.model_comparison_view.options()
    return host, queue, candidate


@pytest.mark.parametrize("kind", ["td", "pdf"])
@pytest.mark.parametrize("fault", ["modal_edit", "late_edit", "modal_close"])
def test_legacy_report_stale_dialog_or_worker_cannot_publish(window, tmp_path, monkeypatch, kind, fault):
    from pathlib import Path
    import llc_design.gui.main_window as main
    import llc_design.report.export as report
    import llc_design.analysis as analyses
    host, queue, _ = prepare_legacy_td_report(window)
    output = tmp_path / ("report-dir" if kind == "td" else "report.pdf")
    def edit(): host.fields["ln_ratio"].setValue(host.fields["ln_ratio"].value() + .1)
    def dialog(*args):
        if fault == "modal_edit": edit()
        elif fault == "modal_close": host.close()
        return str(output) if kind == "td" else (str(output), "PDF")
    monkeypatch.setattr(main.QFileDialog, "getExistingDirectory", dialog)
    monkeypatch.setattr(main.QFileDialog, "getSaveFileName", dialog)
    generated = []
    def export(analysis, directory):
        path = Path(directory) / "LLC_calculation_book.md"
        path.parent.mkdir(parents=True, exist_ok=True); path.write_text(str(analysis.spec.ln_ratio))
        generated.append(path)
        return {"report": path}
    def pdf(analysis, path):
        path = Path(path); path.write_bytes(b"%PDF-test-staging")
        generated.append(path)
        return path
    monkeypatch.setattr(report, "export_calculation_book", export)
    monkeypatch.setattr(analyses, "export_multifidelity_analysis", lambda *a: None)
    monkeypatch.setattr(main, "build_formula_pdf", pdf)
    before = len(queue)
    (host._export_td_loss_report if kind == "td" else host.export_formula_pdf)()
    if fault == "late_edit":
        assert len(queue) == before + 1
        edit(); queue[-1].run()
        assert generated and all(not path.exists() for path in generated)
    else:
        assert len(queue) == before
    assert not output.exists()


def test_actual_legacy_td_report_and_formula_pdf_publish_matching_sources(window, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QFileDialog
    host, queue, candidate = prepare_legacy_td_report(window)
    output = tmp_path / "td-report"
    pdf = tmp_path / "formula.pdf"
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *a: str(output))
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a: (str(pdf), "PDF"))
    errors = []
    monkeypatch.setattr(host, "_worker_error", errors.append)
    host._export_td_loss_report(); queue[-1].run()
    assert not errors
    assert candidate.analysis.nominal.loss_waveform_sha256 in (output / "LLC_calculation_book.md").read_text()
    assert (output / "model_comparison").is_dir()
    host.export_formula_pdf(); queue[-1].run()
    assert not errors and pdf.read_bytes().startswith(b"%PDF") and pdf.stat().st_size > 1000
    assert not list(output.glob(".llc-export-*"))


def test_unrun_edited_request_is_saved_without_inventing_candidates(window):
    from llc_design.gui.optimization_install import capture_two_stage_session, restore_two_stage_session
    host, view, _, _, _ = window
    view.top_n.setValue(2)
    view.verification_scope.setCurrentIndex(view.verification_scope.findData("critical"))
    saved = capture_two_stage_session(host)
    assert saved["request"]["top_n"] == 2
    assert saved["request"]["verification_scope"] == "critical"
    assert saved["candidates"] == [] and saved["stage_a"] == [] and saved["selected_index"] == -1
    restore_two_stage_session(host, saved)
    assert view.top_n.value() == 2 and view.verification_scope.currentData() == "critical"
    assert not view.apply_button.isEnabled() and view.result is None


def test_real_cli_two_stage_nominal_report_uses_actual_solver(tmp_path):
    from click.testing import CliRunner
    from llc_design.__main__ import cli
    from llc_design.core.config import save_spec
    source = tmp_path / "nominal-source.json"
    save_spec(LLCDesignSpec(ln_ratio=5.5, vbus_hold_end_v=320., bus_capacitance_f=.0033), source)
    output = tmp_path / "cli-result"
    result = CliRunner().invoke(cli, ["optimize", "--config", str(source), "--profile", "current",
        "--two-stage", "--verification-scope", "nominal", "--max-candidates", "1", "--top-n", "1",
        "--candidate-rank", "1", "--output", str(output)])
    assert result.exit_code == 0, result.output + repr(result.exception)
    assert "Only nominal-point TD losses" in result.output
    evidence = json.loads((output / "two_stage_verification.json").read_text())
    assert evidence["request"]["verification_scope"] == "nominal"
    accepted = [row for row in evidence["candidates"] if not row["rejected"]]
    assert len(accepted) == 1 and accepted[0]["candidate_id"] == evidence["selected_candidate_id"]
    sources = accepted[0]["sources"]
    td = [point for point in sources if point["waveform_sha256"]]
    assert len(td) == 1
    assert td[0]["waveform_sha256"] in (output / "selected_candidate" / "LLC_calculation_book.md").read_text()
    assert (output / "selected_candidate" / "optimization_provenance.json").exists()
    table = pd.read_csv(output / "two_stage_candidates.csv")
    assert table.iloc[0]["td_scope_worst_loss_w"] == pytest.approx(td[0]["total_loss_w"])
    assert not (output / "best_design").exists()


def test_cli_unknown_candidate_produces_evidence_not_fha_fallback(tmp_path, monkeypatch):
    from click.testing import CliRunner
    from llc_design.__main__ import cli
    spec = LLCDesignSpec()
    candidate = model.VerifiedCandidate(spec, None, model.ConstraintStatus.UNKNOWN,
        model.ModelValidity.UNKNOWN, None, None, None, {}, True, "ZVS Level-2 UNKNOWN; no device evidence")
    result = model.TwoStageOptimizationResult(OptimizationResult(pd.DataFrame(), pd.DataFrame(), None),
        (candidate,), dict(model.DEFAULT_WEIGHTS), ("UNKNOWN evidence excluded",))
    monkeypatch.setattr(model, "TwoStageLLCOptimizer", lambda: SimpleNamespace(run=lambda *a, **k: result))
    output = tmp_path / "unknown"
    response = CliRunner().invoke(cli, ["optimize", "--profile", "current", "--two-stage", "--top-n", "1", "--output", str(output)])
    assert response.exit_code != 0 and "No verified candidate" in response.output
    evidence = json.loads((output / "two_stage_verification.json").read_text())
    assert evidence["selected_candidate_id"] is None
    assert evidence["candidates"][0]["zvs_margin_worst"] is None
    assert not (output / "selected_candidate").exists() and not (output / "best_design").exists()


@pytest.mark.parametrize("options", [["--top-n", "1"], ["--two-stage", "--top-n", "0"],
    ["--two-stage", "--max-candidates", "129"], ["--two-stage", "--top-n", "1", "--candidate-rank", "2"]])
def test_cli_rejects_implicit_scope_or_invalid_bounds(tmp_path, options):
    from click.testing import CliRunner
    from llc_design.__main__ import cli
    output = tmp_path / "invalid"
    response = CliRunner().invoke(cli, ["optimize", "--profile", "current", "--output", str(output), *options])
    assert response.exit_code != 0 and not output.exists()


def test_cli_without_opt_in_still_uses_original_stage_a(tmp_path, monkeypatch):
    from click.testing import CliRunner
    import llc_design.__main__ as entry
    called = []
    def stage_a(*args):
        called.append(args)
        return OptimizationResult(pd.DataFrame(), pd.DataFrame(), None)
    monkeypatch.setattr(entry, "LLCOptimizer", lambda: SimpleNamespace(run=stage_a))
    response = CliRunner().invoke(entry.cli, ["optimize", "--profile", "current", "--output", str(tmp_path / "stage-a")])
    assert response.exit_code == 0, response.output
    assert len(called) == 1 and "Stage A / FHA" in response.output
    assert not (tmp_path / "stage-a" / "two_stage_verification.json").exists()

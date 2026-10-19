"""Desktop adapter for the real two-stage LLC optimizer and exact session replay."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
import io
import json
from pathlib import Path
import tempfile
from threading import Event
from types import MethodType
import zipfile

from PySide6.QtWidgets import QFileDialog
from shiboken6 import isValid

from ..analysis.time_domain import TimeDomainConfig
from ..control.design_session import decode_model, encode_model
from ..optimization.sweep import OptimizationConfig
from ..optimization.two_stage import (
    SESSION_MODEL_REVISION, SESSION_SCHEMA, TwoStageLLCOptimizer, candidate_provenance,
    validate_two_stage_request, validate_two_stage_session,
)
from .widgets.two_stage_optimization_view import TwoStageOptimizationView


def _desktop_time_domain_settings():
    return asdict(TimeDomainConfig(samples_per_cycle=256, output_cycles=1,
        maximum_settling_cycles=120, shooting_max_evaluations=60, frequency_scan_points=5))


candidate_history = candidate_provenance


def capture_two_stage_session(host):
    view = getattr(host, "two_stage_optimization_view", None)
    if view is None:
        return None
    if view.result is None or not view._valid:
        if view.historical is not None:
            return deepcopy(view.historical)
        request = deepcopy(view.request)
        if request is None:
            request = validate_two_stage_request({"base_spec": encode_model(host._spec_from_widgets()),
                **view.options(), "time_domain": deepcopy(view._time_domain_settings)})
        payload = {"schema": SESSION_SCHEMA, "schema_version": 1,
            "model_revision": SESSION_MODEL_REVISION, "request": request,
            "candidates": [], "stage_a": [], "selected_index": -1}
        return validate_two_stage_session(payload)
    selected = view.table.currentRow()
    payload = {"schema": SESSION_SCHEMA, "schema_version": 1,
        "model_revision": SESSION_MODEL_REVISION, "request": deepcopy(view.request),
        "candidates": [candidate_history(candidate) for candidate in view.result.verified],
        "stage_a": json.loads(view.result.stage_a.table.to_json(orient="records")),
        "selected_index": selected if 0 <= selected < len(view.result.verified) else -1}
    return validate_two_stage_session(payload)


def restore_two_stage_session(host, prepared):
    payload = None if prepared is None else validate_two_stage_session(prepared)
    view = install_two_stage_optimization(host)
    host.cancel_two_stage_optimization()
    if prepared is None:
        view.request = view.historical = None
        view._time_domain_settings = _desktop_time_domain_settings()
        view.set_initial_spec(host._spec_from_widgets())
        return
    view.request = deepcopy(payload["request"])
    view.historical = payload
    view.restore_options(payload["request"])
    view._time_domain_settings = deepcopy(payload["request"]["time_domain"])
    host._two_stage_optimization_state["replay_base"] = deepcopy(payload["request"]["base_spec"])
    host._two_stage_optimization_state["replay_anchor"] = encode_model(host._spec_from_widgets())
    compatibility = (" 模型版本已改变：" + payload["model_revision"] + " → " + SESSION_MODEL_REVISION + "。"
                     if payload["model_revision"] != SESSION_MODEL_REVISION else "")
    view.status.setText("历史优化记录，未经本次运行验证；请重新运行。Apply / 报告已禁用。" + compatibility)
    view.details.setPlainText(json.dumps(payload, ensure_ascii=False, indent=2))


def install_two_stage_optimization(host):
    if hasattr(host, "two_stage_optimization_view"):
        return host.two_stage_optimization_view
    view = TwoStageOptimizationView()
    host.two_stage_optimization_view = view
    host.tabs.addTab(view, "LLC Two-Stage 优化")
    view.set_initial_spec(host._spec_from_widgets())
    view._time_domain_settings = _desktop_time_domain_settings()
    state = {"generation": 0, "cancel": Event(), "anchor": None, "applying": False}
    host._two_stage_optimization_state = state

    def invalidate(self, message="输入已改变；优化结果已失效。请重新运行。"):
        if state["applying"]:
            return
        state["cancel"].set(); state["generation"] += 1
        state["anchor"] = None
        state.pop("replay_base", None); state.pop("replay_anchor", None)
        view.historical = None
        view.request = None
        view.invalidate(message)

    def cancel(self):
        invalidate(self, "已取消/丢弃优化。当前候选结束后释放线程；迟到结果不会应用。")

    def current(generation, anchor):
        if not isValid(host) or host._closing or generation != state["generation"] or state["cancel"].is_set():
            return False
        try:
            return encode_model(host._spec_from_widgets()) == anchor
        except (ValueError, TypeError):
            return False

    def run(self):
        if host._closing:
            return
        replay_base = deepcopy(state.get("replay_base"))
        replay_anchor = deepcopy(state.get("replay_anchor"))
        invalidate(self)
        try:
            # Widget access happens only here on the GUI thread. Each worker owns
            # immutable input copies, never host.spec or live Qt widgets.
            displayed_spec = deepcopy(host._spec_from_widgets())
            display_anchor = encode_model(displayed_spec)
            spec = decode_model(replay_base) if replay_base is not None and replay_anchor == display_anchor else displayed_spec
            options = view.options()
            request = validate_two_stage_request({"base_spec": encode_model(spec), **options,
                "time_domain": deepcopy(view._time_domain_settings)})
            cfg = OptimizationConfig(**{key: tuple(value) for key, value in request["config"].items()})
        except (ValueError, TypeError) as exc:
            view.status.setText(f"请求无效：{exc}")
            return
        state["cancel"] = Event()
        cancel_event = state["cancel"]
        generation = state["generation"]
        anchor = deepcopy(display_anchor)
        state["anchor"] = anchor
        view.request = deepcopy(request)
        view.status.setText("运行 Stage A → B；取消/编辑输入会丢弃本次结果。")

        def calculate():
            return TwoStageLLCOptimizer().run(spec, cfg,
                maximum_candidates=request["maximum_candidates"], top_n=request["top_n"],
                ranking_weights=deepcopy(request["ranking_weights"]), cancelled=cancel_event.is_set,
                time_domain=TimeDomainConfig(**request["time_domain"]), verification_scope=request["verification_scope"])

        calculate._llc_waveform_current = lambda: current(generation, anchor)

        def ready(result):
            if not current(generation, anchor):
                return
            view.set_result(deepcopy(request), result)
            host.tabs.setCurrentWidget(view)
            host._append_log(f"Two-stage LLC optimization: {sum(not x.rejected for x in result.verified)} ranked, "
                             f"{sum(x.rejected for x in result.verified)} rejected; current project unchanged")

        host._run_worker("正在运行 LLC Stage A → TD Stage B…", calculate, ready)

    def selected():
        candidate = view.selected_candidate()
        if candidate is None or state["anchor"] is None or not current(state["generation"], state["anchor"]):
            return None
        if view.options() != {key: view.request[key] for key in ("config", "ranking_weights", "maximum_candidates", "top_n", "verification_scope")}:
            invalidate(host)
            return None
        # A verified record must carry actual accepted TD source fingerprints.
        if candidate.analysis.spec != candidate.spec or not any(
                point.loss_waveform_sha256 and point.semiconductor_source == "TIME_DOMAIN_WAVEFORM"
                for point in candidate.analysis.operating_points):
            view.status.setText("候选缺少匹配的 TD 损耗来源；必须重新运行。")
            return None
        return candidate

    def apply(self):
        candidate = selected()
        if candidate is None or view._busy:
            return
        state["applying"] = True
        try:
            host.spec = deepcopy(candidate.spec)
            host._load_spec_to_widgets(host.spec)
            if encode_model(host._spec_from_widgets()) != encode_model(candidate.spec):
                raise ValueError("Applied widgets do not preserve exact candidate specification")
            state["anchor"] = encode_model(candidate.spec)
            # Reuse the already-verified analysis object. A fresh FHA-only run
            # here would silently replace the selected waveform losses.
            host._design_ready(candidate.analysis)
            host.tabs.setCurrentWidget(view)
            view.status.setText("已将所选候选应用到实际工程；设计总览/损耗汇总使用同一 TD 分析。")
        finally:
            state["applying"] = False

    def export(self):
        candidate = selected()
        if candidate is None or view._busy:
            return
        generation, anchor = state["generation"], deepcopy(state["anchor"])
        selected_row = view.table.currentRow()
        def report_current():
            return current(generation, anchor) and view.selected_candidate() is candidate and view.table.currentRow() == selected_row
        path, _ = QFileDialog.getSaveFileName(host, "导出所选 TD 候选报告", str(host.output_directory / "llc_candidate.zip"), "ZIP (*.zip)")
        if not path or not report_current():
            return
        request = deepcopy(view.request)
        provenance = candidate_history(candidate)

        def calculate():
            from ..report.export import export_calculation_book
            # No external output until the GUI accepts the same selection again.
            # Temporary solver/export artifacts are removed even on cancellation.
            with tempfile.TemporaryDirectory(prefix="llc-candidate-") as directory:
                root = Path(directory)
                export_calculation_book(candidate.analysis, root)
                (root / "optimization_provenance.json").write_text(json.dumps({
                    "model_revision": SESSION_MODEL_REVISION, "request": request,
                    "selected_candidate": provenance}, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
                stream = io.BytesIO()
                with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
                    for item in sorted(root.rglob("*")):
                        if item.is_file():
                            archive.write(item, item.relative_to(root))
                return stream.getvalue()

        calculate._llc_waveform_current = report_current
        def ready(content):
            if report_current():
                output = Path(path)
                temporary = None
                try:
                    output.parent.mkdir(parents=True, exist_ok=True)
                    with tempfile.NamedTemporaryFile(dir=output.parent, delete=False, suffix=".tmp") as stream:
                        temporary = Path(stream.name)
                        stream.write(content)
                    temporary.replace(output)
                except OSError as exc:
                    view.status.setText(f"报告写入失败：{exc}")
                    host._append_log(f"Candidate report publication failed: {exc}")
                    return
                finally:
                    if temporary is not None:
                        temporary.unlink(missing_ok=True)
                host._append_log(f"Selected verified LLC candidate report exported: {output}")
        host._run_worker("正在导出所选候选的 TD 损耗与来源…", calculate, ready)

    for name, function in (("invalidate_two_stage_optimization", invalidate), ("cancel_two_stage_optimization", cancel),
                           ("run_two_stage_optimization", run), ("apply_two_stage_candidate", apply),
                           ("export_two_stage_candidate_report", export)):
        setattr(host, name, MethodType(function, host))
    original_busy = host._set_busy
    def set_busy(self, busy, message=""):
        original_busy(busy, message)
        view.set_busy(busy)
    host._set_busy = MethodType(set_busy, host)
    view.inputs_changed.connect(host.invalidate_two_stage_optimization)
    view.run_requested.connect(host.run_two_stage_optimization)
    view.cancel_requested.connect(host.cancel_two_stage_optimization)
    view.apply_requested.connect(host.apply_two_stage_candidate)
    view.report_requested.connect(host.export_two_stage_candidate_report)
    return view


def publish_export_files(directory, files):
    """Publish accepted export bytes, staging completely before atomic file moves.

    Call only on the GUI thread after the request/selection identity check.
    No Qt events are processed here, so input edits cannot interleave publication.
    Existing unrelated files in the selected destination remain untouched.
    """
    root = Path(directory)
    for relative in files:
        path = Path(relative)
        if path.is_absolute() or ".." in path.parts or str(path) in ("", "."):
            raise ValueError("Invalid export relative path")
    root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".llc-export-", dir=root) as directory:
        staging = Path(directory)
        for relative, content in files.items():
            path = staging / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        for relative in files:
            destination = root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            (staging / relative).replace(destination)
    return {relative: root / relative for relative in files}

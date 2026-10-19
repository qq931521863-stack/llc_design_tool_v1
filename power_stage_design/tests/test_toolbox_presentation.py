"""Real Qt presentation, exact-result exports and versioned-input restore gates."""
from __future__ import annotations

import copy
import json
import os
import time
from pathlib import Path

import pytest

from llc_design import __version__
from power_stage_design.toolbox import MODEL_VERSION
from power_stage_design.toolbox_presentation import (
    LABELS,
    NOTICES,
    details_text,
    format_value,
    result_text,
)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
TOOL_IDS = ["X01", "X02", "X03", "X04", "X05", "X06", "X07", "X08", "X09", "X10", "X12", "G04"]


@pytest.fixture(scope="module")
def app():
    qt = pytest.importorskip("PySide6.QtWidgets")
    application = qt.QApplication.instance() or qt.QApplication([])
    yield application
    application.processEvents()


@pytest.fixture
def widget(app):
    from PySide6.QtCore import QCoreApplication, QEvent

    from power_stage_design.toolbox_gui import ToolBoxWidget

    view = ToolBoxWidget()
    view.resize(1400, 900)
    view.show()
    app.processEvents()
    yield view
    assert view.close()
    view.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()


def _settle(app, widget):
    deadline = time.monotonic() + 10
    while widget._workers and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.002)
    app.processEvents()
    assert not widget._workers


def _assert_labels(value):
    if isinstance(value, dict):
        assert set(value) <= set(LABELS), set(value) - set(LABELS)
        for child in value.values():
            _assert_labels(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            _assert_labels(child)


def test_filter_current_is_labeled_and_persisted_as_stage_average(widget, app, tmp_path):
    from power_stage_design.models import DesignInputs
    from power_stage_design.topologies import calculate_stage

    stage=calculate_stage(DesignInputs("T15",48,5,2,100e3,470e-6,
        capacitance_f=1e-3,turns_ratio_np_ns=2,diode_drop_v=.7,
        transfer_capacitance_f=1e-3,magnetizing_inductance_h=1e-3))
    widget.set_stage_result(stage);widget.select_tool("X12");widget.import_stage()
    page=widget.pages["X12"]
    assert page._form.labelForField(page.widgets["input_current_a"]).text()=="输入平均电流 / A"
    result=widget.calculate()
    assert result is not None,widget.status.text()
    # Independent DC operating point, not the T15 source pulse or RMS current.
    expected=(5+.7)*2/48
    assert result.inputs["input_current_a"]==pytest.approx(expected,abs=1e-14)
    state=json.loads(json.dumps(widget.state()))
    widget.restore_state(state)
    assert widget.latest_result.inputs["input_current_a"]==pytest.approx(expected,abs=1e-14)
    path=tmp_path/"filter-average.json";widget.export_to(path)
    assert json.loads(path.read_text(encoding="utf-8"))["inputs"]["input_current_a"]==pytest.approx(expected,abs=1e-14)


@pytest.mark.parametrize("tool_id", TOOL_IDS)
def test_all_real_tools_show_chinese_results_exact_exports_and_invalidation(widget, app, tmp_path, tool_id):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    widget.select_tool(tool_id)
    QTest.mouseClick(widget.calculate_button, Qt.MouseButton.LeftButton)
    result = widget.latest_result
    assert result is not None, widget.status.text()
    original = result.to_json()
    assert widget.output.toPlainText() == result_text(result)
    assert "计算结果" in widget.output.toPlainText()
    assert "适用条件" in widget.output.toPlainText()
    assert "未翻译" not in widget.output.toPlainText()
    assert "补充结果" not in widget.output.toPlainText()
    assert result.result_id not in widget.output.toPlainText()
    assert result.model_version not in widget.output.toPlainText()
    assert result.result_id not in widget.status.text()
    assert result.status not in widget.status.text()
    _assert_labels(result.outputs)
    assert all(text in NOTICES for text in result.assumptions)
    for key, value in result.outputs.items():
        if isinstance(value, (int, float)) and tool_id != "X10":
            assert LABELS[key] + "：" + format_value(value, result.units.get(key, "")) in widget.output.toPlainText()
    widget.details_toggle.setChecked(True)
    details = json.loads(widget.output.toPlainText())
    for key in ("result_id", "model_version", "source", "inputs", "status"):
        assert details[key] == json.loads(original)[key]
    for suffix, expected in ((".json", result.to_json()), (".txt", result.to_text()), (".csv", result.to_csv())):
        path = tmp_path / (tool_id + suffix)
        widget.export_to(path)
        assert path.read_bytes() == expected.encode("utf-8")
    widget.details_toggle.setChecked(False)
    assert widget.latest_result is result
    assert result.to_json() == original
    field = next(field for field in widget.pages[tool_id].fields if field.kind == "number" and field.default is not None)
    widget.input_widgets[field.key].setText(str(float(widget.input_widgets[field.key].text()) + .1))
    assert widget.latest_result is None
    assert widget.output.toPlainText() == ""
    assert not widget.export_button.isEnabled() and not widget.save_inputs_button.isEnabled()
    with pytest.raises(ValueError):
        widget.export_to(tmp_path / "stale.json")
    assert not (tmp_path / "stale.json").exists()


@pytest.mark.parametrize("tool_id,changes,expected", [
    ("X01", {"a_rds_on_ohm": None}, "缺失分项：导通损耗"),
    ("X01", {"role": "sr"}, "同步整流近似忽略开关交叠"),
    ("X02", {"phase_margin_deg": 45}, "相位裕量低于资料建议"),
    ("X04", {}, "不应把本估算当作已验证的热设计依据"),
    ("X05", {"ripple_fraction": .2}, "钳位纹波较大"),
    ("X06", {"switching_frequency_hz": None, "voltage_swing_v": None}, "附加充放电功耗估算：未知 / 未提供"),
    ("X12", {"input_current_a": 100}, "近似阻尼系数不大于零"),
])
def test_model_warnings_and_missing_terms_are_visible_without_details(widget, tool_id, changes, expected):
    widget.select_tool(tool_id)
    widget.set_values(changes)
    result = widget.calculate()
    assert result is not None
    text = widget.output.toPlainText()
    assert expected in text
    assert "未翻译" not in text
    for warning in result.warnings:
        assert warning.startswith("Missing baseline terms: ") or warning in NOTICES


def test_fet_comparison_retains_each_device_unknown_and_known_values(widget):
    widget.set_values({"compare": True, "a_rds_on_ohm": None, "b_name": "实测器件 B"})
    result = widget.calculate()
    text = widget.output.toPlainText()
    assert "实测器件 B" in text and "部分数据缺失" in text
    assert "已建模分项总损耗：未知 / 未提供" in text
    assert result.outputs["devices"][0]["baseline_total_w"] is None
    assert result.outputs["devices"][1]["baseline_total_w"] > 0
    assert "conduction_w" not in text


@pytest.mark.parametrize("tool_id,field,expected", [
    ("X01", "role", ["主开关", "同步整流管"]),
    ("X02", "control", ["电流模式（CMC）", "电压模式（VMC）"]),
    ("X07", "operation", ["根据目标反算电阻", "按实际电阻计算输出"]),
    ("X07", "known_arm", ["下臂电阻", "上臂电阻"]),
    ("X12", "configuration", ["π 型网络", "LC 网络"]),
])
def test_choice_labels_translate_without_changing_enum_data(widget, tool_id, field, expected):
    from power_stage_design.toolbox_gui import TOOLS

    widget.select_tool(tool_id)
    combo = widget.input_widgets[field]
    assert [combo.itemText(i) for i in range(combo.count())] == expected
    definition = next(item for item in TOOLS[tool_id][2] if item.key == field)
    assert [combo.itemData(i) for i in range(combo.count())] == list(definition.options)


@pytest.mark.parametrize("category,from_unit,to_unit,value,expected", [
    ("temperature", "C", "F", 0, "转换结果：32 °F"),
    ("temperature", "F", "C", 32, "转换结果：0 °C"),
    ("power_gain", "factor", "dB", 10, "转换结果：10 dB"),
    ("voltage_gain", "factor", "dB", 10, "转换结果：20 dB"),
    ("power", "W", "PS (metric hp)", 735.49875, "转换结果：1 PS（公制马力）"),
])
def test_unit_conversion_caption_is_dimension_specific(widget, category, from_unit, to_unit, value, expected):
    widget.select_tool("X10")
    widget.set_values({"category": category, "from_unit": from_unit, "to_unit": to_unit, "value": value})
    assert widget.calculate()
    assert expected in widget.output.toPlainText()
    assert widget.input_widgets["category"].currentText() != category


@pytest.mark.parametrize("tool_id,changes", [("X09", {"bits": 16}), ("X12", {"points": 4001})])
def test_large_real_map_and_curves_have_bounded_preview_and_complete_exact_export(widget, app, tmp_path, tool_id, changes):
    widget.select_tool(tool_id)
    widget.set_values(changes)
    assert widget.calculate() is None
    _settle(app, widget)
    result = widget.latest_result
    assert result is not None
    original = result.to_json()
    assert len(widget.output.toPlainText()) < 12_000
    assert "完整数据请导出" in widget.output.toPlainText()
    widget.details_toggle.setChecked(True)
    assert len(widget.output.toPlainText()) < 20_000
    assert widget.output.toPlainText() == details_text(result)
    output = result.outputs["mapping"] if tool_id == "X09" else result.outputs["curves"]["frequency_hz"]
    assert len(output) == (65536 if tool_id == "X09" else 4001)
    path = tmp_path / "complete.json"
    widget.export_to(path)
    assert path.read_bytes() == original.encode("utf-8")
    assert result.to_json() == original


@pytest.mark.parametrize("tool_id", TOOL_IDS)
def test_versioned_inputs_restore_recomputes_every_tool_with_exact_identity(widget, tmp_path, tool_id):
    from power_stage_design.toolbox_gui import INPUT_SCHEMA

    widget.select_tool(tool_id)
    original = widget.calculate()
    state = widget.state()
    path = tmp_path / (tool_id + ".json")
    widget.save_inputs_to(path)
    document = json.loads(path.read_text(encoding="utf-8"))
    assert document["schema"] == INPUT_SCHEMA
    assert document["toolkit_version"] == __version__
    assert document["model_version"] == MODEL_VERSION
    assert document["state"] == json.loads(json.dumps(state))
    assert document["input_provenance"] == widget._result_provenance(original)
    assert "outputs" not in document
    widget.select_tool("X04" if tool_id != "X04" else "X01")
    widget.calculate()
    widget.open_inputs_from(path)
    assert widget.current_tool_id == tool_id
    assert widget.state() == state
    assert widget.latest_result is not original
    assert widget.latest_result.to_json() == original.to_json()


@pytest.mark.parametrize("fault", [
    "schema", "model", "toolkit", "legacy", "provenance_null", "provenance_forged", "provenance_missing",
    "cached_outputs", "bad_state", "tool_id_type", "unknown_input", "missing_input", "numeric_string",
    "numeric_boolean", "ignored_numeric_boolean", "text_type", "invalid_domain", "duplicate_root", "duplicate_input",
    "nonfinite", "overflow", "giant_integer", "display_overflow", "deep", "truncated", "not_object", "invalid_utf8", "oversize",
])
def test_bad_input_files_leave_current_state_result_and_view_unchanged(widget, tmp_path, fault):
    widget.select_tool("X02")
    widget.calculate()
    path = tmp_path / "inputs.json"
    widget.save_inputs_to(path)
    document = json.loads(path.read_text(encoding="utf-8"))
    if fault in {"schema", "model", "toolkit"}:
        key = {"schema": "schema", "model": "model_version", "toolkit": "toolkit_version"}[fault]
        document[key] = "unknown-future-version"
    elif fault == "legacy":
        document = {"schema": "psd-toolbox-inputs-1", "state": document["state"]}
    elif fault == "provenance_null":
        document["input_provenance"] = None
    elif fault == "provenance_forged":
        document["input_provenance"]["result_id"] = "cached-result-from-another-model"
    elif fault == "provenance_missing":
        del document["input_provenance"]["source"]
    elif fault == "cached_outputs":
        document["outputs"] = {"minimum_capacitance_f": 0}
    elif fault == "bad_state":
        document["state"] = []
    elif fault == "tool_id_type":
        document["state"]["tool_id"] = []
    elif fault == "unknown_input":
        document["state"]["values"]["surprise"] = 1
    elif fault == "missing_input":
        del document["state"]["values"]["delta_current_a"]
    elif fault in {"numeric_string", "numeric_boolean", "invalid_domain"}:
        document["state"]["values"]["delta_current_a"] = {"numeric_string": "2.5", "numeric_boolean": True, "invalid_domain": -1}[fault]
    elif fault == "giant_integer":
        document["state"]["values"]["delta_current_a"] = 10 ** 400
    elif fault == "display_overflow":
        document["state"]["values"]["inductance_h"] = 1e308
    elif fault == "ignored_numeric_boolean":
        document["state"]["values"]["inductance_h"] = True
    elif fault == "text_type":
        widget.select_tool("X01")
        widget.calculate()
        widget.save_inputs_to(path)
        document = json.loads(path.read_text(encoding="utf-8"))
        document["state"]["values"]["a_source"] = {"fake": "source"}
    text = json.dumps(document)
    if fault == "duplicate_root":
        text = text.replace('"schema":', '"schema": "duplicate", "schema":', 1)
    elif fault == "duplicate_input":
        text = text.replace('"delta_current_a":', '"delta_current_a": 0, "delta_current_a":', 1)
    elif fault in {"nonfinite", "overflow"}:
        text = text.replace('"delta_current_a": 2.5', '"delta_current_a": ' + ("NaN" if fault == "nonfinite" else "1e999"))
    elif fault == "deep":
        text = '[' * 1000 + '0' + ']' * 1000
    elif fault == "truncated":
        text = text[:-3]
    elif fault == "not_object":
        text = '[]'
    elif fault == "oversize":
        text = ' ' * 256001
    path.write_bytes(b'\xff\xfe' if fault == "invalid_utf8" else text.encode("utf-8"))
    before = (copy.deepcopy(widget.state()), widget.latest_result, widget.output.toPlainText(), widget._generation)
    with pytest.raises(ValueError):
        widget.open_inputs_from(path)
    assert widget.state() == before[0]
    assert widget.latest_result is before[1]
    assert widget.output.toPlainText() == before[2]
    assert widget._generation == before[3]
    assert widget.export_button.isEnabled() and widget.save_inputs_button.isEnabled()


def test_input_save_uses_atomic_replace_and_preserves_previous_file_on_failure(widget, monkeypatch, tmp_path):
    widget.calculate()
    path = tmp_path / "inputs.json"
    old = b"previous saved input bytes"
    path.write_bytes(old)
    replacements = []

    def denied_replace(source, destination):
        replacements.append((source, destination))
        assert source.parent == path.parent
        assert json.loads(source.read_text(encoding="utf-8"))["state"] == widget.state()
        raise OSError("simulated atomic replacement failure")

    monkeypatch.setattr(Path, "replace", denied_replace)
    with pytest.raises(OSError, match="simulated"):
        widget.save_inputs_to(path)
    assert len(replacements) == 1
    assert path.read_bytes() == old
    assert list(tmp_path.iterdir()) == [path]


def test_import_provenance_rechecked_before_restore_and_never_converted_to_independent(widget, tmp_path):
    from dataclasses import replace

    from power_stage_design.models import DesignInputs
    from power_stage_design.topologies import calculate_stage

    inputs = DesignInputs("T01", 12, 5, 3, 100e3, 47e-6)
    stage = calculate_stage(inputs)
    widget.set_stage_result(stage)
    widget.select_tool("X01")
    widget.import_stage()
    original = widget.calculate()
    path = tmp_path / "imported.json"
    widget.save_inputs_to(path)
    widget.open_inputs_from(path)
    assert widget.latest_result.to_json() == original.to_json()
    assert widget.latest_result.inputs["stage_import"]["design_id"] == stage.design_id
    widget.set_stage_result(calculate_stage(replace(inputs, vin_v=13)))
    widget.select_tool("X04")
    unrelated = widget.calculate()
    with pytest.raises(ValueError, match="current stage"):
        widget.open_inputs_from(path)
    assert widget.latest_result is unrelated
    assert widget.current_tool_id == "X04"
    widget.set_stage_result(stage)
    document = json.loads(path.read_text(encoding="utf-8"))
    document["state"]["stage_import"]["mapped_inputs"]["frequency_hz"] = True
    path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(ValueError, match="provenance"):
        widget.open_inputs_from(path)
    assert widget.latest_result is unrelated


@pytest.mark.parametrize("action", ["export", "save", "open"])
@pytest.mark.parametrize("change", ["details", "edit", "close"])
def test_modal_action_ownership_tracks_input_not_details_toggle(widget, monkeypatch, tmp_path, action, change):
    from PySide6.QtWidgets import QFileDialog

    widget.select_tool("X02")
    original = widget.calculate()
    path = tmp_path / "action.json"
    widget.save_inputs_to(path)
    old = path.read_bytes()
    before = widget.state()

    def choose(*args):
        if change == "details":
            widget.details_toggle.setChecked(True)
        elif change == "edit":
            widget.input_widgets["delta_current_a"].setText("2")
            widget.calculate()
        else:
            assert widget.close()
        return str(path), ""

    monkeypatch.setattr(QFileDialog, "getOpenFileName" if action == "open" else "getSaveFileName", choose)
    {"export": widget._export_dialog, "save": widget._save_inputs_dialog, "open": widget._open_inputs_dialog}[action]()
    if change == "details":
        if action == "export":
            assert path.read_bytes() == original.to_json().encode("utf-8")
        else:
            assert widget.state() == before
            assert widget.latest_result.to_json() == original.to_json()
    else:
        assert path.read_bytes() == old
        assert widget.state()["values"]["delta_current_a"] == (2 if change == "edit" else 2.5)


def test_oversize_input_cannot_be_saved_as_an_unreadable_session(widget, tmp_path):
    widget.set_values({key: "来源" * 15000 for key in ("a_source", "b_source", "a_name", "b_name")})
    assert widget.calculate()
    path = tmp_path / "previous.json"
    path.write_bytes(b"previous")
    with pytest.raises(ValueError, match="256 kB"):
        widget.save_inputs_to(path)
    assert path.read_bytes() == b"previous"


def test_restore_replaces_pending_generation_without_old_worker_overwrite(widget, app, tmp_path):
    widget.select_tool("X02")
    original = widget.calculate()
    path = tmp_path / "inputs.json"
    widget.save_inputs_to(path)
    widget.select_tool("X09")
    widget.set_values({"bits": 16})
    assert widget.calculate() is None and widget._pending
    pending_generation = widget._generation
    widget.open_inputs_from(path)
    restored = widget.latest_result
    assert restored is not None and restored.to_json() == original.to_json()
    assert widget._generation > pending_generation
    _settle(app, widget)
    assert widget.latest_result is restored
    assert widget.current_tool_id == "X02"


def test_invalid_restore_preserves_current_pending_generation(widget, app, tmp_path):
    path = tmp_path / "bad.json"
    path.write_text('{"schema": "wrong"}', encoding="utf-8")
    widget.select_tool("X09")
    widget.set_values({"bits": 16})
    widget.calculate()
    generation = widget._generation
    with pytest.raises(ValueError):
        widget.open_inputs_from(path)
    assert widget._generation == generation and widget._pending
    _settle(app, widget)
    assert widget.latest_result is not None
    assert len(widget.latest_result.outputs["mapping"]) == 65536


def test_restore_rejects_text_that_would_be_silently_truncated_by_qlineedit(widget):
    original = widget.calculate()
    state = widget.state()
    state["values"]["a_source"] = "来源" * 20000
    with pytest.raises(ValueError, match="长度上限"):
        widget.restore_state(state)
    assert widget.latest_result is original
    assert widget.state()["values"]["a_source"] != state["values"]["a_source"]

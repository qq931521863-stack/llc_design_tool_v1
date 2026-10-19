"""Current-input debounce, branch-local text, and per-page stage ownership.

Real Qt edits and production kernels are used. Held worker callbacks establish
late-result ownership deterministically; they still execute the real kernel.
"""
from __future__ import annotations

import copy
import json
import math
import os
import time
from dataclasses import replace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


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
    view.show()
    app.processEvents()
    yield view
    assert view.close()
    view.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()


def wait_for(app, condition, timeout=10):
    deadline = time.monotonic() + timeout
    while not condition() and time.monotonic() < deadline:
        app.processEvents()
        # Release Python's GIL as the real QApplication event loop does;
        # spinning QTest.qWait can starve Python numerical workers in CI.
        time.sleep(.002)
    assert condition(), "Timed out waiting for current-input update"


def settle(app, widget):
    wait_for(app, lambda: not widget._auto_timer.isActive() and not widget._pending and not widget._workers)
    app.processEvents()


def choose(widget, key, value):
    control = widget.input_widgets[key]
    control.setCurrentIndex(control.findData(value))


def test_keystrokes_debounce_last_valid_input_and_exact_export(widget, app, tmp_path):
    from PySide6.QtTest import QTest
    widget.select_tool("X10")
    original = widget.calculate()
    assert original.outputs["converted_value"] == 100
    entry = widget.input_widgets["value"]
    entry.selectAll()
    QTest.keyClicks(entry, "-")
    assert widget.latest_result is None and not widget.export_button.isEnabled()
    assert "编辑" in widget.status.text()
    settle(app, widget)
    assert widget.latest_result is None and "有限数字" in widget.status.text()
    for text in ("2", "20", "200", "2000"):
        entry.setText(text)
        assert widget.latest_result is None
    settle(app, widget)
    result = widget.latest_result
    # Independent exact unit definition: 1 G = 1e-4 T = 0.1 mT.
    assert result.inputs["value"] == 2000
    assert result.outputs["converted_value"] == 200
    for suffix in ("json", "csv", "txt"):
        path = tmp_path / ("current." + suffix)
        widget.export_to(path)
        assert result.result_id in path.read_text()
        assert original.result_id not in path.read_text()


@pytest.mark.parametrize("text", ["", "-", "nan", "inf", "-300", "oops"])
def test_device_load_rejects_temperature_without_changing_any_parameters(widget, app, text):
    widget.select_tool("X01")
    widget.calculate()
    page = widget.pages["X01"]
    page.widgets["a_temperature_c"].setText(text)
    before = page.snapshot()
    page.load_device("a", widget.database.primary[0])
    assert page.snapshot() == before
    assert page.widgets["a_temperature_c"].text() == text
    assert "未载入器件" in widget.status.text()
    assert widget.latest_result is None
    settle(app, widget)
    assert widget.latest_result is None and not widget.export_button.isEnabled()


def test_valid_temperature_load_uses_actual_library_conditions(widget, app):
    from power_stage_design.toolbox import fet_parameters_from_device
    widget.select_tool("X01")
    page = widget.pages["X01"]
    page.widgets["a_temperature_c"].setText("75")
    device = widget.database.primary[0]
    expected = fet_parameters_from_device(device, 75)
    page.load_device("a", device)
    settle(app, widget)
    result = widget.latest_result
    assert result is not None and result.status == "PARTIAL"
    assert page.values()["a_temperature_c"] == 75
    assert page.values()["a_rds_on_ohm"] == expected.rds_on_ohm
    assert page.widgets["a_qgd_c"].text() == ""


@pytest.mark.parametrize("tool, selector, first, second, key", [
    ("X01", "compare", True, False, "b_rds_on_ohm"),
    ("X02", "control", "VMC", "CMC", "inductance_h"),
    ("X03", "branch_count", 3, 2, "c3"),
    ("X07", "operation", "evaluate", "design", "high_ohm"),
    ("X07", "operation", "design", "evaluate", "known_resistance_ohm"),
    ("X12", "configuration", "pi", "lc", "filter_capacitance_f"),
    ("X12", "configuration", "pi", "lc", "filter_esr_ohm"),
    ("X12", "configuration", "pi", "lc", "filter_esl_h"),
])
def test_inactive_invalid_text_survives_mode_switch_save_and_restore(widget, app, tmp_path, tool, selector, first, second, key):
    widget.select_tool(tool)
    def switch(value):
        if selector == "compare":
            widget.input_widgets[selector].setChecked(value)
        else:
            choose(widget, selector, value)
    switch(first)
    widget.input_widgets[key].setText("-")
    settle(app, widget)
    assert widget.latest_result is None
    switch(second)
    settle(app, widget)
    result = widget.latest_result
    assert result is not None, widget.status.text()
    assert widget.input_widgets[key].text() == "-"
    state = widget.state()
    assert state["raw_inputs"][key] == "-"
    assert key not in state["values"]
    path = tmp_path / "inputs.json"
    widget.save_inputs_to(path)
    widget.input_widgets[key].setText("123")
    widget.open_inputs_from(path)
    assert widget.latest_result.result_id == result.result_id
    assert widget.input_widgets[key].text() == "-"
    switch(first)
    settle(app, widget)
    assert widget.latest_result is None and "有限数字" in widget.status.text()
    assert not widget.export_button.isEnabled()


def test_cmc_inactive_inductance_does_not_change_independent_expected_capacitance(widget, app):
    widget.select_tool("X02")
    choose(widget, "control", "VMC")
    widget.input_widgets["inductance_h"].clear()
    choose(widget, "control", "CMC")
    settle(app, widget)
    result = widget.latest_result
    # TI CMC expression, independently evaluated from the visible default inputs.
    expected = 1 / (2 * math.pi * 10_000 * (.15 / 2.5 - .05) * 2 * math.sin(math.pi / 6))
    assert result.outputs["minimum_capacitance_f"] == pytest.approx(expected, rel=1e-14)
    assert result.inputs["inductance_h"] is None
    assert widget.input_widgets["inductance_h"].text() == ""


def stages():
    from power_stage_design.models import DesignInputs
    from power_stage_design.topologies import calculate_stage
    inputs = DesignInputs("T01", 12, 5, 3, 100e3, 47e-6)
    return calculate_stage(inputs), calculate_stage(replace(inputs, vin_v=13))


def test_multiple_page_imports_remain_bound_across_stage_switch_save_restore(widget, app, tmp_path):
    first, second = stages()
    widget.set_stage_result(first)
    for tool in ("X01", "X12"):
        widget.select_tool(tool)
        widget.import_stage()
        assert widget.calculate().inputs["stage_import"]["design_id"] == first.design_id
    state = widget.state()
    assert state["page_states"]["X01"]["stage_import"]["design_id"] == first.design_id
    path = tmp_path / "both-pages.json"
    widget.save_inputs_to(path)
    widget.set_stage_result(second)
    for tool in ("X01", "X12"):
        widget.select_tool(tool)
        settle(app, widget)
        assert widget.latest_result is None
        assert widget.calculate() is None and "stale" in widget.status.text()
        assert widget.state()["stage_import"]["design_id"] == first.design_id
        with pytest.raises(ValueError):
            widget.export_to(tmp_path / "stale.txt")
        with pytest.raises(ValueError):
            widget.save_inputs_to(tmp_path / "stale.json")
    widget.set_stage_result(first)
    widget.open_inputs_from(path)
    assert widget.latest_result.inputs["stage_import"]["design_id"] == first.design_id
    widget.select_tool("X01")
    settle(app, widget)
    assert widget.latest_result.inputs["stage_import"]["design_id"] == first.design_id
    widget.input_widgets["drain_voltage_v"].setText("48")
    settle(app, widget)
    assert "stage_import" not in widget.latest_result.inputs
    widget.set_stage_result(second)
    assert widget.latest_result is not None  # explicitly independent X01 only
    widget.select_tool("X12")
    settle(app, widget)
    assert widget.latest_result is None and "stale" in widget.status.text()


def test_restoring_nonselected_stale_page_keeps_input_binding_without_result_authority(widget, app, tmp_path):
    first, second = stages()
    widget.set_stage_result(first)
    widget.select_tool("X01")
    widget.import_stage()
    widget.calculate()
    widget.set_stage_result(second)
    widget.select_tool("X10")
    widget.calculate()
    path = tmp_path / "stale-other-page.json"
    widget.save_inputs_to(path)
    widget.select_tool("X01")
    widget.input_widgets["drain_voltage_v"].setText("48")
    assert widget.calculate() is not None
    widget.open_inputs_from(path)
    assert widget.latest_result.tool_id == "X10"
    widget.select_tool("X01")
    settle(app, widget)
    assert widget.latest_result is None and "stale" in widget.status.text()


@pytest.mark.parametrize("tool", ["X09", "X12"])
@pytest.mark.parametrize("action", ["cancel", "switch", "edit", "close_reopen"])
def test_timer_and_real_late_worker_never_republish_abandoned_input(widget, app, monkeypatch, tool, action):
    from PySide6.QtTest import QTest

    from power_stage_design.toolbox_gui import AUTO_UPDATE_DELAY_MS
    held = []
    monkeypatch.setattr(widget._pool, "start", held.append)
    widget.select_tool(tool)
    choose(widget, "bits" if tool == "X09" else "points", 13 if tool == "X09" else 4001)
    wait_for(app, lambda: bool(held))
    assert widget._pending and widget.cancel_button.isEnabled()
    if action == "cancel":
        widget.cancel_pending()
    elif action == "switch":
        widget.select_tool("X10")
        widget.calculate()
    elif action == "edit":
        choose(widget, "bits" if tool == "X09" else "points", 4 if tool == "X09" else 101)
        widget.calculate()
    else:
        assert widget.close()
        widget.show()
    expected = widget.latest_result
    # Compute the genuinely scheduled kernel, then deliver its now-late callback.
    held[0].run()
    app.processEvents()
    QTest.qWait(AUTO_UPDATE_DELAY_MS + 25)
    app.processEvents()
    assert widget.latest_result is expected
    assert not widget._pending
    if expected is not None:
        assert expected.tool_id == (tool if action == "edit" else "X10")
        if action == "edit":
            data = expected.outputs["mapping"] if tool == "X09" else expected.outputs["curves"]["frequency_hz"]
            assert len(data) == (16 if tool == "X09" else 101)
    else:
        assert not widget.export_button.isEnabled()


@pytest.mark.parametrize("action", ["cancel", "switch", "close_reopen"])
def test_unfired_debounce_is_owned_by_tool_and_stopped_on_cancel_close(widget, app, action):
    widget.select_tool("X10")
    widget.input_widgets["value"].setText("2000")
    assert widget._auto_timer.isActive()
    if action == "cancel":
        widget.cancel_pending()
    elif action == "switch":
        widget.select_tool("X04")
    else:
        assert widget.close()
        widget.show()
    settle(app, widget)
    assert widget.latest_result is None


def test_lc_without_cf_matches_independent_series_l_shunt_c_transfer(widget, app):
    widget.select_tool("X12")
    widget.set_values({"configuration": "lc", "inductor_dcr_ohm": 0.0,
                       "converter_esr_ohm": 0.0, "converter_esl_h": 0.0})
    for key in ("filter_capacitance_f", "filter_esr_ohm", "filter_esl_h"):
        widget.input_widgets[key].setText("invalid")
    settle(app, widget)
    result = widget.latest_result
    assert all(result.inputs[key] is None for key in ("filter_capacitance_f", "filter_esr_ohm", "filter_esl_h"))
    curves = result.outputs["curves"]
    frequency = curves["frequency_hz"][12]
    w = 2 * math.pi * frequency
    inductance, capacitance = 4.7e-6, 4.7e-6
    # Inductor parallel self-capacitance retained by the actual network.
    self_capacitance = 1 / (inductance * (2 * math.pi * 16e6) ** 2)
    zl = 1j * w * inductance / (1 - w*w*inductance*self_capacitance)
    zc = 1 / (1j * w * capacitance)
    expected = abs(zc / (zl + zc))
    assert curves["other_to_noise_undamped_magnitude"][12] == pytest.approx(expected, rel=1e-13)
    assert curves["noise_to_other_magnitude"] == [1.0] * 401


@pytest.mark.parametrize("fault", ["raw_unknown", "raw_choice", "raw_overlap", "other_origin_boolean", "other_unknown", "duplicate_current"])
def test_extended_state_invalidity_is_transactional(widget, fault):
    first, _ = stages()
    widget.set_stage_result(first)
    widget.select_tool("X01")
    widget.import_stage()
    widget.calculate()
    widget.select_tool("X10")
    original = widget.calculate()
    before = copy.deepcopy(widget.state())
    state = copy.deepcopy(before)
    if fault == "raw_unknown":
        state["raw_inputs"] = {"nonexistent": "-"}
    elif fault == "raw_choice":
        state["raw_inputs"] = {"category": "-"}
        del state["values"]["category"]
    elif fault == "raw_overlap":
        state["raw_inputs"] = {"value": "-"}
    elif fault == "other_origin_boolean":
        state["page_states"]["X01"]["stage_import"]["mapped_inputs"]["frequency_hz"] = True
    elif fault == "other_unknown":
        state["page_states"]["unknown"] = {}
    else:
        state["page_states"]["X10"] = {"values": state["values"], "stage_import": None}
    with pytest.raises(ValueError):
        widget.restore_state(state)
    assert widget.latest_result is original
    assert widget.state() == before


@pytest.mark.parametrize("imported", [False, True])
def test_legacy_schema_two_lc_identity_is_recomputed_and_migrated_honestly(widget, tmp_path, imported):
    from llc_design import __version__
    from power_stage_design.toolbox import (
        MODEL_VERSION,
        FilterInputs,
        differential_filter,
    )
    from power_stage_design.toolbox_gui import INPUT_SCHEMA
    widget.select_tool("X12")
    choose(widget, "configuration", "lc")
    if imported:
        first, _ = stages()
        widget.set_stage_result(first)
        widget.import_stage()
    values = widget.pages["X12"].values()
    original = differential_filter(FilterInputs(**values))
    origin = widget._stage_imports.get("X12")
    if origin:
        original = replace(original, inputs={**original.inputs, "stage_import": origin})
    legacy = {"schema": INPUT_SCHEMA, "toolkit_version": __version__, "model_version": MODEL_VERSION,
              "state": {"tool_id": "X12", "values": values, "stage_import": origin},
              "input_provenance": widget._result_provenance(original)}
    path = tmp_path / "legacy-lc.json"
    path.write_text(json.dumps(legacy))
    widget.open_inputs_from(path)
    current = widget.latest_result
    assert current.result_id != original.result_id
    assert current.outputs == original.outputs
    assert current.inputs["filter_capacitance_f"] is None
    assert widget.input_widgets["filter_capacitance_f"].text() == "10"
    assert "旧版 LC" in widget.status.text()
    if imported:
        assert current.inputs["stage_import"] == origin
    legacy["input_provenance"]["result_id"] = "forged-historical-result"
    path.write_text(json.dumps(legacy))
    with pytest.raises(ValueError, match="未采用历史结果"):
        widget.open_inputs_from(path)
    assert widget.latest_result is current


@pytest.mark.parametrize("tool", ["X09", "X12"])
def test_heavy_autoupdate_uses_real_worker_and_publishes_only_final_inputs(widget, app, tool):
    widget.select_tool(tool)
    choose(widget, "bits" if tool == "X09" else "points", 13 if tool == "X09" else 4001)
    settle(app, widget)
    result = widget.latest_result
    assert result is not None and result.tool_id == tool
    if tool == "X09":
        assert len(result.outputs["mapping"]) == 8192
        assert result.outputs["mapping"][-1]["output_v"] == pytest.approx(12)
    else:
        assert len(result.outputs["curves"]["frequency_hz"]) == 4001
    assert widget.export_button.isEnabled() and not widget.cancel_button.isEnabled()


def test_stale_restored_origin_is_revalidated_when_matching_stage_returns(widget, app):
    first, second = stages()
    widget.set_stage_result(first)
    widget.select_tool("X01")
    widget.import_stage()
    widget.calculate()
    widget.set_stage_result(second)
    widget.select_tool("X10")
    widget.calculate()
    state = widget.state()
    snapshot = state["page_states"]["X01"]
    snapshot["values"]["frequency_hz"] *= 2
    snapshot["stage_import"]["mapped_inputs"]["frequency_hz"] *= 2
    # Without the originating stage it is retained only as an untrusted binding.
    widget.restore_state(state)
    widget.set_stage_result(first)
    widget.select_tool("X01")
    settle(app, widget)
    assert widget.latest_result is None
    assert "mapped values" in widget.status.text()
    assert not widget.export_button.isEnabled()


def heavy_input_file(widget, app, tmp_path, tool):
    widget.select_tool(tool)
    choose(widget, "bits" if tool == "X09" else "points", 13 if tool == "X09" else 4001)
    widget.calculate()
    settle(app, widget)
    expected = widget.latest_result
    path = tmp_path / (tool + "-inputs.json")
    widget.save_inputs_to(path)
    widget.select_tool("X10")
    old = widget.calculate()
    return path, expected, old


def open_dialog(widget, monkeypatch, path):
    from PySide6.QtWidgets import QFileDialog
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *args: (str(path), ""))
    widget._open_inputs_dialog()


@pytest.mark.parametrize("tool", ["X09", "X12"])
def test_real_file_open_worker_leaves_gui_responsive_and_adopts_atomically(widget, app, tmp_path, monkeypatch, tool):
    import threading

    from PySide6.QtCore import QTimer

    from power_stage_design.toolbox_gui import TOOLS
    path, expected, old = heavy_input_file(widget, app, tmp_path, tool)
    title, description, fields, compute = TOOLS[tool]
    started, release = threading.Event(), threading.Event()
    ticks = []

    def held_compute(values):
        started.set()
        if not release.wait(3):
            raise RuntimeError("test worker release timed out")
        return compute(values)

    monkeypatch.setitem(TOOLS, tool, (title, description, fields, held_compute))
    try:
        before = widget.state()
        open_dialog(widget, monkeypatch, path)
        assert widget._restore_pending and widget.latest_result is old
        wait_for(app, started.is_set)
        QTimer.singleShot(1, lambda: ticks.append("GUI is responsive"))
        wait_for(app, lambda: bool(ticks))
        assert widget.state() == before and widget.current_tool_id == "X10"
        assert widget.latest_result is old and widget.export_button.isEnabled()
        release.set()
        settle(app, widget)
        assert widget.latest_result is not expected
        assert widget.latest_result.to_json() == expected.to_json()
        assert widget.current_tool_id == tool
        assert not widget._restore_pending
    finally:
        release.set()


@pytest.mark.parametrize("action", ["cancel", "edit", "switch", "new_request", "malformed_new_request", "close_reopen", "stage_change"])
def test_file_open_late_callback_cannot_replace_newer_user_action(widget, app, tmp_path, monkeypatch, action):
    path, _expected, old = heavy_input_file(widget, app, tmp_path, "X12")
    held = []
    monkeypatch.setattr(widget._pool, "start", held.append)
    open_dialog(widget, monkeypatch, path)
    assert widget._restore_pending and widget.latest_result is old
    if action == "cancel":
        widget.cancel_pending()
        assert widget.latest_result is old
    elif action == "edit":
        widget.input_widgets["value"].setText("3000")
        widget.calculate()
    elif action == "switch":
        widget.select_tool("X02")
        widget.calculate()
    elif action == "new_request":
        other = tmp_path / "new-fast-inputs.json"
        widget.save_inputs_to(other)
        open_dialog(widget, monkeypatch, other)
    elif action == "malformed_new_request":
        broken = tmp_path / "bad-inputs.json"
        broken.write_text("{broken")
        open_dialog(widget, monkeypatch, broken)
        assert "打开失败" in widget.status.text()
    elif action == "stage_change":
        first, _ = stages()
        widget.set_stage_result(first)
    else:
        assert widget.close()
        widget.show()
    current, state = widget.latest_result, widget.state()
    assert not widget._restore_pending
    held[0].run()
    app.processEvents()
    assert widget.latest_result is current
    assert widget.state() == state
    assert not widget._pending


@pytest.mark.parametrize("fault", ["domain", "provenance", "malformed_inactive_page"])
def test_file_open_worker_failure_does_not_mutate_current_inputs_or_result(widget, app, tmp_path, monkeypatch, fault):
    path, _expected, old = heavy_input_file(widget, app, tmp_path, "X09")
    document = json.loads(path.read_text())
    if fault == "domain":
        document["state"]["values"]["top_resistance_ohm"] = -1
    elif fault == "provenance":
        document["input_provenance"]["result_id"] = "forged-historical-result"
    else:
        document["state"]["page_states"] = {"X02": {"values": {}, "stage_import": None}}
    path.write_text(json.dumps(document))
    before = widget.state()
    open_dialog(widget, monkeypatch, path)
    settle(app, widget)
    assert widget.latest_result is old
    assert widget.state() == before
    assert "打开失败" in widget.status.text()
    assert widget.export_button.isEnabled() and widget.save_inputs_button.isEnabled()


@pytest.mark.parametrize("update", [
    {"value": 2000, "to_unit": "not-a-unit"},
    {"category": "temperature", "value": 50, "from_unit": "C", "to_unit": "not-a-unit"},
    {"category": "not-a-category", "value": 2000},
    {"value": 2000, "to_unit": True},
])
def test_public_multi_field_update_is_atomic_before_any_widget_or_result_change(widget, app, tmp_path, update):
    widget.select_tool("X10")
    original = widget.calculate()
    before = widget.state()
    generation = widget._generation
    units = {key: [widget.input_widgets[key].itemData(k) for k in range(widget.input_widgets[key].count())]
             for key in ("from_unit", "to_unit")}
    with pytest.raises(ValueError):
        widget.set_values(update)
    settle(app, widget)
    assert widget.state() == before
    assert widget.latest_result is original
    assert widget._generation == generation
    assert widget.input_widgets["value"].text() == "1000"
    assert not widget.pages["X10"]._loading
    for key, expected in units.items():
        assert [widget.input_widgets[key].itemData(k) for k in range(widget.input_widgets[key].count())] == expected
    path = tmp_path / "unchanged.json"
    widget.export_to(path)
    assert json.loads(path.read_text())["inputs"]["value"] == 1000


def test_malformed_actual_device_library_load_is_transactional_and_rejects_prior_output(widget, app, tmp_path):
    from dataclasses import asdict

    from llc_design.models.devices import DeviceDatabase
    from power_stage_design.toolbox import fet_parameters_from_device

    record = asdict(widget.database.primary[0])
    record.update(part_number="Malformed real library record", gate_voltage_v=True)
    library = tmp_path / "malformed-library.json"
    library.write_text(json.dumps({"primary_mosfets": [record], "sr_mosfets": []}))
    database = DeviceDatabase(path=library, include_user=False)
    device = database.primary[0]
    assert fet_parameters_from_device(device, 100).gate_voltage_v is True
    widget.select_tool("X01")
    widget.calculate()
    page = widget.pages["X01"]
    before = page.snapshot()
    page.load_device("a", device)
    settle(app, widget)
    assert page.snapshot() == before
    assert page.widgets["a_name"].text() == "Manual A"
    assert page.widgets["a_temperature_c"].text() == "100"
    assert not page._loading
    assert widget.latest_result is None
    assert "未载入器件" in widget.status.text()
    assert not widget.export_button.isEnabled() and not widget.save_inputs_button.isEnabled()
    with pytest.raises(ValueError):
        widget.export_to(tmp_path / "rejected-device.json")

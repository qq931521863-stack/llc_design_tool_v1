"""Real Qt tolerance workers and current-state restore/report ownership."""
import json
import os
import time
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
qt = pytest.importorskip("PySide6.QtWidgets")
from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtTest import QTest

from power_stage_design.gui import PowerStageMainWindow
from power_stage_design.loop import Compensator, analyze_loop
from power_stage_design.report import write_report

NEW_FIELDS = ("secondary_inductance_factors", "loop_capacitance2_factors",
              "loop_esr_factors", "loop_esr2_factors", "loop_dcr_factors")


@pytest.fixture(scope="module")
def app():
    yield qt.QApplication.instance() or qt.QApplication([])


@pytest.fixture
def window(app):
    view = PowerStageMainWindow()
    view.show()
    app.processEvents()
    yield view
    view.close()
    view.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()


def settle(app, window):
    deadline = time.monotonic() + 30
    while window._jobs and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.005)
    assert not window._jobs
    assert window._pool.waitForDone(1000)
    app.processEvents()


def configure(window):
    for key, value in {"capacitance2_f": 22., "esr_ohm": .03,
                       "esr2_ohm": .12, "dcr_ohm": .08}.items():
        window.loop_widgets[key].setValue(value)
    window.analyze_loop()
    assert window.loop_result is not None
    for key, line in window.corner_fields.items():
        line.setText({"input_voltages_v": "12", "output_currents_a": "3"}.get(key, "1"))
    for key in NEW_FIELDS[1:]:
        window.corner_fields[key].setText("0.8, 1.2")


def click_scan(app, window):
    window.tabs.setCurrentWidget(window.corner_table.parentWidget())
    app.processEvents()
    button = next(button for button in window.findChildren(qt.QPushButton) if button.text() == "扫描所列工况")
    QTest.mouseClick(button, Qt.MouseButton.LeftButton)
    settle(app, window)
    assert window.corner_sweep is not None, window.corner_status.text()
    return window.corner_sweep


def test_real_new_dimensions_save_restore_recompute_and_report(app, window, tmp_path):
    configure(window)
    sweep = click_scan(app, window)
    assert len(sweep.points) == 16
    assert len({point.design_id for point in sweep.points}) == 1
    assert len({point.point_id for point in sweep.points}) == 16
    assert window.corner_table.rowCount() == len(sweep.extrema)
    for index, (metric, extrema) in enumerate(sweep.extrema.items()):
        for col, kind in ((2, "minimum"), (4, "maximum")):
            point = sweep.points[extrema[kind]["point_index"]]
            item = window.corner_table.item(index, col)
            assert "C2=" in item.text() and "ESR1=" in item.text() and "DCR=" in item.text()
            assert point.point_id in item.toolTip()
        if metric.startswith("loop."):
            for point in sweep.points:
                expected = analyze_loop(point.loop_plant, Compensator(**sweep.controller))
                attribute = {"loop.phase_margin_deg": "worst_phase_margin_deg", "loop.gain_margin_db": "worst_gain_margin_db",
                             "loop.crossover_hz": "main_crossover_hz", "loop.ms": "ms"}[metric]
                assert point.metrics[metric] == pytest.approx(getattr(expected.stability, attribute))
    output_dir = Path(os.environ.get("PSD_EVIDENCE_DIR", tmp_path))
    output_dir.mkdir(parents=True, exist_ok=True)
    saved = output_dir / "A01-expanded-tolerances.json"
    window.save_to(saved)
    document = json.loads(saved.read_text(encoding="utf-8"))
    assert "corner_sweep" not in document
    for key in NEW_FIELDS[1:]:
        assert document["corner_request"][key] == [.8, 1.2]
    window.loop_widgets["capacitance2_f"].setValue(80.)
    window.corner_fields["loop_esr2_factors"].setText("0.2, 2")
    window.open_from(saved)
    settle(app, window)
    assert window.corner_sweep == sweep
    assert window.corner_sweep is not sweep
    window.report_to(output_dir / "A01-expanded-tolerances.html")
    report = (output_dir / "A01-expanded-tolerances.html").read_text(encoding="utf-8")
    assert "loop-only" in report
    for point in sweep.points:
        assert point.point_id in report
    window.tabs.setCurrentWidget(window.corner_table.parentWidget())
    app.processEvents()
    assert window.grab().save(str(output_dir / "A01-expanded-tolerances-qt.png"))


@pytest.mark.parametrize("field", NEW_FIELDS)
def test_every_new_field_invalidates_inflight_scan_and_late_result(app, window, field):
    configure(window)
    original = click_scan(app, window)
    for key in NEW_FIELDS:
        window.corner_fields[key].setText("1")
    window.corner_fields["input_voltages_v"].setText(", ".join(str(10+i*.01) for i in range(100)))
    window.corner_fields["output_currents_a"].setText("1, 1.5, 2, 2.5, 3")
    window.run_corners()
    token = window._sweep_generation
    assert window._jobs
    window.corner_fields[field].setText("0.9, 1.1")
    assert window.corner_sweep is None
    assert window.corner_table.rowCount() == 0
    status = window.corner_status.text()
    window._on_corners((token, original, None))
    assert window.corner_sweep is None
    settle(app, window)
    assert window.corner_status.text() == status


@pytest.mark.parametrize("field", NEW_FIELDS[1:])
def test_tolerance_edit_inside_report_chooser_does_not_write_stale_report(app, window, tmp_path, monkeypatch, field):
    configure(window)
    click_scan(app, window)
    destination = tmp_path / "stale.html"

    def choose(*args):
        window.corner_fields[field].setText("0.6, 1.7")
        return str(destination), "HTML"

    monkeypatch.setattr(qt.QFileDialog, "getSaveFileName", choose)
    window._report_dialog()
    assert not destination.exists()
    assert window.corner_sweep is None
    saved = tmp_path / "latest.json"
    window.save_to(saved)
    assert json.loads(saved.read_text(encoding="utf-8"))["corner_request"][field] == [.6, 1.7]
    click_scan(app, window)
    window.report_to(destination)
    assert "逐指标多工况极值" in destination.read_text(encoding="utf-8")


def test_legacy_restore_resets_new_dimensions_not_current_edited_values(app, window, tmp_path):
    configure(window)
    click_scan(app, window)
    path = tmp_path / "legacy.json"
    window.save_to(path)
    document = json.loads(path.read_text(encoding="utf-8"))
    for key in NEW_FIELDS:
        del document["corner_request"][key]
        window.corner_fields[key].setText("0.2, 2")
    path.write_text(json.dumps(document), encoding="utf-8")
    window.open_from(path)
    settle(app, window)
    assert len(window.corner_sweep.points) == 1
    assert window.corner_sweep.points[0].loop_plant.capacitance2_f == pytest.approx(22e-6)
    for key in NEW_FIELDS:
        assert window.corner_fields[key].text() == "1"


def test_missing_loop_in_project_is_rejected_before_ui_restore(app, window, tmp_path):
    configure(window)
    original = click_scan(app, window)
    path = tmp_path / "invalid.json"
    window.save_to(path)
    document = json.loads(path.read_text(encoding="utf-8"))
    document["loop_settings"] = None
    document["inputs"]["vin_v"] = 13.
    path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(ValueError, match="requires"):
        window.open_from(path)
    assert window.corner_sweep is original
    assert window.result.inputs.vin_v == 12.


def test_actual_zero_nominal_rejects_scan_without_claiming_points(app, window):
    configure(window)
    window.loop_widgets["capacitance2_f"].setValue(0.)
    window.analyze_loop()
    window.run_corners()
    settle(app, window)
    assert window.corner_sweep is None
    assert window.corner_table.rowCount() == 0
    assert "positive" in window.corner_status.text()


def test_current_plant_change_rejects_historical_loop_only_sweep(app, window, tmp_path):
    configure(window)
    sweep = click_scan(app, window)
    base_id = window.result.design_id
    window.loop_widgets["esr2_ohm"].setValue(.16)
    window.analyze_loop()
    assert window.result.design_id == base_id
    destination = tmp_path / "old.html"
    with pytest.raises(ValueError, match="different loop"):
        write_report(window.result, destination, loop_result=window.loop_result,
                     loop_settings=window._loop_settings, corner_sweep=sweep)
    assert not destination.exists()

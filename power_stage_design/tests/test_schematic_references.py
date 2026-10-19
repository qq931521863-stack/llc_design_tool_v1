"""Winding references checked against independently reconstructed circuit nodes."""
import os
from dataclasses import replace

import numpy as np
import pytest

from power_stage_design.models import DesignInputs
from power_stage_design.topologies import calculate_stage


def _inputs(topology, light=False):
    if topology == "T11":
        return DesignInputs(topology, 325, 12, 1, 100e3, 1e-3,
                            turns_ratio_np_ns=10, diode_drop_v=.7, resonant_capacitance_f=100e-12)
    if topology == "T18":
        return DesignInputs(topology, 400, 48, .1 if light else 5, 100e3, 100e-6,
                            turns_ratio_np_ns=4, diode_drop_v=.7,
                            magnetizing_inductance_h=1e-3, leakage_inductance_h=20e-6)
    return DesignInputs(topology, 48, 5, .03 if light else 2, 100e3, 470e-6,
                        capacitance_f=1e-3, turns_ratio_np_ns=2, diode_drop_v=.7,
                        transfer_capacitance_f=1e-3, magnetizing_inductance_h=1e-2 if light else 1e-3)


def _samples(result, key, field):
    # The same interior phases for all branches, avoiding event-boundary
    # conventions while exercising both polarities and off/reset intervals.
    wave = getattr(result.waveforms[key], field)
    times = np.linspace(.000013, .999987, 503)
    answer = np.empty_like(times)
    for segment in wave.segments:
        mask = (times >= segment.start_fraction) & (times < segment.end_fraction)
        answer[mask] = segment.evaluate((times[mask]-segment.start_fraction)/segment.duration_fraction)
    return answer


@pytest.mark.parametrize("topology", ["T15", "T16", "T17"])
def test_low_lm_light_load_keeps_its_explicit_unsupported_commutation_rejection(topology):
    # The first light-load fixture hit a genuinely unsupported physical mode.
    # Correcting that fixture must never weaken the production domain guard.
    from power_stage_design.models import ModelDomainError
    with pytest.raises(ModelDomainError, match="commutation capacitances are required"):
        calculate_stage(replace(_inputs(topology, light=True), magnetizing_inductance_h=1e-3))


@pytest.mark.parametrize("topology,light", [(f"T{i:02}", False) for i in range(9, 19)] +
                         [(f"T{i:02}", True) for i in range(15, 19)] +
                         [(f"T{i:02}", "dcm") for i in range(15, 19)])
def test_named_winding_ports_agree_with_switch_and_rectifier_node_voltages(topology, light):
    if light == "dcm":
        inputs = (replace(_inputs(topology), iout_a=.1) if topology == "T18" else
                  replace(_inputs(topology), iout_a=.001 if topology == "T15" else .005,
                          magnetizing_inductance_h=.1, diode_drop_v=.7))
    else:
        inputs = _inputs(topology, light)
    result = calculate_stage(inputs)
    if light == "dcm" or (light and topology == "T18"):
        assert result.mode == "DCM"
    elif topology in ("T15", "T16", "T17", "T18"):
        # The original .03 A / 10 mH light-load fixture is still CCM.
        assert result.mode == "CCM"
    voltage = lambda key: _samples(result, key, "voltage_v")
    current = lambda key: _samples(result, key, "current_a")
    vin, vo = result.inputs.vin_v, result.inputs.vout_v
    if "secondary" in result.waveforms:
        # The winding delivers current into the rectifier's anode. Thus the
        # positive winding current runs from isolated return toward that end.
        sec = result.waveforms["secondary"]
        assert sec.current_a.reference.startswith("ISO to SEC")
        assert sec.voltage_v.reference == "SEC minus ISO"
        rectifier_node = vo if topology in ("T09", "T10", "T11") else vo+voltage("L1")
        np.testing.assert_allclose(voltage("secondary"), rectifier_node-voltage("D1"), atol=2e-10)
        np.testing.assert_allclose(current("secondary"), current("D1"), atol=2e-12)
        primary_top = "P" if topology in ("T10", "T14") else "VIN"
        assert result.waveforms["primary"].voltage_v.reference == primary_top+" minus N"
        np.testing.assert_allclose(voltage("primary"), vin-voltage("Q1")-(voltage("Q2") if topology in ("T10", "T14") else 0), atol=2e-10)
    else:
        rectifier_node = vo+voltage("L1")
        for number in (1, 2):
            sec = result.waveforms[f"secondary{number}"]
            assert sec.current_a.reference.startswith(f"ISO to S{number}")
            assert sec.voltage_v.reference == f"S{number} minus ISO"
            np.testing.assert_allclose(voltage(f"secondary{number}"), rectifier_node-voltage(f"D{number}"), atol=2e-10)
            np.testing.assert_allclose(current(f"secondary{number}"), current(f"D{number}"), atol=2e-12)
    if topology == "T16":
        for number in (1, 2):
            winding = result.waveforms[f"primary{number}"]
            assert winding.current_a.reference.startswith(f"VIN to P{number}")
            assert winding.voltage_v.reference.startswith(f"VIN minus P{number}")
            np.testing.assert_allclose(voltage(f"primary{number}"), vin-voltage(f"Q{number}"), atol=2e-10)
            np.testing.assert_allclose(current(f"primary{number}"), current(f"Q{number}"), atol=2e-12)
        # Np2 has its dot at P2. Its displayed voltage is centre tap minus dot,
        # so claiming dot-minus-undotted is a genuine sign reversal.
        assert voltage("primary2")[0] < 0 < voltage("primary1")[0]
    if topology == "T13":
        assert result.waveforms["reset_winding"].current_a.reference.startswith("GND to RESET")
        assert result.waveforms["reset_winding"].voltage_v.reference == "RESET minus GND"
        np.testing.assert_allclose(voltage("reset_winding"), vin-voltage("Dreset1"), atol=2e-10)
    if topology in {f"T{i:02}" for i in range(12, 19)}:
        assert result.waveforms["Cout"].current_a.reference == "OUT to ISO (into Cout positive plate)"
        np.testing.assert_allclose(current("Cout"), current("L1")-result.inputs.iout_a, atol=2e-12)


@pytest.mark.parametrize("mode", ["CCM", "DCM"])
def test_real_gui_restore_and_html_preserve_winding_directions(tmp_path, mode):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt = pytest.importorskip("PySide6.QtWidgets")
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest
    from power_stage_design.gui import PowerStageMainWindow
    from power_stage_design.schematic import layout
    from power_stage_design.report import write_waveform_csv
    app = qt.QApplication.instance() or qt.QApplication([])
    window = PowerStageMainWindow()
    inputs = (_inputs("T16") if mode == "CCM" else
              replace(_inputs("T16"), iout_a=.005, magnetizing_inductance_h=.1, diode_drop_v=.7))
    window.set_inputs(inputs)
    assert window.result.mode == mode
    window.show()
    app.processEvents()
    original = window.result
    before = write_waveform_csv(original, tmp_path/"before.csv").read_bytes()
    window.save_to(tmp_path/"design.json")
    window.set_inputs(replace(_inputs("T16"), vout_v=4))
    window.open_from(tmp_path/"design.json")
    app.processEvents()
    assert window.result is not original
    assert window.result.design_id == original.design_id
    assert write_waveform_csv(window.result, tmp_path/"after.csv").read_bytes() == before
    for width, height in ((1280, 720), (1560, 1100)):
        window.resize(width, height)
        app.processEvents()
        for key in ("primary1", "primary2", "secondary1", "secondary2"):
            part = next(e for e in layout("T16")[0] if e.key == key)
            bounds = window.schematic.schematic_rect()
            point = QPoint(round(bounds.x()+part.x*bounds.width()/740), round(bounds.y()+part.y*bounds.height()/285))
            QTest.mouseClick(window.schematic, Qt.MouseButton.LeftButton, pos=point)
            assert window.component_combo.currentData() == key
            wave = window.result.waveforms[key]
            assert wave.current_a.reference in window.reference_label.text()
            assert wave.voltage_v.reference in window.reference_label.text()
        directory = os.environ.get("PSD_SCHEMATIC_EVIDENCE_DIR")
        if directory:
            from pathlib import Path
            assert window.grab().save(str(Path(directory)/f"T16-{mode}-actual-window-{width}x{height}.png"))
    report = tmp_path/"report.html"
    window.report_to(report)
    text = report.read_text(encoding="utf-8")
    for key in ("primary1", "primary2", "secondary1", "secondary2"):
        wave = window.result.waveforms[key]
        assert wave.current_a.reference in text and wave.voltage_v.reference in text
    assert "VIN minus P2 (center tap minus dotted lower outer terminal)" in text
    window.close()


def test_dynamic_psfb_final_result_and_consumers_keep_terminal_references(tmp_path):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt = pytest.importorskip("PySide6.QtWidgets")
    from power_stage_design.psfb_dynamics import PSFBOptions
    from power_stage_design.gui import PowerStageMainWindow
    from power_stage_design.report import write_report
    inputs = replace(_inputs("T18"), dead_time_s=200e-9, psfb_options=PSFBOptions(
        300e-12, .15, .8, .05, 100e-12, 1e-9, .02, "Explicit circuit test constants"))
    result = calculate_stage(inputs)
    assert result.mode == "CCM"
    assert result.waveforms["primary"].voltage_v.reference == "P minus B"
    for number in (1, 2):
        winding = result.waveforms[f"secondary{number}"]
        assert winding.current_a.reference.startswith(f"ISO to S{number}")
        assert winding.voltage_v.reference == f"S{number} minus ISO"
        assert winding.current_a.segments is result.waveforms[f"D{number}"].current_a.segments
    assert result.waveforms["primary"].voltage_v.segments is result.waveforms["Lm"].voltage_v.segments
    for node in ("A", "B"):
        assert result.waveforms[f"node_{node}"].voltage_v.reference == f"node {node} to DC return"
    app = qt.QApplication.instance() or qt.QApplication([])
    window = PowerStageMainWindow()
    window._apply_stage_result(result)
    window.show()
    app.processEvents()
    for key in ("primary", "secondary1", "secondary2", "node_A", "node_B"):
        window.component_combo.setCurrentIndex(window.component_combo.findData(key))
        app.processEvents()
        wave = result.waveforms[key]
        assert wave.current_a.reference in window.reference_label.text()
        assert wave.voltage_v.reference in window.reference_label.text()
    report = tmp_path/"dynamic.html"
    write_report(result, report)
    html = report.read_text(encoding="utf-8")
    for key in ("primary", "secondary1", "secondary2", "node_A", "node_B"):
        wave = result.waveforms[key]
        assert wave.current_a.reference in html and wave.voltage_v.reference in html
    window.close()

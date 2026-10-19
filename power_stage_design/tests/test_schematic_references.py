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
    expected = {
        "L1": ("RECT to OUT", "RECT minus OUT"),
        "Lleak": ("A to P", "A minus P"),
        "Lm": ("P to B (magnetizing state)", "P minus B"),
        "primary": ("P to B (into primary dot; excludes shunt capacitance)", "P minus B"),
        "secondary1": ("ISO to S1 (center tap to upper outer terminal)", "S1 minus ISO"),
        "secondary2": ("ISO to S2 (center tap to lower outer terminal)", "S2 minus ISO"),
        "Cprimary": ("P to B", "P minus B"),
        "Crectifier": ("RECT to ISO", "RECT minus ISO"),
        "Cout": ("OUT to ISO (into Cout positive plate)", "OUT minus ISO"),
        "Cin": ("VIN to GND (into Cin positive plate)", "VIN minus GND"),
        "node_A": ("ideal voltage probe draws zero current", "node A to DC return"),
        "node_B": ("ideal voltage probe draws zero current", "node B to DC return"),
    }
    for number in range(1, 5):
        expected[f"Q{number}"] = ("drain to source; channel only, excludes Coss/body diode", "drain to source")
        expected[f"Dbody{number}"] = ("source to drain (anode to cathode)", "cathode to anode")
        expected[f"Coss{number}"] = ("drain to source displacement current", "drain to source")
    for number in (1, 2):
        expected[f"D{number}"] = ("anode to cathode", "cathode to anode")
    assert {key: (wave.current_a.reference, wave.voltage_v.reference)
            for key, wave in result.waveforms.items()} == expected
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
    for key in expected:
        window.component_combo.setCurrentIndex(window.component_combo.findData(key))
        app.processEvents()
        wave = result.waveforms[key]
        assert wave.current_a.reference in window.reference_label.text()
        assert wave.voltage_v.reference in window.reference_label.text()
    report = tmp_path/"dynamic.html"
    write_report(result, report)
    html = report.read_text(encoding="utf-8")
    for key in expected:
        wave = result.waveforms[key]
        assert wave.current_a.reference in html and wave.voltage_v.reference in html
    window.close()


def test_static_qr_output_capacitor_reference_reaches_real_gui_and_html(tmp_path):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt = pytest.importorskip("PySide6.QtWidgets")
    from power_stage_design.gui import PowerStageMainWindow
    from power_stage_design.report import write_report
    result = calculate_stage(_inputs("T11"))
    reference = "OUT to ISO (into Cout positive plate)"
    assert result.waveforms["Cout"].current_a.reference == reference
    np.testing.assert_allclose(_samples(result, "Cout", "current_a"),
                               _samples(result, "D1", "current_a")-result.inputs.iout_a, atol=2e-12)
    app = qt.QApplication.instance() or qt.QApplication([])
    window = PowerStageMainWindow()
    window._apply_stage_result(result)
    window.component_combo.setCurrentIndex(window.component_combo.findData("Cout"))
    app.processEvents()
    assert reference in window.reference_label.text()
    report = tmp_path/"qr.html"
    write_report(result, report)
    assert reference in report.read_text(encoding="utf-8")
    window.close()


# Explicit branch ends for the final metadata-only cleanup. These directions
# follow the existing signed arrays, including SEPIC/Cuk L2 and active-clamp Q2.
_STATIC_BRANCH_REFERENCES = {
    "T01": {"L1": ("SW to OUT", "SW minus OUT")},
    "T02": {"L1": ("VIN to SW", "VIN minus SW")},
    "T03": {"L1": ("SW to GND", "SW minus GND")},
    "T04": {"Lleak": ("SEC to SEC_L", "SEC minus SEC_L")},
    "T06": {"L2": ("GND to B", "GND minus B"),
            "Ctransfer": ("A to B (into Ctransfer positive plate)", "A minus B")},
    "T07": {"L2": ("B to OUT", "B minus OUT"),
            "Ctransfer": ("B to A (into Ctransfer positive plate)", "B minus A")},
    "T08": {"L2": ("OUT to B", "OUT minus B"),
            "Ctransfer": ("A to B (into Ctransfer positive plate)", "A minus B")},
    "T10": {"Dclamp1": ("N to VIN (anode to cathode)", "VIN minus N (cathode to anode)"),
            "Dclamp2": ("GND to P (anode to cathode)", "P minus GND (cathode to anode)")},
    "T12": {"Q2": ("N to CLAMP (source to drain; signed)", "CLAMP minus N (drain to source)"),
            "Cclamp": ("CLAMP to VIN (into Cclamp positive plate)", "CLAMP minus VIN")},
    "T19": {"Cseries": ("TOP to A (into Cseries positive plate)", "TOP minus A")},
}


@pytest.mark.parametrize("topology", [f"T{i:02}" for i in range(1, 20)])
def test_all_real_presets_references_match_device_roles_and_named_branch_ends(topology):
    pytest.importorskip("PySide6.QtWidgets")
    from power_stage_design.gui import PRESETS
    from power_stage_design.schematic import layout
    result = calculate_stage(PRESETS[topology])
    parts = {part.key: part for part in layout(topology)[0] if part.selectable}
    assert set(result.waveforms) == set(parts)
    for key, device in result.waveforms.items():
        assert device.current_a.unit == "A" and device.voltage_v.unit == "V"
        assert device.current_a.reference and device.voltage_v.reference
        if key.startswith("L") and key != "Lm":
            assert not any(word in device.current_a.reference.lower() for word in ("flyback", "rectifier", "anode", "capacitor"))
        if key.startswith("Q"):
            assert "capacitor" not in device.current_a.reference.lower()
        if key.startswith("D"):
            assert "inductor" not in device.current_a.reference.lower()
    for key, expected in _STATIC_BRANCH_REFERENCES.get(topology, {}).items():
        device, part = result.waveforms[key], parts[key]
        assert (device.current_a.reference, device.voltage_v.reference) == expected
        current_ends = (expected[0].split()[0], expected[0].split()[2])
        voltage_ends = (expected[1].split()[0], expected[1].split()[2])
        assert set(current_ends) == set(voltage_ends) == set(part.nets)
        if key.startswith("C"):
            positive_ends = part.nets[::-1] if part.reverse else part.nets
            assert current_ends == voltage_ends == positive_ends
    voltage = lambda key: _samples(result, key, "voltage_v")
    current = lambda key: _samples(result, key, "current_a")
    vin, vo = result.inputs.vin_v, result.inputs.vout_v
    if topology in ("T01", "T02", "T03"):
        np.testing.assert_allclose(voltage("L1"), vin-voltage("Q1")-(vo if topology == "T01" else 0), atol=2e-10)
    if topology == "T04":
        np.testing.assert_allclose(current("Lleak"), current("secondary"), atol=2e-12)
        np.testing.assert_allclose(voltage("Lleak"), voltage("secondary")-voltage("Cout")+voltage("D1"), atol=2e-9)
    if topology in ("T06", "T07", "T08"):
        np.testing.assert_allclose(voltage("L2"), voltage("D1")-vo, atol=2e-10)
        on = np.linspace(.000013, .999987, 503) < result.duty
        np.testing.assert_allclose(current("Ctransfer"), np.where(on, -current("L2"), current("L1")), atol=2e-12)
    if topology == "T10":
        for diode, switch in (("Dclamp1", "Q2"), ("Dclamp2", "Q1")):
            np.testing.assert_array_equal(current(diode), 0)
            np.testing.assert_allclose(voltage(diode), vin-voltage(switch), atol=2e-10)
    if topology == "T12":
        np.testing.assert_allclose(current("Q2"), current("Cclamp"), atol=2e-12)
        # The blocking-voltage model uses the DC clamp voltage, excluding the
        # separately reconstructed first-order ripple, as its assumptions say.
        clamp_mean = result.waveforms["Cclamp"].voltage_v.statistics()["mean"]
        np.testing.assert_allclose(voltage("Q2"), vin+clamp_mean-voltage("Q1"), atol=2e-10)


def test_all_real_preset_references_reach_gui_selectors_and_html(tmp_path):
    from html import escape
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt = pytest.importorskip("PySide6.QtWidgets")
    from power_stage_design.gui import PRESETS, PowerStageMainWindow
    from power_stage_design.report import write_report
    app = qt.QApplication.instance() or qt.QApplication([])
    window = PowerStageMainWindow()
    try:
        for topology, inputs in PRESETS.items():
            result = calculate_stage(inputs)
            window._apply_stage_result(result)
            text = write_report(result, tmp_path/f"{topology}.html").read_text(encoding="utf-8")
            for key, device in result.waveforms.items():
                index = window.component_combo.findData(key)
                assert index >= 0
                window.component_combo.setCurrentIndex(index)
                app.processEvents()
                for reference in (device.current_a.reference, device.voltage_v.reference):
                    assert reference in window.reference_label.text(), (topology, key)
                    assert escape(reference) in text, (topology, key)
    finally:
        window.close()

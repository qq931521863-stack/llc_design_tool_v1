"""AC RMS uses centered full-period energy, not peak-to-peak ripple."""
from __future__ import annotations

import math

import numpy as np
import pytest

from power_stage_design.gui import PRESETS
from power_stage_design.project import read_project, save_project
from power_stage_design.report import write_report
from power_stage_design.topologies import calculate_stage
from power_stage_design.waveforms import Waveform, WaveformSegment, piecewise


@pytest.mark.parametrize("bias", [0.0, -12.0, 1e10])
def test_triangle_ac_rms_preserves_small_ripple_on_large_bias(bias):
    wave = piecewise(1e-5, (.5, .5), ((bias - 1, bias + 1), (bias + 1, bias - 1)), "A", "reference")
    stats = wave.statistics()
    assert stats["ac_rms"] == pytest.approx(1 / math.sqrt(3), rel=1e-14)
    assert stats["peak_to_peak"] == 2
    assert stats["mean"] == bias


@pytest.mark.parametrize("current,duty", [(3.0, .3), (-7.0, .8), (10.0, .001)])
def test_pwm_ac_rms_uses_full_period_weight(current, duty):
    wave = piecewise(2e-5, (duty, 1 - duty), ((current, current), (0, 0)), "A", "reference")
    assert wave.statistics()["ac_rms"] == pytest.approx(abs(current) * math.sqrt(duty * (1 - duty)))


def test_constant_and_harmonic_ac_rms_have_distinct_physical_limits():
    constant = piecewise(1e-5, (1.,), ((-300., -300.),), "V", "reference")
    assert constant.statistics()["ac_rms"] == 0
    harmonic = Waveform(1e-5, "A", "reference", (WaveformSegment(0, 1, (12.,), (3., 4., 2 * math.pi)),))
    assert harmonic.statistics()["ac_rms"] == pytest.approx(5 / math.sqrt(2), rel=1e-14)


@pytest.mark.parametrize("topology", sorted(PRESETS))
def test_every_modeled_branch_ac_rms_matches_independent_centered_quadrature(topology):
    result = calculate_stage(PRESETS[topology])
    nodes, weights = np.polynomial.legendre.leggauss(96)
    u = (nodes + 1) / 2
    weights = weights / 2
    for component, device in result.waveforms.items():
        for prefix, suffix, wave in (("current", "a", device.current_a), ("voltage", "v", device.voltage_v)):
            samples = [(segment.duration_fraction, segment.evaluate(u)) for segment in wave.segments]
            mean = sum(duration * float(weights @ values) for duration, values in samples)
            centered_energy = sum(duration * float(weights @ ((values - mean) ** 2)) for duration, values in samples)
            expected = math.sqrt(centered_energy)
            actual = result.stress[component][f"{prefix}_ac_rms_{suffix}"]
            assert actual == pytest.approx(expected, rel=3e-8, abs=2e-9), (topology, component, prefix)


def test_report_and_reopened_design_recompute_ac_statistics(tmp_path):
    first = calculate_stage(PRESETS["T01"])
    path = save_project(first.inputs, tmp_path / "project.json")
    inputs, _ = read_project(path)
    second = calculate_stage(inputs)
    assert second.stress == first.stress
    output = write_report(second, tmp_path / "report.html").read_text(encoding="utf-8")
    assert "current_ac_rms_a" in output
    assert "voltage_ac_rms_v" in output
    assert first.design_id in output


def test_real_component_table_displays_ac_rms_distinct_from_total_rms():
    from PySide6.QtCore import QCoreApplication, QEvent
    from PySide6.QtWidgets import QApplication

    from power_stage_design.gui import PowerStageMainWindow

    app = QApplication.instance() or QApplication([])
    view = PowerStageMainWindow()
    try:
        view.show()
        app.processEvents()
        view.component_combo.setCurrentIndex(view.component_combo.findData("L1"))
        app.processEvents()
        expected = view.result.stress["L1"]["current_ac_rms_a"]
        assert view.stress_table.columnCount() == 7
        assert view.stress_table.horizontalHeaderItem(6).text() == "交流 RMS"
        assert float(view.stress_table.item(0, 6).text()) == pytest.approx(expected, rel=1e-7)
        assert float(view.stress_table.item(0, 4).text()) > expected
    finally:
        view.close()
        # PlotWidget.close() detaches the plot; retain Qt's owner tree until deletion.
        view.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        app.processEvents()

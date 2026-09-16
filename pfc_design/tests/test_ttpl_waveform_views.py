from __future__ import annotations

from dataclasses import replace
import os

import pytest


def _fast_config():
    from pfc_design.control import PFCControlLabConfig

    return replace(
        PFCControlLabConfig(),
        waveform_line_cycles=3,
        waveform_integration_rate_hz=200e3,
        switching_cycles=2,
        switching_samples_per_cycle=200,
    )


def test_ac_and_switching_views_consume_the_existing_solver_result():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")

    from pfc_design.control import build_pfc_switching_waveforms, simulate_pfc_line_cycle
    from pfc_design.gui.ttpl_waveform_views import (
        TTPLACPerformanceView,
        TTPLSwitchingValidationView,
    )

    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    config = _fast_config()
    line = simulate_pfc_line_cycle(config)
    switching = build_pfc_switching_waveforms(
        config,
        line_cycle=line,
        line_angle_deg=60.0,
        cycles=2,
        samples_per_cycle=200,
    )
    shared_result = (None, line, switching)

    ac = TTPLACPerformanceView(lambda: config)
    ac.set_result(shared_result)
    text = ac.summary.toPlainText()
    assert "TTPL SETTLED AC PERFORMANCE" in text
    assert "Current THD" in text
    assert f"{line.metrics.power_factor:.7f}" in text
    assert len(ac.line_figure.axes) >= 4
    assert len(ac.harmonic_figure.axes) == 2

    sw = TTPLSwitchingValidationView(lambda: config)
    sw.set_result(shared_result)
    assert sw.switching is switching
    assert "Angle=60.00" in sw.summary.text()

    # Rebuild a different local switching workpoint from the same settled line
    # cycle: no new AC solve and no alternate plant model are introduced.
    sw.angle.setValue(90.0)
    sw.cycles.setValue(2)
    sw.samples.setValue(240)
    sw.rebuild()
    assert sw.switching is not None
    assert sw.switching.line_angle_deg == pytest.approx(90.0)
    assert len(sw.switching.time_s) == 480
    assert sw.switching.source_time_s is not None
    assert "Angle=90.00" in sw.summary.text()

    ac.close()
    sw.close()
    app.processEvents()

"""V3 desktop integration keeps a single frozen analysis and simulation."""

import os
from dataclasses import replace
from unittest.mock import patch

import numpy as np
import pytest

from pfc_design.control import PFCControlLabConfig, build_pfc_control_lab_analysis
from pfc_design.engineering import pfc_v3


def _config():
    return replace(
        PFCControlLabConfig(),
        waveform_line_cycles=3,
        waveform_integration_rate_hz=250e3,
    )


def test_v3_builder_runs_each_authority_once_and_reuses_results():
    with (
        patch.object(
            pfc_v3,
            "build_pfc_control_lab_analysis",
            wraps=pfc_v3.build_pfc_control_lab_analysis,
        ) as bode,
        patch.object(
            pfc_v3, "simulate_pfc_line_cycle", wraps=pfc_v3.simulate_pfc_line_cycle
        ) as simulate,
    ):
        result = pfc_v3.build_pfc_engineering_v3(_config())
        assert bode.call_count == simulate.call_count == 1
        reused = pfc_v3.build_pfc_engineering_v3(
            result.smart_control.analysis.config,
            analysis=result.smart_control.analysis,
            waveforms=result.line_cycle.waveforms,
        )
        assert bode.call_count == simulate.call_count == 1
    assert reused.smart_control.analysis is result.smart_control.analysis
    assert reused.line_cycle.waveforms is result.line_cycle.waveforms
    assert reused.as_dict() == result.as_dict()


def test_v3_rejects_mismatched_analysis_config():
    config = _config()
    analysis = build_pfc_control_lab_analysis(config)
    with pytest.raises(ValueError, match="must match"):
        pfc_v3.build_pfc_engineering_v3(
            replace(config, waveform_line_cycles=4), analysis=analysis
        )


def test_desktop_routes_v3_to_existing_stages_and_uses_frozen_config(
    monkeypatch, tmp_path
):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt = pytest.importorskip("PySide6.QtWidgets")
    from pfc_design.control.handoff import assert_handoff_matches_analysis
    from pfc_design.gui.main_window import PFCMainWindow
    from pfc_design.gui.solution_map_install import install_ttpl_solution_map

    app = qt.QApplication.instance() or qt.QApplication([])
    monkeypatch.setattr(PFCMainWindow, "_auto_check_update", lambda self: None)
    window = PFCMainWindow()
    solution_map = install_ttpl_solution_map(window)
    errors = []
    monkeypatch.setattr(window, "_worker_error", errors.append)
    monkeypatch.setattr(
        window, "_run_worker", lambda label, function, callback: callback(function())
    )
    config = _config()
    with patch.object(
        pfc_v3, "simulate_pfc_line_cycle", wraps=pfc_v3.simulate_pfc_line_cycle
    ) as simulate:
        window.run_ttpl_analysis(config)
        assert simulate.call_count == 1
    assert not errors
    analysis, line, _switching = window.result
    v3 = window.engineering_result
    assert analysis is v3.smart_control.analysis
    assert line is v3.line_cycle.waveforms
    bench = window.control_lab_view
    assert bench.exact_hz_view.analysis is analysis
    assert bench.ngspice_closed_loop_view.analysis is analysis
    assert bench.control_lab.result is window.result
    assert bench.ac_performance_view.result is window.result
    assert bench.switching_validation_view.result is window.result
    assert_handoff_matches_analysis(analysis, v3.smart_control.handoff)
    assert np.allclose(
        solution_map.sources["current"].fixed_loop_response
        * analysis.current_loop.responses["controller_ci"],
        analysis.current_loop.responses["open_current"],
    )
    text = bench.v3_diagnostics_view.toPlainText()
    for label in (
        "Convergence:",
        "PF/THD:",
        "Distortion localization",
        "Zero crossing:",
        "Dual-loop separation:",
        "Phase budget:",
    ):
        assert label in text
    assert (
        v3.line_cycle.convergence.status.value
        in bench.ac_performance_view.summary.toPlainText()
    )

    # Pending input edits must not reinterpret an already completed waveform.
    monkeypatch.setattr(
        bench.ac_performance_view,
        "config_provider",
        lambda: pytest.fail("live config used"),
    )
    monkeypatch.setattr(
        bench.switching_validation_view,
        "config_provider",
        lambda: pytest.fail("live config used"),
    )
    bench.ac_performance_view.set_result(window.result)
    bench.switching_validation_view.set_result(window.result)
    bench.switching_validation_view.rebuild()
    assert bench.switching_validation_view.switching is not None

    # A second controller replaces all shared evidence, rather than leaving V3 stale.
    changed = replace(
        config,
        current_controller=replace(
            config.current_controller, kp=config.current_controller.kp * 0.9
        ),
    )
    window.run_ttpl_analysis(changed)
    assert not errors
    assert window.engineering_result is not v3
    assert window.result[0].config is changed
    assert_handoff_matches_analysis(
        window.result[0], window.engineering_result.smart_control.handoff
    )
    window.subtabs.setCurrentIndex(0)
    bench.tabs.setCurrentWidget(bench.control_lab)
    bench.control_lab.tabs.setCurrentWidget(bench.v3_diagnostics_view)
    window.show()
    app.processEvents()
    assert window.grab().save(str(tmp_path / "pfc-v3-desktop.png"))
    window.close()
    app.processEvents()

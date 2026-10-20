"""The actual cursor's listed factors must compose its displayed return ratio."""
import numpy as np
import pytest


@pytest.mark.parametrize("workspace,loop_key,panel_name", [
    ("ttpl", "voltage", "voltage_bode"),
    ("vienna", "voltage", "bv"),
    ("vienna", "balance", "bb"),
])
@pytest.mark.parametrize("frequency", [100., 3005.])
def test_actual_phase_budget_factors_compose_total(
    desktop_qapp, monkeypatch, workspace, loop_key, panel_name, frequency,
):
    from llc_design.control.phase_budget import phase_budget
    from llc_design.gui.widgets.bode_cursor import BodeCursorMeasurement
    if workspace == "ttpl":
        from pfc_design.control import PFCControlLabConfig, build_pfc_control_lab_analysis
        from pfc_design.gui import control_lab_view as module
        analysis = build_pfc_control_lab_analysis(PFCControlLabConfig())
        view = module.PFCControlLabView()
    else:
        from pfc_design.vienna import ViennaControlLabConfig, build_vienna_control_lab_analysis
        from pfc_design.gui import vienna_control_view as module
        analysis = build_vienna_control_lab_analysis(ViennaControlLabConfig())
        view = module.ViennaControlLabView()
    captured = []

    def capture(frequencies, responses, labels, cursor, keys):
        entries = phase_budget(frequencies, responses, labels, cursor, keys)
        captured.append((responses, tuple(keys), entries))
        return entries

    monkeypatch.setattr(module, "phase_budget", capture)
    try:
        view.result = (analysis, None, None)
        getattr(view, panel_name).cursor_changed.emit(BodeCursorMeasurement(frequency, ()))
        assert len(captured) == 1
        responses, keys, entries = captured[0]
        total_key = "open_" + loop_key
        assert keys[-1] == total_key
        assert len(keys) == len(set(keys))
        # Independent complex composition checks every frequency sample, not
        # only the rounded two-decimal strings displayed at the cursor.
        composed = np.prod([responses[key] for key in keys[:-1]], axis=0)
        np.testing.assert_allclose(composed, responses[total_key], rtol=2e-14, atol=0)
        assert abs(sum(e.gain_db for e in entries[:-1]) - entries[-1].gain_db) < 1e-10
        phase_error = sum(e.phase_deg for e in entries[:-1]) - entries[-1].phase_deg
        assert abs((phase_error + 180.) % 360. - 180.) < 1e-10
        if workspace == "ttpl":
            assert "current_closed_for_outer" not in keys
            assert "Bus + closed current loop" in view.phase_budget_label.text()
        else:
            assert loop_key + "_command_hold" in keys
            assert "Command ZOH" in view.budget.text()
    finally:
        view.close()

"""Control annotations reflect actual command rates, not legacy defaults."""
from dataclasses import replace
from types import SimpleNamespace

import numpy as np


def _config():
    from pfc_design.control import PFCControlLabConfig
    base = PFCControlLabConfig()
    return replace(base,
        firmware=replace(base.firmware, current_loop_rate_hz=40000., voltage_loop_rate_hz=8000., amc_rate_hz=20000.),
        current_controller=replace(base.current_controller, sample_time_s=1/40000.),
        voltage_controller=replace(base.voltage_controller, sample_time_s=1/8000.),
        current_sense=replace(base.current_sense, timing=replace(base.current_sense.timing, sample_rate_hz=40000.)),
        vbus_sense=replace(base.vbus_sense, timing=replace(base.vbus_sense.timing, sample_rate_hz=8000.)))


def _check(view):
    subtitles = {block.key: block.subtitle for block in view._diagram_blocks}
    assert subtitles['current_controller'] == 'Ci(z) · 40 kHz'
    assert subtitles['voltage_controller'] == 'Cv(z) · 8 kHz'
    assert subtitles['iref'] == 'Gcmd·|Vac| · 20 kHz'
    assert '40 kHz' in view.current_controller_group.title()
    assert '8 kHz' in view.voltage_controller_group.title()
    assert '40 kHz' in view.autotune_current_button.toolTip()
    assert '50 kHz' not in view.autotune_current_button.toolTip()


def test_restored_rates_and_unrelated_input_edit_keep_actual_model_labels(desktop_qapp, monkeypatch):
    from pfc_design.gui import main_window
    from pfc_design.gui.solution_map_install import install_ttpl_solution_map
    from llc_design.gui.design_session_install import _reflect_ttpl
    monkeypatch.setattr(main_window, 'check_for_updates', lambda *_a, **_k: None)
    host = main_window.PFCMainWindow()
    try:
        install_ttpl_solution_map(host)
        view = host.control_lab_view.control_lab
        config = _config()
        _reflect_ttpl(view, config)
        view._solution_map_restore_config(config)
        _check(view)
        view.diagram.select_block('current_controller')
        view.pout.setValue(3400.)
        _check(view)
        assert view.diagram.selected_key == 'current_controller'
        assert view._config().firmware.current_loop_rate_hz == 40000.
        # Sensor rate has a separate owner from the command/controller update.
        view.current_sense['sample'].setValue(20.)
        _check(view)
        assert view._config().current_sense.timing.sample_rate_hz == 20000.
        view.current_rate_khz.setValue(60.)
        view.voltage_rate_khz.setValue(6.)
        view.amc_rate_khz.setValue(15.)
        edited = view._config()
        assert edited.current_controller.sample_time_s == 1/60000.
        assert edited.voltage_controller.sample_time_s == 1/6000.
        assert edited.firmware.amc_rate_hz == 15000.
        assert '60 kHz' in view.current_controller_group.title()
        from llc_design.control.design_session import decode_model
        saved = decode_model(view._session_capture()['inputs'])
        assert saved == edited
        from pfc_design.control import build_pfc_control_lab_analysis
        result = build_pfc_control_lab_analysis(saved)
        assert result.current_loop.frequencies_hz[-1] < 10000.
        assert result.voltage_loop.frequencies_hz[-1] < 3000.
    finally:
        host.close()


def test_control_strobe_legend_uses_result_snapshot_rates(desktop_qapp):
    from pfc_design.gui.control_lab_view import PFCControlLabView
    from pfc_design.control import build_pfc_control_lab_analysis
    view = PFCControlLabView()
    try:
        analysis = build_pfc_control_lab_analysis(_config())
        keys = ('vac_rms_estimate', 'vloop', 'voltage_error', 'gcmd', 'duty_ff',
                'duty_pi', 'duty_total', 'effective_duty_min', 'vbus_ripple',
                'minimum_pulse_active', 'current_update_strobe', 'amc_update_strobe',
                'voltage_update_strobe')
        fixture = SimpleNamespace(time_s=np.linspace(0, .02, 201),
                                  signals={key: np.zeros(201) for key in keys})
        # Controls still have default 50k/10k; historical waveform labels must
        # come from the result's 40k/8k immutable configuration instead.
        assert view._config().firmware.current_loop_rate_hz == 50000.
        view._plot_ac_control(fixture, analysis)
        assert [line.get_label() for line in view.ac_control_figure.axes[-1].lines] == [
            'Current 40 kHz', 'AMC 20 kHz', 'Voltage 8 kHz']
    finally:
        view.close()


def test_unrepresentable_rate_restore_does_not_partially_change_inputs(desktop_qapp):
    import pytest
    from pfc_design.gui.control_lab_view import PFCControlLabView
    from llc_design.gui.design_session_install import _reflect_ttpl
    view = PFCControlLabView()
    try:
        before = view._config()
        invalid = replace(_config(), firmware=replace(_config().firmware, current_loop_rate_hz=2e6))
        with pytest.raises(ValueError, match="current_rate_khz.*session not restored"):
            _reflect_ttpl(view, invalid)
        assert view._config() == before
    finally:
        view.close()

"""Vienna sample-domain, source ownership and finite-band evidence contracts."""
from dataclasses import replace
from types import SimpleNamespace

import numpy as np
import pytest

from pfc_design.vienna import (
    ViennaControlLabConfig, build_vienna_control_lab_analysis,
    illustrative_vienna_config, validate_vienna_analysis,
)
from pfc_design.control.sensing import sense_frequency_response


def test_each_loop_has_own_strict_sample_domain_and_independent_outer_formula():
    c = ViennaControlLabConfig()
    a = build_vienna_control_lab_analysis(c)
    validate_vienna_analysis(a)
    assert len(a.current_loop.frequencies_hz) > len(a.voltage_loop.frequencies_hz)
    for key in ('current', 'voltage', 'balance'):
        loop = getattr(a, key+'_loop')
        assert np.all(loop.frequencies_hz < loop.nyquist_hz)
        assert all(v.shape == loop.frequencies_hz.shape for v in loop.responses.values())
        assert loop.closed_loop_stability == 'NOT_PROVEN'
    f = a.voltage_loop.frequencies_hz
    s = 2j*np.pi*f
    stage, fw = c.power_stage, c.firmware
    hi = sense_frequency_response(c.phase_current_sense, f).total
    gi = .5*stage.bus_voltage_v/(stage.boost_inductance_h*s+stage.phase_series_resistance_ohm)
    pwm = np.sinc(f/fw.current_loop_rate_hz)*np.exp(-s*(.5/fw.current_loop_rate_hz+fw.current_computation_delay_s+fw.pwm_update_delay_s))
    forward = c.current_controller.transfer_function().frequency_response(f)*pwm*gi
    ti = forward/(1+forward*hi)
    g0 = stage.input_conductance_a_per_v
    bus = 3*stage.phase_rms_v**2*(1-2*stage.phase_series_resistance_ohm*g0-stage.boost_inductance_h*g0*s)/(stage.bus_voltage_v*stage.series_bus_capacitance_f*s)
    cv = c.voltage_controller.transfer_function().frequency_response(f)
    hold = np.sinc(f/fw.voltage_loop_rate_hz)*np.exp(-s*(.5/fw.voltage_loop_rate_hz+fw.voltage_computation_delay_s))
    expected = cv*hold*ti*bus*sense_frequency_response(c.split_bus_sense, f).total
    np.testing.assert_allclose(a.voltage_loop.responses['open_voltage'], expected, rtol=3e-14)


@pytest.mark.parametrize('key', ['current','voltage','balance'])
def test_mismatched_controller_clock_is_rejected(key):
    c=ViennaControlLabConfig()
    with pytest.raises(ValueError, match='sample time must match'):
        replace(c, **{key+'_controller': replace(getattr(c,key+'_controller'), sample_time_s=1/17000)}).validate()


@pytest.mark.parametrize('value',[float('nan'),float('inf'),-float('inf')])
def test_nonfinite_inputs_cannot_reach_numeric_engine(value):
    c=ViennaControlLabConfig()
    for bad in (replace(c,frequency_start_hz=value),
                replace(c,power_stage=replace(c.power_stage,boost_inductance_h=value)),
                replace(c,firmware=replace(c.firmware,current_computation_delay_s=value)),
                replace(c,phase_current_offset_a=(0,value,0)),
                replace(c,split_bus_sense=replace(c.split_bus_sense,adc_vref_v=value))):
        with pytest.raises(ValueError,match='finite'):
            bad.validate()


def test_missing_margin_does_not_imply_stability_and_all_crossings_remain():
    c=ViennaControlLabConfig()
    a=build_vienna_control_lab_analysis(replace(c,voltage_controller=replace(c.voltage_controller,kp=1e-12)))
    assert not a.voltage_loop.likely_stable
    assert a.voltage_loop.margin_screen == 'INCOMPLETE'
    assert a.summary_dict()['voltage']['margins']['gain_margins_db'] == a.voltage_loop.margins.gain_margins_db


def test_legacy_adc_overrange_preserved_and_new_divider_is_opt_in():
    c=ViennaControlLabConfig()
    a=build_vienna_control_lab_analysis(c)
    assert c.split_bus_sense.front_end_gain_v_per_unit == 1600/118600
    assert any('ADC_OVERRANGE Vdc+' in w and '4.748735' in w for w in a.warnings)
    assert any('bias' in w and 'UNKNOWN' in w for w in a.warnings)
    b=build_vienna_control_lab_analysis(illustrative_vienna_config())
    assert not any('ADC_OVERRANGE' in w for w in b.warnings)


def test_slow_sensor_rate_constrains_all_consumers_and_empty_band_rejected():
    c=ViennaControlLabConfig()
    c=replace(c,phase_current_sense=replace(c.phase_current_sense,timing=replace(c.phase_current_sense.timing,sample_rate_hz=2000)))
    a=build_vienna_control_lab_analysis(c)
    assert a.current_loop.nyquist_hz == a.voltage_loop.nyquist_hz == 1000
    with pytest.raises(ValueError,match='two requested'):
        replace(c,frequency_start_hz=1000).validate()


def test_real_qt_controller_edit_invalidates_exports_and_late_results(monkeypatch):
    from PySide6.QtWidgets import QApplication
    app=QApplication.instance() or QApplication([])
    from pfc_design.gui.vienna_control_view import ViennaControlLabView
    v=ViennaControlLabView()
    # Exercise actual Bode consumers, avoid long line-cycle numerics here.
    monkeypatch.setattr(v.performance,'set_vienna',lambda *args:None)
    for name in ('_plot_ac','_plot_bus','_plot_switch','_plot_sector','_plot_harm','_summary'):
        monkeypatch.setattr(v,name,lambda *args:None)
    a=build_vienna_control_lab_analysis(v._config())
    result=(a,SimpleNamespace(),SimpleNamespace())
    assert v.set_result(result)
    assert v.codegen_button.isEnabled()
    np.testing.assert_array_equal(v.bv._frequencies_hz,a.voltage_loop.frequencies_hz)
    v.cc['kp'].setValue(v.cc['kp'].value()*1.1)
    assert v.result is None and not v.codegen_button.isEnabled()
    assert 'STALE' in v.cursor.text()
    assert not v.set_result(result)
    v.set_busy(True); v.set_busy(False)
    assert not v.codegen_button.isEnabled()
    v.current_rate.setValue(40)
    assert v._config().current_controller.sample_time_s == 1/40000
    assert '40 kHz' in v._controller_groups[0].title()
    v.close()

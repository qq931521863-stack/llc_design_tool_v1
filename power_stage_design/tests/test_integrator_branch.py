"""Exact circuit KCL and real desktop integrator-branch preset contract."""
import json
import os
from dataclasses import asdict

import numpy as np
import pytest

from power_stage_design.loop import (
    Compensator,
    compensator_frequency_response,
    integrator_branch_settings,
)


@pytest.mark.parametrize('network', ['type_ii', 'isolated_zener', 'isolated_inner'])
def test_integrator_branch_preserves_actual_other_parameters_and_matches_node_kcl(network):
    original = Compensator(network=network, r1_ohm=17321.125, r_lower_ohm=6234.2,
        c1_f=83.123e-9, r2_ohm=13000, c2_f=9e-9, cff_f=3e-9,
        gbw_hz=27000, open_loop_gain_db=42, ctr=.63, opto_pole_hz=4321,
        rpullup_ohm=9300, rled_ohm=2700)
    selected = integrator_branch_settings(original)
    expected_parameters = asdict(original)
    expected_parameters.update(r2_ohm=0, c2_f=0, cff_f=0)
    assert asdict(selected) == expected_parameters
    frequencies=np.geomspace(.2, 49000, 90)
    actual,_,warnings = compensator_frequency_response(selected, frequencies)
    expected=[]
    for f in frequencies:
        s=2j*np.pi*f
        A=1/(10**(-selected.open_loop_gain_db/20)+s/(2*np.pi*selected.gbw_hz))
        # Independent unknowns Vfb and Vcomp: KCL at FB; Vcomp=-A*Vfb.
        matrix=np.array([[1/selected.r1_ohm+1/selected.r_lower_ohm+s*selected.c1_f,
                          -s*selected.c1_f],[A,1]], dtype=complex)
        _vfb,vcomp=np.linalg.solve(matrix, [1/selected.r1_ohm,0])
        response=-vcomp
        if network.startswith('isolated'):
            response = selected.ctr*selected.rpullup_ohm/selected.rled_ohm/(1+s/(2*np.pi*selected.opto_pole_hz))*(response+(network=='isolated_inner'))
        expected.append(response)
    np.testing.assert_allclose(actual,expected,rtol=2e-14)
    assert any('第34页' in warning and '直接 +1' in warning for warning in warnings)
    assert original.r2_ohm==13000


@pytest.mark.parametrize('network', ['type_iii', 'type_ii_gm', 'unknown'])
def test_non_applicable_network_does_not_get_silently_replaced(network):
    with pytest.raises(ValueError,match='仅适用于'):
        integrator_branch_settings(Compensator(network=network))


def test_guide_equal_rc_tip_is_not_a_pure_integrator():
    c=Compensator(r1_ohm=10000,r2_ohm=10000,c1_f=1e-7,c2_f=1e-7,
                  open_loop_gain_db=260,gbw_hz=1e18)
    f=np.array([.001,1e7])
    actual,_,_=compensator_frequency_response(c,f)
    # Equal capacitors act in parallel at low frequency, while at high frequency
    # the series R suppresses one of them: a 2:1 change, not a pure integrator.
    low_frequency_matched=1/(2j*np.pi*f*c.r1_ohm*(c.c1_f+c.c2_f))
    ratios=abs(actual/low_frequency_matched)
    assert ratios[0] == pytest.approx(1,rel=1e-6)
    assert ratios[-1] == pytest.approx(2,rel=1e-6)


@pytest.mark.parametrize('network',['type_ii','isolated_zener','isolated_inner'])
def test_actual_button_reanalyzes_exact_settings_then_restore_report(tmp_path,network):
    os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
    pytest.importorskip('PySide6.QtWidgets')
    from PySide6.QtCore import QCoreApplication, QEvent, Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication

    from power_stage_design.gui import PowerStageMainWindow
    app=QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    window=PowerStageMainWindow(); window.show(); app.processEvents()
    try:
        window.network_combo.setCurrentIndex(window.network_combo.findData(network))
        window.comp_widgets['gbw_hz'].setValue(.05)
        window.analyze_loop(); old=window.loop_result
        assert old is not None
        QTest.mouseClick(window.integrator_branch_button,Qt.MouseButton.LeftButton)
        assert window.loop_result is not None and window.loop_result is not old
        _,selected=window.loop_parameters()
        assert selected.r2_ohm==selected.c2_f==selected.cff_f==0
        assert selected.gbw_hz==pytest.approx(50000)
        assert '第34页' in window.loop_status.text()
        path=tmp_path/'project.json'; window.save_to(path)
        document=json.loads(path.read_text(encoding='utf-8'))
        assert document['loop_settings']['compensator']['network']==network
        window.comp_widgets['r2_ohm'].setValue(12)
        assert window.loop_result is None
        window.open_from(path)
        assert window.loop_parameters()[1] == selected
        assert window.loop_result is not None
        report=tmp_path/'report.html'; window.report_to(report)
        text=report.read_text(encoding='utf-8')
        assert '第34页' in text and '直接 +1' in text
        assert window.result.design_id not in window.status.text()
        assert window.result.design_id in window.status.toolTip()
        window.network_combo.setCurrentIndex(window.network_combo.findData('type_iii'))
        assert not window.integrator_branch_button.isEnabled()
    finally:
        window.close();window.deleteLater()
        QCoreApplication.sendPostedEvents(None,QEvent.Type.DeferredDelete);app.processEvents()

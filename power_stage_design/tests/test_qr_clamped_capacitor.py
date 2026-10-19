"""Finite Cnode branch only where zero-voltage turn-on has no impulse."""
import xml.etree.ElementTree as ET

import numpy as np
import pytest
from scipy.integrate import quad

from power_stage_design.models import DesignInputs
from power_stage_design.schematic import layout, svg_schematic
from power_stage_design.topologies import calculate_stage


@pytest.mark.parametrize("ratio", [3.2, 4., 8.])
def test_zero_clamp_capacitor_kcl_charge_energy_and_derivative(ratio):
    inputs = DesignInputs("T11", 48., 15., 1., 1e6, 1e-3,
                          turns_ratio_np_ns=ratio, resonant_capacitance_f=300e-12)
    result = calculate_stage(inputs)
    cap = result.waveforms["Cnode"]
    primary = result.waveforms["primary"].current_a
    switch = result.waveforms["Q1"].current_a
    energy = charge = square = 0.
    for index, voltage in enumerate(cap.voltage_v.segments):
        current = cap.current_a.segments[index]
        def at(signal, u, index=index):
            return float(signal.segments[index].evaluate(u))
        duration = voltage.duration_fraction * cap.voltage_v.period_s
        a, b, w = voltage.harmonic
        for u in (.1, .3, .7, .9):
            dv = (np.polynomial.polynomial.polyval(u,
                  np.polynomial.polynomial.polyder(voltage.coefficients))
                  - a*w*np.sin(w*u) + b*w*np.cos(w*u)) / duration
            actual = float(current.evaluate(u))
            assert actual == pytest.approx(inputs.resonant_capacitance_f*dv, abs=1e-10)
            assert at(primary,u) == pytest.approx(at(switch,u)+actual, abs=1e-10)
        charge += duration*quad(lambda u, current=current: float(current.evaluate(u)), 0, 1, epsabs=1e-12)[0]
        energy += duration*quad(lambda u, current=current, voltage=voltage: float(current.evaluate(u)*voltage.evaluate(u)), 0, 1, epsabs=1e-12)[0]
        square += duration*quad(lambda u, current=current: float(current.evaluate(u))**2, 0, 1, epsabs=1e-12)[0]
    assert charge == pytest.approx(0, abs=1e-14)
    assert energy == pytest.approx(0, abs=1e-12)
    assert result.stress["Cnode"]["current_rms_a"] == pytest.approx(np.sqrt(square/cap.current_a.period_s), rel=1e-10)
    parts = {e.key:e for e in layout("T11",device_keys=result.waveforms)[0]}
    assert parts["Cnode"].selectable and parts["Cnode"].nets == ("N","GND")
    assert "Cnode_parameter" not in parts
    svg = ET.fromstring(svg_schematic("T11",device_keys=result.waveforms))
    assert {e.attrib["data-device"] for e in svg.iter() if "data-device" in e.attrib} == set(result.waveforms)


def test_positive_valley_keeps_unbounded_impulse_capacitor_nonselectable():
    result = calculate_stage(DesignInputs("T11",325.,12.,1.,100e3,1e-3,
                            turns_ratio_np_ns=10.,diode_drop_v=.7,resonant_capacitance_f=100e-12))
    assert "Cnode" not in result.waveforms
    cap = next(e for e in layout("T11",device_keys=result.waveforms)[0] if e.key=="Cnode_parameter")
    assert not cap.selectable
    assert any("impulse" in a for a in result.assumptions)

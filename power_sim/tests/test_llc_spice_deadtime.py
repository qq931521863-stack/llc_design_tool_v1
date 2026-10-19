"""Physical regressions for the ideal LLC bridge's reverse-current paths.

The live checks use ngspice currents and an independent stored-energy ledger,
not agreement with another solver that could share the same missing topology.
"""
from dataclasses import replace

import numpy as np
import pytest

from llc_design.core.spec import LLCDesignSpec
from llc_design.core.tank import design_tank
from power_sim.spice import (
    ElementKind,
    LLCSpiceConfig,
    NgSpiceBatchEngine,
    build_ideal_llc_circuit,
    render_netlist,
)


@pytest.mark.parametrize("mode", ["pulse", "external"])
def test_every_primary_switch_has_an_oriented_antiparallel_path(mode):
    spec = LLCDesignSpec()
    config = LLCSpiceConfig(spec.resonant_frequency_hz, spec.vbus_nom_v, gate_drive_mode=mode)
    circuit = build_ideal_llc_circuit(spec, design_tank(spec), config)
    diodes = {item.refdes: item for item in circuit.elements if item.kind == ElementKind.DIODE}
    for name, nodes in {"DAH": ("A", "BUS"), "DAL": ("0", "A"),
                        "DBH": ("B", "BUS"), "DBL": ("0", "B")}.items():
        assert name in diodes, f"missing deadtime path {name}"
        assert diodes[name].terminals == nodes
        assert diodes[name].model == "DBODY"
    assert circuit.metadata["primary_device_model_source"] == "ideal_correlation_approximation_not_vendor_data"
    assert circuit.metadata["body_diode_cjo_f"] == circuit.metadata["body_diode_tt_s"] == 0.0


def test_body_diode_parameters_are_explicit_and_independent_of_rectifier():
    spec = LLCDesignSpec()
    config = LLCSpiceConfig(spec.resonant_frequency_hz, spec.vbus_nom_v,
                            body_diode_is_a=3e-8, body_diode_rs_ohm=0.025)
    circuit = build_ideal_llc_circuit(spec, design_tank(spec), config)
    assert ".model DBODY D(Is=3.000000000000e-08 N=1 Rs=2.500000000000e-02 Cjo=0 Tt=0)" in render_netlist(circuit)
    assert circuit.metadata["body_diode_is_a"] == 3e-8
    assert circuit.metadata["body_diode_rs_ohm"] == 0.025
    assert config.diode_is_a == 1e-9
    assert config.diode_rs_ohm == 0.05


def test_tank_conditioning_is_explicit_and_not_labeled_measured_data():
    spec = LLCDesignSpec()
    config = LLCSpiceConfig(spec.resonant_frequency_hz, spec.vbus_nom_v, tank_node_shunt_ohm=2e8)
    circuit = build_ideal_llc_circuit(spec, design_tank(spec), config)
    shunts = [element for element in circuit.elements if element.refdes.startswith("RNUM_")]
    assert {element.terminals for element in shunts} == {
        (node, "0") for node in ("LR_IN", "N_LR", "CR_IN", "PRI", "PRI_W")
    }
    assert all(element.kind == ElementKind.RESISTOR and float(element.value) == 2e8 for element in shunts)
    assert circuit.metadata["tank_node_shunt_ohm"] == 2e8
    assert circuit.metadata["tank_node_shunt_purpose"] == "numerical_conditioning_not_measured_component_data"


@pytest.mark.parametrize("parameter", ["body_diode_is_a", "body_diode_rs_ohm", "tank_node_shunt_ohm"])
@pytest.mark.parametrize("value", [0.0, -1.0, float("nan"), float("inf")])
def test_diode_and_conditioning_reject_nonphysical_parameters(parameter, value):
    spec = LLCDesignSpec()
    config = replace(LLCSpiceConfig(spec.resonant_frequency_hz, spec.vbus_nom_v), **{parameter: value})
    with pytest.raises(ValueError, match=parameter):
        build_ideal_llc_circuit(spec, design_tank(spec), config)


@pytest.fixture(scope="module", params=["nominal", "long_deadtime_light_load", "uncharged_output"])
def live_llc(request, tmp_path_factory):
    engine = NgSpiceBatchEngine()
    if not engine.available:
        pytest.skip("ngspice executable is not installed")
    spec = LLCDesignSpec()
    config = LLCSpiceConfig(spec.resonant_frequency_hz, spec.vbus_nom_v)
    if request.param == "long_deadtime_light_load":
        spec.primary_deadtime_s = 500e-9
        config = replace(config, switching_frequency_hz=spec.maximum_frequency_hz,
                         bus_voltage_v=spec.vbus_max_v, load_fraction=0.1)
    elif request.param == "uncharged_output":
        config = replace(config, initial_output_v=0.0)
    tank = design_tank(spec)
    circuit = build_ideal_llc_circuit(spec, tank, config)
    # Instrument the network without changing any component or solver option.
    circuit.save_vectors += ["v(lr_in)", "v(n_lr)", "v(cr_in)", "v(pri_w)", "v(sec_w)", "v(co_int)"]
    for item in circuit.elements:
        if item.kind == ElementKind.DIODE:
            circuit.save_vectors.append(f"@{item.refdes.lower()}[id]")
        elif item.kind == ElementKind.SWITCH:
            circuit.save_vectors.append(f"@{item.refdes.lower()}[i]")
    result = engine.run_transient(
        circuit, stop_time_s=40.0 / config.switching_frequency_hz,
        max_step_s=1.0 / config.switching_frequency_hz / 200,
        workdir=tmp_path_factory.mktemp(request.param), use_initial_conditions=True,
    )
    assert result.succeeded, result.stderr + result.stdout
    assert result.data is not None
    assert all(np.all(np.isfinite(values)) for values in result.data.vectors.values())
    return spec, tank, config, circuit, result.data


def test_live_bridge_nodes_stay_bounded_during_positive_deadtime(live_llc):
    _, _, config, _, data = live_llc
    va, vb = data.vector("v(a)"), data.vector("v(b)")
    # 5% bridge allowance is far wider than the generic diode drop, but rejects
    # the previous tens-of-megavolts off-state-resistance conduction artifact.
    assert np.max(np.abs(va - vb)) < 1.05 * config.bus_voltage_v
    for node in (va, vb):
        assert np.min(node) > -0.025 * config.bus_voltage_v
        assert np.max(node) < 1.025 * config.bus_voltage_v
    # Internal ideal-transformer/tank nodes must also remain numerically sane,
    # including the very first gate edge, not just a settled waveform window.
    for name, voltage in data.vectors.items():
        if name.startswith("v("):
            assert np.max(np.abs(voltage)) < 5.0 * config.bus_voltage_v, name


def test_live_deadtime_current_flows_through_both_correct_diode_pairs(live_llc):
    _, _, config, _, data = live_llc
    v = data.vector
    dead = v("time") > 1.0 / config.switching_frequency_hz
    for name in ("ah", "al", "bh", "bl"):
        dead &= v(f"v(g_{name})") < 0.1
    current = v("i(lr)")
    for polarity, conducting in ((1, ("dal", "dbh")), (-1, ("dah", "dbl"))):
        selected = dead & (polarity * current > 0.2)
        assert np.count_nonzero(selected) > 20
        for diode in conducting:
            actual = v(f"i(@{diode}[id])")[selected]
            assert np.all(actual > 0.0)
            # KCL: only microamp bleeder/off-state currents can bypass the pair.
            # Allow sub-percent nonlinear-solver residual at individual samples
            # while checking total deadtime charge more tightly below.
            np.testing.assert_allclose(actual, np.abs(current[selected]), rtol=5e-3, atol=2e-3)
            weight = np.diff(v("time"), prepend=v("time")[0])[selected]
            expected_charge = np.sum(np.abs(current[selected]) * weight)
            assert abs(np.sum(actual * weight) - expected_charge) < 2e-3 * expected_charge
        # The inductor returns energy to the bus through the clamping pair.
        assert np.all(v("i(vbus)")[selected] > 0.0)
        assert np.all((v("v(a)") - v("v(b)"))[selected] * current[selected] < 0.0)


def test_live_independent_current_and_energy_balance(live_llc):
    spec, tank, config, circuit, data = live_llc
    v = data.vector
    time = v("time")

    def integral(values):
        # Explicit trapezoids keep compatibility with the supported NumPy 1.x.
        return float(np.sum(0.5 * (values[1:] + values[:-1]) * np.diff(time)))

    # Independent volt-second check, using the inductor's actual terminals.
    flux_change = integral(v("v(lr_in)") - v("v(n_lr)"))
    measured_change = tank.lr_h * (v("i(lr)")[-1] - v("i(lr)")[0])
    assert abs(flux_change - measured_change) < 0.01 * tank.lr_h * np.max(np.abs(v("i(lr)")))

    def node_voltage(node):
        if node == "0":
            return np.zeros_like(time)
        if node == "BUS":
            return np.full_like(time, config.bus_voltage_v)
        return v(f"v({node.lower()})")

    dissipated_power = np.zeros_like(time)
    for element in circuit.elements:
        if element.kind not in {ElementKind.RESISTOR, ElementKind.DIODE, ElementKind.SWITCH}:
            continue
        voltage = node_voltage(element.terminals[0]) - node_voltage(element.terminals[1])
        if element.kind == ElementKind.RESISTOR:
            power = voltage**2 / float(element.value)
        else:
            suffix = "id" if element.kind == ElementKind.DIODE else "i"
            power = voltage * v(f"i(@{element.refdes.lower()}[{suffix}])")
        assert integral(power) >= -1e-8, element.refdes
        dissipated_power += power

    # The mutual term matters: summing only individual winding energies counts
    # load current as magnetizing energy and gives a false energy ledger.
    lp = tank.lm_h
    ls = lp / spec.turns_ratio**2
    mutual = config.transformer_coupling * np.sqrt(lp * ls)
    ip, isec = v("i(lpri)"), v("i(lsec)")
    stored = (0.5 * tank.lr_h * v("i(lr)")**2
              + 0.5 * lp * ip**2 + 0.5 * ls * isec**2 + mutual * ip * isec
              + 0.5 * tank.cr_f * (v("v(cr_in)") - v("v(pri)"))**2
              + 0.5 * spec.output_capacitance_f * v("v(co_int)")**2)
    delivered = integral(-config.bus_voltage_v * v("i(vbus)"))
    consumed = integral(dissipated_power)
    delta_stored = float(stored[-1] - stored[0])
    assert delivered > 0.0
    scale = max(abs(delivered), consumed, abs(delta_stored))
    assert abs(delivered - consumed - delta_stored) < 0.005 * scale


def test_live_conditioning_resistance_does_not_set_operating_point(tmp_path):
    engine = NgSpiceBatchEngine()
    if not engine.available:
        pytest.skip("ngspice executable is not installed")
    spec = LLCDesignSpec()
    tank = design_tank(spec)
    measurements = []
    # Two decades cover 10x either side of the default. The test checks that
    # numerical regularization is not an artificial load setting tank behavior.
    for resistance in (1e7, 1e8, 1e9):
        config = LLCSpiceConfig(spec.resonant_frequency_hz, spec.vbus_nom_v,
                                tank_node_shunt_ohm=resistance)
        result = engine.run_transient(
            build_ideal_llc_circuit(spec, tank, config),
            stop_time_s=40 / spec.resonant_frequency_hz,
            max_step_s=1 / spec.resonant_frequency_hz / 200,
            workdir=tmp_path / f"shunt_{resistance:g}", use_initial_conditions=True,
        )
        assert result.succeeded, result.stderr + result.stdout
        v = result.data.vector
        assert np.max(np.abs(v("v(pri)"))) < 5 * spec.vbus_nom_v
        sample_time = np.linspace(30 / spec.resonant_frequency_hz, 40 / spec.resonant_frequency_hz, 4001)
        current = np.interp(sample_time, v("time"), v("i(lr)"))
        output = np.interp(sample_time, v("time"), v("v(out)"))
        measurements.append((np.sqrt(np.mean(current**2)), np.mean(output)))
    for measurement in measurements:
        np.testing.assert_allclose(measurement, measurements[1], rtol=1e-3)

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from llc_design.core.spec import LLCDesignSpec, PrimaryTopology
from llc_design.core.tank import design_tank
from power_sim.spice import (
    CircuitIR,
    ElementKind,
    LLCSpiceConfig,
    NgSpiceBatchEngine,
    SpiceElement,
    build_ideal_llc_circuit,
    default_llc_transient_window,
    parse_ascii_raw,
    render_netlist,
    validate_circuit,
)


def test_circuit_ir_validation_and_netlist_are_deterministic():
    circuit = CircuitIR("RC smoke")
    circuit.extend(
        [
            SpiceElement("V1", ElementKind.VOLTAGE_SOURCE, ("IN", "0"), "DC 1"),
            SpiceElement("R1", ElementKind.RESISTOR, ("IN", "OUT"), "1k"),
            SpiceElement("C1", ElementKind.CAPACITOR, ("OUT", "0"), "1u"),
        ]
    )
    circuit.save_vectors += ["time", "v(out)"]
    validation = validate_circuit(circuit)
    assert validation.valid
    text1 = render_netlist(circuit)
    text2 = render_netlist(circuit)
    assert text1 == text2
    assert "V1 IN 0 DC 1" in text1
    assert ".save time v(out)" in text1
    assert text1.rstrip().endswith(".end")


def test_ascii_raw_parser_handles_point_index_format(tmp_path: Path):
    path = tmp_path / "test.raw"
    path.write_text(
        "Title: RC\n"
        "Date: today\n"
        "Plotname: Transient Analysis\n"
        "Flags: real\n"
        "No. Variables: 3\n"
        "No. Points: 2\n"
        "Variables:\n"
        "\t0\ttime\ttime\n"
        "\t1\tv(out)\tvoltage\n"
        "\t2\ti(v1)\tcurrent\n"
        "Values:\n"
        "0\t0.000000e+00\n"
        "\t0.000000e+00\n"
        "\t-1.000000e-03\n"
        "1\t1.000000e-06\n"
        "\t1.000000e-03\n"
        "\t-9.990000e-04\n",
        encoding="utf-8",
    )
    data = parse_ascii_raw(path)
    assert data.plot_name == "Transient Analysis"
    assert data.points == 2
    assert data.variables == ("time", "v(out)", "i(v1)")
    assert np.allclose(data.vector("time"), [0.0, 1e-6])
    assert np.allclose(data.vector("v(out)"), [0.0, 1e-3])


def test_llc_design_generates_full_bridge_ideal_spice_netlist():
    spec = LLCDesignSpec()
    tank = design_tank(spec)
    config = LLCSpiceConfig(
        switching_frequency_hz=spec.resonant_frequency_hz,
        bus_voltage_v=spec.vbus_nom_v,
        load_fraction=1.0,
    )
    circuit = build_ideal_llc_circuit(spec, tank, config)
    assert validate_circuit(circuit).valid
    text = render_netlist(circuit)
    assert "SAH BUS A G_AH 0 SWMOD" in text
    assert "SBL B 0 G_BL 0 SWMOD" in text
    assert "KTX LPRI LSEC" in text
    assert ".model SWMOD SW(" in text
    assert ".model DRECT D(" in text
    assert ".save time v(out)" in text
    assert "i(lr)" in text

    lsec = next(item for item in circuit.elements if item.refdes == "LSEC")
    assert abs(float(lsec.value) - tank.lm_h / spec.turns_ratio**2) / (tank.lm_h / spec.turns_ratio**2) < 1e-12

    stop, step = default_llc_transient_window(config, cycles=100, samples_per_cycle=200)
    assert abs(stop - 100.0 / config.switching_frequency_hz) < 1e-15
    assert abs(step - 1.0 / config.switching_frequency_hz / 200.0) < 1e-18


def test_llc_external_gate_mode_marks_sources_for_shared_ngspice():
    spec = LLCDesignSpec()
    tank = design_tank(spec)
    circuit = build_ideal_llc_circuit(
        spec,
        tank,
        LLCSpiceConfig(
            switching_frequency_hz=spec.resonant_frequency_hz,
            bus_voltage_v=spec.vbus_nom_v,
            gate_drive_mode="external",
        ),
    )
    text = render_netlist(circuit)
    assert "VGAH G_AH 0 DC 0 EXTERNAL" in text
    assert "VGBL G_BL 0 DC 0 EXTERNAL" in text


def test_half_bridge_is_not_silently_approximated():
    spec = LLCDesignSpec(primary_topology=PrimaryTopology.HALF_BRIDGE)
    tank = design_tank(spec)
    with pytest.raises(NotImplementedError):
        build_ideal_llc_circuit(
            spec,
            tank,
            LLCSpiceConfig(
                switching_frequency_hz=spec.resonant_frequency_hz,
                bus_voltage_v=spec.vbus_nom_v,
            ),
        )


def test_live_ngspice_batch_rc_transient_if_installed(tmp_path: Path):
    engine = NgSpiceBatchEngine()
    if not engine.available:
        pytest.skip("ngspice executable is not installed")

    circuit = CircuitIR("RC live smoke")
    circuit.extend(
        [
            SpiceElement("V1", ElementKind.VOLTAGE_SOURCE, ("IN", "0"), "PULSE(0 1 0 1n 1n 10m 20m)"),
            SpiceElement("R1", ElementKind.RESISTOR, ("IN", "OUT"), "1k"),
            SpiceElement("C1", ElementKind.CAPACITOR, ("OUT", "0"), "1u"),
        ]
    )
    circuit.save_vectors += ["time", "v(in)", "v(out)"]
    result = engine.run_transient(circuit, stop_time_s=5e-3, max_step_s=20e-6, workdir=tmp_path)
    assert result.returncode == 0, result.stderr + result.stdout
    assert result.data is not None, result.errors
    assert not result.errors
    assert result.data.points > 10
    assert "v(out)" in result.data.vectors
    assert float(result.data.vector("v(out)")[-1]) > 0.95


def test_live_ngspice_generated_llc_switches_and_carries_tank_current_if_installed(tmp_path: Path):
    """Execute the generated LLC netlist in real ngspice, not only string-test it."""
    engine = NgSpiceBatchEngine()
    if not engine.available:
        pytest.skip("ngspice executable is not installed")

    spec = LLCDesignSpec()
    tank = design_tank(spec)
    config = LLCSpiceConfig(
        switching_frequency_hz=spec.resonant_frequency_hz,
        bus_voltage_v=spec.vbus_nom_v,
        load_fraction=1.0,
        gate_drive_mode="pulse",
        initial_output_v=spec.vout_v,
    )
    circuit = build_ideal_llc_circuit(spec, tank, config)
    stop_time = 80.0 / config.switching_frequency_hz
    max_step = 1.0 / config.switching_frequency_hz / 80.0
    result = engine.run_transient(
        circuit,
        stop_time_s=stop_time,
        max_step_s=max_step,
        workdir=tmp_path / "llc_live",
        use_initial_conditions=True,
    )

    assert result.returncode == 0, result.stderr + result.stdout
    assert result.data is not None, result.errors
    assert not result.errors
    data = result.data
    assert data.points > 500

    t = data.vector("time")
    va = data.vector("v(a)")
    vb = data.vector("v(b)")
    ilr = data.vector("i(lr)")
    vout = data.vector("v(out)")
    assert len(t) == len(va) == len(vb) == len(ilr) == len(vout)
    assert np.all(np.isfinite(ilr))
    assert np.all(np.isfinite(vout))

    bridge = va - vb
    assert float(np.max(bridge)) > 0.8 * spec.vbus_nom_v
    assert float(np.min(bridge)) < -0.8 * spec.vbus_nom_v
    assert float(np.max(np.abs(ilr))) > 0.1

    # With Vout precharged to the design target, the smoke run should not
    # numerically collapse or explode. Tight regulation/correlation belongs to
    # later closed-loop/hardware validation, not this ideal fixed-Fs test.
    tail = vout[int(0.75 * len(vout)) :]
    assert 0.25 * spec.vout_v < float(np.mean(tail)) < 2.0 * spec.vout_v

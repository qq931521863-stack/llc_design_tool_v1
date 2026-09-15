from __future__ import annotations

import numpy as np
import pytest

from power_sim.spice import NgSpiceSharedLibrary, find_ngspice_shared_library


def test_shared_ngspice_dc_operating_point_if_library_installed():
    library = find_ngspice_shared_library()
    if library is None:
        pytest.skip("shared libngspice is not installed")

    session = NgSpiceSharedLibrary(library)
    session.initialize()
    rc = session.load_circuit(
        [
            "* shared ngspice divider smoke",
            "V1 IN 0 DC 1",
            "R1 IN OUT 1k",
            "R2 OUT 0 1k",
            ".op",
            ".end",
        ]
    )
    assert rc == 0
    assert session.command("op") == 0
    out = session.vector("v(out)")
    assert out.size >= 1
    assert np.isfinite(out[-1])
    assert abs(float(out[-1]) - 0.5) < 1e-6


def test_shared_ngspice_background_transient_streams_senddata_if_library_installed():
    library = find_ngspice_shared_library()
    if library is None:
        pytest.skip("shared libngspice is not installed")

    points: list[tuple[int, float, float]] = []

    def on_data(index: int, values: dict[str, complex]) -> None:
        # shared-ngspice SendData exposes saved node voltages by their internal
        # vector name ("out"), while ngGet_Vec_Info accepts expression syntax
        # ("v(out)").  This test intentionally checks the raw callback contract.
        lowered = {name.lower(): value for name, value in values.items()}
        if "time" in lowered and "out" in lowered:
            points.append((index, float(lowered["time"].real), float(lowered["out"].real)))

    session = NgSpiceSharedLibrary(library)
    session.initialize(data_callback=on_data)
    rc = session.load_circuit(
        [
            "* shared ngspice background streaming smoke",
            "V1 IN 0 PULSE(0 1 0 1n 1n 10m 20m)",
            "R1 IN OUT 1k",
            "C1 OUT 0 1u",
            ".save time v(out)",
            ".tran 10u 2m 0 10u",
            ".end",
        ]
    )
    assert rc == 0
    session.run_background(timeout_s=20.0)

    time_vector = session.vector("time")
    out_vector = session.vector("v(out)")
    assert len(time_vector) > 20
    assert len(out_vector) == len(time_vector)

    diagnostic = (
        f"SendData transient callback mismatch; "
        f"data_cb={session.data_callback_count}, init_cb={session.init_callback_count}, "
        f"init_names={session.init_vector_names}, last_names={session.last_data_names}, "
        f"plot={session.current_plot()!r}, all_vecs={session.all_vectors()}, "
        f"direct_time_len={len(time_vector)}, direct_out_len={len(out_vector)}, "
        f"messages_tail={session.messages[-12:]}"
    )
    assert session.data_callback_count > 20, diagnostic
    assert session.init_callback_count >= 1, diagnostic
    assert "out" in {name.lower() for name in session.init_vector_names}, diagnostic
    assert len(points) > 20, diagnostic

    times = np.asarray([item[1] for item in points])
    outputs = np.asarray([item[2] for item in points])
    assert np.all(np.diff(times) >= 0.0)
    assert np.all(np.isfinite(outputs))
    assert outputs[-1] > 0.8

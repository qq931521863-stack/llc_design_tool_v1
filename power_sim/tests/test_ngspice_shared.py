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

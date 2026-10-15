import ctypes
import shutil
import subprocess
from dataclasses import replace

import numpy as np
import pytest

from pfc_design.three_phase.pll import KINDS, GridPLL, PLLConfig, compare_plls
from pfc_design.three_phase.pll_codegen import render_pll_c99


@pytest.fixture(scope="module")
def comparison():
    return compare_plls()


def test_sequence_rejection_and_lock_diagnostics(comparison):
    m = comparison["metrics"]
    assert m["SRF"]["initial_lock_s"] < 0.1
    assert m["DDSRF"]["tail_phase_rms_deg"] < 0.2
    assert m["DDSOGI"]["tail_phase_rms_deg"] < 0.1
    assert (
        m["DDSRF"]["tail_frequency_ripple_pp_hz"]
        < m["SRF"]["tail_frequency_ripple_pp_hz"] / 3
    )
    assert (
        m["DDSOGI"]["tail_frequency_ripple_pp_hz"]
        < m["DDSRF"]["tail_frequency_ripple_pp_hz"]
    )
    assert m["DDSOGI"]["relock_s"] < 0.15


@pytest.mark.parametrize("kind", KINDS)
def test_pll_dropouts_and_nonfinite_inputs_do_not_claim_lock(kind):
    pll = GridPLL(kind=kind)
    for _ in range(500):
        pll.step([0, 0, 0])
    assert not pll.locked
    pll.step([np.nan, 0, 0])
    assert pll.fault and not pll.locked
    assert np.isfinite(pll.theta)
    with pytest.raises(ValueError):
        GridPLL(replace(PLLConfig(), sample_hz=100))


def test_exported_c99_compiles_and_matches_all_algorithms(comparison, tmp_path):
    gcc = shutil.which("gcc")
    if gcc is None:
        pytest.skip("Host C compiler unavailable")
    source = (
        render_pll_c99()
        + r"""
void run(int kind, int n, const float *input, float *output) {
    GRID_PLL p;
    if (!pll_init(&p, &pll_default_config, kind)) { return; }
    for (int j=0; j<n; ++j) {
        float theta = pll_step(&p,input[3*j],input[3*j+1],input[3*j+2]);
        output[3*j] = theta; output[3*j+1] = p.frequency_hz;
        output[3*j+2] = p.locked ? 1.0f : 0.0f;
    }
}
"""
    )
    file = tmp_path / "pll.c"
    file.write_text(source)
    library = tmp_path / "pll.so"
    subprocess.run(
        [
            gcc,
            "-std=c99",
            "-Wall",
            "-Wextra",
            "-Werror",
            "-shared",
            "-fPIC",
            str(file),
            "-lm",
            "-o",
            str(library),
        ],
        check=True,
        capture_output=True,
    )
    run = ctypes.CDLL(str(library)).run
    pointer = ctypes.POINTER(ctypes.c_float)
    run.argtypes = [ctypes.c_int, ctypes.c_int, pointer, pointer]
    inputs = np.ascontiguousarray(comparison["voltage_abc"], dtype=np.float32)
    for index, kind in enumerate(KINDS):
        output = np.zeros((len(inputs), 3), dtype=np.float32)
        run(
            index,
            len(inputs),
            inputs.ctypes.data_as(pointer),
            output.ctypes.data_as(pointer),
        )
        py = comparison["results"][kind]
        phase_error = np.angle(np.exp(1j * (output[:, 0] - py["theta_rad"])))
        assert np.max(abs(phase_error)) < 0.0005
        assert np.max(abs(output[:, 1] - py["frequency_hz"])) < 0.02
        assert np.mean(output[:, 2] == py["locked"]) > 0.995

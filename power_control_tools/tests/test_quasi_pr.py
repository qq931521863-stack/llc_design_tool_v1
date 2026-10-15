"""Formula, parallel structure, numeric precision and compiled C contract."""
import ctypes
import json
import shutil
import subprocess

import numpy as np
import pytest
from scipy import signal

from power_control_tools.quasi_pr import (
    HARMONICS,
    QuasiPRConfig,
    QuasiPRRuntime,
    ResonantBranch,
    branch_coefficients,
    frequency_response,
    resonant_coefficients,
    validate_float32,
)
from power_control_tools.quasi_pr_codegen import coefficient_json, render_quasi_pr_c99


@pytest.mark.parametrize('h', HARMONICS)
@pytest.mark.parametrize('kp', [0.0, 0.3])
def test_user_formula_matches_independent_scipy_bilinear(h, kp):
    kr, wc, wr, fs = 20, 2*np.pi, h*2*np.pi*50, 40000
    b, a = resonant_coefficients(kr, wc, wr, 1/fs, kp)
    expected_b, expected_a = signal.bilinear([kp, 2*wc*(kp+kr), kp*wr**2],
                                           [1, 2*wc, wr**2], fs=fs)
    np.testing.assert_allclose(b, expected_b, atol=1e-15)
    np.testing.assert_allclose(a, expected_a, atol=1e-15)


@pytest.mark.parametrize('mask', range(64))
def test_selection_dc_and_nyquist_limits(mask):
    cfg = QuasiPRConfig(branches=tuple(ResonantBranch(h) for i, h in enumerate(HARMONICS) if mask & (1 << i)))
    total, branches = frequency_response(cfg, np.array([0.0]))
    assert len(branches) == mask.bit_count()
    assert total[0] == pytest.approx(cfg.kp, abs=1e-10)
    data = json.loads(coefficient_json(cfg))
    assert len(data['coefficients_float32']) == mask.bit_count()


def test_resonance_gain_and_tustin_warping():
    cfg = QuasiPRConfig(sample_rate_hz=4000, branches=(ResonantBranch(11, 17, 1),))
    fc = 11*cfg.grid_frequency_hz
    digital_center = cfg.sample_rate_hz/np.pi*np.arctan(np.pi*fc/cfg.sample_rate_hz)
    analog, _ = frequency_response(cfg, [fc], analog=True)
    digital, _ = frequency_response(cfg, [digital_center])
    assert analog[0] == pytest.approx(17.1)
    assert digital[0] == pytest.approx(analog[0], abs=1e-10)
    assert digital_center < fc


def test_parallel_runtime_matches_independent_filter_sum():
    cfg = QuasiPRConfig(branches=tuple(ResonantBranch(h, 1+i, 3) for i, h in enumerate(HARMONICS)),
                        output_min=-1e6, output_max=1e6)
    x = np.random.default_rng(41).normal(size=20000)
    expected = cfg.kp*x
    for b, a in branch_coefficients(cfg):
        expected += signal.lfilter(b, a, x)
    runtime = QuasiPRRuntime(cfg, float32=False)
    actual = np.array([runtime.step(v) for v in x])
    np.testing.assert_allclose(actual, expected, atol=1e-10)


@pytest.mark.parametrize('config', [
    QuasiPRConfig(sample_rate_hz=0), QuasiPRConfig(kp=float('nan')),
    QuasiPRConfig(output_min=2), QuasiPRConfig(branches=(ResonantBranch(2),)),
    QuasiPRConfig(branches=(ResonantBranch(1), ResonantBranch(1))),
    QuasiPRConfig(branches=(ResonantBranch(1, bandwidth_hz=0),)),
    QuasiPRConfig(sample_rate_hz=1000, branches=(ResonantBranch(11),)),
])
def test_invalid_configs(config):
    with pytest.raises(ValueError):
        render_quasi_pr_c99(config)


def test_float32_degenerate_denominator_rejected():
    cfg = QuasiPRConfig(sample_rate_hz=1e6, branches=(ResonantBranch(1, bandwidth_hz=0.0001),))
    with pytest.raises(ValueError, match='float32'):
        validate_float32(cfg)


@pytest.mark.parametrize('freeze', [False, True])
def test_saturation_reset_fault_and_recovery(freeze):
    cfg = QuasiPRConfig(kp=10, freeze_on_saturation=freeze)
    rt = QuasiPRRuntime(cfg)
    assert rt.step(2) == 1
    assert rt.saturated
    assert np.any(rt.states) != freeze
    assert rt.step(float('nan')) == 0
    assert rt.fault and not np.any(rt.states)
    assert rt.step(0) == 0
    assert not rt.fault


def _compile(tmp_path, config):
    compiler = shutil.which('gcc')
    if compiler is None:
        pytest.skip('GCC not installed')
    (tmp_path/'test.h').write_text(render_quasi_pr_c99(config, 'TEST'))
    (tmp_path/'test.c').write_text('''#include "test.h"
void run(const float *x, float *y, unsigned int n) {
    TEST_PR_CTRL_YS_DATA_DEF p;
    unsigned int i;
    TEST_Init(&p);
    for(i=0;i<n;i++) { y[i]=TEST_pr_ctrl_ys_func(x[i],&p); }
}
int coeff(float kr,float wc,float wr,float kp,float ts,float *out) {
    TEST_IIR_2ORD_DATA_DEF p={0};
    if(!TEST_updata_pr_ctrl_coeff(&p,kr,wc,wr,kp,ts)) { return 0; }
    out[0]=p.coeff_B0; out[1]=p.coeff_B1; out[2]=p.coeff_B2;
    out[3]=p.coeff_A1; out[4]=p.coeff_A2;
    return 1;
}
float section(float x) {
    TEST_IIR_2ORD_DATA_DEF p={0};
    p.coeff_B0=1.0F;
    return TEST_iir_2order(x,&p);
}
int contracts(void) {
    TEST_PR_CTRL_YS_DATA_DEF p={0};
    if(TEST_pr_ctrl_ys_func(1.0F,&p)!=0.0F || p.fault!=1U) {return 0;}
    TEST_Init(&p);
    (void)TEST_pr_ctrl_ys_func(1.0F,&p);
    TEST_Reset(&p);
    return p.section[0].filter_W1==0.0F && p.pr_ctrl_out==0.0F;
}
''')
    subprocess.run([compiler, '-std=c99', '-O2', '-Wall', '-Wextra', '-Werror', '-pedantic',
                    '-ffp-contract=off', '-shared', '-fPIC', str(tmp_path/'test.c'),
                    '-lm', '-o', str(tmp_path/'test.so')], check=True, capture_output=True)
    return ctypes.CDLL(str(tmp_path/'test.so'))


@pytest.mark.parametrize('freeze,branches', [
    (False, tuple(ResonantBranch(h) for h in HARMONICS)),
    (True, (ResonantBranch(1), ResonantBranch(7))), (False, ()),
])
def test_compiled_c_matches_float32_sample_by_sample(tmp_path, freeze, branches):
    cfg = QuasiPRConfig(branches=branches, freeze_on_saturation=freeze)
    lib = _compile(tmp_path, cfg)
    x = np.random.default_rng(913).normal(0, 0.5, 16000).astype(np.float32)
    x[4000:8000] = 20
    x[9000] = np.nan
    x[10000] = np.inf
    x[-1000:] = 0
    out = np.zeros_like(x)
    ptr = ctypes.POINTER(ctypes.c_float)
    lib.run.argtypes = [ptr, ptr, ctypes.c_uint]
    lib.run(x.ctypes.data_as(ptr), out.ctypes.data_as(ptr), len(x))
    runtime = QuasiPRRuntime(cfg)
    expected = np.array([runtime.step(v) for v in x], dtype=np.float32)
    np.testing.assert_array_equal(out, expected)
    assert lib.contracts() == 1
    lib.section.argtypes = [ctypes.c_float]
    lib.section.restype = ctypes.c_float
    assert lib.section(0.25) == 0.25
    lib.coeff.argtypes = [ctypes.c_float]*5 + [ptr]
    for h in HARMONICS:
        args = np.array([10, 2*np.pi, 2*np.pi*h*50, .2, 1/40000], dtype=np.float32)
        values = np.zeros(5, dtype=np.float32)
        assert lib.coeff(*map(float, args), values.ctypes.data_as(ptr)) == 1
        b, a = resonant_coefficients(float(args[0]), float(args[1]), float(args[2]), float(args[4]), float(args[3]))
        np.testing.assert_array_equal(values, np.r_[b, a[1:]].astype(np.float32))
    assert lib.coeff(1, 0, 300, 0, .001, values.ctypes.data_as(ptr)) == 0


def test_narrowband_float32_long_run_agrees_with_bode():
    cfg = QuasiPRConfig(branches=tuple(ResonantBranch(h) for h in HARMONICS),
                        output_min=-1e6, output_max=1e6)
    t = np.arange(120000)/cfg.sample_rate_hz
    x = sum(np.sin(2*np.pi*h*cfg.grid_frequency_hz*t) for h in HARMONICS)*.01
    rt = QuasiPRRuntime(cfg)
    y = np.array([rt.step(v) for v in x])
    tail = slice(-40000, None)
    for h in HARMONICS:
        f = h*cfg.grid_frequency_hz
        measured = np.sum(y[tail]*np.exp(-2j*np.pi*f*t[tail])) / np.sum(x[tail]*np.exp(-2j*np.pi*f*t[tail]))
        expected, _ = frequency_response(cfg, [f], quantized=True)
        assert measured == pytest.approx(expected[0], rel=.015, abs=.01)

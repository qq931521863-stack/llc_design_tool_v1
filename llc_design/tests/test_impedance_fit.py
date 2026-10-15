import numpy as np
import pytest

from llc_design.magnetics.dowell import _fft_rms_phasors_local
from llc_design.magnetics.impedance_fit import (
    ChokeModel,
    fit_choke_impedance,
    load_impedance_csv,
)
from llc_design.magnetics.litz import round_wire_skin_factor
from llc_design.magnetics.spectrum import rms_phasors


@pytest.mark.parametrize('n', [63, 64, 2048])
def test_full_spectrum_parseval_including_nyquist(n):
    x = np.random.default_rng(22).normal(size=n) + 3
    spectrum = rms_phasors(x, n//2)
    assert sum(abs(v)**2 for v in spectrum) == pytest.approx(np.mean(x*x), rel=1e-13)
    np.testing.assert_array_equal(_fft_rms_phasors_local(x, n//2), spectrum)


def test_nyquist_bin_not_doubled():
    x = (-1.0)**np.arange(64)
    assert abs(rms_phasors(x, 32)[32]) == pytest.approx(1)


def test_scaled_bessel_avoids_high_frequency_overflow():
    from scipy.special import iv

    from llc_design.magnetics.litz import skin_depth_m
    diameter, f = .001, 1e5
    z = (1+1j)*diameter/2/skin_depth_m(f, 100)
    reference = ((z/2)*iv(0, z)/iv(1, z)).real
    assert round_wire_skin_factor(diameter, f, 100) == pytest.approx(reference)
    extreme = round_wire_skin_factor(.01, 1e9, 100)
    assert np.isfinite(extreme) and extreme > 100
    round_wire_skin_factor.cache_clear()
    first = round_wire_skin_factor(diameter, f, 100)
    assert round_wire_skin_factor(diameter, f, 100) == first
    assert round_wire_skin_factor.cache_info().hits == 1


def test_complex_fit_recovers_known_model_and_passivity():
    model = ChokeModel(.02, 120e-6, 25000, 45e-12)
    f = np.geomspace(20, 1e7, 201)
    result = fit_choke_impedance(f, model.impedance(f))
    np.testing.assert_allclose(result.model.values(), model.values(), rtol=1e-6)
    assert result.relative_rms_error < 1e-8
    assert np.all(result.model.impedance(f).real > 0)
    assert '.SUBCKT' in result.model.spice()


def test_noisy_fit_and_band_limit_warning():
    model = ChokeModel(.02, 120e-6, 25000, 45e-12)
    f = np.geomspace(20, 1e7, 201)
    rng = np.random.default_rng(21)
    z = model.impedance(f)*(1 + .005*rng.normal(size=len(f)) + .005j*rng.normal(size=len(f)))
    result = fit_choke_impedance(f, z)
    assert result.relative_rms_error < .02
    np.testing.assert_allclose(result.model.values(), model.values(), rtol=.05)
    low = np.geomspace(20, 2000, 80)
    limited = fit_choke_impedance(low, model.impedance(low))
    assert any('Resonance outside' in warning for warning in limited.warnings)


@pytest.mark.parametrize('f,z', [([1]*8, [1j]*8), ([1,2], [1j,2j]),
                                (np.arange(1,9), [np.nan]*8),
                                (np.arange(1,9), [-1j]*8)])
def test_bad_data_rejected(f,z):
    with pytest.raises(ValueError):
        fit_choke_impedance(f,z)


def test_explicit_csv_units(tmp_path):
    p = tmp_path/'input.csv'
    p.write_text('frequency_hz,real_ohm,imag_ohm\n10,1,2\n20,3,4\n')
    f,z = load_impedance_csv(p)
    np.testing.assert_array_equal(f, [10,20])
    np.testing.assert_array_equal(z, [1+2j,3+4j])
    p.write_text('frequency_khz,R,L\n1,2,3\n')
    with pytest.raises(ValueError, match='headers'):
        load_impedance_csv(p)

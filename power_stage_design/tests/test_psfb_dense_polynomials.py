"""Independent real-function oracles for public PSFB dense-output reconstruction."""
from types import SimpleNamespace

import numpy as np
import pytest
from scipy.integrate import quad, solve_ivp

from power_stage_design.models import ModelDomainError
from power_stage_design.psfb_dynamics import _dense_blocks, _fit_state, _roots_inside
from power_stage_design.waveforms import WaveformSegment

SCALES = np.array([400., 400., 2., 2., 400., 48., 5.])
LEFT, RIGHT = .137, .641


def polynomial(coefficients, u):
    """Evaluate the analytic oracle without the production polynomial helpers."""
    return sum(coefficient * np.asarray(u)**order
               for order, coefficient in enumerate(coefficients))


def from_roots(roots):
    coefficients = [1.]
    for root in roots:
        product = [0.] * (len(coefficients) + 1)
        for order, coefficient in enumerate(coefficients):
            product[order] -= root * coefficient
            product[order + 1] += coefficient
        coefficients = product
    return tuple(coefficients)


MIXED_ROOTS = ((), (), (.25,), (.2, .8), (.2, .5, .8),
               (.1, .3, .6, .9), (.1, .25, .5, .7, .9))
MIXED = ((0.,), (-2.,), *(from_roots(roots) for roots in MIXED_ROOTS[2:]))
CASES = [
    pytest.param(((0.,),) * 7, ((),) * 7, 3, id="zero"),
    pytest.param(((0.,), (-2.,), (1.,), (.5,), (3.,), (-.25,), (4.,)),
                 ((),) * 7, 3, id="constant"),
    pytest.param(((0.,), (1.,), (-.25, 1.), (.75, -1.), (0., 2.),
                  (1., -1.), (-2., 1.)), ((), (), (.25,), (.75,), (), (), ()),
                 3, id="linear-with-boundary-roots"),
    pytest.param(MIXED, MIXED_ROOTS, 5, id="mixed-seven-state-orders"),
]


def solution_for(coefficients):
    def solution(t):
        u = (np.asarray(t) - LEFT) / (RIGHT - LEFT)
        return np.asarray([polynomial(row, u) for row in coefficients])
    return solution


@pytest.mark.parametrize("coefficients, roots, degree", CASES)
@pytest.mark.parametrize("single_step", [True, False], ids=["single-step", "coalesced"])
def test_real_functions_retain_rectangular_coefficients_and_waveform_math(
        coefficients, roots, degree, single_step):
    matrix = _fit_state(solution_for(coefficients), LEFT, RIGHT, SCALES,
                        single_step=single_step)
    assert matrix.shape == (7, max(degree, 3 if single_step else 5) + 1)
    assert np.isfinite(matrix).all()
    # Different grid, including both endpoints, from either fit/check grid.
    u = np.linspace(0., 1., 41)
    for row, scale, expected, expected_roots in zip(matrix, SCALES, coefficients, roots):
        segment = WaveformSegment(LEFT, RIGHT, tuple(row))
        np.testing.assert_allclose(segment.evaluate(u) / scale, polynomial(expected, u),
                                   rtol=0., atol=2e-11)
        for square in (False, True):
            power = 2 if square else 1
            integral = quad(lambda x, expected=expected, power=power:
                            float(polynomial(expected, x))**power, 0., 1.,
                            epsabs=1e-13, epsrel=1e-13)[0]
            assert segment.integral(square=square) / scale**power == pytest.approx(
                (RIGHT - LEFT) * integral, rel=0., abs=2e-11)
        assert sorted(_roots_inside(row)) == pytest.approx(expected_roots, abs=2e-9)


@pytest.mark.parametrize("degree", [7, 12])
def test_higher_order_real_functions_preserve_all_coefficients(degree):
    high_order = (-.3, *(0.,) * (degree - 1), 1.)
    coefficients = (*MIXED[:6], high_order)
    matrix = _fit_state(solution_for(coefficients), LEFT, RIGHT, SCALES)
    assert matrix.shape == (7, degree + 1)
    segment = WaveformSegment(0., 1., tuple(matrix[-1] / SCALES[-1]))
    u = np.linspace(0., 1., 79)
    np.testing.assert_allclose(segment.evaluate(u), u**degree - .3, rtol=0., atol=2e-11)
    assert segment.integral() == pytest.approx(1. / (degree + 1) - .3, abs=2e-11)
    assert _roots_inside(matrix[-1]) == pytest.approx([.3**(1. / degree)], abs=2e-9)


def test_public_lsoda_dense_output_with_zero_and_mixed_order_states():
    coefficients = ((0.,), (1.,), (-.25, 1.), (.16, -1., 1.),
                    (-.08, .66, -1.5, 1.), (-.5,), (2.,))
    analytic = solution_for(coefficients)

    def rhs(t, _y):
        u = (t - LEFT) / (RIGHT - LEFT)
        return np.asarray([sum(order * c * u**(order - 1)
                               for order, c in enumerate(row) if order)
                           for row in coefficients]) / (RIGHT - LEFT)

    solved = solve_ivp(rhs, (LEFT, RIGHT), analytic(LEFT), method="LSODA",
                       rtol=1e-11, atol=1e-13, max_step=(RIGHT - LEFT) / 10,
                       dense_output=True)
    assert solved.success
    # Consume only the public callable and solver times, as production does.
    blocks = list(_dense_blocks(SimpleNamespace(t=solved.t, sol=solved.sol), SCALES))
    assert blocks[0][0] == LEFT and blocks[-1][1] == RIGHT
    integrals = np.zeros(7)
    for index, (left, right, matrix) in enumerate(blocks):
        assert matrix.ndim == 2 and matrix.shape[0] == 7
        if index:
            assert blocks[index - 1][1] == left
        u = np.linspace(0., 1., 23)
        expected = analytic(left + (right - left) * u)
        for state, (row, scale) in enumerate(zip(matrix, SCALES)):
            segment = WaveformSegment(left, right, tuple(row))
            np.testing.assert_allclose(segment.evaluate(u) / scale, expected[state],
                                       rtol=0., atol=5e-10)
            integrals[state] += segment.integral() / scale
    for integral, row in zip(integrals, coefficients):
        expected = (RIGHT - LEFT) * quad(lambda u, row=row: float(polynomial(row, u)), 0., 1.)[0]
        assert integral == pytest.approx(expected, abs=5e-10)


def test_unresolved_real_function_still_fails_independent_withheld_check():
    def solution(t):
        u = (np.asarray(t) - LEFT) / (RIGHT - LEFT)
        return np.asarray([np.sin(100. * u), *(np.zeros_like(u),) * 6])

    assert _fit_state(solution, LEFT, RIGHT, SCALES, single_step=False) is None
    with pytest.raises(ModelDomainError, match="independent sample check"):
        _fit_state(solution, LEFT, RIGHT, SCALES)


def test_solver_boundaries_still_reject_an_aliased_zero_fit():
    # This real, piecewise-linear pulse lies between both fit and withheld
    # probes. Its solver-boundary peak must force subdivision of the zero fit.
    center, half_width = .25, 1e-4

    def solution(t):
        u = (np.asarray(t) - LEFT) / (RIGHT - LEFT)
        pulse = np.maximum(1. - np.abs(u - center) / half_width, 0.)
        return np.asarray([pulse, *(np.zeros_like(u),) * 6])

    aliased = _fit_state(solution, LEFT, RIGHT, SCALES, single_step=False)
    np.testing.assert_array_equal(aliased, np.zeros((7, 6)))
    times = LEFT + (RIGHT - LEFT) * np.array(
        [0., center - half_width, center, center + half_width, 1.])
    blocks = list(_dense_blocks(SimpleNamespace(t=times, sol=solution), SCALES))
    assert len(blocks) == 4
    integral, crossings = 0., []
    for left, right, matrix in blocks:
        segment = WaveformSegment(left, right, tuple(matrix[0] / SCALES[0]))
        np.testing.assert_allclose(segment.evaluate([0., 1.]),
                                   solution(np.array([left, right]))[0], rtol=0., atol=2e-11)
        integral += segment.integral()
        threshold = matrix[0] / SCALES[0]
        threshold[0] -= .5
        crossings.extend(left + (right - left) * root for root in _roots_inside(threshold))
    assert integral == pytest.approx((RIGHT - LEFT) * half_width, abs=2e-13)
    expected = LEFT + (RIGHT - LEFT) * np.array([center - half_width / 2, center + half_width / 2])
    np.testing.assert_allclose(crossings, expected, rtol=0., atol=2e-13)

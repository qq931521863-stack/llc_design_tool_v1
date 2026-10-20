"""Continuous polynomials must retain the state-space response, not just DC."""
from dataclasses import replace
import numpy as np
import pytest
from llc_design.control.linearize import LinearizedPlant, SISOTransferFunction


def _plant(gain=1.0):
    a = -np.diag([1e3, 2e4, 3e5, 4e6])
    return LinearizedPlant(
        a=a, b=np.ones((4, 1)), c=gain * np.ones((1, 4)), d=np.zeros((1, 1)),
        state_names=tuple(f"x{i}" for i in range(4)), input_names=("u",),
        input_units=("V",), output_names=("output_voltage_v",), output_units=("V",),
        steady_states=np.zeros(4), steady_inputs=np.ones(1), steady_outputs=np.ones(1),
        poles=np.diag(a).astype(complex),
    )


def _direct(plant, frequencies):
    return np.asarray([(plant.c @ np.linalg.solve(2j*np.pi*f*np.eye(len(plant.a))-plant.a,
                                               plant.b) + plant.d)[0, 0]
                       for f in frequencies])


@pytest.mark.parametrize("gain", [1e-12, 1.0, 1e12])
def test_polynomial_response_matches_independent_state_equation_over_band(gain):
    plant = _plant(gain)
    frequencies = np.geomspace(.1, 1e7, 500)
    transfer = plant.siso()
    # Old mixed-unit 1e-11*max(num) trimming removed this real s^3 term.
    assert len(transfer.numerator) == 4
    np.testing.assert_allclose(transfer.frequency_response(frequencies),
                               _direct(plant, frequencies), rtol=2e-8, atol=1e-30)


def test_time_unit_change_does_not_remove_dynamics():
    plant = _plant()
    f = np.geomspace(.1, 1e7, 300)
    # Express time in milliseconds. Transfer response at the same physical
    # frequency is unchanged, despite a different coefficient scale hierarchy.
    milliseconds = replace(plant, a=plant.a/1000, b=plant.b/1000)
    np.testing.assert_allclose(plant.siso().frequency_response(f),
                               milliseconds.siso().frequency_response(f/1000), rtol=1e-8)


def test_explicit_legacy_trim_parameter_warns_without_changing_model():
    plant = _plant()
    with pytest.warns(DeprecationWarning, match="different units"):
        transfer = plant.siso(relative_trim=1e-11)
    np.testing.assert_array_equal(transfer.numerator, plant.siso().numerator)


def test_frequency_response_does_not_normalize_away_tiny_leading_coefficient():
    tf = SISOTransferFunction(np.array([1e-18, 1.]), np.array([1., 1.]),
                              "u", "V", "y", "V", np.array([-1.+0j]),
                              np.array([-1e18+0j]), 1.)
    f = np.array([1e15, 1e18])
    expected = (1e-18*(2j*np.pi*f)+1)/(2j*np.pi*f+1)
    np.testing.assert_allclose(tf.frequency_response(f), expected, rtol=1e-14, atol=0)
    assert tf.frequency_response(f)[-1] == pytest.approx(tf.evaluate(2j*np.pi*f[-1]), rel=1e-14)


def test_actual_llc_frequency_and_disturbance_models_match_state_equations():
    from llc_design.control.analysis import build_small_signal_analysis
    from llc_design.core.spec import LLCDesignSpec
    small = build_small_signal_analysis(LLCDesignSpec())
    plant = small.continuous_plant
    f = np.geomspace(.1, .245/small.sample_time_s, 350)
    for name in plant.input_names:
        index = plant.input_index(name)
        siso_state = replace(plant, b=plant.b[:, [index]], c=plant.c[[0], :],
                             d=plant.d[[0], :][:, [index]])
        expected = _direct(siso_state, f)
        actual = plant.siso(input_name=name).frequency_response(f)
        np.testing.assert_allclose(actual, expected, rtol=1e-7, atol=1e-12)


@pytest.mark.parametrize("gain", [1e-12, 1e-6, 1., 1e6, 1e12])
def test_input_unit_scaling_preserves_state_space_response(gain):
    original = _plant()
    plant = replace(original, b=original.b*gain, d=original.d*gain)
    f = np.geomspace(.1, 1e7, 400)
    np.testing.assert_allclose(plant.siso().frequency_response(f), _direct(plant, f),
                               rtol=2e-8, atol=1e-30)


def test_zero_dynamic_path_and_nonzero_feedthrough_are_preserved():
    original = _plant()
    plant = replace(original, b=np.zeros_like(original.b), d=np.array([[1e-12]]))
    f = np.geomspace(.1, 1e7, 50)
    np.testing.assert_allclose(plant.siso().frequency_response(f), np.full(50, 1e-12),
                               rtol=1e-12, atol=0)

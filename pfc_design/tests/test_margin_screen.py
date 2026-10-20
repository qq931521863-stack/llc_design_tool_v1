"""Finite-band margins and a PI integrator are not a closed-loop proof."""
from dataclasses import replace

import numpy as np
import pytest

from llc_design.control.digital_loop import DigitalTransferFunction, PIControllerConfig, PIFControllerConfig, StabilityMargins
from pfc_design.control.analysis import _margin_screen, build_pfc_control_lab_analysis
from pfc_design.control.config import PFCControlLabConfig


def positive_margins():
    return StabilityMargins((100.,), (45.,), (1000.,), (10.,), 100., 45., 1000., 10., .001)


@pytest.mark.parametrize("config", [PIControllerConfig(), PIFControllerConfig()])
def test_single_typed_pi_integrator_does_not_fail_margin_screen(config):
    tf = config.transfer_function()
    assert not tf.stable
    assert _margin_screen(positive_margins(), tf, intentional_pi_integrator=True) == "POSITIVE_MARGINS"
    assert _margin_screen(positive_margins(), tf) == "REVIEW_CONTROLLER_POLES"


@pytest.mark.parametrize("poles", [[-1.], [1.001], [1., 1.], [1j, -1j], [1., -1.], [1., 1.001]])
def test_other_marginal_and_unstable_controller_poles_require_review(poles):
    tf = DigitalTransferFunction([1.], np.poly(poles).real, 20e-6)
    assert _margin_screen(positive_margins(), tf, intentional_pi_integrator=True) == "REVIEW_CONTROLLER_POLES"


@pytest.mark.parametrize("change,expected", [
    ({"critical_gain_crossover_hz": None}, "INCOMPLETE"),
    ({"phase_margin_deg": None}, "INCOMPLETE"),
    ({"gain_margin_db": None}, "INCOMPLETE"),
    ({"gain_margin_db": float("nan")}, "INCOMPLETE"),
    ({"phase_margin_deg": 0.}, "NONPOSITIVE_MARGIN"),
    ({"gain_margin_db": -1.}, "NONPOSITIVE_MARGIN"),
    ({"gain_margins_db": (10., -2.)}, "NONPOSITIVE_MARGIN"),
    ({"phase_margins_deg": (45., -3.)}, "NONPOSITIVE_MARGIN"),
])
def test_missing_or_negative_margin_never_becomes_positive(change, expected):
    tf = PIControllerConfig().transfer_function()
    assert _margin_screen(replace(positive_margins(), **change), tf, intentional_pi_integrator=True) == expected


def test_summary_never_exports_a_closed_loop_stable_boolean():
    result = build_pfc_control_lab_analysis(PFCControlLabConfig())
    summary = result.summary_dict()
    for key in ("current_loop", "voltage_loop"):
        assert "stable" not in summary[key]
        assert summary[key]["closed_loop_stability"] == "NOT_PROVEN"
        assert summary[key]["margin_screen"] in {"POSITIVE_MARGINS", "NONPOSITIVE_MARGIN", "INCOMPLETE", "REVIEW_CONTROLLER_POLES"}

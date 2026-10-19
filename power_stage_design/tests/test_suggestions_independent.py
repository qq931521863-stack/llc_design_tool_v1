"""Re-solve proposed components with independent periodic circuit constraints."""
from __future__ import annotations

from dataclasses import replace

import numpy as np
import pytest
from scipy.optimize import root

from power_stage_design.models import DesignInputs
from power_stage_design.suggestions import SuggestionTargets, suggest_parameters
from power_stage_design.tests.test_extended_independent import _TwoInductorCircuit
from power_stage_design.tests.test_independent_validation import (
    GOLDEN_CIRCUITS,
    _solve_circuit,
)


@pytest.mark.parametrize("circuit", GOLDEN_CIRCUITS, ids=lambda value: value.topology)
@pytest.mark.parametrize("ripple", [.6, 20.])
def test_proposed_l_meets_target_in_independent_state_charge_root(circuit, ripple):
    topology = {"buck": "T01", "boost": "T02", "flyback": "T09"}[circuit.topology]
    inputs = DesignInputs(topology, circuit.vin, circuit.vout, circuit.iout,
                          circuit.fsw, circuit.inductance, capacitance_f=1e-3,
                          turns_ratio_np_ns=circuit.turns_np_ns, diode_drop_v=.4)
    proposal = suggest_parameters(inputs, SuggestionTargets(ripple, .8))
    # This observer solves state closure and output charge with SciPy root and
    # event-split quadrature. It never calls calculate_stage or the sizing helper.
    reference = _solve_circuit(replace(circuit, inductance=proposal.inputs.inductance_h,
                                       diode_drop=.4))
    physical_ripple = reference.peak_current - reference.initial_current
    assert physical_ripple == pytest.approx(ripple, rel=1e-9)
    assert reference.on == pytest.approx(proposal.result.duty, rel=1e-9)
    assert reference.on <= .8
    assert reference.rms("switch") == pytest.approx(proposal.result.stress["Q1"]["current_rms_a"], rel=1e-9)


@pytest.mark.parametrize("topology", ["T06", "T07", "T08"])
@pytest.mark.parametrize("targets", [(.7, 1.1), (14., 22.)])
def test_proposed_dual_l_meets_targets_in_independent_nodal_periodic_root(topology, targets):
    inputs = DesignInputs(topology, 48, 5, 2, 100e3, 470e-6,
                          secondary_inductance_h=100e-6, transfer_capacitance_f=1e-3,
                          capacitance_f=1e-3, diode_drop_v=.3)
    one, two = targets
    proposal = suggest_parameters(inputs, SuggestionTargets(one, .8, secondary_ripple_pp_a=two))
    p = proposal.inputs
    voltage = {"T06": p.vin_v, "T07": p.vout_v, "T08": p.vin_v + p.vout_v}[topology]

    def continuous_residual(state):
        duty, i1, i2, vc = state
        return _TwoInductorCircuit(p, duty, 1 - duty, i1, i2, vc).constraints()

    solved = root(continuous_residual, [.25, p.iout_a, p.iout_a, voltage], tol=1e-11)
    # A converged physical residual can accompany HYBR's "no progress" flag
    # at roundoff. Require finite state and actual constraints, never a flag alone.
    assert np.isfinite(solved.x).all()
    assert np.linalg.norm(continuous_residual(solved.x), ord=np.inf) < 1e-9
    duty, i1, i2, vc = solved.x
    if i1 + i2 >= 0:
        assert 0 < duty < 1
        reference = _TwoInductorCircuit(p, duty, 1 - duty, i1, i2, vc)
    else:
        def discontinuous_residual(state):
            duty, off, i1, i2, vc = state
            return [*_TwoInductorCircuit(p, duty, off, i1, i2, vc).constraints(), i1 + i2]

        solved = root(discontinuous_residual, [duty / 2, (1 - duty) / 2, 0., 0., voltage], tol=1e-11)
        assert np.isfinite(solved.x).all()
        assert np.linalg.norm(discontinuous_residual(solved.x), ord=np.inf) < 1e-9
        duty, off, i1, i2, vc = solved.x
        assert duty > 0 and off > 0 and duty + off < 1
        reference = _TwoInductorCircuit(p, duty, off, i1, i2, vc)
    samples = np.asarray([
        reference.currents(float(phase))
        for start, end in reference.intervals if end > start
        for phase in np.linspace(start, end, 25)
    ])
    assert np.ptp(samples[:, 0]) == pytest.approx(one, rel=1e-9)
    assert np.ptp(samples[:, 1]) == pytest.approx(two, rel=1e-9)
    assert reference.on == pytest.approx(proposal.result.duty, rel=1e-9)

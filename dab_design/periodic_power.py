"""Smooth, event-exact periodic series-RLC power mapping for TPS control.

Unlike rasterized bridge waveforms, analytic segment propagation retains small
phase perturbations. This is a periodic steady-state map, not Cp transient states
in the outer-loop simulation.
"""

import math

import numpy as np

from .optimization import bridge_segments


def periodic_lc_power(stage, vb_v, phases):
    """Solve x=[IL, VCp] periodic boundary; integrate power via Cp*dVCp."""
    stage.validate()
    events, va, vb = bridge_segments(stage, vb_v, phases)
    l, cp, r = (
        stage.inductance_h,
        stage.primary_capacitance_f,
        stage.series_resistance_ohm,
    )
    alpha = r / (2 * l)
    omega0_sq = 1 / (l * cp)
    discriminant = alpha * alpha - omega0_sq
    m = np.array([[-alpha, -1 / l], [1 / cp, alpha]])
    identity = np.eye(2)
    transitions = []
    phi_total = identity.copy()
    offset = np.zeros(2)
    for width, drive in zip(np.diff(events), va - vb):
        dt = width / stage.switching_hz
        if abs(discriminant) <= 1e-12 * max(alpha * alpha, omega0_sq):
            decay = math.exp(-alpha * dt)
            c, q = decay, decay * dt
        elif discriminant < 0:
            wd = math.sqrt(-discriminant)
            decay = math.exp(-alpha * dt)
            c, q = decay * math.cos(wd * dt), decay * math.sin(wd * dt) / wd
        else:
            wd = math.sqrt(discriminant)
            # Two decaying exponentials avoid exp(-alpha*t)*cosh(wd*t).
            ep, en = math.exp((-alpha + wd) * dt), math.exp((-alpha - wd) * dt)
            c, q = (ep + en) / 2, (ep - en) / (2 * wd)
        phi = c * identity + q * m
        forcing = (identity - phi) @ np.array([0.0, drive])
        transitions.append((phi, forcing))
        phi_total = phi @ phi_total
        offset = phi @ offset + forcing
    boundary = identity - phi_total
    singular = np.linalg.svd(boundary, compute_uv=False)
    if singular[-1] <= 1e-12 * max(1.0, singular[0]):
        raise ValueError(
            "Undamped/ill-conditioned LC periodic boundary; no reliable power map"
        )
    state = np.linalg.solve(boundary, offset)
    transferred_energy = 0.0
    for (phi, forcing), voltage in zip(transitions, vb):
        next_state = phi @ state + forcing
        # Integral(IL dt) = Cp*(VCp_end-VCp_start), with no sample-grid aliasing.
        transferred_energy += voltage * cp * (next_state[1] - state[1])
        state = next_state
    power = float(transferred_energy * stage.switching_hz)
    if not math.isfinite(power):
        raise ValueError("Nonfinite periodic RLC power")
    return power

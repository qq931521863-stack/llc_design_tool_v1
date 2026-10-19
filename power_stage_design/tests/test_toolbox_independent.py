"""Independent M2 toolbox evidence, not TI executable or hardware validation.

Oracles below use charge/time integration, component-level modified nodal
analysis (MNA), load-line roots, exhaustive tolerance corners, and unit-defining
identities. No toolbox implementation helper or existing test oracle is used.
TI-only approximations (X01/X02/X04/X12 damping) are explicitly delimited.
Sources: SLVUBB4B (2023) §§2–11,13 and task brief §12 G04. In particular,
X04's printed RMS is source evidence, not independently validated physics.
"""
from __future__ import annotations

import itertools
import math
from dataclasses import replace

import numpy as np
import pytest
from scipy.integrate import quad, solve_ivp, trapezoid
from scipy.optimize import brentq

from power_stage_design.models import ModelDomainError
from power_stage_design.toolbox import (
    CapacitorBranch,
    FETOperatingPoint,
    FETParameters,
    FilterInputs,
    acdc_bulk_capacitor,
    analog_scaling,
    capacitor_current_sharing,
    compare_fets,
    convert_units,
    design_divider,
    differential_filter,
    digital_scaling,
    divider_output,
    fet_losses,
    hold_up_capacitance,
    load_step_capacitance,
    preferred_resistance,
    rc_snubber,
    rcd_snubber,
)


def _mna(edges, fixed, injected=None):
    """Solve each individual branch Vp−Vn=Z I plus node KCL.

    Edges contain (positive_node, negative_node, impedance). Branch-current
    unknowns allow ideal wires/zero resistance without admittance infinities.
    This intentionally does not reduce any series/parallel impedance network.
    """
    injected = {} if injected is None else injected
    unknown = sorted({node for p, n, _ in edges for node in (p, n)} - fixed.keys())
    index = {node: k for k, node in enumerate(unknown)}
    nv = len(unknown)
    matrix = np.zeros((nv + len(edges), nv + len(edges)), dtype=complex)
    rhs = np.zeros(len(matrix), dtype=complex)
    for node, current in injected.items():
        rhs[index[node]] = current
    for k, (p, n, impedance) in enumerate(edges):
        row = nv + k
        matrix[row, nv + k] = -impedance
        for node, sign in ((p, 1), (n, -1)):
            if node in index:
                matrix[index[node], nv + k] += sign
                matrix[row, index[node]] += sign
            else:
                rhs[row] -= sign * fixed[node]
    solution = np.linalg.solve(matrix, rhs)
    voltages = {**fixed, **{node: solution[k] for node, k in index.items()}}
    return voltages, solution[nv:]


def _capacitor_edges(start, end, prefix, capacitance, resistance, inductance, s):
    return [(start, prefix + 'r', resistance),
            (prefix + 'r', prefix + 'l', s * inductance),
            (prefix + 'l', end, 1 / (s * capacitance))]


def _feedback_voltage(vref, top, ground_resistors, adjust=None):
    """Numerically solve feedback-node current balance for the source voltage."""
    def residual(vout):
        currents = [(vref - vout) / top]
        currents.extend(vref / resistance for resistance in ground_resistors)
        if adjust is not None:
            vadj, resistance = adjust
            currents.append((vref - vadj) / resistance)
        return math.fsum(currents)
    return brentq(residual, -1e7, 1e7, xtol=1e-11)


def _gate_charge_time(charge, current):
    """Charge-domain ODE with event, under TI's piecewise constant Ig model."""
    def reached(_t, state):
        return state[0] - charge
    reached.terminal = True
    reached.direction = 1
    trajectory = solve_ivp(lambda _t, _state: [current], (0, 1e-6), [0.0],
                           events=reached, rtol=1e-11, atol=1e-20, max_step=1e-9)
    assert len(trajectory.t_events[0]) == 1
    return trajectory.t_events[0][0]


def _fet_fixture():
    frequency = 125e3
    period = 1 / frequency
    on_time = .3 * period
    rms = math.sqrt(quad(lambda t: (3 + 6 * t / on_time) ** 2, 0, on_time)[0] / period)
    operating = FETOperatingPoint(rms, 3, 9, 120, frequency,
                                 dead_time_on_s=30e-9, dead_time_off_s=80e-9)
    device = FETParameters(rds_on_ohm=.045, gate_voltage_v=12, gate_charge_c=36e-9,
        coss_f=240e-12, qgs_c=10e-9, qg_threshold_c=4e-9, qgd_c=12e-9,
        gate_resistance_ohm=4, miller_voltage_v=6, threshold_voltage_v=2,
        body_diode_drop_v=.95, source='Independent synthetic fixture, no device claim')
    return operating, device


@pytest.mark.parametrize('role', ['main', 'sr'])
def test_x01_piecewise_charge_ode_and_full_period_energy(role):
    op, device = _fet_fixture()
    op = replace(op, role=role)
    output = fet_losses(op, device).outputs
    # TI uses midpoint gate voltage 4 V in the current-rise/fall segment.
    tr = _gate_charge_time(6e-9, 2.0) + _gate_charge_time(12e-9, 1.5)
    tf = _gate_charge_time(12e-9, 1.5) + _gate_charge_time(6e-9, 1.0)
    overlap = quad(lambda t: 120 * (1 - t / tr) * 3, 0, tr)[0]
    overlap += quad(lambda t: 120 * t / tf * 9, 0, tf)[0]
    period = 1 / op.frequency_hz
    conduction = quad(lambda t: .045 * (3 + 6 * t / (.3 * period)) ** 2,
                      0, .3 * period)[0]
    coss_energy = quad(lambda voltage: 240e-12 * voltage, 0, 120)[0]
    body = quad(lambda _t: .95 * 3, 0, 30e-9)[0] + quad(lambda _t: .95 * 9, 0, 80e-9)[0]
    assert output['rise_time_s'] == pytest.approx(tr, rel=1e-9)
    assert output['fall_time_s'] == pytest.approx(tf, rel=1e-9)
    assert output['conduction_w'] == pytest.approx(conduction / period, rel=1e-12)
    assert output['output_capacitance_w'] == pytest.approx(coss_energy / period, rel=1e-12)
    expected = (conduction + coss_energy + (overlap if role == 'main' else body)) / period
    assert output['baseline_total_w'] == pytest.approx(expected, rel=1e-9)
    driver_energy = quad(lambda _charge: 12, 0, 36e-9)[0]
    assert output['gate_driver_w'] == pytest.approx(driver_energy / period)
    assert output['full_device_loss_w'] is None


@pytest.mark.parametrize('missing', ['rds_on_ohm', 'coss_f', 'qgs_c'])
def test_x01_different_known_terms_never_create_complete_comparison(missing):
    op, device = _fet_fixture()
    partial = replace(device, **{missing: None})
    result = compare_fets(op, [device, partial])
    complete, incomplete = result.outputs['devices']
    assert complete['baseline_total_w'] is not None
    assert incomplete['baseline_total_w'] is None
    assert incomplete['full_device_loss_w'] is None
    assert len(incomplete['missing_terms']) == 1
    assert result.status == incomplete['status'] == 'PARTIAL'
    terms = ['conduction_w', 'output_capacitance_w', 'switching_overlap_w', 'body_diode_w']
    assert incomplete['known_subtotal_w'] == pytest.approx(
        sum(incomplete[term] for term in terms if incomplete[term] is not None))


def test_x01_nonlinear_energy_input_replaces_constant_coss_and_driver_is_optional():
    op, device = _fet_fixture()
    nonlinear_energy = quad(lambda v: 500e-12 * v / (1 + v / 30), 0, 120)[0]
    result = fet_losses(op, replace(device, coss_f=None, eoss_j=nonlinear_energy, gate_charge_c=None))
    assert result.outputs['output_capacitance_w'] == pytest.approx(nonlinear_energy * op.frequency_hz)
    assert result.outputs['gate_driver_w'] is None
    assert result.outputs['baseline_total_w'] is not None
    with pytest.raises(ModelDomainError, match='Coss or Eoss'):
        fet_losses(op, replace(device, eoss_j=nonlinear_energy))


@pytest.mark.parametrize('control', ['CMC', 'VMC'])
@pytest.mark.parametrize('phase_margin', [25, 60, 91, 150])
def test_x02_closed_loop_unit_phasor_geometry_and_transient_impedance(control, phase_margin):
    di, dv, fc, esr, inductance = 7.3, .42, 12.7e3, .008, 8.2e-6
    result = load_step_capacitance(control, di, dv, fc, phase_margin, esr,
                                  inductance if control == 'VMC' else None)
    c = result.outputs['minimum_capacitance_f']
    # At crossover the return ratio has unit magnitude and angle -180+PM.
    return_ratio = np.exp(1j * math.radians(-180 + phase_margin))
    sensitivity_distance = abs(1 + return_ratio)
    assert result.outputs['phase_factor'] == pytest.approx(sensitivity_distance, rel=1e-13)
    capacitive_reactance = abs(1 / (2j * np.pi * fc * c))
    capacitor_admittance = 1 / (esr + capacitive_reactance / sensitivity_distance)
    inductor_correction = 0 if control == 'CMC' else abs(1 / (2j * np.pi * fc * inductance))
    assert capacitor_admittance + inductor_correction == pytest.approx(di / dv, rel=1e-12)


def test_x02_no_solution_on_both_sides_of_vmc_and_esr_boundaries():
    fc, di, dv = 10e3, 5, .25
    inductance_boundary = dv / (2 * np.pi * fc * di)
    for inductance in [inductance_boundary * .1, inductance_boundary * (1 - 1e-8)]:
        with pytest.raises(ModelDomainError):
            load_step_capacitance('VMC', di, dv, fc, 60, 0, inductance)
    for esr in [dv / di, 1.1 * dv / di]:
        with pytest.raises(ModelDomainError):
            load_step_capacitance('CMC', di, dv, fc, 60, esr)
    valid = load_step_capacitance('VMC', di, dv, fc, 60, 0, inductance_boundary * (1 + 1e-8))
    assert valid.outputs['minimum_capacitance_f'] > 0
    cmc = load_step_capacitance('CMC', di, dv, fc, 60, .01).outputs['minimum_capacitance_f']
    vmc = load_step_capacitance('VMC', di, dv, fc, 60, .01, 1e5).outputs['minimum_capacitance_f']
    assert vmc == pytest.approx(cmc, rel=1e-10)


@pytest.mark.parametrize('spectral', [[(73000, 3.4)], [(73000, 3.4), (219000, 1.2), (511000, .7)]])
def test_x03_component_mna_and_time_domain_harmonic_rms(spectral):
    branches = [CapacitorBranch(47e-6, .11, 8e-9), CapacitorBranch(2.2e-6, .012, 2e-9),
                CapacitorBranch(100e-9, .025, .7e-9)]
    total = math.sqrt(sum(amplitude ** 2 for _, amplitude in spectral))
    result = capacitor_current_sharing(branches, total, 73000,
                                      harmonics=spectral if len(spectral) > 1 else None)
    # Integer harmonics make integration over one fundamental period exact in
    # the Fourier sense. Phasors are derived from individual MNA components.
    t = np.linspace(0, 1 / 73000, 8193)
    samples = np.zeros((len(branches), len(t)))
    for (frequency, current), reported in zip(spectral, result.outputs['spectrum']):
        s = 2j * np.pi * frequency
        edges = []
        for k, branch in enumerate(branches):
            edges.extend(_capacitor_edges('bus', 'ground', str(k), branch.capacitance_f,
                                          branch.esr_ohm, branch.esl_h, s))
        _, edge_currents = _mna(edges, {'ground': 0}, {'bus': current})
        phasors = edge_currents[::3]
        assert sum(phasors) == pytest.approx(current, rel=1e-11, abs=1e-12)
        for k, phasor in enumerate(phasors):
            actual = reported['branches'][k]
            assert complex(actual['current_real_a'], actual['current_imag_a']) == pytest.approx(phasor, rel=1e-10)
            samples[k] += np.sqrt(2) * np.real(phasor * np.exp(s * t))
    for k, branch in enumerate(branches):
        ms = trapezoid(samples[k] ** 2, t) / (t[-1] - t[0])
        assert result.outputs['branches'][k]['current_rms_a'] == pytest.approx(math.sqrt(ms), rel=1e-10)
        assert result.outputs['branches'][k]['esr_loss_w'] == pytest.approx(branch.esr_ohm * ms, rel=1e-10)
    assert not math.isclose(sum(b['current_rms_a'] for b in result.outputs['branches']), total, rel_tol=1e-3)


def test_x03_g04_identical_branches_and_spectral_domain():
    branch = CapacitorBranch(10e-6, .02, 3e-9)
    result = capacitor_current_sharing([branch, branch], 6.4, 100e3)
    assert [x['current_rms_a'] for x in result.outputs['branches']] == pytest.approx([3.2, 3.2])
    with pytest.raises(ModelDomainError):
        capacitor_current_sharing([branch, branch], 2, 100e3, harmonics=[(100e3, 1), (100e3, math.sqrt(3))])
    with pytest.raises(ModelDomainError):
        capacitor_current_sharing([branch, branch], 3, 100e3, harmonics=[(100e3, 1), (200e3, 1)])


@pytest.mark.parametrize('power,vmin,ripple,frequency', [(100, 85, .2, 47), (450, 250, .08, 60), (20, 18, .35, 50)])
def test_x04_rectified_sine_crossing_energy_and_charge_balance(power, vmin, ripple, frequency):
    result = acdc_bulk_capacitor(power, vmin, ripple, frequency)
    output = result.outputs
    c, vp = output['capacitance_f'], output['maximum_voltage_v']
    period, w = 1 / (2 * frequency), 2 * np.pi * frequency
    crossing = brentq(lambda t: -vp * np.cos(w * t) - vmin, period / 2, period,
                      xtol=1e-15)
    assert output['discharge_time_s'] == pytest.approx(crossing, rel=1e-11)
    assert output['charge_time_s'] + crossing == pytest.approx(period)
    ode = solve_ivp(lambda _t, voltage: [-power / (c * voltage[0])], (0, crossing), [vp],
                    dense_output=True, rtol=1e-11, atol=1e-11)
    assert ode.y[0, -1] == pytest.approx(vmin, rel=1e-9)
    discharge_charge = quad(lambda t: -power / ode.sol(t)[0], 0, crossing, epsabs=1e-12)[0]
    recharge_charge = quad(lambda t: c * vp * w * np.sin(w * t), crossing, period, epsabs=1e-12)[0]
    assert discharge_charge + recharge_charge == pytest.approx(0, abs=1e-10)
    # RMS for that charge-balanced peak-start model is an independent result.
    # It is NOT eq13, and is not claimed to reproduce TI executable behaviour.
    square = quad(lambda t: (power / ode.sol(t)[0]) ** 2, 0, crossing)[0]
    square += quad(lambda t: (c * vp * w * np.sin(w * t)) ** 2, crossing, period)[0]
    periodic_rms = math.sqrt(square / period)
    assert abs(output['bulk_rms_current_eq13_a'] / periodic_rms - 1) > .02
    assert result.status == 'MODELED_WITH_SOURCE_DISCREPANCY'
    assert any('RMS' in warning and 'approximation' in warning for warning in result.warnings)
    if (power, vmin, ripple, frequency) == (100, 85, .2, 47):
        assert periodic_rms == pytest.approx(2.304974583529848, rel=1e-9)
        assert output['bulk_rms_current_eq13_a'] == pytest.approx(2.622526265460834, rel=1e-12)


def test_g04_hold_up_energy_in_charge_domain_and_reference_divider():
    result = hold_up_capacitance(400, 300, 100, .02)
    c = result.outputs['capacitance_f']
    # Integrate V dq using capacitor charge as the independent state.
    charge_start, charge_end = 400 * c, 300 * c
    energy = quad(lambda charge: charge / c, charge_end, charge_start, epsabs=1e-12)[0]
    assert energy == pytest.approx(2, rel=1e-12)
    assert c * 1e6 == pytest.approx(57.1428571429, rel=1e-12)
    divider = design_divider(2.5, 12, 10e3)
    assert divider.outputs['high_ohm'] == pytest.approx(38e3)
    assert _feedback_voltage(2.5, 38e3, [10e3]) == pytest.approx(12)


@pytest.mark.parametrize('clamp_ratio', [1.2, 1.5, 3])
def test_x05_leakage_current_ode_and_clamp_energy(clamp_ratio):
    leakage, peak, frequency, reflected = 1.3e-6, 7.5, 92e3, 95
    result = rcd_snubber(leakage, peak, frequency, reflected, clamp_ratio, .025).outputs
    clamp = result['clamp_voltage_above_input_v']
    def discharged(_t, state):
        return state[0]
    discharged.terminal = True
    discharged.direction = -1
    ode = solve_ivp(lambda _t, _i: [(reflected - clamp) / leakage], (0, 1 / frequency), [peak],
                    events=discharged, dense_output=True, rtol=1e-11, atol=1e-11)
    end = ode.t_events[0][0]
    dissipated_energy = quad(lambda t: clamp * ode.sol(t)[0], 0, end, epsabs=1e-14)[0]
    assert result['leakage_discharge_time_s'] == pytest.approx(end, rel=1e-11)
    assert result['snubber_power_w'] == pytest.approx(dissipated_energy * frequency, rel=1e-11)
    assert clamp ** 2 / result['resistance_ohm'] == pytest.approx(result['snubber_power_w'])
    # Additional energy beyond initial leakage comes from the reflected source.
    source_energy = quad(lambda t: reflected * ode.sol(t)[0], 0, end)[0]
    magnetic_energy = quad(lambda current: leakage * current, 0, peak)[0]
    assert dissipated_energy == pytest.approx(source_energy + magnetic_energy, rel=1e-11)
    # Small-ripple RCD approximation: R discharge over one period changes C
    # voltage by r; exact exponential differs O(r^2), deliberately not exact.
    exact_fraction = -math.expm1(-1 / (frequency * result['resistance_ohm'] * result['capacitance_f']))
    assert exact_fraction == pytest.approx(.025, rel=.013)


@pytest.mark.parametrize('inductance,capacitance,added', [(250e-9, 220e-12, 680e-12), (1.1e-6, 1e-9, 2.2e-9)])
def test_x06_measured_ring_zero_crossings_recover_hidden_components(inductance, capacitance, added):
    measured = []
    for c in [capacitance, capacitance + added]:
        # Undamped tank state [vC, iL]; first downward voltage zero is T/4.
        def zero(_t, state):
            return state[0]
        zero.terminal = True
        zero.direction = -1
        ode = solve_ivp(lambda _t, state, c=c: [-state[1] / c, state[0] / inductance],
                        (0, 1e-5), [1, 0], events=zero, rtol=1e-11, atol=1e-13,
                        max_step=1e-9)
        measured.append(1 / (4 * ode.t_events[0][0]))
    result = rc_snubber(*measured, added, 80e3, 300).outputs
    assert result['parasitic_inductance_h'] == pytest.approx(inductance, rel=1e-9)
    assert result['parasitic_capacitance_f'] == pytest.approx(capacitance, rel=1e-9)
    # R carries the LC characteristic energy ratio, while full charge and
    # discharge each lose Csn*V^2/2 in the optional hard-switch extension.
    c_sn, resistance = result['snubber_capacitance_f'], result['snubber_resistance_ohm']
    assert .5 * inductance * (1 / resistance) ** 2 == pytest.approx(.5 * capacitance, rel=1e-9)
    cycle_energy = 2 * quad(lambda charge: charge / c_sn, 0, c_sn * 300)[0]
    assert result['charge_discharge_power_w'] == pytest.approx(cycle_energy * 80e3)


@pytest.mark.parametrize('known_arm,parallel', [('low', False), ('high', False), ('low', True), ('high', True)])
def test_x07_actual_selected_parts_all_elementary_tolerance_corners(known_arm, parallel):
    reference, target, known = 1.225, 13.7, 12.4e3
    rt, vt = .025, .0075
    result = design_divider(reference, target, known, known_arm, series='E24', parallel=parallel,
                            resistor_tolerance=rt, reference_tolerance=vt).outputs
    chosen = result['selected_resistors_ohm']
    assert len(chosen) == (2 if parallel else 1)
    # Enumerate individual parallel resistors, rather than assigning tolerance
    # only to an already-collapsed equivalent arm.
    high_parts = chosen if known_arm == 'low' else [known]
    low_parts = [known] if known_arm == 'low' else chosen
    corners = []
    for signs in itertools.product([-1, 1], repeat=1 + len(high_parts) + len(low_parts)):
        vr = reference * (1 + signs[0] * vt)
        hp = [r * (1 + sign * rt) for r, sign in zip(high_parts, signs[1:])]
        lp = [r * (1 + sign * rt) for r, sign in zip(low_parts, signs[1 + len(high_parts):])]
        def residual(vout, vr=vr, hp=hp, lp=lp):
            return sum((vr - vout) / r for r in hp) + sum(vr / r for r in lp)
        corners.append(brentq(residual, vr, 1000))
    assert result['minimum_output_v'] == pytest.approx(min(corners), rel=1e-11)
    assert result['maximum_output_v'] == pytest.approx(max(corners), rel=1e-11)
    ground_current = sum(reference / r for r in low_parts)
    assert result['divider_current_a'] == pytest.approx(ground_current, rel=1e-12)
    assert result['output_v'] * ground_current == pytest.approx(
        result['high_arm_power_w'] + result['low_arm_power_w'], rel=1e-12)
    for voltage in corners:
        assert result['minimum_relative_error'] - 1e-12 <= voltage / target - 1 <= result['maximum_relative_error'] + 1e-12


@pytest.mark.parametrize('series,target,expected', [
    ('E24', 2800, 2700), ('E48', 2800, 2740), ('E96', 2800, 2800),
    ('E24', 4700, 4700), ('E48', 4700, 4640), ('E96', 4700, 4750),
    ('E24', 9900, 10000), ('E48', 9900, 10000), ('E96', 9900, 10000),
])
def test_x07_preferred_number_landmarks_and_decade_covariance(series, target, expected):
    # IEC preferred-number landmarks are input data, independent of code tables.
    assert preferred_resistance(target, series) == pytest.approx((expected,))
    for scale in [.001, 10, 1000]:
        assert preferred_resistance(target * scale, series) == pytest.approx((expected * scale,))


def test_x07_parallel_selection_is_bounded_best_e24_conductance_pair():
    # Independently enumerate a fixed laboratory inventory, then minimize in
    # total-conductance space converted back to actual resistance error.
    inventory = [value * scale for scale in [10, 100, 1000, 10000]
                 for value in [10, 11, 12, 13, 15, 16, 18, 20, 22, 24, 27, 30,
                               33, 36, 39, 43, 47, 51, 56, 62, 68, 75, 82, 91]]
    target = 7350
    choices = [r for r in inventory if target <= r <= 100 * target]
    errors = [abs(1 / (1 / left + 1 / right) - target)
              for left, right in itertools.combinations_with_replacement(choices, 2)]
    actual = preferred_resistance(target, 'E24', parallel=True)
    assert all(r in choices for r in actual)
    assert abs(1 / sum(1 / r for r in actual) - target) == pytest.approx(min(errors), abs=1e-10)


@pytest.mark.parametrize('vmin,vmax,vref,vadj,r1', [(24, 36, 2.5, 3.3, 100e3), (15, 18, 1.25, .5, 39e3)])
def test_x08_entire_analog_map_satisfies_real_feedback_kcl(vmin, vmax, vref, vadj, r1):
    output = analog_scaling(vmin, vmax, vref, vadj, r1).outputs
    r2, r3 = output['bottom_resistance_ohm'], output['adjust_resistance_ohm']
    checked = []
    for row in output['mapping']:
        voltage = _feedback_voltage(vref, r1, [r2], (row['adjust_v'], r3))
        assert row['output_v'] == pytest.approx(voltage, rel=1e-11)
        checked.append(voltage)
    assert checked[0] == pytest.approx(vmax)
    assert checked[-1] == pytest.approx(vmin)
    assert np.all(np.diff(checked) < 0)
    assert output['minimum_top_current_a'] == pytest.approx((checked[-1] - vref) / r1)
    assert output['adjust_to_output_gain'] == pytest.approx((checked[-1] - checked[0]) / vadj)


def test_x08_positive_resistor_feasibility_boundary():
    minimum, maximum, reference = 10, 20, 2.5
    boundary = reference * (maximum - minimum) / (maximum - reference)
    with pytest.raises(ModelDomainError):
        analog_scaling(minimum, maximum, reference, boundary * (1 - 1e-9), 100e3)
    output = analog_scaling(minimum, maximum, reference, boundary * (1 + 1e-6), 100e3).outputs
    assert output['bottom_resistance_ohm'] > 1e9
    assert output['output_at_zero_adjust_v'] == pytest.approx(maximum)
    assert output['output_at_maximum_adjust_v'] == pytest.approx(minimum)


@pytest.mark.parametrize('bits', [1, 3, 8, 16])
def test_x09_bit_array_feedback_kcl_including_every_carry_and_code_boundary(bits):
    vmin, vmax, reference, top = 8.2, 21.4, 1.2, 68e3
    output = digital_scaling(vmin, vmax, reference, bits, top).outputs
    rows = output['mapping']
    assert len(rows) == 2 ** bits
    codes = (range(2 ** bits) if bits <= 8 else
             sorted({0, 65535, *(2 ** k - 1 for k in range(1, bits + 1)), *(2 ** k for k in range(bits))}))
    for code in codes:
        resistors = [output['bottom_resistance_ohm']]
        resistors.extend(r for bit, r in enumerate(output['bit_resistances_ohm_lsb_first']) if code & (1 << bit))
        voltage = _feedback_voltage(reference, top, resistors)
        assert rows[code]['code'] == code
        assert rows[code]['output_v'] == pytest.approx(voltage, rel=1e-11)
    assert rows[0]['output_v'] == pytest.approx(vmin)
    assert rows[-1]['output_v'] == pytest.approx(vmax)
    diffs = np.diff([row['output_v'] for row in rows])
    assert np.all(diffs > 0)
    assert diffs == pytest.approx(output['step_v'], rel=1e-9)


@pytest.mark.parametrize('bits', [0, -1, 1.5, True, 17])
def test_x09_invalid_bit_counts_do_not_truncate_or_allocate_unbounded_mapping(bits):
    with pytest.raises(ModelDomainError):
        digital_scaling(5, 12, 1.25, bits, 10e3)


@pytest.mark.parametrize('category,start,end,value,expected', [
    ('flux_density', 'G', 'T', 10000, 1),
    ('length', 'mil', 'mm', 1000, 25.4),
    ('mass', 'oz', 'g', 16, 453.59237),
    ('airflow', 'lfm', 'm/s', 60, .3048),
    ('pcb_copper', 'oz/ft2 (nominal)', 'um', 2, 70),
    ('temperature', 'F', 'K', -459.67, 0),
    ('temperature', 'C', 'F', 100, 212),
    ('power', 'PS (metric hp)', 'W', 1, 75 * 9.80665),
    ('power', 'hp (mechanical)', 'W', 1, 550 * .3048 * .45359237 * 9.80665),
    ('torque', 'lb ft', 'N m', 1, .3048 * .45359237 * 9.80665),
    ('speed', 'mph', 'm/s', 60, 60 * 5280 * .3048 / 3600),
])
def test_x10_dimensional_definitions_and_bidirectional_round_trip(category, start, end, value, expected):
    actual = convert_units(value, category, start, end).outputs['converted_value']
    assert actual == pytest.approx(expected, rel=1e-12, abs=1e-10)
    restored = convert_units(actual, category, end, start).outputs['converted_value']
    assert restored == pytest.approx(value, rel=1e-12, abs=1e-10)


def test_x10_voltage_and_power_db_link_by_squared_waveform_energy():
    t = np.linspace(0, 1, 4097)
    voltage_in = np.sin(2 * np.pi * 3 * t)
    voltage_out = 3.7 * voltage_in
    energy_ratio = trapezoid(voltage_out ** 2, t) / trapezoid(voltage_in ** 2, t)
    voltage_db = convert_units(3.7, 'voltage_gain', 'factor', 'dB').outputs['converted_value']
    power_db = convert_units(energy_ratio, 'power_gain', 'factor', 'dB').outputs['converted_value']
    assert voltage_db == pytest.approx(power_db, rel=1e-12)
    assert convert_units(voltage_db, 'voltage_gain', 'dB', 'factor').outputs['converted_value'] == pytest.approx(3.7)
    assert convert_units(power_db, 'power_gain', 'dB', 'factor').outputs['converted_value'] == pytest.approx(energy_ratio)
    with pytest.raises(ModelDomainError):
        convert_units(0, 'voltage_gain', 'factor', 'dB')
    with pytest.raises(ModelDomainError):
        convert_units(-274, 'temperature', 'C', 'F')


def _filter_edges(inputs, frequency, *, damping=True, filter_cap=True):
    s = 2j * np.pi * frequency
    # Self capacitance is established from a measured ideal unloaded LC period.
    period = 1 / inputs.inductor_srf_hz
    self_c = (period / (2 * np.pi)) ** 2 / inputs.inductance_h
    edges = [('source', 'coilr', inputs.inductor_dcr_ohm),
             ('coilr', 'converter', s * inputs.inductance_h),
             ('source', 'converter', 1 / (s * self_c))]
    edges += _capacitor_edges('converter', 'ground', 'cin', inputs.converter_capacitance_f,
                              inputs.converter_esr_ohm, inputs.converter_esl_h, s)
    if damping:
        edges += [('converter', 'dampingr', inputs.damping_resistance_ohm)]
        edges += _capacitor_edges('dampingr', 'ground', 'cd', inputs.damping_capacitance_f,
                                  inputs.damping_esr_ohm, inputs.damping_esl_h, s)
    if filter_cap:
        edges += _capacitor_edges('source', 'ground', 'cf', inputs.filter_capacitance_f,
                                  inputs.filter_esr_ohm, inputs.filter_esl_h, s)
    return edges


@pytest.mark.parametrize('configuration', ['lc', 'pi'])
@pytest.mark.parametrize('scale', [.3, 1, 5])
def test_x12_individual_component_mna_checks_both_directions_and_impedances(configuration, scale):
    inputs = FilterInputs(6.8e-6 * scale, 4.7e-6, 2.2e-6, 33e-6, .47, 24, 3,
        inductor_dcr_ohm=.035, inductor_srf_hz=3e6,
        converter_esr_ohm=.035, converter_esl_h=3.1e-9,
        filter_esr_ohm=.018, filter_esl_h=1.6e-9,
        damping_esr_ohm=.06, damping_esl_h=9e-9,
        minimum_frequency_hz=31, maximum_frequency_hz=13e6, points=25,
        configuration=configuration, noise_frequency_hz=537e3)
    output = differential_filter(inputs).outputs
    curves = output['curves']
    for k, frequency in enumerate(curves['frequency_hz']):
        damped = _filter_edges(inputs, frequency, filter_cap=configuration == 'pi')
        undamped = _filter_edges(inputs, frequency, damping=False, filter_cap=configuration == 'pi')
        voltage, _ = _mna(damped, {'ground': 0, 'converter': 1})
        expected = {'noise_to_other': voltage['source']}
        for name, edges in [('damped', damped), ('undamped', undamped)]:
            voltage, _ = _mna(edges, {'ground': 0, 'source': 1})
            expected['other_to_noise_' + name] = voltage['converter']
            voltage, _ = _mna(edges, {'ground': 0, 'source': 0}, {'converter': 1})
            expected['output_impedance_' + name] = voltage['converter']
        for name, oracle in expected.items():
            reconstructed = curves[name + '_magnitude'][k] * np.exp(1j * math.radians(curves[name + '_phase_deg'][k]))
            assert reconstructed == pytest.approx(oracle, rel=2e-9, abs=1e-11)
    assert output['inductor_dc_loss_w'] == pytest.approx(3 ** 2 * .035)
    # Noise point is evaluated independently of the plotting grid.
    edges = _filter_edges(inputs, inputs.noise_frequency_hz, filter_cap=configuration == 'pi')
    voltage, _ = _mna(edges, {'ground': 0, 'converter': 1})
    measured_db = 20 * np.log10(abs(voltage['source']) * inputs.ripple_amplitude_v / 1e-6)
    assert output['noise_frequency_checks']['noise_to_other']['filtered_level_dbuv'] == pytest.approx(measured_db, abs=1e-9)


@pytest.mark.parametrize('current', [0, .5, 12])
def test_x12_negative_incremental_input_resistance_matches_independent_state_matrix(current):
    inputs = FilterInputs(10e-6, 1e-6, 4.7e-6, 4e-6, 0, 12, current,
        inductor_dcr_ohm=.1, inductor_srf_hz=1e9, converter_esr_ohm=0,
        damping_esr_ohm=0, minimum_frequency_hz=100, maximum_frequency_hz=200e3,
        points=31)
    output = differential_filter(inputs).outputs
    total_c = inputs.converter_capacitance_f + inputs.damping_capacitance_f
    assert output['effective_capacitance_f'] == pytest.approx(total_c, rel=1e-10)
    assert output['effective_esr_ohm'] == pytest.approx(0, abs=1e-12)
    # Linearize I=P/V numerically. dI/dV is NEGATIVE, including at light load.
    power = inputs.input_voltage_v * current
    eps = 1e-4
    derivative = ((power / (12 + eps)) - (power / (12 - eps))) / (2 * eps)
    state_matrix = np.array([[-.1 / inputs.inductance_h, -1 / inputs.inductance_h],
                             [1 / total_c, -derivative / total_c]])
    eigenvalues = np.linalg.eigvals(state_matrix)
    omega0 = 1 / math.sqrt(inputs.inductance_h * total_c)
    # TI's approximate delta normalizes the trace by the unloaded omega0.
    # It is not exact loaded-pole damping ratio when R*G is appreciable.
    assert output['damping_factor'] == pytest.approx(-sum(eigenvalues).real / (2 * omega0), rel=1e-8, abs=1e-12)
    assert (output['damping_factor'] > 0) == (max(eigenvalues.real) < 0)
    if current:
        assert output['constant_power_input_resistance_ohm'] == pytest.approx(1 / derivative, rel=1e-8)
    else:
        assert output['constant_power_input_resistance_ohm'] is None
    if current == 12:
        assert any('Non-positive' in warning for warning in differential_filter(inputs).warnings)


def test_x12_single_noise_point_ideal_srf_is_a_model_domain_error():
    # SRF is outside the curve grid but exactly at the additional noise check.
    # It must receive the same domain handling as a singular plotted sample.
    inputs = FilterInputs(3e-6, 1e-6, 1e-6, 4e-6, 1, 12, 1,
                          inductor_srf_hz=1e6, noise_frequency_hz=1e6,
                          maximum_frequency_hz=1e5)
    with pytest.raises(ModelDomainError):
        differential_filter(inputs)


def test_x04_actual_ideal_diode_conduction_exposes_model_limit_not_a_new_golden():
    """A different physical model must NOT be used to silently rewrite eq13.

    The diode stays on briefly AFTER the line peak until C*dVline/dt+P/V=0.
    Its actual periodic RMS differs from both the printed formula and screenshot.
    Source impedance, bridge drop, ESR and AC source inductance remain excluded.
    """
    output = acdc_bulk_capacitor(100, 85, .2, 47).outputs
    c, vp, power = output['capacitance_f'], output['maximum_voltage_v'], 100
    period, omega = 1 / 94, 2 * np.pi * 47
    line = lambda t: vp * abs(np.cos(omega * t))
    diode_current_after_peak = lambda t: -c * vp * omega * np.sin(omega * t) + power / line(t)
    toff = brentq(diode_current_after_peak, 1e-12, period / 10, xtol=1e-16)
    free = solve_ivp(lambda _t, v: [-power / (c * v[0])], (toff, period), [line(toff)],
                     dense_output=True, rtol=1e-12, atol=1e-12)
    ton = brentq(lambda t: free.sol(t)[0] - line(t), period / 2, period, xtol=1e-16)
    assert 0 < toff < period / 2 < ton < period
    assert free.sol(ton)[0] == pytest.approx(85.2997146103, rel=1e-9)
    i1 = lambda t: -c * vp * omega * np.sin(omega * t)
    i2 = lambda t: -power / free.sol(t)[0]
    i3 = lambda t: c * vp * omega * np.sin(omega * t)
    portions = [(i1, 0, toff), (i2, toff, ton), (i3, ton, period)]
    charge = sum(quad(fn, a, b, epsabs=1e-13)[0] for fn, a, b in portions)
    assert charge == pytest.approx(0, abs=1e-11)
    rms_integral = math.sqrt(sum(quad(lambda t, fn=fn: fn(t) ** 2, a, b)[0]
                                 for fn, a, b in portions) / period)
    assert rms_integral == pytest.approx(2.281250614332158, rel=1e-9)
    # Explicit convergence of an independent piecewise trapezoid integration.
    values = []
    for count in [129, 513, 2049]:
        ms = 0
        for fn, a, b in portions:
            t = np.linspace(a, b, count)
            ms += trapezoid(fn(t) ** 2, t)
        values.append(math.sqrt(ms / period))
    errors = [abs(value - rms_integral) for value in values]
    assert errors[2] < errors[1] / 10 < errors[0] / 100
    assert values[-1] == pytest.approx(rms_integral, rel=1e-7)
    # Check the assumed conduction states actually satisfy ideal diode signs.
    assert np.all([diode_current_after_peak(t) >= -1e-9 for t in np.linspace(0, toff, 41)])
    assert np.all([free.sol(t)[0] >= line(t) - 1e-8 for t in np.linspace(toff, ton, 101)])
    assert not math.isclose(rms_integral, 2.49, rel_tol=.01)
    assert not math.isclose(rms_integral, output['bulk_rms_current_eq13_a'], rel_tol=.01)


def test_x03_antiresonant_circulating_currents_are_not_clipped_to_source_current():
    frequency, capacitance = 10e3, 1e-6
    omega = 2 * np.pi * frequency
    inductance = 2 / (omega ** 2 * capacitance)
    branches = [CapacitorBranch(capacitance, .05), CapacitorBranch(capacitance, .05, inductance)]
    output = capacitor_current_sharing(branches, 1, frequency).outputs
    # Opposite reactive branch impedances nearly cancel in total admittance.
    # A 1 A source can therefore cause >100 A in EACH ideal linear branch.
    currents = [complex(row['current_real_a'], row['current_imag_a'])
                for row in output['spectrum'][0]['branches']]
    assert all(abs(current) > 100 for current in currents)
    assert currents[0].imag * currents[1].imag < 0
    assert sum(currents) == pytest.approx(1, abs=1e-10)
    edges = []
    for k, branch in enumerate(branches):
        edges += _capacitor_edges('bus', 'ground', str(k), branch.capacitance_f,
                                  branch.esr_ohm, branch.esl_h, 1j * omega)
    _, oracle = _mna(edges, {'ground': 0}, {'bus': 1})
    assert currents == pytest.approx(oracle[::3], rel=1e-10)
    with pytest.raises(ModelDomainError):
        capacitor_current_sharing([replace(branch, esr_ohm=0) for branch in branches], 1, frequency)


@pytest.mark.parametrize('scale', [.01, 1, 100])
def test_x07_forward_divider_is_voltage_scale_invariant_but_dissipation_is_not(scale):
    output = divider_output(2.5, 38e3 * scale, 10e3 * scale,
                            resistor_tolerance=0, reference_tolerance=0).outputs
    voltage = _feedback_voltage(2.5, 38e3 * scale, [10e3 * scale])
    assert output['output_v'] == pytest.approx(voltage)
    assert output['minimum_output_v'] == pytest.approx(voltage)
    assert output['maximum_output_v'] == pytest.approx(voltage)
    assert output['divider_current_a'] * scale == pytest.approx(250e-6)
    assert (output['high_arm_power_w'] + output['low_arm_power_w']) * scale == pytest.approx(.003)


@pytest.mark.parametrize('resistance', [.05, .5, 5])
@pytest.mark.parametrize('requested_frequency', [None, 13700])
def test_x12_rc_damping_equivalent_is_the_actual_independent_node_impedance(resistance, requested_frequency):
    inputs = FilterInputs(4.7e-6, 2.2e-6, 10e-6, 33e-6, resistance, 24, 2,
        inductor_dcr_ohm=.025, converter_esr_ohm=.018, damping_esr_ohm=.045,
        converter_esl_h=7e-9, damping_esl_h=19e-9, inductor_srf_hz=2e6,
        damping_frequency_hz=requested_frequency, points=25)
    output = differential_filter(inputs).outputs
    frequency = output['damping_frequency_hz']
    # ESL/SRF are intentionally omitted from ONLY this TI damping estimate;
    # full frequency responses are independently covered with them included.
    s = 2j * np.pi * frequency
    edges = _capacitor_edges('converter', 'ground', 'ci', 2.2e-6, .018, 0, s)
    edges += [('converter', 'damper', resistance)]
    edges += _capacitor_edges('damper', 'ground', 'cd', 33e-6, .045, 0, s)
    voltage, _ = _mna(edges, {'ground': 0}, {'converter': 1})
    impedance = voltage['converter']
    reconstruction = output['effective_esr_ohm'] + 1 / (s * output['effective_capacitance_f'])
    assert reconstruction == pytest.approx(impedance, rel=1e-11)
    if requested_frequency is None:
        assert (impedance + s * inputs.inductance_h).imag == pytest.approx(0, abs=1e-10)
    else:
        assert frequency == requested_frequency


def test_x05_and_x06_invalid_energy_and_measurement_domains():
    with pytest.raises(ModelDomainError):
        rcd_snubber(1e-3, 10, 100e3, 100, 1.01, .05)
    with pytest.raises(ModelDomainError):
        rcd_snubber(1e-6, 10, 100e3, 100, 1, .05)
    for f1 in [10e6, 12e6]:
        with pytest.raises(ModelDomainError):
            rc_snubber(10e6, f1, 100e-12)
    with pytest.raises(ModelDomainError):
        rc_snubber(10e6, 5e6, 100e-12, switching_frequency_hz=100e3)

"""QR extensions checked against legacy closed forms and independent voltage ODEs."""
import json
import math
from dataclasses import FrozenInstanceError, asdict, replace

import numpy as np
import pytest
from scipy.integrate import quad, solve_ivp
from scipy.linalg import expm
from scipy.optimize import brentq, minimize_scalar

from power_stage_design.models import DesignInputs, ModelDomainError
from power_stage_design.qr_dynamics import QROptions, calculate_qr_dynamic
from power_stage_design.topologies import _quasi_resonant_flyback, calculate_stage


def inputs(vin=48.0, turns=8.0, **kwargs):
    defaults = {"topology": "T11", "vin_v": vin, "vout_v": 12.0, "iout_a": 1.0,
                "fsw_hz": 1e6, "inductance_h": 200e-6, "capacitance_f": 100e-6,
                "turns_ratio_np_ns": turns, "resonant_capacitance_f": 500e-12}
    defaults.update(kwargs)
    return DesignInputs(**defaults)


def selected(index=1, **kwargs):
    return QROptions(valley_policy="selected", valley_index=index, **kwargs)


def at(wave, time):
    fraction = time/wave.period_s
    for segment in wave.segments:
        if segment.start_fraction <= fraction <= segment.end_fraction:
            return float(segment.evaluate((fraction-segment.start_fraction)/segment.duration_fraction))
    raise AssertionError("Sample outside waveform")


@pytest.mark.parametrize("vin,turns", [(400.0, 4.0), (48.0, 8.0), (48.0, 4.0)])
def test_constant_c_first_event_matches_existing_analytical_limit(vin, turns):
    spec = inputs(vin, turns)
    baseline = _quasi_resonant_flyback(spec)
    result = calculate_qr_dynamic(spec, selected())
    for metric in ("actual_switching_frequency_hz", "turn_off_charge_time_s", "demagnetization_time_s", "resonant_wait_time_s", "turn_on_capacitive_energy_j"):
        assert result.metrics[metric] == pytest.approx(baseline.metrics[metric], rel=1e-8, abs=1e-16)
    for device in ("Q1", "D1", "Lm", "primary", "secondary", "Cout"):
        for key in ("current_rms_a", "current_mean_a", "voltage_rms_v"):
            assert result.stress[device][key] == pytest.approx(baseline.stress[device][key], rel=1e-8, abs=1e-9)


def test_second_clamped_valley_has_actual_hold_release_and_analytic_period():
    spec = inputs()
    result = calculate_qr_dynamic(spec, selected(2))
    lm, c, vin = spec.inductance_h, spec.resonant_capacitance_f, spec.vin_v
    reflected = spec.turns_ratio_np_ns*spec.vout_v
    impedance = math.sqrt(lm/c)
    omega = 1/math.sqrt(lm*c)
    negative = -math.sqrt(reflected**2-vin**2)/impedance
    expected_hold = -lm*negative/vin
    expected_wait = math.acos(-vin/reflected)/omega+expected_hold+2*math.pi/omega
    assert result.metrics["body_clamp_hold_time_s"] == pytest.approx(expected_hold, rel=1e-9)
    assert result.metrics["resonant_wait_time_s"] == pytest.approx(expected_wait, rel=1e-9)
    assert result.stress["Dbody"]["current_max_a"] == pytest.approx(-negative, rel=1e-8)
    assert result.stress["Dbody"]["current_mean_a"] > 0
    assert result.metrics["gate_turn_on_voltage_v"] == pytest.approx(0, abs=1e-10)
    assert result.metrics["turn_on_magnetizing_current_a"] == pytest.approx(0, abs=1e-10)
    assert abs(result.diagnostics["modeled_power_balance_w"]) < 1e-8


def test_first_feasible_ceiling_and_selected_rejection():
    spec = inputs(fsw_hz=100e3)
    result = calculate_qr_dynamic(spec, QROptions(maximum_valley_index=4))
    assert result.metrics["valley_index"] == 2
    assert result.metrics["actual_switching_frequency_hz"] < spec.fsw_hz
    with pytest.raises(ModelDomainError, match="exceeds ceiling"):
        calculate_qr_dynamic(spec, selected())
    with pytest.raises(ModelDomainError, match="No feasible QR valley"):
        calculate_qr_dynamic(spec, QROptions(maximum_valley_index=1))


def test_delays_inside_clamp_and_after_release_are_distinct_real_states():
    spec = inputs()
    no_delay = calculate_qr_dynamic(spec, selected())
    inside = calculate_qr_dynamic(spec, selected(detector_delay_s=100e-9, gate_delay_s=100e-9))
    after = calculate_qr_dynamic(spec, selected(gate_delay_s=1e-6))
    assert inside.metrics["gate_turn_on_voltage_v"] == pytest.approx(0, abs=1e-10)
    expected = no_delay.metrics["turn_on_magnetizing_current_a"]+spec.vin_v/spec.inductance_h*200e-9
    assert inside.metrics["turn_on_magnetizing_current_a"] == pytest.approx(expected, rel=1e-8)
    assert inside.metrics["body_clamp_hold_time_s"] == pytest.approx(200e-9, rel=1e-8)
    assert after.metrics["gate_turn_on_voltage_v"] > 10
    assert after.metrics["turn_on_capacitive_loss_w"] > 0
    assert after.metrics["body_clamp_hold_time_s"] > inside.metrics["body_clamp_hold_time_s"]
    assert abs(after.diagnostics["modeled_power_balance_w"]) < 1e-8


DIFFERENTIAL = ((0.0, 1e-9), (20.0, 800e-12), (80.0, 300e-12), (200.0, 200e-12))
CHARGE = ((0.0, 0.0), (20.0, 20e-9), (80.0, 50e-9), (200.0, 75e-9))


@pytest.mark.parametrize("model,table", [("charge", CHARGE), ("differential", DIFFERENTIAL)])
def test_nonlinear_tables_change_physics_and_keep_periodic_closure(model, table):
    spec = inputs()
    nonlinear = calculate_qr_dynamic(spec, selected(2, capacitance_model=model, capacitance_table=table))
    constant = calculate_qr_dynamic(spec, selected(2))
    assert abs(nonlinear.metrics["actual_switching_frequency_hz"]-constant.metrics["actual_switching_frequency_hz"]) > 100
    assert abs(nonlinear.diagnostics["modeled_power_balance_w"]) < 1e-8
    assert abs(nonlinear.diagnostics["inductor_mean_voltage_v"]) < 1e-6
    assert abs(nonlinear.diagnostics["periodic_charge_residual_c"]) < 1e-15
    assert abs(nonlinear.diagnostics["output_capacitor_mean_current_a"]) < 1e-9
    assert nonlinear.provenance["capacitance_model"] == model
    assert "not independently verified" in nonlinear.provenance["capacitance_source"]
    current = nonlinear.waveforms["Lm"].current_a
    assert float(current.segments[0].evaluate(0)) == pytest.approx(float(current.segments[-1].evaluate(1)), abs=1e-9)


def test_nonlinear_differential_cycle_against_independent_voltage_state_ode():
    """Reference integrates (i,V), while production integrates (i,Q)."""
    spec = inputs()
    result = calculate_qr_dynamic(spec, selected(2, capacitance_model="differential", capacitance_table=DIFFERENTIAL))
    vpoints, cpoints = np.array(DIFFERENTIAL).T
    lm, vin, vr = spec.inductance_h, spec.vin_v, spec.turns_ratio_np_ns*spec.vout_v
    vmax = vin+vr
    def derivative(t, state):
        current, voltage = state
        capacitance = np.interp(voltage, vpoints, cpoints)
        return [(vin-voltage)/lm, current/capacitance]
    def advance(state, component, value, direction):
        def event(t, y): return y[component]-value
        event.terminal = True
        event.direction = direction
        solution = solve_ivp(derivative, (0, 20e-6), state, events=event, method="DOP853",
                             rtol=2e-12, atol=[1e-13, 1e-10], max_step=2e-9)
        assert solution.success and solution.t_events[0].size
        return solution.t[-1], solution.y[:, -1]
    first_time, first_state = advance((0.0, vmax), 1, 0.0, -1)
    hold = -lm*first_state[0]/vin
    crest_time, crest = advance((0.0, 0.0), 0, 0.0, -1)
    trough_time, trough = advance((0.0, crest[1]), 0, 0.0, 1)
    wait = first_time+hold+crest_time+trough_time
    assert abs(trough[1]) < 2e-6
    def cycle(peak):
        charging, at_clamp = advance((peak, 0.0), 1, vmax, 1)
        td = lm*at_clamp[0]/vr
        period = lm*peak/vin+charging+td+wait
        delivered = .5*lm*at_clamp[0]**2/period
        return delivered, period
    peak = brentq(lambda current: cycle(current)[0]-spec.vout_v*spec.iout_a, .1, 10, xtol=1e-11)
    _, period = cycle(peak)
    assert result.metrics["turn_off_magnetizing_current_a"] == pytest.approx(peak, rel=2e-8)
    assert result.metrics["actual_switching_frequency_hz"] == pytest.approx(1/period, rel=2e-8)
    assert result.metrics["resonant_wait_time_s"] == pytest.approx(wait, rel=2e-8)
    assert result.metrics["body_clamp_hold_time_s"] == pytest.approx(hold, rel=2e-8)


@pytest.fixture(scope="module")
def resistive_result():
    spec = inputs(400, 4)
    return spec, calculate_qr_dynamic(spec, selected(on_resistance_ohm=2.0))


def test_finite_ron_physically_consumes_cnode_and_closes_charge(resistive_result):
    spec, result = resistive_result
    assert result.metrics["turn_on_capacitive_loss_w"] == 0
    assert result.metrics["channel_resistive_loss_w"] > 0
    assert result.metrics["ideal_turn_on_charge_impulse_c"] == 0
    gate_v = spec.vin_v-spec.turns_ratio_np_ns*spec.vout_v
    assert result.stress["Q1"]["current_max_a"] == pytest.approx(gate_v/2, rel=1e-8)
    assert result.stress["Cnode"]["current_min_a"] < -100
    assert abs(result.stress["Cnode"]["current_mean_a"]) < 1e-8
    assert abs(result.diagnostics["modeled_power_balance_w"]) < 1e-8
    for t in (0, 1e-10, 1e-9, 1e-8):
        primary = at(result.waveforms["primary"].current_a, t)
        node = at(result.waveforms["Cnode"].current_a, t)
        channel = at(result.waveforms["Q1"].current_a, t)
        assert primary == pytest.approx(node+channel, abs=1e-9)
    voltage = result.waveforms["Q1"].voltage_v
    assert at(voltage, 1e-8) < .01*at(voltage, 0)
    assert voltage.segments[0].evaluate(0) == pytest.approx(voltage.segments[-1].evaluate(1), abs=1e-8)


def test_finite_ron_current_energy_against_independent_ode_quadrature(resistive_result):
    spec, result = resistive_result
    resistance, c, lm = 2.0, spec.resonant_capacitance_f, spec.inductance_h
    peak = result.metrics["turn_off_magnetizing_current_a"]
    def rhs(t, y):
        current, voltage, _energy = y
        return [(spec.vin_v-voltage)/lm, (current-voltage/resistance)/c, voltage**2/resistance]
    def event(t, y): return y[0]-peak
    event.direction = 1
    event.terminal = True
    initial_v = spec.vin_v-spec.turns_ratio_np_ns*spec.vout_v
    reference = solve_ivp(rhs, (0, 20e-6), (0.0, initial_v, 0.0), events=event, method="Radau",
                          rtol=1e-11, atol=[1e-12, 1e-10, 1e-15], dense_output=True)
    assert reference.success and reference.t_events[0].size
    expected_power = reference.y[2, -1]*result.metrics["actual_switching_frequency_hz"]
    assert result.metrics["channel_resistive_loss_w"] == pytest.approx(expected_power, rel=2e-8)
    expected_rms = math.sqrt(expected_power/resistance)
    assert result.stress["Q1"]["current_rms_a"] == pytest.approx(expected_rms, rel=2e-8)
    assert result.duty/result.metrics["actual_switching_frequency_hz"] == pytest.approx(reference.t[-1], rel=2e-8)


def test_constant_tables_equal_constant_c_and_energy_integral():
    spec = inputs(400, 4)
    c = spec.resonant_capacitance_f
    plain = calculate_qr_dynamic(spec, selected())
    for model, table in (("charge", ((0, 0), (200, 200*c), (500, 500*c))), ("differential", ((0, c), (200, c), (500, c)))):
        result = calculate_qr_dynamic(spec, selected(capacitance_model=model, capacitance_table=table))
        assert result.metrics["actual_switching_frequency_hz"] == pytest.approx(plain.metrics["actual_switching_frequency_hz"], rel=1e-8)
        gate = result.metrics["gate_turn_on_voltage_v"]
        energy = quad(lambda v: v*c, 0, gate, epsabs=1e-14)[0]
        assert result.metrics["turn_on_capacitive_energy_j"] == pytest.approx(energy, rel=1e-10)


def test_nonlinear_energy_boundary_is_not_guessed_from_voltage_ratio():
    # Vin<reflected voltage would clamp with constant C. A strongly decreasing
    # differential C puts the conserved energy below the zero-voltage boundary.
    spec = inputs()
    table = ((0, 10e-9), (10, 200e-12), (150, 100e-12))
    result = calculate_qr_dynamic(spec, selected(capacitance_model="differential", capacitance_table=table))
    assert result.metrics["drain_valley_voltage_v"] > 0
    assert result.metrics["body_clamp_hold_time_s"] == 0


@pytest.mark.parametrize("kwargs", [
    {"valley_index": 0}, {"valley_index": True}, {"maximum_valley_index": 101},
    {"valley_policy": "guessed_ti"}, {"detector_delay_s": -1}, {"gate_delay_s": math.nan},
    {"on_resistance_ohm": 0}, {"model_revision": "future"}, {"capacitance_source": ""},
    {"capacitance_model": "charge", "capacitance_table": ((0, 0), (20, 1e-8), (20, 2e-8))},
    {"capacitance_model": "charge", "capacitance_table": ((0, 0), (20, 1e-8), (30, 1e-8))},
    {"capacitance_model": "charge", "capacitance_table": ((0, 1e-9), (20, 1e-8))},
    {"capacitance_model": "differential", "capacitance_table": ((0, 1e-9), (20, 0))},
    {"capacitance_model": "differential", "capacitance_table": ((1, 1e-9), (20, 1e-9))},
    {"capacitance_model": "differential", "capacitance_table": ((0, 1e-9), (20, math.inf))},
    {"capacitance_table": ((0, 0), (20, 1e-9))},
])
def test_invalid_options_are_explicit(kwargs):
    with pytest.raises(ModelDomainError):
        QROptions(**kwargs)


def test_table_coverage_missing_constant_and_unsupported_domain():
    with pytest.raises(ModelDomainError, match="cover"):
        calculate_qr_dynamic(inputs(), selected(capacitance_model="differential", capacitance_table=((0, 1e-9), (100, 1e-9))))
    with pytest.raises(ModelDomainError, match="resonant_capacitance_f"):
        calculate_qr_dynamic(inputs(resonant_capacitance_f=None), selected())
    with pytest.raises(ModelDomainError, match="only to QR"):
        calculate_qr_dynamic(inputs(topology="T09"), selected())
    with pytest.raises(ModelDomainError, match="positive output"):
        calculate_qr_dynamic(inputs(iout_a=0), selected())
    with pytest.raises(ModelDomainError, match="leakage"):
        calculate_qr_dynamic(inputs(leakage_inductance_h=1e-6), selected())


def test_immutable_table_and_bound_result_identity():
    table = [[0, 0], [200, 100e-9]]
    options = selected(capacitance_model="charge", capacitance_table=table)
    table[1][1] = 999
    assert options.capacitance_table == ((0.0, 0.0), (200.0, 100e-9))
    with pytest.raises(FrozenInstanceError):
        options.valley_index = 2
    spec = inputs()
    result = calculate_qr_dynamic(spec, options)
    assert result.inputs.qr_options == options
    assert result.design_id == replace(spec, qr_options=options).design_id
    assert result.design_id != spec.design_id


def test_cancel_before_and_during_solver():
    with pytest.raises(InterruptedError):
        calculate_qr_dynamic(inputs(), selected(), cancelled=lambda: True)
    count = 0
    def cancel():
        nonlocal count
        count += 1
        return count > 20
    with pytest.raises(InterruptedError):
        calculate_qr_dynamic(inputs(), selected(), cancelled=cancel)
    assert count == 21


@pytest.mark.parametrize("model,table", [("charge", CHARGE), ("differential", DIFFERENTIAL)])
def test_table_plus_finite_ron_and_diode_drop_preserves_energy(model, table):
    result = calculate_qr_dynamic(inputs(diode_drop_v=.6, resonant_capacitance_f=None),
                                  selected(2, capacitance_model=model, capacitance_table=table,
                                           on_resistance_ohm=1.0, detector_delay_s=100e-9))
    assert result.metrics["modeled_diode_loss_w"] == pytest.approx(.6)
    assert result.metrics["channel_resistive_loss_w"] > 0
    assert abs(result.diagnostics["modeled_power_balance_w"]) < 1e-8
    assert abs(result.stress["Cnode"]["current_mean_a"]) < 1e-8
    assert result.stress["Dbody"]["current_mean_a"] > 0


def test_late_gate_with_negative_current_enters_body_clamp_during_discharge():
    spec = inputs(400, 4)
    delay = 1.5*math.pi*math.sqrt(spec.inductance_h*spec.resonant_capacitance_f)
    result = calculate_qr_dynamic(spec, selected(on_resistance_ohm=2.0, gate_delay_s=delay))
    assert result.metrics["drain_valley_voltage_v"] > 0
    assert result.metrics["turn_on_magnetizing_current_a"] < 0
    assert result.metrics["gate_turn_on_voltage_v"] > 0
    assert result.metrics["body_clamp_hold_time_s"] > 0
    assert result.stress["Dbody"]["current_max_a"] > 0
    assert result.stress["Q1"]["voltage_min_v"] > -1e-8
    assert abs(result.diagnostics["modeled_power_balance_w"]) < 1e-8


def test_production_dispatch_and_json_restore_preserve_all_option_identity():
    options = selected(2, capacitance_model="charge", capacitance_table=CHARGE,
                       detector_delay_s=20e-9, capacitance_source="Laboratory Q–V data, sample A")
    spec = inputs(qr_options=options)
    restored = DesignInputs(**json.loads(json.dumps(asdict(spec))))
    assert restored == spec
    assert restored.design_id == spec.design_id
    result = calculate_stage(restored)
    assert result.design_id == restored.design_id
    assert result.inputs.qr_options == options
    assert result.provenance["model_revision"] == "qr-dynamic-1"
    assert result.provenance["capacitance_source"] == options.capacitance_source
    for changed in (replace(options, valley_index=3), replace(options, gate_delay_s=1e-9),
                    replace(options, capacitance_source="Another sample"),
                    replace(options, capacitance_table=((0, 0), (200, 80e-9)))):
        assert replace(spec, qr_options=changed).design_id != spec.design_id
    # A direct call overriding options must bind those options to the returned
    # snapshot, even when inputs previously named a different controller.
    override = selected(3)
    direct = calculate_qr_dynamic(spec, override)
    assert direct.inputs.qr_options == override
    assert direct.design_id == replace(spec, qr_options=override).design_id


def test_project_roundtrip_recomputes_advanced_cycle_and_rejects_future_revision(tmp_path):
    from power_stage_design.project import read_project, save_project
    options = selected(2, capacitance_model="differential", capacitance_table=DIFFERENTIAL,
                       capacitance_source="User-provided measured curve")
    spec = inputs(qr_options=options)
    path = save_project(spec, tmp_path/"qr-project.json")
    restored, document = read_project(path)
    assert restored == spec
    assert document["result_identity"] == spec.design_id
    assert calculate_stage(restored).metrics["valley_index"] == 2
    saved = json.loads(path.read_text())
    saved["inputs"]["qr_options"]["model_revision"] = "qr-dynamic-unknown"
    path.write_text(json.dumps(saved))
    with pytest.raises(ModelDomainError, match="model_revision"):
        read_project(path)
    with pytest.raises(ModelDomainError, match="Invalid qr_options"):
        replace(spec, qr_options={"unrecognized": 1})


def _constant_ron_time_oracle(resistance):
    """Independent exact voltage/current matrix exponential, parameterized by time.

    This reference deliberately does not use the production charge state,
    current-event bracketing or sampled power-maximum search.
    """
    vin, reflected, lm, capacitance = 48.0, 96.0, 200e-6, 500e-12
    impedance = math.sqrt(lm/capacitance)
    omega = 1/math.sqrt(lm*capacitance)
    hold = lm*math.sqrt(reflected**2-vin**2)/impedance/vin
    waiting = math.acos(-vin/reflected)/omega
    matrix = np.array([[0, -1/lm], [1/capacitance, -1/(resistance*capacitance)]])
    equilibrium = np.array([vin/resistance, vin])
    def state(time):
        return equilibrium-expm(matrix*time)@equilibrium
    def cycle(time):
        current, voltage = state(time)
        crest_time = math.atan2(impedance*current, voltage-vin)/omega
        def voltage_off(t):
            return vin+(voltage-vin)*math.cos(omega*t)+impedance*current*math.sin(omega*t)
        if voltage_off(crest_time) <= vin+reflected:
            return 0.0, None, current
        charging = brentq(lambda t: voltage_off(t)-vin-reflected, 0, crest_time, xtol=1e-18)
        transferred = current*math.cos(omega*charging)-(voltage-vin)/impedance*math.sin(omega*charging)
        period = hold+time+charging+lm*transferred/reflected+waiting
        return .5*lm*transferred**2/period, period, current
    maximum = minimize_scalar(lambda time: -cycle(time)[0], bounds=(1e-14, 15*lm/resistance),
                              method="bounded", options={"xatol": 1e-16})
    assert maximum.success
    return cycle, maximum.x, -maximum.fun


@pytest.mark.parametrize("resistance", [2.0, 10.0, 20.0, 80.0])
def test_finite_ron_power_maximum_and_two_roots_against_matrix_exponential(resistance):
    cycle, maximum_time, maximum_power = _constant_ron_time_oracle(resistance)
    target = .95*maximum_power
    low_time = brentq(lambda t: cycle(t)[0]-target, 1e-14, maximum_time, xtol=1e-17)
    high_time = brentq(lambda t: cycle(t)[0]-target, maximum_time, 50*200e-6/resistance, xtol=1e-17)
    # Long high-current roots need sufficient Cout for the declared stiff-Vo
    # approximation; a small capacitor legitimately crosses zero otherwise.
    spec = inputs(iout_a=target/12, capacitance_f=5e-3)
    results = []
    for policy, time in (("lower_peak", low_time), ("higher_peak", high_time)):
        result = calculate_qr_dynamic(spec, selected(on_resistance_ohm=resistance, resistive_root_policy=policy))
        _, reference_period, reference_peak = cycle(time)
        assert result.metrics["turn_off_magnetizing_current_a"] == pytest.approx(reference_peak, rel=2e-8)
        assert result.metrics["actual_switching_frequency_hz"] == pytest.approx(1/reference_period, rel=2e-8)
        assert result.metrics["controller_maximum_output_power_w"] == pytest.approx(maximum_power, rel=2e-8)
        assert result.provenance["resistive_root_policy"] == policy
        assert result.provenance["resistive_search_domain"] == "asymptotic_equilibrium"
        assert abs(result.diagnostics["modeled_power_balance_w"]) < 2e-8
        results.append(result)
    assert results[0].metrics["turn_off_magnetizing_current_a"] < results[1].metrics["turn_off_magnetizing_current_a"]
    assert results[0].metrics["actual_switching_frequency_hz"] > results[1].metrics["actual_switching_frequency_hz"]


def test_finite_ron_tangent_maximum_and_above_maximum_are_distinguished():
    cycle, maximum_time, maximum_power = _constant_ron_time_oracle(20)
    tangent = []
    for policy in ("lower_peak", "higher_peak"):
        result = calculate_qr_dynamic(inputs(iout_a=maximum_power/12),
                                      selected(on_resistance_ohm=20, resistive_root_policy=policy))
        tangent.append(result)
        assert result.metrics["turn_off_magnetizing_current_a"] == pytest.approx(cycle(maximum_time)[2], rel=2e-6)
        assert abs(result.diagnostics["output_capacitor_mean_current_a"]) < 1e-9
    assert tangent[0].metrics["turn_off_magnetizing_current_a"] == tangent[1].metrics["turn_off_magnetizing_current_a"]
    with pytest.raises(ModelDomainError, match="controller maximum output power"):
        calculate_qr_dynamic(inputs(iout_a=maximum_power*1.000001/12), selected(on_resistance_ohm=20))


def test_frequency_ceiling_never_silently_changes_resistive_root():
    cycle, maximum_time, maximum_power = _constant_ron_time_oracle(20)
    target = .95*maximum_power
    low = brentq(lambda t: cycle(t)[0]-target, 1e-14, maximum_time, xtol=1e-17)
    high = brentq(lambda t: cycle(t)[0]-target, maximum_time, 50*200e-6/20, xtol=1e-17)
    ceiling = .5*(1/cycle(low)[1]+1/cycle(high)[1])
    spec = inputs(iout_a=target/12, fsw_hz=ceiling)
    with pytest.raises(ModelDomainError, match="exceeds ceiling"):
        calculate_qr_dynamic(spec, selected(on_resistance_ohm=20, resistive_root_policy="lower_peak"))
    accepted = calculate_qr_dynamic(spec, selected(on_resistance_ohm=20, resistive_root_policy="higher_peak"))
    assert accepted.metrics["actual_switching_frequency_hz"] < ceiling
    first_feasible = calculate_qr_dynamic(inputs(fsw_hz=100e3), QROptions(on_resistance_ohm=2, maximum_valley_index=3))
    assert first_feasible.metrics["valley_index"] == 2
    assert first_feasible.provenance["resistive_root_policy"] == "lower_peak"


def test_low_ron_remains_valid_and_unresolved_asymptotic_high_root_is_explicit():
    result = calculate_qr_dynamic(inputs(), selected(on_resistance_ohm=.1))
    assert result.metrics["actual_switching_frequency_hz"] == pytest.approx(140883.7501434323, rel=1e-8)
    assert abs(result.diagnostics["modeled_power_balance_w"]) < 1e-8
    with pytest.raises(ModelDomainError, match="asymptotic on-time"):
        calculate_qr_dynamic(inputs(), selected(on_resistance_ohm=.1, resistive_root_policy="higher_peak"))


def test_underdamped_first_rise_can_cross_equilibrium_current():
    # Independent exp(A t) solution: the actual first current maximum is
    # 0.3300419336 A, while Vin/Ron is only 0.2 A. A reachable 0.28074769 A
    # cutoff must therefore not be rejected as above the equilibrium value.
    spec = inputs(400, 4, iout_a=2.834002656924/12)
    result = calculate_qr_dynamic(spec, selected(on_resistance_ohm=2000))
    assert result.metrics["turn_off_magnetizing_current_a"] == pytest.approx(.280747691642, rel=2e-8)
    assert result.metrics["turn_off_magnetizing_current_a"] > result.metrics["resistive_equilibrium_current_a"]
    assert result.metrics["actual_switching_frequency_hz"] == pytest.approx(1/3.02872637803e-6, rel=2e-8)
    assert result.provenance["resistive_search_domain"] == "finite_current_crest"
    assert result.stress["Q1"]["voltage_min_v"] >= -1e-8
    assert result.stress["Q1"]["voltage_max_v"] <= 448+1e-8
    assert abs(result.diagnostics["modeled_power_balance_w"]) < 1e-8


def test_resistive_orbit_numerical_failure_is_not_a_load_domain_rejection(monkeypatch):
    from types import SimpleNamespace

    from power_stage_design import qr_dynamics
    actual = qr_dynamics.solve_ivp
    def fail_resistive(*args, **kwargs):
        if kwargs.get("method") == "Radau":
            return SimpleNamespace(success=False, message="injected integration failure")
        return actual(*args, **kwargs)
    monkeypatch.setattr(qr_dynamics, "solve_ivp", fail_resistive)
    with pytest.raises(ModelDomainError, match="resistive turn-on integration failed: injected"):
        calculate_qr_dynamic(inputs(), selected(on_resistance_ohm=20))


def test_resistive_root_policy_is_validated_hashed_and_restored():
    with pytest.raises(ModelDomainError, match="resistive_root_policy"):
        QROptions(on_resistance_ohm=20, resistive_root_policy="automatic")
    with pytest.raises(ModelDomainError, match="requires finite"):
        QROptions(resistive_root_policy="higher_peak")
    lower = inputs(qr_options=selected(on_resistance_ohm=20))
    higher = replace(lower, qr_options=replace(lower.qr_options, resistive_root_policy="higher_peak"))
    restored = DesignInputs(**json.loads(json.dumps(asdict(higher))))
    assert restored.qr_options.resistive_root_policy == "higher_peak"
    assert restored.design_id == higher.design_id
    assert higher.design_id != lower.design_id


def test_failed_power_maximum_search_is_reported_as_numerical_failure(monkeypatch):
    from types import SimpleNamespace

    from power_stage_design import qr_dynamics
    monkeypatch.setattr(qr_dynamics, "minimize_scalar", lambda *args, **kwargs: SimpleNamespace(success=False))
    with pytest.raises(ModelDomainError, match="maximum search did not converge"):
        calculate_qr_dynamic(inputs(), selected(on_resistance_ohm=20))


def test_underdamped_finite_current_crest_is_an_included_high_root_endpoint():
    vin, reflected, resistance, lm, capacitance = 400.0, 48.0, 2000.0, 200e-6, 500e-12
    matrix = np.array([[0, -1/lm], [1/capacitance, -1/(resistance*capacitance)]])
    equilibrium = np.array([vin/resistance, vin])
    initial = np.array([0.0, vin-reflected])
    def state(time):
        return equilibrium+expm(matrix*time)@(initial-equilibrium)
    half_ring = math.pi/math.sqrt(1/(lm*capacitance)-1/(2*resistance*capacitance)**2)
    crest_time = brentq(lambda t: state(t)[1]-vin, 1e-12, half_ring, xtol=1e-18)
    peak = state(crest_time)[0]
    impedance = math.sqrt(lm/capacitance)
    omega = 1/math.sqrt(lm*capacitance)
    charging = math.asin(reflected/(impedance*peak))/omega
    transferred = math.sqrt(peak**2-(reflected/impedance)**2)
    period = crest_time+charging+lm*transferred/reflected+math.pi/omega
    power = .5*lm*transferred**2/period
    result = calculate_qr_dynamic(inputs(400, 4, iout_a=power/12),
                                  selected(on_resistance_ohm=resistance, resistive_root_policy="higher_peak"))
    assert result.metrics["turn_off_magnetizing_current_a"] == pytest.approx(peak, rel=2e-8)
    assert result.metrics["actual_switching_frequency_hz"] == pytest.approx(1/period, rel=2e-8)
    assert abs(result.diagnostics["output_capacitor_mean_current_a"]) < 1e-9

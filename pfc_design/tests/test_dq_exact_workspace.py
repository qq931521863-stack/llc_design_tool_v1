"""Independent recurrence, lifted-state, source/session and delivery contracts."""

import json
import subprocess
import sys
from dataclasses import replace
from threading import Event

import numpy as np
import pytest
from scipy import signal

from pfc_design.three_phase.control import build_three_phase_loops, request_identity
from pfc_design.three_phase.delivery import export_bundle
from pfc_design.three_phase.exact_control import (
    DF2T,
    ExactController,
    SampledSense,
    controller_for,
)
from pfc_design.three_phase.model import ThreePhaseConfig, simulate_three_phase
from pfc_design.three_phase.workspace import (
    apply_candidate,
    design_candidate,
    load_session,
    save_session,
    scan_operating_points,
)


def test_default_recurrences_preserve_original_physical_update_order():
    cfg = ThreePhaseConfig()
    rng = np.random.default_rng(5)
    errors = rng.normal(size=400)
    ci, cv = controller_for(cfg, "current"), controller_for(cfg, "voltage")
    assert ci.b == pytest.approx([3.77, -3.74485], abs=1e-15)
    assert cv.b == pytest.approx([0.51, -0.5], abs=1e-15)
    for loop, c, kp, ki, ts in (
        ("current", ci, cfg.current_kp, cfg.current_ki, 1 / cfg.control_hz),
        ("voltage", cv, cfg.voltage_kp, cfg.voltage_ki, 1 / cfg.voltage_loop_hz),
    ):
        integ = 0.0
        expected = []
        for error in errors:
            if loop == "voltage":
                integ += ki * ts * error
            expected.append(kp * error + integ)
            if loop == "current":
                integ += ki * ts * error
        actual = signal.lfilter(c.b, c.a, errors)
        np.testing.assert_allclose(actual, expected, rtol=1e-12, atol=2e-13)


@pytest.mark.parametrize(
    "b,a",
    [
        ((0.0, 0.2, 0.1), (1.0, -0.7, 0.1)),
        ((0.4, 0.2, 0.1, 0.05), (1.0,)),
        ((2.0,), (1.0,)),
        ((1.0, -1.0), (1.0, -0.8)),
    ],
)
def test_arbitrary_canonical_df2t_matches_independent_scipy(b, a):
    c = ExactController(b, a, 20000.0)
    values = np.r_[
        1.0, np.zeros(20), np.ones(30), np.random.default_rng(81).normal(size=200)
    ]
    runtime = DF2T(c)
    actual = [runtime.advance(x) for x in values]
    np.testing.assert_allclose(
        actual, signal.lfilter(b, a, values), rtol=2e-13, atol=2e-13
    )


def test_actual_imported_controller_drives_nonlinear_runtime():
    cfg = ThreePhaseConfig(duration_s=0.08, output_power_w=500.0)
    native = controller_for(cfg, "current")
    exact = ExactController(
        tuple(np.convolve(native.b, [0.4])),
        tuple(np.convolve(native.a, [1.0, -0.6])),
        cfg.control_hz,
        "independent general order2",
    )
    cfg = replace(cfg, current_controller=exact)
    result = simulate_three_phase(cfg)
    # Independent scipy DF2T consumes actual logged errors and external tracking correction.
    for axis in ("d", "q"):
        state = np.zeros(2)
        outputs = []
        for error, correction in zip(
            result.signals[f"current_error_{axis}_a"],
            result.signals[f"current_tracking_{axis}_v"],
        ):
            y, state = signal.lfilter(exact.b, exact.a, [error], zi=state)
            outputs.append(y[0])
            state[0] += correction
        np.testing.assert_allclose(
            result.signals[f"controller_{axis}_v"], outputs, rtol=1e-12, atol=1e-11
        )
    assert result.metrics["source_identity"] == request_identity(cfg)
    loops = build_three_phase_loops(cfg)
    assert loops["current"].controller_b.tolist() == list(exact.b)
    assert loops["current"].controller_a.tolist() == list(exact.a)


def test_independent_closed_inner_lifted_equations_include_energy_delay_and_filter():
    cfg = replace(
        ThreePhaseConfig(),
        current_sense=SampledSense(pole_hz=4500, delay_ticks=1),
        pwm_delay_ticks=2,
        voltage_sense=SampledSense(pole_hz=200, delay_ticks=1),
        voltage_delay_ticks=1,
    )
    loops = build_three_phase_loops(cfg)
    outer = loops["voltage"]
    vd = cfg.line_line_rms_v * np.sqrt(2 / 3)
    i0 = loops["operating_id_a"]
    cv = cfg.bus_capacitance_f * cfg.bus_voltage_v
    ac = np.array(
        [
            [-cfg.resistance_ohm / cfg.inductance_h, 0.0],
            [1.5 * (vd - cfg.resistance_ohm * i0) / cv, 0.0],
        ]
    )
    bc = np.array([[1 / cfg.inductance_h], [-1.5 * i0 / cv]])
    a, b, _, _, _ = signal.cont2discrete(
        (ac, bc, np.eye(2), np.zeros((2, 1))), 1 / cfg.control_hz
    )
    ci = controller_for(cfg, "current")
    sense = cfg.current_sense.controller(cfg.control_hz)
    n = 2 + ci.order + sense.order + cfg.pwm_delay_ticks
    rng = np.random.default_rng(19)
    x = rng.normal(size=n)
    reference = 0.7
    original = x.copy()
    for _ in range(round(cfg.control_hz / cfg.voltage_loop_hz)):
        current, zs = signal.lfilter(
            sense.b, sense.a, [x[0]], zi=x[2 + ci.order : 2 + ci.order + sense.order]
        )
        command, zc = signal.lfilter(
            ci.b, ci.a, [reference - current[0]], zi=x[2 : 2 + ci.order]
        )
        plant = a @ x[:2] + b[:, 0] * x[-cfg.pwm_delay_ticks]
        x = np.r_[plant, zc, zs, x[-cfg.pwm_delay_ticks + 1 :], command[0]]
    expected = outer.plant_state_a @ original + outer.plant_state_b * reference
    np.testing.assert_allclose(x, expected, rtol=2e-12, atol=2e-12)
    # Complete-state eigenvalues, not roots of an ill-conditioned lifted TF.
    np.testing.assert_allclose(
        np.sort_complex(outer.poles),
        np.sort_complex(np.linalg.eigvals(outer.closed_state_a)),
    )


def test_sense_command_budget_valid_grid_and_distinct_ownership():
    cfg = ThreePhaseConfig()
    base = build_three_phase_loops(cfg)
    delayed = build_three_phase_loops(replace(cfg, voltage_delay_ticks=1))
    f = base["voltage"].frequency_hz
    np.testing.assert_allclose(
        delayed["voltage"].open_response / base["voltage"].open_response,
        np.exp(-2j * np.pi * f / cfg.voltage_loop_hz),
        rtol=2e-12,
    )
    np.testing.assert_array_equal(
        base["current"].open_response, delayed["current"].open_response
    )
    for key in ("current", "voltage"):
        loop = base[key]
        assert loop.frequency_hz[-1] < 0.5 / loop.sample_time_s
        index = 345
        budget = loop.phase_budget(float(loop.frequency_hz[index]))
        assert complex(*budget["return_ratio"]) == pytest.approx(
            loop.open_response[index], rel=3e-12
        )
        assert sum(b["gain_db"] for b in budget["blocks"].values()) == pytest.approx(
            budget["gain_db"], abs=1e-10
        )
        with pytest.raises(ValueError):
            loop.phase_budget(0.5 / loop.sample_time_s)


def test_all_signed_margins_and_actual_unstable_poles_remain_visible():
    loops = build_three_phase_loops(replace(ThreePhaseConfig(), current_kp=40))
    loop = loops["current"]
    assert not loop.stable
    assert max(abs(loop.poles)) > 1
    assert min(loop.summary()["signed_gain_margins_db"]) < 0
    missing = build_three_phase_loops(replace(ThreePhaseConfig(), analysis_max_hz=0.1))[
        "current"
    ]
    assert missing.summary()["minimum_signed_gain_margin_db"] is None
    assert missing.margins.critical_gain_crossover_hz is None


def test_shared_candidate_apply_and_changed_inner_rejection():
    cfg = ThreePhaseConfig()
    candidate = design_candidate(cfg, "current", 1000, 60)
    assert candidate.applicable
    applied = apply_candidate(cfg, candidate)
    assert applied.current_controller == candidate.controller
    loops = build_three_phase_loops(applied)
    assert loops["current"].controller_b.tolist() == list(candidate.controller.b)
    with pytest.raises(ValueError, match="stale"):
        apply_candidate(replace(cfg, current_ki=cfg.current_ki + 1), candidate)
    unreachable = design_candidate(cfg, "voltage", 900, 170)
    assert not unreachable.applicable
    with pytest.raises(ValueError):
        apply_candidate(cfg, unreachable)


def test_retained_operating_points_use_same_controller_and_reject_rate_mismatch():
    cfg = ThreePhaseConfig()
    rows = scan_operating_points(
        cfg,
        [
            replace(cfg, line_line_rms_v=380, output_power_w=5000),
            replace(cfg, control_hz=40000),
        ],
    )
    assert rows[0]["status"] == "ANALYZED"
    assert rows[0]["current"]["controller_b"] == list(controller_for(cfg, "current").b)
    assert (
        rows[1]["status"] == "INVALID" and "sample rate mismatch" in rows[1]["message"]
    )


def test_session_exact_roundtrip_and_new_process_replay(tmp_path):
    cfg = ThreePhaseConfig()
    exact = ExactController(
        (0.0, 1.2345678901234567, -1.2),
        (1.0, -1.0),
        cfg.control_hz,
        "leading zero identity",
    )
    cfg = replace(
        cfg,
        current_controller=exact,
        current_sense=SampledSense(pole_hz=7000, delay_ticks=1),
    )
    corner = replace(cfg, output_power_w=6000, line_line_rms_v=380)
    path = tmp_path / "session.json"
    save_session(path, cfg, [corner], {"loop": "current", "fc_hz": 500, "pm_deg": 60})
    restored, retained, targets = load_session(path)
    assert restored == cfg and retained == (corner,)
    assert targets["fc_hz"] == 500
    code = "from pfc_design.three_phase.workspace import load_session; from pfc_design.three_phase.control import request_identity; import sys; print(request_identity(load_session(sys.argv[1])[0]))"
    result = subprocess.run(
        [sys.executable, "-c", code, str(path)],
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.strip() == request_identity(cfg)
    data = json.loads(path.read_text())
    data["config"]["current_controller"]["b"][0] = 0.001
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="identity"):
        load_session(path)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda x: x["config"].update(unknown_field=1),
        lambda x: x["config"].update(control_hz=float("nan")),
        lambda x: x["config"].update(pwm_delay_ticks=1.2),
        lambda x: x["config"].update(
            current_controller={"b": [1], "a": [1], "sample_rate_hz": 100}
        ),
        lambda x: x["config"].update(voltage_sense={"gain": 1, "unknown": 3}),
    ],
)
def test_session_rejects_unknown_nonfinite_and_mismatched_data(tmp_path, mutation):
    path = tmp_path / "session.json"
    save_session(path, ThreePhaseConfig())
    data = json.loads(path.read_text())
    mutation(data)
    path.write_text(json.dumps(data))
    with pytest.raises((ValueError, TypeError)):
        load_session(path)


def test_adc_unknown_overrange_and_no_silent_modification():
    cfg = replace(ThreePhaseConfig(), voltage_sense=SampledSense(adc_v_per_unit=0.01))
    loops = build_three_phase_loops(cfg)
    assert loops["adc_headroom"]["current"]["status"] == "UNKNOWN"
    assert loops["adc_headroom"]["voltage"]["status"] == "OVERRANGE"
    assert cfg.voltage_sense.adc_v_per_unit == 0.01
    assert not design_candidate(cfg, "current", 1000, 60).applicable


def test_verified_c99_and_actual_standalone_research_delivery(tmp_path):
    cfg = ThreePhaseConfig()
    loops = build_three_phase_loops(cfg)
    result = export_bundle(tmp_path / "delivery", cfg, loops)
    assert result["c99"]["status"] == "PASS"
    assert result["c99"]["maximum_absolute_error"] < 1e-12
    report = json.loads((tmp_path / "delivery" / "report.json").read_text())
    assert report["source_identity"] == request_identity(cfg)
    assert report["matlab_verification"] == "NOT_RUN"
    assert "PASS" in (tmp_path / "delivery" / "python-verification.txt").read_text()
    with pytest.raises(ValueError, match="stale"):
        export_bundle(tmp_path / "stale", replace(cfg, current_kp=4), loops)


def test_cancellation_and_nonzero_iq_model_boundary():
    cancel = Event()
    cancel.set()
    with pytest.raises(InterruptedError):
        simulate_three_phase(ThreePhaseConfig(), cancel_event=cancel)
    with pytest.raises(ValueError, match="Iq=0"):
        build_three_phase_loops(replace(ThreePhaseConfig(), iq_reference_a=5))


def test_sense_gain_moves_physical_regulated_operating_point_and_load_slope():
    cfg = replace(
        ThreePhaseConfig(),
        voltage_sense=SampledSense(gain=1.1),
        current_sense=SampledSense(gain=0.95),
        load_model="resistance",
    )
    analysis = build_three_phase_loops(cfg)
    assert analysis["operating_bus_v"] == pytest.approx(700 / 1.1)
    assert analysis["operating_power_w"] == pytest.approx(10000 / 1.1**2)
    assert analysis["operating_current_reference_a"] == pytest.approx(
        0.95 * analysis["operating_id_a"]
    )
    with pytest.raises(ValueError, match="SVPWM headroom"):
        build_three_phase_loops(replace(cfg, voltage_sense=SampledSense(gain=2)))


def test_explicit_load_override_changes_effective_source_identity():
    cfg = ThreePhaseConfig()
    default = build_three_phase_loops(cfg)
    override = build_three_phase_loops(cfg, load_model="resistance")
    assert override["source_identity"] != default["source_identity"]
    assert override["source_identity"] == request_identity(
        replace(cfg, load_model="resistance")
    )
    assert not np.allclose(
        override["voltage"].open_response, default["voltage"].open_response
    )


def test_current_candidate_cannot_hide_an_unstable_fixed_outer_loop():
    cfg = replace(ThreePhaseConfig(), voltage_kp=100.0)
    assert not build_three_phase_loops(cfg)["voltage"].stable
    candidate = design_candidate(cfg, "current", 1000, 60)
    assert not candidate.applicable
    assert not candidate.nominal["both_loop_stability"]["voltage"]["stable"]


def test_retained_corner_adc_overrange_prevents_candidate_apply():
    cfg = replace(ThreePhaseConfig(), voltage_sense=SampledSense(adc_v_per_unit=0.004))
    corner = replace(cfg, bus_voltage_v=900.0)
    assert (
        build_three_phase_loops(cfg)["adc_headroom"]["voltage"]["status"] == "IN_RANGE"
    )
    candidate = design_candidate(cfg, "current", 1000.0, 60.0, [corner])
    assert candidate.corners[0]["adc_headroom"]["voltage"]["status"] == "OVERRANGE"
    assert not candidate.applicable


def test_adc_envelope_uses_gain_adjusted_physical_current_limit():
    cfg = replace(
        ThreePhaseConfig(),
        current_sense=SampledSense(gain=0.5, adc_v_per_unit=0.02, adc_bias_v=1.65),
    )
    room = build_three_phase_loops(cfg)["adc_headroom"]["current"]
    assert room["status"] == "OVERRANGE"
    assert room["adc_max_v"] == pytest.approx(1.65 + 0.02 * 120)


def test_nonunity_sense_runtime_settling_and_error_domains_are_consistent():
    cfg = replace(
        ThreePhaseConfig(),
        duration_s=0.4,
        current_sense=SampledSense(gain=0.95),
        voltage_sense=SampledSense(gain=1.05),
    )
    result = simulate_three_phase(cfg)
    assert result.metrics["settled"]
    assert result.metrics["vbus_regulated_target_v"] == pytest.approx(700 / 1.05)
    assert result.metrics["vbus_average_v"] == pytest.approx(700 / 1.05, abs=0.1)
    assert result.metrics["id_error_rms_a"] < 0.05
    np.testing.assert_allclose(
        result.signals["id_feedback_a"], 0.95 * result.signals["id_a"], atol=1e-12
    )

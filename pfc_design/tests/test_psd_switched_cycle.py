"""A04 switching producer: live circuit branch/KCL/load evidence."""

from dataclasses import replace

import numpy as np
import pytest

from pfc_design.control import PFCControlLabConfig, build_pfc_control_lab_analysis
from pfc_design.control.config import LoadModel
from power_sim.spice import TTPLSharedNgSpiceConfig, run_ttpl_shared_closed_loop


def _require_shared_engine():
    from power_sim.spice import find_ngspice_shared_library

    if find_ngspice_shared_library() is None:
        pytest.skip("shared libngspice is not installed")


@pytest.mark.parametrize(
    "load_model",
    [LoadModel.CONSTANT_POWER, LoadModel.RESISTIVE, "constant_power", "resistive"],
)
def test_real_switching_producer_load_branch_currents_and_kcl(load_model):
    _require_shared_engine()
    cfg = PFCControlLabConfig()
    cfg = replace(cfg, power_stage=replace(cfg.power_stage, load_model=load_model))
    result = run_ttpl_shared_closed_loop(
        build_pfc_control_lab_analysis(cfg),
        simulation=TTPLSharedNgSpiceConfig(
            duration_s=140e-6,
            input_phase_deg=90,
            output_step_s=0.2e-6,
            max_step_s=0.08e-6,
            wall_timeout_s=30,
        ),
    )
    s = result.vectors
    assert result.config is cfg
    assert result.metadata["load_model"] == LoadModel(load_model).value
    for name in (
        "i(vshfh)",
        "i(vshfl)",
        "i(vslfp)",
        "i(vslfn)",
        "i(vdhfh)",
        "i(vdhfl)",
        "i(vdlfp)",
        "i(vdlfn)",
        "i(vscap)",
        "i(vsload)",
    ):
        assert name in s
        assert len(s[name]) == len(s["time"])
        assert np.all(np.isfinite(s[name]))
    bus = s["v(bus)"]
    load = s["i(vsload)"]
    if load_model == LoadModel.CONSTANT_POWER:
        assert load * np.maximum(bus, 25) == pytest.approx(
            cfg.power_stage.output_power_w, rel=1e-7
        )
    else:
        assert load == pytest.approx(
            bus * cfg.power_stage.output_power_w / cfg.power_stage.bus_voltage_v**2,
            rel=1e-7,
        )
    incoming = -s["i(vshfh)"] - s["i(vslfp)"] + s["i(vdhfh)"] + s["i(vdlfp)"]
    assert incoming == pytest.approx(s["i(vscap)"] + load, abs=1e-6, rel=3e-4)
    sw = s["v(sw)"]
    assert (s["i(lboost)"] + s["i(vshfh)"] + s["i(vdhfl)"]) == pytest.approx(
        s["i(vshfl)"] + s["i(vdhfh)"] + sw / 1e7, abs=1e-6, rel=3e-4
    )
    assert np.max(s["i(vdhfh)"]) > 0.1


def test_signed_current_feedback_keeps_regenerative_direction():
    from power_sim.spice.pfc_closed_loop import rectified_current_feedback

    # In positive-half PWM, IL=-5 A is reverse flow. The current controller
    # must increase duty to recover it, not treat it as +5 A forward current.
    assert rectified_current_feedback(-5.0, 1) == -5.0
    assert rectified_current_feedback(5.0, -1) == -5.0
    assert rectified_current_feedback(-5.0, -1) == 5.0


def _native_mean(t, values):
    # Independent endpoint weights, not the production window/integrator.
    delta = np.diff(t)
    weights = np.r_[delta[0] / 2, (delta[:-1] + delta[1:]) / 2, delta[-1] / 2]
    return float(weights @ values / (t[-1] - t[0]))


@pytest.fixture(scope="module")
def small_source():
    _require_shared_engine()
    from pfc_design.engineering.switched_cycle import observe_switched_cycle

    cfg = PFCControlLabConfig()
    raw = run_ttpl_shared_closed_loop(
        build_pfc_control_lab_analysis(cfg),
        simulation=TTPLSharedNgSpiceConfig(
            duration_s=140e-6,
            input_phase_deg=90,
            output_step_s=0.2e-6,
            max_step_s=0.08e-6,
            wall_timeout_s=30,
        ),
    )
    return raw, observe_switched_cycle(raw)


def test_native_branch_moments_and_device_power_agree_independently(small_source):
    from pfc_design.engineering import PFCDeviceDatabase
    from pfc_design.engineering.switched_loss import _physical

    raw, source = small_source
    t, v = raw.vectors["time"], raw.vectors
    assert not source.accepted
    assert source.capacitor_current_rms_a**2 == pytest.approx(
        _native_mean(t, v["i(vscap)"] ** 2), rel=1e-12
    )
    esr_power = _native_mean(t, (v["v(bus)"] - v["v(c_int)"]) * v["i(vscap)"])
    assert source.capacitor_current_rms_a**2 * 0.035 == pytest.approx(
        esr_power, rel=1e-9
    )
    device = PFCDeviceDatabase().all[0]
    for p in ("hfh", "hfl", "lfp", "lfn"):
        result = _physical(
            source, device, p, 100.0
        )  # math unit test; no acceptance claim
        assert result.conduction_w == pytest.approx(
            _native_mean(t, v[f"i(vs{p})"] ** 2) * result.rds_hot_ohm, rel=1e-12
        )
        native_voltage = {
            "hfh": v["v(sw)"] - v["v(bus)"],
            "hfl": -v["v(sw)"],
            "lfp": v["v(neutral)"] - v["v(bus)"],
            "lfn": -v["v(neutral)"],
        }[p]
        assert result.diode_conduction_w == pytest.approx(
            _native_mean(t, native_voltage * v[f"i(vd{p})"]), rel=1e-12
        )
        assert result.total_w == pytest.approx(
            sum(
                (
                    result.conduction_w,
                    result.diode_conduction_w,
                    result.switching_w,
                    result.coss_w,
                    result.gate_drive_w,
                )
            )
        )
    moments = source.capacitor_pwm_moments()
    assert moments["total_mean_square_a2"] == pytest.approx(
        moments["pwm_interval_mean_square_a2"] + moments["within_pwm_variance_a2"]
    )


def test_incomplete_source_is_rejected_by_all_public_consumers(small_source):
    from pfc_design.engineering import (
        CapacitorUnitSpec,
        PFCDeviceDatabase,
        TTPLDesignSpec,
        analyze_ttpl_design,
        compare_ttpl_devices,
        design_bus_capacitor_bank,
        solve_ttpl_semiconductor_thermal,
    )

    _, source = small_source
    db = PFCDeviceDatabase()
    design = analyze_ttpl_design(TTPLDesignSpec())
    for call in (
        lambda: compare_ttpl_devices(design, db, switched_cycle=source),
        lambda: design_bus_capacitor_bank(
            design, CapacitorUnitSpec(), switched_cycle=source
        ),
        lambda: solve_ttpl_semiconductor_thermal(
            design, db.all[0], db.all[0], switched_cycle=source
        ),
    ):
        with pytest.raises(ValueError, match="converged, complete"):
            call()


def test_source_identity_rejects_stale_metrics_metadata_and_qualification(small_source):
    import copy

    _, source = small_source
    variants = [
        replace(source, capacitor_current_rms_a=source.capacitor_current_rms_a + 1)
    ]
    metadata = copy.deepcopy(source.source_metadata)
    metadata["current_b"] = [9.0, 9.0]
    variants.append(replace(source, source_metadata=metadata))
    signals = dict(source.qualification_signals)
    signals["i(vscap)"] = signals["i(vscap)"].copy() + 1
    variants.append(replace(source, qualification_signals=signals))
    for altered in variants:
        with pytest.raises(ValueError, match="identity changed"):
            altered.require_accepted()


def test_native_power_boundary_and_duplicate_event_limits():
    from pfc_design.engineering.switched_cycle import _window, time_mean

    t = np.array([0.0, 1.0, 1.0, 2.0])
    current = np.array([10.0, 10.0, 0.0, 0.0])
    voltage = np.array([1.0, 1.0, -400.0, -400.0])
    tt, vv = _window(t, {"power": current * voltage}, 0.25, 1.75)
    # Event has distinct left/right limits at the identical timestamp.
    assert list(tt) == [0.25, 1.0, 1.0, 1.75]
    assert list(vv["power"]) == [10.0, 10.0, 0.0, 0.0]
    assert time_mean(tt, vv["power"]) == pytest.approx(5.0)
    tt, vv = _window(np.array([0.0, 1.0]), {"square": np.array([100.0, 0.0])}, 0.5, 1.0)
    assert time_mean(tt, vv["square"]) == pytest.approx(
        25.0
    )  # square native, then clip


def test_eon_only_does_not_double_count_coss(small_source):
    from pfc_design.engineering import PFCDeviceDatabase
    from pfc_design.engineering.switched_loss import _physical

    _, source = small_source
    device = replace(
        PFCDeviceDatabase().all[0],
        eon_ref_uj=100.0,
        eoff_ref_uj=0.0,
        e_ref_v=400.0,
        e_ref_i=20.0,
    )
    assert _physical(source, device, "hfh", 100.0).coss_w == 0.0


def test_timestep_and_event_refinement_preserves_native_moments():
    _require_shared_engine()
    records = []
    cfg = PFCControlLabConfig()
    for step, guard in ((80e-9, 1e-9), (40e-9, 0.5e-9), (20e-9, 0.25e-9)):
        r = run_ttpl_shared_closed_loop(
            build_pfc_control_lab_analysis(cfg),
            simulation=TTPLSharedNgSpiceConfig(
                duration_s=140e-6,
                input_phase_deg=90,
                output_step_s=0.2e-6,
                max_step_s=step,
                event_guard_s=guard,
                wall_timeout_s=30,
            ),
        )
        t, v = r.vectors["time"], r.vectors
        for p in ("hfh", "hfl", "lfp", "lfn"):
            assert np.min(v[f"i(vd{p})"]) > -1e-6
        records.append(
            (
                _native_mean(t, v["i(vscap)"] ** 2),
                _native_mean(t, (v["v(sw)"] - v["v(bus)"]) * v["i(vdhfh)"]),
            )
        )
    records = np.array(records)
    assert np.ptp(records[:, 0]) / records[-1, 0] < 0.002
    assert np.ptp(records[:, 1]) / records[-1, 1] < 0.02


@pytest.mark.parametrize("direction", [-1, 0, 1])
def test_stop_time_roundtrips_control_tick_plus_minus_one_ulp(direction):
    _require_shared_engine()
    stop = 0.14e-3
    if direction:
        stop = float(np.nextafter(stop, np.inf if direction > 0 else -np.inf))
    r = run_ttpl_shared_closed_loop(
        build_pfc_control_lab_analysis(PFCControlLabConfig()),
        simulation=TTPLSharedNgSpiceConfig(
            duration_s=stop, input_phase_deg=90, wall_timeout_s=30
        ),
    )
    assert r.metadata["native_stop_token"] in r.circuit_netlist
    assert r.vectors["time"][-1] == r.metadata["native_stop_time_s"]
    assert all(
        sample.time_s <= r.metadata["native_stop_time_s"] for sample in r.samples
    )
    assert r.vectors["time"][-1] <= stop
    assert stop - r.vectors["time"][-1] <= np.spacing(stop)
    assert not any(
        "aborted" in m.lower() or "timestep too small" in m.lower()
        for m in r.simulator_messages
    )


def test_accepted_time_cannot_silently_discard_a_real_rewind():
    from power_sim.spice.pfc_closed_loop import validate_accepted_time

    validate_accepted_time(0.001, 0.001)
    validate_accepted_time(np.nextafter(0.001, np.inf), 0.001)
    with pytest.raises(ValueError, match="backwards"):
        validate_accepted_time(0.001 - 1e-8, 0.001)
    with pytest.raises(ValueError, match="nonfinite"):
        validate_accepted_time(float("nan"), 0.001)


@pytest.mark.parametrize(
    "fault", ["diode", "config", "missing", "nan", "truncated", "re_discretized"]
)
def test_invalid_native_source_never_reaches_consumers(small_source, fault):
    import copy
    from dataclasses import asdict

    from pfc_design.engineering.switched_cycle import observe_switched_cycle

    raw, _ = small_source
    vectors = {k: v.copy() for k, v in raw.vectors.items()}
    metadata = copy.deepcopy(raw.metadata)
    if fault == "diode":
        vectors["i(vdhfh)"][len(vectors["time"]) // 2] = -4920.0
    elif fault == "config":
        metadata["source_config"]["power_stage"]["vin_rms_v"] += 1.0
    elif fault == "missing":
        del vectors["i(vscap)"]
    elif fault == "nan":
        vectors["i(vshfl)"][4] = np.nan
    elif fault == "truncated":
        vectors = {k: v[:-100] for k, v in vectors.items()}
    else:
        metadata["controller_re_discretized"] = True
    bad = replace(raw, vectors=vectors, metadata=metadata)
    if fault == "diode":
        observed = observe_switched_cycle(bad)
        assert observed.convergence["status"] == "INCONSISTENT_DEVICE_LAW"
        assert observed.convergence["diode_constitutive_invalid_samples"]["hfh"] > 0
        with pytest.raises(ValueError):
            observed.require_accepted()
    else:
        with pytest.raises(ValueError):
            observe_switched_cycle(bad)
    assert raw.metadata["source_config"] == asdict(raw.config)


@pytest.mark.parametrize("direction", [-1, 1])
def test_observer_rejects_one_ulp_actual_horizon_mismatch(small_source, direction):
    from pfc_design.engineering.switched_cycle import observe_switched_cycle

    raw, _ = small_source
    vectors = dict(raw.vectors)
    vectors["time"] = vectors["time"].copy()
    vectors["time"][-1] = np.nextafter(
        vectors["time"][-1], np.inf if direction > 0 else -np.inf
    )
    with pytest.raises(ValueError, match="duration"):
        observe_switched_cycle(replace(raw, vectors=vectors))


def test_optional_backend_skip_is_based_on_actual_discovery(monkeypatch):
    import power_sim.spice

    monkeypatch.setattr(power_sim.spice, "find_ngspice_shared_library", lambda: None)
    with pytest.raises(
        pytest.skip.Exception, match="shared libngspice is not installed"
    ):
        _require_shared_engine()

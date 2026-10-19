"""Independent branch-current/energy oracles for the A03 consumer boundary.

These test only the declared ideal-waveform semiconductor screen. Expected
conduction is integrated through explicitly switched physical branches, rather
than evaluated with the implementation's aggregate RMS formula.
"""
from dataclasses import replace
from types import SimpleNamespace

import numpy as np
import pytest

from llc_design.analysis.fha import solve_fha
from llc_design.analysis.types import FidelityLevel, LLCAnalysisRequest
from llc_design.core.operating_point import solve_operating_point
from llc_design.core.spec import LLCDesignSpec, PrimaryTopology
from llc_design.core.tank import design_tank
from llc_design.dynamics.waveforms import WaveformBundle, WaveformSignal
from llc_design.models.devices import DeviceDatabase
from llc_design.models.system import LLCSystemAnalyzer
from llc_design.models.waveform_loss import (
    bridge_loss_with_provenance,
    rectifier_loss_with_provenance,
)
from llc_design.report.export import operating_point_rows


def _sample_bundle(frequency=137_000.0):
    n = 4096
    phase = 2 * np.pi * (np.arange(n) + 0.5) / n
    currents = {
        "i_resonant": 12 * np.sin(phase) + 3 * np.sin(3 * phase),
        "i_transformer_secondary": 35 * np.sin(phase) - 7 * np.sin(3 * phase),
    }
    # Additional explicit passive samples make this a complete synthetic source.
    currents["i_output_cap"] = 8 * np.sin(2 * phase)
    currents["i_magnetizing"] = 300 / (2 * np.pi * frequency * design_tank(LLCDesignSpec()).lm_h) * np.sin(phase)
    voltages = {"v_transformer_primary": 300 * np.cos(phase),
        "v_resonant_cap": 120 * np.cos(phase),
        "v_output_cap_internal": 53 - 0.1 * np.cos(2 * phase),
        "v_output": 53 - 0.1 * np.cos(2 * phase) + 0.01 * currents["i_output_cap"]}
    # Intentionally stale scalar caches: only sample evidence is authoritative.
    signals = {key: WaveformSignal(key, key, unit, values, SimpleNamespace(rms=-99.0))
               for unit, data in (("A", currents), ("V", voltages)) for key, values in data.items()}
    return WaveformBundle(np.arange(n) / (n * frequency), frequency,
                          "independent_periodic_fixture", signals), phase


@pytest.mark.parametrize("topology", list(PrimaryTopology))
@pytest.mark.parametrize("parallel", [1, 3])
def test_full_period_device_rms_and_conduction_from_switched_branches(topology, parallel):
    spec = LLCDesignSpec(primary_topology=topology, primary_parallel_devices=parallel,
        primary_turns=15 if topology == PrimaryTopology.HALF_BRIDGE else 30,
        primary_deadtime_s=1e-12, sr_parallel_devices_per_position=2)
    tank = design_tank(spec)
    op = solve_operating_point(spec, tank, spec.vbus_nom_v, 1.0)
    db = DeviceDatabase()
    primary_device = db.get_primary(spec.primary_device)
    sr_device = db.get_sr(spec.sr_device)
    waveform, phase = _sample_bundle()
    primary = bridge_loss_with_provenance(spec, tank, op, primary_device,
                                          waveform=waveform, td_converged=True)
    sr = rectifier_loss_with_provenance(spec, op, sr_device,
                                       waveform=waveform, td_converged=True)
    # Enumerate actual current-carrying branches and every parallel die.
    ir = waveform.signal("i_resonant").values
    first_leg = [np.where(phase < np.pi, ir, 0), np.where(phase >= np.pi, ir, 0)]
    branches = first_leg if topology == PrimaryTopology.HALF_BRIDGE else first_leg + first_leg[::-1]
    physical_currents = [branch / parallel for branch in branches for _ in range(parallel)]
    losses = [np.mean(current * current) * primary_device.rds_at(spec.primary_junction_temperature_c)
              for current in physical_currents]
    assert primary.legacy.conduction_w == pytest.approx(sum(losses), rel=1e-12)
    assert primary.legacy.device_rms_current_a == pytest.approx(
        np.sqrt(np.mean(physical_currents[0] ** 2)), rel=1e-12)
    isec = waveform.signal("i_transformer_secondary").values
    positive = np.where(isec > 0, isec, 0)
    negative = np.where(isec < 0, isec, 0)
    sr_currents = [branch / 2 for branch in (positive, negative, negative, positive) for _ in range(2)]
    assert sr.legacy.conduction_w == pytest.approx(sum(np.mean(i * i) for i in sr_currents)
        * sr_device.rds_at(spec.sr_junction_temperature_c), rel=1e-12)
    assert sr.legacy.device_rms_current_a == pytest.approx(np.sqrt(np.mean(sr_currents[0] ** 2)))


@pytest.mark.parametrize("topology", list(PrimaryTopology))
def test_eoss_count_is_one_event_per_physical_die_per_period(topology):
    spec = LLCDesignSpec(primary_topology=topology,
        primary_turns=15 if topology == PrimaryTopology.HALF_BRIDGE else 30,
        primary_parallel_devices=2, primary_deadtime_s=1e-12)
    tank = design_tank(spec)
    op = replace(solve_operating_point(spec, tank, 400, 1), commutation_current_a=0)
    device = DeviceDatabase().get_primary(spec.primary_device)
    waveform, phase = _sample_bundle()
    gates = [phase < np.pi, phase >= np.pi]
    if topology == PrimaryTopology.FULL_BRIDGE:
        gates += gates[::-1]
    # Count wrapping rising edges over one complete interval, not role labels.
    events = sum(np.count_nonzero(gate & ~np.roll(gate, 1)) for gate in gates) * 2
    event_energy = 0.5 * device.coss_er_f * op.vbus_v ** 2
    result = bridge_loss_with_provenance(spec, tank, op, device,
                                         waveform=waveform, td_converged=True)
    dissipation = sum(term.value_w for term in result.terms
                      if term.name in ("turn_on", "coss_residual"))
    assert dissipation == pytest.approx(events * event_energy * waveform.switching_frequency_hz)
    assert result.total_w == pytest.approx(sum(term.value_w for term in result.terms))


@pytest.fixture(scope="module")
def source_context():
    spec = LLCDesignSpec()
    analyzer = LLCSystemAnalyzer()
    analysis = analyzer.analyze(spec, work_points=[(400.0, 1.0)])
    result = solve_fha(LLCAnalysisRequest(spec, samples_per_cycle=128, waveform_cycles=1))
    waveform, _ = _sample_bundle()
    td = replace(result, fidelity=FidelityLevel.SWITCHED_TIME_DOMAIN, waveform=waveform,
                 metrics=replace(result.metrics, switching_frequency_hz=waveform.switching_frequency_hz))
    return analyzer, analysis, td


def test_actual_frequency_reaches_both_bridges_system_and_report(source_context):
    analyzer, original, td = source_context
    updated = analyzer.with_time_domain_losses(original, [td])
    point = updated.nominal
    spec = original.spec
    devices = DeviceDatabase()
    primary = devices.get_primary(spec.primary_device)
    sr = devices.get_sr(spec.sr_device)
    actual_hz = td.waveform.switching_frequency_hz
    assert actual_hz != pytest.approx(point.operating_point.switching_frequency_hz)
    assert point.primary.gate_drive_w == pytest.approx(
        4 * spec.primary_parallel_devices * primary.qg_c * primary.gate_voltage_v * actual_hz)
    assert point.synchronous_rectifier.gate_drive_w == pytest.approx(
        4 * spec.sr_parallel_devices_per_position * sr.qg_c * sr.gate_voltage_v * actual_hz)
    row = operating_point_rows(updated)[0]
    assert row["semiconductor_frequency_hz"] == actual_hz
    assert row["primary_device_rms_current_a"] == point.primary.device_rms_current_a
    assert row["sr_device_rms_current_a"] == point.synchronous_rectifier.device_rms_current_a
    assert row["total_loss_w"] == pytest.approx(sum(point.breakdown().values()))
    assert point.transformer is not original.nominal.transformer
    assert point.output_capacitor is not original.nominal.output_capacitor
    assert row["passive_frequency_hz"] == actual_hz
    assert row["magnetic_source"] == "TIME_DOMAIN_WAVEFORM_IGSE_LITZ"
    assert row["capacitor_source"] == "TIME_DOMAIN_WAVEFORM_CONSTANT_ESR"


@pytest.mark.parametrize("fault", ["not_periodic", "not_regulated", "wrong_bus", "wrong_load"])
def test_system_refuses_unaccepted_or_wrong_identity_sources(source_context, fault):
    analyzer, original, td = source_context
    if fault == "not_periodic":
        td = replace(td, convergence=replace(td.convergence, converged=False))
    elif fault == "not_regulated":
        td = replace(td, metrics=replace(td.metrics, output_voltage_v=original.spec.vout_v + 0.5))
    elif fault == "wrong_bus":
        td = replace(td, request=replace(td.request, vbus_v=420.0))
    else:
        td = replace(td, request=replace(td.request, load_fraction=0.5))
    with pytest.raises(ValueError):
        analyzer.with_time_domain_losses(original, [td])


@pytest.mark.parametrize("accepted", [True, False])
def test_stage_b_retains_actual_frequency_or_rejects_unregulated_source(source_context, monkeypatch, accepted):
    import pandas as pd

    import llc_design.optimization.two_stage as module

    analyzer, original, td = source_context
    if not accepted:
        td = replace(td, metrics=replace(td.metrics, output_voltage_v=40.0))
    spec = original.spec
    row = {"feasible": True, "ln": spec.ln_ratio, "q_full": spec.q_full_load,
        "fr_khz": spec.resonant_frequency_hz / 1000, "primary_turns": spec.primary_turns,
        "secondary_turns": spec.secondary_turns, "primary_device": spec.primary_device,
        "sr_parallel": spec.sr_parallel_devices_per_position, "weighted_loss_w": 1.0}
    optimizer = module.TwoStageLLCOptimizer(analyzer)
    monkeypatch.setattr(optimizer.fast, "run", lambda *a, **k: SimpleNamespace(table=pd.DataFrame([row])))
    monkeypatch.setattr(analyzer, "analyze", lambda *a, **k: original)
    monkeypatch.setattr(module, "_envelope_status", lambda *a: module.ConstraintStatus.PASS)
    monkeypatch.setattr(module, "validate_fha_against_time_domain", lambda *a:
        SimpleNamespace(overall_validity=module.ModelValidity.PASS, points=[SimpleNamespace(td=td)]))
    candidate = optimizer.run(spec, top_n=1).verified[0]
    assert candidate.rejected is not accepted
    if accepted:
        point = candidate.analysis.nominal
        assert point.semiconductor_frequency_hz == 137_000
        assert candidate.rank_metrics["total_loss_w"] == point.total_loss_w
        assert candidate.rank_metrics["efficiency"] == point.efficiency
    else:
        assert "regulation" in candidate.reject_reason
        assert not candidate.rank_metrics

from __future__ import annotations

import hashlib
import shutil
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from pydantic import ValidationError

from llc_design.core.operating_point import solve_operating_point
from llc_design.core.spec import LLCDesignSpec
from llc_design.core.tank import design_tank
from power_agent import c99, jobs, storage, tools, workflow
from power_agent.contracts import (
    CoefficientRequest,
    ControlRequest,
    CornerRequest,
    LLCSpecInput,
    OptimizationRequest,
)
from power_agent.storage import DesignStore, atomic_json, digest, read_json
from power_control_tools.codegen import C99Verification
from power_control_tools.models import DigitalTransferFunction


@pytest.mark.parametrize('payload', [
    {'pout_w': True}, {'pout_w': '3000'}, {'pout_w': None}, {'pout_w': float('nan')},
    {'pout_w': float('inf')}, {'resonant_frequency_hz': 0}, {'primary_turns': 2.5},
    {'unknown': 1}, {'primary_topology': 'buck'}, {'vbus_nom_v': 2000},
])
def test_llc_spec_rejects_ambiguous_or_out_of_domain_values(payload):
    with pytest.raises((ValidationError, ValueError)):
        LLCSpecInput.model_validate(payload)


def test_validation_exposes_effective_defaults_without_feasibility_claim():
    result = tools.design_validate_spec({'pout_w': 3000})
    assert result['public_spec'] == {'pout_w': 3000.0}
    assert result['supplied_fields'] == ['pout_w']
    assert 'vbus_nom_v' in result['defaulted_fields']
    assert result['effective_spec']['vbus_nom_v'] == 400
    assert 'feasible' not in result
    assert result['evidence']['verified_for_hardware'] is False


def test_snapshots_are_deterministic_and_distinguish_input_and_runtime(tmp_path, monkeypatch):
    store = DesignStore(tmp_path)
    first = store.create({'pout_w': 3000.0})
    assert first['design_id'] == store.create({'pout_w': 3000.0})['design_id']
    assert store.create({'pout_w': 2000.0})['design_id'] != first['design_id']
    assert store.load(first['design_id'])['effective_spec']['pout_w'] == 3000
    original = storage.runtime_identity()
    monkeypatch.setattr(storage, 'runtime_identity', lambda: {**original, 'source_sha256': 'changed'})
    with pytest.raises(ValueError, match='runtime/data changed'):
        store.load(first['design_id'])


def test_snapshot_tampering_and_path_escape_are_rejected(tmp_path):
    store = DesignStore(tmp_path)
    design = store.create({})
    path = Path(design['path'])
    payload = read_json(path)
    payload['effective_spec']['pout_w'] = 123
    atomic_json(path, payload)
    with pytest.raises(ValueError, match='integrity'):
        store.load(design['design_id'])
    with pytest.raises(ValueError, match='identifier'):
        store.path('designs', '../private')


def test_optimization_axes_are_explicit_and_other_fields_are_fixed():
    spec = LLCDesignSpec(primary_turns=28, secondary_turns=4, sr_parallel_devices_per_position=3)
    request = OptimizationRequest(ln_values=[4.0, 5.0])
    config = workflow.optimization_config(spec, request)
    assert config.count == 2
    assert config.primary_turn_values == (28,)
    assert config.sr_parallel_values == (3,)
    assert config.q_values == (spec.q_full_load,)
    with pytest.raises(ValidationError):
        OptimizationRequest(ln_values=[2.0]*8, q_values=[0.3]*8)
    with pytest.raises(ValueError, match='AUTO_DESIGN'):
        workflow.optimization_config(spec.clone(parameter_mode='USER_DEFINED'), request)


@pytest.mark.parametrize('corner_input', [
    {'vbus_values_v': [400.0]*8, 'load_fractions': [1.0]*8, 'lr_scales': [0.95, 1.0, 1.05]},
    {'vbus_values_v': [400.0], 'load_fractions': [0.01]},
    {'vbus_values_v': [100.0], 'load_fractions': [1.0]},
])
def test_corner_budget_model_minimum_and_bus_range_are_enforced(corner_input):
    with pytest.raises((ValidationError, ValueError)):
        workflow.explicit_corners(LLCDesignSpec(), CornerRequest.model_validate(corner_input))


def test_real_corner_matches_kernel_and_zero_load_stays_unknown(tmp_path):
    store = DesignStore(tmp_path)
    snapshot = store.create({})
    result = workflow.execute('corners', store, snapshot['design_id'],
        {'vbus_values_v': [400.0], 'load_fractions': [0.0, 1.0]})
    assert result['design_status'] == 'UNKNOWN'
    points = result['result']['points']
    assert points[0]['overall_status'] == 'UNKNOWN'
    assert points[0]['operating_point'] is None
    expected = solve_operating_point(LLCDesignSpec(), design_tank(LLCDesignSpec()), 400, 1)
    assert points[1]['operating_point']['switching_frequency_hz'] == pytest.approx(expected.switching_frequency_hz)
    assert result['result']['corner_count'] == 2
    assert result['evidence']['verified_for_hardware'] is False


def test_fha_only_optimization_cannot_claim_verified_design(tmp_path):
    store = DesignStore(tmp_path)
    snapshot = store.create({})
    result = workflow.execute('optimization', store, snapshot['design_id'], {'verify_time_domain': False})
    assert result['design_status'] == 'SCREENED'
    assert result['result']['evaluated_count'] == 1
    assert result['result']['candidates'] == []
    assert 'NOT_RUN' in result['result']['warnings'][0]


@pytest.fixture
def control_request():
    f = np.geomspace(1, 10000, 120)
    plant = 1 / (1 + 1j*f/1000)
    return ControlRequest(frequency_hz=f.tolist(), plant_real=plant.real.tolist(),
        plant_imag=plant.imag.tolist(), source_description='synthetic one-pole equivalent plant',
        sample_rate_hz=50000.0, target_crossover_hz=500.0, target_phase_margin_deg=70.0)


def test_real_control_design_retains_requested_and_achieved_metrics(control_request):
    result = workflow.design_control(control_request)
    assert result['accepted'] is False
    assert result['status'] == 'REVIEW'
    assert result['selected']['gain_margin_db'] is None
    assert result['input_sha256'] == digest(control_request.model_dump())


def test_multipoled_plant_can_meet_controller_targets():
    f = np.geomspace(20.0, 20000.0, 700)
    s = 1j*2*np.pi*f
    plant = 1.5/((1+s/(2*np.pi*350))*(1+s/(2*np.pi*2500))*(1+s/(2*np.pi*12000)))
    request = ControlRequest(frequency_hz=f.tolist(), plant_real=plant.real.tolist(),
        plant_imag=plant.imag.tolist(), source_description='synthetic three-pole equivalent plant',
        controller_kind='2p2z', sample_rate_hz=50000.0, target_crossover_hz=3000.0,
        target_phase_margin_deg=60.0)
    result = workflow.design_control(request)
    assert result['accepted'] is True
    assert result['requested_target_met'] is True
    assert result['status'] == 'PASS'
    assert result['selected']['achieved_crossover_hz'] == pytest.approx(3000, rel=0.03)
    assert result['selected']['achieved_phase_margin_deg'] >= 58


def test_lower_crossover_requires_explicit_opt_in(control_request, monkeypatch):
    selected = SimpleNamespace(controller=DigitalTransferFunction((0.1,), (1.0,), 50000),
        achieved_crossover_hz=100, achieved_phase_margin_deg=70, gain_margin_db=None,
        ms=1.2, mt=1.1, accepted=True, parameters={})
    monkeypatch.setattr(workflow, 'auto_design_controller', lambda *a, **k: SimpleNamespace(
        selected=selected, fallback_used=True, status='PASS', requested_crossover_hz=500,
        requested_phase_margin_deg=70, message='Lower crossover selected'))
    assert workflow.design_control(control_request)['status'] == 'REVIEW'
    assert workflow.design_control(control_request)['accepted'] is False
    allowed = control_request.model_copy(update={'allow_lower_crossover': True})
    assert workflow.design_control(allowed)['accepted'] is True


def test_control_rejects_complete_loop_and_nyquist(control_request):
    data = control_request.model_dump()
    with pytest.raises(ValidationError):
        ControlRequest.model_validate({**data, 'response_kind': 'complete_loop'})
    with pytest.raises(ValidationError):
        ControlRequest.model_validate({**data, 'sample_rate_hz': 10000.0})


def wait_terminal(manager, job_id, seconds=30):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        result = manager.get(job_id)
        if result['state'] in jobs.TERMINAL:
            return result
        time.sleep(0.02)
    manager.cancel(job_id)
    pytest.fail('Job did not reach a terminal state')


def test_real_subprocess_job_export_and_result_integrity(tmp_path):
    store = DesignStore(tmp_path)
    manager = jobs.JobManager(store)
    design = store.create({})
    job = manager.submit('corners', design['design_id'],
        {'vbus_values_v': [400.0], 'load_fractions': [1.0]}, 30)
    status = wait_terminal(manager, job['job_id'])
    assert status['state'] == 'COMPLETED', status
    assert status['design_status'] == 'PASS'
    result = manager.get(job['job_id'], True)['result']
    assert result['design_id'] == design['design_id']
    exported = manager.export(job['job_id'])
    report = read_json(Path(exported['path']))
    assert report['design']['effective_spec']['pout_w'] == 3000
    assert hashlib.sha256(Path(exported['path']).read_bytes()).hexdigest() == exported['sha256']
    html = manager.export(job['job_id'], 'html')
    assert 'Hardware validation: NOT RUN' in Path(html['path']).read_text()
    result['design_status'] = 'invented'
    atomic_json(store.path('jobs', job['job_id'], '.result.json'), result)
    with pytest.raises(ValueError, match='integrity'):
        manager.get(job['job_id'], True)
    manager.close()


@pytest.mark.parametrize('action, expected', [('timeout', 'TIMED_OUT'), ('cancel', 'CANCELLED')])
def test_jobs_enforce_process_timeout_and_cancellation(tmp_path, monkeypatch, action, expected):
    real_popen = subprocess.Popen
    def slow_worker(*args, **kwargs):
        return real_popen([sys.executable, '-c', 'import time; time.sleep(60)'], **kwargs)
    monkeypatch.setattr(jobs.subprocess, 'Popen', slow_worker)
    store = DesignStore(tmp_path)
    manager = jobs.JobManager(store)
    design = store.create({})
    job = manager.submit('analysis', design['design_id'], {}, 1 if action == 'timeout' else 30)
    with pytest.raises(ValueError, match='active'):
        manager.submit('analysis', design['design_id'], {}, 30)
    with pytest.raises(ValueError, match='completed'):
        manager.export(job['job_id'])
    other_session = jobs.JobManager(store)
    assert other_session.get(job['job_id'])['state'] == 'UNMANAGED'
    other_session.cancel(job['job_id'])
    assert manager.get(job['job_id'])['state'] == 'RUNNING'
    if action == 'cancel':
        manager.cancel(job['job_id'])
    status = wait_terminal(manager, job['job_id'], 5)
    assert status['state'] == expected
    assert not manager._active
    manager.close()


def test_c99_failed_verification_stays_compile_blocked(tmp_path, monkeypatch):
    from power_control_tools import codegen
    monkeypatch.setattr(codegen, 'verify_c99_filter', lambda *a, **kw:
        C99Verification(True, False, 1.0, 1.0, message='injected mismatch'))
    request = CoefficientRequest(b=[0.1, 0.1], a=[1.0, -0.8], sample_rate_hz=50000)
    result = c99.export_verified(request, DesignStore(tmp_path))
    assert result['verification']['status'] == 'FAIL'
    assert '#error' in Path(result['path']).read_text()


def test_c99_actual_host_verification_and_header_hash(tmp_path):
    if shutil.which('gcc') is None:
        pytest.skip('No host C compiler')
    request = CoefficientRequest(b=[0.1, 0.1], a=[1.0, -0.8], sample_rate_hz=50000)
    result = c99.export_verified(request, DesignStore(tmp_path))
    assert result['verification']['status'] == 'PASS', result
    assert '#error' not in Path(result['path']).read_text()
    assert result['verification']['header_sha256'] == hashlib.sha256(Path(result['path']).read_bytes()).hexdigest()
    assert result['evidence']['verified_for_hardware'] is False


def test_unknown_two_stage_evidence_cannot_be_promoted(tmp_path, monkeypatch):
    import pandas as pd

    from llc_design.analysis.fha_td_validation import ModelValidity
    from llc_design.core.engineering import ConstraintStatus
    from llc_design.optimization.two_stage import VerifiedCandidate

    store = DesignStore(tmp_path)
    snapshot = store.create({})
    candidate = VerifiedCandidate(LLCDesignSpec(), SimpleNamespace(feasible=True, operating_points=()),
        ConstraintStatus.UNKNOWN, ModelValidity.UNKNOWN, None, None, None, {}, False)
    optimized = SimpleNamespace(stage_a=SimpleNamespace(table=pd.DataFrame([{'feasible': True}])),
        verified=(candidate,), warnings=())
    monkeypatch.setattr(workflow, 'TwoStageLLCOptimizer', lambda: SimpleNamespace(run=lambda *a, **kw: optimized))
    result = workflow.execute('optimization', store, snapshot['design_id'], {})
    assert result['design_status'] == 'UNKNOWN'
    row = result['result']['candidates'][0]
    assert row['accepted'] is False
    assert row['zvs_margin_worst'] is None
    assert row['reject_reason']


def test_changed_source_requires_server_restart_before_new_snapshot(tmp_path, monkeypatch):
    original = storage.runtime_identity()
    monkeypatch.setattr(storage, 'runtime_identity', lambda: {**original, 'source_sha256': 'changed'})
    with pytest.raises(ValueError, match='server startup'):
        DesignStore(tmp_path).create({})

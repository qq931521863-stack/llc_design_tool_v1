"""Priority proof mutations reject absent consumers, changed sources and targets."""
from copy import deepcopy

import pytest

from llc_design.tests.test_completion_native_contract import validate
from llc_design.tests.test_release_verification import ROOT, _proof


def test_priority_proof_required_even_with_prior_completion_proofs():
    proof = _proof()
    validate(proof)
    del proof['power_stage_priority']
    with pytest.raises(RuntimeError, match='report'):
        validate(proof)


@pytest.mark.parametrize('section', ['full_bridge_suggestion', 'advanced_suggestion', 'llc_components'])
def test_missing_priority_consumer_is_rejected(section):
    proof = _proof()
    del proof['power_stage_priority'][section]
    with pytest.raises(RuntimeError, match='report'):
        validate(proof)


@pytest.mark.parametrize('section', ['full_bridge_suggestion', 'advanced_suggestion'])
@pytest.mark.parametrize('field,value', [
    ('preview_did_not_apply', False), ('apply_recomputed', False),
    ('nested_options_preserved', False), ('project_roundtrip', False),
    ('recomputed_waveforms_identical', False), ('report_consistency', False),
    ('waveform_sha256', 'missing'), ('design_id', '0'*20), ('proposal', {}),
])
def test_suggestion_consumers_must_all_be_proven(section, field, value):
    proof = _proof()
    proof['power_stage_priority'][section][field] = value
    with pytest.raises(RuntimeError, match='report'):
        validate(proof)


@pytest.mark.parametrize('section', ['full_bridge_suggestion', 'advanced_suggestion'])
@pytest.mark.parametrize('fault', ['algorithm', 'source_identity', 'result_identity', 'ripple', 'duty',
                                   'frequency', 'changed_fields', 'untargeted', 'nested'])
def test_suggestion_targets_and_nested_source_must_match(section, fault):
    proof = _proof()
    case = proof['power_stage_priority'][section]
    record = case['proposal']
    if fault == 'algorithm': record['algorithm'] = 'legacy'
    elif fault == 'source_identity': record['source_design_id'] = '0'*20
    elif fault == 'result_identity': record['result_design_id'] = '0'*20
    elif fault == 'ripple': record['achieved']['ripple_pp_a'] *= 1.01
    elif fault == 'duty': record['achieved']['duty'] = .99
    elif fault == 'frequency': record['achieved']['switching_frequency_hz'] = True
    elif fault == 'changed_fields': record['changed_fields'] = ['inductance_h', 'two_switch_options']
    elif fault == 'untargeted': case['inputs']['magnetizing_inductance_h'] = .123
    else: case['inputs']['two_switch_options'] = {'parameter_source': 'fabricated'}
    with pytest.raises(RuntimeError, match='report'):
        validate(proof)


@pytest.mark.parametrize('fault', ['ccm_duty', 'ccm_mode', 'inductance', 'target'])
def test_light_load_bridge_cannot_be_substituted_with_conventional_ccm(fault):
    proof = _proof()
    case = proof['power_stage_priority']['full_bridge_suggestion']
    if fault == 'ccm_duty': case['proposal']['achieved']['duty'] = 5/24
    elif fault == 'ccm_mode': case['proposal']['achieved']['mode'] = 'CCM'
    elif fault == 'target': case['proposal']['targets']['ripple_pp_a'] = .1
    else: case['inputs']['inductance_h'] *= 2
    with pytest.raises(RuntimeError, match='report'):
        validate(proof)


@pytest.mark.parametrize('component', ['coesr', 'rseries', 'bus'])
@pytest.mark.parametrize('field,value', [('signals', ['v_bridge', 'i_resonant']),
    ('source_identity', '3'*64), ('max_curve_absolute_error', 1e-12),
    ('max_curve_absolute_error', True), ('report_selection_exact', False)])
def test_t21_component_click_curves_and_report_bind_exact_source(component, field, value):
    proof = _proof()
    proof['power_stage_priority']['llc_components']['components'][component][field] = value
    with pytest.raises(RuntimeError, match='report'):
        validate(proof)


@pytest.mark.parametrize('field,value', [
    ('primary_topology', 'HALF_BRIDGE'), ('solver', 'fabricated'), ('source_identity', ''),
    ('waveform_sha256', ''), ('samples_per_signal', True), ('components', {}),
    ('project_roundtrip', False), ('recomputed_waveforms_identical', False),
    ('bus_power_max_absolute_error_w', 1e-5), ('bus_power_max_absolute_error_w', float('nan')),
    ('bus_reverse_current_min_a', 0), ('bus_voltage_ac_rms_v', .1),
])
def test_t21_dc_bus_and_recomputation_gates(field, value):
    proof = _proof()
    proof['power_stage_priority']['llc_components'][field] = value
    with pytest.raises(RuntimeError, match='report'):
        validate(proof)


def test_optional_proposal_source_is_bound_to_actual_advanced_case():
    proof = _proof()
    case = proof['power_stage_priority']['advanced_suggestion']
    # Mutate both copies, so plain option-equality flags alone cannot pass.
    case['inputs'] = deepcopy(case['inputs'])
    case['proposal']['source_inputs']['two_switch_options']['parameter_source'] = 'changed source'
    case['inputs']['two_switch_options']['parameter_source'] = 'changed source'
    with pytest.raises(RuntimeError, match='report'):
        validate(proof)


def test_production_launcher_invokes_priority_consumers_without_qa_entrypoint():
    source = (ROOT/'packaging/launcher.py').read_text(encoding='utf-8')
    for expression in ('_exercise_priority_bridge(controller.power_stage_window',
                       '_exercise_llc_priority_components(controller.llc_window',
                       'QTest.mouseClick(stage.suggest_button', 'QTest.mouseClick(stage.apply_suggestion_button',
                       'QTest.mouseClick(host.topology_view.viewport()',
                       'QTest.mouseClick(view.export_button'):
        assert expression in source


def test_t17_frequency_accepts_reciprocal_machine_roundoff_only():
    proof = _proof()
    values = proof['power_stage_priority']['full_bridge_suggestion']['proposal']['achieved']
    values['switching_frequency_hz'] = 99999.99999999999
    validate(proof)
    values['switching_frequency_hz'] = 99999.999
    with pytest.raises(RuntimeError, match='report'):
        validate(proof)

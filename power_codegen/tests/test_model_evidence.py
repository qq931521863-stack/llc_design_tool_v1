"""Known-invalid legacy settings remain counterexamples, not migrated fixtures."""
from dataclasses import replace
import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from pfc_design.control import PFCControlLabConfig, illustrative_ttpl_config, build_pfc_control_lab_analysis, tune_pfc_current_loop
from pfc_design.vienna import ViennaControlLabConfig, illustrative_vienna_config, build_vienna_control_lab_analysis
from power_codegen.generator import generate_ttpl_control_code, generate_vienna_control_code, validate_ttpl_codegen, validate_vienna_codegen


def ttpl(config):
    tuned = tune_pfc_current_loop(config)
    return build_pfc_control_lab_analysis(replace(config, current_controller=tuned.controller))


@pytest.fixture(scope='module')
def analyses():
    return {'ttpl':ttpl(illustrative_ttpl_config()),
            'vienna':build_vienna_control_lab_analysis(illustrative_vienna_config())}


@pytest.mark.parametrize('kind', ['ttpl','vienna'])
def test_legacy_adc_magnitude_failure_is_recomputed_even_without_result_warnings(kind,tmp_path):
    if kind=='ttpl':
        config=PFCControlLabConfig(); analysis=ttpl(config); emit=generate_ttpl_control_code
        pin=config.power_stage.bus_voltage_v*config.vbus_sense.raw_dc_gain
        assert pin == pytest.approx(5.396290050590219)
    else:
        config=ViennaControlLabConfig(); analysis=build_vienna_control_lab_analysis(config); emit=generate_vienna_control_code
        pin=config.power_stage.bus_voltage_v/2*config.split_bus_sense.raw_dc_gain
        assert pin == pytest.approx(4.721753794266441)
    analysis=replace(analysis,warnings=())
    with pytest.raises(ValueError,match='model-evidence gate'):
        emit(analysis,tmp_path/'rejected')
    assert not (tmp_path/'rejected').exists()
    result=emit(analysis,tmp_path/'explicit-draft',require_stable=False)
    assert not result.validation.passed
    assert any('ADC' in text for text in result.validation.warnings)
    doc=json.loads(result.files['snapshot'].read_text(encoding='utf-8'))
    assert doc['draft_override'] and not doc['validation_passed']
    assert doc['validation_scope']=='FINITE_BAND_MODEL_SCREEN_ONLY'
    assert doc['closed_loop_stability']=='NOT_PROVEN' and not doc['hardware_validated']
    assert 'Overall: FAIL' in result.files['validation'].read_text(encoding='utf-8')
    assert 'does not certify' in result.files['readme'].read_text(encoding='utf-8')


@pytest.mark.parametrize('kind',['ttpl','vienna'])
@pytest.mark.parametrize('fault',['missing_gm','secondary_negative_pm','secondary_negative_gm','missing_crossing','unstable_controller'])
def test_no_finite_margin_or_bad_other_crossing_can_pass_model_gate(analyses,kind,fault):
    analysis=analyses[kind]; loop=analysis.current_loop; margins=loop.margins
    if fault=='missing_gm': margins=replace(margins,gain_margin_db=None,gain_margins_db=())
    elif fault=='secondary_negative_pm': margins=replace(margins,phase_margins_deg=margins.phase_margins_deg+(-1.,))
    elif fault=='secondary_negative_gm': margins=replace(margins,gain_margins_db=margins.gain_margins_db+(-1.,))
    elif fault=='missing_crossing': margins=replace(margins,critical_gain_crossover_hz=None)
    elif fault=='unstable_controller':
        from llc_design.control.digital_loop import DigitalTransferFunction
        loop=replace(loop,controller=DigitalTransferFunction([1.],[1.,-1.2],loop.controller.sample_time_s))
    analysis=replace(analysis,current_loop=replace(loop,margins=margins))
    check=validate_ttpl_codegen if kind=='ttpl' else validate_vienna_codegen
    assert not check(analysis).passed


@pytest.mark.parametrize('kind',['ttpl','vienna'])
def test_source_mismatch_is_not_a_draft_override(analyses,kind,tmp_path):
    analysis=analyses[kind]
    config=replace(analysis.config,current_controller=replace(analysis.config.current_controller,kp=analysis.config.current_controller.kp*1.1))
    emit=generate_ttpl_control_code if kind=='ttpl' else generate_vienna_control_code
    with pytest.raises(ValueError,match='controller differs'):
        emit(replace(analysis,config=config),tmp_path/'bad',require_stable=False)
    assert not (tmp_path/'bad').exists()


@pytest.mark.parametrize('kind',['ttpl','vienna'])
def test_new_example_cli_has_same_gate_and_honest_snapshot(kind,tmp_path):
    from power_codegen.cli import cli
    result=CliRunner().invoke(cli,[kind,'--output',str(tmp_path/kind)])
    assert result.exit_code==0,result.output
    assert 'illustrative ADC front end' in result.output and 'Validation: PASS' in result.output
    doc=json.loads((tmp_path/kind/'design_snapshot.json').read_text(encoding='utf-8'))
    assert doc['validation_passed'] and not doc['draft_override'] and not doc['hardware_validated']
    result=CliRunner().invoke(cli,[kind,'--help'])
    assert '--allow-unvalidated' in result.output and '--allow-unstable' in result.output

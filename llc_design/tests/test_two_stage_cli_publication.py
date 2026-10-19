"""A request may never mix rejection metadata with an old accepted report."""
import json
from types import SimpleNamespace

import pandas as pd
import pytest
from click.testing import CliRunner

from llc_design.__main__ import cli
from llc_design.core.spec import LLCDesignSpec
from llc_design.optimization import two_stage as model
from llc_design.optimization.sweep import OptimizationResult


@pytest.mark.parametrize('kind',['accepted','empty','file','symlink'])
def test_existing_destination_rejected_before_solver_and_without_changing_bytes(tmp_path,monkeypatch,kind):
    output=tmp_path/'result'
    if kind=='file':output.write_bytes(b'user-owned bytes')
    elif kind=='symlink':
        target=tmp_path/'target';target.mkdir();(target/'keep').write_bytes(b'not ours');output.symlink_to(target,target_is_directory=True)
    else:
        output.mkdir()
        if kind=='accepted':
            (output/'selected_candidate').mkdir()
            (output/'selected_candidate'/'report.md').write_bytes(b'previous accepted exact design')
            (output/'two_stage_verification.json').write_bytes(b'{"selected_candidate_id":"old"}')
    before={str(p.relative_to(tmp_path)):p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    def unexpected(*args,**kwargs):raise AssertionError('Existing destination must reject before computation')
    monkeypatch.setattr(model,'TwoStageLLCOptimizer',unexpected)
    result=CliRunner().invoke(cli,['optimize','--profile','current','--two-stage','--output',str(output)])
    assert result.exit_code!=0
    assert 'new output directory' in result.output or 'is a file' in result.output
    assert before=={str(p.relative_to(tmp_path)):p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}


def test_new_rejected_request_has_explicit_final_status_and_no_selected_report(tmp_path,monkeypatch):
    candidate=model.VerifiedCandidate(LLCDesignSpec(),None,model.ConstraintStatus.UNKNOWN,
        model.ModelValidity.UNKNOWN,None,None,None,{},True,'Independent unavailable evidence')
    result=model.TwoStageOptimizationResult(OptimizationResult(pd.DataFrame(),pd.DataFrame(),None),
        (candidate,),dict(model.DEFAULT_WEIGHTS),())
    monkeypatch.setattr(model,'TwoStageLLCOptimizer',lambda:SimpleNamespace(run=lambda *a,**k:result))
    output=tmp_path/'new'
    response=CliRunner().invoke(cli,['optimize','--profile','current','--two-stage','--output',str(output)])
    assert response.exit_code!=0 and 'No verified candidate' in response.output
    assert json.loads((output/'run_status.json').read_text())['state']=='REJECTED'
    assert not (output/'selected_candidate').exists()

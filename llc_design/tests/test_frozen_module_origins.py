"""Actual filesystem/symlink layouts; no native execution is inferred here."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

SPEC=importlib.util.spec_from_file_location('pdt_origin_launcher',Path(__file__).resolve().parents[2]/'packaging/launcher.py')
MODULE=importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def mac_layout(tmp_path):
    contents=tmp_path/'PowerDesignTool.app'/'Contents'
    for name in ('Frameworks','Resources','MacOS'):(contents/name).mkdir(parents=True)
    exe=contents/'MacOS'/'PowerDesignTool';exe.touch()
    return contents,exe


def link(source,destination):
    try:source.symlink_to(destination,target_is_directory=True)
    except OSError as exc:pytest.skip(f'Platform cannot create test directory symlinks: {exc}')


@pytest.mark.parametrize('package',['llc_design/core','llc_design/magnetics','llc_design/analysis'])
def test_macos_virtual_origin_through_real_resources_link(tmp_path,package):
    contents,exe=mac_layout(tmp_path);root=contents/'Frameworks'
    (contents/'Resources'/'llc_design').mkdir()
    link(root/'llc_design','../Resources/llc_design')
    origin=root/package/'module.py'
    assert not origin.exists()  # PyInstaller pure-module virtual path.
    check=MODULE._frozen_origin_check(origin,root,exe,'darwin')
    assert check['accepted']
    assert Path(check['resolved_origin']).is_relative_to(contents/'Resources')
    assert not Path(check['resolved_origin']).is_relative_to(root)


@pytest.mark.parametrize('platform',['linux','win32','darwin'])
def test_ordinary_bundled_virtual_module_is_accepted(tmp_path,platform):
    root=tmp_path/'_internal';root.mkdir()
    check=MODULE._frozen_origin_check(root/'llc_design/core/spec.py',root,tmp_path/'app',platform)
    assert check['accepted']


@pytest.mark.parametrize('fault',['external','prefix_sibling','resources_direct','escape_package',
                                 'escape_resources','wrong_platform','wrong_executable','nested_outside'])
def test_external_or_untrusted_layout_cannot_expand_bundle_authority(tmp_path,fault):
    contents,exe=mac_layout(tmp_path);root=contents/'Frameworks';resources=contents/'Resources'
    outside=tmp_path/'external';outside.mkdir()
    platform='darwin';origin=root/'pkg/module.py'
    if fault=='external':origin=outside/'module.py'
    elif fault=='prefix_sibling':origin=contents/'Frameworks_external'/'module.py'
    elif fault=='resources_direct':origin=resources/'pkg/module.py'
    elif fault=='escape_package':link(root/'pkg',outside)
    elif fault=='escape_resources':
        resources.rmdir();link(resources,outside);link(root/'pkg','../Resources/pkg')
    elif fault=='wrong_platform':platform='linux';link(root/'pkg','../Resources/pkg')
    elif fault=='wrong_executable':exe=tmp_path/'elsewhere';link(root/'pkg','../Resources/pkg')
    elif fault=='nested_outside':
        (resources/'pkg').mkdir();link(resources/'pkg/sub',outside)
        link(root/'pkg','../Resources/pkg');origin=root/'pkg/sub/module.py'
    check=MODULE._frozen_origin_check(origin,root,exe,platform)
    assert not check['accepted'],check


def test_gate_logs_exact_failed_origin_before_raising(tmp_path,monkeypatch,capsys):
    root=tmp_path/'_internal';root.mkdir();origin=tmp_path/'external/module.py'
    fake=SimpleNamespace(__file__=str(origin))
    # Patch only this module's sys reference, never the interpreter's global state.
    monkeypatch.setattr(MODULE,'sys',SimpleNamespace(frozen=True,_MEIPASS=str(root),
                        executable=str(tmp_path/'app'),platform='linux'))
    with pytest.raises(RuntimeError,match='outside the frozen bundle'):
        MODULE._require_frozen_module_origins({'test.module':fake})
    output=capsys.readouterr().out
    assert 'FROZEN_MODULE_ORIGINS' in output and str(origin) in output and '"accepted": false' in output


def test_frameworks_root_itself_cannot_escape_canonical_app(tmp_path):
    contents,exe=mac_layout(tmp_path);root=contents/'Frameworks'
    outside=tmp_path/'external';outside.mkdir();root.rmdir();link(root,outside)
    check=MODULE._frozen_origin_check(root/'pkg/module.py',root,exe,'darwin')
    assert not check['accepted'] and check['allowed_resolved_roots']==[]


def test_symlink_parent_traversal_is_resolved_before_any_normalization(tmp_path):
    contents,exe=mac_layout(tmp_path);root=contents/'Frameworks'
    outside=tmp_path/'external';(outside/'deep').mkdir(parents=True)
    link(root/'pkg',outside/'deep');origin=root/'pkg/../evil.py'
    check=MODULE._frozen_origin_check(origin,root,exe,'darwin')
    assert Path(check['resolved_origin'])==outside/'evil.py'
    assert not check['accepted']


def test_whole_app_alias_retains_same_canonical_executable_anchor(tmp_path):
    contents,exe=mac_layout(tmp_path);root=contents/'Frameworks'
    (contents/'Resources/pkg').mkdir();link(root/'pkg','../Resources/pkg')
    alias=tmp_path/'Alias.app';link(alias,contents.parent)
    check=MODULE._frozen_origin_check(alias/'Contents/Frameworks/pkg/module.py',
        alias/'Contents/Frameworks',alias/'Contents/MacOS/PowerDesignTool','darwin')
    assert check['accepted']
    assert Path(check['resolved_origin'])==contents/'Resources/pkg/module.py'

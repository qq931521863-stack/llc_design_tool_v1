"""Corner reports bind the complete loop setup, not just controller coefficients."""
from dataclasses import FrozenInstanceError, asdict, replace

import numpy as np
import pytest

from power_stage_design import loop
from power_stage_design.corners import CornerRequest, analyze_corners
from power_stage_design.loop import Compensator, LoopPlant
from power_stage_design.models import DesignInputs
from power_stage_design.report import write_report
from power_stage_design.topologies import calculate_stage


@pytest.fixture
def case(monkeypatch):
    # Actual loop calculations on a small independent grid keep this API test fast.
    original = loop.analyze_loop
    grid = np.geomspace(0.1, 49_900.0, 80)

    def analyze(plant, compensator, frequency_hz=None):
        return original(plant, compensator, grid if frequency_hz is None else frequency_hz)

    monkeypatch.setattr(loop, "analyze_loop", analyze)
    inputs = DesignInputs("T01", 12, 5, 3, 100e3, 47e-6)
    plant = LoopPlant()
    comp = Compensator(network="type_iii")
    settings = {"plant": asdict(plant), "compensator": asdict(comp)}
    request = CornerRequest((12.0,), (3.0,))
    sweep = analyze_corners(inputs, request, loop_settings=settings)
    return inputs, plant, comp, settings, sweep


@pytest.mark.parametrize("changes", [
    {"dcr_ohm": 0.5}, {"esr_ohm": 0.1}, {"capacitance2_f": 22e-6},
    {"esr2_ohm": 0.05}, {"ramp_v": 2.0}, {"control_mode": "peak_cmc"},
    {"sense_ohm": 0.2}, {"sense_gain": 2.0}, {"slope_v": 0.2},
    {"slope_mode": "multiplier"}, {"slope_multiplier": 2.0},
])
def test_report_rejects_same_controller_changed_plant(case, tmp_path, changes):
    inputs, plant, comp, _, sweep = case
    changed = replace(plant, **changes)
    settings = {"plant": asdict(changed), "compensator": asdict(comp)}
    output = tmp_path / "stale.html"
    with pytest.raises(ValueError, match="Corner scan.*loop"):
        write_report(calculate_stage(inputs), output,
                     loop_result=loop.analyze_loop(changed, comp),
                     loop_settings=settings, corner_sweep=sweep)
    assert not output.exists()


def test_report_rejects_changed_controller(case, tmp_path):
    inputs, plant, comp, _, sweep = case
    changed = replace(comp, gbw_hz=5e6)
    with pytest.raises(ValueError, match="Corner scan"):
        write_report(calculate_stage(inputs), tmp_path / "controller.html",
                     loop_result=loop.analyze_loop(plant, changed),
                     loop_settings={"plant": asdict(plant), "compensator": asdict(changed)},
                     corner_sweep=sweep)


def test_complete_identity_is_immutable_and_detached_from_input(case):
    _, plant, comp, settings, sweep = case
    assert asdict(sweep.loop_identity) == settings
    settings["plant"]["dcr_ohm"] = 0.5
    settings["compensator"]["gbw_hz"] = 5e6
    assert sweep.loop_identity.plant == plant
    assert sweep.loop_identity.compensator == comp
    with pytest.raises(FrozenInstanceError):
        sweep.loop_identity.plant.dcr_ohm = 0.5
    with pytest.raises(FrozenInstanceError):
        sweep.loop_identity.compensator.gbw_hz = 5e6


def test_matching_identity_exports_and_legacy_loop_sweep_requires_rescan(case, tmp_path):
    inputs, plant, comp, settings, sweep = case
    result = calculate_stage(inputs)
    current = loop.analyze_loop(plant, comp)
    output = write_report(result, tmp_path / "current.html", loop_result=current,
                          loop_settings=settings, corner_sweep=sweep)
    assert 'loop_identity' in output.read_text(encoding="utf-8")
    legacy = replace(sweep, loop_identity=None)
    with pytest.raises(ValueError, match="Corner scan.*identity"):
        write_report(result, tmp_path / "legacy.html", loop_result=current,
                     loop_settings=settings, corner_sweep=legacy)


def test_no_loop_sweep_remains_supported(case, tmp_path):
    inputs, _, _, _, _ = case
    sweep = analyze_corners(inputs, CornerRequest((12.0,), (3.0,)))
    assert sweep.loop_identity is None
    assert write_report(calculate_stage(inputs), tmp_path / "no-loop.html",
                        corner_sweep=sweep).exists()


def test_loop_sweep_cannot_be_reported_without_current_loop(case, tmp_path):
    inputs, _, _, settings, sweep = case
    with pytest.raises(ValueError, match="Corner scan"):
        write_report(calculate_stage(inputs), tmp_path / "missing-loop.html",
                     loop_settings=settings, corner_sweep=sweep)


def test_no_loop_sweep_cannot_acquire_unverified_loop_settings(case, tmp_path):
    inputs, _, _, settings, _ = case
    sweep = analyze_corners(inputs, CornerRequest((12.0,), (3.0,)))
    with pytest.raises(ValueError, match="Corner scan"):
        write_report(calculate_stage(inputs), tmp_path / "unverified-loop.html",
                     loop_settings=settings, corner_sweep=sweep)


def test_effective_defaults_match_explicit_settings(case, tmp_path):
    inputs, plant, comp, _, sweep = case
    # Equivalent API forms must match after dataclass defaults are applied.
    settings = {"plant": {}, "compensator": {"network": "type_iii"}}
    assert write_report(calculate_stage(inputs), tmp_path / "defaults.html",
                        loop_result=loop.analyze_loop(plant, comp),
                        loop_settings=settings, corner_sweep=sweep).exists()


def test_project_saves_request_and_settings_without_cached_corner_results(case, tmp_path):
    from power_stage_design.project import read_project, save_project

    inputs, _, _, settings, sweep = case
    path = save_project(inputs, tmp_path / "project.json", loop_settings=settings,
                        corner_request=asdict(sweep.request))
    restored, document = read_project(path)
    assert restored == inputs
    assert document["loop_settings"] == settings
    assert document["corner_request"]["input_voltages_v"] == [12.0]
    assert "corner_sweep" not in document
    assert "loop_identity" not in document
    fresh = analyze_corners(restored, CornerRequest(**document["corner_request"]),
                            loop_settings=document["loop_settings"])
    assert fresh.loop_identity == sweep.loop_identity
    assert fresh.extrema == sweep.extrema

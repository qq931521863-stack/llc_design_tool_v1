"""Two-winding integrated resonant magnetics: port, physical and UI contracts."""
import json
import math
from dataclasses import replace
from types import SimpleNamespace

import pytest

from llc_design.core.config import load_spec, save_spec, waveform_spec_identity
from llc_design.core.integrated_magnetics import IntegratedMagnetics, from_spec
from llc_design.core.operating_point import solve_operating_point
from llc_design.core.spec import (
    IntegratedInductanceSource,
    LLCDesignSpec,
    MagneticsMode,
    TankParameterMode,
)
from llc_design.core.tank import design_tank, equivalent_ac_load_ohm, target_gain
from llc_design.magnetics.leakage_geometry import screen_integrated_leakage
from llc_design.models.system import LLCSystemAnalyzer


def reference_spec(**changes):
    # Linear physical T fixture: physical n=7.5, Lmu=300uH,
    # Lsigma,p=20uH and primary-referred Lsigma,s=20uH.
    return LLCDesignSpec(magnetics_mode=MagneticsMode.INTEGRATED,
        integrated_characterized_primary_turns=30, integrated_characterized_secondary_turns=4,
        integrated_characterized_core="PQ50_REF", integrated_lp_h=320e-6,
        integrated_ls_h=320e-6/7.5**2, integrated_mutual_h=300e-6/7.5,
        integrated_measurement_note="Synthetic two-leakage software fixture, not hardware measurement").clone(**changes)


def test_independent_golden_vector_and_energy():
    m = IntegratedMagnetics(100e-6,25e-6,40e-6,2.)
    assert (m.lr_h,m.lm_h,m.effective_turns_ratio) == pytest.approx((36e-6,64e-6,1.6))
    assert (m.physical_magnetizing_h,m.primary_leakage_h,m.secondary_leakage_h)==pytest.approx((80e-6,20e-6,5e-6))
    assert m.physical_magnetizing_current(3,1) == pytest.approx(1.4)
    assert m.flux_density(3,1,20,100e-6) == pytest.approx(.056)
    for ir,im in ((3,1),(-2,5),(0,-1),(2,2)):
        isec = m.effective_turns_ratio*(ir-im)
        ematrix = .5*(m.lp_h*ir**2+m.ls_h*isec**2-2*m.mutual_h*ir*isec)
        eapr=.5*(m.lr_h*ir**2+m.lm_h*im**2)
        assert ematrix == pytest.approx(eapr,rel=1e-12)


@pytest.mark.parametrize("lp,ls,mutual,n", [(0,1,1,1),(1,1,1,1),(1,1,2,1),(1,1,-.5,1),(1,1,.5,.1),(1,1,.5,3),(float('nan'),1,.5,1)])
def test_invalid_matrix_or_physical_ratio_is_rejected(lp,ls,mutual,n):
    with pytest.raises(ValueError): IntegratedMagnetics(lp,ls,mutual,n)


def test_open_short_and_matrix_same_tank_and_ratio():
    s=reference_spec();m=from_spec(s)
    measured=s.clone(integrated_inductance_source=IntegratedInductanceSource.OPEN_SHORT,
                     integrated_lshort_h=m.lr_h,integrated_mutual_h=None)
    assert design_tank(measured) == design_tank(s)
    assert measured.electrical_turns_ratio == pytest.approx(7.03125)
    assert s.turns_ratio == 7.5
    assert target_gain(s,400)==pytest.approx(7.03125*53.4/400)
    assert design_tank(s).rac_nom_ohm==pytest.approx(equivalent_ac_load_ohm(7.03125,53,3000))
    # Optional user Lr/Lm are not used as a second, conflicting physical model.
    manual=s.clone(parameter_mode=TankParameterMode.USER_DEFINED,user_cr_f=70e-9,user_lr_h=None,user_lm_h=None)
    assert design_tank(manual).lr_h == pytest.approx(m.lr_h)


def test_direct_matrix_and_apr_ports_match_complex_loads():
    m=IntegratedMagnetics(100e-6,25e-6,40e-6,2.)
    for freq in (100,10000,100000,1e6):
        w=2j*math.pi*freq
        for zload in (1,10,100,complex(5,2),complex(10,-3)):
            zin=w*m.lp_h-(w*m.mutual_h)**2/(w*m.ls_h+zload)
            zparallel=1/(1/(w*m.lm_h)+1/(m.effective_turns_ratio**2*zload))
            assert w*m.lr_h+zparallel == pytest.approx(zin,rel=1e-12)


def test_identity_and_source_are_required():
    s=reference_spec()
    for changed in (s.clone(primary_turns=31),s.clone(integrated_measurement_note=""),
                    s.clone(integrated_characterized_core=""),s.clone(integrated_lp_h=None)):
        with pytest.raises(ValueError): changed.validate()
    with pytest.raises(ValueError,match="core"):
        LLCSystemAnalyzer().analyze(s,[(400,1)],preferred_transformer_core="PQ40_REF")
    assert waveform_spec_identity(s)!=waveform_spec_identity(s.clone(integrated_mutual_h=s.integrated_mutual_h*.99))


def test_spec_and_control_snapshot_roundtrip_legacy_defaults(tmp_path):
    from llc_design.control.design_session import decode_model, encode_model
    s=reference_spec(); path=save_spec(s,tmp_path/'集成设计.json')
    assert load_spec(path)==s
    assert decode_model(encode_model(s))==s
    legacy=encode_model(LLCDesignSpec())
    legacy['fields']={k:v for k,v in legacy['fields'].items() if not k.startswith('integrated_') and k!='magnetics_mode'}
    assert decode_model(legacy)==LLCDesignSpec()
    plain=json.loads(save_spec(LLCDesignSpec(),tmp_path/'plain.json').read_text(encoding='utf-8'))
    plain={k:v for k,v in plain.items() if not k.startswith('integrated_') and k!='magnetics_mode'}
    path.write_text(json.dumps(plain),encoding='utf-8')
    assert not load_spec(path).integrated_magnetics


@pytest.fixture(scope='module')
def analysis():
    return LLCSystemAnalyzer().analyze(reference_spec(),[(400,1)])


def test_single_shared_magnetic_body_and_physical_flux(analysis):
    from llc_design.dynamics.plant import LLCPlantParameters
    from llc_design.magnetics.magnetic_waveforms import transformer_current_waveforms
    s=analysis.spec;m=from_spec(s);op=analysis.nominal.operating_point
    p,sec,imu=transformer_current_waveforms(s,op,s.magnetic_waveform_samples)
    assert imu == pytest.approx(p+sec/s.turns_ratio)
    expected=m.physical_magnetizing_h*imu/(s.primary_turns*analysis.transformer.core.ae_m2)
    assert analysis.nominal.transformer.b_peak_t==pytest.approx(max(abs(expected)))
    assert analysis.resonant_inductor is None
    assert analysis.nominal.resonant_inductor.total_w==0
    plant=LLCPlantParameters.from_design(s,analysis.tank,op,analysis)
    assert plant.turns_ratio==s.electrical_turns_ratio
    assert plant.physical_magnetizing_h==pytest.approx(300e-6)
    assert plant.resonant_inductor_turns==0 and plant.resonant_inductor_core_area_m2==0
    from llc_design.models.electro_thermal import iterate_electro_thermal
    thermal=iterate_electro_thermal(s,op,loss_builder=lambda spec,op: analysis.nominal,max_iterations=1)
    assert 'resonant_inductor' not in [x.name for x in thermal.nodes]


def test_real_geometry_includes_spacers_and_rejects_unreachable(analysis):
    screen=analysis.transformer.integrated_leakage
    assert len([r for r in screen.regions if r.kind=='P-S insulation'])==2
    assert sum(r.inductance_h for r in screen.regions)==screen.balanced_geometry_h
    assert screen.characterized_balanced_h == pytest.approx(40e-6)
    assert screen.characterized_balanced_h != pytest.approx(analysis.tank.lr_h)
    assert not screen.reachable and screen.required_radial_build_m>screen.available_window_height_m
    assert any('unreachable' in x for x in analysis.feasibility_reasons)


def test_geometry_inverse_spacing_independent_counterexample():
    # Uniform 20:10 turns, 0.5 mm P halves + 1 mm S, 20 mm winding
    # width, 100 mm MLT, target 2.17817 uH needs 1.4 mm at each interface.
    target=2.178170906488923e-6;mu=80e-6;n=2
    s=reference_spec(primary_turns=20,secondary_turns=10,
        integrated_characterized_primary_turns=20,integrated_characterized_secondary_turns=10,
        integrated_lp_h=mu+target/2,integrated_ls_h=(mu+target/2)/n**2,integrated_mutual_h=mu/n)
    design=SimpleNamespace(primary_turns=20,secondary_turns=10,primary_turns_per_layer=10,secondary_turns_per_layer=10,
        primary_wire=SimpleNamespace(equivalent_outer_diameter_m=.0005),secondary_wire=SimpleNamespace(equivalent_outer_diameter_m=.001),
        core=SimpleNamespace(window_width_m=.02,window_height_mm=3.,mlt_primary_mm=100.,mlt_secondary_mm=100.))
    g=screen_integrated_leakage(s,design)
    assert g.required_interwinding_gap_m == pytest.approx(.0014,rel=1e-5)
    assert g.required_radial_build_m == pytest.approx(.0048,rel=1e-5)
    assert not g.reachable
    changed=screen_integrated_leakage(s.clone(integrated_interwinding_gap_m=.0014),design)
    assert changed.balanced_geometry_h==pytest.approx(target,rel=1e-5)
    assert not changed.reachable  # electrical match cannot defeat window limit


def test_optimizer_rejects_stale_measured_turns():
    from llc_design.optimization.sweep import LLCOptimizer, OptimizationConfig
    s=reference_spec();cfg=OptimizationConfig.integrated(s)
    with pytest.raises(ValueError,match='re-characterization'):
        LLCOptimizer().run(s,replace(cfg,primary_turn_values=(31,)))
    with pytest.raises(ValueError,match='re-characterization'):
        LLCOptimizer().run(s,replace(cfg,ln_values=(3.,)))


def test_real_geometry_reachable_and_spacing_matches():
    mu,sigma,n=300e-6,3.5e-6,7.5
    spec=reference_spec(integrated_lp_h=mu+sigma,integrated_ls_h=(mu+sigma)/n**2,
        integrated_mutual_h=mu/n,integrated_interwinding_gap_m=.0010325249820337418)
    result=LLCSystemAnalyzer().analyze(spec,[(400,1)])
    screen=result.transformer.integrated_leakage
    assert screen.reachable and screen.status=='MODEL_REACHABLE_REQUIRES_CHARACTERIZATION'
    assert screen.balanced_geometry_h == pytest.approx(7e-6,rel=1e-11)
    assert screen.relative_error<1e-10
    assert screen.required_radial_build_m < screen.available_window_height_m
    assert 'not hardware' in ' '.join(result.warnings)


def test_three_phase_star_rejects_unimplemented_magnetic_characterization():
    from llc_design.multiphase.electrical import three_phase_design_context
    with pytest.raises(ValueError,match='not implemented'):
        three_phase_design_context(reference_spec())


def test_legacy_waveform_identity_unchanged():
    import hashlib
    from dataclasses import asdict
    spec=LLCDesignSpec();data=asdict(spec)
    data={k:v for k,v in data.items() if not k.startswith('integrated_') and k!='magnetics_mode'}
    assert waveform_spec_identity(spec)==hashlib.sha256(json.dumps(data,sort_keys=True,allow_nan=False).encode()).hexdigest()


def test_stale_tank_and_unmapped_magnetizing_resistance_are_refused(analysis):
    from llc_design.analysis.harmonic_balance import (
        HarmonicBalanceConfig,
        MultiHarmonicLLCSolver,
    )
    from llc_design.analysis.types import LLCAnalysisRequest
    from llc_design.dynamics.plant import LLCPlantParameters
    spec=reference_spec();tank=design_tank(spec)
    with pytest.raises(ValueError,match='characterized matrix'):
        solve_operating_point(spec,replace(tank,lr_h=tank.lr_h*1.1),400,1)
    with pytest.raises(ValueError,match='series resistance'):
        MultiHarmonicLLCSolver(LLCAnalysisRequest(spec),HarmonicBalanceConfig(magnetizing_series_resistance_ohm=.1))
    p=LLCPlantParameters.from_design(spec,tank,analysis.nominal.operating_point)
    with pytest.raises(ValueError,match='series resistance'):
        replace(p,magnetizing_series_resistance_ohm=.1).validate()


@pytest.mark.parametrize("integrated", [False, True])
def test_v8_actual_layer_mmf_uses_secondary_into_dot_sign(monkeypatch, integrated):
    import numpy as np

    from llc_design.analysis.fha import solve_fha
    from llc_design.analysis.types import LLCAnalysisRequest
    from llc_design.magnetics import v8

    spec = (reference_spec() if integrated else LLCDesignSpec()).clone(magnetic_thermal_max_iterations=2)
    analysis = LLCSystemAnalyzer().analyze(spec, [(400, 1)])
    waveform = solve_fha(LLCAnalysisRequest(spec, vbus_v=400, load_fraction=1))
    design = analysis.transformer
    _, ip = v8._one_cycle(waveform, "i_resonant")
    _, isec = v8._one_cycle(waveform, "i_transformer_secondary")
    actual = v8.layered_litz_stack_loss
    captured = []

    def checked(layers, *args, **kwargs):
        mmf = sum(layer.turns * np.asarray(layer.current_waveform_a) for layer in layers)
        np.testing.assert_allclose(mmf, design.primary_turns*ip-design.secondary_turns*isec, atol=1e-10)
        captured.append(mmf)
        return actual(layers, *args, **kwargs)

    monkeypatch.setattr(v8, "layered_litz_stack_loss", checked)
    result = v8.analyze_golden_magnetics(spec, waveform, design)
    assert captured
    if integrated:
        _, im = v8._one_cycle(waveform, "i_magnetizing")
        expected_b = from_spec(spec).flux_density(ip, im, design.primary_turns, design.core.ae_m2)
        assert result.b_peak_t == pytest.approx(np.max(np.abs(expected_b)), rel=1e-12)
        assert result.apr_lr_h == pytest.approx(analysis.tank.lr_h)
        assert "NOT APR Lr" in result.leakage_definition
        assert waveform.waveform.metadata["faraday_validated"] == "false"
        assert "display projection" in waveform.waveform.signal("v_transformer_core").description
        assert any("not Faraday validated" in warning for warning in result.warnings)


def test_real_optimizer_preserves_characterized_body_and_reports_actual_apr():
    from llc_design.optimization.sweep import LLCOptimizer, OptimizationConfig

    spec = reference_spec()
    cfg = replace(OptimizationConfig.integrated(spec), fr_values_hz=(spec.resonant_frequency_hz,))
    result = LLCOptimizer().run(spec, cfg)
    assert len(result.table) == 1
    row = result.table.iloc[0]
    tank = design_tank(spec)
    assert row["inductor_core"] == "INTEGRATED_NO_SEPARATE_CORE"
    assert row["inductor_turns"] == row["inductor_b_t"] == 0
    assert row["turns_ratio"] == spec.turns_ratio
    assert row["electrical_turns_ratio"] == spec.electrical_turns_ratio
    assert row["ln"] == pytest.approx(tank.ln_ratio)
    assert row["q_full"] == pytest.approx(tank.q_full_load)


def test_manual_integrated_optimizer_sweeps_actual_cr_not_unused_fr():
    from llc_design.optimization.sweep import LLCOptimizer, OptimizationConfig

    spec = reference_spec(parameter_mode=TankParameterMode.USER_DEFINED, user_cr_f=70e-9)
    cfg = OptimizationConfig.integrated(spec)
    assert cfg.fr_values_hz[1] == pytest.approx(design_tank(spec).fr_hz)
    captured = []

    class CapturingAnalyzer(LLCSystemAnalyzer):
        def analyze(self, candidate, *args, **kwargs):
            actual = super().analyze(candidate, *args, **kwargs)
            captured.append(actual)
            return actual

    result = LLCOptimizer(CapturingAnalyzer()).run(spec, replace(cfg, fr_values_hz=(90e3, 110e3)))
    assert len(captured) == 2
    for actual, expected_fr in zip(captured, (90e3, 110e3)):
        assert actual.tank.fr_hz == pytest.approx(expected_fr)
        assert actual.tank.cr_f == actual.spec.user_cr_f
        assert from_spec(actual.spec) == from_spec(spec)
        assert actual.transformer.core.part_number == spec.integrated_characterized_core
        assert (actual.transformer.primary_turns, actual.transformer.secondary_turns) == (30, 4)
        assert actual.resonant_inductor is None
    assert captured[0].tank.cr_f != captured[1].tank.cr_f
    rows = result.table.sort_values("fr_khz")
    assert list(rows["fr_khz"]) == pytest.approx([90, 110])
    assert rows.iloc[0]["cr_nf"] > rows.iloc[1]["cr_nf"]
    assert rows.iloc[0]["lr_uh"] == rows.iloc[1]["lr_uh"]
    assert rows.iloc[0]["lm_uh"] == rows.iloc[1]["lm_uh"]
    assert set(rows["inductor_core"]) == {"INTEGRATED_NO_SEPARATE_CORE"}

"""Produce reproducible control/PLL/stress verification metrics (software only)."""
import json
import sys
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dab_design.control import DABVoltageControlConfig, simulate_dab_voltage_loop
from dab_design.core import DABConfig
from pfc_design.three_phase.control import build_three_phase_loops
from pfc_design.three_phase.model import ThreePhaseConfig, simulate_three_phase
from pfc_design.three_phase.performance import evaluate_two_level
from pfc_design.three_phase.pll import compare_plls
from pfc_design.vienna.analysis import build_vienna_control_lab_analysis
from pfc_design.vienna.config import ViennaControlLabConfig


def run():
    _, _, dab_metrics, _ = simulate_dab_voltage_loop(DABConfig(), 400, 3500, DABVoltageControlConfig())
    _, _, dab_cp_metrics, _ = simulate_dab_voltage_loop(
        DABConfig(), 400, 3500, replace(DABVoltageControlConfig(), include_cp=True)
    )
    pfc = simulate_three_phase(ThreePhaseConfig())
    loops = build_three_phase_loops(pfc.config)
    stress = evaluate_two_level(pfc)
    vienna = build_vienna_control_lab_analysis(ViennaControlLabConfig())
    report = {'evidence': 'SOFTWARE_MODEL_ONLY; no hardware measurement',
                  'dab_voltage': dab_metrics, 'dab_voltage_cp': dab_cp_metrics,
                  'pll_comparison': compare_plls()['metrics'],
                  'two_level_nonlinear': pfc.metrics,
                  'two_level_loops': {k: loops[k].summary() for k in ('current','voltage')},
                  'two_level_stress': {k:v for k,v in stress.items() if k not in ('replay','harmonic_rms_a')},
                  'vienna_margins': {k:{'phase_margin_deg': getattr(vienna,k).margins.phase_margin_deg,
                                        'gain_margin_db': getattr(vienna,k).margins.gain_margin_db,
                                        'crossover_hz': getattr(vienna,k).margins.critical_gain_crossover_hz}
                                  for k in ('current_loop','voltage_loop','balance_loop')}}
    (ROOT/'docs/benchmarks/control_validation.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2))


if __name__=='__main__': run()

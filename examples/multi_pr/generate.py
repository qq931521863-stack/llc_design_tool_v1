"""Regenerate demonstration coefficients/C/Bode; these gains are not plant tuned.
Run from repository root: python -m examples.multi_pr.generate
"""
from pathlib import Path

import matplotlib

matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np

from power_control_tools.quasi_pr import (
    HARMONICS,
    QuasiPRConfig,
    ResonantBranch,
    bode_frequencies,
    frequency_response,
)
from power_control_tools.quasi_pr_codegen import coefficient_json, render_quasi_pr_c99


def main():
    destination = Path(__file__).parent
    cfg = QuasiPRConfig(branches=tuple(ResonantBranch(h, 10, 1) for h in HARMONICS))
    (destination/'multi_pr.h').write_text(render_quasi_pr_c99(cfg), encoding='utf-8')
    (destination/'coefficients.json').write_text(coefficient_json(cfg), encoding='utf-8')
    f = bode_frequencies(cfg)
    total, branches = frequency_response(cfg, f)
    analog, _ = frequency_response(cfg, f, analog=True)
    quantized, _ = frequency_response(cfg, f, quantized=True)
    fig, (mag, phase) = plt.subplots(2, 1, figsize=(11, 8), sharex=True)
    curves = [(f'R{b.harmonic}', r, ':') for b, r in zip(cfg.branches, branches)]
    curves += [('Total analog', analog, '--'), ('Total digital f64', total, '-'),
               ('Total digital f32', quantized, '-.')]
    for label, response, style in curves:
        mag.semilogx(f, 20*np.log10(np.maximum(abs(response), 1e-15)), style, label=label)
        phase.semilogx(f, np.unwrap(np.angle(response))*180/np.pi, style)
    mag.set_ylabel('Magnitude / dB')
    phase.set_ylabel('Phase / deg')
    phase.set_xlabel('Frequency / Hz')
    phase.set_xlim(10, 1500)
    mag.set_ylim(-55, 25)
    mag.legend(ncol=3)
    for ax in (mag, phase):
        ax.grid(True, which='both', alpha=.3)
    fig.suptitle('Parallel quasi-PR | Fs=40 kHz, grid=50 Hz, Kp=0.1\n'
                 'Harmonics 1/3/5/7/9/11: Kr=10, wc/(2pi)=1 Hz | Unsaturated example')
    fig.tight_layout()
    fig.savefig(destination/'bode.svg')
    plt.close(fig)


if __name__ == '__main__':
    main()

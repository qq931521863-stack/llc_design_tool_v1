"""Single-header C99 export of parallel quasi-PR (user's direct form II)."""
from __future__ import annotations

import json
import re
from dataclasses import asdict

from .quasi_pr import QuasiPRConfig, branch_coefficients, validate_float32


def coefficient_json(config: QuasiPRConfig) -> str:
    validate_float32(config)
    data = asdict(config)
    data['structure'] = 'Kp + parallel R; denominator 1 + A1*z^-1 + A2*z^-2'
    data['discretization'] = 'Tustin without prewarp'
    data['bandwidth_definition'] = 'bandwidth_hz = wc/(2*pi); full analog -3 dB width = 2*bandwidth_hz'
    data['coefficients_float32'] = [
        {'harmonic': branch.harmonic, 'B0': float(b[0]), 'B1': float(b[1]), 'B2': float(b[2]),
             'A1': float(a[1]), 'A2': float(a[2])}
        for branch, (b, a) in zip(config.branches, branch_coefficients(config, quantized=True))]
    return json.dumps(data, indent=2, allow_nan=False)


def render_quasi_pr_c99(config: QuasiPRConfig, prefix: str = 'MULTI_PR') -> str:
    validate_float32(config)
    name = re.sub(r'[^A-Za-z0-9_]', '_', prefix).upper()
    if not name or not name[0].isalpha():
        name = 'PR_' + name
    if name.startswith('_'):
        name = 'PR' + name

    def c(value):
        return f'{float(value):.9e}F'

    init = []
    for i, (b, a) in enumerate(branch_coefficients(config, quantized=True)):
        init += [f'    p->section[{i}].coeff_{key} = {c(value)};'
                 for key, value in zip(('B0', 'B1', 'B2', 'A1', 'A2'), (*b, *a[1:]))]
    init_code = '\n'.join(init)
    orders = ', '.join(str(b.harmonic) for b in config.branches) or 'none (P only)'
    return f'''/* Parallel quasi-PR: selected harmonics {orders}.
 * Call pr_ctrl_ys_func once per Ts = {1/config.sample_rate_hz:.12g} s.
 * Fs = {config.sample_rate_hz:.12g} Hz. Input/output units are application-defined;
 * gains have output/input units. wc and wr are rad/s; ts is seconds.
 * CPU/CLA single owner: never update coefficients concurrently with Run.
 * Init before first Run; Reset on disabled control. Init clears all state.
 * updata_pr_ctrl_coeff is a BACKGROUND function (double intermediates).
 * Retuning requires a coherent stopped-controller update + Reset, not per ISR.
 * Default saturation clips output only, no anti-windup. Optional state freeze
 * discards tentative states on saturation; validate this nonlinear policy in
 * the plant. Bode describes only the unsaturated linear controller.
 * Nonfinite input/result resets states, returns zero and sets fault.
 * No sensor diagnosis, PWM writes, duty permission or protection logic here.
 * Application must check fault and perform its existing single PWM commit.
 * Do not use fast-math; target compiler/timing/hardware validation is required.
 */
#ifndef {name}_H
#define {name}_H
#include <math.h>
#include <stddef.h>
#include <stdint.h>
#ifndef POWER_CTRL_FLOAT32_T_DEFINED
#define POWER_CTRL_FLOAT32_T_DEFINED
typedef float float32_t;
#endif

typedef struct {{
    float32_t filter_out, filter_W0, filter_W1, filter_W2;
    float32_t coeff_B0, coeff_B1, coeff_B2, coeff_A1, coeff_A2;
}} {name}_IIR_2ORD_DATA_DEF;

typedef struct {{
    /* Single execution context owns this state. Value-initialize or call Init. */
    {name}_IIR_2ORD_DATA_DEF section[6];
    float32_t kp, min, max, pr_ctrl_out;
    uint16_t count, initialized, saturated, fault, freeze_on_saturation;
}} {name}_PR_CTRL_YS_DATA_DEF;

/* Pure R when kp=0; full PR when kp>0. Does not reset existing states.
 * Failure leaves coefficients unchanged. wr*ts must be below pi. */
static inline int {name}_updata_pr_ctrl_coeff(
    {name}_IIR_2ORD_DATA_DEF *p, float32_t kr, float32_t wc,
    float32_t wr, float32_t kp, float32_t ts)
{{
    double q, d, c, r;
    float32_t b0, b1, b2, a1, a2;
    if ((p == NULL) || !isfinite(kr) || !isfinite(wc) || !isfinite(wr) ||
        !isfinite(kp) || !isfinite(ts) || (kr < 0.0F) || (kp < 0.0F) ||
        (wc <= 0.0F) || (wr <= 0.0F) || (ts <= 0.0F) ||
        ((double)wr * ts >= 3.141592653589793)) {{ return 0; }}
    q = ((double)ts * wr) * ((double)ts * wr);
    c = 4.0 * wc * ts;
    d = q + c + 4.0;
    r = kr * c / d;
    a1 = (float32_t)((2.0*q - 8.0)/d);
    a2 = (float32_t)((q - c + 4.0)/d);
    b0 = (float32_t)((double)kp + r);
    b1 = (float32_t)((double)kp * ((2.0*q - 8.0)/d));
    b2 = (float32_t)((double)kp * ((q - c + 4.0)/d) - r);
    if (!isfinite(b0) || !isfinite(b1) || !isfinite(b2) ||
        !isfinite(a1) || !isfinite(a2) ||
        (1.0 + (double)a1 + (double)a2 <= 0.0) ||
        (1.0 - (double)a1 + (double)a2 <= 0.0) || (a2 >= 1.0F)) {{ return 0; }}
    p->coeff_B0=b0; p->coeff_B1=b1; p->coeff_B2=b2;
    p->coeff_A1=a1; p->coeff_A2=a2;
    return 1;
}}

/* Low-level DFII helper; caller checks finite input/output. */
static inline float32_t {name}_iir_2order(float32_t input, {name}_IIR_2ORD_DATA_DEF *p)
{{
    p->filter_W0 = input - p->coeff_A1*p->filter_W1 - p->coeff_A2*p->filter_W2;
    p->filter_out = p->coeff_B0*p->filter_W0 + p->coeff_B1*p->filter_W1 + p->coeff_B2*p->filter_W2;
    p->filter_W2 = p->filter_W1;
    p->filter_W1 = p->filter_W0;
    return p->filter_out;
}}

static inline void {name}_Reset({name}_PR_CTRL_YS_DATA_DEF *p)
{{
    uint16_t i;
    if (p == NULL) {{ return; }}
    for (i=0U; i<6U; ++i) {{
        p->section[i].filter_W0=0.0F; p->section[i].filter_W1=0.0F;
        p->section[i].filter_W2=0.0F; p->section[i].filter_out=0.0F;
    }}
    p->pr_ctrl_out=0.0F; p->fault=0U; p->saturated=0U;
}}

static inline void {name}_Init({name}_PR_CTRL_YS_DATA_DEF *p)
{{
    uint16_t i;
    if (p == NULL) {{ return; }}
    p->initialized=0U;
    for (i=0U; i<6U; ++i) {{
        p->section[i].coeff_B0=0.0F; p->section[i].coeff_B1=0.0F;
        p->section[i].coeff_B2=0.0F; p->section[i].coeff_A1=0.0F;
        p->section[i].coeff_A2=0.0F;
    }}
{init_code}
    p->kp={c(config.kp)}; p->min={c(config.output_min)}; p->max={c(config.output_max)};
    p->count={len(config.branches)}U;
    p->freeze_on_saturation={int(config.freeze_on_saturation)}U;
    {name}_Reset(p);
    p->initialized=1U;
}}

static inline float32_t {name}_pr_ctrl_ys_func(float32_t error, {name}_PR_CTRL_YS_DATA_DEF *p)
{{
    float32_t out, w0[6], y[6];
    uint16_t i;
    if (p == NULL) {{ return 0.0F; }}
    p->fault=0U; p->saturated=0U;
    if ((p->initialized != 1U) || (p->count > 6U) || !isfinite(error)) {{
        {name}_Reset(p); p->fault=1U; return 0.0F;
    }}
    out=p->kp*error;
    for (i=0U; i<p->count; ++i) {{
        const {name}_IIR_2ORD_DATA_DEF *s=&p->section[i];
        w0[i]=error - s->coeff_A1*s->filter_W1 - s->coeff_A2*s->filter_W2;
        y[i]=s->coeff_B0*w0[i] + s->coeff_B1*s->filter_W1 + s->coeff_B2*s->filter_W2;
        out += y[i];
        if (!isfinite(w0[i]) || !isfinite(y[i])) {{
            {name}_Reset(p); p->fault=1U; return 0.0F;
        }}
    }}
    if (!isfinite(out)) {{ {name}_Reset(p); p->fault=1U; return 0.0F; }}
    if (out > p->max) {{ out=p->max; p->saturated=1U; }}
    if (out < p->min) {{ out=p->min; p->saturated=1U; }}
    if ((p->saturated == 0U) || (p->freeze_on_saturation == 0U)) {{
        for (i=0U; i<p->count; ++i) {{
            p->section[i].filter_W2=p->section[i].filter_W1;
            p->section[i].filter_W1=w0[i]; p->section[i].filter_W0=w0[i];
            p->section[i].filter_out=y[i];
        }}
    }}
    p->pr_ctrl_out=out;
    return out;
}}
#endif
'''

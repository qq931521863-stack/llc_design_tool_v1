/* Parallel quasi-PR: selected harmonics 1, 3, 5, 7, 9, 11.
 * Call pr_ctrl_ys_func once per Ts = 2.5e-05 s.
 * Fs = 40000 Hz. Input/output units are application-defined;
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
#ifndef MULTI_PR_H
#define MULTI_PR_H
#include <math.h>
#include <stddef.h>
#include <stdint.h>
#ifndef POWER_CTRL_FLOAT32_T_DEFINED
#define POWER_CTRL_FLOAT32_T_DEFINED
typedef float float32_t;
#endif

typedef struct {
    float32_t filter_out, filter_W0, filter_W1, filter_W2;
    float32_t coeff_B0, coeff_B1, coeff_B2, coeff_A1, coeff_A2;
} MULTI_PR_IIR_2ORD_DATA_DEF;

typedef struct {
    /* Single execution context owns this state. Value-initialize or call Init. */
    MULTI_PR_IIR_2ORD_DATA_DEF section[6];
    float32_t kp, min, max, pr_ctrl_out;
    uint16_t count, initialized, saturated, fault, freeze_on_saturation;
} MULTI_PR_PR_CTRL_YS_DATA_DEF;

/* Pure R when kp=0; full PR when kp>0. Does not reset existing states.
 * Failure leaves coefficients unchanged. wr*ts must be below pi. */
static inline int MULTI_PR_updata_pr_ctrl_coeff(
    MULTI_PR_IIR_2ORD_DATA_DEF *p, float32_t kr, float32_t wc,
    float32_t wr, float32_t kp, float32_t ts)
{
    double q, d, c, r;
    float32_t b0, b1, b2, a1, a2;
    if ((p == NULL) || !isfinite(kr) || !isfinite(wc) || !isfinite(wr) ||
        !isfinite(kp) || !isfinite(ts) || (kr < 0.0F) || (kp < 0.0F) ||
        (wc <= 0.0F) || (wr <= 0.0F) || (ts <= 0.0F) ||
        ((double)wr * ts >= 3.141592653589793)) { return 0; }
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
        (1.0 - (double)a1 + (double)a2 <= 0.0) || (a2 >= 1.0F)) { return 0; }
    p->coeff_B0=b0; p->coeff_B1=b1; p->coeff_B2=b2;
    p->coeff_A1=a1; p->coeff_A2=a2;
    return 1;
}

/* Low-level DFII helper; caller checks finite input/output. */
static inline float32_t MULTI_PR_iir_2order(float32_t input, MULTI_PR_IIR_2ORD_DATA_DEF *p)
{
    p->filter_W0 = input - p->coeff_A1*p->filter_W1 - p->coeff_A2*p->filter_W2;
    p->filter_out = p->coeff_B0*p->filter_W0 + p->coeff_B1*p->filter_W1 + p->coeff_B2*p->filter_W2;
    p->filter_W2 = p->filter_W1;
    p->filter_W1 = p->filter_W0;
    return p->filter_out;
}

static inline void MULTI_PR_Reset(MULTI_PR_PR_CTRL_YS_DATA_DEF *p)
{
    uint16_t i;
    if (p == NULL) { return; }
    for (i=0U; i<6U; ++i) {
        p->section[i].filter_W0=0.0F; p->section[i].filter_W1=0.0F;
        p->section[i].filter_W2=0.0F; p->section[i].filter_out=0.0F;
    }
    p->pr_ctrl_out=0.0F; p->fault=0U; p->saturated=0U;
}

static inline void MULTI_PR_Init(MULTI_PR_PR_CTRL_YS_DATA_DEF *p)
{
    uint16_t i;
    if (p == NULL) { return; }
    p->initialized=0U;
    for (i=0U; i<6U; ++i) {
        p->section[i].coeff_B0=0.0F; p->section[i].coeff_B1=0.0F;
        p->section[i].coeff_B2=0.0F; p->section[i].coeff_A1=0.0F;
        p->section[i].coeff_A2=0.0F;
    }
    p->section[0].coeff_B0 = 1.570525463e-03F;
    p->section[0].coeff_B1 = 0.000000000e+00F;
    p->section[0].coeff_B2 = -1.570525463e-03F;
    p->section[0].coeff_A1 = -1.999624252e+00F;
    p->section[0].coeff_A2 = 9.996858835e-01F;
    p->section[1].coeff_B0 = 1.570331748e-03F;
    p->section[1].coeff_B1 = 0.000000000e+00F;
    p->section[1].coeff_B2 = -1.570331748e-03F;
    p->section[1].coeff_A1 = -1.999130964e+00F;
    p->section[1].coeff_A2 = 9.996859431e-01F;
    p->section[2].coeff_B0 = 1.569944434e-03F;
    p->section[2].coeff_B1 = 0.000000000e+00F;
    p->section[2].coeff_B2 = -1.569944434e-03F;
    p->section[2].coeff_A1 = -1.998144746e+00F;
    p->section[2].coeff_A2 = 9.996860027e-01F;
    p->section[3].coeff_B0 = 1.569363987e-03F;
    p->section[3].coeff_B1 = 0.000000000e+00F;
    p->section[3].coeff_B2 = -1.569363987e-03F;
    p->section[3].coeff_A1 = -1.996666312e+00F;
    p->section[3].coeff_A2 = 9.996861219e-01F;
    p->section[4].coeff_B0 = 1.568590524e-03F;
    p->section[4].coeff_B1 = 0.000000000e+00F;
    p->section[4].coeff_B2 = -1.568590524e-03F;
    p->section[4].coeff_A1 = -1.994696856e+00F;
    p->section[4].coeff_A2 = 9.996863008e-01F;
    p->section[5].coeff_B0 = 1.567624975e-03F;
    p->section[5].coeff_B1 = 0.000000000e+00F;
    p->section[5].coeff_B2 = -1.567624975e-03F;
    p->section[5].coeff_A1 = -1.992237687e+00F;
    p->section[5].coeff_A2 = 9.996864796e-01F;
    p->kp=1.000000000e-01F; p->min=-1.000000000e+00F; p->max=1.000000000e+00F;
    p->count=6U;
    p->freeze_on_saturation=0U;
    MULTI_PR_Reset(p);
    p->initialized=1U;
}

static inline float32_t MULTI_PR_pr_ctrl_ys_func(float32_t error, MULTI_PR_PR_CTRL_YS_DATA_DEF *p)
{
    float32_t out, w0[6], y[6];
    uint16_t i;
    if (p == NULL) { return 0.0F; }
    p->fault=0U; p->saturated=0U;
    if ((p->initialized != 1U) || (p->count > 6U) || !isfinite(error)) {
        MULTI_PR_Reset(p); p->fault=1U; return 0.0F;
    }
    out=p->kp*error;
    for (i=0U; i<p->count; ++i) {
        const MULTI_PR_IIR_2ORD_DATA_DEF *s=&p->section[i];
        w0[i]=error - s->coeff_A1*s->filter_W1 - s->coeff_A2*s->filter_W2;
        y[i]=s->coeff_B0*w0[i] + s->coeff_B1*s->filter_W1 + s->coeff_B2*s->filter_W2;
        out += y[i];
        if (!isfinite(w0[i]) || !isfinite(y[i])) {
            MULTI_PR_Reset(p); p->fault=1U; return 0.0F;
        }
    }
    if (!isfinite(out)) { MULTI_PR_Reset(p); p->fault=1U; return 0.0F; }
    if (out > p->max) { out=p->max; p->saturated=1U; }
    if (out < p->min) { out=p->min; p->saturated=1U; }
    if ((p->saturated == 0U) || (p->freeze_on_saturation == 0U)) {
        for (i=0U; i<p->count; ++i) {
            p->section[i].filter_W2=p->section[i].filter_W1;
            p->section[i].filter_W1=w0[i]; p->section[i].filter_W0=w0[i];
            p->section[i].filter_out=y[i];
        }
    }
    p->pr_ctrl_out=out;
    return out;
}
#endif

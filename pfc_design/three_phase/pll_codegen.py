"""Portable standalone C99 reference implementation of measured-voltage PLLs."""

from .pll import KINDS, PLLConfig


def render_pll_c99(config=None, kind="SRF"):
    config = config or PLLConfig()
    config.validate()
    if kind not in KINDS:
        raise ValueError("Unknown PLL kind")
    values = ", ".join(
        f"{getattr(config, key):.10e}f"
        for key in (
            "sample_hz",
            "nominal_hz",
            "bandwidth_hz",
            "damping",
            "minimum_hz",
            "maximum_hz",
            "nominal_peak_v",
            "initial_angle_deg",
            "lock_error",
            "lock_time_s",
            "sogi_k",
        )
    )
    return _SOURCE.replace("@CONFIG@", values).replace("@KIND@", str(KINDS.index(kind)))


_SOURCE = r"""/* Independent C99 reference; no ADC/PWM or hardware enable logic.
 * kind: 0=SRF, 1=DDSRF, 2=dual SOGI positive-sequence (UI DDSOGI).
 * bandwidth_hz is natural frequency, NOT closed-loop -3 dB bandwidth.
 * step returns the angle used for this sample; theta is the next angle.
 * Initialize once; reset after discontinuities. Call at configured sample_hz.
 */
#include <math.h>
#include <stdbool.h>
#include <stdint.h>
#include <string.h>
#define PLL_PI (3.14159265358979323846f)
typedef struct {
    float sample_hz, nominal_hz, bandwidth_hz, damping, minimum_hz, maximum_hz;
    float nominal_peak_v, initial_angle_deg, lock_error, lock_time_s, sogi_k;
} PLL_CONFIG;
static const PLL_CONFIG pll_default_config = { @CONFIG@ };
enum { PLL_DEFAULT_KIND = @KIND@ };
typedef struct {
    PLL_CONFIG c;
    int kind;
    float theta, integral, frequency_hz, error;
    float lp[4], previous_decoupled[4], sogi[2][2], previous_input[2];
    float positive_alpha, positive_beta;
    uint32_t lock_count;
    bool locked, fault;
} GRID_PLL;
static inline float pll_clamp(float x, float lo, float hi) {
    return fmaxf(lo, fminf(x, hi));
}
static inline float pll_wrap(float angle) {
    float x = fmodf(angle, 2.0f * PLL_PI);
    return (x < 0.0f) ? x + 2.0f * PLL_PI : x;
}
static inline bool pll_config_valid(const PLL_CONFIG *c) {
    return isfinite(c->sample_hz) && isfinite(c->nominal_hz) &&
        isfinite(c->bandwidth_hz) && isfinite(c->damping) &&
        isfinite(c->minimum_hz) && isfinite(c->maximum_hz) &&
        isfinite(c->nominal_peak_v) && isfinite(c->initial_angle_deg) &&
        isfinite(c->lock_error) && isfinite(c->lock_time_s) && isfinite(c->sogi_k) &&
        c->minimum_hz > 0.0f && c->minimum_hz < c->nominal_hz &&
        c->nominal_hz < c->maximum_hz && c->bandwidth_hz > 0.0f &&
        c->damping > 0.0f && c->nominal_peak_v > 0.0f && c->lock_error > 0.0f &&
        c->lock_time_s > 0.0f && c->sogi_k > 0.0f &&
        c->sample_hz >= 40.0f * fmaxf(c->maximum_hz, c->bandwidth_hz) &&
        c->sample_hz * c->lock_time_s < 4294967040.0f;
}
static inline bool pll_init(GRID_PLL *p, const PLL_CONFIG *c, int kind) {
    if ((p == NULL) || (c == NULL)) { return false; }
    if (!pll_config_valid(c) || kind < 0 || kind > 2) { return false; }
    PLL_CONFIG copy = *c;
    memset(p, 0, sizeof(*p));
    p->c = copy; p->kind = kind;
    p->theta = pll_wrap(copy.initial_angle_deg * PLL_PI / 180.0f);
    p->frequency_hz = copy.nominal_hz;
    return true;
}
static inline float pll_step(GRID_PLL *p, float va, float vb, float vc) {
    const PLL_CONFIG *c = &p->c;
    if (!isfinite(va) || !isfinite(vb) || !isfinite(vc)) {
        PLL_CONFIG copy = *c;
        int kind = p->kind;
        (void)pll_init(p, &copy, kind); p->fault = true;
        return p->theta;
    }
    p->fault = false;
    float theta = p->theta, cs = cosf(theta), sn = sinf(theta);
    float alpha = (2.0f * va - vb - vc) / 3.0f;
    float beta = (vb - vc) / sqrtf(3.0f);
    float d = alpha * cs + beta * sn, q = -alpha * sn + beta * cs;
    if (p->kind == 1) {
        float dn = alpha * cs - beta * sn, qn = alpha * sn + beta * cs;
        float c2 = cosf(2.0f * theta), s2 = sinf(2.0f * theta);
        float dec[4] = {
            d - p->lp[2]*c2 - p->lp[3]*s2,
            q + p->lp[2]*s2 - p->lp[3]*c2,
            dn - p->lp[0]*c2 + p->lp[1]*s2,
            qn - p->lp[0]*s2 - p->lp[1]*c2
        };
        float h = PLL_PI * p->frequency_hz / (sqrtf(2.0f) * c->sample_hz);
        for (int j = 0; j < 4; ++j) {
            p->lp[j] = ((1.0f-h)*p->lp[j] + h*(dec[j]+p->previous_decoupled[j]))/(1.0f+h);
            p->previous_decoupled[j] = dec[j];
        }
        d = dec[0]; q = dec[1];
    } else if (p->kind == 2) {
        float a = PLL_PI * p->frequency_hz / c->sample_hz, k = c->sogi_k;
        float denominator = 1.0f + k*a + a*a;
        float inputs[2] = {alpha, beta};
        for (int j = 0; j < 2; ++j) {
            float u = p->sogi[j][0], z = p->sogi[j][1];
            float total = inputs[j] + p->previous_input[j];
            p->sogi[j][0] = ((1.0f-k*a-a*a)*u - 2.0f*a*z + k*a*total)/denominator;
            p->sogi[j][1] = (2.0f*a*u + (1.0f+k*a-a*a)*z + k*a*a*total)/denominator;
            p->previous_input[j] = inputs[j];
        }
        alpha = (p->sogi[0][0] - p->sogi[1][1])/2.0f;
        beta = (p->sogi[0][1] + p->sogi[1][0])/2.0f;
        d = alpha*cs + beta*sn; q = -alpha*sn + beta*cs;
    }
    p->positive_alpha = d*cs - q*sn; p->positive_beta = d*sn + q*cs;
    float amplitude = hypotf(d, q);
    if (amplitude > 0.2f*c->nominal_peak_v) {
        p->error = q/amplitude;
        float wn = 2.0f*PLL_PI*c->bandwidth_hz;
        float candidate = p->integral + wn*wn*p->error/c->sample_hz;
        float raw = 2.0f*PLL_PI*c->nominal_hz + 2.0f*c->damping*wn*p->error + candidate;
        float omega = pll_clamp(raw, 2.0f*PLL_PI*c->minimum_hz, 2.0f*PLL_PI*c->maximum_hz);
        p->integral = candidate + omega - raw;
        p->frequency_hz = omega/(2.0f*PLL_PI);
        if ((d > 0.0f) && (fabsf(p->error) < c->lock_error)) {
            if (p->lock_count < UINT32_MAX) { p->lock_count++; }
        } else { p->lock_count = 0U; }
    } else { p->lock_count = 0U; p->error = 0.0f; }
    p->locked = (p->lock_count >= (uint32_t)ceilf(c->lock_time_s*c->sample_hz));
    p->theta = pll_wrap(theta + 2.0f*PLL_PI*p->frequency_hz/c->sample_hz);
    return theta;
}
"""

# FRA Loop Designer V1/V1.5/V2 Deep Audit — 2026-09-15

## 1. Purpose

This audit intentionally re-checks the FRA code path after the first rapid implementation. The goal is not to maximize feature count. The goal is to establish what is mathematically justified, what has been verified with real data, and what must remain REVIEW/disabled until stronger evidence exists.

Primary audit chain:

```text
FRA import
  -> phase convention / complex response
  -> Complete Loop de-embedding (if C_old is known)
  -> Equivalent Plant
  -> manual / Quick Tune / Auto Design
  -> raw-FRA stability analysis
  -> optional low-order rational fit
  -> fit validation
  -> optional approximate step
  -> exact H(z) / float32 C99
```

Raw measured FRA points are the authority for Fc/PM/GM/S/T/Ms/Mt. A fitted transfer function is secondary and must not overwrite or replace raw-FRA stability evidence.

## 2. Real Bode100 regression fixture

The complete uploaded hardware scan is stored as:

`power_control_tools/tests/data/bode100_real_264vac_190v_90pct.csv`

It contains 101 points from 10 Hz to 100 kHz.

Regression anchors from the file:

- 0-dB crossover: approximately 296.5910398207 Hz
- Bode100 raw phase at crossover: approximately +86.3250170159 deg
- canonical negative-feedback phase after the default -180 deg convention correction: approximately -93.6749829841 deg
- phase margin: approximately 86.3250170159 deg
- raw 0-deg / canonical -180-deg phase crossing: approximately 7616.260359845 Hz
- magnitude at that phase crossing: approximately -27.0950934129 dB
- gain margin: approximately 27.0950934129 dB

The fixture is used in automated tests, not only as a screenshot/manual reference.

## 3. FRA data-quality finding

The real Bode100 scan contains a strong local irregularity near 100 Hz. Representative points are approximately:

- 91.20 Hz: 14.97 dB / +131.66 deg raw
- 100 Hz: 24.95 dB / +75.85 deg raw
- 109.65 Hz: 13.26 dB / +51.24 deg raw

The new quality audit flags suspicious local residuals but **does not delete, smooth, or replace the raw data**. A sharp physical resonance and a bad measurement can look similar from one sweep; automatic filtering would destroy evidence.

## 4. Stability-analysis corrections

### 4.1 Multi-turn phase

SIMPLIS and other frequency responses may unwrap below -360 deg. The expression `PM = 180 + phase` is only valid on the phase branch near -180 deg. The implementation now computes the signed distance to the nearest odd-180-degree phase branch and separately enumerates every odd-180 phase crossing.

Synthetic multi-turn regression tests exercise repeated 0-dB crossings and phase trajectories below -360 deg.

### 4.2 Finite FRA window and gain margin

If the usable FRA range does not contain an odd-180-degree phase crossing, gain margin is not proven. Auto Design therefore cannot treat missing GM evidence as a PASS.

### 4.3 Multiple crossings

All 0-dB crossings are retained. Worst PM/GM are used for acceptance. The first crossover alone is not sufficient for one-click design.

## 5. De-embedding boundary

For Complete Loop TS:

`G_eq = H_scan / C_old`

Only the current controller is removed. PWM, ADC, sensing/filtering and real implementation delay remain inside `G_eq` because they are already present in the measured complete-loop response.

The reconstruction:

`H_rebuild = G_eq * C_old`

is an arithmetic consistency check only. It cannot prove that the user-entered `C_old` is the controller that actually produced the hardware sweep.

The uploaded Bode100 scan does **not** include the actual current controller parameters in the file itself. Therefore it can be used to regression-test import/stability/fitting and as a numerical stress shape for Auto Design, but it cannot by itself produce a physically valid extracted plant or production controller.

## 6. Auto Design audit

### 6.1 Acceptance is global, not local

The optimizer may satisfy local equations at the requested Fc while creating an unstable/non-robust loop elsewhere. One-click PASS now requires:

- one 0-dB crossover in the usable FRA window
- target PM (within tolerance)
- observed GM and GM >= configured minimum
- finite Ms and Ms <= configured maximum
- digital controller has no unit-circle-exterior pole

If the requested Fc fails, the search lowers Fc and retries. If no candidate satisfies all constraints, the best numerical candidate is returned only as REVIEW and GUI one-click Apply remains disabled.

### 6.2 Real Bode100 stress test caught a dangerous local-only result

Because the real Bode100 file is a complete loop with no supplied `C_old`, this is explicitly a numerical stress test, **not a hardware controller design**.

Using the measured response shape as if it were a design plant, a PI can be constructed around 1 kHz with about 60 deg local PM. However, the full sweep contains other odd-180 crossings with positive loop gain, producing negative worst GM. The hardened Auto Design therefore rejects this candidate.

This regression is specifically intended to prevent a future implementation from optimizing only the target Fc/PM point.

### 6.3 2P2Z / 3P3Z semantics

The legacy generic controller engine defines 2P2Z/3P3Z as finite equal-order pole/zero transfer functions. That is not the normal power-supply compensator meaning used by TI/C2000 practice.

Auto Design now uses dedicated power-compensator templates:

```text
Power 2P2Z = K (s+wz1)(s+wz2) / [s (s+wp1)]
Power 3P3Z = K (s+wz1)(s+wz2)(s+wz3) / [s (s+wp1)(s+wp2)]
```

This explicitly includes the integrator pole. Under Tustin the integrator maps to z=1.

Because this topology currently differs from the legacy manual generic P/Z slider semantics, Auto Power 2P2Z/3P3Z results are not silently written back into those sliders. The exact H(z) coefficients remain the authoritative result until a unified exact-H(z) host mode is added.

## 7. Rational-model fitting audit

The current V2 fitter is **not Vector Fitting**. It is a constrained nonlinear variable-projection rational approximation with stable real/complex-conjugate pole structures, up to five poles, optional pure delay, robust loss and control-frequency weighting.

This naming distinction is deliberate.

### 7.1 Real full-band result

For the complete 10 Hz to 100 kHz Bode100 record, a <=5-pole model cannot faithfully explain the entire scan. Independent smoke calculations found approximately:

- selected order: 5
- magnitude RMS error: ~1.61 dB
- magnitude maximum error: ~13.8 dB
- phase RMS error: ~9.9 deg
- phase maximum error: ~57.4 deg
- confidence: LOW

The software/tests require this case to remain LOW confidence. It must not be presented as a reliable time-domain model.

### 7.2 Control-band result

Restricting the same real response to approximately 150 Hz to 15 kHz gives a much closer low-order approximation. Independent smoke calculations found roughly:

- magnitude RMS error: ~0.13 dB
- phase RMS error: ~1.1 deg
- focus-region errors below ~1 dB / a few degrees
- confidence: GOOD/FAIR depending on optimizer start/tolerance

This is evidence that low-order local fitting can be useful for loop-shape interpretation, but it is still not physical component identification.

### 7.3 Step-response gate

A good local Bode fit does not automatically justify a closed-loop step prediction. For the 150 Hz to 15 kHz control-band fit, the low end is only about 2x below the ~296.6 Hz crossover. DC/low-frequency behavior is therefore weakly constrained.

Fit-derived Step is now gated by both control-critical agreement and measurement bandwidth coverage. Default requirements include approximately:

- `Fc / Fmin >= 10`
- `Fmax / Fc >= 5`

Narrow local fits may be useful for Fc/PM approximation but are not automatically authorized for time-domain Step.

## 8. Delay and numerical conditioning

Optional fitted delay remains an explicit `exp(-s Td)` term in frequency-domain fitting. First-order Pade is used only for optional fitted-model root/step analysis.

Very small optimizer delay values are canonicalized/ignored before Pade conversion so numerical noise cannot create absurd ultra-high-frequency fake poles/zeros.

Closed-loop transfer polynomials are trimmed and normalized before calling SciPy time-domain routines to avoid artificial badly-conditioned leading-zero coefficient representations.

## 9. C99 verification

An Auto-designed Power 2P2Z regression now exports its exact digital H(z) through the existing float32 DF2T/SOS generator and executes the generated C code against the Python reference for impulse and step sequences. This is intended to verify the chain:

`Auto Design -> exact H(z) -> SOS -> C99 float32 -> executed response`.

## 10. Remaining limitations before release-grade claim

These are not hidden:

1. The real Bode100 complete-loop file still needs the actual firmware `C_old` to perform a physically valid de-embedding + new-controller hardware-design smoke test.
2. Auto Power 2P2Z/3P3Z exact H(z) is not yet unified with the legacy generic manual P/Z slider model; silent conversion is deliberately blocked.
3. PREWARP_TUSTIN in Auto Design still needs explicit design-crossover prewarp handling or should be hidden from Auto UI before release use. Default Tustin is the audited path.
4. Bode100 redundant Real/Imaginary columns are not yet used as an importer consistency diagnostic, although an independent check on the supplied file showed consistency to numerical precision.
5. Duplicate-frequency handling currently keeps the first sample deterministically; the quality layer should eventually report duplicates explicitly instead of silently normalizing them.
6. Ms/Mt are calculated on the measured frequency grid; very narrow peaks between sparse FRA points can still be underestimated. Point density/quality must remain part of the engineering judgment.
7. This V2 backend is constrained nonlinear rational fitting, not a production-grade Vector Fitting implementation.
8. V2.5 multi-operating-point robust design is intentionally deferred.

## 11. Release rule

Do not merge the audit PR merely because unit tests are green. Merge only after:

- latest audit branch CI is green,
- real Bode100 regression anchors pass,
- Auto global-constraint stress test passes,
- rational-fit confidence/step gating behaves as documented,
- exact H(z) C99 runtime verification passes,
- GUI smoke tests pass,
- remaining limitations are accepted or fixed,
- user performs the final GUI/hardware-oriented review.

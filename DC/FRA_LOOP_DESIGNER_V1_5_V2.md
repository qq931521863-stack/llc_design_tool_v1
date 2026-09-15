# FRA Loop Designer V1.5 / V2 — Design Contract

## V1.5 — Automatic controller design from raw FRA

### Goal

Reduce the field workflow to:

`Complete Loop FRA -> remove C_old -> Equivalent Plant -> choose controller -> enter Fc/PM -> AUTO DESIGN -> H(z)/B/A/C99`

The automatic design engine MUST use the original measured Equivalent Plant complex points as the stability authority. It does not require rational plant fitting.

### User inputs

- Controller type: PI / PIF / PID / 2P2Z / 3P3Z
- Digital sample rate `Fs`
- S-to-Z method
- Requested crossover frequency `Fc_target`
- Requested phase margin `PM_target`
- Minimum gain margin constraint
- Maximum sensitivity peak `Ms`

### Automatic fallback

The requested crossover is tried first. If the selected controller structure cannot satisfy the requested phase margin / robustness constraints, the engine progressively lowers crossover frequency and retries.

The result must report both:

- Requested `Fc`
- Achievable/selected `Fc`

A fallback design must never be presented as if the original target was met.

### Acceptance checks

Every candidate is evaluated on raw FRA points for:

- Main and all 0-dB crossovers
- Phase margin / worst phase margin
- Gain margin when the phase crossover is actually observed in the usable FRA range
- Multiple crossover rejection
- `S = 1/(1+L)` and `Ms=max|S|`
- `T = L/(1+L)` and `Mt=max|T|`
- Digital controller pole stability
- Nyquist / valid-frequency limit

If a phase crossover is not observed in the usable FRA window, GM is reported as `not observed`; the engine may still produce a candidate but must not claim measured GM proof.

### GUI

The FRA workspace exposes an `Auto Design` action. The result can be applied back into the existing `New Structure` controls so the user can continue manual Slider tuning after the one-click result.

---

## V2 — Rational model identification from FRA

### Goal

Create an optional interpretable low-order continuous model that approximates measured complex frequency response for model-based analysis beyond direct FRA margins.

Raw FRA remains the primary stability authority.

### Model form

The rational model is limited to 1..5 poles and uses:

- Stable real poles
- Stable complex-conjugate pole pairs parameterized by natural frequency and damping ratio
- Real numerator coefficients
- Optional pure delay `exp(-s*Td)`

The internal polynomial variable is normalized (`x=s/w_ref`) to improve numerical conditioning over wide frequency spans.

### Fitting method

For each candidate model order:

1. Try available real-pole / complex-pair structures.
2. Optimize stable pole frequencies, damping ratios and optional delay.
3. Solve numerator coefficients by weighted real-valued least squares against the complex FRA points.
4. Prefer the lowest order that reaches GOOD confidence.
5. Otherwise return the lowest-error model through order 5 and mark FAIR/LOW confidence.

The cost works on complex relative error; magnitude and phase are not independently fit as unrelated curves.

### Control-region weighting

The user may provide a control focus frequency (normally current/target crossover). The fit gives additional weight around roughly +/- 0.5 decade of that region while still fitting the complete measurement band.

### Fit evidence

Report:

- Selected model order
- Real poles
- Complex pole pairs `(fn, zeta)`
- Zeros
- Fitted delay
- Gain RMS / max error
- Phase RMS / max error
- Control-focus gain / phase error
- Confidence = GOOD / FAIR / LOW

Measured and fitted Bode curves must be shown together.

### Fit-derived step

When the fit target is the *open-loop response*, an approximate closed-loop step may be calculated from:

`T(s) = L_fit(s) / (1 + L_fit(s))`

The pure delay is replaced by a first-order Pade approximation only for this time-domain estimate. The GUI must label this as fit-derived / approximate and require hardware validation before release use.

### Explicit boundary

The identified model is an engineering approximation of the measured input-output frequency response. It is not automatically interpreted as the physical power-stage component topology and must not replace the raw FRA result for final Fc/PM/GM reporting.

---

## Deferred V2.5

Multi-operating-point robust design / uncertainty envelopes are deliberately deferred. V1.5 and V2 operate on one FRA operating point at a time.

# FRA Loop Designer V1 — Frozen Design Contract

## Goal

Use imported FRA/Bode frequency-response data to redesign a digital power controller without requiring a complete analytical power-stage model.

## Supported input sources

- Bode100 CSV
- SIMPLIS TXT (`freq / Gain / Phase`)
- Generic three-column frequency / gain-dB / phase-deg text or CSV

## TS semantics

### Mode A — Plant TS

The imported data already excludes the controller:

`L_new(jw) = G_plant(jw) * C_new(jw)`

### Mode B — Complete Loop TS

The imported data contains the complete measured loop including the current controller, power stage, PWM, ADC, sensing/filtering and real system delays:

`H_scan(jw) = C_old(jw) * G_eq(jw)`

Only the current controller is removed:

`G_eq(jw) = H_scan(jw) / C_old(jw)`

PWM, ADC, sensing and delays MUST NOT be added again because they are already embedded in the measured TS.

The software immediately rebuilds the original loop:

`H_rebuild(jw) = G_eq(jw) * C_old(jw)`

and reports magnitude/phase reconstruction error.

**Important validation boundary:** this reconstruction check is an algebraic/software consistency check. It verifies the complex division/multiplication chain and numerical implementation. It cannot prove that the controller parameters entered by the user are the same parameters actually running in the measured hardware; a wrong `C_old` will mathematically divide out and multiply back in. Hardware controller provenance therefore remains a required engineering input.

## Existing controller input

V1 supports:

1. Exact digital B/A coefficients — preferred.
2. PI `Kp + Ti` plus `Fs` and the exact S-to-Z method.

Canonical coefficient convention:

`H(z) = (b0 + b1 z^-1 + ...)/(1 + a1 z^-1 + ...)`

`y[n] = sum(b[k] x[n-k]) - sum(a[k] y[n-k])`

For firmware that stores the recurrence as `y[n] = sum(b[k]x[n-k]) + sum(A[k]y[n-k])`, the GUI provides an explicit `Firmware +A` coefficient convention and converts `a[k] = -A[k]`. The software must never silently guess the feedback sign convention.

## Controller tuning modes

### Quick Tune — preferred for field/debug work

When Complete Loop TS is selected, the current controller is the baseline. Unity tuning scales reproduce the measured loop exactly within numerical precision.

For exact B/A input, Quick Tune operates directly on the existing digital controller without attempting controller-structure identification:

- Global numerator gain scale `Kx`
- Per-numerator coefficient scales `b0x ... b3x`
- Per-feedback coefficient scales `a1x ... a3x`

This is intended for common PI / 2P2Z / 3P3Z firmware controllers up to third order. Coefficient signs are preserved by the normal positive slider range; Expert entry remains available for exact coefficients.

For existing `PI Kp + Ti`, Quick Tune exposes only:

- `Kp / loop-gain scale`
- `Ti scale`

The same sample rate and discretization method are retained.

### New Structure — controller redesign

Reuse the existing `power_control_tools` controller engine:

- PI
- PIF
- PID
- 2P2Z
- 3P3Z

Parameter changes are performed with the existing SliderSpin widgets and trigger a debounced real-time recalculation.

## Loop outputs

For every controller change:

- New open-loop Bode
- Main crossover frequency
- All 0-dB crossovers
- Phase margin
- Gain margin
- Worst PM / GM
- Sensitivity `S = 1/(1+L)`
- Complementary sensitivity `T = L/(1+L)`
- `Ms = max |S|`
- `Mt = max |T|`

Multiple gain crossovers are reported explicitly as a warning condition.

If the measured frequency range does not contain a phase crossover, gain margin is **not proven**; the result must be marked for review instead of silently treating GM as satisfied.

## Bode100 phase convention

The Bode100 importer preserves the raw phase and defaults to a `-180 deg` loop-injection correction. The GUI exposes the phase offset so the user can override the convention when required by a different injection setup.

The provided hardware sample is used as a regression anchor around its measured cursor: approximately `296.591 Hz / 0 dB / +86.325 deg` raw Bode100 phase. After the default `-180 deg` convention correction, the loop phase is approximately `-93.675 deg` and PM remains approximately `86.325 deg`.

## Digital-frequency limit

Controller-based stability analysis is limited to `0.49 * Fs` (and to the lower old/new controller limit during de-embedding). Imported measurement data above this limit remains visible for context but is excluded from computed margins.

For Complete Loop TS, the extracted Equivalent Plant is only presented as valid below the existing controller's `0.49 * Fs` limit. Above that point the imported measurement can still be shown for context, but the controller-divided plant must not be presented as a trusted extracted plant.

## Output

- Exact final H(z)
- B/A coefficients
- Existing float32_t C99 DF2T/SOS export and host verification

## V1 boundary and later extensions

V1 itself remains the measured-FRA de-embedding / manual tuning baseline. The next layers are defined in:

- `DC/FRA_LOOP_DESIGNER_V1_5_V2.md`

V1.5 adds target-Fc/PM automatic controller synthesis with automatic crossover fallback. V2 adds optional 1..5 pole rational identification, fit quality grading and fit-derived advanced analysis. Raw FRA remains the primary stability authority in all versions.

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

## Existing controller input

V1 supports:

1. Exact digital B/A coefficients — preferred.
2. PI `Kp + Ti` plus `Fs` and the exact S-to-Z method.

Canonical coefficient convention:

`H(z) = (b0 + b1 z^-1 + ...)/(1 + a1 z^-1 + ...)`

`y[n] = sum(b[k] x[n-k]) - sum(a[k] y[n-k])`

## New controller design

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

## Bode100 phase convention

The Bode100 importer preserves the raw phase and defaults to a `-180 deg` loop-injection correction. The GUI exposes the phase offset so the user can override the convention when required by a different injection setup.

## Digital-frequency limit

Controller-based stability analysis is limited to `0.49 * Fs` (and to the lower old/new controller limit during de-embedding). Imported measurement data above this limit remains visible for context but is excluded from computed margins.

## Output

- Exact final H(z)
- B/A coefficients
- Existing float32_t C99 DF2T/SOS export and host verification

## Explicitly out of V1

- Rational/vector plant fitting
- FRA-derived step response
- Auto-tune / optimization
- Thermal model
- Automatic controller-structure identification from B/A coefficients

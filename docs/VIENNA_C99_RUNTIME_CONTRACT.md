# Vienna C99 startup and restart contract

The export is a host-testable C99/float32 algorithm. It consumes already calibrated
engineering-unit measurements and returns semantic modulation/zero-state duty
commands. It does not configure C2000 ADC, ePWM, CMPSS, Trip Zone, interrupt
routing, or board protections. The BSP still owns measurement validity, output
enabling, safe-off, and the final hardware update.

## Initial controller state

The nominal input conductance is

`G0 = Pout / (efficiency * Vline_line_rms^2)` in A/V.

Both `Vienna_Init` and `Vienna_Reset` restore:

- Current A/B/C and midpoint-balance controller histories to zero.
- Voltage `out_prev`, `y1`, and `y2` to `G0`.
- PI/PIF voltage `i_state` to `G0 / max(Kp, 1e-12)`; other histories to zero.
  The small-gain floor matches the Python waveform controller constructor.
- `gcmd` to `G0`; balance, references, previous references, inductor-feedforward
  values, and current-loop output histories to zero.
- Each slow-task divider counter to its configured divider minus one.

The preload is stored controller state, not a feedforward term added on every
update. For ordinary positive `Kp >= 1e-12`, the zero-error PI/PIF voltage output
starts at `G0` (subject to configured limits and float32 rounding). For general
2P2Z coefficients, the first zero-error output is
`clamp(-(a1 + a2) * G0, output_min, output_max)` and later outputs follow the
specified recurrence. Arbitrary imported poles must not be forced to hold `G0`.
For `Kp < 1e-12`, the constructor's denominator floor means the unfiltered
zero-error PI value is `Kp * G0 / 1e-12`, rather than exactly `G0`.

## First sample and multirate phase

Call `Vienna_ControlStep` once per configured current-loop sample. Let `k = 0`
be the first enabled call after initialization, explicit reset, or a disabled
call. Each slow task executes when `k mod divider == 0`:

1. Voltage controller updates `gcmd`.
2. Midpoint-balance controller updates the balance command.
3. Current-reference task uses the current `gcmd`, then updates inductor
   feedforward from the reference difference over its own sample period.
4. All three current controllers run, followed by semantic modulation outputs.

Thus all tasks execute on the first enabled call, matching the Python waveform
scheduler's initial `next_i = next_r = next_v = next_b = 0`. Subsequent task
periods remain unchanged. A disabled call resets the state and returns an
all-zero output structure; repeated disabled calls do not advance the restart
phase. The meaning of disabled output fields at hardware pins is BSP-owned.

## Verification boundary

`power_codegen/tests/test_vienna_startup_runtime.py` compiles the generated
runtime, Vienna implementation, ISR template, and an observation wrapper with
strict C99 diagnostics. It executes PI, PIF, integrating 2P2Z, and nonintegrating
2P2Z voltage controllers at divider combinations `(1,1,1)`, `(2,5,5)`, and
`(3,4,5)` for reference/voltage/balance tasks. It checks every named state member
at initialization and after reset/disable, zero-error startup, first/restart
task phase, and controller impulse, step, random, saturation, and recovery
sequences. A separate scalar float32 oracle derives controller equations and
uses sample-index modulo scheduling instead of generated counters. Full
control-step comparisons cover all 16 output fields with deterministic inputs,
mid-sequence reset, repeated disable, re-enable, and saturation excursions.

These checks establish the exported controller-state and scheduling contract.
They do not establish complete Python/C time-domain waveform equivalence: the
Python simulator additionally includes sensing chains, plant integration, and
other timing behavior. They do not prove closed-loop stability, physical
startup safety, timing budget, compiler equivalence on C2000, or hardware
readiness. Non-finite or invalid physical measurements still require BSP
validation; this change adds no sensor-fault handling. The ADC/model-evidence
export gate, explicit draft override, and analyzed-controller/source identity
checks remain in force.

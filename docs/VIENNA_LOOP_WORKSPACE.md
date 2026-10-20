# Vienna loop workspace: runnable fan-out checkpoint

This is development software-model evidence, not formal release or hardware
qualification. The supported main model remains ABC stationary-frame current
feedback plus a conductance-command bus-voltage loop. Midpoint balance remains
an explicitly configured auxiliary local-gain approximation. No dq model is
claimed or created by renaming these loops.

## Working consumer path

The Vienna page now has explicit current/reference/voltage/balance firmware rates,
controller-rate identity validation, selectable per-loop Bode plots/cursors and
phase budgets. Every loop owns a strictly sub-Nyquist sample grid. The bus budget
contains its command hold, closed-current response, pure bus factor and sense
exactly once. The midpoint budget contains its command hold exactly once.

`Loop workflow / Session` provides:

- Exact Tustin PI synthesis from Fc/PM, preview and explicit Apply to each of the
  actual three controllers. The generated coefficients are retained exactly,
  including precision beyond displayed spinbox decimals.
- Real retained operating points; one unchanged candidate is checked against
  selected points. Stage/sense/timing and fixed-inner-controller compatibility
  controls which snapshots remain applicable.
- Fc/PM scans on a worker, cooperative per-cell cancellation and late-result
  rejection on edits, cancellation and new sources. No GUI widget is accessed
  from the numerical worker.
- Exact imported ascending-z^-1 coefficients through order 2, including explicit
  sample period. Unsupported orders and clock changes are rejected rather than
  truncated or fitted. General-order imported controllers are not supported by
  the maintained Vienna firmware/runtime in this checkpoint.
- Complete typed input/session serialization, hidden precision preservation,
  retained-model replay, target/constraint restoration, bounded strict JSON and
  preflight atomic failed-load behavior. Restored candidate records are history;
  fresh analysis/candidate generation/explicit Apply are required.
- Actual report JSON/Markdown, one complex-sample CSV per loop, standalone Python
  and MATLAB research files for each loop, plus the existing C99 export. There
  are 11 report/research files. Python scripts verify their controller
  coefficients and feedback identities independently. MATLAB was generated but
  not executed in this environment.

Edits invalidate accepted results and export/candidate authority. Bode plots
left visible are explicitly historical and disabled. Modal export selection
rechecks immutable result identity, current inputs, busy state and closure.
The line-cycle solver offers cooperative cancellation before allocation and
at 256-step intervals; no partial result is published.

## Input and ADC contracts

Legacy values are not silently migrated. At the legacy 700 V bus, 117 kΩ/1.6 kΩ
split sensing produces 4.721754 V nominally and 4.748735 V on the initially
higher half bus, exceeding 3.3 V. Legacy 400 V line-to-line Vabc gain 1/180
requires a 3.628874 V bipolar span, which cannot fit inside 3.3 V at any DC bias.
A public pure `operating_adc_range_warnings(config)` recomputes these necessary
range checks from configuration rather than trusting old result warning text.
Known overrange blocks candidate synthesis/Apply and GUI C99 export.

The explicit new example uses 249 kΩ/1.6 kΩ split sensing and 1/250 Vabc gain.
It only passes the declared nominal/initial magnitude/span checks. Iabc/Vabc
bias is unknown, not assumed to be Vref/2. Board-specific offset, polarity,
noise, transient and clipping behavior remain unqualified.

Nonfinite values, wrong field types, invalid controller output limits,
controller/firmware clock mismatch and insufficient sampled bands are rejected.
The supported controller model requires calibrated engineering-unit sensing;
unmodeled raw-ADC controller units are not accepted implicitly.

## Reproducible validation

Focused tests are `pfc_design/tests/test_vienna_loop_contracts.py` and
`pfc_design/tests/test_vienna_workspace.py`, plus Vienna selections in
`llc_design/tests/test_phase_budget_gui.py`. The earlier owner-only checkpoint passed 27 selected cases. It covers independent complex bus equations,
all crossing retention, missing-margin labels, ADC counterexamples, actual Qt
candidate application, worker cancellation/repeated edits, exact fresh-process
responses, strict/atomic failed loads, standalone research script execution and
modal C99 source changes. This is not a full-repository qualification.

A real offscreen Qt pipeline replay ran four full light configurations
(three line cycles, 200 kHz integration, 500 Bode samples):

- Current Apply: Fc 1500.000855 Hz, PM 59.999936°
- Bus-voltage Apply: Fc 20.000220 Hz, PM 64.999564°
- Auxiliary midpoint Apply: Fc 50.000387 Hz, PM 59.999812°

All three explicitly acknowledged conditional candidates because Fsw is outside
the valid sampled band. No extrapolated Fsw attenuation or pole/Nyquist proof
was substituted. Exact controller values reached the actual waveform/Bode
consumer, fresh-window config replay was identical, the report/research files
were written and generated C99 passed GCC C99 syntax checking with
`-Wall -Wextra -Werror`. This does not establish TI executable equivalence.
Later independent shown-window checks found native-canvas scrolling and
zero-height/clipped candidate plots. The repaired workspace keeps PLL/candidate
canvases outside whole-page scroll areas, scrolls parameter controls only, and
focuses the candidate page while restoring prior diagram/input visibility on
exit. Real 1280×720 and 1366×768 regression checks include all canvas bounds and
Qt callback exceptions. Hidden candidate page hints no longer enlarge the active
Bode page. Final-source independent status is in
[stage validation](CONTROL_FANOUT_VALIDATION.md).

Generated C99 now restores the same explicit voltage-controller preload and
first-tick divider phase as the waveform controller. Independent compiled and
actual-waveform-operand checks cover PI, PIF and general 2P2Z, Reset and
re-enable; see [the exact runtime boundary](VIENNA_C99_RUNTIME_CONTRACT.md).

## Important remaining model boundaries

- `closed_loop_stability=NOT_PROVEN` for these mixed-domain loops. Positive
  finite-band margins are only a screen. All signed crossings remain visible;
  missing PM/GM never becomes a stable label.
- The bus approximation uses feedback-only closed current transfer. Reference
  update hold/delay, reference R/L feedforward and line-periodic interactions
  are explicitly excluded from that approximation. A full lifted bus model is
  still required before claiming timing-equivalence to the multirate runtime.
- The existing line-cycle solver does not apply all Bode computation/PWM delays;
  its ADC-delay discretization and current-polarity/diode reachability around
  zero crossing, reversal and saturation need separate physical corrections.
  Switching reconstruction is derived from that averaged waveform, not an
  independent switched-circuit validation.
- The current line-cycle bus update uses grid-side input power and does not
  yet share the small-signal model's complete copper-loss and inductor-energy
  chain. This is a real model-consistency gap. Frequency-domain bus factors and
  time-domain power/settling results are not claimed physically equivalent.
- Per-phase mismatch diagnostics are waveform diagnostics; the current
  small-signal result is a representative ABC channel, not a coupled MIMO
  mismatch stability proof.
- A Vienna dq option requires independently defined admissible modulation,
  polarity/sector and midpoint coupling, plus a real dq controller/runtime
  consumer. It is not implemented in this checkpoint. The existing ABC mode
  and its limitations are retained.

### Compact-screen result focus

Non-Bode result pages temporarily collapse the overview diagram
and hide Bode-only diagnostics so stacked waveforms and candidate comparisons
retain usable height. Returning to a Bode page restores the prior diagram choice.
The candidate page separately preserves/restores the parameter inspector choice.
Full-screen diagram inspection remains available. Long cursor/phase-budget text
is retained in a bounded scrollable diagnostic area rather than shrinking the
chart; Bode layout recomputes on canvas resize. These UI changes do not change
model arrays or controller coefficients.

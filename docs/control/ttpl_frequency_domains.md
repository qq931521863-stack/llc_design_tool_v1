# TTPL frequency-domain contract

A requested Bode grid is not permission to evaluate a sampled loop above its
principal frequency band. `PFCControlLabConfig.frequency_start_hz`,
`frequency_stop_hz`, and `frequency_points` remain the user's request, including
when saved and reopened. Analysis retains original grid samples strictly below
all participating sampled blocks' Nyquist bounds:

- Current: firmware current rate, current controller rate, current sensing rate
- Voltage: all current-loop bounds, voltage firmware/controller/sensing rates,
  and the AMC rate

Each `LoopResult` owns `frequencies_hz` and `nyquist_hz`. Every response in that
result uses precisely that grid. `analysis.frequencies_hz` is the compatibility
alias for the current-loop grid; it must not be paired with voltage responses.
The voltage grid is a prefix of the current grid, preserving exact inner-loop
samples in the outer plant without interpolation or model regeneration.

For the default 0.5–20000 Hz / 1600-point request, the current loop retains all
1600 points while the 10 kHz voltage loop retains 1390 points through
4973.153599868946 Hz. With 40 kHz current sampling, the equal-to-Nyquist 20000 Hz
endpoint is excluded. Neither case rewrites the requested stop frequency.
Review, analysis warnings, JSON summaries, and research-export metadata disclose
the requested/actual domains and their sampling constraints. A domain with fewer
than two distinct requested points is rejected before analysis. Candidate maps
retain their existing minimum-three-point and qualification gates. Typed
controller sample times must agree with their corresponding firmware loop rates.

Loop-specific domains feed margins, peaking, phase budgets, Bode/cursor views,
candidate sources and evidence signatures, and CSV/plot exports. A combined
research file uses the common valid domain because its format has one frequency
axis; a current-only research file retains the wider current domain. Exported
samples are original complex values, not fitted or interpolated. Exact H(z)
coefficients and time-domain models are unchanged. A frequency-domain result or
missing crossover is not a proof of closed-loop stability.

## Counterexample

For the native default configuration with only outer PI Kp changed to 1000,
the old 20 kHz outer grid includes a gain crossing near 7981.95 Hz and a phase
crossing near 14205.66 Hz, both beyond the outer-loop 5 kHz Nyquist bound. The
valid band contains no gain crossing and retains the phase crossing near
2725.72 Hz with signed GM about −29.47 dB. The old margin helper's positive-first
scalar GM policy selected an out-of-band +14.31 dB while its complete crossing
list also contained that negative GM. Tests check complete valid crossing lists,
not only scalar summaries. Signed negative GM means the critical gain scaling
is below unity; it does not independently establish closed-loop instability.

The shared margin helper's scalar selection convention is not changed by this
frequency-domain repair. Consumers must not treat the scalar as a full stability
proof. Saved model revision is advanced so historical snapshots are replayed
under the explicit principal-band contract.

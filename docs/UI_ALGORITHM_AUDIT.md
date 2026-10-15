# UI and algorithm audit — October 2026

This audit covers desktop result integrity, DAB local modeling, LLC result
rendering and author attribution. All numerical evidence below is software
evidence; no new hardware measurements were performed.

## Confirmed faults and fixes

| Finding | Observed consumer chain | Correction and verification |
|---|---|---|
| Control Tools Type-II/III calculation errors | Qt returns a string from the controller selector; the schematic expected an enum and accessed `.value` | Normalize the enum at the boundary. Exercise all 14 controller entries with all three discretization methods. |
| Backward-Euler PID response error | Causal coefficients in powers of z⁻¹ were passed to a polynomial-degree impulse-response API | Use the same `lfilter(b, a)` convention as the implementation. Independent FIR and explicit-delay impulse cases verify timing. |
| Previous coefficients remained exportable after a failed calculation | The failed recalculation left `current_digital` and old coefficient/code text intact; the toolbar export bypassed the disabled panel button | Invalidate on input edits and before recalculation; guard the export method itself, clear failed plots/code, and synchronize both export controls. |
| Older PFC workers overwrote newer results | Every worker result signal reached the same callback, regardless of submission order | A monotonically increasing request ID gates results, errors and busy-state completion. Tests deliver signals in both completion orders. |
| DAB Cp small-signal command gain was zero | Small TPS phase perturbations did not cross the FFT waveform sampling grid; differentiation therefore saw identical waveforms | Propagate the series-RLC state analytically between switching events, solve the periodic boundary, and integrate transferred charge. Compare against an independent matrix-exponential solution. |
| DAB frequency and time models used different discrete plants | The Bode plant used ZOH while the time simulation advanced capacitor energy with forward Euler | Derive the discrete linear plant from that same energy update. Numerical Jacobians and a small reference step agree with the nonlinear simulation. Two-level PFC retains its existing ZOH model. |
| LLC three-phase interleaved display crashed | The dedicated shared-rectifier model provides `i_secondary_p1`…`p3`; the view requested nonexistent `i_rectified_p1`…`p3` | Plot the signed secondary winding currents and actual total rectifier current, with explicit labels. Both actual two- and three-phase model bundles render successfully. |
| LLC global Run Current ignored visible settings | It hard-coded two phases and empty option dictionaries for several pages | Route through each page's existing run control so phase count, bus voltage, load and sample settings are retained. Block reentry while an LLC worker is active. |

The LLC three-phase fault occurs **after** the numerical solve, in the result
consumer. Default full-design, transformer, small-signal, digital-loop, SR and
waveform calculations were also exercised. The repaired default three-phase HB
GUI path completed calculation and display in a separate manual reproduction.
This identifies one concrete LLC failure; a different project-specific error
still requires its parameters and traceback.

## Additional UI integrity fixes

- DAB waveform playback now identifies the page by widget, so English tab
  captions do not disable animation. A selection made during a solve queues the
  latest changed configuration.
- DAB optimization has a separate feasibility comparison table, explicit
  infeasible status and frozen input context. A search result cannot retain an
  old loop's Bode plot or C99 code.
- PFC loss reports reject exports after parameter changes or calculation
  failure. Vienna-only fields are hidden for two-level PFC.
- Failed magnetic imports/predictions clear old export state. Prediction input
  changes invalidate predictions without invalidating independent measurements.
- The launcher and all five workspaces show a dedicated bilingual contact
  card: **杨帅 / Yang Shuai**, clickable email and copyable WeChat ID. The
  launcher scrolls workspace cards while keeping contacts visible. Chinese,
  English, Japanese and Korean help/about pages, README and generated report
  attribution use the same author name.

![Author contact card](assets/author-contact-bar.png)

## Numerical checks and boundaries

The former Cp-map gain at 400 V / 3500 W was zero. The event-based periodic map
gives **∂Pout/∂Pcmd = 1.0281313**. Analytic segment propagation agrees with an
independent SciPy matrix-exponential calculation for underdamped, critically
damped, overdamped and zero-resistance cases. Finite differences at independent
perturbation sizes verify the derivative rather than merely repeating the same
stencil.

For the default ideal-L voltage loop, the matched discrete model gives a phase
margin of **84.34°** at **40.14 Hz**. With Cp enabled, the corresponding values
are **84.52°** and **41.23 Hz**. Full inputs and results are recorded in
[control_validation.json](benchmarks/control_validation.json). Cp remains a
periodic steady-state power map; its transient states are not integrated into
the outer-loop simulation. Singular or ill-conditioned LC periodic boundaries
and command-clipped linearization stencils are rejected explicitly.

The TPS optimizer now enforces the requested relative power tolerance even
below 1 W, rejects nonfinite physical metrics, and keeps quantized phases
inside their legal range when the PWM period contains an odd number of ticks.
Its local exact-float cache avoids repeated physical evaluations requested by
the objective and constraints. Across three 400 V cases, three alternating-order
repeats with a 5³ grid gave median speed ratios of **1.09×, 1.76× and 2.52×**;
the final PWM candidates were identical to the pre-audit implementation.
These are limited cache/repeated-computation results, **not AI speedups** or
global-optimality evidence. See [the recorded benchmark](benchmarks/dab_cache_audit.json).

The faults above are VERIFIED for the recorded cases. Behavior outside the
tested parameter ranges and correspondence to hardware remain UNKNOWN. In
particular, this audit does not establish device-level ZVS, Cp transient
stability, thermal accuracy or global TPS optimality. A counterexample to the
independent periodic solve, one-step Jacobian, request-order or export-state
tests would invalidate the corresponding correction claim.

## Reproduction and test status

```bash
OPENBLAS_NUM_THREADS=1 python tools/benchmark_controls.py
OPENBLAS_NUM_THREADS=1 python tools/benchmark_dab_cache.py
QT_QPA_PLATFORM=offscreen OPENBLAS_NUM_THREADS=1 python -m pytest \
  dab_design/tests pfc_design/tests/test_gui_request_order.py \
  pfc_design/tests/test_three_phase_dq.py pfc_design/tests/test_three_phase_performance.py \
  pfc_design/tests/test_pll_comparison.py pfc_design/tests/test_v7_vienna.py \
  power_control_tools/tests llc_design/tests/test_llc_calculation_gui.py \
  llc_design/tests/test_digital_loop.py llc_design/tests/test_help.py \
  llc_design/tests/test_i18n.py llc_design/tests/test_v8_sr_dcm_multiphase.py \
  llc_design/tests/test_v8_3_parameter_multiphase.py -q -o addopts=''
```

The recorded combined run completed with **479 passed, exit code 0**, in
102.67 s. It reported 117 font/glyph warnings in the headless environment.
Separate smaller Qt runs also reproduced the previously recorded intermittent
segmentation fault during QApplication teardown, including at
`llc_design/tests/conftest.py:21`; this is not a clean full-suite qualification.
Its root cause remains unresolved. Real UI captures with the application font
setup were visually inspected. Earlier full-suite dependency and baseline test
limitations remain documented in [regression.md](benchmarks/regression.md).

New functionality is in the source tree. Existing downloaded binaries require
a subsequent packaged release to include these changes.

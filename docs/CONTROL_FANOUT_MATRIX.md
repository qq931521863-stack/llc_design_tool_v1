# Control-workspace fan-out

> Submission checkpoint (2026-10-20): installed wheel and original Linux frozen gates pass; source coverage accounting and long/native checks are not complete. Release paused. See [validation status](CONTROL_FANOUT_VALIDATION.md#submission-checkpoint-2026-10-20-0707-utc).

## Scope and baseline

This work follows the request to extend the mature LLC and Totem-Pole PFC loop
workflows to DAB, Vienna and three-phase PFC, including actual model consumers,
not only common-looking plots. Baseline: `a74e225b0afd9163823e8e5de5c954baf9022f46`.
The baseline Guided-loop qualification remained on an unchanged checkout, but its full run was interrupted at 66% and is INCOMPLETE. This
fan-out is a separate development checkpoint; no topology is claimed fully qualified by this document.

Common acceptance path: operating point and declared power-stage model → explicit
sensing/sample/command timing → exact controller → valid-band Bode and complete
margin evidence → bounded candidate synthesis and operating-point comparison →
explicit Apply → save/new-process restore/recompute → report and digital export.
Actual feedback dynamics, controller units and multirate ownership remain
specific to each converter.

## Initial source inspection

| Capability | LLC / TTPL baseline | DAB | Vienna | Three-phase two-level PFC |
|---|---|---|---|---|
| Declared physical loop | LLC FM voltage; TTPL nested current/bus | Forward power-command → TPS periodic power → capacitor-energy voltage loop | ABC current, bus-voltage and auxiliary midpoint approximation | dq current + lifted multirate bus-voltage loop |
| Detailed sensing / timing | Implemented with explicit frame and sampled-band limits | Only integer command delay exposed by core; no detailed ADC chain | Existing three sense chains; per-loop frequency-domain repair required | PWM integer delay; source explicitly excludes PLL/sensor dynamics |
| Exact imported H(z) | Implemented with source identity | Separate control-tool import exists | Consumer/Apply parity audit pending | Consumer/Apply parity audit pending |
| Bode and stability evidence | Valid per-loop bands and all crossings; model-qualified labels | G/C/L/T and actual discrete closed-loop poles | Shared grid crosses slower-loop Nyquist; missing margins currently accepted by helper, repair required | G/C/L/T and discrete closed-loop poles; full consumer audit pending |
| Candidate synthesis / retained workpoints | Existing shared solution-map consumers | Missing native common solution-map consumer | Missing parity under review | Missing parity under review |
| Save / new-process replay | Authoritative expert session; wizard context limitation explicit | Stage project save and output snapshot exist; complete control-session restore missing | Existing control/session coverage to be traced | Existing project/control replay to be traced |
| Research / report / C99 delivery | Actual buttons and standalone scripts verified | Snapshot JSON and displayed C99 exist; full delivery parity missing | Coverage to be traced | Displayed dual-controller C99; full delivery parity missing |
| Invalid / late result ownership | Verified baseline rejection and source identity | Historical output is labeled; cancellation/current-candidate use needs full audit | Pending parity audit | Pending parity audit |

## First implementation stages

1. Preserve the baseline repairs and close their fixed-source qualification.
2. DAB: define TPS/SPS input and branch boundaries, implement the supported full
   end-to-end control workflow with explicit physical assumptions, independent
   Jacobian/energy checks and real GUI replay. Bidirectional power and switching
   transient stability must not be inferred from a forward averaged model.
3. Vienna: repair per-loop sampled domains and misleading incomplete-margin
   acceptance first, then wire shared candidate/session/report consumers to the
   actual ABC and midpoint models. A dq option requires its own model and cannot
   be created by renaming ABC plots.
4. Three-phase two-level PFC: preserve its lifted closed-current-loop model and
   explicit multirate hold; extend sensing/controller/workflow consumers with
   independent state-space/lifted checks. PLL dynamics must remain explicitly
   excluded until modeled and validated.
5. Extend the matrix to other existing topology/module entries using actual
   consumer inspection. Mark unsupported, missing and physically inapplicable
   items separately. Requalify each runnable checkpoint before remote delivery.

No table entry implies hardware validation, TI executable equivalence or formal
release approval. Work is in progress; publication remains paused.

## First runnable checkpoints (2026-10-20, qualification in progress)

| Area | Implemented consumer changes | Evidence obtained so far | Still open |
|---|---|---|---|
| TTPL baseline | Explicit command rates, actual-rate diagram/strobe labels, preserved exact session values; C99 checks every signed margin and recomputes ADC magnitude instead of accepting stale warning text | 96 rate/session/Guided cases; shared C99 tests and real host compilation in a 60-case pass | Prior Guided full run is INCOMPLETE; this integrated fan-out tree requires a fresh full/wheel/frozen qualification |
| DAB | Forward TPS and SPS **power-command** actuator choices; unique-Jacobian guard; matched discrete sensing and nonlinear recurrence; exact candidate/Apply, retained scan, full typed session, HTML/research/C99 delivery | Implementation targeted tests plus independent 90 operating-point/event integrations, four recurrence reconstructions, fresh-process GUI sample replay | Shown-window paint crash was repaired by moving the canvas outside the outer scroll; three independent full shown-window runs now pass. Detailed analog acquisition/SOC/fractional timing parity is missing |
| Vienna ABC | Independent per-loop bands, missing-margin NOT_PROVEN, explicit clock/ADC checks; asynchronous candidates, exact order-2 imports, session/research/report; parent cancellation and late-result ownership | 27 owner-focused cases and real three-loop candidate Apply pipeline; shared source/cancellation checks | Independent compiled-C review verifies repaired initial-state/first-tick behavior; 14-page/focus/canvas/axis-label layout review now passes at `d5be63c`; full-source/package qualification is pending. Vienna dq, bus reference/feedforward timing model and time/switch diode/timing equivalence remain missing |
| Two-level dq | Canonical native FE-current/BE-voltage controllers and arbitrary exact H(z) in runtime, lifted model and C99; explicit discrete sensing, candidates/session/report | 61 owner cases and independent state-space/energy/compiled C99 checks; integrated actual parent replay and 720p visibility pass | Current-source whole-suite/wheel/frozen qualification pending; PLL/cross-coupled stability and analog ADC aperture are not modeled |

The DAB SPS option still accepts Pcmd in watts and maps it to phase. It does not
claim a raw phase-command loop or a current inner loop. The Vienna ABC model is
not relabeled as dq. A discrete calibration/filter/delay model does not complete
the LLC/TTPL analog acquisition/SOC workflow. These are real model/function gaps
to address in subsequent fan-out stages, not hidden behind a general “all done”.

Known legacy ADC examples stay unchanged on load. New explicit example factories
provide in-range nominal dividers; neither a nominal range pass nor C99 host
compilation establishes board-level offsets, transient headroom or hardware
safety. An explicit draft export preserves failed model evidence in its report.

Detailed independent checks, red-to-green repairs and source ownership are recorded in [stage validation](CONTROL_FANOUT_VALIDATION.md).

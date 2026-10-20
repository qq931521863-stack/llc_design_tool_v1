# Control-workspace fan-out: validation status

This is a bounded software-model checkpoint, not a full-topology or hardware
qualification. The native release remains paused. Independent checks below
exercise actual product consumers, rather than a second copy of a product formula.

## Native debounce-test follow-up: 2026-10-20

Run `39401146616` on remote `5a131c3` passed Linux. macOS recorded 4,898
passes, 43 skips and two xfails, with one desktop-workflow failure: after a
fixed 180 ms sleep, the 120 ms Qt debounce had not dispatched yet. The previous
dq arithmetic/export failures did not recur. Windows was still running when
this correction was prepared; no native packaging success is inferred here.

Test-only commit `4cabd36` waits for both debounce timers to stop, all jobs to
finish, and the expected current result/status, using the existing 20 s worker
test bound. All export, invalidation and numerical assertions remain intact.
Production code is unchanged. The full affected desktop module passes all 109
tests. Independent checks reproduce the old failure with a real 420 ms timer,
then pass the corrected full export workflow at 429 ms while rejecting a stale
result delivered at 250 ms. Stopped and stuck timer controls both fail at the
20 s bound; nine independent focused Qt cases pass. Native macOS confirmation
on the correction snapshot remains required; this is not a new full-suite pass.

## Native follow-up repairs: 2026-10-20

Remote `f95370a` native CI exposed failures after the local checkpoint below.
Those failures are retained, not counted as native acceptance:

- Windows run `39386082035`, job `118506140263`: 4 failed, 4,887 passed,
  42 skipped and two xfails. The origin tests created relative directory links
  with literal POSIX separators, which Windows stores without normalizing;
  the fixture now passes a native `Path` and verifies live link identity.
  JSON evidence is decoded and compared exactly. The parent-traversal test
  preserves unconditional rejection while checking the native OS's diagnostic
  resolution. Production origin gates are unchanged; no new skips were added.
  All 189 focused origin/artifact-contract tests pass locally, and independent
  mutation controls reject weakened containment, traversal and corrupted proof.
- macOS job `118506140457`: 2 failed, 4,888 passed, 43 skipped and two xfails;
  packaging was not reached. dq float32 export verification rejected a maximum
  error of `0.0001983642578125`. Forced-FMA GCC and an independent `libm.fmaf`
  oracle reproduce that exact error: fused C expressions differ from the
  separately rounded binary32 reference. Commit `685eddf` adds float-only
  volatile intermediate rounding boundaries to the **delivered header**, not
  merely compiler flags for verification. The forced-FMA error becomes zero;
  double output and numerical tolerances are unchanged. All 46 focused source
  tests pass, including real GUI delivery and corrupted-arithmetic rejection.
  The GUI test now reports exporter warnings directly before checking files.
  An offline-built wheel from exact source `685eddf` additionally passes all
  67 affected dq tests with no skips: 665 installed product files match the
  source and wheel, and all 163 observed product imports originate in the wheel.
  Wheel SHA-256:
  `c6851a20f4a87048acc1012d4a44d10cb2c9ea04d7b9245c20781a301073e220`.

These are bounded follow-up checks, not a new full-source or frozen acceptance.
Actual Windows origin cases and macOS arithmetic/export tests, followed by native
packaging, still require successful CI on the repair snapshot. No target ABI,
ISR timing, hardware or new frozen fan-out execution is established.

## Completed local acceptance: 2026-10-20 07:34 UTC

The bounded local acceptance is complete for product/test source
`270b4665780ad86dd827547d72c84f49b091ef21`. Remote checkpoint
[`f95370a`](https://github.com/yangshuai2022-star/power-design-toolkit/commit/f95370a56bbfe78fab42bc70f0459a20adffa596)
contains that exact product/test content and three documentation-only updates.
This completion note is prepared locally; native CI for that remote checkpoint
remains separate. Formal release is still paused.

- **Segmented exact-source coverage PASS, not a single-process pass:** all
  4,935 original unique nodeids are accounted for: 4,931 passes, two original
  long-test opt-in skips and two original backend xfails. The first process
  recorded 4,148 passes, two skips and one environment-only failure: the fresh
  test venv lacked product distribution metadata. A fresh 785-node tail passed
  783 cases plus the two xfails after copying only six byte-exact dist-info
  files from the verified wheel. Product/test bytes and all assertions were
  unchanged. The failed version test is the single explicitly repeated node;
  its original red record remains retained. Independent review verifies no
  coverage gap or hidden duplicate and all 309 final imports originate from
  the frozen source.
- **Both original current-source long gates PASS:** PFC 1/1 in 860.04 s,
  PSFB 1/1 in 646.10 s, no skips/errors/failures. Both original opt-in tests
  therefore execute separately despite being skipped in the ordinary suite.
  Native Qt top-level widgets, roots and plot objects finish at zero. PFC
  actual GUI, device/capacitor exports and ownership evidence are retained.
  The earlier externally SIGKILLed PFC attempt remains INCOMPLETE and is not
  relabeled. The successful retry used fresh evidence and unchanged assertions
  and timeouts, with more memory headroom.
- **Installed/frozen acceptance PASS:** original production wheel self-test,
  342 baseline tests, 523 changed-test cases and all 76 MCP cases, with no skips
  or failures; actual Linux frozen production self-test and every original
  repair/provenance gate. Coverage is 664 wheel files, 395 frozen modules and
  exactly 17 resources. Artifact hashes are retained in the submission record
  below. A prior MCP test-guard rejection is retained: the reviewed correction
  propagates only sealed guard configuration into a copied clean child
  environment, without changing MCP/product assertions or permitting network.
- Independent actual LLC consumers and all five original integrated-magnetics
  checks pass. Reviewed guard receipts account for normal exits and intentional
  cancellation kills, with zero outbound attempts. The serial scheduler's
  original records are preserved; parallel stage receipts are reconciled
  explicitly rather than falsely marking that serial process as successful.

**Remaining boundaries:** native Windows/macOS CI for the remote snapshot is
pending here; no hardware qualification, MATLAB execution or full physical/model
parity is claimed. New DAB/Vienna/dq candidate/session/export chains are exercised
in the installed wheel, but are **NOT RUN inside the frozen executable**. The
frozen result covers original six-workspace production flows and exact module/
resource provenance. Inherited LLC clipping and the topology/model/timing gaps
listed below remain actionable next-stage work, not waived parity.

## Submission checkpoint: 2026-10-20 07:07 UTC

Submitted at the owner's explicit request before remaining source/long gates
finish. Product/test source is exactly `270b4665780ad86dd827547d72c84f49b091ef21`;
this submission adds documentation only. Formal release remains paused.

- **PASS:** actual installed-wheel production self-test; 342 baseline + 523
  changed-test + 76 MCP tests, no skips/failures; original actual Linux frozen
  production self-test and all artifact repair/provenance gates.
- Verified coverage: 664 wheel files, 395 frozen product modules, exactly 17
  resources. Wheel SHA-256: `a5f0675c8033e1751b62ba00458677a758e6d8df8de208b1776faa44135f0aa5`.
  Linux executable SHA-256: `220872400d6df9e36bef37d9ae69599de7514c45903f67b7c0c9d8d79c27dc41`.
- **PENDING:** complete source collection accounting. The 4,935-node run is
  still active; an external test guard incompatibility with MCP's clean child
  environment is known. The minimal reviewed guard-only fix passed installed
  MCP76. Any source continuation will preserve the original record and account
  for every exact nodeid; it will be labeled segmented coverage, not a
  single-process whole-suite pass. No product failure is waived.
- **INCOMPLETE:** current PFC-long received external SIGKILL after 1,096.99 s,
  with zero pytest failure events but no completed result. The cause is not
  proven; this is neither a pass nor a demonstrated product failure.
- **RUNNING:** current PSFB-long. Native Windows/macOS results for this new
  snapshot remain pending. Hardware remains unqualified.
- **NOT RUN:** new DAB/Vienna/dq candidate/session/export chains inside the
  frozen executable. They were exercised in the installed wheel; the frozen
  acceptance covers the original six-workspace production flows and provenance.

Earlier chronological records below remain retained, not overwritten as passes.

## Source and qualification ownership

- First complete-suite attempt at clean `83185e530767b1b00d18184ab78c56a6a63c24c1`
  stopped fail-fast: **1,153 passed, 1 failed**, 33 warnings in 1,573.09 seconds.
  The TTPL exact-export ownership test's intended successful export was rejected
  by the strict model-evidence gate, leaving no manifest: the legacy divider
  produces 5.39629 V at a 3.3 V ADC, while the explicit in-range example produces
  2.55387 V and passes the unchanged gate. Original independent
  LLC consumers and all five magnetics checks had passed; PFC-long, installed
  wheel and frozen stages were **NOT RUN**. This red evidence is retained and
  is not counted as a completed whole-suite qualification. A fresh exact-source
  run is required after repairing the invalid positive fixture.
- Test-only repair `7ba7289` uses an explicitly in-range TTPL positive fixture,
  preserves ownership/modal/coefficient/hash assertions and adds actual GUI
  rejection of the legacy overrange configuration followed by valid recovery.
  Two candidate-Apply tests now use the default rejecting gate instead of draft
  overrides. Production code and acceptance thresholds are unchanged. The
  affected eight-module run passed 138 tests; the ownership module passed 17
  tests in each of two fresh processes. Independent review of the exact
  three-file patch passed all 53 changed-module tests in a fresh process,
  actual exit 0. Both margin screens pass for the rejected fixture; its ADC
  range alone causes rejection. A fresh full qualification remains pending.

- Second complete-suite attempt at `e61f4f58df3f5b4de06ed01ab9bf280bb6453303`
  passed all 17 TTPL ownership cases and stopped at a different assertion:
  **2,634 passed, 2 skipped, 1 failed**, 42 warnings in 2,057.94 seconds.
  The legacy Vienna voltage-plant test used the global/current grid (1,600
  samples) instead of its own voltage-loop grid (1,390). Audit also found a
  **production CLI defect**: merging those unequal arrays crashed CSV export.
  Repair `b278daa` preserves exact per-loop sample grids in a union CSV, leaves
  unsupported cells empty without interpolation/extrapolation, and reports
  each domain and its NOT_PROVEN limits. The original independent voltage
  plant formula and tolerances remain unchanged. All PFC diagnostics pass
  534 tests with one existing opt-in long-test skip, actual exit 0. Independent
  review passes 39 affected tests, 23 host tests, real default CLI output and
  four edge-domain audits. This diagnostic is not full-source qualification.
- Test-only repair `46eb241` isolates the unrelated scheduled updater at its
  imported network boundary in affected PFC host tests. Real timers and GUI
  assertions remain active; an independent guard records zero outbound
  requests. The earlier denied/incomplete network-triggering diagnostic is
  retained, and no live updater acceptance is claimed. A separate partial
  seven-root diagnostic is also incomplete, with no terminal JUnit/exit; it
  is not counted as a pass. Its two failures were reproduced independently:
  18 new TTPL/dq shell labels lacked translations. Repair `9ffd821` adds
  English/Japanese/Korean catalogue entries and actual widget/tab language
  switch-and-restore coverage. All 16 localization tests and the unchanged
  lifecycle test pass, preserving its injected-failure cleanup and close-only
  negative control (113 native wrappers destroyed per cycle). These focused
  tests made zero outbound attempts. Final complete regression, installed-wheel
  and frozen gates remain pending.

- Comprehensive test-only updater isolation `8428190` replaces narrow module
  allowlists with one session boundary, and explicitly covers the shown-PLL
  subprocess. Both automatic 2,500 ms timers and three manual toolbar entries
  are observed reaching the fake boundary without replacing timer methods.
  Owner checks pass 32 tests; independent checks pass 26 tests plus two fresh
  early/late-import six-workspace probes, all actual exit 0 with zero outbound
  attempts. Updater unit tests retain the real entry point with their existing
  mocked network paths. No live updater acceptance is inferred. Because this
  changes the root test fixture, historical PSFB-long dependency reuse must be
  reevaluated conservatively; current-source long execution remains required
  if that audit invalidates reuse.

- A fresh guarded seven-root diagnostic completed with 2,179 passed, one
  failure, 12 ngspice-path skips and two existing xfails. Its sole failure was
  a **production DQ reopen defect**: close permanently marked the view closed,
  so new worker results were rejected after reopening. A four-line show-event
  repair resets only that flag; revision, cancellation, request identity and
  worker-ownership guards are unchanged. All 191 related owner tests and 85
  independent producer/DQ GUI tests pass with actual exit 0. Independent real
  rendering proves three reopen/recompute cycles and all five waveform plots;
  queued old results/errors remain rejected. The 12 diagnostic skips were not
  accepted as qualification: rerunning the complete simulation root with the
  existing verified ngspice path passed all 58 tests, no skips.
- Installed test-fixture portability is independently verified: an explicit
  archived root-fixture path and SHA-256 replace implicit checkout imports.
  This fixture remains source-only; application/support imports stay in the
  wheel. Independent testing passes 24 installed cases, verifies all 664 product
  files against the archive and rejects a missing fixture contract. Six child
  probes confirm 276 installed-only module origins and zero outbound attempts.
  This is focused installed evidence, not full package or frozen acceptance.

- Baseline Guided source: `a74e225b0afd9163823e8e5de5c954baf9022f46`.
  Its whole-suite process stopped at 66% without terminal JUnit/completion.
  The last observation had zero failure events, but the run is **INCOMPLETE**.
  No full pass is inferred from this partial run.
- The fan-out integrates the Guided source plus DAB, Vienna and two-level dq
  changes. Its complete fixed-source qualification is **PENDING** until a new
  terminal test/wheel/frozen evidence set is recorded.
- Remote native snapshot `320b3e3b8e95b6d466f7afd6f3cc3af485ed3741`
  predates this fan-out. Run `39352715880` has Linux success (4588 passed,
  1 skipped, 2 expected failures) and macOS success (4546 passed, 43 skipped,
  2 expected failures), including actual macOS frozen self-test and package
  upload. Windows remained in tests at the last observation. These are not
  fan-out package results.

## DAB: independently checked first stage

Independent periodic integration checked 90 operating points and 15 analytic
SPS vectors; maximum SPS power error was 8.42e-12 W. Four independent nonlinear
recurrences agreed within 2.1e-13 V. A same-rate imported controller invalidated
old current results; malformed sessions preserved the prior project atomically.
A fresh OS process reproduced the six complex response arrays and 2001 time
samples. Actual compiled C99 covered PI and a non-PI controller with a leading
numerator zero, including saturation/reversal; standalone Python research
exports executed successfully. MATLAB was exported, not executed.

Three separate shown-window runs exercised candidate generation, Apply, Run,
paint, scrolling, export and close with normal process exit. The fixed layout
keeps FigureCanvas outside the whole-page scroll area. This is Linux/offscreen
Qt evidence, not native Windows/macOS evidence.

Scope: forward TPS/SPS **power-command** loops only. Raw phase-command control,
reverse power, detailed analog acquisition/SOC timing, FRA measurement binding
and a complete Cp transient state model remain unimplemented.

## Two-level dq: independently checked first stage

Owner checkpoint `380d55a6b08cf1d00cefb5986dee9e13edcf08d4`, integrated as
`9013324`, preserves native FE-current/BE-voltage runtime coefficients and makes
exact imported H(z) common to the nonlinear and lifted local models and exports.

Independent finite-difference energy Jacobians, exact matrix-exponential ZOH,
block-assembled controller/sensor/FIFO states and repeated-step outer lifts
covered five configurations. Maximum state-matrix disagreement was 8.88e-16;
worst matched-pole disagreement was 3.07e-14. Nonlinear traces were checked
against independent SciPy recurrences, including tracking/freeze policies.
Compiled binary64 C99 over 738 samples per controller agreed within 8.53e-14;
binary32 native/FIR/gain results were exact, with a leading-delay IIR within
5.07e-7. Energy residual decreased by approximately four on each integration
step halving (0.014696, 0.003670, 0.000917 J).

Three sensor-domain defects were found and repaired: retained-corner ADC
excess could previously permit Apply; gain-adjusted current envelope was wrong;
nonunity voltage gain used the wrong physical settling target. Independent
counterexamples now reject 7 V/3.3 V corners, report the corrected gain-0.5
range, and identify 777.7778 V as the gain-0.9 regulated physical target.

The real PFC parent at 1280x720 passed exact import, candidate Apply, invalid
session atomicity, new-process replay, report/C99 export, nonlinear Run, all
nine tabs, PLL Run and cancellation/late-result/close checks. The candidate tab
shows coefficients/metrics/details; it does not contain a separate preview
Bode canvas. Applying then recomputing updates the current-source Bode.

Scope: ideal decoupling and local forward Iq=0 loop model. PLL coupling,
negative-sequence/MIMO stability, detailed analog aperture and hardware behavior
are not established by these checks.

## Shared consumer regression

The candidate summary retains signed current/candidate gain margins, including
negative margins, after moving into the scrollable control sidebar. A regression
found the summary had been shortened too far; the original margin-content test
is preserved and passes after restoring the complete rows. Real LLC/TTPL Apply
and recompute retain exact Kp/Ti/Ts and H(z). An actionable LLC parent-page clipping issue remains for the next repair stage,
not a waived parity criterion. Reproduce at 1280×720: LLC → Digital Control →
Solution Map → Generate; scroll the outer page to its maximum. At `f86b1fb`, the
671×706 preview has only 671×583 visible within a 601-pixel-high ancestor viewport.
Replacing only the shared class with its exact `62a4e87` version still yields a
557×676 preview that cannot fit that same viewport. The host embedding in
`solution_map_install.py` and `digital_loop_view.py` needs a result-focused layout
repair and a strict full-ancestor visibility regression after this batch. This
checkpoint does not claim complete shared-workspace visual parity. TTPL's
preview becomes fully visible through its existing outer scrolling.

The two-level dq export regression now first proves successful current-source
export and six SVPWM traces, then changes an input and verifies that no save
dialog or stale files appear. The former assertion deliberately exported the
old result after changing inputs and conflicts with the new source-bound
contract; successful export coverage is not removed.

## Vienna: independently closed consumer repairs

The first independent pass checked 15 physical complex-response chains and 36
compiled-controller recurrence sequences, exact source/session/report consumers
and all 14 actual tabs. It also found two release-blocking stage defects:

1. **Repaired and independently verified in `81e561d`:** C99 Init previously
   set external G0 but not controller history, and slow tasks first ran after N
   ticks instead of at t=0. The repair passes 103 focused tests; the original
   generator fails all three selected regression cases. Independent compiled
   ControlStep replay covers 12 controller/divider combinations, each with
   160 frames and 16 outputs, Reset and disable/re-enable; maximum float32
   oracle disagreement is zero. Captured actual Python waveform sample operands
   replayed through C99 cover three configurations, 160 frames and nine control
   outputs, with maximum absolute error 1.84e-5. General 2P2Z initial history is
   allowed to decay according to its coefficients, not forced to hold G0.
   See [runtime contract](VIENNA_C99_RUNTIME_CONTRACT.md).
2. **Repaired and independently verified at `d5be63c`:** an expanded diagram
   previously compressed candidates and waveform plots, while intermediate
   parents clipped 12 pixels from the default Bode. Result-focused page layout,
   bounded scrollable diagnostic text and resize-aware plot layout now preserve
   the whole canvas and internal axis-label bounds. All 14 primary pages, 12
   diagram/inspector preference-transition combinations, an actual PLL run and
   both populated performance plot pages pass at 1280×720 with clean close and
   no uncaught Qt callback. Long rotated labels wrap without smaller fonts;
   stacked waveforms retain their shared time-axis labels on the bottom row.
   The original zero-height/clipping failures remain recorded, not reclassified
   as passes.

The final review records 840 tracked files at `d5be63c`; all 37 C99 computational
dependencies match the independently executed runtime source, and the shared
solution-map class matches its requalified source. Full-source regression and
installed/frozen qualification remain separate, pending gates. The pre-existing
waveform/Bode delay and bus-energy inconsistency, reference-update/feedforward
timing and an actual Vienna dq controller/model remain explicit next-stage work.

## Multirate export counter boundary

Independent strict-C99 review found that divider 65536 was accepted even though
TTPL/Vienna runtime counters are uint16_t. The resulting counter wrapped before
its comparison could trigger. Commit `3112f89` rejects divisors above 65535 and
preflights all dividers before creating output directories or writing files.
Twenty-five regressions cover all five TTPL/Vienna rate consumers, existing-file
preservation and strict C99 compilation at divider 65535. All 128 codegen tests
pass on the isolated repair. This is an export-runtime representation limit,
not a restriction newly imposed on the analysis model's physical rates.

## Windows evidence-print failure

Old run `39344422222` completed the Windows packaged self-test, then failed while
printing its Unicode JSON proof to a cp1252 console. Commit `62a4e87` escapes
unsupported console characters while preserving the original UTF-8 evidence and
all validator gates. Seventy-seven packaging/strict-report cases pass, including
real child-process failure rejection and cp1252/ASCII/UTF-8 output. An initial
three-case test assertion used an incorrect substring; that red record was
retained and the assertion corrected to the exact payload. Native Windows
requalification remains required.


## Installed and frozen coverage boundaries

The new qualification will run actual DAB/Vienna/dq candidate, Apply, session,
report and C99 tests against an isolated installed wheel and verify their import
origins. Original Linux frozen production tests still exercise the six real
workspace entries and previous PSD/LLC/magnetics consumers, with unchanged
provenance and resource gates. Collection of new fan-out modules is checked;
execution of the new candidate/session chains inside the frozen executable is
**NOT RUN** at this stage. Installed-wheel consumer success must not be renamed
frozen-consumer success. Native OS checks remain bound to their own remote SHA.

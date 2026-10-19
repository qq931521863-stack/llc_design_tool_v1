# Power Stage Designer development status

## Requalification checkpoint and worker-ownership correction (2026-10-19)

The integrated numerical/layout repair `a698dc533124e9c2d22342761cc2c56bf01a1f88`
passed clean installed-wheel and actual Linux frozen application checks, including
both the accepted WARN and rejected FAIL Two-Stage paths. The original five
independent integrated-magnetics scripts and actual full/half-bridge consumer
checks also passed on that exact source. However, its new full regression stopped
with **3707 passed, two opt-in skips and one failure**: an auto-deleted PFC worker's
Python wrapper remained owned by QThreadPool. This is a genuine retained-wrapper
race, reproduced with actual PFC methods and independently with an empty QRunnable;
it is not dismissed as a timeout or test-observer artifact. The 10-second lifecycle
assertion remains unchanged. A submission barrier orders the binding ownership
handoff before runnable execution, while preserving native auto-delete and safe
queued result disposal across all producers. Fresh aggregate qualification remains
required after this application change.

Separate diagnostic tail: **445 passed, two historical xfails**. This is not an
aggregate pass. The opt-in finite-Coss PSFB joint sizing test passed separately on
`a698dc5` in **487.33 s**, with independent current extrema and physical options
preserved. Public input-only [Two-Stage examples](../examples/llc_two_stage/README.md)
provide the exact admitted WARN and rejected FAIL configurations and singleton axes.

## Native qualification corrections in progress (2026-10-19)

The dependency-corrected main `c19e208466d51e09ab657c2c612d2dd6fa2857cb`
passed Linux CI (4153 passed, one skipped, two historical xfails), but the first
actual macOS test run failed: 16 failed, 4096 passed, 42 skipped, two xfails.
No macOS frozen executable was produced. This invalidates treating the older
Linux-only result as complete platform qualification. The red logs remain retained.

Repairs now address fresh physical residual checks for stagnated PSD roots, a
scale-relative PSFB BCM inverse boundary, native-font/frame LLC layouts, and a
real switched-LLC integration error. The old fixed-grid RK4 map could report a
tiny periodic residual while missing the continuous circuit orbit. Event-split
adaptive Radau integration and independently resolved magnetizing-current extrema
replace that map without changing the physical model or 20% FHA/TD rejection gate.
An independently integrated Ln=5.5/Np=30 full-bridge case is correctly FAIL at
20.92046%; it remains a real GUI/CLI/frozen negative test. The positive path uses
independently checked Ln=4.5/Np=32/Ns=4, retains WARN and nominal-only TD scope,
and exercises Apply, save/reopen, loss and report identity. Old optimization
sessions are explicitly marked with a changed numerical-model revision.

The numerical repair has separate DOP853 circuit/periodic/energy/flux/charge
checks and actual full/half-bridge consumer acceptance. Full qualification of
the integrated repair source, installed wheel and frozen application is still
pending. Older green sections below describe their exact historical source;
they do not qualify this changed application. Release/tag creation remains paused.

## Main push and native-CI correction (2026-10-19)

Main was updated non-force to `21f5df4e7256c67d78add8cac16f3e5ea898977b`.
All 34 logical commits preserve their exact source trees; the final 761 paths,
modes and blobs match local `502367128c96d185c7b3d3824586a1c669e288fe`.
Connected-API commit IDs/authorship timestamps differ from the original local
history, whose bundle remains retained. Existing tags/releases were unchanged.

The first actual main CI completed with **4151 passes and one failure**: the PDF
source-validation test needs `pdfplumber`, previously listed only under research.
It is now included in dev test dependencies with a CI contract; the affected real
PDF/bundle tests give **24 passes**. The application code is unchanged. Native
Windows/macOS tests and frozen builds remain pending a successful replacement CI
run. Both automatic publication jobs remain disabled; no release was created.

## Latest completion checkpoint (2026-10-19)

The requested software workflows and follow-on integrated-LLC/CSC additions are
implemented within their declared domains. The clean application checkpoint
`4f9f5b4513a62fa9c5effb1b4cad5beacf36daac` completed the full configured
Linux/offscreen regression: **4140 passed, 2 opt-in skips, 2 historical xfails,
43 warnings**, exit 0 in 2728.51 s. All 760 tracked-file hashes were unchanged;
final native owner/scene/plot/menu/group counts were zero. Five independent
integrated-LLC consumer/identity/boundary/projection/TD-label scripts pass on
that exact source. This closes the previously incomplete aggregate gate for
that application SHA, without erasing its earlier red runs.

- **Priority workflows:** T01 Buck, T02 Boost and T03 inverting Buck-Boost have
  actual CCM/BCM/DCM component/project/report paths. T05's two-switch optional
  dynamics, T17's ordinary/joint light-load sizing, T18's declared PSFB models and
  T20/T21's existing LLC consumers retain their topology-specific domains. See
  the [current per-priority summary](PSD_FEATURE_MATRIX.md#latest-application-qualification-and-supported-scope-2026-10-19).
- **Desktop entry:** Power Stage Designer is the first-level launcher card;
  direct CLI/module entry and existing workspaces remain available. Linux Qt
  720p/768p/900p layout evidence is separate from native platform qualification.
- **Integrated LLC:** separate-Lr remains the default; declared two-winding
  leakage resonance reaches real physical-flux/winding/loss/thermal, optimization,
  save/reopen and report consumers. Unsupported CT/star and magnetizing-series-R
  domains and FHA/EDF display projections remain explicit.
- **CSC catalog:** 630 explicit parts, 318 source-specific geometries and 44
  printed formulas supplement 46 complete presets and 137 legacy references.
  Actual PFC/LLC consumers retain missing-data and source-conflict boundaries.
- **Long gates:** one actual switched-PFC desktop case passed in 825.16 s;
  one finite-Coss PSFB joint-sizing case passed in 495.49 s, including 18 actual
  forward evaluations and independent extrema. Both used exact 4f9f5b4 and
  retained separate JUnit/source-hash records; the aggregate skip count is unchanged.
- **Artifacts:** the isolated installed wheel and actual Linux frozen application
  pass the schema-4 consumer checks at
  `759a8fb54a0443a2e97125335dc69b61b5942ec7`. The final source-collection audit and
  its retained preparation failure are documented in the validation report.
- **Source identities:** artifact source differs from application-tested 4f9f5b4
  only by the exact two publication guards and 11 new tests. The independent
  release-contract group has 415 passes including those 11. The final documentation
  commit and mapped remote SHA are recorded in the delivery/transfer manifests;
  the complete application run is never attributed to that later SHA.
- **Publication:** formal release remains paused. Main push is authorized after
  qualification, but had not yet occurred at this documentation checkpoint.
  Actual remote CI/native results remain separate verification records.

Remaining work has two categories. Model extensions still exclude, among other
row-specific domains, asymmetric bridge/reset physics, nonlinear commutation,
zero-load/burst policies and missing vendor/thermal data. External verification
still includes exact TI executable correlation, both native platforms, new
bench/thermal/EMI correlation and hardware safety. TI access does not explain or resolve the
unsupported model domains. The [validation report](PSD_VALIDATION_REPORT.md)
records each evidence level and the source/artifact distinction.

Earlier dated checkpoints below are historical. Preserve their failures and
corrections; read their then-current pending/no-push statements in the context
of those checkpoints, with this section governing the latest status.

## Historical qualification after diagnostic repairs (2026-10-19)

The diagnostic-only tail completed with 1279 pass, 2 expected failures and
3 failures. One existing TD-loss GUI test double lacked the new cooperative
checkpoint keyword; it now accepts and actually invokes that callback, with
23 consumer/cancellation cases passing. Two historical v9.4.2 EOL tests wrongly
used the evolving current publisher as their immutable old source. They now use
the exact original workflow fixture from commit e8751d37ceb975ae0e8e13b374773e419de6643b,
with source SHA-256/blob provenance and LF checkout protection. Nine focused
and 87 related release-contract tests pass; negative cases now require the
intended content/raw-proof failure, not an earlier rewrite rejection.
Production release workflows and strict proof verification are unchanged.

These diagnostic segments preserve every failure and cannot be joined into a
complete-source PASS. All narrow repairs are committed locally; a new immutable
full collection, opt-in long PFC and actual wheel/Linux frozen qualification are
next. The five independent integrated-LLC consumer/identity/boundary/projection/
TD-label scripts already pass again at clean bc18737 with unchanged source bytes.
Publication remains paused, with native Windows/macOS, TI executable correlation
and hardware validation still separate unfulfilled gates.


## Historical aggregate red and narrow correction (2026-10-19)

The clean `bfb0e54067eebb973811213a671e9906dca0f6c6` full run stopped normally
with exit 1 after **1353 passed, 1 skipped, 1 failed** of 4140 collected cases.
Its T15 all-drawn-net test injected a cycle-average source current at each instant,
even though equal split capacitors on the stiff bus require a pulsed source.
Independent circuit/ODE review confirms the product currents; the test boundary
is corrected without changing any net, branch orientation or 2e-7 A tolerance.
A fixed original-failure point, zero-current off interval, wrong-mean counterexample
and independent integral check guard against a constant-current pseudo-waveform.
X12 now explicitly labels its existing DC operating-point input as average current.

The 16 independent vectors cover ordinary/joint CCM, BCM and DCM, two diode drops
and nominal/near-limit capacitance. This retains the T15 first-order limitation:
finite split-C midpoint ripple is not fed back into nominal magnetic-state slopes.
At the allowed ripple boundary the instantaneous nominal-primary power discrepancy
can approach 5% of the ideal source pulse; cycle-average energy closure is not a
claim of a fully coupled finite-C transient model. The input-current metric remains
an average, and device-waveform CSV does not fabricate a source-current trace.

The affected 387-case group and the new actual X12 mean-label/save/export case
pass. Independent review reran the original T15 all-net case, new source oracle
and actual X12 consumer (3 pass). A diagnostic remainder subsequently found 14
missing new shell translations in en/ja/ko (1488 pass, 1 skip, 1 fail); accurate
translations and actual switch/restore validation now pass in the complete
16-case i18n/lifecycle group. The original harvest and deliberately failing
translation/owner-cleanup counterexample remain unchanged.
Neither diagnostic segment is a complete-source PASS.

The failed run peaked at 1,836,372 KiB aggregate RSS and ended with zero retained
native owner/scene/plot/menu/group counts. Its later unexecuted cases are being
run separately for diagnosis; those results cannot be joined into an aggregate
PASS. A fresh complete-source run, opt-in long PFC and actual installed/frozen
qualification remain mandatory. Publication remains paused.


## Historical completion extension and lifecycle gate (2026-10-19)

The latest user addition is integrated LLC resonant magnetics: preserve the
separate-Lr default and add transformer-leakage resonance with actual electrical,
physical winding/flux, loss, thermal, selection, persistence and report consumers.
This is implemented and locally integrated as `c06f1d5`, including coherent APR
mapping, physical flux, explicit winding feasibility, actual GUI/save/report and
locked-magnetics optimization. Focused independent physical/TD and 34-step desktop
acceptance pass; five independent physical/identity/boundary/projection/TD-label
scripts also pass at exact clean bfb0e54. Aggregate and artifact gates remain open. Default seven-harmonic HB passes actual-event-loop replay in under three seconds;
the earlier qWait-harness timeout is not a product failure. The narrow cooperative
Cancel/finite total-budget repair now has independent actual HB/TD cancellation,
close, timeout, invalidation and retry acceptance. Product integration is committed; the
new-source aggregate/artifact sequence remains open; no lowered numerical criteria
or partial-result fallback is used to satisfy a timeout.
FHA/EDF projection limits and unsupported multiwinding/series-R domains are explicit.
See the [integrated workflow](magnetics/integrated_llc_workflow.md) and two synthetic
[software examples](../examples/integrated_llc/).

The user additionally requested completion of the magnetic-core selection database
from four supplied CSC documents. This is a new acceptance item alongside the
50 original T/X/L/C/A IDs. Source extraction, strict source-specific data adapters,
actual PFC selection/calculation/persistence/report consumers and LLC reference
catalog integration are implemented in `d51739d`, with small-screen navigation
and explicit self-loss operating-point labels added in `3c07a0f`. Fifty-two
focused cases, 20 independent probes and actual TTPL/Vienna/LLC consumers pass.
The `3f09c4b` mandatory packaging gate also passes 76 affected contract cases and
a real-source helper replay. Actual new-source wheel/frozen qualification remains
pending; these are not artifact results. Missing conventions and parameters remain
explicit; legacy default bias/loss/material values must not fill new records.
The local source will be frozen for qualification after integration; publication
remains paused. No earlier source result is promoted to acceptance of this source.

The `0dfd790` aggregate was deliberately interrupted after a concrete native menu
retention counterexample. Full-log reconciliation found **965 passed, 1 failed**
outcomes among 3763 collected; the session exited 130, without complete JUnit.
Earlier tail-only monitoring missed the T18 failure. It is retained as a failure,
not converted into a complete pass. The default optional magnetizing-ripple target
was wrongly active at 0.1 A; the original unchanged failing test now passes after
defaulting it off, clearing topology/new-project carryover and preserving explicit
saved targets. Focused intent and ideal-physics checks pass.

A bounded all-GUI ownership audit covered 76 local classes, actual application
entrypoints, transient/lazy dialogs, plot/export auxiliaries and nine asynchronous
producer paths. Explicit menu ownership, standalone entry cleanup, transient
impedance dialogs, cached catalog reuse and a parented control-diagram signal proxy
have focused red/green evidence. The shared runnable/signal repair now passes
85 focused cases across 14 actual startup paths. Independent replay of all nine
producer families proves zero workers/signals after three jobs and both owner
disposal stages; two adversarial Toolbox cancellation boundaries also pass without
waiting for unrelated global work. New database GUI consumers must satisfy these
same lifetime checks. A fresh immutable full-suite,
long-PFC and installed-wheel/actual Linux frozen run will follow integration;
native Windows/macOS proof remains a separate release condition.

## Historical direction before final qualification

Publication is paused. No remote push, CI dispatch, tag or release was performed.
Version 9.9.0 and release preflight changes remain local preparation only.
The latest priority is Buck, Boost/Buck-Boost and full-bridge functionality plus
clear desktop access. A further real-consumer audit found and repaired advanced
suggestion records, T17 light-load sizing and missing LLC component terminals.
Screen-level layout and the new aggregate/artifact qualification are in progress;
prior row labels and historical full-suite counts are not treated as this pass.
T05 floating-node dynamics, QR valley/data options and PSFB finite-Coss commutation
are implemented opt-in models with explicit source parameters.
T05/QR/T18 now have independent circuit acceptance and actual desktop persistence/report tests.
Concrete audit findings include calculator-session model identity, everyday Chinese
result presentation and the precise documented Type-I network setup. A03 now has independent acceptance of its actual desktop/CLI candidates, Apply,
save/restore, reports and same-source formula PDF. The
report/corner temperature mismatch is repaired and independently verified. A05
real C99/FRA/LLC/PFC export boundaries are independently accepted after the fixes.

## Historical full-matrix repair and aggregate resource gate

The frozen priority snapshot `3d2f9fbd020fd0b9513f6f9e5950de5c31dfb38e`
collected 3502 cases but its single-process pytest child exited by SIGKILL (-9)
after 2508 passes and 1 skip. There was no assertion failure before termination,
but this is **not a completed regression**. Peak process-tree RSS was 4,983,292 KiB;
parent peak was 4,620,500 KiB. A kernel OOM cause is not proven.

Independent full-task re-audit found ten actual remaining model/consumer gaps.
They are being repaired in `feat/psd-baseline-completion`, based on that frozen
snapshot, with the original qualification tree unchanged. The current matrix
repair section distinguishes numerical kernel checks, actual consumer checks and
remaining aggregate/artifact gates. Previous blanket enhancement/baseline
completion statements are superseded by this specific re-audit.

Ownership probes established that close-only test cleanup retained native LLC/PFC
owners and their canvases despite GC. Real workspace switching deliberately reuses
one instance of each workspace, but Home selector cancellation leaked transient
dialogs and application event-loop return omitted explicit shutdown. The repair
must prove both bounded reuse and destruction of discarded/exit-owned objects;
allocator trimming or split-process test counts alone cannot establish a fix.

The identified matrix/consumer repairs and independent counterexample replays are
now complete. Lifecycle focused tests pass; exact-source aggregate qualification
remains the next gate. Next: checkpoint exact source; run complete configured regression with RSS/object lifetime evidence,
opt-in long switched-PFC GUI, then newly built wheel and real Linux freeze. Native
Windows/macOS and TI executable correlation remain separate unrun gates.

## Historical 9.9.0 integration checkpoint

The M0–M5 implementation exists; the new full-matrix repair cycle remains active
before fresh release qualification. The maintained scope is the [50-ID matrix](PSD_FEATURE_MATRIX.md);
its bounded-model statuses are not a declaration of complete TI equivalence.
Native Windows/macOS release tests and frozen executable proofs are separate
publication gates, recorded by the release workflow and its public proof assets.
At preparation of this candidate those native results are **pending**.

| Milestone | Implemented deliverable | Evidence/remaining gate |
| --- | --- | --- |
| M0 | Existing-code/dependency audit and complete T/X/L/C/A matrix | 9.8.1 baseline retained; no unrelated branch changes |
| M1 | Buck, Boost, Flyback desktop calculation; Buck VMC Type II/III; connected waveform/statistics; save/recompute/report | Independent reference/boundary and real Qt workflow tests |
| M2 | Twelve calculators; six strict-CCM plants; five actual compensation networks | Nodal/energy/frequency-response checks and all 30 plant/network desktop combinations |
| M3 | Nineteen direct stage models and reuse of LLC HB/FB | Per-row domain checks; T05/T11/T18 exact TI policies remain PARTIAL |
| M4 | Corner analysis, linked components, same-source LLC/PFC consumers, exact H(z)/FRA chain and reproducible sessions | Actual downstream consumption and independent records; physical/data limits retained |
| M5 | Full regression, common sizing/AC-RMS/identity requirements, Qt lifetime repair, installed-wheel workflow | Historical source: 2864 passed, 1 explicit long-test skip, 2 existing xfails; long PFC GUI separately passed; new release CI required |

## Useful starting points

- Launch `power-design-gui` and choose **Power Stage Designer**, or run
  `power-stage-design` / `python -m power_stage_design` directly.
- Load one of the small [example projects](../examples/power_stage_design/),
  recalculate, select a schematic component, inspect V/I and stress, then export.
- Buck analog-loop analysis includes two capacitor/ESR branches, inductor DCR,
  finite amplifier gain/bandwidth and reanalysis of the actual rounded R/C values.
- Saved projects are inputs and provenance, not trusted cached calculations.
  A headless report can be regenerated with
  `power-stage-design examples/power_stage_design/buck-loop.json --report buck.html`.
- LLC HB/FB and full switched TTPL remain their existing workspaces; the PSD
  entry passes the actual bridge choice. Shared-source consumers preserve the
  electrical source and separately identify device/capacitor screening overlays.

## Remaining work and exact resumption conditions

| Item | Current boundary | What enables the next step |
| --- | --- | --- |
| T05 exact TI strategy | Two-switch simultaneous CCM/BCM implemented; no four-switch substitution | Authorized official executable and identical input captures to identify actual gating/low-load behavior |
| T05 floating-node DCM stress | Implemented opt-in finite-Coss/Ron/diode/winding periodic state, independently validated | Nonlinear vendor Coss/recovery/startup would need a separately declared model/data; not an unimplemented ideal-node substitution |
| T11 exact TI valley rules | Explicit user-selected true valleys, first-zero clamp/release, delays and frequency ceiling are implemented | Authorized executable/input comparison to identify TI's exact policy; arbitrary burst/skip remains a separately specified extension |
| T11 nonideal turn-on | Explicit Q(V)/C(V), true later valleys/delays and finite-Ron discharge implemented and independently validated | Arbitrary burst/skip logic, gate charge/recovery and uncovered voltage ranges remain outside this declared policy |
| T18 exact TI scope | Finite Llk/Lm/Lo CCM and declared diode-secondary BCM/DCM implemented | Authorized executable comparison; public handbook describes 50–100% load, CCM/ZVS assumptions |
| T18 nonlinear commutation | Explicit constant-Coss/Ron seven-state CCM circuit and gate-event ZVS independently validated | Nonlinear Qoss/recovery/zero-load/burst require separately declared device/control models; hardware correlation still requires measurements |
| Other stage extensions | T04 zero isolated load; T10 leakage-clamp transient; T12 leakage/dead-time ZVS; T15–T17 asymmetric timing/flux/body-diode reset; T19 beyond CCM/duty≤0.5/small transfer-C ripple | A separately specified physical model and independent circuit/energy checks; these are not all blocked by TI login |
| A03/A04 higher-fidelity loss | Accepted-period waveforms are consumed; geometry/material, constant-ESR and commutation assumptions remain explicit | Vendor data and measured material/device/thermal correlation; selected-component overlays require electrical rerun when they change the circuit |
| A01 unscanned regions | Explicit Vin/load/L/C1 and supported L2/C2/ESR/ESR2/DCR Cartesian corners, ≤500 points | Deliberately selected extra dimensions/model inputs; a finite scan is not a global worst-case proof |
| Desktop/native layouts | Priority Linux/offscreen checks repaired LLC minimum to 1228×624 and exercised 1280×720, 1366×768 and 1440×900; current integrated-source artifact qualification remains pending | Fresh installed-wheel/Linux frozen proof plus both supported native CI platform startup/frozen paths; native display review for platform layout claims |
| Hardware/measurement | No complete thermal/EMI, real FRA, ISR/ADC/PWM or hardware safety acceptance | Traceable measurements and a separately approved hardware validation plan |

X01 reverse recovery remains excluded and missing loss terms remain UNKNOWN.
X03 desktop capacitor sharing is single-frequency (explicit harmonic spectra are
available in Python). X04 exposes its named approximate printed-equation RMS,
not a thermal-qualified current. Detailed LLC HB points that do not converge
invalidate export; successful neighboring points do not establish whole-domain convergence.

## Historical source and publication integrity checkpoint

The recovered development source is identified in [the validation report](PSD_VALIDATION_REPORT.md).
Its old local Git object IDs are provenance, not claimed remote commits.
Any future public integration must first reconcile current remote main without
force or replacement of the 9.8.1 tag. No such remote integration occurred in this
paused completion pass. The 9.9.0 release must contain both native packages,
both fresh frozen-process proof JSON files and SHA256SUMS before publication.
Large raw simulation records and iterative debug archives are not bundled into
the public application assets; maintained tests and these engineering boundaries
remain in the source tree.

## Historical completion checkpoint (2026-10-19)

- Publication is paused by the user's latest instruction. No remote commit,
  workflow dispatch, tag or release was created in this resumed pass.
- Explicitly correct the earlier broad A03/A05 acceptance: real two-stage LLC
  candidate consumers were absent, and LLC/Control Tools could announce successful
  C99 generation despite actual float32 verification failure. Those have been
  repaired with actual consumers; A05 has independent red/green acceptance,
  and A03 final independent report/CLI review has passed. Source-engine
  tests alone are insufficient.
- Completed focused repairs: same-temperature nominal/corner reports, exact
  integrator-branch action/help, and toolbox model identity/Chinese presentation.
  See the validation report for red/green evidence and independent checks.
- T05, QR and T18 optional dynamics have immutable parameter/source/revision
  snapshots, real connected diagrams, complete branch selection, persistence/report
  and independent physical acceptance. QR initially missed valid finite-Ron points;
  the retained independent red/green evidence documents both repaired defects.
- LLC formula-PDF source mixing is repaired and independently checked against
  actual raw TD arrays. Exact immutable waveform-input summaries and per-item
  source-aware formulas preserve retained FHA approximations explicitly. CLI
  repeat-output mixing is repaired by requiring a new TwoStage directory.
- The earlier audit cuts did not cover every cross-feature combination. The new
  priority audit found additional real advanced-suggestion and LLC schematic
  consumer defects; these are documented and repaired, not hidden by the previous
  acceptance labels. Fresh aggregate qualification remains required.
- After these paths stabilize: freeze sources, rerun affected modules, then full
  regression/RSS and installed/frozen application checks. Linux evidence cannot
  establish unrun Windows/macOS or hardware/TI executable equivalence.

## Historical artifact-gate implementation checkpoint

The historical production `packaging/launcher.py --self-test` used proof schema 3.
Current schema 4 additionally requires source-backed magnetic and integrated-LLC
actual-consumer proofs; final installed/frozen execution is pending. It retains
all original Buck/Boost/Flyback, independent Buck VMC and six-workspace gates, then
runs the optional T05/QR/T18 desktop solvers, all 47 branches, exact plotted arrays,
save/recompute/report identities and the real nominal TwoStage candidate/Apply/
save/history/report path. The verifier rejects incomplete or tampered evidence.
The solver-inclusive safety timeout is 900 s, not a startup-performance target;
no numerical tolerance was relaxed. The first source preflight and 136 contract tests
passed at the historical freeze. The priority extension below has its own fresh proof. The final wheel and actual production frozen executable must run the same
self-test outside the repository. A separate QA executable is not substituted.


## Historical priority checkpoint

- Buck/Boost/inverting Buck-Boost actual nine-mode workflows independently pass.
- Advanced T05/T18 parameter proposals now preserve typed options and support
  their existing Preview/Apply/save/report consumer path with local invalid-input
  handling. Model-specific help reflects the selected optional physics.
- T17 Full Bridge recommendations now cover its solved light-load magnetic-state
  branches, including BCM/DCM. Actual targets and component values are rechecked;
  three numerical endpoints are repaired and unsupported physical modes still fail.
- T21 LLC Full Bridge Co ESR/RΣ/DC-bus views are actual selectable same-source
  components, with saved selection and report/CSV/PNG. Earlier A02 every-component
  coverage was overstated; the new independent click audit explicitly corrects it.
- PSD is the prominent first-level Chinese/English launcher entry. Existing
  workspaces remain directly available; contact information uses one compact row.
  Real 720p PSD plotting is repaired, with scroll/collapse controls and both traces
  visible together after collapsing the schematic. LLC fit is separately checked.
- The old-source full run ended with one confirmed i18n failure and SIGKILL, not a
  pass. Translation/owned-window teardown checks now pass; the signal-kill cause
  is unconfirmed. New-source all-path regression and RSS will be run after freeze,
  followed by the opt-in long PFC run and actual installed/frozen artifact checks.
- The clean historical Linux wheel/frozen proof remains available for 1878658;
  it does not qualify these newer changes. Windows/macOS native proofs, exact TI
  software comparison and hardware correlation remain distinct unfinished gates.
- Publication is still paused. No remote write, CI dispatch, tag or release has
  been performed during this work.

### Historical priority qualification freeze

All requested Buck/Boost/Buck-Boost/full-bridge repair owners have finished their
source edits. The source is being committed locally for a fresh, sequential
full-suite/RSS → opt-in long PFC → installed-wheel/Linux-frozen qualification.
No earlier aggregate count or artifact proof is reused as a result for this source.

- Advanced/ordinary parameter consumers: 102 pass in 447.06 s.
- LLC passive/component/desktop-layout integration: 52 pass with normal exit;
  waveform lifetime, digital/research/session/TwoStage ownership: 106 pass in
  83.32 s; final three-screen replay: 3 pass in 27.67 s. These overlap and are
  not summed into an aggregate count.
- Actual configured LLC screens preserve all 12 original page objects. The
  minimum is 1228×624; waveforms retain readable per-trace heights and real
  scrolling, the two-row statistics table is compact, and the Bode canvas fits
  its actual ancestor viewport. Source arrays and ownership are unchanged.
- Production schema-3 proof now additionally requires T17 light-load suggestion
  Apply/save/report, T05 typed-option suggestion replay, and actual T21 Co ESR,
  RΣ and full-bridge DC-bus selection/export. Existing gates remain mandatory.
  210 contract cases and an actual 84.51 s source preflight pass with normal
  cleanup; installed/frozen proofs are still pending at this checkpoint.

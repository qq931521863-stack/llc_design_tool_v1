# Power Stage Designer development status

## Current direction: finish remaining functions before publication

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

## Active full-matrix repair and aggregate resource gate

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

## 9.9.0 integration

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
| T11 exact TI valley rules | Finite-Cnode positive valleys and first zero clamp implemented | Authorized executable comparison for ceiling/valley selection; controller policy for later clamped valleys, skip/burst |
| T11 nonideal turn-on | Explicit Q(V)/C(V), true later valleys/delays and finite-Ron discharge implemented and independently validated | Arbitrary burst/skip logic, gate charge/recovery and uncovered voltage ranges remain outside this declared policy |
| T18 exact TI scope | Finite Llk/Lm/Lo CCM and declared diode-secondary BCM/DCM implemented | Authorized executable comparison; public handbook describes 50–100% load, CCM/ZVS assumptions |
| T18 nonlinear commutation | Explicit constant-Coss/Ron seven-state CCM circuit and gate-event ZVS independently validated | Nonlinear Qoss/recovery/zero-load/burst require separately declared device/control models; hardware correlation still requires measurements |
| Other stage extensions | T04 zero isolated load; T10 leakage-clamp transient; T12 leakage/dead-time ZVS; T15–T17 asymmetric timing/flux/body-diode reset; T19 beyond CCM/duty≤0.5/small transfer-C ripple | A separately specified physical model and independent circuit/energy checks; these are not all blocked by TI login |
| A03/A04 higher-fidelity loss | Accepted-period waveforms are consumed; geometry/material, constant-ESR and commutation assumptions remain explicit | Vendor data and measured material/device/thermal correlation; selected-component overlays require electrical rerun when they change the circuit |
| A01 unscanned regions | Explicit Vin/load/L/C1 and supported L2/C2/ESR/ESR2/DCR Cartesian corners, ≤500 points | Deliberately selected extra dimensions/model inputs; a finite scan is not a global worst-case proof |
| Desktop/native layouts | Linux/offscreen source and wheel flows qualified; LLC whole-window minimum 1360×977, not a 900px fit | Both supported native CI platforms for startup/frozen paths; real display review for platform layout claims |
| Hardware/measurement | No complete thermal/EMI, real FRA, ISR/ADC/PWM or hardware safety acceptance | Traceable measurements and a separately approved hardware validation plan |

X01 reverse recovery remains excluded and missing loss terms remain UNKNOWN.
X03 desktop capacitor sharing is single-frequency (explicit harmonic spectra are
available in Python). X04 exposes its named approximate printed-equation RMS,
not a thermal-qualified current. Detailed LLC HB points that do not converge
invalidate export; successful neighboring points do not establish whole-domain convergence.

## Source and publication integrity

The recovered development source is identified in [the validation report](PSD_VALIDATION_REPORT.md).
Its old local Git object IDs are provenance, not claimed remote commits.
The public integration is based on the current remote main, without force or
replacement of the 9.8.1 tag. The 9.9.0 release must contain both native packages,
both fresh frozen-process proof JSON files and SHA256SUMS before publication.
Large raw simulation records and iterative debug archives are not bundled into
the public application assets; maintained tests and these engineering boundaries
remain in the source tree.

## Active completion checkpoint (2026-10-19)

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

## Strengthened artifact gate

Production `packaging/launcher.py --self-test` now uses proof schema 3. It retains
all original Buck/Boost/Flyback, independent Buck VMC and six-workspace gates, then
runs the optional T05/QR/T18 desktop solvers, all 47 branches, exact plotted arrays,
save/recompute/report identities and the real nominal TwoStage candidate/Apply/
save/history/report path. The verifier rejects incomplete or tampered evidence.
The solver-inclusive safety timeout is 900 s, not a startup-performance target;
no numerical tolerance was relaxed. The first source preflight and 136 contract tests
passed at the historical freeze. The priority extension below has its own fresh proof. The final wheel and actual production frozen executable must run the same
self-test outside the repository. A separate QA executable is not substituted.


## Latest priority checkpoint

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

### Priority qualification freeze

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

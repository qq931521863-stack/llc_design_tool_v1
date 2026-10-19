# Power Stage Designer development status

## Current direction: finish remaining functions before publication

Publication is paused. No remote push, CI dispatch, tag or release was performed.
Version 9.9.0 and release preflight changes remain local preparation only.
A new requirement-by-requirement audit is checking actual consumers and common
minimum deliverables, rather than treating bounded row labels as completion.
Work is continuing on explicit T05 floating-node dynamics, QR valley/data options
and PSFB finite-Coss commutation; these extensions are not yet accepted or enabled.
Concrete audit findings include calculator-session model identity, everyday Chinese
result presentation and the precise documented Type-I network setup. A03 is now
PARTIAL: TwoStageLLCOptimizer has model/tests but no production desktop/CLI/API
caller. Its asynchronous candidate/application/report workflow is being added.
A report/corner junction-temperature identity mismatch has an independent red test.

## 9.9.0 integration

The M0–M5 implementation and follow-on requirement repairs are integrated for
release qualification. The maintained scope is the [50-ID matrix](PSD_FEATURE_MATRIX.md);
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
| T05 floating-node DCM stress | Ideal off-state equality does not determine node common-mode voltage | Explicit Coss/Ron/clamp topology and initial charge; solve the full switched periodic node state before reporting finite voltage RMS |
| T11 exact TI valley rules | Finite-Cnode positive valleys and first zero clamp implemented | Authorized executable comparison for ceiling/valley selection; controller policy for later clamped valleys, skip/burst |
| T11 nonideal turn-on | Nonlinear Coss and finite positive-voltage turn-on impulse RMS excluded | Device charge/energy curves and finite transition model; independent event/energy validation |
| T18 exact TI scope | Finite Llk/Lm/Lo CCM and declared diode-secondary BCM/DCM implemented | Authorized executable comparison; public handbook describes 50–100% load, CCM/ZVS assumptions |
| T18 nonlinear commutation | Dead-time/Coss/recovery ZVS proof, zero-load/burst excluded | Controller timings and device curves, followed by switched commutation validation |
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
  C99 generation despite actual float32 verification failure. Those are being
  implemented and independently reviewed; source-engine tests are insufficient.
- Completed focused repairs: same-temperature nominal/corner reports, exact
  integrator-branch action/help, and toolbox model identity/Chinese presentation.
  See the validation report for red/green evidence and independent checks.
- T05 and QR optional dynamic kernels are implemented with explicit immutable
  parameter/source/revision snapshots. Real GUI, diagrams, persistence/report,
  invalidation and independent physical acceptance are currently being integrated.
- T18 switched commutation prototype is being converted into the same consumer
  contract, including separate channel/body/Coss currents and event-based ZVS.
- After these paths stabilize: freeze sources, rerun affected modules, then full
  regression/RSS and installed/frozen application checks. Linux evidence cannot
  establish unrun Windows/macOS or hardware/TI executable equivalence.

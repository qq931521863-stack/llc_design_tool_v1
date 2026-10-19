# Power Stage Designer feature matrix

## Latest application qualification and supported scope (2026-10-19)

Application source `4f9f5b4513a62fa9c5effb1b4cad5beacf36daac` completed the
configured Linux/offscreen regression: **4140 passed, 2 opt-in skips, 2 historical
expected failures**, 43 warnings, exit 0. All 760 tracked-file hashes were
unchanged. The aggregate retains its two skips; each long test passed in a separate serial run on the same exact application source.
The five independent integrated-LLC consumer, identity, boundary, FHA-projection
and TD-label scripts also pass on this exact application source.

| Requested priority | Implemented and software-checked scope | Limits that remain |
| --- | --- | --- |
| T01 Buck | Ideal periodic CCM/BCM/DCM, finite diode drop, same-result component V/I and stress, sizing, save/recompute/report; VMC and peak-CMC analog plants | No synchronous forced-CCM or nonlinear switching model; analog plants require strict CCM |
| T02 Boost | Ideal periodic CCM/BCM/DCM, finite diode drop, actual component/sizing/project/report workflows; peak-CMC analog plant | No synchronous forced-CCM or nonlinear switching model; analog plant requires strict CCM |
| T03 Inverting Buck-Boost | Signed negative output, asynchronous CCM/BCM/DCM, actual component/sizing/project/report workflows; peak-CMC analog plant | Forced synchronous operation and nonlinear parasitics excluded; analog plant requires strict CCM |
| T05 Two-Switch Buck-Boost | Simultaneous-gated ideal CCM/BCM and opt-in finite-Coss/Ron/diode/winding periodic dynamics, including floating-node DCM; typed physical-option suggestions and actual consumers | Constant-capacitance/resistance assumptions; nonlinear Coss, recovery and startup excluded; no four-switch substitution or generic analog-loop plant; exact TI control policy unverified |
| T17 Full Bridge | Symmetric center-tap rectification; ordinary/joint CCM, BCM and DCM; actual Lout/Lm/ratio target reanalysis and component/project/report consumers | Asymmetric timing/flux, primary body-diode-clamped reset, nonlinear parasitics and unsupported commutation sub-modes excluded; no generic analog-loop plant |
| T18 Phase-Shifted Full Bridge | Ideal CCM/diode-secondary BCM/DCM; opt-in seven-state constant-Coss/Ron positive-CCM commutation and per-gate ZVS; explicit Lout/Lm/ratio targets | Nonlinear Qoss, recovery, zero-load/burst and switched closed-loop behavior excluded; no generic analog-loop plant; exact TI policy unverified; separate finite-Coss joint-sizing long test passed |
| T20 LLC Half Bridge | Existing FHA/HB/ideal TD engine with actual bridge identity, component views, saved-input recomputation and reports | Model-specific convergence and commutation limits; ideal midpoint-source current is underdetermined and remains unavailable |
| T21 LLC Full Bridge | Existing FHA/HB/ideal TD engine, Co ESR, modeled series resistance and signed DC-bus component views; separate or integrated resonant magnetics with actual consumers | Model-specific convergence limits; integrated magnetics requires declared linear two-winding data, physical/APR flux separation and feasible geometry; CT/star and nonzero magnetizing-series-R paths are unsupported |

T15 Half Bridge and T16 Push-Pull also support ordinary/joint CCM, BCM and DCM
sizing, with their own pulse amplitudes and turns conventions. T15's finite split-C
ripple reconstruction retains a nominal-midpoint first-order magnetic model and
a 10% individual ripple limit; it is not a coupled finite-C transient model.

The separate CSC addition preserves **630 explicit part numbers, 318 source-specific
geometries and 44 printed formulas**. It supplements the **46 complete presets and
137 legacy references**; geometry/availability data do not become complete material
models. Missing conventions, data and source conflicts remain explicit.

These are bounded software capabilities. Unsupported physical/model domains are
listed above and in the detailed rows; they are not all blocked by TI access.
Separately, exact TI executable correlation, native Windows/macOS qualification,
new device/material/thermal/bench correlation and hardware safety validation
remain unverified. The installed-wheel/Linux frozen runtime checks and both separately executed long tests pass; the validation report records their individual source identities and audit results. Formal release remains paused. Final artifact/push
SHA and any CI/docs-only difference are recorded separately in the
[validation report](PSD_VALIDATION_REPORT.md).

Earlier dated qualification sections below are retained as historical checkpoints.
Their failures and incomplete runs remain visible; their then-pending statements
are superseded by the latest evidence above, not combined into its pass count.

## Historical qualification status after the diagnostic tail (2026-10-19)

The T15 source-boundary, new shell translations, cancellation-aware test double
and immutable historical release fixture are repaired with focused evidence.
The historical LF/CRLF negative tests now establish the intended raw-proof/content
failure, not a coincidental outdated rewrite rejection. No production numerical
or release gate was weakened. A fresh complete-source run is required; separate
red/green diagnostic segments are not an aggregate PASS. Model-domain limitations
below remain unchanged, and publication remains paused.


## Historical T15 source-boundary correction (2026-10-19)

T15's equal split-C/stiff-total-bus model requires instantaneous source current
(Q1+Q2)/2, not the reported input-current cycle mean. An independent 16-vector
ODE audit confirms source/branch currents and capacitor charge; the aggregate
all-net test's old external-source oracle was wrong and is corrected. The current
full-source run is RED (1353 pass, 1 skip, 1 fail), pending complete rerun after
this repair. No topology/kernel tolerance was relaxed. A subsequent diagnostic-only remainder
found 14 new shell strings missing from en/ja/ko; all 42 translations and actual language
switch/restore acceptance now pass in the 16-case i18n/lifecycle group, pending
the fresh aggregate.

T15 remains a first-order nominal-midpoint magnetic model with finite-C ripple
reconstruction, not a fully coupled finite-C transient simulation. The existing
10%-peak-to-peak split-cap ripple limit can leave up to approximately 5% on-pulse
instantaneous nominal-power discrepancy. This declared approximation, asymmetric
balance/body-diode/leakage domains and other remaining model extensions are not
silently promoted by the KCL repair. X12's input is explicitly labeled average
current; its operating-point calculation and imported metric are unchanged.


## Historical acceptance additions and corrections (2026-10-19)

- New user requirement: integrated LLC transformer-leakage resonance is implemented
  alongside the separate-Lr default (`c06f1d5`), separate from the original 50 IDs.
  Two-winding matrix/open-short inputs transform Lr, APR Lm and effective ratio
  together; actual turns, physical flux, P/2-S-P/2 winding feasibility and one-body
  loss/thermal accounting remain explicit. Actual desktop, locked-magnetics
  optimization, saved identity and report consumers have focused independent
  acceptance. Fresh merged-source aggregate/artifact acceptance remains pending.
  FHA/EDF Vμ is explicitly a display projection; accepted TD uses mapped physical
  flux. CT/star and nonzero magnetizing-series-R paths are rejected, not approximated
  as this model. See [workflow and limits](magnetics/integrated_llc_workflow.md).
  The comparison workspace now has cooperative Cancel and an explicit total
  execution budget. These stop HB/TD at numerical checkpoints without weakening
  convergence, publishing partial results or reviving stale report ownership.

- T18 parameter suggestions: the optional Lm target must be off by default and
  must not silently carry between topologies or new projects. A real aggregate
  counterexample found the old 0.1 A default changing untargeted Lm. The unchanged
  workflow and new explicit/untargeted persistence tests now pass after correction.
- Cross-cutting GUI lifetime: final owner destruction must also release separate
  pyqtgraph menu/parameter/file-dialog trees and completed worker objects. Focused
  real-exit, reuse, shared-owner and transient-dialog repairs pass. The common
  asynchronous repair passes 85 focused cases and independent nine-producer
  repeated-job/owner-disposal and two queue-cancellation replays. Integrated
  aggregate qualification remains a cross-cutting gate, not another topology.
- New user requirement: complete the CSC magnetic-core selection database from
  four supplied PDFs, including source-specific models, actual selection,
  calculations, saved identity and reports where the data permits. Source
  integration, strict declaration gates and real TTPL/Vienna/LLC consumers are
  implemented (`d51739d`, `3c07a0f`); 52 focused tests and 20 independent probes
  pass, with final artifact/aggregate qualification pending. This is separate from the original
  50 IDs; geometry-only data and missing model conventions must not be promoted to
  fully calculable material records through legacy defaults.

The stopped `0dfd790` aggregate recorded 965 PASS and 1 FAIL, not a complete pass.
All new changes require fresh integrated-source qualification before any release.

## Historical full-matrix completion repairs (2026-10-19)

The task was re-read in full and all 50 IDs traced to their actual inputs, kernels,
desktop consumers, persistence and exports. This found real software omissions;
TI login was not their cause. Prior statements implying only external TI/native
platform gates remained were too broad and are explicitly corrected here.

| Requirement repair | Current implemented change | Current acceptance |
| --- | --- | --- |
| T15 finite split capacitors | Equal split-C currents from stiff-total-bus node KCL, opposite voltage ripple; positive voltage and 10%-of-half-bus ripple limits in both branches. Magnetic model still uses the declared nominal midpoint. | Independent 64 inverse cases and 8 node/RMS/charge/energy observations pass; old wrong bus sum and 1 pF acceptance reproduced. |
| T15/T16 suggestions | Reuse T17 charge/flux inverse with correct half-bridge/push-pull amplitudes through ordinary/joint CCM, BCM and DCM. | Independent kernel checks and actual suggestion/Apply/restore/report consumers pass; new-source aggregate pending. |
| T18 Lm suggestion | Explicit separate magnetizing-ripple target, selected-model Lout/Lm/ratio solve; untargeted Lm remains user supplied. | Ideal MNA/IVP and real desktop checks pass. Optional finite-Coss three-target recovery plus independent Radau/event extrema/period checks pass (18 evaluations, 456.36 s). |
| X01/X02/X07/X10/X12 interaction | Debounced current-result refresh; invalid library temperature rejected; inactive branch fields preserved without blocking active inputs; per-page imported source retained. | Real-Qt/legacy checks and heavy file-open transactional tests pass. Independent audit additionally reproduced/repaired malformed-library partial field mutation and stale export. |
| L/C and A06 | Canonical validated loop defaults before restore; preserve accepted topology/mode aliases and identity; persist fc/PM/preferred series as intent; debounced fast Bode reanalysis. | 33 new real Qt tests and independent five-class restore/race review pass; 369 calculator/desktop adjacent and 187 modal/identity consumer cases pass before final freeze. |
| Native Qt ownership | Diagnose retained owners separately from allocator RSS; destroy discarded selectors and owned windows on actual application exit while preserving deliberate workspace reuse. | Production/test disposal repaired; 24 lifecycle/i18n cases pass, real repeated workspace reuse is bounded, transient dialogs and final owned roots are destroyed. Full aggregate gate remains. |

No aggregate pass, new installed-wheel/frozen proof or complete baseline acceptance
is claimed for these new changes until the new-source qualification finishes.

## Scope and evidence vocabulary

The 9.9.0 integration adds a dedicated desktop/Python workspace to the 9.8.1
baseline (`e02b0de9c73b46b0990ce337d526e174b0980f4f`). It reuses the existing
LLC, PFC, analog/digital controller, FRA, device-library and reporting engines.
It does not establish new Web or MCP visualization parity.

The comparison baseline is TI Power Stage Designer 01.00.00.0D (2023-02-23),
[SLVUBB4B](https://www.ti.com/lit/ug/slvubb4b/slvubb4b.pdf), rather than later
supplementary articles. [Official product page](https://www.ti.com/tool/POWERSTAGE-DESIGNER).

There are **33 top-level alignment items** (T01–T21 + X01–X12), with bounded
capability labels of **30 VERIFIED_IN_SCOPE and 3 PARTIAL**. The six plants and
five networks are 11 nested X11 subitems; together with six enhancements, the
table lists **50 explicit IDs** without inflating the denominator. These labels
are not release acceptance, complete TI equivalence or hardware certification.
The 2026-10-19 re-audit found and repaired real gaps across T15/T16/T18,
X01/X02/X07/X10/X12, L/C and A06. The current integrated application regression
is complete at the exact SHA above; opt-in long/artifact/native gates remain
separate and pending in this proposal. All six enhancements have actual consumers
in their stated software domains. The historical A03 missing-consumer/formula-source
and A05 false-success C99 findings and their independent red/green corrections
remain below, so the earlier overbroad acceptance is not erased.

- VERIFIED_IN_SCOPE means the implemented calculation has an actual consumer,
  independent model checks, and persistence/invalidation checks where applicable.
- PARTIAL identifies a usable bounded implementation with the specific missing
  alignment or physical domain named in its row.
- UNKNOWN means evidence or a physical input is unavailable; it never means zero loss.
- No status certifies full TI executable parity, vendor-model accuracy or hardware safety.

Historical development-source identifiers in evidence cells identify the retained
pre-integration qualification snapshots. The final recovered source was checked
byte-for-byte before release-only changes; see [validation and provenance](PSD_VALIDATION_REPORT.md).
The current completion/resumption gates are in [development status](PSD_DEVELOPMENT_STATUS.md).

| ID / feature | baseline_reference | supported_domain | existing_implementation | actual_callers | desktop_entry | report_or_export | api_scope | capability_status | validation_evidence | remaining_work |
|---|---|---|---|---|---|---|---|---|---|---|
| T01 Buck | SLVUBB4B | M1 ideal periodic model, diode Vf; CCM/DCM | power_stage_design/topologies.py (piecewise state solution) | calculate_stage → PowerStageMainWindow/report/CLI | Launcher → Power Stage Designer | JSON restore+recompute, HTML and waveform CSV | Desktop/Python target; no new Web/MCP claim | VERIFIED_IN_SCOPE | G01–G03; independent root/quadrature + real Qt workflows; CCM/BCM/DCM/Vf/domain boundaries | TI executable/hardware unavailable; no synchronous forced-CCM claim |
| T02 Boost | SLVUBB4B | M1 ideal periodic model, diode Vf; CCM/DCM | power_stage_design/topologies.py (piecewise state solution) | calculate_stage → PowerStageMainWindow/report/CLI | Launcher → Power Stage Designer | JSON restore+recompute, HTML and waveform CSV | Desktop/Python target; no new Web/MCP claim | VERIFIED_IN_SCOPE | G01–G03; independent root/quadrature + real Qt workflows; CCM/BCM/DCM/Vf/domain boundaries | TI executable/hardware unavailable; no synchronous forced-CCM claim |
| T03 Inverting Buck-Boost | SLVUBB4B; TI topology references in validation report | Ideal asynchronous CCM/BCM/DCM; signed negative output | power_stage_design/topologies.py + waveforms.py + schematic.py | calculate_stage → GUI, project, report, CLI | Launcher → Power Stage Designer → reference preset | Recomputed JSON + HTML + waveform CSV | Desktop/Python; no new Web/MCP coverage | VERIFIED_IN_SCOPE | Output node polarity, charge balance, physical switching node KVL; actual Qt every-component click/save/restore/report | Forced synchronous operation and nonlinear parasitics excluded; TI executable slider-domain parity and hardware remain UNKNOWN |
| T04 Fly-Buck | SLVUBB4B; TI topology references in validation report | Periodic synchronous primary + isolated diode secondary; finite Lm/Lleak and both output capacitors; positive isolated load | power_stage_design/topologies.py + waveforms.py + schematic.py | calculate_stage → GUI, project, report, CLI | Launcher → Power Stage Designer → reference preset | Recomputed JSON + HTML + waveform CSV | Desktop/Python; no new Web/MCP coverage | VERIFIED_IN_SCOPE | Independent flux/charge-coordinate matrix-exponential observer; dual-output energy and diode events; actual Qt every-component click/save/restore/report | No zero-isolated-load solution, nonlinear parasitics or switching-transition/ZVS claim; no Flyback triangular substitution; TI executable slider-domain parity and hardware remain UNKNOWN |
| T05 Buck-Boost 2-Switch | SLVUBB4B; TI topology references in validation report | Ideal simultaneous-gated CCM/BCM plus opt-in four-state finite-Coss/Ron/diode/winding floating-node periodic dynamics | topologies.py + two_switch_dynamics.py + advanced_gui.py + schematic.py | calculate_stage → GUI, project, report, CLI | Power Stage Designer → Advanced physical parameters (opt-in) | Recomputed JSON + HTML + waveform CSV | Desktop/Python; no new Web/MCP coverage | PARTIAL | Dual-diode branch KVL, positive/negative conversion regions and separate blocking nodes; actual Qt every-component click/save/restore/report | Floating-node DCM now has explicit source/revision inputs and full node-charge periodic solution; independent five-case original-network ngspice checks, all branch RMS and energy pass. Constant capacitances/resistances and specified load remain assumptions; nonlinear Coss/recovery/startup are excluded. Four-switch topology is not substituted; bounded-model software workflow verified; exact TI switching/valley/load rules BLOCKED by official myTI login + export approval (anonymous download 403); hardware UNKNOWN |
| T06 SEPIC | SLVUBB4B; TI topology references in validation report | Two independent inductors, transfer capacitor, asynchronous CCM/DCM and finite Vf | power_stage_design/topologies.py + waveforms.py + schematic.py | calculate_stage → GUI, project, report, CLI | Launcher → Power Stage Designer → reference preset | Recomputed JSON + HTML + waveform CSV | Desktop/Python; no new Web/MCP coverage | VERIFIED_IN_SCOPE | Independent multi-state periodic/charge root and branch KCL including opposed DCM currents; actual Qt every-component click/save/restore/report | Small-ripple voltage approximation; coupled-inductor, parasitic ringing and saturation excluded; TI executable slider-domain parity and hardware remain UNKNOWN |
| T07 ZETA | SLVUBB4B; TI topology references in validation report | Two independent inductors, transfer capacitor, asynchronous CCM/DCM and finite Vf | power_stage_design/topologies.py + waveforms.py + schematic.py | calculate_stage → GUI, project, report, CLI | Launcher → Power Stage Designer → reference preset | Recomputed JSON + HTML + waveform CSV | Desktop/Python; no new Web/MCP coverage | VERIFIED_IN_SCOPE | Independent multi-state periodic/charge root and branch KCL including opposed DCM currents; actual Qt every-component click/save/restore/report | Small-ripple voltage approximation; coupled-inductor, parasitic ringing and saturation excluded; TI executable slider-domain parity and hardware remain UNKNOWN |
| T08 Ćuk | SLVUBB4B; TI topology references in validation report | Negative output, two inductors, transfer capacitor, asynchronous CCM/DCM and finite Vf | power_stage_design/topologies.py + waveforms.py + schematic.py | calculate_stage → GUI, project, report, CLI | Launcher → Power Stage Designer → reference preset | Recomputed JSON + HTML + waveform CSV | Desktop/Python; no new Web/MCP coverage | VERIFIED_IN_SCOPE | Independent nodal polarities, capacitor charge and branch KCL; actual Qt every-component click/save/restore/report | Small-ripple voltage approximation; coupled-inductor, parasitic ringing and saturation excluded; TI executable slider-domain parity and hardware remain UNKNOWN |
| T09 Flyback | SLVUBB4B | M1 ideal periodic model, diode Vf; CCM/DCM | power_stage_design/topologies.py (piecewise state solution) | calculate_stage → PowerStageMainWindow/report/CLI | Launcher → Power Stage Designer | JSON restore+recompute, HTML and waveform CSV | Desktop/Python target; no new Web/MCP claim | VERIFIED_IN_SCOPE | G01–G03; independent root/quadrature + real Qt workflows; CCM/BCM/DCM/Vf/domain boundaries | TI executable/hardware unavailable; no synchronous forced-CCM claim |
| T10 2-Switch Flyback | SLVUBB4B; TI topology references in validation report | Two-switch Flyback CCM/BCM/DCM with ideal zero-leakage input clamp and finite Vf | power_stage_design/topologies.py + waveforms.py + schematic.py | calculate_stage → GUI, project, report, CLI | Launcher → Power Stage Designer → reference preset | Recomputed JSON + HTML + waveform CSV | Desktop/Python; no new Web/MCP coverage | VERIFIED_IN_SCOPE | Winding gating, turns convention, clamp headroom and branch stress; actual Qt every-component click/save/restore/report | Leakage-clamp transient and unequal switching timings excluded; zero ideal clamp current is not a hardware loss estimate; TI executable slider-domain parity and hardware remain UNKNOWN |
| T11 QR Flyback | SLVUBB4B; TI topology references in validation report | Ideal first-valley model plus opt-in later true valleys, zero clamp/release, explicit delays, Q(V)/C(V) data and finite-Ron lower/higher peak roots; fs ceiling | topologies.py + qr_dynamics.py + advanced_gui.py + schematic.py | calculate_stage → GUI, project, report, CLI | Power Stage Designer → Advanced physical parameters (opt-in) | Recomputed JSON + HTML + waveform CSV | Desktop/Python; no new Web/MCP coverage | PARTIAL | Independent LC solve_ivp, demagnetization, valley timing, actual period, energy and quadrature; actual Qt every-component click/save/restore/report | Later true valleys and finite discharge RMS are implemented under explicit controller policy. Two independent finite-Ron false-rejection counterexamples were repaired; matrix-exponential roots/maxima, nonlinear data/delay sweeps and energy checks pass. Arbitrary burst/skip controller, gate charge, recovery and unprovided curve regions remain excluded; bounded-model software workflow verified; exact TI switching/valley/load rules BLOCKED by official myTI login + export approval (anonymous download 403); hardware UNKNOWN |
| T12 Active Clamp Forward | SLVUBB4B; TI topology references in validation report | Complementary ideal active-clamp Forward; bipolar magnetizing reset, finite Cclamp with ripple <=10%; output CCM/DCM | power_stage_design/topologies.py + waveforms.py + schematic.py | calculate_stage → GUI, project, report, CLI | Launcher → Power Stage Designer → reference preset | Recomputed JSON + HTML + waveform CSV | Desktop/Python; no new Web/MCP coverage | VERIFIED_IN_SCOPE | Independent transformer KCL, reset/forward/freewheel intervals and charge balance; actual Qt every-component click/save/restore/report | Dead time, leakage-resonant commutation and ZVS are not solved; no claim for those domains; TI executable slider-domain parity and hardware remain UNKNOWN |
| T13 Forward | SLVUBB4B; TI topology references in validation report | Forward with explicit reset winding/turns, finite Lm and output CCM/DCM | power_stage_design/topologies.py + waveforms.py + schematic.py | calculate_stage → GUI, project, report, CLI | Launcher → Power Stage Designer → reference preset | Recomputed JSON + HTML + waveform CSV | Desktop/Python; no new Web/MCP coverage | VERIFIED_IN_SCOPE | Independent magnetizing reset separate from output freewheel; energy returned to input; actual Qt every-component click/save/restore/report | Insufficient reset rejected; leakage overshoot and nonlinear core excluded; TI executable slider-domain parity and hardware remain UNKNOWN |
| T14 2-Switch Forward | SLVUBB4B; TI topology references in validation report | Two-switch Forward with two reset clamp diodes; explicit Lm and output CCM/DCM | power_stage_design/topologies.py + waveforms.py + schematic.py | calculate_stage → GUI, project, report, CLI | Launcher → Power Stage Designer → reference preset | Recomputed JSON + HTML + waveform CSV | Desktop/Python; no new Web/MCP coverage | VERIFIED_IN_SCOPE | Independent transformer/reset branch KCL and diode-loss energy; actual Qt every-component click/save/restore/report | Insufficient reset rejected; leakage transients and unequal switch timing excluded; TI executable slider-domain parity and hardware remain UNKNOWN |
| T15 Half Bridge | SLVUBB4B; TI topology references in validation report | Symmetric bipolar full period; center-tap rectification; ordinary/joint CCM/BCM/DCM and L/Lm/n target suggestions; fixed total bus with equal split-C ripple <=10% of Vin/2 | power_stage_design/topologies.py + waveforms.py + schematic.py | calculate_stage → GUI, project, report, CLI | Launcher → Power Stage Designer → reference preset | Recomputed JSON + HTML + waveform CSV | Desktop/Python; no new Web/MCP coverage | VERIFIED_IN_SCOPE | Ampere-turns, 2fs rectified ripple, full-period stress and independent light-load demagnetization; actual Qt every-component click/save/restore/report | Asymmetric timing/flux, primary body-diode-clamped reset and nonlinear parasitics excluded; TI executable slider-domain parity and hardware remain UNKNOWN |
| T16 Push-Pull | SLVUBB4B; TI topology references in validation report | Symmetric push-pull; center-tapped turns convention; ordinary/joint CCM/BCM/DCM and actual L/Lm/n suggestions | power_stage_design/topologies.py + waveforms.py + schematic.py | calculate_stage → GUI, project, report, CLI | Launcher → Power Stage Designer → reference preset | Recomputed JSON + HTML + waveform CSV | Desktop/Python; no new Web/MCP coverage | VERIFIED_IN_SCOPE | Ampere-turns, full input doubling switch stress, 2fs ripple and magnetic demagnetization; actual Qt every-component click/save/restore/report | Asymmetric timing/flux, primary body-diode-clamped reset and nonlinear parasitics excluded; TI executable slider-domain parity and hardware remain UNKNOWN |
| T17 Full Bridge | SLVUBB4B; TI topology references in validation report | Symmetric bipolar full bridge; center-tap rectification; ordinary/joint-magnetic CCM, BCM and DCM; actual L/Lm/n target suggestions | power_stage_design/topologies.py + bridge_sizing.py + waveforms.py + schematic.py | calculate_stage → GUI, project, report, CLI | Launcher → Power Stage Designer → reference preset | Recomputed JSON + HTML + waveform CSV | Desktop/Python; no new Web/MCP coverage | VERIFIED_IN_SCOPE | Full bridge amplitude, primary/secondary KCL, full-period stress and magnetic demagnetization; actual Qt every-component click/save/restore/report | Asymmetric timing/flux, primary body-diode-clamped reset and nonlinear parasitics excluded; TI executable slider-domain parity and hardware remain UNKNOWN |
| T18 Phase-Shifted Full Bridge | SLVUBB4B; TI topology references in validation report | Ideal PSFB CCM/diode-secondary DCM/BCM plus opt-in seven-state constant-Coss/Ron positive-CCM commutation, finite dead time and per-gate ZVS; explicit Lout/Lm/ratio target sizing | topologies.py + psfb_sizing.py + psfb_dynamics.py + advanced_gui.py + schematic.py | calculate_stage → GUI, project, report, CLI | Power Stage Designer → Advanced physical parameters (opt-in) | Recomputed JSON + HTML + waveform CSV | Desktop/Python; no new Web/MCP coverage | PARTIAL | Independent three-state differential laws, leg KVL, circulation, antisymmetry and energy; actual Qt every-component click/save/restore/report | Opt-in channel/body/Coss currents, finite-dead-time gate-state ZVS and passive energy closure are independently checked against original-network ngspice at 5 A and 15 A. Constant-Coss positive-CCM scope is explicit; nonlinear Qoss, recovery, zero-load/burst and switched closed-loop behavior remain excluded; bounded-model software workflow verified; exact TI switching/valley/load rules BLOCKED by official myTI login + export approval (anonymous download 403); hardware UNKNOWN |
| T19 Series Capacitor Buck | SLVUBB4B; TI topology references in validation report | 180-degree two-phase series-capacitor Buck CCM; duty <=0.5; finite transfer C with ripple <=10% | power_stage_design/topologies.py + waveforms.py + schematic.py | calculate_stage → GUI, project, report, CLI | Launcher → Power Stage Designer → reference preset | Recomputed JSON + HTML + waveform CSV | Desktop/Python; no new Web/MCP coverage | VERIFIED_IN_SCOPE | Independent nodal volt-seconds/charge; Q1b blocks full Vin and Q2a carries combined current; actual Qt every-component click/save/restore/report | DCM, unbalanced drive, startup and large capacitor ripple excluded; TI executable slider-domain parity and hardware remain UNKNOWN |
| T20 LLC Half Bridge | SLVUBB4B; existing LLC engine | Existing FHA/HB/ideal switched TD; actual midpoint-return HB has zero added Cr DC offset; separate PGND-return variant adds Vbus/2 | llc_design/core, analysis, models; no duplicate PSD tank | PSD actual bridge selection → original LLCMainWindow → analyzer → same report | PSD → 既有 LLC 工作区; standalone returns to same PSD window | LLC JSON/Markdown calculation book and model export | Existing LLC Desktop/Python; no new generic Web claim | VERIFIED_IN_SCOPE | 45aabcb: 148 focused + 32 shared-consumer tests; independent 123 passed checks, physical rendered-net reconstruction, exact V/I and total/AC RMS, actual save/recompute/export; HB discriminating bias case | Ideal-model convergence/domain limits remain; detailed HB nominal/off-nominal probes can fail and invalidate export. The whole-window minimum was repaired to 1228×624; real 720p/768p/900p page, scroll, waveform, digital and TwoStage consumers now pass at 100% Linux Qt scale. Vendor commutation/ZVS and hardware validity remain model-specific |
| T21 LLC Full Bridge | SLVUBB4B; existing LLC engine | Existing FHA/HB/ideal switched TD; full-bridge amplitude and full-bridge synchronous rectification | llc_design/core, analysis, models; no duplicate PSD tank | PSD actual bridge selection → original LLCMainWindow → analyzer → same report | PSD → 既有 LLC 工作区; standalone returns to same PSD window | LLC JSON/Markdown calculation book and model export | Existing LLC Desktop/Python; no new generic Web claim | VERIFIED_IN_SCOPE | 45aabcb: 148 focused + 32 shared-consumer tests; independent 123 passed checks, physical rendered-net reconstruction, exact V/I and total/AC RMS, actual save/recompute/export; HB discriminating bias case | Ideal-model convergence/domain limits remain; detailed HB nominal/off-nominal probes can fail and invalidate export. The whole-window minimum was repaired to 1228×624; real 720p/768p/900p page, scroll, waveform, digital and TwoStage consumers now pass at 100% Linux Qt scale. Vendor commutation/ZVS and hardware validity remain model-specific |
| X01 FET Losses Calculator | SLVUBB4B | Hard-switch main or synchronous rectifier; temperature RDS(on), separate overlap/Eoss/dead-time diode/driver terms; reverse recovery excluded; missing terms remain unknown | power_stage_design/toolbox.py; existing bus-capacitor helper reused for G04 | calculate_tool → ToolBoxWidget → export; supported stage mappings checked by identity | Power Stage Designer → 工具箱 | Separate reproducible calculator JSON; JSON/CSV/text result export | Desktop/Python target; no new Web/MCP claim | VERIFIED_IN_SCOPE | 100 focused calculator/Qt tests + 76 independent physical tests; G04 independent 5-case checks | Reverse recovery and absent switching/driver/thermal data remain unknown; stage import supports only explicitly mapped main Q1 waveforms. No complete-efficiency or hardware claim. |
| X02 Load Step Calculator | SLVUBB4B | TI VMC/CMC load-step approximation; finite positive effective excursion and phase-margin factor | power_stage_design/toolbox.py; existing bus-capacitor helper reused for G04 | calculate_tool → ToolBoxWidget → export; supported stage mappings checked by identity | Power Stage Designer → 工具箱 | Separate reproducible calculator JSON; JSON/CSV/text result export | Desktop/Python target; no new Web/MCP claim | VERIFIED_IN_SCOPE | 100 focused calculator/Qt tests + 76 independent physical tests; G04 independent 5-case checks | Small-signal VMC/CMC approximation, not time-domain load-transient proof; invalid effective excursion/phase-margin branches reject. |
| X03 Capacitor Current Sharing | SLVUBB4B | Desktop single-frequency complex C/ESR/ESL parallel branches; Python additionally accepts explicit distinct-frequency RMS spectra | power_stage_design/toolbox.py; existing bus-capacitor helper reused for G04 | calculate_tool → ToolBoxWidget → export; supported stage mappings checked by identity | Power Stage Designer → 工具箱 | Separate reproducible calculator JSON; JSON/CSV/text result export | Desktop/Python target; no new Web/MCP claim | VERIFIED_IN_SCOPE | 100 focused calculator/Qt tests + 76 independent physical tests; G04 independent 5-case checks | Desktop exposes sinusoidal sharing; explicit harmonic spectra are implemented and independently tested in Python only. No automatic broadband waveform decomposition or spectral desktop UI is claimed. |
| X04 AC/DC Bulk Capacitor | SLVUBB4B | AC bridge-bulk sizing by SLVUBB4B eq13; separate DC hold-up energy tool; documented RMS example discrepancy | power_stage_design/toolbox.py; existing bus-capacitor helper reused for G04 | calculate_tool → ToolBoxWidget → export; supported stage mappings checked by identity | Power Stage Designer → 工具箱 | Separate reproducible calculator JSON; JSON/CSV/text result export | Desktop/Python target; no new Web/MCP claim | VERIFIED_IN_SCOPE | 100 focused calculator/Qt tests + 76 independent physical tests; G04 independent 5-case checks | Printed eq13 RMS estimate differs from both TI screenshot and independent periodic integration; not thermal-validated RMS. |
| X05 RCD Snubber for Flyback | SLVUBB4B | Flyback RCD clamp with explicit leakage and reflected voltage; dissipative clamp margin required | power_stage_design/toolbox.py; existing bus-capacitor helper reused for G04 | calculate_tool → ToolBoxWidget → export; supported stage mappings checked by identity | Power Stage Designer → 工具箱 | Separate reproducible calculator JSON; JSON/CSV/text result export | Desktop/Python target; no new Web/MCP claim | VERIFIED_IN_SCOPE | 100 focused calculator/Qt tests + 76 independent physical tests; G04 independent 5-case checks | Specified leakage/reflected-voltage dissipative approximation; no measured ringing, nonlinear Coss or hardware clamp validation. |
| X06 RC Snubber | SLVUBB4B | RC damping from measured original/added-C ring frequencies; passive inferred L/C | power_stage_design/toolbox.py; existing bus-capacitor helper reused for G04 | calculate_tool → ToolBoxWidget → export; supported stage mappings checked by identity | Power Stage Designer → 工具箱 | Separate reproducible calculator JSON; JSON/CSV/text result export | Desktop/Python target; no new Web/MCP claim | VERIFIED_IN_SCOPE | 100 focused calculator/Qt tests + 76 independent physical tests; G04 independent 5-case checks | Requires user-measured ring frequencies and added capacitance; no measurement or hardware optimum fabricated. |
| X07 Output Voltage Resistor Divider | SLVUBB4B | E24/E48/E96 divider and parallel pair search; tolerance endpoints | power_stage_design/toolbox.py; existing bus-capacitor helper reused for G04 | calculate_tool → ToolBoxWidget → export; supported stage mappings checked by identity | Power Stage Designer → 工具箱 | Separate reproducible calculator JSON; JSON/CSV/text result export | Desktop/Python target; no new Web/MCP claim | VERIFIED_IN_SCOPE | 100 focused calculator/Qt tests + 76 independent physical tests; G04 independent 5-case checks | Finite E24/E48/E96 range and independent tolerance endpoints; actual selected parallel network is recomputed. No resistor thermal qualification. |
| X08 Dynamic Analog Output Scaling | SLVUBB4B | Three-resistor ideal analog-voltage injection KCL and endpoint settings | power_stage_design/toolbox.py; existing bus-capacitor helper reused for G04 | calculate_tool → ToolBoxWidget → export; supported stage mappings checked by identity | Power Stage Designer → 工具箱 | Separate reproducible calculator JSON; JSON/CSV/text result export | Desktop/Python target; no new Web/MCP claim | VERIFIED_IN_SCOPE | 100 focused calculator/Qt tests + 76 independent physical tests; G04 independent 5-case checks | Linear resistor/source network only; nonlinear drive compliance and amplifier saturation require separate verification. |
| X09 Dynamic Digital Output Scaling | SLVUBB4B | 1–16 ideal open-drain bits (ground/disconnected), resistor network and code map; >12-bit maps asynchronous | power_stage_design/toolbox.py; existing bus-capacitor helper reused for G04 | calculate_tool → ToolBoxWidget → export; supported stage mappings checked by identity | Power Stage Designer → 工具箱 | Separate reproducible calculator JSON; JSON/CSV/text result export | Desktop/Python target; no new Web/MCP claim | VERIFIED_IN_SCOPE | 100 focused calculator/Qt tests + 76 independent physical tests; G04 independent 5-case checks | Finite 1–16-bit ideal ground/disconnected open-drain resistor network; finite GPIO voltage/drive impedance and tolerance require a separately specified model. |
| X10 Unit Converter | SLVUBB4B | 10 dimensioned unit families plus separate amplitude/power dB conversion | power_stage_design/toolbox.py; existing bus-capacitor helper reused for G04 | calculate_tool → ToolBoxWidget → export; supported stage mappings checked by identity | Power Stage Designer → 工具箱 | Separate reproducible calculator JSON; JSON/CSV/text result export | Desktop/Python target; no new Web/MCP claim | VERIFIED_IN_SCOPE | 100 focused calculator/Qt tests + 76 independent physical tests; G04 independent 5-case checks | Explicit dimensioned linear conversions and separate amplitude/power dB conventions; no dimensionally ambiguous automatic conversion. |
| X11 Loop Calculator | SLVUBB4B | L01–L06 strict CCM and C01–C05 networks, target R/C search and rounded-component reanalysis | power_stage_design/loop.py + existing TypeII/III/FRA | analyze_loop / synthesize_compensator → desktop/project/report | Power Stage Designer → 模拟环路 | PSD JSON+HTML with current loop snapshot | Desktop/Python target; no new Web/MCP claim | VERIFIED_IN_SCOPE | 50 numerical tests; 30 real Qt plant/network save/restore/report combinations; physical nodal network checks | TI executable correlation and hardware remain unknown; finite-EA source discrepancy documented |
| X12 Filter Designer | SLVUBB4B | LC/pi filter with parasitics, damping and optional negative incremental input resistance; >1001-point sweep asynchronous | power_stage_design/toolbox.py; existing bus-capacitor helper reused for G04 | calculate_tool → ToolBoxWidget → export; supported stage mappings checked by identity | Power Stage Designer → 工具箱 | Separate reproducible calculator JSON; JSON/CSV/text result export | Desktop/Python target; no new Web/MCP claim | VERIFIED_IN_SCOPE | 100 focused calculator/Qt tests + 76 independent physical tests; G04 independent 5-case checks | Declared small-signal passive filter/negative input resistance domain; nonlinear converter stability and hardware EMI compliance are not inferred. |
| L01 VMC Buck | SLVUBB4B | Strict CCM; two C/ESR branches and DCR; VMC ramp or matching peak-CMC sense/slope/sample effects | power_stage_design/loop.py; existing controllers/FRA analysis | analyze_loop / synthesize_compensator | Power Stage Designer → 模拟环路 | Bound loop settings in project + HTML | Desktop/Python target; no new Web/MCP claim | VERIFIED_IN_SCOPE | 50 loop tests including independent nodal KCL/static derivative; all 30 real Qt plant/network combinations save/restore/report verified | TI software/hardware not run; TL431/opto bias/saturation and out-of-CCM behavior not claimed |
| L02 CMC Buck | SLVUBB4B | Strict CCM; two C/ESR branches and DCR; VMC ramp or matching peak-CMC sense/slope/sample effects | power_stage_design/loop.py; existing controllers/FRA analysis | analyze_loop / synthesize_compensator | Power Stage Designer → 模拟环路 | Bound loop settings in project + HTML | Desktop/Python target; no new Web/MCP claim | VERIFIED_IN_SCOPE | 50 loop tests including independent nodal KCL/static derivative; all 30 real Qt plant/network combinations save/restore/report verified | TI software/hardware not run; TL431/opto bias/saturation and out-of-CCM behavior not claimed |
| L03 CMC Boost | SLVUBB4B | Strict CCM; two C/ESR branches and DCR; VMC ramp or matching peak-CMC sense/slope/sample effects | power_stage_design/loop.py; existing controllers/FRA analysis | analyze_loop / synthesize_compensator | Power Stage Designer → 模拟环路 | Bound loop settings in project + HTML | Desktop/Python target; no new Web/MCP claim | VERIFIED_IN_SCOPE | 50 loop tests including independent nodal KCL/static derivative; all 30 real Qt plant/network combinations save/restore/report verified | TI software/hardware not run; TL431/opto bias/saturation and out-of-CCM behavior not claimed |
| L04 CMC Inverting Buck-Boost | SLVUBB4B | Strict CCM; two C/ESR branches and DCR; VMC ramp or matching peak-CMC sense/slope/sample effects | power_stage_design/loop.py; existing controllers/FRA analysis | analyze_loop / synthesize_compensator | Power Stage Designer → 模拟环路 | Bound loop settings in project + HTML | Desktop/Python target; no new Web/MCP claim | VERIFIED_IN_SCOPE | 50 loop tests including independent nodal KCL/static derivative; all 30 real Qt plant/network combinations save/restore/report verified | TI software/hardware not run; TL431/opto bias/saturation and out-of-CCM behavior not claimed |
| L05 CMC Forward | SLVUBB4B | Strict CCM; two C/ESR branches and DCR; VMC ramp or matching peak-CMC sense/slope/sample effects | power_stage_design/loop.py; existing controllers/FRA analysis | analyze_loop / synthesize_compensator | Power Stage Designer → 模拟环路 | Bound loop settings in project + HTML | Desktop/Python target; no new Web/MCP claim | VERIFIED_IN_SCOPE | 50 loop tests including independent nodal KCL/static derivative; all 30 real Qt plant/network combinations save/restore/report verified | TI software/hardware not run; TL431/opto bias/saturation and out-of-CCM behavior not claimed |
| L06 CMC Flyback | SLVUBB4B | Strict CCM; two C/ESR branches and DCR; VMC ramp or matching peak-CMC sense/slope/sample effects | power_stage_design/loop.py; existing controllers/FRA analysis | analyze_loop / synthesize_compensator | Power Stage Designer → 模拟环路 | Bound loop settings in project + HTML | Desktop/Python target; no new Web/MCP claim | VERIFIED_IN_SCOPE | 50 loop tests including independent nodal KCL/static derivative; all 30 real Qt plant/network combinations save/restore/report verified | TI software/hardware not run; TL431/opto bias/saturation and out-of-CCM behavior not claimed |
| C01 Type II | SLVUBB4B | Exact circuit impedances; finite voltage/OTA amplifier; optocoupler CTR/pole where applicable; small-signal bias assumed | power_stage_design/loop.py; existing controllers/FRA analysis | analyze_loop / synthesize_compensator | Power Stage Designer → 模拟环路 | Bound loop settings in project + HTML | Desktop/Python target; no new Web/MCP claim | VERIFIED_IN_SCOPE | 60 loop/integrator-setting tests including independent nodal KCL, source-tip counterexample, actual button/save/report; all 30 real Qt plant/network combinations save/restore/report verified | TI software/hardware not run; TL431/opto bias/saturation and out-of-CCM behavior not claimed |
| C02 Type II Transconductance | SLVUBB4B | Exact circuit impedances; finite voltage/OTA amplifier; optocoupler CTR/pole where applicable; small-signal bias assumed | power_stage_design/loop.py; existing controllers/FRA analysis | analyze_loop / synthesize_compensator | Power Stage Designer → 模拟环路 | Bound loop settings in project + HTML | Desktop/Python target; no new Web/MCP claim | VERIFIED_IN_SCOPE | 50 loop tests including independent nodal KCL/static derivative; all 30 real Qt plant/network combinations save/restore/report verified | TI software/hardware not run; TL431/opto bias/saturation and out-of-CCM behavior not claimed |
| C03 Type III | SLVUBB4B | Exact circuit impedances; finite voltage/OTA amplifier; optocoupler CTR/pole where applicable; small-signal bias assumed | power_stage_design/loop.py; existing controllers/FRA analysis | analyze_loop / synthesize_compensator | Power Stage Designer → 模拟环路 | Bound loop settings in project + HTML | Desktop/Python target; no new Web/MCP claim | VERIFIED_IN_SCOPE | 50 loop tests including independent nodal KCL/static derivative; all 30 real Qt plant/network combinations save/restore/report verified | TI software/hardware not run; TL431/opto bias/saturation and out-of-CCM behavior not claimed |
| C04 Isolated Type II Zener Clamp | SLVUBB4B | Exact circuit impedances; finite voltage/OTA amplifier; optocoupler CTR/pole where applicable; small-signal bias assumed | power_stage_design/loop.py; existing controllers/FRA analysis | analyze_loop / synthesize_compensator | Power Stage Designer → 模拟环路 | Bound loop settings in project + HTML | Desktop/Python target; no new Web/MCP claim | VERIFIED_IN_SCOPE | 60 loop/integrator-setting tests including independent nodal KCL, source-tip counterexample, actual button/save/report; all 30 real Qt plant/network combinations save/restore/report verified | TI software/hardware not run; TL431/opto bias/saturation and out-of-CCM behavior not claimed |
| C05 Isolated Type II Inner Loop | SLVUBB4B | Exact circuit impedances; finite voltage/OTA amplifier; optocoupler CTR/pole where applicable; small-signal bias assumed | power_stage_design/loop.py; existing controllers/FRA analysis | analyze_loop / synthesize_compensator | Power Stage Designer → 模拟环路 | Bound loop settings in project + HTML | Desktop/Python target; no new Web/MCP claim | VERIFIED_IN_SCOPE | 60 loop/integrator-setting tests including independent nodal KCL, source-tip counterexample, actual button/save/report; all 30 real Qt plant/network combinations save/restore/report verified | TI software/hardware not run; TL431/opto bias/saturation and out-of-CCM behavior not claimed |
| A01 Multi-condition worst points | Task §5.5 | Explicit Vin/load/L/C/L2 and loop C2/ESR/ESR2/DCR Cartesian factors, <=500 points; fixed controller; per-metric point identities | power_stage_design/corners.py | GUI worker → per-metric extrema → report/CLI | 多工况极值 tab | Bound request JSON + HTML | Desktop/Python | VERIFIED_IN_SCOPE | Independent same-controller per-point checks; obsolete scans and restore tests | Total loss stays UNKNOWN; only selected Q1 known I²R term ranked; unscanned region not covered |
| A02 Schematic/result linkage | Task §5.5 | All 19 direct physical diagrams plus HB/FB connected LLC primary/SR/windings/tank/output; same-result voltage/current/stress and available loss/constraint evidence | schematic.py + devices.py + gui.py; existing LLC topology/waveform components | Actual mouse selection → same StageResult + component evidence | 原理图·波形·应力 tab | Same branch evidence in HTML | Desktop/Python | VERIFIED_IN_SCOPE | 118 direct diagram tests + 19 actual all-component cases; Historical LLC click coverage was incomplete: the priority re-audit found missing Co ESR/RΣ/DC-bus consumers. These now have actual full/half-bridge click, save/recompute/export checks, source-array preservation and independent port identities | Missing switch loss/SOA/thermal and unspecified core/device data remain UNKNOWN; known Cr/Co ESR and limits are consumed. HB midpoint/PGND-variant bias and per-position/per-die current explicitly separated; PSD launcher/trace layout passes configured 720p–1080p Linux screens; LLC also passes separate real 720p/768p/900p layout and actual-consumer checks |
| A03 LLC advanced model consumers | Task §5.5 | Converged, regulated, same-spec/same-point periodic TD waveforms; semiconductors, iGSE/Litz magnetic and constant-ESR capacitor consumers | llc_design/models/waveform_loss.py + system.py + optimization/two_stage.py | Actual TD samples/fs → primary/SR/magnetic/capacitor/R_eq → system loss; bounded Stage-A/TD Stage-B → desktop/CLI candidate/Apply/project/report | LLC TD-loss checkbox; TwoStage candidate page; explicit optimize --two-stage CLI | Exact source request, TD waveform and loss calculation book | Existing LLC Desktop/Python | VERIFIED_IN_SCOPE | Actual two-candidate GUI rank/selection/Apply/save/restore/report and matching CLI independently verified; wrong identity/nonconvergence rejected; five stale legacy-report counterexamples repaired. Actual PDF now retains same-waveform immutable RMS/fs/FFT/flux summaries; 24 independent numerical checks pass, retained FHA commutation terms remain labeled | Accepted-period integration, independent audit, 687-case full LLC regression and actual GUI/report pass at eb127c2. Geometry/material/commutation remain explicit existing approximations; nominal-only and critical-point requests are distinct, rejected critical points are not accepted; ideal TD does not validate device data or hardware |
| A04 PFC line-cycle consistency | Task §5.5 | Qualified full signed switched AC period with three native periods for convergence; exact existing controller/gate policy; generic switch/diode electrical model | Existing shared-ngspice PFC + engineering/switched_cycle.py, switched_loss.py and cap_thermal.py | Same native source → PF/THD2..25/four device I²R+known edge terms/capacitor I²ESR/four-device thermal/export | Existing TTPL workspace → closed-loop shared-ngspice → Accepted switched circuit cycle in device/cap/thermal | Source config/controller/native arrays/thresholds/identity plus distinct selected-bank and device-screening inputs | Existing PFC Desktop/Python | VERIFIED_IN_SCOPE | Actual 64-cycle averaged → 800.1ms switched GUI worker/consumers/export/modal tests, 824.849s exit0; independent final-GUI raw-array equality and source/energy/KCL; three-resolution frozen-gate refinement | MODELED_KNOWN_TERMS_ONLY; generic diode/switch excludes Qrr/nonlinear Coss/vendor/hardware proof. Selected-device/bank overlays require electrical rerun after component changes. PF/THD results are not a performance pass; averaged incomplete PWM branch stays partial |
| A05 Exact H(z)/FRA chain | Task §5.5 | Existing LLC/PFC exact b/a and timing chain; FRA measured/complete-loop/identified plants; bounded exact desktop sessions | models.py.sos; fra/loop_link.py + loop_session.py; existing codegen/runtime | Actual H(z) → analysis/state simulation/session → C99 → compiled gcc validation | Existing Control Tools/FRA/LLC/PFC workspaces; FRA save/open and exact auto-candidate application | Exact input JSON, C99/coefficients and validation report | Existing Desktop/Python; PSD analog loop is not silently digitized | VERIFIED_IN_SCOPE | Independent causal state recurrence and compiled C; leading-zero delays preserved; 5 candidate kinds exact roundtrip; stale export/candidate rejection | Earlier false-success dialogs are fixed: all three exact C99 consumers release only host-gcc PASS headers; FAIL/NOT_RUN are compile-blocked and hash-bound. Actual launcher/FRA/save/restore/report and six PFC C runtime cases independently checked; no hardware/silicon/FRA-measurement accuracy claim; unsupported precision files rejected |
| A06 Reproducible project/report | Task §5.5 | Validated input/model/controller/device snapshots, current-state identity, atomic save and recomputation | project.py/report.py + existing strict JSON | GUI/CLI → same stage/loop/corner results | Save/open/report/CSV actions | JSON + HTML + CSV | Desktop/Python | VERIFIED_IN_SCOPE | Atomic malformed-load/precision/modal/late-worker checks; resumed toolbox schema-2 identity/Chinese-result suite 264 pass and report-temperature 37 pass (9 independently checked); 45aabcb input-only LLC solver/workpoint/effective-plant replay with independent hidden-RΣ counterexample and exact A05 preservation | Saved electrical provenance never restores accepted TD-loss flags. Model v1 migration only verified M1 T01/T02/T09; old extended prototypes rejected; native release qualification is tracked separately in PSD_VALIDATION_REPORT.md |

## Common requirements across the topology rows

All 19 direct stages have executable presets, connected original schematics,
component selection, same-result voltage/current statistics (including distinct
total and AC RMS), saved-input recomputation, HTML reports and waveform CSV.
Target-driven parameter suggestions preserve exact proposed values and require
explicit Apply followed by actual model reanalysis; fixed presets are not counted
as sizing. Unsupported or unreachable targets fail explicitly. T15 Half Bridge, T16 Push-Pull and T17 Full Bridge automatic sizing cover
ordinary CCM, joint-demagnetizing CCM, BCM and DCM, with actual waveform rechecks
of output-L, explicitly targeted Lm and turns-ratio targets. T15 uses a Vin/2
primary pulse; T16 uses Vin and a half-primary/half-secondary turns convention.
T15 retains equal split capacitors on a stiff total bus, a 10%-of-half-bus
individual ripple limit and nominal-midpoint first-order magnetic slopes.
These symmetric models do not add PSFB finite-Coss/ZVS physics; unsupported
commutation sub-modes and unreachable targets are rejected.

Vin/load changes, edited physical inputs, controller replacement, late workers
and modal dialogs cannot retain stale export authority. Corner reports bind the
complete immutable plant and compensator, not merely the displayed topology.
Historical saved results are not promoted to current calculation evidence.

## TI alignment gate

The official software archive requires myTI login and export approval. The
anonymous access check returned HTTP 403. No agreement or access restriction was
bypassed. T05's actual gate/DCM policy, T11's valley-selection/ceiling rules and
T18's executable load/ZVS domain therefore remain PARTIAL for exact alignment.
Public documents describe multiple possible models; they cannot select the
unobserved executable's behavior. Other physical omissions remain distinct from
this access gate and are listed in each row and the development status.

## Explicit correction from the resumed requirement audit (2026-10-19)

The prior broad A03 acceptance counted an optimizer class and its tests as if the
mandatory desktop candidate workflow existed. It did not. Its real single-point
TD loss consumer was valid; the missing two-stage desktop/CLI, Apply, saved
candidate provenance and selected report were subsequently integrated and independently
accepted through actual desktop/CLI workflows. Nominal-only TD verification must not
be labeled full-envelope TD acceptance.

The prior A05 acceptance generalized FRA's failure warning to all code-export
consumers. A real LLC external-H(z) export exceeded the unchanged float32 error
budget but showed an information/success dialog; Control Tools did likewise.
Those two dialogs and FRA's output artifact now use actual PASS / FAIL / NOT_RUN
evidence and default-blocked diagnostic headers when not PASS, with independent
red/green acceptance of the real exporters.
Mathematical coefficient identity remains independently tested; it does not make
a failed implementation suitable for hardware.

A06 also needed explicit repairs beyond the old tests: calculator input files
had no model revision, ordinary results exposed raw identifiers, and a 125°C
corner report silently recalculated nominal device loss at 100°C. The corrected
paths now bind temperature, model identity and current inputs, preserve UNKNOWN
loss terms and reject mismatches before replacing current files/state.

C01/C04/C05 now expose the exact integrator-branch action and explain why the
guide's equal-R/equal-C tip is not an exact integrator. Finite amplifier, optical
pole and C05's direct +1 path remain active; this is not a sixth compensation network.


## Priority Buck/Boost/Full-Bridge re-audit (2026-10-19)

The earlier broad A02/T21 “every-component” claim was too strong: a real clickable
output-capacitor ESR had no waveform/selection consumer, while RΣ and the physical
DC bus lacked complete terminal views. The repaired views now use same-source
Co ESR V/I, frozen RΣ·Ir, and full-bridge Ibus=Vab·Ir/Vbus. ESR and Co show the same
loss and are explicitly not additive. The ideal half-bridge midpoint-source current
is underdetermined and remains unavailable; a bridge-voltage trace is not relabeled
as a DC-source current. Original FHA/fast source arrays and request identities are
byte-identical, with only additional terminal projections. Fast EDF's existing
ripple/R output-node approximation is visible rather than presented as exact KCL.

Advanced-model parameter recommendations previously compared serialized dictionaries
to dataclass options, causing a real T05 preview KeyError and invalid save records.
The comparison now preserves exact immutable option types through preview, Apply,
save/recompute and report. Invalid advanced inputs are caught at the clicked action.
Model help follows the selected option instead of retaining ideal-model exclusions.

The T17 inverse now solves the already implemented joint magnetic-state light-load
branches, rather than rejecting all but ordinary CCM. Independent event/IVP,
charge/flux/energy, exact boundary and actual GUI replay checks cover the extension.
Three floating endpoint defects were repaired without altering requested component
values or dropping representable waveform intervals. Unsupported commutation modes
remain rejected. T01/T02/T03 each passed actual CCM/BCM/DCM branch/loop/session/report
flows in this re-audit. T05/T17/T18 still have no generic analog-loop plant; this is
explicit and is not counted as an implementation of a non-baseline plant.

The main launcher now presents a first-level “功率级设计 / Power Stage Designer”
card naming Buck, Boost, Buck-Boost and full bridge, above all six retained entries.
Real configured 1280×720, 1366×768, 1440×900 and 1920×1080 screens are checked.
PSD traces previously collapsed to zero height at 720p; they now have a minimum
usable plotting area, scroll access and a reversible schematic-collapse action.
This is Linux/offscreen evidence, not unrun native-platform qualification.

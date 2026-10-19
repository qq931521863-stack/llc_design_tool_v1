# Power Stage Designer validation report

## Latest completion qualification correction (2026-10-19)

Integrated-LLC leakage resonance is locally implemented in `c06f1d5`. Independent
review corrected two unsafe substitutions: balanced-MMF leakage energy is not
APR Lshort, and APR magnetizing flux is not generally physical-core flux. The
implemented matrix/APR conversion, physical current mapping, insulation-region
field integral and single-transformer accounting are exercised by actual consumers.
Independent two-port checks cover 900 complex-load/frequency cases (maximum
relative error 6.83e-13). Actual regulated TD converged with residual 1.9241e-10;
physical Bpk 0.0967437178 T and total loss 75.4389136 W independently reconcile
with port currents and the component ledger. These are model/software tests,
not measured hardware qualification. Final merged-source replay is still pending.

Author groups of 142, 29, 7 and 55 focused cases passed; these overlap and must
not be summed. Independent real Qt flow covers 34 checks including exact input
roundtrip, invalid-import atomicity, late-result rejection, mode changes, EDF
waveform/export and formula PDF. A final EDF check reconciles all 2048 exported
Vμ samples and preserves source metadata/warnings. Actual 720p/768p controls and
stale-transformer-status counterexamples were repaired. The first default-HB
observation used a repeated QTest.qWait harness that starved the Python worker;
its 120 s observation limit is not a product timeout or performance failure.
Independent replay using the actual Qt event loop completes default 7-harmonic /
1024-sample HB twice in 2.750/2.849 s (residual 2.438e-15, 9 iterations), with
responsive UI and zero remaining workers/threads. The old source's running-close
hides the window and discards the result, then naturally completes; it did not
cooperatively cancel. A separate narrow repair adds a real Cancel action and a
visible 0.1–3600 s session-only total budget (default 300 s). An independent
36-check replay confirms unchanged default HB output, Cancel, global-input change,
close, actual in-TD cancellation, timeout and retry; a separate seven-check replay
confirms disabled page-input protection and programmatic input invalidation.
Cancellation is observed from a numerical checkpoint with no result payload and
zero workers/native threads, not inferred from hiding the window. The author's
current 10-case gate additionally exports the new 390 V retry and compares every
CSV sample/current metadata. An attempted TD-loss export from a nonconverged
fixture was correctly refused and is retained as an earlier test-fixture error;
convergence was not weakened to make that test pass. Final-source aggregate and
frozen qualification still remain separate gates.

FHA/fast EDF display voltages are hybrid fundamental/square-clamp projections:
one independent example has a 46.47% RMS difference from Lμ*dIμ/dt. Labels,
warnings and metadata disclose this. FHA magnetic loss uses a distinct triangular
reconstruction; accepted TD uses actual physical samples. Actual TD labels retain
the physical induced-voltage meaning. Matrix/core identity, geometry reachability,
full operating envelope and hardware qualification are separate decisions.

The immutable `0dfd790569b8f28935020bc1f3d3f78a5f57e7f0` run collected 3763
cases. It was operator-interrupted (tool session exit 130) after a reproducible
ownership defect, with **965 PASS and 1 FAIL**, no complete JUnit and no aggregate
success claim. Source hashes stayed unchanged and the worktree stayed clean.
The last monitor sample was 1134.249 s; peak process-group RSS was 2,529,196 KiB,
parent peak 1,950,752 KiB, minimum MemAvailable 2,996,472 KiB. These are stopped-run
observations, not proof of a leak-free or successful complete regression.

The failure is `test_new_domain_real_parameter_proposal_apply_and_restored_targets`
for ideal T18 DCM. Replaying it independently took 2.43 s and showed a genuine
GUI intent bug: an untouched 0.1 A optional magnetizing target changed Lm from
1 mH to 4.0180657 mH. Setting the optional target to zero recovered Lo
100.000000261 µH and Np/Ns 3.999999993. The narrow GUI correction preserves the
original numerical assertion; 45 focused intent/consumer tests and 36 additional
ideal-physics/workflow checks pass. Five finite-Coss cases were explicitly excluded
from the latter small check, not credited as passed.

Independent native-object observations found exactly 91 parentless menu/popup
widgets and 21 WidgetGroups per PSD window, despite zero remaining window/scene
roots. Releasing Python references and GC could remove them, but retained invalid
wrappers must not keep native auxiliaries alive. The explicit auxiliary repair
covers actual application quit, three reuse cycles, shared/external owners, direct
and already-closed plot roots, lazy exporter file dialogs and logical Parameter
trees. Its final focused gate is 15 PASS in 70.43 s. Additional actual standalone
entry/linked-LLC/startup-error, impedance dialog and clickable diagram-owner checks
are 5 PASS in 11.72 s. Independent original probes replay green for those paths.

The wider audit also demonstrated completed QRunnable/signal retention in actual
LLC/DAB/PFC-subview/PSD/toolbox producer paths. The common repair now passes
85 cases in 34.18 s across 14 real startup paths, including normal/error, queued
late callback, close/reopen, final owner disposal and unrelated-global-work
negative controls. An independent 76-class/26-modal-callsite audit replayed all
nine producer families: zero worker wrappers, native workers and signals after
each of three jobs and both disposal stages. Both real Toolbox cancel boundaries
pass, including native completion before queued GUI delivery. All ten final
producer hashes were independently checked after formatting-only changes. These
are focused ownership qualifications, not a completed aggregate suite. Future full
runs persist each complete failure traceback as soon as pytest reports it, and
surface a live failure count; an intentional synthetic failure verified this
monitoring path. No tail-only "no failures" claim substitutes for complete logs.

Four newly supplied magnetic-core PDFs add a separate source-data and actual-
consumer acceptance item. Their implemented, independently checked declared scope
is described in the source magnetic catalog section below. Actual new-source
aggregate and installed/frozen qualification remain pending; missing model
conventions and conflicts are never filled from legacy defaults.

## Current re-audit and terminated aggregate run (2026-10-19)

Source `3d2f9fbd020fd0b9513f6f9e5950de5c31dfb38e`: 3502 collected;
2508 PASS + 1 SKIP recorded before SIGKILL (-9), 1741.417 s. No completed
JUnit or normal interpreter exit exists. Peak group RSS 4,983,292 KiB, parent
4,620,500 KiB, lowest sampled system MemAvailable 1,180,168 KiB. The last
completed case was the i18n translated-start test; termination occurred while
entering the lifecycle subprocess test. This run is retained as a red resource
gate, not counted as full success, and OOM causation remains unconfirmed.

Native ownership diagnostics distinguish a real retention path from mere cache:
three LLC create/close/drop-reference/GC cycles retain three windows, three scenes
and 24 canvases; three PFC cycles retain three windows and 111 canvases. Explicit
owned disposal clears all corresponding weakrefs/native wrappers; allocator trim
alone does not. In actual production controller probes, six workspace caches are
bounded and reused, but three cancelled Home selectors leave three dialogs and
Qt event-loop return leaves all six owners alive. Repairs require both test and
production ownership acceptance, including failure/close/reopen and unrelated
window controls.

Full-task re-audit correction: previous completion language missed reachable
T15/T16 inverse modes, T18 Lm targeting, T15 stiff-bus split-cap reconstruction,
fast tool/loop updates, page-local source provenance, invalid-temperature handling,
inactive branch inputs, canonical loop restore and saved synthesis intent.
These are actual implementable requirements, not TI-access limitations.

Current narrow evidence (new development source, aggregate qualification pending):
- Independent bridge observer: 64 inverse recoveries, 8 node/RMS/charge/energy
  checks; fixed-bus sum error zero, node KCL residual <=2.09e-17 A, RMS discrepancy
  <=6.51e-18 A. The observer independently propagates diode states and does not
  consume the production inverse or author's test oracle. Original wrong bus
  sum and 1 pF acceptance were reproduced before checking the repair.
- Bridge owner tests: 202 targeted numeric cases passed with original T17
  compatibility preserved. This count is separate from the independent observer.
- Loop/A06: 15 new real Qt tests cover sparse default normalization, actual failed
  open callback retaining the previous design, exact accepted alias identity,
  debounce/current arrays, invalid-to-valid recovery, exact request persistence,
  preferred series, HTML/CLI and legacy absent-request reset. A first collection
  error using pytest's reserved parameter name was retained then corrected.
- Toolbox: focused real Qt and legacy input/result-identity checks are passing;
  heavy restore consumer and final independent review remain in progress.

Final pre-freeze consumer follow-up:
- 369 toolbox/loop/desktop cases passed (132.290 s); 187 modal/identity/bridge
  consumers passed (94.994 s). One earlier 197-case run had 196 passes plus an
  obsolete manual-only loop expectation. Its red log is preserved; the replacement
  checks actual current 22 kΩ response and that the cancelled synthesis did not
  apply its components.
- Independent consumer audit found inactive loop-field type/enum restore, advanced
  alias options, sparse suggestion defaults and async-open/debounce races. All
  original counterexamples were replayed after repair; seven final failure/edit
  branches preserve the correct current model. The expanded focused module is
  33 passed, 19.417 s, including actual callback failure atomicity.
- Independent toolbox audit found an additional actual malformed-library partial
  mutation/stale-export path. Full-update prevalidation and contained device-load
  failure close it. Five original/category/real-button probes pass; malformed
  hidden/page-state/source mappings fail closed.
- Explicit finite-Coss PSFB Lout/Lm/ratio inverse acceptance ran in 456.36 s,
  18 actual model evaluations. A separately written Radau state/event observer
  rechecked current extrema, periodicity and output charge. This optional test
  passed, rather than being counted from its default skip. Its executed observer
  source and kernel hashes are retained; later import/cache/explicit closure-binding
  lint edits did not change the physical equations or production kernels.
- Native lifecycle suite: 24 passed, 72.27 s; repeated controller-drop probe has
  zero surviving controller weakrefs and zero native top-level roots, with wrapper
  count stable at 676 across three cycles. An accepted-start test harness initially
  scheduled quit before outer app.exec; its timeout is retained and corrected by
  waiting for the real outer-loop entry, with no production workaround.

No result here upgrades TI executable correlation, native Windows/macOS, hardware
qualification or the whole newly edited source to a completed pass.

## Evidence levels and source provenance

Software correctness, model validity and hardware correlation are separate.
The [feature matrix](PSD_FEATURE_MATRIX.md) defines the domain of each claim.
Independent reference values are model checks, not readings from TI software.
Unknown physical inputs are not zeros. No MATLAB, TI executable or hardware
correlation result is fabricated.

The historical final development source was
`bfc8bd9ce4c0eab7ce6d480cace5627d767a0eeb`; documentation/evidence subsequently
advanced to `74fada3cf799610a8bf4d2b15d88204e51432707` without executing-code
changes. After environment recovery the cumulative patch was reapplied to
`e02b0de9c73b46b0990ce337d526e174b0980f4f`. All **672 non-evidence source
hashes matched** before release metadata/documentation and native-proof changes.
These are historical local source identifiers, not claims that those Git objects
were pushed. The new public integration commit and CI provide release provenance.

- Cumulative patch SHA-256: `f109eef6abe6345600d8036ad5f15bb201be2616b0bb889bba60e69bf36c5278`.
- Historical installed wheel SHA-256: `490aa2bf756943895bf09352e117f57efb52d553597a24f70f940214ad5717ed`.
- Historical core delivery SHA-256: `61c1c22cca0294e21070adc1480457ade9d0a9f82bcf6072028bace65ffaca5b`.

## Historical final regression: 2026-10-18

The exact development source completed the ten configured paths in **one native
process: 2864 passed, 1 skipped, 2 xfailed, 130 warnings, 1070.02 s, exit 0**.
JUnit had 2867 cases and zero failures/errors. All 672 source hashes were unchanged;
Qt/session teardown and interpreter exit completed. This is not a sum of reruns.

| Test path | Collected cases |
| --- | ---: |
| power_stage_design | 1272 |
| dab_design | 58 |
| pfc_design | 372 |
| llc_design | 729 |
| power_codegen | 3 |
| power_control_tools | 336 |
| power_sim | 58 |
| power_agent | 4 |
| webapp | 18 |
| backend | 17 |

The sole skip was the opt-in long PFC GUI test, separately run on identical
production code: **one pass, 824.849 s, exit 0**, real averaged preflight and
shared-ngspice worker, actual consumers/export and stale-modal rejection.
The two existing xfails are the explicitly insufficient phase boost of a single
2P2Z for its chosen EDF target. They are not hidden new failures.
Batch ngspice 44.2 and the shared library actually ran in that environment.
Peak sampled RSS was about 4.75 GiB; minimum sampled available memory was about
1.61 GiB. These are measured run observations, not platform memory guarantees.

The exact wheel additionally passed out-of-tree resource/import checks and a
real Qt Buck → loop → save → edit → open/recompute → HTML workflow using three
actual file choosers. All 101 toolkit imports resolved inside the installed target;
owner, plots, scenes and ViewBoxes were destroyed and the native event loop exited 0.
This Linux Python-wheel evidence does not qualify a frozen Windows/macOS binary.

## Reproducible independent checks in the source tree

Run the entire configured suite with `QT_QPA_PLATFORM=offscreen MPLBACKEND=Agg
python -m pytest` after installing `.[gui,dev,web,agent,research]`. On Windows,
set those environment variables using the shell's syntax. Optional simulator
checks report their actual availability; do not describe skipped tests as passes.
The release workflow also runs the full suite on both supported native platforms.

| Claim | Independent mechanism and maintained test entry |
| --- | --- |
| Buck/Boost/Flyback G01–G03 and boundaries | Periodic roots, charge/volt-second balance and numerical quadrature; [independent validation](../power_stage_design/tests/test_independent_validation.py) |
| Extended topologies | Multi-state periodic roots, flux/charge matrix exponentials, nodal KCL/KVL and branch mapping; [extended validation](../power_stage_design/tests/test_extended_independent.py) |
| QR/PSFB extensions | Separate LC ODE/event observers and diode-secondary idle/energy equations; [QR/PSFB](../power_stage_design/tests/test_qr_psfb_extensions.py), [finite QR capacitor](../power_stage_design/tests/test_qr_clamped_capacitor.py) |
| Total versus AC RMS | Centered-energy quadrature over every direct topology; [AC RMS tests](../power_stage_design/tests/test_ac_rms_statistics.py) |
| Sizing and actual Apply | Independent circuit roots, unreachable/domain rejection and real Qt restore/export; [sizing observer](../power_stage_design/tests/test_suggestions_independent.py), [desktop](../power_stage_design/tests/test_parameter_suggestions_desktop.py) |
| Toolbox/G04 | Energy integration, complex admittance, resistor/GPIO nodal solves and endpoints; [toolbox observer](../power_stage_design/tests/test_toolbox_independent.py) |
| Six plants/five networks | Circuit-node KCL, finite amplifier and peak-condition differentiation; [loop tests](../power_stage_design/tests/test_loop.py); all 30 actual GUI combinations |
| Corners and per-metric worst points | Fixed controller and complete point/plant identity; [corner tolerance](../power_stage_design/tests/test_corner_tolerances.py), [identity](../power_stage_design/tests/test_corner_identity.py) |
| LLC waveform consumers | Independent gated-branch I², event counts, flux/iGSE/Litz and I²ESR; [consumer observer](../llc_design/tests/test_psd_consumer_independent.py), [passives](../llc_design/tests/test_td_passive_consumers.py) |
| LLC component linkage | Rendered-wire connectivity, both Cr-return conventions, actual clicks and exact effective-plant replay; [component workflows](../llc_design/tests/test_llc_component_workflows.py) |
| PFC shared switched source | Native-record cycle/energy/KCL and same-source losses/thermal/export; [cycle](../pfc_design/tests/test_psd_switched_cycle.py), [actual GUI](../pfc_design/tests/test_psd_switched_gui.py) |
| H(z)/FRA/code generation | Independent causal recurrence and actual compiled C; leading-zero delay and exact b/a survive Apply/save/restore; [digital chain](../power_stage_design/tests/test_digital_chain_independent.py) |
| Real Qt lifecycle and stale operations | Retained-wrapper subprocesses, repeated owner destruction and late/modal workers; [lifecycle](../power_stage_design/tests/test_lifecycle_subprocess.py), [desktop fixture](../llc_design/tests/test_desktop_fixture_lifetime.py) |
| Switched circuit repairs | Live ngspice dead-time diode paths and integrated KCL/energy; [LLC circuit](../power_sim/tests/test_llc_spice_deadtime.py) |

Closed-form same-model tests normally use rtol 1e-8 with dimensioned absolute
limits. Waveform quadrature checks resolution convergence and ≤0.1% away from
zero. Dedicated switched edges retain their stated weaker time-resolution bounds;
a good integrated energy residual is not proof of nonlinear Coss/Qrr dynamics.

## Source discrepancies are explicit

- SLVUBB4B eq77 as printed fails the ideal-amplifier limit. The finite-EA
  implementation follows circuit-node KCL, not a force-fit to that expression.
  Exact Type II/III impedances retain terms omitted by approximate pole/zero placement.
- Boost peak-CMC fixed-slope normalization uses **Vslope/Vout**, supported by
  independent peak-condition differentiation and TI SNVA542 D11–D14. SLVUBB4B
  eq43 instead prints /Vin. Actual TI executable behavior remains unobserved.
- X04's example gives C=416.294085 µF from its printed equation, while the
  printed RMS expression gives 2.622526 A and the screenshot says 2.49 A.
  Independent periodic ideal-diode integration gives about 2.281251 A under
  its separately stated assumptions. The tool preserves the named
  `bulk_rms_current_eq13_a` approximation and warns against thermal interpretation.
- A sensitive PIF float32 compiled-C case exceeds the existing error budget
  even though exact float64 coefficients are preserved. Validation remains FAIL;
  neither coefficients nor tolerance were changed to manufacture success.

## Real switched-source observations and limitations

The accepted final TTPL GUI record retains 237,682 native points across three
periods, ending exactly at 0.8001 s. All consumers carry source
`6984f40d1848d86bccdcc82064c3b0a3901b846b570aa8d29ae49beee13fef10`.
Current/bus cycle differences were 0.85367%/0.13189%; whole-cycle energy residual
−0.023623 W and peak KCL residual 2.12e-10 A. Independent capacitor RMS was
8.752537235746534 A. Electrical ESR 0.035 Ω gives 2.681242 W; a separately
selected 0.024 Ω bank on frozen current gives 1.838566 W and the configured
empirical hotspot 46.6547 °C. A changed electrical bank requires a circuit rerun.

PF≈0.936962 and THD over harmonics 2–25≈18.93157% are retained as measured
model results, **not a power-quality pass**. Generic switches/diodes and selected
edge-energy terms exclude vendor Qrr/nonlinear Coss and hardware thermal accuracy.
PWM mean/variance decomposition is not frequency-dependent ESR analysis.

LLC accepted-period semiconductor, magnetic, capacitor and damping consumers
use the same source. Material/geometry and commutation approximations keep
separate provenance. Midpoint-return HB Cr has no silently added Vbus/2 offset;
the separate PGND-return variant does. Same displayed operating point with a
different effective RΣ is a different replay identity. Nonconverged detailed-HB
points reject export, and restoring a waveform request does not restore TD-loss authority.

## Failure history and corrective evidence

Earlier red/incomplete runs are not relabeled as passes:

- Six old PFC tests expected stale export after input edits. Revised tests verify
  invalidation, unchanged historical files, and actual current recomputation/export.
- A native exit139 exposed a retained-owner problem: `PlotWidget.close()` detached
  children before owner destruction. Narrow fixture repair preserves the ownership
  tree; strong-wrapper subprocesses verify deletion rather than just no crash.
- A later run ended in SIGKILL (−9 / wrapper137) near 71%, without complete JUnit.
  SIGKILL alone does not prove OOM. Independent i18n cycles established retained
  roots/scenes under session-only cleanup; per-test owner cleanup removes them.
- Replacing a pending Qt singleShot bound callback caused a separate native
  shutdown139. The fixture now mocks only the network boundary, preserving the
  real callback. Strong-owner, live/dead timer and busy-worker-close probes exit0.
- Original LLC dead-time switching produced unphysical bridge excursions despite
  weak smoke tests. Explicit body-diode paths and named numerical shunts were
  repaired and independently checked with live circuit/energy tests.
- Absolute-current TTPL feedback caused positive feedback during reverse current.
  Target gate polarity × signed inductor current repairs that counterexample;
  native I²/VI is integrated before display interpolation. Ngspice44.2 decimal
  endpoint normalization has separate one-ULP and backward-time checks.

No required cleanup, test or resource gate was removed to obtain the final
historical pass. Its successful rerun does not prove a unique cause for every
earlier intermittent crash.

## 9.9.0 native release acceptance

**Pending at candidate preparation.** Required: full Ubuntu test gate, full
Windows x64 and macOS arm64 regression, actual PyInstaller executable runs
outside the checkout, six launcher workspaces, Guided System Design, real PSD
calculation/session/report/loop checks, correct data checksum and native exit0.
Publication requires both platform proof JSON files and archive SHA-256 checks.
The [release process](RELEASE.md) and Actions records are the authority for the
new release; historical Linux evidence alone cannot satisfy these native gates.

## External and physical gates

The official TI download requires myTI login/export approval; anonymous access
returned403. No restriction was bypassed. T05/T11/T18 exact executable policy
comparisons remain PARTIAL. [Development status](PSD_DEVELOPMENT_STATUS.md)
lists the precise next information and separate physical-model extensions.
Hardware, real FRA/thermal/EMI correlation and whole-domain convergence remain
UNKNOWN. Native startup does not certify a minimum physical display size.

## Official source register

- [TI product/version](https://www.ti.com/tool/POWERSTAGE-DESIGNER)
- [SLVUBB4B, February2023 guide](https://www.ti.com/lit/ug/slvubb4b/slvubb4b.pdf), topology/calculator/loop baseline
- [SNVA542](https://www.ti.com/lit/an/snva542/snva542.pdf), AppendixD peak-current Boost slope
- [SLYU036A handbook](https://www.ti.com/lit/eb/slyu036a/slyu036a.pdf), supplemental topology physics and §18.1 PSFB domain
- [SNVA790A](https://www.ti.com/lit/an/snva790a/snva790a.pdf), Fly-Buck
- [SLVA750A](https://www.ti.com/lit/an/slva750a/slva750a.pdf), series-capacitor Buck
- [SLYT584](https://www.ti.com/lit/an/slyt584/slyt584.pdf), distinct two-switch CCM gate strategies
- [UCC28600 SLUS646K](https://www.ti.com/lit/ds/symlink/ucc28600.pdf), controller-specific QR behavior, not generic PSD policy

These documents establish public model evidence, not unobserved software output.

## Resumed-environment compatibility and local preflight: 2026-10-19

The refreshed Python3.12/NumPy2.5.4 environment exposed four real LLC/H(z)
save/replay failures: NumPy's NDArray annotation now has a named type-alias origin,
so the strict encoder rejected a genuine float64 ndarray. Recognizing both the
legacy ndarray origin and the named NDArray origin repairs this boundary without
accepting lists, tuples, array subclasses or weakening dtype/shape/finite limits.
The full session-validation set plus all four failures reran **79 passed, exit0**.
Exact coefficient bytes, including signed zero, have explicit counterexamples.

Windows-default cp1252 emulation reproduced a Chinese-report decode failure.
Explicit UTF-8 test I/O repairs then passed **124 tests / one explicit long-test
skip**; another affected group passed329. Counts are separate focused runs, not
added to the historical full-suite number. The long PFC fixture also preserves
the real Qt callback and stores evidence in its portable test directory.

Release preflight passed77 focused tests; the real Linux PyInstaller executable
passed six-workspace/PSD calculation, saved-project/report and 1600-point BuckVMC
proof checks with frozen=true and exit0. This is Linux/Python3.12 only. No new
Windows/macOS run or remote publication occurred; publication is now paused while
remaining implementation work continues. A new full source freeze/regression is
required after those changes.

## Resumed baseline-consumer audit and corrections (2026-10-19)

An independent production-caller audit found three gaps hidden by the earlier
broad acceptance labels: missing two-stage LLC desktop/CLI consumption, incomplete
C99 failure presentation/artifact safety in LLC and Control Tools, and calculator
input-session model identity. These are tracked as actual implementation work,
not attributed to the unavailable TI executable.

The report-temperature counterexample used one Buck point and a selected MOSFET:
at 125°C the corner conduction loss was 0.2506405527 W while the nominal report
silently used 100°C and 0.2303183457 W. Nine new tests failed before the fix.
The repaired nominal/all-component/snapshot path uses the corner temperature or
an explicit matching temperature; conflicting temperatures reject before writing.
37 affected tests pass. An independent reviewer reran the nine tests and live
−40/75/125/175°C probes; all passed. Missing total losses remain UNKNOWN.

The exact integrator-branch action has 10 new tests plus 50 existing loop tests
passing. Independent two-unknown feedback-node KCL is solved across frequency for
C01/C04/C05, including finite amplifier and the C05 direct path. A source-tip
counterexample shows the equal-R/equal-C setting's gain changing from 1× to 2×
relative to a low-frequency-matched integrator. Real Qt buttons, invalidation,
save/reopen/recompute and HTML source warnings are exercised.

Toolbox normal results now use Chinese labels/units/unknown terms with optional
raw details. Schema-2 input files use atomic writes and explicit model/toolkit
identity. Duplicate keys, nonfinite values, incompatible revisions, versionless
legacy files, oversized content and provenance mismatch reject without mutating
current state. The combined existing/new toolbox/modal/lifecycle run passed
264 tests in 74.09 s. Real Linux offscreen screenshots were inspected; measured
warm-widget p95 over 30 runs was X01 4.68 ms, X02 0.82 ms, X04 1.15 ms,
X10 0.94 ms and X12 34.64 ms. These focused runs do not replace a new full-suite gate.

New T05 finite-Coss/Ron and QR valley/charge extensions have production dispatch
and explicit option identity; their desktop/schematic integration and separate
physical review are still in progress. No full integration acceptance is inferred
from the author's 32 T05 / 37 QR kernel tests alone. T18 commutation remains in
implementation. Publication, tag creation and remote writes remain paused.

## A05 actual-consumer closure (2026-10-19)

The earlier A05 broad acceptance was incorrect at real desktop export boundaries:
LLC and Control Tools could show success for a float32 mismatch, and FRA left an
unblocked failed header. All three now use one guarded export path. The initial
artifact is compile-blocked NOT_RUN before fallible work; only actual host-gcc
PASS releases the unchanged generated header. FAIL/NOT_RUN remain diagnostic
`#error` artifacts with atomic SHA-256-bound b/a/Fs/status sidecars. Raw rendering
APIs remain explicitly unverified building blocks, not desktop acceptance APIs.
PFC exact exports additionally enforce busy/revision/analysis/handoff ownership
before and after native dialogs; its semantic ISR template labels configuration,
float32 and hardware verification separately rather than inventing a board gate.

Independent actual-launcher probes exercised PSD T20/T21 → exact Control Tools
H(z) → LLC real worker → project save/load → restored waveform worker → digital
reanalyze → real Python research-script execution. Coefficients, sample time and
source/operating metadata remained exact. FRA measured-plant, complete-loop and
identified-plant paths each passed actual save/open and compiled C execution.
Independent PFC PI/PIF/2P2Z × voltage/current runtime executions include saturation,
anti-windup, reset and asymmetric coefficients. Maximum error versus separate
recurrence equations was 1.272e-5; versus Python runtime 7.656e-6. This is host
float32 semantic evidence, not an MCU/BSP timing or hardware claim.

The real LLC research consumer exposed a canonical string/Enum boundary that the
old constructor tests missed. The actual PFC 2P2Z export exposed an aliased
integrator pole where two polynomial orientations disagreed despite identical
coefficients. Repairs preserve all coefficients and all source samples. Only the
cross-library numerical self-check excludes relative denominator conditions
≤1e-8, explicitly records the affected indices, and reports NOT_RUN when no
well-conditioned sample exists. The original comparison tolerance is unchanged;
a changed well-conditioned coefficient still fails. Python scripts were executed;
MATLAB output was inspected but MATLAB execution is not claimed.

Focused evidence: 15 new guarded-export tests, 16 PFC ownership tests, 462 existing
compatibility tests, 63 combined consumer tests, and 25 research-export tests
passed in separate runs. Independent positive probes reran after both research
repairs. These counts overlap and are not a fresh full-suite total.

## Explicit switched-physics extensions and independent acceptance (2026-10-19)

The optional T05/T11/T18 models now have actual desktop inputs, circuit-connected
branch selection, waveform/statistic consumers, strict input/source/revision
identity, project restore/recompute, HTML export and cancellation ownership.
The default ideal models remain unchanged. All 14 advanced desktop tests passed
in 216.64 s, including every new branch's real schematic click and exact plotted
I/V arrays, full-precision option restore, malformed data, pending edits, close
and late-result rejection. The shared schematic/Type-I/input-form suite passed
141 tests. New example JSON files are actual saved/recomputed inputs, not cached
accepted results. A seven-state PSFB voltage probe is drawn as a high-impedance
probe and is explicitly excluded from additional power-device interpretation.

T05 independent validation stamps the original switched circuit into ngspice,
without reusing the production solver equations. Five cases include negative
inductor current, floating nodes and body clamps. Maximum node/current discrepancy
was 20.2 µV / 41.6 nA; all 11 branch-current RMS values agree within 0.563 µA
(1.68e-6 scaled relative). Raw external source/branch/storage energy residual is
≤8.85e-8 relative. Constant Coss/Ron and piecewise-resistive diodes are explicit;
there is no nonlinear vendor-model or reverse-recovery claim.

QR review found two real defects after the author's initial tests: a nonmonotonic
finite-Ron power curve caused false load rejection, and an invalid universal
Vin/Ron peak cap rejected reachable underdamped trajectories. Both are fixed,
not reclassified as impossible. The controller explicitly selects lower/higher
peak-current roots on the first rising-current trajectory, bounded by the real
current crest or declared asymptotic numerical limit. Frequency ceilings never
silently switch roots. Independent review accepts 28 finite-Ron load points,
three underdamped counterexamples, seven full waveform/charge/energy cases and
four nonlinear-C/delay maximum-power sweeps (≤7.28e-9 relative discrepancy).
Exact-matrix roots, tangent maxima, excess-load rejection and the finite-crest
endpoint are checked. A steep user charge table can still produce a quadrature
warning; accepted evidence retains that numerical caveat rather than claiming
uniform accuracy for arbitrary data. The author suite passed 49 QR tests and
102 related regression tests separately.

T18 independent original-network ngspice checks distinguish 5 A partial ZVS from
15 A all-four-switch ZVS at 400→48 V / 100 kHz with explicit components. At 5 A,
Q1/Q2 turn on near 146.51 V while Q3/Q4 are body-clamped near −0.889 V; source/load/
branch/storage energy residual is −4.57 µW at 245.9 W. At 15 A, finer external
steps reduce state discrepancy about 20× to 1.58 mV / 4.66 µA; all 19 current RMS
values agree within 2.10e-6 scaled relative and energy residual is −0.086 µW at
738.9 W. These finite numerical errors remain disclosed. The author suite passed
38 new tests and 136 related regressions. Constant-Coss positive CCM and fixed-gate
orbital stability do not prove nonlinear-device, closed-loop or hardware stability.

These declared models remove implementable functional gaps but do not establish
TI executable equivalence. T05/T11/T18 therefore remain PARTIAL in the alignment
register. Authorized TI software capture is still needed to identify its exact
gating/valley/load policies. No login/export gate was bypassed. Full-source and
native-package qualification must be rerun after the remaining consumer repairs.

## TwoStage real consumer checkpoint (2026-10-19)

The previously missing production caller is now a real desktop and opt-in CLI
workflow. Stage-A screening and bounded Stage-B verification preserve each
candidate's own specification, actual TD source, loss scope and ranking metrics.
Unknown/rejected candidates cannot Apply. Nominal-only versus critical-point
verification is explicit; unverified operating points retain labeled FHA terms.
Project restore retains exact requests and historical results without restoring
Apply/report authority. The default CLI without `--two-stage` remains Stage A.

An independent actual GUI run with two SR-parallel candidates obtained
74.9082153351 W / 72.4466596580 W, correctly ranked them, then deliberately selected
rank 2. Apply changed the actual project to precisely that candidate; saved input,
selected ZIP and legacy Markdown report preserved its TD identity. The actual CLI
returned the same candidate ID and loss for the matching specification. A separate
two-point scan correctly rejected volt-second and FHA/TD consistency failures.
Real running-TD cancel and close both ended with zero active workers, no accepted
late result and disabled Apply/report while the Qt event loop remained responsive.

Independent legacy report races exposed five stale/modal/closed publication
counterexamples. Repairs capture the clicked source before dialogs, stage worker
bytes privately and publish only if ownership still matches; all five original
counterexamples and current-state positives now pass. An additional actual CLI
reused-output case exposed stale accepted report files beside new rejection JSON.
TwoStage now requires a new directory before any computation or publication;
17 existing output files remained byte-identical in the independent recheck.
Fresh rejected requests have REJECTED status, selected ID null, no accepted report
and nonzero exit. Incomplete runs retain explicit RUNNING/FAILED status. Existing
user files are never moved or deleted by this policy.

The author consumer suite passed 48 tests; the CLI reuse/new-directory suite plus
existing CLI checks passed 12 tests separately. A03 remains PARTIAL at this
checkpoint because the independent audit found a distinct actual formula-PDF
source error: FHA substitutions gave 10.9737134195 W primary conduction while the
printed TD result was 12.1326694538 W. SR/Cr and frequency substitutions likewise
mixed sources. This is being repaired with same-waveform formula-input summaries;
a successful PDF file alone is not acceptance of its engineering contents.

### Post-integration report identity correction

The strengthened production packaging gate found that HTML used the global PSD
framework revision even when the returned PSFB result had its own kernel revision.
The report now separates `model_revision` (actual result) from
`framework_model_revision` (project/framework contract), both in visible metadata
and the reproducible snapshot. Eleven focused identity/temperature tests pass;
the real advanced GUI and production frozen gates also compare these fields.
QR ideal-Ron channel/Cnode statistics now carry a per-branch warning that ideal
charge impulses are excluded, rather than leaving that limitation only in the
model-wide warning list. A selected-device I²R overlay is explicitly fixed-waveform
screening and does not imply an electrical rerun after changing the device.

## A03 formula-consumer closure (2026-10-19)

The mixed-source PDF defect is now repaired through a small immutable formula-input
summary computed from the same accepted final-period waveform. It retains actual
sample count/step/frequency, RMS/peak/mean values, signed RMS FFT phasors, iGSE
flux-derivative moments, coefficients and final modeled temperatures. This is an
input audit summary, not a second loss model or cached project authority. Existing
historical analyses without the summary explicitly say NOT_AVAILABLE rather than
reconstructing inputs from a result or substituting FHA values.

Actual TD fs=99632.2080779 Hz is separated from the retained FHA reference
99657.6636603 Hz. Primary 12.132669 W, SR 11.497172 W and Cr 0.396492 W now use
matching numerical substitutions. Independent review recomputed 24 displayed
expressions and compared RMS/FFT/flux inputs to raw exported TD CSV; all agreed
within printed rounding, with FFT discrepancy ≤1.8e-15. The actual PDF text and
rendered pages were checked, including per-point mixed TD/FHA provenance and
remaining FHA commutation/ZVS assumptions. The 90-test formula/passive/TwoStage
regression passed in 47.40 s; a final layout-only adjustment then passed 22 formula
tests in 4.80 s. These are focused counts, not the pending full regression total.

A03 bounded software consumption is independently accepted. Nominal-only evidence
does not establish successful convergence/validity at all critical points; actual
critical-point rejection remains a valid, explicitly retained result. No failed
or unknown point is removed to make a candidate pass. Vendor/material/thermal,
commutation approximations and physical operating limits remain visible.

## Final qualification source freeze

Artifact qualification now requires production proof schema 3, including all
47 optional-model branches, exact recomputation/plot/report data and a real
TwoStage TD candidate with explicit nominal scope. The prior six-workspace and
independent M1/Buck-VMC gates remain required. 136 contract tests and a real
source preflight passed before final source freeze; these are not frozen-binary
or cross-platform results. Final aggregate regression, RSS, installed wheel and
actual production frozen-process evidence are recorded separately when completed.
The ngspice batch prerequisite is supplied from an official Debian 44.2 package,
privately extracted and source/SHA-verified; a real RC transient agrees with the
analytic 1τ/5τ responses within 2 µV. No system security setting was changed.


## Priority re-audit and preserved baseline red run (2026-10-19)

The clean `187865855b77060124eb01b217a4d243182663b4` source ran the full configured
suite with aggregate process-group RSS sampled each second. It recorded 2429 of
3330 outcomes (including one failure and the explicit long-PFC skip), then its
pytest process terminated with SIGKILL / exit -9 after 1311.174 s. Peak sampled
aggregate RSS was 4,843,824 KiB (4.62 GiB). No completed JUnit or aggregate pass is
claimed. The environment did not expose kernel/cgroup evidence establishing OOM,
so the cause of SIGKILL remains unconfirmed. The unmodified baseline and its logs,
RSS history and collection-index mapping are retained.

The recorded failing test was independently rerun by exact name:
`llc_design/tests/test_i18n.py::test_every_bound_shell_string_is_translated`.
Five new TwoStage shell labels lacked translations. These and seven launcher
labels now have en/ja/ko entries. Fifteen focused i18n checks pass, including
finally-backed cleanup of all six owned windows. A real subprocess repeats three
construct/destroy cycles, injects a missing-translation assertion in the middle,
retains native wrappers to prove destruction, runs cancellable T18 work, and
preserves an unrelated-window sentinel. Each cycle destroys 109 retained native
wrappers. The negative control shows close() alone retaining all six windows.
This verifies a cleanup contract, not the reason for the earlier SIGKILL.

The same baseline separately passed actual installed-wheel and production Linux
frozen schema-3 self-tests: six workspaces, all 47 advanced branches, identical
saved/recomputed waveform fingerprints, and actual TwoStage nominal Apply/report.
The proofs differed only in the frozen flag. Those artifacts qualify that baseline,
not the subsequent priority repairs. New source aggregate/artifact qualification
must run after the new freeze; prior counts and proofs are not substituted.

### Requested-stage defects and independent repair evidence

- T01/T02/T03: nine actual CCM/BCM/DCM workflows; all drawn component clicks and
  arrays/statistics; independent volt-second/charge/power checks; actual parameter
  inverse, valid CCM loop, save→edit→restore→HTML and current identity.
- T05/T18/QR recommendation records: unchanged advanced dataclasses were wrongly
  marked as changed, breaking preview and persistence. Exact typed comparisons
  repair both producer and validator; forged parameter-source edits still reject.
  The T05 invalid-Ron click now gives local error feedback without an uncaught
  Qt slot exception or worker. Independent red/green probes are retained.
- T17: one-call physical inverse for ordinary CCM, joint-demagnetizing CCM, BCM
  and DCM. Independent switched-circuit IVPs check final L/Lm/n, charge, flux and
  energy. The independently replayed 71-case set includes the BCM root boundary,
  alpha=k zero-freewheel endpoint and tiny representable intervals. 452 reachable
  target round trips and three actual light-load GUI Apply/save/reopen/report
  flows pass; seven originally invalid forward points remain rejected.
- T21: actual clickable Co ESR, RΣ and DC bus now select their physical same-source
  terminal signals and persist through recomputation/report. Eleven focused
  full/half-bridge cases pass, including FHA, harmonic and detailed sources.
  Detailed half bridge at 1024 samples genuinely converges; its failing 256/512
  cases retain the strict rejection. Independent baseline/new comparisons show
  all original 44/42/44/48 FHA/fast signal arrays, time axes and request identities
  unchanged; only terminal projections are added. Model-specific output-node
  approximation and underdetermined half-bridge source current remain explicit.

A newly written T05 GUI test initially used a tight QTest.qWait loop and timed out
waiting for Python ODE callbacks. The retained red result is not hidden. Switching
the test to the actual native QEventLoop allowed the real 3%-different target,
Preview→Apply→save→change→restore→report path to finish (169.99 s); no target or
solver tolerance was relaxed. The combined optional-model and existing parameter
suggestion suites then passed all 102 tests in 447.06 s with normal exit.
Focused counts are not added to an aggregate total.

### Screen-level acceptance

The first-level PSD entry and all existing launcher choices are genuinely clicked
on configured Linux Qt screens, including 1280×720. A real screenshot exposed
zero-height PSD ViewBoxes despite correct data arrays. Minimum 180 px plot widgets,
scroll access and a reversible schematic-collapse control fix that layout defect.
The refreshed 720p collapsed view shows both V/I traces and complete statistics;
array identity and subsequent schematic selection remain checked. The existing LLC whole-window
layout was separately tested and repaired rather than inferred from a successful
PSD→LLC handoff. Native Windows/macOS and hardware remain unrun.

### LLC screen and production-proof closure before aggregate qualification

The digital-control page originally propagated a roughly 977 px minimum height
into every LLC tab. It now scrolls its original controls locally, preserving the
outer page object and existing callbacks, while a screen-bounded initial size and
readable Bode height keep the complete window within 1280×720, 1366×768 and
1440×900 at 100% Qt scale. Minimum size is 1228×624. Actual mouse/scroll tests
visit all 12 pages, compute waveforms and the digital loop, run a real nominal TD
candidate, save a project and export the candidate report. Waveform-array identity,
request identity and the original page objects remain unchanged.

A separate hidden-tab defect retained an old 1350 px waveform canvas after a
two-signal component was selected. The fix resizes only the plotting viewport,
retaining each trace's readable minimum. An interim mixed test then exposed a
400/353 px canvas/viewport mismatch and assertion-path Qt SIGSEGV; both red logs
are retained. Compacting the actual two-row statistics table restores the old
1360×900 simultaneous-visible assertion without weakening it. The integrated
52-case component/layout run and 106-case lifetime/digital/session/TwoStage run
now exit normally; final three-screen replay also passes. On 720p, real scrolling
provides access to both complete waveform plots, while the digital Bode plots
fit together after scrolling the page. This is not a claim of native OS/DPI parity.

The production launcher self-test retains schema 3 and every previous gate, adding
a mandatory priority-workflow evidence object. It uses actual T17 light-load
suggestion/Apply/save/reopen/report, an actual T05 typed-option proposal, and
real T21 Co ESR/RΣ/DC-bus clicks, exact curve arrays, export selection and replay.
The T17 independent oracle checks D=0.08629449216106147 and ΔI=0.02 A. Its initial
frequency equality incorrectly rejected 99999.99999999999 versus 100000 Hz; a
1e-12 relative floating-point comparison and retained boundary regression repair
that oracle without changing model tolerances. All 210 contract cases and an
84.51 s actual source-helper preflight pass with worker/Qt cleanup. These remain
source evidence until the clean frozen source is built and exercised separately.


## Source magnetic catalog integration (2026-10-19)

The four supplied PDFs contain 105 physical pages. The source inventory preserves
630 complete explicit part numbers, 318 source-specific geometries, 1294
availability rows and 44 printed formulas (28 DC-bias and 16 two-term loss).
Availability entries do not create invented complete SKUs. Unknown loss waveform,
B convention, temperature/domain and density remain unknown. Thirty-one source
conflicts, including questionable volume units and contradictory material labels,
are retained; declarations cannot override source conflicts.

Local commits `d51739d` and `3c07a0f` connect selection, exact source formula,
explicit modeling declarations, actual PFC sizing, atomic Apply, save/open, report,
and separately labeled inductor self-loss to TTPL/Vienna consumers. LLC exposes
the source catalog as a reference, not an invented complete ferrite/bobbin design.
Fifty-two focused cases and 20 independent probes pass. Independent real-window
replays cover both PFC workspaces, oversize-Apply rejection, current-request
invalidation, full saved hidden fields, repeated LLC catalog reuse and disposal.
A negative-copper-resistance temperature and stale saved design-context bug were
reproduced and repaired before acceptance.

The PFC window previously had a 1520×1044 minimum even before the catalog change.
A real two-direction scroll host now preserves original pages/callbacks while
allowing 1280×720 and 1366×768; actual Calculate/Save/Open/Report controls remain
reachable. No minimum-size ignore, font shrinking or clipped hidden controls were
used as a substitute. The source self-loss label includes its own Vin/Vbus/P/fs/η,
so a different Devices operating point cannot silently appear to share its source.

The reference CS400060 geometry's L0=221.778 µH and a declared QA-only full PFC
case gives 39 turns, 209.479802629 µH at full load, 2.249745373 W core and
1.013325789 W copper loss. These are explicit model declarations, not manufacturer
qualification. The uploaded application example remains arithmetic replay only: its
printed ripple expression differs from standard boost volt-second balance, so its
3.464085382 W result is not a production physics golden reference.

Packaging commit `3f09c4b` retains all old gates and requires this real magnetic
consumer proof. Seventy-six affected contract cases plus the actual helper and
validator pass after the final layout change. The installed wheel and Linux frozen
executable have not yet been rebuilt for these new sources. Evidence sets are
`magnetic-catalog-integration`, `magnetic-catalog-independent` and
`magnetic-artifact-qa`; their manifests record the exact source/resource hashes.

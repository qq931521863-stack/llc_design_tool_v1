# Power Stage Designer validation report

## Worker-wait qualification correction (2026-10-19)

The refreshed `74decea` installed-wheel qualification preserves a failed run:
152 tests passed, one failed during the valid T11 QR setup's 90-second wait.
The empty-input action had not yet run. Its production wheel self-test had passed,
but MCP/public-API/frozen stages were not reached after fail-fast. Source and
installed bytes remained unchanged; this run is not a completed artifact pass.

Independent real-Qt experiments identify substantial Python worker starvation
under repeated `QTest.qWait(10)` calls. The same installed real QR case passes
with a real Qt event loop while preserving the original deadline and all result,
empty-input, job/timer and cleanup assertions. This corrects the test waiting
strategy, not a numerical model or product execution path. The original whole-run
failure remains recorded, rather than replaced by isolated passing runs.
See [the worker-wait mechanism, counterexamples and source boundary](GUI_TEST_WORKER_WAITS.md).
The fixed source passes all 32 pending-input cases (10.72 s); independent review
passes six semantic cases and preserves every original function/physics assertion
except the waiting strategy. Deadline and deleted-timer counterexamples remain.
The integrated pending-input/publication/frozen-contract selection passes 98
cases in 17.67 s. A subsequent comment-only precision change describes observed
worker starvation without claiming inspection of the generated qWait wrapper.
New exact-source installed/frozen and native qualification remain required;
publication stays paused and no physical-model/TI/hardware status is upgraded.

## Windows UTF-8 report test contract (2026-10-19)

In run `39334695559`, actual Windows 10 pt and 12 pt each complete all 100
normal/integrated whole-control checks. At 12 pt, Microsoft YaHei UI resolves to
height 20; frame 1280×720 and minimum 1243×637. The preserved layout artifact is
SHA-256 verified and all complete-visible-region assertions pass.

The job later stops with **282 passed / 1 failed / 1 skipped**, 1252.20 s, in
`test_actual_legacy_td_report_and_formula_pdf_publish_matching_sources`. Its real
TD report export and no-worker-error assertion already pass. The test's implicit
`read_text()` selects cp1252 and fails at byte 0x90, offset 22, in the UTF-8 Chinese
title. The production Markdown/JSON writers already explicitly use UTF-8. The
four affected test reads now state that encoding; waveform identity, candidate
selection, rejection, loss-table and real PDF assertions are retained.

Reader candidate `f5dafe958c125e42b49b0433d37d2318be438c25` changes only this
test module. An external QA-only cp1252-default reproduction makes the original
real failing test red and the explicit-UTF-8 version green (1 passed, 8.26 s).
Three adjacent real CLI/report/rejection/UNKNOWN cases also pass under the same
profile (59.45 s), including the exact non-ASCII model-validity failure label.
All original product/waveform/PDF assertions remain; two Chinese-title assertions
are added. No encoding shim is committed to the product or native tests.
The integrated three changed test functions pass in 72.05 s (16 existing chart
font warnings). Their test module is byte-identical to the reviewed candidate;
all production and packaging files remain identical to the separately frozen
`74decea` wheel/Linux qualification source. No whole-source full-suite pass is
implied by this test/document-only follow-up.

An independent already-generated real TD candidate report reproduces exactly
the cp1252 error at offset 22. Strict UTF-8 decoding re-encodes to the identical
7380 bytes and preserves the Chinese title. This establishes the reader contract;
it does not reinterpret the previous native failed run as success. New actual
native complete/frozen qualification is still required.

## Native 12 pt window-height repair (2026-10-19)

[Run 39334695559](https://github.com/yangshuai2022-star/power-design-toolkit/actions/runs/39334695559)
records macOS 10 pt success: all 50 controls in separate and integrated modes
have complete visible regions. Its next 12 pt preflight fails before control
traversal: **220 passed / 1 failed / 2 skipped**, 576.31 s. The actual PingFang SC
12 pt font has height 22; screen 1280×720, frame 1278×740, minimum 1274×736, dock
viewport 247×619. The failure screenshot and JSON are preserved; native complete
regression/freezing have not run on that failed job.

The repair moves only the independent search-axis and ranking-weight JSON editors
into parallel columns. Their original maximum heights, native minima, exact
values, labels/buddies and all consumer signals remain. A real Qt control
experiment using 18/34 scrollbar metrics reproduces baseline root minimum
1233×745 and frame 1252×749. The patch reduces that minimum to 1233×679 and frame
to 1252×683, with unchanged width. These are Linux controlled-style results, not
macOS acceptance. The real native artifact showed the 88 px scroll-area minimum;
new diagnostics additionally record raw style metrics and every tab's minimum.

Independent candidate checks use real TD computation, mouse selection/Apply,
project save/reload, and a waveform-source report. Editing invalidates results;
reloaded history remains unavailable for Apply/report until recalculated.
The original native font runs, all controls, real frame checks, negative cases,
publication pause and full/frozen gates remain mandatory. See
[large-font layout evidence and viewing limitation](NATIVE_LARGE_FONT_LAYOUT.md).

Candidate `db413cb1ef834aa554fbfaf0c33a6ae8e3d42b89` passes all nine layout-metrics
cases on a clean source, plus 52 exact-source cases covering the three complete
desktop workflows and 43 Two-Stage cases. The independently audited 796-file snapshot is exact;
19 final targeted cases pass, including cancel/edit/options/newer/close and late
results. The integrated candidate passes 75 layout/publication/frozen-contract
cases in 25.44 s. Its two changed files are byte-identical to the reviewed
candidate. These focused results do not replace native execution.

Earlier complete `6138f0f` application/long tests and `2e724b9` installed-wheel/
Linux frozen results below retain those exact source identities. This new
production layout requires refreshed affected/installed/frozen qualification;
no earlier full-suite result is attributed to the new whole tree.

## Refreshed artifacts and native whole-widget scrolling (2026-10-19)

Clean `2e724b99ee13b3d66ef595768e05ca2c176106af` (same 795-file tree as main
`6323518d412306cbc7a6d0dc7aa60497ed76da18`) completes actual isolated installed-wheel
and production Linux frozen qualification. Both schema-4 proofs pass all six
workspaces. The installed wheel passes **292 focused tests and 76 MCP tests**,
zero failures/errors/skips. Both public APIs pass six software baseline checks;
hardware remains false/UNKNOWN. All 627 wheel files match the frozen source,
62 import origins are inside that installation, 378 frozen production modules
match that wheel, and 17 resources pass byte checks. All 795 tracked source hashes
and prepared-runner hashes stay fixed. The extra wheel file over the prior 626 is
the new font regression test, not an untracked product dependency.

- Wheel SHA-256: `2c51a7ab520f151a5da15fa056191f36e08bf713e6dc5a05e8708c1c035a08f7`
- Linux executable SHA-256: `70fbef4781049489861d0d641f42aa7bd7012de691ea26df9dbe4124ec605b11`
- Linux runtime proof SHA-256: `ea5f90823227c1d283169dc67c3290d04e0237ae0b09ff71c945550ab9e9d50b`

Artifact execution takes 1228.41 s; source/provenance verification passes
separately. Installed focused tests take 385.02 s and MCP takes 17.99 s. Real
TwoStage WARN/FAIL paths, successful/canceled/timed-out comparison and final zero
worker/thread counts remain verified. Process RSS was not sampled in this run;
discrete 22:04–22:12 UTC MemAvailable samples reach 4,803,232 kB, which is neither
full-run minimum nor process peak. Each wheel/frozen self-test retains 16 lines
of Matplotlib DejaVu Sans glyph warnings; Qt glyph acceptance is not generalized
to every chart font. Previous full/long/RSS results keep their original source.

[Run 39331097949](https://github.com/yangshuai2022-star/power-design-toolkit/actions/runs/39331097949)
provides actual native 9 pt three-screen LLC results, including source/plot/tab
identity preservation. Windows resolves Microsoft YaHei UI 9 pt, height 15,
zero missing sample glyphs and 147 system-font registrations in 0.093–0.109 s.
Its 1280×720 normal frame is 1252×660 and minimum 1091×607, so the previous font/
first-frame failure is no longer present. macOS resolves PingFang SC 9 pt, height
17, zero missing sample glyphs, frame 1252×703 and minimum 1122×699. The 30-file
JSON/screenshot evidence sets per platform cover these successful 9 pt flows;
they do not contain the subsequent failed 10 pt case.

Native preflights still fail a later test, before complete native regression or
freezing: Windows **220 passed / 1 failed / 1 skipped**, 943.69 s; macOS **219 passed /
1 failed / 2 skipped**, 632.00 s. Both fail the 10 pt narrow-dock whole-widget
containment assertion. Their spinbox bounds are respectively (91,589,140,32) and
(93,583,134,34). Native logs do not identify the failed field or input-method
rectangle; that original hidden state is not claimed to have been recovered.

Qt's real `ensureWidgetVisible` implementation can return once an input cursor
rectangle is visible even when its containing spinbox border is cropped.
Independent actual-Qt tests reproduce this at both edges and with/without focus;
additional event-loop rounds do not resolve it. Real LLC 10/12 pt separate and
integrated modes reproduce the same helper/whole-widget contract mismatch. The
minimal test repair scrolls to the actual widget rectangle, keeps every original
fit/10/12 pt/control/mode assertion, and strengthens visibility/visible-region
checks. A hidden field with apparently contained stale geometry is rejected;
oversized and clipped-control counterexamples are retained. Failure JSON and
screenshots now identify the exact field, focus, input rectangle, viewport/ranges
and mode before any assertion aborts. No product source or layout changes.

The earlier `3f2d1a4` failures remain historical evidence: Windows 210 passed /
1 failed / 1 skipped at the first frame; macOS 491 passed / 1 failed / 1 skipped at
live-controller recovery. Its fixed 80 ms wait was independently reproduced with
real queued drawing and replaced with bounded state observation, preserving
pending/error/old-worker guards and exact b/a/Ts/SHA. The exact candidate passes
13 independent cases including a SciPy boundary oracle. Native acceptance of
that later recovery path is still required; the current 10 pt preflight stopped
before reaching it. All complete-test/frozen and publication-pause gates remain.

## Native preflight portability repairs (2026-10-19)

[CI run 39306491194](https://github.com/yangshuai2022-star/power-design-toolkit/actions/runs/39306491194)
on `deb0b85` completed Linux successfully: **4514 passed, 1 skipped and 2 expected
failures**, 6859.62 s, followed by the actual launcher screenshot. The corrected
first-file-dialog test passed in the complete order. Native preflights then failed:
macOS **1 failed / 188 passed / 1 skipped**; Windows **1 failed / 198 passed / 1
skipped**. Neither native full suite nor freezing ran; publication stayed skipped.
Those failed results are preserved rather than replaced by Linux evidence.

The macOS failure was a nonportable self-test condition, not evidence by itself
that the magnetic values were wrong. [Qt 6.11.2's QMessageBox implementation](https://github.com/qt/qtbase/blob/v6.11.2/src/widgets/dialogs/qmessagebox.cpp)
ignores `setWindowTitle` on macOS; the old gate required the exact title. Two
independent actual PFC Calculate/Apply probes reproduce the old failure with the
title cleared while L=209.48 µH and DCR=48.817 mΩ remain correct. The repaired gate
checks each real applied value separately, the exact success body and Information
icon, and reports all observed operands on failure. Initial L/DCR deliberately
differ from the expected result, so a no-op Apply cannot pass. Six focused owner
cases and **49 related checks including the original complete source-launcher
self-test** pass. Independent eight-case acceptance rejects wrong L, DCR, text,
icon and an acknowledgement without an actual write, while normal/empty/generic
titles pass real save/reopen/report. The added acknowledgement field is diagnostic
telemetry; the offline validator still verifies its existing numeric/source gates
and does not separately certify that new telemetry field.

Windows' 1280×720 subprocess returned `0xC0000409` without captured output. Qt
splits platform options at colons, so an absolute Windows drive path in
`offscreen:configfile=C:...` is truncated before loading. Independent pure-Qt
reproductions with an existing colon-bearing path abort at QApplication creation;
the same JSON works with `configfile=screen.json` and its containing directory as
cwd. Qt's Windows fatal path uses fast-fail, consistent with the observed code.
The test now uses that portable relative configuration, explicit UTF-8 diagnostics,
unbuffered stage messages, faulthandler and Qt stderr logging. All original real
three-screen navigation, scroll, worker, plot, export, source and cleanup assertions
remain. Six pure-Qt path/screen regressions plus the three complete desktop flows
pass locally; an independent fresh run of the exact final nine cases passes in
183.70 s. The independent 17-case matrix also rejects wrong cwd, malformed JSON
and an unintended default screen. An AST comparison confirms all 50 original
workflow assertions remain, with only the explicit DPR=1 condition added. Original native logs lacked the inner diagnostic
state, so the corrected native runs remain the decisive platform checks.

These changes affect self-test/fixtures, not converter algorithms or production
Apply behavior. The previously documented 6138f0f full/long application evidence
remains attributed to that exact source; the modified production-launcher self-test
requires refreshed installed/frozen execution. Linux and both native jobs now start
in parallel, with the same preflight, complete tests and frozen verification. Both
publication jobs retain `needs: [test, build]` and literal-false pause conditions;
**66 workflow/strict-validator contract checks pass**. There are still only the
existing two native runners, no new paid service, no removed gate and no release.

## Completed Linux application qualification and retained CI red (2026-10-19)

Clean local source `6138f0fd36f07539f78ed208ca4cdee5ce2f136d` has the identical
793-file tree as remote `02c2ca3af21115292b6b8185815a4cd63c63f2db`
(`d16ac697133ef76a99f6ae255f255a0a4ff6bc48`). Its complete local suite passes
**4512 tests, with 2 opt-in skips and 2 historical expected failures**, zero errors,
in 4059.551 s. All tracked hashes stayed unchanged; final GUI/native plot ownership
counts were zero. Aggregate RSS peaked at 1,892,100 KiB. Both skipped long consumers
were then run separately on that same source: real PFC **1 passed / 848.201 s** and
finite-Coss PSFB **1 passed / 533.346 s**, again with unchanged hashes and clean
final GUI ownership. Independent actual LLC and all five integrated-magnetics
consumer scripts also pass on this exact source.

The isolated, non-editable installed wheel passes the actual production schema-4
self-test, **268 installed drawing/desktop/numerical/export tests**, all **76 MCP
tests**, and both public built-in LLC validation APIs. All six software baseline
checks pass while hardware verification stays false/UNKNOWN. All 626 wheel source
files match the archive; 62 imported application module origins are inside that
wheel installation. The actual PyInstaller Linux executable passes the same
schema-4 runtime gates and source/resource collection checks outside the checkout.
The real default seventh-order harmonic-balance result, cancel and timeout paths
finish with zero active workers/threads; the invalid TwoStage example remains
rejected without altering the project. These selections overlap and are not added
to the full-suite total.

- Wheel SHA-256: `395fd92bffaa54837be6c77dae026f86a1737f7e26ec73d8f72319157340d936`
- Linux executable SHA-256: `9ee918c6e839f64557875a811b7ac8dd0cf2262cb584ff237929d93bf6d5bae6`
- Linux runtime proof SHA-256: `0dd6263831899e876d3525969ecb128d416727e09d240547a4fbcca38c4482c0`

The separate GitHub run [39289989355](https://github.com/yangshuai2022-star/power-design-toolkit/actions/runs/39289989355)
remains a **failed** run: 1 failed, 4512 passed, 1 skipped and 2 expected failures.
Its only failure was the first real file-dialog Save Project test: the 24 V current
result was correct, but no project was written. Two independent, product-free Qt
reproductions demonstrate a sufficient automation failure mechanism: key events were sent to a filename
editor without focus; QFileDialog retained selected parent directories, so Accept
navigated instead of saving, and the watchdog canceled the dialog. Real focused
Save-button paths write the exact requested file; cancellation and failed writes
retain their explicit guards. This red result is not erased by the local passes.
The original CI did not capture focus or selected-file telemetry, so its exact
unrecorded widget state cannot be proved retrospectively. The test-only correction
now waits for window activation, clicks the filename field, asserts focus and the
exact selected file, and clicks the actual Save button. Acceptance/rejection and
watchdog outcomes are explicit; no filesystem/dialog mock, retry, enlarged watchdog
or weakened output assertion was introduced. A new fresh-process cold-start test
is red with the old driver and green with the corrected driver. All **97** pending-
input/modal-ownership checks pass locally. Independent acceptance adds 15 state
scenarios and six real save/open/HTML/CSV dialog roundtrips, including changed-input
identity and failed-write/cancel counterexamples; the integrated test file passes
27 checks. Subprocess diagnostics use explicit UTF-8 decoding without changing
exit-code assertions. The new native CI result is still pending.
Windows/macOS frozen packages remain unqualified; publication remains paused.

## Preserved MCP workflow: runtime-cache identity repair (2026-10-19)

The concurrent `54486b6` LLC/MCP workflow was preserved, including its two README
links and all implementation files. Its isolated 35-test suite passed, but the
complete GitHub Linux run failed **10 MCP tests / 4423 passed**. A real three-test
order reproduced the failure in 12.53 s: the launcher subprocess creates
`webapp/.mplcache/fontlist-v3.11.0.json` after the MCP startup fingerprint is frozen;
the old recursive JSON scan mistakes this generated font cache for engineering data.

The repair fingerprints production Python modules and maintained catalogs/data
(including the LLC validation baseline), excluding test trees, hidden caches and
regenerable outputs. It re-enumerates inputs so real module/catalog additions,
changes and deletions still invalidate snapshots, as do dependency-version changes.
The startup fingerprint is not reset to hide drift. The identical real test order
now passes all three cases while still creating the font cache. The focused owner
selection has **120 passed**, including 41 new regressions. Independent acceptance
passes 76 MCP tests plus 635 source and 635 repaired-wheel boundary checks, actual
restart provenance, and real installed stdio design/child-job/JSON+HTML exports.
These overlapping selections are not summed into a full-suite total.

This check also found a real pre-existing wheel omission: both public built-in LLC
validation APIs raised FileNotFoundError because the baseline JSON was not packaged.
The package-data fix includes that committed resource. Both APIs now pass all six
software baseline checks while external-simulation and hardware status remain
UNKNOWN. The installed wheel matches all 357 production Python files and 17 input
JSONs; complete runtime identities and a source-created snapshot match across the
source/wheel boundary. A persistent actual-build/install/public-API regression
passes. Complete final-source regression and native freezing are still pending.

The previous `078907e` baseline also finished its real PSFB long test: **one passed
in 548.692 s**, with all 777 source hashes unchanged. Its wheel/frozen stage was
held before building. The later GitHub `4e15d503` Linux run failed five tests and
seven fixtures at the old PSFB ragged coefficient matrix, confirming that the
already repaired portability defect was not limited to Windows. No failed or
interrupted run is counted as a complete pass. Formal release remains paused;
current Windows/macOS frozen artifacts are still unqualified.

## Native Windows follow-up and source boundary (2026-10-19)

The combined Linux source `078907e` (identical tree to main `4e15d503`)
completed **4401 passed, 2 opt-in skipped, 2 historical xfails**, zero failures
or errors, in 4022.59 s. All 777 tracked-file hashes stayed unchanged. Session-final
top-level/auxiliary widgets, native roots and all native plot-owner counts were
zero; aggregate RSS peaked at 1,925,988 KiB. Its separate real PFC long test
passes (one test, 859.065 s, unchanged source); PSFB long/artifact stages remain
separately accounted for. These results do not qualify later application changes.

The original Windows run at `c19e208` later exposed remaining portability defects.
It was canceled after **46 failed, 1637 passed, 1 skipped and 7 fixture errors**,
not a completed whole-suite pass. Thirty-two failures concern exact export bytes,
four downstream failures and seven fixture errors share the PSFB coefficient
matrix defect, and nine drawing failures plus one LLC width failure are on the
older rendering/layout implementation. The interrupted large-map test was inside
pytest/difflib rendering a multi-megabyte byte mismatch; this is not evidence of a
physical-solver deadlock. PyInstaller/frozen verification was skipped.

Two minimal production corrections address the newly visible defects:
- PSFB polynomial conversion can independently trim high-order zeros in each of
  seven states. The reconstruction now zero-pads to the fitted degree before
  building the matrix. The original 2e-11 withheld-sample check, root filters,
  solver-boundary validation and physical tolerances are unchanged.
- FRA session/CSV and toolbox JSON/TXT/CSV writers preserve the serialized UTF-8
  bytes, avoiding platform newline translation and doubled CR in CSV. Modal
  ownership/current-source guards remain intact. Large-file tests still compare
  every byte; only failure diagnostics are bounded, with corruption counterexamples.

The polynomial repair has 13 red-to-green regressions, 38 existing dynamic checks,
actual T18 GUI/save/report/reopen acceptance, and 19 checks with the original
Windows NumPy/SciPy versions on Linux. The export repair has 631 nonduplicated
related checks. Independent export acceptance covers 30 actual GUI scenarios,
376 assertions and seven corruption classes on a real 5.1 MB map; an independent
real-text-I/O model reproduces all 44 old-writer newline distortions. Independent
PSFB acceptance adds 5,170 assertions, all 26 real T18 branch selections, project
and HTML identity, and independent whole-period energy/current integration. During
the real consumer, 2,652 accepted old/new coefficient arrays are unchanged and
2,124 candidates remain rejected by both versions. Seven deterministic real-function
cases reproduce the former ragged shape; this Linux T18 fixture itself does not
reproduce that particular numerical path. A separate 242-assertion check
uses the original Windows NumPy/SciPy versions on Linux. The integrated
new native-preflight selection passes 64 tests. These are focused Linux/IO-model
results, not corrected native Windows/macOS qualification. Refreshed complete
source qualification and actual native freezes remain required. Publication stays
paused, and no native gate has been removed.


## Historical immediate-export aggregate rerun (2026-10-19)

The first combined-source run at `2738299` stopped with **376 passed and one
failed test**; the remaining 4028 collected tests were not executed. The retained
failure was an outdated expectation that immediate report export must raise while
the debounce timer is active. Independent replay confirmed the new explicit-action
contract recomputes valid current input: the HTML contains 13.25 V and its new
identity, excludes the old identity, and all four loop responses exactly match the
new plant. Invalid Vout=20 V still rejects export and preserves an existing file.
The test now checks those actual data/loop identities and invalid-input protections;
no application code or numerical threshold changed. **39 focused tests pass** after
that test correction. This does not turn the interrupted aggregate into a pass;
a fresh complete run remains required. Actual LLC independent consumers and the
five original integrated-magnetics scripts already pass on the unchanged application
bytes. Publication remains paused.


## Rendered-circuit audit and current source boundary (2026-10-19)

A new audit of the actual Qt/SVG drawings found gaps that the earlier numerical
and netlist checks could not detect. The findings are retained rather than being
hidden by older VERIFIED labels:

- LLC MOSFET body-diode graphics contained a continuous drain-to-source line
  through the diode. The repaired symbol has separate leads and no bypass wire.
- PSD selection backgrounds hid connected terminal wires; nonuniform SVG fitting
  distorted symbols and click coordinates. Background order and shared
  aspect-preserving paint/click mapping are corrected.
- Several winding/capacitor waveforms inherited another device's polarity text.
  The audit follows the **final calculated result**, including later aliases in
  the dynamic PSFB solver, through selection, saved-project restoration and HTML.
  Values, segment objects and CSV bytes are preserved where only references change.
- Dynamic component labels crossed conductors or other glyphs. Independent image
  replay found two additional cases after the first repair; both are corrected.
- Shared PFC/LLC/DAB/PLL control drawings routed lines through unrelated blocks or
  placed labels over signals. The repair adds explicit ports, routing lanes and
  non-junction crossing marks, checked against each real consumer's signal source.
- The separate Control Tools Type II/III drawing previously used only wires and
  R/C labels, visually bypassing every component. Real IEC resistor/capacitor
  symbols now implement R1 (Type II), R1 || (R3 + C3) (Type III), and feedback
  C2 || (R2 + C1), with explicit inverting feedback and Vref. Pole/zero mode
  states that R/C values are not uniquely determined.

The renderer inventory covers 19 direct PSD topologies and three optional
physical diagrams, LLC half/full bridge with separate/integrated magnetics,
17 shared control-diagram variants, both Type II/III networks, the labeled
functional sensing chain and transformer winding-stack illustration. A winding
stack or functional chain is not a physical circuit/netlist. No separate PFC/DAB
power-circuit page is invented as part of this review.

The first six repairs have independent actual-render/click checks: 22 PSD drawings,
408 selected-terminal regions, 872 PSD clicks and 1,424 LLC clicks. Actual T16
CCM and **explicitly asserted DCM** cases cover 1280×720 and 1560×1100 selection,
save/switch/restore, plotted data and HTML references. A former nominally
"light-load" fixture was still CCM and is no longer counted as DCM evidence.
Independent T15/T16/T17 DCM checks preserve numerical results and CSV bytes;
maximum checked KVL residual is 3.56e-15 V. Independent review is now complete
for the fixed drawing candidates, including all 17 control variants (476 actual
clicks), all 26 final dynamic-PSFB device references (52 clicks plus save/reopen/
plots/HTML), and the 10 affected static presets (15 branch clicks). Static and
dynamic reference-only changes preserve non-reference numerical results and CSV
bytes. The Type II/III review used 12 rendered size/mode cases and eight real
main-window states; independent visible-network impedance agrees with the model
within 9.8e-16 relative error. Production PFC 1280×720 scrolling, zoom, selection
and real fullscreen were independently replayed with 52 clicks.

Integration `ebcd1fc6826e144c113cfa730eed50d1ad99a8a8` preserves the exact reviewed
product-file bytes. Integrated control/compensator/window/publication contracts
passed **59 tests in 9.06 s** before the final reference-only commit; the complete
merged reference suite then passed **45 tests in 68.97 s**. These are separate
focused runs, not an aggregate or native-package pass.

### Bounded consumer repair: pending inputs and external H(z)

The original five-path actual GUI audit passed submitted-input Buck, optional-physics
T05, X01, X02 and X07 input/calculate/select/save/reopen/report chains. It also
retained a genuine 15-action red matrix: typing Vin=24 over 12 without Enter/Tab
could calculate at 12; native dialogs could commit after revision capture and
silently abandon output; blank text could revert to old input. The repaired PSD
pre-action boundary commits locale-valid text, rejects zero/blank intermediate
input, invalidates old generations and labels retained plots as previous-input
results. Independent fixed-source replay passes all 15 actions, all five complete
paths, and three real T05 running-worker cases with edits made after 250 ms of
actual execution. Late results cannot publish, save or replace a newly edited
project. Worker pools finish with zero active jobs. No model/worker mock substitutes
for these acceptance paths. The owner regression passes 166 related tests.

Advanced restored values retain their hidden full precision until explicitly edited.
A same-display keyboard edit formerly left that field's old precision cache intact
because Qt emitted no valueChanged signal. The small textEdited correction releases
only that key and emits one change. Three real Qt parameterized groups exercise
all 21 physical fields, options, design identity and UTF-8 JSON snapshots; 62
related tests pass. Independent replay on `43ed514` also checks all 21 fields with
real same-display keyboard edits: zero valueChanged signals, exactly one changed
signal, target-only cache removal, preserved untouched precision, and identical
input/identity after an actual atomic UTF-8 JSON snapshot round trip. These are
identity/GUI checks, not claims of measurable physical solver differences below
display precision.

The actual launcher→Control Tools→LLC audit retained two further red cases: an
invalid live digital source left old H(z) exported as current, and the external
period editor could display 30 µs while controller, ADC and C99 still used 25 µs.
The repair propagates pending/error source invalidation, blocks stale computations
and exports, and makes external sample time exact/read-only. It does not
rediscretize b/a or silently fall back to an internal controller. Saved controller
snapshots have explicit independent source/capture/coefficient identity; malformed
metadata is rejected atomically and legacy sessions remain supported. A snapshot
ignores unrelated live edits until explicitly reconnected. Switching to the internal
controller restores its previous period. Independent replay passes six actual GUI,
late-worker, same/fresh-host save/reopen, legacy snapshot, reconnect and C99 probes.
The fresh host test restores 31.125 µs after leaving a 25 µs snapshot, rather than
keeping its default 20 µs. The owner regression passes 533 related tests; UTF-8
file operations are explicit in the new tests. This is focused candidate acceptance;
complete merged-source, installed-wheel and frozen/native qualification are pending.

### Completed worker-repair baseline; later drawing changes remain separate

Clean source `d81b160fa8679a3fd00274c86f841e628fc43c8a` completed:

- Full Linux/offscreen regression: **4233 passed, 2 opt-in skips, 2 historical
  xfails**, zero failures/errors, 43 warnings, **3824.67 s**. Aggregate peak RSS
  was **1,912,920 KiB**; all 769 tracked-file hashes stayed unchanged.
- Separate real PFC switched-GUI long test: **1 passed**, zero failures/errors,
  101 warnings, **950.01 s**, aggregate peak RSS **2,706,404 KiB**, the same 769
  source hashes unchanged. Actual visible configuration/source/thermal identity,
  stale modal export rejection and source switching were checked with the
  shared-ngspice result (78,485 full-period samples).
- Both runs recorded zero session-final top-level widgets, native roots,
  auxiliary windows/menus and plot-owner categories. Remaining native wrapper
  totals were 3001 and 565 respectively; these are not falsely reported as zero.

This closes the earlier worker-ownership regression on that exact source. The
subsequent artifact stage deliberately exited 75 before any build while the new
schematic scope was being repaired. That planned hold is neither a product
failure nor a wheel/frozen pass. The changed drawing source requires a new
qualification; historical `a698dc5` wheel/Linux-frozen evidence stays historical.

## New-source checkpoint, retained-wrapper failure and numerical changes (2026-10-19)

Source `a698dc533124e9c2d22342761cc2c56bf01a1f88` had unchanged tracked-file hashes
through each of the following separate checks:

- Installed wheel and real Linux PyInstaller production self-test/schema-4 proof
  passed. Both actual GUI Two-Stage examples were exercised: valid WARN Apply /
  save / reopen / report, and invalid FAIL with disabled Apply and unchanged input.
- Independent full/half-bridge actual consumers and all five original integrated
  magnetics consumer/identity/boundary/FHA-label/TD-label scripts passed.
- The full regression stopped after **3707 passes, two opt-in skips and one failure**
  in PFC worker Python-wrapper collection, elapsed **3595.72 s**. Peak aggregate RSS
  was **1,946,992 KiB**. Native worker/signals were already deleted; the wrapper's
  strong referrer was QThreadPool. Independent empty QRunnable reproduction excludes
  product callbacks and test observers as the cause. Red evidence is retained.
- The remaining-package diagnostic run passed **445 tests with two historical
  xfails**. It does not convert the failed full run into a pass.
- The actual finite-Coss PSFB inverse sizing opt-in test passed in **487.33 s**,
  checking Lout/Lm/ratio recovery and independent physical current extrema.

The shared worker-submission barrier prevents native auto-delete before PySide's
post-start wrapper parenting. All production producers use it. It does not add a
sleep, relax collection deadlines, disable auto-delete, purge unrelated jobs, or
turn a rejected candidate into an accepted one. New-source qualification must
include normal/error completion, rejected/duplicate submission, queued clear,
close/reopen, unrelated global work, and aggregate retained-object checks.

The numerical correction changes some integrated-magnetics outputs; historical
values must not be silently carried forward. In the independently rerun case,
accepted TD total loss changes **75.4389136301 W → 76.0383304727 W (+0.794572%)**,
physical B peak **0.09674371783 T → 0.09717147126 T (+0.442151%)**, and output
**53.0136615067 V → 52.9999971246 V**. The new regulated periodic residual is
**1.02083e-14**. The independent port/flux/loss identity checks still pass. These
values qualify this software case, not hardware accuracy or every operating point.

The public [input-only positive and negative examples](../examples/llc_two_stage/README.md)
were saved/loaded through actual project APIs and separately run through the
production CLI. They contain no accepted results or saved analysis. Their exact
singleton axes and nominal-only TD/FHA source split are documented.

## Actual native failures and independently checked numerical repair (2026-10-19)

[CI run 39227806648](https://github.com/yangshuai2022-star/power-design-toolkit/actions/runs/39227806648)
at `c19e208466d51e09ab657c2c612d2dd6fa2857cb` passed Linux with **4153 passed,
1 skipped, 2 historical xfails in 2809.89 s**. macOS failed with **16 failed,
4096 passed, 42 skipped, 2 historical xfails in 2675.33 s**; its freeze steps
were skipped. A successful Linux run does not supersede these native failures.

The failures separate into five stagnated independent PSD roots, two PSFB BCM
inverse-boundary cases, two desktop layout cases, five consumers of an invalid
TwoStage example, and two switched-LLC convergence cases. Repairs retain the
original physical acceptance bounds:

- PSD roots require freshly recomputed charge/state residuals plus admissible
  intervals, diode conditions and power balance. Cached optimizer residuals or
  its success flag alone cannot accept a result. The PSFB inverse branch uses
  relative roundoff tolerance without an absolute-amp floor. Independent
  perturbation/scaling and deliberately bad-root cases cover both changes.
- The LLC solver now splits every bridge event, integrates the stiff smoothed
  rectifier with adaptive Radau and freshly checks the physical periodic defect.
  The previous 512-grid map reported 1.82e-12 while independent DOP853 reintegration
  found a 0.005685 normalized defect. This was an integration-accuracy bug, not
  a harmless platform-specific status difference.
- Continuous derivative-zero Im extrema now drive the FHA/TD Im-peak validity
  metric. Uniform plotted/stored samples and their statistics remain labeled
  as samples. The original Ln=5.5/Np=30/Ns=4 full-bridge example has continuous
  Im=7.52881290784 A and a **20.92046% FAIL**, consistently at 128/256/512 samples;
  the 20% gate is unchanged. Real GUI/CLI and frozen checks retain this rejection
  and cannot Apply/export it as an accepted candidate.
- Separate DOP853 equations, unrestricted periodic-root checks, KVL/KCL and
  integrated energy/volt-second/charge balances validate the revised orbit.
  On numerical commit `58ba89b4e5f053c408bd328849f5d44d8116f0eb`, real TwoStage
  full-bridge Np32 and half-bridge Np16 (Ln=4.5, Ns=4, hold-up=320 V,
  Cbus=0.0033 F) both retain WARN. Six metrics agree with the independent
  references within 0.00836% relative difference; Im differs by 0.0000144%.
  TD loss/report waveform SHA agrees, and only one of seven workpoints is TD.
- For the original negative example, sampled Im/B peak underestimation is
  0.77660%/0.18188%/0.18188%/0.03544% at 128/256/512/1024 points; sampled
  capacitor-loss quadrature errors were separately measured. These case-specific
  figures are not a universal passive-loss accuracy guarantee. Higher sampling
  remains necessary where the requested waveform/loss accuracy requires it.
- Native-size GUI fixes stack dock actions, wrap the transformer subtitle and
  bound long diagnostics with scrolling. The unchanged three desktop workflows
  and atomic-load/ownership set pass 57 checks on `f0772047ca0da86ff15d68bd0e9d0d03bb65cb07`.
  Reverting each constraint reproduces overflow. At forced 12 pt/1280x720 all
  waveform portions are scroll-reachable, though one full plot exceeds the
  viewport height; native display qualification is still separate.

The first local compatibility preflight stopped solely because that new venv
lacked setuptools/wheel. Its red log is retained; installing the same explicit
build prerequisites as CI fixes the environment without changing a test.
The integrated-source full regression and native frozen proofs remain pending.
Previous qualifications below remain historical, not proof for the new solver.

## Pushed-source CI dependency correction (2026-10-19)

The first actual main workflow at `21f5df4e7256c67d78add8cac16f3e5ea898977b`
([run 39221389558](https://github.com/yangshuai2022-star/power-design-toolkit/actions/runs/39221389558))
finished **4151 passed, 1 failed, 1 skipped, 2 historical xfailed in 2773.40 s**.
The single failure was the real PDF source-validation test importing `pdfplumber`;
that package was declared only in the optional research extra, while CI correctly
installed dev/web/gui/agent. This was an incomplete test-dependency declaration,
not a numerical test failure. Windows/macOS builds and both publication jobs
were skipped; no native proof was produced by that run.

The correction declares `pdfplumber>=0.11` in the dev extra, retains the research
extra, and adds a contract for both CI installation commands. The real PDF source
and bundled-data tests pass locally: **24 passed in 5.97 s**. No application
algorithm, default-test skip or publication gate changed. A new complete main CI
run is still required; the earlier failure is retained rather than relabeled.

## Latest completed application regression and remaining gates (2026-10-19)

The immutable application source
`4f9f5b4513a62fa9c5effb1b4cad5beacf36daac` completed the full configured
Linux/offscreen collection with **4140 passed, 2 skipped, 2 historical xfailed,
43 warnings in 2728.51 s**, exit 0. JUnit contains 4144 outcomes, zero failures and
zero errors; its four skip-encoded records consist of two actual skips and two
expected failures. The worktree remained clean and all **760 tracked-file hashes**
matched before/after. This is one completed run, not a sum of diagnostic segments.

| Evidence | Result and exact scope |
| --- | --- |
| Complete regression | `final-regression/integrated-full-4f9f5b4`: `source.json`, `pytest.log`, `junit.xml`, `status.json`, `rss.csv`, `lifetime.jsonl`; source SHA above |
| Ownership and resources | Final native owner roots, scenes, plots, menus and WidgetGroups all zero; peak aggregate RSS **1,847,560 KiB**, peak parent RSS **1,352,888 KiB**. This measured run is not a universal no-leak proof. |
| Independent integrated-LLC consumers | All five consumer/identity/boundary/FHA-projection/TD-label scripts PASS at the same exact SHA; unchanged source hashes. Evidence set: `integrated-merge-qa/4f9f5b4513a62fa9c5effb1b4cad5beacf36daac`. |
| Opt-in real switched-PFC desktop test | **PASS: one actual shared-ngspice desktop/consumer case in 825.16 s, exit 0, exact 4f9f5b4**; remains skipped in the aggregate and is counted only in its separate serial run. |
| Opt-in finite-Coss PSFB joint sizing | **PASS: one finite-Coss joint-sizing case in 495.49 s, exit 0, exact 4f9f5b4, including 18 forward evaluations and independent extrema**; remains skipped in the aggregate and is counted only in its separate serial run. |
| Installed wheel and actual Linux frozen application | **PASS for the actual installed-wheel and Linux frozen schema-4 runs at 759a8fb; source-collection audit is recorded separately below**: both runtime checks completed successfully from an isolated installed wheel, with original proof bytes retained. No older artifact result is substituted. |
| Native Windows/macOS | **pending actual Windows/macOS CI qualification on the pushed source**: both native package/startup/frozen proofs remain separate gates; Linux/offscreen checks do not satisfy them. |

The two existing expected failures are
`backend.tests.test_control_api::test_compensator_design_meets_bandwidth_and_phase_margin`
and `::test_loop_gain_api_reports_margins`. They document the single-2P2Z
attainable-phase-margin limit with the chosen EDF plant (approximately 25 degrees
at 5 kHz, below the requested 45 degrees). They are not newly hidden regressions
or passed control targets. The two opt-in skip reasons and the 43 warnings remain
in the raw log; skipped and expected-failure cases are not counted as passes.

### Exact installed/frozen artifact provenance

The source-isolated installed wheel and actual PyInstaller Linux executable both
completed the production schema-4 self-test and strict bundle validator at
`759a8fb54a0443a2e97125335dc69b61b5942ec7`. All six workspaces, real PSD/bridge
suggestions, TwoStage consumers, CSC selection and integrated-LLC default HB,
cancellation/timeout, save/recompute/report consumers are exercised.

The enclosing preparation command initially exited 1 after these runtime passes:
its separate provenance helper treated PyInstaller's `-` namespace marker as a
real file path. That red log and empty output remain intact. The outside-product
helper now accepts only actual namespace directories without `__init__.py`, and
validates exact runtime-specific import paths and bounded wall times rather than
requiring these inherently different values to match. **78 helper/negative cases
and the actual collection audit pass**: 377 installed-wheel module sources and
17 resources match the frozen source bytes; one namespace is verified. Exactly
seven fields differ (frozen flag, three import locations, three bounded durations);
every other semantic/numerical field matches exactly. Product source, artifacts
and original proof bytes were unchanged. This repair did not rerun or relabel the
application regression, and did not weaken the production bundle validator.

- Wheel SHA-256: `2638343c5797b5371e4204d9e4253e7ee454ce7a574e5905cf52946cfe8ba843`.
- Installed-wheel proof SHA-256: `d29df14e39a1e0ff5a043e01934eb837fa04de59e02b467c4cf27785ffcf63e0`.
- Linux frozen proof SHA-256: `83b0c0652f4a18c983dafd2faa4c62e91aae3d8d5c0941affecddee3687579e5`.
- Linux executable SHA-256: `fa81dc0db058cede0f163e4459366c6dac2b5c260f8c2512c6f314b6c1f6d55b`.

Original logs, proofs, collection audit, repair counterexamples and the separate
exit-status summary remain in the `completion-artifact-qa` evidence set. PFC's
101 warnings include font fallback warnings; the original run and screenshots
are retained. No warning, historical xfail or failed preparation command is
silently counted as a passing test.

### Application source versus final artifact and pushed source

- **Application full-regression SHA:** `4f9f5b4513a62fa9c5effb1b4cad5beacf36daac`.
- **Integrated CI-only pause:** `759a8fb54a0443a2e97125335dc69b61b5942ec7` changes exactly
  two publication-job guards to literal false and adds 11 pause-contract cases.
  Independent focused release-contract qualification is **415 passed, including
  those 11**, not 426. This result is not a second full-suite run or native proof.
  Integration status: **integrated by local fast-forward; all application/resource bytes remain identical to 4f9f5b4**.
- **Artifact build SHA:** **759a8fb54a0443a2e97125335dc69b61b5942ec7**; **final intended push
  SHA:** **recorded externally in the final delivery and remote-commit mapping after this documentation commit**. These identities remain separate.
  The final certificate enumerates every delta from the application-test SHA and
  confirms unchanged application code/resources. The approved CI-only/docs delta
  does not require repeating the full suite; it also must not be described as a
  full run on the later SHA. Any additional application/resource change requires
  renewed qualification rather than this exemption.
- **Remote main:** **authorized after qualification, but not yet performed at this documentation checkpoint; inspect the corresponding GitHub Actions run and final transfer record**; no remote write is claimed
  by this documentation checkpoint. The verified remote SHA belongs in the final transfer record.
- **Formal release:** paused. Source integration/push, CI validation/build and
  public tag/release publication are separate actions. A successful source push
  does not authorize or establish a published 9.9.0 release.

All application evidence is bounded by the detailed feature matrix. Exact TI
executable correlation remains unverified; unsupported physical models are
separate engineering limits. Native platforms, vendor-data accuracy, new bench correlation, thermal/EMI and
hardware safety remain unqualified unless separately proven. Existing measured-FRA
import/analysis capabilities are not a hardware sign-off.
The earlier red, interrupted and diagnostic-only records below are retained as
history; their former pending statuses are superseded only by the specific
completed gates above.

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


## Historical failed aggregate and T15 independent diagnosis (2026-10-19)

At clean source `bfb0e54067eebb973811213a671e9906dca0f6c6`, the full 4140-case
collection stopped at the first failure with **1353 passed, 1 skipped, 1 failed**
in 1493.54 s. Exit 1, complete JUnit and immediate failure telemetry are retained.
The failure is `test_model_branch_currents_obey_kcl_at_every_drawn_net[T15]`:
its external-source boundary used average 0.2375 A at each instant. Independent
CCM KVL/volt-seconds at phase 0.040375 gives 0.4879686037234042 A, exactly explaining
the 0.2504686037234042 A VIN/GND residual. Every internal node was already balanced.
For equal split C on a stiff bus, C1+C2 current is zero and source=(Q1+Q2)/2.
The narrow test correction retains all nets, signs, samples and the 2e-7 A limit;
new fixed-point/off-interval/mean-integral checks reject a fabricated flat source.

An independent SciPy event-ODE review, not a copy of production bridge helpers,
covers 16 combinations of ordinary CCM, joint CCM, BCM, DCM, Vf=0/0.4 V and
nominal/near-10%-ripple capacitance. Maximum independent current error is 1.943e-16 A;
all-net residual 2.429e-17 A; capacitor voltage error 2.132e-14 V; Cdv/dt residual
6.939e-18 A. Source charge and capacitor energy close over the period. GUI/HTML
preserve the mean-metric identity; device CSV has no pseudo source-current column.
X12's DC mean is appropriate for its operating point; the label is made explicit.

The energy audit also quantifies an existing model limit, rather than hiding it.
With midpoint offset delta, exact reconstructed capacitor power is delta*ip and
KVL primary voltage would be sign*Vin/2-delta. Product magnetic slopes use nominal
sign*Vin/2, as the visible assumptions state. Corrected accounting closes to
2.443e-15 W, but this diagnostic is not a coupled magnetic-state solution. Near
the allowed capacitance limit, nominal instantaneous power differs by up to
0.140434452 W / 4.9836% of the source pulse in the audit vectors. Average energy
closure must not be presented as exact finite-C instantaneous dynamics.

Resource evidence for the failed run: peak aggregate RSS 1,836,372 KiB, peak
parent RSS 955,260 KiB, minimum MemAvailable 4,185,580 KiB. Final native roots,
scenes, plots, menus and groups are all zero. The separately selected remainder
is diagnostic only. The affected 387-case group and one additional actual X12 mean-label/persistence/
export case pass. Independent review reran three narrow source/consumer tests
with exit 0. The diagnostic remainder then stopped at a separate new-feature
i18n omission (1488 pass, 1 skip, 1 fail): 14 bound shell strings lack en/ja/ko
entries. The isolated repair supplies all 42 translations and an actual Qt
en/ja/ko/Chinese switch/restore test; all 16 i18n/lifecycle cases pass in 24.81 s.
The harvest gate and deliberate missing-entry cleanup counterexample are unchanged.
The later lifecycle diagnostic failure is the same missing-translation cause,
not evidence of a second resource defect. Complete qualification of the corrected source remains open.


## Historical completion qualification correction (2026-10-19)

Integrated-LLC leakage resonance is locally implemented in `c06f1d5`. Independent
review corrected two unsafe substitutions: balanced-MMF leakage energy is not
APR Lshort, and APR magnetizing flux is not generally physical-core flux. The
implemented matrix/APR conversion, physical current mapping, insulation-region
field integral and single-transformer accounting are exercised by actual consumers.
Independent two-port checks cover 900 complex-load/frequency cases (maximum
relative error 6.83e-13). Actual regulated TD converged with residual 1.9241e-10;
physical Bpk 0.0967437178 T and total loss 75.4389136 W independently reconcile
with port currents and the component ledger. These are model/software tests,
not measured hardware qualification. Five independent actual consumer/identity/
boundary/projection/TD-label scripts subsequently pass at exact clean bfb0e54,
with unchanged start/end tracked hashes. This does not qualify later source edits.

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

## Historical re-audit and terminated aggregate run (2026-10-19)

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

## Historical qualification source freeze

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

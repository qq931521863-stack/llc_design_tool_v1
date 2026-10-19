# Native C99 byte identity and controller snapshot qualification

## Observed native failures

Run [39338660357](https://github.com/yangshuai2022-star/power-design-toolkit/actions/runs/39338660357)
passes the complete 10 pt, 12 pt and controlled-scrollbar layout profiles on both
platforms. Each profile traverses 100 complete normal/integrated controls.
macOS 12 pt minimum height decreases from 736 to 669 pixels, and the actual
1280×720 frame fits the screen. Windows 12 pt minimum is 1243×587; the controlled
scrollbar profile is 1243×647. These are actual native layout results, not native
full-suite or frozen-package acceptance.

The macOS preflight stops with 512 passed, one failed and two skipped. Its saved
controller-snapshot test captures `current_digital` immediately after a fixed
80 ms wait; it is still `None`. A live edit deliberately invalidates the current
controller synchronously, then publishes only when the real debounce timer runs.
Tests must observe that valid/error completion before capturing or comparing a
snapshot. Forcing recalculation or silently using the previous controller would
test a different contract.

Windows stops with 513 passed, one failed and one skipped. Its coefficient and
saved-snapshot assertions already pass, but reading the exported C99 verification
sidecar returns FAIL. The original assertion does not print the report and the
uploaded artifacts contain only layout evidence. The exact original report
reason is unavailable; the subsequent independent reproduction below establishes
a concrete product defect without claiming unique retrospective attribution.

## Independent byte-level reproduction

The verification report hashes the UTF-8 LF header string. The original atomic writer
uses a text temporary file without an explicit newline policy. Windows translates
LF to CRLF; the reader correctly hashes the actual file bytes and rejects that
different digest. This can invalidate a genuine compiled numerical PASS.

An independent experiment uses the real generated 2P2Z controller and GCC 14.2,
changing only the temporary-file text newline profile to Windows CRLF. Both
61543.2 Hz (the actual one-decimal GUI value after the test entry) and 61543.21 Hz
are tested. LF output gives compiled/report/read PASS. CRLF output gives compiled
PASS, sidecar PASS, and reader FAIL with “Header changed after verification”.
The response error is approximately 3.6e-8; the existing 2e-5 scaled numerical
budget is unchanged. A genuinely inaccurate controller still fails that budget.

The repair writes exact UTF-8 bytes for the pending header, compiler probe,
final header and sidecar. The probe is checked as bytes, not newline-normalized
text, so a line-ending-only mutation during verification is detected. Failed or
unavailable verification continues to leave a compile-blocked diagnostic,
and interrupted writes or post-export tampering cannot authorize a stale PASS.

## Focused acceptance

The fixed candidate (`82eb8e4` plus `940d1de`) passes 67 targeted tests in 98.89 s,
with zero failures or skips. Independent real-GCC review passes nine vectors and
18 export safety-gate cases. Separate actual-Qt review passes 18 cases, including
all 11 repository controller contracts and seven independent boundary probes.
All 69 original controller-snapshot assertions remain; editor/controller product
code and wait deadlines are unchanged.

Both legacy and current snapshots are exercised with ordinary and queued edits.
The second save/reload is followed by an actual export into a new, previously
nonexistent directory; an old PASS artifact cannot mask a no-op action. The replay
stores authoritative sample time. Its exported rate is checked exactly against
`1 / reloaded.controller.sample_time_s`, rather than incorrectly requiring a
binary reciprocal round-trip to equal the original decimal rate. The added test's
first expectation mistake produced four preserved failures before this correction;
it was not a failed compiler export or a changed controller period.

The native preflight now includes the complete verified-C99 delivery module
before the unchanged full test and frozen verification gates. Both publication
jobs still require Linux and both native builds and remain explicitly paused.
Native run `39340500559` on the previous `a107797` source also stops at the old
snapshot/export checks: Windows 518 passed / 1 failed / 1 skipped; macOS 518
passed / 1 failed / 2 skipped. Neither failed native run reached freezing.

The earlier exact `52a874375979124458942785a960813ec70dd527` source (tree-equivalent
to `a107797`) completed its separate installed-wheel/Linux-frozen qualification:
304 installed renderer cases, 76 MCP cases, both public validation APIs, and six
workspaces in both actual schema-4 proofs passed. All 798 tracked hashes, 627
wheel/source files, 378 frozen product modules and 17 resources were checked.
There were 96 CJK-glyph warnings and six PyInstaller warning lines, not a
warning-free result. This earlier artifact pass is not reassigned to the changed
C99 writer; refreshed repaired-source artifacts remain required.

The final integrated export/snapshot/publication selection passes all 60 cases
in 96.04 s (196 existing CJK-glyph warnings). Its three repaired files exactly
match the reviewed candidate. An earlier integrated run preserves 66 passes and
four failures from the new reciprocal-rate assertion; all ten packaging checks,
including actual startup and isolated-wheel validation, passed in that run.
Only that test assertion changed afterward; its successful packaging checks are
not presented as a successful aggregate run. Production C99 numerical validation,
raw rendering and readback functions are AST-identical to the prior source;
only atomic UTF-8 output and verified-export probe byte handling change.

## Scope

This is a host-export identity defect and a test completion-boundary correction.
It does not change coefficients, sample periods, filter equations, numerical
tolerances, physical-model coverage or hardware qualification. Existing failed
runs remain failures. Fresh native full-suite, compiled-export and actual frozen
proofs are required for the repaired source. Formal publication remains paused.

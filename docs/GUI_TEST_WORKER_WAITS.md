# Real event-loop waits for GUI worker tests

## Preserved failed qualification

The installed wheel built from `74deceae92abd93e82a240c3f7416b6b0741897d`
passes its production schema-4 self-test across six workspaces. The following
focused suite then stops at **152 passed / 1 failed**, 378.942 s. The failing
`test_empty_advanced_physics_input_rejects_recalculate` sets a valid T11 QR case
and waits 90 seconds, but its stage job remains active. It has not yet performed
the empty-input action, so the failure does not establish an invalid-input
protection defect. Later MCP, public-API, Linux frozen and final provenance stages
are not run. The original failure, source identity and unchanged bytes are kept.

## Diagnosis without changing the physics or deadline

The installed original case passes when run alone (24.00 s in one uninstrumented
control); that isolated pass does not replace the failed complete run. Instrumented
real QR execution also shows actual Radau integration/root-grid progress and
normal GUI heartbeats, followed by result/finished/release and zero remaining
pool jobs. Instrumentation can affect scheduling and is labeled separately.

An independent small Qt 6.11.2 experiment uses no power-stage imports. Within the
same 1.25-second interval, a cooperative Python QRunnable obtains approximately
0.012 seconds of thread CPU under repeated `QTest.qWait(10)`, versus 0.605–0.613
seconds under a real `QEventLoop.exec()` with 10 ms status polling. A busy Python
worker obtains 0.427 versus 1.258 seconds. Responsive GUI heartbeats alone therefore
do not show that a Python numerical worker is receiving normal execution time.

In the actual installed QR test, replacing only the external diagnostic waiting
strategy with the real event loop gives a 1.58-second pass. Its QR wait takes
1.219 seconds wall / 1.280 seconds process CPU; the original 90-second deadline,
real solver, returned result, subsequent empty-input rejection, and zero-job
checks are unchanged. The diagnostic confirms it patches pytest's actual module;
an earlier comparison that did not bind that module is excluded from evidence.
The original complete-run CPU/stack was not captured, so these experiments do not
retroactively reconstruct its entire state.

## Regression contract

The corrected test waiter must use the actual Qt event loop with the original
bounded deadline. It must retain pending stage/loop timers and jobs as blockers,
allow queued completion callbacks to create follow-on work, stop its own timer,
and evaluate its state predicate on the calling Python stack rather than inside
a Qt callback. The timer only quits each short event-loop slice; exceptions
therefore propagate directly to the caller. A never-completing job must
still fail at the requested deadline. Invalid/deleted Qt objects must not cause
an exception swallowed inside a callback to leave a nested event loop running.
No time limit is increased, no numerical solver is replaced, and no product
worker, controller, converter or report behavior is changed.

Candidate `cc5ee20c7fd94fe031d75bff182091194459ae73` passes the complete pending-input
module: 32 cases in 10.72 seconds, with the real QR/empty-input case at 1.381 seconds.
Independent review confirms all 23 pre-existing functions except the waiter are
AST-identical to the original installed source. Six semantic probes cover idle
state, queued completion, timer chains, follow-on work, deadline failure and
invalid Qt-object propagation. Three never-completing work types retain their
pending state and fail at the requested short deadline; a real deleted timer's
RuntimeError returns to the caller rather than hanging the loop.

A separate real FunctionWorker counterexample uses the same 400 ms deadline:
the original waiter twice times out after 40/200 progress steps; the candidate
twice completes all 200 steps, result and finished callbacks in 21–22 ms. This
is a scheduler regression, not replacement converter physics.

Source-scoped installed-wheel and native qualification must still pass on the
final integrated candidate. Historical whole-run and artifact passes retain their original
source identity; the failed refresh is never presented as completed. Native
Windows/macOS, hardware and TI executable evidence remain separate, and formal
publication remains paused.

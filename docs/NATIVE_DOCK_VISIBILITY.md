# Whole-control reachability in the LLC parameter dock

## Observed native failures

macOS CI [run 39331097949, job 118329179908](https://github.com/yangshuai2022-star/power-design-toolkit/actions/runs/39331097949/job/118329179908)
on remote source `6323518d412306cbc7a6d0dc7aa60497ed76da18` failed the 10 pt
parameter-dock test after 219 passes and two skips. The reported failing
`QDoubleSpinBox` bounds were `(93, 583, 134, 34)`. The old diagnostic did not
identify the field, mode, viewport, scrollbar value/range, visibility, or input
method rectangle. It cannot establish which individual field failed or whether
that exact failure had a second cause.

The Windows job in the same run, [job 118329179860](https://github.com/yangshuai2022-star/power-design-toolkit/actions/runs/39331097949/job/118329179860),
also failed this 10 pt assertion after 220 passes and one skip. Its bounds were
`(91, 589, 140, 32)` and the subprocess exited normally with assertion status 1.
The preceding Windows crash/startup failure did not recur in that run. Its old
log has the same field-identity and scrollbar-state evidence gap.

Both jobs crossed the three **9 pt** desktop workflows. The downloaded macOS
artifact contains those successful workflows at three screen sizes. Those screenshots are not evidence that the 10 or 12 pt tests
passed. The 12 pt test had not run when fail-fast stopped either job.

## Verified Qt behavior

[Qt 6.11.2 QScrollArea source, lines 415–443](https://github.com/qt/qtbase/blob/v6.11.2/src/widgets/widgets/qscrollarea.cpp#L415-L443)
shows that `ensureWidgetVisible()` can use `ImCursorRectangle` instead of the
widget's outer rectangle. It returns immediately if that smaller rectangle is
already inside the viewport. Its default margin is applied only after this
check, so increasing that margin would not fix this case.

Real Qt 6.11.2 spin boxes reproduce the mismatch: with a visible insertion cursor
and the outer bottom border clipped, `ensureWidgetVisible()` leaves the scrollbar
unchanged. Processing further layout/events does not expose the border. This
holds both with and without focus. It does not require hidden controls or a
substituted size hint. The original test requested cursor visibility but asserted
whole-widget visibility.

The revised test scrolls to the actual outer rectangle's center with half-size
margins using `ensureVisible()`. One additional pixel covers its strict edge
comparison for both odd and even sizes. This only changes the test's real
scrollbar position. It does not resize, hide, reparent, or restyle product widgets.
No production file, font size, solver, or release workflow changes.

## Gates and evidence

- Keep 10 and 12 pt, normal and integrated modes, all pages, frame/minimum-size
  checks, zero horizontal scrolling, full control containment, and disposal.
- Explicitly cover all 50 parameter inputs/actions, including disabled controls,
  topology/device selectors, Copy Auto, and both reference-example buttons.
  Compare the manifest with the actual child-control inventory; do not filter
  controls out based on current visibility.
- Require `isVisible`, non-hidden state, visibility to the content parent, and
  the entire visible region, in addition to the existing viewport containment.
- Deterministic tests use real spin boxes at 32, 34 and 35 px with verified focus
  states. They reject clipped top/bottom edges, reject hidden and oversized
  controls, accept visible disabled controls, and verify whole-control scrolling.
- Save `llc-1280x720-10pt-dock.json` and `...-12pt-dock.json` to
  `LLC_LAYOUT_EVIDENCE_DIR` (the subprocess's temporary directory otherwise).
  Records identify the control and mode and capture before-scroll, immediately
  scrolled, and event-settled geometry, cursor rectangles, focus, visibility,
  content/viewport sizes, and horizontal/vertical ranges and values.
- Save per-mode screenshots. An assertion failure also retains the failing
  control's state, all controls' exit states, and a failure screenshot before
  disposal. Existing CI artifact upload already runs on failure.

## Validation boundary

Local Linux/Qt 6.11.2 focused tests passed: two font-size workflows and six
adversarial spin-box cases. Each font workflow checked 50 controls in both modes.
The 24 existing three-screen desktop, integrated-magnetics, and offscreen-font
regression cases also passed. A deliberate hidden-field injection into the real
LLC window failed at
`fields.vbus_nom_v`, with `completed: false`, its hidden state, and a retained
failure screenshot and JSON. The gate therefore does not turn hidden content
into success merely because its stored rectangle fits.

This verifies the test API mismatch and the replacement's acceptance boundary.
It does **not** retroactively prove the exact cause of the earlier macOS or Windows failures.
The exact revised source must still pass the real macOS/Windows preflight; new
10/12 pt diagnostics will identify any remaining native failure. Formal release
remains paused.

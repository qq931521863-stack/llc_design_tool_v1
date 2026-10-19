# Native large-font LLC window layout

## Failure and minimal correction

On main `e9a1199`, actual macOS CI passes the complete ordinary 9 pt three-screen
flows and the 10 pt normal/integrated dock checks. The following 12 pt test fails
at its first window containment assertion: the 1280×720 screen cannot contain
its 1278×740 frame. PingFang SC remains 12 pt; the minimum client size is
1274×736. The controls have not yet been traversed, so this is not the older
input-cursor versus whole-widget scrolling mismatch.

The tab stack includes its hidden pages' minima. The Two-Stage candidate page is
the tallest because its search JSON, ranking JSON, candidate table and details
editor each impose a native scroll-area minimum. The production correction
places the two independent JSON editors side by side. It preserves their original
160/95 maximum heights, actual native minima, font size, labels/buddies, exact
values, signals, validation and all Run/Cancel/Apply/report controls. There is no
new solver or change to candidate evaluation, selection or provenance.

## Reproduction and acceptance

A controlled Linux Qt 6.11.2 experiment changes only scrollbar style metrics to
18 px extent / 34 px minimum slider. These are inferred, not directly measured
raw style values: the passed native 10 pt run has a frame-less 265 px dock and
247 px viewport, implying an 18 px scrollbar. The earlier native page records an
88 px scroll-area minimum. The Qt 6.11.2 [scrollbar size-hint formula](https://github.com/qt/qtbase/blob/v6.11.2/src/widgets/widgets/qscrollbar.cpp#L426-L439)
and [scroll-area minimum formula](https://github.com/qt/qtbase/blob/v6.11.2/src/widgets/widgets/qabstractscrollarea.cpp#L1310-L1323)
give 88 = 3 × 18 + 34. The controlled profile is a diagnostic model of those
observations, not a substitute for actual macOS font execution; the new JSON
records raw style values for direct verification.
The old page forces root minimum 1233×745 and frame 1252×749; after the reflow,
they are 1233×679 and 1252×683. Width is unchanged. Every native-font 10/12 pt
workflow remains, as do all complete dock-control, frame and disposal checks.

The added regression also checks both editors with real keyboard JSON edits,
long lines and vertical scrolling, exact options restoration, all candidate
controls' complete visible regions and one label buddy per editor. Diagnostics
record the raw style metrics, toolbar/status/tab/page minima and editor geometry
on success or failure. No widget size hint is replaced in the experiment.

Independent product exercises cover actual TD calculation, mouse row selection
and Apply, changed project parameters, save, waveform-source report, reload of
historical results, unavailable stale Apply/report, status scrolling, tab identity,
close/reopen and final destruction. The corrected source still must complete
native full regression and actual frozen executable proof before native acceptance.

## Larger-font waveform viewing boundary

An additional 12 pt controlled-style run has a 173 px waveform viewport while a
readable individual plot is 189 px high. Its entire plot cannot be displayed
at once at that compact size. The original ordinary-size full-plot visibility
assertion is preserved and was not relaxed to make this diagnostic pass.
Real wheel scrolling reaches both ends; checked scrollbar positions expose
content intervals [0,173), [100,273) and [227,400),
covering the full 400 px canvas with overlap and no horizontal clipping. All 52
signal arrays and request identity remain unchanged, and the normal export
produces complete CSV, metadata, figures, schematic and report files. The CSV
contains 2048 rows × 53 columns; time and all 52 signals round-trip equal every
original sample. Thus this
larger-font view requires vertical scrolling; it is not evidence that every
font/screen combination displays a complete plot simultaneously.

## Windows evidence from the same native run

The Windows job on `e9a1199` completes both 10/12 pt normal/integrated workflows,
100 whole-visible controls at each size. Actual Microsoft YaHei UI 12 pt resolves
to height 20; the maximized frame is 1280×720, minimum 1243×637. Its later red is
an unrelated test-side implicit cp1252 read of an already-successful UTF-8 Chinese
report. Thus the macOS minimum-size failure is not generalized to Windows, and
the Windows larger-font result is not generalized to complete native qualification.

## Qualification boundary

The native failed run and its screenshots remain recorded. Controlled Linux
success does not establish native macOS/Windows full-suite or frozen success.
Earlier full/long, installed-wheel and frozen results retain their own source
hashes in the validation report. Physical-model, TI executable and hardware
validation boundaries are unchanged. Formal publication remains paused.

# Windows offscreen font and desktop-boundary repair

## Observed failure and scope

Windows CI run `39326573293`, job `118313869093`, on remote source
`3f2d1a48e879f0aa04f9859004722fefd96890a2` reached Qt 6.11.2, QApplication,
and a 1280×720 / 96 DPI / DPR 1 screen. The first normal-window frame containment
assertion failed. Qt also reported that its `PySide6/lib/fonts` directory did not
exist. That log did not contain the actual frame or resolved font, so it does
**not** establish that the missing font database was the only Windows failure.
This repair must still pass on the real Windows runner.

## Qt mechanism

These upstream sources explain why native Windows font availability is not
inherited by its offscreen platform:

- [Qt 6.11.2 offscreen integration](https://github.com/qt/qtbase/blob/v6.11.2/src/plugins/platforms/offscreen/qoffscreenintegration.cpp): Windows offscreen constructs `QFreeTypeFontDatabase`; its default theme uses a 9 pt font.
- [Qt 6.11.2 FreeType discovery](https://github.com/qt/qtbase/blob/v6.11.2/src/gui/text/freetype/qfreetypefontdatabase.cpp): a missing font directory returns without populating the database. Directory discovery includes TTF/OTF but omits TTC collections.
- [Qt 6.11.2 font directory](https://github.com/qt/qtbase/blob/v6.11.2/src/gui/text/qplatformfontdatabase.cpp): `QT_QPA_FONTDIR` precedes the default Qt library font directory.
- [Qt public font-loading API](https://doc.qt.io/qt-6/qfontdatabase.html#addApplicationFont): `addApplicationFont` supports TrueType, TrueType collections and OpenType, returning a failure ID when loading fails.
- [Qt standard directories](https://doc.qt.io/qt-6/qstandardpaths.html#StandardLocation-enum): `FontsLocation` identifies installed font directories.

## Changes

The shared theme startup now registers installed `.ttf`, `.ttc` and `.otf` files
from Qt's standard font directories, **only on Windows with the offscreen QPA**.
Registration is cached per QApplication; no family or point size is assigned by
this helper. The existing per-language font stack still chooses the application
font. Native Windows, macOS and Linux paths are unchanged. No fonts are downloaded,
copied into the project, or bundled. An empty/unloadable inventory fails explicitly.

The Windows CI job also supplies its system Fonts directory through
`QT_QPA_FONTDIR`, providing the ordinary fonts to Qt-only tests that do not start
the shared application theme. The application helper additionally handles TTC
collections for source and frozen GUI consumers. Font loading is an offscreen
runtime repair, not a claim of native-compositor qualification.

The desktop test now rejects unresolved fonts and missing Chinese/Latin sample
glyphs and confirms that startup preserved the original point size. Font,
normal-window geometry and screenshots are retained **before** the first frame
assertion; the original full workflow and all prior assertions remain intact.
The CI job preserves these files even on failure. All three screen-config callers
now use a config basename in its own working directory, avoiding Qt's colon and
semicolon option separators in Windows paths. The existing 10/12 pt and four-screen
launcher tests are now also in the native preflight; formal publication stays
paused.

## Controlled evidence and its limits

On Linux with the same Qt 6.11.2, a 1280×720 screen, 96 DPI and DPR 1:

| Font inventory | Resolved font | English sample width | Minimum window | Normal frame |
| --- | --- | ---: | --- | --- |
| Normal installed fonts | Noto Sans CJK SC, 9 pt | 121 px | 1071×623 | (0, 0, 1252, 660) |
| Isolated empty fontconfig | Empty family, invalid resolved point size | 240 px | 1307×595 | (0, 0, 1311, 660) |

The empty-font counterexample reproduces the same horizontal containment failure
without changing any widget constraint. Restoring real fonts removes that
counterexample. Font-family enumeration alone is insufficient: empty fontconfig
still advertises three generic aliases, despite having no usable glyphs.

The new executable registration test starts from an empty Linux font database,
exercises the Windows-only helper through an explicit platform/discovery seam,
and registers real installed TTC files with Qt. It restores readable Chinese and
Latin glyphs at the unchanged 9 pt size. Four TTC files registered in 45 ms in one
local run; a second theme setup made zero registration calls (0.073 ms total).
These are local measurements, **not Windows timing guarantees**.

An independent controlled run also registered real TTF/TTC files, verified all 12
LLC pages and normal/maximized frame containment at 1280×720, 1366×768 and 1440×900,
and confirmed the unchanged 9 pt font. Neither the seam-based test nor the Linux
screenshots replace a genuine Windows/macOS or frozen-binary run.

Local focused checks also passed the font/bootstrap regressions, unchanged
10/12 pt desktop constraints and publication-pause contracts (22 tests). The
full LLC three-screen flow still executes real waveform workers, digital control,
nominal TD candidate validation, report generation and project save. The restored
font inventory does not relax any of those electrical or UI acceptance gates.
The final three-screen LLC workflows and four-screen launcher interactions
passed together: 13 tests in 257.28 s. These are focused Linux/offscreen results;
real Windows qualification of this change is still pending.

## Actual native 9 pt follow-up

Run `39331097949` on `6323518` completes all three ordinary 9 pt desktop flows
on both native platforms. Windows resolves Microsoft YaHei UI with no missing
sample glyphs, registers 147 installed fonts in 0.093–0.109 s, and has a normal
1280×720-screen frame of 1252×660 with minimum 1091×607. macOS resolves PingFang SC
and fits a 1252×703 frame with minimum 1122×699. This closes the previously observed
Windows first-frame failure for these exact tests; it is not general native,
large-font, chart-font or frozen-package qualification. Both native preflights
subsequently stop at a 10 pt narrow-dock whole-widget scrolling assertion, before
full tests/freezing. Its test helper targets an input cursor rather than the
complete spinbox; independent real-Qt counterexamples and the test-only repair
are recorded in the main validation report. Publication remains paused.

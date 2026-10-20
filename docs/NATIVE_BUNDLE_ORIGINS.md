# Frozen module origins: macOS Resources cross-links

The native macOS build of `5247ac475098affa3aadffc92fc79ad74094cbce`
passed its full test suite and PyInstaller build but failed the frozen self-test
at the integrated-magnetics module-origin check. The old check resolved each
virtual module path, then accepted only a descendant of `_MEIPASS`.

PyInstaller 6.22.3 places a macOS application's `_MEIPASS` in
`Contents/Frameworks`, and cross-links data-only package directories to
`Contents/Resources`. Pure Python module `__file__` paths can be virtual and
need not exist. Resolving such a legitimate path can therefore leave Frameworks
while remaining inside the same application. See the official
[importer](https://github.com/pyinstaller/pyinstaller/blob/v6.22.3/PyInstaller/loader/pyimod02_importers.py)
and [bundle layout](https://github.com/pyinstaller/pyinstaller/blob/v6.22.3/PyInstaller/building/osx.py).

The shared self-test helper now requires lexical ownership below `_MEIPASS`
and resolved ownership below either that root or the same executable-anchored
macOS Resources root. It rejects parent traversal, sibling/prefix lookalikes,
external imports, escaping package/Resources/Frameworks links, and unrelated
executables. An alias of the whole application preserves its canonical identity.
Raw and resolved origins are logged before the gate. No model self-test,
closed-loop tolerance, required case or publication gate has been removed.

Validation: 38 focused filesystem and existing release-verification cases pass,
including the real symlink layout and virtual paths. A separate review found
and corrected two initial escaping-link counterexamples and checked the whole-app
alias case. These are local contract tests, not native macOS execution.

The failed native run did not log the actual origin paths. The new native
qualification must confirm the reported paths and complete the unchanged real
GUI/model self-tests before a macOS package can be accepted. Publication remains
paused; this repair alone is not a release-ready verdict.

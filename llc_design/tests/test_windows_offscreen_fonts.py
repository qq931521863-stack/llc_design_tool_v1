"""Windows offscreen must use real system fonts, including TTC collections."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from llc_design.i18n import fonts


@pytest.mark.parametrize("platform,qpa", [("linux", "offscreen"), ("darwin", "offscreen"),
                                          ("win32", "windows")])
def test_other_platforms_do_not_register_fonts(monkeypatch, platform, qpa):
    monkeypatch.setattr(fonts, "sys", SimpleNamespace(platform=platform))
    app = SimpleNamespace(platformName=lambda: qpa)
    assert fonts.load_windows_offscreen_fonts(app) == ()
    assert not hasattr(app, "_power_design_offscreen_font_files")


def test_system_font_files_register_once_without_changing_font(monkeypatch, tmp_path):
    core = pytest.importorskip("PySide6.QtCore")
    gui = pytest.importorskip("PySide6.QtGui")
    paths = [tmp_path / name for name in ("regular.ttf", "collection.TTC", "other.otf", "invalid.ttf")]
    for path in (*paths, tmp_path / "ignored.txt"):
        path.write_bytes(b"test-discovery")
    (tmp_path / "not-a-font.ttf").mkdir()
    monkeypatch.setattr(fonts, "sys", SimpleNamespace(platform="win32"))
    monkeypatch.setattr(core.QStandardPaths, "standardLocations", lambda _: [str(tmp_path)] * 2)
    calls = []

    def register(path):
        calls.append(path)
        return -1 if path.endswith("invalid.ttf") else len(calls)

    monkeypatch.setattr(gui.QFontDatabase, "addApplicationFont", register)
    # No setFont method: font discovery must not change family/point size.
    app = SimpleNamespace(platformName=lambda: "offscreen")
    registered = fonts.load_windows_offscreen_fonts(app)
    assert set(registered) == {str(path) for path in paths if path.name != "invalid.ttf"}
    assert set(calls) == {str(path) for path in paths}
    assert len(calls) == len(paths)
    assert fonts.load_windows_offscreen_fonts(app) is registered
    assert len(calls) == len(paths)


@pytest.mark.parametrize("invalid_file", [False, True])
def test_missing_or_unreadable_fonts_fail_explicitly(monkeypatch, tmp_path, invalid_file):
    core = pytest.importorskip("PySide6.QtCore")
    gui = pytest.importorskip("PySide6.QtGui")
    if invalid_file:
        (tmp_path / "bad.ttc").write_bytes(b"invalid-font")
    monkeypatch.setattr(fonts, "sys", SimpleNamespace(platform="win32"))
    monkeypatch.setattr(core.QStandardPaths, "standardLocations", lambda _: [str(tmp_path)])
    monkeypatch.setattr(gui.QFontDatabase, "addApplicationFont", lambda _: -1)
    app = SimpleNamespace(platformName=lambda: "offscreen")
    with pytest.raises(RuntimeError, match="could not load any installed fonts"):
        fonts.load_windows_offscreen_fonts(app)
    assert not hasattr(app, "_power_design_offscreen_font_files")


def test_real_ttc_registration_restores_glyphs_without_resizing(tmp_path):
    """Linux isolates fontconfig to exercise real Qt loading; Windows is native."""
    pytest.importorskip("PySide6")
    root = Path(__file__).resolve().parents[2]
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", PYTHONPATH=str(root),
               XDG_CACHE_HOME=str(tmp_path / "cache"))
    if sys.platform == "linux":
        cjk = Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc")
        if not cjk.is_file():
            pytest.skip("isolated Linux registration probe requires installed Noto CJK TTC")
        config = tmp_path / "empty-fonts.conf"
        config.write_text('<fontconfig><dir>' + str(tmp_path / "empty") + '</dir></fontconfig>')
        env["FONTCONFIG_FILE"] = str(config)
    elif sys.platform != "win32":
        pytest.skip("Windows offscreen registration has no macOS branch")
    result = subprocess.run([sys.executable, "-u", "-c", r'''
import json, sys, time
from types import SimpleNamespace
from PySide6.QtCore import QStandardPaths
from PySide6.QtGui import QFontDatabase, QFontInfo, QFontMetrics
from PySide6.QtWidgets import QApplication
from llc_design.gui import theme
from llc_design.i18n import fonts
app = QApplication([])
point_size = app.font().pointSizeF()
if sys.platform == "linux":
    assert not QFontMetrics(app.font()).inFontUcs4(ord("电"))
    fonts.sys = SimpleNamespace(platform="win32")
    QStandardPaths.standardLocations = staticmethod(lambda _: ["/usr/share/fonts/opentype/noto"])
calls = []
register = QFontDatabase.addApplicationFont
def counted(path):
    calls.append(path)
    return register(path)
QFontDatabase.addApplicationFont = staticmethod(counted)
start = time.monotonic()
theme.apply_app_theme(app)
first_seconds = time.monotonic() - start
first_count = len(calls)
start = time.monotonic()
theme.apply_app_theme(app)
repeat_seconds = time.monotonic() - start
assert len(calls) == first_count > 0
fonts.apply_qt_font(app, "zh-Hans")
font = app.font()
info, metrics = QFontInfo(font), QFontMetrics(font)
assert font.pointSizeF() == point_size
assert info.family() and info.pointSizeF() > 0
missing = [c for c in "Power Stage Designer 电源设计工具箱波形控制参数" if not metrics.inFontUcs4(ord(c))]
assert not missing, missing
print(json.dumps({"family": info.family(), "point_size": font.pointSizeF(),
    "resolved_point_size": info.pointSizeF(), "registered_files": first_count,
    "repeated_registrations": len(calls) - first_count, "missing_glyphs": missing,
    "first_seconds": first_seconds, "repeat_seconds": repeat_seconds}), flush=True)
'''], env=env, cwd=tmp_path, capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=60, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
    data = json.loads(result.stdout.splitlines()[-1])
    assert data["registered_files"] > 0 and data["repeated_registrations"] == 0
    assert data["missing_glyphs"] == []
    print(result.stdout)

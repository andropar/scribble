"""The reduced Wayland mode: session detection and the glue around the desktop's own tools
(``grim``, ``wl-copy``), exercised with stand-in executables. The mode itself is not covered
by the desktop tests, which need X11 or Windows."""

import os
import stat
import sys
from pathlib import Path

import pytest
from PySide6.QtCore import QRect
from PySide6.QtGui import QColor, QGuiApplication, QImage

from scribble import clipboard, platform as platforms, screens
from scribble.screens import Display, WaylandGrabber

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX shell stand-ins")


def fake_tool(folder: Path, name: str, script: str) -> None:
    path = folder / name
    path.write_text("#!/bin/sh\n" + script + "\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


@pytest.fixture
def tools(tmp_path, monkeypatch):
    folder = tmp_path / "bin"
    folder.mkdir()
    monkeypatch.setenv("PATH", str(folder) + os.pathsep + os.environ["PATH"])
    return folder


def test_wayland_session_detection(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.setenv("XDG_SESSION_TYPE", "x11")
    assert not platforms.is_wayland_session()
    monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")
    assert platforms.is_wayland_session()

    # Overlays need XWayland: Qt is pointed at it unless the user chose a platform.
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.delenv("QT_QPA_PLATFORM", raising=False)
    platforms.prepare_environment()
    assert os.environ["QT_QPA_PLATFORM"] == "xcb"
    monkeypatch.setenv("QT_QPA_PLATFORM", "wayland")
    platforms.prepare_environment()
    assert os.environ["QT_QPA_PLATFORM"] == "wayland"


def test_wayland_platform_is_a_reduced_mode():
    from scribble.platform.x11 import WaylandPlatform
    from scribble.settings import HotkeyAction

    # No X connection needed for what the mode promises.
    platform = WaylandPlatform.__new__(WaylandPlatform)
    assert platform.name == "wayland" and not platform.can_record and not platform.tracks_input
    assert platform.input_state() is None
    hotkeys = platform.create_hotkeys()
    assert not hotkeys.available
    failures = hotkeys.set_hotkeys({"snapshot": HotkeyAction.SNAPSHOT.default_hotkey})
    assert "snapshot" in failures
    notes = " ".join(platform.notes())
    assert "scribble snapshot" in notes and "scribble toggle-draw" in notes and "X11 session" in notes


def test_screenshot_comes_from_the_desktop_tool_and_is_cut_per_display(qapp, tools, tmp_path):
    # A desktop of two 400×300 displays side by side at scale 2: grim delivers 1600×600.
    desktop = QImage(1600, 600, QImage.Format_RGB32)
    desktop.fill(QColor(200, 0, 0))
    for x in range(800, 1600):
        for y in range(0, 600, 60):
            desktop.setPixelColor(x, y, QColor(0, 0, 200))
    right = desktop.copy(800, 0, 800, 600)
    source = tmp_path / "desktop.png"
    desktop.save(str(source))
    calls = tmp_path / "calls"
    fake_tool(tools, "grim", 'echo x >> "{}"\ncp "{}" "$1"'.format(calls, source))

    class Screen:
        def virtualGeometry(self):
            return QRect(0, 0, 800, 300)

    left_display = Display(0, "left", Screen(), QRect(0, 0, 400, 300), 2.0, (0, 0, 800, 600))
    right_display = Display(1, "right", Screen(), QRect(400, 0, 400, 300), 2.0, (400, 0, 800, 600))
    grabber = WaylandGrabber()
    assert not grabber.continuous
    a, b = grabber.grab(left_display), grabber.grab(right_display)
    assert (a.width(), a.height()) == (800, 600) and (b.width(), b.height()) == (800, 600)
    assert a.pixelColor(400, 300) == QColor(200, 0, 0)
    assert b == right.convertToFormat(b.format())
    # Both displays of one Snapshot round come from a single screenshot.
    assert calls.read_text().count("x") == 1
    with pytest.raises(NotImplementedError):
        grabber.grab_array((0, 0, 10, 10))


def test_screenshot_failure_is_reported(qapp, tools, monkeypatch):
    fake_tool(tools, "grim", "exit 1")
    monkeypatch.setattr(screens, "portal_screenshot", lambda: None)
    monkeypatch.setattr(screens, "which", lambda *names: "grim" if "grim" in names else None)
    screen = QGuiApplication.primaryScreen()
    display = Display(0, "d", screen, screen.geometry(), 1.0, (0, 0, 800, 800))
    with pytest.raises(RuntimeError, match="No screenshot on this Wayland session"):
        WaylandGrabber().grab(display)


def test_plain_text_goes_through_wl_copy_on_wayland(qapp, tools, tmp_path, monkeypatch):
    received = tmp_path / "clipboard.txt"
    fake_tool(tools, "wl-copy", 'cat > "{}"\necho "$@" > "{}.args"'.format(received, received))
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")
    clipboard.write_text("dictated täxt")
    assert received.read_text(encoding="utf-8") == "dictated täxt"
    assert "text/plain" in Path(str(received) + ".args").read_text()

    # A failing wl-copy falls back to the X11 clipboard instead of losing the text.
    fake_tool(tools, "wl-copy", "exit 1")
    clipboard.write_text("second")
    assert QGuiApplication.clipboard().text() == "second"

    # On X11 sessions wl-copy is not used at all.
    fake_tool(tools, "wl-copy", 'echo used > "{}"'.format(received))
    monkeypatch.setenv("XDG_SESSION_TYPE", "x11")
    clipboard.write_text("third")
    assert QGuiApplication.clipboard().text() == "third" and received.read_text().strip() != "used"

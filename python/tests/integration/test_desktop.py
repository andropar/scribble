"""Tests against a real desktop session (X11 or Windows) with real synthetic input.

Run with ``pytest --desktop`` on a machine nobody is using: they move the pointer, press
keys and put windows on screen. CI runs them on Windows runners and under Xvfb.
Screenshots of what was on screen are written to ``$VOICE_ANNOTATE_ARTIFACTS``.
"""

import json
import os
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QGuiApplication, QPainter
from PySide6.QtWidgets import QWidget

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

import inject  # noqa: E402
from fakes import FakeAudio, pump, wait_until  # noqa: E402
from helpers import FakeTranscriber  # noqa: E402

from voice_annotate import clipboard, paths, platform as platforms, render, screens, sounds  # noqa: E402
from voice_annotate.clipboard import RecentsStore  # noqa: E402
from voice_annotate.core.models import AnnotationTool, CaptureMode  # noqa: E402
from voice_annotate.processor import RecentCapture  # noqa: E402
from voice_annotate.session import HUD_LEGEND_NOTE, Phase, SessionController  # noqa: E402
from voice_annotate.settings import AppSettings, ClipboardFormat, Hotkey  # noqa: E402

pytestmark = [
    pytest.mark.desktop,
    pytest.mark.skipif(sys.platform == "darwin", reason="no input injection backend for macOS"),
]

ARTIFACTS = Path(
    os.environ.get("VOICE_ANNOTATE_ARTIFACTS") or tempfile.mkdtemp(prefix="voice-annotate-artifacts-")
)
ARTIFACTS.mkdir(parents=True, exist_ok=True)
HYPER = ("ctrl", "alt", "shift")


class Transcriber(FakeTranscriber):
    class _Status:
        is_ready, title = True, "ready"

    status = _Status()

    def subscribe(self, listener):
        pass


class Env:
    def __init__(self) -> None:
        self.platform = platforms.current()
        self.grabber = screens.create_grabber(self.platform)
        self.inject = inject.create()
        self.displays = screens.displays()
        self.display = self.displays[0]

    def native(self, x: float, y: float):
        """Canvas point on the first display → native position for the injector."""
        d = self.display
        return int(round(d.native[0] + x * d.dpr)), int(round(d.native[1] + y * d.dpr))

    def move(self, x: float, y: float) -> None:
        self.inject.move(*self.native(x, y))

    def click(self, x: float, y: float) -> None:
        self.move(x, y)
        pump(0.15)
        self.inject.press()
        pump(0.12)
        self.inject.release()
        pump(0.15)

    def drag(self, points) -> None:
        self.move(*points[0])
        pump(0.15)
        self.inject.press()
        pump(0.1)
        for p in points[1:]:
            self.move(*p)
            pump(0.06)
        self.inject.release()
        pump(0.15)

    def snap(self, name: str):
        """Saves what is on the first display right now and returns the image."""
        image = self.grabber.grab(self.display)
        ARTIFACTS.mkdir(parents=True, exist_ok=True)
        render.write_png(image, ARTIFACTS / "{}_{}.png".format(self.platform.name, name))
        return image

    def pixel(self, image, x: float, y: float):
        c = image.pixelColor(int(round(x * self.display.dpr)), int(round(y * self.display.dpr)))
        return c.red(), c.green(), c.blue()


@pytest.fixture(scope="module")
def env(qapp):
    environment = Env()
    yield environment
    environment.inject.release_all()


class AppWindow(QWidget):
    """Stands in for the app the user is showing: a flat color that counts its clicks."""

    def __init__(self, color: str, x: int, y: int, w: int, h: int, display) -> None:
        super().__init__()
        self.clicks = []
        self._color = QColor(color)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Window)
        self.setWindowTitle("voice-annotate test target")
        self.setGeometry(display.geometry.x() + x, display.geometry.y() + y, w, h)

    def set_color(self, color: str) -> None:
        self._color = QColor(color)
        self.repaint()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), self._color)
        painter.end()

    def mousePressEvent(self, event) -> None:
        self.clicks.append((event.position().x(), event.position().y()))


@pytest.fixture
def target(env):
    window = AppWindow("#2e6fd0", 0, 0, 900, 650, env.display)
    window.show()
    window.raise_()
    window.activateWindow()
    pump(0.4)
    # A real click makes it the foreground window on every system (and, on Windows, lets
    # this process take the foreground later, as a user-started app could).
    env.click(880, 630)
    assert wait_until(lambda: window.isActiveWindow(), 5), "test window did not get focus"
    window.clicks.clear()
    pump(0.3)
    yield window
    window.close()
    pump(0.1)


_controllers = []


@pytest.fixture
def controller(env, tmp_path, monkeypatch):
    monkeypatch.setenv("VOICE_ANNOTATE_HOME", str(tmp_path / "home"))
    sounds.enabled = False
    c = SessionController(
        AppSettings(),
        RecentsStore(),
        Transcriber(start=0.2, step=0.2),
        env.platform,
        env.grabber,
        audio_factory=FakeAudio,
    )
    c.finished = []
    c.capture_finished.connect(c.finished.append)
    _controllers.append(c)
    yield c
    env.inject.release_all()
    if c.phase.is_capturing:
        c.cancel()
    wait_until(lambda: c.phase == Phase.IDLE, 20)
    c.wait_for_background()
    c.toast.hide()
    pump(0.2)


def close_to(actual, expected, tolerance=3) -> bool:
    return all(abs(a - e) <= tolerance for a, e in zip(actual, expected))


# MARK: the platform backend


def test_session_is_a_supported_desktop(env):
    assert env.platform.name in ("windows", "x11")
    assert type(env.grabber).__name__ == "MssGrabber" and env.grabber.continuous
    assert env.platform.can_record and env.platform.tracks_input
    d = env.display
    assert d.native[2] == round(d.geometry.width() * d.dpr) and d.native[3] == round(
        d.geometry.height() * d.dpr
    )
    sys.stdout.write(
        "\n[desktop] {} · {} display(s) · first {}x{} scale {} · compositing {}\n".format(
            env.platform.name,
            len(env.displays),
            d.geometry.width(),
            d.geometry.height(),
            d.dpr,
            env.platform.compositing(),
        )
    )


def test_grab_shows_what_is_on_screen(env, target):
    image = env.snap("grab")
    assert image.width() == env.display.native[2] and image.height() == env.display.native[3]
    # The blue test window covers (0,0)-(900,650) in canvas points.
    assert close_to(env.pixel(image, 450, 300), (0x2E, 0x6F, 0xD0), 6)
    assert close_to(env.pixel(image, 880, 630), (0x2E, 0x6F, 0xD0), 6)
    assert not close_to(env.pixel(image, 950, 700), (0x2E, 0x6F, 0xD0), 6)
    # The continuous grabber used for recording sees the same pixels.
    array = env.grabber.grab_array(env.display.native)
    b, g, r = (int(v) for v in array[int(300 * env.display.dpr), int(450 * env.display.dpr), :3])
    assert close_to((r, g, b), (0x2E, 0x6F, 0xD0), 6)


def test_global_hotkeys_fire_and_report_conflicts(env):
    backend = env.platform.create_hotkeys()
    assert backend.available
    fired = []
    backend.triggered.connect(fired.append)
    wanted = {
        "snapshot": Hotkey(HYPER, "S"),
        "finish": Hotkey(HYPER, "F"),
        "dictation": Hotkey(("ctrl", "alt"), "F9"),
    }
    try:
        assert backend.set_hotkeys(wanted) == {}
        env.inject.key("S", HYPER)
        assert wait_until(lambda: fired == ["snapshot"], 5), fired
        env.inject.key("F", HYPER)
        env.inject.key("F9", ("ctrl", "alt"))
        assert wait_until(lambda: fired == ["snapshot", "finish", "dictation"], 5), fired
        # The same key without the modifiers is not ours.
        env.inject.key("S", ("ctrl",))
        pump(0.5)
        assert len(fired) == 3

        # A combination that is taken is reported instead of silently not working.
        other = env.platform.create_hotkeys()
        try:
            failures = other.set_hotkeys(
                {"walkthrough": Hotkey(HYPER, "S"), "toggleDraw": Hotkey(HYPER, "D")}
            )
            assert list(failures) == ["walkthrough"] and "Ctrl+Alt+Shift+S" in failures["walkthrough"]
        finally:
            other.shutdown()

        # Suspended while a new shortcut is being recorded.
        backend.suspend()
        env.inject.key("S", HYPER)
        pump(0.5)
        assert len(fired) == 3
        backend.resume()
        pump(0.4)
        env.inject.key("S", HYPER)
        assert wait_until(lambda: len(fired) == 4, 5), fired
    finally:
        backend.shutdown()
        env.inject.release_all()


def test_pointer_and_modifier_state(env):
    env.move(321, 234)
    pump(0.2)
    state = env.platform.input_state()
    assert close_to((state.x, state.y), env.native(321, 234), 1)
    assert not state.left_down and not state.mods
    env.inject.key_down("ctrl")
    env.inject.key_down("alt")
    pump(0.2)
    assert env.platform.input_state().mods == frozenset({"ctrl", "alt"})
    env.inject.key_up("alt")
    env.inject.key_up("ctrl")
    env.inject.key_down("shift")
    pump(0.2)
    assert env.platform.input_state().mods == frozenset({"shift"})
    env.inject.key_up("shift")
    pump(0.2)
    assert env.platform.input_state().mods == frozenset()


# MARK: captures with real input


def test_snapshot_with_real_mouse_and_keyboard(env, target, controller):
    focused_before = env.platform.remember_focus()
    controller.start_snapshot()
    assert wait_until(lambda: controller.phase == Phase.SNAPSHOT and bool(controller.overlays), 10)
    overlay = controller.overlays[env.display.index]
    assert wait_until(lambda: overlay.isVisible() and controller.hud.isVisible(), 5)
    pump(0.5)
    # The overlay covers the whole display and shows the frozen screen.
    assert (overlay.width(), overlay.height()) == (
        env.display.geometry.width(),
        env.display.geometry.height(),
    )
    frozen = env.snap("snapshot_overlay")
    assert close_to(env.pixel(frozen, 450, 500), (0x2E, 0x6F, 0xD0), 8)
    # ...also after the real window underneath changed: it is a frozen screenshot.
    target.set_color("#d04a2e")
    pump(0.4)
    assert close_to(env.pixel(env.snap("snapshot_still_frozen"), 450, 500), (0x2E, 0x6F, 0xD0), 8)

    # Keys reach the overlay although our app was never activated by the user: O = ellipse.
    env.inject.key("O")
    assert wait_until(lambda: overlay.tool == AnnotationTool.ELLIPSE, 5), "overlay did not get the keyboard"
    env.drag([(200, 300), (260, 340), (400, 420)])
    assert wait_until(lambda: len(controller.session.annotations) == 1, 5)
    mark = controller.session.annotations[0]
    assert close_to(mark.points[0], (200, 300), 3) and close_to(mark.points[-1], (400, 420), 3)
    assert mark.number == 1

    # A click on the toolbar picks a tool without taking the keyboard away from the overlay.
    hud = controller.hud
    button = hud._tool_buttons[AnnotationTool.ARROW]
    center = button.mapToGlobal(button.rect().center())
    env.click(center.x() - env.display.geometry.x(), center.y() - env.display.geometry.y())
    assert wait_until(lambda: overlay.tool == AnnotationTool.ARROW, 5), "toolbar click was not received"
    env.drag([(500, 500), (650, 350)])
    assert wait_until(lambda: len(controller.session.annotations) == 2, 5)
    marked = env.snap("snapshot_marks")
    # The toolbar is still on top after drawing on the overlay.
    assert hud.isVisible()
    env.inject.key("Z", ("ctrl",))
    assert wait_until(lambda: len(controller.session.annotations) == 1, 5), "Ctrl+Z did not undo"
    env.drag([(500, 500), (650, 350)])
    assert wait_until(lambda: len(controller.session.annotations) == 2, 5)

    pump(0.4)
    env.inject.key("Return")
    assert wait_until(lambda: bool(controller.finished), 30), "Enter did not finish the capture"
    capture = controller.finished[0]
    assert capture is not None, controller.toast.detail
    md = Path(capture.markdown_path).read_text(encoding="utf-8")
    events = json.loads((Path(capture.folder) / "events.json").read_text(encoding="utf-8"))
    assert events["annotations"][0]["shape"] == "ellipse" and events["annotations"][1]["shape"] == "arrow"
    assert "#1 circled (" in md and "#2 arrow from (" in md and "so this" in md

    # The saved frame is the frozen screen with the marks burned in at the described pixels.
    frame = render.load_image(capture.frame_paths[0])
    scale = frame.width() / env.display.geometry.width()
    r, g, b = (lambda c: (c.red(), c.green(), c.blue()))(frame.pixelColor(int(200 * scale), int(360 * scale)))
    assert r > 180 and g < 120, "ellipse edge not in the frame: {}".format((r, g, b))
    r, g, b = (lambda c: (c.red(), c.green(), c.blue()))(frame.pixelColor(int(300 * scale), int(200 * scale)))
    assert close_to((r, g, b), (0x2E, 0x6F, 0xD0), 10), "frame does not show the frozen screen"

    # Overlays are gone, and the app that had the keyboard before has it again.
    pump(0.6)
    assert not controller.overlays and not hud.isVisible()
    assert wait_until(lambda: env.platform.remember_focus() == focused_before, 5), "focus was not handed back"
    after = env.snap("snapshot_after")
    assert close_to(env.pixel(after, 450, 500), (0xD0, 0x4A, 0x2E), 8)


def test_walkthrough_with_real_mouse_and_keyboard(env, target, controller):
    controller.settings.set("fadeSeconds", 6.0)
    controller.start_walkthrough()
    assert controller.phase == Phase.WALKTHROUGH
    overlay = controller.overlays[env.display.index]
    assert wait_until(lambda: not controller.hud.starting, 15), "recording did not start"
    pump(0.6)
    hud = controller.hud
    assert hud.isVisible()

    # The overlay is invisible and lets clicks through to the app underneath...
    idle = env.snap("walkthrough_idle")
    assert close_to(env.pixel(idle, 450, 500), (0x2E, 0x6F, 0xD0), 8)
    env.click(300, 400)
    assert wait_until(lambda: len(target.clicks) == 1, 5), "click did not pass through the overlay"
    # ...and the click is on the capture's timeline, in canvas coordinates.
    assert wait_until(lambda: len(controller.session.clicks) == 1, 5)
    assert close_to(controller.session.clicks[0].location, (300, 400), 2)
    target.set_color("#2ea86f")  # the click changed the app
    pump(1.2)

    # Holding the draw modifier turns the overlay into a canvas.
    for key in controller.settings.draw_modifier.mods:
        env.inject.key_down(key)
    assert wait_until(lambda: hud.drawing, 5), "draw modifier was not detected"
    pump(0.3)
    env.drag([(200, 300), (300, 330), (420, 300), (520, 360)])
    assert wait_until(lambda: len(controller.session.annotations) == 1, 5), "stroke did not reach the overlay"
    assert len(target.clicks) == 1, "the app underneath got the drawing click"
    assert len(controller.session.clicks) == 1
    for key in reversed(controller.settings.draw_modifier.mods):
        env.inject.key_up(key)
    assert wait_until(lambda: not hud.drawing, 5)
    pump(0.8)

    # The stroke is on the real screen (red pen, 4 px), so it is in what gets recorded.
    drawn = env.snap("walkthrough_stroke")
    stroke = controller.session.annotations[0]
    sx, sy = stroke.points[len(stroke.points) // 2]
    r, g, b = env.pixel(drawn, sx, sy)
    assert r > 180 and g < 120 and b < 120, "stroke is not visible on screen: {}".format((r, g, b))

    # Clicks pass through again after drawing.
    env.click(600, 500)
    assert wait_until(lambda: len(target.clicks) == 2, 5), "overlay kept the mouse after drawing"
    pump(1.0)

    controller.finish()
    assert controller.phase == Phase.PROCESSING
    assert wait_until(lambda: bool(controller.finished), 60)
    capture = controller.finished[0]
    assert capture is not None, controller.toast.detail
    from voice_annotate import processor

    processor.wait_for_background()
    folder = Path(capture.folder)
    md = (folder / "capture.md").read_text(encoding="utf-8")
    events = json.loads((folder / "events.json").read_text(encoding="utf-8"))
    reasons = [r for f in events["frames"] for r in f["reasons"]]
    # (The frame for the screen change itself is redundant next to the annotated frame of
    # the same view, and the selector drops it.)
    assert "start" in reasons and "annotation" in reasons, reasons
    assert "[click at (" in md and "drawing over (" in md
    assert (folder / "recording.mp4").is_file()

    # The keyframe chosen for the stroke shows the green app with the stroke burned in.
    annotated = next(f for f in events["frames"] if "annotation" in f["reasons"])
    frame = render.load_image(folder / annotated["file"])
    scale = frame.width() / env.display.geometry.width()
    c = frame.pixelColor(int(sx * scale), int(sy * scale))
    assert c.red() > 170 and c.green() < 130, "stroke missing in keyframe: {}".format(
        (c.red(), c.green(), c.blue())
    )
    c = frame.pixelColor(int(700 * scale), int(550 * scale))
    assert close_to((c.red(), c.green(), c.blue()), (0x2E, 0xA8, 0x6F), 20), "keyframe does not show the app"
    # The first frame shows the app before the click.
    first = render.load_image(folder / events["frames"][0]["file"])
    c = first.pixelColor(int(700 * scale), int(550 * scale))
    assert close_to((c.red(), c.green(), c.blue()), (0x2E, 0x6F, 0xD0), 20)

    if env.platform.can_exclude_from_capture and getattr(hud, "_excluded", False):
        # Windows: the toolbar is hidden from capture, so frames show the app underneath it.
        assert HUD_LEGEND_NOTE not in md
        c = first.pixelColor(int(450 * scale), int(22 * scale))
        assert close_to(
            (c.red(), c.green(), c.blue()), (0x2E, 0x6F, 0xD0), 20
        ), "toolbar leaked into the recording"
    else:
        assert HUD_LEGEND_NOTE in md


def test_walkthrough_toggle_draw_from_toolbar(env, target, controller):
    controller.start_walkthrough()
    assert wait_until(lambda: not controller.hud.starting, 15)
    pump(0.5)
    hud = controller.hud
    button = hud._draw
    center = button.mapToGlobal(button.rect().center())
    where = (center.x() - env.display.geometry.x(), center.y() - env.display.geometry.y())
    env.click(*where)
    assert wait_until(lambda: hud.drawing, 5), "toolbar toggle did not turn draw mode on"
    env.drag([(250, 450), (450, 520)])
    assert wait_until(lambda: len(controller.session.annotations) == 1, 5)
    assert not target.clicks
    # The toolbar still works while the overlay has the mouse.
    env.click(*where)
    assert wait_until(lambda: not hud.drawing, 5), "toolbar toggle did not turn draw mode off"
    env.click(300, 400)
    assert wait_until(lambda: len(target.clicks) == 1, 5)
    controller.cancel()


# MARK: the rest of the system


def test_clipboard_is_readable_by_other_programs(env, tmp_path):
    folder = tmp_path / "cap ture"  # a space, to check quoting
    folder.mkdir()
    md = folder / "capture.md"
    md.write_text("# Screen capture\n\nhällo wörld\n\nFolder: x\n", encoding="utf-8")
    frame = folder / "frame_01.png"
    image = render.new_image(40, 30)
    image.fill(QColor(200, 30, 30))
    render.write_png(image, frame)
    capture = RecentCapture(
        str(folder),
        datetime.now().astimezone(),
        CaptureMode.SNAPSHOT,
        1.0,
        str(md),
        [str(frame)],
        [str(frame)],
        2,
        0,
    )
    clipboard.write(capture, ClipboardFormat.UNIVERSAL)
    pump(0.3)

    def run(command):
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        # Our process serves the clipboard, so keep the event loop running meanwhile.
        assert wait_until(lambda: process.poll() is not None, 30), "clipboard reader timed out"
        return process.stdout.read().decode("utf-8", "replace").replace("\r\n", "\n")

    if sys.platform == "win32":
        ps = ["powershell", "-NoProfile", "-NonInteractive", "-Command"]
        utf8 = "[Console]::OutputEncoding = [Text.Encoding]::UTF8; "
        files = run(ps + [utf8 + "Get-Clipboard -Format FileDropList | ForEach-Object { $_.FullName }"])
        assert [Path(f).resolve() for f in files.strip().split("\n")] == [md.resolve(), frame.resolve()]
        text = run(ps + [utf8 + "Get-Clipboard -Raw"])
        assert "hällo wörld" in text and text.startswith("# Screen capture")
    else:
        targets = run(["xclip", "-selection", "clipboard", "-o", "-t", "TARGETS"])
        assert (
            "text/uri-list" in targets and "UTF8_STRING" in targets and clipboard.GNOME_FILES_MIME in targets
        )
        uris = run(["xclip", "-selection", "clipboard", "-o", "-t", "text/uri-list"]).strip().split("\n")
        assert [u.strip() for u in uris] == [md.as_uri(), frame.as_uri()]
        text = run(["xclip", "-selection", "clipboard", "-o", "-t", "UTF8_STRING"])
        assert text == md.read_text(encoding="utf-8")
        gnome = run(["xclip", "-selection", "clipboard", "-o", "-t", clipboard.GNOME_FILES_MIME])
        assert gnome.split("\n") == ["copy", md.as_uri(), frame.as_uri()]

    # A single image also travels as bitmap data.
    clipboard.write(capture, ClipboardFormat.IMAGES_ONLY)
    pump(0.3)
    if sys.platform == "win32":
        size = run(ps + ['$i = Get-Clipboard -Format Image; "$($i.Width)x$($i.Height)"'])
        assert size.strip() == "40x30"
    else:
        assert "image/png" in run(["xclip", "-selection", "clipboard", "-o", "-t", "TARGETS"])
    clipboard.write_text("plain dictation text")
    pump(0.3)
    if sys.platform == "win32":
        assert run(ps + ["Get-Clipboard -Raw"]).strip() == "plain dictation text"
    else:
        assert run(["xclip", "-selection", "clipboard", "-o"]) == "plain dictation text"


def test_launch_at_login_round_trip(env, tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    platform = env.platform
    was = platform.autostart_enabled()
    try:
        platform.set_autostart(True)
        assert platform.autostart_enabled()
        if sys.platform != "win32":
            entry = (tmp_path / "config" / "autostart" / "voice-annotate.desktop").read_text(encoding="utf-8")
            assert "Exec=" in entry and "voice_annotate" in entry and "Type=Application" in entry
        platform.set_autostart(False)
        assert not platform.autostart_enabled()
        platform.set_autostart(False)  # removing twice is fine
    finally:
        if sys.platform == "win32":
            platform.set_autostart(was)


def test_microphone_records_on_the_session_clock(env):
    from voice_annotate import audio

    if audio.input_device_name() is None:
        pytest.skip("no input device in this session")
    recorder = audio.AudioRecorder()
    levels = []
    recorder.on_level = levels.append
    started = paths.now()
    recorder.start()
    pump(1.5)
    result = recorder.stop()
    stopped = paths.now()
    assert 1.0 < result.duration < 2.2, result.duration
    assert started - 0.5 <= result.first_sample_time <= started + 0.6
    assert result.first_sample_time + result.duration <= stopped + 0.3
    assert levels, "no level updates"


# MARK: the real app, as a separate process


def trigger(command: str, home: Path) -> str:
    result = subprocess.run(
        [sys.executable, "-m", "voice_annotate", command],
        capture_output=True,
        text=True,
        timeout=30,
        env=dict(os.environ, VOICE_ANNOTATE_HOME=str(home)),
    )
    return (result.stdout + result.stderr).strip()


def test_real_app_end_to_end(env, target, tmp_path):
    """Starts the app the way a user would, then drives it with the real global hotkeys,
    the mouse and the command line triggers."""
    home = tmp_path / "home"
    home.mkdir()
    # The smallest model: this test is about the app, transcription has its own test.
    (home / "settings.json").write_text(
        json.dumps({"model": "tiny.en", "onboardingShown": True}), encoding="utf-8"
    )
    log = (
        open(ARTIFACTS / "{}_app.log".format(env.platform.name), "w", encoding="utf-8")
        if ARTIFACTS.is_dir()
        else None
    )
    app_env = dict(os.environ, VOICE_ANNOTATE_HOME=str(home))
    app_env.pop("QT_QPA_PLATFORM", None)
    process = subprocess.Popen(
        [sys.executable, "-m", "voice_annotate"], env=app_env, stdout=log, stderr=subprocess.STDOUT
    )
    try:
        assert wait_until(lambda: trigger("status", home) == "ok idle", 60, step=0.5), "app did not start"
        # A second start does not create a second instance.
        second = subprocess.run(
            [sys.executable, "-m", "voice_annotate"], env=app_env, capture_output=True, text=True, timeout=60
        )
        assert second.returncode == 0 and "already running" in second.stderr
        pump(1.0)

        # Snapshot by global hotkey, one mark by mouse, finish with Enter.
        target.raise_()
        target.activateWindow()
        pump(0.5)
        env.inject.key("S", HYPER)
        assert wait_until(
            lambda: trigger("status", home) == "ok snapshot", 20, step=0.3
        ), "hotkey did not start a Snapshot"
        pump(1.0)
        env.snap("app_snapshot")
        env.inject.key("R")
        pump(0.3)
        env.drag([(220, 320), (460, 470)])
        pump(0.4)
        env.snap("app_snapshot_mark")
        env.inject.key("Return")
        assert wait_until(
            lambda: trigger("status", home) == "ok idle", 90, step=0.5
        ), "Snapshot did not finish"
        captures = sorted((home / "captures").iterdir())
        assert len(captures) == 1
        md = (captures[0] / "capture.md").read_text(encoding="utf-8")
        assert "#1 box around (" in md and (captures[0] / "frame_01.png").is_file()
        events = json.loads((captures[0] / "events.json").read_text(encoding="utf-8"))
        assert close_to(events["annotations"][0]["points"][0], (220, 320), 3)
        pump(0.5)
        env.snap("app_toast")

        # The capture is on the clipboard of the session, written by the app's process.
        board = QGuiApplication.clipboard()
        assert wait_until(lambda: bool(board.mimeData().urls()), 10), "clipboard has no file"
        assert [Path(u.toLocalFile()).resolve() for u in board.mimeData().urls()] == [
            (captures[0] / "capture.md").resolve()
        ]
        assert "#1 box around (" in board.mimeData().text()

        # Walkthrough by command line trigger, finished by the global finish hotkey.
        assert trigger("walkthrough", home).startswith("ok")
        assert wait_until(lambda: trigger("status", home) == "ok walkthrough", 20, step=0.3)
        pump(2.0)
        env.click(300, 400)
        target.set_color("#8a2ed0")
        pump(2.0)
        env.snap("app_walkthrough")
        env.inject.key("F", HYPER)
        assert wait_until(
            lambda: trigger("status", home) == "ok idle", 120, step=0.5
        ), "Walkthrough did not finish"
        captures = sorted((home / "captures").iterdir())
        assert len(captures) == 2
        walkthrough = (captures[1] / "capture.md").read_text(encoding="utf-8")
        assert "keyframes from that recording" in walkthrough and "frame_01.png" in walkthrough
        assert "[click at (" in walkthrough

        # Cancel discards a capture; quit ends the process.
        env.inject.key("S", HYPER)
        assert wait_until(lambda: trigger("status", home) == "ok snapshot", 20, step=0.3)
        pump(0.5)
        env.inject.key("Escape")
        assert wait_until(lambda: trigger("status", home) == "ok idle", 20, step=0.3), "Esc did not cancel"
        assert len(list((home / "captures").iterdir())) == 2
        assert trigger("quit", home).startswith("ok")
        assert wait_until(lambda: process.poll() is not None, 20), "app did not quit"
        assert process.returncode == 0
    finally:
        if process.poll() is None:
            process.kill()
        if log is not None:
            log.close()

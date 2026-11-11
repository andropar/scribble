"""The capture state machine driven end to end, headless: fake screen, fake microphone,
scripted transcript, real overlays, real pipeline, real clipboard object."""

import json
import sys
from datetime import datetime
from pathlib import Path

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtTest import QTest

from scribble import clipboard, paths, sounds
from scribble.clipboard import RecentsStore
from scribble.core.models import AnnotationTool, CaptureMode
from scribble.session import HUD_LEGEND_NOTE, Phase, SessionController
from scribble.settings import AppSettings, ClipboardFormat, DrawModifier, HotkeyAction
from scribble.transcriber import Status
from scribble.ui.overlay import SnapshotOverlay, WalkthroughOverlay

sys.path.insert(0, str(Path(__file__).parent))
from fakes import FakeAudio, FakeGrabber, FakePlatform, pump, wait_until  # noqa: E402
from helpers import FakeTranscriber  # noqa: E402


class Transcriber(FakeTranscriber):
    """FakeTranscriber with the status interface the controller and the tray use."""

    status = Status("ready", device="cpu", model="small.en")

    def subscribe(self, listener):
        pass


# Controllers stay referenced for the whole run: a QObject must not be garbage collected on
# whichever worker thread happens to drop the last reference.
_controllers = []


@pytest.fixture
def app(qapp, tmp_path, monkeypatch):
    monkeypatch.setenv("SCRIBBLE_HOME", str(tmp_path / "home"))
    sounds.enabled = False
    settings = AppSettings()
    platform, grabber = FakePlatform(), FakeGrabber()
    controller = SessionController(
        settings,
        RecentsStore(),
        Transcriber(start=0.2, step=0.15),
        platform,
        grabber,
        audio_factory=FakeAudio,
    )
    finished = []
    controller.capture_finished.connect(finished.append)
    controller.finished = finished
    _controllers.append(controller)
    yield controller
    if controller.phase.is_capturing:
        controller.cancel()
    wait_until(lambda: controller.phase == Phase.IDLE, 5)
    controller.wait_for_background()
    controller.toast.hide()
    QGuiApplication.clipboard().clear()


def drag(canvas, points):
    canvas.begin_stroke(points[0])
    for p in points[1:]:
        canvas.extend_stroke(p)
    canvas.end_stroke()


def test_snapshot_flow(app):
    controller = app
    controller.handle(HotkeyAction.SNAPSHOT)
    assert controller.phase == Phase.SNAPSHOT
    overlay = controller.overlays[0]
    assert isinstance(overlay, SnapshotOverlay) and overlay.isVisible()
    assert controller.hud.isVisible() and not controller.hud.starting
    assert controller.hud.shot_count == 1

    # Keyboard shortcuts on the overlay pick tools and colors.
    QTest.keyClick(overlay, Qt.Key_O)
    assert overlay.tool == AnnotationTool.ELLIPSE and controller.hud.tool == AnnotationTool.ELLIPSE
    QTest.keyClick(overlay, Qt.Key_4)
    assert overlay.color_index == 3

    drag(overlay, [(100, 100), (150, 130), (220, 180)])
    assert [a.number for a in controller.session.annotations] == [1]
    # Pen strokes are drawings: no number.
    QTest.keyClick(overlay, Qt.Key_P)
    drag(overlay, [(300, 300), (320, 330), (360, 310)])
    drag(overlay, [(300, 350), (360, 350)])
    QTest.keyClick(overlay, Qt.Key_N)
    overlay.begin_stroke((500, 500))
    assert [a.number for a in controller.session.annotations] == [1, None, None, 2]
    assert controller.hud.annotation_count == 2

    # Undo removes the pin; a too-short arrow is not a mark; a pen tap is a dot.
    QTest.keyClick(overlay, Qt.Key_Z, Qt.ControlModifier)
    assert [a.number for a in controller.session.annotations] == [1, None, None]
    QTest.keyClick(overlay, Qt.Key_A)
    drag(overlay, [(10, 10), (12, 11)])
    assert len(controller.session.annotations) == 3
    QTest.keyClick(overlay, Qt.Key_P)
    drag(overlay, [(600, 600)])
    assert len(controller.session.annotations[-1].points) == 1
    QTest.keyClick(overlay, Qt.Key_Backspace)
    assert len(controller.session.annotations) == 3

    # Tab hides the overlay for the next shot; the snapshot hotkey takes it.
    QTest.keyClick(overlay, Qt.Key_Tab)
    assert controller.phase == Phase.SNAPSHOT_NAVIGATING and not controller.overlays
    assert controller.hud.isVisible() and controller.hud.navigating
    assert controller.platform.focus_restored == ["previous-app"]
    controller.handle(HotkeyAction.SNAPSHOT)
    assert wait_until(lambda: controller.phase == Phase.SNAPSHOT and bool(controller.overlays), 3)
    second = controller.overlays[0]
    assert second.shot == 1 and controller.hud.shot_count == 2
    QTest.keyClick(second, Qt.Key_R)
    drag(second, [(200, 200), (400, 320)])

    pump(0.5)
    QTest.keyClick(second, Qt.Key_Return)
    assert controller.phase == Phase.PROCESSING and not controller.overlays and not controller.hud.isVisible()
    assert wait_until(lambda: bool(controller.finished), 20)
    assert controller.phase == Phase.IDLE and controller.session is None
    capture = controller.finished[0]
    assert capture.mode == CaptureMode.SNAPSHOT and capture.annotation_count == 3
    md = Path(capture.markdown_path).read_text(encoding="utf-8")
    assert "The user took 2 screenshots" in md
    assert "#1 circled (100,100)-(220,180) in frame 1" in md
    assert "[drawing (2 strokes) over (300,300)-(360,350) in frame 1]" in md
    assert "#2 box around (200,200)-(400,320) in frame 2" in md
    assert "so this" in md
    assert Path(capture.frame_paths[0]).is_file() and Path(capture.frame_paths[1]).is_file()

    # Listed under recent captures, copied in the default format, and the toast says so.
    assert controller.recents.items[0].folder == capture.folder
    mime = QGuiApplication.clipboard().mimeData()
    assert mime.text() == md
    assert [Path(u.toLocalFile()) for u in mime.urls()] == [Path(capture.markdown_path)]
    assert (
        controller.toast.text == "Copied to clipboard"
        and "2 frames · 3 annotations" in controller.toast.detail
    )
    assert controller.platform.focus_restored[-1] == "previous-app"


def test_snapshot_cancel_discards_everything(app):
    controller = app
    controller.start_snapshot()
    folder = controller.session.folder
    assert folder.is_dir()
    QTest.keyClick(controller.overlays[0], Qt.Key_Escape)
    assert controller.phase == Phase.IDLE and not controller.overlays and not controller.hud.isVisible()
    assert wait_until(lambda: not folder.exists(), 5)
    assert controller.toast.text == "Capture cancelled"
    assert not controller.finished and not controller.recents.items


def test_snapshot_without_microphone_still_captures(app):
    controller = app
    controller._audio_factory = lambda: FakeAudio(fail=True)
    controller.start_snapshot()
    assert not controller.hud.mic_available
    controller.overlays[0].tool = AnnotationTool.PIN
    controller.overlays[0].begin_stroke((50, 60))
    controller.finish()
    assert wait_until(lambda: bool(controller.finished), 20)
    md = Path(controller.finished[0].markdown_path).read_text(encoding="utf-8")
    assert "_(no speech detected)_" in md and "#1 pin at (50,60) in frame 1" in md
    assert controller.toast.detail.endswith("no microphone")


def test_hud_buttons_drive_the_capture(app):
    controller = app
    controller.start_snapshot()
    hud, overlay = controller.hud, controller.overlays[0]
    hud._tool_buttons[AnnotationTool.ARROW].click()
    assert overlay.tool == AnnotationTool.ARROW
    hud._swatches[2].click()
    assert overlay.color_index == 2
    drag(overlay, [(100, 100), (300, 300)])
    hud._undo.click()
    assert not controller.session.annotations
    hud._next_shot.click()
    assert controller.phase == Phase.SNAPSHOT_NAVIGATING
    assert "for shot 2" in hud._message.text()
    hud._cancel.click()
    assert controller.phase == Phase.IDLE


def test_walkthrough_flow(app):
    controller = app
    platform, grabber = controller.platform, controller.grabber
    controller.handle(HotkeyAction.WALKTHROUGH)
    assert controller.phase == Phase.WALKTHROUGH
    overlay = controller.overlays[0]
    assert isinstance(overlay, WalkthroughOverlay) and overlay.isVisible()
    # Click-through until draw mode is on.
    assert platform.click_through[overlay] is True
    assert wait_until(lambda: not controller.hud.starting, 5), "recording did not start"
    # Finishing is ignored only while starting; now it is recording.
    pump(0.6)

    # Holding the draw modifier turns draw mode on; the overlay takes the mouse.
    platform.mods = frozenset(controller.settings.draw_modifier.mods)
    assert wait_until(lambda: controller.hud.drawing, 2)
    assert platform.click_through[overlay] is False
    overlay.tool = AnnotationTool.PIN
    platform.left_down = True  # pressing on the overlay is drawing, not a click
    overlay.begin_stroke((400, 300))
    pump(0.1)
    platform.left_down = False
    platform.mods = frozenset()
    assert wait_until(lambda: not controller.hud.drawing, 2)
    assert platform.click_through[overlay] is True
    assert not controller.session.clicks
    assert [a.number for a in controller.session.annotations] == [1]
    # Well over the minimum spacing between keyframes, so the change gets its own frame.
    pump(2.2)

    # A click on the app being shown is recorded with its canvas position.
    platform.x, platform.y = 320, 240
    platform.left_down = True
    pump(0.1)
    platform.left_down = False
    pump(0.1)
    assert [(c.location, c.screen) for c in controller.session.clicks] == [((320.0, 240.0), 0)]
    grabber.level = 200  # the click changed the screen
    pump(1.2)

    # The toggle hotkey works too.
    controller.handle(HotkeyAction.TOGGLE_DRAW)
    assert wait_until(lambda: controller.hud.drawing, 2)
    controller.handle(HotkeyAction.TOGGLE_DRAW)
    assert wait_until(lambda: not controller.hud.drawing, 2)

    pump(1.0)
    controller.handle(HotkeyAction.WALKTHROUGH)
    assert controller.phase == Phase.PROCESSING
    assert wait_until(lambda: bool(controller.finished), 30)
    capture = controller.finished[0]
    assert capture is not None, controller.toast.detail
    from scribble import processor

    processor.wait_for_background()
    folder = Path(capture.folder)
    events = json.loads((folder / "events.json").read_text(encoding="utf-8"))
    md = (folder / "capture.md").read_text(encoding="utf-8")
    reasons = [r for f in events["frames"] for r in f["reasons"]]
    assert "start" in reasons and "annotation" in reasons
    assert "screen change" in reasons and "click" in reasons
    assert "[click at (" in md and "#1 pin at (" in md
    # The fake platform cannot hide the HUD from capture, so the legend explains it.
    assert HUD_LEGEND_NOTE in md
    assert (folder / "recording.mp4").is_file() and (folder / "audio.m4a").is_file()
    assert events["clicks"] == [{"time": events["clicks"][0]["time"], "location": [320, 240], "screen": 0}]


def test_walkthrough_marks_fade(app):
    controller = app
    controller.settings.set("fadeSeconds", 1.0)
    controller.start_walkthrough()
    overlay = controller.overlays[0]
    assert wait_until(lambda: not controller.hud.starting, 5)
    overlay.tool = AnnotationTool.RECTANGLE
    drag(overlay, [(100, 100), (300, 200)])
    assert len(overlay.annotations) == 1
    assert overlay.alpha_for(overlay.annotations[0], controller.session.now()) == 1.0
    # Gone from the screen after hold + fade-out, but still part of the capture.
    assert wait_until(lambda: not overlay.annotations, 4)
    assert len(controller.session.annotations) == 1
    controller.cancel()


def test_walkthrough_needs_continuous_capture(app):
    controller = app
    controller.grabber.continuous = False
    asked = []
    controller.on_needs_setup = lambda: asked.append(True)
    controller.start_walkthrough()
    assert controller.phase == Phase.IDLE and asked == [True]
    assert controller.toast.text == "Walkthrough is not available here"


def test_dictation_flow(app, monkeypatch):
    controller = app
    monkeypatch.setattr("scribble.session.input_device_name", lambda: "Fake Mic")
    controller.handle(HotkeyAction.DICTATION)
    assert controller.phase == Phase.DICTATION and controller.hud.isVisible()
    assert "Dictating" in controller.hud._message.text() and not controller.overlays
    folder = controller.session.folder
    pump(0.7)
    controller.handle(HotkeyAction.DICTATION)
    assert wait_until(lambda: bool(controller.finished), 20)
    assert controller.phase == Phase.IDLE
    assert controller.finished == ["so this button is broken here"]
    assert QGuiApplication.clipboard().text() == "so this button is broken here"
    assert (folder / "transcript.txt").read_text(encoding="utf-8") == "so this button is broken here"
    assert (folder / "audio.m4a").is_file()
    assert controller.toast.detail == "6 words"
    # Dictations are not listed under recent captures.
    assert not controller.recents.items


def test_dictation_without_microphone(app, monkeypatch):
    controller = app
    monkeypatch.setattr("scribble.session.input_device_name", lambda: None)
    controller.start_dictation()
    assert controller.phase == Phase.IDLE and controller.toast.text == "No microphone found"


def test_hotkeys_are_ignored_in_the_wrong_phase(app):
    controller = app
    controller.handle(HotkeyAction.FINISH)
    controller.handle(HotkeyAction.TOGGLE_DRAW)
    assert controller.phase == Phase.IDLE
    controller.start_snapshot()
    controller.handle(HotkeyAction.WALKTHROUGH)
    controller.handle(HotkeyAction.DICTATION)
    assert controller.phase == Phase.SNAPSHOT
    # The snapshot hotkey finishes a Snapshot; the finish hotkey would too.
    controller.handle(HotkeyAction.FINISH)
    assert controller.phase == Phase.PROCESSING
    # While processing, any hotkey only re-shows the busy toast.
    controller.handle(HotkeyAction.SNAPSHOT)
    assert controller.phase == Phase.PROCESSING and controller.toast.busy
    assert wait_until(lambda: bool(controller.finished), 20)


def test_recent_capture_can_be_copied_in_every_format(app):
    controller = app
    controller.start_snapshot()
    controller.overlays[0].tool = AnnotationTool.PIN
    controller.overlays[0].begin_stroke((50, 60))
    controller.finish()
    assert wait_until(lambda: bool(controller.finished), 20)
    capture = controller.finished[0]
    md = Path(capture.markdown_path).read_text(encoding="utf-8")
    frame = capture.frame_paths[0]
    board = QGuiApplication.clipboard()

    def files():
        return [str(Path(u.toLocalFile())) for u in board.mimeData().urls()]

    clipboard.write(capture, ClipboardFormat.UNIVERSAL)
    assert files() == [capture.markdown_path, frame] and board.mimeData().text() == md
    clipboard.write(capture, ClipboardFormat.TEXT_AND_PATHS)
    assert files() == [] and board.text() == md
    clipboard.write(capture, ClipboardFormat.IMAGES_AND_TEXT)
    assert files() == [frame] and board.mimeData().text() == md and board.mimeData().hasImage()
    clipboard.write(capture, ClipboardFormat.IMAGES_ONLY)
    assert files() == [frame] and board.mimeData().hasImage()
    assert not board.mimeData().hasFormat("text/plain")
    clipboard.write(capture, ClipboardFormat.COMPOSITE)
    composite = str(Path(capture.folder) / "composite.png")
    assert files() == [composite] and Path(composite).is_file()
    image = board.image()
    assert image.width() == clipboard.COMPOSITE_WIDTH and image.height() > 600
    clipboard.write(capture, ClipboardFormat.MARKDOWN_FILE)
    assert files() == [capture.markdown_path] and board.mimeData().text() == md


def test_recorder_and_pipeline_with_full_hd_frames(qapp, tmp_path):
    """The recorder thread, the encoder and keyframe extraction at a real display size, with
    the sparse timestamps a mostly static screen produces."""
    import time

    from scribble import processor
    from scribble.core.geometry import Size
    from scribble.processor import CaptureInput, DisplayInfo
    from scribble.recorder import WalkthroughRecorder
    from scribble.screens import Display

    screen = QGuiApplication.primaryScreen()
    display = Display(0, "Full HD", screen, screen.geometry(), 1.0, (0, 0, 1920, 1080))
    grabber = FakeGrabber()
    t0 = paths.now()
    recorder = WalkthroughRecorder(
        grabber, display, tmp_path / "screen_display1.mp4", cursor=lambda: (400.0, 300.0)
    )
    recorder.start()
    assert recorder.started.wait(10)
    time.sleep(2.2)
    grabber.level = 200  # one change, then static again
    time.sleep(2.4)
    result = recorder.stop()
    duration = paths.now() - t0
    assert result.video_path is not None and (result.pixel_size.width, result.pixel_size.height) == (
        1920,
        1080,
    )
    # Unchanged frames were skipped: far fewer samples than the ~46 grabs.
    assert 2 <= len(result.samples) <= 6 and grabber.grabs > 20

    inp = CaptureInput(
        mode=CaptureMode.WALKTHROUGH,
        folder=tmp_path,
        start_date=datetime.now().astimezone(),
        t0=t0,
        duration=duration,
        displays=[DisplayInfo(0, "Full HD", Size(1920, 1080))],
        recordings={0: result},
        cursor_timeline=[(0.0, 0)],
    )
    capture = processor.process(inp, Transcriber())
    processor.wait_for_background()
    events = json.loads((tmp_path / "events.json").read_text(encoding="utf-8"))
    assert [f["reasons"] for f in events["frames"]] == [["start"], ["screen change"]]
    from scribble import render

    first, second = (render.load_image(p) for p in capture.frame_paths)
    assert (first.width(), first.height()) == (1568, 882)
    assert (
        abs(first.pixelColor(1000, 600).red() - 60) < 12
        and abs(second.pixelColor(1000, 600).red() - 200) < 12
    )
    # The pointer was drawn into the frames (white arrow at 400,300 → 327,245 after scaling).
    assert first.pixelColor(330, 252).red() > 200
    assert (tmp_path / "recording.mp4").is_file()


def test_tray_menu_follows_the_app_state(app):
    from scribble.ui.tray import TrayController

    controller = app
    opened = []
    tray = TrayController(
        controller, controller.recents, controller.settings, controller.transcriber, controller.platform
    )
    tray.on_show_settings = lambda: opened.append("settings")

    def titles(menu):
        return [a.text() for a in menu.actions() if not a.isSeparator()]

    def action(menu, start):
        return next(a for a in menu.actions() if a.text().startswith(start))

    tray.rebuild()
    idle = titles(tray.menu)
    assert idle[:3] == [
        "Snapshot\tCtrl+Alt+Shift+S",
        "Walkthrough\tCtrl+Alt+Shift+W",
        "Dictation\tCtrl+Alt+Shift+T",
    ]
    assert "Recent captures" in idle and "Settings…" in idle and "Quit Scribble" in idle
    assert titles(action(tray.menu, "Recent captures").menu()) == ["No captures yet"]

    # A shortcut that could not be registered is flagged, and not advertised.
    tray.hotkey_failures = {"snapshot": "Ctrl+Alt+Shift+S is already used by another program"}
    tray.rebuild()
    assert titles(tray.menu)[0] == "Snapshot"
    warning = action(tray.menu, "⚠ Shortcut unavailable")
    assert "already used" in warning.text()
    warning.trigger()
    assert opened == ["settings"]
    tray.hotkey_failures = {}

    # Starting from the menu; while capturing it offers finish and cancel instead.
    action(tray.menu, "Snapshot").trigger()
    assert controller.phase == Phase.SNAPSHOT
    tray.rebuild()
    assert titles(tray.menu)[:2] == ["Finish capture\tCtrl+Alt+Shift+F", "Cancel capture"]
    controller.overlays[0].tool = AnnotationTool.PIN
    controller.overlays[0].begin_stroke((50, 60))
    action(tray.menu, "Finish capture").trigger()
    assert wait_until(lambda: bool(controller.finished), 20)

    # The capture is listed, and can be copied again in any format from its submenu.
    tray.rebuild()
    recent = action(tray.menu, "Recent captures").menu()
    entry = recent.actions()[0]
    assert entry.text().startswith("Snapshot · ") and "1 frame · 1 annotation" in entry.text()
    formats = titles(entry.menu())
    assert formats[:6] == ["Copy: {}".format(f.title) for f in ClipboardFormat]
    assert formats[-2:] == ["Open capture.md", "Show in file manager"]
    QGuiApplication.clipboard().clear()
    action(entry.menu(), "Copy: Text with image paths").trigger()
    assert QGuiApplication.clipboard().text().startswith("# Screen capture · Snapshot")
    assert (
        controller.toast.text == "Copied to clipboard" and controller.toast.detail == "Text with image paths"
    )

    # Without global hotkeys the menu does not show any.
    tray.hotkeys_available = False
    tray.rebuild()
    assert titles(tray.menu)[:3] == ["Snapshot", "Walkthrough", "Dictation"]

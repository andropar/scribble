"""Captures across two displays with different scaling, headless.

Not collected on its own: ``test_multi_display.py`` runs this file in a child process whose
Qt (offscreen platform) is configured with two screens, a 1920×1080 one at 100% and, to its
right, a 1920×1080 one at 200% (960×540 canvas points). Screen and microphone are fakes; the
overlays, the controller and the pipeline are real.
"""

import json
import sys
from pathlib import Path

import pytest
from PySide6.QtGui import QGuiApplication

from scribble import processor, render, screens, sounds
from scribble.clipboard import RecentsStore
from scribble.core.models import AnnotationTool
from scribble.session import Phase, SessionController
from scribble.settings import AppSettings

sys.path.insert(0, str(Path(__file__).parent))
from fakes import FakeAudio, FakeGrabber, FakePlatform, pump, wait_until  # noqa: E402
from helpers import FakeTranscriber  # noqa: E402


class Transcriber(FakeTranscriber):
    class _Status:
        is_ready, title = True, "ready"

    status = _Status()

    def subscribe(self, listener):
        pass


_controllers = []


@pytest.fixture
def controller(qapp, tmp_path, monkeypatch):
    monkeypatch.setenv("SCRIBBLE_HOME", str(tmp_path / "home"))
    sounds.enabled = False
    c = SessionController(
        AppSettings(),
        RecentsStore(),
        Transcriber(start=0.2, step=0.15),
        FakePlatform(),
        FakeGrabber(),
        FakeAudio,
    )
    c.finished = []
    c.capture_finished.connect(c.finished.append)
    _controllers.append(c)
    yield c
    if c.phase.is_capturing:
        c.cancel()
    wait_until(lambda: c.phase == Phase.IDLE, 5)
    c.wait_for_background()
    c.toast.hide()


def two_displays():
    """The 100% display and the 200% display (Qt's order of screens is not the layout's)."""
    laptop, external = sorted(screens.displays(), key=lambda d: d.dpr)
    return laptop, external


def point_at(platform, display, x, y):
    """Puts the fake pointer at a canvas position of a display."""
    platform.x = int(display.native[0] + x * display.dpr)
    platform.y = int(display.native[1] + y * display.dpr)


def drag(canvas, points):
    canvas.begin_stroke(points[0])
    for p in points[1:]:
        canvas.extend_stroke(p)
    canvas.end_stroke()


def test_displays_keep_their_native_origin_and_scale(qapp):
    first, second = two_displays()
    assert (first.geometry.width(), first.geometry.height(), first.dpr) == (1920, 1080, 1.0)
    assert (second.geometry.width(), second.geometry.height(), second.dpr) == (960, 540, 2.0)
    assert first.native == (0, 0, 1920, 1080) and second.native == (1920, 0, 1920, 1080)
    assert first.index != second.index and first.name != second.name
    both = screens.displays()
    assert (
        screens.display_at(both, 1919, 500) is not None
        and screens.display_at(both, 1919, 500).index == first.index
    )
    assert screens.display_at(both, 1920, 500).index == second.index
    assert screens.display_at(both, 5000, 500) is None
    # Native pixels of the second display are half canvas points.
    assert second.canvas_point(1920 + 300, 200) == (150.0, 100.0)
    assert first.info.canvas_size.width == 1920 and second.info.canvas_size.width == 960


def test_snapshot_freezes_and_frames_both_displays(controller):
    first, second = two_displays()
    platform = controller.platform
    point_at(platform, second, 400, 300)  # the cursor's display leads
    controller.start_snapshot()
    assert controller.phase == Phase.SNAPSHOT and sorted(controller.overlays) == [0, 1]
    for d in (first, second):
        assert controller.overlays[d.index].geometry() == d.geometry
    shots = {s.display: s for s in controller.session.shots}
    assert shots[second.index].primary and not shots[first.index].primary
    # Screenshots are native: both 1920×1080 pixels, for canvases of different size.
    assert (shots[first.index].image.width(), shots[first.index].canvas_size.width) == (1920, 1920)
    assert (shots[second.index].image.width(), shots[second.index].canvas_size.width) == (1920, 960)
    assert controller._hud_display.index == second.index

    controller._select_tool(AnnotationTool.RECTANGLE)
    drag(controller.overlays[first.index], [(100, 100), (400, 300)])
    drag(controller.overlays[second.index], [(100, 100), (400, 300)])
    a, b = controller.session.annotations
    assert (a.screen, a.number) == (first.index, 1) and (b.screen, b.number) == (second.index, 2)
    pump(0.3)
    controller.finish()
    assert wait_until(lambda: bool(controller.finished), 30)
    capture = controller.finished[0]
    md = Path(capture.markdown_path).read_text(encoding="utf-8")
    events = json.loads((Path(capture.folder) / "events.json").read_text(encoding="utf-8"))
    # Frame 1 is the cursor's display, frame 2 the other one (it was drawn on).
    assert [f["display"] for f in events["frames"]] == [second.index, first.index]
    sizes = {d["index"]: d["canvasSize"] for d in events["displays"]}
    assert sizes == {first.index: [1920, 1080], second.index: [960, 540]}
    # The same canvas rectangle lands on different pixels: frames are 1568 px wide, so the
    # scale is 1568/1920 on the first display and 1568/960 on the second.
    assert "#1 box around (82,82)-(327,245) in frame 2" in md
    assert "#2 box around (163,163)-(653,490) in frame 1" in md
    assert 'display "{}"'.format(first.name) in md and 'display "{}"'.format(second.name) in md
    for path in capture.frame_paths:
        image = render.load_image(path)
        assert (image.width(), image.height()) == (1568, 882)
    # The box is burned in where the text says: red on its top edge in both frames.
    top_edge = render.load_image(capture.frame_paths[0]).pixelColor(400, 163)
    assert top_edge.red() > 200 and top_edge.green() < 120
    top_edge = render.load_image(capture.frame_paths[1]).pixelColor(200, 82)
    assert top_edge.red() > 200 and top_edge.green() < 120


def test_snapshot_of_the_cursor_display_only(controller):
    first, second = two_displays()
    controller.settings.set("allDisplays", False)
    point_at(controller.platform, second, 100, 100)
    controller.start_snapshot()
    assert sorted(controller.overlays) == [second.index] and len(controller.session.shots) == 1
    controller.overlays[second.index].tool = AnnotationTool.PIN
    controller.overlays[second.index].begin_stroke((480, 270))
    controller.finish()
    assert wait_until(lambda: bool(controller.finished), 30)
    md = Path(controller.finished[0].markdown_path).read_text(encoding="utf-8")
    assert "#1 pin at (784,441) in frame 1" in md and "several displays" not in md


def test_walkthrough_follows_the_cursor(controller):
    first, second = two_displays()
    platform, grabber = controller.platform, controller.grabber
    point_at(platform, first, 500, 500)
    controller.start_walkthrough()
    assert sorted(controller.overlays) == [0, 1] and sorted(controller._recorders) == [0, 1]
    assert wait_until(lambda: not controller.hud.starting, 15), "recorders did not start"
    hud = controller.hud
    assert controller._hud_display.index == first.index
    pump(0.8)

    def click(display, x, y):
        point_at(platform, display, x, y)
        pump(0.1)
        platform.left_down = True
        pump(0.1)
        platform.left_down = False
        pump(0.1)

    click(first, 700, 400)
    pump(0.8)
    # Over to the second display, and stay there.
    point_at(platform, second, 300, 200)
    assert wait_until(lambda: controller._hud_display.index == second.index, 3), "toolbar did not follow"
    assert [d for _, d in controller.session.cursor_timeline] == [first.index, second.index]
    switched_at = controller.session.cursor_timeline[1][0]
    pump(2.4)
    click(second, 300, 200)
    grabber.level = 200
    pump(0.8)

    # Draw mode reaches the overlays of both displays; the mark goes where it was drawn.
    platform.mods = frozenset(controller.settings.draw_modifier.mods)
    assert wait_until(lambda: hud.drawing, 2)
    assert platform.click_through[controller.overlays[first.index]] is False
    assert platform.click_through[controller.overlays[second.index]] is False
    controller.overlays[second.index].tool = AnnotationTool.PIN
    controller.overlays[second.index].begin_stroke((480, 270))
    platform.mods = frozenset()
    assert wait_until(lambda: not hud.drawing, 2)
    pump(1.0)

    controller.finish()
    assert wait_until(lambda: bool(controller.finished), 60)
    capture = controller.finished[0]
    assert capture is not None, controller.toast.detail
    processor.wait_for_background()
    folder = Path(capture.folder)
    md = (folder / "capture.md").read_text(encoding="utf-8")
    events = json.loads((folder / "events.json").read_text(encoding="utf-8"))

    # Clicks carry their display and that display's canvas coordinates.
    assert [(c["screen"], c["location"]) for c in events["clicks"]] == [
        (first.index, [700, 400]),
        (second.index, [300, 200]),
    ]
    frames = events["frames"]
    assert frames[0]["display"] == first.index and frames[0]["reasons"] == ["start"]
    on_second = [f for f in frames if f["display"] == second.index]
    assert any("display switch" in f["reasons"] for f in on_second)
    assert any("annotation" in f["reasons"] for f in on_second)
    # Once the cursor has left the first display, frames come from the second one.
    assert all(f["display"] == second.index for f in frames if f["time"] > switched_at + 1.1), frames
    assert [f["time"] for f in frames] == sorted(f["time"] for f in frames)
    assert [f["number"] for f in frames] == list(range(1, len(frames) + 1))
    # Image pixels: 1568/1920 per point on the first display, 1568/960 on the second.
    assert "[click at (572,327) in frame 1]" in md
    assert "[click at (490,327) in frame " in md and "#1 pin at (784,441) in frame " in md
    assert 'display "{}"'.format(second.name) in md
    for d in (first, second):
        assert (folder / "recording_display{}.mp4".format(d.index + 1)).is_file()
    assert QGuiApplication.clipboard().mimeData().text() == md

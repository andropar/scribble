"""Desktop tests for several displays and display scaling.

They run where the session has at least two displays. CI builds such a session under Xvfb:
two monitors side by side, the second one scaled 200% (``QT_SCREEN_SCALE_FACTORS``), which
is the layout a laptop with an external monitor has.
"""

import json
import math
import os
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

from fakes import pump, wait_until  # noqa: E402
from support import BLUE, PURPLE, AppWindow, close_to, rgb  # noqa: E402

from voice_annotate import processor, render  # noqa: E402
from voice_annotate.core.models import AnnotationTool  # noqa: E402
from voice_annotate.session import Phase  # noqa: E402

pytestmark = [
    pytest.mark.desktop,
    pytest.mark.skipif(sys.platform == "darwin", reason="no input injection backend for macOS"),
]


@pytest.fixture
def two(env):
    if len(env.displays) < 2:
        pytest.skip("needs two displays")
    return env.displays[0], env.displays[1]


@pytest.fixture
def windows(env, two):
    """A blue window on the first display and a purple one on the second, each 600×400
    canvas points at (40,40) of its display."""
    first, second = two
    left, right = AppWindow("#2e6fd0", 40, 40, 600, 400, first), AppWindow(
        "#8a2ed0", 40, 40, 600, 400, second
    )
    for window in (left, right):
        window.show()
        window.raise_()
    pump(0.5)
    env.click(600, 420, display=first)
    left.clicks.clear()
    pump(0.3)
    yield left, right
    left.close()
    right.close()
    pump(0.1)


def test_displays_are_mapped_to_native_pixels(env, two, windows):
    first, second = two
    expected = os.environ.get("VOICE_ANNOTATE_EXPECT_DISPLAYS")  # "x,y,w,h@scale;…" from the CI setup
    if expected:
        described = ";".join("{},{},{},{}@{:g}".format(*d.native, d.dpr) for d in env.displays)
        assert described == expected
    assert first.native != second.native and first.name != second.name
    for d in (first, second):
        # Canvas size times scale is the native size; canvas and native positions convert.
        assert d.native[2] == round(d.geometry.width() * d.dpr) and d.native[3] == round(
            d.geometry.height() * d.dpr
        )
        x, y = env.native(100, 50, display=d)
        assert d.contains_native(x, y) and close_to(d.canvas_point(x, y), (100, 50), 1)
    sys.stdout.write(
        "\n[displays] "
        + " | ".join(
            "{} {}x{} @{:g} native {}".format(
                d.name, d.geometry.width(), d.geometry.height(), d.dpr, d.native
            )
            for d in env.displays
        )
        + "\n"
    )

    # Each display's grab shows that display: blue window here, purple window there.
    a, b = env.snap("display_1", first), env.snap("display_2", second)
    assert (a.width(), a.height()) == first.native[2:] and (b.width(), b.height()) == second.native[2:]
    assert close_to(env.pixel(a, 300, 200, first), BLUE, 8) and close_to(
        env.pixel(b, 300, 200, second), PURPLE, 8
    )
    assert not close_to(env.pixel(a, 700, 480, first), BLUE, 8)

    # The pointer is found on the right display, in that display's canvas coordinates.
    env.move(123, 77, display=second)
    pump(0.2)
    state = env.platform.input_state()
    from voice_annotate import screens

    assert screens.display_at(env.displays, state.x, state.y) is second
    assert close_to(second.canvas_point(state.x, state.y), (123, 77), 1)


def test_snapshot_across_displays(env, two, windows, controller):
    first, second = two
    # The cursor is on the second display: that one leads the capture.
    env.move(320, 240, display=second)
    pump(0.3)
    controller.start_snapshot()
    assert wait_until(lambda: controller.phase == Phase.SNAPSHOT and len(controller.overlays) == 2, 10)
    pump(0.6)
    shots = controller.session.shots
    assert len(shots) == 2 and [s.primary for s in shots] == [s.display == second.index for s in shots]
    for d in (first, second):
        overlay = controller.overlays[d.index]
        # Each overlay covers exactly its display.
        g = overlay.geometry()
        assert (g.x(), g.y(), g.width(), g.height()) == (
            d.geometry.x(),
            d.geometry.y(),
            d.geometry.width(),
            d.geometry.height(),
        )
    hud = controller.hud
    assert second.geometry.contains(hud.frameGeometry().center()), "toolbar is not on the cursor's display"

    # One mark on each display, drawn with the real mouse; the tool key applies to both.
    env.inject.key("O")
    assert wait_until(lambda: all(o.tool == AnnotationTool.ELLIPSE for o in controller.overlays.values()), 5)
    env.drag([(100, 100), (200, 160), (300, 220)], display=first)
    assert wait_until(lambda: len(controller.session.annotations) == 1, 5), "no mark on the first display"
    env.drag([(120, 120), (220, 180), (320, 240)], display=second)
    assert wait_until(lambda: len(controller.session.annotations) == 2, 5), "no mark on the second display"
    a, b = controller.session.annotations
    assert a.screen == first.index and b.screen == second.index and (a.number, b.number) == (1, 2)
    assert close_to(a.points[0], (100, 100), 2) and close_to(a.points[-1], (300, 220), 2)
    assert close_to(b.points[0], (120, 120), 2) and close_to(b.points[-1], (320, 240), 2)
    env.snap("displays_snapshot_1", first)
    env.snap("displays_snapshot_2", second)

    pump(0.3)
    env.inject.key("Return")
    assert wait_until(lambda: bool(controller.finished), 60), "Enter did not finish the capture"
    capture = controller.finished[0]
    assert capture is not None, controller.toast.detail
    md = Path(capture.markdown_path).read_text(encoding="utf-8")
    events = json.loads((Path(capture.folder) / "events.json").read_text(encoding="utf-8"))
    # Frame 1 is the display the cursor was on, frame 2 the other one (it was drawn on).
    assert [f["display"] for f in events["frames"]] == [second.index, first.index]
    assert (
        "several displays" in md
        and 'display "{}"'.format(second.name) in md
        and 'display "{}"'.format(first.name) in md
    )
    assert "**[00:00 · frames 1, 2]**" in md

    def described(mark, frame_number, display):
        """The reference the capture must contain for a mark, from where it was really drawn."""
        frame = events["frames"][frame_number - 1]
        sx, sy = frame["width"] / display.geometry.width(), frame["height"] / display.geometry.height()
        (x1, y1), (x2, y2) = mark.points[0], mark.points[-1]
        box = [math.floor(v + 0.5) for v in (x1 * sx, y1 * sy, x2 * sx, y2 * sy)]
        return "#{} circled ({},{})-({},{}) in frame {}".format(mark.number, *box, frame_number), (sx, sy)

    text_a, scale_a = described(a, 2, first)
    text_b, scale_b = described(b, 1, second)
    assert text_a in md and text_b in md, md

    # Each frame is its display at native resolution (up to the size limit), marks included.
    frame_1, frame_2 = (render.load_image(p) for p in capture.frame_paths)
    assert frame_1.width() == min(second.native[2], 1568) and frame_2.width() == min(first.native[2], 1568)
    assert close_to(rgb(frame_1.pixelColor(int(500 * scale_b[0]), int(100 * scale_b[1]))), PURPLE, 12)
    assert close_to(rgb(frame_2.pixelColor(int(500 * scale_a[0]), int(100 * scale_a[1]))), BLUE, 12)
    edge = rgb(frame_1.pixelColor(int(120 * scale_b[0]), int(180 * scale_b[1])))
    assert (
        edge[0] > 200 and edge[1] < 110 and edge[2] < 110
    ), "ellipse not on the second display's frame: {}".format(edge)
    edge = rgb(frame_2.pixelColor(int(100 * scale_a[0]), int(160 * scale_a[1])))
    assert edge[0] > 180 and edge[1] < 120, "ellipse not on the first display's frame: {}".format(edge)


def test_walkthrough_follows_the_cursor_across_displays(env, two, windows, controller):
    first, second = two
    left, right = windows
    env.move(300, 300, display=first)
    pump(0.3)
    controller.start_walkthrough()
    assert controller.phase == Phase.WALKTHROUGH and len(controller.overlays) == 2
    assert wait_until(lambda: not controller.hud.starting, 20), "recording did not start on both displays"
    hud = controller.hud
    assert first.geometry.contains(hud.frameGeometry().center())
    pump(1.0)

    env.click(200, 150, display=first)
    assert wait_until(lambda: len(left.clicks) == 1, 5), "click did not pass through on the first display"
    pump(1.0)

    # Move over to the second display and stay: the toolbar follows, the capture notes it.
    env.move(200, 150, display=second)
    assert wait_until(
        lambda: second.geometry.contains(hud.frameGeometry().center()), 5
    ), "toolbar did not follow"
    assert [d for _, d in controller.session.cursor_timeline] == [first.index, second.index]
    pump(2.6)
    env.click(200, 150, display=second)
    assert wait_until(lambda: len(right.clicks) == 1, 5), "click did not pass through on the second display"
    right.set_color("#2ea86f")
    pump(0.5)

    # Draw on the second display with the modifier held.
    for key in controller.settings.draw_modifier.mods:
        env.inject.key_down(key)
    assert wait_until(lambda: hud.drawing, 5)
    pump(0.3)
    env.drag([(100, 250), (180, 280), (260, 250), (340, 300)], display=second)
    assert wait_until(
        lambda: len(controller.session.annotations) == 1, 5
    ), "stroke did not reach the second overlay"
    for key in reversed(controller.settings.draw_modifier.mods):
        env.inject.key_up(key)
    assert wait_until(lambda: not hud.drawing, 5)
    stroke = controller.session.annotations[0]
    assert stroke.screen == second.index and close_to(stroke.points[0], (100, 250), 2)
    assert len(right.clicks) == 1
    pump(1.0)
    env.snap("displays_walkthrough_2", second)

    controller.finish()
    assert wait_until(lambda: bool(controller.finished), 90)
    capture = controller.finished[0]
    assert capture is not None, controller.toast.detail
    processor.wait_for_background()
    folder = Path(capture.folder)
    md = (folder / "capture.md").read_text(encoding="utf-8")
    events = json.loads((folder / "events.json").read_text(encoding="utf-8"))

    # Clicks carry their display and that display's canvas coordinates.
    assert [(c["screen"]) for c in events["clicks"]] == [first.index, second.index]
    assert close_to(events["clicks"][0]["location"], (200, 150), 2) and close_to(
        events["clicks"][1]["location"], (200, 150), 2
    )
    # The capture starts on the first display and switches to the second.
    frames = events["frames"]
    assert frames[0]["display"] == first.index and frames[0]["reasons"] == ["start"]
    switched = [f for f in frames if f["display"] == second.index]
    assert switched and any("display switch" in f["reasons"] for f in switched), frames
    assert any("annotation" in f["reasons"] for f in switched)
    assert [f["time"] for f in frames] == sorted(f["time"] for f in frames)
    assert 'display "{}"'.format(second.name) in md and "[click at (" in md and " in frame " in md
    assert (folder / "recording_display{}.mp4".format(first.index + 1)).is_file()
    assert (folder / "recording_display{}.mp4".format(second.index + 1)).is_file()

    # Frames of the second display show the second display, at its native resolution.
    annotated = next(f for f in switched if "annotation" in f["reasons"])
    frame = render.load_image(folder / annotated["file"])
    assert frame.width() == min(second.native[2], 1568)
    s = frame.width() / second.geometry.width()
    assert close_to(rgb(frame.pixelColor(int(500 * s), int(100 * s))), (0x2E, 0xA8, 0x6F), 20)
    (ax, ay), (bx, by) = stroke.points[1], stroke.points[2]
    c = rgb(frame.pixelColor(int((ax + bx) / 2 * s), int((ay + by) / 2 * s)))
    assert c[0] > 170 and c[1] < 130, "stroke missing in the second display's keyframe: {}".format(c)
    first_frame = render.load_image(folder / frames[0]["file"])
    s0 = first_frame.width() / first.geometry.width()
    assert close_to(rgb(first_frame.pixelColor(int(300 * s0), int(200 * s0))), BLUE, 20)

"""Port of Tests/ScribbleCoreTests/CoreTests.swift: both apps must agree on every case."""

import math
from datetime import datetime, timezone

import numpy as np
import pytest

from scribble.core import describer, keyframes, markdown
from scribble.core.geometry import CoordinateMapper, Rect, Size, detail_rect, fit_size, round_half_away
from scribble.core.keyframes import KeyframeConfig, KeyframeSelection
from scribble.core.markdown import CaptureDocument, DetailRef, FrameRef, Marker
from scribble.core.models import (
    RED,
    Annotation,
    AnnotationShape,
    AnnotationTool,
    CaptureMode,
    FrameSample,
    Word,
    default_shape,
)


def ann(tool, points, start, end=None, **kw):
    a = Annotation(tool=tool, points=points, color=RED, start_time=start, **kw)
    a.finalize(start if end is None else end)
    return a


# MARK: shapes


def test_pen_strokes_stay_freehand_drawings():
    circle = [
        (200 + 80 * math.cos(i / 60 * 2 * math.pi), 150 + 50 * math.sin(i / 60 * 2 * math.pi))
        for i in range(61)
    ]
    loop = ann(AnnotationTool.PEN, circle, 0, 1)
    assert loop.shape == AnnotationShape.FREEHAND
    assert loop.is_drawing
    assert loop.number is None


def test_tool_shapes():
    assert default_shape(AnnotationTool.ARROW) == AnnotationShape.ARROW
    assert default_shape(AnnotationTool.RECTANGLE) == AnnotationShape.BOX
    assert default_shape(AnnotationTool.PIN) == AnnotationShape.PIN


# MARK: geometry


def test_round_half_away_matches_swift():
    assert round_half_away(0.5) == 1
    assert round_half_away(1.5) == 2
    assert round_half_away(2.5) == 3
    assert round_half_away(-0.5) == -1
    assert round_half_away(2.4) == 2


def test_mapper_scales_and_clamps():
    m = CoordinateMapper(Size(1000, 500), Size(2000, 1000))
    assert m.map_point((100, 50)) == (200, 100)
    assert m.map_point((5000, -10)) == (1999, 0)
    assert m.describe(Rect(10, 10, 20, 30)) == "(20,20)-(60,80)"


def test_mapper_with_crop_region():
    m = CoordinateMapper(Size(1000, 500), Size(400, 200), Rect(100, 100, 200, 100))
    assert m.map_point((150, 150)) == (100, 100)


def test_fit_never_upscales():
    assert fit_size(Size(800, 600), 1568) == Size(800, 600)
    assert fit_size(Size(3024, 1964), 1568) == Size(1568, 1018)
    assert fit_size(Size(1000, 4000), 2000) == Size(500, 2000)


def test_detail_crop_only_on_large_displays():
    region = [Rect(100, 100, 50, 50)]
    # Laptop: 1512 pt shown at 1568 px → legible, no crop.
    assert detail_rect(region, Size(1512, 982), 1568) is None
    # 6K display: 3008 pt shown at 1568 px → crop.
    crop = detail_rect(region, Size(3008, 1692), 1568)
    assert crop is not None
    assert crop.min_x >= 0 and crop.min_y >= 0
    assert crop.width >= 800 and crop.height >= 500
    assert crop.contains(region[0])


def test_detail_crop_skipped_when_annotations_cover_everything():
    regions = [Rect(10, 10, 20, 20), Rect(2900, 1600, 50, 50)]
    assert detail_rect(regions, Size(3008, 1692), 1568) is None


# MARK: describer

MAPPER = CoordinateMapper(Size(1000, 1000), Size(1000, 1000))


def test_arrow_description():
    a = ann(AnnotationTool.ARROW, [(10, 20), (300, 400)], 0, id=2, number=2)
    assert describer.describe(a, MAPPER, 3) == "#2 arrow from (10,20) pointing at (300,400) in frame 3"


def test_circled_description():
    a = ann(AnnotationTool.ELLIPSE, [(120, 100), (280, 200)], 0, id=7, number=1)
    assert describer.describe(a, MAPPER, None) == "#1 circled (120,100)-(280,200)"


def test_pin_description():
    pin = ann(AnnotationTool.PIN, [(50, 60)], 0, id=9, number=4)
    assert describer.describe(pin, MAPPER, 1) == "#4 pin at (50,60) in frame 1"


def stroke(x, start, end, shot=0):
    return ann(AnnotationTool.PEN, [(x, 100), (x + 50, 140)], start, end, shot=shot)


def test_drawing_bursts_split_on_pauses_and_frames():
    strokes = [
        stroke(100, 0, 0.5),
        stroke(200, 1.2, 1.8),
        stroke(300, 2.5, 3),
        # A pause to think, then more drawing.
        stroke(400, 8, 8.6),
        # Same moment, another shot.
        stroke(500, 9, 9.5, shot=1),
    ]
    bursts = describer.drawing_bursts(strokes, lambda a: a.shot)
    assert [len(s) for _, s in bursts] == [3, 1, 1]
    assert [f for f, _ in bursts] == [0, 0, 1]


def test_drawing_description_has_no_number():
    text = describer.describe_drawing([stroke(100, 0, 1), stroke(300, 1.5, 2)], MAPPER, 2)
    assert text == "drawing (2 strokes) over (100,100)-(350,140) in frame 2"
    assert describer.describe_drawing([stroke(100, 0, 1)], MAPPER, None) == "drawing over (100,100)-(150,140)"


# MARK: keyframes


def thumb(v):
    return np.full(64 * 40, int(v), dtype=np.uint8)


def two_view_samples(duration=12.0):
    """Screen shows view A until t=5, animates to view B until t=5.5, then stays."""
    samples = []
    t = 0.0
    while t <= duration:
        if t < 5:
            v = 40
        elif t < 5.5:
            v = int(40 + (t - 5) / 0.5 * 160)
        else:
            v = 200
        samples.append(FrameSample(t, thumb(v)))
        t += 0.1
    return samples


def select(samples, annotations=(), clicks=(), duration=12.0, **kw):
    return keyframes.select(samples, list(annotations), list(clicks), [], duration, **kw)


def test_diff():
    assert keyframes.diff(thumb(0), thumb(255)) == pytest.approx(1, abs=1e-9)
    assert keyframes.diff(thumb(10), thumb(10)) == 0


def test_picks_settled_frame_after_change_and_drops_duplicate_end():
    sel = select(two_view_samples())
    assert len(sel) == 2
    assert sel[0].reasons == ["start"]
    change = sel[-1]
    assert change.reasons == ["screen change"]
    # Settled: after the animation ended, not mid-animation.
    assert 5.5 <= change.time < 6.5


def test_end_frame_kept_when_it_shows_something_new():
    samples = two_view_samples(12)
    samples.append(FrameSample(11.9, thumb(120)))
    sel = select(samples)
    assert "end" in sel[-1].reasons


def test_returning_to_earlier_view_is_kept():
    # A → B → A: the second A is chronologically meaningful and must not be deduped away.
    samples = [FrameSample(i * 0.1, thumb(40 if (i * 0.1 < 6 or i * 0.1 >= 13) else 200)) for i in range(200)]
    sel = select(samples, duration=20)
    assert len([s for s in sel if "screen change" in s.reasons]) == 2


def test_annotation_frames_always_kept_and_grouped():
    a1 = ann(AnnotationTool.PIN, [(0, 0)], 2, 2, id=1)
    a2 = ann(AnnotationTool.ARROW, [(0, 0), (10, 10)], 2.5, 3, id=2)
    a3 = ann(AnnotationTool.RECTANGLE, [(0, 0), (10, 10)], 8, 8.5, id=3)
    sel = select(two_view_samples(), [a1, a2, a3])
    annotated = [s for s in sel if "annotation" in s.reasons]
    assert len(annotated) == 2
    assert annotated[0].annotation_ids == [1, 2]
    assert annotated[-1].annotation_ids == [3]
    assert annotated[0].time == pytest.approx(3.25, abs=0.01)


def test_screen_change_splits_annotation_groups():
    a1 = ann(AnnotationTool.PIN, [(0, 0)], 4.6, 4.6, id=1)
    a2 = ann(AnnotationTool.PIN, [(0, 0)], 5.9, 5.9, id=2)
    sel = select(two_view_samples(), [a1, a2])
    assert len([s for s in sel if "annotation" in s.reasons]) == 2


def test_budget_respected_for_optional_frames():
    # Screen alternates every 2 s for 60 s → many change candidates.
    samples = [FrameSample(i * 0.1, thumb(30 if int(i * 0.1 / 2) % 2 == 0 else 220)) for i in range(600)]
    sel = select(samples, duration=60, config=KeyframeConfig(max_frames=8))
    assert 6 <= len(sel) <= 8
    for i in range(1, len(sel)):
        assert sel[i].time > sel[i - 1].time


def test_periodic_fallback_on_static_screen():
    samples = [FrameSample(i * 0.1, thumb(100 + i % 2)) for i in range(600)]
    sel = select(samples, duration=60)
    # Nothing ever changes: periodic and end frames are all duplicates of the start frame.
    assert [s.reasons for s in sel] == [["start"]]


def test_click_with_small_change_adds_frame():
    # A checkbox-sized change right after the click: too small for a "screen change"
    # candidate, but the click still gets its own frame.
    samples = [FrameSample(i * 0.1, thumb(50 if i < 62 else 52)) for i in range(100)]
    sel = select(samples, clicks=[6.0], duration=10)
    assert any("click" in s.reasons for s in sel)


def test_click_without_visible_change_is_dropped():
    samples = [FrameSample(i * 0.1, thumb(50)) for i in range(100)]
    sel = select(samples, clicks=[6.0], duration=10)
    assert [s.reasons for s in sel] == [["start"]]


def test_empty_inputs():
    assert select([], duration=0) == []
    assert [s.reasons for s in select([], duration=10)] == [["start"], ["end"]]


# MARK: markdown


def words(text, start, step=0.3):
    return [Word(" " + w, start + i * step, start + i * step + 0.25) for i, w in enumerate(text.split(" "))]


def test_interleaves_frames_markers_and_words():
    frames = [
        FrameRef(1, 0.3, "/a/frame_01.png", Size(100, 50)),
        FrameRef(2, 4.0, "/a/frame_02.png", Size(100, 50)),
    ]
    w = words("so this button", 0.1) + words("is broken here", 4.2)
    paras = markdown.transcript_paragraphs(frames, w, [Marker(0.55, "#1 pin at (1,2) in frame 1")])
    assert len(paras) == 2
    assert paras[0].frame == 1
    assert paras[0].text == "so this [#1 pin at (1,2) in frame 1] button"
    assert paras[1].frame == 2
    assert paras[1].text == "is broken here"


def test_simultaneous_frames_share_paragraph():
    size = Size(10, 10)
    frames = [
        FrameRef(1, 0.2, "/f1.png", size),
        FrameRef(2, 0.2, "/f2.png", size),
        FrameRef(3, 0.2, "/f3.png", size),
        FrameRef(4, 6, "/f4.png", size),
    ]
    paras = markdown.transcript_paragraphs(frames, words("look here", 1) + words("and there", 6.5), [])
    assert [p.frames for p in paras] == [[1, 2, 3], [4]]
    assert [p.text for p in paras] == ["look here", "and there"]


def test_long_pause_starts_new_paragraph():
    frames = [FrameRef(1, 0, "/f.png", Size(1, 1))]
    paras = markdown.transcript_paragraphs(frames, words("first part", 0) + words("second part", 10), [])
    assert [p.text for p in paras] == ["first part", "second part"]
    assert paras[1].frame is None
    assert paras[1].time == pytest.approx(10, abs=1e-9)


def test_compose_contains_paths_and_sections():
    frames = [
        FrameRef(
            1,
            0,
            "/caps/x/frame_01.png",
            Size(1568, 1018),
            detail=DetailRef("/caps/x/frame_01_detail.png", Size(1200, 750), "(1,2)-(3,4)"),
        )
    ]
    doc = CaptureDocument(
        CaptureMode.SNAPSHOT,
        datetime.fromtimestamp(0, timezone.utc),
        12.4,
        "/caps/x",
        frames,
        words("hello there", 0.5),
        [],
        ["#1 pin at (1,1) in frame 1 · 00:00"],
        False,
    )
    md = markdown.compose(doc)
    assert "/caps/x/frame_01.png" in md
    assert "detail of frame 1, region (1,2)-(3,4), 1200×750: /caps/x/frame_01_detail.png" in md
    assert "## Transcript" in md
    assert "**[00:00 · frame 1]** hello there" in md
    assert "## Annotations" in md
    assert "0:12" in md


def test_no_speech():
    doc = CaptureDocument(CaptureMode.WALKTHROUGH, datetime.now().astimezone(), 3, "/x", [], [], [], [], True)
    md = markdown.compose(doc)
    assert "_(no speech detected)_" in md
    assert "[click at (x,y)]" in md


def test_timecode():
    assert markdown.clock(65.9) == "01:05"
    assert markdown.clock(3725) == "1:02:05"
    assert markdown.duration_text(47.4) == "0:47"


# MARK: multiple displays


def test_active_intervals_from_timeline():
    iv = keyframes.active_intervals([(0, 0), (4, 2), (9, 0)], 3, 12)
    assert iv[0] == [(0, 4), (9, 12)]
    assert iv[1] == []
    assert iv[2] == [(4, 9)]


def test_inactive_display_changes_are_ignored():
    # Screen keeps changing (e.g. a video) but the cursor was never on this display.
    samples = [FrameSample(i * 0.1, thumb(30 if int(i * 0.1 / 2) % 2 == 0 else 220)) for i in range(200)]
    sel = select(samples, duration=20, active_intervals=[], include_start=False, include_end=False)
    assert sel == []


def test_display_switch_is_mandatory_when_user_stays():
    samples = [FrameSample(i * 0.1, thumb(80)) for i in range(200)]
    sel = select(samples, duration=20, active_intervals=[(5, 12)], include_start=False, include_end=False)
    assert [s.reasons for s in sel] == [["display switch"]]
    assert sel[0].mandatory
    assert sel[0].time == pytest.approx(5.4, abs=1e-9)
    # Just passing through (< 2 s) is optional only.
    brief = select(samples, duration=20, active_intervals=[(5, 6)], include_start=False, include_end=False)
    assert not (brief[0].mandatory if brief else False)


def test_annotations_kept_even_outside_active_intervals():
    a = ann(AnnotationTool.PIN, [(0, 0)], 3, 3, id=1, screen=1)
    sel = select([], [a], duration=10, active_intervals=[], include_start=False, include_end=False)
    assert [s.annotation_ids for s in sel] == [[1]]


def test_merge_keeps_mandatory_and_fills_by_score():
    a = [
        KeyframeSelection(0.3, ["start"], [], 50, True),
        KeyframeSelection(5, ["screen change"], [], 2),
        KeyframeSelection(8, ["speech"], [], 0.6),
    ]
    b = [
        KeyframeSelection(4, ["display switch"], [], 40, True),
        KeyframeSelection(6, ["screen change"], [], 3),
    ]
    merged = keyframes.merge([a, b], 4)
    assert [s.time for _, s in merged] == [0.3, 4, 5, 6]
    assert [d for d, _ in merged] == [0, 1, 0, 1]
    # Mandatory frames survive even a tiny budget.
    assert len(keyframes.merge([a, b], 1)) == 2


def test_display_label_in_markdown():
    frames = [
        FrameRef(1, 0, "/a.png", Size(10, 10), display="Built-in"),
        FrameRef(2, 3, "/b.png", Size(10, 10), display="DELL (2)"),
    ]
    doc = CaptureDocument(
        CaptureMode.WALKTHROUGH, datetime.now().astimezone(), 5, "/", frames, [], [], [], False
    )
    md = markdown.compose(doc)
    assert 'frame 2 · 00:03 · display "DELL (2)"' in md
    assert "several displays" in md

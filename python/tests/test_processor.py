"""The capture pipeline end to end, with synthetic screens and a scripted transcript."""

import json
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pytest
from PySide6.QtGui import QColor

from scribble import paths, processor, render, video
from scribble.audio import AudioResult
from scribble.core.geometry import Size
from scribble.core.models import (
    BLUE,
    RED,
    Annotation,
    AnnotationTool,
    CaptureMode,
    ClickEvent,
    FrameSample,
    SpeechSegment,
    Word,
)
from scribble.processor import CaptureInput, DisplayInfo, RecentCapture, RecordingResult, ShotData
from scribble.transcriber import TranscriptionOutput

sys.path.insert(0, str(Path(__file__).parent))
from helpers import FakeTranscriber, gradient_image, solid_frame, tone  # noqa: E402


def mark(tool, points, start, end, **kw):
    a = Annotation(tool=tool, points=points, color=kw.pop("color", RED), start_time=start, **kw)
    a.finalize(end)
    return a


def snapshot_input(folder, canvas=Size(1512, 982), scale=2.0, **kw):
    image = gradient_image(canvas, scale)
    return CaptureInput(
        mode=CaptureMode.SNAPSHOT,
        folder=folder,
        start_date=datetime(2026, 11, 2, 14, 3, 12).astimezone(),
        t0=100.0,
        duration=6.0,
        displays=[DisplayInfo(0, "Demo", canvas)],
        shots=[ShotData(image, 0.1, canvas, 0, 0, True)],
        audio=AudioResult(tone(6.0), 100.2),
        **kw,
    )


def test_snapshot_bundle(qapp, tmp_path):
    inp = snapshot_input(tmp_path)
    inp.annotations = [
        mark(AnnotationTool.ELLIPSE, [(290, 280), (434, 328)], 1.2, 1.6, id=1, number=1),
        mark(AnnotationTool.PEN, [(600, 600), (650, 640), (700, 600)], 2.0, 2.5, id=2),
        mark(AnnotationTool.PEN, [(620, 700), (700, 700)], 3.0, 3.4, id=3),
        mark(AnnotationTool.ARROW, [(430, 878), (780, 222)], 4.0, 4.5, id=4, number=2, color=BLUE),
    ]
    capture = processor.process(inp, FakeTranscriber(start=0.3, step=0.5))

    md = Path(capture.markdown_path).read_text(encoding="utf-8")
    frame = tmp_path / "frame_01.png"
    assert frame.is_file() and (tmp_path / "audio.m4a").is_file()
    image = render.load_image(frame)
    assert (image.width(), image.height()) == (1568, 1018)
    assert "- frame 1 · 00:00 · 1568×1018 · {}".format(frame) in md
    assert "The user took a screenshot and talked while drawing on it" in md
    # Audio started 0.2 s into the session, so word times are shifted onto the session clock,
    # and each mark lands at the word being spoken.
    assert "**[00:00 · frame 1]** so this [#1 circled (301,290)-(450,340) in frame 1] button" in md
    assert "[drawing (2 strokes) over (622,622)-(726,726) in frame 1]" in md
    assert "#2 arrow from (446,910) pointing at (809,230) in frame 1 · 00:04" in md
    assert md.rstrip().endswith("Folder: {}".format(tmp_path))
    assert "\r" not in md
    assert capture.annotation_count == 3 and capture.word_count == 6
    assert capture.summary == "1 frame · 3 annotations · 6 words"
    assert capture.image_paths == [str(frame)]

    events = json.loads((tmp_path / "events.json").read_text(encoding="utf-8"))
    assert events["version"] == 1 and events["mode"] == "snapshot"
    assert events["canvasSize"] == [1512, 982]
    assert events["frames"][0]["annotationIDs"] == [1, 2, 3, 4]
    assert events["frames"][0]["reasons"] == ["screenshot"]
    assert [a.get("number") for a in events["annotations"]] == [1, None, None, 2]
    assert events["words"][0]["start"] == pytest.approx(0.5)

    # The mark is burned into the image at the described pixels: the ellipse's left edge.
    r, g, b = (lambda c: (c.red(), c.green(), c.blue()))(image.pixelColor(301, 315))
    assert r > 200 and g < 120


def test_detail_crop_on_large_display(qapp, tmp_path):
    canvas = Size(3008, 1692)
    inp = snapshot_input(tmp_path, canvas=canvas, scale=1.0, keep_originals=True)
    inp.annotations = [mark(AnnotationTool.RECTANGLE, [(1000, 120), (1280, 260)], 1.0, 1.5, id=1, number=1)]
    capture = processor.process(inp, FakeTranscriber())
    md = Path(capture.markdown_path).read_text(encoding="utf-8")
    assert (tmp_path / "frame_01_detail.png").is_file() and (tmp_path / "frame_01_clean.png").is_file()
    assert "  - detail of frame 1, region " in md and "frame_01_detail.png" in md
    detail = render.load_image(tmp_path / "frame_01_detail.png")
    assert detail.width() >= 800
    assert capture.image_paths == [str(tmp_path / "frame_01.png"), str(tmp_path / "frame_01_detail.png")]
    assert capture.frame_paths == [str(tmp_path / "frame_01.png")]

    # No crop when the setting is off.
    other = tmp_path / "off"
    other.mkdir()
    inp2 = snapshot_input(other, canvas=canvas, scale=1.0, detail_crops=False)
    inp2.annotations = inp.annotations
    processor.process(inp2, FakeTranscriber())
    assert not (other / "frame_01_detail.png").exists()


def test_multi_shot_and_multi_display_ordering(qapp, tmp_path):
    canvas = Size(800, 600)
    inp = snapshot_input(tmp_path, canvas=canvas, scale=1.0)
    img = inp.shots[0].image
    inp.displays = [DisplayInfo(0, "Laptop", canvas), DisplayInfo(1, "DELL", canvas)]
    # Round 0: cursor on display 1; display 0 is drawn on too. Round 1: display 0 only.
    inp.shots = [
        ShotData(img, 0.1, canvas, 0, 0, False),
        ShotData(img, 0.1, canvas, 1, 0, True),
        ShotData(img, 3.0, canvas, 0, 1, True),
        ShotData(img, 3.0, canvas, 1, 1, False),
    ]
    inp.annotations = [
        mark(AnnotationTool.PIN, [(100, 100)], 1.0, 1.0, id=1, number=1, shot=0, screen=0),
        mark(AnnotationTool.PIN, [(200, 200)], 4.0, 4.0, id=2, number=2, shot=2, screen=0),
    ]
    assert processor.snapshot_frame_order(inp.shots, inp.annotations) == [1, 0, 2]
    capture = processor.process(inp, FakeTranscriber())
    md = Path(capture.markdown_path).read_text(encoding="utf-8")
    assert len(capture.frame_paths) == 3
    assert 'frame 1 · 00:00 · display "DELL"' in md and 'frame 2 · 00:00 · display "Laptop"' in md
    assert "**[00:00 · frames 1, 2]**" in md
    assert "#1 pin at (100,100) in frame 2" in md and "#2 pin at (200,200) in frame 3" in md
    assert "The user took 3 screenshots" in md and "several displays" in md


def test_no_microphone_and_failed_transcription(qapp, tmp_path):
    inp = snapshot_input(tmp_path)
    inp.audio = None
    fake = FakeTranscriber()
    capture = processor.process(inp, fake)
    assert fake.calls == 0 and capture.word_count == 0 and capture.note is None
    assert "_(no speech detected)_" in Path(capture.markdown_path).read_text(encoding="utf-8")
    assert not (tmp_path / "audio.m4a").exists()

    failed = tmp_path / "failed"
    failed.mkdir()
    inp2 = snapshot_input(failed)
    capture2 = processor.process(inp2, FakeTranscriber(fail=True))
    md = Path(capture2.markdown_path).read_text(encoding="utf-8")
    assert capture2.note == "Transcription failed: model exploded"
    assert md.endswith("\n_Transcription failed: model exploded_\n")
    # The frame is still there.
    assert (failed / "frame_01.png").is_file()


def record(folder: Path, name: str, t0: float, duration: float, level_at, size=(640, 400), fps=10):
    """Writes a synthetic screen recording and returns it with its live thumbnails."""
    path = folder / name
    writer = video.VideoWriter(path, size[0], size[1], fps=fps)
    samples = []
    first = t0 + 0.05
    for i in range(int(duration * fps)):
        t = i / fps
        frame = solid_frame(size[0], size[1], level_at(t))
        writer.write(frame, t)
        samples.append(FrameSample(first + t, video.thumbnail(frame)))
    writer.close()
    return RecordingResult(path, first, samples, Size(writer.width, writer.height))


def test_walkthrough_bundle(qapp, tmp_path):
    t0, duration = 50.0, 12.0
    canvas = Size(1280, 800)
    # View A (dark) until 5 s, then view B (bright).
    rec = record(tmp_path, "screen_display1.mp4", t0, duration, lambda t: 40 if t < 5 else 200)
    # Pinned while view A is up. (A pin on view B would make the plain "screen change"
    # frame redundant: the selector drops optional frames that look like a kept neighbour.)
    pin = mark(AnnotationTool.PIN, [(640, 400)], 2.0, 2.0, id=1, number=1)
    inp = CaptureInput(
        mode=CaptureMode.WALKTHROUGH,
        folder=tmp_path,
        start_date=datetime.now().astimezone(),
        t0=t0,
        duration=duration,
        displays=[DisplayInfo(0, "Main", canvas)],
        annotations=[pin],
        clicks=[ClickEvent(4.8, (320, 200), 0)],
        audio=AudioResult(tone(duration), t0 + 0.1),
        recordings={0: rec},
        cursor_timeline=[(0, 0)],
        legend_notes=["The dark pill is the recording HUD."],
    )
    capture = processor.process(
        inp, FakeTranscriber(text="first the old view then the new one", start=1.0, step=1.2)
    )
    processor.wait_for_background()

    md = Path(capture.markdown_path).read_text(encoding="utf-8")
    events = json.loads((tmp_path / "events.json").read_text(encoding="utf-8"))
    reasons = [f["reasons"] for f in events["frames"]]
    assert reasons == [["start"], ["annotation"], ["click", "screen change"]]
    assert "The frames below are keyframes from that recording." in md
    assert "Mouse clicks appear as [click at (x,y)]." in md and "[click at (160,100)]" in md
    assert "The dark pill is the recording HUD." in md
    assert "#1 pin at (320,200) in frame" in md

    # Each keyframe shows the screen as it was at its time: dark before the change, bright after.
    for f in events["frames"]:
        image = render.load_image(tmp_path / f["file"])
        assert (image.width(), image.height()) == (640, 400)
        level = image.pixelColor(320, 200).red()
        expected = 40 if f["time"] < 5 - 0.05 else 200
        assert abs(level - expected) < 12, "frame {} at {:.2f}s shows the wrong view".format(
            f["number"], f["time"]
        )

    # The raw video was muxed with the audio into recording.mp4.
    assert (tmp_path / "recording.mp4").is_file() and not (tmp_path / "screen_display1.mp4").exists()
    assert capture.mode == CaptureMode.WALKTHROUGH and len(capture.frame_paths) == len(events["frames"])


def test_walkthrough_two_displays_follow_the_cursor(qapp, tmp_path):
    t0, duration = 10.0, 12.0
    canvas = Size(640, 400)
    # Display 0 changes at 3 s (cursor is there); display 1 keeps changing but only gets the
    # cursor from 6 s on.
    rec0 = record(tmp_path, "screen_display1.mp4", t0, duration, lambda t: 40 if t < 3 else 200)
    rec1 = record(tmp_path, "screen_display2.mp4", t0, duration, lambda t: 30 if int(t / 2) % 2 == 0 else 220)
    inp = CaptureInput(
        mode=CaptureMode.WALKTHROUGH,
        folder=tmp_path,
        start_date=datetime.now().astimezone(),
        t0=t0,
        duration=duration,
        displays=[DisplayInfo(0, "Laptop", canvas), DisplayInfo(1, "DELL", canvas)],
        clicks=[ClickEvent(9.0, (100, 100), 1)],
        recordings={0: rec0, 1: rec1},
        cursor_timeline=[(0, 0), (6.0, 1)],
    )
    capture = processor.process(inp, FakeTranscriber())
    processor.wait_for_background()
    events = json.loads((tmp_path / "events.json").read_text(encoding="utf-8"))
    md = Path(capture.markdown_path).read_text(encoding="utf-8")
    frames = events["frames"]
    assert [f["number"] for f in frames] == list(range(1, len(frames) + 1))
    assert [f["time"] for f in frames] == sorted(f["time"] for f in frames)
    # Start on display 0; display 1 only appears once the cursor moved there.
    assert frames[0]["display"] == 0 and frames[0]["reasons"] == ["start"]
    assert all(f["time"] >= 5.0 for f in frames if f["display"] == 1)
    assert any("display switch" in f["reasons"] for f in frames if f["display"] == 1)
    assert 'display "DELL"' in md and "several displays" in md
    assert "[click at (100,100) in frame " in md
    # No audio: the per-display recordings are kept under their final names.
    assert (tmp_path / "recording_display1.mp4").is_file() and (tmp_path / "recording_display2.mp4").is_file()


def test_walkthrough_without_recording_still_writes_transcript(qapp, tmp_path):
    inp = CaptureInput(
        mode=CaptureMode.WALKTHROUGH,
        folder=tmp_path,
        start_date=datetime.now().astimezone(),
        t0=0.0,
        duration=4.0,
        displays=[DisplayInfo(0, "Main", Size(640, 400))],
        audio=AudioResult(tone(4.0), 0.0),
        recordings={0: RecordingResult(None, None, [], Size(0, 0))},
    )
    capture = processor.process(inp, FakeTranscriber())
    md = Path(capture.markdown_path).read_text(encoding="utf-8")
    assert "_(no frames captured)_" in md and "so this button" in md
    assert capture.frame_paths == []


def test_recent_capture_json_round_trip():
    capture = RecentCapture(
        "/c/x", datetime(2026, 11, 2, 13, 3, 12).astimezone(), CaptureMode.WALKTHROUGH, 47.2, "/c/x/capture.md",
        ["/c/x/frame_01.png", "/c/x/frame_01_detail.png"], ["/c/x/frame_01.png"], 142, 3, None,
    )  # fmt: skip
    again = RecentCapture.from_json(json.loads(json.dumps(capture.to_json())))
    assert again == capture
    assert again.summary == "1 frame · 3 annotations · 142 words"


class FinishedLive:
    """Stands in for ``live.LiveTranscriber``: the transcript made while recording."""

    def __init__(self, output):
        self.output, self.given = output, None

    def finish(self, samples):
        self.given = samples
        return self.output


def test_transcript_made_while_recording_is_used(qapp, tmp_path):
    made = TranscriptionOutput(
        words=[Word(" made", 0.5, 0.8), Word(" live", 0.9, 1.2)],
        segments=[SpeechSegment("made live", 0.5, 1.2)],
        warning="This model only understands English.",
    )
    live, whisper = FinishedLive(made), FakeTranscriber()
    capture = processor.process(snapshot_input(tmp_path, live=live), whisper)
    md = Path(capture.markdown_path).read_text(encoding="utf-8")
    assert "**[00:00 · frame 1]** made live" in md and whisper.calls == 0
    assert len(live.given) == 6 * 16_000
    # What may be wrong with the transcript reaches the user.
    assert capture.note == "This model only understands English."
    assert "_This model only understands English._" in md


def test_recording_is_transcribed_here_when_nothing_was_made_while_recording(qapp, tmp_path):
    live, whisper = FinishedLive(None), FakeTranscriber()
    capture = processor.process(snapshot_input(tmp_path, live=live), whisper)
    assert whisper.calls == 1 and capture.word_count == 6 and capture.note is None

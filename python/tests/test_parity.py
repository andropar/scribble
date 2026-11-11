"""Parity with the macOS app: fixtures/demo_* were written by the Swift pipeline (the README
demo capture). Fed the same timeline, this port must produce the same capture.md."""

import json
from datetime import timedelta, timezone

from scribble.core import describer, markdown
from scribble.core.geometry import CoordinateMapper, Size
from scribble.core.markdown import CaptureDocument, FrameRef, Marker
from scribble.core.models import Annotation, CaptureMode, Word

CET = timezone(timedelta(hours=1))


def test_capture_md_matches_swift_output(fixtures):
    events = json.loads((fixtures / "demo_events.json").read_text(encoding="utf-8"))
    folder = "/captures/demo"
    expected = (fixtures / "demo_capture.md").read_text(encoding="utf-8").replace("{folder}", folder)

    annotations = [Annotation.from_json(a) for a in events["annotations"]]
    words = [Word.from_json(w) for w in events["words"]]
    frame = events["frames"][0]
    image = Size(frame["width"], frame["height"])
    mapper = CoordinateMapper(Size.from_json(events["canvasSize"]), image)
    ref = FrameRef(frame["number"], frame["time"], "{}/{}".format(folder, frame["file"]), image)

    markers, lines = [], []
    for a in annotations:
        text = describer.describe(a, mapper, ref.number)
        markers.append(Marker(a.start_time, text))
        lines.append("{} · {}".format(text, markdown.clock(a.start_time)))

    doc = CaptureDocument(
        CaptureMode(events["mode"]),
        markdown.parse_iso8601(events["startDate"]),
        events["duration"],
        folder,
        [ref],
        words,
        markers,
        lines,
        False,
    )
    assert markdown.compose(doc, tz=CET) == expected


def test_annotation_json_round_trips_to_swift_schema(fixtures):
    events = json.loads((fixtures / "demo_events.json").read_text(encoding="utf-8"))
    for raw in events["annotations"]:
        assert Annotation.from_json(raw).to_json() == raw
    for raw in events["words"]:
        assert Word.from_json(raw).to_json() == raw

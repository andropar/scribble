"""Composes ``capture.md`` and the ``events.json`` timeline.

Mirrors ``Sources/VoiceAnnotateCore/MarkdownComposer.swift``: given the same inputs, the
markdown is byte-identical to the macOS app's.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone, tzinfo
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .geometry import Size
from .models import Annotation, CaptureMode, ClickEvent, Word, _num


@dataclass
class DetailRef:
    path: str
    pixel_size: Size
    #: Region of the parent frame this crop shows, in the parent frame's pixel coordinates.
    region_in_frame: str


@dataclass
class FrameRef:
    number: int
    #: Session time in seconds.
    time: float
    path: str
    pixel_size: Size
    detail: Optional[DetailRef] = None
    #: Display name, set when the capture spans several displays.
    display: Optional[str] = None


@dataclass
class Marker:
    """A bracketed inline reference in the transcript, e.g. an annotation or a click."""

    time: float
    text: str


@dataclass
class CaptureDocument:
    mode: CaptureMode
    start_date: datetime
    duration: float
    folder: str
    frames: List[FrameRef]
    words: List[Word]
    markers: List[Marker]
    #: One line per annotation for the summary list, e.g. "#1 circled … in frame 2 · 00:06".
    annotation_lines: List[str]
    has_clicks: bool
    #: Extra sentences for the legend (platform caveats such as a visible recording HUD).
    legend_notes: List[str] = field(default_factory=list)


@dataclass
class TranscriptParagraph:
    time: float
    #: Frames starting at this paragraph (several when displays were captured together).
    frames: List[int]
    text: str

    @property
    def frame(self) -> Optional[int]:
        return self.frames[0] if self.frames else None


#: Pause after which a new paragraph starts even without a new frame.
PARAGRAPH_PAUSE = 2.5


def clock(seconds: float) -> str:
    """``mm:ss`` (or ``h:mm:ss``) for a position in the capture."""
    s = max(0, int(math.floor(seconds)))
    if s >= 3600:
        return "{}:{:02d}:{:02d}".format(s // 3600, (s % 3600) // 60, s % 60)
    return "{:02d}:{:02d}".format(s // 60, s % 60)


def duration_text(seconds: float) -> str:
    """``m:ss`` for a duration."""
    s = max(0, int(math.floor(seconds + 0.5)))
    return "{}:{:02d}".format(s // 60, s % 60)


def _clean(s: str) -> str:
    t = s.strip()
    while "  " in t:
        t = t.replace("  ", " ")
    return t


def transcript_paragraphs(
    frames: Sequence[FrameRef], words: Sequence[Word], markers: Sequence[Marker]
) -> List[TranscriptParagraph]:
    """Interleaves words, markers and frame boundaries by time into paragraphs."""
    events: List[Tuple[float, int, str, Any]] = []
    for i, f in enumerate(sorted(frames, key=lambda f: f.time)):
        # Everything said before the first frame belongs to it.
        events.append((-math.inf if i == 0 else f.time, 0, "frame", f))
    for m in markers:
        events.append((m.time, 1, "marker", m))
    for w in words:
        events.append((w.start, 2, "word", w))
    events.sort(key=lambda e: (e[0], e[1]))

    paragraphs: List[TranscriptParagraph] = []
    current: Optional[TranscriptParagraph] = None
    last_word_end: Optional[float] = None

    def flush() -> None:
        nonlocal current
        if current is not None:
            current.text = _clean(current.text)
            paragraphs.append(current)
        current = None

    for _, _, kind, item in events:
        if kind == "frame":
            # Frames taken at the same moment (one per display) share a paragraph.
            if (
                current is not None
                and current.frames
                and not current.text
                and abs(current.time - item.time) < 0.05
            ):
                current.frames.append(item.number)
            else:
                flush()
                current = TranscriptParagraph(item.time, [item.number], "")
        elif kind == "marker":
            if current is None:
                current = TranscriptParagraph(item.time, [], "")
            current.text += " [{}]".format(item.text)
        else:
            pause = item.start - last_word_end if last_word_end is not None else 0
            if current is None or (pause > PARAGRAPH_PAUSE and current.text):
                flush()
                current = TranscriptParagraph(item.start, [], "")
            current.text += item.text if item.text.startswith(" ") else " " + item.text
            last_word_end = item.end
    flush()
    return paragraphs


def compose(doc: CaptureDocument, tz: Optional[tzinfo] = None) -> str:
    """Renders ``capture.md``. ``tz`` is the zone for the header date (default: local)."""
    out: List[str] = []
    start = doc.start_date.astimezone(tz) if doc.start_date.tzinfo else doc.start_date
    out.append(
        "# Screen capture · {} · {} · {}".format(
            doc.mode.title, duration_text(doc.duration), start.strftime("%Y-%m-%d %H:%M")
        )
    )
    out.append("")
    if doc.mode == CaptureMode.SNAPSHOT:
        shots = "a screenshot" if len(doc.frames) == 1 else "{} screenshots".format(len(doc.frames))
        out.append("The user took {} and talked while drawing on it (voice-annotate).".format(shots))
    elif doc.mode == CaptureMode.WALKTHROUGH:
        out.append(
            "The user recorded their screen while talking and using the computer (voice-annotate). "
            "The frames below are keyframes from that recording."
        )
    else:
        out.append("The user dictated this (voice-annotate).")
    displays = {f.display for f in doc.frames if f.display is not None}
    if len(displays) > 1:
        out.append("The user has several displays; each frame says which display it shows.")
    legend = (
        "Marks in the images are the user's own: numbered ones (#1, #2, …) point at something, "
        "unnumbered pen strokes are freehand drawing. Each is referenced in the transcript at the "
        "moment it was drawn."
    )
    if doc.has_clicks:
        legend += " Mouse clicks appear as [click at (x,y)]."
    legend += " Coordinates are pixels in the referenced image, origin top-left."
    for note in doc.legend_notes:
        legend += " " + note
    out.append(legend)
    out.append("")

    if not doc.frames:
        out.append("_(no frames captured)_")
    else:
        out.append("Screenshots:" if doc.mode == CaptureMode.SNAPSHOT else "Frames:")
        for f in doc.frames:
            display = ' · display "{}"'.format(f.display) if f.display is not None else ""
            out.append(
                "- frame {} · {}{} · {}×{} · {}".format(
                    f.number,
                    clock(f.time),
                    display,
                    int(f.pixel_size.width),
                    int(f.pixel_size.height),
                    f.path,
                )
            )
            if f.detail is not None:
                d = f.detail
                out.append(
                    "  - detail of frame {}, region {}, {}×{}: {}".format(
                        f.number, d.region_in_frame, int(d.pixel_size.width), int(d.pixel_size.height), d.path
                    )
                )
    out.append("")
    out.append("## Transcript")
    out.append("")

    paragraphs = transcript_paragraphs(doc.frames, doc.words, doc.markers)
    if not doc.words:
        out.append("_(no speech detected)_")
        out.append("")
    for p in paragraphs:
        header = "**[{}".format(clock(p.time))
        if len(p.frames) == 1:
            header += " · frame {}".format(p.frames[0])
        elif p.frames:
            header += " · frames " + ", ".join(str(n) for n in p.frames)
        header += "]**"
        out.append("{} {}".format(header, p.text) if p.text else header)
        out.append("")

    if doc.annotation_lines:
        out.append("## Annotations")
        out.append("")
        for line in doc.annotation_lines:
            out.append("- {}".format(line))
        out.append("")
    out.append("Folder: {}".format(doc.folder))
    return "\n".join(out) + "\n"


def iso8601(date: datetime) -> str:
    """UTC timestamp without fractional seconds, as Swift's ``.iso8601`` strategy writes it."""
    if date.tzinfo is None:
        date = date.astimezone()
    return date.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso8601(text: str) -> datetime:
    return datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


@dataclass
class EventsFrame:
    number: int
    time: float
    file: str
    width: int
    height: int
    reasons: List[str]
    annotation_ids: List[int]
    detail_file: Optional[str]
    display: int

    def to_json(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "number": self.number,
            "time": _num(self.time),
            "file": self.file,
            "width": self.width,
            "height": self.height,
            "reasons": list(self.reasons),
            "annotationIDs": list(self.annotation_ids),
            "display": self.display,
        }
        if self.detail_file is not None:
            d["detailFile"] = self.detail_file
        return d


@dataclass
class EventsDisplay:
    index: int
    name: str
    #: Display size in points: the coordinate space of annotation and click geometry.
    canvas_size: Size

    def to_json(self) -> Dict[str, Any]:
        return {
            "index": self.index,
            "name": self.name,
            "canvasSize": [_num(self.canvas_size.width), _num(self.canvas_size.height)],
        }


@dataclass
class EventsLog:
    """Machine-readable timeline written next to ``capture.md``."""

    mode: CaptureMode
    start_date: datetime
    duration: float
    #: Size of the captured display in points (the coordinate space of annotation geometry).
    canvas_size: Size
    displays: List[EventsDisplay]
    frames: List[EventsFrame]
    words: List[Word]
    annotations: List[Annotation]
    clicks: List[ClickEvent]
    version: int = 1

    def to_json(self) -> Dict[str, Any]:
        return {
            "version": self.version,
            "mode": self.mode.value,
            "startDate": iso8601(self.start_date),
            "duration": _num(self.duration),
            "canvasSize": [_num(self.canvas_size.width), _num(self.canvas_size.height)],
            "displays": [d.to_json() for d in self.displays],
            "frames": [f.to_json() for f in self.frames],
            "words": [w.to_json() for w in self.words],
            "annotations": [a.to_json() for a in self.annotations],
            "clicks": [c.to_json() for c in self.clicks],
        }

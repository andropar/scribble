"""Capture data model, mirroring ``Sources/VoiceAnnotateCore/Models.swift``.

The JSON written by ``to_json`` matches what the macOS app's ``JSONEncoder`` produces, so
``events.json`` has one schema on every platform.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

import numpy as np

from .geometry import Point, Rect, ZERO_RECT


class CaptureMode(str, Enum):
    SNAPSHOT = "snapshot"
    WALKTHROUGH = "walkthrough"
    #: Voice only: the transcript goes to the clipboard as plain text, no bundle markdown.
    DICTATION = "dictation"

    @property
    def title(self) -> str:
        return {"snapshot": "Snapshot", "walkthrough": "Walkthrough", "dictation": "Dictation"}[self.value]


class AnnotationTool(str, Enum):
    PEN = "pen"
    ARROW = "arrow"
    RECTANGLE = "rectangle"
    ELLIPSE = "ellipse"
    HIGHLIGHTER = "highlighter"
    PIN = "pin"

    @property
    def title(self) -> str:
        return {
            "pen": "Pen",
            "arrow": "Arrow",
            "rectangle": "Box",
            "ellipse": "Ellipse",
            "highlighter": "Highlighter",
            "pin": "Pin",
        }[self.value]

    @property
    def shortcut(self) -> str:
        """Single-key shortcut used in Snapshot mode."""
        return {"pen": "p", "arrow": "a", "rectangle": "r", "ellipse": "o", "highlighter": "h", "pin": "n"}[
            self.value
        ]

    @property
    def default_line_width(self) -> float:
        return {"pen": 4, "arrow": 4, "rectangle": 3.5, "ellipse": 3.5, "highlighter": 20, "pin": 0}[
            self.value
        ]

    @property
    def is_two_point(self) -> bool:
        """Tools whose geometry is defined by a start and an end point only."""
        return self in (AnnotationTool.ARROW, AnnotationTool.RECTANGLE, AnnotationTool.ELLIPSE)


class AnnotationShape(str, Enum):
    """Semantic shape of a finished annotation (what the agent is told it is)."""

    FREEHAND = "freehand"
    ARROW = "arrow"
    BOX = "box"
    ELLIPSE = "ellipse"
    HIGHLIGHT = "highlight"
    PIN = "pin"


_DEFAULT_SHAPES = {
    AnnotationTool.PEN: AnnotationShape.FREEHAND,
    AnnotationTool.ARROW: AnnotationShape.ARROW,
    AnnotationTool.RECTANGLE: AnnotationShape.BOX,
    AnnotationTool.ELLIPSE: AnnotationShape.ELLIPSE,
    AnnotationTool.HIGHLIGHTER: AnnotationShape.HIGHLIGHT,
    AnnotationTool.PIN: AnnotationShape.PIN,
}


def default_shape(tool: AnnotationTool) -> AnnotationShape:
    """Pen strokes are never reinterpreted: they stay freehand drawings."""
    return _DEFAULT_SHAPES[tool]


def _num(x: float) -> Any:
    """Integral floats as ints, the way Swift's JSONEncoder writes them."""
    return int(x) if float(x).is_integer() else float(x)


@dataclass(frozen=True)
class RGBAColor:
    r: float
    g: float
    b: float
    a: float = 1.0

    def with_alpha(self, alpha: float) -> "RGBAColor":
        return RGBAColor(self.r, self.g, self.b, alpha)

    @property
    def luminance(self) -> float:
        """Relative luminance, used to pick a readable badge text color."""
        return 0.2126 * self.r + 0.7152 * self.g + 0.0722 * self.b

    def to_json(self) -> Dict[str, Any]:
        return {"r": _num(self.r), "g": _num(self.g), "b": _num(self.b), "a": _num(self.a)}

    @staticmethod
    def from_json(d: Dict[str, Any]) -> "RGBAColor":
        return RGBAColor(float(d["r"]), float(d["g"]), float(d["b"]), float(d.get("a", 1)))


RED = RGBAColor(1.0, 0.231, 0.188)
YELLOW = RGBAColor(1.0, 0.8, 0.0)
GREEN = RGBAColor(0.204, 0.78, 0.349)
BLUE = RGBAColor(0.039, 0.518, 1.0)
MAGENTA = RGBAColor(1.0, 0.176, 0.573)
WHITE = RGBAColor(1.0, 1.0, 1.0)

PALETTE: List[RGBAColor] = [RED, YELLOW, GREEN, BLUE, MAGENTA, WHITE]
COLOR_NAMES: Dict[str, RGBAColor] = {
    "red": RED,
    "yellow": YELLOW,
    "green": GREEN,
    "blue": BLUE,
    "magenta": MAGENTA,
    "white": WHITE,
}

PIN_RADIUS = 13.0


@dataclass
class Annotation:
    """One user annotation. Geometry is in canvas points (origin top-left)."""

    tool: AnnotationTool
    points: List[Point]
    color: RGBAColor
    start_time: float
    #: Unique within a capture (0 while in progress). Not shown; see ``number``.
    id: int = 0
    #: Number shown as a badge and cited in the transcript (#1, #2, …). Pen strokes are
    #: drawings and stay unnumbered.
    number: Optional[int] = None
    line_width: Optional[float] = None
    end_time: Optional[float] = None
    #: Snapshot shot index this annotation belongs to (always 0 in Walkthrough).
    shot: int = 0
    #: Index of the display (in the capture's display list) the annotation was drawn on.
    screen: int = 0
    shape: AnnotationShape = field(init=False)

    def __post_init__(self) -> None:
        self.shape = default_shape(self.tool)
        if self.line_width is None:
            self.line_width = float(self.tool.default_line_width)
        if self.end_time is None:
            self.end_time = self.start_time

    def finalize(self, end_time: float) -> None:
        self.end_time = end_time

    @property
    def is_drawing(self) -> bool:
        """Pen strokes are freehand drawing: never reinterpreted as shapes, never numbered."""
        return self.tool == AnnotationTool.PEN

    @property
    def bounds(self) -> Rect:
        if not self.points:
            return ZERO_RECT
        first = self.points[0]
        if self.tool == AnnotationTool.PIN:
            r = PIN_RADIUS
            return Rect(first[0] - r, first[1] - r, 2 * r, 2 * r)
        xs = [p[0] for p in self.points]
        ys = [p[1] for p in self.points]
        return Rect(min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys))

    def to_json(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "id": self.id,
            "tool": self.tool.value,
            "shape": self.shape.value,
            "points": [[_num(p[0]), _num(p[1])] for p in self.points],
            "color": self.color.to_json(),
            "lineWidth": _num(self.line_width or 0),
            "startTime": _num(self.start_time),
            "endTime": _num(self.end_time if self.end_time is not None else self.start_time),
            "shot": self.shot,
            "screen": self.screen,
        }
        if self.number is not None:
            d["number"] = self.number
        return d

    @staticmethod
    def from_json(d: Dict[str, Any]) -> "Annotation":
        return Annotation(
            tool=AnnotationTool(d["tool"]),
            points=[(float(p[0]), float(p[1])) for p in d["points"]],
            color=RGBAColor.from_json(d["color"]),
            start_time=float(d["startTime"]),
            id=int(d.get("id", 0)),
            number=d.get("number"),
            line_width=float(d["lineWidth"]) if "lineWidth" in d else None,
            end_time=float(d["endTime"]) if "endTime" in d else None,
            shot=int(d.get("shot", 0)),
            screen=int(d.get("screen", 0)),
        )


@dataclass
class ClickEvent:
    #: Session time in seconds.
    time: float
    #: Canvas points, origin top-left of the captured display.
    location: Point
    #: Index of the display (in the capture's display list) that was clicked.
    screen: int = 0

    def to_json(self) -> Dict[str, Any]:
        return {
            "time": _num(self.time),
            "location": [_num(self.location[0]), _num(self.location[1])],
            "screen": self.screen,
        }


@dataclass
class Word:
    text: str
    #: Session time in seconds.
    start: float
    end: float

    def to_json(self) -> Dict[str, Any]:
        return {"text": self.text, "start": _num(self.start), "end": _num(self.end)}

    @staticmethod
    def from_json(d: Dict[str, Any]) -> "Word":
        return Word(d["text"], float(d["start"]), float(d["end"]))


@dataclass
class SpeechSegment:
    text: str
    start: float
    end: float


@dataclass
class FrameSample:
    """Luminance thumbnail of one captured video frame, used for keyframe selection."""

    #: Session time in seconds.
    time: float
    thumb: np.ndarray

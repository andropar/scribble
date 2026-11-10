"""Textual references for annotations, e.g. ``#3 circled (412,230)-(640,310) in frame 2``.

Mirrors ``Sources/VoiceAnnotateCore/AnnotationDescriber.swift``.
"""

from __future__ import annotations

from typing import Callable, List, Optional, Sequence, Tuple

from .geometry import NULL_RECT, CoordinateMapper
from .models import Annotation, AnnotationShape, ClickEvent


def _pt(p: Tuple[int, int]) -> str:
    return "({},{})".format(p[0], p[1])


def _rect(r: Tuple[int, int, int, int]) -> str:
    return "({},{})-({},{})".format(r[0], r[1], r[2], r[3])


def _label(body: str, number: Optional[int], frame: Optional[int]) -> str:
    text = "#{} {}".format(number, body) if number is not None else body
    if frame is not None:
        text += " in frame {}".format(frame)
    return text


def describe(a: Annotation, mapper: CoordinateMapper, frame: Optional[int]) -> str:
    first = a.points[0] if a.points else (0.0, 0.0)
    if a.shape == AnnotationShape.PIN:
        body = "pin at {}".format(_pt(mapper.map_point(first)))
    elif a.shape == AnnotationShape.ARROW:
        tip = a.points[-1] if a.points else first
        body = "arrow from {} pointing at {}".format(_pt(mapper.map_point(first)), _pt(mapper.map_point(tip)))
    elif a.shape == AnnotationShape.ELLIPSE:
        body = "circled {}".format(_rect(mapper.map_rect(a.bounds)))
    elif a.shape == AnnotationShape.BOX:
        body = "box around {}".format(_rect(mapper.map_rect(a.bounds)))
    elif a.shape == AnnotationShape.HIGHLIGHT:
        body = "highlighted {}".format(_rect(mapper.map_rect(a.bounds)))
    else:
        body = "drawing over {}".format(_rect(mapper.map_rect(a.bounds)))
    return _label(body, a.number, frame)


def describe_drawing(strokes: Sequence[Annotation], mapper: CoordinateMapper, frame: Optional[int]) -> str:
    """One reference for a burst of pen strokes (see ``drawing_bursts``); no number."""
    bounds = NULL_RECT
    for s in strokes:
        bounds = bounds.union(s.bounds)
    count = " ({} strokes)".format(len(strokes)) if len(strokes) > 1 else ""
    return _label("drawing{} over {}".format(count, _rect(mapper.map_rect(bounds))), None, frame)


def drawing_bursts(
    strokes: Sequence[Annotation],
    frame_of: Callable[[Annotation], Optional[int]],
    max_gap: float = 1.5,
) -> List[Tuple[int, List[Annotation]]]:
    """Groups pen strokes into bursts: consecutive strokes on the same frame that start
    within ``max_gap`` seconds of the previous one ending. Strokes without a frame are
    dropped. Returns ``(frame, strokes)`` pairs."""
    bursts: List[Tuple[int, List[Annotation]]] = []
    for a in sorted(strokes, key=lambda s: s.start_time):
        frame = frame_of(a)
        if frame is None:
            continue
        if bursts:
            last_frame, last_strokes = bursts[-1]
            prev = last_strokes[-1]
            if last_frame == frame and a.start_time - (prev.end_time or prev.start_time) <= max_gap:
                last_strokes.append(a)
                continue
        bursts.append((frame, [a]))
    return bursts


def describe_click(c: ClickEvent, mapper: CoordinateMapper) -> str:
    return "click at {}".format(_pt(mapper.map_point(c.location)))

"""Geometry helpers and the canvas → image coordinate mapping.

Canvas coordinates are display points (device-independent pixels), origin top-left. This
module mirrors ``Sources/VoiceAnnotateCore/Geometry.swift``, including CoreGraphics'
rounding rules, so both apps describe the same mark with the same numbers.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

Point = Tuple[float, float]


def round_half_away(x: float) -> float:
    """Swift's ``rounded()``: halves round away from zero (Python's ``round`` uses bankers')."""
    return math.floor(x + 0.5) if x >= 0 else -math.floor(-x + 0.5)


@dataclass(frozen=True)
class Size:
    width: float
    height: float

    def to_json(self) -> List[float]:
        return [self.width, self.height]

    @staticmethod
    def from_json(value: Sequence[float]) -> "Size":
        return Size(float(value[0]), float(value[1]))


@dataclass(frozen=True)
class Rect:
    x: float
    y: float
    width: float
    height: float

    @property
    def min_x(self) -> float:
        return self.x

    @property
    def min_y(self) -> float:
        return self.y

    @property
    def max_x(self) -> float:
        return self.x + self.width

    @property
    def max_y(self) -> float:
        return self.y + self.height

    @property
    def mid_x(self) -> float:
        return self.x + self.width / 2

    @property
    def mid_y(self) -> float:
        return self.y + self.height / 2

    @property
    def is_null(self) -> bool:
        return math.isinf(self.x) or math.isinf(self.y)

    def union(self, other: "Rect") -> "Rect":
        if self.is_null:
            return other
        if other.is_null:
            return self
        x1, y1 = min(self.min_x, other.min_x), min(self.min_y, other.min_y)
        x2, y2 = max(self.max_x, other.max_x), max(self.max_y, other.max_y)
        return Rect(x1, y1, x2 - x1, y2 - y1)

    def inset_by(self, dx: float, dy: float) -> "Rect":
        return Rect(self.x + dx, self.y + dy, self.width - 2 * dx, self.height - 2 * dy)

    def integral(self) -> "Rect":
        """Smallest rect with integer edges that contains this one."""
        x1, y1 = math.floor(self.min_x), math.floor(self.min_y)
        x2, y2 = math.ceil(self.max_x), math.ceil(self.max_y)
        return Rect(x1, y1, x2 - x1, y2 - y1)

    def intersection(self, other: "Rect") -> "Rect":
        x1, y1 = max(self.min_x, other.min_x), max(self.min_y, other.min_y)
        x2, y2 = min(self.max_x, other.max_x), min(self.max_y, other.max_y)
        if x2 < x1 or y2 < y1:
            return NULL_RECT
        return Rect(x1, y1, x2 - x1, y2 - y1)

    def contains(self, other: "Rect") -> bool:
        return (
            other.min_x >= self.min_x
            and other.min_y >= self.min_y
            and other.max_x <= self.max_x
            and other.max_y <= self.max_y
        )

    def contains_point(self, p: Point) -> bool:
        return self.min_x <= p[0] < self.max_x and self.min_y <= p[1] < self.max_y


NULL_RECT = Rect(math.inf, math.inf, 0, 0)
ZERO_RECT = Rect(0, 0, 0, 0)


def path_length(points: Sequence[Point]) -> float:
    total = 0.0
    for i in range(1, len(points)):
        total += math.hypot(points[i][0] - points[i - 1][0], points[i][1] - points[i - 1][1])
    return total


@dataclass(frozen=True)
class CoordinateMapper:
    """Maps canvas points to output image pixels."""

    canvas_size: Size
    image_size: Size
    #: Canvas region that the image shows (the full canvas unless it is a detail crop).
    canvas_region: Optional[Rect] = None

    @property
    def region(self) -> Rect:
        if self.canvas_region is not None:
            return self.canvas_region
        return Rect(0, 0, self.canvas_size.width, self.canvas_size.height)

    @property
    def scale_x(self) -> float:
        return self.image_size.width / max(self.region.width, 1)

    @property
    def scale_y(self) -> float:
        return self.image_size.height / max(self.region.height, 1)

    def map_point(self, p: Point) -> Tuple[int, int]:
        region = self.region
        x = round_half_away((p[0] - region.min_x) * self.scale_x)
        y = round_half_away((p[1] - region.min_y) * self.scale_y)
        return (
            int(min(max(x, 0), self.image_size.width - 1)),
            int(min(max(y, 0), self.image_size.height - 1)),
        )

    def map_rect(self, r: Rect) -> Tuple[int, int, int, int]:
        a = self.map_point((r.min_x, r.min_y))
        b = self.map_point((r.max_x, r.max_y))
        return (a[0], a[1], b[0], b[1])

    def describe(self, r: Rect) -> str:
        """``(x1,y1)-(x2,y2)`` in image pixels."""
        x1, y1, x2, y2 = self.map_rect(r)
        return "({},{})-({},{})".format(x1, y1, x2, y2)


def fit_size(size: Size, long_edge: int) -> Size:
    """Size that fits ``size`` into ``long_edge`` pixels on its longest side (never upscales)."""
    longest = max(size.width, size.height)
    if not (longest > long_edge and longest > 0):
        return Size(round_half_away(size.width), round_half_away(size.height))
    s = long_edge / longest
    return Size(max(1, round_half_away(size.width * s)), max(1, round_half_away(size.height * s)))


@dataclass
class CropConfig:
    #: Only crop when the full frame ends up with fewer output pixels per canvas point.
    min_pixels_per_point: float = 0.8
    padding: float = 100
    min_size: Size = Size(800, 500)
    #: Skip the crop if it would cover this much of the canvas in both dimensions anyway.
    max_coverage: float = 0.7


def detail_rect(
    regions: Sequence[Rect],
    canvas_size: Size,
    full_frame_pixel_width: float,
    config: Optional[CropConfig] = None,
) -> Optional[Rect]:
    """Canvas rect of a "detail" crop around annotations on very large displays, where a
    downscaled full frame would make UI text illegible. None if no crop is worthwhile."""
    config = config or CropConfig()
    if not regions or canvas_size.width <= 0 or canvas_size.height <= 0:
        return None
    if not (full_frame_pixel_width / canvas_size.width < config.min_pixels_per_point):
        return None

    union = regions[0]
    for r in regions[1:]:
        union = union.union(r)
    rect = union.inset_by(-config.padding, -config.padding)

    w = min(max(rect.width, config.min_size.width), canvas_size.width)
    h = min(max(rect.height, config.min_size.height), canvas_size.height)
    x, y = rect.mid_x - w / 2, rect.mid_y - h / 2

    # Shift back inside the canvas.
    if x < 0:
        x = 0
    if y < 0:
        y = 0
    if x + w > canvas_size.width:
        x = canvas_size.width - w
    if y + h > canvas_size.height:
        y = canvas_size.height - h
    rect = Rect(x, y, w, h).integral().intersection(Rect(0, 0, canvas_size.width, canvas_size.height))

    if (
        rect.width >= config.max_coverage * canvas_size.width
        and rect.height >= config.max_coverage * canvas_size.height
    ):
        return None
    return rect

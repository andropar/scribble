"""Draws annotations with QPainter (y-down coordinates, like the canvas).

Shared by the live overlay and offline rendering so both look identical. A port of
``Sources/VoiceAnnotateCore/AnnotationRenderer.swift``; CoreGraphics' blurred shadow is
approximated with two soft dark strokes underneath each mark.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import List, Optional, Sequence, Tuple, Union

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QImage, QPainter, QPainterPath, QPen, QPolygonF

from .core.geometry import Point, Rect, Size
from .core.models import PIN_RADIUS, Annotation, AnnotationTool, RGBAColor

BADGE_RADIUS = 11.0
#: Minimum output pixels per point for strokes and badges.
MIN_MARK_SCALE = 1.0

# (extra width, alpha) of the strokes that stand in for a blurred drop shadow.
_SHADOW_PASSES = ((5.0, 0.10), (2.5, 0.20))


def qcolor(c: RGBAColor) -> QColor:
    return QColor.fromRgbF(c.r, c.g, c.b, c.a)


def _rect(a: QPointF, b: QPointF) -> QRectF:
    return QRectF(min(a.x(), b.x()), min(a.y(), b.y()), abs(a.x() - b.x()), abs(a.y() - b.y()))


def _smooth_path(pts: Sequence[QPointF]) -> QPainterPath:
    path = QPainterPath(pts[0])
    if len(pts) == 2:
        path.lineTo(pts[1])
    else:
        for i in range(1, len(pts) - 1):
            mid = QPointF((pts[i].x() + pts[i + 1].x()) / 2, (pts[i].y() + pts[i + 1].y()) / 2)
            path.quadTo(pts[i], mid)
        path.lineTo(pts[-1])
    return path


def _stroke(painter: QPainter, path: QPainterPath, color: QColor, width: float, cap=Qt.RoundCap) -> None:
    pen = QPen(color, width)
    pen.setCapStyle(cap)
    pen.setJoinStyle(Qt.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.NoBrush)
    painter.drawPath(path)


def _stroke_with_shadow(painter: QPainter, path: QPainterPath, color: QColor, width: float, m: float) -> None:
    for extra, alpha in _SHADOW_PASSES:
        _stroke(painter, path.translated(0, 1 * m), QColor(0, 0, 0, int(255 * alpha)), width + extra * m)
    _stroke(painter, path, color, width)


def _arrow_paths(tail: QPointF, tip: QPointF, width: float) -> Optional[Tuple[QPainterPath, QPolygonF]]:
    dx, dy = tip.x() - tail.x(), tip.y() - tail.y()
    length = math.hypot(dx, dy)
    if not length > 0.5:
        return None
    ux, uy = dx / length, dy / length
    head_len = min(max(width * 4, 14), length * 0.6)
    head_half = head_len * 0.55
    base = QPointF(tip.x() - ux * head_len, tip.y() - uy * head_len)
    line_end = QPointF(tip.x() - ux * head_len * 0.6, tip.y() - uy * head_len * 0.6)
    shaft = QPainterPath(tail)
    shaft.lineTo(line_end)
    head = QPolygonF(
        [
            tip,
            QPointF(base.x() - uy * head_half, base.y() + ux * head_half),
            QPointF(base.x() + uy * head_half, base.y() - ux * head_half),
        ]
    )
    return shaft, head


def badge_center(a: Annotation, mark_factor: float = 1.0) -> Point:
    """Badge position in canvas points: at the arrow's tail, else outside the top-left
    corner. ``mark_factor`` = mark size relative to geometry (see ``draw_annotation``)."""
    offset = (BADGE_RADIUS + 4) * mark_factor
    if a.tool == AnnotationTool.ARROW and a.points:
        tail, tip = a.points[0], a.points[-1]
        dx, dy = tail[0] - tip[0], tail[1] - tip[1]
        length = max(math.hypot(dx, dy), 0.001)
        return (tail[0] + dx / length * offset, tail[1] + dy / length * offset)
    b = a.bounds
    pad = (a.line_width or 0) / 2 * mark_factor if a.tool == AnnotationTool.HIGHLIGHTER else 0
    return (b.min_x - pad - offset * 0.7, b.min_y - pad - offset * 0.7)


def annotation_extent(a: Annotation) -> Rect:
    """Canvas region an annotation occupies including its badge."""
    half = (a.line_width or 0) / 2
    r = a.bounds.inset_by(-half, -half)
    if a.tool != AnnotationTool.PIN and a.number is not None:
        cx, cy = badge_center(a)
        r = r.union(Rect(cx - BADGE_RADIUS, cy - BADGE_RADIUS, 2 * BADGE_RADIUS, 2 * BADGE_RADIUS))
    return r


def _draw_badge(
    painter: QPainter, number: int, center: QPointF, radius: float, color: RGBAColor, s: float
) -> None:
    painter.save()
    painter.setPen(Qt.NoPen)
    for extra, alpha in _SHADOW_PASSES:
        painter.setBrush(QColor(0, 0, 0, int(255 * alpha)))
        painter.drawEllipse(
            QPointF(center.x(), center.y() + 1 * s), radius + extra * s / 2, radius + extra * s / 2
        )
    painter.setBrush(qcolor(color))
    painter.drawEllipse(center, radius, radius)
    ring = QPen(QColor(255, 255, 255), 2 * s)
    painter.setPen(ring)
    painter.setBrush(Qt.NoBrush)
    painter.drawEllipse(center, radius - 1 * s, radius - 1 * s)
    if number > 0:
        text = str(number)
        text_color = QColor(0, 0, 0) if color.luminance > 0.6 else QColor(255, 255, 255)
        draw_centered_text(painter, text, center, radius * (1.0 if len(text) > 1 else 1.2), text_color)
    painter.restore()


def draw_centered_text(painter: QPainter, text: str, c: QPointF, font_size: float, color: QColor) -> None:
    font = QFont(painter.font())
    font.setBold(True)
    font.setPixelSize(max(1, int(round(font_size))))
    painter.setFont(font)
    metrics = QFontMetricsF(font)
    width = metrics.horizontalAdvance(text)
    painter.setPen(color)
    painter.drawText(QPointF(c.x() - width / 2, c.y() + (metrics.capHeight()) / 2), text)


def draw_annotation(
    painter: QPainter,
    a: Annotation,
    scale: float = 1.0,
    mark_scale: Optional[float] = None,
    alpha: float = 1.0,
    show_badge: bool = True,
    canvas_size: Optional[Size] = None,
) -> None:
    """Draws one annotation.

    ``scale``: canvas points → painter units. ``mark_scale``: size of strokes and badges in
    painter units per point (defaults to ``scale``; a larger value keeps marks legible in
    strongly downscaled images). ``canvas_size``: if given, badges are kept inside it.
    """
    if not (alpha > 0) or not a.points:
        return
    s = scale
    m = mark_scale if mark_scale is not None else s
    painter.save()
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.setOpacity(painter.opacity() * alpha)

    pts = [QPointF(p[0] * s, p[1] * s) for p in a.points]
    lw = float(a.line_width or 0) * m
    color = qcolor(a.color)

    if a.tool == AnnotationTool.PEN:
        if len(pts) == 1:
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(0, 0, 0, 50))
            painter.drawEllipse(QPointF(pts[0].x(), pts[0].y() + 1 * m), lw / 2 + m, lw / 2 + m)
            painter.setBrush(color)
            painter.drawEllipse(pts[0], lw / 2, lw / 2)
        else:
            _stroke_with_shadow(painter, _smooth_path(pts), color, lw, m)
    elif a.tool == AnnotationTool.HIGHLIGHTER:
        if len(pts) > 1:
            _stroke(painter, _smooth_path(pts), qcolor(a.color.with_alpha(0.38)), lw, cap=Qt.FlatCap)
    elif a.tool == AnnotationTool.ARROW:
        arrow = _arrow_paths(pts[0], pts[-1], lw)
        if arrow is not None:
            shaft, head = arrow
            head_path = QPainterPath()
            head_path.addPolygon(head)
            head_path.closeSubpath()
            for extra, shadow_alpha in _SHADOW_PASSES:
                shadow = QColor(0, 0, 0, int(255 * shadow_alpha))
                _stroke(painter, shaft.translated(0, 1 * m), shadow, lw + extra * m)
                _stroke(painter, head_path.translated(0, 1 * m), shadow, extra * m)
            _stroke(painter, shaft, color, lw)
            painter.setPen(Qt.NoPen)
            painter.setBrush(color)
            painter.drawPolygon(head)
    elif a.tool == AnnotationTool.RECTANGLE:
        r = _rect(pts[0], pts[-1])
        path = QPainterPath()
        path.addRoundedRect(r, min(4 * m, r.width() / 2), min(4 * m, r.height() / 2))
        _stroke_with_shadow(painter, path, color, lw, m)
    elif a.tool == AnnotationTool.ELLIPSE:
        path = QPainterPath()
        path.addEllipse(_rect(pts[0], pts[-1]))
        _stroke_with_shadow(painter, path, color, lw, m)
    elif a.tool == AnnotationTool.PIN:
        _draw_badge(painter, a.number or 0, pts[0], PIN_RADIUS * m, a.color, m)
        painter.restore()
        return

    if show_badge and a.number is not None:
        cx, cy = badge_center(a, mark_factor=m / s)
        if canvas_size is not None:
            r = (BADGE_RADIUS + 2) * (m / s)
            cx = min(max(cx, r), canvas_size.width - r)
            cy = min(max(cy, r), canvas_size.height - r)
        _draw_badge(painter, a.number, QPointF(cx * s, cy * s), BADGE_RADIUS * m, a.color, m)
    painter.restore()


# MARK: offline image rendering


def new_image(width: int, height: int) -> QImage:
    image = QImage(width, height, QImage.Format_RGB32)
    image.fill(QColor(0, 0, 0))
    return image


def image_from_bgra(array: np.ndarray) -> QImage:
    """Wraps a ``(h, w, 4)`` BGRA array (as screen grabbers and the video decoder deliver
    it) into an owned QImage; the alpha channel is ignored."""
    h, w = array.shape[0], array.shape[1]
    data = np.ascontiguousarray(array)
    return QImage(data.data, w, h, w * 4, QImage.Format_RGB32).copy()


def image_to_bgra(image: QImage) -> np.ndarray:
    """``(h, w, 4)`` BGRA copy of an image."""
    img = image.convertToFormat(QImage.Format_RGB32)
    w, h = img.width(), img.height()
    buf = np.frombuffer(img.constBits(), dtype=np.uint8, count=img.sizeInBytes())
    return buf.reshape(h, img.bytesPerLine())[:, : w * 4].reshape(h, w, 4).copy()


def _scaled(image: QImage, width: int, height: int) -> QImage:
    if image.width() == width and image.height() == height:
        return image
    return image.scaled(width, height, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)


def render_annotated(
    image: QImage,
    canvas_size: Size,
    annotations: Sequence[Annotation],
    output_size: Size,
    crop: Optional[Rect] = None,
) -> Optional[QImage]:
    """Renders ``image`` (showing the whole canvas) plus annotations into an image of
    ``output_size``, optionally showing only ``crop`` (canvas points)."""
    region = crop or Rect(0, 0, canvas_size.width, canvas_size.height)
    w, h = int(output_size.width), int(output_size.height)
    if w <= 0 or h <= 0 or region.width <= 0 or image.isNull():
        return None
    s = w / region.width

    # Pixels of the source that the region covers; scaled with area averaging so small UI
    # text survives the downscale.
    px = image.width() / canvas_size.width
    py = image.height() / canvas_size.height
    source = image
    if crop is not None:
        x1, y1 = int(round(region.min_x * px)), int(round(region.min_y * py))
        x2, y2 = int(round(region.max_x * px)), int(round(region.max_y * py))
        source = image.copy(x1, y1, max(1, x2 - x1), max(1, y2 - y1))
    out = _scaled(source, w, h).convertToFormat(QImage.Format_RGB32)
    if out.isNull():
        return None

    painter = QPainter(out)
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.translate(-region.min_x * s, -region.min_y * s)
    # Marks never shrink below their on-screen size, so badges stay readable when a large
    # display is downscaled.
    mark_scale = max(s, MIN_MARK_SCALE)
    for a in annotations:
        draw_annotation(painter, a, scale=s, mark_scale=mark_scale, canvas_size=canvas_size)
    painter.end()
    return out


def resize(image: QImage, size: Size, source_rect: Optional[Rect] = None) -> Optional[QImage]:
    """High-quality resize of (a pixel region of) an image."""
    source = image
    if source_rect is not None:
        r = source_rect.integral()
        source = image.copy(int(r.x), int(r.y), int(r.width), int(r.height))
    w, h = int(size.width), int(size.height)
    if w <= 0 or h <= 0 or source.isNull():
        return None
    return _scaled(source, w, h)


def write_png(image: QImage, path: Union[str, Path]) -> None:
    if not image.save(str(path), "PNG"):
        raise OSError("PNG encoding failed: {}".format(path))


def load_image(path: Union[str, Path]) -> QImage:
    image = QImage(str(path))
    if image.isNull():
        raise OSError("Could not read image {}".format(path))
    return image

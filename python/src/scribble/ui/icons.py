"""Icons drawn with QPainter, so they look the same on every platform and need no icon
theme or font."""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Dict, Optional, Tuple

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap, QPolygonF

from ..core.models import AnnotationTool

_RESOURCES = Path(__file__).resolve().parent.parent / "resources"
_SCALE = 3  # drawn at 3x and shown at 1x: crisp on any display scaling
_cache: Dict[Tuple[str, str, int], QIcon] = {}


def _pen(color: QColor, width: float = 1.7) -> QPen:
    pen = QPen(color, width)
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    return pen


def _pen_tool(p: QPainter, c: QColor) -> None:
    p.setPen(_pen(c))
    path = QPainterPath(QPointF(4, 16))
    path.cubicTo(QPointF(7, 6), QPointF(10, 18), QPointF(13, 9))
    path.cubicTo(QPointF(14.5, 5), QPointF(16, 8), QPointF(17, 5))
    p.drawPath(path)


def _arrow(p: QPainter, c: QColor) -> None:
    p.setPen(_pen(c))
    p.drawLine(QPointF(5, 15), QPointF(14, 6))
    p.setBrush(c)
    p.drawPolygon(QPolygonF([QPointF(16, 4), QPointF(15.2, 10), QPointF(10, 4.8)]))


def _rectangle(p: QPainter, c: QColor) -> None:
    p.setPen(_pen(c))
    p.drawRoundedRect(QRectF(3.5, 5.5, 13, 9), 1.5, 1.5)


def _ellipse(p: QPainter, c: QColor) -> None:
    p.setPen(_pen(c))
    p.drawEllipse(QRectF(3, 5, 14, 10))


def _highlighter(p: QPainter, c: QColor) -> None:
    soft = QColor(c)
    soft.setAlphaF(0.45)
    flat = QPen(soft, 6)
    flat.setCapStyle(Qt.FlatCap)
    p.setPen(flat)
    p.drawLine(QPointF(3, 12), QPointF(17, 12))
    p.setPen(_pen(c, 1.4))
    p.drawLine(QPointF(5, 6), QPointF(15, 6))


def _pin(p: QPainter, c: QColor) -> None:
    p.setPen(_pen(c))
    p.drawEllipse(QPointF(10, 10), 6, 6)
    p.setBrush(c)
    p.setPen(Qt.NoPen)
    p.drawEllipse(QPointF(10, 10), 2.2, 2.2)


def _undo(p: QPainter, c: QColor) -> None:
    p.setPen(_pen(c))
    path = QPainterPath(QPointF(5, 8))
    path.lineTo(QPointF(12, 8))
    path.cubicTo(QPointF(17, 8), QPointF(17, 15), QPointF(12, 15))
    path.lineTo(QPointF(8, 15))
    p.drawPath(path)
    p.drawPolyline(QPolygonF([QPointF(8, 4.5), QPointF(4.5, 8), QPointF(8, 11.5)]))


def _camera(p: QPainter, c: QColor) -> None:
    p.setPen(_pen(c))
    p.drawRoundedRect(QRectF(3, 6.5, 14, 9.5), 2, 2)
    p.drawLine(QPointF(7.5, 6.5), QPointF(8.5, 4.5))
    p.drawLine(QPointF(8.5, 4.5), QPointF(11.5, 4.5))
    p.drawLine(QPointF(11.5, 4.5), QPointF(12.5, 6.5))
    p.drawEllipse(QPointF(10, 11.2), 2.6, 2.6)


def _check(p: QPainter, c: QColor) -> None:
    p.setPen(Qt.NoPen)
    p.setBrush(c)
    p.drawEllipse(QPointF(10, 10), 8, 8)
    p.setPen(_pen(QColor(255, 255, 255), 2))
    p.drawPolyline(QPolygonF([QPointF(6.3, 10.3), QPointF(9, 13), QPointF(13.8, 7.3)]))


def _cross(p: QPainter, c: QColor) -> None:
    p.setPen(Qt.NoPen)
    p.setBrush(c)
    p.drawEllipse(QPointF(10, 10), 8, 8)
    p.setPen(_pen(QColor(30, 30, 34), 2))
    p.drawLine(QPointF(7, 7), QPointF(13, 13))
    p.drawLine(QPointF(13, 7), QPointF(7, 13))


def _cursor(p: QPainter, c: QColor) -> None:
    p.setPen(_pen(c, 1.4))
    p.drawPolygon(
        QPolygonF(
            [
                QPointF(6, 3.5),
                QPointF(6, 15.5),
                QPointF(9, 12.7),
                QPointF(11.2, 17),
                QPointF(13, 16),
                QPointF(10.9, 11.9),
                QPointF(14.8, 11.6),
            ]
        )
    )


def _pencil(p: QPainter, c: QColor) -> None:
    p.setPen(_pen(c))
    p.drawPolygon(QPolygonF([QPointF(4, 16), QPointF(5, 12), QPointF(13, 4), QPointF(16, 7), QPointF(8, 15)]))
    p.drawLine(QPointF(11.5, 5.5), QPointF(14.5, 8.5))


_DRAWERS: Dict[str, Callable[[QPainter, QColor], None]] = {
    "pen": _pen_tool,
    "arrow": _arrow,
    "rectangle": _rectangle,
    "ellipse": _ellipse,
    "highlighter": _highlighter,
    "pin": _pin,
    "undo": _undo,
    "camera": _camera,
    "check": _check,
    "cross": _cross,
    "cursor": _cursor,
    "pencil": _pencil,
}


def icon(name: str, color: str = "#f2f2f4", size: int = 20) -> QIcon:
    """A 20×20-grid glyph in ``color``."""
    key = (name, color, size)
    if key not in _cache:
        pixmap = QPixmap(size * _SCALE, size * _SCALE)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.scale(size * _SCALE / 20.0, size * _SCALE / 20.0)
        painter.setBrush(Qt.NoBrush)
        _DRAWERS[name](painter, QColor(color))
        painter.end()
        pixmap.setDevicePixelRatio(_SCALE)
        _cache[key] = QIcon(pixmap)
    return _cache[key]


def tool_icon(tool: AnnotationTool, color: str = "#f2f2f4") -> QIcon:
    return icon(tool.value, color)


def app_pixmap(size: int = 64) -> QPixmap:
    pixmap = QPixmap(str(_RESOURCES / "icon.png"))
    if pixmap.isNull():
        pixmap = QPixmap(size, size)
        pixmap.fill(QColor("#f0443e"))
        return pixmap
    return pixmap.scaled(size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation)


def app_icon() -> QIcon:
    return QIcon(str(_RESOURCES / "icon.png"))


def tray_icon(state: str = "idle") -> QIcon:
    """The app icon with a status dot: ``recording`` (red) or ``busy`` (orange)."""
    key = ("tray", state, 0)
    if key not in _cache:
        pixmap = app_pixmap(64)
        dot: Optional[QColor] = {"recording": QColor("#ff3b30"), "busy": QColor("#ff9f0a")}.get(state)
        if dot is not None:
            painter = QPainter(pixmap)
            painter.setRenderHint(QPainter.Antialiasing, True)
            painter.setPen(QPen(QColor(255, 255, 255), 4))
            painter.setBrush(dot)
            painter.drawEllipse(QPointF(pixmap.width() - 16, pixmap.height() - 16), 13, 13)
            painter.end()
        _cache[key] = QIcon(pixmap)
    return _cache[key]

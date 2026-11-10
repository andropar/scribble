"""The drawing surface: shows annotations and turns mouse and keyboard input into them.

A port of ``AnnotationCanvasView`` (Overlay.swift). Coordinates are canvas points with the
origin top-left, which is what Qt widgets use anyway.
"""

from __future__ import annotations

from typing import Callable, List, Optional

from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QKeyEvent, QMouseEvent, QPainter, QPaintEvent, QPixmap
from PySide6.QtWidgets import QWidget

from .. import render
from ..core.geometry import Point, Size, path_length
from ..core.models import PALETTE, Annotation, AnnotationTool, RGBAColor

_TOOL_KEYS = {
    Qt.Key_P: AnnotationTool.PEN,
    Qt.Key_A: AnnotationTool.ARROW,
    Qt.Key_R: AnnotationTool.RECTANGLE,
    Qt.Key_O: AnnotationTool.ELLIPSE,
    Qt.Key_H: AnnotationTool.HIGHLIGHTER,
    Qt.Key_N: AnnotationTool.PIN,
}


class AnnotationCanvas(QWidget):
    #: A finished stroke or shape (the receiver numbers it and calls ``add``).
    finished = Signal(object)
    finish_requested = Signal()
    cancel_requested = Signal()
    next_shot_requested = Signal()
    undo_requested = Signal()
    tool_selected = Signal(object)
    color_selected = Signal(int)
    FADE_OUT_DURATION = 0.6
    PIN_EXTRA_HOLD = 2.0
    #: Padding around a mark's bounds that covers its badge, line width and shadow.
    _DIRTY_MARGIN = 44

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.tool = AnnotationTool.PEN
        self.color_index = 0
        #: Session clock (seconds since capture start).
        self.clock: Callable[[], float] = lambda: 0.0
        self.shot = 0
        #: Display index (in the capture's display list) this canvas covers.
        self.screen_index = 0
        #: Walkthrough: seconds an annotation stays fully visible before fading. None = never.
        self.fade_after: Optional[float] = None
        #: Snapshot: the frozen screenshot shown underneath.
        self.backdrop: Optional[QPixmap] = None
        #: Fill painted under the marks (None = nothing; see WalkthroughOverlay).
        self.background: Optional[QColor] = None
        #: Called with the global position of a press; returning True swallows the press
        #: (it belonged to the toolbar, see WalkthroughOverlay's pointer grab).
        self.press_filter: Optional[Callable[[QPoint], bool]] = None
        self.annotations: List[Annotation] = []
        self._current: Optional[Annotation] = None
        self._fade_timer = QTimer(self)
        self._fade_timer.setInterval(33)
        self._fade_timer.timeout.connect(self._fade_tick)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMouseTracking(False)

    # MARK: content

    @property
    def is_tracking(self) -> bool:
        return self._current is not None

    @property
    def current(self) -> Optional[Annotation]:
        return self._current

    def add(self, a: Annotation) -> None:
        self.annotations.append(a)
        self._invalidate(a)
        if self.fade_after is not None and not self._fade_timer.isActive():
            self._fade_timer.start()
        self.content_changed()

    def remove(self, annotation_id: int) -> None:
        for a in self.annotations:
            if a.id == annotation_id:
                self._invalidate(a)
        self.annotations = [a for a in self.annotations if a.id != annotation_id]
        self.content_changed()

    def remove_all(self) -> None:
        self.annotations = []
        self._current = None
        self.update()
        self.content_changed()

    def stop(self) -> None:
        self._fade_timer.stop()

    def content_changed(self) -> None:
        """Hook for subclasses: the set of visible marks changed."""

    @property
    def _color(self) -> RGBAColor:
        return PALETTE[min(max(self.color_index, 0), len(PALETTE) - 1)]

    def _invalidate(self, a: Optional[Annotation]) -> None:
        """Repaints only the part of the canvas a mark occupies."""
        if a is None or not a.points:
            return
        b = a.bounds
        m = self._DIRTY_MARGIN + int(a.line_width or 0)
        self.update(QRect(int(b.min_x) - m, int(b.min_y) - m, int(b.width) + 2 * m, int(b.height) + 2 * m))

    # MARK: mouse

    def _point(self, event: QMouseEvent) -> Point:
        p = event.position()
        return (min(max(p.x(), 0.0), float(self.width())), min(max(p.y(), 0.0), float(self.height())))

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if self.press_filter is not None and self.press_filter(event.globalPosition().toPoint()):
            return
        if event.button() == Qt.RightButton:
            self.undo_requested.emit()
            return
        if event.button() != Qt.LeftButton:
            return
        self.begin_stroke(self._point(event))

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._current is not None:
            self.extend_stroke(self._point(event))

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.LeftButton:
            self.end_stroke()

    def begin_stroke(self, p: Point) -> None:
        t = self.clock()
        if self.tool == AnnotationTool.PIN:
            pin = Annotation(
                tool=AnnotationTool.PIN,
                points=[p],
                color=self._color,
                start_time=t,
                shot=self.shot,
                screen=self.screen_index,
            )
            pin.finalize(t)
            self.finished.emit(pin)
            return
        self._current = Annotation(
            tool=self.tool,
            points=[p],
            color=self._color,
            start_time=t,
            shot=self.shot,
            screen=self.screen_index,
        )
        self._invalidate(self._current)
        self.content_changed()

    def extend_stroke(self, p: Point) -> None:
        a = self._current
        if a is None:
            return
        self._invalidate(a)
        if a.tool.is_two_point:
            a.points = [a.points[0], p]
        else:
            last = a.points[-1]
            if ((p[0] - last[0]) ** 2 + (p[1] - last[1]) ** 2) ** 0.5 >= 1.5:
                a.points.append(p)
        self._invalidate(a)
        self.content_changed()

    def end_stroke(self) -> None:
        a = self._current
        if a is None:
            return
        self._current = None
        self._invalidate(a)
        t = self.clock()
        extent = path_length(a.points)
        keep = True
        if a.tool.is_two_point:
            keep = len(a.points) == 2 and extent >= 6
        elif extent < 3:
            # A pen tap stays a dot; a highlighter tap highlights nothing.
            keep = a.tool == AnnotationTool.PEN
            a.points = [a.points[0]]
        if not keep:
            self.content_changed()
            return
        a.finalize(t)
        self.finished.emit(a)

    # MARK: keyboard (Snapshot mode)

    def focusNextPrevChild(self, next_: bool) -> bool:
        # Tab is "next shot", not focus navigation.
        return False

    def keyPressEvent(self, event: QKeyEvent) -> None:
        key = event.key()
        if key in (Qt.Key_Return, Qt.Key_Enter):
            self.finish_requested.emit()
        elif key == Qt.Key_Escape:
            self.cancel_requested.emit()
        elif key in (Qt.Key_Tab, Qt.Key_Backtab):
            self.next_shot_requested.emit()
        elif key in (Qt.Key_Backspace, Qt.Key_Delete):
            self.undo_requested.emit()
        elif event.modifiers() & (Qt.ControlModifier | Qt.MetaModifier):
            if key == Qt.Key_Z:
                self.undo_requested.emit()
        elif key in _TOOL_KEYS:
            self.tool_selected.emit(_TOOL_KEYS[key])
        elif Qt.Key_1 <= key < Qt.Key_1 + len(PALETTE):
            self.color_selected.emit(key - Qt.Key_1)
        else:
            super().keyPressEvent(event)

    # MARK: drawing

    def alpha_for(self, a: Annotation, now: float) -> float:
        if self.fade_after is None:
            return 1.0
        hold = self.fade_after + (self.PIN_EXTRA_HOLD if a.tool == AnnotationTool.PIN else 0)
        age = now - (a.end_time or a.start_time) - hold
        if age <= 0:
            return 1.0
        return max(0.0, 1 - age / self.FADE_OUT_DURATION)

    def paint_content(self, painter: QPainter, now: Optional[float] = None) -> None:
        """Paints all marks (also used to render the canvas off screen)."""
        now = self.clock() if now is None else now
        size = Size(self.width(), self.height())
        for a in self.annotations:
            render.draw_annotation(painter, a, alpha=self.alpha_for(a, now), canvas_size=size)
        if self._current is not None:
            render.draw_annotation(painter, self._current, show_badge=False)

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setClipRegion(event.region())
        if self.backdrop is not None:
            painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
            painter.drawPixmap(self.rect(), self.backdrop)
        elif self.background is not None:
            painter.setCompositionMode(QPainter.CompositionMode_Source)
            painter.fillRect(event.rect(), self.background)
            painter.setCompositionMode(QPainter.CompositionMode_SourceOver)
        self.paint_content(painter)
        painter.end()

    def _fade_tick(self) -> None:
        now = self.clock()
        gone = [a for a in self.annotations if self.alpha_for(a, now) <= 0]
        for a in self.annotations:
            if self.alpha_for(a, now) < 1:
                self._invalidate(a)
        if gone:
            self.annotations = [a for a in self.annotations if self.alpha_for(a, now) > 0]
            self.content_changed()
        if not self.annotations:
            self._fade_timer.stop()

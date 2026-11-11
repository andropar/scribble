"""Full-screen overlay windows, one per display.

``SnapshotOverlay`` shows the frozen screenshot and takes the keyboard. ``WalkthroughOverlay``
is transparent and lets clicks through until draw mode is on.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import QRect, Qt, QTimer
from PySide6.QtGui import QBitmap, QColor, QImage, QPainter, QPixmap, QRegion

from ..platform import Platform
from ..screens import Display
from .canvas import AnnotationCanvas

log = logging.getLogger(__name__)


class SnapshotOverlay(AnnotationCanvas):
    """The frozen screen of one display, with the canvas on top."""

    def __init__(self, display: Display, image: QImage, platform: Platform):
        super().__init__()
        self._platform = platform
        self.setWindowFlags(platform.overlay_flags())
        self.setAttribute(Qt.WA_DeleteOnClose, True)
        self.setAttribute(Qt.WA_OpaquePaintEvent, True)
        self.setCursor(Qt.CrossCursor)
        self.setWindowTitle("Scribble")
        pixmap = QPixmap.fromImage(image)
        pixmap.setDevicePixelRatio(max(image.width() / max(display.geometry.width(), 1), 0.01))
        self.backdrop = pixmap
        self.setGeometry(display.geometry)

    def present(self, take_keyboard: bool) -> None:
        self.show()
        self.raise_()
        if take_keyboard:
            self.take_keyboard()

    def take_keyboard(self) -> None:
        self._platform.grab_keyboard(self)

    def mousePressEvent(self, event) -> None:
        # Keyboard shortcuts follow the display being drawn on.
        if not self.isActiveWindow():
            self.take_keyboard()
        super().mousePressEvent(event)


class WalkthroughOverlay(AnnotationCanvas):
    """Transparent, always-on-top canvas over one display. Marks drawn here are part of
    the screen, so they end up in the recording.

    Without a compositing manager (some X11 setups) a translucent window would be an opaque
    black rectangle. There the window is cut to the shape of the marks instead, and the
    pointer is grabbed while drawing.
    """

    def __init__(self, display: Display, platform: Platform):
        super().__init__()
        self._platform = platform
        self._composited = platform.compositing()
        self._drawing = False
        self._grabbing = False
        self.setWindowFlags(platform.overlay_flags() | Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_DeleteOnClose, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setFocusPolicy(Qt.NoFocus)
        self.setWindowTitle("Scribble")
        if self._composited:
            self.setAttribute(Qt.WA_TranslucentBackground, True)
            self.background = QColor(0, 0, 0, 0)
        else:
            self.setAttribute(Qt.WA_OpaquePaintEvent, True)
            self.background = QColor(0, 0, 0)
            self._mask_timer = QTimer(self)
            self._mask_timer.setSingleShot(True)
            self._mask_timer.setInterval(30)
            self._mask_timer.timeout.connect(self._update_mask)
        self.setGeometry(display.geometry)

    @property
    def composited(self) -> bool:
        return self._composited

    def present(self) -> None:
        self.show()
        self.raise_()
        if not self._composited:
            self._update_mask()
        self._platform.set_click_through(self, True)

    def set_drawing(self, drawing: bool) -> None:
        """Turns draw mode on (the overlay takes the mouse) or off (clicks pass through)."""
        if drawing == self._drawing:
            return
        self._drawing = drawing
        if self._composited:
            # A barely visible fill makes the whole surface hit-testable: on Windows a fully
            # transparent pixel of a layered window does not receive mouse events.
            self.background = QColor(0, 0, 0, 1 if drawing else 0)
            self.update()
            self._platform.set_click_through(self, not drawing)
            if drawing:
                self.setCursor(Qt.CrossCursor)
            else:
                self.unsetCursor()
        else:
            # Cut to the shape of its marks, the window cannot receive clicks elsewhere: the
            # pointer has to be grabbed instead (see ``set_pointer_grab``).
            self._platform.set_click_through(self, not drawing)
            if not drawing:
                self.set_pointer_grab(False)

    def set_pointer_grab(self, grab: bool) -> None:
        """No compositor: routes all mouse input to this overlay. With several displays the
        controller gives the grab to the overlay under the pointer."""
        if self._composited or grab == self._grabbing:
            return
        self._grabbing = grab
        if grab:
            self.grabMouse(Qt.CrossCursor)
        else:
            self.releaseMouse()

    @property
    def needs_pointer_grab(self) -> bool:
        return self._drawing and not self._composited

    def close(self) -> bool:
        self.set_pointer_grab(False)
        return super().close()

    def content_changed(self) -> None:
        if not self._composited:
            if not self._mask_timer.isActive():
                self._mask_timer.start()

    def _fade_tick(self) -> None:
        super()._fade_tick()
        if not self._composited and self.annotations:
            self.content_changed()

    def _update_mask(self) -> None:
        """Cuts the window to the pixels the marks cover (no compositor)."""
        if not self.annotations and self.current is None:
            # Keep one pixel so the window stays mapped (a grab needs a viewable window).
            self.setMask(QRegion(QRect(0, 0, 1, 1)))
            return
        image = QImage(self.size(), QImage.Format_ARGB32_Premultiplied)
        image.fill(Qt.transparent)
        painter = QPainter(image)
        self.paint_content(painter)
        painter.end()
        mask = QBitmap.fromImage(image.createAlphaMask())
        self.setMask(mask)
        self.update()

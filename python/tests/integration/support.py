"""Shared helpers of the desktop tests: the session under test, synthetic input in canvas
coordinates, screenshots, and a window that stands in for the user's app."""

import os
import sys
import tempfile
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QWidget

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

import inject  # noqa: E402
from fakes import pump  # noqa: E402
from helpers import FakeTranscriber  # noqa: E402

from scribble import platform as platforms, render, screens  # noqa: E402

ARTIFACTS = Path(os.environ.get("SCRIBBLE_ARTIFACTS") or tempfile.mkdtemp(prefix="scribble-artifacts-"))
ARTIFACTS.mkdir(parents=True, exist_ok=True)
HYPER = ("ctrl", "alt", "shift")
BLUE, RED, GREEN, PURPLE = (0x2E, 0x6F, 0xD0), (0xD0, 0x4A, 0x2E), (0x2E, 0xA8, 0x6F), (0x8A, 0x2E, 0xD0)


class Transcriber(FakeTranscriber):
    class _Status:
        is_ready, title = True, "ready"

    status = _Status()

    def subscribe(self, listener):
        pass


class Env:
    """The desktop session. Positions are canvas points of a display (the first one unless
    another is given), converted to native pixels for the injector."""

    def __init__(self) -> None:
        self.platform = platforms.current()
        self.grabber = screens.create_grabber(self.platform)
        self.inject = inject.create()
        self.displays = screens.displays()
        self.display = self.displays[0]

    def native(self, x: float, y: float, display=None):
        d = display or self.display
        return int(round(d.native[0] + x * d.dpr)), int(round(d.native[1] + y * d.dpr))

    def move(self, x: float, y: float, display=None) -> None:
        self.inject.move(*self.native(x, y, display))

    def click(self, x: float, y: float, display=None) -> None:
        self.move(x, y, display)
        pump(0.15)
        self.inject.press()
        pump(0.12)
        self.inject.release()
        pump(0.15)

    def drag(self, points, display=None) -> None:
        self.move(*points[0], display=display)
        pump(0.15)
        self.inject.press()
        pump(0.1)
        for p in points[1:]:
            self.move(*p, display=display)
            pump(0.06)
        self.inject.release()
        pump(0.15)

    def snap(self, name: str, display=None):
        """Saves what is on a display right now and returns the image."""
        image = self.grabber.grab(display or self.display)
        render.write_png(image, ARTIFACTS / "{}_{}.png".format(self.platform.name, name))
        return image

    def pixel(self, image, x: float, y: float, display=None):
        d = display or self.display
        c = image.pixelColor(int(round(x * d.dpr)), int(round(y * d.dpr)))
        return c.red(), c.green(), c.blue()


class AppWindow(QWidget):
    """Stands in for the app the user is showing: a flat color that counts its clicks."""

    def __init__(self, color: str, x: int, y: int, w: int, h: int, display) -> None:
        super().__init__()
        self.clicks = []
        self._color = QColor(color)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Window)
        self.setWindowTitle("scribble test target")
        self.setGeometry(display.geometry.x() + x, display.geometry.y() + y, w, h)

    def set_color(self, color: str) -> None:
        self._color = QColor(color)
        self.repaint()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), self._color)
        painter.end()

    def mousePressEvent(self, event) -> None:
        self.clicks.append((event.position().x(), event.position().y()))


def close_to(actual, expected, tolerance=3) -> bool:
    return all(abs(a - e) <= tolerance for a, e in zip(actual, expected))


def rgb(color) -> tuple:
    return color.red(), color.green(), color.blue()

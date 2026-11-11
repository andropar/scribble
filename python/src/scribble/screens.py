"""Displays and screen grabbing.

Canvas coordinates are a display's device-independent pixels (what Qt widgets use);
grabbing works in native pixels of the virtual desktop. ``Display`` holds both.
"""

from __future__ import annotations

import logging
import os
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass
from typing import Any, List, Optional, Sequence, Tuple

import numpy as np
from PySide6.QtCore import QEventLoop, QObject, QRect, QTimer, QUrl, Slot
from PySide6.QtGui import QCursor, QGuiApplication, QImage, QScreen

from . import render
from .core.geometry import Point, Size
from .platform import Platform
from .platform.base import which
from .processor import DisplayInfo

log = logging.getLogger(__name__)

NativeRect = Tuple[int, int, int, int]  # left, top, width, height


@dataclass
class Display:
    """A display taking part in a capture."""

    index: int
    name: str
    screen: QScreen
    #: Position and size in device-independent pixels (Qt's global coordinates).
    geometry: QRect
    #: Native pixels per device-independent pixel.
    dpr: float
    #: Position and size in native pixels of the virtual desktop.
    native: NativeRect

    @property
    def canvas_size(self) -> Size:
        return Size(self.geometry.width(), self.geometry.height())

    @property
    def info(self) -> DisplayInfo:
        return DisplayInfo(self.index, self.name, self.canvas_size)

    def contains_native(self, x: float, y: float) -> bool:
        left, top, width, height = self.native
        return left <= x < left + width and top <= y < top + height

    def canvas_point(self, x: float, y: float) -> Point:
        """Converts a native virtual-desktop position into this display's canvas points."""
        return ((x - self.native[0]) / self.dpr, (y - self.native[1]) / self.dpr)


def native_rect(geometry: QRect, dpr: float) -> NativeRect:
    # Qt keeps a screen's native origin and scales only its size.
    return (
        geometry.x(),
        geometry.y(),
        int(round(geometry.width() * dpr)),
        int(round(geometry.height() * dpr)),
    )


def displays() -> List[Display]:
    result = []
    for index, screen in enumerate(QGuiApplication.screens()):
        geometry = screen.geometry()
        dpr = float(screen.devicePixelRatio())
        name = screen.name() or "Display {}".format(index + 1)
        model = (screen.model() or "").strip()
        if model and model not in name:
            name = "{} ({})".format(model, name)
        result.append(Display(index, name, screen, geometry, dpr, native_rect(geometry, dpr)))
    return result


def display_at(displays_: Sequence[Display], x: float, y: float) -> Optional[Display]:
    for d in displays_:
        if d.contains_native(x, y):
            return d
    return None


def cursor_native(platform: Platform, displays_: Sequence[Display]) -> Tuple[float, float]:
    """Pointer position in native virtual-desktop pixels."""
    state = platform.input_state()
    if state is not None:
        return (state.x, state.y)
    pos = QCursor.pos()
    for d in displays_:
        if d.geometry.contains(pos):
            return (
                d.native[0] + (pos.x() - d.geometry.x()) * d.dpr,
                d.native[1] + (pos.y() - d.geometry.y()) * d.dpr,
            )
    return (float(pos.x()), float(pos.y()))


# MARK: grabbing


class ScreenGrabber:
    """Grabs screen content. ``grab`` runs on the GUI thread; ``grab_array`` must be safe to
    call from worker threads."""

    #: Whether ``grab_array`` works (needed for Walkthrough).
    continuous = False

    def grab(self, display: Display) -> QImage:
        raise NotImplementedError

    def grab_array(self, rect: NativeRect) -> np.ndarray:
        raise NotImplementedError("Continuous screen capture is not available in this session")


class MssGrabber(ScreenGrabber):
    """GDI (Windows) / X11 grabs through mss. One mss instance per thread, as it requires."""

    continuous = True

    def __init__(self) -> None:
        import mss

        self._factory = getattr(mss, "MSS", None) or mss.mss
        self._local = threading.local()
        # Fail here, not in the middle of a capture, if the screen cannot be reached.
        self._instance()

    def _instance(self) -> Any:
        sct = getattr(self._local, "sct", None)
        if sct is None:
            sct = self._factory()
            self._local.sct = sct
        return sct

    def grab_array(self, rect: NativeRect) -> np.ndarray:
        left, top, width, height = rect
        shot = self._instance().grab({"left": left, "top": top, "width": width, "height": height})
        return np.frombuffer(shot.raw, dtype=np.uint8).reshape(shot.height, shot.width, 4)

    def grab(self, display: Display) -> QImage:
        return render.image_from_bgra(self.grab_array(display.native))


class QtGrabber(ScreenGrabber):
    """Qt's own screen grab: the fallback where mss has no backend."""

    def grab(self, display: Display) -> QImage:
        image = display.screen.grabWindow(0).toImage()
        if image.isNull():
            raise RuntimeError("Screenshot of {} failed".format(display.name))
        return image


class _PortalResponse(QObject):
    def __init__(self) -> None:
        super().__init__()
        self.loop = QEventLoop()
        self.uri: Optional[str] = None

    @Slot("uint", "QVariantMap")
    def on_response(self, code: int, results: Any) -> None:
        if code == 0:
            self.uri = str(dict(results).get("uri") or "")
        self.loop.quit()


def portal_screenshot(timeout_ms: int = 20000) -> Optional[QImage]:
    """Full-desktop screenshot through xdg-desktop-portal (asks the user the first time)."""
    try:
        from PySide6.QtCore import SLOT
        from PySide6.QtDBus import QDBusConnection, QDBusInterface
    except ImportError:
        return None
    bus = QDBusConnection.sessionBus()
    if not bus.isConnected():
        return None
    service, base = "org.freedesktop.portal.Desktop", "/org/freedesktop/portal/desktop"
    token = "scribble{}".format(int(time.time() * 1000) % 100000000)
    sender = bus.baseService().lstrip(":").replace(".", "_")
    request_path = "{}/request/{}/{}".format(base, sender, token)
    response = _PortalResponse()
    slot = SLOT("on_response(uint,QVariantMap)")
    if not bus.connect(service, request_path, "org.freedesktop.portal.Request", "Response", response, slot):
        return None
    portal = QDBusInterface(service, base, "org.freedesktop.portal.Screenshot", bus)
    reply = portal.call("Screenshot", "", {"handle_token": token, "interactive": False})
    if reply.errorName():
        log.info("Screenshot portal refused: %s", reply.errorMessage())
        return None
    QTimer.singleShot(timeout_ms, response.loop.quit)
    response.loop.exec()
    if not response.uri:
        return None
    path = QUrl(response.uri).toLocalFile()
    image = QImage(path)
    try:
        os.remove(path)
    except OSError:
        pass
    return None if image.isNull() else image


def _tool_screenshot() -> Optional[QImage]:
    """Screenshot through the desktop's own command line tool."""
    fd, path = tempfile.mkstemp(suffix=".png", prefix="scribble-")
    os.close(fd)
    commands = []
    if which("grim"):
        commands.append(["grim", path])
    if which("spectacle"):
        commands.append(["spectacle", "-b", "-n", "-f", "-o", path])
    if which("gnome-screenshot"):
        commands.append(["gnome-screenshot", "-f", path])
    try:
        for command in commands:
            try:
                subprocess.run(command, timeout=10, check=True, capture_output=True)
            except (OSError, subprocess.SubprocessError) as error:
                log.info("%s failed: %s", command[0], error)
                continue
            image = QImage(path)
            if not image.isNull():
                return image
        return None
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


class WaylandGrabber(ScreenGrabber):
    """Wayland: one screenshot of the whole desktop from the compositor's own tools, cut
    into displays. Not continuous, so no Walkthrough."""

    def __init__(self) -> None:
        self._last: Optional[QImage] = None
        self._last_time = 0.0

    def _desktop(self) -> QImage:
        # All displays of one Snapshot round come from the same screenshot.
        if self._last is not None and time.monotonic() - self._last_time < 0.5:
            return self._last
        image = (_tool_screenshot() if which("grim") else None) or portal_screenshot() or _tool_screenshot()
        if image is None:
            raise RuntimeError(
                "No screenshot on this Wayland session: the desktop portal refused and neither grim, "
                "spectacle nor gnome-screenshot is installed"
            )
        self._last, self._last_time = image, time.monotonic()
        return image

    def grab(self, display: Display) -> QImage:
        desktop = self._desktop()
        virtual = display.screen.virtualGeometry()
        if virtual.width() <= 0 or virtual.height() <= 0:
            return desktop
        sx, sy = desktop.width() / virtual.width(), desktop.height() / virtual.height()
        g = display.geometry
        return desktop.copy(
            int(round((g.x() - virtual.x()) * sx)),
            int(round((g.y() - virtual.y()) * sy)),
            int(round(g.width() * sx)),
            int(round(g.height() * sy)),
        )


def create_grabber(platform: Platform) -> ScreenGrabber:
    if platform.name == "wayland":
        return WaylandGrabber()
    try:
        return MssGrabber()
    except Exception as error:
        log.warning("mss is not usable here (%s); using Qt's screen grab, without Walkthrough", error)
        return QtGrabber()

"""Stand-ins for the screen, the microphone and the OS, so the whole app can be driven in a
headless test."""

import sys
import time
from pathlib import Path
from typing import Optional

import numpy as np
from PySide6.QtCore import QCoreApplication
from PySide6.QtGui import QImage

from scribble import paths
from scribble.audio import AudioResult
from scribble.platform.base import InputState, Platform
from scribble.screens import ScreenGrabber

sys.path.insert(0, str(Path(__file__).parent))
from helpers import gradient_image, solid_frame, tone  # noqa: E402

from scribble.core.geometry import Size  # noqa: E402


class FakeGrabber(ScreenGrabber):
    """A "screen" that is a flat gray whose level tests can change."""

    continuous = True

    def __init__(self) -> None:
        self.level = 60
        self.grabs = 0

    def grab(self, display) -> QImage:
        self.grabs += 1
        return gradient_image(Size(display.native[2], display.native[3]), 1.0)

    def grab_array(self, rect) -> np.ndarray:
        self.grabs += 1
        return solid_frame(rect[2], rect[3], self.level)


class FakeAudio:
    """Hands back a tone as long as the "recording" lasted."""

    instances = []

    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.on_level = None
        self.started: Optional[float] = None
        FakeAudio.instances.append(self)

    def start(self) -> None:
        if self.fail:
            raise RuntimeError("no microphone")
        self.started = paths.now()
        if self.on_level is not None:
            self.on_level(0.5)

    def stop(self) -> AudioResult:
        duration = max(0.6, paths.now() - (self.started or paths.now()))
        return AudioResult(tone(duration), self.started)


class FakePlatform(Platform):
    """The generic backend plus a scripted pointer and keyboard."""

    name = "fake"
    tracks_input = True

    def __init__(self) -> None:
        self.x, self.y = 10, 10
        self.left_down = False
        self.mods = frozenset()
        self.focus_restored = []
        self.click_through = {}
        self.autostart = False

    def input_state(self) -> InputState:
        return InputState(self.x, self.y, self.left_down, frozenset(self.mods))

    def set_click_through(self, widget, enabled: bool) -> None:
        self.click_through[widget] = enabled

    def remember_focus(self):
        return "previous-app"

    def restore_focus(self, token) -> None:
        self.focus_restored.append(token)

    def autostart_enabled(self) -> bool:
        return self.autostart

    def set_autostart(self, enabled: bool) -> None:
        self.autostart = enabled


def wait_until(condition, timeout: float = 10.0, step: float = 0.01) -> bool:
    """Runs the Qt event loop until ``condition()`` is true."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        QCoreApplication.processEvents()
        if condition():
            return True
        time.sleep(step)
    QCoreApplication.processEvents()
    return bool(condition())


def pump(seconds: float) -> None:
    wait_until(lambda: False, timeout=seconds)

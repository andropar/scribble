"""Operating system backends. ``current()`` picks the one for this session."""

from __future__ import annotations

import logging
import os
import sys
from typing import Optional

from .base import HotkeyBackend, InputState, Platform

log = logging.getLogger(__name__)

_current: Optional[Platform] = None


def is_wayland_session() -> bool:
    return sys.platform.startswith("linux") and (
        os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland" or bool(os.environ.get("WAYLAND_DISPLAY"))
    )


def prepare_environment() -> None:
    """Must run before the QApplication is created."""
    if is_wayland_session() and os.environ.get("DISPLAY") and "QT_QPA_PLATFORM" not in os.environ:
        # Overlays must be placed on a specific screen and stay on top, which Wayland does
        # not let a client decide; XWayland windows can.
        os.environ["QT_QPA_PLATFORM"] = "xcb"


def _create() -> Platform:
    if sys.platform == "win32":
        from .windows import WindowsPlatform

        return WindowsPlatform()
    if sys.platform.startswith("linux") and os.environ.get("DISPLAY"):
        from PySide6.QtGui import QGuiApplication

        if QGuiApplication.platformName() == "xcb":
            try:
                from .x11 import WaylandPlatform, X11Platform

                return WaylandPlatform() if is_wayland_session() else X11Platform()
            except Exception as error:
                log.error("X11 backend not available, running without hotkeys: %s", error)
    return Platform()


def current() -> Platform:
    global _current
    if _current is None:
        _current = _create()
        log.info("Platform backend: %s", _current.name)
    return _current


def set_current(platform: Optional[Platform]) -> None:
    """Replaces the backend (tests)."""
    global _current
    _current = platform


__all__ = ["HotkeyBackend", "InputState", "Platform", "current", "set_current", "prepare_environment"]

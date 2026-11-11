"""What the app needs from the operating system beyond Qt.

``Platform`` is also the fallback used where no native backend exists (e.g. macOS during
development): no global hotkeys, clicks and held modifiers are not tracked.
"""

from __future__ import annotations

import logging
import os
import shutil
import sys
from dataclasses import dataclass
from typing import Any, Dict, FrozenSet, List, Optional

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import QWidget

from ..settings import Hotkey

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class InputState:
    """Pointer and modifier state, independent of which window has focus."""

    #: Pointer position in native pixels of the virtual desktop.
    x: int
    y: int
    left_down: bool
    #: Held modifiers: a subset of ctrl, alt, shift, super.
    mods: FrozenSet[str]


class HotkeyBackend(QObject):
    """Global hotkeys. ``triggered`` carries the action name on the GUI thread."""

    triggered = Signal(str)

    #: False if this session has no way to register global hotkeys.
    available = False

    def set_hotkeys(self, hotkeys: Dict[str, Hotkey]) -> Dict[str, str]:
        """Replaces all registrations. Returns ``{action: reason}`` for those that failed."""
        return {action: "global hotkeys are not available in this session" for action in hotkeys}

    def suspend(self) -> None:
        """Temporarily releases all hotkeys (e.g. while recording a new shortcut)."""

    def resume(self) -> None:
        pass

    def shutdown(self) -> None:
        pass


def launch_command() -> List[str]:
    """Command that starts the app (for autostart entries)."""
    if getattr(sys, "frozen", False):
        # The packaged app has a console launcher next to the windowed one; start the latter.
        folder, name = os.path.split(sys.executable)
        windowed = os.path.join(folder, name.replace("scribble-cli", "scribble"))
        return [windowed if os.path.isfile(windowed) else sys.executable]
    exe = sys.executable
    if sys.platform == "win32":
        windowless = os.path.join(os.path.dirname(exe), "pythonw.exe")
        if os.path.isfile(windowless):
            exe = windowless
    return [exe, "-m", "scribble"]


class Platform:
    name = "generic"

    #: Screen content can be grabbed silently and continuously (Walkthrough needs this).
    can_record = True
    #: Pointer buttons and modifiers can be read while other apps have focus.
    tracks_input = False
    #: Windows can be hidden from screen capture (keeps the HUD out of recordings).
    can_exclude_from_capture = False

    def create_hotkeys(self) -> HotkeyBackend:
        return HotkeyBackend()

    def input_state(self) -> Optional[InputState]:
        return None

    def overlay_flags(self) -> Qt.WindowType:
        """Window flags for full-screen overlays and the HUD."""
        return Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool

    def set_click_through(self, widget: QWidget, enabled: bool) -> None:
        """Lets mouse events pass through ``widget`` to whatever is underneath."""
        handle = widget.windowHandle()
        if handle is not None:
            handle.setFlag(Qt.WindowTransparentForInput, enabled)

    def exclude_from_capture(self, widget: QWidget) -> bool:
        return False

    def clear_owner(self, widget: QWidget) -> None:
        """Cuts the native tie between a window and the window it floats above, so that
        closing that one cannot take this one with it."""

    def window_alive(self, widget: QWidget) -> bool:
        """Whether the widget's native window still exists."""
        return True

    def compositing(self) -> bool:
        """Whether translucent windows are composited (else they would be opaque black)."""
        return True

    def remember_focus(self) -> Any:
        """Token for the window that has keyboard focus now."""
        return None

    def restore_focus(self, token: Any) -> None:
        pass

    def grab_keyboard(self, widget: QWidget) -> None:
        """Gives ``widget`` the keyboard when the window manager will not."""
        widget.activateWindow()
        widget.setFocus(Qt.OtherFocusReason)

    def autostart_enabled(self) -> bool:
        return False

    def set_autostart(self, enabled: bool) -> None:
        raise NotImplementedError("Launch at login is not supported on this platform")

    def flush_clipboard(self) -> None:
        """Hands the clipboard contents over to the system, so they outlive this process
        even if it does not shut Qt down in an orderly way."""

    def allow_foreground(self) -> None:
        """Called in a command line trigger process before it asks the running app to act,
        so the app may take the foreground on its behalf."""

    def notes(self) -> List[str]:
        """Limitations of this session worth telling the user."""
        return []

    def open_folder(self, path: str) -> None:
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices

        QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def reveal(self, path: str) -> None:
        """Shows a file in the file manager."""
        self.open_folder(os.path.dirname(path))


def which(*names: str) -> Optional[str]:
    for name in names:
        found = shutil.which(name)
        if found:
            return found
    return None

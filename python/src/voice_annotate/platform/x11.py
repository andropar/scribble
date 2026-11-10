"""Linux backend for X11 sessions (python-xlib), and the reduced Wayland variant that runs
through XWayland."""

from __future__ import annotations

import logging
import os
import queue
import select
import shlex
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget

from ..settings import Hotkey, HotkeyAction
from .base import HotkeyBackend, InputState, Platform, launch_command, which

log = logging.getLogger(__name__)

_NAMED_KEYS = {
    "Space": "space",
    "Tab": "Tab",
    "Return": "Return",
    "Escape": "Escape",
    "Backspace": "BackSpace",
    "Delete": "Delete",
    "Insert": "Insert",
    "Home": "Home",
    "End": "End",
    "PageUp": "Prior",
    "PageDown": "Next",
    "Left": "Left",
    "Up": "Up",
    "Right": "Right",
    "Down": "Down",
}


def keysym_name(key: str) -> str:
    """X keysym name for a hotkey key name."""
    if len(key) == 1 and key.isascii():
        return key.lower()
    return _NAMED_KEYS.get(key, key)


class X11Hotkeys(HotkeyBackend):
    """Passive key grabs on the root window, served by a thread with its own connection."""

    available = True
    _DEBOUNCE = 0.25

    def __init__(self) -> None:
        super().__init__()
        from Xlib import X, display

        self._X = X
        self._display = display.Display()
        self._root = self._display.screen().root
        self._mod_masks = {
            "ctrl": X.ControlMask,
            "alt": X.Mod1Mask,
            "shift": X.ShiftMask,
            "super": X.Mod4Mask,
        }
        self._relevant = X.ControlMask | X.Mod1Mask | X.ShiftMask | X.Mod4Mask
        # Caps Lock and Num Lock must not break a hotkey: grab every combination.
        self._locks = (0, X.LockMask, X.Mod2Mask, X.LockMask | X.Mod2Mask)
        self._grabs: Dict[Tuple[int, int], str] = {}
        self._wanted: Dict[str, Hotkey] = {}
        self._last: Dict[str, float] = {}
        self._commands: "queue.Queue[Tuple[str, Any, threading.Event, Dict[str, str]]]" = queue.Queue()
        self._wake_r, self._wake_w = os.pipe()
        self._thread = threading.Thread(target=self._run, name="hotkeys", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        X = self._X
        try:
            while True:
                while self._display.pending_events():
                    event = self._display.next_event()
                    if event.type == X.KeyPress:
                        action = self._grabs.get((event.detail, event.state & self._relevant))
                        now = time.monotonic()
                        # Auto-repeat while the keys are held must not fire again.
                        if action and now - self._last.get(action, 0) > self._DEBOUNCE:
                            self._last[action] = now
                            self.triggered.emit(action)
                        elif action:
                            self._last[action] = now
                readable, _, _ = select.select([self._display.fileno(), self._wake_r], [], [])
                if self._wake_r in readable:
                    os.read(self._wake_r, 64)
                    if not self._drain():
                        return
        except Exception:
            log.exception("Hotkey thread stopped")

    def _drain(self) -> bool:
        while True:
            try:
                name, payload, done, result = self._commands.get_nowait()
            except queue.Empty:
                return True
            self._ungrab_all()
            if name == "set":
                for action, hotkey in payload.items():
                    error = self._grab(action, hotkey)
                    if error:
                        result[action] = error
            done.set()
            if name == "quit":
                self._display.close()
                return False

    def _grab(self, action: str, hotkey: Hotkey) -> Optional[str]:
        from Xlib import XK
        from Xlib import error as xerror

        X = self._X
        keysym = XK.string_to_keysym(keysym_name(hotkey.key))
        keycode = self._display.keysym_to_keycode(keysym) if keysym else 0
        if not keycode:
            return "unsupported key {}".format(hotkey.key)
        mods = 0
        for m in hotkey.mods:
            mods |= self._mod_masks[m]
        failed = False
        for lock in self._locks:
            catch = xerror.CatchError(xerror.BadAccess)
            self._root.grab_key(keycode, mods | lock, True, X.GrabModeAsync, X.GrabModeAsync, onerror=catch)
            self._display.sync()
            if catch.get_error():
                failed = True
        if failed:
            for lock in self._locks:
                self._root.ungrab_key(keycode, mods | lock)
            self._display.sync()
            log.error("Failed to grab hotkey %s", hotkey.display)
            return "{} is already used by the desktop or another program".format(hotkey.display)
        self._grabs[(keycode, mods)] = action
        return None

    def _ungrab_all(self) -> None:
        for keycode, mods in list(self._grabs):
            for lock in self._locks:
                self._root.ungrab_key(keycode, mods | lock)
        self._grabs.clear()
        self._display.sync()

    def _send(self, name: str, payload: Any) -> Dict[str, str]:
        if not self._thread.is_alive():
            return {action: "hotkey thread is not running" for action in (payload or {})}
        done, result = threading.Event(), {}
        self._commands.put((name, payload, done, result))
        os.write(self._wake_w, b"x")
        done.wait(3)
        return result

    def set_hotkeys(self, hotkeys: Dict[str, Hotkey]) -> Dict[str, str]:
        self._wanted = dict(hotkeys)
        return self._send("set", self._wanted)

    def suspend(self) -> None:
        self._send("clear", None)

    def resume(self) -> None:
        self._send("set", self._wanted)

    def shutdown(self) -> None:
        if self._thread.is_alive():
            self._send("quit", None)
            self._thread.join(2)


class X11Platform(Platform):
    name = "x11"
    tracks_input = True

    def __init__(self) -> None:
        from Xlib import X, display

        self._X = X
        self._display = display.Display()
        self._screen_number = self._display.get_default_screen()
        self._root = self._display.screen().root
        self._active_window = self._display.intern_atom("_NET_ACTIVE_WINDOW")
        self._has_shape = self._display.has_extension("SHAPE")

    def create_hotkeys(self) -> HotkeyBackend:
        try:
            return X11Hotkeys()
        except Exception as error:
            log.error("Global hotkeys are not available: %s", error)
            return HotkeyBackend()

    def overlay_flags(self) -> Qt.WindowType:
        # Unmanaged windows: no window manager animation, placement or stacking can get in
        # the way of covering the whole screen at once.
        return super().overlay_flags() | Qt.X11BypassWindowManagerHint

    def input_state(self) -> Optional[InputState]:
        X = self._X
        try:
            pointer = self._root.query_pointer()
        except Exception as error:
            log.debug("query_pointer failed: %s", error)
            return None
        mask = pointer.mask
        mods = set()
        if mask & X.ControlMask:
            mods.add("ctrl")
        if mask & X.Mod1Mask:
            mods.add("alt")
        if mask & X.ShiftMask:
            mods.add("shift")
        if mask & X.Mod4Mask:
            mods.add("super")
        return InputState(pointer.root_x, pointer.root_y, bool(mask & X.Button1Mask), frozenset(mods))

    def _window(self, widget: QWidget) -> Any:
        return self._display.create_resource_object("window", int(widget.winId()))

    def set_click_through(self, widget: QWidget, enabled: bool) -> None:
        if not self._has_shape:
            super().set_click_through(widget, enabled)
            return
        from Xlib.ext import shape

        window = self._window(widget)
        try:
            if enabled:
                # An empty input region: every click falls through to the window below.
                window.shape_rectangles(shape.SO.Set, shape.SK.Input, 0, 0, 0, [])
            else:
                window.shape_mask(shape.SO.Set, shape.SK.Input, 0, 0, self._X.NONE)
            self._display.sync()
        except Exception as error:
            log.error("Changing the input shape failed: %s", error)
            super().set_click_through(widget, enabled)

    def compositing(self) -> bool:
        try:
            atom = self._display.intern_atom("_NET_WM_CM_S{}".format(self._screen_number))
            return self._display.get_selection_owner(atom) != self._X.NONE
        except Exception:
            return False

    def remember_focus(self) -> Any:
        try:
            prop = self._root.get_full_property(self._active_window, self._X.AnyPropertyType)
            if prop is not None and len(prop.value) and prop.value[0]:
                return int(prop.value[0])
        except Exception as error:
            log.debug("Reading the active window failed: %s", error)
        return None

    def restore_focus(self, token: Any) -> None:
        if not token:
            return
        from Xlib import error as xerror
        from Xlib.protocol import event

        X = self._X
        try:
            window = self._display.create_resource_object("window", int(token))
            message = event.ClientMessage(
                window=window, client_type=self._active_window, data=(32, [2, X.CurrentTime, 0, 0, 0])
            )
            mask = X.SubstructureRedirectMask | X.SubstructureNotifyMask
            self._root.send_event(message, event_mask=mask)
            # Our overlays take the keyboard without the window manager knowing, so hand it
            # back directly as well.
            window.set_input_focus(X.RevertToParent, X.CurrentTime, onerror=xerror.CatchError())
            self._display.sync()
        except Exception as error:
            log.debug("Restoring focus failed: %s", error)

    def grab_keyboard(self, widget: QWidget) -> None:
        """Gives an unmanaged overlay the keyboard, and checks for a moment that it kept it.

        Qt asks for the focus with the time of the last event it saw, which the server ignores
        if the window manager moved the focus since; and a request made before the window is
        mapped fails. So: ask with CurrentTime until the server confirms.
        """
        from PySide6.QtCore import QTimer

        widget.activateWindow()
        widget.setFocus(Qt.OtherFocusReason)
        window_id = int(widget.winId())
        timer = QTimer(widget)
        timer.setInterval(40)
        attempts = {"left": 40}

        def ensure() -> None:
            attempts["left"] -= 1
            if attempts["left"] < 0 or not widget.isVisible():
                timer.stop()
                return
            if not self._focus_window(window_id):
                widget.activateWindow()

        timer.timeout.connect(ensure)
        timer.start()
        ensure()

    def _focus_window(self, window_id: int) -> bool:
        """Sets the input focus to a window unless it has it. Returns whether it had it."""
        from Xlib import error as xerror

        X = self._X
        try:
            focus = self._display.get_input_focus().focus
            if getattr(focus, "id", focus) == window_id:
                return True
            window = self._display.create_resource_object("window", window_id)
            # BadMatch while the window is not viewable yet: the next attempt will do.
            window.set_input_focus(X.RevertToParent, X.CurrentTime, onerror=xerror.CatchError())
            self._display.sync()
        except Exception as error:
            log.debug("set_input_focus failed: %s", error)
        return False

    # Launch at login: an XDG autostart entry.

    @staticmethod
    def _autostart_file() -> Path:
        config = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
        return Path(config) / "autostart" / "voice-annotate.desktop"

    def autostart_enabled(self) -> bool:
        return self._autostart_file().is_file()

    def set_autostart(self, enabled: bool) -> None:
        path = self._autostart_file()
        if not enabled:
            if path.exists():
                path.unlink()
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        command = " ".join(shlex.quote(part) for part in launch_command())
        path.write_text(
            "[Desktop Entry]\nType=Application\nName=voice-annotate\n"
            "Comment=Talk and draw on your screen, paste the result into any coding agent\n"
            "Exec={}\nTerminal=false\nX-GNOME-Autostart-enabled=true\n".format(command),
            encoding="utf-8",
        )

    def reveal(self, path: str) -> None:
        # The FileManager1 D-Bus call selects the file in most file managers.
        tool = which("dbus-send")
        if tool:
            try:
                subprocess.run(
                    [
                        tool,
                        "--session",
                        "--dest=org.freedesktop.FileManager1",
                        "--type=method_call",
                        "/org/freedesktop/FileManager1",
                        "org.freedesktop.FileManager1.ShowItems",
                        "array:string:file://{}".format(path),
                        "string:",
                    ],
                    timeout=3,
                    check=True,
                    capture_output=True,
                )
                return
            except (OSError, subprocess.SubprocessError):
                pass
        super().reveal(path)

    def notes(self) -> List[str]:
        notes = []
        if not self.compositing():
            notes.append(
                "No compositing manager is running, so windows cannot be translucent: Walkthrough marks "
                "are drawn with hard edges and without their soft shadow."
            )
        return notes


class WaylandPlatform(X11Platform):
    """A Wayland session, reached through XWayland. Wayland keeps apps from watching the
    keyboard, the pointer or the screen, so this is a reduced mode: Snapshot and Dictation
    work (screenshots come from the desktop's own tools), started from the tray or from
    desktop shortcuts bound to the command line triggers."""

    name = "wayland"
    can_record = False
    tracks_input = False

    def create_hotkeys(self) -> HotkeyBackend:
        return HotkeyBackend()

    def input_state(self) -> Optional[InputState]:
        return None

    def overlay_flags(self) -> Qt.WindowType:
        # The compositor has to map and focus the overlay like a normal window.
        return Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint

    def notes(self) -> List[str]:
        triggers = ", ".join("voice-annotate {}".format(a.command) for a in HotkeyAction)
        return [
            "Wayland session: global hotkeys are not available to apps. Bind keys in your desktop's "
            "keyboard settings to these commands instead: {}.".format(triggers),
            "Wayland session: Walkthrough (screen recording with a click-through overlay) needs an X11 "
            "session. Snapshot and Dictation work here.",
        ]

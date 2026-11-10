"""Windows backend: Win32 through ctypes (no extra dependencies)."""

from __future__ import annotations

import ctypes
import logging
import queue
import subprocess
import threading
from ctypes import wintypes
from typing import Any, Dict, List, Optional, Tuple

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget

from ..settings import Hotkey
from .base import HotkeyBackend, InputState, Platform, launch_command

log = logging.getLogger(__name__)

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

WM_HOTKEY = 0x0312
WM_APP = 0x8000
WM_QUIT = 0x0012
PM_NOREMOVE = 0
MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN, MOD_NOREPEAT = 0x1, 0x2, 0x4, 0x8, 0x4000
GWL_EXSTYLE = -20
WS_EX_TRANSPARENT = 0x20
WS_EX_LAYERED = 0x80000
WDA_EXCLUDEFROMCAPTURE = 0x11
ASFW_ANY = 0xFFFFFFFF
VK_LBUTTON, VK_RBUTTON, VK_SHIFT, VK_CONTROL, VK_MENU, VK_LWIN, VK_RWIN = (
    0x01,
    0x02,
    0x10,
    0x11,
    0x12,
    0x5B,
    0x5C,
)
SM_SWAPBUTTON = 23

user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
user32.RegisterHotKey.restype = wintypes.BOOL
user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
user32.GetMessageW.restype = ctypes.c_int
user32.PeekMessageW.argtypes = [
    ctypes.POINTER(wintypes.MSG),
    wintypes.HWND,
    wintypes.UINT,
    wintypes.UINT,
    wintypes.UINT,
]
user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.PostThreadMessageW.restype = wintypes.BOOL
user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
user32.GetAsyncKeyState.restype = ctypes.c_short
user32.GetForegroundWindow.restype = wintypes.HWND
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.IsWindow.argtypes = [wintypes.HWND]
user32.SetWindowDisplayAffinity.argtypes = [wintypes.HWND, wintypes.DWORD]
user32.SetWindowDisplayAffinity.restype = wintypes.BOOL
user32.AllowSetForegroundWindow.argtypes = [wintypes.DWORD]
kernel32.GetCurrentThreadId.restype = wintypes.DWORD

_get_long = getattr(user32, "GetWindowLongPtrW", user32.GetWindowLongW)
_set_long = getattr(user32, "SetWindowLongPtrW", user32.SetWindowLongW)
_get_long.argtypes = [wintypes.HWND, ctypes.c_int]
_get_long.restype = ctypes.c_ssize_t
_set_long.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
_set_long.restype = ctypes.c_ssize_t

_MODS = {"ctrl": MOD_CONTROL, "alt": MOD_ALT, "shift": MOD_SHIFT, "super": MOD_WIN}
_NAMED_KEYS = {
    "Space": 0x20,
    "Tab": 0x09,
    "Return": 0x0D,
    "Escape": 0x1B,
    "Backspace": 0x08,
    "Delete": 0x2E,
    "Insert": 0x2D,
    "Home": 0x24,
    "End": 0x23,
    "PageUp": 0x21,
    "PageDown": 0x22,
    "Left": 0x25,
    "Up": 0x26,
    "Right": 0x27,
    "Down": 0x28,
}


def virtual_key(name: str) -> Optional[int]:
    """Virtual-key code for a hotkey key name."""
    if len(name) == 1 and name.isascii() and name.isalnum():
        return ord(name.upper())
    if name.startswith("F") and name[1:].isdigit() and 1 <= int(name[1:]) <= 24:
        return 0x70 + int(name[1:]) - 1
    return _NAMED_KEYS.get(name)


class WindowsHotkeys(HotkeyBackend):
    """``RegisterHotKey`` on a dedicated message-loop thread: no keyboard hook, the hotkey
    is consumed, and it works whatever has focus."""

    available = True

    def __init__(self) -> None:
        super().__init__()
        self._commands: "queue.Queue[Tuple[str, Any, threading.Event, Dict[str, str]]]" = queue.Queue()
        self._ids: Dict[int, str] = {}
        self._wanted: Dict[str, Hotkey] = {}
        self._thread_id = 0
        ready = threading.Event()
        self._thread = threading.Thread(target=self._run, args=(ready,), name="hotkeys", daemon=True)
        self._thread.start()
        ready.wait(5)

    def _run(self, ready: threading.Event) -> None:
        msg = wintypes.MSG()
        self._thread_id = kernel32.GetCurrentThreadId()
        # Create this thread's message queue before anyone posts to it.
        user32.PeekMessageW(ctypes.byref(msg), None, WM_APP, WM_APP, PM_NOREMOVE)
        ready.set()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == WM_HOTKEY:
                action = self._ids.get(int(msg.wParam))
                if action:
                    self.triggered.emit(action)
            elif msg.message == WM_APP:
                self._drain()
        self._unregister_all()

    def _drain(self) -> None:
        while True:
            try:
                name, payload, done, result = self._commands.get_nowait()
            except queue.Empty:
                return
            self._unregister_all()
            if name == "set":
                for index, (action, hotkey) in enumerate(payload.items()):
                    vk = virtual_key(hotkey.key)
                    mods = 0
                    for m in hotkey.mods:
                        mods |= _MODS[m]
                    if vk is None:
                        result[action] = "unsupported key {}".format(hotkey.key)
                    elif user32.RegisterHotKey(None, index + 1, mods | MOD_NOREPEAT, vk):
                        self._ids[index + 1] = action
                    else:
                        result[action] = "{} is already used by another program".format(hotkey.display)
                        log.error(
                            "Failed to register hotkey %s (error %d)", hotkey.display, ctypes.get_last_error()
                        )
            done.set()

    def _unregister_all(self) -> None:
        for hotkey_id in list(self._ids):
            user32.UnregisterHotKey(None, hotkey_id)
        self._ids.clear()

    def _send(self, name: str, payload: Any) -> Dict[str, str]:
        done, result = threading.Event(), {}
        self._commands.put((name, payload, done, result))
        if not user32.PostThreadMessageW(self._thread_id, WM_APP, 0, 0):
            return {action: "hotkey thread is not running" for action in (payload or {})}
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
            user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
            self._thread.join(2)


def allow_set_foreground() -> None:
    """Lets any process (i.e. the running app) take the foreground after this one."""
    user32.AllowSetForegroundWindow(ASFW_ANY)


def _hwnd(widget: QWidget) -> int:
    return int(widget.winId())


class WindowsPlatform(Platform):
    name = "windows"
    tracks_input = True
    can_exclude_from_capture = True

    def create_hotkeys(self) -> HotkeyBackend:
        return WindowsHotkeys()

    def input_state(self) -> Optional[InputState]:
        point = wintypes.POINT()
        if not user32.GetCursorPos(ctypes.byref(point)):
            return None

        def down(vk: int) -> bool:
            return bool(user32.GetAsyncKeyState(vk) & 0x8000)

        mods = set()
        if down(VK_CONTROL):
            mods.add("ctrl")
        if down(VK_MENU):
            mods.add("alt")
        if down(VK_SHIFT):
            mods.add("shift")
        if down(VK_LWIN) or down(VK_RWIN):
            mods.add("super")
        primary = VK_RBUTTON if user32.GetSystemMetrics(SM_SWAPBUTTON) else VK_LBUTTON
        return InputState(point.x, point.y, down(primary), frozenset(mods))

    def set_click_through(self, widget: QWidget, enabled: bool) -> None:
        # Toggling the extended style directly is instant and does not re-create the window.
        hwnd = _hwnd(widget)
        style = _get_long(hwnd, GWL_EXSTYLE)
        if enabled:
            style |= WS_EX_TRANSPARENT | WS_EX_LAYERED
        else:
            style &= ~WS_EX_TRANSPARENT
        _set_long(hwnd, GWL_EXSTYLE, style)

    def exclude_from_capture(self, widget: QWidget) -> bool:
        # Windows 10 2004 and later; older systems return False and the HUD stays visible.
        return bool(user32.SetWindowDisplayAffinity(_hwnd(widget), WDA_EXCLUDEFROMCAPTURE))

    def remember_focus(self) -> Any:
        return user32.GetForegroundWindow()

    def restore_focus(self, token: Any) -> None:
        if token and user32.IsWindow(token):
            user32.SetForegroundWindow(token)

    def grab_keyboard(self, widget: QWidget) -> None:
        widget.activateWindow()
        user32.SetForegroundWindow(_hwnd(widget))
        widget.setFocus(Qt.OtherFocusReason)

    def allow_foreground(self) -> None:
        allow_set_foreground()

    # Launch at login: the per-user Run key.

    _RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
    _RUN_NAME = "voice-annotate"

    def autostart_enabled(self) -> bool:
        import winreg

        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, self._RUN_KEY) as key:
                winreg.QueryValueEx(key, self._RUN_NAME)
                return True
        except OSError:
            return False

    def set_autostart(self, enabled: bool) -> None:
        import winreg

        if enabled:
            # A fresh user profile may not have the key yet.
            with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, self._RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
                command = subprocess.list2cmdline(launch_command())
                winreg.SetValueEx(key, self._RUN_NAME, 0, winreg.REG_SZ, command)
            return
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, self._RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
                winreg.DeleteValue(key, self._RUN_NAME)
        except FileNotFoundError:
            pass

    def reveal(self, path: str) -> None:
        subprocess.Popen(["explorer", "/select,", path])

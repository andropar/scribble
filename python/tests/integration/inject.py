"""Synthetic mouse and keyboard input for the desktop tests: XTEST on X11, SendInput on
Windows. Positions are native pixels of the virtual desktop."""

import sys
import time
from typing import Iterable, Sequence, Tuple

MODIFIERS = ("ctrl", "alt", "shift", "super")


class Injector:
    def move(self, x: int, y: int) -> None:
        raise NotImplementedError

    def press(self) -> None:
        raise NotImplementedError

    def release(self) -> None:
        raise NotImplementedError

    def key_down(self, name: str) -> None:
        raise NotImplementedError

    def key_up(self, name: str) -> None:
        raise NotImplementedError

    def click(self, x: int, y: int) -> None:
        self.move(x, y)
        time.sleep(0.05)
        self.press()
        time.sleep(0.08)
        self.release()

    def key(self, name: str, mods: Iterable[str] = ()) -> None:
        mods = list(mods)
        for m in mods:
            self.key_down(m)
            time.sleep(0.02)
        self.key_down(name)
        time.sleep(0.05)
        self.key_up(name)
        for m in reversed(mods):
            time.sleep(0.02)
            self.key_up(m)

    def release_all(self) -> None:
        for m in MODIFIERS:
            self.key_up(m)
        self.release()


class X11Injector(Injector):
    _NAMES = {
        "ctrl": "Control_L",
        "alt": "Alt_L",
        "shift": "Shift_L",
        "super": "Super_L",
        "Return": "Return",
        "Escape": "Escape",
        "Tab": "Tab",
    }

    def __init__(self) -> None:
        from Xlib import X, display
        from Xlib.ext import xtest

        self._X, self._xtest = X, xtest
        self._display = display.Display()

    def _fake(self, *args, **kwargs) -> None:
        self._xtest.fake_input(self._display, *args, **kwargs)
        self._display.sync()

    def move(self, x: int, y: int) -> None:
        self._fake(self._X.MotionNotify, x=int(x), y=int(y))

    def press(self) -> None:
        self._fake(self._X.ButtonPress, 1)

    def release(self) -> None:
        self._fake(self._X.ButtonRelease, 1)

    def _keycode(self, name: str) -> int:
        from Xlib import XK

        keysym = XK.string_to_keysym(self._NAMES.get(name, name.lower() if len(name) == 1 else name))
        keycode = self._display.keysym_to_keycode(keysym)
        assert keycode, "no keycode for {}".format(name)
        return keycode

    def key_down(self, name: str) -> None:
        self._fake(self._X.KeyPress, self._keycode(name))

    def key_up(self, name: str) -> None:
        self._fake(self._X.KeyRelease, self._keycode(name))


class WindowsInjector(Injector):
    _VK = {
        "ctrl": 0x11,
        "alt": 0x12,
        "shift": 0x10,
        "super": 0x5B,
        "Return": 0x0D,
        "Escape": 0x1B,
        "Tab": 0x09,
    }

    def __init__(self) -> None:
        import ctypes
        from ctypes import wintypes

        self._ctypes = ctypes
        self._user32 = ctypes.WinDLL("user32", use_last_error=True)
        pointer_sized = ctypes.c_size_t

        class MOUSEINPUT(ctypes.Structure):
            _fields_ = [
                ("dx", wintypes.LONG),
                ("dy", wintypes.LONG),
                ("mouseData", wintypes.DWORD),
                ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD),
                ("dwExtraInfo", pointer_sized),
            ]

        class KEYBDINPUT(ctypes.Structure):
            _fields_ = [
                ("wVk", wintypes.WORD),
                ("wScan", wintypes.WORD),
                ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD),
                ("dwExtraInfo", pointer_sized),
            ]

        class _Union(ctypes.Union):
            _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT)]

        class INPUT(ctypes.Structure):
            _fields_ = [("type", wintypes.DWORD), ("u", _Union)]

        self._INPUT, self._MOUSEINPUT, self._KEYBDINPUT = INPUT, MOUSEINPUT, KEYBDINPUT
        self._user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
        self._user32.SendInput.restype = wintypes.UINT

    def _send(self, item) -> None:
        sent = self._user32.SendInput(1, self._ctypes.byref(item), self._ctypes.sizeof(self._INPUT))
        assert sent == 1, "SendInput was blocked (error {})".format(self._ctypes.get_last_error())

    def _mouse(self, flags: int, x: int = 0, y: int = 0) -> None:
        item = self._INPUT(type=0)
        item.u.mi = self._MOUSEINPUT(x, y, 0, flags, 0, 0)
        self._send(item)

    def move(self, x: int, y: int) -> None:
        u = self._user32
        vx, vy = u.GetSystemMetrics(76), u.GetSystemMetrics(77)  # SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN
        vw, vh = u.GetSystemMetrics(78), u.GetSystemMetrics(79)
        nx = int(round((x - vx) * 65535 / max(vw - 1, 1)))
        ny = int(round((y - vy) * 65535 / max(vh - 1, 1)))
        self._mouse(0x0001 | 0x8000 | 0x4000, nx, ny)  # MOVE | ABSOLUTE | VIRTUALDESK

    def press(self) -> None:
        self._mouse(0x0002)

    def release(self) -> None:
        self._mouse(0x0004)

    def _key(self, name: str, up: bool) -> None:
        vk = self._VK.get(name) or ord(name.upper())
        item = self._INPUT(type=1)
        item.u.ki = self._KEYBDINPUT(vk, 0, 0x0002 if up else 0, 0, 0)
        self._send(item)

    def key_down(self, name: str) -> None:
        self._key(name, False)

    def key_up(self, name: str) -> None:
        self._key(name, True)


def create() -> Injector:
    if sys.platform == "win32":
        return WindowsInjector()
    return X11Injector()

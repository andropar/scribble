"""User settings, persisted as JSON in ``~/.scribble/settings.json``."""

from __future__ import annotations

import json
import logging
import sys
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import paths

log = logging.getLogger(__name__)

MODIFIER_ORDER = ("ctrl", "alt", "shift", "super")
SUPER_NAME = "Win" if sys.platform == "win32" else "Super"
_MOD_LABELS = {"ctrl": "Ctrl", "alt": "Alt", "shift": "Shift", "super": SUPER_NAME}


@dataclass(frozen=True)
class Hotkey:
    """A global shortcut: modifiers plus one key name (``"S"``, ``"F5"``, ``"Space"`` …)."""

    mods: Tuple[str, ...]
    key: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "mods", tuple(m for m in MODIFIER_ORDER if m in self.mods))

    @property
    def display(self) -> str:
        return "+".join([_MOD_LABELS[m] for m in self.mods] + [self.key])

    def to_json(self) -> Dict[str, Any]:
        return {"mods": list(self.mods), "key": self.key}

    @staticmethod
    def from_json(d: Dict[str, Any]) -> "Hotkey":
        return Hotkey(tuple(d.get("mods", ())), str(d["key"]))


class HotkeyAction(str, Enum):
    SNAPSHOT = "snapshot"
    WALKTHROUGH = "walkthrough"
    DICTATION = "dictation"
    FINISH = "finish"
    TOGGLE_DRAW = "toggleDraw"

    @property
    def title(self) -> str:
        return {
            "snapshot": "Snapshot (start / next shot)",
            "walkthrough": "Walkthrough (start / stop)",
            "dictation": "Dictation, voice only (start / stop)",
            "finish": "Finish capture",
            "toggleDraw": "Toggle draw mode (Walkthrough)",
        }[self.value]

    @property
    def default_hotkey(self) -> Hotkey:
        # Ctrl+Alt+Shift is free on Windows and the common Linux desktops. Walkthrough is W
        # because GNOME reserves Ctrl+Alt+Shift+R for its own screen recorder.
        key = {"snapshot": "S", "walkthrough": "W", "dictation": "T", "finish": "F", "toggleDraw": "D"}[
            self.value
        ]
        return Hotkey(("ctrl", "alt", "shift"), key)

    @property
    def command(self) -> str:
        """Name of the matching command line trigger (``scribble snapshot`` …)."""
        return "toggle-draw" if self is HotkeyAction.TOGGLE_DRAW else self.value


class DrawModifier(str, Enum):
    CTRL_ALT = "ctrlAlt"
    CTRL_SHIFT = "ctrlShift"
    ALT_SUPER = "altSuper"
    OFF = "off"

    @property
    def mods(self) -> Tuple[str, ...]:
        return {
            "ctrlAlt": ("ctrl", "alt"),
            "ctrlShift": ("ctrl", "shift"),
            "altSuper": ("alt", "super"),
            "off": (),
        }[self.value]

    @property
    def symbol(self) -> str:
        return "+".join(_MOD_LABELS[m] for m in self.mods)

    @property
    def title(self) -> str:
        return "Hold {}".format(self.symbol) if self.mods else "Off (toggle hotkey only)"

    def matches(self, held: Any) -> bool:
        """True when exactly this modifier combination is held."""
        return bool(self.mods) and set(held) == set(self.mods)


class WhisperModel(str, Enum):
    AUTO = "auto"
    LARGE_TURBO = "large-v3-turbo"
    SMALL_EN = "small.en"
    BASE_EN = "base.en"
    TINY_EN = "tiny.en"

    @property
    def title(self) -> str:
        return {
            "auto": "Auto (Large v3 Turbo on a GPU, Small English on the CPU)",
            "large-v3-turbo": "Large v3 Turbo (best, ~1.6 GB)",
            "small.en": "Small English (~480 MB)",
            "base.en": "Base English (fast, ~145 MB)",
            "tiny.en": "Tiny English (fastest, ~75 MB)",
        }[self.value]


class WhisperCompute(str, Enum):
    AUTO = "auto"
    CPU = "cpu"
    CUDA = "cuda"

    @property
    def title(self) -> str:
        return {
            "auto": "Auto (NVIDIA GPU if it works, else CPU)",
            "cpu": "CPU",
            "cuda": "NVIDIA GPU (CUDA)",
        }[self.value]


class ClipboardFormat(str, Enum):
    MARKDOWN_FILE = "markdownFile"
    UNIVERSAL = "universal"
    TEXT_AND_PATHS = "textAndPaths"
    IMAGES_AND_TEXT = "imagesAndText"
    IMAGES_ONLY = "imagesOnly"
    COMPOSITE = "composite"

    @property
    def title(self) -> str:
        return {
            "markdownFile": "capture.md only (agents that read files)",
            "universal": "capture.md + images (web chats)",
            "textAndPaths": "Text with image paths",
            "imagesAndText": "Images and text",
            "imagesOnly": "Images only",
            "composite": "Single composite image",
        }[self.value]


_DEFAULTS: Dict[str, Any] = {
    "hotkeys": {},
    "model": WhisperModel.AUTO.value,
    "compute": WhisperCompute.AUTO.value,
    "vocabulary": "",
    "maxFrames": 30,
    "imageLongEdge": 1568,
    "detailCrops": True,
    "maxWalkthroughMinutes": 10,
    "fadeSeconds": 3.0,
    "drawModifier": DrawModifier.CTRL_ALT.value,
    "allDisplays": True,
    "keepOriginals": False,
    "clipboardFormat": ClipboardFormat.MARKDOWN_FILE.value,
    "retentionDays": 14,
    "playSounds": True,
    "onboardingShown": False,
}

_ENUMS = {
    "model": WhisperModel,
    "compute": WhisperCompute,
    "drawModifier": DrawModifier,
    "clipboardFormat": ClipboardFormat,
}

_RANGES = {
    "maxFrames": (4, 80),
    "imageLongEdge": (512, 4096),
    "maxWalkthroughMinutes": (1, 60),
    "fadeSeconds": (1.0, 10.0),
    "retentionDays": (1, 365),
}


class AppSettings:
    """Typed access to the settings file. Every change is saved at once and announced to
    ``subscribe``d listeners with the name of the key."""

    def __init__(self, path: Optional[Path] = None):
        self._path = path
        self._values: Dict[str, Any] = dict(_DEFAULTS)
        self._listeners: List[Callable[[str], None]] = []
        self.load()

    @property
    def path(self) -> Path:
        return self._path or paths.settings_file()

    def load(self) -> None:
        values = dict(_DEFAULTS)
        try:
            stored = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            stored = {}
        except (OSError, ValueError) as error:
            log.error("Could not read %s, using defaults: %s", self.path, error)
            stored = {}
        for key, default in _DEFAULTS.items():
            if key in stored and self._valid(key, stored[key]):
                values[key] = stored[key]
        self._values = values

    @staticmethod
    def _valid(key: str, value: Any) -> bool:
        default = _DEFAULTS[key]
        if key in _ENUMS:
            return value in [e.value for e in _ENUMS[key]]
        if isinstance(default, bool):
            return isinstance(value, bool)
        if isinstance(default, (int, float)):
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                return False
            lo, hi = _RANGES.get(key, (value, value))
            return lo <= value <= hi
        if isinstance(default, dict):
            return isinstance(value, dict)
        return isinstance(value, str)

    def save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(self._values, indent=2, sort_keys=True), encoding="utf-8")
            tmp.replace(self.path)
        except OSError as error:
            log.error("Saving settings failed: %s", error)

    def subscribe(self, listener: Callable[[str], None]) -> None:
        self._listeners.append(listener)

    def get(self, key: str) -> Any:
        return self._values[key]

    def set(self, key: str, value: Any) -> None:
        if isinstance(value, Enum):
            value = value.value
        if key not in _DEFAULTS:
            raise KeyError(key)
        if not self._valid(key, value):
            raise ValueError("Invalid value for {}: {!r}".format(key, value))
        if self._values.get(key) == value:
            return
        self._values[key] = value
        self.save()
        for listener in list(self._listeners):
            listener(key)

    # Typed accessors.

    @property
    def model(self) -> WhisperModel:
        return WhisperModel(self._values["model"])

    @property
    def compute(self) -> WhisperCompute:
        return WhisperCompute(self._values["compute"])

    @property
    def draw_modifier(self) -> DrawModifier:
        return DrawModifier(self._values["drawModifier"])

    @property
    def clipboard_format(self) -> ClipboardFormat:
        return ClipboardFormat(self._values["clipboardFormat"])

    @property
    def vocabulary(self) -> str:
        return self._values["vocabulary"]

    @property
    def max_frames(self) -> int:
        return int(self._values["maxFrames"])

    @property
    def image_long_edge(self) -> int:
        return int(self._values["imageLongEdge"])

    @property
    def detail_crops(self) -> bool:
        return self._values["detailCrops"]

    @property
    def max_walkthrough_minutes(self) -> int:
        return int(self._values["maxWalkthroughMinutes"])

    @property
    def fade_seconds(self) -> float:
        return float(self._values["fadeSeconds"])

    @property
    def all_displays(self) -> bool:
        return self._values["allDisplays"]

    @property
    def keep_originals(self) -> bool:
        return self._values["keepOriginals"]

    @property
    def retention_days(self) -> int:
        return int(self._values["retentionDays"])

    @property
    def play_sounds(self) -> bool:
        return self._values["playSounds"]

    @property
    def onboarding_shown(self) -> bool:
        return self._values["onboardingShown"]

    def hotkey(self, action: HotkeyAction) -> Hotkey:
        stored = self._values["hotkeys"].get(action.value)
        if stored:
            try:
                return Hotkey.from_json(stored)
            except (KeyError, TypeError):
                log.error("Ignoring malformed hotkey for %s", action.value)
        return action.default_hotkey

    def set_hotkey(self, action: HotkeyAction, hotkey: Hotkey) -> None:
        hotkeys = dict(self._values["hotkeys"])
        hotkeys[action.value] = hotkey.to_json()
        self.set("hotkeys", hotkeys)

    def reset_hotkeys(self) -> None:
        self.set("hotkeys", {})

    def hotkeys(self) -> Dict[HotkeyAction, Hotkey]:
        return {action: self.hotkey(action) for action in HotkeyAction}

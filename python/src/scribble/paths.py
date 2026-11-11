"""Where captures, models and settings live: ``~/.scribble`` on every platform, the
same layout as the macOS app (no spaces, so terminals and agents handle the paths).

Before 0.5 the app was called voice-annotate and used ``~/.voice-annotate``;
``adopt_old_folder`` takes that folder over on the first start."""

from __future__ import annotations

import json
import logging
import os
import shutil
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

_FOLDER_FORMAT = "%Y-%m-%d_%H-%M-%S"


def root() -> Path:
    override = os.environ.get("SCRIBBLE_HOME")
    return Path(override) if override else Path.home() / ".scribble"


def adopt_old_folder(old: Optional[Path] = None, new: Optional[Path] = None) -> bool:
    """Moves the folder of voice-annotate (captures, models, settings) to where Scribble
    keeps it. A link stays at the old place where the system allows one, so paths in earlier
    captures still open. Returns whether a folder was moved."""
    if new is None and os.environ.get("SCRIBBLE_HOME"):
        return False
    old = old or Path.home() / ".voice-annotate"
    new = new or root()
    if old.is_symlink() or not old.is_dir() or new.exists():
        return False
    try:
        old.rename(new)
    except OSError as error:
        log.error("Could not move %s to %s: %s", old, new, error)
        return False
    try:
        old.symlink_to(new, target_is_directory=True)
    except OSError:
        pass  # Windows without the privilege to create links
    # Paths the app stored itself: the list of recent captures.
    recents = new / "recent.json"
    try:
        stored = json.loads(recents.read_text(encoding="utf-8"))
        recents.write_text(json.dumps(_moved(stored, str(old), str(new)), indent=2), encoding="utf-8")
    except (OSError, ValueError):
        pass
    old_log = new / "voice-annotate.log"
    if old_log.is_file() and not (new / "scribble.log").exists():
        old_log.rename(new / "scribble.log")
    return True


def _moved(value, old: str, new: str):
    """``value`` (parsed JSON) with every path under ``old`` pointing under ``new``."""
    if isinstance(value, str):
        return new + value[len(old) :] if value == old or value.startswith((old + "/", old + "\\")) else value
    if isinstance(value, list):
        return [_moved(item, old, new) for item in value]
    if isinstance(value, dict):
        return {key: _moved(item, old, new) for key, item in value.items()}
    return value


def captures() -> Path:
    return root() / "captures"


def models() -> Path:
    return root() / "models"


def recents_file() -> Path:
    return root() / "recent.json"


def settings_file() -> Path:
    return root() / "settings.json"


def log_file() -> Path:
    return root() / "scribble.log"


def ensure() -> None:
    for directory in (root(), captures(), models()):
        directory.mkdir(parents=True, exist_ok=True)


def new_capture_folder(date: Optional[datetime] = None) -> Path:
    ensure()
    base = (date or datetime.now()).strftime(_FOLDER_FORMAT)
    folder = captures() / base
    n = 2
    while folder.exists():
        folder = captures() / "{}-{}".format(base, n)
        n += 1
    folder.mkdir(parents=True)
    return folder


def _folder_date(folder: Path) -> datetime:
    """Creation time of a capture folder: from its name (not every filesystem records a
    creation date), else its modification time."""
    try:
        return datetime.strptime(folder.name[:19], _FOLDER_FORMAT)
    except ValueError:
        return datetime.fromtimestamp(folder.stat().st_mtime)


def prune(days: int, now: Optional[datetime] = None) -> int:
    """Deletes capture folders older than ``days``. Returns how many were removed."""
    if days <= 0 or not captures().is_dir():
        return 0
    cutoff = (now or datetime.now()) - timedelta(days=days)
    removed = 0
    for item in captures().iterdir():
        if item.name.startswith(".") or not item.is_dir():
            continue
        try:
            if _folder_date(item) < cutoff:
                shutil.rmtree(item)
                removed += 1
                log.info("Pruned old capture %s", item.name)
        except OSError as error:
            log.error("Failed to prune %s: %s", item, error)
    return removed


def now() -> float:
    """The single clock for a capture: monotonic seconds. Strokes, clicks, audio buffers and
    video frames are all stamped with it, which is what aligns a stroke with a spoken word."""
    return time.perf_counter()

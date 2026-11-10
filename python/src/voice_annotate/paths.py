"""Where captures, models and settings live: ``~/.voice-annotate`` on every platform, the
same layout as the macOS app (no spaces, so terminals and agents handle the paths)."""

from __future__ import annotations

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
    override = os.environ.get("VOICE_ANNOTATE_HOME")
    return Path(override) if override else Path.home() / ".voice-annotate"


def captures() -> Path:
    return root() / "captures"


def models() -> Path:
    return root() / "models"


def recents_file() -> Path:
    return root() / "recent.json"


def settings_file() -> Path:
    return root() / "settings.json"


def log_file() -> Path:
    return root() / "voice-annotate.log"


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

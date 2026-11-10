"""Clipboard output and the list of recent captures.

Unlike the macOS pasteboard, the Windows and X11 clipboards hold one item with several
formats. So a capture goes out as plain text (the markdown) plus a file list: terminals
paste the text, chat apps and browsers attach the files.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Callable, List, Optional, Sequence

from PySide6.QtCore import QMimeData, QPointF, QRectF, Qt, QUrl
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QGuiApplication, QImage, QPainter

from . import paths, render
from .processor import RecentCapture
from .settings import ClipboardFormat

log = logging.getLogger(__name__)

GNOME_FILES_MIME = "x-special/gnome-copied-files"


def _wl_copy() -> Optional[str]:
    """``wl-copy`` on Wayland sessions: an XWayland client that is not focused cannot always
    set the clipboard there, but plain text can go through wl-clipboard."""
    if sys.platform.startswith("linux") and os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland":
        return shutil.which("wl-copy")
    return None


def _set_text_wayland(text: str) -> bool:
    tool = _wl_copy()
    if not tool:
        return False
    try:
        subprocess.run(
            [tool, "--type", "text/plain;charset=utf-8"], input=text.encode("utf-8"), timeout=5, check=True
        )
        return True
    except (OSError, subprocess.SubprocessError) as error:
        log.warning("wl-copy failed, using the X11 clipboard: %s", error)
        return False


def write_text(text: str) -> None:
    if _set_text_wayland(text):
        return
    QGuiApplication.clipboard().setText(text)


def _file_list(mime: QMimeData, files: Sequence[str]) -> None:
    urls = [QUrl.fromLocalFile(str(f)) for f in files]
    mime.setUrls(urls)
    if sys.platform.startswith("linux"):
        # What GTK file managers and many GTK apps expect for copied files.
        lines = ["copy"] + [u.toString(QUrl.FullyEncoded) for u in urls]
        mime.setData(GNOME_FILES_MIME, "\n".join(lines).encode("utf-8"))


def build_mime(capture: RecentCapture, fmt: ClipboardFormat) -> QMimeData:
    """The clipboard content for ``capture`` in the given format."""
    text = Path(capture.markdown_path).read_text(encoding="utf-8")
    images = [p for p in capture.image_paths if os.path.isfile(p)]
    mime = QMimeData()
    files: List[str] = []
    with_text = True

    if fmt == ClipboardFormat.UNIVERSAL:
        # Terminals paste the text; chat apps attach the files, so the transcript also
        # travels as capture.md.
        files = [capture.markdown_path] + images
    elif fmt == ClipboardFormat.TEXT_AND_PATHS:
        files = []
    elif fmt == ClipboardFormat.IMAGES_AND_TEXT:
        files = images
    elif fmt == ClipboardFormat.IMAGES_ONLY:
        files, with_text = images, not images
    elif fmt == ClipboardFormat.COMPOSITE:
        files, with_text = [render_composite(capture, text)], False
    else:
        files = [capture.markdown_path]

    if files:
        _file_list(mime, files)
    if with_text:
        mime.setText(text)
    # A single image also goes out as bitmap data, for targets that take no files.
    picture = [f for f in files if f.lower().endswith(".png")]
    if len(picture) == 1 and len(files) == 1:
        image = QImage(picture[0])
        if not image.isNull():
            mime.setImageData(image)
    return mime


def write(capture: RecentCapture, fmt: ClipboardFormat) -> None:
    if fmt == ClipboardFormat.TEXT_AND_PATHS:
        write_text(Path(capture.markdown_path).read_text(encoding="utf-8"))
        return
    QGuiApplication.clipboard().setMimeData(build_mime(capture, fmt))


# MARK: composite image

COMPOSITE_WIDTH = 1400
COMPOSITE_MAX_HEIGHT = 7800
COMPOSITE_MARGIN = 24


def transcript_text(markdown: str) -> str:
    """Transcript section of capture.md without markdown emphasis."""
    lines = markdown.split("\n")
    if "## Transcript" in lines:
        lines = lines[lines.index("## Transcript") + 1 :]
    for i, line in enumerate(lines):
        if line.startswith("Folder: "):
            lines = lines[:i]
            break
    return "\n".join(lines).replace("**", "").replace("## ", "").strip()


def render_composite(capture: RecentCapture, markdown: str) -> str:
    """Renders a single tall image: all frames stacked, transcript text underneath."""
    width, margin, max_height = COMPOSITE_WIDTH, COMPOSITE_MARGIN, COMPOSITE_MAX_HEIGHT
    frames = [QImage(p) for p in capture.frame_paths if os.path.isfile(p)]
    frames = [f for f in frames if not f.isNull()]
    text = transcript_text(markdown)

    text_font = QFont()
    text_font.setPixelSize(17)
    label_font = QFont()
    label_font.setPixelSize(18)
    label_font.setBold(True)
    text_width = width - 2 * margin
    flags = int(Qt.TextWordWrap | Qt.AlignLeft | Qt.AlignTop)
    measured = QFontMetricsF(text_font).boundingRect(QRectF(0, 0, text_width, 1e6), flags, text)
    text_height = min(float(int(measured.height()) + 1), max_height * 0.5)

    label_height = 30
    image_width = width - 2 * margin
    heights = [image_width * f.height() / max(f.width(), 1) for f in frames]
    fixed = len(frames) * (label_height + margin) + text_height + 3 * margin
    available = max_height - fixed
    total = sum(heights)
    scale = 1.0
    if total > available and total > 0:
        scale = max(0.2, available / total)
        heights = [h * scale for h in heights]
    height = int(fixed + sum(heights)) + 1

    image = QImage(width, height, QImage.Format_RGB32)
    image.fill(QColor(255, 255, 255))
    painter = QPainter(image)
    painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
    painter.setRenderHint(QPainter.TextAntialiasing, True)
    y = float(margin)
    for i, frame in enumerate(frames):
        painter.setFont(label_font)
        painter.setPen(QColor(85, 85, 85))
        painter.drawText(
            QRectF(margin, y, text_width, label_height),
            int(Qt.AlignLeft | Qt.AlignTop),
            "frame {}".format(i + 1),
        )
        y += label_height
        w = image_width * scale
        target = QRectF((width - w) / 2, y, w, heights[i])
        scaled = frame.scaled(
            int(target.width()), int(target.height()), Qt.IgnoreAspectRatio, Qt.SmoothTransformation
        )
        painter.drawImage(target.topLeft(), scaled)
        y += heights[i] + margin
    painter.setFont(text_font)
    painter.setPen(QColor(0, 0, 0))
    painter.drawText(QRectF(margin, y + margin, text_width, text_height), flags, text)
    painter.end()

    path = os.path.join(capture.folder, "composite.png")
    render.write_png(image, path)
    return path


# MARK: recent captures


class RecentsStore:
    """The last few captures, so any of them can be copied again in another format."""

    MAX_ITEMS = 10

    def __init__(self, path: Optional[Path] = None):
        self._path = path
        self.items: List[RecentCapture] = []
        self._listeners: List[Callable[[], None]] = []
        self._load()

    @property
    def path(self) -> Path:
        return self._path or paths.recents_file()

    def subscribe(self, listener: Callable[[], None]) -> None:
        self._listeners.append(listener)

    def add(self, capture: RecentCapture) -> None:
        self.items = [c for c in self.items if c.folder != capture.folder]
        self.items.insert(0, capture)
        self.items = self.items[: self.MAX_ITEMS]
        self._save()

    def prune_missing(self) -> None:
        kept = [c for c in self.items if os.path.isfile(c.markdown_path)]
        if len(kept) != len(self.items):
            self.items = kept
            self._save()

    def _load(self) -> None:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            self.items = [RecentCapture.from_json(item) for item in raw]
        except FileNotFoundError:
            self.items = []
        except (OSError, ValueError, KeyError, TypeError) as error:
            log.error("Could not read %s: %s", self.path, error)
            self.items = []
        self.prune_missing()

    def _save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps([c.to_json() for c in self.items], indent=2), encoding="utf-8")
            tmp.replace(self.path)
        except OSError as error:
            log.error("Saving recents failed: %s", error)
        for listener in list(self._listeners):
            listener()

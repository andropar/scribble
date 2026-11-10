"""Records one display into an .mp4 while computing live luminance thumbnails for keyframe
selection (the counterpart of ``WalkthroughRecorder`` in ScreenCapture.swift)."""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Callable, List, Optional, Tuple

import numpy as np
from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPen, QPolygonF

from . import paths, video
from .core.geometry import Size
from .core.models import FrameSample
from .processor import RecordingResult
from .screens import Display, ScreenGrabber

log = logging.getLogger(__name__)

FRAME_RATE = 10


def cursor_sprite(scale: float) -> np.ndarray:
    """A pointer arrow as premultiplied BGRA; screen grabs do not include the real cursor."""
    size = max(8, int(round(22 * scale)))
    image = QImage(size, size, QImage.Format_ARGB32_Premultiplied)
    image.fill(Qt.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.scale(size / 22.0, size / 22.0)
    painter.setPen(QPen(QColor(0, 0, 0), 1.2))
    painter.setBrush(QColor(255, 255, 255))
    painter.drawPolygon(
        QPolygonF(
            [
                QPointF(1, 1),
                QPointF(1, 16.5),
                QPointF(5, 13),
                QPointF(8, 19.5),
                QPointF(10.6, 18.3),
                QPointF(7.7, 12),
                QPointF(13, 12),
            ]
        )
    )
    painter.end()
    buf = np.frombuffer(image.constBits(), dtype=np.uint8, count=image.sizeInBytes())
    return buf.reshape(size, image.bytesPerLine())[:, : size * 4].reshape(size, size, 4).copy()


def blend_sprite(frame: np.ndarray, sprite: np.ndarray, x: int, y: int) -> None:
    """Alpha-blends a premultiplied BGRA sprite into ``frame`` with its top-left at (x, y)."""
    h, w = frame.shape[0], frame.shape[1]
    sh, sw = sprite.shape[0], sprite.shape[1]
    x0, y0, x1, y1 = max(x, 0), max(y, 0), min(x + sw, w), min(y + sh, h)
    if x0 >= x1 or y0 >= y1:
        return
    part = sprite[y0 - y : y1 - y, x0 - x : x1 - x].astype(np.uint16)
    target = frame[y0:y1, x0:x1, :3].astype(np.uint16)
    alpha = part[..., 3:4]
    frame[y0:y1, x0:x1, :3] = (part[..., :3] + target * (255 - alpha) // 255).astype(np.uint8)


class WalkthroughRecorder:
    """Grabs a display about ten times a second on its own thread. Frames that differ from
    the previous one are encoded with their real timestamps and sampled as thumbnails."""

    def __init__(
        self,
        grabber: ScreenGrabber,
        display: Display,
        output: Path,
        cursor: Optional[Callable[[], Optional[Tuple[float, float]]]] = None,
    ):
        self._grabber = grabber
        self._rect = display.native
        self._output = Path(output)
        self._cursor = cursor
        self._sprite = cursor_sprite(display.dpr) if cursor is not None else None
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._run, name="recorder-{}".format(display.index), daemon=True
        )
        self._samples: List[FrameSample] = []
        self._first_time: Optional[float] = None
        self._last_time: Optional[float] = None
        self._last_frame: Optional[np.ndarray] = None
        self._writer: Optional[video.VideoWriter] = None
        self._failed: Optional[BaseException] = None
        #: Set once the first frame is on disk.
        self.started = threading.Event()
        #: Called from the recorder thread if recording breaks down.
        self.on_error: Optional[Callable[[BaseException], None]] = None
        self.display_index = display.index

    def start(self) -> None:
        self._thread.start()

    @property
    def failed(self) -> Optional[BaseException]:
        return self._failed

    def _grab(self) -> np.ndarray:
        frame = self._grabber.grab_array(self._rect)
        if self._cursor is not None and self._sprite is not None:
            position = self._cursor()
            if position is not None:
                frame = np.array(frame)
                blend_sprite(
                    frame, self._sprite, int(position[0] - self._rect[0]), int(position[1] - self._rect[1])
                )
        return frame

    def _run(self) -> None:
        interval = 1.0 / FRAME_RATE
        try:
            next_due = paths.now()
            while not self._stop.is_set():
                t = paths.now()
                frame = self._grab()
                if self._writer is None:
                    self._writer = video.VideoWriter(
                        self._output, frame.shape[1], frame.shape[0], fps=FRAME_RATE
                    )
                    self._first_time = t
                    log.info("Walkthrough recording %dx%d started", self._writer.width, self._writer.height)
                # An unchanged screen costs one comparison instead of an encode.
                if self._last_frame is None or not np.array_equal(frame, self._last_frame):
                    self._writer.write(frame, t - self._first_time)
                    self._samples.append(FrameSample(t, video.thumbnail(frame)))
                    self._last_frame = frame
                    self._last_time = t
                    self.started.set()
                next_due += interval
                delay = next_due - paths.now()
                if delay > 0:
                    self._stop.wait(delay)
                else:
                    next_due = paths.now()
        except BaseException as error:
            self._failed = error
            log.exception("Screen recording stopped")
            if self.on_error is not None and not self._stop.is_set():
                self.on_error(error)
        finally:
            self.started.set()

    def stop(self) -> RecordingResult:
        """Stops and finishes the file. Blocks; call from a worker thread."""
        self._stop.set()
        if self._thread.is_alive():
            self._thread.join(15)
        stop_time = paths.now()
        writer, self._writer = self._writer, None
        size = Size(writer.width, writer.height) if writer is not None else Size(0, 0)
        if writer is None or self._first_time is None or self._last_frame is None:
            return RecordingResult(None, None, self._samples, size)
        try:
            # Hold the last frame until the end, so the video is as long as the recording.
            if self._last_time is not None and stop_time - self._last_time > 0.05:
                writer.write(self._last_frame, stop_time - self._first_time)
            writer.close()
        except Exception as error:
            log.error("Video writing failed: %s", error)
            return RecordingResult(None, None, self._samples, size)
        return RecordingResult(self._output, self._first_time, self._samples, size)

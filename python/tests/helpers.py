"""Shared builders for pipeline tests."""

import math
from datetime import datetime
from typing import List

import numpy as np
from PySide6.QtCore import QPointF, QRectF
from PySide6.QtGui import QColor, QImage, QLinearGradient, QPainter

from voice_annotate import render
from voice_annotate.core.geometry import Size
from voice_annotate.core.models import (
    BLUE,
    GREEN,
    MAGENTA,
    RED,
    WHITE,
    YELLOW,
    Annotation,
    AnnotationTool,
    SpeechSegment,
    Word,
)
from voice_annotate.transcriber import TranscriptionOutput


class FakeTranscriber:
    """Returns a scripted transcript instead of running Whisper."""

    def __init__(self, text="so this button is broken here", start=0.5, step=0.4, fail=False):
        self.text, self.start, self.step, self.fail = text, start, step, fail
        self.calls = 0

    def transcribe(self, samples, model, compute, vocabulary=""):
        self.calls += 1
        if self.fail:
            raise RuntimeError("model exploded")
        tokens = self.text.split(" ")
        words = [
            Word(" " + w, self.start + i * self.step, self.start + i * self.step + 0.3)
            for i, w in enumerate(tokens)
        ]
        segments = [SpeechSegment(self.text, self.start, words[-1].end)] if words else []
        return TranscriptionOutput(words=words, segments=segments)


def solid_frame(w: int, h: int, level: int) -> np.ndarray:
    frame = np.full((h, w, 4), level, dtype=np.uint8)
    frame[..., 3] = 255
    return frame


def gradient_image(canvas: Size, scale: float) -> QImage:
    """The demo backdrop of the macOS app's ``--render-demo``."""
    w, h = int(canvas.width * scale), int(canvas.height * scale)
    image = render.new_image(w, h)
    painter = QPainter(image)
    gradient = QLinearGradient(QPointF(0, h), QPointF(w, 0))
    gradient.setColorAt(0, QColor.fromRgbF(0.93, 0.94, 0.97))
    gradient.setColorAt(1, QColor.fromRgbF(0.75, 0.8, 0.9))
    painter.fillRect(QRectF(0, 0, w, h), gradient)
    painter.end()
    return image


def tone(seconds: float, freq: float = 440.0, rate: int = 16_000) -> np.ndarray:
    t = np.arange(int(seconds * rate)) / rate
    return (0.3 * np.sin(2 * np.pi * freq * t)).astype(np.float32)

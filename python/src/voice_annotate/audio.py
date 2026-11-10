"""Microphone recording into a 16 kHz mono buffer (Whisper's input format)."""

from __future__ import annotations

import logging
import math
import threading
from dataclasses import dataclass
from typing import Any, Callable, List, Optional

import numpy as np

from . import paths, video

log = logging.getLogger(__name__)

SAMPLE_RATE = video.SAMPLE_RATE


class MicrophoneError(RuntimeError):
    """The microphone cannot be used (no device, no access, or no audio backend)."""


@dataclass
class AudioResult:
    #: 16 kHz mono float samples.
    samples: np.ndarray
    #: Clock time (``paths.now()``) of the first sample, if any audio arrived.
    first_sample_time: Optional[float]

    @property
    def duration(self) -> float:
        return len(self.samples) / SAMPLE_RATE


def level_from_rms(rms: float) -> float:
    """Maps an RMS amplitude to a roughly perceptual 0…1 meter level."""
    db = 20 * math.log10(max(rms, 1e-6))
    return max(0.0, min(1.0, (db + 55) / 45))


def input_device_name() -> Optional[str]:
    """Name of the default microphone, or None if there is none (or no audio backend)."""
    try:
        import sounddevice as sd

        return str(sd.query_devices(kind="input")["name"])
    except Exception as error:  # PortAudio missing, no device, …
        log.info("No input device: %s", error)
        return None


def _open_sounddevice_stream(callback: Callable[..., None]) -> Any:
    """Opens the default input, preferring 16 kHz mono and falling back to the device's
    own format (which is then converted when recording stops)."""
    try:
        import sounddevice as sd
    except OSError as error:
        raise MicrophoneError(
            "Audio backend not available ({}). On Linux install PortAudio: sudo apt install libportaudio2".format(
                error
            )
        )
    last_error: Optional[Exception] = None
    for kwargs in ({"samplerate": SAMPLE_RATE, "channels": 1}, {}):
        try:
            stream = sd.InputStream(dtype="float32", callback=callback, **kwargs)
            stream.start()
            return stream
        except Exception as error:  # sd.PortAudioError, ValueError for odd devices
            last_error = error
    raise MicrophoneError("No microphone input available ({})".format(last_error))


class AudioRecorder:
    """Records the default input device. ``on_level`` is called from the audio thread with
    the current level (0…1)."""

    def __init__(self, open_stream: Optional[Callable[[Callable[..., None]], Any]] = None):
        self._open_stream = open_stream or _open_sounddevice_stream
        self._lock = threading.Lock()
        self._chunks: List[np.ndarray] = []
        self._count = 0
        self._first_time: Optional[float] = None
        self._stream: Any = None
        self._rate = SAMPLE_RATE
        self.on_level: Optional[Callable[[float], None]] = None

    def start(self) -> None:
        with self._lock:
            self._chunks, self._count, self._first_time = [], 0, None
        self._stream = self._open_stream(self._callback)
        self._rate = int(round(float(getattr(self._stream, "samplerate", SAMPLE_RATE))))
        log.info("Recording microphone at %d Hz", self._rate)

    def _callback(self, indata: np.ndarray, frames: int, time_info: Any, status: Any) -> None:
        now = paths.now()
        if status:
            log.debug("Audio status: %s", status)
        # When the first sample of this buffer was captured.
        latency = 0.0
        try:
            adc, current = float(time_info.inputBufferAdcTime), float(time_info.currentTime)
            if adc > 0 and current > 0 and 0 <= current - adc < 1:
                latency = current - adc
        except (AttributeError, TypeError):
            pass
        if latency == 0.0:
            latency = frames / float(self._rate)
        buffer_time = now - latency

        mono = (
            indata[:, 0]
            if indata.ndim > 1 and indata.shape[1] == 1
            else indata.reshape(frames, -1).mean(axis=1)
        )
        mono = np.array(mono, dtype=np.float32)
        with self._lock:
            if self._first_time is None:
                self._first_time = buffer_time
            else:
                # Audio missing since the last buffer (overflow, device hiccup) becomes
                # silence, so word times stay aligned with the session clock.
                gap = int((buffer_time - self._first_time) * self._rate) - self._count
                if gap > self._rate // 10:
                    self._chunks.append(np.zeros(gap, dtype=np.float32))
                    self._count += gap
            self._chunks.append(mono)
            self._count += len(mono)
        if self.on_level is not None and len(mono):
            self.on_level(level_from_rms(float(np.sqrt(np.mean(np.square(mono, dtype=np.float64))))))

    def stop(self) -> AudioResult:
        stream, self._stream = self._stream, None
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception as error:
                log.error("Stopping the microphone failed: %s", error)
        with self._lock:
            samples = np.concatenate(self._chunks) if self._chunks else np.zeros(0, dtype=np.float32)
            first = self._first_time
            self._chunks, self._count = [], 0
        if self._rate != SAMPLE_RATE:
            samples = video.resample(samples, self._rate, SAMPLE_RATE)
        return AudioResult(samples=samples, first_sample_time=first)

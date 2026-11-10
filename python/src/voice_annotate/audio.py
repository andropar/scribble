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
#: Longest stretch of missing audio that is filled with silence.
MAX_GAP_SECONDS = 5


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


def _input_candidates(sd: Any) -> List[Any]:
    """Devices to try: the default input, then the default input of each other host API
    (on Windows the same microphone is reachable through MME, DirectSound and WASAPI, and
    a device that fails with one often works with another)."""
    candidates: List[Any] = [None]
    try:
        default_api = sd.default.hostapi
        for index, api in enumerate(sd.query_hostapis()):
            device = api.get("default_input_device", -1)
            if index != default_api and isinstance(device, int) and device >= 0:
                candidates.append(device)
    except Exception as error:
        log.debug("Could not list audio host APIs: %s", error)
    return candidates


def _stream_variants(sd: Any, device: Any) -> List[dict]:
    """Stream settings to try for a device, best first.

    Blocks of a tenth of a second: PortAudio then drains the device on its own thread and
    calls into Python ten times a second, instead of once per hardware period (a few
    milliseconds with some drivers, which a Python callback cannot keep up with).
    """
    variants: List[dict] = [{"samplerate": SAMPLE_RATE, "channels": 1, "blocksize": SAMPLE_RATE // 10}]
    try:
        info = sd.query_devices(device, "input") if device is None else sd.query_devices(device)
        rate = int(info.get("default_samplerate") or 0)
        channels = max(1, min(2, int(info.get("max_input_channels") or 1)))
        if rate > 0:
            variants.append({"samplerate": rate, "channels": channels, "blocksize": rate // 10})
    except Exception as error:
        log.debug("Could not query audio device %s: %s", device, error)
    variants += [{"samplerate": SAMPLE_RATE, "channels": 1}, {}]
    return variants


def _open_sounddevice_stream(callback: Callable[..., None]) -> Any:
    """Opens an input stream, preferring 16 kHz mono and falling back to the device's own
    format (which is then converted when recording stops)."""
    try:
        import sounddevice as sd
    except OSError as error:
        raise MicrophoneError(
            "Audio backend not available ({}). On Linux install PortAudio: "
            "sudo apt install libportaudio2".format(error)
        )
    first_error: Optional[Exception] = None
    for device in _input_candidates(sd):
        for kwargs in _stream_variants(sd, device):
            try:
                stream = sd.InputStream(device=device, dtype="float32", callback=callback, **kwargs)
                stream.start()
                if first_error is not None:
                    log.info("Microphone opened with device %s and %s after: %s", device, kwargs, first_error)
                return stream
            except Exception as error:  # sd.PortAudioError, ValueError for odd devices
                first_error = first_error or error
    raise MicrophoneError("No microphone input available ({})".format(first_error))


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
        self._started_at = 0.0
        self._warned = False
        self.on_level: Optional[Callable[[float], None]] = None

    def start(self) -> None:
        with self._lock:
            self._chunks, self._count, self._first_time = [], 0, None
            self._started_at = paths.now()
            self._warned = False
        self._stream = self._open_stream(self._callback)
        reported = float(getattr(self._stream, "samplerate", SAMPLE_RATE) or SAMPLE_RATE)
        self._rate = int(round(reported)) if 8_000 <= reported <= 384_000 else SAMPLE_RATE
        log.info("Recording microphone at %d Hz", self._rate)

    def _callback(self, indata: np.ndarray, frames: int, time_info: Any, status: Any) -> None:
        now = paths.now()
        if status:
            log.debug("Audio status: %s", status)
        mono = (
            indata[:, 0]
            if indata.ndim > 1 and indata.shape[1] == 1
            else indata.reshape(len(indata), -1).mean(axis=1)
        )
        mono = np.array(mono, dtype=np.float32)
        # When the first sample of this buffer was captured: now minus the input latency.
        # Drivers report that with varying care, so anything implausible is replaced by the
        # length of the buffer, and the result is kept inside the recording's lifetime.
        latency = None
        try:
            adc, current = float(time_info.inputBufferAdcTime), float(time_info.currentTime)
            if adc > 0 and 0 <= current - adc < 1:
                latency = current - adc
        except (AttributeError, TypeError, ValueError):
            pass
        if latency is None:
            latency = min(len(mono) / float(self._rate), 1.0)
        buffer_time = min(max(now - latency, self._started_at - 1.0), now)

        with self._lock:
            if self._first_time is None:
                self._first_time = buffer_time
            else:
                # Audio missing since the last buffer (overflow, device hiccup) becomes
                # silence, so word times stay aligned with the session clock.
                gap = int((buffer_time - self._first_time) * self._rate) - self._count
                if self._rate // 10 < gap <= self._rate * MAX_GAP_SECONDS:
                    self._chunks.append(np.zeros(gap, dtype=np.float32))
                    self._count += gap
                elif gap > self._rate * MAX_GAP_SECONDS and not self._warned:
                    self._warned = True
                    log.warning(
                        "Ignoring an implausible gap of %.1f s in the microphone stream", gap / self._rate
                    )
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

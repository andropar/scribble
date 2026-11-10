"""Short synthesized cues for start, stop, done and error (no sound files to ship)."""

from __future__ import annotations

import logging
import threading
from typing import Dict, Sequence, Tuple

import numpy as np

log = logging.getLogger(__name__)

_RATE = 44_100
_VOLUME = 0.16
#: Whether cues are played at all (follows the "Play sounds" setting).
enabled = True

# (frequency in Hz, duration in seconds) per note.
_CUES: Dict[str, Sequence[Tuple[float, float]]] = {
    "start": ((660, 0.05), (990, 0.07)),
    "stop": ((990, 0.05), (660, 0.07)),
    "done": ((880, 0.07), (1320, 0.12)),
    "error": ((220, 0.09), (180, 0.14)),
}
_cache: Dict[str, np.ndarray] = {}


def waveform(name: str) -> np.ndarray:
    if name not in _cache:
        parts = []
        for freq, seconds in _CUES[name]:
            t = np.arange(int(seconds * _RATE)) / _RATE
            envelope = np.minimum(1.0, np.minimum(t / 0.006, (seconds - t) / 0.03))
            parts.append(np.sin(2 * np.pi * freq * t) * np.clip(envelope, 0, 1))
        _cache[name] = (np.concatenate(parts) * _VOLUME).astype(np.float32)
    return _cache[name]


def play(name: str) -> None:
    """Plays a cue without blocking. Silently does nothing without an output device."""
    if not enabled:
        return

    def run() -> None:
        try:
            import sounddevice as sd

            sd.play(waveform(name), _RATE, blocking=True)
        except Exception as error:
            log.debug("Could not play %s: %s", name, error)

    threading.Thread(target=run, name="sound", daemon=True).start()


def start() -> None:
    play("start")


def stop() -> None:
    play("stop")


def done() -> None:
    play("done")


def error() -> None:
    play("error")

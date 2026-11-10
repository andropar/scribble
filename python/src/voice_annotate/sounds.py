"""Short synthesized cues for start, stop, done and error.

The cues are written once as small WAV files and played by the system: ``winsound`` on
Windows, the desktop's own player on Linux. That keeps playback out of this process (and
away from PortAudio, which the microphone uses and which does not tolerate streams being
opened from several threads at once).
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import sys
import tempfile
import wave
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

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
_waveforms: Dict[str, np.ndarray] = {}
_files: Dict[str, Optional[str]] = {}
_player: Optional[List[str]] = None
_player_searched = False


def waveform(name: str) -> np.ndarray:
    if name not in _waveforms:
        parts = []
        for freq, seconds in _CUES[name]:
            t = np.arange(int(seconds * _RATE)) / _RATE
            envelope = np.minimum(1.0, np.minimum(t / 0.006, (seconds - t) / 0.03))
            parts.append(np.sin(2 * np.pi * freq * t) * np.clip(envelope, 0, 1))
        _waveforms[name] = (np.concatenate(parts) * _VOLUME).astype(np.float32)
    return _waveforms[name]


def cue_file(name: str) -> Optional[str]:
    """Path of the cue as a WAV file (written on first use), or None if that failed."""
    if name not in _files:
        try:
            folder = Path(tempfile.gettempdir()) / "voice-annotate-sounds"
            folder.mkdir(exist_ok=True)
            path = folder / "{}.wav".format(name)
            samples = (np.clip(waveform(name), -1, 1) * 32767).astype("<i2")
            with wave.open(str(path), "wb") as f:
                f.setnchannels(1)
                f.setsampwidth(2)
                f.setframerate(_RATE)
                f.writeframes(samples.tobytes())
            _files[name] = str(path)
        except OSError as error:
            log.debug("Could not write the %s cue: %s", name, error)
            _files[name] = None
    return _files[name]


def _find_player() -> Optional[List[str]]:
    """A command line player on Linux (PulseAudio, PipeWire or ALSA) or macOS."""
    global _player, _player_searched
    if not _player_searched:
        _player_searched = True
        for command in (["paplay"], ["pw-play"], ["aplay", "-q"], ["afplay"]):
            if shutil.which(command[0]):
                _player = command
                break
    return _player


def play(name: str) -> None:
    """Plays a cue without blocking. Silently does nothing if the system cannot play it."""
    if not enabled:
        return
    path = cue_file(name)
    if path is None:
        return
    try:
        if sys.platform == "win32":
            import winsound

            winsound.PlaySound(path, winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
        else:
            player = _find_player()
            if player is not None:
                subprocess.Popen(player + [path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as error:  # no sound device, player vanished, …
        log.debug("Could not play %s: %s", name, error)


def start() -> None:
    play("start")


def stop() -> None:
    play("stop")


def done() -> None:
    play("done")


def error() -> None:
    play("error")

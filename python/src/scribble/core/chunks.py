"""Cutting a recording into pieces Whisper decodes one at a time.

Whisper reads at most 30 s at once. The transcriber gives it a recording as pieces of at
most that length, each decoded on its own, and a piece ends in a pause, so no word is split.
Where a piece ends depends only on the 30 s after its start. That is what lets
``live.LiveTranscriber`` make the same cuts while the recording is still running and decode
every piece as soon as it is complete: the transcript is the same as when the finished
recording is cut and decoded in one go.

(The macOS app does the same with WhisperKit's chunker, which picks the longest stretch
under a fixed level. Here the quietest stretch is taken, which also works with a quiet
microphone.)
"""

from __future__ import annotations

from typing import List, Optional, Tuple

import numpy as np

#: Seconds Whisper reads at once.
WINDOW_SECONDS = 30.0
#: A pause is looked for in a stretch of this length.
PAUSE_SECONDS = 0.3
_HOP_SECONDS = 0.05
#: Audio shorter than this after the last cut is ignored: too short for a word.
MIN_TAIL_SECONDS = 0.5


def first_cut(audio: np.ndarray, rate: int) -> Optional[int]:
    """Where the piece that starts at the beginning of ``audio`` ends: the middle of the
    quietest stretch in the second half of the first 30 s. None while ``audio`` is not
    longer than 30 s, because the piece may still be the last one, which is not cut."""
    window = int(WINDOW_SECONDS * rate)
    if len(audio) <= window:
        return None
    hop = max(1, int(_HOP_SECONDS * rate))
    span = max(1, int(round(PAUSE_SECONDS / _HOP_SECONDS)))
    first = (window // 2) // hop
    count = window // hop - first
    region = np.asarray(audio[first * hop : (first + count) * hop], dtype=np.float64)
    energy = np.square(region).reshape(count, hop).sum(axis=1)
    # Energy of every stretch of `span` hops, by a running sum.
    total = np.concatenate(([0.0], np.cumsum(energy)))
    stretches = total[span:] - total[:-span]
    # The last of equally quiet stretches: longer pieces mean fewer of them.
    quietest = len(stretches) - 1 - int(np.argmin(stretches[::-1]))
    return (first + quietest) * hop + (span * hop) // 2


def pieces(audio: np.ndarray, rate: int, start: int = 0) -> List[Tuple[int, int]]:
    """``(start, end)`` of every piece of ``audio`` from ``start`` on."""
    n = len(audio)
    window = int(WINDOW_SECONDS * rate)
    if start == 0 and n <= window:
        return [(0, n)] if n else []
    out: List[Tuple[int, int]] = []
    position = start
    while n - position > window:
        cut = first_cut(audio[position : position + window + 1], rate)
        assert cut is not None and cut > 0
        out.append((position, position + cut))
        position += cut
    if n - position > int(MIN_TAIL_SECONDS * rate):
        out.append((position, n))
    return out

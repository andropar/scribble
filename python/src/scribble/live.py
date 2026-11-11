"""Transcribing a recording while it is still running.

Finishing then only has to transcribe what came after the last cut: at most half a minute,
however long the recording was.

The transcriber decodes a recording as pieces of at most 30 s (``core.chunks``), each on its
own, and where a piece ends depends only on the 30 s after its start. So the cuts can be
made while recording and every piece decoded as soon as it is complete. Pieces, decoding
options and the way results are joined are those of ``Transcriber.transcribe``, so the
transcript is the same (``--eval-live`` checks this on recordings). If anything fails,
``finish`` returns None and the caller transcribes the whole recording in one go.

A port of ``Sources/Scribble/LiveTranscriber.swift``.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, List, Optional, Tuple

import numpy as np

from .core import chunks
from .settings import WhisperCompute, WhisperModel
from .transcriber import SAMPLE_RATE, Transcriber, TranscriptionOutput, has_speech, merged

log = logging.getLogger(__name__)

POLL_SECONDS = 0.5
_WINDOW = int(chunks.WINDOW_SECONDS * SAMPLE_RATE)


class LiveTranscriber:
    """Decodes the pieces of a running recording on a worker thread. ``recorder`` needs
    ``samples(start, count)``: the 16 kHz audio recorded so far (``AudioRecorder``)."""

    def __init__(
        self,
        recorder: Any,
        transcriber: Transcriber,
        model: WhisperModel,
        compute: WhisperCompute,
        vocabulary: str,
        language: str,
    ):
        self._recorder = recorder
        self._transcriber = transcriber
        self._options = (model, compute, vocabulary, language)
        #: Samples already cut into pieces.
        self._committed = 0
        self._outputs: List[TranscriptionOutput] = []
        self._failed = False
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="live-transcription", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        # One piece at a time. If decoding falls behind, the pieces wait; whatever is still
        # undone when the recording ends is decoded by `finish`.
        while not self._stop.wait(POLL_SECONDS):
            if not self._transcriber.status.is_ready:
                continue
            try:
                # A cut needs audio beyond the 30 s it is chosen from.
                audio = self._recorder.samples(self._committed, _WINDOW + 1)
                cut = chunks.first_cut(audio, SAMPLE_RATE)
                if cut is None:
                    continue
                offset = self._committed
                self._committed += cut
                self._decode(np.array(audio[:cut]), offset)
            except Exception as error:
                log.error("Live transcription failed: %s", error)
                self._failed = True
                return

    def _decode(self, piece: np.ndarray, offset: int) -> None:
        model, compute, vocabulary, language = self._options
        self._outputs.append(self._transcriber.decode(piece, offset, model, compute, vocabulary, language))

    def finish(self, samples: np.ndarray) -> Optional[TranscriptionOutput]:
        """The transcript of ``samples`` (the whole recording), or None if nothing was
        transcribed while recording or a piece failed: the caller then transcribes it in one
        go. Blocking."""
        self._stop.set()
        self._thread.join()
        if self._failed or self._committed == 0 or self._committed > len(samples):
            return None
        if not has_speech(samples):
            return TranscriptionOutput()
        started = time.time()
        rest = chunks.pieces(samples, SAMPLE_RATE, start=self._committed)
        try:
            for start, end in rest:
                self._decode(samples[start:end], start)
        except Exception as error:
            log.error("Live transcription failed at the end: %s", error)
            return None
        output = merged(self._outputs)
        log.info(
            "Live transcription: %.1fs audio, %.1fs left at the end took %.2fs, %d words",
            len(samples) / SAMPLE_RATE,
            (len(samples) - self._committed) / SAMPLE_RATE,
            time.time() - started,
            len(output.words),
        )
        return output

    def cancel(self) -> None:
        """Stops without a result. A piece that is being decoded is finished and dropped."""
        self._stop.set()


def replay(
    samples: np.ndarray,
    transcriber: Transcriber,
    model: WhisperModel,
    compute: WhisperCompute,
    vocabulary: str,
    language: str,
) -> Tuple[TranscriptionOutput, List[int], float]:
    """The transcript ``LiveTranscriber`` produces for a finished recording: cut as it would
    have been cut while recording, every piece decoded on its own. Also returns the cuts and
    how long decoding the audio after the last cut took (the wait after finishing).
    Developer commands and tests."""
    outputs: List[TranscriptionOutput] = []
    cuts: List[int] = []
    committed = 0
    while True:
        cut = chunks.first_cut(samples[committed : committed + _WINDOW + 1], SAMPLE_RATE)
        if cut is None:
            break
        outputs.append(
            transcriber.decode(
                samples[committed : committed + cut], committed, model, compute, vocabulary, language
            )
        )
        committed += cut
        cuts.append(committed)
    started = time.time()
    if not cuts:
        return (
            transcriber.transcribe(samples, model, compute, vocabulary, language),
            [],
            time.time() - started,
        )
    if not has_speech(samples):
        return TranscriptionOutput(), cuts, 0.0
    for start, end in chunks.pieces(samples, SAMPLE_RATE, start=committed):
        outputs.append(transcriber.decode(samples[start:end], start, model, compute, vocabulary, language))
    return merged(outputs), cuts, time.time() - started

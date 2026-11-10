"""Video and audio files via PyAV (FFmpeg): the walkthrough recording, frame extraction,
the audio track and the final mux.

Frames are BGRA numpy arrays ``(height, width, 4)``, the layout screen grabbers deliver.
"""

from __future__ import annotations

import logging
from fractions import Fraction
from pathlib import Path
from typing import Callable, Iterator, List, Optional, Sequence, Tuple, Union

import av
import numpy as np

log = logging.getLogger(__name__)

SAMPLE_RATE = 16_000
THUMB_COLS = 64
THUMB_ROWS = 40
_THUMB_SUB = 3
_TIME_BASE = Fraction(1, 1000)

PathLike = Union[str, Path]


def thumbnail(frame: np.ndarray) -> np.ndarray:
    """64×40 luminance thumbnail of a BGRA frame (3×3 samples averaged per cell), flattened.

    Same sampling grid and integer luma weights as the macOS app's ``Thumbnailer``, so the
    keyframe thresholds mean the same thing on every platform.
    """
    h, w = frame.shape[0], frame.shape[1]
    n_rows, n_cols = THUMB_ROWS * _THUMB_SUB, THUMB_COLS * _THUMB_SUB
    ys = np.minimum(h - 1, (np.arange(n_rows) * h + h // (2 * n_rows)) // n_rows)
    xs = np.minimum(w - 1, (np.arange(n_cols) * w + w // (2 * n_cols)) // n_cols)
    px = frame[ys[:, None], xs[None, :]].astype(np.uint32)
    luma = (px[..., 2] * 77 + px[..., 1] * 150 + px[..., 0] * 29) >> 8
    cells = luma.reshape(THUMB_ROWS, _THUMB_SUB, THUMB_COLS, _THUMB_SUB).sum(axis=(1, 3))
    return (cells // (_THUMB_SUB * _THUMB_SUB)).astype(np.uint8).reshape(-1)


def _video_encoder() -> str:
    for name in ("libx264", "h264", "mpeg4"):
        if name in av.codecs_available:
            return name
    raise RuntimeError("No H.264 or MPEG-4 encoder in this FFmpeg build")


class VideoWriter:
    """Writes BGRA frames with their real timestamps (variable frame rate) to an .mp4."""

    def __init__(self, path: PathLike, width: int, height: int, fps: int = 10, max_width: int = 3840):
        # H.264 4:2:0 needs even dimensions.
        self.source_size = (width, height)
        out_w, out_h = width, height
        if out_w > max_width:
            out_h = int(out_h * max_width / out_w)
            out_w = max_width
        self.width, self.height = max(2, out_w & ~1), max(2, out_h & ~1)
        self.path = str(path)
        self._container = av.open(self.path, mode="w")
        codec = _video_encoder()
        self._stream = self._container.add_stream(codec, rate=fps)
        self._stream.width = self.width
        self._stream.height = self.height
        self._stream.pix_fmt = "yuv420p"
        ctx = self._stream.codec_context
        ctx.time_base = _TIME_BASE
        # Short GOPs: extracting a keyframe then never decodes more than a second of video.
        ctx.gop_size = fps
        if codec in ("libx264", "h264"):
            ctx.options = {"preset": "veryfast", "crf": "20", "tune": "zerolatency"}
        else:
            ctx.bit_rate = max(4_000_000, self.width * self.height * 3 // 2)
        self._last_pts = -1
        self.frames_written = 0

    def write(self, frame: np.ndarray, t: float) -> None:
        """Appends ``frame``, shown from ``t`` seconds after the start of the video."""
        pts = max(int(round(t * 1000)), self._last_pts + 1)
        if frame.shape[1] == self.width and frame.shape[0] == self.height:
            data = frame
        elif frame.shape[1] - self.width in (0, 1) and frame.shape[0] - self.height in (0, 1):
            data = frame[: self.height, : self.width]
        else:
            data = frame
        vf = av.VideoFrame.from_ndarray(np.ascontiguousarray(data), format="bgra")
        vf.pts = pts
        vf.time_base = _TIME_BASE
        for packet in self._stream.encode(vf):
            self._container.mux(packet)
        self._last_pts = pts
        self.frames_written += 1

    def close(self) -> None:
        if self._container is None:
            return
        try:
            for packet in self._stream.encode(None):
                self._container.mux(packet)
        finally:
            self._container.close()
            self._container = None


def video_duration(path: PathLike) -> float:
    with av.open(str(path)) as container:
        stream = container.streams.video[0]
        if stream.duration is not None and stream.time_base is not None:
            return float(stream.duration * stream.time_base)
        return float(container.duration or 0) / av.time_base


def for_each_frame(path: PathLike, times: Sequence[float], body: Callable[[int, np.ndarray], None]) -> None:
    """Calls ``body(index, bgra_frame)`` with the frame on screen at each of ``times``
    (seconds from the start of the video), i.e. the last frame at or before that time.
    Frames are decoded one at a time, so only a single one is in memory."""
    if not times:
        return
    with av.open(str(path)) as container:
        stream = container.streams.video[0]
        stream.thread_type = "AUTO"
        time_base = stream.time_base
        start = stream.start_time or 0
        order = sorted(range(len(times)), key=lambda i: times[i])

        decoder: Optional[Iterator] = None
        current = None  # last decoded frame with pts <= target
        pending = None  # first decoded frame beyond the previous target

        for index in order:
            target = start + int(max(times[index], 0) / time_base)
            # Continue decoding forward when the target is close ahead; seek otherwise.
            reuse = (
                decoder is not None
                and current is not None
                and current.pts <= target
                and (target - current.pts) * time_base <= 2.0
            )
            if not reuse:
                container.seek(target, stream=stream, backward=True, any_frame=False)
                decoder = container.decode(stream)
                current, pending = None, None
            if pending is not None and pending.pts <= target:
                current, pending = pending, None
            if pending is None:
                for frame in decoder:
                    if frame.pts is None:
                        continue
                    if frame.pts > target and current is not None:
                        pending = frame
                        break
                    current = frame
                    if frame.pts > target:
                        break
                else:
                    decoder = None
            if current is None:
                raise RuntimeError("No video frame at {:.2f}s in {}".format(times[index], path))
            body(index, current.to_ndarray(format="bgra"))


# MARK: audio


def write_m4a(samples: np.ndarray, path: PathLike, sample_rate: int = SAMPLE_RATE) -> None:
    """Writes mono float samples as AAC in an .m4a container."""
    if len(samples) == 0:
        return
    with av.open(str(path), mode="w") as container:
        stream = container.add_stream("aac", rate=sample_rate)
        stream.codec_context.bit_rate = 48_000
        stream.codec_context.layout = "mono"
        chunk = sample_rate * 10
        data = np.ascontiguousarray(samples, dtype=np.float32)
        for offset in range(0, len(data), chunk):
            part = data[offset : offset + chunk].reshape(1, -1)
            frame = av.AudioFrame.from_ndarray(part, format="fltp", layout="mono")
            frame.sample_rate = sample_rate
            frame.pts = offset
            frame.time_base = Fraction(1, sample_rate)
            for packet in stream.encode(frame):
                container.mux(packet)
        for packet in stream.encode(None):
            container.mux(packet)


def load_samples(path: PathLike, sample_rate: int = SAMPLE_RATE) -> np.ndarray:
    """Loads any audio file as mono float32 samples at ``sample_rate``."""
    chunks: List[np.ndarray] = []
    with av.open(str(path)) as container:
        if not container.streams.audio:
            raise ValueError("No audio stream in {}".format(path))
        stream = container.streams.audio[0]
        resampler = av.AudioResampler(format="fltp", layout="mono", rate=sample_rate)
        for frame in container.decode(stream):
            for out in resampler.resample(frame):
                chunks.append(out.to_ndarray().reshape(-1))
        for out in resampler.resample(None):
            chunks.append(out.to_ndarray().reshape(-1))
    if not chunks:
        return np.zeros(0, dtype=np.float32)
    return np.concatenate(chunks).astype(np.float32, copy=False)


def resample(samples: np.ndarray, source_rate: int, target_rate: int = SAMPLE_RATE) -> np.ndarray:
    """Resamples mono float samples (band-limited, via libswresample)."""
    if source_rate == target_rate or len(samples) == 0:
        return np.ascontiguousarray(samples, dtype=np.float32)
    resampler = av.AudioResampler(format="fltp", layout="mono", rate=target_rate)
    data = np.ascontiguousarray(samples, dtype=np.float32)
    chunks: List[np.ndarray] = []
    step = source_rate * 30
    for offset in range(0, len(data), step):
        frame = av.AudioFrame.from_ndarray(
            data[offset : offset + step].reshape(1, -1), format="fltp", layout="mono"
        )
        frame.sample_rate = source_rate
        frame.pts = offset
        frame.time_base = Fraction(1, source_rate)
        for out in resampler.resample(frame):
            chunks.append(out.to_ndarray().reshape(-1))
    for out in resampler.resample(None):
        chunks.append(out.to_ndarray().reshape(-1))
    return np.concatenate(chunks).astype(np.float32, copy=False) if chunks else np.zeros(0, dtype=np.float32)


def rms(samples: np.ndarray) -> float:
    if len(samples) == 0:
        return 0.0
    return float(np.sqrt(np.mean(np.square(samples, dtype=np.float64))))


# MARK: mux


def _packets(container, stream, shift: int) -> Iterator[Tuple[float, object]]:
    """Packets of ``stream`` with timestamps shifted by ``shift`` (stream time base units);
    packets that would start before zero are dropped."""
    for packet in container.demux(stream):
        if packet.dts is None or packet.pts is None:
            continue
        packet.pts += shift
        packet.dts += shift
        if packet.pts < 0:
            continue
        yield float(packet.dts * packet.time_base), packet


def _copy_stream(container, template):
    """An output stream that takes packets of ``template`` as they are."""
    if hasattr(container, "add_stream_from_template"):
        return container.add_stream_from_template(template)
    return container.add_stream(template=template)  # PyAV < 14


def mux(video: PathLike, audio: PathLike, audio_offset: float, output: PathLike) -> None:
    """Muxes the screen video and the mic audio into one .mp4 without re-encoding.
    ``audio_offset`` = audio start − video start, in seconds."""
    with av.open(str(video)) as vin, av.open(str(audio)) as ain, av.open(str(output), mode="w") as out:
        v_in, a_in = vin.streams.video[0], ain.streams.audio[0]
        v_out = _copy_stream(out, v_in)
        a_out = _copy_stream(out, a_in)
        shift = int(round(audio_offset / a_in.time_base))
        v_iter = _packets(vin, v_in, 0)
        a_iter = _packets(ain, a_in, shift)
        v_next, a_next = next(v_iter, None), next(a_iter, None)
        # Interleave by decode time so players can stream the file.
        while v_next is not None or a_next is not None:
            take_video = a_next is None or (v_next is not None and v_next[0] <= a_next[0])
            if take_video:
                packet = v_next[1]
                packet.stream = v_out
                v_next = next(v_iter, None)
            else:
                packet = a_next[1]
                packet.stream = a_out
                a_next = next(a_iter, None)
            out.mux(packet)

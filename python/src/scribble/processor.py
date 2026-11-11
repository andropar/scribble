"""Turns a finished capture into a bundle: transcript, frames, capture.md, events.json.

A port of ``Sources/Scribble/CaptureProcessor.swift``. ``process`` blocks; call it
from a worker thread.
"""

from __future__ import annotations

import json
import logging
import shutil
import threading
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from PySide6.QtGui import QImage

from . import render, video
from .audio import AudioResult
from .core import describer, keyframes, language as languages, markdown
from .core.geometry import CoordinateMapper, Rect, Size, detail_rect, fit_size
from .core.keyframes import KeyframeConfig, KeyframeSelection
from .core.markdown import CaptureDocument, DetailRef, EventsDisplay, EventsFrame, EventsLog, FrameRef, Marker
from .core.models import Annotation, CaptureMode, ClickEvent, FrameSample, SpeechSegment, Word
from .settings import WhisperCompute, WhisperModel
from .transcriber import Transcriber

log = logging.getLogger(__name__)


@dataclass
class DisplayInfo:
    """A display taking part in a capture (index = position in the capture's display list)."""

    index: int
    name: str
    canvas_size: Size


@dataclass
class ShotData:
    image: QImage
    #: Session time of the screenshot.
    time: float
    canvas_size: Size
    display: int
    #: Snapshot round (0 = first shot, 1 = after the first "next shot", …).
    round: int
    #: The display the cursor was on when this round was captured.
    primary: bool


@dataclass
class RecordingResult:
    video_path: Optional[Path]
    #: Clock time of the first video frame (= video time 0).
    first_frame_time: Optional[float]
    #: Thumbnails with clock timestamps.
    samples: List[FrameSample]
    pixel_size: Size


@dataclass
class CaptureInput:
    """Everything needed to turn a finished capture into a bundle."""

    mode: CaptureMode
    folder: Path
    start_date: datetime
    t0: float
    duration: float
    displays: List[DisplayInfo]
    annotations: List[Annotation] = field(default_factory=list)
    clicks: List[ClickEvent] = field(default_factory=list)
    shots: List[ShotData] = field(default_factory=list)
    audio: Optional[AudioResult] = None
    #: Walkthrough recordings by display index.
    recordings: Dict[int, RecordingResult] = field(default_factory=dict)
    #: (session time, display index) whenever the cursor moved to another display.
    cursor_timeline: List[Tuple[float, int]] = field(default_factory=list)

    model: WhisperModel = WhisperModel.AUTO
    compute: WhisperCompute = WhisperCompute.AUTO
    vocabulary: str = ""
    language: str = languages.ENGLISH
    #: Transcribed while recording (``live.LiveTranscriber``); None = transcribe the audio here.
    live: Optional[Any] = None
    max_frames: int = 30
    long_edge: int = 1568
    detail_crops: bool = True
    keep_originals: bool = False
    fade_seconds: float = 3.0
    #: Extra legend sentences for capture.md (platform caveats).
    legend_notes: List[str] = field(default_factory=list)


@dataclass
class RecentCapture:
    folder: str
    date: datetime
    mode: CaptureMode
    duration: float
    markdown_path: str
    #: All images in paste order (frames with their detail crops).
    image_paths: List[str]
    #: Main frames only (no detail crops).
    frame_paths: List[str]
    word_count: int
    annotation_count: int
    note: Optional[str] = None

    @property
    def summary(self) -> str:
        n = len(self.frame_paths)
        parts = ["{} {}".format(n, "frame" if n == 1 else "frames")]
        if self.annotation_count > 0:
            parts.append(
                "{} {}".format(
                    self.annotation_count, "annotation" if self.annotation_count == 1 else "annotations"
                )
            )
        parts.append("{} words".format(self.word_count))
        return " · ".join(parts)

    def to_json(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "folder": self.folder,
            "date": markdown.iso8601(self.date),
            "mode": self.mode.value,
            "duration": self.duration,
            "markdownPath": self.markdown_path,
            "imagePaths": list(self.image_paths),
            "framePaths": list(self.frame_paths),
            "wordCount": self.word_count,
            "annotationCount": self.annotation_count,
        }
        if self.note is not None:
            d["note"] = self.note
        return d

    @staticmethod
    def from_json(d: Dict[str, Any]) -> "RecentCapture":
        return RecentCapture(
            folder=d["folder"],
            date=markdown.parse_iso8601(d["date"]),
            mode=CaptureMode(d["mode"]),
            duration=float(d["duration"]),
            markdown_path=d["markdownPath"],
            image_paths=list(d.get("imagePaths", [])),
            frame_paths=list(d.get("framePaths", [])),
            word_count=int(d.get("wordCount", 0)),
            annotation_count=int(d.get("annotationCount", 0)),
            note=d.get("note"),
        )


@dataclass
class _BuiltFrame:
    ref: FrameRef
    mapper: CoordinateMapper
    reasons: List[str]
    annotation_ids: List[int]
    image_paths: List[str]
    display: int
    #: Snapshot: index into ``CaptureInput.shots``.
    shot_index: Optional[int] = None


_background: List[threading.Thread] = []


def wait_for_background(timeout: float = 60) -> None:
    """Waits for background work started by ``process`` (muxing the recording)."""
    for thread in list(_background):
        thread.join(timeout)
        if not thread.is_alive():
            _background.remove(thread)


def _write_text(path: Path, text: str) -> None:
    # Unix newlines on every platform: agents and diffs see the same file everywhere.
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def _frame_name(n: int, suffix: str = "") -> str:
    return "frame_{:02d}{}.png".format(n, suffix)


def snapshot_frame_order(shots: Sequence[ShotData], annotations: Sequence[Annotation]) -> List[int]:
    """Shots that become frames: the cursor's display of every round, plus any other display
    that was drawn on. Ordered by round, cursor display first."""
    annotated = {a.shot for a in annotations}
    indices = [i for i in range(len(shots)) if shots[i].primary or i in annotated]
    return sorted(indices, key=lambda i: (shots[i].round, not shots[i].primary, shots[i].display))


def _frame_index(a: Annotation, mode: CaptureMode, frames: Sequence[_BuiltFrame]) -> Optional[int]:
    if mode == CaptureMode.SNAPSHOT:
        for i, f in enumerate(frames):
            if f.shot_index == a.shot:
                return i
        return None
    for i, f in enumerate(frames):
        if a.id in f.annotation_ids:
            return i
    on_display = [i for i, f in enumerate(frames) if f.display == a.screen]
    for i in on_display:
        if frames[i].ref.time >= a.end_time:
            return i
    return on_display[-1] if on_display else None


def _build_snapshot_frames(inp: CaptureInput) -> List[_BuiltFrame]:
    frames: List[_BuiltFrame] = []
    for n, shot_index in enumerate(snapshot_frame_order(inp.shots, inp.annotations)):
        shot = inp.shots[shot_index]
        number = n + 1
        anns = [a for a in inp.annotations if a.shot == shot_index]
        native = Size(shot.image.width(), shot.image.height())
        out = fit_size(native, inp.long_edge)
        image = render.render_annotated(shot.image, shot.canvas_size, anns, out)
        if image is None:
            raise RuntimeError("Rendering screenshot {} failed".format(number))
        path = inp.folder / _frame_name(number)
        render.write_png(image, path)
        image_paths = [str(path)]

        if inp.keep_originals:
            clean = render.resize(shot.image, out)
            if clean is not None:
                render.write_png(clean, inp.folder / _frame_name(number, "_clean"))

        mapper = CoordinateMapper(shot.canvas_size, out)
        detail: Optional[DetailRef] = None
        if inp.detail_crops:
            crop = detail_rect([render.annotation_extent(a) for a in anns], shot.canvas_size, out.width)
            if crop is not None:
                native_scale = native.width / shot.canvas_size.width
                crop_out = fit_size(
                    Size(crop.width * native_scale, crop.height * native_scale), inp.long_edge
                )
                detail_image = render.render_annotated(
                    shot.image, shot.canvas_size, anns, crop_out, crop=crop
                )
                if detail_image is not None:
                    detail_path = inp.folder / _frame_name(number, "_detail")
                    render.write_png(detail_image, detail_path)
                    detail = DetailRef(str(detail_path), crop_out, mapper.describe(crop))
                    image_paths.append(str(detail_path))
        frames.append(
            _BuiltFrame(
                ref=FrameRef(number, shot.time, str(path), out, detail=detail),
                mapper=mapper,
                reasons=["screenshot"],
                annotation_ids=[a.id for a in anns],
                image_paths=image_paths,
                display=shot.display,
                shot_index=shot_index,
            )
        )
    return frames


def _usable(rec: RecordingResult) -> bool:
    return rec.video_path is not None and rec.first_frame_time is not None


def _build_walkthrough_frames(inp: CaptureInput, speech_starts: Sequence[float]) -> List[_BuiltFrame]:
    recorded = sorted((d, r) for d, r in inp.recordings.items() if _usable(r))
    if not recorded:
        return []
    multi = len(recorded) > 1

    config = KeyframeConfig(max_frames=inp.max_frames, fade_duration=inp.fade_seconds)
    intervals = keyframes.active_intervals(inp.cursor_timeline, len(inp.displays), inp.duration)
    start_display = inp.cursor_timeline[0][1] if inp.cursor_timeline else recorded[0][0]
    end_display = inp.cursor_timeline[-1][1] if inp.cursor_timeline else start_display

    lists: List[List[KeyframeSelection]] = []
    for display, rec in recorded:
        samples = [FrameSample(s.time - inp.t0, s.thumb) for s in rec.samples]
        lists.append(
            keyframes.select(
                samples,
                [a for a in inp.annotations if a.screen == display],
                [c.time for c in inp.clicks if c.screen == display],
                speech_starts,
                inp.duration,
                active_intervals=intervals[display] if multi and display < len(intervals) else None,
                include_start=not multi or display == start_display,
                include_end=not multi or display == end_display,
                config=config,
            )
        )
    merged = keyframes.merge(lists, keyframes.frame_budget(inp.duration, config))
    log.info("Selected %d keyframes across %d display(s)", len(merged), len(recorded))

    by_id = {a.id: a for a in reversed(inp.annotations)}
    frames: List[_BuiltFrame] = []
    for list_index, (display, rec) in enumerate(recorded):
        info = next((d for d in inp.displays if d.index == display), None)
        if info is None:
            continue
        video_start = rec.first_frame_time - inp.t0
        # Frame numbers are chronological across displays.
        items = [(n, sel) for n, (li, sel) in enumerate(merged) if li == list_index]

        def write(i: int, bgra: Any, items=items, info=info, display=display) -> None:
            number = items[i][0] + 1
            sel = items[i][1]
            image = render.image_from_bgra(bgra)
            native = Size(image.width(), image.height())
            out = fit_size(native, inp.long_edge)
            full = render.resize(image, out)
            if full is None:
                raise RuntimeError("Resizing frame {} failed".format(number))
            path = inp.folder / _frame_name(number)
            render.write_png(full, path)
            image_paths = [str(path)]

            mapper = CoordinateMapper(info.canvas_size, out)
            detail: Optional[DetailRef] = None
            anns = [by_id[i] for i in sel.annotation_ids if i in by_id]
            if inp.detail_crops:
                crop = detail_rect([render.annotation_extent(a) for a in anns], info.canvas_size, out.width)
                if crop is not None:
                    s = native.width / info.canvas_size.width
                    pixel_rect = Rect(crop.min_x * s, crop.min_y * s, crop.width * s, crop.height * s)
                    crop_out = fit_size(Size(pixel_rect.width, pixel_rect.height), inp.long_edge)
                    detail_image = render.resize(image, crop_out, source_rect=pixel_rect)
                    if detail_image is not None:
                        detail_path = inp.folder / _frame_name(number, "_detail")
                        render.write_png(detail_image, detail_path)
                        detail = DetailRef(str(detail_path), crop_out, mapper.describe(crop))
                        image_paths.append(str(detail_path))
            frames.append(
                _BuiltFrame(
                    ref=FrameRef(number, sel.time, str(path), out, detail=detail),
                    mapper=mapper,
                    reasons=list(sel.reasons),
                    annotation_ids=list(sel.annotation_ids),
                    image_paths=image_paths,
                    display=display,
                )
            )

        video.for_each_frame(rec.video_path, [sel.time - video_start for _, sel in items], write)
    return sorted(frames, key=lambda f: f.ref.number)


def _mux_in_background(inp: CaptureInput, audio_path: Optional[Path], audio_start: float) -> None:
    recorded = {d: r for d, r in inp.recordings.items() if _usable(r)}

    def work() -> None:
        for display, rec in recorded.items():
            video_start = rec.first_frame_time - inp.t0
            name = "recording.mp4" if len(recorded) == 1 else "recording_display{}.mp4".format(display + 1)
            output = inp.folder / name
            try:
                if audio_path is not None:
                    video.mux(rec.video_path, audio_path, audio_start - video_start, output)
                    rec.video_path.unlink()
                else:
                    shutil.move(str(rec.video_path), str(output))
            except Exception as error:
                log.error("Muxing failed: %s", error)

    thread = threading.Thread(target=work, name="mux", daemon=True)
    _background.append(thread)
    thread.start()


def process(inp: CaptureInput, transcriber: Transcriber) -> RecentCapture:
    folder = inp.folder
    note: Optional[str] = None

    # 1. Audio → transcript (session times).
    words: List[Word] = []
    segments: List[SpeechSegment] = []
    audio_start = 0.0
    audio_path: Optional[Path] = None
    if inp.audio is not None and len(inp.audio.samples) > 0:
        first = inp.audio.first_sample_time
        audio_start = (first if first is not None else inp.t0) - inp.t0
        samples = inp.audio.samples
        target = folder / "audio.m4a"
        written: List[bool] = []

        def write_audio() -> None:
            try:
                video.write_m4a(samples, target)
                written.append(True)
            except Exception as error:
                log.error("Writing audio failed: %s", error)

        writer = threading.Thread(target=write_audio, name="audio-write", daemon=True)
        writer.start()
        try:
            out = inp.live.finish(samples) if inp.live is not None else None
            if out is None:
                out = transcriber.transcribe(samples, inp.model, inp.compute, inp.vocabulary, inp.language)
            note = out.warning
            words = [Word(w.text, w.start + audio_start, w.end + audio_start) for w in out.words]
            segments = [
                SpeechSegment(s.text, s.start + audio_start, s.end + audio_start) for s in out.segments
            ]
        except Exception as error:
            log.error("Transcription failed: %s", error)
            note = "Transcription failed: {}".format(error)
        writer.join()
        if written:
            audio_path = target

    # 2. Frames.
    frames: List[_BuiltFrame] = []
    try:
        if inp.mode == CaptureMode.SNAPSHOT:
            frames = _build_snapshot_frames(inp)
        elif inp.mode == CaptureMode.WALKTHROUGH:
            frames = _build_walkthrough_frames(inp, [s.start for s in segments])
    except Exception as error:
        # Keep the transcript: capture.md is still written, without frames.
        log.exception("Building frames failed")
        note = " · ".join(x for x in (note, "Frames failed: {}".format(error)) if x)
    multi_display = len({f.display for f in frames}) > 1
    if multi_display:
        names = {d.index: d.name for d in inp.displays}
        for f in frames:
            f.ref.display = names.get(f.display)

    # 3. Annotation / click markers.
    markers: List[Marker] = []
    annotation_lines: List[Tuple[float, str]] = []
    for a in inp.annotations:
        if a.is_drawing:
            continue
        index = _frame_index(a, inp.mode, frames)
        if index is None:
            continue
        f = frames[index]
        text = describer.describe(a, f.mapper, f.ref.number)
        markers.append(Marker(a.start_time, text))
        annotation_lines.append((a.start_time, "{} · {}".format(text, markdown.clock(a.start_time))))
    # Pen strokes are referenced per burst (one reference for a sketch drawn in one go).
    bursts = describer.drawing_bursts(
        [a for a in inp.annotations if a.is_drawing], lambda a: _frame_index(a, inp.mode, frames)
    )
    for frame_index, strokes in bursts:
        f = frames[frame_index]
        text = describer.describe_drawing(strokes, f.mapper, f.ref.number)
        start, end = strokes[0].start_time, strokes[-1].end_time
        markers.append(Marker(start, text))
        span = (
            markdown.clock(start)
            if markdown.clock(start) == markdown.clock(end)
            else "{}–{}".format(markdown.clock(start), markdown.clock(end))
        )
        annotation_lines.append((start, "{} · {}".format(text, span)))
    has_click_markers = False
    if inp.mode == CaptureMode.WALKTHROUGH:
        for c in inp.clicks:
            on_display = [f for f in frames if f.display == c.screen]
            if not on_display:
                continue
            earlier = [f for f in on_display if f.ref.time <= c.time + 0.6]
            f = earlier[-1] if earlier else on_display[0]
            text = describer.describe_click(c, f.mapper)
            if multi_display:
                text += " in frame {}".format(f.ref.number)
            markers.append(Marker(c.time, text))
            has_click_markers = True

    # 4. Markdown + events.
    doc = CaptureDocument(
        mode=inp.mode,
        start_date=inp.start_date,
        duration=inp.duration,
        folder=str(folder),
        frames=[f.ref for f in frames],
        words=words,
        markers=markers,
        annotation_lines=[text for _, text in sorted(annotation_lines, key=lambda item: item[0])],
        has_clicks=has_click_markers,
        legend_notes=list(inp.legend_notes) if frames else [],
    )
    text = markdown.compose(doc)
    if note:
        text += "\n_{}_\n".format(note)
    markdown_path = folder / "capture.md"
    _write_text(markdown_path, text)

    first_display = next((d for d in inp.displays if frames and d.index == frames[0].display), None)
    canvas = (
        first_display or (inp.displays[0] if inp.displays else DisplayInfo(0, "", Size(0, 0)))
    ).canvas_size
    events = EventsLog(
        mode=inp.mode,
        start_date=inp.start_date,
        duration=inp.duration,
        canvas_size=canvas,
        displays=[EventsDisplay(d.index, d.name, d.canvas_size) for d in inp.displays],
        frames=[
            EventsFrame(
                number=f.ref.number,
                time=f.ref.time,
                file=Path(f.ref.path).name,
                width=int(f.ref.pixel_size.width),
                height=int(f.ref.pixel_size.height),
                reasons=f.reasons,
                annotation_ids=f.annotation_ids,
                detail_file=Path(f.ref.detail.path).name if f.ref.detail else None,
                display=f.display,
            )
            for f in frames
        ],
        words=words,
        annotations=inp.annotations,
        clicks=inp.clicks,
    )
    _write_text(
        folder / "events.json", json.dumps(events.to_json(), indent=2, sort_keys=True, ensure_ascii=False)
    )

    # 5. Walkthrough: mux each display's video with the audio in the background.
    if inp.mode == CaptureMode.WALKTHROUGH:
        _mux_in_background(inp, audio_path, audio_start)

    return RecentCapture(
        folder=str(folder),
        date=inp.start_date,
        mode=inp.mode,
        duration=inp.duration,
        markdown_path=str(markdown_path),
        image_paths=[p for f in frames for p in f.image_paths],
        frame_paths=[f.ref.path for f in frames],
        word_count=len(words),
        annotation_count=len(annotation_lines),
        note=note,
    )

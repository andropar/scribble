"""Developer commands for exercising the pipeline without the UI (see ``--help``)."""

from __future__ import annotations

import json
import math
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from . import paths


def out(text: str) -> None:
    sys.stdout.write(text + "\n")
    sys.stdout.flush()


def _app(needs_screen: bool = False):
    """A Qt application: off screen unless the command needs the real displays."""
    from PySide6.QtWidgets import QApplication

    if not needs_screen:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    else:
        from .platform import prepare_environment

        prepare_environment()
    return QApplication.instance() or QApplication(["scribble"])


def demo_image(canvas, scale: float):
    """Gradient with white cards: the same test image the macOS app's commands use."""
    from PySide6.QtCore import QPointF, QRectF
    from PySide6.QtGui import QColor, QLinearGradient, QPainter

    from . import render

    w, h = int(canvas.width * scale), int(canvas.height * scale)
    image = render.new_image(w, h)
    painter = QPainter(image)
    gradient = QLinearGradient(QPointF(0, h), QPointF(w, 0))
    gradient.setColorAt(0, QColor.fromRgbF(0.93, 0.94, 0.97))
    gradient.setColorAt(1, QColor.fromRgbF(0.75, 0.8, 0.9))
    painter.fillRect(QRectF(0, 0, w, h), gradient)
    for i in range(6):
        from_bottom = (120 + (i % 3) * 200) * scale
        card = QRectF((60 + i * 220) * scale, h - from_bottom - 120 * scale, 180 * scale, 120 * scale)
        painter.fillRect(card, QColor(255, 255, 255))
    painter.end()
    return image


def demo_annotations(shot: int = 0, start: float = 1.0) -> list:
    from .core.models import BLUE, GREEN, MAGENTA, RED, WHITE, YELLOW, Annotation, AnnotationTool

    anns: List[Annotation] = []

    def add(tool, points, color, t):
        number = None if tool == AnnotationTool.PEN else len([a for a in anns if a.number is not None]) + 1
        a = Annotation(
            tool=tool, points=points, color=color, start_time=t, id=len(anns) + 1, number=number, shot=shot
        )
        a.finalize(t + 0.8)
        anns.append(a)

    turn = 2 * math.pi * 1.05
    circle = [(400 + 120 * math.cos(i / 40 * turn), 300 + 70 * math.sin(i / 40 * turn)) for i in range(41)]
    add(AnnotationTool.PEN, circle, RED, start)
    add(AnnotationTool.ARROW, [(900, 650), (700, 420)], BLUE, start + 2)
    add(AnnotationTool.RECTANGLE, [(1000, 120), (1280, 260)], GREEN, start + 4)
    add(AnnotationTool.HIGHLIGHTER, [(120 + i * 15, 720) for i in range(21)], YELLOW, start + 6)
    add(AnnotationTool.PIN, [(1300, 700)], MAGENTA, start + 8)
    add(AnnotationTool.PEN, [(600 + i * 12, 780) for i in range(21)], RED, start + 9)
    add(AnnotationTool.ELLIPSE, [(200, 450), (330, 520)], WHITE, start + 10)
    return anns


def load_marks(path: str) -> list:
    """Marks of a ``--demo-snapshot`` annotations file: canvas points, session seconds."""
    from .core.models import COLOR_NAMES, Annotation, AnnotationTool

    marks = json.loads(Path(path).read_text(encoding="utf-8"))
    result, numbered = [], 0
    for i, m in enumerate(marks):
        if m["color"] not in COLOR_NAMES:
            raise ValueError("Unknown color {}".format(m["color"]))
        tool = AnnotationTool(m["tool"])
        if tool != AnnotationTool.PEN:
            numbered += 1
        a = Annotation(
            tool=tool,
            points=[(float(p[0]), float(p[1])) for p in m["points"]],
            color=COLOR_NAMES[m["color"]],
            start_time=float(m["start"]),
            id=i + 1,
            number=None if tool == AnnotationTool.PEN else numbered,
        )
        a.finalize(float(m["end"]))
        result.append(a)
    return result


def _load_model(model, compute) -> None:
    from . import transcriber

    whisper = transcriber.shared()
    started = time.time()
    future = whisper.ensure_loaded(model, compute)
    last = ""
    while not future.done():
        title = whisper.status.title
        if title != last:
            out("[model] {}".format(title))
            last = title
        time.sleep(0.25)
    future.result()
    out("[model] {} after {:.1f}s".format(whisper.status.title, time.time() - started))


def _settings_input(mode, folder: Path, t0: float, duration: float, displays):
    from .processor import CaptureInput
    from .settings import AppSettings

    s = AppSettings()
    return CaptureInput(
        mode=mode,
        folder=folder,
        start_date=datetime.now().astimezone(),
        t0=t0,
        duration=duration,
        displays=displays,
        model=s.model,
        compute=s.compute,
        vocabulary=s.vocabulary,
        max_frames=s.max_frames,
        long_edge=s.image_long_edge,
        detail_crops=s.detail_crops,
        keep_originals=s.keep_originals,
        fade_seconds=s.fade_seconds,
    )


def _finish(inp) -> None:
    from . import processor, transcriber

    started = time.time()
    capture = processor.process(inp, transcriber.shared())
    processor.wait_for_background()
    out("processed in {:.2f}s → {}".format(time.time() - started, capture.folder))
    out("----- capture.md -----")
    out(Path(capture.markdown_path).read_text(encoding="utf-8"))


def transcribe(path: str, model_name: Optional[str]) -> None:
    from . import transcriber, video
    from .settings import AppSettings, WhisperModel

    settings = AppSettings()
    model = WhisperModel(model_name) if model_name else settings.model
    samples = video.load_samples(path)
    out("audio: {:.2f}s, rms {:.4f}".format(len(samples) / video.SAMPLE_RATE, video.rms(samples)))
    _load_model(model, settings.compute)
    started = time.time()
    result = transcriber.shared().transcribe(samples, model, settings.compute, settings.vocabulary)
    out("transcribed in {:.2f}s".format(time.time() - started))
    for s in result.segments:
        out("[segment {:6.2f}–{:6.2f}] {}".format(s.start, s.end, s.text))
    for w in result.words:
        out("{:6.2f}–{:6.2f} {}".format(w.start, w.end, w.text))


def render_demo(target: str) -> None:
    from . import render
    from .core import describer
    from .core.geometry import CoordinateMapper, Size, fit_size

    _app()
    canvas = Size(1512, 982)
    image = demo_image(canvas, 2)
    size = fit_size(Size(image.width(), image.height()), 1568)
    rendered = render.render_annotated(image, canvas, demo_annotations(), size)
    if rendered is None:
        raise RuntimeError("render failed")
    render.write_png(rendered, target)
    mapper = CoordinateMapper(canvas, size)
    for a in demo_annotations():
        out(describer.describe(a, mapper, 1))
    out("wrote {}".format(target))


def demo_snapshot(audio_path: str, image_path: Optional[str], marks_path: Optional[str]) -> None:
    from . import render, video
    from .audio import AudioResult
    from .core.geometry import Size
    from .core.models import CaptureMode
    from .processor import DisplayInfo, ShotData
    from .settings import AppSettings

    _app()
    samples = video.load_samples(audio_path)
    settings = AppSettings()
    _load_model(settings.model, settings.compute)
    canvas = Size(3008, 1692)  # a 6K display: exercises detail crops
    image = demo_image(canvas, 2)
    annotations = demo_annotations()
    if image_path and marks_path:
        image = render.load_image(image_path)
        canvas = Size(image.width() // 2, image.height() // 2)
        annotations = load_marks(marks_path)
    t0 = paths.now()
    inp = _settings_input(
        CaptureMode.SNAPSHOT,
        paths.new_capture_folder(),
        t0,
        len(samples) / video.SAMPLE_RATE,
        [DisplayInfo(0, "Demo", canvas)],
    )
    inp.shots = [ShotData(image, 0.1, canvas, 0, 0, True)]
    inp.annotations = annotations
    inp.audio = AudioResult(samples, t0)
    _finish(inp)


def _pick_displays(which: Optional[str]) -> list:
    from . import screens

    available = screens.displays()
    if not available:
        raise RuntimeError("No displays")
    if which == "all":
        return available
    if which is None:
        return [available[0]]
    if not which.isdigit() or int(which) >= len(available):
        raise ValueError("No screen {}".format(which))
    return [available[int(which)]]


def list_displays() -> None:
    from . import platform as platforms
    from . import screens

    _app(needs_screen=True)
    platform = platforms.current()
    grabber = screens.create_grabber(platform)
    out(
        "platform: {}, grabber: {}, compositing: {}".format(
            platform.name, type(grabber).__name__, platform.compositing()
        )
    )
    for d in screens.displays():
        g = d.geometry
        out(
            "display {}: {} · {}x{} at ({},{}) · scale {:.2f} · native {}".format(
                d.index, d.name, g.width(), g.height(), g.x(), g.y(), d.dpr, d.native
            )
        )


def test_screenshot(which: Optional[str]) -> None:
    from . import platform as platforms
    from . import screens
    from .core.models import RED, Annotation, AnnotationTool, CaptureMode
    from .processor import ShotData

    _app(needs_screen=True)
    display = _pick_displays(which)[0]
    grabber = screens.create_grabber(platforms.current())
    image = grabber.grab(display)
    out("screen {} {}: screenshot {}x{}".format(display.index, display.name, image.width(), image.height()))
    inp = _settings_input(
        CaptureMode.SNAPSHOT,
        paths.new_capture_folder(),
        paths.now(),
        1.0,
        [d.info for d in screens.displays()],
    )
    inp.shots = [ShotData(image, 0.0, display.canvas_size, display.index, 0, True)]
    box = Annotation(
        tool=AnnotationTool.RECTANGLE,
        points=[(290, 190), (510, 330)],
        color=RED,
        start_time=0.2,
        id=1,
        number=1,
        screen=display.index,
    )
    box.finalize(0.5)
    inp.annotations = [box]
    _finish(inp)


def test_walkthrough(seconds: float, which: Optional[str]) -> None:
    """Records the given displays. A teal marker appears on the first at 1/4, the cursor
    "moves" to the last at 1/2, where an orange marker appears at 5/8 and is pinned and
    clicked. With one screen both happen there."""
    from PySide6.QtCore import QEventLoop, Qt, QTimer
    from PySide6.QtWidgets import QWidget

    from . import platform as platforms
    from . import screens
    from .core.models import RED, Annotation, AnnotationTool, CaptureMode, ClickEvent
    from .recorder import WalkthroughRecorder

    _app(needs_screen=True)
    platform = platforms.current()
    targets = _pick_displays(which)
    grabber = screens.create_grabber(platform)
    if not grabber.continuous:
        raise RuntimeError("Continuous screen capture is not available in this session")
    folder = paths.new_capture_folder()
    t0 = paths.now()
    recorders = {}
    for d in targets:
        recorder = WalkthroughRecorder(grabber, d, folder / "screen_display{}.mp4".format(d.index + 1))
        recorder.start()
        recorders[d.index] = recorder
        out("screen {} {}: recording {}".format(d.index, d.name, d.native[2:]))

    def wait(fraction: float) -> None:
        loop = QEventLoop()
        QTimer.singleShot(int(seconds * fraction * 1000), loop.quit)
        loop.exec()

    def marker(display, x, y, w, h, color) -> QWidget:
        widget = QWidget()
        widget.setWindowFlags(platform.overlay_flags() | Qt.WindowDoesNotAcceptFocus)
        widget.setAttribute(Qt.WA_ShowWithoutActivating, True)
        widget.setStyleSheet("background: {};".format(color))
        widget.setGeometry(display.geometry.x() + x, display.geometry.y() + y, w, h)
        widget.show()
        return widget

    first, last = targets[0], targets[-1]
    wait(0.25)
    teal = marker(first, 200, 200, 400, 300, "#30b0c7")
    wait(0.375)
    orange = marker(last, 600, 300, 600, 400, "#ff9500")
    wait(0.375)
    duration = paths.now() - t0
    inp = _settings_input(CaptureMode.WALKTHROUGH, folder, t0, duration, [d.info for d in screens.displays()])
    for index, recorder in recorders.items():
        result = recorder.stop()
        name = result.video_path.name if result.video_path else "none"
        out("display {}: frames sampled {}, video {}".format(index, len(result.samples), name))
        inp.recordings[index] = result
    teal.close()
    orange.close()
    inp.cursor_timeline = [(0.0, first.index), (seconds * 0.5, last.index)]
    pin = Annotation(
        tool=AnnotationTool.PIN,
        points=[(700, 350)],
        color=RED,
        start_time=seconds * 0.72,
        id=1,
        number=1,
        screen=last.index,
    )
    pin.finalize(seconds * 0.72)
    inp.annotations = [pin]
    inp.clicks = [ClickEvent(seconds * 0.8, (900, 500), last.index)]
    _finish(inp)


def run(args: List[str]) -> int:
    from .app import USAGE, setup_logging
    from . import __version__

    setup_logging()
    command, rest = args[0], args[1:]
    try:
        if command == "--transcribe" and rest:
            transcribe(rest[0], rest[1] if len(rest) > 1 else None)
        elif command == "--render-demo" and rest:
            render_demo(rest[0])
        elif command == "--demo-snapshot" and rest:
            demo_snapshot(rest[0], rest[1] if len(rest) >= 3 else None, rest[2] if len(rest) >= 3 else None)
        elif command == "--test-screenshot":
            test_screenshot(rest[0] if rest else None)
        elif command == "--test-walkthrough" and rest:
            test_walkthrough(float(rest[0]), rest[1] if len(rest) > 1 else None)
        elif command == "--list-displays":
            list_displays()
        else:
            sys.stderr.write(USAGE.format(version=__version__))
            return 2
    except Exception as error:
        import logging

        logging.getLogger(__name__).debug("Command failed", exc_info=True)
        sys.stderr.write("error: {}\n".format(error))
        return 1
    return 0

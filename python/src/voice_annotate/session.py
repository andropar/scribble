"""Owns the lifecycle of a capture: start → record/annotate → finish → process → clipboard.

A port of ``SessionController.swift``. Works across all connected displays: Snapshot
freezes every display, Walkthrough records every display, and annotations can be drawn
on any of them.
"""

from __future__ import annotations

import logging
import shutil
import threading
import weakref
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtGui import QImage

from . import clipboard, paths, processor, screens, sounds, video
from .audio import AudioRecorder, AudioResult, input_device_name
from .clipboard import RecentsStore
from .core.models import Annotation, AnnotationTool, CaptureMode, ClickEvent
from .platform import Platform
from .processor import CaptureInput, RecentCapture, ShotData
from .recorder import WalkthroughRecorder
from .screens import Display, ScreenGrabber
from .settings import AppSettings, HotkeyAction
from .transcriber import Transcriber
from .ui.canvas import AnnotationCanvas
from .ui.hud import Hud, Toast
from .ui.overlay import SnapshotOverlay, WalkthroughOverlay

log = logging.getLogger(__name__)

HUD_LEGEND_NOTE = (
    "The small dark pill at the top of a frame is voice-annotate's own recording toolbar, "
    "not part of what the user is showing."
)


class _CursorHolder:
    """Latest pointer position, shared with the recorder threads. (They must not hold a
    reference to the controller: the last reference to a QObject may never be dropped on
    a worker thread.)"""

    def __init__(self) -> None:
        self.position: Optional[Tuple[float, float]] = None

    def get(self) -> Optional[Tuple[float, float]]:
        return self.position


class Phase(Enum):
    IDLE = "idle"
    SNAPSHOT = "snapshot"
    SNAPSHOT_NAVIGATING = "snapshotNavigating"
    WALKTHROUGH = "walkthrough"
    DICTATION = "dictation"
    PROCESSING = "processing"

    @property
    def is_capturing(self) -> bool:
        return self in (Phase.SNAPSHOT, Phase.SNAPSHOT_NAVIGATING, Phase.WALKTHROUGH, Phase.DICTATION)


class CaptureSession:
    def __init__(self, mode: CaptureMode, folder: Path, displays: List[Display]):
        self.mode = mode
        self.folder = folder
        self.start_date = datetime.now().astimezone()
        self.t0 = paths.now()
        #: All displays connected when the capture started.
        self.displays = displays
        self.annotations: List[Annotation] = []
        self.clicks: List[ClickEvent] = []
        self.shots: List[ShotData] = []
        self.round = -1
        self.cursor_timeline: List[Tuple[float, int]] = []

    def now(self) -> float:
        return paths.now() - self.t0

    @property
    def next_id(self) -> int:
        return max((a.id for a in self.annotations), default=0) + 1

    @property
    def next_mark_number(self) -> int:
        return max((a.number for a in self.annotations if a.number is not None), default=0) + 1

    @property
    def mark_count(self) -> int:
        """Marks with a number badge (pen drawings don't count)."""
        return len([a for a in self.annotations if a.number is not None])


class SessionController(QObject):
    phase_changed = Signal(object)
    #: Emitted with the RecentCapture (or None for dictation/failure) when processing ends.
    capture_finished = Signal(object)

    _level = Signal(float)
    _processed = Signal(object, object, object)
    _dictated = Signal(object, object, object)
    _recording_failed = Signal(int, str)
    _model_status = Signal()

    def __init__(
        self,
        settings: AppSettings,
        recents: RecentsStore,
        transcriber: Transcriber,
        platform: Platform,
        grabber: ScreenGrabber,
        audio_factory: Callable[[], AudioRecorder] = AudioRecorder,
    ):
        super().__init__()
        self.settings = settings
        self.recents = recents
        self.transcriber = transcriber
        self.platform = platform
        self.grabber = grabber
        self._audio_factory = audio_factory

        self._phase = Phase.IDLE
        self.session: Optional[CaptureSession] = None
        #: One overlay per display taking part (keyed by display index).
        self.overlays: Dict[int, AnnotationCanvas] = {}
        self.hud: Optional[Hud] = None
        self.toast = Toast(platform)
        self._hud_display: Optional[Display] = None
        self._audio: Optional[AudioRecorder] = None
        self._recorders: Dict[int, WalkthroughRecorder] = {}
        self._focus_token: Any = None
        self._last_cursor_display: Optional[int] = None
        self._left_was_down = False
        self._next_raise = 0.0
        self._cursor = _CursorHolder()
        self._threads: List[threading.Thread] = []
        self._processing_toast: Optional[Tuple[str, Any]] = None
        self._tool = AnnotationTool.PEN
        self._color_index = 0
        self.on_needs_setup: Optional[Callable[[], None]] = None

        self._ticker = QTimer(self)
        self._ticker.timeout.connect(self._tick)
        self._level.connect(self._on_level)
        self._processed.connect(self._on_processed)
        self._dictated.connect(self._on_dictated)
        self._recording_failed.connect(self._on_recording_failed)
        self._model_status.connect(self._show_processing_toast)
        transcriber.subscribe(lambda status: self._model_status.emit())

    # MARK: state

    @property
    def phase(self) -> Phase:
        return self._phase

    def _set_phase(self, phase: Phase) -> None:
        if phase != self._phase:
            self._phase = phase
            self.phase_changed.emit(phase)

    # MARK: hotkeys

    def handle(self, action: HotkeyAction) -> None:
        phase = self._phase
        if phase == Phase.PROCESSING:
            self._show_processing_toast()
        elif action == HotkeyAction.SNAPSHOT:
            if phase == Phase.IDLE:
                self.start_snapshot()
            elif phase == Phase.SNAPSHOT:
                self.finish()
            elif phase == Phase.SNAPSHOT_NAVIGATING:
                self._capture_round()
        elif action == HotkeyAction.WALKTHROUGH:
            if phase == Phase.IDLE:
                self.start_walkthrough()
            elif phase == Phase.WALKTHROUGH:
                self.finish()
        elif action == HotkeyAction.DICTATION:
            if phase == Phase.IDLE:
                self.start_dictation()
            elif phase == Phase.DICTATION:
                self.finish()
        elif action == HotkeyAction.FINISH:
            if phase.is_capturing:
                self.finish()
        elif action == HotkeyAction.TOGGLE_DRAW:
            if phase == Phase.WALKTHROUGH:
                self._toggle_draw()

    # MARK: Snapshot

    def start_snapshot(self) -> None:
        if self._phase != Phase.IDLE:
            return
        session = self._make_session(CaptureMode.SNAPSHOT)
        if session is None:
            return
        self._set_phase(Phase.SNAPSHOT)
        self._focus_token = self.platform.remember_focus()
        self._reset_hud(CaptureMode.SNAPSHOT)
        self._start_audio(session)
        self._capture_round()

    def _cursor_display(self, session: CaptureSession) -> Display:
        x, y = screens.cursor_native(self.platform, session.displays)
        return screens.display_at(session.displays, x, y) or session.displays[0]

    def _capture_round(self) -> None:
        """Freezes the displays (all of them, or the one under the cursor) as a new round."""
        session = self.session
        if session is None:
            return
        # Our own pills must not be in the screenshot. Where the system cannot exclude
        # them from capture, hide them and give the screen a moment to repaint.
        in_the_way = [
            w
            for w in (self.hud, self.toast)
            if w is not None and w.isVisible() and not w.excluded_from_capture
        ]
        self.toast.hide()
        if in_the_way:
            for widget in in_the_way:
                widget.hide()
            QTimer.singleShot(120, lambda: self._grab_round(session))
        else:
            self._grab_round(session)

    def _hud_excluded(self) -> bool:
        return self.hud is not None and self.hud.excluded_from_capture

    def _grab_round(self, session: CaptureSession) -> None:
        if self.session is not session or self._phase not in (Phase.SNAPSHOT, Phase.SNAPSHOT_NAVIGATING):
            return
        primary = self._cursor_display(session)
        targets = session.displays if self.settings.all_displays else [primary]
        images: Dict[int, QImage] = {}
        for d in targets:
            try:
                images[d.index] = self.grabber.grab(d)
            except Exception as error:
                log.error("Screenshot of display %d failed: %s", d.index, error)
                if d.index == primary.index:
                    self._abort("Screenshot failed", str(error))
                    return
        session.round += 1
        time = session.now()
        shot_indices: Dict[int, int] = {}
        for d in targets:
            if d.index not in images:
                continue
            shot_indices[d.index] = len(session.shots)
            session.shots.append(
                ShotData(
                    images[d.index], time, d.canvas_size, d.index, session.round, d.index == primary.index
                )
            )
        self._present_round(session, shot_indices, primary)
        if session.round == 0:
            sounds.start()

    def _present_round(self, session: CaptureSession, shot_indices: Dict[int, int], primary: Display) -> None:
        self._close_overlays()
        for display_index, shot_index in sorted(shot_indices.items()):
            display = session.displays[display_index]
            overlay = SnapshotOverlay(display, session.shots[shot_index].image, self.platform)
            self._configure(overlay, shot=shot_index, screen=display_index, fade=None)
            self.overlays[display_index] = overlay
            overlay.present(take_keyboard=False)
        key = self.overlays.get(primary.index) or next(iter(self.overlays.values()), None)
        if isinstance(key, SnapshotOverlay):
            key.take_keyboard()

        self._set_phase(Phase.SNAPSHOT)
        hud = self._ensure_hud()
        hud.navigating = False
        hud.shot_count = session.round + 1
        hud.starting = False
        hud.refresh()
        self._show_hud(primary)

    def _hide_for_next_shot(self) -> None:
        if self._phase != Phase.SNAPSHOT:
            return
        self._set_phase(Phase.SNAPSHOT_NAVIGATING)
        self._detach_hud()
        self._close_overlays()
        hud = self._ensure_hud()
        hud.navigating = True
        hud.refresh()
        if self._hud_display is not None:
            self._show_hud(self._hud_display)
        self.platform.restore_focus(self._focus_token)

    # MARK: Walkthrough

    def start_walkthrough(self) -> None:
        if self._phase != Phase.IDLE:
            return
        if not (self.platform.can_record and self.grabber.continuous):
            self.toast.show_message(
                "Walkthrough is not available here",
                "Recording the screen needs an X11 session (see Permissions & help)",
                success=False,
                duration=5,
            )
            if self.on_needs_setup is not None:
                self.on_needs_setup()
            return
        session = self._make_session(CaptureMode.WALKTHROUGH)
        if session is None:
            return
        self._set_phase(Phase.WALKTHROUGH)
        primary = self._cursor_display(session)
        targets = session.displays if self.settings.all_displays else [primary]
        session.cursor_timeline = [(0.0, primary.index)]
        self._last_cursor_display = primary.index
        self._left_was_down = False
        self._next_raise = 1.0

        for d in targets:
            overlay = WalkthroughOverlay(d, self.platform)
            self._configure(overlay, shot=0, screen=d.index, fade=self.settings.fade_seconds)
            if not overlay.composited:
                # Without a compositor the overlay grabs the pointer while drawing, so a
                # click on the toolbar arrives at the overlay and is passed on.
                overlay.press_filter = self._toolbar_press
            self.overlays[d.index] = overlay
            overlay.present()

        self._reset_hud(CaptureMode.WALKTHROUGH)
        self._show_hud(primary)
        self._start_audio(session)

        for d in targets:
            output = session.folder / "screen_display{}.mp4".format(d.index + 1)
            recorder = WalkthroughRecorder(self.grabber, d, output, cursor=self._cursor.get)
            recorder.on_error = self._recorder_error_handler(d.index)
            self._recorders[d.index] = recorder
            recorder.start()
        self._start_ticker(16)

    def _recorder_error_handler(self, display_index: int) -> Callable[[BaseException], None]:
        controller = weakref.ref(self)

        def report(error: BaseException) -> None:
            target = controller()
            if target is not None:
                target._recording_failed.emit(display_index, str(error))

        return report

    def _on_recording_failed(self, display_index: int, message: str) -> None:
        if self._phase != Phase.WALKTHROUGH or display_index not in self._recorders:
            return
        hud = self.hud
        if hud is not None and hud.starting:
            self._abort("Screen recording failed", message)
            return
        self.toast.show_message("Recording stopped", message, success=False, duration=4)
        self.finish()

    def _toggle_draw(self) -> None:
        if self.hud is not None:
            self.hud.draw_toggled = not self.hud.draw_toggled

    def _toolbar_press(self, global_pos: Any) -> bool:
        return self.hud is not None and self.hud.click_at(global_pos)

    # MARK: Dictation

    def start_dictation(self) -> None:
        if self._phase != Phase.IDLE:
            return
        if input_device_name() is None:
            self.toast.show_message(
                "No microphone found",
                "Connect a microphone, or see Permissions & help",
                success=False,
                duration=4,
            )
            return
        session = self._make_session(CaptureMode.DICTATION)
        if session is None:
            return
        self._set_phase(Phase.DICTATION)
        self._reset_hud(CaptureMode.DICTATION)
        self._show_hud(self._cursor_display(session))
        self._start_audio(session)

    def _finish_dictation(self, session: CaptureSession) -> None:
        self._set_phase(Phase.PROCESSING)
        audio_result = self._stop_audio()
        screen = self._hud_display.screen if self._hud_display is not None else None
        self._teardown_ui()
        sounds.stop()
        self._begin_processing_toast("Transcribing…", screen)
        settings = self.settings
        model, compute, vocabulary = settings.model, settings.compute, settings.vocabulary

        def work() -> None:
            samples = audio_result.samples if audio_result is not None else []
            text, failure = "", None
            if len(samples):
                try:
                    text = self.transcriber.transcribe(samples, model, compute, vocabulary).plain_text
                except Exception as error:
                    log.error("Dictation failed: %s", error)
                    failure = error
            self._save_dictation(samples, text, session.folder)
            self._dictated.emit(text, failure, screen)

        self._run_in_background(work, "dictation")

    @staticmethod
    def _save_dictation(samples: Any, text: str, folder: Path) -> None:
        """Keeps the audio and text in the capture folder (pruned like captures) in case
        the clipboard gets overwritten."""
        try:
            if not len(samples):
                shutil.rmtree(folder, ignore_errors=True)
                return
            if text:
                (folder / "transcript.txt").write_text(text, encoding="utf-8")
            video.write_m4a(samples, folder / "audio.m4a")
        except Exception as error:
            log.error("Saving dictation failed: %s", error)

    def _on_dictated(self, text: str, failure: Any, screen: Any) -> None:
        self._end_processing_toast()
        if failure is not None:
            self.toast.show_message(
                "Transcription failed", str(failure), success=False, duration=5, screen=screen
            )
            sounds.error()
        elif not text:
            self.toast.show_message("No speech detected", success=False, duration=2.5, screen=screen)
        else:
            try:
                clipboard.write_text(text)
                self.toast.show_message(
                    "Copied to clipboard", "{} words".format(len(text.split())), duration=2.5, screen=screen
                )
                sounds.done()
            except Exception as error:
                self.toast.show_message("Copy failed", str(error), success=False, duration=4, screen=screen)
                sounds.error()
        self.session = None
        self._set_phase(Phase.IDLE)
        self.capture_finished.emit(text or None)

    # MARK: ticker

    def _start_ticker(self, interval_ms: int) -> None:
        self._ticker.start(interval_ms)

    def _tick(self) -> None:
        session = self.session
        if session is None:
            return
        hud = self.hud
        if hud is not None:
            hud.set_elapsed(session.now())
        if self._phase != Phase.WALKTHROUGH or hud is None:
            return

        if hud.starting and self._recorders and all(r.started.is_set() for r in self._recorders.values()):
            failed = [r for r in self._recorders.values() if r.failed is not None]
            if not failed:
                hud.starting = False
                hud.refresh()
                sounds.start()

        state = self.platform.input_state() if self.platform.tracks_input else None
        x, y = (
            (state.x, state.y)
            if state is not None
            else screens.cursor_native(self.platform, session.displays)
        )
        self._cursor.position = (x, y)
        display = screens.display_at(session.displays, x, y)

        # Follow the cursor across displays: timeline for keyframe focus, HUD placement.
        if display is not None and display.index != self._last_cursor_display:
            self._last_cursor_display = display.index
            session.cursor_timeline.append((session.now(), display.index))
            if display.index in self._recorders:
                self._show_hud(display)

        held = state is not None and self.settings.draw_modifier.matches(state.mods)
        tracking = any(o.is_tracking for o in self.overlays.values())
        drawing = hud.draw_toggled or held or tracking
        if drawing != hud.drawing:
            hud.drawing = drawing
            hud.refresh()
            for overlay in self.overlays.values():
                if isinstance(overlay, WalkthroughOverlay):
                    overlay.set_drawing(drawing)
        if drawing and not tracking:
            # Without a compositor an overlay draws through a pointer grab, and only one
            # window can hold it: the overlay of the display the pointer is on.
            for index, overlay in self.overlays.items():
                if isinstance(overlay, WalkthroughOverlay) and overlay.needs_pointer_grab:
                    overlay.set_pointer_grab(display is not None and index == display.index)

        # Clicks on whatever the user is showing (not strokes on the overlay, not the HUD).
        if state is not None:
            pressed = state.left_down and not self._left_was_down
            self._left_was_down = state.left_down
            if pressed and not drawing and display is not None and display.index in self._recorders:
                if not self._over_hud(display, x, y):
                    session.clicks.append(
                        ClickEvent(session.now(), display.canvas_point(x, y), display.index)
                    )

        # Unmanaged X11 windows are not kept on top by the window manager: after another
        # window was raised (typically by a click), put the overlays back above it.
        if self.platform.name == "x11" and session.now() >= self._next_raise:
            self._next_raise = session.now() + 1.0
            self._raise_overlays()

        if session.now() >= self.settings.max_walkthrough_minutes * 60:
            self.toast.show_message("Maximum recording length reached", duration=2)
            self.finish()

    def _raise_overlays(self) -> None:
        for overlay in self.overlays.values():
            overlay.raise_()
        if self.hud is not None and self.hud.isVisible():
            self.hud.raise_()

    def _over_hud(self, display: Display, x: float, y: float) -> bool:
        if self.hud is None or not self.hud.isVisible() or self._hud_display is not display:
            return False
        cx, cy = display.canvas_point(x, y)
        g = self.hud.frameGeometry()
        return g.contains(int(display.geometry.x() + cx), int(display.geometry.y() + cy))

    # MARK: finish / cancel

    def finish(self) -> None:
        session = self.session
        if session is None or not self._phase.is_capturing:
            return
        if session.mode == CaptureMode.DICTATION:
            self._finish_dictation(session)
            return
        # Nothing captured yet (recording starting, first screenshot pending).
        if self.hud is not None and self.hud.starting:
            return
        self._set_phase(Phase.PROCESSING)
        mode = session.mode
        duration = session.now()
        audio_result = self._stop_audio()
        recorders, self._recorders = self._recorders, {}
        screen = self._hud_display.screen if self._hud_display is not None else None
        hud_in_frames = mode == CaptureMode.WALKTHROUGH and not self._hud_excluded()
        self._teardown_ui()
        if mode == CaptureMode.SNAPSHOT:
            self.platform.restore_focus(self._focus_token)
        sounds.stop()
        self._begin_processing_toast("Processing capture…", screen)

        s = self.settings
        inp = CaptureInput(
            mode=mode,
            folder=session.folder,
            start_date=session.start_date,
            t0=session.t0,
            duration=duration,
            displays=[d.info for d in session.displays],
            annotations=list(session.annotations),
            clicks=list(session.clicks),
            shots=list(session.shots),
            audio=audio_result,
            cursor_timeline=list(session.cursor_timeline),
            model=s.model,
            compute=s.compute,
            vocabulary=s.vocabulary,
            max_frames=s.max_frames,
            long_edge=s.image_long_edge,
            detail_crops=s.detail_crops,
            keep_originals=s.keep_originals,
            fade_seconds=s.fade_seconds,
            legend_notes=[HUD_LEGEND_NOTE] if hud_in_frames else [],
        )

        def work() -> None:
            try:
                for index, recorder in recorders.items():
                    inp.recordings[index] = recorder.stop()
                capture = processor.process(inp, self.transcriber)
                self._processed.emit(capture, None, (screen, audio_result is None))
            except Exception as error:
                log.exception("Processing failed")
                self._processed.emit(None, error, (screen, audio_result is None))

        self._run_in_background(work, "process")

    def _on_processed(self, capture: Optional[RecentCapture], failure: Any, context: Any) -> None:
        screen, no_microphone = context
        self._end_processing_toast()
        if capture is not None:
            # Listed first, so a failed clipboard write can be redone from the menu.
            self.recents.add(capture)
            try:
                clipboard.write(capture, self.settings.clipboard_format)
                detail = capture.note or capture.summary
                if no_microphone:
                    detail += " · no microphone"
                self.toast.show_message(
                    "Copied to clipboard",
                    detail,
                    success=capture.note is None,
                    duration=2.8 if capture.note is None else 5,
                    screen=screen,
                )
                sounds.done()
            except Exception as error:
                log.exception("Clipboard write failed")
                self.toast.show_message("Copy failed", str(error), success=False, duration=5, screen=screen)
                sounds.error()
        else:
            self.toast.show_message("Capture failed", str(failure), success=False, duration=5, screen=screen)
            sounds.error()
        self.session = None
        self._set_phase(Phase.IDLE)
        self.capture_finished.emit(capture)

    def cancel(self) -> None:
        session = self.session
        if session is None or not self._phase.is_capturing:
            return
        mode = session.mode
        self._stop_audio()
        recorders, self._recorders = list(self._recorders.values()), {}
        self._teardown_ui()
        if mode == CaptureMode.SNAPSHOT:
            self.platform.restore_focus(self._focus_token)
        self.session = None
        self._set_phase(Phase.IDLE)
        folder = session.folder

        def cleanup() -> None:
            for recorder in recorders:
                recorder.stop()
            shutil.rmtree(folder, ignore_errors=True)

        self._run_in_background(cleanup, "cancel")
        self.toast.show_message("Capture cancelled", success=False, duration=1.5)

    def _abort(self, message: str, detail: str) -> None:
        self.cancel()
        self.toast.show_message(message, detail, success=False, duration=5)
        sounds.error()

    def _close_overlays(self) -> None:
        for overlay in self.overlays.values():
            overlay.stop()
            overlay.close()
        self.overlays = {}

    def _detach_hud(self) -> None:
        if self.hud is not None:
            self.hud.detach()

    def _teardown_ui(self) -> None:
        self._ticker.stop()
        self._detach_hud()
        self._close_overlays()
        if self.hud is not None:
            self.hud.hide()
        self._last_cursor_display = None
        self._cursor.position = None

    # MARK: canvas events

    def _on_annotation(self, annotation: Annotation) -> None:
        session, canvas = self.session, self.sender()
        if session is None or not isinstance(canvas, AnnotationCanvas):
            return
        annotation.id = session.next_id
        if not annotation.is_drawing:
            annotation.number = session.next_mark_number
        session.annotations.append(annotation)
        canvas.add(annotation)
        if self.hud is not None:
            self.hud.annotation_count = session.mark_count
            self.hud.refresh()

    def _undo(self) -> None:
        """Removes the most recent annotation that is still visible on any display."""
        session = self.session
        if session is None:
            return
        visible: Dict[int, AnnotationCanvas] = {}
        for canvas in self.overlays.values():
            for a in canvas.annotations:
                visible[a.id] = canvas
        candidates = [a for a in session.annotations if a.id in visible]
        if not candidates:
            return
        last = max(candidates, key=lambda a: a.id)
        session.annotations = [a for a in session.annotations if a.id != last.id]
        visible[last.id].remove(last.id)
        if self.hud is not None:
            self.hud.annotation_count = session.mark_count
            self.hud.refresh()

    def _select_tool(self, tool: AnnotationTool) -> None:
        self._tool = tool
        for canvas in self.overlays.values():
            canvas.tool = tool
        if self.hud is not None:
            self.hud.tool = tool
            self.hud.refresh()

    def _select_color(self, index: int) -> None:
        self._color_index = index
        for canvas in self.overlays.values():
            canvas.color_index = index
        if self.hud is not None:
            self.hud.color_index = index
            self.hud.refresh()

    # MARK: helpers

    def _run_in_background(self, work: Callable[[], None], name: str) -> None:
        self._threads = [t for t in self._threads if t.is_alive()]
        thread = threading.Thread(target=work, name=name, daemon=True)
        self._threads.append(thread)
        thread.start()

    def wait_for_background(self, timeout: float = 30) -> bool:
        """Waits for worker threads (processing, cleanup after a cancel) to end. Returns
        whether all of them did."""
        deadline = paths.now() + timeout
        for thread in list(self._threads):
            thread.join(max(0.0, deadline - paths.now()))
        self._threads = [t for t in self._threads if t.is_alive()]
        return not self._threads

    def _make_session(self, mode: CaptureMode) -> Optional[CaptureSession]:
        displays = screens.displays()
        if not displays:
            return None
        try:
            self.session = CaptureSession(mode, paths.new_capture_folder(), displays)
            return self.session
        except OSError as error:
            self.toast.show_message("Cannot create capture folder", str(error), success=False, duration=4)
            return None

    def _begin_processing_toast(self, title: str, screen: Any) -> None:
        """Shows a busy toast that follows the model status, so a first capture waiting for
        the model download shows progress instead of looking stuck."""
        self._processing_toast = (title, screen)
        self._show_processing_toast()

    def _show_processing_toast(self) -> None:
        if self._phase != Phase.PROCESSING or self._processing_toast is None:
            return
        title, screen = self._processing_toast
        status = self.transcriber.status
        detail = (
            "Transcribing and composing"
            if status.is_ready
            else "Waiting for the model: {}".format(status.title)
        )
        self.toast.show_message(title, detail, busy=True, duration=None, screen=screen)

    def _end_processing_toast(self) -> None:
        self._processing_toast = None

    def _configure(self, canvas: AnnotationCanvas, shot: int, screen: int, fade: Optional[float]) -> None:
        session = self.session
        canvas.tool = self._tool
        canvas.color_index = self._color_index
        canvas.shot = shot
        canvas.screen_index = screen
        canvas.fade_after = fade
        canvas.clock = lambda: session.now() if session is not None else 0.0
        canvas.finished.connect(self._on_annotation)
        canvas.finish_requested.connect(self.finish)
        canvas.cancel_requested.connect(self.cancel)
        canvas.next_shot_requested.connect(self._hide_for_next_shot)
        canvas.undo_requested.connect(self._undo)
        canvas.tool_selected.connect(self._select_tool)
        canvas.color_selected.connect(self._select_color)

    def _ensure_hud(self) -> Hud:
        if self.hud is None:
            hud = Hud(
                self.platform,
                snapshot_key=self.settings.hotkey(HotkeyAction.SNAPSHOT).display,
                finish_key=self.settings.hotkey(HotkeyAction.FINISH).display,
                draw_modifier=self.settings.draw_modifier.symbol if self.platform.tracks_input else "",
            )
            hud.tool_selected.connect(self._select_tool)
            hud.color_selected.connect(self._select_color)
            hud.undo_requested.connect(self._undo)
            hud.next_shot_requested.connect(self._hide_for_next_shot)
            hud.toggle_draw_requested.connect(self._toggle_draw)
            hud.finish_requested.connect(self.finish)
            hud.cancel_requested.connect(self.cancel)
            self.hud = hud
        return self.hud

    def _reset_hud(self, mode: CaptureMode) -> None:
        hud = self._ensure_hud()
        hud.snapshot_key = self.settings.hotkey(HotkeyAction.SNAPSHOT).display
        hud.finish_key = self.settings.hotkey(HotkeyAction.FINISH).display
        hud.draw_modifier = self.settings.draw_modifier.symbol if self.platform.tracks_input else ""
        hud.tool, hud.color_index = self._tool, self._color_index
        hud.reset(mode, mic_available=True)
        self._start_ticker(100)

    def _show_hud(self, display: Display) -> None:
        hud = self._ensure_hud()
        self._hud_display = display
        hud.show_on(display.screen, above=self.overlays.get(display.index))

    def _on_level(self, level: float) -> None:
        if self.hud is not None and self._phase.is_capturing:
            self.hud.set_level(level)

    def _start_audio(self, session: CaptureSession) -> None:
        hud = self._ensure_hud()
        recorder = self._audio_factory()
        recorder.on_level = self._level.emit
        try:
            recorder.start()
            self._audio = recorder
            hud.mic_available = True
            if session.mode == CaptureMode.DICTATION:
                hud.starting = False
                sounds.start()
        except Exception as error:
            log.error("Microphone start failed: %s", error)
            hud.mic_available = False
        hud.refresh()

    def _stop_audio(self) -> Optional[AudioResult]:
        recorder, self._audio = self._audio, None
        if recorder is None:
            return None
        try:
            return recorder.stop()
        except Exception as error:
            log.error("Stopping the microphone failed: %s", error)
            return None

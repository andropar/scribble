"""The Settings window and the welcome / help window."""

from __future__ import annotations

from typing import Callable, Dict, List, Optional

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .. import paths
from ..audio import input_device_name
from ..platform import HotkeyBackend, Platform
from ..settings import (
    AppSettings,
    ClipboardFormat,
    DrawModifier,
    Hotkey,
    HotkeyAction,
    WhisperCompute,
    WhisperModel,
)
from ..transcriber import Transcriber
from . import icons

_OVERLAY_KEYS = (
    "P pen · A arrow · R box · O ellipse · H highlighter · N pin · 1–6 colors · Ctrl+Z undo · "
    "Tab next shot · Enter finish · Esc cancel. Pen strokes stay as drawn and get no number."
)

_KEY_NAMES = {
    Qt.Key_Space: "Space",
    Qt.Key_Tab: "Tab",
    Qt.Key_Return: "Return",
    Qt.Key_Enter: "Return",
    Qt.Key_Backspace: "Backspace",
    Qt.Key_Delete: "Delete",
    Qt.Key_Insert: "Insert",
    Qt.Key_Home: "Home",
    Qt.Key_End: "End",
    Qt.Key_PageUp: "PageUp",
    Qt.Key_PageDown: "PageDown",
    Qt.Key_Left: "Left",
    Qt.Key_Up: "Up",
    Qt.Key_Right: "Right",
    Qt.Key_Down: "Down",
}


def key_name(key: int) -> Optional[str]:
    """Hotkey key name for a Qt key code, or None for keys that cannot be a hotkey."""
    if Qt.Key_A <= key <= Qt.Key_Z or Qt.Key_0 <= key <= Qt.Key_9:
        return chr(key)
    if Qt.Key_F1 <= key <= Qt.Key_F24:
        return "F{}".format(key - Qt.Key_F1 + 1)
    return _KEY_NAMES.get(key)


def hotkey_from_event(key: int, modifiers: Qt.KeyboardModifier) -> Optional[Hotkey]:
    """A hotkey for a key press, or None if it is not usable as a global shortcut
    (no modifier, or Shift alone, would swallow normal typing)."""
    name = key_name(key)
    mods = []
    if modifiers & Qt.ControlModifier:
        mods.append("ctrl")
    if modifiers & Qt.AltModifier:
        mods.append("alt")
    if modifiers & Qt.ShiftModifier:
        mods.append("shift")
    if modifiers & Qt.MetaModifier:
        mods.append("super")
    if name is None or not mods or mods == ["shift"]:
        return None
    return Hotkey(tuple(mods), name)


class HotkeyRecorder(QPushButton):
    """A button that records the next key combination as a shortcut."""

    recorded = Signal(object)

    def __init__(self, hotkey: Hotkey, backend: Optional[HotkeyBackend] = None):
        super().__init__(hotkey.display)
        self._hotkey = hotkey
        self._backend = backend
        self._recording = False
        self.setMinimumWidth(170)
        self.clicked.connect(self._toggle)

    def set_hotkey(self, hotkey: Hotkey) -> None:
        self._hotkey = hotkey
        if not self._recording:
            self.setText(hotkey.display)

    def _toggle(self) -> None:
        if self._recording:
            self._stop()
        else:
            self._recording = True
            self.setText("Press shortcut…")
            # Our own hotkeys must not fire (or be refused) while a new one is typed.
            if self._backend is not None:
                self._backend.suspend()
            self.grabKeyboard()

    def _stop(self) -> None:
        if self._recording:
            self._recording = False
            self.releaseKeyboard()
            if self._backend is not None:
                self._backend.resume()
        self.setText(self._hotkey.display)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if not self._recording:
            super().keyPressEvent(event)
            return
        key = event.key()
        if key in (Qt.Key_Control, Qt.Key_Shift, Qt.Key_Alt, Qt.Key_Meta, Qt.Key_AltGr):
            return
        if key == Qt.Key_Escape:
            self._stop()
            return
        hotkey = hotkey_from_event(key, event.modifiers())
        if hotkey is None:
            return
        self._hotkey = hotkey
        self._stop()
        self.recorded.emit(hotkey)

    def focusOutEvent(self, event) -> None:
        self._stop()
        super().focusOutEvent(event)

    def hideEvent(self, event) -> None:
        self._stop()
        super().hideEvent(event)


def _combo(options, current, on_change: Callable) -> QComboBox:
    box = QComboBox()
    for option in options:
        box.addItem(option.title, option)
    box.setCurrentIndex(max(0, box.findData(current)))
    box.currentIndexChanged.connect(lambda i: on_change(box.itemData(i)))
    return box


def _note(text: str) -> QLabel:
    label = QLabel(text)
    label.setWordWrap(True)
    label.setStyleSheet("color: #767676; font-size: 11px;")
    label.setTextInteractionFlags(Qt.TextSelectableByMouse)
    return label


class SettingsWindow(QWidget):
    def __init__(
        self,
        settings: AppSettings,
        transcriber: Transcriber,
        platform: Platform,
        hotkeys: Optional[HotkeyBackend] = None,
    ):
        super().__init__()
        self.settings, self.transcriber, self.platform, self.hotkeys = (
            settings,
            transcriber,
            platform,
            hotkeys,
        )
        self.setWindowTitle("voice-annotate Settings")
        self.setWindowIcon(icons.app_icon())
        self.hotkey_failures: Dict[str, str] = {}
        tabs = QTabWidget()
        tabs.addTab(self._general(), "General")
        tabs.addTab(self._capture(), "Capture")
        tabs.addTab(self._transcription(), "Transcription")
        tabs.addTab(self._shortcuts(), "Shortcuts")
        layout = QVBoxLayout(self)
        layout.addWidget(tabs)
        self.setMinimumWidth(580)
        self._status_timer = QTimer(self)
        self._status_timer.setInterval(500)
        self._status_timer.timeout.connect(self._refresh_status)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._refresh_status()
        self._status_timer.start()

    def hideEvent(self, event) -> None:
        self._status_timer.stop()
        super().hideEvent(event)

    def _check(self, title: str, key: str) -> QCheckBox:
        box = QCheckBox(title)
        box.setChecked(bool(self.settings.get(key)))
        box.toggled.connect(lambda on: self.settings.set(key, bool(on)))
        return box

    def _spin(self, key: str, low: int, high: int, suffix: str = "") -> QSpinBox:
        spin = QSpinBox()
        spin.setRange(low, high)
        spin.setSuffix(suffix)
        spin.setValue(int(self.settings.get(key)))
        spin.valueChanged.connect(lambda v: self.settings.set(key, int(v)))
        return spin

    def _general(self) -> QWidget:
        page = QWidget()
        form = QFormLayout(page)
        form.addRow(
            "Clipboard format",
            _combo(
                ClipboardFormat,
                self.settings.clipboard_format,
                lambda v: self.settings.set("clipboardFormat", v),
            ),
        )
        form.addRow("", _note("Every format is also available later from the tray menu → Recent captures."))
        form.addRow("Keep captures for", self._spin("retentionDays", 1, 365, " days"))
        folder = QPushButton(str(paths.captures()))
        folder.setFlat(True)
        folder.setCursor(Qt.PointingHandCursor)
        folder.clicked.connect(lambda: self.platform.open_folder(str(paths.captures())))
        form.addRow("Stored in", folder)
        form.addRow("", self._check("Play sounds", "playSounds"))
        autostart = QCheckBox("Launch at login")
        try:
            autostart.setChecked(self.platform.autostart_enabled())
        except Exception:
            autostart.setEnabled(False)
        autostart.toggled.connect(self._set_autostart)
        self._autostart = autostart
        form.addRow("", autostart)
        return page

    def _set_autostart(self, on: bool) -> None:
        try:
            self.platform.set_autostart(on)
        except Exception as error:
            self._autostart.blockSignals(True)
            self._autostart.setChecked(not on)
            self._autostart.blockSignals(False)
            self._autostart.setToolTip(str(error))

    def _capture(self) -> QWidget:
        page = QWidget()
        column = QVBoxLayout(page)
        images = QGroupBox("Images")
        form = QFormLayout(images)
        edge = QComboBox()
        for value, label in (
            (1092, "1092 px (smallest)"),
            (1568, "1568 px (recommended for Claude)"),
            (2048, "2048 px"),
            (2560, "2560 px"),
        ):
            edge.addItem(label, value)
        edge.setCurrentIndex(max(0, edge.findData(self.settings.image_long_edge)))
        edge.currentIndexChanged.connect(lambda i: self.settings.set("imageLongEdge", edge.itemData(i)))
        form.addRow("Image size (long edge)", edge)
        form.addRow(
            "",
            self._check("Capture all displays (draw on any; walkthroughs follow the cursor)", "allDisplays"),
        )
        form.addRow("", self._check("Add zoomed detail crops on large displays", "detailCrops"))
        form.addRow("", self._check("Also keep un-annotated screenshots", "keepOriginals"))
        column.addWidget(images)

        walkthrough = QGroupBox("Walkthrough")
        form = QFormLayout(walkthrough)
        form.addRow("Max keyframes", self._spin("maxFrames", 4, 80))
        form.addRow("Max length", self._spin("maxWalkthroughMinutes", 1, 60, " min"))
        form.addRow(
            "Draw mode",
            _combo(DrawModifier, self.settings.draw_modifier, lambda v: self.settings.set("drawModifier", v)),
        )
        fade = QDoubleSpinBox()
        fade.setRange(1.0, 10.0)
        fade.setSingleStep(0.5)
        fade.setDecimals(1)
        fade.setSuffix(" s")
        fade.setValue(self.settings.fade_seconds)
        fade.valueChanged.connect(lambda v: self.settings.set("fadeSeconds", float(v)))
        form.addRow("Annotations fade after", fade)
        column.addWidget(walkthrough)
        column.addStretch(1)
        return page

    def _transcription(self) -> QWidget:
        page = QWidget()
        form = QFormLayout(page)
        models = [m for m in WhisperModel]
        form.addRow(
            "Whisper model", _combo(models, self.settings.model, lambda v: self.settings.set("model", v))
        )
        form.addRow(
            "Run on", _combo(WhisperCompute, self.settings.compute, lambda v: self.settings.set("compute", v))
        )
        self._status = QLabel("")
        self._status.setWordWrap(True)
        form.addRow("Status", self._status)
        form.addRow(
            "",
            _note(
                "Runs fully on this computer with faster-whisper. Models live in {}. The NVIDIA GPU "
                "option needs CUDA 12 with cuDNN 9; without them the CPU is used.".format(paths.models())
            ),
        )
        vocabulary = QPlainTextEdit(self.settings.vocabulary)
        vocabulary.setFixedHeight(64)
        vocabulary.textChanged.connect(lambda: self.settings.set("vocabulary", vocabulary.toPlainText()))
        form.addRow("Custom vocabulary", vocabulary)
        form.addRow(
            "",
            _note(
                'Names, jargon and identifiers you say often (e.g. "PyTorch, SLURM, kubectl"). '
                "Passed to Whisper as a hint."
            ),
        )
        return page

    def _shortcuts(self) -> QWidget:
        page = QWidget()
        column = QVBoxLayout(page)
        form = QFormLayout()
        self._recorders: Dict[HotkeyAction, HotkeyRecorder] = {}
        self._hotkey_warnings: Dict[HotkeyAction, QLabel] = {}
        for action in HotkeyAction:
            recorder = HotkeyRecorder(self.settings.hotkey(action), self.hotkeys)
            recorder.recorded.connect(lambda hotkey, a=action: self.settings.set_hotkey(a, hotkey))
            warning = QLabel("")
            warning.setStyleSheet("color: #d9822b; font-size: 11px;")
            warning.setWordWrap(True)
            row = QVBoxLayout()
            row.setSpacing(2)
            row.addWidget(recorder)
            row.addWidget(warning)
            self._recorders[action], self._hotkey_warnings[action] = recorder, warning
            form.addRow(action.title, row)
        column.addLayout(form)
        reset = QPushButton("Reset to defaults")
        reset.clicked.connect(self._reset_hotkeys)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(reset)
        column.addLayout(row)
        self._hotkey_note = _note("")
        column.addWidget(self._hotkey_note)
        keys = QGroupBox("Snapshot overlay keys")
        inner = QVBoxLayout(keys)
        inner.addWidget(_note(_OVERLAY_KEYS))
        column.addWidget(keys)
        column.addStretch(1)
        return page

    def _reset_hotkeys(self) -> None:
        self.settings.reset_hotkeys()
        for action, recorder in self._recorders.items():
            recorder.set_hotkey(self.settings.hotkey(action))

    def set_hotkey_failures(self, failures: Dict[str, str], available: bool) -> None:
        """Shows which shortcuts could not be registered."""
        self.hotkey_failures = dict(failures)
        for action, label in self._hotkey_warnings.items():
            reason = failures.get(action.value, "") if available else ""
            label.setText(reason)
            label.setVisible(bool(reason))
        if available:
            self._hotkey_note.setText("")
        else:
            triggers = ", ".join("voice-annotate {}".format(a.command) for a in HotkeyAction)
            self._hotkey_note.setText(
                "Global shortcuts are not available in this session. Bind keys in your desktop's keyboard "
                "settings to these commands instead: {}.".format(triggers)
            )

    def _refresh_status(self) -> None:
        self._status.setText(self.transcriber.status.title)


class OnboardingWindow(QWidget):
    """Welcome, setup check and help in one window."""

    def __init__(
        self,
        settings: AppSettings,
        transcriber: Transcriber,
        platform: Platform,
        hotkeys_available: bool,
        tray_available: bool,
    ):
        super().__init__()
        self.settings, self.transcriber, self.platform = settings, transcriber, platform
        self.hotkeys_available = hotkeys_available
        self.on_action: Callable[[HotkeyAction], None] = lambda action: None
        self.on_show_settings: Callable[[], None] = lambda: None
        self.setWindowTitle("Welcome to voice-annotate")
        self.setWindowIcon(icons.app_icon())
        self.setMinimumWidth(580)
        column = QVBoxLayout(self)
        column.setSpacing(14)

        header = QHBoxLayout()
        logo = QLabel()
        logo.setPixmap(icons.app_pixmap(48))
        header.addWidget(logo)
        titles = QVBoxLayout()
        title = QLabel("voice-annotate")
        title.setStyleSheet("font-size: 18px; font-weight: 700;")
        titles.addWidget(title)
        titles.addWidget(QLabel("Talk, point and draw on your screen. Paste the result into any agent."))
        header.addLayout(titles, 1)
        column.addLayout(header)

        setup = QGroupBox("Setup")
        form = QFormLayout(setup)
        self._mic = QLabel("")
        self._mic.setWordWrap(True)
        form.addRow("Microphone", self._mic)
        self._model = QLabel("")
        self._model.setWordWrap(True)
        form.addRow("Transcription", self._model)
        column.addWidget(setup)

        shortcuts = QGroupBox("Shortcuts" if hotkeys_available else "Start a capture")
        self._shortcut_form = QFormLayout(shortcuts)
        self._shortcut_labels: List[QLabel] = []
        column.addWidget(shortcuts)
        self._build_shortcuts()

        notes = list(platform.notes())
        if not tray_available:
            notes.append(
                "No system tray was found (on GNOME, install the AppIndicator extension). Keep this window "
                "open to start captures with the buttons above and the shortcuts."
            )
        if notes:
            box = QGroupBox("Good to know")
            inner = QVBoxLayout(box)
            for text in notes:
                inner.addWidget(_note(text))
            column.addWidget(box)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        settings_button = QPushButton("Settings…")
        settings_button.clicked.connect(lambda: self.on_show_settings())
        buttons.addWidget(settings_button)
        column.addLayout(buttons)

        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self.refresh)
        settings.subscribe(
            lambda key: self._build_shortcuts() if key in ("hotkeys", "drawModifier") else None
        )

    def _build_shortcuts(self) -> None:
        form = self._shortcut_form
        while form.rowCount():
            form.removeRow(0)
        s = self.settings
        draw = s.draw_modifier
        if draw == DrawModifier.OFF or not self.platform.tracks_input:
            how = "Toggle draw mode with {} or the toolbar.".format(
                s.hotkey(HotkeyAction.TOGGLE_DRAW).display
                if self.hotkeys_available
                else "voice-annotate toggle-draw"
            )
        else:
            how = "{} to draw.".format(draw.title)
        rows = (
            (
                HotkeyAction.SNAPSHOT,
                "Snapshot: freeze the screen, draw and talk. Press again (or Enter) to finish, Tab for another shot.",
            ),
            (
                HotkeyAction.WALKTHROUGH,
                "Walkthrough: record while you use the computer. {} Press again to finish.".format(how),
            ),
            (HotkeyAction.DICTATION, "Dictation: voice only, the transcript is copied as plain text."),
            (HotkeyAction.FINISH, "Finish any capture and copy it to the clipboard."),
        )
        for action, text in rows:
            description = QLabel(text)
            description.setWordWrap(True)
            if self.hotkeys_available:
                key = QLabel(s.hotkey(action).display)
                key.setStyleSheet("font-family: monospace; font-weight: 600;")
                form.addRow(key, description)
            else:
                button = QPushButton(action.command.capitalize())
                button.setToolTip("voice-annotate {}".format(action.command))
                button.clicked.connect(lambda _=False, a=action: self.on_action(a))
                form.addRow(button, description)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.refresh()
        self._timer.start()

    def hideEvent(self, event) -> None:
        self._timer.stop()
        super().hideEvent(event)

    def refresh(self) -> None:
        name = input_device_name()
        if name:
            self._mic.setText("✓ {}".format(name))
        else:
            hint = "No microphone found. Captures still work, without a transcript."
            if self.platform.name == "windows":
                hint += " Check Settings → Privacy & security → Microphone → Let desktop apps access your microphone."
            elif self.platform.name in ("x11", "wayland"):
                hint += " If one is connected, install PortAudio: sudo apt install libportaudio2"
            self._mic.setText(hint)
        self._model.setText(self.transcriber.status.title)

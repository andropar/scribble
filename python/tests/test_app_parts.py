"""Settings, storage, microphone buffer, triggers and the windows, all headless."""

import json
import sys
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from voice_annotate import audio, ipc, paths, sounds, transcriber, video
from voice_annotate.clipboard import RecentsStore, transcript_text
from voice_annotate.core.models import CaptureMode
from voice_annotate.processor import RecentCapture
from voice_annotate.settings import (
    AppSettings,
    ClipboardFormat,
    DrawModifier,
    Hotkey,
    HotkeyAction,
    WhisperCompute,
    WhisperModel,
)
from voice_annotate.ui.windows import (
    HotkeyRecorder,
    OnboardingWindow,
    SettingsWindow,
    hotkey_from_event,
    key_name,
)

sys.path.insert(0, str(Path(__file__).parent))
from fakes import FakePlatform, pump, wait_until  # noqa: E402


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("VOICE_ANNOTATE_HOME", str(tmp_path / "home"))
    return tmp_path / "home"


# MARK: settings


def test_settings_defaults_and_persistence(home):
    s = AppSettings()
    assert s.model == WhisperModel.AUTO and s.compute == WhisperCompute.AUTO
    assert s.max_frames == 30 and s.image_long_edge == 1568 and s.fade_seconds == 3.0
    assert s.clipboard_format == ClipboardFormat.MARKDOWN_FILE and s.retention_days == 14
    assert s.hotkey(HotkeyAction.SNAPSHOT).display == "Ctrl+Alt+Shift+S"
    assert s.hotkey(HotkeyAction.WALKTHROUGH).key == "W"

    seen = []
    s.subscribe(seen.append)
    s.set("maxFrames", 12)
    s.set("clipboardFormat", ClipboardFormat.UNIVERSAL)
    s.set_hotkey(HotkeyAction.FINISH, Hotkey(("super", "ctrl"), "F9"))
    s.set("maxFrames", 12)  # unchanged: no notification
    assert seen == ["maxFrames", "clipboardFormat", "hotkeys"]

    again = AppSettings()
    assert again.max_frames == 12 and again.clipboard_format == ClipboardFormat.UNIVERSAL
    assert again.hotkey(HotkeyAction.FINISH) == Hotkey(("ctrl", "super"), "F9")
    again.reset_hotkeys()
    assert AppSettings().hotkey(HotkeyAction.FINISH) == HotkeyAction.FINISH.default_hotkey


def test_settings_reject_bad_values_and_survive_a_broken_file(home):
    s = AppSettings()
    with pytest.raises(ValueError):
        s.set("maxFrames", 1000)
    with pytest.raises(ValueError):
        s.set("model", "gigantic")
    with pytest.raises(KeyError):
        s.set("nope", 1)
    paths.ensure()
    paths.settings_file().write_text(
        '{"maxFrames": "many", "fadeSeconds": 2.5, "unknown": 1', encoding="utf-8"
    )
    assert AppSettings().max_frames == 30  # unreadable file: defaults
    paths.settings_file().write_text(json.dumps({"maxFrames": "many", "fadeSeconds": 2.5}), encoding="utf-8")
    partly = AppSettings()
    assert partly.max_frames == 30 and partly.fade_seconds == 2.5


def test_hotkey_display_and_draw_modifier():
    assert Hotkey(("shift", "ctrl"), "S").display == "Ctrl+Shift+S"
    assert Hotkey.from_json(Hotkey(("alt",), "F5").to_json()) == Hotkey(("alt",), "F5")
    assert DrawModifier.CTRL_ALT.matches({"ctrl", "alt"})
    assert not DrawModifier.CTRL_ALT.matches({"ctrl"})
    assert not DrawModifier.CTRL_ALT.matches({"ctrl", "alt", "shift"})
    assert not DrawModifier.OFF.matches(set())
    assert DrawModifier.OFF.symbol == "" and DrawModifier.CTRL_SHIFT.title == "Hold Ctrl+Shift"
    assert HotkeyAction.TOGGLE_DRAW.command == "toggle-draw" and HotkeyAction.SNAPSHOT.command == "snapshot"


# MARK: storage


def test_capture_folders_and_pruning(home):
    first = paths.new_capture_folder(datetime(2026, 11, 2, 14, 3, 12))
    second = paths.new_capture_folder(datetime(2026, 11, 2, 14, 3, 12))
    assert first.name == "2026-11-02_14-03-12" and second.name == "2026-11-02_14-03-12-2"
    old = paths.new_capture_folder(datetime(2026, 10, 1, 9, 0, 0))
    (old / "capture.md").write_text("x", encoding="utf-8")
    (paths.captures() / ".hidden").mkdir()
    now = datetime(2026, 11, 10, 12, 0, 0)
    assert paths.prune(14, now=now) == 1
    assert not old.exists() and first.exists() and second.exists()
    assert paths.prune(0, now=now) == 0
    assert paths.prune(3, now=now) == 2 and (paths.captures() / ".hidden").exists()


def capture_in(folder: Path) -> RecentCapture:
    folder.mkdir(parents=True, exist_ok=True)
    md = folder / "capture.md"
    md.write_text("# x\n\n## Transcript\n\n**[00:00 · frame 1]** hello\n\nFolder: /x\n", encoding="utf-8")
    return RecentCapture(
        str(folder), datetime.now().astimezone(), CaptureMode.SNAPSHOT, 3.0, str(md), [], [], 1, 0
    )


def test_recents_store(home):
    store = RecentsStore()
    assert store.items == []
    changes = []
    store.subscribe(lambda: changes.append(1))
    captures = [capture_in(home / "c{}".format(i)) for i in range(12)]
    for c in captures:
        store.add(c)
    assert len(store.items) == RecentsStore.MAX_ITEMS and store.items[0].folder == captures[-1].folder
    store.add(captures[5])  # copying an old one again moves it to the top, without a duplicate
    assert store.items[0].folder == captures[5].folder and len(store.items) == RecentsStore.MAX_ITEMS
    assert changes

    Path(captures[-1].markdown_path).unlink()
    reloaded = RecentsStore()
    assert captures[-1].folder not in [c.folder for c in reloaded.items]
    assert reloaded.items[0].date.tzinfo is not None
    paths.recents_file().write_text("not json", encoding="utf-8")
    assert RecentsStore().items == []


def test_transcript_text_strips_markdown():
    md = "# t\n\nintro\n\n## Transcript\n\n**[00:00 · frame 1]** hello [#1 pin]\n\n## Annotations\n\n- #1 pin\n\nFolder: /x\n"
    assert transcript_text(md) == "[00:00 · frame 1] hello [#1 pin]\n\nAnnotations\n\n- #1 pin"


# MARK: microphone


class FakeStream:
    """Feeds scripted buffers through the recorder's callback, like PortAudio would."""

    def __init__(self, callback, rate, channels=1):
        self.callback, self.samplerate, self.channels = callback, rate, channels
        self.closed = False

    def feed(self, seconds, level=0.2, latency=0.02):
        frames = int(seconds * self.samplerate)
        data = np.full((frames, self.channels), level, dtype=np.float32)

        class Info:
            currentTime = 100.0
            inputBufferAdcTime = 100.0 - latency

        self.callback(data, frames, Info, None)

    def stop(self):
        pass

    def close(self):
        self.closed = True


def test_audio_recorder_resamples_and_timestamps():
    holder = {}

    def opener(callback):
        holder["stream"] = FakeStream(callback, 48_000, channels=2)
        return holder["stream"]

    recorder = audio.AudioRecorder(open_stream=opener)
    levels = []
    recorder.on_level = levels.append
    before = paths.now()
    recorder.start()
    holder["stream"].feed(0.5)
    holder["stream"].feed(0.5)
    result = recorder.stop()
    assert holder["stream"].closed
    # 1 s at 48 kHz stereo became ~1 s of 16 kHz mono; the first sample is stamped with the
    # buffer's capture time (now minus the input latency).
    assert abs(len(result.samples) - 16_000) < 400
    assert before - 0.05 <= result.first_sample_time <= paths.now()
    assert result.duration == pytest.approx(1.0, abs=0.03)
    assert levels and 0.5 < levels[0] <= 1.0


def test_audio_recorder_fills_gaps_with_silence():
    holder = {}

    def opener(callback):
        holder["stream"] = FakeStream(callback, 16_000)
        return holder["stream"]

    recorder = audio.AudioRecorder(open_stream=opener)
    recorder.start()
    holder["stream"].feed(0.2)
    time.sleep(0.6)  # the device delivered nothing for a while
    holder["stream"].feed(0.2)
    result = recorder.stop()
    # 0.2 s of audio, ~0.4 s of silence for the missing stretch, 0.2 s of audio: the second
    # buffer sits 0.6 s after the first, where it was really captured.
    assert 0.75 < result.duration < 0.95
    assert np.count_nonzero(result.samples == 0) > 0.3 * 16_000


def test_audio_recorder_reports_missing_microphone():
    def opener(callback):
        raise audio.MicrophoneError("No microphone input available")

    with pytest.raises(audio.MicrophoneError):
        audio.AudioRecorder(open_stream=opener).start()
    assert audio.level_from_rms(0) == 0 and audio.level_from_rms(1.0) == 1.0
    assert 0.3 < audio.level_from_rms(0.02) < 0.6


def test_sound_cues_are_short_and_quiet():
    for name in ("start", "stop", "done", "error"):
        wave = sounds.waveform(name)
        assert 0.05 < len(wave) / 44_100 < 0.4 and float(np.abs(wave).max()) <= 0.2
        assert abs(float(wave[0])) < 0.01 and abs(float(wave[-1])) < 0.01  # no clicks


# MARK: transcriber helpers


def test_noise_words_and_text_cleaning():
    assert (
        transcriber.is_noise(" ")
        and transcriber.is_noise("[BLANK_AUDIO]")
        and transcriber.is_noise("(music)")
    )
    assert transcriber.is_noise("<|endoftext|>")
    assert not transcriber.is_noise(" hello") and not transcriber.is_noise("()")
    assert transcriber.clean_text(" <|0.00|> Hello there.<|2.00|> ") == "Hello there."
    assert transcriber.resolve_model(WhisperModel.AUTO, "cpu") == "small.en"
    assert transcriber.resolve_model(WhisperModel.AUTO, "cuda") == "large-v3-turbo"
    assert transcriber.resolve_model(WhisperModel.BASE_EN, "cuda") == "base.en"


def test_convert_orders_words_and_drops_noise():
    class W:
        def __init__(self, word, start, end):
            self.word, self.start, self.end = word, start, end

    class S:
        def __init__(self, text, start, end, words):
            self.text, self.start, self.end, self.words = text, start, end, words

    out = transcriber.convert(
        [
            S(" second part", 3.0, 4.0, [W(" second", 3.0, 3.4), W(" part", 3.4, 4.0)]),
            S(" First", 0.0, 1.0, [W(" First", 0.2, 0.8), W(" [BLANK_AUDIO]", 0.8, 1.0)]),
            S("  ", 5.0, 5.5, None),
        ]
    )
    assert [w.text for w in out.words] == [" First", " second", " part"]
    assert out.plain_text == "First second part"


def test_silence_is_not_sent_to_whisper(tmp_path):
    whisper = transcriber.Transcriber(models_dir=tmp_path)
    quiet = np.zeros(16_000 * 3, dtype=np.float32)
    assert whisper.transcribe(quiet, WhisperModel.TINY_EN, WhisperCompute.CPU).words == []
    assert whisper.status.kind == "idle"  # the model was not even loaded
    assert transcriber.Status("downloading", 0.41).title == "Downloading model… 41%"
    assert transcriber.Status("ready", device="cpu", model="small.en").is_ready


# MARK: triggers


def test_trigger_server_round_trip(qapp):
    """A second process (what ``voice-annotate snapshot`` is) reaches the running one."""
    import subprocess

    name = "voice-annotate-test-{}".format(int(time.time() * 1000))
    seen = []

    def handle(command):
        seen.append(command)
        if command == "boom":
            raise RuntimeError("nope")
        return "ok idle"

    server = ipc.TriggerServer(handle, name=name)
    assert server.listen()
    client = (
        "import sys; from voice_annotate import ipc; "
        "print(repr([ipc.send(c, name=sys.argv[1]) for c in ('snapshot', 'boom')]))"
    )
    process = subprocess.Popen([sys.executable, "-c", client, name], stdout=subprocess.PIPE, text=True)
    assert wait_until(lambda: process.poll() is not None, 20)
    assert process.stdout.read().strip() == repr(["ok idle", "error: nope"])
    assert seen == ["snapshot", "boom"]
    server.close()
    # Nobody listening: the caller is told there is no running instance.
    assert ipc.send("status", name=name) is None
    assert "snapshot" in ipc.COMMANDS and "toggle-draw" in ipc.COMMANDS


# MARK: windows


def test_key_names_and_hotkey_rules():
    assert key_name(Qt.Key_S) == "S" and key_name(Qt.Key_7) == "7" and key_name(Qt.Key_F5) == "F5"
    assert key_name(Qt.Key_Space) == "Space" and key_name(Qt.Key_Control) is None
    ctrl_alt = Qt.ControlModifier | Qt.AltModifier
    assert hotkey_from_event(Qt.Key_S, ctrl_alt) == Hotkey(("ctrl", "alt"), "S")
    # A bare key or Shift+key would swallow normal typing.
    assert hotkey_from_event(Qt.Key_S, Qt.NoModifier) is None
    assert hotkey_from_event(Qt.Key_S, Qt.ShiftModifier) is None
    assert hotkey_from_event(Qt.Key_Control, ctrl_alt) is None


def test_key_names_map_to_native_codes():
    # Every key the recorder can produce must be registrable on both platforms.
    from voice_annotate.platform import x11

    names = ["S", "7", "F5", "F12", "Space", "Tab", "Return", "Left", "PageDown", "Delete"]
    assert [x11.keysym_name(n) for n in names] == [
        "s",
        "7",
        "F5",
        "F12",
        "space",
        "Tab",
        "Return",
        "Left",
        "Next",
        "Delete",
    ]
    from Xlib import XK

    assert all(XK.string_to_keysym(x11.keysym_name(n)) for n in names)
    if sys.platform == "win32":
        from voice_annotate.platform import windows

        assert windows.virtual_key("S") == 0x53 and windows.virtual_key("F5") == 0x74
        assert all(windows.virtual_key(n) for n in names)


class FakeBackend:
    def __init__(self):
        self.calls = []

    def suspend(self):
        self.calls.append("suspend")

    def resume(self):
        self.calls.append("resume")


def test_hotkey_recorder_records_a_combination(qapp):
    backend = FakeBackend()
    recorder = HotkeyRecorder(Hotkey(("ctrl", "alt", "shift"), "S"), backend)
    recorded = []
    recorder.recorded.connect(recorded.append)
    recorder.show()
    recorder.click()
    assert recorder.text() == "Press shortcut…" and backend.calls == ["suspend"]
    QTest.keyClick(recorder, Qt.Key_X)  # no modifier: ignored, still recording
    assert recorder.text() == "Press shortcut…"
    QTest.keyClick(recorder, Qt.Key_G, Qt.ControlModifier | Qt.ShiftModifier)
    assert recorded == [Hotkey(("ctrl", "shift"), "G")]
    assert recorder.text() == "Ctrl+Shift+G" and backend.calls == ["suspend", "resume"]
    recorder.click()
    QTest.keyClick(recorder, Qt.Key_Escape)  # Esc keeps the old shortcut
    assert recorder.text() == "Ctrl+Shift+G" and len(recorded) == 1
    recorder.hide()


def test_settings_window_edits_settings(qapp, home, tmp_path):
    settings, platform = AppSettings(), FakePlatform()
    whisper = transcriber.Transcriber(models_dir=tmp_path / "models")
    window = SettingsWindow(settings, whisper, platform, None)
    window.show()
    pump(0.05)
    from PySide6.QtWidgets import QCheckBox, QComboBox, QSpinBox

    checks = {c.text(): c for c in window.findChildren(QCheckBox)}
    checks["Play sounds"].setChecked(False)
    assert settings.play_sounds is False
    checks["Launch at login"].setChecked(True)
    assert platform.autostart is True
    spin = next(s for s in window.findChildren(QSpinBox) if s.suffix() == " days")
    spin.setValue(30)
    assert settings.retention_days == 30
    combo = next(c for c in window.findChildren(QComboBox) if c.findData(ClipboardFormat.COMPOSITE) >= 0)
    combo.setCurrentIndex(combo.findData(ClipboardFormat.UNIVERSAL))
    assert settings.clipboard_format == ClipboardFormat.UNIVERSAL
    window.set_hotkey_failures({"snapshot": "Ctrl+Alt+Shift+S is already used"}, True)
    assert window._hotkey_warnings[HotkeyAction.SNAPSHOT].text() == "Ctrl+Alt+Shift+S is already used"
    window.set_hotkey_failures({}, False)
    assert "voice-annotate snapshot" in window._hotkey_note.text()
    assert AppSettings().retention_days == 30  # saved to disk
    window.close()


def test_onboarding_window_adapts_to_the_session(qapp, home, tmp_path):
    from PySide6.QtWidgets import QPushButton

    settings, platform = AppSettings(), FakePlatform()
    whisper = transcriber.Transcriber(models_dir=tmp_path / "models")
    full = OnboardingWindow(settings, whisper, platform, hotkeys_available=True, tray_available=True)
    full.show()
    pump(0.05)
    assert not [b for b in full.findChildren(QPushButton) if b.text() == "Snapshot"]
    full.close()

    # No global hotkeys and no tray: buttons start the captures instead.
    reduced = OnboardingWindow(settings, whisper, platform, hotkeys_available=False, tray_available=False)
    started = []
    reduced.on_action = started.append
    reduced.show()
    pump(0.05)
    next(b for b in reduced.findChildren(QPushButton) if b.text() == "Snapshot").click()
    assert started == [HotkeyAction.SNAPSHOT]
    reduced.close()

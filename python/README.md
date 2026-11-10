# voice-annotate for Windows and Linux

The port of the macOS app. What it does and how to install it is in the
[main README](../README.md#windows-and-linux); how it relates to the macOS app is in
[SPEC.md §11](../SPEC.md#11-windows-and-linux). This file is for working on it.

## Layout

```
src/voice_annotate/
  core/          models, geometry, keyframe selection, markdown: a port of VoiceAnnotateCore,
                 no Qt and no I/O
  render.py      annotations with QPainter (live overlay and saved frames share it)
  video.py       PyAV: recording, frame extraction, audio files, mux
  audio.py       microphone → 16 kHz mono on the session clock
  transcriber.py faster-whisper, model download, CUDA probe
  processor.py   a finished capture → frames, capture.md, events.json
  screens.py     displays, screen grabbing (mss; the desktop's tools on Wayland)
  recorder.py    one thread per display: grab, encode, thumbnail
  session.py     SessionController: the capture state machine
  platform/      what Qt does not cover: hotkeys, input state, click-through, focus, autostart
                 (windows.py: ctypes, x11.py: python-xlib)
  ui/            canvas, overlays, capture toolbar and toasts, tray, windows
  sounds.py      start/stop/done cues, played by the system
  ipc.py         single instance and the command line triggers
  app.py, cli.py entry point and developer commands
packaging/       PyInstaller spec, build script and a smoke test for the built app
```

## Run and test

```bash
pip install -e ".[dev]"
python -m voice_annotate            # the app, from source
python -m voice_annotate --help     # triggers and developer commands
pytest                              # everything that needs no display
```

`pytest` covers the core (the Swift tests, ported), parity with the macOS app's output, the
pipeline on synthetic recordings, and the capture state machine driven through the real
overlays with a fake screen and microphone.

Two more suites are opt-in:

```bash
# A real Whisper model (tiny.en, ~75 MB) on a recording of the sentence in tests/test_model.py
VOICE_ANNOTATE_TEST_MODEL=1 VOICE_ANNOTATE_SPEECH=speech.wav pytest -m model

# A real desktop session, driven with synthetic mouse and keyboard input
pytest --desktop tests/integration          # Windows
tests/integration/run_x11.sh                # Linux: Xvfb + openbox (COMPOSITOR=1 adds picom)
```

The desktop tests move the pointer, press keys and open windows: run them in a VM or under
Xvfb, not in the session you are working in. CI (`.github/workflows/desktop.yml`) runs all
three suites on Windows and Ubuntu and uploads screenshots of what was on screen.

Several displays: `tests/test_multi_display.py` runs the capture flows on two emulated
displays, the second at 200% (Qt's offscreen platform can be given a screen layout).
Display scaling on a real session: `SCREEN=2880x1620x24 QT_SCALE_FACTOR=1.5
tests/integration/run_x11.sh` is a 1920×1080 desktop at 150%.

## Self-contained build

For people without Python: a folder with everything in it, made with PyInstaller.

```bash
pip install -e . pyinstaller
python packaging/build.py                                  # → dist/voice-annotate/ and an archive
python packaging/smoke_test.py dist/voice-annotate speech.wav
```

It has two launchers over the same files: `voice-annotate` (the app, no console window) and
`voice-annotate-cli` (the triggers and developer commands, with output). The smoke test
runs the commands, the whole Snapshot pipeline on a speech recording, and starts and quits
the app. CI builds and tries it on Windows and Linux and keeps the archives as artifacts.

## Debugging

- Log: `~/.voice-annotate/voice-annotate.log` (and stderr).
- `VOICE_ANNOTATE_HOME=/some/folder` moves settings, models and captures (tests use it).
- `VOICE_ANNOTATE_WATCHDOG=30` writes every thread's stack to stderr every 30 seconds;
  on Linux `kill -USR1 <pid>` does it once.
- `voice-annotate --list-displays` prints the displays, their scaling and the backend in use.

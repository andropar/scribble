"""Entry point: the tray app, command line triggers and developer commands."""

from __future__ import annotations

import ctypes.util
import logging
import logging.handlers
import os
import signal
import sys
from typing import Dict, List, Optional

from . import __version__, paths

log = logging.getLogger(__name__)

USAGE = """scribble {version}

  scribble                 Start the app (tray icon and global shortcuts)
  scribble <command>       Tell the running app to act; bind these to desktop shortcuts:
      snapshot | walkthrough | dictation | finish | toggle-draw | cancel
      settings | help | status | quit

Developer commands:
  --transcribe <audio-file> [model]      Transcribe a file, print word timings
  --render-demo <out.png>                Render every annotation type onto a test image
  --demo-snapshot <audio-file> [screenshot.png annotations.json]
                                         Full Snapshot pipeline on a synthetic (or given @2x) screenshot
  --test-screenshot [screen]             Screenshot a display, run the pipeline
  --test-walkthrough <seconds> [screen|all]  Record display(s), run the pipeline
  --list-displays                        Print the displays as the app sees them
  --version
"""


def setup_logging(verbose: bool = False) -> None:
    root = logging.getLogger()
    if root.handlers:
        return
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    stream = logging.StreamHandler(sys.stderr)
    stream.setFormatter(formatter)
    root.addHandler(stream)
    try:
        paths.ensure()
        file = logging.handlers.RotatingFileHandler(
            paths.log_file(), maxBytes=1_000_000, backupCount=1, encoding="utf-8"
        )
        file.setFormatter(formatter)
        root.addHandler(file)
    except OSError:
        pass
    for noisy in ("httpx", "httpx2", "httpcore", "huggingface_hub", "faster_whisper", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def _check_linux_libraries() -> None:
    """Qt's X11 plugin aborts with a cryptic message when this library is missing."""
    if sys.platform.startswith("linux") and os.environ.get("QT_QPA_PLATFORM", "xcb") == "xcb":
        if os.environ.get("DISPLAY") and ctypes.util.find_library("xcb-cursor") is None:
            sys.stderr.write(
                "Scribble needs the xcb-cursor library for its windows. Install it with:\n"
                "  sudo apt install libxcb-cursor0      (Debian, Ubuntu)\n"
                "  sudo dnf install xcb-util-cursor     (Fedora)\n"
            )


def _enable_diagnostics() -> None:
    """Crash and hang diagnostics on stderr: a traceback on fatal errors, all thread stacks
    on SIGUSR1 (Unix), and every ``SCRIBBLE_WATCHDOG`` seconds if that is set."""
    import faulthandler

    if sys.stderr is None:
        return
    try:
        faulthandler.enable()
        if hasattr(signal, "SIGUSR1"):
            faulthandler.register(signal.SIGUSR1, all_threads=True)
        interval = float(os.environ.get("SCRIBBLE_WATCHDOG", "0") or 0)
        if interval > 0:
            faulthandler.dump_traceback_later(interval, repeat=True)
    except (OSError, ValueError, RuntimeError):
        pass


def trigger(command: str) -> int:
    from . import ipc

    if sys.platform == "win32":
        # Let the running app take the foreground on behalf of this (user-started) process.
        from .platform.windows import allow_set_foreground

        allow_set_foreground()
    reply = ipc.send(command)
    if reply is None:
        sys.stderr.write("Scribble is not running. Start it first (run scribble without arguments).\n")
        return 1
    sys.stdout.write(reply + "\n")
    return 0 if not reply.startswith("error") else 1


def run_app(argv: List[str]) -> int:
    from . import ipc
    from .platform import prepare_environment

    setup_logging("--verbose" in argv)
    _enable_diagnostics()
    prepare_environment()
    _check_linux_libraries()

    from PySide6.QtCore import Qt, QTimer
    from PySide6.QtWidgets import QApplication

    from . import clipboard, platform as platforms, screens, sounds, transcriber
    from .session import SessionController
    from .settings import AppSettings, HotkeyAction
    from .ui import icons
    from .ui.tray import TrayController
    from .ui.windows import OnboardingWindow, SettingsWindow

    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(argv[:1] or ["scribble"])
    app.setApplicationName("scribble")
    app.setApplicationDisplayName("Scribble")
    app.setQuitOnLastWindowClosed(False)
    app.setWindowIcon(icons.app_icon())
    if ipc.send("settings") is not None:
        sys.stderr.write("Scribble is already running (its Settings window was opened).\n")
        return 0
    paths.ensure()

    settings = AppSettings()
    platform = platforms.current()
    platform.autostart_enabled()  # also replaces an entry made under the app's old name
    grabber = screens.create_grabber(platform)
    recents = clipboard.RecentsStore()
    whisper = transcriber.shared()
    sounds.enabled = settings.play_sounds
    controller = SessionController(settings, recents, whisper, platform, grabber)
    hotkeys = platform.create_hotkeys()
    tray = TrayController(controller, recents, settings, whisper, platform)
    tray.hotkeys_available = hotkeys.available
    log.info(
        "Scribble %s started: platform %s, grabber %s, tray %s, hotkeys %s",
        __version__,
        platform.name,
        type(grabber).__name__,
        tray.available,
        hotkeys.available,
    )

    windows: Dict[str, object] = {}

    def show(window) -> None:
        window.show()
        window.raise_()
        window.activateWindow()

    def show_settings() -> None:
        if "settings" not in windows:
            windows["settings"] = SettingsWindow(settings, whisper, platform, hotkeys)
            windows["settings"].set_hotkey_failures(tray.hotkey_failures, hotkeys.available)
        show(windows["settings"])

    def show_help() -> None:
        if "help" not in windows:
            window = OnboardingWindow(settings, whisper, platform, hotkeys.available, tray.available)
            window.on_action = controller.handle
            window.on_show_settings = show_settings
            windows["help"] = window
        show(windows["help"])

    def register_hotkeys() -> None:
        failures = hotkeys.set_hotkeys({a.value: h for a, h in settings.hotkeys().items()})
        tray.hotkey_failures = failures if hotkeys.available else {}
        if "settings" in windows:
            windows["settings"].set_hotkey_failures(failures, hotkeys.available)
        if failures and hotkeys.available:
            first = next(iter(failures.values()))
            controller.toast.show_message("Shortcut unavailable", first, success=False, duration=5)

    def on_setting(key: str) -> None:
        if key == "hotkeys":
            register_hotkeys()
        elif key in ("model", "compute"):
            whisper.preload(settings.model, settings.compute)
        elif key == "playSounds":
            sounds.enabled = settings.play_sounds

    def on_command(command: str) -> str:
        actions = {a.command: a for a in HotkeyAction}
        if command in actions:
            # Deferred, so the trigger process gets its reply before any modal work starts.
            QTimer.singleShot(0, lambda: controller.handle(actions[command]))
        elif command == "cancel":
            QTimer.singleShot(0, controller.cancel)
        elif command == "settings":
            QTimer.singleShot(0, show_settings)
        elif command == "help":
            QTimer.singleShot(0, show_help)
        elif command == "quit":
            QTimer.singleShot(0, app.quit)
        elif command != "status":
            return "error: unknown command {}".format(command)
        return "ok {}".format(controller.phase.value)

    hotkeys.triggered.connect(lambda name: controller.handle(HotkeyAction(name)))
    settings.subscribe(on_setting)
    tray.on_show_settings = show_settings
    tray.on_show_help = show_help
    tray.on_retry_model = lambda: whisper.preload(settings.model, settings.compute)
    controller.on_needs_setup = show_help
    register_hotkeys()

    server = ipc.TriggerServer(on_command)
    server.listen()

    whisper.preload(settings.model, settings.compute)
    paths.prune(settings.retention_days)
    prune_timer = QTimer()
    prune_timer.timeout.connect(lambda: paths.prune(settings.retention_days))
    prune_timer.start(6 * 3600 * 1000)

    if not settings.onboarding_shown or not tray.available:
        settings.set("onboardingShown", True)
        show_help()

    # Let Ctrl+C in a terminal quit the app.
    signal.signal(signal.SIGINT, lambda *_: app.quit())
    wake = QTimer()
    wake.timeout.connect(lambda: None)
    wake.start(250)

    code = app.exec()
    server.close()
    hotkeys.shutdown()
    finished = controller.wait_for_background(10)
    if not finished or whisper.is_loading:
        # A worker is still inside a native library (loading or downloading a model,
        # transcribing). A normal interpreter exit can hang on that, so leave directly,
        # after what an orderly shutdown would have done for the user.
        log.info("Quitting while background work is still running")
        platform.flush_clipboard()
        logging.shutdown()
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(code)
    return code


def configure_output() -> None:
    """Makes printing safe everywhere. A windowed build has no standard streams at all, and
    a pipe on Windows uses a legacy code page that cannot encode what capture.md contains
    (arrows, "×", "…"): piped output is UTF-8 instead, which is what its readers expect."""
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name)
        if stream is None:
            setattr(sys, name, open(os.devnull, "w", encoding="utf-8"))
            continue
        try:
            if stream.isatty():
                stream.reconfigure(errors="replace")
            else:
                stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass


def main(argv: Optional[List[str]] = None) -> int:
    configure_output()
    argv = list(sys.argv if argv is None else argv)
    args = argv[1:]
    if args and args[0] in ("--version", "-V"):
        sys.stdout.write("scribble {}\n".format(__version__))
        return 0
    if args and args[0] in ("--help", "-h"):
        sys.stdout.write(USAGE.format(version=__version__))
        return 0
    if args and args[0] == "--probe-cuda" and len(args) >= 2:
        from .transcriber import probe_cuda_main

        return probe_cuda_main(args[1])
    if args and not args[0].startswith("-"):
        from .ipc import COMMANDS

        if args[0] in COMMANDS:
            return trigger(args[0])
        sys.stderr.write("Unknown command {}\n\n{}".format(args[0], USAGE.format(version=__version__)))
        return 2
    if args and args[0].startswith("--") and args[0] != "--verbose":
        from . import cli

        return cli.run(args)
    if args and args != ["--verbose"]:
        # Never start the app on arguments that were not meant for it.
        sys.stderr.write(
            "Unknown arguments {}\n\n{}".format(" ".join(args), USAGE.format(version=__version__))
        )
        return 2
    return run_app(argv)


if __name__ == "__main__":
    sys.exit(main())

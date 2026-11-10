"""Checks a built app folder the way a user would use it.

    python packaging/smoke_test.py dist/voice-annotate [speech.wav]

Runs the console launcher's commands (version, rendering, and with a speech recording the
whole Snapshot pipeline including a Whisper model download), then starts the windowed app,
talks to it through the triggers and quits it. Needs a display (use xvfb-run on Linux).
"""

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path


def main() -> int:
    folder = Path(sys.argv[1]).resolve()
    speech = sys.argv[2] if len(sys.argv) > 2 else None
    suffix = ".exe" if sys.platform == "win32" else ""
    cli, app = folder / ("voice-annotate-cli" + suffix), folder / ("voice-annotate" + suffix)
    assert cli.is_file() and app.is_file(), "launchers missing in {}".format(folder)

    work = Path(tempfile.mkdtemp(prefix="voice-annotate-smoke-"))
    home = work / "home"
    home.mkdir()
    (home / "settings.json").write_text(
        json.dumps({"model": "tiny.en", "onboardingShown": True}), encoding="utf-8"
    )
    env = dict(os.environ, VOICE_ANNOTATE_HOME=str(home), PYTHONIOENCODING="utf-8")

    def run(*args, timeout=120, offscreen=False):
        environment = dict(env, QT_QPA_PLATFORM="offscreen") if offscreen else env
        result = subprocess.run(
            [str(cli)] + list(args),
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=environment,
        )
        sys.stdout.write(
            "$ voice-annotate-cli {}\n{}{}".format(" ".join(args), result.stdout, result.stderr[-1500:])
        )
        return result

    version = run("--version")
    assert version.returncode == 0 and version.stdout.startswith("voice-annotate "), version.stdout

    picture = work / "demo.png"
    rendered = run("--render-demo", str(picture), offscreen=True)
    assert rendered.returncode == 0 and picture.stat().st_size > 10_000
    assert "#1 arrow from (933,674) pointing at (726,435) in frame 1" in rendered.stdout

    if speech:
        snapshot = run("--demo-snapshot", speech, timeout=900, offscreen=True)
        assert snapshot.returncode == 0, snapshot.stderr[-3000:]
        assert "## Transcript" in snapshot.stdout and "#5 circled (" in snapshot.stdout
        heard = [w for w in ("button", "broken", "legend", "chart") if w in snapshot.stdout.lower()]
        assert len(heard) >= 2, "transcript looks wrong"
        captures = list((home / "captures").iterdir())
        assert len(captures) == 1 and (captures[0] / "frame_01.png").is_file()

    # The windowed app: start, reach it through the triggers, quit.
    process = subprocess.Popen([str(app)], env=env)
    try:
        deadline = time.time() + 90
        status = None
        while time.time() < deadline:
            status = run("status", timeout=30)
            if status.returncode == 0:
                break
            assert process.poll() is None, "the app exited with {}".format(process.returncode)
            time.sleep(1)
        assert status is not None and status.stdout.strip() == "ok idle", "the app did not answer"
        assert run("quit", timeout=30).returncode == 0
        assert process.wait(30) == 0
    finally:
        if process.poll() is None:
            process.kill()
    log = home / "voice-annotate.log"
    if log.is_file():
        sys.stdout.write("--- app log ---\n" + log.read_text(encoding="utf-8", errors="replace")[-3000:])
    sys.stdout.write("\nsmoke test passed\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())

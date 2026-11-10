"""Runs ``multi_display_cases.py`` in a child process with two emulated displays.

Qt's screens are fixed when the application object is created, so a different display
layout needs its own process. The offscreen platform reads the layout from a JSON file.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

# Geometry is in native pixels; the scale factor comes from the logical DPI, as it does on a
# real desktop. The second screen is 1920×1080 pixels at 200%: 960×540 canvas points.
SCREENS = {
    "screens": [
        {
            "name": "Laptop",
            "x": 0,
            "y": 0,
            "width": 1920,
            "height": 1080,
            "logicalDpi": 96,
            "logicalBaseDpi": 96,
        },
        {
            "name": "External",
            "x": 1920,
            "y": 0,
            "width": 1920,
            "height": 1080,
            "logicalDpi": 192,
            "logicalBaseDpi": 96,
        },
    ]
}


def test_captures_across_two_displays_with_mixed_scaling(tmp_path):
    config = tmp_path / "screens.json"
    config.write_text(json.dumps(SCREENS), encoding="utf-8")
    env = dict(
        os.environ,
        QT_QPA_PLATFORM="offscreen:configfile={}".format(config),
        # The offscreen platform crashes while tearing down its emulated screens at exit.
        VOICE_ANNOTATE_EXIT_AFTER_TESTS="1",
    )
    for name in ("QT_SCALE_FACTOR", "QT_SCREEN_SCALE_FACTORS"):
        env.pop(name, None)
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            "-o",
            "faulthandler_timeout=120",
            str(HERE / "multi_display_cases.py"),
        ],
        cwd=str(HERE.parent),
        env=env,
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert result.returncode == 0, (result.stdout + result.stderr)[-6000:]
    assert "4 passed" in result.stdout, result.stdout[-2000:]

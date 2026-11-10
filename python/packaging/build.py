"""Builds the self-contained app with PyInstaller.

    pip install -e . pyinstaller
    python packaging/build.py

Result: ``dist/voice-annotate/`` with ``voice-annotate`` (the app) and ``voice-annotate-cli``
(triggers and developer commands), plus an archive of that folder next to it.
"""

import os
import platform
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent


def windows_icon(target: Path) -> str:
    """Converts the PNG icon to .ico (PyInstaller wants that format on Windows)."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtGui import QGuiApplication, QImage

    app = QGuiApplication.instance() or QGuiApplication([])
    image = QImage(str(ROOT / "src" / "voice_annotate" / "resources" / "icon.png"))
    if image.isNull() or not image.save(str(target), "ICO"):
        raise RuntimeError("Could not write {}".format(target))
    del app
    return str(target)


def main() -> int:
    import PyInstaller.__main__

    sys.path.insert(0, str(ROOT / "src"))
    from voice_annotate import __version__

    dist, work = ROOT / "dist", ROOT / "build"
    if sys.platform == "win32":
        work.mkdir(exist_ok=True)
        os.environ["VOICE_ANNOTATE_ICON"] = windows_icon(work / "voice-annotate.ico")
    PyInstaller.__main__.run(
        [str(HERE / "voice-annotate.spec"), "--noconfirm", "--clean", "--distpath", str(dist), "--workpath", str(work)]
    )
    system = {"win32": "windows", "linux": "linux"}.get(sys.platform, sys.platform)
    name = "voice-annotate-{}-{}-{}".format(__version__, system, platform.machine().lower())
    archive = shutil.make_archive(
        str(dist / name), "zip" if sys.platform == "win32" else "gztar", root_dir=dist, base_dir="voice-annotate"
    )
    sys.stdout.write("built {}\n".format(archive))
    return 0


if __name__ == "__main__":
    sys.exit(main())

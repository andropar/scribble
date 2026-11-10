"""Builds the self-contained app with PyInstaller.

    pip install -e . pyinstaller
    python packaging/build.py

Result: ``dist/voice-annotate/`` with ``voice-annotate`` (the app) and ``voice-annotate-cli``
(triggers and developer commands). Next to it, on Windows a zip and (with Inno Setup
installed) ``voice-annotate-windows-setup.exe``; on Linux a tar.gz that carries ``install.sh``.
"""

import os
import platform
import shutil
import subprocess
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
        [
            str(HERE / "voice-annotate.spec"),
            "--noconfirm",
            "--clean",
            "--distpath",
            str(dist),
            "--workpath",
            str(work),
        ]
    )
    if sys.platform == "win32":
        archive = shutil.make_archive(str(dist / "voice-annotate-windows-x64"), "zip", dist, "voice-annotate")
        sys.stdout.write("built {}\n".format(archive))
        compiler = shutil.which("ISCC") or r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
        if os.path.isfile(compiler):
            subprocess.run(
                [compiler, "/Q", "/DAppVersion=" + __version__, str(HERE / "installer.iss")], check=True
            )
            sys.stdout.write("built {}\n".format(dist / "voice-annotate-windows-setup.exe"))
        else:
            sys.stdout.write("Inno Setup not found: no installer, only the zip\n")
    else:
        # The archive installs itself: unpack and run ./install.sh
        shutil.copy(HERE / "install-linux.sh", dist / "voice-annotate" / "install.sh")
        name = "voice-annotate-{}-{}".format(
            "linux" if sys.platform == "linux" else sys.platform, platform.machine().lower()
        )
        archive = shutil.make_archive(str(dist / name), "gztar", root_dir=dist, base_dir="voice-annotate")
        sys.stdout.write("built {}\n".format(archive))
    return 0


if __name__ == "__main__":
    sys.exit(main())

# PyInstaller spec: a self-contained folder for people without Python.
# Build with `python packaging/build.py` (it sets SCRIBBLE_ICON on Windows).
import os
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

root = Path(SPECPATH).parent
src = root / "src"

datas = [(str(src / "scribble" / "resources"), "scribble/resources")]
datas += collect_data_files("faster_whisper")  # the voice activity detection model

hidden = []
if sys.platform.startswith("linux"):
    hidden += collect_submodules("Xlib")  # python-xlib loads its extensions by name

a = Analysis(
    [str(Path(SPECPATH) / "entry.py")],
    pathex=[str(src)],
    datas=datas,
    hiddenimports=hidden,
    excludes=["tkinter", "matplotlib", "PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtPdf"],
)
pyz = PYZ(a.pure)
icon = os.environ.get("SCRIBBLE_ICON") or None

# Two launchers over the same files: the app without a console window, and a console
# program for the command line triggers and developer commands.
app = EXE(pyz, a.scripts, [], exclude_binaries=True, name="scribble", console=False, icon=icon, upx=False)
cli = EXE(pyz, a.scripts, [], exclude_binaries=True, name="scribble-cli", console=True, icon=icon, upx=False)
COLLECT(app, cli, a.binaries, a.datas, name="scribble", upx=False)

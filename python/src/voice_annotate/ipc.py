"""One running instance, and command line triggers that talk to it.

``voice-annotate snapshot`` (and friends) ask the running app to act. That makes every
action bindable to a desktop shortcut, which is how hotkeys work on Wayland.
"""

from __future__ import annotations

import getpass
import logging
import re
import time
from typing import Callable, Optional

from PySide6.QtCore import QCoreApplication, QObject
from PySide6.QtNetwork import QLocalServer, QLocalSocket

log = logging.getLogger(__name__)

#: Commands accepted on the command line (besides starting the app).
COMMANDS = (
    "snapshot",
    "walkthrough",
    "dictation",
    "finish",
    "toggle-draw",
    "cancel",
    "settings",
    "help",
    "status",
    "quit",
)


def server_name() -> str:
    try:
        user = getpass.getuser()
    except Exception:
        user = "user"
    return "voice-annotate-" + re.sub(r"[^A-Za-z0-9_.-]", "_", user)


class TriggerServer(QObject):
    """Listens for commands from other ``voice-annotate`` processes of the same user."""

    def __init__(self, handler: Callable[[str], str], name: Optional[str] = None):
        super().__init__()
        self._handler = handler
        self._name = name or server_name()
        self._server = QLocalServer(self)
        self._server.setSocketOptions(QLocalServer.UserAccessOption)
        self._server.newConnection.connect(self._accept)

    def listen(self) -> bool:
        if not self._server.listen(self._name):
            # A stale socket from a crashed instance (the caller checked that none answers).
            QLocalServer.removeServer(self._name)
            if not self._server.listen(self._name):
                log.error("Cannot listen for triggers: %s", self._server.errorString())
                return False
        return True

    def close(self) -> None:
        self._server.close()

    def _accept(self) -> None:
        while self._server.hasPendingConnections():
            socket = self._server.nextPendingConnection()
            socket.readyRead.connect(lambda s=socket: self._read(s))
            socket.disconnected.connect(socket.deleteLater)
            # The command may have arrived before the signal was connected.
            if socket.bytesAvailable():
                self._read(socket)

    def _read(self, socket: QLocalSocket) -> None:
        command = bytes(socket.readAll()).decode("utf-8", "replace").strip()
        if not command:
            return
        try:
            reply = self._handler(command)
        except Exception as error:
            log.exception("Trigger %s failed", command)
            reply = "error: {}".format(error)
        socket.write((reply + "\n").encode("utf-8"))
        socket.flush()
        socket.disconnectFromServer()


def send(command: str, timeout_ms: int = 2000, name: Optional[str] = None) -> Optional[str]:
    """Sends a command to the running instance. Returns its reply, or None if none runs."""
    if QCoreApplication.instance() is None:
        send._app = QCoreApplication([])  # type: ignore[attr-defined]
    socket = QLocalSocket()
    socket.connectToServer(name or server_name())
    if not socket.waitForConnected(500):
        return None
    socket.write((command + "\n").encode("utf-8"))
    socket.flush()
    if socket.bytesToWrite() > 0:
        socket.waitForBytesWritten(timeout_ms)
    reply = b""
    deadline = time.monotonic() + timeout_ms / 1000.0
    while True:
        # Read first: the app may already have answered and hung up.
        reply += bytes(socket.readAll())
        remaining = int((deadline - time.monotonic()) * 1000)
        if b"\n" in reply or remaining <= 0:
            break
        if not socket.waitForReadyRead(remaining):
            reply += bytes(socket.readAll())
            break
    socket.disconnectFromServer()
    return reply.decode("utf-8", "replace").strip()

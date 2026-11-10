"""The tray icon and its menu (the counterpart of the macOS menu bar item)."""

from __future__ import annotations

import logging
from typing import Callable, Dict, List, Optional

from PySide6.QtCore import QObject, QUrl
from PySide6.QtGui import QAction, QCursor, QDesktopServices
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from .. import clipboard, paths
from ..clipboard import RecentsStore
from ..platform import Platform
from ..session import Phase, SessionController
from ..settings import AppSettings, ClipboardFormat, HotkeyAction
from ..transcriber import Transcriber
from . import icons

log = logging.getLogger(__name__)


class TrayController(QObject):
    def __init__(
        self,
        controller: SessionController,
        recents: RecentsStore,
        settings: AppSettings,
        transcriber: Transcriber,
        platform: Platform,
    ):
        super().__init__()
        self.controller = controller
        self.recents = recents
        self.settings = settings
        self.transcriber = transcriber
        self.platform = platform
        self.on_show_settings: Callable[[], None] = lambda: None
        self.on_show_help: Callable[[], None] = lambda: None
        self.on_retry_model: Callable[[], None] = lambda: None
        #: ``{action: reason}`` for hotkeys that could not be registered.
        self.hotkey_failures: Dict[str, str] = {}

        self.menu = QMenu()
        self.menu.aboutToShow.connect(self.rebuild)
        #: Submenus of the current menu. They are referenced here because a submenu that is
        #: only known to Qt can be deleted under the menu by some PySide versions.
        self._submenus: List[QMenu] = []
        self.tray: Optional[QSystemTrayIcon] = None
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray = QSystemTrayIcon(icons.tray_icon("idle"))
            self.tray.setContextMenu(self.menu)
            self.tray.activated.connect(self._activated)
            self.tray.setToolTip("voice-annotate")
            self.tray.show()
        controller.phase_changed.connect(self.update_phase)
        self.rebuild()

    @property
    def available(self) -> bool:
        return self.tray is not None

    def _activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        # A left click opens the menu too (the default on Windows would do nothing).
        if reason == QSystemTrayIcon.Trigger:
            self.menu.popup(QCursor.pos())

    def update_phase(self, phase: Phase) -> None:
        if self.tray is None:
            return
        state = "idle"
        if phase in (Phase.WALKTHROUGH, Phase.DICTATION, Phase.SNAPSHOT, Phase.SNAPSHOT_NAVIGATING):
            state = "recording"
        elif phase == Phase.PROCESSING:
            state = "busy"
        self.tray.setIcon(icons.tray_icon(state))
        self.tray.setToolTip("voice-annotate — {}".format(self.transcriber.status.title))

    def _add(self, menu: QMenu, title: str, handler: Callable[[], None], enabled: bool = True) -> QAction:
        action = menu.addAction(title)
        action.triggered.connect(lambda _=False: handler())
        action.setEnabled(enabled)
        return action

    def _submenu(self, parent: QMenu, title: str) -> QMenu:
        submenu = QMenu(title, parent)
        parent.addMenu(submenu)
        self._submenus.append(submenu)
        return submenu

    def _key(self, action: HotkeyAction) -> str:
        if action.value in self.hotkey_failures or not self.hotkeys_available:
            return ""
        return "\t" + self.settings.hotkey(action).display

    #: Set by the app: whether this session has global hotkeys at all.
    hotkeys_available = True

    def rebuild(self) -> None:
        menu, controller = self.menu, self.controller
        menu.clear()
        for submenu in self._submenus:
            submenu.deleteLater()
        self._submenus = []
        phase = controller.phase

        if phase.is_capturing:
            self._add(menu, "Finish capture" + self._key(HotkeyAction.FINISH), controller.finish)
            self._add(menu, "Cancel capture", controller.cancel)
        else:
            idle = phase == Phase.IDLE
            self._add(menu, "Snapshot" + self._key(HotkeyAction.SNAPSHOT), controller.start_snapshot, idle)
            self._add(
                menu, "Walkthrough" + self._key(HotkeyAction.WALKTHROUGH), controller.start_walkthrough, idle
            )
            self._add(menu, "Dictation" + self._key(HotkeyAction.DICTATION), controller.start_dictation, idle)

        menu.addSeparator()
        status = self.transcriber.status
        self._add(menu, status.title, lambda: None, enabled=False)
        if status.kind == "failed":
            self._add(menu, "Retry model download", self.on_retry_model)
        for action, reason in self.hotkey_failures.items():
            self._add(menu, "⚠ Shortcut unavailable: {}".format(reason), self.on_show_settings)

        menu.addSeparator()
        self.recents.prune_missing()
        recent = self._submenu(menu, "Recent captures")
        if not self.recents.items:
            self._add(recent, "No captures yet", lambda: None, enabled=False)
        for capture in self.recents.items:
            title = "{} · {} · {}".format(
                capture.mode.title, capture.date.astimezone().strftime("%b %d, %H:%M"), capture.summary
            )
            sub = self._submenu(recent, title)
            for fmt in ClipboardFormat:
                self._add(sub, "Copy: {}".format(fmt.title), lambda c=capture, f=fmt: self._copy(c, f))
            sub.addSeparator()
            self._add(
                sub,
                "Open capture.md",
                lambda c=capture: QDesktopServices.openUrl(QUrl.fromLocalFile(c.markdown_path)),
            )
            self._add(sub, "Show in file manager", lambda c=capture: self.platform.reveal(c.markdown_path))
        self._add(menu, "Open captures folder", lambda: self.platform.open_folder(str(paths.captures())))

        menu.addSeparator()
        self._add(menu, "Settings…", self.on_show_settings)
        self._add(menu, "Permissions && help…", self.on_show_help)
        menu.addSeparator()
        self._add(menu, "Quit voice-annotate", QApplication.quit)

    def _copy(self, capture, fmt: ClipboardFormat) -> None:
        toast = self.controller.toast
        try:
            clipboard.write(capture, fmt)
            toast.show_message("Copied to clipboard", fmt.title)
        except Exception as error:
            log.exception("Copy failed")
            toast.show_message("Copy failed", str(error), success=False, duration=4)

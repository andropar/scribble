"""The floating toolbar shown during a capture, and toasts.

Both are small always-on-top pills that never take keyboard focus, so the app being
captured (or the Snapshot overlay) keeps the keyboard.
"""

from __future__ import annotations

from typing import List, Optional

from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QCursor,
    QGuiApplication,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPen,
    QRegion,
    QScreen,
)
from PySide6.QtWidgets import (
    QAbstractButton,
    QApplication,
    QHBoxLayout,
    QLabel,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..core.markdown import clock
from ..core.models import PALETTE, AnnotationTool, CaptureMode
from ..platform import Platform
from . import icons

_TEXT = "#f2f2f4"
_DIM = "#a9a9b2"
_BUTTON_STYLE = """
QToolButton { border: none; border-radius: 6px; padding: 0px; background: transparent; }
QToolButton:hover { background: rgba(255, 255, 255, 36); }
QToolButton:checked { background: rgba(90, 160, 255, 110); }
"""


class _Pill(QWidget):
    """A dark rounded panel that floats above everything and takes no focus."""

    RADIUS = 20

    def __init__(self, platform: Platform, click_through: bool = False):
        super().__init__()
        self._platform = platform
        self._composited = platform.compositing()
        flags = platform.overlay_flags() | Qt.WindowDoesNotAcceptFocus
        if click_through:
            flags |= Qt.WindowTransparentForInput
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setFocusPolicy(Qt.NoFocus)
        if self._composited:
            self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setWindowTitle("voice-annotate")
        self._excluded = False

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        radius = min(self.RADIUS, rect.height() / 2)
        painter.setPen(QPen(QColor(255, 255, 255, 40), 1))
        painter.setBrush(QColor(30, 30, 34, 238 if self._composited else 255))
        painter.drawRoundedRect(rect, radius, radius)
        painter.end()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if not self._composited:
            # No compositor: cut the corners instead of blending them.
            path = QPainterPath()
            radius = min(self.RADIUS, self.height() / 2)
            path.addRoundedRect(QRectF(self.rect()), radius, radius)
            self.setMask(QRegion(path.toFillPolygon().toPolygon()))

    def place(self, screen: Optional[QScreen]) -> None:
        """Fits the pill to its content at the top center of ``screen``."""
        screen = screen or QGuiApplication.primaryScreen()
        if screen is None:
            return
        self.adjustSize()
        area = screen.availableGeometry()
        self.move(area.center().x() - self.width() // 2, area.top() + 6)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if not self._excluded:
            # Keeps the pill out of screenshots and recordings where the system can do that.
            self._excluded = self._platform.exclude_from_capture(self)


class LevelMeter(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self._level = 0.0
        self._available = True
        self.setFixedSize(23, 18)

    def set_level(self, level: float, available: bool = True) -> None:
        level = level if available else 0.0
        if abs(level - self._level) > 0.01 or available != self._available:
            self._level, self._available = level, available
            self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setPen(Qt.NoPen)
        painter.setOpacity(1.0 if self._available else 0.3)
        for i in range(5):
            height = 6 + i * 3
            lit = i / 5.0 < self._level
            painter.setBrush(QColor("#34c759") if lit else QColor(255, 255, 255, 46))
            painter.drawRoundedRect(QRectF(i * 5, self.height() - height, 3, height), 1, 1)
        painter.end()


class _Dot(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self._color = QColor("#ff9f0a")
        self.setFixedSize(10, 10)

    def set_color(self, color: str) -> None:
        self._color = QColor(color)
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setPen(Qt.NoPen)
        painter.setBrush(self._color)
        painter.drawEllipse(QRectF(0.5, 0.5, 9, 9))
        painter.end()


class _Swatch(QAbstractButton):
    def __init__(self, index: int) -> None:
        super().__init__()
        self.index = index
        self.setCheckable(True)
        self.setFixedSize(20, 20)
        self.setFocusPolicy(Qt.NoFocus)
        self.setCursor(Qt.PointingHandCursor)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        c = PALETTE[self.index]
        painter.setBrush(QColor.fromRgbF(c.r, c.g, c.b))
        selected = self.isChecked()
        painter.setPen(QPen(QColor(255, 255, 255, 235 if selected else 70), 2 if selected else 1))
        painter.drawEllipse(QRectF(3, 3, 14, 14))
        painter.end()


def _separator() -> QWidget:
    line = QWidget()
    line.setFixedSize(1, 22)
    line.setStyleSheet("background: rgba(255, 255, 255, 46);")
    return line


class Hud(_Pill):
    """Recording state, tools and colors, and the finish/cancel buttons."""

    tool_selected = Signal(object)
    color_selected = Signal(int)
    undo_requested = Signal()
    next_shot_requested = Signal()
    toggle_draw_requested = Signal()
    finish_requested = Signal()
    cancel_requested = Signal()

    def __init__(self, platform: Platform, snapshot_key: str, finish_key: str, draw_modifier: str):
        super().__init__(platform)
        self.snapshot_key, self.finish_key, self.draw_modifier = snapshot_key, finish_key, draw_modifier
        self.mode = CaptureMode.SNAPSHOT
        self.elapsed = 0.0
        self.drawing = False
        self.draw_toggled = False
        self.tool = AnnotationTool.PEN
        self.color_index = 0
        self.navigating = False
        self.shot_count = 1
        self.annotation_count = 0
        self.mic_available = True
        self.starting = True
        self._drag_offset: Optional[QPoint] = None
        self._screen: Optional[QScreen] = None
        self._owner: Optional[QWidget] = None
        self.setStyleSheet(_BUTTON_STYLE)

        row = QHBoxLayout(self)
        row.setContentsMargins(14, 7, 12, 7)
        row.setSpacing(8)

        self._dot = _Dot()
        self._time = QLabel("00:00")
        self._time.setStyleSheet("color: {}; font-weight: 600; font-size: 12px;".format(_TEXT))
        self._time.setMinimumWidth(38)
        self._counts = QLabel("")
        self._counts.setStyleSheet("color: {}; font-size: 11px;".format(_DIM))
        self._meter = LevelMeter()
        self._meter.setToolTip("Microphone level")
        for w in (self._dot, self._time, self._counts, self._meter):
            row.addWidget(w)
        row.addWidget(_separator())

        self._message = QLabel("")
        self._message.setStyleSheet("color: {}; font-size: 12px; font-weight: 500;".format(_TEXT))
        row.addWidget(self._message)

        self._tools = QWidget()
        tools = QHBoxLayout(self._tools)
        tools.setContentsMargins(0, 0, 0, 0)
        tools.setSpacing(2)
        self._tool_buttons = {}
        for tool in AnnotationTool:
            button = self._button(icons.tool_icon(tool), tool.title, checkable=True)
            button.clicked.connect(lambda _=False, t=tool: self.tool_selected.emit(t))
            self._tool_buttons[tool] = button
            tools.addWidget(button)
        tools.addWidget(_separator())
        self._swatches: List[_Swatch] = []
        for index in range(len(PALETTE)):
            swatch = _Swatch(index)
            swatch.clicked.connect(lambda _=False, i=index: self.color_selected.emit(i))
            self._swatches.append(swatch)
            tools.addWidget(swatch)
        tools.addWidget(_separator())
        self._undo = self._button(icons.icon("undo"), "Undo (Ctrl+Z)")
        self._undo.clicked.connect(self.undo_requested)
        tools.addWidget(self._undo)
        self._next_shot = self._button(icons.icon("camera"), "Hide overlay and take another shot (Tab)")
        self._next_shot.clicked.connect(self.next_shot_requested)
        tools.addWidget(self._next_shot)
        self._draw = QToolButton()
        self._draw.setFocusPolicy(Qt.NoFocus)
        self._draw.setCheckable(True)
        self._draw.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self._draw.setIconSize(QSize(18, 18))
        self._draw.setFixedHeight(26)
        self._draw.setToolTip("Toggle draw mode")
        self._draw.setStyleSheet(
            "QToolButton {{ color: {}; font-size: 11px; font-weight: 500; padding: 0 8px; "
            "background: rgba(255, 255, 255, 20); }}".format(_TEXT)
        )
        self._draw.clicked.connect(self.toggle_draw_requested)
        tools.addWidget(self._draw)
        row.addWidget(self._tools)
        self._tools_separator = _separator()
        row.addWidget(self._tools_separator)

        self._finish = self._button(icons.icon("check", "#34c759"), "Finish and copy")
        self._finish.clicked.connect(self.finish_requested)
        row.addWidget(self._finish)
        self._cancel = self._button(icons.icon("cross", "#b9b9c2"), "Cancel")
        self._cancel.clicked.connect(self.cancel_requested)
        row.addWidget(self._cancel)
        self.refresh()

    @staticmethod
    def _button(icon, tip: str, checkable: bool = False) -> QToolButton:
        button = QToolButton()
        button.setIcon(icon)
        button.setIconSize(QSize(20, 20))
        button.setFixedSize(28, 26)
        button.setToolTip(tip)
        button.setCheckable(checkable)
        button.setFocusPolicy(Qt.NoFocus)
        return button

    def reset(self, mode: CaptureMode, mic_available: bool) -> None:
        self.mode = mode
        self.elapsed = 0.0
        self.drawing = False
        self.draw_toggled = False
        self.navigating = False
        self.shot_count = 0 if mode == CaptureMode.SNAPSHOT else 1
        self.annotation_count = 0
        self.starting = True
        self.mic_available = mic_available
        self._meter.set_level(0, mic_available)
        self.refresh()

    def set_level(self, level: float) -> None:
        self._meter.set_level(level, self.mic_available)

    def set_elapsed(self, elapsed: float) -> None:
        self.elapsed = elapsed
        text = clock(elapsed)
        if text != self._time.text():
            self._time.setText(text)

    def refresh(self) -> None:
        """Brings every widget in line with the state fields, then refits the pill."""
        snapshot = self.mode == CaptureMode.SNAPSHOT
        self._dot.set_color("#ff9f0a" if self.starting else "#ff3b30")
        self._time.setText(clock(self.elapsed))
        counts = []
        if snapshot and self.shot_count > 1:
            counts.append("{} shots".format(self.shot_count))
        if self.annotation_count > 0:
            counts.append("#{}".format(self.annotation_count))
        self._counts.setText("· " + " · ".join(counts) if counts else "")
        self._counts.setVisible(bool(counts))
        self._meter.setToolTip("Microphone level" if self.mic_available else "No microphone")
        self._meter.set_level(0, self.mic_available)

        message = ""
        if self.mode == CaptureMode.DICTATION:
            message = "Dictating · {} to finish".format(self.finish_key)
        elif self.navigating:
            message = "Navigate, then {} for shot {} · {} to finish".format(
                self.snapshot_key, self.shot_count + 1, self.finish_key
            )
        self._message.setText(message)
        self._message.setVisible(bool(message))
        show_tools = not message
        self._tools.setVisible(show_tools)
        self._tools_separator.setVisible(show_tools)

        for tool, button in self._tool_buttons.items():
            button.setChecked(tool == self.tool)
            button.setToolTip("{} ({})".format(tool.title, tool.shortcut.upper()) if snapshot else tool.title)
        for swatch in self._swatches:
            swatch.setChecked(swatch.index == self.color_index)
            swatch.setToolTip("Color {}".format(swatch.index + 1))
            swatch.update()
        self._next_shot.setVisible(snapshot)
        self._draw.setVisible(not snapshot)
        self._draw.setChecked(self.drawing)
        self._draw.setIcon(icons.icon("pencil" if self.drawing else "cursor"))
        if self.drawing:
            self._draw.setText("Drawing")
        elif self.draw_modifier:
            self._draw.setText("Hold {} to draw".format(self.draw_modifier))
        else:
            self._draw.setText("Click-through")
        self._finish.setToolTip(
            "Finish and copy ({}{})".format("Enter / " if snapshot else "", self.finish_key)
        )
        self._cancel.setToolTip("Cancel (Esc)" if snapshot else "Cancel")
        self.layout().activate()
        self.adjustSize()
        if self.isVisible() and self._drag_offset is None:
            self.place(self._screen)

    def show_on(self, screen: Optional[QScreen], above: Optional[QWidget] = None) -> None:
        """Shows the HUD at the top of ``screen``, stacked above the overlay ``above``."""
        self._screen = screen
        if above is not self._owner:
            # An owned window always stays above its owner, also after the overlay is clicked.
            # The owner can only change while the window is hidden.
            self.hide()
            self.winId()
            self.windowHandle().setTransientParent(above.windowHandle() if above is not None else None)
            self._owner = above
        self.place(screen)
        self.show()
        self.raise_()

    def detach(self) -> None:
        """Unties the HUD from its overlay before that closes (on Windows an owned window
        is destroyed together with its owner)."""
        if self._owner is not None:
            self.hide()
            if self.windowHandle() is not None:
                self.windowHandle().setTransientParent(None)
            self._owner = None

    # Dragging the pill by its background.

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.LeftButton:
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._drag_offset is not None and event.buttons() & Qt.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_offset)

    def hideEvent(self, event) -> None:
        self._drag_offset = None
        super().hideEvent(event)

    def click_at(self, global_pos: QPoint) -> bool:
        """Clicks the button under a global position (used while the pointer is grabbed)."""
        if not self.isVisible() or not self.frameGeometry().contains(global_pos):
            return False
        child = self.childAt(self.mapFromGlobal(global_pos))
        while child is not None and not isinstance(child, QAbstractButton):
            child = child.parentWidget()
        if isinstance(child, QAbstractButton):
            child.click()
        return True


class Toast(_Pill):
    """A short message at the top of the screen: progress while processing, then the result."""

    def __init__(self, platform: Platform):
        super().__init__(platform, click_through=True)
        self.text = ""
        self.detail = ""
        self.busy = False
        self.success = True
        row = QHBoxLayout(self)
        row.setContentsMargins(16, 9, 18, 9)
        row.setSpacing(10)
        self._dot = _Dot()
        row.addWidget(self._dot)
        column = QVBoxLayout()
        column.setSpacing(1)
        self._title = QLabel("")
        self._title.setStyleSheet("color: {}; font-size: 13px; font-weight: 600;".format(_TEXT))
        self._detail = QLabel("")
        self._detail.setStyleSheet("color: {}; font-size: 11px;".format(_DIM))
        column.addWidget(self._title)
        column.addWidget(self._detail)
        row.addLayout(column)
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self.hide)
        self._pulse = QTimer(self)
        self._pulse.setInterval(450)
        self._pulse.timeout.connect(self._blink)
        self._blink_on = True

    def _blink(self) -> None:
        self._blink_on = not self._blink_on
        self._dot.set_color("#ff9f0a" if self._blink_on else "#7a5a20")

    def show_message(
        self,
        text: str,
        detail: str = "",
        busy: bool = False,
        success: bool = True,
        duration: Optional[float] = 2.8,
        screen: Optional[QScreen] = None,
    ) -> None:
        self.text, self.detail, self.busy, self.success = text, detail, busy, success
        self._title.setText(text)
        self._detail.setText(detail)
        self._detail.setVisible(bool(detail))
        if busy:
            self._dot.set_color("#ff9f0a")
            self._pulse.start()
        else:
            self._pulse.stop()
            self._dot.set_color("#34c759" if success else "#ff9f0a")
        self.layout().activate()
        if screen is None:
            screen = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
        self.place(screen)
        self.show()
        self.raise_()
        self._hide_timer.stop()
        if duration is not None:
            self._hide_timer.start(int(duration * 1000))

    def hideEvent(self, event) -> None:
        self._pulse.stop()
        super().hideEvent(event)

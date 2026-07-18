"""Drop-target label — self-contained, no app-level dependencies."""

from __future__ import annotations

from pathlib import Path

from PySide6 import QtWidgets, QtGui, QtCore


class DropLabel(QtWidgets.QLabel):
    """A QLabel styled as a file-drop target with animated state feedback.

    Usage::

        area = DropLabel(hint="拖拽文件至此处", parent=win)
        area.setObjectName("myDropArea")
        area.clicked.connect(on_click)
    """

    class State:
        IDLE = 0
        HOVER = 1
        DRAG = 2
        PRESS = 3

    _ALPHAS = {State.IDLE: 0.0, State.HOVER: 0.05, State.DRAG: 0.14, State.PRESS: 0.14}
    SUPPORTED_SUFFIXES = {".wav", ".flac", ".mp3", ".ogg", ".m4a", ".mid", ".midi"}

    clicked = QtCore.Signal()
    files_dropped = QtCore.Signal(list)  # list[str] of file paths

    def __init__(
        self,
        hint: str = "",
        border_color: str = "#bbb",
        border_radius: int = 8,
        parent: QtWidgets.QWidget | None = None,
    ):
        super().__init__(parent)
        self._state = self.State.IDLE
        self._bg_alpha = 0.0
        self._anim: QtCore.QPropertyAnimation | None = None
        self._border_color = border_color
        self._border_radius = border_radius

        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_Hover)
        self.setAcceptDrops(True)
        self.setTextFormat(QtCore.Qt.TextFormat.RichText)
        self.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self.setCursor(QtGui.QCursor(QtCore.Qt.CursorShape.PointingHandCursor))
        if hint:
            self.setText(hint)

        # Base stylesheet — border always visible, background animated.
        self._refresh_base_style()

    # ── Style helpers ──────────────────────────────────────────────────
    def setObjectName(self, name: str) -> None:
        super().setObjectName(name)
        self._refresh_base_style()

    def _refresh_base_style(self) -> None:
        sel = f"QLabel#{self.objectName() or 'DropLabel'}"
        self._base_style = (
            f"{sel} {{"
            f"background: transparent;"
            f"border: 2px dashed {self._border_color};"
            f"border-radius: {self._border_radius}px;"
            f"}}"
        )
        self._apply_bg(self._bg_alpha)

    def _apply_bg(self, value: float) -> None:
        """Merge base border style with the animated background alpha."""
        a = int(value * 255)
        if a < 2:
            self.setStyleSheet(self._base_style)
        else:
            sel = f"QLabel#{self.objectName() or 'DropLabel'}"
            self.setStyleSheet(
                self._base_style + (
                    f"{sel} {{ background: rgba(0,0,0,{a}); }}"
                )
            )

    # ── Qt property (animated) ──────────────────────────────────────────
    @QtCore.Property(float)
    def bgAlpha(self) -> float:
        return self._bg_alpha

    @bgAlpha.setter
    def bgAlpha(self, value: float) -> None:
        self._bg_alpha = value
        self._apply_bg(value)

    # ── Animation ──────────────────────────────────────────────────────
    def _animate_to(self, target: float, ms: int = 200) -> None:
        if self._anim is not None:
            self._anim.stop()
        self._anim = QtCore.QPropertyAnimation(self, b"bgAlpha", self)
        self._anim.setDuration(ms)
        self._anim.setStartValue(self._bg_alpha)
        self._anim.setEndValue(target)
        self._anim.setEasingCurve(QtCore.QEasingCurve.Type.InOutCubic)
        self._anim.start()

    # ── State machine ──────────────────────────────────────────────────
    def _set_state(self, new: int) -> None:
        if new == self._state:
            return
        self._state = new
        self._animate_to(self._ALPHAS.get(new, 0.0))

    # ── Events ─────────────────────────────────────────────────────────
    def dragEnterEvent(self, event: QtGui.QDragEnterEvent) -> None:
        if event.mimeData().hasUrls():
            paths = _urls_to_paths(event.mimeData().urls())
            if any(_has_supported_suffix(p) for p in paths):
                event.acceptProposedAction()
                self._set_state(self.State.DRAG)

    def dragMoveEvent(self, event: QtGui.QDragMoveEvent) -> None:
        if event.mimeData().hasUrls():
            paths = _urls_to_paths(event.mimeData().urls())
            if any(_has_supported_suffix(p) for p in paths):
                event.acceptProposedAction()

    def dragLeaveEvent(self, _event: QtGui.QDragLeaveEvent) -> None:
        self._set_state(self.State.IDLE)
        if self.rect().contains(self.mapFromGlobal(QtGui.QCursor.pos())):
            self._set_state(self.State.HOVER)

    def dropEvent(self, event: QtGui.QDropEvent) -> None:
        self._set_state(self.State.IDLE)
        paths = [p for p in _urls_to_paths(event.mimeData().urls())
                 if _has_supported_suffix(p)]
        if paths:
            self.files_dropped.emit(paths)

    def enterEvent(self, _event: QtCore.QEvent) -> None:
        if self._state not in (self.State.DRAG, self.State.PRESS):
            self._set_state(self.State.HOVER)

    def leaveEvent(self, _event: QtCore.QEvent) -> None:
        self._set_state(self.State.IDLE)

    def mousePressEvent(self, _event: QtGui.QMouseEvent) -> None:
        self._set_state(self.State.PRESS)

    def mouseMoveEvent(self, _event: QtGui.QMouseEvent) -> None:
        if self._state == self.State.PRESS:
            inside = self.rect().contains(_event.pos())
            if not inside:
                self._set_state(self.State.IDLE)
            elif self._bg_alpha != self._ALPHAS[self.State.PRESS]:
                self._set_state(self.State.PRESS)

    def mouseReleaseEvent(self, _event: QtGui.QMouseEvent) -> None:
        if self._state == self.State.PRESS:
            self.clicked.emit()
        self._set_state(
            self.State.HOVER
            if self.rect().contains(_event.pos())
            else self.State.IDLE
        )


# ── Helpers ────────────────────────────────────────────────────────────────

def _urls_to_paths(urls: list[QtCore.QUrl]) -> list[str]:
    """Convert a list of QUrl objects to local file paths."""
    return [u.toLocalFile() for u in urls if u.isLocalFile()]


def _has_supported_suffix(path: str) -> bool:
    """Check if *path* ends with one of the supported audio suffixes."""
    return Path(path).suffix.lower() in DropLabel.SUPPORTED_SUFFIXES

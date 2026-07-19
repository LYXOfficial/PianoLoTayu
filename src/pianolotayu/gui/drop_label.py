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

    # Background fill alpha per state (depth)
    _ALPHAS = {
        State.IDLE: 0.0,
        State.HOVER: 0.06,
        State.DRAG: 0.18,
        State.PRESS: 0.16,
    }
    SUPPORTED_SUFFIXES = {
        ".wav", ".flac", ".mp3", ".ogg", ".m4a", ".aac", ".mid", ".midi",
    }

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
        # External drag highlight (e.g. elevated WM_DROPFILES path has no OLE events)
        self._external_drag = False
        # True while a local mouse press is in progress (not a shell file drag)
        self._local_press = False

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

    def _border_spec(self) -> str:
        """Dashed idle / solid deeper border when pressed or dragging files."""
        r = self._border_radius
        if self._state in (self.State.DRAG, self.State.PRESS):
            # Solid + slightly darker = “pressed in” depth
            return (
                f"border: 2px solid #888;"
                f"border-radius: {r}px;"
            )
        if self._state == self.State.HOVER:
            return (
                f"border: 2px dashed #999;"
                f"border-radius: {r}px;"
            )
        return (
            f"border: 2px dashed {self._border_color};"
            f"border-radius: {r}px;"
        )

    def _refresh_base_style(self) -> None:
        sel = f"QLabel#{self.objectName() or 'DropLabel'}"
        self._base_style = (
            f"{sel} {{"
            f"background: transparent;"
            f"{self._border_spec()}"
            f"}}"
        )
        self._apply_bg(self._bg_alpha)

    def _apply_bg(self, value: float) -> None:
        """Merge border style with the animated background alpha."""
        sel = f"QLabel#{self.objectName() or 'DropLabel'}"
        a = int(value * 255)
        if a < 2:
            self.setStyleSheet(
                f"{sel} {{"
                f"background: transparent;"
                f"{self._border_spec()}"
                f"}}"
            )
        else:
            # Slight warm lift on drag for more “depth”
            if self._state == self.State.DRAG:
                bg = f"rgba(40,40,45,{a})"
            else:
                bg = f"rgba(0,0,0,{a})"
            self.setStyleSheet(
                f"{sel} {{"
                f"background: {bg};"
                f"{self._border_spec()}"
                f"}}"
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
        # Border changes immediately with state; fill animates
        self._refresh_base_style()
        self._animate_to(self._ALPHAS.get(new, 0.0))

    def set_external_drag(self, active: bool) -> None:
        """Drive the drag-depth highlight from outside Qt's OLE drag events.

        Used when the process is elevated: OLE is revoked for UIPI, so
        ``dragEnterEvent`` never runs — the main window polls cursor/button
        and calls this instead.
        """
        # Don't steal a local click (open file dialog) for the drag look
        if self._local_press:
            active = False
        active = bool(active)
        if active == self._external_drag and (
            (active and self._state == self.State.DRAG)
            or (not active and self._state != self.State.DRAG)
        ):
            return
        self._external_drag = active
        if active:
            self._set_state(self.State.DRAG)
        else:
            if self._local_press:
                return  # leave PRESS alone
            # Back to hover if cursor still inside, else idle
            if self.rect().contains(self.mapFromGlobal(QtGui.QCursor.pos())):
                self._set_state(self.State.HOVER)
            else:
                self._set_state(self.State.IDLE)

    def pulse_drop_feedback(self) -> None:
        """Brief depth flash after a successful drop (any path)."""
        self._external_drag = False
        self._set_state(self.State.DRAG)
        QtCore.QTimer.singleShot(160, self._after_drop_pulse)

    def _after_drop_pulse(self) -> None:
        if self._external_drag:
            return  # still dragging another file
        if self.rect().contains(self.mapFromGlobal(QtGui.QCursor.pos())):
            self._set_state(self.State.HOVER)
        else:
            self._set_state(self.State.IDLE)

    # ── Events ─────────────────────────────────────────────────────────
    def dragEnterEvent(self, event: QtGui.QDragEnterEvent) -> None:
        # On Windows, urls() may be empty during dragEnter even when
        # hasUrls() is True — accept optimistically, filter in dropEvent.
        if _mime_looks_like_files(event.mimeData()):
            event.setDropAction(QtCore.Qt.DropAction.CopyAction)
            event.accept()
            self._external_drag = False
            self._set_state(self.State.DRAG)
        else:
            event.ignore()

    def dragMoveEvent(self, event: QtGui.QDragMoveEvent) -> None:
        if _mime_looks_like_files(event.mimeData()):
            event.setDropAction(QtCore.Qt.DropAction.CopyAction)
            event.accept()
            # Keep depth while moving inside (re-enter after leave edge cases)
            if self._state != self.State.DRAG:
                self._set_state(self.State.DRAG)
        else:
            event.ignore()

    def dragLeaveEvent(self, _event: QtGui.QDragLeaveEvent) -> None:
        self._external_drag = False
        self._set_state(self.State.IDLE)
        if self.rect().contains(self.mapFromGlobal(QtGui.QCursor.pos())):
            self._set_state(self.State.HOVER)

    def dropEvent(self, event: QtGui.QDropEvent) -> None:
        paths = paths_from_mime(event.mimeData())
        paths = [p for p in paths if _has_supported_suffix(p)]
        if paths:
            event.setDropAction(QtCore.Qt.DropAction.CopyAction)
            event.accept()
            self.pulse_drop_feedback()
            self.files_dropped.emit(paths)
        else:
            self._external_drag = False
            self._set_state(self.State.IDLE)
            event.ignore()

    def enterEvent(self, _event: QtCore.QEvent) -> None:
        if self._external_drag:
            self._set_state(self.State.DRAG)
            return
        if self._state not in (self.State.DRAG, self.State.PRESS):
            self._set_state(self.State.HOVER)

    def leaveEvent(self, _event: QtCore.QEvent) -> None:
        if self._external_drag:
            # Shell drag may still be over us; external poll owns DRAG
            return
        if self._state == self.State.DRAG:
            # OLE dragLeave already handled; don't clobber mid-drag
            return
        self._set_state(self.State.IDLE)

    def mousePressEvent(self, _event: QtGui.QMouseEvent) -> None:
        if self._external_drag:
            return
        self._local_press = True
        self._set_state(self.State.PRESS)

    def mouseMoveEvent(self, _event: QtGui.QMouseEvent) -> None:
        if self._state == self.State.PRESS:
            inside = self.rect().contains(_event.pos())
            if not inside:
                self._set_state(self.State.IDLE)
            elif self._bg_alpha != self._ALPHAS[self.State.PRESS]:
                self._set_state(self.State.PRESS)

    def mouseReleaseEvent(self, _event: QtGui.QMouseEvent) -> None:
        was_press = self._state == self.State.PRESS or self._local_press
        self._local_press = False
        if was_press and self.rect().contains(_event.pos()):
            self.clicked.emit()
        self._set_state(
            self.State.HOVER
            if self.rect().contains(_event.pos())
            else self.State.IDLE
        )


# ── Helpers ────────────────────────────────────────────────────────────────

def _mime_looks_like_files(mime: QtCore.QMimeData) -> bool:
    """True if the mime payload is likely a file drop (Windows-safe)."""
    if mime is None:
        return False
    if mime.hasUrls():
        # Prefer real path check when available; otherwise accept optimistically
        paths = paths_from_mime(mime)
        if not paths:
            return True
        return any(_has_supported_suffix(p) for p in paths)
    # Some shells only expose text/uri-list
    if mime.hasFormat("text/uri-list"):
        return True
    if mime.hasText():
        text = mime.text().strip()
        if text.startswith("file:") or Path(text).suffix:
            return True
    return False


def paths_from_mime(mime: QtCore.QMimeData) -> list[str]:
    """Extract local file paths from a drop mime payload."""
    if mime is None:
        return []
    paths: list[str] = []
    if mime.hasUrls():
        paths.extend(_urls_to_paths(mime.urls()))
    if not paths and mime.hasFormat("text/uri-list"):
        raw = bytes(mime.data("text/uri-list")).decode("utf-8", errors="replace")
        urls = []
        for line in raw.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            urls.append(QtCore.QUrl(line))
        paths.extend(_urls_to_paths(urls))
    if not paths and mime.hasText():
        for line in mime.text().splitlines():
            line = line.strip().strip('"')
            if not line:
                continue
            if line.startswith("file:"):
                paths.extend(_urls_to_paths([QtCore.QUrl(line)]))
            elif Path(line).suffix:
                paths.append(str(Path(line)))
    # Dedup preserve order
    seen: set[str] = set()
    out: list[str] = []
    for p in paths:
        key = p.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(p)
    return out


def _urls_to_paths(urls: list[QtCore.QUrl]) -> list[str]:
    """Convert a list of QUrl objects to local file paths.

    Windows Explorer may hand over ``file:///C:/...`` forms where
    ``isLocalFile()`` is unreliable, so prefer ``toLocalFile()`` and fall
    back to a manual path parse.
    """
    paths: list[str] = []
    for u in urls:
        path = u.toLocalFile()
        if not path:
            # PreferLocalFile already decodes percent-encoding
            s = u.toString(QtCore.QUrl.UrlFormattingOption.PreferLocalFile)
            if s.startswith("file:"):
                s = s[5:]
            # Strip authority: //localhost or ///
            if s.startswith("//"):
                # //host/path or ///C:/path
                rest = s[2:]
                slash = rest.find("/")
                s = rest[slash:] if slash >= 0 else rest
            # Windows drive: /C:/Users/... → C:/Users/...
            if len(s) >= 3 and s[0] == "/" and s[1].isalpha() and s[2] == ":":
                s = s[1:]
            path = s
        if path:
            paths.append(str(Path(path)))
    return paths


def _has_supported_suffix(path: str) -> bool:
    """Check if *path* ends with one of the supported audio suffixes."""
    return Path(path).suffix.lower() in DropLabel.SUPPORTED_SUFFIXES

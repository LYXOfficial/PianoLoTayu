"""Piano-roll MIDI preview with SoundFont playback and video export."""

from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np
import pretty_midi
from PySide6 import QtWidgets, QtGui, QtCore
from .win32_utils import TaskbarProgress

# ═══════════════════════════════════════════════════════════════════════════
# Constants
# ═══════════════════════════════════════════════════════════════════════════

_KEY_WHITE = {0, 2, 4, 5, 7, 9, 11}
NOTE_COLORS = [
    QtGui.QColor(0, 180, 255), QtGui.QColor(255, 140, 0),
    QtGui.QColor(0, 220, 100), QtGui.QColor(255, 80, 130),
    QtGui.QColor(180, 100, 255), QtGui.QColor(255, 210, 0),
]
MIN_PITCH, MAX_PITCH = 21, 108
KEY_W = 40


def _note_color(pitch: int) -> QtGui.QColor:
    return NOTE_COLORS[pitch % len(NOTE_COLORS)]


def _dist_y(pitch: int, note_h: int, extra: int) -> float:
    """Y position with *extra* bonus pixels spread across the top rows."""
    idx = MAX_PITCH - pitch
    if idx < extra:
        return idx * (note_h + 1)
    return extra * (note_h + 1) + (idx - extra) * note_h


def _dist_h(pitch: int, note_h: int, extra: int) -> int:
    """Row height for *pitch* when *extra* bonus pixels are spread."""
    return note_h + 1 if (MAX_PITCH - pitch) < extra else note_h


# ═══════════════════════════════════════════════════════════════════════════
# ffmpeg encoder availability check (cached)
# ═══════════════════════════════════════════════════════════════════════════

_AVAILABLE_ENCODERS: set[str] | None = None


def _get_available_encoders() -> set[str]:
    """Return the set of encoder names available in ffmpeg (cached)."""
    global _AVAILABLE_ENCODERS
    if _AVAILABLE_ENCODERS is not None:
        return _AVAILABLE_ENCODERS
    try:
        import subprocess
        from imageio_ffmpeg import get_ffmpeg_exe
        ffmpeg = get_ffmpeg_exe()
        out = subprocess.run(
            [ffmpeg, "-encoders"], capture_output=True, text=True,
            timeout=10,
        ).stdout
        _AVAILABLE_ENCODERS = set()
        for line in out.splitlines():
            # ffmpeg encoder lines look like: " V....D libx264  ..."
            parts = line.strip().split()
            if len(parts) >= 2 and parts[0].startswith("V"):
                _AVAILABLE_ENCODERS.add(parts[1])
    except Exception:
        # If we can't query, assume all are available (fallback)
        _AVAILABLE_ENCODERS = {"libx264", "libx265", "libsvtav1"}
    return _AVAILABLE_ENCODERS


def _filter_available_codecs(
    codecs: list[tuple[str, str, str]],
) -> list[tuple[str, str, str]]:
    """Return only codecs whose encoder is present in ffmpeg."""
    available = _get_available_encoders()
    return [c for c in codecs if c[1] in available]


# ═══════════════════════════════════════════════════════════════════════════
# Fixed keyboard widget
# ═══════════════════════════════════════════════════════════════════════════

class KeyboardWidget(QtWidgets.QWidget):
    """Piano keyboard synced with the note grid.
    Horizontal mode: vertical strip on the left.
    Vertical mode:   horizontal bar at the bottom."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedWidth(KEY_W)
        self.setMinimumHeight(0)
        self._offset_y = 0
        self._note_h = 18
        self._extra = 0
        self._active_pitches: set[int] = set()
        self._vertical = False
        self._note_w = 20
        self._extra_w = 0

    def set_orientation(self, vertical: bool) -> None:
        self._vertical = vertical
        if vertical:
            self.setMinimumWidth(0)
            self.setMaximumWidth(16777215)
            self.setFixedHeight(52)
        else:
            self.setFixedWidth(KEY_W)
            self.setMinimumHeight(0)
            self.setMaximumHeight(16777215)
        self.update()

    def set_offset_y(self, y: int) -> None:
        self._offset_y = y
        self.update()

    def set_note_h(self, h: int, extra: int = 0) -> None:
        self._note_h = h
        self._extra = extra
        self.update()

    def set_h_fit(self, note_w: int, extra_w: int = 0) -> None:
        self._note_w = note_w
        self._extra_w = extra_w
        self.update()

    def set_active_notes(self, notes: list[tuple[int, float]]) -> None:
        self._active_pitches = {p for p, _ in notes}
        self.update()

    def paintEvent(self, _event: QtGui.QPaintEvent) -> None:
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        if self._vertical:
            self._paint_horizontal(p)
        else:
            self._paint_vertical(p)
        p.end()

    def _paint_vertical(self, p: QtGui.QPainter) -> None:
        """Horizontal-mode: keys are stacked vertically on the left side."""
        note_h, extra = self._note_h, self._extra
        for pitch in range(MIN_PITCH, MAX_PITCH + 1):
            row_h = _dist_h(pitch, note_h, extra)
            y = _dist_y(pitch, note_h, extra) - self._offset_y
            if y + row_h < 0 or y > self.height():
                continue
            is_white = (pitch % 12) in _KEY_WHITE
            if pitch in self._active_pitches:
                bg = QtGui.QColor(180, 180, 180) if is_white else QtGui.QColor(55, 55, 55)
            else:
                bg = QtGui.QColor(245, 245, 245) if is_white else QtGui.QColor(25, 25, 25)
            if is_white:
                p.fillRect(QtCore.QRectF(0, y, self.width(), row_h), bg)
            else:
                w = int(self.width() * 0.6)
                p.fillRect(QtCore.QRectF(self.width() - w, y, w, row_h), bg)
            p.setPen(QtGui.QColor(180, 180, 180))
            p.drawLine(0, y + row_h, self.width(), y + row_h)
            if pitch % 12 == 0:
                name = pretty_midi.note_number_to_name(pitch)
                p.setPen(QtGui.QColor(120, 120, 120))
                font_size = max(5, min(9, row_h - 3))
                p.setFont(QtGui.QFont("sans-serif", font_size))
                p.drawText(QtCore.QRectF(2, y, self.width() - 4, row_h),
                           QtCore.Qt.AlignmentFlag.AlignVCenter | QtCore.Qt.TextFlag.TextSingleLine,
                           name)

    def _paint_horizontal(self, p: QtGui.QPainter) -> None:
        """Vertical-mode: keys laid out horizontally at the bottom.
        Low pitch → left, high pitch → right (standard piano layout)."""
        note_w, extra_w = self._note_w, self._extra_w
        key_h = self.height()
        for pitch in range(MIN_PITCH, MAX_PITCH + 1):
            idx = pitch - MIN_PITCH
            col_w = note_w + 1 if idx < extra_w else note_w
            x = (idx * (note_w + 1)) if idx < extra_w else (
                extra_w * (note_w + 1) + (idx - extra_w) * note_w)
            if x + col_w < 0 or x > self.width():
                continue
            is_white = (pitch % 12) in _KEY_WHITE
            if pitch in self._active_pitches:
                bg = QtGui.QColor(180, 180, 180) if is_white else QtGui.QColor(55, 55, 55)
            else:
                bg = QtGui.QColor(245, 245, 245) if is_white else QtGui.QColor(25, 25, 25)
            if is_white:
                p.fillRect(QtCore.QRectF(x, 0, col_w, key_h), bg)
            else:
                black_h = int(key_h * 0.6)
                p.fillRect(QtCore.QRectF(x, 0, col_w, black_h), bg)
            p.setPen(QtGui.QColor(180, 180, 180))
            p.drawLine(x + col_w, 0, x + col_w, key_h)
            if pitch % 12 == 0:
                name = pretty_midi.note_number_to_name(pitch)
                p.setPen(QtGui.QColor(120, 120, 120))
                font_size = max(5, min(8, col_w - 2))
                p.setFont(QtGui.QFont("sans-serif", font_size))
                p.drawText(QtCore.QRectF(x, key_h - 14, col_w, 12),
                           QtCore.Qt.AlignmentFlag.AlignCenter | QtCore.Qt.TextFlag.TextSingleLine,
                           name)


# ═══════════════════════════════════════════════════════════════════════════
# Note-grid scene + view
# ═══════════════════════════════════════════════════════════════════════════

class GridScene(QtWidgets.QGraphicsScene):
    """Scene holding the note grid — always drawn at a canonical 200 px/s.
    Horizontal zoom is applied as a view transform (no rebuild needed).

    Supports two orientations:
      - Horizontal (default): time→X, pitch→Y, keyboard on the left
      - Vertical   (waterfall): time→Y, pitch→X, keyboard at the bottom
    """

    _CANONICAL_PX = 200  # fixed px-per-second for all scene items (h-mode time)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._midi: pretty_midi.PrettyMIDI | None = None
        # Horizontal-mode (default)
        self._note_h = 18
        self._extra = 0
        # Vertical-mode (waterfall)
        self._vertical = False
        self._note_w = 20
        self._extra_w = 0
        self._time_base = 200  # overwritten in _rebuild
        # Shared
        self._mono: QtGui.QColor | None = None
        self._note_items: dict[tuple[int, float], tuple[QtWidgets.QGraphicsRectItem, int]] = {}
        self._active_keys: set[tuple[int, float]] = set()
        self.setBackgroundBrush(QtGui.QColor(25, 25, 28))

    def set_mono(self, hex_color: str | None) -> None:
        self._mono = QtGui.QColor(hex_color) if hex_color else None
        if self._midi is not None:
            self._rebuild()

    def set_orientation(self, vertical: bool) -> None:
        if self._vertical == vertical:
            return
        self._vertical = vertical
        # Don't rebuild — the caller triggers fit-zoom which handles it

    def set_v_zoom(self, note_h: int, extra: int = 0) -> None:
        self._note_h = note_h
        self._extra = extra
        if self._midi is not None:
            self._rebuild()

    def set_h_fit(self, note_w: int, extra_w: int = 0) -> None:
        """Vertical-mode column fit (pitch axis only — time is always 200 px/s)."""
        self._note_w = note_w
        self._extra_w = extra_w
        if self._midi is not None and self._vertical:
            self._rebuild()

    def load(self, midi_path: str | Path) -> None:
        self._midi = pretty_midi.PrettyMIDI(str(midi_path))
        self._rebuild()

    def _rebuild(self) -> None:
        self.clear()
        self._note_items.clear()
        self._active_keys.clear()
        if self._midi is None:
            return
        total = MAX_PITCH - MIN_PITCH + 1
        if self._vertical:
            tw = total * self._note_w + self._extra_w
            th = self.scene_duration_s() * self._CANONICAL_PX + 200
            self._time_base = th  # must be set BEFORE _draw_notes / _draw_grid
        else:
            tw = self.scene_duration_s() * self._CANONICAL_PX + 200
            th = total * self._note_h + self._extra
        self._draw_grid()
        self._draw_notes()
        self.setSceneRect(0, 0, tw, th)

    def scene_duration_s(self) -> float:
        return self._midi.get_end_time() if self._midi else 0.0

    def time_to_x(self, t: float) -> float:
        """Canonical scene x (always at 200 px/s — horizontal mode)."""
        return t * self._CANONICAL_PX

    def time_to_y(self, t: float) -> float:
        """Canonical scene y — t=0 at bottom, drawn at 200 px/s."""
        return self._time_base - t * self._CANONICAL_PX

    def y_to_time(self, scroll_y: float, vp_h: int) -> float:
        """Convert *canonical* scroll value + viewport height to time."""
        return (self._time_base - scroll_y - vp_h) / self._CANONICAL_PX

    def pitch_to_x(self, pitch: int) -> float:
        """Column x-position for *pitch* (vertical mode).
        Low pitch → left, high pitch → right (standard piano layout)."""
        idx = pitch - MIN_PITCH
        if idx < self._extra_w:
            return idx * (self._note_w + 1)
        return self._extra_w * (self._note_w + 1) + (idx - self._extra_w) * self._note_w

    def pitch_width(self, pitch: int) -> int:
        """Column width for *pitch* (vertical mode)."""
        return self._note_w + 1 if (pitch - MIN_PITCH) < self._extra_w else self._note_w

    def note_h(self) -> int:
        return self._note_h

    # ── Drawing ─────────────────────────────────────────────────────────
    # ── Drawing ─────────────────────────────────────────────────────────
    def _draw_grid(self) -> None:
        if self._vertical:
            th = self.scene_duration_s() * self._CANONICAL_PX + 200
            for pitch in range(MIN_PITCH, MAX_PITCH + 1):
                x = self.pitch_to_x(pitch)
                col_w = self.pitch_width(pitch)
                is_white = (pitch % 12) in _KEY_WHITE
                c = QtGui.QColor(38, 38, 42) if is_white else QtGui.QColor(22, 22, 25)
                r = self.addRect(x, 0, col_w, th,
                                 QtGui.QPen(QtCore.Qt.PenStyle.NoPen), QtGui.QBrush(c))
                r.setZValue(-1)
                line = self.addLine(x, 0, x, th, QtGui.QPen(QtGui.QColor(45, 45, 50), 1))
                line.setZValue(0)
        else:
            tw = self.scene_duration_s() * self._CANONICAL_PX + 200
            note_h, extra = self._note_h, self._extra
            for pitch in range(MIN_PITCH, MAX_PITCH + 1):
                y = _dist_y(pitch, note_h, extra)
                row_h = _dist_h(pitch, note_h, extra)
                is_white = (pitch % 12) in _KEY_WHITE
                c = QtGui.QColor(38, 38, 42) if is_white else QtGui.QColor(22, 22, 25)
                r = self.addRect(0, y, tw, row_h,
                                 QtGui.QPen(QtCore.Qt.PenStyle.NoPen), QtGui.QBrush(c))
                r.setZValue(-1)
                line = self.addLine(0, y, tw, y, QtGui.QPen(QtGui.QColor(45, 45, 50), 1))
                line.setZValue(0)

    def _draw_notes(self) -> None:
        if self._midi is None:
            return
        if self._vertical:
            for inst in self._midi.instruments:
                if inst.is_drum:
                    continue
                for note in inst.notes:
                    if note.pitch < MIN_PITCH or note.pitch > MAX_PITCH:
                        continue
                    col_w = self.pitch_width(note.pitch)
                    x = self.pitch_to_x(note.pitch) + max(1, col_w // 8)
                    w = max(2, col_w - max(2, col_w // 6))
                    y_top = self.time_to_y(note.end)     # later → higher
                    y_bot = self.time_to_y(note.start)   # earlier → lower
                    y = y_top
                    h = max(3.0, y_bot - y_top)
                    alpha = int(80 + note.velocity / 127 * 175)
                    if self._mono:
                        color = QtGui.QColor(self._mono)
                    else:
                        color = _note_color(note.pitch)
                    color.setAlpha(alpha)
                    r = self.addRect(x, y, w, h,
                                     QtGui.QPen(color.darker(120), 1),
                                     QtGui.QBrush(color))
                    r.setZValue(5)
                    self._note_items[(note.pitch, note.start)] = (r, alpha)
        else:
            note_h, extra = self._note_h, self._extra
            for inst in self._midi.instruments:
                if inst.is_drum:
                    continue
                for note in inst.notes:
                    if note.pitch < MIN_PITCH or note.pitch > MAX_PITCH:
                        continue
                    x = self.time_to_x(note.start)
                    w = max(3.0, self.time_to_x(note.end) - x)
                    row_h = _dist_h(note.pitch, note_h, extra)
                    y = _dist_y(note.pitch, note_h, extra) + max(1, row_h // 8)
                    h = max(2, row_h - max(2, row_h // 6))
                    alpha = int(80 + note.velocity / 127 * 175)
                    if self._mono:
                        color = QtGui.QColor(self._mono)
                    else:
                        color = _note_color(note.pitch)
                    color.setAlpha(alpha)
                    r = self.addRect(x, y, w, h,
                                     QtGui.QPen(color.darker(120), 1),
                                     QtGui.QBrush(color))
                    r.setZValue(5)
                    self._note_items[(note.pitch, note.start)] = (r, alpha)

    def set_active_notes(self, notes: list[tuple[int, float]]) -> None:
        """Turn active-note rectangles white; restore others to original."""
        incoming = set(notes)
        if incoming == self._active_keys:
            return
        # Restore notes that are no longer active
        for key in (self._active_keys - incoming):
            entry = self._note_items.get(key)
            if entry is None:
                continue
            item, alpha = entry
            pitch = key[0]
            if self._mono:
                color = QtGui.QColor(self._mono)
            else:
                color = _note_color(pitch)
            color.setAlpha(alpha)
            item.setBrush(QtGui.QBrush(color))
            item.setPen(QtGui.QPen(color.darker(120), 1))
        # Highlight newly active notes in white
        for key in (incoming - self._active_keys):
            entry = self._note_items.get(key)
            if entry is None:
                continue
            item = entry[0]
            item.setBrush(QtGui.QBrush(QtGui.QColor(255, 255, 255)))
            item.setPen(QtGui.QPen(QtGui.QColor(220, 220, 220), 2))
        self._active_keys = incoming


class NoteGridView(QtWidgets.QGraphicsView):
    """Scrollable view over the note grid.  No scrollbars — horizontal
    navigation is driven by the seek bar; horizontal zoom uses a cheap
    view transform so the scene never needs rebuilding on zoom."""

    vertical_offset_changed = QtCore.Signal(int)
    horizontal_offset_changed = QtCore.Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._scene = GridScene(self)
        self.setScene(self._scene)
        self.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        self.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setDragMode(QtWidgets.QGraphicsView.DragMode.ScrollHandDrag)
        self.setTransformationAnchor(QtWidgets.QGraphicsView.ViewportAnchor.NoAnchor)
        self.setFrameStyle(QtWidgets.QFrame.Shape.NoFrame)
        self.setAlignment(QtCore.Qt.AlignmentFlag.AlignTop | QtCore.Qt.AlignmentFlag.AlignLeft)
        self.setMinimumHeight(0)
        self.setMinimumWidth(0)
        self.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Expanding,
        )
        self._display_px_per_sec = GridScene._CANONICAL_PX

    def load(self, midi_path: str | Path) -> None:
        self._scene.load(midi_path)

    def set_h_zoom(self, px_per_sec: int) -> None:
        """Apply zoom as a view transform — no scene rebuild.
        Anchored to the left (h-mode) / top (v-mode) so time position stays put."""
        # Snapshot current time
        if self._scene._vertical:
            t = self._view_y_to_time(self.verticalScrollBar().value())
        else:
            t = self.x_to_time(self.horizontalScrollBar().value())
        # Apply new zoom
        self._display_px_per_sec = px_per_sec
        scale = px_per_sec / GridScene._CANONICAL_PX
        if self._scene._vertical:
            self.setTransform(QtGui.QTransform.fromScale(1.0, scale))
        else:
            self.setTransform(QtGui.QTransform.fromScale(scale, 1.0))
        # Restore scroll so the same time stays at the same viewport position
        self.scroll_to_time(t)

    def set_v_zoom(self, note_h: int, extra: int = 0) -> None:
        self._scene.set_v_zoom(note_h, extra)

    def set_h_fit(self, note_w: int, extra_w: int = 0) -> None:
        """Vertical-mode: fit columns to viewport width."""
        self._scene.set_h_fit(note_w, extra_w)

    def set_orientation(self, vertical: bool) -> None:
        self._scene.set_orientation(vertical)
        self.resetTransform()
        self._display_px_per_sec = 200  # reset to canonical
        if vertical:
            self.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        else:
            self.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

    def set_mono(self, hex_color: str | None) -> None:
        self._scene.set_mono(hex_color)

    def scene_duration_s(self) -> float:
        return self._scene.scene_duration_s()

    def time_to_x(self, t: float) -> float:
        """View-pixel position for time *t* (X in h-mode, Y in v-mode)."""
        if self._scene._vertical:
            return self._view_time_to_y(t)
        return t * self._display_px_per_sec

    def note_h(self) -> int:
        return self._scene.note_h()

    # ── View-level time ↔ pixel helpers (account for QTransform) ─────
    def _view_time_to_y(self, t: float) -> float:
        """View-pixel Y for time *t* (vertical mode, after Y-scale transform)."""
        scale = self._display_px_per_sec / GridScene._CANONICAL_PX
        return self._scene.time_to_y(t) * scale

    def _view_y_to_time(self, scroll_val: int) -> float:
        """Convert vertical-scroll value (view pixels) to time."""
        scale = self._display_px_per_sec / GridScene._CANONICAL_PX
        view_y = scroll_val + self.viewport().height()
        canonical_y = view_y / scale
        return (self._scene._time_base - canonical_y) / GridScene._CANONICAL_PX

    def scroll_to_time(self, t: float) -> None:
        if self._scene._vertical:
            view_y = self._view_time_to_y(t)
            vp_h = self.viewport().height()
            self.verticalScrollBar().setValue(max(0, int(view_y - vp_h)))
        else:
            self.horizontalScrollBar().setValue(int(self.time_to_x(t)))

    def x_to_time(self, x: int) -> float:
        """Convert a scroll-pixel offset to seconds."""
        if self._scene._vertical:
            return self._view_y_to_time(x)
        return x / self._display_px_per_sec if self._display_px_per_sec else 0.0

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:
        """Anchor vertical-mode viewport to the bottom during window resize."""
        t_bottom = None
        if self._scene._vertical and self._scene._midi is not None:
            t_bottom = self._view_y_to_time(self.verticalScrollBar().value())
        super().resizeEvent(event)
        if t_bottom is not None:
            self.scroll_to_time(t_bottom)

    def scrollContentsBy(self, dx: int, dy: int) -> None:
        if self._scene._vertical:
            # Block horizontal scrolling — columns fit viewport exactly
            if dx != 0 and self.horizontalScrollBar().maximum() <= 0:
                dx = 0
            super().scrollContentsBy(dx, dy)
            if dy != 0:
                self.horizontal_offset_changed.emit(self.verticalScrollBar().value())
        else:
            # Block vertical scrolling — rows fit viewport exactly
            if dy != 0 and self.verticalScrollBar().maximum() <= 0:
                dy = 0
            super().scrollContentsBy(dx, dy)
            if dy != 0:
                self.vertical_offset_changed.emit(self.verticalScrollBar().value())
            if dx != 0:
                self.horizontal_offset_changed.emit(self.horizontalScrollBar().value())

    def grab_frame(self, width: int = 1920, height: int = 1080) -> QtGui.QImage:
        img = QtGui.QImage(width, height, QtGui.QImage.Format.Format_RGBA8888)
        img.fill(QtGui.QColor(25, 25, 28))
        painter = QtGui.QPainter(img)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        self._scene.render(painter, QtCore.QRectF(0, 0, width, height),
                           self._scene.sceneRect())
        painter.end()
        return img


# ═══════════════════════════════════════════════════════════════════════════
# SoundFont playback engine
# ═══════════════════════════════════════════════════════════════════════════

class MidiPlayer(QtCore.QObject):
    """Plays a MIDI file through a SoundFont using fluidsynth."""

    position = QtCore.Signal(float)
    active_notes_changed = QtCore.Signal(list)
    finished = QtCore.Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._synth = None
        self._midi: pretty_midi.PrettyMIDI | None = None
        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._tick)
        self._start_ts = 0.0
        self._paused = False
        self._pause_pos = 0.0
        self._active_notes: set[int] = set()

    def load_soundfont(self, sf_path: str) -> bool:
        try:
            import fluidsynth
        except ImportError:
            QtWidgets.QMessageBox.warning(
                self.parent(), "缺少组件",
                "未安装 fluidsynth 系统库\n"
                "Linux: sudo pacman -S fluidsynth\n"
                "Windows: 请下载 fluidsynth DLL 并放在程序目录")
            return False
        try:
            self._synth = fluidsynth.Synth()
            self._sfid = self._synth.sfload(sf_path)
            self._synth.program_select(0, self._sfid, 0, 0)
            if self._sfid < 0:
                raise RuntimeError(f"SoundFont 加载失败：{sf_path}")
            if sys.platform.startswith("linux"):
                for driver in ("pipewire", "pulseaudio"):
                    try:
                        self._synth.start(driver=driver)
                        break
                    except Exception:
                        continue
                else:
                    self._synth.start()  # default driver as last resort
            elif sys.platform == "win32":
                self._synth.start(driver="dsound")
            elif sys.platform == "darwin":
                self._synth.start(driver="coreaudio")
            else:
                self._synth.start()
            return True
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self.parent(), "SoundFont 错误", str(exc))
            return False

    def load_midi(self, midi_path: str | Path) -> None:
        self._midi = pretty_midi.PrettyMIDI(str(midi_path))

    def play(self, sf_path: str = "", start_t: float | None = None) -> None:
        if sf_path and self._synth is None:
            if not self.load_soundfont(sf_path):
                return
        if start_t is not None:
            self._start_ts = time.monotonic() - start_t
            self._paused = False
        elif self._paused:
            self._paused = False
            self._start_ts = time.monotonic() - self._pause_pos
        else:
            self._start_ts = time.monotonic()
        self._active_notes = set()
        self._timer.start()

    def pause(self) -> None:
        self._paused = True
        self._pause_pos = time.monotonic() - self._start_ts
        self._timer.stop()
        self.active_notes_changed.emit([])
        if self._synth is not None:
            self._synth.all_sounds_off(-1)

    def stop(self) -> None:
        self._timer.stop()
        self._paused = False
        self.active_notes_changed.emit([])
        if self._synth is not None:
            self._synth.all_sounds_off(-1)

    def cleanup(self) -> None:
        """Stop playback and release the synth — call before closing window."""
        self.stop()
        if self._synth is not None:
            self._synth.delete()
            self._synth = None

    def seek(self, t: float) -> None:
        if self._synth is not None:
            self._synth.all_sounds_off(0)
        if self._timer.isActive():
            self._start_ts = time.monotonic() - t
        self.position.emit(t)

    def _tick(self) -> None:
        if self._midi is None:
            return
        now = time.monotonic() - self._start_ts
        self.position.emit(now)
        if now > self._midi.get_end_time() + 2.0:
            self.stop()
            self.finished.emit()
            return
        if self._synth is None:
            return
        desired_pitches: set[int] = set()
        desired_notes: list[tuple[int, float]] = []
        for inst in self._midi.instruments:
            if inst.is_drum:
                continue
            for note in inst.notes:
                if note.start <= now < note.end:
                    desired_pitches.add(note.pitch)
                    desired_notes.append((note.pitch, note.start))
        for p in (self._active_notes - desired_pitches):
            self._synth.noteoff(0, p)
        for p in (desired_pitches - self._active_notes):
            v = 0
            for inst in (self._midi.instruments if self._midi else []):
                if inst.is_drum:
                    continue
                for n in inst.notes:
                    if n.pitch == p and n.start <= now < n.end and n.velocity > v:
                        v = n.velocity
            self._synth.noteon(0, p, v or 100)
        self._active_notes = desired_pitches
        self.active_notes_changed.emit(desired_notes)


# ═══════════════════════════════════════════════════════════════════════════
# Video exporter
# ═══════════════════════════════════════════════════════════════════════════

class VideoExportWorker(QtCore.QThread):
    """Render piano-roll frames + optional audio → video file."""

    progress = QtCore.Signal(str, int)  # (phase_name, pct 0-100)
    finished = QtCore.Signal(str)       # output path
    error = QtCore.Signal(str)          # error message

    def __init__(self, midi_path: str, sf_path: str, output: str,
                 fps: int = 30, width: int = 1920, height: int = 1080,
                 v_codec: str = "libx264", v_bitrate: str = "4M",
                 a_bitrate: str = "192k", muted: bool = False,
                 vertical: bool = False, mono_color: str = "",
                 parent=None):
        super().__init__(parent)
        self._midi = midi_path
        self._sf = sf_path
        self._output = output
        self._fps = fps
        self._w = width
        self._h = height
        self._v_codec = v_codec
        self._v_br = v_bitrate
        self._a_br = a_bitrate
        self._muted = muted
        self._vertical = vertical
        if mono_color:
            c = QtGui.QColor(mono_color)
            self._mono_rgb: tuple[int, int, int] | None = (
                c.red(), c.green(), c.blue())
        else:
            self._mono_rgb = None

    def run(self) -> None:
        import os, tempfile, subprocess, imageio, numpy as np
        from imageio_ffmpeg import get_ffmpeg_exe
        _ffmpeg = get_ffmpeg_exe()

        tmp_video: str | None = None
        tmp_audio: str | None = None
        try:
            pm = pretty_midi.PrettyMIDI(str(self._midi))
            dur = pm.get_end_time()
            total_frames = int(dur * self._fps)
            if total_frames < 1:
                self.error.emit("MIDI 时长太短")
                return

            # Determine layout based on orientation
            total_pitches = MAX_PITCH - MIN_PITCH + 1
            if self._vertical:
                key_h = int(self._h * 0.08)  # 10% of height for keyboard bar
                grid_h = self._h - key_h
                grid_w = self._w
                # Column widths for 88 pitches across full width
                note_w_col = grid_w // total_pitches
                extra_w = grid_w - total_pitches * note_w_col
            else:
                key_w = int(self._w * 0.025)  # 2.5% of width for keyboard strip
                note_h = self._h // total_pitches
                extra = self._h - total_pitches * note_h
                grid_w = self._w - key_w

            # ── Collect all notes ──────────────────────────────────────────
            all_notes: list[tuple[int, float, float, int]] = []  # (pitch, start, end, vel)
            for inst in pm.instruments:
                if inst.is_drum:
                    continue
                for note in inst.notes:
                    if MIN_PITCH <= note.pitch <= MAX_PITCH:
                        all_notes.append(
                            (note.pitch, note.start, note.end, note.velocity))

            # ── Pre-compute active notes per frame (event sweep) ────────────
            self.progress.emit("render", 0)
            # Build sorted event list: (time, delta(+1/-1), pitch, start, end, vel)
            events: list[tuple[float, int, int, float, float, int]] = []
            for (pitch, start, end, vel) in all_notes:
                events.append((start, +1, pitch, start, end, vel))
                events.append((end, -1, pitch, start, end, vel))
            events.sort(key=lambda e: e[0])

            frame_active_pitches: list[list[int]] = []
            frame_active_notes: list[
                list[tuple[int, float, float, int]]] = []  # (pitch, start, end, vel)

            active_pitches: set[int] = set()
            active_by_key: dict[tuple[int, float],
                                tuple[int, float, float, int]] = {}
            ev_idx = 0

            for i in range(total_frames):
                t = i / self._fps
                while ev_idx < len(events) and events[ev_idx][0] <= t:
                    _, delta, pitch, start, end, vel = events[ev_idx]
                    key = (pitch, start)
                    if delta == 1:
                        active_pitches.add(pitch)
                        active_by_key[key] = (pitch, start, end, vel)
                    else:
                        active_pitches.discard(pitch)
                        active_by_key.pop(key, None)
                    ev_idx += 1
                frame_active_pitches.append(sorted(active_pitches))
                frame_active_notes.append(list(active_by_key.values()))

            # ── Sort notes by start time for per-frame visible-note lookup ──
            all_notes.sort(key=lambda n: n[1])  # sort by start time
            note_idx_start = 0  # sliding-window start: notes that may have ended

            # ── ffmpeg / imageio writer setup ───────────────────────────────
            tmp_video = tempfile.mktemp(suffix=".mp4")
            use_vaapi = "vaapi" in self._v_codec

            if use_vaapi:
                ff_cmd = [
                    _ffmpeg, "-y",
                    "-f", "rawvideo", "-vcodec", "rawvideo",
                    "-s", f"{self._w}x{self._h}", "-pix_fmt", "rgba",
                    "-r", str(self._fps), "-i", "-",
                    "-vaapi_device", "/dev/dri/renderD128",
                    "-vf", "format=nv12,hwupload",
                    "-c:v", self._v_codec, "-b:v", self._v_br,
                    "-threads", "0", "-pix_fmt", "yuv420p",
                    tmp_video,
                ]
                ff_proc = subprocess.Popen(ff_cmd, stdin=subprocess.PIPE,
                                           stderr=subprocess.DEVNULL)
            else:
                writer = imageio.get_writer(
                    tmp_video, fps=self._fps, format="FFMPEG",
                    codec=self._v_codec, quality=None, bitrate=self._v_br,
                    pixelformat="yuv420p",
                    output_params=["-threads", "0"],
                    macro_block_size=1,
                )

            # ── Colour helper (mono or per-pitch) ────────────────────────────
            def _clr(pitch: int) -> QtGui.QColor:
                if self._mono_rgb is not None:
                    r, g, b = self._mono_rgb
                    return QtGui.QColor(r, g, b)
                return QtGui.QColor(_note_color(pitch))

            # ── Pre-render static base image (keyboard + grid backgrounds) ──
            base_img = QtGui.QImage(self._w, self._h,
                                    QtGui.QImage.Format.Format_RGBA8888)
            base_img.fill(QtGui.QColor(25, 25, 28))
            bp = QtGui.QPainter(base_img)
            bp.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)

            if self._vertical:
                # ── Vertical mode: grid stripes + bottom keyboard bar ───
                # Grid: vertical stripes (pitch columns)
                for pitch in range(MIN_PITCH, MAX_PITCH + 1):
                    idx = pitch - MIN_PITCH
                    col_w = note_w_col + 1 if idx < extra_w else note_w_col
                    sx = (idx * (note_w_col + 1) if idx < extra_w
                          else extra_w * (note_w_col + 1)
                               + (idx - extra_w) * note_w_col)
                    is_white = (pitch % 12) in _KEY_WHITE
                    c = (QtGui.QColor(38, 38, 42) if is_white
                         else QtGui.QColor(22, 22, 25))
                    bp.fillRect(QtCore.QRectF(sx, 0, col_w, grid_h), c)
                    bp.setPen(QtGui.QColor(45, 45, 50))
                    bp.drawLine(QtCore.QPointF(sx + col_w, 0),
                                QtCore.QPointF(sx + col_w, grid_h))
                # Keyboard: horizontal bar at the bottom
                for pitch in range(MIN_PITCH, MAX_PITCH + 1):
                    idx = pitch - MIN_PITCH
                    col_w = note_w_col + 1 if idx < extra_w else note_w_col
                    kx = (idx * (note_w_col + 1) if idx < extra_w
                          else extra_w * (note_w_col + 1)
                               + (idx - extra_w) * note_w_col)
                    is_white = (pitch % 12) in _KEY_WHITE
                    if is_white:
                        bp.fillRect(QtCore.QRectF(kx, grid_h, col_w, key_h),
                                    QtGui.QColor(245, 245, 245))
                    else:
                        bp.fillRect(QtCore.QRectF(kx, grid_h, col_w, key_h),
                                    QtGui.QColor(245, 245, 245))
                        bh = int(key_h * 0.6)
                        bp.fillRect(QtCore.QRectF(kx, grid_h, col_w, bh),
                                    QtGui.QColor(25, 25, 25))
                    bp.setPen(QtGui.QColor(180, 180, 180))
                    bp.drawLine(QtCore.QPointF(kx + col_w, grid_h),
                                QtCore.QPointF(kx + col_w, self._h))
                    if pitch % 12 == 0:
                        name = pretty_midi.note_number_to_name(pitch)
                        bp.setPen(QtGui.QColor(120, 120, 120))
                        font_sz = max(4, min(8, note_w_col // 2))
                        bp.setFont(QtGui.QFont("sans-serif", font_sz))
                        bp.drawText(
                            QtCore.QRectF(kx, grid_h + key_h * 3 // 5, col_w,
                                          max(4, key_h * 2 // 5 - 2)),
                            QtCore.Qt.AlignmentFlag.AlignCenter
                            | QtCore.Qt.TextFlag.TextSingleLine, name)
            else:
                # ── Horizontal mode: left keyboard strip + grid stripes ──
                for pitch in range(MIN_PITCH, MAX_PITCH + 1):
                    row_h_k = _dist_h(pitch, note_h, extra)
                    ky = _dist_y(pitch, note_h, extra)
                    is_white = (pitch % 12) in _KEY_WHITE
                    if is_white:
                        bp.fillRect(QtCore.QRectF(0, ky, key_w, row_h_k),
                                    QtGui.QColor(245, 245, 245))
                    else:
                        bp.fillRect(QtCore.QRectF(0, ky, key_w, row_h_k),
                                    QtGui.QColor(245, 245, 245))
                        bw = int(key_w * 0.6)
                        bp.fillRect(QtCore.QRectF(key_w - bw, ky, bw, row_h_k),
                                    QtGui.QColor(25, 25, 25))
                    bp.setPen(QtGui.QColor(180, 180, 180))
                    bp.drawLine(QtCore.QPointF(0, ky + row_h_k),
                                QtCore.QPointF(key_w, ky + row_h_k))
                    if pitch % 12 == 0:
                        name = pretty_midi.note_number_to_name(pitch)
                        bp.setPen(QtGui.QColor(120, 120, 120))
                        font_sz = max(4, min(7, note_h, key_w // 3))
                        bp.setFont(QtGui.QFont("sans-serif", font_sz))
                        bp.drawText(
                            QtCore.QRectF(2, ky, key_w - 4, row_h_k),
                            QtCore.Qt.AlignmentFlag.AlignVCenter
                            | QtCore.Qt.TextFlag.TextSingleLine, name)
                # Grid backgrounds (horizontal stripes)
                for pitch in range(MIN_PITCH, MAX_PITCH + 1):
                    y = _dist_y(pitch, note_h, extra)
                    row_h_g = _dist_h(pitch, note_h, extra)
                    is_white = (pitch % 12) in _KEY_WHITE
                    c = (QtGui.QColor(38, 38, 42) if is_white
                         else QtGui.QColor(22, 22, 25))
                    bp.fillRect(QtCore.QRectF(key_w, y, grid_w, row_h_g), c)
                    bp.setPen(QtGui.QColor(45, 45, 50))
                    bp.drawLine(QtCore.QPointF(key_w, y),
                                QtCore.QPointF(self._w, y))
            bp.end()

            # ── Pre-compute cached note drawing params (pixel coords) ───────
            # Fields: (pitch, start_t, end_t, vel, pos_x, width, pos_y, height,
            #          alpha, color)
            NoteDC = tuple[int, float, float, int, float, float, float,
                           float, int, QtGui.QColor]
            note_draw_cache: list[NoteDC] = []
            for (pitch, start, end, vel) in all_notes:
                alpha = int(80 + vel / 127 * 175)
                color = _clr(pitch)
                color.setAlpha(alpha)
                if self._vertical:
                    idx = pitch - MIN_PITCH
                    col_w = note_w_col + 1 if idx < extra_w else note_w_col
                    px = (idx * (note_w_col + 1) if idx < extra_w
                          else extra_w * (note_w_col + 1)
                               + (idx - extra_w) * note_w_col)
                    pw = col_w - max(2, col_w // 6)
                    px += max(1, col_w // 8)
                    py = start * 200
                    ph = max(3.0, end * 200 - start * 200)
                    note_draw_cache.append(
                        (pitch, start, end, vel, px, max(2, pw), py,
                         max(3.0, ph), alpha, color))
                else:
                    x = start * 200
                    w = max(3.0, end * 200 - x)
                    row_h_n = _dist_h(pitch, note_h, extra)
                    ny = _dist_y(pitch, note_h, extra) + max(1, row_h_n // 8)
                    nh = max(2, row_h_n - max(2, row_h_n // 6))
                    note_draw_cache.append(
                        (pitch, start, end, vel, x, w, ny, nh, alpha, color))

            # ── Frame loop ──────────────────────────────────────────────────
            if note_draw_cache:
                first = note_draw_cache[0]
                c = first[9]  # color is at index 9
            px_per_s = 200  # canonical px/s

            for i in range(total_frames):
                if self.isInterruptionRequested():
                    return
                t = i / self._fps

                # Copy static base (keyboard + grid) — 8 MB memcpy
                frame_img = base_img.copy()
                painter = QtGui.QPainter(frame_img)
                painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)

                if self._vertical:
                    # ── Vertical waterfall: future notes at top ↓ ─────────
                    # screen_y = 0  → time = t+grid_h/px_per_s (top)
                    # screen_y = grid_h → time = t        (keyboard, now)
                    # Note rect: top=END time, bottom=START time

                    # ── Draw visible notes ────────────────────────────
                    while (note_idx_start < len(note_draw_cache)
                           and note_draw_cache[note_idx_start][2] <= t):
                        note_idx_start += 1

                    for j in range(note_idx_start, len(note_draw_cache)):
                        (_p, start_ts, end_ts, _vel,
                         nx, nw, _ny, nh, _alpha, color) = note_draw_cache[j]
                        if start_ts > t + grid_h / px_per_s:
                            break  # entirely beyond visible future
                        # Top edge = END of note (further in the future)
                        screen_y = grid_h - (end_ts - t) * px_per_s
                        # Bottom edge = START of note; height = start-end
                        # screen_y can be < 0 (note extends above viewport)
                        if screen_y > grid_h or screen_y + nh < 0:
                            continue
                        draw_y = max(0.0, screen_y)
                        draw_h = (min(float(grid_h), screen_y + nh)
                                  - draw_y)
                        if draw_h <= 0:
                            continue
                        painter.fillRect(
                            QtCore.QRectF(nx, draw_y, nw, draw_h),
                            QtGui.QBrush(color))
                        painter.setPen(QtGui.QPen(color.darker(120), 1))
                        painter.drawRect(
                            QtCore.QRectF(nx, draw_y, nw, draw_h))

                    # ── Keyboard active-key highlights ─────────────────
                    for pitch in frame_active_pitches[i]:
                        idx = pitch - MIN_PITCH
                        col_w = (note_w_col + 1 if idx < extra_w
                                 else note_w_col)
                        kx = (idx * (note_w_col + 1) if idx < extra_w
                              else extra_w * (note_w_col + 1)
                                   + (idx - extra_w) * note_w_col)
                        is_white = (pitch % 12) in _KEY_WHITE
                        bg = (QtGui.QColor(180, 180, 180) if is_white
                              else QtGui.QColor(55, 55, 55))
                        if is_white:
                            painter.fillRect(
                                QtCore.QRectF(kx, grid_h, col_w, key_h), bg)
                            if pitch % 12 == 0:
                                name = pretty_midi.note_number_to_name(pitch)
                                painter.setPen(QtGui.QColor(255, 255, 255))
                                font_sz = max(4, min(8, note_w_col // 2))
                                painter.setFont(QtGui.QFont("sans-serif", font_sz))
                                painter.drawText(
                                    QtCore.QRectF(kx, grid_h + key_h * 3 // 5,
                                                  col_w, max(4, key_h * 2 // 5 - 2)),
                                    QtCore.Qt.AlignmentFlag.AlignCenter
                                    | QtCore.Qt.TextFlag.TextSingleLine, name)
                        else:
                            bh_k = int(key_h * 0.6)
                            painter.fillRect(
                                QtCore.QRectF(kx, grid_h, col_w, bh_k), bg)

                    # ── Active-note white highlights on grid ───────────
                    for (_p, start_ts, end_ts, _vel) in frame_active_notes[i]:
                        idx = _p - MIN_PITCH
                        col_w = (note_w_col + 1 if idx < extra_w
                                 else note_w_col)
                        nx_v = (idx * (note_w_col + 1) if idx < extra_w
                                else extra_w * (note_w_col + 1)
                                     + (idx - extra_w) * note_w_col)
                        nw_v = col_w
                        # Top edge = END of note (further in future)
                        screen_y = grid_h - (end_ts - t) * px_per_s
                        nh_v = max(3.0, (end_ts - start_ts) * px_per_s)
                        draw_y = max(0.0, screen_y)
                        draw_h = (min(float(grid_h), screen_y + nh_v)
                                  - draw_y)
                        if draw_h <= 0:
                            continue
                        hl = QtCore.QRectF(nx_v, draw_y, nw_v, draw_h)
                        painter.fillRect(
                            hl, QtGui.QColor(255, 255, 255, 180))
                        painter.setPen(
                            QtGui.QPen(QtGui.QColor(255, 255, 255), 2))
                        painter.drawRect(hl)

                else:
                    # ── Horizontal mode ──────────────────────────────────
                    tx = int(t * px_per_s)

                    # ── Draw visible notes (sliding window) ────────────
                    while (note_idx_start < len(note_draw_cache)
                           and note_draw_cache[note_idx_start][2] <= t):
                        note_idx_start += 1

                    for j in range(note_idx_start, len(note_draw_cache)):
                        _, start_ts, end_ts, _vel, nx, nw, ny, nh, \
                            _alpha, color = note_draw_cache[j]
                        if start_ts > t + grid_w / px_per_s:
                            break
                        if end_ts <= t:
                            continue
                        screen_x = key_w + nx - tx
                        if (screen_x + nw < key_w
                                or screen_x > self._w):
                            continue
                        draw_x = max(float(key_w), screen_x)
                        draw_w = (min(float(self._w), screen_x + nw)
                                  - draw_x)
                        if draw_w <= 0:
                            continue
                        painter.fillRect(
                            QtCore.QRectF(draw_x, ny, draw_w, nh),
                            QtGui.QBrush(color))
                        painter.setPen(QtGui.QPen(color.darker(120), 1))
                        painter.drawRect(
                            QtCore.QRectF(draw_x, ny, draw_w, nh))

                    # ── Keyboard active-key highlights ─────────────────
                    for pitch in frame_active_pitches[i]:
                        row_h_k = _dist_h(pitch, note_h, extra)
                        ky = _dist_y(pitch, note_h, extra)
                        is_white = (pitch % 12) in _KEY_WHITE
                        bg = (QtGui.QColor(180, 180, 180) if is_white
                              else QtGui.QColor(55, 55, 55))
                        if is_white:
                            painter.fillRect(
                                QtCore.QRectF(0, ky, key_w, row_h_k), bg)
                            if pitch % 12 == 0:
                                name = pretty_midi.note_number_to_name(pitch)
                                painter.setPen(QtGui.QColor(255, 255, 255))
                                font_sz = max(4, min(7, note_h, key_w // 3))
                                painter.setFont(
                                    QtGui.QFont("sans-serif", font_sz))
                                painter.drawText(
                                    QtCore.QRectF(2, ky, key_w - 4, row_h_k),
                                    QtCore.Qt.AlignmentFlag.AlignVCenter
                                    | QtCore.Qt.TextFlag.TextSingleLine,
                                    name)
                        else:
                            bw = int(key_w * 0.6)
                            painter.fillRect(
                                QtCore.QRectF(key_w - bw, ky, bw, row_h_k),
                                bg)

                    # ── Active-note white highlights on grid ───────────
                    for (_p, start_ts, end_ts,
                         _vel) in frame_active_notes[i]:
                        row_h_n = _dist_h(_p, note_h, extra)
                        ny_h = (_dist_y(_p, note_h, extra)
                                + max(1, row_h_n // 8))
                        nh_h = max(2, row_h_n - max(2, row_h_n // 6))
                        nx_s = key_w + start_ts * px_per_s - tx
                        nw_s = max(3.0, end_ts * px_per_s
                                        - start_ts * px_per_s)
                        draw_x = max(float(key_w), nx_s)
                        draw_w = (min(float(self._w), nx_s + nw_s)
                                  - draw_x)
                        if draw_w <= 0:
                            continue
                        hl = QtCore.QRectF(draw_x, ny_h, draw_w, nh_h)
                        painter.fillRect(
                            hl, QtGui.QColor(255, 255, 255, 180))
                        painter.setPen(
                            QtGui.QPen(QtGui.QColor(255, 255, 255), 2))
                        painter.drawRect(hl)

                painter.end()

                # ── Encode ──────────────────────────────────────────────
                ptr = frame_img.constBits()
                arr = np.array(ptr).reshape(self._h, self._w, 4)
                if use_vaapi:
                    ff_proc.stdin.write(arr.tobytes())
                else:
                    writer.append_data(arr[..., :3])

                # Progress (0-100 %)
                if i % max(1, total_frames // 100) == 0:
                    self.progress.emit("render",
                                       int(i / total_frames * 100))

            if use_vaapi:
                ff_proc.stdin.close()
                ff_proc.wait()
                if ff_proc.returncode != 0:
                    raise RuntimeError("ffmpeg VAAPI 编码失败")
            else:
                writer.close()
            self.progress.emit("render", 100)

            # ── Audio (if not muted) ─────────────────────────────────────
            if not self._muted and self._sf:
                self.progress.emit("audio", 0)
                tmp_audio = _render_audio_sync(
                    self._midi, self._sf, self._a_br,
                    progress_cb=lambda pct: self.progress.emit("audio", pct),
                )

            # ── Mux or finalize ──────────────────────────────────────────
            if tmp_audio:
                self.progress.emit("mux", 0)
                cmd = [
                    _ffmpeg, "-y",
                    "-i", tmp_video, "-i", tmp_audio,
                    "-c:v", "copy", "-c:a", "aac", "-b:a", self._a_br,
                    "-threads", "0", "-progress", "pipe:1",
                    "-shortest", "-nostats", self._output,
                ]
                proc = subprocess.Popen(
                    cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    text=True, encoding="utf-8", errors="replace",
                )
                # Parse ffmpeg -progress stdout for "out_time_us=" lines
                last_pct = 0
                dur_us = int(dur * 1_000_000)
                for line in proc.stdout:
                    if line.startswith("out_time_us="):
                        try:
                            cur_us = int(line.split("=", 1)[1].strip())
                            pct = (min(cur_us, dur_us) * 100
                                   // max(dur_us, 1))
                            if pct > last_pct:
                                self.progress.emit("mux", pct)
                                last_pct = pct
                        except ValueError:
                            pass
                proc.wait()
                if proc.returncode != 0:
                    err = proc.stderr.read()
                    raise RuntimeError(
                        f"ffmpeg 合成失败 (code {proc.returncode}):\n"
                        f"{err[:500]}")
            else:
                os.rename(tmp_video, self._output)
                tmp_video = None

            self.progress.emit("mux", 100)
            self.progress.emit("done", 100)
            self.finished.emit(self._output)

        except Exception as exc:
            self.error.emit(str(exc))
        finally:
            for p in (tmp_video, tmp_audio):
                if p and os.path.exists(p):
                    os.unlink(p)


def _render_audio_sync(midi_path: str, sf_path: str, bitrate: str,
                       progress_cb=None) -> str:
    """Render MIDI→WAV synchronously, return path to WAV file.

    If *progress_cb* is given it is called as ``progress_cb(pct)``
    with ``pct`` in 0–100.
    """
    import wave
    import os
    import numpy as np
    import fluidsynth
    import tempfile

    pm = pretty_midi.PrettyMIDI(str(midi_path))
    duration = pm.get_end_time()
    RATE = 44100

    fs = fluidsynth.Synth()
    sfid = fs.sfload(sf_path)
    fs.program_select(0, sfid, 0, 0)

    events: list[tuple[float, str, int, int]] = []
    for inst in pm.instruments:
        if inst.is_drum:
            continue
        for note in inst.notes:
            events.append((note.start, "on", note.pitch, note.velocity))
            events.append((note.end, "off", note.pitch, 0))
    events.sort(key=lambda e: e[0])

    total_samples = int(RATE * (duration + 1.5))
    audio = np.empty(total_samples, dtype=np.int16)
    ev_idx = 0
    t = 0.0
    dt = 0.05
    last_pct = -1
    total_steps = (duration + 1.0) / dt
    step = 0
    samp_pos = 0
    while t < duration + 1.0:
        while ev_idx < len(events) and events[ev_idx][0] <= t:
            _time, kind, pitch, vel = events[ev_idx]
            if kind == "on":
                fs.noteon(0, pitch, vel)
            else:
                fs.noteoff(0, pitch)
            ev_idx += 1
        n = int(RATE * dt)
        samples = fs.get_samples(n)
        mono = samples[:n * 2:2]
        m = len(mono)
        if samp_pos + m > len(audio):
            audio = np.append(audio, np.empty(
                max(m, int(RATE * 0.5)), dtype=np.int16))
        audio[samp_pos:samp_pos + m] = mono
        samp_pos += m
        t += dt
        step += 1
        if progress_cb:
            pct = int(step / total_steps * 100)
            if pct > last_pct:
                progress_cb(pct)
                last_pct = pct

    audio = audio[:samp_pos]
    if progress_cb:
        progress_cb(100)

    fd, tmp = tempfile.mkstemp(suffix=".wav")
    os.close(fd)
    with wave.open(tmp, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(RATE)
        wf.writeframes(audio.tobytes())

    fs.delete()
    return tmp


# ═══════════════════════════════════════════════════════════════════════════
# Audio export worker
# ═══════════════════════════════════════════════════════════════════════════

class AudioExportWorker(QtCore.QThread):
    """Render MIDI + SoundFont → audio using pyfluidsynth + pydub.
    Uses numpy.append (per pyfluidsynth docs) and writes mono WAV, then
    converts to target format via pydub."""

    progress = QtCore.Signal(str, int)  # (message, pct 0-100 or -1)
    finished = QtCore.Signal(str)
    error = QtCore.Signal(str)

    def __init__(self, midi_path: str, sf_path: str, output_path: str,
                 bitrate: str = "192k", parent=None):
        super().__init__(parent)
        self._midi = midi_path
        self._sf = sf_path
        self._output = output_path
        self._bitrate = bitrate

    def run(self) -> None:
        import numpy as np
        import wave
        import tempfile
        import os
        import fluidsynth

        fs: fluidsynth.Synth | None = None
        tmp_wav: str | None = None
        try:
            self.progress.emit("正在加载 SoundFont…", -1)
            pm = pretty_midi.PrettyMIDI(str(self._midi))
            duration = pm.get_end_time()
            RATE = 44100

            # Synth() takes NO params (per pyfluidsynth docs)
            fs = fluidsynth.Synth()
            sfid = fs.sfload(self._sf)
            fs.program_select(0, sfid, 0, 0)  # required — selects piano preset

            # Collect note events
            events: list[tuple[float, str, int, int]] = []
            for inst in pm.instruments:
                if inst.is_drum:
                    continue
                for note in inst.notes:
                    events.append((note.start, "on", note.pitch, note.velocity))
                    events.append((note.end, "off", note.pitch, 0))
            events.sort(key=lambda e: e[0])

            self.progress.emit("正在渲染音频…", 0)
            total_samples = int(RATE * (duration + 1.5))
            audio = np.empty(total_samples, dtype=np.int16)
            ev_idx = 0
            t = 0.0
            dt = 0.05  # 50ms steps
            samp_pos = 0
            last_pct = -1
            total_steps = (duration + 1.0) / dt
            step = 0

            while t < duration + 1.0:
                if self.isInterruptionRequested():
                    return
                while ev_idx < len(events) and events[ev_idx][0] <= t:
                    _time, kind, pitch, vel = events[ev_idx]
                    if kind == "on":
                        fs.noteon(0, pitch, vel)
                    else:
                        fs.noteoff(0, pitch)
                    ev_idx += 1

                n = int(RATE * dt)
                samples = fs.get_samples(n)  # int16, shape (2*n,) interlaced stereo
                # Take left channel → mono
                mono = samples[:n * 2:2]
                m = len(mono)
                if samp_pos + m > len(audio):
                    audio = np.append(audio, np.empty(
                        max(m, int(RATE * 0.5)), dtype=np.int16))
                audio[samp_pos:samp_pos + m] = mono
                samp_pos += m
                t += dt
                step += 1
                pct = int(step / total_steps * 100)
                if pct > last_pct:
                    self.progress.emit("正在渲染音频…", pct)
                    last_pct = pct
            audio = audio[:samp_pos]
            self.progress.emit("正在渲染音频…", 100)

            # Write WAV via stdlib (no deps)
            self.progress.emit("正在写入 WAV…", -1)
            tmp_wav = tempfile.mktemp(suffix=".wav")
            with wave.open(tmp_wav, "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)  # int16
                wf.setframerate(RATE)
                wf.writeframes(audio.tobytes())

            # Convert to target format via pydub
            out_fmt = Path(self._output).suffix.lstrip(".").lower()
            if out_fmt == "wav":
                os.rename(tmp_wav, self._output)
                tmp_wav = None
            else:
                self.progress.emit(f"正在转换为 {out_fmt.upper()}…", -1)
                from pydub import AudioSegment
                seg = AudioSegment.from_wav(tmp_wav)
                seg.export(self._output, format=out_fmt, bitrate=self._bitrate)

            self.finished.emit(self._output)

        except Exception as exc:
            self.error.emit(str(exc))
        finally:
            if fs is not None:
                try:
                    fs.delete()
                except Exception:
                    pass
            if tmp_wav and os.path.exists(tmp_wav):
                os.unlink(tmp_wav)


# ═══════════════════════════════════════════════════════════════════════════
# Clickable slider
# ═══════════════════════════════════════════════════════════════════════════

class _SeekSlider(QtWidgets.QSlider):
    clicked = QtCore.Signal(int)

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
        if self.orientation() == QtCore.Qt.Orientation.Horizontal:
            val = (self.minimum() +
                   (self.maximum() - self.minimum()) *
                   event.position().x() / self.width())
        else:
            val = (self.minimum() +
                   (self.maximum() - self.minimum()) *
                   (1 - event.position().y() / self.height()))
        self.clicked.emit(int(val))
        super().mousePressEvent(event)


# ═══════════════════════════════════════════════════════════════════════════
# Main window
# ═══════════════════════════════════════════════════════════════════════════

_SOUNDFONT_DIR = Path(__file__).resolve().parents[3] / "soundfonts"


class PianoRollWindow(QtWidgets.QWidget):
    """Standalone piano-roll preview and export window."""

    def __init__(self, midi_path: str | Path = "", parent=None):
        super().__init__(parent)
        self.setWindowTitle("钢琴卷帘预览")
        self._base_title = self.windowTitle()
        self.resize(1400, 800)
        self._midi_path = str(midi_path) if midi_path else ""

        self._player = MidiPlayer(self)
        self._playing = False
        self._seek_dragging = False
        self._mono_hex = "#de8400"
        self._fit_timer: QtCore.QTimer | None = None
        self._fullscreen = False
        self._taskbar = TaskbarProgress(self)

        # System-tray icon for cross-platform export-done notifications
        self._tray = QtWidgets.QSystemTrayIcon(self)
        self._tray.setIcon(self.style().standardIcon(
            QtWidgets.QStyle.StandardPixmap.SP_MediaPlay))
        self._tray.setToolTip("PianoLoTayu")
        self._tray.show()

        # ── Top controls ────────────────────────────────────────────────
        self._sf_combo = QtWidgets.QComboBox(self)
        self._sf_combo.addItem("（无 SoundFont — 静音预览）", "")
        for sf in sorted(_SOUNDFONT_DIR.glob("*.sf2")):
            self._sf_combo.addItem(sf.name, str(sf))
        if self._sf_combo.count() > 1:
            self._sf_combo.setCurrentIndex(1)
        self._sf_combo.currentIndexChanged.connect(self._on_sf_changed)

        self._btn_play = QtWidgets.QPushButton("▶ 播放")
        self._btn_export = QtWidgets.QPushButton("🎬 导出视频")
        self._btn_export_audio = QtWidgets.QPushButton("🔊 导出音频")

        self._mono_cb = QtWidgets.QCheckBox("单色")
        self._mono_color_btn = QtWidgets.QPushButton()
        self._mono_color_btn.setFixedSize(24, 24)
        self._mono_color_btn.setStyleSheet(
            "background: #de8400; border: 1px solid #999; border-radius: 3px;")
        self._mono_color_btn.clicked.connect(self._on_pick_mono_color)

        self._btn_fullscreen = QtWidgets.QPushButton("⛶ 全屏")
        self._btn_fullscreen.setMaximumWidth(60)
        self._btn_fullscreen.setToolTip("全屏 (Esc 或双击鼠标退出)")
        self._btn_fullscreen.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)

        self._vertical_cb = QtWidgets.QCheckBox("竖向")

        self._export_progress = QtWidgets.QProgressBar(self)
        self._export_progress.setVisible(False)
        self._export_progress.setMaximum(100)
        self._export_progress.setMaximumWidth(120)

        # Zoom sliders
        hz_label = QtWidgets.QLabel("缩放:")
        self._h_zoom = QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal)
        self._h_zoom.setRange(40, 600)
        self._h_zoom.setValue(200)
        self._h_zoom.setMaximumWidth(200)
        self._h_zoom.setTickPosition(QtWidgets.QSlider.TickPosition.TicksBelow)
        self._h_zoom.setTickInterval(80)

        top = QtWidgets.QHBoxLayout()
        top.addWidget(QtWidgets.QLabel("SoundFont:"))
        top.addWidget(self._sf_combo)
        top.addSpacing(12)
        top.addWidget(self._btn_play)
        top.addSpacing(12)
        top.addWidget(self._btn_export)
        top.addWidget(self._btn_export_audio)
        top.addWidget(self._export_progress)
        top.addStretch()
        top.addWidget(self._vertical_cb)
        top.addSpacing(12)
        top.addWidget(self._mono_cb)
        top.addWidget(self._mono_color_btn)
        top.addSpacing(12)
        top.addWidget(hz_label)
        top.addWidget(self._h_zoom)
        top.addWidget(self._btn_fullscreen)

        # ── Keyboard + note grid ────────────────────────────────────────
        self._keyboard = KeyboardWidget(self)
        self._grid = NoteGridView(self)

        # Outer container (always QVBoxLayout) — allows both orientations
        self._mid_area = QtWidgets.QVBoxLayout()
        self._mid_area.setSpacing(0)
        self._mid_area.setContentsMargins(0, 0, 0, 0)
        # Horizontal-mode inner layout (default)
        _h_inner = QtWidgets.QHBoxLayout()
        _h_inner.setSpacing(0)
        _h_inner.setContentsMargins(0, 0, 0, 0)
        _h_inner.addWidget(self._keyboard)
        _h_inner.addWidget(self._grid, 1)
        self._mid_area.addLayout(_h_inner)

        self._grid.vertical_offset_changed.connect(self._keyboard.set_offset_y)
        self._grid.horizontal_offset_changed.connect(self._on_grid_h_scroll)

        # ── Bottom seek bar ─────────────────────────────────────────────
        self._seek_bar = _SeekSlider(QtCore.Qt.Orientation.Horizontal, self)
        self._seek_bar.setRange(0, 1000)
        self._seek_bar.sliderPressed.connect(
            lambda: setattr(self, '_seek_dragging', True))
        self._seek_bar.sliderReleased.connect(self._on_seek_release)
        self._seek_bar.sliderMoved.connect(self._on_seek_drag)
        self._seek_bar.clicked.connect(self._on_seek_click)
        self._time_label = QtWidgets.QLabel("00:00 / 00:00")

        bottom = QtWidgets.QHBoxLayout()
        bottom.addWidget(self._seek_bar, 1)
        bottom.addWidget(self._time_label)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        # Wrap top/bottom in QWidget containers so they can be hidden
        self._top_bar = QtWidgets.QWidget(self)
        self._top_bar.setLayout(top)
        self._bottom_bar = QtWidgets.QWidget(self)
        self._bottom_bar.setLayout(bottom)
        layout.addWidget(self._top_bar)
        layout.addLayout(self._mid_area, 1)
        layout.addWidget(self._bottom_bar)

        # ── Signals ─────────────────────────────────────────────────────
        self._btn_play.clicked.connect(self._on_play_pause)
        self._btn_export.clicked.connect(self._on_export)
        self._btn_export_audio.clicked.connect(self._on_export_audio)
        self._h_zoom.valueChanged.connect(self._on_h_zoom)
        self._mono_cb.toggled.connect(self._on_mono_toggle)
        self._vertical_cb.toggled.connect(self._on_vertical_toggle)
        self._btn_fullscreen.clicked.connect(self._on_fullscreen)
        self._player.position.connect(self._on_position)
        self._player.active_notes_changed.connect(self._on_active_notes_changed)
        self._player.finished.connect(self._on_playback_finished)

        # ── Install event filter so Space always maps to play/pause,
        # even when a QComboBox / QPushButton / QSlider has keyboard focus.
        # Must be on QApplication, not self — child widgets receive events
        # directly, bypassing the parent's filter.
        QtWidgets.QApplication.instance().installEventFilter(self)

        # ── Load ────────────────────────────────────────────────────────
        if self._midi_path:
            self._grid.load(self._midi_path)
            self.setWindowTitle(f"{self._base_title} - {Path(self._midi_path).name}")
            dur = self._grid.scene_duration_s()
            self._time_label.setText(f"00:00 / {self._fmt_time(dur)}")

    # ── Event filter (Space → play/pause globally) ─────────────────────
    def eventFilter(self, obj: QtCore.QObject, event: QtCore.QEvent) -> bool:
        if event.type() == QtCore.QEvent.Type.KeyPress:
            key_event = event  # type: QtGui.QKeyEvent
            if key_event.key() == QtCore.Qt.Key.Key_Space:
                if QtWidgets.QApplication.activeModalWidget() is not None:
                    return False
                self._on_play_pause()
                return True
            if key_event.key() == QtCore.Qt.Key.Key_Escape and self._fullscreen:
                self._on_fullscreen()
                return True
        if event.type() == QtCore.QEvent.Type.MouseButtonDblClick:
            # Only toggle on double-click in the piano roll area (grid or keyboard)
            if obj is self._grid or obj is self._grid.viewport() or obj is self._keyboard:
                self._on_fullscreen()
                return True
        return False

    def _on_sf_changed(self, _index: int) -> None:
        """Hot-swap SoundFont during playback."""
        sf = self._sf_combo.currentData() or ""
        if sf and self._player._synth is not None:
            sfid = self._player._synth.sfload(sf)
            self._player._synth.program_select(0, sfid, 0, 0)

    def _on_fullscreen(self) -> None:
        """Toggle fullscreen: hide controls, show only the piano roll."""
        self._fullscreen = not self._fullscreen
        self._top_bar.setVisible(not self._fullscreen)
        self._bottom_bar.setVisible(not self._fullscreen)
        if self._fullscreen:
            self.showFullScreen()
        else:
            self.showNormal()

    def _on_play_pause(self) -> None:
        if self._playing:
            self._player.pause()
            self._playing = False
            self._btn_play.setText("▶ 播放")
        else:
            if self._midi_path:
                self._player.load_midi(self._midi_path)
            if self._player._midi is None:
                QtWidgets.QMessageBox.warning(self, "提示", "没有加载 MIDI 文件")
                return
            sf = self._sf_combo.currentData()
            if sf and self._player._synth is None:
                if not self._player.load_soundfont(sf):
                    QtWidgets.QMessageBox.warning(self, "提示",
                        f"无法加载 SoundFont：{sf}")
                    return
            elif not sf:
                QtWidgets.QMessageBox.warning(self, "提示",
                    "未选择 SoundFont，将静音播放\n请从下拉框中选择一个 .sf2 文件")
                # continue with silent preview
            dur = self._grid.scene_duration_s()
            t = self._seek_bar.value() / 1000.0 * dur if dur > 0 else 0.0
            self._player.play(sf_path=sf or "", start_t=t)
            self._playing = True
            self._btn_play.setText("⏸ 暂停")

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        self._player.cleanup()
        for w in ("_video_worker", "_audio_worker"):
            worker = getattr(self, w, None)
            if worker is not None:
                try:
                    if worker.isRunning():
                        worker.requestInterruption()
                        worker.wait(5000)
                except RuntimeError:
                    pass  # C++ object already deleted by deleteLater
        if self._tray is not None:
            self._tray.hide()
        super().closeEvent(event)

    def showEvent(self, event: QtGui.QShowEvent) -> None:
        super().showEvent(event)
        self._fit_v_zoom()

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:
        super().resizeEvent(event)
        self._fit_v_zoom()

    def _fit_v_zoom(self) -> None:
        """Debounced: only recalc zoom after resize pauses ≥ 30 ms."""
        if self._fit_timer is None:
            self._fit_timer = QtCore.QTimer(self)
            self._fit_timer.setSingleShot(True)
            self._fit_timer.timeout.connect(self._do_fit_zoom)
        self._fit_timer.start(30)

    def _do_fit_zoom(self) -> None:
        """Recalculate zoom so content fills the viewport exactly."""
        if self._grid._scene._vertical:
            self._do_fit_h_zoom()
        else:
            self._do_fit_v_zoom()

    def _do_fit_v_zoom(self) -> None:
        h = self._grid.viewport().height()
        if h <= 0:
            h = self._grid.height() - 4
        if h > 0:
            total = MAX_PITCH - MIN_PITCH + 1
            note_h = max(1, h // total)
            extra = h - total * note_h
            self._grid.set_v_zoom(note_h, extra)
            self._keyboard.set_note_h(note_h, extra)

    def _do_fit_h_zoom(self) -> None:
        """Vertical-mode: fit 88 columns to viewport width."""
        w = self._grid.viewport().width()
        if w <= 0:
            w = self._grid.width() - 4
        if w > 0:
            total = MAX_PITCH - MIN_PITCH + 1
            note_w = max(1, w // total)
            extra_w = w - total * note_w
            self._grid.set_h_fit(note_w, extra_w)
            self._keyboard.set_h_fit(note_w, extra_w)

    def _on_vertical_toggle(self, checked: bool) -> None:
        """Switch between horizontal (default) and vertical waterfall mode."""
        self._grid.set_orientation(checked)
        self._keyboard.set_orientation(checked)
        # Clear and rebuild mid_area
        while self._mid_area.count():
            self._mid_area.takeAt(0)
        if checked:
            # Vertical: grid on top (fills space), keyboard bar at bottom
            self._mid_area.addWidget(self._grid, 1)
            self._mid_area.addWidget(self._keyboard)
        else:
            # Horizontal: keyboard on left, grid fills space (identical to original)
            inner = QtWidgets.QHBoxLayout()
            inner.setSpacing(0)
            inner.setContentsMargins(0, 0, 0, 0)
            inner.addWidget(self._keyboard)
            inner.addWidget(self._grid, 1)
            self._mid_area.addLayout(inner)
        self._mid_area.activate()
        QtCore.QCoreApplication.processEvents()
        if checked:
            self._do_fit_h_zoom()
        else:
            self._do_fit_v_zoom()
        self._grid.set_h_zoom(self._h_zoom.value())
        dur = self._grid.scene_duration_s()
        t = self._seek_bar.value() / 1000.0 * dur if dur > 0 else 0.0
        self._grid.scroll_to_time(t)

        if not checked:
            self._keyboard.set_offset_y(0)

        kw = self._keyboard
        kw.repaint()

    def _on_active_notes_changed(self, notes: list[tuple[int, float]]) -> None:
        """Highlight active notes in the grid and keyboard."""
        self._grid._scene.set_active_notes(notes)
        self._keyboard.set_active_notes(notes)

    def _on_playback_finished(self) -> None:
        self._playing = False
        self._btn_play.setText("▶ 播放")

    def _on_h_zoom(self, val: int) -> None:
        self._grid.set_h_zoom(val)  # QTransform only, no rebuild in either mode

    def _on_grid_h_scroll(self, x: int) -> None:
        """Seek bar tracks horizontal drags on the piano roll."""
        dur = self._grid.scene_duration_s()
        if dur > 0 and not self._seek_dragging:
            t = self._grid.x_to_time(x)
            self._seek_bar.setValue(int(t / dur * 1000))
            self._time_label.setText(f"{self._fmt_time(t)} / {self._fmt_time(dur)}")

    @staticmethod
    def _fmt_time(seconds: float) -> str:
        m, s = divmod(int(seconds), 60)
        return f"{m:02d}:{s:02d}"

    def _on_position(self, t: float) -> None:
        dur = self._grid.scene_duration_s()
        self._time_label.setText(f"{self._fmt_time(t)} / {self._fmt_time(dur)}")
        if not self._seek_dragging and dur > 0:
            self._seek_bar.setValue(int(t / dur * 1000))
        # Auto-scroll: playhead at bottom (vertical) / left edge (horizontal)
        if self._playing and not self._seek_dragging:
            if self._grid._scene._vertical:
                view_y = self._grid._view_time_to_y(t)
                vp_h = self._grid.viewport().height()
                self._grid.verticalScrollBar().setValue(max(0, int(view_y - vp_h)))
            else:
                x = self._grid.time_to_x(t)
                self._grid.horizontalScrollBar().setValue(int(max(0, x)))

    def _on_seek_drag(self, val: int) -> None:
        dur = self._grid.scene_duration_s()
        if dur > 0:
            t = val / 1000.0 * dur
            self._grid.scroll_to_time(t)
            self._time_label.setText(f"{self._fmt_time(t)} / {self._fmt_time(dur)}")

    def _on_seek_click(self, val: int) -> None:
        dur = self._grid.scene_duration_s()
        if dur > 0:
            t = val / 1000.0 * dur
            self._seek_bar.setValue(val)
            self._grid.scroll_to_time(t)
            self._player.seek(t)

    def _on_seek_release(self) -> None:
        self._seek_dragging = False
        dur = self._grid.scene_duration_s()
        if dur > 0:
            t = self._seek_bar.value() / 1000.0 * dur
            self._player.seek(t)

    def _on_mono_toggle(self, checked: bool) -> None:
        self._grid.set_mono(self._mono_hex if checked else None)

    def _on_pick_mono_color(self) -> None:
        c = QtWidgets.QColorDialog.getColor(
            QtGui.QColor(self._mono_hex), self, "选择音符颜色")
        if c.isValid():
            self._mono_hex = c.name()
            self._mono_color_btn.setStyleSheet(
                f"background: {self._mono_hex}; "
                f"border: 1px solid #999; border-radius: 3px;")
            if self._mono_cb.isChecked():
                self._grid.set_mono(self._mono_hex)

    # ── Video export ────────────────────────────────────────────────────
    def _on_export(self) -> None:
        """Open video export dialog, then start rendering."""
        dlg = QtWidgets.QDialog(self)
        dlg.setWindowTitle("导出视频")
        dlg.setMinimumWidth(240)

        # ── Output path ──────────────────────────────────────────────────
        path_row = QtWidgets.QHBoxLayout()
        path_edit = QtWidgets.QLineEdit(dlg)
        path_edit.setReadOnly(True)
        path_edit.setPlaceholderText("选择输出位置…")
        btn_browse = QtWidgets.QPushButton("浏览…", dlg)
        btn_browse.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)
        path_row.addWidget(path_edit)
        path_row.addWidget(btn_browse)

        # ── Preset defaults ──────────────────────────────────────────────
        midi = Path(self._midi_path) if self._midi_path else Path("output")
        path_edit.setText(str(midi.parent / f"{midi.stem}_video.mp4"))

        # Browse
        _V_FILTERS = "MP4 (*.mp4);;MKV (*.mkv);;WebM (*.webm);;所有文件 (*)"
        def browse() -> None:
            p, _ = QtWidgets.QFileDialog.getSaveFileName(
                dlg, "导出视频", path_edit.text(), _V_FILTERS)
            if p:
                path_edit.setText(p)
        btn_browse.clicked.connect(browse)

        # ── Resolution ───────────────────────────────────────────────────
        res_row = QtWidgets.QHBoxLayout()
        res_combo = QtWidgets.QComboBox(dlg)
        res_combo.addItems(["1920x1080 (1080p)", "1280x720 (720p)", "854x480 (480p)"])
        res_combo.setCurrentIndex(0)
        res_row.addWidget(QtWidgets.QLabel("分辨率:"))
        res_row.addWidget(res_combo, 1)

        # ── FPS ──────────────────────────────────────────────────────────
        fps_row = QtWidgets.QHBoxLayout()
        fps_combo = QtWidgets.QComboBox(dlg)
        fps_combo.addItems(["30", "24", "60"])
        fps_combo.setCurrentIndex(0)
        fps_row.addWidget(QtWidgets.QLabel("帧率:"))
        fps_row.addWidget(fps_combo, 1)

        # ── Codec ────────────────────────────────────────────────────────
        codec_row = QtWidgets.QHBoxLayout()
        codec_combo = QtWidgets.QComboBox(dlg)
        # (label, codec, container_ext)
        _ALL_CODECS = [
            ("H.264 (.mp4)",  "libx264", ".mp4"),
            ("H.265 (.mp4)",  "libx265", ".mp4"),
            ("AV1 (.mp4)",    "libsvtav1", ".mp4"),
            ("H.264 (.mkv)",  "libx264", ".mkv"),
            ("H.265 (.mkv)",  "libx265", ".mkv"),
            ("AV1 (.webm)",   "libsvtav1", ".webm"),
        ]
        # Filter to encoders actually available in ffmpeg
        _CODECS = _filter_available_codecs(_ALL_CODECS)
        for label, *_ in _CODECS:
            codec_combo.addItem(label)
        codec_combo.setCurrentIndex(0)
        codec_row.addWidget(QtWidgets.QLabel("编码:"))
        codec_row.addWidget(codec_combo, 1)

        # ── SoundFont ────────────────────────────────────────────────────
        sf_row = QtWidgets.QHBoxLayout()
        sf_combo_export = QtWidgets.QComboBox(dlg)
        sf_combo_export.addItem("（无 SoundFont — 静音）", "")
        for sf_path in sorted(_SOUNDFONT_DIR.glob("*.sf2")):
            sf_combo_export.addItem(sf_path.name, str(sf_path))
        # Default to same SF as preview window, or first available
        _cur_sf = self._sf_combo.currentData()
        if _cur_sf:
            idx = sf_combo_export.findData(_cur_sf)
            if idx >= 0:
                sf_combo_export.setCurrentIndex(idx)
        elif sf_combo_export.count() > 1:
            sf_combo_export.setCurrentIndex(1)
        sf_row.addWidget(QtWidgets.QLabel("SoundFont:"))
        sf_row.addWidget(sf_combo_export, 1)

        # ── Orientation + colour + audio ───────────────────────────────────
        style_row = QtWidgets.QHBoxLayout()
        mute_cb = QtWidgets.QCheckBox("静音", dlg)
        mute_cb.setChecked(False)
        vertical_cb = QtWidgets.QCheckBox("竖向", dlg)
        vertical_cb.setChecked(False)
        vertical_cb.setToolTip("瀑布流竖向显示（时间从上到下）")
        mono_cb = QtWidgets.QCheckBox("单色", dlg)
        mono_cb.setChecked(False)
        mono_color_btn = QtWidgets.QPushButton(dlg)
        mono_color_btn.setFixedSize(24, 24)
        mono_color_btn.setStyleSheet(
            "background: #de8400; border: 1px solid #999; border-radius: 3px;")
        mono_color_btn.setToolTip("选择单色音符颜色")
        _export_mono_hex = "#de8400"
        def _pick_export_color() -> None:
            nonlocal _export_mono_hex
            c = QtWidgets.QColorDialog.getColor(
                QtGui.QColor(_export_mono_hex), dlg, "选择音符颜色")
            if c.isValid():
                _export_mono_hex = c.name()
                mono_color_btn.setStyleSheet(
                    f"background: {_export_mono_hex}; "
                    f"border: 1px solid #999; border-radius: 3px;")
        mono_color_btn.clicked.connect(_pick_export_color)
        style_row.addWidget(mute_cb)
        style_row.addWidget(vertical_cb)
        style_row.addWidget(mono_cb)
        style_row.addWidget(mono_color_btn)
        style_row.addStretch()

        a_br_row = QtWidgets.QHBoxLayout()
        a_br_combo = QtWidgets.QComboBox(dlg)
        a_br_combo.setEditable(True)
        a_br_combo.addItems(["96", "128", "192", "256", "320"])
        a_br_combo.setCurrentIndex(2)
        a_br_row.addWidget(QtWidgets.QLabel("音频码率 (kbps):"))
        a_br_row.addWidget(a_br_combo, 1)

        # ── Video bitrate ────────────────────────────────────────────────
        v_br_row = QtWidgets.QHBoxLayout()
        v_br_combo = QtWidgets.QComboBox(dlg)
        v_br_combo.setEditable(True)
        v_br_combo.addItems(["512k", "1M", "2M", "4M", "8M", "12M"])
        v_br_combo.setCurrentIndex(3)  # default: 4M
        v_br_row.addWidget(QtWidgets.QLabel("视频码率:"))
        v_br_row.addWidget(v_br_combo, 1)

        # ── Buttons ──────────────────────────────────────────────────────
        btn_row = QtWidgets.QHBoxLayout()
        btn_row.addStretch()
        btn_do = QtWidgets.QPushButton("导出", dlg)
        btn_cancel = QtWidgets.QPushButton("取消", dlg)
        btn_cancel.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)
        btn_row.addWidget(btn_cancel)
        btn_row.addWidget(btn_do)

        # Progress
        status_label = QtWidgets.QLabel("", dlg)
        status_label.setVisible(False)
        pbar = QtWidgets.QProgressBar(dlg)
        pbar.setVisible(False)
        pbar.setMaximum(100)

        _PHASE_NAMES = {"render": "正在渲染帧… (阶段 1/3)", "audio": "正在渲染音频… (阶段 2/3)",
                        "mux": "正在合成… (阶段 3/3)", "done": "完成"}

        # Assemble
        form = QtWidgets.QVBoxLayout(dlg)
        form.addLayout(path_row)
        form.addLayout(res_row)
        form.addLayout(fps_row)
        form.addLayout(codec_row)
        form.addLayout(sf_row)
        form.addLayout(style_row)
        form.addLayout(a_br_row)
        form.addLayout(v_br_row)
        form.addWidget(status_label)
        form.addWidget(pbar)
        form.addLayout(btn_row)

        # ── Export action ────────────────────────────────────────────────
        # Controls that get disabled during export (defined here so
        # _cancel_export can re-enable them)
        _export_widgets = [
            path_edit, btn_browse, res_combo, fps_combo, codec_combo,
            sf_combo_export, mute_cb, vertical_cb, mono_cb, mono_color_btn,
            a_br_combo, v_br_combo, btn_do,
        ]

        def do_export() -> None:
            out = path_edit.text().strip()
            if not out:
                QtWidgets.QMessageBox.warning(dlg, "提示", "请选择输出位置。")
                return
            sf = sf_combo_export.currentData() or ""
            if not self._midi_path:
                QtWidgets.QMessageBox.warning(dlg, "提示", "没有 MIDI 文件。")
                return

            _ci = codec_combo.currentIndex()
            _codec = _CODECS[_ci][1]
            res_w, res_h = [(1920, 1080), (1280, 720), (854, 480)][res_combo.currentIndex()]
            fps = int(fps_combo.currentText())
            for w in _export_widgets:
                w.setEnabled(False)
            btn_cancel.setEnabled(True)
            pbar.setVisible(True)
            pbar.setValue(0)
            status_label.setVisible(True)

            self._video_worker = VideoExportWorker(
                self._midi_path, sf if not mute_cb.isChecked() else "", out,
                fps=fps, width=res_w, height=res_h,
                v_codec=_codec, v_bitrate=v_br_combo.currentText().strip(),
                a_bitrate=a_br_combo.currentText().strip() + "k",
                muted=mute_cb.isChecked(),
                vertical=vertical_cb.isChecked(),
                mono_color=(_export_mono_hex
                            if mono_cb.isChecked() else ""),
            )
            def _on_prog(phase: str, pct: int) -> None:
                status_label.setText(_PHASE_NAMES.get(phase, phase))
                status_label.setVisible(True)
                pbar.setVisible(True)
                if pct == 0:
                    pbar.reset()
                pbar.setValue(pct)
                # Map phased progress to overall 0-100 % for taskbar
                if phase == "render":
                    overall = int(pct * 0.45)
                elif phase == "audio":
                    overall = 45 + int(pct * 0.45)
                elif phase == "mux":
                    overall = 90 + int(pct * 0.1)
                elif phase == "done":
                    overall = 100
                else:
                    overall = pct
                self._taskbar.show_normal(overall)
                if phase == "done":
                    status_label.setVisible(False)
                    pbar.setVisible(False)

            self._video_worker.progress.connect(_on_prog)
            self._video_worker.finished.connect(
                lambda p: self._on_video_done(dlg, p))
            self._video_worker.error.connect(
                lambda e: self._on_video_error(dlg, e))
            self._video_worker.start()

        # ── Cancel / close handling ─────────────────────────────────────
        def _cancel_export() -> None:
            """Interrupt worker and re-enable controls (dialog stays open)."""
            if (self._video_worker is not None
                    and self._video_worker.isRunning()):
                self._video_worker.requestInterruption()
                self._video_worker.wait(5000)
                self._video_worker = None
            for w in _export_widgets:
                w.setEnabled(True)
            btn_do.setEnabled(True)
            btn_cancel.setEnabled(False)
            pbar.setVisible(False)
            pbar.setValue(0)
            status_label.setVisible(False)
            self._taskbar.hide()

        btn_do.clicked.connect(do_export)
        btn_cancel.setEnabled(False)  # disabled until export starts
        btn_cancel.clicked.connect(_cancel_export)
        # X button → interrupt worker before closing
        dlg.rejected.connect(_cancel_export)
        dlg.layout().setSizeConstraint(
            QtWidgets.QLayout.SizeConstraint.SetFixedSize)
        dlg.exec()

    def _on_video_done(self, dlg: QtWidgets.QDialog, output: str) -> None:
        if self._video_worker is not None:
            self._video_worker.wait(5000)
            self._video_worker = None
        dlg.accept()
        QtWidgets.QApplication.alert(self, 0)
        self._tray.showMessage(
            "导出完成", f"视频已保存至：\n{output}",
            QtWidgets.QSystemTrayIcon.MessageIcon.Information, 5000)
        box = QtWidgets.QMessageBox(self)
        box.setWindowTitle("导出完成")
        box.setText(f"视频已保存至：\n{output}")
        box.setIcon(QtWidgets.QMessageBox.Icon.Information)
        btn_file = box.addButton("打开文件", QtWidgets.QMessageBox.ButtonRole.ActionRole)
        btn_folder = box.addButton("打开文件夹", QtWidgets.QMessageBox.ButtonRole.ActionRole)
        box.addButton("确定", QtWidgets.QMessageBox.ButtonRole.AcceptRole)
        box.exec()
        clicked = box.clickedButton()
        if clicked is btn_file:
            QtGui.QDesktopServices.openUrl(
                QtCore.QUrl.fromLocalFile(str(Path(output))))
        elif clicked is btn_folder:
            QtGui.QDesktopServices.openUrl(
                QtCore.QUrl.fromLocalFile(str(Path(output).parent)))

    def _on_video_error(self, dlg: QtWidgets.QDialog, msg: str) -> None:
        if self._video_worker is not None:
            self._video_worker.wait(5000)
            self._video_worker = None
        self._taskbar.hide()
        dlg.reject()
        QtWidgets.QMessageBox.critical(self, "导出失败", f"视频导出失败：\n{msg}")

    # ── Audio export ─────────────────────────────────────────────────────
    def _on_export_audio(self) -> None:
        """Open a dialog to configure audio export, then start rendering."""
        dlg = QtWidgets.QDialog(self)
        dlg.setWindowTitle("导出音频")
        dlg.setMinimumWidth(240)
        # ── Output path ──────────────────────────────────────────────────
        path_row = QtWidgets.QHBoxLayout()
        path_edit = QtWidgets.QLineEdit(dlg)
        path_edit.setReadOnly(True)
        path_edit.setPlaceholderText("选择输出位置…")
        btn_browse = QtWidgets.QPushButton("浏览…", dlg)
        btn_browse.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)
        path_row.addWidget(path_edit)
        path_row.addWidget(btn_browse)

        # Default output path (format chosen via file dialog extension)
        _FILTERS = (
            "mp3 (*.mp3);;m4a (*.m4a);;wav (*.wav);;"
            "flac (*.flac);;ogg (*.ogg);;所有文件 (*)"
        )

        # ── SoundFont ────────────────────────────────────────────────────
        sf_row = QtWidgets.QHBoxLayout()
        sf_combo = QtWidgets.QComboBox(dlg)
        for sf_path in sorted(_SOUNDFONT_DIR.glob("*.sf2")):
            sf_combo.addItem(sf_path.name, str(sf_path))
        if sf_combo.count() == 0:
            sf_combo.addItem("（未找到 SoundFont 文件）", "")
        sf_row.addWidget(QtWidgets.QLabel("SoundFont:"))
        sf_row.addWidget(sf_combo, 1)

        # ── Bitrate (raw number, no "k" suffix) ──────────────────────────
        br_row = QtWidgets.QHBoxLayout()
        br_combo = QtWidgets.QComboBox(dlg)
        br_combo.setEditable(True)
        br_combo.addItems(["96", "128", "192", "256", "320"])
        br_combo.setCurrentIndex(2)
        br_row.addWidget(QtWidgets.QLabel("码率 (kbps):"))
        br_row.addWidget(br_combo, 1)

        # ── Buttons ──────────────────────────────────────────────────────
        btn_row = QtWidgets.QHBoxLayout()
        btn_row.addStretch()
        btn_export_dlg = QtWidgets.QPushButton("导出", dlg)
        btn_cancel = QtWidgets.QPushButton("取消", dlg)
        btn_cancel.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)
        btn_row.addWidget(btn_cancel)
        btn_row.addWidget(btn_export_dlg)

        # ── Progress ─────────────────────────────────────────────────────
        status_label = QtWidgets.QLabel("", dlg)
        status_label.setVisible(False)
        pbar = QtWidgets.QProgressBar(dlg)
        pbar.setVisible(False)
        pbar.setMaximum(100)

        # ── Assemble dialog ──────────────────────────────────────────────
        form = QtWidgets.QVBoxLayout(dlg)
        form.addLayout(path_row)
        form.addLayout(sf_row)
        form.addLayout(br_row)
        form.addWidget(status_label)
        form.addWidget(pbar)
        form.addLayout(btn_row)

        # ── Browse ───────────────────────────────────────────────────────
        def browse() -> None:
            midi = Path(self._midi_path) if self._midi_path else Path("output")
            default = str(midi.parent / f"{midi.stem}_midi.mp3")
            p, _ = QtWidgets.QFileDialog.getSaveFileName(
                dlg, "导出音频", default, _FILTERS)
            if p:
                path_edit.setText(p)

        btn_browse.clicked.connect(browse)
        # auto-fill default (absolute path, same folder as MIDI)
        if self._midi_path and not path_edit.text():
            midi = Path(self._midi_path)
            path_edit.setText(str(midi.parent / f"{midi.stem}_midi.mp3"))

        # ── Export ───────────────────────────────────────────────────────
        _audio_export_widgets = [
            path_edit, btn_browse, sf_combo, br_combo, btn_export_dlg,
        ]

        def do_export() -> None:
            out = path_edit.text().strip()
            if not out:
                QtWidgets.QMessageBox.warning(dlg, "提示", "请选择输出位置")
                return
            sf = sf_combo.currentData() or ""
            if not sf:
                QtWidgets.QMessageBox.warning(dlg, "提示", "请选择 SoundFont")
                return
            if not Path(sf).exists():
                QtWidgets.QMessageBox.warning(dlg, "提示", f"SoundFont 不存在：{sf}")
                return
            if not self._midi_path:
                QtWidgets.QMessageBox.warning(dlg, "提示", "没有 MIDI 文件")
                return

            for w in _audio_export_widgets:
                w.setEnabled(False)
            btn_cancel.setEnabled(True)
            status_label.setVisible(True)
            status_label.setText("正在导出…")
            pbar.setVisible(True)
            pbar.setValue(0)
            self._taskbar.show_indeterminate()

            self._audio_worker = AudioExportWorker(
                self._midi_path, sf, out,
                bitrate=br_combo.currentText().strip() + "k",
            )
            def _on_audio_prog(msg: str, pct: int) -> None:
                status_label.setText(msg)
                status_label.setVisible(True)
                if pct >= 0:
                    pbar.setVisible(True)
                    pbar.setValue(pct)
                if pct >= 0:
                    self._taskbar.show_normal(pct)
            self._audio_worker.progress.connect(_on_audio_prog)
            self._audio_worker.finished.connect(
                lambda p: self._on_audio_export_done(dlg, p))
            self._audio_worker.error.connect(
                lambda e: self._on_audio_export_error(dlg, e))
            self._audio_worker.start()

        btn_export_dlg.clicked.connect(do_export)
        btn_cancel.setEnabled(False)  # disabled until export starts

        def _cancel_audio_export() -> None:
            if (self._audio_worker is not None
                    and self._audio_worker.isRunning()):
                self._audio_worker.requestInterruption()
                self._audio_worker.wait(5000)
                self._audio_worker = None
            for w in _audio_export_widgets:
                w.setEnabled(True)
            btn_export_dlg.setEnabled(True)
            btn_cancel.setEnabled(False)
            status_label.setVisible(False)
            pbar.setVisible(False)
            self._taskbar.hide()

        btn_cancel.clicked.connect(_cancel_audio_export)
        dlg.rejected.connect(_cancel_audio_export)
        dlg.layout().setSizeConstraint(
            QtWidgets.QLayout.SizeConstraint.SetFixedSize)

        dlg.exec()

    def _on_audio_export_done(self, dlg: QtWidgets.QDialog, output: str) -> None:
        if self._audio_worker is not None:
            self._audio_worker.wait(5000)
            self._audio_worker = None
        self._taskbar.hide()
        dlg.accept()
        if not self.isActiveWindow():
            QtWidgets.QApplication.alert(self, 0)
        self._tray.showMessage(
            "导出完成", f"音频已保存至：\n{output}",
            QtWidgets.QSystemTrayIcon.MessageIcon.Information, 5000)
        box = QtWidgets.QMessageBox(self)
        box.setWindowTitle("导出完成")
        box.setText(f"音频已保存至：\n{output}")
        box.setIcon(QtWidgets.QMessageBox.Icon.Information)
        btn_file = box.addButton("打开文件", QtWidgets.QMessageBox.ButtonRole.ActionRole)
        btn_folder = box.addButton("打开文件夹", QtWidgets.QMessageBox.ButtonRole.ActionRole)
        box.addButton("确定", QtWidgets.QMessageBox.ButtonRole.AcceptRole)
        box.exec()
        clicked = box.clickedButton()
        if clicked is btn_file:
            QtGui.QDesktopServices.openUrl(
                QtCore.QUrl.fromLocalFile(str(Path(output))))
        elif clicked is btn_folder:
            QtGui.QDesktopServices.openUrl(
                QtCore.QUrl.fromLocalFile(str(Path(output).parent)))

    def _on_audio_export_error(self, dlg: QtWidgets.QDialog, msg: str) -> None:
        if self._audio_worker is not None:
            self._audio_worker.wait(5000)
            self._audio_worker = None
        self._taskbar.hide()
        dlg.reject()
        box = QtWidgets.QMessageBox(self)
        box.setWindowTitle("导出失败")
        box.setText(f"音频导出失败：\n{msg}")
        box.setIcon(QtWidgets.QMessageBox.Icon.Critical)
        box.addButton("确定", QtWidgets.QMessageBox.ButtonRole.AcceptRole)
        box.exec()

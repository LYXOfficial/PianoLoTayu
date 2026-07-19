"""Piano-roll grid scene, keyboard widget and MIDI playback."""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pretty_midi
from PySide6 import QtWidgets, QtGui, QtCore

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

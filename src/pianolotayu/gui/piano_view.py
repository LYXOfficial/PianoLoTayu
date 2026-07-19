"""Piano-roll grid scene, keyboard widget and MIDI playback."""

from __future__ import annotations

import sys
import time
from pathlib import Path

from PySide6 import QtWidgets, QtGui, QtCore

from .win32_utils import setup_fluidsynth_dll, fluidsynth_status_message

# Note-name table (avoids importing pretty_midi just to label keys)
_NOTE_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")


def _midi_note_name(pitch: int) -> str:
    """MIDI note number → name like ``C4`` (same as pretty_midi)."""
    return f"{_NOTE_NAMES[pitch % 12]}{pitch // 12 - 1}"


def load_pretty_midi(midi_path: str | Path):
    """Load a MIDI file into PrettyMIDI with a corrected tempo map.

    ``pretty_midi`` only reads ``set_tempo`` events from track 0.  Many DAW
    exports put the tempo map on another track, so PrettyMIDI falls back to
    120 BPM and every note time is scaled — preview / export all sound too
    fast or too slow.

    Fix: collect *all* tempo events from every track, merge them onto track
    0, then let PrettyMIDI parse the rewritten file from memory.
    """
    from io import BytesIO

    import mido
    import pretty_midi

    path = str(midi_path)
    src = mido.MidiFile(path)

    # Absolute-tick tempo map from every track (last event at a tick wins)
    tempo_at: dict[int, int] = {}
    for track in src.tracks:
        abs_tick = 0
        for msg in track:
            abs_tick += msg.time
            if msg.type == "set_tempo":
                tempo_at[abs_tick] = int(msg.tempo)

    if tempo_at:
        track0_tempos: dict[int, int] = {}
        abs_tick = 0
        if src.tracks:
            for msg in src.tracks[0]:
                abs_tick += msg.time
                if msg.type == "set_tempo":
                    track0_tempos[abs_tick] = int(msg.tempo)
        needs_fix = track0_tempos != tempo_at
    else:
        needs_fix = False

    if not needs_fix:
        return pretty_midi.PrettyMIDI(path)

    # Strip set_tempo from all tracks
    stripped: list[mido.MidiTrack] = []
    for track in src.tracks:
        new_track = mido.MidiTrack()
        abs_tick = 0
        last_kept = 0
        for msg in track:
            abs_tick += msg.time
            if msg.type == "set_tempo":
                continue
            new_track.append(msg.copy(time=abs_tick - last_kept))
            last_kept = abs_tick
        stripped.append(new_track)

    if not stripped:
        stripped = [mido.MidiTrack()]

    # Rebuild track 0 = original track-0 events + all tempos, sorted by tick
    t0_events: list[tuple[int, object]] = []
    abs_tick = 0
    for msg in stripped[0]:
        abs_tick += msg.time
        t0_events.append((abs_tick, msg))
    for tick, tempo in sorted(tempo_at.items()):
        t0_events.append(
            (tick, mido.MetaMessage("set_tempo", tempo=tempo, time=0))
        )
    # Tempos before other events at the same tick
    t0_events.sort(
        key=lambda it: (it[0], 0 if getattr(it[1], "type", "") == "set_tempo" else 1)
    )

    new_t0 = mido.MidiTrack()
    last = 0
    for tick, msg in t0_events:
        new_t0.append(msg.copy(time=tick - last))
        last = tick
    stripped[0] = new_t0

    fixed = mido.MidiFile(type=src.type, ticks_per_beat=src.ticks_per_beat)
    for t in stripped:
        fixed.tracks.append(t)

    buf = BytesIO()
    fixed.save(file=buf)
    buf.seek(0)
    return pretty_midi.PrettyMIDI(buf)


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

    def set_active_notes(self, notes: list) -> None:
        """Highlight keys for active notes.

        *notes* is a list of ``(pitch, start[, …])`` tuples.
        """
        self._active_pitches = {n[0] for n in notes}
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
                name = _midi_note_name(pitch)
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
                name = _midi_note_name(pitch)
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
        self._midi = None  # pretty_midi.PrettyMIDI when loaded
        # Horizontal-mode (default)
        self._note_h = 18
        self._extra = 0
        # Vertical-mode (waterfall)
        self._vertical = False
        self._note_w = 20
        self._extra_w = 0
        self._time_base = 200  # overwritten in _rebuild
        # Trailing empty time (canonical px) so playhead can stay at the
        # leading edge through t=duration.  Expanded by NoteGridView.
        self._trail_pad = 200
        # Shared
        self._mono: QtGui.QColor | None = None
        # key: (pitch, start, inst_idx) — keeps multi-track / double notes distinct
        self._note_items: dict[tuple[int, float, int], tuple[QtWidgets.QGraphicsRectItem, int]] = {}
        self._active_keys: set[tuple[int, float, int]] = set()
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
        self._midi = load_pretty_midi(midi_path)
        self._rebuild()

    def _rebuild(self) -> None:
        self.clear()
        self._note_items.clear()
        self._active_keys.clear()
        if self._midi is None:
            return
        total = MAX_PITCH - MIN_PITCH + 1
        pad = max(200, int(self._trail_pad))
        if self._vertical:
            tw = total * self._note_w + self._extra_w
            th = self.scene_duration_s() * self._CANONICAL_PX + pad
            self._time_base = th  # must be set BEFORE _draw_notes / _draw_grid
        else:
            tw = self.scene_duration_s() * self._CANONICAL_PX + pad
            th = total * self._note_h + self._extra
        self._draw_grid()
        self._draw_notes()
        self.setSceneRect(0, 0, tw, th)

    def set_trail_pad(self, pad: float) -> None:
        """Set trailing empty time padding (canonical px after last note).

        Horizontal mode only expands the scene rect (notes don't depend on pad).
        Vertical mode must rebuild because note Y positions use ``_time_base``.
        """
        pad = max(200.0, float(pad))
        if abs(pad - self._trail_pad) < 1.0:
            return
        self._trail_pad = pad
        if self._midi is None:
            return
        if self._vertical:
            self._rebuild()
        else:
            # Cheap: widen scene only — note X = t * CANONICAL, independent of pad
            dur = self.scene_duration_s()
            tw = dur * self._CANONICAL_PX + pad
            th = self.sceneRect().height()
            if th <= 0:
                total = MAX_PITCH - MIN_PITCH + 1
                th = total * self._note_h + self._extra
            self.setSceneRect(0, 0, tw, th)
            # Extend grid stripe width if we already drew a shorter grid
            # (background brush covers the rest; optional lines stop early — OK)

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
        pad = max(200, int(self._trail_pad))
        if self._vertical:
            th = self.scene_duration_s() * self._CANONICAL_PX + pad
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
            tw = self.scene_duration_s() * self._CANONICAL_PX + pad
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
        # Count notes sharing (pitch, start) so multi-track unisons can be offset
        same_start: dict[tuple[int, float], int] = {}
        if self._vertical:
            for inst_i, inst in enumerate(self._midi.instruments):
                if inst.is_drum:
                    continue
                for note in inst.notes:
                    if note.pitch < MIN_PITCH or note.pitch > MAX_PITCH:
                        continue
                    col_w = self.pitch_width(note.pitch)
                    base_x = self.pitch_to_x(note.pitch) + max(1, col_w // 8)
                    w = max(2, col_w - max(2, col_w // 6))
                    # Offset stacked unisons slightly so all stay visible
                    k0 = (note.pitch, note.start)
                    stack = same_start.get(k0, 0)
                    same_start[k0] = stack + 1
                    x = base_x + min(stack, 3) * max(1, col_w // 10)
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
                    r.setZValue(5 + stack)
                    self._note_items[(note.pitch, note.start, inst_i)] = (r, alpha)
        else:
            note_h, extra = self._note_h, self._extra
            for inst_i, inst in enumerate(self._midi.instruments):
                if inst.is_drum:
                    continue
                for note in inst.notes:
                    if note.pitch < MIN_PITCH or note.pitch > MAX_PITCH:
                        continue
                    x = self.time_to_x(note.start)
                    w = max(3.0, self.time_to_x(note.end) - x)
                    row_h = _dist_h(note.pitch, note_h, extra)
                    base_y = _dist_y(note.pitch, note_h, extra) + max(1, row_h // 8)
                    h = max(2, row_h - max(2, row_h // 6))
                    k0 = (note.pitch, note.start)
                    stack = same_start.get(k0, 0)
                    same_start[k0] = stack + 1
                    y = base_y + min(stack, 3) * max(1, row_h // 8)
                    alpha = int(80 + note.velocity / 127 * 175)
                    if self._mono:
                        color = QtGui.QColor(self._mono)
                    else:
                        color = _note_color(note.pitch)
                    color.setAlpha(alpha)
                    r = self.addRect(x, y, w, h,
                                     QtGui.QPen(color.darker(120), 1),
                                     QtGui.QBrush(color))
                    r.setZValue(5 + stack)
                    self._note_items[(note.pitch, note.start, inst_i)] = (r, alpha)

    def set_active_notes(self, notes: list) -> None:
        """Turn active-note rectangles white; restore others to original.

        *notes* entries are ``(pitch, start, inst_idx)`` (or longer tuples
        whose first three fields match).
        """
        incoming: set[tuple[int, float, int]] = set()
        for n in notes:
            if len(n) >= 3:
                incoming.add((int(n[0]), float(n[1]), int(n[2])))
            elif len(n) >= 2:
                # Fallback: highlight every rect with this pitch+start
                for key in self._note_items:
                    if key[0] == n[0] and key[1] == n[1]:
                        incoming.add(key)
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
        # Trailing pad depends on zoom (visible scene span changes)
        self.ensure_trail_pad()
        # Restore scroll so the same time stays at the same viewport position
        self.scroll_to_time(t)

    def set_v_zoom(self, note_h: int, extra: int = 0) -> None:
        self._scene.set_v_zoom(note_h, extra)
        self.ensure_trail_pad()

    def set_h_fit(self, note_w: int, extra_w: int = 0) -> None:
        """Vertical-mode: fit columns to viewport width."""
        self._scene.set_h_fit(note_w, extra_w)
        self.ensure_trail_pad()

    def set_orientation(self, vertical: bool) -> None:
        self._scene.set_orientation(vertical)
        self.resetTransform()
        self._display_px_per_sec = 200  # reset to canonical
        if vertical:
            self.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        else:
            self.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.ensure_trail_pad()

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

    def ensure_trail_pad(self) -> None:
        """Make sure the scene has enough empty time after the last note so
        the playhead can stay pinned to the leading edge through t=duration.

        Without this, once the scrollbar hits its maximum the roll freezes
        and only note highlights keep updating (export is fine — it never
        scrolls a scene).
        """
        if self._scene._midi is None:
            return
        scale = self._display_px_per_sec / GridScene._CANONICAL_PX
        if scale <= 0:
            return
        if self._scene._vertical:
            # Need y(duration)=pad such that pad*scale >= viewport height
            vp = max(1, self.viewport().height())
        else:
            vp = max(1, self.viewport().width())
        # +64 margin so the last notes aren't glued to the edge
        needed = vp / scale + 64.0
        self._scene.set_trail_pad(needed)

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
        self.ensure_trail_pad()
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
        self.ensure_trail_pad()
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
    """Plays a MIDI file through a SoundFont using fluidsynth.

    Notes are keyed by ``(pitch, start, inst_idx)`` so multi-track unisons and
    rapid same-pitch re-articulations each get their own on/off.  Non-drum
    instruments are assigned to separate fluidsynth channels (all piano
    timbre) so simultaneous same-pitch notes on different tracks can all sound.
    Drum tracks are skipped entirely.
    """

    position = QtCore.Signal(float)
    active_notes_changed = QtCore.Signal(list)
    finished = QtCore.Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._synth = None
        self._sfid = -1
        self._midi = None  # pretty_midi.PrettyMIDI when loaded
        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._tick)
        self._start_ts = 0.0
        self._paused = False
        self._pause_pos = 0.0
        # Active note instances: (pitch, start, inst_idx) → (channel, velocity)
        self._active: dict[tuple[int, float, int], tuple[int, int]] = {}
        # inst_idx → fluidsynth channel (0–15, skip 9 = drums)
        self._inst_ch: dict[int, int] = {}

    def unload_soundfont(self) -> None:
        """Mute and release the synth (silent / no-SoundFont mode)."""
        if self._synth is None:
            return
        try:
            self._synth.all_sounds_off(-1)
        except Exception:
            pass
        try:
            self._synth.delete()
        except Exception:
            pass
        self._synth = None
        self._sfid = -1
        self._active = {}

    def set_soundfont(self, sf_path: str) -> bool:
        """Load / hot-swap / unload SoundFont. Empty path = silent mode."""
        if not sf_path:
            self.unload_soundfont()
            return True
        if self._synth is not None:
            try:
                sfid = self._synth.sfload(sf_path)
                if sfid < 0:
                    raise RuntimeError(f"SoundFont 加载失败：{sf_path}")
                self._sfid = sfid
                self._assign_programs()
                try:
                    self._synth.all_sounds_off(-1)
                except Exception:
                    pass
                self._active = {}
                return True
            except Exception:
                self.unload_soundfont()
        return self.load_soundfont(sf_path)

    def load_soundfont(self, sf_path: str) -> bool:
        self.unload_soundfont()
        try:
            setup_fluidsynth_dll()
            import fluidsynth
        except ImportError as exc:
            QtWidgets.QMessageBox.warning(
                self.parent(), "缺少组件",
                f"{fluidsynth_status_message()}\n\n详细：{exc}")
            return False
        except OSError as exc:
            QtWidgets.QMessageBox.warning(
                self.parent(), "fluidsynth 加载失败",
                f"{fluidsynth_status_message()}\n\n详细：{exc}")
            return False
        try:
            self._synth = fluidsynth.Synth()
            self._sfid = self._synth.sfload(sf_path)
            if self._sfid < 0:
                raise RuntimeError(f"SoundFont 加载失败：{sf_path}")
            self._assign_programs()
            if sys.platform.startswith("linux"):
                for driver in ("pipewire", "pulseaudio"):
                    try:
                        self._synth.start(driver=driver)
                        break
                    except Exception:
                        continue
                else:
                    self._synth.start()
            elif sys.platform == "win32":
                for driver in ("wasapi", "dsound", "waveout"):
                    try:
                        self._synth.start(driver=driver)
                        break
                    except Exception:
                        continue
                else:
                    self._synth.start()
            elif sys.platform == "darwin":
                self._synth.start(driver="coreaudio")
            else:
                self._synth.start()
            return True
        except Exception as exc:
            self.unload_soundfont()
            QtWidgets.QMessageBox.warning(self.parent(), "SoundFont 错误", str(exc))
            return False

    def _assign_channels(self) -> None:
        """Map each non-drum instrument to a fluidsynth melodic channel."""
        self._inst_ch = {}
        ch = 0
        if self._midi is None:
            return
        for i, inst in enumerate(self._midi.instruments):
            if inst.is_drum:
                self._inst_ch[i] = 9  # GM drum channel (unused if we skip drums)
                continue
            if ch == 9:
                ch = 10
            if ch > 15:
                ch = 0  # wrap; last-resort share
            self._inst_ch[i] = ch
            ch += 1

    def _assign_programs(self) -> None:
        """Select piano (GM program 0) on every melodic channel we use.

        Drum tracks are never played; all other tracks share the piano
        timbre so multi-track MIDI still reads as a piano arrangement.
        """
        if self._synth is None or self._sfid < 0:
            return
        if not self._inst_ch:
            self._assign_channels()
        # Piano on every assigned melodic channel (+ ch 0 as default)
        channels = {0, *self._inst_ch.values()} - {9}
        for ch in channels:
            try:
                self._synth.program_select(ch, self._sfid, 0, 0)
            except Exception:
                pass

    def load_midi(self, midi_path: str | Path) -> None:
        self._midi = load_pretty_midi(midi_path)
        self._assign_channels()
        if self._synth is not None:
            self._assign_programs()

    def play(self, sf_path: str = "", start_t: float | None = None) -> None:
        if sf_path:
            if self._synth is None and not self.load_soundfont(sf_path):
                return
        else:
            self.unload_soundfont()
        if start_t is not None:
            self._start_ts = time.monotonic() - start_t
            self._paused = False
        elif self._paused:
            self._paused = False
            self._start_ts = time.monotonic() - self._pause_pos
        else:
            self._start_ts = time.monotonic()
        self._active = {}
        self._timer.start()

    def pause(self) -> None:
        self._paused = True
        self._pause_pos = time.monotonic() - self._start_ts
        self._timer.stop()
        self.active_notes_changed.emit([])
        if self._synth is not None:
            self._synth.all_sounds_off(-1)
        self._active = {}

    def stop(self) -> None:
        self._timer.stop()
        self._paused = False
        self.active_notes_changed.emit([])
        if self._synth is not None:
            self._synth.all_sounds_off(-1)
        self._active = {}

    def cleanup(self) -> None:
        """Stop playback and release the synth — call before closing window."""
        self.stop()
        self.unload_soundfont()

    def seek(self, t: float) -> None:
        if self._synth is not None:
            try:
                self._synth.all_sounds_off(-1)
            except Exception:
                pass
        self._active = {}
        if self._timer.isActive():
            self._start_ts = time.monotonic() - t
        self.position.emit(t)
        if self._midi is not None:
            self._emit_active_at(t)

    def _collect_active(self, now: float) -> dict[tuple[int, float, int], tuple[int, int]]:
        """Return { (pitch,start,inst_i): (channel, velocity) } sounding at *now*."""
        out: dict[tuple[int, float, int], tuple[int, int]] = {}
        if self._midi is None:
            return out
        for inst_i, inst in enumerate(self._midi.instruments):
            if inst.is_drum:
                continue
            ch = self._inst_ch.get(inst_i, 0)
            for note in inst.notes:
                if note.pitch < MIN_PITCH or note.pitch > MAX_PITCH:
                    continue
                if note.start <= now < note.end:
                    key = (note.pitch, note.start, inst_i)
                    out[key] = (ch, int(note.velocity) or 100)
        return out

    def _emit_active_at(self, now: float) -> None:
        desired = self._collect_active(now)
        self._active = desired
        # UI list: (pitch, start, inst_idx)
        self.active_notes_changed.emit(
            [(p, s, i) for (p, s, i) in desired.keys()]
        )

    def _tick(self) -> None:
        if self._midi is None:
            return
        now = time.monotonic() - self._start_ts
        self.position.emit(now)
        if now > self._midi.get_end_time() + 2.0:
            self.stop()
            self.finished.emit()
            return

        desired = self._collect_active(now)
        old_keys = set(self._active.keys())
        new_keys = set(desired.keys())

        if self._synth is not None:
            # Notes that ended
            for key in (old_keys - new_keys):
                pitch, _start, inst_i = key
                ch, _vel = self._active[key]
                # Only noteoff if no remaining active note uses same ch+pitch
                still = any(
                    k[0] == pitch and desired[k][0] == ch
                    for k in new_keys
                )
                if not still:
                    try:
                        self._synth.noteoff(ch, pitch)
                    except Exception:
                        pass

            # Notes that started — always re-trigger (fixes rapid double-taps)
            for key in (new_keys - old_keys):
                pitch, _start, inst_i = key
                ch, vel = desired[key]
                # If same pitch already sounding on this channel (from another
                # note that hasn't ended yet, or a previous articulation),
                # re-strike: noteoff then noteon so the attack is heard.
                try:
                    self._synth.noteoff(ch, pitch)
                except Exception:
                    pass
                try:
                    self._synth.noteon(ch, pitch, vel)
                except Exception:
                    pass

        self._active = desired
        self.active_notes_changed.emit(
            [(p, s, i) for (p, s, i) in desired.keys()]
        )

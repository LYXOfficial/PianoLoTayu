"""MIDI → video / audio export workers."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pretty_midi
from PySide6 import QtWidgets, QtGui, QtCore

from .piano_view import (
    MIN_PITCH, MAX_PITCH, _KEY_WHITE, NOTE_COLORS,
    _note_color, _dist_y, _dist_h,
)

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


def filter_available_codecs(
    codecs: list[tuple[str, str, str]],
) -> list[tuple[str, str, str]]:
    """Return only codecs whose encoder is present in ffmpeg."""
    available = _get_available_encoders()
    return [c for c in codecs if c[1] in available]


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
        import os, tempfile, subprocess, imageio
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
                key_h = int(self._h * 0.08)  # 8% of height for keyboard bar
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

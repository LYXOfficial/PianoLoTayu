"""MIDI → video / audio export workers."""

from __future__ import annotations

from pathlib import Path

from PySide6 import QtWidgets, QtGui, QtCore

from .piano_view import (
    MIN_PITCH, MAX_PITCH, _KEY_WHITE,
    _note_color, _dist_y, _dist_h, load_pretty_midi, _midi_note_name,
    PlaybackOptions,
)
from .win32_utils import setup_fluidsynth_dll, fluidsynth_status_message

# ═══════════════════════════════════════════════════════════════════════════
# ffmpeg encoder availability check (cached)
# ═══════════════════════════════════════════════════════════════════════════

_AVAILABLE_ENCODERS: set[str] | None = None

# Preferred order for AV1 software encoders
_AV1_ENCODERS = ("libsvtav1", "libaom-av1", "librav1e")


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
            # flags field is always 6 chars starting with V for video
            parts = line.strip().split()
            if len(parts) >= 2 and parts[0].startswith("V") and len(parts[0]) >= 6:
                _AVAILABLE_ENCODERS.add(parts[1])
    except Exception:
        # If we can't query, assume common software encoders
        _AVAILABLE_ENCODERS = {
            "libx264", "libx265", "libaom-av1", "libsvtav1", "libvpx-vp9",
        }
    return _AVAILABLE_ENCODERS


def pick_av1_encoder() -> str | None:
    """Return the best available AV1 encoder name, or None."""
    available = _get_available_encoders()
    for name in _AV1_ENCODERS:
        if name in available:
            return name
    return None


def pick_vp9_encoder() -> str | None:
    """Return the best available VP9 encoder name, or None."""
    available = _get_available_encoders()
    for name in ("libvpx-vp9", "vp9_vaapi"):
        if name in available:
            return name
    return None


def filter_available_codecs(
    codecs: list[tuple[str, str, str]],
) -> list[tuple[str, str, str]]:
    """Return only codecs whose encoder is present in ffmpeg.

    Entries with encoder ``"av1"`` / ``"vp9"`` are resolved to the best
    installed backend.
    """
    available = _get_available_encoders()
    av1 = pick_av1_encoder()
    vp9 = pick_vp9_encoder()
    out: list[tuple[str, str, str]] = []
    for label, enc, ext in codecs:
        if enc == "av1":
            if av1 is None:
                continue
            pretty = {
                "libsvtav1": "SVT-AV1",
                "libaom-av1": "libaom",
                "librav1e": "rav1e",
            }.get(av1, av1)
            out.append((f"{label} [{pretty}]", av1, ext))
        elif enc == "vp9":
            if vp9 is None:
                continue
            pretty = {
                "libvpx-vp9": "libvpx",
                "vp9_vaapi": "VAAPI",
            }.get(vp9, vp9)
            out.append((f"{label} [{pretty}]", vp9, ext))
        elif enc in available:
            out.append((label, enc, ext))
    return out


def build_video_codec_list() -> list[tuple[str, str, str]]:
    """Default export codec menu, filtered to what this ffmpeg can encode."""
    return filter_available_codecs([
        ("H.264 (.mp4)", "libx264", ".mp4"),
        ("H.265 (.mp4)", "libx265", ".mp4"),
        ("AV1 (.mp4)",   "av1",     ".mp4"),
        ("H.264 (.mkv)", "libx264", ".mkv"),
        ("H.265 (.mkv)", "libx265", ".mkv"),
        ("AV1 (.mkv)",   "av1",     ".mkv"),
        ("VP9 (.mkv)",   "vp9",     ".mkv"),
        ("AV1 (.webm)",  "av1",     ".webm"),
        ("VP9 (.webm)",  "vp9",     ".webm"),
    ])


def _audio_mux_args(codec: str, bitrate: str) -> list[str]:
    """ffmpeg audio encode flags for mux stage."""
    c = (codec or "aac").lower()
    # normalize "192" / "192k" / "192K"
    br_raw = (bitrate or "192k").strip()
    if br_raw.endswith(("k", "K", "M")):
        br = br_raw
        try:
            br_num = float(br_raw[:-1])
        except ValueError:
            br_num = 192.0
    else:
        br = f"{br_raw}k"
        try:
            br_num = float(br_raw)
        except ValueError:
            br_num = 192.0
            br = "192k"

    if c in ("aac",):
        return ["-c:a", "aac", "-b:a", br]
    if c in ("opus", "libopus"):
        # Opus is happiest at 48 kHz; ffmpeg will resample
        return ["-c:a", "libopus", "-b:a", br, "-ar", "48000"]
    if c in ("vorbis", "libvorbis"):
        # libvorbis is VBR-oriented; -q:a is the reliable control.
        # Map ~64–320 kbps → q 0–10 (roughly).
        q = max(0, min(10, int(round((br_num - 64) / 25.6))))
        return ["-c:a", "libvorbis", "-q:a", str(q)]
    if c in ("flac",):
        return ["-c:a", "flac"]  # lossless — bitrate ignored
    # fallback
    return ["-c:a", "aac", "-b:a", br]


# container → allowed UI audio codec ids (order = default preference)
AUDIO_CODECS_BY_EXT: dict[str, list[tuple[str, str]]] = {
    ".mp4":  [("AAC", "aac"), ("Opus", "opus")],
    ".mkv":  [("AAC", "aac"), ("Opus", "opus"),
              ("Vorbis", "vorbis"), ("FLAC", "flac")],
    ".webm": [("Opus", "opus"), ("Vorbis", "vorbis")],
}

# UI id → ffmpeg encoder name(s) that must exist
_AUDIO_ENCODER_NEED = {
    "aac": ("aac",),
    "opus": ("libopus", "opus"),
    "vorbis": ("libvorbis", "vorbis"),
    "flac": ("flac",),
}


def audio_codecs_for_path(path: str) -> list[tuple[str, str]]:
    """Return [(label, id), …] for the container of *path*.

    Filters out codecs whose encoder is missing from the bundled ffmpeg.
    """
    ext = Path(path).suffix.lower()
    opts = list(AUDIO_CODECS_BY_EXT.get(ext, AUDIO_CODECS_BY_EXT[".mp4"]))
    aenc = _get_available_audio_encoders()
    out: list[tuple[str, str]] = []
    for label, cid in opts:
        need = _AUDIO_ENCODER_NEED.get(cid, ())
        if not need or any(n in aenc for n in need):
            out.append((label, cid))
    return out or opts  # never return empty if table had entries


_AVAILABLE_AUDIO_ENCODERS: set[str] | None = None


def _get_available_audio_encoders() -> set[str]:
    """Return audio encoder names available in ffmpeg (cached)."""
    global _AVAILABLE_AUDIO_ENCODERS
    if _AVAILABLE_AUDIO_ENCODERS is not None:
        return _AVAILABLE_AUDIO_ENCODERS
    try:
        import subprocess
        from imageio_ffmpeg import get_ffmpeg_exe
        ffmpeg = get_ffmpeg_exe()
        out = subprocess.run(
            [ffmpeg, "-encoders"], capture_output=True, text=True,
            timeout=10,
        ).stdout
        found: set[str] = set()
        for line in out.splitlines():
            parts = line.strip().split()
            # audio encoder lines: " A....D libvorbis ..."
            if len(parts) >= 2 and parts[0].startswith("A") and len(parts[0]) >= 6:
                found.add(parts[1])
        _AVAILABLE_AUDIO_ENCODERS = found
    except Exception:
        _AVAILABLE_AUDIO_ENCODERS = {
            "aac", "libopus", "opus", "libvorbis", "vorbis", "flac",
        }
    return _AVAILABLE_AUDIO_ENCODERS


def _video_encoder_params(codec: str, fps: int = 30) -> list[str]:
    """Speed-oriented ffmpeg flags per encoder + regular keyframes for seeking.

    imageio-ffmpeg usually ships only ``libaom-av1`` for AV1.  libaom's default
    is near cpu-used=1 (extremely slow); we force realtime/high-speed presets
    so piano-roll exports finish in a reasonable time.

    Keyframe interval is ~1 s so MKV/WebM scrubbing does not decode long GOPs.
    """
    gop = max(1, int(fps) if fps and fps > 0 else 30)
    if codec == "libaom-av1":
        # cpu-used: 0=slowest/best … 8=fastest; realtime usage skips heavy tools
        return [
            "-cpu-used", "8",
            "-usage", "realtime",
            "-row-mt", "1",
            "-tiles", "2x2",
            "-threads", "0",
            "-g", str(gop),
            "-keyint_min", str(gop),
        ]
    if codec == "libsvtav1":
        # preset 0=slowest … 12/13=fastest; keyint in frames via svtav1-params
        return [
            "-preset", "10",
            "-svtav1-params", f"fast-decode=1:keyint={gop}",
            "-g", str(gop),
        ]
    if codec == "librav1e":
        return ["-speed", "10", "-g", str(gop)]
    if codec == "libvpx-vp9":
        # deadline=realtime + high cpu-used keeps VP9 usable for exports
        return [
            "-deadline", "realtime",
            "-cpu-used", "8",
            "-row-mt", "1",
            "-tile-columns", "2",
            "-frame-parallel", "1",
            "-threads", "0",
            "-g", str(gop),
            "-keyint_min", str(gop),
            "-auto-alt-ref", "0",
        ]
    if codec == "libx264":
        return [
            "-preset", "veryfast",
            "-g", str(gop),
            "-keyint_min", str(gop),
            "-sc_threshold", "0",
        ]
    if codec == "libx265":
        return [
            "-preset", "fast",
            "-g", str(gop),
            "-keyint_min", str(gop),
        ]
    return ["-g", str(gop)]


def _container_mux_args(path: str) -> list[str]:
    """Muxer flags that improve seek / index layout per container."""
    ext = Path(path).suffix.lower()
    if ext in (".mkv", ".webm"):
        # reserve_index_space: leave room at the *front* for Cues so players
        # do not have to scan the whole file before seeking works smoothly.
        # cluster_time_limit (ms): smaller clusters → denser seek points.
        return [
            "-reserve_index_space", "512k",
            "-cluster_time_limit", "1000",
        ]
    if ext in (".mp4", ".m4a", ".mov"):
        return ["-movflags", "+faststart"]
    return []


def _finalize_container(
    ffmpeg: str, src: str, dst: str, *, abort=None,
) -> None:
    """Remux *src* → *dst* (stream copy) with seek-friendly index layout."""
    import subprocess

    cmd = [
        ffmpeg, "-y",
        "-i", src,
        "-c", "copy",
        "-map", "0",
        *_container_mux_args(dst),
        "-loglevel", "error",
        dst,
    ]
    proc = subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8",
        errors="replace",
    )
    if abort is not None and abort():
        return
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()
        raise RuntimeError(
            f"容器索引优化失败 (code {proc.returncode})：\n"
            f"{err[-600:] or '无错误输出'}\n"
            f"命令：{' '.join(cmd)}"
        )


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
                 a_codec: str = "aac", a_bitrate: str = "192k",
                 muted: bool = False,
                 vertical: bool = False, mono_color: str = "",
                 playback: PlaybackOptions | None = None,
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
        self._a_codec = a_codec or "aac"
        self._a_br = a_bitrate
        self._muted = muted
        self._vertical = vertical
        self._playback = playback or PlaybackOptions()
        if mono_color:
            c = QtGui.QColor(mono_color)
            self._mono_rgb: tuple[int, int, int] | None = (
                c.red(), c.green(), c.blue())
        else:
            self._mono_rgb = None

    def run(self) -> None:
        import os, tempfile, subprocess
        import numpy as np
        from imageio_ffmpeg import get_ffmpeg_exe
        _ffmpeg = get_ffmpeg_exe()

        tmp_video: str | None = None
        tmp_audio: str | None = None
        ff_proc = None
        try:
            pm = load_pretty_midi(self._midi)
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

            # ── Collect notes for VIDEO (same track mute map as preview) ──
            # Audio uses playback options separately in _render_audio_sync.
            # (pitch, start, end, vel, inst_idx)
            opt = self._playback
            all_notes: list[tuple[int, float, float, int, int]] = []
            for inst_i, inst in enumerate(pm.instruments):
                if not opt.is_track_enabled(inst_i, bool(inst.is_drum)):
                    continue
                for note in inst.notes:
                    if MIN_PITCH <= note.pitch <= MAX_PITCH:
                        all_notes.append(
                            (note.pitch, note.start, note.end,
                             note.velocity, inst_i))

            # ── Pre-compute active notes per frame (event sweep) ────────────
            self.progress.emit("render", 0)
            # (time, delta, pitch, start, end, vel, inst_idx)
            events: list[tuple[float, int, int, float, float, int, int]] = []
            for (pitch, start, end, vel, inst_i) in all_notes:
                events.append((start, +1, pitch, start, end, vel, inst_i))
                events.append((end, -1, pitch, start, end, vel, inst_i))
            # on before off at same t
            events.sort(key=lambda e: (e[0], 0 if e[1] > 0 else 1))

            frame_active_pitches: list[list[int]] = []
            frame_active_notes: list[
                list[tuple[int, float, float, int]]] = []  # (pitch, start, end, vel)

            active_by_key: dict[tuple[int, float, int],
                                tuple[int, float, float, int]] = {}
            pitch_refcount: dict[int, int] = {}
            ev_idx = 0

            for i in range(total_frames):
                t = i / self._fps
                while ev_idx < len(events) and events[ev_idx][0] <= t:
                    _, delta, pitch, start, end, vel, inst_i = events[ev_idx]
                    key = (pitch, start, inst_i)
                    if delta == 1:
                        active_by_key[key] = (pitch, start, end, vel)
                        pitch_refcount[pitch] = pitch_refcount.get(pitch, 0) + 1
                    else:
                        active_by_key.pop(key, None)
                        n = pitch_refcount.get(pitch, 0) - 1
                        if n <= 0:
                            pitch_refcount.pop(pitch, None)
                        else:
                            pitch_refcount[pitch] = n
                    ev_idx += 1
                frame_active_pitches.append(sorted(pitch_refcount.keys()))
                frame_active_notes.append(list(active_by_key.values()))

            # ── Sort notes by start time for per-frame visible-note lookup ──
            all_notes.sort(key=lambda n: n[1])  # sort by start time
            note_idx_start = 0  # sliding-window start: notes that may have ended

            # ── ffmpeg rawvideo pipe (all software codecs) ─────────────────
            out_ext = Path(self._output).suffix.lower() or ".mp4"
            tmp_video = tempfile.mktemp(suffix=out_ext)
            is_vaapi = "vaapi" in self._v_codec
            enc_params = _video_encoder_params(self._v_codec, self._fps)
            # Index/layout flags on the *first* encode when possible so
            # video-only exports also get seek-friendly containers.
            mux_layout = _container_mux_args(tmp_video)

            if is_vaapi:
                ff_cmd = [
                    _ffmpeg, "-y",
                    "-f", "rawvideo", "-vcodec", "rawvideo",
                    "-s", f"{self._w}x{self._h}", "-pix_fmt", "rgba",
                    "-r", str(self._fps), "-i", "-",
                    "-vaapi_device", "/dev/dri/renderD128",
                    "-vf", "format=nv12,hwupload",
                    "-c:v", self._v_codec, "-b:v", self._v_br,
                    "-threads", "0", "-pix_fmt", "yuv420p",
                    *mux_layout,
                    tmp_video,
                ]
            else:
                # Direct ffmpeg pipe so encoder flags (libaom speed etc.) apply.
                ff_cmd = [
                    _ffmpeg, "-y",
                    "-f", "rawvideo", "-vcodec", "rawvideo",
                    "-s", f"{self._w}x{self._h}", "-pix_fmt", "rgba",
                    "-r", str(self._fps), "-i", "-",
                    "-an",
                    "-c:v", self._v_codec,
                    "-b:v", self._v_br,
                    *enc_params,
                    "-pix_fmt", "yuv420p",
                    *mux_layout,
                    tmp_video,
                ]
            ff_proc = subprocess.Popen(
                ff_cmd, stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
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
                        name = _midi_note_name(pitch)
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
                        name = _midi_note_name(pitch)
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
            for (pitch, start, end, vel, _inst_i) in all_notes:
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
                                name = _midi_note_name(pitch)
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
                                name = _midi_note_name(pitch)
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
                try:
                    ff_proc.stdin.write(arr.tobytes())
                except BrokenPipeError:
                    err = (ff_proc.stderr.read() if ff_proc.stderr else b"")
                    if isinstance(err, bytes):
                        err = err.decode("utf-8", errors="replace")
                    raise RuntimeError(
                        f"ffmpeg 编码中断：\n{err[-800:] or '无错误输出'}"
                    ) from None

                # Progress (0-100 %)
                if i % max(1, total_frames // 100) == 0:
                    self.progress.emit("render",
                                       int(i / total_frames * 100))

            try:
                ff_proc.stdin.close()
            except Exception:
                pass
            if self.isInterruptionRequested():
                ff_proc.kill()
                ff_proc.wait()
                return
            stderr_out = b""
            if ff_proc.stderr is not None:
                stderr_out = ff_proc.stderr.read()
            ff_proc.wait()
            if self.isInterruptionRequested():
                return
            if ff_proc.returncode != 0:
                err = stderr_out.decode("utf-8", errors="replace") if isinstance(
                    stderr_out, (bytes, bytearray)) else str(stderr_out)
                raise RuntimeError(
                    f"ffmpeg 编码失败 (code {ff_proc.returncode})：\n"
                    f"{err[-800:] or '无错误输出'}"
                )
            self.progress.emit("render", 100)

            # ── Audio (if not muted) ─────────────────────────────────────
            if self.isInterruptionRequested():
                return
            if not self._muted and self._sf:
                self.progress.emit("audio", 0)
                tmp_audio = _render_audio_sync(
                    self._midi, self._sf, self._a_br,
                    progress_cb=lambda pct: self.progress.emit("audio", pct),
                    should_abort=self.isInterruptionRequested,
                    playback=self._playback,
                )
                if self.isInterruptionRequested():
                    return

            # ── Mux or finalize ──────────────────────────────────────────
            if self.isInterruptionRequested():
                return
            if tmp_audio:
                self.progress.emit("mux", 0)
                a_codec = _audio_mux_args(self._a_codec, self._a_br)
                # Write final file with seek-friendly index (Cues / moov)
                cmd = [
                    _ffmpeg, "-y",
                    "-i", tmp_video, "-i", tmp_audio,
                    "-c:v", "copy", *a_codec,
                    *_container_mux_args(self._output),
                    "-threads", "0",
                    "-loglevel", "error",
                    "-progress", "pipe:1",
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
                    if self.isInterruptionRequested():
                        proc.kill()
                        proc.wait()
                        return
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
                if self.isInterruptionRequested():
                    return
                if proc.returncode != 0:
                    err = proc.stderr.read() if proc.stderr else ""
                    # Prefer the real error lines, not the long version banner
                    err_lines = [
                        ln for ln in (err or "").splitlines()
                        if ln.strip() and not ln.startswith("  ")
                        and "configuration:" not in ln
                        and "built with" not in ln
                        and "Copyright" not in ln
                        and "ffmpeg version" not in ln
                    ]
                    useful = "\n".join(err_lines[-30:]) or (err or "")[-800:]
                    raise RuntimeError(
                        f"ffmpeg 合成失败 (code {proc.returncode})：\n"
                        f"音频编码={self._a_codec}  命令：{' '.join(cmd)}\n"
                        f"{useful}"
                    )
            else:
                # Video-only: remux once so MKV/WebM get Cues at the front
                # (and MP4 gets faststart).  Falls back to plain rename.
                self.progress.emit("mux", 0)
                try:
                    _finalize_container(
                        _ffmpeg, tmp_video, self._output,
                        abort=self.isInterruptionRequested,
                    )
                    tmp_video = None  # destination is the final file
                except RuntimeError:
                    # Older ffmpeg without reserve_index_space, etc.
                    os.rename(tmp_video, self._output)
                    tmp_video = None

            if self.isInterruptionRequested():
                return
            self.progress.emit("mux", 100)
            self.progress.emit("done", 100)
            self.finished.emit(self._output)

        except Exception as exc:
            self.error.emit(str(exc))
        finally:
            for p in (tmp_video, tmp_audio):
                if p and os.path.exists(p):
                    try:
                        os.unlink(p)
                    except OSError:
                        pass  # file may still be locked by ffmpeg on Windows


def _render_audio_sync(midi_path: str, sf_path: str, bitrate: str,
                       progress_cb=None, should_abort=None,
                       playback: PlaybackOptions | None = None) -> str:
    """Render MIDI→WAV synchronously, return path to WAV file.

    If *progress_cb* is given it is called as ``progress_cb(pct)``
    with ``pct`` in 0–100.
    If *should_abort* is a zero-arg callable returning True, rendering stops
    early and returns ``""``.
    *playback* controls track mute, out-of-range notes, and timbre matching.
    """
    import wave
    import os
    import numpy as np
    setup_fluidsynth_dll()
    try:
        import fluidsynth
    except (ImportError, OSError) as exc:
        raise RuntimeError(fluidsynth_status_message() + f"\n\n详细：{exc}") from exc
    import tempfile

    opt = playback or PlaybackOptions()
    pm = load_pretty_midi(midi_path)
    duration = pm.get_end_time()
    RATE = 44100

    fs = fluidsynth.Synth()
    try:
        sfid = fs.sfload(sf_path)
        if sfid < 0:
            raise RuntimeError(f"SoundFont 加载失败 (code {sfid}): {sf_path}")

        # Map enabled instruments → channels
        inst_ch: dict[int, int] = {}
        ch = 0
        for i, inst in enumerate(pm.instruments):
            if not opt.is_track_enabled(i, bool(inst.is_drum)):
                continue
            if inst.is_drum:
                inst_ch[i] = 9
                try:
                    fs.program_select(9, sfid, 128, 0)
                except Exception:
                    fs.program_select(9, sfid, 0, 0)
                continue
            if ch == 9:
                ch = 10
            if ch > 15:
                ch = 0
            inst_ch[i] = ch
            prog = (int(getattr(inst, "program", 0) or 0)
                    if opt.use_track_programs else 0)
            try:
                fs.program_select(ch, sfid, 0, prog)
            except Exception:
                fs.program_select(ch, sfid, 0, 0)
            ch += 1
        if not inst_ch:
            fs.program_select(0, sfid, 0, 0)

        # Events: (time, kind, channel, pitch, velocity)
        events: list[tuple[float, str, int, int, int]] = []
        for i, inst in enumerate(pm.instruments):
            if i not in inst_ch:
                continue
            c = inst_ch[i]
            for note in inst.notes:
                if not opt.pitch_ok(note.pitch):
                    continue
                events.append((note.start, "on", c, note.pitch, note.velocity or 100))
                events.append((note.end, "off", c, note.pitch, 0))
        events.sort(key=lambda e: (e[0], 0 if e[1] == "on" else 1))

        total_samples = int(RATE * (duration + 1.5))
        audio = np.empty(total_samples, dtype=np.int16)
        ev_idx = 0
        t = 0.0
        dt = 0.05
        last_pct = -1
        total_steps = (duration + 1.0) / dt
        step = 0
        samp_pos = 0
        voice_count: dict[tuple[int, int], int] = {}
        while t < duration + 1.0:
            if should_abort is not None and should_abort():
                return ""
            while ev_idx < len(events) and events[ev_idx][0] <= t:
                _time, kind, c, pitch, vel = events[ev_idx]
                key = (c, pitch)
                if kind == "on":
                    if voice_count.get(key, 0) > 0:
                        try:
                            fs.noteoff(c, pitch)
                        except Exception:
                            pass
                    fs.noteon(c, pitch, vel)
                    voice_count[key] = voice_count.get(key, 0) + 1
                else:
                    n = voice_count.get(key, 0)
                    if n <= 1:
                        fs.noteoff(c, pitch)
                        voice_count.pop(key, None)
                    else:
                        voice_count[key] = n - 1
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
        return tmp
    finally:
        try:
            fs.delete()
        except Exception:
            pass


def _ffmpeg_audio_args(fmt: str, bitrate: str, codec: str = "") -> list[str]:
    """Codec / container args for standalone audio conversion.

    *codec* is a UI id: ``mp3`` / ``aac`` / ``opus`` / ``vorbis`` / ``flac`` /
    ``pcm`` / ``""`` (infer from *fmt*).
    """
    fmt = (fmt or "").lower().lstrip(".")
    c = (codec or "").lower()
    br_raw = (bitrate or "192k").strip()
    if br_raw.endswith(("k", "K", "M")):
        br = br_raw
        try:
            br_num = float(br_raw[:-1])
        except ValueError:
            br_num = 192.0
    else:
        br = f"{br_raw}k"
        try:
            br_num = float(br_raw)
        except ValueError:
            br_num = 192.0
            br = "192k"

    # Infer codec from container when not specified
    if not c:
        c = {
            "mp3": "mp3",
            "aac": "aac",
            "m4a": "aac",
            "mp4": "aac",
            "ogg": "vorbis",
            "oga": "vorbis",
            "flac": "flac",
            "wav": "pcm",
        }.get(fmt, "aac")

    if c in ("mp3", "libmp3lame"):
        return ["-c:a", "libmp3lame", "-b:a", br]
    if c in ("aac",):
        args = ["-c:a", "aac", "-b:a", br]
        # Raw ADTS stream for .aac files
        if fmt == "aac":
            args += ["-f", "adts"]
        return args
    if c in ("opus", "libopus"):
        # .m4a defaults to the restrictive "ipod" muxer which rejects Opus;
        # force ISO BMFF (mp4) so Opus-in-M4A works (same as .mp4).
        args = ["-c:a", "libopus", "-b:a", br, "-ar", "48000"]
        if fmt in ("m4a", "mp4", "mov"):
            args += ["-f", "mp4"]
        elif fmt == "ogg":
            args += ["-f", "ogg"]
        return args
    if c in ("vorbis", "libvorbis"):
        q = max(0, min(10, int(round((br_num - 64) / 25.6))))
        return ["-c:a", "libvorbis", "-q:a", str(q)]
    if c in ("flac",):
        return ["-c:a", "flac"]
    if c in ("pcm", "wav", "pcm_s16le"):
        return ["-c:a", "pcm_s16le"]
    return ["-c:a", "aac", "-b:a", br]


# Standalone audio export: container → [(label, codec_id), …]
STANDALONE_AUDIO_BY_EXT: dict[str, list[tuple[str, str]]] = {
    ".mp3":  [("MP3", "mp3")],
    ".aac":  [("AAC", "aac")],
    ".m4a":  [("AAC", "aac"), ("Opus", "opus")],
    ".ogg":  [("Vorbis", "vorbis"), ("Opus", "opus")],
    ".flac": [("FLAC", "flac")],
    ".wav":  [("PCM", "pcm")],
}

_STANDALONE_ENCODER_NEED = {
    "mp3": ("libmp3lame",),
    "aac": ("aac",),
    "opus": ("libopus", "opus"),
    "vorbis": ("libvorbis", "vorbis"),
    "flac": ("flac",),
    "pcm": ("pcm_s16le",),  # always present as built-in
}


def standalone_audio_codecs_for_path(path: str) -> list[tuple[str, str]]:
    """Return [(label, codec_id), …] for standalone audio export of *path*."""
    ext = Path(path).suffix.lower()
    opts = list(STANDALONE_AUDIO_BY_EXT.get(ext, STANDALONE_AUDIO_BY_EXT[".mp3"]))
    aenc = _get_available_audio_encoders()
    # pcm_s16le may not appear as "encoder" the same way — always allow pcm
    out: list[tuple[str, str]] = []
    for label, cid in opts:
        need = _STANDALONE_ENCODER_NEED.get(cid, ())
        if cid == "pcm" or not need or any(n in aenc for n in need):
            out.append((label, cid))
    return out or opts


def _convert_wav_with_ffmpeg(wav_path: str, output_path: str,
                             bitrate: str = "192k",
                             codec: str = "") -> None:
    """Convert WAV → target format using the bundled imageio-ffmpeg binary.

    Avoids pydub's dependency on a system ``ffmpeg`` on PATH (which causes
    ``WinError 2`` on many Windows machines).
    """
    import os
    import shutil
    import subprocess
    from imageio_ffmpeg import get_ffmpeg_exe

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    fmt = out.suffix.lstrip(".").lower() or "wav"

    if fmt == "wav" and (not codec or codec in ("pcm", "wav", "pcm_s16le")):
        # Same format — just move/copy into place
        if os.path.abspath(wav_path) != os.path.abspath(str(out)):
            if out.exists():
                out.unlink()
            shutil.move(wav_path, str(out))
        return

    ffmpeg = get_ffmpeg_exe()
    cmd = [
        ffmpeg, "-y",
        "-i", wav_path,
        *_ffmpeg_audio_args(fmt, bitrate, codec),
        "-vn",
        str(out),
    ]
    proc = subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8",
        errors="replace",
    )
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()
        # Drop noisy banner lines
        err_lines = [
            ln for ln in err.splitlines()
            if ln.strip() and "configuration:" not in ln
            and "built with" not in ln
            and "Copyright" not in ln
            and "ffmpeg version" not in ln
            and not ln.startswith("  ")
        ]
        useful = "\n".join(err_lines[-30:]) or err[-800:]
        raise RuntimeError(
            f"音频转码失败 (ffmpeg code {proc.returncode})：\n"
            f"编码={codec or '(auto)'}  命令：{' '.join(cmd)}\n"
            f"{useful or '无错误输出'}"
        )


# ═══════════════════════════════════════════════════════════════════════════
# Audio export worker
# ═══════════════════════════════════════════════════════════════════════════

class AudioExportWorker(QtCore.QThread):
    """Render MIDI + SoundFont → audio using pyfluidsynth + bundled ffmpeg.

    Renders mono WAV via fluidsynth, then converts to the target format
    with imageio-ffmpeg (no system ffmpeg / pydub required).
    """

    progress = QtCore.Signal(str, int)  # (message, pct 0-100 or -1)
    finished = QtCore.Signal(str)
    error = QtCore.Signal(str)

    def __init__(self, midi_path: str, sf_path: str, output_path: str,
                 bitrate: str = "192k", a_codec: str = "",
                 playback: PlaybackOptions | None = None, parent=None):
        super().__init__(parent)
        self._midi = midi_path
        self._sf = sf_path
        self._output = output_path
        self._bitrate = bitrate
        self._a_codec = a_codec or ""
        self._playback = playback or PlaybackOptions()

    def run(self) -> None:
        import os

        tmp_wav: str | None = None
        try:
            if not self._sf:
                raise RuntimeError("未选择 SoundFont")
            if not Path(self._sf).exists():
                raise RuntimeError(f"SoundFont 不存在：{self._sf}")
            if not self._midi or not Path(self._midi).exists():
                raise RuntimeError(f"MIDI 文件不存在：{self._midi}")

            self.progress.emit("正在渲染音频…", 0)

            def _prog(pct: int) -> None:
                self.progress.emit("正在渲染音频…", pct)

            tmp_wav = _render_audio_sync(
                self._midi, self._sf, self._bitrate, progress_cb=_prog,
                should_abort=self.isInterruptionRequested,
                playback=self._playback,
            )
            if self.isInterruptionRequested() or not tmp_wav:
                return

            out_fmt = Path(self._output).suffix.lstrip(".").lower() or "wav"
            codec = self._a_codec
            label = (codec or out_fmt).upper()
            if out_fmt == "wav" and (not codec or codec in ("pcm", "wav")):
                self.progress.emit("正在写入 WAV…", -1)
            else:
                self.progress.emit(f"正在转换为 {label}…", -1)

            # _convert_wav_with_ffmpeg moves WAV for .wav output and may
            # consume tmp_wav — clear the handle so finally doesn't unlink
            # a path that was already moved.
            _convert_wav_with_ffmpeg(
                tmp_wav, self._output, self._bitrate, codec=codec,
            )
            if (out_fmt == "wav" and (not codec or codec in ("pcm", "wav"))
                    or not os.path.exists(tmp_wav)):
                tmp_wav = None

            self.finished.emit(self._output)

        except Exception as exc:
            self.error.emit(f"{type(exc).__name__}：{exc}")
        finally:
            if tmp_wav and os.path.exists(tmp_wav):
                try:
                    os.unlink(tmp_wav)
                except OSError:
                    pass

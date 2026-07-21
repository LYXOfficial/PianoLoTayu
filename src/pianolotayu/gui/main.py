"""PianoLoTayu GUI — PySide6-based graphical interface."""

from __future__ import annotations

import os
import time
from pathlib import Path

# ── Detect system Qt plugin path before PySide6 imports ──────────────────
_QPA_PLATFORMTHEME = os.environ.get("QT_QPA_PLATFORMTHEME", "")
_QT_PLUGIN_PATH = os.environ.get("QT_PLUGIN_PATH", "")

if not _QT_PLUGIN_PATH:
    _CANDIDATES = [
        Path("/usr/lib/qt6/plugins"),
        Path("/usr/lib64/qt6/plugins"),
        Path("/usr/lib/qt/plugins"),
        Path("/usr/local/lib/qt6/plugins"),
    ]
    for _p in _CANDIDATES:
        if _p.is_dir() and any(_p.iterdir()):
            _QT_PLUGIN_PATH = str(_p)
            os.environ["QT_PLUGIN_PATH"] = _QT_PLUGIN_PATH
            break

if not _QPA_PLATFORMTHEME and os.environ.get("XDG_CURRENT_DESKTOP", "").lower() == "kde":
    os.environ["QT_QPA_PLATFORMTHEME"] = "kde"

from PySide6 import QtWidgets, QtGui, QtCore
from .ui_main import Ui_Form
from .drop_label import DropLabel
from .win32_utils import (
    TaskbarProgress, BeepSuppressor, app_icon, set_app_user_model_id,
    enable_elevated_file_drop, reassert_elevated_file_drop,
    prepare_elevated_drop_filters, is_process_elevated,
    attach_parent_console, log_console,
)
from ..config import DEFAULTS, VERSION
import traceback

# Tooltips — hard-coded Chinese for now (i18n later)
_TOOLTIPS = {
    "sr": "分析采样率，单位 Hz（默认：22050）",
    "n_fft": "FFT 窗口大小（默认：4096，在 22.05kHz 下约 5.4 Hz 分辨率）",
    "hop_length": "STFT 帧间跳跃采样数（默认：256，在 22.05kHz 下约 12ms）",
    "threshold": "峰值检测阈值，低于帧最大值的 dB 数（默认：20）",
    "max_notes": "每帧最多同时音符数（默认：16）",
    "min_duration": "最短音符时长，单位毫秒（默认：30）",
    "dynamic_range": "力度映射动态范围，单位 dB（默认：60）",
    "no_piano_limit": "禁用钢琴音域八度折叠。超出钢琴音域（A0–C8）的频率将保留原始 MIDI 值（0–127），而非按八度折叠入音域内",
    "high_damp": "（实验，不建议开启）高频力度衰减。0=关闭，0.35=温和，0.6=强力。降低中央 C 以上音符的力度，避免高音刺耳",
    "mid_boost": "（实验，不建议开启）中频力度增强，突出人声/钢琴。0=关闭，0.6=适度，1.2=强力。增强 C5 附近音符的力度",
}


_HINT_TEXT = (
    '<span style="font-size:36pt; color: #555;">+</span><br>'
    '<span style="font-size:10pt; color: #888;">'
    "拖拽文件至此处或点击打开文件</span><br>"
    '<span style="font-size:9pt; color: #aaa;">'
    "支持格式：wav, flac, mp3, ogg, m4a, aac,</span><br/>"
    '<span style="font-size:9pt; color: #aaa;">'
    "mid, midi（仅预览）</span>"
)


# ── Widget swap helper ────────────────────────────────────────────────────

def _swap_widget(old: QtWidgets.QWidget, new: QtWidgets.QWidget) -> None:
    new.setSizePolicy(old.sizePolicy())
    new.setMinimumSize(old.minimumSize())
    new.setGeometry(old.geometry())
    top = old.window()
    layout = _find_containing_layout(top, old)
    if layout is not None:
        idx = layout.indexOf(old)
        if idx >= 0:
            layout.removeWidget(old)
            layout.insertWidget(idx, new)
    old.hide()
    old.setParent(None)
    old.deleteLater()


def _find_containing_layout(
    root: QtWidgets.QWidget, target: QtWidgets.QWidget,
) -> QtWidgets.QLayout | None:
    top_layout = root.layout()
    if top_layout is None:
        return None
    return _search_layout(top_layout, target)


def _search_layout(
    layout: QtWidgets.QLayout, target: QtWidgets.QWidget,
) -> QtWidgets.QLayout | None:
    if layout.indexOf(target) >= 0:
        return layout
    for i in range(layout.count()):
        item = layout.itemAt(i)
        if item is None:
            continue
        child = item.layout()
        if child is not None:
            found = _search_layout(child, target)
            if found is not None:
                return found
    return None


# ── Configuration → widget binding ────────────────────────────────────────

_WIDGET_CONFIG_MAP = {
    "sr":            ("sampleRateBox",    "label_3"),
    "n_fft":         ("windowFFTBox",     "label_4"),
    "hop_length":    ("hopLengthBox",     "label_5"),
    "threshold":     ("thresholdBox",     "label_6"),
    "max_notes":     ("maxFrameNotesBox", "label_7"),
    "min_duration":  ("minDurationBox",   "label_8"),
    "dynamic_range": ("dynamicRangeBox",  "label_9"),
    "no_piano_limit":("pianoLimitSwitch", "label_10"),
    "high_damp":     ("highDampBox",      "label_12"),
    "mid_boost":     ("voiceBoostBox",    "label_13"),
}

class ConversionWorker(QtCore.QThread):
    """Runs the audio→MIDI pipeline in a background thread."""

    # Fine-grained 0–1000 so the UI can animate without 10–20% jumps.
    progress = QtCore.Signal(int)
    # Do NOT name this ``finished`` — that shadows QThread.finished and
    # can break thread lifetime / wait() on some platforms (invalid handle).
    succeeded = QtCore.Signal(str)      # output path
    failed = QtCore.Signal(str)         # error message

    # Stage map: (start_permille, end_permille)
    # Tiny leading "placebo" band so the bar moves as soon as convert starts
    # (import + open file can take a second with zero real work reported).
    _STAGE_BOOT = (0, 30)          # click → imports ready
    _STAGE_LOAD = (30, 100)        # load_audio
    _STAGE_STFT = (100, 500)       # compute_stft
    _STAGE_ANALYZE = (500, 780)    # analyze_frames
    _STAGE_MIDI = (780, 950)       # create_midi
    _STAGE_SAVE = (950, 1000)      # write .mid

    def __init__(self, input_path: str, output_path: str, config: dict,
                 parent=None):
        super().__init__(parent)
        self._input = input_path
        self._output = output_path
        self._cfg = config

    def _emit_stage(self, stage: tuple[int, int], frac: float) -> None:
        """Map a 0–1 stage fraction into overall 0–1000 progress."""
        lo, hi = stage
        frac = 0.0 if frac < 0.0 else (1.0 if frac > 1.0 else float(frac))
        self.progress.emit(int(lo + (hi - lo) * frac))

    def _say(self, text: str) -> None:
        try:
            log_console(f"[convert] {text}")
        except Exception:
            pass

    @staticmethod
    def _call_with_optional_progress(fn, *args, progress_cb=None, **kwargs):
        """Call *fn*; pass progress_cb only if the function accepts it.

        Lets a partially-synced tree (new main.py + old analysis.py) keep
        working instead of raising TypeError.
        """
        import inspect
        try:
            params = inspect.signature(fn).parameters
            accepts = (
                "progress_cb" in params
                or any(p.kind == inspect.Parameter.VAR_KEYWORD
                       for p in params.values())
            )
        except (TypeError, ValueError):
            accepts = False
        if accepts and progress_cb is not None:
            kwargs["progress_cb"] = progress_cb
        return fn(*args, **kwargs)

    def run(self) -> None:
        try:
            # Immediate placebo so the bar isn't stuck at 0 while heavy imports load
            self._say("boot…")
            self._emit_stage(self._STAGE_BOOT, 0.15)
            if self.isInterruptionRequested():
                return

            # Convert path: numpy + soundfile, ffmpeg fallback via imageio-ffmpeg
            self._say("import convert modules…")
            from ..convert.audio import load_audio, compute_stft
            self._emit_stage(self._STAGE_BOOT, 0.55)
            from ..convert.analysis import analyze_frames
            self._emit_stage(self._STAGE_BOOT, 0.8)
            from ..convert.midi_writer import create_midi, save_midi
            self._emit_stage(self._STAGE_BOOT, 1.0)
            if self.isInterruptionRequested():
                return

            self._say(f"load audio: {self._input}")
            self._emit_stage(self._STAGE_LOAD, 0.0)

            def _on_load(frac: float) -> None:
                if not self.isInterruptionRequested():
                    self._emit_stage(self._STAGE_LOAD, frac)

            signal, sr = load_audio(
                self._input, sr=self._cfg["sr"], progress_cb=_on_load,
            )
            self._emit_stage(self._STAGE_LOAD, 1.0)
            if self.isInterruptionRequested():
                return

            self._say(
                f"loaded {len(signal)} samples @ {sr} Hz "
                f"({len(signal) / max(sr, 1):.1f}s) — STFT…"
            )
            # STFT is often the long silent gap after load (esp. hop=256).
            self._emit_stage(self._STAGE_STFT, 0.0)

            def _on_stft(frac: float) -> None:
                if not self.isInterruptionRequested():
                    self._emit_stage(self._STAGE_STFT, frac)

            D_db, freqs, times = self._call_with_optional_progress(
                compute_stft,
                signal, sr,
                n_fft=self._cfg["n_fft"],
                hop_length=self._cfg["hop_length"],
                progress_cb=_on_stft,
            )
            # Free waveform ASAP — STFT already holds the spectrum
            del signal
            self._emit_stage(self._STAGE_STFT, 1.0)
            if self.isInterruptionRequested():
                return

            self._say(
                f"STFT done {getattr(D_db, 'shape', None)} — analyze…"
            )
            self._emit_stage(self._STAGE_ANALYZE, 0.0)

            def _on_analyze(frac: float) -> None:
                if not self.isInterruptionRequested():
                    self._emit_stage(self._STAGE_ANALYZE, frac)

            frame_notes = self._call_with_optional_progress(
                analyze_frames,
                D_db, freqs, times, sr, self._cfg["hop_length"],
                threshold_db=self._cfg["threshold"],
                max_notes=self._cfg["max_notes"],
                dynamic_range_db=self._cfg["dynamic_range"],
                piano_limit=not self._cfg["no_piano_limit"],
                high_damp=self._cfg["high_damp"],
                mid_boost=self._cfg["mid_boost"],
                progress_cb=_on_analyze,
            )
            del D_db
            self._emit_stage(self._STAGE_ANALYZE, 1.0)
            if self.isInterruptionRequested():
                return

            self._say("build MIDI…")
            def _on_midi(frac: float) -> None:
                if not self.isInterruptionRequested():
                    self._emit_stage(self._STAGE_MIDI, frac)

            midi = self._call_with_optional_progress(
                create_midi,
                frame_notes, sr, self._cfg["hop_length"],
                min_duration_ms=self._cfg["min_duration"],
                progress_cb=_on_midi,
            )
            self._emit_stage(self._STAGE_MIDI, 1.0)
            if self.isInterruptionRequested():
                return

            self._emit_stage(self._STAGE_SAVE, 0.0)
            save_midi(midi, self._output)
            self._emit_stage(self._STAGE_SAVE, 1.0)
            self._say(f"done → {self._output}")
            self.succeeded.emit(self._output)

        except Exception as exc:
            msg = f"{type(exc).__name__}：{exc}\n\n{traceback.format_exc()}"
            try:
                log_console(f"[convert] ERROR\n{msg}")
            except Exception:
                pass
            self.failed.emit(msg)


class PianoLoTayu(Ui_Form, QtWidgets.QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setupUi(self)
        self.retranslateUi(self)
        self._worker: ConversionWorker | None = None
        self._taskbar = TaskbarProgress(self)
        self._elevated_drop_filter = None  # keep alive for WM_DROPFILES
        self._elevated_drag_poll: QtCore.QTimer | None = None
        # Smooth progress: worker reports 0–1000; UI linearly interpolates
        self._prog_target = 0.0
        self._prog_display = 0.0
        self._prog_active = False
        self._prog_lerp_from = 0.0
        self._prog_lerp_to = 0.0
        self._prog_lerp_t0 = 0.0
        self._prog_lerp_dur = 0.25  # seconds
        self._prog_timer = QtCore.QTimer(self)
        self._prog_timer.setInterval(16)  # ~60 fps
        self._prog_timer.timeout.connect(self._tick_progress)
        # Finer bar range so 1‰ steps are visible (Windows taskbar too)
        self.progressBar.setRange(0, 1000)
        self.progressBar.setValue(0)
        self._init_drop_area()
        self._init_io()
        self._init_convert()
        self._bind_defaults()

    def showEvent(self, event: QtGui.QShowEvent) -> None:
        super().showEvent(event)
        # HWND exists after first show — enable admin←Explorer file drops.
        # Re-assert every show: Qt can re-RegisterDragDrop after the first paint.
        if self._elevated_drop_filter is None:
            def _on_elevated_drop(
                paths: list[str],
                client_pt: tuple[int, int] | None = None,
            ) -> None:
                # WM_DROPFILES is registered on the whole top-level HWND —
                # only accept when the drop lands on the drop-target box.
                if not self._is_over_drop_area(client_pt):
                    try:
                        self.drop_area.set_external_drag(False)
                    except Exception:
                        pass
                    return
                ok = [
                    p for p in paths
                    if Path(p).suffix.lower() in DropLabel.SUPPORTED_SUFFIXES
                ]
                # Clear drag highlight + brief depth flash (no OLE dragLeave)
                try:
                    self.drop_area.set_external_drag(False)
                    if ok:
                        self.drop_area.pulse_drop_feedback()
                except Exception:
                    pass
                if ok:
                    self._on_files_selected(ok)
            self._elevated_drop_filter = enable_elevated_file_drop(
                self, _on_elevated_drop,
            )
            # When elevated, OLE is revoked — DropLabel's Qt drag events won't
            # fire.  Delayed re-assert catches Qt re-registering OLE after the
            # first paint/layout pass.  Poll cursor for drag-depth animation.
            if is_process_elevated() and self._elevated_drop_filter is not None:
                QtCore.QTimer.singleShot(
                    0, lambda: reassert_elevated_file_drop(self),
                )
                QtCore.QTimer.singleShot(
                    200, lambda: reassert_elevated_file_drop(self),
                )
                self._start_elevated_drag_poll()
        else:
            reassert_elevated_file_drop(self)

    def _is_over_drop_area(
        self, client_pt: tuple[int, int] | None = None,
    ) -> bool:
        """True if *client_pt* (top-level client coords) or the cursor is
        inside the dashed drop box — nowhere else on the window counts."""
        area = getattr(self, "drop_area", None)
        if area is None:
            return False
        if client_pt is not None:
            # DragQueryPoint → coords relative to the window that got WM_DROPFILES
            global_pos = self.mapToGlobal(
                QtCore.QPoint(int(client_pt[0]), int(client_pt[1])),
            )
        else:
            global_pos = QtGui.QCursor.pos()
        local = area.mapFromGlobal(global_pos)
        return area.rect().contains(local)

    def _start_elevated_drag_poll(self) -> None:
        """Poll LMB + cursor over drop area → DRAG depth (elevated / no OLE)."""
        if self._elevated_drag_poll is not None:
            return
        timer = QtCore.QTimer(self)
        timer.setInterval(33)  # ~30 Hz — smooth enough for the fill animation

        def _tick() -> None:
            area = getattr(self, "drop_area", None)
            if area is None:
                return
            # Physical LMB: Explorer holds capture so QApplication.mouseButtons
            # is often empty during shell drags.
            lmb = False
            try:
                import sys
                if sys.platform == "win32":
                    import ctypes
                    # VK_LBUTTON = 0x01; high bit set while pressed
                    lmb = bool(
                        ctypes.windll.user32.GetAsyncKeyState(0x01) & 0x8000
                    )
                else:
                    lmb = bool(
                        QtWidgets.QApplication.mouseButtons()
                        & QtCore.Qt.MouseButton.LeftButton
                    )
            except Exception:
                lmb = bool(
                    QtWidgets.QApplication.mouseButtons()
                    & QtCore.Qt.MouseButton.LeftButton
                )
            over = self._is_over_drop_area()
            # Only highlight when LMB is down *and* cursor is over the box
            # (same visual as OLE dragEnter).  Outside the box = no effect.
            area.set_external_drag(lmb and over)

        timer.timeout.connect(_tick)
        timer.start()
        self._elevated_drag_poll = timer

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        if self._worker is not None:
            try:
                if self._worker.isRunning():
                    self._worker.requestInterruption()
                    self._worker.quit()
                    try:
                        self._worker.wait(3000)
                    except Exception:
                        # WinError 6 if native handle already gone
                        pass
            except Exception:
                pass
            self._worker = None
        super().closeEvent(event)

    def _init_drop_area(self) -> None:
        old = self.addFileArea
        self.drop_area = DropLabel(hint=_HINT_TEXT, parent=self)
        self.drop_area.setObjectName("addFileArea")
        _swap_widget(old, self.drop_area)

        # Signals → file selection
        self.drop_area.clicked.connect(self._open_file_dialog)
        self.drop_area.files_dropped.connect(self._on_files_selected)

    # ── File / output path ──────────────────────────────────────────────
    _FILTER = ("音频文件 (*.wav *.flac *.mp3 *.ogg *.m4a *.aac);;"
               "MIDI 文件 (*.mid *.midi);;所有文件 (*)")

    def _init_io(self) -> None:
        """Set up read-only input field and clickable output label."""
        self.filePathEdit.setReadOnly(True)
        self.filePathEdit.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)
        self.outputLabel.setWordWrap(True)
        self.outputLabel.setTextFormat(QtCore.Qt.TextFormat.RichText)
        self._output_path: Path | None = None
        self.outputLabel.mousePressEvent = lambda _: self._change_output_dir()

    def _open_file_dialog(self) -> None:
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "选择音频文件", "", self._FILTER,
        )
        if path:
            self._on_files_selected([path])

    def _on_files_selected(self, paths: list[str]) -> None:
        if not paths:
            return
        input_path = Path(paths[0])
        self.filePathEdit.setText(str(input_path))

        if input_path.suffix.lower() in (".mid", ".midi"):
            # MIDI file — preview only, no conversion needed
            self._set_output(input_path)
            self.convertButton.setEnabled(False)
            self.previewButton.setEnabled(True)
        else:
            # Audio file — needs conversion
            candidate = input_path.with_suffix(".mid")
            self._set_output(candidate)
            self.convertButton.setEnabled(True)
            self.previewButton.setEnabled(False)

    def _set_output(self, path: Path) -> None:
        self._output_path = path
        suffix = "（覆盖）" if path.exists() else ""
        link_c = self.palette().color(QtGui.QPalette.ColorRole.Link)
        link_color = link_c.name() if link_c.isValid() else "#44a"
        self.outputLabel.setText(
            f'<span style="color: #888;">输出路径：</span>'
            f'<a href="#" style="color: {link_color};">{path}{suffix}</a>'
        )

    def _change_output_dir(self) -> None:
        if self._output_path is None:
            return
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "选择输出路径", str(self._output_path),
            "MIDI 文件 (*.mid);;所有文件 (*)",
        )
        if path:
            self._set_output(Path(path))

    # ── Conversion ──────────────────────────────────────────────────────
    def _init_convert(self) -> None:
        self.convertButton.clicked.connect(self._start_conversion)
        self.previewButton.clicked.connect(self._open_preview)
        self.previewButton.setEnabled(False)

    def _open_preview(self) -> None:
        if self._output_path is None:
            QtWidgets.QMessageBox.warning(self, "提示", "请先选择文件。")
            return
        if not self._output_path.exists():
            QtWidgets.QMessageBox.warning(
                self, "提示", "MIDI 文件尚未生成，请先点击「开始转换」。")
            return
        from .view_window import PianoRollWindow
        self._preview_win = PianoRollWindow(str(self._output_path))
        self._preview_win.setAttribute(QtCore.Qt.WidgetAttribute.WA_DeleteOnClose)
        self._preview_win.show()

    def _start_conversion(self) -> None:
        if self._output_path is None:
            QtWidgets.QMessageBox.warning(
                self, "提示", "请先选择音频文件",
            )
            return

        cfg = self.collect_config()
        self._worker = ConversionWorker(
            str(self.filePathEdit.text()),
            str(self._output_path),
            cfg,
            self,
        )
        self._worker.progress.connect(self._on_progress)
        self._worker.succeeded.connect(self._on_conversion_done)
        self._worker.failed.connect(self._on_conversion_error)

        self._prog_active = True
        # Placebo: linear lerp 0 → ~1.2% so the bar isn't empty during import
        self._begin_prog_lerp(0.0, 12.0, duration=0.20)
        self._prog_timer.start()
        self.convertButton.setEnabled(False)
        self._worker.start()

    def _begin_prog_lerp(
        self, start: float, end: float, duration: float | None = None,
    ) -> None:
        """Start a linear interpolation of the displayed progress."""
        start = max(0.0, min(1000.0, float(start)))
        end = max(0.0, min(1000.0, float(end)))
        if end < start:
            end = start  # progress only moves forward
        self._prog_lerp_from = start
        self._prog_lerp_to = end
        self._prog_target = end
        self._prog_display = start
        self._prog_lerp_t0 = time.monotonic()
        if duration is None:
            gap = end - start
            # Small gaps (boot/load steps) still get a visible glide
            duration = max(0.10, min(0.28, 0.10 + gap / 1000.0 * 0.25))
        self._prog_lerp_dur = max(0.08, float(duration))
        if not self._prog_timer.isActive():
            self._prog_timer.start()

    def _on_progress(self, permille: int) -> None:
        """Worker reports 0–1000; always ease toward it with linear interpolation.

        Extend the target from the *current displayed* value — do not snap
        small steps (that removed boot/load glide) and do not reset display
        to an old start (that made stages look frozen then jump).
        """
        new = max(self._prog_target, float(permille))
        if new <= self._prog_display + 0.15 and new <= self._prog_target + 0.15:
            return
        # If a lerp is mid-flight, continue from wherever the bar is now
        self._prog_lerp_from = self._prog_display
        self._prog_lerp_to = new
        self._prog_target = new
        self._prog_lerp_t0 = time.monotonic()
        gap = max(0.0, new - self._prog_display)
        # Boot/load are only ~3–7% of the bar; give those steps enough time
        # to read as motion. Dense STFT/analyze ticks use a shorter glide.
        if gap <= 25:
            self._prog_lerp_dur = max(0.12, min(0.28, 0.12 + gap / 1000.0 * 0.40))
        else:
            self._prog_lerp_dur = max(0.08, min(0.22, 0.08 + gap / 1000.0 * 0.18))
        if not self._prog_timer.isActive():
            self._prog_timer.start()

    def _tick_progress(self) -> None:
        """~60 Hz linear interpolation of the bar/taskbar toward the target."""
        elapsed = time.monotonic() - self._prog_lerp_t0
        dur = self._prog_lerp_dur if self._prog_lerp_dur > 0 else 0.05
        t = elapsed / dur
        if t >= 1.0:
            self._prog_display = self._prog_lerp_to
            t = 1.0
        else:
            # Linear: display = from + (to - from) * t
            self._prog_display = (
                self._prog_lerp_from
                + (self._prog_lerp_to - self._prog_lerp_from) * t
            )
        val = int(round(self._prog_display))
        val = 0 if val < 0 else (1000 if val > 1000 else val)
        if self.progressBar.value() != val:
            self.progressBar.setValue(val)
        self._taskbar.show_normal(val, 1000)
        if t >= 1.0 and not self._prog_active:
            self._prog_timer.stop()

    def _stop_progress(self, final: int | None = None) -> None:
        """Stop smoothing; optionally snap bar to *final* (0–1000)."""
        self._prog_active = False
        if final is not None:
            self._prog_lerp_from = float(final)
            self._prog_lerp_to = float(final)
            self._prog_target = float(final)
            self._prog_display = float(final)
            self.progressBar.setValue(final)
            if final > 0:
                self._taskbar.show_normal(final, 1000)
        self._prog_timer.stop()

    def _on_conversion_done(self, output: str) -> None:
        # Never wait() on the QThread here — under Nuitka attach + Explorer
        # launch, the native thread handle can already be invalid (WinError 6).
        worker = self._worker
        self._worker = None
        if worker is not None:
            for sig_name in ("progress", "succeeded", "failed"):
                try:
                    getattr(worker, sig_name).disconnect()
                except Exception:
                    pass
            try:
                worker.deleteLater()
            except Exception:
                pass
        try:
            self.convertButton.setEnabled(True)
            self.previewButton.setEnabled(True)
        except Exception:
            pass
        try:
            self._stop_progress(1000)
        except Exception:
            pass
        try:
            self._taskbar.hide()
        except Exception:
            pass
        try:
            self._show_success_dialog(Path(output))
        except Exception as exc:
            try:
                log_console(f"[convert] success UI error: {exc}")
            except Exception:
                pass
            try:
                QtWidgets.QMessageBox.information(
                    self, "转换完成", f"MIDI 已保存至：\n{output}",
                )
            except Exception:
                pass

    @staticmethod
    def _friendly_error(msg: str) -> str:
        """Map raw exception text to a short user-facing message."""
        lower = msg.lower()
        if "moov" in lower or "损坏或不完整" in msg:
            lines = [ln for ln in msg.splitlines() if ln.strip()]
            return "\n".join(lines[:3]) if lines else msg
        if "无法识别为有效音频" in msg or "invalid data found" in lower:
            lines = [ln for ln in msg.splitlines() if ln.strip()]
            return "\n".join(lines[:3]) if lines else "音频文件无效或已损坏"
        if "解码超时" in msg or "timeout" in lower:
            lines = [ln for ln in msg.splitlines() if ln.strip()]
            return "\n".join(lines[:3]) if lines else "解码超时，请换文件或格式"
        if "无法解码音频" in msg:
            return "无法解码该音频文件，请确认格式完整且可播放"
        if "句柄无效" in msg or "winerror 6" in lower or "error 6" in lower:
            return (
                "内部句柄错误（多见于无控制台启动时的日志绑定问题，"
                "请更新后重试；不影响已生成的文件时可直接打开输出）"
            )
        if "nobackenderror" in lower:
            return "不支持的文件格式，请检查文件是否损坏或格式是否正确"
        if "filenotfounderror" in lower or "找不到文件" in msg:
            return "找不到输入文件"
        if "permissionerror" in lower:
            return "没有读取或写入文件的权限"
        if "memoryerror" in lower:
            return "内存不足，请尝试降低采样率或 FFT 窗口大小"
        import re
        m = re.match(r"(\w+Error|\w+Exception)", msg)
        if m:
            return f"转换失败：{m.group(1)}"
        for ln in msg.splitlines():
            if ln.strip():
                return ln.strip()[:200]
        return "转换过程中出现未知错误"

    def _on_conversion_error(self, msg: str) -> None:
        worker = self._worker
        self._worker = None
        if worker is not None:
            for sig_name in ("progress", "succeeded", "failed"):
                try:
                    getattr(worker, sig_name).disconnect()
                except Exception:
                    pass
            try:
                worker.deleteLater()
            except Exception:
                pass
        try:
            self.convertButton.setEnabled(True)
        except Exception:
            pass
        try:
            self._stop_progress(0)
        except Exception:
            pass
        try:
            self._taskbar.show_error(0, 1000)
        except Exception:
            pass

        friendly = self._friendly_error(msg)
        try:
            box = QtWidgets.QMessageBox(self)
            box.setWindowTitle("转换失败")
            box.setText(friendly)
            box.setIcon(QtWidgets.QMessageBox.Icon.Warning)
            box.addButton("确定", QtWidgets.QMessageBox.ButtonRole.AcceptRole)
            btn_detail = box.addButton(
                "详细信息…", QtWidgets.QMessageBox.ButtonRole.HelpRole)
            box.exec()
            if box.clickedButton() is btn_detail:
                self._show_error_detail(msg)
        except Exception:
            pass

    @staticmethod
    def _show_error_detail(text: str) -> None:
        import tempfile
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False, encoding="utf-8",
        ) as f:
            f.write(text)
            tmp = f.name
        QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(tmp))

    def _show_success_dialog(self, path: Path) -> None:
        box = QtWidgets.QMessageBox(self)
        box.setWindowTitle("转换完成")
        box.setText(f"MIDI 已保存至：\n{path}")
        box.setIcon(QtWidgets.QMessageBox.Icon.Information)

        btn_ok = box.addButton("确定", QtWidgets.QMessageBox.ButtonRole.AcceptRole)
        btn_preview = box.addButton("钢琴卷帘预览", QtWidgets.QMessageBox.ButtonRole.ActionRole)
        btn_folder = box.addButton("打开文件夹", QtWidgets.QMessageBox.ButtonRole.ActionRole)
        btn_system = box.addButton("使用系统应用打开", QtWidgets.QMessageBox.ButtonRole.ActionRole)

        box.exec()

        clicked = box.clickedButton()
        if clicked is btn_folder:
            self._open_folder(path)
        elif clicked is btn_system:
            self._open_with_system(path)
        elif clicked is btn_preview:
            self._open_preview()

    # ── File openers ────────────────────────────────────────────────────
    @staticmethod
    def _open_folder(path: Path) -> None:
        QtGui.QDesktopServices.openUrl(
            QtCore.QUrl.fromLocalFile(str(path.parent))
        )

    @staticmethod
    def _open_with_system(path: Path) -> None:
        QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(path)))

    @staticmethod
    def _open_with_viewer(path: Path) -> None:
        PianoLoTayu._open_with_system(path)

    def _bind_defaults(self) -> None:
        # Version label
        self.versionLabel.setText(
            f'<span style="color: #999;">{VERSION}</span>'
        )
        self.versionLabel.setTextFormat(QtCore.Qt.TextFormat.RichText)

        for key, (widget_name, label_name) in _WIDGET_CONFIG_MAP.items():
            widget = getattr(self, widget_name, None)
            label = getattr(self, label_name, None) if label_name else None
            if widget is None:
                continue

            # Tooltip (hard-coded Chinese for now)
            tip = _TOOLTIPS.get(key, "")
            if label is not None:
                label.setToolTip(tip)
            widget.setToolTip(tip)

            # Default value
            val = DEFAULTS[key]
            if isinstance(val, bool):
                widget.setChecked(val)
            elif isinstance(val, float):
                widget.setValue(float(val))
            else:
                widget.setValue(int(val))

    def collect_config(self) -> dict:
        cfg = {}
        for key, (widget_name, _) in _WIDGET_CONFIG_MAP.items():
            widget = getattr(self, widget_name, None)
            if widget is None:
                continue
            if hasattr(widget, "isChecked"):
                val = widget.isChecked()
            else:
                val = widget.value()
            if isinstance(val, float) and key in (
                "sr", "n_fft", "hop_length", "max_notes", "min_duration",
            ):
                val = int(val)
            cfg[key] = val
        return cfg


def _ensure_system_qt_plugins() -> None:
    """When frozen, still look for *system* Qt plugins (styles, platformthemes).

    Nuitka ``--enable-plugin=pyside6`` only ships a small plugin set.  Without
    system paths the platform theme cannot load the user's Breeze/Oxygen/… style
    and the app sticks on Fusion.  Prepend distro plugin dirs when present.
    """
    import sys
    if sys.platform in ("win32", "darwin"):
        return
    candidates = [
        Path("/usr/lib/qt6/plugins"),
        Path("/usr/lib64/qt6/plugins"),
        Path("/usr/lib/x86_64-linux-gnu/qt6/plugins"),
        Path("/usr/lib/qt/plugins"),
        Path("/usr/lib64/qt/plugins"),
    ]
    existing = [
        str(p) for p in candidates
        if p.is_dir() and any(p.iterdir())
    ]
    if not existing:
        return
    cur = os.environ.get("QT_PLUGIN_PATH", "")
    parts = [p for p in cur.split(os.pathsep) if p]
    for d in reversed(existing):
        if d not in parts:
            parts.insert(0, d)
    os.environ["QT_PLUGIN_PATH"] = os.pathsep.join(parts)


def _linux_desktop_hints() -> tuple[str, str]:
    desktop = os.environ.get("XDG_CURRENT_DESKTOP", "").lower()
    session = os.environ.get("DESKTOP_SESSION", "").lower()
    return desktop, session


def _configure_linux_platform_theme() -> None:
    """Point Qt at the DE's platform theme so *its* style/settings apply.

    Does not pick Breeze/Oxygen itself — KDE/GNOME/qt6ct do that.
    """
    if os.environ.get("QT_QPA_PLATFORMTHEME"):
        return
    desktop, session = _linux_desktop_hints()
    blob = f"{desktop} {session}"
    if any(k in blob for k in ("kde", "plasma", "lxqt")):
        os.environ.setdefault("QT_QPA_PLATFORMTHEME", "kde")
    elif any(k in blob for k in ("gnome", "unity", "cinnamon", "mate", "xfce", "gtk")):
        os.environ.setdefault("QT_QPA_PLATFORMTHEME", "gtk3")
    elif Path.home().joinpath(".config/qt6ct/qt6ct.conf").is_file():
        os.environ.setdefault("QT_QPA_PLATFORMTHEME", "qt6ct")
    elif Path.home().joinpath(".config/qt5ct/qt5ct.conf").is_file():
        os.environ.setdefault("QT_QPA_PLATFORMTHEME", "qt5ct")


def _read_system_widget_style() -> str | None:
    """Best-effort: style name the desktop was configured to use (Linux).

    Used only when the platform theme failed to apply and we are stuck on a
    generic fallback (e.g. Fusion in a frozen build).  Never invents a
    preference order — returns whatever the user/system config says.
    """
    # Explicit user/env override (honour if set and not a bad pin we cleared)
    env = os.environ.get("QT_STYLE_OVERRIDE", "").strip()
    if env:
        return env

    # KDE Plasma: ~/.config/kdeglobals → [KDE] widgetStyle=…
    kdeglobals = Path.home() / ".config" / "kdeglobals"
    if kdeglobals.is_file():
        try:
            section = ""
            for raw in kdeglobals.read_text(encoding="utf-8", errors="replace").splitlines():
                line = raw.strip()
                if line.startswith("[") and line.endswith("]"):
                    section = line[1:-1]
                    continue
                if section == "KDE" and "=" in line:
                    key, _, val = line.partition("=")
                    if key.strip() == "widgetStyle":
                        name = val.strip()
                        if name:
                            return name
        except OSError:
            pass

    # qt6ct / qt5ct
    for conf_rel in ("qt6ct/qt6ct.conf", "qt5ct/qt5ct.conf"):
        conf = Path.home() / ".config" / conf_rel
        if not conf.is_file():
            continue
        try:
            section = ""
            for raw in conf.read_text(encoding="utf-8", errors="replace").splitlines():
                line = raw.strip()
                if line.startswith("[") and line.endswith("]"):
                    section = line[1:-1]
                    continue
                if section == "Appearance" and line.startswith("style="):
                    name = line.split("=", 1)[1].strip()
                    if name:
                        return name
        except OSError:
            pass
    return None


def _apply_platform_style(app: QtWidgets.QApplication) -> None:
    """Follow the system style; only patch bad frozen/classic fallbacks.

    Linux: do **not** force Breeze/Oxygen.  Platform theme + system config
    choose the style.  We only intervene if Qt landed on a useless fallback.

    Windows: prefer windows11/windowsvista over classic Win9x / bare Fusion.
    """
    import sys

    def _try_set(name: str) -> bool:
        if not name:
            return False
        # Case-insensitive match against factory keys
        keys = {str(n).lower(): str(n) for n in QtWidgets.QStyleFactory.keys()}
        actual = keys.get(name.lower(), name)
        style = QtWidgets.QStyleFactory.create(actual)
        if style is None and actual != name:
            style = QtWidgets.QStyleFactory.create(name)
        if style is None:
            return False
        app.setStyle(style)
        return True

    def _current_key() -> str:
        st = app.style()
        return ((st.objectName() if st is not None else "") or "").lower()

    if sys.platform not in ("win32", "darwin"):
        # Linux: make system style plugins visible to a frozen binary
        for d in (
            "/usr/lib/qt6/plugins",
            "/usr/lib64/qt6/plugins",
            "/usr/lib/x86_64-linux-gnu/qt6/plugins",
            "/usr/lib/qt/plugins",
        ):
            p = Path(d)
            if p.is_dir():
                app.addLibraryPath(str(p))

        cur = _current_key()
        # Platform theme already applied something real → leave it alone.
        # Fusion is only "bad" when the user actually configured another style.
        configured = _read_system_widget_style()
        if configured:
            want = configured.lower()
            if cur == want or cur.replace("-", "") == want.replace("-", ""):
                return
            # Stuck on fusion/windows while the desktop wants something else
            if cur in ("fusion", "windows", "windowsonly", ""):
                if _try_set(configured):
                    return
        # No config, or config style unavailable: keep whatever Qt chose
        # (including Fusion).  Do not rank Breeze over Oxygen ourselves.
        return

    if sys.platform == "darwin":
        if _current_key() not in ("macos", "macintosh"):
            if not _try_set("macos"):
                _try_set("Fusion")
        return

    # Windows: avoid classic 9x look, but match the OS generation.
    # Qt 6.7+ ships a "windows11" style plugin that works on Win10 too —
    # if we always try it first, late Win10 + new PySide looks like Win11.
    # Prefer windows11 only on real Windows 11 (build ≥ 22000).
    win_ver = sys.getwindowsversion()
    is_win11 = int(getattr(win_ver, "build", 0) or 0) >= 22000
    preferred = ("windows11", "windowsvista") if is_win11 else ("windowsvista",)
    for name in preferred:
        if _try_set(name):
            return
    if _current_key() in ("windows", "windowsonly", ""):
        _try_set("Fusion")


def main() -> int:
    import signal
    import sys

    # Windows: reconnect print() to the parent console when launched from
    # PowerShell/cmd with Nuitka --windows-console-mode=attach (otherwise
    # sys.stdout is often None and logs vanish).
    # Always sanitize broken OS STD_* handles afterwards — Explorer double-click
    # leaves INVALID_HANDLE_VALUE; children inherit it → WinError 6 句柄无效.
    try:
        from .win32_utils import attach_parent_console, sanitize_std_handles
        if attach_parent_console():
            log_console(f"PianoLoTayu GUI starting (pid={os.getpid()})")
        sanitize_std_handles()
    except Exception:
        try:
            from .win32_utils import sanitize_std_handles
            sanitize_std_handles()
        except Exception:
            pass

    # Windows: own taskbar identity (must be before QApplication)
    set_app_user_model_id("pianolotayu")
    # Windows: UIPI message filter so elevated process can receive Explorer drops
    prepare_elevated_drop_filters()

    # Register fluidsynth DLL dir *before* any import of pyfluidsynth.
    # Must run early so PATH / add_dll_directory are in place.
    try:
        from .win32_utils import setup_fluidsynth_dll
        setup_fluidsynth_dll()
    except Exception:
        pass

    if sys.platform not in ("win32", "darwin"):
        # Frozen Linux: load system styles/platformthemes; follow DE settings
        _ensure_system_qt_plugins()
        _configure_linux_platform_theme()

    app = QtWidgets.QApplication()
    app.setApplicationName("PianoLoTayu")
    app.setOrganizationName("PianoLoTayu")
    try:
        app.setDesktopFileName("pianolotayu")
    except Exception:
        pass

    # Linux: honour system style.  Windows: avoid classic chrome.
    _apply_platform_style(app)

    icon = app_icon()
    if not icon.isNull():
        app.setWindowIcon(icon)

    # ── Suppress Windows beep when clicking outside a modal dialog ────────
    app.installNativeEventFilter(BeepSuppressor())

    # ── Graceful shutdown on Ctrl+C ───────────────────────────────────────
    signal.signal(signal.SIGINT, lambda sig, frame: app.quit())
    # Timer required: Qt event loop blocks Python signal delivery;
    # a periodic no-op timeout unblocks it so SIGINT is processed.
    _sig_timer = QtCore.QTimer()
    _sig_timer.start(400)
    _sig_timer.timeout.connect(lambda: None)

    sys_font = QtGui.QFontDatabase.systemFont(
        QtGui.QFontDatabase.SystemFont.GeneralFont
    )
    app.setFont(sys_font)

    window = PianoLoTayu()
    if not icon.isNull():
        window.setWindowIcon(icon)
    window.show()
    app.exec()
    return 0


if __name__ == "__main__":
    main()

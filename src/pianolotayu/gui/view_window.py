"""Piano-roll preview window — frame, controls, and export dialogs."""

from __future__ import annotations

from pathlib import Path

from PySide6 import QtWidgets, QtGui, QtCore

from .piano_view import (
    MIN_PITCH, MAX_PITCH,
    KeyboardWidget, NoteGridView, MidiPlayer,
)
from .export import (
    VideoExportWorker, AudioExportWorker, filter_available_codecs,
)
from .win32_utils import TaskbarProgress

_SOUNDFONT_DIR = Path(__file__).resolve().parents[3] / "soundfonts"


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
        _CODECS = filter_available_codecs(_ALL_CODECS)
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
        if not self.isActiveWindow():
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

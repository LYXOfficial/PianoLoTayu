"""Piano-roll preview window — frame, controls, and export dialogs."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from PySide6 import QtWidgets, QtGui, QtCore

from .piano_view import (
    MIN_PITCH, MAX_PITCH,
    KeyboardWidget, NoteGridView, MidiPlayer,
    PlaybackOptions, default_track_enabled, list_track_infos,
)
from .win32_utils import TaskbarProgress, app_icon

if TYPE_CHECKING:
    from .export import VideoExportWorker, AudioExportWorker

_SOUNDFONT_DIR = Path(__file__).resolve().parents[3] / "soundfonts"


def _std_icon(name: str, fallback: QtWidgets.QStyle.StandardPixmap) -> QtGui.QIcon:
    """Theme icon with QStyle standard-pixmap fallback."""
    icon = QtGui.QIcon.fromTheme(name)
    if not icon.isNull():
        return icon
    style = QtWidgets.QApplication.style()
    if style is not None:
        return style.standardIcon(fallback)
    return QtGui.QIcon()


# ═══════════════════════════════════════════════════════════════════════════
# Clickable + draggable seek slider
# ═══════════════════════════════════════════════════════════════════════════

class _SeekSlider(QtWidgets.QSlider):
    """QSlider that jumps to the click position *and* keeps dragging.

    Plain ``QSlider`` only drags when the press lands on the handle; a click on
    the groove does a page-step.  A naïve ``setValue`` before ``super()`` also
    fails: the linear ``x / width`` mapping does not match the style's groove /
    handle geometry, so the handle never ends up under the cursor and drag mode
    never engages.

    We map pixels with the style, then drive press / move / release ourselves so
    drag always works.  ``sliderPressed`` / ``sliderMoved`` / ``sliderReleased``
    still fire via ``setSliderDown`` / ``setValue``.
    """

    def _value_at(self, pos: QtCore.QPoint) -> int:
        """Pixel position → slider value, matching the current style geometry."""
        opt = QtWidgets.QStyleOptionSlider()
        self.initStyleOption(opt)
        style = self.style()
        assert style is not None
        groove = style.subControlRect(
            QtWidgets.QStyle.ComplexControl.CC_Slider,
            opt,
            QtWidgets.QStyle.SubControl.SC_SliderGroove,
            self,
        )
        handle = style.subControlRect(
            QtWidgets.QStyle.ComplexControl.CC_Slider,
            opt,
            QtWidgets.QStyle.SubControl.SC_SliderHandle,
            self,
        )
        if self.orientation() == QtCore.Qt.Orientation.Horizontal:
            # Centre the handle on the cursor (same as Qt's absolute-set path)
            span = max(1, groove.width() - handle.width())
            x = pos.x() - handle.width() // 2 - groove.x()
            return QtWidgets.QStyle.sliderValueFromPosition(
                self.minimum(), self.maximum(), x, span, opt.upsideDown)
        span = max(1, groove.height() - handle.height())
        y = pos.y() - handle.height() // 2 - groove.y()
        return QtWidgets.QStyle.sliderValueFromPosition(
            self.minimum(), self.maximum(), y, span, opt.upsideDown)

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
        if event.button() == QtCore.Qt.MouseButton.LeftButton:
            # Enter drag mode first so setValue emits sliderMoved
            self.setSliderDown(True)
            self.setValue(self._value_at(event.position().toPoint()))
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QtGui.QMouseEvent) -> None:
        if (self.isSliderDown()
                and event.buttons() & QtCore.Qt.MouseButton.LeftButton):
            self.setValue(self._value_at(event.position().toPoint()))
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QtGui.QMouseEvent) -> None:
        if (event.button() == QtCore.Qt.MouseButton.LeftButton
                and self.isSliderDown()):
            self.setValue(self._value_at(event.position().toPoint()))
            self.setSliderDown(False)
            event.accept()
            return
        super().mouseReleaseEvent(event)


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
        icon = app_icon()
        if not icon.isNull():
            self.setWindowIcon(icon)

        self._player = MidiPlayer(self)
        self._playing = False
        self._seek_dragging = False
        self._mono_hex = "#de8400"
        self._fit_timer: QtCore.QTimer | None = None
        self._fullscreen = False
        self._taskbar = TaskbarProgress(self)
        self._video_worker = None
        self._audio_worker = None

        # System-tray icon only while a notification is showing (Windows needs
        # a tray icon for balloon toasts; hide it again afterwards).
        self._tray = QtWidgets.QSystemTrayIcon(self)
        if not icon.isNull():
            self._tray.setIcon(icon)
        else:
            self._tray.setIcon(self.style().standardIcon(
                QtWidgets.QStyle.StandardPixmap.SP_MediaPlay))
        self._tray.setToolTip("PianoLoTayu")
        self._tray_hide_timer = QtCore.QTimer(self)
        self._tray_hide_timer.setSingleShot(True)
        self._tray_hide_timer.timeout.connect(self._hide_tray)

        # ── Top controls ────────────────────────────────────────────────
        self._sf_combo = QtWidgets.QComboBox(self)
        self._sf_combo.addItem("（无 SoundFont — 静音预览）", "")
        for sf in sorted(_SOUNDFONT_DIR.glob("*.sf2")):
            self._sf_combo.addItem(sf.name, str(sf))
        if self._sf_combo.count() > 1:
            self._sf_combo.setCurrentIndex(1)
        self._sf_combo.currentIndexChanged.connect(self._on_sf_changed)

        self._btn_play = QtWidgets.QPushButton("播放")
        self._btn_play.setIcon(_std_icon(
            "media-playback-start",
            QtWidgets.QStyle.StandardPixmap.SP_MediaPlay))
        self._btn_export = QtWidgets.QPushButton("导出视频")
        self._btn_export.setIcon(_std_icon(
            "video-x-generic",
            QtWidgets.QStyle.StandardPixmap.SP_DialogSaveButton))
        self._btn_export_audio = QtWidgets.QPushButton("导出音频")
        self._btn_export_audio.setIcon(_std_icon(
            "audio-x-generic",
            QtWidgets.QStyle.StandardPixmap.SP_MediaVolume))
        self._btn_play_opts = QtWidgets.QPushButton("播放选项")
        self._btn_play_opts.setIcon(_std_icon(
            "preferences-system",
            QtWidgets.QStyle.StandardPixmap.SP_FileDialogDetailedView))
        self._btn_play_opts.setToolTip("预览 / 导出共用的音频播放选项")
        self._playback_opts = PlaybackOptions()

        self._mono_cb = QtWidgets.QCheckBox("单色")
        self._mono_color_btn = QtWidgets.QPushButton()
        self._mono_color_btn.setFixedSize(24, 24)
        self._mono_color_btn.setStyleSheet(
            "background: #de8400; border: 1px solid #999; border-radius: 3px;")
        self._mono_color_btn.clicked.connect(self._on_pick_mono_color)

        self._btn_fullscreen = QtWidgets.QPushButton("全屏")
        self._btn_fullscreen.setIcon(_std_icon(
            "view-fullscreen",
            QtWidgets.QStyle.StandardPixmap.SP_TitleBarMaxButton))
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
        top.addWidget(self._btn_play_opts)
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
        self._grid.zoom_delta.connect(self._on_zoom_wheel)

        # ── Bottom seek bar ─────────────────────────────────────────────
        self._seek_bar = _SeekSlider(QtCore.Qt.Orientation.Horizontal, self)
        self._seek_bar.setRange(0, 1000)
        # Press → drag flag; move → scrub view; release → seek audio.
        # (_SeekSlider drives these via setSliderDown / setValue so groove
        # clicks also drag, not just handle grabs.)
        self._seek_bar.sliderPressed.connect(
            lambda: setattr(self, '_seek_dragging', True))
        self._seek_bar.sliderReleased.connect(self._on_seek_release)
        self._seek_bar.sliderMoved.connect(self._on_seek_drag)
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
        self._btn_play_opts.clicked.connect(self._on_play_options)
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
        if event.type() == QtCore.QEvent.Type.Wheel:
            # Ctrl+wheel over keyboard also zooms (grid handles its own viewport)
            if obj is self._keyboard:
                we = event  # type: QtGui.QWheelEvent
                if we.modifiers() & QtCore.Qt.KeyboardModifier.ControlModifier:
                    dy = we.angleDelta().y() or we.pixelDelta().y()
                    if dy != 0:
                        self._on_zoom_wheel(1 if dy > 0 else -1)
                    return True
        return False

    def _on_zoom_wheel(self, step: int) -> None:
        """Ctrl+wheel zoom — same range/axis as the top-right 缩放 slider."""
        cur = self._h_zoom.value()
        # ~10% per notch, minimum step 10; clamp to slider range
        delta = max(10, int(round(cur * 0.1)))
        new = cur + int(step) * delta
        lo, hi = self._h_zoom.minimum(), self._h_zoom.maximum()
        self._h_zoom.setValue(max(lo, min(hi, new)))

    def _on_sf_changed(self, _index: int) -> None:
        """Hot-swap / unload SoundFont (also works mid-playback)."""
        sf = self._sf_combo.currentData() or ""
        if not self._player.set_soundfont(sf):
            if sf:
                QtWidgets.QMessageBox.warning(
                    self, "提示", f"无法加载 SoundFont：{sf}")

    def _on_fullscreen(self) -> None:
        """Toggle fullscreen: hide controls, show only the piano roll."""
        self._fullscreen = not self._fullscreen
        self._top_bar.setVisible(not self._fullscreen)
        self._bottom_bar.setVisible(not self._fullscreen)
        if self._fullscreen:
            self.showFullScreen()
        else:
            self.showNormal()

    def _set_play_button(self, playing: bool) -> None:
        if playing:
            self._btn_play.setText("暂停")
            self._btn_play.setIcon(_std_icon(
                "media-playback-pause",
                QtWidgets.QStyle.StandardPixmap.SP_MediaPause))
        else:
            self._btn_play.setText("播放")
            self._btn_play.setIcon(_std_icon(
                "media-playback-start",
                QtWidgets.QStyle.StandardPixmap.SP_MediaPlay))

    def _on_play_pause(self) -> None:
        if self._playing:
            self._player.pause()
            self._playing = False
            self._set_play_button(False)
        else:
            if self._midi_path:
                self._player.load_midi(self._midi_path)
                # Ensure mute map exists for this MIDI
                if self._playback_opts.track_enabled is None:
                    self._playback_opts.track_enabled = default_track_enabled(
                        self._player._midi)
            if self._player._midi is None:
                QtWidgets.QMessageBox.warning(self, "提示", "没有加载 MIDI 文件")
                return
            self._player.set_playback_options(self._playback_opts)
            sf = self._sf_combo.currentData() or ""
            if sf:
                if not self._player.set_soundfont(sf):
                    QtWidgets.QMessageBox.warning(
                        self, "提示", f"无法加载 SoundFont：{sf}")
                    return
            else:
                # Explicit silent mode — drop any previously loaded synth
                self._player.unload_soundfont()
            dur = self._grid.scene_duration_s()
            t = self._seek_bar.value() / 1000.0 * dur if dur > 0 else 0.0
            self._player.play(sf_path=sf, start_t=t)
            self._playing = True
            self._set_play_button(True)

    def _on_play_options(self) -> None:
        """Modal dialog: shared play/export audio options + per-track mute."""
        # Need MIDI for track list — load if necessary (does not start audio)
        if self._player._midi is None and self._midi_path:
            self._player.load_midi(self._midi_path)
        midi = self._player._midi
        if midi is None:
            QtWidgets.QMessageBox.warning(self, "提示", "没有加载 MIDI 文件")
            return

        if self._playback_opts.track_enabled is None:
            self._playback_opts.track_enabled = default_track_enabled(midi)
        else:
            # Merge defaults for any new track indices
            for i, en in default_track_enabled(midi).items():
                self._playback_opts.track_enabled.setdefault(i, en)

        dlg = QtWidgets.QDialog(self)
        dlg.setWindowTitle("音频选项")
        dlg.setModal(True)
        dlg.setWindowFlag(QtCore.Qt.WindowType.WindowMaximizeButtonHint, False)
        dlg.setWindowFlag(QtCore.Qt.WindowType.WindowMinimizeButtonHint, False)
        dlg.setSizeGripEnabled(False)

        form = QtWidgets.QVBoxLayout(dlg)
        form.setSpacing(10)

        tip = QtWidgets.QLabel(
            "轨道开关会同步到预览卷帘与导出视频的音符显隐，"
            "以及预览 / 导出音频的播放；"
            "若使用全钢琴音色，不建议开启鼓组的音频渲染",
            dlg,
        )
        tip.setWordWrap(True)
        tip.setStyleSheet("color: #666;")
        form.addWidget(tip)

        # Count notes outside piano range (A0–C8) for the checkbox label
        n_oor = 0
        for inst in midi.instruments:
            for note in inst.notes:
                if note.pitch < MIN_PITCH or note.pitch > MAX_PITCH:
                    n_oor += 1

        cb_oor = QtWidgets.QCheckBox(
            f"不忽略钢琴音域外音符（仅播放，不会渲染到视图）（{n_oor} 个）",
            dlg,
        )
        cb_oor.setChecked(self._playback_opts.play_out_of_range)
        form.addWidget(cb_oor)

        cb_prog = QtWidgets.QCheckBox(
            "使用对应音色匹配轨道，而非钢琴", dlg)
        cb_prog.setChecked(self._playback_opts.use_track_programs)
        form.addWidget(cb_prog)

        form.addWidget(QtWidgets.QLabel("轨道：", dlg))

        tracks = list_track_infos(midi)
        n_tracks = len(tracks)

        scroll = QtWidgets.QScrollArea(dlg)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(
            QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setFrameShape(QtWidgets.QFrame.Shape.StyledPanel)
        # Reserve vertical-scrollbar gutter so "音符数" is never covered
        sb_w = dlg.style().pixelMetric(
            QtWidgets.QStyle.PixelMetric.PM_ScrollBarExtent)
        if sb_w <= 0:
            sb_w = 14

        body = QtWidgets.QWidget()
        grid = QtWidgets.QGridLayout(body)
        # Extra right margin = scrollbar width (always reserved)
        grid.setContentsMargins(4, 4, 4 + sb_w, 4)
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(6)
        grid.setRowStretch(0, 0)

        # Header: select-all checkbox | 轨道名 | 音色 | 音符数
        cb_all = QtWidgets.QCheckBox(body)
        cb_all.setToolTip("全选 / 全不选")
        grid.addWidget(cb_all, 0, 0)
        for col, text in enumerate(("轨道名", "音色", "音符数"), start=1):
            h = QtWidgets.QLabel(text, body)
            f = h.font()
            f.setBold(True)
            h.setFont(f)
            if col == 3:
                h.setMinimumWidth(48)
                h.setAlignment(
                    QtCore.Qt.AlignmentFlag.AlignRight
                    | QtCore.Qt.AlignmentFlag.AlignVCenter)
            grid.addWidget(h, 0, col)

        track_cbs: dict[int, QtWidgets.QCheckBox] = {}
        for row, info in enumerate(tracks, start=1):
            idx = info["index"]
            cb = QtWidgets.QCheckBox(body)
            cb.setChecked(self._playback_opts.is_track_enabled(
                idx, info["is_drum"]))
            if info["is_drum"]:
                cb.setToolTip("鼓组 — 默认关闭（开启后卷帘/导出视频也会显示）")
            track_cbs[idx] = cb
            grid.addWidget(cb, row, 0)
            grid.addWidget(QtWidgets.QLabel(info["name"], body), row, 1)
            grid.addWidget(QtWidgets.QLabel(info["program"], body), row, 2)
            nlab = QtWidgets.QLabel(str(info["n_notes"]), body)
            nlab.setMinimumWidth(48)
            nlab.setAlignment(
                QtCore.Qt.AlignmentFlag.AlignRight
                | QtCore.Qt.AlignmentFlag.AlignVCenter)
            grid.addWidget(nlab, row, 3)
            grid.setRowStretch(row, 0)

        grid.setRowStretch(n_tracks + 1, 1)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(2, 1)
        grid.setColumnMinimumWidth(3, 48)
        scroll.setWidget(body)

        def _sync_all_cb() -> None:
            if not track_cbs:
                cb_all.blockSignals(True)
                cb_all.setCheckState(QtCore.Qt.CheckState.Unchecked)
                cb_all.blockSignals(False)
                return
            n_on = sum(1 for c in track_cbs.values() if c.isChecked())
            cb_all.blockSignals(True)
            if n_on == 0:
                cb_all.setCheckState(QtCore.Qt.CheckState.Unchecked)
            elif n_on == len(track_cbs):
                cb_all.setCheckState(QtCore.Qt.CheckState.Checked)
            else:
                cb_all.setCheckState(QtCore.Qt.CheckState.PartiallyChecked)
            cb_all.blockSignals(False)

        def _on_all_clicked() -> None:
            # Toggle: if all on → all off; otherwise select all
            all_on = (
                bool(track_cbs)
                and all(c.isChecked() for c in track_cbs.values())
            )
            target = not all_on
            for c in track_cbs.values():
                c.blockSignals(True)
                c.setChecked(target)
                c.blockSignals(False)
            _sync_all_cb()

        cb_all.setTristate(True)
        cb_all.clicked.connect(_on_all_clicked)
        for c in track_cbs.values():
            c.toggled.connect(lambda _=False: _sync_all_cb())
        _sync_all_cb()

        # Height ≈ header + rows; cap so many tracks scroll instead of growing
        row_h = 28
        content_h = 8 + row_h * (1 + max(n_tracks, 1)) + 8
        scroll.setFixedHeight(min(max(content_h, 56), 280))
        form.addWidget(scroll, 0)

        # 确定 left / 取消 right (Windows-style primary then dismiss)
        btn_row = QtWidgets.QHBoxLayout()
        btn_row.addStretch()
        btn_ok = QtWidgets.QPushButton("确定", dlg)
        btn_cancel = QtWidgets.QPushButton("取消", dlg)
        btn_cancel.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)
        btn_ok.setDefault(True)
        btn_ok.clicked.connect(dlg.accept)
        btn_cancel.clicked.connect(dlg.reject)
        btn_row.addWidget(btn_ok)
        btn_row.addWidget(btn_cancel)
        form.addLayout(btn_row)

        dlg.layout().setSizeConstraint(
            QtWidgets.QLayout.SizeConstraint.SetFixedSize)

        if dlg.exec() != QtWidgets.QDialog.DialogCode.Accepted:
            return

        self._playback_opts = PlaybackOptions(
            play_out_of_range=cb_oor.isChecked(),
            use_track_programs=cb_prog.isChecked(),
            track_enabled={i: cb.isChecked() for i, cb in track_cbs.items()},
        )
        # Sync roll visibility (including drums when enabled)
        self._grid.set_track_enabled(self._playback_opts.track_enabled)
        # Apply immediately (affects next play tick / re-maps programs)
        was_playing = self._playing
        if was_playing:
            # Keep transport running but re-apply programs / mute map
            t = 0.0
            dur = self._grid.scene_duration_s()
            if dur > 0:
                t = self._seek_bar.value() / 1000.0 * dur
            self._player.set_playback_options(self._playback_opts)
            self._player.seek(t)
        else:
            self._player.set_playback_options(self._playback_opts)

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        self._player.cleanup()
        self._stop_worker("_video_worker")
        self._stop_worker("_audio_worker")
        if self._tray is not None:
            self._tray_hide_timer.stop()
            self._tray.hide()
        super().closeEvent(event)

    def _hide_tray(self) -> None:
        if self._tray is not None:
            self._tray.hide()

    def _notify(self, title: str, body: str, msec: int = 5000) -> None:
        """Show a system notification; tray icon only visible for the toast."""
        if self._tray is None:
            return
        if not QtWidgets.QSystemTrayIcon.isSystemTrayAvailable():
            return
        self._tray_hide_timer.stop()
        self._tray.show()
        self._tray.showMessage(
            title, body,
            QtWidgets.QSystemTrayIcon.MessageIcon.Information, msec,
        )
        # Keep tray briefly after the toast so the OS can finish showing it
        self._tray_hide_timer.start(msec + 800)

    def _stop_worker(self, attr: str, timeout_ms: int = 8000) -> None:
        """Interrupt a QThread attribute and drop the reference only after it stops.

        Avoids ``QThread: Destroyed while thread is still running`` when cancel
        races with a long render frame / ffmpeg write.
        """
        worker = getattr(self, attr, None)
        if worker is None:
            return
        try:
            # Prevent finished/error from re-entering dialog handlers mid-cancel
            try:
                worker.progress.disconnect()
            except (RuntimeError, TypeError):
                pass
            try:
                worker.finished.disconnect()
            except (RuntimeError, TypeError):
                pass
            try:
                worker.error.disconnect()
            except (RuntimeError, TypeError):
                pass

            if worker.isRunning():
                worker.requestInterruption()
                if not worker.wait(timeout_ms):
                    # Last resort — better than destroying a live QThread
                    worker.terminate()
                    worker.wait(2000)
        except RuntimeError:
            pass  # C++ object already deleted
        finally:
            setattr(self, attr, None)

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
        self._set_play_button(False)

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
        # While the user is scrubbing the seek bar, leave the slider/label alone
        # so playback ticks don't fight the drag.
        if self._seek_dragging:
            return
        dur = self._grid.scene_duration_s()
        self._time_label.setText(f"{self._fmt_time(t)} / {self._fmt_time(dur)}")
        if dur > 0:
            self._seek_bar.setValue(int(t / dur * 1000))
        # Auto-scroll: playhead at bottom (vertical) / left edge (horizontal)
        if self._playing:
            self._grid.scroll_to_time(t)

    def _on_seek_drag(self, val: int) -> None:
        """Scrub the roll view while the seek handle is held (audio commits on release)."""
        dur = self._grid.scene_duration_s()
        if dur > 0:
            t = val / 1000.0 * dur
            self._grid.scroll_to_time(t)
            self._time_label.setText(f"{self._fmt_time(t)} / {self._fmt_time(dur)}")

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
        from .export import build_video_codec_list
        codec_row = QtWidgets.QHBoxLayout()
        codec_combo = QtWidgets.QComboBox(dlg)
        # (label, codec, container_ext) — AV1 resolves to libaom/SVT/rav1e
        _CODECS = build_video_codec_list()
        if not _CODECS:
            _CODECS = [("H.264 (.mp4)", "libx264", ".mp4")]
        for label, *_ in _CODECS:
            codec_combo.addItem(label)
        codec_combo.setCurrentIndex(0)
        codec_row.addWidget(QtWidgets.QLabel("编码:"))
        codec_row.addWidget(codec_combo, 1)

        # ── Path ↔ codec extension sync ──────────────────────────────────
        _V_FILTERS = "MP4 (*.mp4);;MKV (*.mkv);;WebM (*.webm);;所有文件 (*)"
        _syncing = {"on": False}  # re-entrancy guard

        def _path_with_ext(path: str, ext: str) -> str:
            p = Path(path)
            if not ext.startswith("."):
                ext = f".{ext}"
            stem = p.stem if p.suffix else p.name
            if not stem:
                stem = "output"
            return str(p.with_name(stem + ext))

        def _ext_of(path: str) -> str:
            return Path(path).suffix.lower()

        def _set_path_for_codec(idx: int) -> None:
            if idx < 0 or idx >= len(_CODECS):
                return
            ext = _CODECS[idx][2]
            cur = path_edit.text().strip()
            if not cur:
                m = Path(self._midi_path) if self._midi_path else Path("output")
                cur = str(m.parent / f"{m.stem}_video{ext}")
            else:
                cur = _path_with_ext(cur, ext)
            if path_edit.text() != cur:
                path_edit.setText(cur)
            _refill_audio_codecs(cur)

        def _set_codec_for_path(path: str) -> None:
            ext = _ext_of(path)
            if not ext:
                return
            cur_i = codec_combo.currentIndex()
            if 0 <= cur_i < len(_CODECS) and _CODECS[cur_i][2] == ext:
                _refill_audio_codecs(path)
                return
            for i, (_label, _enc, cext) in enumerate(_CODECS):
                if cext == ext:
                    codec_combo.setCurrentIndex(i)
                    break
            _refill_audio_codecs(path)

        def browse() -> None:
            cur_i = codec_combo.currentIndex()
            selected_filter = _V_FILTERS
            if 0 <= cur_i < len(_CODECS):
                parts = _V_FILTERS.split(";;")
                needle = f"*.{_CODECS[cur_i][2].lstrip('.')}"
                preferred = next(
                    (p for p in parts if needle in p.lower()), None,
                )
                if preferred is not None:
                    selected_filter = ";;".join(
                        [preferred] + [p for p in parts if p != preferred]
                    )
            p, _ = QtWidgets.QFileDialog.getSaveFileName(
                dlg, "导出视频", path_edit.text(), selected_filter)
            if p:
                if not Path(p).suffix and 0 <= cur_i < len(_CODECS):
                    p = _path_with_ext(p, _CODECS[cur_i][2])
                _syncing["on"] = True
                try:
                    path_edit.setText(p)
                    _set_codec_for_path(p)
                finally:
                    _syncing["on"] = False

        btn_browse.clicked.connect(browse)

        def _on_codec_changed(idx: int) -> None:
            if _syncing["on"]:
                return
            _syncing["on"] = True
            try:
                _set_path_for_codec(idx)
            finally:
                _syncing["on"] = False

        codec_combo.currentIndexChanged.connect(_on_codec_changed)
        # Path/codec default alignment runs after audio-codec UI is created

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
        a_codec_combo = QtWidgets.QComboBox(dlg)
        a_br_combo = QtWidgets.QComboBox(dlg)
        a_br_combo.setEditable(True)
        a_br_combo.addItems(["96", "128", "192", "256", "320"])
        a_br_combo.setCurrentIndex(2)
        a_br_row.addWidget(QtWidgets.QLabel("音频编码:"))
        a_br_row.addWidget(a_codec_combo, 1)
        a_br_row.addWidget(QtWidgets.QLabel("码率 (kbps):"))
        a_br_row.addWidget(a_br_combo, 1)

        def _refill_audio_codecs(path: str, keep_id: str | None = None) -> None:
            """Rebuild audio-codec list for the container of *path*."""
            from .export import audio_codecs_for_path
            opts = audio_codecs_for_path(path)
            prev = keep_id
            if prev is None and a_codec_combo.count():
                prev = a_codec_combo.currentData()
            a_codec_combo.blockSignals(True)
            a_codec_combo.clear()
            for label, cid in opts:
                a_codec_combo.addItem(label, cid)
            # restore previous selection if still valid
            idx = a_codec_combo.findData(prev) if prev else -1
            a_codec_combo.setCurrentIndex(idx if idx >= 0 else 0)
            a_codec_combo.blockSignals(False)
            # FLAC ignores bitrate
            is_flac = (a_codec_combo.currentData() or "") == "flac"
            a_br_combo.setEnabled(not is_flac and not mute_cb.isChecked())

        def _on_a_codec_changed(_idx: int = 0) -> None:
            is_flac = (a_codec_combo.currentData() or "") == "flac"
            a_br_combo.setEnabled(not is_flac and not mute_cb.isChecked())

        a_codec_combo.currentIndexChanged.connect(_on_a_codec_changed)
        # Align default path with codec, then fill audio options for that container
        _set_path_for_codec(codec_combo.currentIndex())

        def _on_mute_toggled(checked: bool) -> None:
            a_codec_combo.setEnabled(not checked)
            is_flac = (a_codec_combo.currentData() or "") == "flac"
            a_br_combo.setEnabled(not checked and not is_flac)

        mute_cb.toggled.connect(_on_mute_toggled)

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
        btn_row.addWidget(btn_do)
        btn_row.addWidget(btn_cancel)

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
            a_codec_combo, a_br_combo, v_br_combo, btn_do,
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
            a_codec_id = a_codec_combo.currentData() or "aac"
            for w in _export_widgets:
                w.setEnabled(False)
            btn_cancel.setEnabled(True)
            pbar.setVisible(True)
            pbar.setValue(0)
            status_label.setVisible(True)

            from .export import VideoExportWorker
            self._video_worker = VideoExportWorker(
                self._midi_path, sf if not mute_cb.isChecked() else "", out,
                fps=fps, width=res_w, height=res_h,
                v_codec=_codec, v_bitrate=v_br_combo.currentText().strip(),
                a_codec=a_codec_id,
                a_bitrate=a_br_combo.currentText().strip() + "k",
                muted=mute_cb.isChecked(),
                vertical=vertical_cb.isChecked(),
                mono_color=(_export_mono_hex
                            if mono_cb.isChecked() else ""),
                playback=self._playback_opts,
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
            self._stop_worker("_video_worker")
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
        self._stop_worker("_video_worker")
        dlg.accept()
        if not self.isActiveWindow():
            QtWidgets.QApplication.alert(self, 0)
        self._notify("导出完成", f"视频已保存至：\n{output}")
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
        self._stop_worker("_video_worker")
        self._taskbar.hide()
        # Disconnect rejected→cancel first to avoid double-stop / re-enable race
        try:
            dlg.rejected.disconnect()
        except (RuntimeError, TypeError):
            pass
        dlg.reject()
        QtWidgets.QMessageBox.critical(self, "导出失败", f"视频导出失败：\n{msg}")

    # ── Audio export ─────────────────────────────────────────────────────
    def _on_export_audio(self) -> None:
        """Open a dialog to configure audio export, then start rendering."""
        dlg = QtWidgets.QDialog(self)
        dlg.setWindowTitle("导出音频")
        dlg.setMinimumWidth(300)
        # ── Output path ──────────────────────────────────────────────────
        path_row = QtWidgets.QHBoxLayout()
        path_edit = QtWidgets.QLineEdit(dlg)
        path_edit.setReadOnly(True)
        path_edit.setPlaceholderText("选择输出位置…")
        btn_browse = QtWidgets.QPushButton("浏览…", dlg)
        btn_browse.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)
        path_row.addWidget(path_edit)
        path_row.addWidget(btn_browse)

        # (label, codec_id, extension) — grouped by container, preferred codec first
        _ALL_FORMATS: list[tuple[str, str, str]] = [
            ("MP3 (.mp3)",        "mp3",    ".mp3"),
            ("AAC ADTS (.aac)",   "aac",    ".aac"),
            ("AAC (.m4a)",        "aac",    ".m4a"),
            ("Opus (.m4a)",       "opus",   ".m4a"),
            ("Vorbis (.ogg)",     "vorbis", ".ogg"),
            ("Opus (.ogg)",       "opus",   ".ogg"),
            ("FLAC (.flac)",      "flac",   ".flac"),
            ("WAV (.wav)",        "pcm",    ".wav"),
        ]
        # Keep only formats whose encoder exists
        from .export import standalone_audio_codecs_for_path
        _FORMATS = [
            (lab, cid, ext) for lab, cid, ext in _ALL_FORMATS
            if any(c == cid for _, c in standalone_audio_codecs_for_path(f"x{ext}"))
            or cid == "pcm"
        ]
        if not _FORMATS:
            _FORMATS = [("MP3 (.mp3)", "mp3", ".mp3")]

        _FILTERS = (
            "MP3 (*.mp3);;AAC (*.aac);;M4A (*.m4a);;OGG (*.ogg);;"
            "FLAC (*.flac);;WAV (*.wav);;所有文件 (*)"
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

        # ── Codec + bitrate ──────────────────────────────────────────────
        codec_br_row = QtWidgets.QHBoxLayout()
        a_codec_combo = QtWidgets.QComboBox(dlg)
        for lab, cid, ext in _FORMATS:
            # userData: (codec_id, extension)
            a_codec_combo.addItem(lab, (cid, ext))
        br_combo = QtWidgets.QComboBox(dlg)
        br_combo.setEditable(True)
        br_combo.addItems(["96", "128", "192", "256", "320"])
        br_combo.setCurrentIndex(2)
        codec_br_row.addWidget(QtWidgets.QLabel("编码:"))
        codec_br_row.addWidget(a_codec_combo, 1)
        codec_br_row.addWidget(QtWidgets.QLabel("码率 (kbps):"))
        codec_br_row.addWidget(br_combo, 1)

        _sync = {"on": False}

        def _path_with_ext(path: str, ext: str) -> str:
            p = Path(path)
            if not ext.startswith("."):
                ext = f".{ext}"
            stem = p.stem if p.suffix else p.name
            if not stem:
                stem = "output"
            return str(p.with_name(stem + ext))

        def _current_format() -> tuple[str, str]:
            data = a_codec_combo.currentData()
            if isinstance(data, tuple) and len(data) == 2:
                return str(data[0]), str(data[1])
            return "mp3", ".mp3"

        def _update_br_enabled() -> None:
            cid, _ext = _current_format()
            br_combo.setEnabled(cid not in ("flac", "pcm"))

        def _set_path_for_format() -> None:
            """Always rewrite path suffix to match the selected format."""
            _cid, ext = _current_format()
            cur = path_edit.text().strip()
            if not cur:
                midi = Path(self._midi_path) if self._midi_path else Path("output")
                cur = str(midi.parent / f"{midi.stem}_midi{ext}")
            else:
                cur = _path_with_ext(cur, ext)
            if path_edit.text() != cur:
                path_edit.setText(cur)
            _update_br_enabled()

        def _set_format_for_path(path: str) -> None:
            """Pick the format entry matching path's extension (keep codec if possible)."""
            ext = Path(path).suffix.lower()
            if not ext:
                return
            cur_i = a_codec_combo.currentIndex()
            cur_data = a_codec_combo.itemData(cur_i)
            # Prefer keeping same codec_id if that ext supports it
            if isinstance(cur_data, tuple) and cur_data[1] == ext:
                return
            prefer_cid = cur_data[0] if isinstance(cur_data, tuple) else None
            best = -1
            for i in range(a_codec_combo.count()):
                d = a_codec_combo.itemData(i)
                if not isinstance(d, tuple):
                    continue
                if d[1] != ext:
                    continue
                if prefer_cid and d[0] == prefer_cid:
                    best = i
                    break
                if best < 0:
                    best = i
            if best >= 0 and best != a_codec_combo.currentIndex():
                a_codec_combo.setCurrentIndex(best)
            _update_br_enabled()

        def _on_codec_changed(_idx: int = 0) -> None:
            if _sync["on"]:
                return
            _sync["on"] = True
            try:
                _set_path_for_format()
            finally:
                _sync["on"] = False

        a_codec_combo.currentIndexChanged.connect(_on_codec_changed)

        # ── Buttons ──────────────────────────────────────────────────────
        btn_row = QtWidgets.QHBoxLayout()
        btn_row.addStretch()
        btn_export_dlg = QtWidgets.QPushButton("导出", dlg)
        btn_cancel = QtWidgets.QPushButton("取消", dlg)
        btn_cancel.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)
        btn_row.addWidget(btn_export_dlg)
        btn_row.addWidget(btn_cancel)

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
        form.addLayout(codec_br_row)
        form.addWidget(status_label)
        form.addWidget(pbar)
        form.addLayout(btn_row)

        # ── Browse ───────────────────────────────────────────────────────
        def browse() -> None:
            midi = Path(self._midi_path) if self._midi_path else Path("output")
            default = path_edit.text().strip() or str(
                midi.parent / f"{midi.stem}_midi.mp3")
            # Prefer filter matching current format extension
            selected = _FILTERS
            _cid, cur_ext = _current_format()
            parts = _FILTERS.split(";;")
            needle = f"*{cur_ext}"
            preferred = next(
                (p for p in parts if needle in p.lower()), None,
            )
            if preferred is not None:
                selected = ";;".join(
                    [preferred] + [p for p in parts if p != preferred]
                )
            p, _ = QtWidgets.QFileDialog.getSaveFileName(
                dlg, "导出音频", default, selected)
            if p:
                if not Path(p).suffix:
                    p = _path_with_ext(p, cur_ext)
                _sync["on"] = True
                try:
                    path_edit.setText(p)
                    _set_format_for_path(p)
                finally:
                    _sync["on"] = False

        btn_browse.clicked.connect(browse)

        # Default path + align with default format (index 0 = MP3)
        if self._midi_path and not path_edit.text():
            midi = Path(self._midi_path)
            path_edit.setText(str(midi.parent / f"{midi.stem}_midi.mp3"))
        _sync["on"] = True
        try:
            # If path already has an ext, select matching format first
            if path_edit.text().strip():
                _set_format_for_path(path_edit.text().strip())
            _set_path_for_format()
        finally:
            _sync["on"] = False

        # ── Export ───────────────────────────────────────────────────────
        _audio_export_widgets = [
            path_edit, btn_browse, sf_combo, a_codec_combo, br_combo,
            btn_export_dlg,
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
            a_codec_id, fmt_ext = _current_format()
            # Force path extension to match selected format
            if Path(out).suffix.lower() != fmt_ext:
                out = _path_with_ext(out, fmt_ext)
                path_edit.setText(out)

            for w in _audio_export_widgets:
                w.setEnabled(False)
            btn_cancel.setEnabled(True)
            status_label.setVisible(True)
            status_label.setText("正在导出…")
            pbar.setVisible(True)
            pbar.setValue(0)
            self._taskbar.show_indeterminate()

            from .export import AudioExportWorker
            self._audio_worker = AudioExportWorker(
                self._midi_path, sf, out,
                bitrate=br_combo.currentText().strip() + "k",
                a_codec=a_codec_id,
                playback=self._playback_opts,
            )
            def _on_audio_prog(msg: str, pct: int) -> None:
                status_label.setText(msg)
                status_label.setVisible(True)
                if pct >= 0:
                    pbar.setVisible(True)
                    pbar.setValue(pct)
                    self._taskbar.show_normal(pct)
            self._audio_worker.progress.connect(_on_audio_prog)
            self._audio_worker.finished.connect(
                lambda p: self._on_audio_export_done(dlg, p))
            self._audio_worker.error.connect(
                lambda e: self._on_audio_export_error(dlg, e))
            self._audio_worker.start()

        btn_export_dlg.clicked.connect(do_export)
        btn_cancel.setEnabled(False)

        def _cancel_audio_export() -> None:
            self._stop_worker("_audio_worker")
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
        self._stop_worker("_audio_worker")
        self._taskbar.hide()
        dlg.accept()
        QtWidgets.QApplication.alert(self, 0)
        self._notify("导出完成", f"音频已保存至：\n{output}")
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
        self._stop_worker("_audio_worker")
        self._taskbar.hide()
        try:
            dlg.rejected.disconnect()
        except (RuntimeError, TypeError):
            pass
        dlg.reject()
        box = QtWidgets.QMessageBox(self)
        box.setWindowTitle("导出失败")
        box.setText(f"音频导出失败：\n{msg}")
        box.setIcon(QtWidgets.QMessageBox.Icon.Critical)
        box.addButton("确定", QtWidgets.QMessageBox.ButtonRole.AcceptRole)
        box.exec()

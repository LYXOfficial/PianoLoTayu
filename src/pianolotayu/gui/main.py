"""PianoLoTayu GUI — PySide6-based graphical interface."""

from __future__ import annotations

import os
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
from .win32_utils import TaskbarProgress, BeepSuppressor
from ..config import DEFAULTS, VERSION
from ..convert.audio import load_audio, compute_stft
from ..convert.analysis import analyze_frames
from ..convert.midi_writer import create_midi, save_midi
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
    "支持格式：wav, flac, mp3, ogg, m4a,</span><br/>"
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
            layout.insertWidget(idx, new)
    old.hide()
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

    progress = QtCore.Signal(int)        # 0–100
    finished = QtCore.Signal(str)        # output path
    error = QtCore.Signal(str)           # error message

    def __init__(self, input_path: str, output_path: str, config: dict,
                 parent=None):
        super().__init__(parent)
        self._input = input_path
        self._output = output_path
        self._cfg = config

    def run(self) -> None:
        try:
            self.progress.emit(5)
            if self.isInterruptionRequested():
                return
            signal, sr = load_audio(self._input, sr=self._cfg["sr"])
            self.progress.emit(15)
            if self.isInterruptionRequested():
                return

            D_db, freqs, times = compute_stft(
                signal, sr,
                n_fft=self._cfg["n_fft"],
                hop_length=self._cfg["hop_length"],
            )
            self.progress.emit(30)
            if self.isInterruptionRequested():
                return

            frame_notes = analyze_frames(
                D_db, freqs, times, sr, self._cfg["hop_length"],
                threshold_db=self._cfg["threshold"],
                max_notes=self._cfg["max_notes"],
                dynamic_range_db=self._cfg["dynamic_range"],
                piano_limit=not self._cfg["no_piano_limit"],
                high_damp=self._cfg["high_damp"],
                mid_boost=self._cfg["mid_boost"],
            )
            self.progress.emit(75)
            if self.isInterruptionRequested():
                return

            midi = create_midi(
                frame_notes, sr, self._cfg["hop_length"],
                min_duration_ms=self._cfg["min_duration"],
            )
            self.progress.emit(90)
            if self.isInterruptionRequested():
                return

            save_midi(midi, self._output)
            self.progress.emit(100)
            self.finished.emit(self._output)

        except Exception as exc:
            self.error.emit(f"{type(exc).__name__}：{exc}\n\n"
                            f"{traceback.format_exc()}")


class PianoLoTayu(Ui_Form, QtWidgets.QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setupUi(self)
        self.retranslateUi(self)
        self._worker: ConversionWorker | None = None
        self._taskbar = TaskbarProgress(self)
        self._init_drop_area()
        self._init_io()
        self._init_convert()
        self._bind_defaults()

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        if self._worker is not None:
            try:
                if self._worker.isRunning():
                    self._worker.requestInterruption()
                    self._worker.quit()
                    self._worker.wait(3000)
            except RuntimeError:
                pass  # C++ object already deleted
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
    _FILTER = ("音频文件 (*.wav *.flac *.mp3 *.ogg *.m4a);;"
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
        self._worker.finished.connect(self._on_conversion_done)
        self._worker.error.connect(self._on_conversion_error)

        self.progressBar.setValue(0)
        self.convertButton.setEnabled(False)
        self._worker.start()

    def _on_progress(self, pct: int) -> None:
        self.progressBar.setValue(pct)
        self._taskbar.show_normal(pct)

    def _on_conversion_done(self, output: str) -> None:
        if self._worker is not None:
            self._worker.wait(5000)
            self._worker = None
        self.convertButton.setEnabled(True)
        self.previewButton.setEnabled(True)
        self.progressBar.setValue(100)
        self._taskbar.hide()
        self._show_success_dialog(Path(output))

    @staticmethod
    def _friendly_error(msg: str) -> str:
        """Map raw exception text to a short user-facing message."""
        lower = msg.lower()
        if "nobackenderror" in lower:
            return "不支持的文件格式，请检查文件是否损坏或格式是否正确"
        if "filenotfounderror" in lower:
            return "找不到输入文件"
        if "permissionerror" in lower:
            return "没有读取或写入文件的权限"
        if "memoryerror" in lower:
            return "内存不足，请尝试降低采样率或 FFT 窗口大小"
        # Generic: extract the exception class name
        import re
        m = re.match(r"(\w+Error|\w+Exception)", msg)
        if m:
            return f"转换失败：{m.group(1)}"
        return "转换过程中出现未知错误"

    def _on_conversion_error(self, msg: str) -> None:
        if self._worker is not None:
            self._worker.wait(5000)
            self._worker = None
        self.convertButton.setEnabled(True)
        self.progressBar.setValue(0)
        self._taskbar.show_error(0)

        friendly = self._friendly_error(msg)

        box = QtWidgets.QMessageBox(self)
        box.setWindowTitle("转换失败")
        box.setText(friendly)
        box.setIcon(QtWidgets.QMessageBox.Icon.Warning)

        btn_ok = box.addButton("确定", QtWidgets.QMessageBox.ButtonRole.AcceptRole)
        btn_detail = box.addButton("详细信息…", QtWidgets.QMessageBox.ButtonRole.HelpRole)
        box.exec()

        if box.clickedButton() is btn_detail:
            self._show_error_detail(msg)

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


def main() -> int:
    import signal

    app = QtWidgets.QApplication()
    app.setApplicationName("PianoLoTayu")
    app.setOrganizationName("PianoLoTayu")

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
    window.show()
    app.exec()
    return 0


if __name__ == "__main__":
    main()

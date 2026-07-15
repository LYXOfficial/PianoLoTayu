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
from ..config import DEFAULTS, VERSION


_HINT_TEXT = (
    '<span style="font-size:36pt; color: #555;">+</span><br>'
    '<span style="font-size:10pt; color: #888;">'
    "拖拽文件至此处或点击打开文件</span><br>"
    '<span style="font-size:9pt; color: #aaa;">'
    "支持格式：wav, flac, mp3, ogg, m4a</span>"
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
    "sr": "sampleRateBox",
    "n_fft": "windowFFTBox",
    "hop_length": "hopLengthBox",
    "threshold": "thresholdBox",
    "max_notes": "maxFrameNotesBox",
    "min_duration": "minDurationBox",
    "dynamic_range": "dynamicRangeBox",
    "high_damp": "highDampBox",
    "mid_boost": "voiceBoostBox",
    "no_piano_limit": "pianoLimitSwitch",
}


class PianoLoTayu(Ui_Form, QtWidgets.QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setupUi(self)
        self.retranslateUi(self)
        self._init_drop_area()
        self._bind_defaults()

    def _init_drop_area(self) -> None:
        old = self.addFileArea
        self.drop_area = DropLabel(hint=_HINT_TEXT, parent=self)
        self.drop_area.setObjectName("addFileArea")
        _swap_widget(old, self.drop_area)

    def _bind_defaults(self) -> None:
        # Version label
        self.versionLabel.setText(
            f'<span style="color: #999;">{VERSION}</span>'
        )
        self.versionLabel.setTextFormat(QtCore.Qt.TextFormat.RichText)

        for key, widget_name in _WIDGET_CONFIG_MAP.items():
            widget = getattr(self, widget_name, None)
            if widget is None:
                continue
            val = DEFAULTS[key]
            if isinstance(val, bool):
                widget.setChecked(val)
            elif isinstance(val, float):
                widget.setValue(float(val))
            else:
                widget.setValue(int(val))

    def collect_config(self) -> dict:
        cfg = {}
        for key, widget_name in _WIDGET_CONFIG_MAP.items():
            widget = getattr(self, widget_name, None)
            if widget is None:
                continue
            val = widget.value()
            if isinstance(val, float) and key in (
                "sr", "n_fft", "hop_length", "max_notes", "min_duration",
            ):
                val = int(val)
            cfg[key] = val
        return cfg


def main() -> int:
    app = QtWidgets.QApplication()

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

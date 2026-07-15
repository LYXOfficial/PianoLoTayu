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


# ── Stylesheet fragments ─────────────────────────────────────────────────
_DROP_AREA_STYLE = """
QPushButton#addFileArea {
    background: transparent;
    border: 2px dashed #999;
    border-radius: 8px;
}
QPushButton#addFileArea:hover,
QPushButton#addFileArea:drag-hover {
    background: rgba(128, 128, 128, 0.12);
    border-color: #666;
}
"""


_HINT_TEXT = (
    '<span style="font-size:36pt; color: #555;">+</span><br>'
    '<span style="font-size:10pt; color: #888;">'
    "拖拽文件至此处或点击打开文件</span><br>"
    '<span style="font-size:9pt; color: #aaa;">'
    "支持格式：wav, flac, mp3, ogg, m4a</span>"
)


class DropAreaButton(QtWidgets.QPushButton):
    """A transparent drop-target button with dashed border."""

    drag_hover = QtCore.Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self._hover = False

    # ── Drag & drop (visual only for now) ──────────────────────────────
    def dragEnterEvent(self, event: QtGui.QDragEnterEvent) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            self._set_drag_hover(True)

    def dragMoveEvent(self, event: QtGui.QDragMoveEvent) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dragLeaveEvent(self, event: QtGui.QDragLeaveEvent) -> None:
        self._set_drag_hover(False)

    def dropEvent(self, event: QtGui.QDropEvent) -> None:
        self._set_drag_hover(False)
        # TODO: business logic — collect file paths from event.mimeData().urls()

    def enterEvent(self, event: QtCore.QEvent) -> None:
        self._hover = True
        self._update_style()

    def leaveEvent(self, event: QtCore.QEvent) -> None:
        self._hover = False
        self._update_style()

    # ── helpers ────────────────────────────────────────────────────────
    def _set_drag_hover(self, on: bool) -> None:
        self.setProperty("drag-hover", on)
        self.style().unpolish(self)
        self.style().polish(self)

    def _update_style(self) -> None:
        self.style().unpolish(self)
        self.style().polish(self)


class PianoLoTayu(Ui_Form, QtWidgets.QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setupUi(self)
        self.retranslateUi(self)
        self._post_init()

    def _post_init(self) -> None:
        # Replace the Designer-created addFileArea with our custom drop-target
        old_btn = self.addFileArea
        layout = old_btn.parentWidget().layout()
        # Find the index of the old button in its layout
        idx = layout.indexOf(old_btn) if layout else -1

        self.drop_area = DropAreaButton(self)
        self.drop_area.setObjectName("addFileArea")
        self.drop_area.setSizePolicy(old_btn.sizePolicy())
        self.drop_area.setMinimumSize(old_btn.minimumSize())
        self.drop_area.setText(_HINT_TEXT)

        if layout and idx >= 0:
            layout.insertWidget(idx, self.drop_area)

        old_btn.hide()
        old_btn.deleteLater()


def main() -> int:
    app = QtWidgets.QApplication()

    # System sans-serif override — stylesheet beats hard-coded Designer fonts.
    sys_font = QtGui.QFontDatabase.systemFont(
        QtGui.QFontDatabase.SystemFont.GeneralFont
    )
    app.setFont(sys_font)
    app.setStyleSheet(_DROP_AREA_STYLE)

    window = PianoLoTayu()
    window.show()
    app.exec()
    return 0


if __name__ == "__main__":
    main()

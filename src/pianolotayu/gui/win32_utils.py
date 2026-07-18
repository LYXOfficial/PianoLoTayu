"""Windows-specific utilities: taskbar progress bar & focus-beep suppression.

All classes/functions are safe to import and call on non-Windows platforms —
they become no-ops so callers don't need platform guards.
"""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes, byref, cast, sizeof, POINTER, c_void_p, c_ulong, c_int
from typing import Any

from PySide6 import QtWidgets, QtCore

# ═══════════════════════════════════════════════════════════════════════════════
# Platform guard
# ═══════════════════════════════════════════════════════════════════════════════
_IS_WIN32 = sys.platform == "win32"

# ═══════════════════════════════════════════════════════════════════════════════
# TBPFLAG constants (always defined — used by TaskbarProgress on all platforms)
# ═══════════════════════════════════════════════════════════════════════════════
TBPF_NOPROGRESS = 0x0
TBPF_INDETERMINATE = 0x1
TBPF_NORMAL = 0x2
TBPF_ERROR = 0x4
TBPF_PAUSED = 0x8

# ═══════════════════════════════════════════════════════════════════════════════
# Taskbar progress (ITaskbarList3 via ctypes / COM)
# ═══════════════════════════════════════════════════════════════════════════════

if _IS_WIN32:
    # ── GUID helpers ──────────────────────────────────────────────────────
    class _GUID(ctypes.Structure):
        _fields_ = [
            ("Data1", c_ulong),
            ("Data2", wintypes.USHORT),
            ("Data3", wintypes.USHORT),
            ("Data4", wintypes.BYTE * 8),
        ]

    # ITaskbarList3 CLSID  : {56FDF344-FD6D-11D0-958A-006097C9A090}
    _CLSID_TaskbarList = _GUID(
        0x56FDF344, 0xFD6D, 0x11D0,
        (0x95, 0x8A, 0x00, 0x60, 0x97, 0xC9, 0xA0, 0x90),
    )
    # ITaskbarList3 IID    : {EA1AFB91-9E28-4B86-90E9-9E9F8A5EEFAF}
    _IID_ITaskbarList3 = _GUID(
        0xEA1AFB91, 0x9E28, 0x4B86,
        (0x90, 0xE9, 0x9E, 0x9F, 0x8A, 0x5E, 0xEF, 0xAF),
    )

    # ── ITaskbarList3 vtable wrapper ──────────────────────────────────────
    # The vtable layout (IUnknown + ITaskbarList + ITaskbarList2 + ITaskbarList3):
    #   0 QueryInterface, 1 AddRef, 2 Release,
    #   3 HrInit, 4 AddTab, 5 DeleteTab, 6 ActivateTab, 7 SetActiveAlt,
    #   8 MarkFullscreenWindow,
    #   9 SetProgressValue, 10 SetProgressState, … (12 more we don't need)
    _ITASKBARLIST3_VTABLE_SLOTS = 22

    class _ITaskbarList3(ctypes.Structure):
        pass

    # Pointers are void* — we set the argtypes on each call site.
    _ITaskbarList3._fields_ = [("_vtbl", POINTER(c_void_p * _ITASKBARLIST3_VTABLE_SLOTS))]

    # Prototype helpers
    _STDMETHOD = ctypes.WINFUNCTYPE(ctypes.c_long, c_void_p)          # Release/AddRef
    _STDMETHOD_HWND_ULL_ULL = ctypes.WINFUNCTYPE(                      # SetProgressValue
        ctypes.c_long, c_void_p, wintypes.HWND, ctypes.c_ulonglong,
        ctypes.c_ulonglong,
    )
    _STDMETHOD_HWND_DWORD = ctypes.WINFUNCTYPE(                        # SetProgressState
        ctypes.c_long, c_void_p, wintypes.HWND, ctypes.c_int,
    )

    # Cache ole32 functions
    _ole32 = ctypes.windll.ole32
    _ole32.CoCreateInstance.argtypes = [
        POINTER(_GUID),             # rclsid
        c_void_p,                   # pUnkOuter
        wintypes.DWORD,             # dwClsContext
        POINTER(_GUID),             # riid
        POINTER(c_void_p),          # ppv
    ]
    _ole32.CoCreateInstance.restype = ctypes.c_long

    CLSCTX_INPROC_SERVER = 1


def _get_hwnd(widget: QtWidgets.QWidget) -> int | None:
    """Get the native HWND for *widget*, or None if not yet realised."""
    if widget is None:
        return None
    try:
        wid = widget.winId()
        if wid is not None:
            return int(wid)
    except Exception:
        pass
    return None


class TaskbarProgress:
    """Windows taskbar progress indicator for a top-level QWidget.

    On non-Windows platforms every method is a safe no-op.

    Usage::

        tb = TaskbarProgress(my_window)
        tb.show_normal(50)        # 50 %
        tb.show_paused(75)        # 75 % (yellow)
        tb.show_error(30)         # 30 % (red)
        tb.show_indeterminate()   # spinning green bar
        tb.hide()                 # remove progress

    Thread safety: call from the main (GUI) thread only — this touches COM
    objects that were created on the main thread.
    """

    def __init__(self, widget: QtWidgets.QWidget):
        self._widget = widget
        self._itaskbar3: _ITaskbarList3 | None = None  # type: ignore[valid-type]
        self._ref_add: Any = None
        self._ref_release: Any = None
        self._fn_set_value: Any = None
        self._fn_set_state: Any = None
        self._current_state: int = TBPF_NOPROGRESS if _IS_WIN32 else 0

        if _IS_WIN32:
            self._init_com()

    def _init_com(self) -> None:
        if not _IS_WIN32:
            return
        ppv = c_void_p()
        hr = _ole32.CoCreateInstance(
            byref(_CLSID_TaskbarList), None, CLSCTX_INPROC_SERVER,
            byref(_IID_ITaskbarList3), byref(ppv),
        )
        if hr < 0 or not ppv:
            return  # COM unavailable (e.g. running in a session without explorer)

        ptr: int | None = ppv.value
        if ptr is None:
            return
        self._itaskbar3 = cast(c_void_p(ptr), POINTER(_ITaskbarList3))

        vtbl = self._itaskbar3.contents._vtbl.contents
        # AddRef  = vtbl[1], Release = vtbl[2]
        self._ref_add = _STDMETHOD(vtbl[1])
        self._ref_release = _STDMETHOD(vtbl[2])
        # SetProgressValue  = vtbl[9], SetProgressState = vtbl[10]
        self._fn_set_value = _STDMETHOD_HWND_ULL_ULL(vtbl[9])
        self._fn_set_state = _STDMETHOD_HWND_DWORD(vtbl[10])

    def _hwnd(self) -> int:
        h = _get_hwnd(self._widget)
        return h if h is not None else 0

    def _call_set_state(self, flags: int) -> None:
        if self._fn_set_state is None:
            return
        hwnd = self._hwnd()
        if hwnd:
            self._fn_set_state(self._itaskbar3, hwnd, flags)
        self._current_state = flags

    def _call_set_value(self, completed: int, total: int) -> None:
        if self._fn_set_value is None:
            return
        hwnd = self._hwnd()
        if hwnd:
            self._fn_set_value(self._itaskbar3, hwnd,
                               ctypes.c_ulonglong(completed),
                               ctypes.c_ulonglong(total))

    # ── Public API ───────────────────────────────────────────────────────

    def show_normal(self, value: int, total: int = 100) -> None:
        """Green progress bar at *value* / *total*."""
        self._call_set_state(TBPF_NORMAL)
        self._call_set_value(value, total)

    def show_paused(self, value: int, total: int = 100) -> None:
        """Yellow progress bar at *value* / *total*."""
        self._call_set_state(TBPF_PAUSED)
        self._call_set_value(value, total)

    def show_error(self, value: int, total: int = 100) -> None:
        """Red progress bar at *value* / *total*."""
        self._call_set_state(TBPF_ERROR)
        self._call_set_value(value, total)

    def show_indeterminate(self) -> None:
        """Spinning (marquee) green bar — use when total is unknown."""
        self._call_set_state(TBPF_INDETERMINATE)

    def hide(self) -> None:
        """Remove the taskbar progress overlay."""
        self._call_set_state(TBPF_NOPROGRESS)

    def __del__(self) -> None:
        if self._itaskbar3 is not None and self._ref_release is not None:
            try:
                self._ref_release(self._itaskbar3)
            except Exception:
                pass


# ═══════════════════════════════════════════════════════════════════════════════
# Native event filter — suppresses Windows "ding" when clicking on a window
# that can't receive focus (e.g. parent window behind a modal dialog).
# ═══════════════════════════════════════════════════════════════════════════════

if _IS_WIN32:
    WM_MOUSEACTIVATE = 0x0021
    MA_NOACTIVATEANDEAT = 4

    class _MSG(ctypes.Structure):
        _fields_ = [
            ("hwnd", wintypes.HWND),
            ("message", wintypes.UINT),
            ("wParam", wintypes.WPARAM),
            ("lParam", wintypes.LPARAM),
            ("time", wintypes.DWORD),
            ("pt", wintypes.POINT),
        ]


class BeepSuppressor(QtCore.QAbstractNativeEventFilter):
    """Suppress the Windows system beep when clicking a window that cannot
    receive focus because a modal dialog is active.

    Install once on the QApplication::

        app.installNativeEventFilter(BeepSuppressor())
    """

    def nativeEventFilter(
        self, eventType: QtCore.QByteArray, message: int,
    ) -> tuple[bool, int]:
        if not _IS_WIN32:
            return False, 0
        if eventType != b"windows_generic_MSG":
            return False, 0

        msg = cast(c_void_p(message), POINTER(_MSG)).contents
        if msg.message != WM_MOUSEACTIVATE:
            return False, 0

        # Only suppress when a modal dialog is stealing focus — clicking
        # outside the modal would normally produce a "ding". Returning
        # MA_NOACTIVATEANDEAT (4) eats the message silently.
        active_modal = QtWidgets.QApplication.activeModalWidget()
        if active_modal is not None:
            # The window receiving the click is NOT the modal → suppress beep.
            if msg.hwnd and active_modal.winId() and int(msg.hwnd) != int(active_modal.winId()):
                return True, MA_NOACTIVATEANDEAT

        return False, 0

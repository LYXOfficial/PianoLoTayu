"""Windows-specific utilities: taskbar progress, beep suppression, fluidsynth DLL.

All classes/functions are safe to import and call on non-Windows platforms —
they become no-ops (or return platform-appropriate status) so callers don't
need platform guards.
"""

from __future__ import annotations

import ctypes
import os
import sys
from ctypes import wintypes, byref, cast, sizeof, POINTER, c_void_p, c_ulong, c_int
from pathlib import Path
from typing import Any

from PySide6 import QtWidgets, QtCore, QtGui

# ═══════════════════════════════════════════════════════════════════════════════
# Platform guard
# ═══════════════════════════════════════════════════════════════════════════════
_IS_WIN32 = sys.platform == "win32"

# Project root icon.png (…/gui/win32_utils.py → parents[3] = repo root)
_APP_ICON_PATH = Path(__file__).resolve().parents[3] / "icon.png"
_app_icon_cache: QtGui.QIcon | None = None


def app_icon() -> QtGui.QIcon:
    """Application icon loaded from project-root ``icon.png`` (cached)."""
    global _app_icon_cache
    if _app_icon_cache is None:
        if _APP_ICON_PATH.is_file():
            _app_icon_cache = QtGui.QIcon(str(_APP_ICON_PATH))
        else:
            _app_icon_cache = QtGui.QIcon()  # empty fallback
    return _app_icon_cache


def set_app_user_model_id(app_id: str = "PianoLoTayu") -> None:
    """Windows: group taskbar entries under our own AppUserModelID.

    Without this, a script launched via ``python.exe`` keeps the Python icon
    on the taskbar even after ``setWindowIcon``.
    """
    if not _IS_WIN32:
        return
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(app_id)
    except Exception:
        pass


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


def _native_msg(message: object):
    """Dereference a QAbstractNativeEventFilter *message* pointer as MSG.

    PySide6 passes the native pointer as int / sip.voidptr / VoidPtr — not
    something ``c_void_p()`` always accepts.  ``int(message)`` is the portable
    way to get the address.
    """
    if not _IS_WIN32:
        return None
    try:
        addr = int(message)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        try:
            # Some bindings expose .__int__ only via asint / asvoidptr
            addr = int(getattr(message, "asint", lambda: message)())
        except Exception:
            return None
    if not addr:
        return None
    try:
        return cast(addr, POINTER(_MSG)).contents
    except Exception:
        return None


class BeepSuppressor(QtCore.QAbstractNativeEventFilter):
    """Suppress the Windows system beep when clicking a window that cannot
    receive focus because a modal dialog is active.

    Install once on the QApplication::

        app.installNativeEventFilter(BeepSuppressor())
    """

    def nativeEventFilter(
        self, eventType: QtCore.QByteArray, message: object,
    ) -> tuple[bool, int]:
        if not _IS_WIN32:
            return False, 0
        # eventType may be QByteArray or bytes
        et = bytes(eventType) if not isinstance(eventType, (bytes, bytearray)) else eventType
        if et != b"windows_generic_MSG":
            return False, 0

        msg = _native_msg(message)
        if msg is None or msg.message != WM_MOUSEACTIVATE:
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


# ═══════════════════════════════════════════════════════════════════════════════
# Elevated-process file drop (WM_DROPFILES across UIPI)
# ═══════════════════════════════════════════════════════════════════════════════
#
# When the app runs as Administrator and Explorer does not, OLE drag-drop is
# blocked by UIPI → permanent "prohibited" cursor.  Classic WM_DROPFILES can
# still work if we:
#   1. ChangeWindowMessageFilter (+ Ex) to allow DROPFILES/COPYDATA/COPYGLOBALDATA
#   2. DragAcceptFiles(hwnd, TRUE)
#   3. When elevated: RevokeDragDrop(hwnd) so Explorer falls back from OLE
#      (OLE is preferred; if it fails UIPI, drop is aborted — no WM_DROPFILES)
#   4. Handle WM_DROPFILES and extract paths via DragQueryFileW

if _IS_WIN32:
    WM_DROPFILES = 0x0233
    WM_COPYDATA = 0x004A
    WM_COPYGLOBALDATA = 0x0049  # undocumented but required for HDROP across UIPI
    MSGFLT_ADD = 1
    MSGFLT_ALLOW = 1
    _DROP_MSGS = (WM_DROPFILES, WM_COPYDATA, WM_COPYGLOBALDATA)

    class _CHANGEFILTERSTRUCT(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("ExtStatus", wintypes.DWORD),
        ]

    def is_process_elevated() -> bool:
        """True when this process is running elevated (Administrator)."""
        try:
            # TokenElevation: TOKEN_ELEVATION { DWORD TokenIsElevated }
            TOKEN_QUERY = 0x0008
            TokenElevation = 20
            kernel32 = ctypes.windll.kernel32
            advapi32 = ctypes.windll.advapi32
            h_proc = kernel32.GetCurrentProcess()
            h_token = wintypes.HANDLE()
            if not advapi32.OpenProcessToken(
                h_proc, TOKEN_QUERY, byref(h_token),
            ):
                # Fallback: older helper (also true for elevated admins)
                return bool(ctypes.windll.shell32.IsUserAnAdmin())
            try:
                elev = wintypes.DWORD()
                size = wintypes.DWORD()
                ok = advapi32.GetTokenInformation(
                    h_token, TokenElevation,
                    byref(elev), sizeof(elev), byref(size),
                )
                if ok:
                    return bool(elev.value)
            finally:
                kernel32.CloseHandle(h_token)
        except Exception:
            pass
        try:
            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        except Exception:
            return False

    def _allow_drop_messages_process_wide() -> None:
        """Process-wide UIPI filter — required so non-elevated Explorer can send
        HDROP into an elevated process (WM_COPYGLOBALDATA especially)."""
        user32 = ctypes.windll.user32
        # BOOL ChangeWindowMessageFilter(UINT message, DWORD dwFlag)
        # MSGFLT_ADD = 1.  Present since Vista; no-op failure is fine.
        try:
            user32.ChangeWindowMessageFilter.argtypes = [
                wintypes.UINT, wintypes.DWORD,
            ]
            user32.ChangeWindowMessageFilter.restype = wintypes.BOOL
        except Exception:
            pass
        for msg in _DROP_MSGS:
            try:
                user32.ChangeWindowMessageFilter(
                    wintypes.UINT(msg), wintypes.DWORD(MSGFLT_ADD),
                )
            except Exception:
                pass

    def _allow_drop_messages_for_hwnd(hwnd: int) -> None:
        """Per-window filter (Win7+). Complements the process-wide filter."""
        user32 = ctypes.windll.user32
        cfs = _CHANGEFILTERSTRUCT()
        cfs.cbSize = sizeof(_CHANGEFILTERSTRUCT)
        try:
            user32.ChangeWindowMessageFilterEx.argtypes = [
                wintypes.HWND, wintypes.UINT, wintypes.DWORD,
                ctypes.c_void_p,
            ]
            user32.ChangeWindowMessageFilterEx.restype = wintypes.BOOL
        except Exception:
            pass
        for msg in _DROP_MSGS:
            try:
                user32.ChangeWindowMessageFilterEx(
                    wintypes.HWND(hwnd), wintypes.UINT(msg),
                    wintypes.DWORD(MSGFLT_ALLOW), byref(cfs),
                )
            except Exception:
                pass

    def _drag_query_drop(
        hdrop: int,
    ) -> tuple[list[str], tuple[int, int] | None]:
        """Return (paths, client_point).  Point is in the receiving HWND's
        client coords (from DragQueryPoint), or None if unavailable.

        Must run before DragFinish — point + files are read first.
        """
        shell32 = ctypes.windll.shell32
        pt: tuple[int, int] | None = None
        try:
            shell32.DragQueryPoint.argtypes = [
                wintypes.HANDLE, POINTER(wintypes.POINT),
            ]
            shell32.DragQueryPoint.restype = wintypes.BOOL
            p = wintypes.POINT()
            if shell32.DragQueryPoint(wintypes.HANDLE(hdrop), byref(p)):
                pt = (int(p.x), int(p.y))
        except Exception:
            pt = None

        shell32.DragQueryFileW.argtypes = [
            wintypes.HANDLE, wintypes.UINT,
            wintypes.LPWSTR, wintypes.UINT,
        ]
        shell32.DragQueryFileW.restype = wintypes.UINT
        count = shell32.DragQueryFileW(
            wintypes.HANDLE(hdrop), 0xFFFFFFFF, None, 0,
        )
        paths: list[str] = []
        buf = ctypes.create_unicode_buffer(32768)
        for i in range(int(count)):
            n = shell32.DragQueryFileW(
                wintypes.HANDLE(hdrop), wintypes.UINT(i), buf, 32768,
            )
            if n:
                paths.append(buf.value)
        try:
            shell32.DragFinish(wintypes.HANDLE(hdrop))
        except Exception:
            pass
        return paths, pt

    def _revoke_ole_drop(hwnd: int) -> None:
        """Remove Qt/OLE IDropTarget so Explorer falls back to WM_DROPFILES.

        Without this, elevated targets keep a registered OLE drop target;
        non-elevated Explorer tries OLE first, UIPI blocks it, and the drop
        is aborted — WM_DROPFILES is never sent.
        """
        try:
            # HRESULT RevokeDragDrop(HWND)
            ole32 = ctypes.windll.ole32
            ole32.RevokeDragDrop.argtypes = [wintypes.HWND]
            ole32.RevokeDragDrop.restype = ctypes.c_long
            ole32.RevokeDragDrop(wintypes.HWND(hwnd))
        except Exception:
            pass


else:
    def is_process_elevated() -> bool:
        return False


class ElevatedDropFilter(QtCore.QAbstractNativeEventFilter):
    """Receive Explorer file drops even when this process is elevated.

    Install after the widget has a native HWND (e.g. in ``showEvent``)::

        filt = enable_elevated_file_drop(window, on_paths)
        # keep a reference on the window so the filter is not GC'd
    """

    def __init__(self, callback) -> None:
        super().__init__()
        # callback(paths: list[str], client_pt: tuple[int,int] | None)
        # client_pt = drop position in the top-level HWND client coords
        self._callback = callback

    def nativeEventFilter(
        self, eventType: QtCore.QByteArray, message: object,
    ) -> tuple[bool, int]:
        if not _IS_WIN32:
            return False, 0
        et = (bytes(eventType)
              if not isinstance(eventType, (bytes, bytearray))
              else eventType)
        if et != b"windows_generic_MSG":
            return False, 0
        msg = _native_msg(message)
        if msg is None or msg.message != WM_DROPFILES:
            return False, 0
        # wParam is HDROP
        try:
            hdrop = int(msg.wParam)
        except (TypeError, ValueError):
            return True, 0
        paths, pt = _drag_query_drop(hdrop)
        if paths and self._callback is not None:
            # Defer to Qt event loop so we stay out of the native filter stack
            QtCore.QTimer.singleShot(
                0,
                lambda p=list(paths), cpt=pt: self._callback(p, cpt),
            )
        return True, 0


def enable_elevated_file_drop(
    widget: QtWidgets.QWidget, callback,
) -> ElevatedDropFilter | None:
    """Allow non-elevated Explorer to drop files onto *widget* when elevated.

    Always installs the UIPI message filter + ``DragAcceptFiles``.  When the
    process is elevated, also revokes the OLE drop target (which UIPI would
    otherwise leave in a permanent "prohibited" state) so Explorer falls back
    to classic ``WM_DROPFILES``.

    Returns the installed filter (keep a reference), or None on non-Windows /
    if the HWND is not ready yet.  Call ``reassert_elevated_file_drop`` later
    if Qt may have re-registered OLE.
    """
    if not _IS_WIN32:
        return None
    if not reassert_elevated_file_drop(widget):
        return None
    if callback is None:
        return None
    filt = ElevatedDropFilter(callback)
    app = QtWidgets.QApplication.instance()
    if app is not None:
        app.installNativeEventFilter(filt)
    return filt


def reassert_elevated_file_drop(widget: QtWidgets.QWidget) -> bool:
    """Re-apply UIPI filter + DragAcceptFiles (+ OLE revoke when elevated).

    Safe to call repeatedly — does not install another native event filter.
    Returns True if the HWND was ready and setup was applied.
    """
    if not _IS_WIN32:
        return False
    hwnd = _get_hwnd(widget)
    if not hwnd:
        return False

    # 1) UIPI: let non-elevated senders deliver drop messages
    _allow_drop_messages_process_wide()
    _allow_drop_messages_for_hwnd(hwnd)

    # 2) Classic drop target flag on the top-level HWND
    try:
        shell32 = ctypes.windll.shell32
        shell32.DragAcceptFiles.argtypes = [wintypes.HWND, wintypes.BOOL]
        shell32.DragAcceptFiles.restype = None
        shell32.DragAcceptFiles(wintypes.HWND(hwnd), True)
    except Exception:
        return False

    # 3) Elevated: kill OLE so Explorer doesn't abort after UIPI-blocked OLE
    if is_process_elevated():
        _revoke_ole_drop(hwnd)
        # Also clear Qt acceptDrops on the window tree — otherwise Qt may
        # re-RegisterDragDrop on later events and steal the target again.
        try:
            widget.setAcceptDrops(False)
        except Exception:
            pass
        for child in widget.findChildren(QtWidgets.QWidget):
            try:
                if child.acceptDrops():
                    child.setAcceptDrops(False)
            except Exception:
                pass
        # Re-assert DragAcceptFiles after RevokeDragDrop
        try:
            ctypes.windll.shell32.DragAcceptFiles(
                wintypes.HWND(hwnd), True,
            )
        except Exception:
            pass
    return True


def prepare_elevated_drop_filters() -> None:
    """Process-wide UIPI filter — call once at startup (before first show)."""
    if not _IS_WIN32:
        return
    try:
        _allow_drop_messages_process_wide()
    except Exception:
        pass


# ═══════════════════════════════════════════════════════════════════════════════
# FluidSynth DLL discovery (Windows primarily; Linux/macOS use system packages)
# ═══════════════════════════════════════════════════════════════════════════════

# DLL basenames that pyfluidsynth / FluidSynth ship under
_FL_DLL_NAMES = (
    "libfluidsynth-3.dll",
    "libfluidsynth-2.dll",
    "libfluidsynth-1.dll",
    "libfluidsynth.dll",
    "fluidsynth-3.dll",  # FluidSynth ≥ 2.4.5
    "fluidsynth-2.dll",
    "fluidsynth.dll",
)

_fl_setup_done = False
_fl_found_dir: Path | None = None


def fluidsynth_search_dirs() -> list[Path]:
    """Directories we scan for the fluidsynth shared library."""
    dirs: list[Path] = []

    def _add(p: Path | str | None) -> None:
        if not p:
            return
        try:
            pp = Path(p)
        except (TypeError, ValueError):
            return
        dirs.append(pp)

    # 1. Next to this package / project root
    here = Path(__file__).resolve()
    for parent in here.parents[:5]:
        _add(parent)
        _add(parent / "bin")
        _add(parent / "fluidsynth")
        _add(parent / "fluidsynth" / "bin")
        _add(parent / "lib")

    # 2. Frozen / installed exe layout
    _add(Path(sys.executable).parent)
    _add(Path(sys.executable).parent / "bin")
    _add(Path(sys.executable).parent / "fluidsynth" / "bin")
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        _add(Path(sys._MEIPASS))  # type: ignore[attr-defined]
        _add(Path(sys._MEIPASS) / "fluidsynth" / "bin")  # type: ignore[attr-defined]

    # 3. CWD (what pyfluidsynth itself adds via add_dll_directory)
    _add(Path.cwd())
    _add(Path.cwd() / "bin")
    _add(Path.cwd() / "fluidsynth" / "bin")

    # 4. Common install locations
    _add(Path(r"C:\tools\fluidsynth\bin"))
    local = os.environ.get("LOCALAPPDATA", "")
    appdata = os.environ.get("APPDATA", "")
    pf = os.environ.get("ProgramFiles", r"C:\Program Files")
    pf86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
    for base in (local, appdata, pf, pf86):
        _add(Path(base) / "fluidsynth" / "bin")
        _add(Path(base) / "FluidSynth" / "bin")

    # 5. Existing PATH entries (in case DLL is already on PATH but find_library fails)
    for entry in os.environ.get("PATH", "").split(os.pathsep):
        _add(entry)

    # Deduplicate while preserving order
    seen: set[str] = set()
    out: list[Path] = []
    for d in dirs:
        try:
            key = str(d.resolve()).lower()
        except OSError:
            key = str(d).lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(d)
    return out


def find_fluidsynth_dir() -> Path | None:
    """Return the directory containing a fluidsynth DLL, or None."""
    for d in fluidsynth_search_dirs():
        try:
            if not d.is_dir():
                continue
        except OSError:
            continue
        for name in _FL_DLL_NAMES:
            if (d / name).is_file():
                return d
    return None


def setup_fluidsynth_dll() -> Path | None:
    """Register the fluidsynth DLL directory so pyfluidsynth can load it.

    pyfluidsynth uses ``ctypes.util.find_library``, which on Windows only
    searches ``PATH`` (not ``os.add_dll_directory``).  We therefore both
    prepend the directory to ``PATH`` *and* call ``add_dll_directory`` so
    dependent DLLs (glib, intl, sndfile, …) resolve when ``CDLL`` loads.

    Safe to call multiple times.  Returns the directory found, or None.
    No-op on non-Windows platforms.
    """
    global _fl_setup_done, _fl_found_dir
    if not _IS_WIN32:
        return None
    if _fl_setup_done:
        return _fl_found_dir

    found = find_fluidsynth_dir()
    _fl_found_dir = found
    _fl_setup_done = True
    if found is None:
        return None

    dir_s = str(found.resolve())
    try:
        os.add_dll_directory(dir_s)
    except (OSError, AttributeError):
        pass

    # find_library() only looks at PATH — this is the critical part
    path_parts = os.environ.get("PATH", "").split(os.pathsep)
    if not any(p.lower() == dir_s.lower() for p in path_parts if p):
        os.environ["PATH"] = dir_s + os.pathsep + os.environ.get("PATH", "")

    return found


def fluidsynth_status_message() -> str:
    """Human-readable diagnostics for missing-library errors."""
    if not _IS_WIN32:
        return (
            "未找到 fluidsynth 系统库。\n"
            "Linux: sudo pacman -S fluidsynth  或  sudo apt install fluidsynth libfluidsynth3\n"
            "macOS: brew install fluid-synth"
        )
    found = find_fluidsynth_dir()
    if found is not None:
        return (
            f"已在以下目录找到 DLL：\n{found}\n"
            "但仍无法加载（可能缺少依赖 DLL，如 libglib / libintl / libsndfile）。\n"
            "请把 FluidSynth 发布包 bin 目录下的全部 DLL 一起放进去。"
        )
    tried = "\n".join(f"  · {d}" for d in fluidsynth_search_dirs()[:12])
    return (
        "未找到 fluidsynth DLL（libfluidsynth-3.dll / fluidsynth-3.dll）。\n\n"
        "请从 https://github.com/FluidSynth/fluidsynth/releases 下载 Windows 包，\n"
        "把 bin 目录下的全部 DLL 放到以下任一位置：\n"
        "  1. 程序 exe 同目录\n"
        "  2. 程序目录\\fluidsynth\\bin\\\n"
        "  3. 当前工作目录\n"
        "  4. C:\\tools\\fluidsynth\\bin\\\n\n"
        f"已搜索（前几项）：\n{tried}"
    )

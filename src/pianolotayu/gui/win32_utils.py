"""Windows-specific utilities: taskbar progress, beep suppression, fluidsynth DLL.

All classes/functions are safe to import and call on non-Windows platforms —
they become no-ops (or return platform-appropriate status) so callers don't
need platform guards.
"""

from __future__ import annotations

import ctypes
import os
import shutil
import sys
from ctypes import wintypes, byref, cast, sizeof, POINTER, c_void_p, c_ulong, c_int
from functools import lru_cache
from pathlib import Path
from typing import Any

from PySide6 import QtWidgets, QtCore, QtGui

# ═══════════════════════════════════════════════════════════════════════════════
# Platform guard
# ═══════════════════════════════════════════════════════════════════════════════
_IS_WIN32 = sys.platform == "win32"


def app_base_dirs() -> list[Path]:
    """Candidate roots for loose data (icon.ico, soundfonts/, fluidsynth/).

    Order matters — first existing hit wins for most callers:

    1. Directory of the running executable (Nuitka/PyInstaller dist folder)
    2. Current working directory
    3. Source-tree repo root (…/gui/win32_utils.py → parents[3])
    4. Ancestors of ``__file__`` (frozen layouts that nest the package)
    """
    dirs: list[Path] = []
    seen: set[str] = set()

    def _add(p: Path | None) -> None:
        if p is None:
            return
        try:
            key = str(p.resolve())
        except OSError:
            key = str(p)
        if key in seen:
            return
        seen.add(key)
        dirs.append(p)

    # Frozen / Nuitka standalone: data is dropped next to the binary
    try:
        _add(Path(sys.executable).resolve().parent)
    except Exception:
        pass
    # PyInstaller onefile extract dir
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        try:
            _add(Path(sys._MEIPASS))  # type: ignore[attr-defined]
        except Exception:
            pass
    try:
        _add(Path.cwd())
    except Exception:
        pass
    # Dev: repo root (gui → pianolotayu → src → root)
    here = Path(__file__).resolve()
    if len(here.parents) > 3:
        _add(here.parents[3])
    for parent in here.parents[:6]:
        _add(parent)
    return dirs


def find_data_file(*relative: str) -> Path | None:
    """Return the first existing path under :func:`app_base_dirs` / *relative*."""
    rel = Path(*relative)
    for base in app_base_dirs():
        cand = base / rel
        try:
            if cand.is_file():
                return cand
        except OSError:
            continue
    return None


def find_data_dir(*relative: str) -> Path | None:
    """Return the first existing directory under :func:`app_base_dirs` / *relative*."""
    rel = Path(*relative)
    for base in app_base_dirs():
        cand = base / rel
        try:
            if cand.is_dir():
                return cand
        except OSError:
            continue
    return None


def soundfont_dir() -> Path:
    """Directory scanned for ``*.sf2`` (may not exist yet — callers glob safely)."""
    found = find_data_dir("soundfonts")
    if found is not None:
        return found
    # Prefer exe-adjacent path when frozen so users know where to drop fonts
    try:
        return Path(sys.executable).resolve().parent / "soundfonts"
    except Exception:
        return Path.cwd() / "soundfonts"


# ═══════════════════════════════════════════════════════════════════════════════
# ffmpeg discovery (imageio-ffmpeg / dist / PATH) — all platforms
# ═══════════════════════════════════════════════════════════════════════════════

def _looks_like_ffmpeg_name(name: str) -> bool:
    n = name.lower()
    return n == "ffmpeg" or n == "ffmpeg.exe" or n.startswith("ffmpeg-")


def _is_ffmpeg_candidate(p: Path) -> bool:
    """Accept real files, symlinks, or launcher scripts named ffmpeg*."""
    try:
        if not _looks_like_ffmpeg_name(p.name):
            return False
        # is_file() follows symlinks; also allow symlink path itself
        return p.is_file() or p.is_symlink()
    except OSError:
        return False


def _find_ffmpeg_in_dir(d: Path) -> Path | None:
    try:
        if not d.is_dir():
            return None
    except OSError:
        return None
    hits: list[Path] = []
    try:
        for f in d.iterdir():
            if _is_ffmpeg_candidate(f):
                hits.append(f)
    except OSError:
        return None
    if not hits:
        return None
    # Prefer larger when several (static build vs tiny wrapper), but no minimum size
    def _size(p: Path) -> int:
        try:
            return p.stat().st_size
        except OSError:
            return 0
    hits.sort(key=_size, reverse=True)
    return hits[0]


def _ffmpeg_search_dirs() -> list[Path]:
    dirs: list[Path] = []
    seen: set[str] = set()

    def add(p: Path | None) -> None:
        if p is None:
            return
        try:
            key = str(p.resolve())
        except OSError:
            key = str(p)
        if key in seen:
            return
        seen.add(key)
        dirs.append(p)

    for base in app_base_dirs():
        add(base)
        add(base / "imageio_ffmpeg" / "binaries")
        add(base / "binaries")
        add(base / "bin")
        add(base / "ffmpeg")

    try:
        import imageio_ffmpeg
        pkg = Path(imageio_ffmpeg.__file__).resolve().parent
        add(pkg)
        add(pkg / "binaries")
    except Exception:
        pass
    try:
        import importlib.resources as ir
        ref = ir.files("imageio_ffmpeg.binaries") / "__init__.py"
        with ir.as_file(ref) as path:
            add(Path(path).parent)
    except Exception:
        pass
    return dirs


@lru_cache(maxsize=1)
def get_ffmpeg_exe() -> str:
    """Locate ffmpeg without hardcoding versioned filenames.

    Accepts real binaries, symlinks, or launcher scripts named ``ffmpeg*``.
    Order: ``IMAGEIO_FFMPEG_EXE`` → dist/package dirs → imageio_ffmpeg → PATH.
    """
    env = os.environ.get("IMAGEIO_FFMPEG_EXE", "").strip()
    if env:
        ep = Path(env)
        if ep.exists():
            return str(ep)

    preferred_name = ""
    try:
        from imageio_ffmpeg._definitions import FNAME_PER_PLATFORM, get_platform
        preferred_name = FNAME_PER_PLATFORM.get(get_platform(), "") or ""
    except Exception:
        pass

    for d in _ffmpeg_search_dirs():
        for search in (d / "binaries", d):
            if preferred_name:
                pref = search / preferred_name
                if _is_ffmpeg_candidate(pref):
                    try:
                        return str(pref.resolve())
                    except OSError:
                        return str(pref)
            hit = _find_ffmpeg_in_dir(search)
            if hit is not None:
                try:
                    return str(hit.resolve())
                except OSError:
                    return str(hit)

    try:
        from imageio_ffmpeg import get_ffmpeg_exe as _upstream
        exe = _upstream()
        if exe and Path(exe).exists():
            return str(exe)
    except Exception:
        pass

    found = shutil.which("ffmpeg") or shutil.which("ffmpeg.exe")
    if found:
        return found

    tried = "\n".join(f"  · {d}" for d in _ffmpeg_search_dirs()[:12])
    raise FileNotFoundError(
        "找不到 ffmpeg。\n"
        "请将 imageio-ffmpeg 的 binaries 放到程序旁 "
        "(…/imageio_ffmpeg/binaries/ffmpeg*)，\n"
        "或设置 IMAGEIO_FFMPEG_EXE=路径（可为脚本/符号链接）。\n"
        f"已搜索：\n{tried}"
    )


_app_icon_cache: QtGui.QIcon | None = None


def app_icon() -> QtGui.QIcon:
    """Application icon from ``icon.ico`` next to the app / repo root (cached)."""
    global _app_icon_cache
    if _app_icon_cache is None:
        path = find_data_file("icon.ico")
        if path is not None:
            _app_icon_cache = QtGui.QIcon(str(path))
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


def attach_parent_console() -> bool:
    """Reconnect ``sys.stdout`` / ``sys.stderr`` to the parent console if any.

    Nuitka ``--windows-console-mode=attach``:
      * From cmd/PowerShell → parent console exists → we bind streams to CONOUT$
      * From Explorer double-click → no parent → AttachConsole fails

    Opening CONOUT$ / fd 1 when there is **no** console yields WinError 6
    (句柄无效) later on print/flush — which matches "double-click breaks,
    cmd/attach works, disable works".  So we only rebind when std handles
    are actually valid; otherwise install a quiet sink.
    """
    if not _IS_WIN32:
        try:
            return bool(sys.stdout and not getattr(sys.stdout, "closed", True))
        except Exception:
            return False

    kernel32 = ctypes.windll.kernel32
    ATTACH_PARENT_PROCESS = 0xFFFFFFFF  # DWORD(-1)
    # GetStdHandle returns HANDLE; INVALID_HANDLE_VALUE is (HANDLE)-1
    INVALID_HANDLE = ctypes.c_void_p(-1).value
    STD_INPUT_HANDLE = -10
    STD_OUTPUT_HANDLE = -11
    STD_ERROR_HANDLE = -12

    def _std_handle_ok(n_std: int) -> bool:
        try:
            h = kernel32.GetStdHandle(n_std)
            if not h or h == INVALID_HANDLE:
                return False
            # Reject NULL and pseudo-broken handles
            return True
        except Exception:
            return False

    class _NullIO:
        """Quiet sink so print/read never touch invalid OS handles.

        Used for stdin/stdout/stderr when there is no console (Explorer
        double-click under Nuitka ``--windows-console-mode=attach``).
        """
        encoding = "utf-8"
        closed = False
        name = "<null>"
        mode = "r+"

        def write(self, _s: str = "") -> int:
            return 0

        def writelines(self, _lines) -> None:
            return

        def flush(self) -> None:
            return

        def isatty(self) -> bool:
            return False

        def readable(self) -> bool:
            return True

        def writable(self) -> bool:
            return True

        def seekable(self) -> bool:
            return False

        def fileno(self) -> int:
            raise OSError(9, "no console")

        def reconfigure(self, **_kw) -> None:
            return

        def read(self, _n: int = -1) -> str:
            return ""

        def readline(self, _n: int = -1) -> str:
            return ""

        def readlines(self, _hint: int = -1) -> list:
            return []

        def __iter__(self):
            return iter(())

        def close(self) -> None:
            return

        def __enter__(self):
            return self

        def __exit__(self, *_exc) -> None:
            return

    attached = False
    try:
        if kernel32.GetConsoleWindow():
            attached = True
        else:
            # Only attach when a parent console may exist.  Explorer launch:
            # AttachConsole fails → leave streams alone / null sink.
            ok = bool(kernel32.AttachConsole(ATTACH_PARENT_PROCESS))
            if ok and kernel32.GetConsoleWindow():
                attached = True
            else:
                # Ensure we do not keep a half-attached state
                try:
                    if kernel32.GetConsoleWindow() == 0:
                        pass
                except Exception:
                    pass
                attached = False
    except Exception:
        attached = False

    if not attached or not _std_handle_ok(STD_OUTPUT_HANDLE):
        # Double-click / no console: do not open CONOUT$ (→ 句柄无效).
        # Also null stdin — a broken STD_INPUT_HANDLE is what makes child
        # CreateProcess / ffmpeg fail with WinError 6 when stdin is inherited.
        def _replace_if_unusable(name: str) -> None:
            try:
                stream = getattr(sys, name, None)
                usable = False
                if stream is not None and not getattr(stream, "closed", False):
                    try:
                        usable = bool(stream.isatty())
                    except Exception:
                        usable = False
                if not usable:
                    setattr(sys, name, _NullIO())
            except Exception:
                try:
                    setattr(sys, name, _NullIO())
                except Exception:
                    pass

        _replace_if_unusable("stdin")
        _replace_if_unusable("stdout")
        _replace_if_unusable("stderr")
        return False

    def _bind_out(name: str, n_std: int) -> None:
        stream = getattr(sys, name, None)
        try:
            if stream is not None and not stream.closed and stream.isatty():
                return
        except Exception:
            pass
        if not _std_handle_ok(n_std):
            setattr(sys, name, _NullIO())
            return
        try:
            f = open("CONOUT$", "w", encoding="utf-8", errors="replace", buffering=1)
            setattr(sys, name, f)
            try:
                f.reconfigure(line_buffering=True)  # type: ignore[attr-defined]
            except Exception:
                pass
        except Exception:
            setattr(sys, name, _NullIO())

    try:
        _bind_out("stdout", STD_OUTPUT_HANDLE)
        _bind_out("stderr", STD_ERROR_HANDLE)
        return True
    except Exception:
        try:
            sys.stdout = _NullIO()  # type: ignore[assignment]
            sys.stderr = _NullIO()  # type: ignore[assignment]
        except Exception:
            pass
        return False


def log_console(msg: str) -> None:
    """Best-effort print; never raises (including WinError 6 on bad handles)."""
    for stream_name in ("stdout", "stderr"):
        try:
            stream = getattr(sys, stream_name, None)
            if stream is None or getattr(stream, "closed", False):
                continue
            stream.write(msg + "\n")
            stream.flush()
            return
        except Exception:
            continue


def sanitize_std_handles() -> None:
    """Point broken STD_* handles at ``NUL`` so child processes can inherit them.

    Explorer / Nuitka GUI launches often leave STD_INPUT_HANDLE (and sometimes
    OUT/ERR) as INVALID_HANDLE_VALUE.  Python-level ``sys.stdin = NullIO`` does
    **not** fix that — ``CreateProcess`` inherits the OS handles.  Redirecting
    invalid ones to ``NUL`` stops WinError 6 (句柄无效) in ffmpeg and friends
    even when a call site forgets ``stdin=DEVNULL``.

    Safe no-op on non-Windows and when handles are already valid.
    """
    if not _IS_WIN32:
        return
    try:
        kernel32 = ctypes.windll.kernel32
        INVALID_HANDLE = ctypes.c_void_p(-1).value
        STD_INPUT_HANDLE = -10
        STD_OUTPUT_HANDLE = -11
        STD_ERROR_HANDLE = -12
        GENERIC_READ = 0x80000000
        GENERIC_WRITE = 0x40000000
        FILE_SHARE_READ = 0x1
        FILE_SHARE_WRITE = 0x2
        OPEN_EXISTING = 3

        # BOOL GetStdHandle / SetStdHandle — declare once
        try:
            kernel32.GetStdHandle.argtypes = [wintypes.DWORD]
            kernel32.GetStdHandle.restype = wintypes.HANDLE
            kernel32.SetStdHandle.argtypes = [wintypes.DWORD, wintypes.HANDLE]
            kernel32.SetStdHandle.restype = wintypes.BOOL
            kernel32.CreateFileW.argtypes = [
                wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
            ]
            kernel32.CreateFileW.restype = wintypes.HANDLE
        except Exception:
            pass

        def _bad(n_std: int) -> bool:
            try:
                h = kernel32.GetStdHandle(n_std)
                return (not h) or (h == INVALID_HANDLE)
            except Exception:
                return True

        def _nul(access: int):
            try:
                h = kernel32.CreateFileW(
                    "NUL", access,
                    FILE_SHARE_READ | FILE_SHARE_WRITE,
                    None, OPEN_EXISTING, 0, None,
                )
                if not h or h == INVALID_HANDLE:
                    return None
                return h
            except Exception:
                return None

        if _bad(STD_INPUT_HANDLE):
            h = _nul(GENERIC_READ)
            if h is not None:
                try:
                    kernel32.SetStdHandle(STD_INPUT_HANDLE, h)
                except Exception:
                    pass
        if _bad(STD_OUTPUT_HANDLE):
            h = _nul(GENERIC_WRITE)
            if h is not None:
                try:
                    kernel32.SetStdHandle(STD_OUTPUT_HANDLE, h)
                except Exception:
                    pass
        if _bad(STD_ERROR_HANDLE):
            h = _nul(GENERIC_WRITE)
            if h is not None:
                try:
                    kernel32.SetStdHandle(STD_ERROR_HANDLE, h)
                except Exception:
                    pass
    except Exception:
        pass


def subprocess_no_window_kwargs() -> dict:
    """Kwargs so child processes (ffmpeg, …) do not flash a console on Windows.

    Safe empty dict on non-Windows.  Only sets ``creationflags`` / ``startupinfo``
    — **does not** set stdin/stdout/stderr (callers often pass those as
    positional-style kwargs *before* ``**this``, and a trailing unpack would
    overwrite ``stdin=PIPE``).

    For no-console GUI / Nuitka double-click launches, prefer
    :func:`run_hidden` / :func:`popen_hidden` which also default ``stdin`` to
    ``DEVNULL`` (inheriting an invalid STD_INPUT_HANDLE → WinError 6 句柄无效).

    ::

        run_hidden(cmd, capture_output=True, text=True)
        # or, if you must use raw subprocess:
        subprocess.run(
            cmd, stdin=subprocess.DEVNULL, capture_output=True,
            **subprocess_no_window_kwargs(),
        )
    """
    if not _IS_WIN32:
        return {}
    import subprocess
    kw: dict = {}
    # 0x08000000 == CREATE_NO_WINDOW (also on older Python as constant)
    flags = int(getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000))
    kw["creationflags"] = flags
    try:
        si = subprocess.STARTUPINFO()
        si.dwFlags |= int(subprocess.STARTF_USESHOWWINDOW)
        si.wShowWindow = 0  # SW_HIDE
        kw["startupinfo"] = si
    except Exception:
        pass
    return kw


def merge_subprocess_kwargs(**user_kw) -> dict:
    """Merge :func:`subprocess_no_window_kwargs` with caller kwargs.

    If the caller did not set ``stdin`` (or set it to ``None``), force
    ``stdin=DEVNULL``.  Explorer/Nuitka double-click leaves STD_INPUT_HANDLE
    invalid; children that inherit it fail with WinError 6 (句柄无效).
    Explicit ``stdin=PIPE`` / a file object is preserved.
    """
    import subprocess

    kw = subprocess_no_window_kwargs()
    kw.update(user_kw)
    if kw.get("stdin") is None:
        kw["stdin"] = subprocess.DEVNULL
    return kw


def run_hidden(cmd, **kwargs):
    """``subprocess.run`` with no-window flags + safe default stdin."""
    import subprocess
    return subprocess.run(cmd, **merge_subprocess_kwargs(**kwargs))


def popen_hidden(cmd, **kwargs):
    """``subprocess.Popen`` with no-window flags + safe default stdin."""
    import subprocess
    return subprocess.Popen(cmd, **merge_subprocess_kwargs(**kwargs))


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
        # Last (completed, total) sent to SetProgressValue — avoid redundant
        # COM calls that make the Windows taskbar bar stutter / flash.
        self._last_value: tuple[int, int] | None = None

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
        if flags == self._current_state and flags != TBPF_NOPROGRESS:
            return
        hwnd = self._hwnd()
        if hwnd:
            try:
                self._fn_set_state(self._itaskbar3, hwnd, flags)
            except Exception:
                # HWND/COM can go invalid mid-close; never surface to UI
                return
        self._current_state = flags
        if flags == TBPF_NOPROGRESS:
            self._last_value = None

    def _call_set_value(self, completed: int, total: int) -> None:
        if self._fn_set_value is None:
            return
        completed = max(0, int(completed))
        total = max(1, int(total))
        key = (completed, total)
        if key == self._last_value:
            return
        hwnd = self._hwnd()
        if hwnd:
            try:
                self._fn_set_value(
                    self._itaskbar3, hwnd,
                    ctypes.c_ulonglong(completed),
                    ctypes.c_ulonglong(total),
                )
            except Exception:
                return
        self._last_value = key

    # ── Public API ───────────────────────────────────────────────────────

    def show_normal(self, value: int, total: int = 100) -> None:
        """Green progress bar at *value* / *total*."""
        # Set value first when already NORMAL so Windows animates the fill
        # without a state-reset flash each tick.
        if self._current_state != TBPF_NORMAL:
            self._call_set_state(TBPF_NORMAL)
        self._call_set_value(value, total)

    def show_paused(self, value: int, total: int = 100) -> None:
        """Yellow progress bar at *value* / *total*."""
        if self._current_state != TBPF_PAUSED:
            self._call_set_state(TBPF_PAUSED)
        self._call_set_value(value, total)

    def show_error(self, value: int, total: int = 100) -> None:
        """Red progress bar at *value* / *total*."""
        if self._current_state != TBPF_ERROR:
            self._call_set_state(TBPF_ERROR)
        self._call_set_value(value, total)

    def show_indeterminate(self) -> None:
        """Spinning (marquee) green bar — use when total is unknown."""
        self._last_value = None
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

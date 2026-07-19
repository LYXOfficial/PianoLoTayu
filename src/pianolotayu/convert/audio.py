"""Audio loading and Short-Time Fourier Transform (STFT) analysis.

Uses NumPy + soundfile, with a single-pass fallback to the bundled
``imageio-ffmpeg`` binary (same as video export).  No librosa/scipy required.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Callable

import numpy as np

# Extensions soundfile/libsndfile usually opens quickly
_SOUNDFILE_EXTS = {
    ".wav", ".wave", ".flac", ".ogg", ".oga", ".aif", ".aiff",
    ".aifc", ".au", ".snd", ".caf", ".w64", ".rf64", ".mp3",
}
# Prefer ffmpeg immediately (soundfile fails or is useless)
_FFMPEG_FIRST_EXTS = {
    ".m4a", ".aac", ".mp4", ".m4b", ".mov", ".3gp", ".webm",
    ".mkv", ".wma", ".opus",
}

ProgressCb = Callable[[float], None] | None


def load_audio(
    filepath: str,
    sr: int = 22050,
    progress_cb: ProgressCb = None,
) -> tuple[np.ndarray, int]:
    """Load an audio file and convert to mono at *sr* Hz.

    One decode only:

    1. ``soundfile`` for WAV/FLAC/OGG/MP3 when libsndfile supports it, then
       in-process resample if needed (never re-open via ffmpeg after success).
    2. Else one ``ffmpeg`` process: decode + mono + resample to *sr*.

    (An earlier bug fell through to ffmpeg *after* a successful soundfile read
    on long files — that double-decoded and felt much slower than librosa.)
    """
    path = str(filepath)
    if not Path(path).is_file():
        raise FileNotFoundError(path)
    if progress_cb:
        progress_cb(0.0)

    y, out_sr = _read_mono(path, target_sr=int(sr), progress_cb=progress_cb)
    if progress_cb:
        progress_cb(1.0)
    return np.ascontiguousarray(y, dtype=np.float32), int(out_sr)


def _read_mono(
    path: str,
    target_sr: int,
    progress_cb: ProgressCb = None,
) -> tuple[np.ndarray, int]:
    errors: list[str] = []
    ext = Path(path).suffix.lower()

    try_sf = ext in _SOUNDFILE_EXTS and ext not in _FFMPEG_FIRST_EXTS
    if try_sf:
        try:
            if progress_cb:
                progress_cb(0.05)
            y, file_sr = _read_soundfile_mono(path)
            if progress_cb:
                progress_cb(0.55)
            if y.size == 0:
                return y, int(target_sr)
            if int(file_sr) == int(target_sr):
                return y, int(file_sr)
            # Same decode — resample in-process (do NOT re-decode with ffmpeg)
            if progress_cb:
                progress_cb(0.65)
            y2 = _resample(y, file_sr, target_sr)
            if progress_cb:
                progress_cb(0.95)
            return y2, int(target_sr)
        except Exception as exc:
            errors.append(f"soundfile: {type(exc).__name__}: {exc}")

    try:
        return _read_ffmpeg_mono_resampled(
            path, target_sr=target_sr, progress_cb=progress_cb,
        )
    except Exception as exc:
        errors.append(f"ffmpeg: {type(exc).__name__}: {exc}")
        hint = _diagnose_decode_failure(path, str(exc))
        detail = "\n".join(f"  · {e}" for e in errors)
        if hint:
            raise RuntimeError(
                f"{hint}\n文件：{path}\n\n技术细节：\n{detail}"
            ) from exc
        raise RuntimeError(
            f"无法解码音频：{path}\n"
            "已尝试 soundfile 与 imageio-ffmpeg 自带/系统 ffmpeg。\n"
            f"{detail}"
        ) from exc


def _read_soundfile_mono(path: str) -> tuple[np.ndarray, int]:
    import soundfile as sf
    data, file_sr = sf.read(path, always_2d=True, dtype="float32")
    y = data.mean(axis=1) if data.shape[1] > 1 else data[:, 0]
    return np.ascontiguousarray(y, dtype=np.float32), int(file_sr)


def _resample(y: np.ndarray, orig_sr: int, target_sr: int) -> np.ndarray:
    """Resample mono float audio. Prefer soxr if installed, else NumPy."""
    if orig_sr == target_sr or y.size == 0:
        return y
    # Optional high-quality / fast resampler (small wheel, no scipy)
    try:
        import soxr  # type: ignore
        out = soxr.resample(y, orig_sr, target_sr, quality="LQ")
        return np.ascontiguousarray(out, dtype=np.float32)
    except Exception:
        pass
    return _resample_linear(y, orig_sr, target_sr)


def _diagnose_decode_failure(path: str, err: str) -> str | None:
    low = err.lower()
    name = Path(path).name
    if "timed out" in low or "timeout" in low:
        return (
            f"解码超时：{name}\n"
            "文件过大或解码卡住。请换 WAV/FLAC，或检查文件是否损坏。"
        )
    if "moov atom not found" in low or "moov atom" in low:
        return (
            f"音频文件损坏或不完整（缺少 moov 元数据）：{name}\n"
            "常见原因：下载未完成、录制中断、或把非音频内容改成了 .m4a 后缀。\n"
            "请换一份完整文件，或用播放器确认能否正常打开后再试。"
        )
    if "invalid data found when processing input" in low:
        return (
            f"无法识别为有效音频：{name}\n"
            "文件可能已损坏、为空，或扩展名与实际格式不符。"
        )
    if "error opening input" in low and "no such file" in low:
        return f"找不到文件：{path}"
    try:
        if Path(path).is_file() and Path(path).stat().st_size == 0:
            return f"文件是空的（0 字节）：{name}"
    except OSError:
        pass
    return None


def _ffmpeg_exe() -> str:
    from ..gui.win32_utils import get_ffmpeg_exe
    return get_ffmpeg_exe()


def _creationflags() -> int:
    """Deprecated alias — use subprocess_no_window_kwargs()."""
    from ..gui.win32_utils import subprocess_no_window_kwargs
    return int(subprocess_no_window_kwargs().get("creationflags", 0))


def _popen_hidden(cmd, **kwargs):
    """Popen with Windows console hidden + safe default stdin (DEVNULL).

    Double-click Nuitka builds have no console; inheriting an invalid
    STD_INPUT_HANDLE makes CreateProcess/ffmpeg fail with WinError 6.
    """
    from ..gui.win32_utils import popen_hidden
    return popen_hidden(cmd, **kwargs)


def _decode_timeout_s(path: str) -> float:
    try:
        mb = max(0.1, Path(path).stat().st_size / (1024 * 1024))
    except OSError:
        mb = 10.0
    return max(45.0, min(900.0, mb * 8.0))


def _read_ffmpeg_mono_resampled(
    path: str,
    target_sr: int,
    progress_cb: ProgressCb = None,
) -> tuple[np.ndarray, int]:
    """One ffmpeg pass: decode → mono → *target_sr* → float32 PCM pipe."""
    ffmpeg = _ffmpeg_exe()
    if progress_cb:
        progress_cb(0.05)

    cmd = [
        ffmpeg,
        "-hide_banner",
        "-nostdin",  # do not touch console stdin (GUI / no-console launch)
        "-v", "error",
        "-i", path,
        "-vn",
        "-ac", "1",
        "-ar", str(int(target_sr)),
        "-f", "f32le",
        "-acodec", "pcm_f32le",
        "pipe:1",
    ]
    timeout = _decode_timeout_s(path)
    try:
        fsize = max(1, Path(path).stat().st_size)
    except OSError:
        fsize = 1
    est_pcm = max(fsize * 4, 512 * 1024)

    proc = _popen_hidden(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert proc.stdout is not None
    chunks: list[bytes] = []
    n_read = 0
    t0 = time.monotonic()
    stderr = b""
    last_prog = 0.0
    try:
        while True:
            if time.monotonic() - t0 > timeout:
                proc.kill()
                try:
                    proc.communicate(timeout=5)
                except Exception:
                    pass
                raise TimeoutError(
                    f"ffmpeg 解码超时（>{int(timeout)}s）：{path}"
                )
            block = proc.stdout.read(512 * 1024)
            if not block:
                break
            chunks.append(block)
            n_read += len(block)
            if progress_cb:
                frac = min(0.95, 0.05 + 0.90 * (n_read / est_pcm))
                # Throttle UI signals (~25 Hz) — don't flood the event loop
                if frac - last_prog >= 0.02 or frac >= 0.95:
                    progress_cb(frac)
                    last_prog = frac
        try:
            _, stderr = proc.communicate(timeout=30)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate()
            raise TimeoutError(f"ffmpeg 结束超时：{path}") from None
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass
        raise

    if proc.returncode not in (0, None) and proc.returncode != 0:
        err = (stderr or b"").decode("utf-8", errors="replace").strip()
        raise RuntimeError(
            f"ffmpeg 解码失败 (code {proc.returncode})："
            f"{err[-600:] or '无 stderr'}"
        )

    raw = b"".join(chunks)
    if not raw:
        err = (stderr or b"").decode("utf-8", errors="replace").strip()
        if err:
            raise RuntimeError(f"ffmpeg 未输出音频数据：{err[-500:]}")
        return np.zeros(0, dtype=np.float32), int(target_sr)

    n = len(raw) - (len(raw) % 4)
    samples = np.frombuffer(raw[:n], dtype="<f4").copy()
    return samples, int(target_sr)


def _resample_linear(y: np.ndarray, orig_sr: int, target_sr: int) -> np.ndarray:
    """Linear resampler (fallback when soxr is not installed)."""
    if orig_sr == target_sr or y.size == 0:
        return y
    n_out = max(1, int(round(y.size * float(target_sr) / float(orig_sr))))
    # np.interp on float32 views — avoid float64 copies of multi-minute tracks
    x_old = np.linspace(0.0, 1.0, num=y.size, endpoint=False, dtype=np.float64)
    x_new = np.linspace(0.0, 1.0, num=n_out, endpoint=False, dtype=np.float64)
    return np.interp(x_new, x_old, y).astype(np.float32, copy=False)


def compute_stft(
    signal: np.ndarray,
    sr: int,
    n_fft: int = 4096,
    hop_length: int = 512,
    window: str = "hann",
    progress_cb: ProgressCb = None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Compute the STFT magnitude spectrogram in dB.

    Fast path: one bulk ``rfft`` (same shape of work as old librosa-style STFT).
    Progress path: a few large chunks so the bar moves without the old
    many-small-chunk overhead that made this stage feel slower than before.
    """
    # Prefer scipy.fft when present (often faster); fall back to numpy
    try:
        from scipy.fft import rfft as _rfft  # type: ignore
    except Exception:
        _rfft = np.fft.rfft

    y = np.asarray(signal, dtype=np.float32, order="C")
    if y.ndim != 1:
        y = np.ascontiguousarray(y.reshape(-1), dtype=np.float32)

    pad = n_fft // 2
    n_bins = n_fft // 2 + 1
    if y.size == 0:
        empty = np.full((n_bins, 0), -80.0, dtype=np.float32)
        return empty, _fft_frequencies(sr, n_fft), np.zeros(0, dtype=np.float64)

    if progress_cb:
        progress_cb(0.02)

    y_pad = np.pad(y, pad_width=pad, mode="reflect")
    win = _get_window(window, n_fft).astype(np.float32, copy=False)

    n_samples = y_pad.size
    if n_samples < n_fft:
        y_pad = np.pad(y_pad, (0, n_fft - n_samples), mode="constant")
        n_samples = y_pad.size
    n_frames = 1 + (n_samples - n_fft) // hop_length

    # Framing without copy
    step = y_pad.strides[0]
    frames = np.lib.stride_tricks.as_strided(
        y_pad,
        shape=(n_frames, n_fft),
        strides=(hop_length * step, step),
        writeable=False,
    )

    # ── Bulk FFT when no progress needed, or short enough ───────────────
    # Chunking adds Python/alloc overhead; only use it for long jobs with UI.
    use_chunks = (
        progress_cb is not None
        and n_frames > 2000
    )

    if not use_chunks:
        if progress_cb:
            progress_cb(0.15)
        # One multiply + one rfft — matches pre-refactor cost structure
        windowed = frames * win
        if progress_cb:
            progress_cb(0.35)
        spec = _rfft(windowed, n=n_fft, axis=1)
        if progress_cb:
            progress_cb(0.75)
        mag = np.abs(spec, dtype=np.float32).T  # (n_bins, n_frames)
        del spec, windowed
        if progress_cb:
            progress_cb(0.88)
        peak = float(mag.max()) if mag.size else 1.0
        D_db = _amplitude_to_db(mag, ref=peak if peak > 0 else 1.0)
        if progress_cb:
            progress_cb(1.0)
    else:
        # ~12 large chunks: enough for a moving bar, low overhead
        n_chunks = 12
        chunk = max(1, (n_frames + n_chunks - 1) // n_chunks)
        mag = np.empty((n_bins, n_frames), dtype=np.float32)
        peak = 0.0
        last_prog = 0.0
        for i, c0 in enumerate(range(0, n_frames, chunk)):
            c1 = min(n_frames, c0 + chunk)
            block = frames[c0:c1] * win
            spec = _rfft(block, n=n_fft, axis=1)
            m = np.abs(spec, dtype=np.float32).T
            mag[:, c0:c1] = m
            bp = float(m.max()) if m.size else 0.0
            if bp > peak:
                peak = bp
            if progress_cb:
                frac = min(0.90, 0.10 + 0.80 * (c1 / n_frames))
                if frac - last_prog >= 0.05 or c1 >= n_frames:
                    progress_cb(frac)
                    last_prog = frac
                    time.sleep(0)
        if progress_cb:
            progress_cb(0.92)
        D_db = _amplitude_to_db(mag, ref=peak if peak > 0 else 1.0)
        if progress_cb:
            progress_cb(1.0)

    freqs = _fft_frequencies(sr, n_fft)
    times = (np.arange(n_frames, dtype=np.float64) * hop_length) / float(sr)
    return D_db, freqs, times


def _get_window(name: str, n: int) -> np.ndarray:
    key = (name or "hann").lower()
    if key in ("hann", "hanning"):
        if n <= 1:
            return np.ones(n, dtype=np.float64)
        return 0.5 - 0.5 * np.cos(2.0 * np.pi * np.arange(n) / n)
    raise ValueError(f"不支持的窗函数：{name!r}（仅 hann）")


def _fft_frequencies(sr: int, n_fft: int) -> np.ndarray:
    return np.fft.rfftfreq(n_fft, d=1.0 / float(sr)).astype(np.float64)


def _amplitude_to_db(
    mag: np.ndarray,
    ref: float = 1.0,
    amin: float = 1e-5,
    top_db: float = 80.0,
) -> np.ndarray:
    mag = np.asarray(mag, dtype=np.float32)
    ref = abs(float(ref)) if ref else 1.0
    log_spec = 20.0 * np.log10(np.maximum(amin, mag))
    log_spec -= np.float32(20.0 * np.log10(max(amin, ref)))
    if top_db is not None and mag.size:
        log_spec = np.maximum(log_spec, float(np.max(log_spec)) - top_db)
    return log_spec

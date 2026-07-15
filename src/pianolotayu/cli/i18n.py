"""Internationalisation support — auto-detects system language."""

from __future__ import annotations

import locale
import os
import sys


def _detect_lang() -> str:
    """Detect the user's language from the system environment.

    Checks (in order):
      1. ``LANG`` / ``LC_ALL`` / ``LC_MESSAGES`` environment variables.
      2. ``locale.getdefaultlocale()`` (cross-platform).
      3. Falls back to ``"en"``.

    Returns:
        ``"zh"`` for any Chinese variant (zh-CN, zh-TW, zh-HK, …),
        ``"en"`` for everything else.
    """
    raw = ""

    # 1) POSIX locale env vars
    for var in ("LC_ALL", "LC_MESSAGES", "LANG"):
        val = os.environ.get(var, "")
        if val:
            raw = val
            break

    # 2) Cross-platform fallback (Windows / macOS)
    if not raw:
        try:
            raw = locale.getdefaultlocale()[0] or ""
        except (ValueError, locale.Error):
            pass

    # Normalise
    raw = raw.lower().replace("_", "-")

    if raw.startswith(("zh", "zh-cn", "zh-tw", "zh-hk", "zh-sg")):
        return "zh"
    return "en"


_LANG = _detect_lang()

# ── ANSI colour helpers ───────────────────────────────────────────────────
_RED = "\033[91m"
_GREEN = "\033[92m"
_RESET = "\033[0m"


def red(text: str) -> str:
    """Wrap *text* in ANSI red escape codes."""
    return f"{_RED}{text}{_RESET}"


def green(text: str) -> str:
    """Wrap *text* in ANSI green escape codes."""
    return f"{_GREEN}{text}{_RESET}"


def get(key: str) -> str:
    """Return the translation for *key* in the current language."""
    messages = _ZH if _LANG == "zh" else _EN
    return messages.get(key, _EN.get(key, key))


def current_lang() -> str:
    """Return the current language code (``"zh"`` or ``"en"``)."""
    return _LANG


def override_lang(lang: str) -> None:
    """Override the auto-detected language.

    Args:
        lang: ``"zh"``, ``"en"``, or any value accepted by :func:`_normalise`.
    """
    global _LANG
    lang = _normalise(lang)
    if lang in ("zh", "en"):
        _LANG = lang


def _normalise(raw: str) -> str:
    raw = raw.lower().replace("_", "-")
    if raw.startswith(("zh", "zh-cn", "zh-tw", "zh-hk", "zh-sg")):
        return "zh"
    if raw.startswith("en"):
        return "en"
    return raw


# ── English (source-of-truth) ────────────────────────────────────────────
_EN = {
    # CLI description
    "cli.description": (
        "Convert audio files (MP3/WAV/FLAC/OGG/M4A) to piano MIDI via Fourier Transform."
    ),
    # Positional
    "cli.input": "Input audio file (.mp3, .wav, .flac, .ogg, .m4a, etc.)",
    # Options
    "cli.output": "Output MIDI file path (default: <input_stem>.mid)",
    "cli.sr": "Sample rate for analysis in Hz (default: 22050)",
    "cli.n-fft": "FFT window size (default: 4096, ~5.4 Hz resolution at 22.05kHz)",
    "cli.hop-length": "Hop length between STFT frames (default: 256, ~12ms at 22.05kHz)",
    "cli.threshold": "Peak detection threshold in dB below frame maximum (default: 20)",
    "cli.max-notes": "Maximum simultaneous notes per frame (default: 16)",
    "cli.min-duration": "Minimum note duration in milliseconds (default: 30)",
    "cli.dynamic-range": "Dynamic range for velocity mapping in dB (default: 60)",
    "cli.no-piano-limit": (
        "Disable piano-range octave folding. Frequencies outside the "
        "piano range (A0–C8) are kept at their raw MIDI value (0–127) "
        "instead of being octave-folded into range."
    ),
    "cli.high-damp": (
        "High-frequency velocity damping. 0=off, 0.35=gentle, 0.6=strong. "
        "Reduces velocity of notes above middle C to avoid piercing highs."
    ),
    "cli.mid-boost": (
        "Midrange velocity boost for vocal/piano presence. "
        "0=off, 0.6=moderate, 1.2=strong. Boosts notes around C5."
    ),
    # Help
    "cli.help": "Show this help message and exit.",
    # Language option
    "cli.lang": "Command Interface language: \"zh\" (Chinese) or \"en\" (English). "
                "Default: auto-detect from system locale.",
    "cli.version": "Show version number and exit.",
    # argparse error messages
    "error.missing_input": "the following arguments are required: input",
    "error.unrecognized": "unrecognized arguments: {args}",
    "error.invalid_choice": "argument {arg}: invalid choice: '{value}' (choose from {choices})",
    # Runtime messages
    "error.file_not_found": "Error: Input file not found: {path}",
    "status.loading": "Loading: {path}",
    "status.duration": "  Duration: {duration:.1f}s, Sample rate: {sr} Hz, Samples: {samples}",
    "status.stft": "Computing STFT...",
    "status.stft_info": "  Frequency bins: {bins}, Time frames: {frames}",
    "status.peaks": "Detecting peaks and mapping to MIDI notes...",
    "status.total_peaks": "  Total peak-note detections: {count}",
    "status.tracking": "Tracking notes and building MIDI...",
    "status.midi_notes": "  MIDI notes written: {count}",
    "status.saving": "Saving: {path}",
    "status.done": "Done!",
}

# ── 中文 ──────────────────────────────────────────────────────────────────
_ZH = {
    "cli.description": "基于傅里叶变换将音频文件（MP3/WAV/FLAC/OGG/M4A）转换为钢琴 MIDI。",
    "cli.input": "输入音频文件（.mp3、.wav、.flac、.ogg、.m4a 等）",
    "cli.output": "MIDI 输出路径（默认：<输入文件名>.mid）",
    "cli.sr": "分析采样率，单位 Hz（默认：22050）",
    "cli.n-fft": "FFT 窗口大小（默认：4096，在 22.05kHz 下约 5.4 Hz 分辨率）",
    "cli.hop-length": "STFT 帧间跳跃采样数（默认：256，在 22.05kHz 下约 12ms）",
    "cli.threshold": "峰值检测阈值，低于帧最大值的 dB 数（默认：20）",
    "cli.max-notes": "每帧最多同时音符数（默认：16）",
    "cli.min-duration": "最短音符时长，单位毫秒（默认：30）",
    "cli.dynamic-range": "力度映射动态范围，单位 dB（默认：60）",
    "cli.no-piano-limit": (
        "禁用钢琴音域八度折叠。超出钢琴音域（A0–C8）的频率将保留原始 "
        "MIDI 值（0–127），而非按八度折叠入音域内。"
    ),
    "cli.high-damp": (
        "高频力度衰减。0=关闭，0.35=温和，0.6=强力。"
        "降低中央 C 以上音符的力度，避免高音刺耳。"
    ),
    "cli.mid-boost": (
        "中频力度增强，突出人声/钢琴。0=关闭，0.6=适度，1.2=强力。"
        "增强 C5 附近音符的力度。"
    ),
    "cli.help": "显示此帮助信息并退出。",
    "cli.lang": "命令行语言：\"zh\"（中文）或 \"en\"（英语）。"
                "默认：根据系统区域自动检测。",
    "cli.version": "显示版本号并退出。",
    "error.missing_input": "请提供输入文件",
    "error.unrecognized": "无法识别的参数：{args}",
    "error.invalid_choice": "参数 {arg}：无效选项 \"{value}\"（可选：{choices}）",
    "error.file_not_found": "错误：输入文件不存在：{path}",
    "status.loading": "正在加载：{path}",
    "status.duration": "  时长：{duration:.1f} 秒，采样率：{sr} Hz，采样数：{samples}",
    "status.stft": "正在计算短时傅里叶变换…",
    "status.stft_info": "  频率 bin：{bins}，时间帧：{frames}",
    "status.peaks": "正在检测峰值并映射到 MIDI 音符…",
    "status.total_peaks": "  峰值音符检测总数：{count}",
    "status.tracking": "正在跟踪音符并生成 MIDI…",
    "status.midi_notes": "  已写入 MIDI 音符：{count}",
    "status.saving": "正在保存：{path}",
    "status.done": "完成！",
}


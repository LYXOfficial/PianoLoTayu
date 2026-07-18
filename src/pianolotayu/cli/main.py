"""Command-line interface for PianoLoTayu."""

import argparse
import sys
from pathlib import Path

from ..config import DEFAULTS, VERSION
from ..convert.audio import load_audio, compute_stft
from ..convert.analysis import analyze_frames
from ..convert.midi_writer import create_midi, save_midi
from .i18n import get, override_lang, red, green


# ── Pre-scan argv for -l / --lang so that --help is translated ──────────
def _pre_scan_lang(argv: list[str] | None) -> None:
    """Look for ``-l`` / ``--lang`` in *argv* and apply the override early."""
    args = argv if argv is not None else sys.argv[1:]
    it = iter(args)
    for token in it:
        if token in ("-l", "--lang"):
            try:
                val = next(it)
            except StopIteration:
                return
            if not val.startswith("-"):
                override_lang(val)
                return
        elif token.startswith("--lang="):
            override_lang(token.split("=", 1)[1])
            return


class _TransArgParser(argparse.ArgumentParser):
    """ArgumentParser subclass that translates built-in error messages."""

    def error(self, message: str) -> None:
        # Translate known argparse patterns
        if "the following arguments are required" in message:
            # Extract the argument name(s)
            message = get("error.missing_input")
        elif message.startswith("unrecognized arguments:"):
            args_part = message[len("unrecognized arguments: "):]
            message = get("error.unrecognized").format(args=args_part)
        elif "invalid choice:" in message:
            # "argument -l/--lang: invalid choice: 'xx' (choose from 'zh', 'en')"
            import re
            m = re.match(
                r"argument ([\w/-]+): invalid choice: '(.+?)' \(choose from (.+)\)",
                message,
            )
            if m:
                message = get("error.invalid_choice").format(
                    arg=m.group(1), value=m.group(2), choices=m.group(3),
                )
        self.print_usage(sys.stderr)
        print(red(f"{self.prog}: error: {message}"), file=sys.stderr)
        self.exit(2)


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser."""
    parser = _TransArgParser(
        prog="pianolotayu",
        description=get("cli.description"),
        add_help=False,
    )

    parser.add_argument(
        "-h", "--help",
        action="help",
        help=get("cli.help"),
    )

    parser.add_argument(
        "-v", "--version",
        action="version",
        version=f"pianolotayu {VERSION}",
        help=get("cli.version"),
    )

    parser.add_argument(
        "input",
        type=str,
        help=get("cli.input"),
    )

    parser.add_argument(
        "-o", "--output",
        type=str,
        default=None,
        help=get("cli.output"),
    )

    parser.add_argument(
        "-l", "--lang",
        type=str,
        default=None,
        choices=("zh", "en"),
        help=get("cli.lang"),
    )

    parser.add_argument(
        "--sr",
        type=int,
        default=DEFAULTS["sr"],
        help=get("cli.sr"),
    )

    parser.add_argument(
        "--n-fft",
        type=int,
        default=DEFAULTS["n_fft"],
        help=get("cli.n-fft"),
    )

    parser.add_argument(
        "--hop-length",
        type=int,
        default=DEFAULTS["hop_length"],
        help=get("cli.hop-length"),
    )

    parser.add_argument(
        "--threshold",
        type=float,
        default=DEFAULTS["threshold"],
        help=get("cli.threshold"),
    )

    parser.add_argument(
        "--max-notes",
        type=int,
        default=DEFAULTS["max_notes"],
        help=get("cli.max-notes"),
    )

    parser.add_argument(
        "--min-duration",
        type=float,
        default=DEFAULTS["min_duration"],
        help=get("cli.min-duration"),
    )

    parser.add_argument(
        "--dynamic-range",
        type=float,
        default=DEFAULTS["dynamic_range"],
        help=get("cli.dynamic-range"),
    )

    parser.add_argument(
        "--no-piano-limit",
        action="store_true",
        help=get("cli.no-piano-limit"),
    )

    parser.add_argument(
        "--high-damp",
        type=float,
        default=DEFAULTS["high_damp"],
        help=get("cli.high-damp"),
    )

    parser.add_argument(
        "--mid-boost",
        type=float,
        default=DEFAULTS["mid_boost"],
        help=get("cli.mid-boost"),
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    """Entry point for the pianolotayu CLI.

    Args:
        argv: Command-line arguments (uses sys.argv if None).

    Returns:
        Exit code (0 for success, 1 for error).
    """
    # Pre-scan for -l / --lang so parser help text uses the right language.
    _pre_scan_lang(argv)

    parser = build_parser()
    args = parser.parse_args(argv)

    # Apply language override (the pre-scan may have already set it —
    # this re-applies in case --lang was parsed inline).
    if args.lang:
        override_lang(args.lang)

    input_path = Path(args.input)
    if not input_path.exists():
        print(red(get("error.file_not_found").format(path=args.input)), file=sys.stderr)
        return 1

    if args.output:
        output_path = Path(args.output)
    else:
        output_path = input_path.with_suffix(".mid")

    print(get("status.loading").format(path=input_path))
    signal, sr = load_audio(str(input_path), sr=args.sr)
    duration_s = len(signal) / sr
    print(get("status.duration").format(
        duration=duration_s, sr=sr, samples=len(signal),
    ))

    print(get("status.stft"))
    D_db, freqs, times = compute_stft(
        signal, sr,
        n_fft=args.n_fft,
        hop_length=args.hop_length,
    )
    print(get("status.stft_info").format(
        bins=D_db.shape[0], frames=D_db.shape[1],
    ))

    print(get("status.peaks"))
    frame_notes = analyze_frames(
        D_db, freqs, times, sr, args.hop_length,
        threshold_db=args.threshold,
        max_notes=args.max_notes,
        dynamic_range_db=args.dynamic_range,
        piano_limit=not args.no_piano_limit,
        high_damp=args.high_damp,
        mid_boost=args.mid_boost,
    )

    total_notes_detected = sum(len(fn) for fn in frame_notes)
    print(get("status.total_peaks").format(count=total_notes_detected))

    print(get("status.tracking"))
    midi = create_midi(
        frame_notes, sr, args.hop_length,
        min_duration_ms=args.min_duration,
    )
    print(get("status.midi_notes").format(count=len(midi.instruments[0].notes)))

    print(get("status.saving").format(path=output_path))
    save_midi(midi, str(output_path))

    print(green(get("status.done")))
    return 0

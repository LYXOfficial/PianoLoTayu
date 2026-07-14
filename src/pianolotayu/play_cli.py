"""CLI for MIDI preview playback using sine wave synthesis."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .synthesizer import render_and_play, render_to_wav


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser for pianolotayu-play."""
    parser = argparse.ArgumentParser(
        prog="pianolotayu-play",
        description="Preview a MIDI file using sine wave synthesis.",
    )

    parser.add_argument(
        "input",
        type=str,
        help="Input MIDI file (.mid)",
    )

    parser.add_argument(
        "-o", "--output",
        type=str,
        default=None,
        help="Save rendered audio to WAV instead of (or in addition to) playback",
    )

    parser.add_argument(
        "--sr",
        type=int,
        default=44100,
        help="Output sample rate in Hz (default: 44100)",
    )

    parser.add_argument(
        "--no-play",
        action="store_true",
        help="Only save to WAV, don't play (requires --output)",
    )

    parser.add_argument(
        "--volume",
        type=float,
        default=0.85,
        help="Master volume 0–1 (default: 0.85)",
    )

    parser.add_argument(
        "--attack",
        type=float,
        default=0.008,
        help="ADSR attack time in seconds (default: 0.008)",
    )

    parser.add_argument(
        "--decay",
        type=float,
        default=0.06,
        help="ADSR decay time in seconds (default: 0.06)",
    )

    parser.add_argument(
        "--sustain",
        type=float,
        default=0.70,
        help="ADSR sustain level 0–1 (default: 0.70)",
    )

    parser.add_argument(
        "--release",
        type=float,
        default=0.08,
        help="ADSR release time in seconds (default: 0.08)",
    )

    parser.add_argument(
        "--high-damp",
        type=float,
        default=0,
        help="High-frequency damping (0=off, 0.35=gentle, 0.6=strong). "
             "Reduces piercing highs. Default: 0.35",
    )

    parser.add_argument(
        "--mid-boost",
        type=float,
        default=0,
        help="Midrange boost for vocal presence (0=off, 0.6=+4dB, 1.2=+8dB). "
             "Boosts ~C3–C6 range. Default: 0",
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    """Entry point for the pianolotayu-play CLI."""
    parser = build_parser()
    args = parser.parse_args(argv)

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"Error: MIDI file not found: {args.input}", file=sys.stderr)
        return 1

    if not input_path.suffix.lower() in (".mid", ".midi"):
        print(f"Warning: input does not have .mid extension: {args.input}",
              file=sys.stderr)

    # If --output is specified, render to WAV
    if args.output:
        render_to_wav(
            input_path,
            args.output,
            sr=args.sr,
            master_volume=args.volume,
            high_damp=args.high_damp,
            mid_boost=args.mid_boost,
        )

    # Play unless --no-play
    if not args.no_play:
        render_and_play(
            input_path,
            sr=args.sr,
            master_volume=args.volume,
            high_damp=args.high_damp,
            mid_boost=args.mid_boost,
        )
    elif not args.output:
        print("Error: --no-play requires --output", file=sys.stderr)
        return 1

    return 0

if __name__ == "__main__":
    sys.exit(main())
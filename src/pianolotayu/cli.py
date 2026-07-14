"""Command-line interface for PianoLoTayu."""

import argparse
import sys
from pathlib import Path

from .audio import load_audio, compute_stft
from .analysis import analyze_frames
from .midi_writer import create_midi, save_midi


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser."""
    parser = argparse.ArgumentParser(
        prog="pianolotayu",
        description="Convert audio files (MP3/WAV) to piano MIDI via Fourier Transform.",
    )

    parser.add_argument(
        "input",
        type=str,
        help="Input audio file (.mp3, .wav, .flac, etc.)",
    )

    parser.add_argument(
        "-o", "--output",
        type=str,
        default=None,
        help="Output MIDI file path (default: <input_stem>.mid)",
    )

    parser.add_argument(
        "--sr",
        type=int,
        default=22050,
        help="Sample rate for analysis in Hz (default: 22050)",
    )

    parser.add_argument(
        "--n-fft",
        type=int,
        default=4096,
        help="FFT window size (default: 4096, ~5.4 Hz resolution at 22.05kHz)",
    )

    parser.add_argument(
        "--hop-length",
        type=int,
        default=256,
        help="Hop length between STFT frames (default: 512, ~23ms at 22.05kHz)",
    )

    parser.add_argument(
        "--threshold",
        type=float,
        default=20.0,
        help="Peak detection threshold in dB below frame maximum (default: 30)",
    )

    parser.add_argument(
        "--max-notes",
        type=int,
        default=16,
        help="Maximum simultaneous notes per frame (default: 16)",
    )

    parser.add_argument(
        "--min-duration",
        type=float,
        default=30.0,
        help="Minimum note duration in milliseconds (default: 50)",
    )

    parser.add_argument(
        "--dynamic-range",
        type=float,
        default=60.0,
        help="Dynamic range for velocity mapping in dB (default: 60)",
    )

    parser.add_argument(
        "--no-piano-limit",
        action="store_true",
        help="Disable piano-range octave folding. Frequencies outside the "
             "piano range (A0–C8) are kept at their raw MIDI value (0–127) "
             "instead of being octave-folded into range.",
    )

    parser.add_argument(
        "--play",
        action="store_true",
        help="Preview the output MIDI with sine wave synthesis after conversion",
    )

    parser.add_argument(
        "--high-damp",
        type=float,
        default=0,
        help="High-frequency velocity damping. 0=off, 0.35=gentle, 0.6=strong. "
             "Reduces velocity of notes above middle C to avoid piercing highs.",
    )

    parser.add_argument(
        "--mid-boost",
        type=float,
        default=0,
        help="Midrange velocity boost for vocal/piano presence. "
             "0=off, 0.6=moderate, 1.2=strong. Boosts notes around C5.",
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    """Entry point for the pianolotayu CLI.

    Args:
        argv: Command-line arguments (uses sys.argv if None).

    Returns:
        Exit code (0 for success, 1 for error).
    """
    parser = build_parser()
    args = parser.parse_args(argv)

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"Error: Input file not found: {args.input}", file=sys.stderr)
        return 1

    # Determine output path
    if args.output:
        output_path = Path(args.output)
    else:
        output_path = input_path.with_suffix(".mid")

    print(f"Loading: {input_path}")
    signal, sr = load_audio(str(input_path), sr=args.sr)
    duration_s = len(signal) / sr
    print(f"  Duration: {duration_s:.1f}s, Sample rate: {sr} Hz, "
          f"Samples: {len(signal)}")

    print("Computing STFT...")
    D_db, freqs, times = compute_stft(
        signal, sr,
        n_fft=args.n_fft,
        hop_length=args.hop_length,
    )
    print(f"  Frequency bins: {D_db.shape[0]}, Time frames: {D_db.shape[1]}")

    print("Detecting peaks and mapping to MIDI notes...")
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
    print(f"  Total peak-note detections: {total_notes_detected}")

    print("Tracking notes and building MIDI...")
    midi = create_midi(
        frame_notes, sr, args.hop_length,
        min_duration_ms=args.min_duration,
    )
    print(f"  MIDI notes written: {len(midi.instruments[0].notes)}")

    print(f"Saving: {output_path}")
    save_midi(midi, str(output_path))

    print("Done!")

    # Preview playback
    if args.play:
        from .synthesizer import render_and_play
        print()
        render_and_play(str(output_path),
                        high_damp=args.high_damp,
                        mid_boost=args.mid_boost)

    return 0
if __name__ == "__main__":
    sys.exit(main())
"""MIDI file creation with note-onset/offset tracking."""

import numpy as np
import pretty_midi


def _frame_to_time(frame_idx: int, hop_length: int, sr: int) -> float:
    """Convert a frame index to time in seconds."""
    return frame_idx * hop_length / sr


def create_midi(
    frame_notes: list[list[tuple[int, int]]],
    sr: int,
    hop_length: int,
    min_duration_ms: float = 50.0,
    hysteresis_frames: int = 2,
    progress_cb=None,
) -> pretty_midi.PrettyMIDI:
    """Build a PrettyMIDI object from per-frame note detections.

    Tracks note state across frames. A note is turned on when it first
    appears after being absent, and turned off when it has been absent
    for *hysteresis_frames* consecutive frames (to avoid flickering).
    Notes shorter than *min_duration_ms* are dropped.

    Args:
        frame_notes: List-of-lists from analyze_frames().
        sr: Sample rate used for STFT.
        hop_length: Hop length used for STFT.
        min_duration_ms: Minimum note duration; shorter notes are skipped.
        hysteresis_frames: Consecutive absent frames before note-off.
        progress_cb: Optional ``callable(fraction: float)`` with
            *fraction* in ``[0.0, 1.0]`` (throttled ~1%% steps).

    Returns:
        A pretty_midi.PrettyMIDI object with one Instrument (piano).
    """
    pm = pretty_midi.PrettyMIDI(initial_tempo=120.0)
    instrument = pretty_midi.Instrument(
        program=0,
        is_drum=False,
        name="PianoLoTayu",
    )

    n_frames = len(frame_notes)
    if n_frames == 0:
        if progress_cb is not None:
            progress_cb(1.0)
        pm.instruments.append(instrument)
        return pm

    active_notes: dict[int, tuple[float, int, int]] = {}
    min_duration_s = min_duration_ms / 1000.0
    inv_sr = hop_length / float(sr)

    import time
    t0 = time.monotonic()
    last_prog_t = t0
    last_frac = -1.0
    if progress_cb is not None:
        progress_cb(0.0)

    for t in range(n_frames):
        current_time = t * inv_sr
        notes_t = frame_notes[t]
        current_midi_set = {note[0] for note in notes_t}

        current_velocities: dict[int, int] = {}
        for midi, vel in notes_t:
            prev = current_velocities.get(midi)
            if prev is None or vel > prev:
                current_velocities[midi] = vel

        ended_notes: list[int] = []
        for midi in list(active_notes.keys()):
            start_time, velocity, absent_count = active_notes[midi]
            if midi in current_midi_set:
                new_vel = current_velocities.get(midi, velocity)
                active_notes[midi] = (start_time, max(velocity, new_vel), 0)
            else:
                absent_count += 1
                if absent_count >= hysteresis_frames:
                    ended_notes.append(midi)
                else:
                    active_notes[midi] = (start_time, velocity, absent_count)

        for midi in ended_notes:
            start_time, velocity, _ = active_notes.pop(midi)
            duration = current_time - start_time
            if duration >= min_duration_s:
                instrument.notes.append(pretty_midi.Note(
                    velocity=velocity, pitch=midi,
                    start=start_time, end=current_time,
                ))

        for midi in current_midi_set:
            if midi not in active_notes:
                active_notes[midi] = (current_time, current_velocities[midi], 0)

        if progress_cb is not None:
            now = time.monotonic()
            if now - last_prog_t >= 0.05 or t + 1 == n_frames:
                frac = (t + 1) / n_frames
                if frac - last_frac >= 0.005 or t + 1 == n_frames:
                    progress_cb(frac)
                    last_frac = frac
                    last_prog_t = now

    end_time = n_frames * inv_sr
    for midi, (start_time, velocity, _) in active_notes.items():
        duration = end_time - start_time
        if duration >= min_duration_s:
            instrument.notes.append(pretty_midi.Note(
                velocity=velocity, pitch=midi,
                start=start_time, end=end_time,
            ))

    pm.instruments.append(instrument)
    if progress_cb is not None:
        progress_cb(1.0)
    return pm


def save_midi(pm: pretty_midi.PrettyMIDI, filepath: str) -> None:
    """Write a PrettyMIDI object to a .mid file.

    Args:
        pm: The PrettyMIDI object to save.
        filepath: Output path (should end with .mid or .midi).
    """
    pm.write(filepath)

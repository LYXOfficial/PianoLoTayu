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

    Returns:
        A pretty_midi.PrettyMIDI object with one Instrument (piano).
    """
    pm = pretty_midi.PrettyMIDI(initial_tempo=120.0)
    instrument = pretty_midi.Instrument(
        program=0,  # Acoustic Grand Piano
        is_drum=False,
        name="PianoLoTayu",
    )

    n_frames = len(frame_notes)
    if n_frames == 0:
        pm.instruments.append(instrument)
        return pm

    # Track active notes and their absence counters
    # active_notes[midi] = (start_time, velocity, absent_count)
    active_notes: dict[int, tuple[float, int, int]] = {}
    min_duration_s = min_duration_ms / 1000.0

    for t in range(n_frames):
        current_time = _frame_to_time(t, hop_length, sr)
        current_midi_set = {note[0] for note in frame_notes[t]}

        # Build a dict of current velocities (take max if duplicate)
        current_velocities: dict[int, int] = {}
        for midi, vel in frame_notes[t]:
            if midi not in current_velocities or vel > current_velocities[midi]:
                current_velocities[midi] = vel

        # Check existing active notes: are they still present?
        ended_notes: list[int] = []
        for midi in list(active_notes.keys()):
            start_time, velocity, absent_count = active_notes[midi]
            if midi in current_midi_set:
                # Still active — reset absence counter
                # Update velocity if current is louder
                new_vel = current_velocities.get(midi, velocity)
                active_notes[midi] = (start_time, max(velocity, new_vel), 0)
            else:
                # Absent this frame
                absent_count += 1
                if absent_count >= hysteresis_frames:
                    ended_notes.append(midi)
                else:
                    active_notes[midi] = (start_time, velocity, absent_count)

        # End notes that have been absent long enough
        for midi in ended_notes:
            start_time, velocity, _ = active_notes.pop(midi)
            duration = current_time - start_time
            if duration >= min_duration_s:
                note = pretty_midi.Note(
                    velocity=velocity,
                    pitch=midi,
                    start=start_time,
                    end=current_time,
                )
                instrument.notes.append(note)

        # Start new notes
        for midi in current_midi_set:
            if midi not in active_notes:
                velocity = current_velocities[midi]
                active_notes[midi] = (current_time, velocity, 0)

    # End all remaining active notes at the last frame
    end_time = _frame_to_time(n_frames, hop_length, sr)
    for midi, (start_time, velocity, _) in active_notes.items():
        duration = end_time - start_time
        if duration >= min_duration_s:
            note = pretty_midi.Note(
                velocity=velocity,
                pitch=midi,
                start=start_time,
                end=end_time,
            )
            instrument.notes.append(note)

    pm.instruments.append(instrument)
    return pm


def save_midi(pm: pretty_midi.PrettyMIDI, filepath: str) -> None:
    """Write a PrettyMIDI object to a .mid file.

    Args:
        pm: The PrettyMIDI object to save.
        filepath: Output path (should end with .mid or .midi).
    """
    pm.write(filepath)

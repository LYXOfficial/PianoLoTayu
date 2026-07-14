"""MIDI-to-audio renderer using sine wave synthesis."""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
import pretty_midi
import soundfile as sf


def midi_note_to_freq(pitch: int) -> float:
    """Convert MIDI note number to fundamental frequency in Hz."""
    return 440.0 * (2.0 ** ((pitch - 69) / 12.0))


def _adsr_envelope(
    n_samples: int,
    sr: int,
    attack: float = 0.01,
    decay: float = 0.08,
    sustain_level: float = 0.75,
    release: float = 0.10,
) -> np.ndarray:
    """Generate an ADSR amplitude envelope.

    Args:
        n_samples: Total number of samples in the note.
        sr: Sample rate in Hz.
        attack: Attack time in seconds.
        decay: Decay time in seconds.
        sustain_level: Sustain amplitude (0–1, relative to peak).
        release: Release time in seconds.

    Returns:
        1D array of amplitude values (0–1) for each sample.
    """
    env = np.ones(n_samples, dtype=np.float64)

    n_attack = int(attack * sr)
    n_decay = int(decay * sr)
    n_release = int(release * sr)

    # Ensure we don't exceed the note length
    n_attack = min(n_attack, n_samples)
    n_release = min(n_release, n_samples)

    total_adsr = n_attack + n_decay + n_release

    if total_adsr > n_samples:
        # Scale everything down proportionally
        scale = n_samples / total_adsr
        n_attack = max(1, int(n_attack * scale))
        n_decay = max(1, int(n_decay * scale))
        n_release = max(1, int(n_release * scale))

    # Attack: 0 → 1 (linear)
    if n_attack > 0:
        env[:n_attack] = np.linspace(0.0, 1.0, n_attack)

    # Decay: 1 → sustain_level (linear)
    decay_start = n_attack
    decay_end = n_attack + n_decay
    if n_decay > 0 and decay_end <= n_samples:
        env[decay_start:decay_end] = np.linspace(
            1.0, sustain_level, n_decay
        )
        env[decay_end:] = sustain_level

    # Release: sustain_level → 0 (linear)
    if n_release > 0:
        env[-n_release:] = np.linspace(sustain_level, 0.0, n_release)

    return env


def render_midi(
    midi_path: str | Path,
    sr: int = 44100,
    attack: float = 0.008,
    decay: float = 0.06,
    sustain_level: float = 0.70,
    release: float = 0.08,
    master_volume: float = 0.85,
    high_damp: float = 0.0,
    mid_boost: float = 0.0,
) -> tuple[np.ndarray, int]:
    """Render a MIDI file to audio using sine wave synthesis.

    Each note is synthesized as a pure sine wave with an ADSR envelope
    and scaled by its MIDI velocity. Optional frequency-dependent shaping
    (high_damp, mid_boost) is applied before mixing.

    Args:
        midi_path: Path to the input .mid file.
        sr: Output sample rate in Hz (default 44100).
        attack: Envelope attack time in seconds.
        decay: Envelope decay time in seconds.
        sustain_level: Envelope sustain level (0–1).
        release: Envelope release time in seconds.
        master_volume: Peak output amplitude (0–1).
        high_damp: High-frequency damping exponent. 0 = off. 0.3 = gentle,
            0.6 = strong. Formula: ``scale = (261.63 / freq) ** high_damp``
            for frequencies above middle C.
        mid_boost: Midrange boost for vocal/piano range (C3–C6, ~130–1047 Hz).
            0 = off. 1.0 = +6 dB. 2.0 = +12 dB. Uses a bell-shaped curve
            centred on ~523 Hz (C5).

    Returns:
        Tuple of (audio, sr) where *audio* is a 1D float64 NumPy array.
    """
    pm = pretty_midi.PrettyMIDI(str(midi_path))

    duration = pm.get_end_time()
    if duration <= 0:
        return np.zeros(int(sr * 0.1)), sr

    n_total = int(duration * sr) + int(0.5 * sr)
    audio = np.zeros(n_total, dtype=np.float64)

    ref_freq = 261.63  # Middle C

    for instrument in pm.instruments:
        if instrument.is_drum:
            continue
        for note in instrument.notes:
            freq = midi_note_to_freq(note.pitch)
            start_s = int(note.start * sr)
            end_s = int(note.end * sr)

            if end_s <= start_s:
                continue

            n = end_s - start_s
            t = np.arange(n, dtype=np.float64) / sr

            # Generate sine wave
            sine = np.sin(2.0 * np.pi * freq * t)

            # Apply ADSR envelope
            env = _adsr_envelope(n, sr, attack, decay, sustain_level, release)

            # Scale by velocity (0–127 → 0–1)
            velocity_scale = note.velocity / 127.0

            # High-frequency damping: reduces piercing highs
            if high_damp > 0 and freq > ref_freq:
                velocity_scale *= (ref_freq / freq) ** high_damp

            # Midrange boost: enhances vocal/piano presence (bell curve)
            if mid_boost > 0:
                # Bell centred at C5 (523.25 Hz), width ~2 octaves
                centre = 523.25
                octaves = (np.log2(freq / centre)) ** 2
                # Gaussian: 1.0 at centre, falls off with octave distance
                bell = np.exp(-octaves / 0.8)
                velocity_scale *= 1.0 + mid_boost * bell

            sine *= env * velocity_scale

            # Mix into output buffer
            audio[start_s:end_s] += sine

    # Normalise
    peak = np.max(np.abs(audio))
    if peak > 1e-12:
        audio = audio / peak * master_volume

    return audio, sr


def play_audio(audio: np.ndarray, sr: int) -> None:
    """Play an audio array through the system audio output.

    Tries ``sounddevice`` first for low-latency playback. Falls back to
    ``ffplay`` (from ffmpeg), then ``aplay``, writing a temporary WAV file.

    Args:
        audio: 1D float64 audio array.
        sr: Sample rate in Hz.
    """
    try:
        import sounddevice as sd

        # Ensure float32 for sounddevice
        samples = audio.astype(np.float32)
        sd.play(samples, samplerate=sr)
        sd.wait()
        return
    except Exception as exc:
        print(f"sounddevice unavailable ({exc}), trying fallback...", file=sys.stderr)

    # Fallback: write temp WAV and play with external player
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        tmp_path = tmp.name

    try:
        sf.write(tmp_path, audio, sr)

        # Try ffplay first (part of ffmpeg, common on Linux)
        for player in [
            ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", tmp_path],
            ["aplay", tmp_path],
            ["paplay", tmp_path],
        ]:
            try:
                subprocess.run(player, check=True)
                return
            except (FileNotFoundError, subprocess.CalledProcessError):
                continue

        print(
            f"No audio player found. WAV saved to: {tmp_path}",
            file=sys.stderr,
        )
    finally:
        # Clean up temp file (only if played successfully or no player found
        # and the user doesn't need it — we clean up in all cases since
        # this is just a preview)
        try:
            Path(tmp_path).unlink(missing_ok=True)
        except OSError:
            pass


def render_and_play(
    midi_path: str | Path,
    sr: int = 44100,
    master_volume: float = 0.85,
    high_damp: float = 0.0,
    mid_boost: float = 0.0,
) -> None:
    """Render a MIDI file to audio and play it immediately.

    Convenience wrapper around :func:`render_midi` + :func:`play_audio`.
    """
    print(f"Rendering: {midi_path}")
    audio, sr = render_midi(midi_path, sr=sr, master_volume=master_volume,
                            high_damp=high_damp, mid_boost=mid_boost)
    duration = len(audio) / sr
    print(f"  Duration: {duration:.1f}s, Sample rate: {sr} Hz")
    print("Playing...")
    play_audio(audio, sr)


def render_to_wav(
    midi_path: str | Path,
    wav_path: str | Path,
    sr: int = 44100,
    master_volume: float = 0.85,
    high_damp: float = 0.0,
    mid_boost: float = 0.0,
) -> None:
    """Render a MIDI file to a WAV file."""
    print(f"Rendering: {midi_path}")
    audio, sr = render_midi(midi_path, sr=sr, master_volume=master_volume,
                            high_damp=high_damp, mid_boost=mid_boost)
    print(f"  Duration: {len(audio)/sr:.1f}s, Samples: {len(audio)}")
    sf.write(str(wav_path), audio, sr)
    print(f"Saved: {wav_path}")

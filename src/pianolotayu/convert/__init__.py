"""Audio-to-MIDI conversion library."""

from .audio import load_audio, compute_stft
from .analysis import (
    MIDI_MIN,
    MIDI_MAX,
    freq_to_midi,
    midi_to_freq,
    detect_peaks_per_frame,
    amplitude_to_velocity,
    analyze_frames,
)
from .midi_writer import create_midi, save_midi

__all__ = [
    "load_audio",
    "compute_stft",
    "MIDI_MIN",
    "MIDI_MAX",
    "freq_to_midi",
    "midi_to_freq",
    "detect_peaks_per_frame",
    "amplitude_to_velocity",
    "analyze_frames",
    "create_midi",
    "save_midi",
]

"""Peak detection in the STFT spectrogram and frequency-to-MIDI mapping."""

import numpy as np
from scipy.signal import argrelextrema

# Piano range: A0 (MIDI 21, ~27.5 Hz) to C8 (MIDI 108, ~4186 Hz)
MIDI_MIN = 21
MIDI_MAX = 108


def freq_to_midi(freq: float, piano_limit: bool = True) -> int:
    """Convert a frequency in Hz to the nearest MIDI note number.

    When *piano_limit* is True (default), frequencies outside the piano
    range (MIDI 21–108) are octave-folded into range to preserve harmonic
    information. When False, the raw MIDI number is returned unclamped.

    Args:
        freq: Frequency in Hz.
        piano_limit: If True, octave-fold into [21, 108]. If False, return
            the raw nearest MIDI note anywhere on the MIDI scale (0–127).

    Returns:
        MIDI note number.
    """
    if freq <= 0:
        return MIDI_MIN

    # Standard formula: A4 (440 Hz) = MIDI 69
    midi_float = 69.0 + 12.0 * np.log2(freq / 440.0)
    midi = int(round(midi_float))

    if not piano_limit:
        # Clamp to valid MIDI range 0–127, but no octave folding
        return max(0, min(127, midi))

    # Octave-fold into piano range
    while midi < MIDI_MIN:
        midi += 12
    while midi > MIDI_MAX:
        midi -= 12

    return midi


def midi_to_freq(midi: int) -> float:
    """Convert a MIDI note number to its fundamental frequency in Hz."""
    return 440.0 * (2.0 ** ((midi - 69) / 12.0))


def detect_peaks_per_frame(
    spectrum_db: np.ndarray,
    freqs: np.ndarray,
    threshold_db: float = 30.0,
    max_notes: int = 16,
    min_bin_distance: int = 2,
) -> list[tuple[float, float]]:
    """Detect spectral peaks in a single frame.

    Finds local maxima in the dB spectrum that exceed an adaptive threshold
    (frame maximum minus *threshold_db*). Uses parabolic interpolation across
    3 adjacent bins for sub-bin frequency accuracy.

    Args:
        spectrum_db: 1D array of magnitude values in dB for one time frame.
        freqs: 1D array of bin center frequencies (same length as spectrum_db).
        threshold_db: Peaks must be within this many dB of the frame's maximum.
        max_notes: Maximum number of peaks to return (strongest first).
        min_bin_distance: Minimum separation between peaks in FFT bins.

    Returns:
        List of (frequency_hz, amplitude_db) tuples, sorted by amplitude
        (loudest first), up to *max_notes* entries.
    """
    if spectrum_db.size == 0:
        return []

    frame_max = float(np.max(spectrum_db))
    if np.isneginf(frame_max):
        return []

    threshold = frame_max - threshold_db

    # Find local maxima indices (scipy's argrelextrema)
    # Use comparator=np.greater_equal to catch plateau peaks
    peak_indices = argrelextrema(spectrum_db, np.greater_equal, order=min_bin_distance)[0]

    # Filter by threshold
    peak_indices = peak_indices[spectrum_db[peak_indices] >= threshold]

    if len(peak_indices) == 0:
        return []

    # Sort by amplitude (descending)
    peak_amps = spectrum_db[peak_indices]
    sort_order = np.argsort(peak_amps)[::-1]
    peak_indices = peak_indices[sort_order]
    peak_amps = peak_amps[sort_order]

    # Parabolic interpolation for sub-bin accuracy
    peaks: list[tuple[float, float]] = []
    for idx, amp in zip(peak_indices, peak_amps):
        freq = _interpolate_peak_frequency(spectrum_db, freqs, idx)
        peaks.append((freq, float(amp)))

        if len(peaks) >= max_notes:
            break

    return peaks


def _interpolate_peak_frequency(
    spectrum_db: np.ndarray,
    freqs: np.ndarray,
    peak_idx: int,
) -> float:
    """Refine the frequency of a peak using parabolic interpolation.

    Uses the peak bin and its two neighbors to estimate the true
    centre frequency with sub-bin resolution.

    Args:
        spectrum_db: Full frame spectrum in dB.
        freqs: Frequency array.
        peak_idx: Index of the detected peak bin.

    Returns:
        Interpolated frequency in Hz.
    """
    n_bins = len(spectrum_db)

    if peak_idx <= 0 or peak_idx >= n_bins - 1:
        # Can't interpolate at edges; return bin centre
        return float(freqs[peak_idx])

    alpha = spectrum_db[peak_idx - 1]
    beta = spectrum_db[peak_idx]
    gamma = spectrum_db[peak_idx + 1]

    # Parabolic interpolation formula
    denominator = alpha - 2.0 * beta + gamma
    if abs(denominator) < 1e-12:
        # Degenerate case: return bin centre
        return float(freqs[peak_idx])

    p = 0.5 * (alpha - gamma) / denominator

    # Clamp offset to ±0.5 bins
    p = max(-0.5, min(0.5, p))

    # Linear interpolation between adjacent bin frequencies
    if p >= 0:
        freq = freqs[peak_idx] + p * (freqs[min(peak_idx + 1, n_bins - 1)] - freqs[peak_idx])
    else:
        freq = freqs[peak_idx] + p * (freqs[peak_idx] - freqs[max(peak_idx - 1, 0)])

    return float(freq)


def amplitude_to_velocity(
    amp_db: float,
    frame_max_db: float,
    dynamic_range_db: float = 60.0,
    min_velocity: int = 8,
) -> int | None:
    """Map a dB amplitude to a MIDI velocity value (1–127).

    Uses linear mapping within a configurable dynamic range below the
    frame maximum. Values below the minimum velocity are treated as noise
    and return None.

    Args:
        amp_db: Peak amplitude in dB.
        frame_max_db: Maximum amplitude in the frame (dB).
        dynamic_range_db: dB range mapped to velocity 1–127.
        min_velocity: Velocities below this are dropped (noise gate).

    Returns:
        MIDI velocity 1–127, or None if the peak is too quiet.
    """
    min_db = frame_max_db - dynamic_range_db

    if amp_db < min_db:
        return None

    # Linear mapping: min_db → 1, frame_max_db → 127
    fraction = (amp_db - min_db) / dynamic_range_db
    fraction = max(0.0, min(1.0, fraction))
    velocity = int(round(1.0 + fraction * 126.0))

    if velocity < min_velocity:
        return None

    return velocity


def analyze_frames(
    D_db: np.ndarray,
    freqs: np.ndarray,
    times: np.ndarray,
    sr: int,
    hop_length: int,
    threshold_db: float = 30.0,
    max_notes: int = 16,
    dynamic_range_db: float = 60.0,
    piano_limit: bool = True,
    high_damp: float = 0.35,
    mid_boost: float = 0.6,
) -> list[list[tuple[int, int]]]:
    """Analyze all STFT frames and convert to MIDI note events.

    Each frame is processed for spectral peaks, which are mapped to
    MIDI note numbers and velocities. Frequency-dependent velocity
    shaping (high_damp, mid_boost) is applied so the output MIDI
    already has the desired tonal balance.

    Args:
        D_db: STFT magnitude spectrogram in dB [freq_bins × time_frames].
        freqs: Bin centre frequencies.
        times: Frame times in seconds.
        sr: Sample rate (unused, kept for API consistency).
        hop_length: Hop length (unused, kept for API consistency).
        threshold_db: Peak detection threshold in dB below frame max.
        max_notes: Maximum simultaneous notes per frame.
        dynamic_range_db: Dynamic range for velocity mapping.
        piano_limit: If True, octave-fold into piano range [21–108].
            If False, keep raw MIDI 0–127.
        high_damp: High-frequency damping exponent. 0 = off, 0.35 = gentle.
            Reduces velocity of notes above middle C (261.63 Hz).
        mid_boost: Midrange boost for vocal/piano presence. 0 = off,
            0.6 = moderate. Boosts velocity of notes near C5 (523 Hz).

    Returns:
        List of lists, one per frame. Each inner list contains
        (midi_note, velocity) tuples for detected notes.
    """
    import numpy as np

    n_frames = D_db.shape[1]
    frame_notes: list[list[tuple[int, int]]] = []

    ref_freq = 261.63  # Middle C
    centre_freq = 523.25  # C5, centre of mid boost bell

    for t in range(n_frames):
        spectrum = D_db[:, t]
        frame_max = float(np.max(spectrum)) if spectrum.size > 0 else -80.0

        peaks = detect_peaks_per_frame(
            spectrum, freqs,
            threshold_db=threshold_db,
            max_notes=max_notes,
        )

        notes: list[tuple[int, int]] = []
        for freq, amp_db in peaks:
            midi = freq_to_midi(freq, piano_limit=piano_limit)
            vel = amplitude_to_velocity(
                amp_db, frame_max, dynamic_range_db=dynamic_range_db
            )
            if vel is None:
                continue

            # ---- Frequency-dependent velocity shaping ----
            scale = 1.0

            # High-frequency damping: reduce piercing highs
            if high_damp > 0 and freq > ref_freq:
                scale *= (ref_freq / freq) ** high_damp

            # Midrange boost: enhance vocal/piano presence
            if mid_boost > 0 and freq > 1e-6:
                octaves_sq = (np.log2(freq / centre_freq)) ** 2
                bell = np.exp(-octaves_sq / 0.8)
                scale *= 1.0 + mid_boost * bell

            vel = int(round(vel * scale))
            vel = max(1, min(127, vel))
            # -----------------------------------------------

            notes.append((midi, vel))

        frame_notes.append(notes)

    return frame_notes

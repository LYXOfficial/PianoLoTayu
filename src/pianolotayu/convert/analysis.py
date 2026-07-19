"""Peak detection in the STFT spectrogram and frequency-to-MIDI mapping."""

from __future__ import annotations

import math
import time

import numpy as np

# Piano range: A0 (MIDI 21, ~27.5 Hz) to C8 (MIDI 108, ~4186 Hz)
MIDI_MIN = 21
MIDI_MAX = 108


def _argrelextrema_max(x: np.ndarray, order: int = 1) -> np.ndarray:
    """Indices of local maxima with *order* samples on each side (1-D)."""
    x = np.asarray(x)
    n = int(x.size)
    order = max(1, int(order))
    if n < 2 * order + 1:
        return np.zeros(0, dtype=np.intp)
    mid = x[order:n - order]
    mask = np.ones(mid.shape, dtype=bool)
    for k in range(1, order + 1):
        mask &= mid >= x[order - k:n - order - k]
        mask &= mid >= x[order + k:n - order + k]
    return np.flatnonzero(mask) + order


def _local_max_mask_2d(
    D: np.ndarray,
    order: int = 1,
    progress_cb=None,
    prog_lo: float = 0.0,
    prog_hi: float = 1.0,
) -> np.ndarray:
    """Boolean local-max mask along frequency axis for every frame.

    Processes columns in chunks so *progress_cb* can fire and the GUI thread
    can repaint (numpy releases the GIL between chunks).
    """
    n_bins, n_frames = D.shape
    order = max(1, int(order))
    out = np.zeros((n_bins, n_frames), dtype=bool)
    if n_bins < 2 * order + 1 or n_frames == 0:
        return out

    # ~40 chunks → smooth bar; min 256 frames/chunk keeps numpy happy
    n_chunks = min(40, max(1, n_frames // 256))
    chunk = max(1, (n_frames + n_chunks - 1) // n_chunks)

    for c0 in range(0, n_frames, chunk):
        c1 = min(n_frames, c0 + chunk)
        block = D[:, c0:c1]
        mid = block[order:n_bins - order, :]
        mask = np.ones(mid.shape, dtype=bool)
        for k in range(1, order + 1):
            mask &= mid >= block[order - k:n_bins - order - k, :]
            mask &= mid >= block[order + k:n_bins - order + k, :]
        out[order:n_bins - order, c0:c1] = mask
        if progress_cb is not None:
            frac = prog_lo + (prog_hi - prog_lo) * (c1 / n_frames)
            progress_cb(min(prog_hi, frac))
            # Only yield occasionally — sleep every chunk was wasting time
            if (c0 // max(chunk, 1)) % 4 == 0:
                time.sleep(0)
    return out


def freq_to_midi(freq: float, piano_limit: bool = True) -> int:
    """Convert a frequency in Hz to the nearest MIDI note number."""
    if freq <= 0:
        return MIDI_MIN

    midi = int(round(69.0 + 12.0 * math.log2(freq / 440.0)))

    if not piano_limit:
        return max(0, min(127, midi))

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
    """Detect spectral peaks in a single frame (kept for API / tests)."""
    if spectrum_db.size == 0:
        return []

    frame_max = float(np.max(spectrum_db))
    if np.isneginf(frame_max):
        return []

    threshold = frame_max - threshold_db
    peak_indices = _argrelextrema_max(spectrum_db, order=min_bin_distance)
    peak_indices = peak_indices[spectrum_db[peak_indices] >= threshold]
    if peak_indices.size == 0:
        return []

    peak_amps = spectrum_db[peak_indices]
    order = np.argsort(peak_amps)[::-1]
    peak_indices = peak_indices[order]
    peak_amps = peak_amps[order]

    peaks: list[tuple[float, float]] = []
    for idx, amp in zip(peak_indices, peak_amps):
        peaks.append((
            _interpolate_peak_frequency(spectrum_db, freqs, int(idx)),
            float(amp),
        ))
        if len(peaks) >= max_notes:
            break
    return peaks


def _interpolate_peak_frequency(
    spectrum_db: np.ndarray,
    freqs: np.ndarray,
    peak_idx: int,
) -> float:
    """Refine the frequency of a peak using parabolic interpolation."""
    n_bins = len(spectrum_db)
    if peak_idx <= 0 or peak_idx >= n_bins - 1:
        return float(freqs[peak_idx])

    alpha = float(spectrum_db[peak_idx - 1])
    beta = float(spectrum_db[peak_idx])
    gamma = float(spectrum_db[peak_idx + 1])
    denom = alpha - 2.0 * beta + gamma
    if abs(denom) < 1e-12:
        return float(freqs[peak_idx])

    p = 0.5 * (alpha - gamma) / denom
    p = max(-0.5, min(0.5, p))
    if p >= 0:
        return float(
            freqs[peak_idx]
            + p * (freqs[min(peak_idx + 1, n_bins - 1)] - freqs[peak_idx])
        )
    return float(
        freqs[peak_idx]
        + p * (freqs[peak_idx] - freqs[max(peak_idx - 1, 0)])
    )


def amplitude_to_velocity(
    amp_db: float,
    frame_max_db: float,
    dynamic_range_db: float = 60.0,
    min_velocity: int = 8,
) -> int | None:
    """Map a dB amplitude to a MIDI velocity value (1–127)."""
    min_db = frame_max_db - dynamic_range_db
    if amp_db < min_db:
        return None
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
    progress_cb=None,
    min_bin_distance: int = 2,
) -> list[list[tuple[int, int]]]:
    """Analyze all STFT frames and convert to MIDI note events.

    Peak-finding is vectorised over the whole spectrogram (local-max mask once),
    then a light per-frame loop maps peaks → MIDI.  *progress_cb* is invoked
    about every 50 ms (and at 0 / 1) so the UI can show smooth progress.
    """
    D = np.asarray(D_db, dtype=np.float32)
    if D.ndim != 2:
        return []
    n_bins, n_frames = D.shape
    freqs_a = np.asarray(freqs, dtype=np.float64)
    if freqs_a.shape[0] != n_bins:
        raise ValueError("freqs length must match D_db frequency axis")

    if n_frames == 0:
        if progress_cb is not None:
            progress_cb(1.0)
        return []

    if progress_cb is not None:
        progress_cb(0.0)

    order = max(1, int(min_bin_distance))
    # Peak mask — report 0%→55% in chunks (float32, no full float64 copy)
    local_max = _local_max_mask_2d(
        D, order=order, progress_cb=progress_cb, prog_lo=0.0, prog_hi=0.55,
    )
    frame_max = np.max(D, axis=0)  # (n_frames,)
    finite_max = np.isfinite(frame_max)
    thresholds = frame_max - np.float32(threshold_db)
    if progress_cb is not None:
        progress_cb(0.58)

    ref_freq = 261.63
    centre_freq = 523.25
    log2 = math.log2
    exp = math.exp
    do_high = high_damp > 0
    do_mid = mid_boost > 0
    min_vel = 8

    frame_notes: list[list[tuple[int, int]]] = [[] for _ in range(n_frames)]

    # Map peaks → MIDI: 58%→100%.  Tick every ~2% of frames AND ≥40 ms so the
    # GUI event loop gets intermediate values (not one burst at the end).
    last_prog_t = time.monotonic()
    last_frac = 0.58
    frame_step = max(1, n_frames // 50)

    def _report_map(t_idx: int, force: bool = False) -> None:
        nonlocal last_prog_t, last_frac
        if progress_cb is None:
            return
        now = time.monotonic()
        frame_hit = (t_idx % frame_step == 0) or (t_idx + 1 >= n_frames)
        if not force and not frame_hit and (now - last_prog_t) < 0.04:
            return
        # 0.58 … 1.0 over the mapping loop
        frac = 0.58 + 0.42 * ((t_idx + 1) / n_frames)
        if force or frac - last_frac >= 0.01 or frac >= 0.999:
            progress_cb(min(1.0, frac))
            last_frac = frac
            last_prog_t = now
            time.sleep(0)  # let GUI repaint between chunks

    for t in range(n_frames):
        if not finite_max[t]:
            _report_map(t)
            continue

        thr = float(thresholds[t])
        fmax = float(frame_max[t])
        col = D[:, t]
        idx = np.flatnonzero(local_max[:, t] & (col >= thr))
        if idx.size == 0:
            _report_map(t)
            continue

        amps = col[idx]
        if idx.size > max_notes:
            part = np.argpartition(amps, -max_notes)[-max_notes:]
            idx = idx[part]
            amps = amps[part]
        order_amp = np.argsort(amps)[::-1]
        idx = idx[order_amp]
        amps = amps[order_amp]

        notes: list[tuple[int, int]] = []
        for bi, amp in zip(idx, amps):
            bi = int(bi)
            amp_f = float(amp)
            freq = _interpolate_peak_frequency(col, freqs_a, bi)
            midi = freq_to_midi(freq, piano_limit=piano_limit)

            min_db = fmax - dynamic_range_db
            if amp_f < min_db:
                continue
            fraction = (amp_f - min_db) / dynamic_range_db
            fraction = 0.0 if fraction < 0.0 else (1.0 if fraction > 1.0 else fraction)
            vel = int(round(1.0 + fraction * 126.0))
            if vel < min_vel:
                continue

            scale = 1.0
            if do_high and freq > ref_freq:
                scale *= (ref_freq / freq) ** high_damp
            if do_mid and freq > 1e-6:
                octaves_sq = log2(freq / centre_freq) ** 2
                scale *= 1.0 + mid_boost * exp(-octaves_sq / 0.8)

            vel = int(round(vel * scale))
            if vel < 1:
                vel = 1
            elif vel > 127:
                vel = 127
            notes.append((midi, vel))

        frame_notes[t] = notes
        _report_map(t)

    if progress_cb is not None:
        progress_cb(1.0)
    return frame_notes

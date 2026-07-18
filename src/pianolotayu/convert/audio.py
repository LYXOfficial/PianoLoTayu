"""Audio loading and Short-Time Fourier Transform (STFT) analysis."""

import numpy as np
import librosa


def load_audio(filepath: str, sr: int = 22050) -> tuple[np.ndarray, int]:
    """Load an audio file and convert to mono.

    Args:
        filepath: Path to the audio file (MP3, WAV, FLAC, etc.).
        sr: Target sample rate in Hz. Default 22050 (Nyquist 11025 Hz,
            comfortably above the piano's highest note C8 at ~4186 Hz).

    Returns:
        Tuple of (mono_signal, sample_rate).
    """
    y, sr = librosa.load(filepath, sr=sr, mono=True)
    return y, sr


def compute_stft(
    signal: np.ndarray,
    sr: int,
    n_fft: int = 4096,
    hop_length: int = 512,
    window: str = "hann",
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Compute the STFT magnitude spectrogram in dB.

    Args:
        signal: 1D mono audio signal.
        sr: Sample rate in Hz (used to compute frequency bins).
        n_fft: FFT window size. 4096 → ~46ms window at 22.05kHz,
               giving ~5.4 Hz frequency resolution (enough to
               distinguish the lowest piano notes ~27.5 Hz apart).
        hop_length: Samples between successive frames. 512 → ~23ms hop,
                    giving ~43 frames/sec for responsive note-onset detection.
        window: Window function name for scipy.signal.get_window.

    Returns:
        Tuple of (spectrogram_db, freqs, times):
            - spectrogram_db: 2D array [freq_bins × time_frames] in dB.
            - freqs: 1D array of bin center frequencies in Hz.
            - times: 1D array of frame times in seconds.
    """
    D = librosa.stft(signal, n_fft=n_fft, hop_length=hop_length, window=window)
    mag = np.abs(D)
    D_db = librosa.amplitude_to_db(mag, ref=np.max)

    freqs = librosa.fft_frequencies(sr=sr, n_fft=n_fft)
    times = librosa.frames_to_time(
        np.arange(D_db.shape[1]), sr=sr, hop_length=hop_length
    )

    return D_db, freqs, times

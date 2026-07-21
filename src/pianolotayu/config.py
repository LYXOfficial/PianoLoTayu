"""Shared defaults — single source of truth for both CLI and GUI."""
VERSION = "v1.0.0"

DEFAULTS = {
    "sr": 22050,
    "n_fft": 4096,
    "hop_length": 256,
    "threshold": 20.0,
    "max_notes": 16,
    "min_duration": 30.0,
    "dynamic_range": 60.0,
    "no_piano_limit": False,
    "high_damp": 0.0,
    "mid_boost": 0.0,
}

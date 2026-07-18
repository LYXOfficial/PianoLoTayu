# PianoLoTayu 🎹

Convert audio files (MP3/WAV/FLAC) to piano MIDI via Short-Time Fourier Transform (STFT).

## How It Works

1. **Load audio** — mono conversion at configurable sample rate (default 22.05 kHz)
2. **STFT analysis** — short-time Fourier transform with 4096-point FFT for ~5.4 Hz frequency resolution
3. **Peak detection** — per-frame spectral peak finding with adaptive threshold and parabolic interpolation
4. **Frequency → MIDI** — map detected frequencies to MIDI notes, octave-folding out-of-range frequencies into the piano range (A0–C8, MIDI 21–108)
5. **Velocity estimation** — map spectral amplitudes to MIDI velocity (1–127) with configurable dynamic range
6. **Note tracking** — hysteresis-based onset/offset detection to avoid flickering
7. **MIDI output** — standard `.mid` file with Acoustic Grand Piano instrument

## Installation

```bash
# Clone and enter the project
cd PianoLoTayu

# Install with uv
uv sync
```

## Usage

```bash
# Basic: convert an MP3/WAV to MIDI
uv run python -m pianolotayu song.mp3

# Specify output path
uv run python -m pianolotayu song.wav -o song.mid

# Tune parameters for different audio
uv run python -m pianolotayu song.mp3 \
    --threshold 25 \       # dB below peak to detect (lower = more notes)
    --max-notes 12 \        # max simultaneous notes
    --min-duration 80 \     # minimum note duration in ms
    --dynamic-range 50      # dB range mapped to velocity
```

## Options

| Option | Default | Description |
|--------|---------|-------------|
| `input` | (required) | Input audio file (`.mp3`, `.wav`, `.flac`, etc.) |
| `-o`, `--output` | `<input>.mid` | Output MIDI file path |
| `--sr` | `22050` | Sample rate in Hz for analysis |
| `--n-fft` | `4096` | FFT window size (frequency resolution) |
| `--hop-length` | `512` | Hop length between STFT frames (time resolution) |
| `--threshold` | `30` | Peak detection threshold in dB below frame max |
| `--max-notes` | `16` | Maximum simultaneous notes per frame |
| `--min-duration` | `50` | Minimum note duration in milliseconds |
| `--dynamic-range` | `60` | Dynamic range for velocity mapping in dB |

## Requirements

- Python ≥ 3.13
- Dependencies: `numpy`, `scipy`, `librosa`, `soundfile`, `audioread`, `pretty_midi`

## License

MIT

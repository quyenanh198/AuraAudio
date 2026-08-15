from __future__ import annotations

from pathlib import Path

import numpy as np
from scipy.io import wavfile


def write_guitar_pluck_wav(path: Path, duration_s: float = 2.0, sample_rate: int = 44100) -> Path:
    """Synthesize a short, rights-free guitar-pluck-like signal: a decaying
    sum of harmonics of E2/A2/D3/G3 (open low strings), one note per 0.5s.
    This is not a real recording, but it is enough signal for basic-pitch to
    detect onsets and pitches deterministically in tests."""
    open_string_freqs = [82.41, 110.00, 146.83, 196.00]  # E2 A2 D3 G3
    t = np.linspace(0, duration_s, int(sample_rate * duration_s), endpoint=False)
    signal = np.zeros_like(t)
    note_len = 0.5
    for i, freq in enumerate(open_string_freqs):
        start = i * note_len
        end = start + note_len
        mask = (t >= start) & (t < end)
        local_t = t[mask] - start
        envelope = np.exp(-3.0 * local_t)
        harmonic = sum(np.sin(2 * np.pi * freq * (h + 1) * local_t) / (h + 1) for h in range(4))
        signal[mask] = envelope * harmonic
    signal = (signal / np.max(np.abs(signal)) * 0.8 * 32767).astype(np.int16)
    path.parent.mkdir(parents=True, exist_ok=True)
    wavfile.write(str(path), sample_rate, signal)
    return path

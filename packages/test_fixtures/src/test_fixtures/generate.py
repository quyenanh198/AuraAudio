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
        signal[mask] = _decaying_harmonic(local_t, freq)
    signal = (signal / np.max(np.abs(signal)) * 0.8 * 32767).astype(np.int16)
    path.parent.mkdir(parents=True, exist_ok=True)
    wavfile.write(str(path), sample_rate, signal)
    return path


def write_guitar_pluck_with_silence_wav(
    path: Path,
    pre_note_count: int = 2,
    silence_s: float = 4.0,
    post_note_count: int = 2,
    note_len: float = 0.5,
    sample_rate: int = 44100,
) -> Path:
    """Like write_guitar_pluck_wav, but with a genuine mid-clip silence gap
    (true zero signal, not just quiet) between two groups of plucked notes.

    For exercising quantize.py's silent-measure fidelity fix end-to-end
    against real transcription: `silence_s` (default 4.0s) is long enough
    to span at least one full measure at any tempo librosa's beat tracker
    is realistically going to detect from this fixture's note spacing
    (note_len=0.5s -> a plausible ~120 BPM reading, where one 4/4 measure
    is 2.0s — 4.0s of true silence comfortably covers that with margin for
    detection variance at other plausible tempi too).
    """
    open_string_freqs = [82.41, 110.00, 146.83, 196.00]  # E2 A2 D3 G3
    pre_duration = pre_note_count * note_len
    post_duration = post_note_count * note_len
    total_duration = pre_duration + silence_s + post_duration
    t = np.linspace(0, total_duration, int(sample_rate * total_duration), endpoint=False)
    signal = np.zeros_like(t)

    def _place_notes(start_offset: float, count: int) -> None:
        for i in range(count):
            freq = open_string_freqs[i % len(open_string_freqs)]
            start = start_offset + i * note_len
            end = start + note_len
            mask = (t >= start) & (t < end)
            local_t = t[mask] - start
            signal[mask] = _decaying_harmonic(local_t, freq)

    _place_notes(0.0, pre_note_count)
    _place_notes(pre_duration + silence_s, post_note_count)

    signal = (signal / np.max(np.abs(signal)) * 0.8 * 32767).astype(np.int16)
    path.parent.mkdir(parents=True, exist_ok=True)
    wavfile.write(str(path), sample_rate, signal)
    return path


_METER_PATTERNS = {
    "4/4": [1.0, 0.4, 0.6, 0.4],
    "3/4": [1.0, 0.4, 0.4],
}


def _click(duration: float, freq: float, amp: float, sample_rate: int) -> np.ndarray:
    t = np.linspace(0, duration, int(sample_rate * duration), endpoint=False)
    envelope = np.exp(-200 * t)
    return amp * envelope * np.sin(2 * np.pi * freq * t)


def _decaying_harmonic(
    local_t: np.ndarray, freq: float, num_harmonics: int = 4, decay_rate: float = 3.0
) -> np.ndarray:
    """Synthesize a decaying harmonic signal at a given frequency.

    Args:
        local_t: Time array (relative to note start) in seconds.
        freq: Fundamental frequency in Hz.
        num_harmonics: Number of harmonics to include (default 4).
        decay_rate: Exponential decay rate (default 3.0).

    Returns:
        The envelope-modulated harmonic signal as a numpy array.
    """
    envelope = np.exp(-decay_rate * local_t)
    harmonic = sum(np.sin(2 * np.pi * freq * (h + 1) * local_t) / (h + 1) for h in range(num_harmonics))
    return envelope * harmonic


def write_metronome_pulse_wav(
    path: Path,
    bpm: float = 120.0,
    meter: str = "4/4",
    duration_s: float = 8.0,
    sample_rate: int = 22050,
) -> Path:
    """Synthesize a metronome click track: one click per beat (never per
    subdivision — see the note in the Phase 2 beat/meter/key plan on why
    sub-beat clicks defeat beat-tracking), strong on beat 1, weaker elsewhere,
    at an exact known BPM/meter so tempo and meter detection can be tested
    against ground truth."""
    pattern = _METER_PATTERNS[meter]
    beat_s = 60.0 / bpm
    measure_len = beat_s * len(pattern)
    n_measures = max(int(duration_s / measure_len), 1)
    total_len = n_measures * measure_len + 1.0
    signal = np.zeros(int(total_len * sample_rate))
    for m in range(n_measures):
        for i, amp in enumerate(pattern):
            t0 = m * measure_len + i * beat_s
            c = _click(duration=0.03, freq=200.0, amp=amp, sample_rate=sample_rate)
            i0 = int(t0 * sample_rate)
            end = min(i0 + len(c), len(signal))
            signal[i0:end] += c[: end - i0]
    signal = (signal / np.max(np.abs(signal)) * 0.9 * 32767).astype(np.int16)
    path.parent.mkdir(parents=True, exist_ok=True)
    wavfile.write(str(path), sample_rate, signal)
    return path


_NOTE_NAME_TO_SEMITONE = {
    "C": 0, "C#": 1, "D-": 1, "D": 2, "D#": 3, "E-": 3, "E": 4, "F": 5,
    "F#": 6, "G-": 6, "G": 7, "G#": 8, "A-": 8, "A": 9, "A#": 10, "B-": 10, "B": 11,
}
_MAJOR_INTERVALS = [0, 2, 4, 5, 7, 9, 11, 12]
_MINOR_INTERVALS = [0, 2, 3, 5, 7, 8, 10, 12]


def write_diatonic_melody_wav(
    path: Path,
    key: str = "C major",
    duration_s: float = 4.0,
    sample_rate: int = 22050,
) -> Path:
    """Synthesize a short ascending diatonic scale in the given key (music21
    tonic-name convention: flats as '-', e.g. 'B- major'), so key detection
    can be tested against ground truth."""
    tonic_name, mode = key.split(" ")
    tonic_midi = 60 + _NOTE_NAME_TO_SEMITONE[tonic_name]
    intervals = _MAJOR_INTERVALS if mode == "major" else _MINOR_INTERVALS
    pitches = [tonic_midi + iv for iv in intervals]

    note_len = duration_s / len(pitches)
    t_full = np.linspace(0, duration_s, int(sample_rate * duration_s), endpoint=False)
    signal = np.zeros_like(t_full)
    for i, midi in enumerate(pitches):
        freq = 440.0 * (2 ** ((midi - 69) / 12))
        start = i * note_len
        mask = (t_full >= start) & (t_full < start + note_len)
        local_t = t_full[mask] - start
        signal[mask] = _decaying_harmonic(local_t, freq)
    signal = (signal / np.max(np.abs(signal)) * 0.8 * 32767).astype(np.int16)
    path.parent.mkdir(parents=True, exist_ok=True)
    wavfile.write(str(path), sample_rate, signal)
    return path

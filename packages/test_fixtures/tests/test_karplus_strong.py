import numpy as np
import pytest
from test_fixtures.benchmark_suite import get_benchmark_suite
from test_fixtures.generate import karplus_strong_pluck, scale_pitches
from test_fixtures.reference import midi_to_freq

# DQ-1 carry-along (docs/superpowers/SESSION-HANDOFF.md "Detection-quality
# roadmap", item 1's carry-along from the DQ-0 review): karplus_strong_pluck's
# actual ringing frequency is sample_rate/round(sample_rate/freq), not
# exactly `freq` (see its docstring's "KNOWN LIMIT" paragraph) -- this
# constant is this test module's tolerance for how far that rounding is
# allowed to drift a benchmark fixture's ground-truth pitch from what its
# audio actually contains.
FREQUENCY_ACCURACY_TOLERANCE_CENTS = 20.0

_SAMPLE_RATE = 22050


def _measured_ring_frequency_hz(freq: float, sample_rate: int = _SAMPLE_RATE, n_periods: int = 40) -> float:
    """Measures karplus_strong_pluck's actual ringing frequency via FFT.

    Two deliberate choices keep this measurement seed-independent and
    leakage-free (verified empirically -- a naive full-spectrum, windowed,
    arbitrary-duration FFT peak is NEITHER: this synth's leaky-averaging
    filter damps very gently at low pitches, so nearby harmonics can
    dominate a short/windowed analysis regardless of the note's true
    ringing frequency):

    1. The analysis segment is an EXACT whole number of periods
       (`n_periods * period`), taken unwindowed from the tail of a longer
       render. Since the synth's output is exactly periodic at `period`
       samples (up to slow decay), this makes the true ringing frequency
       land precisely on an FFT bin center -- no spectral leakage, no
       windowing bias, regardless of `n_periods`.
    2. The peak search is restricted to a band around the nominal
       frequency (`_local_peak_bin`), so a stronger harmonic elsewhere in
       the spectrum (a real, expected feature of this synth -- see
       `test_karplus_strong_pluck_decays_over_time` above) can't be
       mistaken for the fundamental.
    """
    period = max(int(round(sample_rate / freq)), 2)
    segment_len = n_periods * period
    # A little extra render length up front so the tail segment is well
    # past the initial noise-burst attack.
    duration_s = (n_periods + 10) * period / sample_rate
    signal = karplus_strong_pluck(freq, duration_s, sample_rate=sample_rate, seed=0)
    segment = signal[-segment_len:]

    spectrum = np.abs(np.fft.rfft(segment))
    freqs = np.fft.rfftfreq(len(segment), d=1.0 / sample_rate)
    search_cents = 100.0
    lo = freq * (2.0 ** (-search_cents / 1200.0))
    hi = freq * (2.0 ** (search_cents / 1200.0))
    band = np.nonzero((freqs >= lo) & (freqs <= hi))[0]
    peak_bin = band[np.argmax(spectrum[band])]
    return float(freqs[peak_bin])


def _cents_error(measured_hz: float, nominal_hz: float) -> float:
    return 1200.0 * np.log2(measured_hz / nominal_hz)


def _suite_pluck_pitches() -> list[int]:
    pitches = sorted(
        {pitch for spec in get_benchmark_suite() if spec.timbre == "pluck" for pitch, _, _ in spec.notes}
    )
    assert pitches, "benchmark suite has no pluck-timbre fixtures -- update this test's assumption"
    return pitches


def test_karplus_strong_pluck_produces_correct_sample_count():
    signal = karplus_strong_pluck(freq=110.0, duration_s=0.5, sample_rate=22050)
    assert len(signal) == int(round(22050 * 0.5))


def test_karplus_strong_pluck_is_not_silent():
    signal = karplus_strong_pluck(freq=196.0, duration_s=0.5, sample_rate=22050, seed=1)
    assert np.max(np.abs(signal)) > 0.05


def test_karplus_strong_pluck_decays_over_time():
    # A plucked string's amplitude envelope should trend downward: compare
    # RMS energy in the first vs. last third of a long-enough note.
    signal = karplus_strong_pluck(freq=110.0, duration_s=1.5, sample_rate=22050, seed=2)
    third = len(signal) // 3
    early_rms = np.sqrt(np.mean(signal[:third] ** 2))
    late_rms = np.sqrt(np.mean(signal[-third:] ** 2))
    assert late_rms < early_rms


def test_karplus_strong_pluck_is_deterministic_given_a_seed():
    a = karplus_strong_pluck(freq=146.83, duration_s=0.3, sample_rate=22050, seed=42)
    b = karplus_strong_pluck(freq=146.83, duration_s=0.3, sample_rate=22050, seed=42)
    np.testing.assert_array_equal(a, b)


def test_karplus_strong_pluck_seeds_differ():
    a = karplus_strong_pluck(freq=146.83, duration_s=0.3, sample_rate=22050, seed=1)
    b = karplus_strong_pluck(freq=146.83, duration_s=0.3, sample_rate=22050, seed=2)
    assert not np.array_equal(a, b)


def test_scale_pitches_c_major_matches_known_semitones():
    # C major: C D E F G A B C -> semitone offsets 0 2 4 5 7 9 11 12 from C4 (60)
    assert scale_pitches("C major", tonic_midi_base=60) == [60, 62, 64, 65, 67, 69, 71, 72]


def test_scale_pitches_a_minor_matches_known_semitones():
    # A minor: A B C D E F G A -> tonic A4 = 69, offsets 0 2 3 5 7 8 10 12
    assert scale_pitches("A minor", tonic_midi_base=60) == [69, 71, 72, 74, 76, 77, 79, 81]


def test_scale_pitches_respects_flat_tonic_name():
    # B- major (music21 convention for B-flat major)
    pitches = scale_pitches("B- major", tonic_midi_base=60)
    assert pitches[0] == 70  # B-flat is 10 semitones above C


@pytest.mark.parametrize("midi_pitch", _suite_pluck_pitches())
def test_karplus_strong_pluck_frequency_matches_nominal_pitch_for_suite_range(midi_pitch):
    # Every pitch the curated benchmark suite (test_fixtures.benchmark_suite)
    # currently renders with the "pluck" timbre must actually ring close
    # enough to its nominal MIDI pitch for that pitch to be trustworthy as
    # ground truth -- if a future suite change adds a pluck-timbre fixture
    # at a high enough pitch, this parametrization picks it up automatically
    # (see _suite_pluck_pitches) and this test fails for that new pitch,
    # exactly per karplus_strong_pluck's own "KNOWN LIMIT" docstring
    # paragraph.
    nominal_hz = midi_to_freq(midi_pitch)
    measured_hz = _measured_ring_frequency_hz(nominal_hz)
    error_cents = abs(_cents_error(measured_hz, nominal_hz))
    assert error_cents <= FREQUENCY_ACCURACY_TOLERANCE_CENTS, (
        f"MIDI {midi_pitch} ({nominal_hz:.2f} Hz): karplus_strong_pluck actually "
        f"rings at {measured_hz:.2f} Hz, {error_cents:.1f} cents off nominal -- "
        f"exceeds the {FREQUENCY_ACCURACY_TOLERANCE_CENTS:.0f}c tolerance. See "
        "karplus_strong_pluck's docstring (KNOWN LIMIT paragraph): this pitch's "
        "period is short enough that sample_rate/round(sample_rate/freq) rounding "
        "has drifted the true ringing frequency too far from the fixture's claimed "
        "ground-truth pitch to trust for benchmark scoring."
    )


def test_karplus_strong_pluck_exceeds_tolerance_at_a_short_period_high_pitch():
    # Demonstrates the documented limitation directly, independent of
    # whatever pitches the benchmark suite happens to use today: a period
    # around 25-30 samples (roughly E5-G5 at 22050 Hz) is short enough that
    # the rounding error alone exceeds this module's tolerance. This is a
    # guardrail pitch, not a suite fixture -- if the benchmark suite ever
    # grows to include a pluck-timbre fixture this high, the parametrized
    # test above fails for that pitch directly, for the same underlying
    # reason this test demonstrates in isolation.
    high_pitch_hz = midi_to_freq(81)  # period 25 at 22050 Hz
    measured_hz = _measured_ring_frequency_hz(high_pitch_hz)
    error_cents = abs(_cents_error(measured_hz, high_pitch_hz))
    assert error_cents > FREQUENCY_ACCURACY_TOLERANCE_CENTS, (
        "expected MIDI 81 to demonstrate karplus_strong_pluck's period-rounding "
        "pitch-accuracy limit (docstring KNOWN LIMIT paragraph); it no longer "
        f"does ({error_cents:.1f}c, within the {FREQUENCY_ACCURACY_TOLERANCE_CENTS:.0f}c "
        "tolerance) -- if the synthesis method genuinely improved, raise this "
        "guardrail pitch to one that still demonstrates the limit, and update the "
        "docstring's period estimate accordingly."
    )


def test_karplus_strong_pluck_documents_its_known_high_pitch_limitation():
    doc = karplus_strong_pluck.__doc__ or ""
    assert "known limit" in doc.lower() and "round" in doc.lower() and "period" in doc.lower(), (
        "karplus_strong_pluck's docstring should document its period-rounding "
        "pitch-accuracy limitation at short periods/high pitches -- see "
        "test_karplus_strong_pluck_frequency_matches_nominal_pitch_for_suite_range "
        "and test_karplus_strong_pluck_exceeds_tolerance_at_a_short_period_high_pitch "
        "above, which enforce it is actually true, not just documented."
    )

from test_fixtures.benchmark_suite import BENCHMARK_SUITE_VERSION, get_benchmark_suite
from test_fixtures.reference import ReferenceClipSpec

_MAX_TOTAL_AUDIO_SECONDS = 90.0  # generous vs. the ~10 min CPU budget for the whole benchmark


def test_get_benchmark_suite_returns_curated_spec_list():
    suite = get_benchmark_suite()
    assert 8 <= len(suite) <= 12
    assert all(isinstance(s, ReferenceClipSpec) for s in suite)


def test_get_benchmark_suite_names_are_unique():
    suite = get_benchmark_suite()
    names = [s.name for s in suite]
    assert len(names) == len(set(names))


def test_get_benchmark_suite_covers_both_instruments():
    suite = get_benchmark_suite()
    instruments = {s.instrument for s in suite}
    assert instruments == {"guitar", "piano"}


def test_get_benchmark_suite_covers_monophonic_and_chordal_fixtures():
    suite = get_benchmark_suite()
    onset_counts = []
    for spec in suite:
        onsets = {onset_s for _, onset_s, _ in spec.notes}
        onset_counts.append(len(spec.notes) / len(onsets))
    # at least one fixture is monophonic (one note per onset)...
    assert any(abs(c - 1.0) < 1e-9 for c in onset_counts)
    # ...and at least one fixture stacks multiple notes on the same onset (a chord)
    assert any(c > 1.0 for c in onset_counts)


def test_get_benchmark_suite_covers_at_least_three_distinct_tempi():
    suite = get_benchmark_suite()
    tempi = {s.tempo_bpm for s in suite}
    assert len(tempi) >= 3


def test_get_benchmark_suite_covers_at_least_two_keys():
    suite = get_benchmark_suite()
    keys = {s.key for s in suite}
    assert len(keys) >= 2


def test_get_benchmark_suite_covers_at_least_two_meters():
    suite = get_benchmark_suite()
    meters = {s.meter for s in suite}
    assert len(meters) >= 2


def test_get_benchmark_suite_only_uses_detectable_meters():
    from score_schema.meters import DETECTABLE_METERS

    suite = get_benchmark_suite()
    assert all(s.meter in DETECTABLE_METERS for s in suite)


def test_get_benchmark_suite_uses_valid_timbres():
    suite = get_benchmark_suite()
    assert all(s.timbre in {"pluck", "tone"} for s in suite)
    # guitar fixtures should use the plucked-string timbre and piano fixtures
    # the damped-tone timbre, matching the spec's stated instrument/timbre pairing.
    for spec in suite:
        if spec.instrument == "guitar":
            assert spec.timbre == "pluck"
        if spec.instrument == "piano":
            assert spec.timbre == "tone"


def test_get_benchmark_suite_total_audio_duration_is_bounded():
    suite = get_benchmark_suite()
    total = sum(max(onset_s + dur for _, onset_s, dur in s.notes) for s in suite)
    assert total < _MAX_TOTAL_AUDIO_SECONDS


def test_get_benchmark_suite_every_spec_has_notes():
    suite = get_benchmark_suite()
    assert all(len(s.notes) > 0 for s in suite)


def test_benchmark_suite_version_is_a_nonempty_string():
    assert isinstance(BENCHMARK_SUITE_VERSION, str)
    assert BENCHMARK_SUITE_VERSION

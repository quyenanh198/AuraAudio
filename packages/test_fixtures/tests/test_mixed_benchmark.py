from test_fixtures.mixed import MixedClipSpec
from test_fixtures.mixed_benchmark import MIXED_BENCHMARK_SUITE_VERSION, get_mixed_benchmark_suite


def test_get_mixed_benchmark_suite_returns_two_to_three_specs():
    suite = get_mixed_benchmark_suite()
    assert 2 <= len(suite) <= 3
    assert all(isinstance(s, MixedClipSpec) for s in suite)


def test_get_mixed_benchmark_suite_names_are_unique():
    suite = get_mixed_benchmark_suite()
    names = [s.name for s in suite]
    assert len(names) == len(set(names))


def test_get_mixed_benchmark_suite_covers_both_instruments():
    suite = get_mixed_benchmark_suite()
    instruments = {s.base.instrument for s in suite}
    assert instruments == {"guitar", "piano"}


def test_get_mixed_benchmark_suite_covers_both_interference_kinds():
    suite = get_mixed_benchmark_suite()
    kinds = {s.interference_kind for s in suite}
    assert "vocal_percussion" in kinds
    assert "pad" in kinds


def test_get_mixed_benchmark_suite_includes_a_real_piano_sample_fixture():
    """Per the hard constraint's own example: real-piano samples layered
    with synthesized interference, not synthetic piano timbre."""
    suite = get_mixed_benchmark_suite()
    piano_specs = [s for s in suite if s.base.instrument == "piano"]
    assert piano_specs
    assert all(s.base.renderer == "real_piano_sample" for s in piano_specs)


def test_mixed_benchmark_suite_version_is_a_nonempty_string():
    assert isinstance(MIXED_BENCHMARK_SUITE_VERSION, str)
    assert MIXED_BENCHMARK_SUITE_VERSION

from aura_worker.piano_hands import HandSplit, candidate_splits


def test_candidate_splits_single_pitch_gives_two_options():
    result = candidate_splits([60])
    assert len(result) == 2
    assert HandSplit(boundary=59, left=(), right=(60,)) in result
    assert HandSplit(boundary=61, left=(60,), right=()) in result


def test_candidate_splits_two_pitches_gives_three_options():
    result = candidate_splits([60, 72])
    assert len(result) == 3
    assert HandSplit(boundary=59, left=(), right=(60, 72)) in result
    assert HandSplit(boundary=66, left=(60,), right=(72,)) in result
    assert HandSplit(boundary=73, left=(60, 72), right=()) in result


def test_candidate_splits_sorts_unsorted_input():
    # verified directly: candidate_splits([72, 48, 60]) sorts internally to
    # [48, 60, 72] before splitting, regardless of input order
    result = candidate_splits([72, 48, 60])
    assert HandSplit(boundary=54, left=(48,), right=(60, 72)) in result


def test_candidate_splits_handles_duplicate_pitches():
    # two notes at the same pitch must not crash — position-based splitting
    # (not pitch-value matching) is required for this to behave sanely,
    # verified directly: this still produces exactly 3 candidates
    result = candidate_splits([60, 60])
    assert len(result) == 3

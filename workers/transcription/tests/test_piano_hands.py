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


from aura_worker.piano_hands import assign_measure


def _event(pitch: int, onset: str) -> dict:
    return {"pitch": pitch, "notatedOnset": onset}


def test_assign_measure_wide_two_hand_passage_stays_split():
    # A low bass pair followed by a high melody pair — verified directly:
    # movement cost (weight 1.0) dominates the tiny middle-C pull (0.05),
    # so each pair stays on its natural hand rather than oscillating.
    events = [
        _event(40, "0/1"), _event(43, "1/4"),
        _event(76, "1/2"), _event(79, "3/4"),
    ]
    assignment = assign_measure(events)
    assert assignment == {0: "left", 1: "left", 2: "right", 3: "right"}


def test_assign_measure_wide_chord_splits_at_span_minimizing_index():
    # A chord spanning two octaves (48, 60, 72) — verified directly: the
    # split that puts 48 alone on the left and 60+72 together on the right
    # minimizes combined span penalty (both hands land at exactly the
    # PREFERRED_MAX_SPAN boundary, zero penalty) and wins the tie over the
    # symmetric alternative (48+60 left, 72 right) via first-found order.
    events = [_event(48, "0/1"), _event(60, "0/1"), _event(72, "0/1")]
    assignment = assign_measure(events)
    assert assignment == {0: "left", 1: "right", 2: "right"}


def test_assign_measure_middle_c_is_a_weak_prior_not_a_hard_boundary():
    # A run starting clearly left-hand (50), then two notes straddling
    # middle C (58, 62) — verified directly: continuity keeps 58 on the
    # left hand even though it's below middle C's exact value, only
    # flipping to right at 62. A hard middle-C boundary would have split
    # right at 60 regardless of what came before; this doesn't.
    events = [_event(50, "0/1"), _event(58, "1/4"), _event(62, "1/2")]
    assignment = assign_measure(events)
    assert assignment == {0: "left", 1: "left", 2: "right"}


def test_assign_measure_skips_out_of_range_pitch():
    # A pitch below MIDI 21 contributes no state and must not break the
    # chain between the notes before and after it.
    events = [_event(50, "0/1"), _event(10, "1/4"), _event(55, "1/2")]
    assignment = assign_measure(events)
    assert assignment == {0: "left", 2: "right"}


def test_assign_measure_handles_duplicate_pitch_chord():
    # Two notes at the same pitch sharing an onset — position-based
    # splitting (not pitch-value matching) means this must not crash and
    # must still produce one "left" and one "right" (verified directly).
    events = [_event(60, "0/1"), _event(60, "0/1")]
    assignment = assign_measure(events)
    assert set(assignment.values()) == {"left", "right"}
    assert len(assignment) == 2


def test_assign_measure_handles_empty_events_safely():
    # A silent measure (quantize.py's silent-measure fidelity fix emits
    # {"number": n, "events": []}) must not break the assign stage's piano
    # DP — no groups, no steps, empty result, no crash.
    assert assign_measure([]) == {}


def test_assign_measure_left_never_exceeds_right_within_an_onset_property():
    # Spec Testing bullet 5 (matches ARCHITECTURE.md §9's property-testing
    # target, and sub-project 2's precedent of committing this directly
    # rather than only checking it ad hoc): for any onset, every assigned
    # left-hand pitch is <= every assigned right-hand pitch. Seeded for
    # reproducibility, matching sub-project 2's fix-wave property test style.
    import random

    rng = random.Random(42)
    for _ in range(500):
        chord_size = rng.randint(1, 8)
        pitches = [rng.randint(21, 108) for _ in range(chord_size)]
        events = [_event(p, "0/1") for p in pitches]
        assignment = assign_measure(events)
        left_pitches = [pitches[i] for i, hand in assignment.items() if hand == "left"]
        right_pitches = [pitches[i] for i, hand in assignment.items() if hand == "right"]
        if left_pitches and right_pitches:
            assert max(left_pitches) <= min(right_pitches)

import random

from aura_worker.fingering import StringFret, candidates_for_pitch, assign_chord


def test_candidates_for_open_low_e_string():
    candidates = candidates_for_pitch(40)  # open low E
    assert StringFret(string=0, fret=0) in candidates


def test_candidates_for_middle_c():
    candidates = candidates_for_pitch(60)
    # 60 - [40,45,50,55,59,64] = [20,15,10,5,1,-4] -> string 5 (64) is invalid (negative fret)
    expected = {
        StringFret(string=0, fret=20),
        StringFret(string=1, fret=15),
        StringFret(string=2, fret=10),
        StringFret(string=3, fret=5),
        StringFret(string=4, fret=1),
    }
    assert set(candidates) == expected


def test_candidates_for_unreachable_low_pitch():
    assert candidates_for_pitch(30) == []  # below open low E, unreachable on any string


def test_candidates_for_unreachable_high_pitch():
    assert candidates_for_pitch(90) == []  # above fret 20 on every string (64+20=84 max)


def test_assign_chord_gives_distinct_strings():
    # C major triad: C4=60, E4=64, G4=67
    result = assign_chord([60, 64, 67])
    assert all(sf is not None for sf in result)
    strings = [sf.string for sf in result]
    assert len(set(strings)) == len(strings)  # hard constraint: all distinct


def test_assign_chord_minimizes_hand_stretch():
    # Same C major triad — the optimal distinct-string assignment is
    # 60->string3/fret5, 64->string4/fret5, 67->string5/fret3 (stretch=2),
    # not e.g. 60->string4/fret1, 64->string5/fret0, 67->string3/fret12 (stretch=12).
    result = assign_chord([60, 64, 67])
    frets = [sf.fret for sf in result]
    assert max(frets) - min(frets) <= 2


def test_assign_chord_partial_when_too_many_pitches_for_strings():
    # 7 distinct pitches can't all get distinct strings (only 6 exist) —
    # exactly one must come back None, the other 6 must still be distinct.
    pitches = [40, 45, 50, 55, 59, 64, 41]  # 41 is reachable on strings 0 and... check candidates
    result = assign_chord(pitches)
    assigned = [sf for sf in result if sf is not None]
    assert len(assigned) == 6
    strings = [sf.string for sf in assigned]
    assert len(set(strings)) == 6


def test_assign_chord_prefers_low_frets_among_equal_stretch_options():
    # C major triad: C4=60, E4=64, G4=67. There are two distinct-string
    # assignments tied for minimal stretch (2): a high-position one around
    # frets 8-10, and a low-position one around frets 3-5. The low-position
    # option should win the tie-break, per the spec's "prefers lower, more
    # accessible frets" goal.
    result = assign_chord([60, 64, 67])
    assert result == [
        StringFret(string=3, fret=5),
        StringFret(string=4, fret=5),
        StringFret(string=5, fret=3),
    ]
    strings = [sf.string for sf in result]
    assert len(set(strings)) == len(strings)
    frets = [sf.fret for sf in result]
    assert max(frets) - min(frets) == 2  # still minimal stretch
    assert max(frets) <= 5  # low position, not the 8-10 high-position tie


def test_assign_chord_distinct_strings_property():
    # Property test (spec Testing bullet 5 / ARCHITECTURE.md §9): for any
    # randomly generated set of simultaneous pitches within guitar range and
    # count <= 6, the assigned strings (excluding nulls) must always be
    # distinct. Seeded for reproducibility; keep trial count modest so the
    # suite stays fast.
    rng = random.Random(42)
    for _ in range(750):
        count = rng.randint(1, 6)
        pitches = [rng.randint(30, 90) for _ in range(count)]
        result = assign_chord(pitches)
        assigned_strings = [sf.string for sf in result if sf is not None]
        assert len(set(assigned_strings)) == len(assigned_strings), (
            f"duplicate string assigned for pitches={pitches!r}, result={result!r}"
        )


def test_assign_chord_returns_none_for_unreachable_pitch_only():
    # One pitch is unreachable on any string; the other two should still
    # get a normal distinct-string assignment.
    result = assign_chord([20, 60, 64])  # 20 is unreachable (below open low E)
    assert result[0] is None
    assert result[1] is not None
    assert result[2] is not None
    assert result[1].string != result[2].string


from aura_worker.fingering import assign_measure


def _event(pitch: int, onset: str) -> dict:
    return {"pitch": pitch, "notatedOnset": onset}


def test_assign_measure_chromatic_run_prefers_staying_on_one_string():
    # A short chromatic run: fret movement is cheap on one string, but
    # switching strings costs STRING_CHANGE_PENALTY (2.0) — for adjacent
    # semitones, one string should almost always win.
    events = [
        _event(50, "0/1"), _event(51, "1/4"), _event(52, "1/2"), _event(53, "3/4"),
    ]
    assignment = assign_measure(events)
    assert len(assignment) == 4
    strings = [assignment[i].string for i in range(4)]
    assert len(set(strings)) == 1  # all four notes land on the same string


def test_assign_measure_prefers_low_frets_for_open_position_chord():
    # An open-position-friendly note sequence should resolve to low frets,
    # not push into the preferred-range penalty zone (fret > 12) when a
    # low-fret option exists.
    events = [_event(40, "0/1")]  # open low E has a fret-0 candidate
    assignment = assign_measure(events)
    assert assignment[0] == StringFret(string=0, fret=0)


def test_assign_measure_handles_chords_as_one_state():
    # Two chords in sequence (each sharing an onset) — both must resolve to
    # distinct-string assignments internally, and the function must not
    # crash treating a chord as a single note.
    events = [
        _event(60, "0/1"), _event(64, "0/1"), _event(67, "0/1"),  # chord 1, onset "0/1"
        _event(62, "1/1"), _event(65, "1/1"),                      # chord 2, onset "1/1"
    ]
    assignment = assign_measure(events)
    assert len(assignment) == 5
    chord1_strings = {assignment[i].string for i in range(3)}
    assert len(chord1_strings) == 3
    chord2_strings = {assignment[i].string for i in range(3, 5)}
    assert len(chord2_strings) == 2


def test_assign_measure_handles_empty_events_safely():
    # A silent measure (quantize.py's silent-measure fidelity fix emits
    # {"number": n, "events": []}) must not break the assign stage's guitar
    # DP — no groups, no steps, empty result, no crash.
    assert assign_measure([]) == {}


def test_assign_measure_skips_wholly_unreachable_chord():
    # A "chord" where every pitch is unreachable contributes no state and
    # must not break the chain between the notes before and after it.
    events = [
        _event(40, "0/1"),   # reachable
        _event(20, "1/4"),   # unreachable on any string
        _event(41, "1/2"),   # reachable
    ]
    assignment = assign_measure(events)
    assert 0 in assignment
    assert 1 not in assignment
    assert 2 in assignment

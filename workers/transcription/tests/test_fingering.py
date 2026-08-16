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


def test_assign_chord_returns_none_for_unreachable_pitch_only():
    # One pitch is unreachable on any string; the other two should still
    # get a normal distinct-string assignment.
    result = assign_chord([20, 60, 64])  # 20 is unreachable (below open low E)
    assert result[0] is None
    assert result[1] is not None
    assert result[2] is not None
    assert result[1].string != result[2].string

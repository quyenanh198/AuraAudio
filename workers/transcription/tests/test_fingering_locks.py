from aura_worker.fingering import StringFret, assign_measure as assign_guitar
from aura_worker.piano_hands import assign_measure as assign_piano


def _ev(pitch, onset="0/1"):
    return {"id": f"e{pitch}", "pitch": pitch, "notatedOnset": onset,
            "notatedDuration": "1/4", "voice": 1, "confidence": 0.9,
            "locked": False, "onsetSeconds": 0.0, "offsetSeconds": 0.5,
            "string": None, "fret": None, "hand": None}


def test_guitar_locked_note_keeps_its_placement():
    # candidates_for_pitch(52) = [SF(0,12), SF(1,7), SF(2,2)]. Unconstrained,
    # the DP picks SF(1,7) (cheapest path into the following note at 55) —
    # verified directly by running unconstrained first. Locking to SF(2,2),
    # a real (but non-chosen) candidate, must override that choice.
    events = [_ev(52, "0/1"), _ev(55, "1/4")]
    locked = {0: StringFret(string=2, fret=2)}
    unconstrained = assign_guitar(events)
    assert unconstrained[0] != locked[0]
    result = assign_guitar(events, locked=locked)
    assert result[0] == locked[0]
    assert 1 in result  # neighbor still assigned


def test_guitar_locked_chord_member_still_respected():
    # Chord (52, 57) sharing an onset. Unconstrained, assign_chord's optimal
    # distinct-string, min-stretch pick is (52->string2/fret2, 57->string3/
    # fret2) — verified directly. Locking note 0 to SF(0,12) — a real
    # candidate for pitch 52 that the unconstrained chord solver does NOT
    # choose — forces the fallback synthesis path (pin the lock, reassign
    # the rest around it) rather than merely confirming a value the
    # generator already produced.
    events = [_ev(52, "0/1"), _ev(57, "0/1")]  # chord
    locked = {0: StringFret(string=0, fret=12)}
    unconstrained = assign_guitar(events)
    assert unconstrained[0] != locked[0]
    result = assign_guitar(events, locked=locked)
    assert result[0] == locked[0]
    assert result[1].string != locked[0].string  # no string collision


def test_piano_locked_hand_respected():
    # Unconstrained, register places pitch 40 on the left hand — verified
    # directly before asserting the lock (to "right") wins, so this can't
    # pass vacuously if a future cost-weight tweak ever changes the default.
    events = [_ev(40, "0/1"), _ev(76, "1/4")]
    unconstrained = assign_piano(events)
    assert unconstrained[0] != "right"
    result = assign_piano(events, locked={0: "right"})  # against register intuition
    assert result[0] == "right"
    assert result[1] in ("left", "right")


def test_piano_locked_hands_force_synthesis_when_order_is_inverted():
    # A chord (48, 72) where the LOWER pitch is locked to the right hand and
    # the HIGHER pitch to the left — no contiguous prefix/suffix split of
    # sorted pitches (what candidate_splits ever produces) can satisfy that,
    # so this only passes if the fallback synthesis path pins both locks
    # directly instead of picking from the generated candidate list.
    events = [_ev(48, "0/1"), _ev(72, "0/1")]
    locked = {0: "right", 1: "left"}
    result = assign_piano(events, locked=locked)
    assert result[0] == "right"
    assert result[1] == "left"


def test_no_locks_matches_previous_behavior():
    events = [_ev(52, "0/1"), _ev(55, "1/4")]
    assert assign_guitar(events) == assign_guitar(events, locked=None)

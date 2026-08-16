from __future__ import annotations

from dataclasses import dataclass

OPEN_STRING_PITCHES = [40, 45, 50, 55, 59, 64]  # low E .. high E, internal index 0-5
MAX_FRET = 20

FRET_MOVE_WEIGHT = 1.0
STRING_CHANGE_PENALTY = 2.0
PREFERRED_MAX_FRET = 12
RANGE_PENALTY_WEIGHT = 0.5


@dataclass(frozen=True)
class StringFret:
    string: int
    fret: int


def candidates_for_pitch(pitch: int) -> list[StringFret]:
    result = []
    for string, open_pitch in enumerate(OPEN_STRING_PITCHES):
        fret = pitch - open_pitch
        if 0 <= fret <= MAX_FRET:
            result.append(StringFret(string=string, fret=fret))
    return result


def assign_chord(pitches: list[int]) -> list["StringFret | None"]:
    """Assign each pitch to a distinct string, maximizing how many pitches
    get assigned at all, then minimizing hand stretch (max fret - min fret)
    among the assigned ones. Exhaustive backtracking — chords are bounded by
    6 strings, so the search space is always small."""
    per_pitch_candidates = [candidates_for_pitch(p) for p in pitches]
    n = len(pitches)

    best_result: list[StringFret | None] = [None] * n
    best_count = -1
    best_stretch: float | None = None

    def backtrack(i: int, used_strings: set[int], current: list[StringFret | None]) -> None:
        nonlocal best_result, best_count, best_stretch
        if i == n:
            count = sum(1 for x in current if x is not None)
            frets = [x.fret for x in current if x is not None]
            stretch = (max(frets) - min(frets)) if frets else 0
            if count > best_count or (count == best_count and (best_stretch is None or stretch < best_stretch)):
                best_count = count
                best_stretch = stretch
                best_result = list(current)
            return

        # Option 1: leave this pitch unassigned.
        current.append(None)
        backtrack(i + 1, used_strings, current)
        current.pop()

        # Option 2: try each candidate string for this pitch.
        for cand in per_pitch_candidates[i]:
            if cand.string in used_strings:
                continue
            current.append(cand)
            used_strings.add(cand.string)
            backtrack(i + 1, used_strings, current)
            used_strings.discard(cand.string)
            current.pop()

    backtrack(0, set(), [])
    return best_result

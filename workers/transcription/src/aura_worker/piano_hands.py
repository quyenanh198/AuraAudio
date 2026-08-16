from __future__ import annotations

from dataclasses import dataclass

STANDARD_PIANO_RANGE = (21, 108)  # MIDI A0 (21) .. C8 (108), inclusive

SPLIT_MOVEMENT_WEIGHT = 1.0
HAND_SPAN_PENALTY_WEIGHT = 0.5
PREFERRED_MAX_SPAN = 12
MIDDLE_C_PULL_WEIGHT = 0.05
MIDDLE_C_MIDI = 60


@dataclass(frozen=True)
class HandSplit:
    boundary: float
    left: tuple[int, ...]
    right: tuple[int, ...]


def candidate_splits(pitches: list[int]) -> list[HandSplit]:
    """pitches: the MIDI pitches sharing one onset (already known to be
    within STANDARD_PIANO_RANGE — that filtering happens one layer up, in
    assign_measure). Every split of the sorted pitches into a lower
    (left-hand) group and an upper (right-hand) group is a valid candidate —
    unlike guitar frets, there is no "unreachable" case here."""
    sorted_pitches = sorted(pitches)
    k = len(sorted_pitches)
    result = []
    for i in range(k + 1):
        left = tuple(sorted_pitches[:i])
        right = tuple(sorted_pitches[i:])
        if i == 0:
            boundary = sorted_pitches[0] - 1
        elif i == k:
            boundary = sorted_pitches[-1] + 1
        else:
            boundary = (sorted_pitches[i - 1] + sorted_pitches[i]) / 2
        result.append(HandSplit(boundary=boundary, left=left, right=right))
    return result

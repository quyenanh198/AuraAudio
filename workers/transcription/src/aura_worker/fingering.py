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


def assign_chord(
    pitches: list[int], excluded_strings: frozenset[int] = frozenset()
) -> list["StringFret | None"]:
    """Assign each pitch to a distinct string, maximizing how many pitches
    get assigned at all, then minimizing hand stretch (max fret - min fret)
    among the assigned ones. Exhaustive backtracking — chords are bounded by
    6 strings, so the search space is always small.

    `excluded_strings` removes strings from consideration entirely (used to
    keep remaining chord members off strings already claimed by locked
    members — see `_options_for_group`). Empty by default, which reproduces
    prior behavior exactly."""
    per_pitch_candidates = [
        [c for c in candidates_for_pitch(p) if c.string not in excluded_strings]
        for p in pitches
    ]
    n = len(pitches)

    best_result: list[StringFret | None] = [None] * n
    best_count = -1
    best_stretch: float | None = None
    best_max_fret: int | None = None

    def backtrack(i: int, used_strings: set[int], current: list[StringFret | None]) -> None:
        nonlocal best_result, best_count, best_stretch, best_max_fret
        if i == n:
            count = sum(1 for x in current if x is not None)
            frets = [x.fret for x in current if x is not None]
            stretch = (max(frets) - min(frets)) if frets else 0
            max_fret = max(frets) if frets else 0
            is_better = count > best_count or (
                count == best_count
                and (
                    best_stretch is None
                    or stretch < best_stretch
                    or (stretch == best_stretch and (best_max_fret is None or max_fret < best_max_fret))
                )
            )
            if is_better:
                best_count = count
                best_stretch = stretch
                best_max_fret = max_fret
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


@dataclass
class _PlacementOption:
    representative: StringFret
    assignments: dict[int, StringFret]


def _transition_cost(prev: StringFret, curr: StringFret) -> float:
    cost = FRET_MOVE_WEIGHT * abs(curr.fret - prev.fret)
    if curr.string != prev.string:
        cost += STRING_CHANGE_PENALTY
    cost += RANGE_PENALTY_WEIGHT * max(0, curr.fret - PREFERRED_MAX_FRET)
    return cost


def _entry_cost(sf: StringFret) -> float:
    return RANGE_PENALTY_WEIGHT * max(0, sf.fret - PREFERRED_MAX_FRET)


def _measure_groups(events: list[dict]) -> list[list[int]]:
    """Group event indices by shared notatedOnset (a chord grouping),
    preserving first-seen order."""
    groups: dict[str, list[int]] = {}
    order: list[str] = []
    for i, ev in enumerate(events):
        onset = ev["notatedOnset"]
        if onset not in groups:
            groups[onset] = []
            order.append(onset)
        groups[onset].append(i)
    return [groups[o] for o in order]


def _options_for_group(
    events: list[dict],
    indices: list[int],
    locked: dict[int, StringFret] | None = None,
) -> list[_PlacementOption]:
    locked = locked or {}

    if len(indices) == 1:
        idx = indices[0]
        pitch = events[idx]["pitch"]
        candidates = candidates_for_pitch(pitch)
        if idx in locked:
            locked_sf = locked[idx]
            # Filter to the generated candidate matching the lock; if the
            # generator never proposed it (e.g. the locked fret is outside
            # this pitch's normal candidate set), synthesize it directly —
            # a lock always wins regardless of whether the DP would have
            # reached it on its own.
            candidates = [c for c in candidates if c == locked_sf]
            if not candidates:
                candidates = [locked_sf]
        return [
            _PlacementOption(representative=c, assignments={idx: c})
            for c in candidates
        ]

    pitches = [events[i]["pitch"] for i in indices]
    locked_members = {
        j: locked[indices[j]] for j in range(len(indices)) if indices[j] in locked
    }

    if not locked_members:
        chord_result = assign_chord(pitches)
    else:
        # Try the unconstrained chord solution first — if it already
        # happens to satisfy every lock, no synthesis is needed.
        chord_result = assign_chord(pitches)
        satisfies_locks = all(
            chord_result[j] == sf for j, sf in locked_members.items()
        )
        if not satisfies_locks:
            # Synthesize: pin the locked members to their locked strings,
            # and run the same per-chord assigner on the remaining members
            # with those strings excluded, so it can never reuse them.
            locked_strings = frozenset(sf.string for sf in locked_members.values())
            remaining_js = [j for j in range(len(indices)) if j not in locked_members]
            remaining_pitches = [pitches[j] for j in remaining_js]
            remaining_result = assign_chord(remaining_pitches, excluded_strings=locked_strings)
            chord_result = [None] * len(indices)
            for j, sf in locked_members.items():
                chord_result[j] = sf
            for k, j in enumerate(remaining_js):
                chord_result[j] = remaining_result[k]

    assignments = {
        indices[j]: sf for j, sf in enumerate(chord_result) if sf is not None
    }
    if not assignments:
        return []
    representative = min(assignments.values(), key=lambda sf: sf.fret)
    return [_PlacementOption(representative=representative, assignments=assignments)]


def assign_measure(
    events: list[dict], locked: dict[int, StringFret] | None = None
) -> dict[int, StringFret]:
    groups = _measure_groups(events)
    all_steps = [_options_for_group(events, idxs, locked) for idxs in groups]
    steps = [s for s in all_steps if s]  # drop wholly-unreachable groups

    result: dict[int, StringFret] = {}
    if not steps:
        return result

    # dp[i] = list of (cumulative_cost, backpointer_index_into_dp[i-1]) per option in steps[i]
    dp: list[list[tuple[float, int]]] = []
    for i, options in enumerate(steps):
        row: list[tuple[float, int]] = []
        if i == 0:
            for opt in options:
                row.append((_entry_cost(opt.representative), -1))
        else:
            prev_options = steps[i - 1]
            prev_row = dp[i - 1]
            for opt in options:
                best_cost = None
                best_j = -1
                for j, prev_opt in enumerate(prev_options):
                    cost = prev_row[j][0] + _transition_cost(prev_opt.representative, opt.representative)
                    if best_cost is None or cost < best_cost:
                        best_cost = cost
                        best_j = j
                row.append((best_cost, best_j))
        dp.append(row)

    last_row = dp[-1]
    best_final = min(range(len(last_row)), key=lambda k: last_row[k][0])

    chosen = [0] * len(steps)
    idx = best_final
    for i in range(len(steps) - 1, -1, -1):
        chosen[i] = idx
        idx = dp[i][idx][1]

    for i, options in enumerate(steps):
        result.update(options[chosen[i]].assignments)

    return result

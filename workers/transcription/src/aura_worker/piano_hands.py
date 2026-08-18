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


def _in_range(pitch: int) -> bool:
    return STANDARD_PIANO_RANGE[0] <= pitch <= STANDARD_PIANO_RANGE[1]


@dataclass
class _PlacementOption:
    split: HandSplit
    left_indices: list[int]
    right_indices: list[int]


def _span_penalty(split: HandSplit) -> float:
    left_span = (split.left[-1] - split.left[0]) if len(split.left) > 1 else 0
    right_span = (split.right[-1] - split.right[0]) if len(split.right) > 1 else 0
    return max(0, left_span - PREFERRED_MAX_SPAN) + max(0, right_span - PREFERRED_MAX_SPAN)


def _transition_cost(prev: HandSplit, curr: HandSplit) -> float:
    cost = SPLIT_MOVEMENT_WEIGHT * abs(curr.boundary - prev.boundary)
    cost += HAND_SPAN_PENALTY_WEIGHT * _span_penalty(curr)
    cost += MIDDLE_C_PULL_WEIGHT * abs(curr.boundary - MIDDLE_C_MIDI)
    return cost


def _entry_cost(split: HandSplit) -> float:
    cost = HAND_SPAN_PENALTY_WEIGHT * _span_penalty(split)
    cost += MIDDLE_C_PULL_WEIGHT * abs(split.boundary - MIDDLE_C_MIDI)
    return cost


def _group_by_onset(events: list[dict]) -> list[list[int]]:
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


def _synthesize_locked_split(
    events: list[dict],
    order: list[int],
    pitches: list[int],
    locked_here: dict[int, str],
) -> tuple[HandSplit, list[int], list[int]]:
    """Build one option that pins every locked index to its locked hand,
    used when no generated split satisfies all the locks (the locks imply a
    left/right partition that isn't a contiguous prefix/suffix of sorted
    pitch order, which candidate_splits can never produce).

    Remaining (unlocked) members are placed by comparing their pitch to a
    boundary — the same register-threshold rule candidate_splits uses to
    place a pitch relative to a split point — derived from the locked
    pitches themselves so unlocked members land on the side their register
    would naturally suggest, consistent with the locked assignments."""
    left_indices: list[int] = []
    right_indices: list[int] = []
    unlocked_order_positions: list[int] = []
    for pos, idx in enumerate(order):
        hand = locked_here.get(idx)
        if hand == "left":
            left_indices.append(idx)
        elif hand == "right":
            right_indices.append(idx)
        else:
            unlocked_order_positions.append(pos)

    left_locked_pitches = [events[i]["pitch"] for i in left_indices]
    right_locked_pitches = [events[i]["pitch"] for i in right_indices]
    if left_locked_pitches and right_locked_pitches:
        boundary = (max(left_locked_pitches) + min(right_locked_pitches)) / 2
    elif left_locked_pitches:
        boundary = max(left_locked_pitches) + 0.5
    elif right_locked_pitches:
        boundary = min(right_locked_pitches) - 0.5
    else:
        boundary = MIDDLE_C_MIDI

    for pos in unlocked_order_positions:
        idx = order[pos]
        pitch = pitches[pos]
        if pitch <= boundary:
            left_indices.append(idx)
        else:
            right_indices.append(idx)

    split = HandSplit(
        boundary=boundary,
        left=tuple(sorted(events[i]["pitch"] for i in left_indices)),
        right=tuple(sorted(events[i]["pitch"] for i in right_indices)),
    )
    return split, left_indices, right_indices


def _options_for_group(
    events: list[dict], indices: list[int], locked: dict[int, str] | None = None
) -> list[_PlacementOption]:
    locked = locked or {}
    in_range = [i for i in indices if _in_range(events[i]["pitch"])]
    if not in_range:
        return []
    # Sort by pitch (stable, so duplicate pitches keep their original
    # relative order) — splitting by POSITION in this sorted order, not by
    # matching pitch VALUES back to candidate_splits' left/right tuples,
    # is what makes duplicate-pitch chords split correctly.
    order = sorted(in_range, key=lambda i: events[i]["pitch"])
    pitches = [events[i]["pitch"] for i in order]
    splits = candidate_splits(pitches)
    options = []
    for k, split in enumerate(splits):
        options.append(_PlacementOption(split=split, left_indices=order[:k], right_indices=order[k:]))

    locked_here = {i: locked[i] for i in in_range if i in locked}
    if not locked_here:
        return options

    filtered = []
    for opt in options:
        left_set = set(opt.left_indices)
        right_set = set(opt.right_indices)
        satisfies = all(
            (idx in left_set) if hand == "left" else (idx in right_set)
            for idx, hand in locked_here.items()
        )
        if satisfies:
            filtered.append(opt)
    if filtered:
        return filtered

    split, left_indices, right_indices = _synthesize_locked_split(events, order, pitches, locked_here)
    return [_PlacementOption(split=split, left_indices=left_indices, right_indices=right_indices)]


def assign_measure(
    events: list[dict], locked: dict[int, str] | None = None
) -> dict[int, str]:
    groups = _group_by_onset(events)
    all_steps = [_options_for_group(events, idxs, locked) for idxs in groups]
    steps = [s for s in all_steps if s]  # drop wholly-out-of-range groups

    result: dict[int, str] = {}
    if not steps:
        return result

    # dp[i] = list of (cumulative_cost, backpointer_index_into_dp[i-1]) per option in steps[i]
    dp: list[list[tuple[float, int]]] = []
    for i, options in enumerate(steps):
        row: list[tuple[float, int]] = []
        if i == 0:
            for opt in options:
                row.append((_entry_cost(opt.split), -1))
        else:
            prev_options = steps[i - 1]
            prev_row = dp[i - 1]
            for opt in options:
                best_cost = None
                best_j = -1
                for j, prev_opt in enumerate(prev_options):
                    cost = prev_row[j][0] + _transition_cost(prev_opt.split, opt.split)
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
        opt = options[chosen[i]]
        for j in opt.left_indices:
            result[j] = "left"
        for j in opt.right_indices:
            result[j] = "right"

    return result

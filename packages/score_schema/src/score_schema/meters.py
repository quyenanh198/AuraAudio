"""Single source of truth for supported meters and meter math.

Mirrored by the frontend's METER_OPTIONS in
apps/desktop/web/src/lib/noteEdit.ts — both sides pin the list with
tests, so a drift on either side fails that side's suite.
"""
from __future__ import annotations

from fractions import Fraction

SUPPORTED_METERS: tuple[str, ...] = (
    "2/4", "3/4", "4/4", "5/4", "2/2", "3/8", "6/8", "7/8", "9/8", "12/8",
)

DETECTABLE_METERS: tuple[str, ...] = ("4/4", "3/4", "6/8", "2/4")


def beats_per_measure(meter: str) -> Fraction:
    """Measure length in quarter-note beats: num * 4 / den.

    Only meters in SUPPORTED_METERS are accepted; callers that surface
    user input validate first and turn ValueError into their own error.
    """
    if meter not in SUPPORTED_METERS:
        raise ValueError(f"unsupported meter: {meter!r}")
    num, den = meter.split("/")
    return Fraction(int(num) * 4, int(den))


def is_compound(meter: str) -> bool:
    """Compound meters group eighth notes in threes (6/8, 9/8, 12/8).

    3/8 is excluded even though 3 % 3 == 0: musically it is simple
    triple time (one dotted-quarter-equivalent group), not compound —
    compound top numbers are multiples of 3 greater than 3.
    """
    if meter not in SUPPORTED_METERS:
        raise ValueError(f"unsupported meter: {meter!r}")
    num, den = meter.split("/")
    return int(den) == 8 and int(num) % 3 == 0 and int(num) > 3


def notated_beats(meter: str) -> int:
    """Felt beats per measure: compound → numerator/3, simple → numerator."""
    num = int(meter.split("/")[0])
    return num // 3 if is_compound(meter) else num

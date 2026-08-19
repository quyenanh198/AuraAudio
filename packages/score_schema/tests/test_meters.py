from fractions import Fraction

import pytest

from score_schema.meters import (
    DETECTABLE_METERS,
    SUPPORTED_METERS,
    beats_per_measure,
    is_compound,
    notated_beats,
)

EXPECTED_ORDER = ("2/4", "3/4", "4/4", "5/4", "2/2", "3/8", "6/8", "7/8", "9/8", "12/8")


def test_supported_meters_exact_list_and_order():
    assert SUPPORTED_METERS == EXPECTED_ORDER


def test_detectable_meters_exact_and_subset():
    assert DETECTABLE_METERS == ("4/4", "3/4", "6/8", "2/4")
    assert set(DETECTABLE_METERS) <= set(SUPPORTED_METERS)


@pytest.mark.parametrize(
    ("meter", "beats"),
    [
        ("2/4", Fraction(2)), ("3/4", Fraction(3)), ("4/4", Fraction(4)),
        ("5/4", Fraction(5)), ("2/2", Fraction(4)), ("3/8", Fraction(3, 2)),
        ("6/8", Fraction(3)), ("7/8", Fraction(7, 2)), ("9/8", Fraction(9, 2)),
        ("12/8", Fraction(6)),
    ],
)
def test_beats_per_measure_all_supported(meter, beats):
    assert beats_per_measure(meter) == beats


@pytest.mark.parametrize("bad", ["13/16", "0/4", "4/3", "44", "", "6/8 ", "four/four"])
def test_beats_per_measure_rejects_unsupported(bad):
    with pytest.raises(ValueError):
        beats_per_measure(bad)


@pytest.mark.parametrize(
    ("meter", "compound"),
    [
        ("6/8", True), ("9/8", True), ("12/8", True),
        ("3/8", False), ("7/8", False), ("2/4", False), ("3/4", False),
        ("4/4", False), ("5/4", False), ("2/2", False),
    ],
)
def test_is_compound(meter, compound):
    assert is_compound(meter) is compound


@pytest.mark.parametrize(
    ("meter", "felt"),
    [
        ("6/8", 2), ("9/8", 3), ("12/8", 4),
        ("2/4", 2), ("3/4", 3), ("4/4", 4), ("5/4", 5), ("2/2", 2),
        ("3/8", 3), ("7/8", 7),
    ],
)
def test_notated_beats(meter, felt):
    assert notated_beats(meter) == felt

# Written with an SI tool (Claude, Anthropic) for NASA Space Apps 2026. See AI_USE.md.
"""Erosivity share on days without a live crop (plan 10.9 and 7.5). Synthetic, "example, fictitious"."""

import math
from datetime import date, timedelta
from fractions import Fraction

import pytest

from pipeline.adapters import base
from pipeline.decision import calendar as cal
from pipeline.decision import erosivity as ero

PROTOCOL = base.load_config()["protocol"]
FLAT = [100] * 12                    # synthetic: the same R every month


def days(a, b):
    a, b = date.fromisoformat(a), date.fromisoformat(b)
    return [a + timedelta(days=i) for i in range((b - a).days + 1)]


def col(*gaps):
    return [{"crop": "none", "start": a, "end": b, "source_id": "example_calendar", "state": "no_crop"} for a, b in gaps]


def test_protocol_band_is_five_points():
    assert PROTOCOL["erosion"]["similar_within_pct_points"] == 5


def test_fractions_sum_to_one_and_units_cancel():
    fr = ero.monthly_fractions([3, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1])
    assert sum(fr.values()) == 1 and fr[1] == Fraction(3, 14)
    assert ero.monthly_fractions([x * 1000 for x in range(1, 13)]) == ero.monthly_fractions(list(range(1, 13)))


def test_whole_season_holds_the_whole_year():
    fr = ero.monthly_fractions([1300, 1100, 900, 500, 300, 200, 150, 150, 300, 700, 1000, 1200])
    assert ero.share_on_days(cal.season_days("2026/27"), fr) == 1
    assert ero.share_on_days(cal.season_days("2027/28"), fr) == 1      # leap February


def test_share_is_the_monthly_fraction_over_the_days_of_the_month():
    fr = ero.monthly_fractions(FLAT)
    assert ero.share_on_days(days("2027-02-01", "2027-02-28"), fr) == Fraction(1, 12)
    assert ero.share_on_days(days("2027-02-01", "2027-02-14"), fr) == Fraction(1, 24)
    with pytest.raises(ero.ErosivityError):
        ero.share_on_days([date(2027, 2, 1), date(2027, 2, 1)], fr)


def test_classes_against_s1_with_exact_limits():
    five = Fraction(5, 100)
    assert ero.classify(Fraction(30, 100), Fraction(25, 100), 5) == "similar"     # exactly +5 points
    assert ero.classify(Fraction(20, 100), Fraction(25, 100), 5) == "similar"     # exactly -5 points
    assert ero.classify(Fraction(25, 100) + five + Fraction(1, 10**9), Fraction(25, 100), 5) == "higher"
    assert ero.classify(Fraction(25, 100) - five - Fraction(1, 10**9), Fraction(25, 100), 5) == "lower"
    # Float inputs that are not exact in binary still land on the right side.
    assert ero.classify(0.3, 0.25, 5) in ("similar", "higher")


def test_erosivity_block_has_only_the_four_words():
    cols = {
        "S1": col(("2027-08-01", "2027-08-31")),
        "S2": col(("2027-08-10", "2027-08-31")),
        "S3": col(("2027-02-01", "2027-04-30")),
    }
    out = ero.erosivity(cols, FLAT, similar_within_pct_points=5)
    assert out["classes"] == {"S1": "reference", "S2": "similar", "S3": "higher"}
    assert set(out["classes"].values()) <= set(ero.CLASSES)
    assert out["shares"]["S1"] == Fraction(1, 12)
    # Never t/ha: the block holds classes, and the shares are fractions of the year.
    assert all(0 <= s <= 1 for s in out["shares"].values())


def test_lower_when_the_gap_shrinks():
    cols = {"S1": col(("2026-12-01", "2027-02-28")), "S2": col(("2027-08-25", "2027-08-31")),
            "S3": col(("2026-12-01", "2027-02-28"))}
    out = ero.erosivity(cols, FLAT, similar_within_pct_points=5)
    assert out["classes"] == {"S1": "reference", "S2": "lower", "S3": "similar"}


@pytest.mark.parametrize("bad", [[1] * 11, [1] * 11 + [-1], [0] * 12, [1] * 11 + [math.nan], [1] * 11 + ["1"],
                                 {m: 1 for m in range(1, 12)}])
def test_bad_monthly_r(bad):
    with pytest.raises(ero.ErosivityError):
        ero.monthly_fractions(bad)


def test_sanity_warns_and_keeps_going():
    ok = ero.sanity(7900, tolerance=0.10)                  # tolerance: fictitious, [DECIDE]
    assert not ok["diverges"] and ok["reference"] == 7945
    off = ero.sanity(4000, tolerance=0.10)
    assert off["diverges"] and set(off) == {"pixel", "reference", "relative_difference", "diverges"}
    with pytest.raises(ero.ErosivityError):
        ero.sanity(7900, tolerance=-1)

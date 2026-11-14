# Written with an SI tool (Claude, Anthropic) for NASA Space Apps 2026. See AI_USE.md.
"""Civil dekads with both origins: ribbon (1 Sep) and Zarc table (1 Jan)."""

from datetime import date, timedelta

import pytest

from pipeline.common import dekad


def every_day(start: date, end: date):
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)


def test_dekad_in_month_edges():
    assert [dekad.dekad_in_month(x) for x in (1, 10, 11, 20, 21, 28, 31)] == [1, 1, 2, 2, 3, 3, 3]
    with pytest.raises(ValueError):
        dekad.dekad_in_month(0)


# Ribbon origin: 1 September.

def test_ribbon_first_and_last():
    assert dekad.ribbon_index(date(2025, 9, 1)) == 1
    assert dekad.ribbon_index(date(2025, 9, 10)) == 1
    assert dekad.ribbon_index(date(2025, 9, 11)) == 2
    assert dekad.ribbon_index(date(2025, 12, 31)) == 12
    assert dekad.ribbon_index(date(2026, 1, 1)) == 13
    assert dekad.ribbon_index(date(2026, 8, 21)) == 36
    assert dekad.ribbon_index(date(2026, 8, 31)) == 36


def test_ribbon_season_label():
    assert dekad.season_label(date(2025, 9, 1)) == "2025/26"
    assert dekad.season_label(date(2026, 8, 31)) == "2025/26"
    assert dekad.season_label(date(2026, 9, 1)) == "2026/27"
    assert dekad.season_label(date(1999, 12, 31)) == "1999/00"
    assert dekad.season_start_year("1999/00") == 1999
    with pytest.raises(ValueError):
        dekad.season_start_year("2025/27")


def test_ribbon_bounds_cross_the_year():
    assert dekad.ribbon_bounds(1, "2025/26") == (date(2025, 9, 1), date(2025, 9, 10))
    assert dekad.ribbon_bounds(12, "2025/26") == (date(2025, 12, 21), date(2025, 12, 31))
    assert dekad.ribbon_bounds(13, "2025/26") == (date(2026, 1, 1), date(2026, 1, 10))
    assert dekad.ribbon_bounds(18, "2025/26") == (date(2026, 2, 21), date(2026, 2, 28))
    assert dekad.ribbon_bounds(18, "2027/28") == (date(2028, 2, 21), date(2028, 2, 29))
    assert dekad.ribbon_bounds(36, "2025/26") == (date(2026, 8, 21), date(2026, 8, 31))


def test_ribbon_season_is_36_contiguous_dekads():
    for season in ("2023/24", "2025/26"):  # leap and common February
        y = dekad.season_start_year(season)
        spans = [dekad.ribbon_bounds(i, season) for i in range(1, 37)]
        assert spans[0][0] == date(y, 9, 1)
        assert spans[-1][1] == date(y + 1, 8, 31)
        for (_, end), (start, _) in zip(spans, spans[1:]):
            assert start == end + timedelta(days=1)
        days = sum((e - s).days + 1 for s, e in spans)
        assert days == (date(y + 1, 9, 1) - date(y, 9, 1)).days


def test_ribbon_index_matches_bounds_every_day():
    season = "2023/24"
    for d in every_day(date(2023, 9, 1), date(2024, 8, 31)):
        i = dekad.ribbon_index(d)
        lo, hi = dekad.ribbon_bounds(i, season)
        assert lo <= d <= hi
        assert dekad.season_label(d) == season


# Zarc origin: 1 January, dec1..dec36.

def test_zarc_first_and_last():
    assert dekad.zarc_index(date(2026, 1, 1)) == 1
    assert dekad.zarc_index(date(2026, 1, 10)) == 1
    assert dekad.zarc_index(date(2026, 2, 28)) == 6
    assert dekad.zarc_index(date(2026, 9, 1)) == 25
    assert dekad.zarc_index(date(2026, 12, 31)) == 36


def test_zarc_bounds():
    assert dekad.zarc_bounds(1, 2026) == (date(2026, 1, 1), date(2026, 1, 10))
    assert dekad.zarc_bounds(6, 2026) == (date(2026, 2, 21), date(2026, 2, 28))
    assert dekad.zarc_bounds(6, 2028) == (date(2028, 2, 21), date(2028, 2, 29))
    assert dekad.zarc_bounds(25, 2026) == (date(2026, 9, 1), date(2026, 9, 10))
    assert dekad.zarc_bounds(36, 2026) == (date(2026, 12, 21), date(2026, 12, 31))


def test_zarc_index_matches_bounds_every_day():
    for d in every_day(date(2024, 1, 1), date(2024, 12, 31)):
        lo, hi = dekad.zarc_bounds(dekad.zarc_index(d), 2024)
        assert lo <= d <= hi


# Both origins together.

def test_conversion_between_origins():
    assert dekad.zarc_to_ribbon(25) == 1     # 1-10 Sep
    assert dekad.zarc_to_ribbon(36) == 12    # 21-31 Dec
    assert dekad.zarc_to_ribbon(1) == 13     # 1-10 Jan
    assert dekad.zarc_to_ribbon(24) == 36    # 21-31 Aug
    assert dekad.ribbon_to_zarc(1) == 25
    assert dekad.ribbon_to_zarc(13) == 1
    for i in range(1, 37):
        assert dekad.ribbon_to_zarc(dekad.zarc_to_ribbon(i)) == i


def test_same_day_same_dekad_in_both_origins():
    for d in every_day(date(2025, 9, 1), date(2026, 8, 31)):
        assert dekad.zarc_to_ribbon(dekad.zarc_index(d)) == dekad.ribbon_index(d)
        r = dekad.ribbon_index(d)
        z = dekad.zarc_index(d)
        assert dekad.ribbon_bounds(r, "2025/26") == dekad.zarc_bounds(z, d.year)


def test_labels():
    assert dekad.label(1, dekad.ZARC_START_MONTH) == "01-10 Jan"
    assert dekad.label(6, dekad.ZARC_START_MONTH) == "21-28/29 Feb"
    assert dekad.label(1, dekad.RIBBON_START_MONTH) == "01-10 Sep"
    assert dekad.label(12, dekad.RIBBON_START_MONTH) == "21-31 Dec"


def test_invalid_index():
    for bad in (0, 37, -1):
        with pytest.raises(ValueError):
            dekad.zarc_bounds(bad, 2026)
    with pytest.raises(ValueError):
        dekad.index(date(2026, 1, 1), 13)

# Written with an SI tool (Claude, Anthropic) for NASA Space Apps 2026. See AI_USE.md.
"""L2 counts: dry autumns and cold in the window, years by name (plan 12.2 and 10.12).

Counts are "N of M", never a probability. Synthetic daily tables, "example, fictitious".
Thresholds and spans for dry autumn and the cold window are not in the protocol, so the
values here are fictitious test values ([DECIDE]).
"""

from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from pipeline.adapters import base
from pipeline.decision import risk_counts as rc
from pipeline.decision import strategies as st

PROTOCOL = base.load_config()["protocol"]
SPAN = ["04-01", "04-10"]


def daily(years, rain=1.0, tmin=10.0, spans=(("04-01", "04-10"), ("07-01", "07-10"))):
    idx, r, t = [], [], []
    for y in years:
        for a, b in spans:
            d0 = date(y, *map(int, a.split("-")))
            d1 = date(y, *map(int, b.split("-")))
            d = d0
            while d <= d1:
                idx.append(pd.Timestamp(d))
                r.append(rain(y, d) if callable(rain) else rain)
                t.append(tmin(y, d) if callable(tmin) else tmin)
                d += timedelta(days=1)
    return pd.DataFrame({"PRECTOTCORR": r, "T2M_MIN": t}, index=pd.DatetimeIndex(idx, name="date"))


def test_climate_years_from_the_protocol():
    years = rc.climate_years(PROTOCOL)
    assert (years.start, years.stop - 1, len(years)) == (1984, 2025, 42)


def test_k_in_10_rounds_half_up_exactly():
    assert rc.about_k_in_10(0, 42) == 0
    assert rc.about_k_in_10(42, 42) == 10
    assert rc.about_k_in_10(1, 4) == 3          # 2.5 rounds up
    assert rc.about_k_in_10(3, 40) == 1         # 0.75
    assert rc.about_k_in_10(2, 40) == 1         # 0.5 rounds up
    assert rc.about_k_in_10(1, 40) == 0         # 0.25
    assert rc.about_k_in_10(0, 0) is None
    for bad in ((5, 4), (-1, 4), (1.0, 4)):
        with pytest.raises(rc.RiskCountError):
            rc.about_k_in_10(*bad)


def test_dry_autumn_counts_years_below_the_threshold():
    dry_years = {1990, 2001, 2020}
    df = daily(range(1984, 2026), rain=lambda y, d: 0.5 if y in dry_years else 5.0)
    c = rc.dry_autumn(df, threshold_mm=30, span=SPAN, years=rc.climate_years(PROTOCOL))
    assert (c.n, c.m, c.years) == (3, 42, (1990, 2001, 2020))
    assert c.as_json() == {"n": 3, "m": 42, "years": [1990, 2001, 2020]}


def test_threshold_is_strict():
    df = daily([2000], rain=3.0)             # exactly 30 mm over 10 days
    assert rc.dry_autumn(df, threshold_mm=30, span=SPAN, years=[2000]).n == 0
    assert rc.dry_autumn(df, threshold_mm=30.1, span=SPAN, years=[2000]).n == 1


def test_cold_uses_the_protocol_minimum_strictly_below():
    t = PROTOCOL["frost"]["tmin_below_c"]
    assert t == 2
    df = daily([2000, 2001, 2002], tmin=lambda y, d: {2000: 2.0, 2001: 1.9, 2002: 8.0}[y] if d.day == 5 else 9.0)
    c = rc.cold_in_window(df, tmin_below_c=t, span=["07-01", "07-10"], years=[2000, 2001, 2002])
    assert (c.n, c.m, c.years) == (1, 3, (2001,))


def test_missing_day_leaves_the_year_out_of_m():
    df = daily([2000, 2001, 2002], rain=0.1)
    df.loc[pd.Timestamp("2001-04-05"), "PRECTOTCORR"] = np.nan
    df = df.drop(pd.Timestamp("2002-04-10"))
    c = rc.dry_autumn(df, threshold_mm=30, span=SPAN, years=[2000, 2001, 2002])
    assert (c.n, c.m, c.years, c.left_out) == (1, 1, (2000,), (2001, 2002))


def test_bad_spans_and_thresholds():
    df = daily([2000])
    for span in (["05-01", "04-01"], ["02-29", "03-01"], ["04-01"]):
        with pytest.raises(rc.RiskCountError):
            rc.dry_autumn(df, threshold_mm=30, span=span, years=[2001])
    for t in (0, -1, float("nan"), "30", True):
        with pytest.raises(rc.RiskCountError):
            rc.dry_autumn(df, threshold_mm=t, span=SPAN, years=[2000])
    with pytest.raises(rc.RiskCountError):
        rc.cold_in_window(df.drop(columns="T2M_MIN"), tmin_below_c=2, span=SPAN, years=[2000])


def test_fixed_texts_come_from_the_copy_files():
    c = rc.Count(n=8, m=42, years=(1985, 1999))
    for lang in ("en", "pt"):
        copy = st.load_copy(lang)
        txt = rc.power_text(c, lang)
        assert txt.startswith(copy["risk"]["power"].format(N=8, M=42, K=2))
        assert "1985, 1999" in txt
        assert rc.simulated_text(8, 42, lang) == copy["risk"]["simulated"].format(N=8, M=42, K=2)
    en = rc.power_text(c)
    assert en.startswith("POWER reanalysis, regional cell, 1984 to 2025: 8 of 42 years (about 2 in 10)")
    for t in (en, rc.simulated_text(8, 42)):
        assert "%" not in t and "probab" not in t.lower() and "chance" not in t.lower()
    with pytest.raises(rc.RiskCountError):
        rc.simulated_text(0, 0)


def test_always_ten_icons():
    assert rc.icons(rc.Count(n=8, m=42, years=())) == [True, True] + [False] * 8
    assert len(rc.icons(rc.Count(n=0, m=0, years=()))) == 10


def test_example_fixture_counts():
    from pathlib import Path
    fx = Path(__file__).parent / "fixtures" / "decision_example_daily.csv"
    df = pd.read_csv(fx, comment="#", index_col="date", parse_dates=True)
    c = rc.dry_autumn(df, threshold_mm=30, span=SPAN, years=rc.climate_years(PROTOCOL))
    assert c.left_out == (1990,) and c.m == 41


# Climate over the occupation (SA-D72.5, protocol.climate) ------------------------------------

def full_daily(first, last, rain=1.0, tmin=10.0):
    idx = pd.date_range(f"{first}-01-01", f"{last}-12-31", freq="D")
    r = [rain(d) if callable(rain) else rain for d in idx]
    t = [tmin(d) if callable(tmin) else tmin for d in idx]
    return pd.DataFrame({"PRECTOTCORR": r, "T2M_MIN": t}, index=pd.DatetimeIndex(idx, name="date"))


def test_protocol_climate_block():
    assert rc.protocol_years(PROTOCOL) == (1984, 2025)
    cl = PROTOCOL["climate"]
    assert cl["frost"]["variable"] == rc.TMIN and cl["rain"]["variable"] == rc.RAIN
    assert cl["frost"]["below_c"] == PROTOCOL["frost"]["tmin_below_c"] == 2


def test_occupation_periods_are_whole_and_named_by_season():
    corn = rc.occupation_periods("03-22", "09-19", (1984, 2025))
    assert len(corn) == 42
    assert corn[0] == ("1983/84", date(1984, 3, 22), date(1984, 9, 19))
    assert corn[-1][0] == "2024/25"
    # A period that crosses 31 Dec ends in the next year and must lie whole inside the years.
    soy = rc.occupation_periods("10-19", "02-27", (1984, 2025))
    assert soy[0] == ("1984/85", date(1984, 10, 19), date(1985, 2, 27)) and len(soy) == 41
    assert rc.season_label(date(1999, 9, 1)) == "1999/00" and rc.season_label(date(2000, 8, 31)) == "1999/00"
    with pytest.raises(rc.RiskCountError):
        rc.occupation_periods("3-22", "09-19", (1984, 2025))


def test_percentile_and_median():
    assert rc.p10_nearest_rank(list(range(1, 43))) == 5          # rank ceil(4.2) = 5
    assert rc.p10_nearest_rank([7.0, 3.0]) == 3.0 and rc.p10_nearest_rank([]) is None
    assert rc.p10_nearest_rank(list(range(1, 11))) == 1          # rank 1 of 10
    assert rc.median([3, 1, 2]) == 2 and rc.median([1, 2, 3, 4]) == 2.5 and rc.median([]) is None


def test_occupation_climate_counts_frost_and_rain():
    # 2 mm a day, except 1990 (dry, 0 mm) and 2000 (1 mm). Frost only on one day of 1995 and one of 2010.
    def rain(d):
        return 0.0 if d.year == 1990 else (1.0 if d.year == 2000 else 2.0)

    def tmin(d):
        if (d.year, d.month, d.day) in ((1995, 6, 1), (2010, 9, 19)):
            return 1.9
        if (d.year, d.month, d.day) == (2011, 9, 20):   # the day after the period: never counted
            return -3.0
        return 2.0                                       # 2 °C is not below 2 °C
    out = rc.occupation_climate(full_daily(1984, 2025, rain, tmin), start="03-22", end="09-19",
                                years=(1984, 2025), frost_below_c=2)
    days = (date(1984, 9, 19) - date(1984, 3, 22)).days + 1
    assert out["n"] == 42 and out["left_out"] == []
    assert out["frost"] == {"below_c": 2.0, "k": 2, "seasons": ["1994/95", "2009/10"]}
    assert out["rain"]["median_mm"] == 2.0 * days
    assert out["rain"]["p10_mm"] == 2.0 * days                   # rank 5 of 42: two dry seasons only
    assert out["rain"]["driest"] == [{"season": "1989/90", "mm": 0.0}, {"season": "1999/00", "mm": float(days)},
                                     {"season": "1983/84", "mm": 2.0 * days}]   # ties by season name


def test_occupation_climate_leaves_out_a_missing_day():
    df = full_daily(1984, 2025)
    df.loc["2001-05-05", "T2M_MIN"] = float("nan")
    df = df.drop(pd.Timestamp("2002-04-01"))
    out = rc.occupation_climate(df, start="03-22", end="09-19", years=(1984, 2025), frost_below_c=2)
    assert out["n"] == 40 and out["left_out"] == ["2000/01", "2001/02"]
    empty = rc.occupation_climate(df.iloc[:0], start="03-22", end="09-19", years=(1984, 2025), frost_below_c=2)
    assert empty["n"] == 0 and empty["rain"] == {"median_mm": None, "p10_mm": None, "driest": []}
    with pytest.raises(rc.RiskCountError):
        rc.occupation_climate(df.drop(columns="T2M_MIN"), start="03-22", end="09-19", years=(1984, 2025),
                              frost_below_c=2)

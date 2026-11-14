# Written with an SI tool (Claude, Anthropic) for NASA Space Apps 2026. See AI_USE.md.
"""P1, wrong year (section 11.2), on synthetic fixtures only.

B = 9 backward circular shifts, k = 4 to 36 by 4, in 1985 to 2024.
p = (1 + number of shifted S >= real S) / 10, tie against, pass with p <= 0.10
in T0 and in T1. All nine shifts or "not_completed".
"""

import copy
import json
from datetime import date, timedelta
from fractions import Fraction
from pathlib import Path

import pytest

from pipeline.bench import common, p1

FIX = Path(__file__).parent / "fixtures"
PROTOCOL = json.loads((FIX / "bench_protocol.json").read_text(encoding="utf-8"))
P1FX = json.loads((FIX / "bench_p1.json").read_text(encoding="utf-8"))
PLAN_K = [4, 8, 12, 16, 20, 24, 28, 32, 36]


def frac(pair):
    return Fraction(pair[0], pair[1])


@pytest.fixture
def design():
    return p1.P1Design.from_protocol(PROTOCOL)


# Design --------------------------------------------------------------------------

def test_design_is_the_plan(design):
    assert list(design.shifts) == PLAN_K
    assert list(p1.PLAN_SHIFTS_YEARS) == PLAN_K
    assert design.b == 9
    assert (design.first_year, design.last_year, design.span) == (1985, 2024, 40)
    assert design.alpha == Fraction(1, 10)
    assert design.climate == (date(1984, 1, 1), date(2025, 12, 31))


@pytest.mark.parametrize("mutate", [
    lambda p: p["tests"]["P1"].update(shifts_back_years=[4, 8, 12, 16, 20, 24, 28, 32]),
    lambda p: p["tests"]["P1"].update(shifts_back_years=[1, 2, 3, 4, 5, 6, 7, 8, 9]),
    lambda p: p["tests"]["P1"].update(shifts_back_years=list(reversed(PLAN_K))),
    lambda p: p["tests"]["P1"].update(circular_years=[1984, 2025]),
    lambda p: p["tests"]["P1"].update(ties="for"),
    lambda p: p["tests"]["P1"].update(draws="random"),
    lambda p: p["tests"]["P1"].update(statistic="kendall_tau"),
    lambda p: p["tests"]["P1"].update(B=10),
    lambda p: p["tests"]["P1"].pop("B"),
    lambda p: p["tests"]["P1"].update(alpha=0),
    lambda p: p["tests"]["P1"].pop("alpha"),
    lambda p: p["tests"]["P1"].pop("shifts_back_years"),
    lambda p: p["dates"].update(p1_circular_years=[1986, 2025]),
    lambda p: p["dates"].update(climate=["1984", "x"]),
])
def test_other_designs_are_refused(mutate):
    p = copy.deepcopy(PROTOCOL)
    mutate(p["protocol"])
    with pytest.raises(common.ProtocolError):
        p1.P1Design.from_protocol(p)


# Circular shift ------------------------------------------------------------------

@pytest.mark.parametrize("case", P1FX["years"], ids=lambda c: f"{c['year']}-{c['k']}")
def test_shift_year_cases(design, case):
    assert p1.shift_year(case["year"], case["k"], design) == case["source"]


@pytest.mark.parametrize("year", [1984, 2025, 1900])
def test_years_outside_the_span_are_refused(design, year):
    with pytest.raises(ValueError):
        p1.shift_year(year, 4, design)


def test_each_shift_is_a_permutation_without_fixed_years(design):
    years = list(range(1985, 2025))
    for k in design.shifts:
        mapped = [p1.shift_year(y, k, design) for y in years]
        assert sorted(mapped) == years
        assert all(m != y for m, y in zip(mapped, years))
        assert all(m == y - k or m == y - k + 40 for m, y in zip(mapped, years))


def test_offsets_are_multiples_of_four_and_keep_29_february(design):
    for k in design.shifts:
        for y in range(1985, 2025):
            off = p1.chain_offset(y, k, design)
            assert off % 4 == 0 and off in (-k, 40 - k)
    assert p1.source_date(date(2012, 2, 29), 2012, 4, design) == date(2008, 2, 29)
    assert p1.source_date(date(2012, 2, 29), 2012, 36, design) == date(2016, 2, 29)


def test_move_year_falls_back_to_28_february():
    assert p1.move_year(date(2024, 2, 29), 2023) == date(2023, 2, 28)
    assert p1.move_year(date(2024, 3, 1), 2023) == date(2023, 3, 1)


def test_whole_chain_moves_by_one_offset(design):
    # Start year 2012, k = 28: s = 1984 + 40 = 2024, offset +12 for every date,
    # so the chain that reaches 2013 takes 2025, still inside the series.
    d, prev = date(2011, 6, 1), None
    while d <= date(2013, 6, 30):
        src = p1.source_date(d, 2012, 28, design)
        assert src == date(d.year + 12, d.month, d.day)
        if prev is not None:
            assert (src - prev).days == 1
        prev, d = src, d + timedelta(days=1)


def test_every_site_season_has_its_own_offset(design):
    assert p1.chain_offset(2012, 28, design) == 12
    assert p1.chain_offset(2014, 28, design) == -28
    assert p1.chain_offset(2021, 4, design) == -4
    assert p1.chain_offset(1987, 4, design) == 36


def test_shifted_chain_outside_the_climate_series_is_refused(design):
    with pytest.raises(ValueError):
        p1.source_date(date(2014, 1, 1), 2012, 28, design)      # 2026
    assert p1.source_date(date(1988, 1, 1), 1989, 4, design) == date(1984, 1, 1)
    with pytest.raises(ValueError):
        p1.source_date(date(1987, 12, 31), 1989, 4, design)     # 1983


# p and decision ------------------------------------------------------------------

@pytest.mark.parametrize("case", P1FX["cases"], ids=lambda c: c["name"])
def test_p_cases(design, case):
    r = p1.evaluate_arm("T1", case["s_real"], dict(zip(PLAN_K, case["s_shifted"])), design)
    assert r.n_at_least == case["n_at_least"]
    assert r.p == frac(case["p"])
    assert r.status == case["status"]
    assert p1.p_value(case["s_real"], case["s_shifted"], design) == frac(case["p"])


def test_p_equal_to_0_10_passes_and_a_tie_fails(design):
    below = p1.evaluate_arm("T0", 21, dict(zip(PLAN_K, [19] * 9)), design)
    tie = p1.evaluate_arm("T0", 21, dict(zip(PLAN_K, [21] + [19] * 8)), design)
    assert below.p == Fraction(1, 10) and below.status == "pass"
    assert tie.p == Fraction(2, 10) and tie.status == "fail"


def test_missing_shift_is_not_completed_without_p(design):
    s = dict(zip(PLAN_K, [1] * 9))
    del s[36]
    r = p1.evaluate_arm("T1", 21, s, design)
    assert (r.status, r.p, r.missing_shifts) == ("not_completed", None, [36])
    s[36] = None
    assert p1.evaluate_arm("T1", 21, s, design).status == "not_completed"
    assert p1.evaluate_arm("T1", None, dict(zip(PLAN_K, [1] * 9)), design).status == "not_completed"


def test_shift_outside_the_design_is_refused(design):
    s = dict(zip(PLAN_K, [1] * 9))
    s[40] = 1
    with pytest.raises(ValueError):
        p1.evaluate_arm("T1", 21, s, design)
    with pytest.raises(ValueError):
        p1.p_value(21, [1] * 8, design)


@pytest.mark.parametrize("a, b, expected", [
    ("pass", "pass", "pass"),
    ("pass", "fail", "fail"),
    ("fail", "pass", "fail"),
    ("pass", "not_completed", "not_completed"),
    ("not_completed", "not_completed", "not_completed"),
    ("fail", "not_completed", "fail"),
])
def test_p1_needs_both_arms(design, a, b, expected):
    def arm(name, status):
        if status == "not_completed":
            return p1.evaluate_arm(name, 21, {}, design)
        return p1.evaluate_arm(name, 21, dict(zip(PLAN_K, [19 if status == "pass" else 23] * 9)), design)
    r = p1.combine(arm("T0", a), arm("T1", b))
    assert r.status == expected
    json.dumps(r.to_dict())


# Shifted runs to S ---------------------------------------------------------------

def test_shifted_runs_give_the_fixture_s_and_pass(design):
    it = P1FX["integration"]
    runs = {int(k): v["simulated"] for k, v in it["shifted"].items()}
    s = p1.shifted_s(it["observed"], runs, design)
    assert s == {int(k): v["s"] for k, v in it["shifted"].items()}
    r = p1.evaluate_arm("T1", it["s_real"], s, design)
    assert r.status == "pass" and r.p == Fraction(1, 10)


def test_incomplete_shifted_run_gives_none(design):
    it = P1FX["integration"]
    runs = {int(k): dict(v["simulated"]) for k, v in it["shifted"].items()}
    first_key = next(iter(it["observed"]))
    del runs[8][first_key]
    runs[12][first_key] = None
    del runs[16]
    s = p1.shifted_s(it["observed"], runs, design)
    assert s[8] is None and s[12] is None and s[16] is None
    assert p1.evaluate_arm("T1", it["s_real"], s, design).status == "not_completed"


def test_shifted_run_with_unknown_site_season_is_refused(design):
    it = P1FX["integration"]
    runs = {int(k): dict(v["simulated"]) for k, v in it["shifted"].items()}
    runs[4]["synthetic_site_z|2000/01"] = 1
    with pytest.raises(ValueError):
        p1.shifted_s(it["observed"], runs, design)

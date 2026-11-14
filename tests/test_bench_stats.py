# Written with an SI tool (Claude, Anthropic) for NASA Space Apps 2026. See AI_USE.md.
"""Bench statistics (criterion M4.5), on synthetic fixtures only.

Kendall S with ties worth zero, its exact null (n = 10: S = 21 gives tau 0.467
and one-sided p 0.036), RMSE and the sums of section 11.3.
"""

import itertools
import json
import math
from decimal import Decimal
from fractions import Fraction
from pathlib import Path

import pytest

from pipeline.bench import stats

FIX = Path(__file__).parent / "fixtures"
KENDALL = json.loads((FIX / "bench_kendall.json").read_text(encoding="utf-8"))
T1 = json.loads((FIX / "bench_t1.json").read_text(encoding="utf-8"))


def frac(pair):
    return Fraction(pair[0], pair[1])


def naive_s(x, y):
    sg = lambda v: (v > 0) - (v < 0)  # noqa: E731
    return sum(sg(x[i] - x[j]) * sg(y[i] - y[j]) for i, j in itertools.combinations(range(len(x)), 2))


# Exact numbers ------------------------------------------------------------------

def test_exact_reads_floats_by_their_decimal_form():
    assert stats.exact(279.9) == Fraction(2799, 10)
    assert stats.exact(0.1) == Fraction(1, 10)
    assert stats.exact("279,9") == Fraction(2799, 10)
    assert stats.exact(Decimal("3247.9")) == Fraction(32479, 10)
    assert stats.exact(7) == 7


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), None, True, "", "nan", "abc"])
def test_exact_rejects_non_numbers(bad):
    with pytest.raises((TypeError, ValueError)):
        stats.exact(bad)


# Kendall S ----------------------------------------------------------------------

@pytest.mark.parametrize("case", KENDALL["cases"], ids=lambda c: c["name"])
def test_kendall_s_fixture(case):
    assert stats.kendall_s(case["x"], case["y"]) == case["s"]


def test_kendall_boundary_cases_are_20_and_21():
    by_name = {c["name"]: c for c in KENDALL["cases"]}
    assert by_name["twelve_inversions_s21"]["s"] == 21
    assert by_name["one_tie_s20"]["s"] == 20
    assert by_name["thirteen_inversions_s19"]["s"] == 19


def test_tie_counts_zero():
    # One tied pair in x: that pair adds nothing, the other two are concordant.
    assert stats.kendall_s([1, 1, 2], [1, 2, 3]) == 2
    assert stats.kendall_s([1, 2, 3], [5, 5, 5]) == 0


def test_kendall_is_symmetric_and_flips_sign():
    x, y = [3, 1, 4, 1.5, 9, 2.6], [2, 7, 1, 8, 2.8, 1.8]
    assert stats.kendall_s(x, y) == stats.kendall_s(y, x) == naive_s(x, y)
    assert stats.kendall_s([-v for v in x], y) == -stats.kendall_s(x, y)


def test_kendall_lengths_must_match():
    with pytest.raises(ValueError):
        stats.kendall_s([1, 2], [1, 2, 3])


def test_tau_for_s21_is_0_467():
    tau = stats.kendall_tau_a(21, 10)
    assert tau == Fraction(21, 45)
    assert round(float(tau), 3) == 0.467


def test_s_by_group_keeps_order_and_counts_inside_each_group():
    groups = ["a", "a", "b", "b", "b"]
    x = [1, 2, 3, 2, 1]
    y = [1, 2, 1, 2, 3]
    assert stats.kendall_s_by_group(groups, x, y) == {"a": 1, "b": -3}


# Exact null distribution ------------------------------------------------------------

@pytest.mark.parametrize("n", range(0, 8))
def test_inversion_counts_match_brute_force(n):
    brute = {}
    for p in itertools.permutations(range(n)):
        k = sum(1 for i, j in itertools.combinations(range(n), 2) if p[i] > p[j])
        brute[k] = brute.get(k, 0) + 1
    counts = stats.inversion_counts(n)
    assert sum(counts) == math.factorial(n)
    assert {k: c for k, c in enumerate(counts) if c} == brute


def test_null_n10_matches_brute_force_fixture():
    expected = {int(k): v for k, v in KENDALL["null_n10_brute_force"].items()}
    assert stats.kendall_s_null(10) == expected


def test_without_ties_s_is_odd_for_n10():
    assert all(s % 2 == 1 for s in stats.kendall_s_null(10))


def test_p_upper_s21_is_0_036_and_s19_is_0_054():
    p21 = stats.kendall_p_upper(21, 10)
    p19 = stats.kendall_p_upper(19, 10)
    assert p21 == frac(KENDALL["null_n10_p_ge_21"])
    assert p19 == frac(KENDALL["null_n10_p_ge_19"])
    assert round(float(p21), 3) == 0.036
    assert round(float(p19), 3) == 0.054
    # S = 21 is the first value below 0.05 (section 11.3).
    assert p19 > Fraction(5, 100) > p21


def test_p_upper_edges():
    assert stats.kendall_p_upper(-45, 10) == 1
    assert stats.kendall_p_upper(45, 10) == Fraction(1, math.factorial(10))
    assert stats.kendall_p_upper(47, 10) == 0


# Sums and RMSE ------------------------------------------------------------------

def test_sums_of_synthetic_delta():
    obs = T1["observed_delta"]
    s = stats.sums(obs)
    assert s.n == 10
    assert s.total == sum(obs)
    assert s.total_sq == sum(v * v for v in obs)
    assert s.ss_about_mean == sum((Fraction(v) - s.mean) ** 2 for v in obs)
    assert s.mean == 200


def test_constant_null_rmse_is_exactly_280_in_the_fixture():
    obs = T1["observed_delta"]
    assert stats.mse_constant(obs) == frac(T1["mse_constant"]) == 280 ** 2
    assert stats.sqrt_float(stats.mse_constant(obs)) == 280.0
    # The constant itself reaches the reference exactly.
    assert stats.mse([200] * 10, obs) == stats.mse_constant(obs)


def test_zero_and_leave_one_out_nulls():
    obs = T1["observed_delta"]
    n = len(obs)
    assert stats.mse_zero(obs) == frac(T1["mse_zero"])
    loo = stats.mse_constant_loo(obs)
    assert loo == frac(T1["mse_constant_loo"])
    # Section 11.3: leave-one-out RMSE = in-sample RMSE x n / (n - 1).
    assert loo == stats.mse_constant(obs) * Fraction(n, n - 1) ** 2


def test_rmse_boundary_values_are_exact():
    obs = T1["observed_delta"]
    assert stats.mse([round(o + 279.9, 1) for o in obs], obs) == Fraction(2799, 10) ** 2
    assert stats.mse([o + 280 for o in obs], obs) == 280 ** 2
    assert stats.rmse([o + 280 for o in obs], obs) == 280.0


def test_count_at_least_counts_ties():
    assert stats.count_at_least([19, 21, 23, 5], 21) == 2
    assert stats.count_at_least([], 0) == 0

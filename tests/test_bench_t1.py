# Written with an SI tool (Claude, Anthropic) for NASA Space Apps 2026. See AI_USE.md.
"""T1 and T0 rules (sections 11.2 and 11.3), on synthetic fixtures only.

T1: S >= 21 and RMSE strictly below the in-sample constant, in 10
site-seasons, with S inside each site and the reason of a failure. T0: S >= 21
and the sum of S inside each site greater than zero.
"""

import copy
import json
from fractions import Fraction
from pathlib import Path

import pytest

from pipeline.bench import common, t0, t1
from pipeline.bench.common import Pair

FIX = Path(__file__).parent / "fixtures"
PROTOCOL = json.loads((FIX / "bench_protocol.json").read_text(encoding="utf-8"))
T1FX = json.loads((FIX / "bench_t1.json").read_text(encoding="utf-8"))
T0FX = json.loads((FIX / "bench_t0.json").read_text(encoding="utf-8"))


def frac(pair):
    return Fraction(pair[0], pair[1])


def pairs(fx, observed_key, simulated):
    return [Pair(site, season, o, s)
            for site, season, o, s in zip(fx["sites"], fx["seasons"], fx[observed_key], simulated)]


def scenario(fx, name):
    return next(s for s in fx["scenarios"] if s["name"] == name)


@pytest.fixture
def rule():
    return t1.T1Rule.from_protocol(PROTOCOL)


# Protocol ------------------------------------------------------------------------

def test_rule_comes_from_protocol(rule):
    assert rule.s_min == 21
    assert rule.n_site_seasons == 10
    assert rule.rmse_reference == "in_sample_constant_rmse"


def test_rule_accepts_the_bare_protocol_block():
    assert t1.T1Rule.from_protocol(PROTOCOL["protocol"]).s_min == 21


@pytest.mark.parametrize("mutate", [
    lambda p: p["protocol"]["tests"]["T1"].pop("s_total_min"),
    lambda p: p["protocol"].pop("min_site_seasons_for_label"),
    lambda p: p["protocol"]["tests"]["T1"].update(rmse_lt="loo_constant_rmse"),
    lambda p: p["protocol"]["tests"]["T1"].update(s_total_min="21"),
    lambda p: p["protocol"]["tests"]["T1"].update(s_total_min=46),
    lambda p: p["protocol"]["trial_site_seasons"]["synthetic_site_a"]["seasons"].pop(),
    lambda p: p["protocol"]["tests"].pop("T1"),
])
def test_bad_protocol_is_refused(mutate):
    p = copy.deepcopy(PROTOCOL)
    mutate(p)
    with pytest.raises(common.ProtocolError):
        t1.T1Rule.from_protocol(p)


# Scenarios -----------------------------------------------------------------------

@pytest.mark.parametrize("sc", T1FX["scenarios"], ids=lambda s: s["name"])
def test_t1_scenarios(rule, sc):
    r = t1.evaluate(pairs(T1FX, "observed_delta", sc["simulated"]), rule)
    assert r.s == sc["s"]
    assert r.mse == frac(sc["mse"])
    assert r.status == sc["status"]
    assert r.failed_on == sc["failed_on"]
    assert r.s_by_site == sc["s_by_site"]
    assert r.n == 10
    assert r.mse_threshold == r.mse_constant == frac(T1FX["mse_constant"])
    assert r.mse_constant_loo == frac(T1FX["mse_constant_loo"])
    assert r.mse_zero == frac(T1FX["mse_zero"])
    assert r.n_observed_positive == T1FX["n_observed_positive"]
    json.dumps(r.to_dict())


def test_s_boundary_20_fails_on_order_and_21_passes(rule):
    passed = t1.evaluate(pairs(T1FX, "observed_delta", scenario(T1FX, "s21_rmse_below")["simulated"]), rule)
    failed = t1.evaluate(pairs(T1FX, "observed_delta", scenario(T1FX, "s20_rmse_below")["simulated"]), rule)
    assert (passed.s, passed.status, passed.failed_on) == (21, "pass", None)
    assert (failed.s, failed.status, failed.failed_on) == (20, "fail", "order")


def test_rmse_boundary_279_9_passes_and_280_fails_on_size(rule):
    below = t1.evaluate(pairs(T1FX, "observed_delta", scenario(T1FX, "offset_279_9")["simulated"]), rule)
    at = t1.evaluate(pairs(T1FX, "observed_delta", scenario(T1FX, "offset_280")["simulated"]), rule)
    assert below.rmse == pytest.approx(279.9) and below.rmse_threshold == 280.0
    assert (below.status, below.failed_on) == ("pass", None)
    assert at.rmse == 280.0
    assert (at.status, at.failed_on) == ("fail", "size")


def test_the_constant_fails_both_conditions(rule):
    r = t1.evaluate(pairs(T1FX, "observed_delta", [200] * 10), rule)
    assert r.s == 0 and r.mse == r.mse_threshold
    assert (r.status, r.failed_on) == ("fail", "both")


@pytest.mark.parametrize("s, mse, expected", [
    (21, Fraction(2799, 10) ** 2, ("pass", None)),
    (21, Fraction(280) ** 2, ("fail", "size")),
    (20, Fraction(2799, 10) ** 2, ("fail", "order")),
    (20, Fraction(280) ** 2, ("fail", "both")),
    (45, Fraction(0), ("pass", None)),
    (-45, Fraction(10 ** 9), ("fail", "both")),
])
def test_decide_at_the_exact_limits(rule, s, mse, expected):
    status, fo, _, _ = t1.decide(s, mse, Fraction(280) ** 2, 10, rule)
    assert (status, fo) == expected


def test_threshold_uses_observed_values_only(rule):
    a = t1.evaluate(pairs(T1FX, "observed_delta", scenario(T1FX, "s21_rmse_below")["simulated"]), rule)
    b = t1.evaluate(pairs(T1FX, "observed_delta", scenario(T1FX, "offset_280")["simulated"]), rule)
    assert a.mse_threshold == b.mse_threshold


def test_fewer_site_seasons_is_exploratory_never_pass(rule):
    sim = scenario(T1FX, "s21_rmse_below")["simulated"]
    r = t1.evaluate(pairs(T1FX, "observed_delta", sim)[:9], rule)
    assert r.n == 9
    assert r.status == "exploratory"
    assert r.failed_on is None
    assert r.notes


def test_more_site_seasons_than_the_protocol_is_refused(rule):
    ps = pairs(T1FX, "observed_delta", T1FX["observed_delta"])
    ps.append(Pair("synthetic_site_c", "2013/14", 1, 1))
    with pytest.raises(ValueError):
        t1.evaluate(ps, rule)


def test_missing_simulated_value_is_incomplete_input(rule):
    sim = list(scenario(T1FX, "s21_rmse_below")["simulated"])
    sim[3] = None
    with pytest.raises(common.IncompleteInput):
        t1.evaluate(pairs(T1FX, "observed_delta", sim), rule)
    sim[3] = float("nan")
    with pytest.raises(common.IncompleteInput):
        t1.evaluate(pairs(T1FX, "observed_delta", sim), rule)


def test_duplicate_site_season_is_refused(rule):
    ps = pairs(T1FX, "observed_delta", T1FX["observed_delta"])
    ps[1] = Pair(ps[0].site, ps[0].season, ps[1].observed, ps[1].simulated)
    with pytest.raises(ValueError):
        t1.evaluate(ps, rule)


def test_zeroed_engine_yield_counts_against_the_model(rule):
    # An engine outcome arrives as a number and is scored, never skipped.
    sim = list(scenario(T1FX, "offset_279_9")["simulated"])
    sim[0] = 0
    r = t1.evaluate(pairs(T1FX, "observed_delta", sim), rule)
    assert r.n == 10 and r.status == "fail"


# T0 ------------------------------------------------------------------------------

@pytest.fixture
def rule0():
    return t0.T0Rule.from_protocol(PROTOCOL)


def test_t0_rule_comes_from_protocol(rule0):
    assert (rule0.s_min, rule0.n_site_seasons, rule0.within_site_sum_gt) == (21, 10, 0)


@pytest.mark.parametrize("mutate", [
    lambda p: p["protocol"]["tests"]["T0"].pop("s_total_min"),
    lambda p: p["protocol"]["tests"]["T0"].pop("within_site_s_sum_gt"),
    lambda p: p["protocol"]["tests"]["T0"].update(within_site_s_sum_gt="0"),
])
def test_t0_bad_protocol_is_refused(mutate):
    p = copy.deepcopy(PROTOCOL)
    mutate(p)
    with pytest.raises(common.ProtocolError):
        t0.T0Rule.from_protocol(p)


@pytest.mark.parametrize("sc", T0FX["scenarios"], ids=lambda s: s["name"])
def test_t0_scenarios(rule0, sc):
    r = t0.evaluate(pairs(T0FX, "observed", sc["simulated"]), rule0)
    assert r.s == sc["s"]
    assert r.s_by_site == sc["s_by_site"]
    assert r.within_site_sum == sc["within_site_sum"]
    assert r.status == sc["status"]
    json.dumps(r.to_dict())


@pytest.mark.parametrize("s, within, expected", [
    (21, 1, "pass"), (21, 0, "fail"), (20, 12, "fail"), (21, -12, "fail"), (45, 12, "pass"),
])
def test_t0_decide_limits(rule0, s, within, expected):
    assert t0.decide(s, within, 10, rule0)[0] == expected


def test_t0_fewer_site_seasons_is_exploratory(rule0):
    sim = scenario(T0FX, "perfect")["simulated"]
    assert t0.evaluate(pairs(T0FX, "observed", sim)[:9], rule0).status == "exploratory"

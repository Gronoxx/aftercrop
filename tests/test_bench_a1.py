# Written with an SI tool (Claude, Anthropic) for NASA Space Apps 2026. See AI_USE.md.
"""A1, with and without carried-over water (section 11.2), on synthetic fixtures.

D = S with state minus S without state. Paired swap over the 1,024 patterns,
p = patterns with D* >= D / 1,024, tie against, pass with D > 0 and p <= 0.10.
"""

import copy
import itertools
import json
from fractions import Fraction
from pathlib import Path

import pytest

from pipeline.bench import a1, common

FIX = Path(__file__).parent / "fixtures"
PROTOCOL = json.loads((FIX / "bench_protocol.json").read_text(encoding="utf-8"))
A1FX = json.loads((FIX / "bench_a1.json").read_text(encoding="utf-8"))


def frac(pair):
    return Fraction(pair[0], pair[1])


def records(case, n=10):
    return [a1.A1Record(site, season, o, w, wo)
            for site, season, o, w, wo in zip(A1FX["sites"], A1FX["seasons"], A1FX["observed_delta"],
                                              case["with_state"], case["without_state"])][:n]


def case(name):
    return next(c for c in A1FX["cases"] if c["name"] == name)


@pytest.fixture
def rule():
    return a1.A1Rule.from_protocol(PROTOCOL)


def test_rule_from_protocol(rule):
    assert rule.alpha == Fraction(1, 10)
    assert rule.n_site_seasons == 10
    assert rule.n_patterns == 1024


@pytest.mark.parametrize("mutate", [
    lambda s: s["permutation"].update(patterns=512),
    lambda s: s["permutation"].update(kind="random"),
    lambda s: s["permutation"].update(exhaustive=False),
    lambda s: s.update(ties="for"),
    lambda s: s.update(draws="random"),
    lambda s: s.pop("alpha"),
    lambda s: s.update(alpha=1.5),
    lambda s: s.pop("permutation"),
])
def test_bad_protocol_is_refused(mutate):
    p = copy.deepcopy(PROTOCOL)
    mutate(p["protocol"]["tests"]["A1"])
    with pytest.raises(common.ProtocolError):
        a1.A1Rule.from_protocol(p)


@pytest.mark.parametrize("c", A1FX["cases"], ids=lambda c: c["name"])
def test_a1_cases(rule, c):
    r = a1.evaluate(records(c), rule)
    assert r.n_patterns == 1024
    assert r.d == c["d"]
    assert r.s_with_state == c["s_with_state"]
    assert r.s_without_state == c["s_without_state"]
    assert r.n_at_least == c["n_at_least"]
    assert r.p == frac(c["p"])
    assert r.status == c["status"]
    json.dumps(r.to_dict())


def test_p_boundary_102_passes_and_103_fails(rule):
    assert a1.evaluate(records(case("count_102_pass")), rule).status == "pass"
    assert a1.evaluate(records(case("count_103_fail")), rule).status == "fail"
    assert Fraction(102, 1024) <= Fraction(1, 10) < Fraction(103, 1024)


@pytest.mark.parametrize("d, n_ge, expected", [
    (18, 102, "pass"), (18, 103, "fail"), (1, 1, "pass"), (0, 1, "fail"), (-4, 1, "fail"), (35, 1024, "fail"),
])
def test_decide_limits(rule, d, n_ge, expected):
    assert a1.decide(d, n_ge, 10, rule)[0] == expected


def test_ties_count_against(rule):
    # Only one site-season differs between the arms: half of the patterns tie
    # with the real D. Counted, p = 0.5; not counted, it would be 0.
    r = a1.evaluate(records(case("one_difference_ties_against")), rule)
    assert r.d > 0
    assert r.n_at_least == 512 and r.status == "fail"


def test_identical_arms_give_d_zero_and_fail(rule):
    r = a1.evaluate(records(case("identical_arms_d_zero")), rule)
    assert (r.d, r.n_at_least, r.p, r.status) == (0, 1024, 1, "fail")


def test_distribution_matches_a_naive_enumeration_and_is_symmetric():
    c = case("count_5_pass")
    obs, w, wo = A1FX["observed_delta"], c["with_state"], c["without_state"]
    dist = a1.swap_distribution(obs, w, wo)
    assert len(dist) == 1024
    assert dist[0] == c["d"]
    full = (1 << 10) - 1
    assert all(dist[m] == -dist[full ^ m] for m in range(1024))

    def s(x, y):
        sg = lambda v: (v > 0) - (v < 0)  # noqa: E731
        return sum(sg(x[i] - x[j]) * sg(y[i] - y[j]) for i, j in itertools.combinations(range(len(x)), 2))
    naive = sorted(
        s([wo[i] if b else w[i] for i, b in enumerate(bits)], obs)
        - s([w[i] if b else wo[i] for i, b in enumerate(bits)], obs)
        for bits in itertools.product((0, 1), repeat=10)
    )
    assert sorted(dist) == naive


def test_no_random_draw(rule):
    c = case("count_102_pass")
    assert a1.evaluate(records(c), rule) == a1.evaluate(records(c), rule)


def test_fewer_site_seasons_is_exploratory(rule):
    r = a1.evaluate(records(case("count_5_pass"), n=9), rule)
    assert r.n_patterns == 512 and r.status == "exploratory"


def test_missing_value_is_incomplete_input(rule):
    c = copy.deepcopy(case("count_5_pass"))
    c["without_state"][2] = None
    with pytest.raises(common.IncompleteInput):
        a1.evaluate(records(c), rule)

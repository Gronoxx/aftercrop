# Written with an SI tool (Claude, Anthropic) for NASA Space Apps 2026. See AI_USE.md.
"""N1 noise floor (sections 11.2 and 11.5) and the exact percentile behind it,
on synthetic disagreements labelled "example, fictitious".

The floor is the larger of the percentiles of field halves and of L30 against
S30, in days. The protocol names the 90th percentile only in words and no
method, so the tests pass both as arguments [DECIDE].
"""

import copy
import itertools
import json
from datetime import date
from fractions import Fraction
from pathlib import Path

import numpy as np
import pytest

from pipeline.bench import common, n1, stats

FIX = Path(__file__).parent / "fixtures"
PROTOCOL = json.loads((FIX / "bench_protocol.json").read_text(encoding="utf-8"))
N1FX = json.loads((FIX / "bench_n1.json").read_text(encoding="utf-8"))


def val(x):
    return Fraction(*x) if isinstance(x, list) else Fraction(x)


def rule(method):
    return n1.N1Rule.from_protocol(PROTOCOL, percentile=N1FX["percentile"], method=method)


# Percentile ----------------------------------------------------------------------

def test_nearest_rank_is_an_observed_value():
    v = list(range(1, 11))
    assert stats.percentile(v, 90, "nearest_rank") == 9
    assert stats.percentile(v, 10, "nearest_rank") == 1
    assert stats.percentile([5], 90, "nearest_rank") == 5
    assert stats.percentile([3, 1, 2, 5, 4], 90, "nearest_rank") == 5


@pytest.mark.parametrize("n, q", list(itertools.product([1, 2, 5, 10, 41], [10, 50, 90])))
def test_linear_matches_numpy(n, q):
    v = [(i * 37) % 23 - 7.5 for i in range(n)]
    assert float(stats.percentile(v, q, "linear")) == pytest.approx(float(np.percentile(v, q)), abs=1e-12)


def test_percentile_refuses_bad_input():
    with pytest.raises(ValueError):
        stats.percentile([], 90, "nearest_rank")
    with pytest.raises(ValueError):
        stats.percentile([1], 100, "nearest_rank")
    with pytest.raises(ValueError):
        stats.percentile([1], 90, "type_6")


def test_median():
    assert stats.median([3, 1, 2]) == 2
    assert stats.median([4, 1, 3, 2]) == Fraction(5, 2)
    with pytest.raises(ValueError):
        stats.median([])


# Rule ------------------------------------------------------------------------------

def test_rule_needs_level_and_method_until_the_protocol_holds_them():
    with pytest.raises(common.ProtocolError):
        n1.N1Rule.from_protocol(PROTOCOL)
    with pytest.raises(common.ProtocolError):
        n1.N1Rule.from_protocol(PROTOCOL, percentile=90)
    r = rule("nearest_rank")
    assert (r.percentile, r.method, r.unit) == (90, "nearest_rank", "days")


def test_protocol_value_wins_and_a_different_argument_is_refused():
    p = copy.deepcopy(PROTOCOL)
    p["protocol"]["tests"]["N1"].update(percentile=90, percentile_method="linear")
    assert n1.N1Rule.from_protocol(p).method == "linear"
    assert n1.N1Rule.from_protocol(p, percentile=90, method="linear").percentile == 90
    with pytest.raises(common.ProtocolError):
        n1.N1Rule.from_protocol(p, method="nearest_rank")


@pytest.mark.parametrize("mutate", [
    lambda s: s.update(unit="hours"),
    lambda s: s.pop("floor"),
    lambda s: s.update(percentile=100),
    lambda s: s.update(percentile_method="mean"),
])
def test_bad_protocol_is_refused(mutate):
    p = copy.deepcopy(PROTOCOL)
    p["protocol"]["tests"]["N1"].update(percentile=90, percentile_method="nearest_rank")
    mutate(p["protocol"]["tests"]["N1"])
    with pytest.raises(common.ProtocolError):
        n1.N1Rule.from_protocol(p)


# Floor -------------------------------------------------------------------------------

@pytest.mark.parametrize("method", ["nearest_rank", "linear"])
def test_floor_is_the_larger_of_the_two(method):
    exp = N1FX["expected"][method]
    r = n1.evaluate(N1FX["halves_days"], N1FX["l30_vs_s30_days"], rule(method))
    assert r.status == "reported"
    assert r.p_halves == val(exp["halves"])
    assert r.p_sensors == val(exp["l30_vs_s30"])
    assert r.floor_days == val(exp["floor"])
    assert r.set_by == exp["set_by"]
    d = r.to_dict()
    assert (d["n_halves"], d["n_l30_vs_s30"], d["n"]) == (10, 5, 15)
    assert d["threshold"] is None
    json.dumps(d)


def test_signed_differences_are_read_as_sizes():
    signed = [-x for x in N1FX["halves_days"]]
    a = n1.evaluate(signed, N1FX["l30_vs_s30_days"], rule("nearest_rank"))
    assert a.p_halves == 6


def test_one_null_missing_leaves_n1_not_completed():
    r = n1.evaluate(N1FX["halves_days"], [], rule("nearest_rank"))
    assert r.status == "not_completed"
    assert r.floor_days is None and r.set_by is None
    assert r.p_halves == 6
    assert "no pairs of L30 against S30" in r.notes


def test_missing_value_is_incomplete_input():
    with pytest.raises(common.IncompleteInput):
        n1.evaluate([1, None], [1], rule("nearest_rank"))


def test_disagreement_days():
    assert n1.disagreement_days(date(2025, 2, 10), date(2025, 2, 3)) == 7
    assert n1.disagreement_days(date(2025, 2, 3), date(2025, 2, 10)) == 7

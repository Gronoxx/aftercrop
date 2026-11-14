# Written with an SI tool (Claude, Anthropic) for NASA Space Apps 2026. See AI_USE.md.
"""A2 rain and A3 HLS ablation (sections 8.6 and 11.2), on synthetic values
labelled "example, fictitious".

A2 compares L2 counts with PRECTOTCORR and IMERG only in years inside the
common period, with no threshold. A3 counts the field and option states that
change without HLS; zero changes gives "context only".
"""

import copy
import json
from datetime import date
from pathlib import Path

import pytest

from pipeline.bench import ablation, common

FIX = Path(__file__).parent / "fixtures"
PROTOCOL = json.loads((FIX / "bench_protocol.json").read_text(encoding="utf-8"))
AFX = json.loads((FIX / "bench_ablation.json").read_text(encoding="utf-8"))
SCENARIOS = ["example_low", "example_mid", "example_high"]


def l2_count(name="dry_autumn"):
    c = AFX["a2"][name]
    first, last = c["window_months"]
    years = {int(y) for y in c["main"]} | {int(y) for y in c["check"]}
    windows = {y: (date(y, first, 1), date(y, last, 31)) for y in years}
    return ablation.L2Count(name, {int(k): v for k, v in c["main"].items()},
                            {int(k): v for k, v in c["check"].items()}, windows)


@pytest.fixture
def a2rule():
    return ablation.A2Rule.from_protocol(PROTOCOL)


@pytest.fixture
def a3rule():
    return ablation.A3Rule.from_protocol(PROTOCOL)


def test_a2_rule_from_protocol(a2rule):
    assert a2rule.common_period == (date(1998, 1, 1), date(2025, 9, 30))


@pytest.mark.parametrize("mutate", [
    lambda p: p["tests"]["A2"].update(threshold=0.1),
    lambda p: p["tests"]["A2"].update(role="key"),
    lambda p: p["dates"].pop("a2_common_period"),
    lambda p: p["dates"].update(a2_common_period=["2025-09-30", "1998-01-01"]),
])
def test_a2_bad_protocol_is_refused(mutate):
    p = copy.deepcopy(PROTOCOL)
    mutate(p["protocol"])
    with pytest.raises(common.ProtocolError):
        ablation.A2Rule.from_protocol(p)


def test_a2_compares_only_inside_the_common_period(a2rule):
    r = ablation.evaluate_a2([l2_count()], a2rule)
    assert r.status == "reported"
    c = r.counts[0]
    # 1997 is before IMERG; the 2025 autumn ends in May, inside the period, and counts.
    assert c.years == [1998, 1999, 2000, 2024, 2025]
    assert c.years_left_out == [1997]
    assert c.years_main == [1999, 2024]
    assert c.years_check == [1998, 1999, 2025]
    assert (c.only_main, c.only_check) == ([2024], [1998, 2025])
    d = r.to_dict()
    assert d["threshold"] is None
    assert d["counts"][0]["n_changed"] == 3
    assert d["n"] == 5
    json.dumps(d)


def test_a2_year_with_one_rain_or_window_past_the_period_is_left_out(a2rule):
    c = l2_count()
    windows = dict(c.windows)
    windows[2025] = (date(2025, 9, 1), date(2025, 10, 31))
    main = dict(c.main)
    del main[2000]
    r = ablation.evaluate_a2([ablation.L2Count(c.id, main, c.check, windows)], a2rule)
    assert r.counts[0].years == [1998, 1999, 2024]
    assert r.counts[0].years_left_out == [1997, 2000, 2025]


def test_a2_without_any_comparable_year_is_refused(a2rule):
    c = ablation.L2Count("dry_autumn", {1990: True}, {1990: True}, {1990: (date(1990, 3, 1), date(1990, 5, 31))})
    with pytest.raises(ValueError):
        ablation.evaluate_a2([c], a2rule)
    with pytest.raises(ValueError):
        ablation.evaluate_a2([], a2rule)


def test_a2_flag_must_be_boolean(a2rule):
    c = l2_count()
    main = dict(c.main)
    main[1999] = None
    with pytest.raises(common.IncompleteInput):
        ablation.evaluate_a2([ablation.L2Count(c.id, main, c.check, c.windows)], a2rule)


def test_a3_rule_from_protocol(a3rule):
    assert a3rule.zero_changes == "context only"
    p = copy.deepcopy(PROTOCOL)
    p["protocol"]["tests"]["A3"]["zero_changes"] = "minor"
    with pytest.raises(common.ProtocolError):
        ablation.A3Rule.from_protocol(p)


def test_a3_counts_states_that_change(a3rule):
    r = ablation.evaluate_a3(AFX["a3"]["with"], AFX["a3"]["without_changes"], a3rule,
                             expected_scenarios=SCENARIOS)
    assert r.status == "reported"
    assert (r.states_changed, r.states_total) == (2, 12)
    assert r.verdict == "changes_answers"
    assert r.by_scenario == {"example_low": {"changed": 1, "total": 4},
                             "example_mid": {"changed": 0, "total": 4},
                             "example_high": {"changed": 1, "total": 4}}
    assert {(c["scenario"], c["state"]) for c in r.changed} == {("example_low", "field_a|S3"),
                                                                ("example_high", "field_b|S3")}
    src = r.ablation_source()
    assert set(src) == {"id", "swap", "status", "states_changed", "states_total", "verdict"}
    json.dumps(r.to_dict())


def test_a3_zero_changes_is_context_only(a3rule):
    same = copy.deepcopy(AFX["a3"]["with"])
    r = ablation.evaluate_a3(AFX["a3"]["with"], same, a3rule)
    assert (r.states_changed, r.verdict) == (0, "context_only")


def test_a3_tuple_keys_and_not_run_source(a3rule):
    w = {"example_mid": {("field_a", "S3"): "try_strip"}}
    wo = {"example_mid": {("field_a", "S3"): "tied"}}
    r = ablation.evaluate_a3(w, wo, a3rule)
    assert r.changed[0]["state"] == "field_a/S3"
    assert ablation.not_run_source("hls_vi_v2", "without HLS")["verdict"] == "not_run"


@pytest.mark.parametrize("mutate", [
    lambda w, wo: wo.pop("example_mid"),
    lambda w, wo: wo["example_low"].pop("field_a|S2"),
    lambda w, wo: wo["example_low"].update({"field_a|S2": None}),
    lambda w, wo: (w.clear(), wo.clear()),
])
def test_a3_mismatched_or_missing_states_are_refused(a3rule, mutate):
    w, wo = copy.deepcopy(AFX["a3"]["with"]), copy.deepcopy(AFX["a3"]["without_changes"])
    mutate(w, wo)
    with pytest.raises(ValueError):
        ablation.evaluate_a3(w, wo, a3rule)


def test_a3_scenarios_must_be_the_expected_ones(a3rule):
    with pytest.raises(ValueError):
        ablation.evaluate_a3(AFX["a3"]["with"], AFX["a3"]["with"], a3rule, expected_scenarios=["example_low"])

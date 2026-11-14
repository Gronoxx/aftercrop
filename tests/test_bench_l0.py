# Written with an SI tool (Claude, Anthropic) for NASA Space Apps 2026. See AI_USE.md.
"""L0 sanity (sections 10.6 and 11.2), on synthetic fixtures labelled
"example, fictitious".

Closure within 1 mm at every swap (1 mm passes), exact repeat, no artifact
left unexplained. The CN and REW change of Delta is reported with no threshold.
"""

import copy
import json
from fractions import Fraction
from pathlib import Path

import pytest

from pipeline.bench import common, l0

FIX = Path(__file__).parent / "fixtures"
PROTOCOL = json.loads((FIX / "bench_protocol.json").read_text(encoding="utf-8"))
L0FX = json.loads((FIX / "bench_l0.json").read_text(encoding="utf-8"))


@pytest.fixture
def rule():
    return l0.L0Rule.from_protocol(PROTOCOL)


def artifacts(explained=True):
    found = []
    for s in L0FX["seasons"]:
        found += l0.detect_artifacts(s["arm"], s["season"], dry_yield_t_ha=s["dry_yield_t_ha"],
                                     crop_dead=s["crop_dead"], days_to_emergence=s["days_to_emergence"])
    if not explained:
        return found
    texts = {tuple(k.split("|")): v for k, v in L0FX["explanations"].items()}
    return l0.explain(found, texts)


def run(gaps=None, second=None, arts=None, rule=None):
    return l0.evaluate(gaps or L0FX["gaps_mm"], L0FX["run"], L0FX["run"] if second is None else second,
                       artifacts() if arts is None else arts, rule)


def test_rule_from_protocol(rule):
    assert rule.closure_mm == 1
    assert rule.repeat == "exact"
    assert rule.max_unexplained == 0
    assert rule.artifact_kinds == ("days_to_emergence", "zeroed_season", "dead_canopy")


@pytest.mark.parametrize("mutate", [
    lambda p: p["tests"]["L0"].pop("closure_mm_per_swap"),
    lambda p: p["tests"]["L0"].update(repeat="close"),
    lambda p: p["tests"]["L0"].update(unexplained_artifacts=-1),
    lambda p: p["tests"]["L0"].update(closure_mm_per_swap=-1),
    lambda p: p["chains"].pop("artifact_checks"),
    lambda p: p["tests"].pop("L0"),
])
def test_bad_protocol_is_refused(mutate):
    p = copy.deepcopy(PROTOCOL)
    mutate(p["protocol"])
    with pytest.raises(common.ProtocolError):
        l0.L0Rule.from_protocol(p)


def test_all_clear_passes_and_1_mm_counts_as_closed(rule):
    r = run(rule=rule)
    assert r.status == "pass" and r.failed_on == []
    assert r.max_gap_mm == 1 and r.n_gaps_over == 0
    assert r.n_swaps == 14
    assert r.repeat_ok and r.digest_first == r.digest_second
    assert len(r.artifacts) == 3 and r.n_unexplained == 0
    d = r.to_dict()
    assert d["blocks_delta"] is False
    json.dumps(d)


def test_gap_just_above_1_mm_fails_on_closure(rule):
    gaps = copy.deepcopy(L0FX["gaps_mm"])
    gaps["S3 run 1"][1] = 1.001
    r = run(gaps=gaps, rule=rule)
    assert (r.status, r.failed_on, r.n_gaps_over) == ("fail", ["closure"], 1)
    assert r.max_gap_mm == Fraction("1.001")
    assert r.to_dict()["blocks_delta"] is True


def test_negative_gap_is_read_as_its_size(rule):
    gaps = copy.deepcopy(L0FX["gaps_mm"])
    gaps["S1 run 1"][0] = -1.5
    assert run(gaps=gaps, rule=rule).failed_on == ["closure"]


def test_any_change_between_runs_fails_on_repeat(rule):
    second = copy.deepcopy(L0FX["run"])
    second["S3"]["2019/20"]["soy_kg_ha"] = 3305.2500000000005
    r = run(second=second, rule=rule)
    assert (r.status, r.failed_on) == ("fail", ["repeat"])
    assert r.digest_first != r.digest_second


def test_repeat_ignores_key_order_and_nan_equals_nan(rule):
    a = {"x": float("nan"), "y": [1.0, 2.0]}
    b = {"y": [1.0, 2.0], "x": float("nan")}
    assert l0.evaluate({"c": [0]}, a, b, [], rule).repeat_ok


def test_unexplained_artifact_fails(rule):
    r = run(arts=artifacts(explained=False), rule=rule)
    assert (r.status, r.failed_on) == ("fail", ["artifacts"])
    assert r.n_unexplained == 3


def test_all_three_failures_are_listed(rule):
    gaps = {"S1 run 1": [2.0]}
    second = {"other": 1}
    r = run(gaps=gaps, second=second, arts=artifacts(explained=False), rule=rule)
    assert r.failed_on == ["closure", "repeat", "artifacts"]


def test_detect_artifacts():
    kinds = {a.kind for a in artifacts(explained=False)}
    assert kinds == {"zeroed_season", "dead_canopy", "days_to_emergence"}
    late = l0.detect_artifacts("S1", "x", dry_yield_t_ha=3, crop_dead=False, days_to_emergence=30,
                               max_days_to_emergence=20)
    assert [a.kind for a in late] == ["days_to_emergence"]
    assert l0.detect_artifacts("S1", "x", dry_yield_t_ha=3, crop_dead=False, days_to_emergence=30) == []
    with pytest.raises(common.IncompleteInput):
        l0.detect_artifacts("S1", "x", dry_yield_t_ha=None, crop_dead=False, days_to_emergence=5)


def test_explanation_for_an_artifact_not_found_is_refused():
    with pytest.raises(ValueError):
        l0.explain(artifacts(explained=False), {("S1", "2019/20", "dead_canopy"): "no such artifact"})


def test_artifact_kind_outside_protocol_is_refused(rule):
    odd = [l0.Artifact("S1", "2019/20", "lodging", explanation="x")]
    with pytest.raises(ValueError):
        run(arts=odd, rule=rule)


def test_missing_gap_is_incomplete_and_no_swap_is_refused(rule):
    with pytest.raises(common.IncompleteInput):
        run(gaps={"S1 run 1": [0.1, None]}, rule=rule)
    with pytest.raises(ValueError):
        l0.evaluate({"S1 run 1": []}, {}, {}, [], rule)


def test_cn_rew_variation_is_reported_only():
    c = L0FX["cn_rew"]
    v = l0.cn_rew_variation(c["computed"], c["fixed_61_9"])
    assert v["threshold"] is None
    assert v["by_season"]["2019/20"]["difference"] == -25.75
    assert v["max_abs_difference"] == 30.0
    with pytest.raises(ValueError):
        l0.cn_rew_variation(c["computed"], {"2019/20": 1})

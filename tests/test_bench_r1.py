# Written with an SI tool (Claude, Anthropic) for NASA Space Apps 2026. See AI_USE.md.
"""R1 robustness (section 12.4), on synthetic Delta labelled "example,
fictitious". No engine run, no trial value.

Runs only if T1 passes. Paired Latin hypercube from protocol.seed, band from
the pooled Delta, inversion within 20%, L = the larger of 166 and the T1 RMSE,
three states. The band levels and the percentile method are test values,
passed as arguments while the protocol lacks them [DECIDE].
"""

import copy
import hashlib
import json
from datetime import date
from fractions import Fraction
from pathlib import Path

import pytest

from pipeline.bench import common, r1

FIX = Path(__file__).parent / "fixtures"
PROTOCOL = json.loads((FIX / "bench_protocol.json").read_text(encoding="utf-8"))
R1FX = json.loads((FIX / "bench_r1.json").read_text(encoding="utf-8"))
SEASONS = [f"{y}/{(y + 1) % 100:02d}" for y in range(2010, 2018)]
RMSE_280_SQ = Fraction(280) ** 2
REFERENCE = 3000   # fictitious soybean after corn, kg/ha


@pytest.fixture
def rule():
    return r1.R1Rule.from_protocol(PROTOCOL, band=R1FX["band_percentiles"], method=R1FX["percentile_method"])


def run_with(delta_by_season):
    return {y: {"S1": REFERENCE, "S3": REFERENCE + d} for y, d in zip(SEASONS, delta_by_season)}


def field(member_deltas, base_delta, field_id="field_a"):
    """One field whose member m has the same Delta in every season."""
    members = {m: run_with([d] * len(SEASONS)) for m, d in enumerate(member_deltas)}
    return r1.R1Field(field_id, run_with([base_delta] * len(SEASONS)), members)


def evaluate(fields, rule, t1_mse=RMSE_280_SQ, t1_status="pass", members=None):
    return r1.evaluate(fields, rule, t1_status=t1_status, t1_mse=t1_mse, members=members)


# Protocol --------------------------------------------------------------------------

def test_rule_from_protocol(rule):
    assert (rule.members, rule.cut_to, rule.seed) == (50, 20, 20261114)
    assert rule.inversion_max == Fraction(1, 5)
    assert rule.equivalence == 166
    assert rule.band == (10, 90) and rule.method == "nearest_rank"
    assert [d.name for d in rule.dimensions] == R1FX["dimensions"]


def test_dimensions_follow_the_protocol_ranges(rule):
    dims = {d.name: d for d in rule.dimensions}
    assert (dims["Zmax_m"].kind, dims["Zmax_m"].low, dims["Zmax_m"].high) == ("uniform", Fraction(2, 5), 1)
    assert (dims["HI0.maize"].low, dims["HI0.maize"].high) == (Fraction(2, 5), Fraction(53, 100))
    assert (dims["HI0.soybean"].kind, dims["HI0.soybean"].low, dims["HI0.soybean"].high) == (
        "factor", Fraction(9, 10), Fraction(11, 10))
    assert (dims["senescence"].kind, dims["available_water"].kind) == ("factor", "class")
    assert (dims["available_water"].low, dims["available_water"].high) == (1, 6)
    assert dims["sowing.wheat"].kind == dims["sowing.maize"].kind == "interval"


def test_draw_order_does_not_depend_on_key_order(rule):
    p = copy.deepcopy(PROTOCOL)
    ranges = p["protocol"]["r1"]["ranges"]
    p["protocol"]["r1"]["ranges"] = dict(reversed(list(ranges.items())))
    other = r1.R1Rule.from_protocol(p, band=[10, 90], method="nearest_rank")
    assert r1.design(other).points == r1.design(rule).points


def test_band_and_method_are_needed_until_the_protocol_holds_them():
    with pytest.raises(common.ProtocolError):
        r1.R1Rule.from_protocol(PROTOCOL)
    with pytest.raises(common.ProtocolError):
        r1.R1Rule.from_protocol(PROTOCOL, band=[10, 90])
    p = copy.deepcopy(PROTOCOL)
    p["protocol"]["r1"].update(band_percentiles=[10, 90], percentile_method="linear")
    assert r1.R1Rule.from_protocol(p).method == "linear"
    with pytest.raises(common.ProtocolError):
        r1.R1Rule.from_protocol(p, band=[5, 95])


@pytest.mark.parametrize("mutate", [
    lambda p: p["r1"].update(runs_only_if="always"),
    lambda p: p["r1"].update(sampling="random"),
    lambda p: p["r1"].update(distribution="normal"),
    lambda p: p["r1"].update(paired=False),
    lambda p: p["r1"].update(cut_to=60),
    lambda p: p["r1"].update(inversion_max=1.2),
    lambda p: p["r1"].update(equivalence_kg_ha=0),
    lambda p: p["r1"]["states"].pop("inconclusive"),
    lambda p: p["r1"]["ranges"].update(CCx=[0.99, 0.85]),
    lambda p: p["r1"]["ranges"].update(CCx=7),
    lambda p: p.pop("seed"),
    lambda p: p.pop("r1"),
])
def test_bad_protocol_is_refused(mutate):
    p = copy.deepcopy(PROTOCOL)
    mutate(p["protocol"])
    with pytest.raises(common.ProtocolError):
        r1.R1Rule.from_protocol(p, band=[10, 90], method="nearest_rank")


@pytest.mark.parametrize("band", [[90, 10], [0, 90], [10, 100]])
def test_bad_band_is_refused(band):
    with pytest.raises(common.ProtocolError):
        r1.R1Rule.from_protocol(PROTOCOL, band=band, method="nearest_rank")


# Design --------------------------------------------------------------------------

def _sha(design):
    return hashlib.sha256(json.dumps([list(p) for p in design.points]).encode()).hexdigest()


def test_design_is_pinned_by_the_seed(rule):
    d = r1.design(rule)
    assert d.members == 50 and len(d.dimensions) == 12
    assert list(d.points[0]) == R1FX["members_50"]["first_member"]
    assert list(d.points[-1]) == R1FX["members_50"]["last_member"]
    assert _sha(d) == R1FX["members_50"]["sha256"]
    d20 = r1.design(rule, members=20)
    assert list(d20.points[0]) == R1FX["members_20"]["first_member"]
    assert _sha(d20) == R1FX["members_20"]["sha256"]
    json.dumps(d.to_dict())


@pytest.mark.parametrize("n, d", [(50, 12), (20, 12), (7, 3), (1, 1)])
def test_one_point_per_stratum_in_every_dimension(n, d):
    pts = r1.latin_hypercube(n, d, 20261114)
    assert len(pts) == n
    for j in range(d):
        column = [p[j] for p in pts]
        assert all(0 <= u < 1 for u in column)
        assert sorted(int(u * n) for u in column) == list(range(n))


def test_same_seed_same_design_other_seed_other_design():
    assert r1.latin_hypercube(10, 4, 1) == r1.latin_hypercube(10, 4, 1)
    assert r1.latin_hypercube(10, 4, 1) != r1.latin_hypercube(10, 4, 2)


def test_only_the_protocol_member_counts_are_accepted(rule):
    with pytest.raises(common.ProtocolError):
        r1.design(rule, members=30)
    second_cut = r1.design(rule, members=20, only=["Zmax_m", "available_water"])
    assert [x.name for x in second_cut.dimensions] == ["Zmax_m", "available_water"]
    with pytest.raises(ValueError):
        r1.design(rule, only=["LAI"])


def test_member_parameters_are_paired_across_seasons(rule):
    d = r1.design(rule)
    a = d.parameters(3, estimate_class=4, intervals={"sowing.maize": (date(2019, 2, 1), date(2019, 2, 20)),
                                                       "sowing.wheat": (date(2019, 4, 1), date(2019, 5, 20))})
    b = d.parameters(3, estimate_class=4, intervals={"sowing.maize": (date(2020, 2, 1), date(2020, 2, 20)),
                                                       "sowing.wheat": (date(2020, 4, 1), date(2020, 5, 20))})
    u = d.unit(3)
    assert 0.4 <= a["Zmax_m"] <= 1.0 and 0.9 <= a["HI0.soybean"] <= 1.1
    assert a["available_water"] in (3, 4, 5)
    for k in a:
        if k.startswith("sowing"):
            assert (a[k].month, a[k].day) == (b[k].month, b[k].day)   # same draw, each season's interval
        else:
            assert a[k] == b[k]
    assert a["Zmax_m"] == pytest.approx(0.4 + u["Zmax_m"] * 0.6)


def test_value_mapping_at_the_edges():
    cls = r1.Dimension("available_water", "class", Fraction(1), Fraction(6))
    assert r1.value(cls, 0.0, estimate_class=1) == 1 and r1.value(cls, 0.99, estimate_class=1) == 2
    assert r1.value(cls, 0.0, estimate_class=6) == 5 and r1.value(cls, 0.99, estimate_class=6) == 6
    assert r1.value(cls, 0.5, estimate_class=3) == 3
    with pytest.raises(ValueError):
        r1.value(cls, 0.5, estimate_class=7)
    iv = r1.Dimension("sowing.wheat", "interval")
    assert r1.value(iv, 0.0, interval=(date(2020, 4, 1), date(2020, 5, 20))) == date(2020, 4, 1)
    assert r1.value(iv, 0.9999, interval=(date(2020, 4, 1), date(2020, 5, 20))) == date(2020, 5, 20)
    with pytest.raises(ValueError):
        r1.value(iv, 0.5)
    with pytest.raises(ValueError):
        r1.value(cls, 1.0, estimate_class=3)


# States ----------------------------------------------------------------------------
# Each member keeps one Delta in every season, so with nearest rank and 50
# members P10 is the 5th smallest member value and P90 the 45th.

def test_band_above_l_with_low_inversion_is_lower_risk_for_s3(rule):
    r = evaluate([field([400] * 50, 400)], rule)
    f = r.fields[0]
    assert (r.status, f.status, f.state, f.column) == ("pass", "pass", r1.LOWER, "S3")
    assert f.band == (400, 400) and f.n_inverted == 0
    assert f.l_kg_ha == 280.0
    json.dumps(r.to_dict())


def test_p10_equal_to_l_is_not_above_it(rule):
    at = evaluate([field([280] * 5 + [400] * 45, 400)], rule).fields[0]
    above = evaluate([field([280.1] * 5 + [400] * 45, 400)], rule).fields[0]
    assert at.band[0] == 280 and at.state == r1.INCONCLUSIVE
    assert above.band[0] == Fraction("280.1") and above.state == r1.LOWER


def test_l_is_the_equivalence_when_t1_rmse_is_smaller(rule):
    small = Fraction(100) ** 2
    assert evaluate([field([167] * 50, 167)], rule, t1_mse=small).fields[0].state == r1.LOWER
    assert evaluate([field([166] * 50, 166)], rule, t1_mse=small).fields[0].state == r1.NO_DIFFERENCE
    assert evaluate([field([250] * 50, 250)], rule).fields[0].state == r1.INCONCLUSIVE   # 166 < P10 < 280


def test_band_below_minus_l_gives_lower_risk_to_the_current_rotation(rule):
    f = evaluate([field([-400] * 50, -400)], rule).fields[0]
    assert (f.state, f.column) == (r1.LOWER, "S1")


def test_equivalence_band_is_inclusive(rule):
    f = evaluate([field([-166] * 5 + [0.5] * 39 + [166] * 6, 0.5)], rule).fields[0]
    assert f.band == (-166, 166) and f.state == r1.NO_DIFFERENCE
    g = evaluate([field([-166.5] * 5 + [0.5] * 45, 0.5)], rule).fields[0]
    assert g.state == r1.INCONCLUSIVE


def test_inversion_limit_is_inclusive_and_zero_median_counts_against(rule):
    ok = evaluate([field([-50] * 10 + [400] * 40, 400)], rule)
    over = evaluate([field([-50] * 11 + [400] * 39, 400)], rule)
    zero = evaluate([field([0] * 11 + [400] * 39, 400)], rule)
    assert (ok.status, ok.fields[0].n_inverted, ok.fields[0].inversion) == ("pass", 10, Fraction(1, 5))
    assert (over.status, over.fields[0].n_inverted) == ("fail", 11)
    assert (zero.status, zero.fields[0].n_inverted) == ("fail", 11)
    assert over.fields[0].state == r1.INCONCLUSIVE


def test_base_median_of_zero_gives_no_direction(rule):
    f = evaluate([field([400] * 50, 0)], rule).fields[0]
    assert f.n_inverted == 50 and f.status == "fail" and f.state == r1.INCONCLUSIVE


def test_member_median_uses_every_season(rule):
    # Member 0 is negative in 5 of 8 seasons: its median is negative, so it is inverted.
    fld = field([400] * 50, 400)
    members = dict(fld.members)
    members[0] = run_with([-10, -10, -10, -10, -10, 500, 500, 500])
    f = evaluate([r1.R1Field("field_a", fld.base, members)], rule).fields[0]
    assert f.n_inverted == 1


def test_large_inversion_does_not_hide_no_difference(rule):
    f = evaluate([field([-20] * 25 + [20] * 25, 20)], rule).fields[0]
    assert f.status == "fail" and f.state == r1.NO_DIFFERENCE


def test_cut_to_20_members(rule):
    r = evaluate([field([400] * 20, 400)], rule, members=20)
    assert r.members == 20 and r.fields[0].state == r1.LOWER
    assert r.notes == ["cut from 50 to 20 members"]
    with pytest.raises(common.ProtocolError):
        evaluate([field([400] * 30, 400)], rule, members=30)


# Gates and gaps ------------------------------------------------------------------

@pytest.mark.parametrize("t1_status", ["fail", "exploratory", "not_run", "not_completed"])
def test_r1_runs_only_if_t1_passes(rule, t1_status):
    r = evaluate([field([400] * 50, 400)], rule, t1_status=t1_status)
    assert r.status == "not_run"
    assert [(f.field_id, f.state) for f in r.fields] == [("field_a", r1.INCONCLUSIVE)]
    assert r.to_dict()["fields"][0]["band_kg_ha"] is None


def test_missing_member_or_season_leaves_the_field_not_completed(rule):
    fld = field([400] * 50, 400)
    members = dict(fld.members)
    del members[7]
    r = evaluate([r1.R1Field("field_a", fld.base, members)], rule)
    assert (r.status, r.fields[0].missing_members, r.fields[0].state) == ("not_completed", [7], r1.INCONCLUSIVE)
    members = dict(fld.members)
    members[3] = dict(list(members[3].items())[:-1])
    members[4] = run_with([400] * len(SEASONS))
    members[4][SEASONS[0]] = {"S1": None, "S3": 3400}
    r = evaluate([r1.R1Field("field_a", fld.base, members)], rule)
    assert r.fields[0].incomplete_members == [3, 4]


def test_failure_in_one_field_wins_over_a_gap_in_another(rule):
    good = field([400] * 50, 400, "field_a")
    bad = field([-50] * 11 + [400] * 39, 400, "field_b")
    gap = r1.R1Field("field_c", good.base, {m: v for m, v in good.members.items() if m})
    assert evaluate([good, bad, gap], rule).status == "fail"
    assert evaluate([good, gap], rule).status == "not_completed"


def test_inputs_that_break_the_design_are_refused(rule):
    fld = field([400] * 50, 400)
    with pytest.raises(ValueError):
        evaluate([fld], rule, t1_mse=None)
    with pytest.raises(ValueError):
        evaluate([r1.R1Field("field_a", fld.base, {**fld.members, 50: fld.base})], rule)
    with pytest.raises(ValueError):
        evaluate([fld, fld], rule)
    with pytest.raises(ValueError):
        evaluate([], rule)
    with pytest.raises(common.IncompleteInput):
        evaluate([r1.R1Field("field_a", {SEASONS[0]: {"S1": 1}}, fld.members)], rule)

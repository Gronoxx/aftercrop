# Written with an SI tool (Claude, Anthropic) for NASA Space Apps 2026. See AI_USE.md.
"""L1 data check (section 11.2), on synthetic values labelled "example,
fictitious": rain bias per season up to 10% and temperature bias up to 1 C,
both limits inclusive. A series outside is labelled, never corrected.
"""

import copy
import json
from datetime import date
from fractions import Fraction
from pathlib import Path

import pytest

from pipeline.bench import common, l1

FIX = Path(__file__).parent / "fixtures"
PROTOCOL = json.loads((FIX / "bench_protocol.json").read_text(encoding="utf-8"))
L1FX = json.loads((FIX / "bench_l1.json").read_text(encoding="utf-8"))


@pytest.fixture
def rule():
    return l1.L1Rule.from_protocol(PROTOCOL)


def rain(extra=()):
    return [l1.RainSeason(**r) for r in L1FX["rain"] + list(extra)]


def temps(extra=()):
    return [l1.TemperatureBias(**t) for t in L1FX["temperature"] + list(extra)]


def daily(name):
    return {date.fromisoformat(k): v for k, v in L1FX["daily"][name].items()}


def test_rule_from_protocol(rule):
    assert rule.site == "synthetic_site_a"
    assert rule.stations == ("INMET", "IDR-Parana")
    assert (rule.rain_bias_max_pct, rule.temperature_bias_max_c) == (10, 1)


@pytest.mark.parametrize("mutate", [
    lambda s: s.pop("rain_bias_max_pct_per_season"),
    lambda s: s.pop("temperature_bias_max_c"),
    lambda s: s.update(stations=[]),
    lambda s: s.update(site=""),
    lambda s: s.update(temperature_bias_max_c=0),
])
def test_bad_protocol_is_refused(mutate):
    p = copy.deepcopy(PROTOCOL)
    mutate(p["protocol"]["tests"]["L1"])
    with pytest.raises(common.ProtocolError):
        l1.L1Rule.from_protocol(p)


def test_limits_are_inclusive(rule):
    r = l1.evaluate(L1FX["site"], rain(), temps(), rule)
    assert r.status == "pass" and r.flags == []
    assert r.max_abs_rain_bias_pct == 10
    assert r.max_abs_temperature_bias_c == 1
    assert [x["bias_pct"] for x in r.rain] == [10.0, -10.0, 0.875, 8.0]
    assert r.stations_used == ["INMET", "IDR-Parana"] and r.stations_missing == []
    json.dumps(r.to_dict())


def test_rain_just_above_the_limit_is_labelled(rule):
    r = l1.evaluate(L1FX["site"], rain([L1FX["rain_just_out"]]), temps(), rule)
    assert r.status == "fail"
    assert len(r.flags) == 1
    assert "PRECTOTCORR against INMET" in r.flags[0] and "1 of 3 seasons (2020/21)" in r.flags[0]
    assert r.max_abs_rain_bias_pct == Fraction("10.01")


def test_temperature_just_above_the_limit_is_labelled(rule):
    r = l1.evaluate(L1FX["site"], rain(), temps([L1FX["temperature_just_out"]]), rule)
    assert r.status == "fail"
    assert r.flags == ["POWER T2M_MIN against IDR-Parana: mean bias +1.01 C, limit 1 C"]


def test_one_station_missing_is_noted(rule):
    only_inmet = [r for r in rain() if r.station == "INMET"]
    r = l1.evaluate(L1FX["site"], only_inmet, temps(), rule)
    assert r.status == "pass"
    assert r.stations_missing == ["IDR-Parana"]
    assert "no comparison with IDR-Parana" in r.notes


def test_wrong_site_unknown_station_and_empty_input_are_refused(rule):
    with pytest.raises(ValueError):
        l1.evaluate("synthetic_site_b", rain(), temps(), rule)
    with pytest.raises(ValueError):
        l1.evaluate(L1FX["site"], [l1.RainSeason("PRECTOTCORR", "CEMADEN", "2018/19", 1, 1, 30)], [], rule)
    with pytest.raises(ValueError):
        l1.evaluate(L1FX["site"], [], [], rule)
    with pytest.raises(ValueError):
        l1.evaluate(L1FX["site"], rain(L1FX["rain"][:1]), [], rule)   # duplicate season


def test_zero_station_total_and_no_paired_days_are_refused(rule):
    with pytest.raises(ValueError):
        l1.evaluate(L1FX["site"], [l1.RainSeason("PRECTOTCORR", "INMET", "x", 5, 0, 30)], [], rule)
    with pytest.raises(ValueError):
        l1.evaluate(L1FX["site"], [l1.RainSeason("PRECTOTCORR", "INMET", "x", 5, 5, 0)], [], rule)


def test_totals_use_paired_days_only():
    seasons = {"september": (date(2019, 9, 1), date(2019, 9, 30)), "october": (date(2019, 10, 1), date(2019, 10, 31))}
    totals = l1.season_totals(daily("product"), daily("station"), seasons)
    # 4 Sep has no product value and 5 Sep no product day: both drop from both sides.
    assert totals["september"] == (Fraction("15.5"), Fraction(14), 3)
    assert totals["october"] == (Fraction(2), Fraction("2.5"), 1)
    recs = l1.rain_records("PRECTOTCORR", "INMET", totals)
    assert recs[0].bias_pct == Fraction(100 * 3, 28)


def test_mean_difference():
    bias, n = l1.mean_difference(daily("product"), daily("station"))
    assert n == 4 and bias == Fraction(2 - 1 + Fraction(1, 2) - Fraction(1, 2), 4)
    with pytest.raises(ValueError):
        l1.mean_difference({}, {})

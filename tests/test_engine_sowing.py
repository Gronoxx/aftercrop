# Written with an SI tool (Claude, Anthropic) for NASA Space Apps 2026. See AI_USE.md.
"""Sowing rain rule (plan 10.6, PROTOCOL.md 6 and 16, D6). Synthetic rain only.

Run: .venv-engine/bin/python -m pytest tests/test_engine_sowing.py -q
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pipeline.engine.sowing import (  # noqa: E402
    ProtocolGap, RainRule, RainSowing, common_trigger, first_trigger, rain_series,
    s3_gap_days, trigger, zarc_window_md,
)

# Mirrors the shape of protocol.sowing.soybean_field (values as in the draft).
BLOCK = {"sowing": {"soybean_field": {
    "rain_mm": 20, "rain_days": 3, "start": "09-01", "latest": "12-31",
    "common_to_arms": True, "s3_not_before_wheat_harvest_plus_days": 20,
}}}


def rule(**kw) -> RainRule:
    args = dict(rain_mm=20.0, rain_days=3, start="09-01", latest="12-31", rain_after_not_before=True)
    args.update(kw)
    return RainRule(**args)


def rain(events: dict[str, float], first="2005-08-01", last="2006-01-31") -> pd.Series:
    s = pd.Series(0.0, index=pd.date_range(first, last, freq="D"))
    for d, mm in events.items():
        s.loc[d] = mm
    return s


def test_first_day_with_20mm_in_3_days():
    r = rain({"2005-09-10": 5.0, "2005-09-20": 8.0, "2005-09-21": 7.0, "2005-09-22": 5.0})
    assert trigger(r, RainSowing(rule(), 2005), date(2005, 9, 1)) == date(2005, 9, 22)


def test_rain_before_start_does_not_count():
    # 30 mm on 31 Aug: the window 30 Aug..1 Sep is not wholly on or after the start.
    r = rain({"2005-08-31": 30.0, "2005-09-02": 1.0, "2005-09-15": 25.0})
    assert trigger(r, RainSowing(rule(), 2005), date(2005, 9, 1)) == date(2005, 9, 15)


def test_exactly_20mm_counts_despite_float_sum():
    r = rain({"2005-10-01": 6.7, "2005-10-02": 6.6, "2005-10-03": 6.7})
    assert trigger(r, RainSowing(rule(), 2005), date(2005, 9, 1)) == date(2005, 10, 3)
    r2 = rain({"2005-10-01": 6.7, "2005-10-02": 6.6, "2005-10-03": 6.69})
    assert trigger(r2, RainSowing(rule(), 2005), date(2005, 9, 1)) is None


def test_no_trigger_by_latest_is_none_and_latest_day_counts():
    assert trigger(rain({}), RainSowing(rule(), 2005), date(2005, 9, 1)) is None
    r = rain({"2005-12-31": 20.0})
    assert trigger(r, RainSowing(rule(), 2005), date(2005, 9, 1)) == date(2005, 12, 31)
    late = rain({"2006-01-01": 50.0})
    assert trigger(late, RainSowing(rule(), 2005), date(2005, 9, 1)) is None


def test_not_before_opens_the_rain_window_or_not():
    # Harvest on 10 Oct, S3 gap 20 days: not before 30 Oct. Rain on 28-30 Oct.
    r = rain({"2005-10-28": 10.0, "2005-10-29": 10.0, "2005-10-30": 1.0, "2005-11-10": 20.0})
    spec_yes = RainSowing(rule(rain_after_not_before=True), 2005, min_days_after_harvest=20)
    spec_no = RainSowing(rule(rain_after_not_before=False), 2005, min_days_after_harvest=20)
    harvest = date(2005, 10, 10)
    earliest = spec_yes.not_before(date(2005, 10, 11), harvest)
    assert earliest == date(2005, 10, 30)
    assert trigger(r, spec_yes, earliest) == date(2005, 11, 10)
    assert trigger(r, spec_no, earliest) == date(2005, 10, 30)


def test_common_date_is_the_first_valid_in_every_arm():
    r = rain({"2005-09-20": 20.0, "2005-10-20": 20.0})
    spec = RainSowing(rule(), 2005, common="site:2005/06")
    d = common_trigger(r, spec, {"after_wheat": date(2005, 10, 1), "after_maize": date(2005, 9, 5)})
    assert d == date(2005, 10, 20)


def test_window_crossing_the_new_year():
    r = rule(start="11-15", latest="01-20")
    assert r.window(2005) == (date(2005, 11, 15), date(2006, 1, 20))
    s = rain({"2006-01-10": 25.0})
    assert trigger(s, RainSowing(r, 2005), date(2005, 11, 15)) == date(2006, 1, 10)


def test_rain_must_cover_the_window():
    with pytest.raises(ValueError, match="does not cover"):
        trigger(rain({}, last="2005-11-30"), RainSowing(rule(), 2005), date(2005, 9, 1))


def test_from_protocol_reads_block_and_requires_the_open_point():
    with pytest.raises(ProtocolGap, match="rain_after_not_before"):
        RainRule.from_protocol(BLOCK)
    r = RainRule.from_protocol(BLOCK, rain_after_not_before=True)
    assert (r.rain_mm, r.rain_days, r.start, r.latest) == (20.0, 3, "09-01", "12-31")
    with pytest.raises(ValueError, match="in the protocol"):
        RainRule.from_protocol(BLOCK, rain_mm=25, rain_after_not_before=True)
    with pytest.raises(ProtocolGap, match="rain_mm"):
        RainRule.from_protocol({"sowing": {"soybean_field": {}}}, rain_after_not_before=True)
    assert s3_gap_days(BLOCK) == 20
    with pytest.raises(ProtocolGap):
        s3_gap_days({})


def test_rule_validation():
    with pytest.raises(ValueError):
        rule(rain_mm=0)
    with pytest.raises(ValueError):
        rule(start="02-29")
    with pytest.raises(TypeError):
        rule(rain_after_not_before=None)
    with pytest.raises(ValueError):
        RainSowing(rule(), 2005, min_days_after_harvest=0)


def test_first_trigger_low_level_and_rain_series():
    w = pd.DataFrame({"Date": pd.date_range("2005-09-01", periods=10, freq="D"),
                      "Precipitation": [0, 0, 10, 10, 0, 0, 0, 0, 0, 0.0]})
    s = rain_series(w)
    r = rule(rain_days=2)
    assert first_trigger(s, r, date(2005, 9, 1), date(2005, 9, 1), date(2005, 9, 10)) == date(2005, 9, 4)
    assert first_trigger(s, r, date(2005, 9, 4), date(2005, 9, 4), date(2005, 9, 10)) is None


def test_zarc_window_needs_exactly_one_run():
    one = [{"start": "2026-09-11", "end": "2026-12-20"}]
    assert zarc_window_md(one) == ("09-11", "12-20")
    with pytest.raises(ProtocolGap):
        zarc_window_md(one * 2)

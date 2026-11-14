# Written with an SI tool (Claude, Anthropic) for NASA Space Apps 2026. See AI_USE.md.
"""End of the first green cycle per season, rule M2-green-end-v2 (D12, SA-D62). Offline.

Every series below is an example, fictitious: synthetic NDVI, never HLS data and never a
real field. The rule values equal the D12 values of the protocol, passed as test
arguments; one test checks that ``from_protocol`` reads the same values.
"""

import dataclasses
import copy
import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from pipeline.adapters import base
from pipeline.bench.common import ProtocolError
from pipeline.fields import events, ribbon

FIX = json.loads((Path(__file__).parent / "fixtures" / "fields_series_gap39.json").read_text(encoding="utf-8"))
REPO = Path(__file__).resolve().parents[1]
V2 = dict(half_height_frac=0.5, min_drop=0.3, exit_window=("12-01", "05-31"), cycle_start_window=("09-01", "12-31"),
          rebound_days=10, rebound_within=0.1, max_gap_days=20, variant_amplitude_frac=0.1)
CLEAN = ribbon.CleanRule(0.30, 0.5, 20)
RULE = events.EventRule(clean=CLEAN, **V2)


def rule(**over):
    return events.EventRule(**{"clean": CLEAN, **V2, **over})


def row(d, ndvi, n_clean=100, sensor="S30"):
    return {"date": d, "sensor": sensor, "granule_ur": f"EXAMPLE.{sensor}.{d}", "ndvi": ndvi,
            "n_clean": n_clean, "n_possible": 100}


def series(start, step, values, **kw):
    """Rows every ``step`` days from ``start``. None is a cloudy row (not clean)."""
    d0 = date.fromisoformat(start)
    out = []
    for i, v in enumerate(values):
        d = (d0 + timedelta(days=i * step)).isoformat()
        out.append(row(d, 0.9 if v is None else v, n_clean=5 if v is None else 100, **kw))
    return out


def one(rows, season="2025/26", rule=RULE):
    return events.green_ends(rows, [season], rule)[0]


# Soybean then second-crop corn, every 12 days from 10 Sep 2025 (fictitious).
SOY_CORN = [0.25, 0.22, 0.20, 0.35, 0.60, 0.80, 0.85, 0.88, 0.86, 0.80, 0.60, 0.38,   # 10 Sep .. 20 Jan
            0.30, 0.50, 0.75, 0.85, 0.80, 0.60, 0.40, 0.30, 0.25]                      # 1 Feb .. 8 May


def test_soybean_then_corn_with_the_dip_visible():
    e = one(series("2025-09-10", 12, SOY_CORN))
    assert (e["low"], e["high"], e["gap_days"], e["status"], e["reason"]) == ("2026-01-08", "2026-01-20", 12, "ok", "")
    assert e["m_high"] == e["peak_ndvi"] == 0.88 and e["peak_date"] == "2025-12-03"
    assert e["drop"] == pytest.approx(0.5) and (e["ndvi_low"], e["ndvi_high"]) == (0.60, 0.38)
    assert e["cycle_start"] == "2025-10-16"           # last clean look at or below half height before the peak
    assert e["longest_gap_days"] == 12 and e["rebounds"] == 0
    assert e["green_cycles"] == 2                      # the corn cycle is reported, the status ignores it
    # the 10% variant merges the two cycles: 0.30 on 1 Feb is still "green" above 0.268, so its
    # exit is the corn exit (SA-D62, why not the 10% of the amplitude)
    assert e["v10_threshold"] == pytest.approx(0.268) and (e["v10_low"], e["v10_high"]) == ("2026-04-26", "2026-05-08")


def test_dip_hidden_by_a_cloud_gap_is_never_ok():
    vals = list(SOY_CORN)
    vals[10:14] = [None, None, None, None]            # 8 Jan to 13 Feb cloudy: the dip is not seen
    rows = series("2025-09-10", 12, vals) + [row("2026-01-21", 0.70)]   # one clear look, still green
    e = one(rows)
    assert e["status"] == "to_confirm"
    assert (e["low"], e["high"]) == ("2026-04-02", "2026-04-14")   # the corn exit, kept as found, never "ok"
    assert "interval of 35 days above 20" in e["reason"] and e["longest_gap_days"] == 35
    assert e["green_cycles"] == 1                     # the ribbon cannot split what it did not see


def test_rebound_after_one_low_unflagged_value_drops_the_pair():
    rows = series("2025-09-10", 12, [0.25, 0.22, 0.20, 0.35, 0.60, 0.80, 0.85, 0.88]) + [row("2025-12-18", 0.87),
        row("2026-01-02", 0.85), row("2026-01-10", 0.35),   # a cloud the mask missed
        row("2026-01-16", 0.84), row("2026-01-26", 0.80), row("2026-02-05", 0.60), row("2026-02-15", 0.30)]
    e = one(rows)
    assert (e["low"], e["high"], e["status"], e["rebounds"], e["reason"]) == ("2026-02-05", "2026-02-15", "ok", 1, "")
    # without the rebound rule the false dip would win
    e0 = one(rows, rule=rule(rebound_days=0))
    assert (e0["low"], e0["high"]) == ("2026-01-02", "2026-01-10")


def test_rebound_window_edge():
    rows = series("2025-09-10", 12, [0.25, 0.22, 0.20, 0.35, 0.60, 0.80, 0.85, 0.88]) + [row("2025-12-18", 0.87),
        row("2026-01-02", 0.85), row("2026-01-10", 0.35), row("2026-01-20", 0.79), row("2026-01-30", 0.30)]
    assert one(rows)["high"] == "2026-01-30"          # 10 days later and 0.79 >= 0.88 - 0.1: dropped
    late = [r if r["date"] != "2026-01-20" else row("2026-01-21", 0.79) for r in rows]
    assert one(late)["high"] == "2026-01-10"          # 11 days later: kept


def test_wheat_ripening_in_september_is_not_the_exit():
    rows = series("2025-09-02", 13, [0.75, 0.60, 0.25, 0.20, 0.30, 0.60, 0.85, 0.90, 0.88, 0.80, 0.55, 0.30])
    e = one(rows)
    assert (e["low"], e["high"], e["status"]) == ("2026-01-10", "2026-01-23", "ok")   # not 15 Sep to 28 Sep
    assert date.fromisoformat(e["high"]) >= date(2025, 12, 1)
    assert e["green_cycles"] == 2 and e["cycle_start"] == "2025-10-24"


def test_late_december_sowing_starts_at_the_end_of_the_window():
    fallow = series("2025-09-04", 12, [0.20] * 10)                         # 4 Sep .. 21 Dec
    soy = [row("2025-12-28", 0.20), row("2026-01-09", 0.40), row("2026-01-21", 0.70), row("2026-02-02", 0.85),
           row("2026-02-14", 0.88), row("2026-02-26", 0.86), row("2026-03-10", 0.80), row("2026-03-22", 0.60),
           row("2026-04-03", 0.35)]
    e = one(fallow + soy)
    assert (e["low"], e["high"], e["status"], e["cycle_start"]) == ("2026-03-22", "2026-04-03", "ok", "2025-12-28")
    # the same crop after a cover above half height, first low look on 9 Jan: the start is outside the window
    cover = series("2025-09-04", 12, [0.50] * 10)
    e2 = one(cover + [r for r in soy if r["date"] != "2025-12-28"] + [row("2025-12-30", 0.60)])
    assert e2["status"] == "to_confirm" and "cycle start not seen" in e2["reason"]


def test_cycle_with_no_low_observation_before_the_peak():
    rows = [row("2025-12-20", 0.70), row("2026-01-01", 0.85), row("2026-01-13", 0.88), row("2026-01-25", 0.80),
            row("2026-02-06", 0.60), row("2026-02-18", 0.30)]
    e = one(rows)
    assert (e["low"], e["high"], e["gap_days"], e["status"]) == ("2026-02-06", "2026-02-18", 12, "to_confirm")
    assert e["cycle_start"] is None and "cycle start not seen" in e["reason"]
    # a look at or below half height after the peak does not count as the start
    assert one(rows + [row("2025-12-08", 0.50)])["status"] == "to_confirm"
    assert one(rows + [row("2025-12-08", 0.44)])["status"] == "ok"


def test_no_clean_observation_no_canopy_and_no_pair():
    assert one([])["status"] == "no_data" and one([])["reason"] == "no clean observation in the season"
    cloudy = one(series("2025-09-10", 12, [None] * 20))
    assert (cloudy["status"], cloudy["low"], cloudy["gap_days"]) == ("no_data", None, None)
    bare = one(series("2025-09-10", 12, [0.2, 0.25, 0.3, 0.22]))
    assert bare["status"] == "no_data" and "no clean observation above ndvi_green 0.3" in bare["reason"]
    late = one(series("2025-11-01", 12, [0.2, 0.5, 0.8, 0.85, 0.85, 0.85, 0.85, 0.85, 0.85, 0.85, 0.85, 0.85,
                                         0.85, 0.85, 0.85, 0.85, 0.85, 0.85, 0.85, 0.2]))   # drop on 18 Jun
    assert late["status"] == "to_confirm" and late["low"] is None and "no exit pair with high by 05-31" in late["reason"]
    gone = one([row("2025-10-01", 0.2), row("2025-11-01", 0.8), row("2026-06-15", 0.2)])
    assert gone["status"] == "to_confirm" and "no clean observation from 12-01 to 05-31" in gone["reason"]
    assert set(events.STATUSES) == {"ok", "to_confirm", "no_data"}


def test_thresholds_are_exact():
    head = [row("2025-10-01", 0.2), row("2025-10-13", 0.4), row("2025-10-25", 0.6)]
    edge = one(head + [row("2025-12-05", 0.5), row("2025-12-17", 0.3)], rule=rule(max_gap_days=60))
    assert (edge["high"], edge["status"]) == ("2025-12-17", "ok")      # 0.3 is half of 0.6 and 0.6 - 0.3 = 0.3
    small = one([row("2025-10-01", 0.2), row("2025-10-25", 0.58), row("2025-12-05", 0.5), row("2025-12-17", 0.29)],
                rule=rule(max_gap_days=60))
    assert small["status"] == "to_confirm" and small["low"] is None    # drop 0.29 below 0.3: no pair


def test_gap_limit_from_1_december_to_high():
    base_rows = series("2025-09-10", 12, [0.25, 0.22, 0.20, 0.35, 0.60, 0.80, 0.85, 0.88, 0.86])  # .. 15 Dec
    ok = one(base_rows + [row("2026-01-04", 0.86), row("2026-01-24", 0.30)])   # 20 days
    assert (ok["status"], ok["longest_gap_days"]) == ("ok", 20)
    far = one(base_rows + [row("2026-01-04", 0.86), row("2026-01-25", 0.30)])  # 21 days
    assert far["status"] == "to_confirm" and "interval of 21 days above 20" in far["reason"]
    assert (far["low"], far["high"], far["gap_days"]) == ("2026-01-04", "2026-01-25", 21)   # interval kept
    # a gap that spans 1 December counts; one that ends before it does not
    spans = one(series("2025-09-10", 12, [0.2, 0.2, 0.35, 0.6, 0.8]) + [row("2025-12-12", 0.88),
                row("2025-12-24", 0.86), row("2026-01-05", 0.3)])
    assert spans["status"] == "to_confirm" and "interval of 45 days" in spans["reason"]   # 28 Oct to 12 Dec
    before = one([row("2025-09-10", 0.2), row("2025-10-30", 0.35)] + series("2025-11-10", 12, [0.6, 0.8, 0.88, 0.86,
                 0.80, 0.3]))
    assert before["status"] == "ok" and before["longest_gap_days"] == 12


def test_same_day_sensors_collapse_to_the_larger_ndvi():
    rows = [row("2025-11-30", 0.2), row("2025-12-10", 0.8), row("2026-01-20", 0.8, sensor="L30"),
            row("2026-01-20", 0.3, sensor="S30"), row("2026-01-28", 0.3)]
    e = one(rows)
    assert (e["low"], e["high"], e["sensor_low"]) == ("2026-01-20", "2026-01-28", "L30")


def test_fixture_with_the_39_day_gap():
    e = one(FIX["rows"], rule=rule(clean=ribbon.CleanRule(**FIX["rule"])))
    exp = FIX["expected"]["green_end"]
    assert {k: e[k] for k in ("low", "high", "gap_days", "status", "sensor_high")} == {
        k: exp[k] for k in ("low", "high", "gap_days", "status", "sensor_high")}
    assert e["drop"] == pytest.approx(exp["drop"])
    assert "39 days above 20" in e["reason"]


def test_each_season_reads_its_own_observations():
    rows = series("2025-09-10", 12, SOY_CORN)
    e25, e26 = events.green_ends(rows, ["2025/26", "2026/27"], RULE)
    assert e25["status"] == "ok" and e26["status"] == "no_data"
    assert events.season_day(2025, "12-01") == date(2025, 12, 1) and events.season_day(2025, "05-31") == date(2026, 5, 31)


def test_rule_from_protocol_reads_every_d12_value():
    cfg = base.load_config()
    # The protocol raised the gap from 20 to 25 days after the rehearsal (allowed adjustment, PROTOCOL.md D12).
    assert events.EventRule.from_protocol(cfg) == dataclasses.replace(RULE, max_gap_days=25)
    assert events.METHOD_ID == cfg["protocol"]["field"]["green_end_method"] == "M2-green-end-v2"
    keys = ("green_end_method", "green_end_reference", "green_end_half_height_frac", "green_end_min_drop",
            "green_end_exit_window", "green_end_cycle_start_window", "green_end_rebound_days",
            "green_end_rebound_within", "green_end_max_gap_days", "green_end_compared_variant",
            "ndvi_green", "min_clean_frac", "min_clean_pixels")
    for k in keys:
        p = copy.deepcopy(cfg)
        p["protocol"]["field"].pop(k)
        with pytest.raises(ProtocolError, match=k):
            events.EventRule.from_protocol(p)
        p["protocol"]["field"][k] = None
        with pytest.raises(ProtocolError, match=f"{k} is null"):
            events.EventRule.from_protocol(p)
    for k, bad in (("green_end_method", "M2-green-end-v1"), ("green_end_reference", "segment_max"),
                   ("green_end_exit_window", ["12-01"]), ("green_end_exit_window", ["12-01", "02-30"]),
                   ("green_end_compared_variant", "a tenth of the amplitude"), ("green_end_min_drop", True)):
        p = copy.deepcopy(cfg)
        p["protocol"]["field"][k] = bad
        with pytest.raises(ProtocolError):
            events.EventRule.from_protocol(p)
    with pytest.raises(ValueError, match="ends before it starts"):
        rule(exit_window=("05-31", "12-01"))


def test_csv_round_trip(tmp_path):
    rows = series("2025-09-10", 12, SOY_CORN) + FIX["rows"][:0]
    evs = events.green_ends(rows, ["2024/25", "2025/26"], RULE)
    back = events.read_csv(events.write_csv(tmp_path / "field_x1_green_end.csv", evs))
    assert [(e["season"], e["status"], e["low"], e["gap_days"], e["cycle_start"], e["v10_threshold"] is None)
            for e in back] == [(e["season"], e["status"], e["low"], e["gap_days"], e["cycle_start"],
                                e["v10_threshold"] is None) for e in evs]
    assert back[1]["drop"] == pytest.approx(evs[1]["drop"], abs=1e-6) and back[1]["rebounds"] == 0
    # a row written without the v2 columns (older callers) reads back with them empty
    old = {k: evs[1][k] for k in events.BASE_COLUMNS}
    again = events.read_csv(events.write_csv(tmp_path / "old.csv", [old]))[0]
    assert again["m_high"] is None and again["rebounds"] == 0 and again["low"] == evs[1]["low"]


def test_no_fixed_margin_anywhere_in_pipeline_or_site():
    # M2.3: `rg -n "±15" pipeline site` finds nothing.
    hits = []
    for top in ("pipeline", "site"):
        for p in (REPO / top).rglob("*"):
            if p.is_file() and p.suffix in {".py", ".mjs", ".js", ".html", ".json", ".md", ".yaml", ".css", ".txt"}:
                if "±15" in p.read_text(encoding="utf-8", errors="ignore"):
                    hits.append(str(p.relative_to(REPO)))
    assert hits == []

# Written with an SI tool (Claude, Anthropic) for NASA Space Apps 2026. See AI_USE.md.
"""Choice of Field A, B and C (plan 8.4 step 4), by criterion M2-select-v1. Offline.

Every candidate, event and slope below is an example, fictitious. No real polygon is
chosen here: the protocol has not locked ndvi_green and min_clean_frac (D10).
"""

import copy
import json

import pytest
from rasterio.crs import CRS
from rasterio.warp import transform

from pipeline.adapters import base
from pipeline.bench.common import ProtocolError
from pipeline.fields import geometry, select

SEASONS = ["2025/26"]


def ev(low, high, status="ok", season="2025/26"):
    return {"season": season, "low": low, "high": high, "status": status}


def test_exit_day_is_the_midpoint_after_1_september():
    assert select.exit_day(ev("2026-01-01", "2026-01-11")) == 127   # 1 Sep 2025 + 122 days = 1 Jan; + 5
    assert select.exit_day(ev("2025-09-01", "2025-09-02")) == 0.5


def table(exits, slopes):
    return select.candidate_table({k: [v] if v else [] for k, v in exits.items()}, slopes, SEASONS)


def test_choose_a_early_b_late_c_steepest_of_the_rest():
    t = table({"c1": ev("2026-01-20", "2026-01-30"), "c2": ev("2026-02-20", "2026-03-02"),
               "c3": ev("2026-02-01", "2026-02-11"), "c4": ev("2026-01-10", "2026-01-20", "to_confirm"),
               "c5": None},
              {"c1": 9.0, "c2": 1.0, "c3": 2.0, "c4": 4.5, "c5": 4.5})
    ch = select.choose(t)
    assert (ch["a"], ch["b"], ch["c"]) == ("c1", "c2", "c4")  # c4 and c5 tie on slope: smaller id
    assert ch["exit_difference_days"] == 31 and ch["notes"] == []
    assert [r["exit_score_days"] is None for r in t] == [False, False, False, True, True]


def test_ties_and_missing_contrast():
    t = table({"c2": ev("2026-01-20", "2026-01-30"), "c1": ev("2026-01-20", "2026-01-30"),
               "c3": ev("2026-02-20", "2026-03-02")}, {})
    ch = select.choose(t)
    assert (ch["a"], ch["b"], ch["c"]) == ("c1", "c3", None)
    assert "no Field C" in ch["notes"][0]
    flat = select.choose(table({"c1": ev("2026-01-20", "2026-01-30"), "c2": ev("2026-01-20", "2026-01-30")},
                               {"c1": 1.0}))
    assert (flat["a"], flat["b"], flat["c"]) == (None, None, "c1")
    lone = select.choose(table({"c1": ev("2026-01-20", "2026-01-30"), "c2": ev("2026-01-01", "2026-01-05", "no_data")},
                               {"c1": 3.0, "c2": 2.0}))
    assert (lone["a"], lone["b"], lone["c"]) == (None, None, "c1")
    assert "fewer than 2" in lone["notes"][0]


def test_every_choice_season_must_be_ok():
    two = ["2024/25", "2025/26"]
    t = select.candidate_table(
        {"c1": [ev("2025-01-20", "2025-01-30", season="2024/25"), ev("2026-01-20", "2026-01-30")],
         "c2": [ev("2026-02-20", "2026-03-02")]}, {}, two)
    assert t[0]["exit_score_days"] is not None and t[1]["exit_score_days"] is None
    assert t[1]["exits"][0] == {"season": "2024/25", "low": None, "high": None, "status": "no_data"}


def test_bad_slope_is_refused(tmp_path):
    with pytest.raises(ValueError):
        select.candidate_table({}, {"c1": "steep"}, SEASONS)
    p = tmp_path / "slopes.json"
    p.write_text(json.dumps({"c1": 3.5, "c2": None}))
    assert select.read_slopes(p) == {"c1": 3.5, "c2": None}


def ring(cols_rows):
    wkt = CRS.from_epsg(32622).to_wkt()
    xs = [470000 + 30 * c for c, _ in cols_rows]
    ys = [-2550000 - 30 * r for _, r in cols_rows]
    lon, lat = transform(wkt, "EPSG:4326", xs, ys)
    pts = [[x, y] for x, y in zip(lon, lat)]
    return pts + [pts[0]]


def test_selected_geojson_is_anonymous_and_valid():
    cands = {"type": "FeatureCollection", "window_id": "example_window", "features": [
        {"type": "Feature", "properties": {"field_id": f"c{i}"},
         "geometry": {"type": "Polygon", "coordinates": [ring([(i * 10, 0), (i * 10 + 8, 0), (i * 10 + 8, 8), (i * 10, 8)])]}}
        for i in range(1, 4)]}
    out = select.selected_geojson(cands, {"a": "c2", "b": "c1", "c": None}, SEASONS)
    fs = geometry.parse_fields(out)
    assert [(f["field_id"], f["criterion"]) for f in fs] == [
        ("a", "Field A: earliest end of green among the drawn candidates in 2025/26"),
        ("b", "Field B: latest end of green among the drawn candidates in 2025/26")]
    assert out["features"][0]["geometry"] == cands["features"][1]["geometry"]
    assert set(out) == {"type", "features", "window_id"}
    doc = select.selection_doc([], {"a": None, "b": None, "c": None, "notes": []}, SEASONS,
                               select.events.EventRule.from_protocol(base.load_config()))
    assert doc["criterion_id"] == "M2-select-v1" and doc["choice_seasons"] == SEASONS
    json.dumps(doc)


def test_choice_seasons_and_d10_guard(monkeypatch, tmp_path):
    cfg = copy.deepcopy(base.load_config())
    assert select.choice_seasons(cfg) == ["2025/26"]
    cfg["protocol"]["field"].update(ndvi_green=None, min_clean_frac=None)
    monkeypatch.setattr(base, "load_config", lambda *a, **k: cfg)

    def no_read(*a, **k):
        raise AssertionError("a series was read before the protocol locked D10")

    monkeypatch.setattr(select.ndvi_series, "index_records", no_read)
    monkeypatch.setattr(select.ndvi_series, "series_from_records", no_read)
    with pytest.raises(ProtocolError, match="ndvi_green is null"):
        select.main([str(tmp_path / "candidates.geojson"), "--slopes", str(tmp_path / "slopes.json")])
    bad = copy.deepcopy(cfg)
    bad["protocol"]["field"]["choice_seasons"] = []
    with pytest.raises(ProtocolError):
        select.choice_seasons(bad)


def test_candidate_count_by_mode():
    cfg = base.load_config()
    assert select.candidate_bounds(cfg) == (8, 12)
    for n in (8, 12):
        select.check_count(n, cfg)
    for n in (7, 13):
        with pytest.raises(ValueError, match="product mode needs 8 to 12"):
            select.check_count(n, cfg)
    select.check_count(2, cfg, rehearsal=True)
    with pytest.raises(ValueError, match="at least 2"):
        select.check_count(1, cfg, rehearsal=True)
    bad = copy.deepcopy(cfg)
    bad["protocol"]["field"]["candidates"] = [12, 8]
    with pytest.raises(ProtocolError):
        select.candidate_bounds(bad)


def test_choice_falls_back_one_older_season_at_a_time():
    # protocol-v1.1: fewer than 2 ok candidates in the choice season -> the next older season, alone.
    def row(cid, score):
        return {"candidate_id": cid, "exit_score_days": score, "slope": 1.0}
    tables = {("2025/26",): [row("p1", 150), row("p2", None), row("p3", None)],
              ("2024/25",): [row("p1", 140), row("p2", None), row("p3", None)],
              ("2023/24",): [row("p1", 140), row("p2", 160), row("p3", None)],
              ("2022/23",): [row("p1", 100), row("p2", 200), row("p3", 150)]}
    calls = []

    def table_for(sel):
        calls.append(tuple(sel))
        return tables[tuple(sel)]
    choice, used, log, _ = select.choose_in_order([["2025/26"], ["2024/25"], ["2023/24"], ["2022/23"]], table_for)
    assert (choice["a"], choice["b"], used) == ("p1", "p2", ["2023/24"])
    assert calls == [("2025/26",), ("2024/25",), ("2023/24",)]   # stops at the first season with A and B
    assert [x["n_ok"] for x in log] == [1, 1, 2]


def test_fallback_seasons_come_from_the_protocol():
    cfg = base.load_config()
    assert select.fallback_seasons(cfg) == ["2024/25", "2023/24", "2022/23"]

# Written with an SI tool (Claude, Anthropic) for NASA Space Apps 2026. See AI_USE.md.
"""N1 inputs (M2.6): field halves and L30 against S30, in the shape pipeline.bench.n1 reads. Offline.

Every raster, granule id, polygon and date below is an example, fictitious. The clean
rule (NDVI 0.5) is a test value. The end-of-green rule takes the D12 values of the protocol.
"""

import copy
import json
from pathlib import Path

import numpy as np
import pytest
from rasterio.crs import CRS
from rasterio.warp import transform

from pipeline.adapters import base
from pipeline.bench import common, n1
from pipeline.fields import events, geometry, ndvi_series, noise, ribbon

WKT = CRS.from_epsg(32622).to_wkt()
T = (30.0, 0.0, 470000.0, 0.0, -30.0, -2550000.0)
RULE = events.EventRule(ribbon.CleanRule(0.5, 0.5, 20), half_height_frac=0.5, min_drop=0.3,
                        exit_window=("12-01", "05-31"), cycle_start_window=("09-01", "12-31"), rebound_days=10,
                        rebound_within=0.1, max_gap_days=20, variant_amplitude_frac=0.1)
FM = {"fill": -19999, "masked_bits": ["cloud", "shadow"], "masked_aerosol": []}
FIX = Path(__file__).parent / "fixtures"
BENCH_PROTOCOL = json.loads((FIX / "bench_protocol.json").read_text(encoding="utf-8"))


def ev(season, low, high, status="ok"):
    return {"season": season, "low": low, "high": high, "gap_days": None, "status": status}


def test_disagreement_by_interval_point():
    a, b = ev("2025/26", "2026-01-01", "2026-01-11"), ev("2025/26", "2026-01-04", "2026-01-09")
    assert noise.disagreement_days(a, b, "low") == 3
    assert noise.disagreement_days(a, b, "high") == 2
    assert noise.disagreement_days(a, b, "midpoint") == 0.5
    assert noise.disagreement_days(b, a, "midpoint") == 0.5
    with pytest.raises(ValueError):
        noise.disagreement_days(a, b, "mean")


def test_pairs_need_ok_on_both_sides():
    a = [ev("2024/25", "2025-01-01", "2025-01-11"), ev("2025/26", "2026-01-01", "2026-01-11"),
         ev("2023/24", None, None, "no_data")]
    b = [ev("2024/25", "2025-01-05", "2025-01-15", "to_confirm"), ev("2025/26", "2026-01-03", "2026-01-13")]
    pairs, skipped = noise.pair_events(a, b, null="halves", field_id="x1", point="midpoint")
    assert [(p["season"], p["disagreement_days"]) for p in pairs] == [("2025/26", 2)]
    assert [(s["season"], s["status_a"], s["status_b"]) for s in skipped] == [
        ("2023/24", "no_data", None), ("2024/25", "ok", "to_confirm")]
    with pytest.raises(ValueError):
        noise.pair_events(a, b, null="other", field_id="x1", point="low")


def test_point_comes_from_the_protocol_only():
    cfg = base.load_config()
    assert noise.point_from_protocol(cfg) == "midpoint"              # D12
    assert noise.point_from_protocol(cfg, "midpoint") == "midpoint"  # the flag only checks
    with pytest.raises(common.ProtocolError, match="caller gave 'low'"):
        noise.point_from_protocol(cfg, "low")
    rule = n1.N1Rule.from_protocol(cfg)                              # percentile and method, no argument
    assert (float(rule.percentile), rule.method) == (90.0, "nearest_rank")
    p = copy.deepcopy(cfg)
    p["protocol"]["tests"]["N1"].pop("interval_point", None)
    with pytest.raises(common.ProtocolError, match="interval_point"):
        noise.point_from_protocol(p)
    with pytest.raises(common.ProtocolError, match="interval_point"):
        noise.point_from_protocol(p, "midpoint")                     # a flag never fills the protocol
    p["protocol"]["tests"]["N1"]["interval_point"] = "high"
    assert noise.point_from_protocol(p) == "high"
    with pytest.raises(common.ProtocolError):
        noise.point_from_protocol(p, "low")
    p["protocol"]["tests"]["N1"]["interval_point"] = "mean"
    with pytest.raises(common.ProtocolError):
        noise.point_from_protocol(p)


# End to end on a synthetic cache ---------------------------------------------------------

def write_band(root, name, data, scale="0.0001"):
    path = root / "data" / "raw" / "hls" / "w_example" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(path, data=data, transform=np.asarray(T, dtype=np.float64), crs_wkt=np.asarray(WKT),
                        window_px=np.asarray([0, 0, *data.shape], dtype=np.int64))
    base.meta_path(path).write_text(json.dumps({"url": f"https://example.test/{name}", "nodata": None,
                                                "tags": {"scale_factor": scale}, "crs_epsg": 32622}))
    return str(path.relative_to(root))


def granule(root, sensor, d, left, right):
    """NDVI ``left`` on columns 0..9 and ``right`` on 10..19 of a clear 20 x 20 window."""
    ur = f"EXAMPLE.{sensor}.{d}"
    nd = np.empty((20, 20))
    nd[:, :10], nd[:, 10:] = left, right
    files = {"Fmask": write_band(root, f"{ur}.Fmask.npz", np.zeros((20, 20), dtype=np.uint8)),
             "NDVI": write_band(root, f"{ur}.NDVI.npz", np.round(nd / 1e-4).astype(np.int16))}
    return {"granule_ur": ur, "date": d, "sensor": sensor, "files": files}


def field():
    xs = [T[2] + c * T[0] for c in (3, 17, 17, 3)]
    ys = [T[5] + r * T[4] for r in (6, 6, 12, 12)]
    lon, lat = transform(WKT, "EPSG:4326", xs, ys)
    return {"field_id": "x1", "criterion": None, "ring": list(zip(lon, lat))}  # 14 x 6 -> 12 x 4 inside


def test_end_to_end_feeds_bench_n1(tmp_path):
    recs = [granule(tmp_path, "S30", "2025-12-20", 0.2, 0.2), granule(tmp_path, "L30", "2025-12-22", 0.2, 0.2),
            granule(tmp_path, "S30", "2026-01-01", 0.8, 0.8), granule(tmp_path, "L30", "2026-01-03", 0.8, 0.8),
            granule(tmp_path, "S30", "2026-01-06", 0.8, 0.8), granule(tmp_path, "S30", "2026-01-11", 0.3, 0.8),
            granule(tmp_path, "L30", "2026-01-13", 0.3, 0.3), granule(tmp_path, "S30", "2026-01-16", 0.3, 0.3)]
    grule = geometry.GeometryRule(30, 20)
    rows, _ = ndvi_series.series_from_records(recs, geometry.FieldMasks(field(), grule, with_halves=True),
                                              root=tmp_path, **FM)
    assert [len(rows[k]) for k in ("whole", "half_a", "half_b")] == [8, 8, 8]
    assert rows["half_a"][0]["n_possible"] == rows["half_b"][0]["n_possible"] == 24

    pairs, skipped = noise.field_pairs("x1", rows, ["2025/26"], RULE, "midpoint")
    by = {p["null"]: p for p in pairs}
    # halves: left exits 6 -> 11 Jan, right 11 -> 13 Jan; midpoints 8.5 and 12 Jan
    assert {by["halves"]["a"]["low"], by["halves"]["b"]["low"]} == {"2026-01-06", "2026-01-11"}
    assert by["halves"]["disagreement_days"] == 3.5
    # sensors: L30 3 -> 13 Jan, S30 11 -> 16 Jan (whole field mean 0.55 on 11 Jan is above half of 0.8)
    assert (by["l30_vs_s30"]["a"]["low"], by["l30_vs_s30"]["b"]["low"]) == ("2026-01-03", "2026-01-11")
    assert by["l30_vs_s30"]["disagreement_days"] == 5.5
    assert skipped == []

    doc = noise.build_doc(pairs, skipped, fields=["x1"], seasons=["2025/26"], rule=RULE, point="midpoint",
                          n1_rule=n1.N1Rule.from_protocol(base.load_config()))
    doc = json.loads(json.dumps(doc))  # what noise_floor.json holds
    assert (doc["percentile"], doc["percentile_method"], doc["rule"]["green_end_method"]) == (
        90.0, "nearest_rank", "M2-green-end-v2")
    assert (doc["n_halves"], doc["n_l30_vs_s30"], doc["unit"]) == (1, 1, "days")
    halves, sensors = noise.n1_inputs(doc)
    rule = n1.N1Rule.from_protocol(BENCH_PROTOCOL, percentile=90, method="nearest_rank")
    r = n1.evaluate(halves, sensors, rule)
    assert r.status == "reported" and float(r.floor_days) == 5.5 and r.set_by == "l30_vs_s30"
    assert r.to_dict()["n"] == 2


def test_small_halves_are_left_out_with_a_reason():
    rows = {"whole": [], "half_a": [{"n_possible": 10}], "half_b": [{"n_possible": 10}]}
    pairs, skipped = noise.field_pairs("x1", rows, ["2025/26"], RULE, "midpoint")
    assert pairs == [] and skipped[0]["null"] == "halves" and "fewer than 20" in skipped[0]["reason"]


def test_n1_inputs_checks_the_doc():
    doc = noise.build_doc([], [], fields=[], seasons=[], rule=RULE, point="low")
    assert noise.n1_inputs(doc) == ([], [])
    assert n1.evaluate(*noise.n1_inputs(doc), n1.N1Rule.from_protocol(
        BENCH_PROTOCOL, percentile=90, method="nearest_rank")).status == "not_completed"
    bad = {**doc, "n_halves": 3}
    with pytest.raises(ValueError):
        noise.n1_inputs(bad)
    with pytest.raises(ValueError):
        noise.n1_inputs({**doc, "unit": "dekads"})

# Written with an SI tool (Claude, Anthropic) for NASA Space Apps 2026. See AI_USE.md.
"""Relief of a field polygon: Horn slope on a geographic grid, classes, low spot. Offline.

Every DEM and polygon below is an example, fictitious (fixtures/fields_relief_example.json).
"""

import json
import math
from pathlib import Path

import numpy as np
import pytest

from pipeline.adapters import base
from pipeline.fields import relief

FIX = json.loads((Path(__file__).parent / "fixtures" / "fields_relief_example.json").read_text(encoding="utf-8"))


@pytest.fixture
def tmp_repo(tmp_path, monkeypatch):
    monkeypatch.setattr(base, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(base, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(base, "RAW_DIR", tmp_path / "data" / "raw")
    monkeypatch.setattr(base, "INTERIM_DIR", tmp_path / "data" / "interim")
    return tmp_path


def dem_from(data) -> dict:
    d = FIX["dem"]
    return {"data": np.asarray(data, dtype=np.float32), "step_deg": d["step_deg"], "lon0": d["lon0"],
            "lat0": d["lat0"], "crs": "EPSG:4326", "tile": "example"}


def ns_plane(pct: float) -> dict:
    """Height rises northwards by ``pct`` percent: z = base + pct/100 * metres north of the last row."""
    d = FIX["dem"]
    _, dy = relief.spacing_m(d["lat0"], d["step_deg"])
    rows = np.arange(d["rows"])[::-1, None] + np.zeros((1, d["cols"]))
    return dem_from(d["base_m"] + pct / 100 * dy * rows)


def ew_plane(pct: float) -> dict:
    d = FIX["dem"]
    lats = d["lat0"] - np.arange(d["rows"]) * d["step_deg"]
    dx, _ = relief.spacing_m(lats, d["step_deg"])
    return dem_from(d["base_m"] + pct / 100 * dx[:, None] * np.arange(d["cols"])[None, :])


def bowl(depth_m: float) -> dict:
    """Lowest at the field centre, ``depth_m`` higher about 400 m away."""
    d = FIX["dem"]
    dx, dy = relief.spacing_m(d["lat0"], d["step_deg"])
    ys, xs = np.mgrid[0:d["rows"], 0:d["cols"]]
    r2 = ((ys - 30) * dy) ** 2 + ((xs - 30) * dx) ** 2
    return dem_from(d["base_m"] + depth_m * r2 / 400 ** 2)


def field():
    return FIX["field"]


# Grid and slope ------------------------------------------------------------------

def test_metric_spacing_on_wgs84():
    dx, dy = relief.spacing_m(-23.08, 1 / 3600)
    assert 28.3 < float(dx) < 28.6 and 30.7 < dy < 30.8
    dx0, dy0 = relief.spacing_m(0.0, 1 / 3600)
    assert float(dx0) == pytest.approx(30.92, abs=0.01) and dy0 == pytest.approx(30.72, abs=0.01)


def test_horn_slope_of_planes():
    s = relief.slope_pct(ns_plane(5.0))
    inner = s[1:-1, 1:-1]
    assert np.allclose(inner, 5.0, atol=1e-3)
    assert np.isnan(s[0]).all() and np.isnan(s[:, -1]).all()  # window edge has no slope
    s = relief.slope_pct(ew_plane(12.0))
    assert np.allclose(s[1:-1, 1:-1], 12.0, rtol=1e-4)


def test_no_data_spreads_to_neighbours_only():
    dem = ns_plane(5.0)
    dem["data"][10, 10] = np.nan
    s = relief.slope_pct(dem)
    assert np.isnan(s[9:12, 9:12]).all() and np.isfinite(s[13, 13]) and np.isfinite(s[10, 13])


def test_classes_lower_edge_inclusive():
    cases = {0: "flat", 2.99: "flat", 3: "gentle", 7.99: "gentle", 8: "rolling", 20: "strongly_rolling",
             45: "mountainous", 75: "steep", 300: "steep"}
    for slope, code in cases.items():
        assert relief.class_of(slope) == code, slope
    for bad in (-1, math.nan, math.inf):
        with pytest.raises(relief.ReliefError):
            relief.class_of(bad)
    sh = relief.class_shares(np.array([1.0, 2.0, 5.0, 9.0, np.nan]))
    assert sh["flat"] == 0.5 and sh["gentle"] == 0.25 and sh["rolling"] == 0.25 and sum(sh.values()) == 1
    assert [c[0] for c in relief.CLASSES] == ["flat", "gentle", "rolling", "strongly_rolling", "mountainous", "steep"]


# Polygon -----------------------------------------------------------------------------

def centres_inside(dem: dict, poly) -> int:
    (x0, y0), (x1, _), (_, y1) = poly["coordinates"][0][:3]
    d = dem["step_deg"]
    rows, cols = dem["data"].shape
    lats = dem["lat0"] - np.arange(rows) * d
    lons = dem["lon0"] + np.arange(cols) * d
    inr = ((lats < max(y0, y1)) & (lats > min(y0, y1))).sum()
    inc = ((lons < max(x0, x1)) & (lons > min(x0, x1))).sum()
    return int(inr * inc)


def test_field_relief_is_classes_and_summaries_only():
    dem = ns_plane(FIX["plane_slope_pct"])
    rec = relief.field_relief(dem, field())
    assert rec["n_px"] == centres_inside(dem, field()) and rec["n_px"] > 20
    assert rec["slope_pct"]["median"] == pytest.approx(5.0, abs=1e-3)
    assert rec["slope_class"] == "gentle" and rec["class_shares"]["gentle"] == 1.0
    assert rec["context_only"] is True and rec["terrace_question"] is True
    assert rec["lowspot"]["status"] == "rule_not_set" and rec["lowspot_alert"] is None
    text = json.dumps(rec)  # serialisable, and no list as long as a pixel layer
    def longest(o):
        if isinstance(o, dict):
            return max([0, *map(longest, o.values())])
        if isinstance(o, list):
            return max([len(o), *map(longest, o)])
        return 0
    assert longest(json.loads(text)) <= len(relief.CLASSES)
    assert not any(k in text for k in ("LS", "t/ha", "erosion"))


def test_polygon_in_a_projected_crs_gives_the_same_pixels():
    from rasterio.warp import transform_geom

    dem = ns_plane(5.0)
    utm = transform_geom("EPSG:4326", "EPSG:32722", field())
    a = relief.field_relief(dem, field())["n_px"]
    b = relief.field_relief(dem, utm, crs="EPSG:32722")["n_px"]
    assert abs(a - b) <= 0.02 * a


def test_polygon_outside_or_wrong_type_is_an_error():
    dem = ns_plane(5.0)
    far = {"type": "Polygon", "coordinates": [[[-50, -22], [-49.9, -22], [-49.9, -22.1], [-50, -22]]]}
    with pytest.raises(relief.ReliefError, match="no NASADEM pixel"):
        relief.field_relief(dem, far)
    with pytest.raises(relief.ReliefError, match="Polygon"):
        relief.field_relief(dem, {"type": "Point", "coordinates": [-51.29, -23.057]})


def test_lowspot_only_with_a_rule():
    rule = FIX["lowspot_rule"]
    low = relief.field_relief(bowl(10.0), field(), rule=rule)
    assert low["lowspot"]["status"] == "computed" and low["lowspot_alert"] is True
    assert low["lowspot"]["field_minus_ring_m"] < -rule["drop_m"] and low["lowspot"]["n_ring_px"] > 0
    high = relief.field_relief(bowl(-10.0), field(), rule=rule)
    assert high["lowspot_alert"] is False
    flat = relief.field_relief(ns_plane(0.0), field(), rule=rule)
    assert flat["lowspot_alert"] is False and flat["slope_class"] == "flat"


def test_ring_is_outside_the_field_and_within_reach():
    dem = ns_plane(0.0)
    mask = relief.polygon_mask(dem, field())
    ring = relief.ring_mask(dem, mask, 100.0)
    assert not (ring & mask).any() and ring.any()
    rows, cols = np.nonzero(mask)
    rr, rc = np.nonzero(ring)
    assert rr.min() >= rows.min() - 4 and rr.max() <= rows.max() + 4  # 100 m is about 3 pixels of 31 m
    assert rc.min() >= cols.min() - 4 and rc.max() <= cols.max() + 4


def test_build_and_write_carry_the_keys_m5_reads(tmp_repo):
    cfg = base.load_config()
    rec = relief.build("example", ns_plane(10.0), field(), config=cfg)
    for k in ("slope_class", "lowspot_alert", "terrace_question", "ledger"):
        assert k in rec
    assert rec["ledger"] == "field_example.relief" and rec["slope_class"] == "rolling"
    path = relief.write(rec)
    assert path == base.INTERIM_DIR / "field_example_relief.json" and json.loads(path.read_text())["n_px"] > 0
    for bad in ("../x", "Example"):
        with pytest.raises(relief.ReliefError, match="unsafe"):
            relief.build(bad, ns_plane(1.0), field())
    assert relief.build("north-1", ns_plane(1.0), field())["ledger"] == "field_north-1.relief"


def test_inner_mask_on_the_dem_follows_the_geometry_rule():
    dem = ns_plane(5.0)
    ring = field()["coordinates"][0][:-1]
    whole = relief.inner_mask_on_dem(dem, ring, 0.0)
    assert abs(int(whole.sum()) - int(relief.polygon_mask(dem, field()).sum())) <= 0.02 * whole.sum()
    inner = relief.inner_mask_on_dem(dem, ring, 30.0)
    assert inner.sum() < whole.sum() and not (inner & ~whole).any()
    rec = relief.field_relief(dem, mask=inner)
    assert rec["n_px"] == int(inner.sum()) and rec["slope_class"] == "gentle"
    with pytest.raises(relief.ReliefError, match="differ"):
        relief.field_relief(dem, mask=inner[1:])
    assert relief.utm_crs(-51.3, -23.1) == "EPSG:32722" and relief.utm_crs(3.0, 45.0) == "EPSG:32631"


def test_cli_writes_records_and_the_slopes_of_select(tmp_repo, monkeypatch, tmp_path):
    from pipeline.adapters import nasadem
    from pipeline.fields import select

    ring = field()["coordinates"][0]
    gj = tmp_path / "candidates.geojson"
    gj.write_text(json.dumps({"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {"field_id": "k1"}, "geometry": {"type": "Polygon", "coordinates": [ring]}}]}),
        encoding="utf-8")
    monkeypatch.setattr(nasadem, "load_window", lambda spec, pad_px=None: ns_plane(10.0))
    out = tmp_path / "slopes.json"
    assert relief.main([str(gj), "--slopes", str(out)]) == 0
    rec = json.loads((base.INTERIM_DIR / "field_k1_relief.json").read_text(encoding="utf-8"))
    assert rec["ledger"] == "field_k1.relief"
    slopes = select.read_slopes(out)                                   # the file select.py --slopes reads
    assert slopes == {"k1": rec["slope_pct"]["median"]} and slopes["k1"] == pytest.approx(10.0, abs=1e-2)
    assert relief.slopes_path(gj) == base.INTERIM_DIR / "candidates_slopes.json"
    with pytest.raises(relief.ReliefError, match="twice"):
        relief.slopes([rec, rec])

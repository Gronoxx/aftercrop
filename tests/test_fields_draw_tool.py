# Written with an SI tool (Claude, Anthropic) for NASA Space Apps 2026. See AI_USE.md.
"""tools/draw_fields.html: offline, and its lon/lat maths agrees with PROJ and geometry.py. Offline.

The JavaScript between the "aftercrop-core" markers runs under node. Every grid and
polygon below is an example, fictitious, on a 30 m grid labelled UTM 22N with negative
northings like the southern HLS tiles (plan 9.2). Skipped when node is missing.
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest
from rasterio.crs import CRS
from rasterio.warp import transform

from pipeline.fields import geometry

HTML = Path(__file__).resolve().parents[1] / "tools" / "draw_fields.html"
TEXT = HTML.read_text(encoding="utf-8")
WKT = CRS.from_epsg(32622).to_wkt()
GEOREF = {"width": 347, "height": 374, "transform": [30.0, 0.0, 467130.0, 0.0, -30.0, -2546790.0],
          "crs_wkt": WKT, "crs_epsg": 32622, "window": {"id": "example_window"}}
NODE = shutil.which("node")


def core() -> str:
    m = re.search(r"// BEGIN aftercrop-core.*?\n(.*?)// END aftercrop-core", TEXT, re.S)
    assert m, "core markers missing"
    return m.group(1)


def run_node(tmp_path, body: str):
    script = tmp_path / "harness.mjs"
    script.write_text(core() + "\n" + body, encoding="utf-8")
    out = subprocess.run([NODE, str(script)], capture_output=True, text=True, timeout=60, check=True)
    return json.loads(out.stdout)


def test_page_loads_nothing_from_the_network():
    urls = re.findall(r"[a-z]+://[^\s\"')]*", TEXT)
    assert urls == ["http://www.w3.org/2000/svg"]  # the SVG namespace name, never fetched
    assert "<link" not in TEXT and not re.search(r"<script[^>]+src=", TEXT)
    assert "@import" not in TEXT
    assert '"../data/interim/basemap_"' in TEXT


needs_node = pytest.mark.skipif(NODE is None, reason="node not installed")


@needs_node
def test_projection_matches_proj(tmp_path):
    rng = np.random.default_rng(1)
    px = np.column_stack([rng.uniform(0, 347, 40), rng.uniform(0, 374, 40)]).tolist() + [[0, 0], [347, 374]]
    body = f"""
const g = {json.dumps(GEOREF)};
const p = checkGeoref(g);
const px = {json.dumps(px)};
const ll = px.map((q) => pxToLonLat(q[0], q[1], g, p));
const back = ll.map((q) => lonLatToPx(q[0], q[1], g, p));
const south = tmParams({{crs_epsg: 32722}});
const wktLess = tmParams({{crs_epsg: 32622}});
console.log(JSON.stringify({{ll, back, p, south, wktLess}}));
"""
    out = run_node(tmp_path, body)
    t = GEOREF["transform"]
    xs = [t[2] + c * t[0] for c, _ in px]
    ys = [t[5] + r * t[4] for _, r in px]
    lon, lat = transform(WKT, "EPSG:4326", xs, ys)
    got = np.array(out["ll"])
    # 1e-8 degree is about 1 mm on the ground
    assert np.abs(got[:, 0] - lon).max() < 1e-8 and np.abs(got[:, 1] - lat).max() < 1e-8
    assert np.abs(np.array(out["back"]) - np.array(px)).max() < 1e-4  # pixels, so about 3 mm
    assert out["p"]["lon0"] == -51 and out["p"]["fn"] == 0 and out["p"]["k0"] == 0.9996
    assert out["south"]["fn"] == 10000000 and out["wktLess"]["lon0"] == -51


@needs_node
def test_inner_pixel_count_and_ring_checks_match_geometry(tmp_path):
    polys = [[[10.3, 12.7], [60.2, 15.1], [55.4, 48.9], [14.8, 44.2]],
             [[100, 100], [140.5, 101], [150, 130.2], [120.1, 160.7], [96.2, 140]],
             [[200, 200], [204, 200], [204, 204], [200, 204]]]
    bowtie = [[10, 10], [30, 30], [30, 10], [10, 30]]
    body = f"""
const g = {json.dumps(GEOREF)};
const p = checkGeoref(g);
const polys = {json.dumps(polys)};
const counts = polys.map((q) => innerPixelCount(q, g, BUFFER_M));
const areas = polys.map((q) => areaHa(q, g));
const errs = [ringErrors({json.dumps(bowtie)}), ringErrors(polys[0])];
const cands = polys.map((q, i) => ({{id: "c" + (i + 1), pts: q, closed: true}}));
const gj = buildGeoJSON(cands, g, p, g.window.id);
const reread = parseGeoJSON(gj, g, p);
console.log(JSON.stringify({{counts, areas, errs, gj, reread, consts: [BUFFER_M, MIN_PIXELS, MAX_CANDIDATES, PRODUCT_LON_MAX, STRIP_LON_MIN]}}));
"""
    out = run_node(tmp_path, body)
    assert out["consts"] == [30, 20, 12, -51.25, -51.245]
    grid = geometry.grid_from_georef(GEOREF)
    t = GEOREF["transform"]
    for poly, n, ha in zip(polys, out["counts"], out["areas"]):
        xy = np.array([[t[2] + c * t[0], t[5] + r * t[4]] for c, r in poly])
        assert n == int(geometry.inner_mask(xy, grid, 30).sum())
        assert ha == pytest.approx(abs(geometry.signed_area(xy)) / 1e4)
    assert out["counts"][2] == 4  # a 4 x 4 square keeps 2 x 2
    assert "edges cross" in out["errs"][0] and out["errs"][1] == []

    # The export is what geometry.py reads, with the same pixels after the lon/lat round trip.
    gj = out["gj"]
    assert set(gj) == {"type", "features", "window_id"} and gj["window_id"] == "example_window"
    fields = geometry.parse_fields(gj)
    assert [f["field_id"] for f in fields] == ["c1", "c2", "c3"]
    assert all(set(f["properties"]) == {"field_id"} for f in gj["features"])
    rule = geometry.GeometryRule(30, 20)
    assert geometry.field_on_grid(fields[0], grid, rule)["n_possible"] == out["counts"][0]
    ring = np.array(gj["features"][0]["geometry"]["coordinates"][0])
    assert (ring[0] == ring[-1]).all()
    assert geometry.signed_area(ring[:-1]) > 0  # counter-clockwise in lon/lat (RFC 7946)
    for orig, back in zip(polys, out["reread"]):
        a = np.array(orig)
        b = np.array(back["pts"])
        assert len(a) == len(b)
        assert min(np.abs(np.roll(b, k, axis=0)[::d] - a).max() for k in range(len(b)) for d in (1, -1)) < 1e-3


def test_limits_match_the_protocol():
    from pipeline.adapters import base

    sec = base.load_config()["protocol"]["field"]
    consts = dict(re.findall(r"const (MAX_CANDIDATES|PRODUCT_LON_MAX|STRIP_LON_MIN) = (-?[\d.]+);", core()))
    assert int(consts["MAX_CANDIDATES"]) == sec["candidates"][1]
    assert float(consts["PRODUCT_LON_MAX"]) == sec["product_area"]["lon_max"]
    assert float(consts["STRIP_LON_MIN"]) == sec["development_strip"]["lon_min"]


@needs_node
def test_meridians_and_zones(tmp_path):
    body = f"""
const g = {json.dumps(GEOREF)};
const p = checkGeoref(g);
const ll0 = pxToLonLat(0, 374, g, p), ll1 = pxToLonLat(347, 0, g, p);
const gb = Object.assign({{}}, g, {{bounds_lonlat_approx: {{lon_min: ll0[0], lat_min: ll0[1], lon_max: ll1[0], lat_max: ll1[1]}}}});
const line = meridianPx(-51.3, gb, p, 8);
const back = line.map((q) => pxToLonLat(q[0], q[1], gb, p)[0]);
console.log(JSON.stringify({{none: meridianPx(-51.3, g, p), outside: meridianPx(-40, gb, p), n: line.length, back,
  zones: [zoneOf([-51.3, -51.26]), zoneOf([-51.24, -51.2]), zoneOf([-51.26, -51.24]), zoneOf([-51.248, -51.247])]}}));
"""
    out = run_node(tmp_path, body)
    assert out["none"] is None and out["outside"] is None   # no bounds, or the meridian off the basemap
    assert out["n"] == 9 and max(abs(v + 51.3) for v in out["back"]) < 1e-9
    assert out["zones"] == ["product", "strip", "neither", "neither"]

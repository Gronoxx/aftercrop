# Written with an SI tool (Claude, Anthropic) for NASA Space Apps 2026. See AI_USE.md.
"""Field polygons (M2.1): validation, 30 m inner buffer in metres, possible pixels, halves. Offline.

Every grid and polygon below is an example, fictitious: a 60 x 60 grid of 30 m pixels
labelled UTM 22N with negative northings, like the southern HLS tiles (plan 9.2).
No polygon is a real field and no HLS file is read.
"""

import copy
import json
import math

import numpy as np
import pytest
from rasterio.crs import CRS
from rasterio.warp import transform

from pipeline.adapters import base
from pipeline.bench.common import ProtocolError
from pipeline.fields import geometry

WKT = CRS.from_epsg(32622).to_wkt()
T = (30.0, 0.0, 470000.0, 0.0, -30.0, -2550000.0)
GRID = geometry.make_grid(T, (60, 60), WKT)
RULE = geometry.GeometryRule(inner_buffer_m=30, min_clean_pixels=20)


def lonlat(xy, crs=WKT):
    xs, ys = zip(*xy)
    lon, lat = transform(crs, "EPSG:4326", list(xs), list(ys))
    return list(zip(lon, lat))


def px_to_xy(cols_rows):
    return [(T[2] + c * T[0], T[5] + r * T[4]) for c, r in cols_rows]


def feature(fid, ring_lonlat, **props):
    ring = [list(p) for p in ring_lonlat] + [list(ring_lonlat[0])]
    return {"type": "Feature", "properties": {"field_id": fid, **props},
            "geometry": {"type": "Polygon", "coordinates": [ring]}}


def collection(*feats, **top):
    return {"type": "FeatureCollection", "features": list(feats), **top}


SQUARE = lonlat(px_to_xy([(10, 10), (30, 10), (30, 30), (10, 30)]))  # 20 x 20 pixels, edges on pixel edges


def field(ring, fid="x1"):
    return {"field_id": fid, "criterion": None, "ring": ring}


# Inner buffer ----------------------------------------------------------------------------

def test_buffer_of_30_m_drops_the_outer_ring_of_pixels():
    r = geometry.field_on_grid(field(SQUARE), GRID, RULE)
    assert r["n_possible"] == 18 * 18  # centres 15 m from the edge leave, 45 m stay
    assert r["area_ha"] == pytest.approx(36.0, rel=1e-9)
    assert r["mask"][11:29, 11:29].all() and r["mask"].sum() == 324
    assert r["polygon_px"] == [[10.0, 10.0], [30.0, 10.0], [30.0, 30.0], [10.0, 30.0]]
    assert geometry.field_on_grid(field(SQUARE), GRID, geometry.GeometryRule(0, 20))["n_possible"] == 400
    assert geometry.field_on_grid(field(SQUARE), GRID, geometry.GeometryRule(60, 20))["n_possible"] == 16 * 16


def test_buffer_matches_half_planes_on_a_convex_pentagon():
    # For a convex polygon the inner polygon is the set at distance >= d inside every edge line.
    xy = np.array(px_to_xy([(5.3, 8.1), (40.7, 4.2), (52.2, 30.6), (30.1, 51.9), (7.8, 40.4)]))
    mask = geometry.inner_mask(xy, GRID, 30)
    cc, rr = np.meshgrid(np.arange(60), np.arange(60))
    px, py = T[2] + (cc + 0.5) * T[0], T[5] + (rr + 0.5) * T[4]
    ok = np.ones(px.shape, dtype=bool)
    sign = math.copysign(1, geometry.signed_area(xy))
    for k in range(len(xy)):
        (x1, y1), (x2, y2) = xy[k], xy[(k + 1) % len(xy)]
        inward = sign * ((x2 - x1) * (py - y1) - (y2 - y1) * (px - x1)) / math.hypot(x2 - x1, y2 - y1)
        ok &= inward >= 30
    assert mask.sum() > 500
    assert (mask == ok).all()


def test_too_few_possible_pixels_is_refused():
    small = lonlat(px_to_xy([(10, 10), (15, 10), (15, 15), (10, 15)]))  # 5 x 5 -> 3 x 3 inside the strip
    with pytest.raises(geometry.GeometryError, match="9 possible clean pixels"):
        geometry.field_on_grid(field(small), GRID, RULE)
    assert geometry.field_on_grid(field(small), GRID, geometry.GeometryRule(30, 9))["n_possible"] == 9


def test_crs_comes_from_the_file():
    # The same lon/lat polygon on the same ground grid, labelled UTM 22S (northing + 10,000 km).
    south = CRS.from_epsg(32722).to_wkt()
    grid_s = geometry.make_grid((30.0, 0.0, 470000.0, 0.0, -30.0, -2550000.0 + 1e7), (60, 60), south)
    a = geometry.field_on_grid(field(SQUARE), GRID, RULE)["mask"]
    b = geometry.field_on_grid(field(SQUARE), grid_s, RULE)["mask"]
    assert (a == b).all()


def test_vertex_outside_the_window_is_refused():
    out = lonlat(px_to_xy([(10, 10), (70, 10), (70, 30), (10, 30)]))
    with pytest.raises(geometry.GeometryError, match="outside the HLS window"):
        geometry.field_on_grid(field(out), GRID, RULE)


# GeoJSON checks (M2.1) ---------------------------------------------------------------------

def test_parse_keeps_only_id_criterion_and_ring():
    fs = geometry.parse_fields(collection(feature("c1", SQUARE), feature("a", SQUARE, criterion="Example, fictitious"),
                                          window_id="example_window"))
    assert [f["field_id"] for f in fs] == ["c1", "a"]
    assert fs[1]["criterion"] == "Example, fictitious"
    assert len(fs[0]["ring"]) == 4


@pytest.mark.parametrize("mutate, msg", [
    (lambda d: d["features"][0]["properties"].update(owner="someone"), "not allowed"),
    (lambda d: d["features"][0].update(id=7), "not allowed"),
    (lambda d: d.update(name="farm"), "not allowed"),
    (lambda d: d["features"][0]["properties"].update(field_id="C1"), "field_id"),
    (lambda d: d["features"][0]["properties"].update(field_id="../x"), "field_id"),
    (lambda d: d["features"].append(copy.deepcopy(d["features"][0])), "repeated"),
    (lambda d: d["features"][0]["geometry"].update(type="MultiPolygon"), "Polygon"),
    (lambda d: d["features"][0]["geometry"]["coordinates"].append(d["features"][0]["geometry"]["coordinates"][0]),
     "no holes"),
    (lambda d: d["features"][0]["geometry"]["coordinates"][0].pop(), "not closed"),
    (lambda d: d["features"][0]["geometry"]["coordinates"][0][0].append(0.0), "position"),
    (lambda d: d["features"][0]["geometry"]["coordinates"][0].__setitem__(1, [200.0, 0.0]), "lon/lat"),
    (lambda d: d.update(features=[]), "no features"),
    (lambda d: d.update(type="Feature"), "FeatureCollection"),
])
def test_parse_refuses(mutate, msg):
    d = collection(feature("c1", SQUARE))
    mutate(d)
    with pytest.raises(geometry.GeometryError, match=msg):
        geometry.parse_fields(d)


def test_self_intersection_and_degenerate_rings_are_refused():
    bowtie = lonlat(px_to_xy([(10, 10), (30, 30), (30, 10), (10, 30)]))
    with pytest.raises(geometry.GeometryError, match="self-intersection"):
        geometry.parse_fields(collection(feature("c1", bowtie)))
    line = [(-51.30, -23.10), (-51.29, -23.10), (-51.28, -23.10)]
    with pytest.raises(geometry.GeometryError, match="zero area"):
        geometry.parse_fields(collection(feature("c1", line)))
    sliver = lonlat(px_to_xy([(10, 10), (20, 10), (30, 10)]))  # not exactly collinear in lon/lat
    with pytest.raises(geometry.GeometryError):
        geometry.field_on_grid(geometry.parse_fields(collection(feature("c1", sliver)))[0], GRID, RULE)
    spike = px_to_xy([(10, 10), (30, 10), (20, 10), (20, 30)])
    assert any("fold back" in e for e in geometry.ring_errors(np.array(spike)))


def test_consecutive_duplicate_vertices_are_dropped():
    ring = [SQUARE[0], SQUARE[0], SQUARE[1], SQUARE[2], SQUARE[3], SQUARE[3]]
    assert len(geometry.parse_fields(collection(feature("c1", ring)))[0]["ring"]) == 4


# Halves (N1) ----------------------------------------------------------------------------------

def test_halves_split_across_the_long_axis():
    wide = lonlat(px_to_xy([(5, 20), (45, 20), (45, 30), (5, 30)]))  # 40 x 10 pixels
    mask = geometry.field_on_grid(field(wide), GRID, RULE)["mask"]
    a, b = geometry.halves(mask, GRID)
    assert a.sum() == b.sum() == mask.sum() // 2
    assert not (a & b).any() and ((a | b) == mask).all()
    ca, cb = np.nonzero(a)[1], np.nonzero(b)[1]
    assert ca.max() < cb.min()  # left and right, not top and bottom
    with pytest.raises(geometry.GeometryError):
        geometry.halves(np.zeros((3, 3), dtype=bool), GRID)


def test_field_masks_cache_by_grid():
    fm = geometry.FieldMasks(field(SQUARE), RULE, with_halves=True)
    m1 = fm(GRID)
    assert set(m1) == {"whole", "half_a", "half_b"} and m1 is fm(dict(GRID))
    assert m1["half_a"].sum() + m1["half_b"].sum() == 324


# Protocol and CLI summary --------------------------------------------------------------------

def test_rule_from_protocol():
    cfg = base.load_config()
    r = geometry.GeometryRule.from_protocol(cfg)
    assert (r.inner_buffer_m, r.min_clean_pixels) == (30, 20)
    p = copy.deepcopy(cfg)
    p["protocol"]["field"]["min_clean_pixels"] = None
    with pytest.raises(ProtocolError, match="null"):
        geometry.GeometryRule.from_protocol(p)
    del p["protocol"]["field"]
    with pytest.raises(ProtocolError):
        geometry.GeometryRule.from_protocol(p)


def test_check_file_reports_each_polygon(tmp_path):
    georef = {"width": 60, "height": 60, "transform": list(T), "crs_wkt": WKT, "crs_epsg": 32622}
    small = lonlat(px_to_xy([(40, 40), (44, 40), (44, 44), (40, 44)]))
    path = tmp_path / "candidates.geojson"
    path.write_text(json.dumps(collection(feature("c1", SQUARE), feature("c2", small), window_id="example_window")))
    s = geometry.check_file(path, georef, RULE, "example_window")
    assert [(f["field_id"], f["ok"]) for f in s["fields"]] == [("c1", True), ("c2", False)]
    assert s["fields"][0]["n_possible"] == 324 and "possible clean pixels" in s["fields"][1]["error"]
    json.dumps(s)
    with pytest.raises(geometry.GeometryError, match="drawn on window"):
        geometry.check_file(path, georef, RULE, "another_window")


def test_grid_refuses_rotation_and_missing_crs():
    with pytest.raises(geometry.GeometryError):
        geometry.make_grid((30, 1, 0, 0, -30, 0), (2, 2), WKT)
    with pytest.raises(geometry.GeometryError):
        geometry.make_grid(T, (2, 2), "")


# Product area and development strip (SA-D63) ---------------------------------------------

AREA = geometry.AreaRule(product_lon_max=-51.25, strip_lon_min=-51.245)


def box(lon0, lon1, lat0=-23.20, lat1=-23.19):
    return [(lon0, lat0), (lon1, lat0), (lon1, lat1), (lon0, lat1)]


def test_area_rule_by_mode():
    west, east, across, between = (field(box(-51.30, -51.26)), field(box(-51.24, -51.23)),
                                   field(box(-51.26, -51.24)), field(box(-51.249, -51.246)))
    assert AREA.error(west) is None
    assert "use --rehearsal" in AREA.error(east) and "product area limit -51.25" in AREA.error(east)
    assert "use --rehearsal" not in AREA.error(across)
    assert AREA.error(between) is not None and AREA.error(between, rehearsal=True) is not None
    assert AREA.error(east, rehearsal=True) is None
    assert "Product polygons are refused in rehearsal mode" in AREA.error(west, rehearsal=True)
    assert AREA.error(field(box(-51.30, -51.25))) is not None              # on the limit is not west of it
    assert AREA.error(field(box(-51.245, -51.23)), rehearsal=True) is not None
    with pytest.raises(geometry.GeometryError, match="x1"):
        geometry.check_area([west, east], AREA)
    geometry.check_area([west], AREA)


def test_area_rule_from_protocol():
    cfg = base.load_config()
    assert geometry.AreaRule.from_protocol(cfg) == AREA
    p = copy.deepcopy(cfg)
    p["protocol"]["field"].pop("product_area")
    with pytest.raises(ProtocolError, match="product_area"):
        geometry.AreaRule.from_protocol(p)
    p = copy.deepcopy(cfg)
    p["protocol"]["field"]["development_strip"]["lon_min"] = None
    with pytest.raises(ProtocolError, match="lon_min is null"):
        geometry.AreaRule.from_protocol(p)
    with pytest.raises(ProtocolError, match="overlap"):
        geometry.AreaRule(product_lon_max=-51.2, strip_lon_min=-51.3)


def test_load_fields_and_check_file_refuse_by_mode(tmp_path):
    cfg = base.load_config()
    path = tmp_path / "fields.geojson"
    path.write_text(json.dumps(collection(feature("c1", box(-51.24, -51.23)))))
    with pytest.raises(geometry.GeometryError, match="not west of the product area limit"):
        geometry.load_fields(path, cfg)
    assert [f["field_id"] for f in geometry.load_fields(path, cfg, rehearsal=True)] == ["c1"]
    georef = {"width": 60, "height": 60, "transform": list(T), "crs_wkt": WKT, "crs_epsg": 32622}
    ok = tmp_path / "west.geojson"
    ok.write_text(json.dumps(collection(feature("c1", SQUARE))))     # the example grid lies near lon -51.29
    s = geometry.check_file(ok, georef, RULE, area=AREA)
    assert s["mode"] == "product" and s["fields"][0]["ok"]
    r = geometry.check_file(ok, georef, RULE, area=AREA, rehearsal=True)
    assert r["mode"] == "rehearsal" and not r["fields"][0]["ok"] and "rehearsal mode" in r["fields"][0]["error"]


# Municipal boundary (SA-D64), checked when the polygon is drawn ---------------------------

def _mesh_split_at(lon_split):
    from pipeline.adapters import ibge_malhas
    lons = [p[0] for p in SQUARE]
    lats = [p[1] for p in SQUARE]
    lo, hi, la, lb = min(lons) - 0.1, max(lons) + 0.1, min(lats) - 0.1, max(lats) + 0.1

    def poly(x0, x1):
        return {"type": "Polygon", "coordinates": [[[x0, la], [x1, la], [x1, lb], [x0, lb], [x0, la]]]}
    return ibge_malhas.parse_mesh({"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {"codarea": "9999901"}, "geometry": poly(lo, lon_split)},
        {"type": "Feature", "properties": {"codarea": "9999902"}, "geometry": poly(lon_split, hi)}]})


def test_check_file_refuses_a_polygon_across_a_municipal_boundary(tmp_path):
    # Rehearsal regression: field c crossed the Cambé / Bela Vista do Paraíso boundary, passed
    # geometry and only failed at make decision-base, which stopped the rest of make piso.
    georef = {"width": 60, "height": 60, "transform": list(T), "crs_wkt": WKT, "crs_epsg": 32622}
    path = tmp_path / "fields.geojson"
    path.write_text(json.dumps(collection(feature("c1", SQUARE))))
    lons = sorted(p[0] for p in SQUARE)
    across = geometry.check_file(path, georef, RULE, area=AREA, mesh=_mesh_split_at((lons[0] + lons[-1]) / 2))
    f = across["fields"][0]
    assert not f["ok"] and "crosses the boundary" in f["error"] and across["municipality_checked"]
    inside = geometry.check_file(path, georef, RULE, area=AREA, mesh=_mesh_split_at(lons[-1] + 0.01))
    assert inside["fields"][0]["ok"] and inside["fields"][0]["municipio_ibge"] == "9999901"
    unchecked = geometry.check_file(path, georef, RULE, area=AREA)
    assert unchecked["fields"][0]["ok"] and not unchecked["municipality_checked"]
    assert "municipio_ibge" not in unchecked["fields"][0]

# Written with an SI tool (Claude, Anthropic) for NASA Space Apps 2026. See AI_USE.md.
"""Municipality of a field from the IBGE mesh, by the polygon (SA-D64, protocol.field.municipality).

Every mesh here is synthetic: two square "municipalities" side by side, with fictitious codes.
"""

import json

import pytest

from pipeline.adapters import base, ibge_malhas as im


def square(x0, y0, x1, y1):
    return [[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]]


# West 9999901 from lon -2 to -1, east 9999902 from -1 to 0, both lat -1 to 0. East has a hole.
MESH_DOC = {"type": "FeatureCollection", "features": [
    {"type": "Feature", "properties": {"codarea": "9999901"},
     "geometry": {"type": "Polygon", "coordinates": [square(-2, -1, -1, 0)]}},
    {"type": "Feature", "properties": {"codarea": "9999902"},
     "geometry": {"type": "MultiPolygon", "coordinates": [[square(-1, -1, 0, 0), square(-0.4, -0.6, -0.2, -0.4)]]}},
]}
MESH = im.parse_mesh(MESH_DOC)


def test_a_field_inside_one_municipality():
    r = im.field_municipality(MESH, square(-1.6, -0.6, -1.4, -0.4))
    assert r == {"ok": True, "ibge": "9999901", "centroid": [-1.5, -0.5], "reason": None}
    assert im.field_municipality(MESH, square(-0.9, -0.9, -0.7, -0.7))["ibge"] == "9999902"


def test_a_field_across_a_boundary_is_refused():
    r = im.field_municipality(MESH, square(-1.1, -0.6, -0.95, -0.4))     # centroid in the west, edge in the east
    assert not r["ok"] and r["ibge"] == "9999901" and "crosses the boundary" in r["reason"]
    # Touching the boundary counts as crossing it.
    assert not im.field_municipality(MESH, square(-1.2, -0.6, -1.0, -0.4))["ok"]
    # A field around the hole is not inside the municipality either.
    assert not im.field_municipality(MESH, square(-0.45, -0.65, -0.15, -0.35))["ok"]


def test_a_field_outside_the_mesh_is_refused():
    r = im.field_municipality(MESH, square(5, 5, 5.1, 5.1))
    assert not r["ok"] and r["ibge"] is None and "no municipality" in r["reason"]
    assert not im.field_municipality(MESH, [[-1.5, -0.5], [-1.4, -0.5], [-1.5, -0.5]])["ok"]


def test_area_centroid_not_the_vertex_mean():
    # An L-shaped ring with many vertices on one side: the vertex mean would move, the centroid does not.
    ring = [[0, 0], [2, 0], [2, 1], [1, 1], [1, 2], [0, 2], [0, 0]]
    cx, cy = im.area_centroid(ring)
    assert (round(cx, 6), round(cy, 6)) == (round(5 / 6, 6), round(5 / 6, 6))
    assert im.area_centroid(list(reversed(ring))) == pytest.approx((cx, cy))


def test_window_cover_lists_the_municipalities_by_band():
    cover = im.window_cover(MESH, [-1.5, -0.9, -0.5, -0.1], step=0.1,
                            lon_splits={"west_part": [None, -1.25], "east_part": [-0.75, None]})
    win = {m["ibge"]: m["share"] for m in cover["bands"]["window"]["municipalities"]}
    assert set(win) == {"9999901", "9999902"} and sum(win.values()) == pytest.approx(1)
    assert [m["ibge"] for m in cover["bands"]["west_part"]["municipalities"]] == ["9999901"]
    assert {m["ibge"] for m in cover["bands"]["east_part"]["municipalities"]} == {"9999902"}


@pytest.mark.parametrize("doc", [
    {"type": "Feature"},
    {"type": "FeatureCollection", "features": []},
    {"type": "FeatureCollection", "features": [{"properties": {"codarea": "41"}, "geometry": MESH_DOC["features"][0]["geometry"]}]},
    {"type": "FeatureCollection", "features": [{"properties": {"codarea": "9999901"},
                                                "geometry": {"type": "Polygon", "coordinates": [[[0, 0], [1, "x"], [1, 1], [0, 0]]]}}]},
    {"type": "FeatureCollection", "features": [{"properties": {"codarea": "9999901"},
                                                "geometry": {"type": "Point", "coordinates": [0, 0]}}]},
    {"type": "FeatureCollection", "features": [MESH_DOC["features"][0], MESH_DOC["features"][0]]},
])
def test_untrusted_mesh_is_checked(doc):
    with pytest.raises(im.MeshError):
        im.parse_mesh(doc)


def test_read_refuses_an_oversized_file(tmp_path, monkeypatch):
    p = tmp_path / "mesh.geojson"
    p.write_text(json.dumps(MESH_DOC), encoding="utf-8")
    assert set(im.read(p)) == {"9999901", "9999902"}
    monkeypatch.setattr(im, "MAX_BYTES", 10)
    with pytest.raises(im.MeshError, match="limit"):
        im.read(p)


def test_spec_is_one_cached_file_of_its_own_source():
    s = im.spec(base.load_config())
    assert s["source_id"] == im.SOURCE_ID == "ibge_malhas"
    assert im.path_of(s).parent == base.RAW_DIR / "ibge_malhas"
    assert s["params"]["intrarregiao"] == "municipio" and s["url"].endswith("/malhas/estados/41")

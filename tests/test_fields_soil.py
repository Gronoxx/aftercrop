# Written with an SI tool (Claude, Anthropic) for NASA Space Apps 2026. See AI_USE.md.
"""Field soil profile from SoilGrids and the soil-domain rule of plan 13.7. Offline.

Every SoilGrids value, polygon and declared soil below is an example, fictitious
(fixtures/fields_soil_example.json).
"""

import json
import math
import re
from pathlib import Path

import numpy as np
import pytest
import yaml

from pipeline.adapters import base, soilgrids
from pipeline.fields import soil_profile as sp

FIX = json.loads((Path(__file__).parent / "fixtures" / "fields_soil_example.json").read_text(encoding="utf-8"))
REQUIRED = {"source_id", "url", "params", "retrieved_at", "sha256", "bytes", "http_status"}


@pytest.fixture(scope="module")
def rule():
    return base.load_config()["protocol"]["soil_domain"]


@pytest.fixture
def tmp_repo(tmp_path, monkeypatch):
    monkeypatch.setattr(base, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(base, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(base, "RAW_DIR", tmp_path / "data" / "raw")
    monkeypatch.setattr(base, "INTERIM_DIR", tmp_path / "data" / "interim")
    return tmp_path


def means(**override) -> dict:
    """Area means as layer_means returns them, the same mapped value at every depth."""
    mapped = {**FIX["mapped"], **override}
    out = {}
    for d in sp.DEPTHS:
        for p in sp.MEAN_PROPERTIES:
            out[f"{p}_{d}_mean"] = {"raw": float(mapped[p]), "data_share": 1.0, "n_px": 4}
        for q in sp.QUANTILES:
            out[f"clay_{d}_{q}"] = {"raw": float(mapped[f"clay_{q}"]), "data_share": 1.0, "n_px": 4}
    return out


# Soil domain (13.7) ----------------------------------------------------------------

def test_three_bands_and_their_edges(rule):
    assert rule["decides"] == "user_declared_only"
    cases = {0: "not_enough_evidence", 349.9: "not_enough_evidence", 350: "use_with_care", 480: "use_with_care",
             600: "use_with_care", 600.1: "inside", 780: "inside", 1000: "inside"}
    for clay, b in cases.items():
        assert sp.band(clay, rule) == b, clay
    for bad in (True, math.nan, -1, 1000.5, "700", None):
        with pytest.raises(sp.SoilError):
            sp.band(bad, rule)


def test_only_declared_data_gives_a_state(rule):
    none = sp.soil_domain(None, rule)
    assert none["state"] is None and none["decided_by"] is None and none["copy_key"] == "page.answer.transfer_warning"
    assert sp.soil_domain({"no_till": True}, rule)["state"] is None
    conv = sp.soil_domain({"clay_g_kg": 800, "no_till": False}, rule)
    assert conv["state"] == "not_enough_evidence" and conv["label"] == rule["conventional_tillage"]
    care = sp.soil_domain({"clay_g_kg": 450}, rule)
    assert (care["state"], care["label"], care["decided_by"]) == ("use_with_care", "Use with care", "user_declared")
    assert sp.soil_domain({"clay_g_kg": 780, "no_till": True}, rule)["state"] == "inside"
    assert sp.soil_domain({"clay_g_kg": 300, "no_till": True}, rule)["state"] == "not_enough_evidence"
    with pytest.raises(sp.SoilError, match="no_till"):
        sp.soil_domain({"no_till": "yes"}, rule)
    with pytest.raises(sp.SoilError, match="user_declared_only"):
        sp.soil_domain({"clay_g_kg": 780}, {**rule, "decides": "soilgrids"})


def test_soilgrids_never_decides(tmp_repo):
    cfg = base.load_config()
    rich = sp.build("example", means(clay=800), declared=None, config=cfg)
    assert rich["domain"]["state"] is None and rich["decides"] is False
    assert rich["estimated_band"] == {**rich["estimated_band"], "band": "inside", "decides": False}
    poor = sp.build("example", means(clay=200, sand=500), declared={"clay_g_kg": 780, "no_till": True}, config=cfg)
    assert poor["domain"]["state"] == "inside" and poor["domain"]["decided_by"] == "user_declared"
    assert poor["estimated_band"]["band"] == "not_enough_evidence"  # shown beside it, never used


# Profile ---------------------------------------------------------------------------

def test_profile_units_layers_and_extension():
    cfg = base.load_config()
    layers = sp.profile(means(), cfg["protocol"]["engine"]["profile_min_depth_m"])
    assert [(x["top_m"], x["bottom_m"]) for x in layers] == [
        (0.0, 0.05), (0.05, 0.15), (0.15, 0.3), (0.3, 0.6), (0.6, 1.0), (1.0, 2.4)]
    assert layers[-1]["extended_from_m"] == 2.0 and "extended_from_m" not in layers[0]
    top = layers[0]
    assert (top["clay_pct"], top["sand_pct"], top["silt_pct"]) == (65.0, 12.0, 23.0)
    assert top["clay_g_kg"] == 650 and top["clay_g_kg_q05_q95"] == [560, 740]
    assert top["soc_g_kg"] == 15.0 and top["om_pct"] == round(15.0 * 1.724 / 10, 2)
    assert top["bdod_kg_dm3"] == 1.25 and top["cec_cmol_kg"] == 9.5
    missing = means()
    missing["clay_30-60cm_mean"]["raw"] = None
    with pytest.raises(sp.SoilError, match="30-60cm"):
        sp.profile(missing, 2.4)


def test_textural_groups_of_sibcs(rule):
    cases = [((650, 100), "very_clayey"), ((400, 300), "clayey"), ((600, 100), "clayey"),
             ((100, 850), "sandy"), ((200, 100), "silty"), ((250, 400), "medium")]
    for (clay, sand), code in cases:
        assert sp.textural_group(clay, sand, rule)["code"] == code, (clay, sand)


def test_build_carries_the_keys_m5_reads(tmp_repo):
    rec = sp.build("example", means(), config=base.load_config())
    for k in ("source", "layers", "clay_q05_q95", "estimated_class", "zarc_ad_class", "transfer_warning",
              "lab_report_request", "ledger"):
        assert k in rec, k
    for lay in rec["layers"]:
        assert {"top_m", "bottom_m", "clay_pct", "sand_pct", "om_pct"} <= set(lay)
    assert rec["clay_q05_q95"] == [56.0, 74.0]
    # The page fills "Soil used: {class}, estimated" with a string; the group stays beside it.
    assert rec["estimated_class"] == "very clayey" and rec["estimated_class_detail"]["code"] == "very_clayey"
    assert rec["estimated_class_detail"]["estimated"] is True
    # The ids the ledger, the manifest and the page use.
    assert rec["ledger"] == "field_example.soil"
    assert rec["source_id"] == sp.SOURCE_ID == base.load_config()["sources"]["soilgrids"]["source_id"]
    path = sp.write(rec)
    assert path == base.INTERIM_DIR / "field_example_soil.json"
    for bad in ("a/b", "A", "x" * 40):
        with pytest.raises(sp.SoilError, match="unsafe"):
            sp.build(bad, means())
    assert sp.build("north-1", means())["ledger"] == "field_north-1.soil"     # geometry.FIELD_ID allows "-"


def test_zarc_soil_class_is_a_loud_gap(tmp_repo):
    """The Tábua indexes soil by AD1 to AD6; without their function in the protocol, no class (never silent)."""
    rec = sp.build("example", means(), config=base.load_config())
    assert rec["zarc_ad_class"] is None and rec["zarc_ad_status"] == "unknown"     # SA-D64: no report
    assert "AD1 to AD6" in rec["zarc_ad_note"]
    assert rec["zarc_tipo_2008"] == "Tipo 3"                        # 65% clay, history only
    assert sp.zarc_tipo_2008(34.9) == "Tipo 2" and sp.zarc_tipo_2008(35) == "Tipo 3"
    assert sp.zarc_tipo_2008(9.9) is None and sp.zarc_tipo_2008(None) is None
    assert rec["zarc_ad_status"] in sp.ZARC_AD_STATUSES


def test_cli_writes_one_record_per_field(tmp_repo, monkeypatch, tmp_path):
    ring = FIX["field"]["coordinates"][0]
    gj = tmp_path / "fields.geojson"
    gj.write_text(json.dumps({"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {"field_id": "a"}, "geometry": {"type": "Polygon", "coordinates": [ring]}}]}),
        encoding="utf-8")
    seen = []

    def fake_fetch(geom, config=None):
        seen.append(geom)
        return {"stub": tmp_path / "stub.tif"}

    monkeypatch.setattr(sp, "fetch_layers", fake_fetch)
    monkeypatch.setattr(sp, "layer_means", lambda paths, geom: means())
    assert sp.main([str(gj)]) == 0
    rec = json.loads((base.INTERIM_DIR / "field_a_soil.json").read_text(encoding="utf-8"))
    assert rec["ledger"] == "field_a.soil" and rec["estimated_class"] == "very clayey"
    coords = seen[0]["coordinates"][0]
    assert coords[0] == coords[-1] and len(coords) == len(ring)          # the ring is closed again


def test_copy_keys_exist():
    copy = yaml.safe_load((base.REPO_ROOT / "config" / "copy_en.yaml").read_text(encoding="utf-8"))
    for key in sp.COPY_KEYS.values():
        node = copy
        for part in key.split("."):
            node = node[part]
        assert isinstance(node, str), key


# Area mean -------------------------------------------------------------------------

def test_pixel_weights_and_area_mean():
    t = (250.0, 0.0, 0.0, 0.0, -250.0, 1000.0)  # 4 x 4 example grid in metres
    square = {"type": "Polygon", "coordinates": [[[0, 1000], [375, 1000], [375, 750], [0, 750], [0, 1000]]]}
    w = sp.pixel_weights(square, t, (4, 4))
    assert w[0, 0] == 1.0 and w[0, 1] == 0.5 and w[1:].sum() == 0 and w[0, 2:].sum() == 0
    v = np.array([[600.0, 900.0, 0, 0], [0, 0, 0, 0], [0, 0, 0, 0], [0, 0, 0, 0]])
    mean, wd, total = sp.area_mean(v, w)
    assert mean == pytest.approx((600 + 0.5 * 900) / 1.5) and wd == total == 1.5
    v[0, 1] = np.nan
    mean, wd, total = sp.area_mean(v, w)
    assert mean == 600 and wd == 1.0 and total == 1.5
    with pytest.raises(sp.SoilError, match="covers no"):
        sp.area_mean(v, np.zeros((4, 4)))


def test_polygon_box_in_the_service_crs():
    x0, y0, x1, y1 = sp.igh_box(FIX["field"], margin_px=1)
    corners = [soilgrids.igh_xy(lat, lon) for lon, lat in FIX["field"]["coordinates"][0]]
    assert x0 == pytest.approx(min(c[0] for c in corners) - 250, abs=1e-6)
    assert y1 == pytest.approx(max(c[1] for c in corners) + 250, abs=1e-6)
    assert x1 - x0 > 500 and y1 - y0 > 500
    _, params, name = sp.request("https://example.test/mapserv", "clay_0-5cm_Q0.05", (x0, y0, x1, y1))
    assert params["SUBSET"][0].startswith("x(") and "SUBSETTINGCRS" not in params
    assert params["map"] == "/map/clay.map" and name.startswith("field_clay_0-5cm_Q0.05_")
    assert len(sp.coverages()) == 6 * (len(sp.MEAN_PROPERTIES) + len(sp.QUANTILES))


def wcs_tiff(x0: float, y1: float, cols: int, rows: int, value: int) -> bytes:
    from affine import Affine
    from rasterio.io import MemoryFile

    data = np.full((rows, cols), value, dtype=np.int16)
    data[-1, -1] = soilgrids.NODATA
    with MemoryFile() as mem:
        with mem.open(driver="GTiff", width=cols, height=rows, count=1, dtype="int16",
                      transform=Affine(250.0, 0, x0, 0, -250.0, y1), nodata=soilgrids.NODATA) as dst:
            dst.write(data, 1)
        return mem.read()


class FakeResponse:
    def __init__(self, body: bytes):
        self.body, self.status_code, self.url = body, 200, "https://example.test/mapserv"
        self.headers = {"Content-Type": "image/tiff"}
        self.encoding, self.text = None, ""

    def iter_content(self, n):
        yield self.body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_fetch_and_area_means_then_offline(tmp_repo, monkeypatch):
    monkeypatch.delenv("AFTERCROP_OFFLINE")
    values = {"clay_mean": 700, "sand_mean": 100, "soc_mean": 200, "bdod_mean": 120, "cec_mean": 80,
              "clay_Q0.05": 600, "clay_Q0.95": 800}
    calls = []

    def get(u, params=None, **k):
        calls.append(params["COVERAGEID"])
        x0, x1 = (float(v) for v in re.fullmatch(r"x\((.+),(.+)\)", params["SUBSET"][0]).groups())
        y0, y1 = (float(v) for v in re.fullmatch(r"y\((.+),(.+)\)", params["SUBSET"][1]).groups())
        gx0, gy1 = math.floor(x0 / 250) * 250, math.ceil(y1 / 250) * 250
        cols, rows = math.ceil((x1 - gx0) / 250), math.ceil((gy1 - y0) / 250)
        prop, _, stat = params["COVERAGEID"].split("_")
        return FakeResponse(wcs_tiff(gx0, gy1, cols, rows, values[f"{prop}_{stat}"]))

    monkeypatch.setattr(base.requests, "get", get)
    paths = sp.fetch_layers(FIX["field"])
    assert len(paths) == len(calls) == len(sp.coverages())
    for p in paths.values():
        assert REQUIRED <= set(base.read_meta(p)) and base.verify(p)
    lm = sp.layer_means(paths, FIX["field"])
    assert lm["clay_0-5cm_mean"]["raw"] == pytest.approx(700) and lm["clay_0-5cm_mean"]["data_share"] == 1.0
    rec = sp.build("example", lm, config=base.load_config())
    assert rec["layers"][0]["clay_pct"] == 70.0 and rec["clay_q05_q95"] == [60.0, 80.0]
    assert rec["estimated_band"]["band"] == "inside" and rec["domain"]["state"] is None
    monkeypatch.setenv("AFTERCROP_OFFLINE", "1")

    def boom(*a, **k):
        raise AssertionError("network used")

    monkeypatch.setattr(base.requests, "get", boom)
    assert sp.fetch_layers(FIX["field"]) == paths


def test_zarc_class_only_from_a_soil_report():
    """SA-D64: a declared class AD1 to AD6, never SoilGrids; texture by IN 1/2022 is not guessed."""
    assert sp.zarc_soil(65.0)["zarc_ad_class"] is None                       # SoilGrids clay never gives one
    rec = sp.zarc_soil(65.0, {"ad_class": "AD4"})
    assert (rec["zarc_ad_class"], rec["zarc_ad_status"]) == ("AD4", "declared")
    for bad, msg in (({"ad_class": "AD7"}, "AD1 to AD6"), ({"ad_class": "Tipo 3"}, "AD1 to AD6"),
                     ({"clay_pct": 65, "sand_pct": 20, "silt_pct": 15}, "not implemented"),
                     ({"source_id": sp.SOURCE_ID, "ad_class": "AD4"}, "never gives"), ({"note": "x"}, "no Zarc class")):
        with pytest.raises(sp.SoilError, match=msg):
            sp.zarc_soil(65.0, bad)
    # The class reaches the record only as a declared report value.
    rec = sp.build("example", means(), declared={"zarc_ad_class": "AD5"}, config=base.load_config())
    assert (rec["zarc_ad_class"], rec["zarc_ad_status"]) == ("AD5", "declared")
    # build() has no path from the SoilGrids means to a class, whatever the clay.
    for clay in (100.0, 400.0, 700.0):
        assert sp.zarc_soil(clay)["zarc_ad_status"] == "unknown"

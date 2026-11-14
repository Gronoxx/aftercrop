# Written with an SI tool (Claude, Anthropic) for NASA Space Apps 2026. See AI_USE.md.
"""Polygon NDVI series (plan 8.4 step 6): mask, 0.0001 scale, mean, dedupe, clean counts. Offline.

Every raster, granule id and polygon below is an example, fictitious. The synthetic
cache lives in tmp_path with the layout of pipeline.adapters.hls; data/raw/ is never read.
"""

import copy
import json

import numpy as np
import pytest
from rasterio.crs import CRS
from rasterio.warp import transform

from pipeline.adapters import base
from pipeline.bench.common import ProtocolError
from pipeline.fields import geometry, ndvi_series

WKT = CRS.from_epsg(32622).to_wkt()
T = (30.0, 0.0, 470000.0, 0.0, -30.0, -2550000.0)
GRID = geometry.make_grid(T, (20, 20), WKT)
RULE = geometry.GeometryRule(inner_buffer_m=30, min_clean_pixels=20)
FILL = -19999
FM = {"masked_bits": ["cirrus", "cloud", "adjacent", "shadow", "snow_ice", "water"], "masked_aerosol": ["high"]}
CLOUD, HIGH_AEROSOL = 1 << 1, 3 << 6


def ring_px(cols_rows):
    xs = [T[2] + c * T[0] for c, _ in cols_rows]
    ys = [T[5] + r * T[4] for _, r in cols_rows]
    lon, lat = transform(WKT, "EPSG:4326", xs, ys)
    return list(zip(lon, lat))


FIELD = {"field_id": "x1", "criterion": None, "ring": ring_px([(2, 2), (12, 2), (12, 12), (2, 12)])}  # 8 x 8 inside


def stored(ndvi):
    return np.round(np.asarray(ndvi, dtype=np.float64) / 1e-4).astype(np.int16)


# polygon_stats --------------------------------------------------------------------------

def test_stats_mask_scale_and_mean():
    mask = np.zeros((3, 4), dtype=bool)
    mask[:, :3] = True                                    # 9 possible pixels
    fm = np.zeros((3, 4), dtype=np.uint8)
    fm[0, 0] = CLOUD                                      # masked
    fm[0, 1] = HIGH_AEROSOL                               # masked
    fm[0, 2] = 255                                        # Fmask fill
    nd = stored([[0.9, 0.9, 0.9, 0.0], [0.2, 0.4, 0.6, 0.0], [0.8, 0.0, 0.0, 0.0]])
    nd[2, 1] = FILL                                       # NDVI fill
    nd[2, 2] = 15000                                      # 1.5 is not a valid NDVI
    nd[1, 3] = 9000                                       # outside the mask: ignored
    nb = stored([[0, 0, 0, 0], [0.1, 0.2, 0.3, 0], [0.4, 0, 0, 0]])
    nb[1, 0] = FILL
    st = ndvi_series.polygon_stats(fm, nd, mask, fill=FILL, nbr2=nb, **FM)
    assert (st["n_possible"], st["n_clean"]) == (9, 4)    # (1,0) (1,1) (1,2) (2,0)
    assert st["clean_frac"] == pytest.approx(4 / 9)
    assert st["ndvi"] == pytest.approx((0.2 + 0.4 + 0.6 + 0.8) / 4)
    assert st["nbr2"] == pytest.approx((0.2 + 0.3 + 0.4) / 3)  # NBR2 fill left out


def test_stats_all_cloudy_and_bad_inputs():
    mask = np.ones((2, 2), dtype=bool)
    st = ndvi_series.polygon_stats(np.full((2, 2), CLOUD, np.uint8), stored(np.full((2, 2), 0.5)), mask,
                                   fill=FILL, **FM)
    assert (st["n_clean"], st["ndvi"], st["clean_frac"]) == (0, None, 0.0)
    with pytest.raises(ValueError, match="scale_factor"):
        ndvi_series.polygon_stats(np.zeros((2, 2), np.uint8), stored(np.full((2, 2), 0.5)), mask, fill=FILL,
                                  ndvi_scale_tag="0.001", **FM)
    with pytest.raises(ValueError, match="shapes"):
        ndvi_series.polygon_stats(np.zeros((2, 3), np.uint8), stored(np.full((2, 2), 0.5)), mask, fill=FILL, **FM)
    with pytest.raises(ValueError, match="empty"):
        ndvi_series.polygon_stats(np.zeros((2, 2), np.uint8), stored(np.full((2, 2), 0.5)), ~mask, fill=FILL, **FM)


def test_dedupe_one_row_per_sensor_and_date():
    rows = [{"sensor": "L30", "date": "2026-01-05", "granule_ur": "g1", "n_clean": 50},
            {"sensor": "L30", "date": "2026-01-05", "granule_ur": "g2", "n_clean": 50},
            {"sensor": "L30", "date": "2026-01-05", "granule_ur": "g0", "n_clean": 70},
            {"sensor": "S30", "date": "2026-01-05", "granule_ur": "g3", "n_clean": 10},
            {"sensor": "L30", "date": "2026-01-01", "granule_ur": "g4", "n_clean": 0}]
    out = ndvi_series.dedupe(rows)
    assert [(r["date"], r["sensor"], r["granule_ur"], r["n_duplicates"]) for r in out] == [
        ("2026-01-01", "L30", "g4", 0), ("2026-01-05", "L30", "g0", 2), ("2026-01-05", "S30", "g3", 0)]
    tie = ndvi_series.dedupe(rows[:2])
    assert tie[0]["granule_ur"] == "g2"  # same count: larger granule id, as hls.dedupe


# Reading a synthetic cache ---------------------------------------------------------------

def write_band(root, name, data, transform_=T, scale="0.0001"):
    path = root / "data" / "raw" / "hls" / "w_example" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(path, data=data, transform=np.asarray(transform_, dtype=np.float64),
                        crs_wkt=np.asarray(WKT), window_px=np.asarray([0, 0, *data.shape], dtype=np.int64))
    tags = {"scale_factor": scale} if scale else {}
    meta = {"url": f"https://example.test/{name}", "nodata": None, "tags": tags, "crs_epsg": 32622}
    base.meta_path(path).write_text(json.dumps(meta))
    return str(path.relative_to(root))


def granule(root, ur, d, sensor, ndvi_value, cloud_rows=0, **extra):
    fm = np.zeros((20, 20), dtype=np.uint8)
    fm[:cloud_rows] = CLOUD
    files = {"Fmask": write_band(root, f"{ur}.Fmask.npz", fm),
             "NDVI": write_band(root, f"{ur}.NDVI.npz", stored(np.full((20, 20), ndvi_value))),
             "NBR2": write_band(root, f"{ur}.NBR2.npz", stored(np.full((20, 20), 0.1)), scale=None)}
    return {"granule_ur": ur, "date": d, "sensor": sensor, "files": files, **extra}


def test_series_from_a_synthetic_cache(tmp_path):
    recs = [granule(tmp_path, "EX.S30.a", "2026-01-10", "S30", 0.8),
            granule(tmp_path, "EX.S30.b", "2026-01-10", "S30", 0.7, cloud_rows=6),   # duplicate, cloudier
            granule(tmp_path, "EX.L30.c", "2026-01-12", "L30", 0.3, cloud_rows=6),   # rows 3..5 of the field masked
            {"granule_ur": "EX.L30.d", "date": "2026-01-20", "sensor": "L30", "error": "HTTPError: 503"},
            {**granule(tmp_path, "EX.S30.e", "2026-01-25", "S30", 0.9), "vi_skipped": "window closed"}]
    rows, skipped = ndvi_series.series_from_records(recs, geometry.FieldMasks(FIELD, RULE), fill=FILL,
                                                    root=tmp_path, **FM)
    whole = rows["whole"]
    assert [(r["date"], r["sensor"], r["granule_ur"]) for r in whole] == [
        ("2026-01-10", "S30", "EX.S30.a"), ("2026-01-12", "L30", "EX.L30.c"), ("2026-01-25", "S30", "EX.S30.e")]
    a, c, e = whole
    assert (a["n_possible"], a["n_clean"], a["n_duplicates"]) == (64, 64, 1)
    assert a["ndvi"] == pytest.approx(0.8) and a["nbr2"] == pytest.approx(0.1)
    assert (c["n_clean"], c["clean_frac"]) == (40, pytest.approx(40 / 64))  # field rows 3..10, rows 3..5 cloudy
    assert (e["n_clean"], e["ndvi"], e["note"]) == (0, None, "vi_skipped")
    assert [s["granule_ur"] for s in skipped] == ["EX.L30.d"]

    path = ndvi_series.write_csv(tmp_path / "out" / "field_x1_ndvi.csv", whole)
    back = ndvi_series.read_csv(path)
    assert [r["granule_ur"] for r in back] == [r["granule_ur"] for r in whole]
    assert back[0]["ndvi"] == pytest.approx(0.8, abs=1e-6) and back[2]["ndvi"] is None


def test_series_with_halves_and_grid_checks(tmp_path):
    recs = [granule(tmp_path, "EX.S30.a", "2026-01-10", "S30", 0.8)]
    rows, _ = ndvi_series.series_from_records(recs, geometry.FieldMasks(FIELD, RULE, with_halves=True),
                                              fill=FILL, root=tmp_path, **FM)
    assert {k: v[0]["n_possible"] for k, v in rows.items()} == {"whole": 64, "half_a": 32, "half_b": 32}

    bad = granule(tmp_path, "EX.S30.z", "2026-01-11", "S30", 0.8)
    bad["files"]["NDVI"] = write_band(tmp_path, "EX.S30.z.NDVI.npz", stored(np.full((20, 20), 0.8)),
                                      transform_=(30.0, 0.0, 470030.0, 0.0, -30.0, -2550000.0))
    with pytest.raises(ValueError, match="grid differs"):
        ndvi_series.series_from_records([bad], geometry.FieldMasks(FIELD, RULE), fill=FILL, root=tmp_path, **FM)
    outside = granule(tmp_path, "EX.S30.y", "2026-01-12", "S30", 0.8)
    outside["files"]["Fmask"] = "../../etc/passwd"
    with pytest.raises(ValueError, match="outside"):
        ndvi_series.series_from_records([outside], geometry.FieldMasks(FIELD, RULE), fill=FILL, root=tmp_path, **FM)


def test_csv_refuses_other_columns(tmp_path):
    p = tmp_path / "x.csv"
    p.write_text("date,ndvi\n2026-01-01,0.5\n")
    with pytest.raises(ValueError, match="columns"):
        ndvi_series.read_csv(p)
    with pytest.raises(ValueError, match="data/raw"):
        ndvi_series.write_csv(base.RAW_DIR / "hls" / "x.csv", [])


# D10 guard --------------------------------------------------------------------------------

def test_cli_refuses_before_reading_while_d10_is_open(monkeypatch, tmp_path):
    cfg = copy.deepcopy(base.load_config())
    cfg["protocol"]["field"].update(ndvi_green=None, min_clean_frac=None)
    monkeypatch.setattr(base, "load_config", lambda *a, **k: cfg)

    def no_read(*a, **k):
        raise AssertionError("a series was read before the protocol locked D10")

    monkeypatch.setattr(ndvi_series, "index_records", no_read)
    monkeypatch.setattr(ndvi_series, "series_from_records", no_read)
    with pytest.raises(ProtocolError, match="ndvi_green is null"):
        ndvi_series.main([str(tmp_path / "fields.geojson")])

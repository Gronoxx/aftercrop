# Written with an SI tool (Claude, Anthropic) for NASA Space Apps 2026. See AI_USE.md.
"""HLS adapter: CMR parsing, Fmask decoding, window maths, cache rules. Offline.

Every granule id, url, response and pixel below is an example, fictitious.
"""

import json

import numpy as np
import pytest

from pipeline.adapters import base, hls

REQUIRED = {"source_id", "url", "params", "retrieved_at", "sha256", "bytes", "http_status"}
TILE_T = (30.0, 0.0, 399960.0, 0.0, -30.0, -2499960.0)  # example grid, north-up, negative northings


# Example, fictitious fixtures --------------------------------------------------

def url(ur, band, coll="HLSL30_VI"):
    return f"https://example.test/{coll}.020/{ur}/{ur}.{band}.tif"


def umm_item(ur, begin, bands=("Fmask", "NDVI", "NBR2"), cloud="12", coll="HLSL30_VI"):
    return {
        "meta": {"concept-id": f"G-EXAMPLE-{ur[-24:-5]}", "revision-id": 1},
        "umm": {
            "GranuleUR": ur,
            "TemporalExtent": {"RangeDateTime": {"BeginningDateTime": begin}},
            "AdditionalAttributes": [{"Name": "CLOUD_COVERAGE", "Values": [cloud]},
                                     {"Name": "SPATIAL_COVERAGE", "Values": ["100"]}],
            "RelatedUrls": [{"Type": "GET DATA", "URL": url(ur, b, coll)} for b in bands]
            + [{"Type": "GET DATA VIA DIRECT ACCESS", "URL": f"s3://example/{ur}.NDVI.tif"},
               {"Type": "VIEW RELATED INFORMATION", "URL": f"https://example.test/{ur}_stac.json"}],
        },
    }


L30_A = "HLS-VI.L30.T22KDV.2026005T132314.v2.0"
L30_B = "HLS-VI.L30.T22KDV.2026005T132338.v2.0"   # same date, second scene
L30_OTHER_TILE = "HLS-VI.L30.T22KEV.2026005T132314.v2.0"
S30_A = "HLS-VI.S30.T22KDV.2026010T133241.v2.0"

DOCS = {
    "HLSL30_VI": {"hits": 3, "items": [umm_item(L30_A, "2026-01-05T13:23:14.522Z"),
                                       umm_item(L30_B, "2026-01-05T13:23:38.100Z"),
                                       umm_item(L30_OTHER_TILE, "2026-01-05T13:23:14.522Z")]},
    "HLSS30_VI": {"hits": 1, "items": [umm_item(S30_A, "2026-01-10T13:39:08.096Z", coll="HLSS30_VI")]},
}


class FakeResponse:
    def __init__(self, body: bytes, status=200):
        self.body, self.status_code = body, status
        self.headers = {"Content-Type": "application/json"}
        self.url, self.encoding, self.text = "https://example.test/cmr", "utf-8", body.decode()

    def iter_content(self, n):
        yield self.body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.fixture
def tmp_repo(tmp_path, monkeypatch):
    monkeypatch.setattr(base, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(base, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(base, "RAW_DIR", tmp_path / "data" / "raw")
    monkeypatch.setattr(base, "INTERIM_DIR", tmp_path / "data" / "interim")
    return tmp_path


@pytest.fixture
def spec():
    return hls.specs(start="2025-10-01", end="2026-03-31")[0]


def fake_cmr(monkeypatch, docs=DOCS):
    calls = []

    def get(u, params=None, **k):
        calls.append(params["short_name"])
        return FakeResponse(json.dumps(docs[params["short_name"]]).encode())

    monkeypatch.setattr(base.requests, "get", get)
    return calls


def fake_remote(monkeypatch, fmask_value=64, shape=(4, 5)):
    """Remote window reader that returns example arrays; counts calls."""
    calls = []

    def read_remote(u, bbox, pad_px):
        calls.append(u)
        band = u.rsplit(".", 2)[-2]
        if band == "Fmask":
            data = np.full(shape, fmask_value, dtype=np.uint8)
        else:
            data = np.arange(np.prod(shape), dtype=np.int16).reshape(shape) * 100
        info = {"crs_wkt": "EXAMPLE_WKT", "crs_epsg": 32622, "window_px": [10, 20, *shape],
                "transform": list(hls.window_transform(TILE_T, 10, 20)), "nodata": -19999,
                "tags": {"scale_factor": "0.0001"}, "dtype": str(data.dtype)}
        return data, info

    monkeypatch.setattr(hls, "_read_remote_window", read_remote)
    return calls


def no_network(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("network used")

    monkeypatch.setattr(base.requests, "get", boom)
    monkeypatch.setattr(hls, "_read_remote_window", boom)


# CMR ---------------------------------------------------------------------------

def test_parse_cmr_reads_ids_dates_and_https_urls():
    recs = hls.parse_cmr(DOCS["HLSL30_VI"], "L30", "HLSL30_VI")
    r = recs[0]
    assert r["granule_ur"] == L30_A and r["tile"] == "T22KDV" and r["sensor"] == "L30"
    assert r["date"] == "2026-01-05" and r["season"] == "2025/26" and r["version"] == "2.0"
    assert r["cloud_cover_pct"] == 12.0 and r["spatial_coverage_pct"] == 100.0
    assert sorted(r["urls"]) == ["Fmask", "NBR2", "NDVI"]
    assert all(u.startswith("https://") for u in r["urls"].values())


def test_parse_cmr_rejects_bad_ids():
    doc = {"items": [umm_item("HLS-VI.L30.T22KDV.2026005T132314.v2.0", "2026-01-06T13:23:14Z")]}
    with pytest.raises(ValueError, match="disagree"):
        hls.parse_cmr(doc, "L30", "HLSL30_VI")
    with pytest.raises(ValueError, match="sensor"):
        hls.parse_cmr(DOCS["HLSL30_VI"], "S30", "HLSS30_VI")
    with pytest.raises(ValueError, match="unexpected"):
        hls.parse_cmr({"items": [umm_item("NOT-HLS.x", "2026-01-05T00:00:00Z")]}, "L30", "x")


def test_cmr_request_is_stable_and_named(spec):
    u, params, name = hls.cmr_request(spec, spec["collections"][0])
    assert params["bounding_box"] == ",".join(str(v) for v in spec["bbox"])
    assert params["temporal"] == "2025-10-01T00:00:00Z,2026-03-31T23:59:59Z"
    assert "attribute[]" not in params  # VI: no tile cloud filter, Fmask decides per pixel
    assert name.startswith("cmr_HLSL30_VI_2025-10-01_2026-03-31_") and name.endswith(".json")
    sr = hls.sr_spec(base.load_config(), "2026-06-01", "2026-08-31", 20)
    assert hls.cmr_request(sr, sr["collections"][0])[1]["attribute[]"] == "int,CLOUD_COVERAGE,0,20"


def test_search_caches_filters_tile_and_sorts_newest_first(tmp_repo, spec, monkeypatch):
    monkeypatch.delenv("AFTERCROP_OFFLINE")
    calls = fake_cmr(monkeypatch)
    recs = hls.search(spec)
    assert [r["granule_ur"] for r in recs] == [S30_A, L30_B, L30_A]  # other tile dropped
    assert sorted(calls) == ["HLSL30_VI", "HLSS30_VI"]
    for p in [q for q in (base.RAW_DIR / "hls").glob("cmr_*.json") if not q.name.endswith(".meta.json")]:
        assert REQUIRED <= set(base.read_meta(p)) and base.verify(p)
    monkeypatch.setenv("AFTERCROP_OFFLINE", "1")
    no_network(monkeypatch)
    assert hls.search(spec) == recs


def test_search_offline_miss_is_an_error(tmp_repo, spec, monkeypatch):
    no_network(monkeypatch)
    with pytest.raises(base.OfflineCacheMiss):
        hls.search(spec)


def test_search_refuses_a_truncated_answer(tmp_repo, spec, monkeypatch):
    monkeypatch.delenv("AFTERCROP_OFFLINE")
    fake_cmr(monkeypatch, {**DOCS, "HLSS30_VI": {**DOCS["HLSS30_VI"], "hits": 5}})
    with pytest.raises(RuntimeError, match="hits"):
        hls.search(spec)


# Fmask -------------------------------------------------------------------------

def test_fmask_bits_decode():
    fm = np.array([64, 1, 2, 4, 8, 16, 32, 192, 128, 255, 0], dtype=np.uint8)
    d = hls.decode_fmask(fm)
    assert d["cirrus"].tolist() == [0, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0]
    assert d["cloud"].tolist() == [0, 0, 1, 0, 0, 0, 0, 0, 0, 0, 0]
    assert d["adjacent"].tolist() == [0, 0, 0, 1, 0, 0, 0, 0, 0, 0, 0]
    assert d["shadow"].tolist() == [0, 0, 0, 0, 1, 0, 0, 0, 0, 0, 0]
    assert d["snow_ice"].tolist() == [0, 0, 0, 0, 0, 1, 0, 0, 0, 0, 0]
    assert d["water"].tolist() == [0, 0, 0, 0, 0, 0, 1, 0, 0, 0, 0]
    assert d["aerosol"].tolist() == [1, 0, 0, 0, 0, 0, 0, 3, 2, -1, 0]
    assert d["fill"].tolist() == [0] * 9 + [1, 0]
    assert not d["cloud"][9]  # fill is never read as a flag


def test_clear_and_open_masks():
    fm = np.array([64, 1, 2, 4, 8, 16, 32, 192, 128, 255, 0], dtype=np.uint8)
    assert hls.clear_mask(fm).tolist() == [1, 0, 0, 0, 0, 0, 0, 0, 1, 0, 1]
    assert hls.clear_mask(fm, ("cloud", "shadow"), ()).tolist() == [1, 1, 0, 1, 0, 1, 1, 1, 1, 0, 1]
    assert hls.open_mask(fm).tolist() == hls.clear_mask(fm, ("cloud", "shadow"), ()).tolist()
    with pytest.raises(KeyError):
        hls.clear_mask(fm, ("haze",))


# Window maths -----------------------------------------------------------------------

def test_pixel_window_takes_every_touched_pixel_plus_pad():
    # x 400005..400095 touches cols 1..4; y -2500010..-2500080 touches rows 1..3
    b = (400005, -2500080, 400095, -2500010)
    assert hls.pixel_window(b, TILE_T, (3660, 3660)) == (1, 1, 3, 4)
    assert hls.pixel_window(b, TILE_T, (3660, 3660), pad_px=1) == (0, 0, 5, 6)
    with pytest.raises(ValueError, match="outside"):  # a pad of 2 would need row -1
        hls.pixel_window(b, TILE_T, (3660, 3660), pad_px=2)


def test_pixel_window_exact_edges_add_no_pixel():
    t = TILE_T
    left, top = t[2] + 30 * 100, t[5] - 30 * 200
    b = (left, top - 30 * 10, left + 30 * 20, top)
    assert hls.pixel_window(b, t, (3660, 3660)) == (200, 100, 10, 20)
    assert hls.pixel_window(b, t, (3660, 3660), pad_px=2) == (198, 98, 14, 24)


def test_pixel_window_rejects_bad_grids_and_bounds():
    with pytest.raises(ValueError, match="north-up"):
        hls.pixel_window((0, 0, 1, 1), (30, 0, 0, 0, 30, 0), (10, 10))
    with pytest.raises(ValueError, match="empty"):
        hls.pixel_window((1, 0, 1, 1), TILE_T, (10, 10))
    with pytest.raises(ValueError, match="outside"):
        hls.pixel_window((399960, -2500260, 400260, -2499960), TILE_T, (5, 5))


def test_window_transform_moves_the_origin():
    assert hls.window_transform(TILE_T, 200, 100) == (30.0, 0.0, 402960.0, 0.0, -30.0, -2505960.0)


def test_project_bbox_follows_the_declared_crs():
    bbox = [-51.25, -23.35, -51.15, -23.25]
    north = hls.project_bbox(bbox, "EPSG:32622")   # how the files label southern tiles
    south = hls.project_bbox(bbox, "EPSG:32722")
    assert -2.59e6 < north[1] < north[3] < -2.56e6
    assert 7.41e6 < south[1] < south[3] < 7.44e6
    assert north[0] == pytest.approx(south[0]) and north[2] == pytest.approx(south[2])


def test_real_reader_on_a_local_example_geotiff(tmp_repo):
    """The GDAL reader on a local file: same pixels as the window maths say."""
    import rasterio
    from rasterio.transform import Affine

    t = Affine(30.0, 0.0, 470010.0, 0.0, -30.0, -2565030.0)
    rows, cols = np.mgrid[0:700, 0:600]
    data = ((rows % 100) * 100 + cols % 100).astype(np.int16)
    path = tmp_repo / "example_fictitious.tif"
    with rasterio.open(path, "w", driver="GTiff", width=600, height=700, count=1, dtype="int16",
                       crs="EPSG:32622", transform=t, nodata=-19999, tiled=True,
                       blockxsize=256, blockysize=256, compress="deflate") as dst:
        dst.write(data, 1)
        dst.update_tags(scale_factor="0.0001")
    bbox = [-51.25, -23.35, -51.15, -23.25]
    out, info = hls._read_remote_window(str(path), bbox, 2)
    r0, c0, nr, nc = info["window_px"]
    assert (nr, nc) == out.shape and 360 < nr < 380 and 335 < nc < 355
    assert np.array_equal(out, data[r0:r0 + nr, c0:c0 + nc])
    assert info["crs_epsg"] == 32622 and info["tags"]["scale_factor"] == "0.0001"
    assert info["transform"] == list(hls.window_transform(tuple(t)[:6], r0, c0))


# Scale ----------------------------------------------------------------------------

def test_scale_is_fixed_and_fill_is_nan():
    v = hls.scaled(np.array([5000, -19999, 10000, -2000], dtype=np.int16), -19999)
    assert v[0] == pytest.approx(0.5) and np.isnan(v[1]) and v[2] == pytest.approx(1.0)
    assert np.isnan(hls.scaled(np.array([12000], dtype=np.int16), -19999, valid=(-1, 1))[0])
    hls.scaled(np.array([1], dtype=np.int16), -19999, scale_tag="0.0001")
    hls.scaled(np.array([1], dtype=np.int16), -19999, scale_tag=None)  # L30 granules without scale_factor
    with pytest.raises(ValueError, match="product changed"):
        hls.scaled(np.array([1], dtype=np.int16), -19999, scale_tag="0.001")


# Cache ----------------------------------------------------------------------------

def test_cached_window_offline_miss_never_reads(tmp_repo, spec, monkeypatch):
    no_network(monkeypatch)
    with pytest.raises(base.OfflineCacheMiss):
        hls.cached_window(spec, url(L30_A, "NDVI"))
    assert not (base.RAW_DIR / "hls").exists()


def test_cached_window_writes_once_with_meta(tmp_repo, spec, monkeypatch):
    monkeypatch.delenv("AFTERCROP_OFFLINE")
    calls = fake_remote(monkeypatch)
    n0 = base.network_calls
    p = hls.cached_window(spec, url(L30_A, "NDVI"))
    assert p.parent.name == hls.window_key(spec) and p.name == f"{L30_A}.NDVI.npz"
    meta = base.read_meta(p)
    assert REQUIRED <= set(meta) and meta["bytes"] == p.stat().st_size and base.verify(p)
    assert meta["params"]["bbox"] == spec["bbox"] and base.network_calls == n0 + 1
    got = hls.read(p)
    assert got["data"].dtype == np.int16 and got["data"].shape == (4, 5)
    assert got["transform"] == hls.window_transform(TILE_T, 10, 20) and got["crs_wkt"] == "EXAMPLE_WKT"
    assert hls.cached_window(spec, url(L30_A, "NDVI")) == p and len(calls) == 1
    monkeypatch.setenv("AFTERCROP_OFFLINE", "1")
    assert hls.cached_window(spec, url(L30_A, "NDVI")) == p
    assert not list(p.parent.glob("*.part"))


def test_incomplete_window_entry_is_never_trusted(tmp_repo, spec, monkeypatch):
    monkeypatch.delenv("AFTERCROP_OFFLINE")
    fake_remote(monkeypatch)
    p = hls.band_path(spec, url(L30_A, "NDVI"))
    p.parent.mkdir(parents=True)
    p.write_bytes(b"half")
    with pytest.raises(RuntimeError, match="incomplete"):
        hls.cached_window(spec, url(L30_A, "NDVI"))


def test_window_key_changes_with_the_window(spec):
    assert hls.window_key(spec) != hls.window_key({**spec, "bbox": [-51.3, -23.35, -51.2, -23.25]})
    assert hls.window_key(spec) != hls.window_key({**spec, "pad_px": 0})
    with pytest.raises(ValueError):
        hls.band_path(spec, "https://example.test/a/b.jp2")


# Granules, duplicates, seasons --------------------------------------------------------

def granule(ur, d="2026-01-05", sensor="L30"):
    return {"granule_ur": ur, "sensor": sensor, "date": d, "datetime": f"{d}T13:23:14Z", "tile": "T22KDV",
            "urls": {b: url(ur, b) for b in ("Fmask", "NDVI", "NBR2")}}


def test_fetch_granule_skips_vi_when_the_window_is_closed(tmp_repo, spec, monkeypatch):
    monkeypatch.delenv("AFTERCROP_OFFLINE")
    calls = fake_remote(monkeypatch, fmask_value=66)  # low aerosol + cloud everywhere
    rec = hls.fetch_granule(spec, granule(L30_A))
    assert rec["n_open"] == 0 and rec["n_clear"] == 0 and "vi_skipped" in rec
    assert list(rec["files"]) == ["Fmask"] and len(calls) == 1


def test_fetch_granule_reads_vi_when_open(tmp_repo, spec, monkeypatch):
    monkeypatch.delenv("AFTERCROP_OFFLINE")
    spec = {**spec, "skip_vi_below": 20}
    calls = fake_remote(monkeypatch, fmask_value=68, shape=(5, 5))  # adjacent only: open, not clear
    rec = hls.fetch_granule(spec, granule(L30_A))
    assert rec["n_open"] == 25 and rec["n_clear"] == 0 and "vi_skipped" not in rec
    assert sorted(rec["files"]) == ["Fmask", "NBR2", "NDVI"] and len(calls) == 3
    assert rec["downloaded"] == ["Fmask", "NDVI", "NBR2"]


def test_dedupe_keeps_the_clearest_per_sensor_and_date():
    recs = [{**granule(L30_A), "n_clear": 10}, {**granule(L30_B), "n_clear": 30},
            {**granule(S30_A, "2026-01-10", "S30"), "n_clear": 5},
            {**granule(S30_A.replace("2026010", "2026005"), "2026-01-05", "S30"), "n_clear": 1}]
    out = {r["granule_ur"]: r["duplicate_of"] for r in hls.dedupe(recs)}
    assert out[L30_A] == L30_B and out[L30_B] is None
    assert out[S30_A] is None and list(out.values()).count(None) == 3  # S30 on 01-05 is not an L30 duplicate


def test_window_summary_counts_and_gaps():
    def rec(d, n, frac, **k):
        return {"granule_ur": d, "sensor": "S30", "date": d, "n_clear": n, "clear_frac": frac, **k}

    recs = [rec("2026-01-01", 100, 0.9), rec("2026-01-11", 10, 0.0), rec("2026-01-21", 50, 0.2),
            rec("2026-02-20", 100, 0.8), rec("2026-02-25", 0, 0.0, vi_skipped="closed"),
            rec("2026-03-01", 0, 0, error="x")]
    s = hls.window_summary(recs, 20)
    assert s["granules"] == 6 and s["failed"] == 1 and s["vi_skipped"] == 1
    assert s["dates_with_min_clear_px"] == 3 and s["longest_gap_days_min_clear_px"] == 30
    assert s["dates_half_window_clear"] == 2 and s["longest_gap_days_half_window_clear"] == 50


def test_specs_follow_the_protocol_seasons_newest_first():
    config = base.load_config()
    ss = hls.specs(config)
    assert [s["label"] for s in ss][:2] == ["2025-26", "2024-25"] and ss[-1]["label"] == "2018-19"
    assert len(ss) == 8 and ss[0]["start"] == "2025-09-01" and ss[0]["end"] == "2026-08-31"
    assert ss[0]["skip_vi_below"] == config["protocol"]["field"]["min_clean_pixels"]
    assert "hls" in config["sources"] and "hls" not in config["protocol"]
    with pytest.raises(ValueError, match="outside"):
        hls.specs(config, seasons=["2014/15"])
    with pytest.raises(ValueError):
        hls.specs(config, start="2025-10-01")


def test_fetch_end_to_end_then_offline_with_zero_network(tmp_repo, spec, monkeypatch):
    monkeypatch.delenv("AFTERCROP_OFFLINE")
    fake_cmr(monkeypatch)
    fake_remote(monkeypatch, shape=(6, 6))
    idx = json.loads(hls.fetch(spec, workers=2).read_text())
    assert idx["summary"]["granules"] == 3 and idx["summary"]["duplicates"] == 1
    assert [g["granule_ur"] for g in idx["granules"]] == [S30_A, L30_B, L30_A]

    monkeypatch.setenv("AFTERCROP_OFFLINE", "1")
    no_network(monkeypatch)
    n0 = base.network_calls
    again = json.loads(hls.fetch(spec).read_text())
    assert base.network_calls == n0
    assert all(not g["downloaded"] for g in again["granules"])
    prov = hls.provenance(spec)
    assert prov["n_files"] == 9 and len(prov["cmr"]) == 2 and prov["version"] == "HLSL30_VI 2.0, HLSS30_VI 2.0"
    for f in prov["files"]:
        assert base.verify(base.REPO_ROOT / f["path"])

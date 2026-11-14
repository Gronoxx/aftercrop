# Written with an SI tool (Claude, Anthropic) for NASA Space Apps 2026. See AI_USE.md.
"""Basemap: scene choice, median of clear pixels, stretch, PNG bytes, georeference. Offline.

Every granule id, url, response and pixel below is an example, fictitious.
"""

import json
import struct
import zlib

import numpy as np
import pytest

from pipeline.adapters import base, hls
from pipeline.fields import basemap

NAN = np.nan


# Example, fictitious records -----------------------------------------------------

def rec(ur, d, frac, **k):
    return {"granule_ur": ur, "date": d, "clear_frac": frac, "sensor": "S30", **k}


def test_select_scenes_one_per_date_clearest_first():
    recs = [rec("a", "2026-07-01", 0.99), rec("b", "2026-07-01", 0.97), rec("c", "2026-07-11", 0.96),
            rec("d", "2026-07-21", 0.50), rec("e", "2026-07-31", 1.00), rec("f", "2026-08-10", 1.0, error="x")]
    got = basemap.select_scenes(recs, 0.95, 2)
    assert [r["granule_ur"] for r in got] == ["a", "e"]  # by date, after ranking by clear fraction
    assert [r["granule_ur"] for r in basemap.select_scenes(recs, 0.95, 9)] == ["a", "c", "e"]
    assert basemap.select_scenes(recs, 1.01, 9) == []


def test_composite_is_the_median_of_clear_pixels_only():
    # 3 scenes, 3 bands, 1x3 pixels
    b = np.zeros((3, 3, 1, 3), dtype=np.float32)
    b[:, :, 0, 0] = np.array([0.1, 0.2, 0.9])[:, None]   # pixel 0: all clear -> 0.2
    b[:, :, 0, 1] = np.array([0.1, 0.3, 0.9])[:, None]   # pixel 1: scene 2 cloudy -> median(0.1, 0.3)
    b[:, :, 0, 2] = 0.5                                  # pixel 2: never clear
    b[1, 0, 0, 0] = NAN                                  # scene 1 red is fill at pixel 0: obs dropped
    clear = np.array([[[1, 1, 0]], [[1, 1, 0]], [[1, 0, 0]]], dtype=bool)
    med, count = basemap.composite(b, clear)
    assert med[:, 0, 0] == pytest.approx([0.5, 0.5, 0.5])  # median of scenes 0 and 2
    assert med[:, 0, 1] == pytest.approx([0.2, 0.2, 0.2])
    assert np.isnan(med[:, 0, 2]).all()
    assert count.tolist() == [[2, 2, 0]]
    with pytest.raises(ValueError):
        basemap.composite(b[:, :2], clear)


def test_stretch_is_common_to_the_bands_and_keeps_gaps_transparent():
    rgb = np.array([[[0.0, 0.1, NAN]], [[0.05, 0.2, NAN]], [[0.0, 0.2, NAN]]])  # (3, 1, 3)
    out = basemap.stretch(rgb, 0, 100)
    assert out.shape == (1, 3, 4) and out.dtype == np.uint8
    assert out[0, 0].tolist() == [0, 64, 0, 255]  # 0.05 of 0..0.2 -> 63.75 -> 64
    assert out[0, 1].tolist() == [128, 255, 255, 255]
    assert out[0, 2].tolist() == [0, 0, 0, 0]
    assert basemap.stretch(rgb, 0, 100, gamma=2.0)[0, 0, 1] == 128  # sqrt(0.25) = 0.5
    with pytest.raises(ValueError, match="flat"):
        basemap.stretch(np.full((3, 2, 2), 0.1), 0, 100)
    with pytest.raises(ValueError, match="no pixel"):
        basemap.stretch(np.full((3, 2, 2), NAN))


def decode_png(data: bytes):
    """Minimal reader for the PNGs written here (8 bit, filter 0)."""
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    pos, chunks = 8, []
    while pos < len(data):
        n = struct.unpack(">I", data[pos:pos + 4])[0]
        kind, body = data[pos + 4:pos + 8], data[pos + 8:pos + 8 + n]
        assert struct.unpack(">I", data[pos + 8 + n:pos + 12 + n])[0] == zlib.crc32(kind + body)
        chunks.append((kind, body))
        pos += 12 + n
    assert [k for k, _ in chunks] == [b"IHDR", b"IDAT", b"IEND"]
    w, h, depth, ctype = struct.unpack(">IIBB", chunks[0][1][:10])
    ch = {6: 4, 2: 3}[ctype]
    raw = zlib.decompress(chunks[1][1])
    rows = [raw[r * (w * ch + 1):(r + 1) * (w * ch + 1)] for r in range(h)]
    assert all(r[0] == 0 for r in rows) and depth == 8
    return np.frombuffer(b"".join(r[1:] for r in rows), dtype=np.uint8).reshape(h, w, ch)


def test_png_round_trip_rgba_and_rgb(tmp_path):
    img = np.random.default_rng(1).integers(0, 256, (7, 5, 4), dtype=np.uint8)
    assert np.array_equal(decode_png(basemap.png_bytes(img)), img)
    assert np.array_equal(decode_png(basemap.png_bytes(img[..., :3])), img[..., :3])
    p = basemap.write_png(tmp_path / "x.png", img)
    assert np.array_equal(decode_png(p.read_bytes()), img)
    with pytest.raises(ValueError):
        basemap.png_bytes(img[..., 0])


def test_png_never_goes_to_raw(tmp_path, monkeypatch):
    monkeypatch.setattr(base, "RAW_DIR", tmp_path / "raw")
    with pytest.raises(ValueError):
        basemap.write_png(tmp_path / "raw" / "hls" / "x.png", np.zeros((1, 1, 4), dtype=np.uint8))


def test_georef_bounds_and_pixel_size():
    from rasterio.crs import CRS

    wkt = CRS.from_epsg(32622).to_wkt()
    g = basemap.georef((30.0, 0.0, 474390.0, 0.0, -30.0, -2571150.0), wkt, 32622, (373, 345))
    assert g["width"] == 345 and g["height"] == 373 and g["pixel_size_m"] == [30.0, 30.0]
    assert g["bounds"] == {"left": 474390.0, "bottom": -2571150.0 - 373 * 30, "right": 474390.0 + 345 * 30,
                           "top": -2571150.0}
    ll = g["bounds_lonlat_approx"]
    assert -51.26 < ll["lon_min"] < -51.24 and -51.15 < ll["lon_max"] < -51.13
    assert -23.36 < ll["lat_min"] < -23.34 and -23.25 < ll["lat_max"] < -23.23
    with pytest.raises(ValueError):
        basemap.georef((30.0, 1.0, 0.0, 0.0, -30.0, 0.0), wkt, 32622, (2, 2))


# End to end with example, fictitious scenes ------------------------------------------

SCENES = {  # granule id -> Fmask pattern on a 5x5 window
    "HLS.S30.T22KDV.2026190T133241.v2.0": "clear",
    "HLS.L30.T22KDV.2026200T132314.v2.0": "one_cloud",
    "HLS.S30.T22KDV.2026210T133241.v2.0": "half",
}


def sr_doc(coll, sensor):
    items = []
    for ur in SCENES:
        if f".{sensor}." not in ur:
            continue
        doy = ur.split(".")[3][:7]
        d = np.datetime64(f"{doy[:4]}-01-01") + np.timedelta64(int(doy[4:]) - 1, "D")
        items.append({"meta": {"concept-id": "G-EXAMPLE"}, "umm": {
            "GranuleUR": ur, "TemporalExtent": {"RangeDateTime": {"BeginningDateTime": f"{d}T13:30:00Z"}},
            "AdditionalAttributes": [{"Name": "CLOUD_COVERAGE", "Values": ["5"]}],
            "RelatedUrls": [{"Type": "GET DATA", "URL": f"https://example.test/{coll}.020/{ur}/{ur}.{b}.tif"}
                            for b in ("Fmask", "B04", "B03", "B02")]}})
    return {"hits": len(items), "items": items}


class FakeResponse:
    def __init__(self, body: bytes):
        self.body, self.status_code = body, 200
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


def fakes(monkeypatch):
    from rasterio.crs import CRS

    wkt = CRS.from_epsg(32622).to_wkt()
    t = (30.0, 0.0, 474390.0, 0.0, -30.0, -2571150.0)
    docs = {"HLSL30": sr_doc("HLSL30", "L30"), "HLSS30": sr_doc("HLSS30", "S30")}
    monkeypatch.setattr(base.requests, "get",
                        lambda u, params=None, **k: FakeResponse(json.dumps(docs[params["short_name"]]).encode()))

    def remote(u, bbox, pad_px):
        name = u.rsplit("/", 1)[-1]
        ur, band = name.rsplit(".", 2)[0], name.rsplit(".", 2)[1]
        if band == "Fmask":
            fm = np.full((5, 5), 64, dtype=np.uint8)
            if SCENES[ur] == "one_cloud":
                fm[0, 0] = 66
            elif SCENES[ur] == "half":
                fm[:, :3] = 66
            data = fm
        else:
            level = {"B04": 800, "B03": 600, "B02": 400}[band] + 100 * list(SCENES).index(ur)
            data = np.full((5, 5), level, dtype=np.int16)
            data[4, 4] = -9999 if ur.startswith("HLS.L30") else level  # fill in one scene
        return data, {"crs_wkt": wkt, "crs_epsg": 32622, "window_px": [0, 0, 5, 5], "transform": list(t),
                      "nodata": None, "tags": {"scale_factor": "0.0001"}, "dtype": str(data.dtype)}

    monkeypatch.setattr(hls, "_read_remote_window", remote)


def test_run_builds_png_and_georeference_then_works_offline(tmp_repo, monkeypatch):
    monkeypatch.delenv("AFTERCROP_OFFLINE")
    fakes(monkeypatch)
    doc = basemap.run()
    assert [s["granule_ur"] for s in doc["scenes"]] == list(SCENES)[:2]  # the half-cloudy scene is out
    assert doc["candidates"] == 3 and doc["width"] == 5 and doc["height"] == 5
    assert doc["pixels_without_clear_obs"] == 0 and doc["clear_obs_per_pixel"]["min"] == 1
    png = decode_png((base.REPO_ROOT / doc["png"]).read_bytes())
    assert png.shape == (5, 5, 4) and (png[..., 3] == 255).all()
    on_disk = json.loads((base.INTERIM_DIR / f"basemap_{base.load_config()['sources']['hls']['window']['id']}.json").read_text())
    assert on_disk["crs_epsg"] == 32622 and on_disk["method"]["period"] == ["2026-06-01", "2026-08-31"]

    monkeypatch.setenv("AFTERCROP_OFFLINE", "1")

    def boom(*a, **k):
        raise AssertionError("network used")

    monkeypatch.setattr(base.requests, "get", boom)
    monkeypatch.setattr(hls, "_read_remote_window", boom)
    n0 = base.network_calls
    assert basemap.run()["scenes"] == doc["scenes"] and base.network_calls == n0

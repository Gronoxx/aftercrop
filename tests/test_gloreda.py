# Written with an SI tool (Claude, Anthropic) for NASA Space Apps 2026. See AI_USE.md.
"""GloREDa adapter: Zenodo record, zip directory, TIFF head, budget stop, tile streaming. Offline.

The archive, record and erosivity values below are an example, fictitious: a small zip
with 12 tiled BigTIFFs built here with the layout of the real files.
"""

import io
import json
import re
import zipfile
import zlib
from pathlib import Path

import numpy as np
import pytest

from pipeline.adapters import base, gloreda

FIXTURES = Path(__file__).parent / "fixtures"
REQUIRED = {"source_id", "url", "params", "retrieved_at", "sha256", "bytes", "http_status"}
RECORD_URL = "https://zenodo.org/api/records/0000000"
ARCHIVE_URL = "https://zenodo.org/api/records/0000000/files/GloREDa.zip/content"
# Example grid: 0.05 degree pixels from (-52, -22), 64 x 48, tiles of 16 x 16.
GRID = {"west": -52.0, "north": -22.0, "step": 0.05, "width": 64, "height": 48, "tile": 16}


def month_raster(m: int) -> np.ndarray:
    r = np.arange(GRID["height"])[:, None] % 3
    data = (100 * m + r + np.zeros((1, GRID["width"]), dtype=int)).astype(np.int16)
    data[0, :5] = gloreda.NODATA
    return data


def tif_bytes(data: np.ndarray, predictor: int = 1) -> bytes:
    from affine import Affine
    from rasterio.io import MemoryFile

    with MemoryFile() as mem:
        with mem.open(driver="GTiff", width=data.shape[1], height=data.shape[0], count=1, dtype="int16",
                      crs="EPSG:4326", transform=Affine(GRID["step"], 0, GRID["west"], 0, -GRID["step"], GRID["north"]),
                      tiled=True, blockxsize=GRID["tile"], blockysize=GRID["tile"], compress="deflate",
                      predictor=predictor, BIGTIFF="YES", nodata=gloreda.NODATA) as dst:
            dst.write(data, 1)
        return mem.read()


@pytest.fixture(scope="module")
def archive() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("_Readme.txt", "example, fictitious")
        for m, name in enumerate(gloreda.MONTHS, 1):
            z.writestr(f"Rfactor_{name}.tif", tif_bytes(month_raster(m)))
    return buf.getvalue()


@pytest.fixture
def tmp_repo(tmp_path, monkeypatch):
    monkeypatch.setattr(base, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(base, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(base, "RAW_DIR", tmp_path / "data" / "raw")
    monkeypatch.setattr(base, "INTERIM_DIR", tmp_path / "data" / "interim")
    return tmp_path


@pytest.fixture
def spec():
    s = gloreda.spec()
    # window rows 20-22, cols 13-15 (tile 4); site row 24, col 16 (tile 5) on the example grid
    return {**s, "record_url": RECORD_URL, "bbox": [-51.32, -23.13, -51.22, -23.03],
            "site": {"id": "example_site", "lat": -23.2003, "lon": -51.1778}, "max_download_bytes": 10 ** 9}


class FakeResponse:
    def __init__(self, body: bytes, url: str, status: int, headers: dict, served: list):
        self.body, self.url, self.status_code, self.headers, self.served = body, url, status, headers, served
        self.encoding, self.text = None, ""

    def iter_content(self, n):
        for i in range(0, len(self.body), 4096):
            self.served.append(len(self.body[i:i + 4096]))
            yield self.body[i:i + 4096]

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def fake_zenodo(monkeypatch, archive: bytes, ignore_range=False, license_id="cc-by-4.0"):
    served, calls = [], []
    rec = json.loads((FIXTURES / "gloreda_record_example.json").read_text(encoding="utf-8"))
    rec["files"][0]["size"] = len(archive)
    rec["metadata"]["license"]["id"] = license_id

    def get(u, params=None, headers=None, **k):
        calls.append((u, (headers or {}).get("Range")))
        if u == RECORD_URL:
            return FakeResponse(json.dumps(rec).encode(), u, 200, {"Content-Type": "application/json"}, served)
        assert u == ARCHIVE_URL
        rng = (headers or {}).get("Range")
        if rng is None or ignore_range:
            return FakeResponse(archive, u, 200, {}, served)
        a, b = (int(x) for x in re.fullmatch(r"bytes=(\d+)-(\d+)", rng).groups())
        b = min(b, len(archive) - 1)
        return FakeResponse(archive[a:b + 1], u, 206, {"Content-Range": f"bytes {a}-{b}/{len(archive)}"}, served)

    monkeypatch.setattr(base.requests, "get", get)
    return calls, served


def no_network(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("network used")

    monkeypatch.setattr(base.requests, "get", boom)


# Spec and record --------------------------------------------------------------------

def test_spec_is_the_zenodo_copy_with_a_budget():
    cfg = base.load_config()
    s = gloreda.spec(cfg)
    assert s["record_url"].startswith("https://zenodo.org/api/records/8036998")
    assert s["doi"] == "10.5281/zenodo.8036998" and "esdac" not in s["record_url"].lower()
    assert s["members"][0] == "Rfactor_January.tif" and s["members"][-1] == "Rfactor_December.tif"
    assert len(s["members"]) == 12
    assert s["max_download_bytes"] == int(base.load_config()["sources"]["gloreda"]["max_download_bytes"])
    assert 0 < s["max_download_bytes"] <= 2_300_000_000  # never the whole 2.2 GB zip by accident
    assert s["bbox"] == [float(x) for x in cfg["sources"]["hls"]["window"]["bbox"]]
    assert s["site"]["id"] == "londrina"


def test_archive_info_checks_license_and_host():
    rec = json.loads((FIXTURES / "gloreda_record_example.json").read_text(encoding="utf-8"))
    info = gloreda.archive_info(rec, "GloREDa.zip")
    assert info["url"] == ARCHIVE_URL and info["license"] == "cc-by-4.0"
    with pytest.raises(ValueError, match="license"):
        gloreda.archive_info({**rec, "metadata": {"license": {"id": "other"}}}, "GloREDa.zip")
    moved = json.loads(json.dumps(rec))
    moved["files"][0]["links"]["self"] = "https://esdac.example.test/GloREDa.zip"
    with pytest.raises(ValueError, match="zenodo.org"):
        gloreda.archive_info(moved, "GloREDa.zip")
    with pytest.raises(ValueError, match="not found"):
        gloreda.archive_info(rec, "Other.zip")


# Zip and TIFF parsing ---------------------------------------------------------------

def test_directory_head_and_tiles_needed(archive, spec):
    members = gloreda.parse_directory(archive[-gloreda.TAIL_BYTES:], max(0, len(archive) - gloreda.TAIL_BYTES))
    assert set(members) == {"_Readme.txt", *spec["members"]}
    m = members["Rfactor_March.tif"]
    assert m["method"] == 8
    head = archive[m["lho"]:m["lho"] + gloreda.HEAD_BYTES]
    ds = gloreda.data_start(head, m["lho"], "Rfactor_March.tif")
    tif = gloreda._head_tiff(head, ds - m["lho"])
    gloreda.check_layout(tif)
    assert (tif["width"], tif["height"], tif["tile_w"], tif["tile_h"]) == (64, 48, 16, 16) and tif["bigtiff"]
    assert tif["transform"] == [pytest.approx(0.05), 0.0, pytest.approx(-52.0), 0.0, pytest.approx(-0.05),
                                pytest.approx(-22.0)]
    assert gloreda.bbox_rc(tif, spec["bbox"]) == (20, 13, 22, 15)
    assert gloreda.pixel_rc(tif, spec["site"]["lat"], spec["site"]["lon"]) == (24, 16)
    assert gloreda.tiles_needed(tif, spec["bbox"], (spec["site"]["lat"], spec["site"]["lon"])) == [4, 5]
    with pytest.raises(ValueError, match="local header names another"):
        gloreda.data_start(head, m["lho"], "Rfactor_April.tif")


# Budget, streaming, cache ------------------------------------------------------------

def test_over_budget_stops_before_any_tile(tmp_repo, spec, archive, monkeypatch):
    monkeypatch.delenv("AFTERCROP_OFFLINE")
    calls, served = fake_zenodo(monkeypatch, archive)
    small = {**spec, "max_download_bytes": 100}
    monkeypatch.setattr(gloreda, "spec", lambda config=None: small)
    with pytest.raises(gloreda.BudgetExceeded):
        gloreda.run()
    raw = base.RAW_DIR / "gloreda"
    assert not list(raw.glob("tile_*")) and len(list(raw.glob("head_*.bin"))) == 12
    assert all(u == RECORD_URL or rng for u, rng in calls)  # the archive is only read by ranges
    assert sum(served) <= 12 * gloreda.HEAD_BYTES + gloreda.TAIL_BYTES + 10_000
    pl = json.loads(gloreda.plan_path(small).read_text(encoding="utf-8"))
    assert pl["fits_budget"] is False and len(pl["months"]) == 12
    assert pl["stream_bytes_total_estimate"] > pl["max_download_bytes"]


def test_full_run_reads_window_and_site_then_works_offline(tmp_repo, spec, archive, monkeypatch):
    monkeypatch.delenv("AFTERCROP_OFFLINE")
    fake_zenodo(monkeypatch, archive)
    monkeypatch.setattr(gloreda, "spec", lambda config=None: spec)
    rec = gloreda.run()
    assert rec["window_mean_monthly_r"] == [100 * m + 1 for m in range(1, 13)]  # rows 20, 21, 22 add 2, 0, 1
    assert rec["site_monthly_r"] == [100 * m for m in range(1, 13)]
    assert rec["site_annual_r"] == 7800 and sum(rec["site_monthly_share"]) == pytest.approx(1, abs=1e-5)
    assert rec["sanity"]["relative_difference"] == pytest.approx((7800 - 7945) / 7945, abs=1e-4)
    tiles = sorted(p for p in (base.RAW_DIR / "gloreda").glob("tile_*.zlib"))
    assert len(tiles) == 24  # two tiles in each of 12 months
    for p in [q for q in (base.RAW_DIR / "gloreda").iterdir() if not q.name.endswith(".meta.json")]:
        m = base.read_meta(p)
        assert REQUIRED <= set(m) and base.verify(p) and m["bytes"] == p.stat().st_size
    monkeypatch.setenv("AFTERCROP_OFFLINE", "1")
    no_network(monkeypatch)
    again = gloreda.run()
    assert again["window_mean_monthly_r"] == rec["window_mean_monthly_r"]
    prov = gloreda.provenance(spec)
    assert prov["record"]["license"].startswith("CC BY 4.0") and len(prov["pieces"]) == 1 + 12 + 24


def test_offline_miss_is_an_error(tmp_repo, spec, monkeypatch):
    no_network(monkeypatch)
    monkeypatch.setattr(gloreda, "spec", lambda config=None: spec)
    with pytest.raises(base.OfflineCacheMiss):
        gloreda.run(plan_only=True)


def test_a_range_must_come_back_as_206(tmp_repo, spec, archive, monkeypatch):
    monkeypatch.delenv("AFTERCROP_OFFLINE")
    fake_zenodo(monkeypatch, archive, ignore_range=True)
    with pytest.raises(base.FetchError, match="HTTP 200"):
        gloreda.cached_range("gloreda", ARCHIVE_URL, 0, 99, "piece.bin")
    assert not (base.RAW_DIR / "gloreda" / "piece.bin").exists()


def test_wrong_license_stops_at_the_record(tmp_repo, spec, archive, monkeypatch):
    monkeypatch.delenv("AFTERCROP_OFFLINE")
    fake_zenodo(monkeypatch, archive, license_id="proprietary")
    monkeypatch.setattr(gloreda, "spec", lambda config=None: spec)
    with pytest.raises(ValueError, match="license"):
        gloreda.run(plan_only=True)


def test_read_tile_undoes_the_horizontal_predictor(tmp_path):
    data = (np.arange(16 * 16).reshape(16, 16) * 37 - 3000).astype(np.int16)
    diff = data.astype(np.uint16).copy()
    diff[:, 1:] = (data[:, 1:].astype(np.int32) - data[:, :-1].astype(np.int32)).astype(np.uint16)
    p = tmp_path / "tile_example_0.zlib"
    body = zlib.compress(diff.astype("<u2").tobytes())
    p.write_bytes(body)
    base.meta_path(p).write_text(json.dumps({"tiff": {"tile_w": 16, "tile_h": 16, "predictor": 2,
                                                      "byteorder": "<"}}), encoding="utf-8")
    assert (gloreda.read_tile(p) == data).all()
    p.write_bytes(body[:-2] + b"\x00\x00")
    with pytest.raises(zlib.error):
        gloreda.read_tile(p)  # the adler32 of the tile catches a damaged file


@pytest.mark.data
def test_cached_plan_of_the_field_window(offline_session):
    s = gloreda.spec()
    try:
        pl = gloreda.plan(s)
    except base.OfflineCacheMiss:
        pytest.skip("GloREDa directory not cached, run `python -m pipeline.adapters.gloreda --plan-only` first")
    assert pl["archive_url"].startswith("https://zenodo.org/") and pl["license"] == "cc-by-4.0"
    assert [m["member"] for m in pl["months"]] == s["members"]
    for m in pl["months"]:
        assert m["tiff"]["width"] == 43200 and m["tiff"]["tile_w"] == 1024
        assert m["tiff"]["transform"][0] == pytest.approx(1 / 120)

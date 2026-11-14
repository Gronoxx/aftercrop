# Written with an SI tool (Claude, Anthropic) for NASA Space Apps 2026. See AI_USE.md.
"""NASADEM adapter: CMR parsing, Earthdata download rules, tile reading and window cut. Offline.

Every granule id, url, response and height below is an example, fictitious.
"""

import io
import json
import zipfile
from pathlib import Path

import numpy as np
import pytest

from pipeline.adapters import base, nasadem

FIXTURES = Path(__file__).parent / "fixtures"
REQUIRED = {"source_id", "url", "params", "retrieved_at", "sha256", "bytes", "http_status"}
CMR_DOC = json.loads((FIXTURES / "nasadem_cmr_example.json").read_text(encoding="utf-8"))


class FakeResponse:
    def __init__(self, body: bytes, url: str, status=200, ctype="binary/octet-stream", history=()):
        self.body, self.status_code, self.url = body, status, url
        self.headers = {"Content-Type": ctype}
        self.history = list(history)
        self.encoding, self.text = None, ""

    def iter_content(self, n):
        for i in range(0, len(self.body), 7):
            yield self.body[i:i + 7]

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class Hop:
    def __init__(self, url):
        self.url = url


@pytest.fixture
def tmp_repo(tmp_path, monkeypatch):
    monkeypatch.setattr(base, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(base, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(base, "RAW_DIR", tmp_path / "data" / "raw")
    monkeypatch.setattr(base, "INTERIM_DIR", tmp_path / "data" / "interim")
    return tmp_path


@pytest.fixture
def spec():
    return {**nasadem.spec(), "cmr_url": "https://example.test/cmr"}


def no_network(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("network used")

    monkeypatch.setattr(base.requests, "get", boom)


def hgt_zip(tile: str, heights: np.ndarray) -> bytes:
    """Example zip with one big-endian int16 .hgt member, as LP DAAC packs NASADEM_HGT."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(f"{tile}.hgt", heights.astype(">i2").tobytes())
        z.writestr(f"{tile}.num", b"\x00" * heights.size)
    return buf.getvalue()


def fake_server(monkeypatch, zips: dict[str, bytes], signed=True, ctype="binary/octet-stream"):
    calls = []

    def get(u, params=None, **k):
        calls.append(u)
        if u == "https://example.test/cmr":
            return FakeResponse(json.dumps(CMR_DOC).encode(), u, ctype="application/json")
        name = u.rsplit("/", 1)[-1]
        final = f"https://cdn.example.test/bucket/{name}" + ("?X-Amz-Signature=EXAMPLE&X-Amz-Security-Token=EXAMPLE"
                                                            if signed else "")
        hops = [Hop(u), Hop("https://urs.example.test/oauth/authorize?client_id=EXAMPLE")]
        return FakeResponse(zips[name], final, ctype=ctype, history=hops)

    monkeypatch.setattr(base.requests, "get", get)
    return calls


# Spec and CMR ---------------------------------------------------------------------

def test_spec_uses_the_one_field_window():
    cfg = base.load_config()
    s = nasadem.spec(cfg)
    assert s["bbox"] == [float(x) for x in cfg["sources"]["hls"]["window"]["bbox"]]
    assert s["window_id"] == cfg["sources"]["hls"]["window"]["id"]
    assert s["collection"] == {"short_name": "NASADEM_HGT", "version": "001"}
    assert s["pad_px"] >= 1  # slope needs a neighbour ring around the window


def test_tile_bounds():
    assert nasadem.tile_bounds("s24w052") == (-52, -24, -51, -23)
    assert nasadem.tile_bounds("N01E010") == (10, 1, 11, 2)
    with pytest.raises(ValueError):
        nasadem.tile_bounds("x24w052")


def test_parse_cmr_keeps_the_https_zip():
    recs = nasadem.parse_cmr(CMR_DOC)
    assert [r["tile"] for r in recs] == ["s24w052", "s25w052"]
    assert recs[0]["url"].endswith("/NASADEM_HGT_s24w052.zip") and recs[0]["url"].startswith("https://")
    bad = {"items": [{"umm": {"GranuleUR": "SRTMGL1_s24w052", "RelatedUrls": []}}]}
    with pytest.raises(ValueError, match="unexpected"):
        nasadem.parse_cmr(bad)
    two = {"items": [{"umm": {"GranuleUR": "NASADEM_HGT_s24w052", "RelatedUrls": [
        {"Type": "GET DATA", "URL": "https://example.test/a/NASADEM_HGT_s24w052.zip"},
        {"Type": "GET DATA", "URL": "https://example.test/b/NASADEM_HGT_s24w052.zip"}]}}]}
    with pytest.raises(ValueError, match="one https zip"):
        nasadem.parse_cmr(two)


def test_search_drops_tiles_outside_the_window(tmp_repo, spec, monkeypatch):
    monkeypatch.delenv("AFTERCROP_OFFLINE")
    fake_server(monkeypatch, {})
    found = nasadem.search(spec)
    assert [g["tile"] for g in found] == ["s24w052"]  # s25w052 does not reach -23.13
    monkeypatch.setenv("AFTERCROP_OFFLINE", "1")
    no_network(monkeypatch)
    assert nasadem.search(spec) == found


# Earthdata download -----------------------------------------------------------------

def test_download_is_cached_once_and_keeps_no_signed_query(tmp_repo, spec, monkeypatch):
    monkeypatch.delenv("AFTERCROP_OFFLINE")
    heights = np.full((11, 11), 450, dtype=np.int16)
    calls = fake_server(monkeypatch, {"NASADEM_HGT_s24w052.zip": hgt_zip("s24w052", heights)})
    paths = nasadem.fetch(spec)
    assert len(paths) == 1 and paths[0].name == "NASADEM_HGT_s24w052.zip"
    m = base.read_meta(paths[0])
    assert REQUIRED <= set(m) and base.verify(paths[0]) and m["bytes"] == paths[0].stat().st_size
    assert "?" not in m["final_url"] and "Signature" not in json.dumps(m) and "Token" not in json.dumps(m)
    assert m["redirect_hosts"][:2] == ["example.test", "urs.example.test"]
    n = len(calls)
    assert nasadem.fetch(spec) == paths and len(calls) == n  # CMR answer and zip both come from the cache
    monkeypatch.setenv("AFTERCROP_OFFLINE", "1")
    no_network(monkeypatch)
    assert nasadem.fetch(spec) == paths


def test_login_page_is_an_error_and_nothing_is_cached(tmp_repo, spec, monkeypatch):
    monkeypatch.delenv("AFTERCROP_OFFLINE")
    fake_server(monkeypatch, {"NASADEM_HGT_s24w052.zip": b"<html>login</html>"}, ctype="text/html")
    with pytest.raises(base.FetchError, match="HTML"):
        nasadem.fetch(spec)
    assert not list((base.RAW_DIR / "nasadem").glob("NASADEM_HGT_*"))


def test_non_zip_body_is_an_error(tmp_repo, spec, monkeypatch):
    monkeypatch.delenv("AFTERCROP_OFFLINE")
    fake_server(monkeypatch, {"NASADEM_HGT_s24w052.zip": b"not a zip at all"})
    with pytest.raises(base.FetchError, match="not a zip"):
        nasadem.fetch(spec)
    assert not list((base.RAW_DIR / "nasadem").glob("NASADEM_HGT_*"))


def test_offline_miss_is_an_error(tmp_repo, spec, monkeypatch):
    no_network(monkeypatch)
    with pytest.raises(base.OfflineCacheMiss):
        nasadem.cached_earthdata("nasadem", "https://example.test/NASADEM_HGT_s24w052.zip",
                                 "NASADEM_HGT_s24w052.zip")


# Read, mosaic, window ----------------------------------------------------------------

def write_zip(tmp_path, tile, heights) -> Path:
    p = tmp_path / f"NASADEM_HGT_{tile}.zip"
    p.write_bytes(hgt_zip(tile, heights))
    return p


def test_read_tile_grid_and_no_data(tmp_path):
    h = (np.arange(121).reshape(11, 11) + 400).astype(np.int16)
    h[0, 0] = nasadem.NODATA
    dem = nasadem.read(write_zip(tmp_path, "s24w052", h))
    assert dem["data"].shape == (11, 11) and np.isnan(dem["data"][0, 0]) and dem["data"][0, 1] == 401
    assert dem["step_deg"] == pytest.approx(0.1) and (dem["lon0"], dem["lat0"]) == (-52.0, -23.0)
    a, b, c, d, e, f = nasadem.transform(dem)
    assert (a, e) == (pytest.approx(0.1), pytest.approx(-0.1))
    assert c == pytest.approx(-52.05) and f == pytest.approx(-22.95)  # pixel centres on whole degrees


def test_read_refuses_odd_members(tmp_path):
    p = tmp_path / "NASADEM_HGT_s24w052.zip"
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("s24w052.hgt", b"\x00" * 7)
    with pytest.raises(ValueError, match="square"):
        nasadem.read(p)
    q = tmp_path / "NASADEM_HGT_s25w052.zip"
    q.write_bytes(hgt_zip("s24w052", np.zeros((3, 3), dtype=np.int16)))
    with pytest.raises(ValueError, match="another tile"):
        nasadem.read(q)


def test_mosaic_joins_neighbours_on_their_shared_edge(tmp_path):
    west = nasadem.read(write_zip(tmp_path, "s24w052", np.full((11, 11), 100, dtype=np.int16)))
    east = nasadem.read(write_zip(tmp_path, "s24w051", np.full((11, 11), 200, dtype=np.int16)))
    m = nasadem.mosaic([east, west])
    assert m["data"].shape == (11, 21) and (m["lon0"], m["lat0"]) == (-52.0, -23.0)
    assert (m["data"][:, :10] == 100).all() and (m["data"][:, 11:] == 200).all()
    assert m["data"][0, 10] in (100, 200)  # shared column -51.0, the first non-NaN tile wins


def test_window_cut_and_pad(tmp_path):
    h = (np.arange(121).reshape(11, 11) + 400).astype(np.int16)
    dem = nasadem.read(write_zip(tmp_path, "s24w052", h))
    w = nasadem.window(dem, [-51.75, -23.55, -51.45, -23.25], pad_px=0)
    assert w["data"].shape == (3, 3) and (w["lon0"], w["lat0"]) == (pytest.approx(-51.7), pytest.approx(-23.3))
    assert w["data"][0, 0] == 400 + 3 * 11 + 3
    wp = nasadem.window(dem, [-51.75, -23.55, -51.45, -23.25], pad_px=1)
    assert wp["data"].shape == (5, 5) and wp["data"][1, 1] == w["data"][0, 0]
    with pytest.raises(ValueError, match="outside"):
        nasadem.window(dem, [-52.5, -23.5, -51.5, -23.2])
    s = nasadem.summary(w)
    assert s["n_px"] == 9 and s["min_m"] == 436 and s["max_m"] == 460 and s["n_nodata"] == 0


@pytest.mark.data
def test_cached_tile_of_the_field_window(offline_session):
    s = nasadem.spec()
    try:
        win = nasadem.load_window(s, pad_px=0)
    except base.OfflineCacheMiss:
        pytest.skip("NASADEM not cached, run `python -m pipeline.adapters.nasadem` first")
    for p in nasadem.fetch(s):
        assert base.verify(p)
        assert "?" not in base.read_meta(p)["final_url"]
    sm = nasadem.summary(win)
    assert sm["n_nodata"] == 0 and sm["shape"] == [361, 361]
    assert 200 < sm["min_m"] <= sm["mean_m"] <= sm["max_m"] < 1000  # northern Parana plateau, metres

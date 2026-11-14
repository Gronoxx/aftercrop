# Written with an SI tool (Claude, Anthropic) for NASA Space Apps 2026. See AI_USE.md.
"""NASADEM elevation of the field window (plan 8.4 step 10, 9.1, 9.5, sheet M1).

Route: LP DAAC, collection NASADEM_HGT v001 (1 arc-second, about 30 m, SRTM
radar of February 2000), one zip per 1 x 1 degree tile, kept whole and as
distributed (about 10 MB). Sheet M1 names the Planetary Computer COG; the
LP DAAC copy is the NASA distributor, uses the Earthdata login that HLS already
uses and needs no SAS token.

Search: CMR granule search over the field window (public, no login), through
``base.cached_get()``.

Download: ``cached_earthdata()``, with the rules of ``base.cached_get()``: one
download, never rewritten, a ``.meta.json`` next to the file, and with
``AFTERCROP_OFFLINE=1`` a cache miss raises before any request. The LP DAAC URL
redirects to urs.earthdata.nasa.gov, and requests applies the Earthdata entry of
``~/.netrc`` to that host by itself (``trust_env``), as curl does inside GDAL for
HLS. Nothing here reads, sets or logs a credential. The last redirect is a
signed S3 URL: the ``.meta.json`` keeps its scheme, host and path, never the
query string.

Read: the ``.hgt`` member of the zip is read as bytes (big-endian int16, n x n,
pixel centres on whole degrees, n = 3601 at 1 arc-second). -32768 is no data.
Heights as stored, in metres. Nothing in the zip is executed or written to disk.

Run from the repository root:  python -m pipeline.adapters.nasadem
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import sys
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

import numpy as np
import requests

from pipeline.adapters import base

NODATA = -32768
MAX_SIDE = 3601                     # 1 arc-second tile; larger members are refused
EPS = 1e-9
_GRANULE = re.compile(r"^NASADEM_HGT_([ns])(\d{2})([ew])(\d{3})$")
_MEMBER = re.compile(r"(?:^|/)([ns])(\d{2})([ew])(\d{3})\.hgt$", re.IGNORECASE)


# Specs ---------------------------------------------------------------------------

def _resolve(value, config: dict):
    """A value, or a dotted path such as 'sources.hls.window'."""
    if isinstance(value, str) and value.startswith(("sources.", "protocol.")):
        node = config
        for k in value.split("."):
            node = node[k]
        return node
    return value


def spec(config: dict | None = None) -> dict:
    config = config or base.load_config()
    cfg = config["sources"]["nasadem"]
    w = _resolve(cfg["window"], config)
    return {
        "source_id": cfg["source_id"], "cmr_url": cfg["cmr_url"], "license": cfg["license"],
        "doi": cfg["doi"], "collection": dict(cfg["collection"]),
        "window_id": w["id"], "bbox": [float(x) for x in w["bbox"]], "pad_px": int(cfg["pad_px"]),
    }


# CMR search ----------------------------------------------------------------------

def cmr_request(spec: dict) -> tuple[str, dict, str]:
    coll = spec["collection"]
    params = {
        "short_name": coll["short_name"],
        "version": coll["version"],
        "bounding_box": ",".join(f"{x:g}" for x in spec["bbox"]),
        "page_size": 100,
    }
    key = base.cache_key(spec["cmr_url"], params)
    return spec["cmr_url"], params, f"cmr_{coll['short_name']}_{key}.json"


def tile_bounds(tile: str) -> tuple[int, int, int, int]:
    """'s24w052' -> (west, south, east, north) in whole degrees. The name is the south-west corner."""
    m = re.fullmatch(r"([ns])(\d{2})([ew])(\d{3})", tile.lower())
    if not m:
        raise ValueError(f"not a NASADEM tile name: {tile!r}")
    south = int(m.group(2)) * (1 if m.group(1) == "n" else -1)
    west = int(m.group(4)) * (1 if m.group(3) == "e" else -1)
    return west, south, west + 1, south + 1


def parse_cmr(doc: dict) -> list[dict]:
    """Granule records (tile, https zip url) from a CMR umm_json response. Pure."""
    out = []
    for item in doc.get("items", []):
        umm, meta = item["umm"], item.get("meta", {})
        ur = umm["GranuleUR"]
        m = _GRANULE.match(ur)
        if not m:
            raise ValueError(f"unexpected granule id: {ur!r}")
        tile = "".join(m.groups())
        zips = [r["URL"] for r in umm.get("RelatedUrls", [])
                if r.get("Type") == "GET DATA" and r.get("URL", "").startswith("https://")
                and r["URL"].endswith(".zip")]
        if len(zips) != 1:
            raise ValueError(f"{ur}: expected one https zip url, found {len(zips)}")
        if zips[0].rsplit("/", 1)[-1] != f"{ur}.zip":
            raise ValueError(f"{ur}: zip name does not match the granule id")
        out.append({"granule_ur": ur, "concept_id": meta.get("concept-id"), "tile": tile,
                    "bounds": list(tile_bounds(tile)), "url": zips[0]})
    return sorted(out, key=lambda g: g["granule_ur"])


def _intersects(bounds, bbox) -> bool:
    w, s, e, n = bounds
    return w < bbox[2] and e > bbox[0] and s < bbox[3] and n > bbox[1]


def search(spec: dict) -> list[dict]:
    """Tiles that overlap the window. A window with no tile is an error."""
    url, params, name = cmr_request(spec)
    path = base.cached_get(spec["source_id"], url, params=params, filename=name, timeout=120)
    with open(path, encoding="utf-8") as f:
        doc = json.load(f)
    if int(doc.get("hits", 0)) > len(doc.get("items", [])):
        raise RuntimeError(f"{name}: truncated CMR answer")
    found = [g for g in parse_cmr(doc) if _intersects(g["bounds"], spec["bbox"])]
    if not found:
        raise RuntimeError(f"no NASADEM tile overlaps the window {spec['bbox']}")
    return found


# Earthdata download ------------------------------------------------------------------

def _no_query(url: str) -> str:
    u = urlsplit(url)
    return f"{u.scheme}://{u.netloc}{u.path}"


def zip_path(spec: dict, url: str) -> Path:
    """Where the zip of ``url`` is stored. Pure: no I/O."""
    return base.raw_path(spec["source_id"], url, None, url.rsplit("/", 1)[-1])


def cached_earthdata(source_id: str, url: str, filename: str, timeout: float = 300.0) -> Path:
    """Whole-file download behind the Earthdata login, with the rules of ``base.cached_get()``.

    The answer must be a zip (``PK\\x03\\x04``): a login page or any other body
    raises ``base.FetchError`` and nothing is cached.
    """
    path = base.raw_path(source_id, url, None, filename)
    meta = base.meta_path(path)
    if path.exists() and meta.exists():
        return path
    if path.exists() or meta.exists():
        raise RuntimeError(f"incomplete cache entry, inspect by hand: {path}")
    if base.is_offline():
        raise base.OfflineCacheMiss(f"AFTERCROP_OFFLINE=1 and not cached: {url}")

    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_name(path.name + ".part")
    retrieved_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    base.network_calls += 1
    try:
        with requests.get(url, headers={"User-Agent": base.USER_AGENT}, timeout=timeout, stream=True) as r:
            hosts = [urlsplit(h.url).netloc for h in getattr(r, "history", [])] + [urlsplit(r.url).netloc]
            ctype = r.headers.get("Content-Type", "")
            if r.status_code != 200:
                raise base.FetchError(f"HTTP {r.status_code} from {_no_query(r.url)}", r.status_code)
            if "text/html" in ctype:
                raise base.FetchError(f"HTML answer from {_no_query(r.url)}: Earthdata login not accepted?",
                                      r.status_code)
            h, size = hashlib.sha256(), 0
            with open(part, "wb") as f:
                for block in r.iter_content(base.CHUNK):
                    f.write(block)
                    h.update(block)
                    size += len(block)
            with open(part, "rb") as f:
                if f.read(4) != b"PK\x03\x04":
                    raise base.FetchError(f"not a zip: {_no_query(r.url)}", r.status_code)
            info = {
                "source_id": source_id, "url": url, "params": {},
                "final_url": _no_query(r.url), "redirect_hosts": hosts,
                "retrieved_at": retrieved_at, "http_status": r.status_code, "content_type": ctype or None,
                "last_modified": r.headers.get("Last-Modified"), "etag": r.headers.get("ETag"),
                "bytes": size, "sha256": h.hexdigest(), "cache_key": base.cache_key(url, None),
                "auth": "Earthdata login, ~/.netrc read by requests on the redirect; no credential stored",
            }
    except BaseException:
        part.unlink(missing_ok=True)
        raise
    if path.exists():  # another process won the race; keep the first file
        part.unlink()
        return path
    os.replace(part, path)
    with open(meta, "w", encoding="utf-8") as f:
        json.dump(info, f, indent=2, ensure_ascii=False)
        f.write("\n")
    return path


def fetch(spec: dict) -> list[Path]:
    """Every tile zip of the window, one cached file each."""
    return [cached_earthdata(spec["source_id"], g["url"], zip_path(spec, g["url"]).name) for g in search(spec)]


# Read ------------------------------------------------------------------------------

def _side(nbytes: int) -> int:
    n = math.isqrt(nbytes // 2)
    if n * n * 2 != nbytes or n < 3 or n > MAX_SIDE:
        raise ValueError(f"{nbytes} bytes is not a square int16 grid of side 3 to {MAX_SIDE}")
    return n


def read(path: Path) -> dict:
    """Heights of one tile as float32 (NaN at -32768), pixel-centre grid. No side effects."""
    with zipfile.ZipFile(path) as z:
        members = [i for i in z.infolist() if _MEMBER.search(i.filename)]
        if len(members) != 1:
            raise ValueError(f"{path.name}: expected one .hgt member, found {len(members)}")
        info = members[0]
        n = _side(info.file_size)
        raw = z.read(info)
    if len(raw) != info.file_size:
        raise ValueError(f"{path.name}: member size changed while reading")
    tile = "".join(_MEMBER.search(info.filename).groups()).lower()
    if not path.name.lower().endswith(f"{tile}.zip"):
        raise ValueError(f"{path.name}: member {info.filename} is another tile")
    data = np.frombuffer(raw, dtype=">i2").reshape(n, n).astype(np.float32)
    data[data == NODATA] = np.nan
    west, south, east, north = tile_bounds(tile)
    step = 1.0 / (n - 1)
    return {"data": data, "tile": tile, "step_deg": step, "lon0": float(west), "lat0": float(north),
            "member": info.filename, "crs": "EPSG:4326"}


def transform(dem: dict) -> tuple[float, ...]:
    """(a, b, c, d, e, f) of the pixel corners. Pixel (row, col) has its centre at (lat0 - row*step, lon0 + col*step)."""
    s = dem["step_deg"]
    return s, 0.0, dem["lon0"] - s / 2, 0.0, -s, dem["lat0"] + s / 2


def mosaic(tiles: list[dict]) -> dict:
    """One grid from tiles of the same step. Shared edge pixels are equal; the first non-NaN wins. Pure."""
    if not tiles:
        raise ValueError("no tile")
    step = tiles[0]["step_deg"]
    if any(not math.isclose(t["step_deg"], step, rel_tol=1e-12) for t in tiles):
        raise ValueError("tiles of different resolution")
    k = round(1 / step)
    pos = [(round((90 - t["lat0"]) * k), round((t["lon0"] + 180) * k)) for t in tiles]
    r0, c0 = min(p[0] for p in pos), min(p[1] for p in pos)
    r1 = max(p[0] + t["data"].shape[0] for p, t in zip(pos, tiles))
    c1 = max(p[1] + t["data"].shape[1] for p, t in zip(pos, tiles))
    out = np.full((r1 - r0, c1 - c0), np.nan, dtype=np.float32)
    for (r, c), t in zip(pos, tiles):
        rr, cc = r - r0, c - c0
        block = out[rr:rr + t["data"].shape[0], cc:cc + t["data"].shape[1]]
        empty = np.isnan(block)
        block[empty] = t["data"][empty]
    return {"data": out, "tile": "+".join(sorted(t["tile"] for t in tiles)), "step_deg": step,
            "lon0": c0 / k - 180, "lat0": 90 - r0 / k, "crs": "EPSG:4326"}


def window(dem: dict, bbox, pad_px: int = 0) -> dict:
    """Pixels whose centres fall inside ``bbox`` (lon_min, lat_min, lon_max, lat_max), plus ``pad_px``. Pure."""
    lon_min, lat_min, lon_max, lat_max = (float(x) for x in bbox)
    s, (rows, cols) = dem["step_deg"], dem["data"].shape
    c0 = math.ceil((lon_min - dem["lon0"]) / s - EPS) - pad_px
    c1 = math.floor((lon_max - dem["lon0"]) / s + EPS) + pad_px
    r0 = math.ceil((dem["lat0"] - lat_max) / s - EPS) - pad_px
    r1 = math.floor((dem["lat0"] - lat_min) / s + EPS) + pad_px
    if r0 < 0 or c0 < 0 or r1 >= rows or c1 >= cols or r1 < r0 or c1 < c0:
        raise ValueError(f"window rows {r0}:{r1}, cols {c0}:{c1} outside the grid {rows}x{cols}")
    return {**dem, "data": dem["data"][r0:r1 + 1, c0:c1 + 1].copy(),
            "lon0": dem["lon0"] + c0 * s, "lat0": dem["lat0"] - r0 * s, "pad_px": pad_px}


def summary(dem: dict) -> dict:
    """Whole-window check: min, max, mean height and pixel counts. Never per field."""
    d = dem["data"]
    ok = np.isfinite(d)
    return {"n_px": int(d.size), "n_nodata": int((~ok).sum()),
            "min_m": float(np.nanmin(d)) if ok.any() else None,
            "max_m": float(np.nanmax(d)) if ok.any() else None,
            "mean_m": round(float(np.nanmean(d)), 1) if ok.any() else None,
            "shape": list(d.shape), "step_deg": dem["step_deg"]}


def load_window(spec: dict, pad_px: int | None = None) -> dict:
    """Window of the cached tiles (``fetch`` first). Offline-safe once cached."""
    tiles = [read(p) for p in fetch(spec)]
    return window(mosaic(tiles), spec["bbox"], spec["pad_px"] if pad_px is None else pad_px)


def provenance(spec: dict) -> dict:
    url, params, name = cmr_request(spec)
    cmr = base.base_provenance(base.raw_path(spec["source_id"], url, params, name), spec["license"],
                               f"{spec['collection']['short_name']} {spec['collection']['version']}")
    tiles = []
    for g in search(spec):
        p = zip_path(spec, g["url"])
        tiles.append(base.base_provenance(p, spec["license"], f"{spec['collection']['short_name']} "
                                          f"{spec['collection']['version']}", tile=g["tile"], doi=spec["doi"]))
    return {"source_id": spec["source_id"], "license": spec["license"], "doi": spec["doi"],
            "window": {"id": spec["window_id"], "bbox_lonlat": spec["bbox"]}, "cmr": cmr, "tiles": tiles}


def main() -> int:
    s = spec()
    t0 = time.monotonic()
    paths = fetch(s)
    t_fetch = round(time.monotonic() - t0, 1)
    win = load_window(s, pad_px=0)
    out = {"window": {"id": s["window_id"], "bbox_lonlat": s["bbox"]}, "fetch_seconds": t_fetch,
           "files": [{"path": str(p.relative_to(base.REPO_ROOT)), "bytes": p.stat().st_size} for p in paths],
           "window_summary": summary(win), "note": "whole field window, no polygon"}
    base.write_json(base.INTERIM_DIR / f"nasadem_window_{s['window_id']}.json", out)
    print(out)
    print(f"network_calls={base.network_calls}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

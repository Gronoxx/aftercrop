# Written with an SI tool (Claude, Anthropic) for NASA Space Apps 2026. See AI_USE.md.
"""GloREDa monthly rainfall erosivity, Zenodo copy only (plan 9.1, 9.3, 10.9, sheet M1, M1.7).

Zenodo record 8036998 (DOI 10.5281/zenodo.8036998, CC BY 4.0) holds one file,
GloREDa.zip (2.2 GB): 12 monthly GeoTIFFs ``Rfactor_<Month>.tif`` (BigTIFF,
int16, 1/120 degree, deflate tiles of 1024 x 1024, -32768 no data, as read),
the station tables and a readme. The ESDAC copy is never used:
its terms forbid redistribution.

Only what the window needs is read, through HTTP range requests:

1. record: the Zenodo API record (file name, size, md5, license), by ``cached_get()``.
2. directory: the last 64 KiB of the zip, which hold its central directory
   (member names, offsets, sizes, compression).
3. heads: the first 64 KiB of each monthly member, enough for the TIFF header
   and the tile index (the IFD sits at the start of these files).
4. ``plan()``: per month, the tiles that hold the window and the sanity pixel
   (Londrina), and the zip bytes to stream to reach them. A member is one
   deflate stream, so a tile at byte k of a member costs about k bytes from the
   member start. Above ``max_download_bytes`` the run stops before any tile is
   downloaded (``BudgetExceeded``); the plan is written either way to
   ``data/interim/gloreda_plan_<window>.json``.
5. tiles: each member is streamed from its start until the needed tiles are
   decompressed. Only those TIFF tiles are kept, byte for byte as in the TIFF
   (zlib streams, so ``read_tile`` checks each one by its adler32).

Range answers must be 206 with the asked Content-Range. Each raw file gets a
``.meta.json`` as in ``base.cached_get()``, is never rewritten, and offline a
miss raises before any request. Raw files are only parsed as bytes (struct, zlib).

Units as stored: MJ mm/ha/h per month (Panagos et al. 2023). Only the monthly
shares reach the comparator (plan 10.9).

Run from the repository root:
    python -m pipeline.adapters.gloreda --plan-only   # record, directory and heads, about 1 MB
    python -m pipeline.adapters.gloreda               # plus the tiles, if the plan fits the budget
"""

from __future__ import annotations

import argparse
import calendar
import hashlib
import json
import math
import os
import struct
import sys
import time
import zlib
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

import numpy as np
import requests

from pipeline.adapters import base

NODATA = -32768
TAIL_BYTES = 1 << 16
HEAD_BYTES = 1 << 16
STREAM_MARGIN = 1 << 20            # extra compressed bytes allowed in the estimate of each member
MONTHS = [calendar.month_name[m] for m in range(1, 13)]
ZENODO_HOST = "zenodo.org"
LONDRINA_REFERENCE_R = 7945        # MJ mm/ha/h/year, plan 10.9

# TIFF tags read from IFD0
T_WIDTH, T_LENGTH, T_BITS, T_COMPRESSION, T_PREDICTOR = 256, 257, 258, 259, 317
T_TILE_W, T_TILE_L, T_TILE_OFFSETS, T_TILE_COUNTS, T_SAMPLE_FORMAT, T_SPP = 322, 323, 324, 325, 339, 277
T_PIXEL_SCALE, T_TIEPOINT, T_GEOKEYS, T_GDAL_NODATA = 33550, 33922, 34735, 42113
_TYPES = {1: "B", 2: "s", 3: "H", 4: "I", 5: "II", 6: "b", 8: "h", 9: "i", 11: "f", 12: "d", 16: "Q", 17: "q"}
_SIZES = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 6: 1, 7: 1, 8: 2, 9: 4, 10: 8, 11: 4, 12: 8, 16: 8, 17: 8, 18: 8}


class BudgetExceeded(RuntimeError):
    """The bytes the window needs are above ``max_download_bytes``. Nothing was streamed."""


# Specs ---------------------------------------------------------------------------

def _resolve(value, config: dict):
    if isinstance(value, str) and value.startswith(("sources.", "protocol.")):
        node = config
        for k in value.split("."):
            node = node[k]
        return node
    return value


def spec(config: dict | None = None) -> dict:
    config = config or base.load_config()
    cfg = config["sources"]["gloreda"]
    w = _resolve(cfg["window"], config)
    site = next(s for s in config["trial_sites"] if s["id"] == cfg["sanity_site"])
    return {
        "source_id": cfg["source_id"], "record_url": cfg["record_url"], "doi": cfg["doi"],
        "archive": cfg["archive"], "license": cfg["license"],
        "members": [cfg["members"].format(month=m) for m in MONTHS],
        "window_id": w["id"], "bbox": [float(x) for x in w["bbox"]],
        "site": {"id": site["id"], "lat": float(site["lat"]), "lon": float(site["lon"])},
        "max_download_bytes": int(cfg["max_download_bytes"]),
    }


# Zenodo record ---------------------------------------------------------------------

def record_name(spec: dict) -> str:
    return f"record_{spec['record_url'].rstrip('/').rsplit('/', 1)[-1]}.json"


def archive_info(doc: dict, archive: str) -> dict:
    """URL, size and md5 of ``archive`` in a Zenodo API record. Pure.

    The license must be CC BY 4.0 and the file must be served by zenodo.org
    (criterion M1.7: the right copy).
    """
    lic = (doc.get("metadata", {}).get("license") or {}).get("id")
    if lic != "cc-by-4.0":
        raise ValueError(f"record license is {lic!r}, expected cc-by-4.0")
    files = [f for f in doc.get("files", []) if f.get("key") == archive]
    if len(files) != 1:
        raise ValueError(f"{archive} not found once in the record")
    f = files[0]
    url = f["links"]["self"]
    if urlsplit(url).netloc != ZENODO_HOST or urlsplit(url).scheme != "https":
        raise ValueError(f"archive url is not on https://{ZENODO_HOST}: {url}")
    return {"url": url, "size": int(f["size"]), "checksum": f.get("checksum"),
            "doi": doc.get("doi"), "version": doc.get("metadata", {}).get("version"), "license": lic}


def fetch_record(spec: dict) -> dict:
    path = base.cached_get(spec["source_id"], spec["record_url"], filename=record_name(spec), timeout=60)
    with open(path, encoding="utf-8") as f:
        return archive_info(json.load(f), spec["archive"])


# Range requests ----------------------------------------------------------------------

def cached_range(source_id: str, url: str, start: int, end: int, filename: str, timeout: float = 120.0) -> Path:
    """Bytes ``start`` to ``end`` (inclusive) of ``url``, with the rules of ``base.cached_get()``."""
    params = {"range": f"bytes={start}-{end}"}
    path = base.raw_path(source_id, url, params, filename)
    meta = base.meta_path(path)
    if path.exists() and meta.exists():
        return path
    if path.exists() or meta.exists():
        raise RuntimeError(f"incomplete cache entry, inspect by hand: {path}")
    if base.is_offline():
        raise base.OfflineCacheMiss(f"AFTERCROP_OFFLINE=1 and not cached: {url} {params['range']}")
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_name(path.name + ".part")
    retrieved_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    base.network_calls += 1
    headers = {"User-Agent": base.USER_AGENT, "Range": params["range"]}
    try:
        with requests.get(url, headers=headers, timeout=timeout, stream=True) as r:
            _check_206(r, start, end)
            body = b"".join(r.iter_content(base.CHUNK))
            if len(body) != end - start + 1:
                raise base.FetchError(f"short range answer: {len(body)} bytes for {params['range']}", r.status_code)
            part.write_bytes(body)
            info = _meta(source_id, url, params, r, retrieved_at, body)
    except BaseException:
        part.unlink(missing_ok=True)
        raise
    return _commit(part, path, meta, info)


def _check_206(r, start: int, end: int) -> None:
    if r.status_code != 206:
        raise base.FetchError(f"HTTP {r.status_code} for a range request to {r.url}", r.status_code)
    cr = r.headers.get("Content-Range", "")
    if not cr.startswith(f"bytes {start}-{end}/"):
        raise base.FetchError(f"Content-Range {cr!r} does not match bytes {start}-{end}", r.status_code)


def _meta(source_id, url, params, r, retrieved_at, body: bytes) -> dict:
    return {"source_id": source_id, "url": url, "params": params, "final_url": r.url,
            "retrieved_at": retrieved_at, "http_status": r.status_code,
            "content_type": r.headers.get("Content-Type"), "content_range": r.headers.get("Content-Range"),
            "last_modified": r.headers.get("Last-Modified"), "etag": r.headers.get("ETag"),
            "bytes": len(body), "sha256": hashlib.sha256(body).hexdigest(), "cache_key": base.cache_key(url, params)}


def _commit(part: Path, path: Path, meta: Path, info: dict) -> Path:
    if path.exists():  # another process won the race; keep the first file
        part.unlink()
        return path
    os.replace(part, path)
    with open(meta, "w", encoding="utf-8") as f:
        json.dump(info, f, indent=2, ensure_ascii=False)
        f.write("\n")
    return path


# Zip central directory ---------------------------------------------------------------

def parse_directory(tail: bytes, tail_offset: int) -> dict[str, dict]:
    """Members of a zip from its last bytes: name -> method, sizes, local header offset. Pure."""
    i = tail.rfind(b"PK\x05\x06")
    if i < 0 or len(tail) < i + 22:
        raise ValueError("end of central directory not found in the tail")
    _, _, _, _, n, cd_size, cd_off, _ = struct.unpack("<IHHHHIIH", tail[i:i + 22])
    if 0xFFFFFFFF in (cd_size, cd_off) or n == 0xFFFF:
        j = tail.rfind(b"PK\x06\x06")
        if j < 0:
            raise ValueError("zip64 end record not in the tail")
        n, cd_size, cd_off = struct.unpack("<QQQ", tail[j + 32:j + 56])
    p = cd_off - tail_offset
    if p < 0 or p + cd_size > len(tail):
        raise ValueError("central directory is not inside the tail, fetch a longer tail")
    out = {}
    for _ in range(n):
        if tail[p:p + 4] != b"PK\x01\x02":
            raise ValueError("bad central directory entry")
        (method, crc, csize, usize, nl, el, cl, lho) = struct.unpack("<10xH4xIIIHHH8xI", tail[p:p + 46])
        name = tail[p + 46:p + 46 + nl].decode("utf-8")
        extra = tail[p + 46 + nl:p + 46 + nl + el]
        q = 0
        while q + 4 <= len(extra):
            tag, sz = struct.unpack("<HH", extra[q:q + 4])
            if tag == 1:  # zip64 sizes and offset, in this order, only for the saturated fields
                vals, k = extra[q + 4:q + 4 + sz], 0
                if usize == 0xFFFFFFFF:
                    usize, k = struct.unpack("<Q", vals[k:k + 8])[0], k + 8
                if csize == 0xFFFFFFFF:
                    csize, k = struct.unpack("<Q", vals[k:k + 8])[0], k + 8
                if lho == 0xFFFFFFFF:
                    lho = struct.unpack("<Q", vals[k:k + 8])[0]
            q += 4 + sz
        out[name] = {"method": method, "crc32": crc, "csize": csize, "usize": usize, "lho": lho}
        p += 46 + nl + el + cl
    return out


def data_start(head: bytes, lho: int, name: str) -> int:
    """Absolute offset of a member's data from the bytes of its local header. Pure."""
    if head[:4] != b"PK\x03\x04":
        raise ValueError(f"{name}: no local header at {lho}")
    nl, el = struct.unpack("<HH", head[26:30])
    if head[30:30 + nl].decode("utf-8") != name:
        raise ValueError(f"{name}: local header names another member")
    return lho + 30 + nl + el


# TIFF ------------------------------------------------------------------------------

def parse_tiff(buf: bytes) -> dict:
    """Tags of IFD0 needed to locate tiles. ``buf`` is the start of the decompressed TIFF. Pure."""
    bo = {b"II": "<", b"MM": ">"}.get(buf[:2])
    if bo is None:
        raise ValueError("not a TIFF")
    magic = struct.unpack(bo + "H", buf[2:4])[0]
    big = magic == 43
    if magic not in (42, 43):
        raise ValueError(f"TIFF magic {magic}")
    ifd = struct.unpack(bo + ("Q" if big else "I"), buf[8:16] if big else buf[4:8])[0]
    cnt_fmt, ent, val_size = ("Q", 20, 8) if big else ("H", 12, 4)
    n = struct.unpack(bo + cnt_fmt, buf[ifd:ifd + (8 if big else 2)])[0]
    base_off = ifd + (8 if big else 2)
    if base_off + n * ent > len(buf):
        raise ValueError("IFD0 is not inside the head: layout changed, check by hand")
    tags = {}
    for k in range(n):
        e = buf[base_off + k * ent: base_off + (k + 1) * ent]
        tag, typ = struct.unpack(bo + "HH", e[:4])
        count = struct.unpack(bo + ("Q" if big else "I"), e[4:12] if big else e[4:8])[0]
        raw_val = e[12:20] if big else e[8:12]
        size = _SIZES.get(typ, 1) * count
        if size <= val_size:
            data = raw_val[:size]
        else:
            off = struct.unpack(bo + ("Q" if big else "I"), raw_val)[0]
            if off + size > len(buf):
                tags[tag] = ("beyond_head", off, size)
                continue
            data = buf[off:off + size]
        if typ == 2:
            tags[tag] = data.rstrip(b"\x00").decode("ascii", "replace")
        elif typ in _TYPES:
            tags[tag] = list(struct.unpack(bo + _TYPES[typ] * count if typ != 5 else bo + "II" * count, data))
        else:
            tags[tag] = data
    for t in (T_TILE_OFFSETS, T_TILE_COUNTS):
        if not isinstance(tags.get(t), list):
            raise ValueError("tile index is not inside the head (or the file is not tiled)")
    geokeys = tags.get(T_GEOKEYS, [])
    raster_type = next((geokeys[i + 3] for i in range(4, len(geokeys) - 3, 4) if geokeys[i] == 1025), 1)
    sx, sy = tags[T_PIXEL_SCALE][:2]
    tie = tags[T_TIEPOINT]
    x0, y0 = tie[3] - tie[0] * sx, tie[4] + tie[1] * sy
    if raster_type == 2:  # PixelIsPoint: the tie point is a pixel centre
        x0, y0 = x0 - sx / 2, y0 + sy / 2
    return {
        "byteorder": bo, "bigtiff": big, "width": tags[T_WIDTH][0], "height": tags[T_LENGTH][0],
        "bits": tags[T_BITS][0], "sample_format": tags.get(T_SAMPLE_FORMAT, [1])[0],
        "samples": tags.get(T_SPP, [1])[0], "compression": tags[T_COMPRESSION][0],
        "predictor": tags.get(T_PREDICTOR, [1])[0], "tile_w": tags[T_TILE_W][0], "tile_h": tags[T_TILE_L][0],
        "offsets": tags[T_TILE_OFFSETS], "counts": tags[T_TILE_COUNTS],
        "transform": [sx, 0.0, x0, 0.0, -sy, y0], "nodata": tags.get(T_GDAL_NODATA),
    }


def check_layout(t: dict) -> None:
    """The layout as read: one int16 band, deflate tiles. Anything else is a product change."""
    if (t["bits"], t["sample_format"], t["samples"]) != (16, 2, 1):
        raise ValueError(f"expected one int16 band, got bits {t['bits']} format {t['sample_format']}")
    if t["compression"] not in (8, 32946):
        raise ValueError(f"expected deflate tiles, got compression {t['compression']}")
    if t["predictor"] not in (1, 2):
        raise ValueError(f"predictor {t['predictor']} not supported")


def pixel_rc(t: dict, lat: float, lon: float) -> tuple[int, int]:
    sx, _, x0, _, e, y0 = t["transform"]
    r, c = math.floor((y0 - lat) / -e), math.floor((lon - x0) / sx)
    if not (0 <= r < t["height"] and 0 <= c < t["width"]):
        raise ValueError(f"({lat}, {lon}) is outside the raster")
    return r, c


def bbox_rc(t: dict, bbox) -> tuple[int, int, int, int]:
    """(row0, col0, row1, col1), inclusive, of every pixel touching the lon/lat bbox."""
    lon_min, lat_min, lon_max, lat_max = bbox
    sx, _, x0, _, e, y0 = t["transform"]
    r0, c0 = math.floor((y0 - lat_max) / -e + 1e-9), math.floor((lon_min - x0) / sx + 1e-9)
    r1, c1 = math.ceil((y0 - lat_min) / -e - 1e-9) - 1, math.ceil((lon_max - x0) / sx - 1e-9) - 1
    if r0 < 0 or c0 < 0 or r1 >= t["height"] or c1 >= t["width"] or r1 < r0 or c1 < c0:
        raise ValueError(f"bbox {bbox} is outside the raster")
    return r0, c0, r1, c1


def tile_index(t: dict, r: int, c: int) -> int:
    across = math.ceil(t["width"] / t["tile_w"])
    return (r // t["tile_h"]) * across + c // t["tile_w"]


def tiles_needed(t: dict, bbox, site: tuple[float, float]) -> list[int]:
    r0, c0, r1, c1 = bbox_rc(t, bbox)
    idx = {tile_index(t, r, c) for r in range(r0 - r0 % t["tile_h"], r1 + 1, t["tile_h"])
           for c in range(c0 - c0 % t["tile_w"], c1 + 1, t["tile_w"])}
    idx.add(tile_index(t, *pixel_rc(t, *site)))
    return sorted(idx)


# Plan ------------------------------------------------------------------------------

def head_name(member: str) -> str:
    return f"head_{member.rsplit('.', 1)[0]}.bin"


def tile_name(member: str, idx: int) -> str:
    return f"tile_{member.rsplit('.', 1)[0]}_{idx}.zlib"


def _head_tiff(head: bytes, start_in_head: int) -> dict:
    d = zlib.decompressobj(-15)
    return parse_tiff(d.decompress(head[start_in_head:]))


def plan(spec: dict) -> dict:
    """Record, directory and heads (small range requests), then what the window costs. No tile download."""
    arc = fetch_record(spec)
    size = arc["size"]
    t0 = max(0, size - TAIL_BYTES)
    tail = cached_range(spec["source_id"], arc["url"], t0, size - 1, f"zip_tail_{t0}-{size - 1}.bin").read_bytes()
    members = parse_directory(tail, t0)
    months, total = [], 0
    for name in spec["members"]:
        if name not in members:
            raise ValueError(f"{name} not in {spec['archive']}")
        m = members[name]
        if m["method"] != 8:
            raise ValueError(f"{name}: zip method {m['method']}, the streaming plan assumes deflate (8)")
        hp = cached_range(spec["source_id"], arc["url"], m["lho"], min(m["lho"] + HEAD_BYTES, size) - 1,
                          head_name(name))
        head = hp.read_bytes()
        ds = data_start(head, m["lho"], name)
        tif = _head_tiff(head, ds - m["lho"])
        check_layout(tif)
        idx = tiles_needed(tif, spec["bbox"], (spec["site"]["lat"], spec["site"]["lon"]))
        end = max(tif["offsets"][i] + tif["counts"][i] for i in idx)
        est = min(m["csize"], math.ceil(end * m["csize"] / m["usize"]) + STREAM_MARGIN)
        total += est
        months.append({"member": name, "csize": m["csize"], "usize": m["usize"], "data_start": ds,
                       "tiles": [{"index": i, "offset": tif["offsets"][i], "count": tif["counts"][i]} for i in idx],
                       "tiff_end_needed": end, "stream_bytes_estimate": est,
                       "share_of_member": round(end / m["usize"], 4),
                       "tiff": {k: tif[k] for k in ("width", "height", "tile_w", "tile_h", "transform", "nodata",
                                                    "compression", "predictor", "byteorder")}})
    return {"source_id": spec["source_id"], "archive": spec["archive"], "archive_url": arc["url"],
            "archive_bytes": size, "archive_md5": arc["checksum"], "doi": arc["doi"], "version": arc["version"],
            "license": arc["license"], "window": {"id": spec["window_id"], "bbox_lonlat": spec["bbox"]},
            "site": spec["site"], "months": months, "stream_bytes_total_estimate": total,
            "max_download_bytes": spec["max_download_bytes"], "fits_budget": total <= spec["max_download_bytes"],
            "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}


def plan_path(spec: dict) -> Path:
    return base.INTERIM_DIR / f"gloreda_plan_{spec['window_id']}.json"


# Tiles -----------------------------------------------------------------------------

def tile_path(spec: dict, url: str, member: str, idx: int) -> Path:
    return base.raw_path(spec["source_id"], url, None, tile_name(member, idx))


def stream_tiles(spec: dict, url: str, month: dict, timeout: float = 600.0) -> list[Path]:
    """Stream one member from its start until its needed tiles are complete; keep only those tiles."""
    tiles = month["tiles"]
    paths = [tile_path(spec, url, month["member"], t["index"]) for t in tiles]
    todo = [(t, p) for t, p in zip(tiles, paths) if not (p.exists() and base.meta_path(p).exists())]
    if not todo:
        return paths
    for _, p in todo:
        if p.exists() or base.meta_path(p).exists():
            raise RuntimeError(f"incomplete cache entry, inspect by hand: {p}")
    if base.is_offline():
        raise base.OfflineCacheMiss(f"AFTERCROP_OFFLINE=1 and not cached: {month['member']} tiles")
    start, stop = month["data_start"], month["data_start"] + month["csize"] - 1
    params = {"range": f"bytes={start}-{stop}"}
    retrieved_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    bufs = {t["index"]: bytearray() for t, _ in todo}
    end = max(t["offset"] + t["count"] for t, _ in todo)
    d, pos, streamed = zlib.decompressobj(-15), 0, 0
    base.network_calls += 1
    with requests.get(url, headers={"User-Agent": base.USER_AGENT, "Range": params["range"]},
                      timeout=timeout, stream=True) as r:
        _check_206(r, start, stop)
        for block in r.iter_content(base.CHUNK):
            streamed += len(block)
            out = d.decompress(block)
            for t, _ in todo:
                a, b = max(t["offset"], pos), min(t["offset"] + t["count"], pos + len(out))
                if a < b:
                    bufs[t["index"]] += out[a - pos:b - pos]
            pos += len(out)
            if pos >= end:
                break
        status, final_url, headers = r.status_code, r.url, r.headers
    for t, p in todo:
        body = bytes(bufs[t["index"]])
        if len(body) != t["count"]:
            raise base.FetchError(f"{month['member']} tile {t['index']}: {len(body)} of {t['count']} bytes", status)
        zlib.decompress(body)  # adler32 check of the tile before it is cached
        part = p.with_name(p.name + ".part")
        p.parent.mkdir(parents=True, exist_ok=True)
        part.write_bytes(body)
        info = {"source_id": spec["source_id"], "url": url, "params": {**params, "member": month["member"],
                "tile": t["index"], "tiff_offset": t["offset"], "tiff_count": t["count"]},
                "final_url": final_url, "retrieved_at": retrieved_at, "http_status": status,
                "content_type": "application/zlib (one TIFF tile, bytes as in the member)",
                "content_range": headers.get("Content-Range"), "zip_bytes_streamed": streamed,
                "bytes": len(body), "sha256": hashlib.sha256(body).hexdigest(),
                "cache_key": base.cache_key(url, {**params, "tile": t["index"]}),
                "tiff": month["tiff"]}
        _commit(part, p, base.meta_path(p), info)
    return paths


def read_tile(path: Path) -> np.ndarray:
    """One tile as int16 (tile_h, tile_w), predictor undone. No side effects."""
    t = base.read_meta(path)["tiff"]
    raw = zlib.decompress(path.read_bytes())
    n = t["tile_w"] * t["tile_h"]
    if len(raw) != 2 * n:
        raise ValueError(f"{path.name}: {len(raw)} bytes, expected {2 * n}")
    arr = np.frombuffer(raw, dtype=t["byteorder"] + "u2").reshape(t["tile_h"], t["tile_w"])
    if t["predictor"] == 2:
        arr = np.cumsum(arr, axis=1, dtype=np.uint16)
    return arr.astype(np.uint16).view(np.int16).copy()


def values(month: dict, tiles: dict[int, np.ndarray], spec: dict) -> dict:
    """Window block and sanity pixel of one month, as float with NaN at no data. Pure."""
    t = month["tiff"]

    def px(r, c):
        i = tile_index(t, r, c)
        return float(tiles[i][r % t["tile_h"], c % t["tile_w"]])

    r0, c0, r1, c1 = bbox_rc(t, spec["bbox"])
    block = np.array([[px(r, c) for c in range(c0, c1 + 1)] for r in range(r0, r1 + 1)])
    block[block == NODATA] = np.nan
    site = px(*pixel_rc(t, spec["site"]["lat"], spec["site"]["lon"]))
    return {"block": block, "rc": [r0, c0, r1, c1], "site": None if site == NODATA else site}


def window_record(spec: dict, pl: dict, paths_by_month: list[list[Path]]) -> dict:
    """Monthly R of the window (mean of its pixels) and of the sanity pixel, with shares and the check."""
    win, site, rc = [], [], None
    for month, paths in zip(pl["months"], paths_by_month):
        tiles = {t["index"]: read_tile(p) for t, p in zip(month["tiles"], paths)}
        v = values(month, tiles, spec)
        rc = v["rc"]
        win.append(None if np.isnan(v["block"]).all() else round(float(np.nanmean(v["block"])), 2))
        site.append(v["site"])
    out = {"window": {"id": spec["window_id"], "bbox_lonlat": spec["bbox"], "pixels_rc": rc},
           "months": MONTHS, "unit": "MJ mm/ha/h per month, as stored",
           "window_mean_monthly_r": win, "site": spec["site"], "site_monthly_r": site}
    for key, series in (("window", win), ("site", site)):
        if all(x is not None for x in series) and sum(series) > 0:
            out[f"{key}_annual_r"] = round(sum(series), 1)
            out[f"{key}_monthly_share"] = [round(x / sum(series), 6) for x in series]
    if "site_annual_r" in out:
        out["sanity"] = {"reference_r": LONDRINA_REFERENCE_R, "reference": "plan 10.9 (redalyc 180235588026)",
                         "pixel_annual_r": out["site_annual_r"],
                         "relative_difference": round((out["site_annual_r"] - LONDRINA_REFERENCE_R)
                                                      / LONDRINA_REFERENCE_R, 4)}
    return out


def provenance(spec: dict) -> dict:
    """Record and every cached piece of the archive. Never touches the network."""
    rec = base.base_provenance(base.raw_path(spec["source_id"], spec["record_url"], None, record_name(spec)),
                               spec["license"], "GloREDa 1.2 (Zenodo)", doi=spec["doi"])
    d = base.RAW_DIR / spec["source_id"]
    pieces = []
    for p in sorted(d.glob("*")):
        if p.name.endswith(".meta.json") or p.name == record_name(spec) or not p.is_file():
            continue
        m = base.read_meta(p)
        pieces.append({"path": str(p.relative_to(base.REPO_ROOT)), "url": m["url"], "params": m["params"],
                       "sha256": m["sha256"], "bytes": m["bytes"], "retrieved_at": m["retrieved_at"]})
    return {"source_id": spec["source_id"], "license": spec["license"], "doi": spec["doi"],
            "record": rec, "pieces": pieces}


def run(config: dict | None = None, plan_only: bool = False) -> dict:
    s = spec(config)
    t0 = time.monotonic()
    pl = plan(s)
    pl["plan_seconds"] = round(time.monotonic() - t0, 1)
    base.write_json(plan_path(s), pl)
    if plan_only:
        return pl
    if not pl["fits_budget"]:
        raise BudgetExceeded(f"the window needs about {pl['stream_bytes_total_estimate'] / 1e9:.2f} GB of the "
                             f"{pl['archive_bytes'] / 1e9:.2f} GB zip, above max_download_bytes "
                             f"{s['max_download_bytes'] / 1e9:.2f} GB. Plan in {plan_path(s)}")
    t1 = time.monotonic()
    paths = [stream_tiles(s, pl["archive_url"], m) for m in pl["months"]]
    rec = window_record(s, pl, paths)
    rec["tiles_seconds"] = round(time.monotonic() - t1, 1)
    base.write_json(base.INTERIM_DIR / f"gloreda_window_{s['window_id']}.json", rec)
    return rec


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="GloREDa monthly erosivity of the field window (Zenodo copy).")
    ap.add_argument("--plan-only", action="store_true", help="record, directory and heads only")
    args = ap.parse_args(argv)
    try:
        r = run(plan_only=args.plan_only)
    except BudgetExceeded as e:
        print(f"stopped: {e}")
        print(f"network_calls={base.network_calls}")
        return 2
    if "months" in r and isinstance(r["months"], list) and r["months"] and isinstance(r["months"][0], dict):
        for m in r["months"]:
            print(f"{m['member']}: tiles {[t['index'] for t in m['tiles']]}, stream about "
                  f"{m['stream_bytes_estimate'] / 1e6:.0f} MB of {m['csize'] / 1e6:.0f} MB")
        print(f"total about {r['stream_bytes_total_estimate'] / 1e9:.2f} GB, budget "
              f"{r['max_download_bytes'] / 1e9:.2f} GB, fits={r['fits_budget']}, {r['plan_seconds']} s")
    else:
        print({k: v for k, v in r.items() if k != "window"})
    print(f"network_calls={base.network_calls}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

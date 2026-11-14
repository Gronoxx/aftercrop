# Written with an SI tool (Claude, Anthropic) for NASA Space Apps 2026. See AI_USE.md.
"""HLS v2.0 windows of the field window (plan, section 9.2 and sheet M1).

Search: CMR granule search (public, no login), one cached request per
collection and period, through ``base.cached_get()``.

Extraction: windowed reads of the GeoTIFFs at LP DAAC through GDAL ``/vsicurl/``
(HTTP range requests), never whole tiles. The login is the Earthdata entry of
``~/.netrc``, read by curl inside GDAL, with a cookie jar in ``data/.auth/``
(outside data/raw/, so the M1.2 manifest check stays exact). ``cached_window()``
is the cached path of this source and follows the rules of ``cached_get()``:
one download, never rewritten, a ``.meta.json`` next to each file, and with
``AFTERCROP_OFFLINE=1`` a cache miss raises before GDAL is touched.

Extraction rules of section 9.2:

- newest season first, cache per band file of each granule;
- the CRS is read from each file, never fixed. Southern tiles come labelled as
  UTM north (EPSG:32622 with negative northings), so the window is projected
  into whatever CRS the file declares;
- the scale 0.0001 is applied in code, with or without ``scale_factor``;
- duplicates stay on disk and are marked by sensor and date (``dedupe``);
- seasons from ``protocol.dates.ribbon_seasons`` (2018/19 onwards).

Fmask is read first. When the window has fewer open pixels (not fill, cloud or
cloud shadow) than ``protocol.field.min_clean_pixels``, NDVI and NBR2 are not
read: no polygon of the window could reach that count under any stricter mask.

Raw files: ``data/raw/hls/w_<key>/<tif name>.npz`` with the window values as
stored (int16 or uint8), the window's affine transform and the file CRS.
``read()`` loads them with ``allow_pickle=False``; nothing in them is executed.

Run from the repository root:
    python -m pipeline.adapters.hls --start 2025-10-01 --end 2026-03-31
    python -m pipeline.adapters.hls --workers 4          # every ribbon season
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from pathlib import Path

import numpy as np

from pipeline.adapters import base
from pipeline.common import dekad

SCALE = 1e-4
PAGE_SIZE = 2000
FMASK_FILL = 255
FMASK_BITS = {"cirrus": 0, "cloud": 1, "adjacent": 2, "shadow": 3, "snow_ice": 4, "water": 5}
AEROSOL = {"climatology": 0, "low": 1, "moderate": 2, "high": 3}  # bits 6-7
KEEP_TAGS = ("scale_factor", "add_offset", "_FillValue", "long_name", "SATELLITE", "SPACECRAFT_NAME",
             "SENSING_TIME", "MGRS_TILE_ID", "cloud_coverage", "spatial_coverage",
             "HORIZONTAL_CS_NAME", "ACCODE", "Fmask bit description")
EPS = 1e-6
_GRANULE = re.compile(r"^HLS(?:-VI)?\.(L30|S30)\.(T\d{2}[A-Z]{3})\.(\d{7})T(\d{6})\.v(\d+\.\d+)$")
_SAFE_NAME = re.compile(r"^[A-Za-z0-9._-]+$")
_lock = threading.Lock()


def cookie_path() -> Path:
    return base.DATA_DIR / ".auth" / "earthdata.cookies"


def gdal_env() -> dict:
    """GDAL options for the LP DAAC redirect to Earthdata login. No credential is set here."""
    jar = str(cookie_path())
    return {
        "GDAL_HTTP_NETRC": "YES",
        "GDAL_HTTP_COOKIEFILE": jar,
        "GDAL_HTTP_COOKIEJAR": jar,
        "GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR",
        "CPL_VSIL_CURL_ALLOWED_EXTENSIONS": ".tif",
        "GDAL_HTTP_MERGE_CONSECUTIVE_RANGES": "YES",
        "GDAL_HTTP_MAX_RETRY": "4",
        "GDAL_HTTP_RETRY_DELAY": "2",
        "GDAL_HTTP_TIMEOUT": "120",
        "GDAL_HTTP_CONNECTTIMEOUT": "30",
        "GDAL_HTTP_USERAGENT": base.USER_AGENT,
        "CPL_CURL_VERBOSE": "NO",
    }


# Specs ---------------------------------------------------------------------

def _cfg(config: dict) -> dict:
    return config["sources"]["hls"]


def _from_protocol(value, config: dict) -> int:
    """An int, or a dotted path such as 'protocol.field.min_clean_pixels'."""
    if isinstance(value, str) and value.startswith("protocol."):
        node = config
        for k in value.split("."):
            node = node[k]
        return int(node)
    return int(value)


def _common(config: dict, product: str) -> dict:
    cfg = _cfg(config)
    w, p = cfg["window"], cfg[product]
    return {
        "source_id": cfg["source_id"], "cmr_url": cfg["cmr_url"], "license": cfg["license"],
        "window_id": w["id"], "bbox": [float(x) for x in w["bbox"]], "tile": w["tile"],
        "pad_px": int(w["pad_px"]), "product": product,
        "collections": [dict(c) for c in p["collections"]], "bands": list(p["bands"]),
        "fill": int(p["fill"]), "fmask": {k: list(v) if isinstance(v, list) else v for k, v in cfg["fmask"].items()},
        "max_tile_cloud_pct": None,
        "skip_vi_below": _from_protocol(p.get("skip_vi_when_open_px_below", 0), config),
    }


def specs(config: dict | None = None, seasons: list[str] | None = None,
          start: str | None = None, end: str | None = None) -> list[dict]:
    """VI specs, one per ribbon season, newest first. ``start`` and ``end`` give one custom period."""
    config = config or base.load_config()
    common = _common(config, "vi")
    if start or end:
        if not (start and end):
            raise ValueError("give both start and end")
        return [{**common, "label": f"{start}_{end}", "season": None, "start": start, "end": end}]
    first, last = config["protocol"]["dates"]["ribbon_seasons"]
    y0, y1 = dekad.season_start_year(first), dekad.season_start_year(last)
    wanted = seasons or [dekad.season_label(date(y, 9, 1)) for y in range(y1, y0 - 1, -1)]
    out = []
    for s in wanted:
        y = dekad.season_start_year(s)
        if not y0 <= y <= y1:
            raise ValueError(f"season {s} outside protocol.dates.ribbon_seasons {first}..{last}")
        out.append({**common, "label": s.replace("/", "-"), "season": s,
                    "start": f"{y}-09-01", "end": f"{y + 1}-08-31"})
    return sorted(out, key=lambda s: s["start"], reverse=True)


def sr_spec(config: dict, start: str, end: str, max_tile_cloud_pct: int | None) -> dict:
    """Surface reflectance spec (true colour for the basemap only)."""
    return {**_common(config, "sr"), "label": f"sr_{start}_{end}", "season": None,
            "start": start, "end": end, "max_tile_cloud_pct": max_tile_cloud_pct}


# CMR search ------------------------------------------------------------------

def cmr_request(spec: dict, coll: dict) -> tuple[str, dict, str]:
    params = {
        "short_name": coll["short_name"],
        "version": coll["version"],
        "bounding_box": ",".join(f"{x:g}" for x in spec["bbox"]),
        "temporal": f"{spec['start']}T00:00:00Z,{spec['end']}T23:59:59Z",
        "page_size": PAGE_SIZE,
        "sort_key": "start_date",
    }
    if spec.get("max_tile_cloud_pct") is not None:
        params["attribute[]"] = f"int,CLOUD_COVERAGE,0,{int(spec['max_tile_cloud_pct'])}"
    key = base.cache_key(spec["cmr_url"], params)
    name = f"cmr_{coll['short_name']}_{spec['start']}_{spec['end']}_{key}.json"
    return spec["cmr_url"], params, name


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def parse_cmr(doc: dict, sensor: str, collection: str) -> list[dict]:
    """Granule records from a CMR umm_json response. Pure."""
    out = []
    for item in doc.get("items", []):
        umm, meta = item["umm"], item.get("meta", {})
        ur = umm["GranuleUR"]
        m = _GRANULE.match(ur)
        if not m:
            raise ValueError(f"unexpected granule id: {ur!r}")
        if m.group(1) != sensor:
            raise ValueError(f"{ur}: sensor {m.group(1)} in a {sensor} collection")
        begin = umm["TemporalExtent"]["RangeDateTime"]["BeginningDateTime"]
        dt = datetime.fromisoformat(begin.replace("Z", "+00:00")).astimezone(timezone.utc)
        if dt.date() != datetime.strptime(m.group(3), "%Y%j").date():
            raise ValueError(f"{ur}: id date and sensing time {begin} disagree")
        attrs = {a["Name"]: (a.get("Values") or [None])[0] for a in umm.get("AdditionalAttributes", [])}
        urls = {}
        for r in umm.get("RelatedUrls", []):
            u = r.get("URL", "")
            if r.get("Type") == "GET DATA" and u.startswith("https://") and u.endswith(".tif"):
                urls[u.rsplit(".", 2)[-2]] = u
        out.append({
            "granule_ur": ur, "concept_id": meta.get("concept-id"), "revision_id": meta.get("revision-id"),
            "collection": collection, "sensor": sensor, "tile": m.group(2), "version": m.group(5),
            "datetime": dt.isoformat().replace("+00:00", "Z"), "date": dt.date().isoformat(),
            "season": dekad.season_label(dt.date()),
            "cloud_cover_pct": _num(attrs.get("CLOUD_COVERAGE")),
            "spatial_coverage_pct": _num(attrs.get("SPATIAL_COVERAGE")),
            "urls": urls,
        })
    return out


def _load_json(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def search(spec: dict) -> list[dict]:
    """Granules of the spec's tile, newest first, unique by granule id."""
    found = {}
    for coll in spec["collections"]:
        url, params, name = cmr_request(spec, coll)
        doc = _load_json(base.cached_get(spec["source_id"], url, params=params, filename=name, timeout=120))
        hits, items = int(doc.get("hits", 0)), len(doc.get("items", []))
        if hits > items:
            raise RuntimeError(f"{name}: {hits} hits but {items} items returned, shorten the period")
        for g in parse_cmr(doc, coll["sensor"], coll["short_name"]):
            if g["tile"] == spec["tile"]:
                found.setdefault(g["granule_ur"], g)
    return sorted(found.values(), key=lambda g: (g["datetime"], g["granule_ur"]), reverse=True)


# Window maths ------------------------------------------------------------------

def pixel_window(bounds_xy, transform, shape, pad_px: int = 0) -> tuple[int, int, int, int]:
    """(row0, col0, nrows, ncols) of every pixel touching ``bounds_xy``, plus ``pad_px``. Pure.

    ``bounds_xy`` is (left, bottom, right, top) in the grid CRS. ``transform`` is
    (a, b, c, d, e, f) of a north-up grid. The window must fit in ``shape``.
    """
    a, b, c, d, e, f = (float(x) for x in transform[:6])
    if b or d or a <= 0 or e >= 0:
        raise ValueError("only north-up grids are supported")
    left, bottom, right, top = (float(x) for x in bounds_xy)
    if right <= left or top <= bottom:
        raise ValueError(f"empty bounds: {bounds_xy}")
    col0 = math.floor((left - c) / a + EPS) - pad_px
    col1 = math.ceil((right - c) / a - EPS) + pad_px
    row0 = math.floor((top - f) / e + EPS) - pad_px
    row1 = math.ceil((bottom - f) / e - EPS) + pad_px
    rows, cols = shape
    if row0 < 0 or col0 < 0 or row1 > rows or col1 > cols:
        raise ValueError(f"window rows {row0}:{row1}, cols {col0}:{col1} falls outside the tile {rows}x{cols}")
    return row0, col0, row1 - row0, col1 - col0


def window_transform(transform, row0: int, col0: int) -> tuple[float, ...]:
    a, b, c, d, e, f = (float(x) for x in transform[:6])
    return a, b, c + col0 * a + row0 * b, d, e, f + col0 * d + row0 * e


def project_bbox(bbox, crs) -> tuple[float, float, float, float]:
    """lon/lat bbox to (left, bottom, right, top) in ``crs``, edges densified. Offline (PROJ)."""
    from rasterio.warp import transform_bounds

    return transform_bounds("EPSG:4326", crs, *bbox, densify_pts=21)


def window_key(spec: dict) -> str:
    s = json.dumps({"bbox": spec["bbox"], "tile": spec["tile"], "pad_px": spec["pad_px"]}, sort_keys=True)
    return "w_" + hashlib.sha256(s.encode()).hexdigest()[:10]


def window_dir(spec: dict) -> Path:
    return base.RAW_DIR / spec["source_id"] / window_key(spec)


def band_path(spec: dict, url: str) -> Path:
    """Where the window of ``url`` is stored. Pure: no I/O."""
    name = url.rsplit("/", 1)[-1]
    if not name.endswith(".tif"):
        raise ValueError(f"not a GeoTIFF url: {url}")
    name = name[:-4] + ".npz"
    if not _SAFE_NAME.match(name):
        raise ValueError(f"unsafe name: {name!r}")
    return window_dir(spec) / name


# Fetch -------------------------------------------------------------------------

def _read_remote_window(url: str, bbox, pad_px: int) -> tuple[np.ndarray, dict]:
    """One band window over HTTP range reads. Network: only called by cached_window()."""
    import rasterio
    from rasterio.windows import Window

    cookie_path().parent.mkdir(parents=True, exist_ok=True)
    with rasterio.Env(**gdal_env()):
        with rasterio.open(url) as src:
            t = tuple(src.transform)[:6]
            bounds = project_bbox(bbox, src.crs)
            row0, col0, nrows, ncols = pixel_window(bounds, t, src.shape, pad_px)
            data = src.read(1, window=Window(col0, row0, ncols, nrows))
            tags = src.tags()
            info = {
                "crs_wkt": src.crs.to_wkt(), "crs_epsg": src.crs.to_epsg(),
                "tile_transform": list(t), "tile_shape": list(src.shape),
                "window_px": [row0, col0, nrows, ncols],
                "transform": list(window_transform(t, row0, col0)),
                "bounds_xy_requested": list(bounds), "dtype": str(data.dtype), "nodata": src.nodata,
                "block_shape": list(src.block_shapes[0]),
                "compression": getattr(src.compression, "value", None),
                "tags": {k: tags[k] for k in KEEP_TAGS if k in tags},
                "gdal_version": rasterio.__gdal_version__,
            }
    return data, info


def cached_window(spec: dict, url: str) -> Path:
    """Return the cached window of ``url`` for the spec's window, reading it once if needed.

    Same rules as ``base.cached_get()``: never rewritten, a file without its
    .meta.json raises, offline cache miss raises before any network use.
    """
    path = band_path(spec, url)
    meta = base.meta_path(path)
    if path.exists() and meta.exists():
        return path
    if path.exists() or meta.exists():
        raise RuntimeError(f"incomplete cache entry, inspect by hand: {path}")
    if base.is_offline():
        raise base.OfflineCacheMiss(f"AFTERCROP_OFFLINE=1 and not cached: {url} ({window_key(spec)})")

    params = {"bbox": spec["bbox"], "tile": spec["tile"], "pad_px": spec["pad_px"]}
    retrieved_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with _lock:
        base.network_calls += 1  # one per remote file; GDAL makes a few range requests inside
    data, info = _read_remote_window(url, spec["bbox"], spec["pad_px"])

    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_name(path.name + f".{threading.get_ident()}.part")
    try:
        with open(part, "wb") as f:
            np.savez_compressed(f, data=data, transform=np.asarray(info["transform"], dtype=np.float64),
                                crs_wkt=np.asarray(info["crs_wkt"]),
                                window_px=np.asarray(info["window_px"], dtype=np.int64))
        record = {
            "source_id": spec["source_id"], "url": url, "params": params, "final_url": url,
            "retrieved_at": retrieved_at,
            "http_status": None,
            "transport": "GDAL /vsicurl/ HTTP range reads; status not exposed, any HTTP error raises",
            "content_type": "application/x-npz (window written by pipeline.adapters.hls)",
            "bytes": part.stat().st_size, "sha256": base.sha256_file(part),
            "cache_key": base.cache_key(url, params), **info,
        }
    except BaseException:
        part.unlink(missing_ok=True)
        raise
    if path.exists():  # another worker won the race; keep the first file
        part.unlink()
        return path
    os.replace(part, path)
    with open(meta, "w", encoding="utf-8") as f:
        json.dump(record, f, indent=2, ensure_ascii=False)
        f.write("\n")
    return path


def read(path: Path) -> dict:
    """Window values as stored, with transform, CRS and the file tags. No side effects."""
    with np.load(path, allow_pickle=False) as z:
        out = {"data": z["data"], "transform": tuple(float(x) for x in z["transform"]),
               "crs_wkt": str(z["crs_wkt"]), "window_px": tuple(int(x) for x in z["window_px"])}
    m = base.read_meta(path)
    out.update(url=m["url"], nodata=m.get("nodata"), tags=m.get("tags", {}), crs_epsg=m.get("crs_epsg"))
    return out


def scaled(data: np.ndarray, fill: int, scale_tag=None, valid: tuple | None = None) -> np.ndarray:
    """float32 values with the fixed scale 0.0001 (plan 9.2). Fill becomes NaN.

    ``scale_tag`` is the file's scale_factor, if any. Missing is fine; a value
    other than 0.0001 means the product changed and raises.
    """
    if scale_tag not in (None, "") and not math.isclose(float(scale_tag), SCALE, rel_tol=1e-9):
        raise ValueError(f"scale_factor {scale_tag} differs from {SCALE}: product changed, check by hand")
    out = (np.asarray(data, dtype=np.float64) * SCALE).astype(np.float32)
    bad = np.asarray(data) == fill
    if valid is not None:
        bad |= (out < valid[0]) | (out > valid[1])
    out[bad] = np.nan
    return out


# Fmask -------------------------------------------------------------------------

def decode_fmask(fm) -> dict[str, np.ndarray]:
    """One boolean array per Fmask bit, 'aerosol' level 0-3 (-1 at fill) and 'fill'. Pure."""
    fm = np.asarray(fm, dtype=np.uint8)
    fill = fm == FMASK_FILL
    out = {name: (((fm >> bit) & 1) == 1) & ~fill for name, bit in FMASK_BITS.items()}
    out["aerosol"] = np.where(fill, np.int8(-1), ((fm >> 6) & 3).astype(np.int8))
    out["fill"] = fill
    return out


def clear_mask(fm, masked_bits=tuple(FMASK_BITS), masked_aerosol=("high",)) -> np.ndarray:
    """True where no masked bit is set, the aerosol level is not masked and the pixel is not fill."""
    d = decode_fmask(fm)
    bad = d["fill"].copy()
    for name in masked_bits:
        bad |= d[name]
    for level in masked_aerosol:
        bad |= d["aerosol"] == AEROSOL[level]
    return ~bad


def open_mask(fm) -> np.ndarray:
    """The most lenient mask: not fill, not cloud, not cloud shadow."""
    return clear_mask(fm, ("cloud", "shadow"), ())


# Granules and seasons ------------------------------------------------------------

IDENTITY = ("granule_ur", "concept_id", "collection", "sensor", "tile", "version",
            "datetime", "date", "season", "cloud_cover_pct", "spatial_coverage_pct")


def _rel(path: Path) -> str:
    try:
        return str(path.relative_to(base.REPO_ROOT))
    except ValueError:
        return str(path)


def fetch_granule(spec: dict, g: dict, bands: list[str] | None = None) -> dict:
    """Fmask first, then the other bands unless the window is closed. Returns the index record."""
    t0 = time.monotonic()
    rec = {k: g.get(k) for k in IDENTITY}
    rec["files"], rec["downloaded"] = {}, []
    bands = list(bands or spec["bands"])
    if "Fmask" not in g["urls"]:
        raise ValueError(f"{g['granule_ur']}: no Fmask url")

    def get(band: str) -> Path:
        url = g["urls"].get(band)
        if url is None:
            raise ValueError(f"{g['granule_ur']}: no {band} url")
        if not band_path(spec, url).exists():
            rec["downloaded"].append(band)
        p = cached_window(spec, url)
        rec["files"][band] = _rel(p)
        return p

    fm = read(get("Fmask"))["data"]
    fmc = spec["fmask"]
    rec["n_px"] = int(fm.size)
    rec["n_open"] = int(open_mask(fm).sum())
    rec["n_clear"] = int(clear_mask(fm, fmc["masked_bits"], fmc["masked_aerosol"]).sum())
    rec["clear_frac"] = round(rec["n_clear"] / fm.size, 4)
    rest = [b for b in bands if b != "Fmask"]
    if spec["product"] == "vi" and rec["n_open"] < spec["skip_vi_below"]:
        rec["vi_skipped"] = f"window closed: {rec['n_open']} open pixels < {spec['skip_vi_below']}"
    else:
        for b in rest:
            get(b)
    rec["seconds"] = round(time.monotonic() - t0, 2)
    return rec


def dedupe(records: list[dict]) -> list[dict]:
    """Mark all but one record per (sensor, date) with ``duplicate_of``. Pure.

    Kept: most clear pixels in the window, then the larger granule id (later time).
    Nothing is deleted; downstream code drops records with ``duplicate_of``.
    """
    groups: dict[tuple, list[dict]] = {}
    for r in records:
        groups.setdefault((r["sensor"], r["date"]), []).append(r)
    out = []
    for rs in groups.values():
        best = max(rs, key=lambda r: (r.get("n_clear", -1), r["granule_ur"]))
        out += [{**r, "duplicate_of": None if r is best else best["granule_ur"]} for r in rs]
    return sorted(out, key=lambda r: (r["datetime"], r["granule_ur"]), reverse=True)


def _gaps(dates: list[str]) -> int | None:
    ds = sorted(date.fromisoformat(d) for d in set(dates))
    return max(((b - a).days for a, b in zip(ds, ds[1:])), default=None)


def window_summary(records: list[dict], min_px: int) -> dict:
    """Counts for the whole window, not for a polygon (plan 9.2)."""
    kept = [r for r in records if not r.get("duplicate_of") and "error" not in r]
    usable = [r for r in kept if "vi_skipped" not in r and r.get("n_clear", 0) >= min_px]
    half = [r for r in usable if r.get("clear_frac", 0) >= 0.5]
    return {
        "granules": len(records),
        "by_sensor": {s: sum(r["sensor"] == s for r in records) for s in sorted({r["sensor"] for r in records})},
        "duplicates": sum(bool(r.get("duplicate_of")) for r in records),
        "failed": sum("error" in r for r in records),
        "vi_skipped": sum("vi_skipped" in r for r in kept),
        "dates_with_min_clear_px": len({r["date"] for r in usable}),
        "longest_gap_days_min_clear_px": _gaps([r["date"] for r in usable]),
        "dates_half_window_clear": len({r["date"] for r in half}),
        "longest_gap_days_half_window_clear": _gaps([r["date"] for r in half]),
        "note": f"window metrics: a date counts with at least {min_px} clear pixels in the whole window",
    }


def index_path(spec: dict) -> Path:
    return base.INTERIM_DIR / "hls" / f"index_{spec['window_id']}_{spec['label']}.json"


def fetch(spec: dict, workers: int = 1) -> Path:
    """Search and extract every granule of the spec, newest first. Returns the index path.

    A granule that fails is recorded with its error and retried on the next run
    (nothing of it was cached). An offline cache miss stops the run.
    """
    granules = search(spec)

    def one(g):
        try:
            return fetch_granule(spec, g)
        except base.OfflineCacheMiss:
            raise
        except Exception as e:  # noqa: BLE001 - recorded, the other granules go on
            return {**{k: g.get(k) for k in IDENTITY}, "error": f"{type(e).__name__}: {e}"}

    if workers > 1:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            results = list(ex.map(one, granules))
    else:
        results = [one(g) for g in granules]
    records = dedupe(results)
    index = {
        "source_id": spec["source_id"], "product": spec["product"], "label": spec["label"],
        "season": spec["season"], "start": spec["start"], "end": spec["end"],
        "window": {"id": spec["window_id"], "bbox_lonlat": spec["bbox"], "tile": spec["tile"],
                   "pad_px": spec["pad_px"], "key": window_key(spec)},
        "collections": spec["collections"], "fmask": spec["fmask"], "scale": SCALE, "fill": spec["fill"],
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "summary": window_summary(records, spec["skip_vi_below"]),
        "granules": records,
    }
    return base.write_json(index_path(spec), index)


def provenance(spec: dict) -> dict:
    """CMR requests and every band file of the spec, from the cache. Never touches the network."""
    cmr = []
    for coll in spec["collections"]:
        url, params, name = cmr_request(spec, coll)
        path = base.raw_path(spec["source_id"], url, params, name)
        cmr.append(base.base_provenance(path, spec["license"], f"{coll['short_name']} {coll['version']}"))
    files = []
    for rec in _load_json(index_path(spec))["granules"]:
        for band, rel in rec.get("files", {}).items():
            m = base.read_meta(base.REPO_ROOT / rel)
            files.append({"granule_ur": rec["granule_ur"], "band": band, "path": rel, "url": m["url"],
                          "sha256": m["sha256"], "bytes": m["bytes"], "retrieved_at": m["retrieved_at"]})
    stamps = sorted(f["retrieved_at"] for f in files)
    return {
        "source_id": spec["source_id"], "license": spec["license"],
        "version": ", ".join(f"{c['short_name']} {c['version']}" for c in spec["collections"]),
        "window": {"bbox_lonlat": spec["bbox"], "tile": spec["tile"], "pad_px": spec["pad_px"],
                   "key": window_key(spec)},
        "period": [spec["start"], spec["end"]], "cmr": cmr,
        "n_files": len(files), "bytes": sum(f["bytes"] for f in files),
        "retrieved_at": [stamps[0], stamps[-1]] if stamps else None, "files": files,
    }


def run(config: dict | None = None, seasons=None, start=None, end=None, workers: int = 1) -> list[dict]:
    out = []
    for spec in specs(config, seasons, start, end):
        t0, calls0 = time.monotonic(), base.network_calls
        idx = _load_json(fetch(spec, workers))
        recs = [r for r in idx["granules"] if r.get("downloaded")]
        out.append({"label": spec["label"], "index": _rel(index_path(spec)),
                    "seconds": round(time.monotonic() - t0, 1),
                    "network_calls": base.network_calls - calls0,
                    "granules_downloaded": len(recs),
                    "mean_seconds_per_downloaded_granule":
                        round(sum(r["seconds"] for r in recs) / len(recs), 2) if recs else None,
                    **idx["summary"]})
    return out


def _dir_bytes(path: Path) -> int:
    return sum(p.stat().st_size for p in path.glob("*") if p.is_file()) if path.exists() else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="HLS-VI windows of the field window (plan 9.2).")
    ap.add_argument("--season", action="append", help="ribbon season such as 2025/26; repeatable")
    ap.add_argument("--start", help="custom period start, YYYY-MM-DD (with --end)")
    ap.add_argument("--end", help="custom period end, YYYY-MM-DD")
    ap.add_argument("--workers", type=int, default=1, help="parallel granules (default 1)")
    args = ap.parse_args(argv)
    config = base.load_config()
    result = run(config, args.season, args.start, args.end, args.workers)
    failed = 0
    for r in result:
        failed += r["failed"]
        print(f"{r['label']}: {r['granules']} granules {r['by_sensor']}, downloaded {r['granules_downloaded']}, "
              f"failed {r['failed']}, duplicates {r['duplicates']}, vi_skipped {r['vi_skipped']}, "
              f"dates>={config['protocol']['field']['min_clean_pixels']}px clear {r['dates_with_min_clear_px']} "
              f"(longest gap {r['longest_gap_days_min_clear_px']} d), half-window clear "
              f"{r['dates_half_window_clear']} (gap {r['longest_gap_days_half_window_clear']} d), "
              f"{r['seconds']} s, {r['mean_seconds_per_downloaded_granule']} s/granule -> {r['index']}")
    spec = specs(config, args.season, args.start, args.end)[0]
    print(f"window dir {_rel(window_dir(spec))}: {_dir_bytes(window_dir(spec)) / 1e6:.1f} MB")
    print(f"network_calls={base.network_calls}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

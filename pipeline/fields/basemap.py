# Written with an SI tool (Claude, Anthropic) for NASA Space Apps 2026. See AI_USE.md.
"""Cloud-free true-colour basemap of the field window (plan, section 8.4 step 3, sheet M2).

The participant draws fields A, B and C by hand on this PNG. The VI product
has no colour bands, so the basemap uses HLS v2.0 surface reflectance
(HLSL30 and HLSS30, B04 B03 B02), read as windows by ``pipeline.adapters.hls``.

Method (config ``sources.hls.basemap``):

1. Candidates: scenes between ``start`` and ``end`` (default: the latest dry
   season, June to August) whose 110 km tile is at most ``max_tile_cloud_pct``
   cloudy in CMR. That filter only narrows the candidates.
2. The Fmask window of each candidate is read first. A candidate enters when at
   least ``min_window_clear_frac`` of the window is clear by the basemap mask
   (``masked_bits`` and ``masked_aerosol``: only what hides the ground; water
   stays, so lakes show). One scene per date, the ``max_scenes`` clearest.
3. Composite: per-pixel median of the clear observations of each band.
4. One linear stretch common to the three bands (keeps the hue), from the
   ``low_pct`` to the ``high_pct`` percentile, then ``gamma``. A pixel without
   a clear observation is transparent (alpha 0), never filled.

Outputs (derived, rewritten on each run):

    data/interim/basemap_<window_id>.png    RGBA, one PNG pixel per HLS pixel
    data/interim/basemap_<window_id>.json   CRS, transform, bounds, pixel size, scenes

PNG pixel (col, row), counted from the top-left corner of the top-left pixel,
is at x = c + col * a, y = f + row * e in the file CRS, with transform
(a, b, c, d, e, f) from the JSON. Use that CRS, never a fixed EPSG (plan 9.2).

Run from the repository root:  python -m pipeline.fields.basemap
"""

from __future__ import annotations

import os
import struct
import sys
import warnings
import zlib
from pathlib import Path

import numpy as np

from pipeline.adapters import base, hls

RGB = ("B04", "B03", "B02")


def spec(config: dict | None = None) -> dict:
    config = config or base.load_config()
    bm = config["sources"]["hls"]["basemap"]
    s = hls.sr_spec(config, bm["start"], bm["end"], bm.get("max_tile_cloud_pct"))
    s.update(min_window_clear_frac=float(bm["min_window_clear_frac"]), max_scenes=int(bm["max_scenes"]),
             stretch={k: float(v) for k, v in bm["stretch"].items()},
             fmask={**s["fmask"], "masked_bits": list(bm["masked_bits"]),
                    "masked_aerosol": list(bm["masked_aerosol"])})
    return s


def select_scenes(records: list[dict], min_clear_frac: float, max_scenes: int) -> list[dict]:
    """Clearest scenes, one per date, at most ``max_scenes``, returned by date. Pure."""
    ok = [r for r in records if "error" not in r and r.get("clear_frac", 0) >= min_clear_frac]
    best: dict[str, dict] = {}
    for r in ok:
        cur = best.get(r["date"])
        if cur is None or (r["clear_frac"], r["granule_ur"]) > (cur["clear_frac"], cur["granule_ur"]):
            best[r["date"]] = r
    ranked = sorted(best.values(), key=lambda r: (r["clear_frac"], r["date"]), reverse=True)
    return sorted(ranked[:max_scenes], key=lambda r: r["date"])


def composite(bands: np.ndarray, clear: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Median of clear observations. Pure.

    ``bands``: (n, 3, H, W) reflectance with NaN at fill. ``clear``: (n, H, W).
    Returns the (3, H, W) median (NaN where no clear observation) and the
    (H, W) count of clear observations with all three bands present.
    """
    bands = np.asarray(bands, dtype=np.float32)
    clear = np.asarray(clear, dtype=bool)
    if bands.ndim != 4 or bands.shape[1] != 3 or clear.shape != (bands.shape[0],) + bands.shape[2:]:
        raise ValueError(f"shapes {bands.shape} and {clear.shape} do not match")
    good = clear & np.isfinite(bands).all(axis=1)
    stack = np.where(good[:, None], bands, np.nan)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)  # all-NaN pixels stay NaN
        med = np.nanmedian(stack, axis=0)
    return med.astype(np.float32), good.sum(axis=0).astype(np.int32)


def stretch(rgb: np.ndarray, low_pct: float = 2, high_pct: float = 98, gamma: float = 1.0) -> np.ndarray:
    """(3, H, W) reflectance -> (H, W, 4) uint8 RGBA, one stretch for the three bands. Pure."""
    rgb = np.asarray(rgb, dtype=np.float64)
    finite = np.isfinite(rgb).all(axis=0)
    if not finite.any():
        raise ValueError("no pixel with a clear observation")
    lo, hi = np.percentile(rgb[:, finite], [low_pct, high_pct])
    if not hi > lo:
        raise ValueError(f"flat image: percentiles {lo} and {hi}")
    x = np.clip((np.where(finite, rgb, lo) - lo) / (hi - lo), 0.0, 1.0) ** (1.0 / gamma)
    out = np.zeros(finite.shape + (4,), dtype=np.uint8)
    out[..., :3] = np.round(np.moveaxis(x, 0, -1) * 255).astype(np.uint8)
    out[..., :3][~finite] = 0
    out[..., 3] = np.where(finite, 255, 0)
    return out


def png_bytes(img: np.ndarray) -> bytes:
    """8-bit RGB or RGBA PNG, no filter, no ancillary chunks. Pure."""
    img = np.ascontiguousarray(img, dtype=np.uint8)
    if img.ndim != 3 or img.shape[2] not in (3, 4):
        raise ValueError(f"expected (H, W, 3 or 4), got {img.shape}")
    h, w, ch = img.shape

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    raw = b"".join(b"\x00" + img[r].tobytes() for r in range(h))
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 6 if ch == 4 else 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b"")


def write_png(path: Path, img: np.ndarray) -> Path:
    if base.RAW_DIR.resolve() in path.resolve().parents:
        raise ValueError("derived files never go to data/raw/")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(png_bytes(img))
    os.replace(tmp, path)
    return path


def georef(transform, crs_wkt: str, crs_epsg, shape) -> dict:
    """Georeference of the PNG: bounds in the file CRS, pixel size, lon/lat corners for display."""
    from rasterio.warp import transform_bounds

    a, b, c, d, e, f = (float(x) for x in transform[:6])
    if b or d:
        raise ValueError("rotated grids are not supported")
    h, w = shape
    left, top, right, bottom = c, f, c + w * a, f + h * e
    lon_lat = transform_bounds(crs_wkt, "EPSG:4326", left, bottom, right, top, densify_pts=21)
    return {
        "width": int(w), "height": int(h),
        "crs_epsg": crs_epsg, "crs_wkt": crs_wkt,
        "crs_note": ("CRS as declared by the HLS file. Southern tiles are labelled UTM north with "
                     "negative northings (plan 9.2): use this CRS, never EPSG:32722."),
        "transform": [a, b, c, d, e, f],
        "pixel_size_m": [a, -e],
        "bounds": {"left": left, "bottom": bottom, "right": right, "top": top},
        "bounds_lonlat_approx": dict(zip(("lon_min", "lat_min", "lon_max", "lat_max"), lon_lat)),
        "pixel_to_xy": "x = c + col * a, y = f + row * e, (col, row) from the top-left corner",
    }


def _same_grid(ref: dict, cur: dict, name: str) -> None:
    if not (np.allclose(ref["transform"], cur["transform"]) and ref["crs_wkt"] == cur["crs_wkt"]
            and ref["data"].shape == cur["data"].shape):
        raise ValueError(f"{name}: grid differs from the first scene, cannot stack")


def run(config: dict | None = None, workers: int = 1) -> dict:
    s = spec(config)
    candidates = hls.search(s)
    by_id = {g["granule_ur"]: g for g in candidates}

    def screen(g):
        try:
            return hls.fetch_granule(s, g, bands=["Fmask"])
        except base.OfflineCacheMiss:
            raise
        except Exception as e:  # noqa: BLE001 - one bad candidate does not stop the basemap
            return {"granule_ur": g["granule_ur"], "date": g["date"], "error": f"{type(e).__name__}: {e}"}

    if workers > 1:
        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(max_workers=workers) as ex:
            screened = list(ex.map(screen, candidates))
    else:
        screened = [screen(g) for g in candidates]
    chosen = select_scenes(screened, s["min_window_clear_frac"], s["max_scenes"])
    if not chosen:
        raise RuntimeError("no candidate is clear enough: widen the period or lower min_window_clear_frac")

    fm = s["fmask"]
    stack, clear, ref, used = [], [], None, []
    for r in chosen:
        g = by_id[r["granule_ur"]]
        mask = hls.read(hls.cached_window(s, g["urls"]["Fmask"]))
        ref = ref or mask
        _same_grid(ref, mask, g["granule_ur"])
        layers = []
        for band in RGB:
            arr = hls.read(hls.cached_window(s, g["urls"][band]))
            _same_grid(ref, arr, f"{g['granule_ur']} {band}")
            layers.append(hls.scaled(arr["data"], s["fill"], arr["tags"].get("scale_factor")))
        stack.append(layers)
        clear.append(hls.clear_mask(mask["data"], fm["masked_bits"], fm["masked_aerosol"]))
        used.append({k: r[k] for k in ("granule_ur", "date", "sensor", "clear_frac", "cloud_cover_pct")})

    med, count = composite(np.array(stack), np.array(clear))
    rgba = stretch(med, s["stretch"]["low_pct"], s["stretch"]["high_pct"], s["stretch"]["gamma"])
    stem = f"basemap_{s['window_id']}"
    png = write_png(base.INTERIM_DIR / f"{stem}.png", rgba)
    doc = {
        "png": hls._rel(png),
        **georef(ref["transform"], ref["crs_wkt"], ref["crs_epsg"], ref["data"].shape),
        "window": {"id": s["window_id"], "bbox_lonlat": s["bbox"], "tile": s["tile"],
                   "pad_px": s["pad_px"], "key": hls.window_key(s)},
        "method": {
            "product": [f"{c['short_name']} {c['version']}" for c in s["collections"]],
            "bands_rgb": list(RGB), "period": [s["start"], s["end"]],
            "max_tile_cloud_pct": s["max_tile_cloud_pct"],
            "min_window_clear_frac": s["min_window_clear_frac"], "max_scenes": s["max_scenes"],
            "fmask": fm, "composite": "per-pixel median of clear observations",
            "stretch": s["stretch"], "no_clear_observation": "alpha 0, not filled",
        },
        "scenes": used,
        "candidates": len(candidates),
        "pixels_without_clear_obs": int((count == 0).sum()),
        "clear_obs_per_pixel": {"min": int(count.min()), "median": float(np.median(count)), "max": int(count.max())},
        "use": "Draw fields on the PNG in pixel coordinates; geometry code converts with the transform.",
    }
    base.write_json(base.INTERIM_DIR / f"{stem}.json", doc)
    return doc


def main() -> int:
    doc = run(workers=4)
    print(f"{doc['png']}: {doc['width']}x{doc['height']} px, {len(doc['scenes'])} scenes of "
          f"{doc['candidates']} candidates, {doc['pixels_without_clear_obs']} px without clear obs")
    for sc in doc["scenes"]:
        print(f"  {sc['date']} {sc['sensor']} clear {sc['clear_frac']:.3f} tile cloud {sc['cloud_cover_pct']}")
    print(f"network_calls={base.network_calls}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

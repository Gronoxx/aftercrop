# Written with an SI tool (Claude, Anthropic) for NASA Space Apps 2026. See AI_USE.md.
"""Polygon NDVI series from the cached HLS-VI windows (plan 8.4 step 6, sheet M2).

For each granule of the index files (``data/interim/hls/index_<window>_<season>.json``,
written by ``pipeline.adapters.hls``): the Fmask, NDVI and NBR2 windows, the field's
inner-pixel mask on that file's grid (``geometry.FieldMasks``), the clear mask of
``sources.hls.fmask`` and the fixed scale 0.0001 with the fill as NaN (``hls.scaled``).
A pixel is clean when the mask says clear and its NDVI is a valid number in [-1, 1].

One row per granule: mean NDVI and NBR2 of the clean pixels, ``n_clean``,
``n_possible`` (pixels of the inner polygon) and ``clean_frac``. A granule whose window
was closed (``vi_skipped``: NDVI never read) gives a row with ``n_clean`` 0.

Dedupe: one row per sensor and date. Kept: most clean pixels in the polygon, then the
larger granule id (the order of ``hls.dedupe``, counted in the polygon instead of the
window). ``n_duplicates`` says how many were dropped.

No clean rule here. Whether a row is a clean observation (``min_clean_frac`` and
``min_clean_pixels``) is decided by ``ribbon.CleanRule``. Even so, the CLI refuses to
run until ``protocol.field`` holds ``ndvi_green`` and ``min_clean_frac``: the plan
locks both before any field series is read (D10).

Output: ``data/interim/field_<id>_ndvi.csv``.

Run from the repository root:
    python -m pipeline.fields.ndvi_series config/fields/fields.geojson [--rehearsal]
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from collections.abc import Callable, Iterable, Mapping, Sequence
from pathlib import Path

import numpy as np

from pipeline.adapters import base, hls
from pipeline.fields import geometry

VALID = (-1.0, 1.0)
COLUMNS = ("date", "sensor", "granule_ur", "ndvi", "nbr2", "n_clean", "n_possible", "clean_frac",
           "n_duplicates", "note")


# Pure statistics ----------------------------------------------------------------------

def polygon_stats(fmask, ndvi, mask, *, fill: int, masked_bits: Sequence[str], masked_aerosol: Sequence[str],
                  ndvi_scale_tag=None, nbr2=None, nbr2_scale_tag=None) -> dict:
    """Clean-pixel counts and means of one granule over one mask. Pure.

    ``fmask``, ``ndvi`` and ``nbr2`` are the stored windows (uint8 and int16). The mean
    is taken in float64 over the clean pixels; NBR2 over the clean pixels whose NBR2 is
    also valid.
    """
    mask = np.asarray(mask, dtype=bool)
    if np.shape(fmask) != mask.shape or np.shape(ndvi) != mask.shape:
        raise ValueError(f"shapes differ: mask {mask.shape}, Fmask {np.shape(fmask)}, NDVI {np.shape(ndvi)}")
    n_possible = int(mask.sum())
    if n_possible == 0:
        raise ValueError("empty mask")
    clear = hls.clear_mask(fmask, tuple(masked_bits), tuple(masked_aerosol))
    v = hls.scaled(ndvi, fill, ndvi_scale_tag, valid=VALID)
    clean = mask & clear & np.isfinite(v)
    n = int(clean.sum())
    out = {"n_clean": n, "n_possible": n_possible, "clean_frac": n / n_possible,
           "ndvi": float(np.mean(v[clean], dtype=np.float64)) if n else None, "nbr2": None}
    if nbr2 is not None and n:
        if np.shape(nbr2) != mask.shape:
            raise ValueError(f"NBR2 shape {np.shape(nbr2)} differs from the mask {mask.shape}")
        b = hls.scaled(nbr2, fill, nbr2_scale_tag, valid=VALID)[clean]
        b = b[np.isfinite(b)]
        out["nbr2"] = float(np.mean(b, dtype=np.float64)) if b.size else None
    return out


def dedupe(rows: Iterable[Mapping]) -> list[dict]:
    """One row per (sensor, date): most clean pixels, then the larger granule id. Pure."""
    groups: dict[tuple, list[Mapping]] = {}
    for r in rows:
        groups.setdefault((r["sensor"], r["date"]), []).append(r)
    out = []
    for rs in groups.values():
        best = max(rs, key=lambda r: (r["n_clean"], r["granule_ur"]))
        out.append({**best, "n_duplicates": len(rs) - 1})
    return sorted(out, key=lambda r: (r["date"], r["sensor"]))


# Reading the cache ------------------------------------------------------------------------

def _same_grid(ref: Mapping, cur: Mapping, name: str) -> None:
    if not (np.allclose(ref["transform"], cur["transform"]) and ref["crs_wkt"] == cur["crs_wkt"]
            and np.shape(ref["data"]) == np.shape(cur["data"])):
        raise ValueError(f"{name}: grid differs from the granule's Fmask")


def _cached(root: Path, rel: str) -> Path:
    path = (root / rel).resolve()
    if root.resolve() not in path.parents:
        raise ValueError(f"index path outside the repository: {rel!r}")
    return path


def series_from_records(records: Iterable[Mapping], masks_for: Callable[[Mapping], Mapping[str, np.ndarray]], *,
                        fill: int, masked_bits: Sequence[str], masked_aerosol: Sequence[str],
                        root: Path = base.REPO_ROOT, read: Callable = hls.read) -> tuple[dict[str, list[dict]], list[dict]]:
    """Rows per mask name (``masks_for(grid)`` gives ``{name: mask}``), deduplicated, and the skipped granules.

    ``records`` are the ``granules`` of the hls index files. Records with ``error`` are
    skipped and listed. Every band of one granule must share the Fmask grid.
    """
    rows: dict[str, list[dict]] = {}
    skipped: list[dict] = []
    for rec in records:
        ident = {"granule_ur": rec.get("granule_ur"), "date": rec.get("date"), "sensor": rec.get("sensor")}
        if "error" in rec:
            skipped.append({**ident, "reason": f"error in the index: {rec['error']}"})
            continue
        files = rec.get("files") or {}
        if "Fmask" not in files:
            skipped.append({**ident, "reason": "no Fmask file"})
            continue
        fm = read(_cached(root, files["Fmask"]))
        masks = masks_for(geometry.grid_of(fm))
        base_row = {**ident, "nbr2": None, "n_duplicates": 0}
        if "vi_skipped" in rec or "NDVI" not in files:
            note = "vi_skipped" if "vi_skipped" in rec else "no NDVI file"
            for name, m in masks.items():
                rows.setdefault(name, []).append({**base_row, "ndvi": None, "n_clean": 0,
                                                  "n_possible": int(np.sum(m)), "clean_frac": 0.0, "note": note})
            continue
        nd = read(_cached(root, files["NDVI"]))
        _same_grid(fm, nd, f"{rec['granule_ur']} NDVI")
        nb = None
        if "NBR2" in files:
            nb = read(_cached(root, files["NBR2"]))
            _same_grid(fm, nb, f"{rec['granule_ur']} NBR2")
        for name, m in masks.items():
            st = polygon_stats(fm["data"], nd["data"], m, fill=fill, masked_bits=masked_bits,
                               masked_aerosol=masked_aerosol, ndvi_scale_tag=nd["tags"].get("scale_factor"),
                               nbr2=None if nb is None else nb["data"],
                               nbr2_scale_tag=None if nb is None else nb["tags"].get("scale_factor"))
            rows.setdefault(name, []).append({**base_row, **st, "note": ""})
    return {name: dedupe(rs) for name, rs in rows.items()}, skipped


def index_records(config: Mapping, seasons: Sequence[str] | None = None) -> list[dict]:
    """Granule records of the hls index files of ``seasons`` (default: every ribbon season)."""
    out = []
    for spec in hls.specs(config, list(seasons) if seasons else None):
        path = hls.index_path(spec)
        if not path.exists():
            raise FileNotFoundError(f"{path}: no HLS index for {spec['label']}, run make hls first")
        with open(path, encoding="utf-8") as f:
            idx = json.load(f)
        if idx["window"]["key"] != hls.window_key(spec):
            raise ValueError(f"{path}: window {idx['window']['key']} is not the config window {hls.window_key(spec)}")
        out += idx["granules"]
    return out


# CSV --------------------------------------------------------------------------------------

def _fmt(v, digits: int) -> str:
    return "" if v is None else f"{v:.{digits}f}"


def write_csv(path: Path, rows: Iterable[Mapping]) -> Path:
    if base.RAW_DIR.resolve() in path.resolve().parents:
        raise ValueError("derived files never go to data/raw/")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(COLUMNS)
        for r in rows:
            w.writerow([r["date"], r["sensor"], r["granule_ur"], _fmt(r["ndvi"], 6), _fmt(r["nbr2"], 6),
                        r["n_clean"], r["n_possible"], _fmt(r["clean_frac"], 6), r["n_duplicates"], r["note"]])
    tmp.replace(path)
    return path


def _opt_float(s: str) -> float | None:
    if s == "":
        return None
    v = float(s)
    if not math.isfinite(v):
        raise ValueError(f"not a finite number: {s!r}")
    return v


def read_csv(path: Path) -> list[dict]:
    with open(path, encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        if tuple(reader.fieldnames or ()) != COLUMNS:
            raise ValueError(f"{path}: columns {reader.fieldnames} are not {list(COLUMNS)}")
        return [{"date": r["date"], "sensor": r["sensor"], "granule_ur": r["granule_ur"],
                 "ndvi": _opt_float(r["ndvi"]), "nbr2": _opt_float(r["nbr2"]),
                 "n_clean": int(r["n_clean"]), "n_possible": int(r["n_possible"]),
                 "clean_frac": float(r["clean_frac"]), "n_duplicates": int(r["n_duplicates"]),
                 "note": r["note"]} for r in reader]


def csv_path(field_id: str) -> Path:
    return base.INTERIM_DIR / f"field_{field_id}_ndvi.csv"


def fmask_settings(config: Mapping) -> dict:
    h = config["sources"]["hls"]
    return {"fill": int(h["vi"]["fill"]), "masked_bits": list(h["fmask"]["masked_bits"]),
            "masked_aerosol": list(h["fmask"]["masked_aerosol"])}


def main(argv=None) -> int:
    from pipeline.fields import ribbon

    ap = argparse.ArgumentParser(description="NDVI series of each field polygon (plan 8.4 step 6).")
    ap.add_argument("geojson", type=Path, help="fields FeatureCollection, such as config/fields/fields.geojson")
    ap.add_argument("--season", action="append", help="ribbon season such as 2025/26; repeatable (default: all)")
    ap.add_argument("--rehearsal", action="store_true",
                    help="development strip only (protocol.field.development_strip), never a product field")
    args = ap.parse_args(argv)
    config = base.load_config()
    ribbon.CleanRule.from_protocol(config)  # D10 guard: refuses before any series is read
    grule = geometry.GeometryRule.from_protocol(config)
    fields = geometry.load_fields(args.geojson, config, rehearsal=args.rehearsal)  # SA-D63 area
    records = index_records(config, args.season)
    for f in fields:
        rows, skipped = series_from_records(records, geometry.FieldMasks(f, grule), **fmask_settings(config))
        out = write_csv(csv_path(f["field_id"]), rows.get("whole", []))
        print(f"{f['field_id']}: {len(rows.get('whole', []))} rows, {len(skipped)} granules skipped -> "
              f"{out.relative_to(base.REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

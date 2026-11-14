# Written with an SI tool (Claude, Anthropic) for NASA Space Apps 2026. See AI_USE.md.
"""Slope, relief class and low-spot position of a field from NASADEM (plan 8.4 step 10, 9.1, 9.5, 10.9, sheet M2).

Context only by construction: relief never changes a state (plan 9.1). It
reaches the page only as classes, never as a per-pixel layer (M2.5), and never
as erosion: no LS, no K, no t/ha (10.9).

Inputs: a NASADEM window from ``pipeline.adapters.nasadem.load_window`` (WGS 84
grid of pixel centres, 1 arc-second) and the inner polygon of the field, the
one left by the 30 m inner buffer of ``geometry.py``, as a GeoJSON geometry in
EPSG:4326 or in the CRS given with it. This module never buffers.

Slope: Horn (1981) 3 x 3 gradient, as ``gdaldem slope``, with metric spacing
per row from the WGS 84 radii of curvature (the grid is in degrees). Percent.
A pixel next to no data, or on the window edge, has no slope.

Polygon: the pixels whose centre is inside it. Reported: pixel count, mean,
median and 90th percentile of slope, height mean, min and max (M5 applies the
600 m white-mold trigger of plan 13.5), and the share of each relief class.
The median slope is the "declive" that ``select.py`` compares for Field C (8.4 step 4).

Relief classes: Embrapa slope phases (Santos et al. 2015, Manual de descrição e
coleta de solo no campo, 7th ed., and SiBCS 2018), lower edge inclusive. A
published classification, not a project threshold. The class of the field is
the class of its median slope ([DECIDE]: median against majority class).

Low spot (frost as inference, 9.1): field mean height minus the mean height of
a ring of ``ring_m`` metres around the polygon; the alert is raised when the
field sits at least ``drop_m`` below the ring. Both are project rules that are
not in ``protocol:`` yet ([DECIDE]): until ``protocol.relief`` holds them, the
record says ``rule_not_set`` and ``lowspot_alert`` is null.

Output: ``data/interim/field_<id>_relief.json`` with the keys M5 reads
(``slope_class``, ``lowspot_alert``, ``terrace_question``, ``ledger``).

CLI: for each field of a FeatureCollection, the inner pixels on the NASADEM grid are the
centres inside the ring and at least ``protocol.field.inner_buffer_m`` from its edge, the
rule of ``geometry.inner_mask`` (its helpers, in a local UTM CRS): this module still never
buffers a polygon. It also writes the slopes file of ``select.py --slopes``,
``{field_id: median slope in percent}`` (``slope_pct.median``, the slope of the class).

Run from the repository root (NASADEM tiles cached by ``make nasadem``):
    python -m pipeline.fields.relief config/fields/candidates.geojson [--slopes data/interim/candidates_slopes.json]
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import numpy as np

from pipeline.adapters import base, nasadem
from pipeline.fields import geometry

SOURCE_ID = "nasadem"
WGS84_A = 6378137.0
WGS84_F = 1 / 298.257223563
WGS84_E2 = WGS84_F * (2 - WGS84_F)

# (code, Portuguese name, lower edge %, upper edge %), Embrapa slope phases
CLASSES = (
    ("flat", "plano", 0.0, 3.0),
    ("gentle", "suave ondulado", 3.0, 8.0),
    ("rolling", "ondulado", 8.0, 20.0),
    ("strongly_rolling", "forte ondulado", 20.0, 45.0),
    ("mountainous", "montanhoso", 45.0, 75.0),
    ("steep", "escarpado", 75.0, math.inf),
)
CLASS_SOURCE = "Embrapa slope phases: Santos et al. 2015 (Manual de descricao e coleta de solo no campo), SiBCS 2018"
_FIELD_ID = geometry.FIELD_ID        # one field id rule, the one of geometry.py
SLOPE_KEY = "median"                 # the slope select.py compares: the one the class is read from


class ReliefError(ValueError):
    pass


# Grid ------------------------------------------------------------------------------

def spacing_m(lat_deg, step_deg: float) -> tuple[np.ndarray, float]:
    """East-west spacing at each latitude and the north-south spacing, in metres (WGS 84)."""
    phi = np.radians(np.asarray(lat_deg, dtype=np.float64))
    w = np.sqrt(1 - WGS84_E2 * np.sin(phi) ** 2)
    n = WGS84_A / w                                    # prime vertical radius
    m = WGS84_A * (1 - WGS84_E2) / w ** 3              # meridional radius
    step = math.radians(step_deg)
    return n * np.cos(phi) * step, m * step


def row_lats(dem: dict) -> np.ndarray:
    return dem["lat0"] - np.arange(dem["data"].shape[0]) * dem["step_deg"]


def slope_pct(dem: dict) -> np.ndarray:
    """Horn slope in percent, NaN on the edge and next to no data. Pure."""
    z = np.asarray(dem["data"], dtype=np.float64)
    out = np.full(z.shape, np.nan)
    if z.shape[0] < 3 or z.shape[1] < 3:
        return out
    dx, dy = spacing_m(row_lats(dem)[1:-1], dem["step_deg"])
    dym = float(np.mean(dy))
    a, b, c = z[:-2, :-2], z[:-2, 1:-1], z[:-2, 2:]
    d, f = z[1:-1, :-2], z[1:-1, 2:]
    g, h, i = z[2:, :-2], z[2:, 1:-1], z[2:, 2:]
    dzdx = ((c + 2 * f + i) - (a + 2 * d + g)) / (8 * dx[:, None])
    dzdy = ((g + 2 * h + i) - (a + 2 * b + c)) / (8 * dym)
    out[1:-1, 1:-1] = 100 * np.hypot(dzdx, dzdy)
    out[~np.isfinite(z)] = np.nan  # Horn skips the centre: a pixel without height gets no slope
    return out


def class_of(slope: float) -> str:
    """Relief class code of one slope in percent."""
    if not math.isfinite(slope) or slope < 0:
        raise ReliefError(f"slope must be a non-negative number, got {slope!r}")
    return next(code for code, _, lo, hi in CLASSES if lo <= slope < hi)


def class_shares(slopes: np.ndarray) -> dict[str, float]:
    s = np.asarray(slopes, dtype=np.float64)
    s = s[np.isfinite(s)]
    if s.size == 0:
        raise ReliefError("no slope inside the polygon")
    return {code: round(float(((s >= lo) & (s < hi)).sum()) / s.size, 4) for code, _, lo, hi in CLASSES}


# Polygon -----------------------------------------------------------------------------

def to_lonlat(geometry: dict, crs: str | None = None) -> dict:
    """GeoJSON geometry in EPSG:4326. Offline (PROJ)."""
    if crs in (None, "EPSG:4326", "OGC:CRS84"):
        return geometry
    from rasterio.warp import transform_geom

    return transform_geom(crs, "EPSG:4326", geometry)


def polygon_mask(dem: dict, geometry: dict, crs: str | None = None) -> np.ndarray:
    """True for pixels whose centre is inside the polygon."""
    from rasterio.features import geometry_mask

    if geometry.get("type") not in ("Polygon", "MultiPolygon"):
        raise ReliefError(f"expected a Polygon or MultiPolygon, got {geometry.get('type')!r}")
    return geometry_mask([to_lonlat(geometry, crs)], out_shape=dem["data"].shape,
                         transform=nasadem.transform(dem), all_touched=False, invert=True)


def ring_mask(dem: dict, mask: np.ndarray, ring_m: float) -> np.ndarray:
    """Pixels outside ``mask`` within ``ring_m`` metres of it (disc dilation, spacing at the window centre)."""
    if ring_m <= 0:
        raise ReliefError("ring_m must be positive")
    lat_c = float(np.mean(row_lats(dem)))
    dx, dy = spacing_m(lat_c, dem["step_deg"])
    dx = float(dx)
    ri, rj = int(ring_m // dy), int(ring_m // dx)
    rows, cols = mask.shape
    grown = mask.copy()
    for i in range(-ri, ri + 1):
        for j in range(-rj, rj + 1):
            if (i * dy) ** 2 + (j * dx) ** 2 > ring_m ** 2 or (i == 0 and j == 0):
                continue
            src = mask[max(0, -i):rows - max(0, i), max(0, -j):cols - max(0, j)]
            grown[max(0, i):rows - max(0, -i), max(0, j):cols - max(0, -j)] |= src
    return grown & ~mask


def lowspot(dem: dict, mask: np.ndarray, rule: dict | None) -> dict:
    """Field mean height against its ring. ``rule`` = {ring_m, drop_m} from protocol.relief, or None."""
    if not rule:
        return {"status": "rule_not_set", "alert": None,
                "note": "ring width and drop are not in protocol.relief yet ([DECIDE])"}
    ring_m, drop_m = float(rule["ring_m"]), float(rule["drop_m"])
    ring = ring_mask(dem, mask, ring_m)
    z = np.asarray(dem["data"], dtype=np.float64)
    zf, zr = z[mask & np.isfinite(z)], z[ring & np.isfinite(z)]
    if zf.size == 0 or zr.size == 0:
        raise ReliefError("no height in the field or in its ring")
    rel = float(zf.mean() - zr.mean())
    return {"status": "computed", "ring_m": ring_m, "drop_m": drop_m, "n_ring_px": int(zr.size),
            "field_minus_ring_m": round(rel, 2), "alert": rel <= -drop_m}


# Field record ------------------------------------------------------------------------

def relief_rule(config: dict | None) -> dict | None:
    return ((config or {}).get("protocol") or {}).get("relief")


def utm_crs(lon: float, lat: float) -> str:
    """WGS 84 UTM zone of a point, as an EPSG code. Pure."""
    zone = min(60, int((lon + 180) // 6) + 1)
    return f"EPSG:{(32700 if lat < 0 else 32600) + zone}"


def inner_mask_on_dem(dem: dict, ring_lonlat, buffer_m: float) -> np.ndarray:
    """DEM pixels whose centre is inside the ring and at least ``buffer_m`` from its edge.

    The rule of ``geometry.inner_mask``, with its helpers, on the DEM grid: centres and
    ring go to the UTM zone of the ring, where distances are metres. Offline (PROJ).
    """
    from rasterio.warp import transform

    if buffer_m < 0:
        raise ReliefError("buffer_m must be zero or more")
    ring = [(float(x), float(y)) for x, y in ring_lonlat]
    lons, lats = zip(*ring)
    crs = utm_crs(sum(lons) / len(lons), sum(lats) / len(lats))
    xy = geometry.project(ring, crs)
    rows, cols = dem["data"].shape
    s = dem["step_deg"]
    # Only the rows and columns around the ring are tested.
    r0 = max(0, math.floor((dem["lat0"] - max(lats)) / s) - 1)
    r1 = min(rows - 1, math.ceil((dem["lat0"] - min(lats)) / s) + 1)
    c0 = max(0, math.floor((min(lons) - dem["lon0"]) / s) - 1)
    c1 = min(cols - 1, math.ceil((max(lons) - dem["lon0"]) / s) + 1)
    mask = np.zeros((rows, cols), dtype=bool)
    if r0 > r1 or c0 > c1:
        return mask
    rr, cc = np.mgrid[r0:r1 + 1, c0:c1 + 1]
    lat_c, lon_c = dem["lat0"] - rr * s, dem["lon0"] + cc * s
    px, py = transform("EPSG:4326", crs, lon_c.ravel().tolist(), lat_c.ravel().tolist())
    px = np.asarray(px, dtype=np.float64).reshape(rr.shape)
    py = np.asarray(py, dtype=np.float64).reshape(rr.shape)
    keep = geometry.inside(px, py, xy) & (geometry.edge_distance(px, py, xy) >= buffer_m - geometry.EPS_M)
    mask[r0:r1 + 1, c0:c1 + 1] = keep
    return mask


def field_relief(dem: dict, geometry: dict | None = None, crs: str | None = None, rule: dict | None = None,
                 *, mask: np.ndarray | None = None) -> dict:
    """Relief facts of one inner polygon (or of an inner-pixel ``mask`` of the DEM grid).

    Classes and summaries only, never a pixel layer. Pure.
    """
    if mask is None:
        mask = polygon_mask(dem, geometry, crs)
    elif np.shape(mask) != np.shape(dem["data"]):
        raise ReliefError(f"mask {np.shape(mask)} and DEM {np.shape(dem['data'])} differ")
    mask = np.asarray(mask, dtype=bool)
    n = int(mask.sum())
    if n == 0:
        raise ReliefError("no NASADEM pixel centre inside the polygon")
    s = slope_pct(dem)[mask]
    z = np.asarray(dem["data"], dtype=np.float64)[mask]
    ok = s[np.isfinite(s)]
    if ok.size == 0:
        raise ReliefError("no slope inside the polygon: widen the window pad")
    med = float(np.median(ok))
    low = lowspot(dem, mask, rule)
    return {
        "source_id": SOURCE_ID, "context_only": True,
        "n_px": n, "n_px_without_slope": int(n - ok.size),
        "slope_pct": {"mean": round(float(ok.mean()), 2), "median": round(med, 2),
                      "p90": round(float(np.percentile(ok, 90)), 2)},
        "height_m": {"mean": round(float(np.nanmean(z)), 1), "min": float(np.nanmin(z)), "max": float(np.nanmax(z))},
        "class_shares": class_shares(ok),
        "slope_class": class_of(med),
        "slope_class_rule": "class of the median slope",
        "classes": [{"code": c, "name_pt": pt, "from_pct": lo, "to_pct": None if math.isinf(hi) else hi}
                    for c, pt, lo, hi in CLASSES],
        "class_source": CLASS_SOURCE,
        "lowspot": low, "lowspot_alert": low["alert"],
        "terrace_question": True,   # the agronomist sheet always asks about terraces (plan 7.12, 9.1)
        "method": {"slope": "Horn 3x3, WGS 84 metric spacing per row", "polygon": "pixel centres inside",
                   "grid_step_deg": dem["step_deg"], "tile": dem.get("tile")},
    }


def build(field_id: str, dem: dict, geometry: dict | None = None, crs: str | None = None,
          config: dict | None = None, *, mask: np.ndarray | None = None) -> dict:
    if not _FIELD_ID.match(field_id):
        raise ReliefError(f"unsafe field id: {field_id!r}")
    rec = field_relief(dem, geometry, crs, relief_rule(config), mask=mask)
    return {"field_id": field_id, **rec, "ledger": f"field_{field_id}.relief"}


def output_path(field_id: str) -> Path:
    if not _FIELD_ID.match(field_id):
        raise ReliefError(f"unsafe field id: {field_id!r}")
    return base.INTERIM_DIR / f"field_{field_id}_relief.json"


def write(record: dict) -> Path:
    return base.write_json(output_path(record["field_id"]), record)


# Slopes file of select.py and CLI ------------------------------------------------------------

def slopes(records) -> dict[str, float]:
    """``{field_id: slope_pct.median}`` of relief records, the input of ``select.py --slopes``. Pure."""
    out = {}
    for r in records:
        if r["field_id"] in out:
            raise ReliefError(f"field {r['field_id']!r} twice")
        out[r["field_id"]] = r["slope_pct"][SLOPE_KEY]
    return out


def slopes_path(geojson: Path) -> Path:
    return base.INTERIM_DIR / f"{Path(geojson).stem}_slopes.json"


def run(geojson: Path, dem: dict, config: dict) -> list[dict]:
    """Relief record of every field of a FeatureCollection, on its inner pixels of ``dem``."""
    buffer_m = geometry.GeometryRule.from_protocol(config).inner_buffer_m
    out = []
    for f in geometry.parse_fields(geometry.read_geojson(geojson)):
        mask = inner_mask_on_dem(dem, f["ring"], buffer_m)
        out.append(build(f["field_id"], dem, config=config, mask=mask))
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Relief class of each field from NASADEM (plan 8.4 step 10).")
    ap.add_argument("geojson", type=Path, help="fields or candidates FeatureCollection in lon/lat")
    ap.add_argument("--slopes", type=Path, default=None,
                    help="slopes JSON for select.py (default: data/interim/<geojson stem>_slopes.json)")
    args = ap.parse_args(argv)
    config = base.load_config()
    dem = nasadem.load_window(nasadem.spec(config))
    records = run(args.geojson, dem, config)
    for r in records:
        p = write(r)
        print(f"{r['field_id']}: {r['slope_class']}, median slope {r['slope_pct']['median']}% -> {p.name}")
    out = args.slopes or slopes_path(args.geojson)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(slopes(records), indent=1) + "\n", encoding="utf-8")
    print(f"slopes -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

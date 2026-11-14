# Written with an SI tool (Claude, Anthropic) for NASA Space Apps 2026. See AI_USE.md.
"""Field polygons: validation, 30 m inner buffer in metres, possible clean pixels.

Plan section 8.4 step 5 and sheet M2 (M2.1). Input: a GeoJSON FeatureCollection in
lon/lat (EPSG:4326, RFC 7946) drawn by hand with ``tools/draw_fields.html`` on the
basemap and saved under ``config/fields/``. Each feature is one Polygon with one ring
and the properties ``field_id`` and, optionally, ``criterion``. Nothing else: the
polygon is anonymous (no CAR, no owner, M2.1). The only other top-level member allowed
is ``window_id``, the basemap window the polygons were drawn on.

Grid: the HLS window grid ``{"transform": (a, b, c, d, e, f), "shape": (rows, cols),
"crs_wkt": ...}``, from a cached HLS window (``grid_of``) or from the basemap
georeference (``grid_from_georef``). The CRS is the one the file declares, never a
fixed EPSG (plan 9.2): southern tiles come as UTM north with negative northings, and
the maths below works on those as they are.

Inner polygon: the points of the polygon at least ``inner_buffer_m`` from its boundary
(what a negative buffer gives), in metres in the grid CRS. A pixel belongs to the field
when its centre lies in the inner polygon. Exact for one ring and without shapely: the
even-odd rule for the inside test and the distance to every edge.

Possible clean pixels: the pixels of the inner polygon. With fewer than
``protocol.field.min_clean_pixels`` the polygon is refused: no observation of it could
ever be clean.

Field halves (N1, ``noise.py``): the inner pixels split in two equal counts across the
principal axis of their centres.

Area (SA-D63, ``AreaRule``). Product mode, the default: every vertex lies west of
``protocol.field.product_area.lon_max`` (lon below it). Rehearsal mode (``--rehearsal``,
``rehearsal=True``): every vertex lies east of ``protocol.field.development_strip.lon_min``
(lon above it), and a product polygon is refused. The development strip is for the
rehearsal only, never a product field. Every field CLI (geometry,
ndvi_series, ribbon, events, noise, select) checks the area through ``load_fields``.

Run from the repository root (reads the polygons and the basemap georeference only,
never a field series):
    python -m pipeline.fields.geometry config/fields/candidates.geojson [--rehearsal]
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from pipeline.adapters import base
from pipeline.bench.common import ProtocolError, protocol_block

ALLOWED_TOP = ("type", "features", "window_id")
ALLOWED_FEATURE = ("type", "properties", "geometry")
ALLOWED_PROPERTIES = ("field_id", "criterion")
# Lower case only: field_<id>_*.csv must not collide on a case-insensitive disk.
FIELD_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
MAX_VERTICES = 500
EPS_M = 1e-6  # metres; a centre exactly at the buffer distance stays in


class GeometryError(ValueError):
    """A polygon file or ring the field modules refuse."""


# Protocol access (shared by ribbon.py and events.py) ---------------------------------

def field_section(protocol: Mapping) -> Mapping:
    """``protocol.field``, from the whole YAML document or from the protocol block."""
    sec = protocol_block(protocol).get("field")
    if not isinstance(sec, Mapping):
        raise ProtocolError("protocol has no 'field' block")
    return sec


def locked(section: Mapping, key: str, where: str = "field"):
    """A value the protocol must hold before any field series is read. Null is refused."""
    if key not in section:
        raise ProtocolError(f"protocol.{where}.{key} is missing [DECIDE]")
    value = section[key]
    if value is None:
        raise ProtocolError(f"protocol.{where}.{key} is null: lock it in the protocol before "
                            "reading any field series [DECIDE]")
    return value


def number(value, where: str, lo: float | None = None, hi: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ProtocolError(f"{where} must be a finite number, got {value!r}")
    if (lo is not None and value < lo) or (hi is not None and value > hi):
        raise ProtocolError(f"{where} must lie in [{lo}, {hi}], got {value!r}")
    return float(value)


def integer(value, where: str, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ProtocolError(f"{where} must be an integer >= {minimum}, got {value!r}")
    return value


@dataclass(frozen=True)
class GeometryRule:
    inner_buffer_m: float
    min_clean_pixels: int

    def __post_init__(self):
        number(self.inner_buffer_m, "inner_buffer_m", 0)
        integer(self.min_clean_pixels, "min_clean_pixels", 1)

    @classmethod
    def from_protocol(cls, protocol: Mapping) -> "GeometryRule":
        sec = field_section(protocol)
        return cls(inner_buffer_m=number(locked(sec, "inner_buffer_m"), "protocol.field.inner_buffer_m", 0),
                   min_clean_pixels=integer(locked(sec, "min_clean_pixels"), "protocol.field.min_clean_pixels", 1))


@dataclass(frozen=True)
class AreaRule:
    """SA-D63: product polygons west of ``product_lon_max``, rehearsal polygons east of ``strip_lon_min``."""
    product_lon_max: float
    strip_lon_min: float

    def __post_init__(self):
        number(self.product_lon_max, "product_area.lon_max", -180, 180)
        number(self.strip_lon_min, "development_strip.lon_min", -180, 180)
        if self.strip_lon_min < self.product_lon_max:
            raise ProtocolError("development_strip.lon_min lies west of product_area.lon_max: the areas overlap")

    @classmethod
    def from_protocol(cls, protocol: Mapping) -> "AreaRule":
        sec = field_section(protocol)
        prod, strip = locked(sec, "product_area"), locked(sec, "development_strip")
        if not isinstance(prod, Mapping) or not isinstance(strip, Mapping):
            raise ProtocolError("protocol.field.product_area and development_strip must be mappings")
        return cls(product_lon_max=number(locked(prod, "lon_max", "field.product_area"),
                                          "protocol.field.product_area.lon_max", -180, 180),
                   strip_lon_min=number(locked(strip, "lon_min", "field.development_strip"),
                                        "protocol.field.development_strip.lon_min", -180, 180))

    def error(self, field: Mapping, rehearsal: bool = False) -> str | None:
        """Why the polygon is outside the area of the mode, or None. Pure."""
        lons = [p[0] for p in field["ring"]]
        fid = field["field_id"]
        if rehearsal:
            if min(lons) <= self.strip_lon_min:
                return (f"{fid}: rehearsal mode, but a vertex lies at lon {min(lons):.5f}, not east of the "
                        f"development strip limit {self.strip_lon_min:g} (protocol.field.development_strip.lon_min). "
                        "Product polygons are refused in rehearsal mode")
            return None
        if max(lons) >= self.product_lon_max:
            hint = " It lies in the development strip: use --rehearsal." if min(lons) > self.strip_lon_min else ""
            return (f"{fid}: a vertex lies at lon {max(lons):.5f}, not west of the product area limit "
                    f"{self.product_lon_max:g} (protocol.field.product_area.lon_max).{hint}")
        return None


def check_area(fields: Sequence[Mapping], area: AreaRule, rehearsal: bool = False) -> None:
    """Refuse the whole file if any polygon is outside the area of the mode."""
    errs = [e for e in (area.error(f, rehearsal) for f in fields) if e]
    if errs:
        raise GeometryError("; ".join(errs))


def load_fields(path: Path, protocol: Mapping, rehearsal: bool = False) -> list[dict]:
    """``parse_fields`` of a file, then the area check of the mode (product by default)."""
    fields = parse_fields(read_geojson(path))
    check_area(fields, AreaRule.from_protocol(protocol), rehearsal)
    return fields


# GeoJSON -------------------------------------------------------------------------------

def read_geojson(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _position(p, where: str) -> tuple[float, float]:
    if not isinstance(p, (list, tuple)) or len(p) != 2:
        raise GeometryError(f"{where}: a position is [lon, lat], got {p!r}")
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in p):
        raise GeometryError(f"{where}: not a finite number in {p!r}")
    lon, lat = float(p[0]), float(p[1])
    if not (-180 <= lon <= 180 and -90 <= lat <= 90):
        raise GeometryError(f"{where}: {p!r} is not lon/lat in degrees (EPSG:4326, lon first)")
    return lon, lat


def clean_ring(positions, where: str) -> list[tuple[float, float]]:
    """Distinct vertices of a closed GeoJSON ring, without the closing point."""
    if not isinstance(positions, (list, tuple)) or len(positions) < 4:
        raise GeometryError(f"{where}: a ring needs at least 4 positions (3 vertices and the closing one)")
    pts = [_position(p, where) for p in positions]
    if pts[0] != pts[-1]:
        raise GeometryError(f"{where}: ring not closed (first and last positions differ)")
    out: list[tuple[float, float]] = []
    for p in pts[:-1]:
        if not out or p != out[-1]:
            out.append(p)
    while len(out) > 1 and out[-1] == out[0]:
        out.pop()
    if len(out) < 3:
        raise GeometryError(f"{where}: fewer than 3 distinct vertices")
    if len(out) > MAX_VERTICES:
        raise GeometryError(f"{where}: {len(out)} vertices, at most {MAX_VERTICES}")
    return out


def parse_fields(doc) -> list[dict]:
    """``[{"field_id", "criterion", "ring": [(lon, lat), ...]}]`` from a FeatureCollection.

    Refuses anything beyond ``field_id`` and ``criterion`` in the properties (M2.1),
    MultiPolygons, holes, repeated ids and self-intersecting rings.
    """
    if not isinstance(doc, Mapping) or doc.get("type") != "FeatureCollection":
        raise GeometryError("expected a GeoJSON FeatureCollection")
    extra = sorted(set(doc) - set(ALLOWED_TOP))
    if extra:
        raise GeometryError(f"top-level members {extra} are not allowed (M2.1: only {list(ALLOWED_TOP)})")
    feats = doc.get("features")
    if not isinstance(feats, list) or not feats:
        raise GeometryError("no features")
    out, seen = [], set()
    for i, f in enumerate(feats):
        where = f"feature {i}"
        if not isinstance(f, Mapping) or f.get("type") != "Feature":
            raise GeometryError(f"{where}: not a Feature")
        extra = sorted(set(f) - set(ALLOWED_FEATURE))
        if extra:
            raise GeometryError(f"{where}: members {extra} are not allowed (M2.1)")
        props = f.get("properties")
        if not isinstance(props, Mapping):
            raise GeometryError(f"{where}: properties must be an object with field_id")
        extra = sorted(set(props) - set(ALLOWED_PROPERTIES))
        if extra:
            raise GeometryError(f"{where}: properties {extra} are not allowed (M2.1: only field_id and criterion)")
        fid = props.get("field_id")
        if not isinstance(fid, str) or not FIELD_ID.match(fid):
            raise GeometryError(f"{where}: field_id must match {FIELD_ID.pattern}, got {fid!r}")
        if fid in seen:
            raise GeometryError(f"{where}: field_id {fid!r} repeated")
        seen.add(fid)
        crit = props.get("criterion")
        if crit is not None and not isinstance(crit, str):
            raise GeometryError(f"{where}: criterion must be text or null")
        geom = f.get("geometry")
        if not isinstance(geom, Mapping) or geom.get("type") != "Polygon":
            raise GeometryError(f"{where} ({fid}): geometry must be a Polygon")
        rings = geom.get("coordinates")
        if not isinstance(rings, list) or len(rings) != 1:
            raise GeometryError(f"{where} ({fid}): exactly one ring, no holes")
        ring = clean_ring(rings[0], f"{where} ({fid})")
        errs = ring_errors(np.asarray(ring, dtype=np.float64))
        if errs:
            raise GeometryError(f"{where} ({fid}): {'; '.join(errs)}")
        out.append({"field_id": fid, "criterion": crit, "ring": ring})
    return out


# Planar ring checks ---------------------------------------------------------------------

def signed_area(xy: np.ndarray) -> float:
    x, y = xy[:, 0], xy[:, 1]
    return 0.5 * float(np.dot(x, np.roll(y, -1)) - np.dot(np.roll(x, -1), y))


def _orient(p, q, r) -> int:
    v = (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])
    return (v > 0) - (v < 0)


def _on_segment(p, q, r) -> bool:
    """q on segment pr, given the three are collinear."""
    return min(p[0], r[0]) <= q[0] <= max(p[0], r[0]) and min(p[1], r[1]) <= q[1] <= max(p[1], r[1])


def _segments_meet(p1, p2, q1, q2) -> bool:
    o1, o2, o3, o4 = _orient(p1, p2, q1), _orient(p1, p2, q2), _orient(q1, q2, p1), _orient(q1, q2, p2)
    if o1 != o2 and o3 != o4:
        return True
    return ((o1 == 0 and _on_segment(p1, q1, p2)) or (o2 == 0 and _on_segment(p1, q2, p2))
            or (o3 == 0 and _on_segment(q1, p1, q2)) or (o4 == 0 and _on_segment(q1, p2, q2)))


def ring_errors(xy: np.ndarray) -> list[str]:
    """Why a ring of distinct vertices (no closing point) is not a simple polygon. Pure."""
    n = len(xy)
    pts = [(float(p[0]), float(p[1])) for p in np.asarray(xy, dtype=np.float64)]
    errs = []
    if n < 3:
        return ["fewer than 3 vertices"]
    if signed_area(np.asarray(pts)) == 0:
        errs.append("zero area")
    for i in range(n):
        a, b, c = pts[i], pts[(i + 1) % n], pts[(i + 2) % n]
        # consecutive edges folding back over each other
        if _orient(a, b, c) == 0 and (b[0] - a[0]) * (c[0] - b[0]) + (b[1] - a[1]) * (c[1] - b[1]) < 0:
            errs.append(f"edges {i} and {(i + 1) % n} fold back over each other")
    for i in range(n):
        for j in range(i + 2, n):
            if i == 0 and j == n - 1:
                continue  # adjacent through the closing vertex
            if _segments_meet(pts[i], pts[(i + 1) % n], pts[j], pts[(j + 1) % n]):
                errs.append(f"edges {i} and {j} cross or touch (self-intersection)")
    return errs


# Grids ----------------------------------------------------------------------------------

def make_grid(transform, shape, crs_wkt: str) -> dict:
    a, b, c, d, e, f = (float(x) for x in tuple(transform)[:6])
    if b or d or a <= 0 or e >= 0:
        raise GeometryError("only north-up grids are supported")
    rows, cols = (int(v) for v in shape)
    if rows < 1 or cols < 1:
        raise GeometryError(f"empty grid {shape}")
    if not isinstance(crs_wkt, str) or not crs_wkt:
        raise GeometryError("the grid needs the CRS declared by the file")
    return {"transform": (a, b, c, d, e, f), "shape": (rows, cols), "crs_wkt": crs_wkt}


def grid_of(window: Mapping) -> dict:
    """Grid of a window read by ``hls.read()``."""
    return make_grid(window["transform"], np.shape(window["data"]), window["crs_wkt"])


def grid_from_georef(doc: Mapping) -> dict:
    """Grid of the basemap PNG, from ``basemap_<window id>.json``."""
    return make_grid(doc["transform"], (doc["height"], doc["width"]), doc["crs_wkt"])


def grid_key(grid: Mapping) -> tuple:
    return grid["crs_wkt"], tuple(grid["transform"]), tuple(grid["shape"])


def grid_bounds(grid: Mapping) -> tuple[float, float, float, float]:
    a, _, c, _, e, f = grid["transform"]
    rows, cols = grid["shape"]
    return c, f + rows * e, c + cols * a, f  # left, bottom, right, top


def project(ring_lonlat: Sequence[tuple[float, float]], crs_wkt: str) -> np.ndarray:
    """lon/lat vertices to (n, 2) metres in the grid CRS. Offline (PROJ inside rasterio)."""
    from rasterio.warp import transform

    lons, lats = zip(*ring_lonlat)
    xs, ys = transform("EPSG:4326", crs_wkt, list(lons), list(lats))
    xy = np.column_stack([np.asarray(xs, dtype=np.float64), np.asarray(ys, dtype=np.float64)])
    if not np.isfinite(xy).all():
        raise GeometryError("projection to the grid CRS failed")
    return xy


def to_pixels(xy: np.ndarray, grid: Mapping) -> list[list[float]]:
    """Grid metres to PNG pixel [col, row] from the top-left corner (basemap convention)."""
    a, _, c, _, e, f = grid["transform"]
    return [[round(float((x - c) / a), 3), round(float((y - f) / e), 3)] for x, y in np.asarray(xy, dtype=np.float64)]


# Inner pixels ----------------------------------------------------------------------------

def inside(px: np.ndarray, py: np.ndarray, xy: np.ndarray) -> np.ndarray:
    """Even-odd rule, vectorised over points."""
    x1, y1 = xy[:, 0], xy[:, 1]
    x2, y2 = np.roll(x1, -1), np.roll(y1, -1)
    inside = np.zeros(px.shape, dtype=bool)
    with np.errstate(divide="ignore", invalid="ignore"):
        for k in range(len(xy)):
            crosses = (y1[k] > py) != (y2[k] > py)
            x_at = x1[k] + (py - y1[k]) * (x2[k] - x1[k]) / (y2[k] - y1[k])
            inside ^= crosses & (px < x_at)
    return inside


def edge_distance(px: np.ndarray, py: np.ndarray, xy: np.ndarray) -> np.ndarray:
    """Distance from each point to the nearest edge of the ring."""
    x1, y1 = xy[:, 0], xy[:, 1]
    x2, y2 = np.roll(x1, -1), np.roll(y1, -1)
    d2 = np.full(px.shape, np.inf)
    for k in range(len(xy)):
        dx, dy = x2[k] - x1[k], y2[k] - y1[k]
        t = np.clip(((px - x1[k]) * dx + (py - y1[k]) * dy) / (dx * dx + dy * dy), 0.0, 1.0)
        d2 = np.minimum(d2, (px - x1[k] - t * dx) ** 2 + (py - y1[k] - t * dy) ** 2)
    return np.sqrt(d2)


def inner_mask(xy: np.ndarray, grid: Mapping, buffer_m: float) -> np.ndarray:
    """Pixels whose centre lies in the polygon and at least ``buffer_m`` from its boundary. Pure."""
    xy = np.asarray(xy, dtype=np.float64)
    a, _, c, _, e, f = grid["transform"]
    rows, cols = grid["shape"]
    mask = np.zeros((rows, cols), dtype=bool)
    xmin, ymin = xy.min(axis=0)
    xmax, ymax = xy.max(axis=0)
    col0 = max(0, math.floor((xmin - c) / a - 0.5))
    col1 = min(cols - 1, math.ceil((xmax - c) / a - 0.5))
    row0 = max(0, math.floor((ymax - f) / e - 0.5))
    row1 = min(rows - 1, math.ceil((ymin - f) / e - 0.5))
    if col0 > col1 or row0 > row1:
        return mask
    cc, rr = np.meshgrid(np.arange(col0, col1 + 1), np.arange(row0, row1 + 1))
    px, py = c + (cc + 0.5) * a, f + (rr + 0.5) * e
    keep = inside(px, py, xy) & (edge_distance(px, py, xy) >= buffer_m - EPS_M)
    mask[row0:row1 + 1, col0:col1 + 1] = keep
    return mask


def halves(mask: np.ndarray, grid: Mapping) -> tuple[np.ndarray, np.ndarray]:
    """Two equal-count halves of the inner pixels, split across their principal axis. Pure.

    The axis is the leading eigenvector of the pixel centres' scatter (sign fixed so its
    first non-zero component is positive). Pixels are ordered along it, then by row and
    column; the first ``n // 2`` are half "a", the rest half "b".
    """
    rr, cc = np.nonzero(mask)
    n = rr.size
    if n < 2:
        raise GeometryError("fewer than 2 inner pixels, no halves")
    a, _, c, _, e, f = grid["transform"]
    pts = np.column_stack([c + (cc + 0.5) * a, f + (rr + 0.5) * e])
    pts -= pts.mean(axis=0)
    _, vecs = np.linalg.eigh(pts.T @ pts)
    axis = vecs[:, -1]
    if axis[0] < 0 or (axis[0] == 0 and axis[1] < 0):
        axis = -axis
    order = np.lexsort((cc, rr, pts @ axis))
    out = []
    for part in (order[: n // 2], order[n // 2:]):
        m = np.zeros(mask.shape, dtype=bool)
        m[rr[part], cc[part]] = True
        out.append(m)
    return out[0], out[1]


# A field on a grid --------------------------------------------------------------------------

def field_on_grid(field: Mapping, grid: Mapping, rule: GeometryRule) -> dict:
    """Validate one parsed field on the grid. Returns the mask, counts, area and pixel ring."""
    fid = field["field_id"]
    xy = project(field["ring"], grid["crs_wkt"])
    errs = ring_errors(xy)
    if errs:
        raise GeometryError(f"{fid}: {'; '.join(errs)} (in the grid CRS)")
    left, bottom, right, top = grid_bounds(grid)
    if (xy[:, 0] < left).any() or (xy[:, 0] > right).any() or (xy[:, 1] < bottom).any() or (xy[:, 1] > top).any():
        raise GeometryError(f"{fid}: a vertex falls outside the HLS window")
    mask = inner_mask(xy, grid, rule.inner_buffer_m)
    n = int(mask.sum())
    if n < rule.min_clean_pixels:
        raise GeometryError(f"{fid}: {n} possible clean pixels after the {rule.inner_buffer_m:g} m inner buffer, "
                            f"the protocol needs at least {rule.min_clean_pixels}")
    return {"field_id": fid, "criterion": field.get("criterion"), "mask": mask, "n_possible": n,
            "area_ha": abs(signed_area(xy)) / 1e4, "polygon_px": to_pixels(xy, grid)}


class FieldMasks:
    """Inner-pixel masks of one field on each grid it meets, cached by grid.

    Called with a grid, returns ``{"whole": mask}``, plus ``"half_a"`` and ``"half_b"``
    when ``with_halves``. Raises GeometryError if the field fails on that grid.
    """

    def __init__(self, field: Mapping, rule: GeometryRule, with_halves: bool = False):
        self.field, self.rule, self.with_halves = field, rule, with_halves
        self._cache: dict[tuple, dict[str, np.ndarray]] = {}

    def __call__(self, grid: Mapping) -> dict[str, np.ndarray]:
        key = grid_key(grid)
        if key not in self._cache:
            whole = field_on_grid(self.field, grid, self.rule)["mask"]
            masks = {"whole": whole}
            if self.with_halves:
                masks["half_a"], masks["half_b"] = halves(whole, grid)
            self._cache[key] = masks
        return self._cache[key]


# CLI ---------------------------------------------------------------------------------------

def georef_path(config: Mapping) -> Path:
    return base.INTERIM_DIR / f"basemap_{config['sources']['hls']['window']['id']}.json"


def municipality_error(field: Mapping, mesh: Mapping | None) -> tuple[str | None, str | None]:
    """``(ibge code, error)`` of one field by the IBGE mesh rule (SA-D64), or ``(None, None)``
    without a mesh. The same rule as ``make decision-base``, so a polygon that crosses a
    municipal boundary is refused when it is drawn, not on the event day."""
    if mesh is None:
        return None, None
    from pipeline.adapters import ibge_malhas
    m = ibge_malhas.field_municipality(mesh, field["ring"])
    return m["ibge"], (None if m["ok"] else m["reason"])


def check_file(path: Path, georef: Mapping, rule: GeometryRule, window_id: str | None = None, *,
               area: AreaRule | None = None, rehearsal: bool = False, mesh: Mapping | None = None) -> dict:
    """Summary of every polygon of a file on the basemap grid (no masks). Pure apart from reading.

    ``area`` defaults to the repository protocol. A polygon outside the area of the mode is
    reported as refused, like a polygon that fails on the grid. With ``mesh`` (the IBGE
    municipal mesh, ``ibge_malhas.read``), a polygon that crosses a municipal boundary is
    refused too, and each ok field carries its ``municipio_ibge``.
    """
    if area is None:
        area = AreaRule.from_protocol(base.load_config())
    doc = read_geojson(path)
    drawn_on = doc.get("window_id") if isinstance(doc, Mapping) else None
    if window_id and drawn_on and drawn_on != window_id:
        raise GeometryError(f"{path}: drawn on window {drawn_on!r}, the config window is {window_id!r}")
    grid = grid_from_georef(georef)
    results = []
    for f in parse_fields(doc):
        try:
            where = area.error(f, rehearsal)
            if where:
                raise GeometryError(where)
            r = field_on_grid(f, grid, rule)
            code, merr = municipality_error(f, mesh)
            if merr:
                raise GeometryError(f"{merr} (IBGE mesh, SA-D64: draw it inside one municipality)")
            row = {"field_id": r["field_id"], "criterion": r["criterion"], "ok": True,
                   "area_ha": round(r["area_ha"], 3), "n_possible": r["n_possible"],
                   "polygon_px": r["polygon_px"]}
            if code is not None:
                row["municipio_ibge"] = code
            results.append(row)
        except GeometryError as exc:
            results.append({"field_id": f["field_id"], "criterion": f["criterion"], "ok": False, "error": str(exc)})
    return {"source": str(path), "window_id": window_id, "mode": "rehearsal" if rehearsal else "product",
            "product_lon_max": area.product_lon_max, "development_strip_lon_min": area.strip_lon_min,
            "inner_buffer_m": rule.inner_buffer_m, "municipality_checked": mesh is not None,
            "min_clean_pixels": rule.min_clean_pixels, "grid_crs_epsg": georef.get("crs_epsg"),
            "pixel_rule": "pixel centre in the polygon and at least inner_buffer_m from its boundary",
            "fields": results}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Validate field polygons on the basemap grid (plan 8.4 step 5).")
    ap.add_argument("geojson", type=Path, help="FeatureCollection in lon/lat, such as config/fields/candidates.geojson")
    ap.add_argument("--georef", type=Path, help="basemap georeference JSON (default: the config window's)")
    ap.add_argument("--rehearsal", action="store_true",
                    help="development strip only (protocol.field.development_strip), never a product field")
    args = ap.parse_args(argv)
    config = base.load_config()
    rule = GeometryRule.from_protocol(config)
    area = AreaRule.from_protocol(config)
    window_id = config["sources"]["hls"]["window"]["id"]
    georef = read_geojson(args.georef or georef_path(config))
    mesh = None
    try:
        from pipeline.adapters import ibge_malhas
        mesh = ibge_malhas.mesh_from_cache(config)
    except Exception as exc:  # the mesh is a check, never a reason to stop the grid summary
        print(f"municipality not checked (IBGE mesh unavailable: {exc}); make decision-base checks it", file=sys.stderr)
    summary = check_file(args.geojson, georef, rule, window_id, area=area, rehearsal=args.rehearsal, mesh=mesh)
    out = base.INTERIM_DIR / f"{args.geojson.stem}_geometry.json"
    base.write_json(out, summary)
    bad = 0
    for r in summary["fields"]:
        if r["ok"]:
            print(f"{r['field_id']}: ok, {r['area_ha']:.2f} ha, {r['n_possible']} possible clean pixels")
        else:
            bad += 1
            print(f"{r['field_id']}: REFUSED, {r['error']}")
    print(f"-> {out.relative_to(base.REPO_ROOT)}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())


# Old private names, kept so older callers keep working.
_inside = inside
_edge_distance = edge_distance

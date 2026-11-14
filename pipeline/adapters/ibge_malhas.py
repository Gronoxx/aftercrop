# Written with an SI tool (Claude, Anthropic) for NASA Space Apps 2026. See AI_USE.md.
"""IBGE municipal mesh: the municipality of a field, from its polygon (SA-D64).

Source: the IBGE mesh API v3 (servicodados.ibge.gov.br), state 41 (Parana) split by
municipality, one cached GeoJSON. Read as untrusted data: parsed as JSON only, every
coordinate checked to be a finite number, no property used except ``codarea``.

Rule (protocol.field.municipality): the polygon's area centroid must fall inside one
municipality, and the polygon must not cross that municipality's boundary (every vertex
inside, no edge crossing). Otherwise the field is refused, with the reason.

For the field window, ``window_record`` lists the municipalities that cover each part of
the window bbox, on a regular grid of points, and writes
``data/interim/municipalities_<window>.json``.

Pure geometry in lon/lat degrees (planar tests are enough at field scale). No shapely.

Run from the repository root:  python -m pipeline.adapters.ibge_malhas [--offline-only]
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from pipeline.adapters import base

SOURCE_ID = "ibge_malhas"
MAX_BYTES = 200_000_000          # refuse a mesh file above this size (untrusted input)
GRID_STEP_DEG = 0.005            # window grid: about 0.5 km


class MeshError(ValueError):
    """A mesh file or a polygon the rule cannot read."""


# Contract --------------------------------------------------------------------------

def spec(config: Mapping[str, Any] | None = None) -> dict:
    cfg = (config or base.load_config())["sources"]["ibge_malhas"]
    return {"source_id": cfg["source_id"], "url": cfg["state_mesh"]["url"],
            "params": dict(cfg["state_mesh"]["params"]), "filename": cfg["state_mesh"]["filename"],
            "license": cfg["license"]}


def path_of(s: Mapping[str, Any]) -> Path:
    return base.raw_path(s["source_id"], s["url"], s["params"], s["filename"])


def fetch(s: Mapping[str, Any]) -> Path:
    return base.cached_get(s["source_id"], s["url"], params=s["params"], filename=s["filename"], timeout=600)


def provenance(s: Mapping[str, Any]) -> dict:
    p = path_of(s)
    return base.base_provenance(p, s["license"], f"IBGE mesh API v3, retrieved {base.read_meta(p)['retrieved_at']}")


def _ring(raw: Any, where: str) -> list[tuple[float, float]]:
    if not isinstance(raw, list) or len(raw) < 4:
        raise MeshError(f"{where}: a ring needs at least 4 positions")
    out = []
    for p in raw:
        if not (isinstance(p, list) and len(p) >= 2 and all(isinstance(c, (int, float)) and not isinstance(c, bool)
                                                            and math.isfinite(c) for c in p[:2])):
            raise MeshError(f"{where}: a position is not two finite numbers")
        lon, lat = float(p[0]), float(p[1])
        if not (-180 <= lon <= 180 and -90 <= lat <= 90):
            raise MeshError(f"{where}: position out of range")
        out.append((lon, lat))
    if out[0] != out[-1]:
        out.append(out[0])
    return out


def _polygons(geom: Any, where: str) -> list[list[list[tuple[float, float]]]]:
    """[[outer, hole, ...], ...] from a Polygon or MultiPolygon."""
    if not isinstance(geom, Mapping):
        raise MeshError(f"{where}: no geometry")
    kind, coords = geom.get("type"), geom.get("coordinates")
    if kind == "Polygon":
        polys = [coords]
    elif kind == "MultiPolygon":
        polys = coords
    else:
        raise MeshError(f"{where}: geometry {kind!r}, expected Polygon or MultiPolygon")
    if not isinstance(polys, list) or not polys:
        raise MeshError(f"{where}: empty geometry")
    return [[_ring(r, where) for r in poly] for poly in polys]


def parse_mesh(doc: Any) -> dict[str, dict]:
    """``{codarea: {"polygons", "bbox"}}`` from a FeatureCollection. Pure."""
    if not isinstance(doc, Mapping) or doc.get("type") != "FeatureCollection" or not isinstance(doc.get("features"), list):
        raise MeshError("the mesh is not a FeatureCollection")
    out: dict[str, dict] = {}
    for i, f in enumerate(doc["features"]):
        code = (f.get("properties") or {}).get("codarea") if isinstance(f, Mapping) else None
        if not (isinstance(code, str) and code.isdigit() and len(code) == 7):
            raise MeshError(f"feature {i}: codarea is not a 7-digit IBGE code")
        if code in out:
            raise MeshError(f"feature {i}: codarea {code} twice")
        polys = _polygons(f.get("geometry"), f"feature {code}")
        pts = [p for poly in polys for p in poly[0]]
        out[code] = {"polygons": polys, "bbox": (min(p[0] for p in pts), min(p[1] for p in pts),
                                                 max(p[0] for p in pts), max(p[1] for p in pts))}
    if not out:
        raise MeshError("the mesh has no feature")
    return out


def read(path: Path) -> dict[str, dict]:
    """The cached mesh, parsed. No side effects."""
    size = Path(path).stat().st_size
    if size > MAX_BYTES:
        raise MeshError(f"{Path(path).name}: {size} bytes, above the {MAX_BYTES} limit")
    with open(path, encoding="utf-8") as f:
        return parse_mesh(json.load(f))


# Geometry (pure) ----------------------------------------------------------------------

def _in_ring(lon: float, lat: float, ring: Sequence[tuple[float, float]]) -> bool:
    inside = False
    for (x1, y1), (x2, y2) in zip(ring, ring[1:]):
        if (y1 > lat) != (y2 > lat):
            x = x1 + (lat - y1) * (x2 - x1) / (y2 - y1)
            if x > lon:
                inside = not inside
    return inside


def contains(entry: Mapping[str, Any], lon: float, lat: float) -> bool:
    a, b, c, d = entry["bbox"]
    if not (a <= lon <= c and b <= lat <= d):
        return False
    for poly in entry["polygons"]:
        if _in_ring(lon, lat, poly[0]) and not any(_in_ring(lon, lat, h) for h in poly[1:]):
            return True
    return False


def municipality_at(mesh: Mapping[str, dict], lon: float, lat: float) -> str | None:
    hits = [code for code, e in mesh.items() if contains(e, lon, lat)]
    if len(hits) > 1:
        raise MeshError(f"point {lon}, {lat} falls in {len(hits)} municipalities: {hits}")
    return hits[0] if hits else None


def area_centroid(ring: Sequence[Sequence[float]]) -> tuple[float, float]:
    """Area centroid (lon, lat) of a simple ring, closed or not. Pure."""
    pts = [(float(p[0]), float(p[1])) for p in ring]
    if pts[0] != pts[-1]:
        pts.append(pts[0])
    a = cx = cy = 0.0
    for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
        cross = x1 * y2 - x2 * y1
        a += cross
        cx += (x1 + x2) * cross
        cy += (y1 + y2) * cross
    if abs(a) < 1e-18:
        raise MeshError("the polygon has no area")
    return cx / (3 * a), cy / (3 * a)


def _orient(p, q, r) -> float:
    return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])


def _segments_cross(p1, p2, q1, q2) -> bool:
    """True when the two segments touch or cross (collinear overlap included)."""
    d1, d2 = _orient(q1, q2, p1), _orient(q1, q2, p2)
    d3, d4 = _orient(p1, p2, q1), _orient(p1, p2, q2)
    if ((d1 > 0) != (d2 > 0)) and ((d3 > 0) != (d4 > 0)) and d1 and d2 and d3 and d4:
        return True

    def on(p, q, r):
        return (min(p[0], q[0]) <= r[0] <= max(p[0], q[0]) and min(p[1], q[1]) <= r[1] <= max(p[1], q[1]))
    return ((d1 == 0 and on(q1, q2, p1)) or (d2 == 0 and on(q1, q2, p2))
            or (d3 == 0 and on(p1, p2, q1)) or (d4 == 0 and on(p1, p2, q2)))


def crosses_boundary(entry: Mapping[str, Any], ring: Sequence[tuple[float, float]]) -> bool:
    """True when an edge of ``ring`` touches or crosses any ring of the municipality."""
    xs, ys = [p[0] for p in ring], [p[1] for p in ring]
    box = (min(xs), min(ys), max(xs), max(ys))
    edges = list(zip(ring, ring[1:]))
    for poly in entry["polygons"]:
        for r in poly:
            for a, b in zip(r, r[1:]):
                if max(a[0], b[0]) < box[0] or min(a[0], b[0]) > box[2] or max(a[1], b[1]) < box[1] \
                        or min(a[1], b[1]) > box[3]:
                    continue
                if any(_segments_cross(p, q, a, b) for p, q in edges):
                    return True
    return False


def field_municipality(mesh: Mapping[str, dict], ring: Sequence[Sequence[float]]) -> dict:
    """``{"ok", "ibge", "centroid", "reason"}`` for one field polygon (outer ring, lon/lat).

    ok: the area centroid falls in one municipality, every vertex falls in it too, and no
    edge touches its boundary. Otherwise ok is False and ``reason`` says why.
    """
    pts = [(float(p[0]), float(p[1])) for p in ring]
    if pts[0] != pts[-1]:
        pts.append(pts[0])
    if len(pts) < 4:
        return {"ok": False, "ibge": None, "centroid": None, "reason": "a polygon needs at least 3 vertices"}
    try:
        c = area_centroid(pts)
    except MeshError as exc:
        return {"ok": False, "ibge": None, "centroid": None, "reason": str(exc)}
    code = municipality_at(mesh, *c)
    cen = [round(c[0], 6), round(c[1], 6)]
    if code is None:
        return {"ok": False, "ibge": None, "centroid": cen, "reason": "the centroid falls in no municipality of the mesh"}
    e = mesh[code]
    outside = [p for p in pts[:-1] if not contains(e, *p)]
    if outside or crosses_boundary(e, pts):
        return {"ok": False, "ibge": code, "centroid": cen,
                "reason": f"the polygon crosses the boundary of municipality {code}"}
    return {"ok": True, "ibge": code, "centroid": cen, "reason": None}


def window_cover(mesh: Mapping[str, dict], bbox: Sequence[float], step: float = GRID_STEP_DEG,
                 lon_splits: Mapping[str, Sequence[float | None]] | None = None) -> dict:
    """Municipalities over the window bbox, by a regular grid of points. Pure.

    ``lon_splits``: named longitude bands ``{name: [lon_min or None, lon_max or None]}``,
    such as the product area and the development strip, counted apart.
    """
    lon0, lat0, lon1, lat1 = (float(x) for x in bbox)
    nx, ny = int(round((lon1 - lon0) / step)) + 1, int(round((lat1 - lat0) / step)) + 1
    bands = {"window": [None, None], **(lon_splits or {})}
    counts: dict[str, dict[str, int]] = {b: {} for b in bands}
    totals = {b: 0 for b in bands}
    for i in range(nx):
        lon = lon0 + i * (lon1 - lon0) / (nx - 1)
        for j in range(ny):
            lat = lat0 + j * (lat1 - lat0) / (ny - 1)
            code = municipality_at(mesh, lon, lat) or "none"
            for b, (lo, hi) in bands.items():
                if (lo is None or lon >= lo) and (hi is None or lon <= hi):
                    counts[b][code] = counts[b].get(code, 0) + 1
                    totals[b] += 1
    return {"grid": {"nx": nx, "ny": ny, "step_deg": step},
            "bands": {b: {"lon": bands[b], "points": totals[b],
                          "municipalities": [{"ibge": k, "points": v, "share": round(v / totals[b], 4)}
                                             for k, v in sorted(counts[b].items(), key=lambda kv: -kv[1])]}
                      for b in bands if totals[b]}}


# Run ------------------------------------------------------------------------------------

def mesh_from_cache(config: Mapping[str, Any] | None = None) -> dict[str, dict]:
    s = spec(config)
    return read(fetch(s))


def window_record(config: Mapping[str, Any] | None = None) -> dict:
    config = config or base.load_config()
    s = spec(config)
    mesh = read(fetch(s))
    w = config["sources"]["hls"]["window"]
    fld = config["protocol"]["field"]
    splits = {"product_area": [None, float(fld["product_area"]["lon_max"])],
              "development_strip": [float(fld["development_strip"]["lon_min"]), None]}
    cover = window_cover(mesh, w["bbox"], lon_splits=splits)
    return {"window_id": w["id"], "bbox": list(w["bbox"]), "rule": config["protocol"]["field"]["municipality"],
            **cover, "provenance": provenance(s)}


def output_path(window_id: str) -> Path:
    return base.INTERIM_DIR / f"municipalities_{window_id}.json"


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.parse_args(argv)
    rec = window_record()
    zcsv = base.INTERIM_DIR / "zarc_norte_pr.csv"
    if zcsv.is_file():   # names as the decoded Tábua writes them, for the reader of the record
        import pandas as pd
        z = pd.read_csv(zcsv, usecols=["geocodigo", "municipio"]).drop_duplicates()
        names = {str(int(g)): m for g, m in zip(z["geocodigo"], z["municipio"])}
        for b in rec["bands"].values():
            for m in b["municipalities"]:
                m["name_in_zarc"] = names.get(m["ibge"])
    out = base.write_json(output_path(rec["window_id"]), rec)
    for band, b in rec["bands"].items():
        print(band, ", ".join(f"{m['ibge']} {m['share']:.0%}" for m in b["municipalities"]))
    print(f"-> {out.relative_to(base.REPO_ROOT)}  network_calls={base.network_calls}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

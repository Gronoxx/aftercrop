# Written with an SI tool (Claude, Anthropic) for NASA Space Apps 2026. See AI_USE.md.
"""Field soil profile from SoilGrids 2.0, and the soil-domain rule (plan 8.4 step 10, 9.1, 9.5, 9.6, 13.7, sheet M2).

What SoilGrids may do (plan 9.1, 9.6, 13.7):

- give the engine its texture: six layers, area-weighted over the field;
- give the estimated class of the fixed soil line ("Soil used: {class}, estimated");
- nothing else. It never decides the soil domain, never unlocks a seal and
  never separates neighbouring fields. Only data the user declares excludes
  (``protocol.soil_domain.decides: user_declared_only``).

Soil domain (13.7, ``protocol.soil_domain``), from declared clay and tillage only:

- clay below 350 g/kg, or conventional tillage: "Not enough evidence";
- clay from 350 to 600 g/kg, both ends included: "Use with care";
- clay above 600 g/kg: inside the trial domain.

Without a declared clay or tillage there is no domain state, and the warning of
5.1 stays (copy ``page.answer.transfer_warning``). The band the SoilGrids clay would
fall in is reported beside it as an estimate, with ``decides: false``. The
counts of 14.5 (steps of 1 sc/ha) are never touched here.

Profile: SoilGrids depths 0-5, 5-15, 15-30, 30-60, 60-100 and 100-200 cm. The
last layer is extended to ``protocol.engine.profile_min_depth_m`` (2.4 m) with
the same values (8.4 step 10, M2 step 6).

Area mean (9.5): the 250 m pixels of the WCS answer (service CRS, Homolosine,
equal area) are weighted by the share of each pixel inside the polygon, from a
10 x 10 sub-pixel raster of it. Pixels without data drop out; the weight with
data is reported. The polygon is projected with ``soilgrids.igh_xy`` vertex by
vertex (field edges are short, so straight edges stay straight enough).

Units (WCS mapped values): clay, sand g/kg (/10 -> %), soc dg/kg (/10 -> g/kg),
bdod cg/cm3 (/100 -> kg/dm3), cec mmol(c)/kg (/10 -> cmol(c)/kg). Silt is
100 - clay - sand. Organic matter = 1.724 x SOC (van Bemmelen factor), only for
the ``om_pct`` field M5 reads.

Zarc soil class: the 2026/27 Tábua indexes soil by the six available-water classes AD1
to AD6 (IN SPA/MAPA 1/2022, a pedotransfer function of sand, silt and clay from 0 to
0.4 m), not by the three textural Tipos of IN 2/2008 (Tipo 3 = clay of 35% or more), and
MAPA states there is no analogy between the two (source check notes of the project, outside
this repository, item 3). The class comes from a soil report only (``protocol.zarc.field_ad_class``, SA-D64):
a declared class AD1 to AD6, or (not yet implemented) sand, silt and clay of 0 to 0.4 m by
the IN MAPA 1/2022 formula. SoilGrids never gives a class. Without a report
``zarc_ad_class`` is null and ``zarc_ad_status`` says "unknown": M5 then states
"soil_class_unknown" for the field's Tábua rows (never a silent "not_found") and reads the
window warning on the envelope of every class ("for any soil class").
The IN 2/2008 Tipo of the estimated clay is kept beside it as history, never used to
pick a Tábua row.

Output: ``data/interim/field_<id>_soil.json`` with the keys M5 reads.

Run from the repository root (SoilGrids WCS through ``base.cached_get()``, then offline):
    python -m pipeline.fields.soil_profile config/fields/fields.geojson
"""

from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

import numpy as np

from pipeline.adapters import base, soilgrids
from pipeline.fields import geometry

SOURCE_ID = "soilgrids_wcs"          # sources.soilgrids.source_id of the config and of the manifest
DEPTHS = ("0-5cm", "5-15cm", "15-30cm", "30-60cm", "60-100cm", "100-200cm")
LAYERS_M = ((0.0, 0.05), (0.05, 0.15), (0.15, 0.3), (0.3, 0.6), (0.6, 1.0), (1.0, 2.0))
MEAN_PROPERTIES = ("clay", "sand", "soc", "bdod", "cec")
QUANTILES = ("Q0.05", "Q0.95")       # clay only: the clay_q05_q95 pair M5 reads
CONVERSION = {**soilgrids.CONVERSION, "bdod": (100.0, "kg/dm3"), "cec": (10.0, "cmol(c)/kg")}
VAN_BEMMELEN = 1.724
SUPERSAMPLE = 10
TOPSOIL_M = 0.3                      # depth of the clay used for the estimated class and band
STATES = {"not_enough_evidence": "Not enough evidence", "use_with_care": "Use with care",
          "inside": "Inside the trial domain"}
COPY_KEYS = {"transfer_warning": "page.answer.transfer_warning", "use_with_care": "page.answer.use_with_care",
             "use_with_care_note": "page.answer.use_with_care_note",
             "out_of_domain": "page.answer.soil_out_of_domain", "soil_line": "page.rows.soil.line"}
# SiBCS 2018 textural groups (g/kg): arenosa = classes areia and areia franca of the
# texture triangle (silt + 2 clay < 300); siltosa = clay < 350 and sand < 150.
SIBCS_SANDY_SILT_PLUS_2CLAY_G_KG = 300
SIBCS_SILTY_SAND_BELOW_G_KG = 150
_FIELD_ID = geometry.FIELD_ID        # one field id rule, the one of geometry.py
# English names of the SiBCS groups, for the soil line "Soil used: {class}, estimated" (7.2).
CLASS_NAMES_EN = {"very_clayey": "very clayey", "clayey": "clayey", "sandy": "sandy", "silty": "silty",
                  "medium": "medium texture"}
# IN SPA/MAPA 2/2008 soil types (clay %, lower edge inclusive), history only (conferencia item 3.2).
ZARC_TIPOS_2008 = (("Tipo 1", 10, 15), ("Tipo 2", 15, 35), ("Tipo 3", 35, 101))
ZARC_AD_STATUSES = ("unknown", "declared")   # SA-D64: from a soil report only
ZARC_AD_CLASSES = ("AD1", "AD2", "AD3", "AD4", "AD5", "AD6")


class SoilError(ValueError):
    pass


# Soil domain (13.7) ----------------------------------------------------------------

def _rule(config: dict | None = None) -> dict:
    return (config or base.load_config())["protocol"]["soil_domain"]


def band(clay_g_kg: float, rule: dict) -> str:
    """Band of a clay content: not_enough_evidence, use_with_care or inside. Pure."""
    if isinstance(clay_g_kg, bool) or not isinstance(clay_g_kg, (int, float)) or not math.isfinite(clay_g_kg):
        raise SoilError(f"clay must be a number in g/kg, got {clay_g_kg!r}")
    if not 0 <= clay_g_kg <= 1000:
        raise SoilError(f"clay {clay_g_kg} g/kg is outside 0 to 1000")
    low, high = rule["use_with_care_clay_g_kg"]
    if low != rule["not_enough_evidence_below_clay_g_kg"] or high != rule["inside_above_clay_g_kg"]:
        raise SoilError("protocol.soil_domain bands do not join")
    if clay_g_kg < low:
        return "not_enough_evidence"
    if clay_g_kg <= high:
        return "use_with_care"
    return "inside"


def soil_domain(declared: dict | None, rule: dict) -> dict:
    """Domain state from what the user declared. ``declared``: {clay_g_kg, no_till}, either may be missing.

    Conventional tillage (``no_till`` False) gives "Not enough evidence" whatever the clay.
    Nothing declared gives no state: SoilGrids never fills in.
    """
    if rule.get("decides") != "user_declared_only":
        raise SoilError("protocol.soil_domain.decides must be user_declared_only")
    declared = declared or {}
    clay, no_till = declared.get("clay_g_kg"), declared.get("no_till")
    if no_till not in (None, True, False):
        raise SoilError(f"no_till must be true, false or missing, got {no_till!r}")
    if no_till is False:
        return {"state": "not_enough_evidence", "label": rule["conventional_tillage"], "decided_by": "user_declared",
                "reason": "conventional tillage", "copy_key": COPY_KEYS["out_of_domain"]}
    if clay is None:
        return {"state": None, "label": None, "decided_by": None,
                "reason": "no declared clay. SoilGrids never decides", "copy_key": COPY_KEYS["transfer_warning"]}
    b = band(clay, rule)
    key = {"not_enough_evidence": "out_of_domain", "use_with_care": "use_with_care_note", "inside": None}[b]
    return {"state": b, "label": STATES[b], "decided_by": "user_declared", "reason": f"declared clay {clay:g} g/kg",
            "copy_key": COPY_KEYS[key] if key else None}


def estimated_band(clay_g_kg: float | None, rule: dict) -> dict:
    """The band the SoilGrids clay would fall in. Shown as an estimate, never a state."""
    return {"band": None if clay_g_kg is None else band(round(clay_g_kg, 1), rule),
            "clay_g_kg": clay_g_kg, "depth_m": [0.0, TOPSOIL_M], "decides": False,
            "note": "SoilGrids estimate. It does not change the answer, unlock a seal or separate fields"}


def textural_group(clay_g_kg: float, sand_g_kg: float, rule: dict) -> dict:
    """SiBCS 2018 textural group of a clay and sand content. The 350 and 600 come from protocol. Pure."""
    low, high = rule["use_with_care_clay_g_kg"]
    silt = 1000 - clay_g_kg - sand_g_kg
    if clay_g_kg > high:
        code, pt = "very_clayey", "muito argilosa"
    elif clay_g_kg >= low:
        code, pt = "clayey", "argilosa"
    elif silt + 2 * clay_g_kg < SIBCS_SANDY_SILT_PLUS_2CLAY_G_KG:
        code, pt = "sandy", "arenosa"
    elif sand_g_kg < SIBCS_SILTY_SAND_BELOW_G_KG:
        code, pt = "silty", "siltosa"
    else:
        code, pt = "medium", "média"
    return {"code": code, "name_en": CLASS_NAMES_EN[code], "name_pt": pt, "source": rule.get("source", "SiBCS 2018"),
            "estimated": True}


def zarc_tipo_2008(clay_pct: float | None) -> str | None:
    """IN 2/2008 Tipo of a clay content in %, or None below 10%. History: the current Tábua uses AD1 to AD6."""
    if clay_pct is None:
        return None
    return next((t for t, lo, hi in ZARC_TIPOS_2008 if lo <= clay_pct < hi), None)


def report_ad_class(report: dict | None) -> str | None:
    """The Zarc class of a soil report (SA-D64), or None without one. Pure.

    ``report``: ``{"ad_class": "AD1".."AD6"}``, a class the user declares from the report.
    Texture of 0 to 0.4 m by the IN MAPA 1/2022 formula is planned, not implemented: a report
    with texture only is refused, never guessed. SoilGrids is never a report.
    """
    if not report:
        return None
    if not isinstance(report, dict):
        raise SoilError("a soil report is an object such as {'ad_class': 'AD3'}")
    if report.get("source_id") == SOURCE_ID:
        raise SoilError("SoilGrids is not a soil report: it never gives a Zarc class (SA-D64)")
    cls = report.get("ad_class")
    if cls is None:
        if any(k in report for k in ("sand_pct", "silt_pct", "clay_pct")):
            raise SoilError("texture to Zarc class by the IN MAPA 1/2022 formula is not implemented yet: "
                            "declare the class AD1 to AD6 printed on the report")
        raise SoilError("the soil report gives no Zarc class")
    if cls not in ZARC_AD_CLASSES:
        raise SoilError(f"Zarc class {cls!r}: AD1 to AD6 only")
    return cls


def zarc_soil(clay_top_pct: float | None, report: dict | None = None) -> dict:
    """The Zarc soil class of the field: from a soil report only, else unknown (module notes). Pure.

    ``clay_top_pct`` (SoilGrids) only fills the IN 2/2008 history line: it never picks a class.
    """
    cls = report_ad_class(report)
    return {
        "zarc_ad_class": cls,
        "zarc_ad_status": "unknown" if cls is None else "declared",
        "zarc_ad_note": ("The Tábua uses available-water classes AD1 to AD6 (IN SPA/MAPA 1/2022). "
                         "The class comes from a soil report only, never from SoilGrids. "
                         + ("Without a report the class is unknown: the window warning holds for any soil class."
                            if cls is None else "Declared from the soil report.")),
        "zarc_tipo_2008": zarc_tipo_2008(clay_top_pct),
        "zarc_tipo_2008_note": "IN 2/2008 type of the estimated clay, history only. It never picks a Tábua row.",
    }


# WCS requests for a polygon ----------------------------------------------------------

def coverages() -> list[str]:
    out = [f"{p}_{d}_mean" for p in MEAN_PROPERTIES for d in DEPTHS]
    return out + [f"clay_{d}_{q}" for d in DEPTHS for q in QUANTILES]


def igh_polygon(geometry: dict) -> dict:
    """GeoJSON Polygon or MultiPolygon in lon/lat -> the same in the service CRS (metres). Pure."""
    def ring(r):
        return [list(soilgrids.igh_xy(float(lat), float(lon))) for lon, lat in r]

    if geometry.get("type") == "Polygon":
        return {"type": "Polygon", "coordinates": [ring(r) for r in geometry["coordinates"]]}
    if geometry.get("type") == "MultiPolygon":
        return {"type": "MultiPolygon", "coordinates": [[ring(r) for r in p] for p in geometry["coordinates"]]}
    raise SoilError(f"expected a Polygon or MultiPolygon, got {geometry.get('type')!r}")


def _points(geom: dict):
    polys = geom["coordinates"] if geom["type"] == "MultiPolygon" else [geom["coordinates"]]
    return [pt for p in polys for r in p for pt in r]


def igh_box(geometry: dict, margin_px: int = 1) -> tuple[float, float, float, float]:
    """(x0, y0, x1, y1) of the polygon in the service CRS, plus ``margin_px`` native pixels."""
    pts = _points(igh_polygon(geometry))
    m = margin_px * soilgrids.NATIVE_PIXEL_M
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    return min(xs) - m, min(ys) - m, max(xs) + m, max(ys) + m


def request(url: str, coverage: str, box) -> tuple[str, dict, str]:
    """GetCoverage in the service CRS (native 250 m pixels), as the native form of soilgrids.py."""
    x0, y0, x1, y1 = box
    params = {"map": f"/map/{coverage.split('_')[0]}.map", "SERVICE": "WCS", "VERSION": "2.0.1",
              "REQUEST": "GetCoverage", "COVERAGEID": coverage, "FORMAT": "image/tiff",
              "SUBSET": [f"x({x0:.1f},{x1:.1f})", f"y({y0:.1f},{y1:.1f})"]}
    return url, params, f"field_{coverage}_{base.cache_key(url, params)}.tif"


def fetch_layers(geometry: dict, config: dict | None = None) -> dict[str, Path]:
    """Every coverage of the profile for the polygon box, through ``base.cached_get()``."""
    config = config or base.load_config()
    cfg = config["sources"]["soilgrids"]
    box = igh_box(geometry)
    out = {}
    for cov in coverages():
        url, params, name = request(cfg["url"], cov, box)
        out[cov] = base.cached_get(cfg["source_id"], url, params=params, filename=name, timeout=180)
    return out


# Area mean -------------------------------------------------------------------------

def grid_of(path: Path) -> tuple[np.ndarray, tuple[float, ...]]:
    """Raw values (NaN at -32768) and the (a, b, c, d, e, f) transform of a WCS GeoTIFF."""
    tags = soilgrids.geotags(path)
    sx, sy = tags["pixel_scale"][:2]
    i, j, _, x, y, _ = tags["tiepoint"][:6]
    return soilgrids.read(path), (sx, 0.0, x - i * sx, 0.0, -sy, y + j * sy)


def pixel_weights(geometry_igh: dict, transform, shape, k: int = SUPERSAMPLE) -> np.ndarray:
    """Share of each pixel inside the polygon, from a k x k sub-pixel raster. Pure."""
    from rasterio.features import rasterize

    a, _, c, _, e, f = transform
    fine = rasterize([(geometry_igh, 1)], out_shape=(shape[0] * k, shape[1] * k),
                     transform=(a / k, 0.0, c, 0.0, e / k, f), fill=0, all_touched=False, dtype="uint8")
    return fine.reshape(shape[0], k, shape[1], k).sum(axis=(1, 3)) / (k * k)


def area_mean(values: np.ndarray, weights: np.ndarray) -> tuple[float | None, float, float]:
    """(weighted mean, weight with data, total weight). Pixels without data drop out. Pure."""
    v, w = np.asarray(values, dtype=np.float64), np.asarray(weights, dtype=np.float64)
    if v.shape != w.shape:
        raise SoilError(f"values {v.shape} and weights {w.shape} differ")
    ok = np.isfinite(v) & (w > 0)
    total, wd = float(w.sum()), float(w[ok].sum())
    if total <= 0:
        raise SoilError("the polygon covers no SoilGrids pixel")
    return (float((v[ok] * w[ok]).sum() / wd) if wd > 0 else None), wd, total


def convert(prop: str, raw: float | None) -> float | None:
    if raw is None:
        return None
    div, _ = CONVERSION[prop]
    return raw / div


def layer_means(paths: dict[str, Path], geometry: dict) -> dict[str, dict]:
    """coverage -> {raw: area mean of the mapped value, data_share}. Reads only cached files."""
    geom = igh_polygon(geometry)
    out, cache = {}, {}
    for cov, path in paths.items():
        vals, t = grid_of(path)
        key = (t, vals.shape)
        if key not in cache:
            cache[key] = pixel_weights(geom, t, vals.shape)
        mean, wd, total = area_mean(vals, cache[key])
        out[cov] = {"raw": mean, "data_share": round(wd / total, 4), "n_px": int((cache[key] > 0).sum())}
    return out


# Profile ---------------------------------------------------------------------------

def profile(means: dict[str, dict], min_depth_m: float) -> list[dict]:
    """Six layers in plan units, the last extended to ``min_depth_m``. Pure."""
    layers = []
    for d, (top, bottom) in zip(DEPTHS, LAYERS_M):
        raw = {p: means[f"{p}_{d}_mean"]["raw"] for p in MEAN_PROPERTIES}
        if raw["clay"] is None or raw["sand"] is None:
            raise SoilError(f"no clay or sand for {d}: no SoilGrids data under the polygon")
        clay, sand = convert("clay", raw["clay"]), convert("sand", raw["sand"])
        soc = convert("soc", raw["soc"])
        q = [means.get(f"clay_{d}_{x}", {}).get("raw") for x in QUANTILES]
        layers.append({
            "depth": d, "top_m": top, "bottom_m": bottom,
            "clay_pct": round(clay, 1), "sand_pct": round(sand, 1), "silt_pct": round(100 - clay - sand, 1),
            "clay_g_kg": round(raw["clay"], 1),
            "clay_g_kg_q05_q95": [None if x is None else round(x, 1) for x in q],
            "soc_g_kg": None if soc is None else round(soc, 2),
            "om_pct": None if soc is None else round(soc * VAN_BEMMELEN / 10, 2),
            "bdod_kg_dm3": None if raw["bdod"] is None else round(convert("bdod", raw["bdod"]), 3),
            "cec_cmol_kg": None if raw["cec"] is None else round(convert("cec", raw["cec"]), 2),
            "data_share": min(means[f"{p}_{d}_mean"]["data_share"] for p in MEAN_PROPERTIES),
        })
    if min_depth_m > layers[-1]["bottom_m"]:
        layers[-1] = {**layers[-1], "bottom_m": float(min_depth_m), "extended_from_m": layers[-1]["bottom_m"]}
    return layers


def topsoil(layers: list[dict], key: str, depth_m: float = TOPSOIL_M) -> float | None:
    """Thickness-weighted mean of ``key`` from the surface to ``depth_m``. Pure."""
    acc = w = 0.0
    for lay in layers:
        top, bottom = lay["top_m"], min(lay["bottom_m"], depth_m)
        if bottom <= top:
            continue
        if lay[key] is None:
            return None
        acc, w = acc + lay[key] * (bottom - top), w + (bottom - top)
    return acc / w if w else None


def build(field_id: str, means: dict[str, dict], declared: dict | None = None, config: dict | None = None) -> dict:
    """Soil record of one field with the keys M5 reads. ``means`` from ``layer_means``."""
    if not _FIELD_ID.match(field_id):
        raise SoilError(f"unsafe field id: {field_id!r}")
    config = config or base.load_config()
    rule = config["protocol"]["soil_domain"]
    layers = profile(means, float(config["protocol"]["engine"]["profile_min_depth_m"]))
    clay_top = topsoil(layers, "clay_g_kg")
    sand_top = topsoil(layers, "sand_pct")
    q05 = [lay["clay_g_kg_q05_q95"][0] for lay in layers]
    q95 = [lay["clay_g_kg_q05_q95"][1] for lay in layers]
    top = [lay for lay in layers if lay["top_m"] < TOPSOIL_M]
    qpair = None
    if all(x is not None for x in q05 + q95):
        th = [min(lay["bottom_m"], TOPSOIL_M) - lay["top_m"] for lay in top]
        qpair = [round(sum(lay["clay_g_kg_q05_q95"][i] * t for lay, t in zip(top, th)) / sum(th) / 10, 1)
                 for i in (0, 1)]
    group = None if clay_top is None else textural_group(clay_top, sand_top * 10, rule)
    return {
        "field_id": field_id, "source_id": SOURCE_ID,
        "source": "SoilGrids 2.0 (ISRIC, WCS), area-weighted mean over the field, estimate",
        "estimate": True, "decides": False,
        "layers": layers,
        "clay_q05_q95": qpair,
        "clay_q05_q95_note": "% clay, 0 to 0.3 m, thickness-weighted layer quantiles (not a quantile of the mean)",
        # The page fills "Soil used: {class}" with a string; the group itself stays beside it.
        "estimated_class": None if group is None else group["name_en"],
        "estimated_class_detail": group,
        "estimated_band": estimated_band(None if clay_top is None else round(clay_top, 1), rule),
        "domain": soil_domain(declared, rule),
        # SA-D64: a Zarc class only from a soil report the user declares ({"zarc_ad_class": "AD3"}).
        **zarc_soil(None if clay_top is None else clay_top / 10,
                    {"ad_class": declared["zarc_ad_class"]} if (declared or {}).get("zarc_ad_class") else None),
        "transfer_warning": COPY_KEYS["transfer_warning"],
        "lab_report_request": COPY_KEYS["soil_line"],
        "copy_keys_note": "transfer_warning and lab_report_request are copy keys of config/copy_<lang>.yaml",
        "ledger": f"field_{field_id}.soil",
    }


def output_path(field_id: str) -> Path:
    if not _FIELD_ID.match(field_id):
        raise SoilError(f"unsafe field id: {field_id!r}")
    return base.INTERIM_DIR / f"field_{field_id}_soil.json"


def write(record: dict) -> Path:
    return base.write_json(output_path(record["field_id"]), record)


# CLI ---------------------------------------------------------------------------------------

def field_geometry(field: dict) -> dict:
    """GeoJSON Polygon in lon/lat of a field parsed by ``geometry.parse_fields`` (ring closed again). Pure."""
    ring = [list(p) for p in field["ring"]]
    return {"type": "Polygon", "coordinates": [ring + [ring[0]]]}


def run(geojson: Path, config: dict | None = None) -> list[Path]:
    """Soil record of every field of a FeatureCollection: WCS (cached), area means, profile, write."""
    config = config or base.load_config()
    out = []
    for f in geometry.parse_fields(geometry.read_geojson(geojson)):
        geom = field_geometry(f)
        paths = fetch_layers(geom, config)
        out.append(write(build(f["field_id"], layer_means(paths, geom), config=config)))
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Soil profile of each field from SoilGrids 2.0 (plan 8.4 step 10).")
    ap.add_argument("geojson", type=Path, help="fields FeatureCollection, such as config/fields/fields.geojson")
    args = ap.parse_args(argv)
    for p in run(args.geojson):
        print(f"-> {p.relative_to(base.REPO_ROOT) if base.REPO_ROOT in p.parents else p}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

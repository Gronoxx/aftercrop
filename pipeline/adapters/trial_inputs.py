# Written with an SI tool (Claude, Anthropic) for NASA Space Apps 2026. See AI_USE.md.
"""Engine inputs of the trial sites that no adapter writes (PROTOCOL.md 5 and 6, SA-D65).

    .venv/bin/python -m pipeline.adapters.trial_inputs soil [--sites londrina,floresta,cafelandia]
    .venv/bin/python -m pipeline.adapters.trial_inputs power-printed

``soil`` writes ``data/interim/trial_<site>_soil.json``, the soil record that
``pipeline.engine.cli`` reads (``layers`` with top_m, bottom_m, sand_pct,
clay_pct, om_pct), with the source recorded field by field. Rule (SA-D65, amended
at execution level, open to the participant's veto): the three trial
soils are Latossolos with a homogeneous B horizon, so the texture each Doc prints
for the trial area is extended to the whole profile. Floresta (Doc 398) prints only
the class "muito argiloso": clay at the class lower bound, 600 g/kg, labelled as
such, and the other 400 g/kg split between sand and silt in the SoilGrids sand:silt
ratio at the point (recorded). The Doc values come from the method sections
(``DOC_SOIL`` below, with pages). Organic matter: the Doc's in its printed layer
only (organic matter as printed, or 1.724 x organic carbon, van Bemmelen), 1.724 x
the SoilGrids SOC below. The last SoilGrids depth (100 to 200 cm) runs to
``protocol.engine.profile_min_depth_m``. ``trial_<site>_soil_soilgrids.json`` keeps
the SoilGrids texture at the point in every layer, a sensitivity side file only.

SoilGrids is read at the trial point: the native 250 m pixel (service CRS) that
holds the point, through the WCS (``pipeline.adapters.soilgrids``) and
``base.cached_get()``. The point is ``trial_sites`` of the config, the protocol
coordinate. A Doc texture class without numbers (Floresta, "muito argiloso") is
turned into its lower bound, labelled; the SoilGrids clay is kept beside as a check.

``power-printed`` downloads the NASA POWER daily met call at the coordinate Doc
398 prints for Floresta (``protocol.side_runs.floresta_printed_coordinate``,
D2), as ``data/raw/power_daily/floresta_printed_met_<start>_<end>_LST.json``,
for the side run. Download only: nothing is simulated here.

Nothing here reads a trial value or runs the engine.
"""
from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path
from typing import Any, Mapping

from pipeline.adapters import base, soilgrids

SOURCE_ID = "soilgrids_wcs"
DEPTHS = ("0-5cm", "5-15cm", "15-30cm", "30-60cm", "60-100cm", "100-200cm")
LAYERS_M = ((0.0, 0.05), (0.05, 0.15), (0.15, 0.3), (0.3, 0.6), (0.6, 1.0), (1.0, 2.0))
PROPERTIES = ("clay", "sand", "soc")
VAN_BEMMELEN = 1.724
VERY_CLAYEY_MIN_CLAY_PCT = 60.0   # SiBCS 2018: "muito argilosa" is clay above 600 g/kg
PRINTED_SITE = "floresta_printed"

# What each Doc prints about the soil of the trial area (method sections only).
DOC_SOIL: dict[str, dict[str, Any]] = {
    "londrina": {
        "doc": "Embrapa Doc 476", "page": "Material e métodos, PDF p. 27 (printed p. 26)",
        "class": "Latossolo Vermelho eutroférrico, textura muito argilosa",
        "texture": {"top_m": 0.0, "bottom_m": 0.2, "clay_g_kg": 780, "silt_g_kg": 147, "sand_g_kg": 73},
        "organic": {"top_m": 0.0, "bottom_m": 0.2, "kind": "organic_carbon_g_kg", "value": 17.8,
                    "method": "Walkley-Black"},
    },
    "floresta": {
        "doc": "Embrapa Doc 398", "page": "Descrição do protocolo experimental, p. 11",
        "class": "Latossolo Vermelho distroférrico muito argiloso",
        "texture": None,
        "texture_class": "very_clayey",
        "organic": {"top_m": 0.0, "bottom_m": 0.2, "kind": "organic_matter_g_kg", "value": 36.0,
                    "method": "MOS, as printed"},
    },
    "cafelandia": {
        "doc": "Embrapa Doc 459", "page": "Metodologia, p. 17",
        "class": "Latossolo Vermelho distroférrico, textura muito argilosa",
        "texture": {"top_m": 0.0, "bottom_m": 0.4, "clay_g_kg": 770, "silt_g_kg": 131, "sand_g_kg": 99},
        "organic": {"top_m": 0.0, "bottom_m": 0.2, "kind": "organic_carbon_g_kg", "value": 18.6,
                    "method": "Walkley-Black"},
    },
}


class TrialInputError(ValueError):
    pass


# SoilGrids at a point ------------------------------------------------------------------

def point_box(lat: float, lon: float, pixels: int = 1) -> tuple[float, float, float, float]:
    """Box in the service CRS around the point, ``pixels`` native pixels on each side."""
    x, y = soilgrids.igh_xy(lat, lon)
    m = pixels * soilgrids.NATIVE_PIXEL_M
    return x - m, y - m, x + m, y + m


def request(url: str, site: str, coverage: str, box) -> tuple[str, dict, str]:
    x0, y0, x1, y1 = box
    params = {"map": f"/map/{coverage.split('_')[0]}.map", "SERVICE": "WCS", "VERSION": "2.0.1",
              "REQUEST": "GetCoverage", "COVERAGEID": coverage, "FORMAT": "image/tiff",
              "SUBSET": [f"x({x0:.1f},{x1:.1f})", f"y({y0:.1f},{y1:.1f})"]}
    return url, params, f"trial_{site}_{coverage}_{base.cache_key(url, params)}.tif"


def value_at(path: Path, lat: float, lon: float) -> float | None:
    """Raw mapped value of the native pixel that holds the point. None: no data there."""
    from pipeline.fields.soil_profile import grid_of

    vals, (a, _, c, _, e, f) = grid_of(path)
    x, y = soilgrids.igh_xy(lat, lon)
    col, row = int(math.floor((x - c) / a)), int(math.floor((y - f) / e))
    if not (0 <= row < vals.shape[0] and 0 <= col < vals.shape[1]):
        raise TrialInputError(f"{path.name}: the point is outside the returned grid")
    v = float(vals[row, col])
    return None if math.isnan(v) else v


def soilgrids_point(site: str, lat: float, lon: float, config: Mapping[str, Any]) -> dict[str, dict]:
    """{depth: {clay_pct, sand_pct, soc_g_kg, files}} at the point, through the WCS cache."""
    cfg = config["sources"]["soilgrids"]
    box = point_box(lat, lon)
    out: dict[str, dict] = {}
    for d in DEPTHS:
        rec: dict[str, Any] = {"files": []}
        for prop in PROPERTIES:
            cov = f"{prop}_{d}_mean"
            url, params, name = request(cfg["url"], site, cov, box)
            path = base.cached_get(cfg["source_id"], url, params=params, filename=name, timeout=180)
            rec["files"].append(str(path.relative_to(base.REPO_ROOT)))
            raw = value_at(path, lat, lon)
            div, _ = soilgrids.CONVERSION[prop]
            key = {"clay": "clay_pct", "sand": "sand_pct", "soc": "soc_g_kg"}[prop]
            rec[key] = None if raw is None else raw / div
        out[d] = rec
    return out


# Record ----------------------------------------------------------------------------------

def _edges(doc: Mapping[str, Any]) -> list[float]:
    """SoilGrids depths, plus the bottom of the layer where the Doc prints organic matter."""
    edges = {0.0, *(b for _, b in LAYERS_M)}
    if doc.get("organic"):
        edges.add(float(doc["organic"]["bottom_m"]))
    return sorted(edges)


def _inside(part: Mapping[str, Any] | None, top: float, bottom: float) -> bool:
    return bool(part) and part["top_m"] - 1e-9 <= top and bottom <= part["bottom_m"] + 1e-9


def _sg_depth(top: float, bottom: float) -> str:
    for d, (a, b) in zip(DEPTHS, LAYERS_M):
        if a - 1e-9 <= top and bottom <= b + 1e-9:
            return d
    raise TrialInputError(f"layer {top}-{bottom} m crosses a SoilGrids depth")


def sand_silt_ratio(sg: Mapping[str, Mapping[str, Any]]) -> dict:
    """Sand share of sand + silt in the SoilGrids profile at the point, thickness-weighted over
    0 to 2 m (one value: the B horizon of a Latossolo is homogeneous). Pure."""
    sand = silt = 0.0
    for d, (a, b) in zip(DEPTHS, LAYERS_M):
        g = sg[d]
        if g.get("clay_pct") is None or g.get("sand_pct") is None:
            raise TrialInputError(f"no SoilGrids clay or sand at {d}")
        sand += g["sand_pct"] * (b - a)
        silt += (100 - g["clay_pct"] - g["sand_pct"]) * (b - a)
    if sand + silt <= 0:
        raise TrialInputError("SoilGrids gives no sand or silt at the point")
    return {"sand_share": sand / (sand + silt), "depth_m": [0.0, 2.0],
            "source": "SoilGrids 2.0 sand and silt (100 - clay - sand) at the trial point, "
                      "thickness-weighted over its six depths"}


def doc_texture(doc: Mapping[str, Any], sg: Mapping[str, Mapping[str, Any]]) -> dict:
    """The texture used in the whole profile: the Doc's, extended in depth (Latossolo).

    A Doc that prints only the class "muito argiloso" (Floresta) gives clay at the class
    lower bound, 600 g/kg, and the other 400 g/kg split between sand and silt in the
    SoilGrids sand:silt ratio at the point. Pure.
    """
    cite = f"{doc['doc']}, {doc['page']}"
    tex = doc.get("texture")
    if tex:
        return {"clay_pct": tex["clay_g_kg"] / 10, "sand_pct": tex["sand_g_kg"] / 10,
                "source": f"{cite}, {tex['top_m']:g}-{tex['bottom_m']:g} m, extended in depth (Latossolo)",
                "printed_depth_m": [tex["top_m"], tex["bottom_m"]]}
    if doc.get("texture_class") != "very_clayey":
        raise TrialInputError(f"{doc['doc']}: no texture and no texture class")
    ratio = sand_silt_ratio(sg)
    clay = VERY_CLAYEY_MIN_CLAY_PCT
    sand = round((100 - clay) * ratio["sand_share"], 1)
    return {"clay_pct": clay, "sand_pct": sand,
            "source": (f"{cite}, class '{doc['class']}' only: clay at the class lower bound "
                       f"(600 g/kg, SiBCS 2018), extended in depth (Latossolo); sand and silt split the "
                       f"other 400 g/kg in the SoilGrids sand:silt ratio at the point"),
            "clay_note": "lower bound of the class 'muito argilosa' (above 600 g/kg), not a measurement",
            "sand_silt_ratio": {**ratio, "sand_share": round(ratio["sand_share"], 4)}}


def _organic(lay: dict, src: dict, org: Mapping[str, Any] | None, cite: str, g: Mapping[str, Any],
             d: str, site: str, top: float, bottom: float) -> None:
    if _inside(org, top, bottom):
        if org["kind"] == "organic_matter_g_kg":
            lay["om_pct"] = round(org["value"] / 10, 3)
            src["om_pct"] = f"{cite}, organic matter {org['value']:g} g/kg ({org['method']}), printed layer only"
        else:
            lay["om_pct"] = round(org["value"] * VAN_BEMMELEN / 10, 3)
            src["om_pct"] = (f"{cite}, organic carbon {org['value']:g} g/kg ({org['method']}) "
                             f"x {VAN_BEMMELEN}, printed layer only")
    else:
        if g.get("soc_g_kg") is None:
            raise TrialInputError(f"{site}: no SoilGrids SOC at {d}")
        lay["om_pct"] = round(g["soc_g_kg"] * VAN_BEMMELEN / 10, 3)
        src["om_pct"] = f"SoilGrids 2.0 {d} mean at the trial point, SOC {g['soc_g_kg']:g} g/kg x {VAN_BEMMELEN}"


def build(site: str, sg: Mapping[str, Mapping[str, Any]], *, lat: float, lon: float,
          min_depth_m: float, doc: Mapping[str, Any] | None = None, texture: str = "doc") -> dict:
    """Soil record of one trial site. Pure.

    ``texture="doc"`` (the engine input): the Doc texture in every layer, extended in depth
    (Latossolo, SA-D65 as amended). ``texture="soilgrids"``: the SoilGrids
    texture at the point in every layer, a sensitivity side file only. Organic matter in
    both: the Doc's in its printed layer, SoilGrids below.
    """
    if texture not in ("doc", "soilgrids"):
        raise ValueError(f"texture must be doc or soilgrids, got {texture!r}")
    doc = DOC_SOIL[site] if doc is None else doc
    org = doc.get("organic")
    cite = f"{doc['doc']}, {doc['page']}"
    used = doc_texture(doc, sg) if texture == "doc" else None
    edges = _edges(doc)
    layers = []
    for top, bottom in zip(edges, edges[1:]):
        d = _sg_depth(top, bottom)
        g = sg[d]
        lay: dict[str, Any] = {"top_m": top, "bottom_m": bottom}
        src: dict[str, str] = {}
        if used is not None:
            lay["clay_pct"], lay["sand_pct"] = used["clay_pct"], used["sand_pct"]
            src["clay_pct"] = src["sand_pct"] = used["source"]
        else:
            if g.get("clay_pct") is None or g.get("sand_pct") is None:
                raise TrialInputError(f"{site}: no SoilGrids clay or sand at {d}")
            lay["clay_pct"], lay["sand_pct"] = round(g["clay_pct"], 1), round(g["sand_pct"], 1)
            src["clay_pct"] = src["sand_pct"] = f"SoilGrids 2.0 {d} mean at the trial point (native 250 m pixel)"
        _organic(lay, src, org, cite, g, d, site, top, bottom)
        lay["silt_pct"] = round(100 - lay["clay_pct"] - lay["sand_pct"], 1)
        lay["source"] = src
        layers.append(lay)
    if min_depth_m > layers[-1]["bottom_m"]:
        layers[-1] = {**layers[-1], "bottom_m": float(min_depth_m), "extended_from_m": layers[-1]["bottom_m"]}
    sg_clay = [round(sg[d]["clay_pct"], 1) if sg[d].get("clay_pct") is not None else None for d in DEPTHS]
    checks = [{"check": "SoilGrids clay at the point against the clay used, by SoilGrids depth",
               "soilgrids_clay_pct": dict(zip(DEPTHS, sg_clay)),
               "clay_used_pct": None if used is None else used["clay_pct"]}]
    if doc.get("texture_class") == "very_clayey":
        checks.append({"check": f"Doc class '{doc['class']}' (clay above 60%) against SoilGrids, 0 to 0.3 m",
                       "agrees": all(c is not None and c > VERY_CLAYEY_MIN_CLAY_PCT for c in sg_clay[:3])})
    rule = ("Doc texture of the trial area extended to the whole profile (Latossolo, homogeneous B horizon). "
            "SA-D65 as amended at execution level, open to the participant's veto"
            if used is not None else
            "SoilGrids 2.0 texture at the trial point in every layer: sensitivity side file only, "
            "never the engine input")
    return {
        "_note": "Written with an SI tool (Claude, Anthropic) for NASA Space Apps 2026. See AI_USE.md.",
        "site": site, "kind": "trial_soil" if used is not None else "trial_soil_sensitivity",
        "rule": rule,
        "point": {"lat": lat, "lon": lon, "source": "config trial_sites (protocol coordinate)"},
        "doc": {"source": cite, "class": doc["class"]},
        "texture_used": used,
        "layers": layers,
        "checks": checks,
        "estimate": used is None or doc.get("texture") is None,
    }


def write_soil(record: Mapping[str, Any]) -> Path:
    site = record["site"]
    if site not in DOC_SOIL:
        raise TrialInputError(f"unknown trial site {site!r}")
    suffix = "" if record["kind"] == "trial_soil" else "_soilgrids"
    return base.write_json(base.INTERIM_DIR / f"trial_{site}_soil{suffix}.json", record)


def run_soil(sites: list[str], config: Mapping[str, Any] | None = None) -> list[Path]:
    config = config or base.load_config()
    min_depth = float(config["protocol"]["engine"]["profile_min_depth_m"])
    out = []
    for site in sites:
        s = next((x for x in config["trial_sites"] if x["id"] == site), None)
        if s is None:
            raise TrialInputError(f"trial_sites has no {site!r}")
        lat, lon = float(s["lat"]), float(s["lon"])
        sg = soilgrids_point(site, lat, lon, config)
        out.append(write_soil(build(site, sg, lat=lat, lon=lon, min_depth_m=min_depth)))
        out.append(write_soil(build(site, sg, lat=lat, lon=lon, min_depth_m=min_depth, texture="soilgrids")))
    return out


# POWER at the printed Floresta point -------------------------------------------------------

def printed_power_spec(config: Mapping[str, Any], call_id: str = "met") -> dict:
    """POWER spec of ``pipeline.adapters.power`` at protocol.side_runs.floresta_printed_coordinate."""
    pt = config["protocol"]["side_runs"]["floresta_printed_coordinate"]
    cfg = config["sources"]["power"]
    call = next(c for c in cfg["calls"] if c["id"] == call_id)
    return {"source_id": cfg["source_id"], "url": cfg["url"], "license": cfg["license"],
            "site": PRINTED_SITE, "lat": float(pt["lat"]), "lon": float(pt["lon"]),
            "call": call["id"], "parameters": list(call["parameters"]), "start": call["start"],
            "end": call["end"], "time_standard": call["time_standard"], "community": cfg["community"]}


def run_power_printed(config: Mapping[str, Any] | None = None) -> Path:
    from pipeline.adapters import power

    config = config or base.load_config()
    spec = printed_power_spec(config)
    path = power.fetch(spec)
    prov = power.provenance(spec)
    df = power.read(path)
    base.write_json(base.INTERIM_DIR / f"power_{PRINTED_SITE}.json",
                    {**prov, "summary": power.summary(df),
                     "role": "side run of D2: the coordinate Doc 398 prints (23 55 S). Download only"})
    return path


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Trial engine inputs: soil records and the printed Floresta POWER.")
    sub = ap.add_subparsers(dest="command", required=True)
    so = sub.add_parser("soil")
    so.add_argument("--sites", default=",".join(DOC_SOIL))
    sub.add_parser("power-printed")
    args = ap.parse_args(argv)
    if args.command == "soil":
        for p in run_soil([s.strip() for s in args.sites.split(",") if s.strip()]):
            print(f"-> {p.relative_to(base.REPO_ROOT)}")
    else:
        print(f"-> {run_power_printed().relative_to(base.REPO_ROOT)}")
    print(f"network_calls={base.network_calls}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

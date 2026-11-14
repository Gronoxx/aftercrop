# Written with an SI tool (Claude, Anthropic) for NASA Space Apps 2026. See AI_USE.md.
"""Precompute input of the real fields, assembled from the M2 outputs (plan 8.4 steps 5 to 11 and 15, sheet M5).

The bridge from M2 to M5. It reads what the field modules wrote and computes no field
fact of its own: no NDVI, no green exit, no field choice. Per field of the fields
FeatureCollection (``config/fields/fields.geojson``):

- geometry: ``data/interim/<stem>_geometry.json`` (``pipeline.fields.geometry``): ``area_ha``,
  ``polygon_px`` on the basemap grid;
- ``field_<id>_ndvi.csv`` (``ndvi_series``), ``field_<id>_ribbon.csv`` (``ribbon``),
  ``field_<id>_green_end.csv`` (``events``);
- ``field_<id>_soil.json`` (``soil_profile``) and ``field_<id>_relief.json`` (``relief``).

For the whole window:

- weather: the POWER met file (``pipeline.adapters.power``) of the trial site whose POWER
  cell holds every field, written as the daily CSV precompute reads (rain and minimum
  temperature). A field outside that cell stops the run: the POWER cell is the unit;
- noise floor: ``noise_floor.json`` (``pipeline.fields.noise``) through
  ``pipeline.bench.n1.evaluate``, when the protocol fixes the N1 percentile ([DECIDE]);
  otherwise null, and the page shows no field order;
- erosivity: the GloREDa window record (``gloreda_window_<window>.json``) unless the base
  gives ``erosivity``.

``scale_note`` (plan 8.6, 9.5) is the side of a square with the area of each source cell at
the field's latitude: POWER meteorology 0.5 x 0.625 degree and radiation 1 degree (plan
9.1, power.larc.nasa.gov/docs/methodology), SoilGrids 250 m, the NASADEM step of the relief
record and the GloREDa step of its plan file.

Everything else precompute needs (region pack, trials, calendars, cycles, risk spans) comes
from a base JSON the participant writes, with the [DECIDE] values. The result is labelled
"real"; each field's ``m2`` block carries ``provenance`` (paths and sha256 of its files),
which ``precompute`` checks again before it writes anything.

Rehearsal (``--rehearsal``, SA-D63): the base must be a rehearsal base (``run_mode``), every
field must lie in the development strip and the geometry summary must come from
``geometry --rehearsal``. The inputs are stamped ``run_mode: rehearsal``. Without the flag a
rehearsal base is refused, and the fields must lie in the product area: fictitious trials never
reach a product run. The build folder never lies in site/data/.

Run from the repository root (only after the D10 values are locked and the M2 steps ran):
    python -m pipeline.decision.field_inputs config/fields/fields.geojson --base <decision_base.json>
        [--power-site londrina] [--build-out data/build]
    python -m pipeline.decision.precompute --real --inputs data/build/precompute_inputs.json
        --zarc data/interim/zarc_norte_pr.csv --out site/data --build-out data/build
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any


from pipeline.adapters import base, power, soilgrids
from pipeline.bench import common as bench_common
from pipeline.bench import n1
from pipeline.common import rehearsal as rh
from pipeline.decision import precompute as pc
from pipeline.decision import risk_counts as rc
from pipeline.fields import events, geometry, ndvi_series, relief, ribbon, soil_profile

ASSEMBLER = pc.ASSEMBLER
# Grid steps (lat, lon) in degrees, plan 9.1 and 9.5 (power.larc.nasa.gov/docs/methodology).
POWER_MET_GRID_DEG = (0.5, 0.625)
POWER_RADIATION_GRID_DEG = (1.0, 1.0)
INPUTS_NAME = "precompute_inputs.json"
BASE_KEYS = ("season", "current_safra", "cycles", "occupancy", "risk", "example_context", "example_constraints",
             "region", "trials")
SOIL_KEYS = ("source", "source_id", "clay_q05_q95", "estimated_class", "estimated_class_detail", "estimated_band",
             "zarc_ad_class", "zarc_ad_status", "zarc_ad_note", "transfer_warning", "lab_report_request", "ledger")
LAYER_KEYS = ("top_m", "bottom_m", "clay_pct", "sand_pct", "silt_pct", "om_pct")
RELIEF_KEYS = ("source_id", "context_only", "slope_class", "slope_pct", "class_shares", "height_m", "lowspot_alert",
               "terrace_question", "ledger")


class FieldInputsError(ValueError):
    pass


def _need(cond: bool, msg: str) -> None:
    if not cond:
        raise FieldInputsError(msg)


def _rel(path: Path) -> str:
    p = Path(path).resolve()
    root = base.REPO_ROOT.resolve()
    return p.relative_to(root).as_posix() if root in p.parents else str(p)


def file_entry(path: Path) -> dict:
    return {"path": _rel(path), "sha256": base.sha256_file(path)}


# Per-field blocks (pure) ----------------------------------------------------------------------

def num(rid: str, v: Any) -> dict:
    return {"v": v, "l": rid}


def side_m(lat: float, step_lat_deg: float, step_lon_deg: float) -> float:
    """Side of the square with the area of a ``step_lat x step_lon`` degree cell at ``lat`` (WGS 84). Pure."""
    dx, _ = relief.spacing_m(lat, step_lon_deg)
    _, dy = relief.spacing_m(lat, step_lat_deg)
    return math.sqrt(float(dx) * float(dy))


def scale_note(field_id: str, lat: float, *, dem_step_deg: float | None, erosivity_step_deg: float | None) -> dict:
    """``scale_note`` of plan 8.6: one Num per source, the cell side at the field's latitude. Pure."""
    p = f"field_{field_id}.scale_note"
    met = side_m(lat, *POWER_MET_GRID_DEG) / 1000
    rad = side_m(lat, *POWER_RADIATION_GRID_DEG) / 1000
    dem = None if dem_step_deg is None else round(side_m(lat, dem_step_deg, dem_step_deg), 1)
    ero = None if erosivity_step_deg is None else round(side_m(lat, erosivity_step_deg, erosivity_step_deg) / 1000, 2)
    return {
        "power_cell_km": num(f"{p}.power_cell_km", round(met, 1)),
        "radiation_cell_km": num(f"{p}.radiation_cell_km", round(rad, 1)),
        "soil_m": num(f"{p}.soil_m", int(soilgrids.NATIVE_PIXEL_M)),
        "dem_m": num(f"{p}.dem_m", dem),
        "erosivity_km": num(f"{p}.erosivity_km", ero),
    }


def area_ha(field_id: str, geo: Mapping[str, Any]) -> dict:
    """``area_ha`` as geometry.py measured it (``field_on_grid``, outer polygon in the grid CRS)."""
    _need(geo.get("ok") is True, f"field {field_id}: refused by geometry.py ({geo.get('error')})")
    return num(f"field_{field_id}.area_ha", geo["area_ha"])


def ndvi_block(field_id: str, rows: Sequence[Mapping[str, Any]]) -> dict:
    """The series for the page: rows with at least one clean pixel, one record for the whole series."""
    pts = [{"date": r["date"], "ndvi": round(r["ndvi"], 4), "nbr2": None if r["nbr2"] is None else round(r["nbr2"], 4),
            "sensor": r["sensor"], "n_clean": r["n_clean"], "clean_frac": round(r["clean_frac"], 4)}
           for r in rows if r["ndvi"] is not None]
    return {"ledger": f"field_{field_id}.ndvi_series", "points": sorted(pts, key=lambda p: (p["date"], p["sensor"]))}


def ribbon_block(field_id: str, seasons: Sequence[Mapping[str, Any]]) -> dict:
    return {"ledger": f"field_{field_id}.ribbon",
            "seasons": [{"season": s["season"], "cells": list(s["cells"]), "n_obs": s["n_obs"],
                         "longest_gap_days": s["longest_gap_days"], "green_cycles": s["green_cycles"]}
                        for s in seasons]}


def green_end_block(field_id: str, evs: Sequence[Mapping[str, Any]]) -> list[dict]:
    """``green_end`` of plan 8.6, ``gap_days`` as a Num per season (the interval is the uncertainty)."""
    return [{"season": e["season"], "low": e["low"], "high": e["high"],
             "gap_days": num(pc.ledger_id(f"field_{field_id}", "green_end", e["season"]), e["gap_days"]),
             "status": e["status"]} for e in evs]


def soil_block(field_id: str, rec: Mapping[str, Any]) -> dict:
    """The page's soil block from ``soil_profile.build``. A record of an older schema is refused."""
    _need(rec.get("field_id") == field_id, f"soil record of {rec.get('field_id')!r} given for {field_id!r}")
    _need(rec.get("ledger") == f"field_{field_id}.soil" and rec.get("source_id") == soil_profile.SOURCE_ID
          and (rec.get("estimated_class") is None or isinstance(rec.get("estimated_class"), str)),
          f"field {field_id}: soil record of an older schema, run python -m pipeline.fields.soil_profile again")
    out = {k: rec.get(k) for k in SOIL_KEYS}
    out["layers"] = [{k: lay.get(k) for k in LAYER_KEYS} for lay in rec["layers"]]
    return out


def relief_block(field_id: str, rec: Mapping[str, Any]) -> dict:
    _need(rec.get("field_id") == field_id, f"relief record of {rec.get('field_id')!r} given for {field_id!r}")
    _need(rec.get("ledger") == f"field_{field_id}.relief",
          f"field {field_id}: relief record of an older schema, run python -m pipeline.fields.relief again")
    out = {k: rec.get(k) for k in RELIEF_KEYS}
    out["lowspot"] = {"status": rec["lowspot"]["status"], "alert": rec["lowspot"]["alert"]}
    return out


def centroid(ring: Sequence[Sequence[float]]) -> tuple[float, float]:
    """(lon, lat) mean of the distinct vertices. Pure; enough for a cell lookup and a latitude."""
    return sum(p[0] for p in ring) / len(ring), sum(p[1] for p in ring) / len(ring)


# Window inputs ---------------------------------------------------------------------------------

def power_cell(lat: float, lon: float, step: tuple[float, float] = POWER_MET_GRID_DEG) -> tuple[int, int]:
    """Index of the POWER meteorology cell (nearest grid centre from -90, -180). Pure."""
    return round((lat + 90) / step[0]), round((lon + 180) / step[1])


def power_site_for(points: Sequence[tuple[float, float]], config: Mapping[str, Any], site: str | None = None) -> str:
    """The trial site whose POWER cell holds every (lon, lat) point. ``site`` must agree when given."""
    cells = {power_cell(lat, lon) for lon, lat in points}
    _need(len(cells) == 1, f"the fields fall in {len(cells)} POWER cells: one weather series cannot serve them")
    cell = next(iter(cells))
    same = [s["id"] for s in config["trial_sites"] if power_cell(float(s["lat"]), float(s["lon"])) == cell]
    if site is not None:
        _need(site in same, f"POWER site {site!r} is not in the POWER cell of the fields (sites there: {same})")
        return site
    _need(len(same) >= 1, "no trial site shares the POWER cell of the fields: a POWER call for the field window "
                          "is needed (pipeline.adapters.power)")
    return same[0]


def power_file(site: str, config: Mapping[str, Any]) -> Path:
    """The cached POWER met file of a trial site, as ``power.fetch`` stores it."""
    specs = [s for s in power.specs(config) if s["site"] == site and s["call"] == "met"]
    _need(len(specs) == 1, f"no POWER met call for site {site!r}")
    url, params, name = power.request(specs[0])
    return base.raw_path(specs[0]["source_id"], url, params, name)


def write_daily(power_path: Path, out: Path) -> Path:
    """The daily CSV of precompute: rain and minimum temperature of the POWER file, never labelled example."""
    df = power.read(power_path)
    missing = [c for c in (rc.RAIN, rc.TMIN) if c not in df.columns]
    _need(not missing, f"{power_path.name} lacks {missing}")
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8", newline="") as f:
        f.write(f"# NASA POWER daily point data (power_daily), {power_path.name}, "
                f"sha256 {base.sha256_file(power_path)}\n")
        df[[rc.RAIN, rc.TMIN]].to_csv(f, index_label="date", date_format="%Y-%m-%d")
    return out


def noise_floor(doc: Mapping[str, Any] | None, protocol: Mapping[str, Any], *, percentile: Any = None,
                method: str | None = None) -> dict:
    """``{"days", "data_version", "note"}`` for region.json, from noise_floor.json and the N1 rule.

    Without the file, without the percentile in the protocol, or with N1 not completed:
    ``days`` null, said in the note.
    """
    if doc is None:
        return {"days": None, "data_version": "not completed: no noise_floor.json", "note": None}
    halves, sensors = pipeline_noise_inputs(doc)
    try:
        rule = n1.N1Rule.from_protocol(protocol, percentile=percentile, method=method)
    except bench_common.ProtocolError as exc:
        return {"days": None, "data_version": "not completed: the N1 rule is not fixed",
                "note": f"{exc}"}
    res = n1.evaluate(halves, sensors, rule)
    dv = (f"N1 of noise_floor.json: {res.n_halves} pairs of field halves, {res.n_sensors} of L30 against S30, "
          f"percentile {float(rule.percentile):g} ({rule.method})")
    days = None if res.floor_days is None else float(res.floor_days)
    return {"days": days, "data_version": dv, "note": "; ".join(res.notes) or None}


def pipeline_noise_inputs(doc: Mapping[str, Any]) -> tuple[list, list]:
    from pipeline.fields import noise

    try:
        return noise.n1_inputs(doc)
    except (KeyError, ValueError) as exc:
        raise FieldInputsError(f"noise_floor.json: {exc}") from exc


def erosivity_from_gloreda(doc: Mapping[str, Any]) -> dict:
    r = doc.get("window_mean_monthly_r")
    _need(isinstance(r, list) and len(r) == 12 and all(isinstance(x, (int, float)) for x in r),
          "the GloREDa window record has no 12 monthly values")
    return {"monthly_r": list(r), "data_version": "GloREDa 1.2 (Zenodo 10.5281/zenodo.8036998), "
                                                  "mean of the field-window pixels"}


def gloreda_step_deg(plan: Mapping[str, Any] | None) -> float | None:
    """Pixel step of GloREDa from its plan file (TIFF transform), or None."""
    try:
        return float(plan["months"][0]["tiff"]["transform"][0])
    except (TypeError, KeyError, IndexError, ValueError):
        return None


# Assembly ---------------------------------------------------------------------------------------

def _load(path: Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def field_paths(field_id: str, interim: Path) -> dict[str, Path]:
    """The M2 files of one field in ``interim`` (data/interim/ by default)."""
    return {
        "ndvi": interim / ndvi_series.csv_path(field_id).name,
        "ribbon": interim / ribbon.csv_path(field_id).name,
        "green_end": interim / events.csv_path(field_id).name,
        "soil": interim / soil_profile.output_path(field_id).name,
        "relief": interim / relief.output_path(field_id).name,
    }


def m2_block(field: Mapping[str, Any], geo: Mapping[str, Any], paths: Mapping[str, Path], *,
             geometry_path: Path, erosivity_step_deg: float | None) -> dict:
    """``fields[].m2`` for precompute, with the provenance of every file it read."""
    fid = field["field_id"]
    missing = [f"{k}: {_rel(p)}" for k, p in paths.items() if not p.is_file()]
    _need(not missing, f"field {fid}: M2 outputs missing ({', '.join(missing)})")
    rel = _load(paths["relief"])
    lon, lat = centroid(field["ring"])
    return {
        "polygon_px": geo["polygon_px"],
        "area_ha": area_ha(fid, geo),
        "scale_note": scale_note(fid, lat, dem_step_deg=(rel.get("method") or {}).get("grid_step_deg"),
                                 erosivity_step_deg=erosivity_step_deg),
        "soil": soil_block(fid, _load(paths["soil"])),
        "relief": relief_block(fid, rel),
        "ndvi_series": ndvi_block(fid, ndvi_series.read_csv(paths["ndvi"])),
        "ribbon": ribbon_block(fid, ribbon.read_csv(paths["ribbon"])),
        "green_end": green_end_block(fid, events.read_csv(paths["green_end"])),
        "provenance": {"label": pc.REAL, "assembler": ASSEMBLER,
                       "files": [file_entry(geometry_path)] + [file_entry(p) for p in paths.values()]},
    }


def geometry_summary_path(geojson: Path, interim: Path) -> Path:
    return interim / f"{Path(geojson).stem}_geometry.json"


def per_field(base_inputs: Mapping[str, Any], fid: str, key: str) -> Any:
    own = ((base_inputs.get("fields") or {}).get(fid) or {})
    value = own.get(key, base_inputs.get(key))
    _need(value is not None, f"the base gives no {key} for field {fid} ([DECIDE])")
    return value


def assemble(geojson: Path, base_inputs: Mapping[str, Any], *, config: Mapping[str, Any],
             interim_dir: Path | None = None, build_dir: Path | None = None, power_site: str | None = None,
             power_path: Path | None = None, n1_percentile: Any = None, n1_method: str | None = None,
             rehearsal: bool = False) -> dict:
    """The precompute input labelled "real". Writes only the daily CSV, to ``build_dir``.

    ``rehearsal``: a rehearsal base and development-strip fields only, inputs stamped (SA-D63).
    """
    interim = Path(interim_dir or base.INTERIM_DIR)
    build = Path(build_dir or pc.BUILD_DIR)
    _need(not rh.inside_site_data(build), "the build folder never lies in site/data/")
    try:
        rh.check_mode(base_inputs, rehearsal, "the decision base")
    except rh.RehearsalError as exc:
        raise FieldInputsError(str(exc)) from exc
    _need(base_inputs.get("label") in (None, pc.REAL), f"the base is labelled {base_inputs.get('label')!r}, not real")
    absent = [k for k in BASE_KEYS if k not in base_inputs]
    _need(not absent, f"the base lacks {absent}")
    fields = geometry.load_fields(geojson, config, rehearsal=rehearsal)     # SA-D63 area of the mode
    geo_path = geometry_summary_path(geojson, interim)
    _need(geo_path.is_file(), f"no {geo_path.name}: run python -m pipeline.fields.geometry {geojson}")
    geo_doc = _load(geo_path)
    want = "rehearsal" if rehearsal else "product"
    _need(geo_doc.get("mode") in ((want,) if rehearsal else (None, want)),
          f"{geo_path.name} was checked in mode {geo_doc.get('mode')!r}, this run is {want!r}: run "
          f"python -m pipeline.fields.geometry {geojson}{' --rehearsal' if rehearsal else ''}")
    window_id = config["sources"]["hls"]["window"]["id"]
    _need(geo_doc.get("window_id") in (None, window_id),
          f"{geo_path.name} was checked on window {geo_doc.get('window_id')!r}, the config window is {window_id!r}")
    geo_by_id = {g["field_id"]: g for g in geo_doc.get("fields", [])}
    gl_plan = interim / f"gloreda_plan_{window_id}.json"
    ero_step = gloreda_step_deg(_load(gl_plan)) if gl_plan.is_file() else None

    out_fields = []
    for f in fields:
        fid = f["field_id"]
        _need(fid in geo_by_id, f"field {fid} is not in {geo_path.name}")
        entry = {"field_id": fid, "criterion": f["criterion"],
                 "fictional_persona_note": per_field(base_inputs, fid, "fictional_persona_note"),
                 "municipio": per_field(base_inputs, fid, "municipio"),
                 "m2": m2_block(f, geo_by_id[fid], field_paths(fid, interim), geometry_path=geo_path,
                                erosivity_step_deg=ero_step)}
        own = (base_inputs.get("fields") or {}).get(fid) or {}
        if "next_soy" in own:
            entry["next_soy"] = own["next_soy"]
        out_fields.append(entry)
    _need(bool(out_fields), "no field")

    site = power_site_for([centroid(f["ring"]) for f in fields], config, power_site)
    pw = Path(power_path) if power_path else power_file(site, config)
    _need(pw.is_file(), f"no POWER file {_rel(pw)}: run make power")
    daily = write_daily(pw, build / f"daily_weather_{site}.csv")

    nf_path = interim / "noise_floor.json"
    nf = noise_floor(_load(nf_path) if nf_path.is_file() else None, config["protocol"],
                     percentile=n1_percentile, method=n1_method)
    window_files = [file_entry(pw)] + ([file_entry(nf_path)] if nf_path.is_file() else [])

    if "erosivity" in base_inputs:
        ero = base_inputs["erosivity"]
    else:
        gw = interim / f"gloreda_window_{window_id}.json"
        _need(gw.is_file(), f"no {gw.name}: run make gloreda, or give erosivity in the base")
        ero = erosivity_from_gloreda(_load(gw))
        window_files.append(file_entry(gw))

    inputs = {"label": pc.REAL, "assembled_by": ASSEMBLER, "fields_file": _rel(Path(geojson)),
              **{k: base_inputs[k] for k in BASE_KEYS},
              "erosivity": ero, "noise_floor": nf, "power_site": site,
              "daily_weather_csv": daily.name, "window_files": window_files, "fields": out_fields}
    return rh.stamped(inputs) if rehearsal else inputs


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Precompute input of the real fields from the M2 outputs (sheet M5).")
    ap.add_argument("geojson", type=Path, help="fields FeatureCollection, such as config/fields/fields.geojson")
    ap.add_argument("--base", required=True, type=Path,
                    help="JSON with season, cycles, occupancy, risk, region, trials and per-field municipio")
    ap.add_argument("--build-out", type=Path, default=pc.BUILD_DIR, help="where the inputs and the daily CSV go")
    ap.add_argument("--power-site", default=None, help="trial site whose POWER file serves the field window")
    ap.add_argument("--n1-percentile", default=None, help="until protocol.tests.N1.percentile holds it [DECIDE]")
    ap.add_argument("--n1-method", default=None, help="until protocol.tests.N1.percentile_method holds it [DECIDE]")
    ap.add_argument("--rehearsal", action="store_true",
                    help="rehearsal base and development-strip fields only, inputs stamped run_mode rehearsal (SA-D63)")
    a = ap.parse_args(argv)
    config = base.load_config()
    try:
        inputs = assemble(a.geojson, _load(a.base), config=config, build_dir=a.build_out, power_site=a.power_site,
                          n1_percentile=a.n1_percentile, n1_method=a.n1_method, rehearsal=a.rehearsal)
    except (FieldInputsError, geometry.GeometryError, pc.PrecomputeError, ValueError) as exc:
        print(f"field_inputs: {exc}", file=sys.stderr)
        return 1
    out = Path(a.build_out) / INPUTS_NAME
    out.write_text(json.dumps(inputs, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"{len(inputs['fields'])} field(s), POWER site {inputs['power_site']}, "
          f"noise floor {inputs['noise_floor']['days']} -> {_rel(out)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

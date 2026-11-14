# Written with an SI tool (Claude, Anthropic) for NASA Space Apps 2026. See AI_USE.md.
"""Observation region (SA-D79, plan 5.6 and 8.8): the method travels, the verdict does not.

One field of a region without a Regional Pack (no trials, no zoning calendar). The same M2
code as the default region reads it: the HLS-VI series, the 8-season ribbon and the end of the
first green cycle (protocol.field values copied from the tagged protocol), NASADEM relief, and
NASA POWER daily at the field point (nights below 2 degrees C and rain over a fixed period of
the config). Nothing here decides, compares, prices or labels: the documents carry no state,
no G and no strategy, and say ``mode: "observation"``.

Paths come from the region config (``paths:``): raw downloads in ``paths.raw`` through
``base.cached_get`` (HLS windows through ``hls.cached_window``, same rules), derived files in
``paths.interim``, the page documents in ``paths.site_out`` (inside site/, published). It
refuses to write to site/data/ and to the default data/raw/ and data/interim/.

Run from the repository root (make observation REGION=free_state runs every step):
    .venv/bin/python -m pipeline.regions.observation fetch  --config config/region_free_state.yaml
    .venv/bin/python -m pipeline.regions.observation build  --config config/region_free_state.yaml
The basemap comes first, from the same config:
    AFTERCROP_CONFIG=config/region_free_state.yaml .venv/bin/python -m pipeline.fields.basemap
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from pipeline.adapters import base

SCHEMA_VERSION = "1"
MODE = "observation"
SITE_DATA = base.REPO_ROOT / "site" / "data"
FIELD_LETTERS = ("a",)


class ObservationError(RuntimeError):
    """The region config or its inputs cannot make an observation page."""


def _need(cond: bool, msg: str) -> None:
    if not cond:
        raise ObservationError(msg)


def _rel(path: Path) -> str:
    try:
        return Path(path).resolve().relative_to(base.REPO_ROOT.resolve()).as_posix()
    except ValueError:
        return str(path)


def setup(config_path: Path | str) -> dict:
    """Load the region config, check it is an observation region with its own folders, and point
    ``base`` at them. Returns the config."""
    config = base.use_region(config_path)
    _need(config.get("mode") == MODE, f"{config_path}: mode must be {MODE!r}, got {config.get('mode')!r}")
    paths = config.get("paths") or {}
    for key in ("raw", "interim", "site_out", "fields"):
        _need(isinstance(paths.get(key), str) and paths[key], f"{config_path}: paths.{key} is missing")
    default_raw, default_interim = base.DATA_DIR / "raw", base.DATA_DIR / "interim"
    _need(base.RAW_DIR.resolve() != default_raw.resolve(), "paths.raw must not be the default data/raw/")
    _need(base.INTERIM_DIR.resolve() != default_interim.resolve(), "paths.interim must not be the default data/interim/")
    out = site_out(config)
    _need(out.resolve() != SITE_DATA.resolve() and SITE_DATA.resolve() not in out.resolve().parents,
          "paths.site_out must not be site/data/ (frozen product data)")
    _need((base.REPO_ROOT / "site").resolve() in out.resolve().parents, "paths.site_out must lie inside site/ to be published")
    return config


def site_out(config: Mapping[str, Any]) -> Path:
    return base.REPO_ROOT / config["paths"]["site_out"]


def fields_path(config: Mapping[str, Any]) -> Path:
    return base.REPO_ROOT / config["paths"]["fields"]


def load_fields(config: Mapping[str, Any]) -> list[dict]:
    from pipeline.fields import geometry

    fields = geometry.load_fields(fields_path(config), config)
    _need(len(fields) == 1 and fields[0]["field_id"] == "a", "an observation region holds one field, field_id 'a'")
    return fields


def open_ring(ring):
    """The distinct vertices of a ring, without the closing point. Pure."""
    ring = [tuple(p) for p in ring]
    return ring[:-1] if len(ring) > 1 and ring[0] == ring[-1] else ring


# POWER at the field point ------------------------------------------------------------------

def power_spec(config: Mapping[str, Any], field: Mapping[str, Any]) -> dict:
    from pipeline.decision.field_inputs import centroid

    cfg = config["sources"]["power"]
    call = next(c for c in cfg["calls"] if c["id"] == "met")
    lon, lat = centroid(open_ring(field["ring"]))
    return {"source_id": cfg["source_id"], "url": cfg["url"], "license": cfg["license"],
            "site": f"{config['region']}_field_{field['field_id']}", "lat": round(lat, 4), "lon": round(lon, 4),
            "call": "met", "parameters": list(call["parameters"]), "start": call["start"], "end": call["end"],
            "time_standard": call["time_standard"], "community": cfg["community"]}


# Fetch -------------------------------------------------------------------------------------

def fetch(config: Mapping[str, Any], workers: int = 4) -> dict:
    """HLS-VI of every ribbon season, the NASADEM tile and POWER at the field point."""
    from pipeline.adapters import hls, nasadem, power

    out = {"hls": hls.run(config, workers=workers)}
    out["nasadem"] = [_rel(p) for p in nasadem.fetch(nasadem.spec(config))]
    field = load_fields(config)[0]
    out["power"] = _rel(power.fetch(power_spec(config, field)))
    out["network_calls"] = base.network_calls
    return out


# Build -------------------------------------------------------------------------------------

def climate_block(config: Mapping[str, Any], daily) -> dict:
    """Nights below the frost threshold and rain over the fixed period of the config."""
    from pipeline.decision import risk_counts as rc

    cl = config["protocol"]["observation_climate"]
    _need(cl["frost"]["variable"] == rc.TMIN and cl["rain"]["variable"] == rc.RAIN,
          "observation_climate names other POWER variables than risk_counts reads")
    start, end = cl["period"]
    years = rc.protocol_years({"climate": {"years": cl["years"]}})
    res = rc.occupation_climate(daily, start=start, end=end, years=years, frost_below_c=cl["frost"]["below_c"])
    seasons = [s for s, _, _ in rc.occupation_periods(start, end, years)]
    return {**res, "period_kind": cl["period_kind"], "years": [int(y) for y in years],
            "first_season": seasons[0] if seasons else None, "last_season": seasons[-1] if seasons else None}


def _mb(n: int) -> float:
    return round(n / 1e6, 1)


def sources_block(config: Mapping[str, Any], pspec: Mapping[str, Any]) -> list[dict]:
    """One entry per NASA source the page used, read from the cache (no network). No download date
    goes to the page (SA-D104): it stays in the local cache metadata."""
    from pipeline.adapters import hls, nasadem, power

    out = []
    files = granules = size = 0
    for spec in hls.specs(config):
        prov = hls.provenance(spec)
        files += prov["n_files"]
        size += prov["bytes"]
        idx = json.loads(hls.index_path(spec).read_text(encoding="utf-8"))
        granules += len(idx["granules"])
    first, last = config["protocol"]["dates"]["ribbon_seasons"]
    out.append({"id": "hls_vi_v2", "title": "HLS-VI v2.0 (HLSL30_VI, HLSS30_VI), NASA LP DAAC",
                "license": config["sources"]["hls"]["license"],
                "detail": {"granules": granules, "band_files": files, "mb": _mb(size), "seasons": [first, last],
                           "tile": config["sources"]["hls"]["window"]["tile"]}})
    nspec = nasadem.spec(config)
    tiles = nasadem.provenance(nspec)["tiles"]
    out.append({"id": "nasadem", "title": "NASADEM_HGT v001, NASA JPL, LP DAAC", "license": nspec["license"],
                "detail": {"tiles": len(tiles), "mb": _mb(sum(t["bytes"] for t in tiles)),
                           "names": [t.get("tile") for t in tiles]}})
    pp = power.provenance(pspec)
    out.append({"id": "power_daily", "title": "NASA POWER daily, community AG", "license": pspec["license"],
                "detail": {"point": [pspec["lat"], pspec["lon"]], "returned_point": pp.get("returned_point"),
                           "period": [pspec["start"], pspec["end"]], "parameters": pspec["parameters"],
                           "mb": _mb(pp["bytes"]), "version": pp["version"]}})
    return out


def build(config: Mapping[str, Any]) -> dict:
    """Interim files of the field, then region.json, field_a.json and img/ in paths.site_out."""
    from pipeline.adapters import nasadem, power
    from pipeline.build import notice
    from pipeline.decision import field_inputs as fi
    from pipeline.fields import events, geometry, ndvi_series, relief, ribbon

    field = load_fields(config)[0]
    fid = field["field_id"]
    window_id = config["sources"]["hls"]["window"]["id"]
    georef = geometry.read_geojson(geometry.georef_path(config))
    summary = geometry.check_file(fields_path(config), georef, geometry.GeometryRule.from_protocol(config), window_id,
                                  area=geometry.AreaRule.from_protocol(config))
    geo = summary["fields"][0]
    _need(geo["ok"], f"field {fid} refused: {geo.get('error')}")
    base.write_json(base.INTERIM_DIR / "fields_geometry.json", summary)

    # M2: series, ribbon, end of the first green cycle, as the default region runs them.
    clean = ribbon.CleanRule.from_protocol(config)
    erule = events.EventRule.from_protocol(config)
    grule = geometry.GeometryRule.from_protocol(config)
    seasons = ribbon.ribbon_seasons(config)
    records = ndvi_series.index_records(config)
    rows, _skipped = ndvi_series.series_from_records(records, geometry.FieldMasks(field, grule),
                                                     **ndvi_series.fmask_settings(config))
    ndvi_series.write_csv(ndvi_series.csv_path(fid), rows.get("whole", []))
    series = ndvi_series.read_csv(ndvi_series.csv_path(fid))
    ribbon.write_csv(ribbon.csv_path(fid), ribbon.ribbon(series, seasons, clean))
    events.write_csv(events.csv_path(fid), events.green_ends(series, seasons, erule))
    dem = nasadem.load_window(nasadem.spec(config))
    rel = relief.run(fields_path(config), dem, config)[0]
    relief.write(rel)

    pspec = power_spec(config, field)
    daily = power.read(power.fetch(pspec))
    climate = climate_block(config, daily)

    rb = ribbon.read_csv(ribbon.csv_path(fid))
    evs = events.read_csv(events.csv_path(fid))
    lon, lat = fi.centroid(open_ring(field["ring"]))
    field_doc = notice.stamped({
        "schema_version": SCHEMA_VERSION,
        "mode": MODE,
        "region_id": config["region"],
        "field_id": fid,
        "criterion": field.get("criterion"),
        "point": {"lat": round(lat, 5), "lon": round(lon, 5)},
        "polygon_px": geo["polygon_px"],
        "area_ha": {"v": round(geo["area_ha"], 2), "l": None},
        "n_inner_pixels": geo["n_possible"],
        "relief": {**{k: rel.get(k) for k in fi.RELIEF_KEYS}, "ledger": None},
        "ndvi_series": {**fi.ndvi_block(fid, series), "ledger": None},
        "ribbon": {**fi.ribbon_block(fid, rb), "ledger": None},
        "green_end": [{**e, "gap_days": {"v": e["gap_days"]["v"], "l": None}} for e in fi.green_end_block(fid, evs)],
        "ribbon_counts": {"no_green": {**ribbon.no_green_count(rb), "ledger": None}},
        "climate": climate,
    })
    region_doc = notice.stamped({
        "schema_version": SCHEMA_VERSION,
        "region_id": config["region"],
        "mode": MODE,
        "name": config["name"],
        "pack": {"decision": False, "strategies": False, "calendar": False, "trials": False, "reviewer": False},
        "not_loaded": list(config.get("not_loaded") or []),
        "window": {"id": window_id, "bbox_lonlat": config["sources"]["hls"]["window"]["bbox"],
                   "tile": config["sources"]["hls"]["window"]["tile"]},
        "rule_copied_from": config["protocol"]["copied_from"],
        "sources": sources_block(config, pspec),
    })
    out = site_out(config)
    (out / "img").mkdir(parents=True, exist_ok=True)
    for ext in ("png", "json"):
        shutil.copyfile(base.INTERIM_DIR / f"basemap_{window_id}.{ext}", out / "img" / f"basemap.{ext}")
    _write(out / "region.json", region_doc)
    _write(out / f"field_{fid}.json", field_doc)
    return {"region": region_doc, "field": field_doc, "out": _rel(out)}


def _write(path: Path, doc: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Observation region (SA-D79): ribbon, relief and climate, no verdict.")
    ap.add_argument("step", choices=["fetch", "build", "all"])
    ap.add_argument("--config", default=None, help="region config (default: AFTERCROP_CONFIG)")
    ap.add_argument("--workers", type=int, default=4)
    args = ap.parse_args(argv)
    path = args.config or base.config_path()
    _need(Path(path).resolve() != base.DEFAULT_CONFIG_PATH.resolve(),
          "the default region is not an observation region: pass --config or set AFTERCROP_CONFIG")
    config = setup(path)
    if args.step in ("fetch", "all"):
        res = fetch(config, args.workers)
        for r in res["hls"]:
            print(f"hls {r['label']}: {r['granules']} granules {r['by_sensor']}, downloaded {r['granules_downloaded']}, "
                  f"failed {r['failed']}, vi_skipped {r['vi_skipped']}")
        print(f"nasadem {res['nasadem']}; power {res['power']}; network_calls={res['network_calls']}")
    if args.step in ("build", "all"):
        res = build(config)
        f = res["field"]
        counts = {}
        for e in f["green_end"]:
            counts[e["status"]] = counts.get(e["status"], 0) + 1
        c = f["climate"]
        print(f"field a: {f['area_ha']['v']} ha, {f['n_inner_pixels']} inner pixels, green end {counts}, "
              f"relief {f['relief']['slope_class']}, frost {c['frost']['k']} of {c['n']}, "
              f"rain median {c['rain']['median_mm']} mm -> {res['out']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

# Written with an SI tool (Claude, Anthropic) for NASA Space Apps 2026. See AI_USE.md.
"""Soil diagnosis of the frozen bench (plan SA-D77, package T1): read only.

For each trial site, and for each soil file there is (the one the engine used,
``trial_<site>_soil.json``, and the sensitivity side file written before the
freeze, ``trial_<site>_soil_soilgrids.json``), it prints and writes the water
limits the engine derives from the texture: wilting point, field capacity and
saturation per group of equal compartments, and the available water
(field capacity minus wilting point) over 0 to 1 m and 0 to 2.4 m. The field
soils of the L0 chains (``field_<id>_soil.json``) are listed the same way.

The limits come from the same code path as a run: ``cli.load_layers`` (texture
horizons on the compartment grid, Ksat choice "value" as in the trial runs)
then ``soilmodel.make_soil``, whose texture function is AquaCrop-OSPy's
``Soil.calculate_soil_hydraulic_properties`` (Saxton and Rawls 2006). Nothing
is simulated and nothing is changed.

Next to them it counts the simulated seasons with zero dry yield in the trial
runs (``trials_<site>.csv``, ``_a1``, ``_p1``): engine outputs only, rows whose
outcome is "simulated". No observed value is read.

Run from the repository root with the engine environment:
    .venv-engine/bin/python -m pipeline.engine.soil_diagnosis \
        [--config data/runs/config.json] [--data data] [--runs data/runs] \
        [--out data/bench/soil_diagnosis.json] [--fields a,b,c]
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
NOTICE = "Written with an SI tool (Claude, Anthropic) for NASA Space Apps 2026. See AI_USE.md."
SCHEMA_VERSION = 1
DEPTHS_M = (1.0, 2.4)
VARIANTS = ("base", "a1", "p1")
SIMULATED = "simulated"
KSAT_CHOICE = "value"            # the trial runs use the measured Ksat (cli.trial_runs, layers_key)
# The clay above which the plan says the texture function leaves its fitted range. Stated in
# plan SA-D77 item 2, not checked against the paper here.
CLAY_LIMIT_PCT = 60.0
CLAY_LIMIT_SOURCE = "plan SA-D77 item 2 (fitted range of the texture function as the plan states it)"
FORMULA = ("aquacrop 3.1.0 Soil.calculate_soil_hydraulic_properties (Saxton and Rawls 2006), "
           "through pipeline.engine.cli.load_layers and pipeline.engine.soilmodel.make_soil")
SOIL_FILES = (("used", "trial_{site}_soil.json"), ("sensitivity", "trial_{site}_soil_soilgrids.json"))


def sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def rel(path: Path) -> str:
    p = Path(path).resolve()
    return str(p.relative_to(ROOT)) if p.is_relative_to(ROOT) else str(p)


# Water limits ----------------------------------------------------------------------------

def available_water_mm(tops: Sequence[float], bottoms: Sequence[float], wp: Sequence[float],
                       fc: Sequence[float], depth_m: float) -> float:
    """Field capacity minus wilting point over 0..depth_m (mm), compartments cut where they cross it."""
    total = 0.0
    for z0, z1, w, f in zip(tops, bottoms, wp, fc):
        part = max(0.0, min(z1, depth_m) - z0)
        total += (float(f) - float(w)) * part * 1000.0
    return total


def compartment_table(layers: Sequence[tuple]) -> list[dict]:
    """Per compartment: top, bottom, wp, fc, sat (m3/m3), from the engine's own soil object."""
    from .soilmodel import make_soil

    prof = make_soil(layers).profile
    out = []
    for _, r in prof.iterrows():
        bottom = float(r["zBot"])
        top = round(bottom - float(r["dz"]), 6)
        out.append({"top_m": top, "bottom_m": round(bottom, 6), "wp": float(r["th_wp"]),
                    "fc": float(r["th_fc"]), "sat": float(r["th_s"])})
    return out


def texture_by_compartment(layers: Sequence[tuple]) -> list[tuple[float, float, float]]:
    """(sand, clay, om) of each compartment, from the engine layers (thickness first)."""
    from .soilmodel import DZ

    edges = []
    z = 0.0
    for i, (thick, sand, clay, om, *_) in enumerate(layers):
        last = i == len(layers) - 1
        z1 = float("inf") if last else z + float(thick)
        edges.append((z, z1, float(sand), float(clay), float(om)))
        z = z1
    out = []
    bottom = 0.0
    for dz in DZ:
        bottom = round(bottom + dz, 6)
        for z0, z1, sand, clay, om in edges:
            if bottom <= z1 + 1e-6:
                out.append((sand, clay, om))
                break
    return out


def groups(comps: Sequence[Mapping[str, float]], texture: Sequence[tuple[float, float, float]]) -> list[dict]:
    """Consecutive compartments with the same texture and limits, merged."""
    out: list[dict] = []
    for c, (sand, clay, om) in zip(comps, texture):
        key = (round(c["wp"], 9), round(c["fc"], 9), round(c["sat"], 9), round(sand, 6), round(clay, 6), round(om, 6))
        if out and out[-1]["_key"] == key:
            out[-1]["bottom_m"] = c["bottom_m"]
            continue
        out.append({"_key": key, "top_m": c["top_m"], "bottom_m": c["bottom_m"], "sand_pct": round(sand, 2),
                    "clay_pct": round(clay, 2), "om_pct": round(om, 3), "wp": round(c["wp"], 3),
                    "fc": round(c["fc"], 3), "sat": round(c["sat"], 3),
                    "aw_mm_per_m": round((c["fc"] - c["wp"]) * 1000.0, 1)})
    for g in out:
        g.pop("_key")
    return out


def aw_key(depth_m: float) -> str:
    """'aw_mm_0_1m', 'aw_mm_0_2_4m'."""
    return "aw_mm_0_" + f"{depth_m:g}".replace(".", "_") + "m"


def soil_record(path: Path, protocol: Mapping[str, Any]) -> dict:
    """Everything the diagnosis says about one soil file."""
    from .cli import load_layers

    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    layers = load_layers(Path(path), protocol, KSAT_CHOICE)
    comps = compartment_table(layers)
    tops = [c["top_m"] for c in comps]
    bottoms = [c["bottom_m"] for c in comps]
    wp = [c["wp"] for c in comps]
    fc = [c["fc"] for c in comps]
    clays = [float(l["clay_pct"]) for l in doc.get("layers") or ()]
    rec = {
        "file": rel(path),
        "sha256": sha256(path),
        "kind": doc.get("kind"),
        "rule": doc.get("rule"),
        "clay_pct_min": min(clays),
        "clay_pct_max": max(clays),
        "above_clay_limit": max(clays) > CLAY_LIMIT_PCT,
        "groups": groups(comps, texture_by_compartment(layers)),
    }
    for d in DEPTHS_M:
        rec[aw_key(d)] = round(available_water_mm(tops, bottoms, wp, fc, d), 1)
    # Whole-profile storage at the two limits, the scale of storage_end_mm in the run CSV files.
    thick = [b - t for t, b in zip(tops, bottoms)]
    rec["profile_m"] = round(bottoms[-1], 6)
    rec["fc_storage_mm_profile"] = round(sum(f * h for f, h in zip(fc, thick)) * 1000.0, 1)
    rec["wp_storage_mm_profile"] = round(sum(w * h for w, h in zip(wp, thick)) * 1000.0, 1)
    return rec


# Zero yields -----------------------------------------------------------------------------

def zero_yield_counts(path: Path) -> dict | None:
    """Simulated seasons and those with zero dry yield in one trial-run CSV. None if absent."""
    if not Path(path).exists():
        return None
    with open(path, newline="", encoding="utf-8") as fh:
        rows = [r for r in csv.DictReader(fh) if r.get("outcome") == SIMULATED]
    zero = [r for r in rows if r.get("dry_yield_t_ha") not in (None, "") and float(r["dry_yield_t_ha"]) == 0.0]
    by_crop: dict[str, int] = {}
    for r in zero:
        by_crop[r.get("crop") or "?"] = by_crop.get(r.get("crop") or "?", 0) + 1
    return {"file": rel(path), "simulated": len(rows), "zero_yield": len(zero), "zero_by_crop": dict(sorted(by_crop.items()))}


def site_zero_yields(runs: Path, site: str) -> dict:
    out = {}
    for v in VARIANTS:
        name = f"trials_{site}.csv" if v == "base" else f"trials_{site}_{v}.csv"
        out[v] = zero_yield_counts(runs / name)
    return out


# L0 field chains -------------------------------------------------------------------------
#
# The L0 artifacts are on the field chains (chain_<field>.csv), not on the trial chains. This
# part reads only existing outputs (the frozen L0 task file, the chain CSV files) and the weather
# input of the field chains, and runs nothing: it shows what the dead crops look like, so a
# review can tell cold, water or the crop file apart.

TMIN_DAYS_BEFORE = 10


def l0_artifact_counts(l0_path: Path) -> dict | None:
    """Artifacts of the frozen L0 by kind and by field, crop and crop year. None if absent."""
    if not Path(l0_path).exists():
        return None
    doc = json.loads(Path(l0_path).read_text(encoding="utf-8"))
    per = doc.get("fields") or {doc.get("field_id", "?"): doc}
    by_kind: dict[str, int] = {}
    by_crop_year: dict[str, dict[str, int]] = {}
    total = 0
    for fid, d in per.items():
        for a in d.get("artifacts") or ():
            total += 1
            by_kind[a["kind"]] = by_kind.get(a["kind"], 0) + 1
            label = str(a.get("season", "")).split(" ")[-1]          # "2018 2018:maize" -> "2018:maize"
            year, _, crop = label.partition(":")
            key = f"{crop or '?'} {year}"
            by_crop_year.setdefault(key, {})
            by_crop_year[key][fid] = by_crop_year[key].get(fid, 0) + 1
    return {"file": rel(l0_path), "status": doc.get("status"), "n_artifacts": total,
            "n_unexplained": doc.get("n_unexplained"), "by_kind": dict(sorted(by_kind.items())),
            "by_crop_year_and_field": dict(sorted(by_crop_year.items()))}


def dead_crops(chain_csv: Path, tmin=None) -> list[dict]:
    """Every crop that died in a field chain (run 1), a shared prefix counted once.

    ``tmin`` maps a date to the daily minimum temperature of the chain's weather (°C): the
    lowest in the TMIN_DAYS_BEFORE days up to the day of death is reported with its date.
    """
    from datetime import date, timedelta

    if not Path(chain_csv).exists():
        return []
    with open(chain_csv, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    seen, out = set(), []
    for r in rows:
        if r.get("crop_dead") != "true" or r.get("outcome") != SIMULATED:
            continue
        key = (r.get("label"), r.get("sowing"))
        if key in seen:
            continue
        seen.add(key)
        sown, died = date.fromisoformat(r["sowing"]), date.fromisoformat(r["harvest"])
        rec = {"arm": r.get("arm"), "year": r.get("year"), "label": r.get("label"), "crop": r.get("crop"),
               "role": r.get("role"), "sowing": r["sowing"], "death": r["harvest"],
               "end_rule": r.get("end_rule"), "days_after_sowing": (died - sown).days,
               "germinated": r.get("germinated") == "true",
               "days_to_emergence": int(r["days_to_emergence"]) if r.get("days_to_emergence") else None,
               "canopy_max": round(float(r["canopy_max"]), 4) if r.get("canopy_max") else None,
               "storage_end_mm": round(float(r["storage_end_mm"]), 1) if r.get("storage_end_mm") else None,
               "rain_mm": round(float(r["rain_mm"]), 1) if r.get("rain_mm") else None}
        if tmin is not None:
            days = [died - timedelta(days=i) for i in range(TMIN_DAYS_BEFORE)]
            vals = [(tmin[d], d) for d in days if d in tmin]
            if vals:
                low, when = min(vals)
                rec["min_tmin_c_10d_before"] = round(float(low), 2)
                rec["min_tmin_date"] = when.isoformat()
        out.append(rec)
    return out


def deaths_line(deaths: Sequence[Mapping[str, Any]], fc_mm: Sequence[float]) -> str:
    """One line on the dead maize of the field chains, for the next review."""
    maize = [d for d in deaths if d["crop"] == "maize"]
    if not maize:
        return "no dead maize in the field chains"
    das = [d["days_after_sowing"] for d in maize]
    st = [d["storage_end_mm"] for d in maize if d["storage_end_mm"] is not None]
    tm = [d["min_tmin_c_10d_before"] for d in maize if "min_tmin_c_10d_before" in d]
    no_em = sorted({d["label"] for d in maize if not d["days_to_emergence"]})
    months = sorted({d["death"][5:7] for d in maize})
    parts = [f"{len(maize)} dead maize crops (fields counted apart, shared prefixes once), sown on "
             f"{', '.join(sorted({d['sowing'][5:] for d in maize}))}, dead {min(das)} to {max(das)} days after "
             f"sowing (months {', '.join(months)})"]
    if no_em:
        parts.append(f"no emergence in {', '.join(no_em)}")
    if st:
        fc = f" against field capacity {min(fc_mm):.0f} to {max(fc_mm):.0f} mm" if fc_mm else ""
        parts.append(f"profile water at death {min(st):.0f} to {max(st):.0f} mm{fc}")
    if tm:
        parts.append(f"lowest minimum temperature in the {TMIN_DAYS_BEFORE} days before death {min(tm):.1f} "
                     f"to {max(tm):.1f} °C (POWER cell of the field chains)")
    return ". ".join(x[:1].upper() + x[1:] for x in parts) + ". Read from existing outputs, no run."


def field_weather_tmin(config: Mapping[str, Any], data: Path, site: str) -> tuple[dict | None, str | None]:
    """Daily minimum temperature of the field chains' weather, through the engine's own loader."""
    from .cli import load_weather

    try:
        weather, _ = load_weather(config, config["protocol"], data, site)
    except Exception as exc:  # the line is then written without temperatures
        return None, f"{type(exc).__name__}: {exc}"
    return {d.date(): float(t) for d, t in zip(weather["Date"], weather["MinTemp"])}, None


def l0_section(config: Mapping[str, Any], data: Path, runs: Path, bench: Path, fields: Sequence[str],
               field_soils: Mapping[str, Any], weather_site: str) -> dict:
    tmin, weather_error = field_weather_tmin(config, data, weather_site)
    deaths = {fid: dead_crops(runs / f"chain_{fid}.csv", tmin) for fid in fields}
    soils = [s for s in field_soils.values() if s]
    clay = (min(s["clay_pct_min"] for s in soils), max(s["clay_pct_max"] for s in soils)) if soils else None
    aw = (min(s["aw_mm_0_1m"] for s in soils), max(s["aw_mm_0_1m"] for s in soils)) if soils else None
    above = any(s["above_clay_limit"] for s in soils)
    statement = (f"The L0 artifacts are on the field chains of fields {', '.join(fields)} (weather site "
                 f"{weather_site}), which do not use the trial soils. "
                 + (f"Their soils hold {clay[0]:g} to {clay[1]:g}% clay"
                    + (", above" if above else ", inside") + f" the clay limit, with {aw[0]:.0f} to {aw[1]:.0f} mm "
                    "of available water in 0 to 1 m. " if soils else "")
                 + ("The trial-soil diagnosis does not explain them, so no l0_explanations.json was written."
                    if not above else "Check whether the texture function explains them."))
    return {
        "explained_by_this_diagnosis": False if not above else None,
        "statement": statement,
        "artifacts": l0_artifact_counts(bench / "tasks" / "L0.json"),
        "weather_site": weather_site,
        "weather_error": weather_error,
        "dead_crops": deaths,
        "maize_deaths_line": deaths_line([d for v in deaths.values() for d in v],
                                         [s["fc_storage_mm_profile"] for s in soils]),
    }


# Document --------------------------------------------------------------------------------

def protocol_sites(config: Mapping[str, Any]) -> list[str]:
    seasons = (config.get("protocol") or {}).get("trial_site_seasons")
    if not isinstance(seasons, Mapping) or not seasons:
        raise ValueError("protocol.trial_site_seasons is missing in the config JSON")
    return list(seasons)


def diagnose(config_path: Path, data: Path, runs: Path, fields: Sequence[str], *,
             now: datetime | None = None, bench: Path | None = None, weather_site: str = "londrina") -> dict:
    config = json.loads(Path(config_path).read_text(encoding="utf-8"))
    protocol = config["protocol"]
    sites = {}
    for site in protocol_sites(config):
        soils = {}
        for role, pattern in SOIL_FILES:
            p = data / "interim" / pattern.format(site=site)
            soils[role] = soil_record(p, protocol) if p.exists() else None
        sites[site] = {"soils": soils, "zero_yield": site_zero_yields(runs, site)}
    field_soils = {}
    for fid in fields:
        p = data / "interim" / f"field_{fid}_soil.json"
        field_soils[fid] = soil_record(p, protocol) if p.exists() else None
    return {
        "_note": NOTICE,
        "schema_version": SCHEMA_VERSION,
        "written_at": (now or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "decision": "SA-D77 item 2 (read-only diagnosis of the frozen bench)",
        "config": {"file": rel(config_path), "sha256": sha256(config_path)},
        "formula": FORMULA,
        "ksat_choice": KSAT_CHOICE,
        "clay_limit_pct": CLAY_LIMIT_PCT,
        "clay_limit_source": CLAY_LIMIT_SOURCE,
        "aw_definition": "field capacity minus wilting point, mm, over the depth named",
        "zero_yield_definition": "rows with outcome 'simulated' and dry_yield_t_ha equal to 0, engine outputs only",
        "sites": sites,
        "field_soils": field_soils,
        "l0_field_chains": l0_section(config, data, runs, bench or data / "bench", fields, field_soils,
                                      weather_site),
    }


def summary_lines(doc: Mapping[str, Any]) -> list[str]:
    lines = [f"texture function: {doc['formula']}", f"clay limit {doc['clay_limit_pct']}% ({doc['clay_limit_source']})", ""]
    head = f"{'site/field':<12} {'soil':<12} {'clay %':>11} {'AW 0-1 m':>9} {'AW 0-2.4 m':>11}  zero-yield base / a1 / p1"
    lines.append(head)

    def zero(z):
        return "-" if z is None else f"{z['zero_yield']}/{z['simulated']}"

    for site, s in doc["sites"].items():
        zy = s["zero_yield"]
        for role, rec in s["soils"].items():
            if rec is None:
                lines.append(f"{site:<12} {role:<12} {'(no file)':>11}")
                continue
            clay = f"{rec['clay_pct_min']:g}-{rec['clay_pct_max']:g}"
            z = "  ".join(zero(zy[v]) for v in VARIANTS) if role == "used" else ""
            lines.append(f"{site:<12} {role:<12} {clay:>11} {rec['aw_mm_0_1m']:>9.1f} {rec['aw_mm_0_2_4m']:>11.1f}  {z}")
        for role, rec in s["soils"].items():
            if rec:
                for g in rec["groups"]:
                    lines.append(f"    {role:<11} {g['top_m']:.2f}-{g['bottom_m']:.2f} m  clay {g['clay_pct']:5.1f}  "
                                 f"wp {g['wp']:.3f}  fc {g['fc']:.3f}  sat {g['sat']:.3f}  AW {g['aw_mm_per_m']:.0f} mm/m")
    for fid, rec in doc["field_soils"].items():
        if rec:
            clay = f"{rec['clay_pct_min']:g}-{rec['clay_pct_max']:g}"
            lines.append(f"{'field ' + fid:<12} {'used':<12} {clay:>11} {rec['aw_mm_0_1m']:>9.1f} {rec['aw_mm_0_2_4m']:>11.1f}")
    l0 = doc.get("l0_field_chains") or {}
    if l0:
        lines += ["", f"L0: {l0['statement']}", f"L0 deaths: {l0['maize_deaths_line']}"]
    return lines


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="python -m pipeline.engine.soil_diagnosis", description=__doc__.splitlines()[0])
    ap.add_argument("--config", type=Path, default=ROOT / "data" / "runs" / "config.json")
    ap.add_argument("--data", type=Path, default=ROOT / "data")
    ap.add_argument("--runs", type=Path, default=None, help="trial runs folder, default <data>/runs")
    ap.add_argument("--out", type=Path, default=None, help="default <data>/bench/soil_diagnosis.json")
    ap.add_argument("--fields", default="a,b,c", help="field ids of the L0 chains, comma-separated")
    ap.add_argument("--bench", type=Path, default=None, help="bench folder with tasks/L0.json, default <data>/bench")
    ap.add_argument("--weather-site", default="londrina", help="POWER site of the field chains (FIELD_WEATHER_SITE)")
    return ap


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    runs = args.runs or args.data / "runs"
    out = args.out or args.data / "bench" / "soil_diagnosis.json"
    fields = [f.strip() for f in args.fields.split(",") if f.strip()]
    doc = diagnose(args.config, args.data, runs, fields, bench=args.bench, weather_site=args.weather_site)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, indent=1, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
    print("\n".join(summary_lines(doc)))
    print(f"\nwrote {rel(out)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

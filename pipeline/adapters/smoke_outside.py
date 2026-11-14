# Written with an SI tool (Claude, Anthropic) for NASA Space Apps 2026. See AI_USE.md.
"""Engine plumbing smoke outside Brazil, on a field chain (never a trial, never a trial site).

Point: Free State, South Africa (lat -28.5, lon 26.8), a dryland maize and soybean area
with the same September to August season as northern Parana. NASA POWER daily through
``pipeline.adapters.power`` (met call of the config, its own source stretches by probes,
never the Londrina cell). Soil: an invented clay profile, labelled as such. Nothing here
says anything about Parana or about the trials: it checks that a POWER series runs
through the engine, how long each crop season takes, and that the water balance closes.

    .venv/bin/python -m pipeline.adapters.smoke_outside prepare        # download, input tree
    .venv-engine/bin/python -m pipeline.engine.cli field --config data/runs/smoke_outside/input/config.json \
        --data data/runs/smoke_outside/input --runs data/runs/smoke_outside ...   # (make smoke-outside)
    .venv-engine/bin/python -m pipeline.adapters.smoke_outside summarize   # timing per crop season, closure

Outputs in ``data/runs/smoke_outside/``: the CLI files (chain CSVs, timing.json,
artifacts.json) and, added to timing.json, ``per_crop_season`` and ``closure``.
"""
from __future__ import annotations

import argparse
import csv
import json
import shutil
import statistics
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "data" / "runs" / "smoke_outside"
INPUT = OUT / "input"
SITE = "smoke_freestate"
FIELD = "smoke_clay"
POINT = {"lat": -28.5, "lon": 26.8}
LABEL = "example, invented: engine plumbing smoke outside Brazil, not a result"
SOIL = {"_note": LABEL, "source": "invented clay profile, no measurement", "layers": [
    {"top_m": 0.0, "bottom_m": 0.3, "sand_pct": 15.0, "clay_pct": 60.0, "om_pct": 2.0},
    {"top_m": 0.3, "bottom_m": 2.4, "sand_pct": 12.0, "clay_pct": 65.0, "om_pct": 0.8},
]}
TOL_MM = 1.0


def prepare() -> Path:
    """POWER at the point (cached), its source stretches, the smoke config and soil."""
    from pipeline.adapters import base, power

    config = base.load_config()
    cfg = config["sources"]["power"]
    call = next(c for c in cfg["calls"] if c["id"] == "met")
    spec = {"source_id": cfg["source_id"], "url": cfg["url"], "license": cfg["license"], "site": SITE,
            "lat": POINT["lat"], "lon": POINT["lon"], "call": "met", "parameters": list(call["parameters"]),
            "start": call["start"], "end": call["end"], "time_standard": call["time_standard"],
            "community": cfg["community"]}
    path = power.fetch(spec)
    doc = json.loads(path.read_text(encoding="utf-8"))
    lon, lat, elevation = doc["geometry"]["coordinates"]
    segments = power.source_segments(spec)
    raw = INPUT / "raw" / cfg["source_id"]
    raw.mkdir(parents=True, exist_ok=True)
    for f in (path, base.meta_path(path)):
        shutil.copyfile(f, raw / f.name)
    interim = INPUT / "interim"
    interim.mkdir(parents=True, exist_ok=True)
    (interim / "power_summary.json").write_text(json.dumps(
        {"_note": LABEL, "point": POINT, "source_segments": {"met": segments}}, indent=2) + "\n", encoding="utf-8")
    (interim / f"field_{FIELD}_soil.json").write_text(json.dumps(SOIL, indent=2) + "\n", encoding="utf-8")
    smoke = json.loads(json.dumps(config, default=str))
    smoke["_note"] = LABEL
    smoke["trial_sites"] = [{"id": SITE, "name": "Free State smoke point", "lat": POINT["lat"],
                             "lon": POINT["lon"], "altitude_m": float(elevation),
                             "coord_source": "chosen for the smoke; altitude from the POWER response",
                             "returned_point": [lon, lat]}]
    out = INPUT / "config.json"
    out.write_text(json.dumps(smoke, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"-> {out.relative_to(ROOT)}; POWER {path.name}, elevation {elevation} m; "
          f"network_calls={base.network_calls}")
    return out


def _rows(path: Path) -> list[dict[str, str]]:
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def summarize(runs: Path = OUT) -> dict[str, Any]:
    """Seconds per crop season and the L0-like closure checks, added to timing.json."""
    first, second = _rows(runs / f"chain_{FIELD}.csv"), _rows(runs / f"chain_{FIELD}_repeat.csv")
    sims = [r for r in first if r["outcome"] == "simulated"]
    by_crop: dict[str, list[float]] = {}
    for r in sims:
        by_crop.setdefault(r["crop"], []).append(float(r["seconds"]))
    per = {c: {"seasons": len(v), "mean_seconds": statistics.fmean(v), "max_seconds": max(v),
               "total_seconds": sum(v)} for c, v in by_crop.items()}
    gaps = [float(r["handover_gap_mm"]) for r in first if r["handover_gap_mm"]]
    resid = [abs(float(r["balance_residual_mm"])) for r in sims if r["balance_residual_mm"]]
    drop = {"run_key", "variant", "run", "init_seconds", "seconds"}
    same = [{k: v for k, v in r.items() if k not in drop} for r in first] == \
           [{k: v for k, v in r.items() if k not in drop} for r in second]
    closure = {
        "tolerance_mm": TOL_MM,
        "handover_gaps": len(gaps), "max_handover_gap_mm": max(gaps) if gaps else None,
        "seasons": len(sims), "max_abs_balance_residual_mm": max(resid) if resid else None,
        "repeat_identical": same,
        "outcomes": sorted({r["outcome"] for r in first}),
        "end_rules": {e: sum(1 for r in sims if r["end_rule"] == e) for e in sorted({r["end_rule"] for r in sims})},
        "passes": bool(gaps) and max(gaps) <= TOL_MM and bool(resid) and max(resid) <= TOL_MM and same,
    }
    path = runs / "timing.json"
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc["smoke_outside"] = {"_note": LABEL, "point": POINT, "field": FIELD,
                            "per_crop_season": per, "closure": closure}
    path.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return doc["smoke_outside"]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Engine plumbing smoke outside Brazil (Free State, South Africa).")
    ap.add_argument("command", choices=("prepare", "summarize"))
    args = ap.parse_args(argv)
    if args.command == "prepare":
        prepare()
    else:
        print(json.dumps(summarize(), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())

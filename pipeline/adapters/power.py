# Written with an SI tool (Claude, Anthropic) for NASA Space Apps 2026. See AI_USE.md.
"""NASA POWER daily point data for the trial sites (plan, sheet M1, criterion M1.3).

Two calls per site: the meteorology for the engine (local solar time) and a
separate call for IMERG_PRECTOT with time-standard=UTC. -999 becomes missing,
never zero. The source of each stretch of the series is recorded.

Run from the repository root:  python -m pipeline.adapters.power
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from pipeline.adapters import base

FILL = -999.0


# Contract ------------------------------------------------------------------

def specs(config: dict | None = None) -> list[dict]:
    config = config or base.load_config()
    cfg = config["sources"]["power"]
    out = []
    for site in config["trial_sites"]:
        for call in cfg["calls"]:
            out.append({
                "source_id": cfg["source_id"], "url": cfg["url"], "license": cfg["license"],
                "site": site["id"], "lat": float(site["lat"]), "lon": float(site["lon"]),
                "call": call["id"], "parameters": list(call["parameters"]),
                "start": call["start"], "end": call["end"],
                "time_standard": call["time_standard"], "community": cfg["community"],
            })
    return out


def request(spec: dict) -> tuple[str, dict, str]:
    params = {
        "parameters": ",".join(spec["parameters"]),
        "community": spec["community"],
        "latitude": f"{spec['lat']:.4f}",
        "longitude": f"{spec['lon']:.4f}",
        "start": spec["start"].replace("-", ""),
        "end": spec["end"].replace("-", ""),
        "format": "JSON",
        "time-standard": spec["time_standard"],
        "header": "true",
    }
    name = f"{spec['site']}_{spec['call']}_{params['start']}_{params['end']}_{spec['time_standard']}.json"
    return spec["url"], params, name


def fetch(spec: dict) -> Path:
    url, params, name = request(spec)
    return base.cached_get(spec["source_id"], url, params=params, filename=name, timeout=600)


def _load(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def read(path: Path) -> pd.DataFrame:
    """Daily table indexed by date, one column per parameter. Fill values become NaN."""
    doc = _load(path)
    fill = float(doc.get("header", {}).get("fill_value", FILL))
    cols = {}
    for name, series in doc["properties"]["parameter"].items():
        s = pd.Series(series, dtype="float64")
        s.index = pd.to_datetime(s.index, format="%Y%m%d")
        cols[name] = s.where(~np.isclose(s, fill), np.nan)  # never zero
    df = pd.DataFrame(cols).sort_index()
    df.index.name = "date"
    return df


def provenance(spec: dict) -> dict:
    url, params, name = request(spec)
    path = base.raw_path(spec["source_id"], url, params, name)
    doc = _load(path)
    header = doc.get("header", {})
    version = f"POWER {header.get('api', {}).get('version', '?')}, {header.get('title', '')}".strip()
    return base.base_provenance(
        path, spec["license"], version,
        site=spec["site"], call=spec["call"], time_standard=spec["time_standard"],
        sources=header.get("sources"), fill_value=header.get("fill_value"),
        requested_point={"lat": spec["lat"], "lon": spec["lon"]},
        returned_point=doc.get("geometry", {}).get("coordinates"),
        messages=doc.get("messages"),
    )


# Source by stretch -----------------------------------------------------------
# The response header lists the union of sources for the whole request. To know
# which source covers which days, ask one-day questions per parameter at both
# ends and bisect where the answer changes. Probes are cached like any request.

def _probe(spec: dict, param: str, day: pd.Timestamp) -> tuple[str, ...]:
    d = day.strftime("%Y-%m-%d")
    s = {**spec, "parameters": [param], "start": d, "end": d, "call": f"probe-{param}"}
    return tuple(_load(fetch(s)).get("header", {}).get("sources") or ())


def source_segments(spec: dict, max_probes: int = 40) -> list[dict]:
    """[{parameter, start, end, sources}] covering the request, one change at most per parameter."""
    start, end = pd.Timestamp(spec["start"]), pd.Timestamp(spec["end"])
    out = []
    for param in spec["parameters"]:
        s_lo, s_hi = _probe(spec, param, start), _probe(spec, param, end)
        if s_lo == s_hi:
            out.append({"parameter": param, "start": start.date().isoformat(),
                        "end": end.date().isoformat(), "sources": list(s_lo), "probes": 2})
            continue
        lo, hi, n = start, end, 2
        while (hi - lo).days > 1 and n < max_probes:
            mid = lo + (hi - lo) // 2
            s_mid = _probe(spec, param, mid.normalize())
            n += 1
            if s_mid == s_lo:
                lo = mid.normalize()
            elif s_mid == s_hi:
                hi = mid.normalize()
            else:
                raise RuntimeError(f"{param}: third source set {s_mid} at {mid.date()}, bisect by hand")
        out.append({"parameter": param, "start": start.date().isoformat(),
                    "end": lo.date().isoformat(), "sources": list(s_lo), "probes": n})
        out.append({"parameter": param, "start": hi.date().isoformat(),
                    "end": end.date().isoformat(), "sources": list(s_hi), "probes": n})
    return out


def summary(df: pd.DataFrame) -> dict:
    out = {}
    for c in df.columns:
        s = df[c]
        valid = s.dropna()
        out[c] = {
            "days": int(len(s)), "missing": int(s.isna().sum()),
            "first_valid": valid.index.min().date().isoformat() if len(valid) else None,
            "last_valid": valid.index.max().date().isoformat() if len(valid) else None,
            "missing_after_first_valid": int(s[s.index >= valid.index.min()].isna().sum()) if len(valid) else None,
            "min": float(valid.min()) if len(valid) else None,
            "max": float(valid.max()) if len(valid) else None,
        }
    return out


def run(config: dict | None = None, probe_site: str = "londrina") -> dict:
    """Fetch every site and call, record sources by stretch, write a summary."""
    all_specs = specs(config)
    result = {"files": [], "source_segments": {}, "probe_site": probe_site}
    for spec in all_specs:
        path = fetch(spec)
        df = read(path)
        result["files"].append({**provenance(spec), "summary": summary(df)})
    # Source stretches come from global products, so one site answers for all.
    for spec in all_specs:
        if spec["site"] == probe_site:
            result["source_segments"][spec["call"]] = source_segments(spec)
    base.write_json(base.INTERIM_DIR / "power_summary.json", result)
    return result


def main() -> int:
    result = run()
    for f in result["files"]:
        print(f["site"], f["call"], f["time_standard"], f["bytes"], "bytes", "sources", f["sources"])
    for call, segs in result["source_segments"].items():
        for s in segs:
            print(f"  {call} {s['parameter']}: {s['start']}..{s['end']} {s['sources']}")
    print(f"network_calls={base.network_calls}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

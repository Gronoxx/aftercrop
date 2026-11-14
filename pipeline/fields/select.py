# Written with an SI tool (Claude, Anthropic) for NASA Space Apps 2026. See AI_USE.md.
"""Choice of Field A, B and C among the drawn candidates (plan 8.4 step 4, 13.7).

The criterion is fixed here, before any simulation (``CRITERION_ID``), and reads only
the choice seasons (``protocol.field.choice_seasons``, D9). Those seasons then stay out
of the field order (plan 13.7, rule 5).

Inputs per candidate: its end-of-green events (``events.py``) and one slope number
from the relief input (``relief.py``, another module), larger meaning steeper, the same
unit for every candidate: the median slope in percent over the polygon
(``protocol.field.slope: median``).

Candidates (``protocol.field.candidates``, SA-D63): product mode needs between the two
bounds (8 to 12), every one west of ``protocol.field.product_area.lon_max``. Rehearsal
mode (``--rehearsal``) needs at least 2, every one in the development strip.

Criterion ``M2-select-v1``:

1. Exit score of a candidate: the midpoint of its end-of-green interval, in days after
   1 September of the season, averaged over the choice seasons. Only candidates with
   status "ok" in every choice season have a score.
2. Field A: the smallest score (soybean that leaves early). Field B: the largest score
   of the others (soybean that leaves late). Ties go to the smaller candidate id. With
   fewer than two scores, or no difference between A and B, neither is chosen.
3. Field C: the largest slope among the candidates that are not A or B (stronger
   relief). Ties go to the smaller candidate id. Without a slope, C is not chosen.

A and B are chosen before C, because the field order needs the exit contrast
[DECIDE]. Nothing here is a claim about the fields: green ending is not harvest.

Outputs: ``data/interim/fields_selection.json`` (the table and the choice) and
``data/interim/fields_selected.geojson`` (ids "a", "b", "c" with their criterion, the
only properties). Copy the latter to ``config/fields/fields.geojson`` by hand.

Run from the repository root (reads only the choice seasons of the HLS cache):
    python -m pipeline.fields.select config/fields/candidates.geojson --slopes <slopes.json> [--rehearsal]
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections.abc import Mapping, Sequence
from datetime import date
from fractions import Fraction
from pathlib import Path

from pipeline.adapters import base
from pipeline.bench.common import ProtocolError
from pipeline.common import dekad
from pipeline.fields import events, geometry, ndvi_series

CRITERION_ID = "M2-select-v1"
ROLES = ("a", "b", "c")
CRITERION = {
    "a": "earliest end of green among the drawn candidates in {seasons}",
    "b": "latest end of green among the drawn candidates in {seasons}",
    "c": "steepest slope among the other drawn candidates",
}


def choice_seasons(protocol: Mapping) -> list[str]:
    seasons = geometry.locked(geometry.field_section(protocol), "choice_seasons")
    if not isinstance(seasons, list) or not seasons:
        raise ProtocolError("protocol.field.choice_seasons must be a non-empty list")
    for s in seasons:
        dekad.season_start_year(s)
    return list(seasons)


def fallback_seasons(protocol: Mapping) -> list[str]:
    """``protocol.field.choice_fallback.older_seasons_in_order`` (protocol-v1.1): the seasons tried,
    one at a time, when fewer than 2 candidates are ok in the choice seasons. Empty if absent."""
    fb = geometry.field_section(protocol).get("choice_fallback") or {}
    seasons = fb.get("older_seasons_in_order") or []
    if not isinstance(seasons, list):
        raise ProtocolError("protocol.field.choice_fallback.older_seasons_in_order must be a list")
    for s in seasons:
        dekad.season_start_year(s)
    return list(seasons)


def choose_in_order(attempts: Sequence[Sequence[str]], table_for) -> tuple[dict, list[str], list[dict], list[dict]]:
    """Try each season list in order until Field A and B exist (protocol-v1.1). ``table_for(seasons)``
    gives the candidate table of those seasons. Returns the choice, the seasons used and the log of
    every attempt. Pure apart from ``table_for``."""
    log = []
    choice, used, table = None, list(attempts[0]), None
    for seasons in attempts:
        table = table_for(list(seasons))
        choice = choose(table)
        log.append({"seasons": list(seasons), "a": choice["a"], "b": choice["b"],
                    "n_ok": sum(1 for r in table if r["exit_score_days"] is not None), "notes": list(choice["notes"])})
        used = list(seasons)
        if choice["a"] is not None and choice["b"] is not None:
            break
    return choice, used, log, table


REHEARSAL_MIN_CANDIDATES = 2  # two scores are the least that choose() can compare


def candidate_bounds(protocol: Mapping) -> tuple[int, int]:
    """``protocol.field.candidates`` as (least, most)."""
    span = geometry.locked(geometry.field_section(protocol), "candidates")
    if not isinstance(span, list) or len(span) != 2:
        raise ProtocolError("protocol.field.candidates must be [least, most]")
    lo = geometry.integer(span[0], "protocol.field.candidates[0]", 1)
    hi = geometry.integer(span[1], "protocol.field.candidates[1]", lo)
    return lo, hi


def check_count(n: int, protocol: Mapping, rehearsal: bool = False) -> None:
    """Refuse a candidates file with too few or too many polygons for the mode."""
    if rehearsal:
        if n < REHEARSAL_MIN_CANDIDATES:
            raise ValueError(f"{n} candidate(s): rehearsal mode needs at least {REHEARSAL_MIN_CANDIDATES}")
        return
    lo, hi = candidate_bounds(protocol)
    if not lo <= n <= hi:
        raise ValueError(f"{n} candidate(s): product mode needs {lo} to {hi} (protocol.field.candidates)")


def exit_day(event: Mapping) -> Fraction:
    """Midpoint of the interval, in days after 1 September of the event's season."""
    origin = date(dekad.season_start_year(event["season"]), 9, 1).toordinal()
    lo, hi = date.fromisoformat(event["low"]).toordinal(), date.fromisoformat(event["high"]).toordinal()
    return Fraction(lo + hi, 2) - origin


def _slope(v, cid: str) -> float | None:
    if v is None:
        return None
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        raise ValueError(f"slope of {cid} must be a finite number or null, got {v!r}")
    return float(v)


def candidate_table(green_end: Mapping[str, Sequence[Mapping]], slopes: Mapping[str, object],
                    seasons: Sequence[str]) -> list[dict]:
    """One row per candidate: exits in the choice seasons, score and slope. Pure."""
    rows = []
    for cid in sorted(set(green_end) | set(slopes)):
        by = {e["season"]: e for e in green_end.get(cid, [])}
        exits = [by.get(s) for s in seasons]
        ok = all(e is not None and e["status"] == "ok" for e in exits)
        score = sum(exit_day(e) for e in exits) / len(exits) if ok else None
        rows.append({
            "candidate_id": cid,
            "exits": [{"season": s, "low": None if e is None else e["low"], "high": None if e is None else e["high"],
                       "status": "no_data" if e is None else e["status"]} for s, e in zip(seasons, exits)],
            "exit_score_days": None if score is None else float(score),
            "slope": _slope(slopes.get(cid), cid),
        })
    return rows


def choose(table: Sequence[Mapping]) -> dict:
    """``{"a", "b", "c": candidate id or None, "notes": [...]}`` by criterion M2-select-v1. Pure."""
    notes = []
    scored = [r for r in table if r["exit_score_days"] is not None]
    a = b = None
    if len(scored) < 2:
        notes.append("fewer than 2 candidates with status ok in every choice season: no Field A or B")
    else:
        early = sorted(scored, key=lambda r: (r["exit_score_days"], r["candidate_id"]))[0]
        late = sorted((r for r in scored if r is not early),
                      key=lambda r: (-r["exit_score_days"], r["candidate_id"]))[0]
        if late["exit_score_days"] > early["exit_score_days"]:
            a, b = early, late
        else:
            notes.append("no difference in exit score between candidates: no Field A or B")
    taken = {r["candidate_id"] for r in (a, b) if r is not None}
    rest = [r for r in table if r["candidate_id"] not in taken and r["slope"] is not None]
    c = sorted(rest, key=lambda r: (-r["slope"], r["candidate_id"]))[0] if rest else None
    if c is None:
        notes.append("no other candidate with a slope: no Field C")
    out = {k: None if r is None else r["candidate_id"] for k, r in zip(ROLES, (a, b, c))}
    if a is not None:
        out["exit_difference_days"] = b["exit_score_days"] - a["exit_score_days"]
    out["notes"] = notes
    return out


def criterion_text(role: str, seasons: Sequence[str]) -> str:
    return f"Field {role.upper()}: " + CRITERION[role].format(seasons=", ".join(seasons))


def selection_doc(table: Sequence[Mapping], choice: Mapping, seasons: Sequence[str], rule: events.EventRule) -> dict:
    return {
        "criterion_id": CRITERION_ID,
        "choice_seasons": list(seasons),
        "note": "These seasons stay out of the field order (plan 13.7). Green ending is not harvest.",
        "criteria": {r: criterion_text(r, seasons) for r in ROLES},
        "rule": rule.as_dict(),
        "candidates": list(table),
        "choice": dict(choice),
    }


def selected_geojson(candidates: Mapping, choice: Mapping, seasons: Sequence[str]) -> dict:
    """Fields a, b and c with their criterion, geometry copied from the candidates. Checked by geometry."""
    by_id = {f["properties"]["field_id"]: f for f in candidates["features"]}
    feats = []
    for role in ROLES:
        cid = choice.get(role)
        if cid is None:
            continue
        feats.append({"type": "Feature", "properties": {"field_id": role, "criterion": criterion_text(role, seasons)},
                      "geometry": by_id[cid]["geometry"]})
    doc = {"type": "FeatureCollection", "features": feats}
    if "window_id" in candidates:
        doc["window_id"] = candidates["window_id"]
    if feats:
        geometry.parse_fields(doc)
    return doc


def read_slopes(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        doc = json.load(f)
    if not isinstance(doc, dict):
        raise ValueError(f"{path}: expected {{candidate_id: slope}}")
    return {str(k): _slope(v, str(k)) for k, v in doc.items()}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Choose Field A, B and C among the candidates (plan 8.4 step 4).")
    ap.add_argument("geojson", type=Path, help="candidates FeatureCollection, such as config/fields/candidates.geojson")
    ap.add_argument("--slopes", type=Path, required=True,
                    help="JSON {candidate_id: median slope}, larger is steeper")
    ap.add_argument("--rehearsal", action="store_true",
                    help="development strip only (protocol.field.development_strip), at least 2 candidates")
    args = ap.parse_args(argv)
    config = base.load_config()
    rule = events.EventRule.from_protocol(config)  # D10 and D12 guard
    seasons = choice_seasons(config)
    grule = geometry.GeometryRule.from_protocol(config)
    candidates = geometry.read_geojson(args.geojson)
    fields = geometry.load_fields(args.geojson, config, rehearsal=args.rehearsal)  # SA-D63 area
    check_count(len(fields), config, rehearsal=args.rehearsal)
    slopes = read_slopes(args.slopes)
    unknown = sorted(set(slopes) - {f["field_id"] for f in fields})
    if unknown:
        raise ValueError(f"slopes for unknown candidates: {unknown}")
    def table_for(sel: list[str]) -> list[dict]:
        records = ndvi_series.index_records(config, sel)  # the seasons of this attempt only
        green_end = {}
        for f in fields:
            rows, _ = ndvi_series.series_from_records(records, geometry.FieldMasks(f, grule),
                                                      **ndvi_series.fmask_settings(config))
            green_end[f["field_id"]] = events.green_ends(rows.get("whole", []), sel, rule)
        return candidate_table(green_end, slopes, sel)

    attempts = [seasons] + [[s] for s in fallback_seasons(config)]
    choice, seasons, log, table = choose_in_order(attempts, table_for)
    doc = selection_doc(table, choice, seasons, rule)
    doc["attempts"] = log   # protocol-v1.1: every season tried, in order, published
    base.write_json(base.INTERIM_DIR / "fields_selection.json", doc)
    out = base.write_json(base.INTERIM_DIR / "fields_selected.geojson", selected_geojson(candidates, choice, seasons))
    print(f"A={choice['a']} B={choice['b']} C={choice['c']} {choice['notes']} -> {out.relative_to(base.REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

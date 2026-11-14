# Written with an SI tool (Claude, Anthropic) for NASA Space Apps 2026. See AI_USE.md.
"""Inputs of the N1 noise floor: field halves and L30 against S30 (plan 8.4 step 9, M2.6).

Two nulls of the same field (plan 11.2 and 11.5), each giving the end-of-green event
of a season (``events.py``) twice:

- halves: the inner pixels split in two equal halves across the field's principal
  axis (``geometry.halves``), each with its own series from the same granules;
- L30 against S30: the field's series split by sensor.

A pair is one field and one season where both members have status "ok". Any other
status leaves the pair out, listed in ``skipped`` with its reason. A half with fewer
possible pixels than ``min_clean_pixels`` gives no halves pairs for that field.

Disagreement of a pair, in days: the absolute difference of one point of the two
intervals. ``interval_point`` is "low", "high" or "midpoint", read from
``protocol.tests.N1.interval_point`` (D12: the midpoint, a representative value only,
never shown as an observed date). The CLI flag ``--point`` is only a check: it must
agree with the protocol. A midpoint difference can be a half day.

The N1 percentile and its method (``tests.N1.percentile``, ``percentile_method``) are
read from the protocol too, through ``pipeline.bench.n1.N1Rule``, and recorded in the
file. The floor itself is computed by ``n1.evaluate``.

``noise_floor.json`` keeps the pooled lists that ``pipeline.bench.n1.evaluate`` takes:

    halves, l30_vs_s30 = noise.n1_inputs(doc)
    n1.evaluate(halves, l30_vs_s30, n1.N1Rule.from_protocol(protocol, ...))

with ``n_halves`` and ``n_l30_vs_s30`` (M2.6: number of pairs reported), every pair
with its two intervals, and the skipped ones. Every season of the ribbon enters: the
choice seasons are kept out of the field order, not out of the noise floor.

Run from the repository root:
    python -m pipeline.fields.noise config/fields/fields.geojson [--rehearsal]
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Mapping, Sequence
from datetime import date
from fractions import Fraction
from pathlib import Path

from pipeline.adapters import base
from pipeline.bench import common, n1
from pipeline.fields import events, geometry, ndvi_series, ribbon

SCHEMA_VERSION = "1"
POINTS = ("low", "high", "midpoint")
NULLS = ("halves", "l30_vs_s30")
SENSORS = ("L30", "S30")


def point_from_protocol(protocol: Mapping, given: str | None = None) -> str:
    """``protocol.tests.N1.interval_point``. ``given`` is only a check: it must agree."""
    sec = common.protocol_section(protocol, "N1")
    if sec.get("interval_point") is None:
        raise common.ProtocolError("protocol.tests.N1.interval_point is missing: the protocol holds it, "
                                   "not the command line")
    point = common.setting(sec, "interval_point", "tests.N1", given)
    if point not in POINTS:
        raise common.ProtocolError(f"protocol.tests.N1.interval_point must be one of {POINTS}, got {point!r}")
    return point


def interval_point(event: Mapping, point: str) -> Fraction:
    """The chosen point of an event interval, as a day number (exact)."""
    if point not in POINTS:
        raise ValueError(f"point must be one of {POINTS}")
    lo, hi = date.fromisoformat(event["low"]).toordinal(), date.fromisoformat(event["high"]).toordinal()
    if lo > hi:
        raise ValueError(f"{event['season']}: low after high")
    return {"low": Fraction(lo), "high": Fraction(hi), "midpoint": Fraction(lo + hi, 2)}[point]


def disagreement_days(a: Mapping, b: Mapping, point: str) -> int | float:
    d = abs(interval_point(a, point) - interval_point(b, point))
    return int(d) if d.denominator == 1 else float(d)


def _brief(e: Mapping | None) -> dict | None:
    if e is None:
        return None
    return {"low": e["low"], "high": e["high"], "gap_days": e["gap_days"], "status": e["status"]}


def pair_events(events_a: Sequence[Mapping], events_b: Sequence[Mapping], *, null: str, field_id: str,
                point: str) -> tuple[list[dict], list[dict]]:
    """Pairs of seasons where both events are "ok", and the seasons left out. Pure."""
    if null not in NULLS:
        raise ValueError(f"null must be one of {NULLS}")
    a = {e["season"]: e for e in events_a}
    b = {e["season"]: e for e in events_b}
    pairs, skipped = [], []
    for s in sorted(set(a) | set(b)):
        ea, eb = a.get(s), b.get(s)
        if ea is None or eb is None or ea["status"] != "ok" or eb["status"] != "ok":
            skipped.append({"null": null, "field_id": field_id, "season": s,
                            "status_a": None if ea is None else ea["status"],
                            "status_b": None if eb is None else eb["status"],
                            "reason": "both members need status ok"})
            continue
        pairs.append({"null": null, "field_id": field_id, "season": s, "a": _brief(ea), "b": _brief(eb),
                      "disagreement_days": disagreement_days(ea, eb, point)})
    return pairs, skipped


def field_pairs(field_id: str, rows: Mapping[str, Sequence[Mapping]], seasons: Sequence[str],
                rule: events.EventRule, point: str) -> tuple[list[dict], list[dict]]:
    """Both nulls of one field. ``rows`` has the series of "whole", "half_a" and "half_b". Pure."""
    pairs, skipped = [], []
    halves = [rows.get("half_a") or [], rows.get("half_b") or []]
    small = [max((r["n_possible"] for r in h), default=0) < rule.clean.min_clean_pixels for h in halves]
    if any(small):
        skipped.append({"null": "halves", "field_id": field_id, "season": None, "status_a": None, "status_b": None,
                        "reason": f"a half has fewer than {rule.clean.min_clean_pixels} possible pixels or no rows"})
    else:
        p, s = pair_events(events.green_ends(halves[0], seasons, rule), events.green_ends(halves[1], seasons, rule),
                           null="halves", field_id=field_id, point=point)
        pairs += p
        skipped += s
    whole = rows.get("whole") or []
    by = [events.green_ends([r for r in whole if r["sensor"] == sensor], seasons, rule) for sensor in SENSORS]
    p, s = pair_events(by[0], by[1], null="l30_vs_s30", field_id=field_id, point=point)
    return pairs + p, skipped + s


def build_doc(pairs: Sequence[Mapping], skipped: Sequence[Mapping], *, fields: Sequence[str],
              seasons: Sequence[str], rule: events.EventRule, point: str,
              n1_rule: "n1.N1Rule | None" = None) -> dict:
    halves = [p["disagreement_days"] for p in pairs if p["null"] == "halves"]
    sensors = [p["disagreement_days"] for p in pairs if p["null"] == "l30_vs_s30"]
    return {
        "schema_version": SCHEMA_VERSION,
        "id": "N1_inputs",
        "note": ("Inputs of N1 (plan 11.2, 11.5). The floor is computed by pipeline.bench.n1.evaluate("
                 "halves_days, l30_vs_s30_days, rule), the larger of the two percentiles."),
        "unit": "days",
        "event": (f"end of the first green cycle ({events.METHOD_ID}): last clean observation above half "
                  "height to the first at or below it"),
        "interval_point": point,
        "percentile": None if n1_rule is None else float(n1_rule.percentile),
        "percentile_method": None if n1_rule is None else n1_rule.method,
        "fields": list(fields),
        "seasons": list(seasons),
        "rule": rule.as_dict(),
        "halves_days": halves,
        "l30_vs_s30_days": sensors,
        "n_halves": len(halves),
        "n_l30_vs_s30": len(sensors),
        "pairs": list(pairs),
        "skipped": list(skipped),
    }


def n1_inputs(doc: Mapping) -> tuple[list, list]:
    """``(halves_days, l30_vs_s30_days)`` of a noise_floor.json, after its checks."""
    if doc.get("schema_version") != SCHEMA_VERSION or doc.get("unit") != "days":
        raise ValueError("not a noise_floor.json of this schema")
    h, s = list(doc["halves_days"]), list(doc["l30_vs_s30_days"])
    if len(h) != doc["n_halves"] or len(s) != doc["n_l30_vs_s30"]:
        raise ValueError("pair counts disagree with the lists")
    return h, s


def out_path() -> Path:
    return base.INTERIM_DIR / "noise_floor.json"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="N1 inputs: field halves and L30 against S30 (plan 8.4 step 9).")
    ap.add_argument("geojson", type=Path, help="fields FeatureCollection, such as config/fields/fields.geojson")
    ap.add_argument("--point", choices=POINTS,
                    help="only a check: refused if it differs from protocol.tests.N1.interval_point")
    ap.add_argument("--rehearsal", action="store_true",
                    help="development strip only (protocol.field.development_strip), never a product field")
    args = ap.parse_args(argv)
    config = base.load_config()
    rule = events.EventRule.from_protocol(config)  # D10 and D12 guard
    point = point_from_protocol(config, args.point)
    n1_rule = n1.N1Rule.from_protocol(config)  # percentile and method from the protocol
    grule = geometry.GeometryRule.from_protocol(config)
    seasons = ribbon.ribbon_seasons(config)
    fields = geometry.load_fields(args.geojson, config, rehearsal=args.rehearsal)
    records = ndvi_series.index_records(config)
    pairs, skipped = [], []
    for f in fields:
        rows, _ = ndvi_series.series_from_records(records, geometry.FieldMasks(f, grule, with_halves=True),
                                                  **ndvi_series.fmask_settings(config))
        p, s = field_pairs(f["field_id"], rows, seasons, rule, point)
        pairs += p
        skipped += s
    doc = build_doc(pairs, skipped, fields=[f["field_id"] for f in fields], seasons=seasons, rule=rule, point=point,
                    n1_rule=n1_rule)
    out = base.write_json(out_path(), doc)
    print(f"halves {doc['n_halves']} pairs, L30 vs S30 {doc['n_l30_vs_s30']} pairs, "
          f"{len(skipped)} left out -> {out.relative_to(base.REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

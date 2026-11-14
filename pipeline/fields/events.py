# Written with an SI tool (Claude, Anthropic) for NASA Space Apps 2026. See AI_USE.md.
"""End of the first green cycle per season, as the real interval between two clean observations.

Plan 7.7, 8.4 step 8, 12.3, sheet M2 (M2.3, M2.4) and PROTOCOL.md section 15, D12
(SA-D62). The end of the first green cycle is a proxy for the soybean phase, never a
crop classification. Green ending is not harvest and never a sowing date.

The uncertainty of the event is the event's own interval: from ``low``, the last clean
observation above half height, to ``high``, the first clean observation at or below it.
No fixed plus-or-minus margin exists anywhere in this code. ``gap_days`` is
``high - low`` and changes with each season and field.

Observations are the clean rows of the field series (``ribbon.CleanRule``). Rows of the
same date (L30 and S30) collapse to the larger NDVI, as the ribbon cells do, so the
order of two sensors on one day never decides an event.

Rule ``M2-green-end-v2``. Every number comes from ``protocol.field`` (``EventRule``):

- Season: 1 September to 31 August (``pipeline.common.dekad``).
- ``M(t)``: the running maximum of the clean NDVI from 1 September of the season up to
  ``t`` (``green_end_reference: running_max``).
- Exit: the first pair of consecutive clean observations of the season (``low``,
  ``high``) with ``high`` in ``green_end_exit_window`` (1 Dec to 31 May), NDVI(low) above
  ``half_height_frac``·M, NDVI(high) at or below ``half_height_frac``·M and a drop
  M − NDVI(high) of at least ``min_drop``. M is always ``M(high)``. Choice: with M(high)
  the three tests read one number. It equals M(low) whenever the pair passes, because
  NDVI(high) at or below half of M(high) cannot be a new maximum.
- Rebound: a clean observation within ``rebound_days`` after ``high`` with NDVI at or
  above M − ``rebound_within`` drops the pair. The search goes on with the next pair.
  ``rebounds`` counts the dropped pairs. ``reason`` names them only when the status is
  "to_confirm": an "ok" row always has an empty ``reason``.
- Cycle start: a clean observation at or below half height of the peak (M(high)), dated
  in ``green_end_cycle_start_window`` (1 Sep to 31 Dec) and before the peak date.
  Otherwise "to_confirm". ``cycle_start`` is the last such observation.
- Gaps: "ok" only if no gap between consecutive clean observations that ends from
  1 December of the season up to ``high`` is longer than ``max_gap_days``. A gap that
  spans 1 December counts. ``longest_gap_days`` is the largest of them.
- No pair by 31 May: "to_confirm" if a clean observation of the season had NDVI above
  ``ndvi_green`` (canopy seen). "no_data" if the season had no clean observation.
  Choice: clean observations but none above ``ndvi_green`` also give "no_data" (no green
  cycle, so no end of one). ``reason`` says which case it is.

Status (M2.4): "ok" (pair, cycle start seen, no long gap), "to_confirm" (with ``reason``)
or "no_data". The number of green cycles of the ribbon (``green_cycles``) is reported
but never changes the status: the second cycle stays in the data and the ribbon.

Compared variant, columns ``v10_*`` only (``green_end_compared_variant``, the rehearsal
reports both): green is NDVI above min + frac·(max − min) of the season's clean
observations. Its exit is the first consecutive pair green then not green with ``high``
in the exit window. It never sets a status.

Output: ``data/interim/field_<id>_green_end.csv``.

Run from the repository root:
    python -m pipeline.fields.events config/fields/fields.geojson [--rehearsal]
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from fractions import Fraction
from pathlib import Path

from pipeline.adapters import base
from pipeline.bench.common import ProtocolError
from pipeline.common import dekad
from pipeline.fields import geometry, ndvi_series, ribbon

STATUSES = ("ok", "to_confirm", "no_data")
METHOD_ID = "M2-green-end-v2"
REFERENCE = "running_max"
BASE_COLUMNS = ("season", "low", "high", "gap_days", "status", "ndvi_low", "ndvi_high", "peak_ndvi", "drop",
                "green_cycles", "sensor_low", "sensor_high", "reason")
EXTRA_COLUMNS = ("m_high", "peak_date", "cycle_start", "longest_gap_days", "rebounds",
                 "v10_threshold", "v10_low", "v10_high", "v10_gap_days")
COLUMNS = BASE_COLUMNS + EXTRA_COLUMNS
MD = re.compile(r"^(\d{2})-(\d{2})$")
VARIANT = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*% of the season amplitude")


def _window(value, where: str) -> tuple[str, str]:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise ProtocolError(f"{where} must be [\"MM-DD\", \"MM-DD\"], got {value!r}")
    for v in value:
        m = MD.match(str(v))
        if not m:
            raise ProtocolError(f"{where} must hold MM-DD dates, got {v!r}")
        try:
            date(2001, int(m.group(1)), int(m.group(2)))  # a real day (no 29 Feb)
        except ValueError as exc:
            raise ProtocolError(f"{where}: {v!r} is not a day of the year") from exc
    return str(value[0]), str(value[1])


def season_day(start_year: int, md: str) -> date:
    """The date of ``MM-DD`` inside the season that starts on 1 Sep of ``start_year``."""
    m, d = int(md[:2]), int(md[3:])
    return date(start_year if m >= dekad.RIBBON_START_MONTH else start_year + 1, m, d)


@dataclass(frozen=True)
class EventRule:
    clean: ribbon.CleanRule
    half_height_frac: float
    min_drop: float
    exit_window: tuple[str, str]
    cycle_start_window: tuple[str, str]
    rebound_days: int
    rebound_within: float
    max_gap_days: int
    variant_amplitude_frac: float

    def __post_init__(self):
        frac = geometry.number(self.half_height_frac, "half_height_frac", 0, 1)
        if not 0 < frac < 1:
            raise ValueError("half_height_frac must lie strictly between 0 and 1")
        geometry.number(self.min_drop, "min_drop", 0, 2)
        _window(self.exit_window, "exit_window")
        _window(self.cycle_start_window, "cycle_start_window")
        for name in ("exit_window", "cycle_start_window"):
            a, b = getattr(self, name)
            if season_day(2001, a) > season_day(2001, b):
                raise ValueError(f"{name} ends before it starts")
        geometry.integer(self.rebound_days, "rebound_days", 0)
        geometry.number(self.rebound_within, "rebound_within", 0, 2)
        geometry.integer(self.max_gap_days, "max_gap_days", 0)
        geometry.number(self.variant_amplitude_frac, "variant_amplitude_frac", 0, 1)

    @classmethod
    def from_protocol(cls, protocol: Mapping) -> "EventRule":
        """Every threshold from ``protocol.field`` (D10, D12). A missing or null key is refused."""
        clean = ribbon.CleanRule.from_protocol(protocol)
        sec = geometry.field_section(protocol)

        def val(key):
            return geometry.locked(sec, key)

        method = val("green_end_method")
        if method != METHOD_ID:
            raise ProtocolError(f"protocol.field.green_end_method is {method!r}, this code is {METHOD_ID!r}")
        if val("green_end_reference") != REFERENCE:
            raise ProtocolError(f"protocol.field.green_end_reference must be {REFERENCE!r}")
        variant = VARIANT.match(str(val("green_end_compared_variant")))
        if not variant:
            raise ProtocolError("protocol.field.green_end_compared_variant must start with "
                                "'<number>% of the season amplitude'")
        p = "protocol.field."
        return cls(
            clean=clean,
            half_height_frac=geometry.number(val("green_end_half_height_frac"), p + "green_end_half_height_frac", 0, 1),
            min_drop=geometry.number(val("green_end_min_drop"), p + "green_end_min_drop", 0, 2),
            exit_window=_window(val("green_end_exit_window"), p + "green_end_exit_window"),
            cycle_start_window=_window(val("green_end_cycle_start_window"), p + "green_end_cycle_start_window"),
            rebound_days=geometry.integer(val("green_end_rebound_days"), p + "green_end_rebound_days", 0),
            rebound_within=geometry.number(val("green_end_rebound_within"), p + "green_end_rebound_within", 0, 2),
            max_gap_days=geometry.integer(val("green_end_max_gap_days"), p + "green_end_max_gap_days", 0),
            variant_amplitude_frac=float(Fraction(variant.group(1)) / 100))

    def as_dict(self) -> dict:
        return {**self.clean.as_dict(), "green_end_method": METHOD_ID, "green_end_reference": REFERENCE,
                "green_end_half_height_frac": self.half_height_frac, "green_end_min_drop": self.min_drop,
                "green_end_exit_window": list(self.exit_window),
                "green_end_cycle_start_window": list(self.cycle_start_window),
                "green_end_rebound_days": self.rebound_days, "green_end_rebound_within": self.rebound_within,
                "green_end_max_gap_days": self.max_gap_days,
                "green_end_variant_amplitude_frac": self.variant_amplitude_frac}


def _exact(x: float) -> Fraction:
    return Fraction(repr(float(x)))


def daily(obs: Iterable[Mapping]) -> list[dict]:
    """One observation per date: the largest NDVI of that date (ties: sensor name). Pure."""
    best: dict[date, Mapping] = {}
    for o in obs:
        cur = best.get(o["date"])
        if cur is None or (o["ndvi"], o["sensor"]) > (cur["ndvi"], cur["sensor"]):
            best[o["date"]] = o
    return [dict(best[d]) for d in sorted(best)]


def _empty(season: str, cycles: int, status: str, reason: str) -> dict:
    out = {c: None for c in COLUMNS}
    out.update(season=season, status=status, green_cycles=cycles, reason=reason, rebounds=0)
    return out


def variant_10(mine: Sequence[Mapping], start_year: int, rule: EventRule) -> dict:
    """The compared variant (``v10_*`` columns). Never a status. Pure."""
    out = {"v10_threshold": None, "v10_low": None, "v10_high": None, "v10_gap_days": None}
    if len(mine) < 2:
        return out
    lo_v, hi_v = min(_exact(d["ndvi"]) for d in mine), max(_exact(d["ndvi"]) for d in mine)
    if hi_v == lo_v:
        return out
    thr = lo_v + _exact(rule.variant_amplitude_frac) * (hi_v - lo_v)
    out["v10_threshold"] = float(thr)
    w0, w1 = (season_day(start_year, md) for md in rule.exit_window)
    for a, b in zip(mine, mine[1:]):
        if w0 <= b["date"] <= w1 and _exact(a["ndvi"]) > thr >= _exact(b["ndvi"]):
            out.update(v10_low=a["date"].isoformat(), v10_high=b["date"].isoformat(),
                       v10_gap_days=(b["date"] - a["date"]).days)
            break
    return out


def season_event(days: Sequence[Mapping], season: str, cycles: int, rule: EventRule) -> dict:
    """End of the first green cycle of one season from the daily clean observations. Pure.

    ``days`` holds the daily clean observations of every season, by date.
    """
    y = dekad.season_start_year(season)
    mine = [d for d in days if dekad.season_label(d["date"]) == season]
    if not mine:
        return _empty(season, cycles, "no_data", "no clean observation in the season")
    variant = variant_10(mine, y, rule)
    frac = _exact(rule.half_height_frac)
    w0, w1 = (season_day(y, md) for md in rule.exit_window)
    c0, c1 = (season_day(y, md) for md in rule.cycle_start_window)

    # Running maximum M(t) and the date it was reached (first date of the maximum).
    run_max, peak_at = [], []
    m, at = None, None
    for d in mine:
        v = _exact(d["ndvi"])
        if m is None or v > m:
            m, at = v, d["date"]
        run_max.append(m)
        peak_at.append(at)

    k, rebounds = None, 0
    for i in range(1, len(mine)):
        low, high, mh = mine[i - 1], mine[i], run_max[i]
        if not w0 <= high["date"] <= w1:
            continue
        nl, nh = _exact(low["ndvi"]), _exact(high["ndvi"])
        if not (nl > frac * mh and nh <= frac * mh and mh - nh >= _exact(rule.min_drop)):
            continue
        until = high["date"] + timedelta(days=rule.rebound_days)
        back = next((d for d in days if high["date"] < d["date"] <= until
                     and _exact(d["ndvi"]) >= mh - _exact(rule.rebound_within)), None)
        if back is not None:
            rebounds += 1
            continue
        k = i
        break

    dropped = [f"{rebounds} pair(s) dropped by a rebound"] if rebounds else []
    if k is None:
        canopy = any(d["green"] for d in mine)
        upto = [d for d in mine if d["date"] <= w1]
        out = _empty(season, cycles, "to_confirm" if canopy else "no_data", "")
        out.update(variant, rebounds=rebounds,
                   peak_ndvi=float(max(_exact(d["ndvi"]) for d in upto)) if upto else None)
        if not canopy:
            reasons = [f"no clean observation above ndvi_green {rule.clean.ndvi_green:g} in the season"]
        elif not any(w0 <= d["date"] <= w1 for d in mine):
            reasons = [f"canopy seen, but no clean observation from {rule.exit_window[0]} to {rule.exit_window[1]}"]
        else:
            reasons = [f"canopy seen, but no exit pair with high by {rule.exit_window[1]}"]
        out["reason"] = "; ".join(reasons + dropped)
        return out

    low, high, mh, peak_date = mine[k - 1], mine[k], run_max[k], peak_at[k]
    gap = (high["date"] - low["date"]).days
    reasons = []
    starts = [d for d in mine if c0 <= d["date"] <= c1 and d["date"] < peak_date and _exact(d["ndvi"]) <= frac * mh]
    cycle_start = starts[-1]["date"].isoformat() if starts else None
    if cycle_start is None:
        reasons.append(f"cycle start not seen: no clean observation at or below half height of the peak "
                       f"from {rule.cycle_start_window[0]} to {rule.cycle_start_window[1]} before the peak")
    gaps = [(b["date"] - a["date"]).days for a, b in zip(days, days[1:]) if w0 <= b["date"] <= high["date"]]
    longest = max(gaps, default=None)
    if longest is not None and longest > rule.max_gap_days:
        reasons.append(f"interval of {longest} days above {rule.max_gap_days} between clean observations "
                       f"from {rule.exit_window[0]} to high")
    out = {
        "season": season, "low": low["date"].isoformat(), "high": high["date"].isoformat(), "gap_days": gap,
        "status": "to_confirm" if reasons else "ok", "ndvi_low": low["ndvi"], "ndvi_high": high["ndvi"],
        "peak_ndvi": float(mh), "drop": float(mh - _exact(high["ndvi"])), "green_cycles": cycles,
        "sensor_low": low["sensor"], "sensor_high": high["sensor"],
        "reason": "; ".join(reasons + dropped) if reasons else "",
        "m_high": float(mh), "peak_date": peak_date.isoformat(), "cycle_start": cycle_start,
        "longest_gap_days": longest, "rebounds": rebounds, **variant,
    }
    return out


def green_ends(rows: Iterable[Mapping], seasons: Sequence[str], rule: EventRule) -> list[dict]:
    """End-of-green event of each season from the series rows of ``ndvi_series``. Pure."""
    obs = ribbon.clean_observations(rows, rule.clean)
    days = daily(obs)
    return [season_event(days, s, ribbon.season_row(obs, s)["green_cycles"], rule) for s in seasons]


# CSV --------------------------------------------------------------------------------------

def _cell(v) -> str:
    if v is None:
        return ""
    return f"{v:.6f}" if isinstance(v, float) else str(v)


def write_csv(path: Path, events: Iterable[Mapping]) -> Path:
    """Every column of ``COLUMNS``. The extra v2 columns may be absent (written empty)."""
    if base.RAW_DIR.resolve() in path.resolve().parents:
        raise ValueError("derived files never go to data/raw/")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(COLUMNS)
        for e in events:
            w.writerow([_cell(e[c]) for c in BASE_COLUMNS] + [_cell(e.get(c)) for c in EXTRA_COLUMNS])
    tmp.replace(path)
    return path


def read_csv(path: Path) -> list[dict]:
    def opt(s, kind):
        return None if s == "" else kind(s)

    with open(path, encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        if tuple(reader.fieldnames or ()) != COLUMNS:
            raise ValueError(f"{path}: unexpected columns")
        out = []
        for r in reader:
            if r["status"] not in STATUSES:
                raise ValueError(f"{path}: unknown status {r['status']!r}")
            out.append({"season": r["season"], "low": opt(r["low"], str), "high": opt(r["high"], str),
                        "gap_days": opt(r["gap_days"], int), "status": r["status"],
                        "ndvi_low": opt(r["ndvi_low"], float), "ndvi_high": opt(r["ndvi_high"], float),
                        "peak_ndvi": opt(r["peak_ndvi"], float), "drop": opt(r["drop"], float),
                        "green_cycles": int(r["green_cycles"]), "sensor_low": opt(r["sensor_low"], str),
                        "sensor_high": opt(r["sensor_high"], str), "reason": r["reason"],
                        "m_high": opt(r["m_high"], float), "peak_date": opt(r["peak_date"], str),
                        "cycle_start": opt(r["cycle_start"], str),
                        "longest_gap_days": opt(r["longest_gap_days"], int),
                        "rebounds": opt(r["rebounds"], int) or 0,
                        "v10_threshold": opt(r["v10_threshold"], float), "v10_low": opt(r["v10_low"], str),
                        "v10_high": opt(r["v10_high"], str), "v10_gap_days": opt(r["v10_gap_days"], int)})
        return out


def csv_path(field_id: str) -> Path:
    return base.INTERIM_DIR / f"field_{field_id}_green_end.csv"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="End of the first green cycle per season of each field (plan 12.3, D12).")
    ap.add_argument("geojson", type=Path, help="fields FeatureCollection, such as config/fields/fields.geojson")
    ap.add_argument("--rehearsal", action="store_true",
                    help="development strip only (protocol.field.development_strip), never a product field")
    args = ap.parse_args(argv)
    config = base.load_config()
    rule = EventRule.from_protocol(config)  # D10 and D12 guard
    seasons = ribbon.ribbon_seasons(config)
    for f in geometry.load_fields(args.geojson, config, rehearsal=args.rehearsal):
        rows = ndvi_series.read_csv(ndvi_series.csv_path(f["field_id"]))
        evs = green_ends(rows, seasons, rule)
        out = write_csv(csv_path(f["field_id"]), evs)
        counts = {s: sum(e["status"] == s for e in evs) for s in STATUSES}
        print(f"{f['field_id']}: {counts} -> {out.relative_to(base.REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

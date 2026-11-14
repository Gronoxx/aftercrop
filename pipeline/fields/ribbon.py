# Written with an SI tool (Claude, Anthropic) for NASA Space Apps 2026. See AI_USE.md.
"""The 8-season ribbon of a field (plan 7.7, 8.4 step 7, sheet M2, M2.2).

Rows: the seasons of ``protocol.dates.ribbon_seasons`` (2018/19 to 2025/26). Columns:
36 dekads from 1 September (``pipeline.common.dekad``). Cell states:

- ``no_data``: no clean observation in the dekad. Never interpolated, never filled
  from a neighbour, whatever the gap.
- ``green``: a clean observation of the dekad has NDVI above ``ndvi_green``. With
  several clean observations the dekad takes the largest NDVI (maximum value
  composite), so ``green`` means at least one clean observation was green.
- ``not_green``: clean observations, none green. Not bare soil: NDVI cannot separate
  straw from soil.

The ribbon writes only these three states. ``to_confirm`` stays a known cell name, so
older files and the site contract keep reading, but no cell gets it any more: since D12
(SA-D62) a second green cycle stays visible as green and never changes a status.

Green cycles: runs of green cells separated by at least one ``not_green`` cell.
``no_data`` cells neither join nor split a run. The count is information only
(``green_cycles``): the end-of-green status never reads it.

Clean observation (``CleanRule``): NDVI present, at least ``min_clean_pixels`` clean
pixels and at least ``min_clean_frac`` of the possible ones. ``ndvi_green`` and
``min_clean_frac`` are project rules locked in the protocol before any field series is
read (D10); ``from_protocol`` refuses while they are null. Tests build the rule with
example values.

Per season the row also gives ``n_obs`` (clean observations, one per sensor and date)
and ``longest_gap_days`` (largest number of days between consecutive dates with a
clean observation, None with fewer than two), as ``hls.window_summary`` counts gaps.

Output: ``data/interim/field_<id>_ribbon.csv``.

Run from the repository root:
    python -m pipeline.fields.ribbon config/fields/fields.geojson [--rehearsal]
"""

from __future__ import annotations

import argparse
import csv
import math
import sys
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from fractions import Fraction
from pathlib import Path

from pipeline.adapters import base
from pipeline.common import dekad
from pipeline.fields import geometry, ndvi_series

CELLS = ("green", "not_green", "to_confirm", "no_data")
N = dekad.N_DEKADS
COLUMNS = ("season", "n_obs", "longest_gap_days", "green_cycles") + tuple(f"d{i:02d}" for i in range(1, N + 1))


@dataclass(frozen=True)
class CleanRule:
    ndvi_green: float
    min_clean_frac: float
    min_clean_pixels: int

    def __post_init__(self):
        geometry.number(self.ndvi_green, "ndvi_green", -1, 1)
        if not 0 < geometry.number(self.min_clean_frac, "min_clean_frac", 0, 1):
            raise ValueError("min_clean_frac must be above 0")
        geometry.integer(self.min_clean_pixels, "min_clean_pixels", 1)

    @classmethod
    def from_protocol(cls, protocol: Mapping) -> "CleanRule":
        sec = geometry.field_section(protocol)
        return cls(ndvi_green=geometry.number(geometry.locked(sec, "ndvi_green"), "protocol.field.ndvi_green", -1, 1),
                   min_clean_frac=geometry.number(geometry.locked(sec, "min_clean_frac"),
                                                  "protocol.field.min_clean_frac", 0, 1),
                   min_clean_pixels=geometry.integer(geometry.locked(sec, "min_clean_pixels"),
                                                     "protocol.field.min_clean_pixels", 1))

    def is_clean(self, row: Mapping) -> bool:
        v = row.get("ndvi")
        if v is None or not math.isfinite(v):
            return False
        n, n_possible = int(row["n_clean"]), int(row["n_possible"])
        return (n_possible > 0 and n >= self.min_clean_pixels
                and Fraction(n, n_possible) >= Fraction(repr(float(self.min_clean_frac))))

    def is_green(self, ndvi: float) -> bool:
        return ndvi > self.ndvi_green

    def as_dict(self) -> dict:
        return {"ndvi_green": self.ndvi_green, "min_clean_frac": self.min_clean_frac,
                "min_clean_pixels": self.min_clean_pixels}


def ribbon_seasons(protocol: Mapping) -> list[str]:
    """Season labels of ``protocol.dates.ribbon_seasons``, oldest first."""
    from pipeline.bench.common import ProtocolError, protocol_block

    span = (protocol_block(protocol).get("dates") or {}).get("ribbon_seasons")
    if not isinstance(span, list) or len(span) != 2:
        raise ProtocolError("protocol.dates.ribbon_seasons must be [first, last]")
    y0, y1 = dekad.season_start_year(span[0]), dekad.season_start_year(span[1])
    if y1 < y0:
        raise ProtocolError("protocol.dates.ribbon_seasons ends before it starts")
    return [dekad.season_label(date(y, 9, 1)) for y in range(y0, y1 + 1)]


def clean_observations(rows: Iterable[Mapping], rule: CleanRule) -> list[dict]:
    """Clean rows as ``{date, sensor, granule_ur, ndvi, green}``, by date then sensor. Pure."""
    out = [{"date": date.fromisoformat(r["date"]) if isinstance(r["date"], str) else r["date"],
            "sensor": r["sensor"], "granule_ur": r.get("granule_ur", ""), "ndvi": float(r["ndvi"]),
            "green": rule.is_green(float(r["ndvi"]))}
           for r in rows if rule.is_clean(r)]
    return sorted(out, key=lambda o: (o["date"], o["sensor"], o["granule_ur"]))


def season_cells(obs: Iterable[Mapping], season: str) -> list[str]:
    """36 cells of one season from clean observations. Pure."""
    cells = ["no_data"] * N
    for o in obs:
        if dekad.season_label(o["date"]) != season:
            continue
        i = dekad.ribbon_index(o["date"]) - 1
        if o["green"]:
            cells[i] = "green"
        elif cells[i] == "no_data":
            cells[i] = "not_green"
    return cells


def green_cycles(cells: Sequence[str]) -> int:
    """Runs of green (or to_confirm) cells split by not_green cells; no_data is skipped."""
    n, prev = 0, None
    for c in cells:
        if c not in CELLS:
            raise ValueError(f"unknown cell {c!r}")
        if c == "no_data":
            continue
        g = c in ("green", "to_confirm")
        if g and prev is not True:
            n += 1
        prev = g
    return n


def longest_gap_days(dates: Iterable[date]) -> int | None:
    ds = sorted(set(dates))
    return max(((b - a).days for a, b in zip(ds, ds[1:])), default=None)


def season_row(obs: Sequence[Mapping], season: str) -> dict:
    """One ribbon row from the clean observations (any season). Pure."""
    cells = season_cells(obs, season)
    cycles = green_cycles(cells)  # information only: every green cell stays green (D12)
    mine = [o for o in obs if dekad.season_label(o["date"]) == season]
    return {"season": season, "cells": cells, "n_obs": len(mine),
            "longest_gap_days": longest_gap_days(o["date"] for o in mine), "green_cycles": cycles}


def ribbon(rows: Iterable[Mapping], seasons: Sequence[str], rule: CleanRule) -> list[dict]:
    """Ribbon rows of ``seasons`` from the series rows of ``ndvi_series``. Pure."""
    obs = clean_observations(rows, rule)
    return [season_row(obs, s) for s in seasons]


def no_green_count(seasons: Sequence[Mapping]) -> dict:
    """The "no green" count of the ribbon (SA-D72.4, protocol.ribbon_counts.no_green).

    Per season, the ``not_green`` dekads. Then the median over the seasons given, as N of 36.
    ``no_data`` dekads are counted apart and never as no green. Not bare soil: NDVI cannot
    separate straw from soil. Pure. With no season the median is None.
    """
    per = []
    for s in seasons:
        cells = list(s["cells"])
        if len(cells) != N or any(c not in CELLS for c in cells):
            raise ValueError(f"season {s.get('season')!r}: a ribbon row needs {N} known cells")
        per.append({"season": s["season"], "not_green": cells.count("not_green"), "no_data": cells.count("no_data")})
    v = sorted(p["not_green"] for p in per)
    k = len(v)
    if k == 0:
        med = None
    elif k % 2:
        med = v[k // 2]
    else:
        two = v[k // 2 - 1] + v[k // 2]            # an even count: the mean of the two middle seasons
        med = two // 2 if two % 2 == 0 else two / 2
    return {"median": med, "of": N, "seasons": k, "per_season": per}


# CSV --------------------------------------------------------------------------------------

def write_csv(path: Path, seasons: Iterable[Mapping]) -> Path:
    if base.RAW_DIR.resolve() in path.resolve().parents:
        raise ValueError("derived files never go to data/raw/")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(COLUMNS)
        for s in seasons:
            gap = "" if s["longest_gap_days"] is None else s["longest_gap_days"]
            w.writerow([s["season"], s["n_obs"], gap, s["green_cycles"], *s["cells"]])
    tmp.replace(path)
    return path


def read_csv(path: Path) -> list[dict]:
    with open(path, encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        if tuple(reader.fieldnames or ()) != COLUMNS:
            raise ValueError(f"{path}: unexpected columns")
        out = []
        for r in reader:
            cells = [r[f"d{i:02d}"] for i in range(1, N + 1)]
            if any(c not in CELLS for c in cells):
                raise ValueError(f"{path}: unknown cell in {r['season']}")
            out.append({"season": r["season"], "cells": cells, "n_obs": int(r["n_obs"]),
                        "longest_gap_days": int(r["longest_gap_days"]) if r["longest_gap_days"] else None,
                        "green_cycles": int(r["green_cycles"])})
        return out


def csv_path(field_id: str) -> Path:
    return base.INTERIM_DIR / f"field_{field_id}_ribbon.csv"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="8-season ribbon of each field (plan 7.7).")
    ap.add_argument("geojson", type=Path, help="fields FeatureCollection, such as config/fields/fields.geojson")
    ap.add_argument("--rehearsal", action="store_true",
                    help="development strip only (protocol.field.development_strip), never a product field")
    args = ap.parse_args(argv)
    config = base.load_config()
    rule = CleanRule.from_protocol(config)  # D10 guard
    seasons = ribbon_seasons(config)
    for f in geometry.load_fields(args.geojson, config, rehearsal=args.rehearsal):
        rows = ndvi_series.read_csv(ndvi_series.csv_path(f["field_id"]))
        out = write_csv(csv_path(f["field_id"]), ribbon(rows, seasons, rule))
        print(f"{f['field_id']}: {len(seasons)} seasons -> {out.relative_to(base.REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

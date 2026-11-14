# Written with an SI tool (Claude, Anthropic) for NASA Space Apps 2026. See AI_USE.md.
"""``site/data/sensitivity.json``: the one soil sensitivity run after the freeze (plan SA-D77).

The run itself is ``make sensitivity-soil``: the trial chains (base, A1, P1) with the SoilGrids
side files ``trial_<site>_soil_soilgrids.json`` and every other input identical, into
``data/runs_sens/``, then the bench tasks L0, T0, T1, P1 and A1 into ``data/bench_sens/`` and
the scoreboard builder into ``data/bench_sens/scoreboard.json``. The label there is computed by
``pipeline.bench.label`` through the scoreboard builder, as for the frozen one. This module only
copies what that scoreboard shows for the five label tests, next to the soil and zero-yield
counts, and marks the document as a sensitivity after the freeze: ``frozen`` is false, and it
never feeds the label of ``site/data/scoreboard.json``, which it only reads.

Numbers sit in blocks with a ``ledger`` key (``sensitivity.<block>``, ``sensitivity.tests.<id>``),
declared in ``pipeline.ledger.registry``. Like the scoreboard, it carries no p-value, no date and
no commit (SA-D104).

Run from the repository root:
    .venv/bin/python -m pipeline.bench.sensitivity --scoreboard data/bench_sens/scoreboard.json \
        --frozen site/data/scoreboard.json --diagnosis data/bench/soil_diagnosis.json \
        --runs data/runs_sens --out site/data/sensitivity.json
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from pipeline.bench import scoreboard as sb

SCHEMA_VERSION = 1
ROLE = "sensitivity after the freeze"
DECISION = "SA-D77"
TESTS = ("L0", "T0", "T1", "P1", "A1")
VARIANTS = ("base", "a1", "p1")
SOIL_SOURCE = ("SoilGrids 2.0 texture at each trial point, in every layer: the side files "
               "data/interim/trial_<site>_soil_soilgrids.json, written as sensitivity before the freeze")
FROZEN_SOIL = "clay printed in the trial reports, extended to the whole profile (data/interim/trial_<site>_soil.json)"
L0_NOTE = ("L0 reads the field chains, which do not use the trial soils: it checks the same chains as the "
           "frozen L0")
NOTE = "Decided after the freeze (SA-D77). It never changes the frozen label."


def zero_yields(runs: Path, site: str) -> dict[str, dict | None]:
    """Simulated seasons and zero-yield seasons per variant, from the run CSV files."""
    from pipeline.engine.soil_diagnosis import zero_yield_counts   # stdlib only, no engine import

    out = {}
    for v in VARIANTS:
        name = f"trials_{site}.csv" if v == "base" else f"trials_{site}_{v}.csv"
        z = zero_yield_counts(Path(runs) / name)
        out[v] = None if z is None else {"zero": z["zero_yield"], "simulated": z["simulated"]}
    return out


def _frozen_zero(diag_site: Mapping[str, Any]) -> dict[str, dict | None]:
    out = {}
    for v in VARIANTS:
        z = (diag_site.get("zero_yield") or {}).get(v)
        out[v] = None if z is None else {"zero": z["zero_yield"], "simulated": z["simulated"]}
    return out


def _soil_site(diag_site: Mapping[str, Any]) -> dict[str, Any]:
    soils = diag_site.get("soils") or {}
    out = {}
    for role in ("used", "sensitivity"):
        rec = soils.get(role)
        if rec is None:
            raise ValueError(f"the diagnosis has no {role} soil for a site")
        out["frozen" if role == "used" else "sensitivity"] = {
            "file": rec["file"], "sha256": rec["sha256"],
            "clay_pct_min": rec["clay_pct_min"], "clay_pct_max": rec["clay_pct_max"],
            "aw_mm_0_1m": rec["aw_mm_0_1m"], "aw_mm_0_2_4m": rec["aw_mm_0_2_4m"],
        }
    return out


def build(sens: Mapping[str, Any], frozen: Mapping[str, Any], diagnosis: Mapping[str, Any],
          runs: Path) -> dict[str, Any]:
    """The sensitivity document from the sensitivity scoreboard, the frozen one (read only),
    the soil diagnosis and the sensitivity runs."""
    if sens.get("label_id") not in sb.label.LABEL_IDS:
        raise ValueError(f"the sensitivity scoreboard has no label id: {sens.get('label_id')!r}")
    if sens.get("label_text") != sb.label.label_text(sens["label_id"]):
        raise ValueError("the sensitivity label text is not the label code's text")
    rows = {t.get("id"): t for t in sens.get("tests") or () if isinstance(t, Mapping)}
    missing = [tid for tid in TESTS if tid not in rows]
    if missing:
        raise ValueError(f"the sensitivity scoreboard lacks tests {missing}")
    sites = list((diagnosis.get("sites") or {}))
    if not sites:
        raise ValueError("the diagnosis lists no trial site")
    tests = []
    for tid in TESTS:
        row = {k: v for k, v in rows[tid].items() if k != "ledger"}
        if tid == "L0":
            row["note"] = "; ".join(x for x in (row.get("note"), L0_NOTE) if x)
        tests.append({**row, "ledger": f"sensitivity.tests.{tid}"})
    return {
        "schema_version": SCHEMA_VERSION,
        "role": ROLE,
        "frozen": False,
        "decision": DECISION,
        "label_id": sens["label_id"],
        "label_text": sens["label_text"],
        "frozen_label_id": frozen.get("label_id"),
        "protocol_hash": sens.get("protocol_hash"),
        "note": NOTE,
        "soil": {
            "ledger": "sensitivity.soil",
            "source": SOIL_SOURCE,
            "frozen_source": FROZEN_SOIL,
            "formula": diagnosis.get("formula"),
            "by_site": {sb.display_site(s): _soil_site(diagnosis["sites"][s]) for s in sites},
        },
        "zero_yield": {
            "ledger": "sensitivity.zero_yield",
            "by_site": {sb.display_site(s): {"sensitivity": zero_yields(runs, s),
                                             "frozen": _frozen_zero(diagnosis["sites"][s])} for s in sites},
        },
        "site_seasons": {"ledger": "sensitivity.site_seasons", "block": sens.get("site_seasons")},
        "s_by_site": {"ledger": "sensitivity.s_by_site", "block": sens.get("s_by_site")},
        "tests": tests,
        "t1_failed_on": sens.get("t1_failed_on"),
    }


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m pipeline.bench.sensitivity", description=__doc__.splitlines()[0])
    ap.add_argument("--scoreboard", type=Path, required=True, help="the sensitivity scoreboard (data/bench_sens/)")
    ap.add_argument("--frozen", type=Path, required=True, help="the frozen scoreboard, read only")
    ap.add_argument("--diagnosis", type=Path, required=True, help="data/bench/soil_diagnosis.json")
    ap.add_argument("--runs", type=Path, required=True, help="the sensitivity runs folder")
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args(argv)
    if args.out.resolve() == args.frozen.resolve():
        raise SystemExit("--out must not be the frozen scoreboard")
    read = lambda p: json.loads(Path(p).read_text(encoding="utf-8"))  # noqa: E731
    doc = build(read(args.scoreboard), read(args.frozen), read(args.diagnosis), args.runs)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(doc, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
    print(f"{args.out}: {doc['label_text']} ({ROLE})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

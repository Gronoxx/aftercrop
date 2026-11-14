# Written with an SI tool (Claude, Anthropic) for NASA Space Apps 2026. See AI_USE.md.
"""Bench task runners: one per test, each writing ``data/bench/tasks/<ID>.json`` (plan P0.3, 11.6, M4.11).

    .venv/bin/python -m pipeline.bench.run_tasks T0 [--config ...] [--data data]
    .venv/bin/python -m pipeline.bench.run_tasks all

The file holds the task output in the shape ``pipeline.bench.scoreboard.read_tasks``
reads (``to_dict`` of the task result, with its status). Rules, for every task:

- the old task file is deleted first, so a crash never leaves an older result
  behind: a planned task with no file is "not_completed" on the scoreboard;
- any exception (code error, missing file, a value the protocol does not hold
  yet) gives "not_completed" with the error, never "fail". Engine outcomes are
  results and count against the model (section 11.6): a season whose sowing
  rule did not trigger arrives as a yield of 0;
- T0, T1, P1 and A1 read trial values. They run only when ``protocol.status`` is
  "tagged": before the tag their file says "not_run" and nothing is read;
- exit code 1 when the task is "not_completed", 0 otherwise, so ``make -k``
  shows the failure and runs the other tasks.

Inputs, under ``--data`` (default ``data/``), files only (plan 8.3):

    L0      runs/chain_<field>.csv, chain_<field>_repeat.csv, chain_<field>_cn_rew.csv
            (side run, optional), bench/l0_explanations.json (optional, "arm|season|kind": text)
    L1      interim/l1_stations.json: site, rain season totals and temperature biases
            already paired by day, in the records of pipeline.bench.l1
    T0, T1  manual/trials_a.csv and trials_b.csv (must be equal, M4.6), runs/trials_<site>.csv
    P1      the same, and runs/trials_<site>_p1.csv
    A1      the same, and runs/trials_<site>_a1.csv
    A2      raw/power_daily/<site>_met_*.json and <site>_imerg_*.json (the POWER cache)
    A3      build/states_m5.json, written by pipeline.decision.precompute
    N1      interim/noise_floor.json, written by pipeline.fields.noise
    R1      bench/tasks/T1.json and runs/r1_members.json
    H1      bench/h1_results.json, filled by hand after the sessions

Open points, passed as options until the protocol holds them [DECIDE, PROTOCOL.md D12]:
``--field`` (L0), ``--n1-percentile`` and ``--n1-method`` (N1, an explicit value must
agree with the block), ``--r1-band`` and ``--r1-method`` (R1), ``--a2-threshold-mm``,
``--a2-span`` and ``--a2-site`` (A2).

Yield basis (``protocol.tests.T1.yield_basis``): simulated yields are compared at 13%
moisture, the engine dry yield in t/ha x 1000 / 0.87. The basis each Doc declares is
read from ``trial_dates.json`` next to the config (``config/trial_dates.json``, key
``yield_basis``, with its page; ``--trial-meta`` to point elsewhere) and checked: a
site whose Doc declares another moisture leaves the task not_completed.

Event status: L1 (``tests.L1.event_status``) and R1 (``r1.event_status``) are
``planned_not_run`` in the event (SA-D65): their files say "not_run" and read nothing.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections.abc import Callable, Mapping, Sequence
from datetime import date
from fractions import Fraction
from pathlib import Path
from typing import Any

from pipeline.bench.common import NOT_COMPLETED, NOT_RUN, PASS, REPORTED, IncompleteInput, ProtocolError

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = ROOT / "config" / "region_norte_pr.yaml"
DEFAULT_DATA = ROOT / "data"
TRIAL_META_NAME = "trial_dates.json"     # next to the config: yield_basis per site, from the Docs
TASK_IDS = ("L0", "L1", "T0", "T1", "P1", "A1", "A2", "A3", "N1", "R1", "H1")
TRIAL_TASKS = ("T0", "T1", "P1", "A1")
TAGGED = "tagged"
ARM_WHEAT, ARM_CORN = "after_wheat", "after_maize"      # pipeline.engine.regimes.TRIAL_ARMS
SIMULATED = "simulated"
AFTER_STOP = "after_stop"
# Engine outcomes that count against the model as a yield of 0: the sowing rule did not
# trigger by the latest date ("the season counts as a failure, never moved"), and the
# seasons the stopped chain could not reach [DECIDE]. A stop for a sowing date before the
# simulated harvest is a setup problem and leaves the value missing (not_completed).
COUNTS_AGAINST = ("sowing_not_triggered",)
TIMING_COLUMNS = ("init_seconds", "seconds")             # pipeline.engine.cli.TIMING_COLUMNS
RUN_COLUMNS = ("run_key", "variant", "run")              # differ between the two L0 runs by name only
MOISTURE_PCT = 13                                        # protocol.tests.T1.yield_basis
DRY_TO_MOIST = 1 - MOISTURE_PCT / 100                    # a dry yield is divided by 0.87
YIELD_BASIS = "13% moisture: engine dry yield, t/ha x 1000 / 0.87"
PLANNED_NOT_RUN = "planned_not_run"


class NotTagged(Exception):
    """A task that reads trial values was asked to run before the protocol tag."""


# Context ---------------------------------------------------------------------------

class Context:
    """Paths, options and the config of one invocation. Records every file it reads."""

    def __init__(self, *, config: Path = DEFAULT_CONFIG, data: Path = DEFAULT_DATA,
                 options: Mapping[str, Any] | None = None, trial_meta: Path | None = None,
                 **dirs: Path | None):
        self.config_path = Path(config)
        self.data = Path(data)
        self.trial_meta = Path(trial_meta) if trial_meta else self.config_path.parent / TRIAL_META_NAME
        for name in ("manual", "runs", "interim", "raw", "build", "bench"):
            setattr(self, name, Path(dirs.get(name) or self.data / name))
        self.tasks = Path(dirs.get("tasks") or self.bench / "tasks")
        self.options = dict(options or {})
        self.inputs: list[str] = []
        self._config: dict | None = None

    @property
    def config(self) -> dict:
        if self._config is None:
            import yaml

            self._config = yaml.safe_load(self.read_text(self.config_path))
        return self._config

    @property
    def protocol(self) -> Mapping[str, Any]:
        block = self.config.get("protocol") if isinstance(self.config, Mapping) else None
        if not isinstance(block, Mapping):
            raise ProtocolError("the config has no protocol: block")
        return block

    def option(self, name: str) -> Any:
        return self.options.get(name)

    def _note(self, path: Path) -> None:
        p = Path(path).resolve()
        self.inputs.append(str(p.relative_to(ROOT)) if p.is_relative_to(ROOT) else str(p))

    def read_text(self, path: Path) -> str:
        text = Path(path).read_text(encoding="utf-8")
        self._note(path)
        return text

    def read_json(self, path: Path) -> Any:
        return json.loads(self.read_text(path))

    def read_csv(self, path: Path) -> list[dict[str, str]]:
        with open(path, newline="", encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
        self._note(path)
        return rows


def require_tagged(ctx: Context, tid: str) -> None:
    status = ctx.protocol.get("status")
    if status != TAGGED:
        raise NotTagged(f"protocol.status is {status!r}, not {TAGGED!r}: {tid} reads trial values and "
                        "runs only after the protocol tag. Nothing was read")


def trial_keys(ctx: Context) -> list[tuple[str, str]]:
    listed = ctx.protocol.get("trial_site_seasons")
    if not isinstance(listed, Mapping) or not listed:
        raise ProtocolError("protocol.trial_site_seasons is missing")
    return [(site, str(s)) for site, spec in listed.items() for s in spec["seasons"]]


# Trial inputs ------------------------------------------------------------------------

def observed(ctx: Context) -> list:
    """Observed pairs of the protocol site-seasons, from transcription A once A equals B (M4.6).

    A site-season with no pair (no legible axis label, for example) is left out and lowers n.
    """
    from pipeline.bench import trials

    path_a, path_b = ctx.manual / "trials_a.csv", ctx.manual / "trials_b.csv"
    a = trials.read_transcription(path_a, "a")
    b = trials.read_transcription(path_b, "b")
    ctx._note(path_a)
    ctx._note(path_b)
    cmp = trials.compare(a, b)
    if not cmp.equal:
        # counts only: the message never carries a trial value
        raise ValueError(f"transcriptions A and B differ (M4.6): {len(cmp.mismatches)} mismatches, "
                         f"{len(cmp.only_in_a)} rows only in A, {len(cmp.only_in_b)} only in B, "
                         f"{len(cmp.duplicates_a) + len(cmp.duplicates_b)} duplicates")
    by_key = {(p.site, p.season): p for p in trials.observed_pairs(a.rows)}
    return [by_key[k] for k in trial_keys(ctx) if k in by_key]


def yield_basis(ctx: Context, sites: Sequence[str]) -> dict:
    """The basis the protocol fixes and the one each Doc declares, checked (T1.yield_basis).

    Every site must declare 13% moisture, with its source. Anything else raises: the
    observed and simulated yields would not be on one basis (not_completed)."""
    text = str((ctx.protocol.get("tests", {}).get("T1") or {}).get("yield_basis") or "")
    if not text.startswith(f"{MOISTURE_PCT}% moisture"):
        raise ProtocolError(f"protocol.tests.T1.yield_basis {text!r} is not the {MOISTURE_PCT}% basis of this code")
    declared = (ctx.read_json(ctx.trial_meta).get("yield_basis") or {})
    by_site = {}
    for site in sites:
        d = declared.get(site)
        if not isinstance(d, Mapping) or not d.get("source"):
            raise ProtocolError(f"{ctx.trial_meta.name}: no declared yield basis for {site}")
        if d.get("moisture_pct") != MOISTURE_PCT:
            raise ProtocolError(f"{site}: the Doc declares {d.get('moisture_pct')}% moisture, the protocol "
                                f"compares at {MOISTURE_PCT}%")
        by_site[site] = {"moisture_pct": d["moisture_pct"], "source": d["source"]}
    return {"basis": YIELD_BASIS, "protocol": text, "declared": by_site}


def simulated_kg_ha(row: Mapping[str, str]) -> float | None:
    """Yield of one engine row in kg/ha at 13% moisture, 0 for an outcome that counts
    against the model, None when the value is missing (the task becomes not_completed)."""
    outcome = row.get("outcome", "")
    if outcome == SIMULATED:
        text = row.get("dry_yield_t_ha", "")
        return float(text) * 1000 / DRY_TO_MOIST if text not in ("", None) else None
    if outcome in COUNTS_AGAINST or (outcome == AFTER_STOP and row.get("stop_reason") in COUNTS_AGAINST):
        return 0.0
    return None


def target_yields(ctx: Context, sites: Sequence[str], suffix: str = "") -> dict[tuple, float | None]:
    """Target soybean per (site, season, arm, shift_k, offset_years) from runs/trials_<site><suffix>.csv.

    A site whose file is missing raises: the task becomes not_completed.
    """
    out: dict[tuple, float | None] = {}
    for site in sites:
        for row in ctx.read_csv(ctx.runs / f"trials_{site}{suffix}.csv"):
            if row.get("role") != "target" or row.get("crop") != "soybean" or row.get("site") != site:
                continue
            k = int(row["shift_k"]) if row.get("shift_k") else None
            o = int(row["offset_years"]) if row.get("offset_years") else 0
            key = (site, row["season"], row["arm"], k, o)
            if key in out:
                raise ValueError(f"trials_{site}{suffix}.csv: target {key} appears twice")
            out[key] = simulated_kg_ha(row)
    return out


def _delta(sim: Mapping[tuple, float | None], site: str, season: str, k: int | None = None, o: int = 0):
    w, c = sim.get((site, season, ARM_WHEAT, k, o)), sim.get((site, season, ARM_CORN, k, o))
    return None if w is None or c is None else w - c


def _sites(pairs: Sequence[Any]) -> list[str]:
    return list(dict.fromkeys(p.site for p in pairs))


# Runners -----------------------------------------------------------------------------

def run_t0(ctx: Context) -> dict:
    from pipeline.bench import t0
    from pipeline.bench.common import Pair

    require_tagged(ctx, "T0")
    rule = t0.T0Rule.from_protocol(ctx.protocol)
    obs = observed(ctx)
    basis = yield_basis(ctx, _sites(obs))
    sim = target_yields(ctx, _sites(obs))
    pairs = [Pair(o.site, o.season, o.soy_after_corn, sim.get((o.site, o.season, ARM_CORN, None, 0))) for o in obs]
    out = t0.evaluate(pairs, rule).to_dict()
    out["yield_basis"] = basis
    return out


def run_t1(ctx: Context) -> dict:
    from pipeline.bench import t1
    from pipeline.bench.common import Pair

    require_tagged(ctx, "T1")
    rule = t1.T1Rule.from_protocol(ctx.protocol)
    obs = observed(ctx)
    basis = yield_basis(ctx, _sites(obs))
    sim = target_yields(ctx, _sites(obs))
    pairs = [Pair(o.site, o.season, o.delta, _delta(sim, o.site, o.season)) for o in obs]
    res = t1.evaluate(pairs, rule)
    out = res.to_dict()
    out["mse_exact"] = f"{res.mse.numerator}/{res.mse.denominator}"   # R1 reads L from it, exactly
    out["yield_basis"] = basis
    return out


def run_p1(ctx: Context) -> dict:
    """All 9 shifts or nothing: a shift with any missing value leaves P1 not_completed."""
    from pipeline.bench import p1, stats
    from pipeline.bench.trials import normalize_season

    require_tagged(ctx, "P1")
    design = p1.P1Design.from_protocol(ctx.protocol)
    obs = observed(ctx)
    sites = _sites(obs)
    basis = yield_basis(ctx, sites)
    base = target_yields(ctx, sites)
    shifted: dict[tuple, float | None] = {}
    for site in sites:
        path = ctx.runs / f"trials_{site}_p1.csv"
        if path.exists():
            shifted.update(target_yields(ctx, [site], "_p1"))
    keys = [(o.site, o.season) for o in obs]
    observed_by = {"T0": {(o.site, o.season): o.soy_after_corn for o in obs},
                   "T1": {(o.site, o.season): o.delta for o in obs}}

    def value(arm: str, site: str, season: str, k: int | None, o: int, table) -> float | None:
        if arm == "T0":
            return table.get((site, season, ARM_CORN, k, o))
        return _delta(table, site, season, k, o)

    arms = []
    for arm in ("T0", "T1"):
        real = [value(arm, s, y, None, 0, base) for s, y in keys]
        s_real = None
        if all(v is not None for v in real):
            s_real = stats.kendall_s(stats.exact_all(real), stats.exact_all([observed_by[arm][k] for k in keys]))
        by_shift = {}
        for k in design.shifts:
            run = {}
            for site, season in keys:
                o = p1.chain_offset(normalize_season(season)[1], k, design)
                run[(site, season)] = value(arm, site, season, k, o, shifted)
            by_shift[k] = run if any(v is not None for v in run.values()) else None
        arms.append(p1.evaluate_arm(arm, s_real, p1.shifted_s(observed_by[arm], by_shift, design), design))
    out = p1.combine(*arms).to_dict()
    out["yield_basis"] = basis
    return out


def run_a1(ctx: Context) -> dict:
    from pipeline.bench import a1

    require_tagged(ctx, "A1")
    rule = a1.A1Rule.from_protocol(ctx.protocol)
    obs = observed(ctx)
    sites = _sites(obs)
    basis = yield_basis(ctx, sites)
    with_state = target_yields(ctx, sites)
    without_state = target_yields(ctx, sites, "_a1")
    records = [a1.A1Record(o.site, o.season, o.delta, _delta(with_state, o.site, o.season),
                           _delta(without_state, o.site, o.season)) for o in obs]
    out = a1.evaluate(records, rule).to_dict()
    out["yield_basis"] = basis
    return out


def _field_id(ctx: Context) -> str:
    fid = ctx.option("field")
    if not fid:
        raise ProtocolError("L0 needs --field: the field whose chains it checks is not fixed [DECIDE]")
    return str(fid)


def _dedup_shared(rows: Sequence[Mapping[str, str]]) -> list[Mapping[str, str]]:
    """Rows of one run with the shared prefix kept once, in the first arm that has it."""
    first_arm: dict[str, str] = {}
    out = []
    for r in rows:
        year = r.get("year", "")
        first_arm.setdefault(year, r["arm"])
        if r.get("shared_prefix") == "true" and r["arm"] != first_arm[year]:
            continue
        out.append(r)
    return out


def _stripped(rows: Sequence[Mapping[str, str]]) -> list[dict]:
    drop = set(TIMING_COLUMNS) | set(RUN_COLUMNS)
    return [{k: v for k, v in r.items() if k not in drop} for r in rows]


def _target_delta_by_year(rows: Sequence[Mapping[str, str]]) -> dict[str, float | None]:
    by: dict[str, dict[str, float | None]] = {}
    for r in rows:
        if r.get("role") == "target" and r.get("crop") == "soybean":
            by.setdefault(r["year"], {})[r["arm"]] = simulated_kg_ha(r)
    out = {}
    for year, arms in by.items():
        s1, s3 = arms.get("S1"), arms.get("S3")
        out[year] = None if s1 is None or s3 is None else s3 - s1
    return out


def run_l0(ctx: Context) -> dict:
    """L0 on one field, or on several (``--field a,b,c``): each field is checked alone, and L0
    fails if any field fails (team lead decision, R2: the three product fields).
    ``--l0-settings`` is the text of the field-chain settings, kept in the task file."""
    from pipeline.bench import common

    fids = [f.strip() for f in _field_id(ctx).split(",") if f.strip()]
    if len(fids) == 1:
        out = _run_l0_field(ctx, fids[0])
    else:
        per = {fid: _run_l0_field(ctx, fid) for fid in fids}
        failed = [fid for fid, d in per.items() if d["status"] == common.FAIL]
        out = {
            "id": "L0",
            "status": common.FAIL if failed else common.PASS,
            "statistic": next(iter(per.values()))["statistic"],
            "field_id": ",".join(fids),
            "failed_on": sorted({x for d in per.values() for x in (d.get("failed_on") or [])}),
            "closure_mm_per_swap": next(iter(per.values()))["closure_mm_per_swap"],
            "n_swaps": sum(d["n_swaps"] for d in per.values()),
            "max_gap_mm": max(d["max_gap_mm"] for d in per.values()),
            "n_gaps_over": sum(d["n_gaps_over"] for d in per.values()),
            "repeat_exact": all(d["repeat_exact"] for d in per.values()),
            "n_artifacts": sum(d["n_artifacts"] for d in per.values()),
            "n_unexplained": sum(d["n_unexplained"] for d in per.values()),
            "unexplained_artifacts_max": next(iter(per.values()))["unexplained_artifacts_max"],
            "blocks_delta": bool(failed),
            "notes": [f"fields failed: {', '.join(failed)}"] if failed else [],
            "fields": per,
        }
    settings = ctx.option("l0_settings")
    if settings:
        out["chain_settings"] = settings
    return out


def _run_l0_field(ctx: Context, fid: str) -> dict:
    from pipeline.bench import l0

    rule = l0.L0Rule.from_protocol(ctx.protocol)
    first = ctx.read_csv(ctx.runs / f"chain_{fid}.csv")
    second = ctx.read_csv(ctx.runs / f"chain_{fid}_repeat.csv")
    if not first:
        raise IncompleteInput(f"chain_{fid}.csv has no rows")
    gaps: dict[str, list] = {}
    found = []
    notes = []
    for name, rows in (("run 1", first), ("run 2", second)):
        for r in _dedup_shared(rows):
            if r.get("handover_gap_mm"):
                gaps.setdefault(f"{r['arm']} {r['year']} {name}", []).append(r["handover_gap_mm"])
            if name != "run 1":
                continue
            season = f"{r['year']} {r['label']}"
            outcome = r.get("outcome")
            if outcome == SIMULATED:
                dte = r.get("days_to_emergence")
                found += l0.detect_artifacts(r["arm"], season, dry_yield_t_ha=r["dry_yield_t_ha"],
                                             crop_dead=r.get("crop_dead") == "true",
                                             days_to_emergence=int(dte) if dte else None)
            elif outcome in COUNTS_AGAINST:
                found.append(l0.Artifact(r["arm"], season, l0.ZEROED, "sowing rule not triggered by the latest date"))
            elif outcome == AFTER_STOP:
                notes.append(f"{r['arm']} {season}: not simulated, the chain stopped before it")
            else:
                raise IncompleteInput(f"{r['arm']} {season}: chain stopped ({outcome}), a setup problem")
    path = ctx.bench / "l0_explanations.json"
    texts = ctx.read_json(path) if path.exists() else {}
    found = l0.explain(found, {tuple(k.split("|")): v for k, v in texts.items()})
    cn_rew = None
    side = ctx.runs / f"chain_{fid}_cn_rew.csv"
    if side.exists():
        try:
            cn_rew = l0.cn_rew_variation(_target_delta_by_year(first), _target_delta_by_year(ctx.read_csv(side)))
        except (IncompleteInput, ValueError) as exc:
            notes.append(f"CN 61 and REW 9 side run not compared: {exc}")
    else:
        notes.append(f"CN 61 and REW 9 side run missing (chain_{fid}_cn_rew.csv)")
    out = l0.evaluate(gaps, _stripped(first), _stripped(second), found, rule, cn_rew).to_dict()
    out["field_id"] = fid
    out["notes"] = list(out.get("notes", [])) + notes
    return out


def _planned_not_run(ctx: Context, section: Mapping[str, Any] | None) -> bool:
    return isinstance(section, Mapping) and section.get("event_status") == PLANNED_NOT_RUN


def run_l1(ctx: Context) -> dict:
    from pipeline.bench import l1

    if _planned_not_run(ctx, (ctx.protocol.get("tests") or {}).get("L1")):
        return {"id": "L1", "status": NOT_RUN,
                "note": "planned, not run in the event: no station file is prepared (protocol.tests.L1.event_status, "
                        "SA-D65)"}
    doc = ctx.read_json(ctx.interim / "l1_stations.json")
    rain = [l1.RainSeason(**r) for r in doc.get("rain", [])]
    temperature = [l1.TemperatureBias(**t) for t in doc.get("temperature", [])]
    return l1.evaluate(doc["site"], rain, temperature, l1.L1Rule.from_protocol(ctx.protocol)).to_dict()


def _power_file(ctx: Context, site: str, call: str) -> Path:
    from pipeline.adapters import power

    spec = next((s for s in power.specs(ctx.config) if s["site"] == site and s["call"] == call), None)
    if spec is None:
        raise ProtocolError(f"no POWER call {call!r} for site {site!r} in sources.power")
    return ctx.raw / spec["source_id"] / power.request(spec)[2]


def _span(text: Any) -> tuple[str, str]:
    parts = [p.strip() for p in str(text or "").split(",") if p.strip()]
    if len(parts) != 2:
        raise ProtocolError("A2 needs --a2-span MM-DD,MM-DD: the dry-autumn months are not in the protocol "
                            "[DECIDE D12]")
    return parts[0], parts[1]


A2_NOT_RUN_NOTE = ("the count A2 checked was removed, SA-D68; the rain lines of SA-D72 are not compared "
                   "with IMERG in the event")


def run_a2(ctx: Context) -> dict:
    """The dry-autumn count of L2 with PRECTOTCORR and again with IMERG_PRECTOT, same rule."""
    from pipeline.adapters import power
    from pipeline.bench import ablation
    from pipeline.decision import risk_counts as rc

    threshold = ctx.option("a2_threshold_mm")
    if threshold is None:
        # Team lead decision (R2): the dry-autumn count A2 compared left the page for
        # lack of a sourced threshold (SA-D68), so A2 has nothing to compare. No statistic.
        return {"id": "A2", "status": NOT_RUN, "note": A2_NOT_RUN_NOTE}
    rule = ablation.A2Rule.from_protocol(ctx.protocol)
    span = _span(ctx.option("a2_span"))
    site = ctx.option("a2_site") or ctx.protocol.get("tests", {}).get("L1", {}).get("site")
    if not site:
        raise ProtocolError("A2 needs --a2-site [DECIDE]")
    met_path, imerg_path = _power_file(ctx, site, "met"), _power_file(ctx, site, "imerg")
    met, imerg = power.read(met_path), power.read(imerg_path)
    ctx._note(met_path)
    ctx._note(imerg_path)
    years = list(rc.climate_years(ctx.protocol))
    main = rc.dry_autumn(met, threshold_mm=float(threshold), span=span, years=years)
    check = rc.dry_autumn(imerg.rename(columns={"IMERG_PRECTOT": rc.RAIN})[[rc.RAIN]],
                          threshold_mm=float(threshold), span=span, years=years)
    (m1, d1), (m2, d2) = ((int(x) for x in s.split("-")) for s in span)
    count = ablation.L2Count(
        id="dry_autumn",
        main={y: y in main.years for y in years if y not in main.left_out},
        check={y: y in check.years for y in years if y not in check.left_out},
        windows={y: (date(y, m1, d1), date(y, m2, d2)) for y in years},
    )
    out = ablation.evaluate_a2([count], rule).to_dict()
    out.update(site=site, threshold_mm=float(threshold), span=list(span))
    return out


def run_a3(ctx: Context) -> dict:
    from pipeline.decision import precompute

    states = ctx.read_json(ctx.build / "states_m5.json")
    return precompute.a3_result(states, ctx.protocol).to_dict()


def run_n1(ctx: Context) -> dict:
    from pipeline.bench import n1
    from pipeline.fields import noise

    doc = ctx.read_json(ctx.interim / "noise_floor.json")
    halves, sensors = noise.n1_inputs(doc)
    rule = n1.N1Rule.from_protocol(ctx.protocol, percentile=ctx.option("n1_percentile"),
                                   method=ctx.option("n1_method"))
    return n1.evaluate(halves, sensors, rule).to_dict()


def run_r1(ctx: Context) -> dict:
    """Runs only if T1 passed. Without the member runs it is "not_run" (R1 was cut, plan 16.4)."""
    from pipeline.bench import r1

    if _planned_not_run(ctx, ctx.protocol.get("r1")):
        t1_file = ctx.tasks / "T1.json"
        t1_seen = ctx.read_json(t1_file).get("status", NOT_RUN) if t1_file.exists() else NOT_RUN
        out = r1.not_run(t1_seen, []).to_dict()
        out["notes"] = ["planned, not run in the event: the engine has no R1 path (protocol.r1.event_status, "
                        "SA-D65). L5 says inconclusive"]
        return out
    t1_path = ctx.tasks / "T1.json"
    t1d = ctx.read_json(t1_path) if t1_path.exists() else {"status": NOT_RUN}
    t1_status = t1d.get("status", NOT_RUN)
    members_path = ctx.runs / "r1_members.json"
    doc = ctx.read_json(members_path) if members_path.exists() else None
    field_ids = [f["field_id"] for f in (doc or {}).get("fields", [])]
    if t1_status != PASS:
        return r1.not_run(t1_status, field_ids).to_dict()
    if doc is None:
        out = r1.not_run(t1_status, field_ids).to_dict()
        out["notes"] = ["T1 passed, but there are no R1 member runs (runs/r1_members.json): R1 was cut "
                        "(plan 16.4)"]
        return out
    band = ctx.option("r1_band")
    rule = r1.R1Rule.from_protocol(ctx.protocol, band=None if band is None else [float(x) for x in band.split(",")],
                                   method=ctx.option("r1_method"))
    if "mse_exact" not in t1d:
        raise IncompleteInput("T1.json has no mse_exact: run T1 with this runner first")
    fields = [r1.R1Field(f["field_id"], f["base"], {int(m): run for m, run in f["members"].items()})
              for f in doc["fields"]]
    return r1.evaluate(fields, rule, t1_status=PASS, t1_mse=Fraction(t1d["mse_exact"]),
                       members=doc.get("members")).to_dict()


def run_h1(ctx: Context) -> dict:
    """H1 from the hand-filled results: the first ``slots`` completed sessions in the order of
    confirmation count (SA-D57). A reserve enters only when someone did not attend or finish,
    never to replace a result. No name is kept: each session has an id only."""
    path = ctx.bench / "h1_results.json"
    if not path.exists():
        return {"id": "H1", "status": NOT_RUN, "note": "no H1 results file (bench/h1_results.json)"}
    doc = ctx.read_json(path)
    h1 = ctx.protocol.get("h1")
    if not isinstance(h1, Mapping):
        raise ProtocolError("protocol.h1 is missing")
    slots = int(h1["slots"])
    task_ids = [str(t["id"]) for t in h1["tasks"]]
    sessions = list(doc.get("sessions", []))
    ids = [s.get("id") for s in sessions]
    orders = [s.get("confirmed_order") for s in sessions]
    if len(set(ids)) != len(ids) or None in ids:
        raise ValueError("every H1 session needs a unique id")
    if len(set(orders)) != len(orders) or not all(isinstance(o, int) for o in orders):
        raise ValueError("every H1 session needs a unique integer confirmed_order")
    passed: dict[str, dict[str, bool]] = {}
    for s in sessions:
        if not isinstance(s.get("completed"), bool):
            raise ValueError(f"H1 session {s['id']}: completed must be true or false")
        if s["completed"]:
            tasks = {str(k): v for k, v in (s.get("tasks") or {}).items()}
            if set(tasks) != set(task_ids) or not all(isinstance(v, bool) for v in tasks.values()):
                raise ValueError(f"H1 session {s['id']}: tasks must be {task_ids}, each true or false")
            passed[s["id"]] = tasks
    counted = [s for s in sorted(sessions, key=lambda s: s["confirmed_order"]) if s["completed"]][:slots]
    if not counted:
        return {"id": "H1", "status": NOT_RUN, "note": "no completed H1 session"}
    by_task = {t: {"passed": sum(1 for s in counted if passed[s["id"]][t]), "of": len(counted)}
               for t in task_ids}
    languages: dict[str, int] = {}
    for s in counted:
        lang = str(s.get("language") or "not recorded")
        languages[lang] = languages.get(lang, 0) + 1
    notes = ["informal; measures page comprehension, not farm impact"]
    if len(counted) < slots:
        notes.append(f"{len(counted)} of {slots} slots completed")
    return {"id": "H1", "status": REPORTED, "statistic": "tasks_passed",
            "value": {"tasks": by_task, "sessions": len(counted), "languages": languages},
            "n": len(counted), "note": "; ".join(notes),
            "sessions_counted": [s["id"] for s in counted],
            "sessions_not_counted": [s["id"] for s in sessions if s["id"] not in {c["id"] for c in counted}]}


RUNNERS: dict[str, Callable[[Context], dict]] = {
    "L0": run_l0, "L1": run_l1, "T0": run_t0, "T1": run_t1, "P1": run_p1, "A1": run_a1,
    "A2": run_a2, "A3": run_a3, "N1": run_n1, "R1": run_r1, "H1": run_h1,
}


# Guard and file -----------------------------------------------------------------------

def _as_dict(tid: str, out: Any) -> dict:
    from pipeline.bench import scoreboard

    d = scoreboard.task_dict(tid, out)          # checks id and status as the scoreboard will
    json.dumps(d, allow_nan=False)
    return d


def run_task(tid: str, ctx: Context) -> dict:
    """Run one task and write its file, whatever happens. Returns what was written."""
    if tid not in RUNNERS:
        raise ValueError(f"unknown task {tid!r}, expected one of {TASK_IDS}")
    ctx.tasks.mkdir(parents=True, exist_ok=True)
    path = ctx.tasks / f"{tid}.json"
    path.unlink(missing_ok=True)
    ctx.inputs = []
    try:
        if ctx._config is not None:          # read by an earlier task of this invocation
            ctx._note(ctx.config_path)
        d = _as_dict(tid, RUNNERS[tid](ctx))
    except NotTagged as exc:
        d = {"id": tid, "status": NOT_RUN, "note": str(exc)}
    except Exception as exc:  # code error, missing file, value the protocol lacks: never "fail"
        d = {"id": tid, "status": NOT_COMPLETED, "error": f"{type(exc).__name__}: {exc}"}
    d["inputs"] = list(dict.fromkeys(ctx.inputs))
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(d, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
    tmp.replace(path)
    return d


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="python -m pipeline.bench.run_tasks", description=__doc__.splitlines()[0])
    ap.add_argument("task_ids", nargs="+", metavar="TASK", help=f"task ids ({', '.join(TASK_IDS)}) or 'all'")
    ap.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    ap.add_argument("--data", type=Path, default=DEFAULT_DATA, help="data root (default data/)")
    for name in ("manual", "runs", "interim", "raw", "build", "bench", "tasks"):
        ap.add_argument(f"--{name}", type=Path, default=None, help=f"default <data>/{name}" if name != "tasks"
                        else "default <bench>/tasks")
    ap.add_argument("--field", default=None, help="L0: field id(s) of the chains checked, comma-separated")
    ap.add_argument("--l0-settings", default=None, help="L0: field-chain settings, kept in the task file")
    ap.add_argument("--n1-percentile", type=float, default=None, help="N1 percentile [DECIDE D12]")
    ap.add_argument("--n1-method", default=None, help="N1 percentile method [DECIDE D12]")
    ap.add_argument("--r1-band", default=None, help="R1 band percentiles LOW,HIGH [DECIDE D12]")
    ap.add_argument("--r1-method", default=None, help="R1 percentile method [DECIDE D12]")
    ap.add_argument("--a2-threshold-mm", type=float, default=None, help="A2 dry-autumn threshold [DECIDE D12]")
    ap.add_argument("--a2-span", default=None, help="A2 dry-autumn span MM-DD,MM-DD [DECIDE D12]")
    ap.add_argument("--a2-site", default=None, help="A2 POWER site, default protocol.tests.L1.site")
    ap.add_argument("--trial-meta", type=Path, default=None,
                    help="declared yield basis per site, default trial_dates.json next to --config")
    return ap


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    ids = list(TASK_IDS) if [t.lower() for t in args.task_ids] == ["all"] else [t.upper() for t in args.task_ids]
    unknown = [t for t in ids if t not in RUNNERS]
    if unknown:
        print(f"unknown tasks {unknown}, expected {list(TASK_IDS)} or all", file=sys.stderr)
        return 2
    options = {k: getattr(args, k) for k in ("field", "l0_settings", "n1_percentile", "n1_method", "r1_band", "r1_method",
                                             "a2_threshold_mm", "a2_span", "a2_site")}
    ctx = Context(config=args.config, data=args.data, options=options, trial_meta=args.trial_meta,
                  **{k: getattr(args, k) for k in ("manual", "runs", "interim", "raw", "build", "bench", "tasks")})
    failed = 0
    for tid in ids:
        d = run_task(tid, ctx)
        detail = d.get("error") or d.get("note") or ""
        print(f"{tid}: {d['status']}" + (f" ({detail})" if detail and d["status"] in (NOT_COMPLETED, NOT_RUN) else ""))
        failed += d["status"] == NOT_COMPLETED
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

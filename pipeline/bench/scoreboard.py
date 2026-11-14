# Written with an SI tool (Claude, Anthropic) for NASA Space Apps 2026. See AI_USE.md.
"""The scoreboard: ``site/data/scoreboard.json`` and ``data/bench/results.json``.

Shape of section 8.6 of the plan, criterion M4.8: the twelve tests L0, L1, T0,
T1, P1, A1, A2, A3, N1, H1, H2 and R1 are always present, each with a status.
A failure shows as "fail"; an infrastructure failure (code error, missing file,
time out) as "not_completed", never as a failure; a test that was not tried as
"not_run" ("planned, not run"). The S inside each trial site is shown for every
site, none left out, and ``t1_failed_on`` says whether T1 failed on order, size
or both. The label is computed by ``label.label``; nobody writes it.

Two files:

- ``site/data/scoreboard.json`` goes to the page. It carries no p-value, no
  confidence interval and no significance level (section 11.2: "Nada de p nem
  IC na pagina"), and no R1 Delta in kg/ha (section 12.4: internal use only).
  P1 shows how many wrong-year S reached the real S; A1 shows D.
- ``data/bench/results.json`` keeps every task's full output, p included.

H2 comes from ``protocol.tests.H2`` (not run, SA-D56) and cannot be passed in.
R1 must be "not_run" unless T1 passed. Thresholds come from the protocol.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from pipeline.bench import a1, ablation, common, l0, l1, label, p1, stats, t0, t1
from pipeline.bench.common import (EXPLORATORY, FAIL, NOT_COMPLETED, NOT_RUN, PASS, REPORTED,
                                   STATUSES)

SCHEMA_VERSION = 1
TEST_IDS = ("L0", "L1", "T0", "T1", "P1", "A1", "A2", "A3", "N1", "H1", "H2", "R1")
LABEL_TESTS = ("T0", "T1", "P1", "A1")
REPORT_ONLY = ("A2", "A3", "N1", "H1")      # no threshold: "reported" once run
ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = ROOT / "config" / "region_norte_pr.yaml"
SITE_PATH = ROOT / "site" / "data" / "scoreboard.json"
RESULTS_PATH = ROOT / "data" / "bench" / "results.json"
TASKS_DIR = ROOT / "data" / "bench" / "tasks"

STATISTIC = {
    "L0": "water_closure_repeat_artifacts",
    "L1": "rain_bias_pct_per_season_and_temperature_bias_c",
    "T0": "kendall_s",
    "T1": "kendall_s_and_rmse",
    "P1": "kendall_s",
    "A1": "d_kendall_s_with_minus_without_state",
    "A2": "l2_counts_prectotcorr_against_imerg",
    "A3": "states_changed_without_source",
    "N1": "percentile_of_disagreement_days",
    "H1": "tasks_passed",
    "H2": None,
    "R1": "percentile_band_and_inversion",
}

# Never on the page (section 11.2). Checked on the whole scoreboard before writing.
FORBIDDEN_KEYS = frozenset({"p", "p_value", "alpha", "ci", "confidence_interval", "band_kg_ha"})

NOTE_NOT_RUN = "planned, not run"
NOTE_NOT_COMPLETED = ("not completed: infrastructure failure (code error, missing file or time out). "
                      "It never counts as a failure")
NOTE_EXPLORATORY = "fewer site-seasons than the protocol requires: exploratory, no approval"


# Protocol ------------------------------------------------------------------------

def protocol_hash(config_path: str | Path = CONFIG_PATH) -> str:
    """sha256 of the config bytes from the line ``protocol:`` to the end (PROTOCOL.md, section 4)."""
    text = Path(config_path).read_bytes()
    return hashlib.sha256(text[text.index(b"\nprotocol:\n") + 1:]).hexdigest()


def display_site(site: str) -> str:
    """Scoreboard key of a trial site: "londrina" gives "Londrina" (section 8.6)."""
    return site[:1].upper() + site[1:]


def trial_sites(protocol: Mapping[str, Any]) -> dict[str, int]:
    """Trial site ids of the protocol and their expected site-seasons."""
    listed = common.protocol_block(protocol).get("trial_site_seasons")
    if not isinstance(listed, Mapping) or not listed:
        raise common.ProtocolError("protocol.trial_site_seasons is missing")
    common.site_seasons_required(protocol)   # checks the count
    return {site: len(v["seasons"]) for site, v in listed.items()}


def thresholds(protocol: Mapping[str, Any]) -> dict[str, dict[str, Any] | None]:
    """The threshold of each test, from the protocol only. None when there is none."""
    r0 = l0.L0Rule.from_protocol(protocol)
    r1_data = l1.L1Rule.from_protocol(protocol)
    rt0 = t0.T0Rule.from_protocol(protocol)
    rt1 = t1.T1Rule.from_protocol(protocol)
    dp1 = p1.P1Design.from_protocol(protocol)
    a1.A1Rule.from_protocol(protocol)
    ablation.A2Rule.from_protocol(protocol)
    ra3 = ablation.A3Rule.from_protocol(protocol)
    sec_r1 = common.protocol_block(protocol).get("r1")
    if not isinstance(sec_r1, Mapping):
        raise common.ProtocolError("protocol has no 'r1' block")
    # P1 passes with p = (1 + c) / (B + 1) <= alpha: c, the wrong-year S at or above the real S,
    # may reach floor(alpha (B + 1)) - 1, which is 0 for B = 9 and 0.10.
    c_max = math.floor(dp1.alpha * (dp1.b + 1)) - 1
    return {
        "L0": {"closure_mm_per_swap": float(r0.closure_mm), "repeat": r0.repeat,
               "unexplained_artifacts_max": r0.max_unexplained},
        "L1": {"rain_bias_max_pct_per_season": float(r1_data.rain_bias_max_pct),
               "temperature_bias_max_c": float(r1_data.temperature_bias_max_c)},
        "T0": {"s_min": rt0.s_min, "within_site_s_sum_gt": rt0.within_site_sum_gt},
        "T1": {"s_min": rt1.s_min, "rmse_lt": rt1.rmse_reference},
        "P1": {"wrong_year_s_at_least_max": c_max, "shifts": dp1.b},
        "A1": {"d_gt": 0, "rule": "and the exhaustive paired swap of the protocol"},
        "A2": None,
        "A3": {"zero_changes": ra3.zero_changes},
        "N1": None,
        "H1": None,
        "H2": None,
        "R1": {"inversion_max": float(common.require_level(sec_r1, "inversion_max", "r1")),
               "equivalence_kg_ha": float(common.require(sec_r1, "equivalence_kg_ha", "r1")),
               "runs_only_if": common.require(sec_r1, "runs_only_if", "r1")},
    }


def h2_entry(protocol: Mapping[str, Any]) -> dict[str, Any]:
    """H2 as the protocol fixes it: not attempted (SA-D56)."""
    sec = common.protocol_section(protocol, "H2")
    status = common.require(sec, "status", "tests.H2")
    if status not in STATUSES:
        raise common.ProtocolError(f"protocol.tests.H2.status must be one of {STATUSES}")
    note = "; ".join(str(sec[k]) for k in ("reason", "page") if sec.get(k))
    return {"id": "H2", "status": status, "statistic": None, "value": None, "threshold": None,
            "n": None, "note": note or None}


# Task outputs --------------------------------------------------------------------

def allowed_statuses(test_id: str) -> tuple[str, ...]:
    if test_id in REPORT_ONLY:
        return (REPORTED, NOT_RUN, NOT_COMPLETED)
    if test_id in ("T0", "T1", "A1"):
        return (PASS, FAIL, NOT_RUN, NOT_COMPLETED, EXPLORATORY)
    return (PASS, FAIL, NOT_RUN, NOT_COMPLETED)


def task_dict(test_id: str, outcome: Any) -> dict[str, Any] | None:
    """One task's output as a dict: from a result object (``to_dict``), a
    ``label.TaskOutcome`` (``run_guarded``) or a dict read from JSON."""
    if outcome is None:
        return None
    if isinstance(outcome, label.TaskOutcome):
        res = outcome.result
        if hasattr(res, "to_dict"):
            d = dict(res.to_dict())
        elif isinstance(res, Mapping):
            d = dict(res)
        else:
            d = {}
        d["status"] = outcome.status
        if outcome.error:
            d["error"] = outcome.error
    elif hasattr(outcome, "to_dict"):
        d = dict(outcome.to_dict())
    elif isinstance(outcome, Mapping):
        d = dict(outcome)
    else:
        raise TypeError(f"{test_id}: cannot read a task output of type {type(outcome).__name__}")
    if d.get("id", test_id) != test_id:
        raise ValueError(f"output of {d.get('id')!r} given as {test_id}")
    d["id"] = test_id
    if d.get("status") not in allowed_statuses(test_id):
        raise ValueError(f"{test_id} status must be one of {allowed_statuses(test_id)}, got {d.get('status')!r}")
    return d


def _f(x: Any) -> Any:
    return None if x is None else float(x)


def _sites_view(by_site: Mapping[str, Any] | None, sites: Mapping[str, int]) -> dict[str, Any]:
    by_site = by_site or {}
    unknown = sorted(set(by_site) - set(sites))
    if unknown:
        raise ValueError(f"sites outside protocol.trial_site_seasons: {unknown}")
    return {display_site(s): by_site.get(s) for s in sites}


def _value(test_id: str, d: Mapping[str, Any], sites: Mapping[str, int]) -> tuple[Any, Any, list[str]]:
    """value, n and notes of a test that produced a result."""
    notes: list[str] = []
    if test_id == "L0":
        if d.get("failed_on"):
            notes.append("failed on " + ", ".join(d["failed_on"]) + "; a failure blocks reading any Delta")
        return ({"max_gap_mm": d["max_gap_mm"], "repeat_exact": d["repeat_exact"],
                 "n_artifacts": d["n_artifacts"], "n_unexplained": d["n_unexplained"]}, d["n_swaps"], notes)
    if test_id == "L1":
        notes += list(d.get("flags", [])) + [f"no comparison with {s}" for s in d.get("stations_missing", [])]
        return ({"max_abs_rain_bias_pct": d.get("max_abs_rain_bias_pct"),
                 "max_abs_temperature_bias_c": d.get("max_abs_temperature_bias_c"),
                 "stations_used": list(d.get("stations_used", []))}, d.get("n"), notes)
    if test_id == "T0":
        return ({"s": d["s"], "tau": d["tau"], "within_site_sum": d["within_site_sum"],
                 "s_by_site": _sites_view(d.get("s_by_site"), sites)}, d["n"], notes)
    if test_id == "T1":
        if d.get("t1_failed_on"):
            notes.append(f"failed on {d['t1_failed_on']}")
        notes.append("signs are reported and approve nothing")
        return ({"s": d["s"], "tau": d["tau"], "rmse_kg_ha": d["rmse"],
                 "rmse_constant_in_sample_kg_ha": d["rmse_constant_in_sample"],
                 "rmse_constant_loo_kg_ha": d["rmse_constant_loo"], "rmse_zero_kg_ha": d["rmse_zero"],
                 "n_same_sign": d["n_same_sign"], "n_observed_positive": d["n_observed_positive"],
                 "s_by_site": _sites_view(d.get("s_by_site"), sites)}, d["n"], notes)
    if test_id == "P1":
        arms = {}
        for arm in ("t0", "t1"):
            a = d[arm]
            arms[arm] = {"status": a["status"], "s_real": a["s_real"], "wrong_year_s_at_least": a["n_at_least"]}
        return arms, len(d["t1"]["s_by_shift"]), notes
    if test_id == "A1":
        if d["status"] == FAIL and d["d"] > 0:
            notes.append("D above zero, but the paired-swap rule of the protocol was not met")
        elif d["status"] == FAIL:
            notes.append("D not above zero")
        return ({"d": d["d"], "s_with_state": d["s_with_state"], "s_without_state": d["s_without_state"],
                 "patterns": d["n_patterns"]}, d["n"], notes)
    if test_id == "A2":
        notes.append("reported in How we know. Not a key of the NASA switch")
        counts = [{k: c[k] for k in ("id", "m", "n_main", "n_check", "n_changed", "years_main", "years_check")}
                  for c in d["counts"]]
        return {"counts": counts, "common_period": d.get("common_period")}, d.get("n"), notes
    if test_id == "A3":
        notes.append("measures dependence on the source, not accuracy; read as a sensitivity to price")
        return ({"states_changed": d["states_changed"], "states_total": d["states_total"],
                 "verdict": d["verdict"], "scenarios": d.get("scenarios")}, d["states_total"], notes)
    if test_id == "N1":
        notes.append("nothing below the floor reaches the screen")
        return ({"floor_days": d["floor_days"], "percentile": d["percentile"],
                 "percentile_halves_days": d["percentile_halves_days"],
                 "percentile_l30_vs_s30_days": d["percentile_l30_vs_s30_days"],
                 "n_halves": d["n_halves"], "n_l30_vs_s30": d["n_l30_vs_s30"]}, d["n"], notes)
    if test_id == "H1":
        if d.get("note"):
            notes.append(str(d["note"]))
        return d.get("value"), d.get("n"), notes
    if test_id == "R1":
        if d.get("notes"):
            notes += list(d["notes"])
        fields = [{"field_id": f["field_id"], "status": f["status"], "state": f["state"], "column": f["column"],
                   "members": f["members"], "n_inverted": f["n_inverted"],
                   "n_same_direction": f["n_same_direction"]} for f in d["fields"]]
        return {"fields": fields, "members": d.get("members")}, d.get("n"), notes
    raise ValueError(f"unknown test {test_id}")


def entry(test_id: str, d: Mapping[str, Any] | None, threshold: Any, sites: Mapping[str, int]) -> dict[str, Any]:
    """One line of ``tests`` in the scoreboard."""
    base = {"id": test_id, "status": NOT_RUN, "statistic": STATISTIC[test_id], "value": None,
            "threshold": None if threshold is None else dict(threshold), "n": None, "note": NOTE_NOT_RUN}
    if d is None:
        if test_id == "R1":
            base["note"] = f"{NOTE_NOT_RUN}. Runs only if T1 passes. L5 reads inconclusive"
        return base
    status = d["status"]
    base["status"] = status
    if status == NOT_COMPLETED:
        base["note"] = NOTE_NOT_COMPLETED
        if test_id == "P1":
            base["note"] += ". P1 incomplete counts as not passed, and no partial result is read"
        return base
    if status == NOT_RUN:
        if test_id == "R1":
            base["note"] = "; ".join(list(d.get("notes", [])) + ["L5 reads inconclusive"])
        elif test_id == "A2" and d.get("note"):    # R2: the reason (SA-D68) goes to the page
            base["note"] = f"{NOTE_NOT_RUN}: {d['note']}"
        return base
    value, n, notes = _value(test_id, d, sites)
    if test_id == "T1" and base["threshold"] is not None:
        base["threshold"]["rmse_lt_kg_ha"] = d["rmse_threshold"]
    if status == EXPLORATORY:
        notes.insert(0, NOTE_EXPLORATORY)
    base.update(value=value, n=n, note="; ".join(notes) if notes else None)
    return base


def _forbidden(obj: Any, path: str = "") -> list[str]:
    found = []
    if isinstance(obj, Mapping):
        for k, v in obj.items():
            if k in FORBIDDEN_KEYS:
                found.append(f"{path}.{k}")
            found += _forbidden(v, f"{path}.{k}")
    elif isinstance(obj, (list, tuple)):
        for i, v in enumerate(obj):
            found += _forbidden(v, f"{path}[{i}]")
    return found


# Extras --------------------------------------------------------------------------

def noise_summary(records: Sequence[tuple[str, Any, Any]]) -> dict[str, Any]:
    """``noise`` of the scoreboard from (site, observed Delta, SED or None) per
    site-season: how many observed Delta lie within 1 SED, how many have a SED,
    and the sites with none (section 11.3). Computed at the event only."""
    with_sed = [(site, stats.exact(delta), stats.exact(sed)) for site, delta, sed in records if sed is not None]
    sites = list(dict.fromkeys(site for site, _, _ in records))
    have = {site for site, _, _ in with_sed}
    return {
        "n_within_1_sed": sum(1 for _, delta, sed in with_sed if abs(delta) <= sed),
        "n_with_sed": len(with_sed),
        "sites_without_sed": [display_site(s) for s in sites if s not in have],
    }


def _check_noise(noise: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if noise is None:
        return None
    keys = {"n_within_1_sed", "n_with_sed", "sites_without_sed"}
    if set(noise) != keys:
        raise ValueError(f"noise must hold exactly {sorted(keys)}")
    return dict(noise)


def _check_ceiling(ceiling: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if ceiling is None:
        return None
    if set(ceiling) != {"low", "high"}:
        raise ValueError("correlation_ceiling must hold exactly 'low' and 'high'")
    return {"low": _f(ceiling["low"]), "high": _f(ceiling["high"])}


# Build and write -----------------------------------------------------------------

def build(
    outcomes: Mapping[str, Any],
    protocol: Mapping[str, Any],
    *,
    region_id: str,
    protocol_hash: str | None = None,
    noise: Mapping[str, Any] | None = None,
    correlation_ceiling: Mapping[str, Any] | None = None,
    late_notes: Sequence[Mapping[str, str]] = (),
) -> tuple[dict[str, Any], dict[str, Any]]:
    """(scoreboard, results) from the task outputs, keyed by test id.

    A test absent from ``outcomes`` (or None) is "not_run". H2 is taken from
    the protocol. Raises on anything the plan forbids: an unknown id, a status
    outside the schema, R1 run without a T1 pass, a p-value on the page.

    Neither document carries a date or a commit: no freeze time, no protocol commit, and the
    late notes lose their ``at`` time (SA-D104).
    """
    unknown = sorted(set(outcomes) - set(TEST_IDS))
    if unknown:
        raise ValueError(f"unknown tests: {unknown}")
    if outcomes.get("H2") is not None:
        raise ValueError("H2 comes from protocol.tests.H2 (SA-D56) and cannot be passed in")
    sites = trial_sites(protocol)
    limits = thresholds(protocol)
    dicts = {tid: task_dict(tid, outcomes.get(tid)) for tid in TEST_IDS if tid != "H2"}
    status = {tid: (d["status"] if d else NOT_RUN) for tid, d in dicts.items()}
    if status["T1"] != PASS and status["R1"] != NOT_RUN:
        # R1 runs only if T1 passes: an R1 output without a T1 pass is never read.
        dicts["R1"] = {"id": "R1", "status": NOT_RUN, "fields": [],
                       "notes": [f"runs only if T1 passes; T1 is {status['T1']}",
                                 "an R1 output was given and was not read"]}
        status["R1"] = NOT_RUN
    status["H2"] = h2_entry(protocol)["status"]

    # Site-seasons and S by site come from T1, or from T0 when T1 has nothing.
    source = next((dicts[k] for k in ("T1", "T0")
                   if dicts[k] and dicts[k]["status"] not in (NOT_RUN, NOT_COMPLETED)), None)
    n_by_site = dict(source.get("n_by_site") or {}) if source else {}
    _sites_view(n_by_site, sites)
    complete = sum(n_by_site.values()) if n_by_site else (source["n"] if source else 0)
    t1d = dicts["T1"] if dicts["T1"] and dicts["T1"]["status"] not in (NOT_RUN, NOT_COMPLETED) else None
    n_required = common.site_seasons_required(protocol)
    label_id = label.label(status["T0"], status["T1"], status["P1"], status["A1"], complete,
                           n_required=n_required, l0=status["L0"])

    tests = [h2_entry(protocol) if tid == "H2" else entry(tid, dicts[tid], limits[tid], sites)
             for tid in TEST_IDS]

    scoreboard = {
        "schema_version": SCHEMA_VERSION,
        "region_id": region_id,
        "label_id": label_id,
        "label_text": label.label_text(label_id),
        "completed": all(status[k] not in (NOT_RUN, NOT_COMPLETED) for k in LABEL_TESTS),
        "protocol_hash": protocol_hash,
        "not_blind": True,        # PROTOCOL.md: "The test is not blind."
        "exploratory": True,      # PROTOCOL.md: "The test is exploratory, with a fixed rule."
        "site_seasons": {
            "expected": n_required,
            "complete": complete,
            "by_site": {display_site(s): {"expected": n, "complete": n_by_site.get(s, 0)} for s, n in sites.items()},
        },
        "s_by_site": _sites_view(t1d.get("s_by_site") if t1d else None, sites),
        "tests": tests,
        "t1_failed_on": t1d.get("t1_failed_on") if t1d else None,
        "noise": _check_noise(noise),
        "correlation_ceiling": _check_ceiling(correlation_ceiling),
        "late_notes": [{k: v for k, v in n.items() if k != "at"} for n in late_notes],
    }
    bad = _forbidden(scoreboard)
    if bad:
        raise ValueError(f"the page never shows p, CI or R1 Delta: {bad}")
    results = {
        "schema_version": SCHEMA_VERSION,
        "region_id": region_id,
        "protocol_hash": protocol_hash,
        "label_id": label_id,
        "n_site_seasons": complete,
        "tests": {tid: full_output(tid, dicts, protocol) for tid in TEST_IDS},
    }
    json.dumps(scoreboard, allow_nan=False)
    json.dumps(results, allow_nan=False)
    return scoreboard, results


def full_output(test_id: str, dicts: Mapping[str, Any], protocol: Mapping[str, Any]) -> dict[str, Any]:
    if test_id == "H2":
        return h2_entry(protocol)
    return dicts[test_id] or {"id": test_id, "status": NOT_RUN}


def dump(doc: Mapping[str, Any]) -> str:
    return json.dumps(doc, indent=2, ensure_ascii=False, allow_nan=False) + "\n"


def write(scoreboard: Mapping[str, Any], results: Mapping[str, Any],
          site_path: str | Path = SITE_PATH, results_path: str | Path = RESULTS_PATH) -> None:
    for path, doc in ((Path(site_path), scoreboard), (Path(results_path), results)):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(dump(doc), encoding="utf-8")


def read_tasks(tasks_dir: str | Path, planned: Sequence[str] = ()) -> dict[str, Any]:
    """Task outputs written by the Make targets, one ``<ID>.json`` per task.

    A planned task without a file did not finish (hard stop or crash):
    "not_completed". An unplanned task without a file is "not_run".
    """
    out: dict[str, Any] = {}
    for tid in TEST_IDS:
        if tid == "H2":
            continue
        path = Path(tasks_dir) / f"{tid}.json"
        if path.exists():
            out[tid] = json.loads(path.read_text(encoding="utf-8"))
        elif tid in planned:
            out[tid] = {"id": tid, "status": NOT_COMPLETED, "error": f"no result file {path.name}"}
    return out


def main(argv: Sequence[str] | None = None) -> int:
    import yaml

    ap = argparse.ArgumentParser(description="Write the scoreboard and the bench results.")
    ap.add_argument("--config", default=str(CONFIG_PATH))
    ap.add_argument("--tasks", default=str(TASKS_DIR))
    ap.add_argument("--planned", default="", help="comma-separated ids the bench tried, e.g. L0,T0,T1,P1,A1")
    ap.add_argument("--extra", default=None, help="JSON with noise, correlation_ceiling and late_notes")
    ap.add_argument("--site-out", default=str(SITE_PATH))
    ap.add_argument("--results-out", default=str(RESULTS_PATH))
    args = ap.parse_args(argv)
    config = yaml.safe_load(Path(args.config).read_text(encoding="utf-8"))
    extra = json.loads(Path(args.extra).read_text(encoding="utf-8")) if args.extra else {}
    planned = [p.strip() for p in args.planned.split(",") if p.strip()]
    scoreboard, results = build(
        read_tasks(args.tasks, planned), config,
        region_id=config.get("region"), protocol_hash=protocol_hash(args.config),
        noise=extra.get("noise"), correlation_ceiling=extra.get("correlation_ceiling"),
        late_notes=extra.get("late_notes", ()),
    )
    write(scoreboard, results, args.site_out, args.results_out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

# Written with an SI tool (Claude, Anthropic) for NASA Space Apps 2026. See AI_USE.md.
"""Bench task runners (pipeline/bench/run_tasks.py, plan P0.3 and criterion M4.11).

Synthetic data only, labelled "example, fictitious": made-up sites, transcriptions,
engine rows, weather and sessions, written to a temporary folder. Never data/.

- each runner writes data/bench/tasks/<ID>.json in the shape the scoreboard reads;
- a raised error, a missing file or a value the protocol lacks gives "not_completed";
- an engine outcome (sowing rule not triggered) counts against the model, as a yield of 0;
- T0, T1, P1 and A1 read nothing before the protocol tag ("not_run");
- the old task file is deleted first, so a crash never leaves a stale result.

``make_tree`` is reused by tests/test_makefile.py for ``make -k bench``.
"""

from __future__ import annotations

import copy
import csv
import json
import shutil
from datetime import date, timedelta
from pathlib import Path

import pytest
import yaml

from pipeline.bench import label, p1, run_tasks, scoreboard
from pipeline.bench.common import NOT_COMPLETED, NOT_RUN
from pipeline.engine.cli import CHAIN_COLUMNS, TRIAL_COLUMNS

ROOT = Path(__file__).resolve().parents[1]
FIX = Path(__file__).parent / "fixtures"
LABEL = "example, fictitious"
PROTOCOL = json.loads((FIX / "bench_protocol.json").read_text(encoding="utf-8"))["protocol"]
SITES = {site: list(v["seasons"]) for site, v in PROTOCOL["trial_site_seasons"].items()}
KEYS = [(s, y) for s, seasons in SITES.items() for y in seasons]
# Fictitious soybean after second-crop corn and Delta (after wheat minus after corn), kg/ha.
CORN = [2500 + 61 * i for i in range(len(KEYS))]
DELTA = [-130, 75, 210, -40, 160, 20, 305, -95, 120, 260]
TRANSCRIPTION_COLUMNS = ("doc", "page", "table_or_figure", "site", "season", "predecessor", "soy_yield",
                         "unit", "soy_yield_kg_ha", "cv_or_sed", "note")
OPTIONS = {"field": "example", "n1_percentile": 90, "n1_method": "nearest_rank", "r1_band": "10,90",
           "r1_method": "nearest_rank", "a2_threshold_mm": 100.0, "a2_span": "03-01,05-31", "a2_site": None}
CLIMATE = ("1997-01-01", "2001-12-31")


# Synthetic tree --------------------------------------------------------------------------

def protocol(status="tagged"):
    p = copy.deepcopy(PROTOCOL)
    p["status"] = status
    p["dates"]["climate"] = list(CLIMATE)
    p["h1"] = {"slots": 3, "tasks": [{"id": i, "task": LABEL, "pass": LABEL} for i in range(1, 6)]}
    p["tests"]["T1"]["yield_basis"] = "13% moisture. A dry yield is divided by 0.87"
    return p


def trial_meta(moisture=13):
    """The declared basis per site, as config/trial_dates.json holds it (fictitious sources)."""
    return {"_note": LABEL, "sites": {},
            "yield_basis": {s: {"moisture_pct": moisture, "unit": "kg/ha", "source": LABEL} for s in SITES}}


def config(status="tagged"):
    calls = [{"id": "met", "parameters": ["T2M_MAX", "T2M_MIN", "PRECTOTCORR"], "start": CLIMATE[0],
              "end": CLIMATE[1], "time_standard": "LST"},
             {"id": "imerg", "parameters": ["IMERG_PRECTOT"], "start": CLIMATE[0], "end": CLIMATE[1],
              "time_standard": "UTC"}]
    return {"_note": LABEL, "region": "example_region",
            "trial_sites": [{"id": s, "lat": -23.0, "lon": -51.0, "altitude_m": 500} for s in SITES],
            "sources": {"power": {"source_id": "power_daily", "url": "https://power.example.invalid/daily",
                                  "license": LABEL, "community": "AG", "calls": calls}},
            "protocol": protocol(status)}


def write_csv(path: Path, columns, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(columns), extrasaction="raise")
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in columns})


def transcription_rows():
    rows = []
    for (site, season), corn, delta in zip(KEYS, CORN, DELTA):
        for pred, value in (("trigo", corn + delta), ("milho_2a_safra", corn)):
            rows.append({"doc": "Doc398", "page": "1", "table_or_figure": "Tabela 1", "site": site,
                         "season": season, "predecessor": pred, "soy_yield": value, "unit": "kg/ha",
                         "soy_yield_kg_ha": value, "cv_or_sed": "", "note": LABEL})
    return rows


def target_row(site, season, arm, kg_ha, **extra):
    row = {"run_key": f"trial_{site}_x", "site": site, "variant": "base", "arm": arm, "index": 4,
           "label": f"{season}:soybean", "role": "target", "crop": "soybean", "season": season,
           "outcome": "simulated", "dry_yield_t_ha": kg_ha * 0.87 / 1000, "offset_years": 0}
    row.update(extra)
    return row


def trial_rows(site, *, corn, wheat, **extra):
    out = []
    for (s, season), c, w in zip(KEYS, corn, wheat):
        if s == site:
            out.append(target_row(site, season, "after_maize", c, **extra))
            out.append(target_row(site, season, "after_wheat", w, **extra))
    return out


def chain_rows(variant="base", *, s1=3100.0, s3=3400.0):
    def row(arm, i, label, role, crop, kg, gap, shared):
        return {"run_key": f"field_example_{variant}_2019", "field_id": "example", "year": 2019,
                "variant": variant, "setup": "base", "ksat": "value", "run": 1, "arm": arm, "index": i,
                "label": label, "role": role, "crop": crop, "season": label.rsplit(":", 1)[0],
                "outcome": "simulated", "stop_reason": "", "shared_prefix": "true" if shared else "false",
                "dry_yield_t_ha": kg / 1000, "crop_dead": "false", "days_to_emergence": 8,
                "handover_gap_mm": gap, "seconds": 0.123}
    return [row("S1", 0, "2018/19:soybean", "prefix", "soybean", 3000, "", True),
            row("S1", 1, "2019:maize", "predecessor", "maize", 6000, 0.25, False),
            row("S1", 2, "2019/20:soybean", "target", "soybean", s1, 0.5, False),
            row("S3", 0, "2018/19:soybean", "prefix", "soybean", 3000, "", True),
            row("S3", 1, "2019:wheat", "predecessor", "wheat", 2500, 0.1, False),
            row("S3", 2, "2019/20:soybean", "target", "soybean", s3, 0.999, False)]


def power_doc(parameters: dict) -> dict:
    return {"_note": LABEL, "header": {"fill_value": -999.0}, "properties": {"parameter": parameters}}


def daily_rain(dry_years, *, missing_before=None):
    out, d = {}, date.fromisoformat(CLIMATE[0])
    while d <= date.fromisoformat(CLIMATE[1]):
        if missing_before and d < missing_before:
            out[d.strftime("%Y%m%d")] = -999.0
        else:
            out[d.strftime("%Y%m%d")] = 1.0 if d.year in dry_years else 2.0
        d += timedelta(days=1)
    return out


def make_tree(tmp: Path, status="tagged") -> dict:
    """Every input of every task, synthetic. Returns the paths."""
    data = tmp / "data"
    cfg_path = tmp / "region_example.yaml"
    cfg_path.write_text(yaml.safe_dump(config(status), sort_keys=False), encoding="utf-8")
    (tmp / "trial_dates.json").write_text(json.dumps(trial_meta()), encoding="utf-8")
    for name in ("a", "b"):
        write_csv(data / "manual" / f"trials_{name}.csv", TRANSCRIPTION_COLUMNS, transcription_rows())
    sim_corn = [c * 1.1 for c in CORN]
    sim_wheat = [c * 1.1 + 0.9 * d for c, d in zip(CORN, DELTA)]
    runs = data / "runs"
    for site in SITES:
        write_csv(runs / f"trials_{site}.csv", TRIAL_COLUMNS, trial_rows(site, corn=sim_corn, wheat=sim_wheat))
        write_csv(runs / f"trials_{site}_a1.csv", TRIAL_COLUMNS,
                  trial_rows(site, corn=[3000.0] * len(KEYS), wheat=[3000.0] * len(KEYS), variant="a1"))
        design = p1.P1Design.from_protocol(PROTOCOL)
        rows = []
        for k in design.shifts:
            for (s, season), c, d in zip(KEYS, CORN, DELTA):
                if s != site:
                    continue
                o = p1.chain_offset(int(season[:4]), k, design)
                back = 5000.0 - c       # the wrong years reverse the order: S = -45
                rows.append(target_row(site, season, "after_maize", back, variant="p1", shift_k=k, offset_years=o))
                rows.append(target_row(site, season, "after_wheat", back - d, variant="p1", shift_k=k,
                                       offset_years=o))
        write_csv(runs / f"trials_{site}_p1.csv", TRIAL_COLUMNS, rows)
    write_csv(runs / "chain_example.csv", CHAIN_COLUMNS, chain_rows())
    write_csv(runs / "chain_example_repeat.csv", CHAIN_COLUMNS,
              [dict(r, run_key="field_example_repeat_2019", variant="repeat", run=2, seconds=0.456)
               for r in chain_rows("repeat")])
    write_csv(runs / "chain_example_cn_rew.csv", CHAIN_COLUMNS, chain_rows("cn_rew", s1=3150.0, s3=3380.0))
    members = [{"field_id": "field_a", "base": {"2019/20": {"S1": 3000, "S3": 3400}},
                "members": {str(m): {"2019/20": {"S1": 3000, "S3": 3400}} for m in range(50)}}]
    (runs / "r1_members.json").write_text(json.dumps({"_note": LABEL, "members": 50, "fields": members}),
                                          encoding="utf-8")
    interim = data / "interim"
    interim.mkdir(parents=True)
    l1 = json.loads((FIX / "bench_l1.json").read_text(encoding="utf-8"))
    (interim / "l1_stations.json").write_text(json.dumps({"_note": LABEL, "site": l1["site"], "rain": l1["rain"],
                                                           "temperature": l1["temperature"]}), encoding="utf-8")
    n1fx = json.loads((FIX / "bench_n1.json").read_text(encoding="utf-8"))
    (interim / "noise_floor.json").write_text(json.dumps({
        "_note": LABEL, "schema_version": "1", "unit": "days", "halves_days": n1fx["halves_days"],
        "l30_vs_s30_days": n1fx["l30_vs_s30_days"], "n_halves": len(n1fx["halves_days"]),
        "n_l30_vs_s30": len(n1fx["l30_vs_s30_days"])}), encoding="utf-8")
    raw = data / "raw" / "power_daily"
    raw.mkdir(parents=True)
    a2_site = PROTOCOL["tests"]["L1"]["site"]
    stamp = f"{CLIMATE[0].replace('-', '')}_{CLIMATE[1].replace('-', '')}"
    (raw / f"{a2_site}_met_{stamp}_LST.json").write_text(json.dumps(power_doc(
        {"PRECTOTCORR": daily_rain({1998, 2000}), "T2M_MAX": {}, "T2M_MIN": {}})), encoding="utf-8")
    (raw / f"{a2_site}_imerg_{stamp}_UTC.json").write_text(json.dumps(power_doc(
        {"IMERG_PRECTOT": daily_rain({1998, 2001}, missing_before=date(1998, 1, 1))})), encoding="utf-8")
    build = data / "build"
    build.mkdir(parents=True)
    shutil.copy(FIX / "decision_example" / "states_m5.json", build / "states_m5.json")
    bench = data / "bench"
    bench.mkdir(parents=True)
    sessions = [{"id": f"h1-{i}", "confirmed_order": i, "language": "en", "completed": i != 2,
                 "tasks": {str(t): (t != 3 or i == 4) for t in range(1, 6)}} for i in range(1, 6)]
    (bench / "h1_results.json").write_text(json.dumps({"_note": LABEL, "sessions": sessions}), encoding="utf-8")
    return {"data": data, "config": cfg_path, "tasks": bench / "tasks", "runs": runs, "manual": data / "manual"}


@pytest.fixture
def tree(tmp_path):
    return make_tree(tmp_path)


def context(tree, **options):
    return run_tasks.Context(config=tree["config"], data=tree["data"], options={**OPTIONS, **options})


def task_file(tree, tid):
    return json.loads((tree["tasks"] / f"{tid}.json").read_text(encoding="utf-8"))


# Every runner -------------------------------------------------------------------------------

EXPECTED = {"L0": "pass", "L1": ("pass", "fail"), "T0": "pass", "T1": "pass", "P1": "pass", "A1": "pass",
            "A2": "reported", "A3": "reported", "N1": "reported", "R1": "not_run", "H1": "reported"}


@pytest.mark.parametrize("tid", run_tasks.TASK_IDS)
def test_each_runner_writes_a_valid_task_file(tree, tid):
    d = run_tasks.run_task(tid, context(tree))
    on_disk = task_file(tree, tid)
    assert on_disk == d
    assert scoreboard.task_dict(tid, on_disk)["status"] == d["status"]
    expected = EXPECTED[tid]
    assert d["status"] in (expected if isinstance(expected, tuple) else (expected,)), d.get("error")
    assert d["inputs"] and all("data/manual" not in p or str(tree["data"]) in p for p in d["inputs"])


def test_all_tasks_then_the_scoreboard(tree, tmp_path):
    ctx = context(tree)
    for tid in run_tasks.TASK_IDS:
        run_tasks.run_task(tid, ctx)
    site, res = tmp_path / "scoreboard.json", tmp_path / "results.json"
    assert scoreboard.main(["--config", str(tree["config"]), "--tasks", str(tree["tasks"]),
                            "--planned", ",".join(run_tasks.TASK_IDS),
                            "--site-out", str(site), "--results-out", str(res)]) == 0
    board = json.loads(site.read_text(encoding="utf-8"))
    by = {t["id"]: t for t in board["tests"]}
    assert board["label_id"] == label.DIRECTION_SUPPORTED
    assert by["T1"]["value"]["s"] == 45 and by["P1"]["value"]["t1"]["wrong_year_s_at_least"] == 0
    assert by["H1"]["n"] == 3 and by["H1"]["value"]["tasks"]["3"] == {"passed": 1, "of": 3}
    assert board["site_seasons"]["complete"] == 10


def test_r1_runs_after_a_t1_pass(tree):
    ctx = context(tree)
    run_tasks.run_task("T1", ctx)
    d = run_tasks.run_task("R1", ctx)
    assert task_file(tree, "T1")["mse_exact"]
    assert d["status"] == "pass" and d["fields"][0]["state"] == "lower water risk in the model"
    (tree["runs"] / "r1_members.json").unlink()
    d = run_tasks.run_task("R1", ctx)
    assert d["status"] == "not_run" and "cut" in d["notes"][0]


def test_r1_not_run_when_t1_did_not_pass(tree):
    d = run_tasks.run_task("R1", context(tree))          # no T1.json yet
    assert d["status"] == "not_run" and "T1 is not_run" in d["notes"][0]


# Failures -------------------------------------------------------------------------------------

def test_a_raised_error_gives_not_completed(tree, monkeypatch, capsys):
    def broken(ctx):
        raise RuntimeError("synthetic crash")
    monkeypatch.setitem(run_tasks.RUNNERS, "T1", broken)
    code = run_tasks.main(["T1", "T0", "--config", str(tree["config"]), "--data", str(tree["data"])])
    assert code == 1
    t1 = task_file(tree, "T1")
    assert t1["status"] == NOT_COMPLETED and "RuntimeError: synthetic crash" in t1["error"]
    assert task_file(tree, "T0")["status"] == "pass"     # the next task still ran
    assert "T1: not_completed" in capsys.readouterr().out


def test_missing_file_gives_not_completed(tree):
    (tree["runs"] / "trials_synthetic_site_b.csv").unlink()
    d = run_tasks.run_task("T0", context(tree))
    assert d["status"] == NOT_COMPLETED and "FileNotFoundError" in d["error"]


def test_open_point_without_option_gives_not_completed(tree):
    d = run_tasks.run_task("N1", context(tree, n1_percentile=None, n1_method=None))
    assert d["status"] == NOT_COMPLETED and "DECIDE" in d["error"]
    d = run_tasks.run_task("A2", context(tree, a2_threshold_mm=None))   # SA-D68: nothing to compare
    assert d["status"] == NOT_RUN and d["note"] == run_tasks.A2_NOT_RUN_NOTE and "error" not in d
    d = run_tasks.run_task("L0", context(tree, field=None))
    assert d["status"] == NOT_COMPLETED and "--field" in d["error"]


def test_a_crash_leaves_no_stale_file(tree, monkeypatch):
    (tree["tasks"]).mkdir(parents=True, exist_ok=True)
    (tree["tasks"] / "A1.json").write_text(json.dumps({"id": "A1", "status": "pass"}), encoding="utf-8")

    def killed(ctx):
        raise KeyboardInterrupt      # not caught: the process dies, as at the hard stop
    monkeypatch.setitem(run_tasks.RUNNERS, "A1", killed)
    with pytest.raises(KeyboardInterrupt):
        run_tasks.run_task("A1", context(tree))
    assert not (tree["tasks"] / "A1.json").exists()
    assert scoreboard.read_tasks(tree["tasks"], planned=["A1"])["A1"]["status"] == NOT_COMPLETED


def test_transcriptions_must_agree_and_the_error_shows_no_value(tree):
    rows = transcription_rows()
    rows[3]["soy_yield"] = rows[3]["soy_yield_kg_ha"] = rows[3]["soy_yield"] + 1
    write_csv(tree["manual"] / "trials_b.csv", TRANSCRIPTION_COLUMNS, rows)
    d = run_tasks.run_task("T1", context(tree))
    assert d["status"] == NOT_COMPLETED and "differ (M4.6)" in d["error"]
    for v in (str(r["soy_yield"]) for r in transcription_rows()):
        assert v not in d["error"]


def test_engine_outcome_counts_against_the_model(tree):
    path = tree["runs"] / "trials_synthetic_site_a.csv"
    rows = list(csv.DictReader(open(path, encoding="utf-8")))
    rows[0].update(outcome="sowing_not_triggered", stop_reason="sowing_not_triggered", dry_yield_t_ha="")
    write_csv(path, TRIAL_COLUMNS, rows)
    d = run_tasks.run_task("T0", context(tree))
    assert d["status"] in ("pass", "fail") and d["n"] == 10     # read as a yield of 0, not as a gap


def test_setup_stop_leaves_the_value_missing(tree):
    path = tree["runs"] / "trials_synthetic_site_a.csv"
    rows = list(csv.DictReader(open(path, encoding="utf-8")))
    rows[0].update(outcome="sowing_before_previous_harvest", stop_reason="sowing_before_previous_harvest",
                   dry_yield_t_ha="")
    write_csv(path, TRIAL_COLUMNS, rows)
    d = run_tasks.run_task("T0", context(tree))
    assert d["status"] == NOT_COMPLETED and "IncompleteInput" in d["error"]


def test_p1_missing_shift_is_not_completed(tree):
    for site in SITES:
        path = tree["runs"] / f"trials_{site}_p1.csv"
        rows = [r for r in csv.DictReader(open(path, encoding="utf-8")) if r["shift_k"] != "36"]
        write_csv(path, TRIAL_COLUMNS, rows)
    d = run_tasks.run_task("P1", context(tree))
    assert d["status"] == NOT_COMPLETED and d["t1"]["missing_shifts"] == [36]


def test_l0_repeat_ignores_timing_and_fails_on_a_changed_value(tree):
    assert run_tasks.run_task("L0", context(tree))["status"] == "pass"
    rows = [dict(r, run_key="field_example_repeat_2019", variant="repeat", run=2) for r in chain_rows("repeat")]
    rows[2]["dry_yield_t_ha"] = 3.1000001
    write_csv(tree["runs"] / "chain_example_repeat.csv", CHAIN_COLUMNS, rows)
    d = run_tasks.run_task("L0", context(tree))
    assert d["status"] == "fail" and d["failed_on"] == ["repeat"]


def test_l0_on_several_fields_fails_if_any_field_fails(tree):
    """--field a,b,c (R2): each field alone, aggregated; the settings text stays in the task file."""
    import shutil
    for suffix in ("", "_repeat", "_cn_rew"):
        shutil.copy(tree["runs"] / f"chain_example{suffix}.csv", tree["runs"] / f"chain_second{suffix}.csv")
    d = run_tasks.run_task("L0", context(tree, field="example,second", l0_settings="years 2019"))
    assert d["status"] == "pass" and set(d["fields"]) == {"example", "second"}
    assert d["n_swaps"] == 2 * d["fields"]["example"]["n_swaps"] and d["chain_settings"] == "years 2019"
    rows = [dict(r, run_key="field_second_repeat_2019", variant="repeat", run=2) for r in chain_rows("repeat")]
    rows[2]["dry_yield_t_ha"] = 3.1000001
    write_csv(tree["runs"] / "chain_second_repeat.csv", CHAIN_COLUMNS, rows)
    d = run_tasks.run_task("L0", context(tree, field="example,second"))
    assert d["status"] == "fail" and d["failed_on"] == ["repeat"] and d["notes"] == ["fields failed: second"]
    assert d["fields"]["example"]["status"] == "pass"


# Before the tag --------------------------------------------------------------------------------

def test_trial_tasks_read_nothing_before_the_tag(tmp_path):
    tree = make_tree(tmp_path, status="draft")
    shutil.rmtree(tree["manual"])                         # nothing to read: a read would crash
    for tid in run_tasks.TRIAL_TASKS:
        d = run_tasks.run_task(tid, context(tree))
        assert d["status"] == NOT_RUN and "tag" in d["note"], tid
        assert not any("manual" in p or "trials_" in p for p in d["inputs"])
    assert run_tasks.run_task("L0", context(tree))["status"] == "pass"   # no trial value: runs


def test_unknown_task_is_refused(tree, capsys):
    assert run_tasks.main(["T9", "--data", str(tree["data"])]) == 2
    assert "unknown tasks" in capsys.readouterr().err


def test_h1_without_results_is_not_run(tree):
    (tree["data"] / "bench" / "h1_results.json").unlink()
    assert run_tasks.run_task("H1", context(tree))["status"] == NOT_RUN


def test_h1_reserve_enters_only_for_a_session_not_completed(tree):
    d = run_tasks.run_task("H1", context(tree))
    assert d["sessions_counted"] == ["h1-1", "h1-3", "h1-4"]
    assert d["sessions_not_counted"] == ["h1-2", "h1-5"]


# Yield basis and event status (SA-D65) ------------------------------------------------------

def test_simulated_yield_is_compared_at_13_percent_moisture():
    row = {"outcome": "simulated", "dry_yield_t_ha": "0.87"}
    assert run_tasks.simulated_kg_ha(row) == pytest.approx(1000.0)
    assert run_tasks.simulated_kg_ha({"outcome": "sowing_not_triggered"}) == 0.0


def test_the_declared_basis_is_checked_and_recorded(tree):
    d = run_tasks.run_task("T1", context(tree))
    assert d["status"] == "pass"
    assert d["yield_basis"]["basis"].startswith("13% moisture")
    assert set(d["yield_basis"]["declared"]) == set(SITES)
    assert any(p.endswith("trial_dates.json") for p in d["inputs"])
    meta = tree["config"].parent / "trial_dates.json"
    meta.write_text(json.dumps(trial_meta(moisture=14)), encoding="utf-8")
    d = run_tasks.run_task("T1", context(tree))
    assert d["status"] == NOT_COMPLETED and "14% moisture" in d["error"]


def test_l1_and_r1_planned_not_run_read_nothing(tree):
    cfg = yaml.safe_load(tree["config"].read_text(encoding="utf-8"))
    cfg["protocol"]["tests"]["L1"]["event_status"] = "planned_not_run"
    cfg["protocol"]["r1"]["event_status"] = "planned_not_run"
    tree["config"].write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")
    ctx = context(tree)
    run_tasks.run_task("T1", ctx)                     # a T1 pass does not make R1 run in the event
    for tid in ("L1", "R1"):
        d = run_tasks.run_task(tid, ctx)
        assert d["status"] == NOT_RUN, d
        assert not any("l1_stations" in p or "r1_members" in p for p in d["inputs"])
    assert "planned, not run" in task_file(tree, "L1")["note"]
    assert "planned, not run" in task_file(tree, "R1")["notes"][0]

# Written with an SI tool (Claude, Anthropic) for NASA Space Apps 2026. See AI_USE.md.
"""Engine command line (pipeline/engine/cli.py), on synthetic inputs only.

Synthetic data only: a made-up site, a made-up POWER series and a made-up soil,
labelled "example, fictitious". No trial site, no trial value, no real weather.

- the guard: no trial run while protocol.status is not "tagged" (exit 2, nothing written);
- --dry-run lists the runs, imports no aquacrop and flags shifted chains that start
  before the climate series;
- the P1 offset equals the bench rule (pipeline.bench.p1.chain_offset);
- the engine never imports the bench and never names the manual data folder (M4.1);
- with aquacrop (.venv-engine): a synthetic trial run and a synthetic field run write
  the CSV files, timing.json and artifacts.json, and the repeat run is identical.

Runs in both venvs: .venv skips the aquacrop part, .venv-engine needs no PyYAML here.
"""
from __future__ import annotations

import ast
import csv
import json
import math
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pipeline.engine import cli  # noqa: E402
from pipeline.engine.regimes import WARMUP_DATES  # noqa: E402

LABEL = "example, fictitious"
SITE = "synthetic_site_a"
PARAMS = ("T2M_MAX", "T2M_MIN", "T2MDEW", "WS2M", "ALLSKY_SFC_SW_DWN", "PRECTOTCORR")
UNITS = {"T2M_MAX": "C", "T2M_MIN": "C", "T2MDEW": "C", "WS2M": "m/s",
         "ALLSKY_SFC_SW_DWN": "MJ/m^2/day", "PRECTOTCORR": "mm/day"}


# Synthetic inputs -----------------------------------------------------------------

def protocol(status="tagged", *, seasons=("2002/03",), climate=("1999-01-01", "2004-12-31"),
             shifts=(1,), circular=(2001, 2004), run="continuous_per_site_and_arm"):
    return {
        "id": "protocol-example", "status": status, "_note": LABEL,
        "dates": {"climate": list(climate)},
        "trial_site_seasons": {SITE: {"doc": "none, synthetic", "seasons": list(seasons)}},
        "engine": {
            "ksat": {"mode": "measured", "value_mm_h": 60.0, "source": LABEL, "layer_m": [0.0, 0.3],
                     "sensitivity": "texture_formula"},
            "sensitivity_only": {"cn": 61, "rew": 9, "zmax": "library_default"},
        },
        "sowing": {"soybean_field": {"rain_mm": 20, "rain_days": 3, "start": "09-01", "latest": "12-31",
                                     "rain_after_not_before": True, "s3_not_before_wheat_harvest_plus_days": 20}},
        "chains": {"trial": {"run": run, "warmup_dates": WARMUP_DATES, "initial_state": "field_capacity"},
                   "artifact_checks": ["days_to_emergence", "zeroed_season", "dead_canopy"]},
        "tests": {"L0": {"closure_mm_per_swap": 1.0}, "A1": {"restart_profile": "field_capacity"},
                  "P1": {"shifts_back_years": list(shifts), "circular_years": list(circular)}},
    }


def config(**kw):
    first, last = kw.get("climate", ("1999-01-01", "2004-12-31"))
    return {
        "_note": LABEL, "region": "example_region",
        "trial_sites": [{"id": SITE, "name": "Synthetic A", "lat": -23.2, "lon": -51.2, "altitude_m": 500}],
        "sources": {"power": {"source_id": "power_daily", "calls": [
            {"id": "met", "parameters": list(PARAMS), "start": first, "end": last, "time_standard": "LST"}]}},
        "protocol": protocol(**kw),
    }


def dates_doc(seasons=("2002/03",)):
    rows = []
    for s in seasons:
        y = int(s[:4])
        rows.append({"season": s, "soybean_sowing": f"{y}-10-25",
                     "predecessor_sowing": {"wheat": f"{y}-05-10", "maize": f"{y}-03-01"}})
    return {"_note": LABEL, "sites": {SITE: rows}}


def synthetic_power(first: date, last: date) -> dict:
    """A smooth southern-hemisphere year: warm and wet from September to April, cool and drier
    from May to August. Rain falls every second day, so the 20 mm in 3 days rule triggers."""
    series = {p: {} for p in PARAMS}
    d = first
    while d <= last:
        c = math.cos(2 * math.pi * (d.timetuple().tm_yday - 15) / 365.25)
        key = d.strftime("%Y%m%d")
        wet = d.month in (9, 10, 11, 12, 1, 2, 3, 4)
        tmin = round(15.0 + 5.0 * c, 2)
        values = {"T2M_MAX": round(26.0 + 5.0 * c, 2), "T2M_MIN": tmin, "T2MDEW": round(tmin - 1.5, 2),
                  "WS2M": 2.0, "ALLSKY_SFC_SW_DWN": round(18.0 + 6.0 * c, 2),
                  "PRECTOTCORR": (12.0 if d.toordinal() % 2 == 0 else 0.0) if wet
                  else (8.0 if d.toordinal() % 4 == 0 else 0.0)}
        for p, v in values.items():
            series[p][key] = v
        d += timedelta(days=1)
    return {"_note": LABEL, "header": {"fill_value": -999.0, "sources": ["MERRA2"]},
            "properties": {"parameter": series}, "parameters": {p: {"units": u} for p, u in UNITS.items()}}


SOIL = {"_note": LABEL, "layers": [
    {"depth": "0-30cm", "top_m": 0.0, "bottom_m": 0.3, "sand_pct": 15.0, "clay_pct": 65.0, "om_pct": 2.5},
    {"depth": "30-240cm", "top_m": 0.3, "bottom_m": 2.4, "sand_pct": 12.0, "clay_pct": 70.0, "om_pct": 1.0},
]}


def write_inputs(tmp: Path, cfg: dict, *, seasons=("2002/03",), field_id="example") -> dict:
    data = tmp / "data"
    first, last = (date.fromisoformat(x) for x in cfg["protocol"]["dates"]["climate"])
    power = cli.power_path(cfg, data / "raw", SITE)
    power.parent.mkdir(parents=True)
    power.write_text(json.dumps(synthetic_power(first, last)), encoding="utf-8")
    interim = data / "interim"
    interim.mkdir(parents=True)
    segs = [{"parameter": p, "start": first.isoformat(), "end": last.isoformat(), "sources": ["MERRA2"]}
            for p in PARAMS]
    (interim / "power_summary.json").write_text(json.dumps({"_note": LABEL, "source_segments": {"met": segs}}),
                                                encoding="utf-8")
    (interim / f"trial_{SITE}_soil.json").write_text(json.dumps(SOIL), encoding="utf-8")
    (interim / f"field_{field_id}_soil.json").write_text(json.dumps(SOIL), encoding="utf-8")
    cfg_path = tmp / "config.json"
    cfg_path.write_text(json.dumps(cfg), encoding="utf-8")
    dates_path = tmp / "trial_dates.json"
    dates_path.write_text(json.dumps(dates_doc(seasons)), encoding="utf-8")
    return {"data": data, "config": cfg_path, "dates": dates_path, "runs": data / "runs"}


def trial_args(p, *extra):
    return ["trials", "--config", str(p["config"]), "--data", str(p["data"]),
            "--trial-dates", str(p["dates"]), *extra]


FIELD_ARGS = ["--field", "example", "--years", "2002", "--prefix-years", "0", "--corn-sowing", "after_harvest",
              "--wheat-sowing", "05-01", "--initial", "field_capacity", "--weather-site", SITE]


def read_rows(path):
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


# Guard -----------------------------------------------------------------------------

@pytest.mark.parametrize("status", ["draft", None, "Tagged"])
def test_trial_runs_are_refused_before_the_tag(tmp_path, capsys, status):
    cfg = config(status=status)
    p = write_inputs(tmp_path, cfg)
    assert cli.main(trial_args(p)) == 2
    err = capsys.readouterr().err
    assert "refused" in err and "tagged" in err and "trial sites" in err
    assert not p["runs"].exists()


def test_guard_comes_before_any_input_is_read(tmp_path, capsys):
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(json.dumps(config(status="draft")), encoding="utf-8")
    missing = tmp_path / "nothing_here"
    code = cli.main(["trials", "--config", str(cfg_path), "--data", str(missing),
                     "--trial-dates", str(missing / "dates.json")])
    assert code == 2
    assert "refused" in capsys.readouterr().err


def test_require_tagged():
    cli.require_tagged({"status": "tagged"})
    with pytest.raises(cli.Refused):
        cli.require_tagged({"status": "draft"})
    with pytest.raises(cli.Refused):
        cli.require_tagged({})


# Dry run ----------------------------------------------------------------------------

def test_dry_run_lists_the_runs_and_the_guard(tmp_path, capsys):
    p = write_inputs(tmp_path, config(status="draft"))
    assert cli.main(trial_args(p, "--dry-run")) == 0
    out = capsys.readouterr().out
    assert "would be refused" in out
    for key in (f"trial_{SITE}_base", f"trial_{SITE}_a1", f"trial_{SITE}_p1_k1_o-1"):
        assert key in out
    assert f"trials_{SITE}.csv" in out and f"trials_{SITE}_a1.csv" in out and f"trials_{SITE}_p1.csv" in out
    assert "3 runs" in out
    assert not p["runs"].exists()


def test_dry_run_of_a_tagged_protocol_says_allowed(tmp_path, capsys):
    p = write_inputs(tmp_path, config())
    assert cli.main(trial_args(p, "--dry-run", "--variants", "base")) == 0
    out = capsys.readouterr().out
    assert "trial runs allowed" in out and "1 runs" in out


def test_dry_run_puts_the_p1_warmup_on_the_ring(tmp_path, capsys):
    # p1_warmup: the years of a shifted chain before 1985 follow the 1985-2024 ring, so no
    # P1 run is refused for climate before 1984 (SA-D65).
    seasons = ("2020/21", "2021/22")
    cfg = config(status="draft", seasons=seasons, climate=("1984-01-01", "2025-12-31"),
                 shifts=(4, 36), circular=(1985, 2024))
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(json.dumps(cfg), encoding="utf-8")
    dates = tmp_path / "dates.json"
    dates.write_text(json.dumps(dates_doc(seasons)), encoding="utf-8")
    code = cli.main(["trials", "--config", str(cfg_path), "--data", str(tmp_path / "data"),
                     "--trial-dates", str(dates), "--variants", "p1", "--dry-run"])
    assert code == 0
    lines = capsys.readouterr().out.splitlines()
    k4 = [x for x in lines if "_p1_k4_" in x]
    k36 = [x for x in lines if "_p1_k36_" in x]
    assert len(k4) == 1 and "climate ok" in k4[0] and "ring" not in k4[0]   # both seasons move by -4
    assert len(k36) == 2                                  # 2020 wraps to 2024 (+4), 2021 goes to 1985 (-36)
    plus4 = next(x for x in k36 if "_o+4" in x)
    minus36 = next(x for x in k36 if "_o-36" in x)
    assert "climate ok" in plus4
    # warm-up 2018 and 2019, and the untested 2020/21, move to 1982 to 1984: ring years 2022 to 2024
    assert "climate ok" in minus36 and "ring 1982-1984 -> 2022-2024" in minus36
    assert "(0 refused by the climate check)" in lines[-2]
    assert lines[-1] == "8 chains (site-season arms): p1 8"    # 2 shifts x 2 site-seasons x 2 arms


def test_ring_seam_never_reaches_a_tested_season():
    from pipeline.engine.regimes import Regime, Restart, Season

    regime = Regime("example", date(1982, 9, 1), Restart("field_capacity"),
                    {"a": (Season("soybean", date(1982, 10, 1)),)})
    ring = cli.ring_years(regime, 1985, 2024)
    assert ring == {1982: 2022, 1983: 2023, 1984: 2024}
    climate = (date(1984, 1, 1), date(2025, 12, 31))
    assert cli.climate_problems(regime, climate, ring, cli.tested_years(["1985/86"], 0)) == []
    assert "seam" in " ".join(cli.climate_problems(regime, climate, ring, cli.tested_years(["1984/85"], 0)))
    odd = cli.ring_years(regime, 1985, 2003)           # a 19-year ring breaks the leap years
    assert "29 February" in " ".join(cli.climate_problems(regime, climate, odd))


def test_rain_flag_must_agree_with_the_protocol(tmp_path, capsys):
    p = write_inputs(tmp_path, config(status="draft"))
    assert cli.main(trial_args(p, "--dry-run", "--rain-after-not-before", "true")) == 0
    capsys.readouterr()
    assert cli.main(trial_args(p, "--dry-run", "--rain-after-not-before", "false")) == 1
    assert "rain_after_not_before" in capsys.readouterr().err


def test_dates_must_match_the_protocol_seasons(tmp_path, capsys):
    p = write_inputs(tmp_path, config())
    p["dates"].write_text(json.dumps(dates_doc(("2003/04",))), encoding="utf-8")
    assert cli.main(trial_args(p, "--dry-run")) == 1
    assert "differ from protocol.trial_site_seasons" in capsys.readouterr().err


def test_unknown_chain_mode_is_a_protocol_gap(tmp_path, capsys):
    p = write_inputs(tmp_path, config(run="something_else"))
    assert cli.main(trial_args(p, "--dry-run")) == 1
    assert "DECIDE D7" in capsys.readouterr().err


def test_field_dry_run_lists_side_runs_and_repeat(tmp_path, capsys):
    p = write_inputs(tmp_path, config(status="draft"))
    code = cli.main(["field", "--config", str(p["config"]), "--data", str(p["data"]), *FIELD_ARGS,
                     "--setups", "base,cn_rew", "--repeat", "--dry-run"])
    assert code == 0
    out = capsys.readouterr().out
    assert "chain_example.csv" in out and "chain_example_repeat.csv" in out and "chain_example_cn_rew.csv" in out
    assert "3 runs" in out


def test_field_needs_the_open_points(tmp_path, capsys):
    p = write_inputs(tmp_path, config())
    with pytest.raises(SystemExit):
        cli.main(["field", "--config", str(p["config"]), "--field", "example", "--years", "2002"])


# P1 offset ----------------------------------------------------------------------------

def test_p1_offset_equals_the_bench_rule():
    p1 = pytest.importorskip("pipeline.bench.p1")
    design = p1.P1Design(shifts=p1.PLAN_SHIFTS_YEARS, first_year=1985, last_year=2024,
                         alpha=__import__("fractions").Fraction(1, 10))
    for k in p1.PLAN_SHIFTS_YEARS:
        for y in range(1985, 2025):
            assert cli.p1_offset(y, k, 1985, 2024) == p1.chain_offset(y, k, design)
    with pytest.raises(ValueError):
        cli.p1_offset(1984, 4, 1985, 2024)


# Leakage (M4.1) ------------------------------------------------------------------------

def test_engine_does_not_import_the_bench_nor_name_the_manual_folder():
    for path in sorted((ROOT / "pipeline" / "engine").glob("*.py")):
        text = path.read_text(encoding="utf-8")
        assert "data/manual" not in text and "data\\manual" not in text, path.name
        for node in ast.walk(ast.parse(text)):
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [("." * node.level) + (node.module or "")]
            else:
                continue
            assert not any("bench" in n for n in names), (path.name, names)


def test_config_json_needs_yaml_and_round_trips(tmp_path):
    pytest.importorskip("yaml")
    out = tmp_path / "config.json"
    assert cli.main(["config-json", "--config", str(ROOT / "config" / "region_norte_pr.yaml"),
                     "--out", str(out)]) == 0
    doc = json.loads(out.read_text(encoding="utf-8"))
    assert doc["protocol"]["id"] == "protocol-v1" and "trial_sites" in doc


# Real runs on synthetic weather (.venv-engine) ---------------------------------------------

@pytest.fixture(scope="module")
def trial_run(tmp_path_factory):
    pytest.importorskip("aquacrop")
    tmp = tmp_path_factory.mktemp("trial")
    p = write_inputs(tmp, config())
    code = cli.main(trial_args(p))
    return p, code


def test_trial_run_writes_every_output(trial_run):
    p, code = trial_run
    assert code == 0
    runs = p["runs"]
    for name in (f"trials_{SITE}.csv", f"trials_{SITE}_a1.csv", f"trials_{SITE}_p1.csv",
                 "timing.json", "artifacts.json"):
        assert (runs / name).exists(), name
    rows = read_rows(runs / f"trials_{SITE}.csv")
    assert list(rows[0]) == list(cli.TRIAL_COLUMNS)
    targets = [r for r in rows if r["role"] == "target"]
    assert {r["arm"] for r in targets} == {"after_wheat", "after_maize"}
    assert {r["season"] for r in targets} == {"2002/03"}
    for r in targets:
        assert r["outcome"] == "simulated" and float(r["dry_yield_t_ha"]) >= 0
        assert r["sowing"] == "2002-10-25"
    # crop end: the tested soybean ends on the same day in both arms, with the rule that ended it
    assert len({r["harvest"] for r in targets}) == 1
    assert all(r["end_rule"] in ("maturity", "crop_dead", "common_end", "next_sowing", "published_harvest",
                                 "horizon") for r in rows)
    a1 = [r for r in read_rows(runs / f"trials_{SITE}_a1.csv") if r["role"] == "target"]
    assert all(r["restarted"] == "true" for r in a1)
    p1 = [r for r in read_rows(runs / f"trials_{SITE}_p1.csv") if r["role"] == "target"]
    assert {(r["shift_k"], r["offset_years"]) for r in p1} == {("1", "-1")}
    assert all(r["sowing"] == "2001-10-25" for r in p1)
    # the shifted warm-up starts in 1999, before the 2001-2004 ring: 1999 and 2000 take 2003 and 2004
    timing = json.loads((runs / "timing.json").read_text(encoding="utf-8"))
    assert timing["runs"][f"trial_{SITE}_p1_k1_o-1"]["ring_years"] == {"1999": 2003, "2000": 2004}


def test_trial_timing_and_artifacts(trial_run):
    p, _ = trial_run
    timing = json.loads((p["runs"] / "timing.json").read_text(encoding="utf-8"))
    assert timing["machine"]["cpu_count"] >= 1
    base = timing["runs"][f"trial_{SITE}_base"]
    assert base["instances"] == 7 and base["seconds_per_instance"] > 0      # 3 warm-up shared, 2 per arm
    art = json.loads((p["runs"] / "artifacts.json").read_text(encoding="utf-8"))
    rep = art["regimes"][f"trial_{SITE}_base"]
    assert set(rep["arms"]) == {"after_wheat", "after_maize"}
    assert rep["checks"] == ["days_to_emergence", "zeroed_season", "dead_canopy"]
    for arm in rep["arms"].values():
        assert all("days_to_emergence" in s for s in arm["seasons"])


def test_a_rerun_replaces_its_rows_and_keeps_the_others(trial_run, tmp_path):
    p, _ = trial_run
    before = read_rows(p["runs"] / f"trials_{SITE}.csv")
    target = tmp_path / "copy.csv"
    rows = [dict(r) for r in before]
    cli.write_csv(target, cli.TRIAL_COLUMNS, rows)
    extra = dict(rows[0], run_key="another_run")
    cli.write_csv(target, cli.TRIAL_COLUMNS, [extra], replaces=["another_run"])
    cli.write_csv(target, cli.TRIAL_COLUMNS, rows, replaces=[rows[0]["run_key"]])
    after = read_rows(target)
    assert len(after) == len(rows) + 1
    assert sum(1 for r in after if r["run_key"] == "another_run") == 1


@pytest.fixture(scope="module")
def field_run(tmp_path_factory):
    pytest.importorskip("aquacrop")
    tmp = tmp_path_factory.mktemp("field")
    p = write_inputs(tmp, config(status="draft"))
    code = cli.main(["field", "--config", str(p["config"]), "--data", str(p["data"]), *FIELD_ARGS,
                     "--setups", "base,cn_rew", "--repeat"])
    return p, code


def test_field_run_repeats_exactly_and_writes_the_side_run(field_run):
    p, code = field_run
    assert code == 0
    runs = p["runs"]
    first = read_rows(runs / "chain_example.csv")
    second = read_rows(runs / "chain_example_repeat.csv")
    side = read_rows(runs / "chain_example_cn_rew.csv")
    assert list(first[0]) == list(cli.CHAIN_COLUMNS)
    strip = set(cli.TIMING_COLUMNS) | {"run_key", "variant", "run"}
    assert [{k: v for k, v in r.items() if k not in strip} for r in first] == \
           [{k: v for k, v in r.items() if k not in strip} for r in second]
    assert {r["arm"] for r in first} == {"S1", "S3"}
    gaps = [float(r["handover_gap_mm"]) for r in first if r["handover_gap_mm"]]
    assert gaps and max(gaps) <= 1.0
    assert all(r["setup"] == "cn_rew" for r in side)
    art = json.loads((runs / "artifacts.json").read_text(encoding="utf-8"))
    assert {"field_example_base_2002", "field_example_repeat_2002", "field_example_cn_rew_2002"} <= set(art["regimes"])


# Soil sensitivity run (SA-D77): side soil file, separate runs folder ---------------------------

def test_soil_path_takes_the_suffix_for_trials_only(tmp_path):
    assert cli.soil_path(tmp_path, "trial", "x") == tmp_path / "interim" / "trial_x_soil.json"
    assert cli.soil_path(tmp_path, "trial", "x", "_soilgrids") == tmp_path / "interim" / "trial_x_soil_soilgrids.json"
    assert cli.soil_path(tmp_path, "field", "a", "_soilgrids") == tmp_path / "interim" / "field_a_soil.json"


def test_soil_suffix_refuses_the_default_runs_folder(tmp_path, capsys):
    p = write_inputs(tmp_path, config())
    assert cli.main(trial_args(p, "--variants", "base", "--trial-soil-suffix", "_soilgrids")) != 0
    assert not p["runs"].exists()


def test_soil_suffix_run_reads_the_side_file_into_its_own_folder(trial_run, tmp_path):
    pytest.importorskip("aquacrop")
    p = write_inputs(tmp_path, config())
    side = json.loads(json.dumps(SOIL))
    for lay in side["layers"]:
        lay["clay_pct"], lay["sand_pct"] = 40.0, 30.0
    (p["data"] / "interim" / f"trial_{SITE}_soil_soilgrids.json").write_text(json.dumps(side), encoding="utf-8")
    out = tmp_path / "runs_sens"
    assert cli.main(trial_args(p, "--variants", "base", "--trial-soil-suffix", "_soilgrids",
                               "--runs", str(out))) == 0
    assert not p["runs"].exists()
    timing = json.loads((out / "timing.json").read_text(encoding="utf-8"))
    assert timing["trial_soil_files"] == {SITE: str(p["data"] / "interim" / f"trial_{SITE}_soil_soilgrids.json")}
    sens = read_rows(out / f"trials_{SITE}.csv")
    used = read_rows(trial_run[0]["runs"] / f"trials_{SITE}.csv")
    assert [r["sowing"] for r in sens] == [r["sowing"] for r in used]
    assert [r["storage_start_mm"] for r in sens] != [r["storage_start_mm"] for r in used]

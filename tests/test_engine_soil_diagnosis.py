# Written with an SI tool (Claude, Anthropic) for NASA Space Apps 2026. See AI_USE.md.
"""Soil diagnosis (SA-D77) on a synthetic profile and synthetic run files: read only.

Run: .venv-engine/bin/python -m pytest tests/test_engine_soil_diagnosis.py -q
"""
from __future__ import annotations

import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

aquacrop = pytest.importorskip("aquacrop")

from pipeline.engine import soil_diagnosis as sd  # noqa: E402
from pipeline.engine.cli import load_layers  # noqa: E402
from pipeline.engine.soilmodel import DZ, make_soil  # noqa: E402

PROTOCOL = {
    "trial_site_seasons": {"alpha": ["2019/20"]},
    "engine": {"ksat": {"mode": "measured", "value_mm_h": 60.0, "layer_m": [0.0, 0.3]}},
}


def soil_doc(clay_top: float, clay_deep: float) -> dict:
    return {"kind": "trial_soil", "rule": "synthetic", "layers": [
        {"top_m": 0.0, "bottom_m": 0.3, "sand_pct": 20.0, "clay_pct": clay_top, "om_pct": 2.0},
        {"top_m": 0.3, "bottom_m": 2.4, "sand_pct": 15.0, "clay_pct": clay_deep, "om_pct": 1.0},
    ]}


def write_runs(path: Path, yields: list[tuple[str, str, str]]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["outcome", "crop", "dry_yield_t_ha"])
        w.writeheader()
        for outcome, crop, y in yields:
            w.writerow({"outcome": outcome, "crop": crop, "dry_yield_t_ha": y})


@pytest.fixture()
def tree(tmp_path):
    data = tmp_path / "data"
    (data / "interim").mkdir(parents=True)
    runs = data / "runs"
    runs.mkdir()
    (data / "interim" / "trial_alpha_soil.json").write_text(json.dumps(soil_doc(78.0, 78.0)), encoding="utf-8")
    (data / "interim" / "trial_alpha_soil_soilgrids.json").write_text(json.dumps(soil_doc(40.0, 50.0)),
                                                                     encoding="utf-8")
    (data / "interim" / "field_a_soil.json").write_text(json.dumps(soil_doc(45.0, 50.0)), encoding="utf-8")
    config = runs / "config.json"
    config.write_text(json.dumps({"protocol": PROTOCOL}), encoding="utf-8")
    write_runs(runs / "trials_alpha.csv", [("simulated", "soybean", "0.0"), ("simulated", "maize", "0"),
                                           ("simulated", "soybean", "2.9"), ("after_stop", "wheat", "")])
    write_runs(runs / "trials_alpha_a1.csv", [("simulated", "soybean", "3.1")])
    return data, runs, config


def test_available_water_cuts_the_compartment_that_crosses_the_depth():
    # 0..0.1 and 0.1..0.3: 1 m depth takes both whole; 0.2 m takes half of the second.
    tops, bottoms = [0.0, 0.1], [0.1, 0.3]
    wp, fc = [0.2, 0.3], [0.4, 0.4]
    assert sd.available_water_mm(tops, bottoms, wp, fc, 1.0) == pytest.approx(20.0 + 20.0)
    assert sd.available_water_mm(tops, bottoms, wp, fc, 0.2) == pytest.approx(20.0 + 10.0)


def test_soil_record_matches_the_engine_soil(tree):
    data, _, _ = tree
    path = data / "interim" / "trial_alpha_soil.json"
    rec = sd.soil_record(path, PROTOCOL)
    prof = make_soil(load_layers(path, PROTOCOL, "value")).profile
    wp, fc = prof["th_wp"].to_numpy(), prof["th_fc"].to_numpy()
    bottoms = prof["zBot"].to_numpy()
    tops = bottoms - prof["dz"].to_numpy()
    expected_1m = sum((f - w) * max(0.0, min(b, 1.0) - t) * 1000 for t, b, w, f in zip(tops, bottoms, wp, fc))
    assert rec["aw_mm_0_1m"] == pytest.approx(expected_1m, abs=0.05)
    assert rec["aw_mm_0_2_4m"] > rec["aw_mm_0_1m"]
    assert rec["clay_pct_min"] == rec["clay_pct_max"] == 78.0 and rec["above_clay_limit"] is True
    # Groups cover the whole compartment grid without gaps, and carry the engine's limits.
    g = rec["groups"]
    assert g[0]["top_m"] == 0.0 and g[-1]["bottom_m"] == pytest.approx(sum(DZ))
    assert all(a["bottom_m"] == pytest.approx(b["top_m"]) for a, b in zip(g, g[1:]))
    assert g[0]["wp"] == pytest.approx(wp[0], abs=5e-4) and g[0]["sat"] == pytest.approx(prof["th_s"].iloc[0], abs=5e-4)
    assert rec["sha256"] == sd.sha256(path)


def test_high_clay_gives_less_available_water_than_the_moderate_profile(tree):
    data, _, _ = tree
    used = sd.soil_record(data / "interim" / "trial_alpha_soil.json", PROTOCOL)
    sens = sd.soil_record(data / "interim" / "trial_alpha_soil_soilgrids.json", PROTOCOL)
    assert used["aw_mm_0_1m"] < sens["aw_mm_0_1m"]
    assert sens["above_clay_limit"] is False


def test_zero_yield_counts_only_simulated_rows(tree):
    _, runs, _ = tree
    z = sd.zero_yield_counts(runs / "trials_alpha.csv")
    assert z["simulated"] == 3 and z["zero_yield"] == 2
    assert z["zero_by_crop"] == {"maize": 1, "soybean": 1}
    assert sd.zero_yield_counts(runs / "trials_missing.csv") is None


def test_diagnose_end_to_end_and_main_writes_the_file(tree, capsys):
    data, runs, config = tree
    now = datetime(2000, 1, 1, 0, 0, tzinfo=timezone.utc)
    doc = sd.diagnose(config, data, runs, ["a", "zz"], now=now)
    assert doc["written_at"] == "2000-01-01T00:00:00Z"
    site = doc["sites"]["alpha"]
    assert set(site["soils"]) == {"used", "sensitivity"}
    assert site["zero_yield"]["base"]["zero_yield"] == 2
    assert site["zero_yield"]["a1"]["zero_yield"] == 0 and site["zero_yield"]["p1"] is None
    assert doc["field_soils"]["a"]["clay_pct_max"] == 50.0 and doc["field_soils"]["zz"] is None
    out = data / "bench" / "soil_diagnosis.json"
    assert sd.main(["--config", str(config), "--data", str(data), "--fields", "a", "--out", str(out)]) == 0
    written = json.loads(out.read_text(encoding="utf-8"))
    assert written["sites"]["alpha"]["soils"]["used"]["aw_mm_0_1m"] == site["soils"]["used"]["aw_mm_0_1m"]
    printed = capsys.readouterr().out
    assert "alpha" in printed and "2/3" in printed


# L0 field chains: dead crops read from existing outputs ----------------------------------------

CHAIN_FIELDS = ["arm", "year", "label", "crop", "role", "outcome", "shared_prefix", "sowing", "harvest", "end_rule",
                "crop_dead", "germinated", "days_to_emergence", "canopy_max", "storage_end_mm", "rain_mm"]


def chain_row(arm, label, crop, sowing, harvest, dead=True, prefix="false", dte="5"):
    return {"arm": arm, "year": "2019", "label": label, "crop": crop, "role": "predecessor", "outcome": "simulated",
            "shared_prefix": prefix, "sowing": sowing, "harvest": harvest,
            "end_rule": "crop_dead" if dead else "maturity", "crop_dead": "true" if dead else "false",
            "germinated": "true", "days_to_emergence": dte, "canopy_max": "0.5", "storage_end_mm": "1000.04",
            "rain_mm": "300"}


def test_dead_crops_counts_a_shared_prefix_once_and_reads_the_cold_days(tmp_path):
    from datetime import date, timedelta

    p = tmp_path / "chain_x.csv"
    with open(p, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=CHAIN_FIELDS)
        w.writeheader()
        w.writerow(chain_row("S1", "2019:maize", "maize", "2019-03-22", "2019-07-22", prefix="true"))
        w.writerow(chain_row("S3", "2019:maize", "maize", "2019-03-22", "2019-07-22", prefix="true"))
        w.writerow(chain_row("S1", "2020:maize", "maize", "2020-03-22", "2020-04-05", dte=""))
        w.writerow(chain_row("S3", "2019/20:soybean", "soybean", "2019-11-01", "2020-03-01", dead=False))
    tmin = {date(2019, 7, 22) - timedelta(days=i): 10.0 + i for i in range(15)}
    tmin[date(2019, 7, 12)] = -3.0                       # 10 days before: outside the window
    deaths = sd.dead_crops(p, tmin)
    assert [(d["label"], d["days_after_sowing"]) for d in deaths] == [("2019:maize", 122), ("2020:maize", 14)]
    assert deaths[0]["min_tmin_c_10d_before"] == 10.0 and deaths[0]["min_tmin_date"] == "2019-07-22"
    assert "min_tmin_c_10d_before" not in deaths[1]       # no weather for that date
    assert deaths[0]["storage_end_mm"] == 1000.0 and deaths[1]["days_to_emergence"] is None
    line = sd.deaths_line(deaths, [1050.0])
    assert "2 dead maize crops" in line and "14 to 122 days after sowing" in line
    assert "No emergence in 2020:maize" in line and "field capacity 1050" in line and "10.0 to 10.0" in line
    assert sd.dead_crops(tmp_path / "missing.csv") == []


def test_l0_artifact_counts_by_kind_and_crop_year(tmp_path):
    l0 = {"status": "fail", "n_unexplained": 3, "fields": {
        "a": {"artifacts": [{"kind": "dead_canopy", "season": "2018 2018:maize"},
                            {"kind": "zeroed_season", "season": "2021 2021:maize"}]},
        "b": {"artifacts": [{"kind": "dead_canopy", "season": "2019 2018:maize"}]}}}
    p = tmp_path / "L0.json"
    p.write_text(json.dumps(l0), encoding="utf-8")
    c = sd.l0_artifact_counts(p)
    assert c["n_artifacts"] == 3 and c["by_kind"] == {"dead_canopy": 2, "zeroed_season": 1}
    assert c["by_crop_year_and_field"] == {"maize 2018": {"a": 1, "b": 1}, "maize 2021": {"a": 1}}
    assert sd.l0_artifact_counts(tmp_path / "none.json") is None


def test_diagnose_states_the_l0_field_chains_are_not_explained(tree):
    data, runs, config = tree
    doc = sd.diagnose(config, data, runs, ["a"])
    l0 = doc["l0_field_chains"]
    assert l0["explained_by_this_diagnosis"] is False
    assert "does not explain them" in l0["statement"] and "inside the clay limit" in l0["statement"]
    assert l0["weather_error"]                              # no weather in the synthetic tree: no temperatures
    assert l0["maize_deaths_line"] == "no dead maize in the field chains"
    assert doc["field_soils"]["a"]["fc_storage_mm_profile"] > doc["field_soils"]["a"]["wp_storage_mm_profile"]

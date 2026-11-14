# Written with an SI tool (Claude, Anthropic) for NASA Space Apps 2026. See AI_USE.md.
"""Trial soil records and the printed Floresta POWER spec (pipeline/adapters/trial_inputs.py).

Pure parts only: SoilGrids point values are invented here ("example, fictitious"),
no network, no trial value. Runs in .venv.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

yaml = pytest.importorskip("yaml")

from pipeline.adapters import trial_inputs as TI  # noqa: E402

SG = {d: {"clay_pct": 50.0 + i, "sand_pct": 20.0, "soc_g_kg": 10.0 - i, "files": []}
      for i, d in enumerate(TI.DEPTHS)}          # example, fictitious


def test_doc_texture_is_extended_to_the_whole_profile():
    # SA-D65 as amended: Latossolo, homogeneous B horizon, the Doc texture in every layer
    rec = TI.build("cafelandia", SG, lat=-1.0, lon=-1.0, min_depth_m=2.4)
    assert rec["kind"] == "trial_soil" and rec["estimate"] is False
    lay = {(x["top_m"], x["bottom_m"]): x for x in rec["layers"]}
    assert list(lay) == [(0.0, 0.05), (0.05, 0.15), (0.15, 0.2), (0.2, 0.3), (0.3, 0.6), (0.6, 1.0), (1.0, 2.4)]
    for x in rec["layers"]:
        assert (x["clay_pct"], x["sand_pct"], x["silt_pct"]) == (77.0, 9.9, 13.1)
        assert "Doc 459" in x["source"]["clay_pct"] and "extended in depth (Latossolo)" in x["source"]["sand_pct"]
    top, deep = lay[(0.0, 0.05)], lay[(1.0, 2.4)]
    assert top["om_pct"] == round(18.6 * 1.724 / 10, 3) and "printed layer only" in top["source"]["om_pct"]
    assert "SoilGrids 2.0 15-30cm" in lay[(0.2, 0.3)]["source"]["om_pct"]       # below the printed 0-0.2 m
    assert deep["om_pct"] == round(5.0 * 1.724 / 10, 3) and deep["extended_from_m"] == 2.0
    assert rec["checks"][0]["soilgrids_clay_pct"]["0-5cm"] == 50.0
    # contiguous from the surface, as pipeline.engine.cli.load_layers needs
    assert all(a["bottom_m"] == b["top_m"] for a, b in zip(rec["layers"], rec["layers"][1:]))


def test_floresta_takes_the_class_lower_bound_and_the_soilgrids_sand_silt_ratio():
    rec = TI.build("floresta", SG, lat=-1.0, lon=-1.0, min_depth_m=2.4)
    used = rec["texture_used"]
    # SoilGrids here: sand 20 everywhere, silt 30 - i by depth; thickness-weighted over 0-2 m
    th = [0.05, 0.1, 0.15, 0.3, 0.4, 1.0]
    silt = sum((30.0 - i) * w for i, w in enumerate(th))
    share = 20.0 * 2.0 / (20.0 * 2.0 + silt)
    assert used["clay_pct"] == 60.0 and "lower bound" in used["clay_note"]
    assert used["sand_pct"] == round(40.0 * share, 1)
    assert used["sand_silt_ratio"]["sand_share"] == round(share, 4)
    for x in rec["layers"]:
        assert x["clay_pct"] == 60.0 and x["sand_pct"] == used["sand_pct"]
        assert "600 g/kg" in x["source"]["clay_pct"] and "sand:silt ratio" in x["source"]["sand_pct"]
    assert rec["layers"][0]["om_pct"] == 3.6 and "SoilGrids" in rec["layers"][3]["source"]["om_pct"]
    assert rec["estimate"] is True
    assert rec["checks"][1]["agrees"] is False          # SoilGrids clay 50-52% against "muito argiloso"


def test_soilgrids_texture_is_only_a_side_file():
    rec = TI.build("londrina", SG, lat=-1.0, lon=-1.0, min_depth_m=2.4, texture="soilgrids")
    assert rec["kind"] == "trial_soil_sensitivity" and "never the engine input" in rec["rule"]
    assert [x["clay_pct"] for x in rec["layers"]] == [50.0, 51.0, 52.0, 52.0, 53.0, 54.0, 55.0]
    assert "Doc 476" in rec["layers"][0]["source"]["om_pct"]           # organic matter rule unchanged
    with pytest.raises(ValueError):
        TI.build("londrina", SG, lat=-1.0, lon=-1.0, min_depth_m=2.4, texture="other")


def test_missing_soilgrids_value_raises():
    bad = {**SG, "30-60cm": {"clay_pct": None, "sand_pct": None, "soc_g_kg": None}}
    with pytest.raises(TI.TrialInputError, match="30-60cm"):
        TI.build("londrina", bad, lat=-1.0, lon=-1.0, min_depth_m=2.4)


def test_printed_floresta_spec_uses_the_side_run_point():
    config = yaml.safe_load((ROOT / "config" / "region_norte_pr.yaml").read_text(encoding="utf-8"))
    spec = TI.printed_power_spec(config)
    pt = config["protocol"]["side_runs"]["floresta_printed_coordinate"]
    assert (spec["lat"], spec["lon"]) == (pt["lat"], pt["lon"]) and spec["site"] == "floresta_printed"
    assert spec["parameters"] == next(c for c in config["sources"]["power"]["calls"] if c["id"] == "met")["parameters"]

# Written with an SI tool (Claude, Anthropic) for NASA Space Apps 2026. See AI_USE.md.
"""field_inputs.py: the bridge from the M2 outputs to precompute, and the real run end to end.

Every file below is synthetic and lives in a temporary folder: polygons, series, ribbons,
green-end intervals, SoilGrids means, DEM, POWER days and noise pairs are made up for the
test (the values of fixtures/fields_*_example.json and decision_example_daily.csv). They are
shaped like the M2 outputs and labelled "real" only to run the real path; nothing is read
from data/interim/ or the HLS cache and nothing is written to site/data/ or data/build/.
"""

import copy
import json
import shutil
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from pipeline.adapters import base
from pipeline.build import assemble
from pipeline.build import manifest as mf
from pipeline.decision import field_inputs as fi
from pipeline.decision import precompute as pc
from pipeline.fields import events, ndvi_series, relief, ribbon, soil_profile
from pipeline.ledger import build as lb

FIX = Path(__file__).parent / "fixtures"
CONFIG = base.load_config()
RELIEF_FIX = json.loads((FIX / "fields_relief_example.json").read_text(encoding="utf-8"))
SOIL_FIX = json.loads((FIX / "fields_soil_example.json").read_text(encoding="utf-8"))
SEASONS = [f"{y}/{(y + 1) % 100:02d}" for y in range(2018, 2026)]
NEUTRAL = "synthetic test value"


def ring(dx=0.0):
    r = RELIEF_FIX["field"]["coordinates"][0]
    return [[x + dx, y] for x, y in r]


def soil_means():
    out = {}
    for d in soil_profile.DEPTHS:
        for p in soil_profile.MEAN_PROPERTIES:
            out[f"{p}_{d}_mean"] = {"raw": float(SOIL_FIX["mapped"][p]), "data_share": 1.0, "n_px": 4}
        for q in soil_profile.QUANTILES:
            out[f"clay_{d}_{q}"] = {"raw": float(SOIL_FIX["mapped"][f"clay_{q}"]), "data_share": 1.0, "n_px": 4}
    return out


def dem():
    d = RELIEF_FIX["dem"]
    _, dy = relief.spacing_m(d["lat0"], d["step_deg"])
    rows = np.arange(d["rows"])[::-1, None] + np.zeros((1, d["cols"]))
    return {"data": (d["base_m"] + 0.05 * dy * rows).astype(np.float32), "step_deg": d["step_deg"],
            "lon0": d["lon0"], "lat0": d["lat0"], "crs": "EPSG:4326", "tile": "synthetic"}


def green_end_rows(shift_days):
    out = []
    for s in SEASONS:
        y = int(s[:4]) + 1
        low = pd.Timestamp(f"{y}-02-10") + pd.Timedelta(days=shift_days)
        out.append({"season": s, "low": low.date().isoformat(), "high": (low + pd.Timedelta(days=12)).date().isoformat(),
                    "gap_days": 12, "status": "ok", "ndvi_low": 0.8, "ndvi_high": 0.3, "peak_ndvi": 0.85,
                    "drop": 0.55, "green_cycles": 1, "sensor_low": "S30", "sensor_high": "L30", "reason": ""})
    out[6] = {**out[6], "low": None, "high": None, "gap_days": None, "status": "to_confirm", "reason": "gap"}
    return out


def write_m2(interim: Path, fid: str, shift_days: int) -> None:
    ndvi_series.write_csv(interim / f"field_{fid}_ndvi.csv", [
        {"date": "2026-01-15", "sensor": "S30", "granule_ur": "G1", "ndvi": 0.812345, "nbr2": 0.1, "n_clean": 90,
         "n_possible": 100, "clean_frac": 0.9, "n_duplicates": 0, "note": ""},
        {"date": "2026-01-20", "sensor": "L30", "granule_ur": "G2", "ndvi": None, "nbr2": None, "n_clean": 0,
         "n_possible": 100, "clean_frac": 0.0, "n_duplicates": 0, "note": "vi_skipped"},
    ])
    cells = ["not_green"] * 6 + ["green"] * 20 + ["not_green"] * 6 + ["no_data"] * 4
    ribbon.write_csv(interim / f"field_{fid}_ribbon.csv", [
        {"season": s, "cells": cells, "n_obs": 20, "longest_gap_days": 16, "green_cycles": 1} for s in SEASONS])
    events.write_csv(interim / f"field_{fid}_green_end.csv", green_end_rows(shift_days))
    base.write_json(interim / f"field_{fid}_soil.json", soil_profile.build(fid, soil_means(), config=CONFIG))
    base.write_json(interim / f"field_{fid}_relief.json",
                    relief.build(fid, dem(), RELIEF_FIX["field"], config=CONFIG))


def power_json(path: Path) -> Path:
    daily = pd.read_csv(FIX / "decision_example_daily.csv", comment="#", index_col="date")
    doc = {"header": {"fill_value": -999.0},
           "properties": {"parameter": {c: {d.replace("-", ""): float(v) for d, v in daily[c].items()}
                                        for c in daily.columns}}}
    path.write_text(json.dumps(doc), encoding="utf-8")
    return path


def neutral_base() -> dict:
    """The decision base of a real run: the fixture's [DECIDE] values, scrubbed of every example label."""
    i = json.loads((FIX / "decision_precompute_inputs.json").read_text(encoding="utf-8"))
    text = json.dumps({k: i[k] for k in fi.BASE_KEYS}, ensure_ascii=False)
    text = (text.replace("example_trials", "synthetic_trials").replace("example_ref", "synthetic_ref").replace("example_calendar", "synthetic_calendar")
            .replace("Example, fictitious", NEUTRAL).replace("example, fictitious", NEUTRAL))
    b = json.loads(text)
    b.update(municipio="Londrina", fictional_persona_note="Fictional farm persona. Synthetic polygon.",
             erosivity={"monthly_r": i["erosivity"]["monthly_r"], "data_version": NEUTRAL})
    return b


@pytest.fixture
def m2(tmp_path):
    interim = tmp_path / "interim"
    interim.mkdir()
    feats = [{"type": "Feature", "properties": {"field_id": fid, "criterion": f"Field {fid.upper()}: synthetic"},
              "geometry": {"type": "Polygon", "coordinates": [ring(dx)]}} for fid, dx in (("a", 0.0), ("b", 0.02))]
    gj = tmp_path / "fields.geojson"
    gj.write_text(json.dumps({"type": "FeatureCollection", "features": feats}), encoding="utf-8")
    window = CONFIG["sources"]["hls"]["window"]["id"]
    geo = {"window_id": window, "fields": [
        {"field_id": fid, "criterion": None, "ok": True, "area_ha": area, "n_possible": 120,
         "polygon_px": [[10, 10], [60, 12], [55, 50], [12, 48]]} for fid, area in (("a", 72.1), ("b", 70.4))]}
    base.write_json(interim / "fields_geometry.json", geo)
    write_m2(interim, "a", 0)
    write_m2(interim, "b", 9)
    return {"geojson": gj, "interim": interim, "build": tmp_path / "build", "power": power_json(tmp_path / "p.json"),
            "tmp": tmp_path}


def assembled(m2, **kw):
    return fi.assemble(m2["geojson"], kw.pop("base", neutral_base()), config=CONFIG, interim_dir=m2["interim"],
                       build_dir=m2["build"], power_path=m2["power"], **kw)


# The m2 blocks --------------------------------------------------------------------------------

def test_m2_blocks_have_the_shape_precompute_checks(m2):
    inputs = assembled(m2)
    assert inputs["label"] == pc.REAL and inputs["power_site"] == "londrina"
    for f in inputs["fields"]:
        pc.check_m2(f["m2"], f["field_id"])
        b = f["m2"]
        fid = f["field_id"]
        assert b["area_ha"] == {"v": 72.1 if fid == "a" else 70.4, "l": f"field_{fid}.area_ha"}
        assert set(b["scale_note"]) == set(pc.SCALE_KEYS)
        assert all(pc.is_num(v) and v["l"] == f"field_{fid}.scale_note.{k}" for k, v in b["scale_note"].items())
        assert 55 < b["scale_note"]["power_cell_km"]["v"] < 65            # 0.5 x 0.625 degree near 23 S
        assert b["scale_note"]["soil_m"]["v"] == 250 and 25 < b["scale_note"]["dem_m"]["v"] < 35
        assert b["soil"]["ledger"] == f"field_{fid}.soil" and b["soil"]["estimated_class"] == "very clayey"
        assert b["soil"]["zarc_ad_class"] is None and b["soil"]["zarc_ad_status"] == "unknown"
        assert b["relief"]["ledger"] == f"field_{fid}.relief"
        assert [p["date"] for p in b["ndvi_series"]["points"]] == ["2026-01-15"]      # no clean pixel, no point
        assert b["ndvi_series"]["points"][0]["ndvi"] == 0.8123
        assert [g["season"] for g in b["green_end"]] == SEASONS
        assert b["green_end"][6]["gap_days"] == {"v": None, "l": f"field_{fid}.green_end.2024_25"}
        assert {x["path"].rsplit("/", 1)[-1] for x in b["provenance"]["files"]} == {
            "fields_geometry.json", f"field_{fid}_ndvi.csv", f"field_{fid}_ribbon.csv",
            f"field_{fid}_green_end.csv", f"field_{fid}_soil.json", f"field_{fid}_relief.json"}
    first = (m2["build"] / inputs["daily_weather_csv"]).read_text(encoding="utf-8").splitlines()[0]
    assert first.startswith("# NASA POWER daily") and "fictitious" not in first


def test_missing_or_stale_m2_outputs_stop_the_bridge(m2):
    (m2["interim"] / "field_b_ribbon.csv").unlink()
    with pytest.raises(fi.FieldInputsError, match="M2 outputs missing"):
        assembled(m2)
    write_m2(m2["interim"], "b", 9)
    old = json.loads((m2["interim"] / "field_a_soil.json").read_text(encoding="utf-8"))
    base.write_json(m2["interim"] / "field_a_soil.json", {**old, "ledger": "a.soil", "source_id": "soilgrids"})
    with pytest.raises(fi.FieldInputsError, match="older schema"):
        assembled(m2)


def test_the_base_must_be_complete_and_not_an_example(m2):
    b = neutral_base()
    with pytest.raises(fi.FieldInputsError, match="not real"):
        assembled(m2, base={**b, "label": pc.EXAMPLE})
    with pytest.raises(fi.FieldInputsError, match="lacks"):
        assembled(m2, base={k: v for k, v in b.items() if k != "trials"})
    with pytest.raises(fi.FieldInputsError, match="municipio"):
        assembled(m2, base={k: v for k, v in b.items() if k != "municipio"})


def test_power_cell_decides_the_weather():
    assert fi.power_cell(-23.200278, -51.177778) == fi.power_cell(-23.08, -51.27)     # Londrina, field window
    assert fi.power_site_for([(-51.27, -23.08)], CONFIG) == "londrina"
    with pytest.raises(fi.FieldInputsError, match="not in the POWER cell"):
        fi.power_site_for([(-51.27, -23.08)], CONFIG, "floresta")
    with pytest.raises(fi.FieldInputsError, match="2 POWER cells"):
        fi.power_site_for([(-51.27, -23.08), (-52.07, -23.58)], CONFIG)
    with pytest.raises(fi.FieldInputsError, match="no trial site"):
        fi.power_site_for([(-40.0, -10.0)], CONFIG)


def test_noise_floor_needs_the_file_and_the_rule():
    doc = {"schema_version": "1", "unit": "days", "halves_days": [1, 2, 3, 4], "l30_vs_s30_days": [2, 6],
           "n_halves": 4, "n_l30_vs_s30": 2}
    assert fi.noise_floor(None, CONFIG["protocol"])["days"] is None
    # The protocol now fixes the N1 rule (tests.N1: percentile 90, nearest rank, SA-D68).
    from_protocol = fi.noise_floor(doc, CONFIG["protocol"])
    assert from_protocol["days"] == 6.0 and "percentile 90 (nearest_rank)" in from_protocol["data_version"]
    # Without the rule in the protocol, the floor stays null and the note names the percentile.
    bare = copy.deepcopy(CONFIG["protocol"])
    for k in ("percentile", "percentile_method"):
        bare["tests"]["N1"].pop(k)
    no_rule = fi.noise_floor(doc, bare)
    assert no_rule["days"] is None and "percentile" in no_rule["note"]
    done = fi.noise_floor(doc, bare, percentile=90, method="nearest_rank")
    assert done["days"] == 6.0 and "4 pairs of field halves" in done["data_version"]
    with pytest.raises(fi.FieldInputsError):
        fi.noise_floor({**doc, "n_halves": 3}, CONFIG["protocol"])


def test_erosivity_and_its_scale_from_gloreda(m2):
    window = CONFIG["sources"]["hls"]["window"]["id"]
    base.write_json(m2["interim"] / f"gloreda_window_{window}.json",
                    {"window_mean_monthly_r": [100.0] * 12})
    base.write_json(m2["interim"] / f"gloreda_plan_{window}.json",
                    {"months": [{"tiff": {"transform": [1 / 120, 0, -180, 0, -1 / 120, 90]}}]})
    b = {k: v for k, v in neutral_base().items() if k != "erosivity"}
    inputs = assembled(m2, base=b)
    assert inputs["erosivity"]["monthly_r"] == [100.0] * 12
    assert 0.8 < inputs["fields"][0]["m2"]["scale_note"]["erosivity_km"]["v"] < 1.0
    assert any(f["path"].endswith(f"gloreda_window_{window}.json") for f in inputs["window_files"])


# The real run, end to end ------------------------------------------------------------------------

def real_inputs_file(m2, **kw) -> Path:
    inputs = assembled(m2, **kw)
    p = m2["build"] / fi.INPUTS_NAME
    p.write_text(json.dumps(inputs, ensure_ascii=False), encoding="utf-8")
    return p


def zarc_rows():
    i = json.loads((FIX / "decision_precompute_inputs.json").read_text(encoding="utf-8"))
    return pc.zarc_rows_frame(i["zarc_rows"])


def test_real_run_from_m2_to_an_assembled_site(m2):
    nf = {"schema_version": "1", "unit": "days", "halves_days": [1, 2, 3], "l30_vs_s30_days": [2, 4],
          "n_halves": 3, "n_l30_vs_s30": 2}
    base.write_json(m2["interim"] / "noise_floor.json", nf)
    path = real_inputs_file(m2, n1_percentile=90, n1_method="nearest_rank")
    inputs, _, daily = pc.load_inputs(path)
    res = pc.build(inputs, config=CONFIG, zarc_rows=zarc_rows(), daily=daily, mode=pc.REAL)
    assert res["label"] == pc.REAL and res["states_input"]["label"] == pc.REAL
    assert all(f["origin"] == pc.REAL_ORIGIN for f in res["states_input"]["fields"])
    region = res["docs"]["region.json"]
    assert region["noise_floor"] == {"v": 4.0, "l": "region.noise_floor"}
    assert region["choice_seasons"] == CONFIG["protocol"]["field"]["choice_seasons"]
    fa = res["docs"]["field_a.json"]
    assert "field_order" not in fa and "provenance" not in json.dumps(fa)
    assert {v["state"] for v in fa["risk"]["zarc_class"].values()} == {"soil_class_unknown"}   # loud, AD not derived
    text = json.dumps(res["docs"]) + json.dumps(res["ledger"])
    records = res["ledger"]["records"]
    assert pc.EXAMPLE not in json.dumps({k: v for k, v in records.items() if ".price_scenarios." not in k})
    assert all(r["data_version"] == pc.EXAMPLE for k, r in records.items() if ".price_scenarios." in k)  # 14.3
    assert "example_" not in text.replace("example_low", "").replace("example_mid", "").replace("example_high", "")

    site, build = m2["tmp"] / "site_data", m2["tmp"] / "build_out"
    pc.write(res, site, build)
    assert not list(site.glob("*_m5.json")) and (build / "ledger_m5.json").exists()
    assert lb.load_json(build / "ledger_m5.json")["label"] == pc.REAL
    raw = m2["tmp"] / "data" / "raw"
    raw.mkdir(parents=True)
    extra = {sid: {"title": f"Synthetic {sid}", "kind": "document"} for sid in ("synthetic_trials", "synthetic_calendar")}
    man = mf.build_manifest(raw, config=CONFIG, extra_sources=extra)
    mf.write(man, site / "manifest.json")
    led = lb.build_ledger(lb.site_docs(site), lb.load_json(build / "ledger_m5.json"), man)
    assert led["label"] == pc.REAL and lb.unresolved_transforms(led["records"]) == []
    lb.write(led, site / "ledger.json")
    assert assemble.check_site(site) == []


def test_a_changed_m2_file_after_assembly_is_refused(m2):
    path = real_inputs_file(m2)
    inputs, _, daily = pc.load_inputs(path)
    with open(m2["interim"] / "field_a_green_end.csv", "a", encoding="utf-8") as f:
        f.write("\n")
    with pytest.raises(pc.PrecomputeError, match="changed after it was assembled"):
        pc.build(inputs, config=CONFIG, zarc_rows=zarc_rows(), daily=daily, mode=pc.REAL)


def test_real_inputs_never_run_as_the_example_and_back(m2):
    inputs, _, daily = pc.load_inputs(real_inputs_file(m2))
    with pytest.raises(pc.PrecomputeError, match="cannot be labelled"):
        pc.build({**inputs, "label": pc.EXAMPLE}, config=CONFIG, zarc_rows=zarc_rows(), daily=daily)
    tainted = copy.deepcopy(inputs)
    tainted["trials"]["rights_note"] = "Example, fictitious. Steps of 1 bag/ha."
    with pytest.raises(pc.PrecomputeError, match="carries the label"):
        pc.build(tainted, config=CONFIG, zarc_rows=zarc_rows(), daily=daily, mode=pc.REAL)
    res = pc.build(inputs, config=CONFIG, zarc_rows=zarc_rows(), daily=daily, mode=pc.REAL,
                   states_runner=lambda p: (_ for _ in ()).throw(FileNotFoundError("no node (test)")))
    with pytest.raises(pc.PrecomputeError, match="tests/"):
        pc.write(res, pc.TESTS_DIR / "fixtures" / "never")


def test_cli_writes_the_inputs_next_to_the_daily_csv(m2, monkeypatch):
    monkeypatch.setattr(base, "INTERIM_DIR", m2["interim"])
    monkeypatch.setattr(fi, "power_file", lambda site, config: m2["power"])
    (m2["tmp"] / "base.json").write_text(json.dumps(neutral_base()), encoding="utf-8")
    assert fi.main([str(m2["geojson"]), "--base", str(m2["tmp"] / "base.json"),
                    "--build-out", str(m2["build"])]) == 0
    doc = json.loads((m2["build"] / fi.INPUTS_NAME).read_text(encoding="utf-8"))
    assert (m2["build"] / doc["daily_weather_csv"]).exists() and doc["noise_floor"]["days"] is None
    shutil.rmtree(m2["interim"])
    assert fi.main([str(m2["geojson"]), "--base", str(m2["tmp"] / "base.json"),
                    "--build-out", str(m2["build"])]) == 1

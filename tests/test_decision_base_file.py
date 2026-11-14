# Written with an SI tool (Claude, Anthropic) for NASA Space Apps 2026. See AI_USE.md.
"""The decision base producer (pipeline.decision.base_file): fields, municipalities, trials.

Everything here is synthetic, "example, fictitious": the mesh, the polygons, the Tábua rows
and the transcription. data/manual/ is never opened: the manual path is refused before the
tag, and after a (simulated) tag it reads a synthetic CSV in a temporary folder.
"""

import copy
import json
import shutil
from pathlib import Path

import pandas as pd
import pytest

from pipeline.adapters import base
from pipeline.decision import base_file as bf
from pipeline.decision import precompute as pc

FIX = Path(__file__).parent / "fixtures"
CONFIG = base.load_config()


def square(x0, y0, x1, y1):
    return [[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]]


MESH = __import__("pipeline.adapters.ibge_malhas", fromlist=["x"]).parse_mesh({
    "type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {"codarea": "9999901"},
         "geometry": {"type": "Polygon", "coordinates": [square(-2, -1, -1, 0)]}},
        {"type": "Feature", "properties": {"codarea": "9999902"},
         "geometry": {"type": "Polygon", "coordinates": [square(-1, -1, 0, 0)]}}]})
ZARC = pd.DataFrame([{"geocodigo": 9999901, "municipio": "Vila Ficticia"}])


def fields_file(tmp_path, rings):
    feats = [{"type": "Feature", "properties": {"field_id": fid, "criterion": "synthetic"},
              "geometry": {"type": "Polygon", "coordinates": [ring]}} for fid, ring in rings.items()]
    p = tmp_path / "fields.geojson"
    p.write_text(json.dumps({"type": "FeatureCollection", "features": feats}), encoding="utf-8")
    return p


@pytest.fixture
def fields(tmp_path):
    return fields_file(tmp_path, {"a": square(-1.6, -0.6, -1.5, -0.5), "b": square(-0.6, -0.6, -0.5, -0.5)})


def tagged():
    c = copy.deepcopy(CONFIG)
    c["protocol"]["status"] = "tagged"
    return c


def test_example_base_is_labelled_and_complete(fields):
    doc = bf.build(CONFIG, trials="example", fields_geojson=fields, mesh=MESH, zarc_rows=ZARC)
    assert doc["label"] == pc.EXAMPLE and doc["trials_source"] == "example"
    assert (doc["season"], doc["current_safra"]) == ("2026/27", "2026/2027")
    for k in ("season", "current_safra", "cycles", "occupancy", "risk", "example_context", "example_constraints",
              "region", "trials"):
        assert k in doc, k
    # Municipality from the mesh: the Tábua name when it has rows, the IBGE code otherwise.
    assert doc["fields"]["a"]["municipio"] == "Vila Ficticia" and doc["fields"]["a"]["municipio_ibge"] == "9999901"
    assert doc["fields"]["b"]["municipio"] == "IBGE 9999902" and doc["fields"]["b"]["municipio_in_zarc"] is False
    t = doc["trials"]
    assert t["options"]["S3"]["n"] == 10 and t["options"]["S3"]["n_sites"] == 3
    assert all(pc.EXAMPLE in t["options"][o][k].casefold() for o in ("S2", "S3")
               for k in ("condition", "transfer_warning", "bad_year_text"))
    assert t["options"]["S2"]["source_ids"] == ["example_trials"]


def test_s2_has_seven_site_seasons_and_the_method_of_each(fields):
    s2 = bf.build(CONFIG, trials="example", fields_geojson=fields, mesh=MESH, zarc_rows=ZARC)["trials"]["options"]["S2"]
    assert s2["n"] == 7 and s2["n_sites"] == 2
    assert {(m["method"], m["n"], tuple(m["sites"])) for m in s2["by_method"]} == {
        ("in_row_or_alternate_rows_2kg", 4, ("Floresta",)), ("broadcast_8_12kg", 3, ("Cafelandia",))}  # config name: no Tábua row here
    assert all(s["method"] == bf.S2_METHODS[s["site"]] for s in s2["site_seasons"])
    # Steps come from steps_from_gains, the aggregate and each method.
    gains = json.loads(bf.EXAMPLE_TRIALS.read_text(encoding="utf-8"))["S2"]
    assert s2["steps"] == pc.steps_from_gains([g["gain_kg_ha"] for g in gains], bag_kg=60)
    flo = [g["gain_kg_ha"] for g in gains if g["site"] == "floresta"]
    assert s2["by_method"][0]["steps"] == pc.steps_from_gains(flo, bag_kg=60)


def test_a_site_season_without_a_method_drops_the_per_method_block(monkeypatch, fields):
    monkeypatch.setitem(bf.S2_METHODS, "cafelandia", None)
    s2 = bf.build(CONFIG, trials="example", fields_geojson=fields, mesh=MESH, zarc_rows=ZARC)["trials"]["options"]["S2"]
    assert "by_method" not in s2           # decision.mjs then gives S2 Not enough evidence (method_missing)


def test_a_field_across_a_municipal_boundary_is_refused(tmp_path):
    f = fields_file(tmp_path, {"a": square(-1.05, -0.6, -0.95, -0.5)})
    with pytest.raises(bf.BaseFileError, match="crosses the boundary"):
        bf.build(CONFIG, trials="example", fields_geojson=f, mesh=MESH, zarc_rows=ZARC)


def test_manual_trials_are_refused_before_the_tag_without_opening_anything(monkeypatch, fields, tmp_path):
    from pipeline.bench import trials as tr

    def boom(*a, **k):
        raise AssertionError("the transcription was opened")
    monkeypatch.setattr(tr, "read_transcription", boom)
    monkeypatch.setattr(bf, "settings_for", boom)
    # The repository protocol is tagged (SA-D71): the guard is tested on a draft copy.
    draft = copy.deepcopy(CONFIG)
    draft["protocol"]["status"] = "draft"
    monkeypatch.setattr(bf.base, "load_config", lambda *a, **k: draft)
    with pytest.raises(bf.BaseFileError, match="only after the tag"):
        bf.build(draft, trials="manual", fields_geojson=fields, mesh=MESH, zarc_rows=ZARC,
                 manual_path=tmp_path / "never" / "trials_a.csv")
    # The CLI refuses too, before the settings, the fields or the Tábua are read.
    assert bf.main(["--trials", "manual", "--fields", str(tmp_path / "absent.geojson"),
                    "--settings", str(tmp_path / "absent.json"), "--out", str(tmp_path / "out.json")]) == 1
    assert not (tmp_path / "out.json").exists()


def test_manual_needs_its_settings(fields, tmp_path):
    with pytest.raises(bf.BaseFileError, match="--settings lacks"):
        bf.build(tagged(), trials="manual", fields_geojson=fields, mesh=MESH, zarc_rows=ZARC,
                 manual_path=tmp_path / "unused.csv")


SYN_CSV_HEAD = "doc,page,table_or_figure,site,season,predecessor,soy_yield,unit,soy_yield_kg_ha,cv_or_sed,note\n"


def syn_rows():
    """Fictitious transcription rows: Floresta 2 seasons, Cafelandia 1, Londrina 1 (no grass)."""
    out = []
    for site, season, w, c, g in (("Floresta-PR", "2012/13", 3300, 3000, 3200), ("Floresta-PR", "2013/14", 3000, 3010, 3070),
                                  ("Cafelandia-PR", "2020/21", 3600, 3500, 3700), ("Londrina-PR", "2021/22", 3900, 3700, None)):
        for pred, v in (("trigo", w), ("milho_2a_safra", c), ("milho_2a_safra+braquiaria", g)):
            if v is not None:
                out.append(f"SYN-1,1,Tabela 1,{site},{season},{pred},{v},kg/ha,{v},,synthetic")
    return SYN_CSV_HEAD + "\n".join(out) + "\n"


def test_manual_after_the_tag_reads_a_transcription(fields, tmp_path):
    csv = tmp_path / "trials_a.csv"
    csv.write_text(syn_rows(), encoding="utf-8")
    settings = {k: v for k, v in json.loads(bf.EXAMPLE_SETTINGS.read_text(encoding="utf-8")).items()
                if k in bf.SETTINGS_KEYS}
    ex = json.loads(bf.EXAMPLE_SETTINGS.read_text(encoding="utf-8"))["region"]
    settings["region"] = {k: ex[k] for k in bf.REGION_SETTINGS}
    doc = bf.build(tagged(), trials="manual", fields_geojson=fields, settings=settings, mesh=MESH, zarc_rows=ZARC,
                   manual_path=csv)
    assert doc["label"] == pc.REAL and doc["trials"]["options"]["S2"]["source_ids"] == ["embrapa_docs"]
    s2, s3 = doc["trials"]["options"]["S2"], doc["trials"]["options"]["S3"]
    assert (s2["n"], s3["n"]) == (3, 4)
    # S2 gains 200, 60, 200 kg/ha; S3 gains 300, -10, 100, 200 kg/ha (bag of 60 kg).
    assert s2["steps"] == pc.steps_from_gains([200, 60, 200])
    assert s3["steps"] == pc.steps_from_gains([300, -10, 100, 200])
    assert [m["n"] for m in s2["by_method"]] == [2, 1]
    assert "sha256" in s2["data_version"] and "tagged" in s2["data_version"]
    assert pc.EXAMPLE not in json.dumps(doc["trials"], ensure_ascii=False).casefold()


def test_gains_never_leave_as_values(fields):
    doc = bf.build(CONFIG, trials="example", fields_geojson=fields, mesh=MESH, zarc_rows=ZARC)
    assert "gain_kg_ha" not in json.dumps(doc)


def test_the_base_trials_run_through_precompute_and_the_rule(fields):
    """The example base trials feed precompute: trials_steps.json carries by_method, and the
    decision rule reads it (S2 gets a verdict from the counts, never method_missing)."""
    trials = bf.build(CONFIG, trials="example", fields_geojson=fields, mesh=MESH, zarc_rows=ZARC)["trials"]
    inputs = json.loads((FIX / "decision_precompute_inputs.json").read_text(encoding="utf-8"))
    inputs["trials"] = {**inputs["trials"], "options": {
        o: {**trials["options"][o], "summary": inputs["trials"]["options"][o].get("summary")} for o in ("S2", "S3")}}
    rows = pc.zarc_rows_frame(inputs["zarc_rows"])
    daily = pd.read_csv(FIX / inputs["daily_weather_csv"], comment="#", index_col="date", parse_dates=True)
    if shutil.which("node") is None:
        pytest.skip("node not found")
    out = pc.build(inputs, config=CONFIG, zarc_rows=rows, daily=daily)
    s2 = out["docs"]["trials_steps.json"]["options"]["S2"]
    assert [m["method"] for m in s2["by_method"]] == ["in_row_or_alternate_rows_2kg", "broadcast_8_12kg"]
    assert not pc.loose_numbers(out["docs"]["trials_steps.json"])
    assert out["states"] is not None, out["errors"]
    codes = {s["hls_on"]["code"] for s in out["states"]["states"] if s["option"] == "S2"}
    assert codes and "method_missing" not in codes
    # Without the per-method block the same inputs give method_missing on every count verdict.
    del inputs["trials"]["options"]["S2"]["by_method"]
    out = pc.build(inputs, config=CONFIG, zarc_rows=rows, daily=daily)
    s2_states = [s["hls_on"] for s in out["states"]["states"] if s["option"] == "S2"]
    assert all(s["code"] not in ("counts_try", "counts_keep", "counts_tied") for s in s2_states)
    assert any(s["code"] == "method_missing" and s["state"] == "not_enough" for s in s2_states)


def test_precompute_refuses_counts_per_method_that_do_not_add_up(fields):
    trials = bf.build(CONFIG, trials="example", fields_geojson=fields, mesh=MESH, zarc_rows=ZARC)["trials"]
    bad = copy.deepcopy(trials["options"]["S2"]["by_method"])
    bad[0]["n"] = 3
    with pytest.raises(pc.PrecomputeError, match="add up"):
        pc.by_method_block(bad, 7, "trials.options.S2.by_method")

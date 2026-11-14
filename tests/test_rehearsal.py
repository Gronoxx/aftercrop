# Written with an SI tool (Claude, Anthropic) for NASA Space Apps 2026. See AI_USE.md.
"""Rehearsal mode (SA-D63, pipeline.common.rehearsal) through base_file, field_inputs, precompute,
manifest, ledger and assemble, on synthetic data only.

Polygons, mesh, M2 files, weather and trial gains are synthetic (the fixtures of
tests/test_decision_field_inputs.py, shifted into the development strip). Nothing is written to
site/data/, data/build/ or data/rehearsal/: every output goes to a temporary folder.
"""

import copy
import json
import subprocess
from pathlib import Path

import pandas as pd
import pytest

from pipeline.adapters import base, ibge_malhas
from pipeline.build import assemble
from pipeline.build import manifest as mf
from pipeline.common import rehearsal as rh
from pipeline.decision import base_file as bf
from pipeline.decision import field_inputs as fi
from pipeline.decision import precompute as pc
from pipeline.decision import settings as sm
from pipeline.fields import geometry
from pipeline.ledger import build as lb

from test_decision_field_inputs import neutral_base, power_json, ring, write_m2, zarc_rows

CONFIG = base.load_config()
STRIP = CONFIG["protocol"]["field"]["development_strip"]["lon_min"]       # -51.245
EAST = 0.1          # the fixture ring (lon -51.297) moved to lon -51.197, inside the strip
ROOT = Path(__file__).resolve().parents[1]
MAKE = __import__("shutil").which("make")


def square(x0, y0, x1, y1):
    return [[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]]


def fields_file(tmp_path, rings, name="fields.geojson"):
    feats = [{"type": "Feature", "properties": {"field_id": fid, "criterion": f"Field {fid.upper()}: synthetic"},
              "geometry": {"type": "Polygon", "coordinates": [r]}} for fid, r in rings.items()]
    p = tmp_path / name
    p.write_text(json.dumps({"type": "FeatureCollection", "features": feats}), encoding="utf-8")
    return p


def walk_strings(node):
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for v in node.values():
            yield from walk_strings(v)
    elif isinstance(node, list):
        for v in node:
            yield from walk_strings(v)


# The stamp and the folder rule --------------------------------------------------------------

def test_stamp_sits_after_the_header_keys():
    doc = rh.stamped({"records": {}, "label": "real", "schema_version": "1", "_notice": "n"})
    assert list(doc) == ["_notice", "schema_version", "label", "run_mode", "run_note", "records"]
    assert doc["run_note"] == "Rehearsal: real NASA data on a development strip, fictitious trials"
    assert rh.is_rehearsal(doc) and not rh.is_rehearsal({"run_mode": "product"})


def test_site_data_is_refused_and_any_other_folder_is_not(tmp_path):
    for bad in (rh.SITE_DATA, rh.SITE_DATA / "sub", rh.SITE_DATA / "img"):
        with pytest.raises(rh.RehearsalError, match="inside site/data"):
            rh.check_out(bad)
    rh.check_out(tmp_path / "site_data")
    rh.check_out(ROOT / "data" / "rehearsal" / "site_data")
    assert rh.main(["check-out", str(rh.SITE_DATA)]) == 2 and rh.main(["check-out", str(tmp_path)]) == 0


def test_mode_check_works_both_ways():
    rh.check_mode(rh.stamped({}), True, "x")
    rh.check_mode({}, False, "x")
    with pytest.raises(rh.RehearsalError, match="not stamped"):
        rh.check_mode({}, True, "x")
    with pytest.raises(rh.RehearsalError, match="product mode refuses it"):
        rh.check_mode(rh.stamped({}), False, "x")


# base_file ------------------------------------------------------------------------------------

MESH = ibge_malhas.parse_mesh({"type": "FeatureCollection", "features": [
    {"type": "Feature", "properties": {"codarea": "9999901"},
     "geometry": {"type": "Polygon", "coordinates": [square(-51.40, -23.30, -51.00, -22.90)]}}]})
ZARC = pd.DataFrame([{"geocodigo": 9999901, "municipio": "Vila Ficticia"}])
STRIP_RING = square(-51.20, -23.06, -51.19, -23.05)
PRODUCT_RING = square(-51.30, -23.06, -51.29, -23.05)


def rehearsal_base(tmp_path, rings=None, **kw):
    gj = fields_file(tmp_path, rings or {"a": STRIP_RING})
    return bf.build(CONFIG, trials="example", fields_geojson=gj, settings=sm.load(), mesh=MESH, zarc_rows=ZARC,
                    rehearsal=True, **kw)


def test_rehearsal_base_is_real_stamped_and_fictitious_trials_say_so(tmp_path):
    doc = rehearsal_base(tmp_path)
    assert doc["label"] == pc.REAL and doc["run_mode"] == rh.REHEARSAL and doc["run_note"] == rh.NOTE
    for opt in ("S2", "S3"):
        e = doc["trials"]["options"][opt]
        assert e["source_ids"] == [rh.TRIALS_SOURCE_ID]
        assert all(e[k].startswith(rh.NOTE) for k in ("condition", "transfer_warning", "bad_year_text"))
        assert rh.NOTE in e["data_version"] and "sha256" in e["data_version"]
    assert doc["trials"]["rights_note"].startswith(rh.NOTE) and "Fictitious gains" in doc["trials"]["rights_note"]
    assert doc["risk"] == {"dry_autumn": None, "cold_in_window": None}           # removed, no sourced threshold
    # The real run of precompute refuses the example label anywhere in its inputs.
    assert not any(pc.EXAMPLE in s.casefold() for s in walk_strings(doc))


def test_rehearsal_base_refuses_product_fields_the_transcription_and_no_settings(tmp_path):
    with pytest.raises(geometry.GeometryError, match="Product polygons are refused in rehearsal mode"):
        rehearsal_base(tmp_path, {"a": STRIP_RING, "b": PRODUCT_RING})
    gj = fields_file(tmp_path, {"a": STRIP_RING})
    with pytest.raises(bf.BaseFileError, match="never reads the trial transcription"):
        bf.build(CONFIG, trials="manual", fields_geojson=gj, settings=sm.load(), mesh=MESH, zarc_rows=ZARC,
                 rehearsal=True)
    with pytest.raises(bf.BaseFileError, match="needs --settings"):
        bf.build(CONFIG, trials="example", fields_geojson=gj, mesh=MESH, zarc_rows=ZARC, rehearsal=True)


def test_product_base_keeps_the_example_label_for_fictitious_trials(tmp_path):
    gj = fields_file(tmp_path, {"a": STRIP_RING})
    doc = bf.build(CONFIG, trials="example", fields_geojson=gj, settings=sm.load(), mesh=MESH, zarc_rows=ZARC)
    assert doc["label"] == pc.EXAMPLE and "run_mode" not in doc


def test_base_cli_refuses_site_data_before_reading_anything(tmp_path):
    out = rh.SITE_DATA / "decision_base.json"
    assert bf.main(["--rehearsal", "--trials", "example", "--fields", str(tmp_path / "none.geojson"),
                    "--out", str(out)]) == 1
    assert not out.exists()


# field_inputs ---------------------------------------------------------------------------------

@pytest.fixture
def strip(tmp_path):
    """The M2 tree of tests/test_decision_field_inputs.py, with both fields in the development strip."""
    interim = tmp_path / "interim"
    interim.mkdir()
    gj = fields_file(tmp_path, {"a": ring(EAST), "b": ring(EAST + 0.02)})
    assert min(p[0] for p in ring(EAST)) > STRIP
    geo = {"window_id": CONFIG["sources"]["hls"]["window"]["id"], "mode": "rehearsal", "fields": [
        {"field_id": fid, "criterion": None, "ok": True, "area_ha": area, "n_possible": 120,
         "polygon_px": [[10, 10], [60, 12], [55, 50], [12, 48]]} for fid, area in (("a", 72.1), ("b", 70.4))]}
    base.write_json(interim / "fields_geometry.json", geo)
    write_m2(interim, "a", 0)
    write_m2(interim, "b", 9)
    return {"geojson": gj, "interim": interim, "build": tmp_path / "build", "power": power_json(tmp_path / "p.json"),
            "tmp": tmp_path}


def strip_base() -> dict:
    """neutral_base with the rehearsal trials of base_file and the sourced settings, stamped."""
    b = neutral_base()
    gains = bf.read_example_gains()
    names = {s["id"]: s["name"] for s in CONFIG["trial_sites"]}
    b["trials"] = bf.trials_block(gains, names=names, mode=bf.REHEARSAL,
                                  data_version=f"{rh.NOTE}: synthetic", bag_kg=60)
    s = sm.load()
    b["risk"], b["cycles"] = s["risk"], s["cycles"]
    return rh.stamped(b)


def assembled(strip, base_inputs, rehearsal=True):
    return fi.assemble(strip["geojson"], base_inputs, config=CONFIG, interim_dir=strip["interim"],
                       build_dir=strip["build"], power_path=strip["power"], rehearsal=rehearsal)


def test_rehearsal_inputs_are_stamped(strip):
    inputs = assembled(strip, strip_base())
    assert inputs["label"] == pc.REAL and rh.is_rehearsal(inputs) and inputs["run_note"] == rh.NOTE


def test_product_field_inputs_refuse_a_rehearsal_base_and_strip_fields(strip):
    with pytest.raises(fi.FieldInputsError, match="product mode refuses it"):
        assembled(strip, strip_base(), rehearsal=False)
    with pytest.raises(geometry.GeometryError, match="use --rehearsal"):
        assembled(strip, neutral_base(), rehearsal=False)          # fictitious-free base, strip fields


def test_rehearsal_field_inputs_refuse_an_unstamped_base_and_a_product_geometry(strip):
    with pytest.raises(fi.FieldInputsError, match="not stamped"):
        assembled(strip, neutral_base())
    geo_path = strip["interim"] / "fields_geometry.json"
    geo = json.loads(geo_path.read_text(encoding="utf-8"))
    base.write_json(geo_path, {**geo, "mode": "product"})
    with pytest.raises(fi.FieldInputsError, match="checked in mode 'product'"):
        assembled(strip, strip_base())


def test_rehearsal_field_inputs_refuse_product_fields(strip, tmp_path):
    product = {**strip, "geojson": fields_file(tmp_path, {"a": ring(0.0)}, "product.geojson")}
    with pytest.raises(geometry.GeometryError, match="Product polygons are refused"):
        assembled(product, strip_base())


# precompute, manifest, ledger, assemble ---------------------------------------------------------

def strip_inputs(strip):
    inputs = assembled(strip, strip_base())
    p = strip["build"] / fi.INPUTS_NAME
    p.write_text(json.dumps(inputs, ensure_ascii=False), encoding="utf-8")
    return pc.load_inputs(p)


def no_node(_payload):
    raise FileNotFoundError("no node (test)")


def test_precompute_stamps_every_document_and_counts_the_climate(strip):
    inputs, _, daily = strip_inputs(strip)
    res = pc.build(inputs, config=CONFIG, zarc_rows=zarc_rows(), daily=daily, mode=pc.REAL, rehearsal=True)
    assert res["label"] == pc.REAL and res["run_mode"] == rh.REHEARSAL
    assert all(rh.is_rehearsal(d) and d["run_note"] == rh.NOTE for d in res["docs"].values())
    risk = res["docs"]["field_a.json"]["risk"]
    assert "dry_autumn" not in risk and "cold_in_window" not in risk       # replaced by protocol.climate (SA-D72.5)
    assert set(risk["climate"]) == {"S1", "S2", "S3", "ledger"} and risk["climate"]["ledger"] == "field_a.climate"
    assert res["ledger"]["records"]["field_a.climate"]["evidence_class"] == "reanalysis"
    assert res["docs"]["field_a.json"]["ribbon_counts"]["no_green"]["ledger"] == "field_a.ribbon_counts.no_green"
    assert res["docs"]["trials_steps.json"]["options"]["S3"]["condition"].startswith(rh.NOTE)


def test_product_precompute_refuses_rehearsal_inputs_and_trials(strip):
    inputs, _, daily = strip_inputs(strip)
    with pytest.raises(pc.PrecomputeError, match="product mode refuses it"):
        pc.build(inputs, config=CONFIG, zarc_rows=zarc_rows(), daily=daily, mode=pc.REAL, states_runner=no_node)
    unstamped = {k: v for k, v in inputs.items() if k not in (rh.MODE_KEY, rh.NOTE_KEY)}
    with pytest.raises(pc.PrecomputeError, match="fictitious rehearsal trials in a product run"):
        pc.build(unstamped, config=CONFIG, zarc_rows=zarc_rows(), daily=daily, mode=pc.REAL, states_runner=no_node)
    with pytest.raises(pc.PrecomputeError, match="not stamped"):
        pc.build(unstamped, config=CONFIG, zarc_rows=zarc_rows(), daily=daily, mode=pc.REAL, rehearsal=True,
                 states_runner=no_node)


def test_product_precompute_still_refuses_fictitious_trials(strip):
    """The guard of before: an example-labelled trial text never passes as real."""
    inputs, _, daily = strip_inputs(strip)
    tainted = copy.deepcopy({k: v for k, v in inputs.items() if k not in (rh.MODE_KEY, rh.NOTE_KEY)})
    tainted["trials"] = copy.deepcopy(neutral_base()["trials"])
    tainted["trials"]["rights_note"] = "Example, fictitious. Fictitious gains, not trial values."
    with pytest.raises(pc.PrecomputeError, match="carries the label"):
        pc.build(tainted, config=CONFIG, zarc_rows=zarc_rows(), daily=daily, mode=pc.REAL, states_runner=no_node)


def test_rehearsal_never_writes_into_site_data(strip):
    inputs, _, daily = strip_inputs(strip)
    res = pc.build(inputs, config=CONFIG, zarc_rows=zarc_rows(), daily=daily, mode=pc.REAL, rehearsal=True,
                   states_runner=no_node)
    before = sorted(p.name for p in pc.SITE_DATA.glob("*"))
    with pytest.raises(pc.PrecomputeError, match="inside site/data"):
        pc.write(res, pc.SITE_DATA, strip["tmp"] / "build_out")
    assert sorted(p.name for p in pc.SITE_DATA.glob("*")) == before
    assert not (strip["tmp"] / "build_out").exists()                         # refused before any write


def test_rehearsal_chain_to_an_assembled_folder(strip):
    inputs, _, daily = strip_inputs(strip)
    res = pc.build(inputs, config=CONFIG, zarc_rows=zarc_rows(), daily=daily, mode=pc.REAL, rehearsal=True)
    site, build = strip["tmp"] / "rehearsal" / "site_data", strip["tmp"] / "rehearsal" / "build"
    pc.write(res, site, build)
    m5 = lb.load_json(build / "ledger_m5.json")
    assert rh.is_rehearsal(m5)
    raw = strip["tmp"] / "data" / "raw"
    raw.mkdir(parents=True)
    extra = {"synthetic_calendar": {"title": "Synthetic calendar", "kind": "document"},
             rh.TRIALS_SOURCE_ID: rh.TRIALS_SOURCE}
    man = rh.stamped(mf.build_manifest(raw, config=CONFIG,
                                       extra_sources=extra))
    mf.write(man, site / "manifest.json")
    assert next(s for s in man["sources"] if s["source_id"] == rh.TRIALS_SOURCE_ID)["fictitious"] is True
    led = lb.build_ledger(lb.site_docs(site), m5, man)
    assert rh.is_rehearsal(led) and list(led)[:4] == ["schema_version", "label", "run_mode", "run_note"]
    lb.write(led, site / "ledger.json")
    (site / "scoreboard.json").write_text(json.dumps({"schema_version": "1", "tests": []}, indent=1) + "\n",
                                          encoding="utf-8")
    assert "scoreboard.json: not stamped" in " ".join(assemble.check_site(site, rehearsal=True))
    assert assemble.add_rehearsal_stamp(site) == ["scoreboard.json"]
    assert assemble.check_site(site, rehearsal=True) == []
    product = assemble.check_site(site)                        # the same folder checked as a product
    assert product and all("a rehearsal document" in p for p in product)
    assert len(product) == len(list(site.glob("*.json")))


def test_assemble_ledger_and_manifest_clis_refuse_site_data(tmp_path):
    copy_json = (rh.SITE_DATA / "copy.json").read_bytes()
    # site/data holds the real run once it has been made: the check is that nothing there changes.
    before = {f.name: f.read_bytes() for f in rh.SITE_DATA.glob("*.json")}
    assert assemble.main(["--rehearsal", "--site-data", str(rh.SITE_DATA)]) == 1
    assert lb.main(["--rehearsal", "--site-data", str(rh.SITE_DATA), "--m5-ledger", str(tmp_path / "m5.json")]) == 1
    assert lb.main(["--rehearsal", "--site-data", str(tmp_path), "--out", str(rh.SITE_DATA / "ledger.json"),
                    "--m5-ledger", str(tmp_path / "m5.json")]) == 1
    assert mf.main(["--rehearsal", "--raw", str(tmp_path), "--out", str(rh.SITE_DATA / "manifest.json")]) == 1
    assert (rh.SITE_DATA / "copy.json").read_bytes() == copy_json
    assert {f.name: f.read_bytes() for f in rh.SITE_DATA.glob("*.json")} == before


def test_ledger_cli_modes_must_match_the_m5_ledger(tmp_path):
    site = tmp_path / "site"
    site.mkdir()
    (site / "manifest.json").write_text(json.dumps({"schema_version": "1", "sources": []}), encoding="utf-8")
    m5 = tmp_path / "ledger_m5.json"
    m5.write_text(json.dumps({"schema_version": "1", "label": pc.REAL, "records": {}}), encoding="utf-8")
    assert lb.main(["--rehearsal", "--site-data", str(site), "--m5-ledger", str(m5)]) == 1
    m5.write_text(json.dumps(rh.stamped({"schema_version": "1", "label": pc.REAL, "records": {}})), encoding="utf-8")
    assert lb.main(["--site-data", str(site), "--m5-ledger", str(m5)]) == 1
    assert not (site / "ledger.json").exists()


# Makefile -------------------------------------------------------------------------------------

def make_n(*args):
    return subprocess.run([MAKE, "-n", "-k", *args], cwd=ROOT, capture_output=True, text=True, timeout=120).stdout


CHAIN = ("pipeline.decision.base_file", "pipeline.decision.field_inputs", "pipeline.decision.precompute",
         "pipeline.build.manifest", "pipeline.ledger.build", "pipeline.build.assemble")


@pytest.mark.skipif(MAKE is None, reason="make not installed")
def test_make_rehearsal_runs_the_chain_outside_site_data():
    out = make_n("piso", "REHEARSAL=1", "FIELDS_GEOJSON=config/fields/rehearsal.geojson",
                 "SITE_OUT=data/rehearsal/site_data")
    lines = out.splitlines()
    for module in CHAIN:
        line = next(x for x in lines if f"-m {module} " in x)
        assert "--rehearsal" in line, line
        assert " site/data" not in line and " data/build" not in line, line
    assert "--trials example" in out and "--settings config/decision_settings.yaml" in out
    assert "--out data/rehearsal/site_data --build-out data/rehearsal/build" in out
    assert "--site-out data/rehearsal/site_data/scoreboard.json" in out
    assert "--results-out data/rehearsal/bench/results.json" in out
    guard = "-m pipeline.common.rehearsal check-out data/rehearsal/site_data"
    assert out.index(guard) < out.index("mkdir -p data/rehearsal/site_data/img")


@pytest.mark.skipif(MAKE is None, reason="make not installed")
def test_make_product_keeps_its_paths_and_the_manual_trials():
    out = make_n("piso")
    assert "--rehearsal" not in out.replace("pipeline.common.rehearsal", "")
    assert "--trials manual" in out and "--out site/data --build-out data/build" in out
    assert "--site-data site/data" in out and "mkdir -p site/img" in out
    # The default rehearsal folder when SITE_OUT is not given.
    assert "--out data/rehearsal/site_data " in make_n("decide", "REHEARSAL=1")

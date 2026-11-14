# Written with an SI tool (Claude, Anthropic) for NASA Space Apps 2026. See AI_USE.md.
"""Evidence Ledger (sheet M6, criteria M6.1 to M6.5): every screen number has a record.

Built only from synthetic inputs labelled "example, fictitious": the M5 example outputs in
tests/fixtures/decision_example/ (read only), the scoreboard example of M4
(tests/fixtures/scoreboard_example.json) and a manifest of an empty raw folder plus two
fictitious sources. Every output goes to a temporary folder, never to site/data/.

M6.2 and M6.5 run on tests/fixtures/build_dom_example.html, a hand-written stand-in for a
rendered page, and also on build/dom_*.html with site/data/ledger.json once the site exists.
"""

import copy
import json
import shutil
from pathlib import Path

import pytest

from pipeline.build import assemble
from pipeline.build import manifest as mf
from pipeline.decision import precompute as pc
from pipeline.ledger import build as lb
from pipeline.ledger import registry as reg

FIX = Path(__file__).parent / "fixtures"
EXAMPLE_DIR = FIX / "decision_example"
SCOREBOARD = FIX / "scoreboard_example.json"
DOM_EXAMPLE = FIX / "build_dom_example.html"
PLANTED = FIX / "ledger_planted_number.json"
ROOT = lb.ROOT
EXTRA_SOURCES = {
    "example_trials": {"title": "Example trials, fictitious", "fictitious": True, "kind": "trials"},
    "example_calendar": {"title": "Example calendar, fictitious", "fictitious": True, "kind": "document"},
}


def example_manifest(tmp_path: Path) -> dict:
    raw = tmp_path / "data" / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    return mf.build_manifest(raw,
                             extra_sources=EXTRA_SOURCES)


def copy_example_docs(site: Path) -> None:
    site.mkdir(parents=True, exist_ok=True)
    for p in EXAMPLE_DIR.glob("*.json"):
        if not p.name.endswith("_m5.json"):
            shutil.copy(p, site / p.name)
    board = json.loads(SCOREBOARD.read_text(encoding="utf-8"))["scoreboard"]
    (site / "scoreboard.json").write_text(json.dumps(board, ensure_ascii=False), encoding="utf-8")


def m5_ledger() -> dict:
    return lb.load_json(EXAMPLE_DIR / "ledger_m5.json")


@pytest.fixture
def site(tmp_path):
    """A complete example site/data/ in a temporary folder: docs, manifest.json, ledger.json."""
    d = tmp_path / "site_data"
    copy_example_docs(d)
    manifest = example_manifest(tmp_path)
    mf.write(manifest, d / "manifest.json")
    ledger = lb.build_ledger(lb.site_docs(d), m5_ledger(), manifest)
    lb.write(ledger, d / "ledger.json")
    return d


def records_of(site_dir: Path) -> dict:
    return lb.load_json(site_dir / "ledger.json")["records"]


def plant(doc, path, value):
    node = doc
    for k in path[:-1]:
        node = node[k]
    node[path[-1]] = value


# Registry ------------------------------------------------------------------------------------

def test_registry_enums_match_precompute():
    assert reg.EVIDENCE_CLASSES == pc.EVIDENCE_CLASSES
    assert reg.VALIDATION_STATES == pc.VALIDATION_STATES
    assert reg.RECORD_FIELDS == pc.RECORD_FIELDS
    assert reg.SCHEMA_VERSION == pc.SCHEMA_VERSION


def test_every_static_and_m5_id_has_exactly_one_template():
    ids = list(reg.STATIC_IDS) + list(m5_ledger()["records"]) + list(m5_ledger()["external"])
    for rid in ids:
        assert reg.ID_RE.match(rid), rid
        assert reg.template_for(rid) is not None, rid


def test_seal_names_cover_every_class_in_the_copy():
    names = lb.seal_names()
    assert set(names) == set(reg.EVIDENCE_CLASSES)
    assert names["estimated_from_satellite"] == "Estimated from satellite"
    assert names["seen_by_satellite"] == "Seen by satellite"


def test_scoreboard_ids_are_implicit():
    board = json.loads(SCOREBOARD.read_text(encoding="utf-8"))["scoreboard"]
    assert lb.loose_numbers(board), "the raw scoreboard has numbers without a ledger key"
    view = reg.explicit_view("scoreboard.json", board)
    assert lb.loose_numbers(view) == []
    ids = {r.id for r in lb.references("scoreboard.json", board)}
    assert {f"scoreboard.tests.{t}" for t in ("L0", "T0", "T1", "P1", "A1", "H2", "R1")} <= ids
    assert "scoreboard.s_by_site" in ids


# Build ---------------------------------------------------------------------------------------

def test_example_builds_and_assembles(site):
    assert assemble.check_site(site) == []
    led = lb.load_json(site / "ledger.json")
    assert led["schema_version"] == reg.SCHEMA_VERSION
    assert led["label"] == "example, fictitious"
    owners = {r["owner"] for r in led["records"].values()}
    assert owners == {"M2", "M4", "M5", "M7"}
    assert set(m5_ledger()["records"]) <= set(led["records"])
    assert set(m5_ledger()["external"]) <= set(led["records"])


def test_build_is_deterministic(tmp_path):
    d = tmp_path / "s"
    copy_example_docs(d)
    m = example_manifest(tmp_path)
    a = lb.dump(lb.build_ledger(lb.site_docs(d), m5_ledger(), m))
    b = lb.dump(lb.build_ledger(lb.site_docs(d), m5_ledger(), copy.deepcopy(m)))
    assert a == b
    assert "built_at" not in a


def test_external_record_takes_value_and_interval_from_the_screen(site):
    rec = records_of(site)["field_a.green_end.2025_26"]
    field = lb.load_json(site / "field_a.json")
    ge = next(g for g in field["green_end"] if g["season"] == "2025/26")
    assert rec["value"] == ge["gap_days"]["v"]
    assert rec["uncertainty"] == {"kind": "event_interval", "low": ge["low"], "high": ge["high"]}
    assert rec["seal"] == "Estimated from satellite"
    assert rec["limits"] == "Green ending is not harvest. Confirm with your own date."
    assert rec["data_version"].startswith("HLSL30_VI 2.0, HLSS30_VI 2.0")


def test_data_version_carries_no_download_date():
    # SA-D104: the version only, even when an old manifest still holds a download time.
    sources = {"x": {"version": "X 1.0", "retrieved_at": "2000-01-01T00:00:00+00:00"}, "y": {"title": "Y"}}
    assert lb.compose_data_version(["x", "y"], sources) == "X 1.0; Y"


def test_m5_record_keeps_its_fields_and_gets_its_gaps_filled(site):
    m5 = m5_ledger()["records"]["field_a.risk"]
    rec = records_of(site)["field_a.risk"]
    for k in ("unit", "evidence_class", "source_id", "transform", "method_id", "data_version", "validation_state"):
        assert rec[k] == m5[k], k
    assert m5["limits"] is None and rec["limits"]
    assert m5["uncertainty"] is None and rec["uncertainty"] == {"kind": "not_quantified"}


def test_browser_records_are_null_and_name_the_function(site):
    recs = records_of(site)
    for rid in reg.STATIC_IDS:
        assert recs[rid]["value"] is None, rid
    assert recs["browser.S3.G"]["transform"] == "decision.mjs:computeG3"
    assert recs["browser.S2.G"]["transform"] == "decision.mjs:computeG2"
    assert lb.resolve_transform("decision.mjs:computeG3")
    # Counts N of n share the source of the trial evidence they count.
    assert recs["browser.S3.N"]["source_id"] == recs["trials.S3"]["source_id"] == "example_trials"


def test_undeclared_id_stops_the_build(tmp_path):
    d = tmp_path / "s"
    copy_example_docs(d)
    doc = lb.load_json(d / "region.json")
    doc["units"]["acre_ha"] = {"v": 0.4, "l": "region.mystery.acre_ha"}
    (d / "region.json").write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(lb.LedgerError, match="undeclared"):
        lb.build_ledger(lb.site_docs(d), m5_ledger(), example_manifest(tmp_path))


def test_one_id_with_two_values_stops_the_build(tmp_path):
    d = tmp_path / "s"
    copy_example_docs(d)
    doc = lb.load_json(d / "field_b.json")
    doc["area_ha"] = {"v": 99.0, "l": "field_a.area_ha"}
    (d / "field_b.json").write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(lb.LedgerError, match="different values"):
        lb.build_ledger(lb.site_docs(d), m5_ledger(), example_manifest(tmp_path))


def test_cli_writes_only_where_asked(tmp_path):
    d = tmp_path / "s"
    copy_example_docs(d)
    mf.write(example_manifest(tmp_path), d / "manifest.json")
    out = tmp_path / "out" / "ledger.json"
    assert lb.main(["--site-data", str(d), "--m5-ledger", str(EXAMPLE_DIR / "ledger_m5.json"),
                    "--out", str(out)]) == 0
    assert out.exists() and not (d / "ledger.json").exists()
    assert lb.main(["--site-data", str(d), "--m5-ledger", str(tmp_path / "absent.json"),
                    "--out", str(out)]) == 1


def test_transforms_resolve_statically():
    assert lb.resolve_transform("pipeline.decision.calendar.window_reading")
    assert lb.resolve_transform("pipeline.decision.risk_counts")
    assert lb.resolve_transform("pipeline/decision/run_states.mjs")
    assert not lb.resolve_transform("pipeline.decision.calendar.no_such_function")
    assert not lb.resolve_transform("decision.mjs:noSuchFunction")
    probs = lb.unresolved_transforms({"x.y": {"transform": "pipeline.nowhere.f"}})
    assert probs and "x.y" in probs[0]


# M6.1 ----------------------------------------------------------------------------------------

def test_planted_number(tmp_path, site):
    """M6.1: each planted number stops the build. If a plant passes, this test fails."""
    plants = json.loads(PLANTED.read_text(encoding="utf-8"))["planted"]
    assert assemble.main(["--site-data", str(site)]) == 0
    for name, p in plants.items():
        d = tmp_path / f"planted_{name}"
        shutil.copytree(site, d)
        doc = lb.load_json(d / p["file"])
        plant(doc, p["path"], p["value"])
        (d / p["file"]).write_text(json.dumps(doc), encoding="utf-8")
        # The checker on the published folder exits with an error.
        assert assemble.check_site(d), f"planted {name} passed the checker"
        assert assemble.main(["--site-data", str(d)]) == 1, name
        # Rebuilding the ledger does not let it through either.
        if name != "loose":
            with pytest.raises(lb.LedgerError):
                lb.build_ledger(lb.site_docs(d), m5_ledger(), lb.load_json(d / "manifest.json"))
        else:
            ledger = lb.build_ledger(lb.site_docs(d), m5_ledger(), lb.load_json(d / "manifest.json"))
            lb.write(ledger, d / "ledger.json")
            assert any("without a ledger record" in x for x in assemble.check_site(d))


# M6.3 ----------------------------------------------------------------------------------------

def test_records(site):
    """M6.3: ten fields filled, enums valid, source_id in the manifest."""
    recs = records_of(site)
    sources = set(lb.manifest_index(lb.load_json(site / "manifest.json")))
    assert recs
    for rid, r in recs.items():
        assert r["id"] == rid
        assert all(k in r for k in reg.RECORD_FIELDS), rid
        for k in ("unit", "transform", "method_id", "data_version", "limits"):
            assert isinstance(r[k], str) and r[k].strip(), (rid, k)
        assert r["evidence_class"] in reg.EVIDENCE_CLASSES, rid
        assert r["validation_state"] in reg.VALIDATION_STATES, rid
        assert r["uncertainty"]["kind"] in reg.UNCERTAINTY_KINDS, rid
        sids = lb.source_list(r["source_id"])
        if sids:
            assert set(sids) <= sources, (rid, sids)
        else:
            assert r["evidence_class"] in reg.SOURCELESS_CLASSES, rid
        assert r["seal"] == lb.seal_names()[r["evidence_class"]]
    assert lb.validate_records(recs, lb.load_json(site / "manifest.json")) == []


@pytest.mark.parametrize("change, message", [
    ({"limits": None}, "limits is empty"),
    ({"method_id": " "}, "method_id is empty"),
    ({"evidence_class": "observed"}, "evidence_class"),
    ({"validation_state": "validated"}, "validation_state"),
    ({"uncertainty": None}, "uncertainty"),
    ({"uncertainty": {"kind": "event_interval"}}, "low and high"),
    ({"source_id": "hls_vi_v2,not_in_manifest"}, "not in the manifest"),
    ({"source_id": None}, "source_id is empty"),
    ({"value": float("nan")}, "finite"),
])
def test_records_rejects_incomplete(site, change, message):
    recs = records_of(site)
    rec = {**recs["field_a.green_end.2025_26"], **change}
    probs = lb.validate_record(rec, set(lb.manifest_index(lb.load_json(site / "manifest.json"))))
    assert any(message in p for p in probs), probs


def test_record_without_a_field_is_rejected(site):
    rec = dict(records_of(site)["field_a.risk"])
    del rec["data_version"]
    assert any("missing fields" in p for p in lb.validate_record(rec, set()))


# M6.4 ----------------------------------------------------------------------------------------

def test_classes(site):
    """M6.4: seen_by_satellite only on reflectance and NDVI, POWER is reanalysis, +234 derived."""
    recs = records_of(site)
    docs = lb.site_docs(site)
    assert lb.check_classes(recs, docs) == []
    seen = [rid for rid, r in recs.items() if r["evidence_class"] == "seen_by_satellite"]
    assert seen and all(rid.endswith(".ndvi_series") for rid in seen)
    power = [rid for rid, r in recs.items() if reg.POWER_SOURCE in lb.source_list(r["source_id"])]
    assert power and all(recs[rid]["evidence_class"] == "reanalysis" for rid in power)
    # The step counts of S3 (counts N of n on the deltas) are derived, never measured.
    assert recs["trials.S3"]["evidence_class"] == "derived_from_trials"

    def broken(rid, **change):
        return lb.check_classes({**recs, rid: {**recs[rid], **change}}, docs)

    assert any("reflectance and NDVI" in p for p in broken("field_a.area_ha", evidence_class="seen_by_satellite"))
    assert any("reflectance and NDVI" in p for p in broken("field_a.ribbon", evidence_class="seen_by_satellite"))
    assert any("reanalysis" in p for p in broken("field_a.risk", evidence_class="estimated_from_satellite"))
    assert any("counts N of n" in p for p in broken("trials.S3", evidence_class="measured_in_trials"))
    assert any("never shows money" in p for p in broken("field_a.next_soy", unit="R$/ha"))
    imerg = {**recs["field_a.risk"], "id": "field_a.risk_imerg", "evidence_class": "estimated_from_satellite"}
    assert lb.check_classes({"field_a.risk_imerg": imerg}) == []


def test_plus_234_is_derived_from_trials(tmp_path):
    """The mean of the deltas (+234 kg/ha, plan 10.1) cannot be labelled as measured.

    M5 emits it in trials_steps.json as options.S3.summary.delta (a fictitious value here)."""
    d = tmp_path / "s"
    copy_example_docs(d)
    doc = lb.load_json(d / "trials_steps.json")
    assert doc["options"]["S3"]["summary"]["delta"]["l"] == "trials.S3.mean_delta"
    m = example_manifest(tmp_path)
    m5 = m5_ledger()
    ledger = lb.build_ledger(lb.site_docs(d), m5, m)
    rec = ledger["records"]["trials.S3.mean_delta"]
    assert rec["evidence_class"] == "derived_from_trials" and rec["value"] == doc["options"]["S3"]["summary"]["delta"]["v"]
    bad = copy.deepcopy(m5)
    bad["records"]["trials.S3.mean_delta"]["evidence_class"] = "measured_in_trials"
    with pytest.raises(lb.LedgerError, match="derived_from_trials"):
        lb.build_ledger(lb.site_docs(d), bad, m)


def test_field_order_inputs_and_browser_records(site):
    """8.7: the browser computes the field order from region.noise_floor; no field_<id>.field_order."""
    recs = records_of(site)
    assert recs["region.noise_floor"]["transform"] == "pipeline.bench.n1.evaluate"
    assert not [rid for rid in recs if rid.endswith(".field_order") and rid.startswith("field_")]
    for rid, fn in (("browser.field_order", "decision.mjs:fieldOrder"), ("browser.hls_switch", "app.mjs:switchCount"),
                    ("browser.nitrogen_exposure", "app.mjs:nitrogenExposure")):
        assert rid in reg.STATIC_IDS and recs[rid]["transform"] == fn and recs[rid]["value"] is None, rid
    assert recs["browser.field_order"]["source_id"] == "hls_vi_v2"


def test_strict_every_transform_names_code(site):
    """--strict: every record of the example ledger points to an existing module, function or file."""
    recs = records_of(site)
    assert lb.unresolved_transforms(recs) == []
    for t in reg.TEMPLATES:
        if t.transform:
            assert lb.resolve_transform(t.transform), (t.pattern, t.transform)
    for rid, t in reg.TRANSFORM_OVERRIDES.items():
        assert lb.resolve_transform(t), (rid, t)
    out = site.parent / "strict" / "ledger.json"
    assert lb.main(["--site-data", str(site), "--m5-ledger", str(EXAMPLE_DIR / "ledger_m5.json"),
                    "--out", str(out), "--strict"]) == 0


def test_every_geometry_field_id_gives_ledger_ids():
    """geometry.FIELD_ID allows "-": the ledger id scheme accepts every id it accepts."""
    from pipeline.fields import geometry

    for fid in ("a", "north-1", "c_2", "0x", "a" * 32):
        assert geometry.FIELD_ID.match(fid), fid
        for rid in (f"field_{fid}.soil", f"field_{fid}.green_end.2025_26", f"field_{fid}.ndvi_series"):
            assert reg.ID_RE.match(rid) and reg.template_for(rid) is not None, rid
    assert any(p.match("field_north-1.ndvi_series") for p in reg.SEEN_BY_SATELLITE_IDS)
    assert not reg.ID_RE.match("field_a..soil") and not reg.ID_RE.match("Field_a.soil")


# M6.2 and M6.5 -------------------------------------------------------------------------------

def real_pages():
    pages = assemble.dom_pages()
    ledger = ROOT / "site" / "data" / "ledger.json"
    return pages if pages and ledger.exists() else []


def test_rendered_dom(site):
    """M6.2: every text with a digit is under data-ledger or in the fixed copy list."""
    allow = assemble.CopyAllowlist.from_files(assemble.COPY_FILES)
    ids = set(records_of(site))
    sources = set(lb.manifest_index(lb.load_json(site / "manifest.json")))
    html = DOM_EXAMPLE.read_text(encoding="utf-8")
    assert assemble.check_dom(html, ids, sources, allow) == []
    for planted in ('<p>Area 12.5 ha</p>',                                     # a bare number
                    '<p>Bag = 50 kg.</p>',                                    # copy changed
                    '<img src="x.svg" alt="7 of 10">',                        # visible attribute
                    '<input type="text" value="100">',                        # typed example value
                    '<p data-copy="page.why.S3.evidence">Embrapa Doc 999</p>',  # not in that key
                    '<p>S3 needs 4 sc/ha</p>'):                               # identifier plus a number
        bad = html.replace("</main>", planted + "</main>")
        assert assemble.check_dom(bad, ids, sources, allow), f"planted {planted!r} passed"
    # Script and style text is not screen text.
    assert assemble.check_dom("<script>let x = 5;</script><style>p{margin:1px}</style>", ids, sources, allow) == []
    for page in real_pages():
        led = lb.load_json(ROOT / "site" / "data" / "ledger.json")
        man = lb.load_json(ROOT / "site" / "data" / "manifest.json")
        assert assemble.check_dom(page.read_text(encoding="utf-8"), set(led["records"]),
                                  set(lb.manifest_index(man)), allow) == [], page.name


def test_ids_resolve(site):
    """M6.5: every rendered data-ledger has a record in ledger.json."""
    ids = set(records_of(site))
    html = DOM_EXAMPLE.read_text(encoding="utf-8")
    used = assemble.dom_ledger_ids(html)
    assert used and set(used) <= ids
    allow = assemble.CopyAllowlist.from_files(assemble.COPY_FILES)
    sources = set(lb.manifest_index(lb.load_json(site / "manifest.json")))
    bad = html.replace('data-ledger="browser.S3.N"', 'data-ledger="browser.S3.N_typo"')
    assert any("no record" in p for p in assemble.check_dom(bad, ids, sources, allow))
    bad = html.replace('data-manifest="power_daily"', 'data-manifest="power_hourly"')
    assert any("not in manifest" in p for p in assemble.check_dom(bad, ids, sources, allow))
    # The whole check through the CLI, on a folder of rendered pages.
    dom = site.parent / "dom"
    dom.mkdir()
    (dom / "dom_index.html").write_text(html, encoding="utf-8")
    assert assemble.main(["--site-data", str(site), "--dom", str(dom)]) == 0
    (dom / "dom_sheet.html").write_text(bad, encoding="utf-8")
    assert assemble.main(["--site-data", str(site), "--dom", str(dom)]) == 1
    for page in real_pages():
        led = lb.load_json(ROOT / "site" / "data" / "ledger.json")
        assert set(assemble.dom_ledger_ids(page.read_text(encoding="utf-8"))) <= set(led["records"]), page.name

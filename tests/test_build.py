# Written with an SI tool (Claude, Anthropic) for NASA Space Apps 2026. See AI_USE.md.
"""Build step: site/data/manifest.json and the assemble check (plan 8.3, 8.6, sheets M6 and M9).

The manifest tests run on a synthetic raw folder in a temporary directory, with files and
``.meta.json`` written the way ``pipeline.adapters.base.cached_get()`` writes them. The
contents are fictitious bytes. One test marked ``data`` reads the real cache (read only) and
writes its output to a temporary folder. Nothing here writes to site/data/ or data/raw/.
"""

import hashlib
import json
import shutil
from pathlib import Path

import pytest

from pipeline.adapters import base
from pipeline.build import assemble
from pipeline.build import copy_json
from pipeline.build import manifest as mf
from pipeline.build import notice
from pipeline.ledger import build as lb
from pipeline.ledger import registry as reg

FIX = Path(__file__).parent / "fixtures"
EXAMPLE_DIR = FIX / "decision_example"
CONFIG = base.load_config()


def put(raw: Path, folder: str, rel: str, body: bytes, *, url: str = "https://example.invalid/x",
        retrieved_at: str = "2000-01-01T00:00:00+00:00", meta: bool = True, **override) -> Path:
    """A raw file and its .meta.json, in the shape of base.cached_get()."""
    p = raw / folder / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(body)
    if meta:
        info = {"source_id": folder, "url": url, "params": {}, "final_url": url, "retrieved_at": retrieved_at,
                "http_status": 200, "content_type": "application/octet-stream", "bytes": len(body),
                "sha256": hashlib.sha256(body).hexdigest(), "cache_key": "0" * 16, **override}
        base.meta_path(p).write_text(json.dumps(info), encoding="utf-8")
    return p


@pytest.fixture
def raw(tmp_path):
    r = tmp_path / "data" / "raw"
    put(r, "power_daily", "londrina_met.json", b"example, fictitious 1",
        url="https://power.larc.nasa.gov/api/temporal/daily/point", retrieved_at="2000-01-01T00:00:01+00:00")
    put(r, "power_daily", "londrina_imerg.json", b"example, fictitious 2",
        url="https://power.larc.nasa.gov/api/temporal/daily/point", retrieved_at="2000-01-01T00:00:02+00:00")
    put(r, "hls", "w_0/HLS-VI.L30.T22KDV.2026001T000000.v2.0.NDVI.npz", b"vi ndvi")
    put(r, "hls", "w_0/HLS.L30.T22KDV.2026001T000000.v2.0.B04.npz", b"sr red")
    put(r, "hls", "cmr_HLSL30_VI_2025-09-01_2026-08-31_0000.json", b"cmr vi")
    put(r, "embrapa_docs", "Doc398.pdf", b"%PDF example",
        url="https://www.infoteca.cnptia.embrapa.br/infoteca/bitstream/doc/1086382/1/Doc398.pdf")
    return r


def build(raw, **kw):
    return mf.build_manifest(raw, config=CONFIG, **kw)


def by_id(doc):
    return {s["source_id"]: s for s in doc["sources"]}


# Manifest ------------------------------------------------------------------------------------

def test_manifest_lists_every_raw_file_with_its_meta(raw):
    doc = build(raw)
    assert doc["schema_version"] == reg.SCHEMA_VERSION
    assert "built_at" not in doc and "code_commit" not in doc     # no build time, no commit
    data_files = sorted(p.relative_to(raw.parent.parent).as_posix() for p in raw.rglob("*")
                        if p.is_file() and not p.name.endswith(".meta.json"))
    assert [f["path"] for f in doc["files"]] == data_files
    for f in doc["files"]:
        meta = json.loads(base.meta_path(raw.parent.parent / f["path"]).read_text(encoding="utf-8"))
        for k in ("url", "params", "sha256", "bytes"):
            assert f[k] == meta[k]
        assert "retrieved_at" not in f        # the download time stays in the local .meta.json


def test_manifest_source_entries_follow_plan_8_6(raw):
    s = by_id(build(raw))
    keys = {"source_id", "title", "url", "license", "version", "sha256", "attribution"}
    assert all(keys <= set(e) for e in s.values())
    p = s["power_daily"]
    assert p["n_files"] == 2 and p["cached"]
    assert not any("retrieved_at" in e for e in s.values())
    lines = "".join(f"{f['path']}\t{f['sha256']}\n" for f in sorted(
        (f for f in build(raw)["files"] if f["source_id"] == "power_daily"), key=lambda f: f["path"]))
    assert p["sha256"] == hashlib.sha256(lines.encode()).hexdigest()
    assert p["license"] == CONFIG["sources"]["power"]["license"]       # the config owns raw licenses
    assert p["version"] == "POWER daily point API, community AG"
    assert s["hls"]["version"].startswith("HLSL30_VI 2.0, HLSS30_VI 2.0")


def test_manifest_vi_subset_and_cited_documents(raw):
    s = by_id(build(raw))
    assert s["hls"]["n_files"] == 3
    vi = s["hls_vi_v2"]
    assert vi["n_files"] == 2 and vi["files_from"] == "hls"
    assert vi["version"] == "HLSL30_VI 2.0, HLSS30_VI 2.0"
    # A document cited by the decision (strategies.SOURCES) points to its cached copy by URL.
    doc = s["embrapa_doc398"]
    assert doc["cached"] and doc["files_from"] == "embrapa_docs"
    assert doc["sha256"] == hashlib.sha256(b"%PDF example").hexdigest()
    assert not s["embrapa_doc476"]["cached"] and s["embrapa_doc476"]["sha256"] is None
    # Declared but not cached yet.
    assert s["stations"]["cached"] is False and s["stations"]["n_files"] == 0


def test_vi_subset_holds_the_fmask_of_the_series_only(raw):
    """hls_vi_v2 takes the HLS Fmask that ndvi_series masks VI with, not the basemap window's."""
    put(raw, "hls", "w_0/HLS.S30.T22KDV.2026001T000000.v2.0.Fmask.npz", b"vi window fmask")
    put(raw, "hls", "w_1/HLS.L30.T22KDV.2026002T000000.v2.0.Fmask.npz", b"basemap fmask")
    put(raw, "hls", "w_1/HLS.L30.T22KDV.2026002T000000.v2.0.B04.npz", b"basemap red")
    doc = build(raw)
    vi = {f["path"] for f in doc["files"]
          if f in mf.subset_files([x for x in doc["files"] if x["source_id"] == "hls"], "hls",
                                  reg.SOURCE_INFO["hls_vi_v2"])}
    assert "data/raw/hls/w_0/HLS.S30.T22KDV.2026001T000000.v2.0.Fmask.npz" in vi
    assert "data/raw/hls/w_1/HLS.L30.T22KDV.2026002T000000.v2.0.Fmask.npz" not in vi
    assert "data/raw/hls/cmr_HLSL30_VI_2025-09-01_2026-08-31_0000.json" in vi       # top-level CMR answers
    s = by_id(doc)
    assert s["hls_vi_v2"]["n_files"] == 3 and s["hls"]["n_files"] == 6


def test_manifest_is_reproducible(raw, tmp_path):
    a = mf.write(build(raw), tmp_path / "a" / "manifest.json")
    b = mf.write(build(raw), tmp_path / "b" / "manifest.json")
    assert a.read_bytes() == b.read_bytes()
    assert assemble.canonical_digest(a) == assemble.canonical_digest(b)


@pytest.mark.parametrize("damage, message", [
    ("no_meta", "no .meta.json"),
    ("orphan_meta", "without its file"),
    ("part", "unfinished download"),
    ("sha", "sha256 differs"),
    ("size", "size differs"),
    ("folder", "the folder is"),
    ("undeclared", "not declared"),
    ("meta_keys", "lacks"),
])
def test_manifest_refuses_a_broken_cache(raw, damage, message):
    if damage == "no_meta":
        put(raw, "power_daily", "x.json", b"x", meta=False)
    elif damage == "orphan_meta":
        p = put(raw, "power_daily", "y.json", b"y")
        p.unlink()
    elif damage == "part":
        put(raw, "power_daily", "z.json.part", b"z", meta=False)
    elif damage == "sha":
        put(raw, "power_daily", "s.json", b"s", sha256="0" * 64)
    elif damage == "size":
        put(raw, "power_daily", "t.json", b"t", bytes=99)
    elif damage == "folder":
        put(raw, "power_daily", "u.json", b"u", source_id="hls")
    elif damage == "undeclared":
        put(raw, "mystery_source", "v.json", b"v")
    elif damage == "meta_keys":
        p = put(raw, "power_daily", "w.json", b"w")
        m = json.loads(base.meta_path(p).read_text(encoding="utf-8"))
        del m["sha256"]
        base.meta_path(p).write_text(json.dumps(m), encoding="utf-8")
    with pytest.raises(mf.ManifestError, match=message):
        build(raw)


def test_manifest_never_writes_to_raw(raw):
    with pytest.raises(ValueError):
        mf.write(build(raw), base.RAW_DIR / "manifest.json")


def test_manifest_cli_splits_the_file_list(raw, tmp_path):
    out, files = tmp_path / "o" / "manifest.json", tmp_path / "o" / "manifest_files.json"
    assert mf.main(["--raw", str(raw), "--out", str(out), "--files-out", str(files)]) == 0
    doc, lst = lb.load_json(out), lb.load_json(files)
    assert "files" not in doc and doc["files_in"] == "manifest_files.json"
    assert "code_commit" not in doc and "built_at" not in doc and "built_at" not in lst
    assert len(lst["files"]) == 6 and lst["schema_version"] == reg.SCHEMA_VERSION
    put(raw, "power_daily", "broken.json", b"b", meta=False)
    assert mf.main(["--raw", str(raw), "--out", str(out)]) == 1


@pytest.mark.data
def test_manifest_of_the_real_cache(tmp_path):
    """Every file of data/raw/ is in the manifest and every raw folder is declared (read only)."""
    if not base.RAW_DIR.exists() or not any(base.RAW_DIR.iterdir()):
        pytest.skip("data/raw/ is empty, run `make data` first")
    doc = mf.build_manifest(base.RAW_DIR, verify=False)
    n = sum(1 for p in base.RAW_DIR.rglob("*")
            if p.is_file() and p.name not in mf.IGNORED and not p.name.endswith(".meta.json"))
    assert len(doc["files"]) == n
    folders = {p.name for p in base.RAW_DIR.iterdir() if p.is_dir()}
    assert folders <= set(by_id(doc))
    mf.write(doc, tmp_path / "manifest.json")


# Assemble ------------------------------------------------------------------------------------

@pytest.fixture
def site(tmp_path, raw):
    d = tmp_path / "site_data"
    d.mkdir()
    for p in EXAMPLE_DIR.glob("*.json"):
        if not p.name.endswith("_m5.json"):
            shutil.copy(p, d / p.name)
    m = build(raw, extra_sources={"example_trials": {"title": "Example trials, fictitious", "fictitious": True},
                                  "example_calendar": {"title": "Example calendar, fictitious", "fictitious": True}})
    mf.write(m, d / "manifest.json")
    lb.write(lb.build_ledger(lb.site_docs(d), lb.load_json(EXAMPLE_DIR / "ledger_m5.json"), m), d / "ledger.json")
    return d


def test_assemble_passes_the_example(site):
    assert assemble.check_site(site) == []
    assert assemble.main(["--site-data", str(site)]) == 0


def test_assemble_refuses_the_example_in_the_public_folder(site):
    assert assemble.check_site(site) == []                       # a test folder may hold the example
    probs = assemble.check_site(site, public=True)               # as if it were site/data/
    assert any("public folder" in p for p in probs)
    doc = lb.load_json(site / "ledger.json")
    doc["label"] = "real"
    (site / "ledger.json").write_text(json.dumps(doc), encoding="utf-8")
    assemble.add_notice(site)                                    # the public folder needs the SI notice
    assert assemble.check_site(site, public=True) == []


def test_assemble_needs_schema_version_everywhere(site):
    for name in ("region.json", "ledger.json", "manifest.json"):
        doc = lb.load_json(site / name)
        doc.pop("schema_version")
        (site / name).write_text(json.dumps(doc), encoding="utf-8")
        assert f"{name}: no schema_version" in assemble.check_site(site)


def test_assemble_refuses_intermediates_and_missing_meta(site):
    shutil.copy(EXAMPLE_DIR / "states_m5.json", site / "states_m5.json")
    assert any("M5 intermediate" in p for p in assemble.check_site(site))
    (site / "states_m5.json").unlink()
    (site / "ledger.json").unlink()
    probs = assemble.check_site(site)
    assert "ledger.json: missing from " + str(site) in probs
    assert any("not in ledger.json" in p for p in probs)


def test_assemble_checks_every_named_source(site):
    doc = lb.load_json(site / "region.json")
    doc["strategies"][0]["source_ids"] = ["embrapa_doc398", "no_such_source"]
    (site / "region.json").write_text(json.dumps(doc), encoding="utf-8")
    assert "region.json: source no_such_source is not in manifest.json" in assemble.check_site(site)


@pytest.mark.parametrize("doc_name, plant, where", [
    ("manifest.json", lambda d: d.update(built_at="2000-01-01T00:00:00+00:00"), "built_at"),
    ("manifest.json", lambda d: d.update(code_commit="0" * 40), "code_commit"),
    ("manifest.json", lambda d: d["sources"][0].update(retrieved_at="2000-01-01T00:00:00+00:00"), "retrieved_at"),
    ("ledger.json", lambda d: d.update(late_notes=[{"at": "2000-01-01T00:00", "text": "x"}]), "late_notes[0].at"),
])
def test_assemble_refuses_a_date_of_the_work(site, doc_name, plant, where):
    # SA-D104: no download, build or freeze time and no commit in the published files.
    assert assemble.check_site(site) == []
    doc = lb.load_json(site / doc_name)
    plant(doc)
    (site / doc_name).write_text(json.dumps(doc), encoding="utf-8")
    probs = assemble.check_site(site)
    assert any(where in p and "SA-D104" in p for p in probs), probs


def test_assemble_checks_manifest_entries(site):
    doc = lb.load_json(site / "manifest.json")
    del doc["sources"][0]["attribution"]
    (site / "manifest.json").write_text(json.dumps(doc), encoding="utf-8")
    assert any("lacks ['attribution']" in p for p in assemble.check_site(site))


def test_assemble_without_pages_in_dom_folder_fails(site, tmp_path):
    empty = tmp_path / "empty_dom"
    empty.mkdir()
    assert assemble.main(["--site-data", str(site), "--dom", str(empty)]) == 1


def test_digests_are_canonical(site, capsys):
    before = assemble.digests(site)
    doc = lb.load_json(site / "manifest.json")
    (site / "manifest.json").write_text(json.dumps(doc, indent=4, sort_keys=True), encoding="utf-8")
    assert assemble.digests(site) == before                    # layout and key order do not count
    assert assemble.main(["--site-data", str(site), "--digests"]) == 0
    doc["sources"][0]["title"] = "other"
    (site / "manifest.json").write_text(json.dumps(doc), encoding="utf-8")
    assert assemble.digests(site)["manifest.json"] != before["manifest.json"]
    assert "ledger.json" in json.loads(capsys.readouterr().out)


# SI notice as metadata (M9.4) ---------------------------------------------------------------

def test_stamp_keeps_the_indent_and_is_idempotent(tmp_path):
    for indent in (None, 1, 2):
        p = tmp_path / f"x{indent}.json"
        p.write_text(json.dumps({"schema_version": 1, "a": [1, 2]}, indent=indent) + "\n", encoding="utf-8")
        assert notice.stamp_file(p) is True
        text = p.read_text(encoding="utf-8")
        doc = json.loads(text)
        assert list(doc)[0] == notice.KEY and doc[notice.KEY] == notice.TEXT
        assert doc["a"] == [1, 2] and doc["schema_version"] == 1
        assert text == json.dumps(doc, indent=indent, ensure_ascii=False) + "\n"
        assert notice.stamp_file(p) is False                     # already there: not rewritten
    (tmp_path / "list.json").write_text("[1]", encoding="utf-8")
    assert notice.stamp_file(tmp_path / "list.json") is False     # not an object: left alone


def test_assemble_adds_the_notice_then_requires_it(site, capsys):
    assert all(notice.KEY not in lb.load_json(p) for p in site.glob("*.json"))
    assert any("no SI notice" in p for p in assemble.check_site(site, require_notice=True))
    assert assemble.main(["--site-data", str(site), "--check-only"]) == 1   # check only: nothing written
    assert all(notice.KEY not in lb.load_json(p) for p in site.glob("*.json"))
    assert assemble.main(["--site-data", str(site)]) == 0
    assert "SI notice added to" in capsys.readouterr().err
    for p in site.glob("*.json"):
        assert notice.has_notice(lb.load_json(p)), p.name
    assert assemble.check_site(site, require_notice=True) == []
    before = {p.name: p.read_bytes() for p in site.glob("*.json")}
    assert assemble.main(["--site-data", str(site)]) == 0          # a second run rewrites nothing
    assert {p.name: p.read_bytes() for p in site.glob("*.json")} == before


def test_assemble_never_stamps_the_fixtures():
    before = {p: p.read_bytes() for p in EXAMPLE_DIR.glob("*.json")}
    assert assemble.add_notice(EXAMPLE_DIR) == []
    assert {p: p.read_bytes() for p in EXAMPLE_DIR.glob("*.json")} == before


def test_copy_json_carries_the_notice():
    doc = copy_json.build_copy()
    assert list(doc)[0] == notice.KEY and doc[notice.KEY] == notice.TEXT
    on_disk = json.loads((assemble.SITE_DATA / "copy.json").read_text(encoding="utf-8"))
    assert notice.has_notice(on_disk)


def test_the_notice_names_ai_use_and_matches_the_code_notice():
    from tests.test_ai_notice import NOTICE
    assert notice.TEXT == NOTICE

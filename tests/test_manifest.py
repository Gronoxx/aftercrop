# Written with an SI tool (Claude, Anthropic) for NASA Space Apps 2026. See AI_USE.md.
"""Criterion M1.2: every file in data/raw/ has its .meta.json and the sha256 matches."""

import pytest

from pipeline.adapters import base

REQUIRED = {"source_id", "url", "params", "retrieved_at", "sha256", "bytes", "http_status"}


def raw_files():
    if not base.RAW_DIR.exists():
        return []
    return sorted(p for p in base.RAW_DIR.rglob("*") if p.is_file() and p.name != ".DS_Store")


@pytest.mark.data
def test_every_raw_file_has_meta_and_matching_sha256():
    files = raw_files()
    if not files:
        pytest.skip("data/raw/ is empty, run `make data` first")
    data = [p for p in files if not p.name.endswith(".meta.json")]
    metas = {p for p in files if p.name.endswith(".meta.json")}
    assert not [p for p in data if p.name.endswith(".part")], "unfinished download left behind"
    for p in data:
        m = base.meta_path(p)
        assert m in metas, f"no .meta.json for {p}"
        info = base.read_meta(p)
        assert REQUIRED <= set(info), (p, REQUIRED - set(info))
        assert info["bytes"] == p.stat().st_size, p
        assert base.verify(p), f"sha256 mismatch: {p}"
    orphans = [m for m in metas if not m.with_name(m.name[: -len(".meta.json")]).exists()]
    assert not orphans, orphans


def test_every_source_gets_a_license_line():
    # M9.3: a subset document takes its folder's license, a cited-only document says so.
    from pipeline.build import manifest as mf
    sub = mf.entry("embrapa_doc398", [{"bytes": 1, "sha256": "0" * 64, "path": "x"}],
                   bib={"title": "Doc 398", "url": "u"}, info={}, cfg=None, files_from="embrapa_docs")
    assert sub["license"].startswith("Conflict")
    cited = mf.entry("some_paper", [], bib={"title": "A paper", "url": "u"}, info={}, cfg=None, files_from=None)
    assert cited["license"] == mf.CITED_ONLY

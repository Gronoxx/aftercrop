# Written with an SI tool (Claude, Anthropic) for NASA Space Apps 2026. See AI_USE.md.
"""cached_get(): one download, never overwritten, offline cache miss is an error."""

import json

import pytest

from pipeline.adapters import base


class FakeResponse:
    def __init__(self, body=b"hello", status=200):
        self.body, self.status_code = body, status
        self.headers = {"Content-Type": "text/plain", "ETag": '"1"'}
        self.url = "https://example.test/x?a=1"
        self.encoding = "utf-8"
        self.text = body.decode()

    def iter_content(self, n):
        yield self.body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.fixture
def raw_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(base, "RAW_DIR", tmp_path / "raw")
    monkeypatch.setattr(base, "REPO_ROOT", tmp_path)
    return tmp_path / "raw"


def test_offline_miss_is_an_error(raw_dir):
    with pytest.raises(base.OfflineCacheMiss):
        base.cached_get("src", "https://example.test/x", {"a": 1})


def test_downloads_once_and_records_meta(raw_dir, monkeypatch):
    monkeypatch.delenv("AFTERCROP_OFFLINE")
    calls = []
    monkeypatch.setattr(base.requests, "get", lambda *a, **k: calls.append(1) or FakeResponse())
    p = base.cached_get("src", "https://example.test/x", {"a": 1}, filename="x.txt")
    assert p.read_bytes() == b"hello"
    meta = json.loads(base.meta_path(p).read_text())
    assert meta["sha256"] == base.sha256_file(p) and meta["bytes"] == 5 and meta["params"] == {"a": 1}
    assert base.verify(p)
    monkeypatch.setenv("AFTERCROP_OFFLINE", "1")
    assert base.cached_get("src", "https://example.test/x", {"a": 1}, filename="x.txt") == p
    assert len(calls) == 1


def test_error_status_is_not_cached(raw_dir, monkeypatch):
    monkeypatch.delenv("AFTERCROP_OFFLINE")
    monkeypatch.setattr(base.requests, "get", lambda *a, **k: FakeResponse(b"busy", 503))
    with pytest.raises(base.FetchError) as e:
        base.cached_get("src", "https://example.test/y")
    assert e.value.status == 503
    assert not any(raw_dir.rglob("*.bin"))


def test_incomplete_entry_is_never_trusted(raw_dir, monkeypatch):
    monkeypatch.delenv("AFTERCROP_OFFLINE")
    p = base.raw_path("src", "https://example.test/z", None, "z.txt")
    p.parent.mkdir(parents=True)
    p.write_bytes(b"half")
    with pytest.raises(RuntimeError):
        base.cached_get("src", "https://example.test/z", filename="z.txt")


def test_cache_key_ignores_param_order():
    assert base.cache_key("u", {"a": 1, "b": 2}) == base.cache_key("u", {"b": 2, "a": 1})


def test_unsafe_names_rejected():
    for bad in ("../x", "a/b", ""):
        if bad:
            with pytest.raises(ValueError):
                base.raw_path("src", "u", None, bad)
    with pytest.raises(ValueError):
        base.raw_path("../up", "u")


def test_derived_files_never_go_to_raw(raw_dir, tmp_path):
    with pytest.raises(ValueError):
        base.write_json(raw_dir / "src" / "x.json", {})

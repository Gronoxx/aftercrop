# Written with an SI tool (Claude, Anthropic) for NASA Space Apps 2026. See AI_USE.md.
"""pipeline/build/verify_link.py against a local http.server (127.0.0.1, random port).

Never a remote URL. The served folder and the "local checkout" are two synthetic copies of
a site in a temporary directory, so a test can make them differ.
"""

import shutil
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

import pytest

from pipeline.build import verify_link as vl

FILES = {
    "index.html": "<!doctype html><title>example, fictitious</title>\n",
    "data/copy.json": '{"schema_version": 1, "langs": {}}\n',
    "data/scoreboard.json": '{"schema_version": 1, "_note": "example, fictitious"}\n',
    "data/manifest.json": '{"schema_version": 1, "sources": [], "code_commit": "example0"}\n',
}


class Quiet(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


def write_site(root):
    for rel, text in FILES.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    return root


@pytest.fixture
def served(tmp_path, monkeypatch):
    """(base URL of a server on a copy of the site under a subpath, the local site folder)."""
    for k in ("http_proxy", "HTTP_PROXY", "https_proxy", "HTTPS_PROXY", "all_proxy", "ALL_PROXY"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("no_proxy", "127.0.0.1")
    local = write_site(tmp_path / "local" / "site")
    root = tmp_path / "remote"
    shutil.copytree(local, root / "aftercrop")                 # served at /aftercrop/, as on Pages
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(Quiet, directory=str(root)))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}/aftercrop/", local, root / "aftercrop"
    finally:
        server.shutdown()
        server.server_close()


def test_base_url_is_the_site_folder():
    assert vl.base_url("https://o.github.io/repo") == "https://o.github.io/repo/"
    assert vl.base_url("https://o.github.io/repo/index.html?x=1") == "https://o.github.io/repo/"
    for bad in ("file:///etc/passwd", "ftp://x/", "o.github.io/repo", ""):
        with pytest.raises(ValueError):
            vl.base_url(bad)


def test_a_matching_site_passes(served, capsys):
    url, local, _ = served
    checks = vl.verify(url, local, timeout=5)
    assert [c.problem for c in checks] == [None] * 4
    assert [c.status for c in checks] == [200] * 4
    assert all(c.remote_sha256 == c.local_sha256 for c in checks)
    assert vl.main([url + "index.html", "--site", str(local), "--timeout", "5"]) == 0
    assert "all 4 files match" in capsys.readouterr().out


def test_a_changed_or_missing_file_fails_with_a_list(served, capsys):
    url, local, remote = served
    (remote / "data" / "scoreboard.json").write_text('{"schema_version": 1, "other": true}\n', encoding="utf-8")
    (remote / "data" / "manifest.json").unlink()
    checks = {c.local: c.problem for c in vl.verify(url, local, timeout=5)}
    assert checks["site/index.html"] is None and checks["site/data/copy.json"] is None
    assert checks["site/data/scoreboard.json"].startswith("sha256 ")
    assert checks["site/data/manifest.json"] == "HTTP 404"
    assert vl.main([url, "--site", str(local), "--timeout", "5"]) == 1
    out = capsys.readouterr()
    assert "BAD" in out.out and "2 of 4 file(s) do not match" in out.err


def test_a_file_missing_locally_is_named(served):
    url, local, _ = served
    (local / "data" / "copy.json").unlink()
    checks = {c.local: c.problem for c in vl.verify(url, local, timeout=5)}
    assert "missing in the local checkout" in checks["site/data/copy.json"]


def test_no_answer_is_a_problem_not_a_crash(tmp_path):
    local = write_site(tmp_path / "site")
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Quiet)
    port = srv.server_address[1]
    srv.server_close()                                          # nothing listens on this port now
    checks = vl.verify(f"http://127.0.0.1:{port}/", local, timeout=2)
    assert all(c.problem and c.problem.startswith("no answer") for c in checks)


def test_a_bad_url_exits_2():
    assert vl.main(["file:///tmp/x"]) == 2

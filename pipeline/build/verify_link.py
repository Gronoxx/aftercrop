# Written with an SI tool (Claude, Anthropic) for NASA Space Apps 2026. See AI_USE.md.
"""Check the published site against this checkout (plan P4). It reads, it never sends.

For the page and three data files (``data/copy.json``, ``data/scoreboard.json``,
``data/manifest.json``) it asks for the published copy and checks

- HTTP 200;
- the sha256 of the bytes served equals the sha256 of the same file in the local ``site/``.

``manifest.json`` pins the build (``built_at``, ``code_commit`` and the digest of every raw
source), so the same manifest bytes mean the published data come from the same build as the
local checkout. The other files are compared byte for byte with their local copy.

Exit 0 when everything matches, 1 with the list of problems, 2 on a bad URL. Standard library
only. The only request is a GET of the four URLs, with a generic User-Agent and no cookies.

Run from the repository root (``make verify-link URL=...``):
    python -m pipeline.build.verify_link https://<owner>.github.io/<repository>/ [--site site]
"""

from __future__ import annotations

import argparse
import hashlib
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SITE = ROOT / "site"
# Published path -> local path under site/. "" is the page itself (Pages serves index.html).
FILES = (
    ("", "index.html"),
    ("data/copy.json", "data/copy.json"),
    ("data/scoreboard.json", "data/scoreboard.json"),
    ("data/manifest.json", "data/manifest.json"),
)
USER_AGENT = "aftercrop-verify-link/1"
TIMEOUT_S = 20.0


@dataclass(frozen=True)
class Check:
    url: str
    local: str
    status: int | None
    remote_sha256: str | None
    local_sha256: str | None
    problem: str | None


def base_url(url: str) -> str:
    """The folder of the published site, ending in "/". Raises ValueError on anything not http(s)."""
    parts = urllib.parse.urlsplit(url.strip())
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise ValueError(f"not an http(s) URL: {url!r}")
    path = parts.path or "/"
    if path.endswith("/index.html"):
        path = path[: -len("index.html")]
    elif not path.endswith("/"):
        path += "/"
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, path, "", ""))


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def fetch(url: str, timeout: float = TIMEOUT_S) -> tuple[int | None, bytes | None, str | None]:
    """(status, body, error). A non-200 answer keeps its status and no body."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Cache-Control": "no-cache"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            return res.status, res.read(), None
    except urllib.error.HTTPError as exc:
        return exc.code, None, f"HTTP {exc.code}"
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        reason = getattr(exc, "reason", exc)
        return None, None, f"no answer ({reason})"


def verify(url: str, site: Path = SITE, *, timeout: float = TIMEOUT_S) -> list[Check]:
    """One Check per file of FILES. ``problem`` is None when the file matches."""
    base = base_url(url)
    out = []
    for published, local in FILES:
        target = urllib.parse.urljoin(base, published)
        lp = Path(site) / local
        local_sha = sha256(lp.read_bytes()) if lp.is_file() else None
        status, body, error = fetch(target, timeout)
        remote_sha = sha256(body) if body is not None else None
        if local_sha is None:
            problem = f"site/{local} is missing in the local checkout (run make piso first)"
        elif error is not None:
            problem = error
        elif status != 200:
            problem = f"HTTP {status}"
        elif remote_sha != local_sha:
            problem = f"sha256 {remote_sha[:12]} served, {local_sha[:12]} in site/{local}"
        else:
            problem = None
        out.append(Check(target, f"site/{local}", status, remote_sha, local_sha, problem))
    return out


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("url", help="the published site, e.g. https://<owner>.github.io/<repository>/")
    ap.add_argument("--site", type=Path, default=SITE, help="the local site folder (default: site/)")
    ap.add_argument("--timeout", type=float, default=TIMEOUT_S)
    a = ap.parse_args(argv)
    try:
        checks = verify(a.url, a.site, timeout=a.timeout)
    except ValueError as exc:
        print(f"verify-link: {exc}", file=sys.stderr)
        return 2
    bad = [c for c in checks if c.problem]
    for c in checks:
        print(f"verify-link: {'ok ' if c.problem is None else 'BAD'} {c.url}" + (f": {c.problem}" if c.problem else ""))
    if bad:
        print(f"verify-link: {len(bad)} of {len(checks)} file(s) do not match the local checkout", file=sys.stderr)
        return 1
    print(f"verify-link: all {len(checks)} files match the local checkout")
    return 0


if __name__ == "__main__":
    sys.exit(main())

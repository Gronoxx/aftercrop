# Written with an SI tool (Claude, Anthropic) for NASA Space Apps 2026. See AI_USE.md.
"""Contract shared by every adapter (plan, section 22, sheet M1).

    fetch(spec) -> path        downloads through cached_get(), never overwrites data/raw/
    read(path) -> table/array  no side effects
    provenance(spec) -> dict   url, params, retrieved_at, sha256, license, version
    AFTERCROP_OFFLINE=1        a cache miss is an error

Every network call in the pipeline goes through ``cached_get()``. Each raw file
gets a ``<name>.meta.json`` next to it. Raw files are untrusted data: they are
only read as bytes, text or tables, never imported or executed.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode

import requests

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = REPO_ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
INTERIM_DIR = DATA_DIR / "interim"
DEFAULT_CONFIG_PATH = REPO_ROOT / "config" / "region_norte_pr.yaml"
CONFIG_ENV = "AFTERCROP_CONFIG"
CONFIG_PATH = DEFAULT_CONFIG_PATH

USER_AGENT = "aftercrop-pipeline/0.1 (+python-requests)"
CHUNK = 1 << 20
_SAFE_NAME = re.compile(r"^[A-Za-z0-9._-]+$")

network_calls = 0  # incremented by every real request; logged as network_calls=N


class OfflineCacheMiss(RuntimeError):
    """AFTERCROP_OFFLINE=1 and the file is not in data/raw/."""


class FetchError(RuntimeError):
    """The source answered, but not with a usable 200 response. Nothing is cached."""

    def __init__(self, message: str, status: int | None = None, body: str = ""):
        super().__init__(message)
        self.status = status
        self.body = body


def is_offline() -> bool:
    return os.environ.get("AFTERCROP_OFFLINE") == "1"


def canonical_url(url: str, params: dict | None) -> str:
    if not params:
        return url
    items = []
    for k in sorted(params):
        v = params[k]
        for x in v if isinstance(v, (list, tuple)) else [v]:
            items.append((k, x))
    return f"{url}?{urlencode(items)}"


def cache_key(url: str, params: dict | None) -> str:
    return hashlib.sha256(canonical_url(url, params).encode()).hexdigest()[:16]


def raw_path(source_id: str, url: str, params: dict | None = None, filename: str | None = None) -> Path:
    """Where cached_get() stores this request. Pure: no I/O."""
    for part in (source_id, filename or ""):
        if part and not _SAFE_NAME.match(part):
            raise ValueError(f"unsafe name: {part!r}")
    return RAW_DIR / source_id / (filename or f"{cache_key(url, params)}.bin")


def meta_path(path: Path) -> Path:
    return path.with_name(path.name + ".meta.json")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(CHUNK), b""):
            h.update(block)
    return h.hexdigest()


def read_meta(path: Path) -> dict:
    with open(meta_path(path), encoding="utf-8") as f:
        return json.load(f)


def verify(path: Path) -> bool:
    """True when the file still matches the sha256 recorded at download time."""
    return sha256_file(path) == read_meta(path)["sha256"]


def cached_get(
    source_id: str,
    url: str,
    params: dict | None = None,
    filename: str | None = None,
    timeout: float = 300.0,
    extra_headers: dict | None = None,
) -> Path:
    """Return the cached file for (url, params), downloading it once if needed.

    A cached file is never rewritten. A file without its .meta.json is treated
    as corrupt and raises, so that a half-finished state is never trusted.
    """
    global network_calls
    path = raw_path(source_id, url, params, filename)
    meta = meta_path(path)
    if path.exists() and meta.exists():
        return path
    if path.exists() or meta.exists():
        raise RuntimeError(f"incomplete cache entry, inspect by hand: {path}")
    if is_offline():
        raise OfflineCacheMiss(f"AFTERCROP_OFFLINE=1 and not cached: {canonical_url(url, params)}")

    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_name(path.name + ".part")
    headers = {"User-Agent": USER_AGENT, **(extra_headers or {})}
    retrieved_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    network_calls += 1
    with requests.get(url, params=params, headers=headers, timeout=timeout, stream=True) as r:
        if r.status_code != 200:
            body = r.text[:2000] if r.encoding or "text" in r.headers.get("Content-Type", "") else ""
            raise FetchError(f"HTTP {r.status_code} from {r.url}", r.status_code, body)
        h = hashlib.sha256()
        size = 0
        with open(part, "wb") as f:
            for block in r.iter_content(CHUNK):
                f.write(block)
                h.update(block)
                size += len(block)
        info = {
            "source_id": source_id,
            "url": url,
            "params": params or {},
            "final_url": r.url,
            "retrieved_at": retrieved_at,
            "http_status": r.status_code,
            "content_type": r.headers.get("Content-Type"),
            "last_modified": r.headers.get("Last-Modified"),
            "etag": r.headers.get("ETag"),
            "bytes": size,
            "sha256": h.hexdigest(),
            "cache_key": cache_key(url, params),
        }
    if path.exists():  # another process won the race; keep the first file
        part.unlink()
        return path
    os.replace(part, path)
    with open(meta, "w", encoding="utf-8") as f:
        json.dump(info, f, indent=2, ensure_ascii=False)
        f.write("\n")
    return path


def base_provenance(path: Path, license: str, version: str, **extra) -> dict:
    """Provenance record from the .meta.json of a cached file. Never touches the network."""
    m = read_meta(path)
    out = {
        "source_id": m["source_id"],
        "url": m["url"],
        "params": m["params"],
        "retrieved_at": m["retrieved_at"],
        "sha256": m["sha256"],
        "bytes": m["bytes"],
        "license": license,
        "version": version,
        "path": str(path.relative_to(REPO_ROOT)),
    }
    out.update(extra)
    return out


def config_path() -> Path:
    """The region config: ``AFTERCROP_CONFIG`` (relative to the repository root, or absolute)
    when set, else ``config/region_norte_pr.yaml``. Read at call time."""
    value = os.environ.get(CONFIG_ENV, "").strip()
    if not value:
        return DEFAULT_CONFIG_PATH
    path = Path(value)
    return path if path.is_absolute() else REPO_ROOT / path


def load_config(path: Path | None = None) -> dict:
    """The YAML of ``path``, else of ``config_path()`` (the default region unless switched)."""
    import yaml

    with open(path or config_path(), encoding="utf-8") as f:
        return yaml.safe_load(f)


def use_region(path: Path | str | None = None) -> dict:
    """Point the module paths at the region of ``path`` (default ``config_path()``).

    A region config with a ``paths:`` block (``raw``, ``interim``, relative to the repository
    root) gets its own raw and interim folders, so a run of another region never writes
    data/raw/ or data/interim/ of the default one. The default config has no such block and
    leaves every path as it is. Returns the config.
    """
    global CONFIG_PATH, RAW_DIR, INTERIM_DIR
    p = Path(path) if path else config_path()
    p = p if p.is_absolute() else REPO_ROOT / p
    config = load_config(p)
    paths = config.get("paths") or {}
    CONFIG_PATH = p
    RAW_DIR = REPO_ROOT / paths["raw"] if paths.get("raw") else DATA_DIR / "raw"
    INTERIM_DIR = REPO_ROOT / paths["interim"] if paths.get("interim") else DATA_DIR / "interim"
    return config


# A switched region takes its own folders as soon as the pipeline is imported.
if os.environ.get(CONFIG_ENV, "").strip():
    use_region()


def write_json(path: Path, obj) -> Path:
    """Write a derived file (data/interim/ and later). Never used for data/raw/."""
    if RAW_DIR.resolve() in path.resolve().parents:
        raise ValueError("derived files never go to data/raw/")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, ensure_ascii=False, default=str)
        f.write("\n")
    os.replace(tmp, path)
    return path

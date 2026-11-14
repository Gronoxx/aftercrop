# Written with an SI tool (Claude, Anthropic) for NASA Space Apps 2026. See AI_USE.md.
"""Build ``site/data/manifest.json``: every raw input and every cited source (plan 8.3, 8.6, M6, M9).

``sources`` has one entry per source id, in the shape of plan 8.6 (``source_id, title, url,
license, version, sha256, attribution``) plus ``kind``, ``cached``, ``n_files``,
``bytes`` and ``files_from``. ``files`` lists every file of ``data/raw/`` with the provenance
of its ``.meta.json`` (the contract of ``pipeline.adapters.base``: url, params, sha256,
bytes). For a source with several files, ``sha256`` is the digest of its sorted
``path<TAB>sha256`` lines. No date goes to the published files: the download time stays in the
local ``.meta.json`` (``retrieved_at``), and the manifest carries no build time and no commit.

Where each fact comes from (one owner each): titles and URLs of cited documents from
``pipeline.decision.strategies.SOURCES``, licenses of raw sources from
``config/region_norte_pr.yaml`` (``sources.*.license``), the rest from
``pipeline.ledger.registry.SOURCE_INFO``. A raw folder that no table declares is an error.

The build never touches the network and never writes to ``data/raw/``. Raw files are read
as bytes only. Two builds of the same cache give the same bytes (criterion M9.1).

Rehearsal (``--rehearsal``, SA-D63): both files are stamped ``run_mode: rehearsal``, neither
goes to site/data/, and the fictitious ``rehearsal_trials`` source is declared (``"fictitious": true``).

Run from the repository root:
    python -m pipeline.build.manifest [--raw data/raw] [--out site/data/manifest.json] [--no-verify]
                                      [--files-out site/data/manifest_files.json] [--rehearsal]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from pipeline.adapters import base
from pipeline.common import rehearsal as rh
from pipeline.ledger import registry as reg

ROOT = base.REPO_ROOT
OUT = ROOT / "site" / "data" / "manifest.json"
SCHEMA_VERSION = reg.SCHEMA_VERSION
IGNORED = {".DS_Store"}
# Read from each .meta.json. retrieved_at must be there (pipeline.adapters.base) but stays local.
META_KEYS = ("url", "params", "retrieved_at", "sha256", "bytes")
FILE_KEYS = ("url", "params", "sha256", "bytes")


class ManifestError(ValueError):
    def __init__(self, problems: Sequence[str]):
        self.problems = list(problems)
        super().__init__(f"{len(self.problems)} manifest problem(s): " + "; ".join(self.problems[:5]))


def bibliography() -> dict[str, Mapping[str, Any]]:
    """Titles and URLs of the documents the decision cites (owner: strategies.SOURCES)."""
    from pipeline.decision import strategies

    return dict(strategies.SOURCES)


# Raw files --------------------------------------------------------------------------------

def scan(raw_dir: Path, *, verify: bool = True) -> tuple[list[dict[str, Any]], list[str]]:
    """One entry per raw file, from its ``.meta.json``, and the problems found (criterion M1.2)."""
    raw_dir = Path(raw_dir)
    root = raw_dir.parent.parent
    entries, problems = [], []
    if not raw_dir.is_dir():
        return entries, [f"no raw folder at {raw_dir}"]
    paths = sorted(p for p in raw_dir.rglob("*") if p.is_file() and p.name not in IGNORED)
    metas = {p for p in paths if p.name.endswith(".meta.json")}
    for p in paths:
        rel = p.relative_to(root).as_posix()
        if p in metas:
            if not p.with_name(p.name[: -len(".meta.json")]).exists():
                problems.append(f"{rel}: .meta.json without its file")
            continue
        if p.name.endswith((".part", ".tmp")):
            problems.append(f"{rel}: unfinished download left behind")
            continue
        m = base.meta_path(p)
        if m not in metas:
            problems.append(f"{rel}: no .meta.json")
            continue
        info = json.loads(m.read_text(encoding="utf-8"))
        folder = p.relative_to(raw_dir).parts[0]
        absent = [k for k in ("source_id", *META_KEYS) if k not in info]
        if absent:
            problems.append(f"{rel}: .meta.json lacks {absent}")
            continue
        if info["source_id"] != folder:
            problems.append(f"{rel}: .meta.json says source {info['source_id']!r}, the folder is {folder!r}")
        if info["bytes"] != p.stat().st_size:
            problems.append(f"{rel}: size differs from .meta.json")
        elif verify and base.sha256_file(p) != info["sha256"]:
            problems.append(f"{rel}: sha256 differs from .meta.json")
        entries.append({"source_id": folder, "path": rel, **{k: info[k] for k in FILE_KEYS}})
    return entries, problems


def digest(files: Sequence[Mapping[str, Any]]) -> str | None:
    """sha256 of the sorted ``path<TAB>sha256`` lines; the file's own sha256 when there is one file."""
    if not files:
        return None
    if len(files) == 1:
        return files[0]["sha256"]
    lines = "".join(f"{f['path']}\t{f['sha256']}\n" for f in sorted(files, key=lambda f: f["path"]))
    return hashlib.sha256(lines.encode()).hexdigest()


# Sources ----------------------------------------------------------------------------------

def config_sources(config: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    """``sources.*`` of the config, keyed by their ``source_id``."""
    return {v["source_id"]: v for v in (config.get("sources") or {}).values()
            if isinstance(v, Mapping) and "source_id" in v}


def version_of(sid: str, cfg: Mapping[str, Any] | None, info: Mapping[str, Any]) -> str | None:
    """Version text, from the config where it holds the fact, else from the registry."""
    cfg = cfg or {}
    if sid == "hls" and cfg:
        colls = [c for prod in ("vi", "sr") for c in (cfg.get(prod) or {}).get("collections", [])]
        return ", ".join(f"{c['short_name']} {c['version']}" for c in colls) or None
    if sid == "hls_vi_v2" and cfg:
        return ", ".join(f"{c['short_name']} {c['version']}" for c in (cfg.get("vi") or {}).get("collections", []))
    if sid == "zarc_tabua" and cfg.get("tables"):
        return "Tábua de Risco, " + ", ".join(f"safra {t['safra']} ({t['role']})" for t in cfg["tables"])
    if sid == "power_daily" and cfg.get("community"):
        return f"POWER daily point API, community {cfg['community']}"
    return info.get("version")


CITED_ONLY = "cited by link only: no file cached, no text or number embedded beyond the citation"


def entry(sid: str, files: Sequence[Mapping[str, Any]], *, bib: Mapping[str, Any] | None,
          info: Mapping[str, Any], cfg: Mapping[str, Any] | None, files_from: str | None,
          fictitious: bool = False) -> dict[str, Any]:
    bib = bib or {}
    out = {
        "source_id": sid,
        "title": bib.get("title") or info.get("title"),
        "url": bib.get("url") or info.get("url") or (cfg or {}).get("url") or (cfg or {}).get("dataset"),
        "license": (cfg or {}).get("license") or info.get("license"),
        "version": version_of(sid, cfg, info),
        "sha256": digest(files),
        "attribution": info.get("attribution"),
        "kind": info.get("kind") or ("document" if bib else None),
        "cached": bool(files),
        "n_files": len(files),
        "bytes": sum(f["bytes"] for f in files),
        "files_from": files_from,
    }
    if fictitious:
        out["fictitious"] = True
    if not out["license"] and files_from and files_from in reg.SOURCE_INFO:
        # A document cut from a cached folder (subset source) takes the license of that folder.
        out["license"] = reg.SOURCE_INFO[files_from].get("license")
    if not out["license"] and not files and out["kind"] == "document":
        # A document cited by link, with nothing of it cached or embedded (M9.3, LICENSES.md).
        out["license"] = CITED_ONLY
    if not out["license"] and fictitious:
        out["license"] = "not applicable: fictitious values written for the example or the rehearsal"
    return out


def subset_files(files: Sequence[Mapping[str, Any]], parent: str, info: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    """Files of ``parent`` that belong to a subset source (``registry.SOURCE_INFO``). Pure.

    ``include`` is a regex on the path. With ``companion_dir``, a matching file inside a
    sub-folder of the source folder counts only when a file of that same folder matches
    ``companion_dir``: the HLS Fmask joins the VI subset only next to VI bands.
    """
    pat = re.compile(info["include"])
    comp = re.compile(info["companion_dir"]) if info.get("companion_dir") else None
    root = f"data/raw/{parent}"
    folders = {}
    for f in files:
        folders.setdefault(f["path"].rsplit("/", 1)[0], []).append(f["path"])
    out = []
    for f in files:
        if not pat.search(f["path"]):
            continue
        folder = f["path"].rsplit("/", 1)[0]
        if comp is not None and folder != root and not any(comp.search(p) for p in folders[folder]):
            continue
        out.append(f)
    return out


def build_sources(files: Sequence[Mapping[str, Any]], config: Mapping[str, Any],
                  extra_sources: Mapping[str, Mapping[str, Any]] | None = None) -> tuple[list[dict], list[str]]:
    problems = []
    bib = bibliography()
    cfgs = config_sources(config)
    by_source: dict[str, list[Mapping[str, Any]]] = {}
    for f in files:
        by_source.setdefault(f["source_id"], []).append(f)
    by_url: dict[str, list[Mapping[str, Any]]] = {}
    for f in files:
        by_url.setdefault(f["url"], []).append(f)

    out: dict[str, dict] = {}
    for sid, fs in by_source.items():
        if sid not in reg.SOURCE_INFO and sid not in bib:
            problems.append(f"data/raw/{sid}/: source not declared in registry.SOURCE_INFO or strategies.SOURCES")
            continue
        out[sid] = entry(sid, fs, bib=bib.get(sid), info=reg.SOURCE_INFO.get(sid, {}), cfg=cfgs.get(sid),
                         files_from=sid)
    for sid, info in reg.SOURCE_INFO.items():
        if sid in out:
            continue
        if info.get("subset_of"):
            parent = info["subset_of"]
            fs = subset_files(by_source.get(parent, []), parent, info)
            out[sid] = entry(sid, fs, bib=bib.get(sid), info=info, cfg=cfgs.get(parent), files_from=parent)
        else:   # declared, not cached yet (planned adapters)
            out[sid] = entry(sid, [], bib=bib.get(sid), info=info, cfg=cfgs.get(sid), files_from=None)
    for sid, b in bib.items():
        if sid in out:
            continue
        fs = by_url.get(b.get("url"), [])    # a cited document with a cached copy in data/raw/
        out[sid] = entry(sid, fs, bib=b, info=reg.SOURCE_INFO.get(sid, {}), cfg=None,
                         files_from=fs[0]["source_id"] if fs else None)
    for sid, x in (extra_sources or {}).items():
        if sid in out:
            problems.append(f"extra source {sid} already declared")
            continue
        out[sid] = entry(sid, [], bib=x, info=x, cfg=None, files_from=None, fictitious=bool(x.get("fictitious")))
    return [out[k] for k in sorted(out)], problems


# Build ------------------------------------------------------------------------------------

def build_manifest(raw_dir: Path = base.RAW_DIR, *, config: Mapping[str, Any] | None = None,
                   verify: bool = True,
                   extra_sources: Mapping[str, Mapping[str, Any]] | None = None,
                   include_files: bool = True) -> dict[str, Any]:
    """The manifest document. Raises ``ManifestError`` on any broken cache entry."""
    config = config if config is not None else base.load_config()
    files, problems = scan(raw_dir, verify=verify)
    sources, more = build_sources(files, config, extra_sources)
    problems += more
    if problems:
        raise ManifestError(problems)
    doc = {
        "schema_version": SCHEMA_VERSION,
        "sources": sources,
    }
    if include_files:
        doc["files"] = sorted(files, key=lambda f: f["path"])
    return doc


def dump(doc: Mapping[str, Any]) -> str:
    return json.dumps(doc, indent=1, ensure_ascii=False, allow_nan=False) + "\n"


def write(doc: Mapping[str, Any], path: Path = OUT) -> Path:
    path = Path(path)
    if base.RAW_DIR.resolve() in path.resolve().parents:
        raise ValueError("the manifest never goes to data/raw/")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(dump(doc), encoding="utf-8")
    tmp.replace(path)
    return path


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--raw", type=Path, default=base.RAW_DIR)
    ap.add_argument("--out", type=Path, default=OUT)
    ap.add_argument("--no-verify", action="store_true", help="skip the sha256 of every raw file")
    ap.add_argument("--files-out", type=Path, default=None,
                    help="write the per-file list to this file (e.g. site/data/manifest_files.json), not the manifest")
    ap.add_argument("--rehearsal", action="store_true",
                    help="stamp run_mode rehearsal, declare the fictitious rehearsal trials, never site/data (SA-D63)")
    a = ap.parse_args(argv)
    stamp = rh.stamped if a.rehearsal else dict
    try:
        if a.rehearsal:
            for p in (a.out, a.files_out):
                if p is not None:
                    try:
                        rh.check_out(p, "manifest")
                    except rh.RehearsalError as exc:
                        raise ManifestError([str(exc)]) from exc
        doc = build_manifest(a.raw, verify=not a.no_verify,
                             extra_sources={rh.TRIALS_SOURCE_ID: rh.TRIALS_SOURCE} if a.rehearsal else None)
    except ManifestError as exc:
        for p in exc.problems:
            print(f"manifest: {p}", file=sys.stderr)
        return 1
    if a.files_out:
        files = doc.pop("files")
        doc["files_in"] = a.files_out.name
        write(stamp({"schema_version": SCHEMA_VERSION, "files": files}), a.files_out)
    doc = stamp(doc)
    write(doc, a.out)
    no_license = [s["source_id"] for s in doc["sources"] if not s["license"]]
    if no_license:
        print(f"manifest: warning: no license recorded for {no_license} (criterion M9.3)", file=sys.stderr)
    print(f"manifest: {len(doc['sources'])} sources -> {a.out}" + (f", files -> {a.files_out}" if a.files_out else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())

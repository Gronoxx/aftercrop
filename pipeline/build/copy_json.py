# Written with an SI tool (Claude, Anthropic) for NASA Space Apps 2026. See AI_USE.md.
"""Build site/data/copy.json from config/copy_en.yaml and config/copy_pt.yaml (plan SA-D59, 8.6).

The page never hard-codes user-facing text. Every string comes from the two YAML files,
which carry the same keys (tests/test_copy.py). This step only converts them: it adds no
text, drops no key and writes no timestamp, so the output is the same for the same input.

Run from the repository root:
    .venv/bin/python -m pipeline.build.copy_json            # writes site/data/copy.json
    .venv/bin/python -m pipeline.build.copy_json --check    # exit 1 if the file is stale
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence

import yaml

from pipeline.build import notice

ROOT = Path(__file__).resolve().parents[2]
SOURCES = {"en": ROOT / "config" / "copy_en.yaml", "pt": ROOT / "config" / "copy_pt.yaml"}
OUT = ROOT / "site" / "data" / "copy.json"
# Copy of the observation regions (SA-D79), kept apart so site/data/copy.json stays as frozen.
# app.mjs loads it from data_regions/copy.json and merges it under the key "regions".
REGION_SOURCES = {"en": ROOT / "config" / "copy_regions_en.yaml", "pt": ROOT / "config" / "copy_regions_pt.yaml"}
REGION_OUT = ROOT / "site" / "data_regions" / "copy.json"
SCHEMA_VERSION = "1"
DEFAULT_LANG = "en"


class CopyError(ValueError):
    """The two copy files cannot be served as they are."""


def leaves(node: Any, path: tuple[str, ...] = ()) -> Iterator[tuple[str, Any]]:
    """(dotted key, value) for every leaf. List items count by index."""
    if isinstance(node, dict):
        for key, value in node.items():
            yield from leaves(value, path + (str(key),))
    elif isinstance(node, list):
        for i, value in enumerate(node):
            yield from leaves(value, path + (str(i),))
    else:
        yield ".".join(path), node


def check_trees(trees: Mapping[str, Any]) -> None:
    """Same keys in every language and a non-empty string at every leaf."""
    keysets = {lang: dict(leaves(tree)) for lang, tree in trees.items()}
    langs = list(keysets)
    base = set(keysets[langs[0]])
    for lang in langs[1:]:
        other = set(keysets[lang])
        if base != other:
            raise CopyError(
                f"keys differ: only in {langs[0]}: {sorted(base - other)[:10]}, "
                f"only in {lang}: {sorted(other - base)[:10]}")
    for lang, flat in keysets.items():
        bad = [k for k, v in flat.items() if not isinstance(v, str) or not v.strip()]
        if bad:
            raise CopyError(f"{lang}: not a non-empty string at {bad[:10]}")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_copy(sources: Mapping[str, Path] = SOURCES) -> dict:
    """The copy.json document. Raises CopyError if the files do not match."""
    trees = {}
    for lang, path in sources.items():
        tree = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(tree, dict):
            raise CopyError(f"{path}: top level must be a mapping")
        trees[lang] = tree
    check_trees(trees)
    if DEFAULT_LANG not in trees:
        raise CopyError(f"default language {DEFAULT_LANG!r} missing")
    return {
        notice.KEY: notice.TEXT,          # M9.4. The page reads langs only
        "schema_version": SCHEMA_VERSION,
        "default_lang": DEFAULT_LANG,
        "langs": trees,
        "sources": {
            lang: {"path": path.relative_to(ROOT).as_posix() if path.is_relative_to(ROOT) else path.name,
                   "sha256": sha256(path)}
            for lang, path in sources.items()
        },
    }


def render(doc: Mapping[str, Any]) -> str:
    return json.dumps(doc, ensure_ascii=False, indent=1) + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true", help="exit 1 if the output file is missing or stale")
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--regions", action="store_true",
                    help="build site/data_regions/copy.json from config/copy_regions_*.yaml instead")
    args = ap.parse_args(argv)
    args.out = args.out or (REGION_OUT if args.regions else OUT)
    try:
        text = render(build_copy(REGION_SOURCES if args.regions else SOURCES))
    except CopyError as exc:
        print(f"copy_json: {exc}", file=sys.stderr)
        return 2
    if args.check:
        current = args.out.read_text(encoding="utf-8") if args.out.exists() else None
        if current != text:
            print(f"copy_json: {args.out} is stale, run python -m pipeline.build.copy_json", file=sys.stderr)
            return 1
        return 0
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(text, encoding="utf-8")
    print(f"copy_json: wrote {args.out.relative_to(ROOT) if args.out.is_relative_to(ROOT) else args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

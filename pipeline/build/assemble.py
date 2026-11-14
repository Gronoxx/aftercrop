# Written with an SI tool (Claude, Anthropic) for NASA Space Apps 2026. See AI_USE.md.
"""Check ``site/data/`` before it is published (plan 8.4 step 16, criteria M6.1 to M6.5, M9.1).

"Número sem registro para o build": this step exits 1 when

- a file of ``site/data/`` has no ``schema_version``, or an M5 intermediate (``*_m5.json``)
  sits in the public folder;
- the public folder holds the fictitious example: ``ledger.json`` labelled "example,
  fictitious" in ``site/data/``, which the page reads as real (precompute refuses to write
  it there; this catches a copy by hand);
- a screen number is neither a Num ``{"v", "l"}`` nor inside an object with a ``ledger`` id
  (M6.1), or names an id that ``ledger.json`` does not hold, or shows a value different from
  its record;
- a ledger record is incomplete, has a wrong enum or a source missing from
  ``manifest.json`` (M6.3), or breaks a class rule (M6.4);
- a ``source_id`` named in a screen document is not in the manifest;
- a file carries a date or a commit of the work (SA-D104): a key of ``WORK_DATE_KEYS`` anywhere,
  or an ``at`` time on a late note. The download times stay in the local ``.meta.json`` files;
- a file lacks the SI notice ``"_notice"`` (M9.4, ``pipeline.build.notice``), required in the
  public folder and by the command line;
- with ``--dom``, a rendered page ``dom_*.html`` shows text with a digit outside
  ``data-ledger``, ``data-manifest`` and the fixed copy (M6.2), or a ``data-ledger`` id has
  no record (M6.5).

It writes only the SI notice: the command line first adds ``"_notice"`` to every JSON of the
folder that lacks it, keeping each file's indent, and names the files it rewrote
(``--check-only`` skips that; a folder under ``tests/`` is never rewritten). ``--digests`` prints the sha256 of every file without ``built_at``, the
number ``make repro-check`` compares (M9.1).

Rehearsal (``--rehearsal``, SA-D63): the folder must lie outside site/data/. The command line
also stamps ``"run_mode": "rehearsal"`` on every JSON that lacks it (the scoreboard, which the
bench writes unstamped), names them, and then every file must carry the stamp. Without the
flag a stamped file is a problem: a rehearsal folder never ships as the product.

Run from the repository root:
    python -m pipeline.build.assemble [--site-data site/data] [--dom build] [--digests] [--check-only]
    python -m pipeline.build.assemble --rehearsal --site-data data/rehearsal/site_data
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

import yaml

from pipeline.build import notice
from pipeline.common import rehearsal as rh
from pipeline.ledger import build as lb
from pipeline.ledger import registry as reg

ROOT = lb.ROOT
SITE_DATA = lb.SITE_DATA
DOM_DIR = ROOT / "build"
# Keys that record when the work happened or which commit it ran on (SA-D104). None is published.
WORK_DATE_KEYS = frozenset({"retrieved_at", "retrieved", "built_at", "code_commit", "frozen_at",
                            "protocol_commit", "run_at", "checked_at"})
COPY_FILES = (ROOT / "config" / "copy_en.yaml", ROOT / "config" / "copy_pt.yaml")
SOURCE_KEYS = ("source_id", "source_ids")
DIGIT = re.compile(r"\d")
EXAMPLE_LABEL = "example, fictitious"      # pipeline.decision.precompute.EXAMPLE
PLACEHOLDER = re.compile(r"\{[^{}]*\}")


# site/data ----------------------------------------------------------------------------------

def doc_sources(doc: Any) -> set[str]:
    """Every source id a screen document names under ``source_id`` or ``source_ids``."""
    out: set[str] = set()
    if isinstance(doc, Mapping):
        for k, v in doc.items():
            if k in SOURCE_KEYS:
                out.update(lb.source_list(v))
            else:
                out |= doc_sources(v)
    elif isinstance(doc, list):
        for v in doc:
            out |= doc_sources(v)
    return out


def check_site(site_dir: Path = SITE_DATA, *, dom: Sequence[Path] = (),
               copy_files: Sequence[Path] = COPY_FILES, public: bool | None = None,
               require_notice: bool | None = None, rehearsal: bool = False) -> list[str]:
    """Every problem of ``site_dir`` (and of the rendered pages ``dom``). Empty when it can ship.

    ``public`` (default: ``site_dir`` is site/data/) refuses the fictitious example there.
    ``require_notice`` (default: ``public``) asks every file for the SI notice (M9.4).
    ``rehearsal``: every file must be stamped ``run_mode: rehearsal`` and the folder lie outside
    site/data/. Otherwise a stamped file is a problem.
    """
    site_dir = Path(site_dir)
    if public is None:
        public = site_dir.resolve() == Path(SITE_DATA).resolve()
    if require_notice is None:
        require_notice = public
    problems: list[str] = []
    paths = sorted(site_dir.glob("*.json"))
    if not paths:
        return [f"{site_dir}: no JSON file"]
    raw: dict[str, Any] = {}
    for p in paths:
        if reg.INTERMEDIATE_RE.match(p.name):
            problems.append(f"{p.name}: M5 intermediate in the public folder, write it to data/build/")
            continue
        try:
            raw[p.name] = lb.load_json(p)
        except json.JSONDecodeError as exc:
            problems.append(f"{p.name}: not JSON ({exc})")
            continue
        d = raw[p.name]
        if not isinstance(d, Mapping) or d.get("schema_version") in (None, ""):
            problems.append(f"{p.name}: no schema_version")
        if require_notice and not notice.has_notice(d):
            problems.append(f"{p.name}: no SI notice ({notice.KEY!r}), run python -m pipeline.build.assemble")
        if rehearsal and not rh.is_rehearsal(d):
            problems.append(f"{p.name}: not stamped {rh.MODE_KEY}={rh.REHEARSAL!r} in a rehearsal folder")
        if not rehearsal and rh.is_rehearsal(d):
            problems.append(f"{p.name}: a rehearsal document (fictitious trials), never part of the product. "
                            "Check the folder with --rehearsal")
    if rehearsal and rh.inside_site_data(site_dir, SITE_DATA):
        problems.append(f"{site_dir}: a rehearsal folder never lies inside site/data/ (SA-D63)")
    for meta in ("ledger.json", "manifest.json"):
        if meta not in raw:
            problems.append(f"{meta}: missing from {site_dir}")
    ledger = raw.get("ledger.json") or {}
    manifest = raw.get("manifest.json") or {}
    records = ledger.get("records") if isinstance(ledger.get("records"), Mapping) else {}
    if "ledger.json" in raw and not records:
        problems.append("ledger.json: no records")
    if public and isinstance(ledger, Mapping) and ledger.get("label") == EXAMPLE_LABEL:
        problems.append(f"ledger.json: labelled {EXAMPLE_LABEL!r} in the public folder, which the page reads as "
                        "real data. Open the example with ?data=<folder>")
    sources = lb.manifest_index(manifest)
    for i, s in enumerate(manifest.get("sources", []) if isinstance(manifest, Mapping) else []):
        absent = [k for k in ("source_id", "title", "url", "license", "version", "sha256",
                              "attribution") if k not in s]
        if absent:
            problems.append(f"manifest.json: sources[{i}] lacks {absent}")

    for name, d in raw.items():
        for path in work_dates(d):
            problems.append(f"{name}: {path} records a date or commit of the work (SA-D104)")

    docs = {n: d for n, d in raw.items() if n not in reg.META_DOCS}
    for name, doc in docs.items():
        for path in lb.loose_numbers(reg.explicit_view(name, doc)):
            problems.append(f"{name}: number without a ledger record at {path}")
        for r in lb.references(name, doc):
            rec = records.get(r.id)
            if rec is None:
                problems.append(f"{name}: {r.path} names {r.id}, which is not in ledger.json")
            elif r.kind == "num" and lb.is_number(rec.get("value")) and rec["value"] != r.v:
                problems.append(f"{name}: {r.path} shows {r.v}, the record {r.id} holds {rec['value']}")
        for sid in sorted(doc_sources(doc) - set(sources)):
            problems.append(f"{name}: source {sid} is not in manifest.json")
    if records:
        problems += lb.validate_records(records, manifest)
        problems += lb.check_classes(records, docs)
    if dom:
        allow = CopyAllowlist.from_files(copy_files)
        for p in dom:
            problems += [f"{Path(p).name}: {x}" for x in
                         check_dom(Path(p).read_text(encoding="utf-8"), set(records), set(sources), allow)]
    return problems


# Rendered pages (M6.2, M6.5) -------------------------------------------------------------------

def _norm(text: str) -> str:
    return " ".join(text.split())


def copy_leaves(node: Any, path: tuple[str, ...] = ()) -> Iterable[tuple[str, str]]:
    if isinstance(node, Mapping):
        for k, v in node.items():
            yield from copy_leaves(v, path + (str(k),))
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from copy_leaves(v, path + (str(i),))
    elif isinstance(node, str):
        yield ".".join(path), node


@dataclass
class CopyAllowlist:
    """The fixed copy of M6.2: texts written in config/copy_*.yaml before any result.

    A text node passes when it equals a piece of a copy string between placeholders
    (``{N}`` and the like), or, inside ``data-copy="<key>"``, when it is part of the copy
    string of that key.
    """

    segments: set[str] = field(default_factory=set)
    by_key: dict[str, list[str]] = field(default_factory=dict)

    @classmethod
    def from_trees(cls, trees: Iterable[Any]) -> CopyAllowlist:
        out = cls()
        for tree in trees:
            for key, s in copy_leaves(tree):
                text = _norm(s)
                out.by_key.setdefault(key, []).append(text)
                for seg in PLACEHOLDER.split(text):
                    seg = seg.strip()
                    if DIGIT.search(seg):
                        out.segments.add(seg)
        return out

    @classmethod
    def from_files(cls, paths: Sequence[Path]) -> CopyAllowlist:
        trees = []
        for p in paths:
            with open(p, encoding="utf-8") as f:
                trees.append(yaml.safe_load(f))
        return cls.from_trees(trees)

    def allows(self, text: str, key: str | None) -> bool:
        if key is not None:
            return any(text in PLACEHOLDER.sub(" ", s) or text in s for s in self.by_key.get(key, []))
        return text in self.segments


VOID = frozenset({"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source",
                  "track", "wbr"})
SKIP = frozenset({"script", "style", "template"})
TEXT_ATTRS = ("aria-label", "title", "alt", "placeholder")


class _DomCheck(HTMLParser):
    def __init__(self, ledger_ids: set[str], manifest_ids: set[str], allow: CopyAllowlist):
        super().__init__(convert_charrefs=True)
        self.ledger_ids, self.manifest_ids, self.allow = ledger_ids, manifest_ids, allow
        self.stack: list[tuple[str, bool, str | None, bool]] = []   # tag, covered, copy key, skip
        self.problems: list[str] = []
        self.used: list[str] = []

    def _state(self) -> tuple[bool, str | None, bool]:
        if not self.stack:
            return False, None, False
        _, covered, key, skip = self.stack[-1]
        return covered, key, skip

    def _text(self, text: str, covered: bool, key: str | None, where: str) -> None:
        text = _norm(text)
        if covered or not DIGIT.search(text):
            return
        if not DIGIT.search(reg.IDENTIFIER_TOKENS.sub("", text)):
            return
        if self.allow.allows(text, key):
            return
        line, _ = self.getpos()
        self.problems.append(f"line {line}: {where} {text!r} has a digit outside data-ledger and the fixed copy")

    def _open(self, tag: str, attrs: list[tuple[str, str | None]]) -> tuple[bool, str | None, bool]:
        a = dict(attrs)
        covered, key, skip = self._state()
        line, _ = self.getpos()
        lid = a.get("data-ledger")
        if lid is not None:
            lid = lid.strip()
            self.used.append(lid)
            if not lid:
                self.problems.append(f"line {line}: empty data-ledger on <{tag}>")
            elif lid not in self.ledger_ids:
                self.problems.append(f"line {line}: data-ledger {lid!r} has no record in ledger.json")
            covered = True
        mid = a.get("data-manifest")
        if mid is not None:
            if mid.strip() not in self.manifest_ids:
                self.problems.append(f"line {line}: data-manifest {mid!r} is not in manifest.json")
            covered = True
        if a.get("data-copy"):
            key = a["data-copy"].strip()
            if key not in self.allow.by_key:
                self.problems.append(f"line {line}: data-copy {key!r} is not a copy key")
        skip = skip or tag in SKIP
        if not skip:
            for name in TEXT_ATTRS + (("value",) if tag == "input" else ()):
                if a.get(name):
                    self._text(a[name], covered, key, f"<{tag} {name}>")
        return covered, key, skip

    def handle_starttag(self, tag, attrs):
        covered, key, skip = self._open(tag, attrs)
        if tag not in VOID:
            self.stack.append((tag, covered, key, skip))

    def handle_startendtag(self, tag, attrs):
        self._open(tag, attrs)

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                del self.stack[i:]
                return

    def handle_data(self, data):
        covered, key, skip = self._state()
        if not skip:
            self._text(data, covered, key, "text")


def check_dom(html: str, ledger_ids: set[str], manifest_ids: set[str], allow: CopyAllowlist) -> list[str]:
    """Problems of one rendered page (M6.2 and M6.5)."""
    p = _DomCheck(ledger_ids, manifest_ids, allow)
    p.feed(html)
    p.close()
    return p.problems


def dom_ledger_ids(html: str) -> list[str]:
    """Every ``data-ledger`` value of a page, in order."""
    p = _DomCheck(set(), set(), CopyAllowlist())
    p.feed(html)
    p.close()
    return p.used


def dom_pages(dom_dir: Path = DOM_DIR) -> list[Path]:
    return sorted(Path(dom_dir).glob("dom_*.html"))


def work_dates(obj: Any, path: str = "") -> list[str]:
    """Paths of every ``WORK_DATE_KEYS`` key, and of every ``at`` of a late note. Pure."""
    out: list[str] = []
    if isinstance(obj, Mapping):
        for k, v in obj.items():
            here = f"{path}.{k}" if path else str(k)
            if k in WORK_DATE_KEYS:
                out.append(here)
            elif k == "late_notes" and isinstance(v, list):
                out += [f"{here}[{i}].at" for i, n in enumerate(v) if isinstance(n, Mapping) and "at" in n]
            out += work_dates(v, here)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            out += work_dates(v, f"{path}[{i}]")
    return out


# Reproducibility (M9.1) ---------------------------------------------------------------------

def without_built_at(obj: Any) -> Any:
    if isinstance(obj, Mapping):
        return {k: without_built_at(v) for k, v in obj.items() if k != "built_at"}
    if isinstance(obj, list):
        return [without_built_at(v) for v in obj]
    return obj


def canonical_digest(path: Path) -> str:
    """sha256 of a JSON file with every ``built_at`` removed, keys sorted."""
    obj = without_built_at(lb.load_json(path))
    text = json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(text.encode()).hexdigest()


def digests(site_dir: Path = SITE_DATA) -> dict[str, str]:
    return {p.name: canonical_digest(p) for p in sorted(Path(site_dir).glob("*.json"))}


def add_notice(site_dir: Path = SITE_DATA) -> list[str]:
    """Add the SI notice to every JSON of ``site_dir`` that lacks it. Names of the files rewritten.

    Never under tests/: fixtures stay as their producer wrote them.
    """
    site_dir = Path(site_dir).resolve()
    if site_dir.is_relative_to(ROOT / "tests"):
        return []
    return [p.name for p in sorted(site_dir.glob("*.json"))
            if not reg.INTERMEDIATE_RE.match(p.name) and notice.stamp_file(p)]


def add_rehearsal_stamp(site_dir: Path) -> list[str]:
    """Stamp ``run_mode: rehearsal`` on every JSON object of ``site_dir`` that lacks it, keeping its indent.

    Refused inside site/data/ and under tests/. Names of the files rewritten.
    """
    site_dir = Path(site_dir).resolve()
    rh.check_out(site_dir, "site folder", SITE_DATA)
    if site_dir.is_relative_to(ROOT / "tests"):
        return []
    out = []
    for p in sorted(site_dir.glob("*.json")):
        if reg.INTERMEDIATE_RE.match(p.name):
            continue
        text = p.read_text(encoding="utf-8")
        try:
            doc = json.loads(text)
        except json.JSONDecodeError:
            continue
        if not isinstance(doc, Mapping) or rh.is_rehearsal(doc):
            continue
        indent = notice._indent_of(text)
        body = json.dumps(rh.stamped(doc), ensure_ascii=False, indent=indent, allow_nan=False)
        p.write_text(body + ("\n" if indent is not None or text.endswith("\n") else ""), encoding="utf-8")
        out.append(p.name)
    return out


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--site-data", type=Path, default=SITE_DATA)
    ap.add_argument("--dom", type=Path, default=None, help="folder with the rendered pages dom_*.html")
    ap.add_argument("--digests", action="store_true", help="print the sha256 of each file without built_at")
    ap.add_argument("--check-only", action="store_true", help="do not add the SI notice, only check it")
    ap.add_argument("--rehearsal", action="store_true",
                    help="rehearsal folder (outside site/data): stamp and require run_mode rehearsal (SA-D63)")
    a = ap.parse_args(argv)
    if a.rehearsal:
        try:
            if not a.check_only:
                stamped = add_rehearsal_stamp(a.site_data)
                if stamped:
                    print(f"assemble: rehearsal stamp added to {', '.join(stamped)}", file=sys.stderr)
            else:
                rh.check_out(a.site_data, "site folder", SITE_DATA)
        except rh.RehearsalError as exc:
            print(f"assemble: {exc}", file=sys.stderr)
            return 1
    if not a.check_only:
        rewritten = add_notice(a.site_data)
        if rewritten:
            print(f"assemble: SI notice added to {', '.join(rewritten)}", file=sys.stderr)
    pages = dom_pages(a.dom) if a.dom else []
    if a.dom and not pages:
        print(f"assemble: no dom_*.html in {a.dom}", file=sys.stderr)
        return 1
    problems = check_site(a.site_data, dom=pages, require_notice=True, rehearsal=a.rehearsal)
    for p in problems:
        print(f"assemble: {p}", file=sys.stderr)
    if a.digests:
        print(json.dumps(digests(a.site_data), indent=1, sort_keys=True))
    if problems:
        print(f"assemble: {len(problems)} problem(s), not publishable", file=sys.stderr)
        return 1
    print(f"assemble: {a.site_data} ok ({len(pages)} rendered page(s) checked)",
          file=sys.stderr if a.digests else sys.stdout)
    return 0


if __name__ == "__main__":
    sys.exit(main())

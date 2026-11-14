# Written with an SI tool (Claude, Anthropic) for NASA Space Apps 2026. See AI_USE.md.
"""Build and validate ``site/data/ledger.json`` (plan 8.3, 8.6, sheet M6, criteria M6.1 to M6.5).

Inputs, all files (plan 8.3, "Comunicação: só por arquivo"):

- the screen documents of ``site/data/`` (M4 and M5): every Num ``{"v", "l"}`` and every
  ``ledger`` key names a record. The scoreboard's implicit ids come from
  ``registry.explicit_view``;
- ``ledger_m5.json``, the records ``pipeline.decision.precompute`` writes for its own ids;
- ``site/data/manifest.json`` (``pipeline.build.manifest``): source versions and dates;
- ``pipeline.ledger.registry``: the declaration of every id.

Each record has the ten fields of 8.6 plus ``id``, ``seal`` (the 7.14 name of its
evidence class, from config/copy_en.yaml) and ``owner``. The build fails, and writes
nothing, when an id is undeclared, a field is empty, an enum is wrong, a source is not in
the manifest, or a class breaks the rules of M6.4. A record says where a number came
from; it does not prove the number right (sheet M6, "Limitações").

Rehearsal (``--rehearsal``, SA-D63): the M5 ledger must be stamped ``run_mode: rehearsal``,
``ledger.json`` keeps the stamp, and neither the site folder nor the output lies in site/data/.
Without the flag a stamped M5 ledger is refused.

Run from the repository root:
    python -m pipeline.ledger.build [--site-data site/data] [--m5-ledger data/build/ledger_m5.json]
                                    [--manifest site/data/manifest.json] [--out site/data/ledger.json] [--strict]
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from pipeline.common import rehearsal as rh
from pipeline.ledger import registry as reg

ROOT = Path(__file__).resolve().parents[2]
SITE_DATA = ROOT / "site" / "data"
M5_LEDGER = ROOT / "data" / "build" / "ledger_m5.json"
COPY_EN = ROOT / "config" / "copy_en.yaml"
SCHEMA_VERSION = reg.SCHEMA_VERSION
TEXT_FIELDS = ("unit", "transform", "method_id", "data_version", "limits")


class LedgerError(ValueError):
    """The ledger cannot be built or does not cover the site. ``problems`` lists every cause."""

    def __init__(self, problems: Sequence[str]):
        self.problems = list(problems)
        head = "; ".join(self.problems[:5])
        more = f" (and {len(self.problems) - 5} more)" if len(self.problems) > 5 else ""
        super().__init__(f"{len(self.problems)} ledger problem(s): {head}{more}")


# Reading documents -------------------------------------------------------------------------

def load_json(path: Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def site_docs(site_dir: Path) -> dict[str, Any]:
    """Screen documents of ``site_dir``: every ``*.json`` except the meta files and M5 intermediates."""
    out = {}
    for p in sorted(Path(site_dir).glob("*.json")):
        if p.name in reg.META_DOCS or reg.INTERMEDIATE_RE.match(p.name):
            continue
        out[p.name] = load_json(p)
    return out


def is_number(x: Any) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def is_num(x: Any) -> bool:
    """A ``Num`` of plan 8.6: exactly ``v`` (number or null) and ``l`` (ledger id)."""
    return (isinstance(x, Mapping) and set(x) == {"v", "l"} and isinstance(x["l"], str)
            and (x["v"] is None or is_number(x["v"])))


@dataclass(frozen=True)
class Ref:
    """One place a document names a ledger id."""

    id: str
    doc: str
    path: str
    kind: str                         # "num" or "block"
    v: Any = None                     # the Num value
    parent: Mapping[str, Any] | None = None   # the object that holds the Num or the ledger key


def references(name: str, doc: Any) -> list[Ref]:
    """Every ledger id ``doc`` names, in document order, implicit ids included."""
    out: list[Ref] = []

    def walk(node: Any, path: str, parent: Mapping[str, Any] | None) -> None:
        if isinstance(node, Mapping):
            if is_num(node):
                out.append(Ref(node["l"], name, path, "num", node["v"], parent))
                return
            for k, v in node.items():
                sub = f"{path}.{k}" if path else str(k)
                if k == "ledger" and isinstance(v, str):
                    out.append(Ref(v, name, sub, "block", None, node))
                else:
                    walk(v, sub, node)
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, f"{path}[{i}]", parent)

    walk(reg.explicit_view(name, doc), "", None)
    return out


def loose_numbers(doc: Any, path: str = "", covered: bool = False) -> list[str]:
    """Paths of numbers that are neither a Num nor inside an object with a ``ledger`` id (M6.1)."""
    if isinstance(doc, bool) or doc is None or isinstance(doc, str):
        return []
    if is_number(doc):
        return [] if covered else [path]
    if isinstance(doc, Mapping):
        if is_num(doc):
            return []
        here = covered or isinstance(doc.get("ledger"), str)
        out = []
        for k, v in doc.items():
            if k in reg.NOT_SCREEN_KEYS:
                continue
            out += loose_numbers(v, f"{path}.{k}" if path else str(k), here)
        return out
    if isinstance(doc, list):
        out = []
        for i, v in enumerate(doc):
            out += loose_numbers(v, f"{path}[{i}]", covered)
        return out
    return [path]


# Manifest and sources -----------------------------------------------------------------------

def source_list(value: Any) -> list[str]:
    """``"a,b"`` -> ``["a", "b"]``; None -> []."""
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [str(x).strip() for x in value if str(x).strip()]
    return [x.strip() for x in str(value).split(",") if x.strip()]


def manifest_index(manifest: Mapping[str, Any] | None) -> dict[str, Mapping[str, Any]]:
    if not manifest:
        return {}
    return {s["source_id"]: s for s in manifest.get("sources", []) if isinstance(s, Mapping) and "source_id" in s}


def compose_data_version(sids: Sequence[str], sources: Mapping[str, Mapping[str, Any]]) -> str | None:
    """'HLSL30_VI 2.0, HLSS30_VI 2.0' from the manifest versions, with no date. None if a source is absent."""
    parts = []
    for sid in sids:
        s = sources.get(sid)
        if s is None:
            return None
        parts.append(str(s.get("version") or s.get("title") or sid))
    return "; ".join(parts) if parts else None


def seal_names(copy_path: Path = COPY_EN) -> dict[str, str]:
    """The 7.14 seal names by evidence class, from the copy file (the owner of screen text)."""
    with open(copy_path, encoding="utf-8") as f:
        copy = yaml.safe_load(f)
    return dict(copy["seals"]["names"])


# Building records ---------------------------------------------------------------------------

def _filled(x: Any) -> bool:
    return x is not None and not (isinstance(x, str) and not x.strip())


def make_record(rid: str, tpl: reg.Template, m5: Mapping[str, Any] | None, refs: Sequence[Ref],
                sources: Mapping[str, Mapping[str, Any]], built: Mapping[str, Mapping[str, Any]],
                seals: Mapping[str, str], problems: list[str]) -> dict[str, Any]:
    """One record: the M5 fields where set, then the template, then the class defaults."""
    rec: dict[str, Any] = {k: None for k in reg.RECORD_FIELDS}
    if m5 is not None:
        rec.update({k: m5.get(k) for k in reg.RECORD_FIELDS})
        if tpl.require_class and tpl.evidence_class and m5.get("evidence_class") != tpl.evidence_class:
            problems.append(f"{rid}: evidence_class {m5.get('evidence_class')!r}, the registry requires "
                            f"{tpl.evidence_class!r}")
    for k, v in tpl.defaults().items():
        if not _filled(rec.get(k)):
            rec[k] = json.loads(json.dumps(v)) if isinstance(v, Mapping) else v
    if rid in reg.TRANSFORM_OVERRIDES:
        rec["transform"] = reg.TRANSFORM_OVERRIDES[rid]

    # Value: the M5 record's, else the Num on screen. One id, one value.
    nums = [r for r in refs if r.kind == "num"]
    vs = {json.dumps(r.v) for r in nums}
    if len(vs) > 1:
        problems.append(f"{rid}: one id, different values on screen: {sorted(vs)}")
    if m5 is not None:
        if is_number(m5.get("value")):
            for r in nums:
                if r.v != m5["value"]:
                    problems.append(f"{rid}: {r.doc} {r.path} shows {r.v}, the record holds {m5['value']}")
    elif nums:
        rec["value"] = nums[0].v

    # Uncertainty bounds from the object that holds the Num (green_end low, high).
    if tpl.uncertainty_from and nums and nums[0].parent is not None:
        lo, hi = tpl.uncertainty_from
        rec["uncertainty"] = {**(rec.get("uncertainty") or {}), "low": nums[0].parent.get(lo),
                              "high": nums[0].parent.get(hi)}

    if tpl.inherit:
        src_id = tpl.inherit.format(*rid.split("."))
        src = built.get(src_id)
        if src is None:
            problems.append(f"{rid}: shares its source with {src_id}, which is not in the ledger")
        else:
            for k in ("source_id", "data_version"):
                if not _filled(rec.get(k)):
                    rec[k] = src.get(k)

    if not _filled(rec.get("limits")) and rec.get("evidence_class") in reg.CLASS_LIMITS:
        rec["limits"] = reg.CLASS_LIMITS[rec["evidence_class"]]
    if not _filled(rec.get("data_version")):
        rec["data_version"] = compose_data_version(source_list(rec.get("source_id")), sources)

    seal = seals.get(rec.get("evidence_class"))
    if rec.get("evidence_class") in reg.EVIDENCE_CLASSES and not seal:
        problems.append(f"{rid}: no seal name for {rec.get('evidence_class')!r} in the copy")
    return {"id": rid, **{k: rec[k] for k in reg.RECORD_FIELDS}, "seal": seal, "owner": tpl.owner}


def build_ledger(docs: Mapping[str, Any], m5_ledger: Mapping[str, Any] | None, manifest: Mapping[str, Any] | None,
                 *, seals: Mapping[str, str] | None = None) -> dict[str, Any]:
    """The ``ledger.json`` document. Raises ``LedgerError`` listing every problem."""
    problems: list[str] = []
    seals = dict(seals if seals is not None else seal_names())
    sources = manifest_index(manifest)
    m5_records = dict((m5_ledger or {}).get("records") or {})
    for rid, r in m5_records.items():
        if not isinstance(r, Mapping) or r.get("id") != rid:
            problems.append(f"ledger_m5: record {rid} is not an object with the same id")
        else:
            missing = [k for k in reg.RECORD_FIELDS if k not in r]
            if missing:
                problems.append(f"ledger_m5: record {rid} lacks {missing}")

    refs: dict[str, list[Ref]] = {}
    for name, doc in docs.items():
        for r in references(name, doc):
            refs.setdefault(r.id, []).append(r)

    ids = set(m5_records) | set(refs) | set(reg.STATIC_IDS)
    templates: dict[str, reg.Template] = {}
    for rid in sorted(ids):
        if not reg.ID_RE.match(rid):
            problems.append(f"{rid}: not a ledger id (scheme <document>.<block>[.<item>])")
            continue
        tpl = reg.template_for(rid)
        if tpl is None:
            where = ", ".join(f"{r.doc} {r.path}" for r in refs.get(rid, [])[:3]) or "ledger_m5"
            problems.append(f"{rid}: undeclared in pipeline.ledger.registry (named in {where})")
            continue
        templates[rid] = tpl

    built: dict[str, dict[str, Any]] = {}
    for rid in sorted(templates, key=lambda i: (templates[i].inherit is not None, i)):
        built[rid] = make_record(rid, templates[rid], m5_records.get(rid), refs.get(rid, []), sources, built,
                                 seals, problems)
    records = {rid: built[rid] for rid in sorted(built)}
    problems += validate_records(records, manifest)
    problems += check_classes(records, docs)
    if problems:
        raise LedgerError(problems)
    doc: dict[str, Any] = {"schema_version": SCHEMA_VERSION}
    if (m5_ledger or {}).get("label"):
        doc["label"] = m5_ledger["label"]
    if rh.is_rehearsal(m5_ledger):
        doc = rh.stamped(doc)
    doc["records"] = records
    return doc


# Validation (M6.3, M6.4) ----------------------------------------------------------------------

def _json_value_ok(x: Any) -> bool:
    if x is None or isinstance(x, (str, bool)):
        return True
    if is_number(x):
        return math.isfinite(x)
    if isinstance(x, Mapping):
        return all(isinstance(k, str) and _json_value_ok(v) for k, v in x.items())
    if isinstance(x, list):
        return all(_json_value_ok(v) for v in x)
    return False


def validate_record(rec: Mapping[str, Any], source_ids: set[str]) -> list[str]:
    """Problems of one record: ten fields filled, enums valid, sources in the manifest (M6.3)."""
    rid = rec.get("id")
    out = []
    if not isinstance(rid, str) or not reg.ID_RE.match(rid):
        out.append(f"{rid!r}: bad id")
    missing = [k for k in reg.RECORD_FIELDS if k not in rec]
    if missing:
        return out + [f"{rid}: missing fields {missing}"]
    if rec["evidence_class"] not in reg.EVIDENCE_CLASSES:
        out.append(f"{rid}: evidence_class {rec['evidence_class']!r} not in {reg.EVIDENCE_CLASSES}")
    if rec["validation_state"] not in reg.VALIDATION_STATES:
        out.append(f"{rid}: validation_state {rec['validation_state']!r} not in {reg.VALIDATION_STATES}")
    for k in TEXT_FIELDS:
        if not (isinstance(rec[k], str) and rec[k].strip()):
            out.append(f"{rid}: {k} is empty")
    u = rec["uncertainty"]
    if not isinstance(u, Mapping) or u.get("kind") not in reg.UNCERTAINTY_KINDS:
        out.append(f"{rid}: uncertainty must be an object with kind in {reg.UNCERTAINTY_KINDS}")
    elif u["kind"] == "event_interval" and not {"low", "high"} <= set(u):
        out.append(f"{rid}: an event interval needs low and high")
    if not _json_value_ok(rec["value"]):
        out.append(f"{rid}: value is not a finite JSON value")
    sids = source_list(rec["source_id"])
    if not sids:
        if rec["evidence_class"] not in reg.SOURCELESS_CLASSES:
            out.append(f"{rid}: source_id is empty for class {rec['evidence_class']!r}")
    else:
        absent = [s for s in sids if s not in source_ids]
        if absent:
            out.append(f"{rid}: source_id {absent} not in the manifest")
    return out


def validate_records(records: Mapping[str, Mapping[str, Any]], manifest: Mapping[str, Any] | None) -> list[str]:
    ids = set(manifest_index(manifest))
    out = []
    for rid, rec in records.items():
        if rec.get("id") != rid:
            out.append(f"{rid}: the record says id {rec.get('id')!r}")
        out += validate_record(rec, ids)
    return out


def check_classes(records: Mapping[str, Mapping[str, Any]], docs: Mapping[str, Any] | None = None) -> list[str]:
    """Rules of criterion M6.4 (plan 7.14, 10.1)."""
    out = []
    for rid, rec in records.items():
        cls = rec.get("evidence_class")
        sids = source_list(rec.get("source_id"))
        if cls == "seen_by_satellite" and not any(p.match(rid) for p in reg.SEEN_BY_SATELLITE_IDS):
            out.append(f"{rid}: seen_by_satellite is only for reflectance and NDVI")
        if reg.POWER_SOURCE in sids and cls != "reanalysis" and not reg.IMERG_IDS.search(rid):
            out.append(f"{rid}: a NASA POWER value is reanalysis, not {cls}")
        if any(p.match(rid) for p in reg.DERIVED_FROM_TRIALS_IDS) and cls != "derived_from_trials":
            out.append(f"{rid}: deltas, their mean and counts N of n are derived_from_trials, not {cls}")
        if cls == "simulated" and any(u in str(rec.get("unit") or "") for u in reg.SIMULATED_FORBIDDEN_UNITS):
            out.append(f"{rid}: a simulated value never shows money, a percentage or a yield ({rec.get('unit')})")
    # Step tables count site-seasons at or above each gain: a team calculation (10.1).
    trials = (docs or {}).get("trials_steps.json")
    if isinstance(trials, Mapping):
        for opt, e in (trials.get("options") or {}).items():
            if isinstance(e, Mapping) and e.get("steps"):
                rec = records.get(e.get("ledger"))
                if rec is not None and rec.get("evidence_class") != "derived_from_trials":
                    out.append(f"{e.get('ledger')}: the steps of {opt} are counts N of n, derived_from_trials, "
                               f"not {rec.get('evidence_class')}")
    return out


# Transforms ---------------------------------------------------------------------------------

_JS_DIRS = ("site/js", "pipeline/decision")


def resolve_transform(transform: str, root: Path = ROOT) -> bool:
    """True when the code path names an existing file, module or function (static, no import)."""
    if not isinstance(transform, str) or not transform:
        return False
    if ":" in transform:                                   # decision.mjs:computeG3
        name, fn = transform.split(":", 1)
        pat = re.compile(rf"\bfunction\s+{re.escape(fn)}\b|\bconst\s+{re.escape(fn)}\s*=")
        return any((root / d / name).is_file() and pat.search((root / d / name).read_text(encoding="utf-8"))
                   for d in _JS_DIRS)
    if "/" in transform:                                   # pipeline/decision/run_states.mjs
        return (root / transform).is_file()
    parts = transform.split(".")
    for i in range(len(parts), 0, -1):
        mod = root.joinpath(*parts[:i])
        f = mod.with_suffix(".py") if not mod.is_dir() else mod / "__init__.py"
        if f.is_file():
            rest = parts[i:]
            if not rest:
                return True
            if len(rest) == 1:
                return re.search(rf"^(def|class)\s+{re.escape(rest[0])}\b|^{re.escape(rest[0])}\s*=",
                                 f.read_text(encoding="utf-8"), re.M) is not None
            return False
    return False


def unresolved_transforms(records: Mapping[str, Mapping[str, Any]], root: Path = ROOT) -> list[str]:
    """Records whose transform names no code yet: M2 modules not written, or a typo."""
    cache: dict[str, bool] = {}
    out = []
    for rid, rec in records.items():
        t = rec.get("transform")
        if t not in cache:
            cache[t] = resolve_transform(t, root)
        if not cache[t]:
            out.append(f"{rid}: transform {t!r} not found")
    return out


# Write and CLI ------------------------------------------------------------------------------

def dump(doc: Mapping[str, Any]) -> str:
    return json.dumps(doc, indent=1, ensure_ascii=False, allow_nan=False) + "\n"


def write(doc: Mapping[str, Any], path: Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(dump(doc), encoding="utf-8")
    tmp.replace(path)
    return path


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--site-data", type=Path, default=SITE_DATA)
    ap.add_argument("--m5-ledger", type=Path, default=M5_LEDGER)
    ap.add_argument("--manifest", type=Path, default=None, help="default: <site-data>/manifest.json")
    ap.add_argument("--out", type=Path, default=None, help="default: <site-data>/ledger.json")
    ap.add_argument("--copy", type=Path, default=COPY_EN)
    ap.add_argument("--strict", action="store_true", help="a transform that names no code is an error")
    ap.add_argument("--rehearsal", action="store_true",
                    help="stamped M5 ledger only, ledger.json stamped, never in site/data (SA-D63)")
    a = ap.parse_args(argv)
    manifest_path = a.manifest or a.site_data / "manifest.json"
    out = a.out or a.site_data / "ledger.json"
    try:
        if a.rehearsal:
            for what, p in (("site folder", a.site_data), ("ledger", out)):
                try:
                    rh.check_out(p, what)
                except rh.RehearsalError as exc:
                    raise LedgerError([str(exc)]) from exc
        if not manifest_path.is_file():
            raise LedgerError([f"no manifest at {manifest_path}: run python -m pipeline.build.manifest"])
        if not a.m5_ledger.is_file():
            raise LedgerError([f"no M5 ledger at {a.m5_ledger}: run the decision step (make decide)"])
        m5 = load_json(a.m5_ledger)
        try:
            rh.check_mode(m5, a.rehearsal, f"the M5 ledger {a.m5_ledger}")
        except rh.RehearsalError as exc:
            raise LedgerError([str(exc)]) from exc
        doc = build_ledger(site_docs(a.site_data), m5, load_json(manifest_path), seals=seal_names(a.copy))
        missing_code = unresolved_transforms(doc["records"])
        if missing_code and a.strict:
            raise LedgerError(missing_code)
    except LedgerError as exc:
        for p in exc.problems:
            print(f"ledger: {p}", file=sys.stderr)
        return 1
    for w in missing_code:
        print(f"ledger: warning: {w}", file=sys.stderr)
    write(doc, out)
    owners: dict[str, int] = {}
    for r in doc["records"].values():
        owners[r["owner"]] = owners.get(r["owner"], 0) + 1
    print(f"ledger: {len(doc['records'])} records ({', '.join(f'{k} {v}' for k, v in sorted(owners.items()))}) -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

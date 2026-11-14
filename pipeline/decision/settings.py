# Written with an SI tool (Claude, Anthropic) for NASA Space Apps 2026. See AI_USE.md.
"""Loader of ``config/decision_settings.yaml``: the values the protocol does not fix, each with its source.

``pipeline.decision.base_file --settings`` reads the result. The YAML holds one entry per value:

- ``{value: <v>, source: "<where it comes from>"}``, or
- ``{value: null, removed: "<why there is no source>"}``: the variable goes (protocol.unsourced_rules,
  SA-D68). The two risk counts stay removed: protocol.climate (SA-D72.5) takes their place.

Top keys: ``cycles`` (one entry per crop), ``occupancy`` (one entry per strategy), ``risk`` (one
entry per count), ``example_context``, ``example_constraints`` (entries) and ``region`` (``pack``,
``price_k``, ``example_numbers``, entries). The loader returns the plain shape base_file
expects, plus ``sources``: the source or removal text of every value, by dotted key.

A ``.json`` file is read as before, already in the plain shape (no source check).
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml

from pipeline.decision import strategies as st

DEFAULT_PATH = Path(__file__).resolve().parents[2] / "config" / "decision_settings.yaml"
SCHEMA_VERSION = 1
GROUPS = {"cycles": ("corn", "corn_grass", "wheat"), "occupancy": ("S1", "S2", "S3"),
          "risk": ("dry_autumn", "cold_in_window"), "region": ("pack", "price_k", "example_numbers")}
ENTRIES = ("example_context", "example_constraints")
# protocol.unsourced_rules: these may be removed, and need a threshold when present.
REMOVABLE = {"cycles.corn", "cycles.corn_grass", "cycles.wheat", "occupancy.S1", "occupancy.S2", "occupancy.S3",
             "risk.dry_autumn", "risk.cold_in_window"}


class SettingsError(ValueError):
    pass


def _need(cond: bool, msg: str) -> None:
    if not cond:
        raise SettingsError(msg)


def entry(node: Any, key: str, sources: dict[str, str]) -> Any:
    """The value of one ``{value, source}`` or ``{value: null, removed}`` entry. Records its text."""
    _need(isinstance(node, Mapping) and "value" in node, f"{key}: an entry is {{value, source}} or "
                                                         "{value: null, removed}")
    extra = sorted(set(node) - {"value", "source", "removed"})
    _need(not extra, f"{key}: unknown keys {extra}")
    has_src, has_rm = "source" in node, "removed" in node
    _need(has_src != has_rm, f"{key}: give exactly one of source or removed")
    text = node["source"] if has_src else node["removed"]
    _need(isinstance(text, str) and text.strip(), f"{key}: the source or removal text is empty")
    if has_rm:
        _need(key in REMOVABLE, f"{key}: this value cannot be removed")
        _need(node["value"] is None, f"{key}: a removed value must be null")
        sources[key] = "removed: " + text.strip()
    else:
        _need(node["value"] is not None, f"{key}: a sourced value cannot be null (use removed)")
        sources[key] = text.strip()
    return node["value"]


def _mmdd(x: Any, where: str) -> None:
    _need(isinstance(x, str) and len(x) == 5 and x[2] == "-", f"{where} must be MM-DD, got {x!r}")


def check_occupancy(sid: str, spec: Any) -> None:
    where = f"occupancy.{sid}"
    _need(isinstance(spec, Mapping) and isinstance(spec.get("phases"), list) and spec["phases"],
          f"{where}: {{source_id, phases: [...]}}")
    for i, p in enumerate(spec["phases"]):
        _need(isinstance(p, Mapping) and p.get("crop") in st.CROPS, f"{where}.phases[{i}]: unknown crop")
        _mmdd(p.get("start"), f"{where}.phases[{i}].start")
        _mmdd(p.get("end"), f"{where}.phases[{i}].end")
        src = p.get("source_id", spec.get("source_id"))
        _need(isinstance(src, str) and src in st.SOURCES,
              f"{where}.phases[{i}]: source_id {src!r} is not in strategies.SOURCES")


def check_risk(key: str, v: Any) -> None:
    where = f"risk.{key}"
    _need(isinstance(v, Mapping), f"{where} must be an object")
    span = v.get("span")
    _need(isinstance(span, list) and len(span) == 2, f"{where}.span: [MM-DD, MM-DD]")
    for x in span:
        _mmdd(x, f"{where}.span")
    if key == "dry_autumn":
        t = v.get("threshold_mm")
        _need(isinstance(t, (int, float)) and not isinstance(t, bool) and t > 0, f"{where}.threshold_mm")


def parse(doc: Any) -> dict:
    """The plain settings of base_file from the sourced YAML document. Pure."""
    _need(isinstance(doc, Mapping), "the settings must be a mapping")
    _need(doc.get("schema_version") == SCHEMA_VERSION, f"schema_version must be {SCHEMA_VERSION}")
    known = {"schema_version", *GROUPS, *ENTRIES}
    extra = sorted(set(doc) - known)
    _need(not extra, f"unknown top keys {extra}")
    sources: dict[str, str] = {}
    out: dict[str, Any] = {}
    for g, keys in GROUPS.items():
        node = doc.get(g)
        _need(isinstance(node, Mapping), f"{g} is missing")
        _need(set(node) == set(keys), f"{g} needs exactly {list(keys)}, got {sorted(node)}")
        out[g] = {k: entry(node[k], f"{g}.{k}", sources) for k in keys}
    for k in ENTRIES:
        _need(k in doc, f"{k} is missing")
        out[k] = entry(doc[k], k, sources)
    for sid, spec in out["occupancy"].items():
        _need(spec is not None, f"occupancy.{sid}: the calendar has no source, so strategy {sid} cannot be shown")
        check_occupancy(sid, spec)
    for k, v in out["risk"].items():
        if v is not None:
            check_risk(k, v)
    _need(all(c is None or isinstance(c, str) for c in out["cycles"].values()), "cycles: a group name or null")
    out["sources"] = sources
    return out


def load(path: Path = DEFAULT_PATH) -> dict:
    """Settings from a sourced ``.yaml`` (checked) or a plain ``.json`` (as is)."""
    path = Path(path)
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".json":
        return json.loads(text)
    return parse(yaml.safe_load(text))

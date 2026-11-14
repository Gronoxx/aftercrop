# Written with an SI tool (Claude, Anthropic) for NASA Space Apps 2026. See AI_USE.md.
"""The SI notice as metadata of every generated JSON of ``site/data/`` (plan M9.4, AI_USE.md).

Each file carries a top-level ``"_notice"`` string. The page never reads it (app.mjs reads
named keys only). ``copy_json`` writes it into copy.json. ``assemble`` adds it to the other
files of ``site/data/`` that lack it, then checks that every file holds it.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

KEY = "_notice"
TEXT = "Written with an SI tool (Claude, Anthropic) for NASA Space Apps 2026. See AI_USE.md."
_INDENT = re.compile(r"\n( +)\S")


def stamped(doc: Mapping[str, Any]) -> dict[str, Any]:
    """``doc`` with the notice as its first key (any older notice replaced)."""
    return {KEY: TEXT, **{k: v for k, v in doc.items() if k != KEY}}


def has_notice(doc: Any) -> bool:
    return isinstance(doc, Mapping) and doc.get(KEY) == TEXT


def _indent_of(text: str) -> int | None:
    """Indent of a JSON text as its writer left it: None when written on one line."""
    m = _INDENT.search(text)
    return len(m.group(1)) if m else None


def stamp_file(path: Path) -> bool:
    """Add the notice to one JSON file, keeping its indent. True when the file was rewritten.

    A file that is not a JSON object is left alone (assemble reports it).
    """
    path = Path(path)
    text = path.read_text(encoding="utf-8")
    try:
        doc = json.loads(text)
    except json.JSONDecodeError:
        return False
    if not isinstance(doc, Mapping) or has_notice(doc):
        return False
    indent = _indent_of(text)
    out = json.dumps(stamped(doc), ensure_ascii=False, indent=indent, allow_nan=False)
    if indent is not None or text.endswith("\n"):
        out += "\n"
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(out, encoding="utf-8")
    tmp.replace(path)
    return True

# Written with an SI tool (Claude, Anthropic) for NASA Space Apps 2026. See AI_USE.md.
"""Rehearsal mode (SA-D63, plan P5): real NASA data on the development strip, fictitious trials.

The rehearsal chain runs end to end only on fields of the development strip (east of
``protocol.field.development_strip.lon_min``), with the fictitious trial gains of
``tests/fixtures/decision_base_trials_example.json``. Every CLI of the chain takes
``--rehearsal`` (base_file, field_inputs, precompute, ledger.build, manifest, assemble). In
that mode:

- every document carries ``"run_mode": "rehearsal"`` and the ``run_note`` below;
- nothing is written into ``site/data/``, the folder the page reads as the product;
- the trials are the fictitious gains, never the transcription (``data/manual/``).

Product mode refuses any input stamped as a rehearsal, so fictitious trials never reach a
product run. This module holds the one definition of the stamp and of the folder rule.

Make uses it to guard the copy steps:
    python -m pipeline.common.rehearsal check-out <folder>
"""

from __future__ import annotations

import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
SITE_DATA = REPO_ROOT / "site" / "data"
DEFAULT_OUT = REPO_ROOT / "data" / "rehearsal" / "site_data"

MODE_KEY = "run_mode"
NOTE_KEY = "run_note"
REHEARSAL = "rehearsal"
NOTE = "Rehearsal: real NASA data on a development strip, fictitious trials"
# Source id of the fictitious gains in a rehearsal (trials_steps.json source_ids, manifest entry).
TRIALS_SOURCE_ID = "rehearsal_trials"
TRIALS_SOURCE = {
    "title": "Rehearsal trial gains, fictitious (tests/fixtures/decision_base_trials_example.json)",
    "kind": "document",
    "fictitious": True,
}


class RehearsalError(ValueError):
    pass


def is_rehearsal(doc: Any) -> bool:
    """True when ``doc`` is stamped as a rehearsal document."""
    return isinstance(doc, Mapping) and doc.get(MODE_KEY) == REHEARSAL


def stamped(doc: Mapping[str, Any]) -> dict[str, Any]:
    """``doc`` with the rehearsal stamp, after ``_notice``, ``schema_version`` and ``label`` when present."""
    head = [k for k in ("_notice", "schema_version", "label") if k in doc]
    out = {k: doc[k] for k in head}
    out[MODE_KEY] = REHEARSAL
    out[NOTE_KEY] = NOTE
    out.update({k: v for k, v in doc.items() if k not in out})
    return out


def inside_site_data(path: Path, site_data: Path = SITE_DATA) -> bool:
    p, f = Path(path).resolve(), Path(site_data).resolve()
    return p == f or f in p.parents


def check_out(path: Path, what: str = "output", site_data: Path = SITE_DATA) -> None:
    """A rehearsal never writes into site/data/. Raises ``RehearsalError``."""
    if inside_site_data(path, site_data):
        raise RehearsalError(f"rehearsal {what} {path} lies inside site/data/, which the page reads as the product. "
                             f"Write it outside, for example {DEFAULT_OUT.relative_to(REPO_ROOT)}/ (SA-D63)")


def check_mode(doc: Any, rehearsal: bool, where: str) -> None:
    """The document's stamp must match the run: rehearsal needs it, product refuses it."""
    if rehearsal and not is_rehearsal(doc):
        raise RehearsalError(f"{where} is not stamped {MODE_KEY}={REHEARSAL!r}: a rehearsal run reads only "
                             "rehearsal inputs (run the earlier step with --rehearsal)")
    if not rehearsal and is_rehearsal(doc):
        raise RehearsalError(f"{where} is a rehearsal document (fictitious trials): product mode refuses it. "
                             "Use --rehearsal and a folder outside site/data/")


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 2 or args[0] != "check-out":
        print("usage: python -m pipeline.common.rehearsal check-out <folder>", file=sys.stderr)
        return 2
    try:
        check_out(Path(args[1]))
    except RehearsalError as exc:
        print(f"rehearsal: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())

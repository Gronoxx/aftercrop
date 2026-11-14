# Written with an SI tool (Claude, Anthropic) for NASA Space Apps 2026. See AI_USE.md.
"""The label table, computed by code (plan, section 11.6 and sheet M4).

Nobody writes the label: it follows from the statuses of L0, T0, T1, P1 and A1
and from the number of site-seasons, in the order of ``protocol.label.order``
(``ORDER`` below, checked against the block by the tests). L0 comes first: an L0
fail blocks reading any delta and gives "Model not validated here". The texts are those of sections 7.15 and 11.6,
written before any result. When ``config/copy_en.yaml`` exists, it becomes the
source of the texts and must carry the same five ids.

Task statuses:

- "not_completed": infrastructure failure (code error, missing file, time out)
  or the hard stop. Never counts as a failure.
- "not_run": planned, not run (shown as "planned, not run").
- Engine outcomes (zeroed yield, no emergence, dead canopy) are results and
  count against the model: they never turn into "not_completed".
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from pipeline.bench import common
from pipeline.bench.common import FAIL, NOT_COMPLETED, NOT_RUN, PASS, STATUSES

NOT_COMPLETED_LABEL = "not_completed"
MODEL_NOT_VALIDATED = "model_not_validated_here"
DIRECTION_NOT_VALIDATED = "direction_not_validated"
MECHANISM_NOT_SHOWN = "direction_matched_mechanism_not_shown"
DIRECTION_SUPPORTED = "direction_supported_exploratory"
LABEL_IDS = (NOT_COMPLETED_LABEL, MODEL_NOT_VALIDATED, DIRECTION_NOT_VALIDATED,
             MECHANISM_NOT_SHOWN, DIRECTION_SUPPORTED)

LABEL_TITLE = {
    NOT_COMPLETED_LABEL: "Not completed",
    MODEL_NOT_VALIDATED: "Model not validated here",
    DIRECTION_NOT_VALIDATED: "Direction not validated",
    MECHANISM_NOT_SHOWN: "Direction matched, mechanism not shown",
    DIRECTION_SUPPORTED: "Direction supported in 10 trial site-seasons (exploratory, not blind)",
}

LABEL_MESSAGE = {
    NOT_COMPLETED_LABEL: "The test bench did not finish before the freeze. No result is implied.",
    MODEL_NOT_VALIDATED: (
        "The model did not pass its check against the field trials. Results from the crop "
        "sequence stay on How we know. This page uses satellite history, trials, rules and "
        "your numbers."   # SA-D84: one reason, without the "or"
    ),
    DIRECTION_NOT_VALIDATED: (
        "The model passed its reference check but did not reproduce the difference between wheat "
        "and corn as the previous crop. That means not shown, not disproved. Water at sowing is "
        "simulated, not independently checked."
    ),
    MECHANISM_NOT_SHOWN: (
        "The model followed the trials, but it did not pass the wrong-year test or the run "
        "without carried-over water, or one of them did not finish. We cannot say water "
        "explains it."
    ),
    DIRECTION_SUPPORTED: (
        "The model followed the trials and passed both controls. This holds for wheat on part, "
        "in this region only. It says nothing about how much you would gain."
    ),
}

# protocol.label.order, word for word ("when", label id). label() follows it.
ORDER = (
    ("L0 fails", MODEL_NOT_VALIDATED),
    ("T0 not_run or not_completed", NOT_COMPLETED_LABEL),
    ("T0 fails or fewer than 10 site-seasons", MODEL_NOT_VALIDATED),
    ("T1 not_run or not_completed", NOT_COMPLETED_LABEL),
    ("T1 fails", DIRECTION_NOT_VALIDATED),
    ("P1 or A1 fails", MECHANISM_NOT_SHOWN),
    ("T0, T1, P1 and A1 pass", DIRECTION_SUPPORTED),
)

PAGE_PREFIX = ("Crop-sequence model check (wheat option, water only): {label}. "
               "The answer above does not depend on it.")

STATUS_TEXT = {
    "pass": "pass",
    "fail": "fail",
    NOT_RUN: "planned, not run",
    NOT_COMPLETED: "not completed",
    "exploratory": "exploratory",
    "reported": "reported",
}


def _check_status(name: str, status: str) -> None:
    if status not in STATUSES:
        raise ValueError(f"{name} status must be one of {STATUSES}, got {status!r}")


def label(t0: str, t1: str, p1: str, a1: str, n_site_seasons: int, *, n_required: int,
          l0: str = NOT_RUN) -> str:
    """Label id from the statuses, in the order of ``ORDER`` (protocol.label.order).

    ``n_required`` is the protocol's ``min_site_seasons_for_label`` (10); fewer
    completed site-seasons gives "Model not validated here". ``l0`` is the L0
    status: only an L0 fail changes the label (first row). L0 not run or not
    completed leaves the label to the other rows.
    """
    for name, status in (("L0", l0), ("T0", t0), ("T1", t1), ("P1", p1), ("A1", a1)):
        _check_status(name, status)
    if isinstance(n_site_seasons, bool) or not isinstance(n_site_seasons, int) or n_site_seasons < 0:
        raise ValueError(f"n_site_seasons must be a non-negative integer, got {n_site_seasons!r}")
    if l0 == FAIL:
        return MODEL_NOT_VALIDATED
    if t0 in (NOT_RUN, NOT_COMPLETED):
        return NOT_COMPLETED_LABEL
    if t0 != PASS or n_site_seasons < n_required:
        return MODEL_NOT_VALIDATED
    if t1 in (NOT_RUN, NOT_COMPLETED):
        return NOT_COMPLETED_LABEL
    if t1 != PASS:
        return DIRECTION_NOT_VALIDATED
    if p1 != PASS or a1 != PASS:
        return MECHANISM_NOT_SHOWN
    return DIRECTION_SUPPORTED


def label_from_protocol(t0: str, t1: str, p1: str, a1: str, n_site_seasons: int,
                        protocol: Mapping[str, Any], *, l0: str = NOT_RUN) -> str:
    return label(t0, t1, p1, a1, n_site_seasons, n_required=common.site_seasons_required(protocol), l0=l0)


def order_matches(protocol: Mapping[str, Any]) -> bool:
    """True when ``protocol.label.order`` is ``ORDER`` (conditions and label texts)."""
    block = protocol.get("protocol", protocol) if isinstance(protocol, Mapping) else {}
    rows = ((block.get("label") or {}).get("order")) or []
    got = [(str(r.get("when")), str(r.get("label")).lower()) for r in rows]
    return got == [(w, LABEL_TITLE[i].lower()) for w, i in ORDER]


def label_text(label_id: str) -> str:
    return LABEL_TITLE[label_id]


def page_line(label_id: str) -> str:
    return PAGE_PREFIX.format(label=LABEL_TITLE[label_id])


def status_text(status: str) -> str:
    _check_status("task", status)
    return STATUS_TEXT[status]


@dataclass(frozen=True)
class TaskOutcome:
    status: str
    result: Any = None
    error: str | None = None


def run_guarded(task: Callable[..., Any], *args: Any, **kwargs: Any) -> TaskOutcome:
    """Run one bench task. Any exception is an infrastructure failure and gives
    "not_completed", never "fail". A result with a ``status`` keeps its status
    (engine outcomes count against the model inside the result)."""
    try:
        result = task(*args, **kwargs)
    except Exception as exc:  # code error, missing file, time out
        return TaskOutcome(status=NOT_COMPLETED, error=f"{type(exc).__name__}: {exc}")
    status = getattr(result, "status", None)
    if status is None and isinstance(result, Mapping):
        status = result.get("status")
    if status not in STATUSES:  # a code error, not a result
        return TaskOutcome(status=NOT_COMPLETED, result=result, error=f"task returned status {status!r}")
    return TaskOutcome(status=status, result=result)

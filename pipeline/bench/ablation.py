# Written with an SI tool (Claude, Anthropic) for NASA Space Apps 2026. See AI_USE.md.
"""A2 and A3: how much the answer depends on one source (plan, section 11.2).

A2, rain. The L2 counts made with ``PRECTOTCORR`` are made again with
``IMERG_PRECTOT``, only in the years whose window lies wholly inside the common
period (``protocol.dates.a2_common_period``, IMERG from 1998, Final V07 to
09/2025), so a difference never comes from the period. Listed years and the
counts that change are reported, with no threshold (``tests.A2``). A2 is not a
key of the NASA switch and counts no states. Only counts that read rain belong
here (dry autumn); the cold count reads temperature and does not change.

A3, HLS. The states of every field and option, with and without HLS, on the
fictional example in the three price scenarios (the state depends on price, so
the count reads as a sensitivity). Zero changes gives "context only"
(``tests.A3.zero_changes``), whatever the source. The states come from
``run_states.mjs``; this module only counts them. A3 measures dependence, not
accuracy (section 11.8).

Both report and neither passes nor fails: a completed run has status
"reported" (common.REPORTED).
"""

from __future__ import annotations

from collections.abc import Hashable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from pipeline.bench import common
from pipeline.bench.common import REPORTED

CONTEXT_ONLY = "context_only"
CHANGES_ANSWERS = "changes_answers"
VERDICT_NOT_RUN = "not_run"


# A2 ------------------------------------------------------------------------------

@dataclass(frozen=True)
class A2Rule:
    common_period: tuple[date, date]

    @classmethod
    def from_protocol(cls, protocol: Mapping[str, Any]) -> "A2Rule":
        """Reads ``protocol.tests.A2`` (no threshold, reported only) and the period."""
        sec = common.protocol_section(protocol, "A2")
        if sec.get("threshold", "none") not in ("none", None):
            raise common.ProtocolError("protocol.tests.A2.threshold must be 'none'")
        if sec.get("role") != "reported_only":
            raise common.ProtocolError("protocol.tests.A2.role must be 'reported_only'")
        dates = common.protocol_block(protocol).get("dates")
        if not isinstance(dates, Mapping) or "a2_common_period" not in dates:
            raise common.ProtocolError("protocol.dates.a2_common_period is missing")
        try:
            first, last = (date.fromisoformat(str(x)) for x in dates["a2_common_period"])
        except (TypeError, ValueError) as exc:
            raise common.ProtocolError(f"protocol.dates.a2_common_period not understood: {exc}") from exc
        if last < first:
            raise common.ProtocolError("protocol.dates.a2_common_period ends before it starts")
        return cls(common_period=(first, last))


@dataclass(frozen=True)
class L2Count:
    """One L2 count, year by year, with each rain.

    ``main`` and ``check`` say whether the year counts (for example, a dry
    autumn) with ``PRECTOTCORR`` and with ``IMERG_PRECTOT``. ``windows`` gives
    the first and last day the count reads in each year.
    """

    id: str
    main: Mapping[int, bool]
    check: Mapping[int, bool]
    windows: Mapping[int, tuple[date, date]]


@dataclass(frozen=True)
class A2CountResult:
    id: str
    years: list[int]               # compared: both rains, window inside the period
    years_main: list[int]
    years_check: list[int]
    years_left_out: list[int]

    @property
    def m(self) -> int:
        return len(self.years)

    @property
    def only_main(self) -> list[int]:
        return sorted(set(self.years_main) - set(self.years_check))

    @property
    def only_check(self) -> list[int]:
        return sorted(set(self.years_check) - set(self.years_main))

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "m": self.m,
            "n_main": len(self.years_main),
            "n_check": len(self.years_check),
            "n_changed": len(self.only_main) + len(self.only_check),
            "years_main": list(self.years_main),
            "years_check": list(self.years_check),
            "only_main": self.only_main,
            "only_check": self.only_check,
            "years_left_out": list(self.years_left_out),
        }


@dataclass(frozen=True)
class A2Result:
    status: str
    common_period: tuple[date, date]
    counts: list[A2CountResult]

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": "A2",
            "status": self.status,
            "statistic": "l2_counts_prectotcorr_against_imerg",
            "main": "PRECTOTCORR",
            "check": "IMERG_PRECTOT",
            "common_period": [d.isoformat() for d in self.common_period],
            "n": max((c.m for c in self.counts), default=0),
            "threshold": None,
            "counts": [c.to_dict() for c in self.counts],
        }


def _flag(v: Any, what: str) -> bool:
    if not isinstance(v, bool):
        raise common.IncompleteInput(f"{what} must be true or false, got {v!r}")
    return v


def evaluate_a2(counts: Sequence[L2Count], rule: A2Rule) -> A2Result:
    if not counts:
        raise ValueError("A2 needs at least one L2 count")
    common.check_keys([(c.id, "") for c in counts])
    first, last = rule.common_period
    out = []
    for c in counts:
        years, left_out = [], []
        for y in sorted(set(c.main) | set(c.check)):
            window = c.windows.get(y)
            if y in c.main and y in c.check and window is not None and first <= window[0] <= window[1] <= last:
                years.append(y)
            else:
                left_out.append(y)
        out.append(A2CountResult(
            id=c.id,
            years=years,
            years_main=[y for y in years if _flag(c.main[y], f"{c.id} {y} main")],
            years_check=[y for y in years if _flag(c.check[y], f"{c.id} {y} check")],
            years_left_out=left_out,
        ))
    if not any(r.m for r in out):
        raise ValueError("A2 has no year with both rains inside the common period")
    return A2Result(status=REPORTED, common_period=rule.common_period, counts=out)


# A3 ------------------------------------------------------------------------------

@dataclass(frozen=True)
class A3Rule:
    zero_changes: str

    @classmethod
    def from_protocol(cls, protocol: Mapping[str, Any]) -> "A3Rule":
        sec = common.protocol_section(protocol, "A3")
        text = common.require(sec, "zero_changes", "tests.A3")
        if text != "context only":
            raise common.ProtocolError("protocol.tests.A3.zero_changes must be 'context only'")
        return cls(zero_changes=text)


@dataclass(frozen=True)
class A3Result:
    status: str
    source: str
    swap: str
    scenarios: list[str]
    states_total: int
    states_changed: int
    by_scenario: dict[str, dict[str, int]]
    changed: list[dict[str, Any]] = field(default_factory=list)

    @property
    def verdict(self) -> str:
        return CONTEXT_ONLY if self.states_changed == 0 else CHANGES_ANSWERS

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": "A3",
            "status": self.status,
            "statistic": "states_changed_without_source",
            "source": self.source,
            "swap": self.swap,
            "scenarios": list(self.scenarios),
            "n": self.states_total,
            "states_changed": self.states_changed,
            "states_total": self.states_total,
            "verdict": self.verdict,
            "by_scenario": {k: dict(v) for k, v in self.by_scenario.items()},
            "changed": list(self.changed),
        }

    def ablation_source(self) -> dict[str, Any]:
        """One entry of ``sources`` in ``site/data/ablation.json`` (section 8.6)."""
        return {"id": self.source, "swap": self.swap, "status": self.status,
                "states_changed": self.states_changed, "states_total": self.states_total,
                "verdict": self.verdict}


def not_run_source(source: str, swap: str) -> dict[str, Any]:
    """An ablation source that was planned and not run."""
    return {"id": source, "swap": swap, "status": common.NOT_RUN,
            "states_changed": None, "states_total": None, "verdict": VERDICT_NOT_RUN}


def evaluate_a3(
    with_source: Mapping[str, Mapping[Hashable, str]],
    without_source: Mapping[str, Mapping[Hashable, str]],
    rule: A3Rule,
    *,
    source: str = "hls_vi_v2",
    swap: str = "without HLS: no window warning and no field order",
    expected_scenarios: Sequence[str] | None = None,
) -> A3Result:
    """Count the field and option states that change when the source is removed.

    Each mapping goes scenario -> {(field, option): state}. Both sides must hold
    the same scenarios and the same states. The field order is not a state: it
    disappears without HLS and is not counted.
    """
    del rule  # the rule fixes the wording of zero changes; the count needs nothing else
    if set(with_source) != set(without_source):
        raise ValueError("with and without the source must cover the same scenarios")
    if not with_source:
        raise ValueError("A3 needs at least one scenario")
    if expected_scenarios is not None and list(with_source) != list(expected_scenarios):
        raise ValueError(f"A3 scenarios {list(with_source)} differ from {list(expected_scenarios)}")
    total = changed = 0
    by_scenario, details = {}, []
    for sc in with_source:
        a, b = with_source[sc], without_source[sc]
        if set(a) != set(b):
            raise ValueError(f"scenario {sc}: with and without the source hold different states")
        if not a:
            raise ValueError(f"scenario {sc} has no state")
        n_changed = 0
        for key in a:
            for state in (a[key], b[key]):
                if not isinstance(state, str) or not state:
                    raise common.IncompleteInput(f"scenario {sc}, {key}: state missing")
            if a[key] != b[key]:
                n_changed += 1
                details.append({"scenario": sc, "state": "/".join(map(str, key)) if isinstance(key, tuple) else str(key),
                                "with": a[key], "without": b[key]})
        by_scenario[sc] = {"changed": n_changed, "total": len(a)}
        total += len(a)
        changed += n_changed
    return A3Result(status=REPORTED, source=source, swap=swap, scenarios=list(with_source),
                    states_total=total, states_changed=changed, by_scenario=by_scenario, changed=details)

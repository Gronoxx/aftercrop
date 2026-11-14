# Written with an SI tool (Claude, Anthropic) for NASA Space Apps 2026. See AI_USE.md.
"""N1, noise floor: the smallest contrast of dates that HLS supports.

Two nulls of the same field (plan, sections 11.2 and 11.5): the two halves of
one polygon, and L30 against S30, each giving the soybean exit date of a
season. The disagreement of a pair is the absolute difference in days. The
floor is the larger of the two percentiles (``protocol.tests.N1.floor``):
nothing below it reaches the screen, and the field order needs a difference
above it.

The protocol states the level (the 90th percentile) only in words and names no
percentile method. Both are read from ``tests.N1.percentile`` and
``tests.N1.percentile_method`` when present; until then the caller passes them
[DECIDE]. No value is assumed here.

The floor needs both nulls. Without pairs on one side the larger of the two is
undefined, so N1 is "not_completed" [DECIDE]. A completed N1 reports and
cannot fail (status "reported").
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from fractions import Fraction
from typing import Any

from pipeline.bench import common, stats
from pipeline.bench.common import NOT_COMPLETED, REPORTED


def _level(value: Any, where: str) -> Fraction:
    try:
        level = stats.exact(value)
    except (TypeError, ValueError) as exc:
        raise common.ProtocolError(f"{where}: {exc}") from exc
    if not 0 < level < 100:
        raise common.ProtocolError(f"{where} must lie in (0, 100), got {level}")
    return level


@dataclass(frozen=True)
class N1Rule:
    percentile: Fraction
    method: str
    unit: str

    @classmethod
    def from_protocol(cls, protocol: Mapping[str, Any], *, percentile: Any = None,
                      method: str | None = None) -> "N1Rule":
        sec = common.protocol_section(protocol, "N1")
        where = "tests.N1"
        common.require(sec, "floor", where)
        unit = common.require(sec, "unit", where)
        if unit != "days":
            raise common.ProtocolError(f"protocol.{where}.unit must be 'days'")
        level = _level(common.setting(sec, "percentile", where, percentile), f"protocol.{where}.percentile")
        m = common.setting(sec, "percentile_method", where, method)
        if m not in stats.PERCENTILE_METHODS:
            raise common.ProtocolError(f"protocol.{where}.percentile_method must be one of {stats.PERCENTILE_METHODS}")
        return cls(percentile=level, method=m, unit=unit)


def disagreement_days(a: date, b: date) -> int:
    """Disagreement of one pair: the absolute difference of the two dates, in days."""
    return abs((a - b).days)


@dataclass(frozen=True)
class N1Result:
    status: str
    percentile: Fraction
    method: str
    n_halves: int
    n_sensors: int
    p_halves: Fraction | None
    p_sensors: Fraction | None
    notes: list[str] = field(default_factory=list)

    @property
    def floor_days(self) -> Fraction | None:
        if self.p_halves is None or self.p_sensors is None:
            return None
        return max(self.p_halves, self.p_sensors)

    @property
    def set_by(self) -> str | None:
        if self.floor_days is None:
            return None
        return "halves" if self.p_halves >= self.p_sensors else "l30_vs_s30"

    def to_dict(self) -> dict[str, Any]:
        def f(x):
            return None if x is None else float(x)
        return {
            "id": "N1",
            "status": self.status,
            "statistic": "percentile_of_disagreement_days",
            "unit": "days",
            "percentile": float(self.percentile),
            "percentile_method": self.method,
            "n": self.n_halves + self.n_sensors,
            "n_halves": self.n_halves,
            "n_l30_vs_s30": self.n_sensors,
            "percentile_halves_days": f(self.p_halves),
            "percentile_l30_vs_s30_days": f(self.p_sensors),
            "floor_days": f(self.floor_days),
            "set_by": self.set_by,
            "threshold": None,
            "notes": list(self.notes),
        }


def _days(values: Sequence[Any], what: str) -> list[Fraction]:
    return [abs(v) for v in common.exact_simulated(list(values), what)]


def evaluate(halves: Sequence[Any], l30_vs_s30: Sequence[Any], rule: N1Rule) -> N1Result:
    """N1 from the disagreements in days: field halves, and L30 against S30.

    Each list pools every field and season of ``noise_floor.json``. A signed
    difference is taken in absolute value.
    """
    a = _days(halves, "disagreement of field halves")
    b = _days(l30_vs_s30, "disagreement of L30 against S30")
    notes = []
    if not a:
        notes.append("no pairs of field halves")
    if not b:
        notes.append("no pairs of L30 against S30")
    pa = stats.percentile(a, rule.percentile, rule.method) if a else None
    pb = stats.percentile(b, rule.percentile, rule.method) if b else None
    status = REPORTED if a and b else NOT_COMPLETED
    if status == NOT_COMPLETED:
        notes.append("the floor needs both nulls")
    return N1Result(status=status, percentile=rule.percentile, method=rule.method,
                    n_halves=len(a), n_sensors=len(b), p_halves=pa, p_sensors=pb, notes=notes)


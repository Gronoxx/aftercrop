# Written with an SI tool (Claude, Anthropic) for NASA Space Apps 2026. See AI_USE.md.
"""P1, wrong year: does the climate of the right year add anything?

Fixed in full at the tag (plan, section 11.2, rule of the project):

- B = 9 backward shifts, k = 4, 8, ..., 36 years, circular in 1985 to 2024.
  Each k swaps the climate of every site-season at once. Shift rule of the
  protocol (``tests.P1.shift_rule``): a site-season starting in year y takes
  the shifted start year s = y - k, plus 40 when y - k is before 1985, and its
  whole chain moves by the same offset s - y, so the wrong-year climate stays a
  real, unbroken sequence. Dates keep month and day; 29 February moves to
  28 February in a common year (never needed here: every offset is a multiple
  of 4). The shifted chain must stay inside the climate series.
- Statistic: Kendall S, for T0 and T1 separately.
- Null: the right year adds nothing, the real S is exchangeable with the S of
  the wrong years. Alternative, one-sided: the real S is larger.
- p = (1 + number of shifted S >= real S) / (B + 1). A tie counts against.
  Passes with p <= 0.10 in both T0 and T1, that is, with no shifted S equal to
  or above the real one.
- Stopping rule: all 9 shifts or nothing. A missing shift leaves P1
  "not_completed", never approved, and no partial p is computed.
- P1 draws nothing at random.

This module only holds the logic. The chains run elsewhere, and their results
arrive as arguments.
"""

from __future__ import annotations

import calendar
from collections.abc import Hashable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from fractions import Fraction
from typing import Any

from pipeline.bench import common, stats
from pipeline.bench.common import FAIL, NOT_COMPLETED, PASS

# The plan fixes these exactly (section 11.2). A protocol that differs is refused.
PLAN_SHIFTS_YEARS = (4, 8, 12, 16, 20, 24, 28, 32, 36)
PLAN_CIRCULAR_YEARS = (1985, 2024)


@dataclass(frozen=True)
class P1Design:
    shifts: tuple[int, ...]
    first_year: int
    last_year: int
    alpha: Fraction
    climate: tuple[date, date] | None = None   # first and last day of the climate series

    @property
    def b(self) -> int:
        return len(self.shifts)

    @property
    def span(self) -> int:
        return self.last_year - self.first_year + 1

    @classmethod
    def from_protocol(cls, protocol: Mapping[str, Any]) -> "P1Design":
        """Reads ``protocol.tests.P1`` and the climate series of ``protocol.dates``."""
        sec = common.protocol_section(protocol, "P1")
        where = "tests.P1"
        shifts = tuple(common.require(sec, "shifts_back_years", where))
        years = tuple(common.require(sec, "circular_years", where))
        if shifts != PLAN_SHIFTS_YEARS:
            raise common.ProtocolError(
                f"protocol.{where}.shifts_back_years must be {list(PLAN_SHIFTS_YEARS)}, got {list(shifts)}")
        if years != PLAN_CIRCULAR_YEARS:
            raise common.ProtocolError(
                f"protocol.{where}.circular_years must be {list(PLAN_CIRCULAR_YEARS)}, got {list(years)}")
        if common.require(sec, "B", where) != len(shifts):
            raise common.ProtocolError(f"protocol.{where}.B must equal the number of shifts ({len(shifts)})")
        if sec.get("statistic", "kendall_s") != "kendall_s":
            raise common.ProtocolError(f"protocol.{where}.statistic must be 'kendall_s'")
        common.require_ties_against(sec, where)
        common.require_no_draws(sec, where)
        block = common.protocol_block(protocol)
        dates = block.get("dates") if isinstance(block.get("dates"), Mapping) else {}
        if "p1_circular_years" in dates and tuple(dates["p1_circular_years"]) != years:
            raise common.ProtocolError("protocol.dates.p1_circular_years differs from tests.P1.circular_years")
        climate = None
        if "climate" in dates:
            try:
                first, last = (date.fromisoformat(str(x)) for x in dates["climate"])
            except (TypeError, ValueError) as exc:
                raise common.ProtocolError(f"protocol.dates.climate not understood: {exc}") from exc
            climate = (first, last)
        design = cls(shifts=shifts, first_year=years[0], last_year=years[1],
                     alpha=common.require_level(sec, "alpha", where), climate=climate)
        design.check()
        return design

    def check(self) -> None:
        """Distinct shifts strictly inside the span, and offsets that keep leap years."""
        if len(set(self.shifts)) != len(self.shifts):
            raise common.ProtocolError("P1 shifts must be distinct")
        if any(not 0 < k < self.span for k in self.shifts):
            raise common.ProtocolError("every P1 shift must lie strictly inside the circular span")
        for k in self.shifts:
            for y in range(self.first_year, self.last_year + 1):
                if chain_offset(y, k, self) % 4:
                    raise common.ProtocolError(f"shift {k} gives start year {y} an offset that is not a multiple of 4")


# Circular shift of the climate ---------------------------------------------------

def shift_year(start_year: int, k: int, design: P1Design) -> int:
    """Shifted start year s = y - k, plus the span (40) when y - k is before the span."""
    if not design.first_year <= start_year <= design.last_year:
        raise ValueError(f"start year {start_year} outside the circular span "
                         f"{design.first_year}-{design.last_year}")
    return design.first_year + (start_year - k - design.first_year) % design.span


def chain_offset(start_year: int, k: int, design: P1Design) -> int:
    """Years added to every date of the site-season's chain: -k or span - k."""
    return shift_year(start_year, k, design) - start_year


def move_year(d: date, year: int) -> date:
    """Same month and day in another year; 29 February becomes 28 February in a common year."""
    if d.month == 2 and d.day == 29 and not calendar.isleap(year):
        return date(year, 2, 28)
    return d.replace(year=year)


def source_date(d: date, start_year: int, k: int, design: P1Design) -> date:
    """Day whose weather drives day ``d`` of the chain of the site-season that
    starts in ``start_year``, under shift k. Raises when it falls outside the
    climate series."""
    src = move_year(d, d.year + chain_offset(start_year, k, design))
    if design.climate is not None and not design.climate[0] <= src <= design.climate[1]:
        raise ValueError(f"shift {k} takes {d} of start year {start_year} to {src}, "
                         f"outside the climate series {design.climate[0]} to {design.climate[1]}")
    return src


# Statistic and p ---------------------------------------------------------------

def p_value(s_real: int, s_shifted: Sequence[int], design: P1Design) -> Fraction:
    """p = (1 + number of shifted S >= real S) / (B + 1). Needs all B values."""
    if len(s_shifted) != design.b:
        raise ValueError(f"P1 needs {design.b} shifted S, got {len(s_shifted)}")
    return Fraction(1 + stats.count_at_least(s_shifted, s_real), design.b + 1)


def shifted_s(
    observed: Mapping[Hashable, Any],
    simulated_by_shift: Mapping[int, Mapping[Hashable, Any] | None],
    design: P1Design,
) -> dict[int, int | None]:
    """Kendall S of each shifted run against the same observed values.

    ``observed`` maps a site-season key to its observed value. Each shifted run
    maps the same keys to simulated values. A run that is absent, lacks a key or
    holds a non-number gives None (incomplete). A shift outside the design or a
    key outside ``observed`` raises.
    """
    extra = set(simulated_by_shift) - set(design.shifts)
    if extra:
        raise ValueError(f"shifts outside the protocol: {sorted(extra)}")
    keys = list(observed)
    obs = common.exact_observed([observed[key] for key in keys])
    out: dict[int, int | None] = {}
    for k in design.shifts:
        run = simulated_by_shift.get(k)
        if run is None:
            out[k] = None
            continue
        unknown = set(run) - set(keys)
        if unknown:
            raise ValueError(f"shift {k} has site-seasons outside the observed set: {sorted(map(str, unknown))}")
        try:
            sim = common.exact_simulated([run[key] for key in keys])
        except (KeyError, common.IncompleteInput):
            out[k] = None
            continue
        out[k] = stats.kendall_s(sim, obs)
    return out


@dataclass(frozen=True)
class P1ArmResult:
    arm: str                          # "T0" or "T1"
    status: str                       # pass, fail or not_completed
    s_real: int | None
    s_by_shift: dict[int, int | None]
    n_at_least: int | None            # shifted S >= real S, ties included
    p: Fraction | None
    missing_shifts: list[int] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "arm": self.arm,
            "status": self.status,
            "s_real": self.s_real,
            "s_by_shift": {str(k): v for k, v in self.s_by_shift.items()},
            "n_at_least": self.n_at_least,
            "p": None if self.p is None else float(self.p),
            "missing_shifts": list(self.missing_shifts),
        }


def evaluate_arm(arm: str, s_real: int | None, s_by_shift: Mapping[int, int | None], design: P1Design) -> P1ArmResult:
    """P1 for one arm (T0 or T1). All 9 shifts or "not_completed"."""
    extra = set(s_by_shift) - set(design.shifts)
    if extra:
        raise ValueError(f"shifts outside the protocol: {sorted(extra)}")
    by_shift = {k: s_by_shift.get(k) for k in design.shifts}
    missing = [k for k, v in by_shift.items() if v is None]
    if s_real is None or missing:
        return P1ArmResult(arm, NOT_COMPLETED, s_real, by_shift, None, None, missing)
    values = [by_shift[k] for k in design.shifts]
    n_ge = stats.count_at_least(values, s_real)
    p = p_value(s_real, values, design)
    return P1ArmResult(arm, PASS if p <= design.alpha else FAIL, s_real, by_shift, n_ge, p, [])


@dataclass(frozen=True)
class P1Result:
    status: str
    t0: P1ArmResult
    t1: P1ArmResult

    def to_dict(self) -> dict[str, Any]:
        return {"id": "P1", "status": self.status, "statistic": "kendall_s",
                "t0": self.t0.to_dict(), "t1": self.t1.to_dict()}


def combine(t0: P1ArmResult, t1: P1ArmResult) -> P1Result:
    """P1 passes only if both arms pass. A measured failure is a failure; an
    incomplete arm without a failure leaves P1 "not_completed"."""
    statuses = (t0.status, t1.status)
    if statuses == (PASS, PASS):
        status = PASS
    elif FAIL in statuses:
        status = FAIL
    else:
        status = NOT_COMPLETED
    return P1Result(status=status, t0=t0, t1=t1)

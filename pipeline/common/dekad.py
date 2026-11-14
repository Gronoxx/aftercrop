# Written with an SI tool (Claude, Anthropic) for NASA Space Apps 2026. See AI_USE.md.
"""Civil dekads: days 1-10, 11-20 and 21 to the end of the month.

One module for every dekad in the project (plan, section 12.3). A year has 36
dekads. Only the origin changes:

- ribbon: index 1 is 1-10 September, index 36 is 21-31 August (section 8.6);
- Zarc table: index 1 is 1-10 January, index 36 is 21-31 December
  (``dec1`` to ``dec36``, origin confirmed by ``pipeline.adapters.zarc``).
"""

from __future__ import annotations

import calendar
import operator
from datetime import date

N_DEKADS = 36
RIBBON_START_MONTH = 9
ZARC_START_MONTH = 1


def dekad_in_month(day: int) -> int:
    """1 for days 1-10, 2 for days 11-20, 3 for day 21 to month end."""
    if not 1 <= day <= 31:
        raise ValueError(f"day out of range: {day}")
    return 1 if day <= 10 else 2 if day <= 20 else 3


def _check_index(i: int) -> int:
    i = operator.index(i)  # accepts numpy integers, rejects floats
    if not 1 <= i <= N_DEKADS:
        raise ValueError(f"dekad index must be 1..{N_DEKADS}, got {i!r}")
    return i


def _check_month(m: int) -> int:
    m = operator.index(m)
    if not 1 <= m <= 12:
        raise ValueError(f"month must be 1..12, got {m!r}")
    return m


def index(d: date, start_month: int) -> int:
    """Dekad index 1..36 of ``d`` in a year that starts on day 1 of ``start_month``."""
    start_month = _check_month(start_month)
    return ((d.month - start_month) % 12) * 3 + dekad_in_month(d.day)


def cycle_start_year(d: date, start_month: int) -> int:
    """Calendar year in which the 36-dekad cycle containing ``d`` begins."""
    start_month = _check_month(start_month)
    return d.year if d.month >= start_month else d.year - 1


def month_and_part(i: int, start_month: int) -> tuple[int, int, int]:
    """(month, part 1..3, months after the cycle start) for dekad ``i``."""
    i = _check_index(i)
    start_month = _check_month(start_month)
    offset = (i - 1) // 3
    return (start_month - 1 + offset) % 12 + 1, (i - 1) % 3 + 1, offset


def bounds(i: int, start_month: int, cycle_year: int) -> tuple[date, date]:
    """First and last day of dekad ``i`` in the cycle that begins in ``cycle_year``."""
    month, part, offset = month_and_part(i, start_month)
    year = cycle_year + (start_month - 1 + offset) // 12
    first = (1, 11, 21)[part - 1]
    last = (10, 20, calendar.monthrange(year, month)[1])[part - 1]
    return date(year, month, first), date(year, month, last)


def convert(i: int, from_month: int, to_month: int) -> int:
    """Same dekad, renumbered from one origin to another."""
    i = _check_index(i)
    from_month, to_month = _check_month(from_month), _check_month(to_month)
    shift = ((from_month - to_month) % 12) * 3
    return (i - 1 + shift) % N_DEKADS + 1


# Ribbon: 1 September to 31 August, one row per season.

def ribbon_index(d: date) -> int:
    return index(d, RIBBON_START_MONTH)


def season_label(d: date) -> str:
    """Season of the ribbon that contains ``d``, as '2025/26'."""
    y = cycle_start_year(d, RIBBON_START_MONTH)
    return f"{y}/{(y + 1) % 100:02d}"


def season_start_year(label: str) -> int:
    """'2025/26' -> 2025."""
    first, second = label.split("/")
    y = int(first)
    if int(second) != (y + 1) % 100:
        raise ValueError(f"not a season label: {label!r}")
    return y


def ribbon_bounds(i: int, season: str) -> tuple[date, date]:
    return bounds(i, RIBBON_START_MONTH, season_start_year(season))


# Zarc table: dec1..dec36 over the calendar year.

def zarc_index(d: date) -> int:
    return index(d, ZARC_START_MONTH)


def zarc_bounds(i: int, year: int) -> tuple[date, date]:
    return bounds(i, ZARC_START_MONTH, year)


def zarc_to_ribbon(i: int) -> int:
    return convert(i, ZARC_START_MONTH, RIBBON_START_MONTH)


def ribbon_to_zarc(i: int) -> int:
    return convert(i, RIBBON_START_MONTH, ZARC_START_MONTH)


def label(i: int, start_month: int) -> str:
    """Year-free label such as '01-10 Sep' or '21-28/29 Feb'."""
    month, part, _ = month_and_part(i, start_month)
    abbr = calendar.month_abbr[month]
    if part == 1:
        return f"01-10 {abbr}"
    if part == 2:
        return f"11-20 {abbr}"
    if month == 2:
        return f"21-28/29 {abbr}"
    return f"21-{calendar.monthrange(2001, month)[1]} {abbr}"

# Written with an SI tool (Claude, Anthropic) for NASA Space Apps 2026. See AI_USE.md.
"""Counts for L2: dry autumns and cold in the window, with the years by name (plan 12.2, 10.12, 7.5).

Counts are counts: "N of M years", never a probability, a percentile or a forecast.
The fixed texts of 12.2 are copy (``risk.power`` for the direct POWER count, ``risk.simulated``
for an engine frequency): this module only fills N, M and K.

K = 10 × N / M, rounded half up (the plan says "rounded" and nothing more), always shown
with 10 icons. The years are listed by name so the farmer can check them from memory.

"Dry autumn" uses a rain threshold in mm fixed in the protocol, and "cold in the window" the
minimum of 10.12 (``protocol.frost.tmin_below_c``, 2 °C, Zarc 2026/27; POWER is not a shelter).
The protocol block has no dry-autumn threshold and neither rule fixes the date span, so both
come in as explicit arguments ([DECIDE]). A percentile of the record itself is never used: it
would give the same frequency everywhere.

Input: the daily POWER table of ``pipeline.adapters.power.read`` (date index, one column per
parameter, missing as NaN). A year with a missing day inside the span is left out of M.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

import pandas as pd

from pipeline.decision import strategies as st

RAIN = "PRECTOTCORR"
TMIN = "T2M_MIN"
SOURCE_ID = "power_daily"


class RiskCountError(ValueError):
    pass


@dataclass(frozen=True)
class Count:
    n: int
    m: int
    years: tuple[int, ...]
    left_out: tuple[int, ...] = field(default_factory=tuple)   # years with a missing day

    def as_json(self) -> dict:
        """The shape of field_<id>.json: ``{n, m, years}``."""
        return {"n": self.n, "m": self.m, "years": list(self.years)}

    @property
    def k_in_10(self) -> int | None:
        return about_k_in_10(self.n, self.m)


def about_k_in_10(n: int, m: int) -> int | None:
    """K = 10 n / m rounded half up, in exact integer arithmetic. None when m = 0."""
    if not (isinstance(n, int) and isinstance(m, int)) or n < 0 or m < 0 or n > m:
        raise RiskCountError(f"not a count: {n} of {m}")
    if m == 0:
        return None
    return (20 * n + m) // (2 * m)


def climate_years(protocol: Mapping[str, Any]) -> range:
    """Full years of ``protocol.dates.climate`` (1984-01-01 to 2025-12-31)."""
    a, b = (date.fromisoformat(x) for x in protocol["dates"]["climate"])
    first = a.year if (a.month, a.day) == (1, 1) else a.year + 1
    last = b.year if (b.month, b.day) == (12, 31) else b.year - 1
    if last < first:
        raise RiskCountError("no full year in protocol.dates.climate")
    return range(first, last + 1)


def _span_days(year: int, span: Sequence[str]) -> list[date]:
    if len(span) != 2:
        raise RiskCountError(f"span needs a start and an end, got {span!r}")
    try:
        a = date(year, *(int(x) for x in span[0].split("-")))
        b = date(year, *(int(x) for x in span[1].split("-")))
    except ValueError as exc:
        raise RiskCountError(f"span {span!r} is not a day of {year}: {exc}") from exc
    if a > b:
        raise RiskCountError(f"span {span!r} must lie inside one calendar year")
    return [a + timedelta(days=i) for i in range((b - a).days + 1)]


def _series(daily: pd.DataFrame, column: str) -> pd.Series:
    if column not in daily.columns:
        raise RiskCountError(f"daily table has no {column}")
    s = pd.to_numeric(daily[column], errors="coerce")
    s.index = pd.DatetimeIndex(daily.index).normalize()
    if s.index.has_duplicates:
        raise RiskCountError(f"duplicate days in {column}")
    return s


def count_years(daily: pd.DataFrame, column: str, *, span: Sequence[str], years: Sequence[int], hit) -> Count:
    """Years whose values over ``span`` satisfy ``hit(values)``. Incomplete years are left out of M."""
    s = _series(daily, column)
    n_years, hits, left = 0, [], []
    for y in years:
        idx = pd.DatetimeIndex([pd.Timestamp(d) for d in _span_days(y, span)])
        vals = s.reindex(idx)
        if vals.isna().any():
            left.append(int(y))
            continue
        n_years += 1
        if hit(vals.to_numpy(dtype=float)):
            hits.append(int(y))
    return Count(n=len(hits), m=n_years, years=tuple(hits), left_out=tuple(left))


def _threshold(x: Any, what: str) -> float:
    if isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x):
        raise RiskCountError(f"{what} must be a finite number, got {x!r}")
    return float(x)


def dry_autumn(daily: pd.DataFrame, *, threshold_mm: Any, span: Sequence[str], years: Sequence[int]) -> Count:
    """Years whose rain over ``span`` totals less than ``threshold_mm`` ([DECIDE] both)."""
    t = _threshold(threshold_mm, "threshold_mm")
    if t <= 0:
        raise RiskCountError("threshold_mm must be positive")
    return count_years(daily, RAIN, span=span, years=years, hit=lambda v: float(v.sum()) < t)


def cold_in_window(daily: pd.DataFrame, *, tmin_below_c: Any, span: Sequence[str], years: Sequence[int]) -> Count:
    """Years with at least one day of minimum temperature below ``tmin_below_c`` inside ``span``."""
    t = _threshold(tmin_below_c, "tmin_below_c")
    return count_years(daily, TMIN, span=span, years=years, hit=lambda v: bool((v < t).any()))


def power_text(c: Count, lang: str = "en") -> str:
    """Direct POWER count (12.2), from copy key ``risk.power``, with the years by name
    (``page.rows.L2.years``)."""
    out = _fill(st.text("risk.power", lang), c.n, c.m)
    if c.years:
        out += ". " + st.text("page.rows.L2.years", lang).format(years=", ".join(str(y) for y in c.years))
    return out


def simulated_text(n: int, m: int, lang: str = "en") -> str:
    """Engine frequency (12.2), from copy key ``risk.simulated``. Never without the word simulated."""
    return _fill(st.text("risk.simulated", lang), n, m)


def _fill(template: str, n: int, m: int) -> str:
    k = about_k_in_10(n, m)
    if k is None:
        raise RiskCountError("no year to count: the fixed text needs M > 0")
    return template.format(N=n, M=m, K=k)


def icons(c: Count) -> list[bool]:
    """Always 10 icons, K of them filled."""
    k = c.k_in_10 or 0
    return [i < k for i in range(10)]


# Climate over the occupation of each crop (SA-D72.5, protocol.climate) -------------------------
#
# Replaces dry autumn and cold in the window, which had no sourced threshold (SA-D68). The period
# is the occupation of the crop that follows the soybean (config/decision_settings.yaml occupancy,
# Embrapa Doc 476 medians), so no date span is invented. Descriptive counts of the past: never a
# forecast and never a threshold of failure.
#
# A period is named by its season (1 Sep to 31 Aug) of the start day: corn sown on 22 Mar 2024
# follows the 2023/24 soybean and is "2023/24". Only periods that lie whole inside the protocol
# years count, and a period with a missing day of either variable is left out of n.

SEASON_START_MONTH = 9


def season_label(d: date) -> str:
    """The season (1 Sep to 31 Aug) that holds ``d``, as the ribbon names it: ``2023/24``."""
    y = d.year if d.month >= SEASON_START_MONTH else d.year - 1
    return f"{y}/{(y + 1) % 100:02d}"


def _md(month_day: Any) -> tuple[int, int]:
    if not (isinstance(month_day, str) and len(month_day) == 5 and month_day[2] == "-"):
        raise RiskCountError(f"not MM-DD: {month_day!r}")
    return int(month_day[:2]), int(month_day[3:])


def protocol_years(protocol: Mapping[str, Any]) -> tuple[int, int]:
    """``protocol.climate.years`` as two integers (1984, 2025)."""
    ys = (protocol.get("climate") or {}).get("years")
    if not (isinstance(ys, (list, tuple)) and len(ys) == 2):
        raise RiskCountError("protocol.climate.years must be [first, last]")
    a, b = (int(x) for x in ys)
    if b < a:
        raise RiskCountError("protocol.climate.years: last before first")
    return a, b


def occupation_periods(start: str, end: str, years: tuple[int, int]) -> list[tuple[str, date, date]]:
    """``(season, first day, last day)`` of every whole occupation period inside ``years``.

    ``start`` and ``end`` are MM-DD. An end earlier in the calendar than the start falls in the
    next calendar year. Pure.
    """
    (sm, sd), (em, ed) = _md(start), _md(end)
    lo, hi = date(years[0], 1, 1), date(years[1], 12, 31)
    out = []
    for y in range(years[0], years[1] + 1):
        try:
            a = date(y, sm, sd)
            b = date(y if (em, ed) >= (sm, sd) else y + 1, em, ed)
        except ValueError as exc:
            raise RiskCountError(f"{start} to {end} is not a period of {y}: {exc}") from exc
        if lo <= a and b <= hi:
            out.append((season_label(a), a, b))
    return out


def p10_nearest_rank(values: Sequence[float]) -> float | None:
    """10th percentile by nearest rank: the value of rank ceil(0.1 n) in ascending order."""
    v = sorted(values)
    if not v:
        return None
    return v[-(-len(v) // 10) - 1]


def median(values: Sequence[float]) -> float | None:
    v = sorted(values)
    n = len(v)
    if n == 0:
        return None
    return v[n // 2] if n % 2 else (v[n // 2 - 1] + v[n // 2]) / 2


def _mm(x: float | None) -> float | None:
    return None if x is None else round(float(x), 1)


def occupation_climate(daily: pd.DataFrame, *, start: str, end: str, years: tuple[int, int],
                       frost_below_c: Any, n_driest: int = 3) -> dict:
    """Frost and rain over the occupation ``start`` to ``end`` (MM-DD), one value per season.

    ``frost``: seasons with at least one day of T2M_MIN below ``frost_below_c`` (k of n).
    ``rain``: PRECTOTCORR total per season, the median, the 10th percentile by nearest rank and
    the ``n_driest`` driest seasons by name (ties by season name). Totals in mm, one decimal.
    """
    t = _threshold(frost_below_c, "frost_below_c")
    rain, tmin = _series(daily, RAIN), _series(daily, TMIN)
    totals: dict[str, float] = {}
    frost: list[str] = []
    left: list[str] = []
    for season, a, b in occupation_periods(start, end, years):
        idx = pd.date_range(a, b, freq="D")
        r, m = rain.reindex(idx), tmin.reindex(idx)
        if r.isna().any() or m.isna().any():
            left.append(season)
            continue
        totals[season] = float(r.to_numpy(dtype=float).sum())
        if bool((m.to_numpy(dtype=float) < t).any()):
            frost.append(season)
    driest = sorted(totals.items(), key=lambda kv: (kv[1], kv[0]))[:n_driest]
    vals = list(totals.values())
    return {
        "start": start, "end": end, "n": len(totals), "left_out": left,
        "frost": {"below_c": t, "k": len(frost), "seasons": frost},
        "rain": {"median_mm": _mm(median(vals)), "p10_mm": _mm(p10_nearest_rank(vals)),
                 "driest": [{"season": s, "mm": _mm(v)} for s, v in driest]},
    }

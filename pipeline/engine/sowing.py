# Written with an SI tool (Claude, Anthropic) for NASA Space Apps 2026. See AI_USE.md.
"""Sowing dates: the rain rule for field soybean (plan 10.6, PROTOCOL.md 6).

Rule: the first day d whose rain on d-(n-1) .. d sums to ``rain_mm`` or
more, with all n days on or after the window start, and d no later than the
latest date. No trigger by then: the season counts as a failure, never moved.
In S3 the soybean is never sown before the wheat harvest plus a fixed number
of days. Trials use published dates and need nothing from here, except a
missing Floresta date: both arms take the first date that meets the rule in
both arms (``common`` key, resolved by ``chain.run_arms``).

Every number comes from the ``protocol:`` block or from an explicit argument.
A value missing from both raises ``ProtocolGap``, never a default.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd

# Open points the protocol block does not settle. Recommendations, not values.
OPEN_ITEMS = {
    "rain_after_not_before": (
        "[DECIDE D6] When the soybean has a not-before date later than the window "
        "start (the instance start after the predecessor harvest, or the S3 wheat "
        "harvest plus 20 days), must the n rain days also fall on or after it? "
        "Recommended: yes, as PROTOCOL.md 16 already does for the window start."),
    "zarc_window": (
        "[DECIDE D6] Option (b) takes start and latest from the Zarc soybean window "
        "of the municipality, which needs a soil class and a cycle, and one run of "
        "indicated dekads. Recommended: option (a), 09-01 to 12-31, already in the block."),
}


class ProtocolGap(KeyError):
    """A rule value is neither in the protocol block nor given explicitly."""


def _md(s: str) -> tuple[int, int]:
    try:
        m, d = (int(x) for x in str(s).split("-"))
        date(2001, m, d)  # validates; 29 Feb rejected on purpose
    except ValueError as e:
        raise ValueError(f"not an MM-DD date: {s!r}") from e
    return m, d


def _pick(section: Mapping[str, Any], key: str, explicit: Any, where: str) -> Any:
    in_block = section.get(key) if isinstance(section, Mapping) else None
    if in_block is not None and explicit is not None and in_block != explicit:
        raise ValueError(f"{where}.{key} is {in_block!r} in the protocol, got {explicit!r}")
    value = in_block if in_block is not None else explicit
    if value is None:
        note = OPEN_ITEMS.get(key, "[DECIDE] not in the protocol block")
        raise ProtocolGap(f"{where}.{key}: pass it explicitly. {note}")
    return value


@dataclass(frozen=True)
class RainRule:
    rain_mm: float
    rain_days: int
    start: str                  # MM-DD, window start, common to both arms
    latest: str                 # MM-DD, last day d may take
    rain_after_not_before: bool  # see OPEN_ITEMS

    def __post_init__(self):
        if not self.rain_mm > 0:
            raise ValueError("rain_mm must be positive")
        if int(self.rain_days) != self.rain_days or self.rain_days < 1:
            raise ValueError("rain_days must be a positive integer")
        _md(self.start), _md(self.latest)
        if not isinstance(self.rain_after_not_before, bool):
            raise TypeError("rain_after_not_before must be True or False")

    @classmethod
    def from_protocol(cls, protocol: Mapping[str, Any], *, rain_mm: float | None = None,
                      rain_days: int | None = None, start: str | None = None,
                      latest: str | None = None,
                      rain_after_not_before: bool | None = None) -> "RainRule":
        """Values from protocol.sowing.soybean_field, explicit ones fill the gaps."""
        where = "protocol.sowing.soybean_field"
        sec = (protocol.get("sowing") or {}).get("soybean_field") or {}
        return cls(
            rain_mm=float(_pick(sec, "rain_mm", rain_mm, where)),
            rain_days=int(_pick(sec, "rain_days", rain_days, where)),
            start=str(_pick(sec, "start", start, where)),
            latest=str(_pick(sec, "latest", latest, where)),
            rain_after_not_before=_pick(sec, "rain_after_not_before", rain_after_not_before, where),
        )

    def window(self, season_year: int) -> tuple[date, date]:
        """Start in ``season_year``. A latest MM-DD before the start falls in the next year."""
        (sm, sd), (lm, ld) = _md(self.start), _md(self.latest)
        first = date(season_year, sm, sd)
        latest = date(season_year, lm, ld)
        if latest < first:
            latest = date(season_year + 1, lm, ld)
        return first, latest


def s3_gap_days(protocol: Mapping[str, Any], explicit: int | None = None) -> int:
    """Days after the wheat harvest before the S3 soybean may be sown."""
    sec = (protocol.get("sowing") or {}).get("soybean_field") or {}
    v = int(_pick(sec, "s3_not_before_wheat_harvest_plus_days", explicit,
                  "protocol.sowing.soybean_field"))
    if v < 1:
        raise ValueError("the S3 gap must be at least one day")
    return v


@dataclass(frozen=True)
class RainSowing:
    """Sowing spec of one season: the rain rule in the window of ``season_year``.

    ``min_days_after_harvest`` 1 means the day after the previous harvest (the
    instance start). S3 soybean uses the protocol gap. Arms whose season at
    the same position shares a ``common`` key take one date, the first that
    meets the rule in every one of them.
    """
    rule: RainRule
    season_year: int
    min_days_after_harvest: int = 1
    common: str | None = None

    def __post_init__(self):
        if self.min_days_after_harvest < 1:
            raise ValueError("min_days_after_harvest must be at least 1")

    def window(self) -> tuple[date, date]:
        return self.rule.window(self.season_year)

    def not_before(self, instance_start: date, previous_harvest: date | None) -> date:
        if previous_harvest is None:
            return instance_start
        gap = previous_harvest + timedelta(days=self.min_days_after_harvest)
        return max(instance_start, gap)


def rain_series(weather: pd.DataFrame) -> pd.Series:
    """Daily rain indexed by date, from the engine weather table."""
    s = pd.Series(weather["Precipitation"].to_numpy(dtype=float),
                  index=pd.DatetimeIndex(weather["Date"]), name="rain_mm")
    if s.isna().any():
        raise ValueError("rain has NaN")
    return s


def first_trigger(rain: pd.Series, rule: RainRule, rain_from: date, first_day: date,
                  latest: date) -> date | None:
    """First d in [first_day, latest] whose n-day rain sum, all days >= rain_from, meets the rule."""
    n = int(rule.rain_days)
    lo = max(first_day, rain_from + timedelta(days=n - 1))
    if lo > latest:
        return None
    a, b = pd.Timestamp(rain_from), pd.Timestamp(latest)
    if rain.index[0] > a or rain.index[-1] < b:
        raise ValueError(f"rain does not cover {rain_from}..{latest}")
    part = rain.loc[a:b]
    if len(part) != (b - a).days + 1:
        raise ValueError("rain is not a continuous daily series")
    # Rounded to 1e-6 mm so that 6.7 + 6.6 + 6.7 counts as 20 mm.
    sums = np.round(part.rolling(n, min_periods=n).sum().to_numpy(), 6)
    ok = (sums >= rule.rain_mm) & (part.index >= pd.Timestamp(lo))
    hits = np.flatnonzero(ok)
    return part.index[hits[0]].date() if len(hits) else None


def trigger(rain: pd.Series, spec: RainSowing, earliest: date) -> date | None:
    """Sowing date of ``spec`` for a soybean that cannot be sown before ``earliest``."""
    start, latest = spec.window()
    first_day = max(start, earliest)
    rain_from = first_day if spec.rule.rain_after_not_before else start
    return first_trigger(rain, spec.rule, rain_from, first_day, latest)


def common_trigger(rain: pd.Series, spec: RainSowing, earliest_by_arm: Mapping[str, date]) -> date | None:
    """One date for every arm: the first that meets the rule in all of them."""
    if not earliest_by_arm:
        raise ValueError("no arm given")
    return trigger(rain, spec, max(earliest_by_arm.values()))


def zarc_window_md(runs: Sequence[Mapping[str, Any]]) -> tuple[str, str]:
    """(start, latest) MM-DD from ``zarc.windows`` output. Exactly one run, or [DECIDE D6]."""
    if len(runs) != 1:
        raise ProtocolGap(f"Zarc gives {len(runs)} soybean windows, pick one. {OPEN_ITEMS['zarc_window']}")
    a, b = date.fromisoformat(runs[0]["start"]), date.fromisoformat(runs[0]["end"])
    return f"{a.month:02d}-{a.day:02d}", f"{b.month:02d}-{b.day:02d}"

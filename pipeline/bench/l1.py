# Written with an SI tool (Claude, Anthropic) for NASA Space Apps 2026. See AI_USE.md.
"""L1, data: how far are POWER and IMERG from the stations?

Londrina, INMET and IDR-Parana stations (plan, section 11.2). Rule, read from
``protocol.tests.L1``:

- rain: bias of each season total, 100 x (product - station) / station, within
  ``rain_bias_max_pct_per_season`` (10%), in absolute value, inclusive ("up to");
- temperature: mean of the daily differences product - station over the paired
  days, within ``temperature_bias_max_c`` (1 degree C), in absolute value,
  inclusive. The key has no "per season", so the bias is taken over the whole
  compared period, one per variable and station.

Both sides are summed on the same days only: a day missing on either side is
left out of both. A series outside the limits is labelled, never corrected in
silence: the status is "fail" and ``flags`` names each series. The season
boundaries come from the caller [DECIDE: which season, see the report].
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from fractions import Fraction
from typing import Any

from pipeline.bench import common, stats
from pipeline.bench.common import FAIL, PASS


@dataclass(frozen=True)
class L1Rule:
    site: str
    stations: tuple[str, ...]
    rain_bias_max_pct: Fraction
    temperature_bias_max_c: Fraction

    @classmethod
    def from_protocol(cls, protocol: Mapping[str, Any]) -> "L1Rule":
        """Reads ``protocol.tests.L1``."""
        sec = common.protocol_section(protocol, "L1")
        where = "tests.L1"
        site = common.require(sec, "site", where)
        stations = common.require(sec, "stations", where)
        if not isinstance(site, str) or not site:
            raise common.ProtocolError(f"protocol.{where}.site must be a site id")
        if not isinstance(stations, (list, tuple)) or not stations or not all(isinstance(s, str) for s in stations):
            raise common.ProtocolError(f"protocol.{where}.stations must list the stations")
        limits = []
        for key in ("rain_bias_max_pct_per_season", "temperature_bias_max_c"):
            try:
                value = stats.exact(common.require(sec, key, where))
            except (TypeError, ValueError) as exc:
                raise common.ProtocolError(f"protocol.{where}.{key}: {exc}") from exc
            if value <= 0:
                raise common.ProtocolError(f"protocol.{where}.{key} must be positive")
            limits.append(value)
        return cls(site=site, stations=tuple(stations), rain_bias_max_pct=limits[0],
                   temperature_bias_max_c=limits[1])


# Pairing ---------------------------------------------------------------------------

def _value(v: Any) -> Fraction | None:
    try:
        return stats.exact(v)
    except (TypeError, ValueError):
        return None  # missing day, NaN or the -999 fill already turned into None


def paired_days(product: Mapping[date, Any], station: Mapping[date, Any]) -> list[tuple[date, Fraction, Fraction]]:
    """Days present, with a number, on both sides, in date order."""
    out = []
    for d in sorted(set(product) & set(station)):
        a, b = _value(product[d]), _value(station[d])
        if a is not None and b is not None:
            out.append((d, a, b))
    return out


def season_totals(
    product: Mapping[date, Any],
    station: Mapping[date, Any],
    seasons: Mapping[str, tuple[date, date]],
) -> dict[str, tuple[Fraction, Fraction, int]]:
    """(product total, station total, paired days) per season, inclusive bounds."""
    days = paired_days(product, station)
    out = {}
    for name, (first, last) in seasons.items():
        if last < first:
            raise ValueError(f"season {name} ends before it starts")
        inside = [(a, b) for d, a, b in days if first <= d <= last]
        out[name] = (sum((a for a, _ in inside), Fraction(0)), sum((b for _, b in inside), Fraction(0)), len(inside))
    return out


def mean_difference(product: Mapping[date, Any], station: Mapping[date, Any]) -> tuple[Fraction, int]:
    """Mean of product - station over the paired days, and the number of days."""
    days = paired_days(product, station)
    if not days:
        raise ValueError("no paired days")
    return sum((a - b for _, a, b in days), Fraction(0)) / len(days), len(days)


# Records -----------------------------------------------------------------------

@dataclass(frozen=True)
class RainSeason:
    product: str        # PRECTOTCORR or IMERG_PRECTOT
    station: str
    season: str
    product_mm: Any
    station_mm: Any
    n_days: int

    @property
    def bias_pct(self) -> Fraction:
        station = stats.exact(self.station_mm)
        if station <= 0:
            raise ValueError(f"{self.station} {self.season}: station total must be positive to give a bias")
        return 100 * (stats.exact(self.product_mm) - station) / station


@dataclass(frozen=True)
class TemperatureBias:
    product: str        # for example POWER
    variable: str       # for example T2M_MAX
    station: str
    mean_bias_c: Any
    n_days: int


def rain_records(product: str, station: str, totals: Mapping[str, tuple[Fraction, Fraction, int]]) -> list[RainSeason]:
    return [RainSeason(product, station, s, p, st, n) for s, (p, st, n) in totals.items()]


@dataclass(frozen=True)
class L1Result:
    status: str
    site: str
    rain: list[dict[str, Any]]
    temperature: list[dict[str, Any]]
    flags: list[str]
    stations_used: list[str]
    stations_missing: list[str]
    rain_bias_max_pct: Fraction
    temperature_bias_max_c: Fraction
    max_abs_rain_bias_pct: Fraction | None
    max_abs_temperature_bias_c: Fraction | None
    notes: list[str] = field(default_factory=list)

    @property
    def n_seasons(self) -> int:
        return len(self.rain)

    def to_dict(self) -> dict[str, Any]:
        def f(x):
            return None if x is None else float(x)
        return {
            "id": "L1",
            "status": self.status,
            "statistic": "rain_bias_pct_per_season_and_temperature_bias_c",
            "site": self.site,
            "n": self.n_seasons,
            "rain": self.rain,
            "temperature": self.temperature,
            "flags": list(self.flags),
            "stations_used": list(self.stations_used),
            "stations_missing": list(self.stations_missing),
            "rain_bias_max_pct_per_season": float(self.rain_bias_max_pct),
            "temperature_bias_max_c": float(self.temperature_bias_max_c),
            "max_abs_rain_bias_pct": f(self.max_abs_rain_bias_pct),
            "max_abs_temperature_bias_c": f(self.max_abs_temperature_bias_c),
            "notes": list(self.notes),
        }


def evaluate(site: str, rain: Sequence[RainSeason], temperature: Sequence[TemperatureBias], rule: L1Rule) -> L1Result:
    """L1 on season rain totals and temperature biases, already paired by day."""
    if site != rule.site:
        raise ValueError(f"L1 runs at {rule.site!r} (protocol), got {site!r}")
    if not rain and not temperature:
        raise ValueError("L1 has no station comparison")
    stations = {r.station for r in rain} | {t.station for t in temperature}
    unknown = sorted(stations - set(rule.stations))
    if unknown:
        raise ValueError(f"stations outside protocol.tests.L1.stations: {unknown}")
    common.check_keys([(f"{r.product}|{r.station}", r.season) for r in rain])
    common.check_keys([(f"{t.product}|{t.station}", t.variable) for t in temperature])

    rain_out, flags = [], []
    bad_rain: dict[tuple[str, str], list[str]] = {}
    max_rain = None
    for r in rain:
        if r.n_days <= 0:
            raise ValueError(f"{r.product} {r.station} {r.season}: no paired days")
        b = r.bias_pct
        ok = abs(b) <= rule.rain_bias_max_pct
        max_rain = abs(b) if max_rain is None else max(max_rain, abs(b))
        if not ok:
            bad_rain.setdefault((r.product, r.station), []).append(r.season)
        rain_out.append({"product": r.product, "station": r.station, "season": r.season,
                         "product_mm": float(stats.exact(r.product_mm)),
                         "station_mm": float(stats.exact(r.station_mm)),
                         "n_days": r.n_days, "bias_pct": float(b), "within": ok})
    n_seasons = {}
    for r in rain:
        n_seasons[(r.product, r.station)] = n_seasons.get((r.product, r.station), 0) + 1
    for (product, station), seasons in bad_rain.items():
        flags.append(f"{product} against {station}: rain bias above {float(rule.rain_bias_max_pct):g}% "
                     f"in {len(seasons)} of {n_seasons[(product, station)]} seasons ({', '.join(seasons)})")

    temp_out = []
    max_temp = None
    for t in temperature:
        if t.n_days <= 0:
            raise ValueError(f"{t.product} {t.variable} {t.station}: no paired days")
        b = stats.exact(t.mean_bias_c)
        ok = abs(b) <= rule.temperature_bias_max_c
        max_temp = abs(b) if max_temp is None else max(max_temp, abs(b))
        if not ok:
            flags.append(f"{t.product} {t.variable} against {t.station}: mean bias {float(b):+.2f} C, "
                         f"limit {float(rule.temperature_bias_max_c):g} C")
        temp_out.append({"product": t.product, "variable": t.variable, "station": t.station,
                         "n_days": t.n_days, "mean_bias_c": float(b), "within": ok})

    used = [s for s in rule.stations if s in stations]
    missing = [s for s in rule.stations if s not in stations]
    notes = [f"no comparison with {s}" for s in missing]
    if flags:
        notes.append("series outside the limits are labelled, never corrected in silence")
    return L1Result(
        status=FAIL if flags else PASS,
        site=site,
        rain=rain_out,
        temperature=temp_out,
        flags=flags,
        stations_used=used,
        stations_missing=missing,
        rain_bias_max_pct=rule.rain_bias_max_pct,
        temperature_bias_max_c=rule.temperature_bias_max_c,
        max_abs_rain_bias_pct=max_rain,
        max_abs_temperature_bias_c=max_temp,
        notes=notes,
    )

# Written with an SI tool (Claude, Anthropic) for NASA Space Apps 2026. See AI_USE.md.
"""Weather input for the engine.

Two sources only: the sample files shipped with aquacrop (tests and spike)
and the NASA POWER daily table that ``pipeline.adapters.power.read`` returns
(one row per day, one column per parameter, the fill value already NaN).

POWER rules (plan 10.6, sheet M1, criteria M1.3 and M4.3):

- -999 is missing, never zero. A missing day is never filled in silence: any
  gap raises ``WeatherGapError`` unless the caller passes an explicit
  ``fill_max_days``, and rain is never filled at all.
- Rain is PRECTOTCORR (MERRA-2 corrected). IMERG_PRECTOT is a check outside
  the engine.
- The source of each stretch is required and copied to the report. A stretch
  from GEOS-IT inside the window raises: the engine series ends by
  2025-12-31 with no GEOS-IT stretch.
- ET0 is FAO-56 Penman-Monteith (Allen et al. 1998, eq. 6), daily, G = 0.
"""
from __future__ import annotations

from datetime import date
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd

# aquacrop reads the table by position (core.py turns it into an array:
# 0 MinTemp, 1 MaxTemp, 2 Precipitation, 3 ReferenceET, 4 Date) and only
# checks that the names exist. Any other order runs silently with swapped
# variables, so check_weather returns exactly this order.
COLUMNS = ("MinTemp", "MaxTemp", "Precipitation", "ReferenceET", "Date")

RAIN = "PRECTOTCORR"
POWER_PARAMETERS = ("T2M_MAX", "T2M_MIN", "T2MDEW", "WS2M", "ALLSKY_SFC_SW_DWN", RAIN)
FILL = -999.0
# Units of the AG community daily point API, as the response header lists
# them per parameter. Radiation also accepts kW-hr/m^2/day (other communities).
POWER_AG_UNITS = {
    "T2M_MAX": "C", "T2M_MIN": "C", "T2MDEW": "C", "WS2M": "m/s",
    "ALLSKY_SFC_SW_DWN": "MJ/m^2/day", RAIN: "mm/day",
}
RADIATION_TO_MJ = {"MJ/m^2/day": 1.0, "kW-hr/m^2/day": 3.6}
FORBIDDEN_SOURCES = ("GEOSIT",)  # M4.3: no GEOS-IT stretch in the engine series
# Floor that aquacrop's own prepare_weather applies (divide by zero in the
# model). Kept so both weather paths agree. Every clipped day is reported.
ET0_FLOOR_MM = 0.1

# FAO-56 constants.
GSC = 0.0820            # solar constant, MJ m-2 min-1
SIGMA = 4.903e-9        # Stefan-Boltzmann, MJ K-4 m-2 day-1
ALBEDO = 0.23           # grass reference


class WeatherError(ValueError):
    pass


class WeatherGapError(WeatherError):
    def __init__(self, gaps: list[dict]):
        self.gaps = gaps
        head = ", ".join(f"{g['parameter']} {g['start']}..{g['end']}" for g in gaps[:5])
        more = f" and {len(gaps) - 5} more" if len(gaps) > 5 else ""
        super().__init__(f"missing weather, never filled in silence: {head}{more}")


# Sample climate -------------------------------------------------------------

def sample_weather(name: str = "cordoba_climate.txt") -> pd.DataFrame:
    """Package sample. Cordoba (Argentina) is the only southern-hemisphere one."""
    from aquacrop.utils import get_filepath, prepare_weather

    return check_weather(prepare_weather(get_filepath(name)))


def check_weather(df: pd.DataFrame) -> pd.DataFrame:
    missing = [c for c in COLUMNS if c not in df]
    if missing:
        raise ValueError(f"weather missing columns {missing}")
    if df[list(COLUMNS)].isna().any().any():
        raise ValueError("weather has NaN")
    step = df["Date"].diff().dropna().dt.days
    if not (step == 1).all():
        raise ValueError("weather is not a continuous daily series")
    return df[list(COLUMNS)].reset_index(drop=True)


# FAO-56 ---------------------------------------------------------------------

def sat_vapour_pressure(t_c):
    """e°(T), kPa (eq. 11)."""
    t_c = np.asarray(t_c, dtype=float)
    return 0.6108 * np.exp(17.27 * t_c / (t_c + 237.3))


def extraterrestrial_radiation(lat_deg: float, doy) -> np.ndarray:
    """Ra, MJ m-2 day-1 (eqs. 21 to 25). South latitudes are negative."""
    phi = np.deg2rad(lat_deg)
    j = np.asarray(doy, dtype=float)
    dr = 1 + 0.033 * np.cos(2 * np.pi * j / 365)
    delta = 0.409 * np.sin(2 * np.pi * j / 365 - 1.39)
    ws = np.arccos(np.clip(-np.tan(phi) * np.tan(delta), -1.0, 1.0))
    return (24 * 60 / np.pi) * GSC * dr * (
        ws * np.sin(phi) * np.sin(delta) + np.cos(phi) * np.cos(delta) * np.sin(ws))


def et0_fao56(tmax, tmin, tdew, u2, rs, *, lat_deg: float, elevation_m: float,
              doy) -> dict[str, np.ndarray]:
    """Daily grass reference ET0 (mm/day) and its terms.

    tmax, tmin, tdew in °C at 2 m, u2 wind at 2 m (m/s), rs solar radiation
    (MJ m-2 day-1). Actual vapour pressure from dew point (eq. 14). Rs/Rso is
    capped at 1.0 in the long-wave term (eq. 39 note). G = 0 for daily steps.
    """
    tmax, tmin, tdew, u2, rs = (np.asarray(x, dtype=float) for x in (tmax, tmin, tdew, u2, rs))
    pressure = 101.3 * ((293 - 0.0065 * elevation_m) / 293) ** 5.26      # eq. 7
    gamma = 0.665e-3 * pressure                                          # eq. 8
    tmean = (tmax + tmin) / 2                                            # eq. 9
    es = (sat_vapour_pressure(tmax) + sat_vapour_pressure(tmin)) / 2     # eq. 12
    ea = sat_vapour_pressure(tdew)                                       # eq. 14
    slope = 4098 * sat_vapour_pressure(tmean) / (tmean + 237.3) ** 2     # eq. 13
    ra = extraterrestrial_radiation(lat_deg, doy)
    rso = (0.75 + 2e-5 * elevation_m) * ra                               # eq. 37
    ratio = np.minimum(rs / rso, 1.0)
    rns = (1 - ALBEDO) * rs                                              # eq. 38
    rnl = SIGMA * ((tmax + 273.16) ** 4 + (tmin + 273.16) ** 4) / 2 \
        * (0.34 - 0.14 * np.sqrt(ea)) * (1.35 * ratio - 0.35)            # eq. 39
    rn = rns - rnl                                                       # eq. 40
    et0 = (0.408 * slope * rn + gamma * 900 / (tmean + 273) * u2 * (es - ea)) \
        / (slope + gamma * (1 + 0.34 * u2))                              # eq. 6
    return {"et0": et0, "ra": ra, "rso": rso, "rn": rn, "rnl": rnl, "es": es, "ea": ea,
            "slope": slope, "gamma": np.full_like(et0, gamma)}


# POWER table ----------------------------------------------------------------

def climate_window(protocol: Mapping[str, Any]) -> tuple[date, date]:
    """protocol.dates.climate, the only climate the engine may see."""
    try:
        first, last = protocol["dates"]["climate"]
    except (KeyError, TypeError, ValueError) as e:
        raise KeyError("protocol.dates.climate missing: pass the window explicitly") from e
    return date.fromisoformat(str(first)), date.fromisoformat(str(last))


def missing_runs(df: pd.DataFrame, columns: Sequence[str]) -> list[dict]:
    """Runs of NaN per column: [{parameter, start, end, days}]."""
    out = []
    for c in columns:
        miss = df[c].isna().to_numpy()
        if not miss.any():
            continue
        idx = df.index
        edges = np.flatnonzero(np.diff(np.concatenate(([0], miss.astype(int), [0]))))
        for a, b in zip(edges[::2], edges[1::2]):
            out.append({"parameter": c, "start": idx[a].date().isoformat(),
                        "end": idx[b - 1].date().isoformat(), "days": int(b - a)})
    return out


def check_units(units: Mapping[str, str]) -> dict[str, float]:
    """Unit check against the AG units. Returns the factor per parameter."""
    factors = {}
    for p in POWER_PARAMETERS:
        if p not in units:
            raise WeatherError(f"unit of {p} not given")
        u = str(units[p]).strip()
        if p == "ALLSKY_SFC_SW_DWN" and u in RADIATION_TO_MJ:
            factors[p] = RADIATION_TO_MJ[u]
        elif u == POWER_AG_UNITS[p]:
            factors[p] = 1.0
        else:
            raise WeatherError(f"{p} in {u!r}, expected {POWER_AG_UNITS[p]!r}")
    return factors


def source_stretches(segments: Sequence[Mapping[str, Any]], parameters: Sequence[str],
                     first: date, last: date,
                     forbidden: Sequence[str] = FORBIDDEN_SOURCES) -> list[dict]:
    """Stretches of ``power.source_segments`` clipped to the window.

    Every parameter must be covered day by day, and no stretch inside the
    window may come from a forbidden source.
    """
    out = []
    for p in parameters:
        segs = sorted((s for s in segments if s.get("parameter") == p), key=lambda s: s["start"])
        cursor = first
        for s in segs:
            a, b = date.fromisoformat(s["start"]), date.fromisoformat(s["end"])
            if b < first or a > last:
                continue
            if a > cursor:
                raise WeatherError(f"{p}: no source recorded for {cursor}..{a}")
            lo, hi = max(a, first), min(b, last)
            sources = [str(x) for x in s.get("sources") or ()]
            if not sources:
                raise WeatherError(f"{p}: empty source list for {lo}..{hi}")
            bad = [x for x in sources if any(f.upper() in x.upper() for f in forbidden)]
            if bad:
                raise WeatherError(f"{p}: forbidden source {bad} in {lo}..{hi}")
            out.append({"parameter": p, "start": lo.isoformat(), "end": hi.isoformat(),
                        "sources": sources})
            cursor = max(cursor, date.fromordinal(hi.toordinal() + 1))
        if cursor <= last:
            raise WeatherError(f"{p}: no source recorded for {cursor}..{last}")
    return out


def _fill_short_gaps(df: pd.DataFrame, columns: Sequence[str], max_days: int) -> list[dict]:
    """Linear in time, only runs of at most max_days, never at the series edges."""
    filled = []
    for g in missing_runs(df, columns):
        a, b = pd.Timestamp(g["start"]), pd.Timestamp(g["end"])
        inside = a > df.index[0] and b < df.index[-1]
        if g["days"] <= max_days and inside:
            filled.append(g)
    for g in filled:
        col = g["parameter"]
        lo = pd.Timestamp(g["start"]) - pd.Timedelta(days=1)
        hi = pd.Timestamp(g["end"]) + pd.Timedelta(days=1)
        df.loc[lo:hi, col] = df.loc[lo:hi, col].interpolate(method="time")
    return filled


def power_to_ospy(power: pd.DataFrame, *, latitude_deg: float, elevation_m: float,
                  window: tuple[date, date], segments: Sequence[Mapping[str, Any]],
                  units: Mapping[str, str], fill_max_days: int | None = None,
                  downloaded_at: str | None = None) -> tuple[pd.DataFrame, dict]:
    """POWER daily table to the aquacrop weather input, plus a report.

    ``window`` is the protocol climate window or a part of it, and the table
    must cover it day by day. ``segments`` is ``power.source_segments`` for
    the same request. ``fill_max_days`` None (the plan rule) raises on any
    gap. An integer interpolates gaps of at most that many days in the
    non-rain parameters and lists every filled day. [DECIDE] if ever used.
    """
    if not -90 <= latitude_deg <= 90:
        raise WeatherError(f"latitude {latitude_deg} out of range")
    first, last = window
    if last < first:
        raise WeatherError(f"empty window {first}..{last}")
    missing_cols = [p for p in POWER_PARAMETERS if p not in power]
    if missing_cols:
        raise WeatherError(f"POWER table missing {missing_cols}")
    factors = check_units(units)
    if not isinstance(power.index, pd.DatetimeIndex):
        raise WeatherError("POWER table must be indexed by date")
    days = pd.date_range(first, last, freq="D")
    if power.index.has_duplicates:
        raise WeatherError("POWER table has duplicate dates")
    absent = days.difference(power.index)
    if len(absent):
        raise WeatherError(f"POWER table lacks {len(absent)} days of the window, "
                           f"first {absent[0].date()}")
    df = power.loc[days, list(POWER_PARAMETERS)].astype(float).copy()
    df = df.where(~np.isclose(df, FILL), np.nan)  # -999 left by any path is missing
    for p, f in factors.items():
        df[p] = df[p] * f

    filled: list[dict] = []
    if fill_max_days is not None:
        if int(fill_max_days) != fill_max_days or fill_max_days < 1:
            raise WeatherError("fill_max_days must be a positive integer or None")
        filled = _fill_short_gaps(df, [p for p in POWER_PARAMETERS if p != RAIN], int(fill_max_days))
    gaps = missing_runs(df, POWER_PARAMETERS)
    if gaps:
        raise WeatherGapError(gaps)

    if (df["T2M_MIN"] > df["T2M_MAX"]).any():
        bad = df.index[df["T2M_MIN"] > df["T2M_MAX"]]
        raise WeatherError(f"T2M_MIN above T2M_MAX on {len(bad)} days, first {bad[0].date()}")
    for p in (RAIN, "WS2M", "ALLSKY_SFC_SW_DWN"):
        if (df[p] < 0).any():
            raise WeatherError(f"negative {p}")

    stretches = source_stretches(segments, POWER_PARAMETERS, first, last)
    terms = et0_fao56(df["T2M_MAX"], df["T2M_MIN"], df["T2MDEW"], df["WS2M"],
                      df["ALLSKY_SFC_SW_DWN"], lat_deg=latitude_deg, elevation_m=elevation_m,
                      doy=df.index.day_of_year)
    et0 = terms["et0"]
    clipped = df.index[et0 < ET0_FLOOR_MM]
    out = pd.DataFrame({
        "MinTemp": df["T2M_MIN"].to_numpy(),
        "MaxTemp": df["T2M_MAX"].to_numpy(),
        "Precipitation": df[RAIN].to_numpy(),
        "ReferenceET": np.maximum(et0, ET0_FLOOR_MM),
        "Date": df.index,
    })
    report = {
        "window": [first.isoformat(), last.isoformat()],
        "days": int(len(out)),
        "rain": RAIN,
        "et0": "FAO-56 Penman-Monteith, daily, G = 0, ea from T2MDEW",
        "latitude_deg": float(latitude_deg),
        "elevation_m": float(elevation_m),
        "units": {p: str(units[p]) for p in POWER_PARAMETERS},
        "source_stretches": stretches,
        "downloaded_at": downloaded_at,
        "filled": filled,
        "fill_max_days": fill_max_days,
        "et0_floor_mm": ET0_FLOOR_MM,
        "et0_clipped_days": [d.date().isoformat() for d in clipped],
        "ea_above_es_days": int((terms["ea"] > terms["es"]).sum()),
    }
    return check_weather(out), report


# P1 ring ----------------------------------------------------------------------

def ring_weather(weather: pd.DataFrame, years: Mapping[int, int]) -> pd.DataFrame:
    """Engine weather for a shifted P1 chain (``protocol.chains.trial.p1_warmup``).

    ``years`` maps each calendar year before the ring to its climate year. Those years,
    and any real rows they cover, are replaced by a copy of the climate year with the
    dates relabelled. Both years must have the same number of days: 29 February is
    never invented or dropped. The rest of the table is left as it is.
    """
    if not years:
        return weather
    df = check_weather(weather)
    when = pd.DatetimeIndex(df["Date"])
    parts = []
    for y in sorted(years):
        c = int(years[y])
        if pd.Timestamp(y, 12, 31).day_of_year != pd.Timestamp(c, 12, 31).day_of_year:
            raise WeatherError(f"ring year {y} and climate year {c} differ in length")
        part = df.loc[when.year == c].copy()
        if len(part) != pd.Timestamp(c, 12, 31).day_of_year:
            raise WeatherError(f"climate year {c} is not complete in the table")
        part["Date"] = pd.DatetimeIndex(part["Date"]) + pd.DateOffset(years=y - c)
        parts.append(part)
    keep = df.loc[~when.year.isin(list(years))]
    out = pd.concat(parts + [keep]).sort_values("Date").reset_index(drop=True)
    return check_weather(out)


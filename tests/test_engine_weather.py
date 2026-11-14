# Written with an SI tool (Claude, Anthropic) for NASA Space Apps 2026. See AI_USE.md.
"""POWER daily table to the engine weather input (plan 10.6, M1.3, M4.3).

Synthetic data only: the fixture tests/fixtures/engine_power_synthetic.csv
is invented, and FAO-56 worked examples check the ET0 arithmetic. The one
engine run uses a generated series at a latitude away from every trial site.

Run: .venv-engine/bin/python -m pytest tests/test_engine_weather.py -q
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pipeline.engine import weather as W  # noqa: E402

FIXTURE = ROOT / "tests" / "fixtures" / "engine_power_synthetic.csv"
WINDOW = (date(2001, 1, 1), date(2001, 1, 21))
LAT, ELEV = -30.0, 100.0  # synthetic, no trial site


def power_table(path=FIXTURE) -> pd.DataFrame:
    df = pd.read_csv(path, parse_dates=["date"]).set_index("date")
    return df.astype(float)


def segments(first="1984-01-01", last="2025-12-31", sources=("MERRA2",)):
    return [{"parameter": p, "start": first, "end": last, "sources": list(sources)}
            for p in W.POWER_PARAMETERS]


def convert(df=None, **kw):
    args = dict(latitude_deg=LAT, elevation_m=ELEV, window=WINDOW, segments=segments(),
                units=W.POWER_AG_UNITS)
    args.update(kw)
    return W.power_to_ospy(power_table() if df is None else df, **args)


def _tdew(ea):
    x = np.log(ea / 0.6108)
    return 237.3 * x / (17.27 - x)


def test_fao56_example_18_daily_et0():
    # Allen et al. 1998, example 18: Uccle, 6 July, 50°48'N, 100 m.
    t = W.et0_fao56(21.5, 12.3, _tdew(1.409), 2.078, 22.07, lat_deg=50.8, elevation_m=100, doy=187)
    assert t["ra"] == pytest.approx(41.09, abs=0.05)
    assert t["rso"] == pytest.approx(30.90, abs=0.05)
    assert t["rnl"] == pytest.approx(3.71, abs=0.02)
    assert t["rn"] == pytest.approx(13.28, abs=0.03)
    assert t["et0"] == pytest.approx(3.9, abs=0.05)


def test_fao56_example_8_southern_ra():
    # Example 8: 3 September, 20°S, Ra = 32.2 MJ m-2 day-1.
    assert W.extraterrestrial_radiation(-20.0, 246) == pytest.approx(32.2, abs=0.05)


def test_fixture_converts_to_ospy_columns():
    out, rep = convert()
    assert list(out.columns) == list(W.COLUMNS)
    assert len(out) == 21 and rep["days"] == 21
    src = power_table()
    assert np.array_equal(out["Precipitation"].to_numpy(), src["PRECTOTCORR"].to_numpy())
    assert np.array_equal(out["MaxTemp"].to_numpy(), src["T2M_MAX"].to_numpy())
    assert (out["ReferenceET"] >= W.ET0_FLOOR_MM).all()
    assert 2.0 < out["ReferenceET"].mean() < 8.0
    assert rep["rain"] == "PRECTOTCORR" and rep["filled"] == []
    assert {s["parameter"] for s in rep["source_stretches"]} == set(W.POWER_PARAMETERS)
    assert all(s["start"] == "2001-01-01" and s["end"] == "2001-01-21" for s in rep["source_stretches"])


def test_missing_rain_raises_never_zero():
    df = power_table()
    df.loc["2001-01-05", "PRECTOTCORR"] = np.nan
    with pytest.raises(W.WeatherGapError) as e:
        convert(df)
    assert e.value.gaps == [{"parameter": "PRECTOTCORR", "start": "2001-01-05",
                             "end": "2001-01-05", "days": 1}]
    with pytest.raises(W.WeatherGapError):  # rain is never filled, even when asked
        convert(df, fill_max_days=3)


def test_fill_value_left_in_table_is_missing():
    df = power_table()
    df.loc["2001-01-07", "T2M_MAX"] = W.FILL
    with pytest.raises(W.WeatherGapError):
        convert(df)


def test_explicit_fill_only_short_inner_gaps_and_listed():
    df = power_table()
    df.loc["2001-01-08":"2001-01-09", "ALLSKY_SFC_SW_DWN"] = np.nan
    out, rep = convert(df, fill_max_days=2)
    assert rep["filled"] == [{"parameter": "ALLSKY_SFC_SW_DWN", "start": "2001-01-08",
                              "end": "2001-01-09", "days": 2}]
    with pytest.raises(W.WeatherGapError):
        convert(df, fill_max_days=1)
    edge = power_table()
    edge.loc["2001-01-01", "WS2M"] = np.nan  # no neighbour on one side: never filled
    with pytest.raises(W.WeatherGapError):
        convert(edge, fill_max_days=5)


def test_geosit_stretch_inside_window_raises():
    segs = segments(last="2000-12-31") + [
        {"parameter": p, "start": "2001-01-01", "end": "2025-12-31", "sources": ["GEOSIT"]}
        for p in W.POWER_PARAMETERS]
    with pytest.raises(W.WeatherError, match="forbidden source"):
        convert(segments=segs)


def test_source_must_cover_every_day():
    segs = [s for s in segments(last="2001-01-10")]
    with pytest.raises(W.WeatherError, match="no source recorded"):
        convert(segments=segs)
    two = segments(last="2001-01-10") + segments(first="2001-01-11", sources=("MERRA2", "SRB"))
    _, rep = convert(segments=two)
    rain = [s for s in rep["source_stretches"] if s["parameter"] == "PRECTOTCORR"]
    assert [(s["start"], s["end"]) for s in rain] == [("2001-01-01", "2001-01-10"),
                                                      ("2001-01-11", "2001-01-21")]


def test_units_checked_and_radiation_converted():
    df = power_table()
    df["ALLSKY_SFC_SW_DWN"] = df["ALLSKY_SFC_SW_DWN"] / 3.6
    units = {**W.POWER_AG_UNITS, "ALLSKY_SFC_SW_DWN": "kW-hr/m^2/day"}
    a, _ = convert()
    b, _ = convert(df, units=units)
    assert np.allclose(a["ReferenceET"], b["ReferenceET"])
    with pytest.raises(W.WeatherError):
        convert(units={**W.POWER_AG_UNITS, "WS2M": "km/h"})


def test_window_must_be_covered_and_consistent():
    with pytest.raises(W.WeatherError, match="lacks"):
        convert(window=(date(2000, 12, 31), date(2001, 1, 21)))
    df = power_table()
    df.loc["2001-01-03", "T2M_MIN"] = 40.0
    with pytest.raises(W.WeatherError, match="T2M_MIN above"):
        convert(df)


def test_et0_floor_reported():
    df = power_table()
    # Dew point above Tmax with strong wind: negative Penman-Monteith ET0.
    df.loc["2001-01-04", "T2MDEW"] = df.loc["2001-01-04", "T2M_MAX"] + 5.0
    df.loc["2001-01-04", ["WS2M", "ALLSKY_SFC_SW_DWN"]] = [10.0, 8.0]
    out, rep = convert(df)
    assert rep["et0_clipped_days"] == ["2001-01-04"]
    assert rep["ea_above_es_days"] == 1
    assert out.loc[3, "ReferenceET"] == W.ET0_FLOOR_MM


def test_column_order_is_the_one_aquacrop_reads_by_position():
    out, _ = convert()
    assert list(out.columns) == ["MinTemp", "MaxTemp", "Precipitation", "ReferenceET", "Date"]
    shuffled = out[["Date", "MaxTemp", "MinTemp", "ReferenceET", "Precipitation"]]
    assert list(W.check_weather(shuffled).columns) == list(W.COLUMNS)


def test_climate_window_reads_protocol_or_raises():
    proto = {"dates": {"climate": ["1984-01-01", "2025-12-31"]}}
    assert W.climate_window(proto) == (date(1984, 1, 1), date(2025, 12, 31))
    with pytest.raises(KeyError):
        W.climate_window({})


def synthetic_power(first="2000-08-01", last="2002-07-31") -> pd.DataFrame:
    days = pd.date_range(first, last, freq="D")
    c = np.cos(2 * np.pi * (days.day_of_year - 15) / 365)  # southern summer peak
    summer = c > 0
    rain = np.where(summer, np.where(days.day % 2 == 0, 12.0, 0.0),
                    np.where(days.day % 10 == 0, 5.0, 0.0))
    return pd.DataFrame({
        "T2M_MAX": 26.0 + 5.0 * c, "T2M_MIN": 15.0 + 4.0 * c, "T2MDEW": 12.0 + 4.0 * c,
        "WS2M": 2.0, "ALLSKY_SFC_SW_DWN": 17.0 + 5.0 * c, "PRECTOTCORR": rain,
    }, index=days)


def test_engine_runs_on_converted_synthetic_weather():
    pytest.importorskip("aquacrop")
    from pipeline.engine.chain import Season, run_season
    from pipeline.engine.soilmodel import CLAY_TEST_LAYERS

    df = synthetic_power()
    out, _ = W.power_to_ospy(df, latitude_deg=LAT, elevation_m=ELEV,
                             window=(date(2000, 8, 1), date(2002, 7, 31)),
                             segments=segments(), units=W.POWER_AG_UNITS)
    res = run_season(out, CLAY_TEST_LAYERS, Season("soybean", date(2000, 11, 1)),
                     date(2000, 10, 1), None)
    assert res.matured and res.harvest > res.sowing and res.canopy_max > 0
    assert res.rain_mm == pytest.approx(out.loc[(out["Date"] >= "2000-10-01")
                                                & (out["Date"] <= pd.Timestamp(res.harvest)),
                                                "Precipitation"].sum())
    assert abs(res.balance_residual_mm) < 1e-6


# P1 ring (protocol.chains.trial.p1_warmup) ----------------------------------------------

def _engine_table(first: str, last: str) -> pd.DataFrame:
    days = pd.date_range(first, last, freq="D")
    n = np.arange(len(days), dtype=float)
    return pd.DataFrame({"MinTemp": 10 + n % 7, "MaxTemp": 25 + n % 5, "Precipitation": n % 3,
                         "ReferenceET": 3 + n % 2, "Date": days})


def test_ring_weather_copies_whole_climate_years():
    df = _engine_table("1984-01-01", "2025-12-31")      # synthetic numbers, no POWER series
    out = W.ring_weather(df, {1982: 2022, 1983: 2023, 1984: 2024})
    assert out["Date"].iloc[0] == pd.Timestamp("1982-01-01") and out["Date"].iloc[-1] == pd.Timestamp("2025-12-31")
    assert len(out) == len(df) + 365 + 365            # 1982 and 1983 added, real 1984 replaced
    by = out.set_index("Date")
    src = df.set_index("Date")
    assert by.loc["1984-02-29", "MinTemp"] == src.loc["2024-02-29", "MinTemp"]      # leap years agree
    assert (by.loc["1982-01-01":"1982-12-31"].to_numpy() == src.loc["2022-01-01":"2022-12-31"].to_numpy()).all()
    assert (by.loc["1985-01-01":].to_numpy() == src.loc["1985-01-01":].to_numpy()).all()
    assert W.ring_weather(df, {}) is df


def test_ring_weather_never_invents_29_february():
    df = _engine_table("1984-01-01", "2025-12-31")
    with pytest.raises(W.WeatherError, match="length"):
        W.ring_weather(df, {1983: 2024})
    with pytest.raises(W.WeatherError, match="not complete"):
        W.ring_weather(_engine_table("2023-06-01", "2025-12-31"), {1983: 2023})

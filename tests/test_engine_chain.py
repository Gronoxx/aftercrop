# Written with an SI tool (Claude, Anthropic) for NASA Space Apps 2026. See AI_USE.md.
"""Chain mode of the engine on the aquacrop sample climate.

Run: .venv-engine/bin/python -m pytest tests/test_engine_chain.py -q
"""
from __future__ import annotations

import hashlib
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

aquacrop = pytest.importorskip("aquacrop")

from pipeline.engine.chain import (  # noqa: E402
    CONSERVATION_TOL_MM, Season, last_planted_row, run_chain, run_season,
)
from pipeline.engine.crops import make_crop  # noqa: E402
from pipeline.engine.regimes import Setup  # noqa: E402
from pipeline.engine.soilmodel import (  # noqa: E402
    CLAY_TEST_LAYERS, DZ, MIN_DEPTH_M, make_soil, water_limits,
)
from pipeline.engine.weather import sample_weather  # noqa: E402

START = date(2001, 8, 1)
SOY_MAIZE_SOY = [Season("soybean", date(2001, 10, 1)), Season("maize"),
                 Season("soybean", date(2002, 11, 30))]
SOY_WHEAT_SOY = [Season("soybean", date(2001, 10, 1)), Season("wheat", date(2002, 5, 1)),
                 Season("soybean", date(2002, 11, 30))]


@pytest.fixture(scope="module")
def weather():
    return sample_weather()


@pytest.fixture(scope="module")
def maize_chain(weather):
    return run_chain(weather, CLAY_TEST_LAYERS, SOY_MAIZE_SOY, START)


def _digest(chain) -> str:
    h = hashlib.sha256()
    for s in chain.seasons:
        h.update(s.th_end.tobytes())
        h.update(str((s.harvest, s.dry_yield_t_ha)).encode())
    return h.hexdigest()


def test_geometry_fixed():
    soil = make_soil(CLAY_TEST_LAYERS)
    prof = soil.profile
    assert np.allclose(prof["dz"].to_numpy(), DZ)
    assert sum(DZ) >= MIN_DEPTH_M
    assert not prof[["th_wp", "th_fc", "th_s", "Ksat", "Layer"]].isna().any().any()


def test_geometry_holds_for_library_zmax(weather):
    # run_season raises if OSPy stretches the compartments.
    run_season(weather, CLAY_TEST_LAYERS, Season("maize", date(2001, 3, 1), {"Zmax": 2.3}),
               date(2001, 2, 1), None)


def test_crop_overrides_closed():
    crop = make_crop("soybean", date(2001, 10, 1), Maturity=2500.0)
    assert crop.Maturity == 2500.0 and crop.Zmax == 1.0
    assert (crop.Tmax_lo, crop.Tmax_up) == (40.0, 45.0)
    with pytest.raises(KeyError):
        make_crop("soybean", date(2001, 10, 1), WP=40.0)


def test_last_planted_row_skips_zero_tail():
    ws = pd.DataFrame({"time_step_counter": [0, 1, 2, 0, 0], "dap": [0, 1, 2, 0, 0],
                       "th1": [0.3, 0.31, 0.32, 0.0, 0.0]})
    assert last_planted_row(ws)["th1"] == 0.32


def test_water_carries_over(maize_chain):
    seasons = maize_chain.seasons
    assert len(seasons) == 3
    for prev, nxt in zip(seasons, seasons[1:]):
        assert nxt.start == date.fromordinal(prev.harvest.toordinal() + 1)
        assert np.allclose(nxt.th_start, prev.th_end, atol=1e-9, rtol=0)
        assert prev.th_end.sum() > 0
    assert all(g <= CONSERVATION_TOL_MM for g in maize_chain.handover_gap_mm)


def test_initial_state_changes_result(weather, maize_chain):
    wp, fc = water_limits(CLAY_TEST_LAYERS)
    dry = run_chain(weather, CLAY_TEST_LAYERS, SOY_MAIZE_SOY, START, wp + 0.25 * (fc - wp))
    wet_end, dry_end = maize_chain.seasons[-1], dry.seasons[-1]
    assert abs(wet_end.storage_end_mm - dry_end.storage_end_mm) > 10
    assert any(abs(a.dry_yield_t_ha - b.dry_yield_t_ha) > 1e-3
               for a, b in zip(maize_chain.seasons, dry.seasons))


def test_predecessor_changes_following_soy_state(weather, maize_chain):
    wheat = run_chain(weather, CLAY_TEST_LAYERS, SOY_WHEAT_SOY, START)
    # Shared prefix is identical, the following soybean starts from different water.
    assert np.array_equal(wheat.seasons[0].th_end, maize_chain.seasons[0].th_end)
    assert not np.allclose(wheat.seasons[2].th_start, maize_chain.seasons[2].th_start)


def test_repeatable(weather, maize_chain):
    again = run_chain(weather, CLAY_TEST_LAYERS, SOY_MAIZE_SOY, START)
    assert _digest(again) == _digest(maize_chain)


def test_ksat_override_only_top_layer():
    layers = ((*CLAY_TEST_LAYERS[0], 1440.0), *CLAY_TEST_LAYERS[1:])
    base, meas = make_soil(CLAY_TEST_LAYERS).profile, make_soil(layers).profile
    top = meas["Layer"] == 1
    assert (meas.loc[top, "Ksat"] == 1440.0).all()
    assert meas.loc[~top, "Ksat"].equals(base.loc[~top, "Ksat"])
    assert meas[["th_wp", "th_fc", "th_s"]].equals(base[["th_wp", "th_fc", "th_s"]])


def test_no_raw_last_row_in_engine():
    pattern = "iloc" + "[-1]"
    for f in (ROOT / "pipeline" / "engine").glob("*.py"):
        assert pattern not in f.read_text(), f.name


# Long fallow (late note, real run R2) -----------------------------------------------------

def _dead_then_long_fallow_weather(weather):
    """Synthetic edit of the sample climate: one wet day after sowing, then a hot dry spell."""
    w = weather.copy()
    dry = (w["Date"] >= "2002-06-01") & (w["Date"] <= "2002-10-31")
    w.loc[dry, "Precipitation"] = 0.0
    w.loc[dry, "ReferenceET"] *= 4.0
    w.loc[dry, "MaxTemp"] = w.loc[dry, "MaxTemp"].clip(lower=30)
    w.loc[w["Date"] == "2002-06-02", "Precipitation"] = 20.0
    return w


def test_anniversary_of_29_february():
    from pipeline.engine.chain import _anniversary
    assert _anniversary(date(2020, 12, 3)) == date(2019, 12, 3)
    assert _anniversary(date(2024, 2, 29)) == date(2023, 2, 28)


def test_dead_first_crop_then_fallow_longer_than_a_year(weather):
    """A crop that dies early leaves a fallow of almost two years before a fixed sowing.

    Before the fix OSPy built two seasons in that instance and the chain raised. Now the
    fallow runs as bare soil in pieces, every day with its water balance, and the chain goes on.
    """
    from pipeline.engine.chain import FALLOW_PIECE_DAYS
    w = _dead_then_long_fallow_weather(weather)
    wp, fc = water_limits(CLAY_TEST_LAYERS, DZ)
    seasons = [Season("maize", date(2002, 6, 1)), Season("soybean", date(2004, 5, 15))]
    chain = run_chain(w, CLAY_TEST_LAYERS, seasons, date(2002, 6, 1), wp.copy())
    dead, soy = chain.seasons
    assert chain.completed and dead.crop_dead and dead.germinated
    assert soy.start == dead.harvest + pd.Timedelta(days=1)
    assert (soy.sowing - soy.start).days > 365 + FALLOW_PIECE_DAYS     # two fallow pieces
    assert soy.sim_days == (soy.harvest - soy.start).days + 1          # no day skipped
    assert abs(soy.storage_start_mm - dead.storage_end_mm) <= CONSERVATION_TOL_MM
    assert np.allclose(soy.th_start, dead.th_end)                      # no reset of soil water
    assert abs(soy.balance_residual_mm) < 1e-6
    rain = w.loc[(w["Date"] >= pd.Timestamp(soy.start)) & (w["Date"] <= pd.Timestamp(soy.harvest)),
                 "Precipitation"].sum()
    assert soy.rain_mm == pytest.approx(rain)
    assert soy.germinated and soy.dry_yield_t_ha >= 0


def test_long_fallow_pieces_match_one_instance(weather):
    """The bare-soil pieces give the same soil water as one OSPy instance over the same days.

    A fallow just under a year needs no piece; the same days run as one piece must end in the
    same water state (the split only moves where the instance starts).
    """
    from pipeline.engine.chain import _fallow_piece
    w = _dead_then_long_fallow_weather(weather)
    wp, fc = water_limits(CLAY_TEST_LAYERS, DZ)
    th0 = wp + 0.5 * (fc - wp)
    start, sowing = date(2003, 1, 10), date(2003, 9, 1)
    one = run_season(w, CLAY_TEST_LAYERS, Season("soybean", sowing), start, th0)
    piece = _fallow_piece(w, CLAY_TEST_LAYERS, Season("soybean", sowing), start,
                          date(2003, 5, 31), th0, DZ, Setup())
    rest = run_season(w, CLAY_TEST_LAYERS, Season("soybean", sowing), date(2003, 6, 1), piece.th_end)
    assert np.allclose(rest.th_end, one.th_end, atol=1e-9)
    assert rest.harvest == one.harvest
    assert rest.dry_yield_t_ha == pytest.approx(one.dry_yield_t_ha, abs=1e-9)

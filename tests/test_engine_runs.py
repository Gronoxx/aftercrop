# Written with an SI tool (Claude, Anthropic) for NASA Space Apps 2026. See AI_USE.md.
"""Regimes run end to end on the aquacrop sample climate (Cordoba), test soil.

Shared prefix and branching, common rain-rule dates, A1 restart, stops,
side runs and soil from texture with a Ksat input. Agronomic numbers here
mean nothing for Parana: only the mechanics are tested.

Run: .venv-engine/bin/python -m pytest tests/test_engine_runs.py -q
"""
from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

aquacrop = pytest.importorskip("aquacrop")

from pipeline.engine.chain import (  # noqa: E402
    CONSERVATION_TOL_MM, CalendarError, restart_profile, run_chain, run_regime, run_season,
)
from pipeline.engine.crops import make_crop  # noqa: E402
from pipeline.engine.regimes import (  # noqa: E402
    AFTER_HARVEST, Restart, Season, Setup, TrialSeason, field_regime, trial_regime,
    trial_warmup, with_restart,
)
from pipeline.engine.soilmodel import (  # noqa: E402
    CLAY_TEST_LAYERS, DZ, MM_H_TO_MM_DAY, make_soil, soilgrids_horizons, texture_layers,
    water_limits,
)
from pipeline.engine.sowing import RainRule, RainSowing, rain_series, trigger  # noqa: E402
from pipeline.engine.weather import sample_weather  # noqa: E402

RULE = RainRule(20.0, 3, "09-01", "12-31", rain_after_not_before=True)
FC = Restart("field_capacity")


def field(y=2003):
    return field_regime(y, RULE, prefix_years=0, corn_sowing=AFTER_HARVEST,
                        wheat_sowing="05-01", s3_gap_days=20, initial=FC)


TRIAL = [
    TrialSeason("2004/05", date(2004, 10, 20), {"wheat": date(2004, 5, 5), "maize": date(2004, 3, 20)}),
    TrialSeason("2005/06", None, {"wheat": date(2005, 5, 20), "maize": date(2005, 5, 10)}),
]


@pytest.fixture(scope="module")
def weather():
    return sample_weather()


@pytest.fixture(scope="module")
def field_run(weather):
    return run_regime(weather, CLAY_TEST_LAYERS, field())


@pytest.fixture(scope="module")
def trial_run(weather):
    regime = trial_regime("site_x", TRIAL, RULE, warmup=trial_warmup("2004/05", RULE), initial=FC)
    return run_regime(weather, CLAY_TEST_LAYERS, regime)


def test_field_arms_branch_from_one_state(field_run):
    s1, s3 = field_run["S1"], field_run["S3"]
    assert s1.completed and s3.completed
    assert s1.shared_prefix == s3.shared_prefix == 1
    assert s1.seasons[0] is s3.seasons[0]
    assert np.array_equal(s1.seasons[1].th_start, s3.seasons[1].th_start)
    assert [s.crop for s in s1.seasons] == ["soybean", "maize", "soybean"]
    assert [s.crop for s in s3.seasons] == ["soybean", "wheat", "soybean"]
    for c in (s1, s3):
        assert all(g is not None and g <= CONSERVATION_TOL_MM for g in c.handover_gap_mm)
        target = c.seasons[-1]
        assert target.role == "target" and target.sowing_source == "rain_rule"
        assert target.sowing_window == (date(2003, 9, 1), date(2003, 12, 31))
    wheat_harvest = s3.seasons[1].harvest
    assert s3.seasons[-1].sowing >= wheat_harvest + timedelta(days=20)


def test_season_water_balance_closes(field_run, trial_run):
    for out in (field_run, trial_run):
        for chain in out.values():
            for s in chain.seasons:
                assert abs(s.balance_residual_mm) < 1e-6, s.label


def test_trial_keeps_published_dates_and_shares_missing_one(weather, trial_run):
    w, m = trial_run["after_wheat"], trial_run["after_maize"]
    assert w.completed and m.completed
    assert w.shared_prefix == m.shared_prefix == 3
    assert [s.label for s in w.seasons][3:] == ["2004/05:wheat", "2004/05:soybean",
                                               "2005/06:wheat", "2005/06:soybean"]
    assert w.seasons[4].sowing == m.seasons[4].sowing == date(2004, 10, 20)
    assert w.seasons[3].sowing == date(2004, 5, 5) and m.seasons[3].sowing == date(2004, 3, 20)
    soy_w, soy_m = w.seasons[-1], m.seasons[-1]
    assert soy_w.sowing == soy_m.sowing and soy_w.sowing_source == "rain_rule_common"
    # The common date is the first one valid in both arms.
    spec = RainSowing(RULE, 2005, common="site_x:2005/06")
    later = max(spec.not_before(c.seasons[-2].harvest + timedelta(days=1), c.seasons[-2].harvest)
                for c in (w, m))
    assert soy_w.sowing == trigger(rain_series(weather), spec, later)


def test_a1_restart_replaces_carried_water_at_target(weather, field_run):
    out = run_regime(weather, CLAY_TEST_LAYERS, with_restart(field(), FC))
    fc = restart_profile(FC, CLAY_TEST_LAYERS)
    for arm in ("S1", "S3"):
        base, a1 = field_run[arm], out[arm]
        assert np.array_equal(a1.seasons[1].th_end, base.seasons[1].th_end)
        assert a1.seasons[-1].restarted and a1.handover_gap_mm[-1] is None
        assert np.allclose(a1.seasons[-1].th_start, fc, atol=1e-9)
    assert np.array_equal(out["S1"].seasons[-1].th_start, out["S3"].seasons[-1].th_start)


def test_fixed_date_before_simulated_harvest_ends_the_previous_crop(weather):
    # crop_end: a published sowing before the simulated harvest no longer stops the arm.
    # The soybean (maturity on 2002-03-18 here) ends the day before the corn sowing.
    chain = run_chain(weather, CLAY_TEST_LAYERS,
                      [Season("soybean", date(2001, 10, 1)), Season("maize", date(2002, 1, 15), label="m")],
                      date(2001, 8, 1))
    assert chain.completed and len(chain.seasons) == 2
    soy, corn = chain.seasons
    assert soy.harvest == date(2002, 1, 14) and soy.end_rule == "next_sowing" and not soy.matured
    assert corn.start == corn.sowing == date(2002, 1, 15)
    assert chain.handover_gap_mm[0] <= CONSERVATION_TOL_MM
    assert abs(soy.balance_residual_mm) <= CONSERVATION_TOL_MM


def test_published_harvest_ends_the_crop_only_when_earlier(weather):
    early = run_chain(weather, CLAY_TEST_LAYERS,
                      [Season("soybean", date(2001, 10, 1), harvest=date(2002, 2, 10))], date(2001, 8, 1))
    s = early.seasons[0]
    assert (s.harvest, s.end_rule, s.matured) == (date(2002, 2, 10), "published_harvest", False)
    late = run_chain(weather, CLAY_TEST_LAYERS,
                     [Season("soybean", date(2001, 10, 1), harvest=date(2002, 6, 1))], date(2001, 8, 1))
    s = late.seasons[0]
    assert s.end_rule == "maturity" and s.matured and s.harvest < date(2002, 6, 1)


def test_dates_out_of_order_raise(weather):
    with pytest.raises(ValueError, match="out of order"):
        run_chain(weather, CLAY_TEST_LAYERS,
                  [Season("soybean", date(2001, 10, 1)), Season("maize", date(2001, 9, 15))], date(2001, 8, 1))
    with pytest.raises(ValueError, match="not after sowing"):
        Season("soybean", date(2001, 10, 1), harvest=date(2001, 9, 1))


def test_tested_soybean_ends_on_the_same_day_in_both_arms(weather):
    from pipeline.engine.chain import run_arms

    arms = {
        "a": (Season("maize", date(2001, 2, 1)), Season("soybean", date(2001, 10, 1), common_end="x"),
              Season("maize", date(2002, 1, 20))),
        "b": (Season("wheat", date(2001, 4, 1)), Season("soybean", date(2001, 10, 1), common_end="x"),
              Season("wheat", date(2002, 5, 1))),
    }
    out = run_arms(weather, CLAY_TEST_LAYERS, arms, date(2001, 1, 15))
    a, b = out["a"].seasons[1], out["b"].seasons[1]
    assert a.harvest == b.harvest == date(2002, 1, 19)
    assert (a.end_rule, b.end_rule) == ("next_sowing", "common_end")
    # without the key each arm keeps its own end
    free = {n: tuple(Season(s.crop, s.sowing) for s in seq) for n, seq in arms.items()}
    out = run_arms(weather, CLAY_TEST_LAYERS, free, date(2001, 1, 15))
    assert out["a"].seasons[1].harvest == date(2002, 1, 19) and out["b"].seasons[1].end_rule == "maturity"


def test_shared_prefix_ends_before_the_earliest_next_sowing(weather):
    from pipeline.engine.chain import run_arms

    arms = {"a": (Season("soybean", date(2001, 10, 1)), Season("maize", date(2002, 1, 20))),
            "b": (Season("soybean", date(2001, 10, 1)), Season("wheat", date(2002, 5, 1)))}
    out = run_arms(weather, CLAY_TEST_LAYERS, arms, date(2001, 8, 1))
    assert out["a"].shared_prefix == 1
    assert out["a"].seasons[0].harvest == out["b"].seasons[0].harvest == date(2002, 1, 19)
    assert out["b"].seasons[1].start == date(2002, 1, 20) and out["b"].seasons[1].sowing == date(2002, 5, 1)


def test_rain_rule_without_trigger_stops_as_failure(weather):
    never = RainRule(10_000.0, 3, "09-01", "12-31", rain_after_not_before=True)
    chain = run_chain(weather, CLAY_TEST_LAYERS,
                      [Season("soybean", date(2001, 10, 1)),
                       Season("soybean", RainSowing(never, 2002), label="t", role="target")],
                      date(2001, 8, 1))
    assert chain.stopped["reason"] == "sowing_not_triggered"
    assert (chain.stopped["window_start"], chain.stopped["latest"]) == ("2002-09-01", "2002-12-31")


def test_climate_too_short_is_an_error_not_an_outcome(weather):
    cut = weather.loc[weather["Date"] <= "2002-01-15"].reset_index(drop=True)
    with pytest.raises(CalendarError):
        run_season(cut, CLAY_TEST_LAYERS, Season("soybean", date(2001, 10, 1)), date(2001, 8, 1), None)
    with pytest.raises(ValueError, match="resolved"):
        run_season(weather, CLAY_TEST_LAYERS, Season("soybean", RainSowing(RULE, 2001)),
                   date(2001, 8, 1), None)


def test_side_run_cn_rew_and_library_zmax(weather):
    soil = make_soil(CLAY_TEST_LAYERS, cn=61, rew=9)
    assert (soil.calc_cn, soil.cn, soil.adj_rew, soil.rew) == (0, 61.0, 1, 9.0)
    base = make_soil(CLAY_TEST_LAYERS)
    assert (base.calc_cn, base.adj_rew) == (1, 0)
    s = Season("soybean", date(2001, 10, 1))
    side = run_season(weather, CLAY_TEST_LAYERS, s, date(2001, 9, 1), None, setup=Setup(cn=61, rew=9))
    assert side.curve_number == 61.0
    assert make_crop("maize", date(2001, 3, 1), library_zmax=True).Zmax != 1.0
    with pytest.raises(ValueError):
        make_crop("maize", date(2001, 3, 1), library_zmax=True, Zmax=1.0)


def test_restart_fraction_profile():
    wp, fc = water_limits(CLAY_TEST_LAYERS)
    th = restart_profile(Restart("available_fraction", 0.25), CLAY_TEST_LAYERS)
    assert np.allclose(th, wp + 0.25 * (fc - wp)) and (th > 0).all()


# Soil from texture with a Ksat input (D1 open) ------------------------------

SAND = [20.0, 18.0, 16.0, 14.0, 12.0, 12.0]   # synthetic, not SoilGrids output
CLAY = [62.0, 64.0, 66.0, 70.0, 72.0, 72.0]
SOC = [22.0, 18.0, 12.0, 8.0, 5.0, 4.0]


def test_texture_layers_follow_compartments_and_take_ksat():
    hz = soilgrids_horizons(SAND, CLAY, SOC, om_per_soc=1.724)
    assert hz[0][4] == pytest.approx(22.0 / 10 * 1.724)
    layers = texture_layers(hz, ksat_mm_h=60.5, ksat_depth_m=(0.0, 0.3))
    assert sum(t for t, *_ in layers) == pytest.approx(sum(DZ))
    # Compartment 1 (0-0.1 m) averages the 0-5 and 5-15 cm horizons by overlap.
    assert layers[0][1] == pytest.approx((20.0 + 18.0) / 2)
    prof = make_soil(layers).profile
    assert not prof[["th_wp", "th_fc", "th_s", "Ksat", "Layer"]].isna().any().any()
    top = prof["dzsum"].round(2) <= 0.3
    assert (prof.loc[top, "Ksat"] == 60.5 * MM_H_TO_MM_DAY).all()
    assert (prof.loc[~top, "Ksat"] < 100).all()  # texture formula below 0.3 m
    texture = make_soil(texture_layers(hz, ksat_mm_h=None, ksat_depth_m=(0.0, 0.3))).profile
    assert np.allclose(texture["th_fc"], prof["th_fc"]) and (texture["Ksat"] < 100).all()


def test_texture_layers_reject_off_grid_or_buried_ksat():
    hz = soilgrids_horizons(SAND, CLAY, SOC, om_per_soc=1.724)
    with pytest.raises(ValueError, match="boundary"):
        texture_layers(hz, ksat_mm_h=60.5, ksat_depth_m=(0.0, 0.25))
    with pytest.raises(ValueError, match="surface"):
        texture_layers(hz, ksat_mm_h=60.5, ksat_depth_m=(0.1, 0.3))
    with pytest.raises(ValueError, match="contiguous"):
        texture_layers([hz[0], hz[2]], ksat_mm_h=None, ksat_depth_m=(0.0, 0.3))

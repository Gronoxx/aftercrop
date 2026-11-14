# Written with an SI tool (Claude, Anthropic) for NASA Space Apps 2026. See AI_USE.md.
"""Engine artifacts before any delta (plan 10.6, M3.6, L0).

The two arms of the spike on the aquacrop sample climate give real outcome
artifacts (dead corn, wheat "matured" with no canopy). Integrity and setup
flags are checked on edited copies of those results.

Run: .venv-engine/bin/python -m pytest tests/test_engine_artifacts.py -q
"""
from __future__ import annotations

import json
import sys
from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

aquacrop = pytest.importorskip("aquacrop")

from pipeline.engine import artifacts as A  # noqa: E402
from pipeline.engine.chain import ChainResult, Season, run_arms  # noqa: E402
from pipeline.engine.soilmodel import CLAY_TEST_LAYERS  # noqa: E402
from pipeline.engine.sowing import ProtocolGap  # noqa: E402
from pipeline.engine.weather import sample_weather  # noqa: E402

TARGET = "2002/03:soybean"
ARMS = {
    "after_maize": [Season("soybean", date(2001, 10, 1), label="2001/02:soybean", role="prefix"),
                    Season("maize", label="2002:maize", role="predecessor"),
                    Season("soybean", date(2002, 11, 30), label=TARGET, role="target")],
    "after_wheat": [Season("soybean", date(2001, 10, 1), label="2001/02:soybean", role="prefix"),
                    Season("wheat", date(2002, 5, 1), label="2002:wheat", role="predecessor"),
                    Season("soybean", date(2002, 11, 30), label=TARGET, role="target")],
}
THR = A.Thresholds(closure_mm_per_swap=1.0, season_balance_mm=1.0)


@pytest.fixture(scope="module")
def arms():
    return run_arms(sample_weather(), CLAY_TEST_LAYERS, ARMS, date(2001, 8, 1))


def codes(flags, index=None):
    return {f.code for f in flags if index is None or f.index == index}


def test_real_outcomes_found_and_maturity_not_trusted(arms):
    maize = A.chain_flags(arms["after_maize"], THR, "after_maize")
    wheat = A.chain_flags(arms["after_wheat"], THR, "after_wheat")
    assert codes(maize, 1) == {"dead_canopy", "zeroed_season", "not_matured"}
    # OSPy says the wheat matured, but it never had a canopy.
    assert arms["after_wheat"].seasons[1].matured
    assert codes(wheat, 1) == {"canopy_never_formed", "zeroed_season", "matured_without_canopy"}
    assert all(f.kind == "outcome" for f in maize + wheat)
    assert codes(maize, 0) == codes(maize, 2) == set()


def test_report_has_the_three_checks_per_arm_and_day_difference(arms):
    body = A.report(arms, THR, A.checks_from_protocol(
        {"chains": {"artifact_checks": ["days_to_emergence", "zeroed_season", "dead_canopy"]}}))
    json.dumps(body)  # artifacts.json body
    assert body["checks"] == ["days_to_emergence", "zeroed_season", "dead_canopy"]
    for arm in body["arms"].values():
        assert arm["completed"] and arm["shared_prefix"] == 1
        for s in arm["seasons"]:
            assert {"days_to_emergence", "zeroed_season", "dead_canopy"} <= set(s)
    w = body["arms"]["after_wheat"]["seasons"][1]
    assert w["days_to_emergence"] is None and w["zeroed_season"] and not w["dead_canopy"]
    assert body["sowing_day_difference"] == [
        {"label": TARGET, "sowing": {"after_maize": "2002-11-30", "after_wheat": "2002-11-30"},
         "difference_days": 0}]
    assert body["counts"] == {"outcome": 6, "integrity": 0, "setup": 0, "threshold": 0}
    assert not body["blocks_delta"]
    assert body["not_checked"] == ["yield_above_max", "late_emergence"]


def test_integrity_flags_block_reading(arms):
    soy = arms["after_maize"].seasons[2]
    assert codes(A.season_flags(replace(soy, dry_yield_t_ha=float("nan")), THR)) == {"yield_invalid"}
    above = replace(soy, dry_yield_t_ha=soy.yield_potential_t_ha + 0.5)
    assert "yield_above_potential" in codes(A.season_flags(above, THR))
    leak = replace(soy, balance_residual_mm=5.0)
    assert codes(A.season_flags(leak, THR)) == {"water_balance_open"}
    assert codes(A.season_flags(leak, A.Thresholds(1.0))) == set()  # no tolerance: not checked
    chain = arms["after_maize"]
    broken = ChainResult([*chain.seasons[:2], leak], chain.handover_gap_mm, None, 1)
    body = A.report({"a": broken}, THR)
    assert body["blocks_delta"] and body["counts"]["integrity"] == 1


def test_setup_and_threshold_flags(arms):
    soy = arms["after_maize"].seasons[2]
    cut = replace(soy, matured=False, crop_dead=False, sim_end=soy.harvest)
    assert "cut_at_sim_end" in codes(A.season_flags(cut, THR))
    thr = A.Thresholds(1.0, max_yield_t_ha={"soybean": 0.1}, max_days_to_emergence=10)
    got = A.season_flags(soy, thr)
    assert codes(got) == {"yield_above_max", "late_emergence"}
    assert {f.kind for f in got} == {"threshold"}
    with pytest.raises(ProtocolGap):
        A.season_flags(replace(soy, crop="maize"), thr)


def test_handover_gap_and_stop_become_flags(arms):
    chain = arms["after_wheat"]
    gap = ChainResult(chain.seasons, [0.0, 2.5], None, 1)
    assert [f.code for f in A.chain_flags(gap, THR, "w") if f.kind == "integrity"] == ["handover_gap"]
    stop = {"index": 2, "crop": "soybean", "label": TARGET, "role": "target",
            "reason": "sowing_not_triggered", "window_start": "2002-09-01", "latest": "2002-12-31"}
    stopped = ChainResult(chain.seasons[:2], [0.0], stop, 1)
    flags = A.chain_flags(stopped, THR, "w")
    assert flags[-1].code == "sowing_not_triggered" and flags[-1].kind == "outcome"
    diff = A.sowing_day_difference({"w": stopped, "m": arms["after_maize"]})
    assert diff == [{"label": TARGET, "sowing": {"w": None, "m": "2002-11-30"}, "difference_days": None}]


def test_unexplained_lists_flags_without_text(arms):
    body = A.report(arms, THR)
    ids = [f["id"] for a in body["arms"].values() for f in a["flags"]]
    notes = {ids[0]: "corn died in the dry test winter"}
    assert A.unexplained(body, notes) == ids[1:]


def test_thresholds_read_l0_from_protocol():
    thr = A.Thresholds.from_protocol({"tests": {"L0": {"closure_mm_per_swap": 1.0}}}, season_balance_mm=1.0)
    assert thr.closure_mm_per_swap == 1.0 and thr.season_balance_mm == 1.0
    with pytest.raises(ProtocolGap):
        A.Thresholds.from_protocol({})
    with pytest.raises(ProtocolGap):
        A.checks_from_protocol({"chains": {"artifact_checks": ["roots"]}})

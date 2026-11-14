# Written with an SI tool (Claude, Anthropic) for NASA Space Apps 2026. See AI_USE.md.
"""Regime builders (plan 10.5 and 11.4, PROTOCOL.md 7, D7 and D8). No model run.

Run: .venv-engine/bin/python -m pytest tests/test_engine_regimes.py -q
"""
from __future__ import annotations

import ast
import sys
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pipeline.engine.regimes import (  # noqa: E402
    AFTER_HARVEST, Restart, Season, Setup, TrialSeason, field_regime, ksat_choices,
    sensitivity_setups, shift_date, shifted, trial_regime, trial_warmup, with_restart,
)
from pipeline.engine.sowing import ProtocolGap, RainRule, RainSowing  # noqa: E402

RULE = RainRule(20.0, 3, "09-01", "12-31", rain_after_not_before=True)
FC = Restart("field_capacity")


def field(y=2003, **kw):
    args = dict(prefix_years=1, corn_sowing=AFTER_HARVEST, wheat_sowing="05-01",
                s3_gap_days=20, initial=FC)
    args.update(kw)
    return field_regime(y, RULE, **args)


def test_field_regime_branches_after_common_prefix():
    r = field()
    s1, s3 = r.arms["S1"], r.arms["S3"]
    assert r.start == date(2001, 9, 1)
    assert s1[:3] == s3[:3]
    assert [s.crop for s in s1] == ["soybean", "maize", "soybean", "maize", "soybean"]
    assert [s.crop for s in s3] == ["soybean", "maize", "soybean", "wheat", "soybean"]
    assert s1[2].sowing == RainSowing(RULE, 2002) and s1[2].label == "2002/03:soybean"
    assert s3[3].sowing == date(2003, 5, 1)
    assert s1[3].sowing is None  # corn the day after the soybean harvest
    t1, t3 = s1[-1], s3[-1]
    assert t1.label == t3.label == "2003/04:soybean" and t1.role == t3.role == "target"
    assert t1.sowing.min_days_after_harvest == 1 and t3.sowing.min_days_after_harvest == 20
    assert t1.sowing.window() == t3.sowing.window()  # start common to both arms


def test_field_regime_explicit_corn_date_and_no_prefix():
    r = field(prefix_years=0, corn_sowing="02-20")
    assert [s.crop for s in r.arms["S1"]] == ["soybean", "maize", "soybean"]
    assert r.arms["S1"][1].sowing == date(2003, 2, 20)
    assert r.start == date(2002, 9, 1)


def trial_seasons(missing_second=False):
    return [
        TrialSeason("2004/05", date(2004, 11, 10), {"wheat": date(2004, 5, 5), "maize": date(2004, 3, 1)}),
        TrialSeason("2005/06", None if missing_second else date(2005, 11, 12),
                    {"wheat": date(2005, 5, 4), "maize": date(2005, 3, 2)}),
    ]


def test_trial_regime_continuous_per_arm_with_common_warmup():
    warm = trial_warmup("2004/05", RULE)
    r = trial_regime("site_x", trial_seasons(missing_second=True), RULE, warmup=warm, initial=FC)
    w, m = r.arms["after_wheat"], r.arms["after_maize"]
    assert w[:3] == m[:3] == tuple(warm)
    assert [s.crop for s in warm] == ["soybean", "maize", "soybean"]
    assert warm[0].sowing.season_year == 2002 and warm[2].sowing.season_year == 2003
    assert r.start == date(2002, 9, 1)
    assert [s.crop for s in w[3:]] == ["wheat", "soybean", "wheat", "soybean"]
    assert [s.crop for s in m[3:]] == ["maize", "soybean", "maize", "soybean"]
    assert w[4].sowing == m[4].sowing == date(2004, 11, 10)
    # Missing published date: the same rain-rule date in both arms.
    assert isinstance(w[6].sowing, RainSowing) and w[6].sowing == m[6].sowing
    assert w[6].sowing.common == "site_x:2005/06"


def test_trial_regime_rejects_gaps():
    warm = trial_warmup("2004/05", RULE)
    s = trial_seasons()
    with pytest.raises(ValueError, match="consecutive"):
        trial_regime("x", [s[0], TrialSeason("2006/07", None, s[1].predecessor_sowing)], RULE,
                     warmup=warm, initial=FC)
    with pytest.raises(ProtocolGap, match="D7"):
        trial_regime("x", [TrialSeason("2004/05", None, {"maize": date(2004, 3, 1)})], RULE,
                     warmup=warm, initial=FC)
    with pytest.raises(ValueError, match="warm-up must end"):
        trial_regime("x", s, RULE, warmup=warm[:2], initial=FC)


def test_a1_restart_only_on_targets():
    r = with_restart(field(), FC)
    for seq in r.arms.values():
        assert [s.restart is not None for s in seq] == [s.role == "target" for s in seq]
    with pytest.raises(ValueError):
        with_restart(field(), FC, roles=("nothing",))


def test_p1_shift_moves_every_date():
    base = field()
    moved = shifted(base, -4)
    assert moved.start == date(1997, 9, 1)
    assert moved.arms["S3"][3].sowing == date(1999, 5, 1)
    assert moved.arms["S3"][-1].sowing.season_year == 1999
    assert [s.label for s in moved.arms["S1"]] == [s.label for s in base.arms["S1"]]
    assert shift_date(date(2004, 2, 29), 1) == date(2005, 2, 28)
    assert shift_date(date(2004, 2, 29), 4) == date(2008, 2, 29)


def test_data_types_validate():
    with pytest.raises(ValueError):
        Restart("available_fraction", 0.0)  # never zero water
    assert Restart("available_fraction", 0.5).fraction == 0.5
    with pytest.raises(KeyError):
        Season("barley")
    with pytest.raises(TypeError):
        Season("soybean", "2005-10-01")
    assert Setup().is_base and not Setup(cn=61, rew=9).is_base


def test_protocol_readers_on_inline_block():
    block = {"engine": {
        "sensitivity_only": {"cn": 61, "rew": 9, "zmax": "library_default"},
        "ksat": {"mode": "measured", "value_mm_h": 60.5, "alternative_mm_h": 78,
                 "layer_m": [0.0, 0.3], "sensitivity": "texture_formula", "source": "a"},
    }}
    setups = sensitivity_setups(block)
    assert setups == {"base": Setup(), "cn_rew": Setup(cn=61.0, rew=9.0),
                      "library_zmax": Setup(library_zmax=True)}
    k = ksat_choices(block)
    assert (k["value"].mm_h, k["alternative"].mm_h, k["texture"].mm_h) == (60.5, 78.0, None)
    assert k["value"].depth_m == (0.0, 0.3)
    with pytest.raises(ProtocolGap):
        ksat_choices({"engine": {"ksat": {"mode": "texture"}}})
    with pytest.raises(ProtocolGap):
        Restart.a1_from_protocol({"tests": {"A1": {"restart_profile": "half"}}})


def test_engine_never_reads_bench_or_manual_data():
    banned_text = "data/" + "manual"
    for f in (ROOT / "pipeline" / "engine").glob("*.py"):
        src = f.read_text()
        assert banned_text not in src, f.name
        for node in ast.walk(ast.parse(src)):
            if isinstance(node, ast.ImportFrom):
                mod = ("." * node.level) + (node.module or "")
                assert "bench" not in mod and "adapters" not in mod, f"{f.name}: {mod}"
            elif isinstance(node, ast.Import):
                assert not any("bench" in a.name or "adapters" in a.name for a in node.names), f.name

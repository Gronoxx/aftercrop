# Written with an SI tool (Claude, Anthropic) for NASA Space Apps 2026. See AI_USE.md.
"""The engine reads the real protocol: block, and fails where the block is silent.

Needs PyYAML, so it runs in .venv (make test-py) and is skipped in
.venv-engine. Imports only the engine modules that do not need aquacrop.
Reads config/region_norte_pr.yaml only, never data/.
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

yaml = pytest.importorskip("yaml")

from pipeline.engine import artifacts as A  # noqa: E402
from pipeline.engine import regimes as R  # noqa: E402
from pipeline.engine import weather as W  # noqa: E402
from pipeline.engine.sowing import ProtocolGap, RainRule, s3_gap_days  # noqa: E402

CONFIG = ROOT / "config" / "region_norte_pr.yaml"


@pytest.fixture(scope="module")
def config():
    with open(CONFIG, encoding="utf-8") as f:
        return yaml.safe_load(f)


@pytest.fixture(scope="module")
def protocol(config):
    return config["protocol"]


def test_rain_rule_comes_whole_from_the_block(protocol):
    # D6 is closed: the block holds rain_after_not_before, and an explicit value must agree with it.
    r = RainRule.from_protocol(protocol)
    assert (r.rain_mm, r.rain_days, r.start, r.latest) == (20.0, 3, "09-01", "12-31")
    assert r.rain_after_not_before is True
    assert RainRule.from_protocol(protocol, rain_after_not_before=True) == r
    with pytest.raises(ValueError, match="rain_after_not_before"):
        RainRule.from_protocol(protocol, rain_after_not_before=False)
    assert s3_gap_days(protocol) == 20


def test_climate_window_and_power_parameters(config, protocol):
    assert W.climate_window(protocol) == (date(1984, 1, 1), date(2025, 12, 31))
    met = next(c for c in config["sources"]["power"]["calls"] if c["id"] == "met")
    assert set(met["parameters"]) == set(W.POWER_PARAMETERS)
    assert protocol["engine"]["rain"]["main"] == W.RAIN


def test_side_runs_ksat_and_profiles(protocol):
    setups = R.sensitivity_setups(protocol)
    assert setups["cn_rew"] == R.Setup(cn=61.0, rew=9.0)
    assert setups["library_zmax"] == R.Setup(library_zmax=True)
    k = R.ksat_choices(protocol)
    assert k["value"].mm_h == 60.5 and k["alternative"].mm_h == 78.0 and k["texture"].mm_h is None
    assert k["value"].depth_m == (0.0, 0.3)
    assert R.Restart.a1_from_protocol(protocol) == R.Restart("field_capacity")
    assert R.Restart.trial_initial_from_protocol(protocol) == R.Restart("field_capacity")


def test_trial_warmup_text_is_the_one_implemented(protocol):
    rule = RainRule.from_protocol(protocol, rain_after_not_before=True)
    warm = R.trial_warmup_from_protocol(protocol, "2021/22", rule)
    assert [s.crop for s in warm] == ["soybean", "maize", "soybean"]


def test_artifact_checks_and_l0(protocol):
    assert A.checks_from_protocol(protocol) == list(A.KNOWN_CHECKS)
    assert A.Thresholds.from_protocol(protocol).closure_mm_per_swap == 1.0


# Trial dates (config/trial_dates.json): dates only, no engine run, no trial value -------

def test_trial_dates_fit_the_protocol_and_no_run_is_refused(protocol):
    from pipeline.engine import cli

    dates = cli.load_trial_dates(ROOT / "config" / "trial_dates.json", protocol,
                                 list(protocol["trial_site_seasons"]))
    for site, seasons in dates.items():
        for s in seasons:
            assert s.soybean_sowing is not None                   # every Doc prints them
            assert set(s.predecessor_sowing) == {"wheat", "maize"}
            for crop, d in s.predecessor_sowing.items():
                h = s.predecessor_harvest.get(crop)
                assert h is None or d < h < s.soybean_sowing, (site, s.season, crop)
            assert s.soybean_sowing < s.soybean_harvest
    runs = cli.trial_runs(protocol, dates, RainRule.from_protocol(protocol), ["base", "a1", "p1"], [],
                          W.climate_window(protocol))
    assert [r.key for r in runs if r.problems] == []
    count = protocol["chains"]["count"]
    assert cli.chain_count(runs) == {"base": count["base"], "a1": count["a1"], "p1": count["p1"]}
    assert sum(cli.chain_count(runs).values()) == count["total"]


def test_trial_dates_record_sources_and_the_yield_basis(protocol):
    import json

    doc = json.loads((ROOT / "config" / "trial_dates.json").read_text(encoding="utf-8"))
    for site, rows in doc["sites"].items():
        assert all("Doc" in r["source"] for r in rows), site
        assert doc["yield_basis"][site]["moisture_pct"] == 13
    assert set(doc["yield_basis"]) == set(protocol["trial_site_seasons"])

# Written with an SI tool (Claude, Anthropic) for NASA Space Apps 2026. See AI_USE.md.
"""The bench reads its rules from the one owner: the protocol: block of
config/region_norte_pr.yaml (criteria M0.4 and M4.4).

Builds the L0, L1, T0, T1, P1, A1, A2, A3, N1, H2 and R1 rules from the real
block and checks they hold the plan's values (sections 11.2, 11.3 and 12.4).
Skips while the block does not exist. No trial value is read.
"""

from datetime import date
from fractions import Fraction
from pathlib import Path

import pytest
import yaml

from pipeline.bench import a1, ablation, common, l0, l1, label, n1, p1, r1, scoreboard, t0, t1

CONFIG = Path(__file__).resolve().parents[1] / "config" / "region_norte_pr.yaml"


def _protocol():
    if not CONFIG.exists():
        return None
    doc = yaml.safe_load(CONFIG.read_text(encoding="utf-8")) or {}
    return doc if isinstance(doc.get("protocol"), dict) else None


PROTOCOL = _protocol()
pytestmark = pytest.mark.skipif(PROTOCOL is None, reason="config/region_norte_pr.yaml has no protocol: block yet")


def test_t0_and_t1_rules_hold_the_plan_values():
    r0 = t0.T0Rule.from_protocol(PROTOCOL)
    r1 = t1.T1Rule.from_protocol(PROTOCOL)
    assert (r0.s_min, r0.n_site_seasons, r0.within_site_sum_gt) == (21, 10, 0)
    assert (r1.s_min, r1.n_site_seasons, r1.rmse_reference) == (21, 10, "in_sample_constant_rmse")


def test_p1_design_holds_the_plan_values():
    d = p1.P1Design.from_protocol(PROTOCOL)
    assert list(d.shifts) == [4, 8, 12, 16, 20, 24, 28, 32, 36]
    assert (d.b, d.first_year, d.last_year, d.alpha) == (9, 1985, 2024, Fraction(1, 10))
    assert d.climate == (date(1984, 1, 1), date(2025, 12, 31))


def test_a1_rule_holds_the_plan_values():
    r = a1.A1Rule.from_protocol(PROTOCOL)
    assert (r.alpha, r.n_site_seasons, r.n_patterns) == (Fraction(1, 10), 10, 1024)


def test_label_count_comes_from_the_protocol():
    assert label.label_from_protocol("pass", "pass", "pass", "pass", 10, PROTOCOL) == label.DIRECTION_SUPPORTED
    assert label.label_from_protocol("pass", "pass", "pass", "pass", 9, PROTOCOL) == label.MODEL_NOT_VALIDATED


def test_l0_and_l1_rules_hold_the_plan_values():
    r0 = l0.L0Rule.from_protocol(PROTOCOL)
    assert (r0.closure_mm, r0.repeat, r0.max_unexplained) == (1, "exact", 0)
    assert r0.artifact_kinds == ("days_to_emergence", "zeroed_season", "dead_canopy")
    r1_ = l1.L1Rule.from_protocol(PROTOCOL)
    assert (r1_.site, r1_.stations) == ("londrina", ("INMET", "IDR-Parana"))
    assert (r1_.rain_bias_max_pct, r1_.temperature_bias_max_c) == (10, 1)


def test_ablation_rules_hold_the_plan_values():
    assert ablation.A2Rule.from_protocol(PROTOCOL).common_period == (date(1998, 1, 1), date(2025, 9, 30))
    assert ablation.A3Rule.from_protocol(PROTOCOL).zero_changes == "context only"


def test_n1_and_r1_take_percentile_level_and_method_from_the_block():
    # D12 closed them: P90 by nearest rank for N1, the 10 to 90 band by nearest rank for R1.
    r = n1.N1Rule.from_protocol(PROTOCOL)
    assert (r.percentile, r.method, r.unit) == (90, "nearest_rank", "days")
    assert r1.R1Rule.from_protocol(PROTOCOL) == r1.R1Rule.from_protocol(PROTOCOL, band=[10, 90],
                                                                          method="nearest_rank")


def test_l1_and_r1_are_planned_not_run_in_the_event():
    assert PROTOCOL["protocol"]["tests"]["L1"]["event_status"] == "planned_not_run"
    assert PROTOCOL["protocol"]["r1"]["event_status"] == "planned_not_run"


def test_r1_rule_holds_the_plan_values():
    r = r1.R1Rule.from_protocol(PROTOCOL, band=[10, 90], method="nearest_rank")
    assert (r.members, r.cut_to, r.seed) == (50, 20, 20261114)
    assert (r.inversion_max, r.equivalence) == (Fraction(1, 5), 166)
    assert len(r.dimensions) == 12


def test_h2_and_every_threshold_come_from_the_block():
    assert scoreboard.h2_entry(PROTOCOL)["status"] == "not_run"
    limits = scoreboard.thresholds(PROTOCOL)
    assert set(limits) == set(scoreboard.TEST_IDS)
    assert limits["P1"]["wrong_year_s_at_least_max"] == 0
    assert scoreboard.trial_sites(PROTOCOL) == {"londrina": 3, "floresta": 4, "cafelandia": 3}
    board, _ = scoreboard.build({}, PROTOCOL, region_id="norte_pr")
    assert list(board["s_by_site"]) == ["Londrina", "Floresta", "Cafelandia"]


def test_label_code_follows_the_protocol_order():
    assert label.order_matches(PROTOCOL)
    assert label.label("pass", "pass", "pass", "pass", 10, n_required=10, l0="fail") == label.MODEL_NOT_VALIDATED
    assert PROTOCOL["protocol"]["tests"]["L0"]["fail_label"] == label.LABEL_TITLE[label.MODEL_NOT_VALIDATED]

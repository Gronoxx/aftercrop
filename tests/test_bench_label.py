# Written with an SI tool (Claude, Anthropic) for NASA Space Apps 2026. See AI_USE.md.
"""Label computed by code (section 11.6, sheet M4, criterion M4.7).

Every combination of pass, fail, not_run and not_completed in T0, T1, P1 and A1
gives one of the five labels. Infrastructure failure gives "not completed" and
never a failure; a task not run is "planned, not run".
"""

import itertools
import json
from pathlib import Path

import pytest

from pipeline.bench import label as lb

FIX = Path(__file__).parent / "fixtures"
LABELS = json.loads((FIX / "bench_label.json").read_text(encoding="utf-8"))
PROTOCOL = json.loads((FIX / "bench_protocol.json").read_text(encoding="utf-8"))
N_REQ = LABELS["n_required"]
FOUR = ("pass", "fail", "not_run", "not_completed")
ALL = FOUR + ("exploratory",)


@pytest.mark.parametrize("c", LABELS["cases"], ids=lambda c: c["name"])
def test_each_outcome(c):
    assert lb.label(c["t0"], c["t1"], c["p1"], c["a1"], c["n"], n_required=N_REQ) == c["label"]


def test_every_label_is_reached():
    assert {c["label"] for c in LABELS["cases"]} == set(lb.LABEL_IDS)


@pytest.mark.parametrize("n", [0, 9, 10])
def test_all_combinations_give_one_of_five(n):
    for t0, t1, p1, a1 in itertools.product(ALL, repeat=4):
        assert lb.label(t0, t1, p1, a1, n, n_required=N_REQ) in lb.LABEL_IDS


def test_supported_only_when_all_pass_with_ten():
    for n in (9, 10):
        for combo in itertools.product(ALL, repeat=4):
            got = lb.label(*combo, n, n_required=N_REQ)
            assert (got == lb.DIRECTION_SUPPORTED) == (combo == ("pass",) * 4 and n >= N_REQ)


def test_infrastructure_never_becomes_a_failure():
    for t1, p1, a1 in itertools.product(ALL, repeat=3):
        for t0 in ("not_run", "not_completed"):
            assert lb.label(t0, t1, p1, a1, 10, n_required=N_REQ) == lb.NOT_COMPLETED_LABEL
    for p1, a1 in itertools.product(ALL, repeat=2):
        for t1 in ("not_run", "not_completed"):
            assert lb.label("pass", t1, p1, a1, 10, n_required=N_REQ) == lb.NOT_COMPLETED_LABEL


def test_a_failure_never_becomes_not_completed():
    for t1, p1, a1 in itertools.product(ALL, repeat=3):
        assert lb.label("fail", t1, p1, a1, 10, n_required=N_REQ) == lb.MODEL_NOT_VALIDATED
    for p1, a1 in itertools.product(ALL, repeat=2):
        assert lb.label("pass", "fail", p1, a1, 10, n_required=N_REQ) == lb.DIRECTION_NOT_VALIDATED


def test_p1_or_a1_short_of_pass_is_mechanism_not_shown():
    for p1, a1 in itertools.product(ALL, repeat=2):
        expected = lb.DIRECTION_SUPPORTED if (p1, a1) == ("pass", "pass") else lb.MECHANISM_NOT_SHOWN
        assert lb.label("pass", "pass", p1, a1, 10, n_required=N_REQ) == expected


@pytest.mark.parametrize("bad", ["passed", "Not completed", "", None])
def test_unknown_status_is_refused(bad):
    with pytest.raises(ValueError):
        lb.label("pass", bad, "pass", "pass", 10, n_required=N_REQ)


@pytest.mark.parametrize("n", [-1, 9.5, True, None])
def test_bad_count_is_refused(n):
    with pytest.raises(ValueError):
        lb.label("pass", "pass", "pass", "pass", n, n_required=N_REQ)


def test_label_reads_the_count_from_protocol():
    assert lb.label_from_protocol("pass", "pass", "pass", "pass", 10, PROTOCOL) == lb.DIRECTION_SUPPORTED
    assert lb.label_from_protocol("pass", "pass", "pass", "pass", 9, PROTOCOL) == lb.MODEL_NOT_VALIDATED


def test_texts():
    assert set(lb.LABEL_TITLE) == set(lb.LABEL_MESSAGE) == set(lb.LABEL_IDS)
    assert lb.label_text(lb.NOT_COMPLETED_LABEL) == "Not completed"
    assert lb.label_text(lb.DIRECTION_SUPPORTED) == (
        "Direction supported in 10 trial site-seasons (exploratory, not blind)")
    assert lb.page_line(lb.MODEL_NOT_VALIDATED) == (
        "Crop-sequence model check (wheat option, water only): Model not validated here. "
        "The answer above does not depend on it.")
    assert lb.status_text("not_run") == "planned, not run"
    assert lb.status_text("not_completed") == "not completed"
    assert set(lb.STATUS_TEXT) == set(ALL) | {"reported"}  # reported: A2, A3, N1, H1, no threshold


# Task runner ---------------------------------------------------------------------

class Result:
    def __init__(self, status):
        self.status = status


def test_exception_is_not_completed_never_fail():
    def broken():
        raise FileNotFoundError("data/runs/chain_x.csv")
    out = lb.run_guarded(broken)
    assert out.status == "not_completed"
    assert "FileNotFoundError" in out.error


def test_timeout_is_not_completed():
    def slow():
        raise TimeoutError("hard stop")
    assert lb.run_guarded(slow).status == "not_completed"


def test_engine_outcome_keeps_its_failure():
    # A zeroed yield is a result: the task reports "fail" and it stays "fail".
    out = lb.run_guarded(lambda: Result("fail"))
    assert out.status == "fail" and out.error is None
    assert lb.run_guarded(lambda: {"status": "pass"}).status == "pass"


@pytest.mark.parametrize("value", [None, Result(None), Result("passed"), 42])
def test_result_without_a_valid_status_is_not_completed(value):
    assert lb.run_guarded(lambda: value).status == "not_completed"


def test_l0_fail_comes_first():
    # protocol.label.order: "L0 fails" gives "Model not validated here" before any other row
    for t0, t1, p1, a1 in (("pass", "pass", "pass", "pass"), ("not_run", "not_run", "not_run", "not_run"),
                           ("not_completed", "pass", "pass", "pass")):
        assert lb.label(t0, t1, p1, a1, 10, n_required=10, l0="fail") == lb.MODEL_NOT_VALIDATED
    for l0 in ("pass", "not_run", "not_completed"):
        assert lb.label("pass", "pass", "pass", "pass", 10, n_required=10, l0=l0) == lb.DIRECTION_SUPPORTED
    assert lb.label("pass", "pass", "pass", "pass", 10, n_required=10) == lb.DIRECTION_SUPPORTED
    assert lb.ORDER[0] == ("L0 fails", lb.MODEL_NOT_VALIDATED)
    with pytest.raises(ValueError):
        lb.label("pass", "pass", "pass", "pass", 10, n_required=10, l0="passed")


# Written with an SI tool (Claude, Anthropic) for NASA Space Apps 2026. See AI_USE.md.
"""Scoreboard complete (section 8.6, criterion M4.8), on synthetic task outputs
labelled "example, fictitious". No engine run, no trial value.

L0, L1, T0, T1, P1, A1, A2, A3, N1, H1, H2 and R1 are always present with a
status, with S by site and t1_failed_on. A failure shows as "fail", an
infrastructure failure as "not_completed", a test not tried as "not_run". The
page carries no p-value and no confidence interval.

The example output lives in tests/fixtures/scoreboard_example.json and
scoreboard_results_example.json. After a deliberate change, rewrite both with
``.venv/bin/python -m tests.test_scoreboard`` and review the diff.
"""

import functools
import hashlib
import json
from fractions import Fraction
from pathlib import Path

import pytest
import yaml

from pipeline.bench import a1, ablation, common, l0, l1, label, n1, p1, r1, scoreboard, t0, t1
from pipeline.bench.common import Pair

ROOT = Path(__file__).resolve().parents[1]
FIX = Path(__file__).parent / "fixtures"


def load(name):
    return json.loads((FIX / name).read_text(encoding="utf-8"))


PROTOCOL = load("bench_protocol.json")
T0FX, T1FX, P1FX, A1FX = load("bench_t0.json"), load("bench_t1.json"), load("bench_p1.json"), load("bench_a1.json")
L0FX, L1FX, AFX, N1FX = load("bench_l0.json"), load("bench_l1.json"), load("bench_ablation.json"), load("bench_n1.json")
EXAMPLE = FIX / "scoreboard_example.json"
RESULTS_EXAMPLE = FIX / "scoreboard_results_example.json"
SITES = ["Synthetic_site_a", "Synthetic_site_b", "Synthetic_site_c"]
META = {"region_id": "example_region", "protocol_hash": "0" * 64}
EXAMPLE_NOTE = ("Example, fictitious. Built by tests/test_scoreboard.py from synthetic task outputs "
                "(tests/fixtures/bench_*.json). Not a bench result, not trial data, not engine output.")


# Synthetic task outputs ------------------------------------------------------------

def _scenario(fx, name):
    return next(s for s in fx.get("scenarios", fx.get("cases", [])) if s["name"] == name)


def _pairs(fx, key, simulated, n=10):
    return [Pair(a, b, o, s) for a, b, o, s in zip(fx["sites"], fx["seasons"], fx[key], simulated)][:n]


def t0_result(name="perfect", n=10):
    return t0.evaluate(_pairs(T0FX, "observed", _scenario(T0FX, name)["simulated"], n), t0.T0Rule.from_protocol(PROTOCOL))


def t1_result(name="s21_rmse_below", n=10):
    return t1.evaluate(_pairs(T1FX, "observed_delta", _scenario(T1FX, name)["simulated"], n),
                       t1.T1Rule.from_protocol(PROTOCOL))


def p1_result(t0_case="all_below_p_0_10", t1_case="all_below_p_0_10", drop_shift=None):
    design = p1.P1Design.from_protocol(PROTOCOL)

    def arm(name, case):
        c = _scenario(P1FX, case)
        by_shift = dict(zip(design.shifts, c["s_shifted"]))
        if drop_shift is not None:
            by_shift[drop_shift] = None
        return p1.evaluate_arm(name, c["s_real"], by_shift, design)
    return p1.combine(arm("T0", t0_case), arm("T1", t1_case))


def a1_result(name="count_5_pass"):
    c = _scenario(A1FX, name)
    recs = [a1.A1Record(s, y, o, w, wo) for s, y, o, w, wo in
            zip(A1FX["sites"], A1FX["seasons"], A1FX["observed_delta"], c["with_state"], c["without_state"])]
    return a1.evaluate(recs, a1.A1Rule.from_protocol(PROTOCOL))


def l0_result():
    found = []
    for s in L0FX["seasons"]:
        found += l0.detect_artifacts(s["arm"], s["season"], dry_yield_t_ha=s["dry_yield_t_ha"],
                                     crop_dead=s["crop_dead"], days_to_emergence=s["days_to_emergence"])
    found = l0.explain(found, {tuple(k.split("|")): v for k, v in L0FX["explanations"].items()})
    cn = l0.cn_rew_variation(L0FX["cn_rew"]["computed"], L0FX["cn_rew"]["fixed_61_9"])
    return l0.evaluate(L0FX["gaps_mm"], L0FX["run"], L0FX["run"], found, l0.L0Rule.from_protocol(PROTOCOL), cn)


def l1_result():
    return l1.evaluate(L1FX["site"], [l1.RainSeason(**r) for r in L1FX["rain"]],
                       [l1.TemperatureBias(**t) for t in L1FX["temperature"]], l1.L1Rule.from_protocol(PROTOCOL))


def a2_result():
    from datetime import date
    c = AFX["a2"]["dry_autumn"]
    years = {int(y) for y in c["main"]}
    return ablation.evaluate_a2(
        [ablation.L2Count("dry_autumn", {int(k): v for k, v in c["main"].items()},
                          {int(k): v for k, v in c["check"].items()},
                          {y: (date(y, 3, 1), date(y, 5, 31)) for y in years})],
        ablation.A2Rule.from_protocol(PROTOCOL))


def a3_result():
    return ablation.evaluate_a3(AFX["a3"]["with"], AFX["a3"]["without_changes"], ablation.A3Rule.from_protocol(PROTOCOL))


def n1_result():
    rule = n1.N1Rule.from_protocol(PROTOCOL, percentile=N1FX["percentile"], method="nearest_rank")
    return n1.evaluate(N1FX["halves_days"], N1FX["l30_vs_s30_days"], rule)


def r1_result(t1res):
    rule = r1.R1Rule.from_protocol(PROTOCOL, band=[10, 90], method="nearest_rank")
    seasons = ["2018/19", "2019/20", "2020/21"]
    run = {y: {"S1": 3000, "S3": 3400} for y in seasons}
    fld = r1.R1Field("field_a", run, {m: run for m in range(rule.members)})
    return r1.evaluate([fld], rule, t1_status=t1res.status, t1_mse=t1res.mse)


@functools.cache
def _outcomes():
    t1res = t1_result()
    return {"L0": l0_result(), "L1": l1_result(), "T0": t0_result(), "T1": t1res, "P1": p1_result(),
            "A1": a1_result(), "A2": a2_result(), "A3": a3_result(), "N1": n1_result(), "R1": r1_result(t1res)}


def all_outcomes():
    """Every task passing (or reported), computed once; each test gets its own dict."""
    return dict(_outcomes())


def build(outcomes, **kw):
    args = dict(META)
    args.update(kw)
    return scoreboard.build(outcomes, PROTOCOL, **args)


def entries_by_id(board):
    return {t["id"]: t for t in board["tests"]}


def walk_keys(obj):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield k
            yield from walk_keys(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from walk_keys(v)


# Shape (M4.8) --------------------------------------------------------------------

def test_every_test_is_present_in_order_with_a_status():
    for outcomes in ({}, all_outcomes()):
        board, results = build(outcomes)
        assert [t["id"] for t in board["tests"]] == list(scoreboard.TEST_IDS)
        assert list(results["tests"]) == list(scoreboard.TEST_IDS)
        for t in board["tests"]:
            assert set(t) == {"id", "status", "statistic", "value", "threshold", "n", "note"}
            assert t["status"] in common.STATUSES + (common.REPORTED,)


def test_top_level_keys_follow_section_8_6():
    board, _ = build(all_outcomes())
    assert set(board) == {"schema_version", "region_id", "label_id", "label_text", "completed",
                          "protocol_hash", "not_blind", "exploratory", "site_seasons",
                          "s_by_site", "tests", "t1_failed_on", "noise", "correlation_ceiling", "late_notes"}
    assert board["not_blind"] is True and board["exploratory"] is True
    assert list(board["s_by_site"]) == SITES


def test_all_pass_gives_direction_supported():
    board, results = build(all_outcomes())
    by = entries_by_id(board)
    assert board["label_id"] == label.DIRECTION_SUPPORTED
    assert board["label_text"] == label.LABEL_TITLE[label.DIRECTION_SUPPORTED]
    assert board["completed"] is True
    assert board["t1_failed_on"] is None
    assert board["s_by_site"] == {"Synthetic_site_a": 3, "Synthetic_site_b": 4, "Synthetic_site_c": 1}
    assert board["site_seasons"] == {"expected": 10, "complete": 10, "by_site": {
        "Synthetic_site_a": {"expected": 3, "complete": 3}, "Synthetic_site_b": {"expected": 4, "complete": 4},
        "Synthetic_site_c": {"expected": 3, "complete": 3}}}
    assert {k: by[k]["status"] for k in by} == {
        "L0": "pass", "L1": "pass", "T0": "pass", "T1": "pass", "P1": "pass", "A1": "pass",
        "A2": "reported", "A3": "reported", "N1": "reported", "H1": "not_run", "H2": "not_run", "R1": "pass"}
    assert by["T1"]["threshold"]["rmse_lt_kg_ha"] == 280.0
    assert by["R1"]["value"]["fields"][0]["state"] == r1.LOWER
    assert results["label_id"] == board["label_id"]


def test_nothing_run_is_not_completed_and_every_site_is_listed():
    board, results = build({})
    assert board["label_id"] == label.NOT_COMPLETED_LABEL
    assert board["completed"] is False
    assert board["s_by_site"] == dict.fromkeys(SITES)
    assert board["site_seasons"]["complete"] == 0
    for t in board["tests"]:
        assert t["status"] == "not_run" and t["value"] is None
    assert entries_by_id(board)["T0"]["threshold"] == {"s_min": 21, "within_site_s_sum_gt": 0}
    assert results["tests"]["T1"] == {"id": "T1", "status": "not_run"}


def test_h2_is_not_run_from_the_protocol_and_cannot_be_passed_in():
    h2 = entries_by_id(build(all_outcomes())[0])["H2"]
    assert h2["status"] == "not_run"
    assert "SA-D56" in h2["note"] and "no local review" in h2["note"]
    with pytest.raises(ValueError):
        build({"H2": {"id": "H2", "status": "pass"}})


# Failures, infrastructure and untested ---------------------------------------------

def test_t1_failure_shows_as_failure_with_its_reason():
    outcomes = all_outcomes()
    outcomes["T1"] = t1_result("offset_280")
    outcomes.pop("R1")
    board, _ = build(outcomes)
    by = entries_by_id(board)
    assert by["T1"]["status"] == "fail"
    assert board["t1_failed_on"] == "size"
    assert "failed on size" in by["T1"]["note"]
    assert board["label_id"] == label.DIRECTION_NOT_VALIDATED
    assert by["R1"]["status"] == "not_run" and "inconclusive" in by["R1"]["note"]


def test_crash_is_not_completed_and_the_other_tasks_stand():
    def broken():
        raise FileNotFoundError("data/runs/a1_chain.csv")
    outcomes = all_outcomes()
    outcomes["A1"] = label.run_guarded(broken)
    board, results = build(outcomes)
    by = entries_by_id(board)
    assert by["A1"]["status"] == "not_completed"
    assert "never counts as a failure" in by["A1"]["note"]
    assert "FileNotFoundError" not in json.dumps(board)
    assert "FileNotFoundError" in results["tests"]["A1"]["error"]
    assert board["label_id"] == label.MECHANISM_NOT_SHOWN
    assert by["T1"]["status"] == "pass" and by["P1"]["status"] == "pass"


def test_t0_crash_gives_not_completed_label():
    outcomes = all_outcomes()
    outcomes["T0"] = label.run_guarded(lambda: (_ for _ in ()).throw(TimeoutError("hard stop")))
    board, _ = build(outcomes)
    assert entries_by_id(board)["T0"]["status"] == "not_completed"
    assert board["label_id"] == label.NOT_COMPLETED_LABEL
    assert board["completed"] is False


def test_incomplete_p1_shows_no_partial_result():
    outcomes = all_outcomes()
    outcomes["P1"] = p1_result(drop_shift=36)
    by = entries_by_id(build(outcomes)[0])
    assert by["P1"]["status"] == "not_completed" and by["P1"]["value"] is None
    assert "no partial result" in by["P1"]["note"]


def test_fewer_site_seasons_is_exploratory_and_not_validated():
    outcomes = all_outcomes()
    outcomes["T0"], outcomes["T1"] = t0_result(n=9), t1_result(n=9)
    outcomes.pop("R1")
    board, _ = build(outcomes)
    by = entries_by_id(board)
    assert by["T0"]["status"] == by["T1"]["status"] == "exploratory"
    assert by["T1"]["note"].startswith("fewer site-seasons")
    assert board["site_seasons"]["complete"] == 9
    assert board["site_seasons"]["by_site"]["Synthetic_site_c"] == {"expected": 3, "complete": 2}
    assert board["label_id"] == label.MODEL_NOT_VALIDATED


def test_r1_output_without_a_t1_pass_is_not_read():
    outcomes = all_outcomes()
    outcomes["T1"] = t1_result("offset_280")
    board, results = build(outcomes)
    by = entries_by_id(board)
    assert by["R1"]["status"] == "not_run" and by["R1"]["value"] is None
    assert "was not read" in by["R1"]["note"]
    assert results["tests"]["R1"]["status"] == "not_run"


def test_r1_not_run_by_its_own_gate():
    outcomes = all_outcomes()
    outcomes["T1"] = t1_result("offset_280")
    outcomes["R1"] = r1.not_run("fail", ["field_a"])
    by = entries_by_id(build(outcomes)[0])
    assert by["R1"]["status"] == "not_run"


@pytest.mark.parametrize("tid, status", [
    ("T1", "passed"), ("T1", "reported"), ("P1", "exploratory"), ("A2", "pass"), ("N1", "fail"), ("L0", None),
])
def test_status_outside_the_schema_is_refused(tid, status):
    with pytest.raises(ValueError):
        build({tid: {"id": tid, "status": status}})


def test_unknown_test_or_mismatched_id_is_refused():
    with pytest.raises(ValueError):
        build({"T2": {"id": "T2", "status": "pass"}})
    with pytest.raises(ValueError):
        build({"T0": t1_result()})


# What the page never shows -------------------------------------------------------

def test_no_p_value_no_ci_and_no_r1_delta_on_the_page():
    board, results = build(all_outcomes())
    keys = set(walk_keys(board))
    assert not keys & {"p", "p_value", "alpha", "ci", "band_kg_ha", "base_median_kg_ha"}
    assert "p" in results["tests"]["A1"] and "p" in results["tests"]["P1"]["t1"]
    by = entries_by_id(board)
    assert by["P1"]["value"]["t1"]["wrong_year_s_at_least"] == 0
    assert by["P1"]["threshold"] == {"wrong_year_s_at_least_max": 0, "shifts": 9}


def test_a_p_value_slipped_into_h1_is_refused():
    with pytest.raises(ValueError):
        build({"H1": {"id": "H1", "status": "reported", "value": {"p": 0.04}, "n": 3}})


def test_h1_result_passes_through():
    board, _ = build({"H1": {"id": "H1", "status": "reported", "value": {"1": "3 of 3"}, "n": 3,
                             "note": "informal"}})
    h1 = entries_by_id(board)["H1"]
    assert (h1["status"], h1["value"], h1["n"], h1["note"]) == ("reported", {"1": "3 of 3"}, 3, "informal")


# Extras ----------------------------------------------------------------------------

def test_noise_and_correlation_ceiling():
    records = [("synthetic_site_a", 86, 151), ("synthetic_site_a", 417, 125), ("synthetic_site_a", -142, 254),
               ("synthetic_site_b", -223, None), ("synthetic_site_b", 446, None),
               ("synthetic_site_c", 85, 85), ("synthetic_site_c", 623, 99)]
    noise = scoreboard.noise_summary(records)
    assert noise == {"n_within_1_sed": 3, "n_with_sed": 5, "sites_without_sed": ["Synthetic_site_b"]}
    board, _ = build({}, noise=noise, correlation_ceiling={"low": 0.5, "high": Fraction(3, 4)})
    assert board["noise"] == noise
    assert board["correlation_ceiling"] == {"low": 0.5, "high": 0.75}
    with pytest.raises(ValueError):
        build({}, noise={"n_with_sed": 1})
    with pytest.raises(ValueError):
        build({}, correlation_ceiling={"low": 0.5})


def test_late_notes_are_kept_without_their_time():
    # SA-D104: a late note keeps its text, never its time.
    note = {"at": "2000-01-01T00:00:00-03:00", "text": "H1 recorded after the freeze (example)"}
    assert build({}, late_notes=[note])[0]["late_notes"] == [{"text": "H1 recorded after the freeze (example)"}]


def test_no_date_or_commit_in_either_document():
    board, results = build(all_outcomes())
    for doc in (board, results):
        assert not {"frozen_at", "protocol_commit"} & set(doc)


def test_site_outside_the_protocol_is_refused():
    with pytest.raises(ValueError):
        build({"T1": {**t1_result().to_dict(), "s_by_site": {"elsewhere": 3}}})


def test_dicts_read_back_from_json_give_the_same_board():
    outcomes = all_outcomes()
    as_json = {k: json.loads(json.dumps(v.to_dict())) for k, v in outcomes.items()}
    assert build(as_json) == build(outcomes)


# Files -----------------------------------------------------------------------------

def example_documents():
    board, results = build(all_outcomes(), noise=None, correlation_ceiling=None)
    return ({"_note": EXAMPLE_NOTE, "scoreboard": board}, {"_note": EXAMPLE_NOTE, "results": results})


def test_example_output_is_current():
    board, results = example_documents()
    assert json.loads(EXAMPLE.read_text(encoding="utf-8")) == board
    assert json.loads(RESULTS_EXAMPLE.read_text(encoding="utf-8")) == results


def test_write_and_read_back(tmp_path):
    board, results = build(all_outcomes())
    site, res = tmp_path / "site" / "data" / "scoreboard.json", tmp_path / "data" / "bench" / "results.json"
    scoreboard.write(board, results, site, res)
    assert json.loads(site.read_text(encoding="utf-8")) == board
    assert json.loads(res.read_text(encoding="utf-8")) == results
    assert site.read_text(encoding="utf-8").endswith("}\n")


def test_main_reads_task_files(tmp_path):
    tasks = tmp_path / "tasks"
    tasks.mkdir()
    for tid, result in all_outcomes().items():
        if tid not in ("P1", "R1"):
            (tasks / f"{tid}.json").write_text(json.dumps(result.to_dict()), encoding="utf-8")
    config = tmp_path / "region.yaml"
    config.write_text(yaml.safe_dump({"region": "example_region", "protocol": PROTOCOL["protocol"]},
                                     sort_keys=False), encoding="utf-8")
    site, res = tmp_path / "scoreboard.json", tmp_path / "results.json"
    assert scoreboard.main(["--config", str(config), "--tasks", str(tasks), "--planned", "T0,T1,P1,A1",
                            "--site-out", str(site), "--results-out", str(res)]) == 0
    board = json.loads(site.read_text(encoding="utf-8"))
    by = entries_by_id(board)
    assert by["P1"]["status"] == "not_completed"        # planned, no file: did not finish
    assert by["R1"]["status"] == "not_run"              # not planned, no file
    assert board["label_id"] == label.MECHANISM_NOT_SHOWN
    assert board["protocol_hash"] == scoreboard.protocol_hash(config)


def test_protocol_hash_follows_protocol_md():
    config = ROOT / "config" / "region_norte_pr.yaml"
    if not config.exists():
        pytest.skip("no config")
    text = config.read_bytes()
    expected = hashlib.sha256(text[text.index(b"\nprotocol:\n") + 1:]).hexdigest()
    assert scoreboard.protocol_hash(config) == expected


def test_default_paths():
    assert scoreboard.SITE_PATH == ROOT / "site" / "data" / "scoreboard.json"
    assert scoreboard.RESULTS_PATH == ROOT / "data" / "bench" / "results.json"


if __name__ == "__main__":   # rewrite the example output after a deliberate change
    b, r = example_documents()
    EXAMPLE.write_text(json.dumps(b, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    RESULTS_EXAMPLE.write_text(json.dumps(r, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def test_an_l0_fail_gives_model_not_validated_first():
    # protocol.label.order starts with "L0 fails" (SA-D65)
    import dataclasses

    outcomes = all_outcomes()
    outcomes["L0"] = dataclasses.replace(outcomes["L0"], status="fail")
    board, results = build(outcomes)
    assert board["label_id"] == label.MODEL_NOT_VALIDATED == results["label_id"]



def test_a2_not_run_keeps_its_reason():
    note = "the count A2 checked was removed, SA-D68"
    a2 = [t for t in build({"A2": {"id": "A2", "status": "not_run", "note": note}})[0]["tests"] if t["id"] == "A2"][0]
    assert a2["status"] == "not_run" and a2["note"].endswith(note) and a2["value"] is None

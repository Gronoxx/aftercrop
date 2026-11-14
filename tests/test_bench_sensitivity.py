# Written with an SI tool (Claude, Anthropic) for NASA Space Apps 2026. See AI_USE.md.
"""Soil sensitivity after the freeze (SA-D77): pipeline.bench.sensitivity and make sensitivity-soil.

Synthetic tree of tests/test_bench_runners.py ("example, fictitious"): the trial runs are copied
to a sensitivity runs folder, the bench tasks run there into their own bench folder, the
scoreboard builder computes the label, and the sensitivity document copies it. The frozen
scoreboard is only read. Nothing here reads data/ or site/.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from pipeline.bench import label, run_tasks, scoreboard
from pipeline.bench import sensitivity as sens
from pipeline.ledger import build as lb
from pipeline.ledger import registry as reg
from test_bench_runners import OPTIONS, SITES, make_tree

ROOT = Path(__file__).resolve().parents[1]
MAKE = shutil.which("make")


def diagnosis_doc(runs: Path) -> dict:
    soil = {"file": "x.json", "sha256": "0" * 64, "clay_pct_min": 40.0, "clay_pct_max": 50.0,
            "aw_mm_0_1m": 120.0, "aw_mm_0_2_4m": 290.0}
    used = dict(soil, clay_pct_min=78.0, clay_pct_max=78.0, aw_mm_0_1m=60.0, aw_mm_0_2_4m=160.0)
    return {"formula": "texture formula", "sites": {
        s: {"soils": {"used": used, "sensitivity": soil},
            "zero_yield": {"base": {"zero_yield": 2, "simulated": 4}, "a1": None, "p1": None}}
        for s in SITES}}


@pytest.fixture()
def built(tmp_path):
    tree = make_tree(tmp_path)
    data = tree["data"]
    runs_sens = tmp_path / "runs_sens"
    shutil.copytree(data / "runs", runs_sens)
    bench_sens = tmp_path / "bench_sens"
    common = ["--config", str(tree["config"]), "--data", str(data), "--trial-meta", str(tmp_path / "trial_dates.json")]
    assert run_tasks.main(["L0", *common, "--bench", str(bench_sens), "--field", OPTIONS["field"]]) == 0
    for tid in ("T0", "T1", "P1", "A1"):
        assert run_tasks.main([tid, *common, "--bench", str(bench_sens), "--runs", str(runs_sens)]) == 0
    sens_board = bench_sens / "scoreboard.json"
    assert scoreboard.main(["--config", str(tree["config"]), "--tasks", str(bench_sens / "tasks"),
                            "--planned", "L0,T0,T1,P1,A1", "--site-out", str(sens_board),
                            "--results-out", str(bench_sens / "results.json")]) == 0
    frozen = tmp_path / "site" / "scoreboard.json"
    frozen.parent.mkdir()
    frozen_doc = {"label_id": "model_not_validated_here", "tests": []}
    frozen.write_text(json.dumps(frozen_doc), encoding="utf-8")
    diag = tmp_path / "diag.json"
    diag.write_text(json.dumps(diagnosis_doc(runs_sens)), encoding="utf-8")
    out = tmp_path / "site" / "sensitivity.json"
    before = frozen.read_bytes()
    assert sens.main(["--scoreboard", str(sens_board), "--frozen", str(frozen), "--diagnosis", str(diag),
                      "--runs", str(runs_sens), "--out", str(out)]) == 0
    assert frozen.read_bytes() == before                     # the frozen scoreboard is only read
    return {"doc": json.loads(out.read_text(encoding="utf-8")),
            "board": json.loads(sens_board.read_text(encoding="utf-8")), "frozen": frozen_doc}


def test_label_comes_from_the_label_code_through_the_scoreboard(built):
    doc, board = built["doc"], built["board"]
    status = {t["id"]: t["status"] for t in board["tests"]}
    expected = label.label(status["T0"], status["T1"], status["P1"], status["A1"],
                           board["site_seasons"]["complete"], n_required=board["site_seasons"]["expected"],
                           l0=status["L0"])
    assert doc["label_id"] == expected == board["label_id"]
    assert doc["label_text"] == label.label_text(expected)


def test_marked_as_sensitivity_and_never_frozen(built):
    doc = built["doc"]
    assert doc["role"] == "sensitivity after the freeze" and doc["frozen"] is False
    assert doc["decision"] == "SA-D77"
    assert doc["frozen_label_id"] == built["frozen"]["label_id"]
    # SA-D104: no run time, no freeze time, no commit.
    assert not {"run_at", "frozen_at", "protocol_commit"} & set(doc)


def test_tests_are_the_scoreboard_rows_of_the_five_label_tests(built):
    doc, board = built["doc"], built["board"]
    rows = {t["id"]: t for t in board["tests"]}
    assert [t["id"] for t in doc["tests"]] == ["L0", "T0", "T1", "P1", "A1"]
    for t in doc["tests"]:
        assert t["ledger"] == f"sensitivity.tests.{t['id']}"
        expect = dict(rows[t["id"]])
        got = {k: v for k, v in t.items() if k != "ledger"}
        if t["id"] == "L0":
            assert sens.L0_NOTE in got.pop("note")
            expect.pop("note")
        assert got == expect


def test_zero_yields_are_counted_from_the_sensitivity_runs(built):
    z = built["doc"]["zero_yield"]["by_site"]
    for site in SITES:
        entry = z[scoreboard.display_site(site)]
        assert entry["frozen"]["base"] == {"zero": 2, "simulated": 4}
        assert entry["sensitivity"]["base"]["simulated"] > 0


def test_every_number_has_a_declared_ledger_id_and_no_p_value(built):
    doc = built["doc"]
    assert lb.loose_numbers(doc) == []
    ids = {r.id for r in lb.references("sensitivity.json", doc)}
    assert {"sensitivity.soil", "sensitivity.zero_yield", "sensitivity.tests.T0"} <= ids
    for rid in ids:
        assert reg.template_for(rid) is not None, rid
    assert scoreboard._forbidden(doc) == []


def test_refuses_missing_tests_and_writing_over_the_frozen_scoreboard(built, tmp_path):
    with pytest.raises(ValueError, match="lacks tests"):
        sens.build({"label_id": "model_not_validated_here", "label_text": "Model not validated here", "tests": []},
                   {}, {"sites": {}}, tmp_path)
    p = tmp_path / "x.json"
    p.write_text("{}", encoding="utf-8")
    with pytest.raises(SystemExit):
        sens.main(["--scoreboard", str(p), "--frozen", str(p), "--diagnosis", str(p), "--runs", str(tmp_path),
                   "--out", str(p)])


# Make target ----------------------------------------------------------------------------------

needs_make = pytest.mark.skipif(MAKE is None, reason="make not installed")


def make(*args):
    return subprocess.run([MAKE, *args], cwd=ROOT, capture_output=True, text=True, timeout=120)


@needs_make
def test_sensitivity_target_writes_only_its_own_folders(tmp_path):
    out = make("-n", "sensitivity-soil", f"DATA={tmp_path}/d", f"CONFIG={tmp_path}/c.yaml",
               f"SITE_DATA={tmp_path}/s").stdout
    lines = [x for x in out.splitlines() if "pipeline." in x]
    engine = [x for x in lines if "pipeline.engine.cli trials" in x]
    assert [x.split("--variants ")[1].split()[0] for x in engine] == ["base", "a1", "p1"]
    for x in engine:
        assert f"--trial-soil-suffix _soilgrids --runs {tmp_path}/d/runs_sens" in x
        assert f"--config {tmp_path}/d/runs_sens/config.json" in x
    assert f"cmp {tmp_path}/d/runs_sens/config.json {tmp_path}/d/runs/config.json" in out
    bench = [x for x in lines if "pipeline.bench.run_tasks" in x]
    assert [x.split("run_tasks ")[1].split()[0] for x in bench] == ["L0", "T0", "T1", "P1", "A1"]
    assert all(f"--bench {tmp_path}/d/bench_sens" in x for x in bench)
    assert all(f"--runs {tmp_path}/d/runs_sens" in x for x in bench[1:])
    board = next(x for x in lines if "pipeline.bench.scoreboard" in x)
    assert f"--site-out {tmp_path}/d/bench_sens/scoreboard.json" in board
    pub = next(x for x in lines if "pipeline.bench.sensitivity" in x)
    assert f"--out {tmp_path}/s/sensitivity.json" in pub and f"--frozen {tmp_path}/s/scoreboard.json" in pub
    for x in lines:
        assert " data/" not in x and " site/" not in x, x


@needs_make
def test_sensitivity_target_refuses_a_second_run(tmp_path):
    runs = tmp_path / "d" / "runs_sens"
    runs.mkdir(parents=True)
    (runs / "timing.json").write_text("{}", encoding="utf-8")
    res = make("sensitivity-soil", f"DATA={tmp_path}/d", f"CONFIG={tmp_path}/c.yaml", f"SITE_DATA={tmp_path}/s")
    assert res.returncode != 0 and "one run only" in res.stderr
    assert sorted(p.name for p in runs.iterdir()) == ["timing.json"]


@needs_make
def test_sensitivity_publisher_declares_the_flags_the_target_passes():
    line = next(x for x in make("-n", "sensitivity-soil").stdout.splitlines() if "-m pipeline.bench.sensitivity" in x)
    help_text = subprocess.run([str(ROOT / ".venv" / "bin" / "python"), "-m", "pipeline.bench.sensitivity", "--help"],
                               cwd=ROOT, capture_output=True, text=True, timeout=60).stdout
    for flag in [w for w in line.split() if w.startswith("--")]:
        assert flag in help_text, flag

# Written with an SI tool (Claude, Anthropic) for NASA Space Apps 2026. See AI_USE.md.
"""Makefile: one target per task (plan P0.3), make -n sane, and M4.11 with make -k bench.

The make runs here use a synthetic tree in a temporary folder (tests/test_bench_runners.py,
"example, fictitious"): CONFIG, DATA, SITE_DATA and SITE_IMG point there, never at data/ or site/.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from test_bench_runners import OPTIONS, make_tree

ROOT = Path(__file__).resolve().parents[1]
MAKEFILE = ROOT / "Makefile"
NOTICE = "# Written with an SI tool (Claude, Anthropic) for NASA Space Apps 2026. See AI_USE.md."
MAKE = shutil.which("make")

# Targets of plan P0.3 ("um alvo de Make por tarefa, com make -k"; fumaça antes das 00:00; piso)
# and of the M9 order that this session owns: one per bench task, the engine runs, the floor.
P03_TARGETS = ("piso", "decide", "field-inputs", "basemap-site", "soil", "relief", "slopes", "select", "chains",
               "chain", "smoke",
               "bench", "scoreboard", "l0", "l1", "t0", "t1", "p1", "a1", "a2", "a3", "n1", "r1", "h1")
EXISTING = ("test", "test-py", "test-engine", "test-js", "data", "zarc", "power", "soilgrids", "hls", "basemap",
            "nasadem", "gloreda", "manifest", "ledger", "assemble", "sheet-pdf", "copy-json", "serve", "draw",
            "fields-check")
BENCH_MODULE = "pipeline.bench.run_tasks"

needs_make = pytest.mark.skipif(MAKE is None, reason="make not installed")


def text():
    return MAKEFILE.read_text(encoding="utf-8")


def rules():
    out = set()
    for line in text().splitlines():
        m = re.match(r"^([A-Za-z0-9_][A-Za-z0-9_ .-]*?)\s*:(?!=)", line)
        if m and not line.startswith("\t"):
            out.update(m.group(1).split())
    return out


def phony():
    return {t for line in text().splitlines() if line.startswith(".PHONY:") for t in line.split(":", 1)[1].split()}


def make(*args, check=True, cwd=ROOT, timeout=300):
    res = subprocess.run([MAKE, *args], cwd=cwd, capture_output=True, text=True, timeout=timeout)
    if check and res.returncode != 0:
        raise AssertionError(f"make {' '.join(args)} exited {res.returncode}\n{res.stdout}\n{res.stderr}")
    return res


def test_notice_on_the_first_line():
    assert text().splitlines()[0] == NOTICE


@pytest.mark.parametrize("target", P03_TARGETS + EXISTING)
def test_target_exists_and_is_phony(target):
    assert target in rules(), f"no rule for {target}"
    assert target in phony(), f"{target} is not .PHONY"


@needs_make
@pytest.mark.parametrize("target", P03_TARGETS)
def test_dry_run_is_sane(target):
    res = make("-n", target)
    assert res.returncode == 0
    assert res.stdout.strip(), f"make -n {target} prints nothing"


@needs_make
def test_bench_runs_every_task_then_the_scoreboard():
    out = make("-n", "bench").stdout
    for tid in ("L0", "l1", "t0", "t1", "p1", "a1", "A2", "a3", "N1", "R1", "h1"):
        assert f"{BENCH_MODULE} {tid} " in out, tid
    assert out.index(f"{BENCH_MODULE} t1 ") < out.index(f"{BENCH_MODULE} R1 ")     # R1 reads T1.json
    assert "-k l0 l1 t0 t1 p1 a1 a2 a3 n1 r1 h1" in out
    assert out.rstrip().splitlines()[-1].startswith(".venv/bin/python -m pipeline.bench.scoreboard")
    assert '--planned "L0,L1,T0,T1,P1,A1,A2,A3,N1,R1,H1"' in out


FIELD_VARS = ("L0_FIELD", "FIELDS", "FIELD_YEARS", "FIELD_PREFIX_YEARS", "FIELD_CORN_SOWING",
              "FIELD_WHEAT_SOWING", "FIELD_INITIAL", "FIELD_WEATHER_SITE")


@needs_make
def test_engine_targets_use_the_engine_venv_and_the_json_config():
    out = make("-n", "chains").stdout
    assert "pipeline.engine.cli config-json --config config/region_norte_pr.yaml --out data/runs/config.json" in out
    for variant in ("base", "a1", "p1"):
        assert f".venv-engine/bin/python -m pipeline.engine.cli trials --config data/runs/config.json" in out
        assert f"--variants {variant}" in out
    # The field-chain values were decided by the team lead (R2, late note): the defaults run them.
    assert ("pipeline.engine.cli field --config data/runs/config.json --data data  --field a --field b --field c "
            "--prefix-years 1 --corn-sowing 03-22 --wheat-sowing 05-24 --initial field_capacity "
            "--weather-site londrina --years 2018,2019,2020,2021,2022,2023,2024") in out
    cleared = make("-n", "chains", *(f"{v}=" for v in FIELD_VARS)).stdout
    assert "set FIELDS FIELD_YEARS" in cleared      # without values, the open points are named, not guessed


@needs_make
def test_overridden_paths_reach_every_command(tmp_path):
    out = make("-n", "bench", "piso", "chains-base", f"DATA={tmp_path}/d", f"CONFIG={tmp_path}/c.yaml",
               f"SITE_DATA={tmp_path}/s", f"SITE_IMG={tmp_path}/i").stdout
    for line in out.splitlines():
        if BENCH_MODULE in line or "pipeline.bench.scoreboard" in line or "pipeline.engine.cli" in line \
                or "precompute" in line:
            assert " data/" not in line and " site/" not in line, line


# Wired steps of other modules: (target, module, extra make variables). Their flags must be the
# ones the module's own argparse declares.
WIRED = [
    ("soil", "pipeline.fields.soil_profile", ()),
    ("relief", "pipeline.fields.relief", ()),
    ("slopes", "pipeline.fields.relief", ()),
    ("select", "pipeline.fields.select", ()),
    ("field-inputs", "pipeline.decision.field_inputs",
     ("DECISION_BASE=base.json", "POWER_SITE=x", "N1_PERCENTILE=90", "N1_METHOD=nearest_rank")),
    ("decide", "pipeline.decision.precompute", ()),
    ("ledger", "pipeline.ledger.build", ()),
]


@needs_make
@pytest.mark.parametrize("target, module, extra", WIRED)
def test_wired_commands_use_flags_the_module_declares(target, module, extra):
    line = next(x for x in make("-n", target, *extra).stdout.splitlines() if f"-m {module}" in x)
    flags = re.findall(r"(?<!\S)(--[a-z][a-z0-9-]*)", line)
    help_text = subprocess.run([str(ROOT / ".venv" / "bin" / "python"), "-m", module, "--help"], cwd=ROOT,
                               capture_output=True, text=True, timeout=120).stdout
    assert help_text, module
    for flag in flags:
        assert flag in help_text, f"make {target}: {module} does not declare {flag}"


@needs_make
def test_slopes_feed_select_and_ledger_is_strict():
    slopes = make("-n", "slopes").stdout
    select = make("-n", "select").stdout
    assert "--slopes data/interim/candidates_slopes.json" in slopes and "--slopes data/interim/candidates_slopes.json" in select
    assert "pipeline.ledger.build --m5-ledger data/build/ledger_m5.json --strict" in make("-n", "ledger").stdout
    decide = make("-n", "decide").stdout
    assert "--inputs data/build/precompute_inputs.json --out site/data --build-out data/build" in decide
    assert "--zarc data/interim/zarc_norte_pr.csv --real" in decide


@needs_make
def test_field_inputs_names_its_base(tmp_path):
    res = make("field-inputs", f"DATA={tmp_path}", "DECISION_BASE=", check=False)
    assert res.returncode != 0 and "set DECISION_BASE" in res.stderr
    assert not (tmp_path / "build").exists()


@needs_make
def test_decision_base_feeds_field_inputs(tmp_path):
    """P2: DECISION_BASE defaults to the output of make decision-base, which reads the trial
    transcription only with DECISION_TRIALS=manual (refused by the module until the tag)."""
    out = make("-n", "field-inputs", f"DATA={tmp_path}").stdout
    assert f"--base {tmp_path}/build/decision_base.json" in out
    dry = make("-n", "decision-base", f"DATA={tmp_path}").stdout
    assert "-m pipeline.decision.base_file --trials manual " in dry and f"--out {tmp_path}/build/decision_base.json" in dry
    ex = make("-n", "decision-base", "DECISION_TRIALS=example", "DECISION_SETTINGS=s.json").stdout
    assert "--trials example " in ex and "--settings s.json" in ex


@needs_make
@pytest.mark.parametrize("target", ["chains-field", "smoke"])
def test_field_targets_name_their_open_points(tmp_path, target):
    tree = make_tree(tmp_path)
    res = make(target, f"CONFIG={tree['config']}", f"DATA={tree['data']}", *(f"{v}=" for v in FIELD_VARS),
               check=False)
    assert res.returncode != 0
    assert "set FIELDS FIELD_YEARS FIELD_PREFIX_YEARS" in res.stderr and "[DECIDE]" in res.stderr
    assert (tree["data"] / "runs" / "config.json").exists()          # engine-config ran, in the tree
    assert not (tree["data"] / "runs" / "smoke").exists()


@needs_make
def test_basemap_site_copies_the_window_files(tmp_path):
    tree = make_tree(tmp_path)
    cfg = tree["config"]
    doc = yaml.safe_load(cfg.read_text(encoding="utf-8"))
    doc["sources"]["hls"] = {"window": {"id": "example_window"}}
    cfg.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    interim = tree["data"] / "interim"
    (interim / "basemap_example_window.png").write_bytes(b"\x89PNG example, fictitious")
    (interim / "basemap_example_window.json").write_text('{"_note": "example, fictitious"}', encoding="utf-8")
    img = tmp_path / "img"
    make("basemap-site", f"CONFIG={cfg}", f"DATA={tree['data']}", f"SITE_IMG={img}")
    assert (img / "basemap.png").read_bytes() == b"\x89PNG example, fictitious"
    assert json.loads((img / "basemap.json").read_text(encoding="utf-8"))["_note"] == "example, fictitious"


@needs_make
def test_make_k_bench_isolates_a_broken_task(tmp_path):
    """M4.11: one task is knocked down by its input, the others run, the scoreboard is written."""
    tree = make_tree(tmp_path)
    (tree["data"] / "interim" / "l1_stations.json").write_text("{not json", encoding="utf-8")
    site = tmp_path / "site_data"
    args = ["-k", "bench", f"CONFIG={tree['config']}", f"DATA={tree['data']}", f"SITE_DATA={site}",
            f"L0_FIELD={OPTIONS['field']}", f"N1_PERCENTILE={OPTIONS['n1_percentile']}",
            f"N1_METHOD={OPTIONS['n1_method']}", f"R1_BAND={OPTIONS['r1_band']}", f"R1_METHOD={OPTIONS['r1_method']}",
            f"A2_THRESHOLD_MM={OPTIONS['a2_threshold_mm']}", f"A2_SPAN={OPTIONS['a2_span']}"]
    res = make(*args, check=False)
    assert res.returncode == 0, res.stdout + res.stderr
    statuses = {p.stem: json.loads(p.read_text(encoding="utf-8"))["status"] for p in tree["tasks"].glob("*.json")}
    assert set(statuses) == {"L0", "L1", "T0", "T1", "P1", "A1", "A2", "A3", "N1", "R1", "H1"}
    assert statuses["L1"] == "not_completed"
    assert all(s != "not_completed" for t, s in statuses.items() if t != "L1"), statuses
    board = json.loads((site / "scoreboard.json").read_text(encoding="utf-8"))
    by = {t["id"]: t for t in board["tests"]}
    assert by["L1"]["status"] == "not_completed" and "never counts as a failure" in by["L1"]["note"]
    assert by["T1"]["status"] == "pass" and by["R1"]["status"] == "pass"
    assert board["label_id"] == "direction_supported_exploratory"
    assert (tree["data"] / "bench" / "results.json").exists()


# Fields (P1): the M2 chain of fields.geojson, chained into piso before field-inputs.
FIELD_STEPS = [("geometry", "pipeline.fields.geometry"), ("series", "pipeline.fields.ndvi_series"),
               ("ribbon", "pipeline.fields.ribbon"), ("events", "pipeline.fields.events"),
               ("noise", "pipeline.fields.noise")]


@pytest.mark.parametrize("target", [t for t, _ in FIELD_STEPS])
def test_field_step_exists_and_is_phony(target):
    assert target in rules() and target in phony()


@needs_make
@pytest.mark.parametrize("target, module", FIELD_STEPS)
def test_field_steps_take_the_fields_file_and_the_rehearsal_flag(target, module):
    product = make("-n", target).stdout
    assert f"-m {module} config/fields/fields.geojson" in product and "--rehearsal" not in product
    rehearsal = make("-n", target, "REHEARSAL=1", "FIELDS_GEOJSON=config/fields/strip.geojson").stdout
    line = next(x for x in rehearsal.splitlines() if f"-m {module}" in x)
    assert line.split()[-2:] == ["config/fields/strip.geojson", "--rehearsal"]
    help_text = subprocess.run([str(ROOT / ".venv" / "bin" / "python"), "-m", module, "--help"], cwd=ROOT,
                               capture_output=True, text=True, timeout=120).stdout
    assert "--rehearsal" in help_text, module


@needs_make
def test_piso_runs_the_field_steps_before_field_inputs():
    out = make("-n", "-k", "piso").stdout
    at = [out.index(f"-m {module} ") for _, module in FIELD_STEPS]
    assert at == sorted(at)
    assert at[-1] < out.index("-m pipeline.decision.base_file ") < out.index("-m pipeline.decision.field_inputs ")


# Engine inputs and the freeze stamp (P3) ------------------------------------------------------

def test_trial_input_targets_exist_and_are_phony():
    for target in ("trial-soil", "power-floresta-printed"):
        assert target in rules() and target in phony()


@needs_make
def test_scoreboard_gets_no_date_and_no_commit():
    # SA-D104: the scoreboard carries no freeze time and no protocol commit.
    line = make("-n", "scoreboard").stdout.rstrip().splitlines()[-1]
    assert "-m pipeline.bench.scoreboard " in line
    assert "--frozen-at" not in line and "--protocol-commit" not in line, line
    sens = make("-n", "sensitivity-soil", check=False).stdout
    assert "--frozen-at" not in sens and "--run-at" not in sens and "--protocol-commit" not in sens

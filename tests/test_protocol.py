# Written with an SI tool (Claude, Anthropic) for NASA Space Apps 2026. See AI_USE.md.
"""Protocol checks, sheet M0 of the plan.

M0.3: PROTOCOL.md carries the required declarations and the exact P1, A1
and H1 items. M0.4 (test_hash): the sha256 of the protocol: block equals the
one printed in PROTOCOL.md. M0.6 (test_opening_checks): four opening checks
filled with a result, and no check time (SA-D104).

Nothing here reads trial values or runs the engine.
"""
from __future__ import annotations

import ast
import hashlib
import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
YAML_PATH = ROOT / "config" / "region_norte_pr.yaml"
PROTOCOL_PATH = ROOT / "PROTOCOL.md"
DECISION_JS = ROOT / "site" / "js" / "decision.mjs"
CROPS_PY = ROOT / "pipeline" / "engine" / "crops.py"

# M0.3, in the words the plan requires (section 11.1).
DECLARATIONS = (
    "The observed values have already been read.",
    "The test is not blind.",
    "The test is exploratory, with a fixed rule.",
    "The contrast and the thresholds derive from the same 10 values.",
    "Only cycle and dates are adjusted.",
    "Everything is published, including failures.",
)


HASH_LINE = re.compile(r"^protocol_block_sha256: ([0-9a-f]{64})$", re.MULTILINE)
BLOCK_START = b"\nprotocol:\n"


def protocol_block_bytes(raw: bytes) -> bytes:
    """Bytes from the line 'protocol:' to the end of the file (PROTOCOL.md, section 4)."""
    assert b"\r" not in raw, "the YAML must use LF line endings"
    assert raw.count(BLOCK_START) == 1, "exactly one top-level protocol: line"
    return raw[raw.index(BLOCK_START) + 1:]


def protocol_sha256(raw: bytes) -> str:
    return hashlib.sha256(protocol_block_bytes(raw)).hexdigest()


@pytest.fixture(scope="module")
def raw_yaml() -> bytes:
    return YAML_PATH.read_bytes()


@pytest.fixture(scope="module")
def config(raw_yaml) -> dict:
    return yaml.safe_load(raw_yaml)


@pytest.fixture(scope="module")
def protocol(config) -> dict:
    return config["protocol"]


@pytest.fixture(scope="module")
def text() -> str:
    return PROTOCOL_PATH.read_text(encoding="utf-8")


def contains(text: str, phrase: str) -> bool:
    return phrase.lower() in text.lower()


# ---- M0.4 -------------------------------------------------------------------

def test_hash(raw_yaml, text):
    printed = HASH_LINE.findall(text)
    assert len(printed) == 1, "PROTOCOL.md prints the hash once"
    assert printed[0] == protocol_sha256(raw_yaml)


def test_protocol_is_last_top_level_key(raw_yaml, config):
    block = protocol_block_bytes(raw_yaml).decode("utf-8").splitlines()
    later_keys = [line for line in block[1:] if re.match(r"^[^\s#]", line)]
    assert later_keys == []
    assert list(config)[-1] == "protocol"


def test_hash_command_is_documented(text):
    assert "python3 -I -c" in text
    assert 't.index(b"\\nprotocol:\\n")+1' in text


# ---- M0.3 -------------------------------------------------------------------

@pytest.mark.parametrize("phrase", DECLARATIONS)
def test_declarations(text, phrase):
    assert phrase in text




def test_p1_exact(protocol, text):
    p1 = protocol["tests"]["P1"]
    assert p1["B"] == 9
    assert p1["shifts_back_years"] == [4, 8, 12, 16, 20, 24, 28, 32, 36]
    assert len(p1["shifts_back_years"]) == p1["B"]
    assert p1["circular_years"] == [1985, 2024]
    assert p1["statistic"] == "kendall_s"
    assert p1["applied_to"] == ["T0", "T1"]
    assert p1["alpha"] == 0.10
    assert p1["ties"] == "count_against"
    assert p1["draws"] == "none"
    ks = p1["shifts_back_years"]
    listed = ", ".join(str(k) for k in ks[:-1]) + f" and {ks[-1]}"
    assert f"B = {p1['B']}" in text
    assert f"k = {listed} years" in text
    for key in ("h0", "h1", "p", "stop_rule", "shift_rule", "calendar"):
        assert contains(text, p1[key]), f"P1 {key} not in PROTOCOL.md"


def test_a1_exact(protocol, text):
    a1 = protocol["tests"]["A1"]
    assert a1["permutation"] == {"kind": "paired_swap", "patterns": 1024, "exhaustive": True}
    assert a1["permutation"]["patterns"] == 2 ** len_site_seasons(protocol)
    assert a1["alpha"] == 0.10
    assert a1["ties"] == "count_against"
    assert a1["draws"] == "none"
    for key in ("restart_at", "statistic", "h0", "h1", "p", "pass"):
        assert contains(text, a1[key]), f"A1 {key} not in PROTOCOL.md"


def len_site_seasons(protocol) -> int:
    return sum(len(s["seasons"]) for s in protocol["trial_site_seasons"].values())


def test_ten_site_seasons(protocol):
    assert len_site_seasons(protocol) == 10
    assert protocol["min_site_seasons_for_label"] == 10


def test_220_chains(protocol, text):
    count = protocol["chains"]["count"]
    n, arms, shifts = len_site_seasons(protocol), 2, protocol["tests"]["P1"]["B"]
    assert count["base"] == n * arms
    assert count["a1"] == n * arms
    assert count["p1"] == shifts * n * arms
    assert count["total"] == count["base"] + count["a1"] + count["p1"] == 220
    assert protocol["tests"]["P1"]["chains"] == count["p1"]
    assert protocol["tests"]["A1"]["chains"] == count["a1"]
    assert "exactly 220 chains" in text


def test_seed(protocol, text):
    seed = protocol["seed"]
    assert isinstance(seed, int) and not isinstance(seed, bool)
    assert f"Seed: {seed}" in text


def test_ties_count_against(text):
    assert text.count("Ties count against.") >= 2


def test_t0_t1_thresholds(protocol, text):
    t0, t1 = protocol["tests"]["T0"], protocol["tests"]["T1"]
    assert t0["s_total_min"] == t1["s_total_min"] == 21
    assert t0["within_site_s_sum_gt"] == 0
    assert t1["rmse_lt"] == "in_sample_constant_rmse"
    assert "S >= 21" in text


def test_labels(protocol, text):
    for row in protocol["label"]["order"]:
        assert contains(text, row["label"])


def test_h1_tasks(protocol, text):
    h1 = protocol["h1"]
    assert (h1["confirmed"], h1["slots"], h1["minutes"]) == (4, 3, 3)
    assert len(h1["tasks"]) == 5
    assert [t["id"] for t in h1["tasks"]] == [1, 2, 3, 4, 5]
    for task in h1["tasks"]:
        assert task["pass"], f"task {task['id']} has no criterion"
        assert task["task"] in text
        assert task["pass"] in text
    assert h1["language"] in text
    assert h1["profile"] in text
    assert h1["slot_rule"] in text
    assert h1["reserve_rule"] in text


def test_decision_rule(protocol, text):
    d = protocol["decision"]
    assert (d["min_n"], d["try_ratio_min"], d["keep_ratio_max"], d["gain_step_sc_ha"]) == (5, 0.7, 0.3, 1)
    assert "n_i < 5" in text and "0.7" in text and "0.3" in text


def test_soil_domain(protocol, text):
    s = protocol["soil_domain"]
    assert s["not_enough_evidence_below_clay_g_kg"] == 350
    assert s["use_with_care_clay_g_kg"] == [350, 600]
    assert s["inside_above_clay_g_kg"] == 600
    assert s["decides"] == "user_declared_only"
    assert "SoilGrids never decides" in text


def test_sowing_rule(protocol):
    s = protocol["sowing"]["soybean_field"]
    assert (s["rain_mm"], s["rain_days"], s["s3_not_before_wheat_harvest_plus_days"]) == (20, 3, 20)
    assert s["common_to_arms"] is True


# ---- one owner per number (M0.4, "Limiar tem um dono so") --------------------

def test_site_coordinates_match_trial_sites(config):
    sites = {s["id"]: s for s in config["trial_sites"]}
    coords = config["protocol"]["site_coordinates"]
    assert set(coords) == set(sites) == set(config["protocol"]["trial_site_seasons"])
    for site_id, c in coords.items():
        assert (c["lat"], c["lon"]) == (sites[site_id]["lat"], sites[site_id]["lon"])


def test_decision_mjs_matches_protocol(protocol):
    js = DECISION_JS.read_text(encoding="utf-8")
    min_n = int(re.search(r"MIN_SITE_SEASONS = (\d+);", js).group(1))
    try_num, try_den = map(int, re.search(r"TRY_RATIO = Object\.freeze\(\{ num: (\d+), den: (\d+) \}\)", js).groups())
    keep_num, keep_den = map(int, re.search(r"KEEP_RATIO = Object\.freeze\(\{ num: (\d+), den: (\d+) \}\)", js).groups())
    d = protocol["decision"]
    assert min_n == d["min_n"]
    assert try_num / try_den == d["try_ratio_min"]
    assert keep_num / keep_den == d["keep_ratio_max"]


def _module_literal(path: Path, name: str):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        targets = node.targets if isinstance(node, ast.Assign) else [getattr(node, "target", None)]
        if any(isinstance(t, ast.Name) and t.id == name for t in targets):
            value = node.value
            if isinstance(value, ast.Call):        # frozenset({...})
                value = value.args[0]
            return ast.literal_eval(value)
    raise KeyError(name)


def test_crops_overrides_match_protocol(protocol):
    allowed = protocol["engine"]["allowed_overrides"]
    fixed = allowed["fixed"]
    keys = set(allowed["cycle"]) | {k for crop in fixed.values() for k in crop}
    assert keys == set(_module_literal(CROPS_PY, "ALLOWED"))
    assert fixed == _module_literal(CROPS_PY, "BASE_OVERRIDES")


# ---- M0.6 and the tag gate --------------------------------------------------

OPENING_CHECKS = ("imerg_prectot_last_date", "imerg_v08_in_cmr", "merra2_geosit_boundary", "soilgrids_rest")


def test_opening_checks(protocol):
    checks = protocol["opening_checks"]
    assert tuple(checks) == OPENING_CHECKS
    for item in checks.values():
        assert "result" in item
        assert "checked_at" not in item         # SA-D104: no date of the work in the protocol
    if protocol["status"] == "draft":
        pytest.skip("draft: the opening checks are filled in the first hour of 14/11")
    for name, item in checks.items():
        assert item["result"], f"{name} has no result"


def test_status(protocol):
    assert protocol["status"] in ("draft", "tagged")
    assert protocol["id"] == "protocol-v1"


def test_no_open_decision_when_tagged(protocol, raw_yaml, text):
    open_points = text.count("[DECIDE") + protocol_block_bytes(raw_yaml).count(b"[DECIDE")
    if protocol["status"] == "draft":
        pytest.skip(f"draft: {open_points} [DECIDE] markers still open")
    assert open_points == 0
    assert protocol["field"]["ndvi_green"] is not None
    assert protocol["field"]["min_clean_frac"] is not None

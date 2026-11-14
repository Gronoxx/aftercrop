# Written with an SI tool (Claude, Anthropic) for NASA Space Apps 2026. See AI_USE.md.
"""precompute.py: the JSON the site reads, in the schema of plan 8.6, with a ledger id for every number.

Built only from synthetic inputs labelled "example, fictitious" (tests/fixtures/). The example
outputs live in tests/fixtures/decision_example/, never in site/data/. A3 states come from
run_states.mjs, which imports site/js/decision.mjs: the Python side never reimplements the rule.
"""

import copy
import json
import shutil
from pathlib import Path

import pytest

from pipeline.adapters import base
from pipeline.decision import erosivity as ero
from pipeline.decision import precompute as pc
from pipeline.decision import strategies as st

from test_decision_field_inputs import m2  # noqa: F401  (fixture)
from test_rehearsal import strip  # noqa: F401  (fixture)

FIX = Path(__file__).parent / "fixtures"
INPUTS_PATH = FIX / "decision_precompute_inputs.json"
EXAMPLE_DIR = FIX / "decision_example"
CONFIG = base.load_config()
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node not found")


def load():
    return pc.load_inputs(INPUTS_PATH)


def build(inputs=None, **kw):
    i, rows, daily = load()
    return pc.build(inputs or i, config=CONFIG, zarc_rows=rows, daily=daily, **kw)


def no_node(_payload):
    raise FileNotFoundError("node not found (test)")


@pytest.fixture(scope="module")
def result():
    if NODE is None:
        pytest.skip("node not found")
    return build()


@pytest.fixture(scope="module")
def offline_result():
    return build(states_runner=no_node)


# Schema of 8.6 ----------------------------------------------------------------------

REGION_KEYS = {"schema_version", "region_id", "mode", "trial_sites", "pack", "strategies", "rules", "constraints",
               "alerts", "units", "price_scenarios", "noise_floor", "choice_seasons", "nasa_switch"}
# No field_order: decision.mjs fieldOrder computes it in the browser (plan 8.7).
FIELD_KEYS = {"schema_version", "field_id", "criterion", "fictional_persona_note", "polygon_px", "area_ha",
              "scale_note", "soil", "relief", "ndvi_series", "ribbon", "green_end", "window_reading", "window_basis",
              "risk", "ribbon_counts", "calendar", "erosivity", "next_soy"}


def test_output_files(offline_result):
    assert set(offline_result["docs"]) == {"region.json", "trials_steps.json", "field_a.json", "field_b.json",
                                           "ablation.json"}
    for name, doc in offline_result["docs"].items():
        assert doc["schema_version"] == pc.SCHEMA_VERSION, name


def test_region_carries_the_nasa_switch_rule_of_the_protocol(offline_result):
    """SA-D66: the rule reaches the browser through region.json, as a Num with a ledger record."""
    r = offline_result["docs"]["region.json"]["nasa_switch"]
    rule = CONFIG["protocol"]["nasa_switch"]
    assert r["main_page_if_states_changed_min"] == {
        "v": rule["main_page_if_states_changed_min"], "l": "region.nasa_switch.main_page_if_states_changed_min"}
    assert r["otherwise"] == rule["otherwise"] == "how_we_know"
    rec = offline_result["ledger"]["records"]["region.nasa_switch.main_page_if_states_changed_min"]
    assert rec["unit"] == "states" and rec["validation_state"] == "not_applicable"


@pytest.mark.parametrize("bad", [None, {"main_page_if_states_changed_min": -1, "otherwise": "how_we_know"},
                                 {"main_page_if_states_changed_min": 1.5, "otherwise": "how_we_know"},
                                 {"main_page_if_states_changed_min": 1, "otherwise": "main_page"}])
def test_a_broken_nasa_switch_rule_is_refused(bad):
    cfg = copy.deepcopy(CONFIG)
    if bad is None:
        del cfg["protocol"]["nasa_switch"]
    else:
        cfg["protocol"]["nasa_switch"] = bad
    with pytest.raises(pc.PrecomputeError, match="nasa_switch"):
        pc.nasa_switch_rule(cfg["protocol"], pc.Ledger())


def test_region_schema(offline_result):
    r = offline_result["docs"]["region.json"]
    assert set(r) == REGION_KEYS
    assert r["region_id"] == CONFIG["region"] == "norte_pr"
    assert r["mode"] == "decision"
    assert r["trial_sites"] == ["Londrina", "Floresta", "Cafelândia"]
    assert set(r["pack"]) == {"decision", "strategies", "calendar", "trials", "reviewer"}
    assert r["pack"]["reviewer"] is False
    assert [s["id"] for s in r["strategies"]] == ["S1", "S2", "S3"]
    assert set(r["rules"]) == {"hard", "soft"}
    for c in r["constraints"]:
        assert set(c) == {"id", "label", "removes", "why"}
    for a in r["alerts"]:
        assert set(a) == {"id", "text_en", "text_pt", "trigger", "source_id", "audited"}
    assert set(r["units"]) == {"bag_kg", "alqueire_ha", "screen_unit"}
    assert r["units"]["bag_kg"]["v"] == 60 and r["units"]["alqueire_ha"]["v"] == 2.42
    for s in r["price_scenarios"]:
        assert set(s) == {"id", "label", "values", "fictitious"} and s["fictitious"] is True
    sc = st.load_copy("en")["page"]["inputs"]["scenarios"]
    assert [s["label"] for s in r["price_scenarios"]] == [
        sc["low_note"].format(k="15%"), sc["base"], sc["high_note"].format(k="15%")]
    # Texts come from the copy file, through the ids of strategies.py.
    assert r["constraints"] == st.constraints("en") and r["alerts"] == st.alerts()
    assert r["rules"]["soft"] == st.soft_rules("en")
    # Inputs of the field order the browser computes (8.7): N1 and the choice seasons (D9).
    assert r["noise_floor"] == {"v": None, "l": "region.noise_floor"}     # the example has no noise_floor.json
    assert r["choice_seasons"] == CONFIG["protocol"]["field"]["choice_seasons"]
    rec = offline_result["ledger"]["records"]["region.noise_floor"]
    assert (rec["unit"], rec["evidence_class"], rec["validation_state"]) == ("days", "estimated_from_satellite",
                                                                          "not_completed")


def test_price_scenarios_move_only_the_corn_price(offline_result):
    sc = {s["id"]: {k: v["v"] for k, v in s["values"].items()} for s in offline_result["docs"]["region.json"]["price_scenarios"]}
    assert list(sc) == ["example_low", "example_mid", "example_high"]
    base_numbers = load()[0]["region"]["example_numbers"]
    assert sc["example_mid"] == base_numbers
    assert sc["example_low"]["p_corn"] == 42.5 and sc["example_high"]["p_corn"] == 57.5   # k = 15% (plan 14.3)
    for sid in ("example_low", "example_high"):
        assert {k: v for k, v in sc[sid].items() if k != "p_corn"} == {k: v for k, v in base_numbers.items() if k != "p_corn"}


def test_field_schema(offline_result):
    for fid in ("a", "b"):
        d = offline_result["docs"][f"field_{fid}.json"]
        assert set(d) == FIELD_KEYS
        assert d["field_id"] == fid
        assert set(d["window_reading"]) == {"state", "k", "n", "zarc_window_end", "basis", "soil_class", "ledger"}
        assert d["window_reading"]["state"] in ("at_the_edge", "outside")
        assert d["window_reading"]["basis"] == "declared_class"       # the example fields declare AD4 and AD3
        assert set(d["window_basis"]) == {"municipio", "min_seasons", "classes", "seasons", "S1", "S2", "ledger",
                                          "risk_at_exit", "ended_before"}
        # SA-D72: the band, the risk at exit and "ended before", each with its own record.
        wb = d["window_basis"]
        assert wb["risk_at_exit"]["ledger"] == f"field_{fid}.risk_at_exit"
        assert wb["ended_before"]["ledger"] == f"field_{fid}.ended_before"
        for sid in ("S1", "S2"):
            band = wb[sid]["risk_band"]
            assert band["ledger"] == f"field_{fid}.zarc_risk_band"
            assert len(band["min"]) == len(band["max"]) == len(band["some_outside"]) == 36
            for cls, b in band["by_class"].items():
                assert len(b["risk"]) == 36 and all(v is None or v > 0 for v in b["risk"]), cls
        assert all(g["high_dekad"] is None or 1 <= g["high_dekad"] <= 36 for g in wb["seasons"])
        for sid in ("S1", "S2", "S3"):
            zc = d["risk"]["zarc_class"][sid]
            assert set(zc["any_class"]) == {"risk_min", "risk_max"} and isinstance(zc["by_class"], dict)
        assert isinstance(d["soil"]["estimated_class"], str)          # "Soil used: {class}" (7.2)
        for g in d["green_end"]:
            assert set(g) == {"season", "low", "high", "gap_days", "status"}
        assert set(d["risk"]) == {"zarc_class", "climate", "ledger"}
        assert set(d["risk"]["zarc_class"]) == {"S1", "S2", "S3"}
        cl = d["risk"]["climate"]
        assert set(cl) == {"S1", "S2", "S3", "ledger"} and cl["ledger"] == f"field_{fid}.climate"
        for sid, crop in (("S1", "corn"), ("S2", "corn_grass"), ("S3", "wheat")):
            c = cl[sid]
            assert set(c) == {"crop", "start", "end", "n", "left_out", "frost", "rain"} and c["crop"] == crop
            assert 0 <= c["frost"]["k"] <= c["n"] and c["frost"]["below_c"] == 2
            assert c["n"] + len(c["left_out"]) == 42                    # whole periods of 1984 to 2025
            assert c["rain"]["p10_mm"] <= c["rain"]["median_mm"] and len(c["rain"]["driest"]) == 3
        # The example corn period (02-11 to 07-31) holds the missing day of 1990-04-05, wheat (04-15) does not.
        assert cl["S1"]["left_out"] == ["1989/90"] and cl["S3"]["left_out"] == []
        ng = d["ribbon_counts"]["no_green"]
        assert ng["of"] == 36 and ng["seasons"] == len(d["ribbon"]["seasons"])
        assert ng["ledger"] == f"field_{fid}.ribbon_counts.no_green"
        assert set(d["calendar"]) == {"S1", "S2", "S3"}
        for col in d["calendar"].values():
            for t in col:
                assert set(t) == {"crop", "start", "end", "source_id", "state"}
        assert set(d["erosivity"]) == {"S1", "S2", "S3", "ledger"}
        assert d["erosivity"]["S1"] == "reference"
        assert {d["erosivity"]["S2"], d["erosivity"]["S3"]} <= set(ero.CLASSES)
        assert set(d["next_soy"]) == {"state", "on_main_page", "water_note_on_how_we_know", "ledger"}
        for s in d["ribbon"]["seasons"]:
            assert len(s["cells"]) == 36


def test_trials_steps_schema(offline_result):
    t = offline_result["docs"]["trials_steps.json"]
    assert set(t) == {"schema_version", "rights_note", "reference", "options", "money_refs"}
    assert set(t["options"]) == {"S2", "S3"}
    for e in t["options"].values():
        assert set(e) == {"n", "n_sites", "condition", "transfer_warning", "steps", "bad_year", "summary", "ledger"}
        assert set(e["bad_year"]) == {"text", "ledger"}
    for m in t["money_refs"]:
        assert set(m) == {"id", "text", "price_basis", "ledger"}


def test_ablation_schema(result):
    a = result["docs"]["ablation.json"]
    assert set(a) == {"schema_version", "scenarios", "sources"}
    assert a["scenarios"] == ["example_low", "example_mid", "example_high"]
    for s in a["sources"]:
        assert set(s) == {"id", "swap", "status", "states_changed", "states_total", "verdict"}
        assert s["verdict"] in pc.ABLATION_VERDICTS and s["status"] in pc.ABLATION_STATUSES


# Every number has a ledger id ---------------------------------------------------------

def test_no_loose_number(offline_result):
    for name, doc in offline_result["docs"].items():
        assert pc.loose_numbers(doc) == [], name


def test_loose_number_is_caught():
    assert pc.loose_numbers({"a": 1}) == ["a"]
    assert pc.loose_numbers({"a": {"v": 1, "l": "x"}}) == []
    assert pc.loose_numbers({"ledger": "x", "b": [1, 2]}) == []
    assert pc.loose_numbers({"polygon_px": [[1, 2]], "c": [{"d": 3.5}]}) == ["c[0].d"]


def test_every_ledger_id_has_a_record_or_an_owner(offline_result):
    recs = offline_result["ledger"]["records"]
    ext = set(offline_result["ledger"]["external"])
    refs = set().union(*(pc.referenced_ids(d) for d in offline_result["docs"].values()))
    assert refs == set(recs) | ext
    assert not (set(recs) & ext)
    # External ids are those that came in with the M2 blocks.
    assert all(i.split(".")[1] in {"area_ha", "scale_note", "soil", "relief", "ndvi_series", "ribbon", "green_end"}
               for i in ext)
    for rid, rec in recs.items():
        assert set(rec) == {"id", *pc.RECORD_FIELDS}, rid
        assert rec["evidence_class"] in pc.EVIDENCE_CLASSES
        assert rec["validation_state"] in pc.VALIDATION_STATES


def test_ledger_refuses_incomplete_records():
    led = pc.Ledger()
    with pytest.raises(pc.PrecomputeError):
        led.add("x", value=1)
    full = dict(value=None, unit="u", evidence_class="unknown", source_id=None, transform="t", method_id="m",
                data_version="d", uncertainty=None, validation_state="not_applicable", limits="l")
    led.add("x", **full)
    with pytest.raises(pc.PrecomputeError):
        led.add("x", **full)
    with pytest.raises(pc.PrecomputeError):
        led.add("y", **{**full, "evidence_class": "guess"})


def test_ledger_id_format():
    assert pc.ledger_id("field_a", "green_end", "2025/26") == "field_a.green_end.2025_26"


# Inputs ------------------------------------------------------------------------------

def test_the_label_picks_the_run_and_must_agree():
    i, rows, daily = load()
    for label in (None, "fictitious", "Real"):
        with pytest.raises(pc.PrecomputeError, match="labelled"):
            pc.build({**i, "label": label}, config=CONFIG, zarc_rows=rows, daily=daily, states_runner=no_node)
    with pytest.raises(pc.PrecomputeError, match="--real"):
        pc.build(i, config=CONFIG, zarc_rows=rows, daily=daily, states_runner=no_node, mode=pc.REAL)
    assert pc.build(i, config=CONFIG, zarc_rows=rows, daily=daily, states_runner=no_node,
                    mode=pc.EXAMPLE)["label"] == pc.EXAMPLE


def test_fictitious_inputs_never_pass_as_real():
    """A real label on the synthetic inputs is refused for every trace of the example."""
    i, rows, daily = load()
    with pytest.raises(pc.PrecomputeError) as exc:
        pc.build({**i, "label": pc.REAL}, config=CONFIG, zarc_rows=rows, daily=daily, states_runner=no_node)
    msg = str(exc.value)
    assert "no provenance" in msg and "example, fictitious" in msg
    clean = copy.deepcopy(i)
    clean["label"] = pc.REAL
    for f in clean["fields"]:
        f["m2"]["provenance"] = {"label": pc.REAL, "assembler": pc.ASSEMBLER,
                                 "files": [{"path": "tests/fixtures/no_such_file.json", "sha256": "0"}]}
    with pytest.raises(pc.PrecomputeError, match="missing"):
        pc.build(clean, config=CONFIG, zarc_rows=rows, daily=daily, states_runner=no_node)


def test_real_outputs_never_carry_the_example_label_in_the_inputs():
    inputs = {"label": pc.REAL, "fields": [], "zarc_rows": [{"x": 1}],
              "trials": {"options": {"S3": {"source_ids": ["example_trials"], "note": "Example, fictitious."}}}}
    with pytest.raises(pc.PrecomputeError) as exc:
        pc.check_inputs_label(inputs, pc.REAL)
    msg = str(exc.value)
    for part in ("trials.options.S3.note", "example source", "zarc_rows"):
        assert part in msg, part
    pc.check_inputs_label({"label": pc.EXAMPLE, "fields": [{"m2": {}}]}, pc.EXAMPLE)
    with pytest.raises(pc.PrecomputeError, match="cannot be labelled"):
        pc.check_inputs_label({"fields": [{"m2": {"provenance": {"label": pc.REAL}}}]}, pc.EXAMPLE)


def test_example_weather_must_say_so(tmp_path):
    i = json.loads(INPUTS_PATH.read_text(encoding="utf-8"))
    body = (FIX / i["daily_weather_csv"]).read_text(encoding="utf-8").split("\n", 1)[1]
    (tmp_path / "w.csv").write_text(body, encoding="utf-8")                  # no label line
    (tmp_path / "in.json").write_text(json.dumps({**i, "daily_weather_csv": "w.csv"}), encoding="utf-8")
    with pytest.raises(pc.PrecomputeError, match="labelled"):
        pc.load_inputs(tmp_path / "in.json")
    (tmp_path / "in.json").write_text(json.dumps({**i, "label": pc.REAL,
                                                  "daily_weather_csv": str(FIX / i["daily_weather_csv"])}),
                                      encoding="utf-8")
    with pytest.raises(pc.PrecomputeError, match="never reads weather"):
        pc.load_inputs(tmp_path / "in.json")


def test_mode_follows_the_regional_pack():
    i = copy.deepcopy(load()[0])
    i["region"]["pack"]["trials"] = False
    out = build(i, states_runner=no_node)
    assert out["docs"]["region.json"]["mode"] == "observation"


def test_trial_sites_must_match_the_config():
    i = copy.deepcopy(load()[0])
    i["region"]["trial_sites"] = ["Londrina", "Floresta"]
    with pytest.raises(pc.PrecomputeError):
        build(i, states_runner=no_node)


def test_m2_blocks_are_checked():
    i = copy.deepcopy(load()[0])
    i["fields"][0]["m2"]["ribbon"]["seasons"][0]["cells"] = ["green"] * 35
    with pytest.raises(pc.PrecomputeError):
        build(i, states_runner=no_node)
    i = copy.deepcopy(load()[0])
    i["fields"][0]["m2"]["area_ha"] = 12.5
    with pytest.raises(pc.PrecomputeError):
        build(i, states_runner=no_node)


def test_steps_are_checked():
    with pytest.raises(pc.PrecomputeError):
        pc.check_steps([{"g_sc_ha": 1, "count": 3}, {"g_sc_ha": 2, "count": 4}], 5, "s")
    with pytest.raises(pc.PrecomputeError):
        pc.check_steps([{"g_sc_ha": 1, "count": 6}], 5, "s")
    with pytest.raises(pc.PrecomputeError):
        pc.check_steps([{"g_sc_ha": 1.5, "count": 3}], 5, "s")
    assert pc.check_steps([], 1, "s") == []


def test_steps_from_synthetic_gains():
    # Synthetic gains in kg/ha (example, fictitious): 0, 59, 60, 61, 130, -20.
    steps = pc.steps_from_gains([0, 59, 60, 61, 130, -20])
    assert steps == [{"g_sc_ha": 1, "count": 3}, {"g_sc_ha": 2, "count": 1}, {"g_sc_ha": 3, "count": 0}]
    assert pc.steps_from_gains([-5]) == [{"g_sc_ha": 1, "count": 0}]
    assert pc.check_steps(steps, 6, "s") == steps


# A3 through run_states.mjs ------------------------------------------------------------

def test_infrastructure_failure_is_not_completed(offline_result):
    hls = offline_result["docs"]["ablation.json"]["sources"][0]
    assert (hls["id"], hls["status"], hls["verdict"], hls["states_changed"]["v"]) == (
        "hls_vi_v2", "not_completed", "not_run", None)
    assert offline_result["errors"] and offline_result["states"] is None


def fake_states(changed_keys=()):
    """A run_states.mjs result where only ``changed_keys`` (field, scenario, option) move."""
    states = []
    for f in ("a", "b"):
        for sc in pc.SCENARIO_IDS:
            for opt in ("S2", "S3"):
                moved = (f, sc, opt) in changed_keys
                states.append({"field_id": f, "scenario": sc, "option": opt,
                               "hls_on": {"state": "not_enough" if moved else "tied", "code": "x"},
                               "hls_off": {"state": "keep" if moved else "tied", "code": "x"}, "changed": moved})
    return {"states": states, "states_changed": len(changed_keys), "states_total": len(states)}


def test_zero_changes_is_context_only():
    out = build(states_runner=lambda p: fake_states())
    hls = out["docs"]["ablation.json"]["sources"][0]
    assert (hls["status"], hls["verdict"], hls["states_total"]["v"]) == ("reported", "context_only", 12)


def test_the_count_is_the_bench_count():
    out = build(states_runner=lambda p: fake_states({("a", "example_low", "S3"), ("b", "example_high", "S2")}))
    hls = out["docs"]["ablation.json"]["sources"][0]
    assert (hls["verdict"], hls["states_changed"]["v"]) == ("changes_answers", 2)
    bad = fake_states({("a", "example_low", "S3")})
    bad["states_changed"] = 0                       # a disagreeing summary is refused
    with pytest.raises(pc.PrecomputeError):
        build(states_runner=lambda p: bad)


def test_nasadem_is_context_only_by_construction(offline_result):
    n = offline_result["docs"]["ablation.json"]["sources"][1]
    assert (n["id"], n["verdict"], n["status"]) == ("nasadem", "context_only", "not_run")


def test_states_input_holds_no_engine_output(offline_result):
    p = offline_result["states_input"]
    assert set(p) == {"label", "fields", "scenarios", "trials", "hardRules", "constraints", "context"}
    assert p["label"] == "example, fictitious"
    for f in p["fields"]:
        assert set(f) == {"field_id", "windowReading", "zarcWindow"}
    for s in p["scenarios"]:
        assert set(s["numbers"]) == set(pc.PRICE_FIELDS)
    text = json.dumps(p)
    for word in ("engine", "simulated", "next_soy", "yield_sim"):
        assert word not in text


@needs_node
def test_run_states_counts_the_switch(result):
    s = result["states"]
    assert s["decision_module"] == "site/js/decision.mjs"
    assert s["states_total"] == 2 * 2 * 3                     # fields x options x scenarios
    changed = [(x["field_id"], x["scenario"], x["option"], x["hls_on"]["state"], x["hls_off"]["state"])
               for x in s["states"] if x["changed"]]
    # Only rule 6 can move a state: field b reads "outside", the example marks the Zarc constraint
    # and declares no date, so the keep of the high corn price asks for the sowing date.
    assert changed == [("b", "example_high", "S3", "not_enough", "keep")]
    hls = result["docs"]["ablation.json"]["sources"][0]
    assert (hls["status"], hls["verdict"], hls["states_changed"]["v"], hls["states_total"]["v"]) == (
        "reported", "changes_answers", 1, 12)


@needs_node
def test_without_the_constraint_the_switch_changes_nothing():
    i = copy.deepcopy(load()[0])
    i["example_context"]["zarcConstraint"] = False
    out = build(i)
    assert out["states"]["states_changed"] == 0
    assert out["docs"]["ablation.json"]["sources"][0]["verdict"] == "context_only"


@needs_node
def test_a_declared_date_is_in_charge():
    i = copy.deepcopy(load()[0])
    i["example_context"]["sowingDate"] = "2027-02-05"
    out = build(i)
    assert out["states"]["states_changed"] == 0


@needs_node
def test_example_outputs_match_the_fixtures(result):
    """tests/fixtures/decision_example/ is what precompute writes from the fictitious inputs.
    Regenerate: python -m pipeline.decision.precompute --inputs tests/fixtures/decision_precompute_inputs.json
    --out tests/fixtures/decision_example --build-out tests/fixtures/decision_example
    then copy the docs (and ledger_m5.json as ledger.json) to tests/fixtures/site_example/."""
    files = {**result["docs"], **pc.sidecars(result)}
    assert set(files) - set(result["docs"]) == set(pc.SIDECARS)
    on_disk = {p.name for p in EXAMPLE_DIR.glob("*.json")}
    assert on_disk == set(files)
    for name, doc in files.items():
        assert json.loads((EXAMPLE_DIR / name).read_text(encoding="utf-8")) == json.loads(json.dumps(doc)), name


def test_write_puts_docs_and_sidecars_in_their_folders(tmp_path):
    out = build(states_runner=no_node)
    written = pc.write(out, tmp_path / "site", tmp_path / "build")
    assert {p.name for p in written if p.parent.name == "site"} == {
        "region.json", "trials_steps.json", "field_a.json", "field_b.json", "ablation.json"}
    assert {p.name for p in written if p.parent.name == "build"} == {"ledger_m5.json", "states_input_m5.json"}
    assert json.loads((tmp_path / "build" / "ledger_m5.json").read_text(encoding="utf-8"))["label"] == pc.EXAMPLE


def test_write_refuses_the_wrong_folders(tmp_path, monkeypatch):
    out = build(states_runner=no_node)
    # site/data holds the real run once it has been made: the check is that nothing there changes.
    before = {f.name: f.read_bytes() for f in pc.SITE_DATA.glob("*.json")}
    with pytest.raises(pc.PrecomputeError, match="sidecars"):
        pc.write(out, tmp_path / "site", pc.SITE_DATA)
    with pytest.raises(pc.PrecomputeError, match="never goes to site/data"):
        pc.write(out, pc.SITE_DATA, tmp_path / "build")              # nothing is written before the check
    assert {f.name: f.read_bytes() for f in pc.SITE_DATA.glob("*.json")} == before
    real = {**out, "label": pc.REAL}
    with pytest.raises(pc.PrecomputeError, match="tests/"):
        pc.write(real, tmp_path / "site", pc.TESTS_DIR / "fixtures" / "x")
    with pytest.raises(pc.PrecomputeError, match="result label"):
        pc.write({**out, "label": None}, tmp_path / "site", tmp_path / "build")


def test_l3_lines_are_nums_with_ledger_ids(offline_result):
    t = offline_result["docs"]["trials_steps.json"]
    recs = offline_result["ledger"]["records"]
    assert t["reference"] == {"season": "Example season", "source": "Example document", "ledger": "trials.reference"}
    s3 = t["options"]["S3"]["summary"]
    assert s3["delta"] == {"v": 150, "l": "trials.S3.mean_delta"}                 # fictitious
    assert recs["trials.S3.mean_delta"]["evidence_class"] == "derived_from_trials"
    assert s3["bags"]["v"] == 2.5 and recs[s3["bags"]["l"]]["unit"] == "sc/ha"       # 150 / 60
    assert {k for k, v in s3.items() if pc.is_num(v)} == {"delta", "bags", "pos", "neg", "a", "b"}
    s2 = t["options"]["S2"]["summary"]
    assert s2["season"] == "Example season" and s2["ledger"] == "trials.S2.summary"
    assert {k for k, v in s2.items() if pc.is_num(v)} == {"gain", "roots_pct", "straw_pct"}
    # The copy placeholders of page.rows.L3.* are exactly the keys emitted (plus n, from the option).
    copy_l3 = st.load_copy("en")["page"]["rows"]["L3"]
    import re
    holes = {k: set(re.findall(r"\{(\w+)\}", copy_l3[k])) for k in ("S1", "S2", "S2_roots", "S3", "S3_compilation")}
    assert holes["S1"] == set(pc.REFERENCE_TEXTS)
    assert holes["S2"] | holes["S2_roots"] == set(pc.SUMMARY_NUMBERS["S2"]) | set(pc.SUMMARY_TEXTS["S2"])
    assert holes["S3"] | holes["S3_compilation"] == set(pc.SUMMARY_NUMBERS["S3"]) | {"bags", "n"}


def test_l3_summaries_are_checked():
    i = copy.deepcopy(load()[0])
    i["trials"]["options"]["S3"]["summary"]["mystery"] = 1
    with pytest.raises(pc.PrecomputeError, match="unknown keys"):
        build(i, states_runner=no_node)
    i = copy.deepcopy(load()[0])
    i["trials"]["options"]["S3"]["summary"].update(pos=8, neg=5)                  # 13 > n = 10
    with pytest.raises(pc.PrecomputeError, match="exceed"):
        build(i, states_runner=no_node)
    i = copy.deepcopy(load()[0])
    del i["trials"]["options"]["S2"]["summary"]
    del i["trials"]["reference"]
    out = build(i, states_runner=no_node)["docs"]["trials_steps.json"]
    assert out["options"]["S2"]["summary"] is None and out["reference"] is None


def test_a_field_without_a_zarc_soil_class_says_so():
    """No AD class: every Zarc state is soil_class_unknown, never a silent not_found."""
    i = copy.deepcopy(load()[0])
    i["fields"][0]["m2"]["soil"]["zarc_ad_class"] = None
    out = build(i, states_runner=no_node)
    d = out["docs"]["field_a.json"]
    assert {v["state"] for v in d["risk"]["zarc_class"].values()} == {"soil_class_unknown"}
    gaps = [t for col in d["calendar"].values() for t in col if t["start"] is None]
    assert gaps and {t["state"] for t in gaps} == {"soil_class_unknown"}
    # The window warning uses the envelope of every class and cycle group (SA-D64).
    assert d["window_reading"]["basis"] == "for_any_soil_class" and d["window_reading"]["soil_class"] == "unknown"
    assert d["window_reading"]["zarc_window_end"] == d["window_basis"]["S1"]["any"]["end"]
    assert out["states_input"]["fields"][0]["windowReading"]["basis"] == "for_any_soil_class"
    assert out["states_input"]["fields"][0]["zarcWindow"]["corn"] is None      # decision.mjs flags it
    assert {v["state"] for v in out["docs"]["field_b.json"]["risk"]["zarc_class"].values()} <= {
        "zarc_window", "zarc_previous_table"}
    i["fields"][0]["m2"]["soil"]["estimated_class"] = {"code": "clayey"}
    with pytest.raises(pc.PrecomputeError, match="text or null"):
        build(i, states_runner=no_node)


# The product field selection stays in the product run (SA-D81) ---------------------------------

@pytest.fixture
def product_selection(tmp_path, monkeypatch):
    """A product field selection (protocol-v1.1) that fell back to an older season."""
    sel = tmp_path / "fields_selection.json"
    sel.write_text(json.dumps({"choice_seasons": ["2024/25"]}), encoding="utf-8")
    monkeypatch.setattr(pc, "selection_file", lambda: sel)
    protocol = list(CONFIG["protocol"]["field"]["choice_seasons"])
    assert "2024/25" not in protocol
    return protocol


def test_choice_seasons_reads_the_selection_only_in_a_product_run(product_selection):
    assert pc.choice_seasons(CONFIG["protocol"]) == product_selection
    assert pc.choice_seasons(CONFIG["protocol"], product_run=True) == product_selection + ["2024/25"]


def test_the_example_run_never_reads_the_product_selection(product_selection):
    region = build(states_runner=no_node)["docs"]["region.json"]
    assert region["choice_seasons"] == product_selection


def test_the_rehearsal_never_reads_the_product_selection(product_selection, strip):
    from test_rehearsal import strip_inputs
    from test_decision_field_inputs import zarc_rows
    inputs, _, daily = strip_inputs(strip)
    res = pc.build(inputs, config=CONFIG, zarc_rows=zarc_rows(), daily=daily, mode=pc.REAL, rehearsal=True,
                   states_runner=no_node)
    assert res["docs"]["region.json"]["choice_seasons"] == product_selection


def test_the_product_run_keeps_the_seasons_of_the_selection(product_selection, m2):
    from test_decision_field_inputs import real_inputs_file, zarc_rows
    inputs, _, daily = pc.load_inputs(real_inputs_file(m2))
    res = pc.build(inputs, config=CONFIG, zarc_rows=zarc_rows(), daily=daily, mode=pc.REAL, states_runner=no_node)
    assert res["docs"]["region.json"]["choice_seasons"] == product_selection + ["2024/25"]

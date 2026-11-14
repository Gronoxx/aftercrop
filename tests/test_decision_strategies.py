# Written with an SI tool (Claude, Anthropic) for NASA Space Apps 2026. See AI_USE.md.
"""Fixed menu, rules, constraints and alerts (plan 13.2 to 13.6, 7.4).

Texts are owned by config/copy_en.yaml and config/copy_pt.yaml: strategies.py holds ids,
rules and data only, and resolves texts by copy key.
"""

import ast
import inspect

import pytest

from pipeline.adapters import base
from pipeline.decision import strategies as st

PROTOCOL = base.load_config()["protocol"]


def test_menu_invariants():
    st.check_menu()


def test_current_rotation_first_and_options_against_it():
    assert st.STRATEGY_IDS == ("S1", "S2", "S3")
    assert st.OPTION_IDS == ("S2", "S3")
    assert [s.name for s in st.STRATEGIES.values()] == ["Keep corn", "Corn with grass", "Wheat on part"]


def test_engine_states():
    assert st.S1.engine == ("soybean", "corn", "fallow", "soybean")
    assert st.S3.engine == ("soybean", "fallow", "wheat", "fallow", "soybean")
    assert st.S2.engine == "not_simulated" and st.S2.simulated is False
    # Every chain ends in the same next soybean (13.1).
    assert st.S1.engine[-1] == st.S3.engine[-1] == "soybean"


def test_region_entries_have_the_schema_keys():
    for lang in ("en", "pt"):
        for s in st.STRATEGIES.values():
            e = s.region_entry(lang)
            assert set(e) == {"id", "name", "sequence_text", "source_ids", "simulated"}
            assert e["source_ids"] and all(x in st.SOURCES for x in e["source_ids"])
            assert e["name"] == st.load_copy(lang)["page"]["strategies"][s.id]["name"]
            assert e["sequence_text"] == st.load_copy(lang)["page"]["strategies"][s.id]["sequence"]


def test_no_sentence_in_the_module():
    """Every string literal of strategies.py is an id, a code, a date, a crop name of the
    Tábua, a copy key, a source title or a URL: no screen sentence lives here."""
    tree = ast.parse(inspect.getsource(st))
    docstrings = {id(n.body[0].value) for n in ast.walk(tree)
                  if isinstance(n, (ast.Module, ast.FunctionDef, ast.ClassDef)) and n.body
                  and isinstance(n.body[0], ast.Expr) and isinstance(n.body[0].value, ast.Constant)}
    titles = {v["title"] for v in st.SOURCES.values()}
    for n in ast.walk(tree):
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docstrings:
            v = n.value
            if v in titles or v.startswith("http"):
                continue
            assert not (" " in v and (v.endswith(".") or ". " in v)), v


def test_copy_text_lookup():
    assert st.text("page.strategies.S1.name") == "Keep corn"
    assert st.text("states.keep", "pt") == st.load_copy("pt")["states"]["keep"]
    with pytest.raises(KeyError):
        st.text("page.strategies.S9.name")
    with pytest.raises(ValueError):
        st.load_copy("fr")


def test_zarc_crops_and_tables():
    assert st.S1.zarc_crop == "Milho 2ª Safra"
    assert st.S2.zarc_crop == "Milho 2ª Safra consorciado com Braquiária"
    assert st.S3.zarc_crop == "Trigo"
    assert st.zarc_table_safra(st.S1, PROTOCOL, "2026/2027") == "2026/2027"
    assert st.zarc_table_safra(st.S3, PROTOCOL, "2026/2027") == PROTOCOL["zarc"]["wheat_table"] == "2025/2026"
    with pytest.raises(KeyError):
        st.zarc_table_safra(st.S3, {}, "2026/2027")


def test_hard_rule_in_the_decision_mjs_shape():
    rules = st.hard_rules()
    assert len(rules) == 1
    r = rules[0]
    assert {"id", "removes", "why"} <= set(r) and r["removes"] == []
    assert r["why"] == st.load_copy("en")["page"]["limits"]["reasons"][r["id"]]
    assert st.hard_rules(lang="pt")[0]["why"] == st.load_copy("pt")["page"]["limits"]["reasons"][r["id"]]
    assert r["soy_sowing"] == {"start": "09-01", "end": "12-31"}
    assert r["soy_free"] == {"start": "06-02", "end": "08-31"}
    assert st.hard_rules({r["id"]: ["S3"]})[0]["removes"] == ["S3"]
    with pytest.raises(ValueError):
        st.hard_rules({r["id"]: ["S9"]})


def test_soft_rules_warn_and_never_remove():
    assert {r["id"] for r in st.SOFT_RULES} == {"wheat_max_half_area", "one_winter_without_wheat_or_barley"}
    assert all(r["removes"] == [] for r in st.SOFT_RULES)
    for r in st.soft_rules("pt"):
        assert r["text"] == st.load_copy("pt")["page"]["limits"]["soft"][r["id"]]


def test_constraints_of_section_7_4():
    by_id = {c["id"]: c for c in st.CONSTRAINTS}
    assert by_id["no_narrow_row_seeder"]["removes"] == ["S3"]
    assert by_id["no_grass_seeding"]["removes"] == ["S2"]
    assert by_id["no_wheat_buyer"]["removes"] == ["S3"]
    assert by_id["rented_land"]["removes"] == []              # an item of the sheet
    assert by_id["zarc_insurance_credit"]["removes"] == []    # rule 6 lives in decision.mjs
    for c in st.CONSTRAINTS:
        assert set(c) == {"id", "removes"}
    for lang in ("en", "pt"):
        limits = st.load_copy(lang)["page"]["limits"]
        for c in st.constraints(lang):
            assert set(c) == {"id", "label", "removes", "why"}
            assert c["label"] == limits[c["id"]]["question"]
            assert c["why"] == (limits["reasons"][c["id"]] if c["removes"] else limits[c["id"]]["effect"])


def test_alerts_never_change_a_state():
    for a in st.ALERTS:
        assert set(a) == {"id", "trigger", "source_id", "audited"}
    for a in st.alerts():
        assert set(a) == {"id", "text_en", "text_pt", "trigger", "source_id", "audited"}
        assert a["text_en"] == st.load_copy("en")["sheet"]["alerts"][a["id"]]
        assert a["text_pt"] == st.load_copy("pt")["sheet"]["alerts"][a["id"]]
    unaudited = [a["id"] for a in st.ALERTS if not a["audited"]]
    assert unaudited == ["blast_and_harvest_rain"]
    ids = {a["id"] for a in st.ALERTS}
    assert {"pratylenchus", "wheat_root_disease", "white_mold", "leafhopper_stunting", "frost_at_heading",
            "gibberella"} <= ids


def test_no_composite_score_or_ranking():
    for s in st.STRATEGIES.values():
        for word in ("score", "rank", "best"):
            assert not hasattr(s, word)

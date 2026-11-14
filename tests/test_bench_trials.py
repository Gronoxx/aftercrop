# Written with an SI tool (Claude, Anthropic) for NASA Space Apps 2026. See AI_USE.md.
"""Transcription reader and comparison (M4.6 logic), on synthetic CSVs only.

The real files are compared in tests/test_trials.py.
"""

import json
from fractions import Fraction
from pathlib import Path

import pytest

from pipeline.bench import trials

FIX = Path(__file__).parent / "fixtures"
TRFX = json.loads((FIX / "bench_trials.json").read_text(encoding="utf-8"))
OFFSETS = TRFX["page_offsets"]


def write(tmp_path, name, text):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


def load(tmp_path, csv_a=None, csv_b=None, offsets=OFFSETS):
    pa = write(tmp_path, "a.csv", TRFX["csv_a"] if csv_a is None else csv_a)
    pb = write(tmp_path, "b.csv", TRFX["csv_b"] if csv_b is None else csv_b)
    a = trials.read_transcription(pa, "a", page_offsets=offsets)
    b = trials.read_transcription(pb, "b", page_offsets=offsets)
    return a, b


# Normalisation -------------------------------------------------------------------

@pytest.mark.parametrize("text, expected", [
    ("2012/13", ("2012/13", 2012)),
    ("2012/2013", ("2012/13", 2012)),
    (" 2021 / 2022 ", ("2021/22", 2021)),
    ("1999/00", ("1999/00", 1999)),
    ("1999/2000", ("1999/00", 1999)),
])
def test_season_forms(text, expected):
    assert trials.normalize_season(text) == expected


@pytest.mark.parametrize("text", ["2012/14", "2012/2014", "2012-13", "12/13", "2013/12", ""])
def test_bad_season_is_refused(text):
    with pytest.raises(ValueError):
        trials.normalize_season(text)


@pytest.mark.parametrize("text, expected", [
    ("Floresta-PR", "floresta"),
    ("Floresta - PR", "floresta"),
    ("Floresta (PR)", "floresta"),
    ("Floresta", "floresta"),
    ("Cafelândia", "cafelandia"),
    ("Cafelandia-PR", "cafelandia"),
    ("Vila  Fictícia", "vila ficticia"),
])
def test_site_forms(text, expected):
    assert trials.normalize_site(text) == expected


def test_other_states_are_not_stripped():
    assert trials.normalize_site("Floresta-SP") != trials.normalize_site("Floresta")


@pytest.mark.parametrize("text, expected", [
    ("trigo", "trigo"),
    ("milho_2a_safra", "milho_2a_safra"),
    ("milho_2a_safra+braquiaria", "milho_2a_safra_braquiaria"),
    ("milho_2a_safra_braquiaria", "milho_2a_safra_braquiaria"),
    ("diferenca_trigo_menos_milho_2a_safra", trials.DERIVED_PREFIX),
])
def test_predecessor_forms(text, expected):
    assert trials.normalize_predecessor(text) == expected


@pytest.mark.parametrize("text", ["cevada", "milho", "soja"])
def test_unknown_predecessor_is_refused(text):
    with pytest.raises(ValueError):
        trials.normalize_predecessor(text)


def test_doc_unit_cv_and_page():
    assert trials.normalize_doc("DOC-476") == "doc476"
    assert trials.normalize_doc("Doc398") == "doc398"
    assert trials.normalize_unit(" KG/HA ") == "kg/ha"
    assert trials.normalize_cv("CV=5.7%") == trials.normalize_cv("CV = 5,7 %") == ("cv", Fraction(57, 10), "%")
    assert trials.normalize_cv("") is None
    with pytest.raises(ValueError):
        trials.normalize_cv("5.7")
    with pytest.raises(ValueError):
        trials.normalize_unit("t/ha")
    assert trials.printed_page("46", "doc476", "pdf", trials.PDF_MINUS_PRINTED) == 45
    assert trials.printed_page("45", "doc476", "printed", trials.PDF_MINUS_PRINTED) == 45
    with pytest.raises(ValueError):
        trials.printed_page("12", "doc999", "pdf", trials.PDF_MINUS_PRINTED)


def test_page_conventions_follow_the_notes():
    assert trials.PAGE_CONVENTION == {"a": "printed", "b": "pdf"}
    assert trials.PDF_MINUS_PRINTED["doc476"] == 1


# Comparison --------------------------------------------------------------------------

def test_synthetic_transcriptions_are_equal(tmp_path):
    a, b = load(tmp_path)
    c = trials.compare(a, b)
    assert c.equal, c.summary()
    assert c.n_compared == TRFX["expected"]["n_compared"]
    assert c.derived_dropped_b == TRFX["expected"]["derived_dropped_b"]
    assert c.derived_dropped_a == 0


def test_changed_value_is_a_mismatch(tmp_path):
    a, b = load(tmp_path, csv_b=TRFX["csv_b"].replace(",trigo,3000,kg/ha,3000,", ",trigo,3001,kg/ha,3001,"))
    c = trials.compare(a, b)
    assert not c.equal
    assert {f for _, f, _, _ in c.mismatches} == {"soy_yield", "soy_yield_kg_ha"}


def test_changed_cv_is_a_mismatch(tmp_path):
    a, b = load(tmp_path, csv_b=TRFX["csv_b"].replace('"CV=5,0%"', '"CV=5,1%"', 1))
    c = trials.compare(a, b)
    assert not c.equal and [f for _, f, _, _ in c.mismatches] == ["cv_or_sed"]


def test_wrong_page_offset_is_a_mismatch(tmp_path):
    a, b = load(tmp_path, offsets={"syn100": 1, "syn200": 0})
    c = trials.compare(a, b)
    assert not c.equal
    assert {f for _, f, _, _ in c.mismatches} == {"page_printed"}


def test_missing_row_and_duplicate_row(tmp_path):
    lines = TRFX["csv_b"].splitlines(keepends=True)
    a, b = load(tmp_path, csv_b="".join(lines[:1] + lines[2:]))
    c = trials.compare(a, b)
    assert not c.equal and len(c.only_in_a) == 1 and not c.only_in_b
    a, b = load(tmp_path, csv_b="".join(lines + lines[1:2]))
    c = trials.compare(a, b)
    assert not c.equal and len(c.duplicates_b) == 1


def test_changed_season_is_two_unmatched_rows(tmp_path):
    a, b = load(tmp_path, csv_b=TRFX["csv_b"].replace("1999/2000,trigo", "2000/2001,trigo"))
    c = trials.compare(a, b)
    assert not c.equal and len(c.only_in_a) == 1 and len(c.only_in_b) == 1


def test_bad_header_or_row_is_refused(tmp_path):
    with pytest.raises(ValueError):
        load(tmp_path, csv_a=TRFX["csv_a"].replace("cv_or_sed,", "cv,", 1))
    with pytest.raises(ValueError):
        load(tmp_path, csv_a=TRFX["csv_a"].replace(",trigo,", ",cevada,", 1))


def test_empty_files_are_not_equal(tmp_path):
    header = TRFX["csv_a"].splitlines()[0] + "\n"
    a, b = load(tmp_path, csv_a=header, csv_b=header)
    assert not trials.compare(a, b).equal


# Units and axis labels --------------------------------------------------------------

def test_units_convert_as_declared(tmp_path):
    a, b = load(tmp_path)
    assert trials.check_units(a.rows) == []
    assert trials.check_units(b.rows) == []


def test_wrong_conversion_or_undeclared_alqueire_is_flagged(tmp_path):
    a, _ = load(tmp_path, csv_a=TRFX["csv_a"].replace("2479.3", "2479.4"))
    assert len(trials.check_units(a.rows)) == 1
    a, _ = load(tmp_path, csv_a=TRFX["csv_a"].replace("(alqueire de 2.42 ha declarado)", "(alqueire)", 1)
                .replace("/ 2.42 (", "/ alq (", 1))
    assert any("2.42 ha alqueire" in p for p in trials.check_units(a.rows))
    a, _ = load(tmp_path, csv_a=TRFX["csv_a"].replace(",trigo,3000,kg/ha,3000,", ",trigo,3000,kg/ha,3001,"))
    assert len(trials.check_units(a.rows)) == 1


def test_axis_label_must_be_declared(tmp_path):
    a, b = load(tmp_path)
    assert trials.check_axis_labels(a.rows, docs=("syn200",)) == []
    assert trials.check_axis_labels(b.rows, docs=("syn200",)) == []
    a, _ = load(tmp_path, csv_a=TRFX["csv_a"].replace("rotulo do eixo", "eixo", 1))
    assert len(trials.check_axis_labels(a.rows, docs=("syn200",))) == 1


@pytest.mark.parametrize("value, expected", [
    (Fraction(1, 4), Fraction(3, 10)),
    (Fraction(-1, 4), Fraction(-3, 10)),
    (Fraction(6000, 242) * 100, Fraction(24793, 10)),
    (Fraction(5400, 242), Fraction(223, 10)),
    (Fraction(0), Fraction(0)),
])
def test_round_half_up(value, expected):
    assert trials.round_half_up(value, 1) == expected


# Observed pairs (synthetic only) ---------------------------------------------------

def test_observed_pairs_exclude_the_consortium_and_keep_exact_sacks(tmp_path):
    a, _ = load(tmp_path)
    got = trials.observed_pairs(a.rows)
    exp = TRFX["expected"]["observed_pairs"]
    assert [(p.site, p.season) for p in got] == [(e["site"], e["season"]) for e in exp]
    assert [p.delta for p in got] == [Fraction(*e["delta"]) for e in exp]
    assert got[0].soy_after_wheat == Fraction(100 * 60) / Fraction("2.42")


def test_site_season_without_a_corn_row_is_absent(tmp_path):
    lines = TRFX["csv_a"].splitlines(keepends=True)
    a, _ = load(tmp_path, csv_a="".join(line for line in lines if "Ficticia-PR,2001/2002,milho_2a_safra," not in line))
    assert [(p.site, p.season) for p in trials.observed_pairs(a.rows)] == [("monte inventado", "1999/00")]

# Written with an SI tool (Claude, Anthropic) for NASA Space Apps 2026. See AI_USE.md.
"""Criterion M4.6: the two transcriptions of the trials are equal.

Reads data/manual/trials_a.csv and trials_b.csv (outside git) and skips when
either file is absent. The only use of the trial values here is the equality
of the two transcriptions, after normalising site, season, predecessor and page,
and the declared alqueire of 2.42 ha in the Doc 398 rows. No statistic of the
bench is computed on these values.
"""

from pathlib import Path

import pytest

from pipeline.bench import trials

MANUAL = Path(__file__).resolve().parents[1] / "data" / "manual"
A = MANUAL / "trials_a.csv"
B = MANUAL / "trials_b.csv"

pytestmark = pytest.mark.skipif(not (A.exists() and B.exists()),
                                reason="data/manual/trials_a.csv or trials_b.csv absent")


@pytest.fixture(scope="module")
def both():
    return trials.read_transcription(A, "a"), trials.read_transcription(B, "b")


def test_m4_6_transcriptions_equal(both):
    a, b = both
    c = trials.compare(a, b)
    assert c.equal, c.summary()


def test_m4_6_doc398_rows_declare_the_alqueire(both):
    for t in both:
        sacks = [r for r in t.rows if r.unit == trials.UNIT_SC_ALQ]
        assert sacks, f"transcription {t.name} has no sc/alqueire rows"
        assert {r.doc for r in sacks} == {"doc398"}
        assert trials.check_units(t.rows) == [], t.name


def test_m4_6_doc398_season_read_from_the_axis_label(both):
    for t in both:
        assert trials.check_axis_labels(t.rows) == [], t.name

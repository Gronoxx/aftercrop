# Written with an SI tool (Claude, Anthropic) for NASA Space Apps 2026. See AI_USE.md.
"""The two transcriptions of the Embrapa trials (``data/manual/trials_a.csv`` and
``trials_b.csv``) and criterion M4.6: A equal to B.

The two files were written independently and differ in form only:

- page: A gives the printed page, B the PDF page (DOC-476: PDF 46 is printed 45),
  as each file's notes say;
- season: "2012/13" or "2012/2013";
- site: "Floresta-PR" or "Floresta";
- predecessor: "milho_2a_safra+braquiaria" or "milho_2a_safra_braquiaria";
- B adds derived rows ("diferenca_trigo_menos_milho_2a_safra"), computed by the
  transcriber and not printed in the documents. They are dropped before the
  comparison and counted, never compared.

Equality is checked on normalised doc, printed page, table or figure, site,
season, predecessor, unit, yield as printed, yield in kg/ha and CV, all exact.
The free-text note is not compared.

Rows in sacks per alqueire (Doc 398) must convert with the declared alqueire of
2.42 ha and sack of 60 kg, and say so in the note (M4.6).

The trial values never leave this module except through ``observed_pairs``,
which feeds T0 and T1 at the event and is tested here on synthetic rows only.
"""

from __future__ import annotations

import csv
import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path
from typing import Any

from pipeline.bench import stats

COLUMNS = ("doc", "page", "table_or_figure", "site", "season", "predecessor",
           "soy_yield", "unit", "soy_yield_kg_ha", "cv_or_sed", "note")

UNIT_KG_HA = "kg/ha"
UNIT_SC_ALQ = "sc/alqueire"
UNITS = (UNIT_KG_HA, UNIT_SC_ALQ)
SACK_KG = Fraction(60)
ALQUEIRE_HA = Fraction("2.42")  # declared by the project; Doc 398 does not define it
ALQUEIRE_DECLARATION = re.compile(r"2[.,]42\s*ha")

WHEAT = "trigo"
CORN = "milho_2a_safra"
CORN_BRACHIARIA = "milho_2a_safra_braquiaria"
PREDECESSORS = (WHEAT, CORN, CORN_BRACHIARIA)
DERIVED_PREFIX = "diferenca"

# Page convention of each transcription, from data/manual/trials_*_notes.md.
PAGE_CONVENTION = {"a": "printed", "b": "pdf"}
# PDF page minus printed page, per document, as both transcriptions state.
PDF_MINUS_PRINTED = {"doc398": 0, "doc459": 0, "doc476": 1}

DEFAULT_DIR = Path(__file__).resolve().parents[2] / "data" / "manual"


# Normalisation --------------------------------------------------------------------

def _plain(text: str) -> str:
    """Lowercase, no accents, single spaces."""
    t = unicodedata.normalize("NFKD", text)
    t = "".join(c for c in t if not unicodedata.combining(c))
    return " ".join(t.lower().split())


def normalize_doc(text: str) -> str:
    out = re.sub(r"[^a-z0-9]", "", _plain(text))
    if not out:
        raise ValueError("empty doc")
    return out


def normalize_site(text: str) -> str:
    """'Floresta-PR', 'Floresta - PR', 'Floresta (PR)' and 'Floresta' give 'floresta'."""
    t = re.sub(r"\s*(?:-|/|\()\s*pr\s*\)?$", "", _plain(text))
    if not t:
        raise ValueError("empty site")
    return t


def normalize_season(text: str) -> tuple[str, int]:
    """'2012/13' and '2012/2013' give ('2012/13', 2012). The end year must follow the start."""
    m = re.fullmatch(r"\s*(\d{4})\s*/\s*(\d{2}|\d{4})\s*", text)
    if not m:
        raise ValueError(f"season not in YYYY/YY or YYYY/YYYY form: {text!r}")
    start = int(m.group(1))
    end_txt = m.group(2)
    if len(end_txt) == 2:
        end = start // 100 * 100 + int(end_txt)
        if end < start:
            end += 100
    else:
        end = int(end_txt)
    if end != start + 1:
        raise ValueError(f"season does not span two consecutive years: {text!r}")
    return f"{start}/{end % 100:02d}", start


def normalize_predecessor(text: str) -> str:
    """Canonical predecessor, or DERIVED_PREFIX for a derived (difference) row."""
    t = re.sub(r"[\s+]+", "_", _plain(text))
    t = re.sub(r"_+", "_", t).strip("_")
    if t.startswith(DERIVED_PREFIX):
        return DERIVED_PREFIX
    if t not in PREDECESSORS:
        raise ValueError(f"unknown predecessor: {text!r}")
    return t


def normalize_table(text: str) -> str:
    t = _plain(text)
    if not t:
        raise ValueError("empty table_or_figure")
    return t


def normalize_unit(text: str) -> str:
    t = _plain(text).replace(" ", "")
    if t not in UNITS:
        raise ValueError(f"unknown unit: {text!r}")
    return t


def normalize_cv(text: str) -> tuple[str, Fraction, str] | None:
    """'CV=5.7%' gives ('cv', 57/10, '%'). Empty gives None."""
    t = _plain(text).replace(" ", "")
    if not t:
        return None
    m = re.fullmatch(r"(cv|sed)=(\d+(?:[.,]\d+)?)(%?)", t)
    if not m:
        raise ValueError(f"cv_or_sed not understood: {text!r}")
    return m.group(1), stats.exact(m.group(2)), m.group(3)


def printed_page(page: str, doc: str, convention: str, offsets: Mapping[str, int]) -> int:
    p = int(page)
    if convention == "printed":
        return p
    if convention == "pdf":
        if doc not in offsets:
            raise ValueError(f"no PDF-to-printed page offset for {doc}")
        return p - offsets[doc]
    raise ValueError(f"unknown page convention: {convention!r}")


# Reading -------------------------------------------------------------------------

@dataclass(frozen=True)
class TrialRow:
    doc: str
    page_printed: int
    table: str
    site: str
    season: str
    season_start: int
    predecessor: str
    soy_yield: Fraction
    unit: str
    soy_yield_kg_ha: Fraction
    cv: tuple[str, Fraction, str] | None
    note: str
    line: int

    @property
    def key(self) -> tuple[str, str, str, str]:
        return (self.doc, self.site, self.season, self.predecessor)

    def compared(self) -> dict[str, Any]:
        return {
            "page_printed": self.page_printed,
            "table_or_figure": self.table,
            "unit": self.unit,
            "soy_yield": self.soy_yield,
            "soy_yield_kg_ha": self.soy_yield_kg_ha,
            "cv_or_sed": self.cv,
        }


@dataclass
class Transcription:
    name: str
    rows: list[TrialRow]
    derived: list[TrialRow] = field(default_factory=list)


def read_transcription(
    path: str | Path,
    name: str,
    page_convention: str | None = None,
    page_offsets: Mapping[str, int] | None = None,
) -> Transcription:
    """Read one transcription CSV and normalise every row. A malformed row raises."""
    convention = page_convention or PAGE_CONVENTION[name]
    offsets = PDF_MINUS_PRINTED if page_offsets is None else page_offsets
    rows: list[TrialRow] = []
    derived: list[TrialRow] = []
    with open(path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        if tuple(reader.fieldnames or ()) != COLUMNS:
            raise ValueError(f"{path}: header {reader.fieldnames} differs from {list(COLUMNS)}")
        for i, raw in enumerate(reader, start=2):
            try:
                doc = normalize_doc(raw["doc"])
                season, start = normalize_season(raw["season"])
                row = TrialRow(
                    doc=doc,
                    page_printed=printed_page(raw["page"], doc, convention, offsets),
                    table=normalize_table(raw["table_or_figure"]),
                    site=normalize_site(raw["site"]),
                    season=season,
                    season_start=start,
                    predecessor=normalize_predecessor(raw["predecessor"]),
                    soy_yield=stats.exact(raw["soy_yield"]),
                    unit=normalize_unit(raw["unit"]),
                    soy_yield_kg_ha=stats.exact(raw["soy_yield_kg_ha"]),
                    cv=normalize_cv(raw["cv_or_sed"] or ""),
                    note=raw["note"] or "",
                    line=i,
                )
            except (TypeError, ValueError, KeyError) as exc:
                raise ValueError(f"{path}, line {i}: {exc}") from exc
            (derived if row.predecessor == DERIVED_PREFIX else rows).append(row)
    return Transcription(name=name, rows=rows, derived=derived)


# Comparison (M4.6) ---------------------------------------------------------------

@dataclass
class Comparison:
    n_a: int
    n_b: int
    n_compared: int
    only_in_a: list[tuple[str, str, str, str]]
    only_in_b: list[tuple[str, str, str, str]]
    duplicates_a: list[tuple[str, str, str, str]]
    duplicates_b: list[tuple[str, str, str, str]]
    mismatches: list[tuple[tuple[str, str, str, str], str, Any, Any]]
    derived_dropped_a: int
    derived_dropped_b: int

    @property
    def equal(self) -> bool:
        return not (self.only_in_a or self.only_in_b or self.duplicates_a
                    or self.duplicates_b or self.mismatches) and self.n_compared > 0

    def summary(self) -> str:
        lines = [f"A rows {self.n_a}, B rows {self.n_b}, compared {self.n_compared}, "
                 f"derived rows dropped A {self.derived_dropped_a} B {self.derived_dropped_b}"]
        lines += [f"only in A: {k}" for k in self.only_in_a]
        lines += [f"only in B: {k}" for k in self.only_in_b]
        lines += [f"duplicate in A: {k}" for k in self.duplicates_a]
        lines += [f"duplicate in B: {k}" for k in self.duplicates_b]
        lines += [f"{k} {f}: A {a!r} B {b!r}" for k, f, a, b in self.mismatches]
        return "\n".join(lines)


def _index(rows: Sequence[TrialRow]) -> tuple[dict, list]:
    out: dict = {}
    dups = []
    for r in rows:
        if r.key in out:
            dups.append(r.key)
        out[r.key] = r
    return out, dups


def compare(a: Transcription, b: Transcription) -> Comparison:
    ia, da = _index(a.rows)
    ib, db = _index(b.rows)
    common_keys = sorted(set(ia) & set(ib))
    mismatches = []
    for k in common_keys:
        va, vb = ia[k].compared(), ib[k].compared()
        for f in va:
            if va[f] != vb[f]:
                mismatches.append((k, f, va[f], vb[f]))
    return Comparison(
        n_a=len(a.rows),
        n_b=len(b.rows),
        n_compared=len(common_keys),
        only_in_a=sorted(set(ia) - set(ib)),
        only_in_b=sorted(set(ib) - set(ia)),
        duplicates_a=da,
        duplicates_b=db,
        mismatches=mismatches,
        derived_dropped_a=len(a.derived),
        derived_dropped_b=len(b.derived),
    )


def compare_files(path_a: str | Path | None = None, path_b: str | Path | None = None, **kw) -> Comparison:
    a = read_transcription(path_a or DEFAULT_DIR / "trials_a.csv", "a", **kw)
    b = read_transcription(path_b or DEFAULT_DIR / "trials_b.csv", "b", **kw)
    return compare(a, b)


# Unit checks (M4.6, Doc 398 with the declared alqueire) --------------------------------

def round_half_up(value: Fraction, places: int) -> Fraction:
    """Round half away from zero to ``places`` decimals, exactly."""
    scale = 10 ** places
    scaled = abs(value) * scale
    q = (scaled.numerator * 2 + scaled.denominator) // (scaled.denominator * 2)
    return Fraction(q if value >= 0 else -q, scale)


def sc_alq_to_kg_ha(value: Fraction) -> Fraction:
    """Exact conversion, sacks of 60 kg per alqueire of 2.42 ha to kg/ha."""
    return value * SACK_KG / ALQUEIRE_HA


def kg_ha_exact(row: TrialRow) -> Fraction:
    """Yield in kg/ha from the printed value, without the transcriber's rounding."""
    return row.soy_yield if row.unit == UNIT_KG_HA else sc_alq_to_kg_ha(row.soy_yield)


def check_units(rows: Sequence[TrialRow]) -> list[str]:
    """Problems with units: empty when every row converts as declared.

    - kg/ha rows: printed value equal to the kg/ha column;
    - sc/alqueire rows: kg/ha column equal to value x 60 / 2.42 rounded to 0.1,
      and the note declares the 2.42 ha alqueire.
    """
    problems = []
    for r in rows:
        if r.unit == UNIT_KG_HA:
            if r.soy_yield != r.soy_yield_kg_ha:
                problems.append(f"line {r.line} {r.key}: kg/ha value {r.soy_yield} != {r.soy_yield_kg_ha}")
            continue
        expected = round_half_up(sc_alq_to_kg_ha(r.soy_yield), 1)
        if r.soy_yield_kg_ha != expected:
            problems.append(f"line {r.line} {r.key}: {r.soy_yield} sc/alq gives {float(expected)} kg/ha, "
                            f"file has {float(r.soy_yield_kg_ha)}")
        if not ALQUEIRE_DECLARATION.search(_plain(r.note)):
            problems.append(f"line {r.line} {r.key}: note does not declare the 2.42 ha alqueire")
    return problems


def check_axis_labels(rows: Sequence[TrialRow], docs: Sequence[str] = ("doc398",)) -> list[str]:
    """Rows read from a chart must say the season came from the axis label (section 11.4)."""
    return [f"line {r.line} {r.key}: season not tied to the axis label"
            for r in rows if r.doc in docs and "rotulo do eixo" not in _plain(r.note)]


# Observed pairs for T0 and T1 ------------------------------------------------------

@dataclass(frozen=True)
class ObservedPair:
    """One site-season in the mechanism: soybean after wheat and after second-crop corn."""

    site: str
    season: str
    soy_after_wheat: Fraction
    soy_after_corn: Fraction

    @property
    def delta(self) -> Fraction:
        """Observed Delta, kg/ha: after wheat minus after second-crop corn."""
        return self.soy_after_wheat - self.soy_after_corn


def observed_pairs(rows: Sequence[TrialRow]) -> list[ObservedPair]:
    """Site-seasons with both wheat and second-crop corn rows, in kg/ha, exact.

    Corn with brachiaria is a consortium and stays out of the mechanism
    (section 11.4). A site-season missing one of the two rows is absent.
    Sack values convert without the 0.1 rounding of the transcription.
    """
    idx, dups = _index([r for r in rows if r.predecessor in (WHEAT, CORN)])
    if dups:
        raise ValueError(f"duplicate rows: {dups}")
    seasons = sorted({(r.site, r.season_start, r.season, r.doc) for r in idx.values()})
    out = []
    for site, _, season, doc in seasons:
        w = idx.get((doc, site, season, WHEAT))
        c = idx.get((doc, site, season, CORN))
        if w is None or c is None:
            continue
        out.append(ObservedPair(site=site, season=season,
                                soy_after_wheat=kg_ha_exact(w), soy_after_corn=kg_ha_exact(c)))
    keys = [(p.site, p.season) for p in out]
    if len(set(keys)) != len(keys):
        raise ValueError("the same site-season appears in two documents")
    return out

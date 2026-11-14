# Written with an SI tool (Claude, Anthropic) for NASA Space Apps 2026. See AI_USE.md.
"""Criterion M1.6: Zarc decoded, or plan B declared.

36 dekads per row, converted by pipeline/common/dekad.py (the ribbon's module),
both origins tested. Soybean in Regiao 2 (Norte) only from 1 Sep to 31 Dec.
"""

from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from pipeline.adapters import base, zarc
from pipeline.common import dekad

FIXTURE = Path(__file__).parent / "fixtures" / "zarc_synthetic.csv"
# Trial sites, then the municipalities of the field window (IBGE mesh, SA-D64).
MUNS = {"Londrina": 4113700, "Floresta": 4107900, "Cafelândia": 4103453, "Cambé": 4103701,
        "Bela Vista do Paraíso": 4102802}
SOY_WINDOW = {"start": "09-01", "end": "12-31"}


# Synthetic table: parser, windows and checks -----------------------------------

def read_fixture(**kw):
    kw.setdefault("uf", "PR")
    kw.setdefault("municipalities", [4113700, 4107900])
    kw.setdefault("crops", ["Soja", "Milho 2ª Safra", "Trigo"])
    return zarc.read(FIXTURE, **kw)


def test_read_filters_and_types():
    df = read_fixture()
    assert sorted(df["Nome_cultura"]) == ["Milho 2ª Safra", "Soja", "Trigo"]
    assert list(df.columns[-36:]) == zarc.DEC_COLS
    assert all(str(df[c].dtype) == "Int64" for c in zarc.DEC_COLS)


def test_read_rejects_unknown_header(tmp_path):
    bad = tmp_path / "bad.csv"
    bad.write_text("Nome_cultura;UF;dec1\nSoja;PR;0\n", encoding="utf-8")
    with pytest.raises(ValueError):
        zarc.read(bad)


def test_corn_window_crosses_new_year_in_one_piece():
    df = zarc.decode(read_fixture(crops=["Milho 2ª Safra"]), "2026/2027", "current")
    row = df.iloc[0]
    assert row["season"] == "2026/27"
    w = zarc.windows([row[c] for c in zarc.DEC_COLS], row["season"])
    assert len(w) == 1
    assert (w[0]["zarc_first"], w[0]["zarc_last"]) == (36, 8)
    assert (w[0]["ribbon_first"], w[0]["ribbon_last"]) == (12, 20)
    assert (w[0]["start"], w[0]["end"]) == ("2026-12-21", "2027-03-20")


def test_wheat_and_soy_windows():
    df = zarc.decode(read_fixture(), "2026/2027", "current").set_index("Nome_cultura")
    assert (df.loc["Soja", "window_start"], df.loc["Soja", "window_end"]) == ("2026-09-01", "2026-12-31")
    assert (df.loc["Trigo", "window_start"], df.loc["Trigo", "window_end"]) == ("2027-04-01", "2027-06-10")


def test_check_decoded_on_good_rows():
    df = zarc.decode(read_fixture(), "2026/2027", "current")
    st = zarc.check(df, SOY_WINDOW, {"2026/2027": ["Soja", "Milho 2ª Safra", "Trigo"]})
    assert st["zarc_status"] == "decoded"
    assert all(c["pass"] for c in st["checks"])


def test_check_plan_b_when_soy_falls_outside():
    df = zarc.decode(read_fixture(), "2026/2027", "current")
    df.loc[df["Nome_cultura"] == "Soja", "dec1"] = 20  # January: outside 1 Sep to 31 Dec
    st = zarc.check(df, SOY_WINDOW, {})
    assert st["zarc_status"] == "plan_b"
    assert not next(c for c in st["checks"] if c["id"] == "soy_only_1sep_31dec")["pass"]


def test_check_plan_b_when_a_dekad_is_blank():
    df = zarc.decode(read_fixture(), "2026/2027", "current")
    df.loc[0, "dec7"] = pd.NA
    assert zarc.check(df, SOY_WINDOW, {})["zarc_status"] == "plan_b"


def test_check_plan_b_on_unknown_value():
    df = zarc.decode(read_fixture(), "2026/2027", "current")
    df.loc[0, "dec7"] = 25
    assert zarc.check(df, SOY_WINDOW, {})["zarc_status"] == "plan_b"


def test_coverage_reports_missing_crop():
    df = zarc.decode(read_fixture(crops=["Soja"]), "2026/2027", "current")
    st = zarc.check(df, SOY_WINDOW, {"2026/2027": ["Soja", "Trigo"]})
    assert {"safra": "2026/2027", "crop": "Trigo", "municipio": None, "rows": 0} in st["coverage"]


def test_single_dekad_module():
    assert zarc.dekad is dekad


# Real table from the cache ----------------------------------------------------

@pytest.fixture(scope="session")
def real(offline_session):
    try:
        return zarc.build()
    except base.OfflineCacheMiss as e:
        pytest.skip(f"Zarc table not cached, run `make zarc` first: {e}")


@pytest.mark.data
def test_real_status_decoded(real):
    _, status = real
    failed = [c for c in status["checks"] if not c["pass"]]
    assert status["zarc_status"] == "decoded", f"declare plan B: {failed}"


@pytest.mark.data
def test_real_36_dekads_per_row(real):
    decoded, _ = real
    vals = decoded[zarc.DEC_COLS]
    assert vals.shape[1] == 36 and len(vals) > 0
    assert not vals.isna().any().any()
    assert set(pd.unique(vals.to_numpy().ravel()).tolist()) <= zarc.RISK_VALUES


@pytest.mark.data
@pytest.mark.parametrize("mun", sorted(MUNS))
def test_real_soy_north_only_1sep_31dec(real, mun):
    decoded, _ = real
    soy = decoded[(decoded["Nome_cultura"] == "Soja") & (decoded["municipio"] == mun)]
    assert len(soy) > 0
    for _, row in soy.iterrows():
        y = int(row["SafraIni"])
        indicated = [z for z in range(1, 37) if row[f"dec{z}"] > 0]
        assert indicated, "a soybean row with no indicated dekad"
        for z in indicated:
            lo, hi = dekad.zarc_bounds(z, y)
            assert date(y, 9, 1) <= lo and hi <= date(y, 12, 31), (mun, z, lo, hi)


@pytest.mark.data
def test_real_conversion_both_origins(real):
    decoded, _ = real
    long = zarc.long_form(decoded)
    assert len(long) == 36 * len(decoded)
    for z, r, season, first, last in long[["zarc_dekad", "ribbon_dekad", "season", "first_day", "last_day"]] \
            .drop_duplicates().itertuples(index=False):
        assert r == dekad.zarc_to_ribbon(z)
        rb = dekad.ribbon_bounds(r, season)
        assert (first, last) == (rb[0].isoformat(), rb[1].isoformat())
        zb = dekad.zarc_bounds(z, rb[0].year)
        assert zb == rb


@pytest.mark.data
def test_real_crops_and_gaps(real):
    decoded, _ = real
    cur = decoded[decoded["table_safra"] == "2026/2027"]
    for crop in ("Soja", "Milho 2ª Safra"):
        assert set(cur.loc[cur["Nome_cultura"] == crop, "municipio"]) == set(MUNS), crop
    # Wheat is not in the 2026/27 table; the 2025/26 rows are a labelled reference.
    assert not (cur["Nome_cultura"] == "Trigo").any()
    ref = decoded[decoded["table_role"] == "previous_season_reference"]
    assert set(ref["Nome_cultura"]) == {"Trigo"}
    assert set(ref["municipio"]) == set(MUNS)


@pytest.mark.data
def test_real_soy_has_management_levels(real):
    decoded, _ = real
    soy = decoded[(decoded["Nome_cultura"] == "Soja") & (decoded["table_safra"] == "2026/2027")]
    assert set(soy["nm"].dropna()) == {"NM1", "NM2", "NM3", "NM4"}


@pytest.mark.data
def test_real_raw_files_match_their_sha256(real):
    for spec in zarc.specs():
        path = base.raw_path(spec["source_id"], spec["url"], None, spec["filename"])
        assert base.verify(path), path


def test_the_decoder_covers_every_municipality_of_the_field_window():
    """SA-D64: each municipality the IBGE mesh finds over the field window is decoded."""
    cfg = base.load_config()
    rec = base.INTERIM_DIR / f"municipalities_{cfg['sources']['hls']['window']['id']}.json"
    if not rec.is_file():
        pytest.skip("run make municipalities first")
    import json
    found = {int(m["ibge"]) for m in json.loads(rec.read_text(encoding="utf-8"))["bands"]["window"]["municipalities"]
             if m["ibge"] != "none"}
    assert found and found <= set(cfg["sources"]["zarc"]["municipalities"])
    assert {int(s["ibge"]) for s in cfg["trial_sites"]} <= set(cfg["sources"]["zarc"]["municipalities"])

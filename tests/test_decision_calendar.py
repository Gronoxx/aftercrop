# Written with an SI tool (Claude, Anthropic) for NASA Space Apps 2026. See AI_USE.md.
"""Calendar and window reading, criteria M5.6 and M5.7 (plan sections 12.3, 13.7 and 17).

The plan names this check ``tests/test_calendar.py::test_window``. It lives here as
``test_decision_calendar.py::test_window``, beside the other M5 tests.
All Tábua rows, dates and intervals below are synthetic, "example, fictitious".
"""

import copy
import json
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from pipeline.adapters import base, zarc
from pipeline.common import dekad
from pipeline.decision import calendar as cal
from pipeline.decision import precompute, strategies

FIX = Path(__file__).parent / "fixtures"
INPUTS = json.loads((FIX / "decision_precompute_inputs.json").read_text(encoding="utf-8"))
PROTOCOL = base.load_config()["protocol"]
ROWS = precompute.zarc_rows_frame(INPUTS["zarc_rows"])
SEASON, SAFRA = INPUTS["season"], INPUTS["current_safra"]
CYCLES = INPUTS["cycles"]
FIELD_A = {"municipio": "Londrina", "zarc_ad_class": "AD4"}
FIELD_B = {"municipio": "Londrina", "zarc_ad_class": "AD3"}


def cols(field=FIELD_A, occupancy=None):
    return cal.columns(season=SEASON, field=field, zarc_rows=ROWS, cycles=CYCLES,
                       occupancy_specs=occupancy or INPUTS["occupancy"], protocol=PROTOCOL, current_safra=SAFRA)


def window(sid, field=FIELD_A):
    return cal.autumn_window(sid, field=field, zarc_rows=ROWS, cycles=CYCLES, protocol=PROTOCOL, season=SEASON,
                             current_safra=SAFRA)


def test_fixture_is_labelled_fictitious():
    assert INPUTS["label"] == "example, fictitious"
    assert (FIX / "decision_example_daily.csv").read_text(encoding="utf-8").startswith("# example, fictitious")


# M5.6 same instrument in the three columns ---------------------------------------

def test_one_function_builds_the_three_columns(monkeypatch):
    calls = []
    real = cal.column

    def spy(sid, **kw):
        calls.append(sid)
        return real(sid, **kw)

    monkeypatch.setattr(cal, "column", spy)
    out = cols()
    assert calls == ["S1", "S2", "S3"]
    assert list(out) == ["S1", "S2", "S3"]


def test_calendar_source_is_never_hls():
    for field in (FIELD_A, FIELD_B):
        for sid, col in cols(field).items():
            for t in col:
                src = t["source_id"] or ""
                assert not src.lower().startswith("hls"), (sid, t)


def test_hls_source_in_a_phase_is_refused():
    occ = copy.deepcopy(INPUTS["occupancy"])
    occ["S2"]["phases"][0]["source_id"] = "hls_vi_v2"
    with pytest.raises(cal.CalendarError):
        cols(occupancy=occ)


def test_same_rows_in_every_column():
    out = cols()
    for sid, col in out.items():
        states = [t["state"] for t in col]
        assert states[:2] == ["legal_window", "legal_break"], sid
        assert all(t["state"] in cal.TRECHO_STATES for t in col)
        assert all(set(t) == {"crop", "start", "end", "source_id", "state"} for t in col)
    # The legal marks are identical in the three columns.
    assert out["S1"][:2] == out["S2"][:2] == out["S3"][:2]


def test_growing_and_no_crop_cover_the_season_once():
    days = set(cal.season_days(SEASON))
    for sid, col in cols().items():
        covered = []
        for t in col:
            if t["state"] in ("growing", "no_crop"):
                a, b = date.fromisoformat(t["start"]), date.fromisoformat(t["end"])
                covered += [d for d in days if a <= d <= b]
        assert sorted(covered) == sorted(days), sid


def test_gap_moves_in_s3_and_grass_closes_it_in_s2():
    out = cols()
    gaps = {sid: [(t["start"], t["end"]) for t in col if t["state"] == "no_crop"] for sid, col in out.items()}
    assert ("2027-02-11", "2027-04-14") in gaps["S3"]          # declared fallow, February to April
    assert ("2027-08-01", "2027-08-31") in gaps["S1"]          # after the corn
    assert len(cal.no_crop_days(out["S2"])) < len(cal.no_crop_days(out["S1"]))
    assert all(t["crop"] == "none" for col in out.values() for t in col if t["state"] == "no_crop")


def test_overlapping_phases_are_refused():
    occ = copy.deepcopy(INPUTS["occupancy"])
    occ["S1"]["phases"].append({"crop": "wheat", "start": "07-01", "end": "08-15"})
    with pytest.raises(cal.CalendarError):
        cols(occupancy=occ)


def test_declared_gap_is_not_a_phase():
    occ = copy.deepcopy(INPUTS["occupancy"])
    occ["S1"]["phases"].append({"crop": "none", "start": "08-01", "end": "08-10"})
    with pytest.raises(cal.CalendarError):
        cols(occupancy=occ)


# Zarc windows: two origins, gaps, wheat from the older table ----------------------

def test_corn_window_crosses_new_year_and_uses_both_origins():
    w = window("S1")
    assert (w["start"], w["end"]) == ("2026-12-21", "2027-02-28")
    assert w["zarc_last"] == 6                                   # dec6 = 21-28/29 Feb, Zarc origin
    assert dekad.zarc_to_ribbon(w["zarc_last"]) == 18           # ribbon origin: 1 Sep is dekad 1
    assert not w["has_gap"] and not w["previous_table"]


def test_window_with_a_gap_keeps_every_run():
    w = window("S2")
    assert w["has_gap"]
    assert [(x["start"], x["end"]) for x in w["windows"]] == [("2026-12-21", "2027-01-20"), ("2027-02-01", "2027-02-28")]
    dw = cal.decision_windows(window("S1"), w)
    assert dw["corn"] == {"start": "2026-12-21", "end": "2027-02-28"}
    assert dw["grass"] == {"start": "2026-12-21", "end": "2027-02-28"} and dw["gaps"]["grass"]


def test_wheat_comes_from_the_protocol_table_dated_in_the_decision_season():
    assert PROTOCOL["zarc"]["wheat_table"] == "2025/2026"
    w = window("S3")
    assert w["table_safra"] == "2025/2026" and w["previous_table"]
    assert (w["start"], w["end"]) == ("2027-04-01", "2027-05-20")
    col = cols()["S3"]
    assert [t["state"] for t in col if t["crop"] == "wheat" and t["state"] != "growing"] == ["zarc_previous_table"]


def test_missing_row_gives_not_found_without_dates():
    w = window("S1", {"municipio": "Floresta", "zarc_ad_class": "AD4"})
    assert w["windows"] == [] and w["end"] is None
    col = cal.column("S1", season=SEASON, field={"municipio": "Floresta", "zarc_ad_class": "AD4"}, zarc_rows=ROWS,
                     cycles=CYCLES, occupancy_spec=INPUTS["occupancy"]["S1"], protocol=PROTOCOL, current_safra=SAFRA)
    nf = [t for t in col if t["state"] == "not_found"]
    assert nf == [{"crop": "corn", "start": None, "end": None, "source_id": "zarc_tabua", "state": "not_found"}]


def test_two_matching_rows_are_an_error_never_a_choice():
    rows = pd.concat([ROWS, ROWS.iloc[[0]]], ignore_index=True)
    with pytest.raises(cal.CalendarError):
        cal.autumn_window("S1", field=FIELD_A, zarc_rows=rows, cycles=CYCLES, protocol=PROTOCOL, season=SEASON,
                          current_safra=SAFRA)


def test_cycle_group_is_an_explicit_argument():
    with pytest.raises(cal.CalendarError):
        cal.autumn_window("S1", field=FIELD_A, zarc_rows=ROWS, cycles={}, protocol=PROTOCOL, season=SEASON,
                          current_safra=SAFRA)


# Hard rule ------------------------------------------------------------------------

def test_hard_rule_matches_the_config():
    cfg = base.load_config()["sources"]["zarc"]["soy_legal_window"]
    assert strategies.HARD_RULE["soy_sowing"] == cfg


def test_fixed_menu_passes_the_hard_rule():
    assert cal.hard_rule_breaks(INPUTS["occupancy"], SEASON) == {strategies.HARD_RULE["id"]: []}


@pytest.mark.parametrize("phase", [
    {"crop": "soybean", "start": "01-05", "end": "04-30"},      # sown outside 1 Sep to 31 Dec
    {"crop": "soybean", "start": "12-20", "end": "06-10"},      # alive in the soybean-free period
])
def test_hard_rule_breaks_remove_the_option(phase):
    occ = copy.deepcopy(INPUTS["occupancy"])
    occ["S3"]["phases"] = [phase]
    assert cal.hard_rule_breaks(occ, SEASON) == {strategies.HARD_RULE["id"]: ["S3"]}


# M5.7 honest window reading -------------------------------------------------------

def ok(season, low, high):
    return {"season": season, "low": low, "high": high, "gap_days": None, "status": "ok"}


def bad(season, status):
    return {"season": season, "low": None, "high": None, "gap_days": None, "status": status}


CORN = {"zarc_last": 6, "end": "2027-02-28"}      # synthetic: window ends on 28/29 Feb


def test_window():
    """M5.7: "inside" does not exist. "outside" only with the whole interval after the end of the
    corn Zarc window, zero delay, in 3 seasons or more. to_confirm and no_data stay out of k and n."""
    after = [ok("2018/19", "2019-03-01", "2019-03-10"), ok("2019/20", "2020-03-01", "2020-03-05"),
             ok("2020/21", "2021-03-02", "2021-03-20")]
    r = cal.window_reading(after, CORN, PROTOCOL)
    assert r == {"state": "outside", "k": 3, "n": 3, "zarc_window_end": "2027-02-28"}

    # Two seasons outside is not enough.
    r = cal.window_reading(after[:2], CORN, PROTOCOL)
    assert (r["state"], r["k"], r["n"]) == ("at_the_edge", 2, 2)

    # An interval that starts on the last day of the window, or straddles it, is not outside.
    edge = after[:2] + [ok("2020/21", "2021-02-28", "2021-03-20")]
    assert cal.window_reading(edge, CORN, PROTOCOL)["state"] == "at_the_edge"
    leap = after[:1] + [ok("2019/20", "2020-02-29", "2020-03-05"), after[2]]   # dec6 ends on 29 Feb in 2020
    assert cal.window_reading(leap, CORN, PROTOCOL)["k"] == 2
    leap_after = after[:1] + [ok("2019/20", "2020-03-01", "2020-03-05"), after[2]]
    assert cal.window_reading(leap_after, CORN, PROTOCOL)["k"] == 3

    # Seasons to confirm or without data are out of k and of n.
    mixed = after + [bad("2021/22", "to_confirm"), bad("2022/23", "no_data"), ok("2023/24", "2024-02-01", "2024-02-20")]
    r = cal.window_reading(mixed, CORN, PROTOCOL)
    assert (r["state"], r["k"], r["n"]) == ("outside", 3, 4)
    only_bad = [bad("2021/22", "to_confirm"), bad("2022/23", "no_data")]
    r = cal.window_reading(only_bad, CORN, PROTOCOL)
    assert (r["state"], r["k"], r["n"]) == ("at_the_edge", 0, 0)

    # The vocabulary has no "inside".
    assert "inside" not in cal.WINDOW_STATES
    for case in (after, after[:2], edge, mixed, only_bad, []):
        assert cal.window_reading(case, CORN, PROTOCOL)["state"] in ("at_the_edge", "outside")


def test_window_uses_the_protocol_values():
    ww = PROTOCOL["window_warning"]
    assert ww["outside_min_seasons"] == 3 and ww["of_seasons"] == 8 and ww["never"] == "inside"
    strict = copy.deepcopy(PROTOCOL)
    strict["window_warning"]["outside_min_seasons"] = 4
    after = [ok(f"{y}/{(y + 1) % 100:02d}", f"{y + 1}-03-05", f"{y + 1}-03-10") for y in (2018, 2019, 2020)]
    assert cal.window_reading(after, CORN, strict)["state"] == "at_the_edge"


def test_window_end_follows_each_season_through_dekad_py():
    # Same Zarc dekad, dated per season: 28 Feb in 2019, 29 Feb in 2020.
    assert zarc.dekad_dates(6, "2018/19")[1] == date(2019, 2, 28)
    assert zarc.dekad_dates(6, "2019/20")[1] == date(2020, 2, 29)


@pytest.mark.parametrize("green_end", [
    [ok("2017/18", "2018-03-01", "2018-03-05")],                                 # before the ribbon seasons
    [ok("2018/19", "2019-03-01", "2019-03-05"), ok("2018/19", "2019-03-01", "2019-03-05")],
    [{**ok("2018/19", "2019-03-01", "2019-03-05"), "status": "inside"}],
    [ok("2018/19", "2019-03-09", "2019-03-01")],                                 # low after high
    [ok("2018/19", "2018-03-01", "2018-03-05")],                                 # date in another season
])
def test_window_refuses_bad_input(green_end):
    with pytest.raises(cal.CalendarError):
        cal.window_reading(green_end, CORN, PROTOCOL)


def test_window_without_a_corn_window():
    r = cal.window_reading([ok("2018/19", "2019-03-01", "2019-03-10")], {"zarc_last": None, "end": None}, PROTOCOL)
    assert r == {"state": "no_window", "k": None, "n": None, "zarc_window_end": None}   # never "at the edge"


def test_example_fields_read_as_designed():
    a = INPUTS["fields"][0]["m2"]["green_end"]
    b = INPUTS["fields"][1]["m2"]["green_end"]
    ra = cal.window_reading(a, window("S1", FIELD_A), PROTOCOL)
    rb = cal.window_reading(b, window("S1", FIELD_B), PROTOCOL)
    assert (ra["state"], ra["k"], ra["n"]) == ("at_the_edge", 1, 7)
    assert (rb["state"], rb["k"], rb["n"]) == ("outside", 4, 6)


# Real Tábua (cache) ---------------------------------------------------------------

INTERIM = base.INTERIM_DIR / "zarc_norte_pr.csv"


@pytest.fixture(scope="module")
def interim():
    if not INTERIM.exists():
        pytest.skip("data/interim/zarc_norte_pr.csv absent, run `make zarc` first")
    return pd.read_csv(INTERIM)


@pytest.mark.data
@pytest.mark.parametrize("solo", ["AD1", "AD2", "AD3", "AD4", "AD5", "AD6"])
def test_real_corn_and_wheat_windows_for_londrina(interim, solo):
    field = {"municipio": "Londrina", "zarc_ad_class": solo}
    for ciclo in ("Grupo I", "Grupo II"):
        cyc = {"corn": ciclo, "corn_grass": ciclo, "wheat": ciclo}
        corn = cal.autumn_window("S1", field=field, zarc_rows=interim, cycles=cyc, protocol=PROTOCOL,
                                 season="2026/27", current_safra="2026/2027")
        assert corn["windows"] and corn["start"] <= corn["end"]
        assert corn["end"].startswith("2027-")
        wheat = cal.autumn_window("S3", field=field, zarc_rows=interim, cycles=cyc, protocol=PROTOCOL,
                                  season="2026/27", current_safra="2026/2027")
        assert wheat["previous_table"] and wheat["table_safra"] == "2025/2026"
        assert wheat["windows"] and wheat["start"].startswith("2027-04")


@pytest.mark.data
def test_real_consortium_rows_exist_in_the_raw_table():
    """The 2026/27 table has the intercrop rows S2 needs (Portaria 344/2026), but the config
    does not request them yet, so data/interim/ lacks them."""
    spec = next(s for s in zarc.specs() if s["kind"] == "table" and s["role"] == "current")
    path = base.raw_path(spec["source_id"], spec["url"], None, spec["filename"])
    if not path.exists():
        pytest.skip("raw Tábua 2026/27 absent, run `make zarc` first")
    crop = strategies.S2.zarc_crop
    df = zarc.decode(zarc.read(path, "PR", [4113700], [crop]), "2026/2027", "current")
    assert len(df) > 0 and set(df["Portaria"]) == {"Port.344_de_06-08-2026"}
    field = {"municipio": "Londrina", "zarc_ad_class": "AD1"}
    w = cal.autumn_window("S2", field=field, zarc_rows=df, cycles={"corn_grass": "Grupo II"}, protocol=PROTOCOL,
                          season="2026/27", current_safra="2026/2027")
    assert w["has_gap"] and len(w["windows"]) == 2


# SA-D64: the envelope of the Zarc window without a soil report ----------------------

def _dec(last_zarc: int, first_zarc: int = 36) -> list[int]:
    """Synthetic 36-dekad row: indicated from ``first_zarc`` (Dec) through Jan to ``last_zarc``."""
    out = [0] * 36
    for z in list(range(first_zarc, 37)) + list(range(1, last_zarc + 1)):
        out[z - 1] = 20
    return out


def _rows(spec):
    """spec: [(crop, municipio, ciclo, solo, last_zarc)] -> Tábua frame, example, fictitious."""
    return precompute.zarc_rows_frame([
        {"Nome_cultura": c, "municipio": m, "ciclo": cy, "solo": so, "table_safra": SAFRA, "table_role": "current",
         "dec": _dec(last)} for c, m, cy, so, last in spec])


CORN_CROP = strategies.strategy("S1").zarc_crop
GRASS_CROP = strategies.strategy("S2").zarc_crop
ENV_ROWS = _rows([
    (CORN_CROP, "Vila Ficticia", "Grupo I", "AD1", 2),     # ends 20 Jan
    (CORN_CROP, "Vila Ficticia", "Grupo II", "AD1", 4),    # ends 10 Feb
    (CORN_CROP, "Vila Ficticia", "Grupo II", "AD3", 6),    # ends 28/29 Feb
    (CORN_CROP, "Vila Ficticia", "Grupo I", "AD6", 8),     # ends 20 Mar: the latest of every class and cycle
    (GRASS_CROP, "Vila Ficticia", "Grupo II", "AD3", 5),   # ends 20 Feb
    (GRASS_CROP, "Vila Ficticia", "Grupo II", "AD1", 3),   # ends 31 Jan
])
# Green exits of five seasons (synthetic): lows on 15 Feb, 1 Mar, 5 Mar, 25 Mar and 1 Apr.
ENV_GREEN = [ok("2018/19", "2019-02-15", "2019-02-25"), ok("2019/20", "2020-03-01", "2020-03-08"),
             ok("2020/21", "2021-03-05", "2021-03-12"), ok("2021/22", "2022-03-25", "2022-04-02"),
             ok("2022/23", "2023-04-01", "2023-04-09"), bad("2023/24", "to_confirm")]


def env_reading(soil_class, crop=CORN_CROP, municipio="Vila Ficticia"):
    return cal.field_window_reading(ENV_GREEN, ENV_ROWS, crop=crop, municipio=municipio, soil_class=soil_class,
                                    table_safra=SAFRA, season=SEASON, protocol=PROTOCOL)


def test_envelope_is_the_latest_end_of_every_class_and_cycle():
    env = cal.envelope_window(ENV_ROWS, crop=CORN_CROP, municipio="Vila Ficticia", table_safra=SAFRA, season=SEASON)
    assert env == {"zarc_last": 8, "end": "2027-03-20", "classes": ["AD6"], "cycles": ["Grupo I"]}
    per = cal.class_window_ends(ENV_ROWS, crop=CORN_CROP, municipio="Vila Ficticia", table_safra=SAFRA,
                                season=SEASON)
    assert {k: v["end"] for k, v in per.items()} == {"AD1": "2027-02-10", "AD3": "2027-02-28", "AD6": "2027-03-20"}
    # A window that crosses the new year is read in ribbon order: 20 Jan comes after 21 Dec.
    assert per["AD1"]["ciclo"] == "Grupo II"


def test_without_a_soil_report_the_reading_is_for_any_soil_class():
    r = env_reading(None)
    assert r["basis"] == "for_any_soil_class" and r["soil_class"] == "unknown"
    assert (r["state"], r["k"], r["n"], r["zarc_window_end"]) == ("at_the_edge", 2, 5, "2027-03-20")
    assert env_reading("unknown") == r


def test_a_declared_class_uses_its_own_window():
    r = env_reading("AD3")                                    # Grupo II, ends 28/29 Feb
    assert r["basis"] == "declared_class" and r["soil_class"] == "AD3"
    assert (r["state"], r["k"], r["n"]) == ("outside", 4, 5)
    # A declared class takes its latest cycle group: AD1 ends on 10 Feb (Grupo II), not 20 Jan.
    assert env_reading("AD1")["zarc_window_end"] == "2027-02-10"
    assert env_reading("AD6")["zarc_window_end"] == "2027-03-20"        # only a Grupo I row
    # The envelope is never earlier than any declared class: k for any class <= k for each class.
    k_any = env_reading(None)["k"]
    for cls in ("AD1", "AD3", "AD6"):
        assert env_reading(cls)["k"] >= k_any


def test_no_window_is_its_own_state():
    r = env_reading(None, municipio="Monte Inventado")         # no corn row in the municipality
    assert r["state"] == "no_window" and r["k"] is None and r["basis"] == "for_any_soil_class"
    assert env_reading("AD5")["state"] == "no_window"           # the declared class has no row
    assert "no_window" in cal.WINDOW_STATES and "inside" not in cal.WINDOW_STATES


def test_only_ad_classes_or_unknown():
    for bad_class in ("AD7", "Tipo 3", "clayey", ""):
        with pytest.raises(cal.CalendarError, match="AD1 to AD6"):
            env_reading(bad_class)


def test_basis_lets_the_browser_recompute_the_same_reading():
    basis = cal.window_basis(ENV_GREEN, ENV_ROWS, municipio="Vila Ficticia", table_safra=SAFRA, season=SEASON,
                             protocol=PROTOCOL)
    assert basis["min_seasons"] == PROTOCOL["window_warning"]["outside_min_seasons"]
    assert [s["season"] for s in basis["seasons"]] == [g["season"] for g in ENV_GREEN]
    assert basis["S1"]["any"]["end_by_season"]["2018/19"] == "2019-03-20"
    assert set(basis["S1"]["by_class"]) == {"AD1", "AD3", "AD6"}
    assert basis["S1"]["by_class"]["AD1"]["ciclo"] == "Grupo II"
    for sid, crop in (("S1", CORN_CROP), ("S2", GRASS_CROP)):
        for cls in (None, *cal.AD_CLASSES):
            mine = cal.reading_from_basis(basis, sid, cls)
            ref = env_reading(cls, crop=crop)
            assert mine == ref, (sid, cls)
    # Every end is a date: no dekad logic is left for the browser.
    json.dumps(basis)
    assert basis["S2"]["any"]["end"] == "2027-02-20"


def test_python_reading_matches_the_shared_cases():
    """tests/fixtures/decision_window_basis.json pins the count for decision.mjs windowReadingFor too."""
    doc = json.loads((FIX / "decision_window_basis.json").read_text(encoding="utf-8"))
    assert doc["label"] == "example, fictitious"
    fresh = cal.window_basis(ENV_GREEN, ENV_ROWS, municipio="Vila Ficticia", table_safra=SAFRA, season=SEASON,
                             protocol=PROTOCOL)
    assert json.loads(json.dumps(fresh)) == doc["basis"]
    for c in doc["cases"]:
        assert cal.reading_from_basis(doc["basis"], c["sid"], c["soil_class"]) == c["expected"], c


# SA-D72: the Zarc risk band, the risk at exit and "ended before" ----------------------

def _risk_dec(by_zarc: dict[int, int]) -> list[int]:
    out = [0] * 36
    for z, v in by_zarc.items():
        out[z - 1] = v
    return out


def _risk_rows(spec):
    """spec: [(crop, ciclo, solo, {zarc dekad: risk})] in municipality "Vila Risco". Example, fictitious."""
    return precompute.zarc_rows_frame([
        {"Nome_cultura": c, "municipio": "Vila Risco", "ciclo": cy, "solo": so, "table_safra": SAFRA,
         "table_role": "current", "dec": _risk_dec(d)} for c, cy, so, d in spec])


# Risk does not rise with delay here, as in the real Tábua: 40% early, then outside, then 20%.
RISK_ROWS = _risk_rows([
    (CORN_CROP, "Grupo I", "AD2", {1: 20, 2: 30, 3: 40, 6: 20, 7: 20}),    # gap 1-20 Feb, ends 10 Mar
    (CORN_CROP, "Grupo II", "AD2", {1: 30, 2: 30, 3: 30, 4: 30, 5: 30}),   # ends 20 Feb
    (CORN_CROP, "Grupo I", "AD5", {1: 20, 2: 20, 3: 20, 4: 20, 5: 40, 6: 20, 7: 20, 8: 20, 9: 20}),  # ends 31 Mar
    (GRASS_CROP, "Grupo I", "AD5", {1: 30, 2: 30, 3: 30}),                 # ends 31 Jan
])
# High in dekads z1 (5 Jan), z4 (5 Feb), z5 (15 Feb), z9 (31 Mar, the last day of AD5) and z10 (5 Apr).
RISK_GREEN = [ok("2018/19", "2019-01-01", "2019-01-05"), ok("2019/20", "2020-02-01", "2020-02-05"),
              ok("2020/21", "2021-02-11", "2021-02-15"), ok("2021/22", "2022-03-20", "2022-03-31"),
              ok("2022/23", "2023-04-01", "2023-04-05"), bad("2023/24", "to_confirm")]


def risk_basis():
    return cal.window_basis(RISK_GREEN, RISK_ROWS, municipio="Vila Risco", table_safra=SAFRA, season=SEASON,
                            protocol=PROTOCOL)


def test_risk_band_is_in_ribbon_order_with_none_outside_the_zoning():
    band = cal.risk_band(RISK_ROWS, crop=CORN_CROP, municipio="Vila Risco", table_safra=SAFRA, season=SEASON)
    jan1 = dekad.zarc_to_ribbon(1) - 1                       # 1-10 Jan is ribbon index 13
    assert jan1 == 12
    assert set(band["by_class"]) == {"AD2", "AD5"}
    assert band["by_class"]["AD2"]["ciclo"] == "Grupo I"     # the cycle group that ends latest
    ad2 = band["by_class"]["AD2"]["risk"]
    assert len(ad2) == 36 and ad2[jan1:jan1 + 7] == [20, 30, 40, None, None, 20, 20]
    assert ad2[:jan1] == [None] * jan1                       # September to December: outside the zoning
    # Every class and cycle group: Grupo II of AD2 counts in the range even though it ends earlier.
    assert band["min"][jan1:jan1 + 10] == [20, 20, 20, 20, 30, 20, 20, 20, 20, None]
    assert band["max"][jan1:jan1 + 10] == [30, 30, 40, 30, 40, 20, 20, 20, 20, None]
    assert band["some_outside"][jan1:jan1 + 10] == [False, False, False, True, True, True, True, True, True, False]
    assert not any(band["some_outside"][:jan1])              # outside for every row is not "some"


def test_basis_carries_the_band_and_the_dekad_of_high():
    b = risk_basis()
    assert [g["high_dekad"] for g in b["seasons"]] == [13, 16, 17, 21, 22, None]
    assert b["S1"]["risk_band"]["by_class"]["AD5"]["risk"][12] == 20
    assert set(b["S2"]["risk_band"]["by_class"]) == {"AD5"}
    json.dumps(b)


def test_risk_at_exit_for_a_declared_class():
    b = risk_basis()
    r = cal.risk_at_exit_from_basis(b, "S1", "AD2")
    assert r == {"state": "ok", "n": 5, "groups": [{"risk": 20, "k": 1}, {"risk": None, "k": 4}],
                 "some_outside": None, "basis": "declared_class", "soil_class": "AD2"}
    r5 = cal.risk_at_exit_from_basis(b, "S1", "AD5")
    assert r5["groups"] == [{"risk": 20, "k": 3}, {"risk": 40, "k": 1}, {"risk": None, "k": 1}]
    # A class with no Tábua row has no window: no count, never a zero.
    assert cal.risk_at_exit_from_basis(b, "S1", "AD1")["state"] == "no_window"


def test_risk_at_exit_without_a_report_is_the_range_of_every_class_and_cycle():
    r = cal.risk_at_exit_from_basis(risk_basis(), "S1", None)
    assert r["basis"] == "for_any_soil_class" and r["n"] == 5
    assert r["groups"] == [{"risk_min": 20, "risk_max": 20, "k": 1}, {"risk_min": 20, "risk_max": 30, "k": 2},
                           {"risk_min": 30, "risk_max": 40, "k": 1}, {"risk_min": None, "risk_max": None, "k": 1}]
    assert r["some_outside"] == 3
    assert sum(g["k"] for g in r["groups"]) == r["n"]       # to_confirm seasons stay out of k and n


def test_ended_before_mirrors_outside_with_zero_delay():
    b = risk_basis()
    # AD2 closes on 10 Mar: highs of 5 Jan, 5 Feb, 15 Feb. AD5 and the envelope close on 31 Mar:
    # a high on that very day counts as ended before (the whole interval is not after the end).
    assert cal.ended_before_from_basis(b, "S1", "AD2") == {"state": "ok", "k": 3, "n": 5, "basis": "declared_class",
                                                           "soil_class": "AD2"}
    assert cal.ended_before_from_basis(b, "S1", "AD5")["k"] == 4
    # Without a report: the earliest end across every class and cycle group (SA-D84), here AD2
    # Grupo II on 20 Feb, not the envelope of the warning (31 Mar).
    assert {k: b["S1"]["earliest"][k] for k in ("end", "classes", "cycles")} == {
        "end": "2027-02-20", "classes": ["AD2"], "cycles": ["Grupo II"]}
    any_ = cal.ended_before_from_basis(b, "S1", "unknown")
    assert (any_["k"], any_["n"], any_["basis"]) == (3, 5, "for_any_soil_class")
    assert cal.ended_before_from_basis(b, "S1", "AD1")["state"] == "no_window"
    # "ended before" and "outside" never count the same season.
    for cls in ("AD2", "AD5", "unknown"):
        out = cal.reading_from_basis(b, "S1", cls)
        assert cal.ended_before_from_basis(b, "S1", cls)["k"] + out["k"] <= out["n"]


def test_ended_before_without_a_report_reads_the_earliest_cycle_group_not_the_latest_per_class():
    # A high on 1 Mar 2020: before the AD2 Grupo I end (10 Mar, the latest cycle group of the
    # earliest class) but after the AD2 Grupo II end (20 Feb). Only the earliest pair counts.
    b = json.loads(json.dumps(risk_basis()))
    b["seasons"][1] = {**b["seasons"][1], "high": "2020-03-01"}
    assert cal.ended_before_from_basis(b, "S1", "AD2")["k"] == 3
    assert cal.ended_before_from_basis(b, "S1", "unknown")["k"] == 2
    no_early = {**b, "S1": {k: v for k, v in b["S1"].items() if k != "earliest"}}
    assert cal.ended_before_from_basis(no_early, "S1", "unknown")["state"] == "no_window"
    w = cal.earliest_window(RISK_ROWS, crop=CORN_CROP, municipio="Nowhere", table_safra=SAFRA, season=SEASON)
    assert w == {"zarc_last": None, "end": None, "classes": [], "cycles": []}


def test_class_risk_ranges_for_the_l2_row():
    r = cal.class_risk_ranges(RISK_ROWS, crop=CORN_CROP, municipio="Vila Risco", table_safra=SAFRA, season=SEASON)
    assert r["by_class"] == {"AD2": {"ciclo": "Grupo I", "risk_min": 20, "risk_max": 40},
                             "AD5": {"ciclo": "Grupo I", "risk_min": 20, "risk_max": 40}}
    assert r["any_class"] == {"risk_min": 20, "risk_max": 40}
    none = cal.class_risk_ranges(RISK_ROWS, crop=CORN_CROP, municipio="Nowhere", table_safra=SAFRA, season=SEASON)
    assert none == {"by_class": {}, "any_class": {"risk_min": None, "risk_max": None}}


def test_exit_counts_match_the_shared_cases():
    """tests/fixtures/decision_risk_exit.json pins the same counts for decision.mjs endedBeforeFor and riskAtExitFor."""
    doc = json.loads((FIX / "decision_risk_exit.json").read_text(encoding="utf-8"))
    assert doc["label"] == "example, fictitious"
    assert json.loads(json.dumps(risk_basis())) == doc["basis"]
    for c in doc["cases"]:
        assert cal.ended_before_from_basis(doc["basis"], c["sid"], c["soil_class"]) == c["ended_before"], c
        assert cal.risk_at_exit_from_basis(doc["basis"], c["sid"], c["soil_class"]) == c["risk_at_exit"], c

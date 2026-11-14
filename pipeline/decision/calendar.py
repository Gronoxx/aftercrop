# Written with an SI tool (Claude, Anthropic) for NASA Space Apps 2026. See AI_USE.md.
"""Calendar of the three columns and the window reading (plan 7.5, 7.6, 12.3 and 13.2 to 13.4).

One function, ``column``, builds every column (M5.6), so the three rows use the same
instrument. Nothing here reads HLS: a calendar ``source_id`` is never an HLS source.
HLS enters only through ``window_reading``, which compares the end-of-green interval
of each season with the end of the corn Zarc window (12.3, M5.7). Without a soil report the
field has no Zarc class, and the end is the envelope: the latest end across every AD class
and cycle group of the municipality, "for any soil class" (SA-D64, ``field_window_reading``).
A municipality with no corn window gives ``no_window``.

Dekads come from ``pipeline.common.dekad`` through ``pipeline.adapters.zarc`` and nowhere
else. Two origins: the ribbon starts on 1 Sep, the Zarc table's dec1 is 1-10 Jan.

A column is a list of stretches ("trechos", plan 8.6): ``{crop, start, end, source_id, state}``,
with ISO dates inside one ribbon season (1 Sep to 31 Aug). ``state`` is one of:

- ``legal_window``: legal soybean sowing (hard rule, Portaria 1.579/2026);
- ``legal_break``: soybean-free period (same rule);
- ``zarc_window``: sowing window of the autumn crop in the current Tábua;
- ``zarc_previous_table``: the same from an older Tábua, labelled (wheat, 2025/26);
- ``not_found``: no Tábua row for the field (no dates);
- ``soil_class_unknown``: no Tábua row can apply, because the field has no Zarc soil class
  (``zarc_ad_class`` null: no soil report, see ``pipeline.fields.soil_profile``). Said aloud,
  never folded into ``not_found``;
- ``growing``: a live crop, from the declared calendar of the strategy;
- ``no_crop``: no live crop by that calendar. Never called bare soil.

Which Tábua row applies needs municipality, soil class and cycle (12.3). The cycle group
is not fixed by the plan or the protocol, so it is an explicit argument ([DECIDE]).
The growing stretches (sowing to harvest or desiccation) are not in the Tábua either:
they come in explicitly, each with its own source ([DECIDE]).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, timedelta
from typing import Any

import pandas as pd

from pipeline.adapters import zarc
from pipeline.common import dekad
from pipeline.decision import strategies

TRECHO_STATES = (
    "legal_window", "legal_break", "zarc_window", "zarc_previous_table", "not_found", "soil_class_unknown",
    "growing", "no_crop",
)
WINDOW_STATES = ("at_the_edge", "outside", "no_window")   # "inside" does not exist (12.3)
AD_CLASSES = ("AD1", "AD2", "AD3", "AD4", "AD5", "AD6")    # Zarc soil classes of the 2026/27 Tábua
SOIL_CLASS_UNKNOWN = "unknown"                             # no soil report (SA-D64)
BASIS_DECLARED = "declared_class"                          # the window of the declared class
BASIS_ANY = "for_any_soil_class"                           # the envelope: latest end of every class and cycle
GREEN_END_STATUSES = ("ok", "to_confirm", "no_data")
HLS_SOURCE_PREFIX = "hls"
ZARC_SOURCE_ID = "zarc_tabua"      # sources.zarc.source_id in the config


class CalendarError(ValueError):
    """A calendar input that the rule cannot read."""


# Dates in a ribbon season ---------------------------------------------------------

def season_bounds(season: str) -> tuple[date, date]:
    y = dekad.season_start_year(season)
    return date(y, dekad.RIBBON_START_MONTH, 1), date(y + 1, dekad.RIBBON_START_MONTH, 1) - timedelta(days=1)


def season_days(season: str) -> list[date]:
    first, last = season_bounds(season)
    return [first + timedelta(days=i) for i in range((last - first).days + 1)]


def md(season: str, month_day: str) -> date:
    """'MM-DD' inside the season: Sep to Dec in the first year, Jan to Aug in the second."""
    try:
        return zarc.season_date(season, month_day)
    except ValueError as exc:
        raise CalendarError(f"{month_day!r} is not a day of season {season}: {exc}") from exc


def trecho(crop: str, start: date | None, end: date | None, source_id: str | None, state: str) -> dict:
    if crop not in strategies.CROPS:
        raise CalendarError(f"unknown crop {crop!r}")
    if state not in TRECHO_STATES:
        raise CalendarError(f"unknown state {state!r}")
    if (start is None) != (end is None) or (start is not None and start > end):
        raise CalendarError(f"bad stretch {crop} {start}..{end}")
    return {
        "crop": crop,
        "start": start.isoformat() if start else None,
        "end": end.isoformat() if end else None,
        "source_id": source_id,
        "state": state,
    }


# Zarc rows -------------------------------------------------------------------------

def select_row(zarc_rows: pd.DataFrame, *, crop: str, municipio: str, solo: str, ciclo: str,
               table_safra: str) -> pd.Series | None:
    """The one Tábua row for crop, municipality, soil class, cycle and table edition.

    None when there is no row. More than one row is an error: the rule never picks one.
    """
    df = zarc_rows
    m = ((df["Nome_cultura"] == crop) & (df["municipio"] == municipio) & (df["solo"] == solo)
         & (df["ciclo"] == ciclo) & (df["table_safra"] == table_safra))
    sub = df[m]
    if len(sub) > 1:
        raise CalendarError(f"{len(sub)} Tábua rows for {crop}, {municipio}, {solo}, {ciclo}, {table_safra}")
    return None if sub.empty else sub.iloc[0]


def zarc_window(zarc_rows: pd.DataFrame, *, crop: str, municipio: str, solo: str, ciclo: str,
                table_safra: str, season: str) -> dict:
    """Sowing windows of one Tábua row, dated inside ``season``.

    Dekads are year-free, so a row of an older edition (wheat, 2025/26) is dated in the
    decision season by the same dekads. A window can have gaps (several runs): all runs
    are kept, and ``start``/``end`` are the first and last indicated days.
    """
    row = select_row(zarc_rows, crop=crop, municipio=municipio, solo=solo, ciclo=ciclo, table_safra=table_safra)
    out = {"crop": crop, "municipio": municipio, "solo": solo, "ciclo": ciclo, "table_safra": table_safra,
           "season": season, "windows": [], "start": None, "end": None, "has_gap": False,
           "risk_min": None, "risk_max": None, "zarc_last": None}
    if row is None:
        return out
    values = [row[c] for c in zarc.DEC_COLS]
    if any(pd.isna(v) for v in values):
        raise CalendarError(f"blank dekad in the Tábua row for {crop}, {municipio}")
    wins = zarc.windows([int(v) for v in values], season)
    if not wins:
        return out
    out.update({
        "windows": wins,
        "start": wins[0]["start"],
        "end": wins[-1]["end"],
        "has_gap": len(wins) > 1,
        "risk_min": min(w["risk_min"] for w in wins),
        "risk_max": max(w["risk_max"] for w in wins),
        "zarc_last": wins[-1]["zarc_last"],
    })
    return out


def autumn_window(sid: str, *, field: Mapping[str, Any], zarc_rows: pd.DataFrame, cycles: Mapping[str, str],
                  protocol: Mapping[str, Any], season: str, current_safra: str) -> dict:
    """Zarc window of the crop that follows soybean in strategy ``sid``, for one field.

    ``field`` gives ``municipio`` and ``zarc_ad_class``. ``cycles`` maps the calendar crop
    ("corn", "corn_grass", "wheat") to the Tábua cycle group, such as "Grupo II" ([DECIDE]).
    """
    s = strategies.strategy(sid)
    if s.autumn_crop not in cycles:
        raise CalendarError(f"no cycle group given for {s.autumn_crop} ({sid})")
    table = strategies.zarc_table_safra(s, protocol, current_safra)
    w = zarc_window(zarc_rows, crop=s.zarc_crop, municipio=field["municipio"], solo=field["zarc_ad_class"],
                    ciclo=cycles[s.autumn_crop], table_safra=table, season=season)
    w["previous_table"] = table != current_safra
    return w


# Growing stretches ------------------------------------------------------------------

def _phase_days(season: str, start_md: str, end_md: str) -> list[date]:
    a, b = md(season, start_md), md(season, end_md)
    first, last = season_bounds(season)
    if a <= b:
        return [a + timedelta(days=i) for i in range((b - a).days + 1)]
    # The phase crosses 31 Aug: the calendar repeats every season, so it wraps to 1 Sep.
    tail = [a + timedelta(days=i) for i in range((last - a).days + 1)]
    head = [first + timedelta(days=i) for i in range((b - first).days + 1)]
    return head + tail


def occupancy(spec: Mapping[str, Any], season: str) -> list[dict]:
    """Growing and no-crop stretches of one strategy over one season.

    ``spec``: ``{"source_id": ..., "phases": [{"crop", "start": "MM-DD", "end": "MM-DD",
    "source_id"?}]}``. Phases must not overlap. Days outside every phase are ``no_crop``,
    with the strategy's ``source_id``.
    """
    default_src = spec.get("source_id")
    by_day: dict[date, tuple[str, str | None, str]] = {}
    for p in spec.get("phases", []):
        crop = p["crop"]
        if crop == "none":
            raise CalendarError("a phase is a live crop. Gaps are derived, not declared")
        src = p.get("source_id", default_src)
        for d in _phase_days(season, p["start"], p["end"]):
            if d in by_day:
                raise CalendarError(f"phases overlap on {d} ({by_day[d][0]} and {crop})")
            by_day[d] = (crop, src, "growing")
    runs: list[dict] = []
    cur_key, cur_start, prev = None, None, None
    for d in season_days(season):
        key = by_day.get(d, ("none", default_src, "no_crop"))
        if key != cur_key:
            if cur_key is not None:
                runs.append(trecho(cur_key[0], cur_start, prev, cur_key[1], cur_key[2]))
            cur_key, cur_start = key, d
        prev = d
    runs.append(trecho(cur_key[0], cur_start, prev, cur_key[1], cur_key[2]))
    return runs


# Columns ---------------------------------------------------------------------------

def _check_sources(trechos: Sequence[Mapping[str, Any]]) -> None:
    for t in trechos:
        src = t.get("source_id")
        if isinstance(src, str) and src.lower().startswith(HLS_SOURCE_PREFIX):
            raise CalendarError(f"calendar stretch with an HLS source: {t}")


def column(sid: str, *, season: str, field: Mapping[str, Any], zarc_rows: pd.DataFrame,
           cycles: Mapping[str, str], occupancy_spec: Mapping[str, Any], protocol: Mapping[str, Any],
           current_safra: str) -> list[dict]:
    """One column of L1, the same function for S1, S2 and S3 (M5.6)."""
    rule = strategies.HARD_RULE
    out = [
        trecho("soybean", md(season, rule["soy_sowing"]["start"]), md(season, rule["soy_sowing"]["end"]),
               rule["source_id"], "legal_window"),
        trecho("soybean", md(season, rule["soy_free"]["start"]), md(season, rule["soy_free"]["end"]),
               rule["source_id"], "legal_break"),
    ]
    s = strategies.strategy(sid)
    w = autumn_window(sid, field=field, zarc_rows=zarc_rows, cycles=cycles, protocol=protocol,
                      season=season, current_safra=current_safra)
    src = ZARC_SOURCE_ID
    if w["windows"]:
        state = "zarc_previous_table" if w["previous_table"] else "zarc_window"
        for win in w["windows"]:
            out.append(trecho(s.autumn_crop, date.fromisoformat(win["start"]), date.fromisoformat(win["end"]),
                              src, state))
    else:
        out.append(trecho(s.autumn_crop, None, None, src, window_gap_state(field)))
    out.extend(occupancy(occupancy_spec, season))
    _check_sources(out)
    return out


def window_gap_state(field: Mapping[str, Any]) -> str:
    """State of an autumn window without dates: "soil_class_unknown" without a soil class, else "not_found"."""
    return "soil_class_unknown" if field.get("zarc_ad_class") is None else "not_found"


def columns(*, season: str, field: Mapping[str, Any], zarc_rows: pd.DataFrame, cycles: Mapping[str, str],
            occupancy_specs: Mapping[str, Mapping[str, Any]], protocol: Mapping[str, Any],
            current_safra: str) -> dict[str, list[dict]]:
    """The three columns, each from ``column``."""
    return {sid: column(sid, season=season, field=field, zarc_rows=zarc_rows, cycles=cycles,
                        occupancy_spec=occupancy_specs[sid], protocol=protocol, current_safra=current_safra)
            for sid in strategies.STRATEGY_IDS}


def hard_rule_breaks(occupancy_specs: Mapping[str, Mapping[str, Any]], season: str) -> dict[str, list[str]]:
    """Options whose declared calendar breaks the hard rule (13.5).

    A soybean phase must start inside the legal sowing window, and no soybean may be alive
    in the soybean-free period. Returns ``{rule_id: [option ids]}`` for region.json.
    """
    rule = strategies.HARD_RULE
    sow_a, sow_b = md(season, rule["soy_sowing"]["start"]), md(season, rule["soy_sowing"]["end"])
    free_a, free_b = md(season, rule["soy_free"]["start"]), md(season, rule["soy_free"]["end"])
    broken = []
    for sid in strategies.OPTION_IDS:
        for p in occupancy_specs[sid].get("phases", []):
            if p["crop"] != "soybean":
                continue
            start = md(season, p["start"])
            days = _phase_days(season, p["start"], p["end"])
            if not (sow_a <= start <= sow_b) or any(free_a <= d <= free_b for d in days):
                broken.append(sid)
                break
    return {rule["id"]: broken}


def no_crop_days(col: Sequence[Mapping[str, Any]]) -> list[date]:
    """Days of the column without a live crop, by the declared calendar."""
    days = []
    for t in col:
        if t["state"] == "no_crop":
            a, b = date.fromisoformat(t["start"]), date.fromisoformat(t["end"])
            days.extend(a + timedelta(days=i) for i in range((b - a).days + 1))
    return sorted(days)


# Window reading (12.3, M5.7) ------------------------------------------------------

def _window_end_in(season: str, zarc_last: int) -> date:
    return zarc.dekad_dates(zarc_last, season)[1]


def window_reading(green_end: Sequence[Mapping[str, Any]], corn_window: Mapping[str, Any],
                   protocol: Mapping[str, Any]) -> dict:
    """``{state, k, n, zarc_window_end}`` of the satellite window warning.

    A season counts as "outside" only when its whole end-of-green interval lies after the
    last day of the corn Zarc window, with zero delay: ``low > end``. One-sided: HLS never
    infers "inside". Seasons "to_confirm" or "no_data" stay out of k and n. The field reads
    "outside" when k reaches ``protocol.window_warning.outside_min_seasons``, else
    "at_the_edge". The window end is the same dekad in every season, dated by dekad.py.
    """
    ww = protocol["window_warning"]
    min_k = int(ww["outside_min_seasons"])
    of_seasons = int(ww["of_seasons"])
    allowed = _ribbon_seasons(protocol)
    seen = set()
    for g in green_end:
        s = g["season"]
        if s in seen:
            raise CalendarError(f"season {s} twice in green_end")
        seen.add(s)
        if allowed is not None and s not in allowed:
            raise CalendarError(f"season {s} outside the ribbon seasons {allowed[0]}..{allowed[-1]}")
        if g["status"] not in GREEN_END_STATUSES:
            raise CalendarError(f"unknown green_end status {g['status']!r}")
    if len(green_end) > of_seasons:
        raise CalendarError(f"{len(green_end)} seasons, the protocol reads {of_seasons}")

    zarc_last = corn_window.get("zarc_last")
    end_now = corn_window.get("end")
    if zarc_last is None:
        # No corn Zarc window to compare with: a state of its own, never "at the edge".
        return {"state": "no_window", "k": None, "n": None, "zarc_window_end": None}
    k = n = 0
    for g in green_end:
        if g["status"] != "ok":
            continue
        low, high = date.fromisoformat(g["low"]), date.fromisoformat(g["high"])
        if low > high:
            raise CalendarError(f"green_end {g['season']}: low after high")
        if dekad.season_label(low) != g["season"]:
            raise CalendarError(f"green_end {g['season']}: low {low} is in another season")
        n += 1
        if low > _window_end_in(g["season"], int(zarc_last)):
            k += 1
    state = "outside" if k >= min_k else "at_the_edge"
    assert state in WINDOW_STATES
    return {"state": state, "k": k, "n": n, "zarc_window_end": end_now}


# Envelope of the Zarc window (SA-D64) ----------------------------------------------------

def class_window_ends(zarc_rows: pd.DataFrame, *, crop: str, municipio: str, table_safra: str, season: str,
                      ciclo: str | None = None) -> dict[str, dict]:
    """``{AD class: {ciclo, zarc_last, ribbon_last, start, end, windows}}`` of one crop in one municipality.

    ``ciclo`` given: that cycle group only. None: for each class, the cycle group whose window
    ends latest. A class without indicated dekads is left out. The latest end is compared in
    ribbon order (1 Sep to 31 Aug), so a window that crosses the new year is read in one piece.
    """
    df = zarc_rows
    m = (df["Nome_cultura"] == crop) & (df["municipio"] == municipio) & (df["table_safra"] == table_safra)
    if ciclo is not None:
        m &= df["ciclo"] == ciclo
    out: dict[str, dict] = {}
    for _, row in df[m].iterrows():
        solo = row["solo"]
        if solo not in AD_CLASSES:
            continue
        values = [row[c] for c in zarc.DEC_COLS]
        if any(pd.isna(v) for v in values):
            raise CalendarError(f"blank dekad in the Tábua row for {crop}, {municipio}, {solo}")
        wins = zarc.windows([int(v) for v in values], season)
        if not wins:
            continue
        cand = {"ciclo": row["ciclo"], "zarc_last": wins[-1]["zarc_last"], "ribbon_last": wins[-1]["ribbon_last"],
                "start": wins[0]["start"], "end": wins[-1]["end"], "windows": wins}
        old = out.get(solo)
        if old is not None and ciclo is not None:
            raise CalendarError(f"two Tábua rows for {crop}, {municipio}, {solo}, {ciclo}, {table_safra}")
        if old is None or cand["ribbon_last"] > old["ribbon_last"]:
            out[solo] = cand
    return dict(sorted(out.items()))


def envelope_window(zarc_rows: pd.DataFrame, *, crop: str, municipio: str, table_safra: str, season: str) -> dict:
    """The latest end of the window across every AD class and cycle group of the municipality.

    ``{"zarc_last", "end", "classes", "cycles"}``: the classes and cycle groups that reach that
    end. ``zarc_last`` and ``end`` are None when the municipality has no row with a window.
    """
    ends = class_window_ends(zarc_rows, crop=crop, municipio=municipio, table_safra=table_safra, season=season)
    if not ends:
        return {"zarc_last": None, "end": None, "classes": [], "cycles": []}
    last = max(e["ribbon_last"] for e in ends.values())
    top = {k: e for k, e in ends.items() if e["ribbon_last"] == last}
    first = next(iter(top.values()))
    return {"zarc_last": first["zarc_last"], "end": first["end"], "classes": sorted(top),
            "cycles": sorted({e["ciclo"] for e in top.values()})}


def earliest_window(zarc_rows: pd.DataFrame, *, crop: str, municipio: str, table_safra: str, season: str) -> dict:
    """The earliest end of the window across every AD class and cycle group of the municipality (SA-D84).

    The conservative side for "ended before" without a soil report: a season counts only when its
    first green cycle ended before the window that closes first. ``{"zarc_last", "end", "classes",
    "cycles"}``: the class and cycle group pairs that close on that day, as two sorted lists.
    ``zarc_last`` and ``end`` are None when the municipality has no row with a window.
    """
    df = zarc_rows
    m = (df["Nome_cultura"] == crop) & (df["municipio"] == municipio) & (df["table_safra"] == table_safra)
    cycles = sorted(df[m]["ciclo"].dropna().unique())
    per = [(c, e) for c in cycles
           for e in class_window_ends(zarc_rows, crop=crop, municipio=municipio, table_safra=table_safra,
                                      season=season, ciclo=c).items()]
    if not per:
        return {"zarc_last": None, "end": None, "classes": [], "cycles": []}
    first = min(e["ribbon_last"] for _, (_, e) in per)
    low = [(c, cls, e) for c, (cls, e) in per if e["ribbon_last"] == first]
    return {"zarc_last": low[0][2]["zarc_last"], "end": low[0][2]["end"], "classes": sorted({x[1] for x in low}),
            "cycles": sorted({x[0] for x in low})}


def check_soil_class(soil_class: str | None) -> str:
    """``AD1``..``AD6`` or ``unknown`` (None counts as unknown). Anything else is refused."""
    if soil_class is None or soil_class == SOIL_CLASS_UNKNOWN:
        return SOIL_CLASS_UNKNOWN
    if soil_class not in AD_CLASSES:
        raise CalendarError(f"Zarc soil class {soil_class!r}: AD1 to AD6 from a soil report, or unknown")
    return soil_class


def field_window_reading(green_end: Sequence[Mapping[str, Any]], zarc_rows: pd.DataFrame, *, crop: str,
                         municipio: str, soil_class: str | None, table_safra: str, season: str,
                         protocol: Mapping[str, Any]) -> dict:
    """``window_reading`` of one field for one corn crop (SA-D64).

    Declared class: the latest end of that class across its cycle groups (the user declares a
    soil, never a cycle, and "outside" stays one-sided). Unknown class: the envelope of every
    class and cycle group, labelled ``for_any_soil_class``. No window at all: ``no_window``.
    """
    cls = check_soil_class(soil_class)
    if cls == SOIL_CLASS_UNKNOWN:
        w = envelope_window(zarc_rows, crop=crop, municipio=municipio, table_safra=table_safra, season=season)
        basis = BASIS_ANY
    else:
        ends = class_window_ends(zarc_rows, crop=crop, municipio=municipio, table_safra=table_safra, season=season)
        w = ends.get(cls) or {"zarc_last": None, "end": None}
        basis = BASIS_DECLARED
    r = window_reading(green_end, w, protocol)
    return {**r, "basis": basis, "soil_class": cls}


def window_basis(green_end: Sequence[Mapping[str, Any]], zarc_rows: pd.DataFrame, *, municipio: str,
                 table_safra: str, season: str, protocol: Mapping[str, Any]) -> dict:
    """What the browser needs to recompute the window warning when the user declares a class.

    Per corn crop (S1 corn, S2 corn with grass): for each AD class, the last day of its window
    (the cycle group that ends latest) in every season of ``green_end``, and the same for the
    envelope. Plus the green-exit interval and status of every season. The browser counts a
    season "outside" when its status is ok and ``low`` falls after that day, as here.
    Dekads stay in Python: every end is already a date, every season carries ``high_dekad``
    (the ribbon index 1..36 of ``high``) and each crop its Zarc ``risk_band`` in ribbon order
    (SA-D72), so the browser reads the risk at exit by index only.
    """
    ww = protocol["window_warning"]
    seasons = [{"season": g["season"], "low": g.get("low"), "high": g.get("high"), "status": g["status"],
                "high_dekad": dekad.ribbon_index(date.fromisoformat(g["high"])) if g.get("high") else None}
               for g in green_end]
    out: dict[str, Any] = {"municipio": municipio, "min_seasons": int(ww["outside_min_seasons"]),
                           "classes": list(AD_CLASSES), "seasons": seasons}
    for sid in ("S1", "S2"):
        s = strategies.strategy(sid)
        per = class_window_ends(zarc_rows, crop=s.zarc_crop, municipio=municipio, table_safra=table_safra,
                                season=season)
        env = envelope_window(zarc_rows, crop=s.zarc_crop, municipio=municipio, table_safra=table_safra,
                              season=season)
        early = earliest_window(zarc_rows, crop=s.zarc_crop, municipio=municipio, table_safra=table_safra,
                                season=season)

        def dated(zarc_last):
            return {g["season"]: _window_end_in(g["season"], int(zarc_last)).isoformat() for g in seasons}
        out[sid] = {
            "crop": s.zarc_crop,
            "by_class": {k: {"ciclo": e["ciclo"], "start": e["start"], "end": e["end"],
                             "end_by_season": dated(e["zarc_last"])}
                         for k, e in per.items()},
            "any": None if env["zarc_last"] is None else {
                "end": env["end"], "classes": env["classes"], "cycles": env["cycles"],
                "end_by_season": dated(env["zarc_last"])},
            "earliest": None if early["zarc_last"] is None else {
                "end": early["end"], "classes": early["classes"], "cycles": early["cycles"],
                "end_by_season": dated(early["zarc_last"])},
            "risk_band": risk_band(zarc_rows, crop=s.zarc_crop, municipio=municipio, table_safra=table_safra,
                                   season=season),
        }
    return out


def reading_from_basis(basis: Mapping[str, Any], sid: str, soil_class: str | None) -> dict:
    """The browser's count, in Python: the same result as ``field_window_reading`` (tested)."""
    cls = check_soil_class(soil_class)
    b = basis[sid]
    w = b["any"] if cls == SOIL_CLASS_UNKNOWN else b["by_class"].get(cls)
    tag = BASIS_ANY if cls == SOIL_CLASS_UNKNOWN else BASIS_DECLARED
    if not w:
        return {"state": "no_window", "k": None, "n": None, "zarc_window_end": None, "basis": tag, "soil_class": cls}
    k = n = 0
    for g in basis["seasons"]:
        if g["status"] != "ok":
            continue
        n += 1
        if g["low"] > w["end_by_season"][g["season"]]:
            k += 1
    state = "outside" if k >= int(basis["min_seasons"]) else "at_the_edge"
    return {"state": state, "k": k, "n": n, "zarc_window_end": w["end"], "basis": tag, "soil_class": cls}


def _ribbon_seasons(protocol: Mapping[str, Any]) -> list[str] | None:
    span = (protocol.get("dates") or {}).get("ribbon_seasons")
    if not span:
        return None
    a, b = dekad.season_start_year(span[0]), dekad.season_start_year(span[1])
    return [f"{y}/{(y + 1) % 100:02d}" for y in range(a, b + 1)]


def decision_windows(corn: Mapping[str, Any], grass: Mapping[str, Any]) -> dict:
    """``zarcWindow`` for decision.mjs: ``{corn: {start, end}, grass: {start, end}}``.

    decision.mjs reads one interval per crop. A Tábua window with gaps is passed as its
    envelope, and ``gaps`` says so: a date inside a gap would read "inside". Reported as a
    needed change of decision.mjs (a list of intervals).
    """
    def one(w):
        return {"start": w["start"], "end": w["end"]} if w.get("start") else None
    return {
        "corn": one(corn),
        "grass": one(grass),
        "gaps": {"corn": bool(corn.get("has_gap")), "grass": bool(grass.get("has_gap"))},
    }


# Zarc risk band, risk at exit and "ended before" (SA-D72) ---------------------------

def _ribbon_risks(row: pd.Series, where: str) -> list[int | None]:
    """The 36 risk values of one Tábua row in ribbon order (1 Sep first). 0 becomes None: outside the zoning."""
    values = [row[c] for c in zarc.DEC_COLS]
    if any(pd.isna(v) for v in values):
        raise CalendarError(f"blank dekad in the Tábua row for {where}")
    by_zarc = [int(v) for v in values]
    out = []
    for r in range(1, dekad.N_DEKADS + 1):
        v = by_zarc[dekad.ribbon_to_zarc(r) - 1]
        out.append(v if v > 0 else None)
    return out


def _class_rows(zarc_rows: pd.DataFrame, *, crop: str, municipio: str, table_safra: str) -> pd.DataFrame:
    df = zarc_rows
    m = (df["Nome_cultura"] == crop) & (df["municipio"] == municipio) & (df["table_safra"] == table_safra)
    sub = df[m]
    return sub[sub["solo"].isin(AD_CLASSES)]


def risk_band(zarc_rows: pd.DataFrame, *, crop: str, municipio: str, table_safra: str, season: str) -> dict:
    """Zarc risk per ten-day period, in ribbon order, for the band above the ribbon (protocol.zarc_risk_band).

    ``by_class``: each AD class at its latest-ending cycle group (as ``class_window_ends``),
    ``{ciclo, risk: [36]}``. ``min`` and ``max``: the lowest and highest risk across every class
    and cycle group of the municipality, over the rows that zone that period. ``some_outside``:
    the period is zoned for some rows and outside the zoning for others. ``None`` in ``risk``,
    ``min`` or ``max`` is outside the zoning, never zero risk.
    """
    ends = class_window_ends(zarc_rows, crop=crop, municipio=municipio, table_safra=table_safra, season=season)
    rows = _class_rows(zarc_rows, crop=crop, municipio=municipio, table_safra=table_safra)
    all_risks = [_ribbon_risks(r, f"{crop}, {municipio}, {r['solo']}") for _, r in rows.iterrows()]
    by_class = {}
    for cls, e in ends.items():
        row = select_row(zarc_rows, crop=crop, municipio=municipio, solo=cls, ciclo=e["ciclo"], table_safra=table_safra)
        by_class[cls] = {"ciclo": e["ciclo"], "risk": _ribbon_risks(row, f"{crop}, {municipio}, {cls}")}
    lo, hi, partial = [], [], []
    for i in range(dekad.N_DEKADS):
        zoned = [r[i] for r in all_risks if r[i] is not None]
        lo.append(min(zoned) if zoned else None)
        hi.append(max(zoned) if zoned else None)
        partial.append(bool(zoned) and len(zoned) < len(all_risks))
    return {"crop": crop, "by_class": by_class, "min": lo, "max": hi, "some_outside": partial}


def class_risk_ranges(zarc_rows: pd.DataFrame, *, crop: str, municipio: str, table_safra: str,
                      season: str) -> dict:
    """Risk range of the sowing window for the L2 row (SA-D72.6).

    ``by_class``: ``{AD: {ciclo, risk_min, risk_max}}`` at the latest-ending cycle group.
    ``any_class``: the lowest and highest indicated risk across every class and cycle group of the
    municipality (``risk_min``/``risk_max`` None when no row has a window).
    """
    ends = class_window_ends(zarc_rows, crop=crop, municipio=municipio, table_safra=table_safra, season=season)
    by_class = {cls: {"ciclo": e["ciclo"], "risk_min": min(w["risk_min"] for w in e["windows"]),
                      "risk_max": max(w["risk_max"] for w in e["windows"])} for cls, e in ends.items()}
    rows = _class_rows(zarc_rows, crop=crop, municipio=municipio, table_safra=table_safra)
    zoned = [v for _, r in rows.iterrows() for v in _ribbon_risks(r, f"{crop}, {municipio}") if v is not None]
    return {"by_class": by_class,
            "any_class": {"risk_min": min(zoned) if zoned else None, "risk_max": max(zoned) if zoned else None}}


def _basis_seasons_ok(basis: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    return [g for g in basis["seasons"] if g["status"] == "ok"]


def ended_before_from_basis(basis: Mapping[str, Any], sid: str, soil_class: str | None) -> dict:
    """Seasons whose whole end-of-green interval ends before the corn window closed (SA-D72.3).

    A season counts when its status is ok and ``high`` is on or before the last day of the window
    in that season, with zero delay. The declared class: the window of that class, as the warning.
    Without a report: the earliest end across every class and cycle group (``earliest``, SA-D84),
    the conservative side for "before", where the warning uses the latest. A count, never a sowing
    date and never "inside". Same result as decision.mjs ``endedBeforeFor`` (tested).
    """
    cls = check_soil_class(soil_class)
    b = basis[sid]
    w = b.get("earliest") if cls == SOIL_CLASS_UNKNOWN else b["by_class"].get(cls)
    tag = BASIS_ANY if cls == SOIL_CLASS_UNKNOWN else BASIS_DECLARED
    if not w:
        return {"state": "no_window", "k": None, "n": None, "basis": tag, "soil_class": cls}
    ok = _basis_seasons_ok(basis)
    k = sum(1 for g in ok if g["high"] <= w["end_by_season"][g["season"]])
    return {"state": "ok", "k": k, "n": len(ok), "basis": tag, "soil_class": cls}


def risk_at_exit_from_basis(basis: Mapping[str, Any], sid: str, soil_class: str | None) -> dict:
    """Zarc risk of the ten-day period that holds ``high``, counted over the ok seasons (SA-D72.2).

    Declared class: ``groups = [{risk, k}]``, one per distinct value, ``risk`` None for a period
    outside the zoning (listed last). Unknown class: ``groups = [{risk_min, risk_max, k}]`` across
    every class and cycle group, and ``some_outside`` counts the seasons whose period is outside the
    zoning for at least one class and cycle group. ``n``: the ok seasons. A condition of the field
    being free, never a sowing date. Same result as decision.mjs ``riskAtExitFor`` (tested).
    """
    cls = check_soil_class(soil_class)
    band = basis[sid].get("risk_band") or {}
    any_class = cls == SOIL_CLASS_UNKNOWN
    tag = BASIS_ANY if any_class else BASIS_DECLARED
    if any_class:
        zoned = any(v is not None for v in band.get("max") or [])
        key_of = lambda i: (band["min"][i], band["max"][i])          # noqa: E731
    else:
        risks = (band.get("by_class", {}).get(cls) or {}).get("risk")
        zoned = bool(risks)
        key_of = lambda i: (risks[i],)                               # noqa: E731
    if not zoned:
        return {"state": "no_window", "n": None, "groups": [], "some_outside": None, "basis": tag, "soil_class": cls}
    ok = _basis_seasons_ok(basis)
    counts: dict[tuple, int] = {}
    partial = 0
    for g in ok:
        i = int(g["high_dekad"]) - 1
        counts[key_of(i)] = counts.get(key_of(i), 0) + 1
        partial += bool(any_class and band["some_outside"][i])
    keys = sorted(counts, key=lambda k: (k[0] is None, k[0] or 0, k[-1] or 0))   # outside the zoning last
    if any_class:
        groups = [{"risk_min": k[0], "risk_max": k[1], "k": counts[k]} for k in keys]
    else:
        groups = [{"risk": k[0], "k": counts[k]} for k in keys]
    return {"state": "ok", "n": len(ok), "groups": groups, "some_outside": partial if any_class else None,
            "basis": tag, "soil_class": cls}

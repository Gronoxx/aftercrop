# Written with an SI tool (Claude, Anthropic) for NASA Space Apps 2026. See AI_USE.md.
"""Assemble the JSON the site reads: field_<id>.json, trials_steps.json, ablation.json, region.json.

Plan sections 8.4 (steps 13 and 15), 8.6 (schema) and sheet M5. Every file carries
``schema_version``. A number on screen is never loose: it is a ``Num = {"v", "l"}`` with a
ledger id, or it sits inside an object whose ``ledger`` key names its record. M5 registers
a ledger record (the ten fields of 8.6) for every id it creates. Ids that arrive with the
M2 and M4 blocks belong to their owners and are listed as external.

The decision rule is never reimplemented here: the states of A3 come from
``run_states.mjs``, which imports ``site/js/decision.mjs``.

Two runs, chosen by the ``label`` of the inputs and confirmed on the command line:

- example: synthetic inputs labelled "example, fictitious" (tests/fixtures/). Its outputs
  carry that label and never go to site/data/, the folder the page reads as real;
- real (``--real``): inputs labelled "real", assembled from the M2 outputs by
  ``pipeline.decision.field_inputs``. Each field carries the provenance of its files
  (path and sha256, checked again here), and nothing in the inputs may carry the
  fictitious label or an ``example_*`` source. Real outputs never go to tests/. The price
  scenarios stay the example numbers of plan 14.3 in both runs ("fictitious": true).

Rehearsal (``--real --rehearsal``, SA-D63, ``pipeline.common.rehearsal``): real inputs stamped
``run_mode: rehearsal`` by ``field_inputs --rehearsal`` (development strip, fictitious trials).
Every site document and the M5 ledger carry the stamp, and nothing goes to site/data/. A run
without ``--rehearsal`` refuses stamped inputs and the ``rehearsal_trials`` source.

L2 climate (SA-D72.5, ``protocol.climate``): frost and rain over the occupation of the crop that follows
the soybean replace dry autumn and cold in the window, which had no sourced threshold (SA-D68).
``ribbon_counts.no_green`` (SA-D72.4) counts the ten-day periods with no green seen in the ribbon.

The site documents go to ``--out``; the M5 sidecars (``ledger_m5.json``,
``states_input_m5.json``, ``states_m5.json``) go to ``--build-out`` (data/build/), never
to site/data/, which assemble refuses.

Run from the repository root:
    python -m pipeline.decision.precompute --inputs <inputs.json> --out <dir> [--build-out data/build]
                                           [--zarc data/interim/zarc_norte_pr.csv] [--real]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Mapping, Sequence
from fractions import Fraction
from pathlib import Path
from typing import Any

import pandas as pd

from pipeline.adapters import base, zarc
from pipeline.bench import ablation as a3
from pipeline.bench.common import NOT_COMPLETED, NOT_RUN, REPORTED
from pipeline.common import rehearsal as rh
from pipeline.decision import calendar as cal
from pipeline.decision import erosivity as ero
from pipeline.decision import risk_counts as rc
from pipeline.decision import strategies as st
from pipeline.fields import ribbon as rb

SCHEMA_VERSION = "1"
EXAMPLE = "example, fictitious"
REAL = "real"
LABELS = (EXAMPLE, REAL)
LABEL = EXAMPLE                      # the label of the synthetic inputs (kept for its readers)
ASSEMBLER = "pipeline.decision.field_inputs"
SITE_DATA = base.REPO_ROOT / "site" / "data"
BUILD_DIR = base.REPO_ROOT / "data" / "build"
TESTS_DIR = base.REPO_ROOT / "tests"
SIDECARS = ("ledger_m5.json", "states_input_m5.json", "states_m5.json")
REAL_ORIGIN = "m2_outputs"           # run_states.mjs REAL_ORIGIN
RUN_STATES = Path(__file__).with_name("run_states.mjs")
DECISION_JS = "decision.mjs"

EVIDENCE_CLASSES = (
    "seen_by_satellite", "estimated_from_satellite", "reanalysis", "simulated", "measured_in_trials",
    "derived_from_trials", "official_rule", "published_climatology", "your_numbers", "unknown",
)
VALIDATION_STATES = (
    "not_applicable", "not_completed", "model_not_validated_here", "direction_not_validated",
    "direction_matched_mechanism_not_shown", "direction_supported_exploratory", "checked_against_station",
    "provisional", "not_simulated",
)
RECORD_FIELDS = ("value", "unit", "evidence_class", "source_id", "transform", "method_id", "data_version",
                 "uncertainty", "validation_state", "limits")

PRICE_FIELDS = ("p_soy", "p_corn", "p_wheat", "Y_corn", "Y_wheat", "c_corn", "c_wheat", "c_grass")
SCENARIO_IDS = ("example_low", "example_mid", "example_high")
RIBBON_CELLS = ("green", "not_green", "to_confirm", "no_data")
ABLATION_VERDICTS = ("changes_answers", "context_only", "not_run")
ABLATION_STATUSES = (REPORTED, NOT_RUN, NOT_COMPLETED)
SCALE_KEYS = ("power_cell_km", "radiation_cell_km", "soil_m", "dem_m", "erosivity_km")
# Paths whose numbers are not screen numbers (pixel geometry).
NOT_SCREEN_NUMBERS = {"polygon_px"}
GREEN_END_KEYS = ("season", "low", "high", "gap_days", "status")

# Numbers of the L3 lines (plan 7.5, 13.3, 13.4), named by the placeholders of copy
# page.rows.L3.*: key -> (unit, evidence class). Texts are strings beside them.
SUMMARY_NUMBERS = {
    "S2": {"gain": ("%", "measured_in_trials"), "roots_pct": ("%", "measured_in_trials"),
           "straw_pct": ("%", "measured_in_trials")},
    "S3": {"delta": ("kg/ha", "derived_from_trials"), "pos": ("site-seasons", "derived_from_trials"),
           "neg": ("site-seasons", "derived_from_trials"), "a": ("%", "measured_in_trials"),
           "b": ("%", "measured_in_trials")},
}
SUMMARY_TEXTS = {"S2": ("season",), "S3": ()}
SUMMARY_COUNTS = ("pos", "neg")
# The mean of the S3 deltas has its own id, which the registry requires as derived_from_trials (M6.4).
SUMMARY_IDS = {("S3", "delta"): "trials.S3.mean_delta"}
REFERENCE_TEXTS = ("season", "source")


class PrecomputeError(ValueError):
    pass


# Ledger -----------------------------------------------------------------------------

def ledger_id(*parts: Any) -> str:
    """'field_a', 'green_end', '2025/26' -> 'field_a.green_end.2025_26'."""
    return ".".join(str(p).replace("/", "_").replace(" ", "_") for p in parts)


class Ledger:
    """Records created by M5, each with the ten fields of plan 8.6."""

    def __init__(self) -> None:
        self.records: dict[str, dict] = {}

    def add(self, rid: str, **rec: Any) -> str:
        missing = [k for k in RECORD_FIELDS if k not in rec]
        extra = [k for k in rec if k not in RECORD_FIELDS]
        if missing or extra:
            raise PrecomputeError(f"ledger record {rid}: missing {missing}, unknown {extra}")
        if rec["evidence_class"] not in EVIDENCE_CLASSES:
            raise PrecomputeError(f"ledger record {rid}: evidence_class {rec['evidence_class']!r}")
        if rec["validation_state"] not in VALIDATION_STATES:
            raise PrecomputeError(f"ledger record {rid}: validation_state {rec['validation_state']!r}")
        if rid in self.records:
            raise PrecomputeError(f"ledger id {rid} twice")
        self.records[rid] = {"id": rid, **{k: rec[k] for k in RECORD_FIELDS}}
        return rid

    def num(self, rid: str, v: Any, **rec: Any) -> dict:
        self.add(rid, value=v, **rec)
        return {"v": v, "l": rid}


def is_num(x: Any) -> bool:
    return isinstance(x, Mapping) and set(x) == {"v", "l"} and isinstance(x["l"], str) and (
        x["v"] is None or (isinstance(x["v"], (int, float)) and not isinstance(x["v"], bool)))


def referenced_ids(doc: Any) -> set[str]:
    """Every ledger id a document names: Num ``l`` values and ``ledger`` keys."""
    out: set[str] = set()
    if isinstance(doc, Mapping):
        if is_num(doc):
            out.add(doc["l"])
            return out
        for k, v in doc.items():
            if k == "ledger" and isinstance(v, str):
                out.add(v)
            else:
                out |= referenced_ids(v)
    elif isinstance(doc, list):
        for v in doc:
            out |= referenced_ids(v)
    return out


def loose_numbers(doc: Any, path: str = "", covered: bool = False) -> list[str]:
    """Paths of numbers that are neither a Num nor inside an object with a ``ledger`` id."""
    if isinstance(doc, bool) or doc is None or isinstance(doc, str):
        return []
    if isinstance(doc, (int, float)):
        return [] if covered else [path]
    if isinstance(doc, Mapping):
        if is_num(doc):
            return []
        here = covered or isinstance(doc.get("ledger"), str)
        out = []
        for k, v in doc.items():
            if k in NOT_SCREEN_NUMBERS or k == "schema_version":
                continue
            out += loose_numbers(v, f"{path}.{k}" if path else k, here)
        return out
    if isinstance(doc, list):
        out = []
        for i, v in enumerate(doc):
            out += loose_numbers(v, f"{path}[{i}]", covered)
        return out
    return [path]


# Shape checks for blocks made by M2 and M4 ------------------------------------------

def _need(cond: bool, msg: str) -> None:
    if not cond:
        raise PrecomputeError(msg)


def _keys(obj: Any, keys: Sequence[str], where: str) -> None:
    _need(isinstance(obj, Mapping), f"{where} must be an object")
    missing = [k for k in keys if k not in obj]
    _need(not missing, f"{where} lacks {missing}")


def check_m2(m2: Mapping[str, Any], where: str) -> None:
    _keys(m2, ("polygon_px", "area_ha", "scale_note", "soil", "relief", "ndvi_series", "ribbon", "green_end"), where)
    px = m2["polygon_px"]
    _need(isinstance(px, list) and len(px) >= 3 and all(
        isinstance(p, list) and len(p) == 2 and all(isinstance(c, (int, float)) for c in p) for p in px),
        f"{where}.polygon_px must be at least 3 [x, y] points")
    _need(is_num(m2["area_ha"]), f"{where}.area_ha must be a Num")
    _keys(m2["scale_note"], SCALE_KEYS, f"{where}.scale_note")
    for k in SCALE_KEYS:
        _need(is_num(m2["scale_note"][k]), f"{where}.scale_note.{k} must be a Num")
    _keys(m2["soil"], ("source", "layers", "clay_q05_q95", "estimated_class", "zarc_ad_class", "transfer_warning",
                       "lab_report_request", "ledger"), f"{where}.soil")
    for k in ("estimated_class", "zarc_ad_class"):   # the page fills a sentence with them (7.2)
        _need(m2["soil"][k] is None or isinstance(m2["soil"][k], str), f"{where}.soil.{k} must be text or null")
    for i, layer in enumerate(m2["soil"]["layers"]):
        _keys(layer, ("top_m", "bottom_m", "clay_pct", "sand_pct", "om_pct"), f"{where}.soil.layers[{i}]")
    _keys(m2["relief"], ("slope_class", "lowspot_alert", "terrace_question", "ledger"), f"{where}.relief")
    _keys(m2["ndvi_series"], ("ledger", "points"), f"{where}.ndvi_series")
    for i, p in enumerate(m2["ndvi_series"]["points"]):
        _keys(p, ("date", "ndvi", "nbr2", "sensor", "n_clean", "clean_frac"), f"{where}.ndvi_series.points[{i}]")
    _keys(m2["ribbon"], ("ledger", "seasons"), f"{where}.ribbon")
    for i, s in enumerate(m2["ribbon"]["seasons"]):
        _keys(s, ("season", "cells", "n_obs", "longest_gap_days"), f"{where}.ribbon.seasons[{i}]")
        _need(len(s["cells"]) == 36 and all(c in RIBBON_CELLS for c in s["cells"]),
              f"{where}.ribbon.seasons[{i}].cells must be 36 of {RIBBON_CELLS}")
    for i, g in enumerate(m2["green_end"]):
        _keys(g, ("season", "low", "high", "gap_days", "status"), f"{where}.green_end[{i}]")
        _need(g["status"] in cal.GREEN_END_STATUSES, f"{where}.green_end[{i}].status {g['status']!r}")
        _need(is_num(g["gap_days"]), f"{where}.green_end[{i}].gap_days must be a Num")


def check_next_soy(ns: Mapping[str, Any], where: str) -> None:
    _keys(ns, ("state", "on_main_page", "water_note_on_how_we_know", "ledger"), where)


# region.json ------------------------------------------------------------------------

def _exact_number(x: Any, what: str) -> Fraction:
    _need(isinstance(x, (int, float)) and not isinstance(x, bool), f"{what} must be a number")
    return Fraction(str(x)) if isinstance(x, float) else Fraction(x)


def _as_json_number(x: Fraction) -> int | float:
    return int(x) if x.denominator == 1 else float(x)


def price_scenarios(example: Mapping[str, Any], k: Any, ledger: Ledger, lang: str = "en") -> list[dict]:
    """Three scenarios (14.3): corn price k lower, the typed base, corn price k higher.

    Moving every price together only moves the cost term, so the scenarios change the
    relation between prices. All values are the fictitious example ("example, fictitious").
    """
    _keys(example, PRICE_FIELDS, "example_numbers")
    kk = _exact_number(k, "price_k")
    _need(0 < kk < 1, "price_k must lie strictly between 0 and 1")
    factors = {"example_low": 1 - kk, "example_mid": Fraction(1), "example_high": 1 + kk}
    k_txt = f"{_as_json_number(kk * 100)}%"
    labels = {   # copy keys page.inputs.scenarios.*; "fictitious": true carries the example label
        "example_low": st.text("page.inputs.scenarios.low_note", lang).format(k=k_txt),
        "example_mid": st.text("page.inputs.scenarios.base", lang),
        "example_high": st.text("page.inputs.scenarios.high_note", lang).format(k=k_txt),
    }
    out = []
    for sid in SCENARIO_IDS:
        values = {}
        for f in PRICE_FIELDS:
            v = _exact_number(example[f], f)
            if f == "p_corn":
                v = v * factors[sid]
            values[f] = ledger.num(
                ledger_id("region", "price_scenarios", sid, f), _as_json_number(v),
                unit=_UNITS[f], evidence_class="your_numbers", source_id=None,
                transform="pipeline.decision.precompute.price_scenarios", method_id="M5-price-scenarios-v1",
                data_version=LABEL, uncertainty=None, validation_state="not_applicable",
                limits=None)
        out.append({"id": sid, "label": labels[sid], "values": values, "fictitious": True})
    return out


_UNITS = {"p_soy": "R$/sc", "p_corn": "R$/sc", "p_wheat": "R$/sc", "Y_corn": "sc/ha", "Y_wheat": "sc/ha",
          "c_corn": "R$/ha", "c_wheat": "R$/ha", "c_grass": "R$/ha"}


def noise_floor_num(inputs: Mapping[str, Any], ledger: Ledger, mode: str) -> dict:
    """``region.noise_floor``: N1 in days for the field order the browser computes (8.7, 13.7 rule 5).

    ``inputs["noise_floor"]`` is ``{"days": number or null, "data_version", "note"?}``, made by
    ``field_inputs.noise_floor`` from ``noise_floor.json`` through ``pipeline.bench.n1.evaluate``.
    Absent or null: the page says no field stands out (decision.mjs ``fieldOrder``).
    """
    nf = inputs.get("noise_floor")
    if nf is None:
        nf = {"days": None, "data_version": "not completed: no noise_floor.json"}
    _keys(nf, ("days", "data_version"), "noise_floor")
    days = nf["days"]
    _need(days is None or (isinstance(days, (int, float)) and not isinstance(days, bool) and days >= 0),
          "noise_floor.days must be a number of days, zero or more, or null")
    dv = _dv(nf, mode, "noise_floor")
    return ledger.num(
        "region.noise_floor", days, unit="days", evidence_class="estimated_from_satellite", source_id=HLS_SOURCE,
        transform="pipeline.bench.n1.evaluate", method_id="M4-N1-v1", data_version=dv, uncertainty=None,
        validation_state="not_applicable" if days is not None else "not_completed", limits=nf.get("note"))


def selection_file() -> Path:
    """The field selection written by ``pipeline.fields.select`` (protocol-v1.1). A function, so the
    tests can point it away from the working tree (late note, R2)."""
    return base.INTERIM_DIR / "fields_selection.json"


def choice_seasons(protocol: Mapping[str, Any], *, product_run: bool = False) -> list[str]:
    """``protocol.field.choice_seasons``: the seasons used to choose the fields, kept out of the order (D9).

    ``product_run`` (``--real`` without ``--rehearsal``): the seasons of the product field selection
    (``selection_file()``) are added too. The example and the rehearsal never read that file: the
    product selection does not leak into them (SA-D81)."""
    seasons = (protocol.get("field") or {}).get("choice_seasons") or []
    _need(isinstance(seasons, list) and all(isinstance(x, str) for x in seasons),
          "protocol.field.choice_seasons must be a list of seasons")
    out = list(seasons)
    if not product_run:
        return out
    # protocol-v1.1: a season used by the fallback of the field choice also stays out of the order.
    sel = selection_file()
    if sel.exists():
        used = (json.loads(sel.read_text(encoding="utf-8")).get("choice_seasons") or [])
        out += [s for s in used if isinstance(s, str) and s not in out]
    return out


NASA_SWITCH_OTHERWISE = ("how_we_know",)


def nasa_switch_rule(protocol: Mapping[str, Any], ledger: Ledger) -> dict:
    """``protocol.nasa_switch`` (SA-D66): the NASA switch goes on the main page only when, on
    the fields shown, it changes at least ``main_page_if_states_changed_min`` states. The browser
    counts them (app.mjs ``switchPlacement``). Otherwise How we know shows the result."""
    rule = protocol.get("nasa_switch")
    _need(isinstance(rule, Mapping), "protocol.nasa_switch is missing (SA-D66)")
    _keys(rule, ("main_page_if_states_changed_min", "otherwise"), "protocol.nasa_switch")
    n = rule["main_page_if_states_changed_min"]
    _need(isinstance(n, int) and not isinstance(n, bool) and n >= 0,
          "protocol.nasa_switch.main_page_if_states_changed_min must be a whole number, zero or more")
    _need(rule["otherwise"] in NASA_SWITCH_OTHERWISE,
          f"protocol.nasa_switch.otherwise must be one of {NASA_SWITCH_OTHERWISE}")
    return {
        "main_page_if_states_changed_min": ledger.num(
            "region.nasa_switch.main_page_if_states_changed_min", n, unit="states", evidence_class="unknown",
            source_id=None, transform="pipeline.decision.precompute.nasa_switch_rule",
            method_id="M5-nasa-switch-rule-v1", data_version="protocol.nasa_switch (SA-D66)", uncertainty=None,
            validation_state="not_applicable", limits=None),
        "otherwise": rule["otherwise"],
    }


def region_doc(inputs: Mapping[str, Any], *, config: Mapping[str, Any], hard_removes: Mapping[str, list[str]],
               ledger: Ledger, lang: str = "en", mode: str = EXAMPLE, product_run: bool = False) -> dict:
    """region.json. Texts come from config/copy_<lang>.yaml through the ids of strategies.py:
    the copy files own every user-facing sentence. ``noise_floor`` and ``choice_seasons`` are
    the inputs of the field order, which decision.mjs computes in the browser (8.7).
    ``product_run``: see ``choice_seasons``."""
    r = inputs["region"]
    _keys(r, ("trial_sites", "pack", "units", "price_k", "example_numbers"), "region")
    pack = r["pack"]
    _keys(pack, ("decision", "strategies", "calendar", "trials", "reviewer"), "region.pack")
    _need(all(isinstance(pack[k], bool) for k in pack), "region.pack values must be true or false")
    # Items 1 to 4 turn the comparator on. Without item 5 it stays on and says so (5.6, 8.8).
    pack_mode = "decision" if all(pack[k] for k in ("decision", "strategies", "calendar", "trials")) else "observation"
    sites = list(r["trial_sites"])
    cfg_sites = [s["name"] for s in config.get("trial_sites", [])]
    _need([_fold(s) for s in sites] == [_fold(s) for s in cfg_sites],
          f"trial_sites {sites} do not match the config {cfg_sites}")
    units = r["units"]
    _keys(units, ("bag_kg", "alqueire_ha", "screen_unit"), "region.units")
    unit_rec = dict(evidence_class="unknown", source_id=None, transform="pipeline.decision.precompute.region_doc",
                    method_id="M5-units-v1", data_version="plan 14.2 and 7.15", uncertainty=None,
                    validation_state="not_applicable", limits=None)
    st.check_menu()
    return {
        "schema_version": SCHEMA_VERSION,
        "region_id": config["region"],
        "mode": pack_mode,
        "trial_sites": sites,
        "pack": {k: pack[k] for k in ("decision", "strategies", "calendar", "trials", "reviewer")},
        "strategies": [s.region_entry(lang) for s in st.STRATEGIES.values()],
        "rules": {"hard": st.hard_rules(hard_removes, lang), "soft": st.soft_rules(lang)},
        "constraints": st.constraints(lang),
        "alerts": st.alerts(),
        "units": {
            "bag_kg": ledger.num("region.units.bag_kg", units["bag_kg"], unit="kg", **unit_rec),
            "alqueire_ha": ledger.num("region.units.alqueire_ha", units["alqueire_ha"], unit="ha", **unit_rec),
            "screen_unit": units["screen_unit"],
        },
        "price_scenarios": price_scenarios(r["example_numbers"], r["price_k"], ledger, lang),
        "noise_floor": noise_floor_num(inputs, ledger, mode),
        "choice_seasons": choice_seasons(config["protocol"], product_run=product_run),
        "nasa_switch": nasa_switch_rule(config["protocol"], ledger),
    }


def _fold(s: str) -> str:
    import unicodedata
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c)).lower()


# trials_steps.json ----------------------------------------------------------------

def check_steps(steps: Sequence[Mapping[str, Any]], n: int, where: str) -> list[dict]:
    """Integer steps of 1 sc/ha, g increasing, count not increasing, 0 <= count <= n (14.5)."""
    out = []
    for i, s in enumerate(steps):
        _keys(s, ("g_sc_ha", "count"), f"{where}[{i}]")
        g, c = s["g_sc_ha"], s["count"]
        _need(isinstance(g, int) and not isinstance(g, bool) and isinstance(c, int) and not isinstance(c, bool),
              f"{where}[{i}] must hold integers")
        _need(0 <= c <= n, f"{where}[{i}].count must lie in 0..{n}")
        out.append({"g_sc_ha": g, "count": c})
    for a, b in zip(out, out[1:]):
        _need(b["g_sc_ha"] > a["g_sc_ha"], f"{where}: g_sc_ha must increase")
        _need(b["count"] <= a["count"], f"{where}: count cannot grow with g")
    return out


def steps_from_gains(gains_kg_ha: Sequence[Any], *, bag_kg: Any = 60) -> list[dict]:
    """Step table from per-site-season gains in kg/ha: count of gains >= g sc/ha, g = 1, 2, ...

    The table stops at the first step with count 0, so decision.mjs pins every larger G.
    Exact arithmetic. Run by the bench on the transcribed trials; tested here only on
    synthetic gains (protocol integrity).
    """
    bag = _exact_number(bag_kg, "bag_kg")
    _need(bag > 0, "bag_kg must be positive")
    sc = [(x if isinstance(x, Fraction) else _exact_number(x, "gain")) / bag for x in gains_kg_ha]
    out, g = [], 1
    while True:
        count = sum(1 for x in sc if x >= g)
        out.append({"g_sc_ha": g, "count": count})
        if count == 0:
            return out
        g += 1


def _finite(x: Any, what: str) -> int | float:
    _need(isinstance(x, (int, float)) and not isinstance(x, bool) and x == x and x not in (float("inf"), float("-inf")),
          f"{what} must be a finite number")
    return x


def summary_block(opt: str, e: Mapping[str, Any], ledger: Ledger, *, bag_kg: Any, mode: str) -> dict | None:
    """The numbers and texts of the L3 line of ``opt`` (copy page.rows.L3.<opt>), each number a Num.

    Inputs: ``e["summary"]``, ``{key: number or text}`` with the keys of ``SUMMARY_NUMBERS`` and
    ``SUMMARY_TEXTS``. Made by the bench from the transcribed trials, never here. S3 also gets
    ``bags`` = ``delta`` / ``bag_kg``, exact. Absent: null, and the page leaves the line out.
    """
    sm = e.get("summary")
    if sm is None:
        return None
    where = f"trials.options.{opt}.summary"
    _need(isinstance(sm, Mapping), f"{where} must be an object")
    nums, texts = SUMMARY_NUMBERS[opt], SUMMARY_TEXTS[opt]
    unknown = sorted(set(sm) - set(nums) - set(texts))
    _need(not unknown, f"{where} has unknown keys {unknown}")
    src = ",".join(e["source_ids"])
    dv = _dv(e, mode, f"trials.options.{opt}")
    rec = dict(source_id=src, transform="pipeline.decision.precompute.summary_block", method_id="M5-L3-summary-v1",
               data_version=dv, uncertainty=None, validation_state="not_applicable", limits=None)
    out: dict[str, Any] = {}
    for k in texts:
        if k in sm:
            _need(isinstance(sm[k], str) and sm[k].strip(), f"{where}.{k} must be text")
            out[k] = sm[k]
    for k, (unit, cls) in nums.items():
        if k not in sm:
            continue
        v = _finite(sm[k], f"{where}.{k}")
        rid = SUMMARY_IDS.get((opt, k), ledger_id("trials", opt, "summary", k))
        extra = {"transform": "pipeline.bench.trials", "method_id": "M4-mean-delta-v1"} if rid.endswith("mean_delta") else {}
        out[k] = ledger.num(rid, v, unit=unit, evidence_class=cls, **{**rec, **extra})
    counts = [sm[k] for k in SUMMARY_COUNTS if k in nums and k in sm]
    for c in counts:
        _need(isinstance(c, int) and c >= 0, f"{where}: pos and neg are counts of site-seasons")
    _need(sum(counts) <= e["n"], f"{where}: pos + neg exceed n = {e['n']}")
    if opt == "S3" and "delta" in sm:
        bag = _exact_number(bag_kg, "bag_kg")
        _need(bag > 0, "bag_kg must be positive")
        out["bags"] = ledger.num(ledger_id("trials", opt, "summary", "bags"),
                                 _as_json_number(_exact_number(sm["delta"], "delta") / bag), unit="sc/ha",
                                 evidence_class="derived_from_trials", **rec)
    out["ledger"] = ledger.add(ledger_id("trials", opt, "summary"), value=None, unit="L3 line: text and numbers",
                               evidence_class=e["evidence_class"], **rec)
    return out


def reference_block(t: Mapping[str, Any], ledger: Ledger, mode: str) -> dict | None:
    """The L3 line of S1 (copy page.rows.L3.S1): the bad year of the reference and its source."""
    ref = t.get("reference")
    if ref is None:
        return None
    _keys(ref, (*REFERENCE_TEXTS, "source_ids", "evidence_class"), "trials.reference")
    for k in REFERENCE_TEXTS:
        _need(isinstance(ref[k], str) and ref[k].strip(), f"trials.reference.{k} must be text")
    rid = ledger.add(
        "trials.reference", value=None, unit="text", evidence_class=ref["evidence_class"],
        source_id=",".join(ref["source_ids"]), transform="pipeline.decision.precompute.reference_block",
        method_id="M5-L3-reference-v1", data_version=_dv(ref, mode, "trials.reference"), uncertainty=None,
        validation_state="not_applicable", limits=None)
    return {**{k: ref[k] for k in REFERENCE_TEXTS}, "ledger": rid}


def by_method_block(methods: Any, n: int, where: str) -> list[dict]:
    """S2 per establishment method (protocol 11): ``[{method, n, sites, steps}]``, the n adding up.

    Absent in the inputs: absent in trials_steps.json, and decision.mjs gives S2 "Not enough
    evidence" on a verdict from the counts (``method_missing``).
    """
    _need(isinstance(methods, list) and methods, f"{where} must be a non-empty list")
    out, seen = [], set()
    for i, m in enumerate(methods):
        _keys(m, ("method", "n", "sites", "steps"), f"{where}[{i}]")
        _need(isinstance(m["method"], str) and m["method"].strip() and m["method"] not in seen,
              f"{where}[{i}].method must be a new non-empty id")
        seen.add(m["method"])
        k = m["n"]
        _need(isinstance(k, int) and not isinstance(k, bool) and k >= 0, f"{where}[{i}].n")
        _need(isinstance(m["sites"], list) and all(isinstance(x, str) for x in m["sites"]), f"{where}[{i}].sites")
        out.append({"method": m["method"], "n": k, "sites": list(m["sites"]),
                    "steps": check_steps(m["steps"], k, f"{where}[{i}].steps")})
    _need(sum(m["n"] for m in out) == n, f"{where}: the counts per method do not add up to n = {n}")
    return out


def trials_steps_doc(t: Mapping[str, Any], ledger: Ledger, *, bag_kg: Any = 60, mode: str = EXAMPLE) -> dict:
    _keys(t, ("rights_note", "options", "money_refs"), "trials")
    options = {}
    for opt in st.OPTION_IDS:
        e = t["options"][opt]
        _keys(e, ("n", "n_sites", "condition", "transfer_warning", "steps", "bad_year_text", "source_ids",
                  "evidence_class"), f"trials.options.{opt}")
        n = e["n"]
        _need(isinstance(n, int) and not isinstance(n, bool) and n >= 0, f"trials.options.{opt}.n")
        steps = check_steps(e["steps"], n, f"trials.options.{opt}.steps")
        dv = _dv(e, mode, f"trials.options.{opt}")
        rid = ledger.add(
            ledger_id("trials", opt), value=None, unit="site-seasons",
            evidence_class=e["evidence_class"], source_id=",".join(e["source_ids"]),
            transform="pipeline.decision.precompute.trials_steps_doc", method_id="M5-steps-v1",
            data_version=dv, uncertainty=None, validation_state="not_applicable",
            limits=None)
        bad = ledger.add(
            ledger_id("trials", opt, "bad_year"), value=None, unit="text",
            evidence_class="measured_in_trials", source_id=",".join(e["source_ids"]),
            transform="pipeline.decision.precompute.trials_steps_doc", method_id="M5-bad-year-v1",
            data_version=dv, uncertainty=None, validation_state="not_applicable",
            limits=None)
        options[opt] = {
            "n": n, "n_sites": e["n_sites"], "condition": e["condition"], "transfer_warning": e["transfer_warning"],
            "steps": steps, "bad_year": {"text": e["bad_year_text"], "ledger": bad},
            "summary": summary_block(opt, e, ledger, bag_kg=bag_kg, mode=mode), "ledger": rid,
        }
        if opt == "S2" and "by_method" in e:
            options[opt]["by_method"] = by_method_block(e["by_method"], n, f"trials.options.{opt}.by_method")
    refs = []
    for m in t["money_refs"]:
        _keys(m, ("id", "text", "price_basis", "source_id", "evidence_class"), "trials.money_refs[]")
        rid = ledger.add(
            ledger_id("trials", "money_refs", m["id"]), value=None, unit="R$/ha", evidence_class=m["evidence_class"],
            source_id=m["source_id"], transform="pipeline.decision.precompute.trials_steps_doc",
            method_id="M5-money-refs-v1", data_version=_dv(m, mode, "trials.money_refs[]"), uncertainty=None,
            validation_state="not_applicable", limits=None)
        refs.append({"id": m["id"], "text": m["text"], "price_basis": m["price_basis"], "ledger": rid})
    return {"schema_version": SCHEMA_VERSION, "rights_note": t["rights_note"], "reference": reference_block(t, ledger, mode),
            "options": options, "money_refs": refs}


# field_<id>.json ----------------------------------------------------------------------

def field_doc(f: Mapping[str, Any], *, inputs: Mapping[str, Any], protocol: Mapping[str, Any],
              zarc_rows: pd.DataFrame, daily: pd.DataFrame, ledger: Ledger, mode: str = EXAMPLE) -> tuple[dict, dict]:
    """field_<id>.json and the per-field input of run_states.mjs.

    No field order here: decision.mjs ``fieldOrder`` computes it in the browser from the
    ``green_end`` of every field and region.json ``noise_floor`` and ``choice_seasons`` (8.7).
    """
    fid = f["field_id"]
    prefix = f"field_{fid}"
    _keys(f, ("field_id", "criterion", "fictional_persona_note", "municipio", "m2"), prefix)
    m2 = f["m2"]
    check_m2(m2, f"{prefix}.m2")
    season, safra = inputs["season"], inputs["current_safra"]
    where = {"municipio": f["municipio"], "zarc_ad_class": m2["soil"]["zarc_ad_class"]}
    kw = dict(field=where, zarc_rows=zarc_rows, cycles=inputs["cycles"], protocol=protocol, season=season,
              current_safra=safra)
    wins = {sid: cal.autumn_window(sid, **kw) for sid in st.STRATEGY_IDS}
    cols = cal.columns(season=season, field=where, zarc_rows=zarc_rows, cycles=inputs["cycles"],
                       occupancy_specs=inputs["occupancy"], protocol=protocol, current_safra=safra)

    # Window reading (12.3, M5.7). Without a soil report: the envelope of every class (SA-D64).
    wr = cal.field_window_reading(m2["green_end"], zarc_rows, crop=st.strategy("S1").zarc_crop,
                                  municipio=f["municipio"], soil_class=where["zarc_ad_class"],
                                  table_safra=safra, season=season, protocol=protocol)
    basis = cal.window_basis(m2["green_end"], zarc_rows, municipio=f["municipio"], table_safra=safra,
                             season=season, protocol=protocol)
    wr_id = ledger.add(
        ledger_id(prefix, "window_reading"), value=None, unit="seasons", evidence_class="estimated_from_satellite",
        source_id="hls_vi_v2,zarc_tabua", transform="pipeline.decision.calendar.field_window_reading",
        method_id="M5-window-v2", data_version=f"Tábua {safra}; ribbon {_span(protocol)}",
        uncertainty={"kind": "event_interval", "low": None, "high": None}, validation_state="not_applicable",
        limits=("for any soil class: the latest end of the corn window across every AD class and cycle group "
                "of the municipality" if wr["basis"] == cal.BASIS_ANY else None))
    # SA-D72: the Zarc risk band above the ribbon, the risk at exit and "ended before". The browser
    # counts the last two from window_basis for the class in use (calendar.*_from_basis, tested).
    band_id = ledger.add(
        ledger_id(prefix, "zarc_risk_band"), value=None, unit="% risk per ten-day period",
        evidence_class="official_rule", source_id="zarc_tabua", transform="pipeline.decision.calendar.risk_band",
        method_id="M5-zarc-band-v1", data_version=f"Tábua {safra}", uncertainty={"kind": "not_quantified"},
        validation_state="not_applicable",
        limits="Zarc risk for sowing in each ten-day period. A blank period is outside the zoning, never zero risk.")
    for sid in ("S1", "S2"):
        basis[sid]["risk_band"]["ledger"] = band_id
    exit_kw = dict(value=None, unit="seasons", evidence_class="estimated_from_satellite",
                   source_id="hls_vi_v2,zarc_tabua", data_version=f"Tábua {safra}; ribbon {_span(protocol)}",
                   uncertainty={"kind": "event_interval", "low": None, "high": None}, validation_state="not_applicable")
    rx_id = ledger.add(ledger_id(prefix, "risk_at_exit"), transform="pipeline.decision.calendar.risk_at_exit_from_basis",
                       method_id="M5-risk-at-exit-v1", limits="A condition of the field being free, never a sowing date.",
                       **exit_kw)
    eb_id = ledger.add(ledger_id(prefix, "ended_before"), transform="pipeline.decision.calendar.ended_before_from_basis",
                       method_id="M5-window-v2",
                       limits=("Without a soil report: the earliest end of the corn window across every AD class "
                               "and cycle group of the municipality (SA-D84). Never says that sowing fell inside "
                               "the window."), **exit_kw)
    basis["risk_at_exit"] = {"ledger": rx_id}
    basis["ended_before"] = {"ledger": eb_id}

    # L2 risk (12.2, 10.12). Dry autumn and cold in the window had no sourced threshold (SA-D68) and
    # are replaced by the climate block (SA-D72.5): the settings in inputs["risk"] are no longer read.
    years = rc.climate_years(protocol)
    _need(isinstance(inputs["risk"], Mapping), "risk must be an object")
    climate, clim_id = climate_block(prefix, inputs["occupancy"], protocol, daily, ledger)
    risk_id = ledger.add(
        ledger_id(prefix, "risk"), value=None, unit="years",
        evidence_class="reanalysis", source_id="power_daily,zarc_tabua", transform="pipeline.decision.risk_counts",
        method_id="M5-risk-counts-v1", data_version=f"POWER {years.start} to {years.stop - 1}; Tábua {safra}",
        uncertainty=None, validation_state="not_applicable", limits=None)
    zarc_class = {}
    for sid, w in wins.items():
        state = (cal.window_gap_state(where) if not w["windows"]
                 else ("zarc_previous_table" if w["previous_table"] else "zarc_window"))
        zarc_class[sid] = {"crop": w["crop"], "table_safra": w["table_safra"], "risk_min": w["risk_min"],
                           "risk_max": w["risk_max"], "state": state,
                           # SA-D72.6: each class, and the range across classes when there is no report.
                           **cal.class_risk_ranges(zarc_rows, crop=w["crop"], municipio=f["municipio"],
                                                   table_safra=w["table_safra"], season=season)}

    # Erosivity (10.9).
    e = inputs["erosivity"]
    er = ero.erosivity(cols, e["monthly_r"], similar_within_pct_points=protocol["erosion"]["similar_within_pct_points"])
    er_id = ledger.add(
        ledger_id(prefix, "erosivity"), value={k: ero.pct(v) for k, v in er["shares"].items()},
        unit="% of annual R", evidence_class="published_climatology",
        source_id=ero.SOURCE_ID, transform="pipeline.decision.erosivity.erosivity", method_id="M5-erosivity-v1",
        data_version=_dv(e, mode, "erosivity"), uncertainty=None, validation_state="not_applicable",
        limits=None)

    ns = f.get("next_soy") or {"state": "not_completed", "on_main_page": False, "water_note_on_how_we_know": False,
                               "ledger": None}
    if ns.get("ledger") is None:
        ns = {**ns, "ledger": ledger.add(
            ledger_id(prefix, "next_soy"), value=None, unit="state", evidence_class="simulated", source_id=None,
            transform="pipeline.bench.label", method_id="M5-next-soy-v1",
            data_version=EXAMPLE if mode == EXAMPLE else "not completed: the bench label is not frozen",
            uncertainty=None, validation_state="not_completed", limits=None)}
    check_next_soy(ns, f"{prefix}.next_soy")

    doc = {
        "schema_version": SCHEMA_VERSION,
        "field_id": fid,
        "criterion": f["criterion"],
        "fictional_persona_note": f["fictional_persona_note"],
        "polygon_px": m2["polygon_px"],
        "area_ha": m2["area_ha"],
        "scale_note": m2["scale_note"],
        "soil": m2["soil"],
        "relief": m2["relief"],
        "ndvi_series": m2["ndvi_series"],
        "ribbon": m2["ribbon"],
        "green_end": [{k: g[k] for k in GREEN_END_KEYS} for g in m2["green_end"]],
        "window_reading": {**wr, "ledger": wr_id},
        "window_basis": {**basis, "ledger": wr_id},
        "ribbon_counts": {"no_green": no_green_block(prefix, m2["ribbon"], protocol, ledger)},
        "risk": {"zarc_class": zarc_class,
                 "climate": {**climate, "ledger": clim_id},
                 "ledger": risk_id},
        "calendar": cols,
        "erosivity": {**er["classes"], "ledger": er_id},
        "next_soy": ns,
    }
    states_in = {
        "field_id": fid,
        "windowReading": {"state": wr["state"], "k": wr["k"], "n": wr["n"], "basis": wr["basis"]},
        "zarcWindow": cal.decision_windows(wins["S1"], wins["S2"]),
    }
    if mode == REAL:
        states_in["origin"] = REAL_ORIGIN       # run_states.mjs refuses a real label without it
    return doc, states_in


def climate_block(prefix: str, occupancy: Mapping[str, Any], protocol: Mapping[str, Any], daily: pd.DataFrame,
                  ledger: Ledger) -> tuple[dict, str]:
    """Frost and rain while the crop that follows the soybean occupies the field (SA-D72.5).

    One entry per strategy: corn in S1, corn with grass in S2, wheat in S3, over their occupation
    dates (``occupancy``, Embrapa Doc 476 medians) in ``protocol.climate.years``. Descriptive counts
    of the past (``pipeline.decision.risk_counts.occupation_climate``), never a forecast.
    """
    cl = protocol.get("climate")
    _need(isinstance(cl, Mapping), "protocol.climate is missing")
    _need(cl["frost"]["variable"] == rc.TMIN and cl["rain"]["variable"] == rc.RAIN,
          "protocol.climate names other POWER variables than risk_counts reads")
    cyears = rc.protocol_years(protocol)
    below = cl["frost"]["below_c"]
    out = {}
    for sid in st.STRATEGY_IDS:
        crop = st.strategy(sid).autumn_crop
        phases = [p for p in occupancy[sid]["phases"] if p["crop"] == crop]
        _need(len(phases) == 1, f"occupancy.{sid}: one {crop} phase expected, got {len(phases)}")
        out[sid] = {"crop": crop, **rc.occupation_climate(daily, start=phases[0]["start"], end=phases[0]["end"],
                                                          years=cyears, frost_below_c=below)}
    left = sorted({s for v in out.values() for s in v["left_out"]})
    rid = ledger.add(
        ledger_id(prefix, "climate"), value=None, unit="seasons (frost, k of n) and mm (rain total per season)",
        evidence_class="reanalysis", source_id=f"{rc.SOURCE_ID},embrapa_doc476,zarc_frost_2026_27",
        transform="pipeline.decision.risk_counts.occupation_climate", method_id="M5-climate-occupation-v1",
        data_version=f"POWER {cyears[0]} to {cyears[1]}, whole occupation periods only",
        uncertainty=None, validation_state="not_applicable",
        limits=("Counts of past seasons in one POWER cell, not a forecast and not a threshold of failure."
                + (f" Left out for a missing day: {', '.join(left)}." if left else "")))
    return out, rid


def no_green_block(prefix: str, ribbon: Mapping[str, Any], protocol: Mapping[str, Any], ledger: Ledger) -> dict:
    """Ten-day periods with no green seen, median over the seasons of the ribbon (SA-D72.4)."""
    _need(isinstance((protocol.get("ribbon_counts") or {}).get("no_green"), str), "protocol.ribbon_counts.no_green is missing")
    ng = rb.no_green_count(ribbon["seasons"])
    rid = ledger.add(
        ledger_id(prefix, "ribbon_counts", "no_green"), value=ng["median"],
        unit="ten-day periods of 36 with no green seen, median over the seasons", evidence_class="estimated_from_satellite",
        source_id="hls_vi_v2", transform="pipeline.fields.ribbon.no_green_count", method_id="M5-no-green-v1",
        data_version=f"ribbon {_span(protocol)}", uncertainty=None, validation_state="not_applicable",
        limits="No green is not bare soil: NDVI cannot separate straw from soil. Periods with no clean image are not counted.")
    return {**ng, "ledger": rid}


def _span(protocol: Mapping[str, Any]) -> str:
    s = (protocol.get("dates") or {}).get("ribbon_seasons") or ["?", "?"]
    return f"{s[0]} to {s[-1]}"


# ablation.json ------------------------------------------------------------------------

def run_states(payload: Mapping[str, Any], *, node: str | None = None, timeout: float = 120) -> dict:
    """Call run_states.mjs (which imports site/js/decision.mjs) on ``payload``."""
    exe = node or shutil.which("node")
    if not exe:
        raise FileNotFoundError("node not found")
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "states_input.json"
        p.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        res = subprocess.run([exe, str(RUN_STATES), str(p)], capture_output=True, text=True, timeout=timeout,
                             cwd=base.REPO_ROOT)
    if res.returncode != 0:
        raise RuntimeError(f"run_states.mjs failed: {res.stderr.strip()}")
    return json.loads(res.stdout)


HLS_SOURCE = "hls_vi_v2"
HLS_SWAP = "without_hls_no_window_warning_no_field_order"      # a code: the page text is copy
NASADEM_SWAP = "by_construction_relief_changes_no_state"


def a3_result(states: Mapping[str, Any], protocol: Mapping[str, Any]) -> a3.A3Result:
    """Count the run_states.mjs states with ``pipeline.bench.ablation.evaluate_a3``: one count, one place."""
    with_hls: dict[str, dict] = {}
    without_hls: dict[str, dict] = {}
    for s in states["states"]:
        key = (s["field_id"], s["option"])
        with_hls.setdefault(s["scenario"], {})[key] = s["hls_on"]["state"]
        without_hls.setdefault(s["scenario"], {})[key] = s["hls_off"]["state"]
    res = a3.evaluate_a3(with_hls, without_hls, a3.A3Rule.from_protocol(protocol), source=HLS_SOURCE,
                         swap=HLS_SWAP, expected_scenarios=list(SCENARIO_IDS))
    _need(res.states_changed == states["states_changed"] and res.states_total == states["states_total"],
          "run_states.mjs and the bench count differ")
    return res


def ablation_doc(states: Mapping[str, Any] | None, ledger: Ledger, protocol: Mapping[str, Any],
                 mode: str = EXAMPLE) -> dict:
    """A3 (8.7, 11.2): states that change when HLS is off, in the three example price scenarios.

    Example run: the fictitious fields. Real run: the real fields, with the same example prices.

    Statuses follow the bench: "reported" when counted, "not_completed" on an
    infrastructure failure (never a fail). Zero changes: "context_only". NASADEM changes
    no state and no field order: "context_only" by construction, not run.
    """
    rec = dict(evidence_class="derived_from_trials", transform="pipeline/decision/run_states.mjs",
               method_id="M5-A3-v1",
               data_version=EXAMPLE if mode == EXAMPLE else "real fields from the M2 outputs, example price scenarios",
               uncertainty=None, validation_state="not_applicable", limits=None)
    if states is None:
        hls = {**a3.not_run_source(HLS_SOURCE, HLS_SWAP), "status": NOT_COMPLETED}
    else:
        hls = a3_result(states, protocol).ablation_source()
    for k in ("states_changed", "states_total"):
        hls[k] = ledger.num(ledger_id("ablation", HLS_SOURCE, k), hls[k], unit="states", source_id=HLS_SOURCE, **rec)
    nasadem = {**a3.not_run_source("nasadem", NASADEM_SWAP), "verdict": a3.CONTEXT_ONLY}
    for k in ("states_changed", "states_total"):
        nasadem[k] = ledger.num(ledger_id("ablation", "nasadem", k), None, unit="states", source_id="nasadem", **rec)
    return {"schema_version": SCHEMA_VERSION, "scenarios": list(SCENARIO_IDS), "sources": [hls, nasadem]}


# Build ---------------------------------------------------------------------------------

def zarc_rows_frame(rows: Sequence[Mapping[str, Any]]) -> pd.DataFrame:
    """Synthetic Tábua rows ({..., "dec": [36 values]}) in the columns of the decoded interim CSV."""
    recs = []
    for r in rows:
        _need(len(r["dec"]) == 36, "a Tábua row needs 36 dekads")
        recs.append({**{k: r[k] for k in ("Nome_cultura", "municipio", "ciclo", "solo", "table_safra", "table_role")},
                     **{c: int(v) for c, v in zip(zarc.DEC_COLS, r["dec"])}})
    return pd.DataFrame(recs)


def mode_of(inputs: Mapping[str, Any], expected: str | None = None) -> str:
    """The run: ``EXAMPLE`` or ``REAL``, from the inputs label. ``expected`` must agree (the CLI ``--real``)."""
    label = inputs.get("label")
    _need(label in LABELS, f'inputs must be labelled "{EXAMPLE}" (synthetic) or "{REAL}" '
                           f"(assembled by {ASSEMBLER}), got {label!r}")
    if expected is not None:
        _need(label == expected, f"inputs labelled {label!r}, the run asked for {expected!r} (--real for real inputs)")
    return label


def _dv(block: Mapping[str, Any], mode: str, where: str) -> str:
    """``data_version`` of an input block: required in a real run, the example label otherwise."""
    dv = block.get("data_version")
    if mode == REAL:
        _need(isinstance(dv, str) and bool(dv.strip()), f"{where}.data_version is required in a real run")
        return dv
    return dv or EXAMPLE


def _strings(node: Any, path: str = ""):
    if isinstance(node, str):
        yield path, node
    elif isinstance(node, Mapping):
        for k, v in node.items():
            yield from _strings(v, f"{path}.{k}" if path else str(k))
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from _strings(v, f"{path}[{i}]")


def _source_ids(node: Any) -> list[str]:
    out = []
    if isinstance(node, Mapping):
        for k, v in node.items():
            if k in ("source_id", "source_ids"):
                out += [v] if isinstance(v, str) else [x for x in (v or []) if isinstance(x, str)]
            else:
                out += _source_ids(v)
    elif isinstance(node, list):
        for v in node:
            out += _source_ids(v)
    return out


def provenance_problems(prov: Any, where: str) -> list[str]:
    """Problems of the provenance ``field_inputs`` stamps on a real field: label, assembler, files and sha256."""
    if not isinstance(prov, Mapping):
        return [f"{where}: no provenance, a real field comes from {ASSEMBLER}"]
    out = []
    if prov.get("label") != REAL or prov.get("assembler") != ASSEMBLER:
        out.append(f"{where}: provenance must say label {REAL!r} and assembler {ASSEMBLER!r}")
    files = prov.get("files")
    if not isinstance(files, list) or not files:
        return out + [f"{where}: provenance lists no M2 file"]
    for f in files:
        p = Path(f.get("path", "")) if isinstance(f, Mapping) else Path("")
        p = p if p.is_absolute() else base.REPO_ROOT / p
        if not p.is_file():
            out.append(f"{where}: M2 file {f!r} is missing")
        elif hashlib.sha256(p.read_bytes()).hexdigest() != f.get("sha256"):
            out.append(f"{where}: M2 file {p.name} changed after it was assembled (sha256)")
    return out


def check_inputs_label(inputs: Mapping[str, Any], mode: str) -> None:
    """Real data never carries the fictitious label, and fictitious data never passes as real.

    Real run: every field has the provenance of ``field_inputs`` with its files unchanged; no
    string anywhere says "example, fictitious"; no source id starts with "example"; no
    synthetic Tábua rows. Example run: no field carries a real provenance.
    """
    problems = []
    fields = inputs.get("fields") or []
    if mode == REAL:
        for i, f in enumerate(fields):
            problems += provenance_problems((f.get("m2") or {}).get("provenance"), f"fields[{i}]")
        problems += [f"{p}: carries the label {EXAMPLE!r}" for p, x in _strings(inputs) if EXAMPLE in x.casefold()]
        problems += [f"source {s!r} is an example source" for s in sorted(set(_source_ids(inputs)))
                     if s.casefold().startswith("example")]
        if inputs.get("zarc_rows"):
            problems.append("zarc_rows are synthetic Tábua rows: a real run reads the decoded Tábua (--zarc)")
        if not rh.is_rehearsal(inputs):
            problems += [f"source {rh.TRIALS_SOURCE_ID!r}: fictitious rehearsal trials in a product run"
                         for s in sorted(set(_source_ids(inputs))) if s == rh.TRIALS_SOURCE_ID]
    else:
        for i, f in enumerate(fields):
            prov = (f.get("m2") or {}).get("provenance")
            if isinstance(prov, Mapping) and prov.get("label") == REAL:
                problems.append(f"fields[{i}]: real M2 outputs cannot be labelled {EXAMPLE!r}")
    _need(not problems, "; ".join(problems[:6]) + (f" (and {len(problems) - 6} more)" if len(problems) > 6 else ""))


def build(inputs: Mapping[str, Any], *, config: Mapping[str, Any], zarc_rows: pd.DataFrame, daily: pd.DataFrame,
          states_runner=run_states, mode: str | None = None, rehearsal: bool = False) -> dict:
    """All documents, the M5 ledger records and the A3 states. Pure apart from ``states_runner``
    and, in a real run, reading the M2 files named in the provenance.

    ``rehearsal``: the inputs must be real and stamped ``run_mode: rehearsal``; so is every output.
    Without it, stamped inputs are refused."""
    protocol = config["protocol"]
    mode = mode_of(inputs, mode)
    try:
        rh.check_mode(inputs, rehearsal, "the precompute inputs")
    except rh.RehearsalError as exc:
        raise PrecomputeError(str(exc)) from exc
    _need(not rehearsal or mode == REAL, "a rehearsal reads real NASA data: run it with --real")
    check_inputs_label(inputs, mode)
    ledger = Ledger()
    hard = cal.hard_rule_breaks(inputs["occupancy"], inputs["season"])
    region = region_doc(inputs, config=config, hard_removes=hard, ledger=ledger, mode=mode,
                        product_run=mode == REAL and not rehearsal)
    trials = trials_steps_doc(inputs["trials"], ledger, bag_kg=inputs["region"]["units"]["bag_kg"], mode=mode)

    docs: dict[str, dict] = {"region.json": region, "trials_steps.json": trials}
    fields_in = []
    ids = [f["field_id"] for f in inputs["fields"]]
    _need(len(ids) == len(set(ids)) and ids, "field ids must be unique and present")
    for f in inputs["fields"]:
        doc, sin = field_doc(f, inputs=inputs, protocol=protocol, zarc_rows=zarc_rows, daily=daily, ledger=ledger,
                             mode=mode)
        docs[f"field_{f['field_id']}.json"] = doc
        fields_in.append(sin)

    payload = {
        "label": mode,
        "fields": fields_in,
        "scenarios": [{"id": s["id"], "numbers": {k: v["v"] for k, v in s["values"].items()}}
                      for s in region["price_scenarios"]],
        "trials": {opt: {"n": e["n"], "steps": e["steps"], **({"by_method": e["by_method"]} if "by_method" in e else {})}
                   for opt, e in trials["options"].items()},
        "hardRules": [{"id": r["id"], "removes": r["removes"], "why": r["why"]} for r in region["rules"]["hard"]],
        "constraints": [c for c in region["constraints"] if c["id"] in inputs.get("example_constraints", [])],
        "context": dict(inputs.get("example_context") or {}),
    }
    states, error = None, None
    try:
        states = states_runner(payload)
    except (FileNotFoundError, RuntimeError, subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
        error = f"not completed: {exc}"
    docs["ablation.json"] = ablation_doc(states, ledger, protocol, mode)

    m5_ids = set(ledger.records)
    external = sorted(set().union(*(referenced_ids(d) for d in docs.values())) - m5_ids)
    out = {"label": mode, "docs": docs, "ledger": {"records": ledger.records, "external": external},
           "states": states, "states_input": payload, "errors": [error] if error else []}
    if rehearsal:
        out["docs"] = {name: rh.stamped(d) for name, d in docs.items()}
        out["run_mode"] = rh.REHEARSAL
    return out


def _inside(path: Path, folder: Path) -> bool:
    p, f = path.resolve(), folder.resolve()
    return p == f or f in p.parents


def sidecars(result: Mapping[str, Any]) -> dict[str, Any]:
    """The M5 intermediates: ledger records, A3 input and states. Never in site/data/."""
    led = {"schema_version": SCHEMA_VERSION, "label": result["label"], **result["ledger"]}
    files = {"ledger_m5.json": rh.stamped(led) if result.get("run_mode") == rh.REHEARSAL else led,
             "states_input_m5.json": result["states_input"]}
    if result.get("states") is not None:
        files["states_m5.json"] = result["states"]
    return files


def write(result: Mapping[str, Any], out_dir: Path, build_dir: Path | None = None) -> list[Path]:
    """Site documents to ``out_dir``, the sidecars to ``build_dir`` (data/build/ by default).

    Refused: sidecars in site/data/ (assemble refuses them there), the fictitious example in
    site/data/ (the folder the page reads as real), real outputs in tests/.
    """
    mode = result.get("label")
    _need(mode in LABELS, f"result label {mode!r}")
    out_dir = Path(out_dir)
    build_dir = BUILD_DIR if build_dir is None else Path(build_dir)
    _need(not _inside(build_dir, SITE_DATA), "M5 sidecars never go to site/data/: write them to data/build/")
    if result.get("run_mode") == rh.REHEARSAL:
        try:
            rh.check_out(out_dir, "site documents")
        except rh.RehearsalError as exc:
            raise PrecomputeError(str(exc)) from exc
    if mode == EXAMPLE:
        _need(not _inside(out_dir, SITE_DATA),
              "the fictitious example never goes to site/data/, which the page reads as real data. "
              "Open it with ?data=<folder>")
    else:
        for d in (out_dir, build_dir):
            _need(not _inside(d, TESTS_DIR), "real outputs never go to tests/ (protocol integrity)")
    written = []
    for folder, files in ((out_dir, result["docs"]), (build_dir, sidecars(result))):
        folder.mkdir(parents=True, exist_ok=True)
        for name, obj in files.items():
            p = folder / name
            p.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            written.append(p)
    return written


def daily_label_line(path: Path) -> str:
    with open(path, encoding="utf-8") as f:
        return f.readline()


def load_inputs(path: Path) -> tuple[dict, pd.DataFrame | None, pd.DataFrame]:
    """Inputs, synthetic Tábua rows (or None) and the daily weather.

    The first line of the weather CSV says what it is: "# example, fictitious" for the
    example run, never that for a real one (``field_inputs.write_daily``).
    """
    inputs = json.loads(path.read_text(encoding="utf-8"))
    mode = mode_of(inputs)
    rows = zarc_rows_frame(inputs["zarc_rows"]) if inputs.get("zarc_rows") else None
    csv_path = Path(inputs["daily_weather_csv"])
    csv_path = csv_path if csv_path.is_absolute() else path.parent / csv_path
    first = daily_label_line(csv_path).casefold()
    if mode == EXAMPLE:
        _need(first.startswith("#") and EXAMPLE in first, f"{csv_path.name}: an example run reads only weather "
                                                         f'labelled "# {EXAMPLE}" on its first line')
    else:
        _need(EXAMPLE not in first, f"{csv_path.name}: a real run never reads weather labelled {EXAMPLE!r}")
    daily = pd.read_csv(csv_path, comment="#", index_col="date", parse_dates=True)
    return inputs, rows, daily


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--inputs", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path, help="site documents (site/data for a real run)")
    ap.add_argument("--build-out", type=Path, default=BUILD_DIR, help="M5 sidecars (default data/build)")
    ap.add_argument("--zarc", type=Path, help="decoded Tábua (data/interim/zarc_norte_pr.csv); default: inputs' zarc_rows")
    ap.add_argument("--real", action="store_true", help=f'real run: the inputs must be labelled "{REAL}"')
    ap.add_argument("--rehearsal", action="store_true",
                    help="with --real: inputs stamped run_mode rehearsal, every document stamped, never site/data (SA-D63)")
    a = ap.parse_args(argv)
    inputs, rows, daily = load_inputs(a.inputs)
    if a.zarc:
        rows = pd.read_csv(a.zarc)
    if rows is None:
        raise SystemExit("no Tábua rows: pass --zarc or put zarc_rows in the inputs")
    try:
        result = build(inputs, config=base.load_config(), zarc_rows=rows, daily=daily,
                       mode=REAL if a.real else EXAMPLE, rehearsal=a.rehearsal)
        written = write(result, a.out, a.build_out)
    except PrecomputeError as exc:
        print(f"precompute: {exc}", file=sys.stderr)
        return 1
    for p in written:
        print(p)
    for e in result["errors"]:
        print(f"precompute: {e}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())

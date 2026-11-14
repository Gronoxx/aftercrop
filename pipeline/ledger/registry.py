# Written with an SI tool (Claude, Anthropic) for NASA Space Apps 2026. See AI_USE.md.
"""Evidence Ledger registry: every ledger id the site may show (plan 8.6, 7.14, 10.1, sheet M6).

A number on screen is a ``Num = {"v", "l"}`` or sits in an object whose ``ledger`` key names
its record. This module declares, for every id, what the record says: unit, evidence class
(the seal of 7.14), source, transform (the code path), method, uncertainty, validation state
and limits. ``pipeline.ledger.build`` merges these declarations with the records M5 writes
(``ledger_m5.json``) and with the provenance in ``site/data/manifest.json``.

Ledger id scheme
----------------
Dotted, ``<document>.<block>[.<item>...]``. The first segment names the document the number
comes from: ``region``, ``trials``, ``ablation``, ``field_<id>``, ``scoreboard``, or
``browser`` for numbers ``site/js/decision.mjs`` computes on the page (``value: null``,
``transform`` names the function, plan 8.6). Segments hold letters, digits, ``_`` and ``-``
(the ``-`` that ``pipeline.fields.geometry.FIELD_ID`` allows in a field id); ``/`` and spaces
become ``_`` (``precompute.ledger_id``), so season 2025/26 gives ``field_a.green_end.2025_26``.
A long series has one record for the whole series.

Owners: M2 (field blocks), M4 (scoreboard), M5 (ids made by ``precompute``), M7 (page).
An id that matches no template is an error: nothing reaches the screen undeclared.

Texts in ``limits`` are English drafts from the "Limite" column of plan 10.1 and the notes of
sections 7.10, 11.2 and 12.3. They are marked "drafted, review" and belong in
config/copy_*.yaml once a ``ledger`` block exists there (copy owns every screen sentence).
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

SCHEMA_VERSION = "1"

EVIDENCE_CLASSES = (
    "seen_by_satellite", "estimated_from_satellite", "reanalysis", "simulated", "measured_in_trials",
    "derived_from_trials", "official_rule", "published_climatology", "your_numbers", "unknown",
)
VALIDATION_STATES = (
    "not_applicable", "not_completed", "model_not_validated_here", "direction_not_validated",
    "direction_matched_mechanism_not_shown", "direction_supported_exploratory", "checked_against_station",
    "provisional", "not_simulated",
)
# The ten fields of plan 8.6, in order. A record also carries its ``id``, ``seal`` and ``owner``.
RECORD_FIELDS = ("value", "unit", "evidence_class", "source_id", "transform", "method_id", "data_version",
                 "uncertainty", "validation_state", "limits")
UNCERTAINTY_KINDS = ("event_interval", "range", "exact", "not_quantified")
# Classes whose record may have no source: typed numbers, declared gaps and engine output
# (the engine is the transform; its inputs are named in data_version).
SOURCELESS_CLASSES = ("your_numbers", "unknown", "simulated")

# Segments accept "-" because pipeline.fields.geometry.FIELD_ID does: field_<id> must always be an id.
ID_RE = re.compile(r"^[a-z][a-z0-9_-]*(\.[A-Za-z0-9_-]+)+$")
SEGMENT = r"[A-Za-z0-9_-]+"

# Files of site/data/ that are not screen documents: checked for schema_version only.
META_DOCS = ("ledger.json", "manifest.json", "manifest_files.json", "copy.json")
# Intermediate files of M5 that never go to the public folder.
INTERMEDIATE_RE = re.compile(r".*_m5\.json$")
# Keys whose numbers are not screen numbers (pixel geometry, versions).
NOT_SCREEN_KEYS = frozenset({"schema_version", "polygon_px"})

# Identifiers with digits that are names, not numbers: strategies S1 to S3, the bench tests
# and the comparison rows L1 to L5 (7.5). A text that has no digit left once they are
# removed is not a number (criterion M6.2).
IDENTIFIER_TOKENS = re.compile(r"\b(?:S[1-3]|L[0-5]|T[01]|P1|A[1-3]|N1|H[12]|R1)\b")

LABEL = "example, fictitious"

# Default limits by evidence class: plan 10.1, column "Limite", and 7.14.  # drafted, review
CLASS_LIMITS = {
    "seen_by_satellite": "Does not tell the crop, bare soil or yield.",
    "estimated_from_satellite": "A date estimated from satellite carries its own interval, per event.",
    "reanalysis": "Regional climate model output. It smooths extremes. It is not a measurement.",
    "simulated": "Direction only. Never a percentage, never an absolute yield, never in the money.",
    "measured_in_trials": "A trial plot, not this field.",
    "derived_from_trials": "Our calculation on published trial means. Never a measured value.",
    "official_rule": "Read from the official text. Independent tool, no endorsement.",
    "published_climatology": "Published climatology with no specific year.",
    "your_numbers": "Not verified.",
    "unknown": "A declared gap. It goes to local review.",
}

UNQ = {"kind": "not_quantified"}
EXACT = {"kind": "exact"}
EVENT = {"kind": "event_interval", "low": None, "high": None}


@dataclass(frozen=True)
class Template:
    """What the registry declares for every id that matches ``pattern``.

    ``pattern`` is dotted: ``*`` is one segment, ``(a|b)`` an alternative, anything else
    literal. A field left None is taken from the M5 record, or from the class defaults.
    ``data_version`` None means: compose it from the manifest entries of ``source_id``.
    ``uncertainty_from`` names the keys of the object holding the Num (low, high).
    ``inherit`` names a record (``{1}`` is the second id segment) whose source and data
    version this one shares. ``require_class`` makes ``evidence_class`` a requirement on
    the M5 record too (criterion M6.4), not only a default.
    """

    pattern: str
    owner: str
    unit: str | None = None
    evidence_class: str | None = None
    source_id: str | None = None
    transform: str | None = None
    method_id: str | None = None
    validation_state: str | None = None
    uncertainty: Mapping[str, Any] | None = None
    limits: str | None = None
    data_version: str | None = None
    uncertainty_from: tuple[str, str] | None = None
    inherit: str | None = None
    require_class: bool = False
    regex: re.Pattern = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "regex", compile_pattern(self.pattern))

    def defaults(self) -> dict[str, Any]:
        keys = ("unit", "evidence_class", "source_id", "transform", "method_id", "validation_state",
                "uncertainty", "limits", "data_version")
        return {k: getattr(self, k) for k in keys if getattr(self, k) is not None}


def compile_pattern(pattern: str) -> re.Pattern:
    parts = []
    for seg in pattern.split("."):
        if seg == "*":
            parts.append(SEGMENT)
        elif seg.startswith("(") and seg.endswith(")"):
            parts.append("(?:" + "|".join(re.escape(x) for x in seg[1:-1].split("|")) + ")")
        else:
            parts.append(re.escape(seg).replace(r"\*", SEGMENT))
    return re.compile("^" + r"\.".join(parts) + "$")


F = "field_*"           # one field document, field_<id>
OPT = "(S2|S3)"         # the options compared with S1 (13.6)

TEMPLATES: tuple[Template, ...] = (
    # M2: field blocks (plan 8.4 steps 5 to 10). Values come from the Num in field_<id>.json.
    Template(f"{F}.area_ha", "M2", unit="ha", evidence_class="estimated_from_satellite", source_id="hls",
             transform="pipeline.fields.geometry.field_on_grid", method_id="M2-area-v1",
             validation_state="not_applicable", uncertainty=UNQ,
             limits="Drawn by hand on the satellite basemap. Not a survey of the field."),  # drafted, review
    Template(f"{F}.scale_note.power_cell_km", "M2", unit="km", evidence_class="reanalysis", source_id="power_daily",
             transform="pipeline.decision.field_inputs.scale_note", method_id="M2-scale-note-v1",
             validation_state="not_applicable", uncertainty=EXACT,
             limits="One climate cell covers many fields. It is not a station on your farm."),  # drafted, review
    Template(f"{F}.scale_note.radiation_cell_km", "M2", unit="km", evidence_class="reanalysis",
             source_id="power_daily", transform="pipeline.decision.field_inputs.scale_note",
             method_id="M2-scale-note-v1", validation_state="not_applicable", uncertainty=EXACT,
             limits="Solar radiation comes from a coarser cell than rain and temperature."),  # drafted, review
    Template(f"{F}.scale_note.soil_m", "M2", unit="m", evidence_class="unknown", source_id="soilgrids_wcs",
             transform="pipeline.decision.field_inputs.scale_note", method_id="M2-scale-note-v1",
             validation_state="not_applicable", uncertainty=EXACT,
             limits="A global soil map cell, not a lab report of this field."),  # drafted, review
    Template(f"{F}.scale_note.dem_m", "M2", unit="m", evidence_class="estimated_from_satellite", source_id="nasadem",
             transform="pipeline.decision.field_inputs.scale_note", method_id="M2-scale-note-v1",
             validation_state="not_applicable", uncertainty=EXACT,
             limits="Terrain cell size. Terraces and small low spots are finer than this."),  # drafted, review
    Template(f"{F}.scale_note.erosivity_km", "M2", unit="km", evidence_class="published_climatology",
             source_id="gloreda", transform="pipeline.decision.field_inputs.scale_note", method_id="M2-scale-note-v1",
             validation_state="not_applicable", uncertainty=EXACT, limits=CLASS_LIMITS["published_climatology"]),
    Template(f"{F}.soil", "M2", unit="layers: clay, sand and organic matter, % by mass", evidence_class="unknown",
             source_id="soilgrids_wcs", transform="pipeline.fields.soil_profile", method_id="M2-soil-profile-v1",
             validation_state="not_applicable", uncertainty={"kind": "range"},
             limits="A global soil map, not a lab report. Ask for a soil lab report."),  # drafted, review
    Template(f"{F}.relief", "M2", unit="slope class", evidence_class="estimated_from_satellite", source_id="nasadem",
             transform="pipeline.fields.relief", method_id="M2-relief-v1", validation_state="not_applicable",
             uncertainty=UNQ, limits="Terraces are not seen at this scale. Tell us if the field is terraced."),  # drafted, review
    Template(f"{F}.ndvi_series", "M2", unit="NDVI and NBR2, unitless, mean over the inner polygon",
             evidence_class="seen_by_satellite", source_id="hls_vi_v2", transform="pipeline.fields.ndvi_series",
             method_id="M2-ndvi-series-v1", validation_state="not_applicable", uncertainty=UNQ,
             limits=CLASS_LIMITS["seen_by_satellite"], require_class=True),
    Template(f"{F}.ribbon", "M2", unit="dekad class: green, not green, to confirm or no data",
             evidence_class="estimated_from_satellite", source_id="hls_vi_v2", transform="pipeline.fields.ribbon",
             method_id="M2-ribbon-v1", validation_state="not_applicable", uncertainty=UNQ,
             limits="A dekad with no clean observation stays no data. Nothing is interpolated."),  # 8.4 step 7
    Template(f"{F}.green_end.*", "M2",
             unit="days between the last green and the first not-green clean observation",
             evidence_class="estimated_from_satellite", source_id="hls_vi_v2",
             transform="pipeline.fields.events.green_ends", method_id="M2-green-end-v2",
             validation_state="not_applicable", uncertainty=EVENT, uncertainty_from=("low", "high"),
             limits="Green ending is not harvest. Confirm with your own date."),  # 8.6
    # M5: ids made by pipeline.decision.precompute. The registry only fills what M5 leaves null.
    Template("region.units.*", "M5", uncertainty=EXACT,
             limits="A unit of the region, used to convert your numbers."),  # drafted, review
    Template("region.price_scenarios.*.*", "M5", evidence_class="your_numbers", require_class=True,
             uncertainty=UNQ, limits="Example, fictitious. Type your own prices and costs."),  # 14.3
    Template(f"trials.{OPT}", "M5", uncertainty=UNQ),
    Template(f"trials.{OPT}.bad_year", "M5", uncertainty=UNQ),
    Template(f"trials.{OPT}.mean_delta", "M5", unit="kg/ha", evidence_class="derived_from_trials",
             require_class=True, source_id="embrapa_docs", transform="pipeline.bench.trials",
             method_id="M4-mean-delta-v1", validation_state="not_applicable", uncertainty=UNQ,
             limits=CLASS_LIMITS["derived_from_trials"]),   # +234 (10.1, M6.4), trials_steps.json summary.delta
    Template(f"trials.{OPT}.summary", "M5", uncertainty=UNQ),            # the L3 line of the option (7.5)
    Template(f"trials.{OPT}.summary.*", "M5", uncertainty=UNQ),          # its numbers, page.rows.L3.* placeholders
    Template("trials.reference", "M5", uncertainty=UNQ,
             limits="The bad year of the reference, as the trial report prints it."),  # drafted, review
    Template("trials.money_refs.*", "M5", uncertainty=UNQ),
    Template(f"{F}.window_reading", "M5", uncertainty=EVENT,
             limits="Green ending is not harvest. The corn window is the Zarc's, with zero delay."),  # 12.3
    # SA-D72: the Zarc risk band above the ribbon, the risk at exit and "ended before".
    Template(f"{F}.zarc_risk_band", "M5", unit="% risk per ten-day period", evidence_class="official_rule",
             source_id="zarc_tabua", uncertainty=UNQ,
             limits="Zarc risk for sowing in each ten-day period. A blank period is outside the zoning, never zero risk."),  # drafted, review
    Template(f"{F}.risk_at_exit", "M5", uncertainty=EVENT,
             limits="Green ending is not harvest. A condition of the field being free, never a sowing date."),  # drafted, review
    Template(f"{F}.ended_before", "M5", uncertainty=EVENT,
             limits="Green ending is not harvest. Never says that sowing fell inside the window."),  # drafted, review
    Template(f"{F}.risk", "M5", uncertainty=UNQ,
             limits="Years counted in a climate cell, not at a station on your farm."),  # drafted, review
    Template(f"{F}.climate", "M5", unit="seasons (frost, k of n) and mm (rain total per season)",
             evidence_class="reanalysis", require_class=True, source_id="power_daily,embrapa_doc476,zarc_frost_2026_27",
             transform="pipeline.decision.risk_counts.occupation_climate", method_id="M5-climate-occupation-v1",
             validation_state="not_applicable", uncertainty=UNQ,
             limits="Counts of past seasons in one POWER cell, not a forecast and not a threshold of failure."),  # SA-D72.5  # drafted, review
    Template(f"{F}.ribbon_counts.no_green", "M5", unit="ten-day periods of 36 with no green seen, median over the seasons",
             evidence_class="estimated_from_satellite", require_class=True, source_id="hls_vi_v2",
             transform="pipeline.fields.ribbon.no_green_count", method_id="M5-no-green-v1",
             validation_state="not_applicable", uncertainty=UNQ,
             limits="No green is not bare soil: NDVI cannot separate straw from soil. Periods with no clean image are not counted."),  # SA-D72.4  # drafted, review
    Template(f"{F}.erosivity", "M5", uncertainty=UNQ,
             limits="Climatology with no specific year. Classes compare strategies, not soil loss."),  # drafted, review
    Template("region.noise_floor", "M5", unit="days", evidence_class="estimated_from_satellite",
             require_class=True, source_id="hls_vi_v2", transform="pipeline.bench.n1.evaluate",
             method_id="M4-N1-v1", validation_state="not_applicable", uncertainty=UNQ,
             limits="Nothing below the floor reaches the screen. Field order needs a difference above it."),  # 11.2
    Template("region.nasa_switch.*", "M5", uncertainty=EXACT,
             limits="A rule fixed in the protocol before any run (SA-D66). It places the switch, it is not a result."),  # drafted, review
    Template(f"{F}.next_soy", "M5", uncertainty=UNQ, limits=CLASS_LIMITS["simulated"]),
    Template("ablation.*.(states_changed|states_total)", "M5", uncertainty=UNQ,
             limits="Counted on the fictitious example in three price scenarios. Read as a sensitivity to price."),  # 11.2
    # M4: the scoreboard. Its numbers sit in blocks without a ledger key; IMPLICIT maps them.
    Template("scoreboard.tests.L0", "M4", unit="mm per swap, repeat, artifacts", evidence_class="simulated",
             transform="pipeline.bench.l0", method_id="M4-L0-v1", validation_state="not_applicable",
             uncertainty=EXACT, data_version="crop engine runs of the bench",
             limits="A check of the model's water balance, not of the field."),  # drafted, review
    Template("scoreboard.tests.L1", "M4", unit="% rain bias per season, °C temperature bias",
             evidence_class="reanalysis", source_id="power_daily,stations", transform="pipeline.bench.l1",
             method_id="M4-L1-v1", validation_state="checked_against_station", uncertainty=UNQ,
             limits="Londrina stations only. A series outside the limits is labelled, never corrected in silence."),
    Template("scoreboard.tests.(T0|T1|P1|A1)", "M4", unit="Kendall S, RMSE in kg/ha",
             evidence_class="derived_from_trials", source_id="embrapa_docs", transform="pipeline.bench.label",
             method_id="M4-bench-v1", validation_state="not_applicable", uncertainty=UNQ,
             limits="Exploratory, not blind. 10 site-seasons of 3 sites are not 10 independent sites."),  # 11.2
    Template("scoreboard.tests.A2", "M4", unit="years counted with each rain source", evidence_class="reanalysis",
             source_id="power_daily", transform="pipeline.bench.ablation", method_id="M4-A2-v1",
             validation_state="not_applicable", uncertainty=UNQ,
             limits="Reported in How we know. Not a key of the NASA switch."),  # 11.2
    Template("scoreboard.tests.A3", "M4", unit="states", evidence_class="derived_from_trials", source_id="hls_vi_v2",
             transform="pipeline.bench.ablation.evaluate_a3", method_id="M5-A3-v1",
             validation_state="not_applicable", uncertainty=UNQ,
             limits="Counted on the fictitious example. Read as a sensitivity to price."),  # 11.2
    Template("scoreboard.tests.N1", "M4", unit="days", evidence_class="estimated_from_satellite",
             source_id="hls_vi_v2", transform="pipeline.bench.n1", method_id="M4-N1-v1",
             validation_state="not_applicable", uncertainty=UNQ,
             limits="Nothing below the floor reaches the screen."),  # 11.2
    Template("scoreboard.tests.H1", "M4", unit="tasks", evidence_class="unknown", transform="pipeline.bench.scoreboard",
             method_id="M4-H1-v1", validation_state="not_applicable", uncertainty=UNQ,
             data_version="H1 sessions of the event",
             limits="Informal. It measures understanding of the page, not farm impact."),  # 11.2
    Template("scoreboard.tests.H2", "M4", unit="state", evidence_class="unknown",
             transform="pipeline.bench.scoreboard.h2_entry", method_id="M4-H2-v1",
             validation_state="not_applicable", uncertainty=UNQ, data_version="protocol.tests.H2",
             limits="Not attempted. There was no local agronomist review."),  # 11.2, SA-D56
    Template("scoreboard.tests.R1", "M4", unit="members, inversions", evidence_class="simulated",
             transform="pipeline.bench.r1", method_id="M4-R1-v1", validation_state="not_applicable",
             uncertainty=UNQ, data_version="crop engine runs of the bench",
             limits="Runs only if T1 passes. Stability in the model is not success in the real world."),  # 12.5
    Template("scoreboard.(site_seasons|s_by_site|noise|correlation_ceiling)", "M4", unit="site-seasons, Kendall S",
             evidence_class="derived_from_trials", source_id="embrapa_docs", transform="pipeline.bench.scoreboard.build",
             method_id="M4-scoreboard-v1", validation_state="not_applicable", uncertainty=UNQ,
             limits="Shown for every trial site, none left out. No p-value, no confidence interval."),  # 7.10
    Template("scoreboard.late_notes", "M4", unit="text", evidence_class="unknown",
             transform="pipeline.bench.scoreboard.build", method_id="M4-scoreboard-v1",
             validation_state="not_applicable", uncertainty=UNQ, data_version="notes added after the freeze",
             limits=CLASS_LIMITS["unknown"]),
    # M4: the one soil sensitivity run after the freeze (SA-D77), site/data/sensitivity.json.
    # It sits next to the frozen scoreboard and never changes its label.
    Template("sensitivity.tests.L0", "M4", unit="mm per swap, repeat, artifacts", evidence_class="simulated",
             transform="pipeline.bench.l0", method_id="M4-L0-v1", validation_state="not_applicable",
             uncertainty=EXACT, data_version="crop engine runs of the bench (field chains, as the frozen L0)",
             limits="A check of the model's water balance, not of the field. Run after the freeze, it never "
                    "changes the label."),  # drafted, review
    Template("sensitivity.tests.(T0|T1|P1|A1)", "M4", unit="Kendall S, RMSE in kg/ha",
             evidence_class="derived_from_trials", source_id="embrapa_docs,soilgrids_wcs",
             transform="pipeline.bench.sensitivity.build", method_id="M4-bench-v1",
             validation_state="not_applicable", uncertainty=UNQ,
             limits="Soil sensitivity decided after the freeze: the trial soils from SoilGrids, everything else "
                    "unchanged. Exploratory, not blind. It never changes the label."),  # drafted, review
    Template("sensitivity.(site_seasons|s_by_site)", "M4", unit="site-seasons, Kendall S",
             evidence_class="derived_from_trials", source_id="embrapa_docs",
             transform="pipeline.bench.sensitivity.build", method_id="M4-scoreboard-v1",
             validation_state="not_applicable", uncertainty=UNQ,
             limits="Shown for every trial site, none left out. No p-value, no confidence interval."),  # 7.10
    Template("sensitivity.soil", "M4", unit="clay in % by mass, available water in mm", evidence_class="unknown",
             source_id="soilgrids_wcs,embrapa_docs", transform="pipeline.engine.soil_diagnosis.soil_record",
             method_id="M3-soil-diagnosis-v1", validation_state="not_applicable", uncertainty=UNQ,
             limits="Water limits from the engine's texture formula on a global soil map and on the clay "
                    "printed in the trial reports. Not a lab measurement of these soils."),  # drafted, review
    Template("sensitivity.zero_yield", "M4", unit="seasons", evidence_class="simulated",
             transform="pipeline.engine.soil_diagnosis.zero_yield_counts", method_id="M3-soil-diagnosis-v1",
             validation_state="not_applicable", uncertainty=EXACT, data_version="crop engine runs of the bench",
             limits=CLASS_LIMITS["simulated"]),
    # M7: numbers decision.mjs computes on the page (value null, transform names the function).
    Template(f"browser.{OPT}.G", "M7", unit="sc/ha", evidence_class="your_numbers", require_class=True,
             method_id="M5-G-v1", validation_state="not_applicable",
             uncertainty=UNQ, data_version="computed on the page from your numbers",
             limits="Uses only your numbers. No model output enters the money."),  # M5.4
    Template(f"browser.{OPT}.step", "M7", unit="sc/ha, rounded up", evidence_class="your_numbers",
             transform="decision.mjs:stepOf", method_id="M5-step-v1", validation_state="not_applicable",
             uncertainty=EXACT, data_version="computed on the page from your numbers",
             limits="Rounding up never favours the switch."),  # 14.5
    Template(f"browser.{OPT}.(N|n)", "M7", unit="site-seasons", evidence_class="derived_from_trials",
             require_class=True, transform="decision.mjs:countAtStep", method_id="M5-steps-v1",
             validation_state="not_applicable", uncertainty=UNQ, inherit="trials.{1}",
             limits="Counts of published trial site-seasons at or above your step. Trial plots, not this field."),
    Template("browser.(margin_corn|margin_wheat)", "M7", unit="R$/ha", evidence_class="your_numbers",
             require_class=True, transform="decision.mjs:referenceMargin", method_id="M5-margin-v1",
             validation_state="not_applicable", uncertainty=UNQ, data_version="computed on the page from your numbers",
             limits=CLASS_LIMITS["your_numbers"]),
    Template("browser.field_order", "M7", unit="seasons", evidence_class="estimated_from_satellite",
             require_class=True, source_id="hls_vi_v2", transform="decision.mjs:fieldOrder",
             method_id="M5-field-order-v1", validation_state="not_applicable", uncertainty=UNQ,
             limits="Field order is shown only above the noise floor of N1. The choice seasons stay out."),  # 13.7
    Template("browser.hls_switch", "M7", unit="states", evidence_class="derived_from_trials", require_class=True,
             source_id="hls_vi_v2", transform="app.mjs:switchCount", method_id="M7-switch-v1",
             validation_state="not_applicable", uncertainty=EXACT,
             data_version="computed on the page from your numbers and the HLS window warning",
             limits="States of this page that change when HLS is off. Not a measure of accuracy."),  # 8.7, drafted, review
    Template("browser.nitrogen_exposure", "M7", unit="R$/ha", evidence_class="your_numbers", require_class=True,
             transform="app.mjs:nitrogenExposure", method_id="M7-nitrogen-v1", validation_state="not_applicable",
             uncertainty=EXACT, data_version="computed on the page from your numbers",
             limits="Shown apart. It does not enter G, because fertilizer is already in your costs."),  # 14.8
    Template("browser.input.*", "M7", unit="as typed", evidence_class="your_numbers", require_class=True,
             transform="app.mjs:parseNumber", method_id="M7-inputs-v1", validation_state="not_applicable",
             uncertainty=UNQ, data_version="typed on the page", limits=CLASS_LIMITS["your_numbers"]),
)

# Page records that no JSON names: built every time, value null (plan 8.6).
PRICE_FIELDS = ("p_soy", "p_corn", "p_wheat", "Y_corn", "Y_wheat", "c_corn", "c_wheat", "c_grass")
STATIC_IDS: tuple[str, ...] = (
    *(f"browser.{o}.{q}" for o in ("S2", "S3") for q in ("G", "step", "N", "n")),
    "browser.margin_corn", "browser.margin_wheat",
    "browser.field_order", "browser.hls_switch", "browser.nitrogen_exposure",
    # A typed green-end date has no input on the page yet: its record comes back with its reader.
    *(f"browser.input.{f}" for f in (*PRICE_FIELDS, "sowing_date")),
)
# Functions behind the static records of a family template (browser.S2.G and browser.S3.G have no
# family function, so their template names none: the transform is always set here).
TRANSFORM_OVERRIDES = {
    "browser.S2.G": "decision.mjs:computeG2",
    "browser.S3.G": "decision.mjs:computeG3",
    "browser.S2.n": "decision.mjs:trialCount",
    "browser.S3.n": "decision.mjs:trialCount",
    "browser.margin_corn": "decision.mjs:marginCorn",
    "browser.margin_wheat": "decision.mjs:marginWheat",
    "browser.input.sowing_date": "decision.mjs:sowingDateStatus",
}

# Class rules of criterion M6.4 (plan 7.14, 10.1).
SEEN_BY_SATELLITE_IDS = (re.compile(r"^field_[A-Za-z0-9_-]+\.(ndvi_series|reflectance|basemap)$"),)
IMERG_IDS = re.compile(r"(^|\.)[A-Za-z0-9_]*imerg[A-Za-z0-9_]*(\.|$)")
POWER_SOURCE = "power_daily"
DERIVED_FROM_TRIALS_IDS = (
    re.compile(r"^trials\.(S2|S3)\.(mean_delta|delta[A-Za-z0-9_]*)$"),   # the 10 Δ and +234
    re.compile(r"^browser\.(S2|S3)\.N$"),                               # counts N of n
)
# Simulated values never show money, percentages or yields (10.1).
SIMULATED_FORBIDDEN_UNITS = ("R$", "%", "kg/ha", "sc/ha", "t/ha")


def template_for(rid: str) -> Template | None:
    """The single template of ``rid``, None if undeclared. Two matches are a registry bug."""
    hits = [t for t in TEMPLATES if t.regex.match(rid)]
    if len(hits) > 1:
        raise ValueError(f"ledger id {rid} matches {len(hits)} templates: {[t.pattern for t in hits]}")
    return hits[0] if hits else None


# Implicit ledger keys --------------------------------------------------------------------

SCOREBOARD_BLOCKS = ("site_seasons", "s_by_site", "noise", "correlation_ceiling", "late_notes")


def explicit_view(name: str, doc: Any) -> Any:
    """``doc`` with the ledger ids its producer leaves implicit written out as ``ledger`` keys.

    The scoreboard (M4) carries no Num: each test line maps to ``scoreboard.tests.<id>``
    and each summary block to ``scoreboard.<block>``. Other documents are returned as is.
    """
    if name != "scoreboard.json" or not isinstance(doc, Mapping):
        return doc
    out = dict(doc)
    for k in SCOREBOARD_BLOCKS:
        if out.get(k) is not None:
            out[k] = {"ledger": f"scoreboard.{k}", "block": out[k]}
    if isinstance(out.get("tests"), list):
        out["tests"] = [{**t, "ledger": f"scoreboard.tests.{t.get('id')}"} if isinstance(t, Mapping) else t
                        for t in out["tests"]]
    return out


# Sources -----------------------------------------------------------------------------------
#
# Bibliographic sources (title, url) belong to pipeline.decision.strategies.SOURCES. Raw
# sources and their licenses belong to config/region_norte_pr.yaml (sources.*). This table
# holds what neither has: titles of raw-only sources, versions and attribution texts, which
# follow LICENSES.md. ``subset_of`` and ``include`` define a source as part of the files of
# another (the VI product inside data/raw/hls/).

SOURCE_INFO: dict[str, dict[str, Any]] = {
    "zarc_tabua": {
        "kind": "official",
        "attribution": "Tábua de Risco do Zarc, MAPA, CC BY. No endorsement implied.",
    },
    "power_daily": {
        "kind": "reanalysis",
        "title": "NASA POWER, daily point data (community AG), including IMERG_PRECTOT",
        "url": "https://power.larc.nasa.gov/api/temporal/daily/point",
        "attribution": "NASA POWER Project, daily point data.",
    },
    "soilgrids_wcs": {
        "kind": "soil",
        "title": "SoilGrids 2.0, ISRIC, WCS service",
        "url": "https://maps.isric.org/mapserv",
        "version": "SoilGrids 2.0 (WCS 2.0.1, maps.isric.org)",
        "attribution": "SoilGrids 2.0, ISRIC, CC BY 4.0.",
    },
    "hls": {
        "kind": "satellite",
        "title": "Harmonized Landsat and Sentinel-2 (HLS) v2.0 and HLS-VI v2.0, NASA LP DAAC",
        "url": "https://www.earthdata.nasa.gov/data/catalog/lpcloud-hlss30-2.0",
        "attribution": "HLS and HLS-VI v2.0, NASA LP DAAC. Contains modified Copernicus Sentinel data.",
    },
    "hls_vi_v2": {
        "kind": "satellite",
        "subset_of": "hls",
        # VI bands and their CMR answers, and the Fmask the series masks them with
        # (``ndvi_series.series_from_records``). An HLS Fmask joins only when its window folder
        # holds VI bands (``companion_dir``): the basemap window's Fmask stays with "hls".
        "include": r"(^|/)(HLS-VI\.|cmr_HLS[LS]30_VI_|HLS\.[LS]30\.[^/]+\.Fmask\.npz$)",
        "companion_dir": r"(^|/)HLS-VI\.[^/]+$",
        "title": "HLS-VI v2.0 (HLSL30_VI, HLSS30_VI), NASA LP DAAC: NDVI, NBR2 and Fmask",
        "url": "https://www.earthdata.nasa.gov/data/catalog/lpcloud-hlss30-2.0",
        "attribution": "HLS-VI v2.0, NASA LP DAAC. Contains modified Copernicus Sentinel data.",
    },
    "embrapa_docs": {
        "kind": "trials",
        "title": "Embrapa Soja, Documentos 398, 459 and 476 (cached PDFs)",
        "url": "https://www.infoteca.cnptia.embrapa.br/infoteca/handle/doc/1086382?mode=full",
        "license": ("Conflict: 'Todos os direitos reservados' in the PDF, CC BY-NC-ND 4.0 on a repository page. "
                    "Values are not shipped"),
        "attribution": "Cited with document, page and table. Values are not shipped.",
    },
    "ibge_malhas": {
        "kind": "check_only",
        "title": "IBGE municipal meshes",
        "url": "https://www.ibge.gov.br/acesso-informacao/institucional/termo-de-uso.html",
        "license": "IBGE terms of use. Used to check trial coordinates and to find the municipality of a field, not shipped",
        "attribution": "Not shipped.",
    },
    "nasadem": {
        "kind": "dem",
        "title": "NASADEM",
        "url": None,           # read when the adapter is written (plan 8.5, nasadem.py)
        "license": "NASA data, cite",
        "attribution": "NASADEM, NASA LP DAAC.",
    },
    "gloreda": {
        "kind": "climatology",
        "title": "GloREDa, Global Rainfall Erosivity Database (Zenodo record 8036998)",
        "url": "https://zenodo.org/records/8036998",
        "license": "CC BY 4.0, Zenodo copy only",
        "attribution": "Cited as on Zenodo. ESDAC copy not redistributed.",
    },
    "stations": {
        "kind": "station",
        "title": "INMET and IDR-Paraná weather stations, Londrina",
        "url": None,
        "license": "not used in the event: L1 is planned, not run (SA-D65). Read the station terms before any use",
        "attribution": None,
    },
}

PLANNED_SOURCES = ("nasadem", "gloreda", "stations")   # adapters of plan 8.5 not written yet

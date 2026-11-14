# Written with an SI tool (Claude, Anthropic) for NASA Space Apps 2026. See AI_USE.md.
"""The settings loader (pipeline.decision.settings) and config/decision_settings.yaml.

Every value has a source or a removal reason (protocol.unsourced_rules, SA-D68). The occupation
dates are recomputed from config/trial_dates.json (dates only, no yield). The error cases use
synthetic documents.
"""

import copy
import json
import statistics
from datetime import date, timedelta

import pytest
import yaml

from pipeline.decision import calendar as cal
from pipeline.decision import settings as sm
from pipeline.decision import strategies as st

ROOT = sm.DEFAULT_PATH.parents[1]
RAW = yaml.safe_load(sm.DEFAULT_PATH.read_text(encoding="utf-8"))
TRIAL_DATES = json.loads((ROOT / "config" / "trial_dates.json").read_text(encoding="utf-8"))


def loaded():
    return sm.load(sm.DEFAULT_PATH)


def median_md(isos):
    """Median day of the year of ISO dates, as MM-DD (the rule written in the settings file)."""
    days = sorted(date.fromisoformat(x).replace(year=2001) for x in isos)
    return statistics.median_low(days).strftime("%m-%d")


def test_notice_on_the_first_line():
    first = sm.DEFAULT_PATH.read_text(encoding="utf-8").splitlines()[0]
    assert first == "# Written with an SI tool (Claude, Anthropic) for NASA Space Apps 2026. See AI_USE.md."


def test_every_value_has_a_source_or_a_removal_reason():
    s = loaded()
    expected = {f"{g}.{k}" for g, keys in sm.GROUPS.items() for k in keys} | set(sm.ENTRIES)
    assert set(s["sources"]) == expected
    assert all(isinstance(t, str) and len(t) > 20 for t in s["sources"].values())
    assert set(s) >= {"cycles", "occupancy", "risk", "example_context", "example_constraints", "region"}


def test_unsourced_variables_are_removed_and_say_why():
    s = loaded()
    assert s["risk"] == {"dry_autumn": None, "cold_in_window": None}
    assert s["cycles"] == {"corn": None, "corn_grass": None, "wheat": None}
    for k in ("risk.dry_autumn", "risk.cold_in_window"):
        assert s["sources"][k].startswith("removed: ") and "Searches" in s["sources"][k]   # two searches each
    assert "2 °C" in s["sources"]["risk.cold_in_window"]                                    # the threshold exists


def test_occupation_dates_are_the_londrina_medians_of_doc_476():
    rows = TRIAL_DATES["sites"]["londrina"]
    soy = (median_md([r["soybean_sowing"] for r in rows]), median_md([r["soybean_harvest"] for r in rows]))
    corn = (median_md([r["predecessor_sowing"]["maize"] for r in rows]),
            median_md([r["predecessor_harvest"]["maize"] for r in rows]))
    wheat = (median_md([r["predecessor_sowing"]["wheat"] for r in rows]),
             median_md([r["predecessor_harvest"]["wheat"] for r in rows]))
    occ = loaded()["occupancy"]
    phases = {sid: {p["crop"]: (p["start"], p["end"]) for p in spec["phases"]} for sid, spec in occ.items()}
    assert phases["S1"] == {"soybean": soy, "corn": corn}
    assert phases["S3"] == {"soybean": soy, "wheat": wheat}
    assert phases["S2"]["soybean"] == soy and phases["S2"]["corn_grass"] == corn
    # The grass lives from the day after the corn harvest to its desiccation 15 days before the soybean.
    sow = date(2001, *map(int, soy[0].split("-")))
    after = date(2001, *map(int, corn[1].split("-"))) + timedelta(days=1)
    assert phases["S2"]["grass"] == (after.strftime("%m-%d"), (sow - timedelta(days=15)).strftime("%m-%d"))
    assert all(spec["source_id"] == "embrapa_doc476" for spec in occ.values())
    grass = next(p for p in occ["S2"]["phases"] if p["crop"] == "grass")
    assert grass["source_id"] in st.SOURCES


def test_the_calendar_keeps_the_hard_rule_and_has_no_overlap():
    occ = loaded()["occupancy"]
    assert cal.hard_rule_breaks(occ, "2026/27") == {st.HARD_RULE["id"]: []}
    for spec in occ.values():
        runs = cal.occupancy(spec, "2026/27")
        assert runs[0]["start"] == "2026-09-01" and runs[-1]["end"] == "2027-08-31"


def test_prices_are_the_fictitious_example_farmer():
    s = loaded()
    assert s["region"]["price_k"] == 0.15
    assert set(s["region"]["example_numbers"]) == {"p_soy", "p_corn", "Y_corn", "c_corn", "p_wheat", "Y_wheat",
                                                   "c_wheat", "c_grass"}
    assert s["sources"]["region.example_numbers"].startswith("Example, fictitious")
    assert s["region"]["pack"]["reviewer"] is False                                  # H2 not attempted, SA-D56


def test_no_source_text_reaches_the_base_values():
    """The source texts stay in ``sources``: the values carry no "example, fictitious" string (precompute
    refuses that label in a real run)."""
    s = loaded()
    values = json.dumps({k: v for k, v in s.items() if k != "sources"}, ensure_ascii=False)
    assert "example, fictitious" not in values.casefold()


@pytest.mark.parametrize("mutate, message", [
    (lambda d: d["region"]["price_k"].pop("source"), "exactly one of source or removed"),
    (lambda d: d["region"]["price_k"].update(removed="no"), "exactly one of source or removed"),
    (lambda d: d["region"].update(price_k={"value": None, "removed": "not found"}), "cannot be removed"),
    (lambda d: d["risk"]["dry_autumn"].update(source="a synthetic source"), "exactly one"),
    (lambda d: d["risk"].update(dry_autumn={"value": 30, "removed": "not found"}), "must be null"),
    (lambda d: d["risk"].update(dry_autumn={"value": {"threshold_mm": 30}, "source": "a synthetic source"}), "span"),
    (lambda d: d["region"]["price_k"].update(source=" "), "empty"),
    (lambda d: d.update(extra=1), "unknown top keys"),
    (lambda d: d["occupancy"]["S1"]["value"].update(source_id="nowhere"), "strategies.SOURCES"),
    (lambda d: d["occupancy"].update(S1={"value": None, "removed": "no dates"}), "cannot be shown"),
    (lambda d: d["cycles"].pop("wheat"), "cycles needs exactly"),
    (lambda d: d.update(schema_version=2), "schema_version"),
])
def test_bad_settings_are_refused(mutate, message):
    doc = copy.deepcopy(RAW)
    mutate(doc)
    with pytest.raises(sm.SettingsError, match=message):
        sm.parse(doc)


def test_a_sourced_risk_count_is_kept():
    doc = copy.deepcopy(RAW)
    doc["risk"]["dry_autumn"] = {"value": {"threshold_mm": 30, "span": ["04-01", "05-31"]},
                                 "source": "a synthetic source for this test"}
    s = sm.parse(doc)
    assert s["risk"]["dry_autumn"] == {"threshold_mm": 30, "span": ["04-01", "05-31"]}


def test_a_plain_json_is_read_as_before(tmp_path):
    p = tmp_path / "settings.json"
    p.write_text(json.dumps({"cycles": {"corn": "Grupo II"}}), encoding="utf-8")
    assert sm.load(p) == {"cycles": {"corn": "Grupo II"}}

# Written with an SI tool (Claude, Anthropic) for NASA Space Apps 2026. See AI_USE.md.
"""The 8-season ribbon (plan 7.7, M2.2): missing data stays missing, green cycles counted, counts. Offline.

Every series below is an example, fictitious. The fixture rule (NDVI 0.5, half the
pixels, 20 pixels) is a test value. The protocol value of D10 (0.30) is read in one test.
"""

import copy
import json
from datetime import date
from pathlib import Path

import pytest

from pipeline.adapters import base
from pipeline.bench.common import ProtocolError
from pipeline.common import dekad
from pipeline.fields import ribbon

FIX = json.loads((Path(__file__).parent / "fixtures" / "fields_series_gap39.json").read_text(encoding="utf-8"))
RULE = ribbon.CleanRule(**FIX["rule"])


def row(d, ndvi, n_clean=100, n_possible=100, sensor="S30"):
    return {"date": d, "sensor": sensor, "granule_ur": f"EXAMPLE.{sensor}.{d}", "ndvi": ndvi,
            "n_clean": n_clean, "n_possible": n_possible}


def test_gap_of_39_days_gives_no_data_never_interpolated():
    r = ribbon.ribbon(FIX["rows"], [FIX["season"]], RULE)[0]
    exp = FIX["expected"]
    assert r["cells"] == exp["cells"]
    assert (r["n_obs"], r["longest_gap_days"], r["green_cycles"]) == (exp["n_obs"], exp["longest_gap_days"], 1)
    # the dekads between 1 Jan and 9 Feb have no clean observation: no_data, though both ends are known
    assert r["cells"][13:15] == ["no_data", "no_data"]
    assert set(r["cells"]) <= set(ribbon.CELLS) and len(r["cells"]) == 36


def test_clean_rule_boundaries():
    assert RULE.is_clean(row("2026-01-01", 0.6, n_clean=20, n_possible=40))      # exactly 20 pixels and 0.5
    assert not RULE.is_clean(row("2026-01-01", 0.6, n_clean=19, n_possible=38))  # 0.5 but 19 pixels
    assert not RULE.is_clean(row("2026-01-01", 0.6, n_clean=20, n_possible=41))  # 20 pixels but below 0.5
    assert not RULE.is_clean(row("2026-01-01", None))
    assert not RULE.is_clean(row("2026-01-01", float("nan")))
    r3 = ribbon.CleanRule(0.5, 0.6, 3)
    assert r3.is_clean(row("2026-01-01", 0.6, n_clean=3, n_possible=5))  # 0.6 exactly, no float slip
    assert RULE.is_green(0.51) and not RULE.is_green(0.5)                 # above the threshold, not at it


def test_dekad_takes_the_largest_clean_ndvi():
    rows = [row("2025-11-02", 0.3), row("2025-11-08", 0.7, sensor="L30"), row("2025-11-12", 0.2),
            row("2025-11-15", 0.9, n_clean=5)]  # last one not clean
    cells = ribbon.ribbon(rows, ["2025/26"], RULE)[0]["cells"]
    assert cells[6] == "green" and cells[7] == "not_green"


def test_second_green_cycle_stays_green():
    # D12 (SA-D62): the second cycle stays visible in the ribbon and never turns cells to "to confirm"
    rows = [row("2025-11-05", 0.8), row("2025-12-05", 0.85), row("2026-02-05", 0.3),
            row("2026-02-25", None, n_clean=0),   # cloudy: no_data, does not split
            row("2026-04-05", 0.7), row("2026-05-05", 0.75), row("2026-07-05", 0.2)]
    r = ribbon.ribbon(rows, ["2025/26"], RULE)[0]
    assert r["green_cycles"] == 2                  # counted, as information only
    assert r["cells"].count("green") == 4 and "to_confirm" not in r["cells"]
    assert r["cells"][dekad.ribbon_index(date(2026, 2, 5)) - 1] == "not_green"
    assert r["cells"][dekad.ribbon_index(date(2026, 2, 25)) - 1] == "no_data"


def test_ribbon_at_the_protocol_threshold():
    rule = ribbon.CleanRule.from_protocol(base.load_config())   # D10: 0.30 absolute
    cells = ribbon.ribbon([row("2025-09-05", 0.30), row("2025-09-15", 0.31)], ["2025/26"], rule)[0]["cells"]
    assert cells[:2] == ["not_green", "green"]     # above 0.30, not at it


def test_green_cycles_skip_no_data():
    assert ribbon.green_cycles(["green", "no_data", "green", "not_green", "no_data"]) == 1
    assert ribbon.green_cycles(["not_green", "green", "not_green", "to_confirm"]) == 2
    assert ribbon.green_cycles(["no_data"] * 36) == 0
    with pytest.raises(ValueError):
        ribbon.green_cycles(["grey"])


def test_rows_of_other_seasons_stay_out():
    rows = [row("2025-08-25", 0.8), row("2025-09-02", 0.3), row("2026-09-01", 0.8)]
    r = ribbon.ribbon(rows, ["2024/25", "2025/26"], RULE)
    assert r[0]["n_obs"] == 1 and r[0]["cells"][35] == "green"
    assert r[1]["n_obs"] == 1 and r[1]["cells"][0] == "not_green" and r[1]["longest_gap_days"] is None


def test_empty_season_is_all_no_data():
    r = ribbon.ribbon([], ["2018/19"], RULE)[0]
    assert r == {"season": "2018/19", "cells": ["no_data"] * 36, "n_obs": 0, "longest_gap_days": None,
                 "green_cycles": 0}


def test_protocol_seasons_and_d10_lock():
    cfg = base.load_config()
    assert ribbon.ribbon_seasons(cfg) == ["2018/19", "2019/20", "2020/21", "2021/22", "2022/23", "2023/24",
                                          "2024/25", "2025/26"]
    p = copy.deepcopy(cfg)
    p["protocol"]["field"].update(ndvi_green=None, min_clean_frac=None)
    with pytest.raises(ProtocolError, match="ndvi_green is null"):
        ribbon.CleanRule.from_protocol(p)
    p["protocol"]["field"].update(ndvi_green=0.5)
    with pytest.raises(ProtocolError, match="min_clean_frac is null"):
        ribbon.CleanRule.from_protocol(p)
    p["protocol"]["field"].update(min_clean_frac=0.5)
    assert ribbon.CleanRule.from_protocol(p) == ribbon.CleanRule(0.5, 0.5, 20)
    p["protocol"]["field"].update(ndvi_green=True)
    with pytest.raises(ProtocolError):
        ribbon.CleanRule.from_protocol(p)
    with pytest.raises(ValueError):
        ribbon.CleanRule(0.5, 0.0, 20)


def test_csv_round_trip(tmp_path):
    rows = ribbon.ribbon(FIX["rows"], ["2024/25", FIX["season"]], RULE)
    back = ribbon.read_csv(ribbon.write_csv(tmp_path / "field_x1_ribbon.csv", rows))
    assert back == rows


# "No green" count (SA-D72.4, protocol.ribbon_counts.no_green) --------------------------------

def _row(season, not_green, no_data):
    cells = ["not_green"] * not_green + ["no_data"] * no_data
    return {"season": season, "cells": cells + ["green"] * (36 - len(cells))}


def test_no_green_median_counts_not_green_only():
    rows = [_row("2018/19", 10, 20), _row("2019/20", 12, 0), _row("2020/21", 30, 5)]
    out = ribbon.no_green_count(rows)
    assert out["median"] == 12 and out["of"] == 36 and out["seasons"] == 3       # no_data never counts
    assert out["per_season"][0] == {"season": "2018/19", "not_green": 10, "no_data": 20}


def test_no_green_median_of_an_even_count():
    assert ribbon.no_green_count([_row("a", 10, 0), _row("b", 13, 0)])["median"] == 11.5
    out = ribbon.no_green_count([_row("a", 10, 0), _row("b", 14, 0)])["median"]
    assert out == 12 and isinstance(out, int)
    assert ribbon.no_green_count([])["median"] is None


def test_no_green_refuses_a_short_or_unknown_row():
    with pytest.raises(ValueError):
        ribbon.no_green_count([{"season": "a", "cells": ["green"] * 35}])
    with pytest.raises(ValueError):
        ribbon.no_green_count([{"season": "a", "cells": ["bare_soil"] * 36}])

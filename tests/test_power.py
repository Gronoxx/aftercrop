# Written with an SI tool (Claude, Anthropic) for NASA Space Apps 2026. See AI_USE.md.
"""Criterion M1.3: IMERG in a separate call with time-standard=UTC, -999 becomes
missing and never zero, source recorded by stretch."""

import json

import numpy as np
import pandas as pd
import pytest

from pipeline.adapters import base, power

SITES = {"londrina", "floresta", "cafelandia"}
MET = ["T2M_MAX", "T2M_MIN", "T2MDEW", "WS2M", "ALLSKY_SFC_SW_DWN", "PRECTOTCORR"]


def test_imerg_is_a_separate_utc_call():
    specs = power.specs()
    assert {s["site"] for s in specs} == SITES
    for site in SITES:
        mine = {s["call"]: s for s in specs if s["site"] == site}
        assert set(mine) == {"met", "imerg"}
        assert mine["imerg"]["parameters"] == ["IMERG_PRECTOT"]
        assert mine["imerg"]["time_standard"] == "UTC"
        assert "IMERG_PRECTOT" not in mine["met"]["parameters"]
        assert mine["met"]["parameters"] == MET
        assert (mine["met"]["start"], mine["met"]["end"]) == ("1984-01-01", "2025-12-31")
        assert mine["met"]["community"] == "AG"
        _, params, _ = power.request(mine["imerg"])
        assert params["time-standard"] == "UTC"


def test_fill_value_becomes_missing_never_zero(tmp_path):
    doc = {
        "properties": {"parameter": {"IMERG_PRECTOT": {"20000101": -999.0, "20000102": 0.0, "20000103": 4.5}}},
        "header": {"fill_value": -999.0},
    }
    p = tmp_path / "x.json"
    p.write_text(json.dumps(doc))
    s = power.read(p)["IMERG_PRECTOT"]
    assert np.isnan(s.loc["2000-01-01"])
    assert s.loc["2000-01-02"] == 0.0
    assert s.loc["2000-01-03"] == 4.5


def test_source_segments_bisects_one_change(monkeypatch):
    change = pd.Timestamp("2001-01-01")
    calls = []

    def fake_probe(spec, param, day):
        calls.append(day)
        return ("SRB",) if day < change else ("SYN1DEG",)

    monkeypatch.setattr(power, "_probe", fake_probe)
    spec = {"parameters": ["ALLSKY_SFC_SW_DWN"], "start": "1984-01-01", "end": "2025-12-31"}
    segs = power.source_segments(spec)
    assert [(s["start"], s["end"], s["sources"]) for s in segs] == [
        ("1984-01-01", "2000-12-31", ["SRB"]),
        ("2001-01-01", "2025-12-31", ["SYN1DEG"]),
    ]
    assert len(calls) < 20


# Cached series ---------------------------------------------------------------

def cached(spec):
    url, params, name = power.request(spec)
    path = base.raw_path(spec["source_id"], url, params, name)
    if not path.exists():
        pytest.skip(f"POWER not cached, run `make power` first: {path.name}")
    return path


@pytest.mark.data
@pytest.mark.parametrize("spec", power.specs(), ids=lambda s: f"{s['site']}-{s['call']}")
def test_cached_series(spec):
    path = cached(spec)
    assert base.verify(path)
    meta = base.read_meta(path)
    assert meta["params"]["time-standard"] == spec["time_standard"]
    df = power.read(path)
    assert list(df.columns) == spec["parameters"]
    assert df.index.min() == pd.Timestamp(spec["start"]) and df.index.max() == pd.Timestamp(spec["end"])
    assert not (df == -999).any().any()
    assert (df.dropna() >= -90).all().all()  # no fill value survives as a number


@pytest.mark.data
def test_imerg_gap_is_missing_not_zero():
    spec = next(s for s in power.specs() if s["site"] == "londrina" and s["call"] == "imerg")
    s = power.read(cached(spec))["IMERG_PRECTOT"]
    first = s.first_valid_index()
    assert first is not None and first.year >= 1998
    before = s[s.index < first]
    assert before.isna().all() and len(before) > 0


@pytest.mark.data
@pytest.mark.parametrize("call", ["met", "imerg"])
def test_sources_by_stretch_cover_the_period(offline_session, call):
    spec = next(s for s in power.specs() if s["site"] == "londrina" and s["call"] == call)
    try:
        segs = power.source_segments(spec)
    except base.OfflineCacheMiss as e:
        pytest.skip(f"source probes not cached, run `make power` first: {e}")
    for param in spec["parameters"]:
        mine = [s for s in segs if s["parameter"] == param]
        assert mine[0]["start"] == spec["start"] and mine[-1]["end"] == spec["end"]
        for a, b in zip(mine, mine[1:]):
            assert pd.Timestamp(b["start"]) - pd.Timestamp(a["end"]) == pd.Timedelta(days=1)

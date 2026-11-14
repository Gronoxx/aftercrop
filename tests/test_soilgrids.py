# Written with an SI tool (Claude, Anthropic) for NASA Space Apps 2026. See AI_USE.md.
"""Criterion M1.5 (conversion and no data) and the WCS access test at Londrina."""

import math

import numpy as np
import pytest

from pipeline.adapters import base, soilgrids


def test_conversions_and_nodata():
    raw = np.array([410, 216, soilgrids.NODATA])
    clay, unit = soilgrids.convert("clay", raw)
    assert unit == "%" and clay[0] == 41.0 and clay[1] == 21.6 and np.isnan(clay[2])
    sand, unit = soilgrids.convert("sand", raw)
    assert unit == "%" and sand[1] == 21.6
    soc, unit = soilgrids.convert("soc", np.array([365, soilgrids.NODATA]))
    assert unit == "g/kg" and soc[0] == 36.5 and np.isnan(soc[1])


def test_homolosine_sinusoidal_zone():
    R = soilgrids.IGH_R
    assert soilgrids.igh_xy(0.0, 0.0) == (0.0, 0.0)
    # On a lobe's central meridian x does not depend on latitude.
    for lat in (-35.0, -23.2, -5.0):
        x, y = soilgrids.igh_xy(lat, -60.0)
        assert math.isclose(x, R * math.radians(-60.0))
        assert math.isclose(y, R * math.radians(lat))
    # Parana sits in the southern lobe centred on -60.
    x, _ = soilgrids.igh_xy(-23.2, -51.18)
    assert R * math.radians(-60.0) < x < R * math.radians(-51.18)
    with pytest.raises(NotImplementedError):
        soilgrids.igh_xy(-45.0, -60.0)


def test_requests():
    specs = soilgrids.specs()
    native = [s for s in specs if s["request"] == "GetCoverageNative"]
    geo = [s for s in specs if s["request"] == "GetCoverage"]
    assert native and geo
    _, p, _ = soilgrids.request(native[0])
    assert "SUBSETTINGCRS" not in p and p["SUBSET"][0].startswith("x(")
    _, p, _ = soilgrids.request(geo[0])
    assert p["SUBSETTINGCRS"].endswith("/4326") and p["SUBSET"][0].startswith("long(")
    assert p["VERSION"] == "2.0.1" and p["map"] == "/map/clay.map"


@pytest.mark.data
@pytest.mark.parametrize("spec", [s for s in soilgrids.specs() if s["request"] != "DescribeCoverage"],
                         ids=lambda s: f"{s['coverage']}-{s['request']}")
def test_cached_coverage(spec):
    url, params, name = soilgrids.request(spec)
    path = base.raw_path(spec["source_id"], url, params, name)
    if not path.exists():
        pytest.skip("SoilGrids not cached, run `make soilgrids` first")
    assert base.verify(path)
    raw = soilgrids.read(path)
    assert raw.shape == (4, 4)
    vals, _ = soilgrids.convert(spec["property"], raw)
    assert np.isfinite(vals).all() and (vals >= 0).all()
    if spec["property"] in ("clay", "sand"):
        assert (vals <= 100).all()


@pytest.mark.data
def test_native_and_geographic_boxes_agree_at_the_point():
    by_req = {s["request"]: s for s in soilgrids.specs() if s["coverage"] == "clay_0-5cm_mean"}
    vals = {}
    for req in ("GetCoverage", "GetCoverageNative"):
        url, params, name = soilgrids.request(by_req[req])
        path = base.raw_path(by_req[req]["source_id"], url, params, name)
        if not path.exists():
            pytest.skip("SoilGrids not cached, run `make soilgrids` first")
        raw = soilgrids.read(path)
        vals[req] = raw[2, 2]
    assert vals["GetCoverage"] == vals["GetCoverageNative"]

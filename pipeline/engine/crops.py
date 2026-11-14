# Written with an SI tool (Claude, Anthropic) for NASA Space Apps 2026. See AI_USE.md.
"""Library GDD crops with a closed list of overrides.

aquacrop.Crop silently drops unknown kwargs, so the whitelist is enforced
here and every override is read back from the object.
"""
from __future__ import annotations

from datetime import date

from aquacrop import Crop

LIBRARY_NAME = {
    "soybean": "SoybeanGDD",
    "maize": "MaizeGDD",
    "wheat": "WheatGDD",
}

# Zmax base 1.0 m. Heat ramp: the code reads Tmax_lo as onset and Tmax_up as
# full failure, the library ships them swapped (45/40), which disables it.
BASE_OVERRIDES = {
    "soybean": {"Zmax": 1.0, "Tmax_lo": 40.0, "Tmax_up": 45.0},
    "maize": {"Zmax": 1.0, "Tmax_lo": 40.0, "Tmax_up": 45.0},
    "wheat": {"Zmax": 1.0, "Tmax_lo": 35.0, "Tmax_up": 40.0},
}

# Cycle milestones (GDD), rooting depth and heat ramp only.
ALLOWED = frozenset({
    "Emergence", "MaxRooting", "Senescence", "Maturity", "HIstart",
    "Flowering", "YldForm", "Zmax", "Tmax_lo", "Tmax_up",
})


def make_crop(kind: str, sowing: date, *, library_zmax: bool = False,
              **overrides: float) -> Crop:
    """library_zmax drops the Zmax 1.0 base override (side run only)."""
    if kind not in LIBRARY_NAME:
        raise KeyError(f"unknown crop {kind!r}")
    unknown = set(overrides) - ALLOWED
    if unknown:
        raise KeyError(f"override not allowed: {sorted(unknown)}")
    base = dict(BASE_OVERRIDES[kind])
    if library_zmax:
        if "Zmax" in overrides:
            raise ValueError("library_zmax and a Zmax override exclude each other")
        del base["Zmax"]
    params = {**base, **overrides}
    crop = Crop(LIBRARY_NAME[kind], planting_date=sowing.strftime("%m/%d"), **params)
    for key, value in params.items():
        if getattr(crop, key) != value:
            raise ValueError(f"override {key} not applied")
    return crop

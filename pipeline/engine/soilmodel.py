# Written with an SI tool (Claude, Anthropic) for NASA Space Apps 2026. See AI_USE.md.
"""Soil profile with one compartment geometry shared by every crop.

The geometry must not depend on the crop: AquaCrop-OSPy grows the bottom
compartments in place when Zmax + 0.1 exceeds the profile depth, and the
carried water would then be interpolated onto a different grid.
"""
from __future__ import annotations

from typing import Sequence

import numpy as np
from aquacrop import Soil

# 14 compartments, 2.5 m. 2.5 rather than 2.4 keeps Zmax + 0.1 <= depth
# even for the library default Zmax (2.3 m maize) used as sensitivity.
DZ: tuple[float, ...] = (0.1,) * 5 + (0.2,) * 5 + (0.25,) * 4
MIN_DEPTH_M = 2.4

# (thickness_m, sand_pct, clay_pct, organic_matter_pct[, ksat_mm_day]).
# Test texture for a very clayey profile, not SoilGrids data. Last thickness
# is ignored: the last layer always runs to the bottom of the profile.
# ksat_mm_day replaces only the texture Ksat of that layer (None: texture).
CLAY_TEST_LAYERS: tuple[tuple[float, float, float, float], ...] = (
    (0.3, 15.0, 65.0, 2.5),
    (0.0, 12.0, 70.0, 1.0),
)

Layer = tuple  # (thickness, sand, clay, om) or (thickness, sand, clay, om, ksat_mm_day)


def depth_m(dz: Sequence[float] = DZ) -> float:
    return float(np.round(np.sum(dz), 6))


def midpoints(dz: Sequence[float] = DZ) -> np.ndarray:
    """Compartment mid depths (m), the points used to hand water over."""
    bottoms = np.round(np.cumsum(dz), 2)
    return bottoms - np.asarray(dz) / 2


def make_soil(layers: Sequence[Layer], dz: Sequence[float] = DZ, *,
              cn: float | None = None, rew: float | None = None) -> Soil:
    """New Soil for one instance. OSPy mutates Soil in place, never reuse it.

    Base run: CN from Ksat (calc_cn=1) and REW from texture (adj_rew=0).
    cn and rew fix them instead, for the protocol side run only.
    """
    if depth_m(dz) < MIN_DEPTH_M:
        raise ValueError(f"profile depth {depth_m(dz)} m < {MIN_DEPTH_M} m")
    if not layers:
        raise ValueError("at least one soil layer is required")
    opts = {"calc_cn": 1, "adj_rew": 0}
    if cn is not None:
        opts.update(calc_cn=0, cn=float(cn))
    if rew is not None:
        opts.update(adj_rew=1, rew=float(rew))
    soil = Soil("custom", dz=list(dz), **opts)
    for i, (thick, sand, clay, om, *rest) in enumerate(layers):
        last = i == len(layers) - 1
        # OSPy compares cumulative depths without rounding: pad to avoid
        # float misses at compartment boundaries.
        t = depth_m(dz) + 1.0 if last else thick + 1e-6
        wp, fc, sat, ksat = soil.calculate_soil_hydraulic_properties(sand / 100, clay / 100, om)
        if rest and rest[0] is not None:
            ksat = float(rest[0])
        # add_layer derives tau from Ksat; CN follows Ksat of compartment 1.
        soil.add_layer(t, wp, fc, sat, ksat, 100)
    if soil.profile["Layer"].isna().any():
        raise ValueError("soil layers do not cover every compartment")
    return soil


def water_limits(layers: Sequence[Layer], dz: Sequence[float] = DZ) -> tuple[np.ndarray, np.ndarray]:
    """Per-compartment wilting point and field capacity (m3/m3)."""
    prof = make_soil(layers, dz).profile
    return prof["th_wp"].to_numpy(dtype=float), prof["th_fc"].to_numpy(dtype=float)


# Texture horizons ------------------------------------------------------------

# SoilGrids 2.0 standard depths (m). The last one is extended to the bottom.
SOILGRIDS_DEPTHS_M: tuple[tuple[float, float], ...] = (
    (0.0, 0.05), (0.05, 0.15), (0.15, 0.30), (0.30, 0.60), (0.60, 1.00), (1.00, 2.00),
)
MM_H_TO_MM_DAY = 24.0

Horizon = tuple  # (top_m, bottom_m, sand_pct, clay_pct, om_pct)


def soilgrids_horizons(sand_pct: Sequence[float], clay_pct: Sequence[float],
                       soc_g_kg: Sequence[float], *, om_per_soc: float) -> list[Horizon]:
    """Six SoilGrids depths, already converted by the adapter (%, g/kg).

    om_per_soc turns organic carbon into organic matter for the texture
    function. Not in the plan: [DECIDE], usually 1.724 (van Bemmelen).
    """
    n = len(SOILGRIDS_DEPTHS_M)
    if not (len(sand_pct) == len(clay_pct) == len(soc_g_kg) == n):
        raise ValueError(f"expected {n} values per property")
    if not om_per_soc > 0:
        raise ValueError("om_per_soc must be positive")
    return [(top, bottom, float(s), float(c), float(soc) / 10.0 * om_per_soc)
            for (top, bottom), s, c, soc in zip(SOILGRIDS_DEPTHS_M, sand_pct, clay_pct, soc_g_kg)]


def _on_grid(z: float, dz: Sequence[float]) -> bool:
    edges = np.concatenate(([0.0], np.cumsum(dz)))
    return bool(np.isclose(edges, z, atol=1e-6).any())


def texture_layers(horizons: Sequence[Horizon], *, ksat_mm_h: float | None,
                   ksat_depth_m: tuple[float, float],
                   dz: Sequence[float] = DZ) -> tuple[Layer, ...]:
    """Layers on the compartment grid from texture horizons and a Ksat input.

    Each compartment takes the thickness-weighted mean texture of the
    horizons it overlaps, and the last horizon runs to the profile bottom.
    ksat_mm_h is the measured Ksat (decision D1) for the compartments inside
    ksat_depth_m, which must start at 0 and end on a compartment boundary.
    None keeps the texture Ksat everywhere (sensitivity). Consecutive equal
    compartments merge into one layer. OSPy assigns a compartment to a layer
    by its bottom depth, so layers thinner than a compartment are not allowed.
    """
    if not horizons:
        raise ValueError("no horizon")
    hz = sorted((tuple(float(x) for x in h) for h in horizons), key=lambda h: h[0])
    if not np.isclose(hz[0][0], 0.0):
        raise ValueError("the first horizon must start at 0 m")
    for a, b in zip(hz, hz[1:]):
        if not np.isclose(a[1], b[0]):
            raise ValueError(f"horizons not contiguous at {a[1]} and {b[0]} m")
    for h in hz:
        if not h[1] > h[0]:
            raise ValueError(f"empty horizon {h[:2]}")
        if not (0 <= h[2] <= 100 and 0 <= h[3] <= 100 and h[2] + h[3] <= 100 and h[4] >= 0):
            raise ValueError(f"texture out of range in horizon {h[:2]}")
    top, bottom = (float(x) for x in ksat_depth_m)
    if ksat_mm_h is not None:
        if not ksat_mm_h > 0:
            raise ValueError("ksat_mm_h must be positive or None")
        if not np.isclose(top, 0.0):
            raise ValueError("the measured Ksat layer must start at the surface (CN reads compartment 1)")
        if not _on_grid(bottom, dz) or bottom <= 0:
            raise ValueError(f"Ksat depth {bottom} m is not a compartment boundary")
    last_bottom = depth_m(dz)
    hz[-1] = (hz[-1][0], max(hz[-1][1], last_bottom), *hz[-1][2:])

    bottoms = np.round(np.cumsum(dz), 6)
    tops = bottoms - np.asarray(dz)
    comps = []
    for z0, z1 in zip(tops, bottoms):
        w = np.array([max(0.0, min(z1, h[1]) - max(z0, h[0])) for h in hz])
        if not np.isclose(w.sum(), z1 - z0):
            raise ValueError(f"horizons do not cover {z0:.2f}..{z1:.2f} m")
        sand, clay, om = (float(np.dot(w, [h[k] for h in hz]) / w.sum()) for k in (2, 3, 4))
        measured = ksat_mm_h is not None and z1 <= bottom + 1e-6
        comps.append((float(z1 - z0), round(sand, 9), round(clay, 9), round(om, 9),
                      float(ksat_mm_h) * MM_H_TO_MM_DAY if measured else None))
    layers: list[list] = []
    for c in comps:
        if layers and tuple(layers[-1][1:]) == c[1:]:
            layers[-1][0] += c[0]
        else:
            layers.append(list(c))
    return tuple((round(t, 6), *rest) for t, *rest in layers)


def storage_mm(th: np.ndarray, dz: Sequence[float] = DZ) -> float:
    """Total profile water (mm)."""
    return float(np.dot(np.asarray(th, dtype=float), np.asarray(dz)) * 1000.0)

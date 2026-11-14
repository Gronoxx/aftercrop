# Written with an SI tool (Claude, Anthropic) for NASA Space Apps 2026. See AI_USE.md.
"""Share of the annual rainfall erosivity that falls on days without a live crop (plan 10.9, 7.5).

E_i is the part of the annual R that falls on the no-crop days of option i, by the declared
calendar (never by HLS): the sum, over those days, of the GloREDa monthly fraction divided
by the days of the month. The screen shows only a class against S1: "similar" up to
``protocol.erosion.similar_within_pct_points`` percentage points of the year, "lower" or
"higher" beyond (project rule, 5 points). Never t/ha. No cover factor b, no straw bucket,
and no K, LS or P: they cancel between rotations on the same field.

Arithmetic is exact (``fractions.Fraction``), so a difference of exactly 5 points is
"similar", whatever the float inputs.

Sanity (10.9): the pixel of Londrina against 7,945 MJ mm/ha/h/year. When it diverges the
module warns and keeps using only the monthly fractions. The plan gives no tolerance,
so it is an explicit argument ([DECIDE]).
"""

from __future__ import annotations

import calendar as _cal
import math
from collections.abc import Mapping, Sequence
from datetime import date
from fractions import Fraction
from typing import Any

from pipeline.decision import calendar as cal
from pipeline.decision import strategies

CLASSES = ("reference", "lower", "similar", "higher")
LONDRINA_REFERENCE_R = 7945          # MJ mm/ha/h/year, plan 10.9 (redalyc 180235588026)
SOURCE_ID = "gloreda"                # GloREDa monthly erosivity, published climatology


class ErosivityError(ValueError):
    pass


def _exact(x: Any, what: str) -> Fraction:
    if isinstance(x, bool) or not isinstance(x, (int, float, Fraction)):
        raise ErosivityError(f"{what} is not a number: {x!r}")
    if isinstance(x, float) and not math.isfinite(x):
        raise ErosivityError(f"{what} is not finite: {x!r}")
    return Fraction(x)


def monthly_fractions(monthly_r: Sequence[Any] | Mapping[int, Any]) -> dict[int, Fraction]:
    """Fraction of the annual R in each month, from 12 monthly values (Jan to Dec).

    Units cancel: only the shares are used.
    """
    if isinstance(monthly_r, Mapping):
        if sorted(monthly_r) != list(range(1, 13)):
            raise ErosivityError("monthly R needs months 1 to 12")
        vals = [monthly_r[m] for m in range(1, 13)]
    else:
        vals = list(monthly_r)
        if len(vals) != 12:
            raise ErosivityError(f"monthly R needs 12 values, got {len(vals)}")
    exact = [_exact(v, f"monthly R of month {i}") for i, v in enumerate(vals, 1)]
    if any(v < 0 for v in exact):
        raise ErosivityError("monthly R cannot be negative")
    total = sum(exact)
    if total <= 0:
        raise ErosivityError("annual R must be positive")
    return {m: v / total for m, v in enumerate(exact, 1)}


def share_on_days(days: Sequence[date], fractions: Mapping[int, Fraction]) -> Fraction:
    """Sum over ``days`` of the monthly fraction divided by the days of that month."""
    if len(set(days)) != len(days):
        raise ErosivityError("a day is counted twice")
    return sum((fractions[d.month] / _cal.monthrange(d.year, d.month)[1] for d in days), Fraction(0))


def classify(e: Fraction, e_ref: Fraction, similar_within_pct_points: Any) -> str:
    """Class of E_i against S1: |difference| up to the limit, in points of the year, is "similar"."""
    limit = _exact(similar_within_pct_points, "similar_within_pct_points") / 100
    if limit < 0:
        raise ErosivityError("the similar band cannot be negative")
    diff = Fraction(e) - Fraction(e_ref)
    if abs(diff) <= limit:
        return "similar"
    return "lower" if diff < 0 else "higher"


def erosivity(columns: Mapping[str, Sequence[Mapping[str, Any]]], monthly_r: Sequence[Any] | Mapping[int, Any],
              *, similar_within_pct_points: Any) -> dict:
    """``{"classes": {S1: "reference", S2, S3}, "shares": {S1, S2, S3}}``, shares as Fractions of the year."""
    fr = monthly_fractions(monthly_r)
    shares = {sid: share_on_days(cal.no_crop_days(columns[sid]), fr) for sid in strategies.STRATEGY_IDS}
    classes = {"S1": "reference"}
    for sid in strategies.OPTION_IDS:
        classes[sid] = classify(shares[sid], shares["S1"], similar_within_pct_points)
    return {"classes": classes, "shares": shares}


def sanity(annual_r_pixel: Any, *, tolerance: Any, reference: Any = LONDRINA_REFERENCE_R) -> dict:
    """Londrina pixel against the published annual R. ``tolerance`` is a relative difference ([DECIDE]).

    A divergence only warns (``diverges``, the text is copy): the comparator keeps using the
    monthly fractions.
    """
    px = _exact(annual_r_pixel, "annual R of the pixel")
    ref = _exact(reference, "reference annual R")
    tol = _exact(tolerance, "tolerance")
    if ref <= 0 or tol < 0:
        raise ErosivityError("reference must be positive and tolerance not negative")
    rel = abs(px - ref) / ref
    diverges = rel > tol
    return {"pixel": float(px), "reference": float(ref), "relative_difference": float(rel), "diverges": diverges}


def pct(share: Fraction) -> float:
    """Share of the year in percent, for the ledger record (never on screen as t/ha)."""
    return float(share * 100)

# Written with an SI tool (Claude, Anthropic) for NASA Space Apps 2026. See AI_USE.md.
"""Bench statistics: Kendall S, its exact null, RMSE and the sums behind it.

All arithmetic runs on exact rationals (``fractions.Fraction``). A float is read
through its shortest decimal form, so 279.9 is 2799/10, not the binary value
next to it. Boundary cases (RMSE 279.9 against 280, S 20 against 21) are then
decided as written, never by rounding noise. Square roots only appear in the
reported RMSE; every comparison is made on squared values.

Plan, sections 11.2 and 11.3, sheet M4 (``stats.py``) and criterion M4.5.
"""

from __future__ import annotations

import math
from collections.abc import Hashable, Iterable, Sequence
from dataclasses import dataclass
from decimal import Decimal
from fractions import Fraction
from numbers import Rational

Number = int | float | Decimal | Fraction | str


# Exact numbers ------------------------------------------------------------------

def exact(value: Number) -> Fraction:
    """Exact rational for an input number. Rejects bool, None, NaN and infinity."""
    if isinstance(value, bool) or value is None:
        raise TypeError(f"not a number: {value!r}")
    if isinstance(value, Fraction):
        return value
    if isinstance(value, Rational):  # int and other exact rationals
        return Fraction(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"not a finite number: {value!r}")
        return Fraction(repr(value))
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError(f"not a finite number: {value!r}")
        return Fraction(value)
    if isinstance(value, str):
        text = value.strip().replace(",", ".")
        if not text:
            raise ValueError("empty number")
        return Fraction(text)  # rejects "nan" and "inf"
    raise TypeError(f"not a number: {value!r}")


def exact_all(values: Iterable[Number]) -> list[Fraction]:
    return [exact(v) for v in values]


def sqrt_float(value: Fraction) -> float:
    """Square root of a non-negative rational, as a float, for reporting only."""
    if value < 0:
        raise ValueError("negative value under the square root")
    return math.sqrt(float(value))


# Kendall S ----------------------------------------------------------------------

def _sign(v: Fraction) -> int:
    return (v > 0) - (v < 0)


def n_pairs(n: int) -> int:
    return n * (n - 1) // 2


def kendall_s(x: Sequence[Number], y: Sequence[Number]) -> int:
    """S = concordant pairs minus discordant pairs. A tie in x or y counts zero."""
    if len(x) != len(y):
        raise ValueError(f"lengths differ: {len(x)} and {len(y)}")
    a, b = exact_all(x), exact_all(y)
    n = len(a)
    return sum(
        _sign(a[i] - a[j]) * _sign(b[i] - b[j])
        for i in range(n)
        for j in range(i + 1, n)
    )


def kendall_tau_a(s: int, n: int) -> Fraction:
    """tau = S / (n (n - 1) / 2). With n = 10 and S = 21, tau = 21/45."""
    if n < 2:
        raise ValueError("Kendall tau needs at least two items")
    return Fraction(s, n_pairs(n))


def kendall_s_by_group(
    groups: Sequence[Hashable], x: Sequence[Number], y: Sequence[Number]
) -> dict[Hashable, int]:
    """S computed inside each group (here: inside each trial site), in input order."""
    if not (len(groups) == len(x) == len(y)):
        raise ValueError("groups, x and y must have the same length")
    out: dict[Hashable, int] = {}
    for g in dict.fromkeys(groups):
        idx = [i for i, gi in enumerate(groups) if gi == g]
        out[g] = kendall_s([x[i] for i in idx], [y[i] for i in idx])
    return out


# Exact null distribution of S (no ties) ------------------------------------------

def inversion_counts(n: int) -> list[int]:
    """Number of permutations of n items with k inversions, for k = 0 .. n(n-1)/2.

    Exact enumeration of all n! orders, grouped by inversion count (the Mahonian
    numbers, built one item at a time: the new item adds 0 to m - 1 inversions).
    The test suite checks it against brute force over the permutations.
    """
    if n < 0:
        raise ValueError("n must be non-negative")
    counts = [1]
    for m in range(1, n + 1):
        new = [0] * (len(counts) + m - 1)
        for k, c in enumerate(counts):
            for added in range(m):
                new[k + added] += c
        counts = new
    return counts


def kendall_s_null(n: int) -> dict[int, int]:
    """Exact null distribution of S without ties: {S: number of orders}.

    With x in its natural order and y a permutation, discordant pairs are the
    inversions of y, so S = n(n-1)/2 - 2 * inversions.
    """
    total = n_pairs(n)
    return {total - 2 * k: c for k, c in enumerate(inversion_counts(n))}


def kendall_p_upper(s: int, n: int) -> Fraction:
    """One-sided exact P(S >= s) under the null of no association, without ties.

    Calibrates the rule only (section 11.3: P(S >= 21) = 0.036 for n = 10). It is
    never shown on the page.
    """
    null = kendall_s_null(n)
    return Fraction(sum(c for v, c in null.items() if v >= s), math.factorial(n))


# Sums and RMSE ------------------------------------------------------------------

@dataclass(frozen=True)
class Sums:
    """The sums behind the RMSE thresholds (section 11.3)."""

    n: int
    total: Fraction            # sum of values
    total_sq: Fraction         # sum of squares
    ss_about_mean: Fraction    # sum of squares about the mean

    @property
    def mean(self) -> Fraction:
        return self.total / self.n


def sums(values: Sequence[Number]) -> Sums:
    v = exact_all(values)
    if not v:
        raise ValueError("no values")
    n = len(v)
    total = sum(v, Fraction(0))
    total_sq = sum((x * x for x in v), Fraction(0))
    ss = total_sq - total * total / n
    return Sums(n=n, total=total, total_sq=total_sq, ss_about_mean=ss)


def mse(simulated: Sequence[Number], observed: Sequence[Number]) -> Fraction:
    """Mean squared error, exact."""
    if len(simulated) != len(observed):
        raise ValueError(f"lengths differ: {len(simulated)} and {len(observed)}")
    if not observed:
        raise ValueError("no values")
    s, o = exact_all(simulated), exact_all(observed)
    return sum(((a - b) ** 2 for a, b in zip(s, o)), Fraction(0)) / len(o)


def rmse(simulated: Sequence[Number], observed: Sequence[Number]) -> float:
    return sqrt_float(mse(simulated, observed))


def mse_zero(observed: Sequence[Number]) -> Fraction:
    """Null delta = 0: sum of squares / n (section 11.3, about 365 for the trials)."""
    s = sums(observed)
    return s.total_sq / s.n


def mse_constant(observed: Sequence[Number]) -> Fraction:
    """In-sample constant (the mean): sum of squares about the mean / n.

    Its square root is the T1 threshold (section 11.3, about 280). A constant
    prediction reaches it exactly, so it never passes "smaller than".
    """
    s = sums(observed)
    return s.ss_about_mean / s.n


def mse_constant_loo(observed: Sequence[Number]) -> Fraction:
    """Leave-one-out constant: each value predicted by the mean of the others.

    Reported next to the threshold (section 11.3, about 311). Equals
    mse_constant * (n / (n - 1))**2.
    """
    v = exact_all(observed)
    n = len(v)
    if n < 2:
        raise ValueError("leave-one-out needs at least two values")
    total = sum(v, Fraction(0))
    return sum(((x - (total - x) / (n - 1)) ** 2 for x in v), Fraction(0)) / n


# Percentiles ---------------------------------------------------------------------

PERCENTILE_METHODS = ("nearest_rank", "linear")


def percentile(values: Sequence[Number], q: Number, method: str) -> Fraction:
    """The q-th percentile (0 < q < 100) of the values, exactly.

    - "nearest_rank": the smallest value with at least q% of the values at or
      below it, x[ceil(q n / 100)] counting from 1. Always an observed value.
    - "linear": interpolation between order statistics at position
      (n - 1) q / 100 counting from 0 (Hyndman and Fan type 7, the numpy default).

    The protocol must name the method (N1 and R1); none is assumed here.
    """
    if method not in PERCENTILE_METHODS:
        raise ValueError(f"percentile method must be one of {PERCENTILE_METHODS}, got {method!r}")
    v = sorted(exact_all(values))
    if not v:
        raise ValueError("no values")
    level = exact(q)
    if not 0 < level < 100:
        raise ValueError(f"percentile level must lie in (0, 100), got {level}")
    n = len(v)
    if method == "nearest_rank":
        rank = math.ceil(level * n / 100)
        return v[max(rank, 1) - 1]
    h = (n - 1) * level / 100
    lo = math.floor(h)
    if lo + 1 >= n:
        return v[-1]
    return v[lo] + (h - lo) * (v[lo + 1] - v[lo])


def median(values: Sequence[Number]) -> Fraction:
    """Middle value; the mean of the two middle values when n is even."""
    v = sorted(exact_all(values))
    if not v:
        raise ValueError("no values")
    n = len(v)
    mid = n // 2
    return v[mid] if n % 2 else (v[mid - 1] + v[mid]) / 2


def count_at_least(values: Iterable[Number], reference: Number) -> int:
    """How many values are >= reference. A tie counts (ties go against the model)."""
    r = exact(reference)
    return sum(1 for v in values if exact(v) >= r)

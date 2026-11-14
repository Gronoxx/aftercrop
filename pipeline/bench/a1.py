# Written with an SI tool (Claude, Anthropic) for NASA Space Apps 2026. See AI_USE.md.
"""A1, with and without carried-over water: does inherited water explain T1?

Same dates, with a fixed restart in place of the inherited water (never zero
water), at the start of each instance, after the predecessor's harvest
(plan, section 11.2). Fixed at the tag:

- Statistic: D = S of T1 with state minus S of T1 without state.
- Null: with and without state are exchangeable inside each site-season.
  Alternative, one-sided: D > 0.
- Paired swap over all 2^n patterns (1,024 for the 10 site-seasons), with no
  random draw: each pattern swaps the two simulated Delta of the chosen
  site-seasons. Pattern 0 is the real assignment, so it always counts.
- p = number of patterns with D* >= D / 2^n. A tie counts against.
- Passes with D > 0 and p <= the protocol level (0.10, the level of P1).

This module only holds the logic: the A1 chains run elsewhere.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from fractions import Fraction
from typing import Any

from pipeline.bench import common, stats
from pipeline.bench.common import EXPLORATORY, FAIL, PASS


@dataclass(frozen=True)
class A1Rule:
    alpha: Fraction
    n_site_seasons: int

    @property
    def n_patterns(self) -> int:
        return 2 ** self.n_site_seasons

    @classmethod
    def from_protocol(cls, protocol: Mapping[str, Any]) -> "A1Rule":
        """Reads ``protocol.tests.A1``: ``alpha``, ``ties`` and ``permutation``."""
        sec = common.protocol_section(protocol, "A1")
        where = "tests.A1"
        common.require_ties_against(sec, where)
        common.require_no_draws(sec, where)
        rule = cls(alpha=common.require_level(sec, "alpha", where),
                   n_site_seasons=common.site_seasons_required(protocol))
        perm = common.require(sec, "permutation", where)
        if not isinstance(perm, Mapping):
            raise common.ProtocolError(f"protocol.{where}.permutation must be a mapping")
        if perm.get("kind") != "paired_swap" or perm.get("exhaustive") is not True:
            raise common.ProtocolError(f"protocol.{where}.permutation must be an exhaustive paired_swap")
        if perm.get("patterns") != rule.n_patterns:
            raise common.ProtocolError(
                f"protocol.{where}.permutation.patterns must be 2^{rule.n_site_seasons} = {rule.n_patterns}")
        return rule


@dataclass(frozen=True)
class A1Record:
    """One site-season: observed Delta and the two simulated Delta."""

    site: str
    season: str
    observed: Any
    with_state: Any
    without_state: Any

    @property
    def key(self) -> tuple[str, str]:
        return (self.site, self.season)


def d_statistic(observed: Sequence[Any], with_state: Sequence[Any], without_state: Sequence[Any]) -> int:
    return stats.kendall_s(with_state, observed) - stats.kendall_s(without_state, observed)


def swap_distribution(observed: Sequence[Any], with_state: Sequence[Any], without_state: Sequence[Any]) -> list[int]:
    """D* for every swap pattern. Bit i of the pattern index set means that
    site-season i has its two simulated values exchanged. Index 0 is the real D."""
    n = len(observed)
    if not (len(with_state) == len(without_state) == n):
        raise ValueError("observed, with_state and without_state must have the same length")
    obs = common.exact_observed(observed)
    a = common.exact_simulated(with_state, "A1 value with state")
    b = common.exact_simulated(without_state, "A1 value without state")
    out = []
    for mask in range(2 ** n):
        arm1 = [b[i] if mask >> i & 1 else a[i] for i in range(n)]
        arm2 = [a[i] if mask >> i & 1 else b[i] for i in range(n)]
        out.append(stats.kendall_s(arm1, obs) - stats.kendall_s(arm2, obs))
    return out


def p_value(n_at_least: int, n_patterns: int) -> Fraction:
    return Fraction(n_at_least, n_patterns)


def decide(d: int, n_at_least: int, n: int, rule: A1Rule) -> tuple[str, Fraction]:
    """Status and p. D must be strictly positive, p at most the level."""
    if n > rule.n_site_seasons:
        raise ValueError(f"{n} site-seasons, the protocol fixes {rule.n_site_seasons}")
    n_patterns = 2 ** n
    if not 1 <= n_at_least <= n_patterns:
        raise ValueError("the real assignment always counts: 1 <= n_at_least <= 2^n")
    p = p_value(n_at_least, n_patterns)
    if n < rule.n_site_seasons:
        return EXPLORATORY, p
    return (PASS if d > 0 and p <= rule.alpha else FAIL), p


@dataclass(frozen=True)
class A1Result:
    status: str
    n: int
    n_required: int
    d: int
    s_with_state: int
    s_without_state: int
    n_at_least: int
    n_patterns: int
    p: Fraction

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": "A1",
            "status": self.status,
            "n": self.n,
            "n_required": self.n_required,
            "statistic": "d_kendall_s_with_minus_without_state",
            "d": self.d,
            "s_with_state": self.s_with_state,
            "s_without_state": self.s_without_state,
            "n_at_least": self.n_at_least,
            "n_patterns": self.n_patterns,
            "p": float(self.p),
        }


def evaluate(records: Sequence[A1Record], rule: A1Rule) -> A1Result:
    if len(records) < 2:
        raise ValueError("A1 needs at least two site-seasons")
    common.check_keys([r.key for r in records])
    obs = [r.observed for r in records]
    with_state = [r.with_state for r in records]
    without_state = [r.without_state for r in records]
    dist = swap_distribution(obs, with_state, without_state)
    d = dist[0]
    n_ge = stats.count_at_least(dist, d)
    status, p = decide(d, n_ge, len(records), rule)
    return A1Result(
        status=status,
        n=len(records),
        n_required=rule.n_site_seasons,
        d=d,
        s_with_state=stats.kendall_s(with_state, obs),
        s_without_state=stats.kendall_s(without_state, obs),
        n_at_least=n_ge,
        n_patterns=len(dist),
        p=p,
    )

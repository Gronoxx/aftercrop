# Written with an SI tool (Claude, Anthropic) for NASA Space Apps 2026. See AI_USE.md.
"""T1, the primary test: does the engine reproduce the difference between sequences?

Delta = soybean after wheat minus soybean after second-crop corn, simulated
against observed, in the trial site-seasons (plan, sections 11.2 to 11.4).

Rule: S >= s_min **and** RMSE of Delta strictly smaller than the RMSE of the
in-sample constant (the observed mean). With the trial values that reference is
about 280 kg/ha (section 11.3). It is computed from the observed values only
(criterion M4.4), so a constant prediction reaches it exactly and fails, as the
plan states. All thresholds come from ``protocol``; none is written here.

Fewer site-seasons than the protocol requires gives "exploratory": no approval
(section 11.4). The scoreboard says whether T1 failed on order (S), on size
(RMSE) or on both, and shows S inside each site, all of them.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from fractions import Fraction
from typing import Any

from pipeline.bench import common, stats
from pipeline.bench.common import EXPLORATORY, FAIL, PASS, Pair

RMSE_REFERENCES = ("in_sample_constant_rmse",)


@dataclass(frozen=True)
class T1Rule:
    s_min: int
    n_site_seasons: int
    rmse_reference: str

    @classmethod
    def from_protocol(cls, protocol: Mapping[str, Any]) -> "T1Rule":
        """Reads ``protocol.tests.T1``: ``s_total_min`` and ``rmse_lt``."""
        sec = common.protocol_section(protocol, "T1")
        s_min = common.require_int(sec, "s_total_min", "tests.T1", minimum=1)
        n = common.site_seasons_required(protocol)
        ref = common.require(sec, "rmse_lt", "tests.T1")
        if ref not in RMSE_REFERENCES:
            # Section 11.3 keeps the demanding in-sample constant, not leave-one-out.
            raise common.ProtocolError(f"protocol.tests.T1.rmse_lt must be one of {RMSE_REFERENCES}")
        if s_min > stats.n_pairs(n):
            raise common.ProtocolError("protocol.tests.T1.s_total_min exceeds the number of pairs")
        return cls(s_min=s_min, n_site_seasons=n, rmse_reference=ref)


@dataclass(frozen=True)
class T1Result:
    status: str                     # pass, fail or exploratory
    failed_on: str | None           # order, size, both or None
    n: int
    n_required: int
    s: int
    s_min: int
    tau: Fraction
    s_by_site: dict[str, int]
    order_ok: bool
    size_ok: bool
    mse: Fraction
    mse_threshold: Fraction         # the reference actually applied (squared)
    mse_constant: Fraction          # in-sample constant (about 280 squared)
    mse_constant_loo: Fraction      # leave-one-out constant (about 311 squared), reported only
    mse_zero: Fraction              # Delta = 0 (about 365 squared), reported only
    n_same_sign: int                # signs are reported and nothing more
    n_observed_positive: int
    notes: list[str] = field(default_factory=list)
    n_by_site: dict[str, int] = field(default_factory=dict)   # site-seasons per site

    @property
    def rmse(self) -> float:
        return stats.sqrt_float(self.mse)

    @property
    def rmse_threshold(self) -> float:
        return stats.sqrt_float(self.mse_threshold)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": "T1",
            "status": self.status,
            "t1_failed_on": self.failed_on,
            "n": self.n,
            "n_required": self.n_required,
            "statistic": "kendall_s_and_rmse",
            "s": self.s,
            "s_min": self.s_min,
            "tau": float(self.tau),
            "s_by_site": dict(self.s_by_site),
            "rmse": self.rmse,
            "rmse_threshold": self.rmse_threshold,
            "rmse_constant_in_sample": stats.sqrt_float(self.mse_constant),
            "rmse_constant_loo": stats.sqrt_float(self.mse_constant_loo),
            "rmse_zero": stats.sqrt_float(self.mse_zero),
            "n_same_sign": self.n_same_sign,
            "n_observed_positive": self.n_observed_positive,
            "notes": list(self.notes),
            "n_by_site": dict(self.n_by_site),
        }


def failed_on(order_ok: bool, size_ok: bool) -> str | None:
    if order_ok and size_ok:
        return None
    if not order_ok and not size_ok:
        return "both"
    return "order" if not order_ok else "size"


def decide(s: int, mse_model: Fraction, mse_threshold: Fraction, n: int, rule: T1Rule) -> tuple[str, str | None, bool, bool]:
    """Status, failed_on, order_ok and size_ok for given statistics.

    RMSE "smaller than" is strict and is compared on squares, exactly.
    """
    if n > rule.n_site_seasons:
        raise ValueError(f"{n} site-seasons, the protocol fixes {rule.n_site_seasons}")
    order_ok = s >= rule.s_min
    size_ok = mse_model < mse_threshold
    if n < rule.n_site_seasons:
        return EXPLORATORY, None, order_ok, size_ok
    fo = failed_on(order_ok, size_ok)
    return (PASS if fo is None else FAIL), fo, order_ok, size_ok


def threshold_mse(observed: Sequence[Fraction], rule: T1Rule) -> Fraction:
    """Squared RMSE threshold, from the observed values only (M4.4)."""
    if rule.rmse_reference != "in_sample_constant_rmse":
        raise common.ProtocolError(f"unknown RMSE reference {rule.rmse_reference!r}")
    return stats.mse_constant(observed)


def evaluate(pairs: Sequence[Pair], rule: T1Rule) -> T1Result:
    """T1 on observed and simulated Delta, one Pair per site-season.

    A site-season with no observed value (no legible axis label, for example)
    is left out by the caller and lowers n. A missing simulated value raises
    common.IncompleteInput, which the runner turns into "not_completed".
    """
    if len(pairs) < 2:
        raise ValueError("T1 needs at least two site-seasons")
    sites, obs, sim = common.split_pairs(pairs)
    n = len(obs)
    s = stats.kendall_s(sim, obs)
    m = stats.mse(sim, obs)
    m_ref = threshold_mse(obs, rule)
    status, fo, order_ok, size_ok = decide(s, m, m_ref, n, rule)
    notes = []
    if status == EXPLORATORY:
        notes.append(f"{n} of {rule.n_site_seasons} site-seasons: exploratory, no approval")
    return T1Result(
        status=status,
        failed_on=fo,
        n=n,
        n_required=rule.n_site_seasons,
        s=s,
        s_min=rule.s_min,
        tau=stats.kendall_tau_a(s, n),
        s_by_site=stats.kendall_s_by_group(sites, sim, obs),
        order_ok=order_ok,
        size_ok=size_ok,
        mse=m,
        mse_threshold=m_ref,
        mse_constant=stats.mse_constant(obs),
        mse_constant_loo=stats.mse_constant_loo(obs),
        mse_zero=stats.mse_zero(obs),
        n_same_sign=sum(1 for a, b in zip(sim, obs) if (a > 0) - (a < 0) == (b > 0) - (b < 0)),
        n_observed_positive=sum(1 for b in obs if b > 0),
        notes=notes,
        n_by_site={site: sites.count(site) for site in dict.fromkeys(sites)},
    )

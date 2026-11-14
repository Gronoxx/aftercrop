# Written with an SI tool (Claude, Anthropic) for NASA Space Apps 2026. See AI_USE.md.
"""T0, the positive control: does the engine see real yield breaks?

Reference soybean (the soybean after second-crop corn arm of T1) simulated
against the published mean, in the trial site-seasons (plan, section 11.2).

Rule: total Kendall S >= s_min **and** the sum of the S computed inside each
site strictly greater than zero. The second condition asks for agreement inside
a site, not only between sites. Thresholds come from ``protocol``. Fewer
site-seasons than required gives "exploratory", and the label then reads
"Model not validated here" (section 11.6).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from fractions import Fraction
from typing import Any

from pipeline.bench import common, stats
from pipeline.bench.common import EXPLORATORY, FAIL, PASS, Pair


@dataclass(frozen=True)
class T0Rule:
    s_min: int
    n_site_seasons: int
    within_site_sum_gt: int

    @classmethod
    def from_protocol(cls, protocol: Mapping[str, Any]) -> "T0Rule":
        """Reads ``protocol.tests.T0``: ``s_total_min`` and ``within_site_s_sum_gt``."""
        sec = common.protocol_section(protocol, "T0")
        s_min = common.require_int(sec, "s_total_min", "tests.T0", minimum=1)
        within = common.require(sec, "within_site_s_sum_gt", "tests.T0")
        if isinstance(within, bool) or not isinstance(within, int):
            raise common.ProtocolError("protocol.tests.T0.within_site_s_sum_gt must be an integer")
        n = common.site_seasons_required(protocol)
        if s_min > stats.n_pairs(n):
            raise common.ProtocolError("protocol.tests.T0.s_total_min exceeds the number of pairs")
        return cls(s_min=s_min, n_site_seasons=n, within_site_sum_gt=within)


@dataclass(frozen=True)
class T0Result:
    status: str
    n: int
    n_required: int
    s: int
    s_min: int
    tau: Fraction
    s_by_site: dict[str, int]
    within_site_sum: int
    order_ok: bool
    within_ok: bool
    n_by_site: dict[str, int] = field(default_factory=dict)   # site-seasons per site

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": "T0",
            "status": self.status,
            "n": self.n,
            "n_required": self.n_required,
            "statistic": "kendall_s",
            "s": self.s,
            "s_min": self.s_min,
            "tau": float(self.tau),
            "s_by_site": dict(self.s_by_site),
            "within_site_sum": self.within_site_sum,
            "n_by_site": dict(self.n_by_site),
        }


def decide(s: int, within_site_sum: int, n: int, rule: T0Rule) -> tuple[str, bool, bool]:
    if n > rule.n_site_seasons:
        raise ValueError(f"{n} site-seasons, the protocol fixes {rule.n_site_seasons}")
    order_ok = s >= rule.s_min
    within_ok = within_site_sum > rule.within_site_sum_gt
    if n < rule.n_site_seasons:
        return EXPLORATORY, order_ok, within_ok
    return (PASS if order_ok and within_ok else FAIL), order_ok, within_ok


def evaluate(pairs: Sequence[Pair], rule: T0Rule) -> T0Result:
    """T0 on published and simulated reference soybean, one Pair per site-season."""
    if len(pairs) < 2:
        raise ValueError("T0 needs at least two site-seasons")
    sites, obs, sim = common.split_pairs(pairs)
    n = len(obs)
    s = stats.kendall_s(sim, obs)
    by_site = stats.kendall_s_by_group(sites, sim, obs)
    within = sum(by_site.values())
    status, order_ok, within_ok = decide(s, within, n, rule)
    return T0Result(
        status=status,
        n=n,
        n_required=rule.n_site_seasons,
        s=s,
        s_min=rule.s_min,
        tau=stats.kendall_tau_a(s, n),
        s_by_site=by_site,
        within_site_sum=within,
        order_ok=order_ok,
        within_ok=within_ok,
        n_by_site={site: sites.count(site) for site in dict.fromkeys(sites)},
    )

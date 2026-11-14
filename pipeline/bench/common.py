# Written with an SI tool (Claude, Anthropic) for NASA Space Apps 2026. See AI_USE.md.
"""Shared pieces of the bench: statuses, protocol access and input records.

Statuses follow the scoreboard schema (plan, section 9.1): "pass", "fail",
"not_run", "not_completed" (infrastructure failure: code error, missing file,
time out) and "exploratory" (fewer site-seasons than the protocol requires, so
no approval is possible, section 11.4).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from fractions import Fraction
from typing import Any

from pipeline.bench import stats

PASS = "pass"
FAIL = "fail"
NOT_RUN = "not_run"
NOT_COMPLETED = "not_completed"
EXPLORATORY = "exploratory"
STATUSES = (PASS, FAIL, NOT_RUN, NOT_COMPLETED, EXPLORATORY)

# A test with no threshold (A2, A3, N1, H1) that ran: its numbers are reported
# and it can neither pass nor fail. Kept out of STATUSES on purpose, so the
# label never accepts it for T0, T1, P1 or A1. Section 8.6 lists five statuses
# only, so this sixth value waits for the participant's decision.
REPORTED = "reported"


class ProtocolError(ValueError):
    """The protocol block lacks a key, or holds a value the plan does not allow."""


class IncompleteInput(ValueError):
    """A simulated value is missing or not a number.

    The task runner records this as "not_completed" (infrastructure failure).
    Engine outcomes (zeroed yield, no emergence, dead canopy) are not missing
    values: they arrive as numbers, usually a yield of 0, and count against the
    model (section 11.6).
    """


def protocol_block(protocol: Mapping[str, Any]) -> Mapping[str, Any]:
    """The protocol block, from the whole YAML document (top-level ``protocol:``)
    or from the block itself."""
    block = protocol.get("protocol", protocol) if isinstance(protocol, Mapping) else None
    if not isinstance(block, Mapping):
        raise ProtocolError("no protocol block")
    return block


def protocol_section(protocol: Mapping[str, Any], name: str) -> Mapping[str, Any]:
    """The rule block of one test: ``protocol.tests.<name>`` (T0, T1, P1, A1)."""
    tests = protocol_block(protocol).get("tests")
    if not isinstance(tests, Mapping) or not isinstance(tests.get(name), Mapping):
        raise ProtocolError(f"protocol has no 'tests.{name}' block")
    return tests[name]


def site_seasons_required(protocol: Mapping[str, Any]) -> int:
    """Number of trial site-seasons the bench needs: ``min_site_seasons_for_label``.

    One value for T0, T1, A1 and the label. When the protocol also lists
    ``trial_site_seasons``, their count must agree.
    """
    block = protocol_block(protocol)
    n = require_int(block, "min_site_seasons_for_label", "", minimum=2)
    listed = block.get("trial_site_seasons")
    if listed is not None:
        try:
            count = sum(len(site["seasons"]) for site in listed.values())
        except (AttributeError, KeyError, TypeError) as exc:
            raise ProtocolError(f"protocol.trial_site_seasons not understood: {exc}") from exc
        if count != n:
            raise ProtocolError(f"protocol lists {count} trial site-seasons but requires {n}")
    return n


def _path(where: str, key: str) -> str:
    return ".".join(part for part in ("protocol", where, key) if part)


def require(section: Mapping[str, Any], key: str, where: str) -> Any:
    if key not in section:
        raise ProtocolError(f"{_path(where, key)} is missing")
    return section[key]


def require_int(section: Mapping[str, Any], key: str, where: str, minimum: int = 0) -> int:
    value = require(section, key, where)
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ProtocolError(f"{_path(where, key)} must be an integer >= {minimum}, got {value!r}")
    return value


def require_level(section: Mapping[str, Any], key: str, where: str) -> Fraction:
    """A significance level in (0, 1), read exactly (0.10 is 1/10)."""
    try:
        value = stats.exact(require(section, key, where))
    except (TypeError, ValueError) as exc:
        raise ProtocolError(f"{_path(where, key)}: {exc}") from exc
    if not 0 < value < 1:
        raise ProtocolError(f"{_path(where, key)} must lie in (0, 1), got {value}")
    return value


def setting(section: Mapping[str, Any], key: str, where: str, given: Any) -> Any:
    """A rule the protocol should hold but may not hold yet (N1, R1).

    The protocol wins. An argument fills a gap until the protocol holds the key
    [DECIDE]. Both present and different is refused, and so is neither.
    """
    if key in section:
        if given is not None and given != section[key]:
            raise ProtocolError(f"{_path(where, key)} is {section[key]!r}, the caller gave {given!r}")
        return section[key]
    if given is None:
        raise ProtocolError(f"{_path(where, key)} is missing: pass it as an argument [DECIDE]")
    return given


def require_ties_against(section: Mapping[str, Any], where: str) -> None:
    """The plan allows a single tie rule: a tie counts against the model."""
    if require(section, "ties", where) != "count_against":
        raise ProtocolError(f"{_path(where, 'ties')} must be 'count_against'")


def require_no_draws(section: Mapping[str, Any], where: str) -> None:
    """P1 and A1 draw nothing at random. ``draws``, when present, must say so."""
    if section.get("draws", "none") != "none":
        raise ProtocolError(f"{_path(where, 'draws')} must be 'none'")


@dataclass(frozen=True)
class Pair:
    """One trial site-season: observed value and simulated value."""

    site: str
    season: str
    observed: Any
    simulated: Any

    @property
    def key(self) -> tuple[str, str]:
        return (self.site, self.season)


def check_keys(keys: Sequence[tuple[str, str]]) -> None:
    seen: set[tuple[str, str]] = set()
    for k in keys:
        if k in seen:
            raise ValueError(f"site-season {k} appears twice")
        seen.add(k)


def exact_observed(values: Sequence[Any]) -> list[Fraction]:
    """Observed values must all be numbers (an absent site-season is left out)."""
    return [stats.exact(v) for v in values]


def exact_simulated(values: Sequence[Any], what: str = "simulated value") -> list[Fraction]:
    """Simulated values must all be numbers. A gap raises IncompleteInput."""
    out = []
    for v in values:
        try:
            out.append(stats.exact(v))
        except (TypeError, ValueError) as exc:
            raise IncompleteInput(f"{what} missing or not a number: {v!r}") from exc
    return out


def split_pairs(pairs: Sequence[Pair]) -> tuple[list[str], list[Fraction], list[Fraction]]:
    """Sites, observed and simulated values, after the checks above."""
    check_keys([p.key for p in pairs])
    sites = [p.site for p in pairs]
    obs = exact_observed([p.observed for p in pairs])
    sim = exact_simulated([p.simulated for p in pairs])
    return sites, obs, sim

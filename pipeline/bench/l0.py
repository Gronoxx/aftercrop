# Written with an SI tool (Claude, Anthropic) for NASA Space Apps 2026. See AI_USE.md.
"""L0, sanity: does the chain conserve water and repeat itself?

Chains S1 and S3 on one field, run twice, with CN and REW computed from the
texture and the 61 and 9 side run beside (plan, sections 10.6 and 11.2). Rule,
read from ``protocol.tests.L0``:

- closure: the water balance closes at every crop swap within
  ``closure_mm_per_swap`` (1 mm). "Up to 1 mm" includes 1 mm, as in the engine
  (``pipeline.engine.chain``), which stops above it;
- repeat: the two runs give the same output, exactly (``repeat: exact``). The
  outputs are compared as canonical JSON, so NaN equals NaN and nothing is
  rounded;
- artifacts: no more than ``unexplained_artifacts`` (0) artifacts without a
  written explanation. The kinds are those of ``protocol.chains.artifact_checks``
  (days to emergence, zeroed season, dead canopy).

The change of Delta between the computed CN and REW and the fixed 61 and 9 is
reported, with no threshold. A failure blocks reading any Delta (section 11.2):
the scoreboard says so. This module receives the chain outputs as arguments
and never runs the engine.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from fractions import Fraction
from typing import Any

from pipeline.bench import common, stats
from pipeline.bench.common import FAIL, PASS

REPEAT_RULES = ("exact",)
ZEROED = "zeroed_season"
DEAD = "dead_canopy"
EMERGENCE = "days_to_emergence"


@dataclass(frozen=True)
class L0Rule:
    closure_mm: Fraction
    repeat: str
    max_unexplained: int
    artifact_kinds: tuple[str, ...]

    @classmethod
    def from_protocol(cls, protocol: Mapping[str, Any]) -> "L0Rule":
        """Reads ``protocol.tests.L0`` and ``protocol.chains.artifact_checks``."""
        sec = common.protocol_section(protocol, "L0")
        where = "tests.L0"
        try:
            closure = stats.exact(common.require(sec, "closure_mm_per_swap", where))
        except (TypeError, ValueError) as exc:
            raise common.ProtocolError(f"protocol.{where}.closure_mm_per_swap: {exc}") from exc
        if closure < 0:
            raise common.ProtocolError(f"protocol.{where}.closure_mm_per_swap must be >= 0")
        repeat = common.require(sec, "repeat", where)
        if repeat not in REPEAT_RULES:
            raise common.ProtocolError(f"protocol.{where}.repeat must be one of {REPEAT_RULES}")
        max_unexplained = common.require_int(sec, "unexplained_artifacts", where)
        chains = common.protocol_block(protocol).get("chains")
        kinds = chains.get("artifact_checks") if isinstance(chains, Mapping) else None
        if not isinstance(kinds, (list, tuple)) or not kinds or not all(isinstance(k, str) for k in kinds):
            raise common.ProtocolError("protocol.chains.artifact_checks must list the artifact kinds")
        return cls(closure_mm=closure, repeat=repeat, max_unexplained=max_unexplained,
                   artifact_kinds=tuple(kinds))


# Artifacts -----------------------------------------------------------------------

@dataclass(frozen=True)
class Artifact:
    """One artifact found in one season of one arm."""

    arm: str
    season: str
    kind: str
    detail: str = ""
    explanation: str | None = None

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.arm, self.season, self.kind)

    @property
    def explained(self) -> bool:
        return bool(self.explanation and self.explanation.strip())

    def to_dict(self) -> dict[str, Any]:
        return {"arm": self.arm, "season": self.season, "kind": self.kind,
                "detail": self.detail, "explanation": self.explanation}


def detect_artifacts(
    arm: str,
    season: str,
    *,
    dry_yield_t_ha: Any,
    crop_dead: bool,
    days_to_emergence: int | None,
    max_days_to_emergence: int | None = None,
) -> list[Artifact]:
    """Artifacts of one season, from the fields of ``pipeline.engine.chain.SeasonResult``.

    - zeroed season: dry yield equal to 0;
    - dead canopy: the engine reports the crop dead;
    - days to emergence: the crop never emerged, or (only when
      ``max_days_to_emergence`` is given) emerged later than that. The protocol
      holds no such limit yet [DECIDE], so by default only "never" is flagged.
    """
    out = []
    try:
        yield_value = stats.exact(dry_yield_t_ha)
    except (TypeError, ValueError) as exc:
        raise common.IncompleteInput(f"yield of {arm} {season} missing: {dry_yield_t_ha!r}") from exc
    if yield_value == 0:
        out.append(Artifact(arm, season, ZEROED, "dry yield 0 t/ha"))
    if crop_dead:
        out.append(Artifact(arm, season, DEAD, "crop dead before maturity"))
    if days_to_emergence is None:
        out.append(Artifact(arm, season, EMERGENCE, "no emergence"))
    elif max_days_to_emergence is not None and days_to_emergence > max_days_to_emergence:
        out.append(Artifact(arm, season, EMERGENCE,
                            f"emergence after {days_to_emergence} days, limit {max_days_to_emergence}"))
    return out


def explain(artifacts: Sequence[Artifact], explanations: Mapping[tuple[str, str, str], str]) -> list[Artifact]:
    """Attach the written explanations, keyed by (arm, season, kind)."""
    known = {a.key for a in artifacts}
    extra = set(explanations) - known
    if extra:
        raise ValueError(f"explanations for artifacts that were not found: {sorted(extra)}")
    return [Artifact(a.arm, a.season, a.kind, a.detail, explanations.get(a.key, a.explanation))
            for a in artifacts]


# Repeat --------------------------------------------------------------------------

def canonical(output: Any) -> str:
    """Canonical JSON of one run's output: sorted keys, floats as Python writes them."""
    return json.dumps(output, sort_keys=True, separators=(",", ":"), allow_nan=True, default=str)


def digest(output: Any) -> str:
    return hashlib.sha256(canonical(output).encode("utf-8")).hexdigest()


# Result --------------------------------------------------------------------------

@dataclass(frozen=True)
class L0Result:
    status: str
    failed_on: list[str]
    closure_mm: Fraction
    n_swaps: int
    max_gap_mm: Fraction
    n_gaps_over: int
    repeat_ok: bool
    digest_first: str
    digest_second: str
    artifacts: list[Artifact]
    max_unexplained: int
    cn_rew: dict[str, Any] | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def n_unexplained(self) -> int:
        return sum(1 for a in self.artifacts if not a.explained)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": "L0",
            "status": self.status,
            "failed_on": list(self.failed_on),
            "statistic": "water_closure_repeat_artifacts",
            "closure_mm_per_swap": float(self.closure_mm),
            "n_swaps": self.n_swaps,
            "max_gap_mm": float(self.max_gap_mm),
            "n_gaps_over": self.n_gaps_over,
            "repeat_exact": self.repeat_ok,
            "digest_first": self.digest_first,
            "digest_second": self.digest_second,
            "n_artifacts": len(self.artifacts),
            "n_unexplained": self.n_unexplained,
            "unexplained_artifacts_max": self.max_unexplained,
            "artifacts": [a.to_dict() for a in self.artifacts],
            "cn_rew": self.cn_rew,
            "blocks_delta": self.status != PASS,
            "notes": list(self.notes),
        }


def cn_rew_variation(computed: Mapping[str, Any], fixed: Mapping[str, Any]) -> dict[str, Any]:
    """Delta with CN and REW from the texture against Delta with 61 and 9, per
    season. Reported only: no threshold (section 11.2)."""
    if set(computed) != set(fixed):
        raise ValueError("the CN and REW side run must cover the same seasons as the base run")
    if not computed:
        raise ValueError("no seasons for the CN and REW comparison")
    a = dict(zip(computed, common.exact_simulated(list(computed.values()), "Delta with computed CN and REW")))
    b = dict(zip(fixed, common.exact_simulated([fixed[k] for k in computed], "Delta with CN 61 and REW 9")))
    diff = {k: a[k] - b[k] for k in computed}
    return {
        "by_season": {str(k): {"computed": float(a[k]), "fixed_61_9": float(b[k]), "difference": float(diff[k])}
                      for k in computed},
        "max_abs_difference": float(max(abs(v) for v in diff.values())),
        "threshold": None,
    }


def evaluate(
    gaps_mm: Mapping[str, Sequence[Any]],
    first_run: Any,
    second_run: Any,
    artifacts: Sequence[Artifact],
    rule: L0Rule,
    cn_rew: dict[str, Any] | None = None,
) -> L0Result:
    """L0 on the chain outputs.

    ``gaps_mm`` maps a chain (for example "S1 run 1") to its water gaps at each
    crop swap, in mm. ``first_run`` and ``second_run`` are the outputs of the
    two identical runs, as JSON-able structures. ``artifacts`` come from
    ``detect_artifacts`` and ``explain``.
    """
    values = []
    for chain, gaps in gaps_mm.items():
        values += [abs(v) for v in common.exact_simulated(list(gaps), f"water gap of {chain}")]
    if not values:
        raise ValueError("L0 needs at least one crop swap")
    unknown = sorted({a.kind for a in artifacts} - set(rule.artifact_kinds))
    if unknown:
        raise ValueError(f"artifact kinds outside protocol.chains.artifact_checks: {unknown}")
    common.check_keys([(f"{a.arm}|{a.season}", a.kind) for a in artifacts])
    n_over = sum(1 for v in values if v > rule.closure_mm)
    d1, d2 = digest(first_run), digest(second_run)
    repeat_ok = canonical(first_run) == canonical(second_run)
    n_unexplained = sum(1 for a in artifacts if not a.explained)
    failed_on = []
    if n_over:
        failed_on.append("closure")
    if not repeat_ok:
        failed_on.append("repeat")
    if n_unexplained > rule.max_unexplained:
        failed_on.append("artifacts")
    notes = ["a failure blocks reading any Delta"] if failed_on else []
    return L0Result(
        status=FAIL if failed_on else PASS,
        failed_on=failed_on,
        closure_mm=rule.closure_mm,
        n_swaps=len(values),
        max_gap_mm=max(values),
        n_gaps_over=n_over,
        repeat_ok=repeat_ok,
        digest_first=d1,
        digest_second=d2,
        artifacts=list(artifacts),
        max_unexplained=rule.max_unexplained,
        cn_rew=cn_rew,
        notes=notes,
    )

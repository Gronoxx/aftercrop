# Written with an SI tool (Claude, Anthropic) for NASA Space Apps 2026. See AI_USE.md.
"""Engine artifacts, checked before any delta is read (plan 10.6, M3.6, L0).

Each arm records, for every season, days to emergence, zeroed season and
dead canopy (``protocol.chains.artifact_checks``), plus structured flags:

- outcome: the model's own result, counts against the model
  (``protocol.missing_data.engine_outcome``). Never skipped.
- integrity: the run itself is wrong (water balance, handover, impossible
  yield). L0 fails and no delta may be read.
- setup: the chain did not do what the regime asked (sowing date before the
  simulated harvest, season cut at the end of the simulation).
- threshold: a check against a limit the caller passed explicitly.

No threshold is built in. A check whose limit is neither in the protocol nor
passed explicitly is not run and is listed in ``not_checked``. A flag found
by ``matured`` alone is never trusted: OSPy matures a crop on degree days
even with no canopy, so canopy and crop death are read separately.

Nothing here imports aquacrop. The input is ``chain.ChainResult``.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from datetime import date
from typing import TYPE_CHECKING, Any, Mapping

from .sowing import ProtocolGap

if TYPE_CHECKING:
    from .chain import ChainResult, SeasonResult

KNOWN_CHECKS = ("days_to_emergence", "zeroed_season", "dead_canopy")

KINDS = ("outcome", "integrity", "setup", "threshold")
CODES = {
    "not_germinated": ("outcome", "the seed never germinated"),
    "canopy_never_formed": ("outcome", "canopy cover never rose above zero (no emergence)"),
    "dead_canopy": ("outcome", "the crop died before maturity"),
    "zeroed_season": ("outcome", "dry yield is zero"),
    "not_matured": ("outcome", "the season ended without maturity"),
    "matured_without_canopy": ("outcome", "OSPy marked maturity on degree days with no canopy"),
    "sowing_not_triggered": ("outcome", "the rain rule did not trigger by the latest date"),
    "yield_invalid": ("integrity", "dry yield is NaN or negative"),
    "yield_above_potential": ("integrity", "dry yield above the OSPy yield potential"),
    "water_balance_open": ("integrity", "season water balance does not close"),
    "handover_gap": ("integrity", "water handed over differs from the water left"),
    "sowing_before_previous_harvest": ("setup", "fixed sowing date before the simulated previous harvest"),
    "cut_at_sim_end": ("setup", "harvest on the last simulated day, not matured and not dead"),
    "yield_above_max": ("threshold", "dry yield above the maximum passed by the caller"),
    "late_emergence": ("threshold", "days to emergence above the maximum passed by the caller"),
}
YIELD_EPS_T_HA = 1e-9  # float zero, not a threshold


@dataclass(frozen=True)
class Thresholds:
    closure_mm_per_swap: float                    # protocol.tests.L0
    season_balance_mm: float | None = None        # [DECIDE] not in the protocol
    max_yield_t_ha: Mapping[str, float] | None = None   # [DECIDE] not in the protocol
    max_days_to_emergence: int | None = None      # [DECIDE] not in the protocol

    @classmethod
    def from_protocol(cls, protocol: Mapping[str, Any], *, season_balance_mm: float | None = None,
                      max_yield_t_ha: Mapping[str, float] | None = None,
                      max_days_to_emergence: int | None = None) -> "Thresholds":
        try:
            closure = float(protocol["tests"]["L0"]["closure_mm_per_swap"])
        except (KeyError, TypeError) as e:
            raise ProtocolGap("protocol.tests.L0.closure_mm_per_swap missing") from e
        return cls(closure, season_balance_mm, max_yield_t_ha, max_days_to_emergence)

    def not_checked(self) -> list[str]:
        out = []
        if self.season_balance_mm is None:
            out.append("water_balance_open")
        if self.max_yield_t_ha is None:
            out.append("yield_above_max")
        if self.max_days_to_emergence is None:
            out.append("late_emergence")
        return out


@dataclass(frozen=True)
class Flag:
    code: str
    kind: str
    arm: str | None
    index: int | None
    label: str
    crop: str
    message: str
    detail: dict = field(default_factory=dict)

    @property
    def id(self) -> str:
        return f"{self.arm}:{self.index}:{self.code}"

    def as_dict(self) -> dict:
        return {"id": self.id, **asdict(self)}


def _flag(code: str, arm, index, label: str, crop: str, **detail) -> Flag:
    kind, message = CODES[code]
    return Flag(code, kind, arm, index, label, crop, message, detail)


def checks_from_protocol(protocol: Mapping[str, Any]) -> list[str]:
    try:
        checks = list(protocol["chains"]["artifact_checks"])
    except (KeyError, TypeError) as e:
        raise ProtocolGap("protocol.chains.artifact_checks missing") from e
    unknown = set(checks) - set(KNOWN_CHECKS)
    if unknown:
        raise ProtocolGap(f"artifact checks not implemented: {sorted(unknown)}")
    return checks


def zeroed(res: "SeasonResult") -> bool:
    return math.isfinite(res.dry_yield_t_ha) and abs(res.dry_yield_t_ha) <= YIELD_EPS_T_HA


def season_flags(res: "SeasonResult", thr: Thresholds, arm: str | None = None,
                 index: int | None = None) -> list[Flag]:
    f = lambda code, **d: _flag(code, arm, index, res.label, res.crop, **d)  # noqa: E731
    out = []
    y = res.dry_yield_t_ha
    if not res.germinated:
        out.append(f("not_germinated"))
    if res.days_to_emergence is None:
        out.append(f("canopy_never_formed", germinated=res.germinated))
    if res.crop_dead:
        out.append(f("dead_canopy", canopy_max=res.canopy_max, harvest=res.harvest.isoformat()))
    if not math.isfinite(y) or y < 0:
        out.append(f("yield_invalid", dry_yield_t_ha=y))
    elif zeroed(res):
        out.append(f("zeroed_season"))
    if not res.matured:
        out.append(f("not_matured"))
    elif res.canopy_max <= 0:
        out.append(f("matured_without_canopy"))
    if math.isfinite(y) and math.isfinite(res.yield_potential_t_ha) \
            and y > res.yield_potential_t_ha + YIELD_EPS_T_HA:
        out.append(f("yield_above_potential", dry_yield_t_ha=y, potential_t_ha=res.yield_potential_t_ha))
    if res.sim_end is not None and res.harvest == res.sim_end and not res.matured and not res.crop_dead:
        out.append(f("cut_at_sim_end", sim_end=res.sim_end.isoformat()))
    if thr.season_balance_mm is not None and not abs(res.balance_residual_mm) <= thr.season_balance_mm:
        out.append(f("water_balance_open", residual_mm=res.balance_residual_mm,
                     tolerance_mm=thr.season_balance_mm))
    if thr.max_yield_t_ha is not None:
        if res.crop not in thr.max_yield_t_ha:
            raise ProtocolGap(f"no maximum yield for {res.crop}")
        lim = float(thr.max_yield_t_ha[res.crop])
        if math.isfinite(y) and y > lim:
            out.append(f("yield_above_max", dry_yield_t_ha=y, max_t_ha=lim))
    if thr.max_days_to_emergence is not None and res.days_to_emergence is not None \
            and res.days_to_emergence > thr.max_days_to_emergence:
        out.append(f("late_emergence", days=res.days_to_emergence, max_days=thr.max_days_to_emergence))
    return out


def chain_flags(chain: "ChainResult", thr: Thresholds, arm: str | None = None) -> list[Flag]:
    out = []
    for i, res in enumerate(chain.seasons):
        out.extend(season_flags(res, thr, arm, i))
    for i, gap in enumerate(chain.handover_gap_mm, start=1):
        if gap is not None and not gap <= thr.closure_mm_per_swap:
            res = chain.seasons[i]
            out.append(_flag("handover_gap", arm, i, res.label, res.crop, gap_mm=gap,
                             tolerance_mm=thr.closure_mm_per_swap))
    if chain.stopped is not None:
        s = chain.stopped
        detail = {k: v for k, v in s.items() if k not in ("index", "label", "crop", "role", "reason")}
        out.append(_flag(s["reason"], arm, s["index"], s["label"], s["crop"], **detail))
    return out


def season_record(res: "SeasonResult", index: int) -> dict:
    """The three protocol checks plus what is needed to read them."""
    return {
        "index": index, "label": res.label, "role": res.role, "crop": res.crop,
        "sowing": res.sowing.isoformat(), "sowing_source": res.sowing_source,
        "harvest": res.harvest.isoformat(),
        "days_to_emergence": res.days_to_emergence,
        "zeroed_season": zeroed(res), "dead_canopy": res.crop_dead,
        "germinated": res.germinated, "matured": res.matured,
        "canopy_max": res.canopy_max, "dry_yield_t_ha": res.dry_yield_t_ha,
        "restarted": res.restarted,
    }


def sowing_day_difference(arms: Mapping[str, "ChainResult"]) -> list[dict]:
    """Per label sown in two or more arms after the shared prefix: dates and spread."""
    labels: dict[str, dict[str, date | None]] = {}
    for name, chain in arms.items():
        for res in chain.seasons[chain.shared_prefix:]:
            if res.label:
                labels.setdefault(res.label, {})[name] = res.sowing
        if chain.stopped is not None and chain.stopped.get("label"):
            labels.setdefault(chain.stopped["label"], {})[name] = None
    out = []
    for label, by_arm in labels.items():
        if len(by_arm) < 2:
            continue
        sown = [d for d in by_arm.values() if d is not None]
        out.append({
            "label": label,
            "sowing": {a: (d.isoformat() if d else None) for a, d in by_arm.items()},
            "difference_days": (max(sown) - min(sown)).days if len(sown) == len(by_arm) else None,
        })
    return out


def report(arms: Mapping[str, "ChainResult"], thr: Thresholds,
           checks: list[str] | tuple[str, ...] = KNOWN_CHECKS) -> dict:
    """artifacts.json body for one regime. Written by the bench before t1.json."""
    unknown = set(checks) - set(KNOWN_CHECKS)
    if unknown:
        raise ProtocolGap(f"artifact checks not implemented: {sorted(unknown)}")
    body: dict[str, Any] = {
        "checks": list(checks),
        "thresholds": {k: (dict(v) if isinstance(v, Mapping) else v) for k, v in asdict(thr).items()},
        "not_checked": thr.not_checked(),
        "arms": {},
    }
    counts = dict.fromkeys(KINDS, 0)
    for name, chain in arms.items():
        flags = chain_flags(chain, thr, name)
        for fl in flags:
            counts[fl.kind] += 1
        body["arms"][name] = {
            "completed": chain.completed, "stopped": chain.stopped,
            "shared_prefix": chain.shared_prefix,
            "seasons": [season_record(r, i) for i, r in enumerate(chain.seasons)],
            "flags": [fl.as_dict() for fl in flags],
        }
    body["sowing_day_difference"] = sowing_day_difference(arms)
    body["counts"] = counts
    body["blocks_delta"] = counts["integrity"] > 0
    return body


def unexplained(body: Mapping[str, Any], explanations: Mapping[str, str]) -> list[str]:
    """Flag ids with no written explanation (L0: none may be left)."""
    ids = [fl["id"] for arm in body["arms"].values() for fl in arm["flags"]]
    return [i for i in ids if not str(explanations.get(i, "")).strip()]

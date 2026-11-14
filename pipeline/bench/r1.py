# Written with an SI tool (Claude, Anthropic) for NASA Space Apps 2026. See AI_USE.md.
"""R1, robustness: does the direction of S3 against S1 hold when uncertain
parameters move inside declared ranges? (plan, section 12.4)

Runs only if T1 passes. It does not say whether wheat pays and does not change
the decision state: it feeds only L5. Everything is read from ``protocol.r1``
and ``protocol.seed``:

- design: ``members`` Latin hypercube members (50, cut to ``cut_to``, 20),
  uniform, paired: one draw per member serves both arms and every season;
- ranges: ``protocol.r1.ranges``, one dimension per entry, and one per crop
  where the protocol splits the entry by crop (see ``dimensions``);
- Delta_my = simulated soybean after wheat (S3) minus after second-crop corn
  (S1), kg/ha, member m and season y, for internal use only;
- band: the low and high percentiles of every Delta_my pooled. The protocol
  names P10 and P90 only inside the state wording and names no percentile
  method, so ``band_percentiles`` and ``percentile_method`` are read from
  ``protocol.r1`` when present, else passed by the caller [DECIDE];
- inversion: share of members whose median Delta_my has the opposite sign of
  the base run's median. A zero median counts against (inverted), the bench's
  tie rule; a base median of zero gives no direction, so every member counts
  as inverted [DECIDE];
- L = the larger of ``equivalence_kg_ha`` (166) and the T1 RMSE, compared on
  squares, exactly;
- states, in this order: "lower water risk in the model" for S3 when P10 > +L
  with inversion within ``inversion_max``; the same state for the current
  rotation (S1) when P90 < -L with inversion within the limit (section 12.4);
  "no water-related difference in the model" when P10 >= -166 and P90 <= +166;
  "inconclusive" for everything else, including R1 not run.
- status per field: "pass" when inversion is within the limit, "fail" when it
  is above it, "not_completed" when any member or season is missing (the whole
  design or nothing, as in P1 [DECIDE]).

This module draws the design and reads the results. The chains run elsewhere.
"""

from __future__ import annotations

import random
import re
from collections.abc import Hashable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from fractions import Fraction
from typing import Any

from pipeline.bench import common, stats
from pipeline.bench.common import FAIL, NOT_COMPLETED, NOT_RUN, PASS

LOWER = "lower water risk in the model"
NO_DIFFERENCE = "no water-related difference in the model"
INCONCLUSIVE = "inconclusive"
STATES = (LOWER, NO_DIFFERENCE, INCONCLUSIVE)
ARMS = ("S1", "S3")             # Delta = S3 - S1
RUNS_ONLY_IF = "T1 passes"
PCT_SUFFIX = "_pct_of_library"
CLASS_RANGE = re.compile(r"\bAD(\d+)\s+to\s+AD(\d+)\b")

UNIFORM = "uniform"             # absolute value in [low, high]
FACTOR = "factor"               # multiplier of the library value in [low, high]
CLASS = "class"                 # the estimated class or a neighbour, within [low, high]
INTERVAL = "interval"           # a date in an interval the caller gives (field and season)


# Dimensions ---------------------------------------------------------------------

@dataclass(frozen=True)
class Dimension:
    name: str
    kind: str
    low: Fraction | None = None
    high: Fraction | None = None
    text: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "kind": self.kind,
                "low": None if self.low is None else float(self.low),
                "high": None if self.high is None else float(self.high),
                "text": self.text}


def _dimension(name: str, key: str, value: Any, where: str) -> Dimension:
    if key.endswith(PCT_SUFFIX):
        try:
            pct = stats.exact(value)
        except (TypeError, ValueError) as exc:
            raise common.ProtocolError(f"{where}: {exc}") from exc
        if not 0 < pct < 100:
            raise common.ProtocolError(f"{where} must lie in (0, 100)")
        return Dimension(name, FACTOR, 1 - pct / 100, 1 + pct / 100, f"+-{value}% of the library value")
    if isinstance(value, (list, tuple)):
        try:
            low, high = (stats.exact(v) for v in value)
        except (TypeError, ValueError) as exc:
            raise common.ProtocolError(f"{where} must be [low, high]: {exc}") from exc
        if not low < high:
            raise common.ProtocolError(f"{where} must have low < high")
        return Dimension(name, UNIFORM, low, high)
    if isinstance(value, str) and value:
        m = CLASS_RANGE.search(value)
        if m and "neighbour" in value:
            low, high = int(m.group(1)), int(m.group(2))
            if not low < high:
                raise common.ProtocolError(f"{where}: class range must go up")
            return Dimension(name, CLASS, Fraction(low), Fraction(high), value)
        return Dimension(name, INTERVAL, text=value)
    raise common.ProtocolError(f"{where} not understood: {value!r}")


def dimensions(ranges: Mapping[str, Any]) -> tuple[Dimension, ...]:
    """One dimension per entry of ``protocol.r1.ranges``; an entry split by
    crop gives one per crop ("HI0.maize", "HI0.soybean"). A ``*_pct_of_library``
    entry is a multiplier of the library value. Sorted by name, which fixes the
    draw order whatever the order of the YAML keys.

    Zmax, CCx and senescence are not split by crop in the protocol, so one
    draw serves every crop [DECIDE].
    """
    out = []
    for key, value in ranges.items():
        if isinstance(value, Mapping):
            for sub, v in value.items():
                crop = sub[: -len(PCT_SUFFIX)] if sub.endswith(PCT_SUFFIX) else sub
                out.append(_dimension(f"{key}.{crop}", sub, v, f"protocol.r1.ranges.{key}.{sub}"))
        else:
            name = key[: -len(PCT_SUFFIX)] if key.endswith(PCT_SUFFIX) else key
            out.append(_dimension(name, key, value, f"protocol.r1.ranges.{key}"))
    names = [d.name for d in out]
    if len(set(names)) != len(names):
        raise common.ProtocolError("protocol.r1.ranges gives two dimensions the same name")
    if not out:
        raise common.ProtocolError("protocol.r1.ranges is empty")
    return tuple(sorted(out, key=lambda d: d.name))


# Rule -----------------------------------------------------------------------------

@dataclass(frozen=True)
class R1Rule:
    members: int
    cut_to: int
    seed: int
    dimensions: tuple[Dimension, ...]
    inversion_max: Fraction
    equivalence: Fraction
    band: tuple[Fraction, Fraction]
    method: str

    @classmethod
    def from_protocol(cls, protocol: Mapping[str, Any], *, band: Sequence[Any] | None = None,
                      method: str | None = None) -> "R1Rule":
        """Reads ``protocol.r1`` and ``protocol.seed``."""
        block = common.protocol_block(protocol)
        sec = block.get("r1")
        if not isinstance(sec, Mapping):
            raise common.ProtocolError("protocol has no 'r1' block")
        where = "r1"
        if common.require(sec, "runs_only_if", where) != RUNS_ONLY_IF:
            raise common.ProtocolError(f"protocol.r1.runs_only_if must be {RUNS_ONLY_IF!r}")
        members = common.require_int(sec, "members", where, minimum=2)
        cut_to = common.require_int(sec, "cut_to", where, minimum=2)
        if cut_to > members:
            raise common.ProtocolError("protocol.r1.cut_to must not exceed members")
        for key, expected in (("sampling", "latin_hypercube"), ("distribution", "uniform"), ("paired", True)):
            if common.require(sec, key, where) != expected:
                raise common.ProtocolError(f"protocol.r1.{key} must be {expected!r}")
        seed = common.require_int(block, "seed", "")
        ranges = common.require(sec, "ranges", where)
        if not isinstance(ranges, Mapping):
            raise common.ProtocolError("protocol.r1.ranges must be a mapping")
        inversion_max = common.require_level(sec, "inversion_max", where)
        try:
            equivalence = stats.exact(common.require(sec, "equivalence_kg_ha", where))
        except (TypeError, ValueError) as exc:
            raise common.ProtocolError(f"protocol.r1.equivalence_kg_ha: {exc}") from exc
        if equivalence <= 0:
            raise common.ProtocolError("protocol.r1.equivalence_kg_ha must be positive")
        common.require(sec, "L_kg_ha", where)
        states = common.require(sec, "states", where)
        if not isinstance(states, Mapping) or set(states) != set(STATES):
            raise common.ProtocolError(f"protocol.r1.states must name exactly {list(STATES)}")
        levels = common.setting(sec, "band_percentiles", where, None if band is None else list(band))
        try:
            lo, hi = (stats.exact(x) for x in levels)
        except (TypeError, ValueError) as exc:
            raise common.ProtocolError(f"protocol.r1.band_percentiles must be [low, high]: {exc}") from exc
        if not 0 < lo < hi < 100:
            raise common.ProtocolError("protocol.r1.band_percentiles must satisfy 0 < low < high < 100")
        m = common.setting(sec, "percentile_method", where, method)
        if m not in stats.PERCENTILE_METHODS:
            raise common.ProtocolError(f"protocol.r1.percentile_method must be one of {stats.PERCENTILE_METHODS}")
        return cls(members=members, cut_to=cut_to, seed=seed, dimensions=dimensions(ranges),
                   inversion_max=inversion_max, equivalence=equivalence, band=(lo, hi), method=m)


# Latin hypercube ----------------------------------------------------------------

def _below(rng: random.Random, k: int) -> int:
    """An integer in 0..k-1 from one call of random(), the only method whose
    sequence Python keeps across versions for a given seed."""
    return min(int(rng.random() * k), k - 1)


def latin_hypercube(n: int, d: int, seed: int) -> list[tuple[float, ...]]:
    """n points in [0, 1)^d, one per stratum of width 1/n in every dimension.

    Draw order, fixed: for each dimension in turn, a permutation of 0..n-1 by
    Fisher-Yates (positions n-1 down to 1, one draw each), then n jitters.
    Point i, dimension j is (perm_j[i] + jitter_j[i]) / n.
    """
    if n < 1 or d < 1:
        raise ValueError("a Latin hypercube needs n >= 1 and d >= 1")
    rng = random.Random(seed)
    columns = []
    for _ in range(d):
        perm = list(range(n))
        for i in range(n - 1, 0, -1):
            j = _below(rng, i + 1)
            perm[i], perm[j] = perm[j], perm[i]
        jitter = [rng.random() for _ in range(n)]
        columns.append([(perm[i] + jitter[i]) / n for i in range(n)])
    return [tuple(col[i] for col in columns) for i in range(n)]


@dataclass(frozen=True)
class R1Design:
    seed: int
    dimensions: tuple[Dimension, ...]
    points: tuple[tuple[float, ...], ...]

    @property
    def members(self) -> int:
        return len(self.points)

    def unit(self, member: int) -> dict[str, float]:
        return {d.name: u for d, u in zip(self.dimensions, self.points[member])}

    def parameters(self, member: int, *, estimate_class: int | None = None,
                   intervals: Mapping[str, tuple[date, date]] | None = None) -> dict[str, Any]:
        """Values of one member. ``estimate_class`` is the field's estimated
        available-water class; ``intervals`` gives, for this field and season,
        the dates of every interval dimension (maize and wheat sowing). The
        same unit draw maps into each season's interval, so the pairing holds."""
        intervals = intervals or {}
        out = {}
        for d, u in zip(self.dimensions, self.points[member]):
            out[d.name] = value(d, u, estimate_class=estimate_class, interval=intervals.get(d.name))
        return out

    def to_dict(self) -> dict[str, Any]:
        return {"seed": self.seed, "members": self.members,
                "dimensions": [d.to_dict() for d in self.dimensions],
                "points": [list(p) for p in self.points]}


def value(dim: Dimension, u: float, *, estimate_class: int | None = None,
          interval: tuple[date, date] | None = None) -> Any:
    """Map one unit draw u in [0, 1) to the dimension's value."""
    if not 0 <= u < 1:
        raise ValueError(f"unit draw must lie in [0, 1), got {u}")
    if dim.kind in (UNIFORM, FACTOR):
        return float(dim.low) + u * (float(dim.high) - float(dim.low))
    if dim.kind == CLASS:
        lo, hi = int(dim.low), int(dim.high)
        if estimate_class is None or not lo <= estimate_class <= hi:
            raise ValueError(f"{dim.name}: estimated class must lie in {lo}..{hi}, got {estimate_class!r}")
        candidates = [c for c in (estimate_class - 1, estimate_class, estimate_class + 1) if lo <= c <= hi]
        return candidates[_index(u, len(candidates))]
    if dim.kind == INTERVAL:
        if interval is None:
            raise ValueError(f"{dim.name}: no interval given ({dim.text})")
        first, last = interval
        days = (last - first).days + 1
        if days < 1:
            raise ValueError(f"{dim.name}: interval ends before it starts")
        return first + timedelta(days=_index(u, days))
    raise ValueError(f"unknown dimension kind {dim.kind!r}")


def _index(u: float, k: int) -> int:
    return min(int(u * k), k - 1)


def design(rule: R1Rule, members: int | None = None, only: Sequence[str] | None = None) -> R1Design:
    """The paired design. ``members`` is the protocol's ``members`` or, after a
    cut, ``cut_to``. ``only`` keeps a subset of dimensions (the plan's second
    cut, Zmax and water only, is not in the protocol yet [DECIDE]); the others
    stay at the base run's values."""
    n = rule.members if members is None else members
    if n not in (rule.members, rule.cut_to):
        raise common.ProtocolError(f"R1 runs {rule.members} members or, cut, {rule.cut_to}; got {n}")
    dims = rule.dimensions
    if only is not None:
        unknown = set(only) - {d.name for d in dims}
        if unknown:
            raise ValueError(f"unknown R1 dimensions: {sorted(unknown)}")
        dims = tuple(d for d in dims if d.name in set(only))
    return R1Design(seed=rule.seed, dimensions=dims,
                    points=tuple(latin_hypercube(n, len(dims), rule.seed)))


# Results ------------------------------------------------------------------------

@dataclass(frozen=True)
class R1Field:
    """One field: base run and members, each mapping season -> {"S1": kg/ha, "S3": kg/ha}."""

    field_id: str
    base: Mapping[Hashable, Mapping[str, Any]]
    members: Mapping[int, Mapping[Hashable, Mapping[str, Any]]]


def _deltas(run: Mapping[Hashable, Mapping[str, Any]], seasons: Sequence[Hashable], what: str) -> list[Fraction]:
    out = []
    for y in seasons:
        arms = run[y]
        s1, s3 = common.exact_simulated([arms.get("S1"), arms.get("S3")], f"{what}, season {y}")
        out.append(s3 - s1)
    return out


def _sign(x: Fraction) -> int:
    return (x > 0) - (x < 0)


@dataclass(frozen=True)
class R1FieldResult:
    field_id: str
    status: str
    state: str
    column: str | None                 # S3, S1 or None
    members: int
    n_seasons: int
    n_inverted: int | None
    inversion: Fraction | None
    band: tuple[Fraction, Fraction] | None
    base_median: Fraction | None
    l_kg_ha: float | None
    kendall_s_by_member: list[int] = field(default_factory=list)
    missing_members: list[int] = field(default_factory=list)
    incomplete_members: list[int] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        def f(x):
            return None if x is None else float(x)
        return {
            "field_id": self.field_id,
            "status": self.status,
            "state": self.state,
            "column": self.column,
            "members": self.members,
            "n_seasons": self.n_seasons,
            "n_inverted": self.n_inverted,
            "n_same_direction": None if self.n_inverted is None else self.members - self.n_inverted,
            "inversion": f(self.inversion),
            "band_kg_ha": None if self.band is None else [float(self.band[0]), float(self.band[1])],
            "base_median_kg_ha": f(self.base_median),
            "l_kg_ha": self.l_kg_ha,
            "kendall_s_by_member": list(self.kendall_s_by_member),
            "missing_members": list(self.missing_members),
            "incomplete_members": list(self.incomplete_members),
        }


def state_for(band: tuple[Fraction, Fraction], inversion_ok: bool, equivalence: Fraction,
              t1_mse: Fraction) -> tuple[str, str | None]:
    """State and column from the band, the inversion check and L = max(166, T1 RMSE)."""
    lo, hi = band

    def beyond_l(x: Fraction) -> bool:          # |x| > L, exactly, with x past the equivalence
        return abs(x) > equivalence and x * x > t1_mse

    if inversion_ok and lo > 0 and beyond_l(lo):
        return LOWER, "S3"
    if inversion_ok and hi < 0 and beyond_l(hi):
        return LOWER, "S1"
    if lo >= -equivalence and hi <= equivalence:
        return NO_DIFFERENCE, None
    return INCONCLUSIVE, None


def evaluate_field(fld: R1Field, rule: R1Rule, members: int, t1_mse: Fraction) -> R1FieldResult:
    seasons = list(fld.base)
    if not seasons:
        raise ValueError(f"field {fld.field_id}: the base run has no season")
    base = _deltas(fld.base, seasons, f"field {fld.field_id} base run")
    extra = sorted(set(fld.members) - set(range(members)))
    if extra:
        raise ValueError(f"field {fld.field_id}: members outside the design: {extra}")
    missing = [m for m in range(members) if m not in fld.members]
    deltas: dict[int, list[Fraction]] = {}
    incomplete = []
    for m in range(members):
        run = fld.members.get(m)
        if run is None:
            continue
        if set(run) != set(seasons):
            incomplete.append(m)
            continue
        try:
            deltas[m] = _deltas(run, seasons, f"field {fld.field_id} member {m}")
        except common.IncompleteInput:
            incomplete.append(m)
    if missing or incomplete:
        return R1FieldResult(fld.field_id, NOT_COMPLETED, INCONCLUSIVE, None, members, len(seasons),
                             None, None, None, None, None, missing_members=missing, incomplete_members=incomplete)
    pooled = [d for m in range(members) for d in deltas[m]]
    band = (stats.percentile(pooled, rule.band[0], rule.method), stats.percentile(pooled, rule.band[1], rule.method))
    base_median = stats.median(base)
    base_sign = _sign(base_median)
    n_inverted = 0
    for m in range(members):
        s = _sign(stats.median(deltas[m]))
        if s == 0 or s != base_sign:          # a zero median counts against
            n_inverted += 1
    inversion = Fraction(n_inverted, members)
    inversion_ok = inversion <= rule.inversion_max
    state, column = state_for(band, inversion_ok, rule.equivalence, t1_mse)
    return R1FieldResult(
        field_id=fld.field_id,
        status=PASS if inversion_ok else FAIL,
        state=state,
        column=column,
        members=members,
        n_seasons=len(seasons),
        n_inverted=n_inverted,
        inversion=inversion,
        band=band,
        base_median=base_median,
        l_kg_ha=max(float(rule.equivalence), stats.sqrt_float(t1_mse)),
        kendall_s_by_member=[stats.kendall_s(deltas[m], base) for m in range(members)],
    )


@dataclass(frozen=True)
class R1Result:
    status: str
    t1_status: str
    members: int | None
    fields: list[R1FieldResult]
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": "R1",
            "status": self.status,
            "statistic": "percentile_band_and_inversion",
            "t1_status": self.t1_status,
            "members": self.members,
            "n": len(self.fields),
            "fields": [f.to_dict() for f in self.fields],
            "notes": list(self.notes),
        }


def not_run(t1_status: str, field_ids: Sequence[str]) -> R1Result:
    """R1 when T1 did not pass: not run, and L5 reads "inconclusive" everywhere."""
    fields = [R1FieldResult(fid, NOT_RUN, INCONCLUSIVE, None, 0, 0, None, None, None, None, None)
              for fid in field_ids]
    return R1Result(NOT_RUN, t1_status, None, fields, [f"runs only if T1 passes; T1 is {t1_status}"])


def evaluate(fields: Sequence[R1Field], rule: R1Rule, *, t1_status: str, t1_mse: Any = None,
             members: int | None = None) -> R1Result:
    """R1 over the ready fields. ``t1_mse`` is the squared T1 RMSE
    (``t1.T1Result.mse``, exact). Without a T1 pass nothing is read."""
    common.check_keys([(f.field_id, "") for f in fields])
    if t1_status != PASS:
        return not_run(t1_status, [f.field_id for f in fields])
    if not fields:
        raise ValueError("R1 needs at least one field")
    n = rule.members if members is None else members
    if n not in (rule.members, rule.cut_to):
        raise common.ProtocolError(f"R1 runs {rule.members} members or, cut, {rule.cut_to}; got {n}")
    if t1_mse is None:
        raise ValueError("R1 needs the T1 RMSE (squared) to set L")
    mse = stats.exact(t1_mse)
    if mse < 0:
        raise ValueError("the squared T1 RMSE cannot be negative")
    results = [evaluate_field(f, rule, n, mse) for f in fields]
    statuses = {r.status for r in results}
    if statuses == {PASS}:
        status = PASS
    elif FAIL in statuses:
        status = FAIL
    else:
        status = NOT_COMPLETED
    notes = [] if n == rule.members else [f"cut from {rule.members} to {n} members"]
    return R1Result(status, t1_status, n, results, notes)


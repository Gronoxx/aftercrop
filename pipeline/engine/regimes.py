# Written with an SI tool (Claude, Anthropic) for NASA Space Apps 2026. See AI_USE.md.
"""Chain regimes as configuration: which crops, in which order, sown when.

- Field (plan 10.5): for each year y, soybean and second-crop corn common to
  both arms up to the soybean harvest of y, then S1 (corn, fallow, soybean)
  and S3 (fallow, wheat, fallow, soybean). Both arms start from one state.
- Trial (plan 11.4, protocol.chains.trial): one continuous chain per site and
  arm replicating the trial sequence, published dates, the same initial state
  and a warm-up common to both arms before the first trial season. Crop end
  (``chains.trial.crop_end``): the earliest of simulated maturity, published
  harvest and the day before the next sowing; ``chain.py`` applies it, and
  ends the tested soybean on the same day in both arms (``Season.common_end``).
- A1: the same regime with a fixed restart profile at the start of each
  target soybean instance (the day after the predecessor harvest).
- P1: the same regime with every date moved by whole years.
- Side runs: CN 61 and REW 9, library Zmax, texture Ksat (protocol.engine).

Nothing here runs the model or imports aquacrop. Every value comes from an
argument or from the ``protocol:`` block. Missing ones raise ``ProtocolGap``.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date
from typing import Any, Mapping, Sequence

from .sowing import ProtocolGap, RainRule, RainSowing

KNOWN_CROPS = ("soybean", "maize", "wheat")
TRIAL_ARMS = {"after_wheat": "wheat", "after_maize": "maize"}
FIELD_ARMS = ("S1", "S3")
AFTER_HARVEST = "after_harvest"
WARMUP_DATES = "soybean on the sowing rain rule, corn on the day after the soybean harvest"

OPEN_ITEMS = {
    # D7 closed (protocol.chains.trial.warmup): soybean, second-crop corn and soybean,
    # common to both arms. trial_warmup builds soybean (Y-2)/(Y-1) on the rain rule, corn the
    # day after its harvest, and soybean (Y-1)/Y on the rain rule.
    "trial_predecessor_dates": (
        "[D7] Predecessor sowing dates come from config/trial_dates.json (the Docs' method "
        "sections). A season without one cannot be built."),
    "field_corn_sowing": (
        "[DECIDE] Second-crop corn date in field chains (prefix and S1): not in the "
        "protocol. Recommended: the day after the soybean harvest, as in the trial warm-up."),
    "field_wheat_sowing": (
        "[DECIDE] Wheat date in field S3: not in the protocol (the R1 range is "
        "'April to the 2nd dekad of May'). Recommended: a fixed MM-DD inside that range."),
    "field_prefix_years": (
        "[DECIDE] Number of soybean and corn years in the field prefix before the "
        "soybean of y. Not in the protocol."),
    "field_initial_state": (
        "[DECIDE] Initial state of field chains. Only the trial one is in the protocol "
        "(field capacity). Recommended: the same."),
}
# Closed (protocol.chains.trial.crop_end, SA-D65): a published sowing
# before the simulated harvest of the previous crop no longer stops the arm. The
# previous crop ends the day before that sowing (chain.py).


# Data types -----------------------------------------------------------------

@dataclass(frozen=True)
class Restart:
    """A fixed water profile, never zero water (A1 restart, initial state)."""
    kind: str                       # field_capacity or available_fraction
    fraction: float | None = None   # of available water, wp + f (fc - wp)

    def __post_init__(self):
        if self.kind == "field_capacity":
            if self.fraction is not None:
                raise ValueError("field_capacity takes no fraction")
        elif self.kind == "available_fraction":
            if self.fraction is None or not 0 < self.fraction <= 1:
                raise ValueError("available_fraction needs 0 < fraction <= 1, never zero water")
        else:
            raise ValueError(f"unknown restart profile {self.kind!r}")

    @classmethod
    def named(cls, name: Any) -> "Restart":
        if name == "field_capacity":
            return cls("field_capacity")
        raise ProtocolGap(f"unknown profile {name!r}: pass a Restart explicitly")

    @classmethod
    def a1_from_protocol(cls, protocol: Mapping[str, Any]) -> "Restart":
        try:
            name = protocol["tests"]["A1"]["restart_profile"]
        except (KeyError, TypeError) as e:
            raise ProtocolGap("protocol.tests.A1.restart_profile missing [DECIDE D8]") from e
        return cls.named(name)

    @classmethod
    def trial_initial_from_protocol(cls, protocol: Mapping[str, Any]) -> "Restart":
        try:
            name = protocol["chains"]["trial"]["initial_state"]
        except (KeyError, TypeError) as e:
            raise ProtocolGap("protocol.chains.trial.initial_state missing [DECIDE D7]") from e
        return cls.named(name)


@dataclass(frozen=True)
class Setup:
    """Engine options outside the crop and soil inputs. Default: the base run."""
    cn: float | None = None         # fixed CN (calc_cn=0). None: from Ksat
    rew: float | None = None        # fixed REW (adj_rew=1). None: from texture
    library_zmax: bool = False      # drop the Zmax 1.0 override

    @property
    def is_base(self) -> bool:
        return self == Setup()


@dataclass(frozen=True)
class Season:
    crop: str
    # date: fixed (published or explicit). None: the instance start day, no
    # fallow. RainSowing: the rain rule, resolved by chain.py at run time.
    sowing: date | RainSowing | None = None
    overrides: dict = field(default_factory=dict)
    restart: Restart | None = None  # replace the carried water at instance start
    label: str = ""
    role: str = ""                  # warmup, prefix, predecessor, target
    # Published harvest: the last day the crop may stand (crop end rule). None: not printed.
    harvest: date | None = None
    # Arms whose season at the same position shares this key end on the same day.
    common_end: str | None = None

    def __post_init__(self):
        if self.crop not in KNOWN_CROPS:
            raise KeyError(f"unknown crop {self.crop!r}")
        if self.sowing is not None and not isinstance(self.sowing, (date, RainSowing)):
            raise TypeError(f"sowing must be a date, a RainSowing or None, got {self.sowing!r}")
        if self.harvest is not None:
            if not isinstance(self.harvest, date):
                raise TypeError(f"harvest must be a date or None, got {self.harvest!r}")
            if isinstance(self.sowing, date) and self.harvest <= self.sowing:
                raise ValueError(f"{self.label or self.crop}: harvest {self.harvest} not after sowing {self.sowing}")


@dataclass(frozen=True)
class Regime:
    name: str
    start: date
    initial: Restart
    arms: dict  # arm name -> tuple[Season, ...]

    def __post_init__(self):
        if not self.arms:
            raise ValueError("a regime needs at least one arm")
        for name, seasons in self.arms.items():
            if not seasons:
                raise ValueError(f"arm {name} is empty")


# Protocol readers ------------------------------------------------------------

def sensitivity_setups(protocol: Mapping[str, Any]) -> dict[str, Setup]:
    """Base run and the side runs of protocol.engine.sensitivity_only."""
    try:
        side = protocol["engine"]["sensitivity_only"]
    except (KeyError, TypeError) as e:
        raise ProtocolGap("protocol.engine.sensitivity_only missing") from e
    out = {"base": Setup()}
    if "cn" in side or "rew" in side:
        if not ("cn" in side and "rew" in side):
            raise ProtocolGap("CN and REW are one side run: give both")
        out["cn_rew"] = Setup(cn=float(side["cn"]), rew=float(side["rew"]))
    if "zmax" in side:
        if side["zmax"] != "library_default":
            raise ProtocolGap(f"unknown Zmax side run {side['zmax']!r}")
        out["library_zmax"] = Setup(library_zmax=True)
    return out


@dataclass(frozen=True)
class KsatChoice:
    mm_h: float | None              # None: texture formula
    depth_m: tuple[float, float]    # layer that takes the measured value
    source: str


def ksat_choices(protocol: Mapping[str, Any]) -> dict[str, KsatChoice]:
    """Candidates in protocol.engine.ksat. Choosing one is D1, not done here."""
    try:
        k = protocol["engine"]["ksat"]
    except (KeyError, TypeError) as e:
        raise ProtocolGap("protocol.engine.ksat missing [DECIDE D1]") from e
    if k.get("mode") != "measured":
        raise ProtocolGap(f"protocol.engine.ksat.mode {k.get('mode')!r} not supported [DECIDE D1]")
    depth = tuple(float(x) for x in k["layer_m"])
    out = {"value": KsatChoice(float(k["value_mm_h"]), depth, str(k.get("source", "")))}
    if k.get("alternative_mm_h") is not None:
        out["alternative"] = KsatChoice(float(k["alternative_mm_h"]), depth,
                                        str(k.get("alternative_source", "")))
    if k.get("sensitivity") == "texture_formula":
        out["texture"] = KsatChoice(None, depth, "texture formula, sensitivity")
    return out


def _season_label(y: int) -> str:
    return f"{y}/{(y + 1) % 100:02d}"


def _season_year(label: str) -> int:
    first, second = label.split("/")
    y = int(first)
    if int(second) != (y + 1) % 100:
        raise ValueError(f"not a season label: {label!r}")
    return y


def _md_in(year: int, md: str) -> date:
    m, d = (int(x) for x in md.split("-"))
    return date(year, m, d)


def _corn(year: int, how: str, label: str, role: str) -> Season:
    if how == AFTER_HARVEST:
        return Season("maize", None, label=label, role=role)
    return Season("maize", _md_in(year, how), label=label, role=role)


# Field regime -----------------------------------------------------------------

def field_regime(y: int, rule: RainRule, *, prefix_years: int, corn_sowing: str,
                 wheat_sowing: str, s3_gap_days: int, initial: Restart) -> Regime:
    """Branch at the harvest of soybean (y-1)/y. Target: soybean y/(y+1) in S1 and S3.

    corn_sowing is AFTER_HARVEST or an MM-DD, wheat_sowing an MM-DD in year y.
    Every argument is required: see OPEN_ITEMS for the ones the protocol lacks.
    """
    if prefix_years < 0:
        raise ValueError("prefix_years must be >= 0")
    prefix: list[Season] = []
    for j in range(prefix_years, 0, -1):
        prefix.append(Season("soybean", RainSowing(rule, y - 1 - j), label=f"{_season_label(y - 1 - j)}:soybean",
                             role="prefix"))
        prefix.append(_corn(y - j, corn_sowing, f"{y - j}:maize", "prefix"))
    prefix.append(Season("soybean", RainSowing(rule, y - 1), label=f"{_season_label(y - 1)}:soybean",
                         role="prefix"))
    target = f"{_season_label(y)}:soybean"
    s1 = (*prefix, _corn(y, corn_sowing, f"{y}:maize", "predecessor"),
          Season("soybean", RainSowing(rule, y), label=target, role="target"))
    s3 = (*prefix, Season("wheat", _md_in(y, wheat_sowing), label=f"{y}:wheat", role="predecessor"),
          Season("soybean", RainSowing(rule, y, min_days_after_harvest=s3_gap_days),
                 label=target, role="target"))
    start = rule.window(y - 1 - prefix_years)[0]
    return Regime(f"field_{y}", start, initial, {"S1": s1, "S3": s3})


# Trial regime -----------------------------------------------------------------

@dataclass(frozen=True)
class TrialSeason:
    season: str                       # "2021/22", as labelled in the Doc
    soybean_sowing: date | None       # published. None: common rain-rule date
    predecessor_sowing: dict          # {"wheat": date, "maize": date}
    soybean_harvest: date | None = None          # published. None: not printed
    predecessor_harvest: dict = field(default_factory=dict)   # {"wheat": date or None, ...}


def trial_warmup(first_season: str, rule: RainRule, *, corn_sowing: str = AFTER_HARVEST) -> list[Season]:
    """Common warm-up before the first trial season (protocol.chains.trial.warmup, D7)."""
    y = _season_year(first_season)
    return [
        Season("soybean", RainSowing(rule, y - 2), label=f"warmup:{_season_label(y - 2)}:soybean",
               role="warmup"),
        _corn(y - 1, corn_sowing, f"warmup:{y - 1}:maize", "warmup"),
        Season("soybean", RainSowing(rule, y - 1), label=f"warmup:{_season_label(y - 1)}:soybean",
               role="warmup"),
    ]


def trial_warmup_from_protocol(protocol: Mapping[str, Any], first_season: str,
                               rule: RainRule) -> list[Season]:
    try:
        text = protocol["chains"]["trial"]["warmup_dates"]
    except (KeyError, TypeError) as e:
        raise ProtocolGap("protocol.chains.trial.warmup_dates missing [DECIDE D7]") from e
    if text != WARMUP_DATES:
        raise ProtocolGap(f"warm-up dates {text!r} not implemented [DECIDE D7]")
    return trial_warmup(first_season, rule)


def trial_regime(site: str, seasons: Sequence[TrialSeason], rule: RainRule, *,
                 warmup: Sequence[Season], initial: Restart,
                 arms: Mapping[str, str] = TRIAL_ARMS, start: date | None = None) -> Regime:
    """One continuous chain per arm: warm-up, then predecessor and soybean per season.

    start None: the window start of the first warm-up soybean (rain rule).
    """
    if not seasons:
        raise ValueError("no trial season")
    if not warmup or warmup[-1].crop != "soybean":
        raise ValueError("the warm-up must end with a soybean, the first predecessor follows it")
    years = [_season_year(s.season) for s in seasons]
    if years != list(range(years[0], years[0] + len(years))):
        raise ValueError(f"{site}: trial seasons must be consecutive for one continuous chain")
    out = {}
    for arm, crop in arms.items():
        seq = list(warmup)
        for s, y in zip(seasons, years):
            if crop not in s.predecessor_sowing:
                raise ProtocolGap(f"{site} {s.season}: no {crop} sowing date. "
                                  f"{OPEN_ITEMS['trial_predecessor_dates']}")
            pred = s.predecessor_sowing[crop]
            if pred.year != y:
                raise ValueError(f"{site} {s.season}: {crop} sown in {pred.year}, expected {y}")
            if s.soybean_sowing is None:
                soy = RainSowing(rule, y, common=f"{site}:{s.season}")
            else:
                if s.soybean_sowing.year != y:
                    raise ValueError(f"{site} {s.season}: soybean sown in {s.soybean_sowing.year}")
                soy = s.soybean_sowing
            seq.append(Season(crop, pred, label=f"{s.season}:{crop}", role="predecessor",
                              harvest=s.predecessor_harvest.get(crop)))
            seq.append(Season("soybean", soy, label=f"{s.season}:soybean", role="target",
                              harvest=s.soybean_harvest, common_end=f"{site}:{s.season}"))
        out[arm] = tuple(seq)
    if start is None:
        first = warmup[0].sowing
        if not isinstance(first, RainSowing):
            raise ValueError("pass start, or begin the warm-up with a rain-rule soybean")
        start = first.window()[0]
    return Regime(f"trial_{site}", start, initial, out)


# Variants ---------------------------------------------------------------------

def with_restart(regime: Regime, restart: Restart, roles: Sequence[str] = ("target",)) -> Regime:
    """A1: the restart profile replaces the carried water at each target instance."""
    arms = {name: tuple(replace(s, restart=restart) if s.role in roles else s for s in seq)
            for name, seq in regime.arms.items()}
    if all(s.restart is None for seq in arms.values() for s in seq):
        raise ValueError(f"no season with role in {list(roles)}")
    return replace(regime, name=f"{regime.name}_restart", arms=arms)


def shift_date(d: date, years: int) -> date:
    """Same month and day, ``years`` later. 29 February moves to 28 February."""
    y = d.year + years
    if d.month == 2 and d.day == 29:
        try:
            return date(y, 2, 29)
        except ValueError:
            return date(y, 2, 28)
    return d.replace(year=y)


def shifted(regime: Regime, years: int) -> Regime:
    """P1: the whole chain moves by one offset. The bench computes the offset."""
    def move(s: Season) -> Season:
        harvest = None if s.harvest is None else shift_date(s.harvest, years)
        if isinstance(s.sowing, date):
            return replace(s, sowing=shift_date(s.sowing, years), harvest=harvest)
        if isinstance(s.sowing, RainSowing):
            return replace(s, sowing=replace(s.sowing, season_year=s.sowing.season_year + years), harvest=harvest)
        return replace(s, harvest=harvest)
    arms = {name: tuple(move(s) for s in seq) for name, seq in regime.arms.items()}
    return replace(regime, name=f"{regime.name}_shift{years:+d}",
                   start=shift_date(regime.start, years), arms=arms)

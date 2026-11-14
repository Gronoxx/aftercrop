# Written with an SI tool (Claude, Anthropic) for NASA Space Apps 2026. See AI_USE.md.
"""Chain mode: one AquaCrop-OSPy instance per crop, only soil water carries over.

Each instance starts the day after the previous harvest, simulates the
fallow off-season up to sowing and stops at harvest. Canopy, biomass, roots,
residue and the evaporation state reset at every handover.

Sowing is resolved at run time: a fixed date, the instance start, or the
rain rule (``sowing.RainSowing``), which needs the previous harvest. Arms
that share a leading run of identical seasons run it once and branch from
the same state (plan 10.5). A rain-rule season with a ``common`` key takes one
date for every arm, the first that meets the rule in all of them.

Crop end (``protocol.chains.trial.crop_end``): each crop ends on the earliest
of its simulated maturity, its published harvest (``Season.harvest``) and the
day before the next fixed sowing of the arm. Seasons that several arms run
once (the shared prefix) take the earliest next sowing over those arms.
Seasons with the same ``common_end`` key at the same position (the tested
soybean of a trial site-season) end on the same day in every arm: the
earliest end over the arms whose crop did not die. An arm whose crop would
stand longer is run again to that day. ``SeasonResult.end_rule`` says which
rule ended each crop.

A published sowing before the simulated harvest of the previous crop no
longer stops the arm: the previous crop ends the day before it. An arm
stops, with a structured record in ``ChainResult.stopped``, only when the
rain rule does not trigger by the latest date (the season counts as a
failure). Published dates out of order (a crop whose end would fall on or
before its own sowing) raise, as missing climate does: that is an input or
infrastructure failure.

Long fallow (late note, found in the real run R2): when crops die
early the fallow before the next sowing can last more than a year. OSPy places
plantings by month and day, so such an instance would hold two seasons. The
days up to the previous anniversary of the sowing then run first as bare soil,
in consecutive pieces of at most ``FALLOW_PIECE_DAYS`` days that carry the
water state, every day with its water balance and no reset; the crop instance
follows from the next day. The pieces join the instance accounting (start,
storage, fluxes, days). An instance shorter than a year has no piece and runs
exactly as before.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Mapping, Sequence

import numpy as np
import pandas as pd
from aquacrop import AquaCropModel, InitialWaterContent

from .crops import make_crop
from .regimes import Regime, Restart, Season, Setup
from .soilmodel import DZ, Layer, make_soil, midpoints, storage_mm, water_limits
from .sowing import RainSowing, common_trigger, rain_series, trigger

__all__ = ["Season", "SeasonResult", "ChainResult", "CalendarError", "run_season",
           "run_chain", "run_arms", "run_regime", "restart_profile", "last_planted_row",
           "end_cap", "END_RULES"]

HORIZON_DAYS = 300  # sim end after sowing: past maturity, before next anniversary
# Long fallow (late note, R2): OSPy places plantings by month and day, so an
# instance whose start falls on or before the previous anniversary of its sowing holds two
# seasons. The fallow up to that anniversary then runs first, in pieces of at most this many
# days, carrying th, every day with its water balance (see _fallow_piece).
FALLOW_PIECE_DAYS = 300
CONSERVATION_TOL_MM = 1.0
# What ended a crop. "horizon": none of the rules before the instance horizon.
END_RULES = ("maturity", "crop_dead", "published_harvest", "next_sowing", "common_end", "horizon")


class CalendarError(RuntimeError):
    """The climate cannot carry the crop to maturity in this instance."""


@dataclass
class SeasonResult:
    crop: str
    start: date
    sowing: date
    harvest: date
    th_start: np.ndarray
    th_end: np.ndarray
    storage_start_mm: float
    storage_end_mm: float
    dry_yield_t_ha: float
    matured: bool
    crop_dead: bool
    germinated: bool
    days_to_emergence: int | None  # first day with canopy > 0, None if never
    canopy_max: float
    curve_number: float
    runoff_mm: float
    deep_perc_mm: float
    transpiration_mm: float
    sim_days: int
    init_seconds: float  # model build and initialization
    seconds: float  # whole instance, including output extraction
    label: str = ""
    role: str = ""
    sowing_source: str = "fixed"  # fixed, instance_start, rain_rule, rain_rule_common
    sowing_window: tuple[date, date] | None = None
    restarted: bool = False  # th_start is a restart profile, not the carried water
    sim_end: date | None = None
    end_cap: date | None = None  # latest last crop day allowed by the crop end rule
    end_rule: str = "maturity"   # one of END_RULES
    rain_mm: float = 0.0
    evaporation_mm: float = 0.0
    balance_residual_mm: float = 0.0  # rain + inflows - outflows - storage change
    yield_potential_t_ha: float = float("nan")


@dataclass
class ChainResult:
    seasons: list[SeasonResult]
    handover_gap_mm: list[float | None]  # None at a restart handover
    stopped: dict | None = None
    shared_prefix: int = 0  # leading seasons shared with the other arms

    @property
    def completed(self) -> bool:
        return self.stopped is None

    @property
    def seconds(self) -> float:
        return sum(s.seconds for s in self.seasons)


def _fmt(d: date) -> str:
    return d.strftime("%Y/%m/%d")


def _initial_water(th: np.ndarray | None, dz: Sequence[float],
                   n_layers: int) -> InitialWaterContent:
    if th is None:
        # The OSPy default (layer 1 only) leaves deeper layers at zero water.
        return InitialWaterContent(wc_type="Prop", method="Layer",
                                   depth_layer=list(range(1, n_layers + 1)),
                                   value=["FC"] * n_layers)
    th = np.asarray(th, dtype=float)
    if th.shape != (len(dz),):
        raise ValueError(f"th has {th.shape}, expected ({len(dz)},)")
    # Values at compartment midpoints: OSPy interpolates there, so the
    # profile is reproduced exactly.
    return InitialWaterContent(wc_type="Num", method="Depth",
                               depth_layer=list(midpoints(dz)), value=list(th))


def restart_profile(restart: Restart, layers: Sequence[Layer], dz: Sequence[float] = DZ) -> np.ndarray:
    """Per-compartment water of a fixed profile. Never zero water."""
    wp, fc = water_limits(layers, dz)
    if restart.kind == "field_capacity":
        return fc.copy()
    return wp + float(restart.fraction) * (fc - wp)


def last_planted_row(ws: pd.DataFrame) -> pd.Series:
    """Last day with dap > 0. Rows after the final step are zero-filled."""
    planted = ws.loc[ws["dap"] > 0]
    if planted.empty:
        raise RuntimeError("crop was never sown in this instance")
    return ws.loc[planted.index.max()]


def _weather_last(weather: pd.DataFrame) -> date:
    return pd.Timestamp(weather["Date"].max()).date()


def _anniversary(d: date) -> date:
    """The same month and day one year before (28 February for a 29 February)."""
    try:
        return d.replace(year=d.year - 1)
    except ValueError:
        return date(d.year - 1, 2, 28)


@dataclass
class _FallowPiece:
    """Bare-soil days of a long fallow, run before the crop instance (late note)."""
    start: date
    end: date
    th_start: np.ndarray
    th_end: np.ndarray
    inflow_mm: float      # irrigation, capillary rise and groundwater inflow (rain is counted apart)
    outflow_mm: float     # runoff, deep percolation, tile drainage, soil evaporation, transpiration
    surface_mm: float     # surface storage at the end of the piece (not carried, as at any handover)
    runoff_mm: float
    deep_perc_mm: float
    transpiration_mm: float
    evaporation_mm: float
    days: int
    init_seconds: float


def _fallow_piece(weather: pd.DataFrame, layers: Sequence[Layer], season: Season, start: date,
                  end: date, th: np.ndarray | None, dz: Sequence[float], setup: Setup) -> _FallowPiece:
    """Water balance of the bare soil from ``start`` to ``end``, both included, from ``th``.

    OSPy always needs a crop, so the instance takes the same crop sown the day after ``end``
    (a probe never reached) and runs only the days up to ``end``. Every one of those days is
    checked to have no crop (dap 0): it is the off-season soil water balance that a single
    instance would have run on those days. The piece spans less than a year, so its own
    calendar holds one season.
    """
    probe = end + timedelta(days=1)
    n = (end - start).days + 1
    if not 0 < n <= FALLOW_PIECE_DAYS:
        raise ValueError(f"fallow piece of {n} days, expected 1 to {FALLOW_PIECE_DAYS}")
    t0 = time.perf_counter()
    model = AquaCropModel(
        sim_start_time=_fmt(start),
        sim_end_time=_fmt(min(probe + timedelta(days=HORIZON_DAYS), _weather_last(weather))),
        weather_df=weather,
        soil=make_soil(layers, dz, cn=setup.cn, rew=setup.rew),
        crop=make_crop(season.crop, probe, library_zmax=setup.library_zmax, **season.overrides),
        initial_water_content=_initial_water(th, dz, len(layers)),
        off_season=True,
    )
    try:
        model._initialize()
    except AssertionError as e:
        raise CalendarError(f"fallow piece {start} to {end}: {e}") from e
    init_seconds = time.perf_counter() - t0
    clock = model._clock_struct
    if clock.n_seasons != 1 or pd.Timestamp(clock.planting_dates[0]) != pd.Timestamp(probe):
        raise RuntimeError(f"fallow piece {start} to {end}: unexpected calendar {clock.planting_dates}")
    if not np.allclose(model._param_struct.Soil.profile["dz"].to_numpy(), dz):
        raise RuntimeError("soil geometry changed during initialization")
    th_start = np.array(model._init_cond.th, dtype=float)
    model.run_model(num_steps=n, initialize_model=False, process_outputs=True)
    ws = model.get_water_storage().iloc[:n]
    flux = model.get_water_flux().iloc[:n]
    if len(ws) != n or (ws["dap"] != 0).any() or (flux["dap"] != 0).any():
        raise RuntimeError(f"fallow piece {start} to {end}: a crop day inside the fallow")
    if pd.Timestamp(clock.time_span[n - 1]).date() != end:
        raise RuntimeError(f"fallow piece {start} to {end}: time steps do not match the days")
    th_end = ws.iloc[n - 1][[c for c in ws.columns if c.startswith("th")]].to_numpy(dtype=float)
    return _FallowPiece(
        start=start, end=end, th_start=th_start, th_end=th_end,
        inflow_mm=float(flux["IrrDay"].sum() + flux["CR"].sum() + flux["GwIn"].sum()),
        outflow_mm=float(flux[["Runoff", "DeepPerc", "TileDrain", "Es", "Tr"]].to_numpy().sum()),
        surface_mm=float(flux["surface_storage"].to_numpy()[n - 1]),
        runoff_mm=float(flux["Runoff"].sum()), deep_perc_mm=float(flux["DeepPerc"].sum()),
        transpiration_mm=float(flux["Tr"].sum()), evaporation_mm=float(flux["Es"].sum()),
        days=n, init_seconds=init_seconds,
    )


def run_season(weather: pd.DataFrame, layers: Sequence[Layer], season: Season,
               start: date, th: np.ndarray | None,
               dz: Sequence[float] = DZ, *, sowing: date | None = None,
               sowing_source: str | None = None,
               sowing_window: tuple[date, date] | None = None,
               setup: Setup = Setup(), restarted: bool = False,
               last_day: date | None = None, cap_rule: str | None = None) -> SeasonResult:
    """One instance. ``sowing`` overrides the season spec (resolved rain rule).

    ``last_day`` is the last day the crop may stand (crop end rule), ``cap_rule`` the rule
    that set it. The crop ends on the earlier of its own end and ``last_day``.
    """
    if sowing is None:
        if isinstance(season.sowing, RainSowing):
            raise ValueError("rain-rule sowing is resolved by run_chain or run_arms")
        sowing = start if season.sowing is None else season.sowing
        sowing_source = sowing_source or ("instance_start" if season.sowing is None else "fixed")
    if sowing < start:
        raise ValueError(f"sowing {sowing} before instance start {start}")
    if last_day is not None and last_day <= sowing:
        raise ValueError(f"{season.label or season.crop}: crop end {last_day} ({cap_rule}) on or before "
                         f"sowing {sowing}: published dates out of order")
    last = _weather_last(weather)
    end = min(sowing + timedelta(days=HORIZON_DAYS), last)
    if end <= sowing:
        raise CalendarError(f"climate ends {last}, no room after sowing {sowing}")
    t0 = time.perf_counter()
    # Long fallow: the days up to the previous anniversary of the sowing run first, as bare
    # soil, so the crop instance spans less than a year (FALLOW_PIECE_DAYS). Without a long
    # fallow there is no piece and the instance is built exactly as before.
    pieces: list[_FallowPiece] = []
    crop_start, crop_th = start, th
    while crop_start <= _anniversary(sowing):
        piece_end = min(_anniversary(sowing), crop_start + timedelta(days=FALLOW_PIECE_DAYS - 1))
        piece = _fallow_piece(weather, layers, season, crop_start, piece_end, crop_th, dz, setup)
        pieces.append(piece)
        crop_start, crop_th = piece_end + timedelta(days=1), piece.th_end
    model = AquaCropModel(
        sim_start_time=_fmt(crop_start),
        sim_end_time=_fmt(end),
        weather_df=weather,
        soil=make_soil(layers, dz, cn=setup.cn, rew=setup.rew),
        crop=make_crop(season.crop, sowing, library_zmax=setup.library_zmax, **season.overrides),
        initial_water_content=_initial_water(crop_th, dz, len(layers)),
        off_season=True,
    )
    t_init = time.perf_counter()
    try:
        model._initialize()
    except AssertionError as e:  # OSPy asserts on the crop calendar
        raise CalendarError(f"{season.crop} sown {sowing}, sim end {end}: {e}") from e
    init_seconds = (time.perf_counter() - t0 if not pieces
                    else sum(p.init_seconds for p in pieces) + time.perf_counter() - t_init)
    clock = model._clock_struct
    if clock.n_seasons != 1:
        raise RuntimeError(f"expected 1 season in instance, got {clock.n_seasons}")
    if last_day is not None:
        # OSPy ends the season on the step whose end is the harvest date, so the last
        # day with the crop is the day before it. Only an earlier end is imposed.
        stop = pd.Timestamp(last_day + timedelta(days=1))
        if stop < pd.Timestamp(clock.harvest_dates[0]):
            clock.harvest_dates[0] = stop
    prof = model._param_struct.Soil.profile
    if not np.allclose(prof["dz"].to_numpy(), dz):
        raise RuntimeError("soil geometry changed during initialization")
    th_start = np.array(model._init_cond.th, dtype=float)
    if pieces:
        gap = abs(storage_mm(th_start, dz) - storage_mm(pieces[-1].th_end, dz))
        if gap > CONSERVATION_TOL_MM:
            raise RuntimeError(f"water not conserved after the long fallow: {gap:.3f} mm")
        th_start = pieces[0].th_start
    model.run_model(till_termination=True, initialize_model=False)

    ws = model.get_water_storage()
    row = last_planted_row(ws)
    th_end = row[[c for c in ws.columns if c.startswith("th")]].to_numpy(dtype=float)
    if not th_end.sum() > 0:
        raise RuntimeError("carried water state is empty")
    step = int(row["time_step_counter"])
    harvest = clock.time_span[step].date()
    if last_day is not None and harvest > last_day:
        raise RuntimeError(f"crop stood until {harvest}, after its end {last_day}")

    growth = model.get_crop_growth()
    in_season = growth.loc[growth["dap"] > 0]
    emerged = in_season.loc[in_season["canopy_cover"] > 0, "dap"]
    stats = model.get_simulation_results()
    flux = model.get_water_flux().iloc[: step + 1]
    days = (weather["Date"] >= pd.Timestamp(start)) & (weather["Date"] <= pd.Timestamp(harvest))
    rain = float(weather.loc[days, "Precipitation"].sum())
    if int(days.sum()) != len(flux) + sum(p.days for p in pieces):
        raise RuntimeError("water flux rows do not match the instance days")
    s0, s1 = storage_mm(th_start, dz), storage_mm(th_end, dz)
    inflow = rain + float(flux["IrrDay"].sum() + flux["CR"].sum() + flux["GwIn"].sum())
    outflow = float(flux[["Runoff", "DeepPerc", "TileDrain", "Es", "Tr"]].to_numpy().sum())
    surface = float(flux["surface_storage"].to_numpy()[step])
    runoff, deep = float(flux["Runoff"].sum()), float(flux["DeepPerc"].sum())
    transp, evap, sim_days = float(flux["Tr"].sum()), float(flux["Es"].sum()), step + 1
    if pieces:  # the long fallow joins the instance accounting (no piece: values as before)
        inflow += sum(p.inflow_mm for p in pieces)
        outflow += sum(p.outflow_mm for p in pieces)
        surface += sum(p.surface_mm for p in pieces)   # not carried at a piece handover
        runoff += sum(p.runoff_mm for p in pieces)
        deep += sum(p.deep_perc_mm for p in pieces)
        transp += sum(p.transpiration_mm for p in pieces)
        evap += sum(p.evaporation_mm for p in pieces)
        sim_days += sum(p.days for p in pieces)
    cond = model._init_cond
    matured, dead = bool(cond.crop_mature), bool(cond.crop_dead)
    if dead:
        end_rule = "crop_dead"
    elif matured:
        end_rule = "maturity"
    elif last_day is not None and harvest == last_day:
        end_rule = cap_rule or "next_sowing"
    else:
        end_rule = "horizon"
    seconds = time.perf_counter() - t0
    return SeasonResult(
        crop=season.crop, start=start, sowing=sowing, harvest=harvest,
        th_start=th_start, th_end=th_end, storage_start_mm=s0, storage_end_mm=s1,
        dry_yield_t_ha=float(stats["Dry yield (tonne/ha)"].sum()) if len(stats) else 0.0,
        matured=matured, crop_dead=dead,
        germinated=bool(cond.germination),
        days_to_emergence=int(emerged.min()) if len(emerged) else None,
        canopy_max=float(in_season["canopy_cover"].max()) if len(in_season) else 0.0,
        curve_number=float(model._param_struct.Soil.cn),
        runoff_mm=runoff, deep_perc_mm=deep,
        transpiration_mm=transp,
        sim_days=sim_days, init_seconds=init_seconds, seconds=seconds,
        label=season.label, role=season.role,
        sowing_source=sowing_source or "fixed", sowing_window=sowing_window,
        restarted=restarted, sim_end=end, end_cap=last_day, end_rule=end_rule, rain_mm=rain,
        evaporation_mm=evap,
        balance_residual_mm=inflow - outflow - (s1 - s0) - surface,
        yield_potential_t_ha=(float(stats["Yield potential (tonne/ha)"].sum())
                              if len(stats) else float("nan")),
    )


# Chains -----------------------------------------------------------------------

@dataclass
class _State:
    th: np.ndarray | None
    day: date  # next instance start
    prev_harvest: date | None = None
    results: list = field(default_factory=list)
    gaps: list = field(default_factory=list)
    stopped: dict | None = None

    def copy(self) -> "_State":
        return _State(self.th, self.day, self.prev_harvest, list(self.results),
                      list(self.gaps), self.stopped)

    def result(self, shared: int = 0) -> ChainResult:
        return ChainResult(list(self.results), list(self.gaps), self.stopped, shared)


_UNSET = object()


def _stop(st: _State, index: int, season: Season, reason: str, **detail) -> None:
    st.stopped = {"index": index, "crop": season.crop, "label": season.label,
                  "role": season.role, "reason": reason,
                  **{k: (v.isoformat() if isinstance(v, date) else v) for k, v in detail.items()}}


def end_cap(season: Season, next_seasons: Sequence[Season | None]) -> tuple[date | None, str | None]:
    """Latest last day of ``season`` by the crop end rule, and the rule that sets it.

    The published harvest, and the day before the next fixed sowing; with several next
    seasons (a prefix shared by arms that diverge), the earliest of them. A next sowing by
    the rain rule or at the instance start never caps: it waits for the harvest.
    """
    best: tuple[date | None, str | None] = (season.harvest, "published_harvest" if season.harvest else None)
    for nxt in next_seasons:
        if nxt is not None and isinstance(nxt.sowing, date):
            d = nxt.sowing - timedelta(days=1)
            if best[0] is None or d < best[0]:
                best = (d, "next_sowing")
    return best


@dataclass
class _Plan:
    """A season ready to run in one arm: sowing resolved, start water chosen."""
    season: Season
    sowing: date
    source: str
    window: tuple[date, date] | None
    th: np.ndarray | None
    restarted: bool
    cap: date | None
    cap_rule: str | None


def _prepare(rain: pd.Series, season: Season, st: _State, index: int, layers: Sequence[Layer],
             dz: Sequence[float], next_seasons: Sequence[Season | None], common_date=_UNSET) -> _Plan | None:
    """Resolve the sowing of ``season`` in one arm. None when the arm stops (recorded in ``st``)."""
    if st.stopped is not None:
        return None
    restarted = season.restart is not None
    th = restart_profile(season.restart, layers, dz) if restarted else st.th
    window = None
    spec = season.sowing
    if isinstance(spec, RainSowing):
        window = spec.window()
        earliest = spec.not_before(st.day, st.prev_harvest)
        if common_date is _UNSET:
            sowing, source = trigger(rain, spec, earliest), "rain_rule"
        else:
            sowing, source = common_date, "rain_rule_common"
        if sowing is None:
            _stop(st, index, season, "sowing_not_triggered", window_start=window[0],
                  latest=window[1], earliest=earliest, common=spec.common)
            return None
    elif isinstance(spec, date):
        sowing, source = spec, "fixed"
    else:
        sowing, source = st.day, "instance_start"
    if sowing < st.day:
        # Unreachable with the crop end rule: the previous crop ends the day before a
        # fixed sowing. Kept as a guard for chains built by hand.
        _stop(st, index, season, "sowing_before_previous_harvest", sowing=sowing,
              previous_harvest=st.prev_harvest, instance_start=st.day)
        return None
    cap, rule = end_cap(season, next_seasons)
    return _Plan(season, sowing, source, window, th, restarted, cap, rule)


def _run(weather: pd.DataFrame, layers: Sequence[Layer], plan: _Plan, st: _State, dz: Sequence[float],
         setup: Setup, last_day: date | None = None, rule: str | None = None) -> SeasonResult:
    return run_season(weather, layers, plan.season, st.day, plan.th, dz, sowing=plan.sowing,
                      sowing_source=plan.source, sowing_window=plan.window, setup=setup,
                      restarted=plan.restarted, last_day=plan.cap if last_day is None else last_day,
                      cap_rule=plan.cap_rule if rule is None else rule)


def _commit(st: _State, res: SeasonResult, restarted: bool) -> None:
    if st.results:
        if restarted:
            st.gaps.append(None)
        else:
            gap = abs(res.storage_start_mm - st.results[-1].storage_end_mm)
            if gap > CONSERVATION_TOL_MM:
                raise RuntimeError(f"water not conserved at handover: {gap:.3f} mm")
            st.gaps.append(gap)
    st.results.append(res)
    st.th, st.day, st.prev_harvest = res.th_end, res.harvest + timedelta(days=1), res.harvest


def _advance(weather: pd.DataFrame, rain: pd.Series, layers: Sequence[Layer], season: Season,
             st: _State, index: int, dz: Sequence[float], setup: Setup,
             common_date=_UNSET, next_seasons: Sequence[Season | None] = ()) -> None:
    plan = _prepare(rain, season, st, index, layers, dz, next_seasons, common_date)
    if plan is not None:
        _commit(st, _run(weather, layers, plan, st, dz, setup), plan.restarted)


def _advance_common_end(weather: pd.DataFrame, rain: pd.Series, layers: Sequence[Layer],
                        members: Sequence[str], seqs: Mapping[str, Sequence[Season]],
                        states: Mapping[str, _State], index: int, dz: Sequence[float], setup: Setup,
                        resolved: Mapping[str, date | None]) -> None:
    """Seasons that end on the same day in every member arm (the tested soybean)."""
    plans, results = {}, {}
    for m in members:
        nxt = seqs[m][index + 1] if index + 1 < len(seqs[m]) else None
        plan = _prepare(rain, seqs[m][index], states[m], index, layers, dz, [nxt], resolved.get(m, _UNSET))
        if plan is not None:
            plans[m] = plan
            results[m] = _run(weather, layers, plan, states[m], dz, setup)
    alive = [r.harvest for r in results.values() if not r.crop_dead]
    if alive:
        day = min(alive)
        for m, r in results.items():
            if not r.crop_dead and r.harvest > day:
                results[m] = _run(weather, layers, plans[m], states[m], dz, setup, day, "common_end")
    for m, r in results.items():
        _commit(states[m], r, plans[m].restarted)


def run_chain(weather: pd.DataFrame, layers: Sequence[Layer], seasons: Sequence[Season],
              start: date, th0: np.ndarray | None = None,
              dz: Sequence[float] = DZ, setup: Setup = Setup()) -> ChainResult:
    """Run seasons in order. th0 None starts the first instance at field capacity."""
    return run_arms(weather, layers, {"chain": seasons}, start, th0, dz, setup)["chain"]


def run_arms(weather: pd.DataFrame, layers: Sequence[Layer],
             arms: Mapping[str, Sequence[Season]], start: date,
             th0: np.ndarray | None = None, dz: Sequence[float] = DZ,
             setup: Setup = Setup()) -> dict[str, ChainResult]:
    """Run every arm from one state. The leading identical seasons run once."""
    if not arms:
        raise ValueError("no arm")
    names = list(arms)
    seqs = {n: tuple(arms[n]) for n in names}
    rain = rain_series(weather)
    first = seqs[names[0]]
    p = 0
    while all(p < len(seqs[n]) for n in names) and all(seqs[n][p] == first[p] for n in names):
        p += 1
    shared = _State(None if th0 is None else np.asarray(th0, dtype=float), start)
    for i in range(p):
        nexts = [seqs[n][i + 1] if i + 1 < len(seqs[n]) else None for n in names]
        _advance(weather, rain, layers, first[i], shared, i, dz, setup, next_seasons=nexts)
    states = {n: shared.copy() for n in names}
    for i in range(p, max(len(s) for s in seqs.values())):
        groups: dict[str, list[str]] = {}
        for n in names:
            s = seqs[n][i] if i < len(seqs[n]) else None
            if (s is not None and states[n].stopped is None and isinstance(s.sowing, RainSowing)
                    and s.sowing.common is not None):
                groups.setdefault(s.sowing.common, []).append(n)
        resolved = {}
        for key, members in groups.items():
            specs = {seqs[m][i].sowing for m in members}
            if len(specs) != 1:
                raise ValueError(f"arms disagree on the rule of common sowing {key!r}")
            spec = specs.pop()
            earliest = {m: spec.not_before(states[m].day, states[m].prev_harvest) for m in members}
            d = common_trigger(rain, spec, earliest)
            resolved.update({m: d for m in members})
        ends: dict[str, list[str]] = {}
        for n in names:
            s = seqs[n][i] if i < len(seqs[n]) else None
            if s is not None and s.common_end is not None and states[n].stopped is None:
                ends.setdefault(s.common_end, []).append(n)
        grouped = {m for members in ends.values() if len(members) > 1 for m in members}
        for n in names:
            if i < len(seqs[n]) and n not in grouped:
                nxt = seqs[n][i + 1] if i + 1 < len(seqs[n]) else None
                _advance(weather, rain, layers, seqs[n][i], states[n], i, dz, setup,
                         resolved.get(n, _UNSET), next_seasons=[nxt])
        for members in ends.values():
            if len(members) > 1:
                _advance_common_end(weather, rain, layers, members, seqs, states, i, dz, setup, resolved)
    shared_n = p if len(names) > 1 else 0
    return {n: states[n].result(min(shared_n, len(states[n].results))) for n in names}


def run_regime(weather: pd.DataFrame, layers: Sequence[Layer], regime: Regime,
               dz: Sequence[float] = DZ, setup: Setup = Setup()) -> dict[str, ChainResult]:
    th0 = restart_profile(regime.initial, layers, dz)
    return run_arms(weather, layers, regime.arms, regime.start, th0, dz, setup)

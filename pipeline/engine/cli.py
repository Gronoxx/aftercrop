# Written with an SI tool (Claude, Anthropic) for NASA Space Apps 2026. See AI_USE.md.
"""Engine command line: trial chains and field chains into ``data/runs/`` (plan 8.3, sheet M3).

Subcommands, run from the repository root:

    .venv/bin/python -m pipeline.engine.cli config-json --out data/runs/config.json
    .venv-engine/bin/python -m pipeline.engine.cli trials --config data/runs/config.json \
        --trial-dates config/trial_dates.json --variants base,a1,p1 [--dry-run]
    .venv-engine/bin/python -m pipeline.engine.cli field --config data/runs/config.json \
        --field a --years 2019,2020 --prefix-years 0 --corn-sowing after_harvest \
        --wheat-sowing 05-01 --initial field_capacity --weather-site londrina \
        --setups base,cn_rew --repeat [--dry-run]

``config-json`` exists because ``.venv-engine`` has no PyYAML: it turns the YAML
config into JSON once, with ``.venv``. The other subcommands read either.

Guard: ``trials`` refuses to simulate anything while ``protocol.status`` is not
"tagged" (exit code 2), because nothing may run on the trial sites before the
protocol is tagged. ``--dry-run`` simulates nothing, imports no aquacrop and
reads no weather or soil: it prints the runs and what the guard would do.

Outputs (plan 8.3, row M3). One row per season instance and arm, in run order:

    trials_<site>.csv        base run (T0, T1)
    trials_<site>_a1.csv     A1, restart at each target soybean instance
    trials_<site>_p1.csv     P1, every shift k (columns shift_k and offset_years)
    chain_<field>.csv        field base run; chain_<field>_<variant>.csv for the
                             side runs (cn_rew, library_zmax, ksat_<name>) and
                             chain_<field>_repeat.csv for the second run of L0
    timing.json              seconds per instance, machine and cores (M3.7), merged
    artifacts.json           protocol artifact checks per regime and arm (M3.6), merged,
                             written by the engine before the bench reads any delta

A season that did not run has ``outcome`` "sowing_not_triggered" (the stop of
``chain.run_arms``) or "after_stop" for the seasons after it, with the reason in
``stop_reason``. The engine never turns them into numbers: the bench reads them.
Crop end (``protocol.chains.trial.crop_end``): the earliest of simulated maturity,
published harvest and the day before the next sowing, in ``end_rule``. A published
sowing before the simulated previous harvest no longer stops the arm.

P1 (``protocol.tests.P1``): a site-season starting in year y takes the offset
s - y, with s = y - k plus 40 when y - k is before 1985. Its chain moves by that
offset. With continuous chains (``chains.trial.run: continuous_per_site_and_arm``,
D7 a) the chain of a site-season is the site chain up to that season. Seasons
with the same offset share one run of the chain up to the last of them; the
others are run again under their own offset. ``offset_years`` says which run a
row comes from. Warm-up (``chains.trial.p1_warmup``): the years of a shifted chain
follow the same 1985 to 2024 ring, year by year. A shifted year before 1985 takes
the climate of that year plus 40 (same leap years), so the ring seam may fall in
the warm-up, never in a tested season, and no P1 run needs climate before 1984.
``ring_years`` in timing.json lists the years mapped.

Inputs, all files (plan 8.3): the config (``protocol:`` and ``trial_sites``),
the POWER cache ``<data>/raw/power_daily/<site>_met_<start>_<end>_LST.json``
with ``<data>/interim/power_summary.json`` (sources by stretch), the soil
``<data>/interim/trial_<site>_soil.json`` or ``field_<id>_soil.json`` (layers
with top_m, bottom_m, sand_pct, clay_pct, om_pct) and, for trials, the trial
dates file. Nothing here imports the bench or reads a trial result.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import platform
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from .regimes import (AFTER_HARVEST, Regime, Restart, Setup, TrialSeason, field_regime, ksat_choices,
                      sensitivity_setups, shifted, trial_regime, trial_warmup_from_protocol, with_restart)
from .sowing import ProtocolGap, RainRule, RainSowing, s3_gap_days

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = ROOT / "config" / "region_norte_pr.yaml"
DEFAULT_DATA = ROOT / "data"
DEFAULT_TRIAL_DATES = ROOT / "config" / "trial_dates.json"   # dates from the Docs' method sections
TAGGED = "tagged"
TRIAL_VARIANTS = ("base", "a1", "p1")
CONTINUOUS = "continuous_per_site_and_arm"   # D7 (a)
PER_SEASON = "per_site_season"               # D7 (b)
SCHEMA_VERSION = 1

SEASON_COLUMNS = (
    "run_key", "arm", "index", "label", "role", "crop", "season", "outcome", "stop_reason", "shared_prefix",
    "start", "sowing", "harvest", "end_rule", "sowing_source", "dry_yield_t_ha", "yield_potential_t_ha",
    "matured", "crop_dead", "germinated", "days_to_emergence", "canopy_max",
    "storage_start_mm", "storage_end_mm", "handover_gap_mm", "restarted",
    "runoff_mm", "deep_perc_mm", "transpiration_mm", "rain_mm", "balance_residual_mm",
    "sim_days", "init_seconds", "seconds",
)
TRIAL_COLUMNS = ("site", "variant", "shift_k", "offset_years") + SEASON_COLUMNS
CHAIN_COLUMNS = ("field_id", "year", "variant", "setup", "ksat", "run") + SEASON_COLUMNS
TIMING_COLUMNS = ("init_seconds", "seconds")     # differ between identical runs (L0 repeat ignores them)
SIMULATED = "simulated"
AFTER_STOP = "after_stop"
_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]+$")


class Refused(RuntimeError):
    """The protocol forbids the run (trial sites before the tag)."""


# Config ------------------------------------------------------------------------

def load_doc(path: str | Path) -> Any:
    """JSON, or YAML when PyYAML is installed (.venv). .venv-engine reads JSON only."""
    path = Path(path)
    text = path.read_text(encoding="utf-8")
    if path.suffix == ".json":
        return json.loads(text)
    try:
        import yaml
    except ImportError as e:
        raise RuntimeError(f"{path}: PyYAML is not installed here. Convert it first with "
                           f"`.venv/bin/python -m pipeline.engine.cli config-json --config {path} "
                           f"--out <file>.json`") from e
    return yaml.safe_load(text)


def protocol_of(config: Mapping[str, Any]) -> Mapping[str, Any]:
    block = config.get("protocol") if isinstance(config, Mapping) else None
    if not isinstance(block, Mapping):
        raise ProtocolGap("the config has no protocol: block")
    return block


def require_tagged(protocol: Mapping[str, Any]) -> None:
    """Nothing is simulated on the trial sites before the protocol is tagged."""
    status = protocol.get("status")
    if status != TAGGED:
        raise Refused(
            f"protocol.status is {status!r}, not {TAGGED!r}: nothing may be simulated on the trial "
            f"sites before the protocol is tagged ({protocol.get('id', 'protocol-v1')}). "
            "Tag the protocol first. --dry-run lists the runs without simulating.")


def trial_site_seasons(protocol: Mapping[str, Any]) -> dict[str, list[str]]:
    listed = protocol.get("trial_site_seasons")
    if not isinstance(listed, Mapping) or not listed:
        raise ProtocolGap("protocol.trial_site_seasons is missing")
    return {site: [str(s) for s in spec["seasons"]] for site, spec in listed.items()}


def site_config(config: Mapping[str, Any], site: str) -> Mapping[str, Any]:
    for s in config.get("trial_sites") or ():
        if s.get("id") == site:
            return s
    raise ProtocolGap(f"trial_sites has no site {site!r}")


def _bool_arg(text: str | None) -> bool | None:
    if text is None:
        return None
    t = text.strip().lower()
    if t in ("true", "yes", "1"):
        return True
    if t in ("false", "no", "0"):
        return False
    raise argparse.ArgumentTypeError(f"expected true or false, got {text!r}")


def _list_arg(text: str | None) -> list[str]:
    return [x.strip() for x in (text or "").split(",") if x.strip()]


def _date(value: Any, what: str) -> date | None:
    if value is None:
        return None
    try:
        return date.fromisoformat(str(value))
    except ValueError as e:
        raise ValueError(f"{what}: not an ISO date: {value!r}") from e


def load_trial_dates(path: str | Path, protocol: Mapping[str, Any],
                     sites: Sequence[str]) -> dict[str, list[TrialSeason]]:
    """Trial sowing dates per site, in the season order of protocol.trial_site_seasons.

    Shape: {"sites": {"<site>": [{"season": "2021/22", "soybean_sowing": "2021-10-20" or null,
    "predecessor_sowing": {"wheat": "2021-05-10", "maize": "2021-02-20"},
    "soybean_harvest": "2022-02-01" or null, "predecessor_harvest": {"wheat": ... or null},
    "source": "Doc ..., page"}]}}. A null soybean date is the Floresta rule: both arms take
    the first date that meets the rain rule in both. A null harvest was not printed: the
    crop ends on its simulated maturity or the day before the next sowing. Other top-level
    keys (yield_basis, notes) are read by the bench, not here.
    """
    doc = load_doc(path)
    listed = doc.get("sites") if isinstance(doc, Mapping) else None
    if not isinstance(listed, Mapping):
        raise ValueError(f"{path}: no 'sites' mapping")
    expected = trial_site_seasons(protocol)
    out = {}
    for site in sites:
        if site not in listed:
            raise ValueError(f"{path}: no dates for site {site!r}")
        rows = listed[site]
        labels = [str(r.get("season")) for r in rows]
        if labels != expected[site]:
            raise ValueError(f"{path}: {site} seasons {labels} differ from protocol.trial_site_seasons "
                             f"{expected[site]}")
        seasons = []
        for r in rows:
            pred = r.get("predecessor_sowing") or {}
            harv = r.get("predecessor_harvest") or {}
            unknown = set(harv) - set(pred)
            if unknown:
                raise ValueError(f"{path}: {site} {r['season']} harvest of {sorted(unknown)} without a sowing")
            seasons.append(TrialSeason(
                season=str(r["season"]),
                soybean_sowing=_date(r.get("soybean_sowing"), f"{site} {r['season']} soybean_sowing"),
                predecessor_sowing={crop: _date(d, f"{site} {r['season']} {crop}") for crop, d in pred.items()},
                soybean_harvest=_date(r.get("soybean_harvest"), f"{site} {r['season']} soybean_harvest"),
                predecessor_harvest={crop: _date(d, f"{site} {r['season']} {crop} harvest")
                                     for crop, d in harv.items()},
            ))
        out[site] = seasons
    return out


# P1 shift --------------------------------------------------------------------------

def p1_design(protocol: Mapping[str, Any]) -> tuple[tuple[int, ...], int, int]:
    """Shifts and circular years of protocol.tests.P1. The bench owns the test; this only moves dates."""
    try:
        sec = protocol["tests"]["P1"]
        shifts = tuple(int(k) for k in sec["shifts_back_years"])
        first, last = (int(y) for y in sec["circular_years"])
    except (KeyError, TypeError, ValueError) as e:
        raise ProtocolGap(f"protocol.tests.P1 shifts or circular years missing: {e}") from e
    if not first < last or not shifts:
        raise ProtocolGap("protocol.tests.P1 not understood")
    return shifts, first, last


def p1_offset(start_year: int, k: int, first: int, last: int) -> int:
    """Years added to every date of the chain of the site-season that starts in ``start_year``."""
    if not first <= start_year <= last:
        raise ValueError(f"start year {start_year} outside the circular years {first}-{last}")
    span = last - first + 1
    return first + (start_year - k - first) % span - start_year


def season_year(label: str) -> int:
    return int(str(label).split("/")[0])


def ring_years(regime: Regime, first: int, last: int) -> dict[int, int]:
    """Climate year of each calendar year of a shifted chain before ``first`` (p1_warmup).

    The chain years follow the ``first`` to ``last`` ring year by year: a year before the
    ring takes the climate of the year a whole number of ring spans later. With a 40-year
    ring the leap years agree, so 29 February never needs a rule. A ring that breaks them
    is refused by ``climate_problems``.
    """
    span = last - first + 1
    return {y: first + (y - first) % span for y in range(regime.start.year, first)}


def _leap(y: int) -> bool:
    return y % 4 == 0 and (y % 100 != 0 or y % 400 == 0)


def tested_years(seasons: Sequence[str], offset: int) -> set[int]:
    """Calendar years of the tested site-seasons of a shifted run: the predecessor and the
    soybean sowing in year s, the soybean harvest in s + 1."""
    return {season_year(s) + offset + d for s in seasons for d in (0, 1)}


# Runs ----------------------------------------------------------------------------

@dataclass
class Run:
    key: str                      # unique id, also the artifacts.json and timing.json key
    out: str                      # output file name in the runs folder
    regime: Regime
    columns: tuple[str, ...]
    fixed: dict                   # columns common to every row of this run
    layers_key: tuple             # (kind, id, ksat) of the soil layers
    weather_site: str
    setup: Setup = field(default_factory=Setup)
    problems: list = field(default_factory=list)
    ring: dict = field(default_factory=dict)   # P1: calendar year -> climate year, before the ring
    tested: tuple = ()                          # P1: the site-seasons it tests (at its offset)

    def describe(self) -> str:
        arms = ", ".join(f"{a} {len(s)} seasons" for a, s in self.regime.arms.items())
        state = "; ".join(self.problems) if self.problems else "climate ok"
        ring = ""
        if self.ring:
            ys = sorted(self.ring)
            ring = f"; ring {ys[0]}-{ys[-1]} -> {self.ring[ys[0]]}-{self.ring[ys[-1]]}"
        return f"{self.key} -> {self.out}: start {self.regime.start}, {arms}{ring}; {state}"


def _first_sowing_bounds(regime: Regime) -> tuple[date, date]:
    """Earliest start and latest possible sowing over every arm."""
    latest = regime.start
    for seq in regime.arms.values():
        for s in seq:
            if isinstance(s.sowing, date):
                latest = max(latest, s.sowing)
            elif isinstance(s.sowing, RainSowing):
                latest = max(latest, s.sowing.window()[1])
    return regime.start, latest


def climate_problems(regime: Regime, climate: tuple[date, date], ring: Mapping[int, int] | None = None,
                     tested: set[int] | frozenset = frozenset()) -> list[str]:
    """Why a run cannot have its climate. ``ring`` maps the years before the P1 ring,
    ``tested`` holds the calendar years of the tested seasons, which the seam never reaches."""
    start, latest = _first_sowing_bounds(regime)
    out = []
    first_climate = climate[0]
    if ring:
        first_climate = date(min(ring), 1, 1)   # the ring gives these years their climate
        missing = sorted(c for c in ring.values() if not climate[0].year <= c <= climate[1].year)
        if missing:
            out.append(f"ring years {missing} outside the climate series")
        leap = sorted(y for y, c in ring.items() if _leap(y) != _leap(c))
        if leap:
            out.append(f"ring years {leap} and their climate years differ in leap day: 29 February has no rule")
        seam = sorted(set(ring) & set(tested))
        if seam:
            out.append(f"the ring seam falls in a tested season (years {seam})")
    if start < first_climate:
        out.append(f"starts {start}, before the climate series ({first_climate})")
    if latest > climate[1]:
        out.append(f"a sowing may fall on {latest}, after the climate series ({climate[1]})")
    return out


def trial_runs(protocol: Mapping[str, Any], dates: Mapping[str, list[TrialSeason]],
               rule: RainRule, variants: Sequence[str], shifts: Sequence[int] | None,
               climate: tuple[date, date]) -> list[Run]:
    mode = (protocol.get("chains") or {}).get("trial", {}).get("run")
    if mode not in (CONTINUOUS, PER_SEASON):
        raise ProtocolGap(f"protocol.chains.trial.run {mode!r} not implemented [DECIDE D7]")
    unknown = set(variants) - set(TRIAL_VARIANTS)
    if unknown:
        raise ValueError(f"unknown trial variants {sorted(unknown)}, expected {TRIAL_VARIANTS}")
    initial = Restart.trial_initial_from_protocol(protocol)
    restart = Restart.a1_from_protocol(protocol) if "a1" in variants else None
    p1_shifts, first, last = p1_design(protocol) if "p1" in variants else ((), 0, 0)
    if shifts:
        extra = set(shifts) - set(p1_shifts)
        if extra:
            raise ValueError(f"shifts outside protocol.tests.P1: {sorted(extra)}")
        p1_shifts = tuple(k for k in p1_shifts if k in set(shifts))
    runs: list[Run] = []
    for site, seasons in dates.items():
        def regime_of(sub: Sequence[TrialSeason]) -> Regime:
            warm = trial_warmup_from_protocol(protocol, sub[0].season, rule)
            return trial_regime(site, sub, rule, warmup=warm, initial=initial)

        units = [list(seasons)] if mode == CONTINUOUS else [[s] for s in seasons]

        def add(variant: str, regime: Regime, out: str, k: int | None, offset: int, tag: str,
                tested: Sequence[TrialSeason] = ()) -> None:
            run = Run(key=f"trial_{site}_{variant}{tag}", out=out, regime=regime, columns=TRIAL_COLUMNS,
                      fixed={"site": site, "variant": variant, "shift_k": k, "offset_years": offset},
                      layers_key=("trial", site, "value"), weather_site=site)
            years: set[int] = set()
            run.tested = tuple(s.season for s in tested)
            if variant == "p1":
                run.ring = ring_years(regime, first, last)
                years = tested_years(run.tested, offset)
            run.problems = climate_problems(regime, climate, run.ring, years)
            runs.append(run)

        for variant in variants:
            if variant in ("base", "a1"):
                for sub in units:
                    regime = regime_of(sub)
                    if variant == "a1":
                        regime = with_restart(regime, restart)
                    out = f"trials_{site}.csv" if variant == "base" else f"trials_{site}_a1.csv"
                    tag = "" if mode == CONTINUOUS else f"_{sub[0].season.replace('/', '-')}"
                    add(variant, regime, out, None, 0, tag, sub)
                continue
            for k in p1_shifts:
                offsets = [p1_offset(season_year(s.season), k, first, last) for s in seasons]
                if mode == CONTINUOUS:
                    groups = []
                    for o in dict.fromkeys(offsets):
                        j = max(i for i, x in enumerate(offsets) if x == o)
                        groups.append((o, seasons[: j + 1], [s for s, x in zip(seasons, offsets) if x == o]))
                else:
                    groups = [(o, [s], [s]) for o, s in zip(offsets, seasons)]
                for o, sub, tested in groups:
                    tag = f"_k{k}_o{o:+d}"
                    if mode == PER_SEASON:
                        tag += f"_{sub[0].season.replace('/', '-')}"
                    add("p1", shifted(regime_of(sub), o), f"trials_{site}_p1.csv", k, o, tag, tested)
    return runs


def field_runs(protocol: Mapping[str, Any], field_ids: Sequence[str], years: Sequence[int], rule: RainRule, *,
               prefix_years: int, corn_sowing: str, wheat_sowing: str, initial: Restart, weather_site: str,
               setups: Sequence[str], ksat: str, repeat: bool, climate: tuple[date, date]) -> list[Run]:
    known = sensitivity_setups(protocol)
    unknown = set(setups) - set(known)
    if unknown:
        raise ValueError(f"unknown setups {sorted(unknown)}, the protocol gives {sorted(known)}")
    if ksat not in ksat_choices(protocol):
        raise ValueError(f"unknown Ksat choice {ksat!r}, the protocol gives {sorted(ksat_choices(protocol))}")
    gap = s3_gap_days(protocol)
    runs = []
    for fid in field_ids:
        if not _SAFE_ID.match(fid):
            raise ValueError(f"unsafe field id {fid!r}")
        for setup in setups:
            variant = setup
            if ksat != "value":
                variant = f"ksat_{ksat}" if variant == "base" else f"{variant}_ksat_{ksat}"
            passes = [(1, variant)] + ([(2, "repeat")] if repeat and variant == "base" else [])
            for n, name in passes:
                out = f"chain_{fid}.csv" if name == "base" else f"chain_{fid}_{name}.csv"
                for y in years:
                    regime = field_regime(y, rule, prefix_years=prefix_years, corn_sowing=corn_sowing,
                                          wheat_sowing=wheat_sowing, s3_gap_days=gap, initial=initial)
                    run = Run(key=f"field_{fid}_{name}_{y}", out=out, regime=regime, columns=CHAIN_COLUMNS,
                              fixed={"field_id": fid, "year": y, "variant": name, "setup": setup,
                                     "ksat": ksat, "run": n},
                              layers_key=("field", fid, ksat), weather_site=weather_site,
                              setup=known[setup])
                    run.problems = climate_problems(regime, climate)
                    runs.append(run)
    return runs


# Inputs for a real run ---------------------------------------------------------------

def power_path(config: Mapping[str, Any], raw_dir: Path, site: str, call_id: str = "met") -> Path:
    """Cache name of ``pipeline.adapters.power`` for one site and call."""
    cfg = config["sources"]["power"]
    call = next((c for c in cfg["calls"] if c["id"] == call_id), None)
    if call is None:
        raise ProtocolGap(f"sources.power.calls has no {call_id!r} call")
    start, end = str(call["start"]).replace("-", ""), str(call["end"]).replace("-", "")
    return raw_dir / cfg["source_id"] / f"{site}_{call_id}_{start}_{end}_{call['time_standard']}.json"


def read_power(path: Path):
    """POWER daily table as ``pipeline.adapters.power.read`` gives it (fill value missing, never
    zero), with the units of the response and the download time of its .meta.json."""
    import numpy as np
    import pandas as pd

    doc = json.loads(path.read_text(encoding="utf-8"))
    fill = float(doc.get("header", {}).get("fill_value", -999.0))
    cols = {}
    for name, series in doc["properties"]["parameter"].items():
        s = pd.Series(series, dtype="float64")
        s.index = pd.to_datetime(s.index, format="%Y%m%d")
        cols[name] = s.where(~np.isclose(s, fill), np.nan)
    df = pd.DataFrame(cols).sort_index()
    units = {p: v.get("units") for p, v in (doc.get("parameters") or {}).items()}
    meta = path.with_name(path.name + ".meta.json")
    retrieved = json.loads(meta.read_text(encoding="utf-8")).get("retrieved_at") if meta.exists() else None
    return df, units, retrieved


def load_weather(config: Mapping[str, Any], protocol: Mapping[str, Any], data: Path, site: str):
    """Engine weather for one site: POWER met call, FAO-56 ET0, sources by stretch (M1.3, M4.3).

    Elevation for ET0 is the site altitude of ``trial_sites`` [DECIDE: or the POWER cell
    elevation the response gives]. No gap is filled.
    """
    from .weather import climate_window, power_to_ospy

    df, units, retrieved = read_power(power_path(config, data / "raw", site))
    summary = json.loads((data / "interim" / "power_summary.json").read_text(encoding="utf-8"))
    segments = summary["source_segments"]["met"]
    s = site_config(config, site)
    return power_to_ospy(df, latitude_deg=float(s["lat"]), elevation_m=float(s["altitude_m"]),
                         window=climate_window(protocol), segments=segments, units=units,
                         downloaded_at=retrieved)


def soil_path(data: Path, kind: str, ident: str, trial_soil_suffix: str = "") -> Path:
    """Soil record of a run. ``trial_soil_suffix`` (e.g. "_soilgrids") reads the trial side file
    ``trial_<site>_soil<suffix>.json`` instead: the one soil sensitivity run of SA-D77. Field soils
    never take it."""
    if kind == "trial":
        return data / "interim" / f"trial_{ident}_soil{trial_soil_suffix}.json"
    return data / "interim" / f"field_{ident}_soil.json"


def load_layers(path: Path, protocol: Mapping[str, Any], ksat: str) -> tuple:
    """Engine layers from a soil record (``layers`` with top_m, bottom_m, sand_pct, clay_pct,
    om_pct) and the Ksat choice of protocol.engine.ksat."""
    from .soilmodel import texture_layers

    doc = json.loads(path.read_text(encoding="utf-8"))
    horizons = []
    for lay in doc.get("layers") or ():
        values = [lay.get(k) for k in ("top_m", "bottom_m", "sand_pct", "clay_pct", "om_pct")]
        if any(v is None for v in values):
            raise ValueError(f"{path}: layer {lay.get('depth', '?')} lacks texture or organic matter")
        horizons.append(tuple(float(v) for v in values))
    if not horizons:
        raise ValueError(f"{path}: no soil layers")
    choice = ksat_choices(protocol)[ksat]
    return texture_layers(horizons, ksat_mm_h=choice.mm_h, ksat_depth_m=choice.depth_m)


# Rows and records ---------------------------------------------------------------------

def _num(x: Any) -> Any:
    if isinstance(x, float) and not math.isfinite(x):
        return None
    return x


def _season_of(label: str) -> str:
    return label.rsplit(":", 1)[0] if ":" in label else label


def season_rows(run: Run, arms: Mapping[str, Any]) -> list[dict]:
    rows = []
    for arm, seq in run.regime.arms.items():
        res = arms[arm]
        gaps = res.handover_gap_mm
        for i, spec in enumerate(seq):
            row = dict(run.fixed)
            row.update(run_key=run.key, arm=arm, index=i, label=spec.label, role=spec.role, crop=spec.crop,
                       season=_season_of(spec.label), shared_prefix=i < res.shared_prefix)
            if i < len(res.seasons):
                r = res.seasons[i]
                row.update(
                    outcome=SIMULATED, stop_reason="", start=r.start.isoformat(), sowing=r.sowing.isoformat(),
                    harvest=r.harvest.isoformat(), end_rule=r.end_rule, sowing_source=r.sowing_source,
                    dry_yield_t_ha=_num(r.dry_yield_t_ha), yield_potential_t_ha=_num(r.yield_potential_t_ha),
                    matured=r.matured, crop_dead=r.crop_dead, germinated=r.germinated,
                    days_to_emergence=r.days_to_emergence, canopy_max=_num(r.canopy_max),
                    storage_start_mm=_num(r.storage_start_mm), storage_end_mm=_num(r.storage_end_mm),
                    handover_gap_mm=_num(gaps[i - 1]) if i >= 1 else None, restarted=r.restarted,
                    runoff_mm=_num(r.runoff_mm), deep_perc_mm=_num(r.deep_perc_mm),
                    transpiration_mm=_num(r.transpiration_mm), rain_mm=_num(r.rain_mm),
                    balance_residual_mm=_num(r.balance_residual_mm), sim_days=r.sim_days,
                    init_seconds=r.init_seconds, seconds=r.seconds)
            else:
                reason = (res.stopped or {}).get("reason", "unknown")
                at = (res.stopped or {}).get("index")
                row.update(outcome=reason if i == at else AFTER_STOP, stop_reason=reason)
            rows.append(row)
    return rows


def _cell(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "true" if v else "false"
    return str(v)


def write_csv(path: Path, columns: Sequence[str], rows: Sequence[Mapping[str, Any]],
              replaces: Sequence[str] = ()) -> None:
    """Write ``rows``, keeping the rows of an existing file whose ``run_key`` is not in
    ``replaces``. Every run of this invocation is in ``replaces``, the failed ones too, so a
    failed run never leaves an older result behind, and runs made apart (P1 shifts) add up."""
    kept: list[list[str]] = []
    if path.exists():
        with open(path, newline="", encoding="utf-8") as fh:
            reader = csv.reader(fh)
            header = next(reader, None)
            if header is not None and tuple(header) != tuple(columns):
                raise ValueError(f"{path}: columns differ from this version, move the file away")
            drop, at = set(replaces), list(columns).index("run_key")
            kept = [r for r in reader if r and r[at] not in drop]
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(columns)
        w.writerows(kept)
        for row in rows:
            w.writerow([_cell(row.get(c)) for c in columns])
    tmp.replace(path)


def _clean(obj: Any) -> Any:
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, Mapping):
        return {str(k): _clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_clean(v) for v in obj]
    if isinstance(obj, date):
        return obj.isoformat()
    return obj


def merge_json(path: Path, section: str, entries: Mapping[str, Any], extra: Mapping[str, Any] | None = None,
               replaces: Sequence[str] = ()) -> None:
    """Update ``section`` of a JSON file key by key, so trials and field runs share one file.
    Keys in ``replaces`` (every run of this invocation, failed ones too) are dropped first."""
    doc = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    doc["schema_version"] = SCHEMA_VERSION
    part = {k: v for k, v in doc.get(section, {}).items() if k not in set(replaces)}
    part.update(_clean(dict(entries)))
    doc[section] = part
    for key, value in _clean(dict(extra or {})).items():
        if isinstance(value, dict) and isinstance(doc.get(key), dict):
            doc[key].update(value)          # weather of other sites stays
        else:
            doc[key] = value
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(doc, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
    tmp.replace(path)


def machine() -> dict:
    try:
        from importlib.metadata import version
        aquacrop = version("aquacrop")
    except Exception:  # metadata missing: report it, never fail the run
        aquacrop = None
    return {"platform": platform.platform(), "machine": platform.machine(), "processor": platform.processor(),
            "cpu_count": os.cpu_count(), "python": platform.python_version(), "aquacrop": aquacrop}


def timing_record(arms: Mapping[str, Any], wall_seconds: float) -> dict:
    unique = {id(s): s for chain in arms.values() for s in chain.seasons}.values()
    n = len(unique)
    total = sum(s.seconds for s in unique)
    return {"instances": n, "seconds": total, "wall_seconds": wall_seconds,
            "seconds_per_instance": total / n if n else None,
            "init_seconds_per_instance": sum(s.init_seconds for s in unique) / n if n else None,
            "max_seconds": max((s.seconds for s in unique), default=None),
            "measured_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}


# Execute -----------------------------------------------------------------------------

def execute(runs: Sequence[Run], *, config: Mapping[str, Any], protocol: Mapping[str, Any], data: Path,
            runs_dir: Path, trial_soil_suffix: str = "") -> int:
    """Run every chain, then write the CSV files, artifacts.json and timing.json. A run that fails
    is reported and left out; the others are written. Returns the number of failed runs."""
    from . import artifacts as A
    from .chain import run_regime
    from .weather import ring_weather

    thr = A.Thresholds.from_protocol(protocol)
    checks = A.checks_from_protocol(protocol)
    weather_cache: dict[str, Any] = {}
    layers_cache: dict[tuple, Any] = {}
    rows_by_file: dict[str, list] = {}
    columns_by_file: dict[str, tuple] = {}
    reports, timings, failed = {}, {}, []
    for run in runs:
        columns_by_file.setdefault(run.out, run.columns)
        rows_by_file.setdefault(run.out, [])
        try:
            if run.problems:
                raise ValueError("; ".join(run.problems))
            if run.weather_site not in weather_cache:
                weather_cache[run.weather_site] = load_weather(config, protocol, data, run.weather_site)
            if run.layers_key not in layers_cache:
                kind, ident, ksat = run.layers_key
                layers_cache[run.layers_key] = load_layers(soil_path(data, kind, ident, trial_soil_suffix),
                                                           protocol, ksat)
            weather, _ = weather_cache[run.weather_site]
            if run.ring:
                weather = ring_weather(weather, run.ring)
            t0 = time.perf_counter()
            arms = run_regime(weather, layers_cache[run.layers_key], run.regime, setup=run.setup)
            wall = time.perf_counter() - t0
        except Exception as exc:  # one failed run never stops the others (make -k, plan P0.3)
            failed.append(run.key)
            print(f"error: {run.key}: {type(exc).__name__}: {exc}", file=sys.stderr)
            continue
        rows_by_file[run.out].extend(season_rows(run, arms))
        reports[run.key] = A.report(arms, thr, checks)
        timings[run.key] = timing_record(arms, wall)
        if run.ring:
            timings[run.key]["ring_years"] = {str(y): c for y, c in sorted(run.ring.items())}
        print(f"ran {run.key}: {timings[run.key]['instances']} instances, "
              f"{timings[run.key]['seconds']:.1f} s", file=sys.stderr)
    runs_dir.mkdir(parents=True, exist_ok=True)
    keys_by_file: dict[str, list[str]] = {}
    for run in runs:
        keys_by_file.setdefault(run.out, []).append(run.key)
    for name, rows in rows_by_file.items():
        write_csv(runs_dir / name, columns_by_file[name], rows, replaces=keys_by_file[name])
    every = [run.key for run in runs]
    merge_json(runs_dir / "artifacts.json", "regimes", reports, replaces=every)
    weather = {site: {k: rep[k] for k in ("window", "rain", "et0", "latitude_deg", "elevation_m")}
               for site, (_, rep) in weather_cache.items()}
    extra = {"machine": machine(), "weather": weather}
    if trial_soil_suffix:   # the sensitivity run says which soil files it read
        extra["trial_soil_files"] = {ident: str(soil_path(data, kind, ident, trial_soil_suffix))
                                     for kind, ident, _ in sorted(layers_cache) if kind == "trial"}
    merge_json(runs_dir / "timing.json", "runs", timings, extra, replaces=every)
    return len(failed)


# Commands ------------------------------------------------------------------------------

def _common(ap: argparse.ArgumentParser) -> None:
    ap.add_argument("--config", type=Path, default=DEFAULT_CONFIG,
                    help="config YAML (.venv) or its JSON from config-json (.venv-engine)")
    ap.add_argument("--data", type=Path, default=DEFAULT_DATA, help="data root: raw/, interim/, runs/")
    ap.add_argument("--runs", type=Path, default=None, help="output folder, default <data>/runs")
    ap.add_argument("--rain-after-not-before", type=_bool_arg, default=None,
                    help="must agree with protocol.sowing.soybean_field.rain_after_not_before (D6, closed); "
                         "a different value is refused")
    ap.add_argument("--dry-run", action="store_true", help="print the runs, simulate nothing")


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="python -m pipeline.engine.cli", description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="command", required=True)

    cj = sub.add_parser("config-json", help="convert the YAML config to JSON for .venv-engine")
    cj.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    cj.add_argument("--out", type=Path, required=True)

    tr = sub.add_parser("trials", help="trial chains per site and arm (refused before the tag)")
    _common(tr)
    tr.add_argument("--trial-dates", type=Path, default=DEFAULT_TRIAL_DATES)
    tr.add_argument("--sites", default="", help="comma-separated site ids, default every protocol site")
    tr.add_argument("--variants", default="base,a1,p1", help="comma-separated: base, a1, p1")
    tr.add_argument("--shifts", default="", help="comma-separated P1 shifts k, default all of the protocol")
    tr.add_argument("--trial-soil-suffix", default="",
                    help="read data/interim/trial_<site>_soil<SUFFIX>.json (SA-D77 sensitivity: _soilgrids); "
                         "use with --runs, never into data/runs/")

    fd = sub.add_parser("field", help="field chains S1 and S3")
    _common(fd)
    fd.add_argument("--field", action="append", dest="fields", default=[], help="field id, repeatable")
    fd.add_argument("--years", required=True, help="comma-separated branch years y (target soybean y/y+1)")
    fd.add_argument("--prefix-years", type=int, required=True, help="[DECIDE] soybean and corn years before y")
    fd.add_argument("--corn-sowing", required=True, help=f"[DECIDE] {AFTER_HARVEST} or MM-DD")
    fd.add_argument("--wheat-sowing", required=True, help="[DECIDE] MM-DD of the S3 wheat")
    fd.add_argument("--initial", required=True, help="[DECIDE] initial state, e.g. field_capacity")
    fd.add_argument("--weather-site", required=True, help="[DECIDE] POWER site whose cell serves the field")
    fd.add_argument("--setups", default="base", help="comma-separated: base, cn_rew, library_zmax")
    fd.add_argument("--ksat", default="value", help="Ksat choice of protocol.engine.ksat: value, alternative, texture")
    fd.add_argument("--repeat", action="store_true", help="run the base setup twice (L0 repeat)")
    return ap


def cmd_config_json(args) -> int:
    doc = load_doc(args.config)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(_clean(doc), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(args.out)
    return 0


def _rule(protocol: Mapping[str, Any], args) -> RainRule:
    return RainRule.from_protocol(protocol, rain_after_not_before=args.rain_after_not_before)


def chain_count(runs: Sequence[Run]) -> dict[str, int]:
    """Chains per variant: the site-seasons each run tests, times its arms (protocol.chains.count)."""
    out: dict[str, int] = {}
    for run in runs:
        if run.tested:
            v = run.fixed.get("variant", "")
            out[v] = out.get(v, 0) + len(run.tested) * len(run.regime.arms)
    return out


def _print_plan(runs: Sequence[Run]) -> None:
    for run in runs:
        print(run.describe())
    print(f"{len(runs)} runs", f"({sum(1 for r in runs if r.problems)} refused by the climate check)")
    chains = chain_count(runs)
    if chains:
        parts = ", ".join(f"{v} {n}" for v, n in chains.items())
        print(f"{sum(chains.values())} chains (site-season arms): {parts}")


def cmd_trials(args) -> int:
    config = load_doc(args.config)
    protocol = protocol_of(config)
    guard = None
    try:
        require_tagged(protocol)
    except Refused as exc:
        if not args.dry_run:
            raise
        guard = str(exc)
    from .weather import climate_window

    expected = trial_site_seasons(protocol)
    sites = _list_arg(args.sites) or list(expected)
    unknown = set(sites) - set(expected)
    if unknown:
        raise ValueError(f"sites outside protocol.trial_site_seasons: {sorted(unknown)}")
    dates = load_trial_dates(args.trial_dates, protocol, sites)
    runs = trial_runs(protocol, dates, _rule(protocol, args), _list_arg(args.variants),
                      [int(k) for k in _list_arg(args.shifts)], climate_window(protocol))
    if args.dry_run:
        print(f"protocol {protocol.get('id')} status {protocol.get('status')!r}: "
              + (f"a real run would be refused. {guard}" if guard else "tagged, trial runs allowed"))
        _print_plan(runs)
        return 0
    runs_dir = args.runs or args.data / "runs"
    if args.trial_soil_suffix and runs_dir.resolve() == (args.data / "runs").resolve():
        raise ValueError("--trial-soil-suffix writes a sensitivity run: give --runs a folder other than <data>/runs")
    return 1 if execute(runs, config=config, protocol=protocol, data=args.data, runs_dir=runs_dir,
                        trial_soil_suffix=args.trial_soil_suffix) else 0


def cmd_field(args) -> int:
    config = load_doc(args.config)
    protocol = protocol_of(config)
    from .weather import climate_window

    if not args.fields:
        raise ValueError("give at least one --field")
    years = [int(y) for y in _list_arg(args.years)]
    if not years:
        raise ValueError("give at least one year in --years")
    runs = field_runs(protocol, args.fields, years, _rule(protocol, args), prefix_years=args.prefix_years,
                      corn_sowing=args.corn_sowing, wheat_sowing=args.wheat_sowing,
                      initial=Restart.named(args.initial), weather_site=args.weather_site,
                      setups=_list_arg(args.setups), ksat=args.ksat, repeat=args.repeat,
                      climate=climate_window(protocol))
    if args.dry_run:
        _print_plan(runs)
        return 0
    return 1 if execute(runs, config=config, protocol=protocol, data=args.data,
                        runs_dir=args.runs or args.data / "runs") else 0


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    commands = {"config-json": cmd_config_json, "trials": cmd_trials, "field": cmd_field}
    try:
        return commands[args.command](args)
    except Refused as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    except (ProtocolGap, ValueError, KeyError, FileNotFoundError, RuntimeError) as exc:
        print(f"error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

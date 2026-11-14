# Written with an SI tool (Claude, Anthropic) for NASA Space Apps 2026. See AI_USE.md.
"""Spike (Appendix B item 5): OSPy chain handover, cost, Ksat.

Run from aftercrop/:  .venv-engine/bin/python docs/spikes/ospy_chain_spike.py
Writes docs/spikes/ospy-chain-results.json. Test climate only (package sample).
"""
from __future__ import annotations

import json
import os
import platform
import statistics as st
import subprocess
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from datetime import date, timedelta
from importlib.metadata import version
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402

from pipeline.engine.chain import Season, run_chain, run_season  # noqa: E402
from pipeline.engine.soilmodel import CLAY_TEST_LAYERS, DZ, water_limits  # noqa: E402
from pipeline.engine.weather import sample_weather  # noqa: E402

OUT = Path(__file__).with_name("ospy-chain-results.json")
START = date(2001, 8, 1)
SOY1, SOY2 = date(2001, 10, 1), date(2002, 11, 30)
ARMS = {
    "soy_maize_soy": [Season("soybean", SOY1), Season("maize"), Season("soybean", SOY2)],
    "soy_wheat_soy": [Season("soybean", SOY1), Season("wheat", date(2002, 5, 1)), Season("soybean", SOY2)],
}
KSAT_MM_H = {"texture": None, "measured_60": 60.0, "measured_78": 78.0}
REPS = 7
BENCH_CHAINS = 220
INSTANCES_PER_CHAIN = (3, 5, 9, 17)  # 0, 1, 3, 7 spin-up years at 2 crops/year


def sh(cmd: str) -> str:
    return subprocess.run(cmd, shell=True, capture_output=True, text=True).stdout.strip()


def machine() -> dict:
    return {
        "cpu": sh("sysctl -n machdep.cpu.brand_string"),
        "ncpu": int(sh("sysctl -n hw.ncpu")),
        "perf_cores": int(sh("sysctl -n hw.perflevel0.physicalcpu") or 0),
        "eff_cores": int(sh("sysctl -n hw.perflevel1.physicalcpu") or 0),
        "mem_gb": int(sh("sysctl -n hw.memsize")) / 2**30,
        "os": platform.platform(),
        "python": sys.version.split()[0],
        "packages": {p: version(p) for p in ("aquacrop", "numpy", "pandas")},
    }


def season_row(s) -> dict:
    return {
        "crop": s.crop, "start": str(s.start), "sowing": str(s.sowing), "harvest": str(s.harvest),
        "storage_start_mm": round(s.storage_start_mm, 2), "storage_end_mm": round(s.storage_end_mm, 2),
        "dry_yield_t_ha": round(s.dry_yield_t_ha, 3), "matured": s.matured, "crop_dead": s.crop_dead,
        "germinated": s.germinated, "days_to_emergence": s.days_to_emergence,
        "canopy_max": round(s.canopy_max, 3), "cn": s.curve_number,
        "runoff_mm": round(s.runoff_mm, 1), "deep_perc_mm": round(s.deep_perc_mm, 1),
        "transpiration_mm": round(s.transpiration_mm, 1), "sim_days": s.sim_days,
        "seconds": round(s.seconds, 4),
    }


def layers_with_ksat(mm_h: float | None):
    top, rest = CLAY_TEST_LAYERS[0], CLAY_TEST_LAYERS[1:]
    return ((*top, None if mm_h is None else mm_h * 24.0), *rest)


def handover(w) -> dict:
    wp, fc = water_limits(CLAY_TEST_LAYERS)
    starts = {"field_capacity": None, "dry_25pct_taw": wp + 0.25 * (fc - wp)}
    out = {}
    for arm, seq in ARMS.items():
        for label, th0 in starts.items():
            r = run_chain(w, CLAY_TEST_LAYERS, seq, START, th0)
            exact = [float(np.max(np.abs(b.th_start - a.th_end)))
                     for a, b in zip(r.seasons, r.seasons[1:])]
            out[f"{arm}/{label}"] = {
                "seasons": [season_row(s) for s in r.seasons],
                "handover_gap_mm": r.handover_gap_mm,
                "handover_max_abs_th_diff": exact,
            }
    return out


def timing(w) -> dict:
    for seq in ARMS.values():  # warm-up: imports, pandas caches
        run_chain(w, CLAY_TEST_LAYERS, seq, START)
    per_crop: dict[str, list] = {}
    chains: dict[str, list] = {a: [] for a in ARMS}
    init: list[float] = []
    days: dict[str, int] = {}
    for _ in range(REPS):
        for arm, seq in ARMS.items():
            t = time.perf_counter()
            r = run_chain(w, CLAY_TEST_LAYERS, seq, START)
            chains[arm].append(time.perf_counter() - t)
            days[arm] = sum(s.sim_days for s in r.seasons)
            for i, s in enumerate(r.seasons):
                per_crop.setdefault(f"{arm}[{i}]{s.crop}", []).append((s.seconds, s.sim_days))
                init.append(s.init_seconds)
    inst = [x for v in per_crop.values() for x, _ in v]
    return {
        "reps": REPS,
        "season_s": {k: {"mean": st.mean(x for x, _ in v), "sd": st.stdev(x for x, _ in v),
                         "sim_days": v[0][1]} for k, v in per_crop.items()},
        "chain_s": {k: {"mean": st.mean(v), "sd": st.stdev(v), "sim_days": days[k]}
                    for k, v in chains.items()},
        "instance_mean_s": st.mean(inst),
        "init_mean_s": st.mean(init),
        "ms_per_sim_day": 1000 * sum(inst) / sum(d for v in per_crop.values() for _, d in v),
    }


def compartments(w) -> dict:
    grids = {
        "14_comp_2.5m (DZ)": DZ,
        "12_comp_2.5m": (0.1,) * 4 + (0.2,) * 3 + (0.3,) * 5,
        "25_comp_2.5m": (0.1,) * 25,
    }
    out = {}
    seq = ARMS["soy_maize_soy"]
    for name, dz in grids.items():
        run_chain(w, CLAY_TEST_LAYERS, seq, START, dz=dz)
        ts = []
        for _ in range(5):
            t = time.perf_counter()
            run_chain(w, CLAY_TEST_LAYERS, seq, START, dz=dz)
            ts.append(time.perf_counter() - t)
        out[name] = {"chain_mean_s": st.mean(ts), "n_comp": len(dz)}
    return out


_W = None


def _init_worker():
    global _W
    _W = sample_weather()


def _job(i: int) -> float:
    seq = list(ARMS.values())[i % 2]
    t = time.perf_counter()
    run_chain(_W, CLAY_TEST_LAYERS, seq, START)
    return time.perf_counter() - t


def parallel(n_chains: int = 48) -> dict:
    out = {}
    for k in (1, 2, 4, 6, 8):
        with ProcessPoolExecutor(max_workers=k, initializer=_init_worker) as ex:
            list(ex.map(_job, range(2 * k)))  # warm every worker
            t = time.perf_counter()
            per = list(ex.map(_job, range(n_chains)))
            wall = time.perf_counter() - t
        out[str(k)] = {"wall_s": wall, "chains": n_chains, "chains_per_s": n_chains / wall,
                       "mean_chain_s_in_worker": st.mean(per)}
    base = out["1"]["chains_per_s"]
    for v in out.values():
        v["speedup"] = v["chains_per_s"] / base
    return out


def rain_date(r3, d: date) -> date:
    s = r3.loc[str(d):]
    return s[s >= 20].index[0].date()


def scan(w, layers, wheat: bool = True) -> list[dict]:
    """Every test-climate year: shared soy1, maize and wheat arms, shared soy2 date."""
    r3 = w.set_index("Date")["Precipitation"].rolling(3).sum()
    rows = []
    for y0 in range(1991, 2020):
        head = run_season(w, layers, Season("soybean", rain_date(r3, date(y0, 10, 1))), date(y0, 8, 1), None)
        st0 = head.harvest + timedelta(days=1)
        mid = {"maize": run_season(w, layers, Season("maize"), st0, head.th_end)}
        if wheat:
            mid["wheat"] = run_season(w, layers, Season("wheat", max(st0, date(y0 + 1, 5, 1))), st0, head.th_end)
        s2 = rain_date(r3, max(m.harvest for m in mid.values()) + timedelta(days=1))
        row = {"year": y0, "soy1": season_row(head)}
        for k, m in mid.items():
            row[k] = season_row(m)
            row[f"soy2_after_{k}"] = season_row(
                run_season(w, layers, Season("soybean", s2), m.harvest + timedelta(days=1), m.th_end))
        rows.append(row)
    return rows


def failed(s: dict) -> bool:
    return s["crop_dead"] or not s["matured"] or s["dry_yield_t_ha"] <= 0


def artifact_rates(rows) -> dict:
    keys = [k for k in rows[0] if k != "year"]
    return {k: {"n": len(rows), "failed": sum(failed(r[k]) for r in rows),
                "dead": sum(r[k]["crop_dead"] for r in rows),
                "no_canopy": sum(r[k]["days_to_emergence"] is None for r in rows),
                "mean_yield": st.mean(r[k]["dry_yield_t_ha"] for r in rows)} for k in keys}


def ksat(w) -> dict:
    out = {}
    for name, mm_h in KSAT_MM_H.items():
        layers = layers_with_ksat(mm_h)
        r = run_chain(w, layers, ARMS["soy_maize_soy"], START)
        rows = scan(w, layers, wheat=False)
        summ = {}
        for k in ("soy1", "maize", "soy2_after_maize"):
            summ[k] = {
                "mean_storage_end_mm": st.mean(x[k]["storage_end_mm"] for x in rows),
                "mean_yield_t_ha": st.mean(x[k]["dry_yield_t_ha"] for x in rows),
                "mean_runoff_mm": st.mean(x[k]["runoff_mm"] for x in rows),
                "mean_deep_perc_mm": st.mean(x[k]["deep_perc_mm"] for x in rows),
                "mean_transpiration_mm": st.mean(x[k]["transpiration_mm"] for x in rows),
                "failed": sum(failed(x[k]) for x in rows),
            }
        out[name] = {
            "top_ksat_mm_day": None if mm_h is None else mm_h * 24.0,
            "test_chain": [season_row(s) for s in r.seasons],
            "scan_1991_2019": summ,
            "scan_rows": rows,
        }
    return out


def ksat_paths(w) -> dict:
    """Separate the Ksat effect: CN forced (calc_cn=0) against infiltration cap and tau."""
    import pipeline.engine.chain as chain_mod
    from pipeline.engine.soilmodel import make_soil
    measured = layers_with_ksat(60.0)
    cases = {"texture_cn_calc": (CLAY_TEST_LAYERS, None), "texture_cn_46": (CLAY_TEST_LAYERS, 46.0),
             "measured60_cn_77": (measured, 77.0), "measured60_cn_calc": (measured, None)}
    out, original = {}, chain_mod.make_soil
    try:
        for name, (layers, cn) in cases.items():
            def mk(_layers, dz, layers=layers, cn=cn):
                soil = make_soil(layers, dz)
                if cn is not None:
                    soil.calc_cn, soil.cn = 0, cn
                return soil
            chain_mod.make_soil = mk
            rows = scan(w, CLAY_TEST_LAYERS, wheat=False)
            out[name] = {k: {"mean_yield_t_ha": st.mean(x[k]["dry_yield_t_ha"] for x in rows),
                             "mean_runoff_mm": st.mean(x[k]["runoff_mm"] for x in rows),
                             "mean_storage_end_mm": st.mean(x[k]["storage_end_mm"] for x in rows),
                             "cn": rows[0][k]["cn"]}
                         for k in ("soy1", "maize", "soy2_after_maize")}
    finally:
        chain_mod.make_soil = original
    return out


def main() -> None:
    w = sample_weather()
    res = {"machine": machine(), "weather": "aquacrop sample cordoba_climate.txt (1991-2021)",
           "soil_layers": CLAY_TEST_LAYERS, "dz": DZ}
    t = time.perf_counter()
    res["handover"] = handover(w)
    res["timing"] = timing(w)
    res["compartments"] = compartments(w)
    res["parallel"] = parallel()
    rows = scan(w, CLAY_TEST_LAYERS)
    res["scan_artifacts"] = artifact_rates(rows)
    res["ksat"] = ksat(w)
    res["ksat_paths"] = ksat_paths(w)
    tm, par = res["timing"], res["parallel"]
    best = max(par.values(), key=lambda v: v["chains_per_s"])
    res["projection_220"] = {
        str(n): {"sequential_min": BENCH_CHAINS * n * tm["instance_mean_s"] / 60,
                 "best_parallel_min": BENCH_CHAINS * n * tm["instance_mean_s"] / best["speedup"] / 60}
        for n in INSTANCES_PER_CHAIN}
    res["best_parallel"] = best
    res["spike_wall_s"] = time.perf_counter() - t
    OUT.write_text(json.dumps(res, indent=1, default=str))
    print(json.dumps({k: res[k] for k in ("timing", "compartments", "projection_220")}, indent=1, default=str))
    print("parallel", {k: round(v["speedup"], 2) for k, v in par.items()})


if __name__ == "__main__":
    os.chdir(ROOT)
    main()

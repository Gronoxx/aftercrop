# Written with an SI tool (Claude, Anthropic) for NASA Space Apps 2026. See AI_USE.md.
"""Tabua de Risco do Zarc (MAPA open data): fetch, read, decode, check.

Plan, sheet M1 and criterion M1.6. Each row carries 36 dekads, dec1..dec36.
Decoding found: dec1 is 1-10 January (civil dekads of the
calendar year, ``pipeline.common.dekad``). Evidence: soybean rows in the three
municipalities are non-zero exactly in dec25..dec36, 1 Sep to 31 Dec, the legal
window of Adapar Regiao 2. Values are read as risk classes 20, 30 or 40 (percent),
with 0 for "not indicated". The data dictionary does not say this. To confirm
against one portaria.

If the checks fail, ``zarc_status`` is "plan_b": portarias and published dates.

Run from the repository root:  python -m pipeline.adapters.zarc
"""

from __future__ import annotations

import csv
import sys
from datetime import date
from pathlib import Path

import pandas as pd

from pipeline.adapters import base
from pipeline.common import dekad

DEC_COLS = [f"dec{i}" for i in range(1, dekad.N_DEKADS + 1)]
ID_COLS = [
    "Nome_cultura", "SafraIni", "SafraFin", "Cod_Cultura", "Cod_Ciclo", "Cod_Solo",
    "geocodigo", "UF", "municipio", "Cod_Clima", "Cod_Outros_Manejos",
    "Produtividade", "Cod_NM", "Portaria",
]
RISK_VALUES = {0, 20, 30, 40}

# Code tables from the data dictionary (dicionario-de-dados-tabua-de-risco-2026.pdf).
CICLO = {13: "Perene", 19: "Semiperene", 20: "Grupo I", 21: "Grupo II", 22: "Grupo III",
         24: "Grupo IV", 25: "Grupo V", 26: "Grupo VI"}
SOLO = {1: "Arenoso", 2: "Textura Media", 3: "Argiloso", 11: "AD1", 12: "AD2", 13: "AD3",
        14: "AD4", 15: "AD5", 16: "AD6"}
MANEJO = {1: "Sequeiro", 2: "Irrigado", 3: "Irrigado com controle de geada"}
NM = {1: "NM1", 2: "NM2", 3: "NM3", 4: "NM4"}

OUT_ROWS = "zarc_norte_pr.csv"
OUT_STATUS = "zarc_status.json"


# Contract ------------------------------------------------------------------

def specs(config: dict | None = None) -> list[dict]:
    cfg = (config or base.load_config())["sources"]["zarc"]
    common = {"source_id": cfg["source_id"], "license": cfg["license"]}
    out = [{**common, "kind": "dictionary", **cfg["dictionary"]}]
    for t in cfg["tables"]:
        out.append({
            **common, "kind": "table", "url": t["url"], "filename": t["filename"],
            "safra": t["safra"], "role": t["role"], "crops": list(t["crops"]),
            "uf": cfg["uf"], "municipalities": [int(m) for m in cfg["municipalities"]],
        })
    return out


def _path(spec: dict) -> Path:
    return base.raw_path(spec["source_id"], spec["url"], None, spec["filename"])


def fetch(spec: dict) -> Path:
    return base.cached_get(spec["source_id"], spec["url"], filename=spec["filename"], timeout=900)


def read(path: Path, uf: str | None = None, municipalities=None, crops=None) -> pd.DataFrame:
    """Rows of one table, filtered while streaming. No side effects.

    dec1..dec36 come back as nullable integers, so a blank cell stays missing
    and is caught by ``check()``.
    """
    geos = {str(int(m)) for m in municipalities} if municipalities else None
    crops = set(crops) if crops else None
    keep = []
    with open(path, encoding="utf-8-sig", newline="") as f:
        header = next(csv.reader([f.readline()], delimiter=";"))
        missing = [c for c in ID_COLS + DEC_COLS if c not in header]
        if missing:
            raise ValueError(f"unexpected Zarc header, missing {missing}")
        pos = {c: header.index(c) for c in ID_COLS + DEC_COLS}
        for line in f:
            # Cheap pre-filter on the raw line; exact filter after parsing.
            if geos is not None and not any(f";{g};" in line for g in geos):
                continue
            row = next(csv.reader([line], delimiter=";"))
            if len(row) != len(header):
                raise ValueError(f"row with {len(row)} fields, header has {len(header)}")
            if geos is not None and row[pos["geocodigo"]] not in geos:
                continue
            if uf is not None and row[pos["UF"]] != uf:
                continue
            if crops is not None and row[pos["Nome_cultura"]] not in crops:
                continue
            keep.append([row[pos[c]] for c in ID_COLS + DEC_COLS])
    df = pd.DataFrame(keep, columns=ID_COLS + DEC_COLS)
    for c in DEC_COLS:
        df[c] = pd.to_numeric(df[c].replace("", pd.NA), errors="coerce").astype("Int64")
    return df


def provenance(spec: dict) -> dict:
    path = _path(spec)
    meta = base.read_meta(path)
    version = f"Tabua de Risco, {spec.get('safra', 'dictionary')}, Last-Modified {meta.get('last_modified')}"
    return base.base_provenance(path, spec["license"], version, role=spec.get("role"))


# Decoding --------------------------------------------------------------------

def _code(x) -> int | None:
    try:
        return int(x)
    except (TypeError, ValueError):
        return None


def season_of(safra_ini, safra_fin) -> str:
    """Zarc SafraIni/SafraFin -> ribbon season label, e.g. 2026, 2027 -> '2026/27'."""
    label = f"{int(safra_ini)}/{int(safra_fin) % 100:02d}"
    dekad.season_start_year(label)  # raises if the two years do not follow
    return label


def dekad_dates(zarc_i: int, season: str):
    """First and last day of Zarc dekad ``zarc_i`` inside the season.

    Sep to Dec fall in the first year, Jan to Aug in the second, as on the
    ribbon. The conversion goes through dekad.py and nowhere else.
    """
    return dekad.ribbon_bounds(dekad.zarc_to_ribbon(zarc_i), season)


def season_date(season: str, month_day: str):
    """'09-01' inside season '2026/27' -> 2026-09-01; '03-20' -> 2027-03-20."""
    m, d = (int(x) for x in month_day.split("-"))
    y = dekad.season_start_year(season)
    return date(y if m >= dekad.RIBBON_START_MONTH else y + 1, m, d)


def windows(values, season: str) -> list[dict]:
    """Runs of indicated dekads, in ribbon order (1 Sep to 31 Aug), with dates.

    Ribbon order keeps a window that crosses the new year, such as corn from
    late December to March, in one piece.
    """
    by_ribbon = {dekad.zarc_to_ribbon(z): int(v) for z, v in enumerate(values, 1)}
    runs, cur = [], []
    for r in range(1, dekad.N_DEKADS + 1):
        if by_ribbon[r] > 0:
            cur.append(r)
        elif cur:
            runs.append(cur)
            cur = []
    if cur:
        runs.append(cur)
    out = []
    for run in runs:
        z_first, z_last = dekad.ribbon_to_zarc(run[0]), dekad.ribbon_to_zarc(run[-1])
        risks = [by_ribbon[r] for r in run]
        out.append({
            "ribbon_first": run[0], "ribbon_last": run[-1],
            "zarc_first": z_first, "zarc_last": z_last,
            "start": dekad_dates(z_first, season)[0].isoformat(),
            "end": dekad_dates(z_last, season)[1].isoformat(),
            "n_dekads": len(run), "risk_min": min(risks), "risk_max": max(risks),
        })
    return out


def decode(df: pd.DataFrame, safra: str, role: str) -> pd.DataFrame:
    """One row per Zarc row, with names for the codes and the sowing windows."""
    out = df.copy()
    out.insert(0, "table_safra", safra)
    out.insert(1, "table_role", role)
    out["ciclo"] = out["Cod_Ciclo"].map(lambda x: CICLO.get(_code(x)))
    out["solo"] = out["Cod_Solo"].map(lambda x: SOLO.get(_code(x)))
    out["manejo"] = out["Cod_Outros_Manejos"].map(lambda x: MANEJO.get(_code(x)))
    out["nm"] = out["Cod_NM"].map(lambda x: NM.get(_code(x)))
    out.insert(2, "season", [season_of(a, b) for a, b in zip(out["SafraIni"], out["SafraFin"])])
    wins = [windows(r, season) if not any(pd.isna(v) for v in r) else []
            for season, r in zip(out["season"], out[DEC_COLS].itertuples(index=False))]
    out["n_windows"] = [len(w) for w in wins]
    out["window_start"] = [w[0]["start"] if w else None for w in wins]
    out["window_end"] = [w[-1]["end"] if w else None for w in wins]
    out["windows"] = [";".join(f"{x['start']}..{x['end']}" for x in w) for w in wins]
    return out


def long_form(decoded: pd.DataFrame) -> pd.DataFrame:
    """One line per row and dekad, both numberings side by side."""
    keys = ["table_safra", "season", "Nome_cultura", "municipio", "Cod_Ciclo", "Cod_Solo", "Cod_NM"]
    m = decoded.melt(id_vars=keys, value_vars=DEC_COLS, var_name="dec", value_name="risk_pct")
    m["zarc_dekad"] = m["dec"].str[3:].astype(int)
    m["ribbon_dekad"] = m["zarc_dekad"].map(dekad.zarc_to_ribbon)
    spans = [dekad_dates(z, s) for z, s in zip(m["zarc_dekad"], m["season"])]
    m["first_day"] = [a.isoformat() for a, _ in spans]
    m["last_day"] = [b.isoformat() for _, b in spans]
    return m.drop(columns="dec")


# Checks ------------------------------------------------------------------------

def check(decoded: pd.DataFrame, soy_window: dict, requested: dict) -> dict:
    """Pass or fail for each decoding check. ``requested``: {safra: [crops]}."""
    checks = []

    vals = decoded[DEC_COLS]
    complete = bool(len(decoded)) and not vals.isna().any().any()
    checks.append({"id": "36_dekads_per_row", "pass": complete,
                   "detail": f"{len(decoded)} rows, {len(DEC_COLS)} dekad columns, missing cells: {int(vals.isna().sum().sum())}"})

    seen = set(pd.unique(vals.to_numpy().ravel()).tolist()) if complete else set()
    checks.append({"id": "risk_values", "pass": complete and seen <= RISK_VALUES,
                   "detail": f"values seen: {sorted(int(v) for v in seen if not pd.isna(v))}"})

    soy = decoded[decoded["Nome_cultura"] == "Soja"]
    outside, union = [], set()
    for _, row in soy.iterrows():
        lo = season_date(row["season"], soy_window["start"])
        hi = season_date(row["season"], soy_window["end"])
        for z in range(1, 37):
            v = row[f"dec{z}"]
            if pd.notna(v) and v > 0:
                union.add(z)
                a, b = dekad_dates(z, row["season"])
                if not (lo <= a and b <= hi):
                    outside.append((row["municipio"], row["season"], z))
    checks.append({
        "id": "soy_only_1sep_31dec", "pass": len(soy) > 0 and not outside,
        "detail": (f"{len(soy)} soybean rows; indicated dekads (Zarc numbering) {sorted(union)}; "
                   f"outside {soy_window['start']}..{soy_window['end']}: {outside[:10]}"),
    })

    status = "decoded" if all(c["pass"] for c in checks) else "plan_b"

    coverage = []
    for safra, crops in requested.items():
        sub = decoded[decoded["table_safra"] == safra]
        for crop in crops:
            for mun, n in sub[sub["Nome_cultura"] == crop].groupby("municipio").size().items():
                coverage.append({"safra": safra, "crop": crop, "municipio": mun, "rows": int(n)})
            if not (sub["Nome_cultura"] == crop).any():
                coverage.append({"safra": safra, "crop": crop, "municipio": None, "rows": 0})
    return {"zarc_status": status, "checks": checks, "coverage": coverage}


def corn_window_end(decoded: pd.DataFrame) -> list[dict]:
    """Last indicated day of the Milho 2a Safra window, per municipality, cycle and soil."""
    sub = decoded[(decoded["Nome_cultura"] == "Milho 2ª Safra") & (decoded["table_role"] == "current")]
    return [{"municipio": r.municipio, "ciclo": r.ciclo, "solo": r.solo,
             "window_start": r.window_start, "window_end": r.window_end, "windows": r.windows}
            for r in sub.itertuples()]


# Run ---------------------------------------------------------------------------

def build(config: dict | None = None) -> tuple[pd.DataFrame, dict]:
    config = config or base.load_config()
    cfg = config["sources"]["zarc"]
    frames, prov, requested = [], [], {}
    for spec in specs(config):
        fetch(spec)
        prov.append(provenance(spec))
        if spec["kind"] != "table":
            continue
        df = read(_path(spec), spec["uf"], spec["municipalities"], spec["crops"])
        frames.append(decode(df, spec["safra"], spec["role"]))
        requested[spec["safra"]] = spec["crops"]
    decoded = pd.concat(frames, ignore_index=True)
    status = check(decoded, cfg["soy_legal_window"], requested)
    status.update({
        "decoding": {
            "dec1": "01-10 Jan", "dec36": "21-31 Dec",
            "module": "pipeline.common.dekad (ZARC_START_MONTH=1; ribbon starts 1 Sep)",
            "value_meaning": "risk class in percent: 20, 30 or 40; 0 = dekad not indicated",
            "value_meaning_source": "Zarc convention, not stated in the data dictionary. To confirm against one portaria",
        },
        "corn_window_end": corn_window_end(decoded),
        "soy_legal_window": {**cfg["soy_legal_window"], "source": cfg["soy_legal_window_source"]},
        "provenance": prov,
    })
    return decoded, status


def run(config: dict | None = None) -> dict:
    decoded, status = build(config)
    base.INTERIM_DIR.mkdir(parents=True, exist_ok=True)
    decoded.to_csv(base.INTERIM_DIR / OUT_ROWS, index=False)
    long_form(decoded).to_csv(base.INTERIM_DIR / "zarc_norte_pr_long.csv", index=False)
    base.write_json(base.INTERIM_DIR / OUT_STATUS, status)
    return status


def main() -> int:
    status = run()
    for c in status["checks"]:
        print(f"{'PASS' if c['pass'] else 'FAIL'} {c['id']}: {c['detail']}")
    gaps = [c for c in status["coverage"] if c["rows"] == 0]
    for g in gaps:
        print(f"GAP {g['crop']} not in table {g['safra']}")
    print(f"zarc_status={status['zarc_status']}")
    print(f"network_calls={base.network_calls}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

# Written with an SI tool (Claude, Anthropic) for NASA Space Apps 2026. See AI_USE.md.
"""The decision base file (``DECISION_BASE``) that ``pipeline.decision.field_inputs`` reads.

Before this module the base was written by hand, and the trials block had no producer.
Here it is built from three things:

- the config: decision season and Tábua edition, trial sites, units of plan 14.2;
- the fields (``config/fields/fields.geojson``): one entry per field, with its municipality
  from the IBGE municipal mesh, by the polygon (``pipeline.adapters.ibge_malhas``, SA-D64),
  and the municipality name the decoded Tábua uses;
- a trials source:
  * ``--trials manual``: ``data/manual/trials_a.csv`` through ``pipeline.bench.trials``, ONLY
    when ``protocol.status`` is "tagged". Before the tag the run is refused before the file
    is opened (protocol integrity). The step tables come from ``precompute.steps_from_gains``;
  * ``--trials example``: fictitious gains (tests/fixtures/decision_base_trials_example.json),
    the same code path, everything labelled "example, fictitious".

S2, corn with grass (protocol section 11, ``protocol.decision.s2_evidence``): 7 site-seasons
from Floresta (4) and Cafelândia (3). Each site-season carries its establishment method
(``S2_METHODS``), and ``options.S2.by_method`` holds the count and the step table per method.
A site-season without a method leaves ``by_method`` out, and decision.mjs then gives S2
"Not enough evidence" (``method_missing``).

Values that the protocol does not fix (cycle groups, the declared calendars, the risk
spans and thresholds, the example price numbers, the A3 context) come from ``--settings``:
``config/decision_settings.yaml``, every value with its source (``pipeline.decision.settings``),
or a plain JSON. ``--trials example`` falls back to the fictitious values of
tests/fixtures/decision_precompute_inputs.json. A manual run without them is refused, naming
the keys.

Rehearsal (``--rehearsal``, SA-D63, ``pipeline.common.rehearsal``): every field must lie in the
development strip, the trials are the fictitious gains (``--trials example``, never the
transcription), the settings file is required, and the base is labelled "real" (the NASA data
of the fields are real) with ``"run_mode": "rehearsal"``. Its trial texts say "Rehearsal" and
its trial source is ``rehearsal_trials``. Product runs downstream refuse it.

Run from the repository root:
    python -m pipeline.decision.base_file --trials example [--fields config/fields/fields.geojson]
        [--settings config/decision_settings.yaml] [--out data/build/decision_base.json]
    python -m pipeline.decision.base_file --rehearsal --trials example --fields config/fields/rehearsal.geojson
        --settings config/decision_settings.yaml --out data/rehearsal/build/decision_base.json
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import pandas as pd

from pipeline.adapters import base, ibge_malhas
from pipeline.common import rehearsal as rh
from pipeline.decision import precompute as pc
from pipeline.decision import settings as settings_mod

DEFAULT_OUT = pc.BUILD_DIR / "decision_base.json"
DEFAULT_FIELDS = base.REPO_ROOT / "config" / "fields" / "fields.geojson"
DEFAULT_ZARC = base.INTERIM_DIR / "zarc_norte_pr.csv"
MANUAL_TRIALS = base.REPO_ROOT / "data" / "manual" / "trials_a.csv"
EXAMPLE_TRIALS = pc.TESTS_DIR / "fixtures" / "decision_base_trials_example.json"
EXAMPLE_SETTINGS = pc.TESTS_DIR / "fixtures" / "decision_precompute_inputs.json"
TRIAL_MODES = ("manual", "example")
REHEARSAL = rh.REHEARSAL          # trial texts of a rehearsal base: fictitious gains under a real label

# Establishment method of the S2 consortium per trial site. Read from the method sections only,
# never from a yield: Doc 398 p. 15 (Floresta, in the corn row or alternate rows, 2 kg/ha) and
# Doc 459 material and methods (Cafelândia, broadcast, 8 to 12 kg/ha). See
# the source check notes of the project (outside this repository), item 5.
S2_METHODS = {"floresta": "in_row_or_alternate_rows_2kg", "cafelandia": "broadcast_8_12kg"}
S2_SITES = ("floresta", "cafelandia")
S3_SITES = ("londrina", "floresta", "cafelandia")
SETTINGS_KEYS = ("cycles", "occupancy", "risk", "example_context", "example_constraints")
REGION_SETTINGS = ("pack", "price_k", "example_numbers")
PERSONA_NOTE = "Example field, fictitious farm. The location is real, with no owner data."
TRIAL_DOCS = {"londrina": "Embrapa Doc 476", "floresta": "Embrapa Doc 398", "cafelandia": "Embrapa Doc 459"}


class BaseFileError(ValueError):
    pass


def _need(cond: bool, msg: str) -> None:
    if not cond:
        raise BaseFileError(msg)


def _load(path: Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


# Season and Tábua edition --------------------------------------------------------------

def current_safra(config: Mapping[str, Any]) -> str:
    cur = [t["safra"] for t in config["sources"]["zarc"]["tables"] if t["role"] == "current"]
    _need(len(cur) == 1, "sources.zarc.tables must have one table with role current")
    return cur[0]


def decision_season(safra: str) -> str:
    """'2026/2027' -> '2026/27'."""
    a, b = (int(x) for x in safra.split("/"))
    _need(b == a + 1, f"Tábua edition {safra!r} does not span two years")
    return f"{a}/{b % 100:02d}"


# Fields and municipalities -----------------------------------------------------------------

def zarc_names(zarc_rows: pd.DataFrame | None) -> dict[str, str]:
    """IBGE code -> municipality name as the decoded Tábua writes it."""
    if zarc_rows is None or "geocodigo" not in zarc_rows:
        return {}
    pairs = zarc_rows[["geocodigo", "municipio"]].drop_duplicates()
    return {str(int(g)): str(m) for g, m in zip(pairs["geocodigo"], pairs["municipio"])}


def field_entries(fields: Sequence[Mapping[str, Any]], mesh: Mapping[str, dict], names: Mapping[str, str],
                  settings: Mapping[str, Any]) -> dict[str, dict]:
    """``fields.<id>`` of the base: municipio (Tábua name), its IBGE code and the persona note.

    A polygon that crosses a municipal boundary, or falls outside the mesh, is refused. A code
    with no Tábua row keeps the label "IBGE <code>": the page then says no_window.
    """
    own = settings.get("fields") or {}
    out = {}
    for f in fields:
        fid = f["field_id"]
        m = ibge_malhas.field_municipality(mesh, f["ring"])
        _need(m["ok"], f"field {fid}: {m['reason']}")
        code = m["ibge"]
        note = (own.get(fid) or {}).get("fictional_persona_note", settings.get("fictional_persona_note", PERSONA_NOTE))
        out[fid] = {"municipio": names.get(code, f"IBGE {code}"), "municipio_ibge": code,
                    "municipio_in_zarc": code in names, "municipio_centroid": m["centroid"],
                    "fictional_persona_note": note}
    return out


# Trials --------------------------------------------------------------------------------------

def require_tagged(config: Mapping[str, Any]) -> None:
    """The transcribed trial values are read only after the protocol tag (protocol integrity)."""
    status = (config.get("protocol") or {}).get("status")
    _need(status == "tagged", f"protocol.status is {status!r}: the trial transcription is read only after the "
                              "tag. Use --trials example before it")


def gains_from_rows(rows: Sequence[Any]) -> dict[str, list[dict]]:
    """Per-site-season gains in kg/ha from transcription rows (``pipeline.bench.trials.TrialRow``).

    S3: soybean after wheat minus soybean after second-crop corn. S2: soybean after corn with
    brachiaria minus soybean after second-crop corn. A site-season without both rows is absent.
    """
    from pipeline.bench import trials as tr

    idx: dict[tuple, Any] = {}
    for r in rows:
        key = (r.doc, r.site, r.season, r.predecessor)
        _need(key not in idx, f"duplicate trial row {key}")
        idx[key] = r
    out: dict[str, list[dict]] = {"S2": [], "S3": []}
    for (doc, site, season, pred), r in sorted(idx.items(), key=lambda kv: (kv[0][1], kv[1].season_start)):
        if pred != tr.CORN:
            continue
        corn = tr.kg_ha_exact(r)
        for opt, other in (("S3", tr.WHEAT), ("S2", tr.CORN_BRACHIARIA)):
            o = idx.get((doc, site, season, other))
            if o is not None:
                out[opt].append({"site": site, "season": season, "gain_kg_ha": tr.kg_ha_exact(o) - corn})
    return out


def read_manual_gains(path: Path, config: Mapping[str, Any]) -> dict[str, list[dict]]:
    """Gains from the transcription A. Refused before the file is touched unless tagged."""
    require_tagged(config)
    from pipeline.bench import trials as tr

    return gains_from_rows(tr.read_transcription(path, "a").rows)


def read_example_gains(path: Path = EXAMPLE_TRIALS) -> dict[str, list[dict]]:
    doc = _load(path)
    _need(doc.get("label") == pc.EXAMPLE, f"{Path(path).name} must be labelled {pc.EXAMPLE!r}")
    return {opt: list(doc[opt]) for opt in ("S2", "S3")}


def option_block(opt: str, gains: Sequence[Mapping[str, Any]], *, sites: Sequence[str], names: Mapping[str, str],
                 mode: str, data_version: str, bag_kg: Any) -> dict:
    """``trials.options.<opt>`` for precompute: n, sites, steps from ``steps_from_gains``, texts.

    S2 also gets ``by_method`` (count and steps per establishment method), only when every
    site-season has a method. Otherwise it is left out and the page gives Not enough evidence.
    """
    rows = [g for g in gains if g["site"] in sites]
    unknown = sorted({g["site"] for g in gains} - set(sites))
    _need(not unknown, f"{opt}: trial sites {unknown} are not expected for this option")
    n = len(rows)
    seen = [g["site"] for g in rows]
    per_site = {s: seen.count(s) for s in sites if s in seen}
    example = mode == pc.EXAMPLE
    tag = _tag(mode)
    counts = ", ".join(f"{names.get(s, s)} {k}" for s, k in per_site.items())
    low = min(rows, key=lambda g: g["gain_kg_ha"]) if rows else None
    block: dict[str, Any] = {
        "n": n,
        "n_sites": len(per_site),
        "condition": (tag + (f"{n} site-seasons from {len(per_site)} sites ({counts}), soybean after "
                             + ("second-crop corn with brachiaria, one species sown on the corn sowing day, "
                                "two establishment methods" if opt == "S2" else "wheat")
                             + ", against soybean after second-crop corn")),
        "transfer_warning": tag + "Trials on clay above 750 g/kg. Check whether your soil is similar.",
        "steps": pc.steps_from_gains([g["gain_kg_ha"] for g in rows], bag_kg=bag_kg),
        "bad_year_text": tag + (f"Lowest gain in {names.get(low['site'], low['site'])}, {low['season']}." if low
                                else "No site-season."),
        "source_ids": (["example_trials"] if example else [rh.TRIALS_SOURCE_ID] if mode == REHEARSAL
                       else ["embrapa_docs"]),
        # The steps are counts N of n, a team calculation: derived_from_trials for both options (M6.4,
        # ledger.build.check_classes). The measured S2 gain keeps its own class in the L3 summary.
        "evidence_class": "derived_from_trials",
        "data_version": data_version,
        "site_seasons": [{"site": g["site"], "season": g["season"],
                          **({"method": S2_METHODS.get(g["site"])} if opt == "S2" else {})} for g in rows],
    }
    if opt == "S2":
        methods = [S2_METHODS.get(g["site"]) for g in rows]
        if rows and all(methods):
            block["by_method"] = []
            for meth in dict.fromkeys(S2_METHODS[x] for x in sites if S2_METHODS.get(x) in methods):
                mine = [g for g, m in zip(rows, methods) if m == meth]
                block["by_method"].append({
                    "method": meth, "n": len(mine),
                    "sites": sorted({names.get(g["site"], g["site"]) for g in mine}),
                    "steps": pc.steps_from_gains([g["gain_kg_ha"] for g in mine], bag_kg=bag_kg)})
    return block


def _tag(mode: str) -> str:
    """Prefix of every trial text: the example label, the rehearsal note, or nothing (manual)."""
    if mode == pc.EXAMPLE:
        return f"{pc.EXAMPLE.capitalize()}. "
    return f"{rh.NOTE}. " if mode == REHEARSAL else ""


def trials_block(gains: Mapping[str, Sequence[Mapping[str, Any]]], *, names: Mapping[str, str], mode: str,
                 data_version: str, bag_kg: Any) -> dict:
    """``trials`` of the base. ``mode``: ``pc.EXAMPLE``, ``REHEARSAL`` (fictitious gains) or ``pc.REAL``."""
    return {
        "rights_note": (_tag(mode) + "Fictitious gains, not trial values.") if mode in (pc.EXAMPLE, REHEARSAL) else (
                        "Counts from Embrapa Soja Documentos 398, 459 and 476, cited with document, page and table. "
                        "The yields themselves are not shipped."),
        "options": {
            "S2": option_block("S2", gains["S2"], sites=S2_SITES, names=names, mode=mode, data_version=data_version,
                               bag_kg=bag_kg),
            "S3": option_block("S3", gains["S3"], sites=S3_SITES, names=names, mode=mode, data_version=data_version,
                               bag_kg=bag_kg),
        },
        "money_refs": [],
    }


# Settings and assembly --------------------------------------------------------------------------

def settings_for(mode: str, settings: Mapping[str, Any] | None) -> dict:
    """The [DECIDE] values. Example: the fictitious values of the precompute fixture fill the gaps."""
    s = dict(settings or {})
    if mode == pc.EXAMPLE:
        ex = _load(EXAMPLE_SETTINGS)
        for k in SETTINGS_KEYS:
            s.setdefault(k, ex[k])
        reg = dict(s.get("region") or {})
        for k in REGION_SETTINGS:
            reg.setdefault(k, ex["region"][k])
        s["region"] = reg
    missing = [k for k in SETTINGS_KEYS if k not in s]
    missing += [f"region.{k}" for k in REGION_SETTINGS if k not in (s.get("region") or {})]
    _need(not missing, f"--settings lacks {missing} (values the protocol does not fix) [DECIDE]")
    return s


def build(config: Mapping[str, Any], *, trials: str, fields_geojson: Path, settings: Mapping[str, Any] | None = None,
          mesh: Mapping[str, dict] | None = None, zarc_rows: pd.DataFrame | None = None,
          manual_path: Path = MANUAL_TRIALS, example_path: Path = EXAMPLE_TRIALS, rehearsal: bool = False) -> dict:
    """The base JSON. ``trials`` is "manual" (tagged protocol only) or "example".

    ``rehearsal``: fields of the development strip only, fictitious gains only, settings required.
    The base is labelled "real" and stamped ``run_mode: rehearsal``.
    """
    _need(trials in TRIAL_MODES, f"--trials must be one of {TRIAL_MODES}")
    if rehearsal:
        _need(trials == "example", "a rehearsal never reads the trial transcription: use --trials example (SA-D63)")
        _need(settings is not None, "a rehearsal needs --settings (config/decision_settings.yaml): the fixture "
                                    "values are not used outside the example")
    mode = pc.REAL if trials == "manual" or rehearsal else pc.EXAMPLE
    if trials == "manual":
        require_tagged(config)                      # before anything else is read
    s = settings_for(mode, settings)
    from pipeline.fields import geometry

    if rehearsal:   # SA-D63: every polygon east of protocol.field.development_strip.lon_min
        fields = geometry.load_fields(fields_geojson, config, rehearsal=True)
    else:
        fields = geometry.parse_fields(geometry.read_geojson(fields_geojson))
    names = zarc_names(zarc_rows)
    mesh = mesh if mesh is not None else ibge_malhas.mesh_from_cache(config)
    entries = field_entries(fields, mesh, names, s)
    safra = current_safra(config)
    site_names = {site["id"]: names.get(str(site["ibge"]), site["name"]) for site in config["trial_sites"]}
    from pipeline.bench import trials as tr

    bag = int(tr.SACK_KG)
    if trials == "manual":
        gains = read_manual_gains(manual_path, config)
        dv = (f"{Path(manual_path).name} sha256 {base.sha256_file(manual_path)}, "
              f"{config['protocol']['id']} {config['protocol']['status']}")
    elif rehearsal:
        gains = read_example_gains(example_path)
        dv = f"{rh.NOTE}: {Path(example_path).name} sha256 {base.sha256_file(example_path)}"
    else:
        gains = read_example_gains(example_path)
        dv = pc.EXAMPLE
    block_mode = REHEARSAL if rehearsal else mode
    reg = s["region"]
    out = {
        "label": mode,
        "made_by": "pipeline.decision.base_file",
        "trials_source": trials,
        "season": decision_season(safra),
        "current_safra": safra,
        **{k: s[k] for k in SETTINGS_KEYS},
        "region": {"trial_sites": [site_names[site["id"]] for site in config["trial_sites"]],
                   "pack": reg["pack"],
                   "units": {"bag_kg": bag, "alqueire_ha": float(tr.ALQUEIRE_HA), "screen_unit": "ha"},
                   "price_k": reg["price_k"], "example_numbers": reg["example_numbers"]},
        "trials": trials_block(gains, names=site_names, mode=block_mode, data_version=dv, bag_kg=bag),
        "fields": entries,
    }
    if "erosivity" in s:
        out["erosivity"] = s["erosivity"]
    return rh.stamped(out) if rehearsal else out


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--trials", required=True, choices=TRIAL_MODES)
    ap.add_argument("--fields", type=Path, default=DEFAULT_FIELDS)
    ap.add_argument("--settings", type=Path, default=None,
                    help="config/decision_settings.yaml (sourced values) or a plain JSON: the values the protocol "
                         "does not fix")
    ap.add_argument("--zarc", type=Path, default=DEFAULT_ZARC, help="decoded Tábua, for the municipality names")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--rehearsal", action="store_true",
                    help="development strip only, fictitious trials, every output stamped run_mode rehearsal (SA-D63)")
    a = ap.parse_args(argv)
    config = base.load_config()
    out = Path(a.out)
    try:
        if a.trials == "manual":
            require_tagged(config)
        if a.rehearsal:
            rh.check_out(out, "decision base")
        settings = settings_mod.load(a.settings) if a.settings else None
        rows = pd.read_csv(a.zarc) if Path(a.zarc).is_file() else None
        doc = build(config, trials=a.trials, fields_geojson=a.fields, settings=settings, zarc_rows=rows,
                    rehearsal=a.rehearsal)
    except (BaseFileError, ibge_malhas.MeshError, pc.PrecomputeError, ValueError, FileNotFoundError) as exc:
        print(f"base_file: {exc}", file=sys.stderr)
        return 1
    _need(not pc._inside(out, pc.SITE_DATA), "the decision base never goes to site/data/")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    mode = f", {rh.NOTE}" if a.rehearsal else ""
    print(f"{len(doc['fields'])} field(s), trials {a.trials} ({doc['label']}{mode}), S2 n={doc['trials']['options']['S2']['n']}"
          f" S3 n={doc['trials']['options']['S3']['n']} -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

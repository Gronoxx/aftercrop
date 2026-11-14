# Written with an SI tool (Claude, Anthropic) for NASA Space Apps 2026. See AI_USE.md.
"""Fixed menu of three strategies, rules, user constraints and alerts (plan, section 13).

Ids, rules and data only. Every user-facing text lives in ``config/copy_en.yaml`` and
``config/copy_pt.yaml``, the single owner of screen copy: this module holds the copy keys,
and ``precompute`` resolves them when it writes region.json. ``tests/test_copy.py`` checks
that every id here has its text in both languages.

S2 and S3 are always compared with S1 on the same field, in the same rows (13.6): no
composite score, no ranking, no "best rotation". The menu is fixed (13.8): there is no
generator. The decision rule lives in ``site/js/decision.mjs`` and is never reimplemented
here. No alert changes a state (13.5).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

OPTION_IDS = ("S2", "S3")          # compared with S1, as in decision.mjs OPTIONS
STRATEGY_IDS = ("S1", "S2", "S3")  # the current rotation first (13.1)

CONFIG_DIR = Path(__file__).resolve().parents[2] / "config"
LANGS = ("en", "pt")

# Sources named by the plan in section 13: bibliographic title and URL. The manifest (M9)
# must use the same ids.
SOURCES = {
    "zarc_tabua": {
        "title": "MAPA, Tábua de Risco do Zarc",
        "url": "https://dados.agricultura.gov.br/dataset/tabua-de-risco-zoneamento-agricola-de-risco-climatico",
    },
    "adapar_portaria_1579_2026": {
        "title": "Adapar, Portaria 1.579/2026, vazio sanitário da soja 2026/2027",
        "url": "https://www.adapar.pr.gov.br/Noticia/Periodos-do-vazio-sanitario-da-Soja-para-safra-de-20262027-comecam-em-junho",
    },
    "zarc_portaria_344_2026": {
        "title": "MAPA, Zarc Portaria 344/2026, Paraná",
        "url": "https://www.gov.br/agricultura/pt-br/assuntos/riscos-seguro/programa-nacional-de-zoneamento-agricola-de-risco-climatico/portarias/safra-vigente/parana/POC2DF1.PDF",
    },
    "zarc_frost_2026_27": {
        "title": "MAPA, Zarc 2026/27, Paraná, PO5FD71",
        "url": "https://www.gov.br/agricultura/pt-br/assuntos/riscos-seguro/programa-nacional-de-zoneamento-agricola-de-risco-climatico/portarias/safra-vigente/parana/PO5FD71.PDF",
    },
    "embrapa_doc398": {
        "title": "Embrapa Soja, Documentos 398",
        "url": "https://www.infoteca.cnptia.embrapa.br/infoteca/bitstream/doc/1086382/1/Doc398.pdf",
    },
    "embrapa_doc476": {
        "title": "Embrapa Soja, Documentos 476",
        "url": "https://www.infoteca.cnptia.embrapa.br/infoteca/bitstream/doc/1178322/1/DOC-476-.pdf",
    },
    "embrapa_jorn_acad_2025": {
        "title": "Embrapa Soja, Jornada Acadêmica 2025, p. 131",
        "url": "https://www.alice.cnptia.embrapa.br/alice/bitstream/doc/1178727/1/p-131-ETC-5-com-ISSN-Jorn-Acad-2025-19.pdf",
    },
    "embrapa_sp17_2020": {
        "title": "Embrapa Soja, Sistemas de Produção 17 (2020)",
        "url": "https://www.infoteca.cnptia.embrapa.br/infoteca/bitstream/doc/1123928/1/SP-17-2020-online-1.pdf",
    },
    "embrapa_pratylenchus_host": {
        "title": "Embrapa, Alice 883207, p. 22",
        "url": "https://www.alice.cnptia.embrapa.br/alice/bitstream/doc/883207/1/p22.pdf",
    },
    "embrapa_trigo_sistemas_2019": {
        "title": "Embrapa Trigo, Sistemas de produção (2019)",
        "url": "https://www.alice.cnptia.embrapa.br/alice/bitstream/doc/1111855/1/ID446652019LVSistemasdeproducao.pdf",
    },
    "embrapa_cartilha_cigarrinha": {
        "title": "Embrapa, Cartilha de manejo da cigarrinha e dos enfezamentos do milho",
        "url": "https://www.infoteca.cnptia.embrapa.br/infoteca/bitstream/doc/1130346/1/Cartilha-Manejo-cigarrinha-enfezamentos-milho.pdf",
    },
    "cultivar_giberela": {
        "title": "Revista Cultivar, Menos giberela",
        "url": "https://revistacultivar.com.br/artigos/menos-giberela",
    },
    "epagri_giberela": {
        "title": "Epagri, Revista Agropecuária Catarinense, artigo 635",
        "url": "https://publicacoes.epagri.sc.gov.br/rac/article/download/635/537/0",
    },
    # S2 grass phase of config/decision_settings.yaml (desiccation before the soybean sowing).
    "rsd_2020_brachiaria_desiccation": {
        "title": "Research, Society and Development (2020), article 10867",
        "url": "https://rsdjournal.org/index.php/rsd/article/download/10867/9752",
    },
}


# Copy ----------------------------------------------------------------------------

@lru_cache(maxsize=None)
def load_copy(lang: str = "en") -> dict:
    """``config/copy_<lang>.yaml``, the owner of every user-facing text."""
    if lang not in LANGS:
        raise ValueError(f"unknown language {lang!r}")
    with open(CONFIG_DIR / f"copy_{lang}.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def text(key: str, lang: str = "en", copy: Mapping[str, Any] | None = None) -> Any:
    """The copy value at a dotted key, such as ``page.strategies.S1.name``. KeyError if absent."""
    node: Any = copy if copy is not None else load_copy(lang)
    for part in key.split("."):
        if not isinstance(node, Mapping) or part not in node:
            raise KeyError(f"copy_{lang}.yaml has no key {key!r}")
        node = node[part]
    return node


# Strategies ----------------------------------------------------------------------

@dataclass(frozen=True)
class Strategy:
    id: str
    source_ids: tuple[str, ...]
    simulated: bool
    engine: tuple[str, ...] | str          # engine chain, or the fixed state "not_simulated"
    autumn_crop: str                       # calendar crop key of the crop that follows soybean
    zarc_crop: str                         # Nome_cultura of its sowing window in the Tábua (data)
    zarc_table: str                        # "current", or "wheat_table" (protocol.zarc.wheat_table)
    risk_alerts: tuple[str, ...] = ()      # alert ids for the agronomist sheet (13.5, 7.9)

    @property
    def copy_key(self) -> str:
        return f"page.strategies.{self.id}"

    @property
    def name(self) -> str:
        """Display name, from the English copy."""
        return text(f"{self.copy_key}.name")

    def region_entry(self, lang: str = "en") -> dict:
        """The entry of region.json (plan 8.6): id, name, sequence_text, source_ids, simulated.

        Texts are resolved from the copy file of ``lang``.
        """
        return {
            "id": self.id,
            "name": text(f"{self.copy_key}.name", lang),
            "sequence_text": text(f"{self.copy_key}.sequence", lang),
            "source_ids": list(self.source_ids),
            "simulated": self.simulated,
        }


S1 = Strategy(
    id="S1",
    source_ids=("embrapa_doc398", "embrapa_doc476"),   # model 1 of Doc 398, MP1 of Doc 476 (13.2)
    simulated=True,
    engine=("soybean", "corn", "fallow", "soybean"),
    autumn_crop="corn",
    zarc_crop="Milho 2ª Safra",
    zarc_table="current",
    risk_alerts=("leafhopper_stunting",),
)

S2 = Strategy(
    id="S2",
    source_ids=("embrapa_doc398", "zarc_portaria_344_2026"),   # model 4 of Doc 398 (13.3)
    simulated=False,
    engine="not_simulated",
    autumn_crop="corn_grass",
    zarc_crop="Milho 2ª Safra consorciado com Braquiária",
    zarc_table="current",
    risk_alerts=("pratylenchus", "leafhopper_stunting"),
)

S3 = Strategy(
    id="S3",
    source_ids=("embrapa_doc476", "embrapa_jorn_acad_2025"),   # 13.4
    simulated=True,
    engine=("soybean", "fallow", "wheat", "fallow", "soybean"),
    autumn_crop="wheat",
    zarc_crop="Trigo",
    zarc_table="wheat_table",
    risk_alerts=("wheat_root_disease", "gibberella", "frost_at_heading", "white_mold", "blast_and_harvest_rain"),
)

STRATEGIES = {s.id: s for s in (S1, S2, S3)}

# Calendar crops. "none" is a stretch without a live crop: never called bare soil (7.5).
CROPS = ("soybean", "corn", "corn_grass", "grass", "wheat", "none")


# Rules, constraints and alerts ------------------------------------------------------

# Hard rule for every option (13.5). Plan dates: soybean-free period 2 Jun to 31 Aug, soybean
# sowing 1 Sep to 31 Dec in the North (Portaria 1.579/2026, read by Adapar). The legal window
# is also in the config (sources.zarc.soy_legal_window), and the tests check both agree.
# Its reason text: copy key page.limits.reasons.<id>.
HARD_RULE = {
    "id": "soy_calendar_portaria_1579_2026",
    "source_id": "adapar_portaria_1579_2026",
    "soy_sowing": {"start": "09-01", "end": "12-31"},
    "soy_free": {"start": "06-02", "end": "08-31"},
}

# Soft rules (13.5): they warn and never remove. Text: page.limits.soft.<id>.
SOFT_RULES = (
    {"id": "wheat_max_half_area", "applies_to": ["S3"], "source_id": "embrapa_doc476", "removes": []},
    {"id": "one_winter_without_wheat_or_barley", "applies_to": ["S3"], "source_id": "embrapa_trigo_sistemas_2019",
     "removes": []},
)

# User constraints (7.4). ``removes`` lists the options that leave when the constraint is
# marked, in the shape removeOption() of decision.mjs reads. Text: page.limits.<id>
# (question) and page.limits.reasons.<id> (the reason, for a constraint that removes).
# The Zarc insurance and credit constraint removes nothing here: rule 6 of 13.7 handles it
# inside decision.mjs (zarcConstraint).
CONSTRAINTS = (
    {"id": "no_narrow_row_seeder", "removes": ["S3"]},
    {"id": "no_grass_seeding", "removes": ["S2"]},
    {"id": "no_wheat_buyer", "removes": ["S3"]},
    {"id": "rented_land", "removes": []},              # an item for the agronomist sheet
    {"id": "zarc_insurance_credit", "removes": []},    # rule 6, decision.mjs
)

# Alerts for the agronomist sheet (13.5). None changes a state or becomes a penalty.
# ``trigger`` is a code, ``audited`` is False for the two the plan marks for local review.
# Text: sheet.alerts.<id> in both languages.
ALERTS = (
    {"id": "pratylenchus", "trigger": "option_S2", "source_id": "embrapa_sp17_2020", "audited": True},
    {"id": "wheat_root_disease", "trigger": "option_S3_repeated", "source_id": "embrapa_trigo_sistemas_2019",
     "audited": True},
    {"id": "white_mold", "trigger": "altitude_above_600_m_nasadem", "source_id": "embrapa_sp17_2020", "audited": True},
    {"id": "leafhopper_stunting", "trigger": "live_or_volunteer_corn", "source_id": "embrapa_cartilha_cigarrinha",
     "audited": True},
    {"id": "frost_at_heading", "trigger": "wheat_power_minimum_temperature", "source_id": "zarc_frost_2026_27",
     "audited": True},
    {"id": "gibberella", "trigger": "general", "source_id": "cultivar_giberela", "audited": True},   # sources conflict
    {"id": "blast_and_harvest_rain", "trigger": "local_review", "source_id": None, "audited": False},
)


def strategy(sid: str) -> Strategy:
    try:
        return STRATEGIES[sid]
    except KeyError:
        raise KeyError(f"unknown strategy: {sid!r}") from None


def zarc_table_safra(s: Strategy, protocol: Mapping[str, Any], current_safra: str) -> str:
    """Tábua edition that gives the sowing window of the strategy's autumn crop.

    Wheat is absent from the 2026/27 table, so S3 reads ``protocol.zarc.wheat_table``
    (2025/2026, [DECIDE D4]) and the page carries the label of copy key
    ``page.rows.L2.wheat_zarc`` (``protocol.zarc.wheat_label``).
    """
    if s.zarc_table == "current":
        return current_safra
    if s.zarc_table == "wheat_table":
        z = (protocol or {}).get("zarc") or {}
        if not z.get("wheat_table"):
            raise KeyError("protocol.zarc.wheat_table is missing")
        return str(z["wheat_table"])
    raise ValueError(f"unknown zarc_table: {s.zarc_table!r}")


def reason_text(rule_id: str, lang: str = "en") -> str:
    return text(f"page.limits.reasons.{rule_id}", lang)


def hard_rules(removes: Mapping[str, list[str]] | None = None, lang: str = "en") -> list[dict]:
    """Hard rules in the shape of region.json and decision.mjs: { id, removes, why, ... }.

    ``removes`` maps the rule id to the options whose calendar breaks it, found by
    ``calendar.hard_rule_breaks``. The fixed menu passes by construction (13.1), so the
    list is normally empty. ``why`` comes from the copy file.
    """
    r = list((removes or {}).get(HARD_RULE["id"], []))
    bad = [o for o in r if o not in STRATEGY_IDS]
    if bad:
        raise ValueError(f"unknown options in hard rule removes: {bad}")
    return [{
        "id": HARD_RULE["id"],
        "source_id": HARD_RULE["source_id"],
        "soy_sowing": dict(HARD_RULE["soy_sowing"]),
        "soy_free": dict(HARD_RULE["soy_free"]),
        "removes": sorted(r),
        "why": reason_text(HARD_RULE["id"], lang),
    }]


def soft_rules(lang: str = "en") -> list[dict]:
    return [{**r, "text": text(f"page.limits.soft.{r['id']}", lang)} for r in SOFT_RULES]


def constraints(lang: str = "en") -> list[dict]:
    """region.json constraints: { id, label, removes, why }, texts from the copy file."""
    out = []
    for c in CONSTRAINTS:
        why_key = f"page.limits.reasons.{c['id']}" if c["removes"] else f"page.limits.{c['id']}.effect"
        out.append({"id": c["id"], "label": text(f"page.limits.{c['id']}.question", lang),
                    "removes": list(c["removes"]), "why": text(why_key, lang)})
    return out


def alerts() -> list[dict]:
    """region.json alerts: { id, text_en, text_pt, trigger, source_id, audited }."""
    return [{"id": a["id"], "text_en": text(f"sheet.alerts.{a['id']}", "en"),
             "text_pt": text(f"sheet.alerts.{a['id']}", "pt"), "trigger": a["trigger"],
             "source_id": a["source_id"], "audited": a["audited"]} for a in ALERTS]


def check_menu() -> None:
    """Invariants of the fixed menu, and that every id has its copy in both languages."""
    if tuple(STRATEGIES) != STRATEGY_IDS:
        raise AssertionError("S1 first, then S2 and S3")
    alert_ids = {a["id"] for a in ALERTS}
    for s in STRATEGIES.values():
        if not s.source_ids or any(x not in SOURCES for x in s.source_ids):
            raise AssertionError(f"{s.id}: missing or unknown source")
        if s.autumn_crop not in CROPS or s.simulated != (s.engine != "not_simulated"):
            raise AssertionError(f"{s.id}: bad crop or engine state")
        if not set(s.risk_alerts) <= alert_ids:
            raise AssertionError(f"{s.id}: unknown alert")
    for a in ALERTS:
        if not (a["source_id"] is None or a["source_id"] in SOURCES) or a["audited"] != (a["source_id"] is not None):
            raise AssertionError(a["id"])
    for c in CONSTRAINTS:
        if not set(c["removes"]) <= set(OPTION_IDS):
            raise AssertionError(c["id"])
    for r in SOFT_RULES:
        if r["removes"] != [] or r["source_id"] not in SOURCES:
            raise AssertionError(r["id"])
    for lang in LANGS:
        for s in STRATEGIES.values():
            s.region_entry(lang)
        hard_rules(lang=lang)
        soft_rules(lang)
        constraints(lang)
    alerts()

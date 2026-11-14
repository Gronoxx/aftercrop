# Written with an SI tool (Claude, Anthropic) for NASA Space Apps 2026. See AI_USE.md.
"""Screen copy checks (plan SA-D59 and sheet M7, criteria M7.5 and M7.6).

config/copy_en.yaml and config/copy_pt.yaml hold every text of the page, How
we know, the glossary and the agronomist sheet. Both carry the same keys and
the same placeholders, and show the same state for the same result.

Checked here:
- same keys, same placeholders, no empty string, no dash and no semicolon;
- the forbidden words of M7.5 (test_forbidden_words and neighbours);
- the fixed notices of M7.6 (test_required);
- the strings of STATES, MESSAGES and FIELD_LABELS in site/js/decision.mjs,
  and every result code and flag it emits, have their copy;
- the label texts of pipeline/bench/label.py;
- the fixed phrases of the protocol (M0.3 declarations, labels, states,
  R1 states, "context only", H2 and the soil domain) appear in copy_en;
- the plan wording where the plan fixes it, and the Portuguese sheet of 7.12.

Nothing here renders the page. test_labels_render.py and test_sheet.py do that.
"""
from __future__ import annotations

import ast
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from pipeline.bench import label as lb

ROOT = Path(__file__).resolve().parents[1]
EN_PATH = ROOT / "config" / "copy_en.yaml"
PT_PATH = ROOT / "config" / "copy_pt.yaml"
REGION_YAML = ROOT / "config" / "region_norte_pr.yaml"
PROTOCOL_MD = ROOT / "PROTOCOL.md"
DECISION_JS = ROOT / "site" / "js" / "decision.mjs"
TEST_PROTOCOL = ROOT / "tests" / "test_protocol.py"

PLACEHOLDER = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")
DASHES_AND_SEMICOLONS = ("—", "–", "‒", "―", ";", "；")


def load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def leaves(node, path=()):
    """(dotted key, value) for every leaf. List items count by index."""
    if isinstance(node, dict):
        for key, value in node.items():
            yield from leaves(value, path + (str(key),))
    elif isinstance(node, list):
        for i, value in enumerate(node):
            yield from leaves(value, path + (str(i),))
    else:
        yield ".".join(path), node


EN = load(EN_PATH)
PT = load(PT_PATH)
EN_LEAVES = dict(leaves(EN))
PT_LEAVES = dict(leaves(PT))
BOTH = [("en", k, v) for k, v in EN_LEAVES.items()] + [("pt", k, v) for k, v in PT_LEAVES.items()]
EN_TEXT = list(EN_LEAVES.values())


def get(tree: dict, dotted: str):
    node = tree
    for part in dotted.split("."):
        node = node[int(part)] if isinstance(node, list) else node[part]
    return node


def in_en(phrase: str, *, case: bool = True) -> bool:
    if case:
        return any(phrase in s for s in EN_TEXT)
    return any(phrase.lower() in s.lower() for s in EN_TEXT)


@pytest.fixture(scope="module")
def protocol() -> dict:
    return load(REGION_YAML)["protocol"]


@pytest.fixture(scope="module")
def protocol_md() -> str:
    return PROTOCOL_MD.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def decision() -> dict:
    """The exported constants of decision.mjs, read by Node (the same Node as make test-js)."""
    node = shutil.which("node")
    if node is None:
        pytest.fail("node is needed to read site/js/decision.mjs (make test-js needs it too)")
    script = (
        "const D = await import(process.argv[1]);"
        "console.log(JSON.stringify({STATES: D.STATES, PRECEDENCE: D.PRECEDENCE,"
        " MESSAGES: D.MESSAGES, FIELD_LABELS: D.FIELD_LABELS,"
        " ZARC_CONSORTIUM_REASON: D.ZARC_CONSORTIUM_REASON}));"
    )
    out = subprocess.run(
        [node, "--input-type=module", "-e", script, DECISION_JS.as_uri()],
        capture_output=True, text=True, timeout=60, check=True,
    )
    return json.loads(out.stdout)


@pytest.fixture(scope="module")
def decision_source() -> str:
    return DECISION_JS.read_text(encoding="utf-8")


# ---- structure (SA-D59) ------------------------------------------------------

def test_languages():
    assert EN["meta"]["lang"] == "en"
    assert PT["meta"]["lang"] == "pt-BR"


def test_same_keys():
    only_en = sorted(set(EN_LEAVES) - set(PT_LEAVES))
    only_pt = sorted(set(PT_LEAVES) - set(EN_LEAVES))
    assert not only_en and not only_pt, f"only in copy_en: {only_en}, only in copy_pt: {only_pt}"


def test_nonempty_strings():
    not_text = [(lang, key) for lang, key, value in BOTH if not isinstance(value, str)]
    assert not not_text, f"not a string: {not_text}"
    empty = [(lang, key) for lang, key, value in BOTH if not value.strip()]
    assert not empty, f"empty: {empty}"
    padded = [(lang, key) for lang, key, value in BOTH if value != value.strip()]
    assert not padded, f"leading or trailing spaces: {padded}"


def test_same_placeholders():
    wrong = {}
    for key, en in EN_LEAVES.items():
        pt = PT_LEAVES.get(key)
        if not isinstance(en, str) or not isinstance(pt, str):
            continue
        a, b = sorted(set(PLACEHOLDER.findall(en))), sorted(set(PLACEHOLDER.findall(pt)))
        if a != b:
            wrong[key] = (a, b)
    assert not wrong, f"placeholders differ: {wrong}"


def test_braces_are_placeholders():
    for lang, key, value in BOTH:
        rest = PLACEHOLDER.sub("", value)
        assert "{" not in rest and "}" not in rest, f"{lang} {key}: stray brace in {value!r}"


def test_no_dash_or_semicolon():
    bad = [(lang, key) for lang, key, value in BOTH if any(c in value for c in DASHES_AND_SEMICOLONS)]
    assert not bad, f"em dash, en dash or semicolon in {bad}"


# ---- M7.5: forbidden words ---------------------------------------------------

FORBIDDEN = {
    "t/ha": r"(?<![\w$])t\s?/\s?ha\b",
    "t C/ha": r"(?<![\w$])t\s?C\s?/\s?ha\b",
    "same yield": r"same yield|mesma produtividade",
    "best rotation": r"best rotation|melhor rota[cç][aã]o",
    "composite score": r"composite (score|index|grade)|nota composta|[ií]ndice composto",
    "benchmark": r"benchmark",
    "pre-registered": r"pre-?registered|pr[eé]-?registrad",
    "global framework": r"global framework|framework global|estrutura global",
    "honest": r"\bhonest",
    "p-value": r"\bp-?values?\b|\bp-?valor|\bvalor-?p\b|\bp\s*(<=|>=|<|>|=|≤|≥)\s*[0-9]",
    "confidence interval": r"confidence interval|intervalo de confian[cç]a",
}


@pytest.mark.parametrize("name", list(FORBIDDEN))
def test_forbidden_words(name):
    pattern = re.compile(FORBIDDEN[name], re.IGNORECASE)
    hits = [(lang, key) for lang, key, value in BOTH if pattern.search(value)]
    assert not hits, f"{name!r} in {hits}"


def test_no_ci_or_ic():
    pattern = re.compile(r"\b(CI|IC)\b")  # case-sensitive: the abbreviations only
    hits = [(lang, key) for lang, key, value in BOTH if pattern.search(value)]
    assert not hits, f"CI or IC in {hits}"


def label_titles() -> list[str]:
    titles = [v["title"] for v in EN["labels"].values()] + [v["title"] for v in PT["labels"].values()]
    return sorted(titles, key=len, reverse=True)


def test_validated_only_in_labels():
    pattern = re.compile(r"validat|validad|valida[cç]", re.IGNORECASE)
    hits = []
    for lang, key, value in BOTH:
        rest = value
        for title in label_titles():
            rest = rest.replace(title, "")
        if pattern.search(rest):
            hits.append((lang, key))
    assert not hits, f"'validated' outside the label texts in {hits}"


SIMULATION = re.compile(
    r"simulat|simulad|in the model|no modelo|model variation|varia[cç][oõ]es do modelo|configura", re.IGNORECASE)


def test_no_percent_of_simulation():
    hits = []
    for lang, key, value in BOTH:
        for sentence in re.split(r"(?<=[.!?])\s+", value):
            if "%" in sentence and SIMULATION.search(sentence):
                hits.append((lang, key, sentence))
    assert not hits, f"a percent next to a simulation in {hits}"


def test_count_of_ten_needs_simulated():
    # "8 de 10" only with "simulated" (M7.5, 7.13): a count of ten is never a chance.
    pattern = re.compile(r"\b\d+\s+(?:of|in|de|em)\s+(?:the\s+|os\s+)?10\b", re.IGNORECASE)
    hits = [(lang, key) for lang, key, value in BOTH
            if pattern.search(value) and not re.search(r"simulat|simulad", value, re.IGNORECASE)]
    assert not hits, f"'N of 10' without 'simulated' in {hits}"


def test_never_inside_the_window(protocol):
    assert protocol["window_warning"]["never"] == "inside"
    pattern = re.compile(r"inside (?:the )?(?:corn )?(?:zarc )?window|dentro da janela", re.IGNORECASE)
    hits = [(lang, key) for lang, key, value in BOTH if pattern.search(value)]
    assert not hits, f"'inside the window' in {hits}"


# ---- M7.6: fixed notices -----------------------------------------------------

REQUIRED = (
    "supports, does not replace local judgment",
    "counts of trials from 3 sites, not your chance",
    "Not a forecast for your field",
    "All numbers per hectare",
    "Not visible from orbit: roots, nitrogen, nematodes, disease, yield",
    "Example field, fictitious farm. The location is real, with no owner data.",
    "These trials come from 3 sites on clay above 750 g/kg. Check whether your soil is similar.",
    "Public reference: Zarc/MAPA, 2026/27 season. This tool is independent",
    "This effect is not calculated",
    "First evidence region: northern Paraná, Brazil. The method can be evaluated elsewhere, "
    "but each new region needs its own field-trial evidence.",
    "No field trials, no verdict. The rule travels. The verdict does not.",
    "Trials from 3 sites: Londrina, Floresta and Cafelândia.",
    "Crop-sequence model check (wheat option, water only): {label}. The answer above does not depend on it.",
    "This shows how much the answer depends on NASA HLS. It does not show that the answer is right.",
    "Use paired, repeated strips",
)


@pytest.mark.parametrize("phrase", REQUIRED)
def test_required(phrase):
    assert in_en(phrase, case=False), f"missing in copy_en: {phrase!r}"


def test_required_keys_exist_in_both():
    # The notices sit at fixed keys, so the Portuguese page shows them too.
    keys = (
        "page.footer.supports", "page.answer.caption", "page.answer.per_hectare", "page.footer.not_visible",
        "page.header.persona", "page.answer.transfer_warning", "howweknow.zarc_independence",
        "sheet.independence", "page.strategies.S3.late_soy_gap", "page.header.region",
        "page.header.region_rule", "page.header.trial_sites", "page.answer.model_check",
        "page.nasa_switch.legend",
    )
    for key in keys:
        assert get(EN, key) and get(PT, key), key


# ---- decision.mjs ------------------------------------------------------------

def test_states_match_decision(decision):
    assert list(EN["states"]) == decision["STATES"]
    assert list(PT["states"]) == decision["STATES"]
    assert set(decision["PRECEDENCE"]) == set(decision["STATES"])


def test_messages_match_decision(decision):
    assert set(EN["messages"]) == set(decision["MESSAGES"])
    for key, text in decision["MESSAGES"].items():
        assert EN["messages"][key] == text, key
        assert in_en(text)


def test_field_labels_match_decision(decision):
    assert set(EN["fields"]) == set(decision["FIELD_LABELS"])
    for key, text in decision["FIELD_LABELS"].items():
        assert EN["fields"][key] == text, key


def test_need_field_sentence(decision):
    # needFieldMessage writes "Not enough evidence. We need your {label}." The copy says the same,
    # and the sowing-date case equals MESSAGES.needSowingDate in both languages.
    for key, label in decision["FIELD_LABELS"].items():
        assert EN["answers"]["invalid_field"].format(field=label) == f"Not enough evidence. We need your {label}."
    for copy in (EN, PT):
        assert copy["answers"]["invalid_field"].format(field=copy["fields"]["sowing_date"]) == \
            copy["messages"]["needSowingDate"]
        assert copy["answers"]["need_sowing_date"] == copy["messages"]["needSowingDate"]
        assert copy["answers"]["window_outside"] == copy["messages"]["windowOutside"]


def test_every_code_and_flag_has_copy(decision_source, decision):
    codes = set(re.findall(r"code: '([a-z_]+)'", decision_source))
    flags = set(re.findall(r"flags\.push\('([a-z_]+)'\)", decision_source))
    assert codes and flags
    for copy in (EN, PT):
        assert codes <= set(copy["answers"]), sorted(codes - set(copy["answers"]))
        assert flags <= set(copy["flags"]), sorted(flags - set(copy["flags"]))
        assert decision["ZARC_CONSORTIUM_REASON"] in copy["page"]["limits"]["reasons"]


# The state each result code shows (decision.mjs): the sentence opens with the state name,
# in both languages, so the two pages show the same state (SA-D59).
CODE_STATE = {
    "invalid_field": "not_enough",
    "pays_without_gain": "try_strip",
    "few_trials": "not_enough",
    "counts_try": "try_strip",
    "counts_keep": "keep",
    "counts_tied": "tied",
    "window_outside": "not_enough",
    "need_sowing_date": "not_enough",
    "out_of_range": "not_enough",
    "evidence_unusable": "not_enough",
    "window_unknown": "not_enough",
    "method_missing": "not_enough",
    "both_removed": "keep",
}


@pytest.mark.parametrize("lang", ["en", "pt"])
def test_answer_shows_its_state(lang):
    copy = EN if lang == "en" else PT
    for code, state in CODE_STATE.items():
        sentence = copy["answers"][code]
        assert sentence.startswith(copy["states"][state] + "."), (lang, code)


def test_try_asks_for_paired_strips():
    for code, state in CODE_STATE.items():
        if state == "try_strip":
            assert EN["answers"][code].endswith("Use paired, repeated strips."), code
            assert PT["answers"][code].endswith("Use faixas pareadas e repetidas."), code


# ---- labels and bench texts (pipeline/bench/label.py) -----------------------

def test_labels_match_bench():
    assert list(EN["labels"]) == list(lb.LABEL_IDS)
    for label_id in lb.LABEL_IDS:
        assert EN["labels"][label_id]["title"] == lb.LABEL_TITLE[label_id]
        assert EN["labels"][label_id]["body"] == lb.LABEL_MESSAGE[label_id]
    assert EN["page"]["answer"]["model_check"] == lb.PAGE_PREFIX
    assert EN["howweknow"]["scoreboard"]["status"] == lb.STATUS_TEXT


def test_model_not_validated_names_one_reason():
    # SA-D84: the check against the field trials, without the "or fewer than 10" clause.
    en = EN["labels"]["model_not_validated_here"]["body"]
    pt = PT["labels"]["model_not_validated_here"]["body"]
    assert en.startswith("The model did not pass its check against the field trials. ")
    assert pt.startswith("O modelo não passou na checagem contra os ensaios de campo. ")
    assert " or " not in en.split(". ")[0] and " ou " not in pt.split(". ")[0]


# ---- fixed phrases of the protocol (M0.3, shown on How we know) -------------

def _module_constant(path: Path, name: str):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == name for t in node.targets):
            return ast.literal_eval(node.value)
    raise KeyError(name)


def test_protocol_declarations():
    declarations = _module_constant(TEST_PROTOCOL, "DECLARATIONS")
    assert list(EN["howweknow"]["protocol"]["declarations"]) == list(declarations)
    assert len(PT["howweknow"]["protocol"]["declarations"]) == len(declarations)




def test_protocol_labels(protocol):
    titles = [v["title"].lower() for v in EN["labels"].values()]
    for row in protocol["label"]["order"]:
        assert row["label"].lower() in titles, row["label"]


def test_protocol_decision_states(protocol):
    names = []
    for step in protocol["decision"]["order"]:
        m = re.search(r"(?:gives|otherwise) (.+)$", step)
        assert m, step
        names.append(m.group(1))
    assert set(names) == set(EN["states"].values()) - {EN["states"]["removed"]}


def test_protocol_r1_states(protocol):
    l5 = list(EN["page"]["rows"]["L5"]["states"].values())
    assert len(l5) == len(protocol["r1"]["states"])
    for name in protocol["r1"]["states"]:
        assert any(s == f"Next soybean here: {name}." for s in l5), name


def test_protocol_other_phrases(protocol, protocol_md):
    assert EN["page"]["nasa_switch"]["context_only"] == protocol["tests"]["A3"]["zero_changes"]
    assert EN["page"]["rows"]["L2"]["wheat_zarc"] == protocol["zarc"]["wheat_label"]
    h2 = protocol["tests"]["H2"]["page"]
    assert h2.startswith("says ")
    assert in_en(h2.removeprefix("says "), case=False)
    assert protocol["soil_domain"]["conventional_tillage"] in EN["states"].values()
    assert '"Use with care"' in protocol_md
    assert EN["page"]["answer"]["use_with_care"] == "Use with care"
    assert '"Not enough evidence. We need your {field}"' in protocol_md
    assert EN["answers"]["invalid_field"] == "Not enough evidence. We need your {field}."
    for status in ("not completed", "planned, not run", "context only"):
        assert in_en(status)


def test_declared_limits_come_from_protocol(protocol_md):
    for item in EN["howweknow"]["limits"]["declared"]:
        assert item in protocol_md, item


# ---- plan wording, where the plan fixes it ----------------------------------

PLAN_WORDING = {
    # 7.15
    "answers.pays_without_gain": "Try it on a strip here. {Option} pays on your numbers without any soybean gain. "
                                 "Use paired, repeated strips.",
    "answers.few_trials": "Not enough evidence. On your numbers, {Option} needs {G} bags/ha more soybean next "
                          "season, and we have only {n} trial site-seasons. The rule asks for at least 5.",
    "answers.counts_try": "Try it on a strip here. On your numbers, {Option} needs {G} bags/ha more soybean next "
                          "season. Trials reached that in {N} of {n} site-seasons. Use paired, repeated strips.",
    "answers.counts_keep": "Keep what you do. On your numbers, {Option} needs {G} bags/ha more soybean next "
                           "season. Trials reached that in only {N} of {n} site-seasons.",
    "answers.counts_tied": "Tied. On your numbers, {Option} needs {G} bags/ha more soybean next season. Trials "
                           "reached that in {N} of {n} site-seasons, which does not settle it.",
    "answers.removed": "{Option} is out here: {reason}.",
    "page.field_order.first": "Field {X} first: soybeans left it later than Field {Y} in {N} of {M} seasons, "
                              "to confirm.",
    "page.field_order.none": "No field stands out, use what you know.",
    "flags.date_in_charge": "Your date is in charge on this field.",
    "page.field_order.one_field": "Only one example field is loaded, so there is no field order.",
    # SA-D62 replaced "soybean-phase greenness" with "the first green cycle" (PROTOCOL.md section 11).
    # k counts the outside seasons, so "at the edge" says how few they are (rehearsal).
    "window.at_the_edge": "Satellite estimate: the first green cycle persisted beyond the corn Zarc window "
                          "in {k} of {n} seasons. That is fewer than the rule needs, so the reading stays at the edge.",
    "flags.window_warning": "Satellite estimate: the first green cycle persisted beyond the corn Zarc window "
                            "in {k} of {n} seasons.",
    "flags.check_window_with_bank": "Check your sowing date against the Zarc window with your bank.",
    "page.strategies.S2.fixed": "Not simulated. Corn is shown as in S1 because the trial recorded a very small "
                                "difference in corn.",
    "page.answer.caption": "Counts of trials from 3 sites, not your chance. Not a forecast for your field.",
    "page.answer.per_hectare": "All numbers per hectare.",
    "page.answer.bag": "Bag = 60 kg.",
    # 7.6, 7.7, 7.8
    "page.year_strip.footer": "Same calendar in all three rows. The dots are this field, for reference only.",
    "page.ribbon.legend": "No green does not mean bare soil. NDVI cannot separate straw from soil.",
    "page.ribbon.two_cycles": "second green cycle seen, only the first is read",  # D12: never "to confirm"
    "page.nasa_switch.changed": "Turning this off changes {X} of {Y} field and option states.",
    "page.nasa_switch.date_in_charge": "Your date is in charge, so the satellite warning does not change the "
                                       "answer on this field.",
    "page.nasa_switch.on_your_numbers": "On your numbers.",
    # 7.13, 12.2, 12.4, 12.5
    "risk.simulated": "Simulated with the climate of 1984 to 2025: {N} of {M} seasons (about {K} in 10)",
    "risk.power": "POWER reanalysis, regional cell, 1984 to 2025: {N} of {M} years (about {K} in 10)",
    "risk.regional": "{N} of {M} years, regional",
    "risk.trials": "Trials reached that in {N} of {n} site-seasons",
    "risk.date_bar": "Last clean look {d1}, next clean look {d2}",
    "risk.inside_model": "Inside the model only",
    "risk.parameters": "Parameters: same direction in about {K} in 10 model variations. This is not a chance "
                       "of success.",
    "page.rows.L5.stable_note": "Stable in the model does not mean it will work on your field.",
    # 7.2
    "page.question.lead": "Before you buy seed and fertilizer for the second crop:",
    "page.question.main": "On this field, after this soybean, what comes next?",
    "page.question.options": "Keep corn, add grass to the corn, or test wheat on part of the area?",
    "page.rows.soil.line": "Soil used: {class}, estimated. Ask for your soil test.",
    "page.rows.soil_note": "Rotation helps hold soil. It does not replace terraces or stop runoff.",
    "page.actions.sheet": "Take this to my agronomist",
    # 3, 5.6, 7.10, 11.6
    "page.tagline": "Aftercrop helps a farmer decide what follows soybeans on one field, and says so when the "
                    "evidence is not enough.",
    "howweknow.region.next": "Next candidate region: Iowa, where a replicated rotation trial exists but no "
                             "regional pack is built yet.",
    "howweknow.scoreboard.title": "Exploratory trial check",
    "howweknow.water_note": "Water at sowing: simulated, not independently checked.",
    # 7.11
    "glossary.terms.ndvi.definition": "A greenness index from red and near-infrared light. Low values do not say "
                                      "if the ground is bare or under straw.",
    "glossary.terms.evapotranspiration.definition": "Water that leaves the field as vapor, from the soil and "
                                                    "through plants.",
    "glossary.terms.soil_moisture_anomaly.definition": "How much wetter or drier the soil is than usual for the "
                                                       "season. Satellite values cover about the top 5 cm over "
                                                       "tens of kilometers.",
    "glossary.terms.frost_risk.definition": "Chance of cold that harms the crop. Here it is a regional alert from "
                                            "minimum temperature, not a field forecast.",
    "glossary.terms.water_balance.definition": "Rain and stored water in, minus runoff, drainage, evaporation and "
                                               "plant use.",
}

SEALS = ("Seen by satellite", "Estimated from satellite", "Reanalysis", "Simulated", "Measured in trials",
         "Derived from trials", "Official rule", "Published climatology", "Your numbers", "Unknown")


@pytest.mark.parametrize("key", list(PLAN_WORDING))
def test_plan_wording(key):
    assert get(EN, key) == PLAN_WORDING[key]


def test_seals():
    assert tuple(EN["seals"]["names"].values()) == SEALS
    assert set(EN["seals"]["names"]) == set(EN["seals"]["help"])


# ---- Portuguese --------------------------------------------------------------

# Words the plan's sheet block (7.12) writes without accents. None may reach the Portuguese copy.
UNACCENTED = (
    "nao", "voce", "agronomo", "ficticio", "ficticia", "talhao", "talhoes", "regiao", "opcao", "opcoes",
    "evidencia", "satelite", "precos", "raizes", "nitrogenio", "doenca", "revisao", "terraco", "unica",
    "agua", "proxima", "decendio", "area", "ate", "ja", "tambem", "analise", "pratica", "possivel",
    "historico", "especifico", "hidrico", "balanco", "parametros", "licencas", "glossario", "beneficios",
)


def test_portuguese_accents():
    pattern = re.compile(r"\b(" + "|".join(UNACCENTED) + r")\b", re.IGNORECASE)
    hits = []
    for key, value in PT_LEAVES.items():
        m = pattern.search(PLACEHOLDER.sub("", value))  # {area} is a placeholder, not a word
        if m:
            hits.append((key, m.group(0)))
    assert not hits, f"unaccented Portuguese in {hits}"


def test_place_names_keep_their_accents():
    for lang, key, value in BOTH:
        assert not re.search(r"\bParana\b|\bCafelandia\b", value), (lang, key)


def test_sheet_from_plan():
    sheet = PT["sheet"]
    assert sheet["independence"] == (
        "Referência pública: Zarc/MAPA, safra 2026/27. Esta ferramenta é independente: não foi avaliada "
        "pelo MAPA nem pela Embrapa, não define acesso a seguro ou crédito e não classifica nível de manejo.")
    assert PT["howweknow"]["zarc_independence"] == sheet["independence"]
    assert EN["howweknow"]["zarc_independence"] == EN["sheet"]["independence"]
    assert sheet["single_strip"] == (
        "Faixa única não estima efeito. Esta folha apoia, não substitui o julgamento local.")
    assert "1 alqueire = 2,42 ha" in sheet["items"]["assumptions"]
    assert "1 alqueire = 2.42 ha" in EN["sheet"]["items"]["assumptions"]
    assert "sc/ha" in sheet["items"]["assumptions"] and "sc/alqueire" in sheet["items"]["assumptions"]
    assert sheet["items"]["strip_test"].startswith("Teste em faixas pareadas e repetidas")
    assert sheet["alerts"]["blast_and_harvest_rain"] == "brusone e chuva na colheita"
    assert "revisão local" in sheet["unaudited"] and not re.search(r"\d", sheet["unaudited"])
    assert "Não houve revisão local" in sheet["no_local_review"]
    assert list(sheet["items"]) == ["option", "window", "satellite", "assumptions", "evidence",
                                    "unknowns", "local_review", "strip_test"]


# ---- ids of pipeline/decision/strategies.py ---------------------------------

def test_ids_of_the_strategy_menu():
    """The page looks copy up by the ids of the menu (region.json): every rule, constraint and
    alert id has its text here, in both languages. Skipped while that module does not exist."""
    menu = pytest.importorskip("pipeline.decision.strategies")
    for copy in (EN, PT):
        limits = copy["page"]["limits"]
        removing = {c["id"] for c in menu.CONSTRAINTS if c["removes"]}
        assert removing | {menu.HARD_RULE["id"]} <= set(limits["reasons"])
        assert {c["id"] for c in menu.CONSTRAINTS} <= set(limits)
        assert {r["id"] for r in menu.SOFT_RULES} == set(limits["soft"]) - {"note"}
        assert {a["id"] for a in menu.ALERTS} == set(copy["sheet"]["alerts"])
        assert set(menu.STRATEGY_IDS) == set(copy["page"]["strategies"])
        for sid, strategy in menu.STRATEGIES.items():
            if copy is EN:
                assert copy["page"]["strategies"][sid]["name"] == strategy.name, sid

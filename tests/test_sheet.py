# Written with an SI tool (Claude, Anthropic) for NASA Space Apps 2026. See AI_USE.md.
"""The agronomist sheet (plan 7.12, sheet M8, criteria M8.1 to M8.5).

site/sheet.html, site/js/sheet.mjs and site/css/sheet.css print one A4 page in the
page language (English by default, Portuguese with ?lang=pt or lang=pt in the fragment,
P2.3) from the page state in the URL fragment (app.mjs sheetHash), with no server.
Checked here:

- static: no visible text in the HTML, no user-facing text in the code (every word
  comes from config/copy_*.yaml), no network call but the two static data files,
  no browser storage, print CSS for A4;
- M8.1 content, M8.3 units, M8.4 (test_review) and M8.5 (test_h2), by rendering the
  example state with the real copy through Node;
- M8.2 A4: headless Chrome prints the sheet, pdfinfo reads the page size and count.
  Skipped when Chrome or pdfinfo is absent.

tests/sheet.test.mjs covers the module itself (fragment, every field, alqueire,
missing values, agreement with decision.mjs and with app.mjs).

Also a script, for `make sheet-pdf` (the fictitious example of the M5 fixtures), which
writes build/sheet_en.pdf and build/sheet_pt.pdf:
    .venv/bin/python tests/test_sheet.py --pdf build/sheet.pdf --lang both
"""
from __future__ import annotations

import argparse
import functools
import html.parser
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SITE = ROOT / "site"
SHEET_HTML = SITE / "sheet.html"
SHEET_JS = SITE / "js" / "sheet.mjs"
SHEET_CSS = SITE / "css" / "sheet.css"
DECISION_JS = SITE / "js" / "decision.mjs"
APP_JS = SITE / "js" / "app.mjs"
FIXTURE = ROOT / "tests" / "fixtures" / "sheet_example_state.json"
EXAMPLE_DATA = ROOT / "tests" / "fixtures" / "decision_example"
SCOREBOARD_FIXTURE = ROOT / "tests" / "fixtures" / "scoreboard_example.json"
REGION_YAML = ROOT / "config" / "region_norte_pr.yaml"
COPY_PATHS = {"en": ROOT / "config" / "copy_en.yaml", "pt": ROOT / "config" / "copy_pt.yaml"}

SVG_NS = "http://www.w3.org/2000/svg"
A4_PTS = (595.28, 841.89)


# ---- helpers -----------------------------------------------------------------

def copy_doc() -> dict:
    """site/data/copy.json as pipeline/build/copy_json.py builds it from the YAML files now."""
    try:
        from pipeline.build.copy_json import build_copy
    except ImportError:
        return {"langs": {lang: yaml.safe_load(p.read_text(encoding="utf-8")) for lang, p in COPY_PATHS.items()}}
    return build_copy()


@functools.lru_cache(maxsize=1)
def _copy_json() -> str:
    return json.dumps(copy_doc(), ensure_ascii=False)


@functools.lru_cache(maxsize=1)
def copy_pt() -> dict:
    return json.loads(_copy_json())["langs"]["pt"]


@functools.lru_cache(maxsize=1)
def copy_en() -> dict:
    return json.loads(_copy_json())["langs"]["en"]


def example() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


RENDER = """
const S = await import(process.argv[1]);
let data = '';
for await (const chunk of process.stdin) data += chunk;
const req = JSON.parse(data);
const copy = S.pickCopy(req.copy, req.lang);
const [y, m, d] = req.now.split('-').map(Number);
const model = S.buildSheet(req.state, copy, { now: new Date(y, m - 1, d), scoreboard: req.scoreboard });
console.log(JSON.stringify({ text: S.sheetText(model), seals: S.sheetText(model, { seals: true }), model }));
"""


def render(state: dict | None = None, *, scoreboard=None, lang: str = "pt", now: str = "2026-11-15") -> dict:
    """The sheet as sheet.mjs builds it, with the real copy: { text, seals, model }."""
    node = shutil.which("node")
    if node is None:
        pytest.fail("node is needed to render site/js/sheet.mjs (make test-js needs it too)")
    req = {"copy": json.loads(_copy_json()), "lang": lang, "now": now,
           "state": example()["state"] if state is None else state, "scoreboard": scoreboard}
    out = subprocess.run([node, "--input-type=module", "-e", RENDER, SHEET_JS.as_uri()],
                         input=json.dumps(req, ensure_ascii=False), capture_output=True, text=True,
                         timeout=60, check=True)
    return json.loads(out.stdout)


def item(model: dict, key: str) -> str:
    it = next(i for i in model["items"] if i["key"] == key)
    lines = []
    for ln in it["lines"]:
        body = "".join(s["text"] for s in ln["segments"])
        lines.append(f"{ln['label']}{ln.get('sep', ': ')}{body}" if ln["label"] else body)
    return "\n".join(lines)


class _Html(html.parser.HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack: list[str] = []
        self.tags: list[tuple[str, dict]] = []
        self.visible: list[str] = []
        self.scripts: list[str] = []
        self.title = ""

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))
        if tag not in ("meta", "link", "br", "img", "input"):
            self.stack.append(tag)

    def handle_endtag(self, tag):
        while self.stack:
            if self.stack.pop() == tag:
                break

    def handle_data(self, data):
        where = self.stack[-1] if self.stack else ""
        if where == "script":
            self.scripts.append(data)
        elif where == "title":
            self.title += data
        elif where not in ("style",) and "body" in self.stack:
            self.visible.append(data)


def parse_html() -> _Html:
    p = _Html()
    p.feed(SHEET_HTML.read_text(encoding="utf-8"))
    return p


def strip_js_comments(src: str) -> str:
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return re.sub(r"(?m)^\s*//.*$|\s//\s.*$", "", src)


# ---- static ------------------------------------------------------------------

def test_files_exist():
    for p in (SHEET_HTML, SHEET_JS, SHEET_CSS, DECISION_JS, APP_JS, FIXTURE):
        assert p.is_file(), p


def test_example_is_fictitious():
    assert example()["label"] == "example, fictitious"
    assert example()["_note"].lower().startswith("example, fictitious")
    assert (ROOT / "tests" / "fixtures" / example()["data_dir"]) == EXAMPLE_DATA
    trials = json.loads((EXAMPLE_DATA / "trials_steps.json").read_text(encoding="utf-8"))
    assert trials["rights_note"].lower().startswith("example, fictitious")


def test_html_has_no_visible_text_and_loads_only_local_files():
    p = parse_html()
    assert "".join(p.visible).strip() == "", "every visible word comes from the copy"
    assert p.title.strip() == copy_en()["page"]["title"]  # the product name, until sheet.mjs sets sheet.title
    html_tag = next(a for t, a in p.tags if t == "html")
    assert html_tag.get("lang") == "en"  # English by default, like the page (P2.3)
    lang_link = next(a for t, a in p.tags if t == "a")
    assert lang_link.get("id") == "lang" and "hidden" in lang_link and "href" not in lang_link
    links = [a for t, a in p.tags if t == "link" and a.get("rel") == "stylesheet"]
    assert [a["href"] for a in links] == ["css/sheet.css"]
    assert any(t == "meta" and a.get("name") == "referrer" and a.get("content") == "no-referrer" for t, a in p.tags)
    for tag, attrs in p.tags:
        for name in ("src", "href", "action"):
            v = attrs.get(name)
            assert v is None or not re.match(r"^(?:[a-z]+:)?//", v, re.I), f"external {tag} {name}={v}"
    modules = [a for t, a in p.tags if t == "script"]
    assert modules and all(a.get("type") == "module" for a in modules)
    assert any("from './js/sheet.mjs'" in s and "mount()" in s for s in p.scripts)
    assert not any(t in ("form", "input", "iframe", "img") for t, _ in p.tags)


def test_english_sheet():
    """P2.3: the same sheet in English, every item, English dates and units."""
    out = render(lang="en")
    text, en = out["text"], copy_en()
    assert out["model"]["lang"] == "en"
    assert text.splitlines()[0] == f"{en['sheet']['title']}   {en['sheet']['fictitious']}"
    assert f"Option to review: {en['page']['strategies']['S3']['name']} ({en['states']['try_strip']})" in text
    assert "Zarc window: 21 Dec 2026 to 28 Feb 2027. Declared date: 10 Feb 2027" in text
    assert "G: 2 bags/ha, or 4.9 bags/alqueire" in item(out["model"], "assumptions")
    assert "(1 alqueire = 2.42 ha)" in text
    assert "Evidence: 9 of 10 site-seasons, 3 sites" in text
    assert en["sheet"]["single_strip"] in text and en["sheet"]["no_local_review"] in text
    assert en["sheet"]["unaudited"] in text and en["sheet"]["independence"] in text
    assert re.search(r"[\u2014\u2013\u2012\u2015;\uff1b]", out["seals"]) is None
    for word in ("Talhão", "Gerada", "sc/ha", "falta"):
        assert word not in text, word
    empty = render({}, lang="en")["text"]
    assert f"Field {en['sheet']['date_missing']}" in empty


def test_no_user_facing_text_in_the_code():
    """No Portuguese or English copy lives in sheet.html, sheet.mjs or sheet.css."""
    for path in (SHEET_HTML, SHEET_JS, SHEET_CSS):
        src = path.read_text(encoding="utf-8")
        non_ascii = sorted({c for c in src if ord(c) > 127})
        assert not non_ascii, f"{path.name}: non-ASCII {non_ascii}, so maybe text outside the copy"
    code = strip_js_comments(SHEET_JS.read_text(encoding="utf-8")) + SHEET_HTML.read_text(encoding="utf-8")
    doc = json.loads(_copy_json())
    for lang, tree in doc["langs"].items():
        for key, value in _leaves(tree):
            # Phrases only: one-word values like "exploratory" are also status ids in the code.
            if isinstance(value, str) and len(value) >= 8 and " " in value.strip():
                assert value not in code, f"copy_{lang}.yaml {key} is hard-coded"


def _leaves(node, path=()):
    if isinstance(node, dict):
        for k, v in node.items():
            yield from _leaves(v, path + (str(k),))
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from _leaves(v, path + (str(i),))
    else:
        yield ".".join(path), node


def test_state_never_leaves_the_browser():
    src = SHEET_JS.read_text(encoding="utf-8")
    code = strip_js_comments(src)
    for word in ("localStorage", "sessionStorage", "indexedDB", "sendBeacon", "XMLHttpRequest", "WebSocket",
                 "EventSource", "innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(",
                 "new Function", "history.pushState", "postMessage"):
        assert word not in code, word
    urls = re.findall(r"""['"`]((?:[a-z]+:)?//[^'"`]*)['"`]""", code, re.I)
    assert set(urls) <= {SVG_NS}, urls
    assert re.findall(r"\bfetch\(", code) == ["fetch("], "one fetch helper"
    assert "credentials: 'omit'" in code
    # The files and folder of the page (app.mjs loadAll and dataBase), nothing of the state in the URL.
    assert re.search(r"loadAll\(\(url\) => fetchOrNull\(win, url\), dataBase\(win\.location\.search\)\)", code)
    assert "location.hash" in code, "the state comes from the fragment"
    app = APP_JS.read_text(encoding="utf-8")
    assert "export function dataBase" in app and "export async function loadAll" in app


def test_print_css_is_a4():
    css = SHEET_CSS.read_text(encoding="utf-8")
    page = re.search(r"@page\s*\{([^}]*)\}", css)
    assert page and re.search(r"size:\s*A4\b", page.group(1)), "@page size A4"
    assert re.search(r"margin:\s*\d", page.group(1))
    printing = css[css.index("@media print"):]
    assert re.search(r"\.toolbar[^{]*\{[^}]*display:\s*none", printing), "no button on paper"
    for bad in ("url(", "@import", "@font-face", "http:", "https:"):
        assert bad not in css, bad
    assert "print-color-adjust: exact" in css, "seal colors print (7.14)"


# ---- content (M8.1), units (M8.3), review (M8.4), H2 (M8.5) -------------------

def test_content():
    """M8.1: option, window, assumptions, local review items and paired, repeated strips,
    with the sentence that a single strip does not estimate an effect."""
    out = render()
    model, text = out["model"], out["text"]
    sheet = copy_pt()["sheet"]
    pt = copy_pt()
    assert [i["key"] for i in model["items"]] == ["option", "window", "assumptions", "evidence", "unknowns",
                                                  "local_review", "strip_test"]
    assert text.splitlines()[0] == f"{sheet['title']}   {sheet['fictitious']}"
    assert f"Opção para revisar: {pt['page']['strategies']['S3']['name']} ({pt['states']['try_strip']})" in text
    assert "Janela do Zarc: 21/12/2026" in text and "Data declarada: 10/02/2027" in text
    assert "Satélite: o primeiro ciclo verde passou da janela em 1 de 7 safras" in text
    assert sheet["items"]["assumptions"] in text
    assert "Evidência: 9 de 10 locais-safra, 3 locais, contagem, não chance" in text
    assert sheet["items"]["unknowns"] in text
    assert item(model, "local_review").startswith("Revisão local: ")
    assert "laudo de solo, terraço" in item(model, "local_review")
    strips = item(model, "strip_test")
    assert strips.startswith("Teste em faixas pareadas e repetidas: 12,5 ha, pares a definir aqui")
    assert "Faixa única não estima efeito." in strips
    assert sheet["single_strip"] in strips and sheet["strip_design"] in strips
    assert sheet["independence"] in text
    assert re.search(r"[—–‒―;；]", out["seals"]) is None


def test_units():
    """M8.3: sc/ha and sacks per alqueire, with 2.42 ha declared."""
    out = render()
    a = item(out["model"], "assumptions")
    assert "(1 alqueire = 2,42 ha)" in a
    assert "G: 2 sc/ha, ou 4,9 sc/alqueire" in a  # G = 1.35 sc/ha, step 2 (SA-D91), 2 x 2.42 = 4.84, rounded up
    src = SHEET_JS.read_text(encoding="utf-8")
    assert "export const ALQUEIRE_HA = 2.42;" in src


def test_missing_values_are_never_numbers():
    out = render({})
    missing = copy_pt()["sheet"]["date_missing"]
    text = out["text"]
    assert f"Talhão {missing}" in text
    assert f"Janela do Zarc: {missing}. Data declarada: {missing}" in text
    assert f"G: {missing} sc/ha, ou {missing} sc/alqueire" in text
    copy_digits = {d for _, v in _leaves(copy_pt()) if isinstance(v, str) for d in re.findall(r"\d+", v)}
    allowed = copy_digits | {"1", "2", "3", "4", "5", "6", "7", "15", "11", "2026"}
    assert set(re.findall(r"\d+", text)) <= allowed


def test_review():
    """M8.4: blast and rain at harvest appear as local review, with no number."""
    sheet = copy_pt()["sheet"]
    for state in (example()["state"], {**example()["state"], "alerts": ["pratylenchus"], "option": "S2"}, {}):
        out = render(state)
        review = item(out["model"], "local_review")
        assert sheet["unaudited"] in review
        line = next(ln for ln in review.splitlines() if "Brusone" in ln)
        assert "revisão local" in line and not re.search(r"\d", line)
    listed = item(render()["model"], "local_review").splitlines()[0]
    assert sheet["alerts"]["blast_and_harvest_rain"] in listed


def test_alerts_agree_with_the_strategy_menu():
    """sheet.mjs OPTION_ALERTS is the risk_alerts of pipeline/decision/strategies.py (13.5)."""
    menu = pytest.importorskip("pipeline.decision.strategies")
    node = shutil.which("node")
    script = "const S = await import(process.argv[1]); console.log(JSON.stringify(S.OPTION_ALERTS));"
    out = subprocess.run([node, "--input-type=module", "-e", script, SHEET_JS.as_uri()],
                         capture_output=True, text=True, timeout=60, check=True)
    js = json.loads(out.stdout)
    assert js == {sid: list(st.risk_alerts) for sid, st in menu.STRATEGIES.items()}
    assert {a for ids in js.values() for a in ids} <= set(copy_pt()["sheet"]["alerts"])


def test_h2():
    """M8.5: without H2 done, the sheet and How we know say there was no local review."""
    protocol = yaml.safe_load(REGION_YAML.read_text(encoding="utf-8"))["protocol"]
    assert protocol["tests"]["H2"]["status"] == "not_run"  # SA-D56
    pt = copy_pt()
    en = json.loads(_copy_json())["langs"]["en"]
    scoreboard = json.loads(SCOREBOARD_FIXTURE.read_text(encoding="utf-8"))
    h2 = next(t for t in scoreboard["scoreboard"]["tests"] if t["id"] == "H2")
    assert h2["status"] == "not_run"
    for sb in (None, scoreboard, scoreboard["scoreboard"]):
        out = render(scoreboard=sb)
        assert [c["key"] for c in out["model"]["closing"]][0] == "no_local_review"
        assert pt["sheet"]["no_local_review"] in out["text"]
    assert pt["sheet"]["no_local_review"].startswith("Não houve revisão local")
    assert pt["howweknow"]["no_local_review"].startswith("Não houve revisão local")
    assert "no local review" in en["sheet"]["no_local_review"]
    assert "no local review" in en["howweknow"]["no_local_review"]
    # Only a scoreboard with H2 done drops the sentence: pass, fail or reported with a literal
    # comment (app.mjs localReviewDone, shared with How we know). Nothing in the fragment can.
    done = render(scoreboard={"tests": [{"id": "H2", "status": "reported", "note": "Read in a personal capacity."}]})
    assert pt["sheet"]["no_local_review"] not in done["text"]
    for entry in ({"status": "reported"}, {"status": "pass", "note": " "}, {"status": "exploratory", "note": "x"}):
        out = render(scoreboard={"tests": [{"id": "H2", **entry}]})
        assert pt["sheet"]["no_local_review"] in out["text"], entry
    forged = render({**example()["state"], "h2": "pass", "localReview": True})
    assert pt["sheet"]["no_local_review"] in forged["text"]


def test_h2_how_we_know_page():
    """M8.5 on the How we know page, once it exists: it shows howweknow.no_local_review."""
    page = SITE / "how-we-know.html"
    if not page.exists():
        pytest.skip("site/how-we-know.html not built yet")
    sources = [page.read_text(encoding="utf-8")] + [
        p.read_text(encoding="utf-8") for p in (SITE / "js").glob("*.mjs") if p.name != SHEET_JS.name]
    assert any("no_local_review" in s for s in sources), "How we know must show howweknow.no_local_review"


# ---- M8.2: A4 for real -------------------------------------------------------

def chrome() -> str | None:
    env = os.environ.get("CHROME")
    if env and Path(env).exists():
        return env
    mac = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
    if mac.exists():
        return str(mac)
    for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "chrome"):
        if shutil.which(name):
            return shutil.which(name)
    return None


def page_hash(ui: dict | None) -> str:
    """The fragment app.mjs sheetHash() writes for a UI state. None gives an empty fragment."""
    if ui is None:
        return ""
    node = shutil.which("node")
    script = ("const A = await import(process.argv[1]);"
              "let d = ''; for await (const c of process.stdin) d += c;"
              "process.stdout.write('#' + A.sheetHash(JSON.parse(d)));")
    out = subprocess.run([node, "--input-type=module", "-e", script, APP_JS.as_uri()],
                         input=json.dumps(ui, ensure_ascii=False), capture_output=True, text=True,
                         timeout=60, check=True)
    return out.stdout


def build_pdf(out: Path, ui: dict | None | str = "example", *, data_dir: Path = EXAMPLE_DATA,
              browser: str | None = None, lang: str | None = None) -> Path:
    """Prints sheet.html to `out` with headless Chrome, served over HTTP from a temporary copy of
    the site code, the JSON files of `data_dir` (default: the fictitious M5 example) and
    data/copy.json built from the YAML files now. `ui` is a page UI state for app.mjs sheetHash,
    "example" for the fixture one, or None for no fragment. `lang` ("en" or "pt") goes in
    ?lang=, which wins over the lang of the fragment. None leaves the sheet to choose."""
    browser = browser or chrome()
    if browser is None:
        raise RuntimeError("no Chrome or Chromium found (set CHROME)")
    if ui == "example":
        ui = example()["page"]
    out = Path(out).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="aftercrop-sheet-") as tmp:
        root = Path(tmp) / "site"
        (root / "css").mkdir(parents=True)
        (root / "js").mkdir()
        (root / "data").mkdir()
        shutil.copy2(SHEET_HTML, root / "sheet.html")
        shutil.copy2(SHEET_CSS, root / "css" / "sheet.css")
        for src in (SITE / "js").glob("*.mjs"):
            shutil.copy2(src, root / "js" / src.name)
        for src in Path(data_dir).glob("*.json"):
            shutil.copy2(src, root / "data" / src.name)
        (root / "data" / "copy.json").write_text(_copy_json(), encoding="utf-8")

        class Quiet(SimpleHTTPRequestHandler):
            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Quiet, directory=str(root)))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            query = f"?lang={lang}" if lang in ("en", "pt") else ""
            url = f"http://127.0.0.1:{server.server_address[1]}/sheet.html{query}{page_hash(ui)}"
            if out.exists():
                out.unlink()
            subprocess.run(
                [browser, "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
                 f"--user-data-dir={Path(tmp) / 'profile'}", "--no-pdf-header-footer",
                 "--virtual-time-budget=8000", f"--print-to-pdf={out}", url],
                capture_output=True, text=True, timeout=120, check=False)
        finally:
            server.shutdown()
            server.server_close()
    if not out.exists() or out.stat().st_size == 0:
        raise RuntimeError("Chrome wrote no PDF")
    return out


def pdf_info(path: Path) -> dict:
    out = subprocess.run(["pdfinfo", str(path)], capture_output=True, text=True, timeout=60, check=True).stdout
    pages = int(re.search(r"^Pages:\s+(\d+)", out, re.M).group(1))
    w, h = map(float, re.search(r"^Page size:\s+([\d.]+) x ([\d.]+) pts", out, re.M).groups())
    return {"pages": pages, "width": w, "height": h}


def longest_ui() -> dict:
    """The example page with both wheat limits answered no, insurance tied to the Zarc and an
    impossible date: S3 is out for two reasons, the longest sheet."""
    ui = json.loads(json.dumps(example()["page"]))
    ui["limits"].update(no_wheat_buyer="no", no_narrow_row_seeder="no", zarc_insurance_credit="yes")
    ui["sowingDate"] = "2027-02-30"
    return ui


def test_a4(tmp_path):
    """M8.2: A4 page, at most 2 pages, with the text drawn (not a blank page)."""
    if chrome() is None or shutil.which("pdfinfo") is None:
        pytest.skip("needs Chrome or Chromium and pdfinfo (poppler)")
    pt, en = copy_pt(), copy_en()
    # The example fixture page is in Portuguese: no ?lang= keeps it, ?lang=en turns it English.
    # Without a fragment the sheet is English, the page default.
    cases = (("example", "example", None, "pt"), ("empty", None, None, "en"), ("longest", longest_ui(), None, "pt"),
             ("example_en", "example", "en", "en"), ("longest_en", longest_ui(), "en", "en"))
    for name, ui, lang, expect in cases:
        pdf = build_pdf(tmp_path / f"sheet_{name}.pdf", ui, lang=lang)
        info = pdf_info(pdf)
        assert abs(info["width"] - A4_PTS[0]) < 2 and abs(info["height"] - A4_PTS[1]) < 2, (name, info)
        assert 1 <= info["pages"] <= 2, (name, info)
        if shutil.which("pdftotext"):
            text = subprocess.run(["pdftotext", str(pdf), "-"], capture_output=True, text=True, timeout=60,
                                  check=True).stdout
            if expect == "pt":
                assert pt["sheet"]["title"] in text, name
                assert "Faixa única não estima efeito." in text, name
                assert "Não houve revisão local." in text, name
                if name == "example":
                    assert "2 sc/ha" in text and "4,9" in text and "9 de 10" in text, "the state was read"
            else:
                assert en["sheet"]["title"] in text, name
                assert "A single strip does not estimate an effect." in text, name
                assert "There was no local review." in text, name
                assert "Talhão" not in text, name
                if name == "example_en":
                    assert "2 bags/ha" in text and "4.9" in text and "9 of 10" in text, "the state was read"
    for built in (ROOT / "build" / "sheet_en.pdf", ROOT / "build" / "sheet_pt.pdf"):
        if built.exists():
            info = pdf_info(built)
            assert abs(info["width"] - A4_PTS[0]) < 2 and abs(info["height"] - A4_PTS[1]) < 2
            assert 1 <= info["pages"] <= 2


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Print the example agronomist sheet to an A4 PDF (make sheet-pdf).")
    ap.add_argument("--pdf", type=Path, default=ROOT / "build" / "sheet.pdf")
    ap.add_argument("--ui", type=Path, help="JSON file with a page UI state (default: the fictitious example)")
    ap.add_argument("--data", type=Path, default=EXAMPLE_DATA, help="folder with region.json, field_*.json, ...")
    ap.add_argument("--lang", choices=("en", "pt", "both"), default="both",
                    help="sheet language. both writes <stem>_en.pdf and <stem>_pt.pdf next to --pdf")
    args = ap.parse_args(argv)
    ui = json.loads(args.ui.read_text(encoding="utf-8")) if args.ui else "example"
    langs = ("en", "pt") if args.lang == "both" else (args.lang,)
    for lang in langs:
        path = args.pdf.with_name(f"{args.pdf.stem}_{lang}{args.pdf.suffix}") if args.lang == "both" else args.pdf
        out = build_pdf(path, ui, data_dir=args.data, lang=lang)
        info = pdf_info(out) if shutil.which("pdfinfo") else None
        print(f"sheet ({lang}): wrote {out}"
              + (f", {info['pages']} page(s), {info['width']:.0f} x {info['height']:.0f} pts" if info else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

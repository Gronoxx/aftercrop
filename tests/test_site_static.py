# Written with an SI tool (Claude, Anthropic) for NASA Space Apps 2026. See AI_USE.md.
"""Static checks of the site (sheet M7: M7.1, M7.8, M7.10 and SA-D59).

Nothing here runs a browser. tests/site.test.mjs checks the page logic in Node.

Checked here:
- no external URL at runtime: every src, href, import, fetch and url() in the site's own HTML,
  CSS and JS is relative, and the vendored files fetch nothing absolute;
- no hard-coded user-facing text in the HTML: visible text comes from data-copy keys or from
  Alpine bindings, and every data-copy key exists in both copy files;
- site/data/copy.json is current with config/copy_*.yaml (pipeline/build/copy_json.py);
- vendored files match the sha256 recorded in site/vendor/VENDOR.json, with their license;
- section order of the main page, answer before any chart;
- hosting limits: no file above 25 MiB.
"""
from __future__ import annotations

import hashlib
import json
import re
from html.parser import HTMLParser
from pathlib import Path

import pytest
import yaml

from pipeline.build import copy_json

ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "site"
PAGES = [SITE / "index.html", SITE / "how-we-know.html"]
OWN_JS = [SITE / "js" / n for n in ("app.mjs", "svg.mjs", "ledger.mjs")]
OWN_CSS = [SITE / "css" / "site.css"]
VENDOR = SITE / "vendor"
EN = yaml.safe_load((ROOT / "config" / "copy_en.yaml").read_text(encoding="utf-8"))
PT = yaml.safe_load((ROOT / "config" / "copy_pt.yaml").read_text(encoding="utf-8"))

ABSOLUTE = re.compile(r"^(?:[a-z][a-z0-9+.-]*:|//)", re.IGNORECASE)


def lookup(tree, dotted: str):
    node = tree
    for part in dotted.split("."):
        if isinstance(node, list):
            node = node[int(part)] if part.isdigit() and int(part) < len(node) else None
        elif isinstance(node, dict):
            node = node.get(part)
        else:
            return None
    return node


class Page(HTMLParser):
    """Attributes and visible text of one page, with the element stack."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[str] = []
        self.attrs: list[tuple[str, dict[str, str | None]]] = []
        self.text: list[tuple[str, str]] = []
        self.ids: list[str] = []

    def handle_starttag(self, tag, attrs):
        d = dict(attrs)
        self.attrs.append((tag, d))
        if d.get("id"):
            self.ids.append(d["id"])
        if tag not in {"meta", "link", "br", "img", "input", "hr", "source"}:
            self.stack.append(tag)

    def handle_startendtag(self, tag, attrs):
        d = dict(attrs)
        self.attrs.append((tag, d))
        if d.get("id"):
            self.ids.append(d["id"])

    def handle_endtag(self, tag):
        while self.stack:
            if self.stack.pop() == tag:
                break

    def handle_data(self, data):
        if data.strip():
            self.text.append((self.stack[-1] if self.stack else "", data.strip()))


def parse(path: Path) -> Page:
    p = Page()
    p.feed(path.read_text(encoding="utf-8"))
    return p


# ---- M7.1: no live API, no CDN --------------------------------------------------------

@pytest.mark.parametrize("page", PAGES, ids=lambda p: p.name)
def test_html_references_are_relative(page):
    p = parse(page)
    bad = []
    for tag, attrs in p.attrs:
        for key in ("src", "href", "action", "poster", "data"):
            v = attrs.get(key)
            if v and (ABSOLUTE.match(v) or v.startswith("/")):
                bad.append((tag, key, v))
    assert not bad, f"absolute references in {page.name}: {bad}"
    assert not re.search(r"https?://", page.read_text(encoding="utf-8")), f"URL in {page.name}"


@pytest.mark.parametrize("path", OWN_JS + OWN_CSS, ids=lambda p: p.name)
def test_own_code_holds_no_url(path):
    src = path.read_text(encoding="utf-8")
    assert not re.search(r"https?://", src), f"URL in {path.name}"
    for m in re.finditer(r"""(?:from\s+|import\(\s*|fetch\(\s*|url\(\s*)['"]([^'"]+)['"]""", src):
        target = m.group(1)
        assert not ABSOLUTE.match(target) and not target.startswith("/"), f"{path.name}: {target}"


def test_vendored_code_fetches_nothing_absolute():
    for f in VENDOR.glob("*.js"):
        src = f.read_text(encoding="utf-8")
        assert not re.search(r"""(?:fetch|import)\(\s*['"`]https?:""", src), f.name
        assert not re.search(r"""\bfrom\s*['"]https?:""", src), f.name


# ---- SA-D59: every text from the copy files ---------------------------------------------

@pytest.mark.parametrize("page", PAGES, ids=lambda p: p.name)
def test_no_hard_coded_text(page):
    p = parse(page)
    # Only the product name in <title>, replaced at runtime from the copy.
    visible = [(tag, t) for tag, t in p.text if tag not in {"script", "style"}]
    assert visible == [("title", "Aftercrop")], f"hard-coded text in {page.name}: {visible}"
    for tag, attrs in p.attrs:
        for key in ("alt", "title", "aria-label", "placeholder"):
            assert key not in attrs, f"{page.name}: static {key} on <{tag}>, use data-copy-label or a binding"


@pytest.mark.parametrize("page", PAGES, ids=lambda p: p.name)
def test_every_data_copy_key_exists(page):
    p = parse(page)
    keys = [a[k] for _, a in p.attrs for k in ("data-copy", "data-copy-label", "data-copy-alt") if a.get(k)]
    assert keys
    missing = [(lang, k) for k in keys for lang, tree in (("en", EN), ("pt", PT)) if not isinstance(lookup(tree, k), str)]
    assert not missing, f"{page.name}: {missing}"


@pytest.mark.parametrize("page", PAGES, ids=lambda p: p.name)
def test_page_shell(page):
    src = page.read_text(encoding="utf-8")
    assert src.startswith("<!doctype html>")
    assert '<html lang="en">' in src, "English by default (7.18, SA-D59)"
    assert 'name="viewport"' in src
    assert '<script type="module" src="js/app.mjs"></script>' in src
    assert 'id="ledger-dialog"' in src
    assert "x-cloak" in src


def test_copy_json_is_current():
    current = (SITE / "data" / "copy.json").read_text(encoding="utf-8")
    assert current == copy_json.render(copy_json.build_copy()), "run .venv/bin/python -m pipeline.build.copy_json"


def test_copy_json_shape():
    doc = json.loads((SITE / "data" / "copy.json").read_text(encoding="utf-8"))
    assert doc["default_lang"] == "en"
    assert set(doc["langs"]) == {"en", "pt"}
    assert doc["langs"]["en"] == EN and doc["langs"]["pt"] == PT


def test_copy_json_refuses_mismatched_files(tmp_path):
    a = tmp_path / "en.yaml"
    b = tmp_path / "pt.yaml"
    a.write_text("x: 'one'\ny: 'two'\n", encoding="utf-8")
    b.write_text("x: 'um'\n", encoding="utf-8")
    with pytest.raises(copy_json.CopyError):
        copy_json.build_copy({"en": a, "pt": b})
    b.write_text("x: 'um'\ny: ''\n", encoding="utf-8")
    with pytest.raises(copy_json.CopyError):
        copy_json.build_copy({"en": a, "pt": b})
    b.write_text("x: 'um'\ny: 'dois'\n", encoding="utf-8")
    doc = copy_json.build_copy({"en": a, "pt": b})
    assert doc["langs"]["pt"] == {"x": "um", "y": "dois"}
    assert copy_json.render(doc) == copy_json.render(copy_json.build_copy({"en": a, "pt": b})), "deterministic"


# ---- vendored files ------------------------------------------------------------------------

def vendor_manifest() -> dict:
    return json.loads((VENDOR / "VENDOR.json").read_text(encoding="utf-8"))


def test_vendored_hashes_recorded():
    files = vendor_manifest()["files"]
    listed = {f["file"] for f in files}
    on_disk = {p.name for p in VENDOR.glob("*.js")}
    assert listed == on_disk, f"listed {listed}, on disk {on_disk}"
    for f in files:
        data = (VENDOR / f["file"]).read_bytes()
        assert hashlib.sha256(data).hexdigest() == f["sha256"], f["file"]
        assert f["license"] and (VENDOR / f["license_file"]).is_file(), f["file"]
        assert f["version"] in f["file"]


def test_alpine_is_the_minified_esm_build():
    src = (VENDOR / "alpine-3.17.5.min.js").read_text(encoding="utf-8")
    assert 'version:"3.17.5"' in src
    assert re.search(r"export\{[^}]*as default", src), "ES module build, imported by js/app.mjs"
    assert src.count("\n") < 40, "minified"
    assert "MIT" in (VENDOR / "LICENSE-alpinejs.txt").read_text(encoding="utf-8")


def test_app_imports_only_vendored_alpine():
    src = (SITE / "js" / "app.mjs").read_text(encoding="utf-8")
    targets = re.findall(r"""(?:from\s+|import\(\s*)['"]([^'"]+)['"]""", src)
    assert sorted(set(targets)) == ["../vendor/alpine-3.17.5.min.js", "./decision.mjs", "./ledger.mjs", "./svg.mjs"]


# ---- M7.8: section order ---------------------------------------------------------------------

def test_main_page_order():
    ids = parse(SITE / "index.html").ids
    # SA-D78: the ribbon with its Zarc risk band moved up, before the three columns.
    # SA-D107: right below the answer and the NASA line, before the counts and the model check.
    order = ["sec-picker", "sec-question", "sec-answer", "sec-ribbon", "sec-answer-notes", "sec-columns", "sec-year-strip", "sec-nasa", "sec-strip"]
    pos = [ids.index(i) for i in order]
    assert pos == sorted(pos), f"order {order} broken: {pos}"
    src = (SITE / "index.html").read_text(encoding="utf-8")
    assert src.index('id="sec-answer"') < src.index("<svg") if "<svg" in src else True
    assert src.index('id="sec-answer"') < src.index('id="sec-year-strip"')


def test_ribbon_between_the_answer_and_the_counts():
    """SA-D107: answer box, NASA line, the 8-season ribbon with the Zarc band and the legend, then
    the counts caption and the model check."""
    src = (SITE / "index.html").read_text(encoding="utf-8")
    marks = ['class="answer-box"', 'class="nasa-line"', 'id="sec-ribbon"', "v.ribbon.band", "v.ribbon.legend",
             'id="sec-answer-notes"', 'x-text="v.answer.caption"', 'x-text="v.answer.modelCheck"', 'id="sec-columns"']
    pos = [src.index(m) for m in marks]
    assert pos == sorted(pos), f"order {marks} broken: {pos}"
    answer = src[src.index('id="sec-answer"'):src.index('id="sec-ribbon"')]
    assert "v.answer.caption" not in answer and "v.answer.modelCheck" not in answer


def test_field_facts_strip_next_to_the_buttons():
    """SA-D107: the facts strip and the fixed sentence in the header, the buttons wait for ready."""
    src = (SITE / "index.html").read_text(encoding="utf-8")
    header = src[src.index('id="sec-picker"'):src.index("</header>")]
    assert 'class="field-facts"' in header and 'data-copy="page.header.same_answer"' in header
    assert header.index('class="field-picker"') < header.index('class="field-facts"') < header.index("page.header.same_answer")
    assert ':disabled="!ready"' in header
    js = (SITE / "js" / "app.mjs").read_text(encoding="utf-8")
    assert "ready: false" in js and "this.ready = true" in js and "READY_TIMEOUT_MS" in js
    for lang in ("en", "pt"):
        copy = json.loads((SITE / "data" / "copy.json").read_text(encoding="utf-8"))
        assert "same_answer" in copy["langs"][lang]["page"]["header"], lang
        assert set(copy["langs"][lang]["page"]["header"]["facts"]) >= {"title", "area", "slope", "municipality", "seasons", "no_green"}


def test_answer_is_announced():
    src = (SITE / "index.html").read_text(encoding="utf-8")
    box = src[src.index('class="answer-box"'):][:200]
    assert 'aria-live="polite"' in box


# ---- M7.10: hosting limits --------------------------------------------------------------------

def test_file_sizes():
    big = [p for p in SITE.rglob("*") if p.is_file() and p.stat().st_size > 25 * 1024 * 1024]
    assert not big
    total = sum(p.stat().st_size for p in SITE.rglob("*") if p.is_file())
    assert total < 1024 ** 3


def test_no_example_paths_in_production_code():
    for path in PAGES + OWN_JS:
        assert "tests/fixtures" not in path.read_text(encoding="utf-8"), path.name


# ---- basemap: the page reads what pipeline/fields/basemap.py writes -----------------------------

BASEMAP_PROBE = """
const A = await import(process.argv[1]);
const g = JSON.parse(process.argv[2]);
console.log(JSON.stringify([A.BASEMAP, A.basemapImage(g, { width: g.width, height: g.height }),
  A.basemapImage(g, { width: g.width + 1, height: g.height }), A.basemapImage(null, { width: 1, height: 1 })]));
"""


def test_page_reads_the_basemap_georeference():
    import shutil
    import subprocess

    from rasterio.crs import CRS

    from pipeline.fields import basemap

    node = shutil.which("node")
    if node is None:
        pytest.fail("node is needed to read site/js/app.mjs (make test-js needs it too)")
    g = basemap.georef((30.0, 0.0, 474390.0, 0.0, -30.0, -2571150.0), CRS.from_epsg(32622).to_wkt(), 32622, (373, 345))
    out = subprocess.run([node, "--input-type=module", "-e", BASEMAP_PROBE, (SITE / "js" / "app.mjs").as_uri(), json.dumps(g)],
                         capture_output=True, text=True, timeout=60, check=True)
    paths, ok, wrong_size, no_georef = json.loads(out.stdout)
    assert paths == {"png": "img/basemap.png", "georef": "img/basemap.json"}
    assert ok == {"href": "img/basemap.png", "width": 345, "height": 373}
    assert wrong_size is None and no_georef is None
    src = (SITE / "js" / "app.mjs").read_text(encoding="utf-8")
    # The data folder picks the basemap: BASEMAP for data/, <folder>img/ for a rehearsal folder (SA-D63).
    assert "basemapUrls(data.base)" in src
    assert "fetchJsonFrom(win, urls.georef)" in src and "img.src = urls.png" in src


# ---- P4: publishing from a subpath (GitHub Pages) ------------------------------------------

REF = re.compile(r"""(?:\b(?:src|href)=|from\s+|import\(\s*|fetch\(\s*|url\(\s*)['"]([^'"`$]+)['"]""")


def site_targets():
    """(file, reference) for every static reference of the site's own HTML, JS and CSS."""
    for path in PAGES + [SITE / "sheet.html"] + sorted((SITE / "js").glob("*.mjs")) + OWN_CSS:
        for m in REF.finditer(path.read_text(encoding="utf-8")):
            ref = m.group(1).split("#")[0].split("?")[0]
            if ref and not ref.startswith(("#", "data:", "mailto:")):
                yield path, ref


def test_every_reference_stays_inside_site():
    """Pages serves site/ at /<repository>/: a relative reference must resolve inside site/."""
    refs = list(site_targets())
    assert len(refs) > 5
    bad = []
    for path, ref in refs:
        if ABSOLUTE.match(ref) or ref.startswith("/"):
            bad.append((path.name, ref))
            continue
        target = (path.parent / ref).resolve()
        if not target.is_relative_to(SITE.resolve()):
            bad.append((path.name, ref))
    assert not bad, f"references outside site/: {bad}"


def test_no_literal_path_above_site():
    """Only the ?data= query of the address can point above site/ (the fictitious example of the
    repository, which Pages does not deploy). The page then says so (missing.example_folder,
    tests/publish.test.mjs). No literal in site/ names a path above it."""
    for path in PAGES + [SITE / "sheet.html"] + sorted((SITE / "js").glob("*.mjs")):
        src = path.read_text(encoding="utf-8")
        ups = [m.group(0) for m in re.finditer(r"""['"](?:\.\./)+[^'"]*['"]""", src)]
        assert all(u.strip("'\"").startswith("../vendor/") and path.parent.name == "js" for u in ups), (path.name, ups)
    for page in PAGES:
        assert 'x-show="v.dataFolderMissing"' in page.read_text(encoding="utf-8"), page.name


def test_nojekyll_and_pages_workflow():
    assert (SITE / ".nojekyll").is_file()
    wf = ROOT / ".github" / "workflows" / "pages.yml"
    src = wf.read_text(encoding="utf-8")
    assert src.startswith("# Written with an SI tool (Claude, Anthropic) for NASA Space Apps 2026. See AI_USE.md.")
    doc = yaml.safe_load(src)
    on = doc.get("on", doc.get(True))                            # YAML 1.1 reads "on" as true
    assert on["push"]["branches"] == ["main"] and "workflow_dispatch" in on
    assert doc["permissions"] == {"contents": "read", "pages": "write", "id-token": "write"}
    steps = doc["jobs"]["deploy"]["steps"]
    uses = [s["uses"] for s in steps]
    assert [u.split("@")[0] for u in uses] == ["actions/checkout", "actions/configure-pages",
                                               "actions/upload-pages-artifact", "actions/deploy-pages"]
    assert all(re.fullmatch(r"v\d+", u.split("@")[1]) for u in uses), "pinned to a major version"
    assert steps[2]["with"]["path"] == "site"
    assert not any("run" in s for s in steps), "no build and no script in the workflow"


def test_license_is_apache_2_with_the_team_line():
    text = (ROOT / "LICENSE").read_text(encoding="utf-8")
    assert "Apache License" in text and "Version 2.0, January 2004" in text
    assert "END OF TERMS AND CONDITIONS" in text
    assert "Copyright 2026 The Aftercrop team" in text
    assert "[DECIDE]" not in (ROOT / "LICENSES.md").read_text(encoding="utf-8").split("## Data")[0]


# ---- SA-D66: NASA switch placement and the four levels -------------------------------------

def test_switch_section_follows_the_placement_rule():
    src = (SITE / "index.html").read_text(encoding="utf-8")
    assert re.search(r'<section class="nasa" id="sec-nasa"[^>]*x-show="v\.nasa\.onMain"', src)
    how = (SITE / "how-we-know.html").read_text(encoding="utf-8")
    assert 'id="hw-switch-place"' in how and "v.switchPlace.text" in how
    assert 'id="hw-levels"' in how and 'x-for="l in v.levels"' in how


def test_how_we_know_explains_the_first_green_cycle():
    how = (SITE / "how-we-know.html").read_text(encoding="utf-8")
    assert 'data-copy="howweknow.ground.first_cycle"' in how
    en = EN["howweknow"]["ground"]["first_cycle"]
    assert "first green cycle" in en and "never classifies" in en and "D12" in en
    assert "D12" in PT["howweknow"]["ground"]["first_cycle"]


# ---- SA-D95: the 30-second demo lives on the site ------------------------------------------

DEMO = SITE / "demo.html"
DEMO_VIDEO = SITE / "demo" / "aftercrop_demo.mp4"


def test_demo_page_exists_with_the_site_shell():
    src = DEMO.read_text(encoding="utf-8")
    assert src.startswith("<!doctype html>")
    assert "<!-- Written with an SI tool (Claude, Anthropic) for NASA Space Apps 2026. See AI_USE.md. -->" in src
    assert '<html lang="en">' in src and 'name="viewport"' in src
    assert "<title>Aftercrop · 30-second demo</title>" in src
    assert 'href="favicon.svg"' in src and 'href="css/site.css"' in src
    assert "written with an SI tool" in src, "visible SI notice"
    assert 'href="index.html"' in src, "link back to the main page"
    assert re.search(r"<video[^>]*\bcontrols\b", src)


def test_demo_page_references_are_relative_and_present():
    p = parse(DEMO)
    refs = [attrs[k] for _, attrs in p.attrs for k in ("src", "href", "poster") if attrs.get(k)]
    assert "demo/aftercrop_demo.mp4" in refs
    assert not re.search(r"https?://", DEMO.read_text(encoding="utf-8"))
    for ref in refs:
        assert not ABSOLUTE.match(ref) and not ref.startswith("/"), ref
        target = (SITE / ref.split("#")[0]).resolve()
        assert target.is_relative_to(SITE.resolve()) and target.is_file(), ref


def test_demo_video_is_present_and_small():
    assert DEMO_VIDEO.is_file()
    head = DEMO_VIDEO.read_bytes()[:12]
    assert head[4:8] == b"ftyp", "MP4 container"
    assert DEMO_VIDEO.stat().st_size < 10 * 1024 * 1024


@pytest.mark.parametrize("page", PAGES, ids=lambda p: p.name)
def test_footer_links_the_demo(page):
    src = page.read_text(encoding="utf-8")
    footer = src[src.index('<footer class="fixed-strip"'):src.index("</footer>")]
    assert '<a class="demo-link" href="demo.html" data-copy="page.footer.demo"></a>' in footer

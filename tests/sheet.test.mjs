// Written with an SI tool (Claude, Anthropic) for NASA Space Apps 2026. See AI_USE.md.
// Tests of site/js/sheet.mjs, the agronomist sheet (plan 7.12, sheet M8, criteria M8.1 to M8.5).
// Run: node --test tests/sheet.test.mjs   (Node 22, from the repository root)
//
// The copy is the real one: config/copy_*.yaml in the shape of site/data/copy.json, built by
// pipeline/build/copy_json.py through .venv/bin/python. The fragment is the one app.mjs
// sheetHash() writes, read against the M5 example data of tests/fixtures/decision_example/.
// States are "example, fictitious".

import { test, describe } from 'node:test';
import assert from 'node:assert/strict';
import { existsSync, readFileSync } from 'node:fs';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

import * as S from '../site/js/sheet.mjs';
import * as D from '../site/js/decision.mjs';
import * as A from '../site/js/app.mjs';

const ROOT = fileURLToPath(new URL('..', import.meta.url));
const FIXTURES = new URL('./fixtures/', import.meta.url);
const load = (name) => JSON.parse(readFileSync(new URL(name, FIXTURES), 'utf8'));

// ---------------------------------------------------------------- copy, from the YAML files

const COPY_SCRIPT = `
import json, sys
try:
    from pipeline.build.copy_json import build_copy
    doc = build_copy()
except ImportError:
    import yaml
    doc = {"langs": {l: yaml.safe_load(open(f"config/copy_{l}.yaml", encoding="utf-8")) for l in ("en", "pt")}}
json.dump(doc, sys.stdout, ensure_ascii=False)
`;

function loadCopyDoc() {
  for (const py of [`${ROOT}.venv/bin/python`, 'python3']) {
    if (py.startsWith('/') && !existsSync(py)) continue;
    const r = spawnSync(py, ['-c', COPY_SCRIPT], { cwd: ROOT, encoding: 'utf8', timeout: 60000 });
    if (r.status === 0 && r.stdout) return JSON.parse(r.stdout);
  }
  const built = new URL('../site/data/copy.json', import.meta.url);
  if (existsSync(built)) return JSON.parse(readFileSync(built, 'utf8'));
  throw new Error('cannot read config/copy_*.yaml: need .venv/bin/python with PyYAML, or site/data/copy.json');
}

const COPY_DOC = loadCopyDoc();
const PT = S.pickCopy(COPY_DOC, 'pt');
const EN = S.pickCopy(COPY_DOC, 'en');
const MISSING = PT.sheet.date_missing;

const EXAMPLE = load('sheet_example_state.json');
const DATA_DIR = `${EXAMPLE.data_dir}/`;
const DATA = A.normalizeData({
  copy: COPY_DOC,
  region: load(`${DATA_DIR}region.json`),
  trials: load(`${DATA_DIR}trials_steps.json`),
  scoreboard: null,
  fields: { a: load(`${DATA_DIR}field_a.json`), b: load(`${DATA_DIR}field_b.json`) },
  base: 'data/',
});
const PAGE_UI = EXAMPLE.page;
const fromPage = (ui) => S.resolveState(S.parseFragment(`#${A.sheetHash(ui)}`).params, DATA);
const NOW = new Date(2026, 10, 15);
const clone = (x) => JSON.parse(JSON.stringify(x));
const state = (patch = {}) => ({ ...clone(EXAMPLE.state), ...patch });
const build = (s, opts = {}) => S.buildSheet(s, PT, { now: NOW, ...opts });
const textOf = (s, opts) => S.sheetText(build(s, opts));
const itemText = (model, key) => {
  const item = model.items.find((i) => i.key === key);
  return item.lines.map((l) => (l.label ? `${l.label}${l.sep ?? ': '}` : '') + S.segmentsText(l.segments)).join('\n');
};

function leaves(node, path = []) {
  if (Array.isArray(node)) return node.flatMap((v, i) => leaves(v, [...path, String(i)]));
  if (node && typeof node === 'object') return Object.entries(node).flatMap(([k, v]) => leaves(v, [...path, k]));
  return [[path.join('.'), node]];
}

// Deterministic generator (mulberry32) with a fixed seed, as in decision.test.mjs.
function rng(seed) {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function randomState(r) {
  const pick = (list) => list[Math.floor(r() * list.length)];
  const numberish = (kind) => {
    const u = r();
    if (u < 0.75) return Math.round((kind === 'cost' ? r() * 5000 : 0.01 + r() * 300) * 100) / 100;
    return pick([undefined, null, 0, -1, '12', NaN, Infinity, 1e308]);
  };
  const numbers = {};
  for (const [f, kind] of Object.entries(D.FIELD_KIND)) {
    if (f === 'q') continue;
    const v = numberish(kind);
    if (v !== undefined) numbers[f] = v;
  }
  const s = state({
    numbers,
    option: pick(['S2', 'S3', undefined, 'S1', 'S9']),
    sowingDate: pick(['2027-01-10', '2027-03-30', '2027-02-30', null, undefined, 'soon']),
    windowReading: pick([EXAMPLE.state.windowReading, { state: 'outside', k: 4, n: 6 }, { k: 9, n: 2 }, undefined]),
    hlsOn: pick([true, false]),
    zarcConstraint: pick([true, false, undefined]),
    constraints: pick([[], [{ id: 'no_wheat_buyer', removes: ['S3'] }], [{ id: 'no_grass_seeding', removes: ['S2'] }]]),
    area_ha: pick([12.5, { v: 3.25 }, 0, -2, '7', undefined]),
    field: pick(['a', 'b', undefined, '<b>x</b>']),
  });
  for (const k of Object.keys(s)) if (s[k] === undefined) delete s[k];
  return s;
}

// ---------------------------------------------------------------- fragment

describe('fragment of the URL, as app.mjs sheetHash writes it', () => {
  test('the page link and the sheet agree on every key', () => {
    const hash = `#${A.sheetHash(PAGE_UI)}`;
    const { params, error } = S.parseFragment(hash);
    assert.equal(error, null);
    assert.equal(params.field, 'a');
    assert.equal(params.hlsOn, true);
    assert.equal(params.lang, 'pt');
    assert.equal(params.sowingDate, '2027-02-10');
    assert.deepEqual(params.numbers, { ...EXAMPLE.state.numbers });
    assert.deepEqual(params.limits, { rented_land: 'rented', zarc_insurance_credit: 'no' });
    const off = S.parseFragment(`#${A.sheetHash({ ...PAGE_UI, hlsOn: false })}`).params;
    assert.equal(off.hlsOn, false);
  });

  test('the page state, read with the example data, gives the hand-resolved sheet', () => {
    const resolved = fromPage(PAGE_UI);
    assert.equal(S.sheetText(build(resolved), { seals: true }), S.sheetText(build(state()), { seals: true }));
    assert.equal(resolved.area_ha.v, 12.5);
  });

  test('random page states: the sheet shows the state the page shows (app.mjs decide)', () => {
    const r = rng(311);
    const pick = (list) => list[Math.floor(r() * list.length)];
    for (let i = 0; i < 300; i++) {
      const raw = {};
      for (const f of A.NUMBER_FIELDS) raw[f] = pick(['', 'abc', '0', '-3', String(Math.round(r() * 30000) / 100).replace('.', ',')]);
      const limits = Object.fromEntries(Object.entries(A.LIMITS).map(([k, spec]) => [k, pick(['', ...spec.options])]));
      const ui = { ...PAGE_UI, raw, limits, field: pick(['a', 'b']), hlsOn: pick([true, false]),
        sowingDate: pick(['', '2027-01-15', '2027-03-25', '2026-12-01']) };
      const page = A.decide(DATA, ui);
      const model = build(fromPage(ui));
      const opt = page.answer.main;
      assert.equal(model.option, opt, `${i}`);
      assert.equal(model.state, opt ? page[opt].state : page.answer.state, `${i}`);
    }
  });

  test('bad fragments give an error code and print every value as missing', () => {
    for (const [hash, code] of [['', 'empty'], ['#', 'empty'], ['#x=1', 'no_state'], ['#state=keep&G=99', 'no_state'],
      [`#p_soy=${'1'.repeat(S.MAX_FRAGMENT_LENGTH)}`, 'too_long']]) {
      const { params, error } = S.parseFragment(hash);
      assert.equal(error, code, hash.slice(0, 30));
      const t = S.sheetText(build(S.resolveState(params, DATA)));
      assert.ok(t.includes(`Talhão ${MISSING}`));
      assert.ok(!t.includes('99'));
    }
    assert.equal(S.parseFragment(undefined).error, 'empty');
    assert.equal(S.parseFragment(42).error, 'empty');
  });

  test('only known keys and values pass: unknown field, limit answer or option is dropped', () => {
    const { params } = S.parseFragment('#field=z&no_wheat_buyer=maybe&option=S1&hls=0&owner=x&priority=Income!');
    assert.equal(params.field, undefined);
    assert.deepEqual(params.limits, {});
    assert.equal(params.option, undefined);
    assert.equal(params.priority, undefined);
    assert.equal(params.hlsOn, false);
    assert.ok(!Object.hasOwn(params, 'owner'));
    assert.equal(S.parseFragment('#option=S2&field=b').params.option, 'S2');
  });

  test('numbers: plain decimals parse, anything else stays text and prints as missing', () => {
    const { params } = S.parseFragment('#p_soy=100&p_corn=47.35&p_wheat=1e2&Y_corn=abc&c_corn=Infinity&c_wheat=');
    assert.deepEqual(params.numbers, { p_soy: 100, p_corn: 47.35, p_wheat: 100, Y_corn: 'abc', c_corn: 'Infinity', c_wheat: '' });
    const model = build(S.resolveState(params, DATA));
    assert.equal(model.state, 'not_enough');
    const a = itemText(model, 'assumptions');
    assert.ok(a.includes(`${PT.page.inputs.Y_corn.label}: ${MISSING}`));
    assert.ok(!a.includes('abc') && !a.includes('Infinity'));
  });

  test('the sheet follows the page language (P2.3): ?lang= first, then the fragment, then English', () => {
    assert.equal(S.DEFAULT_SHEET_LANG, 'en');
    assert.deepEqual([...S.SHEET_LANGS], ['en', 'pt']);
    const fragEn = S.parseFragment(`#${A.sheetHash({ ...PAGE_UI, lang: 'en' })}`).params.lang;
    const fragPt = S.parseFragment(`#${A.sheetHash(PAGE_UI)}`).params.lang;
    assert.equal(fragEn, 'en');
    assert.equal(fragPt, 'pt');
    assert.equal(S.sheetLang('', fragPt), 'pt');
    assert.equal(S.sheetLang('', fragEn), 'en');
    assert.equal(S.sheetLang('', undefined), 'en');
    assert.equal(S.sheetLang('?lang=pt', 'en'), 'pt');
    assert.equal(S.sheetLang('?data=../x/&lang=en', 'pt'), 'en');
    for (const bad of ['?lang=fr', '?lang=', '?lang=PT', '?%E0%A4%A=1', null, 42]) assert.equal(S.sheetLang(bad, undefined), 'en', String(bad));
    assert.equal(S.sheetLang('?lang=de', 'pt'), 'pt');
    // The page language never changes the numbers the sheet reads.
    const en = S.buildSheet(fromPage({ ...PAGE_UI, lang: 'en', raw: { ...PAGE_UI.raw, p_corn: '47.35' } }), EN, { now: NOW });
    const pt = build(fromPage(PAGE_UI));
    assert.equal(en.lang, 'en');
    assert.equal(pt.lang, 'pt-BR');
    assert.equal(en.state, pt.state);
    assert.equal(en.option, pt.option);
  });

  test('the language link keeps every other query key and swaps lang', () => {
    assert.equal(S.otherLangSearch('', 'en'), '?lang=pt');
    assert.equal(S.otherLangSearch('?lang=pt', 'pt'), '?lang=en');
    const q = new URLSearchParams(S.otherLangSearch('?data=..%2Fdata%2Frehearsal%2Fsite_data%2F&lang=en', 'en'));
    assert.equal(q.get('data'), '../data/rehearsal/site_data/');
    assert.equal(q.get('lang'), 'pt');
    assert.equal(S.otherLangSearch(null, 'pt'), '?lang=en');
  });

  test('parseFragment never throws, and the sheet builds from whatever it returns', () => {
    const r = rng(20261115);
    const alphabet = '#&=%{}[]":,0123456789abcvS-_.~+ çã\\';
    const keys = ['field=', 'p_soy=', 'hls=', 'sowing_date=', 'no_wheat_buyer=', 'lang='];
    for (let i = 0; i < 400; i++) {
      let h = i % 3 ? keys[i % keys.length] : '';
      const len = Math.floor(r() * 50);
      for (let j = 0; j < len; j++) h += alphabet[Math.floor(r() * alphabet.length)];
      const out = S.parseFragment(i % 2 ? `#${h}` : h);
      assert.ok(out && typeof out === 'object' && 'params' in out && 'error' in out);
      assert.doesNotThrow(() => build(S.resolveState(out.params, DATA)));
    }
  });

  test('a __proto__ key in the fragment stays inert', () => {
    const { params } = S.parseFragment('#__proto__=x&constructor=y&p_soy=5');
    assert.equal({}.x, undefined);
    assert.deepEqual(params.numbers, { p_soy: 5 });
    assert.doesNotThrow(() => build(S.resolveState(params, DATA)));
  });

  test('resolveState without data or params prints a sheet of missing values', () => {
    for (const [p, d] of [[{}, {}], [null, null], [S.parseFragment(`#${A.sheetHash(PAGE_UI)}`).params, {}]]) {
      const t = S.sheetText(build(S.resolveState(p, d)));
      assert.ok(t.includes(`Janela do Zarc: ${MISSING}`));
      assert.ok(t.includes(`Evidência: ${MISSING} de ${MISSING}`));
    }
  });
});

// ---------------------------------------------------------------- units (M8.3)

describe('sacks per hectare and per alqueire (M8.3)', () => {
  test('1 alqueire = 2.42 ha, the same number the sheet declares', () => {
    assert.equal(S.ALQUEIRE_HA, 2.42);
    const decl = PT.sheet.items.assumptions.match(/1 alqueire = (\d+)\D(\d+) ha/);
    assert.ok(decl, 'the assumptions line declares the alqueire');
    assert.equal(Number(`${decl[1]}.${decl[2]}`), S.ALQUEIRE_HA);
    assert.ok(PT.sheet.items.assumptions.includes('sc/ha') && PT.sheet.items.assumptions.includes('sc/alqueire'));
  });

  test('conversion', () => {
    assert.ok(Math.abs(S.toAlqueire(1) - 2.42) < 1e-12);
    assert.ok(Math.abs(S.toAlqueire(10) - 24.2) < 1e-12);
    assert.equal(S.toAlqueire(0), 0);
    for (const bad of [null, undefined, NaN, Infinity, '1']) assert.equal(S.toAlqueire(bad), null);
  });

  test('G is the step of the count in sc/ha, and that step in sc/alqueire (SA-D91)', () => {
    assert.deepEqual(S.gainLine(1.35), { ha: 2, alq: 4.9 }); // step 2, 2 x 2.42 = 4.84
    assert.deepEqual(S.gainLine(4), { ha: 4, alq: 9.7 }); // 9.68
    assert.deepEqual(S.gainLine(6.666666666666667), { ha: 7, alq: 17 }); // 16.94, the frame of the video
    assert.deepEqual(S.gainLine(-2.04), { ha: -2, alq: -4.8 }); // -4.84, up is toward zero
    assert.deepEqual(S.gainLine(-0.01), { ha: 0, alq: 0 });
    assert.ok(Object.is(S.gainLine(-0.01).ha, 0), 'no -0');
    assert.equal(S.gainLine(null), null);
    assert.equal(S.gainLine(NaN), null);
    const r = rng(7);
    for (let i = 0; i < 2000; i++) {
      const G = (r() - 0.3) * 40;
      const g = S.gainLine(G);
      assert.equal(g.ha, D.stepOf(G) || 0, `ha ${G}: the step of ruleState`);
      const alq = g.ha * 2.42;
      assert.ok(g.alq >= alq - 1e-9 && g.alq - alq < 0.1 + 1e-9, `alq ${G}`);
    }
  });

  test('the rendered sheet shows G in sc/ha and sc/alqueire, declaring 2.42 ha', () => {
    const t = textOf(state());
    const e = EXAMPLE.expected;
    assert.ok(t.includes(`G: ${e.G_ha} sc/ha, ou ${e.G_alq} sc/alqueire`), t);
    assert.ok(t.includes('(1 alqueire = 2,42 ha)'));
  });

  test('numbers use the decimal and thousands marks of the copy', () => {
    assert.equal(S.formatNumber(3000, PT), '3.000');
    assert.equal(S.formatNumber(47.35, PT), '47,35');
    assert.equal(S.formatNumber(1234567.5, PT), '1.234.567,5');
    assert.equal(S.formatNumber(4, PT, { max: 1, min: 1 }), '4,0');
    assert.equal(S.formatNumber(-0.001, PT), '0');
    assert.equal(S.formatNumber(-2, PT), '-2');
    assert.equal(S.formatNumber(3000, EN), '3,000');
    assert.equal(S.formatNumber(NaN, PT), null);
  });
});

// ---------------------------------------------------------------- content (M8.1)

describe('every field of 7.12 is rendered (M8.1)', () => {
  const model = build(state());
  const t = S.sheetText(model);
  const e = EXAMPLE.expected;

  test('seven items in the order of 7.12, the satellite line second in item 2', () => {
    assert.deepEqual(model.items.map((i) => i.key), [...S.ITEMS]);
    assert.deepEqual(model.items.map((i) => i.n), [1, 2, 3, 4, 5, 6, 7]);
    const w = model.items[1].lines;
    assert.equal(w[1].label, PT.sheet.items.satellite.split(': ')[0]);
  });

  test('header: title, fictitious label, field, region and date', () => {
    assert.equal(model.title, PT.sheet.title);
    assert.equal(model.fictitious, PT.sheet.fictitious);
    assert.equal(model.lang, 'pt-BR');
    assert.ok(t.startsWith(`${PT.sheet.title}   ${PT.sheet.fictitious}`));
    assert.ok(t.includes('Talhão A'));
    assert.ok(t.includes(PT.sheet.region));
    assert.ok(t.includes(`Gerada em ${e.generated}`));
  });

  test('option and state, computed by decision.mjs', () => {
    assert.equal(model.state, e.state);
    assert.equal(model.state, D.decideOption('S3', S.decisionInput(state())).state);
    assert.ok(t.includes(`Opção para revisar: ${PT.page.strategies.S3.name} (${PT.states.try_strip})`));
  });

  test('window, declared date and satellite count', () => {
    assert.ok(t.includes(e.window[0]) && t.includes(e.window[1]));
    assert.ok(t.includes(`Data declarada: ${e.sowing_date}`));
    assert.ok(t.includes(`o primeiro ciclo verde passou da janela em ${e.k} de ${e.k_of} safras`));
  });

  test('assumptions: every price, cost and yield of the option, with its unit', () => {
    const a = itemText(model, 'assumptions');
    for (const f of D.ESSENTIAL.S3) {
      const v = S.formatNumber(EXAMPLE.state.numbers[f], PT);
      assert.ok(a.includes(`${PT.page.inputs[f].label}: ${v} ${PT.page.inputs[f].unit}`), f);
    }
    assert.ok(!a.includes(PT.page.inputs.c_grass.label), 'S2 input not listed for S3');
    assert.ok(a.includes(`${PT.page.limits.rented_land.question} ${PT.page.limits.rented_land.rented}`));
  });

  test('evidence: N of n site-seasons, a count', () => {
    assert.ok(t.includes(`Evidência: ${e.N} de ${e.n} locais-safra`));
  });

  test('local review: the alerts of the option, soil test, terraces', () => {
    const r = itemText(model, 'local_review');
    for (const id of S.OPTION_ALERTS.S3) assert.ok(r.includes(PT.sheet.alerts[id]), id);
    for (const id of ['pratylenchus', 'leafhopper_stunting']) assert.ok(!r.includes(PT.sheet.alerts[id]), id);
    assert.ok(r.includes('laudo de solo, terraço'));
  });

  test('strip test with the area, the single-strip sentence and the strip design', () => {
    const st = itemText(model, 'strip_test');
    assert.ok(st.includes(`Teste em faixas pareadas e repetidas: ${e.area} ha, pares a definir aqui`));
    assert.ok(st.includes(PT.sheet.single_strip));
    assert.ok(st.includes(PT.sheet.strip_design));
    assert.equal(model.items[6].writeIn, true);
  });

  test('closing: no local review and the independence text', () => {
    assert.deepEqual(model.closing.map((c) => c.key), ['no_local_review', 'independence']);
    assert.ok(t.includes(PT.sheet.no_local_review));
    assert.ok(t.includes(PT.sheet.independence));
  });

  test('every sheet text of the copy appears, by its fixed parts', () => {
    // window_soil_unknown shows only without a Zarc soil class (its own test below).
    // date_format and months build the dates (their own test below).
    const skip = new Set(['sheet.date_missing', 'sheet.print', 'sheet.window_soil_unknown', 'sheet.date_format']);
    for (const [key, tpl] of leaves(PT.sheet, ['sheet'])) {
      if (skip.has(key) || key.startsWith('sheet.months.')) continue;
      if (key.startsWith('sheet.alerts.') && !S.OPTION_ALERTS.S3.includes(key.split('.').pop())) continue;
      for (const part of tpl.split(/\{[A-Za-z_]+\}/)) {
        const p = part.trim();
        if (p) assert.ok(t.includes(p), `${key}: "${p}"`);
      }
    }
    assert.equal(model.print, PT.sheet.print);
  });

  test('each value carries its seal (7.14)', () => {
    const ts = S.sheetText(model, { seals: true });
    const n = PT.seals.names;
    assert.ok(ts.includes(`${e.window[1]} [${n.official_rule}]`));
    assert.ok(ts.includes(`${e.sowing_date} [${n.your_numbers}]`));
    assert.ok(ts.includes(`${e.k_of} safras [${n.estimated_from_satellite}]`));
    assert.ok(ts.includes(`não chance [${n.derived_from_trials}]`));
    assert.ok(ts.includes(`${e.G_alq} sc/alqueire [${n.your_numbers}]`));
    assert.ok(ts.includes(`3.000 R$/ha [${n.your_numbers}]`));
  });

  test('the English tree has the same keys and builds the same sheet (P2.3)', () => {
    const m = S.buildSheet(state(), EN, { now: NOW });
    assert.deepEqual(m.items.map((i) => i.lines.length), model.items.map((i) => i.lines.length));
    assert.equal(m.state, model.state);
    assert.ok(S.sheetText(m).includes('1 alqueire = 2.42 ha'));
  });

  test('dates follow sheet.date_format: dd/mm/aaaa in Portuguese, 21 Dec 2026 in English', () => {
    assert.equal(S.formatDate('2026-12-21', PT), '21/12/2026');
    assert.equal(S.formatDate('2027-02-05', EN), '05 Feb 2027');
    assert.equal(S.formatDate('2027-02-05'), '05/02/2027');
    assert.equal(S.formatDate('2027-02-30', EN), null);
    assert.equal(PT.sheet.months.length, 12);
    assert.equal(EN.sheet.months.length, 12);
    assert.ok(t.includes(`${e.window[0]} a ${e.window[1]}`));
  });
});

// ---------------------------------------------------------------- missing values

describe('missing values print the missing-value text, never a number', () => {
  const digitRuns = (s) => s.match(/\d+/g) ?? [];
  const COPY_DIGITS = new Set(leaves(PT).flatMap(([, v]) => (typeof v === 'string' ? digitRuns(v) : [])));
  const ALLOWED = new Set([...COPY_DIGITS, '1', '2', '3', '4', '5', '6', '7', '15', '11', '2026']);

  test('an empty state: every value is missing, every digit comes from the copy or the date', () => {
    for (const s of [{}, null, undefined, 'x', S.resolveState(S.parseFragment('#junk').params, {})]) {
      const model = build(s);
      const t = S.sheetText(model);
      for (const run of digitRuns(t)) assert.ok(ALLOWED.has(run), `made-up number ${run}`);
      for (const item of model.items) {
        for (const l of item.lines) {
          for (const seg of l.segments) if (seg.seal && seg.seal !== 'unknown') assert.fail(`${item.key}: ${seg.text}`);
        }
      }
      assert.ok(t.includes(`Talhão ${MISSING}`));
      assert.ok(t.includes(`Janela do Zarc: ${MISSING}. Data declarada: ${MISSING}`));
      assert.ok(t.includes(`em ${MISSING} de ${MISSING} safras`));
      assert.ok(t.includes(`G: ${MISSING} sc/ha, ou ${MISSING} sc/alqueire`));
      assert.ok(t.includes(`Evidência: ${MISSING} de ${MISSING} locais-safra`));
      assert.ok(t.includes(`repetidas: ${MISSING}, pares`));
    }
  });

  test('a missing price: its row, G and N are missing, and the sheet says what is needed', () => {
    const numbers = { ...EXAMPLE.state.numbers };
    delete numbers.p_soy;
    const model = build(state({ numbers }));
    const a = itemText(model, 'assumptions');
    assert.ok(a.includes(`${PT.page.inputs.p_soy.label}: ${MISSING}`));
    assert.ok(a.includes(`G: ${MISSING} sc/ha, ou ${MISSING} sc/alqueire`));
    assert.equal(model.state, 'not_enough');
    assert.ok(itemText(model, 'option').includes(PT.answers.invalid_field.replace('{field}', PT.fields.p_soy)));
    assert.ok(itemText(model, 'evidence').includes(`Evidência: ${MISSING} de 10`));
  });

  test('a value out of its domain or not numeric is never printed', () => {
    for (const bad of [-1, 0, '47', null, NaN]) {
      const model = build(state({ numbers: { ...EXAMPLE.state.numbers, p_corn: bad } }));
      const a = itemText(model, 'assumptions');
      assert.ok(a.includes(`${PT.page.inputs.p_corn.label}: ${MISSING}`), String(bad));
      assert.ok(!/G: \d/.test(a), String(bad));
    }
    const costZero = build(state({ numbers: { ...EXAMPLE.state.numbers, c_wheat: 0 } }));
    assert.ok(itemText(costZero, 'assumptions').includes(`${PT.page.inputs.c_wheat.label}: 0 R$/ha`), 'a zero cost is valid');
  });

  test('declared date: missing or impossible prints the missing text', () => {
    for (const d of [undefined, null, '', '2027-02-30', '10/02/2027', 20270210]) {
      const s = state({ sowingDate: d });
      if (d === undefined) delete s.sowingDate;
      assert.ok(textOf(s).includes(`Data declarada: ${MISSING}`), String(d));
    }
    assert.ok(itemText(build(state({ sowingDate: '2027-02-30' })), 'window').includes(PT.flags.date_invalid));
  });

  test('Zarc window: missing, impossible or reversed prints the missing text', () => {
    const cases = [undefined, {}, { corn: { start: '2026-12-21' } }, { corn: { start: '2027-03-01', end: '2027-01-01' } },
      { corn: { start: '2026-12-21', end: '2027-02-31' } }];
    for (const w of cases) {
      const s = state({ zarcWindow: w });
      if (w === undefined) delete s.zarcWindow;
      assert.ok(textOf(s).includes(`Janela do Zarc: ${MISSING}.`), JSON.stringify(w));
    }
  });

  test('satellite count: missing, fractional or k above n prints the missing text', () => {
    for (const wr of [undefined, {}, { k: 1.5, n: 7 }, { k: 8, n: 7 }, { k: 0, n: 0 }, { k: '1', n: 7 }, { k: -1, n: 7 }]) {
      const s = state({ windowReading: wr });
      if (wr === undefined) delete s.windowReading;
      assert.ok(textOf(s).includes(`em ${MISSING} de ${MISSING} safras`), JSON.stringify(wr));
    }
    assert.ok(textOf(state({ windowReading: { state: 'outside', k: 0, n: 8 } })).includes('em 0 de 8 safras'));
  });

  test('area and field id: only a positive number and a plain id print', () => {
    for (const a of [undefined, 0, -3, '12', { v: null }, NaN]) {
      const s = state({ area_ha: a });
      if (a === undefined) delete s.area_ha;
      assert.ok(textOf(s).includes(`repetidas: ${MISSING}, pares`), String(a));
    }
    assert.ok(textOf(state({ area_ha: { v: 3.25, l: 'field_a.area_ha' } })).includes('repetidas: 3,3 ha'));
    for (const f of ['<script>', '', 'a b', 'x'.repeat(13), 7]) assert.ok(textOf(state({ field: f })).includes(`Talhão ${MISSING}`), String(f));
  });

  test('trial evidence absent: N and n are missing, not zero', () => {
    const s = state();
    delete s.trials;
    const model = build(s);
    assert.equal(model.state, 'not_enough');
    assert.ok(itemText(model, 'evidence').includes(`Evidência: ${MISSING} de ${MISSING} locais-safra`));
    assert.ok(!/\b0 locais-safra/.test(S.sheetText(model)));
  });

  test('G <= 0: the count is not used, so N is missing and n stays', () => {
    const model = build(state({ numbers: { ...EXAMPLE.state.numbers, p_corn: 40 } })); // M_corn 1000 < M_wheat 1600
    assert.equal(model.code, 'pays_without_gain');
    assert.ok(itemText(model, 'evidence').includes(`Evidência: ${MISSING} de 10`));
    assert.ok(itemText(model, 'assumptions').includes('G: -6 sc/ha, ou -14,5 sc/alqueire'));
  });
});

// ---------------------------------------------------------------- state from decision.mjs

describe('the state always comes from decision.mjs', () => {
  test('random states: option, state and G agree with decideField', () => {
    const r = rng(1984);
    for (let i = 0; i < 600; i++) {
      const s = randomState(r);
      const model = build(s);
      const decided = D.decideField(S.decisionInput(s));
      const opt = D.OPTIONS.includes(s.option) ? s.option : decided.answer.main;
      assert.equal(model.option, opt);
      const res = opt ? decided[opt] : decided.answer;
      assert.equal(model.state, res.state);
      const t = S.sheetText(model);
      const stateText = PT.states[res.state];
      assert.ok(t.includes(`(${stateText})`));
      const gl = opt ? S.gainLine(res.G) : null;
      const gText = gl
        ? `G: ${S.formatNumber(gl.ha, PT)} sc/ha, ou ${S.formatNumber(gl.alq, PT, { max: 1, min: 1 })} sc/alqueire`
        : `G: ${MISSING} sc/ha, ou ${MISSING} sc/alqueire`;
      assert.ok(t.includes(gText), `${i}: ${gText}`);
    }
  });

  test('a removed option says why, with the copy of the reason', () => {
    const model = build(state({ constraints: [{ id: 'no_wheat_buyer', removes: ['S3'], why: 'ignored' }] }));
    assert.equal(model.state, 'removed');
    const o = itemText(model, 'option');
    assert.ok(o.includes(`(${PT.states.removed})`));
    assert.ok(o.includes(`${PT.page.strategies.S3.name} não entra aqui: ${PT.page.limits.reasons.no_wheat_buyer}.`));
    assert.ok(!o.includes('ignored'), 'the reason text comes from the copy, not the fragment');
  });

  test('both options removed: S1, keep, and both reasons', () => {
    const s = state({ option: undefined, constraints: [
      { id: 'no_wheat_buyer', removes: ['S3'] }, { id: 'no_grass_seeding', removes: ['S2'] }] });
    delete s.option;
    const model = build(s);
    assert.equal(model.option, null);
    assert.equal(model.state, 'keep');
    const o = itemText(model, 'option');
    assert.ok(o.includes(PT.page.strategies.S1.name));
    assert.ok(o.includes(PT.answers.both_removed));
    assert.ok(o.includes(PT.page.limits.reasons.no_grass_seeding) && o.includes(PT.page.limits.reasons.no_wheat_buyer));
  });

  test('without an option, the sheet reviews the main answer', () => {
    const s = state();
    delete s.option;
    const model = build(s);
    assert.equal(model.option, D.decideField(S.decisionInput(s)).answer.main);
  });

  test('a declared date outside the window turns keep into not enough evidence, with its sentence', () => {
    const model = build(state({ numbers: { ...EXAMPLE.state.numbers, p_corn: 60 }, sowingDate: '2027-03-20' }));
    assert.equal(model.state, 'not_enough');
    assert.equal(model.code, 'window_outside');
    assert.ok(itemText(model, 'option').includes(PT.answers.window_outside));
  });

  test('the HLS switch never changes G', () => {
    const on = itemText(build(state({ hlsOn: true })), 'assumptions');
    const off = itemText(build(state({ hlsOn: false })), 'assumptions');
    assert.equal(on, off);
  });
});

// ---------------------------------------------------------------- M8.4 and M8.5

describe('local review (M8.4) and no local review (M8.5)', () => {
  const SCOREBOARD = load('scoreboard_example.json');

  test('blast and harvest rain always print as local review, with no number', () => {
    const r = rng(5);
    for (let i = 0; i < 100; i++) {
      const model = build(randomState(r));
      const lines = model.items.find((x) => x.key === 'local_review').lines;
      const un = lines.find((l) => l.key === 'unaudited');
      assert.ok(un, 'unaudited line present');
      assert.equal(S.segmentsText(un.segments), PT.sheet.unaudited);
      assert.ok(!/\d/.test(PT.sheet.unaudited));
    }
    const listed = itemText(build(state()), 'local_review');
    assert.ok(listed.startsWith(`Revisão local: `) && listed.includes(PT.sheet.alerts.blast_and_harvest_rain));
  });

  test('the alerts follow the option reviewed (13.5), and every alert id has its copy', () => {
    const only = (opt, s) => {
      const r = itemText(build(s), 'local_review');
      for (const [id, text] of Object.entries(PT.sheet.alerts)) {
        assert.equal(r.includes(text), S.OPTION_ALERTS[opt].includes(id), `${opt} ${id}`);
      }
    };
    only('S3', state());
    only('S2', state({ option: 'S2' }));
    const both = state({ constraints: [{ id: 'no_wheat_buyer', removes: ['S3'] }, { id: 'no_grass_seeding', removes: ['S2'] }] });
    delete both.option;
    only('S1', both);
    for (const ids of Object.values(S.OPTION_ALERTS)) for (const id of ids) assert.ok(PT.sheet.alerts[id], id);
  });

  test('without a Zarc soil class the window says so, never a bare missing value', () => {
    const unknown = (f) => ({
      ...f,
      soil: { ...f.soil, zarc_ad_class: null, zarc_ad_status: 'unknown' },
      risk: { ...f.risk, zarc_class: Object.fromEntries(Object.entries(f.risk.zarc_class).map(([k, v]) => [k, { ...v, risk_min: null, risk_max: null, state: 'soil_class_unknown' }])) },
      calendar: Object.fromEntries(Object.entries(f.calendar).map(([k, col]) => [k, col.map((x) => (x.state === 'zarc_window' || x.state === 'zarc_previous_table' ? { ...x, start: null, end: null, state: 'soil_class_unknown' } : x))])),
    });
    const data = A.normalizeData({ ...DATA, fields: { a: unknown(DATA.fields.a), b: DATA.fields.b } });
    const s = S.resolveState(S.parseFragment(`#${A.sheetHash({ ...PAGE_UI, field: 'a' })}`).params, data);
    assert.equal(s.zarcSoilUnknown, true);
    const model = build(s);
    const win = model.items.find((i) => i.key === 'window').lines[0];
    const seg = win.segments.find((x) => x.text === PT.sheet.window_soil_unknown);
    assert.ok(seg, itemText(model, 'window'));
    assert.equal(seg.seal, 'unknown');
    assert.ok(itemText(model, 'window').startsWith(`Janela do Zarc: ${PT.sheet.window_soil_unknown}.`));
    const known = S.resolveState(S.parseFragment(`#${A.sheetHash({ ...PAGE_UI, field: 'b' })}`).params, data);
    assert.equal(known.zarcSoilUnknown, false);
    assert.ok(!itemText(build(known), 'window').includes(PT.sheet.window_soil_unknown));
    assert.ok(!S.parseFragment('#zarcSoilUnknown=1&lang=pt').params.zarcSoilUnknown, 'never from the fragment');
  });

  test('H2 not done: the sheet says there was no local review', () => {
    const h2 = SCOREBOARD.scoreboard.tests.find((t) => t.id === 'H2');
    assert.equal(h2.status, 'not_run');
    for (const sb of [null, undefined, {}, { tests: 'x' }, SCOREBOARD, SCOREBOARD.scoreboard,
      { tests: [{ id: 'H2', status: 'not_completed' }] },
      { tests: [{ id: 'H2', status: 'reported' }] },
      { tests: [{ id: 'H2', status: 'pass', note: '' }] },
      { tests: [{ id: 'H2', status: 'exploratory', note: 'Read in a personal capacity.' }] }]) {
      assert.equal(S.localReviewDone(sb), false, JSON.stringify(sb));
      assert.ok(textOf(state(), { scoreboard: sb }).includes(PT.sheet.no_local_review));
    }
  });

  test('the sheet and How we know share one H2 rule', () => {
    assert.equal(S.localReviewDone, A.localReviewDone);
    for (const status of ['pass', 'fail', 'reported', 'not_run', 'exploratory']) {
      for (const note of [null, 'Read in a personal capacity.']) {
        const sb = { tests: [{ id: 'H2', status, note }] };
        const how = A.buildHowView(A.normalizeData({ copy: COPY_DOC, scoreboard: sb, fields: {}, base: 'data/' }), { lang: 'pt' });
        const sheetSays = textOf(state(), { scoreboard: sb }).includes(PT.sheet.no_local_review);
        assert.equal(how.noLocalReview !== null, sheetSays, `${status} ${note}`);
        assert.equal(sheetSays, !(['pass', 'fail', 'reported'].includes(status) && note !== null), `${status} ${note}`);
      }
    }
  });

  test('only a scoreboard with H2 done drops the sentence. The fragment cannot', () => {
    const done = { tests: [{ id: 'H2', status: 'reported', note: 'Read in a personal capacity.' }] };
    assert.equal(S.localReviewDone(done), true);
    assert.ok(!textOf(state(), { scoreboard: done }).includes(PT.sheet.no_local_review));
    const forged = S.parseFragment(`#${A.sheetHash(PAGE_UI)}&h2=pass&H2=reported&scoreboard=done`).params;
    assert.ok(textOf(S.resolveState(forged, DATA)).includes(PT.sheet.no_local_review));
  });
});

// ---------------------------------------------------------------- house style

describe('house style of the printed text', () => {
  test('no em dash, no en dash, no semicolon, in any state', () => {
    const r = rng(42);
    for (let i = 0; i < 200; i++) {
      const t = S.sheetText(build(randomState(r)), { seals: true });
      assert.ok(!/[—–‒―;；]/.test(t), t);
    }
  });

  test('a missing copy key fails loudly instead of printing a guess', () => {
    const broken = clone(PT);
    delete broken.sheet.items.window;
    assert.throws(() => S.buildSheet(state(), broken, { now: NOW }), /sheet\.items\.window/);
  });
});

// ---------------------------------------------------------------- English sheet (P2.3)

describe('the English sheet (P2.3): every field of 7.12, in English', () => {
  const model = S.buildSheet(state(), EN, { now: NOW });
  const t = S.sheetText(model);

  test('header, option, window, satellite, units, evidence, strips and closing', () => {
    assert.equal(model.lang, 'en');
    assert.equal(model.title, EN.sheet.title);
    assert.ok(t.startsWith(`${EN.sheet.title}   ${EN.sheet.fictitious}`));
    assert.ok(t.includes('Field A'));
    assert.ok(t.includes('Generated on 15 Nov 2026'));
    assert.ok(t.includes(`Option to review: ${EN.page.strategies.S3.name} (${EN.states.try_strip})`));
    assert.ok(t.includes('Zarc window: 21 Dec 2026 to 28 Feb 2027. Declared date: 10 Feb 2027'), t);
    assert.ok(t.includes('the first green cycle persisted beyond the window in 1 of 7 seasons'));
    assert.ok(t.includes('G: 2 bags/ha, or 4.9 bags/alqueire'));
    assert.ok(t.includes('3,000 R$/ha') || t.includes('3,000'), 'thousands mark of the English copy');
    assert.ok(t.includes('Evidence: 9 of 10 site-seasons, 3 sites'));
    assert.ok(t.includes('12.5 ha'));
    assert.ok(t.includes(EN.sheet.single_strip) && t.includes(EN.sheet.strip_design));
    assert.ok(t.includes(EN.sheet.no_local_review) && t.includes(EN.sheet.independence));
    assert.equal(model.print, EN.sheet.print);
    assert.equal(model.switchTo, EN.meta.switch_to);
  });

  test('every English sheet text appears, by its fixed parts, and no Portuguese one', () => {
    const skip = new Set(['sheet.date_missing', 'sheet.print', 'sheet.window_soil_unknown', 'sheet.date_format']);
    for (const [key, tpl] of leaves(EN.sheet, ['sheet'])) {
      if (skip.has(key) || key.startsWith('sheet.months.')) continue;
      if (key.startsWith('sheet.alerts.') && !S.OPTION_ALERTS.S3.includes(key.split('.').pop())) continue;
      for (const part of tpl.split(/\{[A-Za-z_]+\}/)) {
        const p = part.trim();
        if (p) assert.ok(t.includes(p), `${key}: "${p}"`);
      }
    }
    for (const word of ['Talhão', 'Gerada em', 'Janela do Zarc', 'sc/ha', 'falta', 'Evidência']) assert.ok(!t.includes(word), word);
  });

  test('an empty English sheet prints the English missing word', () => {
    const empty = S.sheetText(S.buildSheet(S.resolveState({}, DATA), EN, { now: NOW }));
    assert.ok(empty.includes(`Field ${EN.sheet.date_missing}`));
    assert.ok(empty.includes(`Zarc window: ${EN.sheet.date_missing}`));
    assert.ok(!/[\u2014\u2013;]/.test(empty));
  });

  test('random states build in both languages with the same state and option', () => {
    const r = rng(2026);
    for (let i = 0; i < 150; i++) {
      const st = randomState(r);
      const a = S.buildSheet(st, EN, { now: NOW });
      const b = build(st);
      assert.equal(a.state, b.state, `${i}`);
      assert.equal(a.option, b.option, `${i}`);
      assert.ok(!/[\u2014\u2013;]/.test(S.sheetText(a, { seals: true })), `${i}`);
    }
  });
});

// ---------------------------------------------------------------- copy.json and DOM

describe('copy.json and the DOM', () => {
  test('pickCopy reads the shape of pipeline/build/copy_json.py, and a bare tree', () => {
    assert.equal(S.pickCopy({ langs: { pt: PT, en: EN } }, 'pt'), PT);
    assert.equal(S.pickCopy({ pt: PT }, 'pt'), PT);
    assert.equal(S.pickCopy(PT, 'pt'), PT);
    assert.throws(() => S.pickCopy({ langs: {} }, 'pt'));
  });

  test('site/data/copy.json, when built, carries the same sheet keys', (t) => {
    const url = new URL('../site/data/copy.json', import.meta.url);
    if (!existsSync(url)) return t.skip('site/data/copy.json not built');
    const built = S.pickCopy(JSON.parse(readFileSync(url, 'utf8')), 'pt');
    assert.deepEqual(leaves(built.sheet).map(([k]) => k).sort(), leaves(PT.sheet).map(([k]) => k).sort());
  });

  test('renderSheet writes text nodes only, and shows the whole model', () => {
    const doc = fakeDocument();
    const root = doc.createElement('main');
    const model = build(state());
    S.renderSheet(model, doc, root);
    assert.equal(doc.documentElement.lang, 'pt-BR');
    assert.equal(doc.title, PT.sheet.title);
    const shown = root.textWithout((n) => (n.className ?? '').startsWith('seal '));
    for (const item of model.items) {
      for (const l of item.lines) {
        if (l.label) assert.ok(shown.includes(l.label), l.label);
        const body = S.segmentsText(l.segments);
        assert.ok(shown.includes(body), body);
      }
    }
    for (const c of model.closing) assert.ok(shown.includes(c.text), c.key);
    for (const m of model.meta) assert.ok(shown.includes(S.segmentsText(m)));
    const seals = root.findAll((n) => (n.className ?? '').startsWith('seal seal--'));
    assert.ok(seals.length >= 10);
    assert.equal(root.findAll((n) => n.className === 'write-in__rule').length, 4);
    assert.equal(doc.htmlWrites, 0);
  });
});

// Minimal DOM for renderSheet: no innerHTML, so any HTML write is counted and fails the test.
function fakeDocument() {
  const doc = { htmlWrites: 0, title: '' };
  class Node {
    constructor(tag, text = null) {
      this.tag = tag;
      this.children = [];
      this.attrs = {};
      this.dataset = {};
      this.className = '';
      this._text = text;
    }
    appendChild(c) {
      if (c.tag === '#fragment') this.children.push(...c.children);
      else this.children.push(c);
      return c;
    }
    replaceChildren() {
      this.children = [];
    }
    setAttribute(k, v) {
      this.attrs[k] = v;
      if (k === 'class') this.className = v;
    }
    set textContent(v) {
      this.children = [];
      this._text = String(v);
    }
    get textContent() {
      return (this._text ?? '') + this.children.map((c) => c.textContent).join('');
    }
    set innerHTML(_) {
      doc.htmlWrites++;
    }
    insertAdjacentHTML() {
      doc.htmlWrites++;
    }
    textWithout(skip) {
      if (skip(this)) return '';
      return (this._text ?? '') + this.children.map((c) => c.textWithout(skip)).join('');
    }
    findAll(pred) {
      return [...(pred(this) ? [this] : []), ...this.children.flatMap((c) => c.findAll(pred))];
    }
  }
  doc.documentElement = new Node('html');
  doc.createElement = (tag) => new Node(tag);
  doc.createElementNS = (_ns, tag) => new Node(tag);
  doc.createTextNode = (text) => new Node('#text', text);
  doc.createDocumentFragment = () => new Node('#fragment');
  return doc;
}

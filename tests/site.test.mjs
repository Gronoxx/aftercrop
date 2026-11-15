// Written with an SI tool (Claude, Anthropic) for NASA Space Apps 2026. See AI_USE.md.
// Tests of the page logic: site/js/app.mjs, svg.mjs and ledger.mjs (sheet M7, SA-D59).
// Run: node --test tests/site.test.mjs   (Node 22). No browser: the view models are pure.
// Data: the M5 example outputs in tests/fixtures/decision_example/ (example, fictitious) and
// site/data/copy.json, built by pipeline/build/copy_json.py from config/copy_*.yaml.

import { test, describe } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, existsSync } from 'node:fs';
import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

import * as A from '../site/js/app.mjs';
import * as S from '../site/js/svg.mjs';
import * as L from '../site/js/ledger.mjs';
import * as D from '../site/js/decision.mjs';

const ROOT = new URL('../', import.meta.url);
const read = (rel) => JSON.parse(readFileSync(new URL(rel, ROOT), 'utf8'));
const text = (rel) => readFileSync(new URL(rel, ROOT), 'utf8');

const COPY = read('site/data/copy.json');
const FX = 'tests/fixtures/decision_example/';
const STATES_IN = read(`${FX}states_input_m5.json`);
const STATES_OUT = read(`${FX}states_m5.json`);
const SCOREBOARD = read('tests/fixtures/scoreboard_example.json');

/** Data as the page loads it, from the M5 example (optionally without some files). */
function exampleData({ drop = [], scoreboard = null, fields = ['a', 'b'] } = {}) {
  const files = {
    copy: COPY,
    region: read(`${FX}region.json`),
    trials: read(`${FX}trials_steps.json`),
    ablation: read(`${FX}ablation.json`),
    ledger: read(`${FX}ledger_m5.json`),
    scoreboard,
    manifest: null,
  };
  for (const k of drop) files[k] = null;
  const f = {};
  for (const id of fields) f[id] = read(`${FX}field_${id}.json`);
  return A.normalizeData({ ...files, fields: f, base: 'data/' });
}

let REGISTRY_IDS = null;

/** Browser ids that pipeline/ledger/registry.py declares (STATIC_IDS), read from the registry itself. */
function registryBrowserIds() {
  if (REGISTRY_IDS) return REGISTRY_IDS;
  const venv = new URL('.venv/bin/python', ROOT);
  const py = existsSync(venv) ? fileURLToPath(venv) : 'python3';
  const code = [
    'import json',
    'from pipeline.ledger import registry as r',
    'print(json.dumps(sorted(i for i in r.STATIC_IDS if i.startswith("browser."))))',
  ].join('\n');
  const out = execFileSync(py, ['-c', code], { cwd: fileURLToPath(ROOT), encoding: 'utf8' });
  REGISTRY_IDS = new Set(JSON.parse(out));
  return REGISTRY_IDS;
}

function* strings(o, path = '') {
  if (typeof o === 'string') yield [path, o];
  else if (Array.isArray(o)) for (let i = 0; i < o.length; i++) yield* strings(o[i], `${path}.${i}`);
  else if (o && typeof o === 'object') for (const [k, v] of Object.entries(o)) yield* strings(v, `${path}.${k}`);
}

const COPY_TEXT = new Set([...strings(COPY.langs)].map(([, s]) => s));

/** Shown strings that carry NaN, undefined or a null that the copy itself does not hold. */
function badStrings(view) {
  const bad = [];
  for (const [p, s] of strings(view)) {
    const plain = s.replace(/<[^>]*>/g, ' ');
    if (/\b(NaN|undefined)\b/.test(plain)) bad.push([p, s]);
    else if (/\bnull\b/.test(plain) && !COPY_TEXT.has(s)) bad.push([p, s]);
  }
  return bad;
}

function ui(data, patch = {}) {
  const u = A.initialUi(data, patch.lang ?? 'en');
  return { ...u, ...patch, raw: { ...u.raw, ...(patch.raw ?? {}) }, limits: { ...u.limits, ...(patch.limits ?? {}) } };
}

// ---------------------------------------------------------------- copy and language

describe('copy and language (SA-D59)', () => {
  test('copy.json has both languages, English by default', () => {
    assert.equal(COPY.default_lang, 'en');
    assert.deepEqual(Object.keys(COPY.langs).sort(), ['en', 'pt']);
    assert.equal(A.DEFAULT_LANG, 'en');
    const data = exampleData();
    assert.equal(A.initialUi(data).lang, 'en');
  });

  test('the language switch picks the key of the other tree', () => {
    const en = A.makeT(COPY, 'en');
    const pt = A.makeT(COPY, 'pt');
    assert.equal(en('states.keep'), COPY.langs.en.states.keep);
    assert.equal(pt('states.keep'), COPY.langs.pt.states.keep);
    assert.notEqual(en('page.question.main'), pt('page.question.main'));
    assert.equal(A.makeT(COPY, 'xx')('states.keep'), COPY.langs.en.states.keep, 'unknown language falls back to English');
    const data = exampleData();
    const vEn = A.buildIndexView(data, ui(data));
    const vPt = A.buildIndexView(data, ui(data, { lang: 'pt' }));
    assert.equal(vEn.htmlLang, 'en');
    assert.equal(vPt.htmlLang, 'pt-BR');
    assert.equal(vEn.answer.state, vPt.answer.state, 'same state in both languages');
    assert.ok(vPt.answer.text.startsWith(COPY.langs.pt.states[vPt.answer.state]));
    assert.ok(vEn.answer.text.startsWith(COPY.langs.en.states[vEn.answer.state]));
  });

  test('switching language keeps every number and rewrites the decimal mark', () => {
    const data = exampleData();
    const u = ui(data, { raw: { p_corn: '57.5', n_dose: '67' } });
    const pt = A.switchLang(u, 'pt', COPY);
    assert.equal(pt.lang, 'pt');
    assert.equal(pt.raw.p_corn, '57,5');
    assert.equal(pt.raw.n_dose, '67');
    assert.deepEqual(A.currentNumbers(pt), A.currentNumbers(u));
    const back = A.switchLang(pt, 'en', COPY);
    assert.equal(back.raw.p_corn, '57.5');
  });

  test('the language is remembered only when storage works', () => {
    const mem = new Map();
    const ok = { getItem: (k) => mem.get(k) ?? null, setItem: (k, v) => mem.set(k, v) };
    assert.equal(A.storedLang(ok), null);
    assert.equal(A.storeLang(ok, 'pt'), true);
    assert.equal(A.storedLang(ok), 'pt');
    const broken = { getItem() { throw new Error('blocked'); }, setItem() { throw new Error('blocked'); } };
    assert.equal(A.storedLang(broken), null);
    assert.equal(A.storeLang(broken, 'pt'), false);
    assert.equal(A.storedLang(null), null);
    assert.equal(A.storedLang({ getItem: () => 'fr' }), null);
  });

  test('every copy key the page uses exists in both languages', () => {
    const used = new Set();
    const scenarios = situations();
    for (const [, data, u] of scenarios) {
      for (const lang of A.LANGS) {
        A.buildIndexView(data, { ...u, lang }, { used });
        A.buildHowView(data, { ...u, lang }, { used });
      }
    }
    // Families the matrix may not reach: every state, code, flag, label, seal and status.
    for (const s of D.STATES) used.add(`states.${s}`);
    for (const id of Object.keys(COPY.langs.en.labels)) used.add(`labels.${id}.title`).add(`labels.${id}.body`);
    for (const id of S.SEAL_IDS) used.add(`seals.names.${id}`).add(`seals.help.${id}`);
    const missing = [];
    for (const key of used) {
      for (const lang of A.LANGS) {
        const v = A.lookup(COPY.langs[lang], key) ?? A.PENDING_COPY[key]?.[lang];
        if (v === undefined || (typeof v === 'string' && !v.trim())) missing.push(`${lang}:${key}`);
      }
    }
    assert.deepEqual(missing, []);
    assert.ok(used.size > 150, `only ${used.size} keys reached`);
  });

  test('every data-copy key of the HTML exists in both languages', () => {
    for (const page of ['site/index.html', 'site/how-we-know.html']) {
      const keys = [...text(page).matchAll(/data-copy(?:-label|-alt)?="([^"]+)"/g)].map((m) => m[1]);
      assert.ok(keys.length > 10, page);
      for (const key of keys) {
        for (const lang of A.LANGS) assert.equal(typeof A.lookup(COPY.langs[lang], key), 'string', `${page} ${lang}:${key}`);
      }
    }
  });

  test('pending keys are still missing from the copy files, and complete', () => {
    for (const [key, v] of Object.entries(A.PENDING_COPY)) {
      for (const lang of A.LANGS) {
        assert.equal(A.lookup(COPY.langs[lang], key), undefined, `${key} is now in copy_${lang}.yaml: delete it from PENDING_COPY`);
        assert.equal(typeof v[lang], 'string', `${key} ${lang}`);
        assert.ok(!/[—–;]/.test(v[lang]), `${key} ${lang}: no dash and no semicolon`);
      }
    }
  });

  test('every result code and flag of decision.mjs has an answer or flag sentence', () => {
    const src = text('site/js/decision.mjs');
    const codes = new Set([...src.matchAll(/code: '([a-z_]+)'/g)].map((m) => m[1]));
    codes.delete('removed');
    for (const code of codes) for (const lang of A.LANGS) assert.equal(typeof A.lookup(COPY.langs[lang], A.answerKey(code)), 'string', `${lang} ${code}`);
    const flags = [...src.matchAll(/flags\.push\('([a-z_]+)'\)/g)].map((m) => m[1]);
    for (const f of flags) for (const lang of A.LANGS) assert.equal(typeof A.lookup(COPY.langs[lang], `flags.${f}`), 'string', `${lang} ${f}`);
  });

  test('placeholders are filled, and a missing one reads Unknown, never null', () => {
    const t = A.makeT(COPY, 'en');
    assert.equal(t('page.header.field_name', { X: 'A' }), 'Field A');
    assert.equal(t('page.header.field_name', { X: null }), `Field ${COPY.langs.en.missing.unknown}`);
    assert.equal(t.tIf('page.header.field_name', {}), null);
    assert.deepEqual(A.placeholders('a {N} of {n}'), ['N', 'n']);
  });
});

/** A matrix of page situations: [name, data, ui]. */
function situations() {
  const full = exampleData({ scoreboard: SCOREBOARD });
  const bare = A.normalizeData({ copy: COPY, fields: {}, base: 'data/' });
  const one = exampleData({ fields: ['a'] });
  const soil = A.normalizeData({ ...full, fields: { ...full.fields, a: soilUnknownField(full.fields.a) } });
  const out = [
    ['example A', full, ui(full)],
    ['example B', full, ui(full, { field: 'b' })],
    ['B, Zarc constraint, high corn price', full, ui(full, { field: 'b', limits: { zarc_insurance_credit: 'yes' }, raw: { p_corn: '57.5' } })],
    ['B, HLS off', full, ui(full, { field: 'b', hlsOn: false, limits: { zarc_insurance_credit: 'yes' }, raw: { p_corn: '57.5' } })],
    ['date outside', full, ui(full, { sowingDate: '2027-04-30', limits: { zarc_insurance_credit: 'yes' }, raw: { p_corn: '57.5' } })],
    ['date inside', full, ui(full, { sowingDate: '2027-01-10' })],
    ['date invalid', full, ui(full, { sowingDate: '2027-02-31', raw: { p_corn: '57.5' } })],
    ['both removed', full, ui(full, { limits: { no_narrow_row_seeder: 'no', no_grass_seeding: 'none' } })],
    ['S3 removed', full, ui(full, { limits: { no_wheat_buyer: 'no' } })],
    ['invalid number', full, ui(full, { raw: { p_soy: 'abc' } })],
    ['empty numbers', full, ui(full, { raw: Object.fromEntries(A.NUMBER_FIELDS.map((f) => [f, ''])) })],
    ['pays without gain', full, ui(full, { raw: { p_corn: '30' } })],
    ['nitrogen', full, ui(full, { raw: { n_dose: '67', urea_price: '2500' } })],
    ['priority water', full, ui(full, { priority: 'water' })],
    ['one field', one, ui(one)],
    ['no data at all', bare, ui(bare)],
    ['soil class unknown', soil, ui(soil)],
    ['soil class unknown, date', soil, ui(soil, { sowingDate: '2027-01-10' })],
  ];
  for (const id of Object.keys(COPY.langs.en.labels)) {
    const sb = { ...SCOREBOARD.scoreboard, label_id: id };
    const d = exampleData({ scoreboard: sb });
    out.push([`label ${id}`, d, ui(d)]);
  }
  return out;
}

// ---------------------------------------------------------------- state from data

describe('state from data', () => {
  test('the calendar gives the same Zarc windows as the M5 batch input', () => {
    for (const f of STATES_IN.fields) {
      const field = read(`${FX}field_${f.field_id}.json`);
      const w = A.zarcWindowOf(field);
      assert.deepEqual(w.corn, f.zarcWindow.corn, `field ${f.field_id} corn`);
      assert.deepEqual(w.grass, f.zarcWindow.grass, `field ${f.field_id} grass`);
    }
  });

  test('the page reaches the states of the M5 batch, field by field and scenario by scenario', () => {
    const data = exampleData();
    for (const row of STATES_OUT.states) {
      const sc = STATES_IN.scenarios.find((s) => s.id === row.scenario);
      const t = A.makeT(COPY, 'en');
      const raw = Object.fromEntries(Object.entries(sc.numbers).map(([k, v]) => [k, A.formatInput(v, t)]));
      for (const hlsOn of [true, false]) {
        const u = ui(data, { field: row.field_id, raw, hlsOn, limits: { zarc_insurance_credit: STATES_IN.context.zarcConstraint ? 'yes' : '' } });
        const r = A.decide(data, u);
        const want = hlsOn ? row.hls_on : row.hls_off;
        assert.equal(r[row.option].state, want.state, `${row.field_id} ${row.scenario} ${row.option} hls ${hlsOn}`);
        assert.equal(r[row.option].code, want.code);
      }
    }
  });

  test('Field A opens on the example numbers and the answer of the base scenario', () => {
    const data = exampleData();
    const u = A.initialUi(data);
    assert.equal(u.field, 'a');
    assert.equal(A.isExampleState(data, u), true);
    const v = A.buildIndexView(data, u);
    const r = A.decide(data, u);
    assert.equal(v.answer.state, r.answer.state);
    const main = r[r.answer.main];
    const want = A.makeT(COPY, 'en')(A.answerKey(r.answer.code), {
      Option: COPY.langs.en.page.strategies[main.option].name,
      G: A.formatNumber(main.G, A.makeT(COPY, 'en'), 1),
      N: String(main.N),
      n: String(main.n),
    });
    assert.equal(v.answer.text, want);
    assert.ok(v.answer.other.includes(COPY.langs.en.page.strategies[r.answer.other].name));
    assert.equal(v.example, true);
    assert.equal(v.inputs.every((i) => i.example), true, 'every number tagged example, fictitious');
    assert.equal(v.isExample, true, 'the M5 example data declares itself, so the banner shows');
  });

  test('editing a number drops the example tag and says On your numbers', () => {
    const data = exampleData();
    const v = A.buildIndexView(data, ui(data, { raw: { p_soy: '120' } }));
    assert.equal(v.example, false);
    assert.equal(v.nasa.onYourNumbers, COPY.langs.en.page.nasa_switch.on_your_numbers);
    assert.equal(v.inputs.find((i) => i.id === 'p_soy').example, false);
  });

  test('an invalid number never gives a verdict', () => {
    const data = exampleData();
    const v = A.buildIndexView(data, ui(data, { raw: { p_soy: 'abc' } }));
    assert.equal(v.answer.state, 'not_enough');
    assert.equal(v.answer.text, A.makeT(COPY, 'en')('answers.invalid_field', { field: COPY.langs.en.fields.p_soy }));
    assert.equal(v.inputs.find((i) => i.id === 'p_soy').invalid, true);
  });

  test('a limit removes the option and says why, from the copy', () => {
    const data = exampleData();
    const v = A.buildIndexView(data, ui(data, { limits: { no_wheat_buyer: 'no' } }));
    const s3 = v.columns.find((c) => c.id === 'S3');
    assert.equal(s3.head.badge.state, 'removed');
    const l4 = s3.cells.find((c) => c.id === 'L4').lines.map((l) => l.text).join(' ');
    assert.ok(l4.includes(COPY.langs.en.page.limits.reasons.no_wheat_buyer), l4);
    const rented = A.buildIndexView(data, ui(data, { limits: { rented_land: 'rented' } }));
    assert.equal(rented.answer.text, A.buildIndexView(data, ui(data)).answer.text, 'rented land is a sheet item only');
  });

  test('with both options removed the answer is keep, with both reasons', () => {
    const data = exampleData();
    const v = A.buildIndexView(data, ui(data, { limits: { no_narrow_row_seeder: 'no', no_grass_seeding: 'none' } }));
    assert.equal(v.answer.state, 'keep');
    assert.equal(v.answer.text, COPY.langs.en.answers.both_removed);
    assert.equal(v.answer.reasons.length, 2);
  });

  test('rule 6: the warning asks for the date, and the date is in charge', () => {
    const data = exampleData();
    const base = { field: 'b', limits: { zarc_insurance_credit: 'yes' }, raw: { p_corn: '57.5' } };
    const need = A.buildIndexView(data, ui(data, base));
    assert.equal(need.answer.state, 'not_enough');
    assert.equal(need.answer.text, COPY.langs.en.answers.need_sowing_date);
    const off = A.buildIndexView(data, ui(data, { ...base, hlsOn: false }));
    assert.equal(off.answer.state, 'keep');
    assert.ok(off.nasa.X >= 1 && off.nasa.changed, 'the switch counts the change');
    assert.equal(off.columns[0].head.warning, null, 'no window warning with HLS off');
    assert.equal(off.answer.fieldOrder, null, 'no field order with HLS off');
    const dated = A.buildIndexView(data, ui(data, { ...base, sowingDate: '2027-01-10' }));
    assert.equal(dated.answer.state, 'keep');
    assert.ok(dated.answer.notes.includes(COPY.langs.en.flags.date_in_charge));
    assert.equal(dated.nasa.dateInCharge, COPY.langs.en.page.nasa_switch.date_in_charge);
    const outside = A.buildIndexView(data, ui(data, { ...base, sowingDate: '2027-04-30' }));
    assert.equal(outside.answer.text, COPY.langs.en.answers.window_outside);
  });

  test('the NASA switch counts field and option states, and says context only at zero', () => {
    const data = exampleData();
    const u = ui(data);
    const sw = A.switchCount(data, u);
    assert.equal(sw.Y, data.fieldIds.length * D.OPTIONS.length);
    const v = A.buildIndexView(data, u);
    assert.equal(v.nasa.short, A.makeT(COPY, 'en')('page.nasa_switch.short', { X: String(sw.X), Y: String(sw.Y) }));
    if (sw.X === 0) assert.equal(v.nasa.contextOnly, COPY.langs.en.page.nasa_switch.context_only);
  });

  test('the window warning follows the reading, never says inside', () => {
    const t = A.makeT(COPY, 'en');
    const b = read(`${FX}field_b.json`);
    const w = A.windowWarning(t, b, true);
    assert.equal(w.state, 'outside');
    assert.equal(w.text, t('flags.window_warning', { k: '4', n: '6' }));
    assert.equal(A.windowWarning(t, b, false), null);
    assert.equal(A.windowWarning(t, { window_reading: null }, true).text, COPY.langs.en.window.states.no_data);
    for (const [, s] of strings(A.buildIndexView(exampleData(), ui(exampleData())))) assert.ok(!/\binside\b/i.test(s), s);
  });

  test('priority only highlights a row and never changes a state', () => {
    const data = exampleData();
    const states = A.PRIORITIES.map((p) => {
      const v = A.buildIndexView(data, ui(data, { priority: p }));
      const hl = v.rows.filter((r) => r.highlight).map((r) => r.id);
      assert.deepEqual(hl, [{ income: 'L4', water: 'L2', soil: 'L1' }[p]]);
      return [v.answer.state, ...v.columns.map((c) => c.head.badge?.state ?? null)];
    });
    assert.deepEqual(states[1], states[0]);
    assert.deepEqual(states[2], states[0]);
  });

  test('three price scenarios: corn price k lower and k higher than the typed base', () => {
    const data = exampleData();
    assert.equal(A.scenarioK(data.region), 0.15);
    const v = A.buildIndexView(data, ui(data));
    assert.deepEqual(v.scenarios.list.map((s) => s.id), ['low', 'base', 'high']);
    const fromBatch = (sid) => STATES_OUT.answers?.find((a) => a.field_id === 'a' && a.scenario === sid);
    if (STATES_OUT.answers) {
      const ids = { low: 'example_low', base: 'example_mid', high: 'example_high' };
      for (const s of v.scenarios.list) {
        const want = fromBatch(ids[s.id]);
        if (want) assert.equal(s.answerState, want.hls_on.state, s.id);
      }
    }
  });

  test('labels: L5 only under the fourth label, the model check always prefixed', () => {
    for (const id of Object.keys(COPY.langs.en.labels)) {
      const data = exampleData({ scoreboard: { ...SCOREBOARD.scoreboard, label_id: id } });
      const a = read(`${FX}field_a.json`);
      data.fields.a = { ...a, next_soy: { ...a.next_soy, state: 'inconclusive', on_main_page: true } };
      const v = A.buildIndexView(data, ui(data));
      assert.equal(v.answer.modelCheck, A.makeT(COPY, 'en')('page.answer.model_check', { label: COPY.langs.en.labels[id].title }));
      assert.ok(v.answer.modelCheck.startsWith('Crop-sequence model check'));
      assert.equal(v.rows.some((r) => r.id === 'L5'), id === A.L5_LABEL, id);
    }
    const none = exampleData();
    assert.equal(A.labelId(none), 'not_completed', 'no scoreboard.json reads not completed');
  });

  test('missing files show Unknown, no data or not completed, never NaN, undefined or null', () => {
    for (const [name, data, u] of situations()) {
      for (const lang of A.LANGS) {
        const v = A.buildIndexView(data, { ...u, lang });
        const h = A.buildHowView(data, { ...u, lang });
        assert.deepEqual(badStrings(v), [], `${name} ${lang} index`);
        assert.deepEqual(badStrings(h), [], `${name} ${lang} how`);
      }
    }
    const bare = A.normalizeData({ copy: COPY, fields: {}, base: 'data/' });
    const v = A.buildIndexView(bare, A.initialUi(bare));
    assert.equal(v.noFieldText, COPY.langs.en.missing.no_data);
    assert.equal(v.yearStrip.empty, COPY.langs.en.missing.no_data);
    assert.equal(A.buildHowView(bare, A.initialUi(bare)).currentLabel.includes(COPY.langs.en.labels.not_completed.title), true);
  });

  test('without Field B there is no field order', () => {
    const data = exampleData({ fields: ['a'] });
    const v = A.buildIndexView(data, ui(data));
    assert.ok(v.answer.fieldOrder.endsWith(COPY.langs.en.page.field_order.one_field));
  });

  test('the fixed notices of M7.6 reach the main page view or its HTML', () => {
    const v = A.buildIndexView(exampleData(), ui(exampleData()));
    const html = text('site/index.html');
    const shown = new Set([...strings(v)].map(([, s]) => s));
    for (const m of html.matchAll(/data-copy="([^"]+)"/g)) shown.add(A.lookup(COPY.langs.en, m[1]));
    const all = [...shown].join('\n');
    for (const phrase of [
      'Counts of trials from 3 sites, not your chance',
      'Not a forecast for your field',
      'All numbers per hectare',
      'Not visible from orbit: roots, nitrogen, nematodes, disease, yield',
      'Supports, does not replace local judgment',
      'Example field, fictitious farm',
      'These trials come from 3 sites on clay above 750 g/kg',
      'This tool is independent',
      'This effect is not calculated',
      'First evidence region: northern Paraná, Brazil',
      'No field trials, no verdict. The rule travels. The verdict does not.',
      'Trials from 3 sites: Londrina, Floresta and Cafelândia',
      'Crop-sequence model check',
      'It does not show that the answer is right',
    ]) assert.ok(all.includes(phrase), phrase);
  });

  test('the sheet link carries the state in the fragment only', () => {
    const data = exampleData();
    const v = A.buildIndexView(data, ui(data, { sowingDate: '2027-01-10', limits: { rented_land: 'rented' } }));
    assert.ok(v.sheetHref.startsWith('sheet.html#'));
    assert.ok(!v.sheetHref.includes('?'));
    const p = new URLSearchParams(v.sheetHref.split('#')[1]);
    assert.equal(p.get('field'), 'a');
    assert.equal(p.get('sowing_date'), '2027-01-10');
    assert.equal(p.get('rented_land'), 'rented');
    assert.equal(Number(p.get('p_soy')), 100);
    assert.equal(p.get('option'), A.decide(data, ui(data)).answer.main);
    const none = new URLSearchParams(A.sheetHash(ui(data), null));
    assert.equal(none.get('option'), null, 'no option when both are removed');
  });
});

// ---------------------------------------------------------------- numbers, dates, data base

describe('numbers, dates and the data folder', () => {
  test('numbers typed in each language', () => {
    assert.equal(A.parseNumber('42,5', 'pt'), 42.5);
    assert.equal(A.parseNumber('1.400', 'pt'), 1400);
    assert.equal(A.parseNumber('1.400,5', 'pt'), 1400.5);
    assert.equal(A.parseNumber('42.5', 'pt'), 42.5);
    assert.equal(A.parseNumber('1,400', 'en'), 1400);
    assert.equal(A.parseNumber('42.5', 'en'), 42.5);
    assert.equal(A.parseNumber(' 3000 ', 'en'), 3000);
    assert.equal(A.parseNumber('', 'en'), undefined);
    assert.ok(Number.isNaN(A.parseNumber('abc', 'en')));
    assert.ok(Number.isNaN(A.parseNumber('1e3', 'en')));
    assert.ok(Number.isNaN(A.parseNumber('1,40.5', 'en')));
    assert.equal(A.parseNumber('-5', 'en'), -5, 'parsed, then refused by the domain check');
  });

  test('numbers and dates shown in each language', () => {
    const en = A.makeT(COPY, 'en');
    const pt = A.makeT(COPY, 'pt');
    assert.equal(A.formatNumber(2000, en), '2,000');
    assert.equal(A.formatNumber(2000, pt), '2.000');
    assert.equal(A.formatNumber(3.25, pt, 1), '3,3');
    assert.equal(A.formatNumber(4, en, 1), '4');
    assert.equal(A.formatNumber(NaN, en), COPY.langs.en.missing.unknown);
    assert.equal(A.formatNumber(null, pt), COPY.langs.pt.missing.unknown);
    assert.equal(A.formatDate('2026-02-12', en), `12 ${COPY.langs.en.page.year_strip.months[1]} 2026`);
    assert.equal(A.formatDate('bad', en), COPY.langs.en.missing.unknown);
  });

  test('nitrogen exposure is dose / 0.46 x urea price, apart from G', () => {
    assert.equal(A.nitrogenExposure(46, 1000), 100);
    assert.equal(A.nitrogenExposure(undefined, 1000), null);
    assert.equal(A.nitrogenExposure(-1, 1000), null);
  });

  test('the data folder is relative and never another origin', () => {
    assert.equal(A.dataBase(''), 'data/');
    assert.equal(A.dataBase('?data=../tests/fixtures/site_example'), '../tests/fixtures/site_example/');
    for (const bad of ['https://example.org/', '//example.org/x/', '/abs/', 'javascript:alert(1)', 'data:text/plain,x', 'a\\b']) {
      assert.equal(A.dataBase(`?data=${encodeURIComponent(bad)}`), 'data/', bad);
    }
  });

  test('links between the pages keep a ?data= folder (rehearsal regression)', () => {
    // The rehearsal page (?data=../data/rehearsal/site_data/) linked to how-we-know.html and
    // sheet.html without the query, so both opened on site/data/ with no field.
    const base = A.dataBase('?data=../data/rehearsal/site_data/');
    assert.equal(A.dataQuery('data/'), '');
    assert.equal(A.pageHref('how-we-know.html', 'data/'), 'how-we-know.html');
    const href = A.pageHref('how-we-know.html', base);
    assert.equal(A.dataBase(href.slice(href.indexOf('?'))), base, 'the next page reads the same folder');
    const data = { ...exampleData(), base };
    const v = A.buildIndexView(data, ui(data));
    const [path, hash] = v.sheetHref.split('#');
    assert.equal(A.dataBase(path.slice(path.indexOf('?'))), base);
    assert.equal(new URLSearchParams(hash).get('field'), 'a', 'the state stays in the fragment');
    const html = text('site/js/app.mjs');
    assert.match(html, /querySelectorAll\('a\[href="index.html"\], a\[href="how-we-know.html"\]'\)/, 'boot rewrites the static page links');
  });

  test('loadAll reads copy.json from data/ and the rest from the data folder', async () => {
    const asked = [];
    const data = await A.loadAll(async (url) => {
      asked.push(url);
      return url.endsWith('copy.json') ? COPY : null;
    }, 'x/');
    assert.ok(asked.includes('data/copy.json'));
    assert.ok(asked.includes('x/region.json'));
    assert.ok(asked.includes('x/field_a.json'));
    assert.deepEqual(data.fieldIds, []);
    assert.equal(data.isExample, true, 'a data folder other than data/ is always labelled example');
  });

  test('the site example fixture is the M5 example, unchanged', () => {
    const pairs = [
      ['field_a.json', 'field_a.json'],
      ['field_b.json', 'field_b.json'],
      ['region.json', 'region.json'],
      ['trials_steps.json', 'trials_steps.json'],
      ['ablation.json', 'ablation.json'],
      ['ledger.json', 'ledger_m5.json'],
    ];
    for (const [mine, theirs] of pairs) {
      const p = `tests/fixtures/site_example/${mine}`;
      if (!existsSync(new URL(p, ROOT))) continue;
      assert.deepEqual(read(p), read(`${FX}${theirs}`), `${mine}: copy it again from ${FX}${theirs}`);
    }
  });
});

// ---------------------------------------------------------------- SVG and ledger

describe('svg.mjs and ledger.mjs', () => {
  test('ten-day slots on both axes', () => {
    assert.equal(S.slotJan(1, 1), 0);
    assert.equal(S.slotJan(1, 11), 1);
    assert.equal(S.slotJan(12, 31), 35);
    assert.equal(S.slotSep(9, 1), 0);
    assert.equal(S.slotSep(8, 21), 35);
    assert.equal(S.tenDayOf(20), 1);
    assert.equal(S.tenDayOf(21), 2);
  });

  test('a calendar segment across 31 Dec wraps onto January', () => {
    const slots = S.segmentsToSlots([{ crop: 'soybean', start: '2026-10-01', end: '2027-02-10', state: 'growing' }]);
    assert.equal(slots[S.slotJan(10, 1)], 'soybean');
    assert.equal(slots[S.slotJan(1, 15)], 'soybean');
    assert.equal(slots[S.slotJan(2, 1)], 'soybean');
    assert.equal(slots[S.slotJan(2, 11)], null);
    assert.equal(slots[S.slotJan(6, 1)], null);
  });

  test('green cycles: two separate runs read two, no data neither splits nor joins', () => {
    assert.equal(S.greenCycles(['green', 'green', 'not_green', 'green']), 2);
    assert.equal(S.greenCycles(['green', 'no_data', 'green']), 1);
    assert.equal(S.greenCycles(['not_green', 'no_data']), 0);
    const a = read(`${FX}field_a.json`);
    assert.equal(S.greenCycles(a.ribbon.seasons[0].cells), 2);
  });

  test('the ribbon row has 36 cells with different marks for no data and no green', () => {
    const cells = [...new Array(18).fill('green'), ...new Array(9).fill('not_green'), ...new Array(8).fill('no_data'), 'to_confirm'];
    const svg = S.ribbonRowSvg({ cells, season: '2025/26', exit: { low: '2026-02-12', high: '2026-02-27' }, zarcEnd: '2027-02-28', label: 'x' });
    assert.equal((svg.match(/class="cell /g) ?? []).length, 36);
    assert.ok(svg.includes('url(#pat-nodata)'));
    assert.ok(svg.includes('url(#pat-confirm)'));
    assert.ok(svg.includes('class="exit-bar"'));
    assert.ok(svg.includes('class="zarc-end"'));
    assert.ok(S.defsSvg().includes('id="pat-nodata"') && S.defsSvg().includes('id="pat-nocrop"'));
  });

  test('icons and charts hold no URL and escape their labels', () => {
    const all = [
      ...D.STATES.map(S.stateIcon),
      ...S.SEAL_IDS.map(S.sealIcon),
      S.defsSvg(),
      S.yearRowSvg({ crops: new Array(36).fill('corn'), label: '<b>"x"</b>' }),
      S.stepsSvg({ steps: [{ g_sc_ha: 1, count: 3 }], n: 5, G: 1.5, label: 'a&b' }),
      S.fieldMapSvg({ fields: [{ id: 'a', polygon: [[0, 0], [10, 0], [10, 10]], active: true }], label: 'map' }),
    ].join('');
    assert.ok(!/https?:|\/\//.test(all));
    assert.ok(all.includes('&lt;b&gt;&quot;x&quot;&lt;/b&gt;'));
    assert.ok(all.includes('a&amp;b'));
  });

  test('ledger rows of a record, and of a missing one', () => {
    const led = read(`${FX}ledger_m5.json`);
    const rec = L.recordsOf(led)['field_a.risk'];
    const rows = L.recordRows('field_a.risk', rec, { sealName: (x) => `seal:${x}`, unknown: '?' });
    assert.deepEqual(rows.map((r) => r.key), [...L.RECORD_FIELDS]);
    assert.equal(rows.find((r) => r.key === 'evidence_class').text, 'seal:reanalysis');
    assert.equal(rows.find((r) => r.key === 'value').text, '?');
    const none = L.recordRows('nope', undefined, { unknown: '?' });
    assert.deepEqual(none.map((r) => r.text), ['nope', '?']);
    assert.deepEqual(L.recordsOf(null), {});
  });

  test('browser ledger ids are the ids pipeline/ledger/registry.py declares', () => {
    const declared = registryBrowserIds();
    assert.ok(declared.size > 0, 'the registry declares browser records');
    for (const [key, id] of Object.entries(A.BROWSER_LEDGER)) {
      if (A.PENDING_LEDGER.includes(id)) {
        assert.ok(!declared.has(id), `${id} is now in registry.STATIC_IDS: delete it from PENDING_LEDGER`);
        assert.equal(A.browserLedger(key), null, `${id} pending: the page shows no link to an empty record`);
      } else {
        assert.ok(declared.has(id), `${key}: ${id} is not declared by the registry`);
        assert.equal(A.browserLedger(key), id);
      }
    }
    for (const id of A.PENDING_LEDGER) assert.ok(Object.values(A.BROWSER_LEDGER).includes(id), id);
  });

  test('every ledger id the example pages show is in the M5 ledger or declared by the registry', () => {
    const declared = registryBrowserIds();
    const ids = new Set();
    const walk = (o) => {
      if (Array.isArray(o)) o.forEach(walk);
      else if (o && typeof o === 'object') {
        for (const [k, x] of Object.entries(o)) {
          if (/^(ledger|[a-z]\w*Ledger)$/.test(k) && typeof x === 'string') ids.add(x);
          else walk(x);
        }
      }
    };
    for (const [, data, u] of [...situations(), ...orderSituations()]) {
      walk(A.buildIndexView(data, u));
      walk(A.buildHowView(data, u));
    }
    for (const id of ['browser.margin_corn', 'browser.S3.G', 'browser.S2.G']) assert.ok(ids.has(id), `${id} reached`);
    const data = exampleData();
    const known = new Set([...Object.keys(L.recordsOf(data.ledger)), ...(data.ledger.external ?? []), ...declared]);
    assert.deepEqual([...ids].filter((id) => !known.has(id)), []);
  });
});

// ---------------------------------------------------------------- field order (8.7, 13.7 rule 5)

/** The example with the two region.json keys of the field order (fictitious values). */
function orderData({ noise = { v: 3, l: 'region.noise_floor' }, seasons = ['2025/26'], fields = ['a', 'b'], drop = [] } = {}) {
  const d = exampleData({ fields });
  const region = { ...d.region, noise_floor: noise, choice_seasons: seasons };
  for (const k of drop) delete region[k];
  return A.normalizeData({ ...d, region });
}

function orderSituations() {
  const d = orderData();
  return [
    ['order, base', d, ui(d)],
    ['order, wheat pays without gain', d, ui(d, { raw: { p_corn: '30' } })],
    ['order, field B', d, ui(d, { field: 'b' })],
  ];
}

describe('field order, computed in the browser (8.7, 13.7 rule 5)', () => {
  const t = A.makeT(COPY, 'en');
  const lineOf = (v) => v.answer.fieldOrder;
  const firstLine = (X, Y, N, M) => t('page.answer.which_field_first', { line: t('page.field_order.first', { X, Y, N, M }) });
  const noneLine = t('page.answer.which_field_first', { line: COPY.langs.en.page.field_order.none });

  test('the page calls fieldOrder with every green_end, the state on each field, the floor and the choice seasons', () => {
    const data = orderData();
    for (const patch of [{}, { raw: { p_corn: '30' } }, { field: 'b' }]) {
      const u = ui(data, patch);
      const option = A.decide(data, u).answer.main;
      const states = Object.fromEntries(data.fieldIds.map((id) => [id, A.decide(data, { ...u, field: id })[option].state]));
      const greenEnd = Object.fromEntries(data.fieldIds.map((id) => [id, data.fields[id].green_end]));
      assert.deepEqual(A.fieldOrderOf(data, u, option), D.fieldOrder(greenEnd, 3, states, ['2025/26']), JSON.stringify(patch));
    }
  });

  test('a field above the noise floor in more than half of the comparable seasons goes first', () => {
    const data = orderData();
    const v = A.buildIndexView(data, ui(data));
    const fo = A.fieldOrderOf(data, ui(data), A.decide(data, ui(data)).answer.main);
    assert.equal(fo.first, 'b');
    assert.deepEqual([fo.n, fo.m], [4, 5]);
    assert.equal(lineOf(v), firstLine('B', 'A', '4', '5'));
    assert.equal(v.answer.fieldOrderLedger, A.browserLedger('field_order'));
    const pt = A.buildIndexView(data, ui(data, { lang: 'pt' }));
    assert.ok(pt.answer.fieldOrder.includes(COPY.langs.pt.page.field_order.first.split('{')[0].trim()));
  });

  test('the seasons used to choose the fields stay out, and the floor is the one of region.json', () => {
    const all = A.fieldOrderOf(orderData({ seasons: [] }), ui(exampleData()), 'S3');
    assert.deepEqual([all.first, all.n, all.m], ['b', 4, 6], 'with 2025/26 counted, 6 comparable seasons');
    const high = orderData({ noise: { v: 13, l: 'region.noise_floor' } });
    assert.equal(lineOf(A.buildIndexView(high, ui(high))), noneLine, '2 of 5 seasons above 13 days is not more than half');
  });

  test('without noise_floor or choice_seasons in region.json, no field stands out', () => {
    for (const d of [exampleData(), orderData({ drop: ['noise_floor'] }), orderData({ noise: { v: null, l: 'x' } })]) {
      assert.equal(lineOf(A.buildIndexView(d, ui(d))), noneLine);
    }
    const noSeasons = orderData({ drop: ['choice_seasons'] });
    assert.equal(A.fieldOrderOf(noSeasons, ui(noSeasons), 'S3').m, 6, 'no choice season listed: none left out');
  });

  test('only fields where the option is Try or Tied are compared', () => {
    const data = orderData();
    // S3 removed: the main sentence is S2, not enough evidence on both fields.
    const s2 = A.buildIndexView(data, ui(data, { limits: { no_wheat_buyer: 'no' } }));
    assert.equal(lineOf(s2), noneLine);
    const both = A.buildIndexView(data, ui(data, { limits: { no_narrow_row_seeder: 'no', no_grass_seeding: 'none' } }));
    assert.equal(lineOf(both), noneLine);
    assert.equal(A.fieldOrderOf(data, ui(data), null).reason, 'fewer_than_two_fields');
  });

  test('a declared sowing date or the HLS switch off removes the order, one field has none', () => {
    const data = orderData();
    const dated = A.buildIndexView(data, ui(data, { sowingDate: '2027-01-10' }));
    assert.equal(lineOf(dated), t('page.answer.which_field_first', { line: COPY.langs.en.flags.date_in_charge }));
    assert.equal(dated.answer.fieldOrderLedger, null);
    const off = A.buildIndexView(data, ui(data, { hlsOn: false }));
    assert.equal(lineOf(off), null);
    assert.equal(off.answer.fieldOrderLedger, null);
    const one = orderData({ fields: ['a'] });
    assert.ok(lineOf(A.buildIndexView(one, ui(one))).endsWith(COPY.langs.en.page.field_order.one_field));
  });

  test('the field_order precomputed in field_<id>.json is never read', () => {
    const data = orderData({ drop: ['noise_floor'] });
    const forged = { ...data, fields: { ...data.fields, a: { ...data.fields.a, field_order: { first: 'field_a', other: ['field_b'], n: 9, m: 9 } } } };
    assert.equal(lineOf(A.buildIndexView(A.normalizeData(forged), ui(data))), noneLine);
  });
});

// ---------------------------------------------------------------- basemap and H2

describe('basemap and the local review', () => {
  // Shape of data/interim/basemap_<window id>.json (pipeline/fields/basemap.py georef), copied by make.
  const GEOREF = {
    width: 347, height: 374, crs_epsg: 32622, crs_wkt: 'PROJCS[...]',
    transform: [30, 0, 467130, 0, -30, -2546790], pixel_size_m: [30, 30],
    bounds: { left: 467130, bottom: -2558010, right: 477540, top: -2546790 },
  };

  test('the basemap shows only with its georeference and a PNG of the declared size', () => {
    assert.deepEqual(A.BASEMAP, { png: 'img/basemap.png', georef: 'img/basemap.json' });
    assert.deepEqual(A.basemapImage(GEOREF, { width: 347, height: 374 }), { href: 'img/basemap.png', width: 347, height: 374 });
    assert.equal(A.basemapImage(GEOREF, { width: 0, height: 0 }), null, 'PNG missing or broken');
    assert.equal(A.basemapImage(GEOREF, { width: 347, height: 374 }, '../data/rehearsal/site_data/img/basemap.png').href,
      '../data/rehearsal/site_data/img/basemap.png', 'the loaded PNG, not the published one');
    assert.equal(A.basemapImage(GEOREF, null), null);
    assert.equal(A.basemapImage(GEOREF, { width: 348, height: 374 }), null, 'another PNG than the polygons were drawn on');
    assert.equal(A.basemapImage(null, { width: 347, height: 374 }), null, 'img/basemap.json missing');
    assert.equal(A.basemapImage({ ...GEOREF, width: '347' }, { width: 347, height: 374 }), null);
    assert.equal(A.basemapImage({ ...GEOREF, transform: [30, 1, 0, 1, -30, 0] }, { width: 347, height: 374 }), null, 'rotated grid');
    assert.equal(A.basemapImage({ ...GEOREF, transform: undefined }, { width: 347, height: 374 }), null);
  });

  test('without the basemap the map draws the polygons alone, with it the PNG sits under them', () => {
    const data = exampleData();
    const bare = A.buildIndexView(data, ui(data));
    assert.ok(bare.map.includes('class="map-bg"') && !bare.map.includes('<image'));
    assert.equal((bare.map.match(/class="field-poly/g) ?? []).length, 2);
    const withMap = A.buildIndexView(data, ui(data, { mapImage: A.basemapImage(GEOREF, { width: 347, height: 374 }) }));
    assert.ok(withMap.map.includes('<image href="img/basemap.png" x="0" y="0" width="347" height="374"/>'));
    assert.ok(withMap.map.includes('viewBox="0 0 347 374"'));
  });

  test('H2 counts as a local review only with pass, fail or reported and a literal comment', () => {
    const h2 = SCOREBOARD.scoreboard.tests.find((x) => x.id === 'H2');
    assert.equal(h2.status, 'not_run', 'SA-D56');
    const sb = (entry) => ({ ...SCOREBOARD.scoreboard, tests: SCOREBOARD.scoreboard.tests.map((x) => (x.id === 'H2' ? { ...x, ...entry } : x)) });
    const how = (s) => A.buildHowView(exampleData({ scoreboard: s }), ui(exampleData())).noLocalReview;
    const no = COPY.langs.en.howweknow.no_local_review;
    assert.equal(how(null), no);
    assert.equal(how(SCOREBOARD.scoreboard), no);
    assert.equal(A.localReviewDone(SCOREBOARD), false);
    for (const status of A.H2_REVIEW_STATUSES) {
      assert.equal(how(sb({ status, note: null })), no, `${status} without a comment`);
      assert.equal(how(sb({ status, note: '  ' })), no, `${status} with a blank comment`);
      assert.equal(how(sb({ status, note: 'Read in a personal capacity.' })), null, `${status} with a comment`);
    }
    for (const status of ['not_run', 'not_completed', 'exploratory', 'PASS']) assert.equal(how(sb({ status, note: 'x' })), no, status);
    assert.deepEqual([...A.H2_REVIEW_STATUSES], ['pass', 'fail', 'reported']);
  });
});

// ---------------------------------------------------------------- soil class and trial summaries

/** Field A as M5 writes it without a Zarc soil class (zarc_ad_status "unknown", no soil report). */
function soilUnknownField(f) {
  const blank = (x) => (x.state === 'zarc_window' || x.state === 'zarc_previous_table' ? { ...x, start: null, end: null, state: 'soil_class_unknown' } : x);
  return {
    ...f,
    soil: { ...f.soil, zarc_ad_class: null, zarc_ad_status: 'unknown' },
    risk: { ...f.risk, zarc_class: Object.fromEntries(Object.entries(f.risk.zarc_class).map(([k, v]) => [k, { ...v, risk_min: null, risk_max: null, state: 'soil_class_unknown' }])) },
    calendar: Object.fromEntries(Object.entries(f.calendar).map(([k, col]) => [k, col.map(blank)])),
    window_reading: { ...f.window_reading, state: 'at_the_edge', k: null, n: null, zarc_window_end: null },
  };
}

describe('soil class unknown and the trial summaries', () => {
  const soilData = () => {
    const d = exampleData();
    return A.normalizeData({ ...d, fields: { ...d.fields, a: soilUnknownField(d.fields.a) } });
  };
  const l2 = (v, sid) => v.columns.find((c) => c.id === sid).cells.find((c) => c.id === 'L2').lines[0].text;
  const l3 = (v, sid) => v.columns.find((c) => c.id === sid).cells.find((c) => c.id === 'L3').lines;

  test('without a Zarc soil class every column, the warning and the year strip say so', () => {
    const data = soilData();
    assert.equal(A.zarcSoilUnknown(data.fields.a), true);
    assert.equal(A.zarcSoilUnknown(data.fields.b), false);
    for (const lang of A.LANGS) {
      const t = A.makeT(COPY, lang);
      const v = A.buildIndexView(data, ui(data, { lang }));
      for (const [sid, crop] of [['S1', 'corn'], ['S2', 'grass'], ['S3', 'wheat']]) {
        // SA-D72.6: without a report L2 shows the range across every class and cycle group, never "unknown".
        const anyR = data.fields.a.risk.zarc_class[sid].any_class;
        const cls = anyR.risk_min === anyR.risk_max ? t('page.rows.L2.any_class_one', { a: String(anyR.risk_min) })
          : t('page.rows.L2.any_class_range', { a: String(anyR.risk_min), b: String(anyR.risk_max) });
        assert.equal(l2(v, sid), t('page.rows.L2.zarc_class', { crop: t(`page.rows.L2.crops.${crop}`), class: cls }), `${lang} ${sid}`);
        assert.ok(!l2(v, sid).includes(COPY.langs[lang].missing.unknown), 'never a blank Unknown');
      }
      // SA-D64: without a soil report the warning reads the envelope of every class ("for any soil class").
      const w = v.columns[0].head.warning;
      assert.equal(w.state, 'at_the_edge');
      assert.equal(w.anyClass, true);
      assert.equal(w.text, t('window.at_the_edge_any_class', { k: '1', n: '7' }));
      assert.equal(w.ledger, 'field_a.window_reading');
      assert.equal(v.columns[0].head.warningSeal.id, 'estimated_from_satellite');
      // A field of the older schema (no window_basis) keeps the soil gap sentence.
      const old = A.normalizeData({ ...data, fields: { ...data.fields, a: { ...data.fields.a, window_basis: undefined } } });
      const wo = A.buildIndexView(old, ui(old, { lang })).columns[0].head.warning;
      assert.equal(wo.state, 'soil_class_unknown');
      assert.equal(wo.text, COPY.langs[lang].window.soil_class_unknown);
      assert.equal(wo.ledger, 'field_a.soil');
      assert.equal(v.columns[2].head.warning, null, 'S3 has no corn window warning');
      assert.equal(v.yearStrip.zarcNote.text, COPY.langs[lang].window.soil_class_unknown);
      assert.deepEqual(badStrings(v), []);
    }
    const off = A.buildIndexView(data, ui(data, { hlsOn: false }));
    assert.equal(off.columns[0].head.warning, null, 'the satellite slot goes with HLS off');
    assert.ok(off.yearStrip.zarcNote && l2(off, 'S1').endsWith(COPY.langs.en.page.rows.L2.any_class_range.replace('{a}', '20').replace('{b}', '40')), 'the range stays');
    const dated = A.buildIndexView(data, ui(data, { sowingDate: '2027-01-10' }));
    assert.ok(dated.answer.notes.includes(COPY.langs.en.flags.zarc_window_unknown), 'a declared date cannot be checked either');
    const b = A.buildIndexView(data, ui(data, { field: 'b' }));
    assert.equal(b.yearStrip.zarcNote, null);
    assert.notEqual(b.columns[0].head.warning.state, 'soil_class_unknown');
    assert.equal(b.columns[0].head.warning.anyClass, false, 'field B declares AD3 (fictitious report)');
  });

  test('the soil class shows in the language of the page', () => {
    const d = exampleData();
    const detail = { code: 'very_clayey', name_en: 'very clayey', name_pt: 'muito argilosa', estimated: true };
    const data = A.normalizeData({ ...d, fields: { ...d.fields, a: { ...d.fields.a, soil: { ...d.fields.a.soil, estimated_class: 'very clayey', estimated_class_detail: detail } } } });
    assert.equal(A.buildIndexView(data, ui(data)).soil.text, A.makeT(COPY, 'en')('page.rows.soil.line', { class: 'very clayey' }));
    assert.equal(A.buildIndexView(data, ui(data, { lang: 'pt' })).soil.text, A.makeT(COPY, 'pt')('page.rows.soil.line', { class: 'muito argilosa' }));
    const none = A.normalizeData({ ...d, fields: { ...d.fields, a: { ...d.fields.a, soil: { ...d.fields.a.soil, estimated_class: null, estimated_class_detail: null } } } });
    assert.ok(A.buildIndexView(none, ui(none)).soil.text.includes(COPY.langs.en.missing.unknown));
  });

  test('L3 lines use the reference and the summaries of trials_steps.json, with their ledger ids', () => {
    const data = exampleData();
    const tr = data.trials;
    for (const lang of A.LANGS) {
      const t = A.makeT(COPY, lang);
      const v = A.buildIndexView(data, ui(data, { lang }));
      const pct = (x) => t('meta.percent', { v: A.formatNumber(x.v, t, Number.isInteger(x.v) ? 0 : 1) });
      const s1 = l3(v, 'S1');
      assert.deepEqual(s1[0], { text: t('page.rows.L3.S1', { season: tr.reference.season, source: tr.reference.source }), ledger: 'trials.reference' });
      const s2 = tr.options.S2.summary;
      const [gain, roots] = l3(v, 'S2');
      assert.deepEqual(gain, { text: t('page.rows.L3.S2', { gain: pct(s2.gain), season: s2.season }), ledger: s2.gain.l });
      assert.equal(roots.text, t('page.rows.L3.S2_roots', { roots_pct: String(s2.roots_pct.v), straw_pct: String(s2.straw_pct.v) }));
      assert.equal(roots.ledger, s2.ledger);
      const s3 = tr.options.S3.summary;
      const [main, comp] = l3(v, 'S3');
      const n = (x) => A.formatNumber(x.v, t, Number.isInteger(x.v) ? 0 : 1);
      assert.equal(main.text, t('page.rows.L3.S3', { delta: n(s3.delta), bags: n(s3.bags), n: A.formatNumber(tr.options.S3.n, t), pos: n(s3.pos), neg: n(s3.neg) }));
      assert.equal(main.ledger, s3.ledger);
      assert.equal(comp.text, t('page.rows.L3.S3_compilation', { a: pct(s3.a), b: pct(s3.b) }));
      assert.equal(comp.ledger, s3.ledger);
      assert.ok(comp.text.includes(`${n(s3.a)}%`) && gain.text.includes(`${n(s2.gain)}%`), 'the percent sign');
    }
    // Without the summaries the lines leave, never with a blank or Unknown number.
    const bare = A.normalizeData({ ...data, trials: { ...tr, reference: null, options: { S2: { ...tr.options.S2, summary: null }, S3: { ...tr.options.S3, summary: null } } } });
    const v = A.buildIndexView(bare, ui(bare));
    assert.equal(l3(v, 'S1')[0].text, COPY.langs.en.page.rows.L5.reference);
    assert.ok(!l3(v, 'S2').some((x) => x.text.startsWith('Measured')));
    assert.deepEqual(badStrings(v), []);
  });
});

describe('rehearsal folder (SA-D63): its own banner, its own basemap, the climate and no-green lines', () => {
  const stamp = (doc) => ({ ...doc, run_mode: 'rehearsal', run_note: 'Rehearsal: real NASA data on a development strip, fictitious trials' });

  // Synthetic climate block (SA-D72.5) and no-green count (SA-D72.4), example, fictitious.
  const CLIMATE = {
    S1: { crop: 'corn', start: '03-22', end: '09-19', n: 42, left_out: [], frost: { below_c: 2, k: 12, seasons: [] },
      rain: { median_mm: 441.04, p10_mm: 288.2, driest: [{ season: '2020/21', mm: 236.6 }, { season: '2018/19', mm: 252.5 }, { season: '2023/24', mm: 279.9 }] } },
    S2: { crop: 'corn_grass', start: '03-22', end: '09-19', n: 42, left_out: [], frost: { below_c: 2, k: 12, seasons: [] },
      rain: { median_mm: 441.04, p10_mm: 288.2, driest: [] } },
    S3: { crop: 'wheat', start: '05-24', end: '09-16', n: 0, left_out: [], frost: { below_c: 2, k: 0, seasons: [] },
      rain: { median_mm: null, p10_mm: null, driest: [] } },
    ledger: 'field_a.climate',
  };
  const NO_GREEN = { median: 13.5, of: 36, seasons: 8, per_season: [], ledger: 'field_a.ribbon_counts.no_green' };

  function rehearsalData({ climate = false } = {}) {
    const raw = exampleData();
    const fields = {};
    for (const [id, f] of Object.entries(raw.fields)) {
      const extra = climate ? { risk: { ...f.risk, climate: CLIMATE }, ribbon_counts: { no_green: NO_GREEN } } : {};
      fields[id] = stamp({ ...f, ...extra });
    }
    return A.normalizeData({ ...raw, region: stamp(raw.region), trials: stamp(raw.trials), fields, base: '../data/rehearsal/site_data/' });
  }

  test('a stamped folder shows the rehearsal banner instead of the example one', () => {
    const data = rehearsalData();
    assert.equal(data.isRehearsal, true);
    assert.equal(data.isExample, false);
    const v = A.buildIndexView(data, ui(data));
    assert.equal(v.isRehearsal, true);
    assert.equal(v.rehearsalBanner, COPY.langs.en.page.rehearsal_banner);
    const how = A.buildHowView(data, ui(data, { lang: 'pt' }));
    assert.equal(how.rehearsalBanner, COPY.langs.pt.page.rehearsal_banner);
    const plain = exampleData();
    assert.equal(plain.isRehearsal, false);
    assert.equal(A.buildIndexView(plain, ui(plain)).rehearsalBanner, null);
  });

  test('both pages hold the rehearsal banner', () => {
    for (const page of ['site/index.html', 'site/how-we-know.html']) {
      assert.ok(text(page).includes('x-show="v.isRehearsal" x-text="v.rehearsalBanner"'), page);
    }
  });

  test('the basemap comes from the data folder, the published one from img/', () => {
    assert.deepEqual(A.basemapUrls('data/'), A.BASEMAP);
    assert.deepEqual(A.basemapUrls(undefined), A.BASEMAP);
    assert.deepEqual(A.basemapUrls('../data/rehearsal/site_data/'),
      { png: '../data/rehearsal/site_data/img/basemap.png', georef: '../data/rehearsal/site_data/img/basemap.json' });
  });

  test('L2 shows frost and rain over the occupation dates, L1 of S1 the no-green count, never "not shown"', () => {
    const data = rehearsalData({ climate: true });
    const v = A.buildIndexView(data, ui(data));
    const lines = (sid, row) => v.columns.find((c) => c.id === sid).cells.find((c) => c.id === row).lines;
    const L2 = (sid) => lines(sid, 'L2').map((l) => l.text);
    const s1 = L2('S1');
    assert.ok(s1.includes('Nights below 2 °C, 22 Mar to 19 Sep: in 12 of 42 seasons'), s1.join(' | '));
    assert.ok(s1.includes('Rain, 22 Mar to 19 Sep: median 441 mm. The driest tenth of seasons had 288 mm or less.'), s1.join(' | '));
    assert.ok(s1.includes('Driest seasons: 2020/21 (237 mm), 2018/19 (253 mm), 2023/24 (280 mm)'), s1.join(' | '));
    assert.ok(lines('S1', 'L2').some((l) => l.ledger === 'field_a.climate'));
    assert.ok(!L2('S2').some((x) => x.startsWith('Driest seasons')));       // no driest season, no line
    assert.ok(!L2('S3').some((x) => x.startsWith('Nights below') || x.startsWith('Rain,')), L2('S3').join(' | '));   // n = 0: unknown, said
    for (const sid of ['S1', 'S2', 'S3']) {
      assert.ok(!L2(sid).some((x) => /not shown|Dry autumn|Cold in the window/.test(x)), L2(sid).join(' | '));
      assert.ok(L2(sid).some((x) => x.includes('not a forecast')));
    }
    const l1 = lines('S1', 'L1');
    const ng = l1.find((l) => l.text.startsWith('No green seen'));
    assert.equal(ng.text, 'No green seen in 13.5 of 36 ten-day periods (median of 8 seasons). Periods marked no data had no clean image and are not counted.');
    assert.equal(ng.ledger, 'field_a.ribbon_counts.no_green');
    assert.ok(!lines('S2', 'L1').some((l) => l.text.startsWith('No green')));
    assert.ok(!JSON.stringify(v).toLowerCase().includes('bare soil seen'));
    const pt = A.buildIndexView(data, ui(data, { lang: 'pt' }));
    const ptS1 = pt.columns.find((c) => c.id === 'S1').cells.find((c) => c.id === 'L2').lines.map((l) => l.text);
    assert.ok(ptS1.includes('Noites abaixo de 2 °C, de 22 mar a 19 set: em 12 de 42 safras'), ptS1.join(' | '));
    assert.ok(ptS1.includes('Chuva, de 22 mar a 19 set: mediana de 441 mm. O décimo mais seco das safras teve 288 mm ou menos.'), ptS1.join(' | '));
    assert.deepEqual(badStrings(v), []);
    assert.deepEqual(badStrings(pt), []);
  });
});

// ---------------------------------------------------------------- NASA in the first fold (SA-D78)

describe('NASA line below the answer and the ribbon before the columns (SA-D78)', () => {
  /** site/data as the page loads it (loadAll, the same fetch list), files read from disk. */
  async function siteData() {
    const fetchJson = async (url) => {
      const file = new URL(`site/${url}`, ROOT);
      return existsSync(file) ? JSON.parse(readFileSync(file, 'utf8')) : null;
    };
    return A.loadAll(fetchJson, 'data/');
  }

  test('index.html: the NASA line right below the answer box, the ribbon before the columns', () => {
    const src = text('site/index.html');
    const at = (s) => { const i = src.indexOf(s); assert.ok(i >= 0, s); return i; };
    // SA-D107: the ribbon right below the answer and the NASA line, before the counts and the model check.
    const order = ['class="answer-box"', 'class="nasa-line"', 'id="sec-ribbon"', 'class="caption"', 'class="model-check"', 'id="sec-columns"', 'id="your-numbers"', 'id="sec-year-strip"', 'id="sec-nasa"'];
    const pos = order.map(at);
    assert.deepEqual([...pos].sort((a, b) => a - b), pos, order.join(' < '));
    assert.ok(src.includes('x-for="(p, i) in v.answer.nasaLine"'));
    assert.ok(src.includes(':data-ledger="p.ledger" x-text="p.text"'), 'each clause links to its ledger entry');
    const ribbon = src.slice(at('id="sec-ribbon"'), at('id="sec-columns"'));
    assert.ok(ribbon.includes('v.ribbon.band'), 'the Zarc risk band moves with the ribbon');
    assert.ok(ribbon.includes('v.ribbon.legend'), 'the legend moves with the ribbon');
  });

  test('real fields a, b, c in EN and PT: two numbers, each with its ledger entry, no placeholder', async () => {
    const data = await siteData();
    assert.deepEqual(data.fieldIds, ['a', 'b', 'c']);
    for (const id of data.fieldIds) {
      const f = data.fields[id];
      const eb = D.endedBeforeFor(f.window_basis, 'S1', A.soilClassOf(f, 'unknown'));
      const fr = f.risk.climate.S1;
      for (const lang of ['en', 'pt']) {
        const v = A.buildIndexView(data, ui(data, { field: id, lang }));
        const nl = v.answer.nasaLine;
        assert.equal(nl.length, 2, `${id} ${lang}`);
        const t = A.makeT(data.copy, lang);
        assert.equal(nl[0].text, t('page.answer.nasa_line.hls', { k: A.formatNumber(eb.k, t), n: A.formatNumber(eb.n, t) }));
        assert.equal(nl[1].text, t('page.answer.nasa_line.power', { t: A.formatNumber(fr.frost.below_c, t, 0), k: A.formatNumber(fr.frost.k, t), n: A.formatNumber(fr.n, t) }));
        assert.equal(nl[0].ledger, f.window_basis.ended_before.ledger);
        assert.equal(nl[1].ledger, f.risk.climate.ledger);
        for (const p of nl) {
          assert.ok(data.records[p.ledger], `ledger entry ${p.ledger}`);
          assert.ok(!/[{}]|undefined|NaN|—|;/.test(p.text), p.text);
          assert.ok(p.text.startsWith('NASA '), p.text);
        }
        assert.deepEqual(badStrings(v), []);
      }
    }
  });

  test('the HLS clause follows the soil class selector and the HLS switch, and the S1 column does not repeat it', async () => {
    const data = await siteData();
    const f = data.fields.a;
    const t = A.makeT(data.copy, 'en');
    for (const cls of A.SOIL_CLASS_OPTIONS.filter((c) => c !== 'unknown')) {
      const v = A.buildIndexView(data, ui(data, { field: 'a', soilClass: cls }));
      const eb = D.endedBeforeFor(f.window_basis, 'S1', cls);
      const want = eb?.state === 'ok' && eb.n > 0
        ? t('page.answer.nasa_line.hls', { k: A.formatNumber(eb.k, t), n: A.formatNumber(eb.n, t) }) : null;
      assert.equal(v.answer.nasaLine.find((p) => p.text.startsWith('NASA HLS'))?.text ?? null, want, cls);
      // SA-D90: the count lives on this line only, the S1 column keeps the risk at exit.
      const col = v.columns.find((c) => c.id === 'S1').head.exitLines.find((x) => x.ledger === f.window_basis.ended_before.ledger);
      assert.equal(col, undefined, cls);
    }
    const off = A.buildIndexView(data, ui(data, { field: 'a', hlsOn: false }));
    assert.deepEqual(off.answer.nasaLine.map((p) => p.text.split(':')[0]), ['NASA POWER']);
  });

  test('SA-D84: without a soil class, ended before reads the earliest end, and fields a, b, c keep 4 of 4, 3 of 3, 6 of 6', async () => {
    const data = await siteData();
    const want = { a: [4, 4], b: [3, 3], c: [6, 6] };
    for (const id of data.fieldIds) {
      const b = data.fields[id].window_basis;
      const eb = D.endedBeforeFor(b, 'S1', 'unknown');
      assert.deepEqual([eb.k, eb.n], want[id], id);
      // The earliest end of every class and cycle group, never later than any class at its latest cycle.
      const early = b.S1.earliest.end_by_season;
      for (const w of Object.values(b.S1.by_class)) {
        for (const [s, end] of Object.entries(w.end_by_season)) assert.ok(early[s] <= end, `${id} ${s}`);
      }
      const t = A.makeT(data.copy, 'en');
      const v = A.buildIndexView(data, ui(data, { field: id }));
      assert.equal(v.answer.nasaLine[0].text,
        `NASA HLS, this field: the first green cycle ended before the corn Zarc window closed in ${want[id][0]} of ${want[id][1]} seasons.`);
      assert.equal(t('page.answer.nasa_line.hls', { k: '1', n: '2' }).includes('latest'), false);
    }
    // Without `earliest` the count is not made from another window.
    const b = data.fields.a.window_basis;
    const { earliest, ...noEarly } = b.S1;
    assert.equal(D.endedBeforeFor({ ...b, S1: noEarly }, 'S1', 'unknown').state, 'no_window');
  });

  test('SA-D84: one high between the earliest end and the latest cycle of the earliest class counts only for that class', () => {
    const EXIT = read('tests/fixtures/decision_risk_exit.json');
    const seasons = EXIT.basis.seasons.map((g, i) => (i === 1 ? { ...g, high: '2020-03-01' } : g));
    const b = { ...EXIT.basis, seasons };
    assert.equal(D.endedBeforeFor(b, 'S1', 'AD2').k, 3);
    assert.equal(D.endedBeforeFor(b, 'S1', 'unknown').k, 2);
  });

  test('SA-D84: the answer tag says example prices, real field on real data, and the fictitious example keeps its own', async () => {
    const real = await siteData();
    const ex = exampleData();
    for (const lang of ['en', 'pt']) {
      const t = A.makeT(real.copy, lang);
      const v = A.buildIndexView(real, ui(real, { lang }));
      assert.equal(v.example, true);
      assert.equal(v.answerExampleWord, t('page.answer.example_prices'));
      assert.equal(v.answerExampleWord, lang === 'en' ? 'example prices, real field' : 'preços de exemplo, talhão real');
      assert.equal(v.exampleWord, t('page.inputs.example'));
      const e = A.buildIndexView(ex, ui(ex, { lang }));
      assert.equal(ex.isExample, true);
      assert.equal(e.answerExampleWord, t('page.inputs.example'));
    }
    const src = text('site/index.html');
    const box = src.slice(src.indexOf('class="answer-box"'), src.indexOf('class="nasa-line"'));
    assert.ok(box.includes('x-text="v.answerExampleWord"'), 'the answer box reads the answer tag');
    assert.ok(src.includes('x-show="v.isExample" x-text="v.exampleWord"'), 'the fictitious example keeps its banner');
  });

  test('SA-D84: the main page links the observation region next to the region line, in EN and PT', async () => {
    const data = await siteData();
    for (const lang of ['en', 'pt']) {
      const v = A.buildIndexView(data, ui(data, { lang }));
      assert.equal(v.regionLink.href, 'index.html?data=data_regions/free_state/');
      assert.equal(v.regionLink.text, lang === 'en'
        ? 'Another region, observation only: Free State, South Africa'
        : 'Outra região, só observação: Free State, África do Sul');
    }
    const ex = exampleData();
    assert.equal(A.buildIndexView(ex, ui(ex)).regionLink, null, 'no link without the region copy');
    const src = text('site/index.html');
    const region = src.indexOf('data-copy="page.header.region"');
    const link = src.indexOf('v.regionLink');
    assert.ok(region > 0 && link > region && link < src.indexOf('data-copy="page.header.trial_sites"'));
    assert.ok(src.includes('href="index.html" x-text="v.obs.back"'), 'the observation page links back');
  });

  test('a missing number leaves its clause out, never "undefined"', () => {
    const t = A.makeT(COPY, 'en');
    const base = { window_basis: undefined, risk: { climate: { S1: { n: 42, frost: { below_c: 2, k: 12 } }, ledger: 'x.climate' } } };
    assert.deepEqual(A.nasaLine(t, base).map((p) => p.text), ['NASA POWER: nights below 2 °C in 12 of 42 corn seasons.']);
    assert.deepEqual(A.nasaLine(t, { risk: { climate: { S1: { n: 42, frost: { below_c: 2, k: null } } } } }), []);
    assert.deepEqual(A.nasaLine(t, { risk: { climate: { S1: { n: 0, frost: { below_c: 2, k: 0 } } } } }), []);
    assert.deepEqual(A.nasaLine(t, null), []);
    assert.deepEqual(A.nasaLine(t, { window_basis: { ended_before: { ledger: 'x' } } }), []);
  });

  test('with the frozen label the next-soybean row stays off the main page, no "not completed" in the columns', async () => {
    const data = await siteData();
    assert.notEqual(A.labelId(data), A.L5_LABEL);
    for (const id of data.fieldIds) {
      for (const lang of ['en', 'pt']) {
        const v = A.buildIndexView(data, ui(data, { field: id, lang }));
        assert.ok(!v.rows.some((r) => r.id === 'L5'), `${id} ${lang}`);
        assert.ok(!v.columns.some((c) => c.cells.some((x) => x.id === 'L5')), `${id} ${lang}`);
        const nc = A.makeT(data.copy, lang)('missing.not_completed');
        assert.ok(!JSON.stringify(v.columns).includes(nc), `${id} ${lang}: ${nc}`);
      }
    }
  });
});

// ---------------------------------------------------------------- SA-D89, SA-D90, SA-D91 (plan, V1)

async function realSite() {
  const fetchJson = async (url) => {
    const file = new URL(`site/${url}`, ROOT);
    return existsSync(file) ? JSON.parse(readFileSync(file, 'utf8')) : null;
  };
  return A.loadAll(fetchJson, 'data/');
}

describe('G on the page is the step of the count (SA-D91)', () => {
  test('the sentence and L4 show the whole step that ruleState counts at, never a decimal G', () => {
    const t = A.makeT(COPY, 'en');
    const r = { option: 'S3', G: 20 / 3, step: 7, N: 3, n: 10, state: 'keep', code: 'counts_keep' };
    assert.equal(A.optionSentence(t, r, {}), t('answers.counts_keep', { Option: COPY.langs.en.page.strategies.S3.name, G: '7', N: '3', n: '10' }));
    assert.match(A.optionSentence(A.makeT(COPY, 'pt'), r, {}), / 7 sc\/ha /);
    assert.equal(D.shownG(20 / 3), 7);
    assert.equal(D.shownG(4), 4);
    assert.ok(Object.is(D.shownG(-0.4), 0), 'no -0 on screen');
    assert.equal(D.shownG(NaN), null);
  });

  test('on the real page with soybean at 60, wheat on part needs 7 bags/ha, counted at 7 (the video frame)', async () => {
    const data = await realSite();
    for (const p_soy of ['60', '70', '85', '100', '130']) {
      const u = ui(data, { raw: { p_soy } });
      const res = A.decide(data, u);
      const v = A.buildIndexView(data, u);
      for (const sid of ['S2', 'S3']) {
        const r = res[sid];
        if (!Number.isFinite(r.G) || r.G <= 0) continue;
        assert.equal(D.shownG(r.G), r.step, `${p_soy} ${sid}: the shown G is the counted step`);
        const L4 = v.columns.find((c) => c.id === sid).cells.find((c) => c.id === 'L4').lines.map((l) => l.text).join(' ');
        assert.ok(L4.includes(` ${r.step} bags/ha`), `${p_soy} ${sid}: ${L4}`);
        assert.doesNotMatch(L4, /\d\.\d+ bags\/ha/, `${p_soy} ${sid}: no decimal G`);
      }
    }
    const v60 = A.buildIndexView(data, ui(data, { raw: { p_soy: '60' } }));
    const s3 = v60.columns.find((c) => c.id === 'S3').cells.find((c) => c.id === 'L4').lines.map((l) => l.text);
    assert.ok(s3.includes('Keep what you do. On your numbers, Wheat on part needs 7 bags/ha more soybean next season. Trials reached that in only 3 of 10 site-seasons.'), s3.join(' | '));
  });
});

describe('plain scoreboard lines (SA-D89)', () => {
  const EN_LINES = {
    L0: 'Fail. The water balance closes and runs repeat exactly, but 50 of 50 odd model outputs, such as a dead crop, have no explanation. A pass allows 0.',
    L1: 'Planned, not run. No weather station file was prepared for the event.',
    T0: 'Fail. Kendall S 0, a pass needs 21.',
    T1: 'Fail. Kendall S 11, a pass needs 21. RMSE 896 kg/ha, a constant gives 280.',
    P1: "Fail. With the wrong years' weather, S was at least as high in 7 of 9 shifts for T0 and in 0 of 9 for T1. A pass allows 0.",
    A1: 'Fail. With and without the water hand-off, S 11 against 9, but the paired test of the protocol did not pass.',
    A2: 'Planned, not run. The count it checked was removed, and the rain lines are not compared with IMERG in the event.',
    A3: 'Reported. 0 of 18 field and option states change without NASA HLS. Context only.',
    N1: 'Reported. Noise floor 7.5 days.',
    H1: 'Planned, not run. No reader session took place.',
    H2: "Planned, not run. Not attempted, by the participant's decision, so there was no local review.",
    R1: 'Planned, not run. The engine has no path for it in the event, and it runs only if T1 passes.',
  };

  test('the frozen scoreboard reads as one plain line per test, from the same numbers', async () => {
    const data = await realSite();
    const v = A.buildHowView(data, A.initialUi(data, 'en'));
    assert.deepEqual(Object.fromEntries(v.tests.map((r) => [r.id, `${r.statusLine} ${r.line}`.trim()])), EN_LINES);
    const sb = read('site/data/scoreboard.json');
    const raw = (sb.scoreboard ?? sb).tests;
    for (const r of v.tests) {
      assert.doesNotMatch(`${r.statusLine} ${r.line}`, /[a-z]_[a-z]|\btau\b|\{|\}/, `${r.id}: no machine key`);
      assert.equal(r.ledger, `scoreboard.tests.${r.id}`, 'the ledger link stays');
      const x = raw.find((y) => y.id === r.id);
      if (x.value !== null) assert.ok(r.hasRaw && r.value.length > 0, `${r.id}: stored values behind the toggle`);
    }
    assert.match(v.tests.find((r) => r.id === 'T1').value, /rmse_kg_ha 895\.66/, 'the stored values stay as stored');
    assert.equal(v.rawLabel, COPY.langs.en.howweknow.scoreboard.raw);
  });

  test('the Portuguese lines carry the same numbers with the Portuguese marks', async () => {
    const data = await realSite();
    const v = A.buildHowView(data, A.initialUi(data, 'pt'));
    const line = (id) => { const r = v.tests.find((x) => x.id === id); return `${r.statusLine} ${r.line}`; };
    assert.equal(line('T1'), 'Não passou. S de Kendall 11, para passar precisa de 21. RMSE de 896 kg/ha, uma constante dá 280.');
    assert.equal(line('N1'), 'Relatado. Piso de ruído de 7,5 dias.');
    assert.equal(line('A3'), 'Relatado. 0 de 18 estados de talhão e opção mudam sem o NASA HLS. Só contexto.');
    assert.deepEqual(badStrings(v), []);
  });

  test('a line whose wording would not fit the numbers is left out, the status stays', () => {
    const t = A.makeT(COPY, 'en');
    // L0 failing on the water balance, not on the artifacts: no artifact sentence.
    const l0 = A.scoreboardLine(t, { id: 'L0', status: 'fail', value: { max_gap_mm: 3, repeat_exact: true, n_artifacts: 2, n_unexplained: 2 }, threshold: { closure_mm_per_swap: 1, unexplained_artifacts_max: 0 } });
    assert.deepEqual(l0, { status: 'Fail.', text: '' });
    // A1 with D at or below zero: no "paired test" clause.
    const a1 = A.scoreboardLine(t, { id: 'A1', status: 'fail', value: { d: -1, s_with_state: 8, s_without_state: 9 } });
    assert.equal(a1.text, 'With and without the water hand-off, S 8 against 9.');
    // A missing number drops its sentence, never prints Unknown or a brace.
    assert.deepEqual(A.scoreboardLine(t, { id: 'T1', status: 'fail', value: { s: 11 }, threshold: { s_min: 21 } }), { status: 'Fail.', text: '' });
    assert.deepEqual(A.scoreboardLine(t, { id: 'X9', status: 'odd' }), { status: 'Not completed.', text: '' });
    // The example scoreboard, every test passing, never shows a missing value.
    const data = exampleData({ scoreboard: SCOREBOARD });
    for (const lang of A.LANGS) assert.deepEqual(badStrings(A.buildHowView(data, A.initialUi(data, lang))), []);
  });

  test('how-we-know.html shows the plain line and keeps the stored values behind a toggle', () => {
    const src = text('site/how-we-know.html');
    assert.match(src, /x-text="r\.statusLine"/);
    assert.match(src, /x-text="r\.line"/);
    assert.match(src, /<td x-text="r\.rule"><\/td>/, 'the rule column is plain text, not mono');
    const det = src.slice(src.indexOf('<details class="sb-raw"'), src.indexOf('</details>', src.indexOf('<details class="sb-raw"')));
    for (const k of ['v.rawLabel', 'r.ruleRawText', 'r.valueText', 'r.note']) assert.ok(det.includes(k), `${k} behind the toggle`);
  });

  const EN_RULES = {
    L0: 'The water balance closes within 1 mm at each crop swap, two runs repeat exactly, and every odd model output has an explanation.',
    L1: 'Rain within 10% of the stations per season, temperature within 1 °C.',
    T0: 'Kendall S of at least 21, and the S within each site adds up to more than 0.',
    T1: 'Kendall S of at least 21, and an RMSE below that of a constant, 280 kg/ha.',
    P1: 'In none of the 9 shifts to wrong years is S at least as high as with the right years.',
    A1: 'S with the water hand-off above S without it, and the exhaustive paired swap test of the protocol.',
    A2: 'No pass rule. Reported as is, never as passed.',
    A3: 'No pass rule. If no state changes, NASA HLS is context only.',
    N1: 'No pass rule. Reported as is, never as passed.',
    H1: 'No pass rule. Reported as is, never as passed.',
    H2: 'No pass rule.',
    R1: 'Runs only if T1 passes. The direction holds with inversions up to 20% and an equivalence margin of 166 kg/ha.',
  };

  test('the pass rule column reads as plain rules from the same thresholds, the stored ones behind the toggle', async () => {
    const data = await realSite();
    const v = A.buildHowView(data, A.initialUi(data, 'en'));
    assert.deepEqual(Object.fromEntries(v.tests.map((r) => [r.id, r.rule])), EN_RULES);
    for (const r of v.tests) assert.doesNotMatch(r.rule, /[a-z]_[a-z]|\{|\}/, `${r.id}: no machine key`);
    assert.equal(v.tests.find((r) => r.id === 'T1').ruleRaw, 's_min 21, rmse_lt in_sample_constant_rmse, rmse_lt_kg_ha 279.81', 'stored thresholds as stored');
    assert.equal(v.tests.find((r) => r.id === 'T1').ruleRawText, 'Pass rule as stored: s_min 21, rmse_lt in_sample_constant_rmse, rmse_lt_kg_ha 279.81');
    assert.equal(v.tests.find((r) => r.id === 'H1').ruleRaw, '');
    assert.ok(v.tests.filter((r) => r.ruleRaw).every((r) => r.hasRaw));
    const pt = A.buildHowView(data, A.initialUi(data, 'pt'));
    assert.equal(pt.tests.find((r) => r.id === 'T1').rule, 'S de Kendall de pelo menos 21, e um RMSE abaixo do de uma constante, 280 kg/ha.');
    assert.deepEqual(badStrings(pt), []);
  });

  test('other thresholds pick the general wording, and missing numbers give the unknown word', () => {
    const t = A.makeT(COPY, 'en');
    assert.equal(A.scoreboardRule(t, { id: 'L0', threshold: { closure_mm_per_swap: 1, repeat: 'exact', unexplained_artifacts_max: 2 } }),
      'The water balance closes within 1 mm at each crop swap, two runs repeat exactly, and at most 2 odd model outputs have no explanation.');
    assert.equal(A.scoreboardRule(t, { id: 'P1', threshold: { wrong_year_s_at_least_max: 1, shifts: 9 } }),
      'In at most 1 of the 9 shifts to wrong years, S is at least as high as with the right years.');
    assert.equal(A.scoreboardRule(t, { id: 'A1', threshold: { d_gt: 2 } }),
      'S with the water hand-off minus S without it above 2, and the exhaustive paired swap test of the protocol.');
    assert.equal(A.scoreboardRule(t, { id: 'T1', threshold: { s_min: 21 } }), t.unknown());
    assert.equal(A.scoreboardRule(t, { id: 'X9', threshold: { a_b: 1 } }), t.unknown());
  });
});

describe('one satellite sentence per column (SA-D90)', () => {
  test('real fields: S1 says the risk at exit once, no edge sentence at k 0, S2 the same reading line', async () => {
    const data = await realSite();
    for (const lang of A.LANGS) {
      const t = A.makeT(data.copy, lang);
      for (const id of data.fieldIds) {
        const v = A.buildIndexView(data, ui(data, { field: id, lang }));
        const [s1, s2] = v.columns;
        const wr = A.windowReadingOf(data.fields[id], 'unknown', 'S1');
        assert.equal(wr.k, 0, `${id}: the real fields persist beyond the window in 0 seasons`);
        assert.equal(s1.head.warning, null, `${id} ${lang}: no "persisted beyond" sentence at k 0`);
        assert.equal(s1.head.exitLines.length, 1, `${id} ${lang}: one satellite sentence`);
        assert.equal(s1.head.exitLines[0].ledger, `field_${id}.risk_at_exit`);
        assert.doesNotMatch(s1.head.exitLines[0].text, /persisted beyond|ended before|passou do fim|terminou antes/);
        assert.equal(s2.head.sameSatellite, true, `${id}: S2 numbers equal S1`);
        assert.deepEqual(s2.head.exitLines, [{ text: t('window.same_as_S1', { name: t('page.strategies.S1.name') }), ledger: `field_${id}.risk_at_exit` }]);
        assert.equal(s2.head.warning, null);
        assert.deepEqual(badStrings(v), []);
      }
    }
    const en = A.buildIndexView(data, ui(data, { field: 'a' }));
    assert.equal(en.columns[1].head.exitLines[0].text, 'Same satellite reading as Keep corn.');
    assert.equal(en.columns[0].head.exitLines[0].text, 'Satellite estimate: the first green cycle ended in a ten-day period whose Zarc risk for second-crop corn, depending on the soil class, was 20 to 40% in 1 of 4 seasons and 30 to 40% in 3 of 4 seasons.');
  });

  test('the edge sentence shows when k > 0, and S2 keeps its own sentence when its numbers differ', () => {
    const t = A.makeT(COPY, 'en');
    const edge = { text: 'x', state: 'at_the_edge', ledger: null, k: 1 };
    assert.equal(A.shownWarning(edge), edge);
    assert.equal(A.shownWarning({ ...edge, k: 0 }), null);
    assert.equal(A.shownWarning({ ...edge, state: 'no_window', k: null }).state, 'no_window', 'other warnings stay');
    const data = exampleData();
    const f = data.fields.a;
    // Every S2 risk 10 points higher, by class and across classes: the same seasons, other numbers.
    const up = (a) => (Array.isArray(a) ? a.map((x) => (typeof x === 'number' ? x + 10 : x)) : a);
    const band = f.window_basis.S2.risk_band;
    const byClass = Object.fromEntries(Object.entries(band.by_class ?? {}).map(([c, b]) => [c, { ...b, risk: up(b.risk) }]));
    const shifted = { ...f, window_basis: { ...f.window_basis, S2: { ...f.window_basis.S2, risk_band: { ...band, max: up(band.max), min: up(band.min), by_class: byClass } } } };
    const d2 = A.normalizeData({ ...data, fields: { ...data.fields, a: shifted } });
    const v = A.buildIndexView(d2, ui(d2));
    assert.equal(v.columns[1].head.sameSatellite, false);
    assert.ok(v.columns[1].head.exitLines.length >= 1);
    assert.ok(v.columns[1].head.exitLines.every((x) => x.text.includes(t('page.rows.L2.crops.grass'))), 'S2 names its own crop');
    const same = A.buildIndexView(data, ui(data));
    if (same.columns[1].head.sameSatellite) assert.equal(same.columns[1].head.exitLines[0].text, t('window.same_as_S1', { name: t('page.strategies.S1.name') }));
  });
});

// ---------------------------------------------------------------- SA-D107 (plan of 15 November, H1)

describe('the field change shows: facts strip, fixed sentence, ribbon up, buttons ready (SA-D107)', () => {
  const WANT = {
    en: {
      a: ['Field A', [['area', 'Area', '23.8 ha'], ['slope', 'Median slope', '8.1%'], ['municipality', 'Municipality', 'Cambé'], ['seasons', 'End of green read cleanly', '4 of 8 seasons'], ['no_green', 'No green, median', '8.5 of 36 ten-day periods']]],
      b: ['Field B', [['area', 'Area', '53.8 ha'], ['slope', 'Median slope', '4.8%'], ['municipality', 'Municipality', 'Cambé'], ['seasons', 'End of green read cleanly', '3 of 8 seasons'], ['no_green', 'No green, median', '9 of 36 ten-day periods']]],
      c: ['Field C', [['area', 'Area', '33.1 ha'], ['slope', 'Median slope', '8.5%'], ['municipality', 'Municipality', 'Cambé'], ['seasons', 'End of green read cleanly', '6 of 8 seasons'], ['no_green', 'No green, median', '9.5 of 36 ten-day periods']]],
    },
    pt: {
      a: ['Talhão A', [['area', 'Área', '23,8 ha'], ['slope', 'Declive mediano', '8,1%'], ['municipality', 'Município', 'Cambé'], ['seasons', 'Fim do verde com leitura limpa', '4 de 8 safras'], ['no_green', 'Sem verde, mediana', '8,5 de 36 decêndios']]],
      b: ['Talhão B', [['area', 'Área', '53,8 ha'], ['slope', 'Declive mediano', '4,8%'], ['municipality', 'Município', 'Cambé'], ['seasons', 'Fim do verde com leitura limpa', '3 de 8 safras'], ['no_green', 'Sem verde, mediana', '9 de 36 decêndios']]],
      c: ['Talhão C', [['area', 'Área', '33,1 ha'], ['slope', 'Declive mediano', '8,5%'], ['municipality', 'Município', 'Cambé'], ['seasons', 'Fim do verde com leitura limpa', '6 de 8 safras'], ['no_green', 'Sem verde, mediana', '9,5 de 36 decêndios']]],
    },
  };
  const LEDGER = { area: (id) => `field_${id}.area_ha`, slope: (id) => `field_${id}.relief`, municipality: () => null, seasons: (id) => `field_${id}.ribbon`, no_green: (id) => `field_${id}.ribbon_counts.no_green` };
  const SENTENCE = {
    en: 'The answer is the same on the three fields, because it comes from the trials and your prices. What changes with the field is its satellite record.',
    pt: 'A resposta é a mesma nos três talhões, porque vem dos ensaios e dos seus preços. O que muda com o talhão é o histórico de satélite.',
  };

  test('the strip of fields a, b, c in EN and PT, every number with its ledger entry', async () => {
    const data = await realSite();
    for (const lang of ['en', 'pt']) {
      const seen = new Set();
      for (const id of data.fieldIds) {
        const v = A.buildIndexView(data, ui(data, { field: id, lang }));
        const [title, items] = WANT[lang][id];
        assert.equal(v.fieldFacts.title, title);
        assert.deepEqual(v.fieldFacts.items.map((x) => [x.id, x.label, x.value]), items, `${id} ${lang}`);
        for (const x of v.fieldFacts.items) {
          assert.equal(x.ledger, LEDGER[x.id](id), `${id} ${x.id}`);
          if (x.ledger) assert.ok(data.records[x.ledger], `ledger entry ${x.ledger}`);
          assert.ok(!/[{}]|undefined|NaN|—|–|;/.test(x.label + x.value), x.value);
        }
        // The ok seasons are the n of the NASA HLS line (SA-D84).
        const eb = D.endedBeforeFor(data.fields[id].window_basis, 'S1', 'unknown');
        assert.ok(v.fieldFacts.items.find((x) => x.id === 'seasons').value.startsWith(`${eb.n} `));
        assert.deepEqual(badStrings(v), []);
        seen.add(JSON.stringify(v.fieldFacts));
      }
      assert.equal(seen.size, 3, `${lang}: the strip changes with the field`);
    }
  });

  test('a missing fact is left out, never "undefined", and no field means no strip', () => {
    const t = A.makeT(COPY, 'en');
    assert.equal(A.fieldFactsView(t, null, 'a'), null);
    assert.equal(A.fieldFactsView(t, {}, 'a'), null);
    const one = A.fieldFactsView(t, { area_ha: { v: 10, l: 'x.area_ha' } }, 'b');
    assert.deepEqual(one, { title: 'Field B', items: [{ id: 'area', label: 'Area', value: '10 ha', ledger: 'x.area_ha' }] });
    const ex = exampleData();
    const v = A.buildIndexView(ex, ui(ex));
    assert.deepEqual(badStrings(v.fieldFacts), []);
  });

  test('the fixed sentence in EN and PT, and the answer itself does not change with the field', async () => {
    const data = await realSite();
    for (const lang of ['en', 'pt']) {
      const views = data.fieldIds.map((id) => A.buildIndexView(data, ui(data, { field: id, lang })));
      for (const v of views) assert.equal(v.sameAnswer, SENTENCE[lang]);
      assert.equal(A.makeT(data.copy, lang)('page.header.same_answer'), SENTENCE[lang]);
      const answers = new Set(views.map((v) => JSON.stringify([v.answer.state, v.answer.text, v.answer.caption])));
      assert.equal(answers.size, 1, 'the sentence says what the page does');
    }
  });

  test('index.html: strip and sentence right after the field buttons, the buttons disabled until ready', () => {
    const src = text('site/index.html');
    const at = (s) => { const i = src.indexOf(s); assert.ok(i >= 0, s); return i; };
    const order = ['class="field-picker"', 'class="field-facts"', 'data-copy="page.header.same_answer"', 'data-copy="page.header.persona"', 'id="sec-question"'];
    const pos = order.map(at);
    assert.deepEqual([...pos].sort((a, b) => a - b), pos, order.join(' < '));
    const strip = src.slice(at('class="field-facts"'), at('data-copy="page.header.same_answer"'));
    assert.ok(strip.includes('aria-live="polite"'), 'the change is announced');
    assert.ok(strip.includes(':data-ledger="fc.ledger" x-text="fc.value"'), 'each number links its ledger entry');
    const picker = src.slice(at('class="field-picker"'), at('class="field-facts"'));
    assert.match(picker, /<button[^>]*:disabled="!ready"[^>]*@click="setField\(f\.id\)"/);
  });

  /** Fake Alpine (effects rerun on demand, nextTick as a microtask) and a fake window. */
  function harness({ georef = { ok: true }, image = 'load' } = {}) {
    const effects = [];
    const timers = [];
    const Alpine = { effect: (fn) => { effects.push(fn); fn(); }, nextTick: (fn) => queueMicrotask(fn) };
    const g = JSON.parse(readFileSync(new URL('site/img/basemap.json', ROOT), 'utf8'));
    const images = [];
    class FakeImage {
      // The size the browser reads from the PNG.
      constructor() { images.push(this); this.naturalWidth = g.width; this.naturalHeight = g.height; }
      set src(v) {
        this._src = v;
        if (image === 'never') return;
        queueMicrotask(() => (image === 'load' ? this.onload() : this.onerror()));
      }
    }
    const win = {
      Image: FakeImage,
      setTimeout: (fn, ms) => { timers.push({ fn, ms }); return timers.length; },
      fetch: async () => (georef.ok ? { ok: true, json: async () => g } : { ok: false, json: async () => null }),
    };
    const doc = { documentElement: {}, title: '', querySelectorAll: () => [] };
    return { Alpine, win, doc, effects, timers, images, g };
  }
  const flush = async () => { for (let i = 0; i < 20; i++) await new Promise((r) => setImmediate(r)); };

  test('pageComponent: not ready at first, ready once the basemap settles, the field applies at once', async () => {
    const data = await realSite();
    const h = harness();
    const c = A.pageComponent({ Alpine: h.Alpine, win: h.win, doc: h.doc, data, ui0: A.initialUi(data, 'en'), page: 'index' });
    assert.equal(c.ready, false);
    c.init();
    assert.equal(c.ready, false, 'not ready while the map loads');
    assert.deepEqual(h.timers.map((x) => x.ms), [A.READY_TIMEOUT_MS]);
    c.setField('c');
    h.effects.forEach((fn) => fn());
    assert.equal(c.v.fieldFacts.title, 'Field C', 'a choice on the map applies before ready');
    await flush();
    assert.equal(h.images.length, 1);
    assert.equal(c.ready, true);
    assert.equal(c.ui.mapImage.width, h.g.width);
    h.timers[0].fn();
    assert.equal(c.ready, true, 'the timeout after ready changes nothing');
  });

  test('pageComponent: ready without a basemap, after a failed image, and on timeout', async () => {
    const data = await realSite();
    for (const opt of [{ georef: { ok: false } }, { image: 'error' }]) {
      const h = harness(opt);
      const c = A.pageComponent({ Alpine: h.Alpine, win: h.win, doc: h.doc, data, ui0: A.initialUi(data, 'en') });
      c.init();
      await flush();
      assert.equal(c.ready, true, JSON.stringify(opt));
      assert.ok(!c.ui.mapImage, 'the map keeps the polygons alone');
    }
    const h = harness({ image: 'never' });
    const c = A.pageComponent({ Alpine: h.Alpine, win: h.win, doc: h.doc, data, ui0: A.initialUi(data, 'en') });
    c.init();
    await flush();
    assert.equal(c.ready, false, 'still waiting for the image');
    h.timers[0].fn();
    await flush();
    assert.equal(c.ready, true, 'the buttons turn on after READY_TIMEOUT_MS anyway');
    const how = harness();
    const hc = A.pageComponent({ Alpine: how.Alpine, win: how.win, doc: how.doc, data, ui0: A.initialUi(data, 'en'), page: 'how' });
    hc.init();
    await flush();
    assert.equal(hc.ready, true, 'How we know has no map to wait for');
    assert.equal(how.images.length, 0);
  });
});

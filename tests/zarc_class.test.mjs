// Written with an SI tool (Claude, Anthropic) for NASA Space Apps 2026. See AI_USE.md.
// SA-D64 and the S2 methods in the browser: the window warning recomputed from window_basis
// when the user declares a Zarc soil class, the no_window state, and S2 per establishment method.
// Run: node --test tests/zarc_class.test.mjs (Node 22). All data example, fictitious.

import { test, describe } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

import * as A from '../site/js/app.mjs';
import * as D from '../site/js/decision.mjs';
import * as SV from '../site/js/svg.mjs';

const ROOT = new URL('../', import.meta.url);
const read = (rel) => JSON.parse(readFileSync(new URL(rel, ROOT), 'utf8'));
const COPY = read('site/data/copy.json');
const FX = 'tests/fixtures/decision_example/';
const SHARED = read('tests/fixtures/decision_window_basis.json');

function exampleData(fieldPatch = {}) {
  const f = { a: read(`${FX}field_a.json`), b: read(`${FX}field_b.json`) };
  for (const [id, patch] of Object.entries(fieldPatch)) f[id] = { ...f[id], ...patch };
  return A.normalizeData({
    copy: COPY, region: read(`${FX}region.json`), trials: read(`${FX}trials_steps.json`), ablation: read(`${FX}ablation.json`),
    ledger: read(`${FX}ledger_m5.json`), scoreboard: null, manifest: null, fields: f, base: 'data/',
  });
}

function ui(data, patch = {}) {
  const u = A.initialUi(data, patch.lang ?? 'en');
  return { ...u, ...patch, raw: { ...u.raw, ...(patch.raw ?? {}) }, limits: { ...u.limits, ...(patch.limits ?? {}) } };
}

/** Field A with no soil report, as M5 writes it now: the class is null, window_basis stays. */
const NO_REPORT = { soil: { ...read(`${FX}field_a.json`).soil, zarc_ad_class: null, zarc_ad_status: 'unknown' } };

describe('window reading from window_basis (SA-D64)', () => {
  test('decision.mjs windowReadingFor gives the same reading as the Python count', () => {
    assert.equal(SHARED.label, 'example, fictitious');
    for (const c of SHARED.cases) {
      assert.deepEqual(D.windowReadingFor(SHARED.basis, c.sid, c.soil_class), c.expected, `${c.sid} ${c.soil_class}`);
    }
  });

  test('a class outside AD1 to AD6, or a broken basis, gives no reading', () => {
    assert.equal(D.windowReadingFor(SHARED.basis, 'S1', 'AD7'), null);
    assert.equal(D.windowReadingFor(SHARED.basis, 'S1', 'Tipo 3'), null);
    assert.equal(D.windowReadingFor(SHARED.basis, 'S3', 'unknown'), null);
    assert.equal(D.windowReadingFor({ ...SHARED.basis, min_seasons: '3' }, 'S1', 'unknown'), null);
    const broken = { ...SHARED.basis, seasons: [{ season: '2018/19', low: 'soon', status: 'ok' }] };
    assert.equal(D.windowReadingFor(broken, 'S1', 'unknown'), null);
    assert.deepEqual(D.SOIL_CLASSES, ['unknown', 'AD1', 'AD2', 'AD3', 'AD4', 'AD5', 'AD6']);
  });

  test('the envelope is never earlier than a declared class: k for any class is the smallest', () => {
    const any = D.windowReadingFor(SHARED.basis, 'S1', 'unknown');
    for (const cls of D.SOIL_CLASSES.slice(1)) {
      const r = D.windowReadingFor(SHARED.basis, 'S1', cls);
      if (r.state !== 'no_window') assert.ok(r.k >= any.k, cls);
    }
  });
});

describe('the class selector on the page', () => {
  test('without a report the warning says for any soil class, and a declared class recomputes it', () => {
    const data = exampleData({ a: NO_REPORT });
    const t = A.makeT(COPY, 'en');
    const v0 = A.buildIndexView(data, ui(data));
    const any = A.windowReadingOf(data.fields.a, 'unknown', 'S1');
    assert.equal(any.basis, 'for_any_soil_class');
    assert.equal(v0.columns[0].head.warning.text, t(any.state === 'outside' ? 'window.outside_any_class' : 'window.at_the_edge_any_class', { k: String(any.k), n: String(any.n) }));
    assert.equal(v0.soilClass.value, 'unknown');
    assert.equal(v0.soilClass.note, t('window.any_class_note'));
    assert.deepEqual(v0.soilClass.options.map((o) => o.value), D.SOIL_CLASSES);
    assert.equal(v0.soilClass.options[0].label, t('window.class_selector.unknown'));

    // AD3 ends earlier than the envelope (fictitious Tábua): more seasons persist beyond it.
    const v3 = A.buildIndexView(data, ui(data, { soilClass: 'AD3' }));
    const r3 = A.windowReadingOf(data.fields.a, 'AD3', 'S1');
    assert.equal(r3.basis, 'declared_class');
    assert.ok(r3.k >= any.k);
    const key = r3.state === 'outside' ? 'flags.window_warning' : 'window.at_the_edge';
    assert.equal(v3.columns[0].head.warning.text, t(key, { k: String(r3.k), n: String(r3.n) }));
    assert.equal(v3.soilClass.note, null);
    // The decision rule reads the same reading.
    assert.deepEqual(A.decisionInput(data, ui(data, { soilClass: 'AD3' }), data.fields.a).windowReading.k, r3.k);
    // The sheet link carries the declared class.
    assert.match(A.sheetHash(ui(data, { soilClass: 'AD3' })), /(^|&)soil_class=AD3(&|$)/);
    assert.doesNotMatch(A.sheetHash(ui(data)), /soil_class/);
  });

  test('a class with no Tábua row says no_window, never at the edge', () => {
    const data = exampleData({ a: NO_REPORT });
    const v = A.buildIndexView(data, ui(data, { soilClass: 'AD1' }));        // the fictitious Tábua has AD3 and AD4 only
    assert.equal(v.columns[0].head.warning.state, 'no_window');
    assert.equal(v.columns[0].head.warning.text, A.makeT(COPY, 'en')('window.no_window'));
    assert.equal(A.decisionInput(data, ui(data, { soilClass: 'AD1' }), data.fields.a).windowReading.state, 'no_window');
    // With a declared class the sowing date is checked against that class's window (none here).
    assert.deepEqual(A.zarcWindowOf(data.fields.a, 'AD1'), { corn: undefined, grass: undefined });
    const w4 = A.zarcWindowOf(data.fields.a, 'AD4');
    assert.equal(w4.corn.end, data.fields.a.window_basis.S1.by_class.AD4.end);
  });

  test('S1 and S2 columns read their own crop, and HLS off hides both', () => {
    const data = exampleData({ a: NO_REPORT });
    const v = A.buildIndexView(data, ui(data));
    // SA-D90: S2 reads its own crop, and says so in one line when its numbers equal those of S1.
    const r1 = A.windowReadingOf(data.fields.a, 'unknown', 'S1');
    const r2 = A.windowReadingOf(data.fields.a, 'unknown', 'S2');
    const c2 = v.columns[1].head;
    if (c2.sameSatellite) {
      assert.deepEqual([r2.state, r2.k, r2.n], [r1.state, r1.k, r1.n]);
      assert.equal(c2.warning, null);
    } else {
      assert.equal(c2.warning?.state ?? null, r2.k === 0 ? null : r2.state);
    }
    assert.equal(v.columns[2].head.warning, null);
    const off = A.buildIndexView(data, ui(data, { hlsOn: false }));
    assert.ok(off.columns.every((c) => c.head.warning === null));
  });

  test('both languages have every new sentence', () => {
    for (const lang of A.LANGS) {
      const t = A.makeT(COPY, lang);
      for (const k of ['window.outside_any_class', 'window.at_the_edge_any_class', 'window.no_window', 'window.any_class_note',
        'window.class_selector.label', 'window.class_selector.unknown', 'window.class_selector.hint', 's2_methods.title',
        's2_methods.limit', 's2_methods.line', 's2_methods.line_counted', 'answers.method_missing']) {
        assert.ok(t.has(k), `${lang} ${k}`);
      }
    }
  });
});

describe('S2 by establishment method (protocol 11)', () => {
  const steps7 = [{ g_sc_ha: 1, count: 6 }, { g_sc_ha: 2, count: 5 }, { g_sc_ha: 3, count: 1 }, { g_sc_ha: 4, count: 0 }];
  const byMethod = [
    { method: 'in_row_or_alternate_rows_2kg', n: 4, sites: ['Floresta'], steps: [{ g_sc_ha: 1, count: 4 }, { g_sc_ha: 2, count: 3 }, { g_sc_ha: 3, count: 1 }, { g_sc_ha: 4, count: 0 }] },
    { method: 'broadcast_8_12kg', n: 3, sites: ['Cafelândia'], steps: [{ g_sc_ha: 1, count: 2 }, { g_sc_ha: 2, count: 2 }, { g_sc_ha: 3, count: 0 }] },
  ];
  const S2 = { n: 7, steps: steps7, by_method: byMethod };
  const numbers = { p_soy: 100, c_grass: 150 };                               // G = 1.5 bags/ha, step 2

  test('methodCounts gives n and N per method, and null when the methods cannot show it', () => {
    assert.deepEqual(D.methodCounts(S2, 2), [
      { method: 'in_row_or_alternate_rows_2kg', n: 4, N: 3 }, { method: 'broadcast_8_12kg', n: 3, N: 2 }]);
    assert.deepEqual(D.methodCounts(S2).map((m) => m.N), [null, null]);
    assert.equal(D.methodCounts({ n: 7, steps: steps7 }), null);
    assert.equal(D.methodCounts({ ...S2, by_method: [] }), null);
    assert.equal(D.methodCounts({ ...S2, by_method: [byMethod[0]] }), null, 'the n must add up');
    assert.equal(D.methodCounts({ ...S2, by_method: [byMethod[0], { ...byMethod[1], method: '' }] }), null);
    assert.equal(D.methodCounts({ ...S2, by_method: [byMethod[0], { ...byMethod[0] }] }), null, 'one entry per method');
  });

  test('S2 without the method gives Not enough evidence on a verdict from the counts', () => {
    const withM = D.decideOption('S2', { numbers, trials: { S2 } });
    assert.equal(withM.code, 'counts_try');
    const without = D.decideOption('S2', { numbers, trials: { S2: { n: 7, steps: steps7 } } });
    assert.deepEqual([without.state, without.code], ['not_enough', 'method_missing']);
    // G <= 0 is not a count, and few trials stay few trials.
    assert.equal(D.decideOption('S2', { numbers: { p_soy: 100, c_grass: 0 }, trials: { S2: { n: 7, steps: steps7 } } }).code, 'pays_without_gain');
    assert.equal(D.decideOption('S2', { numbers, trials: { S2: { n: 3, steps: [] } } }).code, 'few_trials');
    // S3 never needs a method.
    assert.notEqual(D.decideOption('S3', { numbers: { p_soy: 100, p_corn: 50, p_wheat: 60, Y_corn: 100, Y_wheat: 50, c_corn: 3000, c_wheat: 1400 }, trials: { S3: { n: 7, steps: steps7 } } }).code, 'method_missing');
  });

  test('the S2 column shows the count per method, and the main sentence says why without it', () => {
    const base = exampleData();
    const trials = { ...base.trials, options: { ...base.trials.options, S2: { ...base.trials.options.S2, ...S2 } } };
    const data = A.normalizeData({ ...base, trials });
    const u = ui(data, { raw: { p_soy: '100', c_grass: '150' } });
    const v = A.buildIndexView(data, u);
    const l3 = v.columns.find((c) => c.id === 'S2').cells.find((c) => c.id === 'L3').lines.map((x) => x.text);
    const t = A.makeT(COPY, 'en');
    assert.ok(l3.includes(t('s2_methods.title')));
    assert.ok(l3.includes(t('s2_methods.line_counted', { method: t('s2_methods.names.in_row_or_alternate_rows_2kg'), sites: 'Floresta', N: '3', n: '4' })));
    assert.ok(l3.includes(t('s2_methods.line_counted', { method: t('s2_methods.names.broadcast_8_12kg'), sites: 'Cafelândia', N: '2', n: '3' })));
    assert.ok(l3.includes(t('s2_methods.limit')));

    const bare = { ...trials, options: { ...trials.options, S2: { ...trials.options.S2, by_method: undefined } } };
    const d2 = A.normalizeData({ ...base, trials: bare });
    const r = A.decide(d2, ui(d2, { raw: { p_soy: '100', c_grass: '150' } }));
    assert.deepEqual([r.S2.state, r.S2.code], ['not_enough', 'method_missing']);
    const l3b = A.buildIndexView(d2, ui(d2, { raw: { p_soy: '100', c_grass: '150' } })).columns.find((c) => c.id === 'S2').cells.find((c) => c.id === 'L3').lines.map((x) => x.text);
    assert.ok(!l3b.includes(t('s2_methods.title')));
  });
});

// SA-D72: the Zarc risk band above the ribbon, the risk at exit, "ended before" and the L2 range.
describe('Zarc risk band, risk at exit and ended before (SA-D72)', () => {
  const EXIT = read('tests/fixtures/decision_risk_exit.json');

  test('decision.mjs endedBeforeFor and riskAtExitFor give the same counts as Python', () => {
    assert.equal(EXIT.label, 'example, fictitious');
    for (const c of EXIT.cases) {
      assert.deepEqual(D.endedBeforeFor(EXIT.basis, c.sid, c.soil_class), c.ended_before, `${c.sid} ${c.soil_class}`);
      assert.deepEqual(D.riskAtExitFor(EXIT.basis, c.sid, c.soil_class), c.risk_at_exit, `${c.sid} ${c.soil_class}`);
    }
  });

  test('a broken basis gives no count', () => {
    assert.equal(D.endedBeforeFor(EXIT.basis, 'S1', 'AD9'), null);
    assert.equal(D.riskAtExitFor(EXIT.basis, 'S3', 'unknown'), null);
    const seasons = EXIT.basis.seasons.map((g) => (g.status === 'ok' ? { ...g, high_dekad: 37 } : g));
    assert.equal(D.riskAtExitFor({ ...EXIT.basis, seasons }, 'S1', 'AD5'), null);
    const noHigh = EXIT.basis.seasons.map((g) => (g.status === 'ok' ? { ...g, high: 'late' } : g));
    assert.equal(D.endedBeforeFor({ ...EXIT.basis, seasons: noHigh }, 'S1', 'unknown'), null);
  });

  test('the column says the risk at exit in one sentence, for the class in use, and hides it with HLS off (SA-D90)', () => {
    const data = exampleData({ a: NO_REPORT });
    const t = A.makeT(COPY, 'en');
    const f = data.fields.a;
    const any = A.buildIndexView(data, ui(data)).columns[0].head.exitLines;
    const eb = D.endedBeforeFor(f.window_basis, 'S1', 'unknown');
    // Ended before is on the NASA line below the answer, never repeated in the column.
    assert.ok(!any.some((x) => x.text === t('window.ended_before_any_class', { k: String(eb.k), n: String(eb.n) })));
    const rx = D.riskAtExitFor(f.window_basis, 'S1', 'unknown');
    assert.ok(rx.groups.length >= 1);
    assert.equal(any.length, 1, 'one satellite sentence');
    assert.match(any[0].text, /second-crop corn/);
    for (const g of rx.groups) assert.ok(any[0].text.includes(`${g.k} of ${rx.n} seasons`), any[0].text);
    assert.equal(any[0].ledger, 'field_a.risk_at_exit');
    const ad3 = A.buildIndexView(data, ui(data, { soilClass: 'AD3' })).columns[0].head.exitLines;
    const r3 = D.riskAtExitFor(f.window_basis, 'S1', 'AD3');
    assert.equal(ad3.length, 1);
    if (r3.groups.length === 1) {
      const g = r3.groups[0];
      assert.equal(ad3[0].text, g.risk === null
        ? t('window.risk_at_exit.outside', { crop: t('page.rows.L2.crops.corn'), k: String(g.k), n: String(r3.n) })
        : t('window.risk_at_exit.line', { r: String(g.risk), crop: t('page.rows.L2.crops.corn'), k: String(g.k), n: String(r3.n) }));
    } else {
      assert.ok(ad3[0].text.startsWith('Satellite estimate: the first green cycle ended in a ten-day period whose Zarc risk for second-crop corn was '), ad3[0].text);
    }
    assert.equal(ad3[0].ledger, 'field_a.risk_at_exit');
    assert.deepEqual(A.buildIndexView(data, ui(data, { hlsOn: false })).columns[0].head.exitLines, []);
    assert.deepEqual(A.buildIndexView(data, ui(data)).columns[2].head.exitLines, []);
    // Never a sowing date, never "inside".
    for (const lang of ['en', 'pt']) {
      for (const cls of D.SOIL_CLASSES) {
        const v = A.buildIndexView(data, ui(data, { lang, soilClass: cls }));
        for (const c of v.columns) for (const l of c.head.exitLines) assert.doesNotMatch(l.text, /inside|dentro|sow on|semeie/i);
      }
    }
  });

  test('the ribbon band follows the class selector: one class, or the lowest and highest of every class', () => {
    const data = exampleData({ a: NO_REPORT });
    const t = A.makeT(COPY, 'en');
    const b0 = A.buildIndexView(data, ui(data)).ribbon.band;
    assert.deepEqual(b0.rows.map((r) => r.id), ['S1', 'S2']);
    assert.equal(b0.note, t('page.ribbon.risk_band.any'));
    assert.equal(b0.ledger, 'field_a.zarc_risk_band');
    assert.ok(b0.legend.some((l) => l.kind === 'risk-partial'));
    assert.match(b0.rows[0].svg, /^<svg class="risk-band"/);
    assert.equal(b0.rows[0].cells.length, 36);
    assert.equal(b0.rows[0].cells[0], t('page.ribbon.risk_band.outside'));   // 1-10 Sep: outside the zoning
    const b4 = A.buildIndexView(data, ui(data, { soilClass: 'AD4' })).ribbon.band;
    assert.equal(b4.note, t('page.ribbon.risk_band.declared', { class: 'AD4' }));
    const risk = data.fields.a.window_basis.S1.risk_band.by_class.AD4.risk;
    assert.deepEqual(b4.rows[0].cells, risk.map((v) => (v ? `${v}%` : t('page.ribbon.risk_band.outside'))));
    assert.ok(!b4.legend.some((l) => l.kind === 'risk-partial'));
    // A class with no Tábua row is drawn outside the zoning in every period, never as zero risk.
    const b1 = A.buildIndexView(data, ui(data, { soilClass: 'AD1' })).ribbon.band;
    assert.ok(b1.rows[0].cells.every((c) => c === t('page.ribbon.risk_band.outside')));
    // A field without window_basis has no band.
    assert.equal(A.riskBandView(t, { ...data.fields.a, window_basis: undefined }), null);
  });

  test('riskBandSvg draws a cell per period, halves for a range and a tick where some classes are outside', () => {
    const risk = new Array(36).fill(null);
    risk[12] = 20; risk[13] = 30; risk[14] = 40;
    const one = SV.riskBandSvg({ risk, label: 'x' });
    assert.equal((one.match(/<rect /g) ?? []).length, 36);
    assert.equal((one.match(/risk-none/g) ?? []).length, 33);
    const min = risk.slice(); const max = risk.map((v) => (v ? 40 : null));
    const part = risk.map((v, i) => i === 12);
    const two = SV.riskBandSvg({ min, max, someOutside: part, label: 'x' });
    assert.equal((two.match(/risk-partial/g) ?? []).length, 1);
    assert.equal((two.match(/<rect /g) ?? []).length, 36 + 2 + 1);   // two split cells, one tick
  });

  test('L2 shows the range across classes without a report, and the declared class with one', () => {
    const data = exampleData({ a: NO_REPORT });
    const t = A.makeT(COPY, 'en');
    const l2 = (u) => A.buildIndexView(data, u).columns.map((c) => c.cells.find((x) => x.id === 'L2').lines[0].text);
    const any = data.fields.a.risk.zarc_class.S1.any_class;
    const lines = l2(ui(data));
    assert.equal(lines[0], t('page.rows.L2.zarc_class', { crop: t('page.rows.L2.crops.corn'),
      class: t('page.rows.L2.any_class_range', { a: String(any.risk_min), b: String(any.risk_max) }) }));
    assert.ok(!lines.some((x) => x.endsWith(t.unknown())));
    const ad3 = data.fields.a.risk.zarc_class.S1.by_class.AD3;
    assert.match(l2(ui(data, { soilClass: 'AD3' }))[0], new RegExp(`${ad3.risk_min}‑${ad3.risk_max}%$`));
    assert.equal(l2(ui(data, { soilClass: 'AD1' }))[0],
      t('page.rows.L2.zarc_class', { crop: t('page.rows.L2.crops.corn'), class: t('page.rows.L2.class_not_zoned', { class: 'AD1' }) }));
  });
});

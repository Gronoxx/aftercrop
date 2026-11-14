// Written with an SI tool (Claude, Anthropic) for NASA Space Apps 2026. See AI_USE.md.
// Observation region (SA-D79): site/js/app.mjs in mode "observation" shows the observation
// sentence, the ribbon, the relief line, the climate lines and the sources, and never a state,
// a G value, a column, money or the sheet link. The default region is unchanged.
// Run: node --test tests/observation.test.mjs (Node 22). No browser: the view models are pure.

import { test, describe } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, existsSync } from 'node:fs';

import * as A from '../site/js/app.mjs';

const ROOT = new URL('../', import.meta.url);
const read = (rel) => JSON.parse(readFileSync(new URL(rel, ROOT), 'utf8'));
const text = (rel) => readFileSync(new URL(rel, ROOT), 'utf8');

const COPY = read('site/data/copy.json');
const REGION_COPY = read('site/data_regions/copy.json');
const OBS = 'site/data_regions/free_state/';
const BUILT = existsSync(new URL(`${OBS}field_a.json`, ROOT));

/** site/<url> as the page fetches it (loadAll), files read from disk. */
const fetchSite = async (url) => {
  const file = new URL(`site/${url}`, ROOT);
  return existsSync(file) ? JSON.parse(readFileSync(file, 'utf8')) : null;
};

// Synthetic observation documents (the shape pipeline/regions/observation.py writes), so these
// tests run without the downloaded data.
const CELLS = [...new Array(10).fill('no_data'), ...new Array(16).fill('green'), ...new Array(10).fill('not_green')];
const FIELD = {
  schema_version: '1', mode: 'observation', region_id: 'free_state', field_id: 'a', criterion: 'observation field',
  point: { lat: -27.32825, lon: 26.76265 }, polygon_px: [[130, 206], [158, 206], [158, 229], [130, 229]],
  area_ha: { v: 57.96, l: null }, n_inner_pixels: 546,
  relief: { source_id: 'nasadem', context_only: true, slope_class: 'flat', slope_pct: { mean: 1.1, median: 0.96, p90: 1.8 }, height_m: { mean: 1320.2, min: 1316, max: 1324 }, ledger: null },
  ndvi_series: { ledger: null, points: [{ date: '2024-01-10', ndvi: 0.71, nbr2: 0.2, sensor: 'S30', n_clean: 500, clean_frac: 0.9 }] },
  ribbon: { ledger: null, seasons: [{ season: '2023/24', cells: CELLS, n_obs: 40, longest_gap_days: 20, green_cycles: 1 }, { season: '2024/25', cells: CELLS, n_obs: 38, longest_gap_days: 30, green_cycles: 1 }] },
  green_end: [{ season: '2023/24', low: '2024-04-02', high: '2024-04-12', gap_days: { v: 10, l: null }, status: 'ok' }, { season: '2024/25', low: null, high: null, gap_days: { v: null, l: null }, status: 'to_confirm' }],
  ribbon_counts: { no_green: { median: 10, of: 36, seasons: 2, per_season: [], ledger: null } },
  climate: { start: '11-01', end: '04-30', n: 41, left_out: [], frost: { below_c: 2, k: 3, seasons: [] }, rain: { median_mm: 455.3, p10_mm: 300.4, driest: [{ season: '1991/92', mm: 210.2 }, { season: '2015/16', mm: 250.9 }, { season: '1982/83', mm: 270 }] }, period_kind: 'fixed_summer_period', years: [1984, 2025], first_season: '1984/85', last_season: '2024/25' },
};
const REGION = {
  schema_version: '1', region_id: 'free_state', mode: 'observation', name: 'Free State, South Africa',
  pack: { decision: false, strategies: false, calendar: false, trials: false, reviewer: false },
  sources: [
    { id: 'hls_vi_v2', title: 'HLS-VI v2.0', license: 'NASA open data', detail: { granules: 900, band_files: 2500, mb: 120.5, seasons: ['2018/19', '2025/26'], tile: 'T35JMK' } },
    { id: 'nasadem', title: 'NASADEM_HGT v001', license: 'NASA data', detail: { tiles: 1, mb: 6.3 } },
    { id: 'power_daily', title: 'NASA POWER daily', license: 'NASA data', detail: { point: [-27.3282, 26.7627], period: ['1984-01-01', '2025-12-31'], mb: 1.2 } },
  ],
};

function obsData({ region = REGION, field = FIELD, base = 'data_regions/free_state/' } = {}) {
  return A.normalizeData({
    copy: A.mergeRegionCopy(COPY, REGION_COPY), region, trials: null, scoreboard: null, ablation: null,
    ledger: null, manifest: null, fields: field ? { a: field } : {}, base,
  });
}

function ui(data, patch = {}) {
  return { ...A.initialUi(data, patch.lang ?? 'en'), ...patch };
}

function* strings(o) {
  if (typeof o === 'string') yield o;
  else if (Array.isArray(o)) for (const x of o) yield* strings(x);
  else if (o && typeof o === 'object') for (const v of Object.values(o)) yield* strings(v);
}

/** Every state word and every money word of the copy, in both languages. */
function verdictWords() {
  const out = [];
  for (const lang of ['en', 'pt']) {
    const L = COPY.langs[lang];
    out.push(...Object.values(L.states));
  }
  return out.filter((s) => typeof s === 'string' && s.length > 3);
}

/** Visible strings of a view, svg markup stripped. */
function shown(view) {
  return [...strings(view)].map((s) => s.replace(/<[^>]*>/g, ' ')).join(' | ');
}

describe('observation region (SA-D79)', () => {
  test('region.json mode "observation" makes the page an observation page, not an example', () => {
    const d = obsData();
    assert.equal(d.isObservation, true);
    assert.equal(d.isExample, false, 'no EXAMPLE banner on real NASA data');
    assert.equal(d.isRehearsal, false);
  });

  for (const lang of ['en', 'pt']) {
    test(`${lang}: the sentence, the ribbon, relief, climate and sources, no state and no G anywhere`, () => {
      const d = obsData();
      const v = A.buildIndexView(d, ui(d, { lang }));
      const R = REGION_COPY.langs[lang];
      assert.equal(v.observation, true);
      assert.equal(v.obs.sentence, R.observation.sentence);
      assert.equal(v.obs.banner, R.observation.banner);
      assert.equal(v.ribbon.rows.length, 2);
      assert.equal(v.ribbon.band, null, 'no Zarc band');
      assert.ok(!v.ribbon.legend.some((l) => l.kind === 'zarc-end'), 'no Zarc window end in the legend');
      assert.equal(v.obs.relief.length, 2);
      assert.equal(v.obs.climate.length, 4);
      assert.equal(v.obs.sources.length, 3);
      for (const k of ['answer', 'columns', 'rows', 'inputs', 'scenarios', 'limits', 'nitrogen', 'sheetHref', 'moneyRefs', 'priority', 'nasa', 'yearStrip']) {
        assert.ok(!(k in v), `${k} must not exist in an observation view`);
      }
      const all = shown(v);
      for (const w of verdictWords()) assert.ok(!all.includes(w), `state word ${JSON.stringify(w)} on the observation page`);
      assert.ok(!/\bG\s*[=:]|\bG\b\s*\d|sc\/ha|R\$/.test(all), all);
      assert.ok(!/[{}]|undefined|NaN|\bnull\b|—|;/.test(all.replace(/<[^>]*>/g, '')), all);
    });
  }

  test('the climate lines read the fixed summer period, as rendered in English and Portuguese', () => {
    const d = obsData();
    const en = A.buildIndexView(d, ui(d)).obs.climate.map((l) => l.text);
    assert.deepEqual(en.slice(0, 3), [
      'Nights below 2 °C, 1 Nov to 30 Apr: in 3 of 41 summers',
      'Rain, 1 Nov to 30 Apr: median 455 mm. The driest tenth of summers had 300 mm or less.',
      'Driest summers: 1991/92 (210 mm), 2015/16 (251 mm), 1982/83 (270 mm)',
    ]);
    assert.ok(en[3].includes('fixed summer period') && en[3].includes('1984/85 to 2024/25'), en[3]);
    const pt = A.buildIndexView(d, ui(d, { lang: 'pt' })).obs.climate.map((l) => l.text);
    assert.equal(pt[0], 'Noites abaixo de 2 °C, de 1 nov a 30 abr: em 3 de 41 verões');
  });

  test('relief and field lines', () => {
    const d = obsData();
    const v = A.buildIndexView(d, ui(d));
    assert.equal(v.obs.relief[0].text, 'Flat relief, median slope 1%, height 1,316 to 1,324 m.');
    assert.equal(v.obs.field, 'One field of 58 ha, drawn by hand on the HLS image, at 27.3283° S, 26.7627° E.');
    assert.equal(v.obs.greenEnd, 'End of the first green cycle found in 1 of 2 seasons. The other seasons are to confirm or have no data.');
  });

  test('without the region copy the default page is untouched: the decision view, no observation key', () => {
    assert.equal(A.mergeRegionCopy(COPY, null), COPY);
    const merged = A.mergeRegionCopy(COPY, REGION_COPY);
    assert.deepEqual(Object.keys(merged.langs.en).filter((k) => k !== 'regions'), Object.keys(COPY.langs.en));
    assert.equal(merged.langs.en.states, COPY.langs.en.states);
  });

  test('the default region: site/data loads as before, decision mode, and How we know links to the observation page', async () => {
    const data = await A.loadAll(fetchSite, 'data/');
    assert.equal(data.isObservation, false);
    assert.equal(data.region.mode, 'decision');
    assert.deepEqual(data.fieldIds, ['a', 'b', 'c']);
    const v = A.buildIndexView(data, ui(data));
    assert.equal(v.observation, false);
    assert.ok(typeof v.answer.text === 'string' && v.answer.text.length > 0);
    assert.equal(v.columns.length, 3);
    for (const lang of ['en', 'pt']) {
      const how = A.buildHowView(data, ui(data, { lang }));
      assert.equal(how.regionLink.text, REGION_COPY.langs[lang].how_link);
      assert.equal(how.regionLink.href, 'index.html?data=data_regions/free_state/');
    }
    assert.equal(A.dataBase('?data=data_regions/free_state/'), 'data_regions/free_state/');
  });

  test('index.html: decision parts only in x-if="!v.observation", observation parts only in x-if="v.observation"', () => {
    const src = text('site/index.html');
    for (const id of ['sec-question', 'sec-answer', 'sec-columns', 'your-numbers', 'your-limits', 'sec-year-strip', 'sec-nasa', 'sec-closing']) {
      const i = src.indexOf(`id="${id}"`);
      assert.ok(i > 0, id);
      const before = src.slice(0, src.lastIndexOf('<section', i));
      assert.ok(before.trimEnd().endsWith('<template x-if="!v.observation">'), `${id} wrapped`);
      assert.ok(before.lastIndexOf('<template x-if="!v.observation">') > before.lastIndexOf('</template>\n\n'), `${id} wrapped`);
    }
    for (const id of ['sec-observation', 'sec-observation-facts']) {
      const at = src.indexOf(`id="${id}"`);
      const before = src.slice(0, src.lastIndexOf('<section', at));
      assert.ok(before.trimEnd().endsWith('<template x-if="v.observation">'), id);
    }
    assert.ok(src.includes('x-show="v.observation" x-text="v.observation ? v.obs.banner'), 'observation banner');
    assert.ok(src.indexOf('id="sec-ribbon"') > 0, 'the ribbon stays for both modes');
    assert.ok(text('site/how-we-know.html').includes('v.regionLink'));
  });

  test('the built Free State folder, when present: EN and PT with no state, no G and no placeholder', { skip: !BUILT && 'run make observation REGION=free_state' }, async () => {
    const data = await A.loadAll(fetchSite, 'data_regions/free_state/');
    assert.equal(data.isObservation, true);
    assert.equal(data.isExample, false);
    assert.deepEqual(data.fieldIds, ['a']);
    for (const lang of ['en', 'pt']) {
      const v = A.buildIndexView(data, ui(data, { lang }));
      assert.equal(v.ribbon.rows.length, 8, 'eight seasons');
      assert.equal(v.obs.sources.length, 3);
      const all = shown(v);
      for (const w of verdictWords()) assert.ok(!all.includes(w), `${lang}: ${w}`);
      assert.ok(!/[{}]|undefined|NaN|\bnull\b|—|;/.test(all), `${lang}: ${all}`);
    }
    const f = read(`${OBS}field_a.json`);
    const r = read(`${OBS}region.json`);
    assert.ok(!Object.values(r.pack).some(Boolean), 'no item of the Regional Pack');
    const { pack: _pack, ...regionRest } = r;
    for (const doc of [f, regionRest]) {
      const keys = JSON.stringify(doc);
      for (const k of ['"state"', '"G"', '"window_reading"', '"window_basis"', '"calendar"', '"risk"', '"strategies"', '"price_scenarios"']) {
        assert.ok(!keys.includes(k), `${k} in an observation document`);
      }
    }
  });

  test('an observation folder asks only for the files it has (no 404 in the console)', async () => {
    const asked = [];
    const fetchJson = async (url) => {
      asked.push(url);
      if (url.endsWith('region.json')) return { mode: 'observation', region_id: 'x' };
      return null;
    };
    await A.loadAll(fetchJson, 'data_regions/free_state/');
    const own = asked.filter((u) => u.startsWith('data_regions/free_state/'));
    assert.deepEqual(own.sort(), ['data_regions/free_state/field_a.json', 'data_regions/free_state/region.json']);
  });
});

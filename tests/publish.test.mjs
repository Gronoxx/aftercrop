// Written with an SI tool (Claude, Anthropic) for NASA Space Apps 2026. See AI_USE.md.
// Publishing package P4: the NASA switch placement (SA-D66), the four levels of state on How
// we know, the SI notice key in the JSON files, the missing example folder on the published site.
// Run: node --test tests/publish.test.mjs. Data: the fictitious example of tests/fixtures/site_example/.

import { test, describe } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

import * as A from '../site/js/app.mjs';

const ROOT = new URL('../', import.meta.url);
const read = (rel) => JSON.parse(readFileSync(new URL(rel, ROOT), 'utf8'));
const COPY = read('site/data/copy.json');
const FX = 'tests/fixtures/site_example/';
const NOTICE = 'Written with an SI tool (Claude, Anthropic) for NASA Space Apps 2026. See AI_USE.md.';

function files() {
  return {
    copy: COPY,
    region: read(`${FX}region.json`),
    trials: read(`${FX}trials_steps.json`),
    scoreboard: read(`${FX}scoreboard.json`),
    ablation: read(`${FX}ablation.json`),
    ledger: read(`${FX}ledger.json`),
    manifest: null,
    fields: { a: read(`${FX}field_a.json`), b: read(`${FX}field_b.json`) },
    base: 'data/',
  };
}

const data = (patch = {}) => A.normalizeData({ ...files(), ...patch });
const withMin = (min) => {
  const region = read(`${FX}region.json`);
  region.nasa_switch.main_page_if_states_changed_min.v = min;
  return data({ region });
};
const allText = (o) => JSON.stringify(o);

describe('NASA switch placement (SA-D66)', () => {
  test('the rule comes from region.json, as precompute writes it from the protocol', () => {
    const rule = A.switchRule(read(`${FX}region.json`));
    assert.equal(rule.min, 1);
    assert.equal(rule.otherwise, 'how_we_know');
    assert.equal(rule.ledger, 'region.nasa_switch.main_page_if_states_changed_min');
    assert.equal(A.switchRule({}), null);
    assert.equal(A.switchRule(null), null);
    assert.equal(A.switchRule({ nasa_switch: { main_page_if_states_changed_min: { v: -1, l: 'x' } } }), null);
    assert.equal(A.switchRule({ nasa_switch: { main_page_if_states_changed_min: { v: 0.5, l: 'x' } } }), null);
  });

  test('the count is switchCount on the starting numbers of the fields shown', () => {
    const d = data();
    const p = A.switchPlacement(d);
    const sw = A.switchCount(d, A.initialUi(d, 'en'));
    assert.deepEqual([p.X, p.Y], [sw.X, sw.Y]);
    assert.equal(p.Y, 2 * d.fieldIds.length, 'two options per field');
    assert.equal(p.onMain, p.X >= 1);
  });

  test('the example changes no state, so the main page leaves the switch out and How we know shows it', () => {
    const d = data();
    const p = A.switchPlacement(d);
    assert.equal(p.X, 0, 'fictitious example: HLS changes no state on its starting numbers');
    assert.equal(p.onMain, false);
    const ui = A.initialUi(d, 'en');
    assert.equal(A.buildIndexView(d, ui).nasa.onMain, false);
    const how = A.buildHowView(d, ui);
    assert.equal(how.switchPlace.contextOnly, COPY.langs.en.page.nasa_switch.context_only);
    assert.match(how.switchPlace.text, /changes 0 of 4 /);
    assert.match(how.switchPlace.text, /at least 1 of them, so it stays here/);
    assert.equal(how.switchPlace.ledger, 'browser.hls_switch');
  });

  test('with a minimum the count reaches, the switch is on the main page and not context only', () => {
    const d = withMin(0);
    assert.equal(A.switchPlacement(d).onMain, true);
    const ui = A.initialUi(d, 'en');
    assert.equal(A.buildIndexView(d, ui).nasa.onMain, true);
    const how = A.buildHowView(d, ui);
    assert.equal(how.switchPlace.contextOnly, null);
    assert.match(how.switchPlace.text, /so it is there/);
  });

  test('typed numbers never move the section', () => {
    const d = withMin(0);
    const ui = A.initialUi(d, 'en');
    for (const k of Object.keys(ui.raw)) ui.raw[k] = '1';
    ui.sowingDate = '2027-02-05';
    ui.hlsOn = false;
    assert.equal(A.buildIndexView(d, ui).nasa.onMain, true);
    assert.equal(A.buildIndexView(data(), ui).nasa.onMain, false);
  });

  test('without the rule the main page leaves the switch out and says why on How we know', () => {
    const region = read(`${FX}region.json`);
    delete region.nasa_switch;
    const d = data({ region });
    const ui = A.initialUi(d, 'en');
    assert.equal(A.buildIndexView(d, ui).nasa.onMain, false);
    const how = A.buildHowView(d, ui);
    assert.equal(how.switchPlace.text, COPY.langs.en.howweknow.switch_placement.no_rule);
    assert.equal(how.switchPlace.ledger, null);
  });

  test('the Portuguese text carries the same numbers', () => {
    const d = data();
    const how = A.buildHowView(d, { ...A.initialUi(d, 'pt'), lang: 'pt' });
    assert.match(how.switchPlace.text, /muda 0 de 4 /);
    assert.match(how.switchPlace.text, /ao menos 1 deles/);
  });
});

describe('four levels of state on How we know (SA-D66)', () => {
  const ids = (how) => how.levels.map((l) => l.id);

  test('four levels, in order, each with at least one line', () => {
    const d = data();
    const how = A.buildHowView(d, A.initialUi(d, 'en'));
    assert.deepEqual(ids(how), ['verified', 'obtained', 'not_executed', 'not_demonstrated']);
    for (const l of how.levels) {
      assert.equal(l.title, COPY.langs.en.howweknow.levels[l.id].title);
      assert.ok(l.items.length > 0, l.id);
    }
  });

  test('not yet executed follows the scoreboard statuses and the fields loaded', () => {
    const none = data({ scoreboard: null, fields: {} });
    const lv = A.stateLevels(A.makeT(COPY, 'en'), none);
    const notRun = lv.find((l) => l.id === 'not_executed').items;
    assert.ok(notRun.includes(COPY.langs.en.howweknow.levels.not_executed.fields));
    assert.ok(notRun.some((x) => x.includes('T0, T1, P1, A1')), 'trial chains');
    assert.ok(notRun.some((x) => x.includes('L0, L1, A2, A3, N1, H1, H2, R1')), 'bench');
    // The example scoreboard ran everything but H1 and H2.
    const ex = A.stateLevels(A.makeT(COPY, 'en'), data()).find((l) => l.id === 'not_executed').items;
    assert.deepEqual(ex, [COPY.langs.en.howweknow.levels.not_executed.bench.replace('{ids}', 'H1, H2')]);
  });

  test('data obtained lists the cached data sources of the manifest, not documents', () => {
    const manifest = {
      sources: [
        { source_id: 'hls_vi_v2', title: 'HLS-VI v2.0', kind: 'satellite', cached: true },
        { source_id: 'power_daily', title: 'NASA POWER daily', kind: 'reanalysis', cached: true },
        { source_id: 'nasadem', title: 'NASADEM_HGT v001', kind: 'dem', cached: false },
        { source_id: 'embrapa_docs', title: 'Embrapa Documentos', kind: 'trials', cached: true },
      ],
    };
    const lv = A.stateLevels(A.makeT(COPY, 'en'), data({ manifest }));
    assert.deepEqual(lv.find((l) => l.id === 'obtained').items, ['HLS-VI v2.0', 'NASA POWER daily']);
    const noManifest = A.stateLevels(A.makeT(COPY, 'en'), data());
    assert.deepEqual(noManifest.find((l) => l.id === 'obtained').items, [COPY.langs.en.missing.not_completed]);
  });

  test('verified internally never reads as a validation of the model', () => {
    const d = data();
    for (const lang of ['en', 'pt']) {
      const lv = A.stateLevels(A.makeT(COPY, lang), d);
      const verified = lv.find((l) => l.id === 'verified').items;
      assert.ok(verified.includes(COPY.langs[lang].howweknow.levels.verified.tests));
      assert.doesNotMatch(allText(lv), /validat|validad|valida[cç]/i);
    }
    assert.match(COPY.langs.en.howweknow.levels.verified.tests, /not the model/);
    const noL0 = A.stateLevels(A.makeT(COPY, 'en'), data({ scoreboard: null }));
    assert.ok(!noL0[0].items.includes(COPY.langs.en.howweknow.levels.verified.l0), 'L0 only when it passed');
  });
});

describe('published site', () => {
  test('the _notice key of every JSON file changes nothing the page shows', () => {
    const stamp = (o) => (o && typeof o === 'object' ? { _notice: NOTICE, ...o } : o);
    const f = files();
    const stamped = { ...f };
    for (const k of ['copy', 'region', 'trials', 'scoreboard', 'ablation', 'ledger']) stamped[k] = stamp(f[k]);
    stamped.fields = { a: stamp(f.fields.a), b: stamp(f.fields.b) };
    const d0 = A.normalizeData(f);
    const d1 = A.normalizeData(stamped);
    const ui = A.initialUi(d0, 'en');
    assert.deepEqual(A.buildIndexView(d1, ui), A.buildIndexView(d0, ui));
    assert.deepEqual(A.buildHowView(d1, ui), A.buildHowView(d0, ui));
    assert.equal(d1.isExample, d0.isExample);
    assert.doesNotMatch(allText(A.buildHowView(d1, ui)), /SI tool/);
  });

  test('a ?data= folder that is not deployed says so instead of an empty example', async () => {
    const d = await A.loadAll(async (url) => (url === 'data/copy.json' ? COPY : null), A.dataBase('?data=../tests/fixtures/site_example/'));
    assert.equal(d.dataFolderMissing, true);
    assert.equal(d.isExample, true);
    const ui = A.initialUi(d, 'en');
    assert.equal(A.buildIndexView(d, ui).dataFolderMissing, COPY.langs.en.missing.example_folder);
    assert.equal(A.buildHowView(d, ui).dataFolderMissing, COPY.langs.en.missing.example_folder);
    assert.equal(A.buildIndexView(d, ui).nasa.onMain, false);
    // The published data/ folder and a found example never show the line.
    assert.equal(A.normalizeData({ copy: COPY, base: 'data/' }).dataFolderMissing, false);
    assert.equal(A.normalizeData({ ...files(), base: '../tests/fixtures/site_example/' }).dataFolderMissing, false);
  });
});

// Written with an SI tool (Claude, Anthropic) for NASA Space Apps 2026. See AI_USE.md.
// Soil sensitivity after the freeze (SA-D77) on How we know: site/js/sensitivity.mjs.
// Run: node --test tests/sensitivity.test.mjs (Node 22). The document here is example, fictitious.

import { test, describe } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

import * as A from '../site/js/app.mjs';
import * as SENS from '../site/js/sensitivity.mjs';

const ROOT = new URL('../', import.meta.url);
const COPY = JSON.parse(readFileSync(new URL('site/data/copy.json', ROOT), 'utf8'));
const HOW = readFileSync(new URL('site/how-we-know.html', ROOT), 'utf8');

function row(id, status, value) {
  return { id, status, statistic: 'x', value, threshold: null, n: 10, note: null, ledger: `sensitivity.tests.${id}` };
}

function exampleDoc(patch = {}) {
  return {
    schema_version: 1,
    role: 'sensitivity after the freeze',
    frozen: false,
    label_id: 'model_not_validated_here',
    label_text: 'Model not validated here',
    tests: [
      row('L0', 'fail', { n_artifacts: 5 }),
      row('T0', 'pass', { s: 23 }),
      row('T1', 'fail', { s: 19, rmse_kg_ha: 1648.25, rmse_constant_in_sample_kg_ha: 279.8 }),
      row('P1', 'fail', {}),
      row('A1', 'fail', { d: 3 }),
    ],
    zero_yield: {
      ledger: 'sensitivity.zero_yield',
      by_site: {
        Londrina: { sensitivity: { base: { zero: 2, simulated: 18 } }, frozen: { base: { zero: 11, simulated: 18 } } },
        Cafelandia: { sensitivity: { base: { zero: 2, simulated: 18 } }, frozen: { base: { zero: 6, simulated: 18 } } },
      },
    },
    ...patch,
  };
}

describe('sensitivity line', () => {
  test('English: label from the copy, statuses, statistics and zero-yield counts', () => {
    const line = SENS.sensitivityLine(exampleDoc(), A.makeT(COPY, 'en'));
    assert.ok(line.includes(A.makeT(COPY, 'en')('labels.model_not_validated_here.title')));
    assert.match(line, /The label of this run is still .+, because L0 checks the field chains, which this run does not change \(L0 fail\)/);
    assert.match(line, /What the run adds: T0 pass \(S 23\)/);
    assert.match(line, /S 19, RMSE 1,648 kg\/ha/);
    assert.match(line, /280 kg\/ha/);
    assert.match(line, /A1 fail \(D 3\)/);
    assert.match(line, /Londrina 2 of 18 \(frozen run: 11\), Cafelândia 2 of 18 \(frozen run: 6\)/);
    assert.ok(!/undefined|null|NaN|\{/.test(line), line);
  });

  test('Portuguese: same numbers in the marks of the language', () => {
    const t = A.makeT(COPY, 'pt');
    const line = SENS.sensitivityLine(exampleDoc(), t);
    assert.ok(line.includes(t('labels.model_not_validated_here.title')));
    assert.ok(line.includes(`RMSE ${A.formatNumber(1648.25, t)} kg/ha`));
    assert.match(line, /Londrina 2 de 18/);
  });

  test('a frozen or unknown document is never shown', () => {
    const t = A.makeT(COPY, 'en');
    assert.equal(SENS.sensitivityLine(exampleDoc({ frozen: true }), t), null);
    assert.equal(SENS.sensitivityLine(exampleDoc({ role: 'scoreboard' }), t), null);
    assert.equal(SENS.sensitivityLine(exampleDoc({ tests: [] }), t), null);
    assert.equal(SENS.sensitivityLine(null, t), null);
    const l0pass = exampleDoc();
    l0pass.tests[0] = { ...l0pass.tests[0], status: 'pass' };
    assert.equal(SENS.sensitivityLine(l0pass, t), null, 'the line would be false if L0 changed');
  });

  test('page language from <html lang>', () => {
    const doc = (lang) => ({ documentElement: { getAttribute: () => lang } });
    assert.equal(SENS.pageLang(doc('pt-BR')), 'pt');
    assert.equal(SENS.pageLang(doc('en')), 'en');
    assert.equal(SENS.pageLang(doc(null)), 'en');
  });
});

describe('How we know section', () => {
  test('hidden until the file is read, after the scoreboard, with its copy keys', () => {
    assert.match(HOW, /<section id="hw-sensitivity"[^>]*\bhidden\b/);
    assert.ok(HOW.indexOf('id="hw-sensitivity"') > HOW.indexOf('id="hw-scoreboard"'));
    assert.match(HOW, /<script type="module" src="js\/sensitivity.mjs"><\/script>/);
    const t = A.makeT(COPY, 'en');
    for (const key of ['title', 'cause', 'unchanged', 'result', 'zero_site']) {
      assert.ok(t.has(`howweknow.sensitivity.${key}`), key);
      assert.ok(A.makeT(COPY, 'pt').has(`howweknow.sensitivity.${key}`), key);
    }
    assert.ok(t('howweknow.sensitivity.unchanged').includes('does not change it'));
  });
});

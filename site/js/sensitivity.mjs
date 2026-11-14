// Written with an SI tool (Claude, Anthropic) for NASA Space Apps 2026. See AI_USE.md.
// Soil sensitivity after the freeze (plan SA-D77) on How we know. It reads data/sensitivity.json,
// written by pipeline.bench.sensitivity, and fills one line of #hw-sensitivity in the language of
// the page. The section stays hidden without the file. It never reads or changes the frozen label:
// the label of the page comes from scoreboard.json through app.mjs.

import { dataBase, formatNumber, makeT } from './app.mjs';

export const SENS_FILE = 'sensitivity.json';
export const SENS_TESTS = ['L0', 'T0', 'T1', 'P1', 'A1'];
// The scoreboard keys drop accents ("Cafelandia"); the page keeps the place name.
const PLACE = { Cafelandia: 'Cafelândia' };

const isObj = (x) => x !== null && typeof x === 'object' && !Array.isArray(x);

/** True for a document this section may show: a sensitivity after the freeze, never frozen. */
export function isSensitivity(doc) {
  return isObj(doc) && doc.role === 'sensitivity after the freeze' && doc.frozen === false
    && typeof doc.label_id === 'string' && Array.isArray(doc.tests);
}

/** The result line in the language of `t`, or null when the document cannot be shown. Pure. */
export function sensitivityLine(doc, t) {
  if (!isSensitivity(doc)) return null;
  const by = Object.fromEntries(doc.tests.filter(isObj).map((r) => [r.id, r]));
  if (!SENS_TESTS.every((id) => isObj(by[id]))) return null;
  // The line says the label is unchanged because L0 is: it holds only while L0 fails as frozen.
  if (by.L0.status !== 'fail') return null;
  const status = (id) => {
    const key = `howweknow.scoreboard.status.${by[id].status}`;
    return t.has(key) ? t(key) : t.unknown();
  };
  const val = (id) => (isObj(by[id].value) ? by[id].value : {});
  const zeroBy = isObj(doc.zero_yield?.by_site) ? doc.zero_yield.by_site : {};
  const zero = Object.entries(zeroBy).map(([site, z]) => t('howweknow.sensitivity.zero_site', {
    site: PLACE[site] ?? site,
    k: formatNumber(z?.sensitivity?.base?.zero, t),
    n: formatNumber(z?.sensitivity?.base?.simulated, t),
    k_frozen: formatNumber(z?.frozen?.base?.zero, t),
  })).join(', ');
  const labelKey = `labels.${doc.label_id}.title`;
  return t('howweknow.sensitivity.result', {
    label: t.has(labelKey) ? t(labelKey) : doc.label_text,
    t0: status('T0'),
    t0_s: formatNumber(val('T0').s, t),
    t1: status('T1'),
    t1_s: formatNumber(val('T1').s, t),
    t1_rmse: formatNumber(val('T1').rmse_kg_ha, t),
    t1_constant: formatNumber(val('T1').rmse_constant_in_sample_kg_ha, t),
    p1: status('P1'),
    a1: status('A1'),
    a1_d: formatNumber(val('A1').d, t),
    l0: status('L0'),
    zero: zero || t.unknown(),
  });
}

/** Language of the page from <html lang> (app.mjs sets it on every change). */
export function pageLang(doc) {
  return String(doc.documentElement.getAttribute('lang') || 'en').toLowerCase().startsWith('pt') ? 'pt' : 'en';
}

async function fetchJson(win, url) {
  try {
    const res = await win.fetch(url, { cache: 'no-cache' });
    return res.ok ? await res.json() : null;
  } catch {
    return null;
  }
}

/** Start the section in a browser. Never runs in Node. */
export async function bootSensitivity(win = globalThis.window) {
  const doc = win.document;
  const section = doc.getElementById('hw-sensitivity');
  const line = section?.querySelector('[data-sens-result]');
  if (!section || !line) return;
  const [copy, sens] = await Promise.all([
    fetchJson(win, 'data/copy.json'),
    fetchJson(win, `${dataBase(win.location.search)}${SENS_FILE}`),
  ]);
  if (!copy || !isSensitivity(sens)) return;
  const render = () => {
    const text = sensitivityLine(sens, makeT(copy, pageLang(doc)));
    if (text === null) return;
    line.textContent = text;
    section.hidden = false;
  };
  render();
  new win.MutationObserver(render).observe(doc.documentElement, { attributes: true, attributeFilter: ['lang'] });
}

if (typeof window !== 'undefined' && typeof document !== 'undefined' && document.body?.dataset?.page === 'how') {
  bootSensitivity(window);
}

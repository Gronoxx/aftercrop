// Written with an SI tool (Claude, Anthropic) for NASA Space Apps 2026. See AI_USE.md.
// Aftercrop page logic (plan sections 7, 8.3, 8.4, 8.6 and 8.7, sheet M7).
//
// Two layers:
// - pure functions (copy lookup, number parsing, decision inputs, view models) that run in
//   Node for tests/site.test.mjs, with no DOM and no network;
// - boot(), which runs only in a browser: it reads site/data/*.json with relative fetches,
//   starts Alpine 3.17.5 from site/vendor and wires the Evidence Ledger.
//
// Every user-facing word comes from copy.json (config/copy_en.yaml and copy_pt.yaml, SA-D59).
// PENDING_COPY holds the few keys the copy files do not have yet, listed in the M7 report. A key
// found in copy.json always wins over PENDING_COPY.
// The decision rule is site/js/decision.mjs, used as is. Nothing typed leaves the browser.

import * as D from './decision.mjs';
import * as S from './svg.mjs';
import { recordsOf, attachLedger } from './ledger.mjs';

export const LANGS = Object.freeze(['en', 'pt']);
export const DEFAULT_LANG = 'en';
export const STORAGE_KEY = 'aftercrop.lang';
export const FIELD_IDS = Object.freeze(['a', 'b', 'c']);
export const STRATEGIES = Object.freeze(['S1', 'S2', 'S3']);
export const NUMBER_FIELDS = Object.freeze(['p_soy', 'p_corn', 'p_wheat', 'Y_corn', 'Y_wheat', 'c_corn', 'c_wheat', 'c_grass']);
export const NITROGEN_FIELDS = Object.freeze(['urea_price', 'n_dose']);
export const UREA_N_SHARE = 0.46; // urea with 46% N (plan 14.8)
export const PRIORITIES = Object.freeze(['income', 'water', 'soil']);
export const DEFAULT_PRIORITY = 'income';
export const L5_LABEL = 'direction_supported_exploratory'; // L5 only under the fourth label (M7.3)

// Ledger ids of numbers computed in the browser (8.6: value null, transform names the function).
// pipeline/ledger/registry.py owns the ids (STATIC_IDS): tests/site.test.mjs checks each one
// against what the registry declares.
export const BROWSER_LEDGER = Object.freeze({
  M_corn: 'browser.margin_corn',
  G_S2: 'browser.S2.G',
  G_S3: 'browser.S3.G',
  switch: 'browser.hls_switch',
  nitrogen: 'browser.nitrogen_exposure',
  field_order: 'browser.field_order',
});

// Ids above that the registry does not declare yet, listed in the M7 report. Until it does, the
// page shows their numbers without a ledger link, so no click opens an empty record. Delete an
// id here once the registry declares it (tests/site.test.mjs says when).
export const PENDING_LEDGER = Object.freeze([]);

/** Ledger id of a number computed in the browser, or null while the registry lacks it. */
export function browserLedger(key) {
  const id = BROWSER_LEDGER[key];
  return typeof id === 'string' && !PENDING_LEDGER.includes(id) ? id : null;
}

// Basemap of the field window (8.4 step 3), copied by make from data/interim/basemap_<window id>.*
// (pipeline/fields/basemap.py). polygon_px are pixels of that PNG, from its top-left corner.
export const BASEMAP = Object.freeze({ png: 'img/basemap.png', georef: 'img/basemap.json' });

/**
 * Basemap URLs for a data folder: the published img/ for data/, else <folder>img/ (a rehearsal
 * folder holds its own basemap, SA-D63). Pure.
 */
export function basemapUrls(base) {
  if (!base || base === 'data/') return BASEMAP;
  return { png: `${base}img/basemap.png`, georef: `${base}img/basemap.json` };
}

// H2 statuses that can mean a local reading (M8.5), each only with a literal comment.
export const H2_REVIEW_STATUSES = Object.freeze(['pass', 'fail', 'reported']);

// Limits of 7.4: the answer that triggers the removal. The option removed comes from
// region.json constraints[].removes, with this project-rule fallback when region.json is absent.
export const LIMITS = Object.freeze({
  no_narrow_row_seeder: { options: ['yes', 'no'], trigger: 'no', removes: ['S3'] },
  no_grass_seeding: { options: ['extra_box', 'with_fertilizer', 'broadcast', 'none'], trigger: 'none', removes: ['S2'] },
  no_wheat_buyer: { options: ['yes', 'no'], trigger: 'no', removes: ['S3'] },
  rented_land: { options: ['own', 'rented'], trigger: null, removes: [] },
  zarc_insurance_credit: { options: ['yes', 'no'], trigger: null, removes: [] },
});

// Copy keys missing from config/copy_*.yaml. Reported, never invented silently.
export const PENDING_COPY = Object.freeze({});

// Seals of each row (plan 7.5).
export const ROW_SEALS = Object.freeze({
  L1: ['official_rule', 'measured_in_trials'],
  L2: ['official_rule', 'reanalysis'],
  L3: ['measured_in_trials', 'derived_from_trials'],
  L4: ['your_numbers', 'derived_from_trials'],
  L5: ['simulated'],
  E: ['published_climatology'],
});

// Proper names of data sources on the scale ladder. Names, not prose.
const SCALE_SOURCES = Object.freeze([
  ['power_cell_km', 'NASA POWER', 'km'],
  ['soil_m', 'SoilGrids', 'm'],
  ['dem_m', 'NASADEM', 'm'],
  ['erosivity_km', 'GloREDa', 'km'],
]);

// ================================================================ copy

function isObj(v) {
  return typeof v === 'object' && v !== null && !Array.isArray(v);
}

/** Value at a dotted key ("page.answer.caption"), or undefined. List items by index. */
export function lookup(tree, key) {
  let node = tree;
  for (const part of String(key).split('.')) {
    if (Array.isArray(node)) node = node[Number(part)];
    else if (isObj(node) && Object.hasOwn(node, part)) node = node[part];
    else return undefined;
  }
  return node;
}

/** The language tree of copy.json for `lang`. */
export function copyTree(copy, lang) {
  return lookup(copy, `langs.${lang}`) ?? {};
}

export function normalizeLang(lang) {
  return LANGS.includes(lang) ? lang : DEFAULT_LANG;
}

const PLACEHOLDER = /\{([A-Za-z_][A-Za-z0-9_]*)\}/g;

/** Placeholders of a template, in order. */
export function placeholders(template) {
  return [...String(template).matchAll(PLACEHOLDER)].map((m) => m[1]);
}

/**
 * Translator for one language. t(key, vars) fills {name} placeholders. A missing var shows the
 * "Unknown" word, never "undefined" or "null". tIf(key, vars) gives null when a var is missing.
 * `used` collects every key asked for (tests/site.test.mjs reads it).
 */
export function makeT(copy, lang, { used = null } = {}) {
  const L = normalizeLang(lang);
  const tree = copyTree(copy, L);
  const raw = (key) => {
    if (used) used.add(key);
    const v = lookup(tree, key);
    if (v !== undefined) return v;
    const p = PENDING_COPY[key];
    return p ? p[L] : undefined;
  };
  const unknownWord = () => {
    const u = raw('missing.unknown');
    return typeof u === 'string' ? u : '';
  };
  const fillWith = (template, vars, strict) => {
    let missing = false;
    const out = String(template).replace(PLACEHOLDER, (m, name) => {
      const v = vars?.[name];
      if (v === undefined || v === null || v === '' || (typeof v === 'number' && !Number.isFinite(v))) {
        missing = true;
        return strict ? m : unknownWord();
      }
      return String(v);
    });
    return strict && missing ? null : out;
  };
  const t = (key, vars = undefined) => {
    const v = raw(key);
    if (typeof v !== 'string') return Array.isArray(v) ? v : '';
    return vars ? fillWith(v, vars, false) : v;
  };
  t.lang = L;
  t.has = (key) => raw(key) !== undefined;
  t.tIf = (key, vars) => {
    const v = raw(key);
    return typeof v === 'string' ? fillWith(v, vars ?? {}, true) : null;
  };
  t.list = (key) => {
    const v = raw(key);
    return Array.isArray(v) ? v : [];
  };
  t.unknown = unknownWord;
  return t;
}

/** Text of every `data-copy` element, and of `data-copy-label` (aria-label) and `data-copy-alt`. */
export function applyCopy(root, t) {
  for (const el of root.querySelectorAll('[data-copy]')) el.textContent = t(el.getAttribute('data-copy'));
  for (const el of root.querySelectorAll('[data-copy-label]')) el.setAttribute('aria-label', t(el.getAttribute('data-copy-label')));
  for (const el of root.querySelectorAll('[data-copy-alt]')) el.setAttribute('alt', t(el.getAttribute('data-copy-alt')));
}

// ================================================================ numbers and dates

function marks(t) {
  const dec = t('meta.decimal_mark') || '.';
  const thou = t('meta.thousands_mark') || ',';
  return { dec, thou };
}

/** Number in the language of the page, or the "Unknown" word. Never NaN, null or undefined. */
export function formatNumber(v, t, digits = 0) {
  if (typeof v !== 'number' || !Number.isFinite(v)) return t.unknown();
  const { dec, thou } = marks(t);
  const neg = v < 0;
  const fixed = Math.abs(v).toFixed(digits);
  const [int, frac] = fixed.split('.');
  const grouped = int.replace(/\B(?=(\d{3})+(?!\d))/g, thou);
  const s = frac && /[1-9]/.test(frac) ? `${grouped}${dec}${frac}` : grouped;
  return neg && /[1-9]/.test(fixed) ? `-${s}` : s;
}

/** Number for an input box: no thousands mark, the decimal mark of the language. */
export function formatInput(v, t) {
  if (typeof v !== 'number' || !Number.isFinite(v)) return '';
  const { dec } = marks(t);
  return String(v).replace('.', dec);
}

/**
 * Number typed in the language of the page. '' gives undefined (missing). Anything that is not
 * a plain number gives NaN, which decision.mjs refuses (Not enough evidence, We need your ...).
 */
export function parseNumber(raw, lang) {
  if (raw === undefined || raw === null) return undefined;
  let s = String(raw).trim().replace(/\s/g, '');
  if (s === '') return undefined;
  const L = normalizeLang(lang);
  const dec = L === 'pt' ? ',' : '.';
  const thou = L === 'pt' ? '.' : ',';
  const grouped = new RegExp(`^[+-]?\\d{1,3}(\\${thou}\\d{3})+(\\${dec}\\d+)?$`);
  if (grouped.test(s)) s = s.split(thou).join('');
  else if (s.includes(thou) && !s.includes(dec)) s = s.replace(thou, dec); // a lone mark of the other language
  else if (s.includes(thou)) return NaN;
  s = s.replace(dec, '.');
  if (!/^[+-]?\d+(\.\d+)?$/.test(s)) return NaN;
  return Number(s);
}

/** "2026-02-12" as "12 Feb 2026" with the month names of the language. */
export function formatDate(iso, t) {
  const p = S.parseIso(iso);
  if (!p) return t.unknown();
  const months = t.list('page.year_strip.months');
  return `${p[2]} ${months[p[1] - 1] ?? p[1]} ${p[0]}`;
}

/** Num of 8.6 ({ v, l }) to its value, or a bare number, or null. */
export function numValue(x) {
  if (isObj(x) && Object.hasOwn(x, 'v')) return typeof x.v === 'number' && Number.isFinite(x.v) ? x.v : null;
  return typeof x === 'number' && Number.isFinite(x) ? x : null;
}

export function numLedger(x) {
  return isObj(x) && typeof x.l === 'string' ? x.l : null;
}

// ================================================================ data

export const DATA_FILES = Object.freeze({
  copy: 'copy.json',
  region: 'region.json',
  trials: 'trials_steps.json',
  scoreboard: 'scoreboard.json',
  ablation: 'ablation.json',
  ledger: 'ledger.json',
  manifest: 'manifest.json',
});

/**
 * Base folder of the JSON files. Default "data/". A ?data=<relative path> query serves the
 * fictitious fixtures during development. Absolute URLs, "//" and ".." beyond the site root
 * are refused, so the page never fetches from another origin.
 */
export function dataBase(search) {
  const q = new URLSearchParams(search ?? '').get('data');
  if (!q) return 'data/';
  if (/^[a-z][a-z0-9+.-]*:/i.test(q) || q.startsWith('/') || q.includes('\\')) return 'data/';
  return q.endsWith('/') ? q : `${q}/`;
}

/**
 * The query that keeps a ?data= folder across the site's own pages (index, How we know,
 * sheet): "" for the default data/ folder, else "?data=<base>". Without it a link from a
 * rehearsal or fixture folder opened the next page on site/data/. Pure.
 */
export function dataQuery(base) {
  if (!base || base === 'data/') return '';
  return `?${new URLSearchParams({ data: base })}`;
}

/** Href of one of the site's own pages, with the data folder kept (dataQuery). Pure. */
export function pageHref(page, base) {
  return `${page}${dataQuery(base)}`;
}

/** Copy of the observation regions (SA-D79), from the site root like data/copy.json. */
export const REGION_COPY = 'data_regions/copy.json';

/** The observation region of the site (SA-D79): the main page on its data folder. */
export const OBSERVATION_HREF = 'index.html?data=data_regions/free_state/';

/**
 * copy.json with the region copy (config/copy_regions_*.yaml) under the key "regions" of each
 * language. site/data/copy.json stays as frozen. Without a region copy, the copy as it is. Pure.
 */
export function mergeRegionCopy(copy, regionCopy) {
  if (!isObj(copy) || !isObj(copy.langs) || !isObj(regionCopy) || !isObj(regionCopy.langs)) return copy;
  const langs = {};
  for (const [lang, tree] of Object.entries(copy.langs)) {
    const extra = regionCopy.langs[lang];
    langs[lang] = isObj(extra) ? { ...tree, regions: extra } : tree;
  }
  return { ...copy, langs };
}

/** Every file, fetched with `fetchJson(url)` that returns parsed JSON or null. Missing files are null. */
// An observation region folder (SA-D79) holds only these files: the page asks for nothing else,
// so the browser logs no 404 for files that region never has.
export const OBSERVATION_FILES = Object.freeze(['region', 'copy']);
export const OBSERVATION_FIELD_IDS = Object.freeze(['a']);

export async function loadAll(fetchJson, base = 'data/') {
  // copy.json is built from config/copy_*.yaml, never example data: always from data/.
  const region = await fetchJson(`${base}${DATA_FILES.region}`);
  const observation = isObj(region) && region.mode === 'observation';
  const wanted = Object.entries(DATA_FILES).filter(([k]) => k !== 'region' && (!observation || OBSERVATION_FILES.includes(k)));
  const entries = await Promise.all(
    wanted.map(async ([k, f]) => [k, await fetchJson(`${k === 'copy' ? 'data/' : base}${f}`)]),
  );
  const data = { region, ...Object.fromEntries(entries) };
  data.copy = mergeRegionCopy(data.copy, await fetchJson(REGION_COPY));
  const fields = {};
  await Promise.all(
    (observation ? OBSERVATION_FIELD_IDS : FIELD_IDS).map(async (id) => {
      const f = await fetchJson(`${base}field_${id}.json`);
      if (isObj(f)) fields[id] = f;
    }),
  );
  return normalizeData({ ...data, fields, base });
}

/** Shape fixes that keep the page tolerant: scoreboard wrapper, missing pieces. Pure. */
export function normalizeData(raw) {
  const d = { ...raw };
  if (isObj(d.scoreboard) && isObj(d.scoreboard.scoreboard)) d.scoreboard = d.scoreboard.scoreboard;
  for (const k of ['region', 'trials', 'scoreboard', 'ablation', 'ledger', 'manifest']) if (!isObj(d[k])) d[k] = null;
  if (!isObj(d.fields)) d.fields = {};
  d.records = recordsOf(d.ledger);
  d.fieldIds = FIELD_IDS.filter((id) => isObj(d.fields[id]));
  d.isRehearsal = isRehearsalData(d);
  // An observation region (SA-D79) shows its own banner and never a verdict.
  d.isObservation = isObservationData(d);
  // A rehearsal folder shows its own banner (real NASA data, fictitious trials), not the example one.
  d.isExample = !d.isRehearsal && !d.isObservation && isExampleData(d);
  d.dataFolderMissing = dataFolderMissing(d);
  return d;
}

/**
 * True when a ?data= folder was asked for and none of its files loaded: the published site
 * holds only site/, not the fictitious example of the repository, so the page says so instead
 * of showing an empty example. Pure.
 */
export function dataFolderMissing(d) {
  if (!d.base || d.base === 'data/') return false;
  return !['region', 'trials', 'scoreboard', 'ablation', 'ledger', 'manifest'].some((k) => isObj(d[k]))
    && !(d.fieldIds?.length > 0);
}

const FICTITIOUS = /example, fictitious|fictitious/i;

/** True when any loaded file declares itself an example. Then a banner says so on screen. */
export function isExampleData(d) {
  if (d.base && d.base !== 'data/') return true;
  const flags = [
    d.ledger?.label,
    d.trials?.rights_note,
    d.scoreboard?._note,
    ...Object.values(d.fields ?? {}).map((f) => f?.criterion),
  ];
  // The price scenarios are always fictitious (7.16) and carry their own tag. They alone do not
  // make the data an example.
  return flags.some((x) => typeof x === 'string' && FICTITIOUS.test(x));
}

/** True when region.json says "mode": "observation" (pipeline/regions/observation.py, SA-D79). Pure. */
export function isObservationData(d) {
  return isObj(d.region) && d.region.mode === 'observation';
}

/** True when any loaded file is stamped "run_mode": "rehearsal" (pipeline/common/rehearsal.py). Pure. */
export function isRehearsalData(d) {
  const docs = [d.region, d.trials, d.scoreboard, d.ablation, d.ledger, d.manifest, ...Object.values(d.fields ?? {})];
  return docs.some((x) => isObj(x) && x.run_mode === 'rehearsal');
}

/** Example numbers of the base scenario (region.json price_scenarios, "example, fictitious"). */
export function exampleNumbers(region) {
  const list = Array.isArray(region?.price_scenarios) ? region.price_scenarios : [];
  const base = list.find((s) => s?.id === 'example_mid') ?? list[1] ?? list[0];
  const out = {};
  for (const f of NUMBER_FIELDS) {
    const v = numValue(base?.values?.[f]);
    if (v !== null) out[f] = v;
  }
  return out;
}

/** k of the price scenarios (14.3): corn price lower and higher by k. From region.json. */
export function scenarioK(region) {
  const list = Array.isArray(region?.price_scenarios) ? region.price_scenarios : [];
  const low = numValue(list.find((s) => s?.id === 'example_low')?.values?.p_corn);
  const mid = numValue(list.find((s) => s?.id === 'example_mid')?.values?.p_corn);
  if (low === null || mid === null || mid <= 0) return null;
  const k = 1 - low / mid;
  return k > 0 && k < 1 ? Math.round(k * 1000) / 1000 : null;
}

// State of M5 when the field has no Zarc soil class (soil.zarc_ad_status "unknown", no soil report):
// no Tabua row can apply, so no Zarc window is matched to this soil. Said aloud, never shown as a blank.
export const SOIL_CLASS_UNKNOWN = 'soil_class_unknown';

// Soil class selector (SA-D64): "unknown" or the class AD1 to AD6 printed on the user's soil report.
export const SOIL_CLASS_OPTIONS = D.SOIL_CLASSES;

/** The class the page uses: the one the user declares, else the field's own report class, else unknown. */
export function soilClassOf(field, soilClass = 'unknown') {
  if (SOIL_CLASS_OPTIONS.includes(soilClass) && soilClass !== 'unknown') return soilClass;
  const own = field?.soil?.zarc_ad_class;
  return SOIL_CLASS_OPTIONS.includes(own) ? own : 'unknown';
}

/**
 * Window reading of `sid` (S1 corn, S2 corn with grass) for the class in use, recomputed in the
 * browser from field.window_basis (decision.mjs windowReadingFor). A field without window_basis
 * (older schema) keeps the precomputed window_reading, for S1 only.
 */
export function windowReadingOf(field, soilClass = 'unknown', sid = 'S1') {
  if (isObj(field?.window_basis)) {
    const r = D.windowReadingFor(field.window_basis, sid, soilClassOf(field, soilClass));
    if (r) return { ...r, ledger: typeof field.window_basis.ledger === 'string' ? field.window_basis.ledger : null };
  }
  return sid === 'S1' && isObj(field?.window_reading) ? field.window_reading : null;
}

/** True when M5 says the Zarc window cannot be matched to this soil, for `sid` or any strategy. */
export function zarcSoilUnknown(field, sid = null) {
  const ids = sid ? [sid] : STRATEGIES;
  return ids.some((id) => field?.risk?.zarc_class?.[id]?.state === SOIL_CLASS_UNKNOWN
    || (Array.isArray(field?.calendar?.[id]) && field.calendar[id].some((x) => x?.state === SOIL_CLASS_UNKNOWN)));
}

/**
 * zarcWindow for decision.mjs. field.zarc_window when M5 writes it, else from the calendar.
 * A class declared on the page (SA-D64) takes the windows of that class from window_basis.
 */
export function zarcWindowOf(field, soilClass = 'unknown') {
  if (isObj(field?.zarc_window)) return field.zarc_window;
  const declared = SOIL_CLASS_OPTIONS.includes(soilClass) && soilClass !== 'unknown' ? soilClass : null;
  if (declared && isObj(field?.window_basis)) {
    const one = (sid) => {
      const w = field.window_basis?.[sid]?.by_class?.[declared];
      return S.parseIso(w?.start) && S.parseIso(w?.end) ? { start: w.start, end: w.end } : undefined;
    };
    return { corn: one('S1'), grass: one('S2') };
  }
  const cal = isObj(field?.calendar) ? field.calendar : {};
  const envelope = (segs) => {
    const ok = segs.filter((s) => S.parseIso(s?.start) && S.parseIso(s?.end));
    if (!ok.length) return undefined;
    const start = ok.map((s) => s.start).sort()[0];
    const end = ok.map((s) => s.end).sort().at(-1);
    return { start, end };
  };
  const pick = (list, crop) => (Array.isArray(list) ? list : []).filter((s) => s?.crop === crop && s?.state === 'zarc_window');
  return { corn: envelope(pick(cal.S1, 'corn')), grass: envelope(pick(cal.S2, 'corn_grass')) };
}

// ================================================================ UI state

/** First UI state: Field A, the example numbers, English unless the viewer chose otherwise. */
export function initialUi(data, lang = DEFAULT_LANG) {
  const L = normalizeLang(lang);
  const t = makeT(data.copy, L);
  const values = exampleNumbers(data.region);
  const raw = {};
  for (const f of NUMBER_FIELDS) raw[f] = formatInput(values[f], t);
  for (const f of NITROGEN_FIELDS) raw[f] = '';
  return {
    lang: L,
    field: data.fieldIds?.[0] ?? null,
    raw,
    sowingDate: '',
    limits: Object.fromEntries(Object.keys(LIMITS).map((k) => [k, ''])),
    priority: DEFAULT_PRIORITY,
    hlsOn: true,
    soilClass: 'unknown',
    mapImage: null,
  };
}

/** Numbers typed, parsed in the language of the page. */
export function currentNumbers(ui) {
  const out = {};
  for (const f of NUMBER_FIELDS) {
    const v = parseNumber(ui.raw?.[f], ui.lang);
    if (v !== undefined) out[f] = v;
  }
  return out;
}

/** True when every number still equals the example (the "example, fictitious" tag stays). */
export function isExampleState(data, ui) {
  const ex = exampleNumbers(data.region);
  const cur = currentNumbers(ui);
  return NUMBER_FIELDS.every((f) => ex[f] === cur[f]);
}

/** Switch language and keep every valid number, written with the new decimal mark. */
export function switchLang(ui, lang, copy) {
  const L = normalizeLang(lang);
  const t = makeT(copy, L);
  const raw = { ...ui.raw };
  for (const f of [...NUMBER_FIELDS, ...NITROGEN_FIELDS]) {
    const v = parseNumber(ui.raw?.[f], ui.lang);
    if (typeof v === 'number' && Number.isFinite(v)) raw[f] = formatInput(v, t);
  }
  return { ...ui, lang: L, raw };
}

/** Language remembered by this viewer, only when storage works. */
export function storedLang(storage) {
  try {
    const v = storage?.getItem(STORAGE_KEY);
    return LANGS.includes(v) ? v : null;
  } catch {
    return null;
  }
}

export function storeLang(storage, lang) {
  try {
    storage?.setItem(STORAGE_KEY, normalizeLang(lang));
    return true;
  } catch {
    return false;
  }
}

// ================================================================ decision inputs

/** Constraints of decision.mjs from the limits the farmer answered. */
export function constraintsFrom(region, limits) {
  const fromRegion = new Map((Array.isArray(region?.constraints) ? region.constraints : []).map((c) => [c?.id, c]));
  const out = [];
  for (const [id, spec] of Object.entries(LIMITS)) {
    if (!spec.trigger || limits?.[id] !== spec.trigger) continue;
    const c = fromRegion.get(id);
    const removes = Array.isArray(c?.removes) && c.removes.length ? c.removes : spec.removes;
    if (removes.length) out.push({ id, removes, why: typeof c?.why === 'string' ? c.why : null });
  }
  return out;
}

/** Input of decision.mjs decideField for one field. */
export function decisionInput(data, ui, field, { hlsOn = ui.hlsOn, numbers = currentNumbers(ui) } = {}) {
  return {
    numbers,
    trials: isObj(data.trials?.options) ? data.trials.options : {},
    constraints: constraintsFrom(data.region, ui.limits),
    hardRules: Array.isArray(data.region?.rules?.hard) ? data.region.rules.hard : [],
    zarcWindow: zarcWindowOf(field, ui.soilClass),
    windowReading: windowReadingOf(field, ui.soilClass, 'S1'),
    sowingDate: ui.sowingDate || null,
    zarcConstraint: ui.limits?.zarc_insurance_credit === 'yes',
    hlsOn: hlsOn !== false,
    priority: ui.priority,
  };
}

/** decision.mjs result for the active field. */
export function decide(data, ui, opts = {}) {
  const field = data.fields?.[ui.field] ?? null;
  return D.decideField(decisionInput(data, ui, field, opts));
}

/**
 * NASA switch (7.8, 8.7): states of every loaded field and option with HLS on and off, on the
 * current numbers. Dependence, not accuracy.
 */
export function switchCount(data, ui) {
  const ids = data.fieldIds?.length ? data.fieldIds : [null];
  let X = 0;
  let Y = 0;
  for (const id of ids) {
    const field = id ? data.fields[id] : null;
    const on = D.decideField(decisionInput(data, ui, field, { hlsOn: true }));
    const off = D.decideField(decisionInput(data, ui, field, { hlsOn: false }));
    for (const opt of D.OPTIONS) {
      Y += 1;
      if (on[opt].state !== off[opt].state) X += 1;
    }
  }
  return { X, Y };
}

/**
 * The protocol rule of the switch (SA-D66), from region.json nasa_switch: the switch goes on the
 * main page only when it changes at least `min` states. Null when region.json lacks it.
 */
export function switchRule(region) {
  const r = region?.nasa_switch;
  const min = numValue(r?.main_page_if_states_changed_min);
  if (!Number.isSafeInteger(min) || min < 0) return null;
  return { min, ledger: numLedger(r.main_page_if_states_changed_min), otherwise: r.otherwise === 'how_we_know' ? r.otherwise : null };
}

const PLACEMENT = new WeakMap();

/**
 * Where the switch goes (SA-D66): counted with switchCount on the fields shown and the page's
 * starting numbers (initialUi), so the section never moves while someone types. The same rule
 * holds for the fictitious example. Without the rule, the main page leaves the switch out.
 * Returns { onMain, X, Y, min, ledger }. Pure (cached per data object).
 */
export function switchPlacement(data) {
  if (isObj(data) && PLACEMENT.has(data)) return PLACEMENT.get(data);
  const sw = switchCount(data, initialUi(data, DEFAULT_LANG));
  const rule = switchRule(data?.region);
  const out = { onMain: rule !== null && sw.X >= rule.min, X: sw.X, Y: sw.Y, min: rule?.min ?? null, ledger: rule?.ledger ?? null };
  if (isObj(data)) PLACEMENT.set(data, out);
  return out;
}

// ================================================================ sentences

function optionName(t, opt) {
  return t(`page.strategies.${opt}.name`);
}

function reasonText(t, reasons) {
  const parts = (Array.isArray(reasons) ? reasons : []).map((r) => {
    const key = `page.limits.reasons.${r?.id}`;
    if (r?.id && t.has(key)) return t(key);
    return typeof r?.why === 'string' && r.why ? r.why : t.unknown();
  });
  return parts.length ? parts.join(', ') : t.unknown();
}

/** Copy key of a result code of decision.mjs (answers.*). */
export function answerKey(code) {
  return `answers.${code}`;
}

/** Sentence of one option result (S2 or S3), with its state words first (7.15). */
export function optionSentence(t, r, trials) {
  const vars = {
    Option: optionName(t, r.option),
    G: formatNumber(D.shownG(r.G), t),
    N: Number.isFinite(r.N) ? formatNumber(r.N, t) : null,
    n: Number.isFinite(r.n) ? formatNumber(r.n, t) : null,
    field: r.field ? t(`fields.${r.field}`) : null,
    reason: r.state === 'removed' ? reasonText(t, r.reasons) : null,
  };
  if (r.state === 'removed') return t('answers.removed', vars);
  if (r.code === 'few_trials' && vars.n === null) vars.n = formatNumber(D.trialCount(trials?.[r.option]) ?? 0, t);
  return t(answerKey(r.code), vars);
}

/** Main sentence and its second clause (7.15). */
export function answerView(t, result, trials) {
  const a = result.answer;
  const main = a.main ? result[a.main] : null;
  let text;
  let other = null;
  const reasons = [];
  if (!main) {
    // Both options removed: keep with both reasons, or rule 6 of 13.7 (window_outside and kin).
    text = t(answerKey(a.code));
    for (const opt of ['S3', 'S2']) if (result[opt]?.state === 'removed') reasons.push(optionSentence(t, result[opt], trials));
  } else {
    text = optionSentence(t, { ...main, state: a.state, code: a.code, field: a.field }, trials);
    const o = result[a.other];
    other = o.state === 'removed'
      ? optionSentence(t, o, trials)
      : t('answers.other_option', { Option: optionName(t, o.option), state: t(`states.${o.state}`) });
  }
  return { state: a.state, stateWord: t(`states.${a.state}`), icon: S.stateIcon(a.state), text, other, reasons };
}

/** Caption under the answer (7.15): counts from 3 sites, or the real number of S2 sites. */
export function captionFor(t, mainOpt, trials) {
  if (mainOpt !== 'S2') return t('page.answer.caption');
  const sites = trials?.S2?.n_sites;
  if (sites === 1) return t('page.answer.caption_one_site');
  if (Number.isSafeInteger(sites) && sites !== 3) return t('page.answer.caption_sites', { sites: formatNumber(sites, t) });
  return t('page.answer.caption');
}

/** Window warning under S1 and S2 (7.2, 12.3). Hidden with the HLS switch off. Never "inside". */
export function windowWarning(t, field, hlsOn, soilClass = 'unknown', sid = 'S1') {
  if (!hlsOn) return null;
  if (isObj(field?.window_basis)) return basisWarning(t, windowReadingOf(field, soilClass, sid));
  // Without a Zarc soil class there is no corn window to compare the satellite with.
  if (zarcSoilUnknown(field, 'S1')) {
    return { text: t('window.soil_class_unknown'), state: SOIL_CLASS_UNKNOWN, ledger: ledgerOf(field?.soil) };
  }
  const wr = field?.window_reading;
  const vars = { k: Number.isFinite(wr?.k) ? formatNumber(wr.k, t) : null, n: Number.isFinite(wr?.n) ? formatNumber(wr.n, t) : null };
  const ledger = typeof wr?.ledger === 'string' ? wr.ledger : null;
  const k = Number.isFinite(wr?.k) ? wr.k : null;
  if (wr?.state === 'outside') return { text: t('flags.window_warning', vars), state: 'outside', ledger, k };
  if (wr?.state === 'at_the_edge') return { text: t('window.at_the_edge', vars), state: 'at_the_edge', ledger, k };
  if (wr?.state === 'to_confirm') return { text: t('window.states.to_confirm'), state: 'to_confirm', ledger };
  return { text: t('window.states.no_data'), state: 'no_data', ledger };
}

/** SA-D64 warning texts: declared class, "for any soil class" without a report, or no window. */
function basisWarning(t, wr) {
  const ledger = typeof wr?.ledger === 'string' ? wr.ledger : null;
  if (!wr || wr.state === 'no_window') return { text: t('window.no_window'), state: 'no_window', ledger };
  const vars = { k: formatNumber(wr.k, t), n: formatNumber(wr.n, t) };
  const any = wr.basis === D.WINDOW_BASIS.any;
  const k = Number.isFinite(wr.k) ? wr.k : null;
  if (wr.state === 'outside') return { text: t(any ? 'window.outside_any_class' : 'flags.window_warning', vars), state: 'outside', ledger, anyClass: any, k };
  return { text: t(any ? 'window.at_the_edge_any_class' : 'window.at_the_edge', vars), state: 'at_the_edge', ledger, anyClass: any, k };
}

/**
 * The satellite sentence under the window warning of S1 and S2 (SA-D72, SA-D90): the Zarc risk of
 * the ten-day period where the first green cycle ended, for the class in use, as one sentence.
 * "Ended before the window closed" is on the NASA line below the answer (SA-D78), not here.
 * Satellite estimates, so hidden with the HLS switch off. Never a sowing date, never "inside".
 * Returns [] or [{ text, ledger }]: one group keeps its own sentence, several share one.
 */
export function exitLines(t, field, hlsOn, soilClass = 'unknown', sid = 'S1') {
  const basis = field?.window_basis;
  if (!hlsOn || !isObj(basis)) return [];
  const cls = soilClassOf(field, soilClass);
  const crop = t(`page.rows.L2.crops.${sid === 'S1' ? 'corn' : 'grass'}`);
  const rx = D.riskAtExitFor(basis, sid, cls);
  if (rx?.state !== 'ok' || !(rx.n > 0) || !rx.groups.length) return [];
  const ledger = ledgerOf(basis.risk_at_exit);
  const n = formatNumber(rx.n, t);
  const any = cls === 'unknown';
  if (rx.groups.length > 1) {
    const parts = rx.groups.map((g) => {
      const k = formatNumber(g.k, t);
      if (!any) {
        return g.risk === null
          ? t('window.risk_at_exit.part_outside', { k, n })
          : t('window.risk_at_exit.part_one', { r: formatNumber(g.risk, t), k, n });
      }
      if (g.risk_max === null) return t('window.risk_at_exit.part_outside_any_class', { k, n });
      return g.risk_min === g.risk_max
        ? t('window.risk_at_exit.part_one', { r: formatNumber(g.risk_min, t), k, n })
        : t('window.risk_at_exit.part_range', { rmin: formatNumber(g.risk_min, t), rmax: formatNumber(g.risk_max, t), k, n });
    });
    const joined = t('window.risk_at_exit.parts_last', { a: parts.slice(0, -1).join(', '), b: parts.at(-1) });
    return [{ text: t(any ? 'window.risk_at_exit.groups_any_class' : 'window.risk_at_exit.groups', { crop, parts: joined }), ledger }];
  }
  return rx.groups.map((g) => {
    const k = formatNumber(g.k, t);
    let text;
    if (!any) {
      text = g.risk === null
        ? t('window.risk_at_exit.outside', { crop, k, n })
        : t('window.risk_at_exit.line', { r: formatNumber(g.risk, t), crop, k, n });
    } else if (g.risk_max === null) {
      text = t('window.risk_at_exit.outside_any_class', { crop, k, n });
    } else if (g.risk_min === g.risk_max) {
      text = t('window.risk_at_exit.line_any_class_one', { r: formatNumber(g.risk_min, t), crop, k, n });
    } else {
      text = t('window.risk_at_exit.line_any_class', { rmin: formatNumber(g.risk_min, t), rmax: formatNumber(g.risk_max, t), crop, k, n });
    }
    // some_outside stays in the data only: on the page it read as an outside verdict (Fable review).
    return { text, ledger };
  });
}

/** The window warning a column shows (SA-D90): the "persisted beyond" sentence only when k > 0. */
export function shownWarning(w) {
  if (!w) return null;
  if ((w.state === 'at_the_edge' || w.state === 'outside') && w.k === 0) return null;
  return w;
}

/**
 * The satellite part of the S1 and S2 column heads (SA-D90): { warning, exitLines, same }. When the
 * satellite numbers of S2 equal those of S1 (the same warning state and counts, the same risk
 * groups), S2 says "Same satellite reading as Keep corn." with the ledger of the S1 sentence.
 */
export function satelliteHeads(t, field, ui, wwarnBy) {
  const head = (sid) => {
    const warning = shownWarning(wwarnBy[sid] ?? null);
    return { warning, exitLines: wwarnBy[sid] ? exitLines(t, field, ui.hlsOn, ui.soilClass, sid) : [], same: false };
  };
  const s1 = head('S1');
  const s2 = head('S2');
  // The numbers each column shows: the warning (state, k, n, basis) and the risk groups at exit.
  const sig = (sid, h) => {
    const w = h.warning;
    const wr = w ? windowReadingOf(field, ui.soilClass, sid) : null;
    const basis = field?.window_basis;
    const rx = h.exitLines.length && isObj(basis) ? D.riskAtExitFor(basis, sid, soilClassOf(field, ui.soilClass)) : null;
    return JSON.stringify({
      w: w ? [w.state, w.k ?? null, w.anyClass ?? null, wr?.n ?? null, wr?.basis ?? null] : null,
      rx: rx ? [rx.state, rx.n, rx.groups] : null,
    });
  };
  if ((s1.warning || s1.exitLines.length) && sig('S1', s1) === sig('S2', s2)) {
    const ledger = s1.exitLines[0]?.ledger ?? s1.warning?.ledger ?? null;
    return { S1: s1, S2: { warning: null, exitLines: [{ text: t('window.same_as_S1', { name: t('page.strategies.S1.name') }), ledger }], same: true } };
  }
  return { S1: s1, S2: s2 };
}

/**
 * The NASA line right below the answer (SA-D78): two numbers of the selected field, each linked
 * to its ledger entry. HLS: seasons whose first green cycle ended before the corn Zarc window
 * closed, for the class in use (without a soil report the earliest end across classes and cycle
 * groups, SA-D84), hidden with the HLS switch off. Only here: the columns do not repeat it (SA-D90).
 * POWER: nights below the frost threshold over the corn occupation dates, k of n seasons.
 * A clause whose number is missing is left out. Returns [{ text, ledger }], possibly empty.
 */
export function nasaLine(t, field, hlsOn = true, soilClass = 'unknown') {
  const out = [];
  const basis = field?.window_basis;
  if (hlsOn && isObj(basis)) {
    const cls = soilClassOf(field, soilClass);
    const eb = D.endedBeforeFor(basis, 'S1', cls);
    if (eb?.state === 'ok' && Number.isFinite(eb.k) && Number.isFinite(eb.n) && eb.n > 0) {
      out.push({
        text: t('page.answer.nasa_line.hls', { k: formatNumber(eb.k, t), n: formatNumber(eb.n, t) }),
        ledger: ledgerOf(basis.ended_before),
      });
    }
  }
  const climate = field?.risk?.climate;
  const c = climate?.S1;
  const frost = c?.frost;
  if (isObj(c) && Number.isFinite(c.n) && c.n > 0 && Number.isFinite(frost?.k) && Number.isFinite(frost?.below_c)) {
    out.push({
      text: t('page.answer.nasa_line.power', { t: formatNumber(frost.below_c, t, 0), k: formatNumber(frost.k, t), n: formatNumber(c.n, t) }),
      ledger: typeof climate?.ledger === 'string' && climate.ledger ? climate.ledger : null,
    });
  }
  return out;
}

/** The class selector of the window warning (SA-D64). Shown only when the field ships window_basis. */
export function soilClassView(t, field, ui) {
  if (!isObj(field?.window_basis)) return null;
  const cls = soilClassOf(field, ui.soilClass);
  return {
    label: t('window.class_selector.label'),
    hint: t('window.class_selector.hint'),
    value: cls,
    options: SOIL_CLASS_OPTIONS.map((c) => ({ value: c, label: c === 'unknown' ? t('window.class_selector.unknown') : c, active: c === cls })),
    note: cls === 'unknown' ? t('window.any_class_note') : null,
  };
}

/** S2 trials by establishment method (protocol 11): one line per method, or null without them. */
export function s2MethodLines(t, ev, r) {
  const list = D.methodCounts(ev, r?.step ?? null);
  if (!list) return null;
  const sitesOf = (m) => (Array.isArray(m?.sites) ? m.sites.filter((x) => typeof x === 'string').join(', ') : '');
  const raw = Array.isArray(ev?.by_method) ? ev.by_method : [];
  return list.map((m) => {
    const src = raw.find((x) => x?.method === m.method);
    const key = `s2_methods.names.${m.method}`;
    const vars = { method: t.has(key) ? t(key) : m.method, sites: sitesOf(src), n: formatNumber(m.n, t),
      N: Number.isFinite(m.N) ? formatNumber(m.N, t) : null };
    return line(t(Number.isFinite(m.N) ? 's2_methods.line_counted' : 's2_methods.line', vars), { ledger: ledgerOf(src) ?? ev?.ledger ?? null, kind: 'small' });
  });
}

function ledgerOf(x) {
  return isObj(x) && typeof x.ledger === 'string' && x.ledger ? x.ledger : null;
}

function fieldLetter(id) {
  return String(id ?? '').replace(/^field_/, '').toUpperCase();
}

/**
 * Which field first (13.7 rule 5, 8.7), computed here by decision.mjs fieldOrder, never
 * precomputed. Inputs: the green_end list of every loaded field, the state of `option` on each
 * field (same decision input as the page), region.json noise_floor (N1, days) and region.json
 * choice_seasons (the seasons used to choose the fields, left out). Without a noise floor, or
 * without an option to compare, fieldOrder says no field stands out.
 */
export function fieldOrderOf(data, ui, option) {
  const greenEnd = {};
  const states = {};
  if (D.OPTIONS.includes(option)) {
    for (const id of data.fieldIds ?? []) {
      const field = data.fields[id];
      if (Array.isArray(field?.green_end)) greenEnd[id] = field.green_end;
      states[id] = D.decideField(decisionInput(data, ui, field))[option].state;
    }
  }
  const seasons = Array.isArray(data.region?.choice_seasons) ? data.region.choice_seasons.filter((s) => typeof s === 'string') : [];
  return D.fieldOrder(greenEnd, numValue(data.region?.noise_floor), states, seasons);
}

/**
 * "Which field first" line (7.15, 13.7 item 5): { text, ledger }, or null with the HLS switch
 * off. A declared sowing date removes the order too, and says why. The option compared is the
 * one of the main sentence on the active field (`result`, decision.mjs decideField).
 */
export function fieldOrderLine(t, data, ui, result = null) {
  if ((data.fieldIds?.length ?? 0) < 2) return { text: t('page.field_order.one_field'), ledger: null };
  if (ui.sowingDate) return { text: t('flags.date_in_charge'), ledger: null };
  if (!ui.hlsOn) return null;
  const fo = fieldOrderOf(data, ui, (result ?? decide(data, ui)).answer.main);
  const ledger = browserLedger('field_order');
  if (fo.first) {
    return {
      text: t('page.field_order.first', {
        X: fieldLetter(fo.first),
        Y: fieldLetter(fo.other[0]),
        N: Number.isFinite(fo.n) ? formatNumber(fo.n, t) : null,
        M: Number.isFinite(fo.m) ? formatNumber(fo.m, t) : null,
      }),
      ledger,
    };
  }
  return { text: t('page.field_order.none'), ledger };
}

/** Label of the crop-sequence model check. not_completed when scoreboard.json is absent. */
export function labelId(data) {
  const id = data.scoreboard?.label_id;
  return typeof id === 'string' && isObj(lookup(copyTree(data.copy, DEFAULT_LANG), `labels.${id}`)) ? id : 'not_completed';
}

export function l5Visible(data, field) {
  return labelId(data) === L5_LABEL && field?.next_soy?.on_main_page === true;
}

/** Row title of the erosivity line: its template without the {class} slot. */
function erosivityTitle(t) {
  return String(t('page.rows.erosivity.line')).replace('{class}', '').replace(/[:\s]+$/, '');
}

function seal(t, id) {
  return { id, name: t(`seals.names.${id}`), icon: S.sealIcon(id) };
}

function line(text, extra = {}) {
  return text === null || text === undefined || text === '' ? null : { text, ...extra };
}

function compact(list) {
  return list.filter(Boolean);
}

/** "a‑b%" with U+2011, a hyphen that does not break the line. Never an en dash. */
function riskRange(t, a, b) {
  return a === b ? `${formatNumber(a, t)}%` : `${formatNumber(a, t)}\u2011${formatNumber(b, t)}%`;
}

/**
 * L2 Zarc risk (SA-D72.6). A class declared on the page that is not the field's own report class
 * reads that class (latest-ending cycle group). No class: the range across every class and cycle
 * group, "for any soil class", instead of "unknown".
 */
function zarcClassText(t, zc, cls = 'unknown', ownCls = 'unknown') {
  if (cls !== 'unknown' && cls !== ownCls && isObj(zc?.by_class)) {
    const r = zc.by_class[cls];
    if (!isObj(r)) return t('page.rows.L2.class_not_zoned', { class: cls });
    if (Number.isFinite(r.risk_min) && Number.isFinite(r.risk_max)) return riskRange(t, r.risk_min, r.risk_max);
  }
  const any = zc?.any_class;
  if (cls === 'unknown' && Number.isFinite(any?.risk_min) && Number.isFinite(any?.risk_max)) {
    return any.risk_min === any.risk_max
      ? t('page.rows.L2.any_class_one', { a: formatNumber(any.risk_min, t) })
      : t('page.rows.L2.any_class_range', { a: formatNumber(any.risk_min, t), b: formatNumber(any.risk_max, t) });
  }
  if (zc?.state === SOIL_CLASS_UNKNOWN) return t('page.rows.L2.soil_class_unknown');
  if (!isObj(zc) || zc.state === 'not_found') return t.unknown();
  const a = zc.risk_min;
  const b = zc.risk_max;
  if (!Number.isFinite(a) || !Number.isFinite(b)) return t.unknown();
  return riskRange(t, a, b);
}

/** 'MM-DD' as "22 Mar", with the month names of the copy. */
export function formatMonthDay(md, t) {
  const m = typeof md === 'string' ? /^(\d{2})-(\d{2})$/.exec(md) : null;
  if (!m) return t.unknown();
  const months = t.list('page.year_strip.months');
  return `${Number(m[2])} ${months[Number(m[1]) - 1] ?? m[1]}`;
}

/**
 * L2 climate lines of one column (SA-D72.5, protocol.climate): nights below the frost threshold
 * (k of n seasons), rain total (median and 10th percentile) and the three driest seasons by name,
 * over the occupation dates of the crop that follows the soybean. Counts of the past, never a forecast.
 */
export function climateLines(t, climate, sid, crop) {
  const c = climate?.[sid];
  const ledger = typeof climate?.ledger === 'string' ? climate.ledger : null;
  if (!isObj(c) || !Number.isFinite(c.n) || c.n <= 0) return [line(t.unknown(), { ledger }), line(t('page.rows.L2.climate_note', { crop }), { kind: 'small' })];
  const span = { start: formatMonthDay(c.start, t), end: formatMonthDay(c.end, t) };
  const mm = (x) => formatNumber(Number.isFinite(x) ? Math.round(x) : x, t, 0);
  const driest = (Array.isArray(c.rain?.driest) ? c.rain.driest : [])
    .filter((d) => typeof d?.season === 'string' && Number.isFinite(d?.mm))
    .map((d) => `${d.season} (${mm(d.mm)} mm)`);
  return compact([
    line(t('page.rows.L2.climate_frost', { ...span, t: formatNumber(c.frost?.below_c, t, 0), k: formatNumber(c.frost?.k, t), n: formatNumber(c.n, t) }), { ledger }),
    line(t('page.rows.L2.climate_rain', { ...span, median: mm(c.rain?.median_mm), p10: mm(c.rain?.p10_mm) }), { ledger }),
    driest.length ? line(t('page.rows.L2.climate_driest', { seasons: driest.join(', ') })) : null,
    line(t('page.rows.L2.climate_note', { crop }), { kind: 'small' }),
  ]);
}

/** L1 line of S1 (SA-D72.4): ten-day periods with no green seen, median over the ribbon seasons. */
export function noGreenLine(t, field) {
  const ng = field?.ribbon_counts?.no_green;
  if (!isObj(ng) || !Number.isFinite(ng.median) || !Number.isFinite(ng.seasons)) return null;
  const N = formatNumber(ng.median, t, Number.isInteger(ng.median) ? 0 : 1);
  return line(t('page.rows.L1.no_green', { N, S: formatNumber(ng.seasons, t) }), { ledger: typeof ng.ledger === 'string' ? ng.ledger : null });
}

/** Template vars from an optional summary object: Num values formatted, strings kept. */
function summaryVars(t, summary, digits = {}) {
  const out = {};
  for (const [k, v] of Object.entries(isObj(summary) ? summary : {})) {
    const n = numValue(v);
    if (n !== null) out[k] = formatNumber(n, t, digits[k] ?? (Number.isInteger(n) ? 0 : 1));
    else if (typeof v === 'string' && v) out[k] = v;
  }
  return out;
}

function dataText(text) {
  // Free text written by the pipeline (English only today). Marked with its language.
  return typeof text === 'string' && text ? { text, lang: 'en', kind: 'data' } : null;
}

// ================================================================ main page view

/** Everything index.html shows, from the data and the UI state. Pure. */
export function buildIndexView(data, ui, { used = null } = {}) {
  if (data.isObservation) return buildObservationView(data, ui, { used });
  const t = makeT(data.copy, ui.lang, { used });
  const field = data.fields?.[ui.field] ?? null;
  const trials = isObj(data.trials?.options) ? data.trials.options : {};
  const result = decide(data, ui);
  const answer = answerView(t, result, trials);
  const example = isExampleState(data, ui);
  const lid = labelId(data);
  const showL5 = l5Visible(data, field);
  const highlight = result.highlight;

  // Header and fields.
  const fields = (data.fieldIds ?? []).map((id) => ({
    id,
    name: t('page.header.field_name', { X: fieldLetter(id) }),
    active: id === ui.field,
    polygon: data.fields[id]?.polygon_px ?? [],
  }));
  const map = fields.length
    ? S.fieldMapSvg({ fields, image: ui.mapImage, label: t('page.header.map_alt') })
    : '';

  // Answer block.
  const flagKeys = new Set();
  for (const r of [result.S2, result.S3]) for (const f of r.flags ?? []) flagKeys.add(f);
  const notes = [];
  if (flagKeys.has('date_in_charge')) notes.push(t('flags.date_in_charge'));
  if (flagKeys.has('check_window_with_bank')) notes.push(t('flags.check_window_with_bank'));
  if (flagKeys.has('date_invalid')) notes.push(t('flags.date_invalid'));
  if (flagKeys.has('zarc_window_unknown')) notes.push(t('flags.zarc_window_unknown'));
  const mainOpt = result.answer.main;

  // Columns.
  const wwarnBy = { S1: windowWarning(t, field, ui.hlsOn, ui.soilClass, 'S1'), S2: windowWarning(t, field, ui.hlsOn, ui.soilClass, 'S2') };
  const satBy = satelliteHeads(t, field, ui, wwarnBy);
  const columns = STRATEGIES.map((sid) => columnView(t, data, ui, field, result, sid, { sat: satBy[sid] ?? null, trials, showL5, example }));

  const rows = [
    { id: 'L1', title: t('page.rows.L1.title') },
    { id: 'L2', title: t('page.rows.L2.title') },
    { id: 'L3', title: t('page.rows.L3.title'), note: t('page.answer.transfer_warning') },
    { id: 'L4', title: t('page.rows.L4.title') },
    ...(showL5 ? [{ id: 'L5', title: t('page.rows.L5.title'), note: t('page.rows.L5.only_if_label') }] : []),
    ...(field?.erosivity ? [{ id: 'E', title: erosivityTitle(t) }] : []),
  ].map((r) => ({ ...r, seals: (ROW_SEALS[r.id] ?? []).map((s) => seal(t, s)), highlight: r.id === highlight }));

  // Soil, the same in the three columns.
  // estimated_class is the English name, estimated_class_detail holds the name in each language.
  const detailName = field?.soil?.estimated_class_detail?.[`name_${t.lang}`];
  const soilClass = [detailName, field?.soil?.estimated_class].find((x) => typeof x === 'string' && x) ?? t.unknown();
  const soil = {
    text: t('page.rows.soil.line', { class: soilClass }),
    ledger: typeof field?.soil?.ledger === 'string' ? field.soil.ledger : null,
    seal: seal(t, 'unknown'),
    note: t('page.rows.soil_note'),
  };
  const moneyRefs = (Array.isArray(data.trials?.money_refs) ? data.trials.money_refs : [])
    .map((m) => (typeof m?.text === 'string' ? { text: m.text, lang: 'en', ledger: m.ledger ?? null } : null))
    .filter(Boolean);

  const sw = switchCount(data, ui);
  const place = switchPlacement(data);
  const nitrogen = nitrogenView(t, ui);
  const order = fieldOrderLine(t, data, ui, result);

  return {
    lang: t.lang,
    htmlLang: t('meta.lang'),
    title: t('page.title'),
    switchTo: t('meta.switch_to'),
    switchAria: t('meta.switch_aria'),
    isExample: data.isExample,
    exampleWord: t('page.inputs.example'),
    // SA-D84: on real data the example numbers are the prices only, the field is real.
    answerExampleWord: data.isExample ? t('page.inputs.example') : t('page.answer.example_prices'),
    isRehearsal: data.isRehearsal,
    rehearsalBanner: data.isRehearsal ? t('page.rehearsal_banner') : null,
    observation: false,
    // SA-D84: the observation region next to the region line, only when its copy is loaded.
    regionLink: typeof lookup(copyTree(data.copy, t.lang), 'regions.main_link') === 'string'
      ? { text: t('regions.main_link'), href: OBSERVATION_HREF }
      : null,
    fields,
    hasFields: fields.length > 0,
    noFieldText: fields.length ? null : t('missing.no_data'),
    map,
    answer: {
      ...answer,
      caption: captionFor(t, mainOpt, trials),
      notes,
      fieldOrder: order ? t('page.answer.which_field_first', { line: order.text }) : null,
      fieldOrderLedger: order?.ledger ?? null,
      nasaLine: nasaLine(t, field, ui.hlsOn, ui.soilClass),
      modelCheck: t('page.answer.model_check', { label: t(`labels.${lid}.title`) }),
      labelBody: t(`labels.${lid}.body`),
      lateSoyGap: t('page.strategies.S3.late_soy_gap'),
    },
    links: { numbers: t('page.rows.L4.your_numbers'), limits: t('page.rows.L4.your_limits') },
    priority: PRIORITIES.map((p) => ({ id: p, label: t(`page.priority.${p}`), active: ui.priority === p })),
    columns,
    rows,
    rowCount: rows.length + 2,
    soil,
    lossYear: t('page.rows.L4.loss_year'),
    moneyRefs,
    yearStrip: yearStripView(t, field),
    ribbon: ribbonView(t, field, ui.soilClass),
    dataFolderMissing: data.dataFolderMissing ? t('missing.example_folder') : null,
    nasa: {
      onMain: place.onMain, // SA-D66: otherwise How we know shows the count
      on: ui.hlsOn,
      stateWord: t(ui.hlsOn ? 'page.nasa_switch.switch_on' : 'page.nasa_switch.switch_off'),
      short: t('page.nasa_switch.short', { X: formatNumber(sw.X, t), Y: formatNumber(sw.Y, t) }),
      changed: ui.hlsOn ? null : t('page.nasa_switch.changed', { X: formatNumber(sw.X, t), Y: formatNumber(sw.Y, t) }),
      contextOnly: sw.X === 0 ? t('page.nasa_switch.context_only') : null,
      dateInCharge: ui.sowingDate ? t('page.nasa_switch.date_in_charge') : null,
      onYourNumbers: example ? null : t('page.nasa_switch.on_your_numbers'),
      X: sw.X,
      Y: sw.Y,
      ledger: browserLedger('switch'),
    },
    inputs: inputsView(t, data, ui),
    soilClass: soilClassView(t, field, ui),
    example,
    scenarios: scenariosView(t, data, ui),
    limits: limitsView(t, ui),
    nitrogen,
    sheetHref: `${pageHref('sheet.html', data.base)}#${sheetHash(ui, result.answer.main)}`,
  };
}

// ================================================================ observation region (SA-D79)

/** Relief line of the observation page: class, median slope and height range (NASADEM). */
export function observationReliefLines(t, field) {
  const r = field?.relief;
  const slope = r?.slope_pct?.median;
  const h = r?.height_m;
  const cls = typeof r?.slope_class === 'string' && t.has(`regions.observation.relief_classes.${r.slope_class}`)
    ? t(`regions.observation.relief_classes.${r.slope_class}`) : null;
  const text = cls && Number.isFinite(slope) && Number.isFinite(h?.min) && Number.isFinite(h?.max)
    ? t('regions.observation.relief_line', { class: cls, slope: formatNumber(slope, t, 1), min: formatNumber(h.min, t), max: formatNumber(h.max, t) })
    : t('missing.no_data');
  return compact([line(text), line(t('regions.observation.relief_note'), { kind: 'small' })]);
}

/** Climate lines of the observation page: nights below 2 °C and rain over the fixed summer period. */
export function observationClimateLines(t, climate) {
  const c = climate;
  if (!isObj(c) || !Number.isFinite(c.n) || c.n <= 0) return [line(t('missing.no_data'))];
  const span = { start: formatMonthDay(c.start, t), end: formatMonthDay(c.end, t) };
  const mm = (x) => formatNumber(Number.isFinite(x) ? Math.round(x) : x, t, 0);
  const driest = (Array.isArray(c.rain?.driest) ? c.rain.driest : [])
    .filter((d) => typeof d?.season === 'string' && Number.isFinite(d?.mm))
    .map((d) => `${d.season} (${mm(d.mm)} mm)`);
  return compact([
    line(t('regions.observation.climate_frost', { ...span, t: formatNumber(c.frost?.below_c, t, 0), k: formatNumber(c.frost?.k, t), n: formatNumber(c.n, t) })),
    line(t('regions.observation.climate_rain', { ...span, median: mm(c.rain?.median_mm), p10: mm(c.rain?.p10_mm) })),
    driest.length ? line(t('regions.observation.climate_driest', { seasons: driest.join(', ') })) : null,
    line(t('regions.observation.climate_note', { ...span, first: c.first_season, last: c.last_season }), { kind: 'small' }),
  ]);
}

/** Source rows of the observation page, from region.json sources (titles and licenses in English). */
export function observationSources(t, region) {
  const list = Array.isArray(region?.sources) ? region.sources : [];
  const n = (x, d = 0) => formatNumber(x, t, d);
  return list.filter(isObj).map((s) => {
    const d = isObj(s.detail) ? s.detail : {};
    let detail = '';
    if (s.id === 'hls_vi_v2') detail = t('regions.observation.source_hls', { granules: n(d.granules), mb: n(d.mb, 1), first: d.seasons?.[0], last: d.seasons?.[1], tile: d.tile });
    else if (s.id === 'nasadem') detail = t('regions.observation.source_nasadem', { tiles: n(d.tiles), mb: n(d.mb, 1) });
    else if (s.id === 'power_daily') detail = t('regions.observation.source_power', { lat: n(d.point?.[0], 4), lon: n(d.point?.[1], 4), start: formatDate(d.period?.[0], t), end: formatDate(d.period?.[1], t), mb: n(d.mb, 1) });
    return {
      id: String(s.id ?? ''),
      title: typeof s.title === 'string' ? s.title : t.unknown(),
      detail: detail ? `${detail}.` : '',
      license: typeof s.license === 'string' ? s.license : t.unknown(),
    };
  });
}

/**
 * The main page of an observation region (SA-D79, plan 5.6): the observation sentence, the field,
 * the ribbon, the relief line, the climate lines and the sources. No answer, no column, no money,
 * no sheet: decide() never runs, so no state and no G exist in this view. Pure.
 */
export function buildObservationView(data, ui, { used = null } = {}) {
  const t = makeT(data.copy, ui.lang, { used });
  const field = data.fields?.[ui.field] ?? data.fields?.[data.fieldIds?.[0]] ?? null;
  const fields = (data.fieldIds ?? []).map((id) => ({
    id,
    name: t('page.header.field_name', { X: fieldLetter(id) }),
    active: id === (field?.field_id ?? ui.field),
    polygon: data.fields[id]?.polygon_px ?? [],
  }));
  const map = fields.length ? S.fieldMapSvg({ fields, image: ui.mapImage, label: t('page.header.map_alt') }) : '';
  const rb = ribbonView(t, field, 'unknown');
  const ribbon = { ...rb, band: null, legend: rb.legend.filter((l) => l.kind !== 'zarc-end') };
  const ends = Array.isArray(field?.green_end) ? field.green_end : [];
  const area = numValue(field?.area_ha);
  const pt = field?.point;
  return {
    lang: t.lang,
    htmlLang: t('meta.lang'),
    title: t('regions.observation.page_title'),
    switchTo: t('meta.switch_to'),
    switchAria: t('meta.switch_aria'),
    isExample: false,
    exampleWord: t('page.inputs.example'),
    isRehearsal: false,
    rehearsalBanner: null,
    observation: true,
    obs: {
      banner: t('regions.observation.banner'),
      sentence: t('regions.observation.sentence'),
      title: t('regions.observation.title'),
      // The region name from the copy, so it reads in the page language (region.json name is English).
      region: t('regions.observation.region', { name: t('regions.observation.title') }),
      field: area !== null && Number.isFinite(pt?.lat) && Number.isFinite(pt?.lon)
        ? t('regions.observation.field_line', { area: formatNumber(area, t, 1), lat: formatNumber(Math.abs(pt.lat), t, 4), lon: formatNumber(Math.abs(pt.lon), t, 4) })
        : t('missing.no_data'),
      packNote: t('regions.observation.pack_note'),
      ribbonRule: t('regions.observation.ribbon_rule'),
      greenEnd: ends.length
        ? t('regions.observation.green_end_count', { k: formatNumber(ends.filter((e) => e?.status === 'ok').length, t), n: formatNumber(ends.length, t) })
        : null,
      reliefTitle: t('regions.observation.relief_title'),
      relief: observationReliefLines(t, field),
      climateTitle: t('regions.observation.climate_title'),
      climate: observationClimateLines(t, field?.climate),
      sourcesTitle: t('regions.observation.sources_title'),
      sources: observationSources(t, data.region),
      notLoaded: t('regions.observation.not_loaded'),
      back: t('regions.observation.back'),
    },
    fields,
    hasFields: fields.length > 0,
    noFieldText: fields.length ? null : t('missing.no_data'),
    map,
    ribbon,
    dataFolderMissing: data.dataFolderMissing ? t('missing.example_folder') : null,
  };
}

function columnView(t, data, ui, field, result, sid, { sat, trials, showL5 }) {
  // SA-D90: the satellite head of S1 and S2 (satelliteHeads), the warning only when k > 0.
  const wwarn = sid === 'S3' ? null : sat?.warning ?? null;
  const r = sid === 'S1' ? null : result[sid];
  const ev = sid === 'S1' ? null : trials?.[sid];
  const cal = field?.calendar;
  const head = {
    code: t(`page.strategies.${sid}.code`),
    name: t(`page.strategies.${sid}.name`),
    sub: t(`page.strategies.${sid}.sub`),
    badge: r ? { state: r.state, word: t(`states.${r.state}`), icon: S.stateIcon(r.state) } : null,
    warning: sid === 'S3' ? null : wwarn,
    warningSeal: sid === 'S3' || !wwarn ? null : seal(t, wwarn.state === SOIL_CLASS_UNKNOWN ? 'unknown' : 'estimated_from_satellite'),
    fixed: sid === 'S2' ? t('page.strategies.S2.fixed') : null,
    exitLines: sid === 'S3' ? [] : sat?.exitLines ?? [],
    sameSatellite: sid === 'S2' && sat?.same === true,
  };

  // L1
  const L1 = compact([
    line(t(`page.rows.L1.${sid}`)),
    sid === 'S1' ? noGreenLine(t, field) : null,
    sid === 'S1' && cal ? line(t('page.rows.L1.hls_note'), { kind: 'small' }) : null,
  ]);

  // L2
  const risk = field?.risk;
  const zc = risk?.zarc_class?.[sid];
  const crop = { S1: 'corn', S2: 'grass', S3: 'wheat' }[sid];
  const L2 = compact([
    line(t('page.rows.L2.zarc_class', { crop: t(`page.rows.L2.crops.${crop}`), class: zarcClassText(t, zc, soilClassOf(field, ui.soilClass), soilClassOf(field, 'unknown')) }), { ledger: risk?.ledger ?? null }),
    sid === 'S3' && zc?.state === 'zarc_previous_table' ? line(t('page.rows.L2.wheat_zarc'), { kind: 'small' }) : null,
    ...climateLines(t, risk?.climate, sid, t(`page.rows.L2.crops.${crop}`)),
    sid === 'S3' ? line(t('page.rows.L2.frost_alert')) : null,
    sid === 'S1' ? line(t('page.rows.L2.regional'), { kind: 'small' }) : null,
  ]);

  // L3
  let L3;
  let steps = '';
  if (sid === 'S1') {
    // trials_steps.json reference: { season, source, ledger }.
    const ref = isObj(data.trials?.reference) ? data.trials.reference : null;
    const s1 = t.tIf('page.rows.L3.S1', summaryVars(t, ref));
    L3 = compact([s1 ? line(s1, { ledger: ledgerOf(ref) }) : line(t('page.rows.L5.reference'))]);
  } else {
    // options.<sid>.summary: S2 { gain, season, roots_pct, straw_pct, ledger },
    // S3 { delta, bags, pos, neg, a, b, ledger }. Numbers are Num { v, l }. gain, a and b are in %.
    const summary = isObj(ev?.summary) ? ev.summary : null;
    const sv = summaryVars(t, summary);
    for (const k of ['gain', 'a', 'b']) if (numValue(summary?.[k]) !== null) sv[k] = t('meta.percent', { v: sv[k] });
    if (Number.isFinite(ev?.n)) sv.n = formatNumber(ev.n, t);
    // A line with one number links its Num, a line with several links the whole summary.
    const l3 = (key, num = null, extra = {}) => {
      const s = t.tIf(key, sv);
      return s ? line(s, { ledger: (num ? numLedger(summary?.[num]) : null) ?? ledgerOf(summary), ...extra }) : null;
    };
    const extra = sid === 'S2'
      ? [l3('page.rows.L3.S2', 'gain'), l3('page.rows.L3.S2_roots'), line(t('page.rows.L3.S2_model8'), { kind: 'small' }), line(t('page.rows.L3.S2_oats'), { kind: 'small' })]
      : [l3('page.rows.L3.S3'), l3('page.rows.L3.S3_compilation', null, { kind: 'small' }), line(t('page.rows.L3.S3_mp6'), { kind: 'small' })];
    const counted = Number.isFinite(r?.N) && Number.isFinite(r?.n);
    const methods = sid === 'S2' ? s2MethodLines(t, ev, r) : null;
    L3 = compact([
      ...extra,
      counted ? line(t('risk.trials', { N: formatNumber(r.N, t), n: formatNumber(r.n, t) }), { ledger: ev?.ledger ?? null }) : null,
      ...(methods ? [line(t('s2_methods.title'), { kind: 'small' }), ...methods, line(t('s2_methods.limit'), { kind: 'small' })] : []),
      ev ? null : line(t.unknown()),
      dataText(ev?.condition),
      ev?.bad_year?.text ? { ...dataText(ev.bad_year.text), ledger: ev.bad_year.ledger ?? null } : null,
    ]);
    if (ev && Number.isFinite(ev.n)) {
      steps = S.stepsSvg({
        steps: ev.steps,
        n: ev.n,
        G: r?.G,
        label: counted ? t('risk.trials', { N: formatNumber(r.N, t), n: formatNumber(r.n, t) }) : t('page.rows.L3.title'),
      });
    }
  }

  // L4
  let L4;
  if (sid === 'S1') {
    const m = result.S1;
    L4 = compact([
      m.M_corn !== null
        ? line(t('page.rows.L4.S1', { M_corn: formatNumber(m.M_corn, t) }), { ledger: browserLedger('M_corn') })
        : line(t('answers.invalid_field', { field: m.field ? t(`fields.${m.field}`) : t.unknown() })),
    ]);
  } else if (r.state === 'removed') {
    L4 = [line(optionSentence(t, r, trials))];
  } else if (r.code === 'invalid_field' || r.G === null) {
    L4 = [line(optionSentence(t, r, trials))];
  } else {
    L4 = compact([
      line(t(`page.rows.L4.${sid}`, { G: formatNumber(D.shownG(r.G), t) }), { ledger: browserLedger(`G_${sid}`) }),
      line(optionSentence(t, r, trials), { kind: 'state' }),
    ]);
  }

  // L5
  let L5 = null;
  if (showL5) {
    const st = field?.next_soy?.state;
    const key = `page.rows.L5.states.${st}`;
    L5 = compact([
      sid === 'S1' ? line(t('page.rows.L5.reference')) : null,
      sid === 'S2' ? line(t('page.strategies.S2.sub')) : null,
      sid === 'S3' ? line(t.has(key) ? t(key) : t('missing.not_completed'), { ledger: field?.next_soy?.ledger ?? null }) : null,
      sid === 'S3' ? line(t('page.rows.L5.stable_note'), { kind: 'small' }) : null,
    ]);
  }

  // Erosivity
  let E = null;
  if (field?.erosivity) {
    const v = field.erosivity[sid];
    const cls = t.has(`page.rows.erosivity.classes.${v}`) ? t(`page.rows.erosivity.classes.${v}`) : t.unknown();
    E = [line(t('page.rows.erosivity.line', { class: cls }), { ledger: field.erosivity.ledger ?? null })];
  }

  const why = ['why_suggested', 'evidence', 'benefits', 'gaps', 'risks'].map((k) => ({
    label: t(`page.why.items.${k}`),
    text: t(`page.why.${sid}.${k}`),
  }));
  const whyNotes = compact([line(t('page.why.nitrogen')), sid === 'S3' ? line(t('page.why.unaudited')) : null]);

  const cells = [
    { id: 'L1', title: t('page.rows.L1.title'), lines: L1, svg: '' },
    { id: 'L2', title: t('page.rows.L2.title'), lines: L2, svg: '' },
    { id: 'L3', title: t('page.rows.L3.title'), lines: L3, svg: steps },
    { id: 'L4', title: t('page.rows.L4.title'), lines: L4, svg: '' },
    ...(L5 ? [{ id: 'L5', title: t('page.rows.L5.title'), lines: L5, svg: '' }] : []),
    ...(E ? [{ id: 'E', title: erosivityTitle(t), lines: E, svg: '' }] : []),
  ].map((c) => ({ ...c, highlight: c.id === result.highlight, seals: (ROW_SEALS[c.id] ?? []).map((x) => seal(t, x)) }));

  return { id: sid, head, cells, why, whyNotes, whyButton: t('page.why.button') };
}

function inputsView(t, data, ui) {
  const ex = exampleNumbers(data.region);
  return NUMBER_FIELDS.map((f) => {
    const v = parseNumber(ui.raw?.[f], ui.lang);
    return {
      id: f,
      label: t(`page.inputs.${f}.label`),
      unit: t(`page.inputs.${f}.unit`),
      raw: ui.raw?.[f] ?? '',
      example: typeof v === 'number' && v === ex[f],
      invalid: v !== undefined && !D.isValidValue(f, v),
    };
  });
}

/** Three price scenarios (14.3): the typed base, corn price k lower and k higher. */
export function scenariosView(t, data, ui) {
  const k = scenarioK(data.region);
  if (k === null) return null;
  const base = currentNumbers(ui);
  const pct = `${formatNumber(k * 100, t)}%`;
  const make = (id, factor, note) => {
    const numbers = { ...base };
    if (typeof base.p_corn === 'number') numbers.p_corn = Math.round(base.p_corn * factor * 1e6) / 1e6;
    const r = decide(data, ui, { numbers });
    return {
      id,
      label: t(`page.inputs.scenarios.${id}`),
      note,
      S2: t(`states.${r.S2.state}`),
      S3: t(`states.${r.S3.state}`),
      answer: t(`states.${r.answer.state}`),
      answerState: r.answer.state,
    };
  };
  const list = [
    make('low', 1 - k, t('page.inputs.scenarios.low_note', { k: pct })),
    make('base', 1, null),
    make('high', 1 + k, t('page.inputs.scenarios.high_note', { k: pct })),
  ];
  const same = list.every((s) => s.answerState === list[0].answerState);
  return {
    title: t('page.inputs.scenarios.title'),
    list,
    summary: t(same ? 'page.inputs.scenarios.same' : 'page.inputs.scenarios.changes'),
    heads: { S2: optionName(t, 'S2'), S3: optionName(t, 'S3'), answer: t('page.answer.title') },
  };
}

function limitsView(t, ui) {
  const words = {
    yes: 'page.limits.answer_yes',
    no: 'page.limits.answer_no',
    extra_box: 'page.limits.no_grass_seeding.extra_box',
    with_fertilizer: 'page.limits.no_grass_seeding.with_fertilizer',
    broadcast: 'page.limits.no_grass_seeding.broadcast',
    none: 'page.limits.no_grass_seeding.none',
    own: 'page.limits.rented_land.own',
    rented: 'page.limits.rented_land.rented',
  };
  return Object.entries(LIMITS).map(([id, spec]) => ({
    id,
    question: t(`page.limits.${id}.question`),
    effect: t(`page.limits.${id}.effect`),
    value: ui.limits?.[id] ?? '',
    options: spec.options.map((o) => ({ value: o, label: t(words[o]), active: ui.limits?.[id] === o })),
  }));
}

/** Nitrogen exposure (14.8): dose / 0.46 x urea price. Apart from G. Null when not declared. */
export function nitrogenExposure(dose, ureaPerTonne) {
  if (![dose, ureaPerTonne].every((x) => typeof x === 'number' && Number.isFinite(x) && x >= 0)) return null;
  return (dose / UREA_N_SHARE) * (ureaPerTonne / 1000);
}

function nitrogenView(t, ui) {
  const dose = parseNumber(ui.raw?.n_dose, ui.lang);
  const urea = parseNumber(ui.raw?.urea_price, ui.lang);
  const v = nitrogenExposure(dose, urea);
  return {
    title: t('page.rows.L4.nitrogen'),
    note: t('page.rows.L4.nitrogen_note'),
    inputs: NITROGEN_FIELDS.map((f) => ({ id: f, label: t(`page.inputs.${f}.label`), unit: t(`page.inputs.${f}.unit`), raw: ui.raw?.[f] ?? '' })),
    value: v === null ? null : `${formatNumber(v, t)} ${t('page.inputs.c_corn.unit')}`,
    ledger: browserLedger('nitrogen'),
  };
}

// ================================================================ year strip and ribbon

/** Crop word for the year strip legend and table. */
function cropWord(t, crop) {
  if (crop === 'soybean' || crop === 'grass') return t(`page.year_strip.crops.${crop}`);
  if (crop === 'corn') return t('page.rows.L2.crops.corn');
  if (crop === 'corn_grass') return t('page.rows.L2.crops.grass');
  if (crop === 'wheat') return t('page.rows.L2.crops.wheat');
  return t('page.year_strip.no_crop');
}

const DAYS_IN_MONTH = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];

/** Labels of the 36 ten-day periods, "1-10 Jan" style, from the month words of the copy. */
export function tenDayLabels(t, fromMonth = 1) {
  const months = t.list('page.year_strip.months');
  const out = [];
  for (let k = 0; k < 12; k++) {
    const m = ((fromMonth - 1 + k) % 12);
    const name = months[m] ?? String(m + 1);
    out.push(`1-10 ${name}`, `11-20 ${name}`, `21-${DAYS_IN_MONTH[m]} ${name}`);
  }
  return out;
}

export function yearStripView(t, field) {
  const cal = isObj(field?.calendar) ? field.calendar : null;
  if (!cal) return { rows: [], empty: t('missing.no_data'), legend: [], heads: [], months: t.list('page.year_strip.months'), zarcNote: null };
  const rows = STRATEGIES.map((sid) => {
    const segs = Array.isArray(cal[sid]) ? cal[sid] : [];
    const crops = S.segmentsToSlots(segs, (s) => s.state === 'growing');
    const marks = {
      soy_window: S.segmentsToSlots(segs, (s) => s.state === 'legal_window', () => true),
      sanitary_break: S.segmentsToSlots(segs, (s) => s.state === 'legal_break', () => true),
      zarc_window: S.segmentsToSlots(segs, (s) => s.state === 'zarc_window' || s.state === 'zarc_previous_table', () => true),
    };
    const ndvi = sid === 'S1' && Array.isArray(field?.ndvi_series?.points) ? field.ndvi_series.points : [];
    const name = `${t(`page.strategies.${sid}.code`)} ${t(`page.strategies.${sid}.name`)}`;
    return {
      id: sid,
      name,
      svg: S.yearRowSvg({ crops, marks, ndvi, label: name }),
      cells: crops.map((c) => cropWord(t, c)),
      ndvi: ndvi.length > 0,
    };
  });
  const present = new Set();
  for (const sid of STRATEGIES) for (const s of cal[sid] ?? []) if (s?.state === 'growing') present.add(s.crop);
  const legend = [
    ...S.CROPS.filter((c) => present.has(c)).map((c) => ({ kind: `crop-${c}`, text: cropWord(t, c) })),
    { kind: 'crop-none', text: t('page.year_strip.no_crop') },
    { kind: 'mark-soy', text: t('page.year_strip.marks.soy_window') },
    { kind: 'mark-break', text: t('page.year_strip.marks.sanitary_break') },
    { kind: 'mark-zarc', text: t('page.year_strip.marks.zarc_window') },
  ];
  return {
    rows,
    empty: null,
    legend,
    // No Zarc window is drawn when it cannot be matched to this soil: say so under the strip.
    zarcNote: zarcSoilUnknown(field) ? { text: t('window.soil_class_unknown'), ledger: ledgerOf(field?.soil) } : null,
    ndviSeal: rows.some((r) => r.ndvi) ? seal(t, 'seen_by_satellite') : null,
    ndviLedger: field?.ndvi_series?.ledger ?? null,
    heads: tenDayLabels(t, 1),
    months: t.list('page.year_strip.months'),
  };
}

/**
 * The Zarc risk band above the ribbon (SA-D72.1), one thin row for S1 and one for S2, from
 * window_basis.S1/S2.risk_band: the class in use, or the lowest and highest of every class and
 * cycle group without a report. Null when the field ships no band.
 */
export function riskBandView(t, field, soilClass = 'unknown') {
  const basis = field?.window_basis;
  if (!isObj(basis)) return null;
  const cls = soilClassOf(field, soilClass);
  const pct = (v) => (Number.isFinite(v) && v > 0 ? `${formatNumber(v, t)}%` : t('page.ribbon.risk_band.outside'));
  const rows = [];
  let ledger = null;
  let split = false;
  for (const sid of ['S1', 'S2']) {
    const band = basis?.[sid]?.risk_band;
    if (!isObj(band)) continue;
    ledger = ledger ?? ledgerOf(band);
    const name = t(`page.ribbon.risk_band.rows.${sid}`);
    const label = t('page.ribbon.risk_band.title');
    if (cls !== 'unknown') {
      const risk = band.by_class?.[cls]?.risk;
      const ok = Array.isArray(risk) && risk.length === 36;
      const r = ok ? risk : new Array(36).fill(null);
      rows.push({ id: sid, name, svg: S.riskBandSvg({ risk: r, label: `${name}. ${label}` }), cells: r.map(pct) });
    } else {
      const lo = Array.isArray(band.min) && band.min.length === 36 ? band.min : new Array(36).fill(null);
      const hi = Array.isArray(band.max) && band.max.length === 36 ? band.max : new Array(36).fill(null);
      const part = Array.isArray(band.some_outside) ? band.some_outside : [];
      if (lo.some((v, i) => S.riskClass(v) !== S.riskClass(hi[i]))) split = true;
      const cells = lo.map((v, i) => {
        const base = !(Number.isFinite(hi[i]) && hi[i] > 0) ? pct(null)
          : v === hi[i] ? pct(v) : t('page.ribbon.risk_band.range', { a: formatNumber(v, t), b: formatNumber(hi[i], t) });
        return part[i] === true ? `${base} (${t('page.ribbon.risk_band.partial')})` : base;
      });
      rows.push({ id: sid, name, svg: S.riskBandSvg({ min: lo, max: hi, someOutside: part, label: `${name}. ${label}` }), cells });
    }
  }
  if (!rows.length) return null;
  const any = cls === 'unknown';
  return {
    title: t('page.ribbon.risk_band.title'),
    rows,
    legend: [
      ...S.RISK_LEVELS.map((v) => ({ kind: `risk-${v}`, text: `${formatNumber(v, t)}%` })),
      { kind: 'risk-none', text: t('page.ribbon.risk_band.outside') },
      ...(any && split ? [{ kind: 'risk-split', text: t('page.ribbon.risk_band.split') }] : []),
      ...(any ? [{ kind: 'risk-partial', text: t('page.ribbon.risk_band.partial') }] : []),
    ],
    note: any ? t('page.ribbon.risk_band.any') : t('page.ribbon.risk_band.declared', { class: cls }),
    ledger,
    seal: seal(t, 'official_rule'),
  };
}

export function ribbonView(t, field, soilClass = 'unknown') {
  const ribbon = Array.isArray(field?.ribbon?.seasons) ? field.ribbon.seasons : [];
  const ends = Array.isArray(field?.green_end) ? field.green_end : [];
  const names = [...new Set([...ribbon.map((s) => s?.season), ...ends.map((e) => e?.season)].filter((s) => typeof s === 'string'))].sort();
  const zarcEnd = typeof field?.window_reading?.zarc_window_end === 'string' ? field.window_reading.zarc_window_end : null;
  const cellWord = (c) => t(`page.ribbon.cells.${S.CELL_STATES.includes(c) ? c : 'no_data'}`);
  const rows = names.map((season) => {
    const r = ribbon.find((s) => s?.season === season);
    const cells = Array.isArray(r?.cells) && r.cells.length === 36 ? r.cells : new Array(36).fill('no_data');
    const e = ends.find((x) => x?.season === season);
    const exit = e?.status === 'ok' && e.low && e.high ? { low: e.low, high: e.high } : null;
    const exitText = exit
      ? t('risk.date_bar', { d1: formatDate(exit.low, t), d2: formatDate(exit.high, t) })
      : t(`window.states.${e?.status === 'to_confirm' ? 'to_confirm' : 'no_data'}`);
    const twoCycles = S.greenCycles(cells) >= 2 ? t('page.ribbon.two_cycles') : null;
    return {
      season,
      svg: S.ribbonRowSvg({ cells, season, exit, zarcEnd, label: `${season}. ${t('page.ribbon.soy_exit')}: ${exitText}` }),
      cells: cells.map(cellWord),
      exitText,
      twoCycles,
      ledger: numLedger(e?.gap_days) ?? field?.ribbon?.ledger ?? null,
    };
  });
  return {
    rows,
    empty: rows.length ? null : t('missing.no_data'),
    legend: [
      ...S.CELL_STATES.map((c) => ({ kind: `cell-${c}`, text: cellWord(c) })),
      { kind: 'exit-bar', text: t('page.ribbon.soy_exit') },
      { kind: 'zarc-end', text: t('page.ribbon.zarc_end') },
    ],
    note: t('page.ribbon.legend'),
    ledger: typeof field?.ribbon?.ledger === 'string' ? field.ribbon.ledger : null,
    seals: [seal(t, 'estimated_from_satellite'), seal(t, 'seen_by_satellite')],
    heads: tenDayLabels(t, 9),
    months: [...t.list('page.year_strip.months').slice(8), ...t.list('page.year_strip.months').slice(0, 8)],
    band: riskBandView(t, field, soilClass),
  };
}

// ================================================================ sheet link

/**
 * State for sheet.html in the URL fragment, which never reaches a server (8.4 step 20).
 * Keys: lang, field, hls (1 or 0), priority, sowing_date, soil_class (when declared), each number, each limit answered and
 * option (S2 or S3, the option of the main sentence, absent when both are removed).
 */
export function sheetHash(ui, option = null) {
  const p = new URLSearchParams();
  p.set('lang', ui.lang);
  if (ui.field) p.set('field', ui.field);
  p.set('hls', ui.hlsOn ? '1' : '0');
  p.set('priority', ui.priority);
  if (ui.sowingDate) p.set('sowing_date', ui.sowingDate);
  if (SOIL_CLASS_OPTIONS.includes(ui.soilClass) && ui.soilClass !== 'unknown') p.set('soil_class', ui.soilClass);
  const nums = currentNumbers(ui);
  for (const f of NUMBER_FIELDS) if (typeof nums[f] === 'number' && Number.isFinite(nums[f])) p.set(f, String(nums[f]));
  for (const [k, v] of Object.entries(ui.limits ?? {})) if (v) p.set(k, v);
  if (D.OPTIONS.includes(option)) p.set('option', option); // the option of the main sentence
  return p.toString();
}

// ================================================================ How we know view

// The English status texts of pipeline/bench/label.py STATUS_TEXT. A note equal to one of them only
// repeats the status, in English, so it is not shown.
const STATUS_NOTE_TEXTS = new Set(['pass', 'fail', 'planned, not run', 'not completed', 'exploratory', 'reported']);

function statusWord(t, status) {
  const key = `howweknow.scoreboard.status.${status}`;
  return t.has(key) ? t(key) : t('missing.not_completed');
}

function compactValue(v, t) {
  if (v === null || v === undefined) return '';
  if (typeof v === 'number') return Number.isFinite(v) ? formatNumber(v, t, Number.isInteger(v) ? 0 : 2) : t.unknown();
  if (typeof v === 'boolean' || typeof v === 'string') return String(v);
  if (Array.isArray(v)) return v.map((x) => compactValue(x, t)).join(', ');
  if (isObj(v)) return Object.entries(v).map(([k, x]) => `${k} ${compactValue(x, t)}`).join(', ');
  return '';
}

export const SCOREBOARD_TESTS = Object.freeze(['L0', 'L1', 'T0', 'T1', 'P1', 'A1', 'A2', 'A3', 'N1', 'H1', 'H2', 'R1']);

// Tests whose not_run line gives the reason from the scoreboard note and the protocol (SA-D89).
const NOT_RUN_REASONS = Object.freeze(['L1', 'A2', 'H1', 'H2', 'R1']);

/**
 * The plain pass rule of one scoreboard test (SA-D89), from the thresholds of scoreboard.json in
 * the language of `t`. No threshold gives "No pass rule" (A2, N1 and H1 are reported, never
 * passed, protocol 9). A rule whose numbers are missing gives the unknown word. Pure.
 */
export function scoreboardRule(t, x) {
  const th = isObj(x?.threshold) ? x.threshold : null;
  const k = (key, vars) => t.tIf(`howweknow.scoreboard.rules.${key}`, vars);
  const fin = (n) => typeof n === 'number' && Number.isFinite(n);
  const n0 = (n, d = 0) => (fin(n) ? formatNumber(n, t, d) : null);
  // Protocol 9: A2, A3, N1 and H1 have no threshold and show as reported. H2 is a review, no rule.
  if (!th) return t(x?.id === 'H2' ? 'howweknow.scoreboard.rules.none_plain' : 'howweknow.scoreboard.rules.none');
  let out = null;
  switch (x?.id) {
    case 'L0':
      if (th.repeat === 'exact') {
        out = th.unexplained_artifacts_max === 0
          ? k('L0_none', { c: n0(th.closure_mm_per_swap, 1) })
          : k('L0', { c: n0(th.closure_mm_per_swap, 1), max: n0(th.unexplained_artifacts_max) });
      }
      break;
    case 'L1':
      out = k('L1', { r: n0(th.rain_bias_max_pct_per_season, 1), c: n0(th.temperature_bias_max_c, 1) });
      break;
    case 'T0':
      out = k('T0', { s: n0(th.s_min), w: n0(th.within_site_s_sum_gt) });
      break;
    case 'T1':
      out = k('T1', { s: n0(th.s_min), c: n0(th.rmse_lt_kg_ha) });
      break;
    case 'P1':
      out = th.wrong_year_s_at_least_max === 0
        ? k('P1_none', { shifts: n0(th.shifts) })
        : k('P1_some', { max: n0(th.wrong_year_s_at_least_max), shifts: n0(th.shifts) });
      break;
    case 'A1':
      out = th.d_gt === 0 ? t('howweknow.scoreboard.rules.A1_zero') : k('A1', { d: n0(th.d_gt) });
      break;
    case 'A3':
      out = th.zero_changes === 'context only' ? t('howweknow.scoreboard.rules.A3') : null;
      break;
    case 'R1':
      // inversion_max is a share: 0.2 reads 20%.
      if (th.runs_only_if === 'T1 passes') out = k('R1', { inv: fin(th.inversion_max) ? n0(th.inversion_max * 100) : null, eq: n0(th.equivalence_kg_ha) });
      break;
    default:
      break;
  }
  return out ?? t.unknown();
}

/**
 * The plain line of one scoreboard test (SA-D89): a status sentence, then one sentence built from
 * the same numbers of scoreboard.json, in the language of `t`. A sentence whose numbers are missing,
 * or whose wording would not fit the numbers, is left out. No number is changed, only rounded for
 * reading: S and counts whole, RMSE whole kg/ha, the noise floor to one decimal. Pure.
 */
export function scoreboardLine(t, x) {
  const status = isObj(x) && typeof x.status === 'string' ? x.status : 'not_completed';
  const statusKey = `howweknow.scoreboard.status_line.${status}`;
  const head = t.has(statusKey) ? t(statusKey) : t('howweknow.scoreboard.status_line.not_completed');
  const v = isObj(x?.value) ? x.value : {};
  const th = isObj(x?.threshold) ? x.threshold : {};
  const fin = (n) => typeof n === 'number' && Number.isFinite(n);
  const n0 = (n) => (fin(n) ? formatNumber(n, t) : null);
  const k = (key, vars) => t.tIf(`howweknow.scoreboard.lines.${key}`, vars);
  const parts = [];
  switch (x?.id) {
    case 'L0':
      // Only when the water balance closed and the runs repeated: then the artifacts made it fail.
      if (status === 'fail' && fin(v.max_gap_mm) && fin(th.closure_mm_per_swap) && v.max_gap_mm <= th.closure_mm_per_swap
        && v.repeat_exact === true && fin(v.n_unexplained) && fin(th.unexplained_artifacts_max) && v.n_unexplained > th.unexplained_artifacts_max) {
        parts.push(k('L0.fail_artifacts', { u: n0(v.n_unexplained), a: n0(v.n_artifacts), max: n0(th.unexplained_artifacts_max) }));
      }
      break;
    case 'T0':
      parts.push(k('T0.any', { s: n0(v.s), s_min: n0(th.s_min) }));
      break;
    case 'T1':
      parts.push(k('T1.any', { s: n0(v.s), s_min: n0(th.s_min), rmse: n0(v.rmse_kg_ha), constant: n0(th.rmse_lt_kg_ha ?? v.rmse_constant_in_sample_kg_ha) }));
      break;
    case 'P1':
      parts.push(k('P1.any', { t0_k: n0(v.t0?.wrong_year_s_at_least), t1_k: n0(v.t1?.wrong_year_s_at_least), shifts: n0(th.shifts), max: n0(th.wrong_year_s_at_least_max) }));
      break;
    case 'A1': {
      const vars = { with: n0(v.s_with_state), without: n0(v.s_without_state) };
      parts.push(k(status === 'fail' && fin(v.d) && v.d > 0 ? 'A1.fail_paired' : 'A1.any', vars));
      break;
    }
    case 'A3':
      parts.push(k('A3.any', { k: n0(v.states_changed), n: n0(v.states_total) }));
      if (v.verdict === 'context_only') parts.push(t('howweknow.scoreboard.lines.context_only'));
      break;
    case 'N1':
      parts.push(k('N1.any', { floor: fin(v.floor_days) ? formatNumber(v.floor_days, t, 1) : null }));
      break;
    default:
      break;
  }
  if (status === 'not_run' && NOT_RUN_REASONS.includes(x?.id)) parts.push(t(`howweknow.scoreboard.lines.${x.id}.not_run`));
  return { status: head, text: parts.filter(Boolean).join(' ') };
}

/**
 * True only when the scoreboard shows a local review (M8.5): the H2 line has status pass, fail
 * or reported and a literal comment, its `note`, as a non-empty string. Anything else, a missing
 * scoreboard included, means there was no local review. How we know and the sheet (sheet.mjs)
 * both use this rule, so they always agree. Today H2 is not_run (SA-D56).
 */
export function localReviewDone(scoreboard) {
  const sb = isObj(scoreboard?.scoreboard) ? scoreboard.scoreboard : scoreboard;
  const tests = isObj(sb) && Array.isArray(sb.tests) ? sb.tests : [];
  const h2 = tests.find((x) => isObj(x) && x.id === 'H2');
  return isObj(h2) && H2_REVIEW_STATUSES.includes(h2.status) && typeof h2.note === 'string' && h2.note.trim() !== '';
}

// Kinds of manifest source that are data (pipeline/ledger/registry.py SOURCE_INFO), not documents.
const DATA_KINDS = Object.freeze(['official', 'reanalysis', 'soil', 'satellite', 'dem', 'climatology', 'station']);
const RAN = Object.freeze(['pass', 'fail', 'reported']);
const CHAIN_TESTS = Object.freeze(['T0', 'T1', 'P1', 'A1']);

/**
 * The four levels of state (SA-D66), from the data the page reads: "verified internally" from
 * the ledger and the L0 line, "data obtained" from manifest.json (sources in the cache), "not yet
 * executed" from the fields and the scoreboard statuses, "not yet demonstrated" from the copy,
 * because no file here can show predictive skill or validity elsewhere. Passing code tests is
 * never shown as validation of the model. Pure.
 */
export function stateLevels(t, data) {
  const sb = data.scoreboard;
  const byId = new Map((Array.isArray(sb?.tests) ? sb.tests : []).map((x) => [x?.id, x]));
  const ran = (id) => RAN.includes(byId.get(id)?.status);
  const verified = [];
  if (Object.keys(data.records ?? {}).length) verified.push(t('howweknow.levels.verified.ledger'));
  if (byId.get('L0')?.status === 'pass') verified.push(t('howweknow.levels.verified.l0'));
  verified.push(t('howweknow.levels.verified.tests'));
  const obtained = (Array.isArray(data.manifest?.sources) ? data.manifest.sources : [])
    .filter((s) => isObj(s) && s.cached === true && DATA_KINDS.includes(s.kind))
    .map((s) => (typeof s.title === 'string' && s.title ? s.title : String(s.source_id ?? t.unknown())));
  const notRun = [];
  if (!data.fieldIds?.length) notRun.push(t('howweknow.levels.not_executed.fields'));
  const chains = CHAIN_TESTS.filter((id) => !ran(id));
  if (chains.length) notRun.push(t('howweknow.levels.not_executed.chains', { ids: chains.join(', ') }));
  const bench = SCOREBOARD_TESTS.filter((id) => !ran(id) && !CHAIN_TESTS.includes(id));
  if (bench.length) notRun.push(t('howweknow.levels.not_executed.bench', { ids: bench.join(', ') }));
  const level = (id, items, empty) => ({
    id,
    title: t(`howweknow.levels.${id}.title`),
    items: items.length ? items : [t(empty)],
  });
  return [
    level('verified', verified, 'missing.not_completed'),
    level('obtained', obtained, 'missing.not_completed'),
    level('not_executed', notRun, 'howweknow.levels.not_executed.none'),
    level('not_demonstrated', t.list('howweknow.levels.not_demonstrated.items'), 'missing.unknown'),
  ];
}

/** Everything how-we-know.html shows. Pure. */
export function buildHowView(data, ui, { used = null } = {}) {
  const t = makeT(data.copy, ui.lang, { used });
  const sb = data.scoreboard;
  const lid = labelId(data);
  const byId = new Map((Array.isArray(sb?.tests) ? sb.tests : []).map((x) => [x?.id, x]));
  const tests = SCOREBOARD_TESTS.map((id) => {
    const x = byId.get(id);
    const plain = x ? scoreboardLine(t, x) : null;
    const ledger = `scoreboard.tests.${id}`;
    const value = x ? compactValue(x.value, t) : '';
    // The scoreboard note often repeats the status text in English: shown only when it adds something.
    const note = x && typeof x.note === 'string' && !STATUS_NOTE_TEXTS.has(x.note) ? x.note : null;
    return {
      id,
      question: t(`howweknow.scoreboard.tests.${id}`),
      // SA-D89: the plain rule on the page, the stored thresholds behind the same toggle.
      rule: x ? scoreboardRule(t, x) : t.unknown(),
      ruleRaw: x ? compactValue(x.threshold, t) : '',
      ruleRawText: x && compactValue(x.threshold, t) ? t('howweknow.scoreboard.raw_rule', { raw: compactValue(x.threshold, t) }) : '',
      status: x ? statusWord(t, x.status) : t('missing.not_completed'),
      // SA-D89: the plain line on the page, the stored values behind a toggle.
      statusLine: plain ? plain.status : t('missing.not_completed'),
      line: plain ? plain.text : '',
      ledger: data.records && Object.hasOwn(data.records, ledger) ? ledger : null,
      value,
      valueText: value ? t('howweknow.scoreboard.raw_result', { raw: value }) : '',
      note,
      hasRaw: Boolean(value || note || (x && compactValue(x.threshold, t))),
    };
  });
  const sbs = isObj(sb?.s_by_site) ? sb.s_by_site : null;
  const sVal = (k) => (sbs && Number.isFinite(sbs[k]) ? formatNumber(sbs[k], t) : null);
  const hls = (Array.isArray(data.ablation?.sources) ? data.ablation.sources : []).find((s) => s?.id === 'hls_vi_v2');
  const field = data.fields?.[ui.field] ?? data.fields?.[data.fieldIds?.[0]] ?? null;
  const area = numValue(field?.area_ha);
  const scale = SCALE_SOURCES.map(([key, name, unit]) => {
    const size = numValue(field?.scale_note?.[key]);
    if (size === null || area === null || area <= 0) return null;
    const cellHa = unit === 'km' ? size * size * 100 : (size * size) / 10000;
    const count = cellHa / area;
    if (count < 1) return null; // finer than the field: not a rung of the ladder
    return { text: t('howweknow.scale.row', { source: name, count: formatNumber(count, t) }), ledger: numLedger(field.scale_note[key]) };
  }).filter(Boolean);
  const manifestSources = (Array.isArray(data.manifest?.sources) ? data.manifest.sources : []).map((s) => ({
    title: typeof s?.title === 'string' ? s.title : s?.source_id ?? t.unknown(),
    license: typeof s?.license === 'string' ? s.license : t.unknown(),
    version: typeof s?.version === 'string' ? s.version : '',
    url: typeof s?.url === 'string' && /^https:\/\//.test(s.url) ? s.url : null,
  }));
  const pack = isObj(data.region?.pack) ? data.region.pack : {};
  const place = switchPlacement(data);
  const placeVars = { X: formatNumber(place.X, t), Y: formatNumber(place.Y, t), min: place.min === null ? null : formatNumber(place.min, t) };

  return {
    lang: t.lang,
    htmlLang: t('meta.lang'),
    title: `${t('howweknow.title')} | ${t('page.title')}`,
    switchTo: t('meta.switch_to'),
    switchAria: t('meta.switch_aria'),
    isExample: data.isExample,
    exampleWord: t('page.inputs.example'),
    isRehearsal: data.isRehearsal,
    rehearsalBanner: data.isRehearsal ? t('page.rehearsal_banner') : null,
    // SA-D79: a link to the observation region, only when its copy is loaded.
    // Checked on the tree, not with t.has, so a page without the region copy never asks for it.
    regionLink: typeof lookup(copyTree(data.copy, t.lang), 'regions.how_link') === 'string'
      ? { text: t('regions.how_link'), note: t('regions.how_link_note'), href: OBSERVATION_HREF }
      : null,
    currentLabel: t('howweknow.current_label', { label: t(`labels.${lid}.title`) }),
    labelBody: t(`labels.${lid}.body`),
    noLocalReview: localReviewDone(sb) ? null : t('howweknow.no_local_review'),
    waterNote: t('howweknow.water_note'),
    tests,
    rawLabel: t('howweknow.scoreboard.raw'),
    sBySite: sbs
      ? t('howweknow.scoreboard.s_by_site', { londrina: sVal('Londrina'), floresta: sVal('Floresta'), cafelandia: sVal('Cafelandia') ?? sVal('Cafelândia') })
      : null,
    t1Failed: sb?.t1_failed_on && t.has(`howweknow.scoreboard.t1_failed_on.${sb.t1_failed_on}`) ? t(`howweknow.scoreboard.t1_failed_on.${sb.t1_failed_on}`) : null,
    scoreboardMissing: sb ? null : t('howweknow.scoreboard.not_completed'),
    dataFolderMissing: data.dataFolderMissing ? t('missing.example_folder') : null,
    switchPlace: {
      text: place.min === null
        ? t('howweknow.switch_placement.no_rule')
        : t(place.onMain ? 'howweknow.switch_placement.on_main' : 'howweknow.switch_placement.here', placeVars),
      ledger: place.min === null ? null : browserLedger('switch'),
      contextOnly: place.onMain ? null : t('page.nasa_switch.context_only'),
    },
    levels: stateLevels(t, data),
    ablation: {
      body: hls
        ? t('howweknow.ablation.body', { S: numValue(hls.states_changed) !== null ? formatNumber(numValue(hls.states_changed), t) : null, Y: numValue(hls.states_total) !== null ? formatNumber(numValue(hls.states_total), t) : null })
        : t('missing.not_completed'),
      ledger: numLedger(hls?.states_changed),
      contextOnly: hls?.verdict === 'context_only' ? t('page.nasa_switch.context_only') : null,
    },
    scale: { body: area !== null ? t('howweknow.scale.body', { area: formatNumber(area, t, 1) }) : t('missing.no_data'), rows: scale },
    manifest: manifestSources,
    manifestMissing: manifestSources.length ? null : t('missing.not_completed'),
    pack: ['decision', 'strategies', 'calendar', 'trials', 'reviewer'].map((k) => ({
      text: t(`howweknow.region.pack.${k}`),
      state: pack[k] === true ? t('howweknow.region.pack_ready') : t('howweknow.region.pack_missing'),
      ok: pack[k] === true,
    })),
    declared: t.list('howweknow.limits.declared'),
    notUsed: t.list('howweknow.limits.not_used'),
    declarations: t.list('howweknow.protocol.declarations'),
    glossary: Object.keys(lookup(copyTree(data.copy, DEFAULT_LANG), 'glossary.terms') ?? {}).map((k) => ({
      id: k,
      term: t(`glossary.terms.${k}.term`),
      definition: t(`glossary.terms.${k}.definition`),
    })),
    seals: S.SEAL_IDS.map((id) => ({ ...seal(t, id), help: t(`seals.help.${id}`) })),
    cropRoles: ['nitrogen_fixers', 'cover_crops', 'deep_roots', 'drought_tolerant'].map((k) => ({
      name: t(`howweknow.crop_roles.${k}.name`),
      text: t(`howweknow.crop_roles.${k}.text`),
    })),
  };
}

// ================================================================ basemap

/**
 * The basemap under the field polygons, or null for the polygons alone. The georeference is the
 * JSON of pipeline/fields/basemap.py: width and height in PNG pixels and an unrotated transform
 * (a, b, c, d, e, f). polygon_px are pixels of that PNG, so the image shows only when the loaded
 * PNG (`natural`: naturalWidth, naturalHeight) has exactly the declared size. `href` is the PNG
 * URL that was loaded (basemapUrls), so a rehearsal folder shows its own basemap. Pure.
 */
export function basemapImage(georef, natural, href = BASEMAP.png) {
  if (!isObj(georef)) return null;
  const { width: w, height: h, transform: tr } = georef;
  if (!Number.isSafeInteger(w) || !Number.isSafeInteger(h) || w <= 0 || h <= 0) return null;
  if (!Array.isArray(tr) || tr.length < 6 || !tr.slice(0, 6).every(Number.isFinite) || tr[1] !== 0 || tr[3] !== 0) return null;
  if (!isObj(natural) || natural.width !== w || natural.height !== h) return null;
  return { href, width: w, height: h };
}

// ================================================================ browser

async function fetchJsonFrom(win, url) {
  try {
    const res = await win.fetch(url, { cache: 'no-cache' });
    if (!res.ok) return null;
    return await res.json();
  } catch {
    return null;
  }
}

function safeStorage(win) {
  try {
    const s = win.localStorage;
    const probe = '__aftercrop_probe__';
    s.setItem(probe, '1');
    s.removeItem(probe);
    return s;
  } catch {
    return null;
  }
}

/** Start the page in a browser. Never runs in Node. */
export async function boot(win = globalThis.window) {
  const doc = win.document;
  const page = doc.body?.dataset?.page === 'how' ? 'how' : 'index';
  const data = await loadAll((url) => fetchJsonFrom(win, url), dataBase(win.location.search));
  if (!data.copy) {
    doc.body.classList.add('is-broken');
    return;
  }
  const storage = safeStorage(win);
  const ui0 = initialUi(data, storedLang(storage) ?? DEFAULT_LANG);
  doc.body.insertAdjacentHTML('afterbegin', S.defsSvg());
  // Links between the site's pages keep a ?data= folder (rehearsal, fixtures). An observation
  // region links to the product pages, which hold the method and the verdict of the default region.
  if (!data.isObservation) {
    for (const a of doc.querySelectorAll('a[href="index.html"], a[href="how-we-know.html"]')) {
      a.setAttribute('href', pageHref(a.getAttribute('href'), data.base));
    }
  }

  const { default: Alpine } = await import('../vendor/alpine-3.17.5.min.js');
  const build = page === 'how' ? buildHowView : buildIndexView;

  Alpine.data('aftercrop', () => ({
    ui: ui0,
    v: build(data, ui0),
    init() {
      Alpine.effect(() => {
        const ui = JSON.parse(JSON.stringify(this.ui));
        this.v = build(data, ui);
        const t = makeT(data.copy, ui.lang);
        doc.documentElement.lang = this.v.htmlLang;
        doc.title = this.v.title;
        applyCopy(doc, t);
      });
      if (page === 'index') this.loadMap();
    },
    async loadMap() {
      // Georeference first. Without it, or without the PNG, the map shows the polygons alone.
      const urls = basemapUrls(data.base);
      const georef = await fetchJsonFrom(win, urls.georef);
      if (!basemapImage(georef, { width: georef?.width, height: georef?.height })) return;
      const img = new win.Image();
      img.onload = () => {
        this.ui.mapImage = basemapImage(georef, { width: img.naturalWidth, height: img.naturalHeight }, urls.png);
      };
      img.onerror = () => {
        this.ui.mapImage = null;
      };
      img.src = urls.png;
    },
    toggleLang() {
      const next = this.ui.lang === 'en' ? 'pt' : 'en';
      Object.assign(this.ui, switchLang(this.ui, next, data.copy));
      storeLang(storage, next);
    },
    setField(id) {
      if (data.fieldIds.includes(id)) this.ui.field = id;
    },
    mapClick(ev) {
      const el = ev.target instanceof win.Element ? ev.target.closest('[data-field]') : null;
      if (el) this.setField(el.getAttribute('data-field'));
    },
    setRaw(id, value) {
      this.ui.raw[id] = value;
    },
    setDate(value) {
      this.ui.sowingDate = value;
    },
    setLimit(id, value) {
      this.ui.limits[id] = this.ui.limits[id] === value ? '' : value;
    },
    setPriority(p) {
      if (PRIORITIES.includes(p)) this.ui.priority = p;
    },
    toggleHls() {
      this.ui.hlsOn = !this.ui.hlsOn;
    },
    setSoilClass(value) {
      this.ui.soilClass = SOIL_CLASS_OPTIONS.includes(value) ? value : 'unknown';
    },
    reset() {
      const fresh = initialUi(data, this.ui.lang);
      this.ui.raw = fresh.raw;
      this.ui.sowingDate = '';
      this.ui.soilClass = 'unknown';
      this.ui.limits = fresh.limits;
    },
  }));
  Alpine.start();
  // Elements of x-if blocks (decision and observation parts of index.html) enter the page after
  // the first applyCopy of init(): fill their data-copy keys now and after the next tick.
  const fill = () => applyCopy(doc, makeT(data.copy, doc.documentElement.lang === 'pt-BR' ? 'pt' : 'en'));
  fill();
  Alpine.nextTick(fill);
  doc.body.classList.add('is-ready');

  const dialog = doc.getElementById('ledger-dialog');
  attachLedger(doc, dialog, () => {
    const t = makeT(data.copy, doc.documentElement.lang === 'pt-BR' ? 'pt' : 'en');
    return {
      records: data.records,
      sealName: (id) => (t.has(`seals.names.${id}`) ? t(`seals.names.${id}`) : id),
      unknown: t.unknown(),
      title: t('ledger.title'),
      close: t('ledger.close'),
    };
  });
}

if (typeof window !== 'undefined' && typeof document !== 'undefined' && document.body?.dataset?.page) {
  boot(window);
}

// Written with an SI tool (Claude, Anthropic) for NASA Space Apps 2026. See AI_USE.md.
// Aftercrop agronomist sheet (plan 7.12, 7.14, 13.5, 14.3 to 14.8, sheet M8, criteria M8.1 to M8.5).
//
// "Take this to my agronomist" opens sheet.html with the page state in the URL fragment, as
// app.mjs sheetHash() writes it: lang, field, hls, priority, sowing_date, soil_class, each number typed and
// each limit answered. A fragment never reaches a server. The sheet reads it in the browser,
// loads the same static data files as the page (app.mjs loadAll, nothing of the state in any
// request), rebuilds the decision input with app.mjs decisionInput, and prints A4 by print CSS.
// No server, no storage, no network call with user data.
//
// Every user-facing word comes from the copy (config/copy_en.yaml and config/copy_pt.yaml through
// data/copy.json, keys sheet.*, states.*, answers.*, fields.*, flags.*, seals.names.*, page.inputs.*,
// page.limits.*, page.strategies.* and meta.*). This file holds no user-facing text.
//
// The state on the sheet is computed by site/js/decision.mjs from those inputs, never read
// from the fragment. A value that is missing, not numeric or outside its domain prints as the
// sheet's missing-value text (sheet.date_missing), never as a number.
//
// Language (P2.3, in scope by SA-D70): the sheet follows the page. sheetLang() reads ?lang=en|pt
// first (the sheet's own language link, make sheet-pdf), then the lang the page wrote in the
// fragment, and falls back to English, the page default. Dates follow sheet.date_format.
//
// Pure functions first (Node tests import them), then the DOM part (mount, renderSheet),
// which runs only when sheet.html calls mount(). Importing this file has no side effect.

import * as D from './decision.mjs';
import { FIELD_IDS, LIMITS, NUMBER_FIELDS, dataBase, decisionInput as pageInput, loadAll, localReviewDone, zarcSoilUnknown } from './app.mjs';

export const MAX_FRAGMENT_LENGTH = 4000;
export const SHEET_LANGS = Object.freeze(['en', 'pt']);
export const DEFAULT_SHEET_LANG = 'en';

/** The sheet language: ?lang= of the query, else lang of the fragment, else English. */
export function sheetLang(search, fragmentLang) {
  let q = null;
  try {
    q = new URLSearchParams(typeof search === 'string' ? search : '').get('lang');
  } catch {
    q = null;
  }
  if (SHEET_LANGS.includes(q)) return q;
  return SHEET_LANGS.includes(fragmentLang) ? fragmentLang : DEFAULT_SHEET_LANG;
}

/** Query string of the same sheet in the other language, keeping ?data= and every other key. */
export function otherLangSearch(search, lang) {
  let q;
  try {
    q = new URLSearchParams(typeof search === 'string' ? search : '');
  } catch {
    q = new URLSearchParams();
  }
  q.set('lang', lang === 'pt' ? 'en' : 'pt');
  return `?${q.toString()}`;
}

/** 1 alqueire = 2.42 ha, as the sheet declares (sheet.items.assumptions, plan 14.2 and 14.7). */
export const ALQUEIRE_HA = 2.42;

/**
 * Alerts of 13.5 per strategy, as pipeline/decision/strategies.py risk_alerts (tests/test_sheet.py
 * checks they agree). region.json carries the alerts but not this list. No alert changes a state.
 */
export const OPTION_ALERTS = Object.freeze({
  S1: Object.freeze(['leafhopper_stunting']),
  S2: Object.freeze(['pratylenchus', 'leafhopper_stunting']),
  S3: Object.freeze(['wheat_root_disease', 'gibberella', 'frost_at_heading', 'white_mold', 'blast_and_harvest_rain']),
});

/** Keys of the resolved sheet state that buildSheet reads. */
export const STATE_KEYS = Object.freeze([
  'field', // field id, shown through sheet.field
  'area_ha', // field area in ha, number or Num { v }
  'option', // option to review: "S2" or "S3". Without it, the main answer of decision.mjs
  'numbers', // the user's numbers, as decision.mjs reads them
  'trials', // { S2: Evid, S3: Evid } of trials_steps.json
  'constraints', // [{ id, removes }] from the user's limits
  'hardRules', // [{ id, removes }] from region.json
  'zarcWindow', // { corn: { start, end }, grass: { start, end } }, ISO dates
  'windowReading', // { state, k, n } of field_<id>.json
  'sowingDate', // declared corn sowing date, ISO, or null
  'hlsOn', // NASA switch
  'zarcConstraint', // insurance or credit tied to the Zarc: true, false, or absent when not answered
  'rentedLand', // "own", "rented" or absent
  'zarcSoilUnknown', // true when M5 says soil_class_unknown: no Zarc window is matched to this soil yet
]);

/** Item order of 7.12. The satellite line is the second line of item 2. */
export const ITEMS = Object.freeze(['option', 'window', 'assumptions', 'evidence', 'unknowns', 'local_review', 'strip_test']);

/** Flags of decision.mjs that item 2 prints. window_warning is the satellite line itself. */
const WINDOW_FLAGS = Object.freeze(['date_in_charge', 'check_window_with_bank', 'date_invalid', 'zarc_window_unknown']);

/** Inputs listed under item 3 when no option can be named (both options removed). */
const ALL_INPUTS = Object.freeze([...new Set([...D.ESSENTIAL.S3, ...D.ESSENTIAL.S2])]);

const PLACEHOLDER = /\{([A-Za-z_][A-Za-z0-9_]*)\}/g;
const FIELD_ID = /^[A-Za-z0-9_]{1,12}$/;
const NUMBER = /^-?(?:\d+\.?\d*|\.\d+)(?:e[+-]?\d+)?$/i;

function isObject(v) {
  return typeof v === 'object' && v !== null && !Array.isArray(v);
}

function own(obj, key) {
  return isObject(obj) && Object.hasOwn(obj, key) ? obj[key] : undefined;
}

// ---------------------------------------------------------------- fragment

/**
 * Reads location.hash in the format of app.mjs sheetHash(). Never throws. Returns
 * { params, error }: params = { lang, field, hlsOn, priority, sowingDate, numbers, limits, option },
 * with only known keys and values. error is null, "empty", "too_long" or "no_state".
 * A number that does not parse stays a string, so decision.mjs reports it as invalid.
 */
export function parseFragment(hash) {
  const empty = { numbers: {}, limits: {} };
  const raw = typeof hash === 'string' ? hash.replace(/^#/, '') : '';
  if (raw === '') return { params: empty, error: 'empty' };
  if (raw.length > MAX_FRAGMENT_LENGTH) return { params: empty, error: 'too_long' };
  let q;
  try {
    q = new URLSearchParams(raw);
  } catch {
    return { params: empty, error: 'no_state' };
  }
  const params = { numbers: {}, limits: {} };
  let known = 0;
  const get = (k) => {
    const v = q.get(k);
    if (v !== null) known++;
    return v;
  };
  const lang = get('lang');
  if (lang === 'pt' || lang === 'en') params.lang = lang;
  const field = get('field');
  if (field !== null && FIELD_IDS.includes(field)) params.field = field;
  const hls = get('hls');
  if (hls !== null) params.hlsOn = hls !== '0';
  const priority = get('priority');
  if (priority !== null && /^[a-z]{1,12}$/.test(priority)) params.priority = priority;
  const date = get('sowing_date');
  if (date !== null && date.length <= 32) params.sowingDate = date;
  const soilClass = get('soil_class');
  if (D.SOIL_CLASSES.includes(soilClass)) params.soilClass = soilClass; // SA-D64, declared on the page
  const option = get('option');
  if (D.OPTIONS.includes(option)) params.option = option;
  for (const f of NUMBER_FIELDS) {
    const v = get(f);
    if (v === null) continue;
    const t = v.trim();
    params.numbers[f] = NUMBER.test(t) && Number.isFinite(Number(t)) ? Number(t) : v.slice(0, 32);
  }
  for (const [id, spec] of Object.entries(LIMITS)) {
    const v = get(id);
    if (v !== null && spec.options.includes(v)) params.limits[id] = v;
  }
  return { params, error: known ? null : 'no_state' };
}

/**
 * The sheet state from the fragment params and the data of app.mjs loadAll (normalized).
 * The decision input is the page's own (app.mjs decisionInput), so the sheet and the page
 * always show the same state for the same numbers.
 */
export function resolveState(params, data) {
  const p = isObject(params) ? params : {};
  const d = isObject(data) ? data : {};
  const id = typeof p.field === 'string' ? p.field : null;
  const field = id && isObject(d.fields) && isObject(d.fields[id]) ? d.fields[id] : null;
  const limits = isObject(p.limits) ? p.limits : {};
  const ui = { hlsOn: p.hlsOn !== false, sowingDate: p.sowingDate ?? '', soilClass: p.soilClass ?? 'unknown', limits, priority: p.priority };
  const input = pageInput(d, ui, field, { numbers: isObject(p.numbers) ? p.numbers : {}, hlsOn: ui.hlsOn });
  const zarc = limits.zarc_insurance_credit;
  return sanitizeState({
    ...input,
    field: field ? id : null,
    area_ha: field ? field.area_ha : undefined,
    option: p.option,
    zarcConstraint: zarc === 'yes' ? true : zarc === 'no' ? false : undefined,
    rentedLand: limits.rented_land,
    zarcSoilUnknown: field ? zarcSoilUnknown(field) : undefined,
  });
}

/** Only the known keys. The decision rule validates every number itself. */
export function sanitizeState(obj) {
  const out = {};
  if (!isObject(obj)) return out;
  for (const key of STATE_KEYS) if (Object.hasOwn(obj, key) && obj[key] !== undefined) out[key] = obj[key];
  return out;
}

/** The input of decision.mjs decideField, taken from the sheet state. */
export function decisionInput(state) {
  const s = isObject(state) ? state : {};
  return {
    numbers: own(s, 'numbers'),
    trials: own(s, 'trials'),
    constraints: Array.isArray(s.constraints) ? s.constraints : [],
    hardRules: Array.isArray(s.hardRules) ? s.hardRules : [],
    zarcWindow: own(s, 'zarcWindow'),
    windowReading: own(s, 'windowReading'),
    sowingDate: own(s, 'sowingDate') ?? null,
    zarcConstraint: own(s, 'zarcConstraint') === true,
    hlsOn: own(s, 'hlsOn') !== false,
  };
}

// ---------------------------------------------------------------- copy

/** The copy tree of one language from data/copy.json ({ langs: { pt, en } }) or a bare tree. */
export function pickCopy(json, lang = DEFAULT_SHEET_LANG) {
  const candidates = [own(own(json, 'langs'), lang), own(json, lang), json];
  for (const c of candidates) if (isObject(c) && isObject(c.sheet)) return c;
  throw new Error(`copy.json has no "${lang}" tree with a sheet block`);
}

/** Value at a dotted key, or undefined. */
export function lookup(copy, key) {
  let node = copy;
  for (const part of key.split('.')) {
    if (Array.isArray(node) && /^\d+$/.test(part)) node = node[Number(part)];
    else node = own(node, part);
    if (node === undefined) return undefined;
  }
  return node;
}

function text(copy, key) {
  const v = lookup(copy, key);
  if (typeof v !== 'string' || v === '') throw new Error(`copy has no text at ${key}`);
  return v;
}

function maybeText(copy, key) {
  const v = lookup(copy, key);
  return typeof v === 'string' && v !== '' ? v : null;
}

/**
 * Template to segments. Each placeholder takes a value { text, seal, missing } or a string.
 * A placeholder without a value prints the missing-value text.
 */
export function fill(template, vars, missingText) {
  const out = [];
  let last = 0;
  for (const m of template.matchAll(PLACEHOLDER)) {
    if (m.index > last) out.push({ text: template.slice(last, m.index) });
    const v = Object.hasOwn(vars, m[1]) ? vars[m[1]] : undefined;
    if (typeof v === 'string') out.push({ text: v });
    else if (isObject(v)) out.push({ ...v });
    else out.push({ text: missingText, seal: 'unknown', missing: true });
    last = m.index + m[0].length;
  }
  if (last < template.length) out.push({ text: template.slice(last) });
  return out;
}

/** "Label: rest" templates print the label apart. The split is taken before filling. */
function line(template, vars, missingText, extra = {}) {
  const i = template.indexOf(': ');
  const brace = template.indexOf('{');
  if (i > 0 && (brace === -1 || i < brace)) {
    return { label: template.slice(0, i), segments: fill(template.slice(i + 2), vars, missingText), ...extra };
  }
  return { label: null, segments: fill(template, vars, missingText), ...extra };
}

export function segmentsText(segments) {
  return segments.map((s) => s.text).join('');
}

// ---------------------------------------------------------------- numbers and dates

/** Decimal and thousands marks of the copy (meta.decimal_mark, meta.thousands_mark). */
export function formatNumber(v, copy, { max = 2, min = 0 } = {}) {
  if (typeof v !== 'number' || !Number.isFinite(v)) return null;
  const dec = text(copy, 'meta.decimal_mark');
  const thousands = text(copy, 'meta.thousands_mark');
  const [int, frac0 = ''] = Math.abs(v).toFixed(max).split('.');
  let frac = frac0.replace(/0+$/, '');
  while (frac.length < min) frac += '0';
  const zero = /^0*$/.test(int) && /^0*$/.test(frac);
  const sign = v < 0 && !zero ? '-' : '';
  return sign + int.replace(/\B(?=(\d{3})+(?!\d))/g, thousands) + (frac ? dec + frac : '');
}

/** Up to one decimal, rounded up. Rounding up never favours the switch (14.5). */
export function ceil1(x) {
  if (typeof x !== 'number' || !Number.isFinite(x)) return null;
  const r = Math.ceil(x * 10 - 1e-9) / 10;
  return Object.is(r, -0) ? 0 : r;
}

/** Sacks per alqueire from sacks per hectare: 1 alqueire = 2.42 ha. */
export function toAlqueire(scHa) {
  if (typeof scHa !== 'number' || !Number.isFinite(scHa)) return null;
  return scHa * ALQUEIRE_HA;
}

/**
 * G in sc/ha and sc/alqueire (SA-D91). sc/ha is the step the count uses, G rounded up to a whole
 * sc/ha (14.5). sc/alqueire is that step converted, rounded up to one decimal.
 */
export function gainLine(G) {
  const ha = D.shownG(G);
  if (ha === null) return null;
  return { ha, alq: ceil1(toAlqueire(ha)) };
}

export function isIsoDate(s) {
  if (typeof s !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(s)) return false;
  const [y, m, d] = s.split('-').map(Number);
  const t = new Date(Date.UTC(y, m - 1, d));
  return t.getUTCFullYear() === y && t.getUTCMonth() === m - 1 && t.getUTCDate() === d;
}

/**
 * An ISO date in the copy's sheet.date_format ({day}, {month} from sheet.months, {year}), or null.
 * Without a copy, dd/mm/aaaa.
 */
export function formatDate(iso, copy = null) {
  if (!isIsoDate(iso)) return null;
  const [y, m, d] = iso.split('-');
  const pattern = copy ? maybeText(copy, 'sheet.date_format') : null;
  const months = copy ? lookup(copy, 'sheet.months') : null;
  const month = Array.isArray(months) && typeof months[Number(m) - 1] === 'string' ? months[Number(m) - 1] : m;
  if (!pattern) return `${d}/${m}/${y}`;
  return pattern.replace('{day}', d).replace('{month}', month).replace('{year}', y);
}

function localIso(now) {
  const d = now instanceof Date && !Number.isNaN(now.getTime()) ? now : new Date();
  const p = (x) => String(x).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}

function count(v) {
  return Number.isSafeInteger(v) && v >= 0 ? v : null;
}

function areaOf(v) {
  const x = isObject(v) ? own(v, 'v') : v;
  return typeof x === 'number' && Number.isFinite(x) && x > 0 ? x : null;
}

// ---------------------------------------------------------------- H2 (M8.5)

// One rule with How we know: app.mjs localReviewDone (pass, fail or reported, with a literal
// comment). A missing scoreboard means no local review. Nothing in the fragment reaches it.
export { localReviewDone };

// ---------------------------------------------------------------- the sheet

/**
 * The printable sheet as data: { lang, title, fictitious, meta, items, closing, print }.
 * Items follow 7.12. Each line is { label, segments, kind }, each segment { text, seal, missing }.
 */
export function buildSheet(state, copy, { now = new Date(), scoreboard = null } = {}) {
  const s = sanitizeState(state);
  const missingText = text(copy, 'sheet.date_missing');
  const missing = () => ({ text: missingText, seal: 'unknown', missing: true });
  const value = (str, seal) => (str === null || str === undefined ? missing() : { text: str, seal });
  const num = (v, seal, opts) => value(formatNumber(v, copy, opts), seal);

  const input = decisionInput(s);
  const decided = D.decideField(input);
  const ctx = D.windowContext(input);
  const opt = D.OPTIONS.includes(s.option) ? s.option : decided.answer.main;
  const result = opt ? decided[opt] : decided.answer;
  const optionName = text(copy, `page.strategies.${opt ?? 'S1'}.name`);
  const evidence = opt ? own(own(s, 'trials'), opt) : undefined;

  // Header
  const fieldId = typeof s.field === 'string' && FIELD_ID.test(s.field) ? s.field.toUpperCase() : null;
  const meta = [
    fill(text(copy, 'sheet.field'), { X: value(fieldId, null) }, missingText),
    fill(text(copy, 'sheet.region'), {}, missingText),
    fill(text(copy, 'sheet.generated'), { date: formatDate(localIso(now), copy) }, missingText),
  ];

  const items = [];

  // 1. Option and state, with the reason when the option is out or the evidence is short.
  {
    const lines = [
      line(text(copy, 'sheet.items.option'), {
        option: { text: optionName, strong: true },
        state: { text: text(copy, `states.${result.state}`), state: result.state },
      }, missingText, { kind: 'main' }),
    ];
    for (const note of answerNotes(copy, result, opt, decided, count(own(evidence, 'n')), missingText)) lines.push(note);
    items.push({ key: 'option', lines });
  }

  // 2. Zarc window, declared date, satellite line, window flags and the Zarc constraint.
  {
    const w = own(own(s, 'zarcWindow'), 'corn');
    const start = formatDate(own(w, 'start'), copy);
    const end = formatDate(own(w, 'end'), copy);
    // Without a Zarc soil class no window can be matched: the sheet says so, never a bare "missing".
    const windowValue =
      start && end && own(w, 'start') <= own(w, 'end')
        ? { text: windowRange(copy, start, end), seal: 'official_rule' }
        : s.zarcSoilUnknown === true
          ? { text: text(copy, 'sheet.window_soil_unknown'), seal: 'unknown' }
          : missing();
    const sowing = isIsoDate(s.sowingDate) ? { text: formatDate(s.sowingDate, copy), seal: 'your_numbers' } : missing();
    const wr = own(s, 'windowReading');
    const k = count(own(wr, 'k'));
    const n = count(own(wr, 'n'));
    const kn = k !== null && n !== null && n > 0 && k <= n;
    const lines = [
      line(text(copy, 'sheet.items.window'), { window: windowValue, sowing_date: sowing }, missingText, { kind: 'main' }),
      line(text(copy, 'sheet.items.satellite'), {
        // k of n is one value: one seal, at the end of the line.
        k: kn ? { text: formatNumber(k, copy), value: true } : missing(),
        n: kn ? { text: formatNumber(n, copy), seal: 'estimated_from_satellite', sealAtEnd: true } : missing(),
      }, missingText, { kind: 'main' }),
    ];
    for (const f of WINDOW_FLAGS) {
      if (ctx.flags.includes(f)) lines.push({ label: null, segments: [{ text: text(copy, `flags.${f}`) }], kind: 'note' });
    }
    if (typeof s.zarcConstraint === 'boolean') {
      lines.push(limitAnswer(copy, 'zarc_insurance_credit', s.zarcConstraint ? 'answer_yes' : 'answer_no'));
    }
    items.push({ key: 'window', lines });
  }

  // 3. Assumptions: the user's prices and costs, G in sc/ha and sc/alqueire, rented land.
  {
    const lines = [line(text(copy, 'sheet.items.assumptions'), {}, missingText, { kind: 'main' })];
    const numbers = own(s, 'numbers');
    const rows = (opt ? D.ESSENTIAL[opt] : ALL_INPUTS).map((f) => {
      const v = own(numbers, f);
      const ok = D.isValidValue(f, v);
      return {
        label: text(copy, `page.inputs.${f}.label`),
        segments: ok
          ? [{ text: `${formatNumber(v, copy)} ${text(copy, `page.inputs.${f}.unit`)}`, seal: 'your_numbers' }]
          : [missing()],
        kind: 'input',
        field: f,
      };
    });
    lines.push(...rows);
    const g = opt ? gainLine(result.G) : null;
    lines.push(line(text(copy, 'sheet.g_line'), {
      // One G in two units: one seal, at the end of the line. G uses only the user's numbers (M5.4).
      G_ha: g ? { text: formatNumber(g.ha, copy), strong: true } : missing(),
      G_alq: g ? { text: formatNumber(g.alq, copy, { max: 1, min: 1 }), seal: 'your_numbers', sealAtEnd: true, strong: true } : missing(),
    }, missingText, { kind: 'gain' }));
    if (s.rentedLand === 'own' || s.rentedLand === 'rented') {
      lines.push(limitAnswer(copy, 'rented_land', s.rentedLand));
    }
    items.push({ key: 'assumptions', lines });
  }

  // 4. Evidence: N of n site-seasons, a count, not a chance.
  {
    const N = count(result.N);
    // n only from the evidence the page sent: decision.mjs counts absent evidence as 0.
    const n = count(own(evidence, 'n'));
    const sites = count(own(evidence, 'n_sites'));
    const lines = [
      line(text(copy, 'sheet.items.evidence'), {
        // N of n is one count: one seal, at the end of the line.
        N: N !== null ? { text: formatNumber(N, copy), value: true } : missing(),
        n: n !== null ? { text: formatNumber(n, copy), seal: 'derived_from_trials', sealAtEnd: true } : missing(),
        sites: sites !== null ? { text: formatNumber(sites, copy), seal: 'derived_from_trials' } : missing(),
      }, missingText, { kind: 'main' }),
    ];
    items.push({ key: 'evidence', lines });
  }

  // 5. What the tool does not know.
  items.push({ key: 'unknowns', lines: [line(text(copy, 'sheet.items.unknowns'), {}, missingText, { kind: 'main' })] });

  // 6. Local review: the option's alerts, soil test, terraces. Blast and harvest rain unaudited.
  {
    const ids = alertIds(copy, OPTION_ALERTS[opt ?? 'S1']);
    const alerts = ids.map((id) => text(copy, `sheet.alerts.${id}`)).join(', ');
    items.push({
      key: 'local_review',
      lines: [
        line(text(copy, 'sheet.items.local_review'), { alerts: { text: alerts } }, missingText, { kind: 'main', alerts: ids }),
        { label: null, segments: [{ text: text(copy, 'sheet.unaudited') }], kind: 'note', key: 'unaudited' },
      ],
    });
  }

  // 7. Paired, repeated strips. The design is the agronomist's.
  {
    const area = areaOf(s.area_ha);
    items.push({
      key: 'strip_test',
      lines: [
        line(text(copy, 'sheet.items.strip_test'), {
          area: area !== null ? { text: areaText(copy, formatNumber(area, copy, { max: 1 })) } : missing(),
        }, missingText, { kind: 'main' }),
        { label: null, segments: [{ text: text(copy, 'sheet.single_strip') }], kind: 'emphasis', key: 'single_strip' },
        { label: null, segments: [{ text: text(copy, 'sheet.strip_design') }], kind: 'note', key: 'strip_design' },
      ],
      writeIn: true,
    });
  }

  const closing = [];
  if (!localReviewDone(scoreboard)) closing.push({ key: 'no_local_review', text: text(copy, 'sheet.no_local_review') });
  closing.push({ key: 'independence', text: text(copy, 'sheet.independence') });

  return {
    lang: text(copy, 'meta.lang'),
    title: text(copy, 'sheet.title'),
    fictitious: text(copy, 'sheet.fictitious'),
    meta,
    items: items.map((it, i) => ({ n: i + 1, ...it })),
    closing,
    print: text(copy, 'sheet.print'),
    switchTo: text(copy, 'meta.switch_to'),
    switchAria: text(copy, 'meta.switch_aria'),
    seals: sealNames(copy),
    option: opt,
    state: result.state,
    code: result.code,
  };
}

/** Why the option is out, or what is missing. Nothing for keep, tied and try (items 3 and 4 say it). */
function answerNotes(copy, result, opt, decided, nEvidence, missingText) {
  const note = (segments) => ({ label: null, segments, kind: 'note' });
  if (result.code === 'removed' || result.code === 'both_removed') {
    const out = [];
    if (result.code === 'both_removed') out.push(note([{ text: text(copy, 'answers.both_removed') }]));
    for (const o of opt ? [opt] : D.OPTIONS) {
      const reasons = Array.isArray(decided[o]?.reasons) ? decided[o].reasons : [];
      for (const r of reasons) {
        const reason = r && typeof r.id === 'string' ? maybeText(copy, `page.limits.reasons.${r.id}`) : null;
        out.push(note(fill(text(copy, 'answers.removed'), {
          Option: text(copy, `page.strategies.${o}.name`),
          reason: reason ?? { text: missingText, seal: 'unknown', missing: true },
        }, missingText)));
      }
    }
    return out;
  }
  if (result.state !== 'not_enough') return [];
  const template = maybeText(copy, `answers.${result.code}`);
  if (!template) return [];
  const g = D.shownG(result.G);
  return [note(fill(template, {
    Option: text(copy, `page.strategies.${opt ?? 'S1'}.name`),
    field: result.field ? maybeText(copy, `fields.${result.field}`) ?? missingText : missingText,
    G: g === null ? undefined : formatNumber(g, copy),
    n: nEvidence === null ? undefined : formatNumber(nEvidence, copy),
  }, missingText))];
}

function limitAnswer(copy, id, answerKey) {
  const answer = answerKey.startsWith('answer_') ? text(copy, `page.limits.${answerKey}`) : text(copy, `page.limits.${id}.${answerKey}`);
  return { label: text(copy, `page.limits.${id}.question`), segments: [{ text: answer, seal: 'your_numbers' }], kind: 'limit', sep: ' ' };
}

/** Alert ids of the option that have copy, in copy order (13.5). */
function alertIds(copy, ids) {
  const known = Object.keys(lookup(copy, 'sheet.alerts') ?? {});
  return known.filter((id) => ids.includes(id));
}

/** Copy key sheet.window_range ("{start} a {end}"). */
function windowRange(copy, start, end) {
  return text(copy, 'sheet.window_range').replace('{start}', start).replace('{end}', end);
}

/** Copy key sheet.area ("{area} ha"). */
function areaText(copy, area) {
  return text(copy, 'sheet.area').replace('{area}', area);
}

function sealNames(copy) {
  const names = lookup(copy, 'seals.names');
  return isObject(names) ? { ...names } : {};
}

/** Plain text of the sheet, in print order. With seals: true, each seal follows its value in brackets. */
export function sheetText(model, { seals = false } = {}) {
  const tag = (x) => ` [${model.seals[x.seal] ?? x.seal}]`;
  const seg = (segments) =>
    segments.map((x) => (seals && x.seal && !x.sealAtEnd ? x.text + tag(x) : x.text)).join('') +
    (seals ? segments.filter((x) => x.seal && x.sealAtEnd).map(tag).join('') : '');
  const out = [`${model.title}   ${model.fictitious}`, model.meta.map(seg).join('   ')];
  for (const item of model.items) {
    item.lines.forEach((l, i) => {
      const body = l.label ? `${l.label}${l.sep ?? ': '}${seg(l.segments)}` : seg(l.segments);
      out.push(i === 0 ? `${item.n} ${body}` : `  ${body}`);
    });
  }
  for (const c of model.closing) out.push(c.text);
  return out.join('\n');
}

// ---------------------------------------------------------------- DOM (sheet.html only)

const SVG_NS = 'http://www.w3.org/2000/svg';

// Line icons of 7.14, 12 by 12, stroke only. Drawn here, no external file.
const SEAL_ICONS = Object.freeze({
  your_numbers: ['M2 10l1.6-.4 6-6-1.2-1.2-6 6z', 'M7.6 3.2l1.2 1.2'],
  official_rule: ['M6 1.8v8.4', 'M3.4 10.2h5.2', 'M2 3.4h8', 'M3 3.4 1.6 6.8h2.8z', 'M9 3.4 7.6 6.8h2.8z'],
  estimated_from_satellite: ['M2.4 4.4l2-2 2 2-2 2z', 'M5.4 5.4l1.4 1.4', 'M1.2 1.2l1.2 1.2', 'M6.4 10.2c.8-.8 1.4.8 2.2 0s1.4.8 2.2 0'],
  seen_by_satellite: ['M2.4 4.4l2-2 2 2-2 2z', 'M5.4 5.4l1.4 1.4', 'M1.2 1.2l1.2 1.2', 'M7.4 9.6l2.2-2.2'],
  measured_in_trials: ['M1.6 1.6h8.8v8.8H1.6z', 'M6 1.6v8.8', 'M1.6 6h8.8'],
  derived_from_trials: ['M1.6 1.6h8.8v8.8H1.6z', 'M6 3.6v4.8', 'M3.6 6h4.8'],
  reanalysis: ['M1.6 1.6h8.8v8.8H1.6z', 'M4.5 1.6v8.8', 'M7.5 1.6v8.8', 'M1.6 4.5h8.8', 'M1.6 7.5h8.8'],
  simulated: ['M6 3.8a2.2 2.2 0 1 0 0 4.4 2.2 2.2 0 0 0 0-4.4z', 'M6 1v1.6M6 9.4V11M1 6h1.6M9.4 6H11M2.5 2.5l1.1 1.1M8.4 8.4l1.1 1.1M2.5 9.5l1.1-1.1M8.4 3.6l1.1-1.1'],
  published_climatology: ['M2 2.2h3.4c.6 0 1 .4 1 1v7c0-.6-.4-1-1-1H2z', 'M10 2.2H7.4c-.6 0-1 .4-1 1v7c0-.6.4-1 1-1H10z'],
  unknown: ['M6 10.8a4.8 4.8 0 1 0 0-9.6 4.8 4.8 0 0 0 0 9.6z', 'M4.6 4.7a1.5 1.5 0 1 1 2.1 1.4c-.5.2-.7.6-.7 1.1', 'M6 8.6v.2'],
});

function el(doc, tag, cls, txt) {
  const e = doc.createElement(tag);
  if (cls) e.className = cls;
  if (txt !== undefined && txt !== null) e.textContent = txt;
  return e;
}

function sealEl(doc, id, names) {
  const span = el(doc, 'span', `seal seal--${id}`);
  const svg = doc.createElementNS(SVG_NS, 'svg');
  svg.setAttribute('viewBox', '0 0 12 12');
  svg.setAttribute('aria-hidden', 'true');
  svg.setAttribute('class', 'seal__icon');
  for (const d of SEAL_ICONS[id] ?? SEAL_ICONS.unknown) {
    const p = doc.createElementNS(SVG_NS, 'path');
    p.setAttribute('d', d);
    svg.appendChild(p);
  }
  span.appendChild(svg);
  span.appendChild(el(doc, 'span', 'seal__text', names[id] ?? ''));
  return span;
}

function segmentsEl(doc, segments, names) {
  const frag = doc.createDocumentFragment();
  const atEnd = [];
  for (const s of segments) {
    if (!s.seal && !s.missing && !s.state && !s.strong && !s.value) {
      frag.appendChild(doc.createTextNode(s.text));
      continue;
    }
    const cls = ['value'];
    if (s.missing) cls.push('value--missing');
    if (s.strong) cls.push('value--strong');
    if (s.state) cls.push('state', `state--${s.state}`);
    const v = el(doc, s.strong || s.state ? 'strong' : 'span', cls.join(' '), s.text);
    frag.appendChild(v);
    if (s.seal && s.sealAtEnd) atEnd.push(s.seal);
    else if (s.seal) frag.appendChild(sealEl(doc, s.seal, names));
  }
  for (const id of atEnd) frag.appendChild(sealEl(doc, id, names));
  return frag;
}

/** Draws the model into `root`. Text goes through textContent only, never as HTML. */
export function renderSheet(model, doc, root) {
  root.replaceChildren();
  doc.documentElement.lang = model.lang;
  doc.title = model.title;

  const head = el(doc, 'header', 'sheet-head');
  const top = el(doc, 'div', 'sheet-head__top');
  top.appendChild(el(doc, 'h1', 'sheet-title', model.title));
  top.appendChild(el(doc, 'p', 'stamp', model.fictitious));
  head.appendChild(top);
  const meta = el(doc, 'p', 'sheet-meta');
  for (const m of model.meta) meta.appendChild(el(doc, 'span', 'sheet-meta__item')).appendChild(segmentsEl(doc, m, model.seals));
  head.appendChild(meta);
  root.appendChild(head);

  const list = el(doc, 'ol', 'items');
  for (const item of model.items) {
    const li = el(doc, 'li', `item item--${item.key}`);
    li.dataset.item = item.key;
    li.appendChild(el(doc, 'span', 'item__n', String(item.n)));
    const body = el(doc, 'div', 'item__body');
    let table = null;
    for (const l of item.lines) {
      if (l.kind === 'input') {
        if (!table) {
          table = el(doc, 'dl', 'inputs');
          body.appendChild(table);
        }
        const row = el(doc, 'div', 'inputs__row');
        row.dataset.field = l.field;
        row.appendChild(el(doc, 'dt', 'inputs__label', l.label));
        row.appendChild(el(doc, 'dd', 'inputs__value')).appendChild(segmentsEl(doc, l.segments, model.seals));
        table.appendChild(row);
        continue;
      }
      table = null;
      const p = el(doc, 'p', `line line--${l.kind}`);
      if (l.key) p.dataset.line = l.key;
      if (l.label) {
        p.appendChild(el(doc, 'span', 'line__label', l.label));
        p.appendChild(doc.createTextNode(l.sep ?? ': '));
      }
      p.appendChild(segmentsEl(doc, l.segments, model.seals));
      body.appendChild(p);
    }
    if (item.writeIn) {
      const box = el(doc, 'div', 'write-in');
      box.setAttribute('aria-hidden', 'true');
      for (let i = 0; i < 4; i++) box.appendChild(el(doc, 'span', 'write-in__rule'));
      body.appendChild(box);
    }
    li.appendChild(body);
    list.appendChild(li);
  }
  root.appendChild(list);

  const foot = el(doc, 'footer', 'sheet-foot');
  for (const c of model.closing) {
    const p = el(doc, 'p', `closing closing--${c.key}`, c.text);
    p.dataset.line = c.key;
    foot.appendChild(p);
  }
  root.appendChild(foot);
}

/** Parsed JSON, or null when the file is missing or broken (app.mjs loadAll treats null as absent). */
async function fetchOrNull(win, url) {
  try {
    const res = await win.fetch(url, { credentials: 'omit', cache: 'no-cache' });
    return res.ok ? await res.json() : null;
  } catch {
    return null;
  }
}

/** Entry point of sheet.html. Reads the fragment, draws the sheet, redraws on hashchange. */
export async function mount({ win = globalThis.window, doc = win?.document } = {}) {
  const root = doc.getElementById('sheet');
  const button = doc.getElementById('print');
  const langLink = doc.getElementById('lang');
  // Same files and folder as the page (data/, or ?data= for the fictitious fixtures).
  const data = await loadAll((url) => fetchOrNull(win, url), dataBase(win.location.search));
  const draw = () => {
    const { params, error } = parseFragment(win.location.hash);
    if (error) console.warn(`sheet: fragment ${error}, every value prints as missing`);
    const lang = sheetLang(win.location.search, params.lang);
    let copy;
    try {
      copy = pickCopy(data.copy, lang);
    } catch (err) {
      doc.documentElement.dataset.error = 'copy';
      console.error('sheet: cannot load the copy', err);
      return;
    }
    // Without the scoreboard, H2 counts as not done (M8.5).
    const model = buildSheet(resolveState(params, data), copy, { now: new Date(), scoreboard: data.scoreboard });
    renderSheet(model, doc, root);
    if (button) {
      button.textContent = model.print;
      button.hidden = false;
    }
    if (langLink) {
      // The same sheet in the other language: same fragment, same data folder.
      langLink.textContent = model.switchTo;
      langLink.setAttribute('aria-label', model.switchAria);
      langLink.setAttribute('lang', lang === 'pt' ? 'en' : 'pt-BR');
      langLink.setAttribute('href', `${otherLangSearch(win.location.search, lang)}${win.location.hash}`);
      langLink.hidden = false;
    }
    doc.documentElement.dataset.lang = lang;
    doc.documentElement.dataset.ready = 'true';
  };
  draw();
  win.addEventListener('hashchange', draw);
  if (button) button.addEventListener('click', () => win.print());
}

// Written with an SI tool (Claude, Anthropic) for NASA Space Apps 2026. See AI_USE.md.
// Aftercrop public decision rule (plan sections 13.7, 14 and sheet M5).
//
// Pure functions only: no DOM, no network, no clock, no randomness. The same file runs
// in the browser and in Node 22 for the batch. Every option is compared with S1 (keep corn)
// on the same field. No engine output enters the money (M5.4): G uses only the farmer's numbers.
//
// States: "keep", "try_strip", "tied", "not_enough", "removed".

export const STATES = Object.freeze(['keep', 'try_strip', 'tied', 'not_enough', 'removed']);

// Order used by the main sentence (section 7.15). Equal states: S3 first.
export const PRECEDENCE = Object.freeze(['try_strip', 'tied', 'keep', 'not_enough', 'removed']);

export const OPTIONS = Object.freeze(['S2', 'S3']);

// Project rule (13.7). Ratios are compared as integers, so N/n = 0.7 and 0.3 are exact.
export const MIN_SITE_SEASONS = 5;
export const TRY_RATIO = Object.freeze({ num: 7, den: 10 });
export const KEEP_RATIO = Object.freeze({ num: 3, den: 10 });

// Domain of each input (14.2). Prices and yields > 0, costs >= 0.
// q no longer enters the money (P0.2): p_wheat is already net of the quality discount.
// If a q is sent, it must still lie in [0, 1], and it is never applied.
export const FIELD_KIND = Object.freeze({
  p_soy: 'price',
  p_corn: 'price',
  p_wheat: 'price',
  Y_corn: 'yield',
  Y_wheat: 'yield',
  c_corn: 'cost',
  c_wheat: 'cost',
  c_grass: 'cost',
  q: 'fraction',
});

// Essential fields per option, in the order of table 14.2. The first invalid one is reported.
export const ESSENTIAL = Object.freeze({
  S2: Object.freeze(['p_soy', 'c_grass']),
  S3: Object.freeze(['p_soy', 'p_corn', 'p_wheat', 'Y_corn', 'Y_wheat', 'c_corn', 'c_wheat']),
});
const OPTIONAL = Object.freeze({ S2: Object.freeze([]), S3: Object.freeze(['q']) });
const CORN_FIELDS = Object.freeze(['p_corn', 'Y_corn', 'c_corn']);

// Default words for {field}. Draft only: the screen copy (copy_en.yaml) may pass its own labels.
export const FIELD_LABELS = Object.freeze({
  p_soy: 'soybean price',
  p_corn: 'corn price',
  p_wheat: 'net wheat price',
  Y_corn: 'typical corn yield',
  Y_wheat: 'typical wheat yield',
  c_corn: 'corn cost',
  c_wheat: 'wheat cost',
  c_grass: 'grass cost',
  q: 'wheat quality factor',
  sowing_date: 'sowing date',
});

// Fixed sentences of section 7.15 that this module emits itself.
export const MESSAGES = Object.freeze({
  needSowingDate: 'Not enough evidence. We need your sowing date.',
  windowOutside:
    'Not enough evidence. Your corn sowing date is outside the Zarc window, so this page will not say keep what you do.',
});

// Reason id when the Zarc constraint removes S2 (rule 6 of 13.7). The text comes from region.json.
export const ZARC_CONSORTIUM_REASON = 'zarc_consortium_window';

// Priority only highlights a row (7.4). It never reaches the state (M5.3).
const PRIORITY_ROW = Object.freeze({ income: 'L4', water: 'L2', soil: 'L1' });

function isObject(v) {
  return typeof v === 'object' && v !== null && !Array.isArray(v);
}

function own(obj, key) {
  return isObject(obj) && Object.hasOwn(obj, key) ? obj[key] : undefined;
}

// ---------------------------------------------------------------- inputs (14.2, M5.9)

/** True when `value` is inside the domain of `field`. Only finite numbers count: no coercion. */
export function isValidValue(field, value) {
  if (typeof value !== 'number' || !Number.isFinite(value)) return false;
  switch (FIELD_KIND[field]) {
    case 'price':
    case 'yield':
      return value > 0;
    case 'cost':
      return value >= 0;
    case 'fraction':
      return value >= 0 && value <= 1;
    default:
      return false;
  }
}

/** First essential field of the option that is missing, not numeric or out of domain, else null. */
export function invalidField(opt, numbers) {
  const fields = ESSENTIAL[opt];
  if (!fields) throw new RangeError(`Unknown option: ${opt}`);
  for (const f of fields) if (!isValidValue(f, own(numbers, f))) return f;
  for (const f of OPTIONAL[opt]) {
    const v = own(numbers, f);
    if (v !== undefined && v !== null && !isValidValue(f, v)) return f;
  }
  return null;
}

/** "Not enough evidence. We need your {field}." */
export function needFieldMessage(field, labels = undefined) {
  const custom = own(labels, field);
  const label = typeof custom === 'string' && custom !== '' ? custom : FIELD_LABELS[field] ?? field;
  return `Not enough evidence. We need your ${label}.`;
}

// ---------------------------------------------------------------- money (14.1)
// These assume validated inputs. decideOption validates first.

/** M_corn = p_corn × Y_corn − c_corn, R$/ha. */
export function marginCorn(n) {
  return n.p_corn * n.Y_corn - n.c_corn;
}

/** M_wheat = p_wheat × Y_wheat − c_wheat, R$/ha, with p_wheat net of the quality discount. */
export function marginWheat(n) {
  return n.p_wheat * n.Y_wheat - n.c_wheat;
}

/** G_3 = (M_corn − M_wheat) / p_soy, sc/ha. */
export function computeG3(n) {
  return (marginCorn(n) - marginWheat(n)) / n.p_soy;
}

/** G_2 = c_grass / p_soy, sc/ha. */
export function computeG2(n) {
  return n.c_grass / n.p_soy;
}

// ---------------------------------------------------------------- trial steps (14.5)

/** Step of 1 sc/ha, G rounded up. Rounding up never favours the switch. */
export function stepOf(G) {
  return Math.ceil(G);
}

/**
 * G as the page and the sheet show it (SA-D91): the step the count uses, a whole number of
 * sc/ha, so the sentence and the count read the same G. -0 shows as 0. Null when G is not finite.
 */
export function shownG(G) {
  if (typeof G !== 'number' || !Number.isFinite(G)) return null;
  const s = stepOf(G);
  return Object.is(s, -0) ? 0 : s;
}

/** Valid trial count n (non-negative integer), 0 when no evidence was counted, null when unusable. */
export function trialCount(evidence) {
  if (evidence === undefined || evidence === null) return 0;
  const n = own(evidence, 'n');
  return Number.isSafeInteger(n) && n >= 0 ? n : null;
}

/** Sorted steps [{g, count}], or null when the table is not a valid non-increasing step table. */
export function parseSteps(evidence, n) {
  const raw = own(evidence, 'steps') ?? [];
  if (!Array.isArray(raw)) return null;
  const steps = [];
  for (const s of raw) {
    const g = own(s, 'g_sc_ha');
    const count = own(s, 'count');
    if (!Number.isSafeInteger(g) || !Number.isSafeInteger(count) || count < 0 || count > n) return null;
    steps.push({ g, count });
  }
  steps.sort((a, b) => a.g - b.g);
  for (let i = 1; i < steps.length; i++) {
    if (steps[i].g === steps[i - 1].g) return null;
    if (steps[i].count > steps[i - 1].count) return null;
  }
  return steps;
}

/**
 * N at step k: site-seasons with gain >= k. Exact when k is listed, or when the neighbours
 * pin it (equal counts around a gap, n below the first step, 0 above the last). Else null.
 */
export function countAtStep(steps, n, k) {
  let lo = null;
  let hi = null;
  for (const s of steps) {
    if (s.g === k) return s.count;
    if (s.g < k) lo = s;
    else if (hi === null) hi = s;
  }
  if (lo && hi) return lo.count === hi.count ? lo.count : null;
  if (hi) return hi.count === n ? n : null;
  if (lo) return lo.count === 0 ? 0 : null;
  return null;
}

// ---------------------------------------------------------------- rule (13.7, items 3 and 4)

/**
 * State from G and the trial steps of one option, in the order of 13.7:
 * G <= 0 try_strip, n < 5 not_enough, N/n >= 0.7 try_strip, N/n <= 0.3 keep, else tied.
 */
export function ruleState(G, evidence) {
  const base = { G: null, step: null, N: null, n: null };
  if (typeof G !== 'number' || !Number.isFinite(G)) {
    return { ...base, state: 'not_enough', code: 'out_of_range' };
  }
  if (G <= 0) return { ...base, G, state: 'try_strip', code: 'pays_without_gain' };
  const step = stepOf(G);
  const n = trialCount(evidence);
  if (n === null) return { ...base, G, step, state: 'not_enough', code: 'evidence_unusable' };
  if (n < MIN_SITE_SEASONS) return { ...base, G, step, n, state: 'not_enough', code: 'few_trials' };
  const steps = parseSteps(evidence, n);
  const N = steps === null ? null : countAtStep(steps, n, step);
  if (N === null) return { ...base, G, step, n, state: 'not_enough', code: 'evidence_unusable' };
  const out = { G, step, N, n };
  if (TRY_RATIO.den * N >= TRY_RATIO.num * n) return { ...out, state: 'try_strip', code: 'counts_try' };
  if (KEEP_RATIO.den * N <= KEEP_RATIO.num * n) return { ...out, state: 'keep', code: 'counts_keep' };
  return { ...out, state: 'tied', code: 'counts_tied' };
}

// ---------------------------------------------------------------- removal (13.7, item 1)

function removes(entry, opt) {
  const r = own(entry, 'removes');
  return Array.isArray(r) ? r.includes(opt) : r === opt;
}

function reasonOf(entry) {
  const id = own(entry, 'id');
  const why = own(entry, 'why');
  return { id: typeof id === 'string' ? id : null, why: typeof why === 'string' ? why : null };
}

/**
 * Hard rules first, then the user's constraints. Each entry is { id, removes, why }, where
 * `removes` is an option id or a list of them. Returns the reasons, or null.
 */
export function removeOption(opt, constraints, hardRules) {
  const reasons = [];
  for (const list of [hardRules, constraints]) {
    if (!Array.isArray(list)) continue;
    for (const entry of list) if (isObject(entry) && removes(entry, opt)) reasons.push(reasonOf(entry));
  }
  return reasons.length ? reasons : null;
}

// ---------------------------------------------------------------- S2 establishment methods (protocol 11)

// Codes of a state read from the trial counts. Only these need the per-method counts.
const COUNT_CODES = Object.freeze(['counts_try', 'counts_keep', 'counts_tied']);

/**
 * Count per establishment method of the S2 trials: [{ method, n, N }], or null when the
 * evidence cannot show it. `evidence.by_method` is a list of { method, n, steps }: every
 * method a non-empty id, the n adding up to the evidence n. N is the count at `step` for that
 * method (null when the step table does not pin it). Without it S2 gives Not enough evidence.
 */
export function methodCounts(evidence, step = null) {
  const n = trialCount(evidence);
  const list = own(evidence, 'by_method');
  if (n === null || !Array.isArray(list) || list.length === 0) return null;
  const out = [];
  let total = 0;
  const seen = new Set();
  for (const m of list) {
    const method = own(m, 'method');
    const mn = trialCount(m);
    if (typeof method !== 'string' || !method.trim() || seen.has(method) || mn === null || own(m, 'n') === undefined) return null;
    seen.add(method);
    total += mn;
    const steps = parseSteps(m, mn);
    if (steps === null) return null;
    const N = Number.isSafeInteger(step) && step > 0 ? countAtStep(steps, mn, step) : null;
    out.push({ method, n: mn, N });
  }
  return total === n ? out : null;
}

// ---------------------------------------------------------------- corn window (13.7, item 6; 12.3)

export const WINDOW_BASIS = Object.freeze({ declared: 'declared_class', any: 'for_any_soil_class' });
export const SOIL_CLASSES = Object.freeze(['unknown', 'AD1', 'AD2', 'AD3', 'AD4', 'AD5', 'AD6']);

/**
 * Window reading of one strategy ("S1" corn, "S2" corn with grass) for a Zarc soil class, from
 * field_<id>.json window_basis (SA-D64). Same count as pipeline.decision.calendar
 * reading_from_basis: a season is "outside" when its status is ok and its low date falls after
 * the end of the window in that season. "unknown" uses the envelope ("for_any_soil_class").
 * No window: "no_window". Returns null when the basis cannot be read. Dates only, no dekads.
 */
export function windowReadingFor(basis, sid, soilClass = 'unknown') {
  const cls = SOIL_CLASSES.includes(soilClass) ? soilClass : null;
  const b = own(basis, sid);
  const seasons = own(basis, 'seasons');
  const minK = own(basis, 'min_seasons');
  if (cls === null || !isObject(b) || !Array.isArray(seasons) || !Number.isSafeInteger(minK)) return null;
  const tag = cls === 'unknown' ? WINDOW_BASIS.any : WINDOW_BASIS.declared;
  const w = cls === 'unknown' ? own(b, 'any') : own(own(b, 'by_class'), cls);
  if (!isObject(w)) return { state: 'no_window', k: null, n: null, zarc_window_end: null, basis: tag, soil_class: cls };
  const ends = own(w, 'end_by_season');
  let k = 0;
  let n = 0;
  for (const g of seasons) {
    if (own(g, 'status') !== 'ok') continue;
    const low = own(g, 'low');
    const end = own(ends, own(g, 'season'));
    if (!isIsoDate(low) || !isIsoDate(end)) return null;
    n += 1;
    if (low > end) k += 1;
  }
  const end = own(w, 'end');
  return { state: k >= minK ? 'outside' : 'at_the_edge', k, n, zarc_window_end: isIsoDate(end) ? end : null, basis: tag, soil_class: cls };
}

/**
 * "Ended before the corn Zarc window closed" (SA-D72.3): ok seasons whose `high` is on or before
 * the last day of the window, zero delay. A declared class: the window of that class, as
 * windowReadingFor. "unknown": the earliest end across every class and cycle group (`earliest`,
 * SA-D84), the conservative side for "before". A count, never a sowing date and never "inside".
 * Same result as calendar.ended_before_from_basis. Null when the basis cannot be read.
 */
export function endedBeforeFor(basis, sid, soilClass = 'unknown') {
  const cls = SOIL_CLASSES.includes(soilClass) ? soilClass : null;
  const b = own(basis, sid);
  const seasons = own(basis, 'seasons');
  if (cls === null || !isObject(b) || !Array.isArray(seasons)) return null;
  const tag = cls === 'unknown' ? WINDOW_BASIS.any : WINDOW_BASIS.declared;
  const w = cls === 'unknown' ? own(b, 'earliest') : own(own(b, 'by_class'), cls);
  if (!isObject(w)) return { state: 'no_window', k: null, n: null, basis: tag, soil_class: cls };
  const ends = own(w, 'end_by_season');
  let k = 0;
  let n = 0;
  for (const g of seasons) {
    if (own(g, 'status') !== 'ok') continue;
    const high = own(g, 'high');
    const end = own(ends, own(g, 'season'));
    if (!isIsoDate(high) || !isIsoDate(end)) return null;
    n += 1;
    if (high <= end) k += 1;
  }
  return { state: 'ok', k, n, basis: tag, soil_class: cls };
}

function riskOrNull(v) {
  if (v === null) return null;
  return Number.isFinite(v) && v > 0 ? v : undefined;
}

/**
 * Zarc risk of the ten-day period that holds `high`, per ok season (SA-D72.2), from the risk band
 * of window_basis. Declared class: groups [{risk, k}], risk null outside the zoning (last). Unknown:
 * groups [{risk_min, risk_max, k}] across every class and cycle group, and some_outside counts the
 * seasons whose period is outside the zoning for at least one of them. Same result as
 * calendar.risk_at_exit_from_basis. A condition of the field being free, never a sowing date.
 */
export function riskAtExitFor(basis, sid, soilClass = 'unknown') {
  const cls = SOIL_CLASSES.includes(soilClass) ? soilClass : null;
  const b = own(basis, sid);
  const seasons = own(basis, 'seasons');
  if (cls === null || !isObject(b) || !Array.isArray(seasons)) return null;
  const band = isObject(own(b, 'risk_band')) ? own(b, 'risk_band') : {};
  const any = cls === 'unknown';
  const tag = any ? WINDOW_BASIS.any : WINDOW_BASIS.declared;
  const lo = own(band, 'min');
  const hi = own(band, 'max');
  const part = own(band, 'some_outside');
  const risks = any ? null : own(own(own(band, 'by_class'), cls), 'risk');
  const arrays = any ? [lo, hi, part] : [risks];
  const zoned = arrays.every((a) => Array.isArray(a) && a.length === 36) && (any ? hi : risks).some((v) => v !== null);
  if (!zoned) return { state: 'no_window', n: null, groups: [], some_outside: null, basis: tag, soil_class: cls };
  const counts = new Map();
  let n = 0;
  let partial = 0;
  for (const g of seasons) {
    if (own(g, 'status') !== 'ok') continue;
    const i = own(g, 'high_dekad');
    if (!Number.isSafeInteger(i) || i < 1 || i > 36) return null;
    const key = any ? [riskOrNull(lo[i - 1]), riskOrNull(hi[i - 1])] : [riskOrNull(risks[i - 1])];
    if (key.includes(undefined)) return null;
    const id = key.join('|');
    const c = counts.get(id) ?? { key, k: 0 };
    c.k += 1;
    counts.set(id, c);
    n += 1;
    if (any && part[i - 1] === true) partial += 1;
  }
  const rank = (k) => [k[0] === null ? 1 : 0, k[0] ?? 0, k.at(-1) ?? 0];
  const list = [...counts.values()].sort((a, b) => {
    const x = rank(a.key);
    const y = rank(b.key);
    return x[0] - y[0] || x[1] - y[1] || x[2] - y[2];
  });
  const groups = list.map((c) => (any ? { risk_min: c.key[0], risk_max: c.key[1], k: c.k } : { risk: c.key[0], k: c.k }));
  return { state: 'ok', n, groups, some_outside: any ? partial : null, basis: tag, soil_class: cls };
}


function isIsoDate(s) {
  if (typeof s !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(s)) return false;
  const [y, m, d] = s.split('-').map(Number);
  const t = new Date(Date.UTC(y, m - 1, d));
  return t.getUTCFullYear() === y && t.getUTCMonth() === m - 1 && t.getUTCDate() === d;
}

/**
 * Declared date against a Zarc window { start, end } in ISO dates, inclusive. No dekad logic here:
 * dekads live in pipeline/common/dekad.py only. Returns "none", "invalid", "window_unknown",
 * "inside" or "outside".
 */
export function sowingDateStatus(date, window) {
  if (date === undefined || date === null || date === '') return 'none';
  if (!isIsoDate(date)) return 'invalid';
  const start = own(window, 'start');
  const end = own(window, 'end');
  if (!isIsoDate(start) || !isIsoDate(end) || start > end) return 'window_unknown';
  return date < start || date > end ? 'outside' : 'inside';
}

/** The HLS window warning is active only with the switch on and the reading "outside". */
export function hlsWarningActive(windowReading, hlsOn = true) {
  return hlsOn !== false && own(windowReading, 'state') === 'outside';
}

/**
 * Context of rule 6. Input keys: sowingDate, zarcWindow: { corn, grass }, windowReading,
 * hlsOn, zarcConstraint (true only when the user marked insurance or credit inside the Zarc).
 */
export function windowContext(input = {}) {
  const zw = own(input, 'zarcWindow');
  const date = own(input, 'sowingDate');
  const corn = sowingDateStatus(date, own(zw, 'corn'));
  const grass = sowingDateStatus(date, own(zw, 'grass'));
  const warning = hlsWarningActive(own(input, 'windowReading'), own(input, 'hlsOn'));
  const constraint = own(input, 'zarcConstraint') === true;
  const flags = [];
  if (warning) flags.push('window_warning');
  if (corn === 'inside' || corn === 'outside') flags.push('date_in_charge');
  if (corn === 'invalid') flags.push('date_invalid');
  if (corn === 'window_unknown') flags.push('zarc_window_unknown');
  if (warning && !constraint) flags.push('check_window_with_bank');
  return { corn, grass, warning, constraint, flags };
}

/** With the Zarc constraint, S2 leaves when the declared date is outside the consortium window. */
export function zarcRemoval(opt, ctx) {
  if (opt !== 'S2' || !ctx.constraint || ctx.grass !== 'outside') return null;
  return { id: ZARC_CONSORTIUM_REASON, why: null };
}

/**
 * Rule 6 on a state. The declared date is in charge, and the warning alone never gives a verdict:
 * the only change it can make is keep -> not_enough, with the constraint and without a date.
 */
export function cornWindow(result, ctx, opt = null) {
  const out = { ...result, flags: [...new Set([...(result.flags ?? []), ...ctx.flags])] };
  if (out.state === 'removed') return out;
  if (opt === 'S2' && ctx.constraint && ctx.grass === 'window_unknown' && out.state !== 'not_enough') {
    return { ...out, state: 'not_enough', code: 'window_unknown' };
  }
  if (out.state !== 'keep') return out;
  switch (ctx.corn) {
    case 'outside':
      return { ...out, state: 'not_enough', code: 'window_outside', message: MESSAGES.windowOutside };
    case 'invalid':
      return { ...out, state: 'not_enough', code: 'need_sowing_date', field: 'sowing_date', message: MESSAGES.needSowingDate };
    case 'window_unknown':
      return { ...out, state: 'not_enough', code: 'window_unknown' };
    case 'none':
      if (ctx.constraint && ctx.warning) {
        return { ...out, state: 'not_enough', code: 'need_sowing_date', field: 'sowing_date', message: MESSAGES.needSowingDate };
      }
      return out;
    default:
      return out;
  }
}

// ---------------------------------------------------------------- one option, one field

function blank(opt) {
  return {
    option: opt,
    state: null,
    code: null,
    G: null,
    step: null,
    N: null,
    n: null,
    M_corn: null,
    M_wheat: null,
    field: null,
    message: null,
    reasons: [],
    flags: [],
  };
}

/**
 * Full rule for S2 or S3 against S1. Input keys: numbers, trials: { S2, S3 } (the `options` of
 * trials_steps.json), constraints, hardRules, labels, and the rule 6 keys of windowContext.
 * Anything else in the input (priority, engine outputs, labels of the model check) is ignored.
 */
export function decideOption(opt, input = {}) {
  if (!ESSENTIAL[opt]) throw new RangeError(`Unknown option: ${opt}`);
  const inp = isObject(input) ? input : {};
  const ctx = windowContext(inp);
  const out = blank(opt);

  // 1. Hard rules and user constraints remove the option, saying why.
  const reasons = removeOption(opt, own(inp, 'constraints'), own(inp, 'hardRules')) ?? [];
  const zarc = zarcRemoval(opt, ctx);
  if (zarc) reasons.push(zarc);
  if (reasons.length) return { ...out, state: 'removed', code: 'removed', reasons, flags: [...ctx.flags] };

  // 2. G only with valid inputs.
  const numbers = own(inp, 'numbers');
  const field = invalidField(opt, numbers);
  if (field) {
    return {
      ...out,
      state: 'not_enough',
      code: 'invalid_field',
      field,
      message: needFieldMessage(field, own(inp, 'labels')),
      flags: [...ctx.flags],
    };
  }
  let G;
  if (opt === 'S3') {
    const mc = marginCorn(numbers);
    const mw = marginWheat(numbers);
    G = computeG3(numbers);
    if (Number.isFinite(G)) {
      out.M_corn = mc;
      out.M_wheat = mw;
    }
  } else {
    G = computeG2(numbers);
  }

  // 3 and 4. Count in steps and apply the rule.
  let r = ruleState(G, own(own(inp, 'trials'), opt));
  // S2: a verdict from the counts needs the establishment method of every site-season (protocol 11).
  if (opt === 'S2' && COUNT_CODES.includes(r.code) && methodCounts(own(own(inp, 'trials'), 'S2'), r.step) === null) {
    r = { ...r, state: 'not_enough', code: 'method_missing' };
  }

  // 6. Corn window.
  return cornWindow({ ...out, ...r }, ctx, opt);
}

/** S1 reference for L4: M_corn when the corn numbers are valid, else the first invalid field. */
export function referenceMargin(numbers) {
  for (const f of CORN_FIELDS) if (!isValidValue(f, own(numbers, f))) return { M_corn: null, field: f };
  const m = marginCorn(numbers);
  return Number.isFinite(m) ? { M_corn: m, field: null } : { M_corn: null, field: null };
}

/**
 * Main sentence (7.15): the state of higher precedence, S3 first on equal states. With both
 * options removed: keep with both reasons, unless rule 6 turns it into not_enough.
 */
export function answer(s2, s3, ctx = null) {
  for (const r of [s2, s3]) {
    if (!isObject(r) || !STATES.includes(r.state)) throw new TypeError('answer() takes two option results');
  }
  if (s2.state === 'removed' && s3.state === 'removed') {
    const both = {
      state: 'keep',
      code: 'both_removed',
      main: null,
      other: null,
      field: null,
      message: null,
      reasons: [...s2.reasons, ...s3.reasons],
      flags: [],
    };
    return ctx ? cornWindow(both, ctx, null) : both;
  }
  const main = PRECEDENCE.indexOf(s3.state) <= PRECEDENCE.indexOf(s2.state) ? s3 : s2;
  const other = main === s3 ? s2 : s3;
  return {
    state: main.state,
    code: main.code,
    main: main.option,
    other: other.option,
    field: main.field,
    message: main.message,
    reasons: [...main.reasons],
    flags: [...main.flags],
  };
}

/** Row highlighted by the single priority (7.4): income L4, water L2, soil L1. */
export function priorityHighlight(priority) {
  return Object.hasOwn(PRIORITY_ROW, priority) ? PRIORITY_ROW[priority] : null;
}

/** Everything the page needs for one field. Priority changes only `highlight`. */
export function decideField(input = {}) {
  const inp = isObject(input) ? input : {};
  const S2 = decideOption('S2', inp);
  const S3 = decideOption('S3', inp);
  return {
    S1: referenceMargin(own(inp, 'numbers')),
    S2,
    S3,
    answer: answer(S2, S3, windowContext(inp)),
    highlight: priorityHighlight(own(inp, 'priority')),
  };
}

/** Day number of an ISO date "YYYY-MM-DD", or null. */
function dayNumber(iso) {
  if (typeof iso !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(iso)) return null;
  const [y, m, d] = iso.split('-').map(Number);
  const t = Date.UTC(y, m - 1, d);
  return Number.isFinite(t) ? t / 86400000 : null;
}

/** Midpoint, in days, of a green-exit interval with status "ok", or null. */
function exitMidpoint(ev) {
  if (!ev || ev.status !== 'ok') return null;
  const lo = dayNumber(ev.low);
  const hi = dayNumber(ev.high);
  return lo === null || hi === null || hi < lo ? null : (lo + hi) / 2;
}

/**
 * Which field first (plan 13.7 rule 5, criterion M5.8).
 * greenEnd: {fieldId: [{season, low, high, status}]}, the green-exit intervals of M2.
 * noiseFloor: N1 in days. states: {fieldId: state of the option on that field}.
 * selectionSeasons: the seasons used to choose the fields, always left out.
 * Only fields whose option is try_strip or tied are compared. A season counts when both
 * fields have an "ok" interval in it. A field goes first when, against every other compared
 * field, its soybean left later by more than the noise floor (interval midpoints) in more
 * than half of the comparable seasons. Otherwise no field stands out.
 * Returns {first, other, n, m, pairs, reason}: n seasons later out of m comparable, against other[0].
 */
export function fieldOrder(greenEnd, noiseFloor, states, selectionSeasons = []) {
  const none = (reason, extra = {}) => ({ first: null, other: [], n: null, m: null, pairs: {}, reason, ...extra });
  if (!Number.isFinite(noiseFloor) || noiseFloor < 0) return none('no_noise_floor');
  const skip = new Set(selectionSeasons);
  const ids = Object.keys(states ?? {})
    .filter((id) => states[id] === 'try_strip' || states[id] === 'tied')
    .filter((id) => Array.isArray(greenEnd?.[id]))
    .sort();
  if (ids.length < 2) return none('fewer_than_two_fields', { other: ids });
  const mids = {};
  for (const id of ids) {
    mids[id] = new Map();
    for (const ev of greenEnd[id]) {
      if (!ev || skip.has(ev.season)) continue;
      const mid = exitMidpoint(ev);
      if (mid !== null) mids[id].set(ev.season, mid);
    }
  }
  const pair = (x, y) => {
    let m = 0;
    let n = 0;
    for (const [season, mx] of mids[x]) {
      if (!mids[y].has(season)) continue;
      m += 1;
      if (mx - mids[y].get(season) > noiseFloor) n += 1;
    }
    return { n, m };
  };
  const all = {};
  for (const x of ids) {
    const others = ids.filter((y) => y !== x);
    const pairs = Object.fromEntries(others.map((y) => [y, pair(x, y)]));
    all[x] = pairs;
    if (others.every((y) => pairs[y].m > 0 && pairs[y].n * 2 > pairs[y].m)) {
      return { first: x, other: others, n: pairs[others[0]].n, m: pairs[others[0]].m, pairs, reason: null };
    }
  }
  return none('no_field_stands_out', { other: ids, pairs: all });
}

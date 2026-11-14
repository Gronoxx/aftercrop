// Written with an SI tool (Claude, Anthropic) for NASA Space Apps 2026. See AI_USE.md.
// Hand-drawn SVG for the page (plan 7.3, 7.6, 7.7, 7.14, 7.18): icons, seals, the year strip,
// the 8-season ribbon, the trial steps and the field map.
//
// Pure functions that return SVG strings. No DOM, no network, no text: every word on screen
// comes from copy.json through app.mjs, which passes it in. Colours are CSS classes, so light
// and dark themes live in site.css. Every colour also has a pattern, an icon or a text.

// Inline SVG in an HTML page needs no xmlns, so this file holds no URL at all.

export function esc(s) {
  return String(s)
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;');
}

function num(x) {
  return Number.isFinite(x) ? Math.round(x * 100) / 100 : 0;
}

// ---------------------------------------------------------------- shared patterns

/** One hidden SVG with the patterns used by every chart. Inserted once per page. */
export function defsSvg() {
  return (
    `<svg class="svg-defs" width="0" height="0" aria-hidden="true" focusable="false">` +
    '<defs>' +
    // No living crop: wide diagonal hatch.
    '<pattern id="pat-nocrop" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">' +
    '<rect class="pat-bg" width="6" height="6"/><line class="pat-ink" x1="0" y1="0" x2="0" y2="6" stroke-width="2"/></pattern>' +
    // No data: cross hatch, different from "no green" and from "no living crop".
    '<pattern id="pat-nodata" width="5" height="5" patternUnits="userSpaceOnUse">' +
    '<rect class="pat-bg" width="5" height="5"/><path class="pat-ink pat-thin" d="M0 0L5 5M5 0L0 5" stroke-width="0.5" fill="none"/></pattern>' +
    // To confirm: dots on green.
    '<pattern id="pat-confirm" width="5" height="5" patternUnits="userSpaceOnUse">' +
    '<rect class="pat-green" width="5" height="5"/><circle class="pat-dot" cx="2.5" cy="2.5" r="1.1"/></pattern>' +
    // Grass alive with corn: thin vertical stripes over the corn colour.
    '<pattern id="pat-cornGrass" width="4" height="4" patternUnits="userSpaceOnUse">' +
    '<rect class="crop-corn" width="4" height="4"/><rect class="crop-grass" x="0" width="1.4" height="4"/></pattern>' +
    '</defs></svg>'
  );
}

// ---------------------------------------------------------------- icons

const STATE_PATHS = {
  // Two paired strips: try it on a strip.
  try_strip: '<rect x="3" y="3" width="4" height="14" rx="1"/><rect x="13" y="3" width="4" height="14" rx="1"/>',
  // Equals: tied.
  tied: '<rect x="3" y="6" width="14" height="2.6" rx="1"/><rect x="3" y="11.4" width="14" height="2.6" rx="1"/>',
  // Tick: keep what you do.
  keep: '<path d="M3.5 10.5l4 4 9-9" fill="none" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"/>',
  // Question mark: not enough evidence.
  not_enough:
    '<circle cx="10" cy="10" r="8" fill="none" stroke-width="2"/>' +
    '<path d="M7.4 7.6a2.7 2.7 0 1 1 3.9 2.4c-.9.5-1.3 1-1.3 2v.6" fill="none" stroke-width="2" stroke-linecap="round"/>' +
    '<circle cx="10" cy="15" r="1.2"/>',
  // Cross: out here.
  removed:
    '<circle cx="10" cy="10" r="8" fill="none" stroke-width="2"/>' +
    '<path d="M6.8 6.8l6.4 6.4M13.2 6.8l-6.4 6.4" fill="none" stroke-width="2" stroke-linecap="round"/>',
};

/** Icon of a decision state. Decorative: the state word always sits next to it. */
export function stateIcon(state) {
  const body = STATE_PATHS[state] ?? STATE_PATHS.not_enough;
  return (
    `<svg class="icon icon-state icon-${esc(state)}" viewBox="0 0 20 20" width="20" height="20" ` +
    `aria-hidden="true" focusable="false">${body}</svg>`
  );
}

// Seal icons of 7.14, drawn on a 16 x 16 grid.
const SATELLITE =
  '<rect x="6" y="6" width="4" height="4" rx=".6"/><rect x="1" y="6.6" width="4" height="2.8"/>' +
  '<rect x="11" y="6.6" width="4" height="2.8"/>';
const SEAL_PATHS = {
  seen_by_satellite: SATELLITE,
  estimated_from_satellite:
    SATELLITE + '<path d="M3 13.5c1.5-1.2 3-1.2 4.5 0s3 1.2 4.5 0" fill="none" stroke-width="1.3"/>',
  reanalysis:
    '<path d="M2 2h12v12H2zM6 2v12M10 2v12M2 6h12M2 10h12" fill="none" stroke-width="1.2"/>',
  simulated:
    '<circle cx="8" cy="8" r="2.3" fill="none" stroke-width="1.4"/>' +
    '<path d="M8 1.5v2.6M8 11.9v2.6M1.5 8h2.6M11.9 8h2.6M3.4 3.4l1.8 1.8M10.8 10.8l1.8 1.8M3.4 12.6l1.8-1.8M10.8 5.2l1.8-1.8" fill="none" stroke-width="1.5" stroke-linecap="round"/>',
  measured_in_trials:
    '<rect x="2" y="2" width="5" height="5"/><rect x="9" y="2" width="5" height="5"/>' +
    '<rect x="2" y="9" width="5" height="5"/><rect x="9" y="9" width="5" height="5"/>',
  derived_from_trials:
    '<rect x="2" y="2" width="5" height="5"/><rect x="9" y="2" width="5" height="5"/>' +
    '<rect x="2" y="9" width="5" height="5"/><path d="M11.5 9v5M9 11.5h5" fill="none" stroke-width="1.6"/>',
  official_rule:
    '<path d="M8 2v12M4 14h8M2 5h12M4 5l-2 5h4zM12 5l-2 5h4z" fill="none" stroke-width="1.2" stroke-linejoin="round"/>',
  published_climatology:
    '<path d="M2 3c2-1 4-1 6 1 2-2 4-2 6-1v10c-2-1-4-1-6 1-2-2-4-2-6-1z M8 4v10" fill="none" stroke-width="1.2" stroke-linejoin="round"/>',
  your_numbers:
    '<path d="M3 13l1-3.5 7-7 2.5 2.5-7 7z M10 3.5l2.5 2.5" fill="none" stroke-width="1.3" stroke-linejoin="round"/>',
  unknown:
    '<circle cx="8" cy="8" r="6.5" fill="none" stroke-width="1.3"/>' +
    '<path d="M6 6.2a2.1 2.1 0 1 1 3 1.9c-.7.4-1 .8-1 1.6" fill="none" stroke-width="1.4" stroke-linecap="round"/>' +
    '<circle cx="8" cy="11.8" r=".9"/>',
};

export const SEAL_IDS = Object.freeze(Object.keys(SEAL_PATHS));

/** Icon of a seal (evidence class). Decorative: the seal name always sits next to it. */
export function sealIcon(id) {
  const body = SEAL_PATHS[id] ?? SEAL_PATHS.unknown;
  return (
    `<svg class="icon icon-seal" viewBox="0 0 16 16" width="16" height="16" ` +
    `aria-hidden="true" focusable="false">${body}</svg>`
  );
}

// ---------------------------------------------------------------- dates on a fixed axis

/** "YYYY-MM-DD" to [y, m, d], or null. */
export function parseIso(s) {
  if (typeof s !== 'string') return null;
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(s);
  if (!m) return null;
  const y = Number(m[1]);
  const mo = Number(m[2]);
  const d = Number(m[3]);
  const t = new Date(Date.UTC(y, mo - 1, d));
  if (t.getUTCFullYear() !== y || t.getUTCMonth() !== mo - 1 || t.getUTCDate() !== d) return null;
  return [y, mo, d];
}

/** Civil ten-day period of a day of month: 0 for days 1 to 10, 1 for 11 to 20, 2 for 21 to the end.
 *  Same civil periods as pipeline/common/dekad.py. Display only, never a decision. */
export function tenDayOf(day) {
  return day <= 10 ? 0 : day <= 20 ? 1 : 2;
}

/** Index 0..35 of a month and day on a January to December axis. */
export function slotJan(month, day) {
  return (month - 1) * 3 + tenDayOf(day);
}

/** Index 0..35 of a month and day on a September to August axis (the ribbon, 1 Sep origin). */
export function slotSep(month, day) {
  return ((month + 3) % 12) * 3 + tenDayOf(day);
}

const DAY = 86400000;

/** Fraction 0..1 of a date along the season that starts on 1 Sep of `startYear`. */
export function seasonFraction(iso, startYear) {
  const p = parseIso(iso);
  if (!p) return null;
  const t0 = Date.UTC(startYear, 8, 1);
  const t1 = Date.UTC(startYear + 1, 8, 1);
  const t = Date.UTC(p[0], p[1] - 1, p[2]);
  return (t - t0) / (t1 - t0);
}

/** Fraction 0..1 of a date along its own calendar year. */
export function yearFraction(iso) {
  const p = parseIso(iso);
  if (!p) return null;
  const t0 = Date.UTC(p[0], 0, 1);
  const t1 = Date.UTC(p[0] + 1, 0, 1);
  return (Date.UTC(p[0], p[1] - 1, p[2]) - t0) / (t1 - t0);
}

/** First year of a season label "2025/26". */
export function seasonStartYear(season) {
  const m = /^(\d{4})\/(\d{2,4})$/.exec(String(season ?? ''));
  return m ? Number(m[1]) : null;
}

/**
 * Calendar segments { crop, start, end, state } to 36 slots on the January to December axis.
 * A segment that crosses 31 Dec wraps. Later segments win, so list order is respected.
 * Returns an array of 36 values from `pick(segment)`, or null where nothing covers the slot.
 */
export function segmentsToSlots(segments, accept = () => true, pick = (s) => s.crop) {
  const out = new Array(36).fill(null);
  for (const s of Array.isArray(segments) ? segments : []) {
    if (!s || !accept(s)) continue;
    const a = parseIso(s.start);
    const b = parseIso(s.end);
    if (!a || !b) continue;
    const from = slotJan(a[1], a[2]);
    const to = slotJan(b[1], b[2]);
    const span = Date.UTC(b[0], b[1] - 1, b[2]) - Date.UTC(a[0], a[1] - 1, a[2]);
    if (span < 0) continue;
    const full = span >= 365 * DAY;
    for (let i = 0; i < 36; i++) {
      const inside = full || (from <= to ? i >= from && i <= to : i >= from || i <= to);
      if (inside) out[i] = pick(s);
    }
  }
  return out;
}

// ---------------------------------------------------------------- year strip (7.6)

export const CROPS = Object.freeze(['soybean', 'corn', 'corn_grass', 'grass', 'wheat']);
const W = 360; // 10 units per ten-day period
const SLOT = W / 36;

function cropFill(crop) {
  if (crop === 'corn_grass') return 'url(#pat-cornGrass)';
  if (CROPS.includes(crop)) return null; // CSS class
  return 'url(#pat-nocrop)';
}

function cropRect(x, y, w, h, crop) {
  const fill = cropFill(crop);
  const cls = CROPS.includes(crop) && crop !== 'corn_grass' ? `crop-${crop}` : 'crop-none';
  return `<rect class="${cls}" x="${num(x)}" y="${num(y)}" width="${num(w)}" height="${num(h)}"${fill ? ` fill="${fill}"` : ''}/>`;
}

/**
 * One strategy row of the year strip: crop band in ten-day slots, with three mark tracks above
 * (legal soybean window, soybean-free period, Zarc window) and optional NDVI dots behind.
 * `crops` and `marks.*` are 36-slot arrays from segmentsToSlots. `ndvi` is [{date, ndvi}].
 */
export function yearRowSvg({ crops, marks = {}, ndvi = [], label = '' }) {
  const H = 26;
  const bandY = 9;
  const bandH = 15;
  let body = '';
  // Mark tracks: three thin lines, one height each, so they never hide each other.
  const tracks = [
    ['soy_window', 1, 'mark-soy'],
    ['sanitary_break', 3.5, 'mark-break'],
    ['zarc_window', 6, 'mark-zarc'],
  ];
  for (const [key, y, cls] of tracks) {
    const arr = marks[key] ?? [];
    for (let i = 0; i < 36; i++) if (arr[i]) body += `<rect class="${cls}" x="${num(i * SLOT)}" y="${y}" width="${num(SLOT)}" height="2"/>`;
  }
  // Crop band, merged into runs.
  let i = 0;
  while (i < 36) {
    const c = crops?.[i] ?? null;
    let j = i;
    while (j + 1 < 36 && (crops?.[j + 1] ?? null) === c) j++;
    body += cropRect(i * SLOT, bandY, (j - i + 1) * SLOT, bandH, c);
    i = j + 1;
  }
  // Month separators.
  for (let m = 1; m < 12; m++) body += `<line class="grid" x1="${m * 30}" y1="${bandY}" x2="${m * 30}" y2="${bandY + bandH}"/>`;
  // NDVI dots: this field, for reference only, behind S1 only (app.mjs decides).
  for (const p of Array.isArray(ndvi) ? ndvi : []) {
    const f = yearFraction(p?.date);
    const v = p?.ndvi;
    if (f === null || typeof v !== 'number' || !Number.isFinite(v)) continue;
    const y = bandY + bandH - Math.max(0, Math.min(1, v)) * bandH;
    body += `<circle class="ndvi-dot" cx="${num(f * W)}" cy="${num(y)}" r="1.4"/>`;
  }
  return (
    `<svg class="strip-row" viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(label)}" focusable="false">` +
    `${body}</svg>`
  );
}

// ---------------------------------------------------------------- ribbon (7.7)

export const CELL_STATES = Object.freeze(['green', 'not_green', 'to_confirm', 'no_data']);

function cellFill(state) {
  if (state === 'no_data') return 'url(#pat-nodata)';
  if (state === 'to_confirm') return 'url(#pat-confirm)';
  return null;
}

/** Number of separate green runs in a season (no data neither splits nor joins). */
export function greenCycles(cells) {
  let cycles = 0;
  let prev = null;
  for (const c of Array.isArray(cells) ? cells : []) {
    if (c === 'no_data') continue;
    const g = c === 'green' || c === 'to_confirm';
    if (g && prev !== true) cycles++;
    prev = g;
  }
  return cycles;
}

/**
 * One season of the ribbon: 36 cells from 1 Sep, the soybean exit as an event bar from the
 * last to the next clean look, and the end of the corn Zarc window as a dashed line.
 */
export function ribbonRowSvg({ cells, season, exit = null, zarcEnd = null, label = '' }) {
  const H = 17;
  const y0 = 1;
  const h = 10;
  let body = '';
  for (let i = 0; i < 36; i++) {
    const s = CELL_STATES.includes(cells?.[i]) ? cells[i] : 'no_data';
    const fill = cellFill(s);
    body += `<rect class="cell cell-${s}" x="${num(i * SLOT + 0.4)}" y="${y0}" width="${num(SLOT - 0.8)}" height="${h}"${fill ? ` fill="${fill}"` : ''}/>`;
  }
  const start = seasonStartYear(season);
  if (start !== null && zarcEnd) {
    const md = /^\d{4}-(\d{2}-\d{2})$/.exec(zarcEnd);
    if (md) {
      const f = seasonFraction(`${start + 1}-${md[1]}`, start);
      if (f !== null && f >= 0 && f <= 1) {
        body += `<line class="zarc-end" x1="${num(f * W)}" y1="0" x2="${num(f * W)}" y2="${H}"/>`;
      }
    }
  }
  if (start !== null && exit && exit.low && exit.high) {
    const a = seasonFraction(exit.low, start);
    const b = seasonFraction(exit.high, start);
    if (a !== null && b !== null && b >= a) {
      const ya = y0 + h + 3.2;
      body +=
        `<rect class="exit-bar" x="${num(a * W)}" y="${ya - 1}" width="${num(Math.max(1.5, (b - a) * W))}" height="2"/>` +
        `<rect class="exit-cap" x="${num(a * W - 0.6)}" y="${ya - 2.5}" width="1.2" height="5"/>` +
        `<rect class="exit-cap" x="${num(b * W - 0.6)}" y="${ya - 2.5}" width="1.2" height="5"/>`;
    }
  }
  return (
    `<svg class="ribbon-row" viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(label)}" focusable="false">` +
    `${body}</svg>`
  );
}

// ---------------------------------------------------------------- Zarc risk band (SA-D72.1)

export const RISK_LEVELS = Object.freeze([20, 30, 40]);

/** CSS class of one Zarc risk value: risk-20, risk-30, risk-40, or risk-none outside the zoning. */
export function riskClass(v) {
  if (!Number.isFinite(v) || v <= 0) return 'risk-none';
  return v <= 20 ? 'risk-20' : v <= 30 ? 'risk-30' : 'risk-40';
}

/**
 * The Zarc risk above the ribbon, one thin row of 36 ten-day periods from 1 Sep. `risk` (36
 * values, null outside the zoning) for a declared class, or `min` and `max` (36 each) with
 * `someOutside` without a report: the upper half is the highest risk, the lower half the lowest,
 * and a tick under the cell marks a period outside the zoning for some classes.
 */
export function riskBandSvg({ risk = null, min = null, max = null, someOutside = null, label = '' }) {
  const H = 9;
  const y0 = 1;
  const h = 6;
  const ok = (a) => Array.isArray(a) && a.length === 36;
  let body = '';
  for (let i = 0; i < 36; i++) {
    const x = num(i * SLOT + 0.4);
    const w = num(SLOT - 0.8);
    if (ok(risk)) {
      body += `<rect class="risk ${riskClass(risk[i])}" x="${x}" y="${y0}" width="${w}" height="${h}"/>`;
    } else if (ok(min) && ok(max)) {
      const hiC = riskClass(max[i]);
      const loC = riskClass(min[i]);
      if (hiC === loC) body += `<rect class="risk ${hiC}" x="${x}" y="${y0}" width="${w}" height="${h}"/>`;
      else {
        body += `<rect class="risk ${hiC}" x="${x}" y="${y0}" width="${w}" height="${h / 2}"/>`;
        body += `<rect class="risk ${loC}" x="${x}" y="${y0 + h / 2}" width="${w}" height="${h / 2}"/>`;
      }
      if (ok(someOutside) && someOutside[i] === true) {
        body += `<rect class="risk-partial" x="${num(i * SLOT + SLOT / 2 - 0.6)}" y="${y0 + h + 0.4}" width="1.2" height="1.4"/>`;
      }
    }
  }
  return (
    `<svg class="risk-band" viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(label)}" focusable="false">` +
    `${body}</svg>`
  );
}

// ---------------------------------------------------------------- trial steps (7.13)

/**
 * Steps of trial counts: site-seasons with gain >= g, for g = 1, 2, ... bags/ha, with a mark
 * at the break-even G of the farmer. A count, not a chance.
 */
export function stepsSvg({ steps, n, G = null, label = '' }) {
  const list = Array.isArray(steps) ? steps.filter((s) => Number.isFinite(s?.g_sc_ha) && Number.isFinite(s?.count)) : [];
  if (!list.length || !Number.isFinite(n) || n <= 0) return '';
  const gMax = Math.max(...list.map((s) => s.g_sc_ha), Number.isFinite(G) ? Math.ceil(G) : 0) + 1;
  const Wd = 200;
  const Hd = 64;
  const x = (g) => 4 + (g / gMax) * (Wd - 8);
  const y = (c) => Hd - 6 - (c / n) * (Hd - 14);
  let body = `<line class="axis" x1="4" y1="${Hd - 6}" x2="${Wd - 4}" y2="${Hd - 6}"/>`;
  const sorted = [...list].sort((a, b) => a.g_sc_ha - b.g_sc_ha);
  for (const s of sorted) {
    body += `<rect class="step" x="${num(x(s.g_sc_ha - 1) + 0.6)}" y="${num(y(s.count))}" width="${num(x(1) - x(0) - 1.2)}" height="${num(Hd - 6 - y(s.count))}"/>`;
  }
  if (Number.isFinite(G) && G > 0) {
    const gx = x(G);
    body += `<line class="g-mark" x1="${num(gx)}" y1="2" x2="${num(gx)}" y2="${Hd - 4}"/>` +
      `<path class="g-head" d="M${num(gx - 3)} 2h6l-3 4z"/>`;
  }
  return (
    `<svg class="steps" viewBox="0 0 ${Wd} ${Hd}" role="img" aria-label="${esc(label)}" focusable="false">` +
    `${body}</svg>`
  );
}

// ---------------------------------------------------------------- field map (7.3)

/** Bounds [minX, minY, maxX, maxY] of every polygon, or null. */
export function polygonBounds(polys) {
  let b = null;
  for (const poly of polys) {
    for (const p of Array.isArray(poly) ? poly : []) {
      if (!Array.isArray(p) || !Number.isFinite(p[0]) || !Number.isFinite(p[1])) continue;
      b = b ? [Math.min(b[0], p[0]), Math.min(b[1], p[1]), Math.max(b[2], p[0]), Math.max(b[3], p[1])] : [p[0], p[1], p[0], p[1]];
    }
  }
  return b;
}

/**
 * Field outlines in image pixels, over the HLS composite when it loads. `fields` is
 * [{ id, polygon, label, active }]. `image` is { href, width, height } or null.
 * The outlines are a pointer shortcut: the field buttons do the same, by keyboard too.
 */
export function fieldMapSvg({ fields, image = null, label = '' }) {
  const polys = fields.map((f) => f.polygon);
  let vb;
  if (image && image.width > 0 && image.height > 0) vb = [0, 0, image.width, image.height];
  else {
    const b = polygonBounds(polys);
    if (!b) return '';
    const pad = Math.max(4, 0.08 * Math.max(b[2] - b[0], b[3] - b[1]));
    vb = [b[0] - pad, b[1] - pad, b[2] - b[0] + 2 * pad, b[3] - b[1] + 2 * pad];
  }
  let body = image ? `<image href="${esc(image.href)}" x="0" y="0" width="${image.width}" height="${image.height}"/>` : `<rect class="map-bg" x="${num(vb[0])}" y="${num(vb[1])}" width="${num(vb[2])}" height="${num(vb[3])}"/>`;
  for (const f of fields) {
    const pts = (Array.isArray(f.polygon) ? f.polygon : [])
      .filter((p) => Array.isArray(p) && Number.isFinite(p[0]) && Number.isFinite(p[1]))
      .map((p) => `${num(p[0])},${num(p[1])}`)
      .join(' ');
    if (!pts) continue;
    body += `<polygon class="field-poly${f.active ? ' is-active' : ''}" data-field="${esc(f.id)}" points="${pts}"/>`;
  }
  return (
    `<svg class="field-map" viewBox="${vb.map(num).join(' ')}" role="img" aria-label="${esc(label)}" ` +
    `preserveAspectRatio="xMidYMid meet" focusable="false">${body}</svg>`
  );
}

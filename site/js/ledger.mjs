// Written with an SI tool (Claude, Anthropic) for NASA Space Apps 2026. See AI_USE.md.
// Evidence Ledger on the page (plan 8.3, 8.4 step 20, 8.6): every number carries the id of a
// ledger record in `data-ledger`. Selecting the number opens its record.
//
// recordRows() is pure and runs in Node. attachLedger() wires the page: one delegated listener,
// keyboard included (the numbers are buttons), and a <dialog> filled with textContent only.

// The ten required fields of a record (8.6), in display order, plus its id.
export const RECORD_FIELDS = Object.freeze([
  'id',
  'value',
  'unit',
  'evidence_class',
  'source_id',
  'transform',
  'method_id',
  'data_version',
  'uncertainty',
  'validation_state',
  'limits',
]);

function show(v, unknown) {
  if (v === null || v === undefined || v === '') return unknown;
  if (typeof v === 'number') return Number.isFinite(v) ? String(v) : unknown;
  if (typeof v === 'string') return v;
  if (Array.isArray(v)) return v.length ? v.map((x) => show(x, unknown)).join(', ') : unknown;
  if (typeof v === 'object') {
    const parts = Object.entries(v).map(([k, x]) => `${k}: ${show(x, unknown)}`);
    return parts.length ? parts.join(', ') : unknown;
  }
  return String(v);
}

/**
 * Rows [{ key, text }] for one record. `sealName(id)` gives the seal word of an evidence
 * class, `unknown` the word for a missing value. A missing record gives one row: the id, unknown.
 */
export function recordRows(id, record, { sealName = (x) => x, unknown = 'Unknown' } = {}) {
  if (!record || typeof record !== 'object') return [{ key: 'id', text: show(id, unknown) }, { key: 'record', text: unknown }];
  return RECORD_FIELDS.map((key) => {
    const v = key === 'id' ? record.id ?? id : record[key];
    const text = key === 'evidence_class' && typeof v === 'string' ? sealName(v) : show(v, unknown);
    return { key, text };
  });
}

/** Records of ledger.json ({ records: { id: record } }), or an empty map. */
export function recordsOf(ledgerDoc) {
  const r = ledgerDoc && typeof ledgerDoc === 'object' ? ledgerDoc.records : null;
  return r && typeof r === 'object' && !Array.isArray(r) ? r : {};
}

/**
 * Wire the page. `getContext()` returns { records, sealName, unknown, title, close } at click
 * time, so a language switch needs no rewiring.
 */
export function attachLedger(doc, dialog, getContext) {
  if (!doc || !dialog) return;
  const open = (id, opener) => {
    const ctx = getContext();
    const rows = recordRows(id, ctx.records?.[id], ctx);
    dialog.replaceChildren();
    const h = doc.createElement('h2');
    h.id = 'ledger-title';
    h.textContent = ctx.title;
    dialog.setAttribute('aria-labelledby', 'ledger-title');
    const dl = doc.createElement('dl');
    dl.className = 'ledger-record';
    for (const row of rows) {
      const dt = doc.createElement('dt');
      dt.textContent = row.key;
      const dd = doc.createElement('dd');
      dd.textContent = row.text;
      dl.append(dt, dd);
    }
    const btn = doc.createElement('button');
    btn.type = 'button';
    btn.className = 'btn';
    btn.textContent = ctx.close;
    btn.addEventListener('click', () => close());
    dialog.append(h, dl, btn);
    const close = () => {
      if (typeof dialog.close === 'function' && dialog.open) dialog.close();
      else dialog.removeAttribute('open');
      if (opener && typeof opener.focus === 'function') opener.focus();
    };
    if (typeof dialog.showModal === 'function') dialog.showModal();
    else dialog.setAttribute('open', '');
    btn.focus();
  };
  doc.addEventListener('click', (ev) => {
    const el = ev.target instanceof Element ? ev.target.closest('[data-ledger]') : null;
    if (!el || dialog.contains(el)) return;
    const id = el.getAttribute('data-ledger');
    if (!id) return;
    ev.preventDefault();
    open(id, el);
  });
}

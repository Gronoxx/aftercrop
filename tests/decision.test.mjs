// Written with an SI tool (Claude, Anthropic) for NASA Space Apps 2026. See AI_USE.md.
// Tests of site/js/decision.mjs, criteria M5.1 to M5.5 and M5.9 (plan section 17).
// Run: node --test tests/decision.test.mjs   (Node 22)
// Fixtures are synthetic and labelled "example, fictitious". No trial values.

import { test, describe } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

import * as D from '../site/js/decision.mjs';

const FIXTURES = new URL('./fixtures/', import.meta.url);
const load = (name) => JSON.parse(readFileSync(new URL(name, FIXTURES), 'utf8'));

const NUM = load('decision_numbers.json');
const STEPS = load('decision_steps.json');
const WIN = load('decision_window.json');

const numbers = (override = {}) => ({ ...NUM.base, ...override });
const g3Case = (id) => NUM.g3_cases.find((c) => c.id === id);

// Input whose S3 state comes from the n20 table at the G3 of a fixture case.
function s3Input(id, extra = {}) {
  return {
    numbers: numbers(g3Case(id).override),
    trials: { S3: STEPS.n20, S2: STEPS.n20 },
    zarcWindow: WIN.zarcWindow,
    ...extra,
  };
}

// Deterministic generator (mulberry32) with a fixed seed.
function rng(seed) {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function assertAllFinite(value, path = 'result') {
  if (typeof value === 'number') {
    assert.ok(Number.isFinite(value), `${path} is ${value}`);
  } else if (Array.isArray(value)) {
    value.forEach((v, i) => assertAllFinite(v, `${path}[${i}]`));
  } else if (value && typeof value === 'object') {
    for (const [k, v] of Object.entries(value)) assertAllFinite(v, `${path}.${k}`);
  }
}

// ---------------------------------------------------------------- random inputs (M5.2, M5.3)

function randomInput(r) {
  const pick = (list) => list[Math.floor(r() * list.length)];
  const value = (kind) => {
    const u = r();
    if (u < 0.7) return kind === 'cost' ? Math.round(r() * 5000 * 100) / 100 : Math.round((0.01 + r() * 300) * 100) / 100;
    return pick([0, -0, -1, -1e-9, NaN, Infinity, -Infinity, '100', 'abc', null, undefined, true, {}, [], 1e200, 5e-324, 1e-300]);
  };
  const nums = {};
  for (const f of ['p_soy', 'p_corn', 'p_wheat', 'Y_corn', 'Y_wheat', 'c_corn', 'c_wheat', 'c_grass']) {
    if (r() < 0.03) continue; // field missing
    nums[f] = value(D.FIELD_KIND[f]);
  }
  if (r() < 0.2) nums.q = pick([0, 0.5, 1, -0.1, 1.1, NaN, 'x', null]);

  const steps = () => {
    const n = Math.floor(r() * 30);
    const out = [];
    let count = n;
    for (let g = 1; g <= 15; g++) {
      if (r() < 0.1) continue; // gap in the table
      count = Math.max(0, count - Math.floor(r() * 4));
      out.push({ g_sc_ha: g, count });
    }
    return { n, steps: out };
  };
  const evidence = () =>
    pick([
      undefined,
      null,
      steps(),
      steps(),
      steps(),
      { n: 20, steps: [{ g_sc_ha: 1, count: 25 }] },
      { n: '20', steps: [] },
      { n: 2.5, steps: [] },
      { n: 10, steps: 'x' },
      { n: 10, steps: [{ g_sc_ha: 1.5, count: 3 }] },
    ]);
  const constraint = () =>
    pick([
      { id: 'no_narrow_row_seeder', removes: ['S3'], why: 'no narrow-row seeder' },
      { id: 'no_grass_seeding', removes: ['S2'], why: 'no way to seed the grass' },
      { id: 'no_wheat_buyer', removes: 'S3', why: 'no buyer for wheat' },
      { id: 'leased_land', removes: [], why: 'sheet item only' },
      'garbage',
      null,
    ]);
  const list = () => (r() < 0.6 ? [] : Array.from({ length: 1 + Math.floor(r() * 2) }, constraint));

  return {
    numbers: r() < 0.02 ? pick([null, 'x', 42]) : nums,
    trials: { S2: evidence(), S3: evidence() },
    constraints: list(),
    hardRules: r() < 0.9 ? [] : [{ id: 'sanitary_break', removes: ['S2', 'S3'], why: 'soybean sanitary break' }],
    sowingDate: pick([undefined, null, '', ...Object.values(WIN.dates), '2027-02-30', '15/02/2027', 20270215]),
    zarcWindow: pick([WIN.zarcWindow, WIN.zarcWindow, undefined, { corn: { start: '2027-03-10', end: '2027-01-11' } }]),
    windowReading: pick([{ state: 'outside', k: 3, n: 8 }, { state: 'at_the_edge', k: 1, n: 8 }, { state: 'inside' }, null, undefined]),
    hlsOn: pick([true, false, undefined]),
    zarcConstraint: pick([true, false, undefined, 'yes']),
    priority: pick(['income', 'water', 'soil', undefined, 'other']),
  };
}

// ---------------------------------------------------------------- fixtures

test('fixtures are labelled "example, fictitious" (M5.4)', () => {
  const dir = fileURLToPath(FIXTURES);
  const files = readdirSync(dir).filter((f) => /^decision_.*\.json$/.test(f));
  assert.ok(files.length >= 3);
  for (const f of files) assert.equal(load(f).label, 'example, fictitious', f);
});

// ---------------------------------------------------------------- M5.1

describe('M5.1 public rule, exact limits', () => {
  test('G = 0 gives try_strip without using the counts', () => {
    for (const ev of [STEPS.n20, STEPS.n4, undefined, { n: 'bad' }]) {
      const r = D.ruleState(0, ev);
      assert.equal(r.state, 'try_strip');
      assert.equal(r.code, 'pays_without_gain');
      assert.equal(r.N, null);
      assert.equal(r.n, null);
    }
    assert.equal(D.ruleState(-0, STEPS.n4).state, 'try_strip');
    assert.equal(D.ruleState(-5, STEPS.n4).state, 'try_strip');
  });

  test('G just above 0 is counted at the first step', () => {
    const r = D.ruleState(Number.MIN_VALUE, STEPS.n20);
    assert.equal(r.step, 1);
    assert.equal(r.N, 20);
    assert.equal(r.code, 'counts_try');
  });

  test('n = 4 gives not_enough even with N/n = 1', () => {
    const r = D.ruleState(1, STEPS.n4);
    assert.equal(r.state, 'not_enough');
    assert.equal(r.code, 'few_trials');
    assert.equal(r.n, 4);
  });

  test('no trial evidence counts as n = 0', () => {
    assert.equal(D.ruleState(2, undefined).code, 'few_trials');
    assert.equal(D.ruleState(2, undefined).n, 0);
  });

  test('n = 5 is evaluated by the counts', () => {
    assert.equal(D.ruleState(2, STEPS.n5).state, 'try_strip'); // 4 of 5
    assert.equal(D.ruleState(3, STEPS.n5).state, 'tied'); // 3 of 5
    assert.equal(D.ruleState(4, STEPS.n5).state, 'tied'); // 2 of 5
    assert.equal(D.ruleState(5, STEPS.n5).state, 'keep'); // 1 of 5
  });

  test('N/n = 0.7 exactly gives try_strip, just below gives tied', () => {
    const at = D.ruleState(4, STEPS.n20);
    assert.deepEqual([at.state, at.N, at.n], ['try_strip', 14, 20]);
    const below = D.ruleState(4.01, STEPS.n20);
    assert.deepEqual([below.state, below.step, below.N], ['tied', 5, 13]);
  });

  test('N/n = 0.3 exactly gives keep, just above gives tied', () => {
    const at = D.ruleState(8, STEPS.n20);
    assert.deepEqual([at.state, at.N, at.n], ['keep', 6, 20]);
    const above = D.ruleState(7, STEPS.n20);
    assert.deepEqual([above.state, above.N], ['tied', 7]);
  });

  test('G is rounded up to the 1 sc/ha step', () => {
    assert.equal(D.stepOf(3.0001), 4);
    assert.equal(D.stepOf(4), 4);
    assert.equal(D.ruleState(3.5, STEPS.n20).N, 14);
    assert.equal(D.ruleState(3, STEPS.n20).N, 16);
  });

  test('order: G <= 0 before n < 5, n < 5 before the ratio', () => {
    assert.equal(D.ruleState(0, STEPS.n4).state, 'try_strip');
    assert.equal(D.ruleState(1, STEPS.n4).state, 'not_enough');
  });

  test('full path from the fictitious numbers to the state', () => {
    const exp = { g3_negative: 'try_strip', g3_zero: 'try_strip', g3_small_positive: 'try_strip', g3_exact_4: 'try_strip', g3_just_above_4: 'tied', g3_exact_7: 'tied', g3_exact_8: 'keep', g3_zero_costs: 'keep' };
    for (const [id, state] of Object.entries(exp)) {
      const r = D.decideOption('S3', s3Input(id));
      assert.equal(r.G, g3Case(id).G3, id);
      assert.equal(r.state, state, id);
    }
  });

  test('step tables: exact count only when the table pins it', () => {
    assert.equal(D.ruleState(2, STEPS.gap_equal).N, 20);
    assert.equal(D.ruleState(2, STEPS.gap_unequal).code, 'evidence_unusable');
    assert.equal(D.ruleState(1, STEPS.bottom_full).N, 20);
    assert.equal(D.ruleState(1, STEPS.bottom_open).code, 'evidence_unusable');
    assert.equal(D.ruleState(50, STEPS.n20).N, 0);
    assert.equal(D.ruleState(5, STEPS.top_open).code, 'evidence_unusable');
    assert.equal(D.ruleState(1, STEPS.count_above_n).code, 'evidence_unusable');
    assert.equal(D.ruleState(1, STEPS.count_grows).code, 'evidence_unusable');
    assert.equal(D.ruleState(1, { n: '20', steps: [] }).code, 'evidence_unusable');
  });

  test('removal comes before everything, with its reasons', () => {
    const hard = [{ id: 'sanitary_break', removes: ['S2', 'S3'], why: 'soybean sanitary break' }];
    const cons = [{ id: 'no_wheat_buyer', removes: 'S3', why: 'no buyer for wheat' }];
    const r = D.decideOption('S3', { numbers: {}, hardRules: hard, constraints: cons });
    assert.equal(r.state, 'removed');
    assert.deepEqual(r.reasons, [
      { id: 'sanitary_break', why: 'soybean sanitary break' },
      { id: 'no_wheat_buyer', why: 'no buyer for wheat' },
    ]);
    assert.equal(D.decideOption('S2', { numbers: numbers(), constraints: cons }).state === 'removed', false);
    assert.equal(D.removeOption('S2', [{ id: 'leased_land', removes: [] }], []), null);
  });
});

// ---------------------------------------------------------------- M5.2

test('M5.2 10,000 random inputs give only keep, try_strip, tied, not_enough or removed', () => {
  const r = rng(20261114);
  const seen = new Set();
  for (let i = 0; i < 10000; i++) {
    const input = randomInput(r);
    const out = D.decideField(input);
    for (const opt of ['S2', 'S3']) {
      const o = out[opt];
      assert.ok(D.STATES.includes(o.state), `draw ${i} ${opt}: ${o.state}`);
      seen.add(o.state);
      if (o.state === 'removed') assert.ok(o.reasons.length > 0);
      if (o.code === 'invalid_field') assert.equal(o.message, D.needFieldMessage(o.field));
      if (['try_strip', 'tied', 'keep'].includes(o.state)) assert.equal(D.invalidField(opt, input.numbers), null);
    }
    assert.ok(['keep', 'try_strip', 'tied', 'not_enough'].includes(out.answer.state), `draw ${i} answer`);
    assertAllFinite(out, `draw ${i}`);
  }
  assert.deepEqual([...seen].sort(), [...D.STATES].sort(), 'every state appears at least once');
});

// ---------------------------------------------------------------- M5.3

test('M5.3 priority does not change the state', () => {
  const r = rng(7);
  for (let i = 0; i < 2000; i++) {
    const input = randomInput(r);
    const [a, b, c] = ['income', 'water', 'soil'].map((priority) => D.decideField({ ...input, priority }));
    for (const x of [b, c]) {
      assert.deepEqual(x.S2, a.S2);
      assert.deepEqual(x.S3, a.S3);
      assert.deepEqual(x.answer, a.answer);
    }
    assert.deepEqual([a.highlight, b.highlight, c.highlight], ['L4', 'L2', 'L1']);
  }
});

// ---------------------------------------------------------------- M5.4

describe('M5.4 money', () => {
  test('M_corn = p_corn × Y_corn − c_corn, M_wheat with the net wheat price, G_3 and G_2', () => {
    for (const c of NUM.g3_cases) {
      const n = numbers(c.override);
      assert.equal(D.marginCorn(n), c.M_corn, c.id);
      assert.equal(D.marginWheat(n), c.M_wheat, c.id);
      assert.equal(D.computeG3(n), c.G3, c.id);
      const r = D.decideOption('S3', { numbers: n, trials: { S3: STEPS.n20 } });
      assert.equal(r.M_corn, c.M_corn);
      assert.equal(r.M_wheat, c.M_wheat);
    }
    for (const c of NUM.g2_cases) assert.equal(D.computeG2(numbers(c.override)), c.G2, c.id);
  });

  test('G reads only the farmer numbers of the option', () => {
    for (const opt of ['S2', 'S3']) {
      const read = new Set();
      const spy = new Proxy(numbers({ engine_next_soy: 9, simulated_yield: 99 }), {
        get(t, k) {
          read.add(k);
          return t[k];
        },
        getOwnPropertyDescriptor(t, k) {
          read.add(k);
          return Object.getOwnPropertyDescriptor(t, k);
        },
      });
      D.decideOption(opt, { numbers: spy, trials: { [opt]: STEPS.n20 } });
      const allowed = new Set([...D.ESSENTIAL[opt], ...(opt === 'S3' ? ['q'] : [])]);
      for (const k of read) assert.ok(allowed.has(k), `${opt} read ${String(k)}`);
    }
  });

  test('engine outputs and labels in the input do not change G or the state', () => {
    const plain = s3Input('g3_just_above_4');
    const withEngine = {
      ...plain,
      numbers: { ...plain.numbers, engine_soy_gain: 50, simulated_yield: 99 },
      next_soy: { state: 'lower water risk in the model' },
      label_id: 'direction_supported_exploratory',
      scoreboard: { tests: [{ id: 'T1', status: 'pass' }] },
    };
    assert.deepEqual(D.decideField(withEngine).S3, D.decideField(plain).S3);
    assert.deepEqual(D.decideField(withEngine).S2, D.decideField(plain).S2);
  });

  test('a valid q is never applied: the wheat price is already net', () => {
    const plain = s3Input('g3_exact_4');
    for (const q of [0, 0.5, 1]) {
      const r = D.decideOption('S3', { ...plain, numbers: { ...plain.numbers, q } });
      assert.equal(r.G, 4);
      assert.equal(r.M_wheat, 1600);
    }
  });
});

// ---------------------------------------------------------------- M5.5

describe('M5.5 the declared date is in charge', () => {
  const reading = { on: { state: 'outside', k: 3, n: 8 }, edge: { state: 'at_the_edge', k: 1, n: 8 } };
  const cases = ['g3_exact_4', 'g3_just_above_4', 'g3_exact_8']; // try_strip, tied, keep
  const hlsVariants = [
    { windowReading: reading.on, hlsOn: true },
    { windowReading: reading.on, hlsOn: false },
    { windowReading: reading.edge, hlsOn: true },
    { windowReading: null, hlsOn: true },
  ];

  test('with a declared date, HLS does not change the state', () => {
    for (const id of cases) {
      for (const sowingDate of [...Object.values(WIN.dates), '2027-02-30']) {
        for (const zarcConstraint of [true, false]) {
          const states = hlsVariants.map((h) => {
            const f = D.decideField(s3Input(id, { sowingDate, zarcConstraint, ...h }));
            return [f.S2.state, f.S3.state, f.answer.state];
          });
          for (const s of states) assert.deepEqual(s, states[0], `${id} ${sowingDate} ${zarcConstraint}`);
        }
      }
    }
  });

  test('without the Zarc constraint, the warning never changes the state', () => {
    for (const id of cases) {
      const off = D.decideField(s3Input(id, { zarcConstraint: false, windowReading: null }));
      const on = D.decideField(s3Input(id, { zarcConstraint: false, windowReading: reading.on }));
      assert.equal(on.S3.state, off.S3.state, id);
      assert.equal(on.S2.state, off.S2.state, id);
      assert.ok(on.S3.flags.includes('check_window_with_bank'));
    }
  });

  test('with the constraint, no date and the warning, keep becomes "We need your sowing date."', () => {
    const r = D.decideOption('S3', s3Input('g3_exact_8', { zarcConstraint: true, windowReading: reading.on }));
    assert.equal(r.state, 'not_enough');
    assert.equal(r.code, 'need_sowing_date');
    assert.equal(r.field, 'sowing_date');
    assert.equal(r.message, 'Not enough evidence. We need your sowing date.');
  });

  test('the warning alone never gives a verdict', () => {
    for (const id of ['g3_exact_4', 'g3_just_above_4']) {
      const r = D.decideOption('S3', s3Input(id, { zarcConstraint: true, windowReading: reading.on }));
      assert.equal(r.state, D.decideOption('S3', s3Input(id)).state, id);
    }
    const few = { ...s3Input('g3_exact_8', { zarcConstraint: true, windowReading: reading.on }), trials: { S3: STEPS.n4 } };
    assert.equal(D.decideOption('S3', few).state, 'not_enough');
  });

  test('"at the edge" and the switch turned off do not ask for the date', () => {
    for (const h of [{ windowReading: reading.edge }, { windowReading: reading.on, hlsOn: false }]) {
      assert.equal(D.decideOption('S3', s3Input('g3_exact_8', { zarcConstraint: true, ...h })).state, 'keep');
    }
  });

  test('with a declared date outside the window, no keep', () => {
    for (const sowingDate of [WIN.dates.outside_both_late, WIN.dates.outside_both_early]) {
      for (const zarcConstraint of [true, false]) {
        const f = D.decideField(s3Input('g3_exact_8', { sowingDate, zarcConstraint }));
        assert.equal(f.S3.state, 'not_enough');
        assert.equal(f.S3.code, 'window_outside');
        assert.equal(f.S3.message, D.MESSAGES.windowOutside);
        assert.notEqual(f.S2.state, 'keep');
        assert.notEqual(f.answer.state, 'keep');
      }
    }
  });

  test('a declared date inside the window keeps the state, window limits included', () => {
    for (const sowingDate of [WIN.dates.inside_both, WIN.dates.corn_start, WIN.dates.corn_end]) {
      const r = D.decideOption('S3', s3Input('g3_exact_8', { sowingDate, zarcConstraint: true, windowReading: reading.on }));
      assert.equal(r.state, 'keep', sowingDate);
      assert.ok(r.flags.includes('date_in_charge'));
    }
  });

  test('with the constraint, S2 leaves when the date is outside the consortium window', () => {
    const late = D.decideOption('S2', s3Input('g3_exact_4', { sowingDate: WIN.dates.outside_both_late, zarcConstraint: true }));
    assert.equal(late.state, 'removed');
    assert.equal(late.reasons[0].id, D.ZARC_CONSORTIUM_REASON);
    const free = D.decideOption('S2', s3Input('g3_exact_4', { sowingDate: WIN.dates.outside_both_late, zarcConstraint: false }));
    assert.notEqual(free.state, 'removed');
  });

  test('a malformed declared date never lets keep through', () => {
    for (const sowingDate of ['2027-02-30', '15/02/2027', 20270215]) {
      const r = D.decideOption('S3', s3Input('g3_exact_8', { sowingDate }));
      assert.equal(r.state, 'not_enough');
      assert.equal(r.message, 'Not enough evidence. We need your sowing date.');
    }
  });

  test('both options removed: keep with both reasons, unless rule 6', () => {
    const constraints = [
      { id: 'no_grass_seeding', removes: ['S2'], why: 'no way to seed the grass' },
      { id: 'no_wheat_buyer', removes: ['S3'], why: 'no buyer for wheat' },
    ];
    const plain = D.decideField(s3Input('g3_exact_4', { constraints }));
    assert.equal(plain.answer.state, 'keep');
    assert.equal(plain.answer.reasons.length, 2);
    const late = D.decideField(s3Input('g3_exact_4', { constraints, sowingDate: WIN.dates.outside_both_late }));
    assert.equal(late.answer.state, 'not_enough');
    const noDate = D.decideField(s3Input('g3_exact_4', { constraints, zarcConstraint: true, windowReading: reading.on }));
    assert.equal(noDate.answer.message, 'Not enough evidence. We need your sowing date.');
  });
});

// ---------------------------------------------------------------- answer precedence (7.15)

test('main sentence: higher precedence first, S3 first on equal states', () => {
  const res = (option, state) => ({ option, state, code: null, field: null, message: null, reasons: [], flags: [] });
  assert.equal(D.answer(res('S2', 'tied'), res('S3', 'keep')).main, 'S2');
  assert.equal(D.answer(res('S2', 'tied'), res('S3', 'tied')).main, 'S3');
  assert.equal(D.answer(res('S2', 'removed'), res('S3', 'not_enough')).main, 'S3');
  assert.equal(D.answer(res('S2', 'try_strip'), res('S3', 'removed')).state, 'try_strip');
});

// ---------------------------------------------------------------- M5.9

describe('M5.9 invalid input never becomes a verdict', () => {
  const bad = { missing: undefined, null: null, string: '100', text: 'abc', nan: NaN, inf: Infinity, ninf: -Infinity, bool: true, object: {} };

  test('missing or non-numeric essential field', () => {
    for (const f of D.ESSENTIAL.S3) {
      for (const [kind, v] of Object.entries(bad)) {
        const n = numbers({ [f]: v });
        if (kind === 'missing') delete n[f];
        const r = D.decideOption('S3', { numbers: n, trials: { S3: STEPS.n20 } });
        assert.equal(r.state, 'not_enough', `${f} ${kind}`);
        assert.equal(r.code, 'invalid_field');
        assert.equal(r.field, f);
        assert.equal(r.message, `Not enough evidence. We need your ${D.FIELD_LABELS[f]}.`);
        assertAllFinite(r);
      }
    }
  });

  test('price or yield <= 0 and cost < 0 are invalid, cost zero is valid', () => {
    for (const f of ['p_soy', 'p_corn', 'p_wheat', 'Y_corn', 'Y_wheat']) {
      for (const v of [0, -0, -1, -1e-9]) assert.equal(D.invalidField('S3', numbers({ [f]: v })), f, `${f} ${v}`);
    }
    for (const f of ['c_corn', 'c_wheat']) {
      for (const v of [-1, -1e-9]) assert.equal(D.invalidField('S3', numbers({ [f]: v })), f);
      for (const v of [0, -0]) assert.equal(D.invalidField('S3', numbers({ [f]: v })), null);
    }
    for (const v of [-1, -1e-9]) assert.equal(D.invalidField('S2', numbers({ c_grass: v })), 'c_grass');
    assert.equal(D.invalidField('S2', numbers({ c_grass: 0 })), null);
    assert.equal(D.decideOption('S2', { numbers: numbers({ c_grass: 0 }) }).state, 'try_strip');
  });

  test('each option checks only its own fields, in a fixed order', () => {
    assert.equal(D.invalidField('S2', numbers({ Y_wheat: -1, c_corn: NaN })), null);
    assert.equal(D.invalidField('S3', numbers({ c_wheat: -1, p_corn: 0 })), 'p_corn');
    assert.equal(D.invalidField('S3', null), 'p_soy');
  });

  test('q, when sent, lies in [0, 1]', () => {
    for (const q of [-0.1, 1.1, NaN, 'x']) assert.equal(D.invalidField('S3', numbers({ q })), 'q');
    for (const q of [0, 1, undefined, null]) assert.equal(D.invalidField('S3', numbers({ q })), null);
  });

  test('labels from the screen copy replace the default words', () => {
    const r = D.decideOption('S3', { numbers: numbers({ p_soy: 0 }), labels: { p_soy: 'soybean price (R$/bag)' } });
    assert.equal(r.message, 'Not enough evidence. We need your soybean price (R$/bag).');
  });

  test('no Infinity or NaN when valid numbers overflow', () => {
    const r = D.decideField({ numbers: numbers({ p_corn: 1e200, Y_corn: 1e200 }), trials: { S3: STEPS.n20 } });
    assert.equal(r.S3.state, 'not_enough');
    assert.equal(r.S3.code, 'out_of_range');
    assert.equal(r.S3.G, null);
    assert.equal(r.S1.M_corn, null);
    assertAllFinite(r);
    const tiny = D.decideOption('S2', { numbers: numbers({ p_soy: 5e-324, c_grass: 1e10 }), trials: { S2: STEPS.n20 } });
    assert.equal(tiny.state, 'not_enough');
    assertAllFinite(tiny);
  });
});

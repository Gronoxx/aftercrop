// Written with an SI tool (Claude, Anthropic) for NASA Space Apps 2026. See AI_USE.md.
// Tests of pipeline/decision/run_states.mjs (A3 states, plan 8.7 and 11.2).
// Run: node --test tests/run_states.test.mjs   (Node 22)
// Input: the fictitious example written by pipeline.decision.precompute. No trial values.

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

import * as D from '../site/js/decision.mjs';
import { runStates, fieldInput, LABEL, REAL_LABEL, LABELS, REAL_ORIGIN } from '../pipeline/decision/run_states.mjs';

const DIR = new URL('./fixtures/decision_example/', import.meta.url);
const load = (name) => JSON.parse(readFileSync(new URL(name, DIR), 'utf8'));
const INPUT = load('states_input_m5.json');
const EXPECTED = load('states_m5.json');
const clone = (x) => JSON.parse(JSON.stringify(x));

test('the input is the fictitious example', () => {
  assert.equal(INPUT.label, 'example, fictitious');
  assert.equal(LABEL, 'example, fictitious');
  assert.deepEqual(LABELS, ['example, fictitious', 'real']);
  for (const label of [undefined, 'fictitious', 'Real']) {
    assert.throws(() => runStates({ ...INPUT, label }), /example, fictitious/);
  }
});

test('a real run keeps its label and takes only fields from the M2 outputs', () => {
  // The example fields stand in for real ones here: only the labels are under test.
  assert.throws(() => runStates({ ...INPUT, label: REAL_LABEL }), /origin/);
  const real = { ...INPUT, label: REAL_LABEL, fields: INPUT.fields.map((f) => ({ ...f, origin: REAL_ORIGIN })) };
  const out = runStates(real);
  assert.equal(out.label, REAL_LABEL);
  assert.equal(out.states_total, EXPECTED.states_total);
  assert.deepEqual(out.states, EXPECTED.states);
  // A field from the M2 outputs never passes under the fictitious label.
  assert.throws(() => runStates({ ...real, label: LABEL }), /cannot be labelled/);
});

test('same result as the file precompute wrote', () => {
  assert.deepEqual(runStates(INPUT), EXPECTED);
});

test('the CLI prints the same JSON', () => {
  const script = fileURLToPath(new URL('../pipeline/decision/run_states.mjs', import.meta.url));
  const input = fileURLToPath(new URL('states_input_m5.json', DIR));
  const r = spawnSync(process.execPath, [script, input], { encoding: 'utf8' });
  assert.equal(r.status, 0, r.stderr);
  assert.deepEqual(JSON.parse(r.stdout), EXPECTED);
});

test('one state per field, option and scenario, only the five states', () => {
  const out = runStates(INPUT);
  assert.equal(out.states_total, INPUT.fields.length * D.OPTIONS.length * INPUT.scenarios.length);
  for (const s of out.states) {
    assert.ok(D.STATES.includes(s.hls_on.state) && D.STATES.includes(s.hls_off.state));
    assert.equal(s.changed, s.hls_on.state !== s.hls_off.state);
  }
  assert.equal(out.states_changed, out.states.filter((s) => s.changed).length);
});

test('the batch calls decision.mjs: each state equals decideField on the same input', () => {
  const out = runStates(INPUT);
  for (const s of out.states) {
    const field = INPUT.fields.find((f) => f.field_id === s.field_id);
    const scen = INPUT.scenarios.find((x) => x.id === s.scenario);
    const on = D.decideField(fieldInput(field, scen, INPUT, true))[s.option];
    const off = D.decideField(fieldInput(field, scen, INPUT, false))[s.option];
    assert.deepEqual(s.hls_on, { state: on.state, code: on.code });
    assert.deepEqual(s.hls_off, { state: off.state, code: off.code });
  }
});

test('turning HLS off only removes the window warning: keep never appears with HLS on and leaves', () => {
  const out = runStates(INPUT);
  for (const s of out.states.filter((x) => x.changed)) {
    assert.equal(s.hls_off.state, 'keep');
    assert.equal(s.hls_on.state, 'not_enough');
    assert.equal(s.hls_on.code, 'need_sowing_date');
  }
});

test('no change without an "outside" reading, without the constraint, or with a declared date', () => {
  const edge = clone(INPUT);
  for (const f of edge.fields) f.windowReading = { ...f.windowReading, state: 'at_the_edge' };
  assert.equal(runStates(edge).states_changed, 0);

  const free = clone(INPUT);
  free.context.zarcConstraint = false;
  assert.equal(runStates(free).states_changed, 0);

  const dated = clone(INPUT);
  dated.context.sowingDate = '2027-02-05';
  assert.equal(runStates(dated).states_changed, 0);
});

test('a removed option stays removed with HLS on and off', () => {
  const inp = clone(INPUT);
  inp.constraints = [{ id: 'no_wheat_buyer', removes: ['S3'], why: 'example' }];
  const out = runStates(inp);
  for (const s of out.states.filter((x) => x.option === 'S3')) {
    assert.equal(s.hls_on.state, 'removed');
    assert.equal(s.hls_off.state, 'removed');
  }
});

test('bad input is refused', () => {
  assert.throws(() => runStates({ ...INPUT, fields: [] }));
  assert.throws(() => runStates({ ...INPUT, scenarios: [{ id: 'x' }] }));
  assert.throws(() => runStates({ ...INPUT, fields: [INPUT.fields[0], INPUT.fields[0]] }));
});

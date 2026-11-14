// Written with an SI tool (Claude, Anthropic) for NASA Space Apps 2026. See AI_USE.md.
// Batch states of the decision rule, for A3 and the precomputed example (plan 8.3, 8.7, 14.3).
//
// Imports site/js/decision.mjs and calls it, so the batch never reimplements the rule.
// For every field, option (S2, S3) and price scenario it computes the state with the HLS
// switch on and off. A state "changes" when the two differ. The page computes the switch
// itself in the browser (7.8): these numbers only feed ablation.json and the scoreboard.
//
// Two runs, by the label of the input, which the output keeps:
// - "example, fictitious": the synthetic example fields of tests/fixtures/;
// - "real": the real fields assembled by pipeline/decision/field_inputs.py. Every field must
//   say origin "m2_outputs", so a real label never covers example fields.
// The price scenarios are the example numbers of plan 14.3 in both runs.
//
// Run from the repository root:  node pipeline/decision/run_states.mjs <input.json>
// The result goes to stdout as JSON.

import { readFileSync } from 'node:fs';
import { pathToFileURL } from 'node:url';

import * as D from '../../site/js/decision.mjs';

export const LABEL = 'example, fictitious';
export const REAL_LABEL = 'real';
export const LABELS = Object.freeze([LABEL, REAL_LABEL]);
export const REAL_ORIGIN = 'm2_outputs';

function isObject(v) {
  return typeof v === 'object' && v !== null && !Array.isArray(v);
}

function need(cond, msg) {
  if (!cond) throw new TypeError(`run_states: ${msg}`);
}

/** Input of decideField for one field and scenario, with the switch on or off. */
export function fieldInput(field, scenario, common, hlsOn) {
  const ctx = isObject(common.context) ? common.context : {};
  return {
    numbers: scenario.numbers,
    trials: common.trials,
    constraints: Array.isArray(common.constraints) ? common.constraints : [],
    hardRules: Array.isArray(common.hardRules) ? common.hardRules : [],
    zarcWindow: field.zarcWindow,
    windowReading: field.windowReading,
    sowingDate: ctx.sowingDate ?? null,
    zarcConstraint: ctx.zarcConstraint === true,
    hlsOn,
  };
}

function pick(r) {
  return { state: r.state, code: r.code };
}

/** States for every field x option x scenario, with HLS on and off. Pure. */
export function runStates(input) {
  need(isObject(input), 'input must be an object');
  need(LABELS.includes(input.label), `input label must be "${LABEL}" or "${REAL_LABEL}"`);
  need(Array.isArray(input.fields) && input.fields.length > 0, 'fields must be a non-empty list');
  need(Array.isArray(input.scenarios) && input.scenarios.length > 0, 'scenarios must be a non-empty list');
  const ids = new Set();
  for (const f of input.fields) {
    need(isObject(f) && typeof f.field_id === 'string', 'each field needs a field_id');
    need(!ids.has(f.field_id), `field ${f.field_id} twice`);
    if (input.label === REAL_LABEL) need(f.origin === REAL_ORIGIN, `field ${f.field_id}: a real run needs origin "${REAL_ORIGIN}"`);
    else need(f.origin !== REAL_ORIGIN, `field ${f.field_id}: real M2 outputs cannot be labelled "${LABEL}"`);
    ids.add(f.field_id);
  }
  for (const s of input.scenarios) need(isObject(s) && typeof s.id === 'string' && isObject(s.numbers), 'each scenario needs id and numbers');

  const states = [];
  const answers = [];
  for (const field of input.fields) {
    for (const scenario of input.scenarios) {
      const on = D.decideField(fieldInput(field, scenario, input, true));
      const off = D.decideField(fieldInput(field, scenario, input, false));
      for (const opt of D.OPTIONS) {
        states.push({
          field_id: field.field_id,
          scenario: scenario.id,
          option: opt,
          hls_on: pick(on[opt]),
          hls_off: pick(off[opt]),
          changed: on[opt].state !== off[opt].state,
        });
      }
      answers.push({
        field_id: field.field_id,
        scenario: scenario.id,
        hls_on: pick(on.answer),
        hls_off: pick(off.answer),
        changed: on.answer.state !== off.answer.state,
      });
    }
  }
  for (const s of states) {
    need(D.STATES.includes(s.hls_on.state) && D.STATES.includes(s.hls_off.state), 'unknown state');
  }
  return {
    label: input.label,
    decision_module: 'site/js/decision.mjs',
    states,
    answers,
    states_changed: states.filter((s) => s.changed).length,
    states_total: states.length,
  };
}

function main(argv) {
  if (argv.length !== 1) {
    process.stderr.write('usage: node pipeline/decision/run_states.mjs <input.json>\n');
    return 2;
  }
  const input = JSON.parse(readFileSync(argv[0], 'utf8'));
  process.stdout.write(`${JSON.stringify(runStates(input), null, 2)}\n`);
  return 0;
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  process.exitCode = main(process.argv.slice(2));
}

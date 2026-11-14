// Written with an SI tool (Claude, Anthropic) for NASA Space Apps 2026. See AI_USE.md.
// fieldOrder (plan 13.7 rule 5, criterion M5.8). Example data, fictitious.
import test from 'node:test';
import assert from 'node:assert/strict';
import { fieldOrder } from '../site/js/decision.mjs';

const ev = (season, low, high, status = 'ok') => ({ season, low, high, status });
// Field A leaves later than B by about 10 days in three of four seasons.
const GE = {
  A: [ev('2019/20', '2020-03-01', '2020-03-11'), ev('2020/21', '2021-03-01', '2021-03-11'),
      ev('2021/22', '2022-03-01', '2022-03-11'), ev('2022/23', '2023-02-01', '2023-02-11')],
  B: [ev('2019/20', '2020-02-20', '2020-03-01'), ev('2020/21', '2021-02-20', '2021-03-01'),
      ev('2021/22', '2022-02-20', '2022-03-01'), ev('2022/23', '2023-02-01', '2023-02-11')],
};
const BOTH = { A: 'try_strip', B: 'tied' };

test('later exit in more than half of the seasons, above the floor, goes first', () => {
  const r = fieldOrder(GE, 5, BOTH);
  assert.equal(r.first, 'A');
  assert.deepEqual(r.other, ['B']);
  assert.equal(r.n, 3);
  assert.equal(r.m, 4);
});

test('a difference below the noise floor gives no field', () => {
  const r = fieldOrder(GE, 12, BOTH);
  assert.equal(r.first, null);
  assert.equal(r.reason, 'no_field_stands_out');
});

test('exactly half of the seasons is not more than half', () => {
  const r = fieldOrder(GE, 5, BOTH, ['2019/20']);
  assert.equal(r.first, 'A');   // 2 of 3 still more than half
  const r2 = fieldOrder(GE, 5, BOTH, ['2019/20', '2020/21']);
  assert.equal(r2.first, null); // 1 of 2 is only half
});

test('the seasons used to choose the fields never count', () => {
  const r = fieldOrder(GE, 5, BOTH, ['2019/20', '2020/21', '2021/22']);
  assert.equal(r.first, null);
  assert.equal(r.reason, 'no_field_stands_out');
});

test('only fields with try_strip or tied are compared', () => {
  for (const s of ['keep', 'not_enough', 'removed']) {
    const r = fieldOrder(GE, 5, { A: 'try_strip', B: s });
    assert.equal(r.first, null);
    assert.equal(r.reason, 'fewer_than_two_fields');
  }
});

test('seasons with no_data or to_confirm in a field are not comparable', () => {
  const ge = { A: GE.A.map((e) => (e.season === '2022/23' ? e : { ...e, status: 'to_confirm' })), B: GE.B };
  const r = fieldOrder(ge, 5, BOTH);
  assert.equal(r.first, null);
  assert.deepEqual(r.pairs.A.B, { n: 0, m: 1 }); // only 2022/23 is comparable
});

test('no noise floor gives no order', () => {
  assert.equal(fieldOrder(GE, null, BOTH).reason, 'no_noise_floor');
  assert.equal(fieldOrder(GE, Number.NaN, BOTH).reason, 'no_noise_floor');
});

test('with three fields, the first must beat both others', () => {
  const C = GE.B.map((e) => ({ ...e }));
  const r = fieldOrder({ ...GE, C }, 5, { ...BOTH, C: 'tied' });
  assert.equal(r.first, 'A');
  assert.deepEqual(r.other, ['B', 'C']);
  const ties = fieldOrder({ A: GE.A, B: GE.B, C: GE.A }, 5, { A: 'tied', B: 'tied', C: 'tied' });
  assert.equal(ties.first, null);
});

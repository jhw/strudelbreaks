const test = require('node:test');
const assert = require('node:assert');
const { rng: { mulberry32, randInt, randChoice, sampleUnique } } = require('../breaks.js');

test('mulberry32 is deterministic for a given seed', () => {
  const a = mulberry32(22682);
  const b = mulberry32(22682);
  for (let i = 0; i < 20; i++) assert.strictEqual(a(), b());
});

test('mulberry32 produces values in [0, 1)', () => {
  const r = mulberry32(1);
  for (let i = 0; i < 1000; i++) {
    const v = r();
    assert.ok(v >= 0 && v < 1, 'v=' + v);
  }
});

test('mulberry32 seeds differ', () => {
  const a = mulberry32(1);
  const b = mulberry32(2);
  assert.notStrictEqual(a(), b());
});

test('randInt stays within [min, max] inclusive', () => {
  const r = mulberry32(42);
  for (let i = 0; i < 1000; i++) {
    const v = randInt(r, 3, 7);
    assert.ok(v >= 3 && v <= 7 && Number.isInteger(v), 'v=' + v);
  }
});

test('randInt with min===max returns min', () => {
  const r = mulberry32(42);
  for (let i = 0; i < 10; i++) assert.strictEqual(randInt(r, 5, 5), 5);
});

test('randChoice returns an element of arr', () => {
  const r = mulberry32(42);
  const arr = ['a', 'b', 'c', 'd'];
  for (let i = 0; i < 100; i++) assert.ok(arr.includes(randChoice(r, arr)));
});

test('sampleUnique returns count distinct items by sig', () => {
  const r = mulberry32(42);
  const out = sampleUnique(r,
    () => [randInt(r, 0, 9), randInt(r, 0, 9)],
    { count: 10, sig: b => b.join(',') });
  assert.strictEqual(out.length, 10);
  const sigs = new Set(out.map(b => b.join(',')));
  assert.strictEqual(sigs.size, 10);
});

test('sampleUnique throws when space exhausted', () => {
  const r = mulberry32(42);
  assert.throws(() => {
    sampleUnique(r, () => 0, { count: 2, sig: x => x });
  }, /exhausted/);
});

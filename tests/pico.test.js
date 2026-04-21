const test = require('node:test');
const assert = require('node:assert');
const { rng: { mulberry32 }, pico: { PicoSequence, SEQUENCE_MODES } } = require('../breaks.js');

test('forward cycle repeats indices in order', () => {
  const s = new PicoSequence({ nSlices: 16, indices: [1, 3, 5], mode: 'forward' });
  assert.deepStrictEqual(s.render(6), [1, 3, 5, 1, 3, 5]);
});

test('reverse cycle repeats indices in reverse', () => {
  const s = new PicoSequence({ nSlices: 16, indices: [1, 3, 5], mode: 'reverse' });
  assert.deepStrictEqual(s.render(6), [5, 3, 1, 5, 3, 1]);
});

test('ping_pong omits endpoints on the reverse leg', () => {
  const s = new PicoSequence({ nSlices: 16, indices: [1, 3, 5], mode: 'ping_pong' });
  // cycle = [1, 3, 5, 3] — length 4
  assert.deepStrictEqual(s.render(8), [1, 3, 5, 3, 1, 3, 5, 3]);
});

test('ping_pong_repeat keeps endpoints doubled', () => {
  const s = new PicoSequence({ nSlices: 16, indices: [1, 3, 5], mode: 'ping_pong_repeat' });
  // cycle = [1, 3, 5, 5, 3, 1]
  assert.deepStrictEqual(s.render(6), [1, 3, 5, 5, 3, 1]);
});

test('random mode is deterministic given a deterministic rng', () => {
  const r1 = mulberry32(7);
  const r2 = mulberry32(7);
  const s = new PicoSequence({ nSlices: 16, indices: [1, 3, 5], mode: 'random' });
  assert.deepStrictEqual(s.render(10, r1), s.render(10, r2));
});

test('single-index sequence fills every event with that (wrapped) index', () => {
  const s = new PicoSequence({ nSlices: 16, indices: [7], mode: 'forward' });
  assert.deepStrictEqual(s.render(4), [7, 7, 7, 7]);
});

test('empty indices yields zeros', () => {
  const s = new PicoSequence({ nSlices: 16, indices: [], mode: 'forward' });
  assert.deepStrictEqual(s.render(3), [0, 0, 0]);
});

test('transpose shifts indices modulo nSlices', () => {
  const s = new PicoSequence({ nSlices: 8, indices: [0, 1, 7], mode: 'forward', transpose: 3 });
  assert.deepStrictEqual(s.render(3), [3, 4, 2]);
});

test('negative transpose wraps correctly', () => {
  const s = new PicoSequence({ nSlices: 8, indices: [0, 1, 2], mode: 'forward', transpose: -1 });
  assert.deepStrictEqual(s.render(3), [7, 0, 1]);
});

test('withMode returns a fresh instance and leaves original untouched', () => {
  const a = new PicoSequence({ nSlices: 16, indices: [1, 2], mode: 'forward' });
  const b = a.withMode('reverse');
  assert.notStrictEqual(a, b);
  assert.strictEqual(a.mode, 'forward');
  assert.strictEqual(b.mode, 'reverse');
});

test('withTranspose returns a fresh instance and leaves original untouched', () => {
  const a = new PicoSequence({ nSlices: 16, indices: [1, 2], mode: 'forward' });
  const b = a.withTranspose(5);
  assert.notStrictEqual(a, b);
  assert.strictEqual(a.transpose, 0);
  assert.strictEqual(b.transpose, 5);
});

test('random factory produces sequences with indices inside [0, nSlices)', () => {
  const r = mulberry32(123);
  for (let i = 0; i < 50; i++) {
    const s = PicoSequence.random(r, 16);
    for (const idx of s.indices) assert.ok(idx >= 0 && idx < 16, 'idx=' + idx);
    assert.ok(SEQUENCE_MODES.includes(s.mode));
  }
});

test('random factory is deterministic given a deterministic rng', () => {
  const r1 = mulberry32(99);
  const r2 = mulberry32(99);
  const a = PicoSequence.random(r1, 16);
  const b = PicoSequence.random(r2, 16);
  assert.deepStrictEqual(a.indices, b.indices);
  assert.strictEqual(a.mode, b.mode);
});

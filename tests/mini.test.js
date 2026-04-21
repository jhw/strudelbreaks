const test = require('node:test');
const assert = require('node:assert');
const { mini: { parseBreak, parsePattern, formatBreak, formatPattern } } = require('../breaks.js');

test('formatBreak produces {names}%N', () => {
  const s = formatBreak(['kick', 'snare', 'kick', 'hat'], { eventsPerCycle: 8 });
  assert.strictEqual(s, '{kick snare kick hat}%8');
});

test('parseBreak → formatBreak round trip', () => {
  const names = ['kick', 'snare', 'kick', 'hat'];
  const s = formatBreak(names, { eventsPerCycle: 8 });
  assert.deepStrictEqual(parseBreak(s), names);
});

test('parseBreak on malformed returns []', () => {
  assert.deepStrictEqual(parseBreak('nope'), []);
});

test('formatPattern renders rests as ~', () => {
  assert.strictEqual(formatPattern([0, null, 2, null]), '[0 ~ 2 ~]');
});

test('parsePattern → formatPattern round trip', () => {
  const steps = [0, 2, 4, null, 6, null, null, 14];
  assert.deepStrictEqual(parsePattern(formatPattern(steps)), steps);
});

test('parsePattern on malformed returns []', () => {
  assert.deepStrictEqual(parsePattern('nope'), []);
});

test('formatPattern accepts custom rest char', () => {
  assert.strictEqual(formatPattern([0, null, 2], { restChar: '.' }), '[0 . 2]');
});

const test = require('node:test');
const assert = require('node:assert');
const { hex: { hex2, hexPad, arrayHex } } = require('../breaks.js');

test('hex2 pads to width 2 and uppercases', () => {
  assert.strictEqual(hex2(0), '00');
  assert.strictEqual(hex2(15), '0F');
  assert.strictEqual(hex2(255), 'FF');
});

test('hexPad respects the requested width', () => {
  assert.strictEqual(hexPad(1, 4), '0001');
  assert.strictEqual(hexPad(22682, 4), '589A');
  assert.strictEqual(hexPad(0xABCDEF, 8), '00ABCDEF');
});

test('arrayHex renders digits 0-F and rest char', () => {
  assert.strictEqual(arrayHex([0, 1, null, 15, null, 10]), '01~F~A');
});

test('arrayHex accepts custom rest char', () => {
  assert.strictEqual(arrayHex([0, null, 1], { restChar: '.' }), '0.1');
});

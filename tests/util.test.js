const test = require('node:test');
const assert = require('node:assert');
const { util: { meanIndex, thinByUniforms } } = require('../breaks.js');

test('meanIndex of empty array is 0', () => {
  assert.strictEqual(meanIndex([]), 0);
});

test('meanIndex averages correctly', () => {
  assert.strictEqual(meanIndex([1, 2, 3, 4]), 2.5);
});

test('thinByUniforms keeps slot when uniform <= probability', () => {
  const shape = [10, 20, 30, 40];
  const uniforms = [0.1, 0.5, 0.7, 0.9];
  assert.deepStrictEqual(thinByUniforms(shape, uniforms, 0.5), [10, 20, null, null]);
});

test('thinByUniforms at probability 1 keeps everything', () => {
  const shape = [1, 2, 3];
  const uniforms = [0.99, 0.5, 0.01];
  assert.deepStrictEqual(thinByUniforms(shape, uniforms, 1), [1, 2, 3]);
});

test('thinByUniforms is monotonic: p1<=p2 → survivors(p1) ⊆ survivors(p2)', () => {
  const shape = [1, 2, 3, 4, 5, 6, 7, 8];
  const uniforms = [0.11, 0.33, 0.55, 0.77, 0.22, 0.88, 0.44, 0.66];
  for (let p1Step = 0; p1Step <= 10; p1Step++) {
    for (let p2Step = p1Step; p2Step <= 10; p2Step++) {
      const p1 = p1Step / 10;
      const p2 = p2Step / 10;
      const a = thinByUniforms(shape, uniforms, p1);
      const b = thinByUniforms(shape, uniforms, p2);
      for (let i = 0; i < shape.length; i++) {
        if (a[i] !== null) {
          assert.strictEqual(b[i], a[i], 'monotonicity violated at i=' + i + ' p1=' + p1 + ' p2=' + p2);
        }
      }
    }
  }
});

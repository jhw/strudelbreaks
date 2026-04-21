const test = require('node:test');
const assert = require('node:assert');
const { store: { createPersistedStore } } = require('../breaks.js');

// Hand-rolled localStorage stub: node:test has no DOM globals.
function installStorageStub() {
  const data = new Map();
  global.localStorage = {
    getItem: (k) => data.has(k) ? data.get(k) : null,
    setItem: (k, v) => { data.set(k, String(v)); },
    removeItem: (k) => { data.delete(k); },
    clear: () => { data.clear(); },
  };
  return data;
}

function silenceWarnings() {
  const original = console.warn;
  console.warn = () => {};
  return () => { console.warn = original; };
}

test('get returns null when nothing stored', () => {
  installStorageStub();
  const s = createPersistedStore({ key: 'k', schemaVersion: 1, defaultPayload: { schema: 1 } });
  assert.strictEqual(s.get(), null);
});

test('set then get round-trips through localStorage', () => {
  installStorageStub();
  const s = createPersistedStore({ key: 'k', schemaVersion: 1, defaultPayload: { schema: 1 } });
  const payload = { schema: 1, banks: [[1, 2, 3]] };
  s.set(payload);
  assert.deepStrictEqual(s.get(), payload);
});

test('get returns null on schema mismatch and logs', () => {
  installStorageStub();
  let warned = false;
  const original = console.warn;
  console.warn = () => { warned = true; };
  try {
    localStorage.setItem('k', JSON.stringify({ schema: 99, data: 'x' }));
    const s = createPersistedStore({ key: 'k', schemaVersion: 1, defaultPayload: { schema: 1 } });
    assert.strictEqual(s.get(), null);
    assert.ok(warned, 'expected a console.warn on schema mismatch');
  } finally {
    console.warn = original;
  }
});

test('clear removes the key', () => {
  installStorageStub();
  const s = createPersistedStore({ key: 'k', schemaVersion: 1, defaultPayload: { schema: 1 } });
  s.set({ schema: 1, banks: [] });
  s.clear();
  assert.strictEqual(s.get(), null);
});

test('get returns null on corrupt JSON and logs', () => {
  installStorageStub();
  const restore = silenceWarnings();
  try {
    localStorage.setItem('k', '{not json');
    const s = createPersistedStore({ key: 'k', schemaVersion: 1, defaultPayload: { schema: 1 } });
    assert.strictEqual(s.get(), null);
  } finally {
    restore();
  }
});

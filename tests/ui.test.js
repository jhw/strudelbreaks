const test = require('node:test');
const assert = require('node:assert');

// Hand-rolled DOM stub: just enough for createActionMenu's surface.
// node:test has no DOM globals, and pulling in jsdom for one widget
// would dwarf the test it's supposed to verify. The other UI primitives
// remain untested per README — they're thin wrappers over
// document.createElement whose only meaningful failure mode is a
// blank-page smoke. createActionMenu has real logic worth pinning
// (close-once idempotency, outside-click dismissal, onClose contract).
function installDOMStub() {
  const docListeners = {};

  function makeElement(tag) {
    const el = {
      tagName: tag.toUpperCase(),
      style: { cssText: '' },
      dataset: {},
      children: [],
      parent: null,
      _listeners: {},
      textContent: '',
      appendChild(child) {
        child.parent = this;
        this.children.push(child);
        return child;
      },
      remove() {
        if (!this.parent) return;
        const i = this.parent.children.indexOf(this);
        if (i >= 0) this.parent.children.splice(i, 1);
        this.parent = null;
      },
      contains(other) {
        if (other === this) return true;
        for (const c of this.children) if (c.contains(other)) return true;
        return false;
      },
      addEventListener(ev, fn) {
        (this._listeners[ev] = this._listeners[ev] || []).push(fn);
      },
      removeEventListener(ev, fn) {
        const arr = this._listeners[ev];
        if (!arr) return;
        const i = arr.indexOf(fn);
        if (i >= 0) arr.splice(i, 1);
      },
      getBoundingClientRect() {
        return { top: 10, bottom: 30, left: 0, right: 100 };
      },
      // `el.click()` — runs registered click handlers on this element
      // only (we don't simulate full DOM bubbling; tests trigger
      // outside clicks by dispatching directly on document).
      click() {
        const ev = { type: 'click', target: this, stopPropagation() {} };
        for (const fn of (this._listeners.click || []).slice()) fn(ev);
      },
    };
    return el;
  }

  global.document = {
    body: makeElement('body'),
    createElement: makeElement,
    addEventListener(ev, fn) {
      (docListeners[ev] = docListeners[ev] || []).push(fn);
    },
    removeEventListener(ev, fn) {
      const arr = docListeners[ev];
      if (!arr) return;
      const i = arr.indexOf(fn);
      if (i >= 0) arr.splice(i, 1);
    },
    getElementById(id) {
      // Minimal walk: descend from body looking for an element whose
      // .id matches. createCornerPanel sets el.id directly, then the
      // stack-ref / re-mount paths look the panel back up by id.
      function find(el) {
        if (el.id === id) return el;
        for (const c of el.children) {
          const hit = find(c);
          if (hit) return hit;
        }
        return null;
      }
      return find(this.body);
    },
    querySelectorAll() { return []; },
    // Test helper: simulate a click somewhere in the document with a
    // particular target. Runs every registered document-level click
    // listener (this is what createActionMenu's outside-click dismiss
    // listens on).
    _dispatchClick(target) {
      const ev = { type: 'click', target };
      for (const fn of (docListeners.click || []).slice()) fn(ev);
    },
  };
  global.window = { innerWidth: 1024, innerHeight: 768 };
}

installDOMStub();
const { ui: { createActionMenu, createStatusPanel } } = require('../breaks.js');

function makeAnchor() {
  const a = document.createElement('button');
  document.body.appendChild(a);
  return a;
}

function flush() {
  // createActionMenu defers its outside-click listener by setTimeout(0).
  return new Promise(resolve => setTimeout(resolve, 0));
}

test('attaches a div with one child button per item', () => {
  const anchor = makeAnchor();
  let chose = null;
  const m = createActionMenu({
    anchor,
    items: [
      { label: 'a', onSelect: () => { chose = 'a'; } },
      { label: 'b', onSelect: () => { chose = 'b'; } },
    ],
  });
  assert.strictEqual(m.element.tagName, 'DIV');
  assert.strictEqual(m.element.children.length, 2);
  assert.strictEqual(m.element.children[0].textContent, 'a');
  assert.strictEqual(m.element.children[1].textContent, 'b');
  assert.strictEqual(m.element.parent, document.body,
    'menu should be appended to document.body');
  assert.strictEqual(chose, null, 'no item fires until clicked');
  m.close();
});

test('selecting an item runs onSelect then closes the menu', () => {
  const anchor = makeAnchor();
  let chose = null;
  let closed = false;
  const m = createActionMenu({
    anchor,
    items: [
      { label: 'a', onSelect: () => { chose = 'a'; } },
      { label: 'b', onSelect: () => { chose = 'b'; } },
    ],
    onClose: () => { closed = true; },
  });
  m.element.children[1].click();
  assert.strictEqual(chose, 'b');
  assert.strictEqual(closed, true);
  assert.strictEqual(m.element.parent, null,
    'menu should be detached from the DOM after item selection');
});

test('close() is idempotent — onClose fires exactly once', () => {
  const anchor = makeAnchor();
  let closeCount = 0;
  const m = createActionMenu({
    anchor, items: [{ label: 'x', onSelect: () => {} }],
    onClose: () => { closeCount++; },
  });
  m.close();
  m.close();
  m.close();
  assert.strictEqual(closeCount, 1);
});

test('outside click closes the menu (after the open-tick deferral)', async () => {
  const anchor = makeAnchor();
  const stranger = document.createElement('div');
  document.body.appendChild(stranger);

  let closed = false;
  const m = createActionMenu({
    anchor, items: [{ label: 'x', onSelect: () => {} }],
    onClose: () => { closed = true; },
  });

  // Before the deferred listener registers, an outside click is a no-op.
  document._dispatchClick(stranger);
  assert.strictEqual(closed, false, 'open-click must not auto-dismiss');

  await flush();

  // Now the listener is wired; an outside click closes.
  document._dispatchClick(stranger);
  assert.strictEqual(closed, true);
  assert.strictEqual(m.element.parent, null);
});

test('clicking inside the menu does not dismiss via outside-click', async () => {
  const anchor = makeAnchor();
  let closed = false;
  const m = createActionMenu({
    anchor, items: [{ label: 'x', onSelect: () => {} }],
    onClose: () => { closed = true; },
  });
  await flush();
  // Dispatch a click whose target is the menu element itself —
  // createActionMenu's contains-check should treat that as inside.
  document._dispatchClick(m.element);
  assert.strictEqual(closed, false);
  m.close();
});

test('throws when items is empty or missing', () => {
  const anchor = makeAnchor();
  assert.throws(() => createActionMenu({ anchor, items: [] }),
    /needs items/);
  assert.throws(() => createActionMenu({ anchor }),
    /needs items/);
});

test('throws when anchor is missing', () => {
  assert.throws(() => createActionMenu({ items: [{ label: 'x', onSelect: () => {} }] }),
    /needs anchor/);
});

test('createStatusPanel: starts hidden, setText shows + writes, clear hides', () => {
  const p = createStatusPanel({ id: 'status-test-1' });
  assert.strictEqual(p.element.style.display, 'none',
    'panel should start hidden');
  p.setText('Exporting to ot-basic…');
  assert.strictEqual(p.element.textContent, 'Exporting to ot-basic…');
  assert.notStrictEqual(p.element.style.display, 'none',
    'setText must un-hide the panel');
  p.clear();
  assert.strictEqual(p.element.textContent, '');
  assert.strictEqual(p.element.style.display, 'none',
    'clear must re-hide the panel');
});

test('createStatusPanel: same id reuses the existing element', () => {
  const a = createStatusPanel({ id: 'status-test-2' });
  a.setText('first');
  const b = createStatusPanel({ id: 'status-test-2' });
  assert.strictEqual(a.element, b.element,
    'second call must reuse the DOM node — same id');
});

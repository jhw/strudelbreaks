# Custom sliders with patch snap-back

## Goal

Replace the four integer Strudel sliders in `tempera.strudel.js`
(`rootBreak`, `altBreak`, `pattern`, `prob`) with custom-drawn DOM
sliders in a corner panel, so that clicking a stored patch in the
captures panel snaps all four back to that patch's values.

The `delay` slider stays native — it's float, continuous, not part of a
patch.

## Feasibility (verified against Strudel source, branch `main` on
`codeberg.org/uzu/strudel`)

### `ref` is available as a bare identifier

- `packages/core/pattern.mjs:3692`:
  `export const ref = (accessor) => pure(1).withValue(() => reify(accessor())).innerJoin();`
- `packages/core/index.mjs` re-exports `pattern.mjs` wholesale.
- `packages/repl/prebake.mjs` calls `evalScope(core, …)`, and
  `evalScope` in `packages/core/evaluate.mjs` does
  `Object.entries(module).forEach(([name, val]) => globalThis[name] = val)`.
- User code runs via `Function("use strict"; return (…))()` — no
  sandboxing. `ref` is callable as a plain identifier.

### `pick` queries its selector per event

- `packages/core/pick.mjs`:
  ```js
  const _pick = (lookup, pat) => {
    …
    return pat.fmap(i => lookup[key]);
  };
  const __pick = register('pick', (lookup, pat) =>
    _pick(lookup, pat, false).innerJoin());
  ```
- `fmap` + `innerJoin` queries the selector on every event, so a
  `ref(() => currentSliders.x)` selector sees mutations live. This is
  the same mechanism Strudel's own `slider()` uses.
- The caching bug in Strudel issue #946 is specific to `lastOf` /
  `every` / `chunk` (combinators that memoize *structure* once per
  outer cycle). `pick` is not in that family.

### Why we're not monkey-patching the native sliders

Read `packages/codemirror/slider.mjs` end to end (236 lines):

- `SliderWidget.toDOM()` reads `this.value` once at construction; the
  widget does not observe `sliderValues` afterwards.
- The drag handler unconditionally does *both*:
  `view.dispatch({ changes: { from, to, insert: String(newVal) } })`
  (rewrites the source text in place) and
  `window.postMessage({ type: 'cm-slider', id, value })` (updates
  `sliderValues`).
- So a synthetic `input` event on `document.querySelector('.cm-slider
  input[type=range]')` would technically drive a slider from outside
  — but every snap would rewrite four numeric literals in the user's
  source and push four entries onto the CM undo stack. Unacceptable
  for a performance control surface.

Custom sliders avoid the source-text churn entirely and use
`ref(...)`, which is a documented primitive.

## Design

### State

```js
const currentSliders = { rootBreak: 0, altBreak: 0, pattern: 0, prob: 7 };
```

Single source of truth. Today it's a mirror of the Strudel sliders;
after this change it becomes canonical.

### Pattern-graph signals

```js
const rootBreakSig = ref(() => currentSliders.rootBreak);
const altBreakSig  = ref(() => currentSliders.altBreak);
const patternSig   = ref(() => currentSliders.pattern);
const probSig      = ref(() => currentSliders.prob);
```

The existing `.withValue(v => { currentSliders.x = v | 0; log.tick(); return v; })`
wrappers go away — the flow reverses (UI writes to state, signal
reads from state).

### Library vs template boundary

The slider widget is a generic UI primitive — it doesn't know about
breaks, patterns, or patches, only about "integer in a range, with a
label and an external setter". It belongs in `breaks.js` alongside
`createCornerPanel` / `createButton`, exported under `SB.ui`.

Domain wiring — which four sliders exist, their ranges, how they map
to `currentSliders`, what happens on snap — stays in
`tempera.strudel.js`.

### `breaks.js` additions (under `ui`)

```js
// SB.ui.createSliderRow({ label, min, max, initial, onChange })
//   → { element, setValue(v), getValue() }
//
// Builds a flex row: label + monospace value readout + <input type=range>.
// - 'input' handler calls onChange(v|0) — consumer writes to their state.
// - setValue(v) assigns to input.value and updates the readout, but does
//   NOT fire onChange — the caller is already the authority on state.
// - Styled to match the existing green-on-dark chrome (same font /
//   colour as createCornerPanel children).

// SB.ui.createSliderPanel({ corner, id, rows })
//   → { element, rows: { [key]: sliderRow }, setAll(values) }
//
// Convenience wrapper over createCornerPanel + N createSliderRow calls.
// `rows` is an array of { key, label, min, max, initial, onChange } —
// one per slider. setAll({ key: value, … }) calls setValue on each
// matching row. This is the single method tempera calls from snapTo.
```

`createSliderRow` alone would be enough; `createSliderPanel` is a thin
convenience — keep if it makes the tempera wiring cleaner, drop if
it's just an extra layer.

Unit tests under `tests/ui.test.js` remain absent for the same reason
they're absent today (see README "Coverage" section — UI primitives
are thin DOM wrappers, not unit-tested).

### Panel layout

Corner panel (position TBD during implementation — likely top-left so
it doesn't fight the captures panel). Four rows:

```
rootBreak  00  [━━●━━━━━━━━━━━━━━━]
altBreak   00  [━━━●━━━━━━━━━━━━━━]
pattern    00  [━━━━━●━━━━━━━━━━━━]
prob       07  [━━━━━━━━━━━━━━━━●━]
```

Each row: label, value readout (hex, reusing the `patchSpan`
hover-swap-to-decimal idiom), and an `<input type="range">`. Native
range inputs give us keyboard arrow support and click-drag for free.

### Tempera wiring

```js
const sliderPanel = SB.ui.createSliderPanel({
  corner: 'top-left', id: 'slider-panel',
  rows: [
    { key: 'rootBreak', label: 'rootBreak', min: 0, max: 15,  initial: 0,
      onChange: v => { currentSliders.rootBreak = v; log.tick(); } },
    { key: 'altBreak',  label: 'altBreak',  min: 0, max: 63,  initial: 0,
      onChange: v => { currentSliders.altBreak  = v; log.tick(); } },
    { key: 'pattern',   label: 'pattern',   min: 0, max: 255, initial: 0,
      onChange: v => { currentSliders.pattern   = v; log.tick(); } },
    { key: 'prob',      label: 'prob',      min: 0, max: 7,   initial: 7,
      onChange: v => { currentSliders.prob      = v; log.tick(); } },
  ],
});
```

### Patch snap (the core feature)

Every `patchSpan` rendered in the UI becomes a snap target — both the
log panel's current-patch span and every cell in the captures panel.
Clicking a captured patch sets all four sliders to the saved values
in one atomic update.

```js
function snapTo(sliders) {
  currentSliders.rootBreak = sliders.rootBreak;
  currentSliders.altBreak  = sliders.altBreak;
  currentSliders.pattern   = sliders.pattern;
  currentSliders.prob      = sliders.prob;
  sliderPanel.setAll(sliders);   // syncs the DOM thumbs + readouts
  log.tick();                     // refresh the hex display
}
```

`patchSpan` gains a `cursor: pointer` and a `click` handler that calls
`snapTo(sliders)`. Passed as a new option to `patchSpan`, so the log
panel can keep its no-op style (or we can just make it clickable
uniformly — clicking the current patch is a visible no-op, which is
fine).

The actual audio update happens on the next query — `ref(() =>
currentSliders.x)` reads the mutated state, `pick` routes to the new
row. No pattern re-evaluation required.

### Persistence (optional, deferred)

Native Strudel sliders reset their initial value on every Ctrl-Enter,
because the source text has a literal. Custom sliders would also reset
`currentSliders` to defaults on re-eval. If we want "last value
persists across re-evals," persist `currentSliders` to localStorage on
every change and restore on init. Keep the key scoped to `gistId`. Not
required for parity — add only if the workflow demands it.

## Implementation order

1. **Add `createSliderRow` (and optionally `createSliderPanel`) to
   `breaks.js`.** Export under the `ui` namespace. Update
   `README.md`'s `ui` section with the new contract. Bump the
   `v0.1.0` perf-pin URL note if we're cutting a release, otherwise
   just push to `main` and consume via the `@main` CDN URL.
2. **Wire the panel in `tempera.strudel.js`.** Add the
   `createSliderPanel` call with the four rows. Leave the existing
   Strudel sliders in place — the new panel is purely visual at this
   stage. Confirm styling and layout in the browser.
3. **Swap the signals.** Replace each
   `const rootBreakSlider = slider(0, 0, 15, 1);
    const rootBreakSig = rootBreakSlider.withValue(v => { currentSliders.rootBreak = v | 0; log.tick(); return v; });`
   pair (× 4) with `const rootBreakSig = ref(() => currentSliders.rootBreak);`.
   Delete the four `slider()` calls. The DOM sliders' `onChange`
   callbacks (wired in step 2) are now the only writers to
   `currentSliders`.
4. **Smoke test.** Drag each slider, verify audio reacts within a
   cycle. Verify keyboard arrows work on focused sliders.
5. **Add `snapTo` and wire click handlers on `patchSpan`.** Teach
   `patchSpan` to accept an optional `onClick` (or just make it
   clickable uniformly). Every captures-panel cell and the log-panel
   current-patch span gains the click binding.
6. **Test snap.** Click through existing captures, confirm all four
   slider thumbs + readouts + audio jump to the saved values.
7. **Persistence (optional, deferred).** If desired, add localStorage
   round-tripping of `currentSliders`, scoped by `gistId`.

## Risks

- **Focus/keyboard competition.** Strudel's editor captures many
  keys. An `<input type=range>` in our panel is a separate DOM focus
  target; arrow keys should work when the input is focused, but we
  should verify no global editor shortcuts swallow them.
- **No inline-in-source feedback.** We lose the thumb-embedded-in-code
  affordance that native Strudel sliders give. The corner panel plus
  the existing log panel cover the same information.
- **Re-eval resets sliders.** Same as Strudel's own slider behavior
  today. If that becomes annoying, add localStorage persistence
  (step 6).

## Not in scope

- Canvas/SVG custom-drawn sliders. Native range inputs are enough for
  v1 and keep keyboard/accessibility for free.
- Replacing the `delay` slider. It's float, continuous, not part of a
  patch; snapping to captures doesn't apply.
- Changes to the captures schema. The cells already store
  `{ rootBreak, altBreak, pattern, prob }` — `snapTo` consumes them
  directly.
- MIDI / gamepad input. Orthogonal.

## Key source references

- `packages/core/pattern.mjs:3692` — `ref`.
- `packages/core/pick.mjs` — `pick` (`fmap`+`innerJoin`, per-event).
- `packages/core/evaluate.mjs` — `evalScope`, global injection.
- `packages/codemirror/slider.mjs` — native slider widget (for
  reference; we're not touching it).
- `packages/repl/prebake.mjs` — the `evalScope(core, …,
  import('@strudel/codemirror'), …)` call that makes `ref`,
  `sliderValues`, `pure`, `innerJoin` etc. all bare-identifier
  reachable in user code.

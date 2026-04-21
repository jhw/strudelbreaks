# strudelbreaks

Reusable plumbing for Strudel breakbeat templates — a seeded RNG, a
Pico-style sequence class, mini-notation helpers, monotonic density
masking, hex formatters, corner-panel UI chrome, and a schema-gated
persisted store. Distributed over jsDelivr as a single UMD file; no
build step, no npm publish.

The namespace is breakbeat-agnostic despite the repo name: any Strudel
gist that needs deterministic generation, a corner HUD, or
localStorage-backed captures can consume the same library.

## Usage

```js
const SB_URL = 'https://cdn.jsdelivr.net/gh/jhw/strudelbreaks@main/breaks.js';

async function loadStrudelBreaks(url) {
  return new Promise((resolve, reject) => {
    const prev = document.getElementById('strudelbreaks');
    if (prev) prev.remove();
    const s = document.createElement('script');
    s.id = 'strudelbreaks';
    s.src = url;
    s.onload = () => resolve(window.StrudelBreaks);
    s.onerror = reject;
    document.head.appendChild(s);
  });
}

const SB = await loadStrudelBreaks(SB_URL);
```

Thereafter:

```js
const rng = SB.rng.mulberry32(22682);
const seq = SB.pico.PicoSequence.random(rng, 16);
const panel = SB.ui.createCornerPanel({ corner: 'bottom-right', id: 'log' });
panel.setText('patch: ABCD');
```

## Namespace

```js
window.StrudelBreaks = {
  rng:   { mulberry32, randInt, randChoice, sampleUnique },
  pico:  { PicoSequence, SEQUENCE_MODES },
  mini:  { parseBreak, parsePattern, formatBreak, formatPattern },
  util:  { meanIndex, thinByUniforms },
  hex:   { hex2, hexPad, arrayHex },
  ui:    { createCornerPanel, createButton },
  store: { createPersistedStore, downloadBlob },
};
```

See `breaks.js` for the per-function contracts; the tests under
`tests/` double as executable documentation.

### rng

- `mulberry32(seed)` — returns a deterministic `() => number` in `[0, 1)`.
- `randInt(rng, min, max)` — integer in the inclusive range.
- `randChoice(rng, arr)` — one element of `arr`.
- `sampleUnique(rng, draw, { count, sig })` — rejection-samples `count`
  distinct items (dedup by `sig(item)`); throws after `count * 100`
  attempts if the space is exhausted.

### pico

`PicoSequence` models an Erica Synths Pico SEQ: a short list of slice
indices played back through one of five modes (`forward`, `reverse`,
`ping_pong`, `ping_pong_repeat`, `random`). `withMode` / `withTranspose`
return fresh clones. `PicoSequence.random(rng, nSlices, opts)` builds a
short contiguous-with-interval run such as `[3, 5, 7]`.

### mini

- `formatBreak(names, { eventsPerCycle })` → `{name name ...}%N`
- `parseBreak(str)` → `[name, name, ...]`
- `formatPattern(steps, { restChar = '~' })` → `[index index ~ index]`
- `parsePattern(str, { restChar = '~' })` → `[index | null, ...]`

Round-trip faithful. Consumers work in structured arrays and serialize
at the edge (for Strudel's `mini()` / `fmap(mini).innerJoin()` dance).

### util

- `meanIndex(xs)` — mean of a list, `0` on empty. Used as a sort key so
  shape-major browsing moves through different regions of the source.
- `thinByUniforms(shape, uniforms, probability)` — masks slots where
  `uniforms[i] > probability`. Monotonic: `p1 <= p2` means every slot
  surviving at `p1` also survives at `p2`, so sweeping density only
  adds or removes notes without reshaping the pattern.

### hex

- `hex2(v)` — two-digit uppercase hex (`v | 0` byte).
- `hexPad(v, width)` — uppercase hex padded to `width`.
- `arrayHex(arr, { restChar = '~' })` — single-digit hex per element,
  `restChar` for `null` entries.

### ui

- `createCornerPanel({ corner, id, style? })` → `{ element, setText }`.
  Creates (or reuses, by `id`) a fixed-position monospace green-on-dark
  div and pins it to one of `top-left | top-right | bottom-left |
  bottom-right`. Append children to `element` for richer panels; use
  `setText` for plain-text HUDs.
- `createButton(label, onClick, { style? })` → `HTMLButtonElement` with
  the house style.

No knowledge of patches, breaks, or captures — those live in the
consuming template.

### store

- `createPersistedStore({ key, schemaVersion, defaultPayload })` →
  `{ get, set, clear, exportAsFile }`. `get()` returns the parsed
  payload when `payload.schema === schemaVersion`; on a mismatch it
  logs a warning and returns `null`. `set` / `clear` round-trip
  through `localStorage`. `exportAsFile(filenamePrefix)` downloads
  the current payload as a timestamped JSON file.
- `downloadBlob(filename, content, mimeType?)` — browser-only helper
  used by `exportAsFile`.

## CDN URLs

- **Perf pin** (cached, stable):
  `https://cdn.jsdelivr.net/gh/jhw/strudelbreaks@v0.1.0/breaks.js`
- **Helper dev** (cache-bust per eval):
  `https://cdn.jsdelivr.net/gh/jhw/strudelbreaks@main/breaks.js?_=${Date.now()}`
- **Exact SHA** (for "I just pushed, test it now"):
  `https://cdn.jsdelivr.net/gh/jhw/strudelbreaks@<sha>/breaks.js`

## Tests

`node:test` — zero deps, runs under Node 18+.

```
npm test
```

CI at `.github/workflows/test.yml` runs the same on every push / PR to
`main`.

Coverage: every pure helper (`rng`, `pico`, `mini`, `util`, `hex`, and
the `store` primitive behind a localStorage stub). `ui` primitives are
deliberately not unit-tested — thin wrappers over `document.createElement`
whose only real failure mode is a blank-page smoke test.

## Demo

`tempera.strudel.js` at the repo root is a thin breakbeat template
that loads this library from jsDelivr and drives it with four sliders
(`rootBreak`, `altBreak`, `pattern`, `prob`) plus a live `delay`
control. Paste it into [Strudel](https://strudel.cc/) to jam.

## Prior art

Extracted from
[`eb2cf0206b7186404125114d4c6bbcf4`](https://gist.github.com/jhw/eb2cf0206b7186404125114d4c6bbcf4),
itself drawing on
[`8ded1bb30962317684234f73ed23e889`](https://gist.github.com/jhw/8ded1bb30962317684234f73ed23e889).

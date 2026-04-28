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
  ui:    { createCornerPanel, createButton, createIconButton, createDeleteIcon, createButtonBar, createFormBar, createSliderRow, createSliderPanel, createToggleSwitch, createActionMenu, resetUI },
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
- `thinByUniforms(shape, uniforms, probability)` — keeps exactly
  `round(probability * shape.length)` slots: those with the lowest
  `uniforms[i]` (ties broken by index). Stepwise monotonic: sweeping
  `probability` up adds slots one at a time without ever removing, so
  the rhythmic shape is preserved across levels. The survivor count is
  exact — no Binomial variance.

### hex

- `hex2(v)` — two-digit uppercase hex (`v | 0` byte).
- `hexPad(v, width)` — uppercase hex padded to `width`.
- `arrayHex(arr, { restChar = '~' })` — single-digit hex per element,
  `restChar` for `null` entries.

### ui

- `createCornerPanel({ corner, id, style?, stack? })` →
  `{ element, setText }`. Creates (or reuses, by `id`) a fixed-position
  monospace green-on-dark div and pins it to one of
  `top-left | top-right | bottom-left | bottom-right`. Append children
  to `element` for richer panels; use `setText` for plain-text HUDs.
  `stack: <otherPanelId>` stacks this panel adjacent to an
  already-rendered sibling (above the ref for bottom corners, below it
  for top corners) with the same 10px gap used at the corner edge —
  so stacked blocks have consistent spacing whether they grow down
  from the top or up from the bottom. The ref panel must already have
  its final content at call time; measurement is one-shot.
- `createButton(label, onClick, { style? })` → `HTMLButtonElement` with
  the house style.
- `createIconButton(glyph, onClick, { hoverBg?, hoverColor?, disabled?, style? })` →
  a small single-glyph span inside a dark-grey circle. Hover colours
  are caller-supplied so the same primitive covers destructive (red)
  and neutral (green) actions; defaults are red-on-hover. When
  `disabled`, the button renders dimmer, takes no click, and shows no
  hover response — callers still render it so layout stays stable at
  list boundaries (e.g. a `<` move-left arrow on the first cell of a
  row). Uses `inline-flex` centring so plain ASCII glyphs (`<`, `>`,
  `x`) land optically centred regardless of font baseline quirks.
- `createDeleteIcon(onClick, { style? })` → red-hover `x` preset over
  `createIconButton` for destructive actions. No confirmation is
  wired — callers handle that at the domain layer.
- `createFormBar({ corner, id, style?, stack?, items, itemMinWidth? })` →
  `{ element }`. Horizontal corner-anchored bar that holds any DOM
  elements — buttons, toggle switches, icon buttons, even spans of
  text. `items` is the array of pre-built elements appended in
  order; `itemMinWidth` (e.g. `'90px'`) applies a uniform min-width
  to every item so the bar looks visually grid-like. Tighter
  default padding than a general panel. Lets a template split its
  toolbar into multiple independently-stacked bars and mix control
  types in one bar.
- `createButtonBar({ corner, id, style?, stack?, buttons })` →
  back-compat alias for `createFormBar` accepting the old `buttons`
  parameter name.
- `createSliderRow({ label, min, max, initial?, step?, onChange, format?, width? })` →
  `{ element, setValue, getValue }`. Flex row: label + readout +
  native `<input type=range>`. `format(v)` renders the readout
  (defaults to decimal); `width` pins the readout width in `ch`.
  `onChange(v|0)` fires on user drag; `setValue(v)` syncs thumb +
  readout without firing `onChange`, so a caller that already owns the
  authoritative value can snap without feedback loops.
- `createSliderPanel({ corner, id, style?, stack?, rows, format? })` →
  `{ element, rows, setAll }`. Corner panel containing N slider rows
  keyed by `row.key`. A panel-level `format` applies to every row, and
  the panel computes a uniform readout width from it so all rows
  align on the left edge of the range input. `setAll({ key: value, … })`
  snaps every named row at once. `stack` forwards to
  `createCornerPanel` — useful for stacking the slider panel above or
  below another corner-anchored block. Thin convenience over
  `createCornerPanel` + repeated `createSliderRow`.
- `createToggleSwitch({ label?, initial?, onChange })` →
  `{ element, setValue, getValue }`. Compact CSS on/off switch — a
  pill track with a sliding knob, optionally preceded by an inline
  text label. Designed to drop into a `createFormBar` next to
  buttons; when the value is genuinely binary, prefer this over
  `createSliderRow`.
- `createActionMenu({ anchor, items, onClose? })` →
  `{ element, close }`. Pop-up dropdown anchored under `anchor` (a
  trigger element); `items` is an array of `{ label, onSelect }`,
  appended in order as full-width buttons. Outside-click dismisses
  (the dismiss listener is registered on the next tick so the same
  click that opened the menu doesn't immediately close it). `close()`
  is idempotent. Caller owns the toggle gesture and should
  `event.stopPropagation()` on the trigger so a re-click doesn't
  reopen the menu after the outside-click listener closes it.
- `resetUI()` — removes every DOM node the library has attached.
  Templates should call this once after loading StrudelBreaks so
  widgets from a previously-pasted script don't linger. Every element
  created by `createCornerPanel` / `createButton` is tagged with
  `data-strudelbreaks="1"`; self-contained templates that don't load
  the library can do the same sweep inline:
  `document.querySelectorAll('[data-strudelbreaks]').forEach(el => el.remove())`.

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

## CDN URL

```
https://cdn.jsdelivr.net/gh/jhw/strudelbreaks@main/breaks.js?_=${Date.now()}
```

The `@main` ref + `?_=${Date.now()}` query param keeps the consumer on
the latest commit and busts jsDelivr's cache on every eval. Dev mode —
no version pinning.

## Export targets

Tempera (`app/launch/tempera.strudel.js`) persists captures to
`localStorage` and exposes a per-format export menu. Selecting a
format POSTs the in-memory payload to the deployed Lambda export
server (one Lambda per export target, all behind a single API Gateway
HTTP API), which renders the artifact via the modules in `app/export/`
and streams it back as a download. The browser saves it to
`~/Downloads/`; per-device push scripts copy from there onto the
device.

| Target | Filename | Doc |
|---|---|---|
| `ot-basic` | `<adj>-<noun>.ot.zip` — OT project zip, per-cell patterns, per-track stems on T1-T3 | [docs/export/octatrack.md](docs/export/octatrack.md) |
| `ot-doom` | `<adj>-<noun>.ot.zip` — OT project zip, megabreak-of-doom matrix chains, per-track stems on T1-T3 | [docs/export/ot-doom.md](docs/export/ot-doom.md) |
| `torso-s4` | `<adj>-<noun>.s4.zip` — Torso S-4 sample bundle, one mixed WAV per row | [docs/export/torso-s4.md](docs/export/torso-s4.md) |
| `strudel` | opens [strudel.cc](https://strudel.cc/) in a new tab and copies the rendered template to your clipboard — paste to play | [docs/export/strudel.md](docs/export/strudel.md) |
| `json` | `tempera-captures-<gistId>-<stamp>.json` — raw payload, browser-side download (no server) | — |

Both OT variants land at `.ot.zip` — the device-side tooling treats
them identically once they're zips, so push/clean don't need to know
which renderer produced a given file.

### Server

The export server is a set of AWS Lambdas (one per target) behind an
API Gateway HTTP API, deployed via Pulumi. Two stacks:

```
infra/pipeline/   ECR + CodeBuild + S3 artifacts + IAM (lambda role)
infra/app/        Four Lambda functions + HTTP API + routes + CORS
```

`scripts/stack/deploy.py` orchestrates the two-step deploy:

1. `pulumi up` on `infra/pipeline` (idempotent — almost always no-op).
2. SHA-256 the source + Dockerfile; if unchanged, reuse the last
   build's image digest. Otherwise upload `source.zip` to the
   artifacts bucket, kick off CodeBuild, capture the resulting
   image digest, persist a marker so the next run can skip.
3. `pulumi up` on `infra/app` with the new image URI as config.

```bash
# 1. Install deps locally (for tests + the deploy orchestrator)
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
pip install pulumi pulumi-aws

# 2. Configure the stack — bucket + auth token
pulumi --cwd infra/pipeline stack init dev
pulumi --cwd infra/pipeline config set oneshot_s3_uri s3://wol-samplebank/samples/
pulumi --cwd infra/app stack init dev

# 3. Deploy
source config/setenv.sh   # exports AWS_REGION, AUTH_TOKEN
python scripts/stack/deploy.py --stage dev
```

Endpoints (one Lambda per route, all on the same image — different
`image_config.commands` per Lambda):

```
POST /api/export/strudel   { payload, name?, seed? }
POST /api/export/ot-basic  { payload, name?, seed?,
                             probability?, split_stems? }
POST /api/export/ot-doom   { payload, name?, seed?, split_stems? }
POST /api/export/torso-s4  { payload, name?, seed?, source? }
```

Auth: HTTP Basic via the `AUTH_TOKEN` env var
(`username:password`). Tempera prompts on first export and stores
the credentials in `localStorage`; a 401 wipes them so a typo
re-prompts cleanly. Response is the artifact bytes (binary targets
base64-encoded by API Gateway) or text (strudel target); the
filename lives in `Content-Disposition`.

Response size cap: 6 MB (Lambda sync response ceiling). Larger
exports return `413` with a hint to use fewer rows; the eventual
fix is a presigned-URL response path, deferred until real exports
hit the limit.

### Device tooling (`tools/sync.py`)

A single `sync.py` handles push/clean/status for all devices.
Auto-detects the connected device by scanning `/Volumes/`; pass
`--device` (or the aliases `ot` / `s4`) when ambiguous. The
`<adj>-<noun>` regex guard keeps custom-named exports (anything you
generated with `--name MYPROJECT`) out of auto-batch verbs.

```
tools/sync.py                          # default = status (no args, never destructive)
tools/sync.py push                     # extract local zips onto the device
tools/sync.py clean local              # remove ~/Downloads/<adj>-<noun>.<suffix>
tools/sync.py clean remote             # remove device-side projects
tools/sync.py clean stubs              # OT-only: remove dangling non-project dirs
tools/sync.py status                   # compare local vs remote for the detected device
tools/sync.py watch                    # poll /Volumes + ~/Downloads; auto-push new zips
                                       # when the device appears (push-only, never cleans)
```

Common args on every verb: `pattern` (substring filter on project
name), `-f / --force` (no per-item prompt), `--device` (override
auto-detect). Examples:

```
tools/sync.py push -f                  # auto-detect, force-push everything
tools/sync.py push foo --device s4     # filter by 'foo', explicit S-4
tools/sync.py status --device strudel  # local-only summary (no remote)
```

Device map:

| Device | Volume | Remote root | Suffix |
|---|---|---|---|
| `octatrack` (alias `ot`) | `/Volumes/OCTATRACK` | `strudelbeats/` (gated by `project.work`; pairs with `AUDIO/projects/<NAME>/`) | `.ot.zip` |
| `torso-s4` (alias `s4`)  | `/Volumes/S4`        | `samples/strudelbeats/` | `.s4.zip` |
| `strudel`                 | — (paste into browser) | — | `.strudel.js` |

### Source rendering

The Strudel sample gist publishes both `name.wav` (the breakbeat)
and `name.json` (a [beatwav](https://github.com/jhw/beatwav)
pattern that re-synthesises the breakbeat from one-shot drum
samples). The export targets render the JSON at the captures' BPM
and the device's native sample rate; the pre-baked WAV is only used
as a legacy fallback for older WAV-only gists (torso-s4 only — the
OT targets need per-stem decomposition and are JSON-only).

The OT targets render each break **per drum stem** (kick / snare /
hat) by filtering the JSON's matched_hits per drum type. Stems map
to OT tracks T1, T2, T3 — each gets its own DJ_EQ + COMPRESSOR for
independent shaping, sharing CHORUS + DELAY on T8. Set the
`split_stems` request field to `false` (the tempera UI exposes a
toggle) to render one mixed sample per break onto T1 only — useful
for A/B-ing the OT export against the Strudel source.

JSON-mode rendering pulls one-shots from the configured S3 bucket
(`ONESHOT_S3_URI` env var, defaults to `s3://wol-samplebank/samples/`)
and mirrors them to `<tmp>/oneshots/` on first use via boto3 — on
Lambda the bucket URI is set as a Pulumi stack config and the
function's IAM role grants read on it; locally the sync inherits the
shell's AWS credentials. The shared resolver, cache layout, and
fallback rules live in `app/export/common/sample_source.py`.
Per-device sample rates live in `app/export/common/devices.py`.

The `torso-s4/` target keeps a `source` field (`'json'` or `'wav'`,
default `'json'`) on the binary export request body for the mixed-stem
rendering it needs.

The `strudel/` target is a JS template generator, not an audio
renderer. The generated `.strudel.js` loads WAVs at runtime via
`samples(gistUrl)`, exactly like `tempera.strudel.js`.

## Tests

JS library — `node:test`, zero deps, runs under Node 18+:

```
npm test
```

Python export targets — stdlib `unittest`, requires the `requirements.txt`
deps installed in a venv (`beatwav`, `octapy`, `pydub`). See
`docs/export/` for per-target documentation:

```
npm run test:py
```

Or both at once: `npm run test:all`.

CI at `.github/workflows/test.yml` runs the JS suite on every push / PR
to `main`.

Coverage:
- JS: every pure helper (`rng`, `pico`, `mini`, `util`, `hex`, and
  the `store` primitive behind a localStorage stub). `ui` primitives
  are deliberately not unit-tested — thin wrappers over
  `document.createElement` whose only real failure mode is a
  blank-page smoke test.
- Python: pure helpers per render target plus a round-trip smoke
  test that synthesises an export, runs the renderer end-to-end
  with stubbed manifest/sample fetches, and asserts on the output
  artefact. The renderers' I/O surfaces (gist fetch, audio rendering)
  are stubbed out at the function level rather than mocked deeper.

## Demo

`app/launch/tempera.strudel.js` is a thin breakbeat template that
loads this library from jsDelivr and drives it with four sliders
(`rootBreak`, `altBreak`, `pattern`, `prob`) plus a live `delay`
control. With the server running, navigate to
`http://127.0.0.1:8000/launch` (optional `?gistUser=&gistId=` query
params bake a different sample gist into the template) — strudel.cc
opens with the template embedded in the URL hash. Or copy the file
manually into [Strudel](https://strudel.cc/) to jam.

## Prior art

Extracted from
[`eb2cf0206b7186404125114d4c6bbcf4`](https://gist.github.com/jhw/eb2cf0206b7186404125114d4c6bbcf4),
itself drawing on
[`8ded1bb30962317684234f73ed23e889`](https://gist.github.com/jhw/8ded1bb30962317684234f73ed23e889).

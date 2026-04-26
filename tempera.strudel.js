// Strudel syntax quirks (single-quoted JS strings, slider-literal args,
// mini()-lifting runtime strings into Patterns): see the source gist's
// STRUDEL.md. Generic plumbing (RNG, PicoSequence, mini/hex helpers,
// panel chrome, persisted store) is loaded from strudelbreaks over
// jsDelivr — see breaks.js in this repo.

// ===== CONFIG =====
const gistUser = 'jhw';
const gistId = 'b39b2cd8b216d2e219c0c932b76e3cf7';
const gistUrl = 'https://gist.githubusercontent.com/' + gistUser + '/' + gistId + '/raw/strudel.json';

const BPM = 128;
const BEATS_PER_CYCLE = 4;
const LOOP_CYCLES = 2;
const N_SLICES = 16;
const EVENTS_PER_CYCLE = 8;

const N_BREAKS = 64;
const N_SEQUENCES = 64;
const N_PATTERN_MODES = 4;

const N_PATTERNS = N_SEQUENCES * N_PATTERN_MODES;
const N_PROBS = 8;

const SEQ_MIN_LENGTH = 2;
const SEQ_MAX_LENGTH = 5;
const SEQ_MIN_INTERVAL = 1;
const SEQ_MAX_INTERVAL = 2;

const BREAK_ALT_NAMES_MIN = 1;
const BREAK_ALT_NAMES_MAX = 1;
const BREAK_ALT_SLOTS_MIN = 1;
const BREAK_ALT_SLOTS_MAX = 2;

const SEED = 22682;

const SB_URL = 'https://cdn.jsdelivr.net/gh/jhw/strudelbreaks@main/breaks.js?_=' + Date.now();
// ==================

// ===== STRUDEL + LIBRARY SETUP =====
await samples(gistUrl);
setCps(BPM / BEATS_PER_CYCLE / 60);

const names = await fetch(gistUrl)
  .then(r => r.json())
  .then(m => Object.keys(m).filter(k => !k.startsWith('_')));

const SB = await new Promise((resolve, reject) => {
  const prev = document.getElementById('strudelbreaks');
  if (prev) prev.remove();
  const s = document.createElement('script');
  s.id = 'strudelbreaks';
  s.src = SB_URL;
  s.onload = () => resolve(window.StrudelBreaks);
  s.onerror = reject;
  document.head.appendChild(s);
});

SB.ui.resetUI();

const PATTERN_MODES = ['forward', 'reverse', 'ping_pong', 'ping_pong_repeat'];
if (PATTERN_MODES.length !== N_PATTERN_MODES) throw new Error('N_PATTERN_MODES must match PATTERN_MODES.length');

const rng = SB.rng.mulberry32(SEED);
// ===================================

// ===== BREAKBEAT DOMAIN =====
function randomiseBreak(rng, nNames, rootIdx, {
  minAltNames = 1, maxAltNames = 1, minAltSlots = 1, maxAltSlots = 2,
} = {}) {
  const slots = 4;
  const others = [];
  for (let i = 0; i < nNames; i++) if (i !== rootIdx) others.push(i);
  if (others.length === 0) throw new Error('need >=2 distinct names');

  const nAltNames = SB.rng.randInt(rng, minAltNames, Math.min(maxAltNames, others.length));
  const namePool = others.slice();
  const altNames = [];
  for (let i = 0; i < nAltNames; i++) {
    const idx = SB.rng.randInt(rng, 0, namePool.length - 1);
    altNames.push(namePool.splice(idx, 1)[0]);
  }

  const slotMin = Math.max(minAltSlots, altNames.length);
  const slotMax = Math.min(maxAltSlots, slots);
  const nAltSlots = SB.rng.randInt(rng, slotMin, Math.max(slotMin, slotMax));
  const positions = [0, 1, 2, 3];
  const altPositions = [];
  for (let i = 0; i < nAltSlots; i++) {
    const idx = SB.rng.randInt(rng, 0, positions.length - 1);
    altPositions.push(positions.splice(idx, 1)[0]);
  }

  const out = Array.from({ length: slots }, () => rootIdx);
  altPositions.forEach((p, i) => { out[p] = altNames[i % altNames.length]; });
  return out;
}

const breakStringsByRoot = [];
for (let r = 0; r < names.length; r++) {
  const rows = SB.rng.sampleUnique(rng,
    () => randomiseBreak(rng, names.length, r, {
      minAltNames: BREAK_ALT_NAMES_MIN, maxAltNames: BREAK_ALT_NAMES_MAX,
      minAltSlots: BREAK_ALT_SLOTS_MIN, maxAltSlots: BREAK_ALT_SLOTS_MAX,
    }),
    { count: N_BREAKS, sig: b => b.join(',') });
  breakStringsByRoot.push(rows.map(idxs =>
    SB.mini.formatBreak(idxs.map(i => names[i]), { eventsPerCycle: EVENTS_PER_CYCLE })));
}

const sequences = [];
for (let s = 0; s < N_SEQUENCES; s++) {
  sequences.push(SB.pico.PicoSequence.random(rng, N_SLICES, {
    minLength: SEQ_MIN_LENGTH, maxLength: SEQ_MAX_LENGTH,
    minInterval: SEQ_MIN_INTERVAL, maxInterval: SEQ_MAX_INTERVAL,
    modes: PATTERN_MODES,
  }));
}
sequences.sort((a, b) => SB.util.meanIndex(a.indices) - SB.util.meanIndex(b.indices));

const patternStringsByPattern = sequences.flatMap((seq) => {
  const uniforms = Array.from({ length: EVENTS_PER_CYCLE }, () => rng());
  return PATTERN_MODES.map((mode) => {
    const shape = seq.withMode(mode).render(EVENTS_PER_CYCLE, rng);
    const variants = [];
    for (let prob = 0; prob < N_PROBS; prob++) {
      variants.push(SB.util.thinByUniforms(shape, uniforms, (prob + 1) / N_PROBS));
    }
    return variants.map(SB.mini.formatPattern);
  });
});
// ============================

// ===== DOMAIN FORMATTERS =====
const nameIndex = Object.fromEntries(names.map((n, i) => [n, i]));

function patchHex(sliders) {
  return [sliders.rootBreak | 0, sliders.altBreak | 0, sliders.pattern | 0, sliders.prob | 0]
    .map(SB.hex.hex2).join('');
}

function patchSpan(sliders) {
  const s = document.createElement('span');
  s.textContent = patchHex(sliders);
  s.style.cssText = 'cursor:pointer';
  s.addEventListener('click', () => snapTo(sliders));
  return s;
}

function snapTo(sliders) {
  currentSliders.rootBreak = sliders.rootBreak | 0;
  currentSliders.altBreak  = sliders.altBreak  | 0;
  currentSliders.pattern   = sliders.pattern   | 0;
  currentSliders.prob      = sliders.prob      | 0;
  sliderPanel.setAll(currentSliders);
  log.tick();
}

// Math.random not the seeded rng: the seeded rng drives deterministic
// content generation at load; an interactive randomise should produce
// different results each click.
function randomise() {
  currentSliders.rootBreak = Math.floor(Math.random() * names.length);
  currentSliders.altBreak  = Math.floor(Math.random() * N_BREAKS);
  currentSliders.pattern   = Math.floor(Math.random() * N_PATTERNS);
  currentSliders.prob      = N_PROBS - 1;
  sliderPanel.setAll(currentSliders);
  log.tick();
}

function breakHex(breakStr) {
  const slugs = SB.mini.parseBreak(breakStr);
  if (slugs.length === 0) return breakStr;
  return slugs.map(n => (nameIndex[n] ?? 0).toString(16).toUpperCase()).join('');
}

function patternHex(patternStr) {
  const slices = SB.mini.parsePattern(patternStr);
  if (slices.length === 0) return patternStr;
  return SB.hex.arrayHex(slices);
}
// =============================

// ===== TOP-RIGHT STACK =====
// Panels stack top-down in this order:
//   1. addBar:          randomise + save
//   2. sliderPanel:     four sliders (custom DOM sliders own
//                       currentSliders; pattern-graph signals read from
//                       it via ref(() => …), which is exactly how
//                       Strudel's own slider() is implemented under
//                       the hood)
//   3. logPanel:        patch / break / pattern readouts
//   4. capturesToolbar: new row + export
//   5. capturesList:    captured rows, newest on top
//
// addCell / newRow / randomise are function declarations so the button
// handlers wired into addBar resolve them at click time, after every
// panel below has been created.

const currentSliders = {
  rootBreak: names.length - 1,
  altBreak:  N_BREAKS - 1,
  pattern:   N_PATTERNS - 1,
  prob:      N_PROBS - 1,
};

const addBar = SB.ui.createButtonBar({
  corner: 'top-right', id: 'add-bar',
  style: 'top:50px',
  buttons: [
    SB.ui.createButton('randomise', randomise, { style: 'min-width:90px' }),
    SB.ui.createButton('save', addCell, { style: 'min-width:90px' }),
  ],
});

const sliderPanel = SB.ui.createSliderPanel({
  corner: 'top-right', id: 'slider-panel',
  stack: 'add-bar',
  style: 'min-width:340px;max-width:520px',
  format: SB.hex.hex2,
  rows: [
    { key: 'rootBreak', label: 'rootBreak', min: 0, max: names.length - 1, initial: currentSliders.rootBreak,
      onChange: v => { currentSliders.rootBreak = v; log.tick(); } },
    { key: 'altBreak',  label: 'altBreak',  min: 0, max: N_BREAKS - 1,     initial: currentSliders.altBreak,
      onChange: v => { currentSliders.altBreak  = v; log.tick(); } },
    { key: 'pattern',   label: 'pattern',   min: 0, max: N_PATTERNS - 1,   initial: currentSliders.pattern,
      onChange: v => { currentSliders.pattern   = v; log.tick(); } },
    { key: 'prob',      label: 'prob',      min: 0, max: N_PROBS - 1,      initial: currentSliders.prob,
      onChange: v => { currentSliders.prob      = v; log.tick(); } },
  ],
});

const logPanel = SB.ui.createCornerPanel({
  corner: 'top-right', id: 'log-display',
  stack: 'slider-panel',
  style: 'padding:12px 14px;max-width:520px;white-space:pre-wrap;cursor:text',
});
let currentBreak = '', currentPattern = '', lastPatch = '';
function renderLog() {
  logPanel.element.textContent = '';
  logPanel.element.appendChild(document.createTextNode('patch:   '));
  logPanel.element.appendChild(patchSpan(currentSliders));
  logPanel.element.appendChild(document.createTextNode(
    '\nbreak:   ' + breakHex(currentBreak) +
    '\npattern: ' + patternHex(currentPattern)
  ));
}
const log = {
  setBreak: (s) => { if (s !== currentBreak) { currentBreak = s; renderLog(); } },
  setPattern: (s) => { if (s !== currentPattern) { currentPattern = s; renderLog(); } },
  tick: () => {
    const p = patchHex(currentSliders);
    if (p !== lastPatch) { lastPatch = p; renderLog(); }
  },
  get break() { return currentBreak; },
  get pattern() { return currentPattern; },
};
renderLog();

const SCHEMA_VERSION = 7;
const captureContext = {
  gistUser, gistId,
  bpm: BPM, beatsPerCycle: BEATS_PER_CYCLE, loopCycles: LOOP_CYCLES,
  nSlices: N_SLICES, eventsPerCycle: EVENTS_PER_CYCLE,
  nBreaks: N_BREAKS, nPatterns: N_PATTERNS, nProbs: N_PROBS,
};
const captureDefault = { schema: SCHEMA_VERSION, context: captureContext, banks: [] };
const capturesStore = SB.store.createPersistedStore({
  key: 'tempera:captures:' + gistId,
  schemaVersion: SCHEMA_VERSION,
  defaultPayload: captureDefault,
});
let capturesPayload = capturesStore.get() || { ...captureDefault };
capturesPayload.context = captureContext;
capturesStore.set(capturesPayload);

const SERVER_URL = 'http://127.0.0.1:8000';

const EXPORT_TARGETS = [
  { label: 'json',      kind: 'local'  },
  { label: 'strudel',   kind: 'text',   target: 'strudel'  },
  { label: 'ot-basic',  kind: 'binary', target: 'ot-basic' },
  { label: 'ot-doom',   kind: 'binary', target: 'ot-doom'  },
  { label: 'torso-s4',  kind: 'binary', target: 'torso-s4' },
];

async function postExport(endpointPath, body) {
  const r = await fetch(SERVER_URL + endpointPath, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!r.ok) {
    let detail = '';
    try { detail = await r.text(); } catch (_) { /* ignore */ }
    throw new Error('HTTP ' + r.status + ': ' + detail.slice(0, 200));
  }
  const cd = r.headers.get('Content-Disposition') || '';
  const m = cd.match(/filename="([^"]+)"/);
  const filename = m ? m[1] : 'export';
  const blob = await r.blob();
  return { filename, blob };
}

function downloadAs(blob, filename) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

async function exportViaServer(spec) {
  const endpointPath = spec.kind === 'text'
    ? '/api/export/text'
    : '/api/export/binary';
  try {
    const { filename, blob } = await postExport(endpointPath, {
      target: spec.target,
      payload: capturesPayload,
    });
    downloadAs(blob, filename);
  } catch (e) {
    console.error('[tempera] export failed:', e);
    alert(
      'Export failed: ' + e.message +
      '\n\nIs the strudelbreaks server running?\n  ./scripts/run.sh',
    );
  }
}

let exportMenu = null;
const exportBtn = SB.ui.createButton('export ▾', function (e) {
  // stopPropagation so the document-level outside-click listener
  // installed by createActionMenu never sees the toggle's own
  // re-click and re-opens the menu after we just closed it.
  e.stopPropagation();
  if (exportMenu) { exportMenu.close(); return; }
  exportMenu = SB.ui.createActionMenu({
    anchor: this,
    items: EXPORT_TARGETS.map(spec => ({
      label: spec.label,
      onSelect: spec.kind === 'local'
        ? () => capturesStore.exportAsFile('tempera-captures-' + gistId)
        : () => exportViaServer(spec),
    })),
    onClose: () => { exportMenu = null; },
  });
});
const capturesToolbar = SB.ui.createButtonBar({
  corner: 'top-right', id: 'captures-toolbar',
  stack: 'log-display',
  buttons: [
    SB.ui.createButton('new row', newRow),
    exportBtn,
  ],
});

const capturesList = SB.ui.createCornerPanel({
  corner: 'top-right', id: 'captures-list',
  stack: 'captures-toolbar',
  style: 'padding:8px 10px;max-width:600px',
});
const listEl = document.createElement('div');
listEl.style.cssText = 'overflow-y:auto;overflow-x:auto;white-space:pre;min-height:1.4em;max-height:45vh';
capturesList.element.appendChild(listEl);

function deleteRow(i) {
  if (!window.confirm('Delete row ' + i + '?')) return;
  capturesPayload.banks.splice(i, 1);
  capturesStore.set(capturesPayload);
  renderCaptures();
}

function deleteCell(i, j) {
  capturesPayload.banks[i].splice(j, 1);
  capturesStore.set(capturesPayload);
  renderCaptures();
}

function renderCaptures() {
  listEl.textContent = '';
  const banks = capturesPayload.banks;
  if (banks.length === 0) {
    capturesList.element.style.display = 'none';
    return;
  }
  capturesList.element.style.display = '';
  for (let i = banks.length - 1; i >= 0; i--) {
    const bank = banks[i];
    const row = document.createElement('div');
    // Plain block div (no flex): listEl's white-space:pre keeps the
    // row on one line and triggers horizontal scroll on overflow; icons
    // stay 16x16 because nothing flex-shrinks them.

    bank.forEach((c, j) => {
      if (j > 0) row.appendChild(document.createTextNode(' │ '));
      row.appendChild(patchSpan(c.sliders));
      row.appendChild(SB.ui.createDeleteIcon(() => deleteCell(i, j)));
    });
    row.appendChild(document.createTextNode(' │ ' + bank.length + ' │ '));
    row.appendChild(SB.ui.createDeleteIcon(() => deleteRow(i)));

    listEl.appendChild(row);
  }
}

function addCell() {
  const s = currentSliders;
  const cell = {
    t: Date.now(), seed: SEED,
    sliders: { ...s },
    break: SB.mini.parseBreak(log.break),
    pattern: SB.mini.parsePattern(log.pattern),
  };
  if (capturesPayload.banks.length === 0) capturesPayload.banks.push([]);
  capturesPayload.banks[capturesPayload.banks.length - 1].push(cell);
  capturesStore.set(capturesPayload);
  renderCaptures();
}

function newRow() {
  const banks = capturesPayload.banks;
  if (banks.length > 0 && banks[banks.length - 1].length === 0) return;
  banks.push([]);
  capturesStore.set(capturesPayload);
  renderCaptures();
}

renderCaptures();
// ===========================

// ===== PATTERN-GRAPH SIGNALS =====
const delaySlider = slider(0.5, 0, 1, 0.01);

const rootBreakSig = ref(() => currentSliders.rootBreak);
const altBreakSig  = ref(() => currentSliders.altBreak);
const patternSig   = ref(() => currentSliders.pattern);
const probSig      = ref(() => currentSliders.prob);
// =================================

// ===== MAIN =====
const breakStr = pick(rootBreakSig, breakStringsByRoot.map(row => pick(altBreakSig, row)));
const patternStr = pick(patternSig, patternStringsByPattern.map(row => pick(probSig, row)));

const break_ = breakStr.withValue(s => { log.setBreak(s); return s; }).fmap(mini).innerJoin();
const pattern = patternStr.withValue(s => { log.setPattern(s); return s; }).fmap(mini).innerJoin();

note(36).sound(break_).loopAt(LOOP_CYCLES).slice(N_SLICES, pattern).delay(delaySlider);
// ================

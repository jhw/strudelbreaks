// Strudel syntax quirks (single-quoted JS strings, slider-literal args,
// mini()-lifting runtime strings into Patterns): see the source gist's
// STRUDEL.md. Generic plumbing (RNG, PicoSequence, mini/hex helpers,
// panel chrome, persisted store) is loaded from strudelbreaks over
// jsDelivr — see breaks.js in this repo.

// ===== CONFIG =====
const gistUser = 'jhw';
const gistId = '94c124a4e74471e533868f8abb71ae08';
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
const N_PROBS = 16;

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

function patchCode(sliders) {
  return SB.hex.hex2(sliders.rootBreak | 0)
       + SB.hex.hex2(sliders.altBreak | 0)
       + SB.hex.hex2(sliders.pattern | 0)
       + SB.hex.hex2(sliders.prob | 0);
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

// ===== LOG PANEL =====
const currentSliders = { rootBreak: 0, altBreak: 0, pattern: 0, prob: 15 };
const logPanel = SB.ui.createCornerPanel({
  corner: 'bottom-right', id: 'log-display',
  style: 'padding:12px 14px;max-width:520px;white-space:pre-wrap;cursor:text',
});
let currentBreak = '', currentPattern = '', lastPatch = '';
function renderLog() {
  logPanel.setText(
    'patch:   ' + patchCode(currentSliders) + '\n' +
    'break:   ' + breakHex(currentBreak) + '\n' +
    'pattern: ' + patternHex(currentPattern)
  );
}
const log = {
  setBreak: (s) => { if (s !== currentBreak) { currentBreak = s; renderLog(); } },
  setPattern: (s) => { if (s !== currentPattern) { currentPattern = s; renderLog(); } },
  tick: () => {
    const p = patchCode(currentSliders);
    if (p !== lastPatch) { lastPatch = p; renderLog(); }
  },
  get break() { return currentBreak; },
  get pattern() { return currentPattern; },
};
// =====================

// ===== CAPTURES PANEL =====
const SCHEMA_VERSION = 6;
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

const capturesPanel = SB.ui.createCornerPanel({
  corner: 'top-right', id: 'captures-display',
  style: 'top:50px;padding:8px 10px;max-height:45vh;max-width:600px;display:flex;flex-direction:column',
});

const btnBar = document.createElement('div');
btnBar.style.cssText = 'display:flex;gap:4px;margin-bottom:6px;align-items:center';
const listEl = document.createElement('div');
listEl.style.cssText = 'overflow-y:auto;overflow-x:auto;white-space:pre;min-height:1.4em';

function renderCaptures() {
  const banks = capturesPayload.banks;
  if (banks.length === 0) { listEl.textContent = '(no captures)'; return; }
  const iw = String(banks.length - 1).length;
  const lines = [];
  for (let i = banks.length - 1; i >= 0; i--) {
    const row = banks[i].map(c => patchCode(c.sliders)).join('  ');
    lines.push(String(i).padStart(iw, ' ') + ' │ ' + row);
  }
  listEl.textContent = lines.join('\n');
}

function addCell() {
  const s = currentSliders;
  const currentBank = capturesPayload.banks[capturesPayload.banks.length - 1] || [];
  for (const existing of currentBank) {
    const es = existing.sliders;
    if (existing.seed === SEED
        && es.rootBreak === s.rootBreak && es.altBreak === s.altBreak
        && es.pattern === s.pattern && es.prob === s.prob) {
      console.warn('[tempera] skipping add: identical patch already in current row');
      return;
    }
  }
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

function clearAll() {
  if (!window.confirm('Clear all captures for this gist?')) return;
  capturesStore.clear();
  capturesPayload = { schema: SCHEMA_VERSION, context: captureContext, banks: [] };
  renderCaptures();
}

btnBar.appendChild(SB.ui.createButton('new row', newRow));
btnBar.appendChild(SB.ui.createButton('add cell', addCell));
const spacer = document.createElement('span');
spacer.style.cssText = 'flex:1';
btnBar.appendChild(spacer);
btnBar.appendChild(SB.ui.createButton('export', () => capturesStore.exportAsFile('tempera-captures-' + gistId)));
btnBar.appendChild(SB.ui.createButton('clear', clearAll));

capturesPanel.element.appendChild(btnBar);
capturesPanel.element.appendChild(listEl);
renderCaptures();
// ==========================

// ===== PERFORMANCE CONTROLS =====
// Slider ranges must be numeric literals (Strudel scans source text to
// render the UI before evaluating). 255 == N_PATTERNS - 1; keep in sync
// with CONFIG.
const rootBreakSlider = slider(0, 0, 15, 1);
const altBreakSlider = slider(0, 0, 63, 1);
const patternSlider = slider(0, 0, 255, 1);
const probSlider = slider(15, 0, 15, 1);
const delaySlider = slider(0.5, 0, 1, 0.01);

const rootBreakSig = rootBreakSlider.withValue(v => { currentSliders.rootBreak = v | 0; log.tick(); return v; });
const altBreakSig = altBreakSlider.withValue(v => { currentSliders.altBreak = v | 0; log.tick(); return v; });
const patternSig = patternSlider.withValue(v => { currentSliders.pattern = v | 0; log.tick(); return v; });
const probSig = probSlider.withValue(v => { currentSliders.prob = v | 0; log.tick(); return v; });
// ================================

// ===== MAIN =====
const breakStr = pick(rootBreakSig, breakStringsByRoot.map(row => pick(altBreakSig, row)));
const patternStr = pick(patternSig, patternStringsByPattern.map(row => pick(probSig, row)));

const break_ = breakStr.withValue(s => { log.setBreak(s); return s; }).fmap(mini).innerJoin();
const pattern = patternStr.withValue(s => { log.setPattern(s); return s; }).fmap(mini).innerJoin();

note(36).sound(break_).loopAt(LOOP_CYCLES).slice(N_SLICES, pattern).delay(delaySlider);
// ================

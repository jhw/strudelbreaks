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

// Non-blocking status display, in place of window.alert. The Web
// Audio context pauses while a modal alert / confirm is open, so
// every user-visible error needs to flow through this transient
// corner panel + console instead. Auto-hides after 5s; the latest
// message replaces any prior one.
const notifyPanel = SB.ui.createCornerPanel({
  corner: 'bottom-right', id: 'notify',
  style: 'min-width:240px;max-width:480px',
});
notifyPanel.element.style.display = 'none';
let notifyTimer = null;
function notify(msg) {
  console.warn('[tempera]', msg);
  notifyPanel.setText(msg);
  notifyPanel.element.style.display = '';
  if (notifyTimer) clearTimeout(notifyTimer);
  notifyTimer = setTimeout(() => {
    notifyPanel.element.style.display = 'none';
    notifyTimer = null;
  }, 5000);
}

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
// different results each click. Probability defaults to 75%
// (`prob+1)/N_PROBS == 0.75`, i.e. slider step `0.75 * N_PROBS - 1`)
// rather than full so the rendered shape has visible thinning out of
// the box.
const RANDOMISE_PROB_DEFAULT = Math.round(0.75 * N_PROBS) - 1;

function randomise() {
  currentSliders.rootBreak = Math.floor(Math.random() * names.length);
  currentSliders.altBreak  = Math.floor(Math.random() * N_BREAKS);
  currentSliders.pattern   = Math.floor(Math.random() * N_PATTERNS);
  currentSliders.prob      = RANDOMISE_PROB_DEFAULT;
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

// Deployed Lambda + API Gateway URL. Override per-environment by
// editing this constant in the pasted script — the source on jsDelivr
// stays unauthenticated, so anyone reading it doesn't see anything
// sensitive.
const SERVER_URL = 'https://YOUR-API-ID.execute-api.YOUR-REGION.amazonaws.com';

// HTTP Basic credentials for the deployed server, kept in
// localStorage. Prompted on first export; "forget" via the export
// menu wipes them. Never embedded in the script source on jsDelivr.
const AUTH_KEY = 'tempera:auth:' + gistId;

function getAuthCreds() {
  try {
    const raw = window.localStorage.getItem(AUTH_KEY);
    if (!raw) return null;
    const v = JSON.parse(raw);
    if (v && typeof v.user === 'string' && typeof v.pass === 'string') return v;
  } catch (_) { /* fall through */ }
  return null;
}

function setAuthCreds(creds) {
  if (creds) window.localStorage.setItem(AUTH_KEY, JSON.stringify(creds));
  else window.localStorage.removeItem(AUTH_KEY);
}

function promptForCreds() {
  const u = window.prompt('Server username:');
  if (!u) return null;
  const p = window.prompt('Server password:');
  if (!p) return null;
  const creds = { user: u, pass: p };
  setAuthCreds(creds);
  return creds;
}

function authHeader() {
  const c = getAuthCreds() || promptForCreds();
  if (!c) return null;
  // btoa is Latin-1 only; UTF-8 encode in case the password has
  // non-ASCII characters.
  const bytes = new TextEncoder().encode(c.user + ':' + c.pass);
  let bin = '';
  for (let i = 0; i < bytes.length; i++) bin += String.fromCharCode(bytes[i]);
  return 'Basic ' + btoa(bin);
}

const EXPORT_TARGETS = [
  { label: 'json',      kind: 'local'  },
  { label: 'strudel',   kind: 'text',   target: 'strudel'  },
  { label: 'ot-basic',  kind: 'binary', target: 'ot-basic' },
  { label: 'ot-doom',   kind: 'binary', target: 'ot-doom'  },
  { label: 'torso-s4',  kind: 'binary', target: 'torso-s4' },
];

async function postExport(target, body) {
  const auth = authHeader();
  if (!auth) throw new Error('auth credentials required');
  const r = await fetch(SERVER_URL + '/api/export/' + target, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Authorization': auth,
    },
    body: JSON.stringify(body),
  });
  if (r.status === 401 || r.status === 403) {
    // Wipe stored creds so the next attempt re-prompts; one bad
    // password shouldn't permanently lock the user out.
    setAuthCreds(null);
    throw new Error('HTTP ' + r.status + ': bad credentials (forgotten — try again)');
  }
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

// Per-target export config. Captured here (not on the request body
// itself) because the toggle UI lives outside the export menu — when
// the user picks 'ot-basic' / 'ot-doom' we read the current state of
// these knobs at send time.
const exportConfig = {
  splitStems: true,  // OT split-stems mode (T1=kick / T2=snare / T3=hat)
};

async function exportViaServer(spec) {
  const body = { payload: capturesPayload };
  if (spec.target === 'ot-basic' || spec.target === 'ot-doom') {
    body.split_stems = exportConfig.splitStems;
  }
  try {
    const { filename, blob } = await postExport(spec.target, body);
    downloadAs(blob, filename);
  } catch (e) {
    console.error('[tempera] export failed:', e);
    notify('Export failed: ' + e.message);
  }
}

// Open strudel.cc in a new tab with the rendered template embedded
// in the URL hash, so the new tab loads our export rather than
// whatever strudel.cc's localStorage last cached. The hash form is
// `https://strudel.cc/#<base64-of-utf8>` — strudel.cc decodes the
// hash on first paint and drops the program straight into the
// editor. We also copy the text to the clipboard as a manual
// fallback in case the URL form is too large or strudel.cc's hash
// reader changes.
async function openInStrudel(spec) {
  let filename, blob, text;
  try {
    ({ filename, blob } = await postExport(spec.target, {
      payload: capturesPayload,
    }));
    text = await blob.text();
  } catch (e) {
    console.error('[tempera] strudel export failed:', e);
    notify('Strudel export failed: ' + e.message);
    return;
  }

  // Best-effort clipboard fallback. Browsers reject writeText if the
  // page isn't focused — strudel.cc steals focus on window.open — so
  // we copy first, then open the tab.
  try {
    await navigator.clipboard.writeText(text);
  } catch (e) {
    console.warn('[tempera] clipboard write blocked:', e);
  }

  // btoa only handles Latin-1; encode UTF-8 first. Chunked
  // String.fromCharCode so very large exports don't blow the call
  // stack on the spread.
  const bytes = new TextEncoder().encode(text);
  let bin = '';
  for (let i = 0; i < bytes.length; i += 0x8000) {
    bin += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
  }
  const url = 'https://strudel.cc/#' + btoa(bin);
  window.open(url, '_blank');
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
        : spec.target === 'strudel'
          ? () => openInStrudel(spec)
          : () => exportViaServer(spec),
    })),
    onClose: () => { exportMenu = null; },
  });
});
// Captures toolbar: action buttons in their own bar, export-config
// toggle in a second bar stacked beneath. Splitting them lets the
// action buttons share a consistent min-width while the toggle keeps
// its natural label+track size without forcing a wide gutter.
const capturesToolbar = SB.ui.createFormBar({
  corner: 'top-right', id: 'captures-toolbar',
  stack: 'log-display',
  itemMinWidth: '90px',
  items: [
    SB.ui.createButton('new row', newRow),
    exportBtn,
  ],
});

const splitStemsToggle = SB.ui.createToggleSwitch({
  label: 'split stems',
  initial: exportConfig.splitStems,
  onChange: (v) => { exportConfig.splitStems = v; },
});
const exportConfigBar = SB.ui.createFormBar({
  corner: 'top-right', id: 'export-config-bar',
  stack: 'captures-toolbar',
  items: [splitStemsToggle.element],
});

const capturesList = SB.ui.createCornerPanel({
  corner: 'top-right', id: 'captures-list',
  stack: 'export-config-bar',
  style: 'padding:8px 10px;max-width:600px',
});
const listEl = document.createElement('div');
// Vertical scroll only on the list; horizontal scroll lives on each
// row's left half (the patterns), so the row count + delete on the
// right stay anchored even when there are too many cells to fit.
listEl.style.cssText = 'overflow-y:auto;min-height:1.4em;max-height:45vh';
capturesList.element.appendChild(listEl);

function deleteRow(i) {
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

    // Two-column flex row: left holds the cell list (left-justified,
    // own horizontal scroll), right holds the count + row-delete
    // (right-justified, never scrolls out of view). `min-width:0` on
    // the left child is the standard flex trick for letting an
    // overflowing inline-content child actually shrink instead of
    // pushing the whole row wide.
    const row = document.createElement('div');
    row.style.cssText = 'display:flex;align-items:center;gap:8px';

    const cells = document.createElement('div');
    cells.style.cssText = 'flex:1;min-width:0;overflow-x:auto;white-space:pre';
    bank.forEach((c, j) => {
      if (j > 0) cells.appendChild(document.createTextNode(' │ '));
      cells.appendChild(patchSpan(c.sliders));
      cells.appendChild(SB.ui.createDeleteIcon(() => deleteCell(i, j)));
    });

    const trail = document.createElement('div');
    trail.style.cssText = 'flex:0 0 auto;display:flex;align-items:center;white-space:pre';
    trail.appendChild(document.createTextNode('│ ' + bank.length + ' │ '));
    trail.appendChild(SB.ui.createDeleteIcon(() => deleteRow(i)));

    row.appendChild(cells);
    row.appendChild(trail);
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

// Auto-randomise on page load so each fresh paste lands on a
// different patch — the deterministic seeded rng generates the
// breaks/patterns vocab; this picks a fresh starting point inside
// that vocab via Math.random.
randomise();
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

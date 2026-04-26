// strudelbreaks — extracted plumbing for Strudel breakbeat templates.
// Attaches to window.StrudelBreaks in the browser and exports the same
// shape via module.exports under Node (for unit tests). See CORE.md in
// the source gist for the extraction boundary: library = generic
// plumbing + UI chrome primitives; template = domain content.

(function (root, factory) {
  if (typeof module === 'object' && module.exports) {
    module.exports = factory();
  } else {
    root.StrudelBreaks = factory();
  }
})(typeof self !== 'undefined' ? self : this, function () {

  // ===== RNG =====
  function mulberry32(seed) {
    let t = seed >>> 0;
    return function () {
      t = (t + 0x6D2B79F5) >>> 0;
      let r = t;
      r = Math.imul(r ^ (r >>> 15), r | 1);
      r ^= r + Math.imul(r ^ (r >>> 7), r | 61);
      return ((r ^ (r >>> 14)) >>> 0) / 4294967296;
    };
  }

  function randInt(rng, min, max) {
    return Math.floor(rng() * (max - min + 1)) + min;
  }

  function randChoice(rng, arr) {
    return arr[Math.floor(rng() * arr.length)];
  }

  // Rejection-sample: call `draw()` until `count` distinct items (by
  // `sig`) are collected. Throws after count*100 attempts so misconfigs
  // fail loudly rather than looping forever.
  function sampleUnique(rng, draw, { count, sig }) {
    const out = [];
    const seen = new Set();
    const maxTries = count * 100;
    let tries = 0;
    while (out.length < count) {
      if (++tries > maxTries) {
        throw new Error('[strudelbreaks] sampleUnique exhausted: got ' + out.length + '/' + count);
      }
      const item = draw();
      const s = sig(item);
      if (seen.has(s)) continue;
      seen.add(s);
      out.push(item);
    }
    return out;
  }

  // ===== PicoSequence =====
  const SEQUENCE_MODES = ['forward', 'reverse', 'ping_pong', 'ping_pong_repeat', 'random'];

  class PicoSequence {
    constructor({ nSlices, indices, mode, transpose = 0 }) {
      this.nSlices = nSlices;
      this.indices = indices.slice();
      this.mode = mode;
      this.transpose = transpose;
    }

    static random(rng, nSlices, {
      minLength = 2,
      maxLength = 5,
      minInterval = 1,
      maxInterval = 2,
      modes = SEQUENCE_MODES,
    } = {}) {
      const mode = randChoice(rng, modes);
      const length = randInt(rng, minLength, Math.min(maxLength, nSlices));
      const intervalCap = length > 1 ? Math.floor((nSlices - 1) / (length - 1)) : maxInterval;
      const interval = randInt(rng, minInterval, Math.max(minInterval, Math.min(maxInterval, intervalCap)));
      const offsetMax = Math.max(0, nSlices - 1 - (length - 1) * interval);
      const offset = randInt(rng, 0, offsetMax);
      const indices = Array.from({ length }, (_, i) => offset + i * interval);
      return new PicoSequence({ nSlices, indices, mode });
    }

    get length() { return this.indices.length; }

    clone() {
      return new PicoSequence({
        nSlices: this.nSlices,
        indices: this.indices,
        mode: this.mode,
        transpose: this.transpose,
      });
    }

    withMode(mode) { const c = this.clone(); c.mode = mode; return c; }
    withTranspose(transpose) { const c = this.clone(); c.transpose = transpose; return c; }

    render(events, rng) {
      const n = this.indices.length;
      const wrap = (i) => ((i + this.transpose) % this.nSlices + this.nSlices) % this.nSlices;
      if (n === 0) return Array(events).fill(0);
      if (n === 1) return Array(events).fill(wrap(this.indices[0]));
      if (this.mode === 'random') {
        return Array.from({ length: events }, () => wrap(randChoice(rng, this.indices)));
      }
      let cycle;
      if (this.mode === 'reverse') {
        cycle = this.indices.slice().reverse();
      } else if (this.mode === 'ping_pong') {
        cycle = this.indices.slice(0, -1).concat(this.indices.slice().reverse().slice(0, -1));
      } else if (this.mode === 'ping_pong_repeat') {
        cycle = this.indices.concat(this.indices.slice().reverse());
      } else {
        cycle = this.indices;
      }
      return Array.from({ length: events }, (_, i) => wrap(cycle[i % cycle.length]));
    }
  }

  // ===== Mini-notation =====
  function formatBreak(names, { eventsPerCycle }) {
    return '{' + names.join(' ') + '}%' + eventsPerCycle;
  }

  function formatPattern(steps, { restChar = '~' } = {}) {
    return '[' + steps.map(s => s === null ? restChar : s).join(' ') + ']';
  }

  function parseBreak(breakStr) {
    const m = breakStr.match(/^\{([^}]+)\}/);
    return m ? m[1].trim().split(/\s+/) : [];
  }

  function parsePattern(patternStr, { restChar = '~' } = {}) {
    const m = patternStr.match(/^\[([^\]]+)\]/);
    if (!m) return [];
    return m[1].trim().split(/\s+/).map(t => t === restChar ? null : parseInt(t, 10));
  }

  // ===== Util =====
  function meanIndex(xs) {
    return xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : 0;
  }

  // Rank-based density masking: keeps exactly round(probability * n)
  // slots — the ones with the lowest uniforms[i] (ties broken by index).
  // Callers supply uniforms once per shape, so sweeping `probability` is
  // stepwise monotonic: each increment adds slots without ever removing,
  // and the survivor count is exact rather than a Binomial draw.
  function thinByUniforms(shape, uniforms, probability) {
    const n = shape.length;
    const keep = Math.round(probability * n);
    if (keep <= 0) return shape.map(() => null);
    if (keep >= n) return shape.slice();
    const kept = new Set(
      uniforms
        .map((u, i) => [u, i])
        .sort((a, b) => a[0] - b[0] || a[1] - b[1])
        .slice(0, keep)
        .map(([, i]) => i)
    );
    return shape.map((v, i) => kept.has(i) ? v : null);
  }

  // ===== Hex =====
  function hexPad(value, width) {
    return (value >>> 0).toString(16).toUpperCase().padStart(width, '0');
  }

  function hex2(value) {
    return hexPad(value, 2);
  }

  function arrayHex(arr, { restChar = '~' } = {}) {
    return arr.map(v => v === null ? restChar : v.toString(16).toUpperCase()).join('');
  }

  // ===== UI =====
  const PANEL_GAP = 10;
  const CORNER_STYLES = {
    'top-left':     'top:'    + PANEL_GAP + 'px;left:'  + PANEL_GAP + 'px',
    'top-right':    'top:'    + PANEL_GAP + 'px;right:' + PANEL_GAP + 'px',
    'bottom-left':  'bottom:' + PANEL_GAP + 'px;left:'  + PANEL_GAP + 'px',
    'bottom-right': 'bottom:' + PANEL_GAP + 'px;right:' + PANEL_GAP + 'px',
  };

  const PANEL_BASE_STYLE = [
    'position:fixed',
    'background:rgba(0,0,0,0.75)',
    'color:#0f0',
    'padding:10px 12px',
    'font-family:ui-monospace,Menlo,Consolas,monospace',
    'font-size:12px',
    'line-height:1.4',
    'z-index:99999',
    'border-radius:6px',
    'user-select:text',
    '-webkit-user-select:text',
  ].join(';');

  // `stack` stacks this panel adjacent to another already-rendered
  // panel (reference by id). For bottom-* corners the new panel sits
  // above the ref; for top-* corners it sits below. The gap matches
  // the corner edge margin so vertically-stacked blocks have the same
  // spacing as the margin to the viewport edge. The ref panel must
  // already be in the DOM with its final content at call time —
  // measurement is one-shot via getBoundingClientRect, which gives the
  // ref's real viewport position rather than just its height, so chains
  // deeper than one level stack correctly.
  function createCornerPanel({ corner, id, style = '', stack }) {
    const pos = CORNER_STYLES[corner];
    if (!pos) throw new Error('[strudelbreaks] unknown corner: ' + corner);
    let element = id ? document.getElementById(id) : null;
    if (!element) {
      element = document.createElement('div');
      if (id) element.id = id;
      document.body.appendChild(element);
    }
    element.dataset.strudelbreaks = '1';
    element.textContent = '';
    let stackStyle = '';
    if (stack) {
      const refEl = document.getElementById(stack);
      if (!refEl) {
        console.warn('[strudelbreaks] stack ref "' + stack + '" not found; ignoring');
      } else {
        const rect = refEl.getBoundingClientRect();
        if (corner.startsWith('bottom')) {
          stackStyle = 'bottom:' + (window.innerHeight - rect.top + PANEL_GAP) + 'px';
        } else {
          stackStyle = 'top:' + (rect.bottom + PANEL_GAP) + 'px';
        }
      }
    }
    element.style.cssText = PANEL_BASE_STYLE + ';' + pos
      + (style ? ';' + style : '')
      + (stackStyle ? ';' + stackStyle : '');
    return {
      element,
      setText(text) { element.textContent = text; },
    };
  }

  const BUTTON_BASE_STYLE = [
    'background:#0a0',
    'color:#000',
    'border:none',
    'padding:2px 8px',
    'font:inherit',
    'cursor:pointer',
    'border-radius:3px',
  ].join(';');

  function createButton(label, onClick, { style = '' } = {}) {
    const b = document.createElement('button');
    b.dataset.strudelbreaks = '1';
    b.textContent = label;
    b.style.cssText = BUTTON_BASE_STYLE + (style ? ';' + style : '');
    b.addEventListener('click', onClick);
    return b;
  }

  // Small single-glyph icon button inside a circular dark-grey
  // background. Hover colours are caller-supplied so the same primitive
  // covers destructive (red) and neutral (green) actions. When
  // `disabled`, the button renders dimmer, takes no click, and shows
  // no hover response — callers still render it so layout stays stable
  // at list boundaries.
  // inline-flex + align/justify:center puts the glyph's bounding box
  // dead centre regardless of font baseline quirks — ASCII '<' '>' 'x'
  // all land in the same visual spot, which text-align + line-height
  // tricks on inline-block can't guarantee. Symmetric horizontal margin
  // so neighbouring glyphs/text sit evenly on both sides.
  const ICON_BUTTON_BASE_STYLE = [
    'display:inline-flex',
    'align-items:center',
    'justify-content:center',
    'box-sizing:border-box',
    'flex:0 0 auto',
    'width:16px', 'height:16px',
    'min-width:16px', 'min-height:16px',
    'border-radius:50%',
    'font-family:ui-monospace,Menlo,Consolas,monospace',
    'font-size:11px',
    'font-weight:bold',
    'margin:0 3px',
    'vertical-align:middle',
    'user-select:none',
    '-webkit-user-select:none',
  ].join(';');

  function createIconButton(glyph, onClick, {
    hoverBg = '#a33', hoverColor = '#fff',
    disabled = false, style = '',
  } = {}) {
    const el = document.createElement('span');
    el.dataset.strudelbreaks = '1';
    el.textContent = glyph;
    const baseBg = disabled ? '#2a2a2a' : '#444';
    const baseColor = disabled ? '#666' : '#bbb';
    el.style.cssText = ICON_BUTTON_BASE_STYLE
      + ';cursor:' + (disabled ? 'default' : 'pointer')
      + ';background:' + baseBg
      + ';color:' + baseColor
      + (style ? ';' + style : '');
    if (!disabled) {
      el.addEventListener('mouseenter', () => { el.style.background = hoverBg; el.style.color = hoverColor; });
      el.addEventListener('mouseleave', () => { el.style.background = baseBg; el.style.color = baseColor; });
      el.addEventListener('click', onClick);
    }
    return el;
  }

  // Red-hover preset over createIconButton for destructive actions.
  // No confirmation is wired — callers handle that at the domain layer.
  function createDeleteIcon(onClick, { style = '' } = {}) {
    return createIconButton('x', onClick, { hoverBg: '#a33', hoverColor: '#fff', style });
  }

  // Thin convenience over createCornerPanel: a flex row of buttons as
  // its own corner-anchored widget, so a template can split toolbars
  // into multiple independently-positioned bars. `buttons` is an array
  // of pre-built elements (typically from createButton / createIconButton)
  // appended in order. Tighter default padding than a general panel so
  // the bar looks like a bar, not a panel.
  function createButtonBar({ corner, id, style = '', stack, buttons }) {
    const barStyle = 'display:flex;gap:4px;align-items:center;padding:6px 10px'
      + (style ? ';' + style : '');
    const panel = createCornerPanel({ corner, id, style: barStyle, stack });
    for (const btn of buttons) panel.element.appendChild(btn);
    return { element: panel.element };
  }

  // Integer-range DOM slider row: label + readout + native
  // <input type=range>. `format(v)` renders the readout (defaults to
  // decimal); `width` pins the readout width in ch (defaults to
  // max(format(min).length, format(max).length)) so a panel can align
  // multiple rows. `onChange(v|0)` fires on drag; setValue syncs thumb
  // + readout without firing onChange, so a caller that already owns
  // the authoritative value can snap without feedback loops.
  function createSliderRow({ label, min, max, initial = min, step = 1, onChange, format, width }) {
    const fmt = format || ((v) => String(v | 0));
    const w = width != null ? width : Math.max(fmt(min).length, fmt(max).length);

    const row = document.createElement('div');
    row.dataset.strudelbreaks = '1';
    row.style.cssText = 'display:flex;align-items:center;gap:8px;margin:2px 0';

    const labelEl = document.createElement('span');
    labelEl.textContent = label;
    labelEl.style.cssText = 'min-width:72px';

    const readoutEl = document.createElement('span');
    readoutEl.style.cssText = 'min-width:' + w + 'ch;text-align:right;font-variant-numeric:tabular-nums';

    const input = document.createElement('input');
    input.type = 'range';
    input.min = String(min);
    input.max = String(max);
    input.step = String(step);
    input.value = String(initial);
    input.style.cssText = 'flex:1;accent-color:#0f0;background:transparent;cursor:pointer';

    function updateReadout() {
      readoutEl.textContent = fmt(input.valueAsNumber | 0).padStart(w, ' ');
    }
    updateReadout();

    input.addEventListener('input', () => {
      updateReadout();
      if (onChange) onChange(input.valueAsNumber | 0);
    });

    row.appendChild(labelEl);
    row.appendChild(readoutEl);
    row.appendChild(input);
    return {
      element: row,
      setValue(v) {
        input.value = String(v);
        updateReadout();
      },
      getValue() { return input.valueAsNumber | 0; },
    };
  }

  // Convenience: corner panel + N slider rows keyed by row.key. An
  // optional panel-level `format` applies to every row (and the panel
  // computes a uniform readout width from it so all rows align on the
  // left edge of the range input). `setAll({ key: value, … })` snaps
  // every named row at once without firing onChange.
  function createSliderPanel({ corner, id, style = '', stack, rows, format }) {
    const fmtFallback = (v) => String(v | 0);
    const widthOf = (f) => Math.max(...rows.flatMap(r => [f(r.min).length, f(r.max).length]));
    const uniformWidth = widthOf(format || fmtFallback);

    const panel = createCornerPanel({ corner, id, style, stack });
    const sliderRows = {};
    for (const cfg of rows) {
      if (!cfg.key) throw new Error('[strudelbreaks] createSliderPanel row missing key');
      const rowCfg = { ...cfg, width: uniformWidth };
      if (format && !cfg.format) rowCfg.format = format;
      const sr = createSliderRow(rowCfg);
      sliderRows[cfg.key] = sr;
      panel.element.appendChild(sr.element);
    }
    return {
      element: panel.element,
      rows: sliderRows,
      setAll(values) {
        for (const key of Object.keys(values)) {
          const r = sliderRows[key];
          if (r) r.setValue(values[key]);
        }
      },
    };
  }

  // Pop-up action menu anchored to a trigger element. Returns
  // `{ element, close }`; the menu is appended to document.body and
  // dismisses itself when the user clicks outside it (the dismiss
  // listener is registered on the next tick so the same click that
  // opened the menu doesn't immediately close it). `items` is an array
  // of `{ label, onSelect }`; clicking one runs onSelect then closes.
  // `onClose` (optional) fires once when the menu goes away.
  //
  // Caller is responsible for the open/toggle gesture; typically:
  //
  //     let menu = null;
  //     btn.addEventListener('click', e => {
  //       e.stopPropagation();           // outside-click handler ignores
  //       if (menu) { menu.close(); return; }
  //       menu = createActionMenu({
  //         anchor: btn, items: [...],
  //         onClose: () => { menu = null; },
  //       });
  //     });
  //
  // The stopPropagation matters: without it, the document-level
  // outside-click listener fires on the toggle's own re-click and
  // re-opens the menu after closeMenu cleared it.
  function createActionMenu({ anchor, items, onClose }) {
    if (!anchor) throw new Error('[strudelbreaks] createActionMenu needs anchor');
    if (!items || !items.length) throw new Error('[strudelbreaks] createActionMenu needs items');

    const menu = document.createElement('div');
    menu.dataset.strudelbreaks = '1';
    menu.dataset.role = 'action-menu';

    const r = anchor.getBoundingClientRect();
    menu.style.cssText =
      'position:fixed;top:' + (r.bottom + 4) + 'px;'
      + 'right:' + (window.innerWidth - r.right) + 'px;'
      + 'background:#0a0a0a;border:1px solid #1f3f1f;padding:6px;z-index:9999;'
      + 'display:flex;flex-direction:column;gap:4px;';

    let closed = false;
    function close() {
      if (closed) return;
      closed = true;
      menu.remove();
      document.removeEventListener('click', onOutsideClick);
      if (onClose) onClose();
    }

    function onOutsideClick(e) {
      if (!menu.contains(e.target)) close();
    }

    for (const item of items) {
      const btn = createButton(item.label, () => { close(); item.onSelect(); },
        { style: 'min-width:140px;text-align:left' });
      menu.appendChild(btn);
    }

    document.body.appendChild(menu);
    setTimeout(() => document.addEventListener('click', onOutsideClick), 0);
    return { element: menu, close };
  }

  // Remove every DOM node the library has ever attached. Templates
  // should call this once after loading StrudelBreaks, before building
  // fresh widgets — otherwise panels from a previously-loaded template
  // stay on screen when you paste a different script into strudel.cc.
  // Self-contained templates that don't load the library can do the
  // same sweep inline:
  //   document.querySelectorAll('[data-strudelbreaks]').forEach(el => el.remove());
  function resetUI() {
    if (typeof document === 'undefined') return;
    document.querySelectorAll('[data-strudelbreaks]').forEach(el => el.remove());
  }

  // ===== Store =====
  // Schema-gated localStorage wrapper. get() returns the parsed payload
  // if its schema matches, null otherwise (with a console.warn). The
  // store doesn't interpret the payload shape — just that it has a
  // `schema` field set to schemaVersion.
  function createPersistedStore({ key, schemaVersion, defaultPayload }) {
    function get() {
      try {
        const raw = localStorage.getItem(key);
        if (!raw) return null;
        const parsed = JSON.parse(raw);
        if (parsed.schema !== schemaVersion) {
          console.warn('[strudelbreaks] ignoring stored payload at ' + key
            + ' with schema ' + parsed.schema + ' (expected ' + schemaVersion + ')');
          return null;
        }
        return parsed;
      } catch (e) {
        console.warn('[strudelbreaks] could not read ' + key + ':', e);
        return null;
      }
    }

    function set(payload) {
      try {
        localStorage.setItem(key, JSON.stringify(payload));
      } catch (e) {
        console.warn('[strudelbreaks] could not write ' + key + ':', e);
      }
    }

    function clear() {
      try { localStorage.removeItem(key); }
      catch (e) { console.warn('[strudelbreaks] could not clear ' + key + ':', e); }
    }

    function exportAsFile(filenamePrefix) {
      try {
        const now = new Date();
        const pad = (n) => String(n).padStart(2, '0');
        const stamp = now.getFullYear() + pad(now.getMonth() + 1) + pad(now.getDate())
          + '-' + pad(now.getHours()) + pad(now.getMinutes()) + pad(now.getSeconds());
        const filename = filenamePrefix + '-' + stamp + '.json';
        const payload = get() || defaultPayload;
        downloadBlob(filename, JSON.stringify(payload, null, 2), 'application/json');
      } catch (e) {
        console.warn('[strudelbreaks] export failed for ' + key + ':', e);
      }
    }

    return { get, set, clear, exportAsFile };
  }

  function downloadBlob(filename, content, mimeType = 'application/octet-stream') {
    const blob = new Blob([content], { type: mimeType });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  }

  return {
    rng:   { mulberry32, randInt, randChoice, sampleUnique },
    pico:  { PicoSequence, SEQUENCE_MODES },
    mini:  { parseBreak, parsePattern, formatBreak, formatPattern },
    util:  { meanIndex, thinByUniforms },
    hex:   { hex2, hexPad, arrayHex },
    ui:    { createCornerPanel, createButton, createIconButton, createDeleteIcon, createButtonBar, createSliderRow, createSliderPanel, createActionMenu, resetUI },
    store: { createPersistedStore, downloadBlob },
  };
});

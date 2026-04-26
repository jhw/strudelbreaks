# Strudel playback-template export

Per-target notes for `scripts/export/strudel/`. Unlike the three audio
targets (`octatrack`, `ot-doom`, `torso-s4`), this one renders no
audio. It generates a standalone `.strudel.js` file that loads the
sample gist at runtime via `samples(gistUrl)` and plays back the
captured rows through Strudel's pattern graph — same playback
contract as `tempera.strudel.js` at the repo root, just with the
breaks/patterns from the export baked in as literal mini-notation
strings.

CLI:

```
python scripts/export/strudel/render.py <export.json>
    [--name NAME] [--seed N]
```

Output: `tmp/strudel/<name>.strudel.js`. Paste the file into
[Strudel](https://strudel.cc/) to play.

There is **no `--source` flag** here. The generated template is
WAV-only by construction — `await samples(gistUrl)` reads the gist's
`strudel.json` (`name → ["name.wav"]`) and Strudel's audio engine
loads each WAV directly. Sibling JSON pattern files in the gist (used
by the audio targets in `--source json` mode) are ignored on the
playback side.

## What gets baked

For each non-empty bank in the export:

- the cell-position-indexed mini-notation strings — break (curly
  polymetric form `{a b c d}%N`) and pattern (positional `[i j k …]`),
- a per-row deduplicated vocabulary so Strudel's editor highlights
  the active cell as the slider moves through the row,
- short rows wrap by cell-index modulo their source length, so every
  row spans `max_row_len` cells.

## Playback controls

Three sliders in the generated template:

- `rowSlider` — selects a bank.
- `cellSlider` — selects a cell within the bank. Sized on the longest
  row.
- `delaySlider` — global delay send.

Slider ranges are emitted as numeric literals at the call site.
Strudel's UI renderer scans the source text *before* evaluation, so
non-literal range arguments don't render correctly — see
`STRUDEL.md` (repo root, "slider arg literals") for the constraint.

## Why this is split out from `tempera.strudel.js`

Tempera (`tempera.strudel.js` at the repo root) is the **capture**
template — it generates breaks/patterns at load time from a seeded
RNG, surfaces sliders + a captures HUD, and persists captures to
`localStorage`. The captures get exported as a JSON payload.

This export target is the **playback** side: take that captures JSON
and emit a minimal Strudel script that just plays the captured rows
back, no generation logic, no captures HUD, no persistence. Useful
for sharing a finished arrangement as a single `.strudel.js` paste.

## References

- `tempera.strudel.js` — the capture-side template this playback
  template mirrors.
- `STRUDEL.md` (repo root) — Strudel transpile rules and runtime quirks
  relevant when editing either template.
- `scripts/export/strudel/templates/playback.strudel.js.j2` — the
  Jinja2 template the renderer fills in.

# Octatrack "Megabreak of Doom" export — `ot-doom`

Second Octatrack export target alongside `scripts/export/octatrack/`.
Renders a tempera captures JSON into an OT project where the crossfader
sweeps continuously between the cells of each row — different captured
patterns become a single morphable timeline.

CLI:

```
python scripts/export/ot-doom/render.py <export.json>
    [--name NAME] [--seed N]
    [--source {json,wav}]
```

Output: `tmp/ot-doom/<name>.zip`. `--source` is the shared flag (see
`docs/export/octatrack.md` for behaviour); JSON mode renders break
audio at 44.1 kHz directly via beatwav, WAV mode bundles the gist's
WAVs and resamples on load.

## Two readings of "Megabreak of Doom"

The technique on the
[Elektronauts thread](https://www.elektronauts.com/t/octatrack-64-breakbeat-x-16-slices-megabreak-of-doom/337)
shows a matrix-chain layout where the crossfader walks the *break*
axis: scene A locks slice 0 (= the 1st break played at full grid),
scene B locks slice N-1 (= the Nth break played at full grid), and N
trigs spaced across the pattern keep playback grid-locked while the
fader chooses *which* break sounds.

The pattern stays the same; the source break changes with the fader.
Forum-canonical inputs: N source breakbeats.

This repo's earlier `picobeats-server` shipped a `doom_exporter` that
took the *same* matrix-chain layout but redefined the input axis:
inputs were N fully-rendered patterns rather than N source breaks.
The fader morphed between *patterns* — different velocity/rhythmic
arrangements — rather than between source samples. The OT-side
plumbing (chain layout, slot count, trig spacing, scene
`slice_index`) is identical; only the meaning of "an input" changes.

`ot-doom` adopts the picobeats-server reading because it maps cleanly
onto how tempera actually works:

| Tempera concept | Doom role |
|---|---|
| Row in captures (`payload.banks[i]`) | One OT pattern. |
| Cell in row (`payload.banks[i][j]`) | One **input** to the doom matrix. |
| `cell.break` / `cell.pattern` | Audio source for that input. |
| Cells per row, |C| | Doom input count, must be **4, 8, or 16**. |

Forum-traditional |B| ∈ {4, 8, 16} (unique source breaks per cell)
won't ever fire on tempera output: tempera generates `{root alt root
alt}` style breaks with at most 2 unique names per cell. Picking
*cells* as inputs sidesteps that ceiling — adding more curated cells
to a row is a one-click action in the captures UI.

## Layout

```
scripts/export/ot-doom/
├── render.py          # main entry, build & zip a project
├── audio.py           # pydub helpers: cell render + matrix chain
├── push.py            # copied 1:1 from octatrack/, retargeted to tmp/ot-doom/
├── clean_local.py     # copied 1:1
├── clean_remote.py    # copied 1:1
└── clean_stubs.py     # copied 1:1
```

`tmp/ot-doom/<name>.zip` is the build output. The push/clean scripts
target the same `/Volumes/OCTATRACK/strudelbeats/` set as the
existing target, so projects from both renderers coexist on the card.

## Render contract

Same export schema as `octatrack/render.py` (schema 7, validated via
`common/schema.py`). The OT-side configuration uses octapy's
`AudioSceneTrack.slice_index` (added in octapy 0.1.23) — confirmed
present in 0.1.31, the version pinned in `requirements.txt`.

### Step 1 — Per row → per OT bank

Each non-empty row becomes one OT bank with one part and one 16-step
pattern on track 1. Rows with |C| ∉ {4, 8, 16} fail loudly. A row
with one cell isn't a "morph" so the validator rejects |C| < 4.

### Step 2 — Render each cell to one bar of audio

For each cell in the row, build an in-memory `AudioSegment` of one
bar at the project tempo:

1. Resolve per-event break names by polymetric stretch of the
   captured `{a b c d}%N` form — see `STRUDEL.md` (repo root) "Polymetric
   stretch". For tempera's `events_per_cycle = 8` and a 4-name break
   that's `[a a b b a a b b]` (with index `i * 4 // 8`).
2. For each event `i`:
   - if `cell.pattern[i]` is `None`, write one slice's worth of
     silence;
   - otherwise write `equal_slices(source_break_wav, 16)[cell.pattern[i]]`,
     the captured slice of the source break for that event.
3. Concatenate the 8 events end-to-end. **No fades** — Strudel doesn't
   apply per-event fades, and adding them on the OT side produces a
   periodic loudness dip relative to Strudel that's small but
   audible. If pathological patterns surface clicks at slice
   boundaries we'll reintroduce a sub-perceptual envelope (≤ 0.5 ms)
   here only — never inside `build_matrix_chain`, where boundaries
   lie inside whatever envelope this step produced and a second pass
   would double-attenuate.

The result is `8 * (1/8 note) = 1 bar` of audio.

### Step 3 — Build N matrix chains

Given N input cell-renders `inputs[0..N-1]`, each one bar long:

1. Slice every `inputs[k]` into N equal segments — segment duration
   = `bar_ms / N`. With N=4 and 1 bar = 1875 ms (at 128 BPM), that's
   ≈ 469 ms per segment.
2. For each `k ∈ 0..N-1` build the chain:
   ```
   chain[k] = inputs[0].segment[k]
            ++ inputs[1].segment[k]
            ++ ...
            ++ inputs[N-1].segment[k]
   ```
   `chain[k]` length = N segments × `bar_ms / N` = 1 bar.
3. Each chain gets exactly N equal slice markers (one per input
   contribution); the slice durations are the segment duration.

### Step 4 — Bind chains to flex slots

Each chain → one flex slot via `project.add_sample(path,
slot_type='FLEX')`. N slots per row, N rows max ⇒ at most 16 × 16 = 256
slots… but tempera in practice uses 1–2 rows × 4–8 cells, well under
the 128 flex pool. The 128-slot ceiling is documented as a
hypothetical risk; the validator will need extending if real usage
ever pushes near it.

### Step 5 — Pattern, trigs, scenes

Pattern length 16 steps (1 bar at 1/16). Trigs at every step `1 + k *
(16/N)` for `k ∈ 0..N-1`:

- N=4 → trigs at 1, 5, 9, 13
- N=8 → trigs at 1, 3, 5, 7, 9, 11, 13, 15
- N=16 → trig at every step

Trig `k` is `sample_lock`-ed to chain `k`. **No** per-trig
`slice_index` lock — that would override the scene and was the bug
in the previous implementation.

Track 1 setup:

- `configure_flex(default_slot=chain_0)`
- `setup.slice = SliceMode.ON`

Scenes:

- `scene(1).track(1).slice_index = 0`     (= input 0 across all chains)
- `scene(2).track(1).slice_index = N - 1` (= input N-1 across all chains)
- `active_scene_a = 0; active_scene_b = 1`

The crossfader interpolates `slice_index` from 0 to N-1; at any
position `s` it picks slice `s` of every chain, which by chain
construction is `inputs[s].segment[k]` at trig `k` — i.e. input `s`
played in full, grid-aligned.

## Why this is a change from the previous build

The earlier ot-doom commit (`ce459d8`) built a different design:

| Aspect | Previous (forum-style intent) | Current (cell-input) |
|---|---|---|
| Inputs per cell | source breaks (`B`) | (n/a — cells are inputs) |
| Inputs per row | (n/a) | cells (`C`) |
| Required count | `|B| ∈ {4, 8, 16}` | `|C| ∈ {4, 8, 16}` |
| Tempera fit | ✗ — `|B| = 2` always | ✓ — user chooses cells per row |
| Patterns per row | 1 per cell (was 4 with 4 cells) | 1 per row |
| Trigs per pattern | 16 (one per step) | N (spaced) |
| Per-trig slice lock | yes (= 0) | no |
| Scene mechanism | `playback_param2` (raw STRT) | `slice_index` (octapy ≥ 0.1.23) |
| Wavs per cell | up to 16 timesliced | (n/a — chains are per-row) |
| Slot dedup loss | Yes — pattern info dropped at `|B| < 16` | None |

The shipped code locked every step's `slice_index = 0`, which
overrode the scene's STRT lock — so the crossfader had no audible
effect. Plus tempera's `|B| = 2` reality meant the renderer could
never accept tempera captures unmodified, requiring a JS-side break
diversification that was out of scope.

The cell-input redesign solves both problems in Python without any
tempera changes.

## Audio dependencies

`pydub>=0.25` is in `requirements.txt`. Pure-WAV operations
(decode/encode/concat/fade/slice) go through stdlib `wave`, so no
ffmpeg dependency.

Source wavs from the strudel sample gist mix 44.1 and 48 kHz. The OT
expects 44.1 kHz at trig time — a 48 kHz file plays ~9% slow. `audio.py`
resamples on load via `set_frame_rate(OT_SAMPLE_RATE)` so every chain
ships at the native rate. See `docs/export/octatrack.md` for the full list of
device-side constraints.

## File layout in the project bundle

```
ot-doom-<name>.zip
├── <NAME>/
│   ├── project.work
│   ├── markers.work       # per-slot N equal slice markers
│   └── bank01.work … bankNN.work
└── AUDIO/projects/<NAME>/
    ├── b01_chain00.wav     # row 1 chain 0 (= segment 0 from inputs 0..N-1)
    ├── b01_chain01.wav
    ├── …
    └── bMM_chainNN.wav
```

## Implementation steps

1. **Doc this design** in `docs/export/ot-doom.md` (this file).
2. **Rewrite `audio.py`**:
   - Keep `load_break`, `equal_slices`, `export_wav`.
   - Drop `render_timesliced_step` (not needed any more).
   - Add `render_cell_audio(cell, source_slices, events_per_cycle)`
     → `AudioSegment` (1 bar).
   - Add `build_matrix_chain(input_audios, k)` →
     `AudioSegment` (1 bar, segment k from each input).
3. **Rewrite `render.py`**:
   - Drop the `B`/`B'`/`pad_b_to_16`/`slot_to_step` plumbing.
   - Per row: validate `|C| ∈ {4, 8, 16}`, render N cell audios, build
     N chains, bind to flex slots with N slice markers, configure
     part/track/scenes, write the 16-step pattern with N spaced trigs.
4. **Smoke test** with a real tempera export (4 cells per row) →
   verify slot count = N per bank, `slice_index` set on scene
   tracks, no `slice_index` p-lock on steps.
5. **Push** to device and crank the fader.

## Open questions / risks

- **Pattern fidelity at slice boundaries.** Chain segments are
  rendered at `bar_ms / N` and concatenated; if `bar_ms / N` doesn't
  divide cleanly into the source-slice grid, segment boundaries cut
  through note attacks. We ship with no fades (matching Strudel) —
  if pathological inputs cause audible clicks at segment cuts, the
  fix is a sub-perceptual envelope inside `render_cell_audio` only.
  Keep N to powers of 2 so segment boundaries align with event
  boundaries: tempera's 1-bar cells with `events_per_cycle = 8` mean
  N=4 cuts at every other event, N=8 cuts at every event, N=16
  oversamples within an event.
- **128-slot ceiling.** Worst case 16 rows × 16 cells = 256 chains,
  exceeds the 128 flex pool. Tempera realistic usage stays well
  under. Add a count check if future captures push closer.
- **Per-cell BPM mismatch.** The render assumes every source break
  is at project tempo (= 32 1/16-steps long, i.e. 2 bars). If a
  source wav has the wrong duration, segment timing drifts. Same
  caveat as the existing `octatrack/render.py`; deferred warning is
  the same fix in both.

## References

- Elektronauts: ["Octatrack — 64 breakbeat × 16 slices Megabreak of Doom"](https://www.elektronauts.com/t/octatrack-64-breakbeat-x-16-slices-megabreak-of-doom/337)
- picobeats-server: `python/export/doom_exporter.py` at commit
  `40c8790` ("Use octapy 0.1.23 slice_index on scene tracks") — the
  prior art this rewrite tracks.
- octapy: `AudioSceneTrack.slice_index` (≥ 0.1.23).
- `STRUDEL.md` (repo root) — Strudel transpile rules, polymetric stretch,
  and the captures-side mini-notation. Useful when iterating on
  `tempera.strudel.js`.

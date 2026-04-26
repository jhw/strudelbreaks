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
| Cells per row, `\|C\|` | Doom input count, must be **4, 8, or 16**. |

Forum-traditional `|B| ∈ {4, 8, 16}` (unique source breaks per cell)
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

### Step 1 — Pack rows into banks (16 patterns each)

Rows pack sequentially: rows 1..16 → bank 1 patterns 1..16, rows
17..32 → bank 2 patterns 1..16, etc. Tempera-realistic exports stay
inside one bank.

Within a bank every pattern shares **part 1**, and therefore the
part's scenes (`slice_index = 0` / `|C| - 1`). All rows in a bank
must share the same `|C|`; mixed-`|C|` banks fail loudly. The OT's
4-parts-per-bank could in principle host up to 4 distinct `|C|`
groups per bank, but tempera-realistic usage is one `|C|` per
session, so we keep the model flat.

Empty rows are dropped. Rows with `|C| ∉ {4, 8, 16}` fail loudly —
a row with one cell isn't a "morph" so the validator rejects `|C| < 4`.

The full project ceiling is `MAX_BANKS × PATTERNS_PER_BANK = 256`
rows, validated up-front — but the flex-slot pool (Step 4) usually
bites first.

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

### Step 3 — Build N matrix chains per row

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
slot_type='FLEX')`. N slots per row across the whole project; the
flex pool ceiling is **128 slots** and is validated up-front. Total
slots = sum of `|C|` across all rows. Worst-case dense packs:

- 16 rows × `|C|=8` = 128 slots — at the ceiling, accepted.
- 17 rows × `|C|=8` = 136 — rejected with an explicit message.
- 16 rows × `|C|=16` = 256 — rejected.

In practice tempera exports are 1–4 rows × `|C|=4..8`, well under.

### Step 5 — Pattern, trigs, scenes

Pattern length 16 steps (1 bar at 1/16). Trigs at every step `1 + k *
(16/N)` for `k ∈ 0..N-1`:

- N=4 → trigs at 1, 5, 9, 13
- N=8 → trigs at 1, 3, 5, 7, 9, 11, 13, 15
- N=16 → trig at every step

Trig `k` is `sample_lock`-ed to chain `k` (the row's chain `k`).
**No** per-trig `slice_index` lock — that would override the scene
and was the bug in the previous implementation.

Part 1 setup (configured once per bank, shared by all the bank's
patterns):

- `t1.configure_flex(default_slot=any_chain)` — the default is only
  used when a step has no `sample_lock`, which never happens in our
  patterns; we pass the first chain of the first pattern.
- `t1.setup.slice = SliceMode.ON`

Scenes (also part-scoped, shared by all the bank's patterns):

- `scene(1).track(1).slice_index = 0`     (= input 0 across all chains)
- `scene(2).track(1).slice_index = N - 1` (= input N-1 across all chains)
- `active_scene_a = 0; active_scene_b = 1`

The crossfader interpolates `slice_index` from 0 to N-1; at any
position `s` it picks slice `s` of every chain, which by chain
construction is `inputs[s].segment[k]` at trig `k` — i.e. input `s`
played in full, grid-aligned. Same fader sweep applies to every
pattern in the bank because they all share part 1.

## Audio dependencies

`pydub>=0.25` is in `requirements.txt`. Pure-WAV operations
(decode/encode/concat/fade/slice) go through stdlib `wave`, so no
ffmpeg dependency.

In `--source wav` mode the strudel sample gist serves wavs at mixed
44.1 / 48 kHz; the OT expects 44.1 kHz at trig time, so `audio.py`
resamples on load via `set_frame_rate(OT_SAMPLE_RATE)`. In `--source
json` mode beatwav renders directly at 44.1 kHz, no resample needed.
See `docs/export/octatrack.md` for the full list of device-side
constraints.

## File layout in the project bundle

```
ot-doom-<name>.zip
├── <NAME>/
│   ├── project.work
│   ├── markers.work       # per-slot N equal slice markers
│   └── bank01.work … bankNN.work
└── AUDIO/projects/<NAME>/
    ├── b01_p01_chain00.wav    # bank 1, pattern 1, chain 0
    ├── b01_p01_chain01.wav    # bank 1, pattern 1, chain 1
    ├── …
    ├── b01_p16_chain00.wav    # bank 1, pattern 16, chain 0
    └── b02_p01_chain00.wav    # bank 2, pattern 1, chain 0 (17th row)
```

Chain WAVs land in `tmp/ot-doom-render/<name>/bank<NN>/`; the zip
gathers them into the OT-conventional `AUDIO/projects/<NAME>/`.

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
- **Mixed `|C|` per session.** If a real-world workflow ever wants
  rows with different cell counts inside one project, the model is
  one of: split into separate exports (current answer); reorder rows
  so each `|C|` group lands in its own bank (good enough for most
  cases since `MAX_BANKS = 16`); or extend the renderer to use up
  to 4 parts per bank (one part per `|C|`).
- **Per-cell BPM mismatch.** WAV-source mode assumes every source
  break is at project tempo (= 32 1/16-steps long, i.e. 2 bars). If
  a source wav has the wrong duration, segment timing drifts. Same
  caveat as the existing `octatrack/render.py`. JSON-source mode
  fixes this by construction (renders at the captures' BPM).

## References

- Elektronauts: ["Octatrack — 64 breakbeat × 16 slices Megabreak of Doom"](https://www.elektronauts.com/t/octatrack-64-breakbeat-x-16-slices-megabreak-of-doom/337)
- picobeats-server: `python/export/doom_exporter.py` at commit
  `40c8790` ("Use octapy 0.1.23 slice_index on scene tracks") — the
  prior art this rewrite tracks.
- octapy: `AudioSceneTrack.slice_index` (≥ 0.1.23).
- `STRUDEL.md` (repo root) — Strudel transpile rules, polymetric stretch,
  and the captures-side mini-notation. Useful when iterating on
  `tempera.strudel.js`.

# Octatrack "Megabreak of Doom" export — `ot-doom`

Second Octatrack export target alongside `app/export/octatrack/ot_basic/`.
Renders a tempera captures JSON into an OT project where the crossfader
sweeps continuously between the cells of each row — different captured
patterns become a single morphable timeline.

Invocation: tempera's `export ▾` menu → `ot-doom`. Posts the captures
payload to `POST /api/export/binary` (`target='ot-doom'`); the server
calls `app.export.octatrack.ot_doom.render.render()` and streams the
project zip back. Browser saves to `~/Downloads/<name>.ot-doom.zip`.

JSON-source rendering only. Two stem modes (controlled by the
request's `split_stems` field, default `true`):

* **`split_stems=true`** — each break is rendered as three drum stems
  (kick / snare / hat) via beatwav at 44.1 kHz, stacked into one
  packed sample per chain position; T1/T2/T3 each play their own stem
  under independent scene drives.
* **`split_stems=false`** — each break is rendered as one mixed
  sample; T1 alone plays it. Used to A/B audio fidelity against the
  Strudel source.

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

Both Octatrack render packages live under `app/export/octatrack/`,
sharing one set of push/clean scripts at `scripts/octatrack/`:

```
app/export/octatrack/                scripts/octatrack/
├── ot_basic/                        ├── push.py        # shared, takes target arg {ot-basic, ot-doom}
│   └── render.py                    ├── clean_remote.py
└── ot_doom/                         └── clean_stubs.py
    ├── render.py
    └── audio.py
```

The downloaded artifact is `~/Downloads/<name>.ot-doom.zip` (sibling
of `<name>.ot-basic.zip` for the per-cell-pattern target). The
push/clean scripts target the same `/Volumes/OCTATRACK/strudelbeats/`
set as the basic target, so projects from both renderers coexist on
the card.

## Render contract

Same export schema as `app/export/octatrack/ot_basic/render.py` (schema
7, validated via `app/export/common/schema.py`). The OT-side
configuration uses octapy's
`AudioSceneTrack.slice_index` (added in octapy 0.1.23) — confirmed
present in 0.1.31, the version pinned in `requirements.txt`.

### Step 1 — Pack rows into banks (16 patterns each)

Rows pack sequentially: rows 1..16 → bank 1 patterns 1..16, rows
17..32 → bank 2 patterns 1..16, etc. Tempera-realistic exports stay
inside one bank.

Within a bank every pattern shares **part 1**, and therefore the
part's per-track scene config (each track sweeps its own
`(N+1)`-slice block of the packed slot — see Step 5). All rows in a
bank must share the same `|C|`; mixed-`|C|` banks fail loudly.

Empty rows are dropped. Rows with `|C| ∉ {4, 8, 16}` fail loudly —
a row with one cell isn't a "morph" so the validator rejects `|C| < 4`.

The full project ceiling is `MAX_BANKS × PATTERNS_PER_BANK = 256`
rows, validated up-front — but the flex-slot pool (Step 4) usually
bites first.

### Step 2 — Render each cell to one bar per drum stem

For each cell in the row, build three in-memory `AudioSegment`s of
one bar each — one per drum stem (kick / snare / hat). Each stem is
beatwav-rendered with the cell's `matched_hits` filtered to that
drum type, so the three stems share timing and dynamics but isolate
the kick / snare / hat audio.

The per-stem source slices come from `equal_slices(load_break(<stem
WAV>), 16)`. For each event `i` in the cell:

1. Resolve per-event break names by polymetric stretch of the
   captured `{a b c d}%N` form — see `STRUDEL.md` (repo root) "Polymetric
   stretch". For tempera's `events_per_cycle = 8` and a 4-name break
   that's `[a a b b a a b b]` (with index `i * 4 // 8`).
2. For each event `i`:
   - if `cell.pattern[i]` is `None`, write one slice's worth of
     silence;
   - otherwise write `equal_slices(stem_break_wav, 16)[cell.pattern[i]]`,
     the captured slice of the **stem's** source break for that event.
3. Concatenate the 8 events end-to-end. **No fades** — Strudel doesn't
   apply per-event fades, and adding them on the OT side produces a
   periodic loudness dip relative to Strudel that's small but
   audible. If pathological patterns surface clicks at slice
   boundaries we'll reintroduce a sub-perceptual envelope (≤ 0.5 ms)
   here only — never inside `build_matrix_chain`, where boundaries
   lie inside whatever envelope this step produced and a second pass
   would double-attenuate.

The result is `8 * (1/8 note) = 1 bar` of audio per stem (3 bars
total per cell).

### Step 3 — Build N packed matrix chains per row

Given N input cell-renders per drum stem (`3 * N` audios total in
split mode, `1 * N` in mixed mode), each one bar long:

1. Slice every per-stem `input[k]` into N equal segments — segment
   duration = `bar_ms / N`. With N=4 and 1 bar = 1875 ms (at 128
   BPM), that's ≈ 469 ms per segment.
2. For each `k ∈ 0..N-1` build per-stem chain blocks:
   ```
   stem_chain[stem][k] = input_0[stem].segment[k]
                       ++ input_1[stem].segment[k]
                       ++ ...
                       ++ input_{N-1}[stem].segment[k]
   ```
   Each per-stem block: `N` segments long, `bar_ms` total.
3. Stack the per-stem blocks into one packed chain in fixed order
   (kick, snare, hat in split mode; the single mixed block in mixed
   mode):
   ```
   chain[k] = stem_chain['kick'][k]
            ++ stem_chain['snare'][k]
            ++ stem_chain['hat'][k]
   ```
   `chain[k]` length = `len(stems) * bar_ms`. For split-mode N=16 and
   `bar_ms = 1875`, that's 3 × 1875 = 5625 ms.
4. Each packed chain gets exactly `len(stems) * N` equal slice markers
   — in split mode kick block at slices `0..N-1`, snare at
   `N..2N-1`, hat at `2N..3N-1`. Per-track scenes on part 1 address
   each block.

### Step 4 — Bind packed chains to flex slots

Each packed chain → one flex slot via `project.add_sample(path,
slot_type='FLEX')`. N slots per row across the whole project; the
flex pool ceiling is **128 slots** and is validated up-front. Total
slots = sum of `|C|` across all rows. Slot count is the same in
mixed and split modes — split mode packs the three stems into one
slot per chain position. Worst-case dense packs:

- 16 rows × `|C|=8` = 128 slots — at the ceiling, accepted.
- 17 rows × `|C|=8` = 136 — rejected with an explicit message.
- 16 rows × `|C|=16` = 256 — rejected.

Slice-marker math sanity (must stay ≤ 64 per sample, split mode):

- `|C|=4`  → 3 × 4  = 12 slices/slot ✓
- `|C|=8`  → 3 × 8  = 24 slices/slot ✓
- `|C|=16` → 3 × 16 = 48 slices/slot ✓

In practice tempera exports are 1–4 rows × `|C|=4..8`, well under.

### Step 5 — Pattern, trigs, per-track scenes, FX

Pattern length 16 steps (1 bar at 1/16). Trigs at every step `1 + k *
(16/N)` for `k ∈ 0..N-1`:

- N=4 → trigs at 1, 5, 9, 13
- N=8 → trigs at 1, 3, 5, 7, 9, 11, 13, 15
- N=16 → trig at every step

In split mode T1, T2, T3 all fire at the same N positions, all
sample-locked to the same packed slot for that chain. In mixed mode
only T1 fires. **No** per-trig `slice_index` lock on any track —
that would override the scene drive and was the bug in the original
ot-doom redesign.

Part 1 setup (configured once per bank, shared by all the bank's
patterns):

- `t<i>.configure_flex(default_slot)` for each enabled track —
  default only used when a step has no `sample_lock`, which never
  happens in our patterns.
- `t<i>.setup.slice = SliceMode.ON` for each enabled track.
- `t<i>.fx1_type = DJ_EQ`, `t<i>.fx2_type = COMPRESSOR` for each
  enabled track.
- `t8.fx1_type = CHORUS` (`.mix = 64`), `t8.fx2_type = DELAY`
  (`.send = 64`). Different param names for the two effects because
  they expose different wet controls in octapy.

Per-track scenes on part 1 (shared across the bank's patterns):

- Split mode:
  - T1 (kick):  `scene(1).track(1).slice_index = 0`,         `scene(2).track(1).slice_index = N - 1`
  - T2 (snare): `scene(1).track(2).slice_index = N`,         `scene(2).track(2).slice_index = 2N - 1`
  - T3 (hat):   `scene(1).track(3).slice_index = 2N`,        `scene(2).track(3).slice_index = 3N - 1`
- Mixed mode:
  - T1:         `scene(1).track(1).slice_index = 0`,         `scene(2).track(1).slice_index = N - 1`
- `active_scene_a = 0; active_scene_b = 1`

For each enabled track the crossfader interpolates raw STRT from
`2 * (track_idx * N)` (scene A) to `2 * (track_idx * N + (N - 1))`
(scene B). Same input `s` is reached on all enabled tracks at the
same fader fraction so the kit pieces of input `s` play in lock-step.

> **Note on uniformity** — an earlier design used `N + 1` slices per
> stem (with the last segment a duplicate of input N-1) so the
> crossfader's STRT lerp would cross N boundaries at exact 1/N
> fractions assuming `slice = floor(raw / 2)`. That math turned out
> not to match the device's behaviour empirically (transitions
> landed off the predicted positions and the duplicate slot read as
> a phantom "extra input"). The current design uses N slices and the
> straightforward range `0 .. N - 1` — accepting whatever
> non-uniformity the device's actual lerp/round mode introduces in
> exchange for a chain that matches what the user sees on screen.

## Audio dependencies

`pydub>=0.25` and `beatwav` (per-stem rendering) are in
`requirements.txt`. Pure-WAV operations (decode/encode/concat/fade/slice)
go through stdlib `wave`, so no ffmpeg dependency.

beatwav renders each break stem directly at OT_SAMPLE_RATE (44.1
kHz), so no resample-on-load is needed. The per-stem WAVs land
under `tmp/samples/<gistId>/rendered/sr44100_bpm<bpm>/<name>__<track>.wav`
(flat layout — basenames must stay unique because the OT slot
manager deduplicates by basename).
See `docs/export/octatrack.md` for the full list of device-side
constraints and `app/export/common/sample_source.py` for the
shared resolver.

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

Chain WAVs are written to a per-request `tempfile.TemporaryDirectory()`
under `<tmp>/render/<name>/bank<NN>/`; the zip gathers them into the
OT-conventional `AUDIO/projects/<NAME>/` and the temp dir is removed
after the response is streamed.

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
- **JSON-only.** Per-stem rendering needs the gist to carry
  `{name}.json` for every break — there's no way to decompose a
  pre-mixed WAV into kick/snare/hat after the fact. Older WAV-only
  gists fail loudly. The torso-s4 target stays on the mixed-stem
  path and continues to support `--source wav`.

## References

- Elektronauts: ["Octatrack — 64 breakbeat × 16 slices Megabreak of Doom"](https://www.elektronauts.com/t/octatrack-64-breakbeat-x-16-slices-megabreak-of-doom/337)
- picobeats-server: `python/export/doom_exporter.py` at commit
  `40c8790` ("Use octapy 0.1.23 slice_index on scene tracks") — the
  prior art this rewrite tracks.
- octapy: `AudioSceneTrack.slice_index` (≥ 0.1.23).
- `STRUDEL.md` (repo root) — Strudel transpile rules, polymetric stretch,
  and the captures-side mini-notation. Useful when iterating on
  `tempera.strudel.js`.

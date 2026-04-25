# Octatrack "Megabreak of Doom" export — `ot-doom`

A second Octatrack export target, independent of `scripts/export/octatrack/`,
that renders captures into the megabreak-of-doom configuration described on
the [Elektronauts thread](https://www.elektronauts.com/t/octatrack-64-breakbeat-x-16-slices-megabreak-of-doom/337).
The crossfader sweeps continuously across N source breaks per pattern: at
fader=0 you hear break 0 played at full grid; at fader=1 you hear break N-1.

## Layout

```
scripts/export/ot-doom/
├── render.py          # main entry, build & zip a project
├── push.py            # copied 1:1 from octatrack/, retargeted to tmp/ot-doom/
├── clean_local.py     # copied 1:1
├── clean_remote.py    # copied 1:1
└── clean_stubs.py     # copied 1:1
```

`tmp/ot-doom/<name>.zip` is the build output. The push/clean scripts
target the same `/Volumes/OCTATRACK/strudelbeats/` set as the existing
target (so projects from both renderers coexist on the card).

The four push/clean scripts are duplicated rather than shared because they
already paramterise their `tmp/<dir>/` root by file constant — the diff is
two `pathlib` lines per file, not worth a common module. Both targets keep
a self-contained, copy-pasteable directory tree.

## Render contract

Same export schema as `octatrack/render.py` (schema 7, validated via
`common/schema.py`); same captures JSON. What differs is what we *do* with
each cell.

Per Strudel row → one OT bank, one part, one pattern. (For now we
constrain to ≤16 rows, like the existing target. Cells per row are limited
to 16 since each cell becomes a step in a single 16-step pattern.)

We assume each row has at most one strudel pattern (one cell) for the
initial implementation — but the renderer will accept up to 16 cells per row
and treat them as variant patterns within the bank, mirroring the existing
target's bank-of-patterns behaviour.

For each strudel cell:

1. **Materialise the source breakbeats from the cell's break vocabulary.**
   The cell stores a list of break names (Strudel `{a b c d}%N` polymetric
   form, expanded via STRUDEL.md's `i*M//N` rule). The unique break names
   in the cell are the "input breaks" — call this set B, with |B| ≤ 4
   (Tempera caps `BREAK_ALT_NAMES_MAX` at 1 so |B| is usually 2). We pad
   B by repeating until it has K=16 entries: `B' = [B[0], B[1], …, B[k-1],
   B[0], B[1], …]`. We don't pad with silence; we *want* duplicates so
   every fader position lands on real audio.

2. **Render 16 output breaks.** Each output break i (0 ≤ i < 16) is a
   bar-length (eventsPerCycle-step) audio rendering of the cell's pattern,
   but where every event sources from `B'[i]`'s break wav. This is what
   the user's prompt calls "the first layer of rendering". Each output
   break is therefore an audio file: `[slice_p0(B'[i]), slice_p1(B'[i]),
   …, slice_p7(B'[i])]` concatenated with small fades, where `p_j` is the
   captured pattern's slice index at event j and `slice_*` is taken from
   the source wav's 16-equal-slice grid.
   - Source break wavs are 32 1/16-steps long; 16 equal slices => 2 steps
     per slice (same as the existing octatrack target).
   - Render duration: `eventsPerCycle * (slice_step_len)` = 8 * 2 steps =
     16 steps = 1 bar at the project tempo. We fix this to 16 1/16 steps
     so the OT pattern grid maps 1:1.
   - Rests (`slice_idx is None`) become silence frames of one slice's
     length.
   - Fades: 2 ms fade-in and 2 ms fade-out on every concatenated slice
     (a tiny click guard — bigger fades audibly soften the transients we
     want).

3. **Time-slice the 16 output breaks.** For every step i (0..15), take
   the i-th equal-step slice of every output break and concatenate them
   in break-order. Result: 16 timesliced wavs, one per pattern step,
   each wav containing 16 sub-slices (one per output break). This is the
   "second layer", and it's exactly what the elektronauts post calls
   for: each timesliced wav holds break 0's slice-i, then break 1's
   slice-i, ... then break 15's slice-i.

4. **Bind to slot manager.** Each timesliced wav becomes one Flex sample
   slot via `project.add_sample(path, slot_type='FLEX')` — 16 slots per
   bank. Each slot is then sliced into 16 equal slices via the existing
   `set_equal_slices` helper (copy-pasted from `octatrack/render.py` —
   the helper itself is generic).

5. **Pattern.** A 16-step pattern; trig at every step `i`; step `i` is
   sample-locked to timesliced slot `i` and slice-index-locked to slice
   0 (the leftmost = break 0). We do **not** spread sample locks across
   steps the way the existing target does; here, *every step i* gets
   slot i, sliced[0]. The step-to-slot mapping is the diagonal — and
   that's what makes the per-step-grain timeslicing audible.

6. **Track SRC config.** On the part, audio track 1 is configured Flex
   (default slot = slot 1). We then set:
   - `t1.setup.slice = SliceMode.ON` — slice mode on, so STRT selects
     a slice instead of a linear position. This is the lever the
     scenes pull.
   - `length_mode` we leave at its octapy default (`TIME`, length=127).
     With slice mode on, length=127 means "play the full slice"; that's
     what we want.
   - We do not need to touch length_mode further (the existing target
     also leaves it alone).

7. **Scenes.** On the bank's part 1, configure two scenes (1 and 2),
   each one locking track 1's `playback_param2` (STRT, the slice
   selector):
   - Scene 1: `track(1).playback_param2 = 0` → slice 0 (= break 0
     across every step).
   - Scene 2: `track(1).playback_param2 = 127` → with 16 slices, the
     STRT range 0..127 is quantised across slices, so 127 selects the
     last slice = break 15.

   Then `part.active_scene_a = 0; active_scene_b = 1` (zero-indexed).
   Now the crossfader sweeps continuously across the 16 sub-slices of
   each timesliced wav, which is exactly "switch dynamically between
   different breaks" because of the way the timesliced wavs are laid
   out (slice j of timesliced[i] = step-i of break-j).

## Mapping fewer than 16 unique breaks

If |B| = 4 (the common case):

- Rendering: still 4 *unique* output breaks. We render each once and
  reuse the audio.
- Timeslicing: still 4 *unique* timesliced wavs. We render each once.
- Slots: only 4 unique flex slots. We assign 4 slots, one per
  timesliced wav.
- Steps: still 16 trigs in the pattern. Steps are sample-locked
  according to the user's distribution rule:
  ```
  step i → timesliced slot index = (i * |B|) // 16
  ```
  i.e., steps 0..3 → slot 0, 4..7 → slot 1, 8..11 → slot 2, 12..15 →
  slot 3 (the polymetric stretch from STRUDEL.md, applied to step
  index → slot index).
- Each per-step `slice_index` is still 0.
- Scenes still lock STRT to 0 / 127.

Each timesliced slot is itself sliced into 16 sub-slices the same way
as the |B|=16 case (the slot has 16 sub-slices because each output
break has 16 step-positions, regardless of how many unique output
breaks there are). The crossfader still sweeps across "which break"
for that step.

That gives the requested "max dynamic behaviour" for fewer-than-16
breaks: each step uses one timesliced slot, but the slot still contains
16 sub-slice variants, and the crossfader keeps morphing across them.

## One layer or two?

The user's prompt asks whether we can collapse layer 1 (output breaks)
and layer 2 (timesliced) into a single rendering pass. The answer is
**no, not without changing the technique**.

- Layer 1 sequences slices *of source breaks* into rhythmic
  output bars: it's a per-cell render driven by the captured Strudel
  pattern.
- Layer 2 transposes layer 1's output: slice i of timesliced[step] =
  layer1_output[step]'s i-th sub-slice. It's a transpose
  (axis swap), not a reorder.

If we tried to skip layer 1 and concatenate "first slice of each
source break, second slice of each source break, …" directly, we'd
ignore the captured Strudel pattern entirely — every step would just
walk through the source-break grid in order, regardless of what the
captured cell asked for.

So: two render passes are necessary, but layer 2 is a pure transpose
of layer 1's output and can be implemented as an in-memory numpy/
pydub operation without an intermediate file write per output break.

In code: we render layer 1 in memory only (`pydub.AudioSegment` per
output break), then transpose those 16 segments into 16 timesliced
segments, only writing to disk for the 16 (or |B|) timesliced wavs
that the slot manager needs.

## Audio dependencies

- Add `pydub>=0.25` to `requirements.txt` and install in the existing
  venv. pydub uses ffmpeg only for non-WAV codecs; pure-WAV operations
  (decode/encode/concat/fade/slice) are stdlib-only via the `wave`
  module, so no extra system dependency.

## File layout in the project bundle

For a row producing 16 unique timesliced wavs, the slot pool fills
1..16. The flex_count auto-update logic in `Project.add_sample` keeps
banks in sync. Filenames are namespaced per bank/cell to avoid
collisions:

```
ot-doom-<name>.zip
├── project/
│   ├── project.work
│   ├── markers.work       # per-slot equal slice grids
│   └── bank01.work … bankNN.work
└── samples/               # bundled, copied to AUDIO/projects/<NAME>/
    ├── b01p01_t00.wav     # bank 1 pattern 1 timeslice 0
    ├── b01p01_t01.wav
    ├── …
    ├── b01p01_t15.wav
    └── …
```

(Reusing across cells: if the same set of breaks + pattern produces
identical timesliced wavs, the slot manager dedups by `ot_path`.)

## Implementation steps

1. **Scaffolding.** Create `scripts/export/ot-doom/` with stub
   `__init__.py` (so common imports work), copy the four push/clean
   scripts, retarget `tmp/octatrack/` → `tmp/ot-doom/` in each.
2. **Add `pydub`** to `requirements.txt`; install in `.venv`.
3. **Audio helpers.** New module
   `scripts/export/ot-doom/audio.py` with:
   - `load_break_wav(path) → AudioSegment` (cached by path).
   - `slice_break(seg, n_slices=16) → list[AudioSegment]` — equal
     slices.
   - `render_output_break(slices, pattern_idxs, fade_ms=2) →
     AudioSegment` — concatenate slices per the cell's Strudel
     pattern, with fades. Rests get silence segments matching slice
     length.
   - `transpose_to_timesliced(output_breaks: list[AudioSegment])
     → list[AudioSegment]` — axis swap.
4. **Render module.**
   `scripts/export/ot-doom/render.py`, modelled on
   `octatrack/render.py`:
   - Load + validate export (same `REQUIRED_CTX` plus we need
     `eventsPerCycle == 8` and `nSlices == 16`).
   - For each non-empty bank-cell, expand the polymetric break to
     a per-event break-name list (reuse the existing
     `expand_cell` helper — copy or factor into a shared module),
     compute B' (length 16, source breaks padded), render layer 1
     and layer 2 in memory, write timesliced wavs to a per-cell
     temp dir, call `project.add_sample` and `set_equal_slices`
     for each.
   - Configure track 1 Flex on the default slot, set
     `setup.slice = SliceMode.ON`.
   - Build the 16-step pattern: every step active, sample_lock per
     the |B|-aware mapping `(i * |B|) // 16`, slice_index = 0.
   - Build scenes 1 and 2 with `playback_param2 = 0` and `127`
     respectively; set `active_scene_a = 0`, `active_scene_b = 1`.
   - `project.to_zip(OUTPUT_DIR / f'{name}.zip')`.
5. **Smoke test** with a tiny export: 1 row × 1 cell, 4 unique
   breaks, verify the zip extracts to a valid project layout, the
   markers.work has 16 slices per slot, and the bank file references
   slot 1..k correctly.
6. **Wire up CLI parity** with the existing target — same args,
   same `common.cli` parser.

## Open questions / risks

- Sample length normalisation: the existing target hands each
  source break to OT untouched. Here, layer 1's output is fixed at
  16 1/16-steps regardless of source break tempo. If a source
  break wav isn't already at the project BPM, our layer 1 render
  will be the wrong duration. Mitigation: assume source breaks are
  at the project tempo (the gist already curates them so), and add
  a length sanity check (warn if abs(source_duration_steps - 32) >
  0.5).
- 128-slot ceiling: 16 banks × 1 cell × 16 timesliced wavs = 256
  slots, exceeds the 128-slot pool. Mitigation: build per-bank
  projects (one zip per row) when total slot demand exceeds 128.
  For the initial milestone, assume small exports (≤8 banks × 16
  slots = 128).
- Static vs Flex: prompt didn't specify. Flex is RAM and matches
  the existing target's choice; static would let us go bigger but
  streams from CF. Default Flex.
- Length mode: leaving at octapy default (TIME, len=127). The
  forum thread doesn't actually require changing this — slice
  mode on is the only mandatory SRC tweak. If field testing shows
  artefacts at slice boundaries, revisit.

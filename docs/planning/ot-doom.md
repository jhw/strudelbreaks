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

1. **Identify the source breakbeats.** The cell stores a list of break
   names (Strudel `{a b c d}%N` polymetric form, expanded via STRUDEL.md's
   `i*M//N` rule). Let `B = unique(cell.break)` — the input breaks. We
   require `|B| ∈ {4, 8, 16}` (see "|B| validation" below). Pad `B` by
   repetition until `|B'| = 16`: `B' = [B[0], B[1], …, B[|B|-1], B[0], …]`.
   Padding-by-repeat (rather than silence) ensures every fader position
   lands on real audio.

2. **Render the 16 timesliced wavs in one pass.** For each step
   `i ∈ 0..15` (one per OT pattern step), and each break-position
   `j ∈ 0..15` (one per sub-slice in the timesliced wav):

   ```
   sub_slice[i][j] = source_slice( B'[j], pattern_idxs[i] )
   timesliced[i]   = concat_over_j(sub_slice[i][0..15]) with fades
   ```

   where `source_slice(name, k)` is the k-th of 16 equal slices of the
   wav for break `name`, and `pattern_idxs[i]` is the captured Strudel
   pattern's slice index at event `i` (or `None` for a rest →
   silence-segment of one slice's length).

   Source break wavs are 32 1/16-steps long; 16 equal slices → 2
   steps per slice (same as the existing octatrack target). Each
   timesliced wav therefore contains `16 * (2/16-step)` = 2 bars of
   audio at the project tempo, and itself slices into 16 sub-slices
   of 2 1/16-steps each.

   See "One pass, not two" below for why this is a direct mapping
   rather than two render layers.

3. **Bind to slot manager.** Each *unique* timesliced wav is added
   via `project.add_sample(path, slot_type='FLEX')` — that's 16, 8,
   or 4 flex slots per bank depending on `|B|`. Each slot is then
   sliced into 16 equal slices via the existing `set_equal_slices`
   helper (copy-pasted from `octatrack/render.py` — the helper itself
   is generic).

   Why fewer unique slots when `|B| < 16`? Because of the structure
   of `B'`: padding-by-repeat means `B'[j] = B'[j + |B|]`, and since
   `pattern_idxs` doesn't depend on `j`, the resulting `timesliced[i]`
   wavs are pairwise identical at offsets `i, i+|B|, …`. The slot
   manager dedups by ot_path, so we only emit `|B|` unique files.

4. **Pattern.** A 16-step pattern; trig at every step `i`. Sample
   lock per the `|B|`-aware mapping:

   ```
   step i → timesliced slot index = (i * |B|) // 16
   ```

   - `|B| = 16`: diagonal — step `i` → slot `i`.
   - `|B| = 8`: each slot covers 2 consecutive steps.
   - `|B| = 4`: each slot covers 4 consecutive steps (steps 0–3 →
     slot 0, …, steps 12–15 → slot 3).

   Every step's `slice_index` is locked to 0 (= break 0 = scene-A).
   The crossfader sweeps STRT to walk into sub-slices 1..15.

5. **Track SRC config.** On the part, audio track 1 is configured
   Flex (default slot = slot 1):
   - `t1.setup.slice = SliceMode.ON` — slice mode on, so STRT
     selects a slice instead of a linear position. This is the lever
     the scenes pull, and it's the only mandatory SRC tweak.
   - `length_mode` left at octapy's default (`TIME`, length=127).
     With slice mode on + length=127, the OT plays the *full*
     selected slice. We don't need to change this.

6. **Scenes.** On the bank's part 1, configure two scenes (1 and 2),
   each one locking track 1's `playback_param2` (STRT, the slice
   selector):
   - Scene 1: `track(1).playback_param2 = 0` → slice 0
     (`= break 0` across every step).
   - Scene 2: `track(1).playback_param2 = 127` → slice 15
     (`= break 15` across every step). With 16 slices, the STRT range
     0..127 quantises across slices, so 127 selects the last slice.

   Then `part.active_scene_a = 0; active_scene_b = 1` (zero-indexed).
   The crossfader continuously interpolates STRT between 0 and 127,
   walking through sub-slices 0..15 of every timesliced wav — which
   is exactly "switch dynamically across breaks" by construction
   (slice `j` of `timesliced[i]` = step-i of break-j).

## |B| validation

The renderer requires `|B| ∈ {4, 8, 16}` per cell. These are the
divisors of 16 that distribute cleanly under the
`step → slot = (i * |B|) // 16` mapping; any other count creates
uneven slot stretches, ambiguous fader behaviour, or both.

If a cell has `|B| ∉ {4, 8, 16}` (including the very common
`|B| = 1` or `|B| = 2` from current Tempera defaults), the renderer
exits with a clear message naming the offending bank/cell and the
unique break names found. No silent padding to the nearest power.
The error is the contract — fix the source template to produce
4/8/16 unique names per cell.

> **Note on Tempera compatibility.** Tempera as configured today
> emits cells with `|B| ≤ 2` (see `BREAK_ALT_NAMES_MIN/MAX = 1` and
> `BREAK_ALT_SLOTS_MIN/MAX = 1..2` in `tempera.strudel.js`). This
> exporter therefore won't accept current Tempera captures
> unmodified. Adapting the source template (raise alt-name caps, or
> use a different Strudel template that emits 4/8/16-name vocabs)
> is a prerequisite — out of scope for this milestone.

## One pass, not two

The user's prompt frames the render as two layers:

- Layer 1: 16 *output breaks*, each a bar-long render of the cell's
  pattern through one source break.
- Layer 2: time-slice each output break into 16 slices and transpose
  → 16 *timesliced wavs*.

Algebraically the two layers compose into a direct mapping:

```
output_break[j].slice_at(i) = source_slice( B'[j], pattern_idxs[i] )
                            = sub_slice[i][j]
timesliced[i]               = concat_over_j(sub_slice[i][0..15])
```

So in production we skip the intermediate output-break audio and
build each `timesliced[i]` straight from `source_slice(B'[j],
pattern_idxs[i])`. No layer-1 buffers, no in-memory transpose
step.

Layer 1 is still useful conceptually, and as a debug surrogate
(audition any single break played through the captured pattern by
re-assembling `output_break[j]` from `sub_slice[*][j]`). But the
shipped renderer is one pass.

## Fades, pops, and attack crispness

Pop/click risk lives at slice boundaries: the **end** of one slice
hitting the **start** of the next. Beat attacks (kicks, snares)
sit in the first ~5–20 ms of a slice; symmetric fades round those
attacks audibly.

Asymmetric envelope:

- **Fade-out**: 2–3 ms cosine ramp at every slice's *tail*. The
  tail is mid-decay anyway, so this is inaudible-soft and
  eliminates the discontinuity into the next slice's transient.
- **Fade-in**: 0.3–0.5 ms (~15–22 samples at 44.1 kHz). A
  pure click guard, sub-perceptual on a transient — the attack
  envelope itself dominates anything we'd add. We do *not* skip
  fade-in entirely: even a perfectly aligned slice can start at
  a non-zero sample, and that DC step pops.

This matches the user's intent ("only small ones, particularly
fade in").

Open option for v2: equal-power crossfade between adjacent
sub-slices (~1 ms overlap) instead of independent fade-out + fade-in.
That's mathematically cleaner (no double-attenuation in the overlap
region) but more code; defer until field testing shows the
asymmetric-fade approach has audible artefacts.

Rests (`pattern_idxs[i] is None`) become silence segments of one
slice's length — they get the same fade-in/out treatment for
consistency, but on silence both are no-ops.

## Audio dependencies

- Add `pydub>=0.25` to `requirements.txt` and install in the
  existing venv. pydub uses ffmpeg only for non-WAV codecs;
  pure-WAV operations (decode/encode/concat/fade/slice) go through
  stdlib `wave`, so no extra system dependency.

## File layout in the project bundle

For a cell with `|B| = 16`, the slot pool fills 1..16. With
`|B| = 4`, only 1..4. The `flex_count` auto-update logic in
`Project.add_sample` keeps banks in sync. Filenames are namespaced
per bank/cell so reuse across cells is safe but explicit:

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
    └── b01p01_t15.wav
```

(Identical timesliced wavs across cells dedup via the slot manager's
`ot_path` key.)

## Implementation steps

1. **Scaffolding.** Create `scripts/export/ot-doom/` with stub
   `__init__.py` (so common imports work), copy the four push/clean
   scripts, retarget `tmp/octatrack/` → `tmp/ot-doom/` in each.
2. **Add `pydub`** to `requirements.txt`; install in `.venv`.
3. **Audio helpers.** New module `scripts/export/ot-doom/audio.py`
   with:
   - `load_break_wav(path) → AudioSegment` (cached by path).
   - `slice_break(seg, n_slices=16) → list[AudioSegment]` — equal
     slices of one source wav.
   - `render_timesliced(source_slices, b_prime, pattern_idxs,
     fade_in_ms=0.4, fade_out_ms=2.5) → AudioSegment` — build one
     timesliced wav by concat over `j ∈ 0..15` of
     `source_slices[B'[j]][pattern_idxs[i]]` (or silence on rest)
     with the asymmetric fade envelope.
4. **Render module.** `scripts/export/ot-doom/render.py`, modelled
   on `octatrack/render.py`:
   - Load + validate export (same `REQUIRED_CTX`; require
     `eventsPerCycle == 8`, `nSlices == 16`).
   - Per cell, compute `B = unique(cell.break)` and exit if
     `|B| ∉ {4, 8, 16}`.
   - Load source slices for each name in `B` (cache by name across
     cells).
   - For each step `i ∈ 0..15`, render `timesliced[i]` in memory;
     write only the `|B|` unique files (the first `|B|` indices,
     since the rest dedup) to a per-cell temp dir.
   - `project.add_sample(path)` for each unique file →
     `set_equal_slices(slot, 16)`.
   - Configure track 1 Flex on slot 1, set
     `setup.slice = SliceMode.ON`.
   - Build the 16-step pattern: every step active, sample_lock per
     `(i * |B|) // 16`, slice_index = 0.
   - Build scenes 1 and 2 with `playback_param2 = 0` and `127`;
     set `active_scene_a = 0`, `active_scene_b = 1`.
   - `project.to_zip(OUTPUT_DIR / f'{name}.zip')`.
5. **Smoke test** with a synthetic export of one cell at `|B| = 4`,
   verify: zip extracts to a valid project layout, `markers.work`
   has 16 slices per of the 4 slots, bank file references slots
   1..4, scenes 1+2 lock STRT, the 16 trigs sample-lock to the
   right slot via `(i*4)//16`.
6. **Wire up CLI parity** with the existing target — same args,
   same `common.cli` parser.

## Open questions / risks

- **Source-break tempo.** Layer-1 timing is fixed in 1/16-steps at
  the project tempo. If a source wav isn't already at project tempo
  (= 32 1/16-steps long for the canonical 2-bar break), the
  resulting timesliced wav has the wrong groove. Mitigation: warn
  when `abs(source_duration_steps - 32) > 0.5` and let the user
  decide whether to ship.
- **128-slot ceiling.** 16 banks × 1 cell × 16 timesliced wavs =
  256 slots, exceeds the 128 flex pool. With `|B|=4` it's 64 slots
  total — fine. With `|B|=16` we'd need to split into per-bank
  projects when total demand > 128.
- **Static vs Flex.** Default Flex (matches the existing target,
  RAM-fast, simpler). Static would let us pack more samples but
  streams from CF; revisit only if 128-slot becomes the binding
  constraint.
- **Length mode.** Leaving at octapy default (`TIME`, len=127). The
  forum thread doesn't require changing this — slice mode on is the
  only mandatory SRC tweak. Revisit if field testing shows artefacts
  at sub-slice boundaries.
- **Crossfade vs independent fades.** Asymmetric independent fades
  are the v1 choice. If sub-slice boundaries audibly click on real
  hardware, switch to a 1ms equal-power crossfade.

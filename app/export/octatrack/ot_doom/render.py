#!/usr/bin/env python3
"""Render a tempera captures JSON export into an Octatrack megabreak-of-doom
project zip — cell-input variant.

Each non-empty row of the captures becomes one OT pattern. Patterns
are packed 16 per bank: rows 1..16 → bank 1 patterns 1..16, rows
17..32 → bank 2 patterns 1..16, etc. Tempera-realistic exports are
small enough that one bank usually suffices.

Within a bank every pattern shares part 1 — and therefore the part's
scenes — so every row in a bank must have the same `|C|` (cells per
row). The validator rejects mixed-`|C|` banks with a clear message.

Two stem modes (controlled by `split_stems`, defaults True):

* **`split_stems=True`** (default): each break is rendered as three
  drum stems (kick / snare / hat) via beatwav. For each chain position
  k, the per-stem chains are stacked into one packed sample of
  `3 * |C|` slices — kick block (slices 0..|C|-1), snare block
  (slices |C|..2|C|-1), hat block (slices 2|C|..3|C|-1). T1, T2, T3
  each sample-lock to the same packed slot; per-track scene values
  address each stem's slice range so the crossfader sweeps each kit
  piece independently.

  Per-track scenes on part 1 (shared across the bank's patterns):

    T1 (kick):  scene A slice_index = 0,         scene B slice_index = |C| - 1
    T2 (snare): scene A slice_index = |C|,       scene B slice_index = 2|C| - 1
    T3 (hat):   scene A slice_index = 2|C|,      scene B slice_index = 3|C| - 1

* **`split_stems=False`**: each break is rendered as one mixed sample.
  Each chain holds the |C| segments of the cells' mixed audio, T1
  alone plays it, scenes A/B sweep across `slice_index = 0` ↔
  `|C| - 1`. Use this for an A/B fidelity check against the Strudel
  source.

Project flex pool is 128 slots. Total chain count = sum(`|C|` per
row) and is validated up-front — independent of stem mode, since the
split-mode chain packs the three stems into one slot.

FX layout (configured once on part 1):
  T1, T2, T3: FX1 = DJ_EQ, FX2 = COMPRESSOR     (split mode)
  T1 only:    FX1 = DJ_EQ, FX2 = COMPRESSOR     (mixed mode)
  T8:         FX1 = CHORUS,  FX2 = DELAY        (mix = 64 each)

This differs from the forum-canonical megabreak (which crossfades
between source breaks rather than between captured patterns). See
docs/export/ot-doom.md for the full design + comparison.

Imported and called by the FastAPI server (`app/exporters.py`) — no CLI;
the server writes the captures payload to a temp file and points
`output_dir` / `render_dir` at a temp dir.

Output:
    <output_dir>/<name>.zip       (defaults to tmp/ot-doom/)
    <render_dir>/<name>/...       intermediate per-bank chain WAVs
                                  (defaults to tmp/ot-doom-render/)
"""
from __future__ import annotations

import pathlib
import sys

from octapy import (
    FX1Type,
    FX2Type,
    Project,
    SliceMode,
)

from app.export.common import sample_source
from app.export.common.audio_fades import (
    DEFAULT_FADE_IN_MS,
    DEFAULT_FADE_OUT_MS,
)
from app.export.common.schema import load_export
from app.export.octatrack._flatten import flatten_cells, regroup_doom

from .audio import (
    OT_SAMPLE_RATE,
    build_matrix_chain,
    equal_slices,
    export_wav,
    load_break,
    render_cell_audio,
)


# Per-drum stems we ask beatwav to produce in split mode. Maps to OT
# audio tracks 1/2/3 with per-track scenes addressing each stem's
# slice range inside the packed chain slot.
TRACKS = ('kick', 'snare', 'hat')

# Mixed-mode pseudo-stem: one mixed render bound to T1 only. Same
# downstream cache/key shape as split mode so the per-stem helpers
# don't have to branch.
MIXED_STEM = 'mixed'


def _stem_tracks(split_stems):
    """OT audio-track stems used for this render.

    Split mode: three stems on T1/T2/T3. Mixed mode: one combined
    stem on T1 only — useful for an A/B fidelity check against the
    Strudel source.
    """
    return TRACKS if split_stems else (MIXED_STEM,)

# Source break wavs are 32 steps (2 bars at 1/16). N_SLICES=16 cuts them
# into 16 slices of 2 steps each — same scheme as the existing octatrack
# target. See app/export/octatrack/ot_basic/render.py for the ghost-beat
# rationale.
N_SLICES = 16
N_PATTERN_STEPS = 16          # 1 bar at 1/16 — one Strudel cycle.
ALLOWED_INPUT_COUNTS = (4, 8, 16)

PATTERNS_PER_BANK = 16        # OT bank capacity.
MAX_BANKS = 16                # OT project capacity.
FLEX_SLOT_LIMIT = 128         # OT project-wide flex pool.

# Wet/dry value for the T8 send/master FX (CHORUS, DELAY). 64 ≈ 50%
# on the OT 0-127 parameter scale. The two effects use different
# parameter names for the wet control: CHORUS exposes .mix, DELAY
# exposes .send.
T8_FX_LEVEL = 64

# DELAY feedback (encoder B on the FX2 page; see octapy
# `FX_PARAM_NAMES[FX2Type.DELAY]`). 32 = ¼ of the 0-127 range —
# audible repeats without runaway tail.
T8_DELAY_FEEDBACK = 32

REPO_ROOT = pathlib.Path(__file__).resolve().parents[4]
OUTPUT_DIR = REPO_ROOT / 'tmp' / 'ot-doom'
RENDER_DIR = REPO_ROOT / 'tmp' / 'ot-doom-render'

REQUIRED_CTX = ('gistUser', 'gistId', 'bpm', 'eventsPerCycle', 'nSlices')


def set_equal_slices(project, slot, n_slices, segment_ms, sample_rate):
    """N equal slice markers across `n_slices * segment_ms` of audio."""
    slot_markers = project.markers.get_slot(slot, is_static=False)
    total_ms = n_slices * segment_ms
    frame_count = int(round(total_ms * sample_rate / 1000))
    slot_markers.sample_length = frame_count
    slices = [
        (int(round(i * segment_ms)), int(round((i + 1) * segment_ms)))
        for i in range(n_slices)
    ]
    slot_markers.set_slices_ms(slices, sample_rate=sample_rate)
    project.markers.set_slot(slot, slot_markers, is_static=False)


def _ensure_track_slices(name, stem, source_slice_cache):
    """Lazy-load equal_slices for one (break, stem) pair. Cache keyed
    by (name, stem) so repeat references inside a row are free.

    `stem` is one of TRACKS in split mode or MIXED_STEM in mixed mode;
    the cache layout is the same either way."""
    key = (name, stem)
    if key in source_slice_cache:
        return
    path = source_slice_cache['__paths__'][name][stem]
    seg = load_break(path)
    source_slice_cache[key] = equal_slices(seg, N_SLICES)


def _per_stem_source_slices(source_slice_cache, stem):
    """View into the cache that exposes only one stem's slices, keyed
    by name — what `render_cell_audio` expects. The reserved
    `'__paths__'` entry holds the path map and is skipped."""
    return {
        key[0]: slices
        for key, slices in source_slice_cache.items()
        if isinstance(key, tuple) and key[1] == stem
    }


def _render_row_chains(
    project,
    bank_num,
    pattern_num,
    cells,
    events_per_cycle,
    stem_tracks,
    source_slice_cache,
    bank_render_dir,
    *,
    fade_in_ms,
    fade_out_ms,
):
    """Render a row's audio per stem, stack into per-position packed
    chains, write WAVs, register flex slots.

    Returns the per-chain-position flex slot list. Caller wires the
    slots into the pattern's trigs (one trig per chain position fires
    on each enabled OT track sample-locked to the same packed slot).
    """
    n = len(cells)

    # Lazy-load per-stem source slices for every break in this row.
    for cell in cells:
        for name in cell['break']:
            for stem in stem_tracks:
                _ensure_track_slices(name, stem, source_slice_cache)

    anchor_slice = next(s[0] for k, s in source_slice_cache.items()
                        if k != '__paths__')
    sample_rate = anchor_slice.frame_rate

    # Render each cell to one bar of audio per stem.
    per_stem_inputs = {}
    for stem in stem_tracks:
        stem_slices = _per_stem_source_slices(source_slice_cache, stem)
        per_stem_inputs[stem] = [
            render_cell_audio(cell, stem_slices, events_per_cycle,
                              fade_in_ms=fade_in_ms,
                              fade_out_ms=fade_out_ms)
            for cell in cells
        ]

    bar_ms = len(per_stem_inputs[stem_tracks[0]][0])
    segment_ms = bar_ms / n
    packed_slices_per_chain = len(stem_tracks) * n

    # Build N packed chains and bind each as a flex slot. Split mode:
    # one n-slice block per stem (kick → snare → hat). Mixed mode: a
    # single n-slice chain. Per-track scenes on part 1 address each
    # stem's slice range.
    bank_render_dir.mkdir(parents=True, exist_ok=True)
    flex_slots = []
    for k in range(n):
        chain_seg = build_matrix_chain(per_stem_inputs, list(stem_tracks), k, n)
        wav_path = (bank_render_dir
                    / f'b{bank_num:02d}_p{pattern_num:02d}_chain{k:02d}.wav')
        export_wav(chain_seg, wav_path)
        slot = project.add_sample(str(wav_path.resolve()), slot_type='FLEX')
        set_equal_slices(project, slot, packed_slices_per_chain,
                         segment_ms=segment_ms,
                         sample_rate=sample_rate)
        flex_slots.append(slot)

    return flex_slots


def _configure_pattern(bank, pattern_num, flex_slots, n, flex_tracks):
    """Write a single pattern: N trigs at 16/N spacing on every flex
    track, all sample-locked to the same packed slot for that chain
    position. No per-trig slice_index lock — trigs inherit the
    per-track slice_index from the active scene on part 1."""
    interval = N_PATTERN_STEPS // n
    pattern = bank.pattern(pattern_num)
    pattern.scale_length = N_PATTERN_STEPS
    active_steps = [k * interval + 1 for k in range(n)]
    for track_num in flex_tracks:
        track = pattern.audio_track(track_num)
        track.active_steps = active_steps
        for k, step_num in enumerate(active_steps):
            step = track.step(step_num)
            step.sample_lock = flex_slots[k]


def flex_track_nums(n_stems, neighbour):
    """OT track numbers each stem plays from. Same scheme as ot-basic:
    neighbour mode interleaves a partner track between every flex
    track, so stems land on T1, T3, T5 instead of T1, T2, T3."""
    step = 2 if neighbour else 1
    return tuple(1 + i * step for i in range(n_stems))


def _configure_part(part, default_packed_slot, n, flex_tracks, *, neighbour=False):
    """Configure part 1 once per bank.

    Each flex track sample-plays from the same packed slots, with
    per-track scene values addressing that stem's slice range. Flex
    tracks take DJ_EQ + COMPRESSOR.

    Without neighbour: T8 hosts CHORUS + DELAY at mix=64 as the
    project-level send chain.

    With neighbour: every flex track is paired with a neighbour
    machine on the next track (FILTER + DELAY). T8 keeps just the
    spatializer — delay moved to the per-track neighbours.
    """
    for stem_idx, track_num in enumerate(flex_tracks):
        t = part.audio_track(track_num)
        t.configure_flex(default_packed_slot)
        t.setup.slice = SliceMode.ON
        t.fx1_type = FX1Type.DJ_EQ
        t.fx2_type = FX2Type.COMPRESSOR

    # Per-track scenes — each stem occupies n contiguous slices in the
    # packed slot. Stem i sweeps slice_index `i*n → i*n + (n - 1)` on
    # its own flex track. The OT crossfader interpolates raw STRT
    # (= slice_index * 2) between scene A and scene B; with this range
    # each input segment is reachable on its own band of the fader.
    for stem_idx, track_num in enumerate(flex_tracks):
        scene_a = part.scene(1).track(track_num)
        scene_b = part.scene(2).track(track_num)
        scene_a.slice_index = stem_idx * n
        scene_b.slice_index = stem_idx * n + (n - 1)
    part.active_scene_a = 0
    part.active_scene_b = 1

    if neighbour:
        for nb_num in (t + 1 for t in flex_tracks):
            nb = part.audio_track(nb_num)
            nb.configure_neighbor()
            nb.fx1_type = FX1Type.FILTER
            nb.fx2_type = FX2Type.DELAY
            nb.fx2.send = T8_FX_LEVEL
            nb.fx2.feedback = T8_DELAY_FEEDBACK
        t8 = part.audio_track(8)
        t8.fx1_type = FX1Type.SPATIALIZER
        t8.fx2_type = FX2Type.OFF
    else:
        t8 = part.audio_track(8)
        t8.fx1_type = FX1Type.CHORUS
        t8.fx1.mix = T8_FX_LEVEL    # CHORUS: wet/dry on .mix
        t8.fx2_type = FX2Type.DELAY
        t8.fx2.send = T8_FX_LEVEL          # DELAY: wet level on .send (no .mix here)
        t8.fx2.feedback = T8_DELAY_FEEDBACK  # encoder B — see T8_DELAY_FEEDBACK


def render_bank(
    project,
    bank_num,
    rows,
    events_per_cycle,
    stem_tracks,
    source_slice_cache,
    bank_render_dir,
    *,
    fade_in_ms,
    fade_out_ms,
    neighbour=False,
):
    """Render up to PATTERNS_PER_BANK rows into one OT bank.

    All rows in the bank must share the same `|C|` — every pattern in
    the bank shares part 1's per-track scene config (each enabled
    track's `slice_index` 0 .. |C| - 1, offset by `track_idx * |C|`),
    which is `|C|`-dependent. Mixed-`|C|` banks fail loudly.
    """
    if not rows:
        return
    if len(rows) > PATTERNS_PER_BANK:
        sys.exit(f'bank {bank_num}: {len(rows)} rows exceeds {PATTERNS_PER_BANK}')

    cs = [len(cells) for cells in rows]
    n = cs[0]
    if n not in ALLOWED_INPUT_COUNTS:
        sys.exit(
            f'bank {bank_num}, row 1: |C|={n} cells '
            f'(allowed {sorted(ALLOWED_INPUT_COUNTS)}) — '
            f'add or remove cells in tempera so each row has 4, 8, or 16'
        )
    if any(c != n for c in cs):
        bad = [(i + 1, c) for i, c in enumerate(cs) if c != n]
        sys.exit(
            f'bank {bank_num}: mixed |C| within bank '
            f'(row 1 has |C|={n}, conflicts: {bad}) — '
            f'all rows in a bank must share the same cell count because '
            f'they share part 1\'s per-track scene config'
        )

    # Render each row's chains and capture the per-pattern slot list.
    pattern_slots = []
    for pattern_idx, cells in enumerate(rows):
        slots = _render_row_chains(
            project, bank_num, pattern_idx + 1, cells,
            events_per_cycle, stem_tracks,
            source_slice_cache, bank_render_dir,
            fade_in_ms=fade_in_ms,
            fade_out_ms=fade_out_ms,
        )
        pattern_slots.append(slots)

    bank = project.bank(bank_num)
    part = bank.part(1)
    flex_tracks = flex_track_nums(len(stem_tracks), neighbour)
    # Default flex slot is the first chain of the first pattern — only
    # used when a step has no sample_lock, which never happens in our
    # patterns.
    _configure_part(part, pattern_slots[0][0], n, flex_tracks,
                    neighbour=neighbour)

    # Write each pattern with its own chain trigs.
    for pattern_idx, slots in enumerate(pattern_slots):
        _configure_pattern(bank, pattern_idx + 1, slots, n, flex_tracks)


def _resolve_stem_paths(*, gist_user, gist_id, names, target_bpm, stem_tracks):
    """Fetch break audio in the layout `_render_row_chains` expects:
    `{name: {stem: path}}`. Split mode delegates to the per-track JSON
    renderer; mixed mode delegates to the regular flat resolver and
    re-shapes to the per-stem cache layout."""
    if stem_tracks == (MIXED_STEM,):
        flat = sample_source.resolve_break_paths(
            gist_user=gist_user,
            gist_id=gist_id,
            names=names,
            source='json',
            target_bpm=target_bpm,
            target_sample_rate=OT_SAMPLE_RATE,
        )
        return {n: {MIXED_STEM: p} for n, p in flat.items()}
    return sample_source.resolve_break_paths(
        gist_user=gist_user,
        gist_id=gist_id,
        names=names,
        source='json',
        target_bpm=target_bpm,
        target_sample_rate=OT_SAMPLE_RATE,
        tracks=stem_tracks,
    )


def build_project(export_path, name, *, split_stems=True, flatten=False,
                  neighbour=False, render_dir=None,
                  fade_in_ms=DEFAULT_FADE_IN_MS,
                  fade_out_ms=DEFAULT_FADE_OUT_MS):
    payload, ctx = load_export(export_path, REQUIRED_CTX)
    if ctx['nSlices'] != N_SLICES:
        sys.exit(f'nSlices {ctx["nSlices"]} != {N_SLICES} (ot-doom assumes 16)')

    rows_in = [b for b in (payload.get('banks') or []) if b]
    if not rows_in:
        sys.exit('no non-empty rows in export')
    if flatten:
        # Collapse list-of-lists into a flat cell list, then re-row by
        # greedy 16/8/4 decomposition. Stray patterns (remainder < 4)
        # raise ValueError → caller surfaces in the UI status panel.
        try:
            rows_in = regroup_doom(flatten_cells(rows_in))
        except ValueError as e:
            sys.exit(str(e))

    max_rows = MAX_BANKS * PATTERNS_PER_BANK
    if len(rows_in) > max_rows:
        sys.exit(
            f'too many rows: {len(rows_in)} > {max_rows} '
            f'({MAX_BANKS} banks × {PATTERNS_PER_BANK} patterns)'
        )

    # Total chain slots over the whole project. The OT flex pool is
    # shared across banks, so this is a project-wide ceiling. Mixed
    # and split modes both use one slot per chain position (split mode
    # packs the three stems into that slot).
    total_slots = sum(len(cells) for cells in rows_in)
    if total_slots > FLEX_SLOT_LIMIT:
        sys.exit(
            f'flex slot limit exceeded: {total_slots} chains '
            f'(sum of |C| across {len(rows_in)} rows) > {FLEX_SLOT_LIMIT} '
            f'(OT project flex pool). Drop rows or use rows with smaller |C|.'
        )

    all_names = sorted({
        break_name
        for cells in rows_in
        for cell in cells
        for break_name in cell['break']
    })

    stem_tracks = _stem_tracks(split_stems)
    stem_paths = _resolve_stem_paths(
        gist_user=ctx['gistUser'],
        gist_id=ctx['gistId'],
        names=all_names,
        target_bpm=ctx['bpm'],
        stem_tracks=stem_tracks,
    )

    project = Project.from_template(name.upper()[:16])
    project.settings.tempo = float(ctx['bpm'])
    project.master_track = True

    # Source-slice cache shared across rows. The path map lives under a
    # reserved key so render_bank can lazy-load on first reference per
    # (break, stem) without changing the function signature.
    source_slice_cache = {'__paths__': stem_paths}

    render_root = pathlib.Path(render_dir) if render_dir is not None else RENDER_DIR
    row_render_root = render_root / name
    if row_render_root.exists():
        for p in row_render_root.rglob('*'):
            if p.is_file():
                p.unlink()

    # Pack rows into banks. Same-|C| within a bank is required (every
    # pattern shares part 1's per-track scenes). Default mode (no
    # flatten): pack 16 patterns per bank sequentially — render_bank
    # rejects mixed-|C|-within-a-bank loudly. Flatten mode produces
    # rows with descending sizes (16-cell rows first, then 8, then 4);
    # we start a new bank whenever |C| changes so each bank stays
    # uniform without the user having to re-arrange anything.
    if flatten:
        bank_groups = _split_into_banks(rows_in, PATTERNS_PER_BANK)
    else:
        bank_groups = [
            rows_in[i:i + PATTERNS_PER_BANK]
            for i in range(0, len(rows_in), PATTERNS_PER_BANK)
        ]
    for bank_num, bank_rows in enumerate(bank_groups, start=1):
        render_bank(
            project,
            bank_num,
            bank_rows,
            ctx['eventsPerCycle'],
            stem_tracks,
            source_slice_cache,
            row_render_root / f'bank{bank_num:02d}',
            fade_in_ms=fade_in_ms,
            fade_out_ms=fade_out_ms,
            neighbour=neighbour,
        )

    return project


def _split_into_banks(rows, patterns_per_bank):
    """Group rows into banks. A new bank starts when (a) the current
    bank is full, or (b) the next row's |C| differs from the bank's
    leading |C|. Mixed-|C|-within-a-bank is rejected downstream by
    render_bank, so this packer keeps banks uniform up-front.
    """
    banks = []
    current = []
    current_n = None
    for row in rows:
        n = len(row)
        if current and (n != current_n or len(current) >= patterns_per_bank):
            banks.append(current)
            current = []
            current_n = None
        if not current:
            current_n = n
        current.append(row)
    if current:
        banks.append(current)
    return banks


def render(export_path, name, *, split_stems=True, flatten=False,
           neighbour=False, output_dir=None, render_dir=None,
           fade_in_ms=DEFAULT_FADE_IN_MS,
           fade_out_ms=DEFAULT_FADE_OUT_MS):
    project = build_project(export_path, name,
                            split_stems=split_stems,
                            flatten=flatten,
                            neighbour=neighbour,
                            render_dir=render_dir,
                            fade_in_ms=fade_in_ms,
                            fade_out_ms=fade_out_ms)
    out_dir = pathlib.Path(output_dir) if output_dir is not None else OUTPUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    zip_path = out_dir / f'{name}.zip'
    project.to_zip(zip_path)
    return zip_path

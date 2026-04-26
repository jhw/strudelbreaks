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

The cells of each row are the doom *inputs* (`|C|` ∈ {4, 8, 16}):
each cell renders to a bar of audio, then `|C|` matrix chains are
built where chain[k] is the k-th equal segment of every input
concatenated. `|C|` flex slots per row hold the chains with `|C|`
slice markers each. The pattern fires `|C|` trigs at intervals of
16/`|C|`, each sample-locked to its chain. Part 1's scenes lock
track 1's slice_index to 0 and `|C|`-1 — the crossfader interpolates
between, walking the input axis: at any fader position s, every
trig plays segment k of input s, so input s plays in full
grid-aligned.

Project flex pool is 128 slots. Total chain count = sum(`|C|` per
row) and is validated up-front; the renderer fails with a clear
message if it would overflow.

This differs from the forum-canonical megabreak (which crossfades
between source breaks rather than between captured patterns). See
docs/export/ot-doom.md for the full design + comparison.

Usage:
    python scripts/export/ot-doom/render.py <path/to/export.json> [--name NAME] [--seed N]

Output:
    tmp/ot-doom/<name>.zip
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from octapy import (
    Project,
    SliceMode,
)

from common import sample_source
from common.cli import build_parser, require_file, resolve_name
from common.schema import load_export

from audio import (
    OT_SAMPLE_RATE,
    build_matrix_chain,
    equal_slices,
    export_wav,
    load_break,
    render_cell_audio,
)


# Source break wavs are 32 steps (2 bars at 1/16). N_SLICES=16 cuts them
# into 16 slices of 2 steps each — same scheme as the existing octatrack
# target. See scripts/export/octatrack/render.py for the ghost-beat
# rationale.
N_SLICES = 16
N_PATTERN_STEPS = 16          # 1 bar at 1/16 — one Strudel cycle.
ALLOWED_INPUT_COUNTS = (4, 8, 16)

PATTERNS_PER_BANK = 16        # OT bank capacity.
MAX_BANKS = 16                # OT project capacity.
FLEX_SLOT_LIMIT = 128         # OT project-wide flex pool.

SCRIPT_DIR = pathlib.Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent.parent.parent
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


def _render_row_chains(
    project,
    bank_num,
    pattern_num,
    cells,
    events_per_cycle,
    source_slice_cache,
    bank_render_dir,
):
    """Render a row's audio, write chain WAVs, register flex slots.

    Returns (flex_slots, sample_rate). Caller wires the slots into the
    pattern's trigs and the part's scenes.
    """
    n = len(cells)

    # Load source slices for every break referenced in this row, lazily.
    for cell in cells:
        for name in cell['break']:
            if name not in source_slice_cache:
                seg = load_break(source_slice_cache['__paths__'][name])
                source_slice_cache[name] = equal_slices(seg, N_SLICES)

    anchor_slice = next(s[0] for k, s in source_slice_cache.items()
                        if k != '__paths__')
    sample_rate = anchor_slice.frame_rate

    # Render each cell to one bar of audio.
    input_audios = [
        render_cell_audio(cell, source_slice_cache, events_per_cycle)
        for cell in cells
    ]

    bar_ms = len(input_audios[0])
    segment_ms = bar_ms / n

    # Build N chains and bind each as a flex slot with N slice markers.
    bank_render_dir.mkdir(parents=True, exist_ok=True)
    flex_slots = []
    for k in range(n):
        chain_seg = build_matrix_chain(input_audios, k, n)
        wav_path = (bank_render_dir
                    / f'b{bank_num:02d}_p{pattern_num:02d}_chain{k:02d}.wav')
        export_wav(chain_seg, wav_path)
        slot = project.add_sample(str(wav_path.resolve()), slot_type='FLEX')
        set_equal_slices(project, slot, n,
                         segment_ms=segment_ms,
                         sample_rate=sample_rate)
        flex_slots.append(slot)

    return flex_slots


def _configure_pattern(bank, pattern_num, flex_slots, n):
    """Write a single pattern: N trigs at 16/N spacing, each
    sample-locked to its chain. No per-trig slice_index lock — trigs
    inherit from the active scene on the part."""
    interval = N_PATTERN_STEPS // n
    pattern = bank.pattern(pattern_num)
    pattern.scale_length = N_PATTERN_STEPS
    track = pattern.audio_track(1)
    active_steps = [k * interval + 1 for k in range(n)]
    track.active_steps = active_steps
    for k, step_num in enumerate(active_steps):
        step = track.step(step_num)
        step.sample_lock = flex_slots[k]


def render_bank(
    project,
    bank_num,
    rows,
    events_per_cycle,
    source_slice_cache,
    bank_render_dir,
):
    """Render up to PATTERNS_PER_BANK rows into one OT bank.

    All rows in the bank must share the same `|C|` — every pattern in
    the bank shares part 1's scene config (`slice_index` 0 / `|C|`-1),
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
            f'they share part 1\'s scene config (slice_index 0 / |C|-1)'
        )

    # Render each row's chains and capture the per-pattern slot list.
    pattern_slots = []
    for pattern_idx, cells in enumerate(rows):
        slots = _render_row_chains(
            project, bank_num, pattern_idx + 1, cells,
            events_per_cycle, source_slice_cache, bank_render_dir,
        )
        pattern_slots.append(slots)

    # Configure part 1 once for the whole bank. Default flex slot is
    # the first chain of the first pattern — only used when a step has
    # no sample_lock, which never happens in our patterns.
    bank = project.bank(bank_num)
    part = bank.part(1)
    t1 = part.audio_track(1)
    t1.configure_flex(pattern_slots[0][0])
    t1.setup.slice = SliceMode.ON

    # Scenes drive the input axis. Scene A → input 0, Scene B → input
    # |C|-1. All patterns in this bank inherit, which is why we
    # require shared |C| above.
    part.scene(1).track(1).slice_index = 0
    part.scene(2).track(1).slice_index = n - 1
    part.active_scene_a = 0
    part.active_scene_b = 1

    # Write each pattern with its own chain trigs.
    for pattern_idx, slots in enumerate(pattern_slots):
        _configure_pattern(bank, pattern_idx + 1, slots, n)


def build_project(export_path, name, source='json'):
    payload, ctx = load_export(export_path, REQUIRED_CTX)
    if ctx['nSlices'] != N_SLICES:
        sys.exit(f'nSlices {ctx["nSlices"]} != {N_SLICES} (ot-doom assumes 16)')

    rows_in = [b for b in (payload.get('banks') or []) if b]
    if not rows_in:
        sys.exit('no non-empty rows in export')

    max_rows = MAX_BANKS * PATTERNS_PER_BANK
    if len(rows_in) > max_rows:
        sys.exit(
            f'too many rows: {len(rows_in)} > {max_rows} '
            f'({MAX_BANKS} banks × {PATTERNS_PER_BANK} patterns)'
        )

    # Total chain slots over the whole project. The OT flex pool is
    # shared across banks, so this is a project-wide ceiling.
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

    paths = sample_source.resolve_break_paths(
        gist_user=ctx['gistUser'],
        gist_id=ctx['gistId'],
        names=all_names,
        source=source,
        target_bpm=ctx['bpm'],
        target_sample_rate=OT_SAMPLE_RATE,
    )

    project = Project.from_template(name.upper()[:16])
    project.settings.tempo = float(ctx['bpm'])
    project.master_track = True

    # Source-slice cache shared across rows. The path map lives under a
    # reserved key so render_bank can lazy-load on first reference per
    # row without changing the function signature.
    source_slice_cache = {'__paths__': paths}

    row_render_root = RENDER_DIR / name
    if row_render_root.exists():
        for p in row_render_root.rglob('*'):
            if p.is_file():
                p.unlink()

    # Pack rows sequentially: rows 1..16 → bank 1, rows 17..32 → bank
    # 2, etc. Same-|C|-per-bank validation happens inside render_bank.
    for bank_idx in range(0, len(rows_in), PATTERNS_PER_BANK):
        bank_num = (bank_idx // PATTERNS_PER_BANK) + 1
        bank_rows = rows_in[bank_idx:bank_idx + PATTERNS_PER_BANK]
        render_bank(
            project,
            bank_num,
            bank_rows,
            ctx['eventsPerCycle'],
            source_slice_cache,
            row_render_root / f'bank{bank_num:02d}',
        )

    return project


def render(export_path, name, source='json'):
    project = build_project(export_path, name, source=source)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    zip_path = OUTPUT_DIR / f'{name}.zip'
    project.to_zip(zip_path)
    return zip_path


def main():
    parser = build_parser(__doc__.splitlines()[0])
    sample_source.add_source_arg(parser)
    args = parser.parse_args()
    require_file(args.export)
    out = render(args.export, resolve_name(args), source=args.source)
    print(out)


if __name__ == '__main__':
    main()

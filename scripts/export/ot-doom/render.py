#!/usr/bin/env python3
"""Render a tempera captures JSON export into an Octatrack megabreak-of-doom
project zip.

Each non-empty bank from the captures becomes one OT bank with one Part
and one 16-step Pattern on track 1. Per cell, the cell's break vocabulary
B (with |B| ∈ {4, 8, 16} required) is padded by repetition to length 16
(B'), and 16 timesliced wavs are rendered such that:

    timesliced[i] = concat_over_j( source_slice(B'[j], pattern_idxs[i]) )

Each timesliced wav is added as a Flex slot, sliced into 16 equal slices
on-device. Track 1 is set to slice mode ON; scenes 1 and 2 lock STRT
(playback_param2) to 0 and 127 respectively, so the crossfader sweeps
sub-slices 0..15 of every step's wav — i.e. it sweeps across breaks.

See docs/planning/ot-doom.md for the design rationale and constraints.

Usage:
    python scripts/export/ot-doom/render.py <path/to/export.json> [--name NAME] [--seed N]

Output:
    tmp/ot-doom/<name>.zip
"""
from __future__ import annotations

import json
import pathlib
import sys
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from octapy import (
    Project,
    SliceMode,
)

from common.cli import build_parser, require_file, resolve_name
from common.schema import load_export

from audio import (
    equal_slices,
    export_wav,
    load_break,
    render_timesliced_step,
)


N_SLICES = 16        # source-break grid (matches the existing octatrack target)
N_SUB_SLICES = 16    # sub-slices per timesliced wav (one per break-position j)
N_PATTERN_STEPS = 16
ALLOWED_B = {4, 8, 16}

SCRIPT_DIR = pathlib.Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent.parent.parent
OUTPUT_DIR = REPO_ROOT / 'tmp' / 'ot-doom'
SAMPLES_DIR = REPO_ROOT / 'tmp' / 'samples'
RENDER_DIR = REPO_ROOT / 'tmp' / 'ot-doom-render'

REQUIRED_CTX = ('gistUser', 'gistId', 'bpm', 'eventsPerCycle', 'nSlices')


def fetch_sample_manifest(gist_user, gist_id):
    url = f'https://gist.githubusercontent.com/{gist_user}/{gist_id}/raw/strudel.json'
    with urllib.request.urlopen(url) as r:
        data = json.loads(r.read())
    base = data.get('_base', '')
    out = {}
    for k, v in data.items():
        if k.startswith('_'):
            continue
        first = v[0] if isinstance(v, list) else v
        out[k] = base + first if not first.startswith(('http://', 'https://')) else first
    return out


def cache_sample(name, url, cache_dir):
    ext = pathlib.Path(url.split('?', 1)[0]).suffix or '.wav'
    path = cache_dir / f'{name}{ext}'
    if path.exists():
        return path
    cache_dir.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(url) as r, open(path, 'wb') as f:
        f.write(r.read())
    return path


def expand_break_names(break_names, events_per_cycle):
    """Apply STRUDEL.md's polymetric stretch i*M//N to expand the
    captured `{a b c d}%N` form to per-event names."""
    n = len(break_names)
    return [break_names[i * n // events_per_cycle] for i in range(events_per_cycle)]


def unique_preserve_order(xs):
    seen = set()
    out = []
    for x in xs:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


def pad_b_to_16(b):
    return [b[i % len(b)] for i in range(16)]


def set_equal_slices(project, slot, n_slices, segment_ms, sample_rate):
    """16 equal slice markers across `segment_ms` of audio at `slot`."""
    slot_markers = project.markers.get_slot(slot, is_static=False)
    frame_count = int(segment_ms * sample_rate / 1000)
    slot_markers.sample_length = frame_count
    slice_ms = segment_ms / n_slices
    slices = [(int(i * slice_ms), int((i + 1) * slice_ms)) for i in range(n_slices)]
    slot_markers.set_slices_ms(slices, sample_rate=sample_rate)
    project.markers.set_slot(slot, slot_markers, is_static=False)


def render_cell(
    project,
    bank_idx,
    cell_idx,
    cell,
    events_per_cycle,
    source_slice_cache,
    cell_render_dir,
):
    """Render one cell into the project: per-step timesliced wavs,
    flex slots, pattern, scenes."""
    expanded = expand_break_names(cell['break'], events_per_cycle)
    b = unique_preserve_order(expanded)
    if len(b) not in ALLOWED_B:
        sys.exit(
            f'bank {bank_idx + 1} cell {cell_idx + 1}: |B|={len(b)} '
            f'(unique breaks {b}) not in {sorted(ALLOWED_B)}'
        )
    b_prime = pad_b_to_16(b)
    cardinality = len(b)

    # Pattern index per OT step. The captured pattern is
    # `events_per_cycle` long (== 8 in tempera); the OT pattern is 16
    # steps long (== one Strudel cycle expanded 1:1 onto a 1/16 grid,
    # matching the existing octatrack target's mapping where each
    # captured event spans 2 OT steps). We therefore replicate every
    # captured slice index into 2 consecutive OT steps via i*N//16.
    captured = cell['pattern']  # list of int|None, len events_per_cycle
    pattern_idx_per_step = [
        captured[i * len(captured) // N_PATTERN_STEPS]
        for i in range(N_PATTERN_STEPS)
    ]

    # Source slice grid: each source break wav → 16 equal slices.
    # Cached across cells.
    for name in b:
        if name not in source_slice_cache:
            seg = load_break(source_slice_cache['__paths__'][name])
            source_slice_cache[name] = equal_slices(seg, N_SLICES)

    # Each source slice is `slice_ms` long. All sources share a tempo
    # in our pipeline, so we anchor on the first one.
    anchor = source_slice_cache[b[0]]
    slice_ms = max(1, int(round(len(anchor[0]))))
    sample_rate = anchor[0].frame_rate

    timesliced_segment_ms = N_SUB_SLICES * slice_ms

    # Render the |B| unique timesliced wavs. timesliced[i] for
    # i ∈ 0..|B|-1 differs because pattern_idx_per_step[i] differs;
    # for |B| < 16, indices i, i+|B|, i+2|B|, … only differ if the
    # captured pattern at those positions differs, so we *don't* try
    # to dedup across i — we always emit N_PATTERN_STEPS unique files,
    # one per OT step. The slot-level dedup happens through sample_lock
    # which points multiple steps at the same slot via (i*|B|)//16.
    # Slot count after this loop = N_PATTERN_STEPS / (16/|B|) = |B|.

    # Strategy: generate one wav per *destination slot*. Slot s holds
    # the timesliced render for any step `i` with (i*|B|)//16 == s.
    # All such steps share the same sample_lock target, so they need
    # one shared wav. The natural wav to emit is timesliced[step=s*16//|B|]
    # — that's the first step in the slot's run.
    slot_to_step = [s * 16 // cardinality for s in range(cardinality)]

    cell_render_dir.mkdir(parents=True, exist_ok=True)
    flex_slots = []
    for s, step in enumerate(slot_to_step):
        seg = render_timesliced_step(
            source_slice_cache,
            b_prime,
            pattern_idx_per_step[step],
            slice_ms,
        )
        wav_path = cell_render_dir / f'b{bank_idx + 1:02d}p{cell_idx + 1:02d}_s{s:02d}.wav'
        export_wav(seg, wav_path)
        slot = project.add_sample(str(wav_path.resolve()), slot_type='FLEX')
        set_equal_slices(project, slot, N_SUB_SLICES,
                         segment_ms=timesliced_segment_ms,
                         sample_rate=sample_rate)
        flex_slots.append(slot)

    # Bank/Part/Pattern setup
    bank = project.bank(bank_idx + 1)
    part = bank.part(1)
    t1 = part.audio_track(1)
    t1.configure_flex(flex_slots[0])
    t1.setup.slice = SliceMode.ON

    # Scenes: scene 1 (index 0) locks STRT to 0 (sub-slice 0 = break 0),
    # scene 2 (index 1) locks STRT to 127 (sub-slice 15 = break 15).
    part.scene(1).track(1).playback_param2 = 0
    part.scene(2).track(1).playback_param2 = 127
    part.active_scene_a = 0
    part.active_scene_b = 1

    pattern = bank.pattern(cell_idx + 1)
    pattern.scale_length = N_PATTERN_STEPS
    track = pattern.audio_track(1)
    track.active_steps = list(range(1, N_PATTERN_STEPS + 1))
    for i in range(N_PATTERN_STEPS):
        slot_idx = (i * cardinality) // 16
        step = track.step(i + 1)
        step.sample_lock = flex_slots[slot_idx]
        step.slice_index = 0


def build_project(export_path, name):
    payload, ctx = load_export(export_path, REQUIRED_CTX)
    if ctx['nSlices'] != N_SLICES:
        sys.exit(f'nSlices {ctx["nSlices"]} != {N_SLICES} (ot-doom assumes 16)')

    banks_in = [b for b in (payload.get('banks') or []) if b]
    if not banks_in:
        sys.exit('no non-empty banks in export')
    if len(banks_in) > 16:
        sys.exit(f'too many banks: {len(banks_in)} > 16')
    for i, bank in enumerate(banks_in):
        if len(bank) > 16:
            sys.exit(f'bank {i + 1} has {len(bank)} cells > 16')

    # Source-break manifest: download wavs from the captures' gist into
    # the shared sample cache (also used by the existing octatrack target).
    manifest = fetch_sample_manifest(ctx['gistUser'], ctx['gistId'])
    cache_dir = SAMPLES_DIR / ctx['gistId']

    # Collect all break names referenced across all cells.
    all_names = set()
    for bank_cells in banks_in:
        for cell in bank_cells:
            for break_name in cell['break']:
                all_names.add(break_name)
    missing = [n for n in all_names if n not in manifest]
    if missing:
        sys.exit(f'sample gist missing breaks: {missing}')

    paths = {n: cache_sample(n, manifest[n], cache_dir) for n in all_names}

    project = Project.from_template(name.upper()[:16])
    project.settings.tempo = float(ctx['bpm'])
    project.master_track = True

    # cache shared across cells: name -> List[AudioSegment] of 16 slices.
    # We tunnel the path map through the same dict under a reserved key
    # so render_cell can lazily load on first reference per cell.
    source_slice_cache = {'__paths__': paths}

    cell_render_root = RENDER_DIR / name
    if cell_render_root.exists():
        # tmp dir; safe to wipe so we don't accumulate stale renders.
        for p in cell_render_root.rglob('*'):
            if p.is_file():
                p.unlink()

    for bank_idx, bank_cells in enumerate(banks_in):
        for cell_idx, cell in enumerate(bank_cells):
            render_cell(
                project,
                bank_idx,
                cell_idx,
                cell,
                ctx['eventsPerCycle'],
                source_slice_cache,
                cell_render_root / f'bank{bank_idx + 1:02d}',
            )

    return project


def render(export_path, name):
    project = build_project(export_path, name)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    zip_path = OUTPUT_DIR / f'{name}.zip'
    project.to_zip(zip_path)
    return zip_path


def main():
    args = build_parser(__doc__.splitlines()[0]).parse_args()
    require_file(args.export)
    out = render(args.export, resolve_name(args))
    print(out)


if __name__ == '__main__':
    main()
